# Source of Truth (NetBox)

NetBox liefert den Soll-Zustand der Infrastruktur (Geräte, IPAM, VLANs, VMs, Verkabelung, ...).

## Zuständigkeit dieses Ordners
- Deployment-Konfiguration für NetBox (Docker Compose / Helm)
- Sync-/Validierungsskripte zwischen NetBox und Ansible-Inventory
- Custom Fields / Tags / Schema-Erweiterungen, die das Projekt benötigt

NetBox selbst wird nicht neu entwickelt, sondern als etabliertes Standard-Tool eingebunden.

## Lab-Setup

- `netbox/` – Bootstrapping einer NetBox-Instanz für den Model-2-Durchstich:
  `./netbox/setup.sh` (netbox-docker, Admin-User, read-only Collector-Token),
  optional `netbox/seed_lab.py` für minimale Lab-Daten. Siehe `netbox/README.md`.
  Infoblox (Model 1) folgt in Phase 2.

Status: 🧪 Lab-Setup-Skript vorhanden (NetBox), Instanz noch aufzusetzen
(siehe ROADMAP.md, M6 „Grundaufbau")
