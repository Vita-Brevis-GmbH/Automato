# Projektkontext für Claude Code

Diese Datei fasst den aktuellen Planungsstand zusammen. Bitte immer zuerst lesen, bevor
an diesem Projekt weitergearbeitet wird.

## Was ist das

Automatisierungsplattform für heterogene Infrastruktur (Fortigate, Cisco ACI, VMware,
Proxmox, Windows, Linux, Cisco Switches). Details: siehe `README.md`, `ARCHITECTURE.md`,
`ROADMAP.md`, `SECURITY.md`, `docs/adr/`.

## Wichtigste Rahmenbedingung: Modularität pro Use Case

Das Projekt wird für **mehrere Kunden/Use Cases** eingesetzt, die unterschiedliche
Werkzeuge im Einsatz haben:
- Manche Kunden nutzen ServiceNow, andere nicht
- Manche Kunden nutzen NetBox als IPAM, andere Infoblox DDI
- Tools müssen **später austauschbar** sein, ohne dass Daten neu erfasst werden müssen

Daraus folgt: **Adapter-Pattern** an allen Stellen, wo ein Kunden-Tool angebunden wird.
Siehe `docs/adr/0002-sot-gateway-adapter-pattern.md` für das Detail-Design.

## Bevorzugter Tech-Stack (Stand jetzt)

- Sprache: Python (Orchestrator, Worker, Services)
- Schreibpfad (spätere Use Cases): Worker mit **Runner-Abstraktion** – Ansible
  primär, Terraform/OpenTofu je Use Case (z. B. VM-Templates auf VMware/Proxmox);
  State-Backend-Entscheid als ADR vor dem ersten schreibenden Use Case
  (siehe ARCHITECTURE.md, Abschnitt Automatisierung)
- Firewall-Regel-Compiler: **Aerleon** (Apache-2.0, Fork von Google Capirca, aktiv
  gepflegt, YAML-Policy-Sprache, Fortinet- und Proxmox-Generatoren vorhanden)
- Source of Truth: NetBox und/oder Infoblox DDI, je nach Kunde, über SoT-Gateway
  abstrahiert (siehe ADR 0002)
- Contracts: kundenabhängig entweder ServiceNow (falls vorhanden) oder ein eigenes
  Modul/iTop (offen, noch nicht final entschieden)
- **Grundsatz: Open Source bevorzugt.** Kommerzielle Tools (Tufin/AlgoSec/FireMon,
  ServiceNow) nur wo vom Kunden bereits vorhanden/vorausgesetzt, nie als Neu-Abhängigkeit
  für den Plattform-Kern.

## Architektur-Kernidee (siehe ADR 0002 für Details)

```
Web UI/Wizard → Rule & Service Store → SoT Gateway (Adapter: NetBox | Infoblox)
                                     → Topology Service (Primary/Backup-Pfade)
                                     → Policy Compiler (Aerleon)
                                     → Orchestrator → Worker (pro Zielplattform)
```

Kernprinzip: Rule & Service Store kennt nur abstrakte Referenzen, nie
tool-spezifische Formate. Migration eines Kunden von NetBox auf Infoblox (oder
umgekehrt) = neuer/anderer Adapter, keine Änderung am Rest der Plattform.


## Verbindliche Vorgaben (immer beachten)

- **Nur nach NetBox schreiben, sonst strikt nur lesen.** FortiGate, APIC und Infoblox DDI
  werden ausschliesslich gelesen (Nur-Lese-Schutz im Code, `sot_gateway.readonly`). Der
  einzige Schreibweg ist der NetBox-Sync (Vorschau → Übernehmen, ADR 0010). Neue
  Schreibpfade nur mit eigener ADR und Freigabe.
- **Testsysteme sind produktiv:** nur Read-only-Konten, gelesene Daten wie Produktionsdaten
  behandeln; Negativtests gegen Geräte (z. B. R16 `--check --strict`) nur opt-in.
- **Keine Secrets** in Chat, Code, Tests, Doku oder Commits; `.env`, `pki/`-Ordner und
  `docker-compose.override.yml` bleiben gitignored.
- **Der Name des Referenzkunden wird nirgends hinterlegt** (auch nicht über Gerätenamen);
  Beispiele anonymisiert (`example.ch`, „Kunde A“).
- **Kein CSV-Import.** Wörterbücher (Namensprofile) sind in-App gepflegte Laufzeitdaten
  **ohne Funktionsrelevanz**: die Plattform muss ohne befüllte Wörterbücher voll funktionieren.
- **Open Source bevorzugt**, Adapter-Pattern für Kunden-Tools (siehe oben).
- Kommunikation mit dem Anwender auf Deutsch; Commits/PRs nur auf den vereinbarten Branch,
  PRs nur auf Wunsch.

## Arbeitsweise

- Tests: `uv run --package <orchestrator|collectors|sot-gateway> pytest <paket>/tests`;
  Lint `uv run ruff check .` + `uv run ruff format --check .`; Frontend in
  `web-ui/frontend`: `npm run lint` (tsc) + `npm run build`. Frontend **nicht** mit prettier
  umformatieren (formatiert ganze Dateien um).
- Gegen Postgres: `AUTOMATO_TEST_DATABASE_URL=postgresql+psycopg://…` (Schema je Test neu);
  die CI fährt Migrationen hoch/runter und alle Orchestrator-Tests gegen Postgres 16.
- Jede Schemaänderung = Alembic-Migration (up/down gegen Postgres prüfen).
- **Version:** eine Plattform-Version in `VERSION`; setzen mit
  `python3 scripts/version.py set X.Y.Z` (CI prüft mit `check`), Änderungen in
  `CHANGELOG.md` (Patch = Korrektur, Minor = Funktion).
- Entscheide als ADR in `docs/adr/`, Risiken in `docs/RISKS.md`, Betrieb/Anwender in
  `docs/BETRIEB.md`. Der frühere ausführliche Statusverlauf (bis 0.4.2) steht in
  `docs/ENTWICKLUNGSVERLAUF.md`.

## Aktueller Stand (0.8.0)

UC1 „Firewall-Regel-Dokumentation (read-only)“ ist für Model 2 (FortiGate + NetBox) und
Model 1 (FortiGate + ACI + Infoblox) umgesetzt und läuft im Dev gegen die echten Systeme.

| Bereich | Stand | Verweise |
|---|---|---|
| Collector | FortiGate (Policies inkl. ISDB/Identität/ZTNA/Negation, Objekte, Services mit Attributen, VIPs/IP-Pools, Interfaces, Dual-Stack, UTM), APIC (Contracts tenant-bewusst per DN, externe EPGs, vzAny, Contract-Interfaces, **ESGs, Taboo, Preferred Groups**, Contract Master, Intra-EPG, Filter als Services, Inventar), Infoblox DDI + NetBox als Referenz | Lesevertrag `docs/collectors-read-contract.md`, ADR 0010 |
| Agent | externer Windows-Agent (Pull, `--poll --watch 20`/`--check`), Geräte + Zugangsdaten zentral, Selbst-Update, **signierte Updates**, mTLS über das **Agent-Gateway** | ADR 0008, 0009, 0011, 0013 |
| Store/Abgleich | Regeln, Objekte, Inventar, Referenzstand, Findings als abgeleiteter Zustand (je Befund Satz + Hinweis, `store/finding_text.py`), IPAM-Namen ohne Präfix (`naming.server_name`), NetBox-Sync | ADR 0004, 0010 |
| Suche/NAT | Regelsuche über alles inkl. Gruppenmitglieder, IP-im-Netz, IPAM-DNS-Namen, NAT (`store/rule_search.py`); Filter `object`/`nat`; NAT mit DNS, Server, Regeln; Links `#regeln?object=…` | – |
| Applikationen | eigenes Objekt mit AP/EPG/Server, **Vorschläge per Projektnummer**, Doku je Applikation (Markdown, Druck/PDF); Doku je System; publizierte Dienste (VIPs) | ADR 0012 |
| Standorte | **virtuell** (000, 001; in Netz-/Cluster-Namen) und **physisch** (Ort-Code, z. B. chzrh1), n:m-Zuordnung; Vorschlag aus Namen (`store/sites.py`), NetBox-Site-Gruppen; VMs ohne Standort im Namen | ADR 0015 |
| Betrieb | **Zeitplan je Quellsystem** (Cron, Europe/Zurich), **Alle einlesen** (`POST /jobs/collect-all`), **System-Seite**, Sicherung/Wiederherstellung (age), `deploy/update.sh` mit Rückfall | ADR 0013, `deploy/backup/`, `docs/BETRIEB.md` |
| Sicherheit | Keycloak (OIDC/PKCE, OTP, SAML-Vorlage), Rollen viewer/operator/admin, Mandanten-Scoping, Audit mit Allowlist (+ optional verschlüsselt), Secrets-Provider, Zugangsdaten Fernet, fail-closed Defaults | ADR 0005, 0007, 0013 |
| Web-UI | alle Ansichten nach Mockup, seitenweise Listen mit Gesamtzahl | `web-ui/frontend/` |

Tests: orchestrator 197, collectors 79, sot-gateway 14 (dazu die CI gegen Postgres).

## Offen / nächste Schritte

- **Phase A gegen die echten Systeme abnehmen** (DDI über den Agenten, Dev-NetBox, NetBox-Sync):
  Checkliste `docs/DEV-SETUP-AGENT.md` Abschnitt 6b.
- Produktiv schalten: Agent-Gateway `bind`, Pakete signieren, tägliche Sicherung, Updates nur
  über `deploy/update.sh` (ADR 0013).
- VM-Collector (vCenter/Proxmox → NetBox), Import-Wizard (E3), gemeinsames Cockpit mit
  Magister (ADR 0014, Entwurf), Schreibpfad (E4) nur mit eigener ADR.
- Applikationen: Regeln direkt zuordnen, Abhängigkeiten zwischen Applikationen (ADR 0012).
