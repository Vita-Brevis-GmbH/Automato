// Schlanker API-Client. Das UI spricht ausschliesslich mit dem Orchestrator
// (ARCHITECTURE.md) - im Dev ueber den Vite-Proxy unter /api.
import * as auth from "./auth";

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
  pair?: RulePair | null; // IPv4/IPv6-Gegenstück (nur Markierung, eigene Regel)
}

// IPv4/IPv6-Gegenstück derselben Firewall: same = gleiche Objekte/Services/Interfaces
export interface RulePair {
  rule_id: number;
  name: string;
  family: string | null;
  match: "same" | "similar";
  diff: string[]; // src | dst | service | name | zone
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
}

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
  read_scope: string; // Cisco ACI: gelesene ACI-Tenants (kommagetrennt, leer = alle)
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
}

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
  targets?: NatTarget[]; // Virtual Server: Real Server
  attrs?: Record<string, unknown>; // Virtual Server: Server-Typ, Methode, Persistenz, Health-Checks
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
  settings: Record<string, string>;
  rules: Rule[];
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
  columns: string[];
  rows: Record<string, string>[];
}

export interface SystemDoc {
  system: string;
  system_ip: string | null;
  tenant_code: string | null;
  site: SiteRef | null;
  tables: DocTable[];
}

// Applikationen (ADR 0012): Mitglieder AP (`Tenant/AP`), EPG (DN) und Server (Objektname)
export type MemberKind = "ap" | "epg" | "host";
export type Criticality = "low" | "medium" | "high" | "critical";
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
  code: string | null;
  owner: string | null;
  criticality: Criticality | null;
  site: SiteRef | null;
  description: string | null;
  member_count: number;
  system_count: number;
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
  code: string;
  name: string;
  application_id: number | null;
  members: AppCandidate[];
}

export interface ApplicationInput {
  name?: string;
  code?: string | null;
  owner?: string | null;
  criticality?: Criticality | null;
  site_id?: number | null;
  description?: string | null;
}

export interface ApplicationDoc {
  application: Application;
  tenant_code: string | null;
  systems: { name: string; kind: string; ip: string | null; site: SiteRef | null }[];
  tables: DocTable[];
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
    if (e.status === 401) return "Nicht angemeldet oder Sitzung abgelaufen – bitte neu anmelden.";
    if (e.status === 403) return `Keine Berechtigung (${e.detail || "Rolle/Mandant fehlt"}).`;
    if (e.status === 409) return `Existiert bereits (${e.detail}).`;
    if (e.status === 422) return `Ungültige Eingabe (${e.detail}).`;
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
  if (!auth.isOidc()) return {};
  const token = await auth.accessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Fetch mit Token und einheitlicher Fehlerbehandlung; liefert die rohe Antwort.
async function fetchChecked(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...(await authHeaders()), ...(init.headers as Record<string, string>) };
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = detailText(body);
    if (response.status === 401 && auth.isOidc()) {
      // Token abgelehnt -> neu anmelden (mit Schleifenschutz, siehe auth.relogin)
      void auth.relogin(`Der Orchestrator lehnt das Anmelde-Token ab: ${detail || "HTTP 401"}`);
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
export const fetchSystems = (tenantId?: number | null) =>
  getJSON<System[]>(`/api/systems${qs({ tenant_id: tenantId })}`);
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
  } & SystemCredentials,
) => sendJSON<System>(`/api/systems/${id}`, "PATCH", body);
export const deleteSystem = (id: number) => sendJSON<DeleteCounts>(`/api/systems/${id}`, "DELETE");

// Läufe
export const fetchRuns = (tenantId?: number | null, limit = 10) =>
  getJSON<Run[]>(`/api/jobs${qs({ tenant_id: tenantId, limit })}`);
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
}
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
    src_zone: opts.srcZone,
    dst_zone: opts.dstZone,
    action: opts.action,
    limit: opts.limit,
    offset: opts.offset,
  };
  const response = await fetchChecked(`/api/rules${qs(params)}`);
  const rules = (await response.json()) as Rule[];
  const total = Number(response.headers.get("X-Total-Count") ?? rules.length);
  return { rules, total: Number.isFinite(total) ? total : rules.length };
}
export const fetchRuleSummary = (tenantId?: number | null) =>
  getJSON<RuleSummary>(`/api/rules/summary${qs({ tenant_id: tenantId })}`);
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
  limit?: number;
  offset?: number;
}) =>
  getPage<Nat>(
    `/api/nat${qs({ tenant_id: opts.tenantId, nat_type: opts.natType, q: opts.q?.trim(), limit: opts.limit, offset: opts.offset })}`,
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
    `/api/findings${qs({
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
  getJSON<FindingSummary>(`/api/findings/summary${qs({ tenant_id: tenantId, type, q: q?.trim() })}`);
export const patchFinding = (id: number, body: { status?: string; note?: string }) =>
  sendJSON<Finding>(`/api/findings/${id}`, "PATCH", body);
export const fetchPublishedServices = (tenantId?: number | null) =>
  getJSON<PublishedService[]>(`/api/published-services${qs({ tenant_id: tenantId })}`);
export const fetchPublishedService = (name: string, tenantId?: number | null) =>
  getJSON<PublishedServiceDetail>(
    `/api/published-services/${encodeURIComponent(name)}${qs({ tenant_id: tenantId })}`,
  );
export const fetchAppDocSystems = (tenantId?: number | null) =>
  getJSON<AppDocSystem[]>(`/api/appdoc/systems${qs({ tenant_id: tenantId })}`);
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
  getJSON<Application[]>(`/api/applications${qs({ tenant_id: tenantId })}`);
export const fetchApp = (id: number) => getJSON<ApplicationDetail>(`/api/applications/${id}`);
export const createApp = (
  tenantId: number,
  body: ApplicationInput & { name: string; members?: { kind: MemberKind; ref: string }[] },
) => sendJSON<ApplicationDetail>(`/api/applications${qs({ tenant_id: tenantId })}`, "POST", body);
export const patchApp = (id: number, body: ApplicationInput) =>
  sendJSON<ApplicationDetail>(`/api/applications/${id}`, "PATCH", body);
export const deleteApp = (id: number) => sendJSON<void>(`/api/applications/${id}`, "DELETE");
export const addAppMembers = (id: number, members: { kind: MemberKind; ref: string }[]) =>
  sendJSON<ApplicationDetail>(`/api/applications/${id}/members`, "POST", { members });
export const removeAppMember = (id: number, memberId: number) =>
  sendJSON<ApplicationDetail>(`/api/applications/${id}/members/${memberId}`, "DELETE");
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

// System (Admin)
export interface SystemStatus {
  version: string;
  database: { revision: string | null; head: string | null; up_to_date: boolean };
  agent_package: { version: string | null; signature: string; pubkey_set: boolean; agents: number; agents_current: number };
  scheduler: { enabled: boolean; interval_seconds: number; timezone: string; scheduled_sources: number; next_run_at: string | null };
  security: { environment: string; auth_mode: string; internal_mtls_mode: string; audit_encryption: boolean; credentials_key: string };
  backup: { at: string | null; file: string | null; encrypted: boolean | null; size: number | null } | null;
}
export const fetchSystemStatus = () => getJSON<SystemStatus>("/api/system/status");

// Objekte & Inventar (rein lesend, Mandant nötig)
export const fetchObjects = (
  tenantId: number,
  params: {
    category?: string; // kommagetrennt
    source_system_id?: number | null;
    q?: string;
    issues_only?: boolean;
    limit?: number;
    offset?: number;
  } = {},
) =>
  getJSON<Paged<DeviceObject>>(
    `/api/objects${qs({
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
  getJSON<Inventory[K][]>(`/api/inventory/${kind}${qs({ tenant_id: tenantId, ...params, q: params.q?.trim() })}`);

// Abgleich
export const fetchReconcileSummary = (tenantId: number) =>
  getJSON<ReconcileSummary>(`/api/reconcile/summary${qs({ tenant_id: tenantId })}`);
export const fetchIpamCompare = (
  tenantId: number,
  params: { status?: string; q?: string; limit?: number; offset?: number } = {},
) => getJSON<IpamCompare>(`/api/reconcile/ipam${qs({ tenant_id: tenantId, ...params, q: params.q?.trim() })}`);
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
