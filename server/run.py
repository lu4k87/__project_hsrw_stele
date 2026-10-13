"""Einstieg: Stele CMS starten (waitress, sonst Flask-Server threaded ohne Reloader).

Umgebungsvariablen: STELECMS_HOST, STELECMS_PORT, STELECMS_DATA, STELECMS_SECRET_KEY,
STELECMS_BACKGROUND, STELECMS_SEED_DEMO (s. docs/SPEC.md §2).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stelecms import config as app_config, create_app  # noqa: E402
from stelecms.seed import log_player_links  # noqa: E402

# waitress puffert den Body vor jeder Anmeldeprüfung → feste Obergrenze = größte einstellbare Dateigröße + Reserve
MAX_BODY_BYTES = (128 + 1) * 1024 * 1024


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log = logging.getLogger("stelecms")
    app = create_app()
    host, port = app.config["HOST"], app.config["PORT"]
    if not app_config.is_loopback_host(host) and not (app.config["HTTPS"] or app.config["ALLOW_INSECURE_LAN"]):
        raise SystemExit("Netzwerkbindung abgelehnt: STELECMS_HTTPS=1 oder ausdrücklich "
                         "STELECMS_ALLOW_INSECURE_LAN=1 setzen.")
    if not app_config.is_loopback_host(host) and app.config["ALLOW_INSECURE_LAN"] and not app.config["HTTPS"]:
        log.warning("Unsicherer HTTP-LAN-Betrieb wurde ausdrücklich aktiviert; keine Zugangsdaten über fremde Netze senden.")
    log.info("Stele CMS läuft auf http://%s:%s/admin/ (Daten: %s)", host, port, app.config["DATA_DIR"])
    log_player_links(app)
    log.info("Eigenes Administratorkonto anlegen: .venv/bin/python server/manage.py create-admin "
             "--username <name> --display-name \"<Anzeigename>\" (Passwort wird erzeugt, wenn --password fehlt)")
    try:
        from waitress import serve
    except ImportError:
        log.warning("waitress nicht installiert – Flask-Entwicklungsserver wird verwendet.")
        app.run(host=host, port=port, threaded=True, use_reloader=False, debug=False)
        return
    serve(app, host=host, port=port, threads=16, ident="SteleCMS", channel_timeout=300,
          max_request_body_size=MAX_BODY_BYTES)


if __name__ == "__main__":
    main()
