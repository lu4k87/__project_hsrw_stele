"""SQLite-Zugriff: Verbindung je Request/Thread, Migrationen, kleine Helfer."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g

from .timeutil import now_iso, parse_iso  # noqa: F401  (Re-Export laut SPEC)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(path) -> sqlite3.Connection:
    """Neue Verbindung (Autocommit; Transaktionen explizit über `transaction`)."""
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        Path(path).chmod(0o600)
    except OSError:
        pass
    return conn


def get_db() -> sqlite3.Connection:
    """Verbindung des aktuellen Requests (lazy)."""
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Schreib-Transaktion (BEGIN IMMEDIATE). Verschachtelte Aufrufe laufen in der äußeren mit."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:  # SQLite rollt bei SQLITE_FULL/IOERR selbst zurück
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> bool:
    """Wendet fehlende Migrationen an. Rückgabe: True, wenn die Datenbank neu angelegt wurde."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] or 0
    is_new = current == 0
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    for f in files:
        num = int(f.name[:4])
        if num <= current:
            continue
        sql = f.read_text(encoding="utf-8")
        # executescript committet selbst; Versionsnummer direkt danach setzen
        conn.executescript("BEGIN;\n" + sql + f"\nINSERT INTO schema_version(version) VALUES ({num});\nCOMMIT;")
    return is_new


# ---------------------------------------------------------------- Helfer

def jloads(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def jdumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def canonical_json(obj) -> str:
    """Kanonische Form für Hashes (sortierte Schlüssel, ohne Leerzeichen)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rows(conn, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r is not None else None


def scalar(conn, sql: str, params=()):
    r = conn.execute(sql, params).fetchone()
    return r[0] if r is not None else None


def insert(conn, table: str, values: dict) -> int:
    """INSERT mit festen Spaltennamen (nur aus dem Code, nie aus Benutzereingaben)."""
    cols = list(values.keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    cur = conn.execute(sql, [values[c] for c in cols])
    return cur.lastrowid


def update(conn, table: str, row_id: int, values: dict) -> None:
    if not values:
        return
    cols = list(values.keys())
    sql = f"UPDATE {table} SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?"
    conn.execute(sql, [values[c] for c in cols] + [row_id])
