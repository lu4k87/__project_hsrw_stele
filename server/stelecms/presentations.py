"""Präsentationen: Ausgabe (§7.5), Folien speichern, Veröffentlichen, Verwerfen.

Wird von der API und vom Seed genutzt.
"""
from __future__ import annotations

from . import schemas, timeutil
from . import db as dbm
from .auth import person
from .media import content_urls, content_warnings
from .resolve import Resolver, item_validity, status_hash
from .validation import Validator, is_int

MAX_ITEMS = 500


# ------------------------------------------------------------------ Ausgabe

def used_by_map(conn) -> dict[int, list[dict]]:
    """Präsentation → [{stele_id, stele_name, how}] (Standard einer Stele oder im Zeitplan)."""
    out: dict[int, list[dict]] = {}
    seen: set[tuple] = set()
    for r in conn.execute("SELECT id, name, default_presentation_id FROM steles "
                          "WHERE default_presentation_id IS NOT NULL ORDER BY name COLLATE NOCASE").fetchall():
        out.setdefault(r["default_presentation_id"], []).append(
            {"stele_id": r["id"], "stele_name": r["name"], "how": "default"})
    for r in conn.execute("SELECT DISTINCT e.presentation_id, s.id, s.name FROM schedule_entries e "
                          "JOIN steles s ON s.id = e.stele_id ORDER BY s.name COLLATE NOCASE").fetchall():
        key = (r["presentation_id"], r["id"])
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(r["presentation_id"], []).append({"stele_id": r["id"], "stele_name": r["name"],
                                                         "how": "schedule"})
    return out


def _ref(row: dict | None, *keys) -> dict | None:
    return {k: row[k] for k in keys} if row else None


def summary(conn, p: dict, resolver: Resolver, used_by: dict | None = None) -> dict:
    settings = schemas.merge_defaults(schemas.PRESENTATION_SETTINGS, dbm.jloads(p["settings"], {}))
    items = resolver.items(p["id"])
    active = [it for it in items if it["enabled"]]
    total = 0.0
    thumb = None
    for it in items:
        c = resolver.content(it["content_id"])
        if not c:
            continue
        if it["enabled"]:
            total += resolver.effective_duration(c, settings, it)
        if thumb is None and it["enabled"]:
            thumb = content_urls(c)["thumb"]
    ub = used_by if used_by is not None else used_by_map(conn)
    return {
        "id": p["id"], "name": p["name"], "description": p["description"],
        "status": resolver.status(p), "review_state": p["review_state"], "review_note": p["review_note"],
        "review_by": person(conn, p["review_by"]), "review_at": p["review_at"],
        "review_changed": review_changed(p, resolver),
        "item_count": len(items), "active_item_count": len(active), "total_duration_s": round(total, 3),
        "design": _ref(resolver.design_row(p["design_id"]), "id", "name"),
        "touch_menu": _ref(resolver.menu_row(p["touch_menu_id"]), "id", "name"),
        "used_by": ub.get(p["id"], []),
        "thumb_url": thumb,
        "published_at": p["published_at"], "published_by": person(conn, p["published_by"]),
        "created_at": p["created_at"], "created_by": person(conn, p["created_by"]),
        "updated_at": p["updated_at"], "updated_by": person(conn, p["updated_by"]),
    }


def full(conn, p: dict, resolver: Resolver) -> dict:
    out = summary(conn, p, resolver)
    settings = schemas.merge_defaults(schemas.PRESENTATION_SETTINGS, dbm.jloads(p["settings"], {}))
    tz = resolver.settings["timezone"]
    now = timeutil.utcnow()
    items = []
    for it in resolver.items(p["id"]):
        c = resolver.content(it["content_id"])
        options = dbm.jloads(it["options"], {})
        item_dict = dict(it, options=options)
        items.append({
            "id": it["id"], "position": it["position"], "enabled": bool(it["enabled"]),
            "duration_s": it["duration_s"], "transition": it["transition"],
            "valid_from": it["valid_from"], "valid_until": it["valid_until"],
            "caption": it["caption"], "options": options,
            "content": ({"id": c["id"], "type": c["type"], "title": c["title"], "status": c["status"],
                         "thumb_url": content_urls(c)["thumb"], "duration_s": c["duration_s"],
                         "page_count": c["page_count"], "warnings": content_warnings(c)} if c else None),
            "effective_duration_s": resolver.effective_duration(c, settings, item_dict) if c else 0,
            "validity": item_validity(it, tz, now),
        })
    out.update({"settings": settings, "design_id": p["design_id"], "touch_menu_id": p["touch_menu_id"],
                "items": items})
    return out


# ------------------------------------------------------------------ Folien

def validate_items(conn, raw, v: Validator) -> list[dict]:
    """Prüft die Folienliste von PUT /items. Feldfehler: items.<i>.<feld>."""
    if not isinstance(raw, list):
        v.error("items", "Folien bitte als Liste angeben.")
        return []
    if len(raw) > MAX_ITEMS:
        v.error("items", f"Höchstens {MAX_ITEMS} Folien je Präsentation.")
        return []
    wanted = sorted({it["content_id"] for it in raw if isinstance(it, dict) and is_int(it.get("content_id"))})
    ids = {r["id"] for r in dbm.rows(conn, f"SELECT id FROM contents WHERE id IN ({','.join('?' * len(wanted))})",
                                     wanted)} if wanted else set()
    out = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            v.error(f"items.{i}", "Ungültige Folie.")
            continue
        s = Validator(it, prefix=f"{v.prefix}items.{i}.")
        cid = it.get("content_id")
        if not is_int(cid):
            s.error("content_id", "Bitte einen Inhalt wählen.")
        elif cid not in ids:
            s.error("content_id", "Der Inhalt existiert nicht mehr. Bitte die Folie entfernen.")
        item_id = it.get("id") if is_int(it.get("id")) else None
        enabled = s.boolean("enabled", default=True)
        duration = s.number("duration_s", min_value=1, max_value=3600, allow_none=True)
        transition = s.choice("transition", schemas.TRANSITIONS, allow_none=True)
        valid_from = s.local_datetime("valid_from")
        valid_until = s.local_datetime("valid_until")
        if valid_from and valid_until and valid_until <= valid_from:
            s.error("valid_until", "Das Ende der Gültigkeit muss nach dem Beginn liegen.")
        caption = s.text("caption", max_len=300, default="") or ""
        options = schemas.normalize_item_options(it.get("options"), s, f"{s.prefix}options.")
        v.merge(s)
        out.append({"id": item_id, "content_id": cid, "enabled": enabled, "duration_s": duration,
                    "transition": transition, "valid_from": valid_from, "valid_until": valid_until,
                    "caption": caption, "options": options})
    return out


def replace_items(conn, pid: int, items: list[dict]) -> None:
    """Ersetzt alle Folien; bekannte IDs dieser Präsentation bleiben erhalten (Reihenfolge = Liste)."""
    existing = {r["id"] for r in conn.execute("SELECT id FROM presentation_items WHERE presentation_id = ?",
                                              (pid,)).fetchall()}
    keep = {it["id"] for it in items if it.get("id") in existing}
    for old in existing - keep:
        conn.execute("DELETE FROM presentation_items WHERE id = ?", (old,))
    used: set[int] = set()
    for pos, it in enumerate(items):
        values = {"presentation_id": pid, "position": pos, "content_id": it["content_id"],
                  "enabled": 1 if it.get("enabled", True) else 0, "duration_s": it.get("duration_s"),
                  "transition": it.get("transition"), "valid_from": it.get("valid_from"),
                  "valid_until": it.get("valid_until"), "caption": it.get("caption") or "",
                  "options": dbm.jdumps(it.get("options") or {})}
        iid = it.get("id")
        if iid in keep and iid not in used:
            used.add(iid)
            dbm.update(conn, "presentation_items", iid, values)
        elif is_int(iid) and iid not in used and \
                dbm.scalar(conn, "SELECT 1 FROM presentation_items WHERE id = ?", (iid,)) is None:
            # „Verwerfen“: ursprüngliche Folien-ID wiederverwenden, wenn frei
            used.add(iid)
            dbm.insert(conn, "presentation_items", dict(values, id=iid))
        else:
            dbm.insert(conn, "presentation_items", values)


# ------------------------------------------------------------------ Freigabe

# Freigabe zurücksetzen (Veröffentlichen, Verwerfen, Bearbeiten nach dem Einreichen)
REVIEW_RESET = "review_state = 'none', review_note = '', review_by = NULL, review_at = NULL, review_hash = NULL"


def review_changed(p: dict, resolver: Resolver) -> bool:
    """Eingereicht, aber der Entwurf weicht seitdem ab (auch über Design, Touch-Menü oder Info-Folie)."""
    if p["review_state"] != "requested":
        return False
    return not p["review_hash"] or resolver.draft_hash(p) != p["review_hash"]


def reset_review_if_changed(conn, pid: int) -> bool:
    """Nach dem Bearbeiten: eingereichte Freigabe zurücksetzen, wenn sich der Entwurf geändert hat."""
    p = dbm.row(conn, "SELECT * FROM presentations WHERE id = ?", (pid,))
    if not p or not review_changed(p, Resolver(conn)):
        return False
    conn.execute("UPDATE presentations SET " + REVIEW_RESET + " WHERE id = ?", (pid,))
    return True


# ------------------------------------------------------------------ Veröffentlichen

def publish_problems(conn, p: dict, resolver: Resolver) -> tuple[str | None, list[dict]]:
    """Gründe, warum nicht veröffentlicht werden kann: (Meldung, betroffene Folien)."""
    items = resolver.items(p["id"])
    active = [it for it in items if it["enabled"]]
    if not active:
        return "Die Präsentation enthält keine aktive Folie. Bitte mindestens eine Folie hinzufügen oder " \
               "einschalten.", []
    bad = []
    for it in active:
        c = resolver.content(it["content_id"])
        if not c or c["status"] != "ready":
            bad.append({"item_id": it["id"], "content_id": it["content_id"], "title": c["title"] if c else "",
                        "status": c["status"] if c else "missing"})
    if bad:
        n = len(bad)
        return (f"{n} Folie{'n' if n != 1 else ''} {'sind' if n != 1 else 'ist'} noch nicht bereit "
                "(Verarbeitung läuft oder ist fehlgeschlagen). Bitte warten oder die Folie deaktivieren."), bad
    return None, []


def publish(conn, p: dict, resolver: Resolver, user_id: int | None) -> dict:
    """Snapshot erzeugen und speichern (ohne Prüfung/Protokoll – das macht der Aufrufer)."""
    now = timeutil.now_iso()
    resolved = resolver.resolve(p, published_at=now)
    source = resolver.draft_source(p)
    conn.execute(
        "UPDATE presentations SET published_snapshot = ?, published_source = ?, published_hash = ?, "
        "published_at = ?, published_by = ?, " + REVIEW_RESET + " WHERE id = ?",
        (dbm.jdumps(resolved), dbm.jdumps(source), status_hash(resolved), now, user_id, p["id"]))
    return resolved


def discard(conn, p: dict) -> None:
    """Entwurf auf den Stand der letzten Veröffentlichung zurücksetzen."""
    src = dbm.jloads(p["published_source"], {}) or {}
    design_id = src.get("design_id")
    if design_id and dbm.scalar(conn, "SELECT 1 FROM designs WHERE id = ?", (design_id,)) is None:
        design_id = None
    menu_id = src.get("touch_menu_id")
    if menu_id and dbm.scalar(conn, "SELECT 1 FROM touch_menus WHERE id = ?", (menu_id,)) is None:
        menu_id = None
    conn.execute("UPDATE presentations SET settings = ?, design_id = ?, touch_menu_id = ?, " + REVIEW_RESET +
                 " WHERE id = ?",
                 (dbm.jdumps(schemas.merge_defaults(schemas.PRESENTATION_SETTINGS, src.get("settings"))),
                  design_id, menu_id, p["id"]))
    items = [it for it in src.get("items") or []
             if is_int(it.get("content_id")) and
             dbm.scalar(conn, "SELECT 1 FROM contents WHERE id = ?", (it["content_id"],)) is not None]
    conn.execute("DELETE FROM presentation_items WHERE presentation_id = ?", (p["id"],))
    replace_items(conn, p["id"], items)


def usages(conn, pid: int) -> list[dict]:
    """Wo läuft die Präsentation? (Standard einer Stele, Zeitplan) – für 409 beim Löschen."""
    out = [{"type": "stele", "id": r["id"], "name": r["name"]} for r in
           conn.execute("SELECT id, name FROM steles WHERE default_presentation_id = ? ORDER BY name", (pid,))]
    out += [{"type": "schedule", "id": r["id"], "name": r["label"] or r["stele_name"], "stele_id": r["stele_id"]}
            for r in conn.execute("SELECT e.id, e.label, e.stele_id, s.name AS stele_name FROM schedule_entries e "
                                  "JOIN steles s ON s.id = e.stele_id WHERE e.presentation_id = ? ORDER BY e.id",
                                  (pid,))]
    return out
