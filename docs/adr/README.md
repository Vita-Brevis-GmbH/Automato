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
| 0015 | Standorte virtuell (000, 001) und physisch (Ort-Code), n:m; NetBox-Site-Gruppen |
| 0016 | IPv4/IPv6 gleichnamig getrennt, IPv4/IPv6-Regelpaare, abgeleiteter Regelindex (Leistung) |
| 0017 | VMware vCenter lesen (nur lesende vSphere-Methoden), Portgruppe → EPG, VMs nach NetBox Virtualization |
| 0018 | Umgebung und Kunden: einmal lesen, je Kunde ein Ausschnitt über Selektoren (Entwurf) |
| 0019 | Topologie und Pfade: VDOM-Kontext, Routing/HA/L3Out lesen, Regel-Relevanz, Designer (Entwurf) |
| 0020 | Parallele Firewalls (Verbund): Regeln zusammenfassen, Abweichungen als Findings |
| 0021 | Verbindungen Ende zu Ende in der Applikations-Doku: Weg je Verbindung, Regeln je Hop, Abgleich Regel ↔ Weg (Entwurf) |
| 0022 | Aufbewahrung (Fristen für Läufe, Sync-Vorschauen, Audit) und getrennte DB-Rollen (Migration als Eigentümer, Betrieb ohne DDL) (Entwurf) |
| 0023 | TLS intern mit eigener CA: Postgres/Redis nur TLS, Orchestrator und Web-UI per HTTPS, Gegenstellen gegen die CA geprüft (Entwurf) |
| 0024 | Logische Zonen in Automato (Selektoren, längstes Präfix), Zonenmatrix mit Absicht je Paar, Zonenplan an Firewalls/VRFs (Entwurf) |
