# Collector-Lesevertrag — FortiGate & Cisco ACI (read-only)

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
| `firewall/address` **und** `firewall/address6` (je Familie eigene Objekte, gleiche Namen erlaubt) | `name`, `type` (`ipmask`\|`iprange`\|`fqdn`\|`geography`\|`interface-subnet`), `subnet`/`start-ip`+`end-ip`/`fqdn`/`country`/`interface`, `ip6`, `color` (int, optional), `comment` | `network_object` (`object_class` aus Prefix/Typ, `value`, `family` aus Sektion v4/v6, `fqdn`, `zone_id` später) |
| `firewall/addrgrp` **und** `firewall/addrgrp6` | `name`, `member[]` | Gruppe (rekursiv aufgelöst, Mitglieder in der eigenen Familie); freitext-benannte Vendor-Gruppen zulässig |
| `firewall.service/custom` | `name` (`FT_…`), `protocol`, `protocol-number`, `icmptype`/`icmpcode`, `tcp-`/`udp-`/`sctp-portrange` (inkl. Quellports nach `:`), `category`, `iprange`/`fqdn`, `session-ttl` und Timer, `helper`, `proxy` | `service_object` (`proto`, `port_start/end`; Ranges `20-21`; tcp+udp möglich); Attribute ohne Standardwerte in `device_object.attrs` |
| `firewall.service/group` | `name`, `member[]` | Service-Gruppe (`device_object`, Regel-Verweis per Name; Werte aufgelöst) |
| `firewall/vip` **und** `firewall/vip6` | `name` (`V-EXT_`/`VIP_`), `type`, `extip`, `mappedip`, `extport`, `mappedport`, `portforward`, `protocol`, `extintf`; **Virtual Server** (`type=server-load-balance`): `server-type`, `ldb-method`, `persistence`, `monitor[]`, `ssl-certificate` (nur Name), `realservers[]` (`ip`/`address`, `port`, `status`, `weight`, `healthcheck`, `monitor[]`, `holddown-interval`, `max-connections`, `http-host`) | `nat_rule` (`dnat_vip`, `vip_type`, `family`, `targets` = Real Server, `attrs`); **externer DNS-Name aus dem Objektnamen** |
| `firewall/ldb-monitor` (optional) | `name`, `type`, `port`, `interval`, `timeout`, `retry`, `http-get`, `http-match` | Health-Checks im Virtual Server (`attrs.monitor`) |
| `firewall/ippool` | `name` (`IPP_`), `type`, `startip`, `endip` | `nat_rule` (`snat_pool`) → Spalte „NAT Externe IPv4" |
| `firewall/policy` | `policyid`, `name`, `srcintf`, `dstintf`, `srcaddr`/`dstaddr` (gegen IPv4-Objekte) und `srcaddr6`/`dstaddr6` (gegen IPv6-Objekte, Verweise getrennt `source_refs6`/`destination_refs6`, Familie v4/v6/dual), **Internet Services** `internet-service[6][-src]-name/-group/-custom/-custom-group/-fortiguard` (→ `isdb:<Name>`, extern erwartet), **Identität** `users`/`groups`/`fsso-groups` (→ `user:`/`usergroup:`), ZTNA-Tags `ztna-ems-tag`/`ztna-geo-tag`, Negation `srcaddr-negate`/`dstaddr-negate`/`service-negate`/`internet-service[-src]-negate`, `service`, `action`, `nat`, `poolname`, `utm-status`, **UTM-Refs** `av-profile`/`webfilter-profile`/`dnsfilter-profile`/`application-list`/`ips-sensor`/`waf-profile`/`ssl-ssh-profile`, `schedule`, `comments` | `rule` (+ `rule_endpoint` v4/v6, `rule_service`); UTM-Refs → Spalte „Schutzmechanismen"; `comments` (enthält oft `CTASK…`/`CT_…`) → „Description" |
| `firewall/policy6` (nur FortiOS 6.x; ab 7.0 fehlt die Tabelle → übersprungen) | wie `firewall/policy`, `srcaddr`/`dstaddr` sind IPv6-Objekte | `rule` mit `external_id` `v6-<policyid>`, Familie v6 |
| `system/zone` | `name`, `interface[]` | `zone`; Zone je Interface im Netzinventar |
| `system/interface` | `name`, `type`, `ip`, `ip6`, `alias`, `role`, `vlanid`, `interface` (Parent), `member[]` (Aggregate), `status`, `vdom` | Netzinventar (ADR 0010): `inv_interface` (Modus, VLAN, LAG/Parent, IPs), `inv_vlan`, `inv_subnet`; Zonen-/Subnetz-Ableitung |
| `system/global` (+ optional `monitor/system/status`) | `hostname`; Modell, Seriennummer, Version | `inv_device` (Firewall) |

Objekte (Adressen, Gruppen, VIPs, IP-Pools, Services, Service-Gruppen) werden zusätzlich **mit
Namen** als `device_object` gespeichert, Regeln verweisen per Name darauf (`rule_object_ref`,
ADR 0010) – die Doku zeigt die Namen wie auf der Firewall, aufgelöst bis zum Wert.

**Nicht für UC1** (erst Topologie/später, und dort Secrets strippen): `vpn ipsec …`
(`psksecret`!), `router bgp …`.

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
| `fvCtx` | `name` (`VRF_…`) | VRF |
| `fvBD` | `name` (`BD_…`); children `fvRsCtx.tnFvCtxName`→VRF, `fvSubnet.ip` (**dual-stack**), `fvRsBDToOut.tnL3extOutName` (Subnetze werden über diesen L3Out nach aussen bekannt gegeben – **keine** Zone) | `epg_subnet` (origin `bd`) |
| `fvAp` | `name` (`AP_###`) | `application` (Projekt-Klammer) |
| `fvAEPg` | `name` (`EG_…`), `prefGrMemb`; children `fvRsBd.tnFvBDName`→BD, `fvRsProv.tnVzBrCPName`/`fvRsCons.tnVzBrCPName`→Contracts, `fvRsIntraEpg.tnVzBrCPName`→Intra-EPG-Contract (Consumer *und* Provider), `fvRsSecInherited.tDn`→Contract Master (erbt Provider/Consumer, eine Stufe), `fvRsDomAtt.tDn`→Domäne, `fvRsPathAtt`→Static Paths, `fvSubnet` | `epg` (+ `epg_subnet`); ein Server = genau eine EPG |
| `vzBrCP` | `name` (`CT_…`), **`scope`** (`global`\|`tenant`\|`context`\|`application-profile`); children `vzSubj` (`SJ_…`) → `vzRsSubjFiltAtt.tnVzFilterName`→Filter | `rule` (ACI-Contract); **`scope` steuert Applikations-Doku-Kategorie** (application-profile→innerhalb AP; context/tenant→innerhalb Tenant; global→andere Tenants) |
| `vzFilter` / `vzEntry` | Filter `FT_…`; Entry `name`, `etherT`, `prot`, `dFromPort`/`dToPort`, `sFromPort`/`sToPort`, `stateful`, `applyToFrag`, `tcpRules`, `icmpv4T`/`icmpv6T` | `service_object`; zusätzlich Service-Objekt `<Tenant>/<FT_…>` (`device_object`, `attrs.entries`), Regeln verweisen per `service_refs` |
| `vzAny` | `vzRsAnyToProv`/`…Cons`, `prefGrMemb` (`enabled` = Preferred Group der VRF an) | VRF-weiter „any"-Contract; Preferred Group → eine Regel je VRF |
| `fvESg` | `name` (ESG), `prefGrMemb`; children `fvRsScope.tnFvCtxName`→VRF, `fvRsProv`/`fvRsCons`, Selektoren `fvEPgSelector.matchEpgDn`, `fvTagSelector.matchKey/matchValue`, `fvEPSelector.matchExpression` (`ip=='…'` → Subnetz) | `epg` mit `kind=esg`, `selectors`; Subnetze origin `esg` |
| `vzTaboo` | `name`; children `vzTSubj` → `vzRsDenyRule.tnVzFilterName`; geschützte EPGs per `fvRsProtBy.tnVzTabooName` | `rule` mit `action=deny`, Quelle any, Ziel = geschützte EPGs |
| `l3extOut` | `name` (`L3O_inet/vpn/bit/res/sra`); children `l3extInstP` (externe EPG, `EN_…`) → `l3extSubnet.ip` (z. B. `0.0.0.0/0`, `::/0`) | `zone` (externer Scope) / Transfernetz |

**Netzinventar (ADR 0010):** zusätzlich zum Tenant-Subtree liest der Collector die Fabric-Klassen
`fabricNode` (Leaf/Spine/Controller mit Modell/Seriennummer), Access Policies
(`infraRsAccPortP`, `infraNodeBlk`, `infraPortBlk`, `infraRsAccBaseGrp`, `infraAccBndlGrp` →
Ports zu Policy-Gruppen/PC/vPC), `l1PhysIf` (Port-Status/Beschreibung) und `lldpAdjEp`/`cdpAdjEp`
(Nachbarn) per `GET /api/class/<klasse>.json`. Aus den EPGs: `fvRsPathAtt` (statische Pfade,
`encap` → VLAN, Modus) und `fvCEp.encap`; aus BD/EPG: `fvSubnet` + `fvRsCtx` → reale Subnetze mit VRF.

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

**Seit 0.5.0:** **ESGs** (`fvESg`) sind Consumer/Provider wie EPGs; ihre Selektoren stehen als
Text an der ESG (`EPG <DN>`, `Tag k=v`, `IP <Ausdruck>`), IP-Selektoren zusätzlich als
Subnetz. **Taboo-Contracts** werden zu Deny-Regeln: jede Quelle, Ziel = die per `fvRsProtBy`
geschützten EPGs, Ports aus `vzRsDenyRule`. **Preferred Group:** ist sie am vzAny einer VRF
eingeschaltet (`prefGrMemb=enabled`), sprechen alle Mitglieder (`prefGrMemb=include` an EPG,
ESG oder externer EPG der VRF) frei miteinander – als eine Regel je VRF mit `ip/any`. Die
Regeln tragen `kind` (`contract` | `taboo` | `preferred_group`) im Rohdatensatz.

**Beobachtete Abweichungen** (Ausnahmen-Kaskade): neben den 7 Standard-EPGs auch
`EG_nfs01`, `EG_privat01`; Dual-Stack-Subnetze real.

## 3. Referenzquellen (ADR 0010; komplett lesen, read-only)

| Quelle | gelesen | → Referenz-Eintrag |
|---|---|---|
| Infoblox WAPI (`/wapi/v2.x/…`, seitenweise `_paging`) | `network`, `ipv6network`, `range`, `record:host`, `record:a`, `record:aaaa`, `record:ptr`, `fixedaddress` (+ Extensible Attributes Site/VLAN) | `reference_record` (prefix, range, host, a/ptr, fixed_address; DNS-Name, Beschreibung, VLAN, Site) |
| NetBox REST (`/api/ipam/…`, seitenweise) | `prefixes`, `ip-addresses`, `ip-ranges`, `vlans` (+ Tenant, Scope/Site, Tags, Custom Fields, Link) | `reference_record` (prefix, ip_address, range, vlan) |

Der Agent lädt den Stand in Teilen hoch (`POST /internal/collector-runs/{id}/references`); erst
das Ergebnis mit passender Anzahl macht ihn zum aktuellen Stand der Quelle.

## Daraus folgende Modell-/Collector-Arbeiten (später, je Phase)

- FortiGate-Collector: `address6`/`addrgrp6` lesen, UTM-Refs + `ippool` + `color`
  (numerisch, optional) + externer VIP-DNS aufnehmen (Phase 1.5).
- ACI-Collector (Phase 2/Model 1): obiger MO-Subtree, Contract-`scope`→Kategorie,
  externe EPG→L3O (Zone), `epg_subnet`/`epg_endpoint` (akkumulierend).
