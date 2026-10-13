"""Gemeinsame Fixtures: eigene App je Test mit Temp-Datenordner, ohne Hintergrund-Threads."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

os.environ["STELECMS_BACKGROUND"] = "0"
os.environ.setdefault("STELECMS_SEED_DEMO", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stelecms import create_app  # noqa: E402

FAST_HASH = "pbkdf2:sha256:1000"
MUTATING = ("POST", "PUT", "PATCH", "DELETE")


class Client:
    """Test-Client mit Anmeldung und automatischem CSRF-Header."""

    def __init__(self, app, remote_addr: str = "127.0.0.1"):
        self.app = app
        self.c = app.test_client()
        self.c.environ_base["REMOTE_ADDR"] = remote_addr
        self.csrf = None
        self.session = None

    def login(self, username: str, password: str | None = None):
        if password is None:
            r = self.c.post("/api/auth/dev-login", json={"username": username})
        else:
            r = self.c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.get_json()
        self.session = r.get_json()
        self.csrf = self.session["csrf_token"]
        return self

    def open(self, method: str, url: str, csrf: bool = True, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if csrf and method in MUTATING and self.csrf:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return self.c.open(url, method=method, headers=headers, **kw)

    def get(self, url, **kw):
        return self.open("GET", url, **kw)

    def post(self, url, **kw):
        return self.open("POST", url, **kw)

    def put(self, url, **kw):
        return self.open("PUT", url, **kw)

    def patch(self, url, **kw):
        return self.open("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.open("DELETE", url, **kw)


@pytest.fixture
def app(tmp_path):
    return create_app({"DATA_DIR": tmp_path / "data", "BACKGROUND": False, "SEED_DEMO": False,
                       "PASSWORD_HASH_METHOD": FAST_HASH, "TESTING": True})


@pytest.fixture
def anon(app):
    return Client(app)


@pytest.fixture
def admin(app):
    return Client(app).login("admin")


@pytest.fixture
def redaktion(app):
    return Client(app).login("redaktion")


@pytest.fixture
def autor(app):
    return Client(app).login("autor")


@pytest.fixture
def betrachter(app):
    return Client(app).login("betrachter")


# ------------------------------------------------------------------ Testdateien

def png_bytes(w=1080, h=1920, color=(30, 90, 168), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, fmt)
    return buf.getvalue()


def pdf_bytes(pages=3) -> bytes:
    imgs = [Image.new("RGB", (600, 800), (255, 255, 255 - i * 40)) for i in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


@pytest.fixture(scope="session")
def video_bytes(tmp_path_factory) -> bytes:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg nicht installiert")
    out = tmp_path_factory.mktemp("vid") / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x568:rate=15", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(out)], check=True, timeout=60)
    return out.read_bytes()


def upload(client: Client, name: str, data: bytes, tags=None):
    form = {"files": (io.BytesIO(data), name)}
    if tags is not None:
        import json
        form["tags"] = json.dumps(tags)
    return client.post("/api/contents/upload", data=form, content_type="multipart/form-data")


def make_image(client: Client, name="bild.png", **kw) -> dict:
    r = upload(client, name, png_bytes(**kw))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["items"][0]


def make_text(client: Client, title="Info", **data) -> dict:
    payload = {"template": "title_text", "fields": {"title": title}}
    payload.update(data)
    r = client.post("/api/contents", json={"type": "text", "title": title, "data": payload})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def make_presentation(client: Client, name="Test", content_ids=(), publish=False) -> dict:
    r = client.post("/api/presentations", json={"name": name})
    assert r.status_code == 201, r.get_json()
    p = r.get_json()
    if content_ids:
        r = client.put(f"/api/presentations/{p['id']}/items",
                       json={"items": [{"content_id": c, "enabled": True} for c in content_ids]})
        assert r.status_code == 200, r.get_json()
        p = r.get_json()
    if publish:
        r = client.post(f"/api/presentations/{p['id']}/publish", json={})
        assert r.status_code == 200, r.get_json()
        p = r.get_json()
    return p


def make_stele(client: Client, name="Stele 1", **extra) -> dict:
    r = client.post("/api/steles", json={"name": name, **extra})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def stele_key(stele: dict) -> str:
    return stele["player_url"].split("key=", 1)[1]
