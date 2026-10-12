---
name: stele-deployment-target
description: "Zielumgebung Stele CMS an der Hochschule (Stand 2026-10-05) – Server Windows 11 oder Linux (offen), Stele Windows, interne CA, nur Hochschulnetz/VPN"
metadata:
  node_type: memory
  type: project
  originSessionId: 701461b2-d4f7-421b-9ac5-6bef533e223e
  modified: 2026-10-05T17:31:14.198Z
---

Entscheidungen des Users für den späteren Betrieb von [[stele-cms-moved]] (Stand 2026-10-05):
- Server: **Windows 11** vorgesehen, Linux-Server/-VM der IT wird noch angefragt (wäre bevorzugt: systemd, nginx, journald) → Dienst (WinSW o. ä.), Proxy mit TLS (Caddy vorgeschlagen), Datei-Log mit Rotation, ffmpeg/poppler nicht über Nutzer-PATH
- HTTPS: **eigene interne CA** gewählt (Alternative DFN/HARICA-Zertifikat der IT noch zu klären)
- Stelen-PC: **Windows** (Edge- oder Chrome-Kiosk offen)
- Zugriff Redaktion: nur Hochschulnetz/VPN, eigene CMS-Konten, kein SSO
- Geplante Pakete: 1 Absichern (Produktionsmodus, ProxyFix, Secure-Cookie, /healthz, Backup/Restore), 2 `deploy/windows/`, 3 Stelen-Kiosk-Skript, 4 Nachweis (CI Windows, VM-Probelauf, Fehlerproben, Dauerlauf)

**Why:** User klärt offene Punkte (DNS-Name, Zertifikat, Edition, Firewall, Backup, Datenschutz) erst an der Hochschule, bevor umgesetzt wird.

**How to apply:** Bei Stele-Betriebsthemen beide Wege (Windows 11 / Linux) offenhalten, bis die IT antwortet; vor Umsetzung nach den Antworten der Hochschul-Checkliste fragen. Feste Herkunft (DNS-Name + HTTPS) vor dem ersten echten Koppeln festlegen.
