"""Stelen (SPEC §7.7): Verwaltung, Kopplung, Schlüssel, Befehle, Live-Manifest, Screenshot."""
from __future__ import annotations

import ipaddress
import re
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, send_file

from .. import schemas, steles, timeutil
from .. import auth as authm
from .. import db as dbm
from ..audit import audit, q
from ..errors import ApiError
from ..permissions import check, has, require
from ..resolve import Resolver
from ..validation import Validator, body, is_int
from .common import arg_int, get_or_404, list_response, ok
from .manifest import manifest_response

PAIRING_CLAIM_GRACE = timedelta(minutes=2)  # Player fragt alle 3 s: Zeit, den Schlüssel nach dem Koppeln abzuholen

bp = Blueprint("api_steles", __name__, url_prefix="/api")

HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
                     r"[A-Za-z0-9])?)*$")


def new_key() -> str:
    return secrets.token_urlsafe(32)


def _serialize(conn, s: dict, resolver: Resolver | None = None, with_url: bool = False) -> dict:
    out = steles.serialize(conn, s, resolver or Resolver(conn))
    if with_url:
        out["player_url"] = steles.player_url(s)
    return out


def _load(conn, sid: int) -> dict:
    return get_or_404(conn, "steles", sid)


def valid_host(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(HOST_RE.match(value))


def _validate(conn, data: dict, v: Validator, old: dict | None) -> dict:
    out = {}
    if old is None or "name" in data:
        name = v.text("name", required=True, max_len=80, empty_msg="Bitte einen Namen für die Stele eingeben.")
        if name:
            out["name"] = name
    if "location" in data:
        out["location"] = v.text("location", max_len=120, default="") or ""
    if "ip_address" in data:
        ip = v.text("ip_address", max_len=253, default="") or ""
        if ip and not valid_host(ip):
            v.error("ip_address", "Bitte eine gültige IP-Adresse (z. B. 192.168.1.50) oder einen Rechnernamen "
                                  "eingeben.")
        else:
            out["ip_address"] = ip
    for key in ("width", "height"):
        if key in data:
            val = v.integer(key, min_value=320, max_value=7680)
            if val is not None:
                out[key] = val
    if "default_presentation_id" in data:
        pid = data.get("default_presentation_id")
        if pid is not None and (not is_int(pid) or
                                dbm.scalar(conn, "SELECT 1 FROM presentations WHERE id = ?", (pid,)) is None):
            v.error("default_presentation_id", "Diese Präsentation gibt es nicht.")
        else:
            out["default_presentation_id"] = pid
    if "settings" in data:
        base = dbm.jloads(old["settings"], {}) if old else None
        out["settings"] = dbm.jdumps(schemas.normalize_stele_settings(base, data.get("settings"), v))
    return out


def _claim_code(conn, code, v_field: str = "code") -> dict:
    """Prüft einen Kopplungscode: 404 unbekannt, 410 abgelaufen, 409 bereits verwendet."""
    code = re.sub(r"\s+", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        raise ApiError(422, "validation_error", "Bitte den 6-stelligen Code eingeben, der auf der Stele angezeigt "
                                                "wird.", fields={v_field: "Bitte 6 Ziffern eingeben."})
    r = dbm.row(conn, "SELECT * FROM pairing_requests WHERE code = ?", (code,))
    if r is None:
        raise ApiError(404, "not_found", "Code unbekannt. Bitte den Code auf der Stele prüfen.",
                       fields={v_field: "Code unbekannt."})
    if r["claimed_at"]:
        raise ApiError(409, "conflict", "Dieser Code wurde bereits verwendet. Die Stele zeigt gleich einen neuen "
                                        "Code an.", fields={v_field: "Code bereits verwendet."})
    if (timeutil.parse_iso(r["expires_at"]) or timeutil.utcnow()) <= timeutil.utcnow():
        raise ApiError(410, "expired", "Code abgelaufen. Die Stele zeigt automatisch einen neuen Code an.",
                       fields={v_field: "Code abgelaufen."})
    return r


def _pair(conn, stele: dict, code_row: dict) -> None:
    now_dt = timeutil.utcnow()
    now = timeutil.iso(now_dt)
    # Kurz vor Ablauf gekoppelt: Code noch so lange gültig lassen, dass der Player den Schlüssel abholen kann
    old_exp = timeutil.parse_iso(code_row["expires_at"])
    expires = timeutil.iso(max(old_exp, now_dt + PAIRING_CLAIM_GRACE) if old_exp else now_dt + PAIRING_CLAIM_GRACE)
    conn.execute("UPDATE pairing_requests SET stele_id = ?, claimed_at = ?, expires_at = ? WHERE code = ?",
                 (stele["id"], now, expires, code_row["code"]))
    conn.execute("UPDATE steles SET paired_at = ?, updated_at = ? WHERE id = ?", (now, now, stele["id"]))
    info = dbm.jloads(code_row["device_info"], {})
    steles.add_event(conn, stele["id"], "info", "paired", "Stele wurde per Code gekoppelt.", {"device_info": info})
    audit(conn, "pair", "stele", f"hat die Stele {q(stele['name'])} per Code gekoppelt", entity_id=stele["id"],
          entity_name=stele["name"])


# ------------------------------------------------------------------ Liste, Anlegen

@bp.get("/steles")
@require("steles.view")
def list_steles():
    conn = dbm.get_db()
    resolver = Resolver(conn)
    rows = dbm.rows(conn, "SELECT * FROM steles ORDER BY name COLLATE NOCASE, id")
    return list_response([_serialize(conn, s, resolver) for s in rows])


@bp.post("/steles")
@require("steles.manage")
def create_stele():
    conn = dbm.get_db()
    data = body()
    v = Validator(data)
    values = _validate(conn, data, v, None)
    v.done()
    code_row = _claim_code(conn, data["pairing_code"], "pairing_code") if data.get("pairing_code") else None
    now = timeutil.now_iso()
    values.setdefault("settings", dbm.jdumps(schemas.merge_defaults(schemas.STELE_SETTINGS, {})))
    values.update({"player_key": new_key(), "created_at": now, "updated_at": now})
    with dbm.transaction(conn):
        sid = dbm.insert(conn, "steles", values)
        audit(conn, "create", "stele", f"hat die Stele {q(values['name'])} hinzugefügt", entity_id=sid,
              entity_name=values["name"])
        if code_row:
            _pair(conn, _load(conn, sid), code_row)
    return jsonify(_serialize(conn, _load(conn, sid), with_url=True)), 201


@bp.get("/pairing/pending")
@require("steles.manage")
def pending_pairings():
    conn = dbm.get_db()
    now = timeutil.now_iso()
    rows = dbm.rows(conn, "SELECT * FROM pairing_requests WHERE claimed_at IS NULL AND expires_at > ? "
                          "ORDER BY created_at DESC", (now,))
    return jsonify({"items": [{"code": r["code"], "device_info": dbm.jloads(r["device_info"], {}),
                               "created_at": r["created_at"], "expires_at": r["expires_at"]} for r in rows],
                    "total": len(rows)})


# ------------------------------------------------------------------ Einzelne Stele

@bp.get("/steles/<int:sid>")
@require("steles.view")
def get_stele(sid: int):
    conn = dbm.get_db()
    return jsonify(_serialize(conn, _load(conn, sid), with_url=has("steles.manage")))


@bp.patch("/steles/<int:sid>")
def update_stele(sid: int):
    data = body()
    # Nur die Standard-Präsentation: auch mit schedule.edit erlaubt
    if set(data.keys()) <= {"default_presentation_id"} and data:
        check("steles.manage", "schedule.edit", any_of=True)
    else:
        check("steles.manage")
    conn = dbm.get_db()
    s = _load(conn, sid)
    v = Validator(data)
    values = _validate(conn, data, v, s)
    v.done()
    changes = {k: val for k, val in values.items() if s.get(k) != val}
    if changes:
        fields = sorted(changes)
        with dbm.transaction(conn):
            changes["updated_at"] = timeutil.now_iso()
            dbm.update(conn, "steles", sid, changes)
            label = values.get("name") or s["name"]
            if fields == ["default_presentation_id"]:
                p = dbm.row(conn, "SELECT name FROM presentations WHERE id = ?", (values["default_presentation_id"],))
                summary = (f"hat die Standard-Präsentation der Stele {q(label)} auf {q(p['name'])} gesetzt" if p
                           else f"hat die Standard-Präsentation der Stele {q(label)} entfernt")
            elif fields == ["name"]:
                summary = f"hat die Stele {q(s['name'])} in {q(label)} umbenannt"
            else:
                summary = f"hat die Stele {q(label)} bearbeitet"
            audit(conn, "update", "stele", summary, entity_id=sid, entity_name=label, details={"fields": fields})
    return jsonify(_serialize(conn, _load(conn, sid), with_url=has("steles.manage")))


@bp.delete("/steles/<int:sid>")
@require("steles.manage")
def delete_stele(sid: int):
    conn = dbm.get_db()
    s = _load(conn, sid)
    with dbm.transaction(conn):
        conn.execute("DELETE FROM steles WHERE id = ?", (sid,))
        audit(conn, "delete", "stele", f"hat die Stele {q(s['name'])} entfernt", entity_id=sid,
              entity_name=s["name"])
    steles.delete_screenshots(current_app.config, sid)
    return ok()


@bp.post("/steles/<int:sid>/pair")
@require("steles.manage")
def pair_stele(sid: int):
    conn = dbm.get_db()
    s = _load(conn, sid)
    data = body()
    code_row = _claim_code(conn, data.get("code"))
    with dbm.transaction(conn):
        _pair(conn, s, code_row)
    return jsonify(_serialize(conn, _load(conn, sid), with_url=True))


@bp.post("/steles/<int:sid>/rotate-key")
@require("steles.manage")
def rotate_key(sid: int):
    conn = dbm.get_db()
    s = _load(conn, sid)
    with dbm.transaction(conn):
        # Neuer Schlüssel: bisher gekoppelte Geräte verlieren den Zugriff (paired_at wird zurückgesetzt)
        conn.execute("UPDATE steles SET player_key = ?, paired_at = NULL, updated_at = ? WHERE id = ?",
                     (new_key(), timeutil.now_iso(), sid))
        steles.add_event(conn, sid, "warning", "key_rotated", "Der Stelen-Schlüssel wurde erneuert. Die Stele muss "
                                                              "neu gekoppelt oder mit dem neuen Link geöffnet werden.")
        audit(conn, "update", "stele", f"hat den Schlüssel der Stele {q(s['name'])} erneuert", entity_id=sid,
              entity_name=s["name"], details={"fields": ["player_key"]})
    return jsonify(_serialize(conn, _load(conn, sid), with_url=True))


# ------------------------------------------------------------------ Befehle

def _command_out(conn, r: dict) -> dict:
    return {"id": r["id"], "command": r["command"], "created_by": authm.person(conn, r["created_by"]),
            "created_at": r["created_at"], "delivered_at": r["delivered_at"], "done_at": r["done_at"],
            "result": r["result"]}


@bp.post("/steles/<int:sid>/commands")
@require("steles.control")
def send_command(sid: int):
    conn = dbm.get_db()
    s = _load(conn, sid)
    data = body()
    v = Validator(data)
    command = v.choice("command", tuple(steles.COMMAND_LABELS), required=True)
    v.done()
    user = authm.current_user()
    now = timeutil.now_iso()
    with dbm.transaction(conn):
        cid = dbm.insert(conn, "stele_commands", {"stele_id": sid, "command": command, "payload": "{}",
                                                  "created_by": user["id"], "created_at": now})
        label = steles.COMMAND_LABELS[command]
        steles.add_event(conn, sid, "info", "command", f"Befehl „{label}“ gesendet von {user['display_name']}.",
                         {"command_id": cid, "command": command})
        audit(conn, "command", "stele", f"hat den Befehl {q(label)} an die Stele {q(s['name'])} gesendet",
              entity_id=sid, entity_name=s["name"], details={"command": command, "command_id": cid})
    return jsonify({"id": cid, "command": command, "created_at": now}), 201


@bp.get("/steles/<int:sid>/commands")
@require("steles.view")
def list_commands(sid: int):
    conn = dbm.get_db()
    _load(conn, sid)
    limit = arg_int("limit", 20, 1, 200)
    rows = dbm.rows(conn, "SELECT * FROM stele_commands WHERE stele_id = ? ORDER BY id DESC LIMIT ?", (sid, limit))
    return jsonify({"items": [_command_out(conn, r) for r in rows], "total": len(rows)})


# ------------------------------------------------------------------ Live-Ansicht

@bp.get("/steles/<int:sid>/manifest")
@require("steles.view")
def stele_manifest(sid: int):
    conn = dbm.get_db()
    s = _load(conn, sid)
    return manifest_response(Resolver(conn).stele_manifest(s))


@bp.get("/steles/<int:sid>/screenshot")
@require("monitoring.view")
def stele_screenshot(sid: int):
    conn = dbm.get_db()
    _load(conn, sid)
    path = steles.screenshot_path(current_app.config, sid)
    if not path.is_file():
        raise ApiError(404, "not_found", "Für diese Stele liegt noch kein Screenshot vor.")
    resp = send_file(path, mimetype="image/jpeg", conditional=True, max_age=None)
    resp.headers["Cache-Control"] = "no-store"
    return resp
