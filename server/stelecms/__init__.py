"""Stele CMS – Flask-Anwendung (SPEC §2).

`create_app(config)` baut die Anwendung: Datenordner, Datenbank (Migrationen + Seed), Blueprints,
Sicherheits-Header, statische Auslieferung von Admin-SPA, Player und gemeinsamen Dateien sowie
(optional) die Hintergrund-Threads für Jobs und Monitoring.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from flask import Flask, g, request

from . import auth, cache, config, db as dbm, errors, security, timeutil
from .api import register_blueprints
from .web import register_web

log = logging.getLogger("stelecms")

# Hartes Limit für Anfragen ohne Datei-Upload (JSON, Formulare)
DEFAULT_MAX_BODY = 16 * 1024 * 1024
SCREENSHOT_MAX_BODY = 12 * 1024 * 1024
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def create_app(overrides: dict | None = None) -> Flask:
    cfg = config.load_config(overrides)
    config.ensure_dirs(cfg)

    app = Flask("stelecms", static_folder=None)
    app.config.update(cfg)
    app.config.update(
        SECRET_KEY=config.load_secret_key(cfg),
        SESSION_COOKIE_NAME="stelecms_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(cfg.get("HTTPS", False)),
        MAX_CONTENT_LENGTH=None,          # je Anfrage in before_request gesetzt
        JSON_SORT_KEYS=False,
        PREFERRED_URL_SCHEME="https" if cfg.get("HTTPS") else "http",
    )
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    app.json.mimetype = "application/json; charset=utf-8"
    app.extensions["stelecms_started_at"] = timeutil.now_iso()
    app.extensions["stelecms_started_mono"] = time.monotonic()
    app.extensions["stelecms_limiters"] = {
        "login": security.RateLimiter(20, 300),      # §7.2: max. 20 Versuche / 5 min je IP
        "pairing": security.RateLimiter(10, 60),     # §9.8: max. 10 Kopplungscodes / min je IP
        "pairing_poll": security.RateLimiter(60, 60),  # Statusabfrage: Player fragt alle 3 s (20 / min)
    }

    # ------------------------------------------------------------ Datenbank
    conn = dbm.connect(app.config["DB_PATH"])
    try:
        is_new = dbm.migrate(conn)
        if is_new:
            from . import seed
            with app.app_context():
                seed.seed(app, conn, demo=bool(app.config["SEED_DEMO"]))
    finally:
        conn.close()
    cache.bump(app.config["DB_PATH"])   # Seed/Migration schrieb ohne Request

    # ------------------------------------------------------------ Request-Ablauf
    app.teardown_appcontext(dbm.close_db)

    @app.before_request
    def _before():
        path = request.path
        g.request_started = time.monotonic()
        _limit_body(app, path)
        if path.startswith("/api/player/") or path.startswith("/api/agent/"):
            return None
        if path.startswith("/api/") and request.method in WRITE_METHODS:
            g.cache_bypass = True   # eigene Änderung nie aus dem Cache sehen; _after verwirft ihn
        if path.startswith("/api/"):
            auth.load_user_from_session()
            security.check_csrf()
        elif path.startswith("/media/"):
            auth.load_user_from_session(extend=False)
        return None

    @app.after_request
    def _after(resp):
        if g.get("cache_bypass"):
            cache.bump(app.config["DB_PATH"])
        return security.apply_security_headers(resp)

    errors.register_error_handlers(app)
    register_blueprints(app)
    register_web(app)

    # ------------------------------------------------------------ Hintergrund
    if app.config["BACKGROUND"]:
        from . import jobs, monitor
        jobs.start_worker(app)
        monitor.start_monitor(app)
    return app


def _limit_body(app: Flask, path: str) -> None:
    """Größenlimit je Anfrage: Uploads nach Einstellung, sonst 16 MB."""
    if path == "/api/contents/upload":
        # Die Grenze je Datei prüft die Upload-Route; hier nur eine grobe Obergrenze gegen Missbrauch.
        from . import appsettings
        conn = dbm.get_db()
        mb = appsettings.get_settings(conn)["upload_max_mb"]
        request.max_content_length = (mb * 50 + 1) * 1024 * 1024
    elif path == "/api/agent/screenshot":
        request.max_content_length = SCREENSHOT_MAX_BODY
    else:
        request.max_content_length = DEFAULT_MAX_BODY


def data_path(app: Flask, *parts) -> Path:
    return Path(app.config["DATA_DIR"]).joinpath(*parts)
