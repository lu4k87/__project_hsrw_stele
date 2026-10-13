"""Kommandozeile server/manage.py: create-admin, reset-password, list-users."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from stelecms import create_app

from conftest import FAST_HASH, Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manage  # noqa: E402


@pytest.fixture
def overrides(tmp_path):
    return {"DATA_DIR": tmp_path / "data", "SEED_DEMO": False, "PASSWORD_HASH_METHOD": FAST_HASH,
            "TESTING": True}


def run(overrides, *argv):
    out = io.StringIO()
    code = manage.main(list(argv), app_overrides=overrides, out=out)
    return code, out.getvalue()


def test_create_admin_with_password_and_login(overrides):
    code, out = run(overrides, "create-admin", "--username", "mk", "--display-name", "MK (Admin)",
                    "--password", "sehrGeheim123", "--must-change")
    assert code == 0 and "sehrGeheim123" not in out and "mk" in out
    app = create_app(dict(overrides, BACKGROUND=False))
    c = Client(app).login("mk", "sehrGeheim123")
    user = c.session["user"]
    assert user["role"]["is_admin"] and user["must_change_password"] is True and user["is_demo"] is False
    assert len(c.session["permissions"]) == 20
    entry = c.get("/api/audit?entity_type=user&action=create").get_json()["items"][0]
    assert entry["user"] is None and "per Kommandozeile" in entry["summary"]
    # Demo-Konten sind weiterhin vorhanden (Seed lief beim Anlegen der Datenbank)
    Client(app).login("admin", "admin123")


def test_create_admin_generated_password_and_duplicate(overrides):
    code, out = run(overrides, "create-admin", "--username", "chefin", "--display-name", "Chefin", "--no-must-change")
    assert code == 0
    pw = out.strip().rsplit(": ", 1)[1]
    assert len(pw) == 14
    code, _ = run(overrides, "create-admin", "--username", "chefin", "--display-name", "Chefin")
    assert code == 1
    code, _ = run(overrides, "create-admin", "--username", "chefin", "--display-name", "Chefin", "--update",
                  "--password", "nochMalNeu123")
    assert code == 0
    app = create_app(dict(overrides, BACKGROUND=False))
    Client(app).login("chefin", "nochMalNeu123")


def test_create_admin_validation(overrides):
    assert run(overrides, "create-admin", "--username", "x", "--display-name", "X")[0] == 1
    assert run(overrides, "create-admin", "--username", "gut", "--display-name", "X", "--password", "kurz")[0] == 1


def test_reset_password_and_list(overrides):
    code, out = run(overrides, "reset-password", "--username", "autor", "--password", "autorNeu123",
                    "--no-must-change")
    assert code == 0
    assert run(overrides, "reset-password", "--username", "niemand")[0] == 1
    code, out = run(overrides, "list-users")
    assert code == 0 and "autor" in out and "Administrator" in out
    app = create_app(dict(overrides, BACKGROUND=False))
    Client(app).login("autor", "autorNeu123")
