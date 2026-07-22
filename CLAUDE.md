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
First-Class-Signal). Details/DoD-Entwurf: `docs/usecases/uc1-firewall-regel-dokumentation.md`
(**DoD noch zu bestätigen**). Die Regel-Automatisierung gemäss ADR 0002 folgt als
späterer Use Case.

Der **Grundaufbau (M6) ist als Code-Gerüst umgesetzt** (Entscheide: ADR 0003):
uv-Workspace mit `orchestrator/` (FastAPI, Alembic, Audit-Log, Job-Kreislauf),
`collectors/` (FortiGate/ACI-Skeleton, Beispieldaten bis zum Lab), `sot-gateway/`
(Infoblox-Adapter lesend, NetBox-Stub), `web-ui/frontend/` (React/Vite-Skeleton);
dazu docker-compose (Profile core/collectors), CI (`.github/workflows/ci.yml`)
und pre-commit/gitleaks. Tests: `uv run --package <name> pytest <name>/tests`.

Als Nächstes: DoD bestätigen (M1-Exit), **M2 Risiken-Review**, **M3
Architektur-/Security-Review**, dann **M5-Bau** (DDL/SQLAlchemy + Alembic aus ADR 0004)
sowie Lab + NetBox-Anbindung für den Model-2-Durchstich (Rest von M6). Offene Fragen
siehe ADR 0004 (Phasenplan), ADR 0002 ("Offene Punkte") und das UC1-Dokument.
