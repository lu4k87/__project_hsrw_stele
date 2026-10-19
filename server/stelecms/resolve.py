"""Präsentation → aufgelöste Form (SPEC §8), Status-Hash, Snapshot und Manifest."""
from __future__ import annotations

import copy
import hashlib

from . import appsettings, cache, schemas, timeutil
from . import db as dbm
from . import fonts as fontsm
from .media import content_urls
from .validation import is_int

MANIFEST_SCHEMA = 1


def sha256_hex(obj) -> str:
    return hashlib.sha256(dbm.canonical_json(obj).encode("utf-8")).hexdigest()


class Resolver:
    """Löst Präsentationen auf; hält Inhalte, Designs und Touch-Menüs je Request im Speicher."""

    def __init__(self, conn, settings: dict | None = None):
        self.conn = conn
        self.settings = settings or appsettings.get_settings(conn)
        self._contents: dict[int, dict | None] = {}
        self._all_contents = False
        self._designs: dict[int, dict | None] = {}
        self._menus: dict[int, dict | None] = {}
        self._items: dict[int, list] = {}
        self._presentations: dict[int, dict | None] = {}
        self._manifests: dict[int, dict] = {}

    # ------------------------------------------------------------ Laden

    def load_all(self) -> "Resolver":
        """Lädt alle Inhalte und Folien auf einmal (für Listen)."""
        for r in dbm.rows(self.conn, "SELECT * FROM contents"):
            self._contents[r["id"]] = r
        self._all_contents = True
        items: dict[int, list] = {}
        for it in dbm.rows(self.conn, "SELECT * FROM presentation_items ORDER BY presentation_id, position, id"):
            items.setdefault(it["presentation_id"], []).append(it)
        for r in dbm.rows(self.conn, "SELECT id FROM presentations"):
            self._items[r["id"]] = items.get(r["id"], [])
        return self

    def content(self, cid) -> dict | None:
        if not is_int(cid):
            return None
        if cid not in self._contents:
            if self._all_contents:
                return None
            self._contents[cid] = dbm.row(self.conn, "SELECT * FROM contents WHERE id = ?", (cid,))
        return self._contents[cid]

    def items(self, pid: int) -> list[dict]:
        if pid not in self._items:
            self._items[pid] = dbm.rows(self.conn, "SELECT * FROM presentation_items WHERE presentation_id = ? "
                                                   "ORDER BY position, id", (pid,))
        return self._items[pid]

    def presentation(self, pid) -> dict | None:
        if not is_int(pid):
            return None
        if pid not in self._presentations:
            self._presentations[pid] = dbm.row(self.conn, "SELECT * FROM presentations WHERE id = ?", (pid,))
        return self._presentations[pid]

    def design_row(self, did) -> dict | None:
        if not is_int(did):
            return None
        if did not in self._designs:
            self._designs[did] = dbm.row(self.conn, "SELECT * FROM designs WHERE id = ?", (did,))
        return self._designs[did]

    def menu_row(self, mid) -> dict | None:
        if not is_int(mid):
            return None
        if mid not in self._menus:
            self._menus[mid] = dbm.row(self.conn, "SELECT * FROM touch_menus WHERE id = ?", (mid,))
        return self._menus[mid]

    def invalidate(self) -> None:
        self.__init__(self.conn, self.settings)

    # ------------------------------------------------------------ Entwurf

    def draft_source(self, p: dict) -> dict:
        """Entwurfsstand (für Veröffentlichen/Verwerfen)."""
        return {
            "settings": schemas.merge_defaults(schemas.PRESENTATION_SETTINGS, dbm.jloads(p["settings"], {})),
            "design_id": p["design_id"],
            "touch_menu_id": p["touch_menu_id"],
            "items": [{
                "id": it["id"], "content_id": it["content_id"], "enabled": bool(it["enabled"]),
                "duration_s": it["duration_s"], "transition": it["transition"],
                "valid_from": it["valid_from"], "valid_until": it["valid_until"],
                "caption": it["caption"], "options": dbm.jloads(it["options"], {}),
            } for it in self.items(p["id"])],
        }

    # ------------------------------------------------------------ Folien

    def image_url(self, cid) -> str | None:
        c = self.content(cid)
        if not c or c["type"] != "image" or c["status"] != "ready":
            return None
        return content_urls(c)["display"]

    def effective_duration(self, c: dict, settings: dict, item: dict | None = None) -> float:
        opts = (item or {}).get("options") or {}
        if isinstance(opts, str):
            opts = dbm.jloads(opts, {})
        base = (item or {}).get("duration_s") or settings["default_duration_s"]
        if c["type"] == "video":
            play_to_end = opts.get("play_to_end", settings["video_play_to_end"])
            if play_to_end and c.get("duration_s"):
                return round(float(c["duration_s"]), 3)
            return float(base)
        if c["type"] == "pdf":
            pages = schemas.parse_pages(opts.get("pages"), int(c.get("page_count") or 0))
            page_duration = opts.get("page_duration_s") or base
            return float(max(1, len(pages)) * page_duration)
        return float(base)

    def slide(self, c: dict, settings: dict, item: dict | None = None) -> dict:
        opts = (item or {}).get("options") or {}
        if isinstance(opts, str):
            opts = dbm.jloads(opts, {})
        urls = content_urls(c)
        data = dbm.jloads(c["data"], {})
        s = {
            "id": item["id"] if item else None,
            "content_id": c["id"],
            "type": c["type"],
            "title": c["title"],
            "duration_s": self.effective_duration(c, settings, item),
            "transition": (item or {}).get("transition") or settings["transition"],
            "valid_from": (item or {}).get("valid_from"),
            "valid_until": (item or {}).get("valid_until"),
            "caption": (item or {}).get("caption") or "",
            "fullscreen": bool(opts.get("fullscreen", False)),
        }
        t = c["type"]
        if t == "image":
            s.update({"src": urls["display"], "width": c["width"], "height": c["height"],
                      "fit": opts.get("fit") or settings["image_fit"],
                      "ken_burns": bool(opts.get("ken_burns", settings["ken_burns"]))})
        elif t == "video":
            s.update({"src": urls["display"], "poster": urls["poster"], "width": c["width"], "height": c["height"],
                      "video_duration_s": c["duration_s"],
                      "play_to_end": bool(opts.get("play_to_end", settings["video_play_to_end"])),
                      "sound": bool(opts.get("sound", settings["video_sound"]))})
        elif t == "pdf":
            all_pages = urls["pages"] or []
            nums = schemas.parse_pages(opts.get("pages"), len(all_pages))
            base = (item or {}).get("duration_s") or settings["default_duration_s"]
            s.update({"pages": [all_pages[n - 1] for n in nums],
                      "page_duration_s": opts.get("page_duration_s") or base})
        elif t == "text":
            d = schemas.merge_defaults(schemas.TEXT_DATA, data)
            fields = {k: v for k, v in d["fields"].items()
                      if k not in schemas.TEXT_OPTIONAL_FIELDS or v}
            fields["image_url"] = self.image_url(fields.get("image_content_id"))
            style = {k: v for k, v in d["style"].items() if k not in schemas.TEXT_STYLE_OPTIONAL or v is not None}
            style["bg_image_url"] = self.image_url(style.get("bg_image_content_id"))
            s.update({"template": d["template"], "fields": fields, "style": style})
        elif t == "web":
            d = schemas.merge_defaults(schemas.WEB_DATA, data)
            s.update({"url": d["url"], "zoom": d["zoom"], "refresh_s": d["refresh_s"],
                      "interactive": d["interactive"]})
        return s

    # ------------------------------------------------------ Design, Touch

    def design(self, did) -> dict | None:
        r = self.design_row(did)
        if not r:
            return None
        cfg = schemas.merge_defaults(schemas.DESIGN_CONFIG, dbm.jloads(r["config"], {}))
        return self.design_out(cfg)

    def design_out(self, cfg: dict) -> dict:
        """Design fürs Manifest: Logo-URL dazu, unbelegte neue Theme-Felder weg (Status-Hash bleibt gleich)."""
        cfg["theme"] = {k: v for k, v in cfg["theme"].items()
                        if k not in schemas.DESIGN_THEME_OPTIONAL or v is not None}
        cfg["logo_url"] = self.image_url(cfg["header"].get("logo_content_id"))
        return cfg

    def fonts(self, obj) -> dict:
        """Hochgeladene Schriften, auf die `obj` (Präsentationen, Vorschau) verweist."""
        return fontsm.fonts_map(self.conn, fontsm.font_refs(obj))

    def _touch_slide(self, cid, settings) -> dict | None:
        c = self.content(cid)
        if not c or c["status"] != "ready":
            return None
        return self.slide(c, settings, None)

    def tile(self, t: dict, settings: dict) -> dict:
        a = t.get("action") or {}
        out = {"id": t.get("id"), "label": t.get("label", ""), "icon": t.get("icon", "info"),
               "color": t.get("color", "#1E5AA8"), "image_url": self.image_url(t.get("image_content_id")),
               "image_content_id": t.get("image_content_id")}
        typ = a.get("type")
        if typ == "gallery":
            ids = [cid for cid in a.get("content_ids") or [] if is_int(cid)]
            items = [s for s in (self._touch_slide(cid, settings) for cid in ids) if s]
            out["action"] = {"type": "gallery", "content_ids": ids, "items": items}
        elif typ == "submenu":
            out["action"] = {"type": "submenu", "tiles": [self.tile(st, settings) for st in a.get("tiles") or []
                                                          if isinstance(st, dict)]}
        else:
            cid = a.get("content_id")
            out["action"] = {"type": "content", "content_id": cid, "item": self._touch_slide(cid, settings)}
        return out

    def touch_menu(self, mid, settings: dict) -> dict | None:
        r = self.menu_row(mid)
        if not r:
            return None
        cfg = schemas.merge_defaults(schemas.TOUCH_CONFIG, dbm.jloads(r["config"], {}))
        cfg["tiles"] = [self.tile(t, settings) for t in cfg["tiles"] if isinstance(t, dict)]
        return cfg

    # ------------------------------------------------------ Präsentation

    def resolve(self, p: dict, source: dict | None = None, published_at: str | None = None) -> dict:
        source = source or self.draft_source(p)
        settings = schemas.merge_defaults(schemas.PRESENTATION_SETTINGS, source.get("settings"))
        slides = []
        for it in source.get("items", []):
            if not it.get("enabled"):
                continue
            c = self.content(it.get("content_id"))
            if not c or c["status"] != "ready":
                continue  # nicht fertige Inhalte werden übersprungen
            slides.append(self.slide(c, settings, it))
        return {
            "id": p["id"],
            "name": p["name"],
            "published_at": published_at,
            "settings": settings,
            "design": self.design(source.get("design_id")),
            "touch_menu": self.touch_menu(source.get("touch_menu_id"), settings),
            "slides": slides,
        }

    def draft_hash(self, p: dict) -> str:
        return cache.get(("draft_hash", p["id"]), lambda: status_hash(self.resolve(p)))

    def status(self, p: dict) -> str:
        if not p["published_hash"]:
            return "draft"
        return "published" if self.draft_hash(p) == p["published_hash"] else "changed"

    def published(self, p: dict | None) -> dict | None:
        """Veröffentlichter Stand (mit aktuellem Namen) oder None."""
        if not p or not p["published_snapshot"]:
            return None
        snap = dbm.jloads(p["published_snapshot"], None)
        if not isinstance(snap, dict):
            return None
        snap["name"] = p["name"]
        return snap

    # ------------------------------------------------------------ Manifest

    def manifest_version(self, stele: dict) -> str:
        """Version des Stelen-Manifests (Heartbeat, Status) – aus dem Cache, sonst neu berechnet."""
        return cache.get(("manifest_version", stele["id"]), lambda: self.stele_manifest(stele)["version"])

    def stele_manifest(self, stele: dict) -> dict:
        if stele["id"] in self._manifests:
            return self._manifests[stele["id"]]
        entries = dbm.rows(self.conn, "SELECT * FROM schedule_entries WHERE stele_id = ? AND enabled = 1 "
                                      "ORDER BY id", (stele["id"],))
        wanted = [e["presentation_id"] for e in entries]
        if stele["default_presentation_id"]:
            wanted.append(stele["default_presentation_id"])
        presentations = {}
        for pid in sorted(set(wanted)):
            snap = self.published(self.presentation(pid))
            if snap:
                presentations[str(pid)] = snap
        schedule = [{
            "id": e["id"], "presentation_id": e["presentation_id"], "days": dbm.jloads(e["days"], []),
            "start": e["start_time"], "end": e["end_time"], "date_from": e["date_from"],
            "date_until": e["date_until"], "priority": e["priority"], "updated_at": e["updated_at"],
        } for e in entries if str(e["presentation_id"]) in presentations]
        default_id = stele["default_presentation_id"]
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "version": "",
            "generated_at": timeutil.now_iso(),
            "preview": False,
            "timezone": self.settings["timezone"],
            "org_name": self.settings["org_name"],
            "stele": {
                "id": stele["id"], "name": stele["name"], "location": stele["location"],
                "width": stele["width"], "height": stele["height"],
                "settings": schemas.merge_defaults(schemas.STELE_SETTINGS, dbm.jloads(stele["settings"], {})),
            },
            "default_presentation_id": default_id if str(default_id) in presentations else None,
            "schedule": schedule,
            "presentations": presentations,
            "feeds": self.feeds(presentations.values()),
            "fonts": (fonts := self.fonts(presentations)),
            "assets": collect_assets([presentations, fonts]),
        }
        finalize_manifest(manifest)
        self._manifests[stele["id"]] = manifest
        return manifest

    def preview_manifest(self, p: dict, source: str = "draft") -> dict | None:
        if source == "published":
            resolved = self.published(p)
            if resolved is None:
                return None
        else:
            resolved = self.resolve(p)
        presentations = {str(p["id"]): resolved}
        manifest = {
            "schema": MANIFEST_SCHEMA, "version": "", "generated_at": timeutil.now_iso(), "preview": True,
            "timezone": self.settings["timezone"], "org_name": self.settings["org_name"], "stele": None,
            "default_presentation_id": p["id"], "schedule": [], "presentations": presentations,
            "feeds": self.feeds(presentations.values()), "fonts": (fonts := self.fonts(presentations)),
            "assets": collect_assets([presentations, fonts]),
        }
        return finalize_manifest(manifest)

    def feeds(self, presentations) -> dict:
        urls = sorted({(pr.get("design") or {}).get("footer", {}).get("ticker_rss_url") or ""
                       for pr in presentations} - {""})
        out = {}
        for url in urls:
            r = dbm.row(self.conn, "SELECT fetched_at, ok, items FROM feed_cache WHERE url = ?", (url,))
            out[url] = {"items": dbm.jloads(r["items"], []) if r and r["ok"] else [],
                        "fetched_at": r["fetched_at"] if r else None}
        return out


def _strip_for_hash(obj):
    """Entfernt Folien-Titel und -IDs (auch in Touch-Aktionen) für den Status-Hash."""
    if isinstance(obj, dict):
        is_slide = "content_id" in obj and "type" in obj and "duration_s" in obj
        return {k: _strip_for_hash(v) for k, v in obj.items() if not (is_slide and k in ("title", "id"))}
    if isinstance(obj, list):
        return [_strip_for_hash(x) for x in obj]
    return obj


def status_hash(resolved: dict) -> str:
    """Hash über die aufgelöste Präsentation ohne published_at, Name und Folien-Titel (§8).

    Zusätzlich ohne Folien-IDs: „Änderungen verwerfen“ legt Folien ggf. mit neuen IDs an; die Reihenfolge
    steckt ohnehin im Array. So zeigt ein verworfener Entwurf zuverlässig wieder „veröffentlicht“.
    """
    d = copy.deepcopy(resolved)
    d.pop("published_at", None)
    d.pop("name", None)
    return sha256_hex(_strip_for_hash(d))


def collect_assets(obj) -> list[str]:
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and o.startswith("/media/"):
            found.add(o)
    walk(obj)
    return sorted(found)


def finalize_manifest(m: dict) -> dict:
    body = {k: v for k, v in m.items() if k not in ("version", "generated_at")}
    # Abrufzeit der Feeds nicht mitzählen: sonst alle 10 min eine neue Version ohne neue Meldungen
    if isinstance(body.get("feeds"), dict):
        body["feeds"] = {url: (f or {}).get("items") for url, f in body["feeds"].items()}
    m["version"] = sha256_hex(body)[:16]
    return m


def item_validity(item: dict, tzname: str, now=None) -> str:
    """active | scheduled | expired – Wanduhr in der eingestellten Zeitzone."""
    local = timeutil.local_now(tzname, now).strftime("%Y-%m-%dT%H:%M")
    if item.get("valid_until") and local >= item["valid_until"]:
        return "expired"
    if item.get("valid_from") and local < item["valid_from"]:
        return "scheduled"
    return "active"
