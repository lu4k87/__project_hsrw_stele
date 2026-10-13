"""Konfiguration: Umgebungsvariablen, Pfade, Versionsnummer (SPEC §2)."""
from __future__ import annotations

import os
import ipaddress
import secrets
from pathlib import Path

APP_NAME = "Stele CMS"
APP_VERSION = "1.0.0"

PACKAGE_DIR = Path(__file__).resolve().parent
SERVER_DIR = PACKAGE_DIR.parent
REPO_DIR = SERVER_DIR.parent
WEB_DIR = REPO_DIR / "web"


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def load_config(overrides: dict | None = None) -> dict:
    """Liest die Umgebungsvariablen und ergänzt sie um explizite Überschreibungen."""
    overrides = dict(overrides or {})
    data_dir = Path(overrides.pop("DATA_DIR", None) or os.environ.get("STELECMS_DATA") or (REPO_DIR / "data"))
    cfg = {
        "HOST": os.environ.get("STELECMS_HOST", "127.0.0.1"),
        "PORT": int(os.environ.get("STELECMS_PORT", "8090")),
        "DATA_DIR": data_dir,
        "SECRET_KEY": os.environ.get("STELECMS_SECRET_KEY") or None,
        "BACKGROUND": _env_flag("STELECMS_BACKGROUND"),
        "SEED_DEMO": _env_flag("STELECMS_SEED_DEMO", "0"),
        "HTTPS": _env_flag("STELECMS_HTTPS", "0"),
        "ALLOW_INSECURE_LAN": _env_flag("STELECMS_ALLOW_INSECURE_LAN", "0"),
        "WEB_DIR": WEB_DIR,
        # Passwort-Hash-Verfahren (werkzeug); Tests nutzen ein schnelles Verfahren.
        "PASSWORD_HASH_METHOD": os.environ.get("STELECMS_PASSWORD_HASH", "scrypt"),
        "TESTING": False,
    }
    cfg.update(overrides)
    cfg["DATA_DIR"] = Path(cfg["DATA_DIR"]).resolve()
    cfg["WEB_DIR"] = Path(cfg["WEB_DIR"]).resolve()
    d = cfg["DATA_DIR"]
    cfg.setdefault("DB_PATH", d / "cms.db")
    cfg.setdefault("MEDIA_DIR", d / "media")
    cfg.setdefault("SCREENSHOT_DIR", d / "screenshots")
    cfg.setdefault("BACKUP_DIR", d / "backups")
    return cfg


def ensure_dirs(cfg: dict) -> None:
    for key in ("DATA_DIR", "MEDIA_DIR", "SCREENSHOT_DIR", "BACKUP_DIR"):
        path = Path(cfg[key])
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
    (Path(cfg["MEDIA_DIR"]) / ".tmp").mkdir(exist_ok=True)


def load_secret_key(cfg: dict) -> str:
    """Geheimschlüssel aus Umgebung oder data/secret_key (wird bei Bedarf erzeugt)."""
    if cfg.get("SECRET_KEY"):
        return cfg["SECRET_KEY"]
    path = Path(cfg["DATA_DIR"]) / "secret_key"
    if path.exists():
        try:
            path.chmod(0o600)
        except OSError:
            pass
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
