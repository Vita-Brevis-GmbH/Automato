# Use Case #1 – Firewall-Regel-Dokumentation (read-only)

Status: In M1 festgelegt (2026-07-21) · Definition of Done: **Entwurf, zu bestätigen**

## Beschreibung

Firewall-Regeln von diversen Firewalls (Start: FortiGate) und Cisco ACI **auslesen**
und zentral abspeichern (Dokumentation). Die Regel-Adressen werden Adressobjekten aus
**Infoblox DDI** zugeordnet; Service-Objekte (TCP/UDP) werden in einer eigenen
Datenbank normalisiert abgebildet. Ein Web-UI stellt die Regeln dar, filterbar nach
Systemen, Netzwerken und Zonen. Pro Applikation lassen sich Dokumente generieren.

Kein Schreibzugriff auf Zielsysteme – damit ist UC1 der risikoärmste Einstieg
(vgl. Risikoregister R1) und legt zugleich die Datenbasis für die spätere
Firewall-Regel-Automatisierung (ADR 0002).

## Umfang (in scope)

1. **Collector FortiGate** (REST API, read-only): Policies, Adress-/Service-Objekte,
   Zonen/Interfaces
2. **Collector Cisco ACI** (APIC REST API, read-only): Contracts, Filter, EPGs –
   Abbildung auf das normalisierte Regelmodell
3. **Normalisiertes Datenmodell + Speicher** (Rule & Service Store, Postgres):
   Regeln, Adress-Referenzen, Service-Objekte, Zonen, Quellsystem, Erfassungszeitpunkt
4. **Infoblox-DDI-Adapter** (SoT Gateway, lesend): Matching Regel-Adressen ↔
   Infoblox-Objekte (exakt + longest-prefix); nicht zuordenbare Adressen werden als
   „unmatched" ausgewiesen, nicht verworfen
5. **Service-Objekt-Datenbank**: Vendor-Definitionen dedupliziert auf kanonische
   Services (z. B. `tcp/443` einmal, egal wie FortiGate/ACI es nennen)
6. **Web-UI**: Regel-Tabelle mit Filtern nach System, Netzwerk und Zone;
   Detailansichten, Suche
7. **Dokumenten-Generierung** pro Applikation (Export, Format offen – siehe offene Fragen)

## Nicht im Umfang (spätere Use Cases)

- Schreibzugriff auf Firewalls/ACI (Regel-Provisionierung, Aerleon-Compiler,
  Topology-Service Primary/Backup)
- Schreiben nach Infoblox (Adressobjekte anlegen)
- Compliance-/Drift-**Bewertung** (UC1 erfasst und zeigt; Soll/Ist-Vergleich ist
  Kandidat für den nächsten Use Case)
- Contracts/ServiceNow/iTop

## Definition of Done (Entwurf – zu bestätigen)

- [ ] Regeln von mindestens 1 FortiGate und 1 ACI-Fabric werden automatisch
  (zyklisch oder auf Knopfdruck) eingelesen
- [ ] Regel-Adressen sind Infoblox-Objekten zugeordnet; „unmatched" ist als solches
  sichtbar (Ziel-Quote festlegen, z. B. ≥ 90 %)
- [ ] Service-Objekte sind dedupliziert als kanonische Services abgebildet
- [ ] Web-UI filtert nach System, Netzwerk und Zone
- [ ] Pro Applikation ist ein Dokument generierbar
- [ ] Read-only garantiert: Service-Accounts der Collectors haben keine Schreibrechte
  (Least Privilege, SECURITY.md)
- [ ] Collector-Läufe sind im Audit-Log nachvollziehbar (wann, was, Ergebnis)

## Architektur-Zuordnung

| UC1-Baustein | Plattform-Komponente |
|---|---|
| Collectors FortiGate/ACI | Documentation Engine bzw. read-only Worker |
| Normalisierte Regeln + Service-Objekte | Rule & Service Store |
| Infoblox-Zuordnung | SoT Gateway (`infoblox-adapter`, lesend) |
| Zonen-Filter | Topology Service (nur Zonen-Modell; Primary/Backup erst später) |
| Darstellung + Filter | Web UI |
| Applikations-Doku | Documentation Engine (Export) |

## Offene Fragen (vor M4/M5 klären)

1. **Welche „diversen Firewalls"** konkret, ausser FortiGate? Jeder weitere
   Hersteller ist ein eigener Collector (grober Zusatzaufwand: siehe Schätzung).
2. **Applikations-Zuordnung**: Wie werden Regeln Applikationen zugeordnet –
   Infoblox Extensible Attributes, Naming-Konventionen, manuelle Pflege im Store?
3. **Zonen-Normalisierung**: FortiGate-Zonen vs. ACI-EPGs/Tenants – gemeinsamer
   Zonen-Begriff für den Filter?
4. **Historisierung**: nur aktueller Stand, oder Änderungen über Zeit nachvollziehbar?
5. **Dokumentformat** für die Applikations-Doku: Markdown, PDF, Word?
6. **Lab-Zugang**: FortiGate-VM und Infoblox-Testinstanz (vNIOS) verfügbar?
   ACI: echte Fabric, Cisco-DevNet-Sandbox oder ACI-Simulator?

## Grobschätzung Entwicklungsaufwand

Basis: Entwicklung mit Claude Code, 1 Person reviewt/steuert/testet. Eine „Session"
ist ein fokussierter Arbeitsblock von ca. 2–3 Stunden.

| Baustein | Sessions | Aktive Stunden (ca.) |
|---|---|---|
| Grundaufbau (CI, docker-compose, Postgres, API-Skeleton) | 2–3 | 5–8 |
| Datenmodell + Backend-API | 3–4 | 8–12 |
| Collector FortiGate | 3–5 | 8–15 |
| Collector Cisco ACI (Contract-Mapping!) | 4–6 | 10–18 |
| Infoblox-Adapter + Adress-Matching | 3–5 | 8–15 |
| Service-Objekt-Normalisierung | 1–2 | 3–5 |
| Web-UI (Tabelle, Filter, Detail, Politur) | 5–8 | 12–20 |
| Applikations-Doku-Generierung | 2–4 | 5–10 |
| E2E-Tests gegen Lab, Bugfixing, Doku | 3–5 | 8–15 |
| **Total** | **26–42** | **≈ 65–120 h** |

Zuschläge: pro zusätzlichem Firewall-Hersteller +3–5 Sessions; Historisierung
+2–4 Sessions. Kalenderdauer hängt von verfügbarer Review-Zeit und Lab-Zugang ab,
nicht primär vom Claude-Kontingent (Einordnung siehe Projektnotizen/Chat).
