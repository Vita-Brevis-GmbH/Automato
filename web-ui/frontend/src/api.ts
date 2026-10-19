// Schlanker API-Client. Das UI spricht ausschliesslich mit dem Orchestrator
// (ARCHITECTURE.md) - im Dev ueber den Vite-Proxy unter /api.
import * as auth from "./auth";

export interface HealthStatus {
  status: string;
  db: string;
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
  sources: string[];
  destinations: string[];
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
}

export interface RuleDetail extends Rule {
  external_id: string;
  sequence: number | null;
  endpoints: Endpoint[];
  services: string[];
  protections?: string[] | null;
}

export interface Finding {
  id: number;
  tenant_id: number;
  type: string;
  severity: string;
  status: string;
  subject_kind: string;
  subject_id: string;
  detail: Record<string, unknown> | null;
  note: string | null;
  first_seen: string | null;
  last_seen: string | null;
}

export interface System {
  id: number;
  name: string;
  platform: string; // fortigate | cisco_aci
  enabled: boolean;
  base_url: string;
}

export interface Tenant {
  id: number;
  name: string;
  code: string | null;
  audit_reads: boolean;
  ipam_type: string;
  dns_type: string;
  naming_profile_id: number | null;
}

export interface Site {
  id: number;
  tenant_id: number;
  code: string;
  name: string;
  description: string | null;
}

export interface AuthConfig {
  auth_mode: string; // dev | oidc
  issuer: string;
  client_id: string;
  scope: string;
  admin_console_url: string; // Keycloak-Benutzerverwaltung (leer = unbekannt)
}

export interface Agent {
  id: number;
  name: string;
  tenant_id: number | null;
  enabled: boolean;
  last_seen: string | null;
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
}

export interface Application {
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

export interface ApplicationDetail extends Application {
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
  site: { id: number; code: string; name: string } | null;
  tables: DocTable[];
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

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
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
  body: { code: string; name: string; description?: string | null },
) => sendJSON<Site>(`/api/tenants/${tenantId}/sites`, "POST", body);
export const deleteSite = (tenantId: number, siteId: number) =>
  sendJSON<void>(`/api/tenants/${tenantId}/sites/${siteId}`, "DELETE");

// Quellsysteme
export const fetchSystems = () => getJSON<System[]>("/api/systems");
export const createSystem = (body: { name: string; platform: string; base_url?: string }) =>
  sendJSON<System>("/api/systems", "POST", body);

// Läufe
export const fetchRuns = (tenantId?: number | null, limit = 10) =>
  getJSON<Run[]>(`/api/jobs${qs({ tenant_id: tenantId, limit })}`);
export const startCollect = (body: {
  platform: string;
  tenant_id: number;
  source_system_id: number;
  execution: "inprocess" | "external";
}) => sendJSON<{ run_id: string; status: string }>("/api/jobs/collect", "POST", body);

// Agenten
export const fetchAgents = () => getJSON<Agent[]>("/api/agents");
export const createAgent = (name: string, tenantId: number | null) =>
  sendJSON<AgentCreated>("/api/agents", "POST", { name, tenant_id: tenantId });
export const setAgentEnabled = (id: number, enabled: boolean) =>
  sendJSON<Agent>(`/api/agents/${id}`, "PATCH", { enabled });

// Namensprofile & Wörterbücher
export const fetchNamingProfiles = () => getJSON<NamingProfile[]>("/api/naming-profiles");
export const createNamingProfile = (body: {
  customer: string;
  name: string;
  with_defaults: boolean;
}) => sendJSON<NamingProfile>("/api/naming-profiles", "POST", body);
export const fetchDictionary = (profileId: number) =>
  getJSON<DictionaryEntry[]>(`/api/naming-profiles/${profileId}/dictionary`);
export const upsertEntry = (
  profileId: number,
  body: { kind: string; key: string; value?: string | null },
) => sendJSON<DictionaryEntry>(`/api/naming-profiles/${profileId}/dictionary`, "POST", body);
export const deleteEntry = (entryId: number) =>
  sendJSON<void>(`/api/naming-profiles/dictionary/${entryId}`, "DELETE");

// Dokumentation
export function fetchRules(q?: string, tenantId?: number | null): Promise<Rule[]> {
  return getJSON<Rule[]>(`/api/rules${qs({ q: q?.trim(), tenant_id: tenantId })}`);
}
export const fetchRuleDetail = (id: number) => getJSON<RuleDetail>(`/api/rules/${id}`);
export const fetchNat = (tenantId?: number | null) =>
  getJSON<Nat[]>(`/api/nat${qs({ tenant_id: tenantId })}`);
export const fetchFindings = (tenantId?: number | null) =>
  getJSON<Finding[]>(`/api/findings${qs({ tenant_id: tenantId })}`);
export const patchFinding = (id: number, body: { status?: string; note?: string }) =>
  sendJSON<Finding>(`/api/findings/${id}`, "PATCH", body);
export const fetchApplications = (tenantId?: number | null) =>
  getJSON<Application[]>(`/api/applications${qs({ tenant_id: tenantId })}`);
export const fetchApplication = (name: string, tenantId?: number | null) =>
  getJSON<ApplicationDetail>(
    `/api/applications/${encodeURIComponent(name)}${qs({ tenant_id: tenantId })}`,
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
