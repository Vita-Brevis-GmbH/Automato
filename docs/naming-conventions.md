# Namenskonventionen — Standardprofil „default"

> **Austauschbares Namensprofil pro Kunde.** Dieses Dokument hält die Standard-Konvention
> fest, abgeleitet aus einer grossen Referenzumgebung mit eigener NetBox (Beispielkunde;
> Namen und Domains hier anonymisiert, z. B. `example.ch`). Andere Kunden mit eigener
> NetBox können abweichen — **im Schnitt sind die Konventionen aber ähnlich**. Automato
> behandelt die Konvention als **Profil** (Adapter-Prinzip wie beim SoT-Gateway); in der
> App ist sie als Standardprofil **„default"** hinterlegt (Werte:
> `orchestrator/app/naming_defaults.py`) und gilt für alle Mandanten ohne eigenes Profil. **Ausnahmen sind erstklassig**: Matching läuft als Kaskade
> Konvention → Typ/Farbcode → manuell/Designer → sonst Finding.
>
> Bezug: ADR 0004 (Datenmodell, Namenskonvention als First-Class-Signal), ADR 0006
> (Objekttyp/Farbe als Signal), ADR 0007 (Mandant/Kunde-Modell),
> `docs/usecases/uc1-applikations-doku-spec.md` (Zielausgabe).
>
> **Noch ausstehend** (vom Kunden nachzureichen): Kapitel 3.1 (vollständige
> Gruppengesellschaft-Abkürzungen), Kapitel 3.2 (vollständige Funktionscodes). Die
> Projektnummern gehören seit 0.36.0 nicht mehr zur Namenskonvention in Automato (Anwender);
> die Projektnummer im Namen (`AP_<nr>`, `…-007-…`) wird weiterhin gelesen. Die im Originaldokument eingebetteten Diagramme (`…png`) lagen nicht
> vor; die Grammatik ist aus den Textbeispielen rekonstruiert.

## Grundprinzip

**Jedes Objekt trägt einen Typ-Prefix**, damit es lesbar/eindeutig ist. Der Prefix ist in
Automato das erstklassige Typ-Signal; der strukturierte Rest liefert die Attribute.

| Prefix | Bedeutung | Automato-Typ |
|---|---|---|
| `H_<fqdn>` | interner Host (FQDN/IP) | `network_object` (host) |
| `H-EXT_` | externer Host/Interface ohne DNS | host, `expected_external` |
| `N_` | Netzwerk/Subnetz | `network_object` (network) |
| `V-EXT_` | FortiGate Destination-NAT / Virtual IP | `nat_rule` (dnat_vip) |
| `IPP_` | FortiGate Source-NAT IP-Pool | `nat_rule` (snat_pool) |
| `VIP_`/`VS_`/`POOL_`/… | Loadbalancer-Objekte | LB-Modul (später) |
| `TE_`/`VRF_` | ACI Tenant / VRF (= Gruppengesellschaft) | `tenant` (GG) |
| `BD_` | ACI Bridge Domain (Sicherheitszone) | `zone` |
| `EG_` | ACI Endpoint Group (feinste Zone) | `epg` |
| `AP_` | ACI Application Profile (= Projekt/Applikation) | `application` |
| `CT_`/`SJ_`/`FT_` | ACI Contract / Subject / Filter | `rule` / `rule_service` / `service_object` |
| `L3O_` | ACI Layer-3-Output / Transfernetz | `zone` (externer Scope) |

## ACI

### Tenant / VRF — `TE_` / `VRF_`
Pro Gruppengesellschaft ein Tenant `TE_<gg>` + VRF `VRF_<gg>` (z. B. `TE_test1` /
`VRF_test1`). Gruppengesellschaft-**übergreifende** Infrastruktur/Dienste:
`TE_infrz01`, `TE_res01`, … (im Namen bereits auf verteilte Rechenzentren ausgelegt).

### Bridge Domains — `BD_`
Virtuelle Netzwerkzonen, trennen Systeme verschiedener Sicherheitsstufen. Pro
GG-Tenant standardmässig dieselben, Anhang = GG-Kürzel. Beispiel (Testname GmbH DE):
- `BD_dmz01` — DMZ-Zone 1 (normale Sicherheit)
- `BD_vip01` — Zone für Load Balancer
- `BD_internal01` — interne Zone (hohe Sicherheit)

Infrastruktur-/Ressourcen-Tenants: `BD_<zweck>_infch` (Infrastruktur) bzw. `_res01`
(Ressourcen); dazwischen ein sprechender Zweck-Text ≤ 6 Zeichen.

### Endpoint Groups — `EG_`
Zonen innerhalb einer BD; **ein Server ist in genau einer EPG**. Kommunikation zwischen
EPGs nur über Contracts. Projekttrennung über die **3-stellige Projektnummer** im Namen
(verwaltet separat, derzeit Excel → Kapitel 3.2). In Ressourcen-/Infrastruktur-Tenants
entfällt die Projektnummer (gemeinsame Nutzung).

**7 EPGs pro Application Profile:**
`EG_dmz01`, `EG_dmz02`, `EG_dmz03` (DMZ 1–3), `EG_internal01`, `EG_internal02`,
`EG_internal03` (Hoch 1–3), `EG_vip01` (Loadbalancer-Interfaces).

In VMware eindeutig zusammengesetzt aus Tenant | Application Profile | EPG, z. B.:
`TE_test1|AP_001|EG_internal01`.

### Contracts / Subjects / Filter — `CT_` / `SJ_` / `FT_`
Erlaubte Verbindungen zwischen EPGs. Filter (`FT_`) definieren Ports; mehrere Filter →
Subject (`SJ_`) → Contract (`CT_`). Source/Destination kann Netzwerk- oder Host-Objekt
sein. Beispiele:
- `CT_ssgch-000-001-dmz01_test1-021-client`
- `CT_ssgch-000-001-internal02_vpn`

### Application Profile — `AP_`
Liste der Verbindungen, die eine Anwendung braucht (je Eintrag: Consumer-EPG →
Provider-EPG + Contract; optional LB/IDS/… ). Name = `AP_<projektnummer>`
(`AP_001`, `AP_002`). In Ressourcen- und `infrz01`-Tenant keine AP (ACI Network-Mode,
keine Microsegmentierung); Projektnummern dort nur zur Benennung von AD-Gruppen.

### Layer-3-Outputs / Transfernetze — `L3O_`
Ausgehende Verbindungen aus dem Tenant. Pro GG-Tenant dieselben:
- `L3O_inet` — Tenant ↔ Internet
- `L3O_res` — Tenant ↔ Ressourcen-Tenant
- `L3O_vpn` — Tenant ↔ VPNs
- `L3O_bit` — Tenant ↔ Büko / Business IT
- `L3O_sra` — Tenant ↔ Administratoren-Access (Remote Access)

Ausserhalb der GG-Tenants weitere `L3O_` mit sprechendem Namen möglich.

### Filter — `FT_` (= kanonischer Service-Katalog)
Standardisierte Ports mit sprechendem Namen; sonst `FT_<proto><port>`:
`FT_tcp4444`, `FT_udp3207`, `FT_tcp1024-65535`, `FT_tcpudp6001`. ICMP als Spezialfall.

**Prüfung in Automato** (FortiGate-Services und ACI-Filter, Findings):
- Ohne Präfix `FT_` → „Name verletzt Konvention“. Das Präfix steht seit 0.36.0 in der Tabelle
  „Objekt-Präfix → Typ“ (`object_prefix`) mit dem Typ **„Service / ACI-Filter“** (`service`)
  und ist dort pflegbar; ohne solchen Eintrag gilt `FT_` (die ältere Wörterbuch-Art
  `service_prefix` gilt zusätzlich).
- Mit Präfix wird der Port geprüft: Steht der Name im Wörterbuch `service` (z. B.
  `FT_https` = `tcp/443`), gilt dieser Wert; sonst das Schema `FT_<proto><port>`. Weicht der
  Service ab, entsteht der Befund „Service ≠ Name“.
- FortiOS-Standardservices (`HTTPS`, `ALL`, …) und die ACI-Filter in `common` (`default`,
  `arp`, `est`, `icmp`) sind von der Namensprüfung ausgenommen und gelten nicht als unbenutzt.
  Sie sollen aber nicht verwendet werden: in einer erlaubenden Regel → Befund „Standardobjekt
  verwendet“ (0.36.0). In der Objekt-Liste stehen sie zuunterst bzw. ausgeblendet.

| Name | Port | | Name | Port |
|---|---|---|---|---|
| `FT_cifs` | TCP/UDP 445 | | `FT_ldap` | TCP/UDP 389 |
| `FT_citrix-ica` | TCP 1494 | | `FT_ldap_gc` | TCP 3268 |
| `FT_dns` | TCP/UDP 53 | | `FT_ldapssl` | TCP 636 |
| `FT_ftp` | TCP 21 | | `FT_ldapssl_gc` | TCP 3269 |
| `FT_ftps` | TCP 989:990 | | `FT_mssql` | TCP/UDP 1433:1434 |
| `FT_h323` | TCP/UDP 1719:1720 | | `FT_mysql` | TCP 3306 |
| `FT_highports` | TCP 1024:65535 | | `FT_netbios-ls` | TCP/UDP 135 |
| `FT_http` | TCP 80 | | `FT_netbios-ns` | TCP/UDP 137 |
| `FT_https` | TCP 443 | | `FT_netbios-ssn` | TCP 139 |
| `FT_imap` | TCP 143 | | `FT_netbios-dgm` | UDP 138 |
| `FT_imapssl` | TCP 993 | | `FT_ntp` | TCP/UDP 123 |
| `FT_ipsec_ike` | UDP 500 | | `FT_ping` | ICMP T08/C00 |
| `FT_ipsec_nat_t` | UDP 4500 | | `FT_ping6` | ICMP6 T128/C00 |
| `FT_ipsec_ah` | IP 51 | | `FT_pop3` | TCP 110 |
| `FT_ipsec_esp` | IP 50 | | `FT_pop3ssl` | TCP 995 |
| `FT_iscsi` | TCP 3260 | | `FT_postgresql` | TCP 5432 |
| `FT_kerberos` | TCP 88 | | `FT_radius` | TCP/UDP 1812 |
| `FT_kerberos_cp` | TCP/UDP 464 | | `FT_rdp` | TCP 3389 |
| `FT_kms` | TCP 1688 | | `FT_rsync` | TCP 873 |
| `FT_l2tp` | TCP/UDP 1701 | | `FT_sip` | TCP/UDP 5060 |
| `FT_sipssl` | TCP 5061 | | `FT_smtp` | TCP 25 |
| `FT_smtpauth` | TCP 587 | | `FT_smtpssl` | TCP 465 |
| `FT_snmp` | UDP 161 | | `FT_snmptraps` | UDP 162 |
| `FT_ssh` | TCP 22 | | `FT_syslog` | UDP 514 |
| `FT_tftp` | UDP 69 | | `FT_telnet` | TCP 23 |
| `FT_traceroute` | UDP 33000:34000 | | `FT_wsus` | TCP 8530 |

## Netzwerk- & Host-Objekte

### Netzwerk `N_`
`N_<tenant>-<virtueller-standort>-<zweck/zone>[_<ip-oder-extra>]` (`000` = z. B. Stretched DC
bzw. Standort/extern unbekannt, siehe *Standorte*). Beispiele:
- `N_test1-000-dmz01` — Bridge Domain (DMZ)
- `N_infzr01-000-res-infrz01` — Transfernetz; `N_test1-000-l3o-vpn-edge` — Transfernetz
  (Sicht vom DC nach aussen: erst `l3o-vpn`, dann `edge`)
- `N_tasch-001-10.100.0.0-16` — Container-Subnetz (Büko Testname AG)
- `N_servicenow-000-wan01_185.161.103.0-24` — externes WAN-Subnetz (Standort unbekannt)

### Host `H_` / extern `H-EXT_`
`H_<funktion+nr>-<projektnr>-<firma>.<domain>`. Beispiel: `H_app01-002-test1.example.ch`
(App-Server 01, Projekt bzw. ACI-Applikationsprofil 002, Testname AG). VMs tragen **keinen
Standort** im Namen. Extern/ohne DNS bzw. Interface in Firewall/ACI:
`H-EXT_<name>-000-wan01_<ip>`, z. B. `H-EXT_servicenow-000-wan01_185.161.103.34`.

## FortiGate-spezifisch

### Farbcode = Herkunfts-/Typ-Signal
Der Collector liest das `color`-Feld als Klassifizierung mit:

| Klasse | Farbe | Code | | Klasse | Farbe | Code |
|---|---|---|---|---|---|---|
| Intern Host | hellgrün | 13 | | VPN Host | pink | 23 |
| Intern Network | dunkelgrün | 16 | | VPN Network | lila | 21 |
| Swisscom Host | hellblau | 18 | | Extern Host | orange | 9 |
| Swisscom Network | dunkelblau | 2 | | Extern Network | dunkelorange | 8 |
| MPLS Host | rot | 6 | | MPLS Network | dunkelrot | 4 |

### Destination-NAT / Virtual IP — `V-EXT_`
Externe + interne IP fürs DNAT. Name = externe URL des Dienstes (bei mehreren Namen die
Haupt-URL). `V-EXT_<fqdn>`, z. B. `V-EXT_www.example.ch`.

### Source-NAT IP-Pools — `IPP_`
- Pro Server: `IPP_<fqdn>` (z. B. `IPP_dcs01-001-res01.example.ch`).
- Pro Tenant (gemeinsam): `IPP_<tenant>-<standort>-<zweck>` (z. B. `IPP_casch-000-inet01`).

**Prüfung (seit 0.95.0):** VIPs (`vip`/`vip6`) müssen mit einem Präfix vom Typ „VIP“ bzw.
„VIP / DNAT“ beginnen (Standard `V-EXT_`, `VIP_`), Virtual Server mit einem vom Typ „Virtual
Server“ (ohne solchen Eintrag wie VIPs), IP-Pools (`ippool`/`ippool6`) mit einem vom Typ „SNAT
IP-Pool“ (Standard `IPP_`) – sonst Befund „Name verletzt die Namenskonvention“. Ein Adress-Präfix
(z. B. `H_`) an einer VIP genügt nicht mehr. Hat das eigene Namensprofil Präfixe, aber keines
dieses Typs, werden diese Namen nicht geprüft. Namen des NSX ALB (Virtual Services) prüft
Automato nicht.

### Schutzmechanismen (UTM-Profile an der Regel)
- Web Filter `WEB_<systemtyp>-<zweck>` — `WEB_Server-default`, `WEB_Server-open`
- Application Control `APP_<systemtyp>-<zweck>` — `APP_server-default`
- Intrusion Prevention `IPS_<systemtyp>-<zweck>` — `IPS_Server-Apache`, `IPS_Server-IIS`,
  `IPS_Server-HighSecurity`
- Web Application Firewall `WAF_<fqdn>[:service]` — `WAF_www.example.ch`
- SSL/SSH Inspection `SSL_<typ>` — `SSL_certificate-inspection`, `SSL_deep-inspection`

### Routing & Virtualisierung
- Prefix List `PL_<FROM|TO>-<protokoll>-<nachbar ROU|ACI>-<V4|V6>` — `PL_FROM-BGP-ROU-V6`
- Route Map `RM_<FROM|TO>-<protokoll>-<nachbar>-<version>` — `RM_FROM-BGP-ROU-V6`
- VDOM `<sra|vpn|fwl><nr><security-tenant>` — Cluster `fwc05-000-infrz` → `sra05-infrz`,
  `vpn05-infrz`

### PBR / L4-L7 (Service-Graph)
Mit Minuszeichen getrennt, soweit nicht anders vermerkt:
- Device General `FWLD_<sec-tenant>-<dc-code>-pbr` — `FWLD_res01-000-pbr`
- Device Firewall = Firewall-Name (`fwl01-001-infrz`)
- Device Interface `<vmware-host>_<nic>` — `evh01-chzrh1-infrz_vmnic4`
- Device Cluster `<sec-tenant>-<dc>-pbr` — `res01-000-pbr`
- IP SLA `SLA_<sec-tenant>-<dc>-pbr` — `SLA_res01-000-pbr`
- Redirect Health Group `RHG_<sec-tenant>-<dc>-<firewall>` — `RHG_res01-000-fwl01-001-infrz`
- Policy-Based Redirect `PBR_<sec-tenant>-<dc>-pbr` — `PBR_res01-000-pbr`
- PBR Destination `<firewall>_<sec-tenant>` — `fwl01-001-infrz_res01`
- Service Graph Template `SGT_<sec-tenant>-<dc>-pbr` — `SGT_res01-000-pbr`

## Standorte (ADR 0015)

- **Virtuell:** dreistellige Nummer (`000`, `001`, …). Steht in Netz-Namen (`N_`, `T_`, `IPP_`)
  und in Namen physischer Cluster: `fwc11-000-infrz` = Firewall-Cluster 11, virtueller
  Standort `000` (Stretched DC), Tenant `infrz`. Ein virtueller Standort kann über mehrere
  physische laufen.
- **Physisch:** Ort-Code des Rechenzentrums (z. B. `chzrh1` = Land, Ort, Nummer). Steht in
  Namen physischer Geräte: `evh01-chzrh1-infrz`.
- **VMs:** kein Standort im Namen; der Block nach der Funktion ist das ACI-Applikationsprofil.

Automato erfasst beide Arten unter *Mandanten & Standorte* (Zuordnung virtuell → physisch)
und schlägt den Standort aus Netz-, BD-, Geräte- und Quellsystem-Namen vor (nur Anzeige).

## Hostnamen

**Intern:** Block1 = 3-stelliger Funktionscode + 2-stellige Laufnummer · Block2 =
3-stellige Projektnummer · Block3 = 5-stelliger Firma-mit-Land-Code · Domäne.
Beispiel: `app01-123-tonch.example.ch`. Gemeinsame Geräte (Ressourcen-/Infrastruktur)
ohne Projektnummer.

**Extern (Clients, Router, …):** Block1 = 3-stelliger Gerätecode + 3-stellige Laufnummer ·
Block2 = 1–8-stellige Kundennummer (alphanumerisch) · Block3 = Projektnummer · Block4 =
Firma-mit-Land · Domäne. Beispiel: `rou-a323-cd3f.example.ch`.

## Benutzer

- **Plattform-User** (Domäne `example.ch`): 4 Buchstaben Nachname + 3 Buchstaben Vorname +
  1-stellige Laufnummer.
- **Service-User**: enthält immer System/Gerät, wo das Passwort gesetzt ist:
  `<hostname><env>.<service>` bzw. `<product><env>.<service>` — `ctdprod.smtp`,
  `fwc01-chzrh1-infrz.ldap`, `app01-001-res01.ldap`. Mehrfach genutzte User gesondert
  dokumentieren.
- **gMSA/SMSA** (NetBIOS, max. 15 Zeichen): SMSA `sa-<hostohnebindestriche>-<tenantnr>`
  (`sa-app03021-1`); GMSA `sa-<appbeschreibung>-<tenantnr>`; Domain-Local-Group
  `DL-gMSA-<appbeschreibung>-<tenantnr>`.

**Tenant-Nummerierung:** `TE_res01`=0, `TE_res02`=1, `TE_ssgch`=2, `TE_casch`=3,
`TE_test1`=4, `TE_test2`=5, `TE_test3`=6 (unbenutzte ausgelassen).

## E-Mail
Personen- und Geräteadressen in `example.ch`; Geräte: `<gerätename>@example.ch`
(`app01-123-tonch@example.ch`).

## PRTG-Monitoring
- **Boards**: Plattform-Betrieb `OPS - <Tenant> - <Nutzung>`; SLA `SLA - <Tenant>`; Kunde
  `<Tenant> - <Kundenname>`; pro Applikation `<Appname> - <Tenant>`.
- **Filter**: analog zu Boards (klein halten, Tenant-Name enthalten).
- **Tags**: Plattform-Betrieb mit Prefix `OPS_` (z. B. `OPS_networksecurity`, `OPS_compute`,
  `OPS_windows`, `OPS_linux`, `OPS_productive`, `OPS_testdev`, `OPS_iaas`, `OPS_saas`,
  `OPS_k8s`, `OPS_vip_external`, `OPS_sla`, `OPS_ad_inf`, `OPS_ddi`, `OPS_backup`, …);
  Kunden-Tags mit Tenant-Kürzel-Prefix (`TONCH_`, `SSGCH_`). **Keine `-`/`+` in Tags**
  (IT-Ops-Board-Operatoren).

## Loadbalancer-Objekte
Prefix — Muster — Beispiel:
- `VS_<fqdn>` Virtual Service — `VS_www.example.ch`
- `PVS_<fqdn>` Parent (Enhanced Virtual Hosting) — `PVS_example.ch`
- `CVS_<fqdn>` Child — `CVS_login.example.ch`; K8s: `CVS_TEST_<fqdn>`, `CVS_PROD_<fqdn>`
- `VIP_<fqdn>[:service]` VS-VIP — `VIP_www.example.ch`
- `POOL_<fqdn>[:port]` Pool — `POOL_www.example.ch`, `POOL_www.example.ch:9443`
- `TUP_<fqdn>` TCP/UDP-Profil · `APPP_<fqdn>` Application-Profil · `WAFP_<fqdn>` WAF-Policy
- `EPP_<fqdn>` Error-Page · `SSLP_<fqdn>` SSL-Profil · `ANLP_<fqdn>` Analytics
- `CRT_<fqdn>_<startjahr>-<endjahr>` Zertifikat — `CRT_www.example.ch_2023-2024`
- `PATH_<fqdn>_<nr>` / `RD_<fqdn>_<nr>` HTTP-Request-Rules · `HM_<fqdn>` Health-Monitor ·
  `DS_<fqdn>_<nr>` Data-Script

## Rancher / Kubernetes
- Cluster-Name `<tenant>-<zweck>-<nr>` (`scaas-k8s-prod01`); VIP `VIP_<cluster>.<domain>:<proto>`;
  App-DNS `*.<cluster>.example.ch`; API `api.<cluster>.example.ch`.
- Cluster-Label (Shared) `<cluster>-<tenant>-<standort>`.
- Egress-IP, Namespace, Persistent Volume (`VOL_<tenant>_<zweck>_<namespace>_pvc_<guid>`)
  — teils gekürzt wegen Längenlimits.

## IPsec / Config-Prefixe
- `IPSP_<hersteller>_<model>` IPsec-Policy · `IPSG_<hostname>` Gateway ·
  `IPSC_<gwA>_<gwB>` Connection · `IPSK_<nr>` PreSharedKey
- `RCT_<serviceprofile><hersteller><type>` Router-Config-Template
- `SP_<firmencode>_<ap-nummer>` Service-Profile — `SP_OPS_ssgch_001`

## Katalog

### Firmen-/Tenant-Codes (Auszug; vollständig = Kapitel 3.1, ausstehend)
`share` (gemeinsame Projekte), `infau` (Automatisierung), `infte` (Testumgebung),
`infdo` (Dokumentation), `infrz01` (Infrastruktur RZ01), `res01` (Ressourcen RZ01),
`Common` (gemeinsame Komponenten RZ01), `infedge` (Edge-Netze). Länderkennung 2-stellig
an das 3-stellige Firmenkürzel angehängt; `infch`/`resch`/`comch` für gruppenübergreifende
Rechner je Einsatzzweck. Beispiele aus dem Dokument: `test1`, `ssgch`, `casch`, `tonch`,
`tasch`.

### Gerätebezeichnungen (3-stelliger Funktionscode)
`aci` ACI Fabric · `app` Applikationsserver · `asc` APIC Switch Controller · `avm`
Antivirus-Mgmt · `bac` Backup-Server · `ban` Backup-Node · `csw` Coreswitch · `dbs`
Datenbankserver · `dcs` Domänenserver · `ddi` IPAM/DNS/DHCP · `dns` DNS · `esw`
Edge-Switch · `evh` Edge-VMware-Host · `exc` Exchange · `fic` UCS Fabric Interconnect ·
`fim` UCS Domain/FI-Manager · `fls` Fileserver · `fwc` Firewall-Cluster · `fwl` Firewall ·
`fwm` Firewall-Manager · `ilo` iLO · `ips` IPS · `lba` Loadbalancer · `lbc`
Loadbalancer-Cluster · `lic` Lizenzserver · `lsw` Leaf-Switch · `mon` Monitor/PRTG ·
`mai` Mailserver · `rou` Router · `rds` Remote Desktop Services · `ssw` Spine-Switch ·
`stc` Storage-Cluster · `stm` Storage-Manager · `sto` Storage · `swi` Switch · `orc`
Orchestrator · `uds` Update-Server · `ucs` UCS-Chassis · `vce` vCenter · `vpn`
VPN-Concentrator · `vho` VMware-Host · `web` Webserver · `wap` Wireless Access Point.

## Ergänzungen aus realen Samples (2026-10-13)

Aus echten Ausschnitten der Referenzumgebung (FortiGate-Config, ACI-Tenant `TE_share`) beobachtet; fliesst
in die (in-App pflegbaren) Prefix-Wörterbücher und die Ausnahmen-Kaskade ein:

- **Zusätzliche Objekt-Prefixe:** `N-EXT_` (externes Netz), `T_` (Transfernetz,
  z. B. `T_infdo-000-l3o-inet_edge`). Bare-name **Geography**-Objekte (`type geography`,
  `country`, z. B. `SE`) → `not_applicable`. **Freitext-Adressgruppen** (Vendor-Ranges,
  z. B. „Atlassian Cloud …") → Mitglieder `expected_external`.
- **Reale UTM-Profil-Prefixe** (weichen vom Abschnitt „Schutzmechanismen" ab, können je
  FortiGate variieren): `AV_` (antivirus), `WF_` (webfilter), `DF_` (dnsfilter),
  `AC_` (application-list), `IPS_`, `WAF_`, `SSL_`. Für die Doku zählen nur die an der
  Policy **referenzierten Profilnamen**.
- **`color` ist optional** und wird als **numerischer Code** geführt (Tabelle oben); nicht
  jedes Objekt hat eine Farbe → dann Fallback auf Prefix/Typ.
- **IPv4/IPv6 getrennt** (`firewall/address` vs. `address6`, Policy `srcaddr`/`srcaddr6`);
  Dual-Stack auch in ACI real (`fvSubnet` v4+v6).
- **`comment`/`comments`** tragen oft `CTASK…` (ServiceNow-Change) bzw. `CT_…`
  (ACI-Contract-Bezug) → nutzbar für „Description"/Rückverfolgbarkeit.

Lesevertrag/Feld-Mapping: `docs/collectors-read-contract.md`.
