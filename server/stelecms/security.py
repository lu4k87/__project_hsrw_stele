"""Passwort-Hash, CSRF, Sicherheits-Header, Rate-Limit."""
from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import socket
import string
import threading
import time
import urllib.parse
import urllib.request
from collections import deque

from flask import current_app, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .errors import ApiError

# ------------------------------------------------------------ Passwörter

_DUMMY_HASH: dict[str, str] = {}


def hash_password(password: str) -> str:
    method = current_app.config.get("PASSWORD_HASH_METHOD", "scrypt")
    return generate_password_hash(password, method=method)


def check_password(pw_hash: str | None, password: str) -> bool:
    if not pw_hash:
        # Gleicher Rechenaufwand wie bei echten Konten (kein Hinweis, ob der Benutzer existiert)
        method = current_app.config.get("PASSWORD_HASH_METHOD", "scrypt")
        if method not in _DUMMY_HASH:
            _DUMMY_HASH[method] = generate_password_hash(secrets.token_hex(8), method=method)
        check_password_hash(_DUMMY_HASH[method], password or "")
        return False
    try:
        return check_password_hash(pw_hash, password or "")
    except (ValueError, TypeError):
        return False


_PW_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0OoIl1")


def generate_password(length: int = 12) -> str:
    """Zufallspasswort ohne leicht verwechselbare Zeichen, mit Ziffer und Groß-/Kleinbuchstaben."""
    while True:
        pw = "".join(secrets.choice(_PW_ALPHABET) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.islower() for c in pw) and any(c.isupper() for c in pw):
            return pw


# ------------------------------------------------------------------ CSRF

CSRF_EXEMPT_PREFIXES = ("/api/player/", "/api/agent/")
CSRF_EXEMPT_PATHS = ("/api/auth/login", "/api/auth/dev-login")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_required() -> bool:
    if request.method in SAFE_METHODS:
        return False
    path = request.path
    if not path.startswith("/api/"):
        return False
    if path in CSRF_EXEMPT_PATHS or path.startswith(CSRF_EXEMPT_PREFIXES):
        return False
    return True


def check_csrf() -> None:
    """Vergleicht den Header X-CSRF-Token mit dem Token der Sitzung (nur bei angemeldeter Sitzung).

    Ohne Sitzung meldet die Anmeldeprüfung 401 – die kommt fachlich zuerst.
    """
    if not csrf_required():
        return
    expected = session.get("csrf")
    if not expected:
        return
    sent = request.headers.get("X-CSRF-Token", "")
    if not sent or not hmac.compare_digest(str(sent), str(expected)):
        raise ApiError(403, "forbidden", "Sicherheitsprüfung fehlgeschlagen – Seite neu laden.")


# ------------------------------------------------------ Sicherheits-Header

CSP_ADMIN = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
             "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-src 'self' http: https:; "
             "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
CSP_PLAYER = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
              "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-src *; "
              "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")


def apply_security_headers(resp):
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    if current_app.config.get("HTTPS"):
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    path = request.path
    if path.startswith("/media/"):
        # Mediendateien: nur Einbettung auf gleicher Herkunft
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if path.startswith("/player"):
        h.setdefault("Content-Security-Policy", CSP_PLAYER)
    else:
        h.setdefault("Content-Security-Policy", CSP_ADMIN)
    if path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    return resp


# ------------------------------------------------------------ Rate-Limit

class RateLimiter:
    """Gleitendes Zeitfenster je Schlüssel (z. B. IP), threadsicher, nur im Speicher."""

    def __init__(self, max_hits: int, window_s: float):
        self.max_hits = max_hits
        self.window_s = window_s
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        dq = self._hits.setdefault(key, deque())
        while dq and now - dq[0] > self.window_s:
            dq.popleft()
        return dq

    def blocked(self, key: str) -> float:
        """0 = erlaubt, sonst Sekunden bis zum nächsten erlaubten Versuch."""
        now = time.monotonic()
        with self._lock:
            dq = self._prune(key, now)
            if len(dq) >= self.max_hits:
                return max(1.0, self.window_s - (now - dq[0]))
            return 0.0

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now).append(now)

    def check_and_hit(self, key: str) -> float:
        now = time.monotonic()
        with self._lock:
            dq = self._prune(key, now)
            if len(dq) >= self.max_hits:
                return max(1.0, self.window_s - (now - dq[0]))
            dq.append(now)
            if len(self._hits) > 1000:  # alte Schlüssel (IPs) nicht endlos sammeln
                for k in [k for k, d in self._hits.items() if not d or now - d[-1] > self.window_s]:
                    del self._hits[k]
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip() -> str:
    return request.remote_addr or ""


def is_loopback(ip: str | None = None) -> bool:
    ip = ip if ip is not None else client_ip()
    if not ip:
        return False
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    return ip == "::1" or ip.startswith("127.")


# ------------------------------------------------------------------ Ausgehende HTTP-Abrufe (Webseiten-Prüfung, Feeds)

def _private_host_allowed(host: str) -> bool:
    allowed = {item.strip().lower().rstrip('.') for item in
               os.environ.get('STELECMS_OUTBOUND_PRIVATE_HOSTS', '').split(',') if item.strip()}
    return host.lower().rstrip('.') in allowed


def _blocked_ip(addr: str, *, allow_private: bool = False) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved
            or (ip.is_private and not allow_private))


def check_outbound_host(url: str) -> None:
    """Wirft ValueError, wenn die Adresse auf den Server selbst oder Link-Local zeigt.

    ponytail: prüft beim Verbindungsaufbau erneut nur über die Weiterleitungen, nicht gegen DNS-Rebinding
    zwischen Prüfung und Abruf; Ausbau wenn die Prüfung auch fremden Konten offensteht.
    """
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        raise ValueError("Adresse ohne Host.")
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValueError(f"Host nicht auflösbar: {host}") from exc
    allow_private = _private_host_allowed(host)
    if any(_blocked_ip(info[4][0], allow_private=allow_private) for info in infos):
        raise ValueError("Lokale, private oder reservierte Adressen werden nicht abgerufen.")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_outbound_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirects)


def open_outbound(req: urllib.request.Request, timeout: float):
    """urlopen mit Host-Prüfung (auch nach Weiterleitungen)."""
    check_outbound_host(req.full_url)
    return _opener.open(req, timeout=timeout)  # noqa: S310 (Aufrufer prüfen http/https)


def read_limited(resp, max_bytes: int, deadline_s: float) -> bytes:
    """Liest höchstens max_bytes (+1) und bricht nach deadline_s Gesamtzeit ab (tröpfelnde Server)."""
    end = time.monotonic() + deadline_s
    chunks, size = [], 0
    while size <= max_bytes:
        if time.monotonic() > end:
            raise TimeoutError("Abruf dauert zu lange.")
        chunk = resp.read(min(65536, max_bytes + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)
