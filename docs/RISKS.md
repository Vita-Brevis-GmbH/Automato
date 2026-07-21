# Risikoregister

Lebendes Dokument: wird an **jedem Meilenstein-Abschluss** (siehe `ROADMAP.md`) überprüft und
aktualisiert – neue Risiken aufnehmen, erledigte schliessen, Bewertungen nachziehen.

Bewertung: Auswirkung und Eintrittswahrscheinlichkeit jeweils **hoch / mittel / tief**.
„Bezug" nennt den Meilenstein bzw. das Dokument, in dem die Gegenmassnahme verankert ist.

## Register

| ID | Risiko | Auswirkung | Wahrsch. | Gegenmassnahmen | Bezug |
|---|---|---|---|---|---|
| R1 | Schreibzugriff auf produktive Infrastruktur: fehlerhafte Automatisierung verursacht Ausfall (grosser Blast Radius, v. a. Firewalls) | hoch | mittel | Durchstich und Tests **nur im Lab**; Least-Privilege-Credentials pro Worker; Rollback-Verhalten als Teil der Definition of Done; read-only, wo möglich | M6, M7, M9, SECURITY.md |
| R2 | Adapter-Aufwand unterschätzt: Abstraktion über NetBox/Infoblox (und ServiceNow/iTop) ist aufwändiger als geplant | mittel | mittel | Adapter-Interface früh und klein spezifizieren; kanonisches Datenmodell minimal halten; zweiten Adapter erst **nach** dem Durchstich bauen | M4, M5, ADR 0002 |
| R3 | Aerleon-Generator-Lücken: Fortinet-/Proxmox-Generatoren decken benötigte Features nicht ab | hoch | mittel | Früher PoC mit realen Regeln im Durchstich; Fallback: Post-Processing oder eigener Generator nur für die Lücke | M7 |
| R4 | Datenqualität im Kunden-IPAM: unvollständige/falsche SoT-Daten führen zu falschen Regeln | hoch | mittel | Validierung im SoT Gateway; Compliance-/Drift-Checks; Datenbereinigung als Teil des Kunden-Onboardings | M5, ADR 0002 |
| R5 | Laborumgebung fehlt oder kommt zu spät → Durchstich verzögert sich oder läuft gegen Produktion | hoch | mittel | Lab ist Exit-Kriterium von M6; virtuelle Appliances nutzen (FortiGate-VM, Proxmox-Testnode) | M6 |
| R6 | Scope Creep durch Multi-Kunden-Anspruch: alles soll sofort austauschbar/generisch sein | mittel | hoch | Pro Projekt genau **ein** Tool-Paar fixieren (M1); Austauschbarkeit über Interfaces sichern, nicht über parallele Implementierungen | M1, ADR 0002 |
| R7 | Secrets-Handling-Fehler: Klartext-Credentials in Repo, Logs oder Inventories | hoch | mittel | Ansible Vault ab M6 (vor dem ersten Zielsystem-Zugriff); Secret-Scanning in der CI; Wachstumspfad HashiCorp Vault | M6, SECURITY.md |
| R8 | Offene Contract-Entscheidung (ServiceNow vs. iTop vs. eigenes Modul) blockiert später Wizards/Integrationen | mittel | mittel | Entscheid pro Kunde in M1; Contracts wie das IPAM hinter ein Adapter-Interface legen | M1, ADR 0002 |
| R9 | Schlüsselpersonen-Risiko: Planungs- und Architekturwissen konzentriert sich auf wenige Köpfe | mittel | mittel | Entscheidungen konsequent als ADRs festhalten; README/CLAUDE.md/ROADMAP aktuell halten; generierte Doku (Documentation Engine) als Ziel beibehalten | M0, docs/adr/ |
| R10 | Vendor-Mapping unterschätzt (UC1): ACI-Contracts/EPGs und weitere Hersteller-Formate lassen sich schwerer auf ein einheitliches Regelmodell abbilden als geplant | mittel | hoch | ACI-Mapping vor M5 prototypisieren; pro zusätzlichem Hersteller eigenen Collector einplanen; nicht abbildbare Regeln sichtbar ausweisen statt verwerfen | M4, M5, M7, UC1 |

## Geschlossene Risiken

*(noch keine)*
