"""Cache für Manifest-Version, Entwurfs-Hash und Verwendungen: Treffer und Verwerfen nach jeder Änderung."""
from __future__ import annotations

from stelecms import cache, jobs, monitor
from stelecms import db as dbm
from stelecms.resolve import Resolver

from conftest import Client, make_presentation, make_stele, make_text, stele_key


def _player(app, stele):
    c = Client(app)
    c.c.environ_base["HTTP_X_STELE_KEY"] = stele_key(stele)
    return c


def _version(player) -> str:
    return player.post("/api/player/heartbeat", json={}).get_json()["manifest_version"]


def _manifest_version(player) -> str:
    return player.get("/api/player/manifest").get_json()["version"]


def _setup(admin, app):
    p = make_presentation(admin, "P", [make_text(admin)["id"]], publish=True)
    s = make_stele(admin, default_presentation_id=p["id"])
    return p, s, _player(app, s)


def _count_manifests(monkeypatch) -> list:
    calls = []
    orig = Resolver.stele_manifest

    def counting(self, stele):
        calls.append(stele["id"])
        return orig(self, stele)
    monkeypatch.setattr(Resolver, "stele_manifest", counting)
    return calls


def test_heartbeat_reuses_cached_manifest_version(admin, app, monkeypatch):
    _p, _s, player = _setup(admin, app)
    calls = _count_manifests(monkeypatch)
    v = _version(player)
    assert _version(player) == v and _version(player) == v
    assert len(calls) == 1
    assert v == _manifest_version(player)


def test_manifest_version_follows_every_admin_change(admin, app):
    p, s, player = _setup(admin, app)
    seen = [_version(player)]

    def changed():
        v = _version(player)
        assert v not in seen and v == _manifest_version(player)
        seen.append(v)

    # Veröffentlichen (neuer Inhalt im veröffentlichten Stand)
    admin.put(f"/api/presentations/{p['id']}/items",
              json={"items": [{"content_id": make_text(admin, "Zwei")["id"], "enabled": True}]})
    assert _version(player) == seen[-1]   # nur Entwurf geändert → Stele zeigt dasselbe
    assert admin.post(f"/api/presentations/{p['id']}/publish", json={}).status_code == 200
    changed()
    # Präsentation umbenannt (Name steht im Manifest)
    assert admin.patch(f"/api/presentations/{p['id']}", json={"name": "Neu"}).status_code == 200
    changed()
    # Stele bearbeitet
    assert admin.patch(f"/api/steles/{s['id']}", json={"name": "Foyer"}).status_code == 200
    changed()
    # Zeitplan-Eintrag
    other = make_presentation(admin, "Abend", [make_text(admin, "Abend")["id"]], publish=True)
    r = admin.post("/api/schedule", json={"stele_id": s["id"], "presentation_id": other["id"], "days": [1],
                                          "start_time": "18:00", "end_time": "22:00"})
    assert r.status_code == 201, r.get_json()
    changed()
    # Einstellungen
    assert admin.patch("/api/settings", json={"org_name": "Hochschule"}).status_code == 200
    changed()


def test_feed_refresh_drops_cached_version(admin, app):
    design_id = admin.get("/api/designs").get_json()["items"][0]["id"]
    admin.patch(f"/api/designs/{design_id}", json={"config": {"footer": {"ticker_rss_url": "https://x.test/rss"}}})
    _p, _s, player = _setup(admin, app)
    v1 = _version(player)
    conn = dbm.connect(app.config["DB_PATH"])
    try:
        monitor.refresh_feeds(conn, fetch=lambda url: ["Neu"], db_path=app.config["DB_PATH"])
    finally:
        conn.close()
    v2 = _version(player)
    assert v2 != v1 and v2 == _manifest_version(player)


def test_finished_job_drops_cache(admin, app, monkeypatch):
    _p, _s, player = _setup(admin, app)
    calls = _count_manifests(monkeypatch)
    _version(player)
    jobs.process_pending(app.config)   # ohne wartende Jobs: nichts verworfen
    _version(player)
    assert len(calls) == 1
    conn = dbm.connect(app.config["DB_PATH"])
    try:
        jobs.enqueue(conn, "pdf", None)   # Job ohne Inhalt endet sofort mit Fehler
    finally:
        conn.close()
    jobs.process_pending(app.config)
    _version(player)
    assert len(calls) == 2


def test_draft_status_cached_until_content_changes(admin, app, monkeypatch):
    t = make_text(admin)
    p = make_presentation(admin, "P", [t["id"]], publish=True)
    resolves = []
    orig = Resolver.resolve

    def counting(self, pres, *a, **kw):
        resolves.append(pres["id"])
        return orig(self, pres, *a, **kw)
    monkeypatch.setattr(Resolver, "resolve", counting)

    def status():
        return {u["id"]: u["status"] for u in admin.get("/api/dashboard").get_json()["unpublished"]}
    assert status() == {} and status() == {}
    assert resolves.count(p["id"]) == 1
    r = admin.patch(f"/api/contents/{t['id']}", json={"data": {"template": "title_text",
                                                              "fields": {"title": "Anders"}}})
    assert r.status_code == 200, r.get_json()
    assert status() == {p["id"]: "changed"}


def test_write_request_never_reads_stale_cache(admin, app):
    p, s, player = _setup(admin, app)
    player.post("/api/player/heartbeat", json={"player_version": "1", "manifest_version": _version(player)})
    assert admin.get(f"/api/steles/{s['id']}").get_json()["player"]["manifest_current"] is True
    # Die Antwort des PATCH wird nach der eigenen Änderung berechnet, nicht aus dem Cache von vorher
    r = admin.patch(f"/api/steles/{s['id']}", json={"name": "Umbenannt"})
    assert r.get_json()["player"]["manifest_current"] is False


def test_usage_index_cached_for_lists_fresh_for_delete(admin, app):
    t = make_text(admin)
    make_presentation(admin, "P", [make_text(admin, "Zwei")["id"]])

    def count():
        return {c["id"]: c["usage_count"] for c in admin.get("/api/contents").get_json()["items"]}[t["id"]]
    assert count() == 0
    # Änderung ohne Admin-Anfrage (kein Bump): die Liste bleibt beim alten Stand, das Löschen prüft frisch
    conn = dbm.connect(app.config["DB_PATH"])
    try:
        pid = admin.get("/api/presentations").get_json()["items"][0]["id"]
        dbm.insert(conn, "presentation_items", {"presentation_id": pid, "content_id": t["id"], "position": 9})
    finally:
        conn.close()
    assert count() == 0
    assert admin.delete(f"/api/contents/{t['id']}").status_code == 409
    assert count() == 1   # das Löschen hat (als Admin-Änderung) den Cache verworfen
