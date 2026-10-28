// Darstellung von Firewall-Objekten mit Referenzdaten aus dem IPAM (DDI/NetBox), ADR 0010.
import { ReactNode, useEffect, useState } from "react";
import { fetchSystems, RefInfo, RefValue, Rule, RuleRef, VmInfo } from "./api";
import { L } from "./i18n";
import { Dash, Icon, MatchBadge } from "./ui";

export const PLATFORM_LABEL: Record<string, string> = {
  fortigate: "FortiGate",
  cisco_aci: "Cisco ACI",
  vmware: "VMware vCenter",
  cisco_switch: L("Switches & Router", "Switches & routers"),
  avi: "NSX ALB (Avi)",
  infoblox: "Infoblox DDI",
  netbox: "NetBox",
};
export const REFERENCE_PLATFORMS = ["infoblox", "netbox"];
export const REF_ROLE_LABEL: Record<string, string> = {
  primary: L("IPAM führend", "Primary IPAM"),
  enrichment: L("Anreicherung", "Enrichment"),
};

// Befund-Typen (Findings) mit verständlicher Bezeichnung
export const FINDING_LABEL: Record<string, string> = {
  unmatched_object: L("Nicht im IPAM", "Not in IPAM"),
  invalid_name: L("Name verletzt Konvention", "Name violates convention"),
  ambiguous_ip: L("IP mehrdeutig", "Ambiguous IP"),
  duplicate_value: L("Doppelter Wert", "Duplicate value"),
  name_type_mismatch: L("Präfix passt nicht zum Typ", "Prefix does not match type"),
  epg_no_endpoints: L("EPG ohne Endpunkte", "EPG without endpoints"),
  name_mismatch: L("Name ≠ DNS-Name", "Name ≠ DNS name"),
  unused_object: L("Unbenutztes Objekt", "Unused object"),
  subnet_not_in_ipam: L("Subnetz fehlt im IPAM", "Subnet missing in IPAM"),
  vlan_mismatch: L("VLAN weicht ab", "VLAN differs"),
  service_mismatch: L("Service ≠ Name", "Service ≠ name"),
  predefined_service_used: L("Standardobjekt verwendet", "Predefined object used"),
  rule_incomplete: L("Regel nicht wirksam", "Rule not effective"),
  vm_not_in_ipam: L("VM-IP nicht im IPAM", "VM IP not in IPAM"),
  portgroup_no_epg: L("Portgruppe ohne EPG", "Port group without EPG"),
  portgroup_name_mismatch: L("Portgruppe: Name ≠ EPG", "Port group: name ≠ EPG"),
  mapped_not_in_ipam: L("Ziel nicht im IPAM", "Target not in IPAM"),
  parallel_rule_mismatch: L("Parallele Regel abweichend", "Parallel rule differs"),
  parallel_rule_missing: L("Regel fehlt parallel", "Rule missing on parallel firewall"),
  parallel_object_mismatch: L("Paralleles Objekt abweichend", "Parallel object differs"),
  parallel_policy_route_mismatch: L("Parallele Policy-Route abweichend", "Parallel policy route differs"),
  parallel_route_mismatch: L("Paralleles Routing abweichend", "Parallel routing differs"),
  parallel_nat_mismatch: L("Paralleles NAT abweichend", "Parallel NAT differs"),
  aci_scope_unpaired: L("Contract: ohne Gegenstelle im Scope", "Contract: no peer in scope"),
  aci_contract_both_sides: L("Contract: EPG Consumer und Provider", "Contract: EPG consumer and provider"),
  epg_assignment_conflict: L("EPG: gelernt ≠ konfiguriert", "EPG: learned ≠ configured"),
  snat_pool_missing: L("SNAT-Pool fehlt", "SNAT pool missing"),
  topo_duplicate_ip: L("IP doppelt im Netz", "Duplicate IP in network"),
  topo_mask_mismatch: L("Netzmaske abweichend", "Netmask differs"),
  topo_route_loop: L("Routing-Schleife", "Routing loop"),
  topo_static_unused: L("Statische Route ohne Wirkung", "Static route without effect"),
  topo_bgp_down: L("BGP nicht aufgebaut", "BGP not established"),
  zone_policy_violation: L("Zone: gegen die Absicht", "Zone: against the intent"),
  zone_overlap: L("Netz in mehreren Zonen", "Network in several zones"),
  zone_outside_schema: L("Zone ausserhalb des Schemas", "Zone outside the schema"),
  zone_net_exit: L("Netz nicht über den Übergang zur Zone", "Network not via the transition to the zone"),
  app_connection_mismatch: L("Applikation: Wege uneinheitlich", "Application: inconsistent paths"),
  app_contract_narrower: L("Applikation: Contract enger als Firewall", "Application: contract narrower than firewall"),
  link_speed_mismatch: L("Kabel: Speed abweichend", "Link: speed differs"),
  link_duplex_mismatch: L("Kabel: Duplex abweichend", "Link: duplex differs"),
  link_vlan_mismatch: L("Kabel: VLANs abweichend", "Link: VLANs differ"),
  link_lag_mismatch: L("Kabel: Port-Channel einseitig", "Link: Port-Channel one-sided"),
  link_one_sided: L("Kabel: nur eine Seite meldet", "Link: only one side reports"),
  uplink_vlan_missing: L("VLAN fehlt am ESXi-Uplink", "VLAN missing on ESXi uplink"),
  l2_single_link: L("L2: nur ein Kabel", "L2: single cable"),
  l2_single_switch: L("L2: alle Kabel am selben Switch", "L2: all cables on the same switch"),
  l2_single_point: L("L2: Switch als einzelner Ausfallpunkt", "L2: switch as single point of failure"),
  lag_degraded: L("Port-Channel unvollständig", "Port-Channel incomplete"),
  vlan_split: L("VLAN getrennt", "VLAN split"),
  vpc_mismatch: L("vPC-Paar uneinheitlich", "vPC pair inconsistent"),
  source_stale: L("Quellsystem: Stand veraltet", "Source system: data outdated"),
  source_partial: L("Quellsystem: nur teilweise gelesen", "Source system: only partially collected"),
};
export const findingLabel = (t: string) => FINDING_LABEL[t] ?? t;

// Was der Befund-Typ prüft (Kurzbeschreibung für Kacheln und Tooltips)
export const FINDING_HINT: Record<string, string> = {
  unmatched_object: L("Firewall-Objekt ohne Eintrag im IPAM", "Firewall object without an entry in the IPAM"),
  name_mismatch: L("Objektname passt nicht zum DNS-Namen im IPAM", "Object name does not match the DNS name in the IPAM"),
  subnet_not_in_ipam: L("Subnetz der Firewall/ACI fehlt im IPAM", "Subnet of the firewall/ACI missing in the IPAM"),
  vlan_mismatch: L("VLAN im Netz weicht vom IPAM ab", "VLAN in the network differs from the IPAM"),
  invalid_name: L("Name verletzt die Namenskonvention", "Name violates the naming convention"),
  name_type_mismatch: L("Präfix passt nicht zum Objekttyp", "Prefix does not match the object type"),
  duplicate_value: L("gleicher Wert in mehreren Objekten", "same value in several objects"),
  ambiguous_ip: L("IP in mehreren Objekten", "IP in several objects"),
  unused_object: L("in keiner Regel oder Gruppe verwendet", "not used in any rule or group"),
  epg_no_endpoints: L("EPG ohne gelernte Endpunkte", "EPG without learned endpoints"),
  service_mismatch: L("Protokoll/Port passt nicht zum Service-Namen", "Protocol/port does not match the service name"),
  predefined_service_used: L(
    "Standard-Service des Herstellers (z. B. HTTPS, ALL) in einer erlaubenden Regel – nicht nach Konvention",
    "Predefined service of the vendor (e.g. HTTPS, ALL) in an accepting rule – not according to convention",
  ),
  rule_incomplete: L("Regel/Contract ohne Quelle bzw. Ziel", "Rule/contract without source or destination"),
  vm_not_in_ipam: L("IP einer VM (vCenter) fehlt im IPAM", "IP of a VM (vCenter) missing in the IPAM"),
  portgroup_no_epg: L(
    "Portgruppe nach ACI-Schema ohne eingelesene EPG",
    "Port group following the ACI schema without a collected EPG",
  ),
  portgroup_name_mismatch: L(
    "Portgruppe nur über das VLAN einer anders benannten EPG zugeordnet",
    "Port group assigned only via the VLAN of a differently named EPG",
  ),
  parallel_rule_mismatch: L(
    "gleiche Policy auf der parallelen Firewall anders konfiguriert",
    "same policy configured differently on the parallel firewall",
  ),
  parallel_rule_missing: L("Policy fehlt auf einer parallelen Firewall", "Policy missing on a parallel firewall"),
  parallel_object_mismatch: L(
    "gleichnamiges Objekt auf der parallelen Firewall anders definiert",
    "object of the same name defined differently on the parallel firewall",
  ),
  parallel_policy_route_mismatch: L(
    "Policy-Route bzw. SD-WAN-Regel auf der parallelen Firewall anders oder fehlt",
    "Policy route or SD-WAN rule differs or is missing on the parallel firewall",
  ),
  parallel_route_mismatch: L(
    "Routen eines VDOMs auf der parallelen Firewall anders (Protokoll, Interface, statische Routen) oder fehlen",
    "Routes of a VDOM differ on the parallel firewall (protocol, interface, static routes) or are missing",
  ),
  parallel_nat_mismatch: L(
    "VIP bzw. IP-Pool auf der parallelen Firewall anders oder fehlt",
    "VIP or IP-Pool differs or is missing on the parallel firewall",
  ),
  aci_scope_unpaired: L(
    "Consumer bzw. Provider, die der Scope des Contracts (AP, VRF, Tenant) mit keiner Gegenstelle verbindet",
    "Consumers or providers that the scope of the contract (AP, VRF, tenant) connects to no peer",
  ),
  aci_contract_both_sides: L(
    "EPG ist Consumer und Provider desselben Contracts – er gilt in beide Richtungen, gegenüber sich selbst nicht",
    "EPG is consumer and provider of the same contract – it applies in both directions, but not towards itself",
  ),
  epg_assignment_conflict: L(
    "IP im ACI in einer anderen EPG gelernt als konfiguriert (Portgruppe der VM bzw. statischer Endpunkt)",
    "IP learned in the ACI in a different EPG than configured (port group of the VM or static endpoint)",
  ),
  snat_pool_missing: L(
    "Policy übersetzt die Quelle auf einen IP-Pool, den die Firewall nicht hat",
    "Policy translates the source to an IP-Pool the firewall does not have",
  ),
  topo_duplicate_ip: L("dieselbe IP auf zwei Geräten im selben Netz", "the same IP on two devices in the same network"),
  topo_mask_mismatch: L(
    "Gegenstellen im selben Netz mit verschiedener Netzmaske",
    "peers in the same network with different netmasks",
  ),
  topo_route_loop: L(
    "zwei gelesene Knoten leiten ein Netz gegenseitig zueinander",
    "two read nodes route a network to each other",
  ),
  topo_static_unused: L(
    "eingeschaltete statische Route, die nicht in der Routing-Tabelle steht",
    "enabled static route that is not in the routing table",
  ),
  topo_bgp_down: L("BGP-Sitzung eines gelesenen Knotens nicht aufgebaut", "BGP session of a read node not established"),
  zone_policy_violation: L(
    "erlaubende Regel zwischen Zonen, die laut Zonenmatrix gesperrt sind",
    "accepting rule between zones that are blocked according to the zone matrix",
  ),
  zone_overlap: L("dasselbe Netz in mehreren logischen Zonen", "the same network in several logical zones"),
  zone_outside_schema: L(
    "logische Zone, die zu keiner Zone des Grundschemas im Namensprofil passt",
    "logical zone that matches no zone of the base schema in the naming profile",
  ),
  zone_net_exit: L(
    "Netz einer Zone, das eine Firewall (Gerät einer Zone) nicht über den Übergang Richtung dieser Zone im Zonenplan lernt (anderes Interface, nur Default-Route bzw. keine Route)",
    "Network of a zone a firewall (device of a zone) does not learn via the transition towards that zone in the zone plan (other interface, only the default route or no route)",
  ),
  app_connection_mismatch: L(
    "Verbindung einer Applikation, die über einen Weg offen und über einen anderen gesperrt ist (aktiv, ECMP, Backup, Firewalls im Verbund)",
    "Connection of an application that is open via one path and blocked via another (active, ECMP, backup, firewalls in a parallel group)",
  ),
  app_contract_narrower: L(
    "Contract Richtung L3Out, der weniger öffnet als die Firewall-Regeln der Applikation (die Firewall ist führend)",
    "Contract towards the L3Out that opens less than the firewall rules of the application (the firewall is leading)",
  ),
  link_speed_mismatch: L(
    "zwei verbundene Switch-Ports mit verschiedener Geschwindigkeit",
    "two connected switch ports with different speeds",
  ),
  link_duplex_mismatch: L(
    "zwei verbundene Switch-Ports mit verschiedenem Duplex",
    "two connected switch ports with different duplex",
  ),
  link_vlan_mismatch: L(
    "Modus, Access- bzw. Native VLAN oder erlaubte VLANs der beiden Ports verschieden",
    "Mode, access or native VLAN or allowed VLANs of the two ports differ",
  ),
  link_lag_mismatch: L(
    "Port in einem Port-Channel, der Port gegenüber nicht",
    "Port in a Port-Channel, the opposite port is not",
  ),
  link_one_sided: L(
    "Verbindung, die nur einer von zwei gelesenen Switches per CDP/LLDP meldet",
    "Link that only one of two read switches reports via CDP/LLDP",
  ),
  uplink_vlan_missing: L(
    "Port, an dem VMs eines ESXi-Hosts gesehen werden, lässt ein VLAN ihres Clusters/vSwitches nicht durch",
    "Port on which VMs of an ESXi host are seen does not pass a VLAN of their cluster/vSwitch",
  ),
  l2_single_link: L(
    "Switch bzw. ESXi-Host hängt mit einem einzigen Kabel an den weiterleitenden Geräten",
    "Switch or ESXi host is connected to the forwarding devices with a single cable",
  ),
  l2_single_switch: L(
    "alle Kabel eines Switches bzw. ESXi-Hosts enden am selben Switch – keine Redundanz gegen dessen Ausfall",
    "all cables of a switch or ESXi host end on the same switch – no redundancy against its failure",
  ),
  l2_single_point: L(
    "fällt dieser Switch aus, zerfällt das Netz in getrennte Teile",
    "if this switch fails, the network breaks into separate parts",
  ),
  lag_degraded: L(
    "Port-Channel, von dem nicht alle Mitglieder verbunden sind",
    "Port-Channel of which not all members are connected",
  ),
  vlan_split: L(
    "VLAN wird an Access-Ports bzw. als SVI auf mehreren Switches genutzt, die Trunks dazwischen lassen es nicht durch",
    "VLAN is used on access ports or as SVI on several switches, the trunks in between do not pass it",
  ),
  vpc_mismatch: L(
    "vPC nur auf einem Peer, down bzw. inkonsistent – oder der Peer des vPC-Paars ist nicht verbunden",
    "vPC only on one peer, down or inconsistent – or the peer of the vPC pair is not connected",
  ),
  source_stale: L(
    "letzter erfolgreicher Lauf älter als die Grenze (AUTOMATO_STALE_DAYS, Zeitplan beachtet) – Doku und Weg-Suche zeigen einen alten Stand",
    "last successful run older than the limit (AUTOMATO_STALE_DAYS, schedule considered) – documentation and path search show outdated data",
  ),
  source_partial: L(
    "letzter Lauf mit Teilfehlern: Geräte nicht erreicht, nicht lesbar bzw. Routing-Kontexte unvollständig",
    "last run with partial errors: devices not reached, not readable or routing contexts incomplete",
  ),
};

// Kategorien der Übersicht (Findings-Seite)
export const FINDING_GROUPS: { key: string; label: string; help: string; types: string[] }[] = [
  {
    key: "ipam",
    label: L("IPAM-Abgleich", "IPAM reconciliation"),
    help: L("Firewall/ACI gegen DDI bzw. NetBox", "Firewall/ACI against DDI or NetBox"),
    types: [
      "unmatched_object",
      "name_mismatch",
      "subnet_not_in_ipam",
      "vlan_mismatch",
      "ambiguous_ip",
      "vm_not_in_ipam",
    ],
  },
  {
    key: "naming",
    label: L("Namenskonvention", "Naming convention"),
    help: L(
      "Präfixe laut Namensprofil, Services nach FT_-Schema",
      "Prefixes per naming profile, services following the FT_ schema",
    ),
    types: ["invalid_name", "name_type_mismatch", "service_mismatch", "predefined_service_used"],
  },
  {
    key: "rules",
    label: L("Regeln", "Rules"),
    help: L(
      "ohne Quelle/Ziel bzw. ohne Consumer/Provider, Contract-Scope ohne Gegenstelle",
      "without source/destination or without consumer/provider, contract scope without peer",
    ),
    types: ["rule_incomplete", "aci_scope_unpaired", "aci_contract_both_sides", "snat_pool_missing"],
  },
  {
    key: "parallel",
    label: L("Parallele Firewalls", "Parallel firewalls"),
    help: L(
      "Regeln, Objekte, Policy-Routen, Routing und NAT im Verbund nicht gleich",
      "Rules, objects, policy routes, routing and NAT not identical in the parallel group",
    ),
    types: [
      "parallel_rule_mismatch",
      "parallel_rule_missing",
      "parallel_object_mismatch",
      "parallel_policy_route_mismatch",
      "parallel_route_mismatch",
      "parallel_nat_mismatch",
    ],
  },
  {
    key: "cleanup",
    label: L("Bereinigung", "Cleanup"),
    help: L("Doppelte und unbenutzte Objekte", "Duplicate and unused objects"),
    types: ["duplicate_value", "unused_object"],
  },
  {
    key: "topology",
    label: L("Topologie", "Topology"),
    help: L(
      "Plausibilität von Netzen und Routing: doppelte IPs, Masken, Schleifen, Routen ohne Wirkung, BGP",
      "Plausibility of networks and routing: duplicate IPs, masks, loops, routes without effect, BGP",
    ),
    types: ["topo_duplicate_ip", "topo_mask_mismatch", "topo_route_loop", "topo_static_unused", "topo_bgp_down"],
  },
  {
    key: "cabling",
    label: L("Verkabelung", "Cabling"),
    help: L(
      "Verbindungen zwischen Switches: Speed, Duplex, VLANs, Port-Channel, einseitig gemeldet",
      "Links between switches: speed, duplex, VLANs, Port-Channel, reported one-sided",
    ),
    types: ["link_speed_mismatch", "link_duplex_mismatch", "link_vlan_mismatch", "link_lag_mismatch", "link_one_sided", "uplink_vlan_missing", "l2_single_link", "l2_single_switch", "l2_single_point", "lag_degraded", "vlan_split", "vpc_mismatch"],
  },
  {
    key: "zones",
    label: L("Zonen", "Zones"),
    help: L(
      "Regeln gegen die Absicht der Zonenmatrix, Netze in mehreren logischen Zonen, Zonen ausserhalb des Grundschemas",
      "Rules against the intent of the zone matrix, networks in several logical zones, zones outside the base schema",
    ),
    types: ["zone_policy_violation", "zone_overlap", "zone_outside_schema", "zone_net_exit"],
  },
  {
    key: "apps",
    label: L("Applikationen", "Applications"),
    help: L(
      "Verbindungen Ende zu Ende: über einen Weg offen, über einen anderen gesperrt",
      "End-to-end connections: open via one path, blocked via another",
    ),
    types: ["app_connection_mismatch", "app_contract_narrower"],
  },
  {
    key: "aci",
    label: "ACI / VMware",
    help: L(
      "EPGs ohne Endpunkte, Portgruppen ohne EPG, gelernt ≠ konfiguriert",
      "EPGs without endpoints, port groups without EPG, learned ≠ configured",
    ),
    types: ["epg_no_endpoints", "portgroup_no_epg", "portgroup_name_mismatch", "epg_assignment_conflict"],
  },
  {
    key: "data",
    label: L("Datenqualität", "Data quality"),
    help: L(
      "Stand der Quellsysteme: veraltet bzw. nur teilweise gelesen – Abgleich → Datenqualität zeigt alles",
      "State of the source systems: outdated or only partially collected – Reconciliation → Data quality shows everything",
    ),
    types: ["source_stale", "source_partial"],
  },
];

export const CATEGORY_LABEL: Record<string, string> = {
  address: L("Adresse", "Address"),
  address_group: L("Adressgruppe", "Address group"),
  vip: "VIP",
  ippool: "IP-Pool",
  service: "Service",
  service_group: L("Servicegruppe", "Service group"),
  vip_target: L("VIP-Ziel", "VIP target"),
};

const KIND_LABEL: Record<string, string> = {
  prefix: L("Netz", "Network"),
  range: "Range",
  ip_address: L("IP-Adresse", "IP address"),
  host: "Host",
  a_record: L("A-Record", "A record"),
  ptr_record: "PTR",
  fixed_address: L("DHCP fix", "DHCP fixed"),
  vlan: "VLAN",
};

export function IssueChips({ issues }: { issues: string[] }) {
  if (!issues.length) return <span className="chip ok">✓</span>;
  return (
    <span className="chips">
      {issues.map((i) => (
        <span key={i} className={`chip ${i === "unused_object" ? "na" : "warn"}`} title={i}>
          {findingLabel(i)}
        </span>
      ))}
    </span>
  );
}

// Kurzfassung der Referenz: DNS-Name · Netz · VLAN · Standort (für Tabellen)
export function refSummary(r: RefInfo | null): string {
  if (!r) return "";
  return [
    r.dns_name || r.name,
    r.prefix && r.prefix !== r.value ? r.prefix : null,
    r.vlan != null ? `VLAN ${r.vlan}${r.vlan_name ? ` (${r.vlan_name})` : ""}` : null,
    r.site,
  ]
    .filter(Boolean)
    .join(" · ");
}

// Was die Anreicherung zusätzlich oder abweichend zur führenden Referenz weiss
export function enrichmentExtra(enr: RefInfo, ref: RefInfo | null): string {
  const differs = (a: unknown, b: unknown) => a != null && a !== "" && a !== b;
  const dns = (x: string | null | undefined) => (x ?? "").replace(/\.$/, "").toLowerCase();
  return [
    enr.dns_name && dns(enr.dns_name) !== dns(ref?.dns_name) ? `DNS ${enr.dns_name}` : null,
    differs(enr.vlan, ref?.vlan) ? `VLAN ${enr.vlan}` : null,
    differs(enr.site, ref?.site) ? enr.site : null,
    differs(enr.tenant, ref?.tenant) ? enr.tenant : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function SourceLink({ r }: { r: RefInfo }) {
  const label = r.source ?? L("Quelle", "Source");
  return r.url ? (
    <a
      className="chip info"
      href={r.url}
      target="_blank"
      rel="noreferrer"
      title={L("In der Quelle öffnen", "Open in source")}
    >
      {label} <Icon name="external" size={11} />
    </a>
  ) : (
    <span className="chip info">{label}</span>
  );
}

// Ein Wert mit Abgleich: IP/Netz, Match-Status, Referenz (führend) und Anreicherung
export function ValueLine({ v, compact }: { v: RefValue; compact?: boolean }) {
  const ref = v.reference;
  const enr = v.enrichment;
  return (
    <div className="refline">
      <span className="mono">{v.value}</span>
      {v.match_status && v.match_status !== "matched" && <MatchBadge status={v.match_status} />}
      {v.zone && <span className="chip">{v.zone}</span>}
      {ref && (
        <span className="ref-text">
          {refSummary(ref) || KIND_LABEL[ref.kind] || ref.kind}
          {!compact && <SourceLink r={ref} />}
        </span>
      )}
      {!compact && enr && (
        <span className="ref-text faint">
          {ref ? enrichmentExtra(enr, ref) : refSummary(enr)}
          <SourceLink r={enr} />
        </span>
      )}
      {v.vm && <VmChip vm={v.vm} />}
    </div>
  );
}

// VM hinter einer IP (vCenter, ADR 0017): Name, Portgruppe → EPG im Tooltip
export function VmChip({ vm }: { vm: VmInfo }) {
  const title = [
    `VM ${vm.name}`,
    vm.power_state === "poweredOff" ? L("ausgeschaltet", "powered off") : null,
    vm.cluster ? `Cluster ${vm.cluster}` : null,
    vm.portgroup ? `${L("Portgruppe", "Port group")} ${vm.portgroup}${vm.vlan != null ? ` (VLAN ${vm.vlan})` : ""}` : null,
    vm.epg ? `EPG ${vm.epg}` : null,
    vm.others?.length ? L(`gleiche IP auch an: ${vm.others.join(", ")}`, `same IP also on: ${vm.others.join(", ")}`) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className={`chip ${vm.power_state === "poweredOff" ? "na" : "info"}`} title={title}>
      VM {vm.name}
      {vm.epg ? ` · ${vm.epg.split("/").pop()}` : ""}
    </span>
  );
}

const POWER_STATE: Record<string, [string, string]> = {
  poweredOn: ["ok", L("an", "on")],
  poweredOff: ["na", L("aus", "off")],
  suspended: ["warn", L("pausiert", "suspended")],
};

// VM im Detail (vCenter, 0.14.0): Status, Gast-OS, Netzwerk (NIC, Portgruppe → EPG), Lage und
// Ressourcen – im Regel-, Objekt- und NAT-Detail
export function VmCard({ vm, role }: { vm: VmInfo; role?: string }) {
  const [tone, state] = POWER_STATE[vm.power_state ?? ""] ?? ["na", vm.power_state ?? "–"];
  const res = [
    vm.vcpus != null ? `${vm.vcpus} vCPU` : null,
    vm.memory_mb != null ? `${Math.round((vm.memory_mb / 1024) * 10) / 10} GB RAM` : null,
    vm.disk_gb != null ? `${Math.round(vm.disk_gb)} GB Disk` : null,
  ].filter(Boolean);
  const rows: [string, ReactNode][] = [
    [L("Gast-OS", "Guest OS"), vm.guest_os],
    ["Hostname", vm.hostname],
    ["IPs", vm.ips?.length ? vm.ips.join(", ") : null],
    [
      L("Netzwerkkarte", "Network adapter"),
      vm.nic
        ? `${vm.nic}${vm.mac ? ` · ${vm.mac}` : ""}${vm.connected === false ? L(" · getrennt", " · disconnected") : ""}`
        : null,
    ],
    [L("Portgruppe", "Port group"), vm.portgroup ? `${vm.portgroup}${vm.vlan != null ? ` (VLAN ${vm.vlan})` : ""}` : null],
    [
      "EPG",
      vm.epg ? (
        <>
          <span className="mono">{vm.epg}</span>
          {vm.epg_match && (
            <span className="faint">
              {L(
                <> · über {vm.epg_match === "vlan" ? "das VLAN" : "den Namen"}</>,
                <> · via {vm.epg_match === "vlan" ? "the VLAN" : "the name"}</>,
              )}
            </span>
          )}
        </>
      ) : null,
    ],
    ["Cluster / Host", [vm.cluster, vm.host].filter(Boolean).join(" · ") || null],
    ["Datacenter", vm.datacenter],
    [L("Ressourcen", "Resources"), res.length ? res.join(" · ") : null],
    [L("Notiz", "Note"), vm.notes],
    [
      L("Gleiche IP", "Same IP"),
      vm.others?.length ? L(`auch an ${vm.others.join(", ")}`, `also on ${vm.others.join(", ")}`) : null,
    ],
  ];
  return (
    <div className="refblock">
      <div className="refline">
        <span className="chip info">VM</span> <b className="mono">{vm.name}</b>
        <span className={`chip ${tone}`}>{state}</span>
        {vm.template && <span className="chip">{L("Vorlage", "Template")}</span>}
        {role && <span className="faint">{role}</span>}
      </div>
      <dl className="kv tight">
        {rows
          .filter(([, v]) => v !== null && v !== undefined && v !== "")
          .map(([k, v]) => (
            <Pair key={k} k={k} v={v} />
          ))}
      </dl>
    </div>
  );
}

// VMs eines Detail-Fensters (jede VM einmal, mit Rolle wie „Quelle“ oder „Ziel 10.0.0.5“)
export function VmSection({ items }: { items: { vm: VmInfo; role?: string }[] }) {
  const seen = new Set<number>();
  const list = items.filter(({ vm }) => !seen.has(vm.id) && seen.add(vm.id));
  if (!list.length) return null;
  return (
    <>
      <div className="sub-h">VMs ({list.length})</div>
      {list.map(({ vm, role }) => (
        <VmCard key={vm.id} vm={vm} role={role} />
      ))}
    </>
  );
}

// Vollständige Referenzdaten (Detailansicht)
export function RefInfoBlock({ title, r }: { title: string; r: RefInfo }) {
  const rows: [string, ReactNode][] = [
    [L("Quelle", "Source"), <SourceLink r={r} key="s" />],
    [L("Art", "Type"), KIND_LABEL[r.kind] ?? r.kind],
    [L("DNS-Name", "DNS name"), r.dns_name],
    ["Name", r.name],
    [L("Netz", "Network"), r.prefix],
    ["VLAN", r.vlan != null ? `${r.vlan}${r.vlan_name ? ` (${r.vlan_name})` : ""}` : null],
    ["VRF", r.vrf],
    [L("Standort", "Site"), r.site],
    ["Tenant", r.tenant],
    ["Status", r.status],
    [L("Beschreibung", "Description"), r.description],
  ];
  return (
    <div className="refblock">
      <div className="faint" style={{ fontSize: 11, fontWeight: 600 }}>
        {title}
      </div>
      <dl className="kv tight">
        {rows
          .filter(([, v]) => v !== null && v !== undefined && v !== "")
          .map(([k, v]) => (
            <Pair key={k} k={k} v={v} />
          ))}
      </dl>
    </div>
  );
}

function Pair({ k, v }: { k: string; v: ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

// Werte eines Objekts, aufklappbar mit allen Referenzdaten
export function ValueList({ values, service }: { values: RefValue[]; service?: boolean }) {
  if (!values.length) return <Dash />;
  if (service) {
    return (
      <span className="chips">
        {values.map((v) => (
          <span className="chip" key={v.value}>
            {v.value}
          </span>
        ))}
      </span>
    );
  }
  return (
    <div className="objs">
      {values.map((v, i) =>
        v.reference || v.enrichment ? (
          <details key={i} className="refdetails">
            <summary>
              <ValueLine v={v} />
            </summary>
            {v.reference && <RefInfoBlock title={L("Referenz (führend)", "Reference (primary)")} r={v.reference} />}
            {v.enrichment && <RefInfoBlock title={L("Anreicherung", "Enrichment")} r={v.enrichment} />}
          </details>
        ) : (
          <ValueLine key={i} v={v} />
        ),
      )}
    </div>
  );
}

// Regel-Verweis: Objekt wie auf der Firewall, bei Gruppen mit Mitgliedern
// Name eines Objekts als Link "alle Regeln mit diesem Objekt" (wenn onObject gesetzt)
function ObjLink({ name, onObject }: { name: string; onObject?: (name: string) => void }) {
  if (!onObject) return <span className="mono">{name}</span>;
  return (
    <button
      className="linkbtn mono objlink"
      title={L("Alle Regeln mit diesem Objekt", "All rules with this object")}
      onClick={() => onObject(name)}
    >
      {name}
    </button>
  );
}

export function RefCard({ r, onObject }: { r: RuleRef; onObject?: (name: string) => void }) {
  const service = r.role === "service";
  const linkable = !service && r.category ? onObject : undefined;
  return (
    <div className="refcard">
      <div className="refcard-head">
        <b>
          <ObjLink name={r.name} onObject={linkable} />
        </b>
        {r.family === "v6" && (
          <span
            className="chip"
            title={L(
              "IPv6-Objekt (address6/addrgrp6) – eigenes Objekt neben einem gleichnamigen IPv4-Objekt",
              "IPv6 object (address6/addrgrp6) – separate object next to an IPv4 object of the same name",
            )}
          >
            IPv6
          </span>
        )}
        {r.category ? (
          <span className="faint">
            {CATEGORY_LABEL[r.category] ?? r.category}
            {r.obj_type ? ` · ${r.obj_type}` : ""}
          </span>
        ) : (
          <span className="faint">{L("eingebaut", "built-in")}</span>
        )}
        {!service && r.match_status && <MatchBadge status={r.match_status} />}
      </div>
      {r.comment && <div className="faint">{r.comment}</div>}
      {r.values.length > 0 && <ValueList values={r.values} service={service} />}
      {r.members.length > 0 && (
        <div className="members">
          {r.members.map((m) => (
            <div key={m.name} className="member">
              <ObjLink name={m.name} onObject={!service && m.category ? onObject : undefined} />
              {m.category ? null : <span className="faint">{L(" (nicht gelesen)", " (not read)")}</span>}
              <ValueList values={m.values} service={service} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Seite ohne Endpunkt (ACI: Contract ohne Consumer/Provider = nicht wirksam) bzw. negiert
export function SideMarks({ r, side }: { r: Rule; side: "src" | "dst" | "service" }) {
  const aci = r.domain === "aci_contract";
  const missing = side !== "service" && r.missing?.includes(side);
  const negated = r.negate?.includes(side);
  if (!missing && !negated) return null;
  const label = aci
    ? side === "src"
      ? L("kein Consumer", "no consumer")
      : L("kein Provider", "no provider")
    : side === "src"
      ? L("keine Quelle", "no source")
      : L("kein Ziel", "no destination");
  return (
    <span className="chips" style={{ marginBottom: 2 }}>
      {missing && (
        <span
          className="chip warn"
          title={
            aci
              ? L(
                  "Contract wird nicht konsumiert bzw. bereitgestellt – nicht wirksam",
                  "Contract is not consumed or provided – not effective",
                )
              : L("keine lesbare Angabe – Policy prüfen", "no readable value – check the policy")
          }
        >
          {label}
          {aci ? L(" · nicht wirksam", " · not effective") : ""}
        </span>
      )}
      {negated && (
        <span
          className="chip crit"
          title={L(
            "negiert: die Regel gilt für alles AUSSER den genannten Objekten",
            "negated: the rule applies to everything EXCEPT the listed objects",
          )}
        >
          {L("nicht (negiert)", "not (negated)")}
        </span>
      )}
    </span>
  );
}

// Dienste mit dem Objektnamen der Konvention (0.36.0): ACI-Filter stehen als „Tenant/FT_x“ im
// Store – angezeigt wird „FT_x“; FortiGate-Services wie auf der Firewall
export function serviceNames(r: Pick<Rule, "service_names" | "aci">): string[] {
  const names = r.service_names ?? [];
  return r.aci ? [...new Set(names.map((n) => n.split("/").pop() ?? n))] : names;
}

export const FAMILY_LABEL: Record<string, string> = { v4: "IPv4", v6: "IPv6", dual: "IPv4+IPv6" };

// VDOMs je Quellsystem (GET /systems, hoechstens einmal je Minute geladen)
let vdomCache: { at: number; map: Promise<Map<number, string[]>> } | null = null;
function loadVdoms(): Promise<Map<number, string[]>> {
  if (!vdomCache || Date.now() - vdomCache.at > 60_000) {
    const map = fetchSystems()
      .then((list) => new Map(list.map((s) => [s.id, s.vdoms ?? []] as [number, string[]])))
      .catch(() => new Map<number, string[]>());
    vdomCache = { at: Date.now(), map };
  }
  return vdomCache.map;
}

// VDOM anzeigen? Eine FortiGate nur mit „root“ ist die Firewall selbst – „VDOM root“ nur bei
// Firewalls mit weiteren VDOMs, andere VDOMs immer (0.17.0)
export function useVdomShown(): (sourceSystemId: number | null | undefined, vdom?: string | null) => boolean {
  const [map, setMap] = useState<Map<number, string[]> | null>(null);
  useEffect(() => {
    let active = true;
    loadVdoms().then((m) => active && setMap(m));
    return () => {
      active = false;
    };
  }, []);
  return (sid, vdom) => !!vdom && (vdom !== "root" || (map?.get(sid ?? -1)?.length ?? 0) > 1);
}

// FortiGate-VDOM (ADR 0019): Policy-IDs und Objektnamen gelten je VDOM. Den Standard-VDOM
// „root“ nur zeigen, wenn es weitere VDOMs gibt (`always`, siehe useVdomShown).
export function VdomChip({ vdom, always }: { vdom?: string | null; always?: boolean }) {
  if (!vdom || (vdom === "root" && !always)) return null;
  return (
    <span
      className="chip"
      title={L(
        "FortiGate-VDOM – Policy-IDs und Objektnamen gelten je VDOM",
        "FortiGate VDOM – policy IDs and object names apply per VDOM",
      )}
    >
      VDOM {vdom}
    </span>
  );
}
const DIFF_LABEL: Record<string, string> = {
  src: L("Quelle", "Source"),
  dst: L("Ziel", "Destination"),
  service: L("Dienste", "Services"),
  name: "Name",
  zone: L("Interfaces/Zonen", "Interfaces/zones"),
  action: L("Aktion", "Action"),
};

// Adressfamilie und IPv4/IPv6-Gegenstück (FortiGate führt beide als eigene Policies)
export function FamilyMarks({ r, onPair }: { r: Rule; onPair?: (id: number) => void }) {
  if (!r.family && !r.pair) return null;
  const p = r.pair;
  const other = p ? FAMILY_LABEL[p.family ?? ""] ?? L("Gegenstück", "Counterpart") : "";
  const diffs = p?.diff.map((d) => DIFF_LABEL[d] ?? d).join(", ");
  const diff = p?.diff.length ? L(`abweichend: ${diffs}`, `differs: ${diffs}`) : "";
  const title = p
    ? L(
        `${other}-Gegenstück „${p.name}“ (#${p.rule_id}) – `,
        `${other} counterpart “${p.name}” (#${p.rule_id}) – `,
      ) +
      (p.match === "same"
        ? L(
            "deckt dasselbe ab (gleiche Objektnamen, Dienste und Interfaces)",
            "covers the same (same object names, services and interfaces)",
          )
        : p.match === "confirmed"
          ? L(
              `von Hand als Paar bestätigt${diff ? `, ${diff}` : ""}`,
              `confirmed as a pair by hand${diff ? `, ${diff}` : ""}`,
            )
          : L(`ähnlich, ${diff}`, `similar, ${diff}`)) +
      L(
        ". Eigene Regel wie auf der FortiGate – nur zur Orientierung verbunden.",
        ". Separate rule as on the FortiGate – linked for orientation only.",
      )
    : "";
  return (
    <div className="chips" style={{ marginTop: 3 }}>
      {r.family && (
        <span className="chip" title={L("Adressfamilie der Policy", "Address family of the policy")}>
          {FAMILY_LABEL[r.family] ?? r.family}
        </span>
      )}
      {p && (
        <button
          className={`chip linkchip ${p.match === "similar" ? "warn" : "ok"}`}
          title={title}
          onClick={(e) => {
            e.stopPropagation();
            onPair?.(p.rule_id);
          }}
        >
          ↔ {other} #{p.rule_id}
          {p.match === "similar"
            ? L(" ähnlich", " similar")
            : p.match === "confirmed"
              ? L(" ✓ bestätigt", " ✓ confirmed")
              : ""}
        </button>
      )}
    </div>
  );
}
