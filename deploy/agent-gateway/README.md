# mTLS-Agent-Gateway

Produktions-Gate für den Agent-Kanal (ADR 0008/0013): Collector-Agenten erreichen den
Orchestrator **nur** über dieses Gateway. Caddy verlangt ein Client-Zertifikat der Agent-CA
(TLS 1.2/1.3) und reicht nur die Agent-Pfade weiter (`/internal/agent/*`, Ergebnis- und
Referenz-Upload). Der Pfad `…/target` des zentralen Workers bleibt intern.

Der Orchestrator prüft zwei Header, die **nur das Gateway** setzt (vom Client gesendete Werte
überschreibt es): `X-Gateway-Secret` (der Aufruf kommt vom Gateway) und
`X-Client-Cert-Subject` (geprüftes Zertifikat).

| `AUTOMATO_INTERNAL_MTLS_MODE` | Wirkung |
|---|---|
| `off` (Standard) | Agent-Token genügt (Dev/Lab) |
| `require` | Agent-Aufrufe nur über das Gateway mit gültigem Client-Zertifikat |
| `bind` | zusätzlich **CN des Zertifikats = Agent-Name** (Zertifikat und Token gehören zum selben Agenten) |

Der zentrale Worker (internes Token) spricht weiter direkt mit dem Orchestrator.

## Einrichten

1. **Zertifikate** (auf der Automato-VM, Ablage `pki/` ist gitignored):

   ```bash
   cd deploy/agent-gateway
   sudo ./make-certs.sh ca                    # einmalig; gibt die CA für die Oberfläche frei
   sudo ./make-certs.sh server 10.0.12.12     # Adresse, unter der die Agenten das Gateway erreichen
   ```

   Die Client-Zertifikate je Agent stellt seit 1.6.0 die Oberfläche aus (ADR 0052, Schritt 3).
   Dafür liest der Orchestrator `pki/agents-ca.key` (nur lesend eingebunden unter
   `/etc/automato/agent-ca`); `make-certs.sh ca` macht die Datei für ihn lesbar (Besitzer
   uid 1001, Modus 0400, Ordner 0711 – `gateway.key` bleibt root). Für eine CA aus einer
   Version vor 1.6.0 einmal `sudo ./make-certs.sh oberflaeche`, danach
   `sudo docker compose up -d`. Wer das nicht will: `sudo ./make-certs.sh oberflaeche aus` –
   dann Zertifikate nur von Hand (`./make-certs.sh agent <agent-name>`).

   Das Server-Zertifikat darf auch von der eigenen CA kommen (`pki/gateway.crt` + `.key`
   ersetzen); dann pinnen die Agenten diese CA statt `agents-ca.crt`.

2. **`.env`** der Automato-VM:

   ```bash
   AUTOMATO_AGENT_GATEWAY_HOST=10.0.12.12
   AUTOMATO_AGENT_GATEWAY_SECRET=<openssl rand -hex 32>
   AUTOMATO_INTERNAL_MTLS_MODE=bind
   # AUTOMATO_AGENT_GATEWAY_PORT=46100   # Port auf der VM, Vorgabe 8443 (1.5.1)
   ```

   Starten: `docker compose up -d` (Profile aus `COMPOSE_PROFILES` in `.env`, z. B.
   `core,collectors,agent-gateway`). Der Orchestrator startet nicht, wenn der Modus aktiv ist
   und das Secret fehlt. Im Container lauscht Caddy immer auf 8443; der Port ändert nur die
   Veröffentlichung auf der VM, die Zertifikate bleiben gültig. `sudo deploy/aufbau.sh env`
   prüft, ob Name und Secret gesetzt sind.

3. **Agent:** im Web-UI unter **Collector-Agenten** beim Agenten **Zugang** →
   **Zugang herunterladen**. Das ZIP enthält `agent.env` (Gateway-Adresse samt Port, neues
   Agent-Token, Zertifikatszeilen), `agent.crt`/`agent.key` (CN = Agent-Name, passt zu `bind`),
   `agents-ca.crt` und eine Anleitung. Inhalt nach `C:\Automato\collector` kopieren (bzw. neben
   `install.ps1` legen – das Skript übernimmt die Dateien); `.\run-agent.ps1 -Check` muss danach
   „OK“ melden. Jeder Download erneuert Token und Zertifikat; das alte Token gilt sofort nicht
   mehr, das alte Zertifikat bis zu seinem Ablauf (Modus `bind` verlangt aber Name und Token
   zusammen).

4. **Netz:** Port 8000 des Orchestrators nicht in die Agent-Netze freigeben – nur den Port des
   Gateways (Vorgabe 8443). In `DOCKER-USER` gilt der Port auf der VM
   (`--ctorigdstport <AUTOMATO_AGENT_GATEWAY_PORT>`, INSTALLATION-PROD 4.3); auf der Firewall
   davor eine reine TCP-Freigabe ohne TLS-Inspektion.

   Mit **TLS intern** (0.49.0, ADR 0023, `deploy/aufbau.sh tls-intern`) spricht das Gateway den
   Orchestrator per HTTPS an und prüft ihn gegen die interne CA – für die Agenten ändert sich
   nichts (sie pinnen weiter die Agent-CA bzw. das Gateway-Zertifikat).

## Prüfen

- ohne Client-Zertifikat: TLS-Abbruch („certificate required“),
- Zertifikat einer fremden CA: TLS-Abbruch,
- Zertifikat eines anderen Agenten (Modus `bind`): `403 client certificate does not belong to
  this agent`,
- direkt an Port 8000 mit Agent-Token: `403 agent channel requires the mTLS gateway`.

Zertifikat sperren: Agent im UI deaktivieren (Token ungültig) bzw. neuen Zugang herunterladen;
eine CRL prüft Caddy nicht – Laufzeit 825 Tage (höchstens bis zum Ablauf der CA), Rotation je
Agent über einen neuen Zugang. Jede Ausgabe steht im Audit (`agent.access_issued` mit
Seriennummer und Ablauf).
