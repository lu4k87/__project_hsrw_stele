"""Hintergrund-Überwachung: Offline-Erkennung, Ping, RSS/Atom-Feeds, Aufräumen (SPEC §2 monitor.py).

Läuft als Daemon-Thread mit eigener SQLite-Verbindung (nur bei STELECMS_BACKGROUND != 0).
Jede Aufgabe ist einzeln aufrufbar (Tests) und fängt ihre Fehler selbst ab.
"""
from __future__ import annotations

import html
import http.client
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from . import appsettings, cache, security, steles, timeutil
from . import db as dbm
from .validation import is_http_url

log = logging.getLogger("stelecms.monitor")
EXT_KEY = "stelecms_monitor"

TICK_S = 10
PING_EVERY_S = 60
FEEDS_EVERY_S = 600
CLEANUP_EVERY_S = 3600
FEED_MAX_BYTES = 2 * 1024 * 1024
FEED_MAX_ITEMS = 30
_PING_MS_RE = re.compile(r"(?:time|zeit)[=<]([\d.]+)\s*ms", re.IGNORECASE)  # Linux, Windows (en/de)
_WINDOWS = os.name == "nt"


# ------------------------------------------------------------------ Offline

def check_offline(conn) -> int:
    """Erzeugt je Stele ein Ereignis „offline“, sobald sie länger als offline_after_s schweigt (einmalig)."""
    settings = appsettings.get_settings(conn)
    count = 0
    now = timeutil.utcnow()
    for s in dbm.rows(conn, "SELECT * FROM steles WHERE last_seen_at IS NOT NULL"):
        if steles.status_of(s, settings, now) != "offline":
            continue
        last = dbm.row(conn, "SELECT kind FROM stele_events WHERE stele_id = ? AND kind IN ('online', 'offline') "
                             "ORDER BY ts DESC, id DESC LIMIT 1", (s["id"],))
        if last and last["kind"] == "offline":
            continue
        with dbm.transaction(conn):
            steles.add_event(conn, s["id"], "error", "offline",
                             "Stele ist offline (keine Meldung seit "
                             f"{timeutil.format_de(s['last_seen_at'], settings['timezone'])}).")
        count += 1
    return count


# ------------------------------------------------------------------ Ping

def ping(host: str) -> tuple[bool, float | None]:
    exe = shutil.which("ping")
    if not exe or not host or host.startswith("-"):
        return False, None
    # Windows: -n Anzahl, -w Zeitgrenze in ms
    args = ["-n", "1", "-w", "1000"] if _WINDOWS else ["-c", "1", "-W", "1"]
    try:
        res = subprocess.run([exe, *args, host], capture_output=True, timeout=5, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False, None
    out = res.stdout.decode("utf-8", "replace")
    # Windows meldet auch „Zielhost nicht erreichbar“ mit 0 → Antwort nur mit TTL
    if res.returncode != 0 or (_WINDOWS and "TTL=" not in out.upper()):
        return False, None
    m = _PING_MS_RE.search(out)
    return True, (float(m.group(1)) if m else None)


def ping_all(conn) -> int:
    rows = dbm.rows(conn, "SELECT id, ip_address, last_ping_ok FROM steles WHERE ip_address != ''")
    # parallel: viele unerreichbare Stelen sollen die Offline-Erkennung im selben Thread nicht aufhalten
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: ping(s["ip_address"]), rows))
    for s, (ok, ms) in zip(rows, results):
        now = timeutil.now_iso()
        with dbm.transaction(conn):
            conn.execute("UPDATE steles SET last_ping_at = ?, last_ping_ok = ?, last_ping_ms = ? WHERE id = ?",
                         (now, 1 if ok else 0, ms, s["id"]))
            if not ok and s["last_ping_ok"] != 0:
                steles.add_event(conn, s["id"], "warning", "ping_failed",
                                 f"Die Stele antwortet nicht auf Ping ({s['ip_address']}).", ts=now)
            elif ok and s["last_ping_ok"] == 0:
                steles.add_event(conn, s["id"], "info", "ping_ok",
                                 f"Die Stele antwortet wieder auf Ping ({s['ip_address']}).", ts=now)
    return len(rows)


# ------------------------------------------------------------------ Feeds

def feed_urls(conn) -> list[str]:
    """Alle verwendeten ticker_rss_url (Designs und veröffentlichte Stände)."""
    urls: set[str] = set()
    for r in conn.execute("SELECT config FROM designs"):
        url = ((dbm.jloads(r["config"], {}) or {}).get("footer") or {}).get("ticker_rss_url")
        if url:
            urls.add(url)
    for r in conn.execute("SELECT published_snapshot FROM presentations WHERE published_snapshot IS NOT NULL"):
        snap = dbm.jloads(r["published_snapshot"], {}) or {}
        url = (((snap.get("design") or {}).get("footer")) or {}).get("ticker_rss_url")
        if url:
            urls.add(url)
    return sorted(u for u in urls if is_http_url(u))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(raw: bytes) -> list[str]:
    """Titel aus RSS 2.0 (item/title), RSS 1.0 (item/title) oder Atom (entry/title)."""
    if b"<!ENTITY" in raw[:4096].upper():
        raise ValueError("Feed mit eingebetteten Entitäten wird aus Sicherheitsgründen nicht gelesen.")
    root = ET.fromstring(raw)
    titles = []
    for el in root.iter():
        if _local(el.tag) in ("item", "entry"):
            for child in el:
                if _local(child.tag) == "title":
                    text = " ".join(html.unescape("".join(child.itertext())).split())
                    if text:
                        titles.append(text[:300])
                    break
        if len(titles) >= FEED_MAX_ITEMS:
            break
    return titles


def fetch_feed(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "SteleCMS-Feed/1.0",
                                               "Accept": "application/rss+xml, application/atom+xml, */*;q=0.5"})
    with security.open_outbound(req, timeout=10) as resp:
        raw = security.read_limited(resp, FEED_MAX_BYTES, 30)
    if len(raw) > FEED_MAX_BYTES:
        raise ValueError("Feed ist größer als 2 MB.")
    return parse_feed(raw)


def refresh_feeds(conn, fetch=fetch_feed, db_path=None) -> int:
    """Holt alle Feeds; mit `db_path` danach den Cache verwerfen (neue Meldungen → neue Manifest-Version)."""
    urls = feed_urls(conn)
    for url in urls:
        now = timeutil.now_iso()
        try:
            items = fetch(url)
            ok, err = 1, ""
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError, http.client.HTTPException) as exc:
            items, ok, err = None, 0, str(exc)[:300]
        with dbm.transaction(conn):
            if ok:
                conn.execute("INSERT INTO feed_cache(url, fetched_at, ok, items, error) VALUES (?, ?, 1, ?, '') "
                             "ON CONFLICT(url) DO UPDATE SET fetched_at = excluded.fetched_at, ok = 1, "
                             "items = excluded.items, error = ''", (url, now, dbm.jdumps(items)))
            else:
                # Letzte gute Meldungen behalten, nur Fehler vermerken
                conn.execute("INSERT INTO feed_cache(url, fetched_at, ok, items, error) VALUES (?, ?, 0, '[]', ?) "
                             "ON CONFLICT(url) DO UPDATE SET error = excluded.error", (url, now, err))
    if db_path is not None and urls:
        cache.bump(db_path)
    return len(urls)


# ------------------------------------------------------------------ Aufräumen

def cleanup(conn, cfg) -> dict:
    settings = appsettings.get_settings(conn)
    now = timeutil.utcnow()
    cutoff = timeutil.iso(now - timedelta(days=settings["retention_days"]))
    counts = {}
    with dbm.transaction(conn):
        for table, col in (("playback_log", "started_at"), ("touch_log", "ts"), ("stele_events", "ts"),
                           ("stele_metrics", "ts"), ("stele_online_segments", "end_at")):
            counts[table] = conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,)).rowcount
        counts["stele_commands"] = conn.execute(
            "DELETE FROM stele_commands WHERE created_at < ?", (cutoff,)).rowcount
        counts["jobs"] = conn.execute("DELETE FROM jobs WHERE status IN ('done', 'error') AND updated_at < ?",
                                      (cutoff,)).rowcount
        counts["pairing_requests"] = conn.execute(
            "DELETE FROM pairing_requests WHERE expires_at < ?", (timeutil.iso(now),)).rowcount
    # Medien-Ordner ohne Inhalt (Absturz zwischen Verschieben und Anlegen, gescheitertes Löschen)
    media_dir = Path(cfg["MEDIA_DIR"])
    if media_dir.is_dir():
        uids = {r["uid"] for r in conn.execute("SELECT uid FROM contents UNION SELECT uid FROM fonts")}
        limit = time.time() - 24 * 3600
        removed = 0
        for d in media_dir.iterdir():
            try:
                if d.is_dir() and not d.name.startswith(".") and d.name not in uids and d.stat().st_mtime < limit:
                    shutil.rmtree(d)
                    removed += 1
            except OSError:
                pass
        counts["media_orphans"] = removed
    # Liegengebliebene Upload-Reste
    tmp = Path(cfg["MEDIA_DIR"]) / ".tmp"
    if tmp.is_dir():
        limit = time.time() - 24 * 3600
        for f in tmp.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < limit:
                    f.unlink()
            except OSError:
                pass
    return counts


# ------------------------------------------------------------------ Thread

class Monitor(threading.Thread):
    def __init__(self, cfg):
        super().__init__(name="stelecms-monitor", daemon=True)
        self.cfg = cfg
        self._stop = threading.Event()
        self._last = {"ping": 0.0, "feeds": 0.0, "cleanup": 0.0}

    def stop(self) -> None:
        self._stop.set()

    def _due(self, key: str, every: float) -> bool:
        now = time.monotonic()
        if now - self._last[key] >= every:
            self._last[key] = now
            return True
        return False

    def run(self) -> None:
        self._stop.wait(2)
        while not self._stop.is_set():
            conn = None
            try:
                conn = dbm.connect(self.cfg["DB_PATH"])
                self._step(conn, "Offline-Erkennung", check_offline, conn)
                if self._due("ping", PING_EVERY_S):
                    self._step(conn, "Ping", ping_all, conn)
                if self._due("feeds", FEEDS_EVERY_S):
                    self._step(conn, "Feeds", refresh_feeds, conn, fetch_feed, self.cfg["DB_PATH"])
                if self._due("cleanup", CLEANUP_EVERY_S):
                    self._step(conn, "Aufräumen", cleanup, conn, self.cfg)
            except Exception:  # noqa: BLE001 – Überwachung darf nie abbrechen
                log.exception("Monitor: unerwarteter Fehler")
            finally:
                if conn is not None:
                    conn.close()
            self._stop.wait(TICK_S)

    @staticmethod
    def _step(_conn, name, fn, *args):
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("Monitor: %s fehlgeschlagen", name)


def start_monitor(app) -> Monitor:
    mon = Monitor(app.config)
    app.extensions[EXT_KEY] = mon
    mon.start()
    return mon
