# .claude – Hilfsmittel für Claude Code

Alles, was nur für die Arbeit mit Claude Code gebraucht wird (nicht für den Betrieb des CMS).
Projektregeln für jeden Chat: `AGENTS.md` in der Wurzel.

| Ort | Zweck |
|---|---|
| `settings.json` | Leseverbote (`data/`, `.venv/`, `secret_key`) + Hook-Einträge; `ENABLE_CLAUDEAI_MCP_SERVERS=false` (claude.ai-Connectoren hier ungenutzt, sparen Kontext) |
| `hooks/pre_tool_guard.py` | warnt vor Bash/Edit (blockiert nie): breites pkill/killall, CMS-Start ohne eigenen Port/`STELECMS_DATA`, ändernde Anfragen an 8090, Schreiben in `data/`/`.venv/`, bestehende Migrationen, `git --force`/`--no-verify`/`add -A` |
| `hooks/chat_claims.py` | Datei-Register paralleler Chats (`/tmp/stele_hook_stamps_<uid>/claims/<session>.json`: Titel, PID, geänderte Dateien); `pre_tool_guard.py` warnt beim Ändern/Committen von Dateien mit uncommitteten Änderungen anderer Chats, `post_edit_check.py` trägt eigene Änderungen ein |
| `hooks/post_edit_check.py` | prüft nach jedem Edit die Datei: Python-Syntax (+ pyflakes, falls installiert), JSON, `cursor: help`, feste Farben außerhalb `tokens.css`, externe Skripte in `web/`; nach Bash dieselben Prüfungen für Dateien, die der Befehl geändert hat (`python3 - <<EOF`, `sed -i`; Stilregeln nur für per `git diff` hinzugefügte Zeilen; Startzeit-Stempel von `pre_tool_guard.py`) |
| `hooks/chat_length_hint.py` | vor jeder Nachricht: ab ~150k Token Kontext Hinweis an Claude (neuen Chat vorschlagen), ab ~250k zusätzlich Meldung an den User; Schwellen per `CHAT_HINT_TOKENS`/`CHAT_STRONG_TOKENS` |
| `skills/<name>/SKILL.md` | Projekt-Skills `test-isolated`, `ui-change`, `docs`; `answer-page`, `commit`, `preview` kommen global aus `~/ai_dev_workflow_global/skills/` (Kopien entfernt 2026-10-14) |

- Übernommen und angepasst aus dem Workspace `dev_ws` (ohne ROS-/Roboter-Teile, ohne `build-pkg` – das CMS hat keinen Build).
- Skripte und Templates eines Skills liegen im Ordner des Skills.
- Ausgaben (Seiten, Screenshots) nur im Scratchpad, nie hier.
- `settings.local.json` ist persönlich und nicht im Git.
