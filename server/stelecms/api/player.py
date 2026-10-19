"""Player-API (SPEC §9.8): Kopplung, Manifest, Heartbeat. Zugang über den Stelen-Schlüssel."""
from __future__ import annotations

import secrets
from datetime import timedelta

from flask import Blueprint, current_app, jsonify

from .. import appsettings, steles, timeutil
from .. import db as dbm
from ..errors import ApiError
from ..resolve import Resolver
from ..security import client_ip
from ..validation import body, is_int, is_number
from .manifest import manifest_response

bp = Blueprint("api_player", __name__, url_prefix="/api/player")

PAIRING_TTL_MIN = 10
PAIRING_REDELIVER_S = 30  # Nachfrist nach der ersten Auslieferung des Schlüssels (verlorene Antwort)
MAX_ERRORS, MAX_PLAYED, MAX_TOUCH, MAX_RESULTS = 20, 500, 500, 50
TOUCH_EVENTS = ("session_start", "tile_open", "session_end")


def require_stele(conn) -> dict:
    s = steles.stele_by_key(conn, steles.key_from_request())
    if s is None:
        raise ApiError(401, "unauthenticated", "Unbekannter Stelen-Schlüssel. Bitte die Stele neu koppeln.")
    return s


def _s(value, max_len: int = 200) -> str:
    return value[:max_len] if isinstance(value, str) else ""


def _num(value):
    return float(value) if is_number(value) else None


def _int(value):
    return value if is_int(value) else None


def _ts(value, fallback: str) -> str:
    """Zeitstempel der Player-Uhr; unplausibel (> 1 Tag daneben, z. B. falsche Uhr) → Serverzeit.

    Sonst fallen Zeilen nie unter die Aufbewahrungsfrist und verzerren die Statistik.
    """
    dt = timeutil.parse_iso(value) if isinstance(value, str) else None
    if dt is None or abs((dt - timeutil.utcnow()).total_seconds()) > 86400:
        return fallback
    return timeutil.iso(dt)


def _screen(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {"w": _int(value.get("w")), "h": _int(value.get("h"))}


# ------------------------------------------------------------------ Kopplung

@bp.post("/pairing")
def pairing_start():
    retry = current_app.extensions["stelecms_limiters"]["pairing"].check_and_hit(client_ip())
    if retry:
        raise ApiError(429, "rate_limited", "Zu viele Kopplungsanfragen. Bitte kurz warten.",
                       details={"retry_after_s": int(retry)})
    conn = dbm.get_db()
    data = body()
    info = data.get("device_info") if isinstance(data.get("device_info"), dict) else {}
    device_info = {"user_agent": _s(info.get("user_agent"), 400), "screen": _screen(info.get("screen")),
                   "ip": client_ip()}
    now = timeutil.utcnow()
    expires = timeutil.iso(now + timedelta(minutes=PAIRING_TTL_MIN))
    with dbm.transaction(conn):
        conn.execute("DELETE FROM pairing_requests WHERE expires_at < ? AND claimed_at IS NULL",
                     (timeutil.iso(now - timedelta(hours=1)),))
        for _ in range(50):
            code = f"{secrets.randbelow(1_000_000):06d}"
            if dbm.scalar(conn, "SELECT 1 FROM pairing_requests WHERE code = ?", (code,)) is None:
                break
        else:  # pragma: no cover – praktisch unmöglich
            raise ApiError(503, "server_error", "Kein freier Kopplungscode. Bitte erneut versuchen.")
        dbm.insert(conn, "pairing_requests", {"code": code, "device_info": dbm.jdumps(device_info),
                                              "created_at": timeutil.iso(now), "expires_at": expires})
    return jsonify({"code": code, "expires_at": expires, "poll_interval_s": 3}), 201


@bp.get("/pairing/<code>")
def pairing_status(code: str):
    # Drossel gegen Durchprobieren der Codes (der Schlüssel steckt in der Antwort)
    retry = current_app.extensions["stelecms_limiters"]["pairing_poll"].check_and_hit(client_ip())
    if retry:
        raise ApiError(429, "rate_limited", "Zu viele Abfragen. Bitte kurz warten.",
                       details={"retry_after_s": int(retry)})
    conn = dbm.get_db()
    r = dbm.row(conn, "SELECT * FROM pairing_requests WHERE code = ?", (code,)) if code.isdigit() else None
    if r is None:
        # Unbekannt (z. B. bereits aufgeräumt) = abgelaufen → Player holt einen neuen Code
        return jsonify({"status": "expired"})
    now = timeutil.utcnow()
    expired = (timeutil.parse_iso(r["expires_at"]) or now) <= now
    if r["claimed_at"] and r["stele_id"]:
        s = dbm.row(conn, "SELECT * FROM steles WHERE id = ?", (r["stele_id"],))
        if s is None or expired:
            return jsonify({"status": "expired"})
        # Schlüssel nur einmal ausliefern: danach bleibt er nur noch kurz abrufbar (Antwort verloren → Wiederholung)
        redeliver_until = timeutil.iso(now + timedelta(seconds=PAIRING_REDELIVER_S))
        with dbm.transaction(conn):
            conn.execute("UPDATE pairing_requests SET expires_at = ? WHERE code = ? AND expires_at > ?",
                         (redeliver_until, code, redeliver_until))
        resp = jsonify({"status": "paired", "key": s["player_key"], "stele": {"id": s["id"], "name": s["name"]}})
        return steles.set_key_cookie(resp, s["player_key"])
    if expired:
        return jsonify({"status": "expired"})
    return jsonify({"status": "waiting"})


# ------------------------------------------------------------------ Manifest

@bp.get("/manifest")
def manifest():
    conn = dbm.get_db()
    s = require_stele(conn)
    return manifest_response(Resolver(conn).stele_manifest(s))


# ------------------------------------------------------------------ Heartbeat

@bp.post("/heartbeat")
def heartbeat():
    conn = dbm.get_db()
    s = require_stele(conn)
    data = body()
    settings = appsettings.get_settings(conn)
    now = timeutil.now_iso()
    current_version = Resolver(conn, settings).manifest_version(s)
    old_state = dbm.jloads(s["last_state"], {})
    reported = _s(data.get("manifest_version"), 64)
    mode = data.get("mode") if data.get("mode") in ("slideshow", "touch", "standby", "idle", "pairing") else None
    cur = data.get("current") if isinstance(data.get("current"), dict) else None
    state = {
        "player_version": _s(data.get("player_version"), 40), "manifest_version": reported, "mode": mode,
        "current": ({"presentation_id": _int(cur.get("presentation_id")), "item_id": _int(cur.get("item_id")),
                     "content_id": _int(cur.get("content_id")), "title": _s(cur.get("title")),
                     "started_at": _ts(cur.get("started_at"), now)} if cur else None),
        "screen": _screen(data.get("screen")), "user_agent": _s(data.get("user_agent"), 400),
        "uptime_s": _num(data.get("uptime_s")), "received_at": now,
    }
    # Seit wann zeigt der Player einen veralteten Stand? (für die Warnung manifest_outdated)
    if reported and reported != current_version:
        state["manifest_mismatch_since"] = old_state.get("manifest_mismatch_since") or now
    with dbm.transaction(conn):
        steles.touch_online(conn, s, settings, now)
        if old_state.get("mode") != "standby" and mode == "standby" and s["last_seen_at"]:
            steles.add_event(conn, s["id"], "info", "standby", "Stele ist im Nachtmodus (Bildschirm aus).", ts=now)
        conn.execute("UPDATE steles SET last_seen_at = ?, last_state = ? WHERE id = ?",
                     (now, dbm.jdumps(state), s["id"]))
        _store_lists(conn, s["id"], data, now)
        commands = steles.deliver_commands(conn, s["id"], steles.PLAYER_COMMANDS, now)
    return jsonify({"manifest_version": current_version, "commands": commands, "server_time": now})


def _store_lists(conn, sid: int, data: dict, now: str) -> None:
    for e in (data.get("errors") if isinstance(data.get("errors"), list) else [])[:MAX_ERRORS]:
        if not isinstance(e, dict) or not _s(e.get("message")):
            continue
        steles.add_event(conn, sid, "error", "player_error", _s(e.get("message"), 500),
                         {"item_id": _int(e.get("item_id"))}, ts=_ts(e.get("ts"), now))
    for p in (data.get("played") if isinstance(data.get("played"), list) else [])[:MAX_PLAYED]:
        if not isinstance(p, dict):
            continue
        dbm.insert(conn, "playback_log", {
            "stele_id": sid, "started_at": _ts(p.get("started_at"), now), "duration_s": _num(p.get("duration_s")),
            "presentation_id": _int(p.get("presentation_id")), "item_id": _int(p.get("item_id")),
            "content_id": _int(p.get("content_id")), "title": _s(p.get("title"))})
    for t in (data.get("touch") if isinstance(data.get("touch"), list) else [])[:MAX_TOUCH]:
        if not isinstance(t, dict) or t.get("event") not in TOUCH_EVENTS:
            continue
        dbm.insert(conn, "touch_log", {
            "stele_id": sid, "ts": _ts(t.get("ts"), now), "event": t["event"],
            "session_id": _s(t.get("session_id"), 64), "tile_id": _s(t.get("tile_id"), 64) or None,
            "label": _s(t.get("label"), 120) or None, "duration_s": _num(t.get("duration_s"))})
    apply_command_results(conn, sid, data.get("command_results"), now)


def apply_command_results(conn, sid: int, results, now: str) -> None:
    for r in (results if isinstance(results, list) else [])[:MAX_RESULTS]:
        if not isinstance(r, dict) or not is_int(r.get("id")):
            continue
        ok = r.get("ok") is not False
        msg = _s(r.get("message"), 300) or ("erledigt" if ok else "fehlgeschlagen")
        cur = conn.execute("UPDATE stele_commands SET done_at = ?, result = ?, "
                           "delivered_at = COALESCE(delivered_at, ?) WHERE id = ? AND stele_id = ? AND done_at IS NULL",
                           (now, ("OK: " if ok else "Fehler: ") + msg, now, r["id"], sid))
        if cur.rowcount and not ok:
            steles.add_event(conn, sid, "warning", "command", f"Befehl fehlgeschlagen: {msg}",
                             {"command_id": r["id"]}, ts=now)
