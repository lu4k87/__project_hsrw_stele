"""Stelen: Status, Ausgabe (Stele-Objekt §7.7), Ereignisse, Online-Segmente, Warnungen (Alerts)."""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from . import appsettings, schemas, timeutil
from . import db as dbm
from .resolve import Resolver
from .schedule import SteleSchedule

COMMAND_LABELS = {"reload": "Neu laden", "identify": "Identifizieren", "screenshot": "Screenshot",
                  "clear_cache": "Zwischenspeicher leeren"}
PLAYER_COMMANDS = ("reload", "identify", "clear_cache")
AGENT_COMMANDS = ("screenshot",)
COMMAND_TTL_S = 10 * 60        # nicht zugestellte Befehle verfallen (Stele lange offline → kein Neustart Tage später)
COMMAND_REDELIVER_S = 3 * 60   # zugestellt, aber kein Ergebnis (Antwort verloren) → erneut zustellen


def deliver_commands(conn, sid: int, kinds: tuple, now: str) -> list[dict]:
    """Offene Befehle für Player bzw. Agent; beide führen jede ID nur einmal aus (Wiederholung ist sicher)."""
    now_dt = timeutil.parse_iso(now) or timeutil.utcnow()
    expired = timeutil.iso(now_dt - timedelta(seconds=COMMAND_TTL_S))
    redeliver = timeutil.iso(now_dt - timedelta(seconds=COMMAND_REDELIVER_S))
    marks = ",".join("?" for _ in kinds)
    conn.execute(f"UPDATE stele_commands SET done_at = ?, result = 'Fehler: abgelaufen – Stele hat den Befehl nicht "
                 f"rechtzeitig bestätigt' WHERE stele_id = ? AND done_at IS NULL AND created_at < ? "
                 f"AND command IN ({marks})", (now, sid, expired, *kinds))
    rows = dbm.rows(conn, f"SELECT * FROM stele_commands WHERE stele_id = ? AND done_at IS NULL "
                          f"AND (delivered_at IS NULL OR delivered_at < ?) AND command IN ({marks}) ORDER BY id",
                    (sid, redeliver, *kinds))
    for r in rows:
        conn.execute("UPDATE stele_commands SET delivered_at = ? WHERE id = ?", (now, r["id"]))
    return [{"id": r["id"], "command": r["command"], "payload": dbm.jloads(r["payload"], {})} for r in rows]
MANIFEST_OUTDATED_S = 300


def status_of(stele: dict, settings: dict, now=None) -> str:
    age = timeutil.seconds_since(stele["last_seen_at"], now)
    if age is None:
        return "never"
    if age > settings["offline_after_s"]:
        return "offline"
    state = dbm.jloads(stele["last_state"], {})
    return "standby" if state.get("mode") == "standby" else "online"


def add_event(conn, stele_id: int, level: str, kind: str, message: str, details: dict | None = None,
              ts: str | None = None) -> None:
    dbm.insert(conn, "stele_events", {"stele_id": stele_id, "ts": ts or timeutil.now_iso(), "level": level,
                                      "kind": kind, "message": message[:500], "details": dbm.jdumps(details or {})})


def touch_online(conn, stele: dict, settings: dict, now_iso: str) -> None:
    """Online-Segment verlängern oder (nach Lücke) neu beginnen + Ereignis „online“."""
    seg = dbm.row(conn, "SELECT * FROM stele_online_segments WHERE stele_id = ? ORDER BY end_at DESC, id DESC "
                        "LIMIT 1", (stele["id"],))
    gap = timeutil.seconds_since(seg["end_at"], timeutil.parse_iso(now_iso)) if seg else None
    if seg and gap is not None and gap <= settings["offline_after_s"]:
        conn.execute("UPDATE stele_online_segments SET end_at = ? WHERE id = ?", (now_iso, seg["id"]))
        return
    dbm.insert(conn, "stele_online_segments", {"stele_id": stele["id"], "start_at": now_iso, "end_at": now_iso})
    if stele["last_seen_at"] is None:
        add_event(conn, stele["id"], "info", "online", "Stele hat sich zum ersten Mal gemeldet.", ts=now_iso)
    else:
        add_event(conn, stele["id"], "info", "online", "Stele ist wieder online.", ts=now_iso)


def screenshot_path(cfg, stele_id: int) -> Path:
    return Path(cfg["SCREENSHOT_DIR"]) / str(int(stele_id)) / "latest.jpg"


def delete_screenshots(cfg, stele_id: int) -> None:
    shutil.rmtree(Path(cfg["SCREENSHOT_DIR"]) / str(int(stele_id)), ignore_errors=True)


def serialize(conn, stele: dict, resolver: Resolver, settings: dict | None = None, now=None) -> dict:
    settings = settings or resolver.settings
    status = status_of(stele, settings, now)
    online = status in ("online", "standby")
    state = dbm.jloads(stele["last_state"], {})
    agent = dbm.jloads(stele["last_agent"], {})
    sched = SteleSchedule(conn, stele, settings["timezone"])
    now_info = sched.now(now)
    item = None
    mode = None
    if online:
        cur = state.get("current") or None
        if isinstance(cur, dict):
            c = resolver.content(cur.get("content_id"))
            item = {"id": cur.get("item_id"), "title": cur.get("title") or (c["title"] if c else ""),
                    "content_type": c["type"] if c else None}
        mode = state.get("mode")
    now_info.update({"item": item, "mode": mode})
    default = None
    if stele["default_presentation_id"]:
        p = resolver.presentation(stele["default_presentation_id"])
        if p:
            default = {"id": p["id"], "name": p["name"], "status": resolver.status(p)}
    player = None
    if state.get("player_version") or state.get("user_agent"):
        manifest_version = resolver.manifest_version(stele)
        player = {"version": state.get("player_version"), "user_agent": state.get("user_agent"),
                  "screen": state.get("screen"), "manifest_version": state.get("manifest_version"),
                  "manifest_current": state.get("manifest_version") == manifest_version,
                  "uptime_s": state.get("uptime_s")}
    agent_out = None
    if stele["last_agent_at"]:
        agent_out = {"last_at": stele["last_agent_at"], "hostname": agent.get("hostname"), "os": agent.get("os"),
                     "cpu": agent.get("cpu"), "ram": agent.get("ram"), "disk": agent.get("disk"),
                     "temp": agent.get("temp"), "uptime_s": agent.get("uptime_s"),
                     "screenshot_at": agent.get("screenshot_at")}
    w, h = stele["width"], stele["height"]
    return {
        "id": stele["id"], "name": stele["name"], "location": stele["location"],
        "ip_address": stele["ip_address"], "width": w, "height": h,
        "orientation": "landscape" if w > h else "portrait",
        "settings": schemas.merge_defaults(schemas.STELE_SETTINGS, dbm.jloads(stele["settings"], {})),
        "paired": stele["paired_at"] is not None, "paired_at": stele["paired_at"],
        "default_presentation": default,
        "status": status,
        "last_seen_at": stele["last_seen_at"],
        "now": now_info,
        "next_change": sched.next_change(now),
        "player": player,
        "network": {"ip_address": stele["ip_address"],
                    "ping_ok": None if stele["last_ping_ok"] is None else bool(stele["last_ping_ok"]),
                    "ping_ms": stele["last_ping_ms"], "checked_at": stele["last_ping_at"]},
        "agent": agent_out,
        "created_at": stele["created_at"], "updated_at": stele["updated_at"],
    }


# ------------------------------------------------------------------ Alerts

def compute_alerts(conn, cfg, resolver: Resolver, settings: dict | None = None) -> list[dict]:
    settings = settings or resolver.settings
    now = timeutil.utcnow()
    alerts = []
    for s in dbm.rows(conn, "SELECT * FROM steles ORDER BY name COLLATE NOCASE"):
        st = status_of(s, settings, now)
        name = f"„{s['name']}“"
        if st == "never":
            alerts.append({"level": "warning", "code": "stele_never_seen", "stele_id": s["id"],
                           "message": f"Stele {name} hat sich noch nie gemeldet. Player-Link auf der Stele öffnen "
                                      "oder die Stele koppeln.", "since": s["created_at"]})
            continue
        if st == "offline":
            alerts.append({"level": "error", "code": "stele_offline", "stele_id": s["id"],
                           "message": f"Stele {name} ist offline. Stromversorgung und Netzwerk prüfen.",
                           "since": s["last_seen_at"]})
            continue
        state = dbm.jloads(s["last_state"], {})
        since = state.get("manifest_mismatch_since")
        if since and (timeutil.seconds_since(since, now) or 0) > MANIFEST_OUTDATED_S:
            current = resolver.manifest_version(s)
            if state.get("manifest_version") != current:
                alerts.append({"level": "warning", "code": "manifest_outdated", "stele_id": s["id"],
                               "message": f"Stele {name} zeigt seit über 5 Minuten einen veralteten Stand. "
                                          "Befehl „Neu laden“ senden.", "since": since})
        sched = SteleSchedule(conn, s, settings["timezone"])
        if sched.now(now)["source"] == "none":
            alerts.append({"level": "warning", "code": "no_presentation", "stele_id": s["id"],
                           "message": f"Auf Stele {name} läuft keine Präsentation. Standard-Präsentation festlegen "
                                      "und veröffentlichen.", "since": s["updated_at"]})
    try:
        du = shutil.disk_usage(cfg["DATA_DIR"])
        if du.total and du.free / du.total < 0.10:
            alerts.append({"level": "warning", "code": "disk_low",
                           "message": f"Wenig Speicherplatz: nur noch {du.free / du.total * 100:.0f} % frei. "
                                      "Nicht verwendete Inhalte löschen.", "since": timeutil.now_iso()})
    except OSError:
        pass
    day_ago = timeutil.iso(now - timedelta(hours=24))
    for j in dbm.rows(conn, "SELECT j.*, c.title FROM jobs j LEFT JOIN contents c ON c.id = j.content_id "
                            "WHERE j.status = 'error' AND j.updated_at >= ? ORDER BY j.updated_at DESC LIMIT 10",
                      (day_ago,)):
        alerts.append({"level": "warning", "code": "job_failed", "content_id": j["content_id"],
                       "message": f"Verarbeitung von „{j['title'] or 'Inhalt'}“ fehlgeschlagen: {j['message']}",
                       "since": j["updated_at"]})
    return alerts


def get_settings(conn) -> dict:
    return appsettings.get_settings(conn)


# ------------------------------------------------------------ Stelen-Schlüssel

KEY_COOKIE = "stele_key"
KEY_HEADER = "X-Stele-Key"
KEY_COOKIE_MAX_AGE = 10 * 365 * 24 * 3600


def key_from_request() -> str:
    from flask import request
    key = request.headers.get(KEY_HEADER) or request.cookies.get(KEY_COOKIE) or ""
    return key.strip()[:200]


def stele_by_key(conn, key: str | None) -> dict | None:
    if not key:
        return None
    return dbm.row(conn, "SELECT * FROM steles WHERE player_key = ?", (key,))


def set_key_cookie(resp, key: str):
    resp.set_cookie(KEY_COOKIE, key, max_age=KEY_COOKIE_MAX_AGE, httponly=True, samesite="Lax", path="/")
    return resp


def player_url(stele: dict) -> str:
    from flask import request
    return f"{request.host_url.rstrip('/')}/player/?key={stele['player_key']}"
