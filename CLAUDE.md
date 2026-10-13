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

## Aktueller Stand / nächster Schritt

Planungsphase. Die Roadmap ist als Meilenstein-Plan **M0–M9 mit Exit-Kriterien**
aufgebaut (siehe `ROADMAP.md`); Risiken werden im Risikoregister `docs/RISKS.md`
geführt und an jedem Meilenstein aktualisiert.

M1 ist inhaltlich entschieden: **Use Case #1 = Firewall-Regel-Dokumentation
(read-only)** – Firewall-Regeln lesen, mit IPAM-Adressobjekten verknüpfen,
Service-Objekt-DB, Web-UI mit Filtern (Systeme/Netzwerke/Zonen), Applikations-Doku.
Zwei Kundenmodelle, **Model 2 zuerst** (FortiGate + **NetBox**, kein ACI), danach
Model 1 (FortiGate + ACI + **Infoblox**). Umsetzung deshalb **NetBox-Adapter zuerst**.
Das **Datenmodell ist als ADR 0004** entworfen (mandantenfähig; eine `rule`-Tabelle mit
polymorphen Endpunkten host|network|epg|zone; Zonen aus dem SoT; Services inkl.
ICMP/Dual-Stack; NAT-Schema; Findings für Datenqualität; Namenskonvention als
First-Class-Signal). Details/DoD: `docs/usecases/uc1-firewall-regel-dokumentation.md`
(**DoD bestätigt**, Model-2-Scope). Die Regel-Automatisierung gemäss ADR 0002 folgt als
späterer Use Case.

Der **Grundaufbau (M6) ist als Code-Gerüst umgesetzt** (Entscheide: ADR 0003):
uv-Workspace mit `orchestrator/` (FastAPI, Alembic, Audit-Log, Job-Kreislauf),
`collectors/` (FortiGate/ACI-Skeleton, Beispieldaten bis zum Lab), `sot-gateway/`
(NetBox- und Infoblox-Adapter lesend), `web-ui/frontend/` (React/Vite-Skeleton);
dazu docker-compose (Profile core/collectors), CI (`.github/workflows/ci.yml`)
und pre-commit/gitleaks. Tests: `uv run --package <name> pytest <name>/tests`.

Das **Datenmodell (M5, ADR 0004) ist als Code umgesetzt (Phase 1)**: SQLAlchemy-Modelle
in `orchestrator/app/models/` (tenant, zone, network_object, service_object, rule,
rule_endpoint, rule_service, finding), Alembic-Migration `0002`, Namenskonvention-Parser
`app/naming.py`, Store-Query + `/rules`-Endpunkt, Tests. ACI-Tabellen (`epg*`) und
`nat_rule` folgen mit ihrer Phase.

Die Gates **M1 (DoD bestätigt), M2 (Risikoregister R1–R14) und M3 (Security-Review,
ADR 0005)** sind erledigt; erste Sofort-Härtungen umgesetzt (Fail-closed Token-Check,
CORS aus Konfiguration).

Der **M7-Durchstich ist codeseitig fertig und fixture-getestet** (Model 2): FortiGate-Collector
mit echtem FortiOS-REST-Abruf (rein lesend, `collectors/fortigate/client.py`) inkl.
Adress-/Service-Auflösung (`normalize.py`: Objektnamen→Werte, FQDN/`H-EXT_` bleiben Name),
NetBox-Adapter (lesend), Ingest/Matching in den Rule & Service Store, Findings, interne
API verdrahtet (`app/store/ingest.py`, `app/sot.py`, Tests `test_ingest.py`/
`test_internal_ingest.py`/`test_fortigate_client.py`). Ohne Lab-Creds liest der Collector
Beispieldaten; mit `AUTOMATO_FORTIGATE_URL`/`_TOKEN` den echten Abruf. Der
**CLI-Auslöser** ist gebaut (`orchestrator/app/cli.py`, Konsolen-Command `automato`:
ensure-tenant/ensure-source/collect/status/lab, `test_cli.py`). Das **Lab-Setup ist
scriptbar** (`lab/install-prereqs.sh`, `lab/up-automato.sh`, `lab/README.md`;
`source-of-truth/netbox/`, `collectors/fortigate/LAB.md`); docker-compose reicht die
NetBox-/FortiGate-Creds an Orchestrator/Collector durch, NetBox läuft als eigener
Stack auf Port 8080 (Orchestrator auf 8000).

Als Nächstes: Lauf gegen das **echte Lab** (Lab-Creds setzen, echter Abruf statt
Beispieldaten via `lab/README.md`), dann M8/M9.

**M7 real erreicht + M8-Review erledigt:** Auf einer VM wurden per `lab/bootstrap.sh`
75 echte FortiGate-Policies read-only via FortiOS-REST gelesen und End-to-End in den
Store geschrieben. Nach Bootstrap-Import der FortiGate-Objekte nach NetBox
(`source-of-truth/netbox/import_from_fortigate.py`, opt-in, schreibt mit Admin-Token;
Collector bleibt read-only) und typ-bewusster Einordnung: **0 nicht aufgelöste
Endpunkte, 0 offene Findings**. Der Collector liefert den FortiGate-Objekttyp mit
(ipmask/interface-subnet/iprange/fqdn/geo/mac/dynamic/vip); der Orchestrator ordnet ein
(fqdn=expected_external, geo/mac/dynamic=not_applicable, VIP über extip). **Web-UI**
(`web-ui/frontend/`) zeigt Regeln-Tabelle, Regel-Detail (aufgelöste Endpunkte/Zonen/
Services) und Findings mit Filtern (System/Zone/Aktion/Freitext); API: `/rules`,
`/rules/{id}`, `/findings`, `/systems`. Erkenntnisse/Entscheide: **ADR 0006**;
Risiken R15–R17 ergänzt.

Als Nächstes (M9, UC1 abschliessen): Applikations-Doku/Export, VIP-NAT-Mapping
(extip→mappedip, Phase 1.5), Zonen aus Interfaces/SoT, Betriebs-/Anwender-Doku,
Security-/Hardening-Check. RBAC/Secrets/mTLS/Netzsegmentierung als Folge-ADRs (ADR 0005).

**M9 erledigt – UC1 Model 2 funktional vollständig & Lab-/Review-reif:** Zusätzlich zum
Obigen umgesetzt: VIP-NAT (`nat_rule`, Alembic 0004; `/nat`; Regel-Detail zeigt extip→
mapped), Zonen aus FortiGate-Interfaces am Subnetz verortet (Import tagt interface-subnets),
**Applikations-Doku** (`/applications`, Markdown-Export im UI). API gesamt: `/rules`,
`/rules/{id}`, `/findings`, `/nat`, `/applications`, `/systems`, `/jobs`. DoD-Abgleich +
8-Prinzipien-Security-Check im UC1-Dokument; Betriebs-/Anwenderhandbuch `docs/BETRIEB.md`.
Sofort-Härtung: öffentliche Read-API ist **fail-closed ausserhalb `dev`**
(`AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API`), da AuthN/RBAC noch fehlt.

**Offenes Produktions-Gate:** AuthN/RBAC vor der Read-API; optional Finding-Triage-
Schreibpfad; mTLS/at-rest, Netzsegmentierung (Folge-ADRs). Danach: Model 1 (ACI + Infoblox),
Schreibpfad (ADR 0002). Tests: collectors 7, orchestrator 28, sot-gateway 6.

**Ausbaustufe B (Planung läuft):** Ausbau zur allgemeinen Workflow-Plattform (über
Firewalls/ACI hinaus: VMs, App-Install, Auto-Doku; Open Source, Vereinfachung) mit
starker **Synergie zu Projekt Magister** (`Vita-Brevis-GmbH/magister`, fast identischer
Stack → gemeinsame Bausteine Auth/Cockpit/Deploy, Module später vereinen). Epics W1–E7
in `ROADMAP.md` (Abschnitt „Ausbaustufe B"). Entschieden: **AuthN/RBAC via Keycloak**
(dedizierter IdP, eigener Container→VM, eigene URL, OIDC+PKCE; lokal + SAML + OTP;
geseedter globaler Admin; Mandanten-Scoping zentral erzwingen; Rollen `viewer/operator/
admin`; Secrets-/Vault-Provider) — **ADR 0007**. Reihenfolge: **Web-UI (W1) vorgezogen**,
dann **E1 Härtung/RBAC** als Schwerpunkt. UI-Mockup liegt als Artifact vor (nicht im Repo).
**Ausstehend vom Kunden:** UC1-Fachdaten (ACI, Namenskonventionen, Tenant-Struktur,
Confluence-Feldschema) → präzisieren „Applikationen" (= bestimmte Systeme, nicht VIPs)
und Model 1; blockieren E1/E2 nicht.
