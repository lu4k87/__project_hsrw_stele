"""Prozess-Cache für abgeleitete Werte (Manifest-Version je Stele, Entwurfs-Hash je Präsentation).

Jede schreibende Admin-Anfrage und jeder Hintergrund-Schritt, der Inhalte, Feeds o. Ä. ändert, verwirft den
ganzen Cache der Datenbank (`bump`) – erst nach dem Commit. Schreibende Admin-Anfragen lesen und füllen ihn
nicht (`g.cache_bypass`), damit sie nie einen Wert von vor ihrer eigenen Änderung sehen. Ein Wert, der
während einer Änderung berechnet wurde, wird beim folgenden `bump` mit verworfen.
"""
from __future__ import annotations

import threading
import time

from flask import current_app, g, has_app_context

# Sicherheitsnetz für Änderungen außerhalb dieses Prozesses (z. B. zweiter Server auf denselben Daten)
MAX_AGE_S = 300.0

_lock = threading.Lock()
_stores: dict[str, dict] = {}   # Datenbank-Pfad → {Schlüssel: (Zeitpunkt, Wert)}


def _scope() -> str | None:
    if not has_app_context() or g.get("cache_bypass"):
        return None
    return str(current_app.config["DB_PATH"])


def get(key, compute):
    """Wert aus dem Cache oder neu berechnet; ohne App-Kontext und in schreibenden Anfragen immer neu."""
    scope = _scope()
    if scope is None:
        return compute()
    now = time.monotonic()
    with _lock:
        store = _stores.setdefault(scope, {})
        hit = store.get(key)
        if hit is not None and now - hit[0] < MAX_AGE_S:
            return hit[1]
    value = compute()
    with _lock:
        # Ein bump während compute() hat einen neuen Store angelegt → alter Wert landet im verworfenen
        if _stores.get(scope) is store:
            store[key] = (now, value)
    return value


def bump(db_path) -> None:
    """Alle Werte dieser Datenbank verwerfen (nach dem Commit einer Änderung aufrufen)."""
    with _lock:
        _stores[str(db_path)] = {}
