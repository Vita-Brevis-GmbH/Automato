// Schlanker API-Client. Das UI spricht ausschliesslich mit dem Orchestrator
// (ARCHITECTURE.md) - im Dev ueber den Vite-Proxy unter /api.
import * as auth from "./auth";
import { exportLang, L, lang } from "./i18n";

export interface HealthStatus {
  status: string;
  db: string;
  version?: string; // Plattform-Version (Datei VERSION)
}

export interface Rule {
  id: number;
  tenant_id: number;
  source_system_id: number;
  domain: string;
  name: string;
  action: string;
  section_label: string | null;
  src_zone: string | null;
  dst_zone: string | null;
  num_src: number;
  num_dst: number;
  num_services: number;
  num_unmatched: number;
  sources: string[]; // Objektnamen wie auf der Firewall (sonst Werte)
  destinations: string[];
  service_names: string[]; // Service-Namen wie auf der Firewall (sonst kanonisch)
  aci?: AciContract | null; // nur ACI-Contracts: Quelle = Consumer-, Ziel = Provider-EPGs
  nat?: RuleNat | null; // Firewall-Policy mit SNAT bzw. VIP
  missing?: ("src" | "dst")[]; // Seite ohne Endpunkt (ACI: kein Consumer/Provider – nicht wirksam)
  negate?: ("src" | "dst" | "service")[]; // FortiGate: negierte Seiten
  family?: "v4" | "v6" | "dual" | null; // Adressfamilie der Policy
  vdom?: string | null; // FortiGate-VDOM der Policy (ADR 0019)
  pair?: RulePair | null; // IPv4/IPv6-Gegenstück (nur Markierung, eigene Regel)
  parallel?: RuleParallel | null; // Verbund paralleler Firewalls (0.18.0)
  // Suche nach einer IP (0.26.0): kann die Regel Verkehr von/zu ihr nach dem Routing sehen
  relevance?: { state: "possible" | "impossible" | "unknown"; text: string; sides: Record<string, string> } | null;
}

// Parallele Firewalls (Verbund): führende Regel mit den Firewalls, auf denen sie gleich steht,
// abweicht bzw. fehlt – oder eine abweichende Regel mit den Feldern zur führenden
export interface RuleParallel {
  group: string;
  same?: string[];
  differs?: string[];
  missing?: string[];
  lead?: string;
  lead_rule_id?: number;
  fields?: string[];
}

// IPv4/IPv6-Gegenstück derselben Firewall: same = gleiche Objekte/Services/Interfaces
export interface RulePair {
  rule_id: number;
  name: string;
  family: string | null;
  match: "same" | "similar" | "confirmed"; // confirmed = von Hand bestätigt
  diff: string[]; // src | dst | service | name | zone
}

// Von Hand entschiedenes IPv4/IPv6-Paar (bleibt über neue Läufe erhalten)
export interface PairDecision {
  id: number;
  decision: "confirmed" | "rejected";
  partner_id: number | null;
  partner_name: string | null;
  partner_external_id: string;
  decided_by: string;
  decided_at: string;
}

// NAT einer Firewall-Policy: SNAT (Pools; leer = Interface-IP) und VIPs im Ziel (DNAT)
export interface RuleNat {
  snat: boolean;
  pools: string[];
  vips: string[];
}

export interface NatBrief {
  id: number;
  name: string;
  nat_type: string;
  ext_ip: string | null;
  ext_port: string | null;
  mapped_ip: string | null;
  mapped_port: string | null;
  protocol: string | null;
  external_name: string | null;
  ext_dns: string[];
  mapped_dns: string[];
  balancers?: BalancerBrief[]; // 0.79.0: Virtual Services des NSX ALB hinter der VIP
}

// Virtual Service des NSX ALB kompakt (0.79.0): hinter einer VIP der FortiGate
export interface BalancerBrief {
  id: number;
  name: string;
  source?: string;
  ext_ip?: string;
  ext_port?: string;
  pool?: string;
  tenant?: string;
  servers?: string[]; // ip bzw. ip:port
}

export interface AciContract {
  tenant: string | null;
  contract: string | null;
  subject: string | null;
  scope: string | null; // application-profile | context | tenant | global
  dn: string | null;
  kind: "contract" | "taboo" | "preferred_group" | string; // taboo = deny, preferred_group = VRF
}

export type RuleDomain = "firewall" | "aci_contract";

export interface RulePage {
  rules: Rule[];
  total: number; // Treffer insgesamt (nach Filtern/Suche), Header X-Total-Count
  impossible?: number | null; // Suche nach einer IP: routingtechnisch unmögliche (X-Impossible-Count)
}

export interface RuleSummary {
  total: number;
  by_domain: Partial<Record<RuleDomain, number>>;
  source_system_ids: number[];
  endpoints: number;
  unmatched: number;
  by_dst_zone: [string, number][];
  by_src_zone?: [string, number][];
  by_action?: Record<string, number>;
  by_vdom?: [string, number][]; // FortiGate-VDOMs mit Anzahl Regeln
}

export interface Endpoint {
  role: string;
  kind: string;
  name: string | null;
  value: string | null;
  match_status: string | null;
  zone: string | null;
  object_class: string | null;
  fqdn: string | null;
  nat_target: string | null;
  epg?: EpgLocation | null; // ACI: wo liegt der Consumer/Provider
  vm?: VmInfo | null; // VM hinter der IP (vCenter)
  scope_partners?: number | null; // ACI (0.45.1): Gegenstellen im Scope des Contracts, 0 = keine
}

// Lage einer EPG (Contract-Consumer/-Provider): Tenant, AP, BD, VRF, Subnetze, Endpunkte
export interface EpgLocation {
  kind: "epg" | "esg" | "external" | "vzany" | string;
  name: string;
  label: string;
  dn: string | null;
  aci_tenant: string | null;
  ap: string | null;
  bd: string | null;
  vrf: string | null;
  l3out: string | null;
  subnets: {
    ip: string;
    origin: "epg" | "bd" | "ext" | string;
    scope?: string | null; // externe EPG: l3extSubnet.scope (import-security = Zuordnung)
    ipam: { prefix: string; description: string | null; vlan: number | null; site: string | null; source: string } | null;
  }[];
  endpoint_count: number;
  endpoints: { ip: string; mac: string | null; dns_name: string | null }[];
  server: { name: string; ip: string | null; site: string | null } | null;
  selectors: string[]; // ESG: Selektoren (EPG, Tag, IP)
  pref_group: boolean; // Mitglied der Preferred Group seiner VRF
  vlans: number[]; // Encap-VLANs (Netzinventar des APICs)
  port_count: number;
  ports: EpgPort[]; // statische Pfade (Leaf-Ports, begrenzt; Gesamtzahl port_count)
  vms?: { name: string; ips: string[]; power_state: string | null; portgroup: string | null; match: string | null }[]; // VMs (vCenter)
}

export interface EpgPort {
  device: string;
  name: string;
  kind: string;
  lag: string | null;
  mode: string | null;
  neighbor: string | null;
  neighbor_port: string | null;
}

// Referenzdaten eines Werts aus dem IPAM (führend, z. B. DDI) oder der Anreicherung (NetBox)
export interface RefInfo {
  source: string | null; // Name des Quellsystems
  kind: string; // prefix | range | ip_address | host | a_record | ptr_record | fixed_address
  value: string;
  dns_name: string | null;
  name: string | null;
  description: string | null;
  prefix: string | null; // umgebendes Netz
  vlan: number | null;
  vlan_name: string | null;
  vrf: string | null;
  site: string | null;
  tenant: string | null;
  status: string | null;
  attrs: Record<string, unknown> | null;
  url: string | null; // Link in die Quelle (NetBox)
}

// Aufgelöster Wert eines Firewall-Objekts mit Abgleich gegen das IPAM
export interface RefValue {
  value: string;
  match_status: string | null;
  zone: string | null;
  source_of_record: string | null; // infoblox | netbox
  reference: RefInfo | null;
  enrichment: RefInfo | null;
  vm?: VmInfo | null; // VM hinter der IP (vCenter)
}

// VM hinter einer IP (vCenter, ADR 0017)
export interface VmInfo {
  id: number;
  name: string;
  power_state: string | null;
  cluster: string | null;
  portgroup: string | null;
  vlan: number | null;
  epg: string | null; // Tenant/AP/EPG der Portgruppe
  others?: string[]; // weitere VMs mit derselben IP
  // Detailansichten (0.14.0)
  template?: boolean;
  guest_os?: string | null;
  hostname?: string | null;
  datacenter?: string | null;
  host?: string | null;
  vcpus?: number | null;
  memory_mb?: number | null;
  disk_gb?: number | null;
  notes?: string | null;
  nic?: string | null;
  mac?: string | null;
  connected?: boolean | null;
  ips?: string[];
  epg_match?: string | null; // name | vlan
  for_ip?: string; // NAT: welche interne IP
}

// VM aus dem vCenter (Objekte → VMs)
export interface VmRow {
  id: number;
  source_system_id: number;
  source: string | null;
  name: string;
  power_state: string | null;
  template: boolean;
  guest_os: string | null;
  hostname: string | null;
  datacenter: string | null;
  cluster: string | null;
  host: string | null;
  vcpus: number | null;
  memory_mb: number | null;
  disk_gb: number | null;
  annotation: string | null;
  nics: {
    name: string;
    mac: string | null;
    portgroup: string | null;
    switch: string | null;
    vlan: number | null;
    connected: boolean | null;
    ips: { ip: string; dns: string[]; in_ipam: boolean | null }[];
    epg: string | null;
    epg_match: string | null; // name | vlan
  }[];
}

export interface PortgroupRow {
  id: number;
  source_system_id: number;
  source: string | null;
  name: string;
  kind: string; // dvs | standard
  switch: string | null;
  vlan: number | null;
  vlan_ranges: string | null;
  vm_count: number;
  epg: string | null;
  epg_match: string | null;
}

export const fetchVms = (tenantId: number, params: Record<string, string | number | null | undefined> = {}) =>
  getJSON<Paged<VmRow>>(`/api/vms${cq({ tenant_id: tenantId, ...params })}`);
// Portgruppe im Detail (0.13.0): EPG, Subnetze (ACI, Inventar, IPAM) und VMs
export interface PortgroupDetail extends Omit<PortgroupRow, "epg" | "source_system_id"> {
  epg: { label: string; dn: string | null; tenant: string | null; ap: string | null; name: string; bd: string | null; vrf: string | null } | null;
  subnets: { prefix: string; sources: string[]; gateway?: string | null; vrf?: string | null; description?: string | null }[];
  vms: {
    id: number;
    name: string;
    power_state: string | null;
    template: boolean;
    nic: string;
    mac: string | null;
    connected: boolean | null;
    ips: { ip: string; dns: string[]; in_ipam: boolean | null }[];
    cluster: string | null;
    host: string | null;
  }[];
}
export const fetchPortgroup = (id: number) => getJSON<PortgroupDetail>(`/api/portgroups/${id}`);
export const fetchPortgroups = (tenantId: number, q?: string) =>
  getJSON<PortgroupRow[]>(`/api/portgroups${cq({ tenant_id: tenantId, q })}`);

export interface RefMember {
  name: string;
  category: string | null;
  obj_type: string | null;
  comment: string | null;
  values: RefValue[];
}

// Verweis einer Regel auf ein Firewall-Objekt – so, wie es auf dem Gerät steht
export interface RuleRef {
  role: string; // src | dst | service
  name: string;
  family?: string | null; // v4 | v6 (gleichnamige Objekte je Familie)
  category: string | null; // null = Built-in/nicht als Objekt gelesen ("all")
  obj_type: string | null;
  comment: string | null;
  color: number | null;
  values: RefValue[];
  members: RefMember[];
  match_status: string | null;
}

export interface RuleDetail extends Rule {
  external_id: string;
  sequence: number | null;
  refs: RuleRef[];
  endpoints: Endpoint[];
  services: string[];
  protections?: string[] | null;
  comment?: string | null;
  nat_entries?: NatBrief[];
  pair_decisions?: PairDecision[];
  twins?: { rule_id: number; source_system_id: number; source: string }[]; // gleich im Verbund
  // ACI (0.45.1): was der Scope verbindet (AP, VRF, Tenant, global)
  aci_scope?: { scope: string | null; label: string; text: string; pairs: number; unpaired: number } | null;
  // ACI ist zustandslos (0.69.1): Dienste, die der Provider zum Consumer aufbauen kann
  aci_direction?: {
    reverse: string[];
    both: boolean;
    text: string;
    derived: boolean;
    replies_only?: boolean; // 0.72.1: Consumer → Provider nur Antworten (Filter nur mit Quellport)
  } | null;
}

export interface Finding {
  id: number;
  tenant_id: number;
  type: string;
  severity: string;
  status: string;
  subject_kind: string;
  subject_id: string;
  source_system_id: number | null;
  detail: Record<string, unknown> | null;
  message: string | null; // Befund in einem Satz (was genau nicht stimmt)
  hint: string | null; // was zu tun wäre
  note: string | null;
  first_seen: string | null;
  last_seen: string | null;
}

export interface System {
  id: number;
  tenant_id: number | null; // null = Altbestand, noch keinem Mandanten zugeordnet
  name: string;
  platform: string; // fortigate | cisco_aci | infoblox | netbox
  enabled: boolean;
  base_url: string;
  agent_id: number | null; // verwaltender Collector-Agent (null = zentraler Worker)
  verify_tls: boolean;
  read_scope: string; // Cisco ACI: gelesene ACI-Tenants (kommagetrennt, leer = alle); Switches: Adressen
  host_keys?: { host: string; fingerprint: string }[]; // Switches (0.62.0): festgehaltene SSH-Host-Schlüssel
  has_credentials: boolean; // Zugangsdaten selbst werden nie ausgegeben (nur schreibbar)
  ref_role: string | null; // Referenzquellen: primary (IPAM führend) | enrichment (ADR 0010)
  has_write_token: boolean; // NetBox: Schreib-Token für den NetBox-Sync hinterlegt
  reference_count: number; // Referenzquellen: Einträge im aktuellen Stand
  reference_at: string | null;
  site_id: number | null; // Standort des Geräts (NetBox-Sync)
  site_hint: SiteHint | null; // Standort aus dem Gerätenamen (Vorschlag)
  schedule: string | null; // Zeitplan (cron, 5 Felder)
  next_run_at: string | null; // nächster Termin laut Zeitplan
  rule_count: number;
  nat_count: number;
  vdoms?: string[]; // FortiGate: gelesene VDOMs – nur „root“ = die Firewall selbst
  parallel_group?: string | null; // Verbund paralleler Firewalls (Regeln zusammengefasst)
}

export interface ParallelSuggestion {
  system_ids: number[];
  names: string[];
  group: string; // vorgeschlagener Name des Verbunds
  reasons: string[]; // z. B. „Session-Sync (FGSP)“, „312 von 320 Policies gleich“
  rejected?: boolean; // abgelehnt: wird nicht mehr vorgeschlagen
}
export const fetchParallelSuggestions = (tenantId: number, includeRejected = false) =>
  getJSON<ParallelSuggestion[]>(
    `/api/systems/parallel-suggestions${qs({ tenant_id: tenantId, include_rejected: includeRejected ? "true" : undefined })}`,
  );
// Vergleich je Verbund (0.72.0): gleich/abweichend/fehlt je Bereich; führend = erstes Mitglied
export type ParallelCounts = { same?: number; differs?: number; missing?: number };
export type ParallelGroup = {
  group: string;
  members: { id: number; name: string }[];
  stats: {
    rules: ParallelCounts;
    objects: ParallelCounts;
    policy_routes: ParallelCounts;
    routes: ParallelCounts;
    nat: ParallelCounts;
  };
};
export const fetchParallelGroups = (tenantId: number) =>
  getJSON<ParallelGroup[]>(`/api/systems/parallel${cq({ tenant_id: tenantId })}`);
// Nicht parallel: Vorschlag ablehnen bzw. Verbund auflösen (beides: nicht mehr vorschlagen)
export const rejectParallel = (tenantId: number, sel: { system_ids?: number[]; group?: string }) =>
  sendJSON<{ source_ids: number[] }>("/api/systems/parallel-reject", "POST", { tenant_id: tenantId, ...sel });
export const unrejectParallel = (tenantId: number, systemIds: number[]) =>
  sendJSON<{ source_ids: number[] }>("/api/systems/parallel-reject/undo", "POST", {
    tenant_id: tenantId,
    system_ids: systemIds,
  });
// Verbund setzen (alle Firewalls); leere Liste = Verbund auflösen
export const setParallelGroup = (tenantId: number, group: string, systemIds: number[]) =>
  sendJSON<System[]>("/api/systems/parallel-group", "POST", {
    tenant_id: tenantId,
    group,
    system_ids: systemIds,
  });

// Anzahl gelöschter Einträge je Art (Rückgabe der Lösch-Endpunkte)
export type DeleteCounts = Record<string, number>;

export interface Tenant {
  id: number;
  name: string;
  code: string | null;
  audit_reads: boolean;
  ipam_type: string;
  dns_type: string;
  naming_profile_id: number | null;
}

// Standorte (ADR 0015): virtuell = Nummer der Namenskonvention (000, 001), physisch = Ort-Code
// (chzrh1); ein virtueller Standort kann an mehreren physischen laufen.
export type SiteKind = "virtual" | "physical";
export interface Site {
  id: number;
  tenant_id: number;
  code: string;
  name: string;
  description: string | null;
  kind: SiteKind;
  physical_site_ids: number[];
  virtual_site_ids: number[];
}
// Standort aus einem Netz-/Geräte-/Cluster-Namen (Vorschlag); known=false: Code nicht erfasst
export interface SiteHint {
  code: string;
  kind: SiteKind;
  known: boolean;
  id?: number;
  name?: string;
  physical?: string[];
  virtual?: string[];
}

export interface AuthConfig {
  auth_mode: string; // dev | oidc
  issuer: string;
  client_id: string;
  scope: string;
  admin_console_url: string; // Keycloak-Benutzerverwaltung (leer = unbekannt)
}

export interface AgentSystem {
  id: number;
  name: string;
  tenant_id: number | null;
  has_credentials: boolean;
}

export interface Agent {
  id: number;
  name: string;
  tenant_id: number | null;
  enabled: boolean;
  last_seen: string | null;
  systems: AgentSystem[]; // zugewiesene Geräte (Quellsysteme)
  version: string | null; // vom Agenten gemeldete Paket-Version
  auto_update: boolean; // Selbst-Update erlaubt (ADR 0011)
  update_available: boolean | null; // null = Version unbekannt
  update_note?: string | null; // warum der Agent nicht aktualisiert (z. B. Signatur fehlt)
}

export interface AgentCreated extends Agent {
  token: string; // nur einmal sichtbar
}

export interface Run {
  id: string;
  platform: string;
  status: string; // queued | running | completed | failed
  tenant_id: number | null;
  source_system_id: number | null;
  rule_count: number | null;
  message: string | null;
  execution: string; // inprocess | external
  trigger: string; // manual | schedule
  queued_at: string | null;
  started_at?: string | null; // abgeholt bzw. vom zentralen Worker gestartet
  finished_at: string | null;
}

export interface Nat {
  id: number;
  name: string;
  external_id: string;
  nat_type: string; // dnat_vip | snat_pool
  ext_ip: string | null;
  ext_port: string | null;
  mapped_ip: string | null;
  mapped_port: string | null;
  protocol: string | null;
  portforward: boolean;
  source_system_id: number;
  comment: string | null;
  interface: string | null;
  external_name: string | null;
  ext_dns: string[];
  mapped_dns: string[];
  server: string | null;
  servers?: string[]; // alle Objekte mit der internen IP (mehrere = doppelt definiert)
  rule_count: number;
  vip_type?: string | null; // static-nat | server-load-balance (Virtual Server) | …
  family?: string | null; // v4 | v6
  vdom?: string | null; // FortiGate-VDOM
  targets?: NatTarget[]; // Virtual Server: Real Server
  attrs?: Record<string, unknown>; // Virtual Server: Server-Typ, Methode, Persistenz, Health-Checks
  issues?: string[]; // Korrektheitsprüfung (0.14.0): Befunde des VIP-/Pool-Objekts, Ziel ohne IPAM
}

// Real Server eines Virtual Servers (FortiGate server-load-balance)
export interface NatTarget {
  ip: string | null;
  address: string | null; // Adressobjekt statt IP (z. B. FQDN)
  port: number | string | null;
  status: string | null; // active | standby | disable
  weight: number | string | null;
  healthcheck: string | null;
  monitor: string[];
  holddown: number | string | null;
  max_connections: number | string | null;
  http_host: string | null;
  servers: string[]; // Firewall-Objekte mit dieser IP
  dns: string[];
}

// IPAM-Sicht auf eine IP je Referenzquelle (führend zuerst)
export interface IpInfo {
  source: string;
  role: string;
  dns_names: string[];
  records: { kind: string; dns_name: string | null; name: string | null }[];
  prefix: { value: string; description: string | null; vlan: number | null; site: string | null; vrf: string | null } | null;
  url: string | null;
}

export interface NatDetail extends Nat {
  ext_info: IpInfo[];
  mapped_info: IpInfo[];
  color: number | null;
  object_id: number | null; // VIP- bzw. Pool-Objekt
  settings: Record<string, string>;
  rules: Rule[];
  vms: VmInfo[]; // VMs hinter dem internen Ziel
}

// Publizierte Dienste (VIPs)
export interface PublishedService {
  name: string;
  ext_ip: string | null;
  ext_port: string | null;
  mapped_ip: string | null;
  mapped_port: string | null;
  protocol: string | null;
  portforward: boolean;
  rule_count: number;
  source?: string | null; // Quellsystem (FortiGate bzw. NSX ALB)
  platform?: string | null; // fortigate | avi (0.77.0, ADR 0027)
  servers?: number; // Server des Pools bzw. Real Server
  pool?: string | null; // NSX ALB: Pool bzw. Pool Group
  balancers?: BalancerBrief[]; // 0.79.0: VIP der FortiGate -> Virtual Services des NSX ALB
  fronted_by?: { name: string; ext_ip: string | null; ext_port: string | null; source?: string | null }[]; // VIPs davor
}

export interface AppRule {
  id: number;
  name: string;
  action: string;
  src_zone: string | null;
  sources: string[];
  services: string[];
}

export interface PublishedServiceDetail extends PublishedService {
  rules: AppRule[];
}

// Applikations-Doku (systemzentriert, 10 Kategorien)
export interface AppDocSystem {
  name: string;
  ip: string | null;
  rule_count: number;
  kind: string; // host | epg
}

export interface DocTable {
  index: number;
  title: string;
  enforcement: string;
  // sichtbare Spalten (0.84.0: leere NAT-/VIP-/Load-Balancer-Spalten fehlen)
  columns: string[];
  // 0.87.0: Anzeige der Spalten in der Sprache der Anfrage (parallel zu columns; columns = Schlüssel)
  labels?: string[];
  // Spalten als Text (mehrere Einträge mit Zeilenumbruch); dazu _rule (Regel-ID), _dir (in |
  // out), _scope (Art des Bezugs), _rules (Applikations-Doku: Regeln der Zeile, bei einem
  // IPv4/IPv6-Paar beide), _systems (unspezifische Regel, 0.37.0: Systeme der Applikation, die
  // sie nutzen können), _app/_peer (Seite der Applikation bzw. Gegenstelle) und _app_col
  // (Source bzw. Destination - dort steht die Applikation)
  rows: Record<string, string | number | number[] | string[] | null>[];
  // 0.69.0 (Applikations-Doku): ausgeblendete Zeilen mit _hides (Begründung, wer, wann)
  hidden?: (Record<string, string | number | number[] | string[] | null> & { _hides?: DocHide[] })[];
}
export interface DocHide {
  id: number;
  rule_ref: string;
  category: number | null; // leer = alle Kategorien
  reason: string;
  label: string;
  by: string;
  at: string | null;
  active?: boolean; // steht in einer Kategorie
}

// VM hinter dem System (vCenter): beim Server die VM der IP, bei der EPG die VMs ihrer Portgruppen
export interface DocVm {
  name: string;
  power_state: string | null;
  portgroup: string | null;
  ips: string[];
  epg: string | null;
}

export interface SystemDoc {
  system: string;
  system_ip: string | null;
  tenant_code: string | null;
  site: SiteRef | null;
  vms?: DocVm[];
  tables: DocTable[];
  // routingtechnisch nicht möglich (0.24.0) – nicht in den Tabellen, mit Begründung
  impossible?: { rule_id: number | null; description: string; side: "src" | "dst" | null; text: string }[];
}

// Applikationen (ADR 0012): Mitglieder AP (`Tenant/AP`), EPG (DN) und Server (Objektname)
export type MemberKind = "ap" | "epg" | "host" | "rule" | "norule"; // rule/norule: Regel zugeordnet bzw. ausgeschlossen (0.60.0)
export type Criticality = "low" | "medium" | "high" | "critical";
export type Purpose = "dev" | "test" | "prod";
export interface SiteRef {
  id: number;
  code: string;
  name: string;
  kind?: SiteKind;
  physical?: string[]; // virtuell: Codes der physischen Standorte
  virtual?: string[]; // physisch: Codes der virtuellen Standorte
}

export interface Application {
  id: number;
  tenant_id: number;
  name: string;
  external_name: string | null; // Name gegen aussen (Fachbereich/Kunde)
  internal_name: string | null; // Name im Betrieb
  purpose: Purpose | null; // Verwendungszweck
  code: string | null;
  owner: string | null;
  criticality: Criticality | null;
  site: SiteRef | null;
  description: string | null;
  infrastructure?: boolean; // 0.88.0: Monitoring, Backup, DNS u. a. (Abhängigkeiten getrennt)
  member_count: number;
  system_count: number;
  vm_count: number; // 0.75.0: VMs an den Portgruppen ihrer EPGs bzw. hinter ihren Servern
  // feinere Rollen: Kunden-Benutzer darf sie pflegen (editor, ganz im Ausschnitt); Betreuer: null
  editable?: boolean | null;
}

export interface AppMember {
  id: number;
  kind: MemberKind;
  ref: string;
  label: string;
  origin: "manual" | "suggestion" | string;
  exists: boolean;
  systems: string[];
}

export interface ApplicationDetail extends Application {
  members: AppMember[];
}

export interface AppCandidate {
  kind: MemberKind;
  ref: string;
  label: string;
  detail: string | null;
  project: string | null;
  applications: number[];
}

export interface AppSuggestion {
  key: string; // 0.74.1: <ACI-Tenant>|<Projektnummer> bzw. die Nummer
  code: string;
  tenant: string | null; // ACI-Tenant der APs (gleiche Nummer in mehreren Tenants = getrennt)
  name: string;
  application_id: number | null;
  members: AppCandidate[];
}

export interface ApplicationInput {
  name?: string;
  external_name?: string | null;
  internal_name?: string | null;
  purpose?: Purpose | null;
  code?: string | null;
  owner?: string | null;
  criticality?: Criticality | null;
  site_id?: number | null;
  description?: string | null;
  infrastructure?: boolean;
}

// Applikations-Doku (0.14.0): Systeme (VMs bzw. Server ohne VM), EPGs, Treffer je Regel und
// System (Richtung, Bezug), Regeln wie in der Regel-Ansicht, Kennzahlen, Befunde
export type DocScope = "assigned" | "specific" | "subnet" | "any" | "vzany" | "pref_group"; // assigned: direkt zugeordnet (0.60.0)
export interface DocRuleCounts {
  specific: number;
  unspecific: number;
}
export interface AppDocSystem2 {
  key: string; // vm:<id> | host:<id>
  kind: "vm" | "host";
  name: string;
  ips: string[];
  dns: string[];
  servers: string[];
  // Firewall-Objekte je Name: auf welchen Firewalls, passt der Name zum DNS-Namen (null = kein DNS)
  objects: {
    name: string;
    name_ok: boolean | null;
    where: { id: number; source_system_id: number; vdom?: string | null }[];
  }[];
  epgs: string[]; // Tenant/AP/EPG
  portgroups: string[];
  // je NIC (0.43.1): Portgruppe → EPG und wie die Zuordnung zustande kam (name | vlan)
  nics?: { name: string; portgroup: string | null; vlan: number | null; ips: string[]; epg: string | null; match: string | null }[];
  hints?: string[]; // z. B. VM-Name nennt ein anderes Projekt als das AP der Portgruppe
  vm_id: number | null;
  power_state: string | null;
  guest_os: string | null;
  cluster: string | null;
  host: string | null;
  site: SiteRef | null;
  member: string; // warum das System dazugehört
  rules: DocRuleCounts;
}
export interface AppDocEpg {
  key: string; // epg:<id>
  id: number;
  label: string; // Tenant/AP/EPG
  dn: string | null;
  tenant: string | null;
  ap: string | null;
  name: string;
  bd: string | null;
  vrf: string | null;
  pref_group: boolean;
  subnets: string[];
  vm_count: number;
  endpoint_count: number;
  member: string;
  rules: DocRuleCounts;
}
export interface AppDocHit {
  rule_id: number;
  system: string; // Schlüssel aus systems/epgs
  table: number; // Kategorie 1–10
  direction: "in" | "out" | "assigned"; // assigned: Regel direkt zugeordnet (0.60.0)
  side: "src" | "dst" | "app"; // Seite der Regel (app: direkt zugeordnet)
  member_id?: number | null;
  object: string; // Objekt der Regel, das das System erfasst (H_web01, N_srv16, any, EPG …)
  value: string; // sein Bereich (10.20.0.0/16; leer bei any/EPG)
  via: string; // Bezug
  scope: DocScope;
  counterpart: string;
  services: string;
  epg?: string | null; // ACI: EPG (Tenant/AP/EPG), über die der Contract für das System gilt (0.42.3)
  parties?: { own: string[]; counter: string[] } | null; // ACI (0.43.2): DNs der Beteiligten
  // Routing (0.24.0): kann die Regel Verkehr zum bzw. vom System sehen; impossible mit Grund
  relevance?: "possible" | "impossible" | "unknown";
  relevance_text?: string;
}
export interface AppDocFinding {
  id: number;
  type: string;
  severity: string;
  status: string;
  subject_kind: string;
  subject_id: string;
  message: string;
  hint: string | null;
  rule_id: number | null;
  epgs?: string[]; // EPGs (Tenant/AP/EPG) des Befunds – Filter nach Tenant, AP, EPG (0.38.0)
}
export interface ApplicationDoc {
  application: Application;
  tenant_code: string | null;
  summary: {
    vms: number;
    servers: number;
    epgs: number;
    rules: number;
    firewall: number;
    aci: number;
    specific: number;
    unspecific: number;
    by_scope: Record<string, number>;
    findings: number;
    impossible?: number; // Regeln nur mit routingtechnisch unmöglichen Treffern
    assigned?: number; // direkt zugeordnete Regeln (0.60.0)
    excluded?: number; // ausgeschlossene Regeln (0.60.0)
    hidden?: number; // in den Kategorien ausgeblendete Zeilen (0.69.0)
  };
  systems: AppDocSystem2[];
  epgs: AppDocEpg[];
  hits: AppDocHit[];
  rules: Rule[];
  findings: AppDocFinding[];
  tables: DocTable[];
  // 0.60.0: direkt zugeordnete (rule) bzw. ausgeschlossene (norule) Regeln, Abhängigkeiten
  rule_members?: { member_id: number; kind: "rule" | "norule"; label: string; rule_id: number }[];
  dependencies?: { uses: AppDependency[]; used_by: AppDependency[] };
  hides?: DocHide[]; // 0.69.0
}
export interface AppDependency {
  application_id: number;
  name: string;
  infrastructure?: boolean; // 0.88.0: andere Applikation ist Infrastruktur
  rules: { rule_id: number; name: string; domain: string; via: string; services: string[]; reverse?: boolean }[];
}
export interface AppDependencies {
  uses: AppDependency[];
  used_by: AppDependency[];
  infrastructure: boolean; // die Applikation selbst
}

// Firewall-Objekt wie auf dem Gerät (Objekt-Ansicht)
export interface DeviceObject {
  id: number;
  source_system_id: number;
  source: string | null;
  category: string; // address | address_group | vip | ippool | service | service_group
  name: string;
  obj_type: string | null;
  family: string | null;
  vdom?: string | null; // FortiGate-VDOM (gleiche Namen in zwei VDOMs = zwei Objekte)
  values: RefValue[]; // Services: nur `value`
  members: string[];
  member_of: string[];
  used_in_rules: number;
  comment: string | null;
  color: number | null;
  interface: string | null;
  fqdn?: string | null;
  attrs?: Record<string, unknown>; // Services: Protokoll, Ports, ICMP, Kategorie, Timer; ACI-Filter: Einträge
  issues: string[]; // Finding-Typen
  site: SiteHint | null; // Netz-Objekte: Standort aus dem Namen
  predefined?: boolean; // Standardobjekt des Herstellers (FortiOS-Service, ACI-Filter in common)
}

// Objekt-Liste (0.36.0): dazu die Anzahl je Typ (Filter) und der Standardobjekte
export interface ObjectPage extends Paged<DeviceObject> {
  types?: Record<string, number>; // obj_type -> Anzahl ("" = ohne Typ)
  predefined?: number;
}

// Objekt-Detail (wie NAT): Attribute, Mitglieder und die Regeln, die es nutzen
export interface ObjectDetail extends DeviceObject {
  members_detail: {
    name: string;
    category: string | null;
    family?: string | null;
    values: string[];
    comment: string | null;
  }[];
  rule_count: number;
  rules: Rule[];
}

export interface Paged<T> {
  total: number;
  rows: T[];
}

// Netzinventar (FortiGate/APIC)
interface InvBase {
  id: number;
  source_system_id: number;
  source: string | null;
  shared?: boolean; // 0.76.0: Kunde gewählt, geteilter Teil der Umgebung
}
export interface InvDevice extends InvBase {
  name: string;
  role: string;
  model: string | null;
  serial: string | null;
  node_id: string | null;
  mgmt_ip: string | null;
  version: string | null;
  site: SiteHint | null; // aus dem Gerätenamen, sonst Standort des Quellsystems
  site_from: "name" | "system" | null;
}
export interface InvInterface extends InvBase {
  device: string;
  name: string;
  kind: string;
  enabled: boolean | null;
  mode: string | null;
  untagged_vlan: number | null;
  tagged_vlans: number[] | null;
  lag: string | null;
  parent: string | null;
  ips: string[] | null;
  description: string | null;
  zone: string | null;
  vdom?: string | null; // FortiGate-VDOM des Interfaces
  neighbor: string | null;
  neighbor_port: string | null;
  policy_group: string | null;
  epgs: string[] | null;
}
export interface InvVlan extends InvBase {
  vid: number;
  name: string;
  group: string | null;
  description: string | null;
  epgs: string[] | null;
}
export interface PrefixInfo {
  source: string;
  found: boolean;
  vlan: number | null;
  description: string | null;
  site: string | null;
  url: string | null;
}
export interface InvSubnet extends InvBase {
  prefix: string;
  gateway: string | null;
  vrf: string | null;
  vlan: number | null;
  context: string;
  context_name: string;
  aci_tenant: string | null;
  ap: string | null;
  epg: string | null;
  bd: string | null;
  description: string | null;
  ipam: PrefixInfo | null;
  enrichment: PrefixInfo | null;
  site: SiteHint | null; // aus dem BD-/Netz-Namen
}
export interface InvVrf extends InvBase {
  name: string;
}
export interface Inventory {
  devices: InvDevice;
  interfaces: InvInterface;
  vlans: InvVlan;
  subnets: InvSubnet;
  vrfs: InvVrf;
}

// Abgleich (ADR 0010)
export interface SourceRef {
  id: number;
  name: string;
  platform: string;
}
export interface ReconcileSummary {
  tenant_id: number;
  findings: Record<string, { open: number; total: number }>;
  reference_sources: (SourceRef & { ref_role: string; has_reference: boolean })[];
  ipam: {
    available: boolean;
    primary?: SourceRef;
    enrichment?: SourceRef;
    summary?: Record<string, number>; // missing | different | only_enrichment | equal
  };
}
export interface RefBrief {
  kind: string;
  value: string;
  dns_name: string;
  description: string | null;
  vlan: number | null;
  site: string | null;
  url: string | null;
}
export interface IpamRow {
  status: string; // missing | different | only_enrichment
  kind: string; // prefix | range | ip
  value: string;
  primary: RefBrief | null;
  enrichment: RefBrief | null;
  diffs: string[];
}
export interface IpamCompare extends Paged<IpamRow> {
  available: boolean;
  primary?: SourceRef;
  enrichment?: SourceRef;
  summary?: Record<string, number>;
}

// NetBox-Sync (ADR 0010): Vorschau -> Übernahme per Klick (admin)
export interface SyncTarget {
  id: number;
  name: string;
  base_url: string;
  ref_role: string | null;
  has_write_token: boolean;
}
export interface SyncChange {
  field: string;
  old: unknown;
  new: unknown;
}
export interface SyncItem {
  kind: string;
  key: string;
  label: string;
  action: string; // create | update | conflict | delete | obsolete
  id?: number;
  manual?: boolean;
  changes?: SyncChange[];
  fields?: Record<string, unknown>;
}
export interface SyncPlan {
  id: number;
  tenant_id: number;
  source_system_id: number;
  target: string;
  target_url: string;
  status: string; // planning | ready | applying | applied | failed | superseded
  options: { sections?: string[]; allow_delete?: boolean };
  summary: Record<string, Record<string, number>> | null;
  totals: Record<string, number>;
  warnings: string[];
  message: string | null;
  result: Record<string, Record<string, number>> | null;
  created_by: string;
  created_at: string;
  applied_by: string | null;
  applied_at: string | null;
  can_apply: boolean;
}

export interface NamingProfile {
  id: number;
  customer: string;
  name: string;
}

// Standardprofil „default": gilt für Mandanten ohne eigenes Namensprofil.
export const DEFAULT_PROFILE = "default";
export const isDefaultProfile = (p: NamingProfile) =>
  p.customer === DEFAULT_PROFILE && p.name === DEFAULT_PROFILE;
export const profileLabel = (p: NamingProfile) =>
  p.customer === p.name ? p.name : `${p.customer} · ${p.name}`;

export interface DictionaryEntry {
  id: number;
  profile_id: number;
  kind: string;
  key: string;
  value: string | null;
  extra: Record<string, unknown> | null;
  updated_at: string | null;
}

// Fehler mit HTTP-Status und der Meldung des Orchestrators (FastAPI `detail`).
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail ? `${detail} (HTTP ${status})` : `HTTP ${status}`);
  }
}

// Verständliche Meldung für die Anzeige im UI.
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401)
      return L(
        "Nicht angemeldet oder Sitzung abgelaufen – bitte neu anmelden.",
        "Not signed in or session expired – please sign in again.",
      );
    if (e.status === 403)
      return L(
        `Keine Berechtigung (${e.detail || "Rolle/Mandant fehlt"}).`,
        `Not permitted (${e.detail || "role/tenant missing"}).`,
      );
    if (e.status === 409) return L(`Existiert bereits (${e.detail}).`, `Already exists (${e.detail}).`);
    if (e.status === 422) return L(`Ungültige Eingabe (${e.detail}).`, `Invalid input (${e.detail}).`);
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

function detailText(body: unknown): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    // Pydantic-Validierungsfehler: [{loc:[...], msg:"..."}]
    return d
      .map((x: { loc?: unknown[]; msg?: string }) =>
        [Array.isArray(x.loc) ? x.loc.filter((l) => l !== "body").join(".") : "", x.msg]
          .filter(Boolean)
          .join(": "),
      )
      .join("; ");
  }
  return "";
}

// Bearer-Token frisch aus der OIDC-Sitzung (erneuert abgelaufene Tokens still).
// Im dev-Modus kein Token -> der Orchestrator nimmt den Standard-Admin an.
async function authHeaders(): Promise<Record<string, string>> {
  if (!auth.isOidc()) return auth.devHeaders(); // dev-Modus: ggf. vorgeführter Kunden-Benutzer
  const token = await auth.accessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Fetch mit Token und einheitlicher Fehlerbehandlung; liefert die rohe Antwort.
async function fetchChecked(url: string, init: RequestInit = {}): Promise<Response> {
  // Sprache der Oberfläche (0.86.0): Texte des Servers folgen schrittweise
  const headers = {
    "Accept-Language": lang,
    ...(await authHeaders()),
    ...(init.headers as Record<string, string>),
  };
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = detailText(body);
    if (response.status === 401 && auth.isOidc()) {
      // Token abgelehnt -> neu anmelden (mit Schleifenschutz, siehe auth.relogin)
      void auth.relogin(
        `${L("Der Orchestrator lehnt das Anmelde-Token ab", "The orchestrator rejects the sign-in token")}: ${detail || "HTTP 401"}`,
      );
    }
    throw new ApiError(response.status, detail);
  }
  return response;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetchChecked(url, init);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const getJSON = <T>(url: string) => request<T>(url);

// Schreib-Helfer (POST/PATCH/DELETE). 204 -> kein Body.
const sendJSON = <T>(url: string, method: string, body?: unknown) =>
  request<T>(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

// Kunden-Filter (ADR 0018 Schritt 2): gilt für die Listen der Doku-Ansichten (Regeln, Objekte,
// NAT, Findings, VMs, Portgruppen, Systeme, Applikationen) - gesetzt über den Umschalter oben
let customerFilter: number | null = null;
export const setCustomerFilter = (id: number | null) => {
  customerFilter = id;
};
export const getCustomerFilter = () => customerFilter;
function cq(params: Record<string, string | number | null | undefined>): string {
  return qs({ ...params, customer_id: customerFilter });
}

function qs(params: Record<string, string | number | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

// Öffentlich (ohne Token)
export const fetchHealth = () => fetch("/api/health").then((r) => r.json() as Promise<HealthStatus>);
export const fetchAuthConfig = async (): Promise<AuthConfig> => {
  const r = await fetch("/api/auth/config");
  if (!r.ok) throw new ApiError(r.status, "");
  return r.json();
};

// Mandanten & Standorte
export const fetchTenants = () => getJSON<Tenant[]>("/api/tenants");
export const createTenant = (body: { name: string; code?: string | null }) =>
  sendJSON<Tenant>("/api/tenants", "POST", body);
export const patchTenant = (
  tenantId: number,
  body: { code?: string | null; audit_reads?: boolean; naming_profile_id?: number | null },
) => sendJSON<Tenant>(`/api/tenants/${tenantId}`, "PATCH", body);
export const fetchSites = (tenantId: number) => getJSON<Site[]>(`/api/tenants/${tenantId}/sites`);
export const createSite = (
  tenantId: number,
  body: {
    code: string;
    name: string;
    description?: string | null;
    kind: SiteKind;
    physical_site_ids?: number[];
  },
) => sendJSON<Site>(`/api/tenants/${tenantId}/sites`, "POST", body);
export const updateSite = (
  tenantId: number,
  siteId: number,
  body: { name?: string; description?: string | null; physical_site_ids?: number[] },
) => sendJSON<Site>(`/api/tenants/${tenantId}/sites/${siteId}`, "PATCH", body);
export const deleteSite = (tenantId: number, siteId: number) =>
  sendJSON<void>(`/api/tenants/${tenantId}/sites/${siteId}`, "DELETE");
// Löscht den Mandanten mit allen Daten; `confirmName` muss dem Namen entsprechen.
export const deleteTenant = (tenantId: number, confirmName: string) =>
  sendJSON<DeleteCounts>(`/api/tenants/${tenantId}${qs({ confirm: confirmName })}`, "DELETE");

// Quellsysteme
// Kunden-Benutzer (ADR 0018) sehen von den Quellsystemen nur die Bezeichnungen
let customerUser = false;
export const setCustomerUser = (on: boolean) => {
  customerUser = on;
};
export const isCustomerUser = () => customerUser;
type SystemLabel = Pick<System, "id" | "tenant_id" | "name" | "platform" | "vdoms" | "parallel_group">;
const fromLabel = (l: SystemLabel): System => ({
  ...l,
  enabled: true,
  base_url: "",
  agent_id: null,
  verify_tls: true,
  read_scope: "",
  has_credentials: false,
  ref_role: null,
  has_write_token: false,
  reference_count: 0,
  reference_at: null,
  site_id: null,
  site_hint: null,
  schedule: null,
  next_run_at: null,
  rule_count: 0,
  nat_count: 0,
});
export const fetchSystems = (tenantId?: number | null) =>
  customerUser
    ? getJSON<SystemLabel[]>(`/api/systems/labels${qs({ tenant_id: tenantId })}`).then((l) => l.map(fromLabel))
    : getJSON<System[]>(`/api/systems${qs({ tenant_id: tenantId })}`);
// Zugangsdaten sind nur schreibbar (FortiGate/NetBox `token`, Cisco ACI/Infoblox `username` +
// `password`, NetBox optional `write_token` nur für den NetBox-Sync): verschlüsselt gespeichert
// und nur dem zugewiesenen Agenten pro Lauf übergeben – das Schreib-Token nie (ADR 0009/0010).
// Bei PATCH: weglassen = unverändert, "" = Feld entfernen.
export type SystemCredentials = {
  token?: string;
  username?: string;
  password?: string;
  write_token?: string;
};
export const createSystem = (
  body: {
    tenant_id: number;
    name: string;
    platform: string;
    base_url?: string;
    agent_id?: number | null;
    verify_tls?: boolean;
    read_scope?: string;
    ref_role?: string | null;
    site_id?: number | null;
    schedule?: string | null;
  } & SystemCredentials,
) => sendJSON<System>("/api/systems", "POST", body);
export const patchSystem = (
  id: number,
  body: {
    tenant_id?: number;
    name?: string;
    base_url?: string;
    enabled?: boolean;
    agent_id?: number | null;
    verify_tls?: boolean;
    read_scope?: string;
    ref_role?: string | null;
    site_id?: number | null;
    schedule?: string | null;
    parallel_group?: string | null;
    forget_host_keys?: string[];
  } & SystemCredentials,
) => sendJSON<System>(`/api/systems/${id}`, "PATCH", body);
export const deleteSystem = (id: number) => sendJSON<DeleteCounts>(`/api/systems/${id}`, "DELETE");
// Erreichbarkeit vom Orchestrator aus (zentral gelesene Systeme): Name, TCP, NetBox-Status
export interface SystemCheck {
  system_id: number;
  name: string;
  ok: boolean;
  hint: string | null;
  steps: { step: "dns" | "tcp" | "http"; ok: boolean; detail: string }[];
}
export const checkSystem = (id: number) => sendJSON<SystemCheck>(`/api/systems/${id}/check`, "POST");

// Läufe
// Alle Läufe seitenweise mit Gesamtzahl (0.13.0); status: open | queued | running | completed | failed
export async function fetchRunsPage(params: {
  tenantId?: number | null;
  status?: string;
  sourceSystemId?: number | null;
  platform?: string;
  trigger?: string;
  limit?: number;
  offset?: number;
}): Promise<{ runs: Run[]; total: number }> {
  const response = await fetchChecked(
    `/api/jobs${qs({
      tenant_id: params.tenantId,
      status: params.status || undefined,
      source_system_id: params.sourceSystemId,
      platform: params.platform || undefined,
      trigger: params.trigger || undefined,
      limit: params.limit,
      offset: params.offset,
    })}`,
  );
  const runs = (await response.json()) as Run[];
  const total = Number(response.headers.get("X-Total-Count") ?? runs.length);
  return { runs, total: Number.isFinite(total) ? total : runs.length };
}
export const startCollect = (body: {
  platform: string;
  tenant_id: number;
  source_system_id: number;
  execution: "inprocess" | "external";
}) =>
  sendJSON<{ run_id: string; status: string; execution: string }>("/api/jobs/collect", "POST", body);

// Alle aktiven Quellsysteme des Mandanten einlesen (Referenzquellen zuerst)
export interface CollectAllItem {
  source_system_id: number;
  name: string;
  platform: string;
  run_id: string | null;
  execution: string | null;
  skipped: string | null;
  blocking_run_id?: string | null; // offener Lauf, der das Quellsystem belegt (abbrechbar)
}
// Offenen Lauf beenden (operator) – z. B. hängt er nach einem Neustart des Agenten
export const cancelRun = (runId: string) => sendJSON<Run>(`/api/jobs/${runId}/cancel`, "POST", {});
export const startCollectAll = (tenantId: number) =>
  sendJSON<{ queued: number; items: CollectAllItem[] }>("/api/jobs/collect-all", "POST", { tenant_id: tenantId });

// Agenten
export const fetchAgents = () => getJSON<Agent[]>("/api/agents");
export const createAgent = (name: string, tenantId: number | null) =>
  sendJSON<AgentCreated>("/api/agents", "POST", { name, tenant_id: tenantId });
export const setAgentEnabled = (id: number, enabled: boolean) =>
  sendJSON<Agent>(`/api/agents/${id}`, "PATCH", { enabled });
export const setAgentAutoUpdate = (id: number, autoUpdate: boolean) =>
  sendJSON<Agent>(`/api/agents/${id}`, "PATCH", { auto_update: autoUpdate });
// Agent-Paket (ZIP: collectors-Wheel + install.ps1/run-agent.ps1, ohne Zugangsdaten)
export const downloadAgentPackage = async (): Promise<Blob> =>
  (await fetchChecked("/api/agents/package")).blob();
// 0.78.2: TLS intern an? Dann spricht der Orchestrator auf :8000 HTTPS mit der internen CA
export interface AgentPackageInfo {
  internal_tls: boolean;
  ca: boolean;
  ca_name: string;
}
export const fetchAgentPackageInfo = () => getJSON<AgentPackageInfo>("/api/agents/package/info");
export const downloadAgentCa = async (): Promise<Blob> => (await fetchChecked("/api/agents/package/ca")).blob();

// Namensprofile & Wörterbücher
export const fetchNamingProfiles = () => getJSON<NamingProfile[]>("/api/naming-profiles");
export const createNamingProfile = (body: {
  customer: string;
  name: string;
  copy_from_profile_id?: number | null; // Vorlage: bestehendes Profil kopieren
  with_defaults?: boolean; // ohne Vorlage: eingebaute Standardwerte
}) => sendJSON<NamingProfile>("/api/naming-profiles", "POST", body);
// Bestehende Wörterbuch-Art (auch aus einem anderen Profil) als neue Art übernehmen
export const copyDictionary = (
  profileId: number,
  body: { source_profile_id: number; source_kind: string; kind: string },
) =>
  sendJSON<{ profile_id: number; kind: string; copied: number }>(
    `/api/naming-profiles/${profileId}/dictionary/copy`,
    "POST",
    body,
  );
export const fetchDictionary = (profileId: number) =>
  getJSON<DictionaryEntry[]>(`/api/naming-profiles/${profileId}/dictionary`);

// Grundschema der Zonen (0.55.0): draw.io-Zeichnung im Namensprofil – Zonen mit Art, Kurzname,
// Farbe und Erkennungsmustern, Übergänge mit Absicht
export interface SchemaZone {
  name: string;
  key: string;
  kind: ZoneKind;
  kind_reason: string;
  code: string | null;
  color: string | null;
  match: string[];
  position: number;
  group: string | null;
  description: string | null;
  geometry: [number, number, number, number] | null;
  per_customer?: boolean; // Vorlage je Kunde (0.82.0, `automato_customer`)
}
export interface SchemaLink {
  src: string;
  dst: string;
  intent: ZoneIntent;
  reason: string;
  note: string | null;
  services?: string[]; // erlaubte Dienste bei „eingeschränkt“ (`automato_services`, 0.57.0)
}
export interface ZoneSchemaData {
  zones: SchemaZone[];
  links: SchemaLink[];
  warnings: string[];
  page: string | null;
  pages: string[];
}
export interface ZoneSchemaState {
  profile_id: number;
  schema: ZoneSchemaData | null;
  drawing: {
    filename: string | null;
    uploaded_by: string | null;
    uploaded_at: string | null;
    edited_by?: string | null; // in der Oberfläche bearbeitet (0.70.0)
    edited_at?: string | null;
  } | null;
}
export interface ZoneSchemaDiff {
  zones: { added: string[]; removed: string[]; changed: { name: string; fields: string[] }[] };
  links: { added: SchemaLink[]; removed: SchemaLink[]; changed: (SchemaLink & { before: ZoneIntent })[] };
}
export const fetchZoneSchema = (profileId: number) => getJSON<ZoneSchemaState>(`/api/naming-profiles/${profileId}/zone-schema`);
export const previewZoneSchema = (profileId: number, content: string, filename: string | null) =>
  sendJSON<{ schema: ZoneSchemaData; diff: ZoneSchemaDiff }>(`/api/naming-profiles/${profileId}/zone-schema/preview`, "POST", {
    content,
    filename,
  });
export const saveZoneSchema = (profileId: number, content: string, filename: string | null) =>
  sendJSON<ZoneSchemaState>(`/api/naming-profiles/${profileId}/zone-schema`, "PUT", { content, filename });
export const deleteZoneSchema = (profileId: number) => sendJSON<void>(`/api/naming-profiles/${profileId}/zone-schema`, "DELETE");
// Grundschema in der Oberfläche bearbeiten (0.70.0, Admin): Zonen und Übergänge ersetzen
export interface SchemaZoneEdit {
  name: string;
  kind: ZoneKind;
  code: string | null;
  color: string | null;
  match: string[];
  group: string | null;
  description: string | null;
  geometry: [number, number, number, number] | null;
  previous: string | null; // bisheriger Name (Umbenennen)
  per_customer?: boolean; // Vorlage je Kunde (0.82.0)
}
export interface SchemaLinkEdit {
  src: string;
  dst: string;
  intent: ZoneIntent;
  services: string[];
  note: string | null;
}
export const editZoneSchema = (profileId: number, body: { zones: SchemaZoneEdit[]; links: SchemaLinkEdit[] }) =>
  sendJSON<ZoneSchemaState>(`/api/naming-profiles/${profileId}/zone-schema`, "PATCH", body);
// Datei vom Server in der Sprache der Exporte (0.87.0: `?lang=` geht vor Accept-Language)
function withLang(url: string): string {
  return `${url}${url.includes("?") ? "&" : "?"}lang=${exportLang()}`;
}
async function fileOf(url: string, fallback: string): Promise<{ blob: Blob; filename: string }> {
  const response = await fetchChecked(withLang(url));
  const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "");
  return { blob: await response.blob(), filename: match?.[1] ?? fallback };
}
// Export für Confluence (0.71.0): Applikations-Doku als Word bzw. Word + Netzwerkplan (draw.io) im ZIP
// 0.73.0: `sections` = gewählte Abschnitte (leer = alle)
export const exportApplication = (id: number, format: "docx" | "zip", sections?: string[]) =>
  fileOf(
    `/api/applications/${id}/export${qs({ format, sections: sections?.length ? sections.join(",") : undefined })}`,
    `applikation.${format}`,
  );
export const zoneSchemaDrawing = (profileId: number) => fileOf(`/api/naming-profiles/${profileId}/zone-schema/drawing`, "zonenschema.drawio");
// Vorlage: ohne Mandant Beispiel-Zonen, mit Mandant dessen Zonen und Absichten als Ausgangspunkt
export const zoneSchemaTemplate = (tenantId?: number) =>
  fileOf(`/api/naming-profiles/zone-schema/template${qs({ tenant_id: tenantId })}`, "zonenschema-vorlage.drawio");
export const upsertEntry = (
  profileId: number,
  body: { kind: string; key: string; value?: string | null },
) => sendJSON<DictionaryEntry>(`/api/naming-profiles/${profileId}/dictionary`, "POST", body);
export const deleteEntry = (entryId: number) =>
  sendJSON<void>(`/api/naming-profiles/dictionary/${entryId}`, "DELETE");

// Dokumentation
export async function fetchRules(opts: {
  q?: string;
  tenantId?: number | null;
  domain?: RuleDomain | "";
  systemId?: number | null;
  object?: string; // Regeln, die dieses Objekt verwenden (auch über Gruppen)
  objectId?: number | null; // genau dieses Objekt (IPv4/IPv6 gleichnamig getrennt)
  nat?: string; // Regeln mit diesem VIP bzw. SNAT-Pool
  net?: string; // Regeln eines Subnetzes (Quelle/Ziel überschneidet sich)
  netAny?: boolean; // bei net auch Regeln mit any (Standard: ja)
  family?: string; // v4 | v6 | dual
  vdom?: string; // FortiGate-VDOM
  pair?: string; // same | similar | confirmed | any | none
  parallel?: string; // differs | missing (Verbund paralleler Firewalls)
  routable?: boolean; // Suche nach einer IP: routingtechnisch unmögliche ausblenden
  srcZone?: string;
  dstZone?: string;
  action?: string;
  limit?: number;
  offset?: number;
}): Promise<RulePage> {
  const params = {
    q: opts.q?.trim(),
    tenant_id: opts.tenantId,
    domain: opts.domain,
    source_system_id: opts.systemId,
    object: opts.objectId ? undefined : opts.object,
    object_id: opts.objectId,
    nat: opts.nat,
    net: opts.net,
    net_any: opts.net && opts.netAny === false ? "false" : undefined,
    family: opts.family,
    vdom: opts.vdom,
    pair: opts.pair,
    parallel: opts.parallel,
    routable: opts.routable ? "true" : undefined,
    src_zone: opts.srcZone,
    dst_zone: opts.dstZone,
    action: opts.action,
    limit: opts.limit,
    offset: opts.offset,
  };
  const response = await fetchChecked(`/api/rules${cq(params)}`);
  const rules = (await response.json()) as Rule[];
  const total = Number(response.headers.get("X-Total-Count") ?? rules.length);
  const impossible = response.headers.get("X-Impossible-Count");
  return {
    rules,
    total: Number.isFinite(total) ? total : rules.length,
    impossible: impossible === null ? null : Number(impossible),
  };
}
// IPv4/IPv6-Paar bestätigen bzw. ablehnen (operator) und die Entscheidung zurücknehmen
export const decidePair = (ruleId: number, partnerId: number, decision: "confirmed" | "rejected") =>
  sendJSON<void>(`/api/rules/${ruleId}/pair`, "POST", { partner_id: partnerId, decision });
export const resetPairDecision = (id: number) => sendJSON<void>(`/api/rules/pair-decisions/${id}`, "DELETE");
export const fetchRuleSummary = (tenantId?: number | null) =>
  getJSON<RuleSummary>(`/api/rules/summary${cq({ tenant_id: tenantId })}`);
export const fetchRuleDetail = (id: number) => getJSON<RuleDetail>(`/api/rules/${id}`);
// Seitenweise Listen: Gesamtzahl (nach Filtern) im Header X-Total-Count
export interface Page<T> {
  rows: T[];
  total: number;
}
async function getPage<T>(url: string): Promise<Page<T>> {
  const response = await fetchChecked(url);
  const rows = (await response.json()) as T[];
  const total = Number(response.headers.get("X-Total-Count") ?? rows.length);
  return { rows, total: Number.isFinite(total) ? total : rows.length };
}
export const fetchNat = (opts: {
  tenantId?: number | null;
  natType?: string;
  q?: string;
  issuesOnly?: boolean;
  limit?: number;
  offset?: number;
}) =>
  getPage<Nat>(
    `/api/nat${cq({ tenant_id: opts.tenantId, nat_type: opts.natType, q: opts.q?.trim(), issues_only: opts.issuesOnly ? "true" : undefined, limit: opts.limit, offset: opts.offset })}`,
  );
export const fetchNatDetail = (id: number) => getJSON<NatDetail>(`/api/nat/${id}`);
export const fetchFindings = (opts: {
  tenantId?: number | null;
  status?: string;
  type?: string;
  q?: string;
  limit?: number;
  offset?: number;
}) =>
  getPage<Finding>(
    `/api/findings${cq({
      tenant_id: opts.tenantId,
      status: opts.status,
      type: opts.type,
      q: opts.q?.trim(),
      limit: opts.limit,
      offset: opts.offset,
    })}`,
  );
export interface FindingSummary {
  total: number;
  open: number;
  by_status: Record<string, number>; // unter Typ-/Suchfilter
  by_type: Record<string, { open: number; total: number }>;
}
export const fetchFindingSummary = (tenantId?: number | null, type?: string, q?: string) =>
  getJSON<FindingSummary>(`/api/findings/summary${cq({ tenant_id: tenantId, type, q: q?.trim() })}`);
export const patchFinding = (id: number, body: { status?: string; note?: string }) =>
  sendJSON<Finding>(`/api/findings/${id}`, "PATCH", body);
export const fetchPublishedServices = (tenantId?: number | null) =>
  getJSON<PublishedService[]>(`/api/published-services${cq({ tenant_id: tenantId })}`);
export const fetchPublishedService = (name: string, tenantId?: number | null) =>
  getJSON<PublishedServiceDetail>(
    `/api/published-services/${encodeURIComponent(name)}${qs({ tenant_id: tenantId })}`,
  );
export const fetchAppDocSystems = (tenantId?: number | null) =>
  getJSON<AppDocSystem[]>(`/api/appdoc/systems${cq({ tenant_id: tenantId })}`);
export const fetchSystemDoc = (name: string, tenantId?: number | null) =>
  getJSON<SystemDoc>(`/api/appdoc/systems/${encodeURIComponent(name)}${qs({ tenant_id: tenantId })}`);
export const assignSystemSite = (name: string, tenantId: number, siteId: number | null) =>
  sendJSON<SystemDoc>(
    `/api/appdoc/systems/${encodeURIComponent(name)}/site${qs({ tenant_id: tenantId })}`,
    "PATCH",
    { site_id: siteId },
  );

// Applikationen
export const fetchApps = (tenantId: number) =>
  getJSON<Application[]>(`/api/applications${cq({ tenant_id: tenantId })}`);
export const fetchApp = (id: number) => getJSON<ApplicationDetail>(`/api/applications/${id}`);
export const createApp = (
  tenantId: number,
  body: ApplicationInput & { name: string; members?: { kind: MemberKind; ref: string }[] },
) => sendJSON<ApplicationDetail>(`/api/applications${qs({ tenant_id: tenantId })}`, "POST", body);
export const patchApp = (id: number, body: ApplicationInput) =>
  sendJSON<ApplicationDetail>(`/api/applications/${id}`, "PATCH", body);
export const deleteApp = (id: number) => sendJSON<void>(`/api/applications/${id}`, "DELETE");
// 0.75.0: mehrere löschen (z. B. ohne VMs), Stammdaten aus CSV mit Vorschau
export const deleteApps = (tenantId: number, ids: number[]) =>
  sendJSON<{ deleted: number }>(`/api/applications/delete${qs({ tenant_id: tenantId })}`, "POST", { ids });
export interface AppImportRow {
  line: number;
  name: string | null;
  action: "create" | "update" | "unchanged" | "error";
  errors: string[];
  changes: Record<string, [unknown, unknown]>;
  application_id: number | null;
}
export interface AppImportPlan {
  columns: Record<string, string>; // Kopfzeile -> Feld
  ignored: string[];
  counts: Record<AppImportRow["action"], number>;
  rows: AppImportRow[];
}
export const importApps = (tenantId: number, csv: string, apply: boolean) =>
  sendJSON<AppImportPlan>(`/api/applications/import${qs({ tenant_id: tenantId, apply: apply ? "true" : undefined })}`, "POST", { csv });
export const fetchAppImportTemplate = async (): Promise<Blob> => (await fetchChecked("/api/applications/import/template")).blob();
export const addAppMembers = (id: number, members: { kind: MemberKind; ref: string }[]) =>
  sendJSON<ApplicationDetail>(`/api/applications/${id}/members`, "POST", { members });
export const removeAppMember = (id: number, memberId: number) =>
  sendJSON<ApplicationDetail>(`/api/applications/${id}/members/${memberId}`, "DELETE");
// Regel direkt zuordnen bzw. ausschliessen (0.60.0, operator)
// Regel(n) einer Zeile in einer Kategorie (bzw. allen) ausblenden – mit Begründung (0.69.0)
export const hideAppRules = (id: number, ruleIds: number[], category: number | null, reason: string) =>
  sendJSON<DocHide[]>(`/api/applications/${id}/hidden`, "POST", { rule_ids: ruleIds, category, reason });
export const showAppRule = (id: number, hideId: number) => sendJSON<void>(`/api/applications/${id}/hidden/${hideId}`, "DELETE");
export const assignAppRule = (id: number, ruleId: number, exclude = false) =>
  sendJSON<ApplicationDetail>(`/api/applications/${id}/rules`, "POST", { rule_id: ruleId, exclude });
export const fetchAppCandidates = (
  tenantId: number,
  opts: { kind?: MemberKind | ""; q?: string; limit?: number } = {},
) =>
  getPage<AppCandidate>(
    `/api/applications/candidates${qs({ tenant_id: tenantId, kind: opts.kind, q: opts.q, limit: opts.limit })}`,
  );
export const fetchAppSuggestions = (tenantId: number) =>
  getJSON<AppSuggestion[]>(`/api/applications/suggestions${qs({ tenant_id: tenantId })}`);
export const acceptAppSuggestion = (tenantId: number, code: string, name?: string) =>
  sendJSON<ApplicationDetail>(
    `/api/applications/suggestions/${encodeURIComponent(code)}/accept${qs({ tenant_id: tenantId })}`,
    "POST",
    name ? { name } : undefined,
  );
export const fetchAppDoc = (id: number) => getJSON<ApplicationDoc>(`/api/applications/${id}/doc`);
// Abhängigkeiten zu anderen Applikationen (0.88.0 wieder in der Doku): erst beim Aufklappen
export const fetchAppDependencies = (id: number) => getJSON<AppDependencies>(`/api/applications/${id}/dependencies`);

// Verbindungen Ende zu Ende (0.45.0): je Verbindung (Quelle → Ziel, Dienst) der Weg über
// Firewalls und ACI mit der entscheidenden Policy bzw. dem Contract je Hop und dem Abgleich mit
// den Regeln der Doku – nicht für Kunden-Benutzer (zeigt die Infrastruktur)
export interface AppConnSide {
  ip: string;
  label: string;
  kind: "system" | "object" | "epg" | "external";
  name: string | null; // IPAM-Name der Gegenstelle
  systems: string[];
  more: number;
  keys: string[]; // Systeme der Applikation (Doku-Schlüssel)
  l3out?: string | null; // externe EPG: ihr L3Out (0.74.0)
}
export interface AppConnRule {
  rule_id: number | null;
  external_id?: string | null;
  name?: string | null;
  label?: string | null; // ACI: „Contract CT_x“, „Taboo …“
  kind?: string | null;
  action: "allow" | "deny";
  src?: string | null; // Objekt der Policy bzw. EPG, über die die Quelle passt
  dst?: string | null;
  services: string[];
  unsure: string[];
  decides: boolean;
  reverse?: boolean; // ACI (0.69.1): Contract in der Rückrichtung (zustandslos)
  note?: string; // ACI (0.72.1): greift nicht, z. B. nur Antworten (Filter nur mit Quellport)
}
export interface AppConnCheck {
  label: string; // Firewall bzw. VRF (im Verbund je Firewall)
  verdict: string;
  text: string;
  src_epg?: string | null;
  dst_epg?: string | null;
  rules: AppConnRule[];
  ingress?: string | null;
  egress?: string | null;
  nat?: TraceHop["nat"];
  snat?: TraceHop["snat"];
  leak?: TraceHop["leak"];
}
export interface AppConnHop {
  label: string;
  kind: string;
  key: string;
  source_system_id: number;
  context: string;
  verdict: string | null;
  checks: AppConnCheck[];
  lb?: TraceHop["lb"]; // NSX ALB: vom Load Balancer weiter zum Server (0.77.0)
}
export interface AppConnState {
  rule_id: number;
  label: string;
  action: "allow" | "deny";
  device: string;
  state: "decides" | "possible" | "other" | "off_path" | "unknown";
  text: string;
}
export interface AppConnection {
  key: string;
  direction: "in" | "out";
  src: AppConnSide;
  dst: AppConnSide;
  service: string | null;
  verdict: string;
  text: string;
  reached: boolean;
  end: { kind: string; text: string } | null;
  start: string | null; // Weg ab dem Gerät der Regel (Quelle an keinem gelesenen Gerät)
  paths: number;
  parallel: string[] | null;
  hops: AppConnHop[];
  rules: AppConnState[];
  mismatch: string[];
  // Wege uneinheitlich (0.73.0): Urteil je Weg (aktiv, ECMP, Backup) bzw. Firewall im Verbund
  variants: { kind: "active" | "backup"; via: string; verdict: string; text: string; reason: string | null }[];
  inconsistent: boolean;
  notes: string[];
  // logische Zonen (0.78.0, ADR 0024): Quelle/Ziel, Kette des Wegs, Absichten mit Urteil
  zones?: AppConnZones | null;
  // Netz als Ziel (0.79.1): genauer geroutete Teile (eigene Verbindungen) bzw. Teil von
  parts?: string[];
  part_of?: string;
}
export interface AppConnZones {
  src: string[];
  dst: string[];
  chain: { name: string; code: string | null; color: string | null; transition?: string | null; role: string }[];
  intents: {
    src_name: string;
    dst_name: string;
    intent: "allow" | "limited" | "deny";
    services: string[];
    verdict: "ok" | "violation" | "warn" | "info";
    text: string;
  }[];
  violation: boolean;
}
// Netzwerkplan je Applikation (0.67.0): Systeme → Knoten der Wege → Gegenstellen; je Linie die
// Dienste je Richtung (von der Applikation aus) und an den Enden die Interfaces
export type AppPlanVerdict = "allow" | "deny" | "depends" | "unknown" | "blocked";
export interface AppPlanNode {
  id: string;
  role: "own" | "hop" | "peer";
  kind: string; // system | firewall | vdom | vrf | l3out | lb | object | epg | external
  label: string;
  title: string;
  sub: string;
  name?: string | null;
  ips: string[];
  systems: string[];
  conns: string[]; // Schlüssel der Verbindungen über den Knoten
  verdict: AppPlanVerdict | null; // nur Hops: Urteil der Verbindungen, die ihn erreichen
  x: number;
  y: number;
  w: number;
  h: number;
  // kompakt (0.74.0): EPG mit ihren VMs im Rahmen des APs, ACI als L3Out, Regeln je Knoten
  group?: string | null;
  lines?: string[];
  more?: number;
  via?: string; // L3Out: ACI-VRF des Wegs; Load Balancer: Knoten, an dem seine VIP liegt
  vips?: string[]; // Load Balancer (0.85.0): VIPs mit Port; Zeilen = Virtual Services
  rules?: { name: string; on: string | null; action: string; services: string[]; rule_id: number | null }[];
  rule_lines?: string[];
  rules_box?: { x: number; y: number; w: number; h: number } | null;
}
export interface AppPlanPort {
  x: number;
  y: number;
  anchor: "start" | "end" | "middle";
  text: string;
}
export interface AppPlanEdge {
  id: string;
  a: string;
  b: string;
  fwd: string[]; // Dienste von der Applikation weg
  back: string[]; // Dienste zur Applikation hin
  blocked: string[]; // vorher gesperrt – erreichen diese Linie nicht
  verdict: AppPlanVerdict;
  conns: string[];
  ports_a: string[];
  ports_b: string[];
  port_a: AppPlanPort | null;
  port_b: AppPlanPort | null;
  dashed: boolean; // Weg hier nicht bekannt
  mismatch: boolean;
  label: string;
  points: [number, number][];
  via: [number, number][]; // Spuren in den Spalten dazwischen (Linie an den Knoten vorbei)
  lx: number;
  ly: number;
  anchor?: "start" | "middle";
}
export interface AppPlan {
  width: number;
  height: number;
  nodes: AppPlanNode[];
  edges: AppPlanEdge[];
  columns: { x: number; title: string }[];
  groups?: { title: string; x: number; y: number; w: number; h: number; nodes: string[] }[]; // Rahmen je AP (0.74.0)
  summary: { connections: number; total: number; nodes: number; edges: number };
}
export interface AppConnections {
  connections: AppConnection[];
  notes: string[];
  summary: { total: number; allow: number; blocked: number; open: number; mismatch: number; inconsistent?: number; zone_violations?: number };
  plan?: AppPlan;
}
export const fetchAppConnections = (id: number) =>
  getJSON<AppConnections>(`/api/applications/${id}/connections`);
// 0.74.0: `rules` = Ebene „Regeln“ mit exportieren
export async function exportAppPlan(id: number, format: "svg" | "drawio", keys?: string[], rules = true): Promise<{ blob: Blob; filename: string }> {
  const params = new URLSearchParams({ format });
  (keys ?? []).forEach((k) => params.append("key", k));
  if (!rules) params.set("rules", "false");
  const response = await fetchChecked(withLang(`/api/applications/${id}/connections/plan/export?${params.toString()}`));
  const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "");
  return { blob: await response.blob(), filename: match?.[1] ?? `netzwerkplan.${format}` };
}

// System (Admin)
// DB-Rolle des Orchestrators (0.48.0): getrennt vom Eigentümer der Tabellen, ohne DDL?
export interface DbRole {
  role: string;
  superuser: boolean;
  ddl: boolean;
  owns_tables: number;
  separated: boolean;
}
export interface SystemStatus {
  version: string;
  database: { revision: string | null; head: string | null; up_to_date: boolean; role: DbRole | null };
  agent_package: { version: string | null; signature: string; pubkey_set: boolean; agents: number; agents_current: number };
  scheduler: { enabled: boolean; interval_seconds: number; timezone: string; scheduled_sources: number; next_run_at: string | null };
  security: {
    environment: string;
    auth_mode: string;
    internal_mtls_mode: string;
    audit_encryption: boolean;
    credentials_key: string;
    // TLS intern (0.49.0): Zertifikat des Orchestrators, TLS-Version der DB-Verbindung
    internal_tls: { enabled: boolean; not_after: string | null; days_left: number | null; database: string | null } | null;
  };
  backup: { at: string | null; file: string | null; encrypted: boolean | null; size: number | null } | null;
}
export const fetchSystemStatus = () => getJSON<SystemStatus>("/api/system/status");

// Aufbewahrung (0.48.0): Fristen in Tagen (0 = nie), was fällig ist, zuletzt aufgeräumt
export interface RetentionCounts {
  runs: number;
  sync_plans: number;
  audit: number;
  l2?: number; // MAC-/ARP-Einträge der Switches (0.63.0)
}
export interface RetentionStatus {
  policy: { runs_days: number; sync_days: number; audit_days: number; l2_days?: number };
  active: boolean;
  scheduled: boolean;
  due: RetentionCounts;
  last: { at: string; actor: string; counts: RetentionCounts } | null;
  deleted?: RetentionCounts;
}
export const fetchRetention = () => getJSON<RetentionStatus>("/api/system/retention");
export const purgeRetention = () => sendJSON<RetentionStatus>("/api/system/retention/purge", "POST");

// Betriebszustand (0.12.0): Ampel je Prüfung
export interface HealthCheck {
  key: string;
  area: string; // Plattform | Agenten | Quellsysteme | NetBox-Sync | Sicherung
  status: "ok" | "warn" | "error";
  title: string;
  detail: string | null;
  view: string | null; // Ansicht im UI
  since: string | null;
}
export interface OpsHealth {
  status: "ok" | "warn" | "error";
  problems: number;
  checks: HealthCheck[];
}
export const fetchOpsHealth = (tenantId?: number | null) =>
  getJSON<OpsHealth>(`/api/system/health${qs({ tenant_id: tenantId })}`);

// Verbindungen (0.40.0): vom Orchestrator-Container aus geprüft - Datenbank, Redis, Keycloak,
// zentral gelesene Quellsysteme; Agent-Systeme als Hinweis
export interface ConnectionItem {
  key: string;
  area: string; // Plattform | Anmeldung | Quellsysteme | Agenten
  name: string;
  target: string | null;
  status: "ok" | "warn" | "error" | "info";
  steps: { step: string; ok: boolean; detail: string }[];
  hint: string | null;
  ufw: { from: string; to: string; port: number; command: string } | null;
}
export interface ConnectionReport {
  status: "ok" | "warn" | "error";
  problems: number;
  checked_at: string;
  container: { network: string | null; host: string };
  items: ConnectionItem[];
}
export const checkConnections = () => getJSON<ConnectionReport>("/api/system/connections");

// Objekte & Inventar (rein lesend, Mandant nötig)
export const fetchObjects = (
  tenantId: number,
  params: {
    category?: string; // kommagetrennt
    source_system_id?: number | null;
    q?: string;
    issues_only?: boolean;
    obj_type?: string; // kommagetrennt; "-" = ohne Typ
    predefined?: "include" | "exclude" | "only";
    object_id?: number | null; // nur dieses Objekt („Objekt öffnen“)
    limit?: number;
    offset?: number;
  } = {},
) =>
  getJSON<ObjectPage>(
    `/api/objects${cq({
      tenant_id: tenantId,
      ...params,
      q: params.q?.trim(),
      issues_only: params.issues_only ? "true" : undefined,
    })}`,
  );
export const fetchObjectDetail = (id: number) => getJSON<ObjectDetail>(`/api/objects/${id}`);
export const fetchInventory = <K extends keyof Inventory>(
  kind: K,
  tenantId: number,
  params: { source_system_id?: number | null; q?: string } = {},
) =>
  getJSON<Inventory[K][]>(`/api/inventory/${kind}${cq({ tenant_id: tenantId, ...params, q: params.q?.trim() })}`);

// Abgleich
export const fetchReconcileSummary = (tenantId: number) =>
  getJSON<ReconcileSummary>(`/api/reconcile/summary${cq({ tenant_id: tenantId })}`);
export const fetchIpamCompare = (
  tenantId: number,
  params: { status?: string; q?: string; limit?: number; offset?: number } = {},
) => getJSON<IpamCompare>(`/api/reconcile/ipam${cq({ tenant_id: tenantId, ...params, q: params.q?.trim() })}`);
// Datenqualität (ADR 0029): Bereiche mit Ampel, Kennzahl, Einträgen (Ursache, Hinweis, Link) und
// Gesamtnote – Werte wie `status`/`key`/`view` kommen als Schlüssel, Texte in der Sprache der Anfrage
export type DqStatus = "ok" | "info" | "warn" | "crit";
export interface DqEntry {
  key: string;
  status: DqStatus;
  title: string;
  count: number | null;
  cause: string | null;
  hint: string | null;
  link: { view: string; params: Record<string, string> } | null;
  samples: string[];
}
export interface DqArea {
  key: string; // sources | rules | vms | ipam | assignment
  title: string;
  status: "ok" | "warn" | "crit";
  metric: string;
  applicable: boolean;
  note: string | null;
  entries: DqEntry[];
}
export interface DataQuality {
  tenant_id: number;
  customer_id: number | null;
  generated_at: string;
  stale_days: number;
  score: {
    percent: number | null;
    ok: number;
    total: number;
    status: "ok" | "warn" | "crit";
    by_status: Record<string, number>;
  };
  sample: { rules: number; of: number } | null;
  areas: DqArea[];
}
export const fetchDataQuality = (tenantId: number) =>
  getJSON<DataQuality>(`/api/data-quality${cq({ tenant_id: tenantId })}`);
export const runReconcile = (tenantId: number) =>
  sendJSON<{ tenant_id: number; findings: Record<string, number> }>(
    `/api/reconcile${qs({ tenant_id: tenantId })}`,
    "POST",
  );

// NetBox-Sync
export const fetchSyncTargets = (tenantId: number) =>
  getJSON<SyncTarget[]>(`/api/netbox-sync/targets${qs({ tenant_id: tenantId })}`);
export const fetchSyncPlans = (tenantId: number) =>
  getJSON<SyncPlan[]>(`/api/netbox-sync/plans${qs({ tenant_id: tenantId })}`);
export const fetchSyncPlan = (id: number) => getJSON<SyncPlan>(`/api/netbox-sync/plans/${id}`);
export const createSyncPlan = (body: {
  tenant_id: number;
  source_system_id: number;
  sections: string[];
  allow_delete: boolean;
}) => sendJSON<SyncPlan>("/api/netbox-sync/plans", "POST", body);
export const fetchSyncItems = (
  id: number,
  params: { action?: string; kind?: string; q?: string; limit?: number; offset?: number } = {},
) => getJSON<Paged<SyncItem>>(`/api/netbox-sync/plans/${id}/items${qs({ ...params, q: params.q?.trim() })}`);
export const applySyncPlan = (id: number) => sendJSON<SyncPlan>(`/api/netbox-sync/plans/${id}/apply`, "POST");
// Laufende Vorschau/Übernahme beenden (hängt z. B. nach einem Neustart des Orchestrators)
export const cancelSyncPlan = (id: number) => sendJSON<SyncPlan>(`/api/netbox-sync/plans/${id}/cancel`, "POST");

// Routing, BGP, HA und L3Out (ADR 0019 Stufe 2) - nur lesend
// Namen einer IP aus dem IPAM (DNS-Name bzw. Name); ohne Namen das Netz mit Beschreibung
export interface Resolved {
  names?: string[];
  network?: string;
}
export interface NextHop extends Resolved {
  ip?: string | null;
  interface?: string | null;
  zone?: string | null; // FortiGate: Zone des Interfaces (Inventar)
}
// Bezeichnung eines Kontexts: FortiGate nur mit root = die Firewall, sonst „fw · VDOM x“,
// ACI „leaf · VRF Tenant:VRF“
export type ContextKind = "firewall" | "vdom" | "vrf";
export interface Labeled {
  source_system_id: number;
  source: string | null;
  node: string; // Firewall (Hostname) bzw. Border-Leaf
  context: string; // VDOM bzw. Tenant:VRF
  display?: ContextKind | "";
  label?: string;
  shared?: boolean; // 0.76.0: Kunde gewählt, Kontext gehört zum geteilten Teil
}
export interface RoutingContext extends Labeled {
  id: number;
  kind: "vdom" | "vrf";
  missing: string[] | null; // nicht lesbar -> Aussage „unbekannt“
  counts: Partial<Record<"rib" | "bgp" | "static" | "peers" | "policy_routes", number>>;
}
// Knoten der Weg-Suche: VDOM bzw. Firewall, ACI-VRF über alle Border-Leafs
export interface RoutingNode {
  key: string; // <Quellsystem>|<Kontext>
  source_system_id: number;
  source: string;
  context: string;
  kind: ContextKind;
  label: string;
  members: string[];
  missing: string[];
  group?: string | null; // Verbund paralleler Firewalls
}
export interface RoutingSummary {
  contexts: RoutingContext[];
  nodes: RoutingNode[];
  ha: number;
  l3outs: number;
  vrfs?: number[]; // VRFs der Routen (FortiGate 7.x, 0 = Standard)
  mgmt_vrf?: boolean; // Routen in der Management-Tabelle vorhanden
}
export interface TopoRoute extends Labeled {
  id: number;
  family: number;
  prefix: string;
  prefix_len: number;
  table: "rib" | "bgp" | "static";
  protocol: string | null;
  best: boolean;
  distance: number | null;
  metric: number | null;
  next_hops: NextHop[];
  attrs: Record<string, unknown> | null;
}
export interface PolicyHit {
  kind: "policy" | "sdwan";
  seq: number | null;
  name: string | null;
  match?: Record<string, unknown>;
  gateway: string | null;
  output_device: string[];
  certain: boolean; // passt sicher (sonst nur über Objekte bestimmt: möglich)
}
export interface RouteLookup extends Labeled {
  kind: string;
  vrf?: number | string | null; // weitere VRF des Kontexts (FortiGate), "mgmt" = Management
  missing: string[];
  route: {
    id: number;
    prefix: string;
    protocol: string | null;
    distance: number | null;
    metric: number | null;
    next_hops: NextHop[];
  } | null;
  policy_routes: PolicyHit[];
}
export interface TracePort extends Resolved {
  interface?: string | null;
  zone?: string;
  next_hop?: string | null;
  exit?: { id: number; name: string; kind: string }; // Anbindung nach aussen (0.18.0)
}
export interface TraceHop extends RoutingNode {
  via: string; // wie der Weg hierher kam (Quelle angeschlossen, Adresse des Nachbarn, …)
  vrf?: number | string; // VRF des Eingangs, wenn nicht die Standard-VRF ("mgmt" = Management)
  ingress: TracePort;
  egress?: TracePort;
  route?: {
    prefix: string;
    prefix_len: number;
    protocol: string | null;
    distance: number | null;
    next_hops: NextHop[];
    members: string[];
    inner?: { prefix: string; protocol: string | null; via: string }[]; // Netz: Teile anders geroutet (0.39.0)
    inner_total?: number;
  } | null;
  policy?: PolicyHit; // Policy-Route bzw. SD-WAN-Regel, der der Weg folgt
  policy_routes?: PolicyHit[];
  backup?: { prefix: string; protocol: string | null; distance: number | null; reason: string }; // Backup-Weg weicht hier ab
  rules?: HopRules; // FortiGate-Hop: passende Policies und Urteil (0.22.0)
  dst?: string; // Ziel an diesem Hop nach NAT auf einem Hop davor (0.23.0)
  // VIP (DNAT) an diesem Hop: ab hier zur internen IP (Virtual Server: alle Real Server)
  // balancer (0.79.0): Ziel der VIP ist dieser Virtual Service des NSX ALB
  nat?: { name: string; id: number; ext: string | null; to: string[]; type: string | null; ports: (string | null)[] | null; balancer?: string };
  // NSX ALB (0.77.0, ADR 0027): VIP hier angeschlossen, der Weg geht vom Load Balancer zum Server
  lb?: {
    name: string;
    id: number;
    source?: string;
    ext: string;
    port?: string;
    to?: string[];
    server_port?: number | string;
    servers?: number;
    pool?: string;
    tenant?: string;
    snat?: string; // service_engine | client
    down?: boolean;
  };
  // SNAT der erlaubenden Policy (0.47.0: VIP mit nat-source-vip, Pool mit Typ und Bereich)
  snat?: {
    pool?: string;
    interface?: string | null;
    vip?: string;
    type?: string;
    range?: string;
    sure?: boolean;
    missing?: boolean;
    more?: string[];
    to: string | null;
  };
  // ACI (0.44.0): Ziel in einer anderen VRF, in diese geleakt (Shared Service bzw. Route-Leaking)
  // 0.87.0: kind statt des (übersetzten) Texts
  leak?: { vrf: string; where: string; text: string; kind?: "leak" | "shared" };
  // Verbund als ein Hop (0.29.0): je Firewall bzw. Variante (andere Interfaces) ein Eintrag
  parts?: TraceHop[];
  zone?: TraceZone | null; // logische Zone des Knotens (0.57.0)
  l2?: L2Segment; // L2 bis zum nächsten Hop bzw. zum Anschluss des Ziels (0.64.0)
}
// L2-Abschnitt (0.64.0): Geräte vom Ausgang bis zum nächsten Gerät, je Gerät Ein- und Ausgang
export interface L2Segment {
  steps: { device: string; kind: string; read: boolean; in: string | null; out: string | null }[];
  vlan: number | null;
  text: string;
  switches: number;
}
export interface L2Location {
  mac: string;
  device: string;
  port: string;
  vlan: number | null;
  via: { device: string; port: string | null; kind: string; read: boolean } | null;
  vm: { name: string; host: string | null } | null;
}
// Zonen in der Weg-Suche (0.57.0): Zone je Hop und die Kette Quelle → Hops → Ziel mit Übergängen
export interface TraceZone {
  id: number | null;
  name: string;
  code: string | null;
  kind: string;
  color: string | null;
}
export interface TraceZoneStep extends TraceZone {
  role: "src" | "hop" | "dst";
  hops: string[]; // Hops in dieser Zone
  transition?: string; // Übergang hierher („DC-EINET“)
}
export interface TraceZoneIntent {
  src: number;
  dst: number;
  src_name: string;
  dst_name: string;
  intent: ZoneIntent;
  note: string | null;
  services: string[];
  verdict: "ok" | "violation" | "warn" | "info";
  text: string;
}
// Policy auf dem Weg: sicher (entscheidet) bzw. möglich (`unsure` = nicht prüfbare Bedingungen)
export interface HopRule {
  rule_id: number;
  external_id: string;
  name: string;
  action: "allow" | "deny";
  src: string | null; // Objekt/Wert, über das die Quelle passt (any, negiert)
  dst: string | null;
  srcintf: string[];
  dstintf: string[];
  services: string[];
  unsure: string[]; // Eingang, Ausgang, Quelle, Ziel, Dienst, Benutzer/Gruppen, Zeitplan …
  decides: boolean;
  label?: string; // ACI (0.35.0): „Contract CT_x“, „Taboo TB_x“, „Preferred Group VRF“
  kind?: "contract" | "taboo" | "preferred_group";
  reverse?: boolean; // ACI (0.69.1): passt in der Rückrichtung (Provider → Consumer, zustandslos)
  note?: string; // ACI (0.72.1): greift nicht, z. B. nur Antworten (Filter nur mit Quellport)
  after?: boolean; // 0.78.4: passt, liegt aber hinter der entscheidenden Policy
}
export type PolicyVerdict = "allow" | "deny" | "implicit_deny" | "depends" | "unknown";
export interface HopRules {
  verdict: PolicyVerdict;
  text: string;
  rules: HopRule[];
  after?: HopRule[]; // Weg-Suche (0.78.4): passende Policies dahinter, nicht erreicht
  src_epg?: string; // ACI-VRF-Hop (0.35.0): Zuordnung der Quelle bzw. des Ziels
  dst_epg?: string;
}
export interface TracePath {
  kind?: "active" | "backup"; // aktiv (inkl. ECMP) bzw. Backup-Weg (eine Abweichung)
  deviation?: { hop: number; label: string; reason: string }; // Backup: wo und warum
  parallel?: string[]; // redundant über diese Verbünde paralleler Firewalls
  extra?: number | null; // Hops mehr als der kürzeste Weg mit Ziel (0 = gleich lang; null = ohne Ziel)
  policy?: { verdict: "allow" | "deny" | "depends" | "unknown"; text: string } | null; // Regeln auf dem Weg
  hops: TraceHop[];
  zones?: TraceZoneStep[]; // Zonen auf dem Weg (0.57.0)
  l2_src?: L2Segment; // vom Anschluss der Quelle zum ersten Hop (0.64.0)
  end: {
    kind: "connected" | "fabric" | "exit" | "external" | "unknown" | "no_route" | "loop" | "max_hops";
    text: string;
    also?: string[]; // Ende über die anderen Firewalls eines Verbunds (0.29.0)
  };
}
export interface TraceResult {
  start: (RoutingNode & { via: string })[];
  paths: TracePath[];
  notes: string[];
  slack?: number; // Backup-Wege mit höchstens so vielen Hops mehr als der kürzeste aktive
  service?: string | null; // geprüfter Dienst (z. B. tcp/443)
  service_name?: string; // angegebenes Service-Objekt bzw. ACI-Filter (FT_https)
  // Zonen (0.57.0): Quell- und Zielzone, Absicht zwischen ihnen mit Urteil; null = keine Zonen
  zones?: { src: TraceZone[]; dst: TraceZone[]; intents: TraceZoneIntent[] } | null;
  l2?: { src?: L2Location[]; dst?: L2Location[] }; // Anschluss von Quelle/Ziel (0.64.0)
}

// Name bzw. FQDN -> IP für die Weg-Suche (0.35.0): IPAM, Objekte der Firewalls, VMs; seit
// 0.38.0 auch Netze (IPAM, Netz-Objekte, Subnetze des Inventars), seitenweise
export interface NameHit {
  ip: string; // Eingabe für die Weg-Suche: IP bzw. bei Netzen das Netz (0.39.0)
  name: string | null;
  kind: "ip" | "ipam" | "object" | "vm" | "net" | "subnet";
  type: "host" | "net";
  net: string | null; // Netz (CIDR) bei type "net"
  source: string | null;
  detail: string | null;
  also?: string[]; // weitere Namen derselben IP bzw. desselben Netzes
}
export interface NameResult {
  hits: NameHit[];
  total: number; // mit dem Art-Filter
  counts: { host: number; net: number };
  capped: boolean; // je Quelle mehr Treffer, als gelesen: genauer suchen
}
export const resolveName = (
  tenantId: number,
  q: string,
  opts: { type?: "host" | "net" | ""; limit?: number; offset?: number } = {},
) =>
  getJSON<NameResult>(
    `/api/routing/resolve${cq({ tenant_id: tenantId, q: q.trim(), type: opts.type || undefined, limit: opts.limit, offset: opts.offset })}`,
  );
export interface TopoPeer extends Labeled {
  id: number;
  peer_ip: string;
  local_ip: string | null;
  remote_as: number | null;
  state: string | null;
  peer?: Resolved;
  peer_node?: string | null; // gelesener Knoten hinter der Adresse
}
export interface TopoHa {
  id: number;
  source: string | null;
  node: string;
  mode: string | null;
  group_name: string | null;
  group_id: number | null;
  members: { hostname?: string; serial?: string; role?: string; priority?: number }[] | null;
  session_pickup: boolean | null;
  sync_peers: (Record<string, unknown> & Resolved & { node?: string | null })[] | null;
}
export interface TopoPolicyRoute extends Labeled {
  id: number;
  kind: "policy" | "sdwan";
  seq: number | null;
  name: string | null;
  enabled: boolean;
  match: Record<string, unknown> | null;
  action: string | null;
  gateway: string | null;
  output_device: string[] | null;
  gateway_resolved?: Resolved;
}
export interface TopoL3Out {
  id: number;
  source: string | null;
  aci_tenant: string;
  name: string;
  dn: string;
  vrf: string | null;
  nodes:
    | {
        pod: string;
        node: string;
        name?: string | null; // Leaf-Name aus dem Inventar
        router_id?: string;
        static_routes?: { prefix: string; next_hops?: string[]; resolved?: Record<string, Resolved> }[];
      }[]
    | null;
  paths:
    | {
        path: string;
        nodes?: string[];
        node_names?: string[];
        interface?: string;
        kind?: string;
        vlan?: number;
        ips?: { ip: string; side?: string }[];
      }[]
    | null;
  peers: ({ ip: string; asn?: number | null; path?: string; node?: string | null } & Resolved)[] | null;
  ext_epgs: string[] | null;
}

export const fetchRoutingSummary = (tenantId: number) =>
  getJSON<RoutingSummary>(`/api/routing/summary${cq({ tenant_id: tenantId })}`);
export const fetchRoutes = (opts: {
  tenantId: number;
  sourceId?: number | null;
  context?: string;
  table?: string;
  vrf?: string; // "0".."n" bzw. "mgmt"
  ip?: string;
  q?: string;
  limit?: number;
  offset?: number;
}) =>
  getPage<TopoRoute>(
    `/api/routing/routes${cq({ tenant_id: opts.tenantId, source_system_id: opts.sourceId, context: opts.context || undefined, table: opts.table || undefined, vrf: opts.vrf || undefined, ip: opts.ip?.trim() || undefined, q: opts.q?.trim() || undefined, limit: opts.limit, offset: opts.offset })}`,
  );
export const lookupRoute = (tenantId: number, ip: string) =>
  getJSON<RouteLookup[]>(`/api/routing/lookup${cq({ tenant_id: tenantId, ip: ip.trim() })}`);
// Weg Hop für Hop: ab der Quelle (dort angeschlossen) bzw. dem Startpunkt (RoutingNode.key)
export const traceRoute = (
  tenantId: number,
  opts: { dst: string; src?: string; start?: string; slack?: number; service?: string },
) =>
  getJSON<TraceResult>(
    `/api/routing/trace${qs({ tenant_id: tenantId, dst: opts.dst.trim(), src: opts.src?.trim() || undefined, start: opts.start || undefined, slack: opts.slack, service: opts.service?.trim() || undefined })}`,
  );
export const fetchPeers = (tenantId: number) => getJSON<TopoPeer[]>(`/api/routing/peers${cq({ tenant_id: tenantId })}`);
export const fetchHa = (tenantId: number) => getJSON<TopoHa[]>(`/api/routing/ha${cq({ tenant_id: tenantId })}`);
export const fetchPolicyRoutes = (tenantId: number) =>
  getJSON<TopoPolicyRoute[]>(`/api/routing/policy-routes${cq({ tenant_id: tenantId })}`);
export const fetchL3Outs = (tenantId: number) => getJSON<TopoL3Out[]>(`/api/routing/l3outs${cq({ tenant_id: tenantId })}`);

// Anbindungen nach aussen (0.18.0, von Hand): Internet, MPLS, WAN, Partner – der Weg endet dort
// bzw. geht nach den manuellen Routen bei einem gelesenen Knoten weiter
export type ExitKind = "internet" | "mpls" | "wan" | "partner" | "other";
export interface ExitRef {
  id: number;
  name: string;
  kind: ExitKind;
}
// Erkannter Router (0.20.0): Next-Hop bzw. BGP-Nachbar ohne gelesenes Gerät
export interface ExitRouter extends Resolved {
  ip: string;
  status: "active" | "backup" | "neighbor"; // aktiv in der RIB, nur BGP-Pfad/statische Route, nur Nachbar
  remote_as: number | null;
  state: string | null; // BGP-Zustand
  default: boolean; // Default-Route über diesen Router
  prefixes: string[]; // die ersten Präfixe
  prefix_count: number;
  exit: ExitRef | null; // Anbindung, die ihn (an einer Firewall) abdeckt
  covered: boolean; // an allen Firewalls abgedeckt
  dismissed?: boolean; // im Vorschlag: ausgeblendet (0.21.0)
  listed?: boolean; // im Zustand einer Anbindung: dort eingetragen
  uses: {
    key: string;
    source_system_id: number;
    source: string;
    label: string;
    interface: string | null;
    zone: string | null;
    status: ExitRouter["status"];
    active: number;
    backup: number;
    remote_as: number | null;
    state: string | null;
    exit: ExitRef | null;
  }[];
}
// Redundanz: Router je Zustand, ECMP (meiste aktive Router für dasselbe Präfix), Firewalls
export interface Redundancy {
  routers: number;
  active: number;
  backup: number;
  neighbor: number;
  ecmp: number;
  firewalls: number;
}
export interface ExitSuggestion {
  key: string;
  name: string;
  kind: ExitKind;
  source_system_ids: number[];
  sources: string[];
  interfaces: string[]; // Zonen bzw. Interfaces
  next_hops: ExitRouter[];
  exits: ExitRef[]; // Anbindungen, die einen Teil schon abdecken
  covered: boolean;
  dismissed: boolean; // ausgeblendet: alle offenen Router nicht mehr vorgeschlagen (0.21.0)
  redundancy: Redundancy;
}
export interface TopoExit {
  id: number;
  tenant_id: number;
  name: string;
  kind: ExitKind;
  source_system_ids: number[]; // nur für diese Firewalls (leer = jede), z. B. beide Session-Sync-Partner
  sources: string[];
  interfaces: string[]; // Interfaces bzw. Zonen
  next_hops: string[]; // IPs der Gegenstelle
  prefixes: string[]; // Netze dahinter
  routes: { prefix: string; target: string }[]; // manuell: Präfix -> Knoten (RoutingNode.key)
  description: string | null;
  updated_at: string | null;
  // erkannte Router, die die Anbindung abdeckt (0.20.0)
  status?: {
    routers: ExitRouter[];
    missing: string[]; // erkannt, aber nicht eingetragen
    unseen: string[]; // eingetragen, aber nicht erkannt
    source_system_ids: number[];
    redundancy: Redundancy;
  };
}
export type TopoExitIn = Omit<TopoExit, "id" | "sources" | "updated_at" | "status">;
export const fetchExits = (tenantId: number) => getJSON<TopoExit[]>(`/api/routing/exits${cq({ tenant_id: tenantId })}`);
// erkannte Anbindungen; `covered` = auch die schon ganz erfassten, `dismissed` = auch ausgeblendete
export const fetchExitSuggestions = (tenantId: number, opts: { covered?: boolean; dismissed?: boolean } = {}) =>
  getJSON<ExitSuggestion[]>(
    `/api/routing/exits/suggestions${cq({ tenant_id: tenantId, covered: opts.covered ? "true" : undefined, dismissed: opts.dismissed ? "true" : undefined })}`,
  );
// erkannte Router nicht mehr vorschlagen bzw. wieder vorschlagen (Operator, 0.21.0)
export const dismissExits = (tenantId: number, ips: string[], undo = false) =>
  sendJSON<{ ips: string[] }>(`/api/routing/exits/dismiss${undo ? "/undo" : ""}`, "POST", { tenant_id: tenantId, ips });
export const createExit = (body: TopoExitIn) => sendJSON<TopoExit>("/api/routing/exits", "POST", body);
export const updateExit = (id: number, body: TopoExitIn) => sendJSON<TopoExit>(`/api/routing/exits/${id}`, "PUT", body);
export const deleteExit = (id: number) => sendJSON<void>(`/api/routing/exits/${id}`, "DELETE");

// Kunden als Ausschnitt der Umgebung (ADR 0018, 0.30.0): Selektoren statt Listen,
// Zugehörigkeit abgeleitet (nach jedem Lauf und beim Speichern)
export type SelectorKind =
  | "aci_tenant"
  | "aci_vrf"
  | "aci_ap"
  | "aci_epg"
  | "aci_bd"
  | "fw_vdom"
  | "fw_zone"
  | "fw_interface"
  | "prefix"
  | "code"
  | "ipam_tenant" // NetBox-Tenant (0.31.0)
  | "ipam_vrf" // NetBox-VRF bzw. DDI-Netzansicht
  | "ipam_tag"; // Tag bzw. Extensible Attribute key=value
export interface CustomerSelector {
  id?: number;
  kind: SelectorKind;
  value: string;
  source_system_id: number | null; // FortiGate-Selektoren: nur dieses Quellsystem
  exclude: boolean;
}
export type CustomerMemberKind = "epg" | "net" | "object" | "rule" | "vm" | "nat" | "finding";
export type CustomerCounts = Record<CustomerMemberKind, number>;
export interface Customer {
  id: number;
  tenant_id: number;
  name: string;
  code: string | null;
  description: string | null;
  shared: boolean; // geteilter Teil der Umgebung (gilt für alle Kunden)
  derived_at: string | null;
  selectors: CustomerSelector[];
  counts: CustomerCounts;
  zones: string[]; // logische Zonen des Kunden (0.53.0)
}
export interface CustomerIn {
  tenant_id: number;
  name: string;
  code: string | null;
  description: string | null;
  shared: boolean;
  selectors: CustomerSelector[];
}
export interface CustomerPreview {
  counts: CustomerCounts;
  samples: Record<CustomerMemberKind, string[]>;
  // Regeln über Netze des Kunden, aber nicht auf einem möglichen Weg (0.58.0)
  off_path?: { count: number; samples: { id: number; name: string; why: string }[] };
}
export interface CustomerSuggestion {
  name: string;
  code: string | null;
  shared: boolean;
  epgs: number;
  selectors: CustomerSelector[];
}
export type Unassigned = Record<"epg" | "rule" | "vm", { count: number; samples: string[] }>;
export const fetchCustomers = (tenantId: number) => getJSON<Customer[]>(`/api/customers${qs({ tenant_id: tenantId })}`);
export const createCustomer = (body: CustomerIn) => sendJSON<Customer>("/api/customers", "POST", body);
export const updateCustomer = (id: number, body: CustomerIn) => sendJSON<Customer>(`/api/customers/${id}`, "PUT", body);
export const deleteCustomer = (id: number) => sendJSON<void>(`/api/customers/${id}`, "DELETE");
export const previewCustomer = (tenantId: number, selectors: CustomerSelector[], customerId?: number) =>
  sendJSON<CustomerPreview>("/api/customers/preview", "POST", { tenant_id: tenantId, selectors, customer_id: customerId ?? null });
export const fetchCustomerSuggestions = (tenantId: number) =>
  getJSON<CustomerSuggestion[]>(`/api/customers/suggestions${qs({ tenant_id: tenantId })}`);
export const fetchUnassigned = (tenantId: number) => getJSON<Unassigned>(`/api/customers/unassigned${qs({ tenant_id: tenantId })}`);
export const fetchCustomerMembers = (id: number, kind: CustomerMemberKind) =>
  getJSON<{ id: number | null; name: string; via: string | null }[]>(`/api/customers/${id}/members${qs({ kind })}`);

// SNAT in Worten (0.47.0): „Pool IPP_x, overload: 198.51.100.1-198.51.100.4“ bzw. „IP von wan1“
export function snatText(s: NonNullable<TraceHop["snat"]>): string {
  if (s.vip) return `VIP ${s.vip}, nat-source-vip`;
  if (s.pool) {
    if (s.missing) return `Pool ${s.pool} nicht gelesen – Quelle danach unbekannt`;
    const range = s.range && !s.sure ? `: Bereich ${s.range}` : "";
    const more = s.more?.length ? ` · weitere Pools ${s.more.join(", ")}` : "";
    return `Pool ${s.pool}${s.type ? `, ${s.type}` : ""}${range}${more}`;
  }
  return `IP von ${s.interface ?? "?"}`;
}

// Topologie-Modell (0.50.0, ADR 0019): Knoten und Kanten der Umgebung mit Herkunft und den
// Plausibilitäts-Befunden
export type TopoNodeKind = "firewall" | "vdom" | "vrf" | "segment" | "exit" | "router";
export type TopoOrigin = "derived" | "manual" | "confirmed";
export interface TopoNode {
  id: string;
  kind: TopoNodeKind;
  label: string;
  origin: TopoOrigin;
  source?: string;
  context?: string;
  members?: string[];
  missing?: string[];
  group?: string | null;
  ha?: { mode: string | null; group: string | null; members: string[] } | null;
  network?: string;
  exit_kind?: string;
  prefixes?: string[];
  ip?: string;
  remote_as?: number | null;
  names?: string[];
  dismissed?: boolean;
  degree: number;
  issues: number;
}
export interface TopoVia {
  node: string;
  label: string;
  interface: string | null;
  zone: string | null;
  address: string;
}
export interface TopoEdgeRouter {
  ip: string;
  status: "active" | "backup" | "neighbor";
  interface: string | null;
  zone: string | null;
  remote_as: number | null;
  state: string | null;
  active: number;
  backup: number;
  default: boolean;
}
export interface TopoEdge {
  id: string;
  kind: "transfer" | "member" | "vdom_link" | "route" | "bgp" | "uplink" | "exit_route" | "parallel" | "sync";
  a: string;
  b: string;
  origin: TopoOrigin;
  network: string | null;
  via: TopoVia[];
  routes: { active: Record<string, number>; backup: Record<string, number>; samples: Record<string, string[]> };
  sessions?: { from: string; peer: string; remote_as: number | null; state: string | null }[];
  routers?: TopoEdgeRouter[];
  status?: string;
  links?: { a: string; b: string }[];
  next_hops?: string[];
  ports?: string[];
  prefixes?: string[];
  group?: string;
  peer?: string;
  to?: Record<string, number>;
}
export interface TopoIssue {
  type: string;
  subject: string;
  node?: string;
  nodes?: (string | TopoVia)[];
}
export interface TopologyModel {
  nodes: TopoNode[];
  edges: TopoEdge[];
  issues: TopoIssue[];
  summary: { nodes: Record<string, number>; edges: Record<string, number>; issues: Record<string, number> };
}
export const fetchTopology = (tenantId: number, node?: string) =>
  getJSON<TopologyModel>(`/api/topology${cq({ tenant_id: tenantId, node })}`);

// Logische Zonen (0.51.0, ADR 0024): in Automato gepflegt, Selektoren wie beim Kunden und dazu
// `exit` (Netze hinter einer Anbindung); Zonenmatrix mit Absicht je Paar, Zonenplan
export type ZoneSelectorKind = SelectorKind | "exit";
// Art = Ebene des Zonenmodells (0.56.0): Datacenter, Edge, Transfer, Core, Distribution, Access,
// Public (extern) – dazu die bisherigen Arten
export type ZoneKind =
  | "dc"
  | "edge"
  | "transfer"
  | "core"
  | "distribution"
  | "access"
  | "external"
  | "internal"
  | "dmz"
  | "management"
  | "shared";
// Woher die Farbe kommt (0.56.0): Grundschema, eingebaute Vorlage, Zone selbst, Art
export type ZoneColorSource = "schema" | "template" | "zone" | "kind";
export type ZoneIntent = "allow" | "limited" | "deny";
export interface ZoneSelector {
  id?: number;
  kind: ZoneSelectorKind;
  value: string;
  source_system_id: number | null;
  exclude: boolean;
}
export interface LogicalZone {
  id: number;
  tenant_id: number;
  name: string;
  shared?: boolean; // 0.76.0: Kunde gewählt, Zone eines anderen bzw. geteilten Kunden
  code: string | null;
  kind: ZoneKind;
  color: string | null;
  customer_id: number | null;
  customer: string | null;
  position: number;
  description: string | null;
  nets: number;
  epgs: number;
  updated_at: string;
  selectors: ZoneSelector[];
  own?: boolean; // Sicht eines Kunden-Benutzers (0.53.0): eigene bzw. geteilte Zone
  color_source?: ZoneColorSource;
  schema?: string | null; // passende Zone des Grundschemas bzw. der Vorlage
  own_color?: string | null; // an der Zone gespeicherte Farbe
  nodes?: string[]; // Knoten der Zone (Firewall/VDOM, ACI-VRF)
  transitions?: { to: string; to_id: number | string; name: string; count: number }[];
}
export interface ZoneIn {
  tenant_id: number;
  name: string;
  code: string | null;
  kind: ZoneKind;
  color: string | null;
  customer_id: number | null;
  position: number;
  description: string | null;
  selectors: ZoneSelector[];
}
export interface ZonePreview {
  counts: { net: number; epg: number };
  samples: { net: { network: string; via: string }[]; epg: string[] };
  overlaps: { network: string; zone: string }[];
  overlaps_total: number;
}
export type ZoneCell = number | "any" | "none" | "other"; // other = anderer Kunde (0.53.0)
export interface ZoneSummary {
  id: number;
  name: string;
  code: string | null;
  kind: ZoneKind;
  color: string | null;
  customer_id: number | null;
  position: number;
  nets: number;
  epgs: number;
  color_source?: ZoneColorSource;
}
export interface ZoneMatrix {
  zones: ZoneSummary[];
  axes: ZoneCell[];
  cells: {
    src: ZoneCell;
    dst: ZoneCell;
    allow: number;
    deny: number;
    violations: number;
    impossible?: number; // nach dem Weg nicht möglich (0.81.0)
    intent: ZoneIntent | null;
    note: string | null;
    services?: string[]; // Absicht je Dienst (0.57.0): erlaubte Dienste bei „eingeschränkt“
  }[];
  intents: {
    src: number;
    dst: number;
    intent: ZoneIntent;
    note: string | null;
    services?: string[];
    updated_by: string | null;
    updated_at: string;
  }[];
  summary: { zones: number; rules: number; assigned: number; without: number; violations: number; off_path?: number };
}
export interface ZoneCellRule {
  id: number;
  name: string;
  external_id: string;
  action: "allow" | "deny";
  domain: string;
  source: string | null;
  source_system_id: number;
  vdom: string | null;
  tenant: string | null;
  violation: boolean;
  outside?: string[]; // Dienste ausserhalb der Absicht „eingeschränkt“ (0.57.0)
  impossible?: string | null; // warum nach dem Weg nicht möglich (0.81.0)
}
export interface ZoneCellRules {
  total: number;
  violations: number;
  impossible?: number;
  items: ZoneCellRule[];
}
// Netz eines Übergangs (0.56.0): Beine der Knoten (Interface bzw. L3Out) und Router ohne gelesenes Gerät
export interface TransitionNet {
  network: string;
  legs: { node: string; label: string; interface: string | null; address: string; l3out: boolean }[];
  routers: { ip: string; names: string[] }[];
  routes?: { zone: number; prefixes: string[] }[]; // Zone nur hinter dem Router (0.81.0)
}
export type PlanZoneId = number | string; // Zone, "customer:<id>", "none", "outside", "other"
export interface PlanZone {
  id: PlanZoneId;
  name: string;
  code: string | null;
  kind: string;
  color: string | null;
  customer_id: number | null;
  customer?: string | null;
  position: number;
  nets: number;
  epgs: number;
  samples: string[];
  nodes?: string[]; // Knoten-IDs (0.56.0)
  nodes_total?: number; // Plan je Kunde: Knoten der geteilten Zone insgesamt (0.57.1)
  internal?: number;
  collapsed?: boolean; // Kunde als eine Zone
  members?: string[];
  partner?: boolean; // 0.66.0: Zone eines anderen Kunden, mit dem kommuniziert wird (ohne Knoten)
}
export interface PlanTransition {
  id: string;
  a: PlanZoneId;
  b: PlanZoneId;
  name: string; // „DC-EINET“
  networks: TransitionNet[];
  count: number;
  l3out: boolean;
  contact: boolean; // Knoten ohne Zone an einem Netz der Zone (kein Übergang)
  intent_ab?: PlanDirection | null; // 0.78.0: Absicht von a nach b
  intent_ba?: PlanDirection | null;
}
// Absicht einer Richtung im Zonenplan (0.78.0): bei einem Kunden als Gruppe die strengste seiner
// Zonen (`mixed` = verschiedene), Dienste der eingeschränkten
export interface PlanDirection {
  intent: ZoneIntent;
  services: string[];
  pairs: number;
  mixed: boolean;
  note: string | null;
}
// Zonenplan: seit 0.56.0 Format 2 (Zonen mit Knoten, Übergänge, Ansicht je Kunde); Versionen bis
// 0.55.0 im Format 1 (Zonen an den Knoten)
export interface ZonePlan {
  format?: number;
  view?: { mode: "all" | "overview" | "customer"; customer_id: number | null; customer: string | null };
  customers?: { id: number; name: string }[];
  zones: PlanZone[];
  nodes: { id: string; kind: string; label: string; source: string; group: string | null; zone?: number | null; zones?: number }[];
  transitions?: PlanTransition[];
  // 0.66.0: erlaubende Regeln je Zonenpaar (`ab` von a nach b, `ba` zurück)
  flows?: { id: string; a: PlanZoneId; b: PlanZoneId; rules: number; ab: number; ba: number }[];
  // 0.78.0: Absichten zwischen Zonen ohne Übergang (eigene Linie)
  intents?: { id: string; a: PlanZoneId; b: PlanZoneId; intent_ab: PlanDirection | null; intent_ba: PlanDirection | null }[];
  conflicts?: { node: string; label: string; zones: string[] }[];
  attachments?: {
    zone: number;
    node: string;
    via: ("interface" | "fabric" | "exit" | "route")[];
    interfaces: string[];
    networks: string[];
    count: number;
    routes: { prefix: string; next_hop: string | null; interface: string | null; names: string[] }[];
    default: boolean;
  }[];
  links?: { a: number; b: number; nodes: string[] }[];
  unzoned: { total: number; items: { network: string; node: string; where: string | null; nodes?: string[] }[] };
  summary: {
    zones: number;
    nodes: number;
    transitions?: number;
    collapsed?: number;
    unassigned?: number;
    attachments?: number;
    links?: number;
    unzoned: number;
    without_node: number;
  };
  layout?: PlanLayout; // Zeichnung (0.52.0), gleich wie im Export
  // 0.61.0: Filter der Ansicht, alle Zonen der Ansicht (Fokus), gespeicherte Anordnung
  filter?: { active: boolean; zones: number; total: number; hidden: number; matches?: number; label?: string };
  zone_choices?: { id: string; name: string; kind: string | null; layer: string }[];
  arrangement?: { view: string | null; saved: boolean; count: number; updated_by: string | null; updated_at: string | null };
  positions?: Record<string, [number, number]>;
}
// Zeichnung des Zonenplans (0.52.0, `store/plan_export.layout`): Pixel, Ursprung oben links
export interface PlanBox {
  id: string | number;
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  sub: string;
  kind: string | null;
  side?: "l" | "r";
  color?: string | null;
  fill?: string;
  lines?: string[]; // Format 2: Knoten der Zone bzw. Zonen des Kunden
  pseudo?: boolean; // Knoten ohne Zone, ausserhalb
  collapsed?: boolean;
  code?: string | null;
  placed?: boolean; // 0.61.0: von Hand angeordnet
  partner?: boolean; // 0.66.0: Zone eines anderen Kunden (Kommunikation)
}
export interface PlanEdge {
  id?: string;
  zone?: number; // Format 1: Zone am Knoten
  node?: string;
  a?: PlanZoneId; // Format 2: Übergang zwischen Zonen
  b?: PlanZoneId;
  points?: [number, number][]; // seit 0.65.1 eine Kurve als Punktfolge
  lx?: number;
  ly?: number;
  anchor?: "start" | "middle"; // Beschriftung neben bzw. über der Linie (0.65.1)
  flow?: boolean; // 0.66.0: Kommunikation (Regeln), keine Netz-Verbindung
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  label: string;
  dashed: boolean;
  count?: number;
  networks?: string[];
  contact?: boolean;
  name?: string; // 0.61.0: Name des Übergangs auch bei ausgeblendeter Beschriftung
  // 0.78.0: Absicht je Richtung (Pfeil am Ziel), Text für den Titel, Linie nur für die Absicht
  ab?: ZoneIntent | null;
  ba?: ZoneIntent | null;
  intent_text?: string;
  intent?: boolean;
}
export interface PlanLayout {
  format?: number;
  width: number;
  height: number;
  zones: PlanBox[];
  nodes: PlanBox[];
  edges: PlanEdge[];
  offset?: [number, number]; // 0.61.0: Verschiebung gegenüber den gespeicherten Positionen
}
// Versionen des Zonenplans (0.52.0)
export interface PlanVersion {
  id: number;
  number: number;
  label: string;
  note: string | null;
  fingerprint: string;
  created_by: string | null;
  created_at: string;
  app_version: string | null;
  summary: Partial<ZonePlan["summary"]>;
}
export interface PlanVersionFull extends PlanVersion {
  data: { zones: (ZoneSummary & { customer: string | null; selectors: { kind: string; value: string; exclude: boolean; source: string | null }[] })[]; plan: ZonePlan; matrix: ZoneMatrix };
}
export interface PlanChanges<T> {
  added: string[];
  removed: string[];
  changed: { key: string; old: T; new: T }[];
}
export interface PlanDiff {
  from: number;
  to: number | null;
  same: boolean;
  zones: PlanChanges<{ kind: string; customer: string | null; color: string | null; selectors: string[] }>;
  intents: PlanChanges<{ intent: ZoneIntent; note: string | null }>;
  attachments: PlanChanges<{ via: string[]; interfaces: string[] }>;
  unzoned: { added: string[]; removed: string[] };
  matrix: { cell: string; old: [number, number, number]; new: [number, number, number] }[];
}
export type PlanFormat = "drawio" | "svg" | "pdf" | "docx" | "zip"; // docx/zip: Word (+ draw.io) für Confluence (0.71.0)
export const fetchZones = (tenantId: number) => getJSON<LogicalZone[]>(`/api/zones${cq({ tenant_id: tenantId })}`);
export const createZone = (body: ZoneIn) => sendJSON<LogicalZone>("/api/zones", "POST", body);
export const updateZone = (id: number, body: ZoneIn) => sendJSON<LogicalZone>(`/api/zones/${id}`, "PUT", body);
export const deleteZone = (id: number) => sendJSON<void>(`/api/zones/${id}`, "DELETE");
// Vorschläge beim Erfassen einer Zone (0.66.0): Selektoren, deren Name zur Zone passt
export interface ZoneAssistItem {
  selector: ZoneSelector;
  source: string;
  source_label: string;
  label: string;
  reason: string;
  score: number;
  nets: number;
  epgs: number;
  samples: string[];
  new: number;
  covered: boolean;
  conflicts: { network: string; zone: string }[];
  conflicts_total: number;
}
export const assistZone = (tenantId: number, body: { name: string; code: string | null; kind: string; zone_id?: number | null; selectors: ZoneSelector[] }) =>
  sendJSON<{ name: string; schema: string | null; items: ZoneAssistItem[]; total: number }>("/api/zones/assist", "POST", { tenant_id: tenantId, ...body });
export const previewZone = (tenantId: number, selectors: ZoneSelector[], zoneId?: number) =>
  sendJSON<ZonePreview>("/api/zones/preview", "POST", { tenant_id: tenantId, selectors, zone_id: zoneId ?? null });
export interface ZoneNetsData {
  total: number;
  items: { network: string; via: string }[];
  nodes?: string[]; // Knoten der Zone (0.56.0)
  transitions?: { to: string; to_id: number | string; name: string; networks: TransitionNet[] }[];
}
export const fetchZoneNets = (id: number) => getJSON<ZoneNetsData>(`/api/zones/${id}/nets`);
// Knoten (Firewall bzw. VDOM, ACI-VRF) einer Zone zuordnen (0.56.0, Operator)
export const assignZoneNodes = (zoneId: number, nodes: string[]) =>
  sendJSON<{ zone_id: number; added: string[] }>(`/api/zones/${zoneId}/nodes`, "POST", { nodes });
// Zonen-Vorschläge aus dem Gelesenen (0.54.0): neue Zone bzw. Selektoren einer bestehenden
// Quellen seit 0.56.0: ACI-Tenants (DC), Firewall (Knoten), Anbindung, IPAM, Grundschema bzw. Vorlage
export type ZoneSuggestionSource = "aci" | "node" | "exit" | "ipam" | "schema";
export interface ZoneSuggestion {
  key: string;
  action: "create" | "extend";
  zone_id: number | null;
  zone: string | null;
  name: string;
  code: string | null;
  kind: ZoneKind;
  kind_reason: string | null;
  color: string | null;
  customer_id: number | null;
  customer: string | null;
  sources: ZoneSuggestionSource[];
  evidence: string[];
  evidence_total: number;
  selectors: ZoneSelector[];
  nodes: string[]; // Knoten, die die Zone bekommt
  counts: { nets: number; epgs: number; nodes: number };
  samples: string[];
  conflicts: { network: string; zone?: string; suggestion?: string }[];
  dismissed: boolean;
  schema: string | null; // Zone des Grundschemas (0.55.0)
}
export interface SchemaIntent {
  src_zone_id: number;
  src: string;
  dst_zone_id: number;
  dst: string;
  intent: ZoneIntent;
  current: ZoneIntent | null;
  services?: string[]; // Dienste aus dem Schema bzw. in Automato (0.57.0)
  current_services?: string[];
  schema_src: string;
  schema_dst: string;
  note: string | null;
}
export interface ZoneSchemaStatus {
  profile_id: number;
  profile: string;
  zones: number;
  links: number;
  matched: Record<string, string>;
  missing: SchemaZone[];
  outside: { id: number; name: string }[];
  intents: SchemaIntent[];
}
// Knoten ohne Zone und ohne Vorschlag – zum Zuordnen (0.56.0)
export interface SuggestionNode {
  id: string;
  label: string;
  kind: string;
  source_system_id: number;
  context: string;
  group: string | null;
  hint: string | null;
  kind_hint: ZoneKind | null;
}
export interface ZoneSuggestions {
  items: ZoneSuggestion[];
  nodes: SuggestionNode[];
  schema: ZoneSchemaStatus | null;
  rest: { total: number; items: { network: string; node: string; where: string; nodes?: string[] }[] };
  summary: { suggestions: number; create: number; extend: number; dismissed: number; nodes: number; rest: number; template: boolean };
}
export const fetchZoneSuggestions = (tenantId: number, dismissed = false) =>
  getJSON<ZoneSuggestions>(`/api/zones/suggestions${qs({ tenant_id: tenantId, dismissed: dismissed ? "true" : undefined })}`);
export const acceptZoneSuggestions = (tenantId: number, keys: string[]) =>
  sendJSON<{ created: number[]; updated: number[]; missing: string[] }>("/api/zones/suggestions/accept", "POST", {
    tenant_id: tenantId,
    keys,
  });
// Absichten aus dem Grundschema setzen (ohne `pairs` alle fehlenden; abweichende nur mit `overwrite`)
export const applySchemaIntents = (tenantId: number, pairs?: [number, number][], overwrite = false) =>
  sendJSON<{ changed: { src: number; dst: number; intent: ZoneIntent; before: ZoneIntent | null }[] }>("/api/zones/schema/intents", "POST", {
    tenant_id: tenantId,
    pairs: pairs ?? null,
    overwrite,
  });
export const dismissZoneSuggestions = (tenantId: number, keys: string[], undo = false) =>
  sendJSON<{ keys: string[] }>(`/api/zones/suggestions/dismiss${undo ? "/undo" : ""}`, "POST", { tenant_id: tenantId, keys });
export const fetchZoneMatrix = (tenantId: number) => getJSON<ZoneMatrix>(`/api/zones/matrix${cq({ tenant_id: tenantId })}`);
export const fetchZoneCellRules = (tenantId: number, src: ZoneCell, dst: ZoneCell, offset = 0, impossible = false) =>
  getJSON<ZoneCellRules>(
    `/api/zones/matrix/rules${cq({ tenant_id: tenantId, src: String(src), dst: String(dst), offset, limit: 100, impossible: impossible ? "true" : undefined })}`,
  );
export const setZonePolicy = (tenantId: number, src: number, dst: number, intent: ZoneIntent | null, note: string | null, services?: string[]) =>
  sendJSON<{ src: number; dst: number; intent: ZoneIntent | null; note: string | null; services: string[] }>("/api/zones/policy", "PUT", {
    tenant_id: tenantId,
    src_zone_id: src,
    dst_zone_id: dst,
    intent,
    note,
    services: services ?? null,
  });
// Ansicht (0.56.0): ohne Kunde die Übersicht (Kunden je als eine Zone), mit Kunde dessen Plan, `all` alle Zonen einzeln
export interface PlanView {
  customerId?: number | null;
  all?: boolean;
  filter?: PlanFilter;
}
// Filter des Zonenplans (0.61.0): Suche bzw. Fokus mit Nachbarn bis `depth`, Ebenen (Spalten) und Elemente
// ausblenden – Ansicht und Export gleich
export interface PlanFilter {
  q?: string;
  focus?: string[];
  depth?: number;
  hideLayers?: string[];
  hide?: string[];
  flows?: boolean; // 0.66.0: Kommunikation (erlaubende Regeln je Zonenpaar) einblenden
}
function planQs(tenantId: number, view?: PlanView, extra: Record<string, string | number | undefined> = {}): string {
  const p = new URLSearchParams(qs({ tenant_id: tenantId, customer_id: view?.customerId ?? undefined, all: view?.all ? "true" : undefined, ...extra }).slice(1));
  const f = view?.filter;
  if (f?.q?.trim()) p.set("q", f.q.trim());
  for (const z of f?.focus ?? []) p.append("focus", z);
  if ((f?.q?.trim() || f?.focus?.length) && f?.depth !== undefined && f.depth !== 1) p.set("depth", String(f.depth));
  for (const l of f?.hideLayers ?? []) p.append("hide_layer", l);
  for (const h of f?.hide ?? []) p.append("hide", h);
  if (f?.flows) p.set("flows", "true");
  const s = p.toString();
  return s ? `?${s}` : "";
}
export const fetchZonePlan = (tenantId: number, view?: PlanView) => getJSON<ZonePlan>(`/api/zones/plan${planQs(tenantId, view)}`);
// Anordnung (0.61.0, Operator): Position je Zone in der Ansicht `overview`, `all` bzw. `customer:<id>`
export const savePlanArrangement = (tenantId: number, view: string, positions: Record<string, [number, number]>) =>
  sendJSON<NonNullable<ZonePlan["arrangement"]>>("/api/zones/plan/arrangement", "PUT", { tenant_id: tenantId, view, positions });
// Zonenplan aus draw.io zurück einlesen (0.83.0, Operator): Vorschau, dann Teile übernehmen
export interface PlanImportPreview {
  view: string;
  page: string | null;
  positions: number;
  moved: string[];
  renames: { id: number; from: string; to: string }[];
  intents: {
    src_zone_id: number;
    src: string;
    dst_zone_id: number;
    dst: string;
    intent: ZoneIntent;
    reason: string;
    services: string[];
    note: string | null;
    current: ZoneIntent | null;
  }[];
  new_zones: { name: string; kind: ZoneKind; code: string | null }[];
  missing: string[];
  warnings: string[];
}
export interface PlanImportParts {
  positions: boolean;
  renames: boolean;
  intents: boolean;
  new_zones: boolean;
}
export const previewPlanImport = (tenantId: number, content: string) =>
  sendJSON<PlanImportPreview>("/api/zones/plan/import/preview", "POST", { tenant_id: tenantId, content });
export const applyPlanImport = (tenantId: number, content: string, parts: PlanImportParts) =>
  sendJSON<{ view: string; positions: number; renames: unknown[]; intents: unknown[]; new_zones: string[]; skipped: string[] }>(
    "/api/zones/plan/import",
    "POST",
    { tenant_id: tenantId, content, ...parts },
  );
export const resetPlanArrangement = (tenantId: number, view: string) =>
  sendJSON<void>(`/api/zones/plan/arrangement${qs({ tenant_id: tenantId, view })}`, "DELETE");
export const fetchPlanVersions = (tenantId: number) =>
  getJSON<{ current_fingerprint: string; items: PlanVersion[] }>(`/api/zones/plan/versions${qs({ tenant_id: tenantId })}`);
export const savePlanVersion = (tenantId: number, label: string, note: string | null) =>
  sendJSON<PlanVersion>("/api/zones/plan/versions", "POST", { tenant_id: tenantId, label, note });
export const fetchPlanVersion = (id: number) => getJSON<PlanVersionFull>(`/api/zones/plan/versions/${id}`);
export const fetchPlanVersionDiff = (id: number, to?: number) =>
  getJSON<PlanDiff>(`/api/zones/plan/versions/${id}/diff${qs({ to })}`);
export const deletePlanVersion = (id: number) => sendJSON<void>(`/api/zones/plan/versions/${id}`, "DELETE");
// Export als Datei: Name aus Content-Disposition (zonenplan-<Datum> bzw. zonenplan-v<n>)
export async function exportZonePlan(
  tenantId: number,
  format: PlanFormat,
  versionId?: number,
  view?: PlanView,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetchChecked(
    withLang(`/api/zones/plan/export${planQs(tenantId, versionId ? undefined : view, { format, version_id: versionId })}`),
  );
  const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "");
  return { blob: await response.blob(), filename: match?.[1] ?? `zonenplan.${format}` };
}

// Wer bin ich laut Orchestrator (0.53.0): Rollen, Mandanten, Kunden – auch aus der Zuordnung in
// Automato (Benutzer → Kunde), die Keycloak nicht kennt
export interface ServerMe {
  subject: string;
  roles: string[];
  tenant_ids: number[] | null;
  customer_ids: number[] | null;
  is_admin: boolean;
  is_customer: boolean;
  customer_source: "claim" | "automato" | "claim+automato" | null;
  customers: { id: number; name: string; role?: string | null }[];
  // feinere Rollen (ADR 0018 Ergänzung): höchste Kunden-Rolle, je Kunde, Capabilities
  customer_role?: CustomerRole | null;
  customer_roles?: Record<string, CustomerRole>;
  capabilities?: Capability[];
  logins?: string[]; // eigene Kennungen (sub, Benutzername, E-Mail – klein)
}
export const fetchServerMe = () => getJSON<ServerMe>("/api/auth/me");

// Feinere Rollen der Kunden-Benutzer (ADR 0018 Ergänzung): Rolle je Kunde → Capabilities;
// „operator“ = Altbestand aus 0.53.0 (gilt als triage)
export type CustomerRole = "viewer" | "triage" | "editor" | "customer_admin";
export type Capability = "doc.read" | "findings.triage" | "applications.edit" | "customer_users.manage";

// Zuordnung Benutzer → Kunde (0.53.0, Admin; seit den feineren Rollen auch der Kunden-Admin)
export interface CustomerUserRow {
  id: number;
  tenant_id: number;
  customer_id: number;
  customer: string | null;
  login: string;
  role: CustomerRole | "operator";
  origin?: "staff" | "customer"; // angelegt vom Betreuer bzw. vom Kunden-Admin
  note: string | null;
  created_by: string | null;
  created_at: string;
}
export const fetchCustomerUsers = (tenantId: number) => getJSON<CustomerUserRow[]>(`/api/customer-users${qs({ tenant_id: tenantId })}`);
export const createCustomerUser = (body: { customer_id: number; login: string; role: string; note: string | null }) =>
  sendJSON<CustomerUserRow>("/api/customer-users", "POST", body);
export const updateCustomerUser = (id: number, body: { role?: string; note?: string | null }) =>
  sendJSON<CustomerUserRow>(`/api/customer-users/${id}`, "PATCH", body);
export const deleteCustomerUser = (id: number) => sendJSON<void>(`/api/customer-users/${id}`, "DELETE");

// Verkabelung (0.62.0, ADR 0025): Geräte, Verbindungen aus CDP/LLDP, Ports der Switches, VLANs
export type LinkState = "both" | "one_sided" | "unread";
export interface CablingDevice {
  key: string;
  name: string;
  shared?: boolean; // 0.76.0: Kunde gewählt, geteilter Teil der Umgebung
  kind: string; // switch | leaf | spine | firewall | esxi | unknown
  read: boolean;
  source: string | null;
  source_system_id: number | null;
  platform: string | null;
  model: string | null;
  version: string | null;
  mgmt_ip: string | null;
  ports: number;
  connected: number;
  links: number;
  issues: number;
  one_sided: number;
}
export interface CablingSummary {
  devices: number;
  switches: number;
  links: number;
  states: Record<LinkState, number>;
  issues: number;
  unread: number;
  endpoints?: number; // MACs an Ports ohne Uplinks (0.63.0)
}
export interface CablingEnd {
  device: string;
  device_key: string;
  kind: string;
  read: boolean;
  source: string | null;
  source_system_id: number | null;
  port: string | null;
  status: string | null;
  speed: string | null;
  duplex: string | null;
  mode: string | null;
  vlan: number | null;
  native_vlan: number | null;
  trunk_vlans: string | null;
  lag: string | null;
  vpc: string | null;
  description: string | null;
}
export interface CablingLink {
  id: string;
  a: CablingEnd;
  b: CablingEnd;
  protocols: string[];
  state: LinkState;
  issues: { type: string; text: string }[];
}
export interface CablingPort {
  device: string;
  name: string;
  kind: string;
  status: string | null;
  enabled: boolean | null;
  speed: string | null;
  duplex: string | null;
  mode: string | null;
  vlan: number | null;
  native_vlan: number | null;
  trunk_vlans: string | null;
  lag: string | null;
  description: string | null;
  media: string | null;
  vpc: string | null;
  // 0.65.0: Peer-Link (vPC-Domäne), Spanning Tree je Port bzw. Port-Channel (VLAN-Bereiche)
  vpc_peer_link?: string | null;
  stp_blocked?: string | null;
  stp_root?: string | null;
  peer: { device: string; port: string | null; read: boolean; kind: string } | null;
  link_state: LinkState | null;
  issues: string[];
  endpoints?: number; // MACs am Port (0.63.0)
}
export interface CablingVlan {
  vid: number;
  names: string[];
  epgs: string[]; // ACI (0.69.1): EPGs mit dem VLAN (Tenant/AP/EPG)
  sources: ("aci" | "switch")[];
  devices: { device: string; access: number; trunks: number }[];
}
export const fetchCablingDevices = (tenantId: number) =>
  getJSON<{ summary: CablingSummary; items: CablingDevice[] }>(`/api/cabling/devices${cq({ tenant_id: tenantId })}`);
export const fetchCablingLinks = (
  tenantId: number,
  f: { device?: string; state?: LinkState | ""; q?: string; issues?: boolean; offset?: number; limit?: number } = {},
) =>
  getJSON<{ total: number; items: CablingLink[] }>(
    `/api/cabling/links${cq({ tenant_id: tenantId, device: f.device, state: f.state || undefined, q: f.q, issues: f.issues ? "true" : undefined, offset: f.offset, limit: f.limit })}`,
  );
// Patchungen eines Geräts im Plan (0.70.0): je Kabel beide Enden mit Konfiguration
export interface CablingPatchEnd extends CablingEnd {
  media?: string | null;
  stp_blocked?: string | null;
  stp_root?: string | null;
  epgs?: string[];
  neighbor?: string | null;
  enabled?: boolean | null;
  endpoints: number;
  model: string | null;
  mgmt_ip: string | null;
}
export interface CablingPatch {
  id: string;
  a: CablingPatchEnd;
  b: CablingPatchEnd;
  state: LinkState;
  protocols: string[];
  issues: { type: string; text: string }[];
}
export interface CablingPatches {
  device: { name: string; key: string; kind: string; model: string | null; version: string | null; mgmt_ip: string | null; source: string | null; read: boolean };
  peer: string | null;
  total: number;
  issues: number;
  items: CablingPatch[];
}
export const fetchCablingPatches = (tenantId: number, device: string, peer?: string | null) =>
  getJSON<CablingPatches>(`/api/cabling/patches${cq({ tenant_id: tenantId, device, peer: peer ?? undefined })}`);
export const fetchCablingPorts = (
  tenantId: number,
  f: { device?: string; status?: string; vlan?: number | null; q?: string; offset?: number; limit?: number } = {},
) =>
  getJSON<{ total: number; items: CablingPort[] }>(
    `/api/cabling/ports${cq({ tenant_id: tenantId, device: f.device, status: f.status || undefined, vlan: f.vlan ?? undefined, q: f.q, offset: f.offset, limit: f.limit })}`,
  );
export const fetchCablingVlans = (tenantId: number) => getJSON<CablingVlan[]>(`/api/cabling/vlans${cq({ tenant_id: tenantId })}`);

// Endpunkte (0.63.0): MAC-Tabelle und ARP der Switches
export interface EndpointVm {
  id: number;
  name: string;
  nic: string;
  portgroup: string | null;
  vlan: number | null;
  switch: string | null;
  host: string | null;
  cluster: string | null;
  power_state: string | null;
}
export interface CablingEndpoint {
  mac: string;
  vlan: number | null;
  device: string;
  port: string;
  kind: string | null;
  source_system_id: number;
  first_seen: string;
  last_seen: string;
  uplink: boolean;
  port_description: string | null;
  port_mode: string | null;
  via: { device: string; port: string | null; kind: string; read: boolean } | null;
  ips: string[];
  names: string[];
  vm: EndpointVm | null;
  epgs: string[];
}
export interface EndpointLocation {
  mac: string;
  ips: string[];
  names: string[];
  vm: EndpointVm | null;
  epgs: string[];
  reason: string;
  seen_on: number;
  locations: CablingEndpoint[];
  behind: CablingEndpoint[];
  earlier: CablingEndpoint[];
}
export const fetchCablingEndpoints = (
  tenantId: number,
  f: { device?: string; port?: string; vlan?: number | null; q?: string; uplinks?: boolean; offset?: number; limit?: number } = {},
) =>
  getJSON<{ total: number; summary: { macs: number; ports: number; vms: number; with_ip: number }; items: CablingEndpoint[] }>(
    `/api/cabling/endpoints${cq({ tenant_id: tenantId, device: f.device, port: f.port, vlan: f.vlan ?? undefined, q: f.q, uplinks: f.uplinks ? "true" : undefined, offset: f.offset, limit: f.limit })}`,
  );
// L2-Sicht (0.64.0): Plan der Verkabelung und Ausfall
export interface L2PlanNode {
  key: string;
  name: string;
  kind: string;
  read: boolean;
  switching: boolean;
  layer: number;
  x: number;
  y: number;
  source: string | null;
  vlan_present: boolean | null;
}
export interface L2PlanEdge {
  a: string;
  b: string;
  ids: string[];
  links: number;
  state: LinkState;
  issues: number;
  lags: string[];
  ports: [string, string][];
  // mit VLAN; „standby“ = Spanning Tree blockiert das VLAN hier (Reserve, 0.65.0)
  vlan?: "yes" | "no" | "partial" | "standby" | "unknown";
  stp: string | null; // von Spanning Tree blockierte VLANs (Bereiche) auf diesem Gerätepaar
}
export interface L2Plan {
  nodes: L2PlanNode[];
  edges: L2PlanEdge[];
  pairs: { a: string; b: string; domain: string }[]; // vPC-Paare (0.65.0)
  width: number;
  height: number;
  vlan?: { vid: number; present: string[]; islands: string[][] };
}
export interface L2Failure {
  devices: string[];
  links: string[];
  device: string | null;
  link: string | null;
  main: string[];
  parts: string[][];
  cut_off: string[][];
  cut_off_keys: string[];
}
export const fetchL2Plan = (tenantId: number, f: { ends?: boolean; vlan?: number | null } = {}) =>
  getJSON<L2Plan>(`/api/cabling/plan${cq({ tenant_id: tenantId, ends: f.ends === false ? "false" : undefined, vlan: f.vlan ?? undefined })}`);
// Ausfall: mehrere Geräte bzw. Kabel gleichzeitig (0.65.0) - Parameter je Wert wiederholt
export const fetchL2Failure = (tenantId: number, f: { devices?: string[]; links?: string[] }) => {
  const p = new URLSearchParams({ tenant_id: String(tenantId) });
  for (const d of f.devices ?? []) p.append("device", d);
  for (const l of f.links ?? []) p.append("link", l);
  return getJSON<L2Failure>(`/api/cabling/failure?${p}`);
};
// Export des Plans (0.65.0): SVG bzw. draw.io wie in der Ansicht, Name aus Content-Disposition
export async function exportL2Plan(
  tenantId: number,
  format: "svg" | "drawio",
  f: { ends?: boolean; vlan?: number | null } = {},
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetchChecked(
    withLang(
      `/api/cabling/plan/export${cq({ tenant_id: tenantId, format, ends: f.ends === false ? "false" : undefined, vlan: f.vlan ?? undefined })}`,
    ),
  );
  const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "");
  return { blob: await response.blob(), filename: match?.[1] ?? `verkabelung.${format}` };
}
export const locateEndpoint = (tenantId: number, q: string) =>
  getJSON<{ query: string; kind: string | null; ips: string[]; items: EndpointLocation[] }>(
    `/api/cabling/locate${cq({ tenant_id: tenantId, q })}`,
  );
