"""Robustheit und Absicherung: Kopplung, Befehle, Zeitstempel, Jobs, Speicherplatz, ausgehende Abrufe."""
from __future__ import annotations

import os
import shutil
import sys
import time
from collections import namedtuple
from datetime import timedelta
from pathlib import Path

import pytest

from stelecms import db as dbm
from stelecms import jobs, media, monitor, resolve, security, steles, timeutil

from conftest import Client, make_stele, png_bytes, stele_key, upload


def _player(app, key=None):
    c = Client(app)
    if key:
        c.c.environ_base["HTTP_X_STELE_KEY"] = key
    return c


def _db(app):
    return dbm.connect(app.config["DB_PATH"])


def _ago(seconds: float) -> str:
    return timeutil.iso(timeutil.utcnow() - timedelta(seconds=seconds))


# ------------------------------------------------------------------ Kopplung

def test_pairing_key_only_shortly_after_first_delivery(admin, app):
    code = _player(app).post("/api/player/pairing", json={}).get_json()["code"]
    make_stele(admin, "Gekoppelt", pairing_code=code)
    first = _player(app).get(f"/api/player/pairing/{code}").get_json()
    assert first["status"] == "paired" and first["key"]
    # Nachfrist für eine verlorene Antwort
    assert _player(app).get(f"/api/player/pairing/{code}").get_json()["status"] == "paired"
    conn = _db(app)
    expires = timeutil.parse_iso(dbm.scalar(conn, "SELECT expires_at FROM pairing_requests WHERE code = ?", (code,)))
    assert expires - timeutil.utcnow() <= timedelta(seconds=31)
    conn.execute("UPDATE pairing_requests SET expires_at = ? WHERE code = ?", (_ago(1), code))
    conn.close()
    assert _player(app).get(f"/api/player/pairing/{code}").get_json() == {"status": "expired"}


def test_pairing_status_is_throttled(app):
    p = _player(app)
    codes = [p.get("/api/player/pairing/000000").status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_rate_limiter_forgets_old_keys():
    rl = security.RateLimiter(1, 0.01)
    for i in range(1001):
        rl.check_and_hit(f"ip{i}")
    time.sleep(0.02)
    rl.check_and_hit("neu")
    assert len(rl._hits) == 1


# ------------------------------------------------------------------ Befehle

def test_commands_expire_and_are_redelivered(admin, app):
    s = make_stele(admin)
    player = _player(app, stele_key(s))
    old = admin.post(f"/api/steles/{s['id']}/commands", json={"command": "reload"}).get_json()["id"]
    lost = admin.post(f"/api/steles/{s['id']}/commands", json={"command": "identify"}).get_json()["id"]
    conn = _db(app)
    conn.execute("UPDATE stele_commands SET created_at = ? WHERE id = ?", (_ago(steles.COMMAND_TTL_S + 5), old))
    conn.close()
    hb = player.post("/api/player/heartbeat", json={}).get_json()
    assert [c["id"] for c in hb["commands"]] == [lost]          # abgelaufener Neustart wird nicht mehr zugestellt
    assert player.post("/api/player/heartbeat", json={}).get_json()["commands"] == []
    conn = _db(app)
    conn.execute("UPDATE stele_commands SET delivered_at = ? WHERE id = ?",
                 (_ago(steles.COMMAND_REDELIVER_S + 5), lost))
    conn.close()
    hb = player.post("/api/player/heartbeat", json={}).get_json()
    assert [c["id"] for c in hb["commands"]] == [lost]          # Antwort verloren → erneut
    items = {c["id"]: c for c in admin.get(f"/api/steles/{s['id']}/commands").get_json()["items"]}
    assert items[old]["result"].startswith("Fehler: abgelaufen")


def test_player_timestamps_are_clamped(admin, app):
    s = make_stele(admin)
    future = timeutil.iso(timeutil.utcnow() + timedelta(days=30))
    _player(app, stele_key(s)).post("/api/player/heartbeat", json={
        "played": [{"started_at": future, "duration_s": 7, "title": "X"}]})
    conn = _db(app)
    started = timeutil.parse_iso(dbm.scalar(conn, "SELECT started_at FROM playback_log"))
    conn.close()
    assert abs((started - timeutil.utcnow()).total_seconds()) < 60


# ------------------------------------------------------------------ Jobs und Medien

def test_orphaned_processing_content_gets_a_job(app):
    conn = _db(app)
    cid = media.insert_content(conn, ctype="pdf", title="Ohne Job", tags=[], user_id=None, uid="0123456789abcdef",
                               status="processing", progress=0, data={"original_file": "original.pdf"},
                               file_name="a.pdf", mime="application/pdf", size_bytes=1)
    assert jobs.requeue_orphans(conn) == 0                      # ganz frisch: Upload reiht selbst ein
    conn.execute("UPDATE contents SET created_at = ? WHERE id = ?", (_ago(jobs.ORPHAN_AFTER_S + 5), cid))
    assert jobs.requeue_orphans(conn) == 1
    assert jobs.requeue_orphans(conn) == 0                      # offener Job vorhanden
    conn.close()


def test_ffmpeg_timeout_without_output():
    t0 = time.monotonic()
    rc, _ = media.run_ffmpeg_progress([sys.executable, "-c", "import time; time.sleep(30)"], None, lambda pct: None, timeout=0.5)
    assert rc != 0 and time.monotonic() - t0 < 5


def test_cleanup_removes_orphaned_media_folders(app):
    media_dir = Path(app.config["MEDIA_DIR"])
    orphan = media_dir / "deadbeefdeadbeef"
    orphan.mkdir()
    fresh = media_dir / "feedfacefeedface"
    fresh.mkdir()
    old = time.time() - 2 * 86400
    os.utime(orphan, (old, old))
    conn = _db(app)
    counts = monitor.cleanup(conn, app.config)
    conn.close()
    assert counts["media_orphans"] == 1 and not orphan.exists() and fresh.exists()


def test_upload_rejected_when_disk_is_full(admin, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10, 10, 0))
    r = upload(admin, "a.png", png_bytes())
    assert r.status_code == 507 and r.get_json()["error"]["code"] == "insufficient_storage"


# ------------------------------------------------------------------ Manifest und ausgehende Abrufe

def test_manifest_version_ignores_feed_fetch_time():
    a = {"feeds": {"https://x/rss": {"items": ["A"], "fetched_at": "2026-01-01T00:00:00Z"}}}
    b = {"feeds": {"https://x/rss": {"items": ["A"], "fetched_at": "2026-01-01T00:10:00Z"}}}
    c = {"feeds": {"https://x/rss": {"items": ["B"], "fetched_at": "2026-01-01T00:10:00Z"}}}
    va, vb, vc = (resolve.finalize_manifest(m)["version"] for m in (a, b, c))
    assert va == vb != vc


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8090/", "http://[::1]/",
                                 "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/"])
def test_outbound_requests_to_server_itself_are_blocked(url):
    with pytest.raises(ValueError):
        security.check_outbound_host(url)
    assert media.check_url(url)["ok"] is False


def test_outbound_lan_is_blocked_by_default(monkeypatch):
    monkeypatch.setattr(security.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("192.168.1.20", 0))])
    with pytest.raises(ValueError):
        security.check_outbound_host("http://intranet.local/")


def test_outbound_lan_requires_exact_allowlist(monkeypatch):
    monkeypatch.setattr(security.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("192.168.1.20", 0))])
    monkeypatch.setenv('STELECMS_OUTBOUND_PRIVATE_HOSTS', 'intranet.local')
    security.check_outbound_host("http://intranet.local/")
    with pytest.raises(ValueError):
        security.check_outbound_host("http://other.local/")
