"""Benutzer (SPEC §7.3) inkl. Schutzregeln (§6.3)."""
from __future__ import annotations

from flask import Blueprint, jsonify

from .. import appsettings, timeutil
from .. import auth as authm
from .. import db as dbm
from .. import permissions as perms
from ..audit import audit, q
from ..errors import ApiError, conflict
from ..permissions import require
from ..security import generate_password, hash_password
from ..validation import USERNAME_RE, Validator, body, is_int
from .common import get_or_404, list_response, ok

bp = Blueprint("api_users", __name__, url_prefix="/api/users")


def _load(conn, uid: int) -> dict:
    get_or_404(conn, "users", uid)
    return authm.load_user(conn, uid)


def _active_admin_count(conn, exclude_id: int | None = None) -> int:
    return dbm.scalar(conn, "SELECT COUNT(*) FROM users u JOIN roles r ON r.id = u.role_id "
                            "WHERE r.is_admin = 1 AND u.is_active = 1 AND u.id != ?", (exclude_id or -1,))


def _is_active_admin(user: dict) -> bool:
    return bool(user["role_is_admin"]) and bool(user["is_active"])


ADMIN_ROLE_MSG = "Die Rolle Administrator kann nur eine Administratorin oder ein Administrator vergeben."
ADMIN_ACCOUNT_MSG = "Konten mit der Rolle Administrator kann nur eine Administratorin oder ein Administrator ändern."
LAST_ADMIN_MSG = "Es muss mindestens ein aktives Konto mit der Rolle Administrator bleiben."
STALE_MSG = "Das Konto wurde inzwischen geändert. Bitte neu laden und erneut speichern."


def _load_covered(conn, uid: int) -> dict:
    """In der Schreib-Transaktion: Ziel-Konto frisch lesen und erneut prüfen – sonst greift die Aktion auf ein
    Konto, das zwischen Prüfung und Schreiben z. B. die Rolle Administrator bekommen hat (CWE-367)."""
    user = _load(conn, uid)
    perms.require_covers(conn, user["role_id"], user["role_is_admin"], ADMIN_ACCOUNT_MSG)
    return user


def _require_admin_left(conn, uid: int) -> None:
    """Innerhalb der Schreib-Transaktion aufrufen: parallele Anfragen zählen sonst beide noch den anderen mit."""
    if _active_admin_count(conn, exclude_id=uid) == 0:
        raise conflict(LAST_ADMIN_MSG, "last_admin")


def _role(conn, v: Validator, role_id):
    if role_id is None:
        return None
    if not is_int(role_id):
        v.error("role_id", "Bitte eine Rolle wählen.")
        return None
    r = dbm.row(conn, "SELECT * FROM roles WHERE id = ?", (role_id,))
    if r is None:
        v.error("role_id", "Diese Rolle gibt es nicht (mehr). Bitte eine andere wählen.")
    return r


@bp.get("")
@require("users.view")
def list_users():
    conn = dbm.get_db()
    users = dbm.rows(conn, authm.USER_SELECT + " ORDER BY u.display_name COLLATE NOCASE, u.username")
    return list_response([authm.serialize_user(u) for u in users])


@bp.post("")
@require("users.manage")
def create_user():
    conn = dbm.get_db()
    data = body()
    settings = appsettings.get_settings(conn)
    v = Validator(data)
    username = v.text("username", required=True, max_len=32, empty_msg="Bitte einen Benutzernamen eingeben.")
    if username and not USERNAME_RE.match(username):
        v.error("username", "3–32 Zeichen: Buchstaben (ohne Umlaute), Ziffern sowie . _ -")
    elif username and dbm.scalar(conn, "SELECT 1 FROM users WHERE username = ?", (username,)):
        v.error("username", "Dieser Benutzername ist bereits vergeben.")
    display_name = v.text("display_name", required=True, max_len=80, empty_msg="Bitte einen Anzeigenamen eingeben.")
    email = v.email("email", default="")
    if "role_id" not in data or data.get("role_id") is None:
        v.error("role_id", "Bitte eine Rolle wählen.")
    role = _role(conn, v, data.get("role_id"))
    password = data.get("password")
    generated = None
    if password in (None, ""):
        password = generated = generate_password(max(12, settings["password_min_length"]))
    elif not isinstance(password, str) or len(password) < settings["password_min_length"]:
        v.error("password", f"Das Passwort muss mindestens {settings['password_min_length']} Zeichen haben.")
    must_change = v.boolean("must_change_password", default=True)
    v.done()
    perms.require_covers(conn, role["id"], role["is_admin"], ADMIN_ROLE_MSG)
    now = timeutil.now_iso()
    with dbm.transaction(conn):
        perms.require_covers(conn, role["id"], role["is_admin"], ADMIN_ROLE_MSG)  # Rechte der Rolle frisch (TOCTOU)
        uid = dbm.insert(conn, "users", {
            "username": username, "display_name": display_name, "email": email or "",
            "password_hash": hash_password(password), "role_id": role["id"], "is_active": 1, "is_demo": 0,
            "must_change_password": 1 if must_change else 0, "created_at": now, "updated_at": now})
        audit(conn, "create", "user", f"hat den Benutzer {q(display_name)} ({username}) mit der Rolle "
                                      f"{q(role['name'])} angelegt", entity_id=uid, entity_name=username)
    return jsonify({"user": authm.serialize_user(authm.load_user(conn, uid)), "generated_password": generated}), 201


@bp.get("/<int:uid>")
@require("users.view")
def get_user(uid: int):
    return jsonify(authm.serialize_user(_load(dbm.get_db(), uid)))


@bp.patch("/<int:uid>")
@require("users.manage")
def update_user(uid: int):
    conn = dbm.get_db()
    me = authm.current_user()
    user = _load(conn, uid)
    data = body()
    v = Validator(data)
    display_name = v.text("display_name", max_len=80)
    if "display_name" in data and not display_name:
        v.error("display_name", "Bitte einen Anzeigenamen eingeben.")
    email = v.email("email")
    role = _role(conn, v, data.get("role_id")) if "role_id" in data else None
    if "role_id" in data and data.get("role_id") is None:
        v.error("role_id", "Bitte eine Rolle wählen.")
    is_active = v.boolean("is_active")
    v.done()
    perms.require_covers(conn, user["role_id"], user["role_is_admin"], ADMIN_ACCOUNT_MSG)
    if role is not None:
        perms.require_covers(conn, role["id"], role["is_admin"], ADMIN_ROLE_MSG)
    role_change = role is not None and role["id"] != user["role_id"]
    deactivate = is_active is False and user["is_active"]
    if uid == me["id"] and role_change:
        raise ApiError(403, "self_protection", "Die eigene Rolle kann nicht geändert werden. Bitte eine andere "
                                               "Administratorin oder einen anderen Administrator fragen.")
    if uid == me["id"] and deactivate:
        raise ApiError(403, "self_protection", "Das eigene Konto kann nicht deaktiviert werden.")
    changes = {}
    if display_name and display_name != user["display_name"]:
        changes["display_name"] = display_name
    if email is not None and email != user["email"]:
        changes["email"] = email
    if role_change:
        changes["role_id"] = role["id"]
    if is_active is not None and bool(is_active) != bool(user["is_active"]):
        changes["is_active"] = 1 if is_active else 0
    if changes:
        with dbm.transaction(conn):
            fresh = _load(conn, uid)
            if (fresh["role_id"], bool(fresh["is_active"])) != (user["role_id"], bool(user["is_active"])):
                raise conflict(STALE_MSG, "stale")
            perms.require_covers(conn, fresh["role_id"], fresh["role_is_admin"], ADMIN_ACCOUNT_MSG)
            if role is not None:
                perms.require_covers(conn, role["id"], role["is_admin"], ADMIN_ROLE_MSG)
            if _is_active_admin(fresh) and (deactivate or (role_change and not role["is_admin"])):
                _require_admin_left(conn, uid)
            details = {k: [user.get(k), v_] for k, v_ in changes.items()}
            if role_change:
                details["role_id"] = [user["role_name"], role["name"]]
            if deactivate:
                changes["session_version"] = user["session_version"] + 1
            changes["updated_at"] = timeutil.now_iso()
            dbm.update(conn, "users", uid, changes)
            if "is_active" in changes and len(details) == 1:
                summary = (f"hat den Benutzer {q(user['display_name'])} "
                           f"{'aktiviert' if is_active else 'deaktiviert'}")
            elif role_change and len(details) == 1:
                summary = (f"hat dem Benutzer {q(user['display_name'])} die Rolle {q(role['name'])} zugewiesen")
            else:
                summary = f"hat den Benutzer {q(user['display_name'])} geändert"
            audit(conn, "update", "user", summary, entity_id=uid, entity_name=user["username"],
                  details={"changes": details})
    return jsonify(authm.serialize_user(authm.load_user(conn, uid)))


@bp.post("/<int:uid>/password")
@require("users.manage")
def reset_password(uid: int):
    conn = dbm.get_db()
    user = _load(conn, uid)
    perms.require_covers(conn, user["role_id"], user["role_is_admin"], ADMIN_ACCOUNT_MSG)
    data = body()
    settings = appsettings.get_settings(conn)
    password = data.get("password")
    generated = None
    if password in (None, ""):
        password = generated = generate_password(max(12, settings["password_min_length"]))
    elif not isinstance(password, str) or len(password) < settings["password_min_length"]:
        raise ApiError(422, "validation_error", "Bitte die markierten Eingaben prüfen.", fields={
            "password": f"Das Passwort muss mindestens {settings['password_min_length']} Zeichen haben."})
    pw_hash = hash_password(password)  # außerhalb der Transaktion: Hashen hält sonst die Schreibsperre
    with dbm.transaction(conn):
        user = _load_covered(conn, uid)
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, "
                     "locked_until = NULL, session_version = session_version + 1, updated_at = ? WHERE id = ?",
                     (pw_hash, timeutil.now_iso(), uid))
        audit(conn, "password_reset", "user", f"hat das Passwort des Benutzers {q(user['display_name'])} "
                                              "zurückgesetzt", entity_id=uid, entity_name=user["username"])
    return jsonify({"generated_password": generated})


@bp.post("/<int:uid>/unlock")
@require("users.manage")
def unlock_user(uid: int):
    conn = dbm.get_db()
    user = _load(conn, uid)
    # sonst hebt users.manage die Sperre beim Raten des Passworts eines mächtigeren Kontos immer wieder auf
    perms.require_covers(conn, user["role_id"], user["role_is_admin"], ADMIN_ACCOUNT_MSG)
    with dbm.transaction(conn):
        user = _load_covered(conn, uid)
        conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
                     (timeutil.now_iso(), uid))
        audit(conn, "unlock", "user", f"hat den Benutzer {q(user['display_name'])} entsperrt",
              entity_id=uid, entity_name=user["username"])
    return jsonify(authm.serialize_user(authm.load_user(conn, uid)))


@bp.delete("/<int:uid>")
@require("users.manage")
def delete_user(uid: int):
    conn = dbm.get_db()
    me = authm.current_user()
    user = _load(conn, uid)
    if uid == me["id"]:
        raise ApiError(403, "self_protection", "Das eigene Konto kann nicht gelöscht werden.")
    perms.require_covers(conn, user["role_id"], user["role_is_admin"], ADMIN_ACCOUNT_MSG)
    with dbm.transaction(conn):
        user = _load_covered(conn, uid)
        if _is_active_admin(user):
            _require_admin_left(conn, uid)
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        audit(conn, "delete", "user", f"hat den Benutzer {q(user['display_name'])} ({user['username']}) gelöscht",
              entity_id=uid, entity_name=user["username"])
    return ok()
