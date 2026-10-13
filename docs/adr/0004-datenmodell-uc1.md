# ADR 0004: Datenmodell UC1 (Ist-Regel-Dokumentation, mandantenfähig)

## Status
Angenommen (Entwurf aus den Planungsrunden zu M1/M5). Das konkrete DDL/SQLAlchemy
folgt beim M5-Bau; dieser ADR fixiert Entitäten, Beziehungen und die tragenden
Entscheide.

## Kontext

UC1 dokumentiert Ist-Firewallregeln **read-only** über heterogene Umgebungen. Aus den
Planungsrunden sind zwei reale Kundenmodelle bekannt:

**Model 2 (zuerst umgesetzt – einfach):** FortiGate + Switches (nur VLAN-/L3-Switching,
keine ACLs), **NetBox** als IPAM, DNS = Active Directory. Subnetz-Gateway meist auf der
FortiGate; VPN mit statischem/dynamischem Routing, teils redundant. Namenskonvention
pro Kunde.

**Model 1 (danach – komplex):** FortiGate + Cisco ACI + Switches, **Infoblox** als
IPAM/DNS, ServiceNow (CMDB im Aufbau, **noch nicht** SoT – nur eine zu befüllende
Kopie). Kernmerkmale:
- ACI mit Microsegmentierung (mehrere EPGs pro Bridge Domain), mehrere Security-Tenants,
  strikte Namenskonvention. Vereinzelt Contracts zwischen verschiedenen Tenants.
- Contracts zu den Firewalls/L3Outs sind **vzAny** (offen) – die Enforcement macht die
  FortiGate. Es gibt 5 L3Outs mit festem Zweck: Internet, Management, DC-Ressourcen,
  Firmennetze, Kunden-VPN. Diese entsprechen den Zonen von der ACI-Seite.
- Mehrere Firewalls in Serie (DC-Zonen-FW → MPLS-Eingang → Aussenstandort → Zonen-FW).
- ACI lernt Endpunkt-IPs **dynamisch**: eine IP ist nur sichtbar, wenn der Host aus
  seinem Subnetz heraus sendet; schweigt er, ist er nach ~30 min nicht mehr bekannt.

Durchgehend: Whitelisting/Zero-Trust. IPs im DC und an Standorten homogenisiert
(keine Überschneidungen ausser vereinzeltem NAT an Aussenstandorten). Infoblox/NetBox
sind Autorität für Netz-/Host-Metadaten (Zone, VLAN, EPG-Hinterlegung).

## Entscheidung

### Tragende Prinzipien

1. **Mandantenfähig in einer Instanz.** `tenant` (Automato-Mandant) ist der oberste
   Scope und trägt seine **SoT-Bindung** (`ipam_type`, `dns_type`). So leben Model 1
   (Infoblox) und Model 2 (NetBox) in derselben Instanz; cross-tenant-Überblick und
   per-tenant-Sicht sind beide möglich (Sichtbarkeit erzwingt RBAC in M3). Ein
   Automato-Mandant kann mehrere ACI-Security-Tenants umfassen (`aci_tenant_names[]`);
   „Tenant" in ACI ≠ „Mandant" in Automato.
2. **Eine `rule`-Tabelle, Domäne per Diskriminator, polymorphe Endpunkte.**
   FortiGate-Policies und ACI-Contracts teilen dasselbe Kernmodell; ACI-Spezifika liegen
   als Zusatzfelder/JSONB an derselben Zeile, ohne ACI zu verbiegen.
3. **SoT ist Autorität für Metadaten.** Zone (am Subnetz), VLAN und Objekt-Attribute
   werden aus NetBox/Infoblox gelesen und gespiegelt – Automato pflegt sie nicht.
4. **ACI-Collector ist Autorität für IP↔EPG**, zweigleisig: stabil (`epg_subnet`,
   Config) als verlässliche Basis + volatil (`epg_endpoint`, gelernt, **akkumulierend
   gemergt**) gegen das 30-min-Aging.
5. **Namenskonvention ist ein First-Class-Signal** (Objektklasse, FQDN/Domain,
   Matching-Erwartung, Datenqualitätsprüfung) – siehe unten.
6. **Datenqualität/Abweichungen als eigenes `finding`-Konstrukt**, read-only befüllt und
   quittierbar. Schreibende Bereinigung erst im Schreibpfad.

### Entitäten (Überblick)

**Scoping**
- `tenant` — `name` (eindeutig, Namenskonvention), `ipam_type` (netbox|infoblox),
  `dns_type` (infoblox|active_directory), `aci_tenant_names[]`, `naming_convention`
  (per-Mandant-Parserregeln).
- `source_system` (bestehend) — Gerät, geräteneutral (eine FortiGate bedient mehrere
  Mandanten); Mandant hängt an der Regel, nicht am Gerät.

**Regeln**
- `rule` — `tenant_id`, `source_system_id`, `domain` (firewall|aci_contract),
  `external_id`, `name`, `action` (allow|deny), `sequence`, `section_label` (nullable,
  Band Zone 1–5, **pro Firewall**), `src_zone_id`, `dst_zone_id`, `last_seen`,
  `collector_run_id`, `raw` (jsonb). ACI-Zusatz: `contract_dn`, `subject`,
  provider/consumer, `is_inter_tenant`.
- `rule_endpoint` — `rule_id`, `role` (src|dst), `kind` (host|network|epg|zone|any|group),
  FK `network_object` **oder** `epg` **oder** `inline_value`, `matched` (bool).
- `rule_service` — `rule_id` ↔ `service_object`.

**Objekte, Zonen, EPG**
- `network_object` — `tenant_id`, `name` (roh), `object_class` (host|network|vip|range|
  external, aus Namenskonvention geparst), `type` (host|network|range, aus SoT),
  `value`, `family` (v4|v6), `fqdn`, `domain` (aus FQDN geparst → löst Mehr-AD-Situation),
  `zone_id` (aus SoT), `vlan`, `source_of_record` (netbox|infoblox|device|automato),
  `application_id` (nullable), `match_status` (matched|unmatched|ambiguous|
  expected_external), `last_seen`.
- `zone` — `tenant_id` (nullable: Internet/Management geteilt), `name` (DMZ, Server,
  Transfer, Edge, Office, Gäste, Partner, …). Mitgliedschaft via `network_object.zone_id`,
  gepflegt im SoT.
- `epg` — `tenant_id`, `aci_tenant`, `ap_profile`, `bd`, `full_name`
  (`gugus-000-012-dmz01`), `last_seen`.
- `epg_subnet` — `epg` ↔ `network_object` (stabile Config-Zuordnung).
- `epg_endpoint` — `epg_id`, `ip`, `mac`, `is_static` (fvStCEp vs. fvCEp),
  `first_seen`, `last_seen`. **Akkumulierend gemergt, nie ersetzt.**

**Services (global, dedupliziert)**
- `service_object` — `proto` (tcp|udp|icmp|icmpv6|ip), `port_start`, `port_end`,
  `icmp_type` (nullable), `canonical_name` (`tcp/443`). Dual-Stack via icmp/icmpv6 +
  family. TCP-Flags/`established` **nicht** gespeichert (aus Richtung ableitbar,
  Schreibpfad-Thema).

**NAT (Schema jetzt, Befüllung Phase 1.5)**
- `nat_rule` — `tenant_id`, `source_system_id`, `nat_type` (dnat_vip|waf|snat_pool|
  full_nat_overlap), `ext_address`, `ext_service`, `int_address`, `int_service`,
  `direction`, `rule_id` (nullable), `raw`. Internet-NAT wird geführt (`VIP_`-Objekte
  hängen hier an); Overlap-NAT nur lokal/minimal, möglichst nicht im SoT.

**Datenqualität / Reconciliation**
- `finding` — `tenant_id`, `type`, `severity` (info|warning|error), `status`
  (open|acknowledged|resolved|ignored), `subject_kind` (rule|network_object|epg),
  `subject_id`, `detail` (jsonb), `note`, `first_seen`, `last_seen`. Status-Änderungen
  laufen ins Audit-Log.
  Typen: `unmatched_object` (Name in Regel, nicht im SoT), `invalid_name` (passt nicht
  zur Konvention), `ambiguous_ip` (IP auf mehreren Objekten), `duplicate_value`,
  `name_type_mismatch` (Präfix `H_` vs. SoT-Typ Netzwerk), `epg_no_endpoints`.

**Vorgesehen, später befüllt**
- `application` — `tenant_id`, `name`; `network_object.application_id`. Quelle später via
  Namenskonvention bzw. die im Aufbau befindliche CMDB (ServiceNow ist heute nicht SoT).

### Namenskonvention (Model 1; per-Mandant konfigurierbar)

Präfixe → `object_class`: `H_` Host · `N_` Netzwerk · `VIP_` Virtual IP · `R_` Range ·
`H-EXT_` externe IPs/DNS-Namen. Der Objektname trägt den **FQDN** (`H_server.domain.info`);
daraus wird `domain` geparst und disambiguiert mehrere AD-Domänen pro Mandant.

**Matching-Erwartung je Klasse:** `H_`/`N_`/`VIP_`/`R_` sollen im IPAM auflösbar sein →
sonst `unmatched_object`. `H-EXT_` ist **bewusst extern** → „nicht im DDI" ist
`expected_external`, kein Fehler. Präfix ↔ SoT-Typ inkonsistent → `name_type_mismatch`.

Model 2 nutzt eine eigene, per-Mandant hinterlegte Konvention (Parser ist Konfiguration,
kein fest kodiertes Schema).

## Phasenplan (Umsetzung)

- **Phase 1 – Durchstich (M7): Model 2.** NetBox-Adapter, FortiGate-Collector,
  `rule`/`rule_endpoint`/`service_object`/`network_object`/`zone`/`finding`, Matching
  gegen NetBox. Kein ACI, kein NAT.
- **Phase 1.5:** NAT (`nat_rule` befüllen) inkl. `VIP_`-Objekte.
- **Phase 2 – Model 1:** Infoblox-Adapter, ACI-Collector (`epg`/`epg_subnet`/
  `epg_endpoint` akkumulierend), Namenskonvention-Parser strikt, Multi-AD-Domain,
  mehrere Firewalls in Serie.
- **Später (Schreibpfad):** Finding-Remediation (DDI-Objekt anlegen, Regel anpassen/
  ablehnen/löschen); ServiceNow/CMDB als Consumer der SoT-Daten.

## Konsequenzen

- **NetBox-Adapter zuerst** – kehrt die „Infoblox-first"-Notiz aus M1 um. Das
  Infoblox-Adapter-Skeleton bleibt und wird in Phase 2 fertiggestellt.
- `tenant_id` ist Querschnittsattribut; RBAC (M3) muss die per-Mandant-Sichtbarkeit
  erzwingen.
- `finding` macht Risiko R4 (Datenqualität) operativ handhabbar und ist read-only-sicher.
- ACI-Endpunkt-Volatilität ist als Risiko R12 geführt; abgefedert durch Akkumulation +
  stabile `epg_subnet`-Basis + regelmässige Collector-Läufe.
- Historisierung UC1: aktueller Stand + `last_seen` überall; vollständige
  Änderungshistorie bleibt vertagt (die ACI-Endpunkt-Akkumulation ist der einzige Teil,
  der schon jetzt Zeitverlauf braucht).
- Persistenz Rule Store: DB (Postgres) als führendes System; Git-Sync des Rule Store
  (offener Punkt aus ADR 0002) ist für die reine Ist-Dokumentation nicht nötig und wird
  erst mit den Rule-**Intents** (Schreibpfad) entschieden.

## Ergänzung 2026-10-13 — reale SOP-Namenskonvention & Mandanten-Modell

Nach Erhalt der produktiven Namenskonvention (`docs/naming-conventions.md`, Profil „SOP")
und der Ziel-Ausgabe (`docs/usecases/uc1-applikations-doku-spec.md`) werden folgende
Punkte präzisiert; Bezug ADR 0006 (Objekttyp/Farbe-Signal), ADR 0007 (AuthN/RBAC).

**E1 — Zwei Ebenen: Kunde/Umgebung über Mandant.** Prinzip 1 wird verfeinert:
- **Mandant = Gruppengesellschaft (GG)** = ein ACI-Tenant `TE_<gg>` + `VRF_<gg>`; die
  Scoping-Einheit für `viewer`/`operator`.
- **Neue Ebene `customer`/`environment`** (z. B. SOP) **über** dem Mandanten: trägt die
  **SoT-Bindung** (NetBox/Infoblox) **und das Namensprofil**. Andere Kunden = eigene
  Umgebung, eigene NetBox, eigenes (ähnliches) Profil. `ipam_type`/`dns_type`/
  `naming_convention` wandern damit von `tenant` auf `customer`.
- **Übergreifende Tenants** (`TE_res01`, `TE_soprz01`, `share`) = Mandanten mit Flag
  „übergreifend/shared".
- **Global-Admin-Gesamtsicht**: ein Scope (RBAC, ADR 0007), keine Datenänderung — die UI
  bietet eine „Alle Mandanten"-Sicht im selben Kontext/derselben NetBox.

**E2 — Namensprofil als austauschbare Komponente.** Der Parser ist nicht hartkodiert,
sondern ein **Profil pro Kunde** (Adapter-Prinzip wie SoT-Gateway); SOP ist das erste
Profil. Gemeinsame Basis-Grammatik (Prefix→Typ) + kundenspezifische Overrides.

**E3 — Ausnahmen-Kaskade (erstklassig).** Einordnung läuft: (1) Konvention → (2) Objekttyp
/ FortiGate-**Farbcode** → (3) manuell/Designer-Override → (4) sonst `finding`. „Es gibt
immer Ausnahmen" ist damit Teil des Modells, nicht ein Sonderfall. Das `color`-Signal
(SOP/Swisscom/MPLS/VPN/Extern × Host/Network) wird am `network_object` als **numerischer
Code** und **optional** mitgeführt (nicht jedes Objekt hat eine Farbe → Fallback
Prefix/Typ). Feld-Mapping der Collectors: `docs/collectors-read-contract.md`.

**E4 — Applikation = System, mit AP/Projekt-Klammer.** `application` wird **systemzentriert**
genutzt: eine Applikation ist am Ende ein Container/VM/Server (`network_object` host).
Gruppierungsschlüssel ist `AP_<projektnr>` (ACI) bzw. die 3-stellige Projektnummer aus dem
Namen (Model 2). Die Zuordnung gilt „zu ~90 %" über `AP_`/Projektnummer, mit
Override-Pfad (Designer) für den Rest.

**E5 — Zusätzliche Collector-Felder** (für die Applikations-Doku, siehe Spec):
- **UTM-Schutzprofile** pro FortiGate-Policy (`webfilter`/`application-list`/`ips-sensor`/
  `waf`/`ssl-ssh-profile`/`av`) → am `rule`.
- **Source-NAT IP-Pools** (`IPP_`) → `nat_rule.nat_type=snat_pool` (Schema vorhanden).
- **Externer DNS-Name** aus `V-EXT_`-Objektnamen → am `nat_rule` (dnat_vip).

**E6 — Zonen/Transfernetze aus ACI.** Die `L3O_`-Outputs (`inet`/`res`/`vpn`/`bit`/`sra`)
sind Zonen vom ACI-Rand und steuern die Kategorisierung der Applikations-Doku
(Tabellen 1–10 der Spec). ACI-Hierarchie: `TE_` → `VRF_` → `BD_` (→ `zone`) → `EG_`
(→ `epg`), Contracts `CT_/SJ_/FT_` → `rule`/`rule_service`/`service_object`.

Die Präfix-Liste aus Prinzip „Namenskonvention" wird durch `docs/naming-conventions.md`
ersetzt/erweitert (u. a. `IPP_`, `V-EXT_`, UTM-Profile, ACI-/LB-/PBR-Präfixe). Die
Parser-Wörterbücher (Kapitel 3.1 GG-Abkürzungen, 3.2 Projektnummern/Funktionscodes) und
Struktur-Samples sind noch nachzureichen.
