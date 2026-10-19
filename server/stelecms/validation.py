"""Kleine Validierungshelfer mit feldgenauen deutschen Meldungen."""
from __future__ import annotations

import math
import re
from urllib.parse import urlparse

from flask import request

from .errors import ApiError, validation
from .timeutil import is_date, is_hhmm, is_local_dt

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")


def body() -> dict:
    """JSON-Body als dict; leerer Body → {}; ungültiges JSON → 422."""
    if not request.get_data(cache=True):
        return {}
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ApiError(422, "validation_error", "Ungültige Anfrage: Es wird ein JSON-Objekt erwartet.")
    return data


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value) -> bool:
    """Endliche Zahl ohne bool; NaN/Infinity (lässt der JSON-Parser durch) bestehen keine Bereichsprüfung."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))


def is_color(value) -> bool:
    return isinstance(value, str) and bool(COLOR_RE.match(value))


def is_http_url(value) -> bool:
    if not isinstance(value, str) or len(value) > 2000:
        return False
    try:
        p = urlparse(value.strip())
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


class Validator:
    """Sammelt Feldfehler; `done()` wirft 422 mit allen Meldungen."""

    def __init__(self, data: dict | None, prefix: str = ""):
        self.data = data if isinstance(data, dict) else {}
        self.errors: dict[str, str] = {}
        self.prefix = prefix

    def _k(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def has(self, key: str) -> bool:
        return key in self.data

    def error(self, key: str, message: str) -> None:
        self.errors.setdefault(self._k(key), message)

    def text(self, key: str, *, required: bool = False, min_len: int = 0, max_len: int = 200,
             default=None, empty_msg: str | None = None, strip: bool = True, multiline: bool = False):
        if key not in self.data or self.data[key] is None:
            if required:
                self.error(key, empty_msg or "Bitte ausfüllen.")
            return default
        value = self.data[key]
        if not isinstance(value, str):
            self.error(key, "Bitte einen Text eingeben.")
            return default
        if strip:
            value = value.strip()
        if not multiline:
            value = value.replace("\r", " ").replace("\n", " ")
        if required and not value:
            self.error(key, empty_msg or "Bitte ausfüllen.")
            return default
        if len(value) < min_len and value:
            self.error(key, f"Mindestens {min_len} Zeichen.")
        elif len(value) > max_len:
            self.error(key, f"Höchstens {max_len} Zeichen.")
        return value

    def integer(self, key: str, *, required: bool = False, min_value: int | None = None,
                max_value: int | None = None, default=None, allow_none: bool = False):
        if key not in self.data or (self.data[key] is None and not allow_none):
            if required:
                self.error(key, "Bitte eine Zahl eingeben.")
            return default
        value = self.data[key]
        if value is None and allow_none:
            return None
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not is_int(value):
            self.error(key, "Bitte eine ganze Zahl eingeben.")
            return default
        return self._range(key, value, min_value, max_value, default)

    def number(self, key: str, *, required: bool = False, min_value=None, max_value=None, default=None,
               allow_none: bool = False):
        if key not in self.data or (self.data[key] is None and not allow_none):
            if required:
                self.error(key, "Bitte eine Zahl eingeben.")
            return default
        value = self.data[key]
        if value is None and allow_none:
            return None
        if not is_number(value):
            self.error(key, "Bitte eine Zahl eingeben.")
            return default
        return self._range(key, value, min_value, max_value, default)

    def _range(self, key, value, lo, hi, default):
        if lo is not None and value < lo or hi is not None and value > hi:
            if lo is not None and hi is not None:
                self.error(key, f"Erlaubt sind Werte von {_fmt(lo)} bis {_fmt(hi)}.")
            elif lo is not None:
                self.error(key, f"Der Wert muss mindestens {_fmt(lo)} sein.")
            else:
                self.error(key, f"Der Wert darf höchstens {_fmt(hi)} sein.")
            return default
        return value

    def boolean(self, key: str, *, default=None):
        if key not in self.data or self.data[key] is None:
            return default
        value = self.data[key]
        if not isinstance(value, bool):
            self.error(key, "Bitte ein- oder ausschalten (true/false).")
            return default
        return value

    def choice(self, key: str, choices, *, required: bool = False, default=None, allow_none: bool = False):
        if key not in self.data or (self.data[key] is None and not allow_none):
            if required:
                self.error(key, "Bitte eine Option wählen.")
            return default
        value = self.data[key]
        if value is None and allow_none:
            return None
        if value not in choices:
            self.error(key, "Ungültige Auswahl. Erlaubt: " + ", ".join(str(c) for c in choices) + ".")
            return default
        return value

    def color(self, key: str, *, default=None):
        if key not in self.data or self.data[key] is None:
            return default
        value = self.data[key]
        if not is_color(value):
            self.error(key, "Bitte eine Farbe im Format #RRGGBB angeben.")
            return default
        return value.upper()

    def time(self, key: str, *, required: bool = False, allow_24: bool = False, default=None,
             allow_empty: bool = False):
        if key not in self.data or self.data[key] is None:
            if required:
                self.error(key, "Bitte eine Uhrzeit (HH:MM) eingeben.")
            return default
        value = self.data[key]
        if allow_empty and value == "":
            return ""
        if not is_hhmm(value, allow_24=allow_24):
            self.error(key, "Bitte eine Uhrzeit im Format HH:MM eingeben (z. B. 08:30).")
            return default
        return value

    def date(self, key: str, *, default=None):
        if key not in self.data or self.data[key] in (None, ""):
            return default
        value = self.data[key]
        if not is_date(value):
            self.error(key, "Bitte ein Datum im Format JJJJ-MM-TT eingeben.")
            return default
        return value

    def local_datetime(self, key: str, *, default=None):
        if key not in self.data or self.data[key] in (None, ""):
            return default
        value = self.data[key]
        if isinstance(value, str) and len(value) == 16 and is_local_dt(value):
            return value
        if isinstance(value, str) and len(value) == 19 and is_local_dt(value[:16]) and value[16:] == ":00":
            return value[:16]
        self.error(key, "Bitte Datum und Uhrzeit im Format JJJJ-MM-TTTHH:MM angeben.")
        return default

    def url(self, key: str, *, required: bool = False, default=None, allow_empty: bool = False):
        if key not in self.data or self.data[key] is None:
            if required:
                self.error(key, "Bitte eine Adresse (https://…) eingeben.")
            return default
        value = self.data[key]
        if allow_empty and value == "":
            return ""
        if not is_http_url(value):
            self.error(key, "Bitte eine gültige Adresse eingeben, die mit http:// oder https:// beginnt.")
            return default
        return value.strip()

    def email(self, key: str, *, default=None):
        value = self.text(key, max_len=120, default=default)
        if value and not EMAIL_RE.match(value):
            self.error(key, "Bitte eine gültige E-Mail-Adresse eingeben (z. B. name@beispiel.de).")
            return default
        return value

    def string_list(self, key: str, *, max_items: int = 50, max_len: int = 200, default=None,
                    allow_empty_items: bool = False):
        if key not in self.data or self.data[key] is None:
            return default
        value = self.data[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.error(key, "Bitte eine Liste von Texten angeben.")
            return default
        items = [v.strip() for v in value]
        if not allow_empty_items:
            items = [v for v in items if v]
        if len(items) > max_items:
            self.error(key, f"Höchstens {max_items} Einträge.")
            return default
        for v in items:
            if len(v) > max_len:
                self.error(key, f"Jeder Eintrag darf höchstens {max_len} Zeichen haben.")
                return default
        return items

    def merge(self, other: "Validator") -> None:
        for k, v in other.errors.items():
            self.errors.setdefault(k, v)

    def done(self, message: str | None = None) -> None:
        if self.errors:
            raise validation(self.errors, message)


def _fmt(n) -> str:
    if isinstance(n, float):
        return f"{n:g}".replace(".", ",")
    return str(n)


def parse_tags(value, v: Validator, key: str = "tags"):
    """Tags: Liste von Texten, getrimmt, ohne Doppelte (Groß-/Kleinschreibung egal)."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        v.error(key, "Tags bitte als Liste von Texten angeben.")
        return None
    seen, out = set(), []
    for t in value:
        t = " ".join(t.split())
        if not t:
            continue
        if len(t) > 40:
            v.error(key, "Ein Tag darf höchstens 40 Zeichen haben.")
            return None
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    if len(out) > 20:
        v.error(key, "Höchstens 20 Tags.")
        return None
    return out
