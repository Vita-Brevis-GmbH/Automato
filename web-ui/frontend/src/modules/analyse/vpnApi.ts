// Kopplung der VPN-Tunnel von Hand (1.23.0, ADR 0063 Nachtrag): Aufrufe `/vpn/pairs`
// (orchestrator/app/api/vpn.py). Laufzeitdaten in Automato - nichts geht an ein Gerät.
import { qs, sendJSON } from "../../api";

export const setVpnPair = (body: { tenant_id: number; a: number; b: number; mode?: "coupled" | "separated" }) =>
  sendJSON<{ pair_id: number; mode: string; previous: string | null; a: string; b: string }>("/api/vpn/pairs", "PUT", body);
export const deleteVpnPair = (id: number, tenantId: number) =>
  sendJSON<{ deleted: number }>(`/api/vpn/pairs/${id}${qs({ tenant_id: tenantId })}`, "DELETE");
