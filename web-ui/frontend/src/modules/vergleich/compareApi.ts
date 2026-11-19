// Wege vergleichen (1.19.0, ADR 0067): Typen und Aufrufe der API `/path-compare` (orchestrator/app/api/path_compare.py).
import { getJSON, qs } from "../../api";

export type CompareState = "ok" | "deviation" | "partial" | "unchecked" | "vpn" | "internal";
export type CompareKind = "vlan" | "cable" | "stp";
export interface CompareReason {
  key: string;
  text: string;
}
export interface ComparePort {
  device: string;
  port: string | null;
  mode: string | null;
  vlans: string | null;
  native_vlan: number | null;
  state?: string;
  role?: string;
  reason?: string;
}
// Abweichung bzw. Hinweis einer Prüfung: VLAN gesperrt bzw. anderes VLAN, blockierter Port, Kabel (NetBox, Verkabelung)
export interface CompareItem {
  kind: string; // not_allowed | other_vlan | blocked | unread | netbox | link_* (Verkabelung)
  link?: string | null;
  ports?: ComparePort[];
  vlan?: number;
  device?: string;
  port?: string | null;
  devices?: string[];
  text?: string;
  cable?: string;
  url?: string | null;
  netbox?: string;
  wrong?: { at: { device: string; port: string }; netbox: { device: string; port: string }; seen: { device: string; port: string | null }[] }[];
  a?: ComparePort;
  b?: ComparePort;
  state?: string;
  role?: string;
  reason?: string; // err-disabled: Grund (z. B. bpduguard)
}
export interface CompareCheck {
  state: Exclude<CompareState, "vpn" | "internal">;
  items: CompareItem[];
  reasons: CompareReason[];
  count: number;
}
export interface CompareSide {
  label: string;
  devices: string[];
  interface: string | null;
  key: string | null;
}
export interface CompareSection {
  label: string;
  kind: "l2" | "attach" | "vpn" | "internal";
  a: CompareSide;
  b: CompareSide;
  vlan: number | null;
  vlan_from: string | null;
  segment: { steps: { device: string; kind: string; read: boolean; in: string | null; out: string | null }[]; text: string; switches: number } | null;
  state: CompareState;
  checks: Record<CompareKind, CompareCheck>;
  reasons: CompareReason[];
  unchecked: string[];
}
export interface CompareSummary {
  sections: number;
  states: Partial<Record<CompareState, number>>;
  deviations: Record<CompareKind, number>;
  unchecked: (CompareReason & { count: number })[];
}
export interface ComparePath {
  kind: string;
  hops: string[];
  end: { kind: string; text: string } | null;
  sections: CompareSection[];
  summary: CompareSummary;
}
export interface CompareTrace {
  paths: ComparePath[];
  summary: CompareSummary;
  notes: string[];
}
export interface CompareSections {
  items: CompareSection[];
  summary: CompareSummary;
  not_l2: Record<string, number>;
  cabling: boolean;
  total: number;
}

export interface CompareQuery {
  src?: string;
  dst?: string;
  service?: string | null;
  start?: string;
  connection?: boolean;
  starts?: string[];
}

export const fetchCompareTrace = (tenantId: number, q: CompareQuery) => {
  const base = qs({ tenant_id: tenantId, src: q.src, dst: q.dst, service: q.service, start: q.start, connection: q.connection ? "true" : null });
  const starts = (q.starts ?? []).map((s) => `&starts=${encodeURIComponent(s)}`).join("");
  return getJSON<CompareTrace>(`/api/path-compare/trace${base}${starts}`);
};
export const fetchCompareSections = (tenantId: number, state?: string, q?: string) =>
  getJSON<CompareSections>(`/api/path-compare/sections${qs({ tenant_id: tenantId, state, q })}`);
