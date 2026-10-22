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
- Schreibpfad (spätere Use Cases): Worker mit **Runner-Abstraktion** – Ansible
  primär, Terraform/OpenTofu je Use Case (z. B. VM-Templates auf VMware/Proxmox);
  State-Backend-Entscheid als ADR vor dem ersten schreibenden Use Case
  (siehe ARCHITECTURE.md, Abschnitt Automatisierung)
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


## Verbindliche Vorgaben (immer beachten)

- **Nur nach NetBox schreiben, sonst strikt nur lesen.** FortiGate, APIC und Infoblox DDI
  werden ausschliesslich gelesen (Nur-Lese-Schutz im Code, `sot_gateway.readonly`). Der
  einzige Schreibweg ist der NetBox-Sync (Vorschau → Übernehmen, ADR 0010). Neue
  Schreibpfade nur mit eigener ADR und Freigabe.
- **Testsysteme sind produktiv:** nur Read-only-Konten, gelesene Daten wie Produktionsdaten
  behandeln; Negativtests gegen Geräte (z. B. R16 `--check --strict`) nur opt-in.
- **Keine Secrets** in Chat, Code, Tests, Doku oder Commits; `.env`, `pki/`-Ordner und
  `docker-compose.override.yml` bleiben gitignored.
- **Der Name des Referenzkunden wird nirgends hinterlegt** (auch nicht über Gerätenamen);
  Beispiele anonymisiert (`example.ch`, „Kunde A“).
- **Kein CSV-Import.** Wörterbücher (Namensprofile) sind in-App gepflegte Laufzeitdaten
  **ohne Funktionsrelevanz**: die Plattform muss ohne befüllte Wörterbücher voll funktionieren.
- **Open Source bevorzugt**, Adapter-Pattern für Kunden-Tools (siehe oben).
- Kommunikation mit dem Anwender auf Deutsch; Commits/PRs nur auf den vereinbarten Branch,
  PRs nur auf Wunsch.

## Arbeitsweise

- Tests: `uv run --package <orchestrator|collectors|sot-gateway> pytest <paket>/tests`;
  Lint `uv run ruff check .` + `uv run ruff format --check .`; Frontend in
  `web-ui/frontend`: `npm run lint` (tsc) + `npm run build`, UI-Tests `npm run e2e`
  (Playwright, Beispieldaten aus den Orchestrator-Tests). Übersicht und CI-Jobs:
  `docs/TESTS.md`. Frontend **nicht** mit prettier umformatieren (formatiert ganze Dateien um).
- Gegen Postgres: `AUTOMATO_TEST_DATABASE_URL=postgresql+psycopg://…` (Schema je Test neu);
  die CI fährt Migrationen hoch/runter und alle Orchestrator-Tests gegen Postgres 16.
- Jede Schemaänderung = Alembic-Migration (up/down gegen Postgres prüfen).
- **Version:** eine Plattform-Version in `VERSION`; setzen mit
  `python3 scripts/version.py set X.Y.Z` (CI prüft mit `check`), Änderungen in
  `CHANGELOG.md` (Patch = Korrektur, Minor = Funktion).
- Entscheide als ADR in `docs/adr/`, Risiken in `docs/RISKS.md`, Betrieb/Anwender in
  `docs/BETRIEB.md`. Der frühere ausführliche Statusverlauf (bis 0.4.2) steht in
  `docs/ENTWICKLUNGSVERLAUF.md`.

## Aktueller Stand (0.18.0)

UC1 „Firewall-Regel-Dokumentation (read-only)“ ist für Model 2 (FortiGate + NetBox) und
Model 1 (FortiGate + ACI + Infoblox) umgesetzt; Phase A ist gegen die echten Systeme abgenommen.
Produktiv erst später – jetzt wird erweitert und verbessert.

| Bereich | Stand | Verweise |
|---|---|---|
| Collector | FortiGate (**alle VDOMs** einzeln (`system/vdom`, `vdom=`; VDOM an Regel/Objekt/NAT/Interface), **Routing je VDOM** (RIB mit ECMP, BGP-Pfade inkl. Backup, Nachbarn, statische/Policy-Routen, SD-WAN) und **HA/FGSP** – Positivliste, keine Secrets (`fortigate/routing.py`), Policies inkl. ISDB/Identität/ZTNA/Negation, `policy6`, Objekte **IPv4/IPv6 getrennt** (gleiche Namen = verschiedene Objekte), Services mit Attributen, VIPs/IP-Pools, **Virtual Server** (Real Server, Methode, Health-Checks, `vip6`), Interfaces, UTM), APIC (Contracts tenant-bewusst per DN, externe EPGs, vzAny, Contract-Interfaces, **ESGs, Taboo, Preferred Groups**, Contract Master, Intra-EPG, Filter als Services, Inventar, **L3Outs** mit Border-Leafs/SVIs/Peers und **Routen der Border-Leafs** je VRF (`cisco_aci/routing.py`)), **VMware vCenter** (pyVmomi, nur lesende vSphere-Methoden: VMs mit NICs/IPs, Hosts, Cluster, Portgruppen), Infoblox DDI + NetBox als Referenz | Lesevertrag `docs/collectors-read-contract.md`, ADR 0010, 0017, 0019 |
| Agent | externer Windows-Agent (Pull, `--poll --watch 20`/`--check`), Geräte + Zugangsdaten zentral, Selbst-Update (**Läufe erst nach dem Update**, `next-run` `update`; **robust**: `update.lock`, Dateiprüfung + Wiederholung, Rückfall auf `packages\`, Selbstheilung in `run-agent.ps1`), **robuste Läufe** (Lesen wiederholt bei Timeout/503 `sot_gateway.readonly.get`; Upload wartet 10 min und wiederholt, Orchestrator quittiert abgeschlossene Läufe nur, Lauf gesperrt beim Einspielen), **signierte Updates**, mTLS über das **Agent-Gateway** | ADR 0008, 0009, 0011, 0013 |
| Store/Abgleich | Regeln, Objekte (eindeutig je VDOM und Familie), Inventar, Referenzstand, Findings als abgeleiteter Zustand (je Befund Satz + Hinweis, `store/finding_text.py`), IPAM-Namen ohne Präfix (`naming.server_name`), **VMs/Portgruppen** mit Zuordnung Portgruppe → EPG (`store/virtual.py`: Name `Tenant\|AP\|EPG`, ohne Präfixe, VLAN), NetBox-Sync mit Abschnitten Netzinventar, DDI-Spiegel, **Virtualisierung**; Einlesen mit Zwischenspeicher je Lauf | ADR 0004, 0010, 0016, 0017 |
| Routing | `topo_*` je Lauf ersetzt (Kontexte mit „nicht lesbar“, Routen mit Bereich für das längste Präfix, Peers, HA, Policy-Routen, L3Outs), `store/topology.lookup` (je Kontext + vorgehende Policy-Routen), **Weg Hop für Hop** (`store/paths.py`: Knoten = VDOM bzw. Firewall nur mit root bzw. ACI-VRF über alle Border-Leafs; Start aus Quelle oder gewählt; Policy-Route sicher vor RIB, ECMP, **VRF des Eingangs** (FortiGate `attrs.vrf`, Management eigene Tabelle), Nachbar nur über den Next-Hop-Besitzer (kein Raten über Transfernetze); `GET /routing/trace`), **Anbindungen nach aussen** (`topo_exit`, von Hand: Internet/MPLS/WAN über Next-Hop bzw. Interface/Zone, Netze, manuelle Routen zu einem Knoten; `/routing/exits`, Operator), Bezeichnung „fw“ / „fw · VDOM x“ / „leaf · VRF T:V“, IPs mit IPAM-Namen (`Graph.resolve`: Name, sonst Netz mit Beschreibung), `vdoms` je Quellsystem (`GET /systems`, UI `useVdomShown`) | ADR 0019 (Stufe 2, Stufe 3 erste Fassung) |
| Parallele Firewalls | **Verbund** (`source_system.parallel_group`, Vorschlag über FGSP bzw. gleiche Policies, `POST /systems/parallel-group`), Vergleich `store/parallel.py` (Policy-ID im VDOM, sonst Name; Inhalt ohne uuid/Kommentar): Zwillinge `rule_index.twin_of` aus Listen/Kennzahlen/Doku ausgeblendet (`rules_store.not_twin`), Findings `parallel_rule_mismatch`/`_missing`/`parallel_object_mismatch` | ADR 0020 |
| Regelindex/Suche/NAT | **abgeleiteter Regelindex** (`store/rule_index.py`: Suchtext, Listenzeile, Objekt-Nutzung direkt/über Gruppen, IP-Bereiche) – Suche über alles per SQL, Regel-Zahl bei Objekten, Filter `object`/`object_id`/`nat`/`family`; **IPv4/IPv6-Paare** (`store/rule_pairs.py`, nur Markierung, von Hand bestätigen/ablehnen: `RulePairDecision` an der Policy-ID); **VIP-Ziele** als Objekte (`vip_target`, `vip_of`; zählen die Regeln der VIP); NAT mit DNS, Server, Regeln; Messskript `scripts/bench_large_ruleset.py` | ADR 0016 |
| Applikationen | eigenes Objekt mit AP/EPG/Server, externem/internem Namen, Zweck Dev/Test/Prod, **Vorschläge per Projektnummer**; **Klick öffnet die Doku** (`store/applications.build_doc`): Übersicht, alle VMs, EPGs (ganzer ACI-Pfad), Regeln wie in der Regel-Ansicht mit Treffern je System (Richtung, Bezug, spezifisch/unspezifisch: Subnetz, any, vzAny, Preferred Group), Filter, Erklärung im Detail, Findings, 10 Kategorien für Markdown/Druck; Doku je System; Regeln je Subnetz; publizierte Dienste (VIPs) | ADR 0012 (Ergänzungen 0.10.0, 0.14.0), 0017 |
| Standorte | **virtuell** (000, 001; in Netz-/Cluster-Namen) und **physisch** (Ort-Code, z. B. chzrh1), n:m-Zuordnung; Vorschlag aus Namen (`store/sites.py`), NetBox-Site-Gruppen; VMs ohne Standort im Namen | ADR 0015 |
| Betrieb | **Zeitplan je Quellsystem** (Cron, Europe/Zurich), **Alle einlesen** (`POST /jobs/collect-all`), **Ansicht Läufe** (alle, seitenweise, Filter; Übersicht zeigt alle offenen), hängende Läufe/Syncs beendet bzw. abbrechbar, **Betriebszustand** (`store/health.py`, `GET /system/health`), Agent-Log + Update-Status, **System-Seite**, Sicherung/Wiederherstellung (age), `deploy/update.sh` mit Rückfall | ADR 0013, `deploy/backup/`, `docs/BETRIEB.md` |
| Sicherheit | Keycloak (OIDC/PKCE, OTP, SAML-Vorlage), Rollen viewer/operator/admin, Mandanten-Scoping, Audit mit Allowlist (+ optional verschlüsselt), Secrets-Provider, Zugangsdaten Fernet, fail-closed Defaults | ADR 0005, 0007, 0013 |
| Web-UI | alle Ansichten nach Mockup, seitenweise Listen mit Gesamtzahl; Ports kompakt mit Detail, Portgruppen-Detail (EPG, Subnetze, VMs) | `web-ui/frontend/` |

Tests: orchestrator 241, collectors 109, sot-gateway 15, UI (Playwright) 49; dazu die CI gegen
Postgres inkl. `alembic check`, Image-Builds und nächtlicher Lauf (`docs/TESTS.md`).

## Offen / nächste Schritte

Reihenfolge laut Anwender (ROADMAP „Nächste Arbeiten“); 0.11–0.16 sind gegen die echten
Systeme abgenommen, 0.17.0 ebenfalls; 0.17.1 (robuste Läufe, VRF, keine Schleifen) und
0.18.0 (parallele Firewalls mit Findings, Anbindungen nach aussen) stehen zur Abnahme an:

- **ADR 0018** (Umgebung und Kunden, Entwurf): eine Instanz je Umgebung; Quellsysteme der
  Umgebung, vollständig gelesen; Kunden als Ausschnitt über **Selektoren** (ACI-Tenant/VRF/
  AP/EPG, VDOM/Zone/Interface, Präfixe, ein- und ausschliessend), Zugehörigkeit abgeleitet;
  später Kunden-Benutzer (`customer_ids`) mit serverseitiger Einschränkung.
- **ADR 0019** (Topologie und Pfade, Entwurf): VDOM-Kontext (0.15.0) und Routing/HA/L3Out
  lesen (0.16.0) umgesetzt und abgenommen; Stufe 3 erste Fassung (Weg Hop für Hop, 0.17.0,
  Test offen). Weiter mit **Regel-Relevanz** (Zonen-Paar je Hop → Regeln, Regeln/Doku nur wo
  der Verkehr durchkommt), Session-Sync-Paar als ein Hop, Inter-VDOM-Links, „Internet“
  zusammengefasst; dann Designer; später Switches/Verkabelung (L1/L2-Sicht).
- Danach: Applikationen (Regeln direkt zuordnen, Abhängigkeiten), produktiv schalten mit den
  R14-Massnahmen (ADR 0013, RISKS), Cockpit (ADR 0014), Import-Wizard (E3); Schreibpfad (E4)
  nur mit eigener ADR.
