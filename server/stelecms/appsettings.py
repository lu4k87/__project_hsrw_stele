"""Systemeinstellungen (SPEC §5.1): Standardwerte, Lesen, Prüfen, Speichern."""
from __future__ import annotations

import copy

from . import db as dbm
from .timeutil import is_valid_tz

DEFAULT_SETTINGS = {
    "org_name": "Meine Organisation",
    "timezone": "Europe/Berlin",
    "session_idle_minutes": 60,
    "lockout_attempts": 5,
    "lockout_minutes": 5,
    "password_min_length": 8,
    "dev_login_enabled": True,
    "upload_max_mb": 128,
    "auto_transcode": True,
    "offline_after_s": 45,
    "retention_days": 30,
    "default_design_id": None,
    "default_slide_duration_s": 7,
    "stale_after_days": 7,
}

# Felder, die jeder angemeldete Benutzer lesen darf
PUBLIC_KEYS = ("org_name", "timezone", "default_slide_duration_s")

# (min, max) für Ganzzahlen
_INT_RANGES = {
    "session_idle_minutes": (5, 1440),
    "lockout_attempts": (3, 20),
    "lockout_minutes": (1, 1440),
    "password_min_length": (6, 64),
    "upload_max_mb": (1, 128),
    "offline_after_s": (20, 3600),
    "retention_days": (1, 3650),
    "default_slide_duration_s": (2, 600),
    "stale_after_days": (1, 365),
}


def get_settings(conn) -> dict:
    result = copy.deepcopy(DEFAULT_SETTINGS)
    for r in conn.execute("SELECT key, value FROM settings").fetchall():
        if r["key"] in result:
            result[r["key"]] = dbm.jloads(r["value"], result[r["key"]])
    result["upload_max_mb"] = min(int(result["upload_max_mb"]), _INT_RANGES["upload_max_mb"][1])
    return result


def validate_patch(conn, data: dict) -> tuple[dict, dict]:
    """Prüft eine Teiländerung. Rückgabe (geprüfte Werte, Feldfehler)."""
    clean, errors = {}, {}
    for key, value in data.items():
        if key not in DEFAULT_SETTINGS:
            continue  # unbekannte Schlüssel ignorieren (robust gegenüber Zusatzfeldern)
        if key == "org_name":
            if not isinstance(value, str) or not value.strip():
                errors[key] = "Bitte einen Namen der Organisation eingeben."
            elif len(value.strip()) > 80:
                errors[key] = "Höchstens 80 Zeichen."
            else:
                clean[key] = value.strip()
        elif key == "timezone":
            if not isinstance(value, str) or not is_valid_tz(value):
                errors[key] = "Unbekannte Zeitzone. Beispiel: Europe/Berlin."
            else:
                clean[key] = value
        elif key in ("dev_login_enabled", "auto_transcode"):
            if not isinstance(value, bool):
                errors[key] = "Bitte ein- oder ausschalten (true/false)."
            else:
                clean[key] = value
        elif key == "default_design_id":
            if value is None:
                clean[key] = None
            elif not isinstance(value, int) or isinstance(value, bool) or \
                    dbm.scalar(conn, "SELECT id FROM designs WHERE id = ?", (value,)) is None:
                errors[key] = "Dieses Design gibt es nicht."
            else:
                clean[key] = value
        elif key in _INT_RANGES:
            lo, hi = _INT_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
                errors[key] = "Bitte eine ganze Zahl eingeben."
            elif not lo <= value <= hi:
                errors[key] = f"Erlaubt sind Werte von {lo} bis {hi}."
            else:
                clean[key] = int(value)
    return clean, errors


def save_settings(conn, values: dict) -> None:
    for key, value in values.items():
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, dbm.jdumps(value)))
