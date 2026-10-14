// Schlanker API-Client. Das UI spricht ausschliesslich mit dem Orchestrator
// (ARCHITECTURE.md) - im Dev ueber den Vite-Proxy unter /api.

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
  platform: string;
  enabled: boolean;
}

export interface Tenant {
  id: number;
  name: string;
  code: string | null;
  ipam_type: string;
  dns_type: string;
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
}

export function setAuthToken(token: string): void {
  try {
    localStorage.setItem("automato_token", token);
  } catch {
    /* localStorage nicht verfuegbar -> ignorieren */
  }
}

export function clearAuthToken(): void {
  try {
    localStorage.removeItem("automato_token");
  } catch {
    /* ignorieren */
  }
}

export function getAuthToken(): string | null {
  try {
    return localStorage.getItem("automato_token");
  } catch {
    return null;
  }
}

export interface Nat {
  id: number;
  name: string;
  external_id: string;
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
  tables: DocTable[];
}

// Bearer-Token (gesetzt nach OIDC-Login, E1.3b). Im dev-Modus leer -> Standard-Admin.
function authHeaders(): Record<string, string> {
  try {
    const t = localStorage.getItem("automato_token");
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch {
    return {};
  }
}

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// Schreib-Helfer (POST/PATCH/DELETE). 204 -> kein Body.
async function sendJSON<T>(url: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const fetchHealth = () => getJSON<HealthStatus>("/api/health");
export const fetchAuthConfig = () => getJSON<AuthConfig>("/api/auth/config");
export const fetchSystems = () => getJSON<System[]>("/api/systems");
export const fetchTenants = () => getJSON<Tenant[]>("/api/tenants");
export const fetchSites = (tenantId: number) =>
  getJSON<Site[]>(`/api/tenants/${tenantId}/sites`);
export const createSite = (
  tenantId: number,
  body: { code: string; name: string; description?: string | null },
) => sendJSON<Site>(`/api/tenants/${tenantId}/sites`, "POST", body);
export const deleteSite = (tenantId: number, siteId: number) =>
  sendJSON<void>(`/api/tenants/${tenantId}/sites/${siteId}`, "DELETE");
export const patchTenantCode = (tenantId: number, code: string | null) =>
  sendJSON<Tenant>(`/api/tenants/${tenantId}`, "PATCH", { code });

function tenantQuery(tenantId?: number | null): string {
  return tenantId != null ? `?tenant_id=${tenantId}` : "";
}
export const fetchAppDocSystems = (tenantId?: number | null) =>
  getJSON<AppDocSystem[]>(`/api/appdoc/systems${tenantQuery(tenantId)}`);
export const fetchSystemDoc = (name: string, tenantId?: number | null) =>
  getJSON<SystemDoc>(`/api/appdoc/systems/${encodeURIComponent(name)}${tenantQuery(tenantId)}`);
export const fetchFindings = () => getJSON<Finding[]>("/api/findings");
export const fetchNat = () => getJSON<Nat[]>("/api/nat");
export const fetchApplications = () => getJSON<Application[]>("/api/applications");
export const fetchApplication = (name: string) =>
  getJSON<ApplicationDetail>(`/api/applications/${encodeURIComponent(name)}`);
export const fetchRuleDetail = (id: number) => getJSON<RuleDetail>(`/api/rules/${id}`);

export function fetchRules(q?: string, tenantId?: number | null): Promise<Rule[]> {
  const params = new URLSearchParams();
  if (q && q.trim()) params.set("q", q.trim());
  if (tenantId != null) params.set("tenant_id", String(tenantId));
  const qs = params.toString();
  return getJSON<Rule[]>(`/api/rules${qs ? `?${qs}` : ""}`);
}
