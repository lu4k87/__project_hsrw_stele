"""Präsentationen: Folien, Veröffentlichen, Freigabe, Verwerfen, Status-Hash, Vorschau (§7.5, §7.5a, §8)."""
from __future__ import annotations

from stelecms import db as dbm

from conftest import make_image, make_presentation, make_stele, make_text


def test_create_uses_default_design_and_settings(admin):
    p = admin.post("/api/presentations", json={"name": "Neu"}).get_json()
    assert p["status"] == "draft" and p["review_state"] == "none"
    assert p["design"]["name"] == "Standard" and p["design_id"] == p["design"]["id"]
    assert p["settings"]["transition"] == "fade" and p["settings"]["default_duration_s"] == 7
    assert p["items"] == [] and p["published_at"] is None


def test_create_validation(admin):
    r = admin.post("/api/presentations", json={"name": "", "design_id": 999})
    assert r.status_code == 422
    assert set(r.get_json()["error"]["fields"]) == {"name", "design_id"}


def test_items_roundtrip_and_durations(admin):
    img = make_image(admin)
    txt = make_text(admin)
    p = make_presentation(admin, "P")
    r = admin.put(f"/api/presentations/{p['id']}/items", json={"items": [
        {"content_id": img["id"], "enabled": True, "duration_s": 7, "transition": "zoom", "caption": "Hallo",
         "options": {"fit": "contain", "fullscreen": True}},
        {"content_id": txt["id"], "enabled": False, "valid_from": "2020-01-01T00:00",
         "valid_until": "2020-02-01T00:00"},
        {"content_id": txt["id"], "enabled": True, "valid_from": "2099-01-01T00:00"}]})
    assert r.status_code == 200
    body = r.get_json()
    assert [i["position"] for i in body["items"]] == [0, 1, 2]
    first = body["items"][0]
    assert first["content"]["type"] == "image" and first["effective_duration_s"] == 7
    assert first["options"] == {"fit": "contain", "fullscreen": True}
    assert [i["validity"] for i in body["items"]] == ["active", "expired", "scheduled"]
    assert body["item_count"] == 3 and body["active_item_count"] == 2 and body["total_duration_s"] == 14
    assert body["thumb_url"] == img["urls"]["thumb"]
    # IDs bleiben beim erneuten Speichern erhalten (Reihenfolge = Array)
    ids = [i["id"] for i in body["items"]]
    items = [dict(i, content_id=i["content"]["id"]) for i in reversed(body["items"])]
    body2 = admin.put(f"/api/presentations/{p['id']}/items", json={"items": items}).get_json()
    assert [i["id"] for i in body2["items"]] == list(reversed(ids))


def test_items_validation(admin):
    p = make_presentation(admin, "P")
    r = admin.put(f"/api/presentations/{p['id']}/items", json={"items": [
        {"content_id": 999}, {"content_id": None, "transition": "wirbel", "valid_from": "morgen",
                              "options": {"pages": "a-b"}}]})
    assert r.status_code == 422
    fields = r.get_json()["error"]["fields"]
    assert {"items.0.content_id", "items.1.content_id", "items.1.transition", "items.1.valid_from",
            "items.1.options.pages"} <= set(fields)


def test_publish_status_changed_discard(admin):
    img = make_image(admin)
    txt = make_text(admin, "Info")
    p = make_presentation(admin, "Foyer", [img["id"], txt["id"]])
    r = admin.post(f"/api/presentations/{p['id']}/publish", json={"note": "Los"})
    assert r.status_code == 200
    pub = r.get_json()
    assert pub["status"] == "published" and pub["published_by"]["display_name"] == "Alex Admin"
    # Umbenennen allein ändert den Status nicht
    assert admin.patch(f"/api/presentations/{p['id']}", json={"name": "Foyer neu"}).get_json()["status"] == "published"
    # Info-Folie ändern → offene Änderungen
    admin.patch(f"/api/contents/{txt['id']}", json={"data": {"fields": {"title": "Anders"}}})
    assert admin.get(f"/api/presentations/{p['id']}").get_json()["status"] == "changed"
    admin.patch(f"/api/contents/{txt['id']}", json={"data": {"fields": {"title": "Info"}}})
    assert admin.get(f"/api/presentations/{p['id']}").get_json()["status"] == "published"
    # Folien/Einstellungen ändern und verwerfen
    admin.put(f"/api/presentations/{p['id']}/items", json={"items": [{"content_id": img["id"]}]})
    admin.patch(f"/api/presentations/{p['id']}", json={"settings": {"transition": "none"}})
    assert admin.get(f"/api/presentations/{p['id']}").get_json()["status"] == "changed"
    r = admin.post(f"/api/presentations/{p['id']}/discard")
    assert r.status_code == 200
    back = r.get_json()
    assert back["status"] == "published" and len(back["items"]) == 2 and back["settings"]["transition"] == "fade"
    assert [i["id"] for i in back["items"]] == [i["id"] for i in pub["items"]]


def test_design_change_marks_changed(admin):
    p = make_presentation(admin, "P", [make_text(admin)["id"]], publish=True)
    admin.patch(f"/api/designs/{p['design_id']}", json={"config": {"header": {"title": "Neu"}}})
    assert admin.get(f"/api/presentations/{p['id']}").get_json()["status"] == "changed"


def test_discard_never_published_409(admin):
    p = make_presentation(admin, "P")
    assert admin.post(f"/api/presentations/{p['id']}/discard").status_code == 409


def test_publish_requires_active_ready_items(admin, app):
    p = make_presentation(admin, "Leer")
    r = admin.post(f"/api/presentations/{p['id']}/publish", json={})
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "validation_error"
    img = make_image(admin)
    conn = dbm.connect(app.config["DB_PATH"])
    conn.execute("UPDATE contents SET status = 'processing' WHERE id = ?", (img["id"],))
    conn.close()
    admin.put(f"/api/presentations/{p['id']}/items", json={"items": [{"content_id": img["id"]}]})
    r = admin.post(f"/api/presentations/{p['id']}/publish", json={})
    assert r.status_code == 422
    assert r.get_json()["error"]["details"]["items"][0]["status"] == "processing"


def test_review_workflow(admin, autor, redaktion):
    p = make_presentation(autor, "Sommerfest", [make_text(autor)["id"]])
    autor.post(f"/api/presentations/{p['id']}/request-review", json={"note": "Bitte prüfen"})
    dash = redaktion.get("/api/dashboard").get_json()
    assert dash["reviews"][0]["presentation"]["id"] == p["id"]
    assert dash["reviews"][0]["requested_by"]["display_name"] == "Arne Autor"
    r = redaktion.post(f"/api/presentations/{p['id']}/reject", json={})
    assert r.status_code == 422 and "note" in r.get_json()["error"]["fields"]
    r = redaktion.post(f"/api/presentations/{p['id']}/reject", json={"note": "Bild fehlt"})
    assert r.get_json()["review_state"] == "rejected" and r.get_json()["review_note"] == "Bild fehlt"
    assert redaktion.post(f"/api/presentations/{p['id']}/reject", json={"note": "x"}).status_code == 409
    autor.post(f"/api/presentations/{p['id']}/request-review", json={})
    r = redaktion.post(f"/api/presentations/{p['id']}/publish", json={})
    assert r.get_json()["review_state"] == "none" and r.get_json()["status"] == "published"
    actions = [e["action"] for e in admin.get("/api/audit?entity_type=presentation").get_json()["items"]]
    assert {"publish", "reject", "request_review", "create"} <= set(actions)


def test_delete_in_use_and_ok(admin):
    p = make_presentation(admin, "Standard", [make_text(admin)["id"]], publish=True)
    s = make_stele(admin, default_presentation_id=p["id"])
    r = admin.delete(f"/api/presentations/{p['id']}")
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "in_use"
    assert r.get_json()["error"]["details"]["usages"][0] == {"type": "stele", "id": s["id"], "name": s["name"]}
    listed = admin.get("/api/presentations").get_json()["items"][0]
    assert listed["used_by"] == [{"stele_id": s["id"], "stele_name": s["name"], "how": "default"}]
    other = make_presentation(admin, "Weg")
    assert admin.delete(f"/api/presentations/{other['id']}").get_json() == {"ok": True}


def test_copy_from(admin):
    src = make_presentation(admin, "Vorlage", [make_text(admin)["id"]])
    r = admin.post("/api/presentations", json={"copy_from": src["id"]})
    assert r.status_code == 201
    copy = r.get_json()
    assert copy["name"] == "Vorlage (Kopie)" and len(copy["items"]) == 1 and copy["status"] == "draft"
    assert copy["items"][0]["id"] != src["items"][0]["id"]


def test_presentation_manifest_preview_etag(admin):
    img = make_image(admin)
    p = make_presentation(admin, "P", [img["id"]])
    r = admin.get(f"/api/presentations/{p['id']}/manifest?source=published")
    assert r.status_code == 404
    r = admin.get(f"/api/presentations/{p['id']}/manifest")
    m = r.get_json()
    assert r.status_code == 200 and m["preview"] is True and m["stele"] is None and m["schema"] == 1
    assert len(m["version"]) == 16 and r.headers["ETag"] == f'"{m["version"]}"'
    slide = m["presentations"][str(p["id"])]["slides"][0]
    assert slide["type"] == "image" and slide["src"] == img["urls"]["display"] and slide["fit"] == "cover"
    assert img["urls"]["display"] in m["assets"]
    r = admin.get(f"/api/presentations/{p['id']}/manifest", headers={"If-None-Match": f'"{m["version"]}"'})
    assert r.status_code == 304


def test_preview_resolve_unsaved(admin):
    txt = make_text(admin, "Gespeichert")
    img = make_image(admin)
    r = admin.post("/api/preview/resolve", json={
        "settings": {"transition": "zoom", "default_duration_s": 5},
        "design": {"header": {"title": "Ungespeichert", "logo_content_id": img["id"]}},
        "touch_menu": {"title": "Menü", "tiles": [{"label": "Bild", "icon": "image",
                                                   "action": {"type": "gallery", "content_ids": [img["id"]]}}]},
        "items": [
            {"id": 11, "content_id": txt["id"], "enabled": True,
             "content": {"type": "text", "data": {"fields": {"title": "Live-Titel"}}}},
            {"id": None, "content_id": img["id"], "enabled": False, "duration_s": 3},
            {"content_id": 9999, "enabled": True},
        ]}, headers={"X-Background-Poll": "1"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["settings"]["transition"] == "zoom" and body["settings"]["image_fit"] == "cover"
    assert body["design"]["header"]["title"] == "Ungespeichert" and body["design"]["logo_url"]
    tile = body["touch_menu"]["tiles"][0]
    assert tile["action"]["type"] == "gallery" and tile["action"]["items"][0]["type"] == "image"
    s0, s1, s2 = body["slides"]
    assert s0["fields"]["title"] == "Live-Titel" and s0["id"] == 11 and s0["enabled"] is True
    assert s0["duration_s"] == 5 and s0["transition"] == "zoom"
    assert s1["enabled"] is False and s1["duration_s"] == 3
    assert s2["type"] == "missing"
    # Nichts wurde gespeichert
    assert admin.get(f"/api/contents/{txt['id']}").get_json()["data"]["fields"]["title"] == "Gespeichert"


def test_preview_requires_view_permission(anon):
    assert anon.post("/api/preview/resolve", json={}).status_code == 401


def test_numbers_must_be_finite(admin):
    """NaN/Infinity lässt der JSON-Parser durch – die Bereichsprüfung darf sie nicht annehmen."""
    from stelecms.validation import is_number
    assert [is_number(x) for x in (1, 1.5, float("nan"), float("inf"), True, "1")] == \
        [True, True, False, False, False, False]
    p = make_presentation(admin, "P")
    for raw in ("NaN", "Infinity", "-Infinity"):
        r = admin.patch(f"/api/presentations/{p['id']}", data='{"settings": {"default_duration_s": %s}}' % raw,
                        content_type="application/json")
        assert r.status_code == 422, raw
        assert "settings.default_duration_s" in r.get_json()["error"]["fields"]
