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

Als Nächstes: **M1 – Use Cases erarbeiten und priorisieren** (Use Case #1 mit
Definition of Done festlegen, Kunden-Tool-Paar fixieren; Favorit gemäss Vorarbeit:
Firewall-Regel-Automatisierung). Danach folgt u. a. **M5 – Datenmodell** für den
Rule & Service Store (Adressobjekt-Referenzen, Service-Objekte,
Firewall-Rule-Intents, Verknüpfung zum Topology Service). Offene Fragen dazu
siehe ADR 0002, Abschnitt "Offene Punkte".
