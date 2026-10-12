#!/usr/bin/env python3
"""PreToolUse-Hook für Claude Code: warnt vor Befehlen/Edits, die AGENTS.md §0/§4 verletzen.

Läuft vor jedem Bash/Edit/Write (Einstellung in .claude/settings.json), braucht < 50 ms.
Blockiert nie – die Warnung geht als Kontext an Claude und als Meldung an den User:
  Bash   pkill/killall/kill -1            nur eigene Prozesse per PID beenden
         run.sh/run.py ohne eigenes STELECMS_DATA oder auf Port 8090   Instanz des Users
         curl/Anfragen an Port 8090 mit POST/PUT/PATCH/DELETE          ändert Daten des Users
         schreibt/löscht in data/ oder .venv/
         git push --force, --no-verify, git add -A/.
  Edit   data/, .venv/, bestehende Migrationen, secret_key
Schreibt ein Bash-Befehl evtl. Dateien, merkt er sich die Startzeit (STAMPS/<tool_use_id>);
post_edit_check.py prüft danach die geänderten Dateien wie bei Edit/Write.
Parallele Chats (chat_claims.py): Ändern oder Committen einer Datei mit uncommitteten
Änderungen eines anderen Chats/unbekannter Quelle → Hinweis mit Titel des Chats.
"""
import json
import re
import sys
import time
from pathlib import Path

import chat_claims
from chat_claims import STAMPS

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = 'server/stelecms/migrations/'

KILL = re.compile(r'\b(pkill|killall)\b|\bkill\s+(-\S+\s+)*-?[01]\s*($|[;&|)])')
START = re.compile(r'(\./run\.sh|server/run\.py)\b')
USER_PORT = re.compile(r'(127\.0\.0\.1|localhost):8090\b')
MUTATING = re.compile(r'-X\s*(POST|PUT|PATCH|DELETE)|--data\b|-d\s|--request\s+(POST|PUT|PATCH|DELETE)')
WRITES = re.compile(r'\bsed\s+(-\w*\s+)*-i|\btee\b|\b(cp|mv|rm|rmdir|truncate)\s|(^|[^0-9&])>{1,2}\s*[^&\s]')
PROTECTED_IN_CMD = re.compile(r'(^|[\s"\'=])(\./)?(data|\.venv)/|/(stele_hsrw|__project_Stele|__project_hsrw_stele)/(data|\.venv)/|secret_key|\bcms\.db')
VENV_EXE = re.compile(r'(\./)?\.venv/bin/\S+')  # Programmaufruf aus .venv ist kein Schreibzugriff
GIT_RISKY = re.compile(r'\bgit\s+push\b[^;&|]*(--force\b|-f\b)|--no-verify\b|\bgit\s+add\s+(-A|--all|\.)(\s|$)')
MESSAGE = re.compile(r'''(-m|--message)(\s+|=)("(?:[^"\\]|\\.)*"|'[^']*')''', re.S)  # Commit-Texte
HEREDOC = re.compile(r'<<-?\s*([\'"]?)(\w+)\1')
# Schreibzugriffe inkl. Heredoc-Skripte (python3 - <<EOF ... open(p, 'w') / write_text)
MAY_WRITE = re.compile(WRITES.pattern + r'|\bperl\s+-\w*i|\bgit\s+(apply|checkout|restore)\b'
                       r'|write_text\(|\.write\(|open\([^)]*[\'"][wa]\+?[\'"]')
GIT_STAGE = re.compile(r'\bgit\s+(add|commit)\b')
GIT_ALL = re.compile(r'\bgit\s+(add\s+(-A|--all|\.)(\s|$)|commit\s+(-\w*a\b|--all))')


def strip_heredocs(cmd):
    """Entfernt Heredoc-Inhalte und Commit-Nachrichten, damit Text keinen Alarm auslöst."""
    out, end = [], None
    for line in MESSAGE.sub(' ', cmd).split('\n'):
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = HEREDOC.search(line)
        if m:
            end = m.group(2)
    return '\n'.join(out)


def check_bash(cmd):
    cmd = strip_heredocs(cmd)
    warns = []
    if KILL.search(cmd):
        warns.append('Breites pkill/killall/kill trifft evtl. die Instanz des Users oder Browser anderer Chats '
                     '(AGENTS.md §0) – nur eigene Prozesse per PID beenden, vorher cwd und Port prüfen.')
    if START.search(cmd) and ('STELECMS_DATA=' not in cmd or re.search(r'STELECMS_PORT=8090\b', cmd)
                              or 'STELECMS_PORT=' not in cmd):
        warns.append('CMS-Start ohne eigenes STELECMS_PORT (18090–18099) und STELECMS_DATA im Scratchpad '
                     'läuft auf Port 8090/data/ des Users – isoliert starten (Skill test-isolated).')
    if USER_PORT.search(cmd) and MUTATING.search(cmd):
        warns.append('Ändernde Anfrage an Port 8090 = Arbeitsstand des Users – nur gegen eine Testinstanz.')
    if WRITES.search(cmd) and PROTECTED_IN_CMD.search(VENV_EXE.sub('', cmd)):
        warns.append('Befehl schreibt/löscht evtl. in data/, .venv/, cms.db oder secret_key – '
                     'Laufzeitdaten des Users nie anfassen (AGENTS.md §0).')
    if GIT_RISKY.search(cmd):
        warns.append('git --force, --no-verify oder „git add -A/.“ – nur eigene Pfade committen '
                     '(git commit -- <pfade>), nie --force, --no-verify nur mit Auftrag (AGENTS.md §4).')
    return warns


def check_edit(fp):
    try:
        path = Path(fp).resolve()
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        return []  # außerhalb des Repos (z. B. Scratchpad)
    warns = []
    if re.match(r'(data|\.venv)/', rel) or rel.endswith('secret_key'):
        warns.append(f'{rel}: Laufzeitdaten/Umgebung – nie von Hand ändern (AGENTS.md §0).')
    if rel.startswith(MIGRATIONS) and rel.endswith('.sql') and path.exists():
        warns.append(f'{rel}: bestehende Migration – nicht ändern, neue Datei anlegen (AGENTS.md §4).')
    return warns


def claim_warnings_edit(data, fp):
    try:
        rel = Path(fp).resolve().relative_to(ROOT).as_posix()
        return chat_claims.conflicts(data, [rel], chat_claims.dirty_files())
    except Exception:  # Hook darf nie scheitern (z. B. außerhalb des Repos)
        return []


def claim_warnings_bash(data, cmd):
    """Uncommittete Dateien, die der Befehl nennt (bzw. bei git add -A/commit -a alle) + Staging."""
    try:
        dirty = chat_claims.dirty_files()
        shell = strip_heredocs(cmd)  # git-Befehle nur außerhalb von Heredoc-Text
        committing = bool(GIT_STAGE.search(shell))
        if GIT_ALL.search(shell):
            rels = sorted(dirty)
        else:
            rels = [r for r in dirty if r in cmd or Path(r).name in cmd]
        if re.search(r'\bgit\s+commit\b', shell):
            rels += sorted(chat_claims.staged_files())
        return chat_claims.conflicts(data, rels, dirty, committing)
    except Exception:  # Hook darf nie scheitern
        return []


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = data.get('tool_name', '')
    inp = data.get('tool_input') or {}
    if tool == 'Bash':
        cmd = inp.get('command') or ''
        may_write = MAY_WRITE.search(cmd)
        if data.get('tool_use_id') and may_write:
            try:
                STAMPS.mkdir(exist_ok=True)
                (STAMPS / data['tool_use_id']).write_text(str(time.time()))
            except OSError:
                pass
        warns = check_bash(cmd)
        if may_write or GIT_STAGE.search(strip_heredocs(cmd)):
            warns += claim_warnings_bash(data, cmd)
    else:
        fp = inp.get('file_path') or inp.get('notebook_path')
        warns = check_edit(fp) if fp else []
        if fp:
            warns += claim_warnings_edit(data, fp)
    if not warns:
        return 0
    text = 'Sicherheits-Hook (nur Warnung, nicht blockiert):\n- ' + '\n- '.join(warns)
    print(json.dumps({
        'systemMessage': '⚠️ ' + text,
        'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': text},
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
