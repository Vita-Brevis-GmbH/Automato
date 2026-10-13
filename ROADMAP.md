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
- [ ] **Produktions-Gate (offen):** AuthN/RBAC vor der öffentlichen Read-API; optional
  Finding-Triage-Schreibpfad; Transport-/at-rest-Verschlüsselung, Netzsegmentierung
  (Folge-ADRs, ADR 0005).

**Exit-Kriterium:** UC1 Model 2 ist **funktional vollständig und Lab-/Review-reif**
(DoD erfüllt, Security-Check für den Scope bestanden). Die **produktive Freigabe**
ist an das AuthN/RBAC-Gate gebunden (fail-closed erzwungen).

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
