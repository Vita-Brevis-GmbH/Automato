// Spanning Tree (1.19.0): Typen und Aufrufe der API `/stp` (orchestrator/app/api/stp.py).
import { getJSON, qs, sendJSON } from "../../api";

export type StpStatus = "read" | "off" | "unsupported" | "unreadable";
export type StpTargetState = "ok" | "backup" | "mismatch" | "none";

export interface StpErrDisabled {
  port: string;
  reason: string | null;
  vlans: string | null;
  bpdu_guard: boolean;
}

export interface StpDeviceRow {
  device: string;
  source_system_id: number;
  source: string | null;
  site: string | null;
  status: StpStatus;
  mode: string | null;
  region: string | null;
  revision: number | null;
  note: string | null;
  read_at: string | null;
  instances: number;
  root_of: string[];
  blocked: number;
  errdisabled: StpErrDisabled[];
  tc_count: number;
  tc_busiest: { instance: string; delta: number; hours: number; per_day: number } | null;
}

export interface StpTarget {
  id: number;
  site_id: number | null;
  site: string | null;
  site_missing: boolean;
  vlans: string | null;
  root: string;
  backup: string | null;
  note: string | null;
  changed_by: string | null;
  changed_at: string | null;
  state: StpTargetState | null;
  root_known?: boolean;
  backup_known?: boolean;
}

export interface StpSite {
  id: number;
  code: string;
  name: string;
  kind: string;
}

export interface StpSummary {
  switches: number;
  status: Record<StpStatus, number>;
  modes: Record<string, number>;
  trees: number;
  instances: number;
  blocked: number;
  unexpected: number;
  errdisabled: number;
  bpdu_guard: number;
  targets: number;
  target_states: Record<StpTargetState, number>;
  tc_per_day: number;
  tc_default: number;
  unsupported: string[];
  unreadable: string[];
}

export interface StpOverview {
  summary: StpSummary;
  devices: StpDeviceRow[];
  sites: StpSite[];
  targets: StpTarget[];
  switch_names: string[];
}

export interface StpTreeBrief {
  key: string;
  kind: "vlan" | "mst";
  number: number;
  label: string;
  vlans: string;
  root: { names: string[]; mac: string | null; priority: number | null; label: string; read: boolean };
  target: StpTarget | null;
  target_state: StpTargetState;
  sites: string[];
  site_ids: number[];
  switches: number;
  blocked: number;
  unexpected: number;
  protocols: string[];
}

export interface StpNeighbor {
  device: string;
  port: string | null;
  kind: string | null;
  read: boolean;
}

export interface StpPort {
  port: string;
  role: string | null;
  state: string | null;
  cost: number | null;
  priority: string | null;
  type: string | null;
  inconsistent: string | null;
  blocked: boolean;
  neighbors: StpNeighbor[];
  unexpected: string[];
}

export interface StpMember {
  device: string;
  source_system_id: number;
  source: string | null;
  instance: string;
  site: string | null;
  protocol: string | null;
  is_root: boolean;
  bridge_priority: number | null;
  bridge_mac: string | null;
  root_cost: number | null;
  root_port: string | null;
  tc_count: number | null;
  tc_last_at: string | null;
  tc_from: string | null;
  tc_window: { delta: number; hours: number; per_day: number } | null;
  read_at: string | null;
  ports: StpPort[];
}

export interface StpEdge {
  from: string;
  port: string;
  kind: "root" | "blocked";
  to: string | null;
  to_port: string | null;
  in_tree: boolean;
  unexpected: string[];
}

export interface StpTree extends StpTreeBrief {
  members: StpMember[];
  edges: StpEdge[];
}

export const fetchStpOverview = (tenantId: number) => getJSON<StpOverview>(`/api/stp/overview${qs({ tenant_id: tenantId })}`);

export const fetchStpTrees = (
  tenantId: number,
  f: { vlan?: number | null; site_id?: number | null; q?: string; only?: string },
) =>
  getJSON<StpTreeBrief[]>(
    `/api/stp/trees${qs({ tenant_id: tenantId, vlan: f.vlan ?? undefined, site_id: f.site_id ?? undefined, q: f.q, only: f.only })}`,
  );

export const fetchStpTree = (tenantId: number, key: string) =>
  getJSON<StpTree>(`/api/stp/tree${qs({ tenant_id: tenantId, key })}`);

export const saveStpTarget = (body: {
  tenant_id: number;
  id?: number | null;
  site_id: number | null;
  vlans: string | null;
  root: string;
  backup: string | null;
  note: string | null;
}) => sendJSON<StpTarget>("/api/stp/targets", "PUT", body);

export const deleteStpTarget = (id: number) => sendJSON<void>(`/api/stp/targets/${id}`, "DELETE");

export const saveStpSettings = (tenantId: number, tcPerDay: number | null) =>
  sendJSON<{ tc_per_day: number; tc_default: number }>("/api/stp/settings", "PUT", {
    tenant_id: tenantId,
    tc_per_day: tcPerDay,
  });
