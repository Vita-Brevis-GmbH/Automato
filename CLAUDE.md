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

## Aktueller Stand (0.38.0)

UC1 „Firewall-Regel-Dokumentation (read-only)“ ist für Model 2 (FortiGate + NetBox) und
Model 1 (FortiGate + ACI + Infoblox) umgesetzt; Phase A ist gegen die echten Systeme abgenommen.
Produktiv erst später – jetzt wird erweitert und verbessert.

| Bereich | Stand | Verweise |
|---|---|---|
| Collector | FortiGate (**alle VDOMs** einzeln (`system/vdom`, `vdom=`; VDOM an Regel/Objekt/NAT/Interface), **Routing je VDOM** (RIB mit ECMP, BGP-Pfade inkl. Backup, Nachbarn, statische/Policy-Routen, SD-WAN) und **HA/FGSP** – Positivliste, keine Secrets (`fortigate/routing.py`), Policies inkl. ISDB/Identität/ZTNA/Negation, `policy6`, Objekte **IPv4/IPv6 getrennt** (gleiche Namen = verschiedene Objekte), Services mit Attributen, VIPs/IP-Pools, **Virtual Server** (Real Server, Methode, Health-Checks, `vip6`), Interfaces, UTM), APIC (Contracts tenant-bewusst per DN, externe EPGs, vzAny, Contract-Interfaces, **ESGs, Taboo, Preferred Groups**, Contract Master, Intra-EPG, Filter als Services, Inventar, **L3Outs** mit Border-Leafs/SVIs/Peers und **Routen der Border-Leafs** je VRF (`cisco_aci/routing.py`)), **VMware vCenter** (pyVmomi, nur lesende vSphere-Methoden: VMs mit NICs/IPs, Hosts, Cluster, Portgruppen), Infoblox DDI + NetBox als Referenz | Lesevertrag `docs/collectors-read-contract.md`, ADR 0010, 0017, 0019 |
| Agent | externer Windows-Agent (Pull, `--poll --watch 20`/`--check`), Geräte + Zugangsdaten zentral, Selbst-Update (**Läufe erst nach dem Update**, `next-run` `update`; **robust**: `update.lock`, Dateiprüfung + Wiederholung, Rückfall auf `packages\`, Selbstheilung in `run-agent.ps1`), **robuste Läufe** (Lesen wiederholt bei Timeout/503 `sot_gateway.readonly.get`; Upload wartet 10 min und wiederholt, Orchestrator quittiert abgeschlossene Läufe nur, Lauf gesperrt beim Einspielen), **signierte Updates**, mTLS über das **Agent-Gateway** | ADR 0008, 0009, 0011, 0013 |
| Store/Abgleich | Regeln, Objekte (eindeutig je VDOM und Familie), Inventar, Referenzstand, Findings als abgeleiteter Zustand (je Befund Satz + Hinweis, `store/finding_text.py`), IPAM-Namen ohne Präfix (`naming.server_name`), **VMs/Portgruppen** mit Zuordnung Portgruppe → EPG (`store/virtual.py`: Name `Tenant\|AP\|EPG`, ohne Präfixe, VLAN), NetBox-Sync mit Abschnitten Netzinventar, DDI-Spiegel, **Virtualisierung**; Einlesen mit Zwischenspeicher je Lauf | ADR 0004, 0010, 0016, 0017 |
| Routing | `topo_*` je Lauf ersetzt (Kontexte mit „nicht lesbar“, Routen mit Bereich für das längste Präfix, Peers, HA, Policy-Routen, L3Outs), `store/topology.lookup` (je Kontext + vorgehende Policy-Routen), **Weg Hop für Hop** (`store/paths.py`: Knoten = VDOM bzw. Firewall nur mit root bzw. ACI-VRF über alle Border-Leafs; Start aus Quelle oder gewählt; Policy-Route sicher vor RIB, ECMP, **VRF des Eingangs** (FortiGate `attrs.vrf`, Management eigene Tabelle), Nachbar nur über den Next-Hop-Besitzer (kein Raten über Transfernetze), **Inter-VDOM-Links** (Route ohne Next-Hop-IP über `<name>0`/`<name>1` bzw. VLANs auf NPU-vlinks → VDOM am anderen Ende, `Graph.vdom_peer`); **redundante Wege**: aktiv/ECMP, **Verbund als ein Hop** (`_merge_parallel`: Wege nur über Firewalls eines Verbunds = ein Weg, `hop.parts` je Firewall, verschiedene Urteile = abhängig) plus Backup-Wege (eine Abweichung über BGP-Pfad bzw. statische Route gleichen Präfixes, höchstens `slack` Hops länger); `GET /routing/trace`), **Anbindungen nach aussen** (`topo_exit`, von Hand: Internet/MPLS/WAN über Next-Hop bzw. Interface/Zone, Netze, manuelle Routen zu einem Knoten, für mehrere Firewalls `source_system_ids`; `/routing/exits` mit Zustand, Operator), **erkannte Anbindungen** (`store/exits.py`: Router ohne gelesenes Gerät aus RIB/BGP/statisch/Nachbarn, aktiv/Backup/AS, zusammengefasst über gemeinsame Router, ECMP, Zone, AS je Firewall bzw. Verbund/FGSP; `GET /routing/exits/suggestions`; ausblenden `topo_exit_dismissal`, `POST /routing/exits/dismiss[/undo]`), Länge je Weg `extra`, **Regeln auf dem Weg** (`store/policy_check.py`: je FortiGate-Hop die Policies in Gerätereihenfolge – Ein-/Ausgang Interface/Zone, Quelle, Ziel, Dienst `service=`; erste sichere entscheidet, nicht Prüfbares = „möglich“ → abhängig, sonst implizit gesperrt; `hop.rules`, `path.policy`; **ACI je VRF-Hop** seit 0.35.0 `store/aci_check.py`: Quelle/Ziel → EPG im Fabric bzw. externe EPG, gleiche EPG, Taboo zuerst, Contract Consumer → Provider direkt/vzAny/ESG/Preferred Group mit Filter, sonst gesperrt), **Weg-Suche als eigene Ansicht** (0.35.0, `#wege`, `Paths.tsx`; Quelle/Ziel als Name: `store/resolve.py`, `GET /routing/resolve` aus IPAM, Adressobjekten, VMs, seit 0.38.0 auch **Netze** (IPAM-Prefix, Netz-Objekt, `InvSubnet`, CIDR; `ip` = erste Adresse ohne Gerät) seitenweise mit `total`/`counts` je Art host/net; Dienst auch als Service-Objekt/ACI-Filter `FT_https`), **NAT auf dem Weg** (`Graph.dnat`: VIP → interne IP ab dem Hop, Port-Weiterleitung, Virtual Server = erster Real Server; SNAT der erlaubenden Policy = Quelle danach; `hop.nat`/`dst`/`snat`), **Relevanz in der Doku** (`store/relevance.py`: je Firewall-Regel und System Route der Firewall zum System vs. Ein-/Ausgang der Regel → possible/impossible/unknown; unmögliche nicht in Doku je System (`impossible`, eingeklappt), Applikations-Doku (seit 0.34.0 gar nicht, nur gezählt), Kennzahlen, Export; **Gegenstelle any** = `Relevance.behind`: Anbindung, Default-Route bzw. Netze hinter dem Interface der Regel, Internet-Anbindung → Kategorie 1/2; **Regel-Liste**: `GET /rules?q=<IP>` mit `relevance`, `X-Impossible-Count`, `routable=true`; **ACI-Contracts über externe EPGs** (`store/aci_relevance.py`: Scope `import-security` aus `l3extSubnet.scope`/`epg_subnet.scope`, längstes Subnetz je VRF, IP im Fabric, 0.0.0.0/0 in mehreren L3Outs nach dem Routing der VRF, sonst unbekannt; Doku je System hinter L3Out, Regel-Liste)), Bezeichnung „fw“ / „fw · VDOM x“ / „leaf · VRF T:V“, IPs mit IPAM-Namen (`Graph.resolve`: Name, sonst Netz mit Beschreibung), `vdoms` je Quellsystem (`GET /systems`, UI `useVdomShown`) | ADR 0019 (Stufe 2, Stufe 3 erste Fassung) |
| Kunden | **Ausschnitt der Umgebung** (ADR 0018 Schritt 1): `customer` (Kürzel, `shared`), `customer_selector` (aci_tenant/vrf/ap/epg/bd, fw_vdom/zone/interface je Quellsystem, prefix, code, **ipam_tenant/ipam_vrf/ipam_tag** aus dem Referenzstand; `exclude`), `customer_member` abgeleitet (`store/customers.derive` nach jedem Lauf, beim Speichern, nach Löschen: EPGs, Netze inkl. EPG-Subnetze/Interface-Netze, Werte-Objekte im Netz, Contracts über EPG, Firewall-Regeln über VDOM/Zone/Interface bzw. Netz ohne any, VMs über Portgruppe/IP/Kürzel, NAT über interne/externe IP, Findings über ihr Subjekt); `/customers` mit `preview`, `suggestions`, `unassigned`, `members`; UI Administration → Kunden; **Kunden-Filter** (`customer_id` an allen Doku-Listen, `store/customer_scope.View`: geteilter Teil nur für EPGs/Netze/Objekte, Regeln/NAT/Findings/VMs nur eigene, Umschalter „Kunde“ oben); **Kunden-Benutzer** (Keycloak-Rolle `customer` bzw. Claim `customer_ids` → `Principal.customer_ids`, nie Admin; **Freigabeliste** `auth/customer_routes.py` in `require_role`, alles andere 403; je Endpunkt Ausschnitt, Details ausserhalb 404, Regel-Zahlen nur eigene, `api/customer_filter.py`; `GET /systems/labels`; Lese-Audit immer; dev: `X-Dev-Customers` bzw. `localStorage automato_dev_customer`) | ADR 0018 |
| Parallele Firewalls | **Verbund** (`source_system.parallel_group`, Vorschlag über FGSP bzw. gleiche Policies, `POST /systems/parallel-group`; **ablehnen/auflösen** → `parallel_rejection`, nicht mehr vorgeschlagen, `POST /systems/parallel-reject[/undo]`), Vergleich `store/parallel.py` (Policy-ID im VDOM, sonst Name; Inhalt ohne uuid/Kommentar): Zwillinge `rule_index.twin_of` aus Listen/Kennzahlen/Doku ausgeblendet (`rules_store.not_twin`), Findings `parallel_rule_mismatch`/`_missing`/`parallel_object_mismatch` | ADR 0020 |
| Regelindex/Suche/NAT | **abgeleiteter Regelindex** (`store/rule_index.py`: Suchtext, Listenzeile, Objekt-Nutzung direkt/über Gruppen, IP-Bereiche) – Suche über alles per SQL, Regel-Zahl bei Objekten, Filter `object`/`object_id`/`nat`/`family`; **IPv4/IPv6-Paare** (`store/rule_pairs.py`, nur Markierung, von Hand bestätigen/ablehnen: `RulePairDecision` an der Policy-ID; seit 0.36.0 in der Liste nebeneinander, `rules_store.paired_order`); Dienste mit Objektnamen (`rule_view.service_labels`, ACI-Filter ohne Tenant); **Objekte** (0.36.0): Typ-Filter mit Anzahl (`obj_type`, dynamische SDN-Objekte mit `sdn`), Standardobjekte des Herstellers (`naming.predefined_service`: FortiOS, ACI `common`) zuunterst/ausblendbar + Befund `predefined_service_used`, `object_id`-Filter für „Objekt öffnen“; Service-Präfix `FT_` in `object_prefix` mit Typ `service` (Migration `0045`), Projektnummern ausgeblendet; **VIP-Ziele** als Objekte (`vip_target`, `vip_of`; zählen die Regeln der VIP); NAT mit DNS, Server, Regeln; Messskript `scripts/bench_large_ruleset.py` | ADR 0016 |
| Applikationen | eigenes Objekt mit AP/EPG/Server, externem/internem Namen, Zweck Dev/Test/Prod, **Vorschläge per Projektnummer**; Findings mit `epgs` (0.38.0, Filter Tenant/AP/EPG); Kategorien seit 0.34.0 mit Aktion, Gerät, Link zur Regel, IPv4/IPv6-Paar als eine Zeile, **unspezifische Regeln nur als Regel** (0.37.0: any/Netz/vzAny → Objekt der Regel, Systeme in `_systems` bzw. Knopf „▸ N Systeme“), **NAT nur aus der Regel** (`TenantData.rule_nat`: SNAT-Pool bzw. Interface-IP, VIP der Regel, nicht bei Deny); **Klick öffnet die Doku** (`store/applications.build_doc`): Übersicht, alle VMs, EPGs (ganzer ACI-Pfad), Regeln wie in der Regel-Ansicht mit Treffern je System (Richtung, Bezug, spezifisch/unspezifisch: Subnetz, any, vzAny, Preferred Group), Filter, Erklärung im Detail, Findings, 10 Kategorien für Markdown/Druck; Doku je System; Regeln je Subnetz; publizierte Dienste (VIPs) | ADR 0012 (Ergänzungen 0.10.0, 0.14.0), 0017 |
| Standorte | **virtuell** (000, 001; in Netz-/Cluster-Namen) und **physisch** (Ort-Code, z. B. chzrh1), n:m-Zuordnung; Vorschlag aus Namen (`store/sites.py`), NetBox-Site-Gruppen; VMs ohne Standort im Namen | ADR 0015 |
| Betrieb | **Verbindung prüfen** (`POST /systems/{id}/check`, `store/reachability.py`: Name, TCP, NetBox-Status), **Zeitplan je Quellsystem** (Cron, Europe/Zurich), **Alle einlesen** (`POST /jobs/collect-all`), **Ansicht Läufe** (alle, seitenweise, Filter; Übersicht zeigt alle offenen), hängende Läufe/Syncs beendet bzw. abbrechbar, **Betriebszustand** (`store/health.py`, `GET /system/health`), Agent-Log + Update-Status, **System-Seite**, Sicherung/Wiederherstellung (age), `deploy/update.sh` mit Rückfall | ADR 0013, `deploy/backup/`, `docs/BETRIEB.md` |
| Sicherheit | Keycloak (OIDC/PKCE, OTP, SAML-Vorlage), Rollen viewer/operator/admin + customer (Kunden-Benutzer fail-closed), Mandanten-Scoping, Audit mit Allowlist (+ optional verschlüsselt), Secrets-Provider, Zugangsdaten Fernet, fail-closed Defaults | ADR 0005, 0007, 0013 |
| Web-UI | alle Ansichten nach Mockup, seitenweise Listen mit Gesamtzahl; Ports kompakt mit Detail, Portgruppen-Detail (EPG, Subnetze, VMs) | `web-ui/frontend/` |

Tests: orchestrator 284, collectors 114, sot-gateway 15, UI (Playwright) 70; dazu die CI gegen
Postgres inkl. `alembic check`, Image-Builds und nächtlicher Lauf (`docs/TESTS.md`).

## Offen / nächste Schritte

Reihenfolge laut Anwender (ROADMAP „Nächste Arbeiten“); 0.11–0.16 sind gegen die echten
Systeme abgenommen, 0.17.x und 0.18.0 ebenfalls (Regeln paralleler Firewalls einmal, keine
Schleifen); 0.19.0 (Verbund ablehnen, redundante Wege) und 0.20.0/0.21.0 (erkannte Anbindungen
mit Redundanz und Ausblenden, Anbindung für mehrere Firewalls, Länge der Wege) sowie 0.22.0/0.23.0
(Regeln und NAT auf dem Weg) und 0.24.0–0.26.0 (Relevanz in Doku und Regel-Liste, Gegenstelle any)
sowie 0.27.0 (Inter-VDOM-Links), 0.28.0 (ACI-Contracts über externe EPGs) und 0.29.0 (Verbund als ein Hop) sowie 0.30.0–0.33.0 (Kunden über Selektoren, IPAM-Merkmale, Kunden-Filter, Kunden-Benutzer) stehen zur Abnahme an; 0.34.0 enthält die Befunde aus dem Test von 0.33.0, 0.35.0 die Weg-Suche als eigene Ansicht (Namen, Contracts) und Dienste mit Objektnamen, 0.36.0 Objekte (Typ, Standardobjekte), Namenskonvention (FT_ pflegbar, ohne Projektnummern), Paare nebeneinander und den Kunden-Editor, 0.37.0 offene Regeln (any, grosse Netze) in der Applikations-Doku nur als Regel, 0.38.0 Netze in der Weg-Suche (gegliedert, vollständig), Paare im Rahmen (`pair-a`/`pair-b`), Vorschläge unter der Applikationsliste und Findings nach EPG:

- **ADR 0018** (Umgebung und Kunden, Entwurf): eine Instanz je Umgebung; Quellsysteme der
  Umgebung, vollständig gelesen; Kunden als Ausschnitt über **Selektoren** (ACI-Tenant/VRF/
  AP/EPG, VDOM/Zone/Interface, Präfixe, ein- und ausschliessend), Zugehörigkeit abgeleitet –
  **Schritt 1 in 0.30.0/0.31.0**, Kunden-Filter (Schritt 2) in 0.32.0, Kunden-Benutzer
  (`customer_ids`, Freigabeliste, serverseitige Einschränkung, Schritt 3) in 0.33.0. Offen:
  Relevanz nach Pfad für die Zugehörigkeit, Kunden-Benutzer in der Oberfläche verwalten.
- **ADR 0019** (Topologie und Pfade, Entwurf): VDOM-Kontext (0.15.0) und Routing/HA/L3Out
  lesen (0.16.0) umgesetzt und abgenommen; Stufe 3 erste Fassung (Weg Hop für Hop, 0.17.0,
  Test offen), Regeln und NAT je Hop auf dem Weg (0.22.0/0.23.0), Relevanz in der Doku
  (0.24.0), Gegenstelle `any` (0.25.0), Relevanz in der Regel-Liste (0.26.0), Inter-VDOM-Links
  (0.27.0), ACI-Contracts über externe EPGs (0.28.0), Verbund als ein Hop (0.29.0), Weg-Suche
  mit ACI-Contracts je VRF-Hop und Namen (0.35.0). Weiter mit
  dem Designer (Stufe 4); später Switches/Verkabelung (L1/L2-Sicht).
- Danach: Applikationen (Regeln direkt zuordnen, Abhängigkeiten, **jede Verbindung einmal Ende
  zu Ende** über Firewalls und ACI mit der Reihenfolge der Regeln), produktiv schalten mit den
  R14-Massnahmen (ADR 0013, RISKS), Cockpit (ADR 0014), Import-Wizard (E3); Schreibpfad (E4)
  nur mit eigener ADR.
