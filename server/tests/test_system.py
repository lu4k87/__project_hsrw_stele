"""Designs, Touch-Menüs, Übersicht, Monitoring, Protokoll, Einstellungen, Sicherung, Auslieferung, Monitor, Seed."""
from __future__ import annotations

import sqlite3
import subprocess
from datetime import timedelta

import pytest

from stelecms import create_app, monitor, timeutil
from stelecms import db as dbm

from conftest import FAST_HASH, Client, make_image, make_presentation, make_stele, make_text, stele_key

# ------------------------------------------------------------------ Designs, Touch-Menüs


def test_design_crud_and_validation(admin):
    img = make_image(admin, "logo.png", w=400, h=200)
    r = admin.post("/api/designs", json={"name": "Hell", "config": {"header": {"height": 50, "bg_color": "blau"}}})
    assert r.status_code == 422
    assert {"config.header.height", "config.header.bg_color"} <= set(r.get_json()["error"]["fields"])
    r = admin.post("/api/designs", json={"name": "Hell", "config": {"header": {"logo_content_id": img["id"]},
                                                                    "footer": {"mode": "text", "text": "Hallo"}}})
    assert r.status_code == 201
    d = r.get_json()
    assert d["config"]["footer"]["mode"] == "text" and d["config"]["theme"]["font"] == "sans"
    assert d["logo_url"] == img["urls"]["display"] and d["used_by"] == []
    copy = admin.post("/api/designs", json={"copy_from": d["id"]}).get_json()
    assert copy["name"] == "Hell (Kopie)" and copy["config"]["footer"]["text"] == "Hallo"
    assert admin.delete(f"/api/designs/{copy['id']}").get_json() == {"ok": True}


def test_design_delete_in_use(admin):
    p = make_presentation(admin, "P")
    r = admin.delete(f"/api/designs/{p['design_id']}")
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "in_use"
    d = admin.get(f"/api/designs/{p['design_id']}").get_json()
    assert d["used_by"] == [{"id": p["id"], "name": "P", "status": "draft"}]


def test_rename_audit_and_in_use_touch_menu(admin):
    """Gemeinsame Helfer (common.save_named, in_use_conflict) für Designs und Touch-Menüs."""
    m = admin.post("/api/touch-menus", json={"name": "Alt"}).get_json()
    admin.patch(f"/api/touch-menus/{m['id']}", json={"name": "Neu"})
    admin.patch(f"/api/touch-menus/{m['id']}", json={"name": "Neu", "config": {"title": "Hallo"}})
    summaries = [e["summary"] for e in admin.get("/api/audit?entity_type=touch_menu&action=update").get_json()["items"]]
    assert any(s.endswith("hat das Touch-Menü „Alt“ in „Neu“ umbenannt") for s in summaries)
    assert any(s.endswith("hat das Touch-Menü „Neu“ bearbeitet") for s in summaries)
    p = make_presentation(admin, "P")
    assert admin.patch(f"/api/presentations/{p['id']}", json={"touch_menu_id": m["id"]}).status_code == 200
    r = admin.delete(f"/api/touch-menus/{m['id']}")
    err = r.get_json()["error"]
    assert r.status_code == 409 and err["code"] == "in_use"
    assert err["message"] == ("Das Touch-Menü „Neu“ wird noch von 1 Präsentation verwendet. "
                              "Bitte dort zuerst ein anderes Touch-Menü wählen.")
    assert err["details"]["usages"] == [{"type": "presentation", "id": p["id"], "name": "P"}]


def test_touch_menu_crud(admin):
    img = make_image(admin)
    txt = make_text(admin)
    cfg = {"title": "Info", "columns": 3, "tiles": [
        {"label": "Über uns", "icon": "info", "action": {"type": "content", "content_id": txt["id"]}},
        {"label": "Galerie", "icon": "image", "image_content_id": img["id"],
         "action": {"type": "gallery", "content_ids": [img["id"]]}},
        {"label": "Mehr", "icon": "grid", "action": {"type": "submenu", "tiles": [
            {"label": "Tief", "icon": "info", "action": {"type": "submenu", "tiles": []}}]}}]}
    r = admin.post("/api/touch-menus", json={"name": "Menü", "config": cfg})
    assert r.status_code == 422
    assert "config.tiles.2.action.tiles.0.action.type" in r.get_json()["error"]["fields"]
    cfg["tiles"][2]["action"]["tiles"][0]["action"] = {"type": "content", "content_id": txt["id"]}
    r = admin.post("/api/touch-menus", json={"name": "Menü", "config": cfg})
    assert r.status_code == 201
    m = r.get_json()
    tiles = m["config"]["tiles"]
    assert all(t["id"].startswith("t-") for t in tiles) and m["config"]["columns"] == 3
    assert tiles[0]["content"]["title"] == txt["title"] and tiles[1]["image"]["id"] == img["id"]
    # Ergänzte Anzeigefelder werden beim Speichern ignoriert
    r = admin.patch(f"/api/touch-menus/{m['id']}", json={"config": m["config"]})
    assert r.status_code == 200 and r.get_json()["config"]["tiles"][0]["id"] == tiles[0]["id"]
    assert admin.get(f"/api/contents/{img['id']}").get_json()["usages"][0]["type"] == "touch_menu"
    p = make_presentation(admin, "P")
    admin.patch(f"/api/presentations/{p['id']}", json={"touch_menu_id": m["id"]})
    assert admin.delete(f"/api/touch-menus/{m['id']}").status_code == 409


def test_touch_menu_bad_icon(admin):
    r = admin.post("/api/touch-menus", json={"name": "X", "config": {"tiles": [
        {"label": "A", "icon": "totenkopf", "action": {"type": "content", "content_id": 1}}]}})
    assert r.status_code == 422 and "config.tiles.0.icon" in r.get_json()["error"]["fields"]


# ------------------------------------------------------------------ Übersicht, Monitoring


def test_dashboard_sections_by_permission(admin, betrachter, autor):
    make_stele(admin)
    d = admin.get("/api/dashboard").get_json()
    assert isinstance(d["activity"], list) and d["activity"] and d["counts"]["users"] == 4
    assert any(a["code"] == "stele_never_seen" for a in d["alerts"])
    v = betrachter.get("/api/dashboard").get_json()
    assert v["activity"] is None and isinstance(v["steles"], list) and isinstance(v["reviews"], list)
    assert autor.get("/api/dashboard").get_json()["activity"] is None


def test_dashboard_expiring_items(admin):
    txt = make_text(admin)
    p = make_presentation(admin, "P")
    soon = (timeutil.local_now("Europe/Berlin") + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    admin.put(f"/api/presentations/{p['id']}/items", json={"items": [{"content_id": txt["id"], "valid_until": soon}]})
    exp = admin.get("/api/dashboard").get_json()["expiring"]
    assert exp[0]["presentation"]["id"] == p["id"] and exp[0]["valid_until"] == soon


def test_dashboard_stale_presentations(app, admin, autor):
    txt = make_text(admin)
    p = make_presentation(admin, "Alt", content_ids=[txt["id"]], publish=True)
    make_presentation(admin, "Ungenutzt", content_ids=[txt["id"]], publish=True)
    make_stele(admin, default_presentation_id=p["id"])
    assert admin.get("/api/dashboard").get_json()["stale"] == []
    conn = dbm.connect(app.config["DB_PATH"])
    old = timeutil.iso(timeutil.utcnow() - timedelta(days=10))
    conn.execute("UPDATE presentations SET published_at = ?", (old,))
    conn.commit()
    conn.close()
    d = admin.get("/api/dashboard").get_json()
    assert [x["name"] for x in d["stale"]] == ["Alt"] and d["stale_after_days"] == 7
    assert admin.patch("/api/settings", json={"stale_after_days": 14}).status_code == 200
    assert admin.get("/api/dashboard").get_json()["stale"] == []
    assert admin.patch("/api/settings", json={"stale_after_days": 0}).status_code == 422
    assert autor.patch("/api/settings", json={"stale_after_days": 3}).status_code == 403


def test_monitoring_overview_server_block(admin):
    make_image(admin)
    ov = admin.get("/api/monitoring/overview").get_json()
    srv = ov["server"]
    assert srv["version"] == "1.0.0" and srv["content_count"] == 1 and srv["db_size_bytes"] > 0
    assert srv["jobs"] == {"queued": 0, "running": 0, "failed": 0} and srv["disk_total_bytes"] > 0
    assert ov["steles"] == [] and isinstance(ov["alerts"], list)


def test_no_presentation_alert(admin, app):
    s = make_stele(admin)
    player = Client(app)
    player.c.environ_base["HTTP_X_STELE_KEY"] = stele_key(s)
    player.post("/api/player/heartbeat", json={"mode": "standby"})
    alerts = admin.get("/api/monitoring/overview").get_json()["alerts"]
    assert any(a["code"] == "no_presentation" and a["stele_id"] == s["id"] for a in alerts)
    assert admin.get(f"/api/steles/{s['id']}").get_json()["status"] == "standby"


# ------------------------------------------------------------------ Protokoll, Einstellungen


def test_audit_filter_and_csv(admin):
    make_text(admin, "=Formel")
    r = admin.get("/api/audit?entity_type=content&action=create")
    items = r.get_json()["items"]
    assert r.get_json()["total"] == 1 and items[0]["summary"].startswith("hat die Info-Folie")
    assert items[0]["user"]["username"] == "admin" and items[0]["ip"] == "127.0.0.1"
    assert admin.get("/api/audit?q=Formel").get_json()["total"] == 1
    assert admin.get("/api/audit?from=2000-01-01&to=2000-01-02").get_json()["total"] == 0
    assert admin.get("/api/audit?from=kaputt").status_code == 422
    r = admin.get("/api/audit/export.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    text = r.data.decode("utf-8")
    assert text.startswith("﻿Zeitpunkt;") and "attachment" in r.headers["Content-Disposition"]


def test_audit_csv_neutralizes_formulas_in_all_columns(admin):
    from stelecms.api.audit import _csv_safe
    assert [_csv_safe(x) for x in ("=1", "+1", "-1", "@A", "\tA", "\rA", "ok", "", None, 7)] == \
        ["'=1", "'+1", "'-1", "'@A", "'\tA", "'\rA", "ok", "", None, 7]
    make_text(admin, "=HYPERLINK(\"http://x\")")
    text = admin.get("/api/audit/export.csv").data.decode("utf-8")
    import csv
    rows = list(csv.reader(text.lstrip("\ufeff").splitlines(), delimiter=";"))
    row = next(r for r in rows if any("HYPERLINK" in c for c in r))
    assert row[5] == "'=HYPERLINK(\"http://x\")", row  # Spalte „Objekt“
    assert not any(c[:1] in ("=", "+", "-", "@", "\t", "\r") for c in row), row


def test_settings_visibility_and_validation(admin, betrachter):
    assert set(betrachter.get("/api/settings").get_json()) == {"org_name", "timezone", "default_slide_duration_s"}
    full = admin.get("/api/settings").get_json()
    assert full["offline_after_s"] == 45 and full["default_design_id"] == 1
    r = admin.patch("/api/settings", json={"timezone": "Mond/Basis", "session_idle_minutes": 1, "org_name": ""})
    assert r.status_code == 422
    assert set(r.get_json()["error"]["fields"]) == {"timezone", "session_idle_minutes", "org_name"}
    r = admin.patch("/api/settings", json={"org_name": "Stadtmuseum", "timezone": "Europe/Vienna"})
    assert r.get_json()["org_name"] == "Stadtmuseum"
    assert admin.get("/api/audit?action=settings").get_json()["total"] == 1
    assert admin.get("/api/auth/session").get_json()["app"]["org_name"] == "Stadtmuseum"


def test_backup_and_system_info(admin, tmp_path):
    r = admin.get("/api/system/backup")
    assert r.status_code == 200 and "stelecms-backup-" in r.headers["Content-Disposition"]
    f = tmp_path / "b.db"
    f.write_bytes(r.data)
    conn = sqlite3.connect(f)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 4
    conn.close()
    info = admin.get("/api/system/info").get_json()
    assert info["version"] == "1.0.0" and info["port"] == 8090 and isinstance(info["ffmpeg"], bool)


# ------------------------------------------------------------------ Auslieferung, Fehler


@pytest.fixture
def web_app(tmp_path):
    web = tmp_path / "web"
    for rel, text in {"admin/index.html": "<!doctype html>admin", "admin/js/main.js": "export {}",
                      "player/index.html": "<!doctype html>player", "player/sw.js": "self.x=1",
                      "shared/icons.js": "export const ICONS={}"}.items():
        (web / rel).parent.mkdir(parents=True, exist_ok=True)
        (web / rel).write_text(text)
    return create_app({"DATA_DIR": tmp_path / "data", "BACKGROUND": False, "SEED_DEMO": False,
                       "PASSWORD_HASH_METHOD": FAST_HASH, "WEB_DIR": web, "TESTING": True})


def test_static_delivery_and_headers(web_app):
    c = web_app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/")
    r = c.get("/admin/")
    assert r.status_code == 200 and b"admin" in r.data and r.headers["Cache-Control"] == "no-cache"
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-src 'self' http: https:" in csp and "frame-ancestors 'self'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert c.get("/admin/presentations/3").status_code == 200          # SPA-Rückfall
    assert c.get("/admin/js/fehlt.js").status_code == 404
    assert c.get("/admin/js/main.js").mimetype == "text/javascript"
    assert c.get("/shared/icons.js").status_code == 200
    r = c.get("/player/")
    assert "frame-src *" in r.headers["Content-Security-Policy"]
    r = c.get("/player/sw.js")
    assert r.headers["Service-Worker-Allowed"] == "/player/" and r.headers["Cache-Control"] == "no-cache"


def test_https_sets_secure_cookie_and_hsts(tmp_path):
    app = create_app({"DATA_DIR": tmp_path / "https-data", "BACKGROUND": False, "SEED_DEMO": False,
                      "PASSWORD_HASH_METHOD": FAST_HASH, "TESTING": True, "HTTPS": True})
    response = app.test_client().post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    assert response.status_code == 200
    assert 'Secure' in response.headers['Set-Cookie']
    assert response.headers['Strict-Transport-Security'].startswith('max-age=31536000')


def test_production_seed_without_demo_has_no_known_accounts(tmp_path):
    app = create_app({"DATA_DIR": tmp_path / "prod-data", "BACKGROUND": False, "SEED_DEMO": False,
                      "PASSWORD_HASH_METHOD": FAST_HASH, "TESTING": False})
    conn = sqlite3.connect(app.config['DB_PATH'])
    try:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        value = conn.execute("SELECT value FROM settings WHERE key = 'dev_login_enabled'").fetchone()[0]
        assert value == 'false'
    finally:
        conn.close()


def test_player_key_cookie(web_app):
    admin = Client(web_app).login("admin")
    s = make_stele(admin)
    c = web_app.test_client()
    r = c.get(f"/player/?key={stele_key(s)}")
    cookie = r.headers["Set-Cookie"]
    assert "stele_key=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Set-Cookie" not in c.get("/player/?key=unbekannt").headers
    assert c.get("/api/player/manifest").status_code == 200   # Cookie aus der ersten Antwort


def test_json_errors_under_api(anon, admin):
    r = anon.get("/api/gibt-es-nicht")
    assert r.status_code == 404 and r.get_json()["error"]["code"] == "not_found"
    r = admin.open("DELETE", "/api/auth/session")
    assert r.status_code == 405 and r.get_json()["error"]["code"] == "method_not_allowed"
    r = admin.post("/api/presentations", data="kein json", content_type="application/json")
    assert r.status_code == 422
    assert admin.get("/api/presentations/999").get_json()["error"]["code"] == "not_found"
    assert anon.get("/api/auth/session").headers["Cache-Control"] == "no-store"


def test_request_too_large_json(admin):
    r = admin.post("/api/contents", data=b"x" * (17 * 1024 * 1024), content_type="application/json")
    assert r.status_code == 413 and r.get_json()["error"]["code"] == "too_large"


# ------------------------------------------------------------------ Monitor


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>K</title>
<item><title>Meldung 1</title></item><item><title>Meldung &amp; 2</title></item></channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>
<entry><title>Atom-Eintrag</title></entry></feed>"""


def test_parse_feeds():
    assert monitor.parse_feed(RSS) == ["Meldung 1", "Meldung & 2"]
    assert monitor.parse_feed(ATOM) == ["Atom-Eintrag"]
    with pytest.raises(ValueError):
        monitor.parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')


def test_feed_refresh_goes_into_manifest(admin, app):
    design_id = admin.get("/api/designs").get_json()["items"][0]["id"]
    admin.patch(f"/api/designs/{design_id}", json={"config": {"footer": {"ticker_rss_url": "https://x.test/rss"}}})
    p = make_presentation(admin, "P", [make_text(admin)["id"]], publish=True)
    s = make_stele(admin, default_presentation_id=p["id"])
    conn = dbm.connect(app.config["DB_PATH"])
    assert monitor.refresh_feeds(conn, fetch=lambda url: ["Neu 1", "Neu 2"]) == 1
    player = Client(app)
    player.c.environ_base["HTTP_X_STELE_KEY"] = stele_key(s)
    m1 = player.get("/api/player/manifest").get_json()
    assert m1["feeds"]["https://x.test/rss"]["items"] == ["Neu 1", "Neu 2"]
    monitor.refresh_feeds(conn, fetch=lambda url: ["Anders"])
    assert player.get("/api/player/manifest").get_json()["version"] != m1["version"]

    def boom(url):
        raise OSError("weg")
    monitor.refresh_feeds(conn, fetch=boom)   # Fehler: letzte Meldungen bleiben
    assert player.get("/api/player/manifest").get_json()["feeds"]["https://x.test/rss"]["items"] == ["Anders"]
    conn.close()


def test_cleanup_and_ping(admin, app):
    s = make_stele(admin, ip_address="127.0.0.1")
    conn = dbm.connect(app.config["DB_PATH"])
    old = timeutil.iso(timeutil.utcnow() - timedelta(days=90))
    conn.execute("INSERT INTO stele_events(stele_id, ts, level, kind, message) VALUES (?, ?, 'info', 'x', 'alt')",
                 (s["id"], old))
    conn.execute("INSERT INTO pairing_requests(code, created_at, expires_at) VALUES ('111111', ?, ?)", (old, old))
    counts = monitor.cleanup(conn, app.config)
    assert counts["stele_events"] == 1 and counts["pairing_requests"] == 1
    monitor.ping_all(conn)
    row = dbm.row(conn, "SELECT last_ping_at, last_ping_ok FROM steles WHERE id = ?", (s["id"],))
    assert row["last_ping_at"] and row["last_ping_ok"] in (0, 1)
    conn.close()
    net = admin.get(f"/api/steles/{s['id']}").get_json()["network"]
    assert net["checked_at"] and net["ip_address"] == "127.0.0.1"


@pytest.mark.parametrize("out, ok, ms", [
    (b"Antwort von 10.0.0.5: Bytes=32 Zeit=3ms TTL=64", True, 3.0),
    (b"Reply from 10.0.0.5: bytes=32 time<1ms TTL=64", True, 1.0),
    (b"Antwort von 10.0.0.1: Zielhost nicht erreichbar.", False, None),   # Windows: Exitcode 0 ohne TTL
])
def test_ping_windows(monkeypatch, out, ok, ms):
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=b"")

    monkeypatch.setattr(monitor, "_WINDOWS", True)
    monkeypatch.setattr(monitor.shutil, "which", lambda _n: "ping")
    monkeypatch.setattr(monitor.subprocess, "run", fake_run)
    assert monitor.ping("10.0.0.5") == (ok, ms)
    assert calls[0] == ["ping", "-n", "1", "-w", "1000", "10.0.0.5"]


# ------------------------------------------------------------------ Seed


def test_demo_seed(tmp_path):
    app = create_app({"DATA_DIR": tmp_path / "data", "BACKGROUND": False, "SEED_DEMO": True,
                      "PASSWORD_HASH_METHOD": FAST_HASH})
    admin = Client(app).login("admin", "admin123")
    contents = admin.get("/api/contents").get_json()["items"]
    types = sorted(c["type"] for c in contents)
    assert types.count("image") == 3 and types.count("text") == 3 and types.count("web") == 1
    assert all(c["status"] == "ready" for c in contents)
    pres = {p["name"]: p for p in admin.get("/api/presentations").get_json()["items"]}
    assert pres["Foyer – Standard"]["status"] == "published" and pres["Abendprogramm"]["status"] == "published"
    assert pres["Sommerfest"]["status"] == "draft" and pres["Sommerfest"]["review_state"] == "requested"
    assert pres["Sommerfest"]["review_by"]["display_name"] == "Arne Autor"
    stele = admin.get("/api/steles").get_json()["items"][0]
    assert stele["name"] == "Stele Foyer" and stele["default_presentation"]["name"] == "Foyer – Standard"
    entries = admin.get(f"/api/schedule?stele_id={stele['id']}").get_json()["items"]
    assert entries[0]["days"] == [1, 2, 3, 4, 5] and entries[0]["start_time"] == "18:00"
    menu = admin.get("/api/touch-menus").get_json()["items"][0]
    labels = [t["label"] for t in menu["config"]["tiles"]]
    assert labels == ["Über uns", "Öffnungszeiten", "Galerie", "Veranstaltungen"]
    for user, pw in (("redaktion", "redaktion123"), ("autor", "autor123"), ("betrachter", "betrachter123")):
        Client(app).login(user, pw)


def test_metrics_downsample_covers_whole_range():
    from stelecms.api.monitoring import _downsample, METRICS_MAX
    rows = [{"ts": f"t{i:05d}", "cpu": float(i % 10), "ram": 50.0, "disk": None, "temp": 40.0} for i in range(1000)]
    out = _downsample(rows)
    assert len(out) <= METRICS_MAX
    assert out[0]["ts"] == "t00000" and out[-1]["ts"] >= "t00996"   # erster bis letzter Abschnitt
    assert out[0]["disk"] is None and out[0]["ram"] == 50.0
    assert _downsample([]) == []


def test_csv_safe_neutralizes_formula_after_separator():
    from stelecms.api.audit import _csv_safe
    assert _csv_safe("x,=1+1") == "x,'=1+1"
    assert _csv_safe("a; @SUM(A1)") == "a; '@SUM(A1)"
    assert _csv_safe("  =1") == "'  =1"
    assert _csv_safe("Müller-Lüdenscheidt") == "Müller-Lüdenscheidt"
    assert _csv_safe(5) == 5
