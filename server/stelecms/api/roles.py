"""Rollen und Rechte-Katalog (SPEC §6, §7.3)."""
from __future__ import annotations

from flask import Blueprint, jsonify

from .. import auth as authm
from .. import db as dbm
from .. import permissions as perms
from .. import timeutil
from ..audit import audit, q
from ..errors import ApiError, conflict, forbidden
from ..permissions import require
from ..validation import Validator, body, is_int
from .common import get_or_404, list_response, ok

bp = Blueprint("api_roles", __name__, url_prefix="/api")

ROLE_LOCKED_MSG = "Die Rolle Administrator hat immer alle Rechte und kann weder umbenannt noch gelöscht werden."
ADMIN_ROLE_MSG = "Die Rolle Administrator kann nur eine Administratorin oder ein Administrator ändern."
STALE_MSG = "Die Rechte der Rolle wurden inzwischen geändert. Bitte neu laden und erneut speichern."


def serialize_role(conn, r: dict) -> dict:
    keys = perms.sort_keys(authm.role_permission_keys(conn, r["id"]))
    is_admin = bool(r["is_admin"])
    return {
        "id": r["id"], "name": r["name"], "description": r["description"], "is_admin": is_admin,
        "permissions": perms.ALL_KEYS[:] if is_admin else keys,
        "effective_permissions": perms.sort_keys(perms.effective(keys, is_admin)),
        "user_count": dbm.scalar(conn, "SELECT COUNT(*) FROM users WHERE role_id = ?", (r["id"],)),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def _permission_list(v: Validator, value) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(k, str) for k in value):
        v.error("permissions", "Rechte bitte als Liste angeben.")
        return None
    unknown = [k for k in value if not perms.is_valid(k)]
    if unknown:
        v.error("permissions", "Unbekannte Rechte: " + ", ".join(unknown))
        return None
    return perms.sort_keys(value)


def _check_grantable(keys: list[str], old_keys: list[str]) -> None:
    """Nur Rechte vergeben, die das eigene Konto selbst hat (Administrator: alle). Damit lässt sich auch die
    eigene Rolle nicht erweitern, denn deren Rechte sind genau die eigenen."""
    if authm.current_user()["role_is_admin"]:
        return
    added = set(perms.effective(keys)) - set(perms.effective(old_keys))
    missing = perms.sort_keys(added - authm.current_permissions())
    if missing:
        raise forbidden("Es lassen sich nur Rechte vergeben, die das eigene Konto selbst hat. Fehlend: "
                        + ", ".join(missing))


def _set_permissions(conn, role_id: int, keys: list[str]) -> None:
    conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
    conn.executemany("INSERT INTO role_permissions(role_id, permission) VALUES (?, ?)", [(role_id, k) for k in keys])


@bp.get("/permissions")
def get_permissions():
    authm.ensure_authenticated()
    return jsonify({"groups": perms.catalog_groups()})


@bp.get("/roles")
@require("users.view", "roles.manage", any_of=True)
def list_roles():
    conn = dbm.get_db()
    roles = dbm.rows(conn, "SELECT * FROM roles ORDER BY is_admin DESC, name COLLATE NOCASE")
    return list_response([serialize_role(conn, r) for r in roles])


@bp.post("/roles")
@require("roles.manage")
def create_role():
    conn = dbm.get_db()
    data = body()
    v = Validator(data)
    name = v.text("name", required=True, max_len=60, empty_msg="Bitte einen Namen für die Rolle eingeben.")
    if name and dbm.scalar(conn, "SELECT 1 FROM roles WHERE name = ?", (name,)):
        v.error("name", "Eine Rolle mit diesem Namen gibt es bereits.")
    description = v.text("description", max_len=300, default=None, multiline=True)
    keys = _permission_list(v, data.get("permissions"))
    source = None
    if data.get("copy_from") is not None:
        if not is_int(data["copy_from"]) or \
                (source := dbm.row(conn, "SELECT * FROM roles WHERE id = ?", (data["copy_from"],))) is None:
            v.error("copy_from", "Die Vorlage-Rolle gibt es nicht.")
    v.done()
    if keys is None:
        if source is not None:
            keys = perms.ALL_KEYS[:] if source["is_admin"] else authm.role_permission_keys(conn, source["id"])
        else:
            keys = []
    _check_grantable(keys, [])
    if description is None:
        description = source["description"] if source else ""
    now = timeutil.now_iso()
    with dbm.transaction(conn):
        rid = dbm.insert(conn, "roles", {"name": name, "description": description, "is_admin": 0,
                                         "created_at": now, "updated_at": now})
        _set_permissions(conn, rid, perms.sort_keys(keys))
        audit(conn, "create", "role", f"hat die Rolle {q(name)} angelegt"
              + (f" (Kopie von {q(source['name'])})" if source else ""), entity_id=rid, entity_name=name,
              details={"permissions": perms.sort_keys(keys)})
    return jsonify(serialize_role(conn, get_or_404(conn, "roles", rid))), 201


@bp.patch("/roles/<int:rid>")
@require("roles.manage")
def update_role(rid: int):
    conn = dbm.get_db()
    role = get_or_404(conn, "roles", rid)
    data = body()
    v = Validator(data)
    name = v.text("name", max_len=60)
    if "name" in data and not name:
        v.error("name", "Bitte einen Namen für die Rolle eingeben.")
    if name and name.lower() != role["name"].lower() and \
            dbm.scalar(conn, "SELECT 1 FROM roles WHERE name = ? AND id != ?", (name, rid)):
        v.error("name", "Eine Rolle mit diesem Namen gibt es bereits.")
    description = v.text("description", max_len=300, multiline=True)
    keys = _permission_list(v, data.get("permissions")) if "permissions" in data else None
    v.done()
    old_keys = perms.sort_keys(authm.role_permission_keys(conn, rid))
    if role["is_admin"]:
        if (name and name != role["name"]) or (keys is not None and keys != perms.ALL_KEYS):
            raise ApiError(403, "role_locked", ROLE_LOCKED_MSG)
        keys = None
    perms.require_covers(conn, rid, role["is_admin"], ADMIN_ROLE_MSG)
    if keys is not None:
        _check_grantable(keys, old_keys)
    changes, details = {}, {}
    if name and name != role["name"]:
        changes["name"] = name
        details["name"] = [role["name"], name]
    if description is not None and description != role["description"]:
        changes["description"] = description
        details["description"] = [role["description"], description]
    perm_changed = keys is not None and keys != old_keys
    if perm_changed:
        details["permissions"] = {"added": [k for k in keys if k not in old_keys],
                                  "removed": [k for k in old_keys if k not in keys]}
    if changes or perm_changed:
        with dbm.transaction(conn):
            # Prüfungen gegen den Stand in der Schreib-Transaktion wiederholen (TOCTOU)
            if perms.sort_keys(authm.role_permission_keys(conn, rid)) != old_keys:
                raise conflict(STALE_MSG, "stale")
            perms.require_covers(conn, rid, role["is_admin"], ADMIN_ROLE_MSG)
            changes["updated_at"] = timeutil.now_iso()
            dbm.update(conn, "roles", rid, changes)
            if perm_changed:
                _set_permissions(conn, rid, keys)
            label = name or role["name"]
            summary = (f"hat die Rechte der Rolle {q(label)} geändert" if perm_changed and len(details) == 1
                       else f"hat die Rolle {q(label)} geändert")
            audit(conn, "update", "role", summary, entity_id=rid, entity_name=label, details={"changes": details})
    return jsonify(serialize_role(conn, get_or_404(conn, "roles", rid)))


@bp.delete("/roles/<int:rid>")
@require("roles.manage")
def delete_role(rid: int):
    conn = dbm.get_db()
    role = get_or_404(conn, "roles", rid)
    if role["is_admin"]:
        raise ApiError(403, "role_locked", ROLE_LOCKED_MSG)
    perms.require_covers(conn, rid, False, ADMIN_ROLE_MSG)
    with dbm.transaction(conn):
        perms.require_covers(conn, rid, False, ADMIN_ROLE_MSG)  # Rechte frisch (TOCTOU)
        count = dbm.scalar(conn, "SELECT COUNT(*) FROM users WHERE role_id = ?", (rid,))
        if count:
            raise conflict(f"Die Rolle wird noch von {count} Benutzer{'n' if count != 1 else ''} verwendet. "
                           "Bitte diesen zuerst eine andere Rolle zuweisen.", "role_in_use", {"user_count": count})
        conn.execute("DELETE FROM roles WHERE id = ?", (rid,))
        audit(conn, "delete", "role", f"hat die Rolle {q(role['name'])} gelöscht", entity_id=rid,
              entity_name=role["name"])
    return ok()
