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
├── orchestrator/          Zentrale Steuerung: API, Jobs, Auth, Scheduling
├── workers/                Worker Nodes je Zielplattform
│   ├── fortigate/
│   ├── cisco-aci/
│   ├── vmware/
│   ├── proxmox/
│   ├── windows/
│   ├── linux/
│   └── cisco-switches/
├── ansible/                 Playbooks, Rollen, Inventories
├── source-of-truth/         NetBox-Integration (Soll-Zustand)
├── documentation-engine/    Ist-Dokumentation + Compliance-Reports
├── web-ui/                  Frontend inkl. Wizards
│   └── wizards/
├── integrations/            Externe Schnittstellen (ServiceNow, ...)
├── security/                Secrets-Management, RBAC, Audit-Log
├── infra/                   Deployment (Docker/Kubernetes), CI/CD
└── docs/                    Projektdokumentation, ADRs
```

## Status

🚧 Planungsphase – Struktur steht (M0 weitgehend erledigt), als Nächstes M1: Use Cases
erarbeiten und priorisieren (siehe ROADMAP.md).
