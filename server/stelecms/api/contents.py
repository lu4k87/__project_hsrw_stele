"""Mediathek-API (SPEC §7.4)."""
from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from .. import appsettings, jobs, media, schemas, timeutil
from .. import auth as authm
from .. import db as dbm
from ..audit import audit, q
from ..errors import ApiError, conflict
from ..permissions import require
from ..validation import Validator, body, is_int, parse_tags
from .common import arg_int, check_unchanged, get_or_404, ok

bp = Blueprint("api_contents", __name__, url_prefix="/api/contents")

TYPE_LABELS = {"image": "das Bild", "video": "das Video", "pdf": "das PDF", "text": "die Info-Folie",
               "web": "die Webseite"}
SORTS = {"updated_desc": "updated_at DESC, id DESC", "created_desc": "created_at DESC, id DESC",
         "title_asc": "title COLLATE NOCASE ASC, id ASC", "size_desc": "COALESCE(size_bytes, 0) DESC, id DESC"}
MAX_FILES_PER_REQUEST = 50
DISK_RESERVE_BYTES = 1024 ** 3  # 1 GB bleibt immer frei


def _media_dir() -> Path:
    return Path(current_app.config["MEDIA_DIR"])


def _serialize(conn, r, idx=None, with_usages=False):
    return media.serialize_content(conn, r, idx or media.UsageIndex.cached(conn), with_usages=with_usages)


@bp.get("")
@require("content.view")
def list_contents():
    conn = dbm.get_db()
    where, params = [], []
    types = [t for t in (request.args.get("type") or "").split(",") if t.strip()]
    if types:
        valid = [t.strip() for t in types if t.strip() in ("image", "video", "pdf", "text", "web")]
        if not valid:
            return jsonify({"items": [], "total": 0, "tags": _all_tags(conn)})
        where.append("type IN (%s)" % ",".join("?" for _ in valid))
        params += valid
    qtext = (request.args.get("q") or "").strip()
    if qtext:
        like = "%" + qtext.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(title LIKE ? ESCAPE '\\' OR file_name LIKE ? ESCAPE '\\' OR EXISTS "
                     "(SELECT 1 FROM json_each(contents.tags) WHERE json_each.value LIKE ? ESCAPE '\\'))")
        params += [like, like, like]
    tag = (request.args.get("tag") or "").strip()
    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(contents.tags) WHERE lower(json_each.value) = lower(?))")
        params.append(tag)
    status = (request.args.get("status") or "").strip()
    if status in ("processing", "ready", "error"):
        where.append("status = ?")
        params.append(status)
    order = SORTS.get(request.args.get("sort") or "updated_desc", SORTS["updated_desc"])
    limit = arg_int("limit", 200, 1, 1000)
    offset = arg_int("offset", 0, 0, 10_000_000)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    total = dbm.scalar(conn, "SELECT COUNT(*) FROM contents" + wsql, params)
    rows = dbm.rows(conn, f"SELECT * FROM contents{wsql} ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, offset])
    idx = media.UsageIndex.cached(conn)
    return jsonify({"items": [_serialize(conn, r, idx) for r in rows], "total": total, "tags": _all_tags(conn)})


def _all_tags(conn) -> list[str]:
    tags: dict[str, str] = {}
    for r in conn.execute("SELECT DISTINCT json_each.value AS t FROM contents, json_each(contents.tags)").fetchall():
        if isinstance(r["t"], str):
            tags.setdefault(r["t"].lower(), r["t"])
    return sorted(tags.values(), key=str.lower)


@bp.post("/upload")
@require("content.edit")
def upload():
    conn = dbm.get_db()
    settings = appsettings.get_settings(conn)
    limit = settings["upload_max_mb"] * 1024 * 1024
    if request.content_length and request.content_length > limit * MAX_FILES_PER_REQUEST + 1024 * 1024:
        raise ApiError(413, "too_large", f"Die Anfrage ist zu groß. Erlaubt sind höchstens "
                                         f"{settings['upload_max_mb']} MB je Datei.")
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        raise ApiError(422, "validation_error", "Bitte mindestens eine Datei auswählen.",
                       fields={"files": "Bitte mindestens eine Datei auswählen."})
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ApiError(422, "validation_error", f"Höchstens {MAX_FILES_PER_REQUEST} Dateien auf einmal.",
                       fields={"files": f"Höchstens {MAX_FILES_PER_REQUEST} Dateien auf einmal."})
    v = Validator({})
    tags = []
    raw_tags = request.form.get("tags")
    if raw_tags:
        try:
            tags = parse_tags(json.loads(raw_tags), v) or []
        except ValueError:
            v.error("tags", "Tags bitte als JSON-Liste angeben, z. B. [\"Foyer\"].")
    v.done()
    user = authm.current_user()
    tmp_dir = _media_dir() / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # Platz für die Kopie in der Mediathek + Reserve, damit Datenbank, Heartbeats und Sitzungen weiterlaufen
    need = (request.content_length or 0) + DISK_RESERVE_BYTES
    if shutil.disk_usage(tmp_dir).free < need:
        raise ApiError(507, "insufficient_storage", "Auf dem Server ist nicht genug Speicherplatz frei. "
                                                    "Bitte nicht mehr benötigte Inhalte löschen.")
    created, errors, need_jobs = [], [], False
    for f in files:
        name = media.safe_file_name(f.filename)
        tmp = tmp_dir / ("up-" + secrets.token_hex(8))
        try:
            f.save(tmp)
            size = tmp.stat().st_size
            if size > limit:
                tmp.unlink(missing_ok=True)
                errors.append({"file_name": name, "code": "too_large",
                               "message": f"Die Datei ist zu groß ({size / 1024 / 1024:.0f} MB). Erlaubt sind "
                                          f"höchstens {settings['upload_max_mb']} MB."})
                continue
            # Verarbeitung außerhalb der Schreib-Transaktion (Bilder können einige Sekunden dauern)
            cid, job_kind = media.import_file(conn, _media_dir(), tmp, name, tags=tags, user_id=user["id"])
            with dbm.transaction(conn):
                if job_kind:
                    jobs.enqueue(conn, job_kind, cid)
                    need_jobs = True
                row = dbm.row(conn, "SELECT * FROM contents WHERE id = ?", (cid,))
                audit(conn, "create", "content", f"hat {TYPE_LABELS[row['type']]} {q(row['title'])} hochgeladen",
                      entity_id=cid, entity_name=row["title"],
                      details={"file_name": name, "size_bytes": size, "type": row["type"]})
            created.append(cid)
        except media.UnsupportedFile as exc:
            errors.append({"file_name": name, "code": "unsupported_media", "message": str(exc)})
        finally:
            tmp.unlink(missing_ok=True)
    if need_jobs:
        jobs.kick(current_app._get_current_object())
    idx = media.UsageIndex(conn)
    items = [_serialize(conn, dbm.row(conn, "SELECT * FROM contents WHERE id = ?", (cid,)), idx) for cid in created]
    status = 201 if items else (415 if all(e["code"] == "unsupported_media" for e in errors) else 413)
    if not items:
        first = errors[0]
        err = ApiError(status, first["code"], first["message"] if len(errors) == 1 else
                       "Keine der Dateien konnte übernommen werden.", details={"errors": errors})
        payload = err.to_dict()
        payload.update({"items": [], "errors": errors})
        return jsonify(payload), status
    return jsonify({"items": items, "errors": errors}), status


def _validate_data(conn, ctype: str, base, patch, v: Validator, creating: bool) -> dict:
    if ctype == "text":
        return schemas.normalize_text_data(conn, base, patch, v)
    return schemas.normalize_web_data(base, patch, v, require_url=creating)


def _maybe_check_embed(old: dict | None, new: dict, patch) -> dict:
    """Webseite: Einbettung prüfen, wenn sich die Adresse ändert und der Client kein Ergebnis mitschickt."""
    sent = isinstance(patch, dict) and isinstance(patch.get("embed_check"), dict)
    url_changed = old is None or old.get("url") != new["url"]
    if url_changed and not sent and new["url"]:
        res = media.check_url(new["url"])
        new["embed_check"] = {"embeddable": res["embeddable"], "message": res["message"],
                              "checked_at": timeutil.now_iso()}
    elif sent and not new["embed_check"].get("checked_at"):
        new["embed_check"]["checked_at"] = timeutil.now_iso()
    return new


@bp.post("")
@require("content.edit")
def create_content():
    conn = dbm.get_db()
    data = body()
    v = Validator(data)
    ctype = v.choice("type", ("text", "web"), required=True)
    title = v.text("title", required=True, max_len=200, empty_msg="Bitte einen Titel eingeben.")
    tags = parse_tags(data.get("tags"), v) or []
    content_data = _validate_data(conn, ctype, None, data.get("data") or {}, v, True) if ctype else {}
    v.done()
    if ctype == "web":
        content_data = _maybe_check_embed(None, content_data, data.get("data"))
    user = authm.current_user()
    with dbm.transaction(conn):
        cid = media.insert_content(conn, ctype=ctype, title=title, tags=tags, data=content_data, user_id=user["id"])
        audit(conn, "create", "content", f"hat {TYPE_LABELS[ctype]} {q(title)} angelegt", entity_id=cid,
              entity_name=title)
    return jsonify(_serialize(conn, get_or_404(conn, "contents", cid))), 201


@bp.get("/<int:cid>")
@require("content.view")
def get_content(cid: int):
    conn = dbm.get_db()
    return jsonify(_serialize(conn, get_or_404(conn, "contents", cid), with_usages=True))


@bp.patch("/<int:cid>")
@require("content.edit")
def update_content(cid: int):
    conn = dbm.get_db()
    r = get_or_404(conn, "contents", cid)
    data = body()
    v = Validator(data)
    title = v.text("title", max_len=200)
    if "title" in data and not title:
        v.error("title", "Bitte einen Titel eingeben.")
    tags = parse_tags(data.get("tags"), v) if "tags" in data else None
    new_data = None
    if data.get("data") is not None:
        if r["type"] not in ("text", "web"):
            v.error("data", "Die Daten dieses Inhaltstyps werden aus der Datei ermittelt und sind nicht änderbar.")
        else:
            old_data = dbm.jloads(r["data"], {})
            new_data = _validate_data(conn, r["type"], old_data, data["data"], v, False)
            if r["type"] == "text" and cid in (new_data["fields"].get("image_content_id"),
                                               new_data["style"].get("bg_image_content_id")):
                v.error("data", "Eine Info-Folie kann nicht auf sich selbst verweisen.")
    v.done()
    changes = {}
    if title and title != r["title"]:
        changes["title"] = title
    if tags is not None and tags != dbm.jloads(r["tags"], []):
        changes["tags"] = dbm.jdumps(tags)
    if new_data is not None:
        if r["type"] == "web":
            new_data = _maybe_check_embed(schemas.merge_defaults(schemas.WEB_DATA, dbm.jloads(r["data"], {})),
                                          new_data, data["data"])
        if new_data != schemas.merge_defaults(schemas.TEXT_DATA if r["type"] == "text" else schemas.WEB_DATA,
                                              dbm.jloads(r["data"], {})):
            changes["data"] = dbm.jdumps(new_data)
    if changes:
        user = authm.current_user()
        with dbm.transaction(conn):
            noun = TYPE_LABELS[r["type"]]
            check_unchanged(conn, "contents", cid, data, f"{noun[0].upper()}{noun[1:]} {q(r['title'])}")
            changes.update({"updated_at": timeutil.now_iso(), "updated_by": user["id"]})
            dbm.update(conn, "contents", cid, changes)
            label = title or r["title"]
            summary = (f"hat {TYPE_LABELS[r['type']]} {q(r['title'])} in {q(title)} umbenannt"
                       if set(changes) == {"title", "updated_at", "updated_by"} else
                       f"hat {TYPE_LABELS[r['type']]} {q(label)} bearbeitet")
            audit(conn, "update", "content", summary, entity_id=cid, entity_name=label,
                  details={"fields": sorted(k for k in changes if k not in ("updated_at", "updated_by"))})
    return jsonify(_serialize(conn, get_or_404(conn, "contents", cid), with_usages=True))


@bp.post("/<int:cid>/duplicate")
@require("content.edit")
def duplicate_content(cid: int):
    conn = dbm.get_db()
    r = get_or_404(conn, "contents", cid)
    if r["type"] not in ("text", "web"):
        raise ApiError(422, "validation_error", "Nur Info-Folien und Webseiten können dupliziert werden.")
    user = authm.current_user()
    title = (r["title"] + " (Kopie)")[:200]
    with dbm.transaction(conn):
        new_id = media.insert_content(conn, ctype=r["type"], title=title, tags=dbm.jloads(r["tags"], []),
                                      data=dbm.jloads(r["data"], {}), user_id=user["id"])
        audit(conn, "create", "content", f"hat {TYPE_LABELS[r['type']]} {q(r['title'])} dupliziert",
              entity_id=new_id, entity_name=title, details={"source_id": cid})
    return jsonify(_serialize(conn, get_or_404(conn, "contents", new_id))), 201


def _delete_one(conn, r: dict) -> None:
    conn.execute("DELETE FROM contents WHERE id = ?", (r["id"],))
    audit(conn, "delete", "content", f"hat {TYPE_LABELS[r['type']]} {q(r['title'])} gelöscht",
          entity_id=r["id"], entity_name=r["title"])


def _in_use_message(r: dict, usages: list) -> str:
    n = len(usages)
    return (f"{q(r['title'])} wird noch an {n} Stelle{'n' if n != 1 else ''} verwendet und kann nicht gelöscht "
            "werden. Bitte zuerst aus den Präsentationen, Designs oder Touch-Menüs entfernen "
            "(bei veröffentlichten Präsentationen danach erneut veröffentlichen).")


@bp.delete("/<int:cid>")
@require("content.delete")
def delete_content(cid: int):
    conn = dbm.get_db()
    r = get_or_404(conn, "contents", cid)
    # Prüfung in der Schreib-Transaktion: sonst kann dazwischen ein Stand mit diesem Inhalt veröffentlicht werden
    with dbm.transaction(conn):
        usages = media.UsageIndex(conn).usages(cid)
        if usages:
            raise conflict(_in_use_message(r, usages), "in_use", {"usages": usages})
        _delete_one(conn, r)
    media.delete_files(_media_dir(), r["uid"])
    return ok()


@bp.post("/bulk-delete")
@require("content.delete")
def bulk_delete():
    conn = dbm.get_db()
    data = body()
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids or not all(is_int(i) for i in ids):
        raise ApiError(422, "validation_error", "Bitte mindestens einen Inhalt auswählen.",
                       fields={"ids": "Bitte eine Liste von IDs angeben."})
    deleted, blocked, uids = [], [], []
    # Verwendungen in der Schreib-Transaktion ermitteln (wie beim Einzel-Löschen)
    with dbm.transaction(conn):
        idx = media.UsageIndex(conn)
        for cid in dict.fromkeys(ids):
            r = dbm.row(conn, "SELECT * FROM contents WHERE id = ?", (cid,))
            if r is None:
                continue
            usages = idx.usages(cid)
            if usages:
                blocked.append({"id": cid, "title": r["title"], "usages": usages})
                continue
            _delete_one(conn, r)
            deleted.append(cid)
            uids.append(r["uid"])
    for uid in uids:
        media.delete_files(_media_dir(), uid)
    return jsonify({"deleted": deleted, "blocked": blocked})


@bp.post("/check-url")
@require("content.edit")
def check_url():
    data = body()
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ApiError(422, "validation_error", "Bitte eine Adresse eingeben.",
                       fields={"url": "Bitte eine Adresse (https://…) eingeben."})
    return jsonify(media.check_url(url.strip()))


def content_exists(conn, cid) -> bool:
    return is_int(cid) and dbm.scalar(conn, "SELECT 1 FROM contents WHERE id = ?", (cid,)) is not None
