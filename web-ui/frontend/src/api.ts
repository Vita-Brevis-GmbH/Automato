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

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

export const fetchHealth = () => getJSON<HealthStatus>("/api/health");
export const fetchSystems = () => getJSON<System[]>("/api/systems");
export const fetchFindings = () => getJSON<Finding[]>("/api/findings");
export const fetchRuleDetail = (id: number) => getJSON<RuleDetail>(`/api/rules/${id}`);

export function fetchRules(q?: string): Promise<Rule[]> {
  const query = q && q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
  return getJSON<Rule[]>(`/api/rules${query}`);
}
