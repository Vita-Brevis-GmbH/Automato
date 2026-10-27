# Roadmap

Prinzip: klein anfangen, jeder Meilenstein liefert ein überprüfbares Ergebnis, Ausbau erfolgt
schrittweise bis zur Full-Stack-Automatisierung.

Die Roadmap ist als **Meilenstein-Plan (M0–M9)** aufgebaut, nicht als Kalenderplan. Jeder
Meilenstein hat ein **Exit-Kriterium (Gate)** – erst wenn es erfüllt ist, startet der nächste
Meilenstein. Ziel: Fehlentwicklungen und nötige Änderungen **früh** erkennen, nicht erst in der
Umsetzung. Das Risikoregister (`docs/RISKS.md`) wird an jedem Meilenstein aktualisiert.

## Überblick

| Meilenstein | Inhalt | Exit-Kriterium (Kurzform) |
|---|---|---|
| M0 | Grundstruktur | Repo-Struktur & ADR-Prozess stehen |
| M1 | Use Cases erarbeiten & priorisieren | Use Case #1 mit Definition of Done dokumentiert |
| M2 | Grösste Risiken identifizieren | Risikoregister mit Gegenmassnahmen existiert |
| M3 | Architektur definieren & Security-Review | Architektur security-seitig freigegeben |
| M4 | Microarchitektur | Schnittstellen für den Durchstich spezifiziert |
| M5 | Datenmodell & Speicherorte | Datenmodell + Persistenz-Entscheid als ADR |
| M6 | Grundaufbau: Skripte, Installation, Labor | Komponenten installierbar, Lab erreichbar |
| M7 | Durchstich (Walking Skeleton) | Ende-zu-Ende-Änderung im Lab inkl. Audit-Trail |
| M8 | Review-Checkpoint | Architektur/ADRs bestätigt oder angepasst |
| M9 | Ersten Use Case abschliessen | Definition of Done erfüllt, Security-Check bestanden |

## M0 – Grundstruktur

- [x] Git-Repo-Struktur aufsetzen (Monorepo mit Komponenten-Ordnern, siehe ADR 0001)
- [x] Erste ADRs für Kernentscheidungen dokumentieren (ADR 0001 Monorepo, ADR 0002 SoT Gateway)
- [ ] ADR-Prozess bestätigen: jede Kernentscheidung wird als ADR festgehalten

**Exit-Kriterium:** Struktur und Entscheidungsprozess stehen, keine offenen Strukturfragen.

## M1 – Use Cases erarbeiten & priorisieren

Zuerst dorthin, wo der Need am grössten ist.

- [x] Use-Case-Kandidaten gesammelt (Firewall-Regel-Automatisierung,
  Read-only-Dokumentation & Compliance, VM-Provisioning)
- [x] Priorisierung nach grösstem Need → **Use Case #1 festgelegt:
  Firewall-Regel-Dokumentation (read-only)** – Regeln von FortiGate und Cisco ACI
  lesen, mit Infoblox-Adressobjekten verknüpfen, Service-Objekt-DB, Web-UI mit
  Filtern (Systeme/Netzwerke/Zonen), Applikations-Doku.
  Details: [docs/usecases/uc1-firewall-regel-dokumentation.md](docs/usecases/uc1-firewall-regel-dokumentation.md)
- [x] **Definition of Done** für Use Case #1 bestätigt (2026-07-22; Model-2-Scope,
  outcome-basiertes Matching-Kriterium – siehe UC1-Dokument)
- [x] Kunden-Tool-Paar fixiert (zwei Modelle, siehe UC1-Dokument): **Model 2 zuerst**
  (FortiGate, IPAM = **NetBox**, DNS = AD), danach Model 1 (FortiGate + ACI, IPAM =
  **Infoblox**). Umsetzungsreihenfolge deshalb: **NetBox-Adapter zuerst**, Infoblox in
  Phase 2 (kehrt die frühere „Infoblox-first"-Notiz um). Contracts (ServiceNow vs. iTop)
  für UC1 nicht relevant – vertagt. Austauschbarkeit über die Adapter-Interfaces (ADR 0002);
  Mandantenfähigkeit über die per-Mandant-SoT-Bindung (ADR 0004)

Die Firewall-Regel-**Automatisierung** (ADR 0002: Rule-Intents, Aerleon,
Primary/Backup-Pfade) folgt als späterer Use Case und baut auf den in UC1
erfassten Daten auf.

**Exit-Kriterium:** Use Case #1 ist mit **bestätigter** Definition of Done dokumentiert. ✓

## M2 – Grösste Risiken identifizieren

Bewusst **vor** der Architektur-Finalisierung, damit Risiken die Architektur beeinflussen können.

- [x] Risikoregister erstellt → [docs/RISKS.md](docs/RISKS.md) (R1–R14)
- [x] Top-Risiken mit Gegenmassnahmen und Meilenstein-Bezug versehen
- [x] Review-Rhythmus festgelegt: Register wird an jedem Meilenstein-Abschluss aktualisiert
  (Review-Datum im Register vermerkt); R13/R14 aus der Mandantenfähigkeit ergänzt

**Exit-Kriterium:** Register existiert, jedes Top-Risiko hat eine Gegenmassnahme. ✓

## M3 – Architektur definieren & mit Security prüfen

- [x] Architektur gegen Use Case #1 und das Risikoregister validiert (ADR 0005: trägt UC1,
  Tenant-Scoping als einzige substanzielle Ergänzung, kein Blocker)
- [x] Security-Review anhand `SECURITY.md` durchgeführt → **ADR 0005** (8 Prinzipien,
  Befund/Entscheid je Punkt)
- [x] Offene Security-Punkte terminiert: Secrets-Backend, Netzwerksegmentierung,
  RBAC-Rollenmodell, mTLS/Zertifikate als Folge-ADRs vermerkt (ADR 0005)
- [x] Sofort-Härtungen umgesetzt: Fail-closed Token-Check, CORS aus Konfiguration,
  Tenant-Scoping-Pflicht in Store-Queries

**Exit-Kriterium:** Architektur security-seitig freigegeben; offene Punkte haben eine
ADR-Nummer oder sind bewusst und dokumentiert vertagt. ✓ (ADR 0005)

## M4 – Microarchitektur definieren

Feinschnitt der Services, so weit, dass der Durchstich ohne Grundsatzdiskussionen baubar ist.

- [ ] Orchestrator-API, Job-Modell und Worker-/Collector-Protokoll (Job annehmen, Ergebnis melden) spezifizieren
- [ ] Collector-Interface (read-only) für FortiGate und Cisco ACI spezifizieren –
  inkl. Mapping ACI-Contracts/EPGs → normalisiertes Regelmodell (UC1)
- [ ] SoT-Gateway-Adapter-Interface spezifizieren (kanonisches Datenmodell, ADR 0002;
  **erster Adapter: NetBox** für Model 2 – Infoblox in Phase 2)
- [ ] Topology-Service-Schnittstelle spezifizieren (für UC1 nur das Zonen-Modell zur
  Filterung; Connections/Primary/Backup erst für die spätere Automatisierung)
- [ ] Kommunikationswege und Authentisierung zwischen den Services festlegen (mTLS, signierte Jobs)

**Exit-Kriterium:** Schnittstellen-Verträge dokumentiert; Durchstich (M7) ist damit planbar.

## M5 – Datenmodell definieren & Speicherorte fixieren

Datenmodell ist als **ADR 0004** entworfen (mandantenfähig, eine `rule`-Tabelle mit
polymorphen Endpunkten, Zonen aus dem SoT, Services, NAT, Findings; Namenskonvention
als First-Class-Signal). Löst zugleich offene Punkte aus ADR 0002.

- [x] Datenmodell-Entwurf als ADR 0004 festgehalten (Ist-Regeln, Objekte, Services,
  NAT-Schema, Findings; Rule-**Intents**/Topology für den späteren Schreibpfad vorgesehen)
- [x] Zonen-Zuordnung entschieden: Zone am Subnetz, im SoT gepflegt, von Automato gelesen
- [x] Applikations-Zuordnung: vorgesehen (`application` am Objekt), Befüllung später
  (Namenskonvention/CMDB)
- [x] Speicherorte fixiert: IPAM-Objekte via SoT Gateway; Regeln/Objekte/Services/
  Findings im Rule & Service Store (Postgres führend; Git-Sync erst mit den
  Rule-Intents, ADR 0004)
- [x] DDL/SQLAlchemy + Alembic-Migration **Phase 1** umgesetzt (`orchestrator/app/models/`,
  Migration `0002`: tenant, zone, network_object, service_object, rule, rule_endpoint,
  rule_service, finding) inkl. Namenskonvention-Parser (`app/naming.py`) und Tests.
  ACI-Tabellen (`epg*`) und `nat_rule` folgen mit ihrer Phase.

**Exit-Kriterium:** Datenmodell als ADR dokumentiert (✓ ADR 0004), Schema (Phase 1)
umgesetzt und migrierbar, Speicherorte fixiert. ✓

## M6 – Grundaufbau: Skripte, Installation, Laborumgebung

- [x] Basis-CI (Lint, Tests) eingerichtet – selektiv pro Komponente
  (`.github/workflows/ci.yml`: Ruff + pytest je Package, Frontend-Build, gitleaks)
- [x] Code-Gerüst steht (siehe ADR 0003): Orchestrator (FastAPI, Alembic, Audit-Log,
  Jobs), Collectors (FortiGate/ACI-Skeleton mit Beispieldaten), `sot-gateway`
  (NetBox- und Infoblox-Adapter lesend, gegen Fixtures getestet), Web-UI-Skeleton (React/Vite)
- [x] Lab-Setup dokumentiert/skriptet: NetBox (`source-of-truth/netbox/setup.sh` + seed),
  FortiGate (`collectors/fortigate/LAB.md`)
- [x] `docker-compose` vervollständigt (Postgres, Redis, Orchestrator, Collector,
  Web-UI; Profile `core`/`collectors`, Healthchecks, `.env.example`)
- [x] Secrets-Handling Grundausbau: `.env` (nie committet), gitleaks in pre-commit
  und CI; Ansible Vault folgt, sobald Lab-Credentials existieren (SECURITY.md)
- [ ] Source of Truth anbinden: **Model 2 zuerst → NetBox** (Testinstanz), Infoblox in
  Phase 2; Naming-/Tagging-Standards inkl. Zonen-Tag am Subnetz definieren
- [ ] **Laborumgebung** für den ersten Durchstich (Model 2): FortiGate-VM + NetBox.
  Model 1 (Phase 2): Infoblox-Testinstanz + Cisco ACI (DevNet-Sandbox / ACI-Simulator).
  Der Durchstich läuft **nie** gegen Produktion

**Exit-Kriterium:** Alle für den Durchstich nötigen Komponenten sind installierbar,
das Lab ist erreichbar.

## M7 – Durchstich (Walking Skeleton)

Eine bewusst **kleine Tätigkeit**, aber über den kompletten Pfad. Für UC1
(read-only) heisst das: **lesen statt schreiben** – und zwar gegen **Model 2**
(FortiGate + NetBox, kein ACI, kein NAT).

**Code-Pfad implementiert und fixture-getestet** (kein Lab nötig): FortiGate-Collector
mit echtem FortiOS-REST-Abruf (rein lesend) inkl. Adress-/Service-Auflösung,
NetBox-Adapter (lesend), Ingest/Matching in den Rule & Service Store, Findings für
nicht auflösbare Objekte, interne API verdrahtet. Offen ist nur noch der Lauf gegen
das **echte Lab** und ein Minimal-UI/CLI-Auslöser.

- [x] FortiGate-Collector: echter FortiOS-REST-Abruf (read-only) über injizierbaren
  httpx-Client; Adressobjekt-Namen → Werte aufgelöst (ipmask→CIDR/Host-IP, iprange,
  Gruppen rekursiv), FQDN/`H-EXT_` bleiben Name; Services → kanonische Tokens
  (MockTransport-getestet, `test_fortigate_client.py`); ohne Lab-Creds Beispieldaten
- [x] Persistenz-/Matching-Pfad: Collector-Ergebnis → interne API → Rule & Service Store
  → Anzeige über `/rules` (fixture-getestet, `test_internal_ingest.py`)
- [x] Adress-Matching via **NetBox-Adapter**; nicht auflösbares Objekt wird als Finding
  ausgewiesen; `H-EXT_` als `expected_external` ohne Fehler (fixture-getestet)
- [x] Job-Rückmeldung + Audit-Log-Eintrag (wer, wann, was, Ergebnis)
- [x] CLI-Auslöser (`automato` im Orchestrator): Mandant/Quellsystem idempotent
  anlegen + Collect-Lauf starten/abfragen — gleicher Pfad wie `POST /jobs/collect`
  (fixture-getestet, `test_cli.py`)
- [x] Scriptbares Lab-Setup einer VM (`lab/`): Prereqs, NetBox (Port 8080) + Automato-Stack
  verdrahtet, Compose reicht NetBox-/FortiGate-Creds durch (`lab/README.md`)
- [x] Lauf gegen das echte Lab: 75 echte FortiGate-Policies read-only via FortiOS-REST
  gelesen und normalisiert (`status=completed rules=75`), End-to-End auf einer VM
  (`lab/bootstrap.sh`). Offen für echte Treffer: NetBox mit echten IPAM-Daten füllen
  (Lab-Seed enthält nur Spielzeugdaten) — nicht Teil des Durchstich-Ziels.

**Exit-Kriterium (erfüllt):** Ein Auftrag liest nachvollziehbar Regeln aus dem Lab,
speichert sie normalisiert und zeigt sie an (`/rules`) – inklusive Audit-Trail.

## M8 – Review-Checkpoint nach dem Durchstich

Expliziter Halt, **bevor** der volle Use Case gebaut wird – hier werden Änderungen erkannt,
solange sie noch billig sind.

- [x] Lessons Learned: Erkenntnisse aus dem echten Lab festgehalten (ADR 0006) –
  NetBox-v2-Token/-Versionspin, typ-bewusste Objekt-Einordnung statt rein
  namensbasiert, Bootstrap-Import als SoT-Seeding, VIPs über extip, Findings als
  abgeleiteter Zustand.
- [x] Architektur/Datenmodell nachgezogen: ADR 0006 ergänzt (verweist auf 0004/0005);
  Collector liefert Objekttyp mit, `MatchStatus.NOT_APPLICABLE` ergänzt.
- [x] Risikoregister aktualisiert (R4/R5 angepasst; R15–R17 ergänzt).

**Exit-Kriterium (erfüllt):** Entscheidungen bestätigt/angepasst (ADR 0006) – M9 kann starten.

**Ergebnis des Durchstichs (echtes Lab):** 75 FortiGate-Policies gelesen, nach
NetBox-Seeding **0 nicht aufgelöste Endpunkte, 0 offene Findings**; Web-UI mit
Filtern (System/Zone/Aktion/Freitext), Regel-Detail und Findings-Ansicht steht.

## M9 – Ersten Use Case abschliessen

- [x] Use Case #1 (Model 2) gemäss DoD umgesetzt: FortiGate read-only einlesen,
  Matching gegen NetBox (unmatched 0 nach Import), kanonische Services, Web-UI mit
  Filtern (System/Netzwerk/Zone), **Applikations-Doku/Export**, VIP-NAT-Mapping,
  Zonen aus Interfaces. DoD-Abgleich: `docs/usecases/uc1-firewall-regel-dokumentation.md`.
- [x] Fehlerbehandlung: Collector-Fehler → `failed`-Result mit Meldung; unvollständige/
  nicht zuordenbare Objekte als Findings sichtbar; SoT-Fehler werden abgefangen.
- [x] Security-/Hardening-Check durchgeführt (DoD-Abgleich + 8-Prinzipien-Tabelle im
  UC1-Dokument). Sofort-Härtung: Read-API fail-closed ausserhalb `dev`.
- [x] Dokumentation erstellt: Betrieb + Anwender (`docs/BETRIEB.md`).
- [x] **Produktions-Gate:** AuthN/RBAC vor der Read-API (Keycloak, ADR 0007),
  Finding-Triage-Schreibpfad, mTLS-Agent-Gateway, signierte Agent-Updates, Sicherung mit
  Verschlüsselung at rest, Update mit Rückfall, R16-Prüfung (ADR 0013, 0.5.0).
  Netzsegmentierung bleibt Betriebsaufgabe je Kundenumgebung.

**Exit-Kriterium:** UC1 Model 2 ist **funktional vollständig und Lab-/Review-reif**
(DoD erfüllt, Security-Check für den Scope bestanden). Die **produktive Freigabe**
ist an das AuthN/RBAC-Gate gebunden (fail-closed erzwungen) – seit 0.5.0 erfüllt; für den
Produktivbetrieb gelten die Vorgaben aus ADR 0013.

## Ausblick nach M9

Weitere Use Cases durchlaufen denselben Zyklus in verkürzter Form (M1 → M5 → M7 → M9),
da Struktur, Architektur und Grundaufbau dann stehen. Geplante Ausbaustufen:

- **Weitere Use Cases**: Compliance/Drift-Bewertung (Soll/Ist);
  **Firewall-Regel-Automatisierung** (ADR 0002: Rule-Intents, Aerleon,
  Primary/Backup); **VM-Provisionierung aus Templates** auf VMware/Proxmox
  (Linux & Windows, inkl. cloud-init/sysprep und IPAM-Integration –
  nächste freie IP via SoT Gateway)
- **Worker-Ausbau (Schreibpfad)**: weitere Plattformen (Fortigate, Cisco ACI, VMware,
  Proxmox, Windows, Linux, Cisco Switches); Runner-Abstraktion pro Worker
  (Ansible und/oder Terraform/OpenTofu je Use Case – State-Backend-Entscheid als
  ADR **vor** dem ersten schreibenden Use Case); Job-Modell um
  Plan → Approve → Apply und Rollback erweitern; Ansible-Rollen konsolidieren
- **Web UI & Wizards**: Grundgerüst (Auth gegen Orchestrator), Contract-Wizard,
  Neue-VM-Wizard, weitere nach Bedarf
- **Integrationen**: ServiceNow-Anbindung, weitere Schnittstellen (Monitoring, CMDB, Ticketing)
- **Skalierung**: multiple Worker-Instanzen pro Typ, Lastverteilung, Umzug auf Kubernetes,
  Hochverfügbarkeit für den Orchestrator
- **Hardening & Full Stack**: Security-Audit, HashiCorp Vault statt Ansible Vault,
  vollständiges RBAC, Monitoring/Alerting (z. B. Prometheus/Grafana), vollständige
  CI/CD-Pipeline, Disaster Recovery

## Ausbaustufe B – Plattform-Ausbau & Produktions-Härtung

Nach dem UC1-Durchstich (M0–M9) wird Automato von der read-only-Dokumentation zur
**allgemeinen Workflow-Plattform** ausgebaut. Leitplanken: **Open Source bevorzugt**,
**Vereinfachung**, und die Vision reicht über Firewalls/ACI hinaus (VMs ausrollen,
Applikationen installieren, automatische Doku). Starke **Synergie mit Projekt
Magister** (`Vita-Brevis-GmbH/magister`, nahezu identischer Stack): gemeinsame
Bausteine für Auth, Cockpit und Deploy, mit dem Ziel, die Module später zu vereinen.

Diese Stufe läuft weiter im Meilenstein-Rhythmus (jedes Epic liefert ein
überprüfbares Ergebnis, Risikoregister wird fortgeschrieben). Reihenfolge
dependency-getrieben; die **Web-UI (W1)** ist **vorgezogen**, das Auth-Fundament
**(E1)** hebt das offene Produktions-Gate aus ADR 0005.

| Epic | Inhalt | Status |
|---|---|---|
| **W1 — Web-UI-Ausbau** | Übersicht/Dashboard, bessere Filter, Regel-Detail, Mandanten-Switcher; zunächst gegen die bestehende (fail-closed) API. Mockup liegt vor. | **umgesetzt** (alle Ansichten nach Mockup) |
| **E1 — Härtung / RBAC** | AuthN/RBAC via **Keycloak** (lokal + SAML + OTP, geseedter globaler Admin), Mandanten-Scoping erzwingen, Rollen `viewer/operator/admin`, Audit-Erweiterung, Secrets-/Vault-Provider. Details: **ADR 0007**. Produktions-Gate ergänzt durch **ADR 0013**. | **umgesetzt** (Keycloak Prod live) |
| **E2 — Gemeinsame Basis (Magister-Synergie)** | Keycloak-Container, **Cockpit** (Multi-Instanz: Health, Version, Update-Trigger, `stable`/`latest` + Rollback-Snapshot), Deploy-/Caddy-Security-Skelett, Audit-/Scope-Muster. Magister-Migration als eigener Magister-Meilenstein. | teilweise: System-Seite, `deploy/update.sh` mit Rückfall, Sicherung; Cockpit als **ADR 0014 (Entwurf)** |
| **E3 — Import-Wizard** | Firewall-Import geführt per Wizard (statt CLI-Bootstrap). | geplant |
| **E4 — Schreibpfad-Fundament** | Worker + **Runner-Abstraktion** (Ansible primär, Terraform/OpenTofu je Use Case) für read/write/delete; State-Backend-Entscheid als ADR **vor** dem ersten schreibenden Use Case (ADR 0002). Plan → Approve → Apply → Rollback. | später |
| **E5 — Workflows** | Kleine, **verkettbare** Workflows (Bausteine zu Abläufen kombinieren) — Kern der allgemeinen Orchestrierung. | später |
| **E6 — Mikroprogramme / modulare Funktionen** | Kleine, wiederverwendbare Funktionsmodule (vgl. Magister ADR 0008 „modulare Funktionen"). | später |
| **E7 — Design-/Topologie-Modul** | Verbindungen über **redundante Pfade** und **Firewalls in Serie** zusammenführen; optional **draw.io-Integration** für Netzwerkpläne (pro Mandant versioniert, Knoten mit Zonen/VIPs verknüpfbar). | später / optional |

### Phase A – Daten sauber anzeigen und abgleichen (ADR 0010) – umgesetzt

Nur lesen – einziger Schreibweg ist der NetBox-Sync (Vorschau → Klick → Neu-Lesen).
Alle Schritte A1–A6 sind als Code umgesetzt und mit Fixtures/Fake-NetBox getestet; offen ist
die Abnahme gegen die echte DDI (Agent) und die Dev-NetBox (Checkliste
`docs/DEV-SETUP-AGENT.md` Abschnitt 6b).

| Schritt | Inhalt |
|---|---|
| **A1** Collector | FortiGate: Objekte/Gruppen/Services mit Namen, Regeln per Name, Interfaces/VLAN-Interfaces/Zonen. APIC: VRF, BD-/EPG-Subnetze, EPG-VLANs, statische Pfade/Ports, Access Policies, LLDP/CDP-Nachbarn, Fabric-Knoten |
| **A2** Store | Inventar-Ebene (`device_object`, `rule_object_ref`, `inv_*`), Findings als abgeleiteter Zustand je Mandant |
| **A3** Referenz | Infoblox DDI (komplett, über den Agent) und NetBox als Quellsysteme mit Rolle führend/Anreicherung, gespeicherter Stand, Nur-Lese-Schutz im Code |
| **A4** Abgleich | Firewall-Objekt ↔ IPAM, DDI ↔ NetBox, ACI-Subnetz/-VLAN ↔ IPAM |
| **A5** UI | Regeln mit Namen und Referenz, Ansichten „Objekte" und „Abgleich", IPAM-Quellsysteme |
| **A6** NetBox-Sync | DDI-Spiegel + Netzinventar nach NetBox, nur eigene (getaggte) Objekte, Vorschau + Übernehmen |

### Plan 0.5.0 – Aufräumen, Applikationen, Betrieb, Produktions-Gate – umgesetzt

| Punkt | Inhalt | Stand |
|---|---|---|
| **1 Aufräumen** | NAT/Findings seitenweise mit Gesamtzahl und Zählern; CI-Job gegen Postgres (Migrationen hoch/runter, alle Orchestrator-Tests); ROADMAP/CLAUDE.md aktualisiert | umgesetzt |
| **2 Applikationen** | eigenes Objekt (AP/EPG/Server), Vorschläge per Projektnummer, Doku je Applikation mit Export/Druck; EPG-Lage mit Leaf-Port/VLAN; VIPs als „publizierte Dienste“ | umgesetzt (ADR 0012) |
| **3 Betrieb** | Zeitplan je Quellsystem (Cron, mehrprozess-sicher); ACI: ESGs, Taboo-Contracts, Preferred Groups; Abnahme-Checkliste Phase A | umgesetzt; Phase A gegen die echten Systeme abgenommen (2026-10-21) |
| **4 Produktions-Gate** | mTLS-Agent-Gateway (`require`/`bind`), signierte Agent-Updates, Sicherung/Wiederherstellung mit age, `deploy/update.sh` mit Rückfall, R16-Prüfung, System-Seite; Cockpit mit Magister als Entwurf | umgesetzt (ADR 0013, 0014 Entwurf) |

### 0.6.0–0.14.x – Dev-Befunde aus dem Betrieb gegen die echten Systeme – umgesetzt

| Version | Inhalt |
|---|---|
| **0.6.0** | Regelsuche über alles (Gruppenmitglieder, IP im Netz, IPAM-DNS-Namen, NAT); NAT/VIPs detailliert mit DNS, Server, Regeln; Verlinkung Objekte/NAT ↔ Regeln; Findings mit Übersicht und Satz je Befund; IPAM-Namen ohne Präfix; „Alle einlesen“; Agent im Wach-Modus (20 s) |
| **0.7.0** | Standorte virtuell (000, 001) und physisch (Ort-Code) mit n:m-Zuordnung, Vorschlag aus Namen, NetBox-Site-Gruppen (ADR 0015) |
| **0.8.0** | Fix NetBox-Sync (eindeutige VLAN-/Site-Namen); Regeln ohne Quelle/Ziel (ISDB, Identität, ZTNA, Contract Master, Intra-EPG, „nicht wirksam“); Dienste mit Attributen, ACI-Filter als Dienste, Prüfung nach FT_-Schema; Objekt-Detail mit Regeln |
| **0.8.1** | Tests/CI ausgebaut (`docs/TESTS.md`): UI-Tests mit Playwright, `alembic check`, Mindestabdeckung, PowerShell-Syntax, Image-Builds, nächtlicher Lauf; Fix `agent.created_at` (Migration 0029) und mehrere Server je NAT-IP |
| **0.9.0** | IPv4/IPv6 gleichnamig getrennt (auch `policy6`), Familie je Regel, IPv4/IPv6-Paare markiert; Regel-Zahl bei Objekten über Gruppen; abgeleiteter Regelindex – Listen/Suche/Detail/Doku < 0,3 s, Einlesen 216 → 25 s bei 3000 Regeln (ADR 0016) |
| **0.10.0** | FortiGate Virtual Server (Real Server, Lastverteilung, Health-Checks, IPv6-VIPs); Doku je System/Applikation mit allen Regeln (Netze, Gruppen, any, vzAny, ESG, EPG des Servers, Spalte „Bezug“); Regeln je Subnetz |
| **0.11.0** | VMware vCenter lesen (nur lesende vSphere-Methoden): VMs mit NICs, IPs, Cluster, Host; Portgruppe → ACI-EPG über den Namen `Tenant\|AP\|EPG` (auch ohne Präfixe) bzw. das VLAN; VMs in Regeln, Suche, Objekten, EPGs und Doku; Befunde VM-IP fehlt im IPAM, Portgruppe ohne EPG; NetBox-Sync-Abschnitt Virtualisierung (ADR 0017) |
| **0.11.1–0.11.4** | Korrekturen aus dem Betrieb: vCenter-Berechtigung verständlich, Commit vor der Antwort (Teil-Upload), FortiGate ohne JSON, hängende Läufe und NetBox-Syncs beenden/abbrechen, Agent-Update im Wach-Modus mit Log und Update-Status, VLAN-Subinterfaces getaggt |
| **0.12.0** | IPv4/IPv6-Paare bestätigen/ablehnen; VIP-Ziele als Objekte; Ports je Gerät; Betriebszustand als Ampel (System-Seite, Übersicht) |
| **0.13.0** | Ansicht „Läufe“ (alle, seitenweise, Filter); Übersicht zeigt alle offenen Läufe; Ports kompakt mit Detail; Portgruppen im Detail (EPG, Subnetze, VMs) |
| **0.14.0–0.14.1** | Applikationen: Doku im Mittelpunkt (Übersicht, alle VMs, EPGs mit ganzem ACI-Pfad, Regeln mit Richtung/Bezug spezifisch–unspezifisch, Filter, Erklärung, Links), externer/interner Name, Zweck Dev/Test/Prod; NAT/VIPs mit Prüfung; VM-Angaben in den Detailansichten; Läufe unter Administration; Regeln mit Objekt (Host/Netz/any) und VMs darin, Firewall-Objekte je Name, Listen mit Scrollbalken im Bild |
| **0.15.0** | VDOM-Kontext: alle VDOMs lesen, VDOM an Regeln, Objekten, NAT, Interfaces, Subnetzen (ADR 0019 Stufe 1) |
| **0.16.0–0.16.1** | Routing je VDOM (RIB mit ECMP, BGP-Pfade inkl. Backup, Nachbarn, statische/Policy-Routen, SD-WAN), HA/FGSP, ACI-L3Outs und Border-Leaf-Routen (Stufe 2); Agent aktualisiert sich vor dem Lauf, robustes Selbst-Update |
| **0.17.0–0.17.1** | Weg Hop für Hop mit Quelle bzw. Startpunkt (Stufe 3, erste Fassung); FortiGate nur mit root = Firewall (auch in Regeln, Objekten, NAT); Nachbarn und Next-Hops mit Namen aus dem IPAM; robuste Läufe (Wiederholung, Upload-Quittung), VRF/Management getrennt, kein Raten über Transfernetze |
| **0.18.0** | Parallele Firewalls (Verbund, ADR 0020): gleiche Policies einmal, Abweichungen als Findings; Anbindungen nach aussen (Internet/MPLS/WAN) von Hand mit manuellen Routen (ADR 0019 Stufe 4, erster Teil) |
| **0.19.0** | Verbund ablehnen bzw. auflösen (nicht mehr vorschlagen); redundante Wege: ECMP, Verbund, Backup-Wege (BGP, schwebende statische Routen) gleicher bzw. fast gleicher Länge mit Übersicht |
| **0.20.0** | Erkannte Anbindungen (Router aus Routen, BGP-Pfaden und BGP-Nachbarn, aktiv/Backup, zusammengefasst je Firewall bzw. Verbund/Session-Sync-Partner) mit allen Routern übernehmen; Anbindung für mehrere Firewalls; Länge der Wege „gleich lang“ / „+n Hops“ |
| **0.21.0** | Erkannte Anbindungen ausblenden bzw. wieder anzeigen; Vorschläge zu unterst, Sprung zum Formular |
| **0.22.0** | Regeln auf dem Weg: je FortiGate-Hop die Policies (Ein-/Ausgang, Quelle, Ziel, Dienst), erste sichere entscheidet – erlaubt, gesperrt, abhängig; Urteil je Weg |
| **0.23.0** | NAT auf dem Weg: VIP/DNAT zur internen IP (Port-Weiterleitung, Virtual Server), SNAT als Quelle für die Firewalls danach |
| **0.24.0** | Regeln nur, wo der Verkehr durchkommt: Relevanz je Regel und System nach dem Routing der Firewall (Ein-/Ausgang), routingtechnisch unmögliche aus Doku je System, Applikations-Doku, Kennzahlen und Export (einblendbar, mit Begründung) |
| **0.25.0** | Gegenstelle `any` in der Doku als das, was hinter dem Interface liegt (Anbindung, Default-Route, Netze); Internet-Anbindung = Kategorie 1/2 |
| **0.26.0** | Regel-Liste: Suche nach einer IP mit Relevanz nach dem Routing, routingtechnisch unmögliche ausblendbar |
| **0.27.0** | Inter-VDOM-Links als Kanten im Weg (vdom-link, VLANs auf NPU-vlinks) |
| **0.28.0** | ACI-Contracts über externe EPGs nach der Zuordnung im ACI (Scope, längstes Subnetz je VRF, Fabric, 0.0.0.0/0 nach Routing) in Doku und Regel-Liste |
| **0.29.0** | Verbund bzw. Session-Sync-Paar als ein Hop im Weg (je Firewall Eingang, Ausgang, Regeln; verschiedene Urteile = abhängig) |
| **0.30.0** | Kunden als Ausschnitt der Umgebung (ADR 0018 Schritt 1): Selektoren ein-/ausschliessend, abgeleitete Zugehörigkeit, Vorschau, Vorschläge je ACI-Tenant, nicht Zugeordnetes |
| **0.31.0** | Kunden: Merkmale aus dem IPAM als Selektor, NAT und Findings als Mitglieder |
| **0.32.0** | Kunden-Filter: Umschalter „Kunde“, alle Doku-Listen auf den Ausschnitt samt geteiltem Teil (ADR 0018 Schritt 2) |
| **0.33.0** | Kunden-Benutzer: Keycloak-Rolle `customer`/`customer_ids`, Freigabeliste fail-closed, serverseitige Einschränkung je Endpunkt, Audit immer an (ADR 0018 Schritt 3) |
| **0.34.0** | Befunde aus dem Test von 0.33.0: Verbindung prüfen (NetBox), fixierter Seitenkopf, Management-Adresse der FortiGate, Portgruppe „Name passt nicht“, VRF und Filter im Routing, Kategorien der Applikations-Doku mit Aktion, Gerät, Link und IPv4/IPv6-Paaren, NAT nur aus der Regel, unmögliche Regeln nicht in der Applikations-Doku, Links in der Kunden-Übersicht |
| **0.35.0** | Weg-Suche als eigene Ansicht: Quelle und Ziel als Name bzw. FQDN (IPAM, Firewall-Objekte, VMs), Dienst als Service-Objekt bzw. ACI-Filter, ACI-Contracts je VRF-Hop (EPG-Zuordnung, Taboo, vzAny, ESG, Preferred Group); Dienste mit Objektnamen (`FT_https`) statt Werten |
| **0.36.0** | IPv4/IPv6-Paare nebeneinander, Typ-Filter (dynamische SDN-Objekte), Standardobjekte des Herstellers zuunterst bzw. ausgeblendet mit Befund bei Verwendung, „Objekt öffnen“ filtert die Liste, `FT_` als pflegbares Service-/Filter-Präfix, Projektnummern aus der Namenskonvention, breiter Kunden-Editor |
| **0.37.0** | Applikations-Doku: unspezifische Regeln (any, grosse Netze; Internet/MPLS) nur als Regel, die Systeme der Applikation auf Knopfdruck |
| **0.38.0** | Weg-Suche mit Netzen (IPAM, Netz-Objekte, Subnetze des Inventars, Netz direkt; geprüft mit einer Adresse daraus), Trefferliste nach Hosts/Netzen mit Anzahl und „Weitere laden“; IPv4/IPv6-Paare im Rahmen mit Marke; Vorschläge unter der Applikationsliste; Findings der Applikations-Doku nach Tenant/AP/EPG |
| **0.39.0** | Weg-Suche: ein Netz als Ganzes – Route, die das ganze Netz enthält (anders geroutete Teile als Hinweis), Policies und Contracts sicher nur, wenn sie das ganze Netz erfassen (sonst „nur teilweise“) |
| **0.40.0** | Verbindungen prüfen: System → Verbindungen (Datenbank, Redis, Keycloak, zentrale Quellsysteme mit ufw-Regel), `deploy/aufbau.sh pruefen/firewall/status`, Prüfung nach jedem Update |
| **0.41.0** | Neustart-Regeln für Automato und das NetBox-Setup, `deploy/aufbau.sh` prüft Docker beim Booten, Neustart-Regeln und die lokale NetBox, `netbox-start` |
| **0.42.0** | Sicherung einmal einrichten: Werte aus `.env`, `deploy/aufbau.sh backup-schluessel`, Prüfung der Sicherung in `pruefen` |
| **0.42.1** | Korrektur: Compose-Konfiguration vor dem Update bzw. in `pruefen` prüfen (fremder Dienst im Override) |
| **0.42.2** | Korrektur: `pruefen` meldet Container ausserhalb der Update-Profile, „starting“ und frühere ufw-Sperren nur als Hinweis |
| **0.42.3** | Korrektur: Applikations-Doku nur mit Contracts aus Tenant/VRF der Systeme (gleiche IPs, externe EPGs anderer VRFs), Filter „EPG“ je Contract |
| **0.43.0** | Docker und Images aktuell halten: Fremd- und Basis-Images bei jedem Update, `deploy/aufbau.sh docker-update` bzw. `AUTOMATO_DOCKER_UPDATE=1`, Stand in `pruefen` |
| **0.43.1** | Korrektur Applikations-Doku: Scope der Contracts (auch aus `common`), Regeln anderer EPGs im gemeinsamen BD, Portgruppe je NIC über Name/VLAN/Switch, Vorgabe vor gelerntem Endpunkt, Hinweis Projektnummer |
| **0.43.2** | Korrektur Applikations-Doku: Contracts nur mit den Beteiligten (eigene EPGs, Gegenstellen im Scope), Firewall mit VDOM |
| **0.44.0** | ACI zwischen VRFs/Tenants: Shared Services (Subnetz-Scope `shared`) und Route-Leaking (`leakRoutes`), VRF aus `tDn` (auch vzAny), Weg-Suche endet im Fabric der anderen VRF, Applikations-Doku urteilt gleich |
| **0.44.1** | Korrektur: Listen mit Filtern zeigen nur die Antwort zur letzten Anfrage (ältere überschrieb die gefilterte) |
| **0.45.0** | Verbindungen Ende zu Ende in der Applikations-Doku: eine Zeile je Verbindung mit dem Weg über ACI und Firewalls, je Hop Policy bzw. Contract mit Objekten, Abgleich Regel ↔ Weg mit Abweichungen, Sprung in die Weg-Suche (ADR 0021 Entwurf) |
| **0.45.1** | Korrektur: Scope der Contracts im ganzen Modell (AP, VRF, Tenant über VRFs, global; vzAny mit Scope AP), Regel-Detail mit Paaren, Regel-Liste nach IP, Befund „ohne Gegenstelle im Scope“ |
| **0.46.0** | Konfiguration vor Gelerntem überall: statischer Endpunkt → Portgruppe der VM → gelernt → Subnetz (Weg-Suche, Regel-Liste), statische Endpunkte und Herkunft gelesen, Befund „EPG: gelernt ≠ konfiguriert“ |
| **0.47.0** | FortiGate SNAT genauer: Pool-Typen (Bereich bzw. feste IP bei fixed-port-range), IPv6-Pools, `nat-source-vip`, Interface-IP je Familie, Befund „SNAT-Pool fehlt“ |
| **0.48.0** | Betrieb: Fristen für alte Läufe, Sync-Vorschauen und Audit (täglich, System → Aufbewahrung), getrennte DB-Rollen (Dienst `migrate` als Eigentümer, Orchestrator ohne DDL, `deploy/aufbau.sh db-rollen`), ADR 0022 |
| **0.49.0** | Betrieb: TLS intern mit eigener CA (Postgres/Redis nur TLS, Orchestrator und Web-UI per HTTPS, Gegenstellen gegen die CA geprüft), `deploy/aufbau.sh tls-intern`, CI mit echten Containern, ADR 0023 |
| **0.50.0** | Topologie-Modell (`GET /topology`, Routing → Topologie): Knoten (Firewall/VDOM, ACI-VRF, Segment, Anbindung, Router) und Kanten mit Herkunft und Routen; Plausibilitäts-Befunde (doppelte IP, Maske, Schleife, statische Route ohne Wirkung, BGP nicht aufgebaut) |
| **0.51.0** | Logische Zonen in Automato (Selektoren wie beim Kunden plus Anbindung, längstes Präfix), Zonenmatrix mit Absicht je Zonenpaar und Befund „gegen die Absicht“, Zonenplan (Zonen an Firewalls/VDOMs und ACI-VRFs, Übergänge, Netze ohne Zone), Netzwerkdesign im Tool, ADR 0024 |
| **0.52.0** | Zonenplan exportieren (draw.io bearbeitbar, SVG, PDF mit Matrix und Tabellen; ein Layout für Oberfläche und Dateien, ohne neue Abhängigkeit) und Versionen des Plans (speichern, Fingerabdruck „geändert seit“, Vergleich, Export je Version, Löschen nur Admin) |
| **0.53.0** | Kunden auf Zonen (Netze und EPGs der Zonen eines Kunden in seiner Zugehörigkeit), Sicht der Kunden-Benutzer auf ihre Zonen, Matrix („anderer Kunde“) und Plan samt Export, Zuordnung Benutzer → Kunde in Automato (Administration → Kunden, `GET /auth/me`) |
| **0.54.0** | Zonen automatisch erkennen und vorschlagen (Netzwerkdesign → Vorschläge): Transfernetze, FortiGate-Zonen und -Interfaces (Name, Rolle, Management), IPAM-Merkmal Zone/Rolle, ACI-VRFs, Anbindungen; übernehmen (neu bzw. ergänzen), ausblenden; Netze ohne Zone und Vorschlag |
| **0.55.0** | Grundschema der Zonen in der Namenskonvention als draw.io-Zeichnung (Namensprofile → Grundschema der Zonen: Vorlage, hochladen mit Vorschau, Zeichnung zurück): Zonen mit Art, Kurzname, Farbe und Erkennungsmustern, Übergänge mit Absicht; Vorschläge nach dem Schema, fehlende Schema-Zonen anlegen, Absichten aus dem Schema in die Zonenmatrix übernehmen |
| **0.56.0** | Zonenmodell nach Ebenen (Korrekturen des Anwenders): Zonen mit Knoten (VDOM, ACI-Tenant/VRF), Netze zwischen Knoten verschiedener Zonen = Übergänge („DC-EINET“), Netze in der Zone nur ohne Übergang; Vorlage DC/Edge/Transfer/Core/Distribution/Access/Public mit festen Farben; Vorschläge nach dem Modell (ACI = DC je Kunde, Internet-Firewalls = Edge Internet, Knoten zuordnen); Zonenplan je Kunde (andere Kunden je als eine Zone) |
| **0.57.0** | Zonen je Hop in der Weg-Suche (Quell-/Zielzone, Zone je Hop, Übergänge, Absicht mit Urteil gegenüber dem Weg) und Absicht je Dienst (eingeschränkt = erlaubte Dienste, Verstoss in Matrix und Befund, Grundschema `automato_services`) |
| **0.57.1** | Korrektur Zonenplan je Kunde: nur die Firewalls/ACI-VRFs, die der Kunde braucht (an seinen Netzen, kürzeste Wege zu den Zonen seiner Regeln), Kopfzeilen-Filter „Kunde“ öffnet seine Ansicht |
| **0.58.0** | Kunden fertig: Zugehörigkeit von Regeln nach dem Weg (Relevanz bzw. Zuordnung im ACI, Vorschau „nicht auf dem Weg“) und Kunden-Doku ohne Gerätenamen (neutrale Bezeichnungen in jeder Antwort an Kunden-Benutzer und im Export) |
| **0.59.0** | Applikationen: Abweichung Regel ↔ Weg als Befund (je Regel und Applikation, nach jedem Lauf, Zeitbudget), Verbindungen Ende zu Ende in Export und Druck der Applikations-Doku |
| **0.60.0** | Applikationen: Regeln direkt zuordnen bzw. ausschliessen (stabiler Verweis über Läufe, auch any/unspezifische; in der Applikations-Doku und in „Regeln & Contracts“), Abhängigkeiten zwischen Applikationen (nutzt / wird genutzt von, mit Regeln) |
| **0.61.0** | Zonenplan übersichtlich: Suche und Fokus mit Nachbarn, Ebenen und Elemente ausblenden, Zonen ziehen (Anordnung je Ansicht gespeichert); Export und Versionen wie die Ansicht |
| **0.62.0** | Switching/Verkabelung erste Fassung: Cisco-Switches (IOS, IOS-XE, NX-OS) strikt lesend über SSH (Exec je Befehl, feste `show`-Befehle, Host-Schlüssel festgehalten), Ports/VLANs/Port-Channel/vPC und Nachbarn aus CDP/LLDP; Ansicht Verkabelung (Verbindungen beidseitig/einseitig/Gegenstelle ohne CDP/LLDP, Geräte, Ports, VLANs) aus Switches und ACI-Leafs; Befunde Speed/Duplex/VLAN/Port-Channel (ADR 0025) |
| **0.63.0** | Endpunkte aus MAC-Tabelle und ARP der Switches (gesammelt mit Frist): „Wo hängt …“ nach MAC/IP/Name, MACs je Port ohne Uplinks, Befund VLAN fehlt am ESXi-Uplink (ADR 0025 Nr. 8) |
| **0.64.0** | L2-Sicht: Plan der Verkabelung nach Ebenen, Ausfall eines Geräts/Kabels simulieren, VLAN-Wege mit Inseln, Befunde zu Redundanz (Einzelkabel, ein Switch, Ausfallpunkt, Port-Channel unvollständig) und getrennten VLANs, L2-Abschnitte und Anschluss von Quelle/Ziel in der Weg-Suche (ADR 0025 Nr. 9) |
| **0.65.0** | Verkabelung abgerundet: Spanning Tree lesen (blockierte VLANs im Plan als Reserve, L2-Weg meidet sie), vPC-Paar als logischer Switch (Rahmen im Plan, Befund vPC-Paar uneinheitlich), mehrere gleichzeitige Ausfälle (Shift-Klick), Export des Plans als SVG/draw.io (ADR 0025 Nr. 10) |
| **0.65.1** | Korrekturen nach dem Test: Weg-Suche Richtung tauschen, Linien des Zonenplans weich aus der zugewandten Seite, Zonen-Vorschläge ohne ACI-Gruppen aus dem IPAM (`SZ_…`), filtern und gesammelt ausblenden |
| **0.66.0** | Zonen: Kommunikation (erlaubende Regeln je Zonenpaar) im Zonenplan einblendbar – Nachbar-Tenants eines Tenants, Partner im Plan eines Kunden einzeln; Vorschläge beim Erfassen einer Zone (FortiGate-Zonen, Interfaces, IPAM, ACI, Anbindungen nach dem Namen) (ADR 0024 Nr. 15) |
| **0.67.0** | Netzwerkplan je Applikation: Systeme → Firewalls/ACI auf den Wegen → Gegenstellen, je Linie Dienste je Richtung und Interfaces an den Enden, Urteil als Farbe, Hervorheben per Suche/Klick, Export draw.io/SVG (ADR 0021 Ergänzung 0.67.0) |
| **0.68.0** | Weitere Switches/Router: Catalyst 1200/1300 (Business-CLI über begrenzte Sitzung), Juniper MX/EX/QFX (Junos, JSON, Interface-Netze), Catalyst 9200L/3650/4500 und Nexus 9000 mit Tests; Router in der Verkabelung (ADR 0025 Nr. 11) |
| **0.69.0** | Applikations-Doku: Regeln je Kategorie ausblenden mit Pflicht-Begründung, Liste „ausgeblendet“ mit wer/wann, Export-Abschnitt, Audit (ADR 0012 Ergänzung 0.69.0) |
| **0.69.1** | Korrekturen: ACI-Contracts zustandslos (Filter ohne Ports in beide Richtungen, mit Zielport nur Antworten) in Weg-Suche, Regel-Detail, Applikations-Doku, Verbindungen und Zonenmatrix; VLANs aus dem ACI (`vlanCktEp`, `pcAggrIf`) im Reiter VLANs (ADR 0019) |
| **0.70.0** | Patchungen im Verkabelungsplan (Gerät bzw. Kabel wählen, Ports beider Seiten mit Konfiguration, Abweichungen, Endpunkten); Grundschema der Zonen in der Oberfläche bearbeiten (ADR 0025 Nr. 12, ADR 0024 Nr. 16) |
| **0.71.0** | Export für Confluence: Applikations-Doku und Zonenplan als Word (.docx) plus draw.io-Plan in einem ZIP, Import von Hand, kein Schreibweg (ADR 0026) |
| **0.72.0** | Parallele Firewalls: Policy-Routen/SD-WAN, Routing je VDOM und NAT im Verbund vergleichen, Befunde und Übersicht „Parallele Firewalls“ im Routing (ADR 0020 Ergänzung) |
| **0.72.1** | Korrekturen: ACI-Filter nur mit Quellport = nur Antworten (gesperrt statt „möglich“), Doku je System fasst die Rückrichtung wie die Applikations-Doku zusammen, Leistung der Zonenmatrix gemessen |
| **0.73.0** | Applikations-Doku übersichtlich: Bereiche zuklappen, Systeme kompakt mit Detail, Export und Druck mit wählbaren Abschnitten, Abhängigkeiten vorerst entfernt, Befund nur bei uneinheitlichen Wegen (ADR 0012/0021 Ergänzung 0.73.0) |
| **0.74.0** | Netzwerkplan der Applikation kompakt: Rahmen je AP mit EPGs und ihren VMs, ACI als L3Out, Regeln mit Diensten als ausblendbare Ebene (auch im draw.io), Zoom und Verschieben (ADR 0021 Ergänzung 0.74.0) |
| **0.74.1** | Korrekturen: Speichern einer Applikation ohne Warten (Befunde im Hintergrund), Routing als Index im Speicher, Vorschläge je ACI-Tenant, Typ-Filter der Objekte beim Reiterwechsel, Network Container aus Infoblox |
| **0.75.0** | Stammdaten der Applikationen aus CSV (Vorschau, Ausnahme vom „kein CSV-Import“), Applikationen ohne VMs ausblenden bzw. mehrere löschen, Routing-Kontexte zugeklappt, Erklärung je Seite einzeilig (ADR 0012 Ergänzung 0.75.0) |
| **0.76.0** | Kunde oben wählen filtert alle Ansichten (Routing, Topologie, Weg-Suche, Inventar, Verkabelung, Abgleich, Zonen, parallele Firewalls, publizierte Dienste), Geteiltes markiert (ADR 0018 Ergänzung 0.76.0) |
| **0.76.1** | Korrektur: externe Netze und Hosts (öffentlich, ausserhalb der Interface-/BD-Netze der gelesenen Geräte) ohne Befund „nicht im IPAM“; im WAN-Interface-Netz bleibt der Befund (ADR 0010 Ergänzung 0.76.1) |
| **0.77.0** | VMware NSX Advanced Load Balancer (Avi) lesend: Virtual Services, VIPs, Pools als publizierte Dienste, NAT-Einstellungen, Weg-Suche über den Load Balancer bis zum Server (ADR 0027) |
| **0.78.0** | Zonen komplett, Teil 1: Zonen und Absicht je Verbindung der Applikations-Doku, Absicht je Richtung im Zonenplan (Pfeile, Linie ohne Übergang, Export), Befund „Zone ausserhalb des Schemas“ (ADR 0024 Nr. 17) |
| **0.78.1** | Korrektur: Agent bekam während des Einspielens eines Laufs keine Antwort (ReadTimeout) – Agent-Zeile nicht mehr gesperrt |
| **0.78.2** | Korrekturen Applikations-Doku: Contracts Richtung L3Out nur, wenn sie weniger öffnen als die Firewall-Regeln (Befund), Kategorie 10 nur Zugriffe auf die Applikation (ADR 0012 Ergänzung 0.78.2) |
| **0.78.3** | Korrektur NSX ALB: HTTP 400 mit Begründung des Controllers, Rückfall ohne `X-Avi-Version`, Prüfung mit dem gewählten Avi-Tenant |
| **0.78.4** | Korrektur Weg-Suche: passende Policies hinter der entscheidenden (z. B. hinter „Default Block …“ zuoberst) und Objekt, über das Quelle/Ziel passen |
| **0.78.5** | Korrekturen nach dem Test des NSX ALB: Spalte „Einträge“ in den Läufen, Zusammenfassung des Avi-Laufs, „Letzte Läufe“ ohne Stau (kein HTTP 500 beim Einspielen) |
| **0.79.0** | VIP der FortiGate vor dem NSX ALB: Regeln, Applikations-Doku, publizierte Dienste und Weg-Suche verketten öffentliche IP → VIP → Virtual Service → Server (ADR 0027 Ergänzung) |
| **0.79.1** | Korrekturen: Verbindungen zu einem Netz in die genauer gerouteten Teile aufgeteilt (nicht alles über 0/0); Contract mit EPG als Consumer und Provider als Befund und richtig in der Applikations-Doku |
| **0.80.0** | Verbindungen Ende zu Ende filtern nach Urteil (erlaubt, gesperrt, abhängig, offen), Regeln (erlaubend/sperrend), Regel auf dem Weg, Richtung und Hinweisen |
| **0.80.1** | Korrektur Läufe: Ergebnisse nacheinander einspielen, Sperre des Mandanten, Wiederholung bei Deadlock, Liste der Läufe ohne HTTP 500 |
| **0.81.0** | Zonen komplett Teil 2: Zonenmatrix nach dem Weg („nicht möglich“ statt Verstoss), Übergang zu Zonen nur hinter einem Router (ADR 0024 Nr. 18) |
| **0.82.0** | Zonen komplett Teil 3: Zonen je Kunde im Grundschema (Vorlage „je Kunde“, ADR 0024 Nr. 19) |
| **0.83.0** | Zonen komplett Teil 4: Zonenplan aus draw.io zurück einlesen (Lage, Namen, Absichten, neue Formen; ADR 0024 Nr. 20) – Schritt 5 abgeschlossen |
| **0.83.1** | Korrektur ACI: VM über die Portgruppe im Fabric (keine Contracts der externen EPG 0.0.0.0/0), VRF der BDs aus `common`, Contracts externer EPGs nach dem L3Out |
| **0.84.0** | Applikations-Doku: Kategorien-Tabellen neu – Source/Destination in Verkehrsrichtung mit Name und IP, Kategorie 2 mit VIP (Firewall), Load Balancer und Server, NAT-Spalte, leere Spalten ausgeblendet |
| **0.85.0** | Netzwerkplan der Applikation: Load Balancer als Knoten in der EPG seiner VIP, verbunden mit den EPGs der Server; VIP der Firewall am Knoten (ADR 0021 Ergänzung 0.85.0) |
| **0.86.0** | Oberfläche auf Deutsch und Englisch umschaltbar (Benutzermenü, je Browser, alle Rollen), Glossar; Texte vom Server und Exporte folgen (ADR 0028 Schritt 1) |
| **0.87.0** | Texte vom Server und Exporte (Word/PDF/SVG/draw.io) auf Deutsch und Englisch, Sprache der Datei wählbar; Gespeichertes bleibt deutsch (ADR 0028 Schritte 2/3) |
| **0.88.0** | Abhängigkeiten zwischen Applikationen wieder in der Doku: Infrastruktur (Monitoring, Backup, DNS …) kennzeichnen und getrennt zeigen, Bereich lädt erst beim Aufklappen, Export-Abschnitt (ADR 0012 Ergänzung 0.88.0) |
| **0.89.0** | Feinere Rollen der Kunden-Benutzer: Lesen, Triage, Editor (eigene Applikationen pflegen), Kunden-Admin (eigene Kunden-Benutzer zuordnen), Rolle je Kunde, Claim `customer_role` (ADR 0018 Ergänzung) |
| **0.90.0** | Läufe, Agent-Konsole und CLI auf Deutsch und Englisch: Laufmeldungen deutsch gespeichert und beim Abruf übersetzt, `AUTOMATO_LANG`, `automato --lang` (ADR 0028 Schritt 4) |
| **0.91.0** | Datenqualität (Schritt 6, erste Fassung): Reiter mit Ampel je Bereich, Ursache und Link, Gesamtnote, Befunde „Stand veraltet“ und „nur teilweise gelesen“, Relevanz-Ursachen als Stichprobe (ADR 0029) |
| **0.92.0** | Verlauf des Routings: Änderungen an Routen, BGP-Nachbarn, HA, Policy-Routen, Kontexten und L3Outs von Lauf zu Lauf, Reiter „Verlauf“, Frist 180 Tage (ADR 0030) |
| **0.93.0** | NetBox-Sync: Abschnitte Verkabelung (Ports, beidseitige Kabel, Modus/VLANs nur mit vorhandenen VLANs) und Applikationen (Tags an VMs und IPs), standardmässig aus (ADR 0031) |
| **0.94.0** | Switches: IPv6-Nachbarn (NDP) in Endpunkten und „Wo hängt …“, Arista EOS und HPE Aruba CX lesend; ArubaOS-Switch/ProCurve erkannt, nicht gelesen (ADR 0025 Nr. 13) |
| **0.94.1** | Korrektur: unerwartete Fehler mit Referenz in Antwort und Protokoll (Fehlersuche HTTP 500 der Applikations-Doku) |
| **0.95.0** | Applikations-Doku: Klick auf eine EPG öffnet ihr Detail (statt Sprung zu den Regeln), Objekt-Detail in der Doku, Namen der Gegenstellen als Einstellung des Admins (ADR 0012 Ergänzung 0.95.0) |
| **0.96.0** | NAT / VIPs: Kette VIP der Firewall → Load Balancer → Server (Liste, Detail, Suche, CSV-Export), VIP-Gruppen der FortiGate, Namenskonvention für VIPs/Virtual Server/IP-Pools (ADR 0027/0016 Ergänzung 0.96.0) |
| **0.97.0** | L3Outs: Firewall führend – Firewall hinter jedem L3Out (abgeleitet bzw. von Hand), Zuordnung der Gegenstellen nach dem Routing der Firewall, Contracts zu externen EPGs nur als Hilfs-Contract an der Firewall-Regel, unspezifische Zugriffe nach L3Out geordnet (ADR 0034) |
| **0.98.0** | Weg-Suche: Details am Weg im Seitenpanel bzw. neuen Tab, „mehr“ je Hop, Suche in der URL, Anbindung als Quelle bzw. Ziel (ADR 0019 Ergänzung 0.98.0) |
| **0.99.0** | Routing bis an die Firmengrenze: Router und L3-Switches strikt lesend (RIB, BGP je VRF), als Knoten in Weg-Suche und Topologie, Firmengrenze bei eBGP zu fremdem AS, Anbindung bzw. Default-Route zum Provider (ADR 0032) |

### Nächste Arbeiten (Stand 0.99.0)

**Abgenommen (Anwender):** alle Stände bis 0.43.0 gegen die echten Systeme (2026-10-23), bis
**0.68.0** (2026-10-26). Zur Abnahme stehen 0.69.0–0.99.0.

**Befunde des Anwenders aus dem Test (2026-10-27, Vorrang):**

| Befund | Stand |
|---|---|
| HTTP 500 beim Laden der Doku einer Applikation | 0.94.1: Fehler mit Referenz im Protokoll; Ursache mit dem Protokoll des Anwenders beheben |
| Klick auf ein Element der Applikation → Detail wie in „Objekte & Inventar“ statt Sprung zu den Regeln | **umgesetzt in 0.95.0** (EPG-Detail, Objekt-Detail in der Doku) |
| L3Outs: nicht das ACI-Routing (zu offen), die Firewalls entscheiden; Zuordnung L3Out → Firewall | **umgesetzt in 0.97.0** (ADR 0034: Firewall dahinter abgeleitet bzw. von Hand, Routing der Firewall entscheidet) |
| Contracts über L3Outs nur als Hilfs-Contract, nicht separat listen | **umgesetzt in 0.97.0** (Spalte „L3Out“ an der Firewall-Regel) |
| Doku nach Kategorien: Namen von Systemen ausserhalb des Tenants – Einstellung des Global-Admins | **umgesetzt in 0.95.0** (Mandant „Namen der Gegenstellen“, nie für Kunden-Benutzer) |
| Unspezifische Zugriffe nach dem L3Out ordnen, über den sie hereinkommen | **umgesetzt in 0.97.0** |
| NAT / VIPs: VIP der Internet-Firewall mit dem VIP des Load Balancers verknüpfen (externe IP → Server) | **umgesetzt in 0.96.0** (Kette in Liste/Detail/Suche, CSV-Export, VIP-Gruppen) |
| Weg-Suche: mehr Infos am Pfad ohne die Suche zu verlassen (Detail, neuer Tab) | **umgesetzt in 0.98.0** (Seitenpanel, „↗“ neuer Tab, „mehr“ je Hop, Suche in der URL) |
| Weg-Suche von bzw. zu einer Anbindung | **umgesetzt in 0.98.0** (`src_exit`/`dst_exit`, Start mit dem Eingang der Firewall) |
| NAT / VIPs: Namen gegen die Namenskonvention prüfen | **umgesetzt in 0.96.0** (Präfix je Typ VIP/Virtual Server/IP-Pool) |

**Auftrag des Anwenders (2026-10-27: „2 durchführen ohne den Punkt 7 / 3 Sprache / 4
Verkabelung, Applikationen, Kunden, Routing“):**

| Bereich | Inhalt | Stand |
|---|---|---|
| Ablauf | `main` nachführen | **erledigt** (PR #2, `main` = 0.87.0) |
| Schritt 6 | Daten verschärfen: Datenqualität der gelesenen Daten und des IPAM | **erste Fassung in 0.91.0** (ADR 0029, Inhalt zur Abstimmung) |
| Sprache | ADR 0028 Schritt 4: Laufmeldungen, Collector/Agent, CLI | **umgesetzt in 0.90.0** |
| Verkabelung | Kabel und Ports nach NetBox (Vorschau → Übernehmen), IPv6-Nachbarn, weitere Hersteller; SNMPv3 als Alternative zu SSH | Kabel/Ports nach NetBox **umgesetzt in 0.93.0** (ADR 0031); IPv6-Nachbarn, Arista EOS und Aruba CX **umgesetzt in 0.94.0** (ADR 0025 Nr. 13; ArubaOS-Switch/ProCurve bewusst nicht); SNMPv3 danach |
| Applikationen | Abhängigkeiten wieder einblenden (Infrastruktur getrennt) | **umgesetzt in 0.88.0** |
| Applikationen | Export nach NetBox (Tags) | **umgesetzt in 0.93.0** (ADR 0031) |
| Kunden | feinere Rollen der Kunden-Benutzer | **umgesetzt in 0.89.0** |
| Routing | Verlauf der Routen; Systeme bis an die Firmengrenze (Aussenrouter lesen) | Verlauf **umgesetzt in 0.92.0** (ADR 0030); Firmengrenze **umgesetzt in 0.99.0** (ADR 0032) |

**Deutsch/Englisch (Anwender 2026-10-27, ADR 0028):** Schritt 1 Oberfläche – **umgesetzt in
0.86.0**; Schritt 2 Texte vom Server und Schritt 3 Exporte mit Sprache der Datei – **umgesetzt in
0.87.0**; Schritt 4 Meldungen der Läufe, Collector/Agent und CLI – **umgesetzt in 0.90.0**.

**Rückmeldung des Anwenders (2026-10-26, zweite Runde):** Korrekturen (Wartezeiten beim
Speichern, Mitglieder, Vorschläge je Tenant, Objekt-Filter, IPAM-Container) – **umgesetzt in
0.74.1**; Import der Stammdaten von Applikationen aus CSV (Ausnahme vom „kein CSV-Import“ nur
dafür), Applikationen ohne VMs ausblenden bzw. löschen, Infos zuklappbar (Routing: „Gelesene
Kontexte“ zu, Reiter ohne Scrollen sichtbar) – **umgesetzt in 0.75.0**; Kunde oben wählen
filtert alles (eigenes plus geteiltes, markiert) – **umgesetzt in 0.76.0**; externe Netze/Hosts
ohne IPAM-Befund (ausser im WAN-Interface-Netz) – **umgesetzt in 0.76.1**; **VMware NSX
Advanced Load Balancer (Avi)** als lesende Quelle für die Doku – **umgesetzt in 0.77.0** (ADR
0027; offen: Adressen der Service Engines, Content Switching, GSLB); weiter mit Schritt 5.

**Dazwischen (Rückmeldung des Anwenders zur Applikations-Doku, 2026-10-26):** Übersicht mit
zuklappbaren Bereichen, Systeme kompakt, Export mit Auswahl, Abhängigkeiten vorerst entfernt,
Befund nur bei uneinheitlichen Wegen – **umgesetzt in 0.73.0**; der Netzwerkplan der
Applikation kompakt (VMs in der EPG, Rahmen um AP und EPGs, aktive L3Outs, Firewalls und Ziele,
Regeln und Filter als ausblendbare Ebene, Zoom und Scrollen; eigener Plan, Export draw.io
bleibt) – **umgesetzt in 0.74.0**; weiter mit Schritt 5.

**Umplanung des Anwenders (2026-10-26) – Reihenfolge bis zum Abschluss des ersten Projekts:**

| Schritt | Inhalt | Stand |
|---|---|---|
| 1 | **Export nach Confluence** – Entscheid des Anwenders: kein Schreibweg, Word (.docx) und draw.io zum Import von Hand, zuerst Applikations-Doku und Zonenplan (ADR 0026) | **umgesetzt in 0.71.0** |
| 2 | **Parallele Firewalls:** Abweichungen bei Policy-Routen, Routing und NAT zwischen den Firewalls eines Verbunds (ADR 0020 Ergänzung 0.72.0) | **umgesetzt in 0.72.0** |
| 3 | **Schwachstellen** aus der Übersicht 0.70.0: ACI-Filter nur mit Quellport (Urteil statt „möglich“), Rückrichtung in der Doku je System wie in der Applikations-Doku, Leistung der Zonenmatrix mit der Rückrichtung messen (20 000 Contracts ≈ 1,2 s, linear – kein Zwischenspeicher nötig) | **umgesetzt in 0.72.1** |
| 4 | **Lücken im Ablauf:** nächtlicher CI-Lauf auf dem aktuellen Stand (`main` nachführen), Überschriften der Roadmap | `main` = 0.70.0 (PR #1, 2026-10-26), = 0.87.0 (PR #2, 2026-10-27); weiter nachführen per PR auf Wunsch des Anwenders |
| 5 | **Zonen komplett:** Zonen je Kunde im Grundschema, Befund „Zone ausserhalb des Schemas“, draw.io-Datei des Zonenplans zurück einlesen, Relevanz nach dem Weg in der Zonenmatrix, Übergänge über Routen ohne gemeinsames Netz, Absicht je Richtung im Plan, Zonen in den Verbindungen der Applikations-Doku (ADR 0024) | Teil 1 **umgesetzt in 0.78.0** (Zonen in den Verbindungen, Absicht je Richtung im Plan, Befund „Zone ausserhalb des Schemas“); Teil 2 **umgesetzt in 0.81.0** (Relevanz nach dem Weg in der Matrix, Übergänge zu Zonen nur hinter einem Router); Teil 3 **umgesetzt in 0.82.0** (Zonen je Kunde im Grundschema); Teil 4 **umgesetzt in 0.83.0** (Zonenplan aus draw.io zurück einlesen) – **abgeschlossen** |
| 6 | **Daten verschärfen** (Inhalt mit dem Anwender festlegen) | erste Fassung **Datenqualität in 0.91.0** (ADR 0029: Bereiche mit Ampel/Ursache/Link, Befunde „Stand veraltet“/„nur teilweise gelesen“); Inhalt mit dem Anwender abstimmen |
| 7 | **Erstes Projekt abschliessen:** Produktivschaltung des DC mit den R14-Massnahmen (ADR 0013, RISKS) | danach |

**Danach (Entscheid nach der Produktivschaltung des DC): Mehrmandanten wie Magister.** Im
Cockpit (ADR 0014) Firmen anlegen und definieren; neben dem DC weitere Kunden, unabhängige
Systeme und Lösungen mit anderen Topologien dokumentieren. Zu entscheiden: je Firma eigene
NetBox und Datenbank oder alles am gleichen Ort (heute: eine Instanz je Umgebung, ADR 0018;
Datenhaltung R14). Eigene ADR vor dem Umbau.

**Cockpit für alle Werkzeuge (Plan 2026-10-27, ADR 0014 Entwurf v2, noch nichts umgesetzt):**
das Cockpit von Magister herauslösen und für Automato, Magister, NetBox und Keycloak ausbauen –
Inventar und Status (P1), Zugänge über Keycloak mit App-Starter (P2), Updates über einen
Host-Agenten (P3), Ausrollen neuer Instanzen auf vorbereiteten VMs (P4); acht offene Entscheide
des Anwenders in der ADR.

**Frühere Reihenfolge (Stand 0.65.0):**

**Entscheide des Anwenders (2026-10-23):**

- **Bewusst später:**
  - Routing bleibt eine Momentaufnahme; Verlauf und Pflege der BGP-Umgebung viel später
    (Verlauf auf Wunsch des Anwenders 2026-10-27 umgesetzt in 0.92.0, ADR 0030).
  - Aussenrouter bzw. Systeme bis an die Grenzen der Firmen später, absichtlich noch nicht.
  - Mehrfachausfälle und Redundanz erst nach Zonen und Design, mit Switching und Verkabelung.
- **Verkehr zwischen VRFs:** über die Firewall; Klarheit bringen Zonen und das Regelwerk der
  BGP-Filter.
- **FortiGate:** kein Central-NAT; SNAT genauer, damit weniger Fälle unbestimmt bleiben.
- **ACI:** Shared Services und Route-Leaking zwischen VRFs und Tenants abbilden. vzAny über die
  aufgelöste VRF (`fvRsCtx.tDn`) statt über den Namen.
- **R12:** Die Zuordnung der VMs (IPs über Portgruppen und deren EPGs) geht vor gelernten
  Endpunkten – konfigurierte Vorgaben statt vom ACI Gelerntes (erster Teil in 0.43.1).
- **Logische Zonen** werden in Automato gepflegt.
- **Plan:** im Tool finden und exportieren (draw.io, SVG, PDF); für Confluence seit 0.71.0
  Word + draw.io zum Import von Hand (ADR 0026, Entscheid 2026-10-26: kein Schreibweg).
- **Vor dem Produktivbetrieb jetzt:** TLS intern, getrennte DB-Rollen, Fristen für das Löschen
  alter Läufe. UI-Einschränkung, OTP, echte API-Tokens und Verschlüsselung später bzw. mit
  anderen Mitteln.
- **Kunden** (ADR 0018, Schritt 4):
  - Ein Kunde besitzt logische Zonen; geteilte Zonen gehören keinem.
  - Der Kunde sieht Contracts und Elemente in seinem Tenant und die Contracts zwischen den
    Kunden, damit ihm bewusst ist, was offen ist und was nicht.
  - Sein Plan enthält nur seine Systeme und die geteilten Elemente, an denen er angebunden
    ist (auch Redundanzen), keine Fremdsysteme.
  - Seine Doku enthält nur seine Dinge – nie die Infrastruktur oder andere Kunden.
  - Zuordnung Benutzer → Kunde in Automato, getrennt von der Anmeldung (Keycloak); wichtig für
    feinere Rollen.

**Entscheide des Anwenders (2026-10-25), Reihenfolge nach 0.53.0:**

1. **Zonen automatisch erkennen und vorschlagen** – umgesetzt in 0.54.0.
2. **Grundschema der Zonen** als Grundlage: in der Namenskonvention anhand einer Zeichnung
   (draw.io) definierbar (Zonen, Art, Farbe, Erkennungsmerkmale); die Vorschläge richten sich
   danach – umgesetzt in 0.55.0. Ohne Schema arbeitet die Erkennung wie in 0.54.0
   (Wörterbücher ohne Funktionsrelevanz).
3. **Zonen/Kunden fertig:** Zonenmodell nach Ebenen mit Übergängen, Vorlage mit festen Farben und
   Plan je Kunde nach den Korrekturen des Anwenders – umgesetzt in 0.56.0 (ADR 0024 Nr. 12).
   Zonen je Hop in der Weg-Suche und Absicht je Dienst – umgesetzt in 0.57.0 (ADR 0024 Nr. 13);
   Plan je Kunde nur mit den benötigten Firewalls/ACI-VRFs – Korrektur in 0.57.1. Doku des
   Kunden ohne Gerätenamen und Zugehörigkeit nach dem Weg – umgesetzt in 0.58.0 (ADR 0018
   Schritt 4). Offen: feinere Rollen.
4. **Applikationen ausbauen:** Verbindungen Ende zu Ende exportieren/drucken und Abweichung
   Regel ↔ Weg als Befund – umgesetzt in 0.59.0 (ADR 0021); Regeln direkt zuordnen bzw.
   ausschliessen und Abhängigkeiten zwischen Applikationen – umgesetzt in 0.60.0 (ADR 0012).
   Dazwischen (Wunsch des Anwenders, 2026-10-25): Zonenplan übersichtlich bei vielen Tenants
   und Zonen – Filter, Ebenen ausblenden, Zonen von Hand anordnen – umgesetzt in 0.61.0 (ADR 0024
   Nr. 14). Netzwerkplan je Applikation (Knoten der Wege, Dienste und Interfaces an den
   Verbindungen, Export) – umgesetzt in 0.67.0 (ADR 0021). Offen bei den Applikationen: Export
   nach NetBox (eigene Freigabe).
5. **Switching/Verkabelung:** Cisco-Switches strikt lesend (Interfaces, VLANs, LLDP/CDP),
   Verkabelung als L1/L2-Sicht – Grundlage für Redundanz und Mehrfachausfälle; vorher eigene ADR.
   Erste Fassung in 0.62.0 (ADR 0025): Switches über SSH mit fester Befehlsliste, Verkabelung aus
   CDP/LLDP aller Quellen, Befunde zwischen zwei gelesenen Switches. MAC-Tabelle/ARP (welcher
   Server an welchem Port, „Wo hängt …“, VLAN am ESXi-Uplink) in 0.63.0. L2-Sicht (Plan,
   Ausfall simulieren, Redundanz, VLAN-Wege, L2 in der Weg-Suche) in 0.64.0. vPC-Paar als ein
   logischer Switch, Spanning Tree, mehrere gleichzeitige Ausfälle und Export des Plans in
   0.65.0. Weiter: Kabel/Ports nach NetBox (eigene Freigabe), SNMPv3 als Alternative zu SSH,
   IPv6-Nachbarn, weitere Hersteller.

**Reihenfolge bis 0.53.0 (Vorschlag, vom Anwender gutgeheissen):**

| Version | Inhalt |
|---|---|
| ~~0.44.0~~ | ACI zwischen VRFs/Tenants – **umgesetzt in 0.44.0** (offen: Shared L3Out, vzAny als Consumer eines Shared Service in der Doku) |
| ~~0.45.0~~ | Verbindungen Ende zu Ende in der Applikations-Doku – **umgesetzt in 0.45.0** (ADR 0021 Entwurf; offen: Export/Druck, Abweichung als Befund) |
| ~~0.46.0~~ | Konfiguration vor Gelerntem überall – **umgesetzt in 0.46.0** |
| ~~0.47.0~~ | FortiGate SNAT genauer – **umgesetzt in 0.47.0** |
| ~~0.48.0~~ | Betrieb: Fristen für alte Läufe, getrennte DB-Rollen – **umgesetzt in 0.48.0** (ADR 0022) |
| ~~0.49.0~~ | Betrieb: TLS intern (interne CA) – **umgesetzt in 0.49.0** (ADR 0023) |
| ~~0.50.0~~ | Topologie-Modell (`GET /topology`) und Plausibilitäts-Befunde – **umgesetzt in 0.50.0** |
| ~~0.51.0~~ | Logische Zonen und Zonenplan im Tool, Zonenmatrix – **umgesetzt in 0.51.0** (ADR 0024; offen: Zonen je Hop in der Weg-Suche, Absicht je Dienst) |
| ~~0.52.0~~ | Export (draw.io, SVG, PDF) und Versionen des Plans – **umgesetzt in 0.52.0** (Confluence: Word + draw.io in 0.71.0; offen: draw.io zurück einlesen) |
| ~~0.53.0~~ | Kunden auf Zonen, Kunden-Benutzer in der Oberfläche – **umgesetzt in 0.53.0** (ADR 0018 Schritt 4; offen: Regeln nach dem Weg, Doku des Kunden ohne Gerätenamen, feinere Rollen) |
| ~~0.54.0~~ | Zonen automatisch erkennen und vorschlagen – **umgesetzt in 0.54.0** (ADR 0024 Nr. 10) |
| ~~0.55.0~~ | Grundschema der Zonen in der Namenskonvention (draw.io) – **umgesetzt in 0.55.0** (ADR 0024 Nr. 11; offen: Zonen je Kunde im Schema, Befund „ausserhalb des Schemas“) |
| ~~0.56.0~~ | Zonenmodell nach Ebenen, Vorlage mit festen Farben, Plan je Kunde – **umgesetzt in 0.56.0** (ADR 0024 Nr. 12; Korrekturen des Anwenders nach dem Test der Zonen) |
| ~~0.57.0~~ | Zonen je Hop in der Weg-Suche, Absicht je Dienst – **umgesetzt in 0.57.0** (ADR 0024 Nr. 13) |
| ~~0.58.0~~ | Kunden-Doku ohne Gerätenamen, Zugehörigkeit nach dem Weg – **umgesetzt in 0.58.0** (ADR 0018 Schritt 4) |
| ~~0.59.0~~ | Verbindungen Ende zu Ende exportieren/drucken, Abweichung Regel ↔ Weg als Befund – **umgesetzt in 0.59.0** (ADR 0021) |
| ~~0.60.0~~ | Applikationen: Regeln direkt zuordnen (auch any/unspezifische), Abhängigkeiten zwischen Applikationen – **umgesetzt in 0.60.0** (ADR 0012 Ergänzung 0.60.0) |
| ~~0.61.0~~ | Zonenplan übersichtlich: Filter, Ebenen ein-/ausblenden, Zonen ziehen – **umgesetzt in 0.61.0** (ADR 0024 Nr. 14) |
| ~~0.62.0~~ | Switching/Verkabelung erste Fassung: Cisco-Switches strikt lesend, Verkabelung aus CDP/LLDP, Befunde – **umgesetzt in 0.62.0** (ADR 0025) |
| ~~0.63.0~~ | Endpunkte aus MAC-Tabelle und ARP, „Wo hängt …“, VLAN am ESXi-Uplink – **umgesetzt in 0.63.0** (ADR 0025 Nr. 8) |
| ~~0.64.0~~ | L2-Sicht: Plan, Ausfall simulieren, Redundanz- und VLAN-Befunde, L2 in der Weg-Suche – **umgesetzt in 0.64.0** (ADR 0025 Nr. 9) |
| ~~0.65.0~~ | Switching/Verkabelung abrunden: vPC-Paar, Spanning Tree, mehrere Ausfälle, Export des Plans – **umgesetzt in 0.65.0** (ADR 0025 Nr. 10) |
| ~~0.67.0~~ | Netzwerkplan je Applikation mit Diensten und Interfaces an den Verbindungen – **umgesetzt in 0.67.0** (ADR 0021; Wunsch des Anwenders 2026-10-26) |
| ~~0.68.0~~ | Weitere Produkte: Catalyst 1300/9200L/3650/4500, Nexus 9000, Juniper MX – **umgesetzt in 0.68.0** (ADR 0025 Nr. 11; offen: Routing-Tabelle des MX für die Wege) |
| ~~0.69.0~~ | Regeln je Kategorie der Applikations-Doku ausblenden mit Begründung und Audit – **umgesetzt in 0.69.0** (ADR 0012) |
| ~~0.69.1/0.70.0~~ | ACI zustandslos, VLANs aus dem ACI, Patchungen im Plan, Grundschema in der Oberfläche bearbeiten – **umgesetzt** (Wünsche des Anwenders 2026-10-26) |
| danach | Abnahme 0.43.1 ff. gegen die grössere Testumgebung (Pilot Zonen und Pläne) – bis 0.68.0 erledigt (2026-10-26); Kabel/Ports nach NetBox nur mit eigener Freigabe, SNMPv3 (siehe oben) – nach der Umplanung vom 2026-10-26 hinter der Produktivschaltung |

Frühere Reihenfolge (Stand 0.42.0, zum Nachlesen):

Konzeptbefund
des Anwenders: Ohne Routing, Verbindungswege und Redundanzen zeigt die Doku Zugriffe, die
routingtechnisch nicht möglich sind, und `any` aus dem Internet wie eine Liste aller
Gegenstellen. Deshalb zuerst Umgebung/Kunden und Topologie:

1. **ADR 0018 und 0019 durchsehen** (Entwurf): Umgebung und Kunden (eine Instanz je
   Umgebung, Kunden als Ausschnitt über Selektoren, Kunden-Benutzer über Keycloak) und
   Topologie/Pfade.
2. **VDOM-Kontext** (ADR 0019, Stufe 1) – **umgesetzt in 0.15.0** und gegen die echten VDOMs
   abgenommen: alle VDOMs lesen, VDOM an Policies, Objekten, NAT, Interfaces und Subnetzen.
3. **Umgebung und Kunden** (ADR 0018): Selektoren und abgeleitete Zugehörigkeit **in 0.30.0**
   (Schritt 1), Kunden-Filter in allen Ansichten **in 0.32.0** (Schritt 2), Kunden-Benutzer mit
   serverseitiger Einschränkung **in 0.33.0** (Schritt 3).
4. **Routing, HA und L3Out lesen** (ADR 0019, Stufe 2) – **umgesetzt in 0.16.0** und
   abgenommen. **Pfade und Relevanz** (Stufe 3): Weg Hop für Hop mit Quelle bzw. Startpunkt –
   **erste Fassung in 0.17.0**, Anbindungen nach aussen und Befund bei abweichenden Regeln
   paralleler Firewalls **in 0.18.0**, redundante Wege **in 0.19.0**, erkannte Anbindungen mit
   Redundanz **in 0.20.0**, Regeln je Hop auf dem Weg **in 0.22.0**, NAT auf dem Weg **in 0.23.0**, Regeln nur, wo der Verkehr durchkommt (Doku) **in
   0.24.0**, Gegenstelle `any` als das, was hinter dem Interface liegt, **in 0.25.0**, Relevanz in der
   Regel-Liste **in 0.26.0**, Inter-VDOM-Links als Kanten **in 0.27.0**, ACI-Contracts über externe EPGs **in 0.28.0**, Verbund bzw. Session-Sync-Paar als ein Hop **in 0.29.0**, Weg-Suche als eigene Ansicht mit ACI-Contracts je VRF-Hop und Suche nach Namen **in 0.35.0**. Offen:
   Aussenrouter selbst lesen.
5. **Designer** (ADR 0019, Stufe 4), Zeichnung später. Vorgesehen: normale Switches und
   weitere Geräte erfassen, Verkabelung dokumentieren (L1/L2-Sicht neben dem Routing).
6. **Applikationen ausbauen:** Regeln direkt zuordnen, Abhängigkeiten zwischen
   Applikationen.
   - **Jede Verbindung einmal, Ende zu Ende** (Anwender, 2026-10-23): Läuft eine Verbindung
     der Applikation über mehrere Firewalls und das ACI, zeigt die Doku sie einmal – mit dem
     Weg (ADR 0019) und je Hop, was zugelassen ist und welche Policy bzw. welcher Contract
     dabei greift. Mit der **Reihenfolge der Regeln** je Firewall, damit bei zwei gleichen
     Regeln klar ist, welche wirklich zieht (die erste passende; die zweite ist verdeckt).
     Grundlage: Weg Hop für Hop und Regeln auf dem Weg (0.17.0/0.22.0), ACI-Contracts über
     externe EPGs (0.28.0).
7. **Produktiv schalten** (ADR 0013) mit den R14-Massnahmen; **Cockpit** für mehrere
   Instanzen (ADR 0014), **Import-Wizard (E3)**.
8. Phase B (Bereinigen auf Firewall/DDI) und Phase C (Regeln schreiben,
   FortiGate/FortiManager/ACI) nur mit eigener ADR und Freigabe.

**Änderungen sichtbar + Rollback** (eigenes Querschnitts-Thema) wird über das
Cockpit (Update-Channels + Rollback-Snapshot, E2) und — für den Schreibpfad — über
das Plan→Approve→Apply→Rollback-Jobmodell (E4) abgedeckt.

**UC1-Fachdaten erhalten & festgehalten:** Standard-Namenskonvention (Profil „default")
(`docs/naming-conventions.md`), Tenant-Modell Kunde/GG und die Applikations-Doku-Zieltabellen
(`docs/usecases/uc1-applikations-doku-spec.md`) → **ADR-0004-Ergänzung** (Mandant = GG,
Kunde/Umgebung darüber mit SoT + Namensprofil, Applikation = System mit AP/Projekt-Klammer,
Ausnahmen-Kaskade, neue Collector-Felder). **Samples erhalten** (ACI + FortiGate) →
Lesevertrag `docs/collectors-read-contract.md`. Wörterbücher (Kap. 3.1/3.2) sind als in-App
pflegbare Daten umgesetzt (Namensprofile, ohne Funktionsrelevanz).
