// Verkabelung im Detail (1.19.0, ADR 0031 Nachtrag 1.19.0): Typen und Aufrufe der API
// `/cabling/labels` und `/cabling/transceivers` (orchestrator/app/api/cable_labels.py).
import { getJSON, qs, sendJSON } from "../../api";

// Herkunft der Kabelnummer: gleich, verschieden, nur Automato, nur NetBox (null = keine Nummer)
export type CableLabelState = "same" | "differs" | "automato" | "netbox";
export type NeighborState = "ok" | "mismatch" | "unknown";

export interface PortRef {
  device: string;
  port: string;
  free?: boolean; // erfasstes Ende ohne gelesenes bzw. gemeldetes Gerät (frei erfasst, 1.23.0)
}
export interface AutomatoLabel {
  id: number;
  label: string;
  note: string | null;
  changed_by: string;
  changed_at: string | null;
  a: PortRef;
  b: PortRef;
}
export interface NetboxCable {
  id: string;
  label: string | null;
  source: string;
  source_system_id: number;
  url: string | null;
  status: string | null;
  a: PortRef;
  b: PortRef;
  type?: string;
  color?: string;
  length?: string;
}
export interface CableLabelInfo {
  state: CableLabelState | null;
  automato: AutomatoLabel | null;
  netbox: NetboxCable[];
}
export interface Transceiver {
  type?: string;
  vendor?: string;
  part?: string;
  serial?: string;
}
export type TransceiverStatus = "read" | "unreadable" | "unsupported";

export interface CableLabelRow extends CableLabelInfo {
  pair: string;
  a: PortRef;
  b: PortRef;
  link: { id: string; state: string; protocols: string[] } | null;
  neighbor: NeighborState | null;
  wrong: { at: PortRef; netbox: PortRef; seen: { device: string; port: string | null }[] }[];
}
export interface CableLabelSummary {
  same: number;
  differs: number;
  automato: number;
  netbox: number;
  automato_total: number;
  netbox_total: number;
  netbox_labels: number;
  mismatch: number;
  unverified: number;
}

export const fetchCableLabels = (tenantId: number, f: { state?: string; q?: string } = {}) =>
  getJSON<{ total: number; summary: CableLabelSummary; items: CableLabelRow[] }>(
    `/api/cabling/labels${qs({ tenant_id: tenantId, state: f.state || undefined, q: f.q || undefined })}`,
  );
export const saveCableLabel = (body: {
  tenant_id: number;
  a_device: string;
  a_port: string;
  b_device: string;
  b_port: string;
  label: string;
  note?: string | null;
}) => sendJSON<AutomatoLabel & { pair: string; state: CableLabelState; previous: string | null }>("/api/cabling/labels", "PUT", body);
export const deleteCableLabel = (id: number, tenantId: number) =>
  sendJSON<{ deleted: number }>(`/api/cabling/labels/${id}${qs({ tenant_id: tenantId })}`, "DELETE");
export const fetchTransceivers = (tenantId: number, device?: string) =>
  getJSON<{
    devices: { device: string; status: TransceiverStatus | null; count: number }[];
    items: ({ device: string; port: string } & Transceiver)[];
  }>(`/api/cabling/transceivers${qs({ tenant_id: tenantId, device })}`);

// Auswahl zum Erfassen (1.23.0, Anwender 2026-11-20: „Geräte und Ports auswählen“): Herkunft eines Geräts –
// gelesen (Ports aus dem Inventar), nur von Nachbarn gemeldet (CDP/LLDP), nur am Kabel einer NetBox bzw.
// früher frei erfasst (z. B. Patchpanel)
export type ChoiceOrigin = "inventory" | "neighbor" | "netbox" | "free";
export interface DeviceChoice {
  name: string;
  origin: ChoiceOrigin;
  kind: string | null;
  source: string | null;
  ports?: number;
  free_ports: boolean; // Ports nicht (vollständig) gelesen: auch frei
}
// Vorschlag der Gegenstelle eines Ports: aus CDP/LLDP, dem Kabel in NetBox bzw. einer Erfassung
export interface PeerChoice {
  device: string;
  port: string;
  via: string[]; // cdp | lldp | netbox | automato
  labels: string[];
}
export interface PortChoice {
  name: string;
  read: boolean;
  status: string | null;
  description: string | null;
  peers: PeerChoice[];
}
export const fetchLabelDevices = (tenantId: number, q: string) =>
  getJSON<{ total: number; items: DeviceChoice[] }>(`/api/cabling/labels/choices${qs({ tenant_id: tenantId, q: q || undefined, limit: 30 })}`);
export const fetchLabelPorts = (tenantId: number, device: string) =>
  getJSON<{ device: DeviceChoice | null; ports: PortChoice[] }>(`/api/cabling/labels/choices${qs({ tenant_id: tenantId, device })}`);
