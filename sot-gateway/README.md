# SoT Gateway

Adapter-Package für den Zugriff auf das jeweils aktive IPAM-/SoT-Tool (ADR 0002).
Der Rest der Plattform kennt nur das kanonische Modell (`NetworkObject`) und das
`SoTAdapter`-Protokoll – nie tool-spezifische Formate.

- `sot_gateway/models.py` – kanonisches Datenmodell
- `sot_gateway/interface.py` – Adapter-Protokoll (`get_by_name`, `search_by_ip`)
- `sot_gateway/adapters/netbox.py` – NetBox, **lesend** – erster Adapter für UC1
  (Model 2); aktuell Stub, wird beim M5-Bau umgesetzt (ADR 0004)
- `sot_gateway/adapters/infoblox.py` – Infoblox DDI via WAPI, **lesend** – Phase 2
  (Model 1); Skeleton vorhanden, gegen Fixtures getestet

Zurzeit eine Library (vom Orchestrator konsumiert); Heraustrennung als eigener
Microservice ist vorbereitet – siehe ADR 0003.

Tests laufen gegen aufgezeichnete WAPI-Antworten (`tests/fixtures/`), es ist
kein Infoblox-System nötig: `uv run --package sot-gateway pytest sot-gateway`
