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
  ipam_type: string;
  dns_type: string;
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

export const fetchHealth = () => getJSON<HealthStatus>("/api/health");
export const fetchSystems = () => getJSON<System[]>("/api/systems");
export const fetchTenants = () => getJSON<Tenant[]>("/api/tenants");
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
