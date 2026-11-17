// Reservierungen von VLAN- und AS-Nummern und AS-Nummern einzeln (1.19.0, ADR 0066): Typen und Aufrufe der API
// `/reservations` (orchestrator/app/api/reservations.py) und `/routing/as-numbers` (orchestrator/app/api/as_numbers.py).
import { getCustomerFilter, getJSON, qs, sendJSON } from "../../api";

export type ReservationKind = "vlan" | "asn";
export type ReservationState = "free" | "in_use" | "conflict";

export interface ReservationUse {
  number: number;
  where: string;
  name?: string;
}

export interface ReservationSite {
  id: number;
  code: string;
  name: string;
  kind: string;
}

export interface Reservation {
  id: number;
  tenant_id: number;
  kind: ReservationKind;
  low: number;
  high: number;
  label: string;
  count: number;
  site_id: number | null;
  site: ReservationSite | null;
  site_missing: boolean;
  vlan_group: string | null;
  purpose: string;
  used_before: number[];
  created_by: string;
  created_at: string;
  changed_by: string | null;
  changed_at: string | null;
  in_use: ReservationUse[];
  in_use_total: number;
  in_use_numbers: number[];
  state: ReservationState;
  findings: { id: number; type: string; status: string }[];
  overlaps?: { id: number; label: string; purpose: string; overlap: string }[]; // nur nach dem Speichern
}

export interface ReservationList {
  items: Reservation[];
  summary: Record<ReservationKind, { total: number; free: number; in_use: number; conflict: number }>;
}

export interface ReservationOptions {
  sites: ReservationSite[];
  vlan_groups: { slug: string; source: string; site_id: number | null; site: string | null }[];
  limits: Record<ReservationKind, [number, number]>;
}

export interface ReservationIn {
  tenant_id: number;
  kind: ReservationKind;
  low: number;
  high: number | null;
  site_id: number | null;
  vlan_group: string | null;
  purpose: string;
}

export const fetchReservations = (tenantId: number, f: { kind?: ReservationKind | ""; q?: string } = {}) =>
  getJSON<ReservationList>(`/api/reservations${qs({ tenant_id: tenantId, kind: f.kind, q: f.q })}`);
export const fetchReservationOptions = (tenantId: number) =>
  getJSON<ReservationOptions>(`/api/reservations/options${qs({ tenant_id: tenantId })}`);
export const createReservation = (body: ReservationIn) => sendJSON<Reservation>("/api/reservations", "POST", body);
export const updateReservation = (id: number, body: ReservationIn) =>
  sendJSON<Reservation>(`/api/reservations/${id}`, "PUT", body);
export const releaseReservation = (id: number) => sendJSON<void>(`/api/reservations/${id}`, "DELETE");

// -- AS-Nummern einzeln ---------------------------------------------------------------------------------------------------

export interface AsDevice {
  kind: "firewall" | "router" | "fabric";
  label: string;
  source: string;
  source_system_id: number;
  node?: string;
  context?: string;
  router_id?: string;
  l3out?: string;
}

export interface AsNeighbor {
  peer_ip: string;
  names?: string[];
  network?: string;
  peer_node?: string; // gelesenes Gerät hinter der Adresse
  at: AsDevice;
  local_as?: number;
  state?: string;
  established: boolean;
  session?: "ebgp" | "ibgp";
  configured_only?: boolean;
  shutdown?: boolean;
  description?: string;
}

export interface AsEntry {
  asn: number;
  own: boolean;
  private: boolean;
  devices: AsDevice[];
  neighbors: AsNeighbor[];
  established: number;
  reservations: { id: number; label: string; purpose: string; by: string }[];
}

export interface AsNumbers {
  items: AsEntry[];
  summary: { total: number; own: number; foreign: number; private: number; reserved: number };
  reservations: { id: number; label: string; low: number; high: number; purpose: string; by: string; used: number[] }[];
}

export const fetchAsNumbers = (tenantId: number, f: { q?: string; scope?: "own" | "foreign" | "" } = {}) =>
  getJSON<AsNumbers>(
    `/api/routing/as-numbers${qs({ tenant_id: tenantId, q: f.q, scope: f.scope, customer_id: getCustomerFilter() })}`,
  );
