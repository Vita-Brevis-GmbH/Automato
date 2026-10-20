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
| `firewall/address` **und** `firewall/address6` | `name`, `type` (`ipmask`\|`iprange`\|`fqdn`\|`geography`\|`interface-subnet`), `subnet`/`start-ip`+`end-ip`/`fqdn`/`country`/`interface`, `ip6`, `color` (int, optional), `comment` | `network_object` (`object_class` aus Prefix/Typ, `value`, `family` aus Sektion v4/v6, `fqdn`, `zone_id` später) |
| `firewall/addrgrp` **und** `firewall/addrgrp6` | `name`, `member[]` | Gruppe (rekursiv aufgelöst); freitext-benannte Vendor-Gruppen zulässig |
| `firewall.service/custom` | `name` (`FT_…`), `protocol`, `tcp-portrange`, `udp-portrange`, `category` | `service_object` (`proto`, `port_start/end`; Ranges `20-21`; tcp+udp möglich) |
| `firewall.service/group` | `name`, `member[]` | Service-Gruppe (`device_object`, Regel-Verweis per Name; Werte aufgelöst) |
| `firewall/vip` | `name` (`V-EXT_`/`VIP_`), `extip`, `mappedip`, `extport`, `mappedport`, `portforward`, `protocol`, `extintf` | `nat_rule` (`dnat_vip`); **externer DNS-Name aus dem Objektnamen** |
| `firewall/ippool` | `name` (`IPP_`), `type`, `startip`, `endip` | `nat_rule` (`snat_pool`) → Spalte „NAT Externe IPv4" |
| `firewall/policy` | `policyid`, `name`, `srcintf`, `dstintf`, `srcaddr`+`srcaddr6`, `dstaddr`+`dstaddr6`, `service`, `action`, `nat`, `poolname`, `utm-status`, **UTM-Refs** `av-profile`/`webfilter-profile`/`dnsfilter-profile`/`application-list`/`ips-sensor`/`waf-profile`/`ssl-ssh-profile`, `schedule`, `comments` | `rule` (+ `rule_endpoint` v4/v6, `rule_service`); UTM-Refs → Spalte „Schutzmechanismen"; `comments` (enthält oft `CTASK…`/`CT_…`) → „Description" |
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
| `fvBD` | `name` (`BD_…`); children `fvRsCtx.tnFvCtxName`→VRF, `fvSubnet.ip` (**dual-stack**), `fvRsBDToOut.tnL3extOutName`→**L3O** | `zone` |
| `fvAp` | `name` (`AP_###`) | `application` (Projekt-Klammer) |
| `fvAEPg` | `name` (`EG_…`), `prefGrMemb`; children `fvRsBd.tnFvBDName`→BD, `fvRsProv.tnVzBrCPName`/`fvRsCons.tnVzBrCPName`→Contracts, `fvRsDomAtt.tDn`→Domäne, `fvRsPathAtt`→Static Paths, `fvSubnet` | `epg` (+ `epg_subnet`); ein Server = genau eine EPG |
| `vzBrCP` | `name` (`CT_…`), **`scope`** (`global`\|`tenant`\|`context`\|`application-profile`); children `vzSubj` (`SJ_…`) → `vzRsSubjFiltAtt.tnVzFilterName`→Filter | `rule` (ACI-Contract); **`scope` steuert Applikations-Doku-Kategorie** (application-profile→innerhalb AP; context/tenant→innerhalb Tenant; global→andere Tenants) |
| `vzFilter` / `vzEntry` | Filter `FT_…`; Entry `etherT`, `prot`, `dFromPort`/`dToPort`, `icmpv4T`/`icmpv6T` | `service_object` |
| `vzAny` | `vzRsAnyToProv`/`…Cons` | VRF-weiter „any"-Contract |
| `l3extOut` | `name` (`L3O_inet/vpn/bit/res/sra`); children `l3extInstP` (externe EPG, `EN_…`) → `l3extSubnet.ip` (z. B. `0.0.0.0/0`, `::/0`) | `zone` (externer Scope) / Transfernetz |

**Netzinventar (ADR 0010):** zusätzlich zum Tenant-Subtree liest der Collector die Fabric-Klassen
`fabricNode` (Leaf/Spine/Controller mit Modell/Seriennummer), Access Policies
(`infraRsAccPortP`, `infraNodeBlk`, `infraPortBlk`, `infraRsAccBaseGrp`, `infraAccBndlGrp` →
Ports zu Policy-Gruppen/PC/vPC), `l1PhysIf` (Port-Status/Beschreibung) und `lldpAdjEp`/`cdpAdjEp`
(Nachbarn) per `GET /api/class/<klasse>.json`. Aus den EPGs: `fvRsPathAtt` (statische Pfade,
`encap` → VLAN, Modus) und `fvCEp.encap`; aus BD/EPG: `fvSubnet` + `fvRsCtx` → reale Subnetze mit VRF.

**ACI-Regel-Rekonstruktion:** eine EPG↔EPG-Verbindung ergibt sich aus Provider
(`fvRsProv`) + Consumer (`fvRsCons`) desselben `vzBrCP`; die Ports kommen aus den
`vzFilter`/`vzEntry` der `vzSubj`. BD→L3Out (`fvRsBDToOut`) ordnet den Verkehr den
L3Outs zu (Firewall-Richtungs-Kategorien).

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
  `fvRsBDToOut`→L3O, `epg_subnet`/`epg_endpoint` (akkumulierend).
