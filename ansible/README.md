# Ansible

Die eigentliche Automatisierungslogik. Wird von den Workern aufgerufen, nicht direkt vom Orchestrator.

## Struktur
- `inventories/` – dynamisches Inventory aus NetBox (Source of Truth) + statische Fallbacks
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
