# Architecture Decision Records (ADR)

Hier werden wichtige Architekturentscheidungen dokumentiert (Kontext, Entscheidung, Konsequenzen).
Format: eine Datei pro Entscheidung, z. B. `0001-monorepo-vs-multirepo.md`.

| ADR | Titel |
|---|---|
| 0001 | Monorepo als Startstruktur |
| 0002 | SoT Gateway Adapter-Pattern, Rule Store, Topology Service |
| 0003 | Grundaufbau – Tech-Stack und Service-Schnitt für UC1 |
| 0004 | Datenmodell UC1 (Ist-Regel-Dokumentation, mandantenfähig) |
| 0005 | Security-Review UC1 & Sicherheitsentscheide (M3) |
| 0006 | UC1-Durchstich: Erkenntnisse & Entscheide aus dem echten Lab (M8) |
| 0007 | AuthN/RBAC via Keycloak (dedizierter IdP, Produktions-Gate) |
| 0008 | Externer/dezentraler Collector (Agent) |
| 0009 | Geräte-Zugangsdaten zentral, Zuweisung Gerät → Agent |
| 0010 | Inventar, Referenz, Abgleich; Schreiben nur nach NetBox |
| 0011 | Selbst-Update der Collector-Agenten |
| 0012 | Applikation als eigenes Objekt (AP/EPG/Server, Vorschläge per Projektnummer) |
| 0013 | Produktions-Gate: mTLS-Agent-Gateway, signierte Updates, Sicherung, Update/Rückfall, R16-Prüfung |
| 0014 | Gemeinsames Betriebs-Cockpit mit Magister (Entwurf) |
