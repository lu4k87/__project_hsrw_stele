"""Rechte je Endpunkt (403/401), Schutzregeln (SPEC §6.3), Benutzer und Rollen (§7.3)."""
from __future__ import annotations

import pytest

from conftest import Client

# (Methode, Pfad, Body) – Betrachter darf das alles nicht
FORBIDDEN_FOR_VIEWER = [
    ("POST", "/api/contents", {"type": "text", "title": "X", "data": {}}),
    ("DELETE", "/api/contents/1", None),
    ("POST", "/api/contents/bulk-delete", {"ids": [1]}),
    ("POST", "/api/contents/check-url", {"url": "https://example.com"}),
    ("POST", "/api/presentations", {"name": "X"}),
    ("PUT", "/api/presentations/1/items", {"items": []}),
    ("POST", "/api/presentations/1/publish", {}),
    ("POST", "/api/presentations/1/reject", {"note": "nein"}),
    ("DELETE", "/api/presentations/1", None),
    ("POST", "/api/designs", {"name": "X"}),
    ("PATCH", "/api/designs/1", {"name": "Y"}),
    ("POST", "/api/touch-menus", {"name": "X"}),
    ("POST", "/api/steles", {"name": "X"}),
    ("POST", "/api/steles/1/commands", {"command": "reload"}),
    ("POST", "/api/steles/1/rotate-key", None),
    ("POST", "/api/schedule", {}),
    ("GET", "/api/users", None),
    ("POST", "/api/users", {}),
    ("POST", "/api/roles", {"name": "X"}),
    ("GET", "/api/audit", None),
    ("GET", "/api/audit/export.csv", None),
    ("PATCH", "/api/settings", {"org_name": "X"}),
    ("GET", "/api/system/backup", None),
    ("GET", "/api/system/info", None),
    ("GET", "/api/pairing/pending", None),
]

NEEDS_LOGIN = ["/api/contents", "/api/presentations", "/api/designs", "/api/touch-menus", "/api/steles",
               "/api/schedule", "/api/monitoring/overview", "/api/dashboard", "/api/audit", "/api/settings",
               "/api/users", "/api/roles", "/api/permissions"]


@pytest.mark.parametrize("method,path,payload", FORBIDDEN_FOR_VIEWER)
def test_viewer_forbidden(betrachter, method, path, payload):
    r = betrachter.open(method, path, json=payload)
    assert r.status_code == 403, (path, r.get_json())
    assert r.get_json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("path", NEEDS_LOGIN)
def test_requires_login(anon, path):
    r = anon.get(path)
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize("path", ["/api/contents", "/api/presentations", "/api/designs", "/api/touch-menus",
                                  "/api/steles", "/api/schedule", "/api/monitoring/overview", "/api/dashboard"])
def test_viewer_can_read(betrachter, path):
    assert betrachter.get(path).status_code == 200


def test_permission_catalog(admin):
    groups = admin.get("/api/permissions").get_json()["groups"]
    keys = [p["key"] for g in groups for p in g["permissions"]]
    assert keys[0] == "monitoring.view" and keys[-1] == "settings.manage" and len(keys) == 20
    edit = next(p for g in groups for p in g["permissions"] if p["key"] == "presentations.edit")
    assert set(edit["implies"]) == {"presentations.view", "content.view"} and edit["description"]


def test_effective_permissions_of_roles(admin):
    roles = {r["name"]: r for r in admin.get("/api/roles").get_json()["items"]}
    assert set(roles) == {"Administrator", "Redaktion", "Autor", "Betrachter"}
    red = roles["Redaktion"]
    assert "presentations.view" in red["effective_permissions"] and "presentations.view" not in red["permissions"]
    assert "steles.view" in red["effective_permissions"]
    assert roles["Administrator"]["is_admin"] and len(roles["Administrator"]["effective_permissions"]) == 20
    assert roles["Autor"]["user_count"] == 1


def test_autor_can_request_review_but_not_publish(autor):
    p = autor.post("/api/presentations", json={"name": "Autorenwerk"}).get_json()
    assert autor.post(f"/api/presentations/{p['id']}/publish", json={}).status_code == 403
    r = autor.post(f"/api/presentations/{p['id']}/request-review", json={"note": "Bitte ansehen"})
    assert r.status_code == 200 and r.get_json()["review_state"] == "requested"
    assert r.get_json()["review_by"]["display_name"] == "Arne Autor"


def _roles(client):
    return {r["name"]: r["id"] for r in client.get("/api/roles").get_json()["items"]}


def _users(client):
    return {u["username"]: u for u in client.get("/api/users").get_json()["items"]}


def test_create_user_generated_password(admin, app):
    r = admin.post("/api/users", json={"username": "neu.user", "display_name": "Neu",
                                       "role_id": _roles(admin)["Autor"]})
    assert r.status_code == 201
    body = r.get_json()
    assert len(body["generated_password"]) == 12 and body["user"]["must_change_password"] is True
    Client(app).login("neu.user", body["generated_password"])


def test_create_user_validation(admin):
    r = admin.post("/api/users", json={"username": "a", "display_name": "", "role_id": 999, "password": "kurz"})
    assert r.status_code == 422
    assert set(r.get_json()["error"]["fields"]) >= {"username", "display_name", "role_id", "password"}
    r = admin.post("/api/users", json={"username": "ADMIN", "display_name": "X", "role_id": 1})
    assert r.status_code == 422 and "username" in r.get_json()["error"]["fields"]


def test_reset_password_ends_sessions(admin, app):
    victim = Client(app).login("autor")
    uid = _users(admin)["autor"]["id"]
    r = admin.post(f"/api/users/{uid}/password", json={})
    assert r.status_code == 200 and r.get_json()["generated_password"]
    assert victim.get("/api/contents").status_code == 401


def test_self_protection(admin):
    me = _users(admin)["admin"]
    r = admin.delete(f"/api/users/{me['id']}")
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "self_protection"
    r = admin.patch(f"/api/users/{me['id']}", json={"role_id": _roles(admin)["Autor"]})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "self_protection"
    r = admin.patch(f"/api/users/{me['id']}", json={"is_active": False})
    assert r.get_json()["error"]["code"] == "self_protection"


def test_admin_accounts_and_role_only_by_admins(admin, app):
    """users.manage reicht nie für Administrator-Konten und die Administrator-Rolle (§6.3) – auch nicht, wenn das
    Konto die Rechte der Rolle Autor mitbringt und Autor-Konten deshalb verwalten darf."""
    roles = _roles(admin)
    hr_role = admin.post("/api/roles", json={"name": "Personal", "permissions": [
        "users.manage", "monitoring.view", "content.edit", "presentations.edit", "schedule.view",
        "steles.view"]}).get_json()
    admin.post("/api/users", json={"username": "personal", "display_name": "HR", "role_id": hr_role["id"],
                                   "password": "personal123", "must_change_password": False})
    hr = Client(app).login("personal", "personal123")
    users = _users(admin)
    admin_id, autor_id = users["admin"]["id"], users["autor"]["id"]
    for method, url, payload in (
            ("DELETE", f"/api/users/{admin_id}", None),
            ("PATCH", f"/api/users/{admin_id}", {"is_active": False}),
            ("PATCH", f"/api/users/{admin_id}", {"role_id": roles["Autor"]}),
            ("PATCH", f"/api/users/{admin_id}", {"display_name": "Übernommen"}),
            ("POST", f"/api/users/{admin_id}/password", {"password": "uebernommen123"}),
            ("PATCH", f"/api/users/{autor_id}", {"role_id": roles["Administrator"]}),
            ("POST", "/api/users", {"username": "chef2", "display_name": "Chef", "role_id": roles["Administrator"],
                                    "password": "chef2chef2"})):
        r = hr.open(method, url, json=payload)
        assert r.status_code == 403 and r.get_json()["error"]["code"] == "forbidden", (method, url, payload)
    after = _users(admin)
    assert after["admin"]["is_active"] and after["admin"]["display_name"] == "Alex Admin"
    assert after["autor"]["role"]["name"] == "Autor" and "chef2" not in after
    Client(app).login("admin", "admin123")  # Passwort unverändert
    # Konten, deren Rechte HR selbst hat, verwaltet HR weiter
    assert hr.patch(f"/api/users/{autor_id}", json={"role_id": roles["Betrachter"]}).status_code == 200
    assert hr.post(f"/api/users/{autor_id}/password", json={}).status_code == 200
    # … aber keine Rolle mit mehr Rechten vergeben und kein Redaktions-Konto übernehmen
    r = hr.patch(f"/api/users/{autor_id}", json={"role_id": roles["Redaktion"]})
    assert r.status_code == 403 and "presentations.publish" in r.get_json()["error"]["details"]["missing_permissions"]
    r = hr.post(f"/api/users/{users['redaktion']['id']}/password", json={})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "forbidden"
    Client(app).login("redaktion", "redaktion123")  # Passwort unverändert
    # Administratoren dürfen es
    r = admin.post("/api/users", json={"username": "chefin", "display_name": "Chefin",
                                       "role_id": roles["Administrator"], "password": "chefin12345"})
    assert r.status_code == 201
    assert admin.patch(f"/api/users/{r.get_json()['user']['id']}", json={"is_active": False}).status_code == 200


def test_users_manage_only_accounts_with_own_permissions(admin, app):
    """Ohne Administrator-Rolle nur Konten verwalten und Rollen vergeben, deren effektive Rechte das eigene Konto
    selbst hat (§6.3) – sonst ließe sich ein mächtigeres Konto per Passwort-Reset übernehmen."""
    roles = _roles(admin)
    hr_role = admin.post("/api/roles", json={"name": "Nur Personal", "permissions": ["users.manage"]}).get_json()
    admin.post("/api/users", json={"username": "personal", "display_name": "HR", "role_id": hr_role["id"],
                                   "password": "personal123", "must_change_password": False})
    hr = Client(app).login("personal", "personal123")
    users = _users(admin)
    autor_id = users["autor"]["id"]
    for method, url, payload in (
            ("DELETE", f"/api/users/{autor_id}", None),
            ("PATCH", f"/api/users/{autor_id}", {"display_name": "Übernommen"}),
            ("PATCH", f"/api/users/{autor_id}", {"is_active": False}),
            ("POST", f"/api/users/{autor_id}/password", {"password": "uebernommen123"}),
            ("POST", f"/api/users/{autor_id}/unlock", None),
            ("POST", "/api/users", {"username": "neu.autor", "display_name": "Neu", "role_id": roles["Autor"],
                                    "password": "neuautor123"})):
        r = hr.open(method, url, json=payload)
        err = r.get_json()["error"]
        assert r.status_code == 403 and err["code"] == "forbidden", (method, url, payload)
        assert "content.edit" in err["details"]["missing_permissions"] and "content.edit" in err["message"]
    after = _users(admin)
    assert after["autor"]["display_name"] == "Arne Autor" and after["autor"]["is_active"]
    assert "neu.autor" not in after
    Client(app).login("autor", "autor123")  # Passwort unverändert
    # Konten mit einer Rolle, deren Rechte HR selbst hat: anlegen, ändern, zurücksetzen, löschen
    lesen = admin.post("/api/roles", json={"name": "Benutzer lesen", "permissions": ["users.view"]}).get_json()
    r = hr.post("/api/users", json={"username": "leser", "display_name": "Leser", "role_id": lesen["id"]})
    assert r.status_code == 201
    uid = r.get_json()["user"]["id"]
    assert hr.patch(f"/api/users/{uid}", json={"display_name": "Lea Leser"}).status_code == 200
    assert hr.post(f"/api/users/{uid}/password", json={}).status_code == 200
    assert hr.post(f"/api/users/{uid}/unlock").status_code == 200
    r = hr.patch(f"/api/users/{uid}", json={"role_id": roles["Betrachter"]})  # neue Rolle hat mehr Rechte
    assert r.status_code == 403 and "content.view" in r.get_json()["error"]["details"]["missing_permissions"]
    assert hr.delete(f"/api/users/{uid}").status_code == 200


def test_users_manage_rechecks_target_in_transaction(admin, app, monkeypatch):
    """Ziel-Konto wird in der Schreib-Transaktion neu gelesen (CWE-367): bekommt es zwischen Prüfung und
    Schreiben die Rolle Administrator, greift weder Passwort-Reset noch Änderung."""
    from stelecms import db as dbm
    from stelecms.api import users as api_users

    roles = _roles(admin)
    hr_role = admin.post("/api/roles", json={"name": "Nur Personal", "permissions": ["users.manage"]}).get_json()
    lesen = admin.post("/api/roles", json={"name": "Benutzer lesen", "permissions": ["users.view"]}).get_json()
    admin.post("/api/users", json={"username": "personal", "display_name": "HR", "role_id": hr_role["id"],
                                   "password": "personal123", "must_change_password": False})
    tid = admin.post("/api/users", json={"username": "ziel", "display_name": "Ziel", "role_id": lesen["id"],
                                         "password": "ziel12345", "must_change_password": False}
                     ).get_json()["user"]["id"]
    hr = Client(app).login("personal", "personal123")
    real_load, calls = api_users._load, []

    def promoted_after_check(conn, uid):
        user = real_load(conn, uid)
        if not calls:  # nach dem ersten Lesen (frühe Prüfung) parallel zum Administrator machen
            other = dbm.connect(app.config["DB_PATH"])
            other.execute("UPDATE users SET role_id = ? WHERE id = ?", (roles["Administrator"], uid))
            other.commit()
            other.close()
        calls.append(uid)
        return user

    def reset_role():
        calls.clear()
        other = dbm.connect(app.config["DB_PATH"])
        other.execute("UPDATE users SET role_id = ? WHERE id = ?", (lesen["id"], tid))
        other.commit()
        other.close()

    monkeypatch.setattr(api_users, "_load", promoted_after_check)
    r = hr.post(f"/api/users/{tid}/password", json={"password": "uebernommen123"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "forbidden"
    reset_role()
    r = hr.patch(f"/api/users/{tid}", json={"display_name": "Übernommen"})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "stale"
    reset_role()
    r = hr.delete(f"/api/users/{tid}")
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "forbidden"
    monkeypatch.undo()
    reset_role()
    assert _users(admin)["ziel"]["display_name"] == "Ziel"
    Client(app).login("ziel", "ziel12345")  # Passwort unverändert


def test_roles_grant_only_own_permissions(admin, app):
    """roles.manage ohne Administrator: nur eigene Rechte vergeben, eigene Rolle nicht erweitern (§6.3)."""
    roles = _roles(admin)
    rv_role = admin.post("/api/roles", json={"name": "Rollenpflege",
                                             "permissions": ["roles.manage", "content.view"]}).get_json()
    admin.post("/api/users", json={"username": "rollen", "display_name": "RV", "role_id": rv_role["id"],
                                   "password": "rollen12345", "must_change_password": False})
    rv = Client(app).login("rollen", "rollen12345")
    for payload in ({"name": "Alles", "permissions": ["settings.manage"]},
                    {"name": "Kopie Admin", "copy_from": roles["Administrator"]}):
        r = rv.post("/api/roles", json=payload)
        assert r.status_code == 403 and r.get_json()["error"]["code"] == "forbidden", payload
    assert rv.post("/api/roles", json={"name": "Lesen", "permissions": ["content.view"]}).status_code == 201
    # Eigene Rolle nicht erweitern
    r = rv.patch(f"/api/roles/{rv_role['id']}", json={"permissions": ["roles.manage", "content.view",
                                                                       "users.manage"]})
    assert r.status_code == 403
    # Fremder Rolle keine Rechte geben, die man selbst nicht hat …
    autor = next(x for x in admin.get("/api/roles").get_json()["items"] if x["name"] == "Autor")
    r = rv.patch(f"/api/roles/{autor['id']}", json={"permissions": autor["permissions"] + ["settings.manage"]})
    assert r.status_code == 403
    # … vorhandene (auch nicht selbst gehaltene) Rechte behalten und entfernen geht
    kept = [k for k in autor["permissions"] if k != "schedule.view"]
    r = rv.patch(f"/api/roles/{autor['id']}", json={"permissions": kept})
    assert r.status_code == 200 and "schedule.view" not in r.get_json()["permissions"]
    assert "settings.manage" not in rv.get("/api/auth/session").get_json()["permissions"]


def test_role_locked_and_in_use(admin):
    roles = _roles(admin)
    r = admin.patch(f"/api/roles/{roles['Administrator']}", json={"name": "Chef"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "role_locked"
    r = admin.delete(f"/api/roles/{roles['Administrator']}")
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "role_locked"
    r = admin.delete(f"/api/roles/{roles['Autor']}")
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "role_in_use"
    assert r.get_json()["error"]["details"]["user_count"] == 1


def test_role_copy_update_delete(admin):
    roles = _roles(admin)
    r = admin.post("/api/roles", json={"name": "Autor plus", "copy_from": roles["Autor"]})
    assert r.status_code == 201
    new = r.get_json()
    assert "presentations.edit" in new["permissions"]
    r = admin.patch(f"/api/roles/{new['id']}", json={"permissions": ["content.view", "gibt.es.nicht"]})
    assert r.status_code == 422
    r = admin.patch(f"/api/roles/{new['id']}", json={"permissions": ["content.delete"]})
    assert r.get_json()["effective_permissions"] == ["content.view", "content.delete"]
    assert admin.delete(f"/api/roles/{new['id']}").get_json() == {"ok": True}
