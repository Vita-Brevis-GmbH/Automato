# Automation Platform

Automatisierungsplattform für heterogene Infrastruktur mit den Kernzielen:

- **Automatische Dokumentation** des Live-Systems (Ist-Zustand)
- **Compliance-Abgleich**: Live-System vs. Konfigurationsvorgaben (Source of Truth)
- **Automatisierung** von Fortigate, Cisco ACI, VMware, Proxmox, Windows, Linux und Cisco Switches
- **Wizards** für wiederkehrende Aufgaben (Contract-Erfassung, neue VM, ...)
- Strikte **Microservice-Trennung** zwischen Ansible, Dokumentation, Source of Truth und Web UI
- **Multiple Worker Nodes**, Orchestrator getrennt von den Arbeitern
- Hohe **Sicherheitsstandards**
- Anbindung externer Schnittstellen (ServiceNow u. a.)
- Philosophie: **klein anfangen, schrittweise zur Full-Stack-Automatisierung ausbauen**

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architektur, Komponenten, Datenflüsse, Tech-Stack |
| [ROADMAP.md](ROADMAP.md) | Meilenstein-Plan (M0–M9) mit Exit-Kriterien, bis zur Full-Stack-Automatisierung |
| [SECURITY.md](SECURITY.md) | Sicherheitskonzept |
| [docs/RISKS.md](docs/RISKS.md) | Risikoregister (lebendes Dokument) |
| [docs/adr/](docs/adr/) | Architecture Decision Records |

## Repository-Struktur

```
automation-platform/
├── orchestrator/          Zentrale Steuerung: API, Jobs, Audit-Log, Rule & Service Store
├── collectors/             Read-only Collectors (UC1): fortigate/, cisco_aci/
├── sot-gateway/            Adapter-Package IPAM/SoT (NetBox lesend, Infoblox folgt)
├── lab/                     Scriptbares Lab-Setup einer VM (NetBox + Automato-Stack)
├── workers/                Worker Nodes je Zielplattform (schreibend, spätere Use Cases)
│   ├── fortigate/
│   ├── cisco-aci/
│   ├── vmware/
│   ├── proxmox/
│   ├── windows/
│   ├── linux/
│   └── cisco-switches/
├── ansible/                 Playbooks, Rollen, Inventories
├── source-of-truth/         IPAM-/SoT-Anbindung (Deployment/Standards; Zugriff via sot-gateway)
├── documentation-engine/    Ist-Dokumentation + Compliance-Reports
├── web-ui/                  Frontend inkl. Wizards
│   └── wizards/
├── integrations/            Externe Schnittstellen (ServiceNow, ...)
├── security/                Secrets-Management, RBAC, Audit-Log
├── infra/                   Deployment (Docker/Kubernetes), CI/CD
└── docs/                    Projektdokumentation, ADRs
```

## Status

✅ **Use Case #1 (Model 2: FortiGate + NetBox) ist funktional vollständig und Lab-/Review-reif**
(M0–M9): read-only einlesen, Matching gegen NetBox, kanonische Services, Web-UI mit Filtern
(Systeme/Netzwerke/Zonen), Regel-Detail, NAT/VIPs, **Applikations-Doku/Export** und
Findings (Datenqualität). Reproduzierbar per `lab/bootstrap.sh`. Betrieb/Bedienung:
[docs/BETRIEB.md](docs/BETRIEB.md).

**Produktions-Gate (offen):** Die öffentliche Read-API hat noch kein AuthN/RBAC
(Folge-ADR, ADR 0005); ausserhalb `dev` ist der Start fail-closed. Weiter offen:
Model 1 (ACI + Infoblox), Schreibpfad. Details: [ROADMAP.md](ROADMAP.md),
[docs/usecases/](docs/usecases/).

Lab starten: `lab/bootstrap.sh` (siehe [lab/README.md](lab/README.md)) – danach
Web-UI auf http://localhost:5173, API auf http://localhost:8000.
