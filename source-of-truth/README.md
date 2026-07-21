# Source of Truth (NetBox)

NetBox liefert den Soll-Zustand der Infrastruktur (Geräte, IPAM, VLANs, VMs, Verkabelung, ...).

## Zuständigkeit dieses Ordners
- Deployment-Konfiguration für NetBox (Docker Compose / Helm)
- Sync-/Validierungsskripte zwischen NetBox und Ansible-Inventory
- Custom Fields / Tags / Schema-Erweiterungen, die das Projekt benötigt

NetBox selbst wird nicht neu entwickelt, sondern als etabliertes Standard-Tool eingebunden.

Status: 🚧 noch nicht aufgesetzt (siehe ROADMAP.md, M6 „Grundaufbau"; Tool-Entscheid
NetBox vs. Infoblox pro Kunde fällt in M1)
