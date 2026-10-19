"""Protokoll (SPEC §7.9): Liste mit Filtern und CSV-Export."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

from flask import Blueprint, Response, jsonify, request

from .. import appsettings, timeutil
from .. import audit as auditm
from .. import db as dbm
from ..errors import ApiError
from ..permissions import require
from .common import arg_id, arg_int

bp = Blueprint("api_audit", __name__, url_prefix="/api/audit")

EXPORT_MAX = 50_000


def _bound(name: str, tz: str, end: bool) -> str | None:
    """from/to: Datum (JJJJ-MM-TT, Wanduhr; `to` inklusive) oder ISO-Zeitstempel."""
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    if timeutil.is_date(raw):
        d = date.fromisoformat(raw) + (timedelta(days=1) if end else timedelta())
        return timeutil.iso(timeutil.local_wall_to_utc(d, 0, tz))
    dt = timeutil.parse_iso(raw)
    if dt is None:
        raise ApiError(422, "validation_error", "Bitte den Zeitraum prüfen.",
                       fields={name: "Bitte ein Datum im Format JJJJ-MM-TT angeben."})
    return timeutil.iso(dt)


def _query(conn):
    tz = appsettings.get_settings(conn)["timezone"]
    where, params = [], []
    qtext = (request.args.get("q") or "").strip()
    if qtext:
        like = "%" + qtext.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(summary LIKE ? ESCAPE '\\' OR entity_name LIKE ? ESCAPE '\\' OR username LIKE ? ESCAPE '\\' "
                     "OR user_display LIKE ? ESCAPE '\\')")
        params += [like] * 4
    uid = arg_id("user_id")
    if uid is not None:
        where.append("user_id = ?")
        params.append(uid)
    for key in ("entity_type", "action"):
        val = (request.args.get(key) or "").strip()
        if val:
            vals = [x.strip() for x in val.split(",") if x.strip()]
            where.append(f"{key} IN ({','.join('?' for _ in vals)})")
            params += vals
    lo, hi = _bound("from", tz, False), _bound("to", tz, True)
    if lo:
        where.append("ts >= ?")
        params.append(lo)
    if hi:
        where.append("ts < ?")
        params.append(hi)
    return (" WHERE " + " AND ".join(where)) if where else "", params, tz


@bp.get("")
@require("audit.view")
def list_audit():
    conn = dbm.get_db()
    wsql, params, _tz = _query(conn)
    limit = arg_int("limit", 50, 1, 500)
    offset = arg_int("offset", 0, 0, 10_000_000)
    total = dbm.scalar(conn, "SELECT COUNT(*) FROM audit_log" + wsql, params)
    rows = dbm.rows(conn, f"SELECT * FROM audit_log{wsql} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                    params + [limit, offset])
    return jsonify({"items": [auditm.serialize_entry(r) for r in rows], "total": total})


@bp.get("/export.csv")
@require("audit.view")
def export_csv():
    conn = dbm.get_db()
    wsql, params, tz = _query(conn)
    rows = dbm.rows(conn, f"SELECT * FROM audit_log{wsql} ORDER BY ts DESC, id DESC LIMIT ?", params + [EXPORT_MAX])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(["Zeitpunkt", "Benutzername", "Name", "Aktion", "Bereich", "Objekt", "Objekt-ID", "Beschreibung",
                "IP-Adresse"])
    for r in rows:
        w.writerow([_csv_safe(c) for c in (
            timeutil.format_de(r["ts"], tz), r["username"], r["user_display"],
            auditm.ACTION_LABELS.get(r["action"], r["action"]),
            auditm.ENTITY_LABELS.get(r["entity_type"], r["entity_type"]), r["entity_name"],
            "" if r["entity_id"] is None else r["entity_id"], r["summary"], r["ip"])])
    stamp = timeutil.local_now(tz).strftime("%Y%m%d-%H%M")
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="stelecms-protokoll-{stamp}.csv"'})


_AFTER_SEP = re.compile(r"([,;])(\s*)(?=[=+\-@])")


def _csv_safe(value):
    """Schutz vor Formel-Injektion in Tabellenprogrammen (alle Textspalten): Anfang, auch nach Leerraum
    oder führendem Tab/CR, und nach Komma/Semikolon (Excel mit anderem Trennzeichen)."""
    if not isinstance(value, str) or not value:
        return value
    value = _AFTER_SEP.sub(r"\1\2'", value)
    return "'" + value if value.lstrip(" ").startswith(("=", "+", "-", "@", "\t", "\r")) else value
