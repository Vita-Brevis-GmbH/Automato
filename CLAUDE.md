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

## Aktueller Stand / nächster Schritt

Planungsphase. Die Roadmap ist als Meilenstein-Plan **M0–M9 mit Exit-Kriterien**
aufgebaut (siehe `ROADMAP.md`); Risiken werden im Risikoregister `docs/RISKS.md`
geführt und an jedem Meilenstein aktualisiert.

M1 ist inhaltlich entschieden: **Use Case #1 = Firewall-Regel-Dokumentation
(read-only)** – Firewall-Regeln lesen, mit IPAM-Adressobjekten verknüpfen,
Service-Objekt-DB, Web-UI mit Filtern (Systeme/Netzwerke/Zonen), Applikations-Doku.
Zwei Kundenmodelle, **Model 2 zuerst** (FortiGate + **NetBox**, kein ACI), danach
Model 1 (FortiGate + ACI + **Infoblox**). Umsetzung deshalb **NetBox-Adapter zuerst**.
Das **Datenmodell ist als ADR 0004** entworfen (mandantenfähig; eine `rule`-Tabelle mit
polymorphen Endpunkten host|network|epg|zone; Zonen aus dem SoT; Services inkl.
ICMP/Dual-Stack; NAT-Schema; Findings für Datenqualität; Namenskonvention als
First-Class-Signal). Details/DoD: `docs/usecases/uc1-firewall-regel-dokumentation.md`
(**DoD bestätigt**, Model-2-Scope). Die Regel-Automatisierung gemäss ADR 0002 folgt als
späterer Use Case.

Der **Grundaufbau (M6) ist als Code-Gerüst umgesetzt** (Entscheide: ADR 0003):
uv-Workspace mit `orchestrator/` (FastAPI, Alembic, Audit-Log, Job-Kreislauf),
`collectors/` (FortiGate/ACI-Skeleton, Beispieldaten bis zum Lab), `sot-gateway/`
(NetBox- und Infoblox-Adapter lesend), `web-ui/frontend/` (React/Vite-Skeleton);
dazu docker-compose (Profile core/collectors), CI (`.github/workflows/ci.yml`)
und pre-commit/gitleaks. Tests: `uv run --package <name> pytest <name>/tests`.

Das **Datenmodell (M5, ADR 0004) ist als Code umgesetzt (Phase 1)**: SQLAlchemy-Modelle
in `orchestrator/app/models/` (tenant, zone, network_object, service_object, rule,
rule_endpoint, rule_service, finding), Alembic-Migration `0002`, Namenskonvention-Parser
`app/naming.py`, Store-Query + `/rules`-Endpunkt, Tests. ACI-Tabellen (`epg*`) und
`nat_rule` folgen mit ihrer Phase.

Die Gates **M1 (DoD bestätigt), M2 (Risikoregister R1–R14) und M3 (Security-Review,
ADR 0005)** sind erledigt; erste Sofort-Härtungen umgesetzt (Fail-closed Token-Check,
CORS aus Konfiguration).

Der **M7-Durchstich ist codeseitig fertig und fixture-getestet** (Model 2): FortiGate-Collector
mit echtem FortiOS-REST-Abruf (rein lesend, `collectors/fortigate/client.py`) inkl.
Adress-/Service-Auflösung (`normalize.py`: Objektnamen→Werte, FQDN/`H-EXT_` bleiben Name),
NetBox-Adapter (lesend), Ingest/Matching in den Rule & Service Store, Findings, interne
API verdrahtet (`app/store/ingest.py`, `app/sot.py`, Tests `test_ingest.py`/
`test_internal_ingest.py`/`test_fortigate_client.py`). Ohne Lab-Creds liest der Collector
Beispieldaten; mit `AUTOMATO_FORTIGATE_URL`/`_TOKEN` den echten Abruf. Der
**CLI-Auslöser** ist gebaut (`orchestrator/app/cli.py`, Konsolen-Command `automato`:
ensure-tenant/ensure-source/collect/status/lab, `test_cli.py`). Das **Lab-Setup ist
scriptbar** (`lab/install-prereqs.sh`, `lab/up-automato.sh`, `lab/README.md`;
`source-of-truth/netbox/`, `collectors/fortigate/LAB.md`); docker-compose reicht die
NetBox-/FortiGate-Creds an Orchestrator/Collector durch, NetBox läuft als eigener
Stack auf Port 8080 (Orchestrator auf 8000).

Als Nächstes: Lauf gegen das **echte Lab** (Lab-Creds setzen, echter Abruf statt
Beispieldaten via `lab/README.md`), dann M8/M9.

**M7 real erreicht + M8-Review erledigt:** Auf einer VM wurden per `lab/bootstrap.sh`
75 echte FortiGate-Policies read-only via FortiOS-REST gelesen und End-to-End in den
Store geschrieben. Nach Bootstrap-Import der FortiGate-Objekte nach NetBox
(`source-of-truth/netbox/import_from_fortigate.py`, opt-in, schreibt mit Admin-Token;
Collector bleibt read-only) und typ-bewusster Einordnung: **0 nicht aufgelöste
Endpunkte, 0 offene Findings**. Der Collector liefert den FortiGate-Objekttyp mit
(ipmask/interface-subnet/iprange/fqdn/geo/mac/dynamic/vip); der Orchestrator ordnet ein
(fqdn=expected_external, geo/mac/dynamic=not_applicable, VIP über extip). **Web-UI**
(`web-ui/frontend/`) zeigt Regeln-Tabelle, Regel-Detail (aufgelöste Endpunkte/Zonen/
Services) und Findings mit Filtern (System/Zone/Aktion/Freitext); API: `/rules`,
`/rules/{id}`, `/findings`, `/systems`. Erkenntnisse/Entscheide: **ADR 0006**;
Risiken R15–R17 ergänzt.

Als Nächstes (M9, UC1 abschliessen): Applikations-Doku/Export, VIP-NAT-Mapping
(extip→mappedip, Phase 1.5), Zonen aus Interfaces/SoT, Betriebs-/Anwender-Doku,
Security-/Hardening-Check. RBAC/Secrets/mTLS/Netzsegmentierung als Folge-ADRs (ADR 0005).

**M9 erledigt – UC1 Model 2 funktional vollständig & Lab-/Review-reif:** Zusätzlich zum
Obigen umgesetzt: VIP-NAT (`nat_rule`, Alembic 0004; `/nat`; Regel-Detail zeigt extip→
mapped), Zonen aus FortiGate-Interfaces am Subnetz verortet (Import tagt interface-subnets),
**Applikations-Doku** (`/applications`, Markdown-Export im UI). API gesamt: `/rules`,
`/rules/{id}`, `/findings`, `/nat`, `/applications`, `/systems`, `/jobs`. DoD-Abgleich +
8-Prinzipien-Security-Check im UC1-Dokument; Betriebs-/Anwenderhandbuch `docs/BETRIEB.md`.
Sofort-Härtung: öffentliche Read-API ist **fail-closed ausserhalb `dev`**
(`AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API`), da AuthN/RBAC noch fehlt.

**Offenes Produktions-Gate:** AuthN/RBAC vor der Read-API (umgesetzt, E1); mTLS/at-rest,
Netzsegmentierung (Folge-ADRs). Danach: Model 1 (ACI + Infoblox), Schreibpfad (ADR 0002).
**Finding-Triage-Schreibpfad umgesetzt**: `PATCH /findings/{id}` (operator, mandantsgescoped,
auditiert `finding.triaged`) setzt Status (open/acknowledged/resolved/ignored) + Notiz;
ungültiger Status → 422; UI-Findings-Tab mit Status-Dropdown. Tests `test_findings_triage.py`.
**Model 1 gestartet (ACI-Collector, read-only):** APIC-MO-Export-Parser
`collectors/.../cisco_aci/mo.py` (`parse_mo_export`) nach Lesevertrag Abschnitt 2 — vzBrCP/
vzSubj/vzRsSubjFiltAtt→Regel+Services (vzFilter/vzEntry), fvRsProv/fvRsCons→Provider/Consumer-
EPGs, fvBD.fvRsBDToOut→L3O als Zone, l3extOut/l3extInstP→externe EPG/Zone. `normalize_contract`
emittiert EPGs typisiert als `epg:<name>` + Zonen; Ingest führt `epg:`-Endpunkte als
**EndpointKind.EPG inline** (kein IPAM-Match, kein Finding). Collector-Task nutzt den MO-Export
(Beispiel bis Lab-Creds; echter APIC-REST-Abruf folgt). Infoblox-SoT-Adapter liegt bereits
lesend vor. Tests: collectors `test_normalize.py` (MO-Parser), orchestrator `test_ingest.py`
(EPG-Ingest). **Offen (eigene Phase):** epg-Tabellen (`epg`/`epg_subnet`/`epg_endpoint`),
Contract-`scope`→Applikations-Doku-Kategorie, EPG als System in der Applikations-Doku,
echter APIC-REST-Client.
**ACI-epg-Phase (A) umgesetzt** (Alembic `0011`): Modelle `epg` (tenant, name, `ap`=Projekt,
`bd`; unique je Mandant) + `epg_subnet` (fvSubnet, dual-stack); `rule.aci_scope` (Contract-Scope)
und `rule_endpoint.epg_id`. DTO erweitert (`NormalizedEpg`, `NormalizedRule.aci_scope`,
`CollectorResult.epgs`). MO-Parser `parse_epgs` (name/ap aus dn/bd/subnets) + `aci_scope` an der
Regel; Ingest `_apply_epgs` persistiert EPGs/Subnetze **vor** den Regeln und verlinkt
`epg:`-Endpunkte auf `epg_id` (matched, kein Finding). Tests collectors (`parse_epgs`/scope) +
orchestrator (`test_ingest`). **ACI-epg-Phase (B) umgesetzt**: `appdoc.build_doc` unterscheidet
Host-System (Firewall-Tabellen 1–6/10) und **EPG-System** (`_epg_doc`, ACI-Tabellen 7/8/9);
`categorize_aci(scope, same_ap, same_tenant)` — `application-profile`/gleiches AP→7, anderes AP→8,
`global`/anderer Tenant→9. `list_systems` liefert EPGs als Systeme (`kind=host|epg`). UI-Tab zeigt
EPG-Systeme (Label „· EPG", keine Standort-Verortung an der EPG). Tests `test_appdoc.py`
(`categorize_aci` + EPG-System 7/8/9). Damit füllen die ACI-internen Doku-Tabellen bei Model 1.
**ACI-epg-Phase (C) umgesetzt** (Alembic `0012`): `epg_endpoint` (gelernte Endpunkte fvCEp/fvIp,
akkumulierend) + `epg.network_object_id` (EPG↔Server). DTO `NormalizedEpgEndpoint` +
`NormalizedEpg.endpoints`; MO-Parser liest `fvCEp`/`fvIp`; Ingest `_apply_epgs` persistiert
Endpunkte und **verknüpft die EPG mit dem Host-Objekt** (Match gelernter Endpunkt-IP →
`network_object.value`, „ein Server = eine EPG"); `_epg_doc` zeigt dann IP + Standort des
Servers. Tests collectors/orchestrator.
**Externer Collector / Modularisierung umgesetzt (ADR 0008):** Zielsysteme stehen in
Kundennetzen, die die zentrale Plattform nicht erreicht → der Collector läuft als
**eigenständiger Agent** (z. B. Windows) nahe am Zielsystem, nur **ausgehend HTTPS** zum
Orchestrator. Zugriffsmodul von Celery entkoppelt: je Plattform `collect_once(run_id)`
(gather→normalize→submit), Celery-Task nur dünner Wrapper. **Standalone-Runner**
`automato-collector --platform … --run-id …` (`collectors/run.py`, Konsolen-Script) ohne
Celery/Redis, einziger Rückkanal `POST /internal/.../result` (`X-Internal-Token`).
Lauf-Modell: `POST /jobs/collect` mit `execution=external` legt den Lauf an **ohne**
Celery-Dispatch (Agent holt ihn per RUN_ID ab); `inprocess` = Default (zentraler Worker).
Secrets liegen beim Agenten (ADR 0007). Doku `collectors/EXTERNAL.md`. Tests
`collectors/tests/test_run.py` + `orchestrator/tests/test_jobs_external.py`.
**Agent-Pull-Modell umgesetzt** (Alembic `0013`): Modell `agent` (name, token_hash,
tenant_id, enabled, last_seen) + `collector_run.execution`. Admin-API `POST/GET /agents`
(Token einmalig, nur Hash gespeichert, auditiert `agent.created`); Agent-API
`GET /internal/agent/next-run` (X-Agent-Token; holt ältesten offenen **externen** Lauf im
Tenant-Scope, QUEUED→RUNNING, auditiert `collector_run.claimed`) + `POST …/heartbeat`.
Runner-Modus `automato-collector --poll` (`collectors/common/agent.py`); kein out-of-band-
RUN_ID mehr nötig. Tests `test_agents.py` + `test_run.py`.
**Agent-Verwaltung im UI umgesetzt**: `AgentOut` um `last_seen` erweitert, `PATCH /agents/{id}`
(admin, `enabled`, auditiert `agent.updated`); deaktivierter Agent authentisiert nicht mehr
(`agent_by_token` filtert `enabled`). UI-Tab „Agenten" (`web-ui/frontend/src/Agents.tsx`):
Agenten anlegen (Token einmalig prominent zum Kopieren), Mandant wählen, Status/`last_seen`
(Heartbeat), Aktivieren/Deaktivieren. **Dev-Einbindung dokumentiert**: `docs/DEV-SETUP-AGENT.md`
(frische NetBox via `NETBOX_RESET=1`, frische Automato-DB via `AUTOMATO_RESET=1`, Agent im UI
registrieren, externen Lauf `execution=external` + `--poll` durchspielen).
**mTLS-Support umgesetzt** (client-seitig): zentraler HTTP-Layer `collectors/common/http.py`
(`client_kwargs`) — Agent präsentiert optional Client-Zertifikat (`AUTOMATO_CLIENT_CERT/_KEY`)
und pinnt die Server-CA (`AUTOMATO_ORCHESTRATOR_CA_BUNDLE`); `upload.py`/`agent.py` laufen
darüber. Serverseitig erzwingt der Reverse-Proxy (Caddy) Client-Zertifikate vor `/internal`
(Snippet in `collectors/EXTERNAL.md`); interner Token bleibt zweite Schicht. Tests
`test_http.py`.
**Connectivity-Self-Check umgesetzt**: `FortiGateClient.ping()` (leichter Lese-Zugriff
`system/global`) + `check()` je Plattform; Runner-Modus `automato-collector --check
--platform …` prüft Erreichbarkeit/Auth des Zielsystems read-only (Exit 0 OK / 2 Fehler),
bevor ein echter Lauf im Kundennetz gestartet wird. Tests `test_fortigate_client.py`
(ping) + `test_run.py` (check).
**Windows-Paketierung umgesetzt**: `deploy/collector-agent/` — `install.ps1` (venv +
collectors-Wheel + Scheduled Task „Automato Collector Agent" → `--poll` alle N min),
`run-agent.ps1` (lädt `agent.env`, startet den Agenten), `agent.env.example` (Token/Ziel-
Creds/TLS), `agent_entry.py` (PyInstaller-Entry für eine Python-freie `.exe`), README. Wheel
baut via `uv build --package collectors`. Damit ist der externe Collector auf Windows
rollbar. **Offen (Model-1-Rest):** echter APIC-REST-Client im Agenten (Login + MO-Subtree).
**Standort-Verortung umgesetzt** (Alembic `0010`): `network_object.site_id` (nullable FK → site)
verortet ein System an einem Standort. `PATCH /appdoc/systems/{name}/site` (operator,
mandantsgescoped, auditiert `system.site_assigned`; fremde Site → 422); `build_doc` liefert jetzt
`tenant_code` (GG-Kürzel) + `site` (code/name). UI-Tab „Applikations-Doku" zeigt GG/Standort und
erlaubt Zuordnung (Site-Dropdown aus den Mandanten-Standorten); Markdown-Export-Header enthält
GG + Standort. Tests `test_appdoc.py`. Offen: EPG als System (ACI) in der Doku.
Tests: collectors 29, orchestrator 109, sot-gateway 6.

**Ausbaustufe B (Planung läuft):** Ausbau zur allgemeinen Workflow-Plattform (über
Firewalls/ACI hinaus: VMs, App-Install, Auto-Doku; Open Source, Vereinfachung) mit
starker **Synergie zu Projekt Magister** (`Vita-Brevis-GmbH/magister`, fast identischer
Stack → gemeinsame Bausteine Auth/Cockpit/Deploy, Module später vereinen). Epics W1–E7
in `ROADMAP.md` (Abschnitt „Ausbaustufe B"). Entschieden: **AuthN/RBAC via Keycloak**
(dedizierter IdP, eigener Container→VM, eigene URL, OIDC+PKCE; lokal + SAML + OTP;
geseedter globaler Admin; Mandanten-Scoping zentral erzwingen; Rollen `viewer/operator/
admin`; Secrets-/Vault-Provider) — **ADR 0007**. Reihenfolge: **Web-UI (W1) vorgezogen**,
dann **E1 Härtung/RBAC** als Schwerpunkt. UI-Mockup liegt als Artifact vor (nicht im Repo).
**UC1-Fachdaten erhalten & festgehalten:** Standard-Namenskonvention (Profil „default")
(`docs/naming-conventions.md`, austauschbares Profil pro Kunde), Tenant-Modell Kunde/GG
und Applikations-Doku-Spec (9+ Tabellen, `docs/usecases/uc1-applikations-doku-spec.md`)
→ **ADR-0004-Ergänzung**. Kernpunkte: Mandant = Gruppengesellschaft; Kunde/Umgebung (z. B. Kunde A)
darüber trägt NetBox + Namensprofil; Global-Admin-Gesamtsicht; Applikation = System
(Container/VM/Server) mit AP/Projekt-Klammer (~90 % automatisch, Rest via Designer);
Ausnahmen-Kaskade (Konvention→Typ/Farbcode→Override→Finding); neue Collector-Felder
(UTM-Schutzprofile, SNAT-IP-Pools `IPP_`, externer DNS aus `V-EXT_`, FortiGate-Farbcode).
**Samples erhalten** (ACI-Tenant-Export `TE_share` + FortiGate-Config einer Referenzumgebung) →
**Collector-Lesevertrag** `docs/collectors-read-contract.md` (FortiGate+ACI; Samples
bleiben ausserhalb Repo, Secrets gestrippt; `color` = numerischer Code, optional; reale
UTM-Prefixe `AV_/WF_/DF_/AC_/IPS_/WAF_/SSL_`; Zusatz-Prefixe `N-EXT_`/`T_`/Geography;
Contract-`scope`→Doku-Kategorie, `fvRsBDToOut`→L3O, Dual-Stack). Wörterbücher (Kap. 3.1
GG-Abkürzungen, 3.2 Projektnummern/Funktionscodes) sind **in-App pflegbare, erweiterbare
Daten** (Namensprofil) — **kein CSV-Import geplant**: der Kunde pflegt diese Daten bei der
Inbetriebnahme direkt in der App (keine Funktionsabhängigkeit, Seed optional).

**Umsetzung Ausbaustufe B läuft (Reihenfolge W1→E1, davor Wörterbücher):** CI war rot
(ruff nicht als Dep deklariert) → behoben (ruff==0.5.5 als Dev-Dependency, identisch zu
pre-commit; CI grün). **(1) Namensprofil-Wörterbücher als Code umgesetzt**: Modelle
`naming_profile`/`dictionary_entry` (offenes `kind` = erweiterbar), Alembic `0005`
(up/down geprüft), Store + idempotenter Standard-Seed, API `/naming-profiles` (Lesen offen,
Schreiben per internem Token bis RBAC), Tests. Mockup-Screen „Namensprofile" + Regeln
mit getrennten Quelle/Ziel-Spalten (mehrere Objekte) + 3-stelliger Standort-Code.
**(2) W1 Web-UI umgesetzt**: Übersicht/Dashboard (KPIs Regeln/NAT/Findings/unmatched,
Regeln je Ziel-Zone, neueste Findings), **Mandanten-Switcher** (neuer `/tenants`-Endpunkt),
Regelliste mit getrennten **Quelle/Ziel**-Spalten inkl. mehrerer Adressobjekte (`/rules`
um `sources`/`destinations` erweitert), Regel-Detail unverändert. Frontend `tsc`+`vite` grün.
**(3) FortiGate-Collector Phase 1.5 umgesetzt**: Dual-Stack (`address6`/`addrgrp6`,
`srcaddr6`/`dstaddr6`), UTM-Schutzprofile pro Policy (`rule.protections` → Regel-Detail),
SNAT-IP-Pools (`firewall/ippool` → `nat_rule.nat_type=snat_pool`, `/nat` zeigt `nat_type`),
FortiGate-**Farbcode** als Objekt-Katalog (`network_object.color`, via Name/Wert-Match).
`collector.gather()` liefert jetzt ein Dict; DTO + Orchestrator-Vertrag erweitert; Alembic
`0006` (up/down geprüft). Collector-Fixtures `sample_addresses6/addrgrps6/ippools.json`.
**`app/naming.py` auf Profil-Wörterbücher umgestellt** (Alembic `0008`): Parser ist jetzt
profil-gesteuert — `parse_object_name(name, prefixes)` nimmt eine aus dem `object_prefix`-
Wörterbuch gebaute Präfix-Liste (`prefixes_from_entries`), bleibt reine Funktion; `tenant.
naming_profile_id` (nullable FK) verknüpft Mandant→Profil, Ingest löst die Liste je Lauf auf
(`_resolve_prefixes`); ohne Profil gilt die **erweiterte Standard-Konvention** (jetzt inkl.
`N-EXT_`/`T_`/`V-EXT_`/`IPP_`). Keine Funktionsabhängigkeit von Daten (leeres Profil → Default).
Tests `test_naming.py`/`test_ingest.py`. **Applikations-Doku-API (systemzentriert, 10 Kategorien)
umgesetzt**: `app/store/appdoc.py` + `GET /appdoc/systems` (Host-Systeme) und
`/appdoc/systems/{name}` (die 10 Tabellen aus `uc1-applikations-doku-spec.md`, je Richtung×
Scope×Enforcement). Pure `categorize()` (NAT dnat_vip→T2 / snat_pool, L3O-Zonen bit/vpn/sra/
res→T3–6/9, extern/inet→T1, sonst T10; ACI-intern T7/8 leer bis Model 1); Spalten nutzen
`rule.protections` (Schutzmechanismen) und `nat_rule` (VIP-IP/externer DNS aus `V-EXT_`).
UI-Tab „Applikations-Doku" (`web-ui/frontend/src/AppDoc.tsx`): System-Auswahl, Tabellen,
**Markdown-Export** (clientseitig). Tests `test_appdoc.py` (categorize + build_doc + API).
**E1-Reste umgesetzt:** (a) **per-Mandant Lesezugriffs-Audit** — `tenant.audit_reads` (Alembic
`0009`, Default aus) schaltet `read.<resource>` (rules/nat/findings/applications/appdoc) scharf,
nur bei konkretem Mandanten (nicht Admin-Gesamtsicht); `app/audit/reads.py`, Allowlist-Einträge,
pflegbar über `PATCH /tenants/{id}` (admin, auch im UI-Tenant-Tab erweiterbar); Tests
`test_read_audit.py`. (b) **SAML-Broker je Kunde (Vorlage)** — `deploy/keycloak/saml-idp-broker.
example.json` + `saml-mappers.example.json` (Attribut-Importer `tenant_ids`, Default-Rolle
`viewer`), Import-Schritte (`kcadm`) im Keycloak-README. API gesamt jetzt inkl. `/appdoc`.
**E1 Härtung/RBAC gestartet (E1.1 Gerüst)**: `app/auth/` mit `Principal`, Rollen
`viewer/operator/admin` (Hierarchie), FastAPI-Dependencies (`require_viewer/operator/admin`)
und **zentralem Mandanten-Scoping** (`tenant_scope`): Nicht-Admin nur innerhalb erlaubter
Mandanten, fremder `tenant_id` → 403 (schliesst R13/ADR 0005 #6). Verdrahtet an `/rules`,
`/rules/{id}`, `/nat`, `/findings`, `/applications`, `/tenants`, `/systems`. `auth_mode`
(dev|oidc): **dev** = Standard-Admin (unverändertes Verhalten), Override via
`X-Dev-Roles`/`X-Dev-Tenants` zum Testen; **oidc** = Keycloak-JWT, noch nicht aktiv (501).
Tests `test_auth.py`. **E1.1b OIDC/Keycloak umgesetzt**: `app/auth/oidc.py` validiert
Bearer-JWT (PyJWT + JWKS, Issuer/Audience), mappt Claims → Principal (`realm_access.roles`,
`tenant_ids`-Claim; `admin`=alle Mandanten); `auth_mode=oidc` aktiv. Keycloak-Scaffold
`deploy/keycloak/` (Realm-Export: Rollen viewer/operator/admin, Clients automato-web
[PKCE]/automato-api, `tenant_ids`-Mapper, geseedter Admin mit OTP-/Passwort-Pflicht) +
Compose-Service (Profil `auth`, Port 8081). Tests `test_auth_oidc.py` (Claim-Mapping,
RSA-Roundtrip, Bearer-Scoping). **E1.3a Rollen an Schreibpfaden umgesetzt**: Collect
(`POST /jobs/collect`) = `operator` + Mandanten-Scope; Namensprofil-Schreiben = `admin`,
Lesen = `viewer`; `GET /jobs/{id}` mandantsgeprüft; Audit-Actor = `user:<principal>`.
UI sendet Bearer aus `localStorage` (api.ts), wenn vorhanden. Tests erweitert.
**E1.3b Web-UI-OIDC-Login umgesetzt** (nur build-verifiziert; End-to-End gegen laufendes
Keycloak): öffentlicher `/auth/config` (auth_mode/issuer/client_id); SPA `src/auth.ts`
via **oidc-client-ts** (Authorization Code + PKCE, Callback → Token in `localStorage`,
Abmelden); dev-Modus = kein Login. **E1.4 Audit auf Principal+Aktion umgesetzt**: zentrale
Schreibstelle `app/audit` mit zwei Schutzschichten — (1) **Allowlist** (`audit/allowlist.py`):
pro Aktion erlaubte `detail`-Keys, alles andere verworfen (fail-closed), verworfene nur als
Name unter `_dropped` → Secrets landen strukturell nie im Log; (2) **optionale
At-Rest-Verschlüsselung** (`audit/crypto.py`, Fernet, `AUTOMATO_AUDIT_ENCRYPTION_KEY`) —
bewusst app-seitig statt pgcrypto (Schlüssel in App/Vault statt DB-nah, Tests DB-agnostisch);
Default leer = Klartext (dev/Tests). Principal (`actor=user:<sub>`) nun auch an den
Namensprofil-Schreibpfaden (create/upsert/delete). Lesen via `audit.decode_detail`. Tests
`test_audit.py`. **E1.5 Secrets-/Vault-Provider umgesetzt**: `app/secrets/` mit
`SecretProvider`-Interface (Adapter-Pattern wie SoT-Gateway) und Providern `EnvProvider`
(Lab/Dev, `.env`), `FileProvider` (Docker-/K8s-Secrets, SOPS/age) und `OpenBaoProvider`
(OpenBao/Vault KV v2 über HTTP, nur stdlib). Secret-tragende Settings dürfen als Referenz
`schema:locator` gesetzt werden (`env:`/`file:`/`openbao:`/`literal:`); `resolve_settings`
löst sie einmalig in `create_app()` auf, **bevor** `check_security_config` greift. Klartext
ohne Schema bleibt unverändert (→ .env/dev wie bisher); nicht erreichbares Backend/fehlendes
Secret = fail-closed. Betriebsanleitung `deploy/secrets/README.md`. Tests `test_secrets.py`.
**Keycloak Dev+Prod-Deployment umgesetzt**: **Dev** = Container in der Automato-VM
(docker-compose Profil `auth`), nun gehärtet — nur an `127.0.0.1` gebunden, persistentes
Volume `keycloak-data` (User/OTP überleben Neustart), `KC_HEALTH_ENABLED`. **Prod** = kleines
**Ansible-Playbook** `deploy/keycloak/ansible/` (keycloak.yml + Templates): Stack Caddy
(TLS 1.3/ACME, HSTS, Security-Header) → Keycloak prod-mode (`start`, `KC_HOSTNAME`,
Proxy-Header, HTTP nur intern) → Postgres (persistent); Secrets via Ansible Vault
(`.gitignore` schützt inventory/vault), Firewall default-deny, fail-closed Pflicht-Var-Check,
Wait auf OIDC-Discovery. **Realm-Härtung** im gemeinsamen `realm-automato.json` (gilt dev+prod):
Brute-Force-Schutz, Passwort-Policy (12+, Mix, History, Ablauf), **OTP/TOTP-Pflicht**,
Browser-Security-Header, `registrationAllowed=false`. Doku `deploy/keycloak/ansible/README.md`.
**Keycloak Prod live** (manuelles Runbook `deploy/keycloak/PROD-INSTALL.md`, Ubuntu 26.04,
eigenes Sectigo-Wildcard als Fullchain, kein ACME): öffentlich `auth.vitabrevis.ch` (Kunden,
hinter FortiGate-Virtual-Server + WAF; `/admin` + `/realms/master` gesperrt), Admin-Konsole nur
intern `auth.int.vitabrevis.ch` (`KC_HOSTNAME_ADMIN`, Zertifikat von der DC-CA, Caddy
`remote_ip` + interne FortiGate-Policy ohne NAT). WAF-Ausnahme per URL-Access-Regel auf Host +
OIDC-Pfad gescoped (Negativ-Test Pflicht, Abschnitt 12). Integrations-Fixes: `docker-compose.yml`
reicht `AUTOMATO_AUTH_MODE`/`AUTOMATO_OIDC_*` an den Orchestrator durch; Realm-Export mit
**Audience-Mapper** (`aud=automato-api`, sonst 401); `tenant_ids` als verwaltetes
User-Profile-Attribut (nur Admin editierbar), OTP-Pflicht per Required-Action-Default (nicht
per Flow-Änderung — sperrt in KC 26 aus); Dev-Login per SSH-Tunnel (PKCE braucht sicheren
Kontext). **TLS-Proxy für die Web-UI** (Compose-Profil `proxy`, `deploy/proxy/`): Caddy auf 443
vor `web-ui:5173`, Host aus `AUTOMATO_UI_HOST` (IP oder Name; Dev: `172.25.12.12`), Zertifikat per
`AUTOMATO_UI_TLS`: `internal` (Default, selbst erzeugt, auch für IPs; `default_sni` nötig, da
Browser bei IPs kein SNI senden) oder Fullchain+Key der eigenen CA aus `deploy/proxy/pki/`
(gitignored); `/api/internal` nicht ausgeliefert; Profil `proxy` nur zusammen mit `core`
(hängt an `web-ui`). Keycloak-Client `automato-web` braucht Redirect-URI/Web-Origin der Adresse.
**Ein Realm pro Automato-Umgebung** (`automato` = Prod, `automato-dev` = Dev; Mandanten-IDs
gehören zur DB der Umgebung): Generator `deploy/keycloak/make_realm.py` erzeugt aus
`realm-automato.json` einen Realm mit eigenem Namen + UI-Adressen (Runbook Abschnitt 13).
**Externer Lauf per CLI**: `automato collect … --execution external` legt den Lauf ohne
Celery-Dispatch an (Agent holt ihn per `--poll`) — funktioniert auch im OIDC-Modus ohne Token.
Agent-Paket: `agent.env.example` nur mit Orchestrator-Adresse + `AUTOMATO_AGENT_TOKEN` (seit
ADR 0009 kein internes Token und keine Geräte-Creds mehr), TLS-Zeilen auskommentiert (fehlende Zertifikatsdateien verhindern den
Start), `run-agent.ps1 -Check`; Agent spricht `/internal` direkt am Orchestrator (Port 8000),
nicht über den UI-Proxy.
**Mandanten-Klärung (GG):** Ein **Mandant = Gruppengesellschaft (GG)** mit **einem oder
mehreren Standorten** und einem **Kürzel/Code** zur Identifikation bei VMs/Objekten. Die
**Wörterbücher (GG-Kürzel etc.) sind kundenspezifische Laufzeitdaten** (kein Seed-Zwang) und
**dürfen keine Funktionsrelevanz** haben — die Plattform muss ohne befüllte Wörterbücher
voll funktionieren (so bereits gebaut: naming_profile/dictionary_entry sind optional).
**Mandant Kürzel + Standorte umgesetzt** (Alembic `0007`, ADR-0004-Ergänzung 2026-10-14):
`tenant.code` (optionales GG-Kürzel, nullable/indiziert, keine Funktionsrelevanz; `PATCH
/tenants/{id}` [admin], CLI `ensure-tenant --code`, in `/tenants` sichtbar) und Modell
`site` (Standort: `tenant_id`, dreistelliger `code`, `name`, `description`; Code je Mandant
eindeutig) mit `GET/POST /tenants/{id}/sites` + `DELETE …/sites/{sid}` (Lesen `viewer`,
Schreiben `admin`, mandantsgescoped, auditiert via E1.4-Allowlist `tenant.updated`/
`site.created`/`site.deleted`). Tests `test_tenants_api.py`. **UI**: Tab „Mandant/Standorte"
(`web-ui/frontend/src/Tenants.tsx`) — Kürzel des gewählten Mandanten pflegen + Standorte
listen/anlegen/löschen (3-stelliger Code), Schreibfehler (403/409/422) werden angezeigt;
`/tenants` liefert `code` für den Switcher. Offen (eigene Phase): Objekt/Applikation am
Standort verorten; Kürzel in die Applikations-Doku. **Als Nächstes in E1:** optional per-Mandant zuschaltbares
Lesezugriffs-Audit; Keycloak-Client-/Session-Secrets über denselben Provider (sobald
Confidential-Client-Flow); SAML-Broker je Kunde. Offen (Eingang abwarten): echter Keycloak-
End-to-End-Login-Test (E1.3b nur build-verifiziert).
**Web-UI nach Mockup fertiggestellt (W1-Abschluss, Dev-Feedback):** Ursache für „neuer
Mandant nicht sichtbar / Agent anlegen geht nicht" war das Token-Handling: das einmal in
`localStorage` abgelegte Access-Token lief nach 5 min ab, ein Reload galt trotzdem als
angemeldet → alle API-Aufrufe 401, Fehler wurden verschluckt. Jetzt ist der
`UserManager` (oidc-client-ts) die einzige Token-Quelle (`src/auth.ts`: stilles Erneuern
per Refresh-Token, sonst Re-Login), `src/api.ts` holt das Token je Request, wirft
`ApiError` mit FastAPI-`detail` und leitet bei 401 zum Login; UI zeigt verständliche
Fehler (403/409/422). Zusätzlich: doppelter Agent-Name lieferte 500 → jetzt 409, leerer
Name/unbekannter Mandant 422. **Shell nach Mockup**: Sidebar (Dokumentation/Administration,
Zähler), Topbar (Mandanten-Schalter, HTTPS, Hell/Dunkel, Benutzermenü), Design-Tokens
+ IBM Plex lokal gebündelt (`@fontsource`, keine Google-Fonts-Abhängigkeit). **Neue
Ansichten/Buttons**: Übersicht mit Abdeckung + „Letzte Läufe" (`GET /jobs`) + **Jetzt
einlesen** (`CollectDialog.tsx`, extern/zentral), Mandanten & Standorte mit **+ Mandant**
(`POST /tenants`), Namensprofil-Zuordnung, Lese-Audit, **+ Standort**, **+ Quellsystem**
(`POST /systems`); Namensprofile (`NamingProfiles.tsx`: **+ Neues Profil** mit Standard-Vorlage
`with_defaults`, **+ Neue Namenskonvention**, Einträge ✎/✕; kein CSV-Import); Benutzer
& Rollen (`Users.tsx`, Links in die Keycloak-Konsole via `admin_console_url` aus
`/auth/config`, konfigurierbar `AUTOMATO_OIDC_ADMIN_CONSOLE_URL`); Netzwerkdesign als
Platzhalter (geplant); Applikationen = Applikations-Doku + „Publizierte Dienste";
Collector-Agenten (Online-Status aus Heartbeat). Nat/Findings/Applications mandantsgescoped.
Verifiziert: `tsc`+`vite build`, Klick-Durchlauf per Playwright gegen lokalen Orchestrator
(dev-Modus); OIDC-Token-Erneuerung nur build-verifiziert (End-to-End im Dev-Keycloak).
API-Übersicht in `docs/BETRIEB.md` Abschnitt 3/4 aktualisiert.
**Anmelde-Schleife behoben (Dev-Feedback „alle Seiten springen auf Übersicht zurück"):**
Ursache: der Orchestrator lehnte jedes Token pauschal mit `401 invalid token` ab (ohne
Grund/Log), das neue UI meldete daraufhin neu an, Keycloak-SSO leitete sofort zurück →
Übersicht. Jetzt: `app/auth/oidc.py` nennt den Grund (expired/audience/issuer/Schlüssel
nicht in JWKS) und loggt ihn (`OIDC-Token abgelehnt: …`); JWKS nicht erreichbar = **503**
(kein Login-Problem); `PyJWKClient` wird wiederverwendet (JWKS-Cache statt Abruf pro
Request). UI: Schleifenschutz (`auth.relogin`: innerhalb 60 s nach einem Login kein
erneuter Redirect, stattdessen Fehlerseite mit Grund + „Erneut anmelden"), die Ansicht
(`#hash`) bleibt über den Login erhalten. Verifiziert mit einem Mock-OIDC-Provider
(Code+PKCE, Refresh-Erneuerung, falsche Audience → Fehlerseite, kein Loop). Tests
`test_auth_oidc.py` (Grund je Fehlerart, 503). Tests: orchestrator 111.
**OIDC-Backchannel (Dev-Befund):** Im Dev meldete der Orchestrator `503 JWKS nicht
erreichbar` — der Container erreicht `auth.vitabrevis.ch` nicht über den öffentlichen Weg
(Hairpin/WAF/DNS), der Browser schon. Lösung (dokumentiert in `PROD-INSTALL.md` 9c):
Backchannel Server-zu-Server direkt zur Keycloak-VM per `docker-compose.override.yml`
(`extra_hosts: auth.vitabrevis.ch:<KEYCLOAK-VM-IP>`, VM-spezifisch, gitignored); Issuer
bleibt die öffentliche URL, Browser/Kunden laufen unverändert über die WAF.
**Uhrenabweichung (Dev-Befund `ImmatureSignatureError`):** Keycloak-VM und Automato-VM liefen
zeitlich auseinander (Token-`iat` aus Sicht des Orchestrators in der Zukunft). `oidc.py` prüft
jetzt mit Toleranz `oidc_clock_skew_seconds` (Default 60, `AUTOMATO_OIDC_CLOCK_SKEW_SECONDS`) und
meldet die Abweichung in Sekunden („token not yet valid … NTP prüfen“; abgelaufen „seit N s“).
Eigentlicher Fix: NTP auf beiden VMs. Tests: orchestrator 112.
**Kundenname entfernt + Standardprofil „default" (Vorgabe):** Der Name des Referenzkunden ist
ein Beispiel und wird **nirgends hinterlegt** — aus Code, UI, Tests und Doku entfernt (Doku
anonymisiert, Beispiel-Domain `example.ch`; Farbcode-Klassen `intern_host`/`intern_network`;
API-Feld `with_defaults`). **Standardprofil „default"**: Werte in `app/naming_defaults.py`
(eine Quelle für Parser-Rückfall, Migration und Vorlage; nur Konventionsregeln — Objekt-/
UTM-Prefixe, Farbcodes, L3Outs, Funktionscodes, Standort-Code, Standard-Services — **keine**
kundenspezifischen GG-/Projektlisten), angelegt per Datenmigration **Alembic `0014`**
(idempotent, up/down geprüft). Mandanten **ohne eigenes Profil nutzen „default"**
(`ingest._resolve_prefixes`), fehlt es → eingebaute Standard-Konvention. „+ Neues Profil" mit
Vorlage kopiert das gepflegte „default". UI: Profil „default" vorausgewählt, „gilt für alle
Mandanten ohne eigenes Profil". Tests: orchestrator 113.
**Quellsysteme je Mandant + Löschen (Alembic `0015`):** `source_system.tenant_id` (FK, Name je
Mandant eindeutig `uq_source_system_tenant_name` statt global); Migration ordnet Altbestände dem
Mandanten zu, dessen Regeln/NAT/Läufe sie eindeutig tragen, sonst „nicht zugeordnet“ (UI-Karte
zum Zuordnen). `/systems` mandantsgescoped (`?tenant_id=`), `POST` verlangt `tenant_id`, `PATCH`
(Name/Adresse/aktiv/Mandant; Mandantenwechsel nur ohne fremde Daten → sonst 409), `DELETE`
(Regeln + NAT + dadurch verwaiste Findings, Läufe bleiben als Historie). `POST /jobs/collect`
prüft: Quellsystem gehört zum Mandanten und Plattform passt (422). **`DELETE /tenants/{id}?confirm=
<Name>`** löscht alle Mandantendaten in FK-Reihenfolge (`app/store/deletion.py`; Agenten des
Mandanten werden gelöscht statt entkoppelt — sonst sähe ein Agent ohne Mandant alle Läufe),
auditiert `tenant.deleted`/`source.deleted` mit Anzahlen. CLI `ensure-source --tenant`. Tests
`test_deletion.py`; **SQLite-Tests prüfen jetzt Fremdschlüssel** (`PRAGMA foreign_keys=ON` in
`conftest.py`), Migrationen 0014/0015 + Löschkaskade zusätzlich gegen echtes Postgres geprüft.
UI: Quellsysteme in der Mandanten-Ansicht, Löschen mit Rückfrage (Mandant: Namen eintippen),
„Jetzt einlesen“/Regel-Filter zeigen nur Systeme des Mandanten. Tests: orchestrator 116.
**Agent-Einrichtung im UI + Kopieren von Namenskonventionen:** Orchestrator-Image baut das
**Agent-Paket** mit (Dockerfile-Stage `agent-package`: collectors-Wheel + `deploy/collector-agent/`
→ `/workspace/agent-package`, Setting `agent_package_dir`); `GET /agents/package` (admin) liefert es
als ZIP **ohne Zugangsdaten**. UI „Collector-Agenten“ → `AgentGuide.tsx`: 8 Schritte (Voraussetzungen,
Agent anlegen, **agent.env-Generator** clientseitig per Blob – Token des neuen Agenten wird übernommen,
Vorschau maskiert, nichts gespeichert/übertragen –, Paket-Download, PowerShell-Befehle zum Kopieren,
Check, erster Lauf, Fehlerbehebung). `install.ps1`: Wheel neben dem Skript automatisch, daneben
liegende `agent.env` wird übernommen, Agent-Code wird bei Neuinstallation aktualisiert. Verifiziert:
Paket aus dem UI-Download in venv installiert, mit UI-`agent.env` `--poll` → Lauf completed.
**Namensprofile:** `POST /naming-profiles` mit `copy_from_profile_id` (Kopie eines Profils; Duplikat →
409), `POST /naming-profiles/{id}/dictionary/copy` (Art aus beliebigem Profil als neue Art, auditiert
`dictionary.copied`); UI: Vorlage-Auswahl beim neuen Profil (Default: Kopie von „default“) und
„Einträge kopieren aus“ bei neuer Namenskonvention. Tests: orchestrator 119.
**Geräte zentral beim Mandanten, Zuweisung an Agenten (ADR 0009, Alembic `0016`):** Quellsystem
trägt `agent_id` (verwaltender Agent; Agent muss zum Mandanten passen oder mandantsübergreifend
sein, sonst 422), `verify_tls` und verschlüsselte `credentials` (Fernet, `app/credentials.py`;
Schlüssel `AUTOMATO_CREDENTIALS_KEY` – auch Secret-Referenz – oder erzeugte Datei
`/var/lib/automato/credentials.key`, 0600, Compose-Volume `orchestrator-data`; fail-closed → 503 /
Lauf `failed`). Token **nur schreibbar** (`POST/PATCH /systems` `token`, `""` = entfernen), API gibt
nur `has_credentials` aus, Audit nur `credentials_set`. `collector_run.agent_id` = abholender Agent.
`next-run` liefert nur Läufe zugewiesener (oder unzugewiesener Alt-)Geräte, `target` mit Token nur
für den zugewiesenen Agenten; `GET /internal/agent/targets` (für `--check`);
`GET /internal/collector-runs/{id}/target` (internes Token, nur `inprocess`) für den zentralen
Worker. Ergebnis-Upload mit `X-Agent-Token` (nur der abholende Agent, sonst 403) oder
`X-Internal-Token`; Gerät mit Agent → `POST /jobs/collect` erzwingt `external`. Collector:
`Target`-DTO, `collect_once(run_id, target, via_agent)`, Agent liest **nie** Beispieldaten (ohne
Creds klarer Abbruch), `--check` prüft alle zugewiesenen Geräte, `run-agent.ps1 -Check` ohne
`--platform`-Zwang. UI: Quellsysteme-Dialog (Adresse, **Verwaltet von**, API-Token write-only, TLS,
aktiv), Spalten „Verwaltet von"/„Zugangsdaten"; Agentenliste mit Spalte **Geräte**; Anleitung mit
Schritt „Geräte zuweisen", `agent.env`-Generator nur noch Orchestrator + Agent-Token;
„Jetzt einlesen" leitet die Ausführung aus der Zuweisung ab (kein Umschalter). Migration 0016 up/down
gegen Postgres geprüft; Klick-Durchlauf per Playwright (Agent → Gerät → Lauf → Agent holt Target mit
Token, `/systems` ohne Token). Tests: orchestrator 126, collectors 33, sot-gateway 6.
