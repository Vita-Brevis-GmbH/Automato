// Meine Exporte (1.21.0, ADR 0069, Anwender: „Zudem geht es sehr lange, bis der Export steht, könnte man dies
// als Hintergrunddienst laufen lassen?“): Exporte der Applikations-Doku im Hintergrund mit Fortschritt (n von m,
// aktuelle Applikation), Fehlern je Applikation, Download, Abbrechen und Entfernen - auch nach dem Neuladen,
// solange der Export nicht abgelaufen ist. Nur die eigenen Exporte (der Server zeigt keine fremden).
import { useCallback, useEffect, useState } from "react";
import { L } from "../../i18n";
import { download, ErrorBox, fmtTime } from "../../ui";
import {
  cancelAppExport,
  deleteAppExport,
  downloadAppExport,
  ExportJob,
  ExportStatus,
  fetchAppExports,
  isActive,
} from "./exportApi";

const POLL_MS = 2000;

const STATUS: Record<ExportStatus, [string, string]> = {
  waiting: ["info", L("wartet", "waiting")],
  running: ["info", L("läuft", "running")],
  done: ["ok", L("fertig", "done")],
  failed: ["crit", L("Fehler", "failed")],
  cancelled: ["na", L("abgebrochen", "cancelled")],
  aborted: ["warn", L("abgebrochen (Neustart)", "aborted (restart)")],
};

function size(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function MyExports({
  tenantId,
  refresh = 0,
  page = false,
}: {
  tenantId: number;
  refresh?: number; // neu laden (z. B. nach dem Start eines Exports)
  page?: boolean; // auf der Seite Applikationen: eigene Karte, nur mit Exporten
}) {
  const [jobs, setJobs] = useState<ExportJob[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null); // Fehler je Applikation aufgeklappt
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetchAppExports(tenantId)
        .then(setJobs)
        .catch((e) => {
          setErr(e);
          setJobs([]);
        }),
    [tenantId],
  );
  useEffect(() => {
    void load();
  }, [load, refresh]);
  const active = (jobs ?? []).some(isActive);
  useEffect(() => {
    if (!active) return;
    const t = window.setTimeout(() => void load(), POLL_MS);
    return () => window.clearTimeout(t);
  }, [active, jobs, load]);

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setBusy(id);
    setErr(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };
  const save = (job: ExportJob) =>
    act(job.id, async () => {
      const { blob, filename } = await downloadAppExport(job);
      download(filename, blob);
    });

  if (jobs === null) return page ? null : <p className="faint">{L("Lade Exporte…", "Loading exports…")}</p>;
  if (page && jobs.length === 0) return null;
  const body = (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {jobs.length === 0 ? (
        <p className="faint">{L("Noch keine Exporte.", "No exports yet.")}</p>
      ) : (
        <table className="my-exports">
          <tbody>
            {jobs.map((j) => {
              const [cls, label] = STATUS[j.status];
              return (
                <tr key={j.id} data-export-id={j.id} data-status={j.status}>
                  <td style={{ width: "100%" }}>
                    <div>
                      <span className={`chip ${cls} export-status`}>{j.cancelling ? L("wird abgebrochen…", "cancelling…") : label}</span>{" "}
                      <span className="mono">{j.filename}</span>{" "}
                      <span className="faint">
                        {L("gestartet", "started")} {fmtTime(j.created_at)}
                        {j.status === "done" && (
                          <>
                            {" "}
                            · {size(j.size)} · {L("abrufbar bis", "available until")} {fmtTime(j.expires_at)}
                          </>
                        )}
                      </span>
                    </div>
                    {isActive(j) && (
                      <div className="export-progress-line">
                        <progress className="export-progress" max={Math.max(1, j.total)} value={j.done} />{" "}
                        <span className="export-count">
                          {L(`${j.done} von ${j.total} Applikationen`, `${j.done} of ${j.total} applications`)}
                        </span>
                        {j.current && (
                          <span className="faint export-current">
                            {" "}
                            · {L("jetzt", "now")}: {j.current}
                          </span>
                        )}
                      </div>
                    )}
                    {!isActive(j) && j.status !== "done" && j.message && <div className="faint export-message">{j.message}</div>}
                    {j.errors.length > 0 && (
                      <div className="export-errors">
                        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(open === j.id ? null : j.id)}>
                          {L(
                            `${j.errors.length} Applikation${j.errors.length === 1 ? "" : "en"} mit Fehler`,
                            `${j.errors.length} application${j.errors.length === 1 ? "" : "s"} failed`,
                          )}{" "}
                          {open === j.id ? "▴" : "▾"}
                        </button>
                        {open === j.id && (
                          <ul className="export-error-list">
                            {j.errors.map((e) => (
                              <li key={e.application_id}>
                                <b>{e.name}</b> <span className="faint mono">{e.error}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </td>
                  <td style={{ whiteSpace: "nowrap", verticalAlign: "top" }}>
                    {j.status === "done" && (
                      <button className="btn btn-primary btn-sm export-download" disabled={busy === j.id} onClick={() => void save(j)}>
                        {L("Herunterladen", "Download")}
                      </button>
                    )}{" "}
                    {isActive(j) ? (
                      <button
                        className="btn btn-ghost btn-sm export-cancel"
                        disabled={busy === j.id || j.cancelling}
                        onClick={() => void act(j.id, () => cancelAppExport(j.id))}
                      >
                        {L("Abbrechen", "Cancel")}
                      </button>
                    ) : (
                      <button
                        className="btn btn-ghost btn-sm export-remove"
                        disabled={busy === j.id}
                        title={L("Export samt Datei entfernen", "Remove export and file")}
                        onClick={() => void act(j.id, () => deleteAppExport(j.id))}
                      >
                        {L("Entfernen", "Remove")}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
  if (!page) return <div id="my-exports">{body}</div>;
  return (
    <div className="card" id="my-exports-page">
      <div className="card-head">
        <h2>{L("Meine Exporte", "My exports")}</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          {L(
            "Doku als ZIP je Applikation mit Export-Passwort – im Hintergrund, abrufbar bis zum Ablauf",
            "Documentation as one ZIP per application with an export password – in the background, available until it expires",
          )}
        </span>
      </div>
      <div className="card-body">{body}</div>
    </div>
  );
}
