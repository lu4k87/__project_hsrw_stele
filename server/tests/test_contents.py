"""Mediathek: Upload (Bild/Video/PDF/falscher Typ), Info-Folien, Webseiten, Verwendung, Mediendateien (§7.4)."""
from __future__ import annotations

import io
import shutil

import pytest

from conftest import make_image, make_presentation, make_stele, make_text, pdf_bytes, png_bytes, stele_key, upload


def test_upload_image_creates_variants(admin, app):
    r = upload(admin, "Foyer Bild.png", png_bytes(1080, 1920), tags=["Foyer", "foyer", "Sommer"])
    assert r.status_code == 201
    c = r.get_json()["items"][0]
    assert c["type"] == "image" and c["status"] == "ready" and c["title"] == "Foyer Bild"
    assert c["tags"] == ["Foyer", "Sommer"]
    assert c["file"]["width"] == 1080 and c["file"]["height"] == 1920 and c["file"]["mime"] == "image/png"
    assert c["urls"]["thumb"].endswith("/thumb.webp") and c["urls"]["display"].endswith("/display.jpg")
    assert c["warnings"] == [] and c["created_by"]["display_name"] == "Alex Admin"
    media = app.config["MEDIA_DIR"] / c["uid"]
    assert (media / "thumb.webp").is_file() and (media / "display.jpg").is_file()


def test_upload_landscape_low_resolution_warnings(admin):
    c = make_image(admin, "quer.jpg", w=800, h=400, fmt="JPEG")
    codes = {w["code"] for w in c["warnings"]}
    assert codes == {"landscape", "low_resolution"}


def test_upload_video_processed(admin, app, video_bytes):
    r = upload(admin, "clip.mp4", video_bytes)
    assert r.status_code == 201
    cid = r.get_json()["items"][0]["id"]
    c = admin.get(f"/api/contents/{cid}").get_json()   # ohne Hintergrund läuft der Job direkt
    assert c["type"] == "video" and c["status"] == "ready", c
    assert c["data"]["codec"] == "h264" and c["data"]["compatible"] is True
    assert c["file"]["duration_s"] > 1.5 and c["urls"]["poster"].endswith("/poster.jpg")
    assert c["urls"]["display"].endswith(".mp4")


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="poppler (pdftoppm) nicht installiert")
def test_upload_pdf_pages(admin):
    r = upload(admin, "Flyer.pdf", pdf_bytes(3))
    assert r.status_code == 201
    c = admin.get(f"/api/contents/{r.get_json()['items'][0]['id']}").get_json()
    assert c["type"] == "pdf" and c["status"] == "ready"
    assert c["file"]["page_count"] == 3 and len(c["urls"]["pages"]) == 3
    assert c["urls"]["pages"][0].endswith("/pages/p001.png")


def test_upload_rejects_wrong_types(admin):
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
    r = upload(admin, "logo.svg", svg)
    assert r.status_code == 415
    body = r.get_json()
    assert body["error"]["code"] == "unsupported_media" and "Dateityp nicht unterstützt" in body["errors"][0]["message"]
    r = upload(admin, "bild.jpg", b"das ist gar kein Bild")  # Endung allein reicht nicht
    assert r.status_code == 415


def test_upload_mixed_partial_success(admin):
    form = {"files": [(io.BytesIO(png_bytes(200, 300)), "ok.png"), (io.BytesIO(b"nix"), "kaputt.png")]}
    r = admin.post("/api/contents/upload", data=form, content_type="multipart/form-data")
    assert r.status_code == 201
    body = r.get_json()
    assert len(body["items"]) == 1 and body["errors"][0]["file_name"] == "kaputt.png"


def test_upload_too_large(admin):
    assert admin.patch("/api/settings", json={"upload_max_mb": 1}).status_code == 200
    big = png_bytes(10, 10) + b"\0" * (1024 * 1024 + 10)
    r = upload(admin, "gross.png", big)
    assert r.status_code == 413 and r.get_json()["errors"][0]["code"] == "too_large"


def test_upload_without_files(admin):
    r = admin.post("/api/contents/upload", data={}, content_type="multipart/form-data")
    assert r.status_code == 422 and "files" in r.get_json()["error"]["fields"]


def test_text_content_create_update_duplicate(admin):
    img = make_image(admin)
    c = make_text(admin, "Willkommen", fields={"title": "Hallo", "image_content_id": img["id"]},
                  template="image_text")
    assert c["data"]["template"] == "image_text" and c["data"]["style"]["bg_color"] == "#0F2747"
    assert c["data"]["fields"]["image_content_id"] == img["id"] and c["file"] is None
    r = admin.patch(f"/api/contents/{c['id']}", json={"data": {"style": {"bg_color": "rot"}}})
    assert r.status_code == 422 and "data.style.bg_color" in r.get_json()["error"]["fields"]
    r = admin.patch(f"/api/contents/{c['id']}", json={"title": "Neu", "data": {"fields": {"items": ["a", "b"]}}})
    assert r.status_code == 200 and r.get_json()["data"]["fields"]["items"] == ["a", "b"]
    assert r.get_json()["data"]["fields"]["title"] == "Hallo"
    dup = admin.post(f"/api/contents/{c['id']}/duplicate").get_json()
    assert dup["title"] == "Neu (Kopie)" and dup["id"] != c["id"]
    # Bild wird von der Info-Folie verwendet
    assert admin.get(f"/api/contents/{img['id']}").get_json()["usages"][0]["type"] == "content"


def test_text_event_audience_admission_qr(admin):
    c = make_text(admin, "Vortrag", template="event", fields={
        "title": "Vortrag", "audience": "Alle Interessierten", "admission": "Eintritt frei",
        "qr_url": "https://example.com/anmeldung", "qr_label": "Anmeldung"})
    f = c["data"]["fields"]
    assert (f["audience"], f["admission"], f["qr_url"], f["qr_label"]) == (
        "Alle Interessierten", "Eintritt frei", "https://example.com/anmeldung", "Anmeldung")
    r = admin.patch(f"/api/contents/{c['id']}", json={"data": {"fields": {"qr_url": "keine-adresse"}}})
    assert r.status_code == 422 and "data.fields.qr_url" in r.get_json()["error"]["fields"]
    r = admin.patch(f"/api/contents/{c['id']}", json={"data": {"fields": {"qr_url": "https://x.de/" + "a" * 500}}})
    assert r.status_code == 422 and "data.fields.qr_url" in r.get_json()["error"]["fields"]
    r = admin.patch(f"/api/contents/{c['id']}", json={"data": {"fields": {"qr_url": ""}}})
    assert r.status_code == 200 and r.get_json()["data"]["fields"]["qr_url"] == ""
    # Leere neue Felder fehlen im Manifest (veröffentlichte Stände bleiben gleich), gefüllte sind enthalten
    plain = make_text(admin, "Ohne")
    p = make_presentation(admin, "M", content_ids=[plain["id"], c["id"]], publish=True)
    m = admin.get(f"/api/presentations/{p['id']}/manifest").get_json()
    slides = m["presentations"][str(p["id"])]["slides"]
    assert "qr_url" not in slides[0]["fields"] and "audience" not in slides[0]["fields"]
    assert slides[1]["fields"]["admission"] == "Eintritt frei"
    assert admin.get(f"/api/presentations/{p['id']}").get_json()["status"] == "published"


def test_text_new_fields_forbidden_for_viewer(admin, betrachter):
    c = make_text(admin, "Info")
    r = betrachter.patch(f"/api/contents/{c['id']}", json={"data": {"fields": {"qr_url": "https://x.de"}}})
    assert r.status_code == 403


def test_web_content_with_client_check(admin):
    r = admin.post("/api/contents", json={"type": "web", "title": "Seite", "data": {
        "url": "https://example.com", "zoom": 1.5,
        "embed_check": {"embeddable": False, "message": "verboten"}}})
    assert r.status_code == 201
    c = r.get_json()
    assert c["data"]["url"] == "https://example.com" and c["data"]["zoom"] == 1.5
    assert c["data"]["embed_check"]["embeddable"] is False and c["data"]["embed_check"]["checked_at"]
    assert [w["code"] for w in c["warnings"]] == ["not_embeddable"]
    r = admin.post("/api/contents", json={"type": "web", "title": "X", "data": {"url": "ftp://x"}})
    assert r.status_code == 422 and "data.url" in r.get_json()["error"]["fields"]


def test_list_filters_and_tags(admin):
    make_image(admin, "Sommer.png")
    make_text(admin, "Winter")
    upload(admin, "Getaggt.png", png_bytes(100, 100), tags=["Event"])
    all_ = admin.get("/api/contents").get_json()
    assert all_["total"] == 3 and all_["tags"] == ["Event"]
    assert admin.get("/api/contents?type=text").get_json()["total"] == 1
    assert admin.get("/api/contents?q=somm").get_json()["items"][0]["title"] == "Sommer"
    assert admin.get("/api/contents?tag=event").get_json()["total"] == 1
    titles = [c["title"] for c in admin.get("/api/contents?sort=title_asc").get_json()["items"]]
    assert titles == sorted(titles, key=str.lower)


def test_delete_in_use_409_and_bulk(admin):
    used = make_image(admin, "benutzt.png")
    free = make_image(admin, "frei.png")
    make_presentation(admin, "P", [used["id"]], publish=True)
    r = admin.delete(f"/api/contents/{used['id']}")
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "in_use"
    usage = r.get_json()["error"]["details"]["usages"][0]
    assert usage["type"] == "presentation" and usage["published"] is True
    r = admin.post("/api/contents/bulk-delete", json={"ids": [used["id"], free["id"]]})
    assert r.get_json()["deleted"] == [free["id"]] and r.get_json()["blocked"][0]["id"] == used["id"]
    assert admin.get(f"/api/contents/{free['id']}").status_code == 404


def test_update_content_edit_conflict(admin):
    """PATCH mit veraltetem expected_updated_at überschreibt nicht still (§3, 409 edit_conflict)."""
    t = make_text(admin, "Original")
    r = admin.patch(f"/api/contents/{t['id']}", json={"title": "Fremd", "expected_updated_at": "2000-01-01T00:00:00Z"})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "edit_conflict"
    assert admin.get(f"/api/contents/{t['id']}").get_json()["title"] == "Original"
    r = admin.patch(f"/api/contents/{t['id']}", json={"title": "Neu", "expected_updated_at": t["updated_at"]})
    assert r.status_code == 200 and r.get_json()["title"] == "Neu"


def test_bulk_delete_checks_usages_inside_transaction(admin, monkeypatch):
    from stelecms import media
    from stelecms.api import contents as api_contents
    seen = []

    class SpyIndex(media.UsageIndex):
        def __init__(self, conn):
            seen.append(conn.in_transaction)
            super().__init__(conn)

    monkeypatch.setattr(api_contents.media, "UsageIndex", SpyIndex)
    free = make_image(admin, "frei.png")
    seen.clear()  # Anlegen baut den Index nur zum Lesen
    r = admin.post("/api/contents/bulk-delete", json={"ids": [free["id"]]})
    assert r.get_json()["deleted"] == [free["id"]] and seen == [True]


def test_published_snapshot_keeps_content_in_use(admin):
    img = make_image(admin)
    p = make_presentation(admin, "P", [img["id"]], publish=True)
    admin.put(f"/api/presentations/{p['id']}/items", json={"items": []})
    usages = admin.get(f"/api/contents/{img['id']}").get_json()["usages"]
    assert usages and usages[0]["published"] is True
    assert admin.delete(f"/api/contents/{img['id']}").status_code == 409


def test_media_access_rules(admin, anon, app):
    img = make_image(admin)
    url = img["urls"]["display"]
    r = anon.get(url)
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "unauthenticated"
    r = admin.get(url)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "private, max-age=31536000, immutable"
    r = admin.get(url, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100
    key = stele_key(make_stele(admin))
    assert anon.get(url, headers={"X-Stele-Key": key}).status_code == 200
    assert anon.get(url, headers={"X-Stele-Key": "falsch"}).status_code == 401
    assert admin.get(f"/media/{img['uid']}/../../cms.db").status_code == 404
