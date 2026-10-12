---
name: stele-cms-moved
description: "Stele CMS liegt in ~/__project_hsrw_stele (seit 2026-10-12 echter Ordner neben dev_ws), Repo lu4k87/stele_hsrw, Port 8090"
metadata:
  node_type: memory
  type: project
  originSessionId: 42e76fc0-fbe9-4fc2-8481-6ce5739b7c8d
  modified: 2026-09-30T06:56:07.159Z
---

Das Stele CMS wurde am 2026-09-30 aus `dev_ws/CMS_STELE_KIOSK` nach `/home/mk/__project_Stele` verschoben (Repo `git@github.com:lu4k87/stele_hsrw.git`, öffentlich (Stand 2026-10-05), Branch `main`; CI GitHub Actions Ubuntu + Windows, Ergebnis per API ohne Login lesbar, Job-Logs nur mit Login → Fehler als Annotation); der alte Ordner ist gelöscht. Das CMS des Users läuft von dort auf `127.0.0.1:8090` mit `data/` als echtem Arbeitsstand.

**Why:** Eigenes Projekt neben dev_ws, eigene `AGENTS.md`, eigener Commit-Hook (`.githooks/pre-commit`).

**How to apply:** Stele-Aufgaben nicht in dev_ws suchen, sondern in `~/__project_hsrw_stele` arbeiten (am besten eigener Chat dort). Tests nur mit eigener Instanz (Port 18090–18099, eigenes `STELECMS_DATA`), nie gegen 8090. `python3-venv` fehlt auf dem Rechner → `.venv` mit `python3 -m venv --without-pip --system-site-packages .venv` bauen.

Am 2026-10-06 umbenannt: `~/stele_hsrw` → `~/__project_Stele` (Präfix-Konvention [[project-folder-prefix]]); `.venv`-Pfade und Claude-Verlauf (`~/.claude/projects/-home-mk---Project-Stele`) mitgezogen. Repo-Name auf GitHub bleibt `stele_hsrw`.
Am 2026-10-10 umbenannt: `stele_hsrw` → `__project_hsrw_stele` (Ordner, Pfade, GitHub-Repo `lu4k87/__project_hsrw_stele`, Pages-URL `https://lu4k87.github.io/__project_hsrw_stele/`).
Am 2026-10-12 ([Claude] dev-ws-d3): echter Ordner von `dev_ws/__project_hsrw_stele` nach `~/__project_hsrw_stele` verschoben (vorher war das nur ein Symlink darauf). GitHub-Repo `__project_hsrw_stele` nicht erreichbar, `stele_hsrw` hat denselben Stand → Remote wieder `git@github.com:lu4k87/stele_hsrw.git` (Pages-URL entsprechend `/stele_hsrw/` prüfen). `~/__project_Stele` = zweiter Klon (alter Remote) → am 2026-10-12 auf Wunsch des Users gelöscht (inkl. älterem `data/`-Stand vom 2026-10-06). Projektname = `HSRW_Stele` (User, 2026-10-12: `config/projects.yaml`, `dev_ws.code-workspace`, AGENTS.md).
