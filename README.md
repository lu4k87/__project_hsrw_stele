# Stele CMS

Web-CMS für eine **digitale Stele** (Hochformat 1080 × 1920, Touch, PC mit Chrome im Kiosk-Modus):
Inhalte pflegen, Präsentationen gestalten, per Zeitplan ausspielen, Stelen überwachen.

<p>
  <img src="docs/img/dashboard.png" alt="Admin-Oberfläche: Übersicht mit Stele, Freigaben und letzter Aktivität" width="72%">
  <img src="docs/img/player.png" alt="Player auf der Stele im Hochformat" width="24%">
</p>

*Links die Admin-Oberfläche (Übersicht), rechts der Player, wie er auf der Stele läuft.*

> **Testbetrieb:** Das CMS läuft vorerst nur lokal (`127.0.0.1:8090`). Netzwerk, HTTPS und die
> endgültige Anmeldung folgen später.

## Quick Setup: Vorführseite testen

In fünf Minuten vom Klonen bis zur ersten eigenen Folie auf der (simulierten) Stele.

1. **Holen und starten** (Python ≥ 3.10; beim ersten Start wird `.venv` angelegt):

   ```bash
   git clone git@github.com:lu4k87/stele_hsrw.git
   cd stele_hsrw
   ./run.sh
   ```

   **Windows:** Ordner kopieren oder klonen, `run.bat` doppelklicken → [Windows](#windows).

2. **Vorführseite öffnen:** <http://127.0.0.1:8090/admin/show-stele-index.html>
3. **Anmelden:** auf ein Demo-Konto klicken, z. B. *Administrator* (Schnellanmeldung nur im Testbetrieb und nur auf diesem Rechner).
4. **Ausprobieren:**
   - **1 · Rundgang:** Karten öffnen die Bereiche der Admin-Oberfläche in einem neuen Tab; „Stele simulieren“ → *Abspielen*.
   - **2 · Folie anlegen:** Vorlage wählen, Text und Farben ändern (Vorschau rechts), *Folie anlegen* → *Im Player abspielen*.
   - **3 · UI-Bausteine:** Knöpfe, Formulare, Meldungen, Dialoge, Tabelle und Diagramme – hier wird nichts gespeichert.
5. **Rollen vergleichen (optional):** oben rechts *Abmelden*, als *Autor* oder *Betrachter* anmelden – fehlende Rechte blenden Aktionen aus.
6. **Echte Stele simulieren (optional):** unter *Stelen → Stele Foyer → Verbindung* den Player-Link kopieren und in einem zweiten Browserfenster öffnen (Vollbild wie an der Stele: `google-chrome --kiosk "<Player-Link>"`, beenden mit Alt+F4). Danach zeigt das Monitoring die Stele als online.
7. **Aufräumen:** unten auf der Vorführseite unter *In dieser Vorführung angelegt* → *Löschen*.

> Alles Angelegte landet in der lokalen Datenbank unter `data/`. Für einen Test mit eigenen, wegwerfbaren
> Daten: `STELECMS_PORT=8091 STELECMS_DATA=/tmp/stele-test ./run.sh` und dann Port `8091` verwenden.

## Inhalt

1. [Funktionen](#1-funktionen)
2. [Schnellstart](#2-schnellstart)
3. [Konfiguration](#3-konfiguration)
4. [Konten und Rollen](#4-konten-und-rollen)
5. [Stele einrichten](#5-stele-einrichten)
6. [Aufbau des Repos](#6-aufbau-des-repos)
7. [Entwicklung und Git](#7-entwicklung-und-git)
8. [Tests](#8-tests)
9. [Backup](#9-backup)

## 1. Funktionen

Alle Bilder zeigen die Demo-Daten einer frischen Installation.

### Übersicht und Kopfleiste

- Startseite: Zustand jeder Stele, was gerade läuft und der nächste Wechsel
- Hinweise auf wartende Freigaben, unveröffentlichte Änderungen, ablaufende Folien und Präsentationen, die länger nicht aktualisiert wurden (Tage in den Einstellungen)
- Kopfleiste: Testbetrieb-Hinweis, Stelen-Status, Zoom 80–150 %, Hell/Dunkel, Benutzer-Menü

![Kopfleiste mit Testbetrieb, Stelen-Status, Zoom, Hell/Dunkel und Benutzer](docs/img/topbar.png)

<p>
  <img src="docs/img/dashboard.png" alt="Übersicht im hellen Farbschema" width="49%">
  <img src="docs/img/dashboard-dark.png" alt="Übersicht im dunklen Farbschema" width="49%">
</p>

*Übersicht in Hell und Dunkel.*

### Mediathek

- Inhalte: Bilder, Videos, PDFs, Info-Folien (aus Vorlagen), Webseiten
- Suche, Filter nach Typ und Schlagwort, Sortierung, Raster- oder Listenansicht
- Anzeige, in wie vielen Präsentationen ein Inhalt verwendet wird

![Mediathek in der Rasteransicht](docs/img/media.png)

![Suche, Filter und Umschalter Raster/Liste](docs/img/media-toolbar.png)

<p>
  <img src="docs/img/media-new.png" alt="Menü „Neu“: Info-Folie oder Webseite" width="36%">
  <img src="docs/img/media-list.png" alt="Mediathek in der Listenansicht" width="62%">
</p>

*Links: neuer Inhalt über „Neu“. Rechts: Listenansicht mit Typ, Größe und Verwendung.*

<p>
  <img src="docs/img/media-detail.png" alt="Details eines Bildes: Vorschau, Titel, Schlagworte, Dateiangaben" width="40%">
  <img src="docs/img/media-web.png" alt="Webseite einbinden: Adresse prüfen, Zoom, Neuladen, Touch-Bedienung" width="40%">
</p>

*Links: Details eines Inhalts mit Löschen, Original und „Zu Präsentation“. Rechts: Webseite einbinden mit Prüfung, ob sie sich einbetten lässt.*

#### Info-Folien

- Fünf Vorlagen: Titel und Text, Bild und Text, Aussage, Veranstaltung, Liste
- Veranstaltung mit Pflichtangaben: Für wen?, Datum, Uhrzeit, Ort, Eintritt
- QR-Code auf jeder Info-Folie: Adresse eingeben, der Code wird erzeugt (z. B. Anmeldung, Lageplan)
- Lesbarkeit: Hinweis unter der Vorschau bei viel Text (über 30 Wörter), stark verkleinerter Schrift (unter 36 px) oder abgeschnittenem Text
- Farbpaletten per Klick, Farben mit Vorschlägen, Farbverlauf, eigene Farben für Überschrift und Unterzeile, Hintergrundbild mit Abdunklung
- Schrift je Folie: Schriftart für Überschrift und Text, Größe (Stufen oder stufenlos), Stärke, Zeilenabstand, Laufweite, Großbuchstaben – ungesetzt gilt das Design
- Layout: Ausrichtung, senkrechte Lage, Innenabstand, Textfeld (Fläche oder Milchglas), Akzentlinie, Logo in einer Ecke
- Live-Vorschau im Hochformat, auf Wunsch mit Header und Footer eines Designs

![Editor für Info-Folien mit Vorlage, Inhalt und Live-Vorschau](docs/img/text-editor.png)

<p>
  <img src="docs/img/text-templates.png" alt="Auswahl der Vorlage" width="49%">
  <img src="docs/img/text-design.png" alt="Gestaltung: Farbpaletten, Farben, Verlauf, Hintergrundbild" width="49%">
</p>

### Präsentationen

- Diashow aus Folien mit eigener Dauer, Übergang, Gültigkeitszeitraum und Bildunterschrift
- Diashow-Einstellungen: Standarddauer, Reihenfolge, Übergänge, Bildanpassung, Videos, Fortschrittsbalken
- Hinweis bei zu langem Durchlauf (über 90 s) und bei Bild- und Info-Folien über 7 s
- **Design** (Rahmen) und **Touch-Menü** je Präsentation
- Bearbeitet wird der Entwurf; an die Stele geht nur der **veröffentlichte** Stand
- Freigabe: Autoren reichen ein, Redaktion veröffentlicht oder lehnt mit Begründung ab
- Vier-Augen-Prinzip abgesichert: Bearbeiten nach dem Einreichen setzt die Freigabe zurück; wurde der Stand seit dem Einreichen geändert (z. B. Design), fragt das Veröffentlichen nach
- Gleichzeitiges Bearbeiten: speichert jemand anderes zwischendurch, erscheint „von X geändert – neu laden“ statt stillem Überschreiben

![Präsentationen mit Status, Folienzahl, Dauer, Design und Verwendung](docs/img/presentations.png)

![Präsentations-Editor: Folienliste, Vorschau im Hochformat, Einstellungen](docs/img/editor.png)

<p>
  <img src="docs/img/editor-slides.png" alt="Folienliste: sortieren, aktivieren, Dauer" width="32%">
  <img src="docs/img/editor-slide.png" alt="Einstellungen der Folie: Dauer, Übergang, Zeitraum, Bildunterschrift, Vollbild" width="32%">
  <img src="docs/img/editor-show.png" alt="Diashow-Einstellungen: Dauer, Reihenfolge, Übergang, Bilder, Videos, Anzeige" width="32%">
</p>

*Folienliste (Ziehen zum Sortieren), Einstellungen der Folie und der Diashow.*

<p>
  <img src="docs/img/editor-frame.png" alt="Rahmen und Touch: Design und Touch-Menü zuordnen" width="32%">
  <img src="docs/img/editor-preview.png" alt="Vorschau der Präsentation mit Steuerung und Touch-Test" width="64%">
</p>

*Links: Design und Touch-Menü zuordnen. Rechts: Vorschau wie auf der Stele, mit „Touch testen“.*

![Freigabe: eingereichte Präsentation veröffentlichen oder ablehnen](docs/img/editor-review.png)

### Designs und Touch-Menüs

- **Design:** Rahmen um alle Folien – Header (Logo, Titel, Uhr, Datum), Footer (Laufband mit Meldungen oder RSS, fester Text), Farbpaletten, Akzentfarbe, Schriften für Text und Überschriften und der Typografie-Standard aller Info-Folien
- **Schriften:** zehn freie Schriften mitgeliefert (offline auf der Stele, Lizenz OFL), eigene Hausschriften hochladbar (WOFF2, WOFF, TTF, OTF) – Verwaltung unter „Designs“
- **Touch-Menü:** Kacheln führen zu Inhalt, Galerie oder Untermenü; nach Inaktivität zurück zur Diashow
- Beide wiederverwendbar; die Übersicht zeigt, welche Präsentationen sie nutzen

![Design-Editor mit Live-Vorschau](docs/img/design-editor.png)

<p>
  <img src="docs/img/designs.png" alt="Liste der Designs mit Skizze und Verwendung" width="49%">
  <img src="docs/img/design-header-footer.png" alt="Header und Footer eines Designs einstellen" width="30%">
</p>

![Touch-Menü-Editor mit Kacheln und Vorschau](docs/img/touch-editor.png)

![Kacheln des Touch-Menüs: Ziel, Reihenfolge, Bearbeiten](docs/img/touch-tiles.png)

### Zeitplan

- Präsentation je Stele nach Wochentag, Uhrzeit, Datumsbereich und Priorität
- Ohne passenden Eintrag läuft die Standard-Präsentation
- Wochenansicht mit aktueller Uhrzeit und Hinweis auf Konflikte und unveröffentlichte Stände

![Zeitplan in der Wochenansicht](docs/img/schedule.png)

<p>
  <img src="docs/img/schedule-entries.png" alt="Liste der Einträge mit Tagen, Uhrzeit, Zeitraum, Priorität" width="58%">
  <img src="docs/img/schedule-dialog.png" alt="Eintrag bearbeiten mit Warnung bei unveröffentlichter Präsentation" width="40%">
</p>

*Links: Einträge (höhere Priorität gewinnt). Rechts: Eintrag bearbeiten – Hinweis, wenn die Präsentation noch nicht veröffentlicht ist.*

### Stelen

- Mehrere Stelen: Name, Standort, IP, Auflösung, Standard-Präsentation
- Fernsteuerung: neu laden, identifizieren, Screenshot anfordern
- Einstellungen: Lautstärke, Touch-Bedienung, Mauszeiger, Nachtmodus, täglicher Neustart
- Kopplung per 6-stelligem Code oder Player-Link; Kiosk- und Agent-Aufruf zum Kopieren

![Stelen als Karten mit Zustand und laufender Präsentation](docs/img/steles.png)

![Stele im Detail: Live-Ansicht, Wiedergabe, Netzwerk, Stelen-PC, letzter Screenshot](docs/img/stele-detail.png)

<p>
  <img src="docs/img/stele-settings.png" alt="Einstellungen einer Stele" width="49%">
  <img src="docs/img/stele-connection.png" alt="Verbindung: Kopplung, Player-Link, Stelen-Agent" width="49%">
</p>

*Links: Gerät, Wiedergabe und Zeiten. Rechts: Kopplung, Player-Link und Agent-Aufruf (Schlüssel im Bild ersetzt).*

![Befehlsverlauf und Ereignisse einer Stele](docs/img/stele-log.png)

### Monitoring

- Zustand und Verfügbarkeit aller Stelen, Hinweise und Störungen oben
- Stelen-PC über den Agenten: CPU, Arbeitsspeicher, Datenträger, Temperatur
- Touch-Nutzung (Sitzungen, Dauer, meistgeöffnete Kacheln), Wiedergabeprotokoll, Ereignisse
- Letzter Screenshot der Stele, Zustand des Servers

![Monitoring: Hinweise und Zustand der Stelen](docs/img/monitoring.png)

![Verfügbarkeit der letzten 24 Stunden](docs/img/monitoring-avail.png)

![Stelen-PC und Touch-Nutzung](docs/img/monitoring-pc.png)

![Wiedergabeprotokoll und Ereignisse](docs/img/monitoring-log.png)

<p>
  <img src="docs/img/monitoring-screenshot.png" alt="Letzter Screenshot der Stele" width="36%">
  <img src="docs/img/monitoring-server.png" alt="Server: Version, Laufzeit, Datenbank, Medien, Speicherplatz" width="62%">
</p>

### Benutzer, Rollen, Protokoll

- Lokale Konten mit Passwort-Hash, Sitzung und Sperre nach Fehlversuchen
- Rollen bündeln Rechte pro Funktion; jedes Konto hat genau eine Rolle
- Protokoll aller Änderungen und Anmeldungen, filterbar, als CSV exportierbar

<p>
  <img src="docs/img/users.png" alt="Benutzerliste mit Rolle, Status und letzter Anmeldung" width="62%">
  <img src="docs/img/user-dialog.png" alt="Benutzer anlegen mit erzeugtem Passwort" width="34%">
</p>

![Rollen und Rechte bearbeiten](docs/img/roles.png)

![Vergleich aller Rollen: Rechte × Rollen](docs/img/roles-compare.png)

![Protokoll mit Filter und aufgeklapptem Eintrag](docs/img/audit.png)

### Einstellungen und Profil

- Allgemein: Organisation, Zeitzone, Standard-Foliendauer und -Design
- Sicherheit: Sitzungsdauer, Sperre, Passwortlänge, Schnellanmeldung (Testbetrieb)
- Medien, Betrieb, System mit Datenbank-Sicherung
- Profil: eigene Angaben, Passwort, Farbschema, eigene Rechte

<p>
  <img src="docs/img/settings.png" alt="Einstellungen: Allgemein" width="49%">
  <img src="docs/img/settings-security.png" alt="Einstellungen: Sicherheit und Testbetrieb" width="49%">
</p>

<p>
  <img src="docs/img/settings-system.png" alt="Einstellungen: System und Sicherung" width="49%">
  <img src="docs/img/profile.png" alt="Profil mit persönlichen Angaben, Passwort und Darstellung" width="49%">
</p>

### Player

- Läuft in Chrome (Kiosk) auf dem Stelen-PC, Bühne fest in Stelen-Auflösung
- Diashow mit Header und Footer; Antippen öffnet das Touch-Menü
- Kopplung per 6-stelligem Code oder Player-Link mit Schlüssel
- Offline-Cache: spielt weiter, wenn das CMS nicht erreichbar ist
- Keine externen CDNs, kein Build-Schritt – alles läuft aus dem Repo

<p>
  <img src="docs/img/player.png" alt="Player: Diashow mit Header und Laufband" width="24%">
  <img src="docs/img/player-touch.png" alt="Player: Touch-Menü mit Kacheln" width="24%">
  <img src="docs/img/player-touch-item.png" alt="Player: geöffneter Inhalt aus dem Touch-Menü" width="24%">
  <img src="docs/img/player-pairing.png" alt="Player: Kopplungscode einer neuen Stele" width="24%">
</p>

*Diashow, Touch-Menü, geöffneter Inhalt und Kopplungscode einer neuen Stele.*

### Vorführseite

`/admin/show-stele-index.html` – zum Vorstellen und Ausprobieren, mit derselben Anmeldung:

- **Rundgang:** Kennzahlen, Karte je Bereich mit Stichpunkten und Link, Stele im Player simulieren
- **Folie anlegen:** Info-Folie gestalten (Live-Vorschau), in neue oder bestehende Präsentation legen, optional veröffentlichen; Angelegtes wieder löschen
- **UI-Bausteine:** Knöpfe, Status, Formulare, Meldungen, Dialoge, Tabelle, Diagramme mit Beispieldaten

![Vorführseite: Rundgang durch das Backend](docs/img/show-tour.png)

<p>
  <img src="docs/img/show-create.png" alt="Vorführseite: Info-Folie und Präsentation anlegen" width="49%">
  <img src="docs/img/show-gallery.png" alt="Vorführseite: UI-Bausteine ausprobieren" width="49%">
</p>

## 2. Schnellstart

Voraussetzungen:

- Python ≥ 3.10 mit `venv` (Debian/Ubuntu: `sudo apt install python3-venv`)
- `ffmpeg` (inkl. `ffprobe`) für Videos, `poppler-utils` (`pdftoppm`, `pdfinfo`) für PDFs
- Chrome für den Player

```bash
git clone git@github.com:lu4k87/stele_hsrw.git
cd stele_hsrw
./run.sh
```

- Erster Start legt `.venv` an und installiert die Abhängigkeiten
- Admin-Oberfläche: <http://127.0.0.1:8090/admin/>
- Vorführseite: <http://127.0.0.1:8090/admin/show-stele-index.html> – Rundgang durch alle Bereiche,
  Info-Folie samt Präsentation in einem Schritt anlegen (mit Live-Vorschau und Player), alle UI-Bausteine ausprobieren
- Player-Link der Stele: steht im Startprotokoll und im CMS unter **Stelen → Verbindung**
- Neue Datenbank erhält Demo-Inhalte (abschaltbar, siehe Konfiguration)

### Windows

1. **Python ≥ 3.10** von <https://www.python.org/downloads/> installieren, Haken bei *Add python.exe to PATH*
   (alternativ `winget install Python.Python.3.12`)
2. **Repo holen:** `git clone` oder Ordner kopieren (USB-Stick, Netzlaufwerk)
   - `.venv/` und `data/` dürfen fehlen; eine mitkopierte Linux-`.venv` legt `run.bat` neu an
   - mitkopiertes `data/` läuft weiter (gleiche Datenbank, Medien, Konten)
3. **Starten:** `run.bat` doppelklicken (oder in PowerShell `.\run.bat`)
   - erster Start legt `.venv` an und installiert die Abhängigkeiten (inkl. `tzdata`, Zeitzonen für Windows)
   - Fenster offen lassen; beenden mit Strg+C
4. **Vorführseite:** <http://127.0.0.1:8090/admin/show-stele-index.html> → weiter wie im [Quick Setup](#quick-setup-vorführseite-testen) ab Schritt 3

- **Optional, Videos und PDFs:** `winget install Gyan.FFmpeg` und `winget install oschwartz10612.Poppler`, danach neues
  Fenster öffnen (PATH); prüfen mit `where ffmpeg` und `where pdftoppm`. Ohne beide läuft alles außer Video-/PDF-Import
  (Demo-Daten dann ohne Video)
- **Player im Vollbild:** Win+R → `chrome --kiosk "<Player-Link>"`, beenden mit Alt+F4
- **Zweite Instanz** (PowerShell): `$env:STELECMS_PORT=8091; $env:STELECMS_DATA="$env:TEMP\stele-test"; .\run.bat`
- **Befehle aus dieser README:** `.venv\Scripts\python.exe` statt `.venv/bin/python`

## 3. Konfiguration

| Umgebungsvariable | Standard | Zweck |
|---|---|---|
| `STELECMS_HOST` | `127.0.0.1` | Bind-Adresse (später Netzwerk) |
| `STELECMS_PORT` | `8090` | Port |
| `STELECMS_DATA` | `./data` | Datenbank, Medien, Screenshots, Schlüssel |
| `STELECMS_BACKGROUND` | `1` | `0` = ohne Hintergrunddienste (Tests) |
| `STELECMS_SEED_DEMO` | `1` | Demo-Inhalte bei neuer Datenbank |

Beispiel für eine zweite Instanz mit eigenen Daten:

```bash
STELECMS_PORT=8091 STELECMS_DATA=/tmp/stele-test ./run.sh
```

## 4. Konten und Rollen

![Anmeldung mit Schnellanmeldung im Testbetrieb](docs/img/login.png)

- Schnellanmeldung mit Demo-Konten: nur im Testbetrieb und nur von localhost

| Konto | Passwort | Rolle |
|---|---|---|
| `admin` | `admin123` | Administrator (Demo) |
| `redaktion` | `redaktion123` | Redaktion (Demo) |
| `autor` | `autor123` | Autor (Demo) |
| `betrachter` | `betrachter123` | Betrachter (Demo) |

Eigenes Administratorkonto und Passwörter auf der Kommandozeile:

```bash
.venv/bin/python server/manage.py create-admin --username <name> --display-name "<Anzeigename>"   # Passwort wird erzeugt
.venv/bin/python server/manage.py reset-password --username <name>
.venv/bin/python server/manage.py list-users
```

> **Vor dem Netzwerkbetrieb:** Demo-Konten löschen oder deaktivieren und die Schnellanmeldung unter
> **Einstellungen → Sicherheit** abschalten.

## 5. Stele einrichten

1. Im CMS **Stelen → Stele hinzufügen** – Name, Standort, IP.
2. Auf dem Stelen-PC Chrome im Kiosk-Modus mit dem Player öffnen:

   ```bash
   google-chrome --kiosk --noerrdialogs --disable-infobars \
     --autoplay-policy=no-user-gesture-required --overscroll-history-navigation=0 \
     --disable-pinch "http://<cms>:8090/player/"
   ```

3. Der Player zeigt einen 6-stelligen Code → im Assistenten auswählen oder eingeben.

   <img src="docs/img/stele-wizard.png" alt="Assistent „Stele hinzufügen“: Angaben, Koppeln, Standard-Präsentation" width="60%">

   Alternativ direkt den Player-Link mit Schlüssel verwenden.
4. Optional den Stelen-Agenten starten (CPU, RAM, Temperatur, Screenshots):
   [stele_agent/README.md](stele_agent/README.md).

## 6. Aufbau des Repos

| Pfad | Inhalt |
|---|---|
| `server/` | Flask-Backend (`stelecms/`), `run.py`, `manage.py`, Tests (`tests/`) |
| `web/admin/` | Admin-Oberfläche (Vanilla JS, ohne Build) – Aufbau in [web/admin/README.md](web/admin/README.md) |
| `web/player/` | Player für die Stele (Diashow, Rahmen, Touch-Modus, Offline-Cache) |
| `web/shared/` | gemeinsame Icons |
| `stele_agent/` | Agent für den Stelen-PC |
| `docs/SPEC.md` | Spezifikation (Datenmodell, API, Player-Vertrag, UX-Regeln) |
| `docs/img/` | Screenshots für diese README – erzeugt mit `docs/screenshots.py` |
| `.githooks/` | Prüfung vor jedem Commit |
| `.github/workflows/` | Tests unter Ubuntu und Windows (GitHub Actions) |
| `AGENTS.md` | Arbeitsregeln für KI-Assistenten (Claude Code) |
| `data/` | Laufzeitdaten – nicht im Git |

## 7. Entwicklung und Git

Einmalig nach dem Klonen:

```bash
git config core.hooksPath .githooks   # Prüfung vor jedem Commit einschalten
git config pull.ff only               # kein stiller Merge beim Pull
```

Ablauf:

```bash
git pull                       # Stand holen
# … ändern, testen …
git status                     # prüfen, was sich geändert hat
git add <dateien>
git commit -m "feat(admin): kurze Beschreibung"
git push
```

- **Commit-Stil:** `typ(bereich): Beschreibung` – Typen `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`;
  Bereiche z. B. `server`, `admin`, `player`, `agent`
- **Prüfung vor dem Commit** (`.githooks/pre-commit`):
  - lehnt Laufzeitdaten ab (`data/`, `.venv/`, Datenbank, `secret_key`)
  - führt bei Änderungen unter `server/` die Tests aus
  - im Ausnahmefall überspringen: `git commit --no-verify`
- **Nicht im Git:** `data/` (Datenbank, Medien, Schlüssel), `.venv/`, Editor-Einstellungen
- **Zeilenenden:** `.gitattributes` hält alle Textdateien auf LF
- **Web-Oberflächen:** kein Build – Browser neu laden genügt

### Screenshots erneuern

Nach sichtbaren Änderungen die Bilder in `docs/img/` neu erzeugen:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt   # einmalig
.venv/bin/python docs/screenshots.py                       # alle Bilder (ca. 6 Minuten)
.venv/bin/python docs/screenshots.py media editor          # nur einzelne Abschnitte (--list zeigt alle)
```

- Startet eine eigene Testinstanz (Port 18095, Temp-Ordner), Chrome headless (de-DE), Player und Stelen-Agent
- Legt Beispieldaten an (PDF, zweite Stele, Zeitplan-Eintrag, Touch-Nutzung) und räumt danach alles ab
- Stelen-Schlüssel und lokale Pfade werden in den Bildern ersetzt; die eigene Instanz (8090, `data/`) bleibt unberührt
- Braucht Chrome oder Chromium (sonst `CHROME=/pfad/zu/chrome`) und `ffmpeg`

## 8. Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest server/tests -q
```

- Windows (PowerShell): `.venv\Scripts\python -m pytest server/tests -q`
- Ohne poppler (`pdftoppm`) wird der PDF-Test übersprungen, alle anderen laufen
- **GitHub Actions** (`.github/workflows/tests.yml`) bei jedem Push auf `main`: Ubuntu und Windows, Python 3.10 und 3.12
  - ffmpeg und poppler installiert (wie beim Anwender), dann pytest
  - echter Start über `run.sh` bzw. `run.bat` → `/admin/` und `/player/` müssen antworten
- Ergebnis im Reiter *Actions* des Repos

## 9. Backup

- Datenbank: im CMS unter **Einstellungen → System → Backup herunterladen** (SQLite)
- Medien: Ordner `data/media/` sichern
