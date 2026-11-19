// Export der Applikations-Doku im Hintergrund (1.21.0, ADR 0069): Auftrag anlegen, meine Exporte,
// Fortschritt, abbrechen, entfernen, herunterladen (orchestrator/app/api/app_exports.py). Das
// Export-Passwort geht nur im Rumpf der Anfrage mit (nie in der Adresse); der Server speichert es nicht.
import { fileOf, getCustomerFilter, getJSON, qs, sendJSON } from "../../api";

export type ExportStatus = "waiting" | "running" | "done" | "failed" | "cancelled" | "aborted";
export interface ExportError {
  application_id: number;
  name: string;
  error: string;
}
export interface ExportJob {
  id: string;
  kind: string;
  tenant_id: number;
  status: ExportStatus;
  total: number;
  done: number;
  current: string | null; // Applikation, die gerade rechnet
  errors: ExportError[];
  message: string | null; // Grund bei Fehler bzw. Abbruch
  filename: string | null;
  size: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  expires_at: string;
  cancelling: boolean;
}

export const isActive = (j: ExportJob) => j.status === "waiting" || j.status === "running";

export const startAppExport = (tenantId: number, password: string, ids: number[]) =>
  sendJSON<ExportJob>("/api/applications/export", "POST", {
    tenant_id: tenantId,
    customer_id: getCustomerFilter(),
    password,
    ids,
  });
export const fetchAppExports = (tenantId?: number | null) =>
  getJSON<ExportJob[]>(`/api/applications/exports${qs({ tenant_id: tenantId ?? undefined })}`);
export const cancelAppExport = (id: string) => sendJSON<ExportJob>(`/api/applications/exports/${id}/cancel`, "POST");
export const deleteAppExport = (id: string) => sendJSON<void>(`/api/applications/exports/${id}`, "DELETE");
export const downloadAppExport = (job: ExportJob) =>
  fileOf(`/api/applications/exports/${job.id}/download`, job.filename ?? "applikationen.zip");
