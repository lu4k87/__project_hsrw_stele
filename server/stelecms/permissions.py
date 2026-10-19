"""Rechte-Katalog (SPEC §6.1), effektive Rechte, Decorator `require`."""
from __future__ import annotations

from functools import wraps

from . import auth
from .errors import ApiError, forbidden

GROUPS = [
    {"key": "monitoring", "label": "Monitoring"},
    {"key": "content", "label": "Mediathek"},
    {"key": "presentations", "label": "Präsentationen"},
    {"key": "schedule", "label": "Zeitplan"},
    {"key": "steles", "label": "Stelen"},
    {"key": "users", "label": "Benutzer"},
    {"key": "system", "label": "System"},
]

# Reihenfolge = Anzeige
CATALOG = [
    {"key": "monitoring.view", "group": "monitoring", "label": "Monitoring ansehen",
     "description": "Zustand, Verfügbarkeit, Wiedergabe und Meldungen der Stelen einsehen.", "implies": []},
    {"key": "content.view", "group": "content", "label": "Mediathek ansehen",
     "description": "Bilder, Videos, PDFs, Info-Folien und Webseiten der Mediathek ansehen.", "implies": []},
    {"key": "content.edit", "group": "content", "label": "Inhalte hochladen, anlegen und bearbeiten",
     "description": "Dateien hochladen sowie Info-Folien und Webseiten anlegen und ändern.",
     "implies": ["content.view"]},
    {"key": "content.delete", "group": "content", "label": "Inhalte löschen",
     "description": "Nicht mehr verwendete Inhalte endgültig aus der Mediathek entfernen.",
     "implies": ["content.view"]},
    {"key": "presentations.view", "group": "presentations",
     "label": "Präsentationen, Designs und Touch-Menüs ansehen (inkl. Vorschau)",
     "description": "Präsentationen, Designs und Touch-Menüs öffnen und in der Vorschau abspielen.", "implies": []},
    {"key": "presentations.edit", "group": "presentations",
     "label": "Präsentationen bearbeiten und zur Freigabe einreichen",
     "description": "Folien und Einstellungen im Entwurf ändern und Entwürfe zur Freigabe einreichen.",
     "implies": ["presentations.view", "content.view"]},
    {"key": "presentations.publish", "group": "presentations",
     "label": "Präsentationen veröffentlichen, Freigaben erteilen oder ablehnen",
     "description": "Entwürfe auf die Stelen bringen und eingereichte Freigaben annehmen oder ablehnen.",
     "implies": ["presentations.view"]},
    {"key": "presentations.delete", "group": "presentations", "label": "Präsentationen löschen",
     "description": "Präsentationen entfernen, die weder Standard einer Stele noch im Zeitplan sind.",
     "implies": ["presentations.view"]},
    {"key": "designs.edit", "group": "presentations", "label": "Designs (Header und Footer) bearbeiten",
     "description": "Kopf- und Fußbereich, Laufband, Schrift und Akzentfarbe der Designs gestalten.",
     "implies": ["presentations.view", "content.view"]},
    {"key": "touch.edit", "group": "presentations", "label": "Touch-Menüs bearbeiten",
     "description": "Kacheln, Galerien und Untermenüs für Besucher an der Stele zusammenstellen.",
     "implies": ["presentations.view", "content.view"]},
    {"key": "schedule.view", "group": "schedule", "label": "Zeitplan ansehen",
     "description": "Sehen, welche Präsentation wann auf welcher Stele läuft.", "implies": []},
    {"key": "schedule.edit", "group": "schedule", "label": "Zeitplan und Standard-Präsentation bearbeiten",
     "description": "Zeitplan-Einträge anlegen und ändern sowie die Standard-Präsentation einer Stele wählen.",
     "implies": ["schedule.view", "steles.view", "presentations.view"]},
    {"key": "steles.view", "group": "steles", "label": "Stelen ansehen",
     "description": "Liste, Status und Live-Ansicht der Stelen einsehen.", "implies": []},
    {"key": "steles.control", "group": "steles", "label": "Stelen fernsteuern (neu laden, identifizieren, Screenshot)",
     "description": "Befehle an eine Stele senden, etwa neu laden oder einen Screenshot anfordern.",
     "implies": ["steles.view"]},
    {"key": "steles.manage", "group": "steles", "label": "Stelen hinzufügen, koppeln, einstellen und entfernen",
     "description": "Stelen anlegen, per Code koppeln, Einstellungen und Schlüssel verwalten und Stelen entfernen.",
     "implies": ["steles.view"]},
    {"key": "users.view", "group": "users", "label": "Benutzer ansehen",
     "description": "Benutzerkonten und ihre Rollen einsehen.", "implies": []},
    {"key": "users.manage", "group": "users", "label": "Benutzer anlegen, bearbeiten, sperren und löschen",
     "description": "Konten anlegen, ändern, deaktivieren, entsperren, Passwörter zurücksetzen und löschen.",
     "implies": ["users.view"]},
    {"key": "roles.manage", "group": "users", "label": "Rollen und Rechte verwalten",
     "description": "Rollen anlegen, umbenennen, löschen und ihre Rechte festlegen.", "implies": ["users.view"]},
    {"key": "audit.view", "group": "system", "label": "Protokoll ansehen",
     "description": "Nachvollziehen, wer wann was geändert hat, und das Protokoll exportieren.", "implies": []},
    {"key": "settings.manage", "group": "system", "label": "Systemeinstellungen ändern, Backup herunterladen",
     "description": "Systemweite Einstellungen ändern und eine Sicherung der Datenbank herunterladen.",
     "implies": []},
]

ALL_KEYS = [p["key"] for p in CATALOG]
_ORDER = {k: i for i, k in enumerate(ALL_KEYS)}
_IMPLIES = {p["key"]: p["implies"] for p in CATALOG}


def is_valid(key: str) -> bool:
    return key in _IMPLIES


def sort_keys(keys) -> list[str]:
    return sorted(set(keys), key=lambda k: _ORDER.get(k, 999))


def effective(keys, is_admin: bool = False) -> list[str]:
    """Gesetzte Rechte ∪ enthaltene (transitiv), alphabetisch sortiert."""
    if is_admin:
        return sorted(ALL_KEYS)
    result: set[str] = set()
    stack = [k for k in keys if k in _IMPLIES]
    while stack:
        k = stack.pop()
        if k in result:
            continue
        result.add(k)
        stack.extend(_IMPLIES.get(k, []))
    return sorted(result)


def catalog_groups() -> list[dict]:
    out = []
    for grp in GROUPS:
        perms = [{"key": p["key"], "label": p["label"], "description": p["description"],
                  "implies": list(p["implies"])} for p in CATALOG if p["group"] == grp["key"]]
        out.append({"key": grp["key"], "label": grp["label"], "permissions": perms})
    return out


# ------------------------------------------------------------ Prüfung

MISSING_MSG = ("Konten und Rollen lassen sich nur verwalten, wenn das eigene Konto alle ihre Rechte selbst hat. "
               "Fehlend: ")


def require_covers(conn, role_id: int, role_is_admin, message: str) -> None:
    """Ohne Administrator-Rolle nur Konten und Rollen verwalten, deren effektive Rechte das eigene Konto selbst
    hat (§6.3) – sonst ließe sich z. B. das Passwort eines mächtigeren Kontos zurücksetzen oder einer fremden Rolle
    Rechte entziehen. Administrator: alles; Ziel-Rolle Administrator: nur Administratoren (`message`).
    Für TOCTOU-Schutz zusätzlich innerhalb der Schreib-Transaktion aufrufen."""
    if auth.current_user()["role_is_admin"]:
        return
    if role_is_admin:
        raise forbidden(message)
    missing = sort_keys(set(effective(auth.role_permission_keys(conn, role_id))) - auth.current_permissions())
    if missing:
        raise ApiError(403, "forbidden", MISSING_MSG + ", ".join(missing), details={"missing_permissions": missing})

def has(perm: str) -> bool:
    return perm in auth.current_permissions()


def has_any(*perms: str) -> bool:
    cur = auth.current_permissions()
    return any(p in cur for p in perms)


def require(*perms: str, any_of: bool = False):
    """Decorator: Anmeldung + Recht(e). Standard: alle genannten Rechte; any_of=True: eines genügt."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth.ensure_authenticated()
            cur = auth.current_permissions()
            ok = any(p in cur for p in perms) if any_of else all(p in cur for p in perms)
            if not ok:
                raise forbidden()
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def check(*perms: str, any_of: bool = False) -> None:
    """Wie `require`, aber innerhalb einer View aufrufbar."""
    auth.ensure_authenticated()
    cur = auth.current_permissions()
    ok = any(p in cur for p in perms) if any_of else all(p in cur for p in perms)
    if not ok:
        raise forbidden()
