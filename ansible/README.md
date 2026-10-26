# Ansible

Die eigentliche Automatisierungslogik (Schreibpfad, spätere Use Cases). Wird von den
Workern über die Runner-Abstraktion aufgerufen, nicht direkt vom Orchestrator.
Je nach Use Case kommt statt Ansible auch Terraform/OpenTofu zum Einsatz –
siehe ARCHITECTURE.md, Abschnitt „Automatisierung".

## Struktur
- `inventories/` – dynamisches Inventory aus dem SoT (via SoT Gateway: Infoblox/NetBox je Kunde) + statische Fallbacks
- `playbooks/` – Playbooks je Aufgabe/Plattform
- `roles/` – wiederverwendbare Rollen je Plattform

## Geplante Collections
- `fortinet.fortios` (Fortigate)
- `cisco.aci` (Cisco ACI)
- `community.vmware` (VMware)
- `community.general` (Proxmox)
- `ansible.windows` (Windows)
- `ansible.posix` / `community.general` (Linux)
- `cisco.ios` / `cisco.nxos` (Cisco Switches)

Secrets: Start mit Ansible Vault (siehe SECURITY.md), keine Klartext-Credentials im Repo.
