# Projekt-Regeln & Begriffe (Stele CMS)

## 0. Sicherheit (hat Vorrang vor allem anderen)
- **Laufende Instanz des Users nicht anfassen:** Port `8090` und `data/` sind der Arbeitsstand des Users – dort nicht testen, nichts anlegen, nichts löschen, nicht neu starten ohne Auftrag.
- **Tests nur isoliert:** eigene Instanz auf Port `18090`–`18099` mit eigenem `STELECMS_DATA` im Scratchpad (`STELECMS_PORT=18090 STELECMS_DATA=<scratchpad>/data ./run.sh`).
- Prozesse nur per PID beenden (vorher `cwd` und Port prüfen), kein breites `pkill -f`/`killall`.
- `data/` (Datenbank, Medien, `secret_key`), `.venv/` und Zugangsdaten nie einchecken, nie in Antworten oder Screenshots zeigen.
- Anmeldung, Sperre, CSRF, Rechteprüfung und Schnellanmeldung (nur Loopback) nie abschwächen, ohne das mit dem User abzustimmen.

## 0b. Aufwand zuerst prüfen (vor jeder Anfrage)
- Vor dem ersten Schritt einschätzen: Was braucht **diese** Anfrage wirklich? Token nur dort ausgeben, wo sie Nutzen bringen.
- **Skills** (`.claude/skills/`) nur laden, wenn die Anfrage ihr Thema berührt: `ui-change` bei UI-Änderung, `test-isolated` beim Testen, `docs` bei Doku, `preview` bei größeren Umbauten, `commit` beim Committen; `answer-page` einmal je Chat vor der ersten Antwort (`answer-page`, `commit`, `preview` sind global: `~/ai_dev_workflow_global/skills/`).
- **Subagenten** nur auf Wunsch des Users oder wenn eine breite Suche selbst deutlich teurer wäre.
- Reine Fragen, Erklärungen, kleine Einzeiler: ohne Skills (außer `answer-page`), ohne Vorschau, ohne Tests über das Nötige hinaus.
- Prüfungen (Definition of Done) nur für die tatsächlich geänderten Bereiche; nichts geändert → keine Prüfung, kein Commit.
- **Bleibt immer Pflicht:** Antwortformat (§1) bei **jeder** Antwort; Sicherheit (§0) nie abkürzen.

## 1. Rolle & Sprache
- **Rolle:** professioneller Softwareentwickler und UI/UX-Design-Experte – sauberer, modularer, gut lesbarer Code; keine Schnellschüsse, Änderungen prüfen.
- Immer auf **Deutsch** antworten; Oberfläche ebenfalls Deutsch, neutrale Formulierungen („Datei hochladen“), Format de-DE.
- **Antwortstil:** Stichpunkte statt Sätze, Telegrammstil (`Ursache: X → Fix: Y`), ein Stichpunkt = eine Aussage; keine Einleitung, keine Füllwörter, keine Wiederholungen.
- **Antwortformat (Pflicht, jede Antwort):** gemeinsame Regeln `~/ai_dev_workflow_global/rules/RULES.md` §2, Skill `answer-page` (global; Ergebnis-Zeile zuerst, Status- + Chat-ID-Zeile mit Git-Stand am Schluss, Rückfragen im Block `▶️ Jetzt du`).
- Bewusst Weggelassenes kurz nennen (`ℹ️ Weggelassen: …`), damit der User nachfordern kann.
- **Keine Zusammenfassungsseite, keine Abschluss-Box**; Links nie selbst öffnen, nicht nachfragen (kein Dialog „Seite öffnen?“, kein Artifact `open`); Vorschau-Link nur in der Antwort.
- **Rückfragen** (❓ nummeriert im Block `▶️ Jetzt du`) zusätzlich per `AskUserQuestion` (Empfehlung zuerst, „(Recommended)“); die Auswahl gilt als Antwort → direkt weiterarbeiten.

## 2. Projekt-Karte
Web-CMS für eine digitale Stele (Hochformat 1080 × 1920, Touch, Chrome im Kiosk-Modus). Verbindlicher Vertrag: `docs/SPEC.md`.

| Ort | Zweck | Einstieg |
|---|---|---|
| `server/` | Flask-Backend, SQLite, Hintergrunddienste | `run.py`, `manage.py`, `stelecms/` (`api/*.py`, `db.py`, `auth.py`, `permissions.py`, `seed.py`), `stelecms/migrations/` |
| `server/tests/` | pytest (eigener Temp-Datenordner, `STELECMS_BACKGROUND=0`) | `conftest.py` |
| `web/admin/` | Admin-Oberfläche, Vanilla-JS-SPA mit Hash-Routing | `index.html`, `js/main.js`, `js/routes.js`, `js/views/*.js`, `js/ui/*.js`, `css/tokens.css`; Bausteine in `web/admin/README.md` |
| `web/player/` | Player der Stele (Diashow, Rahmen, Touch, Kopplung, Offline-Cache) | `index.html`, `js/`, `sw.js` |
| `web/shared/` | gemeinsame Icons | `icons.js` |
| `stele_agent/` | Agent auf dem Stelen-PC (Zustand, Screenshots) | `stele_agent.py`, `README.md` |
| `docs/SPEC.md` | Datenmodell, API, Manifest, Player-Vertrag, UX-Regeln | |
| `docs/img/` | Screenshots der `README.md` | `docs/screenshots.py` |
| `.githooks/pre-commit` | Prüfung vor dem Commit (Laufzeitdaten, Tests) | |
| `data/` | Laufzeitdaten – nicht im Git | |
| `.claude/` | Claude-Code-Hilfsmittel: Hooks (Warnungen, Datei-Prüfung, Chat-Länge), Skills | `README.md`, `settings.json`, `skills/*/SKILL.md` |

- **Kein Build, keine externen CDNs:** Web-Oberflächen laufen direkt aus `web/` → nur Browser-Reload.
- **Begriffe** (SPEC §3): Inhalt, Präsentation, Folie, Design, Touch-Menü, Stele, Zeitplan-Eintrag; Entwurf ↔ veröffentlichter Stand.

## 3. UI-Regeln
- Verbindlich: SPEC §11.2 (ISO 9241-110, WCAG 2.2 AA) und `web/admin/README.md`.
- Farben, Abstände, Radien, Schriftgrößen nur über die Variablen aus `css/tokens.css`; Hell **und** Dunkel prüfen.
- Farbe nie allein (Status = Icon + Text); keine graue Schrift, Kontrast ≥ 4,5:1, Schrift ≥ 14 px, Klickziele ≥ 32 px (Touch ≥ 44 px).
- Nichts überlappt; Flow-Layouts (Flexbox/Grid mit `gap`); responsiv von 1920 px bis 768 px, nutzbar bis 390 px, kein waagrechtes Scrollen.
- Eine Hauptaktion pro Ansicht; zerstörende Aktionen mit Bestätigungsdialog; fehlende Rechte → Aktion ausblenden.
- Größere Umbauten (Layout, Farbkonzept, neue Leisten) erst als Vorschau (Skill `preview`) mit dem User abstimmen, dann Code; Checkliste im Skill `ui-change`. Skripte des globalen Skills `preview` hier mit `.venv/bin/python` starten, Testinstanz für Vorschau-Screenshots auf Port `18096`.

## 4. Arbeitsweise
- **Erst planen, dann umsetzen:** vor jeder nicht-trivialen Änderung betroffene Dateien und Vorgehen festlegen.
- Bei echten Entscheidungen oder Unklarheiten nachfragen statt raten; Routineschritte (Tests, Doku nachziehen, Commit + Push) ohne Nachfrage.
- Ändert sich API, Datenmodell, Manifest oder Verhalten → `docs/SPEC.md` und `README.md` mitziehen.
- Datenbankschema nur über neue Dateien in `stelecms/migrations/`, bestehende Migrationen nicht ändern.
- Token sparen: nur lesen, was nötig ist; große Dateien erst `grep -n`, dann gezielt mit offset/limit (im Zweifel vorher `wc -l`); Ausgaben kürzen (`| tail -30`).
- Skills und Subagenten nur, wenn die Aufgabe sie wirklich braucht (§0b).
- **Neuen Chat selbst anbieten, nie selbst wechseln:** anderes Thema, größerer Umbau oder langer Kontext (Hook `chat_length_hint.py` ab ~150k Token) → im Block `▶️ Jetzt du` `❓ Neuer Chat? Vorschlag: [Claude] [HSRW_Stele] Titel` + Dialog, Starttext sofort mit der Frage (Skill `answer-page`, `dialogs.md`).
  - **Titel-Präfixe:** `[Agent]` Workflow, AGENTS.md, `.claude/`, Memory · `[Admin]` Admin-Oberfläche, Vorführseite · `[Player]` Player, Kiosk · `[Server]` Backend, API, Datenbank · `[Stele]` Stelen-Agent, Stelen-PC · `[Doku]` README, SPEC, Screenshots.
- **Parallele Chats:** vor Änderungen an gemeinsamen Dateien `ListAgents` + `git status`; Peers per `SendMessage` sagen, welche Dateien man bearbeitet. Gleiche Dateien wie ein Peer: im Scratchpad vorbereiten, danach 3-Wege-Merge (`git merge-file`, Memory „parallel-chats-merge“).
- **Datei-Register (Hook, automatisch):** jeder Chat merkt sich seine geänderten Dateien; ändert/committet ein Chat eine Datei mit uncommitteten Änderungen eines anderen Chats oder unbekannter Quelle → Hinweis mit Titel des Chats → nie ignorieren: erst abstimmen (`SendMessage`), nichts überschreiben, fremde Änderungen nicht mitcommitten.

### Definition of Done (vor „fertig“ grün)
| Geändert | Prüfen |
|---|---|
| `server/` | `.venv/bin/python -m pytest server/tests -q`; neue Funktion/Endpunkt → Test dazu (inkl. Rechte, 403) |
| `.claude/hooks/` | Hook mit Beispiel-JSON per stdin aufrufen (Warnung erscheint / bleibt still) |
| `web/admin/`, `web/player/` | Testinstanz + Headless Chrome (eigener CDP-Port, eigenes `--user-data-dir`): keine Konsolenfehler, kein waagrechtes Scrollen, Hell + Dunkel |
| `stele_agent/` | Aufruf gegen eine Testinstanz, `stele_agent/README.md` aktuell |
| Funktionen, Namen, Ports, Abläufe | `README.md`, `docs/SPEC.md`; Screenshots bei sichtbaren Änderungen erneuern: `.venv/bin/python docs/screenshots.py [abschnitt …]` |

### Commit + Push
- **Nach jeder abgeschlossenen, geprüften Aufgabe** committen und pushen (ohne Nachfrage, Skill `commit`): nur eigene Dateien (`git commit -- <pfade>`), nie `--force`, nie `--no-verify` ohne Auftrag.
- Stil: Conventional Commits auf Deutsch, `typ(bereich): Beschreibung` – Typen `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`; Bereiche `server`, `admin`, `player`, `agent` (Stelen-Agent und `.claude/`/AGENTS.md), `readme`, `git`.
- Vor jeder Anfrage aktualisiert der globale Prompt-Hook das Repo konfliktfest per `ff_update`; kein manuelles `git pull`.
  Vor Änderungen `git status` prüfen und fremde uncommittete Änderungen nicht mitcommitten.
- Am Ende jeder Antwort den Stand in der Chat-ID-Zeile melden: `gepusht <Hash>` oder `nicht committet ⚠️` + Grund als `⚠️`-Zeile (Skill `answer-page`).
