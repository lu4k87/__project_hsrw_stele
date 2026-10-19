@echo off
chcp 65001 >nul
rem Stele CMS unter Windows starten: legt bei Bedarf .venv an, installiert Abhängigkeiten und startet server\run.py.
rem Gegenstück zu run.sh; Umgebungsvariablen (STELECMS_PORT, STELECMS_DATA, ...) gelten genauso.
setlocal
cd /d "%~dp0"

if defined PYTHON (
  set "PY=%PYTHON%"
) else (
  where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)
%PY% -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 oder neuer fehlt: https://www.python.org/downloads/ - bei der Installation "Add python.exe to PATH" anhaken.
  goto :fehler
)

rem Fehlt die Windows-Umgebung (neu oder von Linux kopiert), .venv neu anlegen
if not exist ".venv\Scripts\python.exe" (
  echo Lege virtuelle Umgebung .venv an ...
  %PY% -m venv --clear .venv || goto :fehler
)
rem Nur installieren, wenn sich requirements.txt seit der letzten Installation geändert hat (spart den Import-Test)
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
  echo Installiere Abhängigkeiten ...
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto :fehler
  copy /y requirements.txt .venv\requirements.installed >nul
)

.venv\Scripts\python.exe server\run.py %*
if errorlevel 1 (
  echo.
  echo Stele CMS beendet - bei einem Fehler die Meldung oben lesen.
  pause
)
exit /b

:fehler
echo.
echo Start fehlgeschlagen - Meldung oben lesen.
pause
exit /b 1
