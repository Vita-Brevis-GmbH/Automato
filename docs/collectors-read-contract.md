# Collector-Lesevertrag — FortiGate, Cisco ACI, VMware, Switches & Router, NSX ALB (read-only)

> Was die Collectors **rein lesend** erfassen (Felder/Relationen) und wie es auf das
> Datenmodell (ADR 0004 + Ergänzung) abgebildet wird. Abgeleitet aus realen Samples einer Referenzumgebung
> (ACI-Tenant-Export `TE_share`, FortiGate-Config-Ausschnitte). **Die Sample-Dateien
> bleiben ausserhalb des Repos; Secrets werden nie gelesen/gespeichert/geloggt.**
> Bezug: `docs/naming-conventions.md`, `docs/usecases/uc1-applikations-doku-spec.md`,
> ADR 0006 (Objekttyp/Farbe-Signal), SECURITY.md (Least Privilege).

## Gemeinsame Regeln

- **Least Privilege / read-only** Ziel-Accounts.
- **Secrets strippen**: `psksecret`, `passwd`, jede `ENC …`-Zeichenkette, Keys/Zertifikate
  werden nicht übernommen und nie geloggt.
- **Dual-Stack**: IPv4 und IPv6 nebeneinander → `network_object.family` (v4|v6).
- **`color`** (FortiGate): als **numerischer Code** geführt und **optional** (nicht an
  jedem Objekt gesetzt) → Signal „wenn vorhanden", sonst Fallback Prefix/Typ.
- **Namenszuordnung** über das **Namensprofil pro Kunde** (ADR-0004-Ergänzung) mit
  Ausnahmen-Kaskade: Konvention → Typ/Farbe → manuell/Designer → sonst `finding`.

## 1. FortiGate (FortiOS REST, `cmdb/…`; read-only)

| Quelle (REST / CLI) | Schlüsselfelder | → Modell |
|---|---|---|
| `firewall/address` **und** `firewall/address6` (je Familie eigene Objekte, gleiche Namen erlaubt) | `name`, `type` (`ipmask`\|`iprange`\|`fqdn`\|`geography`\|`interface-subnet`\|`dynamic`), `subnet`/`start-ip`+`end-ip`/`fqdn`/`country`/`interface`, `ip6`, `color` (int, optional), `comment`; bei `dynamic` (0.36.0, SDN-Connector z. B. ACI über FortiManager) zusätzlich `sub-type`, `sdn`, `filter`, `obj-tag` als Attribute | `network_object` (`object_class` aus Prefix/Typ, `value`, `family` aus Sektion v4/v6, `fqdn`, `zone_id` später) |
| `firewall/addrgrp` **und** `firewall/addrgrp6` | `name`, `member[]` | Gruppe (rekursiv aufgelöst, Mitglieder in der eigenen Familie); freitext-benannte Vendor-Gruppen zulässig |
| `firewall.service/custom` | `name` (`FT_…`), `protocol`, `protocol-number`, `icmptype`/`icmpcode`, `tcp-`/`udp-`/`sctp-portrange` (inkl. Quellports nach `:`), `category`, `iprange`/`fqdn`, `session-ttl` und Timer, `helper`, `proxy` | `service_object` (`proto`, `port_start/end`; Ranges `20-21`; tcp+udp möglich); Attribute ohne Standardwerte in `device_object.attrs` |
| `firewall.service/group` | `name`, `member[]` | Service-Gruppe (`device_object`, Regel-Verweis per Name; Werte aufgelöst) |
| `firewall/vip` **und** `firewall/vip6` | `name` (`V-EXT_`/`VIP_`), `type`, `extip`, `mappedip`, `extport`, `mappedport`, `portforward`, `protocol`, `extintf`; **Virtual Server** (`type=server-load-balance`): `server-type`, `ldb-method`, `persistence`, `monitor[]`, `ssl-certificate` (nur Name), `realservers[]` (`ip`/`address`, `port`, `status`, `weight`, `healthcheck`, `monitor[]`, `holddown-interval`, `max-connections`, `http-host`) | `nat_rule` (`dnat_vip`, `vip_type`, `family`, `targets` = Real Server, `attrs`); **externer DNS-Name aus dem Objektnamen** |
| `firewall/vipgrp` **und** `firewall/vipgrp6` (optional, 0.96.0) | `name`, `member[]`, `interface`, `color`, `comments` | Adressgruppe (`device_object`, `obj_type` `vipgrp`, Werte = externe IPs der VIPs); Policies auf die Gruppe lösen in ihre VIPs auf (`vip:<VIP>=<extip>`) |
| `firewall/ldb-monitor` (optional) | `name`, `type`, `port`, `interval`, `timeout`, `retry`, `http-get`, `http-match` | Health-Checks im Virtual Server (`attrs.monitor`) |
| `firewall/ippool` | `name` (`IPP_`), `type` (`overload`\|`one-to-one`\|`fixed-port-range`\|`port-block-allocation`), `startip`, `endip`, `source-startip`/`source-endip` (nur `fixed-port-range`) | `nat_rule` (`snat_pool`, `family` v4, `attrs.type`, `attrs.source`) → Spalte „NAT Externe IPv4"; SNAT auf dem Weg (0.47.0) |
| `firewall/ippool6` (optional, 0.47.0) | `name`, `type`, `startip`, `endip` | `nat_rule` (`snat_pool`, `family` v6); Policies wählen ihn über `nat6`/`ippool6`/`poolname6`; seit 0.96.0 auch `device_object` (`ippool`, `family` v6) für die Namenskonvention |
| `firewall/policy` | `policyid`, `name`, `srcintf`, `dstintf`, `srcaddr`/`dstaddr` (gegen IPv4-Objekte) und `srcaddr6`/`dstaddr6` (gegen IPv6-Objekte, Verweise getrennt `source_refs6`/`destination_refs6`, Familie v4/v6/dual), **Internet Services** `internet-service[6][-src]-name/-group/-custom/-custom-group/-fortiguard` (→ `isdb:<Name>`, extern erwartet), **Identität** `users`/`groups`/`fsso-groups` (→ `user:`/`usergroup:`), ZTNA-Tags `ztna-ems-tag`/`ztna-geo-tag`, Negation `srcaddr-negate`/`dstaddr-negate`/`service-negate`/`internet-service[-src]-negate`, `service`, `action`, `nat`, `poolname`, `utm-status`, **UTM-Refs** `av-profile`/`webfilter-profile`/`dnsfilter-profile`/`application-list`/`ips-sensor`/`waf-profile`/`ssl-ssh-profile`, `schedule`, `comments` | `rule` (+ `rule_endpoint` v4/v6, `rule_service`); UTM-Refs → Spalte „Schutzmechanismen"; `comments` (enthält oft `CTASK…`/`CT_…`) → „Description" |
| `firewall/policy6` (nur FortiOS 6.x; ab 7.0 fehlt die Tabelle → übersprungen) | wie `firewall/policy`, `srcaddr`/`dstaddr` sind IPv6-Objekte | `rule` mit `external_id` `v6-<policyid>`, Familie v6 |
| `system/zone` | `name`, `interface[]` | `zone`; Zone je Interface im Netzinventar |
| `system/interface` | `name`, `type`, `ip`, `secondaryip[].ip`, `ip6`, `alias`, `role`, `vlanid`, `interface` (Parent), `member[]` (Aggregate), `status`, `vdom`, `vrf`, `dedicated-to` (nur für das Routing: VRF bzw. Management je Route) | Netzinventar (ADR 0010): `inv_interface` (Modus, VLAN, LAG/Parent, IPs; seit 0.54.0 `role` lan/wan/dmz bzw. management aus `dedicated-to` als Hinweis für die Zonen-Vorschläge), `inv_vlan`, `inv_subnet`; Zonen-/Subnetz-Ableitung |
| `system/global` (+ optional `monitor/system/status`) | `hostname`; Modell, Seriennummer, Version | `inv_device` (Firewall) |
| `system/vdom` (global, 0.15.0) | `name` (interne `vsys_*` ausgenommen) | Liste der VDOMs; danach alle Tabellen oben **je VDOM** |

**VDOMs (ADR 0019, 0.15.0):** Der Collector liest zuerst `system/vdom` und danach jede Tabelle
je VDOM mit dem Parameter `vdom=<name>`. Regeln, Objekte, VIPs/IP-Pools und Zonen tragen ihren
VDOM; Policy-ID und Objektname gelten je VDOM (gleiche Namen in zwei VDOMs = zwei Objekte).
`system/interface` ist global – jedes Interface nennt seinen `vdom`; die Zone gilt in diesem
VDOM. Zum Konto:

- Das REST-API-Konto braucht ein **Read-only-Profil mit Zugriff auf alle VDOMs** (globaler
  Bereich), sonst fehlen VDOMs.
- Ohne Recht auf `system/vdom` (VDOM-Admin) liest der Collector nur den Standard-VDOM des
  Kontos; dessen Name kommt aus der Antwort.
- Ein VDOM ohne Leserecht (HTTP 403) fehlt; die Lauf-Meldung nennt ihn („VDOM nicht lesbar:
  …“). Ist keiner lesbar, schlägt der Lauf fehl.
- Mit mehreren VDOMs nennt die Lauf-Meldung die gelesenen („VDOMs: root, …“).

Objekte (Adressen, Gruppen, VIPs, IP-Pools, Services, Service-Gruppen) werden zusätzlich **mit
Namen** als `device_object` gespeichert, Regeln verweisen per Name darauf (`rule_object_ref`,
ADR 0010) – die Doku zeigt die Namen wie auf der Firewall, aufgelöst bis zum Wert.

**Routing, BGP, HA (ADR 0019 Stufe 2, 0.16.0)** – je VDOM, nur lesend, Felder per
Positivliste (Passwörter, Pre-Shared-Keys und Schlüssel kommen nie mit):

| Quelle | Felder | → Modell |
|---|---|---|
| Monitor `router/ipv4`, `router/ipv6` | `ip_mask`, `type`, `distance`, `metric`, `priority`, `gateway`, `interface`, `vrf` | `topo_route` `rib` (je Präfix/Protokoll/Distanz/VRF, mehrere Gateways = ECMP; `attrs.vrf`, `attrs.mgmt` bei Management-Interfaces) |
| Monitor `router/bgp/paths`, `paths6` | `nlri_prefix`/`_len`, `next_hop`, `learned_from`, `is_best`, `as_path`, `local_pref`, `med`, `origin`, `weight` | `topo_route` `bgp` (auch nicht beste Pfade = Backup-Wege) |
| Monitor `router/bgp/neighbors`, `neighbors6` | `neighbor_ip`, `local_ip`, `remote_as`, `state`, `admin_status` | `topo_peer` |
| `router/static`, `static6` | `seq-num`, `dst`, `gateway`, `device`, `distance`, `priority`, `status`, `blackhole`, `comment` | `topo_route` `static` (auch inaktive bzw. mit höherer Distanz) |
| `router/policy`, `policy6` | `seq-num`, `input-device`, `src`/`srcaddr`, `dst`/`dstaddr`, `protocol`, `start-`/`end-port`, `gateway`, `output-device`, `status` | `topo_policy_route` `policy` |
| `system/sdwan` | `service[]` (`id`, `name`, `mode`, `dst`, `src`, `priority-members`), `members[]` (`interface`, `gateway`, `zone`) | `topo_policy_route` `sdwan` (Mitglieder nach Vorrang) |
| `system/ha` (global), Monitor `system/ha-peer` | `mode`, `group-name`, `group-id`, `session-pickup*`, `priority`, `override`; Mitglieder `hostname`, `serial_no`, `priority`, Rolle | `topo_ha` – **ohne** `password` |
| `system/standalone-cluster` (ab 7.2) bzw. `system/cluster-sync` | `cluster-peer[]`: `sync-id`, `peervd`, `peerip`, `syncvd`; Gruppe/Mitglied | `topo_ha.sync_peers` (FGSP) – **ohne** `psksecret` |

Monitor-Listen liest der Collector seitenweise (`start`/`count`), wenn die FortiGate kürzt;
mehr als 50 000 Einträge je Tabelle werden nicht übernommen (Meldung „zu gross“). Fehlt das
Leserecht (401/403), steht die Tabelle in der Lauf-Meldung („Routing nicht lesbar: …“) und im
Kontext als nicht lesbar; fehlt die Tabelle (Version, Funktion aus), bleibt sie leer. Das
Read-only-Profil braucht Lesezugriff auf **Router** und **System** (inkl. Monitor).

**Nicht gelesen:** `vpn ipsec …` (`psksecret`!), die BGP-Konfiguration `router bgp` (Passwörter
der Nachbarn) – BGP kommt nur aus dem Monitor-API.

**Beobachtete Zusatz-Prefixe** (→ Wörterbuch/Kaskade): `N-EXT_` (externes Netz),
`T_` (Transfernetz), bare-name `geography` (`type geography`, `country` → `not_applicable`),
freitext-Adressgruppen (Vendor-Ranges → Mitglieder `expected_external`). Reale
UTM-Profil-Prefixe: `AV_`/`WF_`/`DF_`/`AC_`/`IPS_`/`WAF_`/`SSL_` (nur **Referenznamen**
lesen, nicht die Profil-Innereien).

## 2. Cisco ACI (APIC REST, MO-Subtree je Tenant; read-only)

Abruf (umgesetzt, `collectors/cisco_aci/client.py`): `POST /api/aaaLogin.json` (read-only
APIC-Benutzer aus dem Quellsystem, ADR 0009) → `GET /api/class/fvTenant.json` → je Tenant
`GET /api/mo/uni/tn-<TE>.json?rsp-subtree=full` (verschachtelt wie der Tenant-Export; nicht
`query-target=subtree`, das liefert eine flache Liste ohne Eltern-Kind-Bezug) →
`POST /api/aaaLogout.json`. Tenants laut Quellsystem-Feld „ACI-Tenants“, leer = alle ausser
`infra`/`mgmt`. Struktur je MO: `{ "<class>": { "attributes": {…}, "children": […] } }`.

| MO-Klasse | Schlüssel/Relationen | → Modell |
|---|---|---|
| `fvTenant` | `name` (`TE_…`), `descr` | Mandant (Gruppengesellschaft) |
| `fvCtx` | `name` (`VRF_…`); children `leakRoutes` → `leakInternalSubnet.ip` bzw. `leakExternalPrefix.ip` → `leakTo.tenantName`/`ctxName` (Route-Leaking, ACI 5.0+, 0.44.0) | VRF; `inv_vrf.leaks` |
| `fvBD` | `name` (`BD_…`); children `fvRsCtx.tnFvCtxName`→VRF (aufgelöst über `fvRsCtx.tDn` → Tenant der VRF, eigener bzw. `common`, 0.44.0), `fvSubnet.ip` (**dual-stack**) und `fvSubnet.scope` (`private`/`public`/`shared` – `shared` = zwischen VRFs geteilt, Shared Services, 0.44.0), `fvRsBDToOut.tnL3extOutName` (Subnetze werden über diesen L3Out nach aussen bekannt gegeben – **keine** Zone) | `epg_subnet` (origin `bd`) |
| `fvAp` | `name` (`AP_###`) | `application` (Projekt-Klammer) |
| `fvAEPg` | `name` (`EG_…`), `prefGrMemb`; children `fvRsBd.tnFvBDName`→BD, `fvRsProv.tnVzBrCPName`/`fvRsCons.tnVzBrCPName`→Contracts, `fvRsIntraEpg.tnVzBrCPName`→Intra-EPG-Contract (Consumer *und* Provider), `fvRsSecInherited.tDn`→Contract Master (erbt Provider/Consumer, eine Stufe), `fvRsDomAtt.tDn`→Domäne, `fvRsPathAtt`→Static Paths, `fvSubnet` (mit `scope`, 0.44.0) | `epg` (+ `epg_subnet`, `epg.vrf_tenant`); ein Server = genau eine EPG |
| `vzBrCP` | `name` (`CT_…`), **`scope`** (`global`\|`tenant`\|`context`\|`application-profile`); children `vzSubj` (`SJ_…`) → `vzRsSubjFiltAtt.tnVzFilterName`→Filter | `rule` (ACI-Contract); **`scope` steuert Applikations-Doku-Kategorie** (application-profile→innerhalb AP; context/tenant→innerhalb Tenant; global→andere Tenants) |
| `vzFilter` / `vzEntry` | Filter `FT_…`; Entry `name`, `etherT`, `prot`, `dFromPort`/`dToPort`, `sFromPort`/`sToPort`, `stateful`, `applyToFrag`, `tcpRules`, `icmpv4T`/`icmpv6T` | `service_object`; zusätzlich Service-Objekt `<Tenant>/<FT_…>` (`device_object`, `attrs.entries`), Regeln verweisen per `service_refs` |
| `vzAny` | `vzRsAnyToProv`/`…Cons`, `prefGrMemb` (`enabled` = Preferred Group der VRF an) | VRF-weiter „any"-Contract; Preferred Group → eine Regel je VRF |
| `fvESg` | `name` (ESG), `prefGrMemb`; children `fvRsScope.tnFvCtxName`→VRF, `fvRsProv`/`fvRsCons`, Selektoren `fvEPgSelector.matchEpgDn`, `fvTagSelector.matchKey/matchValue`, `fvEPSelector.matchExpression` (`ip=='…'` → Subnetz) | `epg` mit `kind=esg`, `selectors`; Subnetze origin `esg` |
| `vzTaboo` | `name`; children `vzTSubj` → `vzRsDenyRule.tnVzFilterName`; geschützte EPGs per `fvRsProtBy.tnVzTabooName` | `rule` mit `action=deny`, Quelle any, Ziel = geschützte EPGs |
| `l3extOut` | `name` (`L3O_inet/vpn/bit/res/sra`); children `l3extInstP` (externe EPG, `EN_…`) → `l3extSubnet.ip` (z. B. `0.0.0.0/0`, `::/0`) und **`scope`** (`import-security` = Zuordnung zur EPG, `export-rtctrl` … = nur Routing; fehlt = APIC-Standard, 0.28.0) | `zone` (externer Scope) / Transfernetz; `epg_subnet.scope` |

**Netzinventar (ADR 0010):** zusätzlich zum Tenant-Subtree liest der Collector die Fabric-Klassen
`fabricNode` (Leaf/Spine/Controller mit Modell/Seriennummer), Access Policies
(`infraRsAccPortP`, `infraNodeBlk`, `infraPortBlk`, `infraRsAccBaseGrp`, `infraAccBndlGrp` →
Ports zu Policy-Gruppen/PC/vPC), `l1PhysIf` (Port-Status/Beschreibung) und `lldpAdjEp`/`cdpAdjEp`
(Nachbarn) per `GET /api/class/<klasse>.json`; seit 0.69.1 auch `vlanCktEp` mit den Kindern
`l2RsPathDomAtt` (`rsp-subtree=children`: die auf den Leafs **ausgerollten VLANs** mit EPG
(`epgDn`) und Ports – auch über AAEP und VMM-Domänen, nicht nur statische Pfade) und `pcAggrIf`
mit `pcRsMbrIfs` (Port-Channel des Leafs `po<n>` → Policy Group, Mitglieder). Aus den EPGs: `fvRsPathAtt` (statische Pfade,
`encap` → VLAN, Modus) und `fvCEp.encap`; Endpunkte aus `fvCEp`/`fvIp` (mit `lcC`: static, vmm, gelernt) und statische Endpunkte `fvStCEp`/`fvStIp` (0.46.0, Konfiguration vor Gelerntem); aus BD/EPG: `fvSubnet` + `fvRsCtx` → reale Subnetze mit VRF.

**ACI-Regel-Rekonstruktion:** eine EPG↔EPG-Verbindung ergibt sich aus Provider
(`fvRsProv`) + Consumer (`fvRsCons`) desselben `vzBrCP`; die Ports kommen aus den
`vzFilter`/`vzEntry` der `vzSubj`. Eine **Zone (L3Out)** hat eine Contract-Seite nur, wenn
sie eine externe EPG (`l3extInstP`) ist. BD→L3Out (`fvRsBDToOut`) bedeutet lediglich, dass
die Subnetze der BD nach aussen bekannt gegeben werden; Verkehr zwischen EPGs läuft nicht
über den L3Out (bis 0.4.0 fälschlich als Zone geführt).

**Identität und Auflösung (seit 0.4.0):** Contracts, EPGs, BDs und Filter werden über die
**DN** geführt – dieselben Namen gibt es in vielen ACI-Tenants. Namensverweise
(`tnVzBrCPName`, `tnFvBDName`, `tnVzFilterName`) löst der Collector wie der APIC auf:
**eigener Tenant, sonst `common`**. Consumer/Provider sind neben Anwendungs-EPGs auch
**externe EPGs** (`l3extInstP` mit `fvRsProv`/`fvRsCons`, Subnetze aus `l3extSubnet`,
VRF aus `l3extRsEctx`) und **vzAny** einer VRF (`vzRsAnyToProv`/`vzRsAnyToCons`).
Exportierte Contracts eines anderen Tenants kommen über Contract-Interfaces (`vzCPIf` +
`vzRsIf.tDn`, verbraucht per `fvRsConsIf`/`vzRsAnyToConsIf`). Nicht-reflexive Subjects
liefern ihre Filter aus `vzInTerm`/`vzOutTerm` (`vzRsFiltAtt`).

**Richtung der Contracts (seit 0.69.1, ACI ist zustandslos):** je Subject gelesen werden die
Filter mit „Apply Both Directions“ (`vzRsSubjFiltAtt`), `vzInTerm` (nur Consumer → Provider) und
`vzOutTerm` (nur Provider → Consumer), dazu `vzSubj.revFltPorts` und an den `vzEntry`
`sFromPort`/`dFromPort`, `stateful`, `tcpRules`. Die Regel trägt die Dienste Consumer → Provider;
`raw.reverse` die Dienste, die der Provider zum Consumer **aufbauen** kann: Filter ohne Ports
(`ip`, `tcp`/`udp` ohne Port, ICMP) gelten in beide Richtungen, mit Zielport (tcp/443) zurück nur
Antworten (Quellport 443), `stateful` bzw. `tcpRules` ohne SYN ebenso. Vor 0.69.1 eingelesene
Contracts: Automato leitet die Rückrichtung aus den Filtern ohne Ports ab (Standard des APIC).

**Seit 0.5.0:** **ESGs** (`fvESg`) sind Consumer/Provider wie EPGs; ihre Selektoren stehen als
Text an der ESG (`EPG <DN>`, `Tag k=v`, `IP <Ausdruck>`), IP-Selektoren zusätzlich als
Subnetz. **Taboo-Contracts** werden zu Deny-Regeln: jede Quelle, Ziel = die per `fvRsProtBy`
geschützten EPGs, Ports aus `vzRsDenyRule`. **Preferred Group:** ist sie am vzAny einer VRF
eingeschaltet (`prefGrMemb=enabled`), sprechen alle Mitglieder (`prefGrMemb=include` an EPG,
ESG oder externer EPG der VRF) frei miteinander – als eine Regel je VRF mit `ip/any`. Die
Regeln tragen `kind` (`contract` | `taboo` | `preferred_group`) im Rohdatensatz.

**Beobachtete Abweichungen** (Ausnahmen-Kaskade): neben den 7 Standard-EPGs auch
`EG_nfs01`, `EG_privat01`; Dual-Stack-Subnetze real.

**L3Out und Border-Leafs (ADR 0019 Stufe 2, 0.16.0):** aus dem gelesenen Tenant-Baum je
`l3extOut`: VRF (`l3extRsEctx`, sonst `common`), Border-Leafs (`l3extRsNodeL3OutAtt`: `tDn`,
`rtrId`; statische Routen `ipRouteP`/`ipNexthopP`), Interfaces bzw. SVIs
(`l3extRsPathL3OutAtt`: `tDn`, `ifInstT`, `encap`, `addr`, `mtu`; vPC-Seiten `l3extMember`,
weitere Adressen `l3extIp`), BGP-Peers (`bgpPeerP` `addr` mit `bgpAsP` `asn` – **ohne**
`password`) und externe EPGs → `topo_l3out`. Dazu je Border-Leaf und VRF (nur lesend):

- `GET /api/node/mo/topology/pod-<p>/node-<n>/sys/uribv4/dom-<Tenant>:<VRF>.json` mit
  `query-target=subtree&target-subtree-class=uribv4Route,uribv4Nexthop` (IPv6: `uribv6…`) →
  `topo_route` `rib` (Next-Hops je Route = ECMP; `owner` → Protokoll, `pref` → Distanz);
- `…/sys/bgp/inst/dom-<Tenant>:<VRF>.json` mit `bgpPeer,bgpPeerEntry` → `topo_peer` (`operSt`).

Der APIC-Benutzer braucht dafür Lesezugriff auf die Fabric-Knoten (`topology`); fehlt er,
steht die Tabelle in der Lauf-Meldung.

## 3. Referenzquellen (ADR 0010; komplett lesen, read-only)

| Quelle | gelesen | → Referenz-Eintrag |
|---|---|---|
| Infoblox WAPI (`/wapi/v2.x/…`, seitenweise `_paging`) | `network`, `ipv6network`, seit 0.74.1 `networkcontainer`, `ipv6networkcontainer` (Status `container`), `range`, `record:host`, `record:a`, `record:aaaa`, `record:ptr`, `fixedaddress` (+ Extensible Attributes Site/VLAN) | `reference_record` (prefix, range, host, a/ptr, fixed_address; DNS-Name, Beschreibung, VLAN, Site) |
| NetBox REST (`/api/ipam/…`, seitenweise) | `prefixes`, `ip-addresses`, `ip-ranges`, `vlans` (+ Tenant, Scope/Site, Tags, Custom Fields, Link; seit 0.54.0 die Rolle des Prefixes als Merkmal `role`) | `reference_record` (prefix, ip_address, range, vlan) |

Der Agent lädt den Stand in Teilen hoch (`POST /internal/collector-runs/{id}/references`); erst
das Ergebnis mit passender Anzahl macht ihn zum aktuellen Stand der Quelle.

## 4. VMware vCenter (pyVmomi, PropertyCollector; read-only, ADR 0017)

Konto mit der vSphere-Rolle **Read-only** auf dem vCenter-Objekt oder als globale Berechtigung,
jeweils an untergeordnete Objekte weitergegeben (der Lauf braucht `System.View` ab dem
Wurzelordner `group-d1`). Der Collector lässt nur die
lesenden Methoden `Login`, `Logout`, `RetrieveServiceContent`, `CurrentTime`,
`CreateContainerView`, `DestroyView` und `RetrieveProperties(Ex)`/`ContinueRetrievePropertiesEx`
zu – geprüft wird der WSDL-Name, jeder andere Aufruf bricht vor dem Senden ab
(`ReadOnlyViolation`). Container-Views sind sitzungsgebunden und werden nach dem Lesen entfernt.

| Objekt | gelesen | → Automato |
|---|---|---|
| `VirtualMachine` | Name, `instanceUuid`, Power-State, Vorlage, Gast-OS, Gast-Hostname, vCPU, RAM, Disk (Summe der virtuellen Disks), Notiz, Host; NICs mit MAC, Label, verbunden, Backing (Portgruppe); `guest.net` (IPs je NIC, ohne Link-Local) | `vm`, `vm_nic`, `vm_ip` |
| `HostSystem` | Name, Eltern, `config.network.portgroup` (Standard-Portgruppen mit VLAN und vSwitch) | Host an der VM, `portgroup` (`standard`) |
| `ComputeResource` (Cluster), `Folder`, `Datacenter` | Name, Eltern | Cluster und Datacenter an der VM |
| `DistributedVirtualPortgroup`, `DistributedVirtualSwitch` | Name, Key, Switch, VLAN (einzeln, Trunk-Bereiche, PVLAN) | `portgroup` (`dvs`) |

Nicht gelesen: Zugangsdaten, Gast-Inhalte, Leistungsdaten, Snapshots, Ereignisse.
Portgruppe → EPG ordnet der Orchestrator zu (Name `Tenant|AP|EPG`, sonst eindeutiges VLAN).

## 5. Switches & Router (SSH, `show`-Befehle; read-only, ADR 0025)

Ein Quellsystem `cisco_switch` ist eine Gruppe von Switches (Adressen im Lesebereich, eine je
Zeile, `host` bzw. `host:port`) mit einem Konto, das nur lesen darf: IOS/IOS-XE
**privilege 1** (bzw. eine Parser-View mit den Befehlen unten), NX-OS Rolle
**`network-operator`**. Je Befehl ein SSH-Exec-Aufruf, keine interaktive Shell, kein Terminal,
kein Konfigurationsmodus. Nur die Befehle der Tabelle sind zugelassen; jeder andere bricht vor
dem Senden ab (`ReadOnlyViolation`). **Kein** `show running-config` – so werden weder
Passwörter noch Schlüssel noch Community-Strings gelesen. Der SSH-Host-Schlüssel jeder Adresse
wird beim ersten Lauf festgehalten und danach verlangt (ADR 0025 Nr. 3).

| Befehl (IOS/IOS-XE · NX-OS) | gelesen | → Automato |
|---|---|---|
| `show version` | Hostname, Modell, Version, Seriennummer; erkennt die Plattform | `inv_device` (Rolle `switch`) |
| `show interfaces status` · `show interface status` | Port, Status, VLAN, Duplex, Speed, Medium | `inv_interface` (`status`, `speed`, `duplex`, `attrs.media`) |
| `show interfaces description` · `show interface description` | Beschreibung, Admin-Status | `inv_interface.description`, `enabled` |
| `show interfaces switchport` · `show interface switchport` | Modus Access/Trunk, Access-VLAN, Native VLAN, erlaubte VLANs | `mode`, `untagged_vlan`, `native_vlan`, `trunk_vlans` (Bereiche), `tagged_vlans` (bis 512) |
| `show vlan brief` | VLAN-ID, Name (ohne 1002–1005) | `inv_vlan` |
| `show etherchannel summary` · `show port-channel summary` | Port-Channel mit Mitgliedern, Protokoll | `inv_interface` (`kind` lag, `lag` am Mitglied) |
| `show cdp neighbors detail` | je Port Nachbar: Name, Port, IP, Plattform, Fähigkeiten | `inv_neighbor` (`protocol` cdp) |
| `show lldp neighbors detail` | je Port Nachbar: Systemname, Port, Management-IP, Beschreibung, Fähigkeiten | `inv_neighbor` (`protocol` lldp); am Port (`inv_interface.neighbor`) gilt CDP vor LLDP |
| `show vpc` (nur NX-OS) | vPC-Nummer je Port-Channel mit Status und Konsistenz; seit 0.65.0 auch Domäne, Rolle, Peer- und Keepalive-Status, Peer-Link (eigene Auswertung) | `inv_interface.attrs` (`vpc`, `vpc_status`, `vpc_consistency`; am Peer-Link `vpc_peer_link` = Domäne, `vpc_role`, `vpc_peer`, `vpc_keepalive`, `vpc_peer_link_status`) |
| `show spanning-tree` (seit 0.65.0) | je VLAN Port, Rolle, Zustand (auf NX-OS ohne passende Vorlage mit der IOS-Vorlage) | `inv_interface.attrs.stp_blocked` (VLANs mit `BLK` bzw. Alternate/Backup), `attrs.stp_root` (Root-Port), als Bereiche |
| `show mac address-table` (seit 0.63.0) | MAC, VLAN, Port, Art (dynamic/static); nur Unicast an einzelnen Ports, nicht CPU/Supervisor/Router/vPC-Peer-Link; höchstens 50 000 je Switch | `mac_entry` (gesammelt, `first_seen`/`last_seen`) |
| `show ip arp` · `show ip arp detail vrf all` (seit 0.63.0) | IP, MAC, L3-Interface, VRF (NX-OS); ohne die eigenen Adressen des Switches | `arp_entry` (gesammelt) |
| `show ipv6 neighbors` · `show ipv6 neighbor vrf all` (seit 0.94.0) | IPv6-Nachbarn (NDP): IPv6-Adresse ohne Zone, MAC, L3-Interface, VRF (NX-OS, eigene Auswertung); nur Unicast mit MAC, unvollständige Einträge fallen weg; fehlt der Befehl, bleibt der Teil ohne Hinweis leer | `arp_entry` (gesammelt wie ARP); Link-local (`fe80::/10`) bleibt, zählt aber nicht als Identität |

**Seit 0.68.0 weitere Produkte** (ADR 0025 Nr. 11), Plattform nach `show version`:

| Plattform | Geräte | Befehle | Konto |
|---|---|---|---|
| IOS/IOS-XE | Catalyst 9200L, 9300, 3650, 4500 | wie oben | privilege 1 bzw. Parser-View |
| NX-OS | Nexus 9000 | wie oben | `network-operator` |
| Business-CLI | Catalyst 1200/1300 (CBS) | `show version`, `show system`, `show inventory`, `show interfaces status`, `show interfaces description`, `show vlan`, `show interfaces port-channel`, `show lldp neighbors`, `show mac address-table`, `show arp`, seit 0.94.0 `show ipv6 neighbors all` – über die **begrenzte Sitzung** (kein Exec): Anmeldung in der Sitzung, nur diese Befehle und `terminal datadump` (Seitenumbruch der Sitzung aus) | Stufe 1 (nur lesen) |
| Junos | Juniper MX (Router), EX, QFX | `show version`; mit `\| display json \| no-more`: `show chassis hardware`, `show interfaces`, `show lldp neighbors`, `show vlans`, `show arp no-resolve`, `show ethernet-switching table`, `show bridge mac-table`, seit 0.94.0 `show ipv6 neighbors` | Klasse `read-only` |
| Arista EOS (seit 0.94.0) | Arista 7000er-Serie | `show version` (Text, erkennt die Plattform); mit `\| json`: `show version`, `show hostname`, `show interfaces status`, `show interfaces description`, `show interfaces switchport`, `show vlan`, `show port-channel`, `show lldp neighbors detail`, `show mac address-table`, `show ip arp vrf all`, `show ipv6 neighbors vrf all` | Rolle `network-operator` |
| AOS-CX (seit 0.94.0) | HPE Aruba CX 6000/8000 | `show version`, `show system`, `show interface`, `show vlan`, `show lldp neighbor-info detail`, `show mac-address-table`, `show arp all-vrfs`, `show ipv6 neighbors all-vrfs` (ntc-Vorlagen `aruba_aoscx`, IPv6-Nachbarn selbst ausgewertet); ohne Exec über die begrenzte Sitzung mit `no page` | Gruppe `operators` |

Gelesen werden dieselben Inhalte wie oben (Gerät, Ports mit Zustand/Speed/Duplex/VLANs/LAG,
Nachbarn, MAC, ARP, seit 0.94.0 IPv6-Nachbarn); bei Junos zusätzlich die Adressen der Interfaces
(`inv_interface.ips`) und ihre Netze (`inv_subnet`, Kontext Interface). Nicht gelesen:
Konfiguration, Routing-Tabelle, Zähler, MLAG der Arista. **ArubaOS-Switch/ProCurve** liest
Automato nicht (kein Exec, Sitzung nur mit Tastendruck und Bildschirmsteuerung): der Collector
erkennt das Banner „Press any key to continue“ und bricht ab, bevor er etwas sendet.

Meldet ein Befehl einen Fehler (z. B. LLDP ausgeschaltet) oder fehlt eine Vorlage, bleibt der
Teil leer und steht als Hinweis in der Meldung des Laufs. Nicht gelesen: Konfiguration,
Zugangsdaten, Logs, Zähler. MAC-, ARP- und IPv6-Nachbar-Einträge werden gesammelt und nach
`AUTOMATO_RETENTION_L2_DAYS` (30) Tagen ohne neue Sichtung gelöscht. Die Verkabelung leitet der Orchestrator
aus den Nachbarn aller Quellen ab (Switches, ACI-Leafs über `lldpAdjEp`/`cdpAdjEp`).

## 6. VMware NSX Advanced Load Balancer (Avi; REST, nur GET; ADR 0027)

Ein Quellsystem `avi` ist ein Controller (bzw. die Cluster-Adresse) mit einem Konto in einer
Rolle **nur mit Leserechten** (`READ_ACCESS`) auf die Objekte unten. Anmeldung per
`POST /login` (Sitzungs-Cookie), Abmelden mit `POST /logout` – die einzigen POST-Aufrufe; jeder
andere Schreibversuch bricht vor dem Senden ab (`ReadOnlyViolation`). Header `X-Avi-Version`
nach `GET /api/initial-data` (ohne Anmeldung). Je Avi-Tenant (Lesebereich, leer = alle, die das
Konto sieht, `GET /api/tenant`) mit `X-Avi-Tenant`; Seiten zu 200 (`include_name=true`), höchstens
20 000 Objekte je Art.

| Objekt (`/api/…`) | gelesen | → Automato |
|---|---|---|
| `virtualservice` | Name, Typ (normal, SNI-Parent/-Child), aktiv, Services (Port bzw. Bereich, SSL), Verweise auf VS VIP, Pool bzw. Pool Group, Application Profile, Cloud, VRF, Parent; HTTP-Richtlinien nur ob vorhanden | `nat_rule` (Art `dnat_vip`, Typ `server-load-balance`) je Virtual Service, VIP-Adresse und Port; `attrs` |
| `vsvip` | VIP-Adressen (IPv4, IPv6, Floating IP), aktiv, FQDN aus `dns_info`, VRF | `ext_ip`, `family`, `attrs.fqdn`/`floating_ip` |
| `pool` | Name, Server (IP bzw. Hostname, Port bzw. `default_server_port`, aktiv, Gewicht), Methode, Health-Monitor-Namen | `targets` (wie Real Server), `mapped_ip`/`mapped_port` = erster aktiver Server, `attrs.pool`/`lb_algorithm`/`health_monitors` |
| `poolgroup` | Mitglieder mit Priorität | Server aller Pools, höchste Priorität zuerst, `attrs.pool_group` |
| `vrfcontext` | Name | `attrs.vrf` |
| `applicationprofile` | Name, Typ, `preserve_client_ip` | `attrs.snat` (`service_engine` bzw. `client`) |
| `virtualservice-inventory` | Betriebszustand (`runtime.oper_status.state`) | `attrs.oper_status` |

Nicht gelesen: Zertifikate und Schlüssel (`sslkeyandcertificate`), Benutzer, Rollen,
Systemeinstellungen, Service Engines, Statistiken, Logs, DataScripts. Fehlende Rechte auf einen
Tenant bzw. ein Objekt stehen als Teilfehler in der Meldung des Laufs.

## Daraus folgende Modell-/Collector-Arbeiten (später, je Phase)

- FortiGate-Collector: `address6`/`addrgrp6` lesen, UTM-Refs + `ippool` + `color`
  (numerisch, optional) + externer VIP-DNS aufnehmen (Phase 1.5).
- ACI-Collector (Phase 2/Model 1): obiger MO-Subtree, Contract-`scope`→Kategorie,
  externe EPG→L3O (Zone), `epg_subnet`/`epg_endpoint` (akkumulierend).
