"""Job-Worker für Video- und PDF-Verarbeitung (eigener Thread, eigene SQLite-Verbindung).

Ohne Hintergrund-Threads (STELECMS_BACKGROUND=0, Tests) laufen Jobs direkt im auslösenden Request.
"""
from __future__ import annotations

import logging
import sqlite3
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path

from PIL import Image

from . import appsettings, cache, media, timeutil
from . import db as dbm

log = logging.getLogger("stelecms.jobs")
EXT_KEY = "stelecms_jobs"


class JobError(Exception):
    """Verarbeitung fehlgeschlagen; Nachricht landet in contents.status_message."""


def enqueue(conn, kind: str, content_id: int) -> int:
    now = timeutil.now_iso()
    return dbm.insert(conn, "jobs", {"kind": kind, "content_id": content_id, "status": "queued", "progress": 0,
                                     "message": "", "created_at": now, "updated_at": now})


def kick(app) -> None:
    """Neue Jobs melden: Worker wecken oder (ohne Hintergrund) sofort abarbeiten."""
    worker = app.extensions.get(EXT_KEY)
    if worker is not None:
        worker.wake()
    else:
        process_pending(app.config)


ORPHAN_AFTER_S = 120


def requeue_orphans(conn) -> int:
    """Video/PDF in Verarbeitung ohne offenen Job (z. B. Upload brach zwischen Anlegen und Einreihen ab).

    Erst nach ORPHAN_AFTER_S, damit ein gerade laufender Upload seinen Job noch selbst einreihen kann.
    """
    cutoff = timeutil.iso(timeutil.utcnow() - timedelta(seconds=ORPHAN_AFTER_S))
    with dbm.transaction(conn):
        rows = conn.execute(
            "SELECT id, type FROM contents c WHERE status = 'processing' AND type IN ('video', 'pdf') "
            "AND created_at < ? AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.content_id = c.id "
            "AND j.status IN ('queued', 'running'))", (cutoff,)).fetchall()
        for r in rows:
            enqueue(conn, r["type"], r["id"])
    if rows:
        log.warning("%d Inhalt(e) ohne Verarbeitungs-Job neu eingereiht", len(rows))
    return len(rows)


def process_pending(cfg) -> int:
    """Arbeitet alle wartenden Jobs ab. Rückgabe: Anzahl bearbeiteter Jobs."""
    conn = dbm.connect(cfg["DB_PATH"])
    count = 0
    try:
        requeue_orphans(conn)
        while True:
            with dbm.transaction(conn):
                job = dbm.row(conn, "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
                if job is None:
                    break
                conn.execute("UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
                             (timeutil.now_iso(), job["id"]))
            run_job(conn, cfg, job)
            cache.bump(cfg["DB_PATH"])   # Inhalt fertig/fehlerhaft → Entwurfs-Status ändert sich
            count += 1
    finally:
        conn.close()
    return count


class _Progress:
    """Schreibt Fortschritt höchstens einmal pro Sekunde in jobs und contents."""

    def __init__(self, conn, job_id: int, content_id: int):
        self.conn, self.job_id, self.content_id = conn, job_id, content_id
        self._last = 0.0
        self._value = -1

    def __call__(self, pct: int) -> None:
        now = time.monotonic()
        if pct == self._value or now - self._last < 1.0:
            return
        self._last, self._value = now, pct
        ts = timeutil.now_iso()
        try:
            self.conn.execute("UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?", (pct, ts, self.job_id))
            self.conn.execute("UPDATE contents SET progress = ? WHERE id = ? AND status = 'processing'",
                              (pct, self.content_id))
        except sqlite3.OperationalError as exc:  # kurz gesperrte Datenbank: Anzeige ist zweitrangig
            log.warning("Fortschritt von Job %s nicht gespeichert: %s", self.job_id, exc)


def run_job(conn, cfg, job: dict) -> None:
    content = dbm.row(conn, "SELECT * FROM contents WHERE id = ?", (job["content_id"],)) if job["content_id"] else None
    if content is None:
        _finish_job(conn, job["id"], "error", "Inhalt wurde inzwischen gelöscht.")
        return
    progress = _Progress(conn, job["id"], content["id"])
    try:
        if job["kind"] == "video":
            _process_video(conn, cfg, content, progress)
        elif job["kind"] == "pdf":
            _process_pdf(conn, content, Path(cfg["MEDIA_DIR"]), progress)
        else:
            raise JobError(f"Unbekannte Job-Art „{job['kind']}“.")
        _finish_job(conn, job["id"], "done", "")
    except JobError as exc:
        _fail(conn, job["id"], content["id"], str(exc))
    except Exception as exc:  # noqa: BLE001 – Worker darf nie abstürzen
        log.exception("Job %s fehlgeschlagen", job["id"])
        _fail(conn, job["id"], content["id"], f"Unerwarteter Fehler ({type(exc).__name__}).")


def _finish_job(conn, job_id: int, status: str, message: str) -> None:
    conn.execute("UPDATE jobs SET status = ?, progress = ?, message = ?, updated_at = ? WHERE id = ?",
                 (status, 100 if status == "done" else 0, message, timeutil.now_iso(), job_id))


def _fail(conn, job_id: int, content_id: int, message: str) -> None:
    _finish_job(conn, job_id, "error", message)
    conn.execute("UPDATE contents SET status = 'error', status_message = ?, progress = NULL, updated_at = ? "
                 "WHERE id = ?", (message, timeutil.now_iso(), content_id))


# ------------------------------------------------------------------ Video

def _ffmpeg() -> str:
    exe = media.tool("ffmpeg")
    if not exe:
        raise JobError("ffmpeg ist auf dem Server nicht installiert.")
    return exe


def _process_video(conn, cfg, content: dict, progress) -> None:
    folder = Path(cfg["MEDIA_DIR"]) / content["uid"]
    data = dbm.jloads(content["data"], {})
    original = folder / (data.get("original_file") or "")
    if not original.is_file():
        raise JobError("Die Originaldatei fehlt.")
    probe = media.ffprobe(original)
    if not probe:
        raise JobError("Das Video konnte nicht gelesen werden.")
    info = media.video_info(original, probe)
    settings = appsettings.get_settings(conn)
    compatible = media.video_compatible(info)
    display, transcoded, codec = original.name, False, info["vcodec"]
    target = folder / "video.mp4"
    part = folder / "video.mp4.part"

    if compatible and info["container"] in ("mp4", "mov") and (
            info["container"] == "mov" or not media.mp4_faststart(original)):
        # Umpacken ohne Neukodierung (schneller Start im Browser)
        cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(original), "-map", "0:v:0",
               "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(part)]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=3600, check=False)
            if res.returncode == 0 and part.exists():
                part.replace(target)
                display = target.name
        except (subprocess.TimeoutExpired, OSError):
            pass
        part.unlink(missing_ok=True)
    elif not compatible and settings["auto_transcode"]:
        cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(original),
               "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", "-f", "mp4", str(part)]
        rc, err = media.run_ffmpeg_progress(cmd, info["duration"], progress)
        if rc != 0 or not part.exists():
            part.unlink(missing_ok=True)
            log.warning("Umwandlung fehlgeschlagen: %s", err)
            raise JobError("Die Umwandlung in ein browsertaugliches Format ist fehlgeschlagen.")
        part.replace(target)
        display, transcoded, compatible, codec = target.name, True, True, "h264"

    progress(99)
    poster_ok = media.extract_poster(folder / display, folder, info["duration"])
    data.update({"codec": codec, "audio": info["acodec"] is not None, "transcoded": transcoded,
                 "compatible": compatible, "display_file": display})
    if poster_ok:
        data["poster_file"] = "poster.jpg"
    conn.execute("UPDATE contents SET status = 'ready', status_message = '', progress = NULL, data = ?, width = ?, "
                 "height = ?, duration_s = ?, updated_at = ? WHERE id = ?",
                 (dbm.jdumps(data), info["width"] or None, info["height"] or None, info["duration"],
                  timeutil.now_iso(), content["id"]))


# -------------------------------------------------------------------- PDF

def _process_pdf(conn, content: dict, media_dir: Path, progress) -> None:
    exe = media.tool("pdftoppm")
    if not exe:
        raise JobError("pdftoppm (poppler) ist auf dem Server nicht installiert.")
    folder = media_dir / content["uid"]
    data = dbm.jloads(content["data"], {})
    original = folder / (data.get("original_file") or "original.pdf")
    if not original.is_file():
        raise JobError("Die Originaldatei fehlt.")
    total = int(content["page_count"] or 0)
    if total < 1:
        info = media.pdfinfo(original) or {}
        try:
            total = int(info.get("Pages", "0"))
        except ValueError:
            total = 0
    if total < 1:
        raise JobError("Die PDF-Datei enthält keine lesbaren Seiten.")
    n = min(total, media.PDF_MAX_PAGES)
    pages_dir = folder / "pages"
    pages_dir.mkdir(exist_ok=True)
    for i in range(1, n + 1):
        prefix = pages_dir / f"p{i:03d}"
        cmd = [exe, "-png", "-scale-to-x", str(media.PDF_PAGE_WIDTH), "-scale-to-y", "-1",
               "-f", str(i), "-l", str(i), "-singlefile", str(original), str(prefix)]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise JobError(f"Seite {i} konnte nicht umgewandelt werden (Zeitüberschreitung).")
        if res.returncode != 0 or not prefix.with_suffix(".png").exists():
            raise JobError(f"Seite {i} konnte nicht umgewandelt werden. Ist die PDF-Datei passwortgeschützt?")
        progress(int(i / n * 98))
    first = pages_dir / "p001.png"
    with Image.open(first) as im:
        im.load()
        width, height = im.size
        media.make_thumb(im, folder / "thumb.webp")
    message = ""
    if total > n:
        data["pages_total"] = total
        message = f"Nur die ersten {n} von {total} Seiten werden angezeigt."
    conn.execute("UPDATE contents SET status = 'ready', status_message = ?, progress = NULL, data = ?, width = ?, "
                 "height = ?, page_count = ?, updated_at = ? WHERE id = ?",
                 (message, dbm.jdumps(data), width, height, n, timeutil.now_iso(), content["id"]))


# ------------------------------------------------------------------ Worker

class JobWorker(threading.Thread):
    def __init__(self, cfg):
        super().__init__(name="stelecms-jobs", daemon=True)
        self.cfg = cfg
        self._event = threading.Event()
        self._stop = threading.Event()

    def wake(self) -> None:
        self._event.set()

    def stop(self) -> None:
        self._stop.set()
        self._event.set()

    def run(self) -> None:
        recover(self.cfg)
        while not self._stop.is_set():
            try:
                process_pending(self.cfg)
            except Exception:  # noqa: BLE001
                log.exception("Job-Worker: Fehler beim Abarbeiten")
            self._event.wait(5)
            self._event.clear()


def recover(cfg) -> None:
    """Nach einem Neustart: abgebrochene Jobs erneut einreihen."""
    conn = dbm.connect(cfg["DB_PATH"])
    try:
        conn.execute("UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
                     (timeutil.now_iso(),))
    finally:
        conn.close()


def start_worker(app) -> JobWorker:
    worker = JobWorker(app.config)
    app.extensions[EXT_KEY] = worker
    worker.start()
    return worker
