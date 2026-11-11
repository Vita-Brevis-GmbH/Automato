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
   ./make-certs.sh ca                    # einmalig; pki/agents-ca.key sicher verwahren
   ./make-certs.sh server 10.0.12.12   # Adresse, unter der die Agenten das Gateway erreichen
   ./make-certs.sh agent <agent-name>    # je Agent, Name wie im UI ("Collector-Agenten")
   ```

   Das Server-Zertifikat darf auch von der eigenen CA kommen (`pki/gateway.crt` + `.key`
   ersetzen); dann pinnen die Agenten diese CA statt `agents-ca.crt`.

2. **`.env`** der Automato-VM:

   ```bash
   AUTOMATO_AGENT_GATEWAY_HOST=10.0.12.12
   AUTOMATO_AGENT_GATEWAY_SECRET=<openssl rand -hex 32>
   AUTOMATO_INTERNAL_MTLS_MODE=bind
   # AUTOMATO_AGENT_GATEWAY_PORT=46100   # Port auf der VM, Vorgabe 8443 (1.5.1)
   ```

   Starten: `docker compose --profile core --profile agent-gateway up -d` (steht
   `COMPOSE_PROFILES` in `.env`: nur `docker compose up -d`). Der Orchestrator startet nicht,
   wenn der Modus aktiv ist und das Secret fehlt. Name und Port kennt auch die Einrichtung im
   Web-UI (Collector-Agenten → Einrichtung): sie schlägt die Gateway-Adresse samt
   Zertifikatszeilen für die `agent.env` vor. Im Container lauscht Caddy immer auf 8443; der
   Port ändert nur die Veröffentlichung auf der VM, die Zertifikate bleiben gültig.

3. **Agent** (`agent.env` auf der Station): Orchestrator-Adresse auf das Gateway umstellen und
   Zertifikate eintragen:

   ```ini
   AUTOMATO_ORCHESTRATOR_URL=https://10.0.12.12:8443
   AUTOMATO_ORCHESTRATOR_CA_BUNDLE=C:\Automato\pki\agents-ca.crt
   AUTOMATO_CLIENT_CERT=C:\Automato\pki\agent-<name>.crt
   AUTOMATO_CLIENT_KEY=C:\Automato\pki\agent-<name>.key
   ```

   Die drei Dateien aus `pki/` auf die Station kopieren (den Schlüssel nur dorthin). Die Adresse
   muss zum Server-Zertifikat passen (Name bzw. IP aus `make-certs.sh server`), der Port zu
   `AUTOMATO_AGENT_GATEWAY_PORT`. `.\run-agent.ps1 -Check` muss danach „OK“ melden.

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

Zertifikat sperren: Agent im UI deaktivieren (Token ungültig) und neues Zertifikat ausstellen;
eine CRL prüft Caddy nicht – kurze Laufzeiten (825 Tage Standard, bei Bedarf kürzer) und
Rotation je Agent.
