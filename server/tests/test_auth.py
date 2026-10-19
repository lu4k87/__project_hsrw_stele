"""Anmeldung, Sperre, Rate-Limit, Schnellanmeldung, CSRF, Leerlauf (SPEC §7.2)."""
from __future__ import annotations

import time

from conftest import Client


def test_session_anonymous(anon):
    r = anon.get("/api/auth/session")
    assert r.status_code == 200
    s = r.get_json()
    assert s["authenticated"] is False and s["user"] is None and s["csrf_token"] is None
    assert s["dev_login"]["enabled"] is True
    assert {u["username"] for u in s["dev_login"]["users"]} == {"admin", "redaktion", "autor", "betrachter"}
    assert s["app"] == {"name": "Stele CMS", "version": "1.0.0", "org_name": "Meine Organisation",
                        "test_mode": True}


def test_login_ok_returns_session(anon):
    r = anon.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    s = r.get_json()
    assert s["authenticated"] and s["user"]["username"] == "admin"
    assert s["user"]["role"]["is_admin"] is True
    assert "settings.manage" in s["permissions"] and s["permissions"] == sorted(s["permissions"])
    assert s["csrf_token"] and s["idle_timeout_s"] == 3600
    assert "stelecms_session" in r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in r.headers["Set-Cookie"]


def test_login_wrong_password_and_unknown_user_same_message(anon):
    r1 = anon.post("/api/auth/login", json={"username": "admin", "password": "falsch"})
    r2 = anon.post("/api/auth/login", json={"username": "gibtsnicht", "password": "falsch"})
    assert r1.status_code == r2.status_code == 401
    assert r1.get_json()["error"]["code"] == "invalid_credentials"
    assert r1.get_json()["error"]["message"] == r2.get_json()["error"]["message"] == \
        "Benutzername oder Passwort ist falsch."


def test_login_validation_fields(anon):
    r = anon.post("/api/auth/login", json={})
    assert r.status_code == 422
    assert set(r.get_json()["error"]["fields"]) == {"username", "password"}


def test_lockout_after_failed_attempts(anon):
    for _ in range(4):
        assert anon.post("/api/auth/login", json={"username": "autor", "password": "x"}).status_code == 401
    r = anon.post("/api/auth/login", json={"username": "autor", "password": "x"})
    assert r.status_code == 423
    err = r.get_json()["error"]
    assert err["code"] == "account_locked" and err["details"]["retry_after_s"] > 0
    # Auch das richtige Passwort hilft während der Sperre nicht
    assert anon.post("/api/auth/login", json={"username": "autor", "password": "autor123"}).status_code == 423


def test_unlock_by_admin(anon, admin):
    for _ in range(5):
        anon.post("/api/auth/login", json={"username": "autor", "password": "x"})
    users = {u["username"]: u for u in admin.get("/api/users").get_json()["items"]}
    assert users["autor"]["locked"] is True
    r = admin.post(f"/api/users/{users['autor']['id']}/unlock")
    assert r.status_code == 200 and r.get_json()["locked"] is False
    assert anon.post("/api/auth/login", json={"username": "autor", "password": "autor123"}).status_code == 200


def test_ip_rate_limit(anon):
    codes = [anon.post("/api/auth/login", json={"username": f"x{i}", "password": "y"}).status_code
             for i in range(21)]
    assert codes[:20] == [401] * 20
    assert codes[20] == 429


def test_ip_rate_limit_counts_every_attempt(anon):
    """Prüfen und Zählen in einem Schritt vor der Passwortprüfung: auch erfolgreiche Versuche zählen."""
    codes = [anon.post("/api/auth/login", json={"username": "autor", "password": "autor123"}).status_code
             for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429


def test_failed_login_counter_is_atomic(app, anon, monkeypatch):
    """Parallele Fehlversuche (hier: während der Passwortprüfung in der DB hochgezählt) gehen nicht verloren."""
    from stelecms import db as dbm
    from stelecms.api import auth as api_auth

    def parallel_failures(_hash, _password):
        conn = dbm.connect(app.config["DB_PATH"])
        conn.execute("UPDATE users SET failed_logins = 4 WHERE username = 'autor'")
        conn.close()
        return False

    monkeypatch.setattr(api_auth, "check_password", parallel_failures)
    r = anon.post("/api/auth/login", json={"username": "autor", "password": "x"})
    assert r.status_code == 423 and r.get_json()["error"]["code"] == "account_locked"


def test_lock_set_during_password_check_blocks_login(app, anon, monkeypatch):
    from stelecms import db as dbm
    from stelecms import timeutil
    from stelecms.api import auth as api_auth
    from datetime import timedelta

    def locked_meanwhile(_hash, _password):
        conn = dbm.connect(app.config["DB_PATH"])
        conn.execute("UPDATE users SET locked_until = ? WHERE username = 'autor'",
                     (timeutil.iso(timeutil.utcnow() + timedelta(minutes=5)),))
        conn.close()
        return True

    monkeypatch.setattr(api_auth, "check_password", locked_meanwhile)
    r = anon.post("/api/auth/login", json={"username": "autor", "password": "autor123"})
    assert r.status_code == 423 and r.get_json()["error"]["code"] == "account_locked"
    # Sperre bleibt bestehen, die Abweisung steht im Protokoll, keine Anmeldung protokolliert
    conn = dbm.connect(app.config["DB_PATH"])
    try:
        assert conn.execute("SELECT locked_until FROM users WHERE username = 'autor'").fetchone()[0]
        actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE entity_name = 'autor' "
                                              "AND entity_type = 'session' ORDER BY id")]
    finally:
        conn.close()
    assert actions == ["login_failed"]


def test_dev_login_loopback_only(app):
    remote = Client(app, remote_addr="10.1.2.3")
    s = remote.get("/api/auth/session").get_json()
    assert s["dev_login"] == {"enabled": False, "users": []}
    r = remote.post("/api/auth/dev-login", json={"username": "admin"})
    assert r.status_code == 403


def test_dev_login_disabled_by_setting(admin, anon):
    assert admin.patch("/api/settings", json={"dev_login_enabled": False}).status_code == 200
    assert anon.post("/api/auth/dev-login", json={"username": "admin"}).status_code == 403
    s = anon.get("/api/auth/session").get_json()
    assert s["app"]["test_mode"] is False and s["dev_login"]["enabled"] is False


def test_dev_login_requires_demo_account(admin, anon):
    roles = {r["name"]: r["id"] for r in admin.get("/api/roles").get_json()["items"]}
    admin.post("/api/users", json={"username": "echt", "display_name": "Echt", "role_id": roles["Autor"],
                                   "password": "geheim12345"})
    assert anon.post("/api/auth/dev-login", json={"username": "echt"}).status_code == 403


def test_csrf_missing_and_wrong(admin):
    r = admin.patch("/api/auth/profile", json={"display_name": "X"}, csrf=False)
    assert r.status_code == 403 and "Sicherheitsprüfung" in r.get_json()["error"]["message"]
    r = admin.patch("/api/auth/profile", json={"display_name": "X"}, csrf=False, headers={"X-CSRF-Token": "falsch"})
    assert r.status_code == 403
    assert admin.patch("/api/auth/profile", json={"display_name": "Alex A."}).status_code == 200


def test_unauthenticated_mutation_is_401_not_403(anon):
    r = anon.post("/api/presentations", json={"name": "X"})
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "unauthenticated"


def test_idle_timeout(admin):
    with admin.c.session_transaction() as sess:
        sess["la"] = time.time() - 3 * 3600
    r = admin.get("/api/contents")
    assert r.status_code == 401
    assert r.get_json()["error"]["message"] == "Sitzung abgelaufen – bitte neu anmelden."


def test_background_poll_does_not_extend_session(admin):
    old = time.time() - 600
    with admin.c.session_transaction() as sess:
        sess["la"] = old
    assert admin.get("/api/dashboard", headers={"X-Background-Poll": "1"}).status_code == 200
    with admin.c.session_transaction() as sess:
        assert abs(sess["la"] - old) < 1
    assert admin.get("/api/dashboard").status_code == 200
    with admin.c.session_transaction() as sess:
        assert sess["la"] > old + 500


def test_logout(admin):
    assert admin.post("/api/auth/logout").get_json() == {"ok": True}
    assert admin.get("/api/contents").status_code == 401


def test_password_change_ends_other_sessions(app):
    a = Client(app).login("redaktion", "redaktion123")
    b = Client(app).login("redaktion", "redaktion123")
    r = a.post("/api/auth/password", json={"current_password": "falsch", "new_password": "neuesPasswort1"})
    assert r.status_code == 422 and "current_password" in r.get_json()["error"]["fields"]
    r = a.post("/api/auth/password", json={"current_password": "redaktion123", "new_password": "kurz"})
    assert r.status_code == 422 and "new_password" in r.get_json()["error"]["fields"]
    r = a.post("/api/auth/password", json={"current_password": "redaktion123", "new_password": "neuesPasswort1"})
    assert r.status_code == 200 and r.get_json()["authenticated"]
    assert a.get("/api/contents").status_code == 200
    assert b.get("/api/contents").status_code == 401
    r = Client(app).c.post("/api/auth/login", json={"username": "redaktion", "password": "neuesPasswort1"})
    assert r.status_code == 200


def test_profile_update(admin):
    r = admin.patch("/api/auth/profile", json={"display_name": "Alexandra Admin", "email": "a@beispiel.de"})
    assert r.status_code == 200 and r.get_json()["user"]["display_name"] == "Alexandra Admin"
    r = admin.patch("/api/auth/profile", json={"email": "kein-mail"})
    assert r.status_code == 422 and "email" in r.get_json()["error"]["fields"]


def test_disabled_account_cannot_login(admin, anon):
    users = {u["username"]: u for u in admin.get("/api/users").get_json()["items"]}
    admin.patch(f"/api/users/{users['autor']['id']}", json={"is_active": False})
    r = anon.post("/api/auth/login", json={"username": "autor", "password": "autor123"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "account_disabled"


def test_login_is_audited(admin):
    items = admin.get("/api/audit?action=login").get_json()["items"]
    assert items and items[0]["user"]["username"] == "admin" and items[0]["entity_type"] == "session"
