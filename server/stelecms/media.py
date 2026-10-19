"""Mediathek: Upload, Inhaltsprüfung, Varianten (Thumbs, Poster, PDF-Seiten), Ausgabe, Verwendungen."""
from __future__ import annotations

import html
import http.client
import json
import logging
import re
import secrets
import shutil
import socket
import struct
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import db as dbm
from . import cache, schemas, security, timeutil
from .auth import person
from .validation import is_http_url, is_int

log = logging.getLogger("stelecms.media")

ALLOWED_DESC = "JPEG, PNG, WebP, GIF, MP4, WebM, MOV, MKV, PDF"
IMAGE_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"),
                 "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}
VIDEO_CONTAINERS = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mkv": "video/x-matroska"}
BROWSER_VIDEO_CODECS = ("h264", "vp8", "vp9", "av1")
BROWSER_AUDIO_CODECS = ("aac", "opus", "vorbis", "mp3")
DISPLAY_MAX = 2160
THUMB_MAX = 480
PDF_MAX_PAGES = 100
PDF_PAGE_WIDTH = 1080
STAGE_W, STAGE_H = 1080, 1920

Image.MAX_IMAGE_PIXELS = 200_000_000  # große Fotos erlauben, echte „Bomben“ abweisen
_IMAGE_SLOTS = threading.BoundedSemaphore(2)


class UnsupportedFile(Exception):
    """Datei wird abgelehnt; Nachricht ist für Menschen gedacht."""


def tool(name: str) -> str | None:
    return shutil.which(name)


def media_root() -> Path:
    from flask import current_app
    return Path(current_app.config["MEDIA_DIR"])


def new_uid(conn) -> str:
    while True:
        uid = secrets.token_hex(8)
        if dbm.scalar(conn, "SELECT 1 FROM contents WHERE uid = ?", (uid,)) is None:
            return uid


def media_url(uid: str, name: str | None) -> str | None:
    return f"/media/{uid}/{name}" if name else None


# ------------------------------------------------------------ Erkennung

def _run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


def ffprobe(path: Path) -> dict | None:
    exe = tool("ffprobe")
    if not exe:
        return None
    try:
        res = _run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None


def pdfinfo(path: Path) -> dict | None:
    exe = tool("pdfinfo")
    if not exe:
        return None
    try:
        res = _run([exe, str(path)], 30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    info = {}
    for line in res.stdout.decode("utf-8", "replace").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def _matroska_kind(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4096)
    return "webm" if b"webm" in head[:200] else "mkv"


def _mp4_kind(probe: dict) -> str:
    brand = ((probe.get("format") or {}).get("tags") or {}).get("major_brand", "")
    return "mov" if brand.strip() == "qt" else "mp4"


def video_info(path: Path, probe: dict) -> dict:
    """Wesentliche Video-Eigenschaften aus ffprobe."""
    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []
    vstreams = [s for s in streams if s.get("codec_type") == "video"
                and not (s.get("disposition") or {}).get("attached_pic")]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    v = vstreams[0] if vstreams else None
    a = astreams[0] if astreams else None
    fname = fmt.get("format_name", "")
    if "matroska" in fname or "webm" in fname:
        container = _matroska_kind(path)
    elif "mp4" in fname or "mov" in fname:
        container = _mp4_kind(probe)
    else:
        container = ""
    width = int(v.get("width") or 0) if v else 0
    height = int(v.get("height") or 0) if v else 0
    rotation = 0
    if v:
        try:
            rotation = int(float((v.get("tags") or {}).get("rotate", 0)))
        except ValueError:
            rotation = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rotation = int(float(sd["rotation"]))
                except (TypeError, ValueError):
                    pass
    if abs(rotation) % 180 == 90:
        width, height = height, width
    duration = None
    for src in (fmt.get("duration"), v.get("duration") if v else None):
        try:
            duration = float(src)
            break
        except (TypeError, ValueError):
            continue
    return {"container": container, "vcodec": (v or {}).get("codec_name", ""),
            "acodec": (a or {}).get("codec_name") if a else None, "has_video": v is not None,
            "width": width, "height": height, "duration": duration}


def detect(path: Path, original_name: str) -> dict:
    """Erkennt den Dateityp über den Inhalt. Rückgabe {type, ext, mime, ...}; sonst UnsupportedFile."""
    ext = Path(original_name).suffix.lower().lstrip(".") or "unbekannt"
    with open(path, "rb") as f:
        head = f.read(1024)
    if not head:
        raise UnsupportedFile("Die Datei ist leer.")
    # PDF
    if head.startswith(b"%PDF-"):
        info = pdfinfo(path)
        if info is None:
            raise UnsupportedFile("Die PDF-Datei ist beschädigt, passwortgeschützt oder kann nicht gelesen werden.")
        try:
            pages = int(info.get("Pages", "0"))
        except ValueError:
            pages = 0
        if pages < 1:
            raise UnsupportedFile("Die PDF-Datei enthält keine Seiten.")
        return {"type": "pdf", "ext": "pdf", "mime": "application/pdf", "pages": pages}
    # Bilder (Pillow)
    fmt = None
    try:
        with Image.open(path) as im:
            fmt = im.format
            if fmt in IMAGE_FORMATS:
                im.verify()
    except Image.DecompressionBombError:
        raise UnsupportedFile("Das Bild hat zu viele Pixel. Bitte vorher verkleinern.")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        if fmt in IMAGE_FORMATS:
            raise UnsupportedFile("Die Bilddatei ist beschädigt und kann nicht gelesen werden.")
        fmt = None
    if fmt:
        if fmt not in IMAGE_FORMATS:
            raise UnsupportedFile(f"Dateityp nicht unterstützt ({fmt}). Erlaubt: {ALLOWED_DESC}.")
        e, mime = IMAGE_FORMATS[fmt]
        return {"type": "image", "ext": e, "mime": mime}
    # Videos (ffprobe)
    if head.lstrip()[:5].lower() in (b"<?xml", b"<svg ", b"<html", b"<!doc"):
        raise UnsupportedFile(f"Dateityp nicht unterstützt ({ext}). Erlaubt: {ALLOWED_DESC}.")
    probe = ffprobe(path)
    if probe:
        info = video_info(path, probe)
        if info["container"] in VIDEO_CONTAINERS:
            if not info["has_video"]:
                raise UnsupportedFile("Die Datei enthält keine Videospur (nur Ton).")
            return {"type": "video", "ext": info["container"], "mime": VIDEO_CONTAINERS[info["container"]],
                    "video": info}
    raise UnsupportedFile(f"Dateityp nicht unterstützt ({ext}). Erlaubt: {ALLOWED_DESC}.")


# ------------------------------------------------------------ Bilder

def _has_alpha(im: Image.Image) -> bool:
    if im.mode in ("RGBA", "LA", "PA"):
        return True
    return im.mode == "P" and "transparency" in im.info


def make_thumb(src: Image.Image, dest: Path) -> None:
    th = src.copy()
    th.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
    th = th.convert("RGBA" if _has_alpha(th) else "RGB")
    th.save(dest, "WEBP", quality=80, method=4)


def process_image(original: Path, folder: Path) -> dict:
    """Erzeugt display.jpg|png (max. 2160 px, EXIF-Drehung) und thumb.webp. GIF/animiert: Original bleibt."""
    with Image.open(original) as im:
        fmt = im.format
        animated = bool(getattr(im, "is_animated", False))
        im.load()
        oriented = ImageOps.exif_transpose(im) or im
        width, height = oriented.size
        if fmt == "GIF" or animated:
            display = original.name
        else:
            disp = oriented.copy()
            disp.thumbnail((DISPLAY_MAX, DISPLAY_MAX), Image.LANCZOS)
            if _has_alpha(disp):
                display = "display.png"
                disp.convert("RGBA").save(folder / display, "PNG", optimize=True)
            else:
                display = "display.jpg"
                disp.convert("RGB").save(folder / display, "JPEG", quality=88, optimize=True, progressive=True)
        make_thumb(oriented, folder / "thumb.webp")
    return {"width": width, "height": height, "display_file": display}


# ------------------------------------------------------------ Videos

def mp4_faststart(path: Path) -> bool:
    """True, wenn das moov-Atom vor mdat liegt (Wiedergabe startet ohne vollständigen Download)."""
    try:
        with open(path, "rb") as f:
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return False
                size, typ = struct.unpack(">I4s", hdr)
                header = 8
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0]
                    header = 16
                if typ == b"moov":
                    return True
                if typ == b"mdat" or size == 0:
                    return False
                if size < header:
                    return False
                f.seek(size - header, 1)
    except OSError:
        return False


def video_compatible(info: dict) -> bool:
    return (info["container"] in ("mp4", "mov", "webm") and info["vcodec"] in BROWSER_VIDEO_CODECS
            and (info["acodec"] is None or info["acodec"] in BROWSER_AUDIO_CODECS))


def run_ffmpeg_progress(cmd: list[str], duration: float | None, on_progress,
                        timeout: float = 6 * 3600) -> tuple[int, str]:
    """ffmpeg mit `-progress pipe:1`; ruft on_progress(0..100) auf. Rückgabe (returncode, stderr)."""

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err_chunks: list[bytes] = []

    def _drain():
        for chunk in iter(lambda: proc.stderr.read(4096), b""):
            if sum(len(c) for c in err_chunks) < 64_000:
                err_chunks.append(chunk)
    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    # Zeitgrenze unabhängig von der Ausgabe: ein stummes, hängendes ffmpeg blockiert sonst den Job-Thread
    killer = threading.Timer(timeout, proc.kill)
    killer.daemon = True
    killer.start()
    for raw in proc.stdout:
        line = raw.decode("ascii", "replace").strip()
        if duration and (line.startswith("out_time_us=") or line.startswith("out_time_ms=")):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            on_progress(max(0, min(99, int(us / 1_000_000 / duration * 100))))
    proc.wait()
    killer.cancel()
    t.join(timeout=5)
    return proc.returncode, b"".join(err_chunks).decode("utf-8", "replace")[-2000:]


def extract_poster(video: Path, folder: Path, duration: float | None) -> bool:
    exe = tool("ffmpeg")
    if not exe:
        return False
    at = min(1.0, (duration or 3.0) / 3)
    poster = folder / "poster.jpg"
    for ts in (at, 0):
        try:
            res = _run([exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", str(video),
                        "-frames:v", "1", "-q:v", "3", str(poster)], 120)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if res.returncode == 0 and poster.exists() and poster.stat().st_size > 0:
            with Image.open(poster) as im:
                im.load()
                make_thumb(im, folder / "thumb.webp")
            return True
    return False


# ------------------------------------------------------------ Anlegen

def insert_content(conn, *, ctype: str, title: str, tags: list, data: dict, user_id: int | None,
                   status: str = "ready", uid: str | None = None, **cols) -> int:
    now = timeutil.now_iso()
    values = {"uid": uid or new_uid(conn), "type": ctype, "title": title, "tags": dbm.jdumps(tags or []),
              "data": dbm.jdumps(data or {}), "status": status, "created_by": user_id, "updated_by": user_id,
              "created_at": now, "updated_at": now}
    values.update(cols)
    return dbm.insert(conn, "contents", values)


def title_from_filename(name: str) -> str:
    stem = Path(name).stem.strip() or "Unbenannt"
    stem = re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
    return stem[:200]


def safe_file_name(name: str) -> str:
    name = Path(name or "datei").name
    return re.sub(r"[\x00-\x1f/\\]", "_", name)[:200] or "datei"


def import_file(conn, media_dir: Path, tmp_path: Path, original_name: str, *, tags: list, user_id: int | None,
                title: str | None = None) -> tuple[int, str | None]:
    """Übernimmt eine (bereits gespeicherte) Datei in die Mediathek.

    Rückgabe (content_id, job_kind|None). Bilder werden sofort verarbeitet; Video/PDF brauchen einen Job.
    Die Temp-Datei wird verschoben bzw. bei Ablehnung gelöscht.
    """
    try:
        info = detect(tmp_path, original_name)
    except UnsupportedFile:
        tmp_path.unlink(missing_ok=True)
        raise
    uid = new_uid(conn)
    folder = media_dir / uid
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / f"original.{info['ext']}"
    shutil.move(str(tmp_path), original)
    size = original.stat().st_size
    base = {"file_name": safe_file_name(original_name), "mime": info["mime"], "size_bytes": size}
    title = title or title_from_filename(original_name)
    try:
        if info["type"] == "image":
            with _IMAGE_SLOTS:  # große Fotos brauchen beim Dekodieren bis ~1 GB RAM → nicht 16 parallel
                res = process_image(original, folder)
            cid = insert_content(conn, ctype="image", title=title, tags=tags, user_id=user_id, uid=uid,
                                 data={"display_file": res["display_file"], "original_file": original.name},
                                 width=res["width"], height=res["height"], **base)
            return cid, None
        if info["type"] == "video":
            v = info["video"]
            cid = insert_content(conn, ctype="video", title=title, tags=tags, user_id=user_id, uid=uid,
                                 status="processing", progress=0,
                                 data={"codec": v["vcodec"], "audio": v["acodec"] is not None, "transcoded": False,
                                       "compatible": video_compatible(v), "original_file": original.name,
                                       "display_file": original.name},
                                 width=v["width"] or None, height=v["height"] or None, duration_s=v["duration"],
                                 **base)
            return cid, "video"
        cid = insert_content(conn, ctype="pdf", title=title, tags=tags, user_id=user_id, uid=uid,
                             status="processing", progress=0, data={"original_file": original.name},
                             page_count=info["pages"], **base)
        return cid, "pdf"
    except UnsupportedFile:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as exc:  # beschädigte Dateien, die erst beim Verarbeiten auffallen
        shutil.rmtree(folder, ignore_errors=True)
        log.warning("Verarbeitung von %s fehlgeschlagen: %s", original_name, exc)
        raise UnsupportedFile("Die Datei konnte nicht verarbeitet werden. Möglicherweise ist sie beschädigt.")


def delete_files(media_dir: Path, uid: str) -> None:
    if re.fullmatch(r"[0-9a-f]{16}", uid or ""):
        shutil.rmtree(media_dir / uid, ignore_errors=True)


# ------------------------------------------------------------ Ausgabe

def content_urls(r: dict) -> dict:
    data = dbm.jloads(r["data"], {}) if isinstance(r["data"], str) else (r["data"] or {})
    uid, t, ready = r["uid"], r["type"], r["status"] == "ready"
    urls = {"thumb": None, "display": None, "original": None, "poster": None, "pages": None}
    if t in ("text", "web"):
        return urls
    urls["original"] = media_url(uid, data.get("original_file"))
    if t == "image":
        urls["thumb"] = media_url(uid, "thumb.webp")
        urls["display"] = media_url(uid, data.get("display_file") or data.get("original_file"))
    elif t == "video":
        urls["display"] = media_url(uid, data.get("display_file") or data.get("original_file"))
        if ready and data.get("poster_file"):
            urls["poster"] = media_url(uid, data["poster_file"])
            urls["thumb"] = media_url(uid, "thumb.webp")
    elif t == "pdf":
        n = int(r.get("page_count") or 0) if ready else 0
        if ready and n:
            urls["thumb"] = media_url(uid, "thumb.webp")
            urls["pages"] = [media_url(uid, f"pages/p{i:03d}.png") for i in range(1, n + 1)]
            urls["display"] = urls["pages"][0]
    return urls


def content_warnings(r: dict) -> list[dict]:
    data = dbm.jloads(r["data"], {}) if isinstance(r["data"], str) else (r["data"] or {})
    out = []
    w, h = r.get("width"), r.get("height")
    if r["type"] in ("image", "video", "pdf") and w and h:
        if w > h:
            out.append({"code": "landscape",
                        "message": "Querformat: wird auf der Hochformat-Stele angepasst (Rand oder Zuschnitt)."})
        low = (w < 720 or h < 1280) if h >= w else (w * h < 0.4 * STAGE_W * STAGE_H)
        if low:
            out.append({"code": "low_resolution",
                        "message": f"Geringe Auflösung ({w} × {h} px): kann auf der Stele unscharf wirken. "
                                   "Empfohlen sind 1080 × 1920 px."})
    if r["type"] == "video":
        if data.get("transcoded"):
            out.append({"code": "transcoded",
                        "message": "Das Video wurde für die Wiedergabe im Browser umgewandelt (H.264/AAC)."})
        if data.get("compatible") is False and r["status"] == "ready":
            out.append({"code": "incompatible",
                        "message": "Dieses Videoformat spielt der Browser eventuell nicht ab. Die automatische "
                                   "Umwandlung ist ausgeschaltet – bitte als MP4 (H.264) hochladen."})
    if r["type"] == "web":
        ec = (data.get("embed_check") or {})
        if ec.get("embeddable") is False:
            out.append({"code": "not_embeddable",
                        "message": "Die Webseite erlaubt keine Einbettung und bleibt auf der Stele leer. "
                                   "Bitte eine andere Seite wählen oder einen Screenshot als Bild verwenden."})
    if r["status"] == "error":
        out.append({"code": "processing_failed",
                    "message": "Verarbeitung fehlgeschlagen: " + (r.get("status_message") or "unbekannter Fehler")
                               + " Bitte die Datei erneut hochladen."})
    return out


def public_data(r: dict) -> dict:
    """Inhalt-Daten laut §5.4 (interne Dateinamen entfallen)."""
    data = dbm.jloads(r["data"], {}) if isinstance(r["data"], str) else dict(r["data"] or {})
    t = r["type"]
    if t == "text":
        return schemas.merge_defaults(schemas.TEXT_DATA, data)
    if t == "web":
        return schemas.merge_defaults(schemas.WEB_DATA, data)
    if t == "video":
        return {k: data.get(k, dv) for k, dv in schemas.VIDEO_DATA.items()}
    if t == "pdf":
        return {k: v for k, v in data.items() if k == "pages_total"}
    return {}


def serialize_content(conn, r: dict, usage_index: "UsageIndex | None" = None, with_usages: bool = False) -> dict:
    has_file = r["type"] in ("image", "video", "pdf")
    out = {
        "id": r["id"], "uid": r["uid"], "type": r["type"], "title": r["title"],
        "tags": dbm.jloads(r["tags"], []), "data": public_data(r),
        "status": r["status"], "status_message": r["status_message"], "progress": r["progress"],
        "file": ({"name": r["file_name"], "mime": r["mime"], "size_bytes": r["size_bytes"], "width": r["width"],
                  "height": r["height"], "duration_s": r["duration_s"], "page_count": r["page_count"]}
                 if has_file else None),
        "urls": content_urls(r),
        "warnings": content_warnings(r),
        "usage_count": 0,
        "created_by": person(conn, r["created_by"]), "updated_by": person(conn, r["updated_by"]),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }
    if usage_index is not None or with_usages:
        idx = usage_index or UsageIndex(conn)
        usages = idx.usages(r["id"])
        out["usage_count"] = len(usages)
        if with_usages:
            out["usages"] = usages
    return out


def content_brief(r: dict | None) -> dict | None:
    """Kurzform für Folien/Kacheln: {id, type, title, status, thumb_url}."""
    if not r:
        return None
    return {"id": r["id"], "type": r["type"], "title": r["title"], "status": r["status"],
            "thumb_url": content_urls(r)["thumb"]}


# ------------------------------------------------------------ Verwendungen

def snapshot_content_ids(obj) -> set[int]:
    """Alle Inhalts-IDs in einem aufgelösten Snapshot (content_id, *_content_id, content_ids)."""
    ids: set[int] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if (k == "content_id" or k.endswith("_content_id")) and is_int(v):
                    ids.add(v)
                elif k == "content_ids" and isinstance(v, list):
                    ids.update(x for x in v if is_int(x))
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(obj)
    return ids


class UsageIndex:
    """Wo wird welcher Inhalt verwendet? (einmal je Request aufgebaut)"""

    def __init__(self, conn):
        self._map: dict[int, dict[tuple, dict]] = {}
        for r in conn.execute("SELECT DISTINCT pi.content_id, p.id, p.name FROM presentation_items pi "
                              "JOIN presentations p ON p.id = pi.presentation_id").fetchall():
            self._add(r["content_id"], "presentation", r["id"], r["name"], published=False, in_draft=True)
        for r in conn.execute("SELECT id, name, published_snapshot FROM presentations "
                              "WHERE published_snapshot IS NOT NULL").fetchall():
            for cid in snapshot_content_ids(dbm.jloads(r["published_snapshot"], {})):
                self._add(cid, "presentation", r["id"], r["name"], published=True)
        for r in conn.execute("SELECT id, name, config FROM designs").fetchall():
            cfg = dbm.jloads(r["config"], {})
            logo = ((cfg.get("header") or {}).get("logo_content_id"))
            if is_int(logo):
                self._add(logo, "design", r["id"], r["name"])
        for r in conn.execute("SELECT id, title, data FROM contents WHERE type = 'text'").fetchall():
            data = dbm.jloads(r["data"], {})
            for cid in ((data.get("fields") or {}).get("image_content_id"),
                        (data.get("style") or {}).get("bg_image_content_id")):
                if is_int(cid):
                    self._add(cid, "content", r["id"], r["title"])
        for r in conn.execute("SELECT id, name, config FROM touch_menus").fetchall():
            for cid in schemas.touch_content_ids(dbm.jloads(r["config"], {})):
                self._add(cid, "touch_menu", r["id"], r["name"])

    @classmethod
    def cached(cls, conn) -> "UsageIndex":
        """Für Lese-Anfragen (Mediathek): verworfen bei jeder Änderung. Lösch-Prüfungen bauen ihn frisch."""
        return cache.get(("usage_index",), lambda: cls(conn))

    def _add(self, cid, typ, oid, name, published=False, in_draft=False):
        entry = self._map.setdefault(cid, {}).setdefault(
            (typ, oid), {"type": typ, "id": oid, "name": name, "published": False, "in_draft": False})
        entry["published"] = entry["published"] or published
        entry["in_draft"] = entry["in_draft"] or in_draft or typ != "presentation"

    def usages(self, cid: int) -> list[dict]:
        order = {"presentation": 0, "touch_menu": 1, "design": 2, "content": 3}
        return sorted(self._map.get(cid, {}).values(), key=lambda u: (order[u["type"]], u["name"].lower()))

    def count(self, cid: int) -> int:
        return len(self._map.get(cid, {}))


# ------------------------------------------------------------ URL-Prüfung

_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)


def analyze_frame_headers(headers) -> tuple[bool, str]:
    """Wertet X-Frame-Options und CSP frame-ancestors aus. Rückgabe (einbettbar, Erklärung)."""
    xfo = (headers.get("X-Frame-Options") or "").strip().upper()
    if "DENY" in xfo:
        return False, "Die Seite verbietet die Einbettung in andere Seiten (X-Frame-Options: DENY)."
    if "SAMEORIGIN" in xfo:
        return False, "Die Seite erlaubt die Einbettung nur auf der eigenen Website (X-Frame-Options: SAMEORIGIN)."
    csps = headers.get_all("Content-Security-Policy") if hasattr(headers, "get_all") else \
        [headers.get("Content-Security-Policy")]
    for csp in csps or []:
        if not csp:
            continue
        for directive in csp.split(";"):
            parts = directive.strip().split()
            if parts and parts[0].lower() == "frame-ancestors":
                sources = [p.lower() for p in parts[1:]]
                if not any(s in ("*", "https:", "http:") for s in sources):
                    return False, ("Die Seite erlaubt die Einbettung nur auf bestimmten Websites "
                                   "(Content-Security-Policy: frame-ancestors).")
    return True, "Die Seite kann auf der Stele eingebettet werden."


def check_url(url: str) -> dict:
    """Prüft, ob eine Webseite erreichbar und einbettbar ist (Timeout 5 s, max. 512 KB)."""
    if not is_http_url(url):
        return {"ok": False, "embeddable": None, "title": None,
                "message": "Bitte eine gültige Adresse eingeben, die mit http:// oder https:// beginnt."}
    req = urllib.request.Request(url.strip(), headers={
        "User-Agent": "Mozilla/5.0 (SteleCMS Einbettungsprüfung)", "Accept": "text/html,*/*;q=0.8"})
    status = 0
    try:
        with security.open_outbound(req, timeout=5) as resp:
            headers, status = resp.headers, resp.status
            body = security.read_limited(resp, 512 * 1024, 10)
    except urllib.error.HTTPError as exc:
        headers, status = exc.headers, exc.code
        try:
            body = exc.read(512 * 1024)
        except Exception:  # noqa: BLE001
            body = b""
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ValueError, OSError,
            http.client.HTTPException):
        return {"ok": False, "embeddable": None, "title": None,
                "message": "Seite nicht erreichbar – später erneut prüfen."}
    embeddable, message = analyze_frame_headers(headers)
    title = None
    m = _TITLE_RE.search(body or b"")
    if m:
        charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
        try:
            raw = m.group(1).decode(charset or "utf-8", "replace")
        except LookupError:
            raw = m.group(1).decode("utf-8", "replace")
        title = " ".join(html.unescape(raw).split())[:200] or None
    ok = status < 400
    if not ok:
        message = f"Die Seite antwortet mit Fehler {status}. Bitte die Adresse prüfen. " + (
            "" if embeddable else message)
        message = message.strip()
    return {"ok": ok, "embeddable": embeddable, "title": title, "message": message}
