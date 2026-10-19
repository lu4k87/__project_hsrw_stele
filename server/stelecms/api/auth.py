"""Anmeldung (SPEC §7.2): Sitzung, Login, Schnellanmeldung, Logout, Passwort, Profil."""
from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, session

from .. import appsettings, timeutil
from .. import auth as authm
from .. import db as dbm
from ..audit import audit
from ..errors import ApiError
from ..permissions import effective
from ..security import check_password, client_ip, hash_password, is_loopback
from ..validation import Validator, body

bp = Blueprint("api_auth", __name__, url_prefix="/api/auth")

INVALID_MSG = "Benutzername oder Passwort ist falsch."


def _limiter():
    return current_app.extensions["stelecms_limiters"]["login"]


@bp.get("/session")
def get_session():
    return jsonify(authm.session_payload(dbm.get_db()))


@bp.post("/login")
def login():
    conn = dbm.get_db()
    data = body()
    ip = client_ip()
    # Prüfen und zählen in einem Schritt (sonst kommen parallele Anfragen alle an der Drossel vorbei, §7.2)
    retry = _limiter().check_and_hit(ip)
    if retry:
        raise ApiError(429, "rate_limited", "Zu viele Anmeldeversuche von dieser Adresse. Bitte in einigen "
                                            "Minuten erneut versuchen.", details={"retry_after_s": int(retry)})
    v = Validator(data)
    username = v.text("username", required=True, max_len=64, empty_msg="Bitte Benutzernamen eingeben.")
    password = data.get("password") if isinstance(data.get("password"), str) else None
    if not password:
        v.error("password", "Bitte Passwort eingeben.")
    v.done()
    settings = appsettings.get_settings(conn)
    user = dbm.row(conn, authm.USER_SELECT + " WHERE u.username = ?", (username,))
    if user and authm.is_locked(user):
        raise authm.account_locked_error(user)
    if not check_password(user["password_hash"] if user else None, password):
        locked_until = None
        with dbm.transaction(conn):
            if user:
                # Zähler atomar in der Schreib-Transaktion: parallele Fehlversuche gehen nicht verloren
                cur = dbm.row(conn, "SELECT failed_logins, locked_until FROM users WHERE id = ?", (user["id"],))
                if authm.is_locked(cur):  # parallel gesperrt
                    locked_until = cur["locked_until"]
                else:
                    if cur["locked_until"]:  # abgelaufene Sperre: Zähler beginnt neu
                        conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?",
                                     (user["id"],))
                    conn.execute("UPDATE users SET failed_logins = failed_logins + 1 WHERE id = ?", (user["id"],))
                    failed = dbm.scalar(conn, "SELECT failed_logins FROM users WHERE id = ?", (user["id"],))
                    if failed >= settings["lockout_attempts"]:
                        locked_until = timeutil.iso(timeutil.utcnow()
                                                    + timedelta(minutes=settings["lockout_minutes"]))
                        conn.execute("UPDATE users SET failed_logins = 0, locked_until = ? WHERE id = ?",
                                     (locked_until, user["id"]))
                audit(conn, "login_failed", "session",
                      "Anmeldung fehlgeschlagen (falsches Passwort)" +
                      (" – Konto vorübergehend gesperrt" if locked_until else ""),
                      entity_id=user["id"], entity_name=user["username"],
                      user={"id": user["id"], "username": user["username"], "display_name": user["display_name"]})
            else:
                audit(conn, "login_failed", "session", f"Anmeldung mit unbekanntem Benutzernamen „{username}“ "
                                                       "fehlgeschlagen", entity_name=username, username=username)
        if locked_until:
            user["locked_until"] = locked_until
            raise authm.account_locked_error(user)
        raise ApiError(401, "invalid_credentials", INVALID_MSG)
    if not user["is_active"]:
        raise ApiError(403, "account_disabled", "Dieses Konto ist deaktiviert. Bitte an die Administration wenden.")
    _finish_login(conn, user, "hat sich angemeldet", check_lock=True)
    return jsonify(authm.session_payload(conn))


@bp.post("/dev-login")
def dev_login():
    conn = dbm.get_db()
    data = body()
    settings = appsettings.get_settings(conn)
    if not settings["dev_login_enabled"] or not is_loopback():
        raise ApiError(403, "forbidden", "Die Schnellanmeldung ist nur im Testbetrieb direkt am Server möglich.")
    v = Validator(data)
    username = v.text("username", required=True, max_len=64, empty_msg="Bitte ein Demo-Konto wählen.")
    v.done()
    user = dbm.row(conn, authm.USER_SELECT + " WHERE u.username = ?", (username,))
    if not user or not user["is_demo"]:
        raise ApiError(403, "forbidden", "Für dieses Konto ist die Schnellanmeldung nicht möglich.")
    if not user["is_active"]:
        raise ApiError(403, "account_disabled", "Dieses Konto ist deaktiviert. Bitte an die Administration wenden.")
    _finish_login(conn, user, "hat sich per Schnellanmeldung (Testbetrieb) angemeldet")
    return jsonify(authm.session_payload(conn))


def _finish_login(conn, user: dict, summary: str, check_lock: bool = False) -> None:
    """check_lock: Sperre in derselben Schreib-Transaktion prüfen wie das Zurücksetzen des Zählers – sonst
    hebt eine richtige Anmeldung eine Sperre auf, die parallele Fehlversuche während der Passwortprüfung
    gesetzt haben."""
    now = timeutil.now_iso()
    locked = None
    with dbm.transaction(conn):
        if check_lock:
            cur = dbm.row(conn, "SELECT locked_until FROM users WHERE id = ?", (user["id"],))
            if authm.is_locked(cur):
                locked = cur
                audit(conn, "login_failed", "session", "Anmeldung abgewiesen (Konto vorübergehend gesperrt)",
                      entity_id=user["id"], entity_name=user["username"],
                      user={"id": user["id"], "username": user["username"], "display_name": user["display_name"]})
        if locked is None:
            conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
                         (now, user["id"]))
            audit(conn, "login", "session", summary, entity_id=user["id"], entity_name=user["username"], user=user)
    if locked is not None:  # erst nach dem Commit: der Protokolleintrag bleibt
        raise authm.account_locked_error(locked)
    authm.start_session(user)
    # Für die Antwort sofort als angemeldet behandeln
    fresh = authm.load_user(conn, user["id"])
    g.user = fresh
    g.perms = frozenset(effective(authm.role_permission_keys(conn, fresh["role_id"]), bool(fresh["role_is_admin"])))
    g.session_expired = False


@bp.post("/logout")
def logout():
    user = authm.ensure_authenticated()
    conn = dbm.get_db()
    with dbm.transaction(conn):
        audit(conn, "logout", "session", "hat sich abgemeldet", entity_id=user["id"], entity_name=user["username"])
    authm.end_session()
    return jsonify({"ok": True})


@bp.post("/password")
def change_password():
    user = authm.ensure_authenticated()
    conn = dbm.get_db()
    data = body()
    settings = appsettings.get_settings(conn)
    v = Validator(data)
    current = data.get("current_password") if isinstance(data.get("current_password"), str) else ""
    new = data.get("new_password") if isinstance(data.get("new_password"), str) else ""
    if not current:
        v.error("current_password", "Bitte das aktuelle Passwort eingeben.")
    elif not check_password(user["password_hash"], current):
        v.error("current_password", "Das aktuelle Passwort ist falsch.")
    if len(new) < settings["password_min_length"]:
        v.error("new_password", f"Das neue Passwort muss mindestens {settings['password_min_length']} Zeichen haben.")
    elif len(new) > 200:
        v.error("new_password", "Höchstens 200 Zeichen.")
    elif current and new == current:
        v.error("new_password", "Das neue Passwort muss sich vom bisherigen unterscheiden.")
    v.done()
    with dbm.transaction(conn):
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, "
                     "session_version = session_version + 1, updated_at = ? WHERE id = ?",
                     (hash_password(new), timeutil.now_iso(), user["id"]))
        audit(conn, "update", "user", "hat das eigene Passwort geändert", entity_id=user["id"],
              entity_name=user["username"])
    fresh = authm.load_user(conn, user["id"])
    session["sv"] = fresh["session_version"]  # diese Sitzung bleibt gültig, andere enden
    g.user = fresh
    return jsonify(authm.session_payload(conn))


@bp.patch("/profile")
def update_profile():
    user = authm.ensure_authenticated()
    conn = dbm.get_db()
    data = body()
    v = Validator(data)
    display_name = v.text("display_name", max_len=80, empty_msg="Bitte einen Anzeigenamen eingeben.")
    if "display_name" in data and not display_name:
        v.error("display_name", "Bitte einen Anzeigenamen eingeben.")
    email = v.email("email")
    v.done()
    changes = {}
    if display_name is not None and display_name != user["display_name"]:
        changes["display_name"] = display_name
    if email is not None and email != user["email"]:
        changes["email"] = email
    if changes:
        with dbm.transaction(conn):
            changes["updated_at"] = timeutil.now_iso()
            dbm.update(conn, "users", user["id"], changes)
            audit(conn, "update", "user", "hat das eigene Profil geändert", entity_id=user["id"],
                  entity_name=user["username"], details={"fields": sorted(k for k in changes if k != "updated_at")})
    g.user = authm.load_user(conn, user["id"])
    return jsonify(authm.session_payload(conn))
