"""Stelen, Kopplung, Player-API (Manifest, Heartbeat), Befehle, Agent (§7.7, §8, §9.8, §10)."""
from __future__ import annotations

import io
from datetime import timedelta

from PIL import Image

from stelecms import db as dbm
from stelecms import timeutil

from conftest import Client, make_image, make_presentation, make_stele, make_text, stele_key


def _player(app, key=None):
    c = Client(app)
    if key:
        c.c.environ_base["HTTP_X_STELE_KEY"] = key
    return c


def test_create_stele_and_player_url_visibility(admin, redaktion):
    s = make_stele(admin, "Foyer", location="Halle", ip_address="192.168.1.50")
    assert s["player_url"].startswith("http://localhost/player/?key=")
    assert s["status"] == "never" and s["paired"] is False and s["orientation"] == "portrait"
    assert s["settings"]["volume"] == 0.8 and s["settings"]["night_mode"]["start"] == "22:00"
    assert s["now"] == {"presentation": None, "source": "none", "schedule_entry_id": None, "item": None,
                        "mode": None}
    assert "player_url" not in redaktion.get(f"/api/steles/{s['id']}").get_json()
    assert "player_url" in admin.get(f"/api/steles/{s['id']}").get_json()


def test_stele_validation(admin):
    r = admin.post("/api/steles", json={"name": "", "ip_address": "nicht gültig!", "width": 10,
                                        "settings": {"volume": 2, "night_mode": {"start": "25:00"}}})
    assert r.status_code == 422
    assert {"name", "ip_address", "width", "settings.volume", "settings.night_mode.start"} <= \
        set(r.get_json()["error"]["fields"])


def test_patch_default_presentation_with_schedule_edit(admin, redaktion):
    p = make_presentation(admin, "P", [make_text(admin)["id"]], publish=True)
    s = make_stele(admin)
    r = redaktion.patch(f"/api/steles/{s['id']}", json={"default_presentation_id": p["id"]})
    assert r.status_code == 200 and r.get_json()["default_presentation"]["id"] == p["id"]
    assert r.get_json()["now"]["source"] == "default"
    assert redaktion.patch(f"/api/steles/{s['id']}", json={"name": "Neu"}).status_code == 403
    assert admin.patch(f"/api/steles/{s['id']}", json={"name": "Neu"}).get_json()["name"] == "Neu"


def test_pairing_flow(admin, app):
    player = _player(app)
    r = player.post("/api/player/pairing", json={"device_info": {"user_agent": "Chrome", "screen": {"w": 1080,
                                                                                                    "h": 1920}}})
    assert r.status_code == 201
    code = r.get_json()["code"]
    assert len(code) == 6 and r.get_json()["poll_interval_s"] == 3
    assert player.get(f"/api/player/pairing/{code}").get_json() == {"status": "waiting"}
    pending = admin.get("/api/pairing/pending").get_json()["items"]
    assert pending[0]["code"] == code and pending[0]["device_info"]["screen"] == {"w": 1080, "h": 1920}
    s = make_stele(admin, "Gekoppelt", pairing_code=code)
    assert s["paired"] is True
    r = player.get(f"/api/player/pairing/{code}")
    body = r.get_json()
    assert body["status"] == "paired" and body["stele"] == {"id": s["id"], "name": "Gekoppelt"}
    assert "stele_key=" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    # Cookie genügt für das Manifest
    assert player.get("/api/player/manifest").status_code == 200
    assert admin.post(f"/api/steles/{s['id']}/pair", json={"code": code}).status_code == 409


def test_pairing_unknown_and_expired(admin, app):
    s = make_stele(admin)
    r = admin.post(f"/api/steles/{s['id']}/pair", json={"code": "123456"})
    assert r.status_code == 404 and r.get_json()["error"]["message"].startswith("Code unbekannt")
    code = _player(app).post("/api/player/pairing", json={}).get_json()["code"]
    conn = dbm.connect(app.config["DB_PATH"])
    conn.execute("UPDATE pairing_requests SET expires_at = ? WHERE code = ?", ("2000-01-01T00:00:00Z", code))
    conn.close()
    r = admin.post(f"/api/steles/{s['id']}/pair", json={"code": code})
    assert r.status_code == 410 and r.get_json()["error"]["code"] == "expired"
    assert _player(app).get(f"/api/player/pairing/{code}").get_json() == {"status": "expired"}
    assert admin.post(f"/api/steles/{s['id']}/pair", json={"code": "12"}).status_code == 422


def test_pairing_rate_limit(app):
    p = _player(app)
    codes = [p.post("/api/player/pairing", json={}).status_code for _ in range(11)]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def test_player_requires_key(app):
    assert _player(app).get("/api/player/manifest").status_code == 401
    assert _player(app, "falsch").post("/api/player/heartbeat", json={}).status_code == 401


def test_manifest_etag_and_version_changes(admin, app):
    img = make_image(admin)
    p = make_presentation(admin, "Foyer", [img["id"]], publish=True)
    s = make_stele(admin, default_presentation_id=p["id"])
    player = _player(app, stele_key(s))
    r = player.get("/api/player/manifest")
    m = r.get_json()
    assert r.status_code == 200 and m["preview"] is False and m["default_presentation_id"] == p["id"]
    assert m["stele"]["name"] == s["name"] and m["stele"]["settings"]["daily_reload"] == "03:30"
    assert list(m["presentations"]) == [str(p["id"])] and m["timezone"] == "Europe/Berlin"
    pres_ = m["presentations"][str(p["id"])]
    assert pres_["published_at"] and pres_["design"]["header"]["title"] == "Willkommen"
    assert pres_["slides"][0]["id"] == p["items"][0]["id"]
    assert r.headers["ETag"] == f'"{m["version"]}"'
    assert player.get("/api/player/manifest", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304
    # Entwurf ändern ändert das Manifest nicht; Veröffentlichen schon
    admin.put(f"/api/presentations/{p['id']}/items", json={"items": []})
    assert player.get("/api/player/manifest").get_json()["version"] == m["version"]
    two = [{"content_id": img["id"]}, {"content_id": img["id"]}]
    admin.put(f"/api/presentations/{p['id']}/items", json={"items": two})
    admin.post(f"/api/presentations/{p['id']}/publish", json={})
    assert player.get("/api/player/manifest").get_json()["version"] != m["version"]
    # Admin-Live-Ansicht liefert dieselbe Form
    live = admin.get(f"/api/steles/{s['id']}/manifest").get_json()
    assert live["version"] == player.get("/api/player/manifest").get_json()["version"]


def test_heartbeat_stores_state_and_logs(admin, app):
    p = make_presentation(admin, "P", [make_text(admin)["id"]], publish=True)
    s = make_stele(admin, default_presentation_id=p["id"])
    player = _player(app, stele_key(s))
    now = timeutil.now_iso()
    r = player.post("/api/player/heartbeat", json={
        "player_version": "1.0.0", "manifest_version": "alt", "mode": "slideshow",
        "current": {"presentation_id": p["id"], "item_id": p["items"][0]["id"], "content_id":
                    p["items"][0]["content"]["id"], "title": "Info", "started_at": now},
        "screen": {"w": 1080, "h": 1920}, "user_agent": "Chrome", "uptime_s": 12,
        "errors": [{"ts": now, "message": "Video kaputt", "item_id": 5}],
        "played": [{"started_at": now, "duration_s": 10, "presentation_id": p["id"], "item_id": 1,
                    "content_id": 1, "title": "Info"}],
        "touch": [{"ts": now, "event": "session_start", "session_id": "a"},
                  {"ts": now, "event": "tile_open", "session_id": "a", "tile_id": "t-1", "label": "Galerie"},
                  {"ts": now, "event": "session_end", "session_id": "a", "duration_s": 42},
                  {"ts": now, "event": "unsinn"}]})
    assert r.status_code == 200
    hb = r.get_json()
    assert hb["commands"] == [] and hb["server_time"] and len(hb["manifest_version"]) == 16
    st = admin.get(f"/api/steles/{s['id']}").get_json()
    assert st["status"] == "online" and st["now"]["mode"] == "slideshow"
    assert st["now"]["item"]["title"] == "Info" and st["now"]["item"]["content_type"] == "text"
    assert st["player"]["manifest_current"] is False and st["player"]["screen"] == {"w": 1080, "h": 1920}
    mon = admin.get(f"/api/monitoring/steles/{s['id']}").get_json()
    assert mon["playback"][0]["title"] == "Info"
    assert mon["touch"]["sessions"] == 1 and mon["touch"]["top_tiles"] == [{"label": "Galerie", "count": 1}]
    assert mon["touch"]["avg_duration_s"] == 42
    kinds = {e["kind"] for e in mon["events"]}
    assert {"online", "player_error"} <= kinds
    ov = admin.get("/api/monitoring/overview").get_json()["steles"][0]
    assert ov["plays_today"] == 1 and ov["touch_sessions_today"] == 1 and ov["errors_24h"] == 1


def test_heartbeat_idle_mode_is_not_night_mode(admin, app):
    """Player ohne Anzeige meldet „idle“ (eigener Modus), nicht „standby“ (Nachtmodus)."""
    s = make_stele(admin)
    player = _player(app, stele_key(s))
    assert player.post("/api/player/heartbeat", json={"mode": "idle"}).status_code == 200
    st = admin.get(f"/api/steles/{s['id']}").get_json()
    assert st["status"] == "online" and st["now"]["mode"] == "idle"
    kinds = {e["kind"] for e in admin.get(f"/api/monitoring/steles/{s['id']}").get_json()["events"]}
    assert "standby" not in kinds
    player.post("/api/player/heartbeat", json={"mode": "quatsch"})
    assert admin.get(f"/api/steles/{s['id']}").get_json()["now"]["mode"] is None


def test_pairing_shortly_before_expiry_keeps_code_for_pickup(admin, app):
    """Kopplung kurz vor Ablauf: Code bleibt so lange gültig, dass der Player den Schlüssel abholen kann."""
    code = _player(app).post("/api/player/pairing", json={}).get_json()["code"]
    conn = dbm.connect(app.config["DB_PATH"])
    conn.execute("UPDATE pairing_requests SET expires_at = ? WHERE code = ?",
                 (timeutil.iso(timeutil.utcnow() + timedelta(seconds=2)), code))
    s = make_stele(admin, "Knapp", pairing_code=code)
    expires = timeutil.parse_iso(dbm.scalar(conn, "SELECT expires_at FROM pairing_requests WHERE code = ?", (code,)))
    conn.close()
    assert expires - timeutil.utcnow() >= timedelta(seconds=110)
    body = _player(app).get(f"/api/player/pairing/{code}").get_json()
    assert body["status"] == "paired" and body["stele"]["id"] == s["id"]


def test_online_segments_and_offline_event(admin, app, monkeypatch):
    from stelecms import monitor
    s = make_stele(admin)
    player = _player(app, stele_key(s))
    t0 = timeutil.utcnow()
    monkeypatch.setattr(timeutil, "utcnow", lambda: t0)
    player.post("/api/player/heartbeat", json={"mode": "slideshow"})
    monkeypatch.setattr(timeutil, "utcnow", lambda: t0 + timedelta(seconds=15))
    player.post("/api/player/heartbeat", json={"mode": "slideshow"})
    conn = dbm.connect(app.config["DB_PATH"])
    assert dbm.scalar(conn, "SELECT COUNT(*) FROM stele_online_segments") == 1
    # 2 Minuten Stille → offline (Monitor meldet genau einmal)
    monkeypatch.setattr(timeutil, "utcnow", lambda: t0 + timedelta(seconds=135))
    assert admin.get(f"/api/steles/{s['id']}").get_json()["status"] == "offline"
    assert monitor.check_offline(conn) == 1 and monitor.check_offline(conn) == 0
    player.post("/api/player/heartbeat", json={"mode": "slideshow"})
    assert dbm.scalar(conn, "SELECT COUNT(*) FROM stele_online_segments") == 2
    kinds = [r["kind"] for r in dbm.rows(conn, "SELECT kind FROM stele_events ORDER BY id")]
    assert kinds == ["online", "offline", "online"]
    conn.close()
    avail = admin.get(f"/api/monitoring/steles/{s['id']}").get_json()["availability"]
    assert len(avail["segments"]) == 2 and 0 < avail["pct"] <= 100


def test_commands_roundtrip(admin, redaktion, betrachter, app):
    s = make_stele(admin)
    player = _player(app, stele_key(s))
    assert betrachter.post(f"/api/steles/{s['id']}/commands", json={"command": "reload"}).status_code == 403
    assert redaktion.post(f"/api/steles/{s['id']}/commands", json={"command": "tanzen"}).status_code == 422
    r = redaktion.post(f"/api/steles/{s['id']}/commands", json={"command": "reload"})
    assert r.status_code == 201
    cid = r.get_json()["id"]
    redaktion.post(f"/api/steles/{s['id']}/commands", json={"command": "screenshot"})
    hb = player.post("/api/player/heartbeat", json={}).get_json()
    assert [c["command"] for c in hb["commands"]] == ["reload"] and hb["commands"][0]["id"] == cid
    assert player.post("/api/player/heartbeat", json={}).get_json()["commands"] == []
    player.post("/api/player/heartbeat", json={"command_results": [{"id": cid, "ok": True, "message": "neu geladen"}]})
    items = admin.get(f"/api/steles/{s['id']}/commands").get_json()["items"]
    reload_cmd = next(i for i in items if i["id"] == cid)
    assert reload_cmd["delivered_at"] and reload_cmd["done_at"] and "neu geladen" in reload_cmd["result"]
    assert reload_cmd["created_by"]["display_name"] == "Rita Redaktion"


def test_rotate_key_invalidates_old(admin, app):
    s = make_stele(admin)
    old = stele_key(s)
    new = admin.post(f"/api/steles/{s['id']}/rotate-key").get_json()
    assert stele_key(new) != old
    assert _player(app, old).get("/api/player/manifest").status_code == 401
    assert _player(app, stele_key(new)).get("/api/player/manifest").status_code == 200


def test_agent_report_and_screenshot(admin, app):
    s = make_stele(admin)
    agent = _player(app, stele_key(s))
    admin.post(f"/api/steles/{s['id']}/commands", json={"command": "screenshot"})
    r = agent.post("/api/agent/report", json={"hostname": "stele-pc", "os": "Linux", "uptime_s": 100, "cpu": 12.5,
                                              "ram": 40, "disk": 55, "temp": 48.2, "ips": ["10.0.0.5"],
                                              "screen": {"w": 1080, "h": 1920}})
    assert r.status_code == 200
    assert [c["command"] for c in r.get_json()["commands"]] == ["screenshot"]
    buf = io.BytesIO()
    Image.new("RGB", (540, 960), (10, 20, 30)).save(buf, "JPEG")
    r = agent.post("/api/agent/screenshot", data={"image": (io.BytesIO(buf.getvalue()), "s.jpg")},
                   content_type="multipart/form-data")
    assert r.status_code == 200
    st = admin.get(f"/api/steles/{s['id']}").get_json()
    assert st["agent"]["hostname"] == "stele-pc" and st["agent"]["cpu"] == 12.5 and st["agent"]["screenshot_at"]
    shot = admin.get(f"/api/steles/{s['id']}/screenshot")
    assert shot.status_code == 200 and shot.mimetype == "image/jpeg"
    mon = admin.get(f"/api/monitoring/steles/{s['id']}").get_json()
    assert mon["metrics"][0]["temp"] == 48.2 and mon["screenshot"]["url"].startswith(f"/api/steles/{s['id']}/")
    cmd = admin.get(f"/api/steles/{s['id']}/commands").get_json()["items"][0]
    assert cmd["done_at"] and cmd["result"].startswith("OK")
    r = agent.post("/api/agent/screenshot", data={"image": (io.BytesIO(b"kein bild"), "s.jpg")},
                   content_type="multipart/form-data")
    assert r.status_code == 415


def test_delete_stele(admin, app):
    s = make_stele(admin)
    assert admin.delete(f"/api/steles/{s['id']}").get_json() == {"ok": True}
    assert admin.get(f"/api/steles/{s['id']}").status_code == 404
    assert _player(app, stele_key(s)).get("/api/player/manifest").status_code == 401
