# Stele CMS – Spezifikation v1

Verbindlicher Vertrag für alle Teile (Server, Admin-Oberfläche, Player, Stelen-Agent).
Abweichungen nur nach Absprache; Unklarheiten → die robustere, einfachere Lösung wählen und im Code kommentieren.

---

## 1. Ziel und Rahmen

- Web-CMS, mit dem eine **digitale Stele** bespielt wird: Hochformat **1080 × 1920**, **Touch**, in der Stele steckt ein **PC mit Chrome im Kiosk-Modus**, eigene IP im Netzwerk.
- Inhalte: Bilder, Videos, PDFs, Info-Folien (Vorlagen), Webseiten, Laufband; Präsentationen (Diashows) mit Einstellungen, Header/Footer (Designs), Touch-Menü für Besucher, Zeitplan.
- Monitoring der Stele(n), Benutzerverwaltung, Rollen & Rechte pro Funktion, Protokoll.
- **Jetzt nur lokal:** Server lauscht auf `127.0.0.1:8090`. Netzwerk/HTTPS kommt später (Host per Umgebungsvariable umstellbar, nichts hart verdrahten).
- **Login „Testbetrieb“:** echte lokale Konten (Passwort-Hash, Sitzung, Sperre). Zusätzlich Schnellanmeldung mit Demo-Konten – nur wenn aktiviert **und** Anfrage von Loopback (127.0.0.1/::1). Oberfläche zeigt dauerhaft „Testbetrieb“.
- Sprache der Oberfläche: **Deutsch** (Du-Form in Hinweisen vermeiden → neutrale Formulierungen: „Datei hochladen“, „Bitte Namen eingeben“). Datums-/Zahlenformat de-DE.
- Mehrere Stelen werden unterstützt (Start mit einer).
- Keine externen CDNs, keine Build-Schritte: alles läuft offline aus dem Repo.

## 2. Technik und Verzeichnisse

- Python ≥ 3.10, Flask 3, SQLite (WAL), Pillow, ffmpeg/ffprobe, pdftoppm/pdfinfo (poppler). WSGI: `waitress`, Fallback Flask-Server (threaded, ohne Reloader).
- Frontend: Vanilla JS (ES-Module), CSS mit Custom Properties, kein Framework, kein Build.

```
CMS_STELE_KIOSK/
├─ README.md                  Start, Demo-Zugänge, Aufbau (Deutsch)
├─ requirements.txt
├─ run.sh                     legt .venv an (falls nötig), startet server/run.py
├─ docs/SPEC.md               diese Datei
├─ server/
│  ├─ run.py                  Einstieg (waitress oder Flask), liest Umgebungsvariablen
│  ├─ stelecms/
│  │  ├─ __init__.py          create_app(config: dict | None = None)
│  │  ├─ config.py            Umgebungsvariablen, Pfade, APP_VERSION = "1.0.0"
│  │  ├─ db.py                Verbindung je Thread/Request, Migrationen, Helfer (now_iso, json-Spalten)
│  │  ├─ migrations/0001_init.sql
│  │  ├─ seed.py              Rollen, Demo-Konten, Demo-Inhalte (nur bei neuer DB)
│  │  ├─ errors.py            ApiError + JSON-Fehlerbehandlung
│  │  ├─ security.py          Passwort-Hash, CSRF, Sicherheits-Header, Rate-Limit
│  │  ├─ auth.py              Sitzung, current_user, login_required
│  │  ├─ permissions.py       Rechte-Katalog, require(perm), effektive Rechte
│  │  ├─ audit.py             audit(action, entity_type, entity, summary, details)
│  │  ├─ validation.py        kleine Validierungshelfer (Pflichtfelder, Längen, Farben, Zeiten)
│  │  ├─ media.py             Upload, Prüfung, Varianten (Thumbs, Poster, PDF-Seiten)
│  │  ├─ jobs.py              Worker-Thread für Video/PDF
│  │  ├─ resolve.py           Präsentation → aufgelöste Form, Hash, Snapshot, Manifest
│  │  ├─ schedule.py          Zeitplan-Auflösung (jetzt / nächster Wechsel / Zeitleiste)
│  │  ├─ monitor.py           Hintergrund: Offline-Erkennung, Ping, RSS, Aufräumen
│  │  └─ api/                 Blueprints: auth, users, roles, contents, presentations, designs,
│  │                          touch_menus, steles, schedule, monitoring, dashboard, audit,
│  │                          settings, player, agent, media (Dateiauslieferung)
│  └─ tests/                  pytest
├─ web/
│  ├─ shared/icons.js         gemeinsames Icon-Set (Admin + Player), s. §12
│  ├─ admin/                  Admin-Oberfläche (SPA), s. §11
│  └─ player/                 Stelen-Player, s. §9
├─ stele_agent/               Python-Skript für den Stelen-PC, s. §10
└─ data/                      (git-ignoriert) cms.db, secret_key, media/, screenshots/, backups/
```

### Umgebungsvariablen
| Variable | Standard | Zweck |
|---|---|---|
| `STELECMS_HOST` | `127.0.0.1` | Bind-Adresse (später Netzwerk) |
| `STELECMS_PORT` | `8090` | Port |
| `STELECMS_DATA` | `<repo>/data` | Datenordner (Tests: eigener Temp-Ordner!) |
| `STELECMS_SECRET_KEY` | – | sonst zufällig erzeugt und in `data/secret_key` gespeichert |
| `STELECMS_BACKGROUND` | `1` | `0` = keine Hintergrund-Threads (Tests) |
| `STELECMS_SEED_DEMO` | `0` | `1` = bekannte Demo-Konten und Demo-Inhalte bei neuer DB anlegen |
| `STELECMS_HTTPS` | `0` | Sichere Cookies + HSTS hinter einem HTTPS-Reverse-Proxy |
| `STELECMS_ALLOW_INSECURE_LAN` | `0` | Explizite Ausnahme für HTTP mit Netzwerk-Bind-Adresse |
| `STELECMS_OUTBOUND_PRIVATE_HOSTS` | – | Exakte, kommagetrennte Freigabeliste privater Hosts für ausgehende Abrufe |

### URL-Aufteilung
| Pfad | Inhalt | Zugriff |
|---|---|---|
| `/` | Weiterleitung auf `/admin/` | – |
| `/admin/` | Admin-SPA (`web/admin/index.html`, statische Dateien darunter) | öffentlich (Login in der SPA) |
| `/admin/show-stele-index.html` | Vorführseite (§11.1) | öffentlich; Daten nur mit Sitzung |
| `/shared/…` | `web/shared/` (z. B. `icons.js`) | öffentlich |
| `/player/` | Player (`web/player/index.html`), `/player/sw.js` (Scope `/player/`) | öffentlich; Daten nur mit Stelen-Schlüssel |
| `/api/…` | Admin-API | Sitzung |
| `/api/player/…`, `/api/agent/…` | Player-/Agent-API | Stelen-Schlüssel |
| `/media/<uid>/<datei>` | Mediendateien (Range-fähig) | Sitzung **oder** Stelen-Schlüssel |

## 3. Begriffe (Domänenmodell)

| Begriff (UI) | Bedeutung |
|---|---|
| **Inhalt** | Eintrag der Mediathek: `image`, `video`, `pdf`, `text` (Info-Folie aus Vorlage), `web` (Webseite) |
| **Präsentation** | Diashow: geordnete **Folien** (Verweise auf Inhalte mit eigener Dauer/Übergang/Gültigkeit) + **Diashow-Einstellungen** + **Design** + optional **Touch-Menü**. Wird **veröffentlicht**; die Stele spielt nur veröffentlichte Stände. |
| **Design** | Rahmen um die Folien: Header (Logo, Titel, Uhr/Datum), Footer (Laufband oder Text), Schriften für Text und Überschriften, Typografie-Standard der Info-Folien, Akzentfarbe. Wiederverwendbar. |
| **Touch-Menü** | Was Besucher beim Antippen sehen: Kacheln → Inhalt, Galerie oder Untermenü (max. 2 Ebenen). Rückkehr zur Diashow nach Inaktivität. |
| **Stele** | Gerät: Name, Standort, IP, Auflösung, Stelen-Schlüssel, Standard-Präsentation, Einstellungen (Lautstärke, Nachtmodus …). |
| **Zeitplan-Eintrag** | Präsentation X auf Stele Y an Wochentagen/Uhrzeiten/Datumsbereich, Priorität. Ohne passenden Eintrag läuft die Standard-Präsentation. |

### Veröffentlichen und Freigabe
- Bearbeiten ändert immer nur den **Entwurf**. „Veröffentlichen“ erzeugt einen **Snapshot** (vollständig aufgelöst inkl. Design und Touch-Menü) – nur der geht an die Stele.
- Status einer Präsentation (berechnet):
  - `draft` – noch nie veröffentlicht
  - `published` – Entwurf == veröffentlichter Stand (Hash gleich)
  - `changed` – veröffentlicht, aber Entwurf weicht ab (auch wenn nur Design/Touch-Menü/Info-Folie geändert wurde)
- Freigabe (`review_state`): `none` · `requested` (Autor ohne Veröffentlichungsrecht hat eingereicht) · `rejected` (mit Begründung). Veröffentlichen und „Änderungen verwerfen“ setzen `none` (Notiz, Person, Zeit, Hash leer).
- Vier-Augen-Prinzip: Beim Einreichen speichert der Server den Status-Hash des Entwurfs (`review_hash`).
  - Ändert danach jemand Folien oder Einstellungen der Präsentation so, dass der Hash abweicht, wird die Freigabe auf `none` zurückgesetzt (Umbenennen/Beschreibung allein nicht) → erneut einreichen. Protokoll: `details.review_reset`.
  - Ändert sich der Entwurf nur indirekt (Design, Touch-Menü, Info-Folie), bleibt `requested`, aber `review_changed` ist `true`; Veröffentlichen → 409 `changed_since_review`, bis die veröffentlichende Person mit `confirm_changed: true` bestätigt, den aktuellen Stand geprüft zu haben.
- „Änderungen verwerfen“: Entwurf (Einstellungen, Folien, Design-/Touch-Zuordnung) auf den Stand der letzten Veröffentlichung zurücksetzen (`published_source`).
- Gleichzeitiges Bearbeiten: Editoren senden bei PATCH/PUT `expected_updated_at` (zuletzt geladenes `updated_at`); weicht der gespeicherte Stand ab → 409 `edit_conflict` „… wurde inzwischen von X geändert – bitte neu laden“ (`details.updated_at`, `details.updated_by`), nichts wird gespeichert. Ohne das Feld keine Prüfung. Grenze: Zeitstempel auf Sekunden.

### Zeitangaben
- Ereignis-Zeitstempel: UTC, ISO 8601 mit `Z`, Sekunden: `2026-09-30T08:15:00Z`.
- Wanduhr-Angaben (Zeitplan `HH:MM`, Gültigkeit `YYYY-MM-DDTHH:MM`, Nachtmodus) gelten in `settings.timezone` (Standard `Europe/Berlin`). Der Player rechnet mit `Intl` in dieser Zeitzone, unabhängig von der Uhr-Zeitzone des Stelen-PCs.

## 4. Datenmodell (SQLite)

`PRAGMA foreign_keys=ON; journal_mode=WAL; busy_timeout=5000`. JSON-Spalten als TEXT. Migrationen nummeriert, Tabelle `schema_version(version INTEGER)`.

```sql
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);           -- value = JSON

CREATE TABLE roles (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  description TEXT NOT NULL DEFAULT '',
  is_admin INTEGER NOT NULL DEFAULT 0,          -- Administrator: alle Rechte, nicht änderbar/löschbar
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE role_permissions (
  role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission TEXT NOT NULL,
  PRIMARY KEY (role_id, permission)
);
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  role_id INTEGER NOT NULL REFERENCES roles(id),
  is_active INTEGER NOT NULL DEFAULT 1,
  is_demo INTEGER NOT NULL DEFAULT 0,           -- für Schnellanmeldung im Testbetrieb
  must_change_password INTEGER NOT NULL DEFAULT 0,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  session_version INTEGER NOT NULL DEFAULT 1,   -- +1 = alle Sitzungen des Benutzers ungültig
  last_login_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE contents (
  id INTEGER PRIMARY KEY,
  uid TEXT NOT NULL UNIQUE,                     -- 16 Hex-Zeichen, Ordnername unter data/media/
  type TEXT NOT NULL CHECK (type IN ('image','video','pdf','text','web')),
  title TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]',
  data TEXT NOT NULL DEFAULT '{}',              -- typabhängig, s. §5
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('processing','ready','error')),
  status_message TEXT NOT NULL DEFAULT '',
  progress INTEGER,                             -- 0..100 während processing
  file_name TEXT, mime TEXT, size_bytes INTEGER,
  width INTEGER, height INTEGER, duration_s REAL, page_count INTEGER,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE designs (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- Hochgeladene Schriften (§5.5): Datei unter MEDIA_DIR/<uid>/font.<format>, Schlüssel „custom-<id>“
CREATE TABLE fonts (
  id INTEGER PRIMARY KEY, uid TEXT NOT NULL UNIQUE, name TEXT NOT NULL, file_name TEXT NOT NULL,
  format TEXT NOT NULL CHECK (format IN ('woff2','woff','ttf','otf')),
  weight INTEGER,                 -- NULL = variable Schrift, sonst 100..900
  size_bytes INTEGER NOT NULL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL, created_at TEXT NOT NULL
);
CREATE TABLE touch_menus (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE presentations (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  settings TEXT NOT NULL,                       -- Diashow-Einstellungen, s. §5
  design_id INTEGER REFERENCES designs(id) ON DELETE SET NULL,
  touch_menu_id INTEGER REFERENCES touch_menus(id) ON DELETE SET NULL,
  published_snapshot TEXT,                      -- aufgelöste Präsentation (Manifest-Form, §8)
  published_source TEXT,                        -- Entwurfsstand beim Veröffentlichen (für „verwerfen“)
  published_hash TEXT,
  published_at TEXT,
  published_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  review_state TEXT NOT NULL DEFAULT 'none' CHECK (review_state IN ('none','requested','rejected')),
  review_note TEXT NOT NULL DEFAULT '',
  review_by INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- wer eingereicht bzw. abgelehnt hat
  review_at TEXT,
  review_hash TEXT,                             -- Status-Hash beim Einreichen (Migration 0002)
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE presentation_items (
  id INTEGER PRIMARY KEY,
  presentation_id INTEGER NOT NULL REFERENCES presentations(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE RESTRICT,
  enabled INTEGER NOT NULL DEFAULT 1,
  duration_s REAL,                              -- NULL = Standard der Präsentation
  transition TEXT,                              -- NULL = Standard
  valid_from TEXT, valid_until TEXT,            -- Wanduhr 'YYYY-MM-DDTHH:MM' oder NULL
  caption TEXT NOT NULL DEFAULT '',
  options TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE steles (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  ip_address TEXT NOT NULL DEFAULT '',
  width INTEGER NOT NULL DEFAULT 1080,
  height INTEGER NOT NULL DEFAULT 1920,
  player_key TEXT NOT NULL UNIQUE,              -- secrets.token_urlsafe(32)
  default_presentation_id INTEGER REFERENCES presentations(id) ON DELETE RESTRICT,
  settings TEXT NOT NULL DEFAULT '{}',          -- s. §5
  paired_at TEXT,
  last_seen_at TEXT,
  last_state TEXT NOT NULL DEFAULT '{}',        -- letzter Heartbeat
  last_agent_at TEXT,
  last_agent TEXT NOT NULL DEFAULT '{}',        -- letzter Agent-Bericht
  last_ping_at TEXT, last_ping_ok INTEGER, last_ping_ms REAL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE schedule_entries (
  id INTEGER PRIMARY KEY,
  stele_id INTEGER NOT NULL REFERENCES steles(id) ON DELETE CASCADE,
  presentation_id INTEGER NOT NULL REFERENCES presentations(id) ON DELETE RESTRICT,
  label TEXT NOT NULL DEFAULT '',
  days TEXT NOT NULL DEFAULT '[1,2,3,4,5,6,7]', -- ISO-Wochentage, Mo=1
  start_time TEXT NOT NULL,                     -- 'HH:MM'
  end_time TEXT NOT NULL,                       -- 'HH:MM', '24:00' erlaubt; end < start = über Mitternacht
  date_from TEXT, date_until TEXT,              -- 'YYYY-MM-DD' oder NULL (inklusive)
  priority INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE pairing_requests (
  code TEXT PRIMARY KEY,                        -- 6 Ziffern
  device_info TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,   -- 10 Minuten gültig
  stele_id INTEGER REFERENCES steles(id) ON DELETE CASCADE,
  claimed_at TEXT
);
CREATE TABLE stele_commands (
  id INTEGER PRIMARY KEY,
  stele_id INTEGER NOT NULL REFERENCES steles(id) ON DELETE CASCADE,
  command TEXT NOT NULL CHECK (command IN ('reload','identify','screenshot','clear_cache')),
  payload TEXT NOT NULL DEFAULT '{}',
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, delivered_at TEXT, done_at TEXT, result TEXT NOT NULL DEFAULT ''
);
CREATE TABLE stele_online_segments (
  id INTEGER PRIMARY KEY,
  stele_id INTEGER NOT NULL REFERENCES steles(id) ON DELETE CASCADE,
  start_at TEXT NOT NULL, end_at TEXT NOT NULL
);
CREATE TABLE stele_events (
  id INTEGER PRIMARY KEY,
  stele_id INTEGER NOT NULL REFERENCES steles(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('info','warning','error')),
  kind TEXT NOT NULL,                           -- online, offline, player_error, command, ping_failed, paired, …
  message TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE stele_metrics (
  stele_id INTEGER NOT NULL REFERENCES steles(id) ON DELETE CASCADE,
  ts TEXT NOT NULL, cpu REAL, ram REAL, disk REAL, temp REAL,
  PRIMARY KEY (stele_id, ts)
);
CREATE TABLE playback_log (
  id INTEGER PRIMARY KEY,
  stele_id INTEGER NOT NULL REFERENCES steles(id) ON DELETE CASCADE,
  started_at TEXT NOT NULL, duration_s REAL,
  presentation_id INTEGER, item_id INTEGER, content_id INTEGER, title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE touch_log (
  id INTEGER PRIMARY KEY,
  stele_id INTEGER NOT NULL REFERENCES steles(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  event TEXT NOT NULL CHECK (event IN ('session_start','tile_open','session_end')),
  session_id TEXT NOT NULL DEFAULT '', tile_id TEXT, label TEXT, duration_s REAL
);
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  user_id INTEGER, username TEXT NOT NULL, user_display TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,       -- login, login_failed, logout, create, update, delete, publish, request_review,
                              -- reject, discard, pair, command, password_reset, unlock, settings, backup
  entity_type TEXT NOT NULL,  -- session, user, role, content, presentation, design, touch_menu, font, stele,
                              -- schedule, settings
  entity_id INTEGER, entity_name TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL,      -- deutscher Satz ohne Subjekt: „hat die Präsentation „Foyer“ veröffentlicht“
  details TEXT NOT NULL DEFAULT '{}',
  ip TEXT NOT NULL DEFAULT ''
);
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,         -- video, pdf
  content_id INTEGER REFERENCES contents(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('queued','running','done','error')),
  progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE feed_cache (
  url TEXT PRIMARY KEY, fetched_at TEXT, ok INTEGER NOT NULL DEFAULT 0,
  items TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT ''
);
```
Sinnvolle Indizes (audit_log.ts, playback_log(stele_id, started_at), touch_log(stele_id, ts), stele_events(stele_id, ts), presentation_items(presentation_id, position)).

## 5. JSON-Strukturen und Standardwerte

Der Server füllt fehlende Schlüssel immer mit Standardwerten auf (`merge_defaults`) und liefert stets vollständige Objekte.

### 5.1 Systemeinstellungen (`settings`)
```json
{
  "org_name": "Meine Organisation",
  "timezone": "Europe/Berlin",
  "session_idle_minutes": 60,
  "lockout_attempts": 5,
  "lockout_minutes": 5,
  "password_min_length": 8,
  "dev_login_enabled": true,
  "upload_max_mb": 128,
  "auto_transcode": true,
  "offline_after_s": 45,
  "retention_days": 30,
  "default_design_id": null,
  "default_slide_duration_s": 7,
  "stale_after_days": 7
}
```

### 5.2 Diashow-Einstellungen (`presentations.settings`)
```json
{
  "default_duration_s": 7,          // 2..600
  "transition": "fade",             // none | fade | slide-left | slide-up | zoom
  "transition_ms": 800,             // 0..3000
  "order": "sequential",            // sequential | shuffle
  "image_fit": "cover",             // cover (füllen) | contain (einpassen)
  "ken_burns": false,               // langsamer Zoom bei Bildern
  "video_sound": false,
  "video_play_to_end": true,        // Video bestimmt die Dauer
  "background": "#000000",          // hinter eingepassten Inhalten
  "show_header": true,
  "show_footer": true,
  "show_progress": false,           // dünner Fortschrittsbalken unten im Folienbereich
  "caption_style": "bar"            // bar (Balken) | shadow (Text mit Schatten)
}
```

### 5.3 Folien-Optionen (`presentation_items.options`, alle optional; fehlend = Präsentations-Standard)
```json
{ "fit": "cover|contain", "ken_burns": true, "sound": false, "play_to_end": true,
  "pages": "1-3,5",                 // nur PDF; leer/fehlt = alle Seiten
  "page_duration_s": 8,             // nur PDF
  "fullscreen": false }             // Header/Footer für diese Folie ausblenden
```

### 5.4 Inhalt-Daten (`contents.data`)
- `image`: `{}` (Datei-Metadaten stehen in den Spalten)
- `video`: `{"codec": "h264", "audio": true, "transcoded": false, "compatible": true}`
- `pdf`: `{}` (Seitenzahl in `page_count`)
- `web`: `{"url": "https://…", "zoom": 1.0, "refresh_s": 0, "interactive": true, "embed_check": {"embeddable": true|false|null, "message": "", "checked_at": "…"}}`
- `text` (Info-Folie):
```json
{
  "template": "title_text",         // title_text | image_text | statement | event | list
  "fields": {
    "title": "", "subtitle": "", "body": "",       // body: Klartext, Zeilenumbrüche erlaubt
    "image_content_id": null,                     // Bild aus der Mediathek (image_text, optional sonst)
    "date": "", "time": "", "location": "",       // event
    "items": [],                                  // list: Array von Strings
    "audience": "", "admission": "",              // event: „Für wen?“, „Eintritt“ (je max. 120)
    "qr_url": "", "qr_label": ""                  // alle Vorlagen: QR-Code (http/https, max. 500) + Beschriftung (max. 80)
  },
  "style": {
    "bg_color": "#0F2747", "text_color": "#FFFFFF", "accent_color": "#F5B400",
    "bg_image_content_id": null, "overlay": 0.4,  // Abdunklung über Hintergrundbild 0..0.8
    "align": "left",                              // left | center
    "size": "m",                                  // s | m | l (Schriftgrößen-Stufe)
    // Feingestaltung – alle optional, null = wie Design (theme) bzw. Standard der Vorlage:
    "heading_font": null, "body_font": null,      // Schrift-Schlüssel (§5.5)
    "heading_weight": null, "body_weight": null,  // 100..900 in Hunderterschritten
    "body_px": null,                              // 28..96, eigene Fließtextgröße statt size
    "heading_scale": null,                        // 1.2..3.5 × Fließtext (Standard 2.15, Aussage 2.5)
    "line_height": null,                          // 1.0..2.0 (Standard 1.4)
    "heading_tracking": null,                     // -0.05..0.25 em (Standard -0.012)
    "heading_case": null,                         // none | upper
    "title_color": null, "subtitle_color": null,  // Standard: Textfarbe bzw. Akzentfarbe
    "bg_color2": null, "bg_angle": null,          // Farbverlauf bg_color → bg_color2, Winkel 0..360 (Standard 180)
    "padding": null,                              // s | l (Standard m)
    "valign": null,                               // top | bottom (Standard Mitte)
    "box": null, "box_color": null, "box_radius": null,  // Textfeld: none | solid | glass, Farbe, Ecken 0..80 px
    "rule": null,                                 // false = Akzentlinie aus
    "logo_corner": null                           // top-left | top-right | bottom-left | bottom-right (Logo des Designs)
  }
}
```

### 5.5 Design (`designs.config`)
```json
{
  "header": {
    "enabled": true, "height": 180,                 // px auf der 1080×1920-Bühne (100..400)
    "bg_color": "#0F2747", "text_color": "#FFFFFF",
    "logo_content_id": null, "logo_position": "left",   // left | center
    "title": "Willkommen", "subtitle": "",
    "show_clock": true, "show_date": true,
    "date_format": "long"                         // long: „Dienstag, 30. September“ | short: „30.09.2026“
  },
  "footer": {
    "enabled": true, "height": 96,                  // 60..240
    "bg_color": "#0F2747", "text_color": "#FFFFFF",
    "mode": "ticker",                               // ticker | text
    "text": "",
    "ticker_items": ["…"],
    "ticker_rss_url": "",
    "ticker_speed": 120,                            // px/s (40..400)
    "ticker_separator": "•"
  },
  "theme": {
    "font": "sans", "accent_color": "#F5B400",      // font: Schrift-Schlüssel für Text, Header-Untertitel, Footer
    // Standard der Info-Folien (null = Standard der Vorlage), Bedeutung wie §5.4:
    "heading_font": null,                           // null = wie font; auch Header-Titel
    "heading_weight": null, "body_weight": null, "line_height": null, "heading_tracking": null, "heading_case": null
  }
}
```
- **Schrift-Schlüssel:** mitgeliefert (`web/shared/fonts.js` `BUILTIN_FONTS`, Dateien in `web/shared/fonts/`, OFL, Teilmenge „latin“): `sans` (Inter), `atkinson` (Atkinson Hyperlegible Next), `montserrat`, `nunito`, `condensed` (Roboto Condensed), `oswald`, `bebas` (Bebas Neue), `serif` (Source Serif 4), `lora`, `playfair` (Playfair Display) – oder hochgeladen `custom-<id>` (Tabelle `fonts`, §7.6). Unbekannte Schlüssel → 422.
- Neue optionale Felder (§5.4 Feingestaltung, Theme ab `heading_font`) mit Wert `null` entfallen in der aufgelösten Form (§8) → veröffentlichte Stände behalten ihren Status-Hash.

### 5.6 Touch-Menü (`touch_menus.config`)
```json
{
  "title": "Informationen", "intro": "Bitte ein Thema wählen",
  "columns": 2,                                     // 1 | 2 | 3
  "idle_timeout_s": 60,                             // 15..600
  "attract": { "enabled": true, "text": "Tippen für Informationen" },
  "tiles": [
    { "id": "t-8f3a", "label": "Über uns", "icon": "info", "color": "#1E5AA8", "image_content_id": null,
      "action": { "type": "content", "content_id": 12 } },
    { "id": "t-19bc", "label": "Galerie", "icon": "image", "color": "#2F6F4E", "image_content_id": 4,
      "action": { "type": "gallery", "content_ids": [3, 4, 5] } },
    { "id": "t-77aa", "label": "Mehr", "icon": "grid", "color": "#5B3E96", "image_content_id": null,
      "action": { "type": "submenu", "tiles": [ /* Kacheln ohne weiteres submenu */ ] } }
  ]
}
```
`icon` = Name aus `TILE_ICONS` in `web/shared/icons.js`. Max. 12 Kacheln je Ebene.

### 5.7 Stelen-Einstellungen (`steles.settings`)
```json
{
  "volume": 0.8,                                    // 0..1
  "touch_enabled": true,
  "night_mode": { "enabled": false, "start": "22:00", "end": "06:00" },   // Bildschirm schwarz
  "daily_reload": "03:30",                          // Player lädt täglich neu ('' = aus)
  "show_cursor": false
}
```

## 6. Rechte und Rollen

### 6.1 Rechte-Katalog (`permissions.py`, Reihenfolge = Anzeige)
| Bereich | Schlüssel | Bezeichnung | Enthält automatisch |
|---|---|---|---|
| Monitoring | `monitoring.view` | Monitoring ansehen | – |
| Mediathek | `content.view` | Mediathek ansehen | – |
| | `content.edit` | Inhalte hochladen, anlegen und bearbeiten | content.view |
| | `content.delete` | Inhalte löschen | content.view |
| Präsentationen | `presentations.view` | Präsentationen, Designs und Touch-Menüs ansehen (inkl. Vorschau) | – |
| | `presentations.edit` | Präsentationen bearbeiten und zur Freigabe einreichen | presentations.view, content.view |
| | `presentations.publish` | Präsentationen veröffentlichen, Freigaben erteilen oder ablehnen | presentations.view |
| | `presentations.delete` | Präsentationen löschen | presentations.view |
| | `designs.edit` | Designs (Header und Footer) bearbeiten | presentations.view, content.view |
| | `touch.edit` | Touch-Menüs bearbeiten | presentations.view, content.view |
| Zeitplan | `schedule.view` | Zeitplan ansehen | – |
| | `schedule.edit` | Zeitplan und Standard-Präsentation bearbeiten | schedule.view, steles.view, presentations.view |
| Stelen | `steles.view` | Stelen ansehen | – |
| | `steles.control` | Stelen fernsteuern (neu laden, identifizieren, Screenshot) | steles.view |
| | `steles.manage` | Stelen hinzufügen, koppeln, einstellen und entfernen | steles.view |
| Benutzer | `users.view` | Benutzer ansehen | – |
| | `users.manage` | Benutzer anlegen, bearbeiten, sperren und löschen | users.view |
| | `roles.manage` | Rollen und Rechte verwalten | users.view |
| System | `audit.view` | Protokoll ansehen | – |
| | `settings.manage` | Systemeinstellungen ändern, Backup herunterladen | – |

Effektive Rechte = gesetzte Rechte ∪ enthaltene (transitiv). Administrator (`is_admin`) hat immer alle.
`GET /api/permissions` liefert Katalog inkl. Bereich, Bezeichnung, Beschreibung (ein Satz), `implies`.

### 6.2 Standardrollen und Demo-Konten (Seed)
| Rolle | Rechte |
|---|---|
| **Administrator** (`is_admin`) | alle |
| **Redaktion** | monitoring.view, content.edit, content.delete, presentations.edit, presentations.publish, presentations.delete, designs.edit, touch.edit, schedule.edit, steles.control, audit.view |
| **Autor** | monitoring.view, content.edit, presentations.edit, schedule.view, steles.view |
| **Betrachter** | monitoring.view, content.view, presentations.view, schedule.view, steles.view |

| Benutzername | Passwort | Anzeigename | Rolle |
|---|---|---|---|
| `admin` | `admin123` | Alex Admin | Administrator |
| `redaktion` | `redaktion123` | Rita Redaktion | Redaktion |
| `autor` | `autor123` | Arne Autor | Autor |
| `betrachter` | `betrachter123` | Bea Betrachter | Betrachter |
(alle `is_demo = 1`, `must_change_password = 0`)

### 6.3 Schutzregeln
- Mindestens **ein aktiver Administrator** muss bleiben (Löschen, Deaktivieren, Rollenwechsel → 409 `last_admin`; gezählt in derselben Schreib-Transaktion wie die Änderung, damit parallele Anfragen nicht beide durchgehen).
- Eigenes Konto: nicht löschen, nicht deaktivieren, eigene Rolle nicht ändern (403 `self_protection`).
- Administrator-Rolle: nicht löschbar, Rechte/Name nicht änderbar (403 `role_locked`).
- Administrator-Rolle vergeben sowie Konten mit Administrator-Rolle ändern, entsperren, löschen oder ihr Passwort zurücksetzen: nur durch Administratoren (sonst 403 `forbidden`).
- Konten verwalten ohne `is_admin` (anlegen, ändern, deaktivieren, entsperren, Passwort zurücksetzen, löschen): nur, wenn das eigene Konto alle effektiven Rechte der Rolle des Kontos selbst hat – beim Anlegen und Rollenwechsel auch die der neuen Rolle (sonst 403 `forbidden`, fehlende Rechte in `message` und `details.missing_permissions`). Damit lässt sich kein mächtigeres Konto per Passwort-Reset übernehmen. Ziel-Konto und Prüfung werden in der Schreib-Transaktion wiederholt; haben sich Rolle oder Aktiv-Status eines Kontos seit dem Laden geändert, lehnt `PATCH /api/users/<id>` mit 409 `stale` ab.
- Rollen anlegen/ändern ohne `is_admin`: nur Rechte hinzufügen, die das eigene Konto selbst hat (effektiv; sonst 403 `forbidden`) – damit auch die eigene Rolle nicht erweitern.
- Rollen ändern oder löschen ohne `is_admin`: nur, wenn das eigene Konto alle bisherigen effektiven Rechte der Rolle selbst hat – also auch keine fremden Rechte entziehen; Administrator-Rolle nur durch Administratoren (sonst 403 `forbidden`, fehlende Rechte in `details.missing_permissions`). Die Prüfung wird in der Schreib-Transaktion wiederholt; haben sich die Rechte der Rolle seit dem Laden geändert, lehnt `PATCH /api/roles/<id>` mit 409 `stale` ab.
- Rolle mit Benutzern nicht löschbar (409 `role_in_use`, `details.user_count`).

## 7. Admin-API

### 7.1 Konventionen
- JSON rein/raus (`application/json; charset=utf-8`), IDs sind Integer.
- Listen: `{"items": [...], "total": n}`.
- Fehler: HTTP-Status + `{"error": {"code": "…", "message": "Deutscher Satz für Menschen", "fields": {"feld": "Meldung"}, "details": {…}}}`.
  Codes: `unauthenticated` 401 · `invalid_credentials` 401 · `forbidden` 403 · `account_disabled` 403 · `self_protection` 403 · `role_locked` 403 · `not_found` 404 · `conflict`/`in_use`/`last_admin`/`role_in_use`/`edit_conflict`/`changed_since_review`/`stale` 409 · `expired` 410 · `too_large` 413 · `unsupported_media` 415 · `validation_error` 422 · `account_locked` 423 (`details.retry_after_s`) · `rate_limited` 429 · `server_error` 500 · `insufficient_storage` 507.
- Personen-Verweis überall: `{"id": 1, "display_name": "Alex Admin"}` oder `null`.
- **CSRF:** alle ändernden Methoden auf `/api/` (außer `/api/auth/login`, `/api/auth/dev-login`, `/api/player/*`, `/api/agent/*`) brauchen Header `X-CSRF-Token` = Token aus der Sitzung; sonst 403 `forbidden` („Sicherheitsprüfung fehlgeschlagen – Seite neu laden“).
- Jede ändernde Aktion schreibt einen Protokolleintrag (§4 audit_log).
- Rechte-Prüfung serverseitig bei **jedem** Endpunkt (die UI blendet nur aus).

### 7.2 Anmeldung (`/api/auth`)
| Methode | Pfad | Recht | Body → Antwort |
|---|---|---|---|
| GET | `/api/auth/session` | öffentlich | → `Session` |
| POST | `/api/auth/login` | öffentlich | `{username, password}` → `Session` |
| POST | `/api/auth/dev-login` | öffentlich, nur Testbetrieb + Loopback + `is_demo` | `{username}` → `Session` |
| POST | `/api/auth/logout` | angemeldet | → `{ok: true}` |
| POST | `/api/auth/password` | angemeldet | `{current_password, new_password}` → `Session` (Sitzung bleibt gültig, andere Sitzungen enden) |
| PATCH | `/api/auth/profile` | angemeldet | `{display_name?, email?}` → `Session` |

```json
Session = {
  "authenticated": true,
  "user": User | null,
  "permissions": ["content.view", …],      // effektiv, sortiert
  "csrf_token": "…" | null,
  "idle_timeout_s": 3600,
  "dev_login": { "enabled": true, "users": [{"username": "admin", "display_name": "Alex Admin", "role_name": "Administrator"}] },
  "app": { "name": "Stele CMS", "version": "1.0.0", "org_name": "…", "test_mode": true }
}
```
- `dev_login.users` nur, wenn Testbetrieb aktiv **und** Anfrage von Loopback; sonst `{"enabled": false, "users": []}`. `app.test_mode` = `dev_login_enabled`.
- Login-Fehler immer „Benutzername oder Passwort ist falsch.“ (kein Hinweis, ob der Benutzer existiert). Nach `lockout_attempts` Fehlversuchen → gesperrt für `lockout_minutes` (423). Zusätzlich pro IP max. 20 Versuche / 5 min (429; jeder Versuch zählt, geprüft und gezählt in einem Schritt vor der Passwortprüfung). Fehlversuchszähler atomar in der Schreib-Transaktion. Nach richtigem Passwort wird die Sperre in derselben Schreib-Transaktion geprüft, die den Zähler zurücksetzt (parallel gesetzte Sperre → 423, Abweisung als `login_failed` protokolliert).
- Sitzung: Flask-Session-Cookie `stelecms_session` (HttpOnly, SameSite=Lax; Secure erst mit HTTPS). Inhalt: user_id, session_version, csrf_token, last_active. Leerlauf > `session_idle_minutes` → 401 `unauthenticated` mit `message` „Sitzung abgelaufen – bitte neu anmelden.“

```json
User = { "id", "username", "display_name", "email", "role": {"id", "name", "is_admin"},
         "is_active", "is_demo", "must_change_password", "locked": bool, "locked_until",
         "last_login_at", "created_at", "updated_at" }
```

### 7.3 Benutzer und Rollen
| Methode | Pfad | Recht | Body → Antwort |
|---|---|---|---|
| GET | `/api/users` | users.view | → `{items: [User], total}` |
| POST | `/api/users` | users.manage | `{username, display_name, email?, role_id, password?, must_change_password?=true}` → `{user: User, generated_password: str|null}` (ohne password → 12 Zeichen erzeugt, nur einmal angezeigt) |
| GET | `/api/users/<id>` | users.view | → `User` |
| PATCH | `/api/users/<id>` | users.manage | `{display_name?, email?, role_id?, is_active?}` → `User` |
| POST | `/api/users/<id>/password` | users.manage | `{password?}` → `{generated_password: str|null}`; setzt must_change_password, beendet Sitzungen |
| POST | `/api/users/<id>/unlock` | users.manage | → `User` |
| DELETE | `/api/users/<id>` | users.manage | → `{ok: true}` |
| GET | `/api/permissions` | angemeldet | → `{groups: [{key, label, permissions: [{key, label, description, implies: []}]}]}` |
| GET | `/api/roles` | users.view oder roles.manage | → `{items: [Role], total}` |
| POST | `/api/roles` | roles.manage | `{name, description?, permissions?: [], copy_from?: id}` → `Role` |
| PATCH | `/api/roles/<id>` | roles.manage | `{name?, description?, permissions?}` → `Role` |
| DELETE | `/api/roles/<id>` | roles.manage | → `{ok: true}` |

`Role = {id, name, description, is_admin, permissions: [gesetzt], effective_permissions: [], user_count, created_at, updated_at}`
Validierung: username 3–32 Zeichen `[a-zA-Z0-9._-]`, eindeutig; display_name 1–80; Passwort ≥ `password_min_length`.

### 7.4 Mediathek (`/api/contents`)
| Methode | Pfad | Recht | Body → Antwort |
|---|---|---|---|
| GET | `/api/contents?type=image,video&q=&tag=&status=&sort=updated_desc&limit=200&offset=0` | content.view | → `{items: [Content], total, tags: [alle Tags]}`; sort: `updated_desc` · `created_desc` · `title_asc` · `size_desc` |
| POST | `/api/contents/upload` (multipart: `files` mehrfach, optional `tags` = JSON-Array) | content.edit | → `{items: [Content], errors: [{file_name, message}]}` |
| POST | `/api/contents` | content.edit | `{type: "text"|"web", title, tags?, data}` → `Content` |
| GET | `/api/contents/<id>` | content.view | → `Content` + `usages` |
| PATCH | `/api/contents/<id>` | content.edit | `{title?, tags?, data?, expected_updated_at?}` (data nur text/web) → `Content`; 409 `edit_conflict` (§3) |
| POST | `/api/contents/<id>/duplicate` | content.edit | nur text/web → `Content` |
| DELETE | `/api/contents/<id>` | content.delete | → `{ok}`; 409 `in_use` mit `details.usages` |
| POST | `/api/contents/bulk-delete` | content.delete | `{ids}` → `{deleted: [ids], blocked: [{id, title, usages}]}` |
| POST | `/api/contents/check-url` | content.edit | `{url}` → `{ok, embeddable: true|false|null, title: str|null, message}` |

```json
Content = {
  "id", "uid", "type", "title", "tags": [], "data": {},
  "status": "ready|processing|error", "status_message": "", "progress": null,
  "file": {"name", "mime", "size_bytes", "width", "height", "duration_s", "page_count"} | null,
  "urls": {"thumb": "/media/…"|null, "display": …|null, "original": …|null, "poster": …|null, "pages": [...]|null},
  "warnings": [{"code": "landscape", "message": "Querformat: wird auf der Hochformat-Stele angepasst (Rand oder Zuschnitt)."}],
  "usage_count": 2,
  "created_by": Person, "updated_by": Person, "created_at", "updated_at"
}
usages = [{"type": "presentation"|"design"|"touch_menu"|"content", "id", "name", "published": bool}]
```
- **Upload:** erlaubt JPEG, PNG, WebP, GIF, MP4, WebM, MOV, MKV, PDF – Prüfung über den **Inhalt** (Pillow `verify`, ffprobe, `%PDF-` + pdfinfo), nicht nur Endung. SVG und alles andere → Fehler „Dateityp nicht unterstützt (…). Erlaubt: …“. Größe ≤ `upload_max_mb` (höchstens 128 MB); Anfrage insgesamt höchstens 129 MB (Waitress-Grenze bereits vor der Anmeldung). Weniger freier Speicher als Anfrage + 1 GB → 507 `insufficient_storage`. Titel = Dateiname ohne Endung.
- **Ausgehende Abrufe** (Webseiten-Prüfung, RSS-Feeds): keine Ziele auf dem Server selbst (Loopback), Link-Local (z. B. 169.254.169.254) oder unspezifiziert – auch nicht über Weiterleitungen; LAN-Adressen erlaubt. Gesamtzeit begrenzt (Prüfung 10 s, Feed 30 s).
- **Dateien:** `data/media/<uid>/original.<ext>`; Bild: `display.jpg|png` (max. 2160 px lange Kante, EXIF-Drehung angewandt, PNG nur bei Transparenz, GIF bleibt Original), `thumb.webp` (max. 480 px). Video: `poster.jpg` (Einzelbild bei min(1 s, Dauer/3)), `thumb.webp`, `video.mp4` wenn umgewandelt/umgepackt. PDF: `pages/p001.png` … (1080 px breit, max. 100 Seiten), `thumb.webp`.
- **Video-Job:** ffprobe → Dauer, Größe, Codec. Browser-tauglich = Container mp4/webm/mov mit H.264/VP8/VP9/AV1 und Audio AAC/Opus/Vorbis/MP3/keins. Sonst (bei `auto_transcode`) ffmpeg → H.264/AAC MP4 `-movflags +faststart -pix_fmt yuv420p -preset veryfast -crf 21`, Fortschritt über `-progress pipe:1` → `contents.progress`. Kompatible MP4 ohne faststart → umpacken (`-c copy -movflags +faststart`).
- **Warnungen** (berechnet): `landscape` (Breite > Höhe), `low_resolution` (Breite < 720 oder Höhe < 1280 bei Hochformat bzw. Fläche < 40 % von 1080×1920), `transcoded`, `incompatible`, `not_embeddable`, `processing_failed`.
- **Verwendungen:** Folien in Entwürfen, veröffentlichte Snapshots (`published: true`), Design-Logo, Info-Folien-Bilder (`type: content`), Touch-Menü-Kacheln/-Aktionen.
- **URL-Prüfung:** nur http/https, Timeout 5 s, max. 512 KB lesen; `X-Frame-Options` DENY/SAMEORIGIN oder CSP `frame-ancestors` ohne `*` → `embeddable: false` mit Erklärung; Netzfehler → `embeddable: null`, „Seite nicht erreichbar – später erneut prüfen“. `<title>` auslesen.

### 7.5 Präsentationen
| Methode | Pfad | Recht | Body → Antwort |
|---|---|---|---|
| GET | `/api/presentations` | presentations.view | → `{items: [PresentationSummary], total}` |
| POST | `/api/presentations` | presentations.edit | `{name, description?, design_id?, touch_menu_id?, copy_from?}` → `Presentation` (Design-Standard: `settings.default_design_id` bzw. erstes Design) |
| GET | `/api/presentations/<id>` | presentations.view | → `Presentation` |
| PATCH | `/api/presentations/<id>` | presentations.edit | `{name?, description?, settings?, design_id?, touch_menu_id?, expected_updated_at?}` → `Presentation`; 409 `edit_conflict` (§3) |
| PUT | `/api/presentations/<id>/items` | presentations.edit | `{items: [{id?, content_id, enabled, duration_s, transition, valid_from, valid_until, caption, options}], expected_updated_at?}` → `Presentation` (Reihenfolge = Array); 409 `edit_conflict` |
| POST | `/api/presentations/<id>/publish` | presentations.publish | `{note?, confirm_changed?}` → `Presentation`; 422, wenn keine aktive Folie oder ein aktiver Inhalt nicht `ready` ist (`details.items`); 409 `changed_since_review`, wenn eingereicht und seitdem geändert (§3) |
| POST | `/api/presentations/<id>/request-review` | presentations.edit | `{note?}` → `Presentation` (speichert `review_hash`) |
| POST | `/api/presentations/<id>/reject` | presentations.publish | `{note}` (Pflicht) → `Presentation` |
| POST | `/api/presentations/<id>/discard` | presentations.edit | → `Presentation` (Freigabe → `none`); 409, wenn nie veröffentlicht |
| DELETE | `/api/presentations/<id>` | presentations.delete | → `{ok}`; 409 `in_use`, wenn Standard einer Stele oder im Zeitplan (`details.usages`) |
| GET | `/api/presentations/<id>/manifest?source=draft|published` | presentations.view | → Manifest (§8) für die Vorschau |

```json
PresentationSummary = {
  "id", "name", "description",
  "status": "draft|published|changed", "review_state": "none|requested|rejected", "review_note",
  "review_by": Person, "review_at",
  "review_changed": bool,                                          // eingereicht, Entwurf weicht seitdem ab (§3)
  "item_count", "active_item_count", "total_duration_s",          // Summe effektiver Dauern aktiver Folien (Video: Länge)
  "design": {"id", "name"} | null, "touch_menu": {"id", "name"} | null,
  "used_by": [{"stele_id", "stele_name", "how": "default|schedule"}],
  "thumb_url": str | null,                                         // erstes Bild/Poster/PDF-Thumb
  "published_at", "published_by": Person, "updated_at", "updated_by": Person
}
Presentation = PresentationSummary + {
  "settings": {…vollständig…}, "design_id", "touch_menu_id",
  "items": [{
    "id", "position", "enabled", "duration_s", "transition", "valid_from", "valid_until", "caption", "options",
    "content": {"id", "type", "title", "status", "thumb_url", "duration_s", "page_count", "warnings": []},
    "effective_duration_s": 10,
    "validity": "active|scheduled|expired"                        // jetzt, in settings.timezone
  }]
}
```

### 7.5a Vorschau ungespeicherter Stände
`POST /api/preview/resolve` (presentations.view) – löst Entwürfe auf, ohne zu speichern (für Live-Vorschauen in den Editoren):
```json
Body: { "settings": {…}|null, "design": {Design-config}|null, "touch_menu": {Touch-config}|null,
        "items": [{ "id": 11|null, "content_id": 7, "content": {"type": "text"|"web", "data": {…}}|null,
                    "enabled", "duration_s", "transition", "valid_from", "valid_until", "caption", "options" }] }
Antwort: { "settings": {vollständig}, "design": {…config, "logo_url"}|null, "touch_menu": {aufgelöst wie Manifest}|null,
           "slides": [Slide (§8), gleiche Reihenfolge, auch deaktivierte mit "enabled": false; unbekannter Inhalt → "type": "missing"],
           "fonts": {verwendete hochgeladene Schriften wie Manifest §8} }
```
`content` überschreibt die gespeicherten Daten eines text/web-Inhalts (ungespeicherte Änderungen im Editor).
Hintergrund-Abfragen der UI senden `X-Background-Poll: 1` – sie verlängern die Sitzung nicht.

### 7.6 Designs und Touch-Menüs
| Methode | Pfad | Recht |
|---|---|---|
| GET | `/api/designs`, `/api/designs/<id>` | presentations.view |
| POST | `/api/designs` `{name, config?, copy_from?}` | designs.edit |
| PATCH | `/api/designs/<id>` `{name?, config?, expected_updated_at?}` (409 `edit_conflict`, §3) | designs.edit |
| DELETE | `/api/designs/<id>` (409 `in_use`, wenn Präsentationen es nutzen) | designs.edit |
| GET/POST/PATCH/DELETE | `/api/touch-menus[/<id>]` analog (PATCH mit `expected_updated_at?`) | presentations.view / touch.edit |

| GET | `/api/fonts` → `{items: [Font]}` | presentations.view oder content.view |
| POST | `/api/fonts` (multipart: `file`, `name?`, `weight?` = `variable` \| 100..900) → `Font`; Format per Dateikopf (WOFF2, WOFF, TTF, OTF, sonst 422 `unsupported_type`), höchstens 8 MB (413) | designs.edit |
| PATCH | `/api/fonts/<id>` `{name}` | designs.edit |
| DELETE | `/api/fonts/<id>` (409 `in_use` mit `usages`, solange Designs, Info-Folien oder veröffentlichte Stände sie nutzen) | designs.edit |

`Design = {id, name, config (vollständig), logo_url: str|null, used_by: [{id, name, status}], created_at, updated_at, updated_by: Person}`
`Font = {id, key: "custom-<id>", name, url: "/media/<uid>/font.<format>", format, weight: int|null, size_bytes, usages: [{type, id, name, published}], created_at, created_by: Person}`
`TouchMenu = {id, name, config (vollständig, Kacheln mit „content“: {id, type, title, thumb_url} zur Anzeige ergänzt), used_by: [...], created_at, updated_at, updated_by}` – beim Speichern wird nur `config` in der Form von §5.6 übernommen (ergänzte Felder ignorieren).

### 7.7 Stelen
| Methode | Pfad | Recht | Body → Antwort |
|---|---|---|---|
| GET | `/api/steles` | steles.view | → `{items: [Stele], total}` |
| POST | `/api/steles` | steles.manage | `{name, location?, ip_address?, width?, height?, default_presentation_id?, settings?, pairing_code?}` → `Stele` |
| GET | `/api/steles/<id>` | steles.view | → `Stele` (+ `player_url` nur mit steles.manage) |
| PATCH | `/api/steles/<id>` | steles.manage (nur `default_presentation_id`: auch schedule.edit) | → `Stele` |
| DELETE | `/api/steles/<id>` | steles.manage | → `{ok}` |
| POST | `/api/steles/<id>/pair` | steles.manage | `{code}` → `Stele`; 404 „Code unbekannt“, 410 „Code abgelaufen“; Code bleibt danach noch mind. 2 min gültig (Player holt den Schlüssel ab) |
| POST | `/api/steles/<id>/rotate-key` | steles.manage | → `Stele` mit neuer `player_url` |
| POST | `/api/steles/<id>/commands` | steles.control | `{command}` → `{id, command, created_at}` |
| GET | `/api/steles/<id>/commands?limit=20` | steles.view | → `{items: [{id, command, created_by, created_at, delivered_at, done_at, result}]}` |
| GET | `/api/steles/<id>/manifest` | steles.view | → Manifest der Stele (für die Live-Ansicht) |
| GET | `/api/steles/<id>/screenshot` | monitoring.view | → JPEG (letzter Agent-Screenshot) oder 404 |
| GET | `/api/pairing/pending` | steles.manage | → `{items: [{code, device_info, created_at, expires_at}]}` (wartende Player) |

```json
Stele = {
  "id", "name", "location", "ip_address", "width", "height", "orientation": "portrait|landscape",
  "settings": {…vollständig…}, "paired": bool, "paired_at",
  "default_presentation": {"id", "name", "status"} | null,
  "status": "online|offline|standby|never",           // standby = Nachtmodus; never = noch nie gemeldet
  "last_seen_at",
  "now": { "presentation": {"id", "name"} | null, "source": "schedule|default|none", "schedule_entry_id",
           "item": {"id", "title", "content_type"} | null, "mode": "slideshow|touch|standby|idle|pairing" | null },
  "next_change": {"at", "presentation": {"id", "name"} | null} | null,
  "player": {"version", "user_agent", "screen": {"w", "h"}, "manifest_version", "manifest_current": bool, "uptime_s"} | null,
  "network": {"ip_address", "ping_ok": bool|null, "ping_ms", "checked_at"},
  "agent": {"last_at", "hostname", "os", "cpu", "ram", "disk", "temp", "uptime_s", "screenshot_at"} | null,
  "created_at", "updated_at"
}
```
`now.presentation/source` berechnet der Server aus dem Zeitplan; `now.item/mode` stammen aus dem letzten Heartbeat (nur wenn online).
`player_url` = `http://<host>:<port>/player/?key=<player_key>` (Host aus der Anfrage).

### 7.8 Zeitplan
| Methode | Pfad | Recht |
|---|---|---|
| GET | `/api/schedule?stele_id=` → `{items: [Entry], total, default_presentation: {id, name, status}|null}` | schedule.view |
| POST | `/api/schedule` `{stele_id, presentation_id, label?, days, start_time, end_time, date_from?, date_until?, priority?, enabled?}` | schedule.edit |
| PATCH/DELETE | `/api/schedule/<id>` | schedule.edit |
| GET | `/api/schedule/timeline?stele_id=&from=YYYY-MM-DD&days=7` | schedule.view |

`Entry = {id, stele_id, presentation: {id, name, status}, label, days, start_time, end_time, date_from, date_until, priority, enabled, created_at, updated_at}`
Timeline-Antwort: `{timezone, days: [{date, weekday, segments: [{start: "HH:MM", end: "HH:MM", presentation: {id, name}|null, source: "schedule|default|none", entry_id}]}], conflicts: [{date, entry_ids, message}]}`

**Auflösungsregel (Server und Player identisch):** Zum Zeitpunkt t aktiv sind Einträge mit `enabled`, Wochentag passt, Uhrzeit in `[start, end)` (über Mitternacht: Tag des Beginns zählt), Datum im Bereich. Gewinner: höchste `priority`; bei Gleichstand der Eintrag mit Datumsbereich; dann der zuletzt geänderte. Ohne aktiven Eintrag → Standard-Präsentation der Stele; ohne diese → `none` (Standby-Bild). Nur **veröffentlichte** Präsentationen können laufen; unveröffentlichte zählen als nicht vorhanden (Timeline markiert das). Konflikt = zwei aktive Einträge mit gleicher Priorität überschneiden sich.

### 7.9 Monitoring, Übersicht, Protokoll, Einstellungen
| Methode | Pfad | Recht | Antwort |
|---|---|---|---|
| GET | `/api/monitoring/overview` | monitoring.view | `{steles: [Stele + {availability_24h_pct, plays_today, touch_sessions_today, errors_24h}], server: {version, started_at, uptime_s, db_size_bytes, media_size_bytes, disk_free_bytes, disk_total_bytes, content_count, jobs: {queued, running, failed}}, alerts: [Alert]}` |
| GET | `/api/monitoring/steles/<id>?hours=24` | monitoring.view | `{stele: Stele, availability: {pct, segments: [{start, end}]}, metrics: [{ts, cpu, ram, disk, temp}], playback: [{started_at, duration_s, title, content_type, presentation_name}], events: [{ts, level, kind, message}], touch: {sessions, avg_duration_s, top_tiles: [{label, count}], per_hour: [{hour, sessions}]}, screenshot: {url, taken_at}|null}` (Listen: neueste zuerst, max. 100) |
| GET | `/api/dashboard` | angemeldet (Abschnitte nach Rechten, fehlende = `null`) | `{steles: [Stele]|null, reviews: [{presentation: {id, name}, requested_by: Person, requested_at, note}]|null, unpublished: [{id, name, updated_at, updated_by}]|null, expiring: [{presentation: {id, name}, item_id, title, valid_until}]|null (nächste 7 Tage), stale: [{id, name, published_at}]|null (auf Stelen eingeplant, länger als `stale_after_days` nicht veröffentlicht), stale_after_days, activity: [AuditEntry]|null (10, nur audit.view), alerts: [Alert], counts: {contents, presentations, steles, users}}` |
| GET | `/api/audit?q=&user_id=&entity_type=&action=&from=&to=&limit=50&offset=0` | audit.view | `{items: [AuditEntry], total}` |
| GET | `/api/audit/export.csv?…` | audit.view | CSV, `;`-getrennt, UTF-8 mit BOM |
| GET | `/api/settings` | angemeldet (ohne settings.manage nur `org_name`, `timezone`, `default_slide_duration_s`) | Settings |
| PATCH | `/api/settings` | settings.manage | → Settings |
| GET | `/api/system/backup` | settings.manage | SQLite-Sicherung als Download `stelecms-backup-YYYYMMDD-HHMM.db` (sqlite3 backup-API) |
| GET | `/api/system/info` | settings.manage | `{version, data_dir, db_path, media_dir, host, port, python, ffmpeg: bool, pdftoppm: bool, started_at}` |

`Alert = {level: "warning|error", code, message, stele_id?, since}` – z. B. `stele_offline` (error), `stele_never_seen`, `manifest_outdated` (Player > 5 min auf altem Stand), `disk_low` (< 10 % frei), `job_failed`, `no_presentation` (Stele ohne laufende Präsentation).
`AuditEntry = {id, ts, user: {id, username, display_name}|null, action, entity_type, entity_id, entity_name, summary, details, ip}`

## 8. Manifest (Vertrag Server ↔ Player)

`GET /api/player/manifest` (Stelen-Schlüssel), `GET /api/steles/<id>/manifest` und `GET /api/presentations/<id>/manifest` liefern dieselbe Form:

```json
{
  "schema": 1,
  "version": "3f9c0a1b2c3d4e5f",            // sha256 über kanonisches JSON ohne version/generated_at, 16 Hex
  "generated_at": "2026-09-30T08:00:00Z",
  "preview": false,                          // true bei Präsentations-Vorschau
  "timezone": "Europe/Berlin",
  "org_name": "Meine Organisation",
  "stele": { "id": 1, "name": "Stele Foyer", "location": "Eingangshalle", "width": 1080, "height": 1920,
             "settings": { …§5.7 vollständig… } } | null,      // null bei Präsentations-Vorschau (Player nutzt 1080×1920 + Standards)
  "default_presentation_id": 3 | null,
  "schedule": [ {"id": 5, "presentation_id": 4, "days": [1,2,3,4,5], "start": "18:00", "end": "22:00",
                 "date_from": null, "date_until": null, "priority": 0, "updated_at": "…"} ],
  "presentations": { "3": ResolvedPresentation },            // nur veröffentlichte (Vorschau: gewählte Quelle)
  "feeds": { "https://…/rss": { "items": ["Meldung 1", "Meldung 2"], "fetched_at": "…" } },
  "fonts": { "custom-2": { "url": "/media/…/font.woff2", "weight": 700 | null, "name": "Hausschrift" } },  // verwendete hochgeladene Schriften
  "assets": ["/media/ab12…/display.jpg", …]                 // alle Dateien zum Vorab-Laden (inkl. fonts)
}

ResolvedPresentation = {
  "id": 3, "name": "Foyer Standard", "published_at": "…" | null,
  "settings": { …§5.2 vollständig… },
  "design": { …§5.5 vollständig…, "logo_url": "/media/…" | null } | null,
  "touch_menu": { …§5.6, Kacheln aufgelöst… } | null,
  "slides": [ Slide ]                                        // nur enabled; Gültigkeit prüft der Player zur Laufzeit
}

Slide (gemeinsame Felder) = { "id": 11 (item_id), "content_id": 7, "type": "image|video|pdf|text|web", "title": "…",
  "duration_s": 10, "transition": "fade", "valid_from": null, "valid_until": null, "caption": "", "fullscreen": false }
  image: + { "src", "width", "height", "fit": "cover|contain", "ken_burns": bool }
  video: + { "src", "poster", "width", "height", "video_duration_s", "play_to_end": bool, "sound": bool }
  pdf:   + { "pages": ["/media/…/pages/p001.png", …], "page_duration_s": 8 }   // duration_s = Seiten × page_duration_s
  text:  + { "template", "fields": {…, "image_url"}, "style": {…, "bg_image_url"} }  // *_content_id → *_url aufgelöst
         //   leere Felder audience, admission, qr_url, qr_label entfallen (veröffentlichte Stände bleiben gleich);
         //   QR-Code erzeugt der Player selbst (web/shared/qr.js, Fehlerkorrektur M, dunkel auf weiß)
  web:   + { "url", "zoom", "refresh_s", "interactive" }

Aufgelöste Touch-Kachel = { "id", "label", "icon", "color", "image_url": str|null,
  "action": { "type": "content", "item": Slide }
          | { "type": "gallery", "items": [Slide] }
          | { "type": "submenu", "tiles": [Kachel] } }
  (Slides in Touch-Aktionen: id = null, duration_s/transition ohne Bedeutung)
```
- **Hash für den Status** (`published_hash`): sha256 über die kanonische ResolvedPresentation **ohne** `published_at`, `name` und die Slide-Felder `title` (Umbenennen allein erzeugt keine „offenen Änderungen“).
- `feeds` enthält die zwischengespeicherten RSS-Titel aller verwendeten `ticker_rss_url` (fließen in `version` ein → Player lädt neu, wenn sich Meldungen ändern).
- ETag = `"<version>"`; `If-None-Match` → 304.

## 9. Player (`web/player/`)

Läuft in Chrome (Kiosk) auf dem Stelen-PC und – als Vorschau – in iframes der Admin-Oberfläche. Kein Framework.

### 9.1 Modi (URL-Parameter)
| Aufruf | Verhalten |
|---|---|
| `/player/` | Normalbetrieb mit Stelen-Schlüssel (Cookie `stele_key` oder `localStorage`); ohne Schlüssel → **Kopplungsbildschirm** |
| `/player/?key=…` | Schlüssel speichern (`localStorage`), Server setzt Cookie; URL per `history.replaceState` bereinigen |
| `/player/?mode=preview&presentation=<id>&source=draft|published[&slide=<index>][&autoplay=0|1][&touch=1]` | Vorschau einer Präsentation (Sitzung des Admins), keine Telemetrie, kein Service Worker |
| `/player/?mode=slide` | Einzelfolie: wartet auf `postMessage({type:'render', …})` |
| `/player/?mode=mirror&stele=<id>` | Live-Ansicht (nachgebildet): lädt `/api/steles/<id>/manifest`, spielt stumm, folgt `showItem`-Nachrichten |

### 9.2 postMessage-API (nur gleiche Herkunft; Admin ↔ Player-iframe)
Admin → Player: `{type:'goto', index}` · `{type:'next'}` · `{type:'prev'}` · `{type:'play'}` · `{type:'pause'}` · `{type:'reload'}` · `{type:'openTouch'}` · `{type:'closeTouch'}` · `{type:'render', slide: Slide, design: Design|null, settings: Diashow-Einstellungen|null, touch_menu: TouchMenu|null, fonts: {…}|null, view: 'slide'|'touch'}` · `{type:'showItem', item_id}` (mirror) · `{type:'setManifest', manifest}` (Vorschau mit ungespeichertem Stand).
Player → Admin: `{type:'player:ready', total}` · `{type:'player:state', index, total, item_id, playing, mode}` · `{type:'player:error', message}` · nur `mode=slide`: `{type:'player:fit', body_px, clipped}` (Fließtextgröße nach Fit-Text, Text abgeschnitten).
Alle Nachrichten tragen zusätzlich `source: 'stelecms'`.

### 9.3 Darstellung
- **Bühne** fest in Stelen-Auflösung (Standard 1080 × 1920), per `transform: scale()` in das Fenster eingepasst (zentriert, schwarzer Rand). Alle Maße in Bühnen-Pixeln.
- **Rahmen:** Header (Logo, Titel/Untertitel, Uhr/Datum in `timezone`), Footer (Laufband mit konstanter Geschwindigkeit in px/s, nahtlose Schleife, oder statischer Text). `show_header/show_footer` der Präsentation und `fullscreen` der Folie blenden aus. Folienbereich = Bühne minus Header/Footer.
- **Folien:** Bild (`object-fit` nach `fit`, Ken-Burns optional), Video (stumm außer `sound`; `play_to_end` oder feste Dauer, bei kürzerem Video Schleife), PDF (Seiten nacheinander, je `page_duration_s`), Info-Folie (HTML/CSS nach Vorlage, gestochen scharf, fünf Vorlagen aus §5.4), Webseite (iframe, `zoom` per CSS-Scale, `sandbox="allow-scripts allow-same-origin allow-forms"`, kein top-navigation/popups; `refresh_s` > 0 → neu laden).
- **Übergänge:** none, fade, slide-left, slide-up, zoom mit `transition_ms`; zwei Ebenen (A/B). Nächste Folie **vorab laden** (Bild `decode()`, Video `canplay` mit 8 s Timeout, iframe `load` mit 10 s Timeout) → kein Schwarzbild.
- **Schriften:** mitgelieferte per `web/shared/fonts/fonts.css` (im Shell-Cache, offline), hochgeladene per `FontFace` aus `fonts` von Manifest bzw. `render`. Design-Theme → CSS-Variablen der Bühne (`--font-body`, `--font-heading`, `--tx-*`, `--logo-url`); die Info-Folie überschreibt nur gesetzte Werte. Fit-Text misst erst nach dem Laden der Schriften (höchstens 3 s).
- **Bildunterschrift** (`caption`): unten im Folienbereich, Stil nach `caption_style`, mind. 40 px Schrift.
- **Fortschrittsbalken** optional (4 px, Akzentfarbe).
- **Standby** (keine Präsentation / keine gültige Folie): dunkler Hintergrund, `org_name` und Uhrzeit, dezent; Heartbeat `mode: 'idle'` (Admin: Warnung „Läuft, aber nichts anzuzeigen“, Status bleibt `online`).
- **Nachtmodus:** schwarz, Videos gestoppt, Heartbeat `mode: 'standby'`.

### 9.4 Ablauf und Robustheit
- Aktive Präsentation nach Zeitplan-Regel (§7.8) mit Wanduhr in `timezone`; Prüfung alle 15 s; Wechsel an der nächsten Foliengrenze (max. 30 s warten).
- Folien filtern nach Gültigkeit (`valid_from`/`valid_until`) bei jedem Durchlauf; `shuffle` mischt je Durchlauf (nie dieselbe Folie zweimal hintereinander).
- Neues Manifest → an der nächsten Foliengrenze übernehmen, ohne sichtbaren Bruch.
- Fehler beim Laden einer Folie → überspringen, Fehler melden. Watchdog: bleibt eine Folie länger als Dauer + 30 s stehen → weiter. Unbehandelter Fehler → melden, nach 10 s `location.reload()` (max. 1× pro 5 min).
- Täglicher Neustart um `daily_reload` (nicht während einer Touch-Sitzung).
- **Offline:** letztes Manifest in `localStorage`; Service Worker (`/player/sw.js`, Scope `/player/`) cached Player-Dateien (network-first) und `/media/*` (cache-first, immutable) inkl. **Range-Anfragen für Videos** (206 aus dem Cache); lädt `assets` nach jedem Manifest im Hintergrund vor (max. 2 parallel) und entfernt nicht mehr benötigte Dateien.
- Kiosk-Härtung: keine Textauswahl, kein Kontextmenü, kein Bild-Ziehen, kein Pinch-Zoom der Seite, Mauszeiger ausgeblendet (außer `show_cursor`).

### 9.5 Touch-Modus
- Nur wenn die aktive Präsentation ein Touch-Menü hat und `touch_enabled`.
- Hinweis-Element („Tippen für Informationen“ + Hand-Icon, dezent pulsierend) unten im Folienbereich, wenn `attract.enabled`.
- Antippen irgendwo → Menü: Titel, Intro, Kachelraster (`columns`), große Kacheln (mind. 220 px hoch, Icon 72 px, Beschriftung ≥ 40 px, Farbe oder Bild mit Verlauf), Header bleibt, Footer bleibt.
- Aktionsleiste unten (mind. 96 px hoch): „‹ Zurück“ (in Unterebenen/Inhalten) und „Zur Startseite“ (beendet Touch-Modus).
- Inhalte: Bild (eingepasst), PDF (Seite blättern per Wischen + große Pfeile, „Seite 2 von 8“), Video (mit Ton nach `volume`, Start/Pause, Neustart, Fortschritt), Info-Folie, Webseite (interaktiv), Galerie (Wischen + Pfeile + Punkte).
- Inaktivität `idle_timeout_s` → Hinweis „Noch da?“ mit 10-s-Countdown und großem „Weiter“-Knopf → danach zurück zur Diashow (an der unterbrochenen Stelle).
- Telemetrie: `session_start`, `tile_open` (tile_id, label), `session_end` (duration_s).

### 9.6 Kopplung
- Ohne Schlüssel: `POST /api/player/pairing` → 6-stelliger Code, groß anzeigen („482 913“), dazu CMS-Adresse (`location.origin`) und Anleitung „Im CMS unter Stelen → Stele hinzufügen diesen Code eingeben“. Alle 3 s `GET /api/player/pairing/<code>`; `paired` → Schlüssel übernehmen (Antwort + Cookie) und starten; `expired` → neuen Code holen.
- Gespeicherter Schlüssel abgelehnt: nur `401` zählt (403 u. Ä. → nur Fehlerprotokoll). Kopplungsbildschirm erst nach mind. 3 Ablehnungen **und** 10 min anhaltender Ablehnung (kurze Server-Störung ≠ entkoppelt). Schlüssel und gespeichertes Manifest bleiben dabei erhalten; Heartbeats laufen mit dem alten Schlüssel weiter – gilt er wieder, bricht der Player die Kopplung ab und zeigt den gespeicherten Stand. Erst eine neue Kopplung ersetzt Schlüssel und Manifest.

### 9.7 Telemetrie und Befehle
- Heartbeat alle 15 s (`POST /api/player/heartbeat`), Antwort enthält `manifest_version` (weicht sie ab → Manifest neu laden) und `commands`.
- Befehle: `reload` (neu laden), `identify` (10 s Vollbild-Overlay mit Stelen-Name/Standort, pulsierender Rahmen), `clear_cache` (nur Medien-Cache löschen; Programmdateien, Schlüssel und gespeichertes Manifest bleiben; neu laden nur, wenn der Server erreichbar ist). Ergebnis im nächsten Heartbeat (`command_results`).
- Zustellung (Player und Agent): offene Befehle verfallen nach 10 min ohne Bestätigung (`result` „Fehler: abgelaufen …“); zugestellt, aber nach 3 min ohne Ergebnis → erneut zustellen (Player und Agent führen jede ID nur einmal aus).
- Admin: Stele mit Status `never` → Player-Befehle gesperrt, Grund sichtbar (Screenshot über den Agenten bleibt möglich); `offline` → Hinweis „wird beim nächsten Kontakt ausgeführt, verfällt nach 10 Min.“.
- Statistik (`played`, `touch`) und offene Ergebnisse überstehen einen Neustart des Players (localStorage).
- Diagnose: 5× schnell in die linke obere Ecke (150 × 150 px) tippen → Info-Overlay (Version, Stele, Schlüssel-Ende, Manifest-Version/-Alter, online/offline, Bildschirm, aktuelle Folie, letzte Fehler), schließt nach 30 s.
- `PLAYER_VERSION = "1.0.0"`.

### 9.8 Player-API (`/api/player`, Schlüssel per Cookie `stele_key` oder Header `X-Stele-Key`)
| Methode | Pfad | Body → Antwort |
|---|---|---|
| POST | `/api/player/pairing` (ohne Schlüssel, max. 10/min je IP) | `{device_info: {user_agent, screen: {w, h}}}` → `{code, expires_at, poll_interval_s: 3}` |
| GET | `/api/player/pairing/<code>` (max. 60/min je IP) | → `{status: "waiting|paired|expired", key?: str, stele?: {id, name}}` (bei `paired` zusätzlich Cookie setzen; nach der ersten Auslieferung nur noch 30 s abrufbar) |
| GET | `/api/player/manifest` | → Manifest (ETag) |
| POST | `/api/player/heartbeat` | `Heartbeat` → `{manifest_version, commands: [{id, command, payload}], server_time}` |
| GET | `/player/?key=…` | setzt Cookie `stele_key` (HttpOnly, SameSite=Lax, 10 Jahre) und liefert die Seite |

```json
Heartbeat = {
  "player_version": "1.0.0", "manifest_version": "…", "mode": "slideshow|touch|standby|idle",
  "current": {"presentation_id", "item_id", "content_id", "title", "started_at"} | null,
  "screen": {"w", "h"}, "user_agent": "…", "uptime_s": 1234,
  "errors": [{"ts", "message", "item_id"}],            // neu seit letztem Heartbeat (max. 20)
  "played": [{"started_at", "duration_s", "presentation_id", "item_id", "content_id", "title"}],
  "touch": [{"ts", "event", "session_id", "tile_id", "label", "duration_s"}],
  "command_results": [{"id", "ok", "message"}]
}
```
Server: `last_seen_at`, `last_state` aktualisieren; Online-Segment verlängern (Lücke > `offline_after_s` → neues Segment + Ereignis `online`); Listen in `playback_log`/`touch_log`/`stele_events` schreiben; Befehle als `delivered_at` markieren (s. §9.7 Zustellung); Zeitstempel der Player-Uhr, die mehr als 1 Tag von der Serverzeit abweichen, durch die Serverzeit ersetzen; unbekannter Schlüssel → 401.

## 10. Stelen-Agent (`stele_agent/`)
- `stele_agent.py`, nur Standardbibliothek; `psutil` und `mss`/`Pillow` optional (Fallbacks: `/proc` unter Linux).
- Aufruf: `python3 stele_agent.py --server http://127.0.0.1:8090 --key <schlüssel> [--interval 60] [--allow-screenshots]`.
- Alle `interval` s: `POST /api/agent/report` `{hostname, os, uptime_s, cpu, ram, disk, temp, ips: [], screen: {w, h}}` → `{ok, commands: [{id, command}]}`. Befehl `screenshot` nur mit `--allow-screenshots` (sonst Ergebnis „Screenshots auf der Stele deaktiviert“): Bildschirm aufnehmen, auf max. 540 px Breite verkleinern, `POST /api/agent/screenshot` (multipart `image`, JPEG) → Server speichert `data/screenshots/<stele_id>/latest.jpg`.
- Server-API: `/api/agent/report`, `/api/agent/screenshot` (Stelen-Schlüssel). Report → `last_agent`, `stele_metrics`.
- Robust: Netzfehler → Wiederholung mit Backoff (max. 5 min), nie abstürzen. README mit Autostart (systemd-User-Dienst, Windows-Aufgabenplanung) und Chrome-Kiosk-Aufruf:
  `google-chrome --kiosk --noerrdialogs --disable-infobars --autoplay-policy=no-user-gesture-required --overscroll-history-navigation=0 --disable-pinch "http://<cms>:8090/player/?key=<schlüssel>"`

## 11. Admin-Oberfläche (`web/admin/`)

### 11.1 Aufbau
- SPA mit Hash-Routing (`#/presentations/3`). Einstieg `index.html` → `js/main.js`. Keine Inline-Skripte (CSP).
- **Shell:** Seitenleiste links (Gruppen, Icons + Text, einklappbar auf Icon-Leiste, unter 1024 px als Schublade mit Menü-Knopf), Kopfleiste oben (Seitentitel/Brotkrumen, Stelen-Status-Pille, Benutzer-Menü), Hinweisband „Testbetrieb“ in der Kopfleiste.
- **Navigation** (nur Einträge mit Recht sichtbar):
  - Übersicht `#/`
  - **Inhalte:** Mediathek `#/media` · Präsentationen `#/presentations` · Touch-Menüs `#/touch-menus` · Designs `#/designs`
  - **Betrieb:** Stelen `#/steles` · Zeitplan `#/schedule` · Monitoring `#/monitoring`
  - **Verwaltung:** Benutzer `#/users` · Rollen & Rechte `#/roles` · Protokoll `#/audit` · Einstellungen `#/settings`
  - Benutzer-Menü: Profil `#/profile`, Passwort ändern, Hell/Dunkel, Abmelden.
- Seitenaufbau einheitlich: Kopf (Titel, ein Satz Beschreibung, Hauptaktion rechts, Nebenaktionen), darunter Werkzeugleiste (Suche/Filter), dann Inhalt.
- **Vorführseite** `show-stele-index.html` → `js/show.js` (+ `js/show/*.js`, `css/views/show.css`): eigene Seite ohne Shell, gleiche Sitzung und Bausteine. Anmeldung über die Schnellanmeldung (nur Testbetrieb/Loopback) oder die Admin-Oberfläche. Abschnitte: (1) Rundgang – Kennzahlen, Karten je Bereich mit Stichpunkten und Link (neuer Tab; ohne Recht ausgeblendet), „Stele simulieren“ (Player-Vorschau einer Präsentation, Diashow oder Touch); (2) Info-Folie anlegen – Vorlage, Texte, Farben, Live-Vorschau; Ziel neue oder bestehende Präsentation, optional veröffentlichen (Recht `presentations.publish`); nutzt nur die bestehenden Endpunkte (§7.4, §7.5, §7.5a); Angelegtes wird im Tab gemerkt (`sessionStorage`) und lässt sich mit Bestätigung wieder löschen; (3) UI-Bausteine – Knöpfe, Status, Formulare, Toasts, Dialoge, Tabelle, Diagramme mit Beispieldaten (speichert nichts).

### 11.2 UX-Regeln (verbindlich)
- ISO 9241-110 (Aufgabenangemessenheit, Selbstbeschreibung, Erwartungskonformität, Fehlertoleranz, Steuerbarkeit), WCAG 2.2 AA.
- Kontrast Text ≥ 4,5:1, auch Nebentexte (keine hellgraue Schrift); Fokus immer sichtbar; alles per Tastatur bedienbar; Klickziele ≥ 32 px (Touch ≥ 44 px); Schrift ≥ 14 px.
- Farbe nie allein: Status immer Icon + Text. Farbbedeutung: Grün = läuft/online/veröffentlicht, Blau = Aktion/Info, Gelb/Orange = Warnung/offene Änderungen, Rot = Fehler/offline/Löschen, Neutral = Entwurf/aus.
- Eine Hauptaktion pro Ansicht (gefüllter Knopf), weitere als Umriss- oder Text-Knöpfe, seltene im „⋯“-Menü.
- Zerstörende Aktionen: Bestätigungsdialog mit eindeutiger Beschriftung („Präsentation löschen“), Folgen benennen („wird auf 1 Stele verwendet“).
- Rückmeldung: Speichern-Status sichtbar, Toasts für Erfolg, Fehlermeldungen am Feld + verständlich (was ist passiert, was tun).
- Leere Zustände erklären und bieten die nächste Aktion an. Ladezustände als Skelett.
- Ungespeicherte Änderungen: Warnung beim Verlassen. Speichern-Konflikt (409 `edit_conflict`): Hinweis mit „Neu laden“, kein automatisches Überschreiben; Editoren übernehmen die Server-Antwort nur, wenn sich lokal während des Speicherns nichts geändert hat.
- Verbindung: Anfragen brechen nach 20 s ab (`ApiError` `timeout`); nach 2 fehlgeschlagenen Hintergrund-Abrufen in Folge zeigt die Kopfleiste „Verbindung unterbrochen – Stand hh:mm“, bis ein Abruf wieder gelingt. Polls laufen nie doppelt.
- Fehlende Rechte: Aktion ausblenden; wo die Aktion erwartbar ist (z. B. Veröffentlichen), stattdessen die erlaubte Alternative zeigen („Zur Freigabe einreichen“).
- Responsiv von 1920 px bis Tablet (768 px), nutzbar bis 390 px; kein waagrechtes Scrollen der Seite.
- Hell und Dunkel.

## 12. Gemeinsame Icons (`web/shared/icons.js`)
ES-Modul: `export const ICONS = {name: '<svg-innen>'}`, `export const TILE_ICONS = [...]` (für Touch-Kacheln erlaubte Namen), `export function iconSvg(name, {size=20, label=null, className=''} = {})` → `SVGElement` (24er-viewBox, Strich-Icons, `currentColor`; ohne `label` `aria-hidden="true"`, mit `label` `role="img"` + `aria-label`).

## 13. Qualität und Tests
- Server: pytest (`server/tests/`), Tests mit eigenem Temp-Datenordner und `STELECMS_BACKGROUND=0`. Abdeckung mindestens: Login/Sperre/CSRF/Leerlauf, Rechte je Endpunkt (403), Schutzregeln, Upload (Bild/Video/PDF, falscher Typ), Veröffentlichen/Freigabe/Verwerfen/Status-Hash, Manifest + ETag, Zeitplan-Auflösung inkl. Mitternacht/Priorität, Kopplung, Heartbeat, Löschen mit Verwendung (409).
- Browser-Tests nur headless mit eigenem CDP-Port und eigenem `--user-data-dir` im Scratchpad; Server-Testinstanzen auf eigenen Ports (18090–18099) mit eigenem `STELECMS_DATA`.
- Keine Konsolenfehler; kein waagrechtes Scrollen; Hell und Dunkel prüfen.
