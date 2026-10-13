"""Erstbefüllung einer neuen Datenbank (SPEC §6.2).

Immer: Standardrollen und das Design „Standard“.
Mit `demo=True` (STELECMS_SEED_DEMO != 0) zusätzlich Demo-Konten und -Inhalte.
"""
from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import appsettings, jobs, media, schemas, timeutil
from . import db as dbm
from . import presentations as pres
from .audit import audit
from .resolve import Resolver
from .security import hash_password

log = logging.getLogger("stelecms.seed")

ROLES = [
    ("Administrator", "Alle Rechte. Diese Rolle kann nicht geändert oder gelöscht werden.", True, []),
    ("Redaktion", "Pflegt Inhalte und Präsentationen, veröffentlicht und steuert die Stelen.", False,
     ["monitoring.view", "content.edit", "content.delete", "presentations.edit", "presentations.publish",
      "presentations.delete", "designs.edit", "touch.edit", "schedule.edit", "steles.control", "audit.view"]),
    ("Autor", "Erstellt Inhalte und Präsentationen und reicht sie zur Freigabe ein.", False,
     ["monitoring.view", "content.edit", "presentations.edit", "schedule.view", "steles.view"]),
    ("Betrachter", "Darf alles ansehen, aber nichts ändern.", False,
     ["monitoring.view", "content.view", "presentations.view", "schedule.view", "steles.view"]),
]

USERS = [
    ("admin", "admin123", "Alex Admin", "Administrator"),
    ("redaktion", "redaktion123", "Rita Redaktion", "Redaktion"),
    ("autor", "autor123", "Arne Autor", "Autor"),
    ("betrachter", "betrachter123", "Bea Betrachter", "Betrachter"),
]

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def seed(app, conn, demo: bool = True) -> None:
    now = timeutil.now_iso()
    with dbm.transaction(conn):
        role_ids = {}
        for name, desc, is_admin, perms in ROLES:
            rid = dbm.insert(conn, "roles", {"name": name, "description": desc, "is_admin": 1 if is_admin else 0,
                                             "created_at": now, "updated_at": now})
            role_ids[name] = rid
            conn.executemany("INSERT INTO role_permissions(role_id, permission) VALUES (?, ?)",
                             [(rid, p) for p in perms])
        user_ids = {}
        if demo or app.config.get("TESTING"):
            for username, password, display, role in USERS:
                user_ids[username] = dbm.insert(conn, "users", {
                    "username": username, "display_name": display, "email": "", "password_hash": hash_password(password),
                    "role_id": role_ids[role], "is_active": 1, "is_demo": 1, "must_change_password": 0,
                    "created_at": now, "updated_at": now})
        owner = user_ids.get("admin")
        design_id = dbm.insert(conn, "designs", {
            "name": "Standard", "config": dbm.jdumps(_standard_design()), "created_by": owner,
            "updated_by": owner, "created_at": now, "updated_at": now})
        appsettings.save_settings(conn, {"default_design_id": design_id,
                                         "dev_login_enabled": bool(demo or app.config.get("TESTING"))})
    log.info("Neue Datenbank: Rollen%s und Design „Standard“ angelegt.",
             ", Demo-Konten" if user_ids else "")
    if demo:
        try:
            _seed_demo(app, conn, user_ids, design_id)
        except Exception:  # noqa: BLE001 – Demo-Inhalte sind optional; Anmeldung muss trotzdem gehen
            log.exception("Demo-Inhalte konnten nicht vollständig angelegt werden.")


def _standard_design() -> dict:
    cfg = schemas.merge_defaults(schemas.DESIGN_CONFIG, {})
    cfg["header"].update({"title": "Willkommen", "subtitle": "Informationen für Besucherinnen und Besucher"})
    cfg["footer"]["ticker_items"] = ["Herzlich willkommen!", "Heute geöffnet von 08:00 bis 18:00 Uhr",
                                     "Aktuelle Veranstaltungen im Touch-Menü"]
    return cfg


# ------------------------------------------------------------------ Demo-Inhalte

def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # ältere Pillow-Versionen
        return ImageFont.load_default()


def _demo_image(path: Path, title: str, subtitle: str, top: tuple, bottom: tuple) -> None:
    w, h = 1080, 1920
    mask = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), bottom), Image.new("RGB", (w, h), top), mask)
    draw = ImageDraw.Draw(img)
    for i in range(6):  # dezente Kreise als Bildelement
        r = 140 + i * 90
        draw.ellipse((w - r - 60 + i * 20, 260 - r // 3, w + r - 60 + i * 20, 260 + r), outline=(255, 255, 255),
                     width=3)
    f1, f2 = _font(110), _font(52)
    y = h // 2 - 120
    for line in title.split("\n"):
        tw = draw.textlength(line, font=f1)
        draw.text(((w - tw) / 2 + 4, y + 4), line, font=f1, fill=(0, 0, 0))
        draw.text(((w - tw) / 2, y), line, font=f1, fill=(255, 255, 255))
        y += 130
    tw = draw.textlength(subtitle, font=f2)
    draw.text(((w - tw) / 2, y + 30), subtitle, font=f2, fill=(240, 240, 240))
    img.save(path, "JPEG", quality=90)


def _demo_video(path: Path) -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        log.warning("ffmpeg fehlt – Demo-Video wird übersprungen.")
        return False
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=25",
           "-t", "8", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-f", "mp4", str(path)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return res.returncode == 0 and path.is_file()


def _import(conn, app, path: Path, name: str, tags: list, user_id: int) -> int | None:
    try:
        cid, job = media.import_file(conn, Path(app.config["MEDIA_DIR"]), path, name, tags=tags, user_id=user_id)
    except media.UnsupportedFile as exc:
        log.warning("Demo-Datei %s abgelehnt: %s", name, exc)
        return None
    if job:
        jobs.enqueue(conn, job, cid)
    return cid


def _text(conn, user_id: int, title: str, data: dict, tags: list) -> int:
    full = schemas.merge_defaults(schemas.TEXT_DATA, data)
    return media.insert_content(conn, ctype="text", title=title, tags=tags, data=full, user_id=user_id)


def _seed_demo(app, conn, users: dict, design_id: int) -> None:
    admin, autor = users["admin"], users["autor"]
    tmp_dir = Path(app.config["MEDIA_DIR"]) / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for title, sub, top, bottom, fname in (
            ("Herzlich\nwillkommen", "Schön, dass Sie da sind", (15, 39, 71), (30, 90, 168), "Willkommen.jpg"),
            ("Unser Haus", "Offen für alle", (47, 111, 78), (12, 50, 36), "Unser Haus.jpg"),
            ("Sommerfest", "Musik · Essen · Spiele", (245, 180, 0), (190, 70, 20), "Sommerfest.jpg")):
        p = tmp_dir / ("seed-" + secrets.token_hex(6))
        _demo_image(p, title, sub, top, bottom)
        cid = _import(conn, app, p, fname, ["Demo"], admin)
        if cid:
            images.append(cid)
    video_id = None
    vp = tmp_dir / ("seed-" + secrets.token_hex(6))
    if _demo_video(vp):
        video_id = _import(conn, app, vp, "Testbild-Video.mp4", ["Demo"], admin)
    vp.unlink(missing_ok=True)
    jobs.process_pending(app.config)  # Video-Poster sofort erzeugen (Job läuft synchron)

    with dbm.transaction(conn):
        welcome = _text(conn, admin, "Herzlich willkommen", {
            "template": "title_text",
            "fields": {"title": "Herzlich willkommen", "subtitle": "Schön, dass Sie hier sind",
                       "body": "Aktuelle Informationen, Veranstaltungen und Öffnungszeiten finden Sie auf "
                               "dieser Stele.\nFür mehr Informationen bitte den Bildschirm antippen."},
            "style": {"align": "center", "size": "l"}}, ["Demo", "Allgemein"])
        event = _text(conn, admin, "Tag der offenen Tür", {
            "template": "event",
            "fields": {"title": "Tag der offenen Tür", "subtitle": "Blick hinter die Kulissen",
                       "body": "Führungen, Mitmach-Aktionen und Gespräche mit dem Team.",
                       "date": "Samstag, 17. Oktober 2026", "time": "10:00 – 16:00 Uhr",
                       "location": "Hauptgebäude, Eingangshalle"},
            "style": {"bg_color": "#5B3E96", "accent_color": "#F5B400"}}, ["Demo", "Veranstaltung"])
        hours = _text(conn, admin, "Öffnungszeiten", {
            "template": "list",
            "fields": {"title": "Öffnungszeiten", "items": ["Montag bis Freitag: 08:00 – 18:00 Uhr",
                                                            "Samstag: 09:00 – 13:00 Uhr",
                                                            "Sonn- und Feiertage: geschlossen"]},
            "style": {"bg_color": "#2F6F4E"}}, ["Demo", "Allgemein"])
        media.insert_content(conn, ctype="web", title="Beispiel-Webseite", tags=["Demo"],
                             data=schemas.merge_defaults(schemas.WEB_DATA, {"url": "https://example.com"}),
                             user_id=admin)
        tiles = [
            {"id": "t-uber", "label": "Über uns", "icon": "info", "color": "#1E5AA8", "image_content_id": None,
             "action": {"type": "content", "content_id": welcome}},
            {"id": "t-zeit", "label": "Öffnungszeiten", "icon": "clock", "color": "#2F6F4E",
             "image_content_id": None, "action": {"type": "content", "content_id": hours}},
            {"id": "t-gal", "label": "Galerie", "icon": "image", "color": "#8A4B08",
             "image_content_id": images[0] if images else None,
             "action": {"type": "gallery", "content_ids": images}},
            {"id": "t-vera", "label": "Veranstaltungen", "icon": "calendar", "color": "#5B3E96",
             "image_content_id": None, "action": {"type": "content", "content_id": event}},
        ]
        if not images:
            tiles = [t for t in tiles if t["action"]["type"] != "gallery"]
        menu_cfg = schemas.merge_defaults(schemas.TOUCH_CONFIG, {"title": "Informationen", "tiles": tiles})
        menu_cfg["tiles"] = tiles
        now = timeutil.now_iso()
        menu_id = dbm.insert(conn, "touch_menus", {"name": "Info-Menü", "config": dbm.jdumps(menu_cfg),
                                                   "created_by": admin, "updated_by": admin,
                                                   "created_at": now, "updated_at": now})

        def presentation(name, desc, items, user, touch=None):
            pid = dbm.insert(conn, "presentations", {
                "name": name, "description": desc,
                "settings": dbm.jdumps(schemas.merge_defaults(schemas.PRESENTATION_SETTINGS, {})),
                "design_id": design_id, "touch_menu_id": touch, "created_by": user, "updated_by": user,
                "created_at": now, "updated_at": now})
            pres.replace_items(conn, pid, [{"content_id": c, "enabled": True, "caption": cap, "options": {}}
                                           for c, cap in items if c])
            return pid

        img = images + [None] * (3 - len(images))
        foyer = presentation("Foyer – Standard", "Begrüßung und allgemeine Informationen im Foyer.",
                             [(welcome, ""), (img[0], ""), (video_id, ""), (img[1], "Unser Haus – offen für alle"),
                              (hours, ""), (event, "")], admin, menu_id)
        evening = presentation("Abendprogramm", "Läuft werktags von 18 bis 22 Uhr.",
                               [(event, ""), (img[1], ""), (hours, "")], admin, menu_id)
        summer = presentation("Sommerfest", "Ankündigung für das Sommerfest (Entwurf).",
                              [(img[2], "Sommerfest – Musik, Essen und Spiele"), (event, "")], autor)
        resolver = Resolver(conn)
        for pid in (foyer, evening):
            pres.publish(conn, dbm.row(conn, "SELECT * FROM presentations WHERE id = ?", (pid,)), resolver, admin)
        summer_hash = resolver.draft_hash(dbm.row(conn, "SELECT * FROM presentations WHERE id = ?", (summer,)))
        conn.execute("UPDATE presentations SET review_state = 'requested', review_note = ?, review_by = ?, "
                     "review_at = ?, review_hash = ? WHERE id = ?",
                     ("Bitte prüfen und veröffentlichen – das Fest ist am letzten Freitag im Juni.", autor, now,
                      summer_hash, summer))
        sid = dbm.insert(conn, "steles", {
            "name": "Stele Foyer", "location": "Eingangshalle", "ip_address": "127.0.0.1", "width": 1080,
            "height": 1920, "player_key": secrets.token_urlsafe(32), "default_presentation_id": foyer,
            "settings": dbm.jdumps(schemas.merge_defaults(schemas.STELE_SETTINGS, {})),
            "created_at": now, "updated_at": now})
        dbm.insert(conn, "schedule_entries", {
            "stele_id": sid, "presentation_id": evening, "label": "Abendprogramm", "days": "[1,2,3,4,5]",
            "start_time": "18:00", "end_time": "22:00", "priority": 0, "enabled": 1,
            "created_at": now, "updated_at": now})
        audit(conn, "create", "settings", "hat die Demo-Inhalte für den Testbetrieb angelegt",
              entity_name="Demo-Inhalte", user=None, username="system")
    log.info("Demo-Inhalte angelegt (%d Bilder, Video: %s).", len(images), "ja" if video_id else "nein")


def log_player_links(app) -> None:
    """Vorhandene Player nennen, ohne ihren geheimen Schlüssel ins Log zu schreiben."""
    conn = dbm.connect(app.config["DB_PATH"])
    try:
        if not appsettings.get_settings(conn)["dev_login_enabled"]:
            return
        host = app.config["HOST"]
        shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        for s in dbm.rows(conn, "SELECT name FROM steles ORDER BY id"):
            log.info("Player %s: http://%s:%s/player/ (Schlüssel nur im Adminbereich)",
                     s["name"], shown, app.config["PORT"])
    finally:
        conn.close()
