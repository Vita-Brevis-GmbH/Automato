# Use Case #1 – Firewall-Regel-Dokumentation (read-only)

Status: In M1 festgelegt (2026-07-21) · Datenmodell: **ADR 0004** · Security: **ADR 0005**
· Definition of Done: **bestätigt (2026-07-22)**

## Beschreibung

Firewall-Regeln von FortiGate (und später Cisco ACI) **auslesen** und zentral
abspeichern (Dokumentation). Die Regel-Adressen werden Adressobjekten aus dem aktiven
IPAM (NetBox bzw. Infoblox, je Mandant) zugeordnet; Service-Objekte werden dedupliziert
abgebildet. Ein Web-UI stellt die Regeln dar, filterbar nach Systemen, Netzwerken und
Zonen. Pro Applikation lassen sich Dokumente generieren.

Kein Schreibzugriff auf Zielsysteme – damit ist UC1 der risikoärmste Einstieg
(vgl. Risikoregister R1) und legt zugleich die Datenbasis für die spätere
Firewall-Regel-Automatisierung (ADR 0002).

## Kundenmodelle & Umsetzungsreihenfolge

Zwei reale Umgebungen; **Model 2 wird zuerst umgesetzt** (einfacher, kein ACI → meidet
Risiko R10 im Durchstich), Model 1 danach. Details und Datenmodell: **ADR 0004**.

- **Model 2 (zuerst):** FortiGate + Switches (nur VLAN/L3), **NetBox** als IPAM,
  DNS = Active Directory. Einfache Struktur, VPN mit statischem/dynamischem Routing.
- **Model 1 (danach):** FortiGate + Cisco ACI + Switches, **Infoblox** als IPAM/DNS,
  ACI mit Microsegmentierung und mehreren Security-Tenants, strikte Namenskonvention,
  mehrere Firewalls in Serie. Contracts zu den Firewalls sind vzAny – die Enforcement
  macht die FortiGate; die dokumentationsrelevanten ACI-Regeln sind die
  East-West-Contracts zwischen EPGs.

Switches sind **keine Regelquelle** (nur VLAN/L3-Switching); Switch-ACLs sind selten
und bleiben für später offen gehalten. Automato ist **mandantenfähig** (ein Mandant
trägt seine SoT-Bindung; cross-tenant-Überblick + per-tenant-Sicht).

## Umfang (in scope)

1. **Collector FortiGate** (REST API, read-only): Policies, Adress-/Service-Objekte,
   Zonen
2. **Collector Cisco ACI** (APIC REST API, read-only, **Model 1 / Phase 2**): Contracts,
   Filter, EPGs; **Autorität für IP↔EPG** (gelernte Endpunkte, akkumulierend gegen das
   30-min-Aging – siehe ADR 0004)
3. **Datenmodell + Speicher** (Rule & Service Store, Postgres): mandantenfähig,
   eine `rule`-Tabelle mit polymorphen Endpunkten, Zonen, Services, NAT, Findings –
   Schema in **ADR 0004**
4. **SoT-Adapter** (SoT Gateway, lesend): Matching Regel-Adressen ↔ IPAM-Objekte
   (exakt + longest-prefix). **NetBox zuerst** (Model 2), Infoblox in Phase 2.
   Nicht zuordenbare Adressen werden als Finding ausgewiesen, nicht verworfen
5. **Service-Objekt-Datenbank**: dedupliziert auf kanonische Services (`tcp/443`,
   ICMP/ICMPv6, IP-Protokolle; Dual-Stack)
6. **Web-UI**: Regel-Tabelle mit Filtern nach System, Netzwerk und Zone; Findings-Sicht
   (Datenqualität); bei FortiGate-Regeln die EPG-Zugehörigkeit der Hosts eingeblendet
7. **Datenqualität/Reconciliation**: Abweichungen (ungültige/fehlende Namen, IP auf
   mehreren Objekten, fehlende IPAM-Objekte, Präfix↔Typ-Konflikt) sichtbar machen und
   quittierbar – schreibende Bereinigung erst im Schreibpfad
8. **Dokumenten-Generierung** pro Applikation (Export, Format offen – siehe offene Fragen)

## Nicht im Umfang (spätere Use Cases)

- Schreibzugriff auf Firewalls/ACI (Regel-Provisionierung, Aerleon-Compiler,
  Topology-Service Primary/Backup)
- Schreiben nach Infoblox (Adressobjekte anlegen)
- Compliance-/Drift-**Bewertung** (UC1 erfasst und zeigt; Soll/Ist-Vergleich ist
  Kandidat für den nächsten Use Case)
- Contracts/ServiceNow/iTop

## Definition of Done (bestätigt)

Die DoD gilt für den **Abschluss von UC1 = Model 2** (FortiGate + NetBox, M9). Model 1
(ACI + Infoblox + strikte Namenskonvention + Firewalls in Serie) ist eine **Erweiterung
von UC1** und durchläuft danach denselben Zyklus (M5-Phase 2 / M7-Phase 2).

- [x] Regeln von mindestens 1 (Lab-)FortiGate werden automatisch (zyklisch oder auf
  Knopfdruck) eingelesen und normalisiert gespeichert — *75 Policies, `automato collect`/`lab`.*
- [~] Regel-Adressen werden gegen NetBox gematcht; **jedes nicht auflösbare Objekt ist
  als Finding sichtbar und triage-bar** (open → acknowledged/resolved/ignored).
  Die Ziel-Matching-Quote als Kennzahl wird **nach dem ersten realen Lauf** festgelegt
  (nicht vorab hart gesetzt – Datenqualität ist erst dann bekannt, R4) — *Matching ✓
  (nach Import unmatched 0), Findings sichtbar ✓; **Status-Triage (open→…) ist eine
  Schreibaktion und folgt mit dem Schreibpfad** — Findings werden derzeit pro Lauf neu
  aufgebaut (ADR 0006).*
- [x] Service-Objekte sind dedupliziert als kanonische Services abgebildet
- [x] Web-UI filtert nach System, Netzwerk und Zone; Findings-Sicht vorhanden —
  *Filter System/Zone/Aktion + Freitext/IP-Suche (Netzwerk); Reiter Findings.*
- [x] Pro Applikation ist ein Dokument generierbar — *Reiter Applikationen, Markdown-Export.*
- [x] Read-only garantiert: Service-Accounts der Collectors haben keine Schreibrechte
  (Least Privilege, SECURITY.md / ADR 0005) — *FortiGate read-only, NetBox-Collector-Token
  `write_enabled=false` + nur view-ObjectPermission; der NetBox-Import ist ein separates
  Admin-Werkzeug, kein Collector-Pfad.*
- [~] Collector-Läufe und Finding-Statusänderungen sind im Audit-Log nachvollziehbar —
  *Collector-Läufe (queued) + Ergebnis-Eingang auditiert; Finding-Statusänderungen gibt es
  noch nicht (Schreibpfad). Lese-Zugriffe noch nicht auditiert.*
- [~] Security-Check für den Model-2-Scope bestanden (ADR 0005) — *siehe „M9 – Abschluss"
  unten: für Lab-/Review-Scope bestanden; **Produktions-Gate: AuthN/RBAC vor der öffentlichen
  Read-API** (fail-closed erzwungen ausserhalb `dev`).*

## M9 – Abschluss: Security-/Hardening-Check & offene Gates

Stand der 8 SECURITY.md-Prinzipien (ADR 0005) für den Model-2-Scope:

| Prinzip | Status | Anmerkung |
|---|---|---|
| Least Privilege | ✅ | Collector-Creds rein lesend (FortiGate + NetBox read-only Token/Permission). |
| Secrets-Handling | ✅ | `.env` nie committet, gitleaks, Fail-closed-Default-Token-Check. |
| Fail-closed Defaults | ✅ | Default-Token **und** unauthentifizierte Read-API ausserhalb `dev` abgelehnt. |
| Mandanten-Scoping | ⚠️ | `tenant_id` in Store-Queries vorhanden; öffentliche API erlaubt noch `tenant_id=None` (alle) → mit RBAC schliessen. |
| Audit-Log | ⚠️ | Läufe + Ergebnis-Eingang auditiert; Lese-Zugriffe und Finding-Triage offen. |
| AuthN/RBAC | ❌ (Gate) | Öffentliche Read-API (`/rules`,`/findings`,`/nat`,`/applications`) **nicht authentifiziert** → Folge-ADR vor Produktion; per `AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API` fail-closed ausserhalb `dev`. |
| Transport/at-rest-Verschlüsselung | ❌ (geplant) | mTLS/at-rest als Folge-ADR (R14). |
| Netzwerksegmentierung | ❌ (geplant) | Folge-ADR; bis dahin Read-API nur in vertrauenswürdigem Netz. |

**Fazit:** UC1 Model 2 ist **funktional vollständig und Lab-/Review-reif**. Die produktive
Freigabe erfordert den **AuthN/RBAC-Gate** (und optional den Finding-Triage-Schreibpfad);
beide sind in ADR 0005/0006 bewusst als Folgearbeiten terminiert. Betrieb/Bedienung:
`docs/BETRIEB.md`.

## Architektur-Zuordnung

| UC1-Baustein | Plattform-Komponente |
|---|---|
| Collectors FortiGate/ACI | Documentation Engine bzw. read-only Worker |
| Regeln, Objekte, Services, NAT, Findings | Rule & Service Store (ADR 0004) |
| IPAM-Zuordnung | SoT Gateway (`netbox-adapter` zuerst, `infoblox-adapter` Phase 2) |
| Zonen-Filter | Zone am Subnetz, aus dem SoT gelesen (Topology Service nur Zonen-Modell) |
| Darstellung + Filter + Findings | Web UI |
| Applikations-Doku | Documentation Engine (Export) |

## Geklärte Punkte (aus den Planungsrunden → ADR 0004)

- **Zonen-Normalisierung:** Zone ist ein Attribut am Subnetz, im SoT gepflegt und von
  Automato nur gelesen. Eine Zone umfasst mehrere Subnetze (DMZ, Server, Transfer,
  Internet, Edge, Office, Gäste, Partner …). ACI-L3Outs (5, zweckgebunden) entsprechen
  Zonen von der ACI-Seite.
- **Applikations-Zuordnung:** vorgesehen (`application` am Objekt), Quelle später über
  Namenskonvention bzw. die im Aufbau befindliche CMDB. ServiceNow ist heute **nicht**
  SoT.
- **Namenskonvention** (Model 1) wird geparst: Präfixe `H_`/`N_`/`VIP_`/`R_`/`H-EXT_`
  → Objektklasse, FQDN → Domain (löst Mehr-AD-Situation). `H-EXT_` = bewusst extern
  (kein IPAM-Match erwartet).
- **Historisierung:** aktueller Stand + `last_seen`; volle Änderungshistorie vertagt.
  Ausnahme: gelernte ACI-Endpunkte werden akkumuliert (30-min-Aging).

## Offene Fragen (Rest)

1. **Definition of Done bestätigen** (Ziel-Matching-Quote festlegen).
2. **Dokumentformat** für die Applikations-Doku: Markdown, PDF, Word?
3. **Lab-Zugang:** Model 2 zuerst → FortiGate-VM + NetBox-Testinstanz. Model 1 (Phase 2):
   Infoblox-Testinstanz (vNIOS) und ACI via echte Fabric / Cisco-DevNet-Sandbox /
   ACI-Simulator.

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
