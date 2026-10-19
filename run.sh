#!/usr/bin/env bash
# Stele CMS starten: legt bei Bedarf .venv an, installiert Abhängigkeiten und startet server/run.py.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  echo "Lege virtuelle Umgebung .venv an …"
  "$PY" -m venv .venv
fi
# Nur installieren, wenn sich requirements.txt seit der letzten Installation geändert hat (spart den Import-Test)
if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installiere Abhängigkeiten …"
  .venv/bin/python -m pip install -q -r requirements.txt
  cp requirements.txt .venv/requirements.installed
fi
exec .venv/bin/python server/run.py "$@"
