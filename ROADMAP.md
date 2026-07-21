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
- [ ] **Definition of Done** für Use Case #1 bestätigen (Entwurf liegt im
  UC1-Dokument), damit „abschliessen" in M9 messbar ist
- [x] Kunden-Tool-Paar fixiert: IPAM = **Infoblox DDI** (lesend). Contracts
  (ServiceNow vs. iTop) sind für UC1 nicht relevant – Entscheid vertagt auf den
  ersten schreibenden Use Case. Austauschbarkeit bleibt über die
  Adapter-Interfaces gewährleistet (ADR 0002)

Die Firewall-Regel-**Automatisierung** (ADR 0002: Rule-Intents, Aerleon,
Primary/Backup-Pfade) folgt als späterer Use Case und baut auf den in UC1
erfassten Daten auf.

**Exit-Kriterium:** Use Case #1 ist mit messbarer, **bestätigter** Definition of
Done dokumentiert.

## M2 – Grösste Risiken identifizieren

Bewusst **vor** der Architektur-Finalisierung, damit Risiken die Architektur beeinflussen können.

- [ ] Initiales Risikoregister erstellen → [docs/RISKS.md](docs/RISKS.md)
- [ ] Top-Risiken mit Gegenmassnahmen und Meilenstein-Bezug versehen
- [ ] Review-Rhythmus festlegen: Register wird an jedem Meilenstein-Abschluss aktualisiert

**Exit-Kriterium:** Register existiert, jedes Top-Risiko hat eine Gegenmassnahme.

## M3 – Architektur definieren & mit Security prüfen

- [ ] Architektur (`ARCHITECTURE.md`) gegen Use Case #1 und das Risikoregister validieren
- [ ] Security-Review der Architektur anhand `SECURITY.md`
- [ ] Offene Security-Punkte als ADRs anstossen: Secrets-Backend, Netzwerksegmentierung,
  RBAC-Rollenmodell, Zertifikatsverwaltung/mTLS
- [ ] Notwendige Anpassungen als ADRs dokumentieren

**Exit-Kriterium:** Architektur ist security-seitig freigegeben; offene Punkte haben eine
ADR-Nummer oder sind bewusst und dokumentiert vertagt.

## M4 – Microarchitektur definieren

Feinschnitt der Services, so weit, dass der Durchstich ohne Grundsatzdiskussionen baubar ist.

- [ ] Orchestrator-API, Job-Modell und Worker-/Collector-Protokoll (Job annehmen, Ergebnis melden) spezifizieren
- [ ] Collector-Interface (read-only) für FortiGate und Cisco ACI spezifizieren –
  inkl. Mapping ACI-Contracts/EPGs → normalisiertes Regelmodell (UC1)
- [ ] SoT-Gateway-Adapter-Interface spezifizieren (kanonisches Datenmodell, ADR 0002;
  erster Adapter: Infoblox, lesend – NetBox-Adapter später)
- [ ] Topology-Service-Schnittstelle spezifizieren (für UC1 nur das Zonen-Modell zur
  Filterung; Connections/Primary/Backup erst für die spätere Automatisierung)
- [ ] Kommunikationswege und Authentisierung zwischen den Services festlegen (mTLS, signierte Jobs)

**Exit-Kriterium:** Schnittstellen-Verträge dokumentiert; Durchstich (M7) ist damit planbar.

## M5 – Datenmodell definieren & Speicherorte fixieren

Löst die offenen Punkte aus ADR 0002.

- [ ] Datenmodell Rule & Service Store: für UC1 die normalisierten **Ist-Regeln**
  (Quellsystem, Quell-/Ziel-Referenzen, Service, Aktion, Zone, Erfassungszeitpunkt)
  plus Adressobjekt-Referenzen und Service-Objekte; Firewall-Rule-**Intents** und
  Topology-Verknüpfung so vorsehen, dass die spätere Automatisierung andockt
- [ ] Applikations-Zuordnung entscheiden: wie werden Regeln Applikationen zugeordnet
  (Infoblox Extensible Attributes, Naming-Konvention, manuelle Pflege) – siehe UC1, offene Fragen
- [ ] Persistenz-Entscheid Rule Store: Git-only vs. DB + Git-Sync → als ADR festhalten
- [ ] Zonen-Zuordnung entscheiden: automatisch aus IPAM-Tags oder manuell im Topology Service
  (inkl. Normalisierung FortiGate-Zonen vs. ACI-EPGs/Tenants)
- [ ] Speicherorte für das erste Projekt fixieren: welche Daten liegen im IPAM/Infoblox
  (via SoT Gateway), welche im Rule & Service Store, welche im Topology Service

**Exit-Kriterium:** Datenmodell und Speicherorte als ADR/Schema dokumentiert; Abschnitt
„Offene Punkte" in ADR 0002 ist abgearbeitet.

## M6 – Grundaufbau: Skripte, Installation, Laborumgebung

- [ ] Basis-CI (Lint, Tests) einrichten – selektiv pro Komponente (Konsequenz aus ADR 0001)
- [ ] `docker-compose` vervollständigen (NetBox inkl. Postgres/Redis, Orchestrator-Grundgerüst)
- [ ] Source of Truth anbinden (M1-Entscheid: **Infoblox DDI** – Test-/Kundeninstanz
  bzw. vNIOS-Eval klären), Naming-/Tagging-Standards definieren
- [ ] **Laborumgebung** mit Zielsystemen für Use Case #1 bereitstellen
  (FortiGate-VM; Cisco ACI via DevNet-Sandbox oder ACI-Simulator; Infoblox-Testinstanz)
  – der Durchstich läuft **nie** gegen Produktion
- [ ] Secrets-Handling von Anfang an korrekt: Ansible Vault, keine Klartext-Credentials (SECURITY.md)

**Exit-Kriterium:** Alle für den Durchstich nötigen Komponenten sind installierbar,
das Lab ist erreichbar.

## M7 – Durchstich (Walking Skeleton)

Eine bewusst **kleine Tätigkeit**, aber über den kompletten Pfad. Für UC1
(read-only) heisst das: **lesen statt schreiben**.

- [ ] Dünnster Ende-zu-Ende-Pfad: Auftrag (CLI/Minimal-UI) → Orchestrator → Job-Queue →
  Collector liest Regeln der Lab-FortiGate → normalisiert in den Rule Store →
  Anzeige der Regeln
- [ ] Eine Regel-Adresse via Infoblox-Adapter auflösen (ein Matching-Fall genügt)
- [ ] Job-Rückmeldung + Audit-Log-Eintrag (wer, wann, was, Ergebnis)

**Exit-Kriterium:** Ein Auftrag liest nachvollziehbar Regeln aus dem Lab, speichert
sie normalisiert und zeigt sie an – inklusive Audit-Trail.

## M8 – Review-Checkpoint nach dem Durchstich

Expliziter Halt, **bevor** der volle Use Case gebaut wird – hier werden Änderungen erkannt,
solange sie noch billig sind.

- [ ] Lessons Learned: Was hat nicht wie geplant funktioniert?
- [ ] Architektur, Microarchitektur und Datenmodell nachziehen, betroffene ADRs aktualisieren
- [ ] Risikoregister aktualisieren

**Exit-Kriterium:** Entscheidungen sind bestätigt oder angepasst – erst dann startet M9.

## M9 – Ersten Use Case abschliessen

- [ ] Use Case #1 vollständig gemäss Definition of Done aus M1 umsetzen
- [ ] Fehlerbehandlung umgesetzt und getestet (Collector-Ausfälle, unvollständige
  Daten, nicht zuordenbare Adressen sichtbar ausweisen)
- [ ] Security-/Hardening-Check vor Abschluss (Secrets, Least Privilege, Audit-Log vollständig)
- [ ] Dokumentation erstellt (Betrieb + Anwender)

**Exit-Kriterium:** Definition of Done erfüllt, Security-Check bestanden →
Use Case #1 ist produktiv nutzbar.

## Ausblick nach M9

Weitere Use Cases durchlaufen denselben Zyklus in verkürzter Form (M1 → M5 → M7 → M9),
da Struktur, Architektur und Grundaufbau dann stehen. Geplante Ausbaustufen:

- **Weitere Use Cases**: Read-only-Dokumentation & Compliance/Drift, VM-Provisioning, …
  (je nach M1-Priorisierung)
- **Worker-Ausbau**: weitere Plattformen (Fortigate, Cisco ACI, VMware, Proxmox, Windows,
  Linux, Cisco Switches), Ansible-Rollen je Plattform konsolidieren
- **Web UI & Wizards**: Grundgerüst (Auth gegen Orchestrator), Contract-Wizard,
  Neue-VM-Wizard, weitere nach Bedarf
- **Integrationen**: ServiceNow-Anbindung, weitere Schnittstellen (Monitoring, CMDB, Ticketing)
- **Skalierung**: multiple Worker-Instanzen pro Typ, Lastverteilung, Umzug auf Kubernetes,
  Hochverfügbarkeit für den Orchestrator
- **Hardening & Full Stack**: Security-Audit, HashiCorp Vault statt Ansible Vault,
  vollständiges RBAC, Monitoring/Alerting (z. B. Prometheus/Grafana), vollständige
  CI/CD-Pipeline, Disaster Recovery
