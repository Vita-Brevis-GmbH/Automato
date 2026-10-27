// Läufe (0.13.0): alle Collector-Läufe des Mandanten seitenweise mit Gesamtzahl – nicht nur die
// letzten, damit auch ältere offene Läufe sichtbar bleiben und mit vielen Geräten. Filter Status
// (offen = wartet + läuft), Quellsystem, Plattform, Auslöser; offene Läufe abbrechen (Operator).
import { useCallback, useEffect, useState } from "react";
import { cancelRun, fetchRunsPage, fetchSystems, Run, System } from "./api";
import type { Me } from "./auth";
import { PLATFORM_LABEL } from "./refs";
import { Empty, ErrorBox, fmtTime, Loading, PageHead, RunStatusChip } from "./ui";
import { L } from "./i18n";

const PAGE = 50;
// Meldung auch bei Erfolg zeigen: Zusammenfassung des Laufs (IPAM, NSX ALB seit 0.78.5)
const SUMMARY_PLATFORMS = new Set(["infoblox", "netbox", "avi"]);

function duration(r: Run): string {
  const start = r.started_at ?? r.queued_at;
  if (!start || !r.finished_at) return "";
  const s = Math.max(0, Math.round((Date.parse(r.finished_at) - Date.parse(start)) / 1000));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

// Tabelle der Läufe – auch in der Übersicht verwendet
export function RunsTable({
  runs,
  systemName,
  canCancel,
  onCancel,
  detailed = false,
}: {
  runs: Run[];
  systemName: (id: number | null) => string;
  canCancel: boolean;
  onCancel: (id: string) => void;
  detailed?: boolean;
}) {
  return (
    <table>
      <thead>
        <tr>
          <th>{L("Lauf", "Run")}</th>
          <th>System</th>
          {detailed && <th>{L("Plattform", "Platform")}</th>}
          <th>{L("Ausführung", "Execution")}</th>
          <th
            className="num"
            title={L(
              "Regeln bzw. publizierte Dienste (NSX ALB), VMs (vCenter), Geräte (Switches), Einträge (IPAM)",
              "Rules or published services (NSX ALB), VMs (vCenter), devices (switches), entries (IPAM)",
            )}
          >
            {L("Einträge", "Entries")}
          </th>
          <th>Status</th>
          <th>{detailed ? L("Angelegt / begonnen", "Created / started") : L("Zeit", "Time")}</th>
          {detailed && <th>{L("Beendet", "Finished")}</th>}
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const open = r.status === "queued" || r.status === "running";
          return (
            <tr key={r.id} title={r.message ?? undefined}>
              <td className="mono">#{r.id.slice(0, 8)}</td>
              <td>{systemName(r.source_system_id)}</td>
              {detailed && <td className="faint">{PLATFORM_LABEL[r.platform] ?? r.platform}</td>}
              <td>
                <span className={`chip ${r.execution === "external" ? "local" : ""}`}>
                  {r.execution === "external" ? "Agent" : L("zentral", "central")}
                </span>
                {r.trigger === "schedule" && (
                  <span
                    className="chip info"
                    title={L("vom Zeitplan des Quellsystems angelegt", "created by the source system's schedule")}
                  >
                    {L("Zeitplan", "Schedule")}
                  </span>
                )}
              </td>
              <td className="num">{r.rule_count ?? "–"}</td>
              <td>
                <RunStatusChip status={r.status} />
                {/* offen: „Ergebnis angekommen – wartet aufs Einspielen“ (0.80.1) */}
                {r.message && (r.status === "failed" || open || SUMMARY_PLATFORMS.has(r.platform)) && (
                  <div className={r.status === "failed" ? "run-msg err" : "run-msg"} title={r.message}>
                    {r.message}
                  </div>
                )}
              </td>
              <td className="mono">
                {detailed ? (
                  <>
                    {fmtTime(r.queued_at)}
                    {r.started_at && <div className="faint">
                        {L("begonnen", "started")} {fmtTime(r.started_at)}
                      </div>}
                  </>
                ) : (
                  fmtTime(r.finished_at ?? r.started_at ?? r.queued_at)
                )}
                {canCancel && open && (
                  <button
                    className="linkbtn"
                    style={{ marginLeft: 6 }}
                    title={L(
                      "Lauf beenden (z. B. hängt er nach einem Neustart des Agenten). Liefert der Agent doch noch ab, wird das Ergebnis eingespielt.",
                      "End the run (e.g. if it hangs after an agent restart). If the agent still delivers, the result is ingested.",
                    )}
                    onClick={() => onCancel(r.id)}
                  >
                    {L("Abbrechen", "Cancel")}
                  </button>
                )}
              </td>
              {detailed && (
                <td className="mono">
                  {r.finished_at ? fmtTime(r.finished_at) : <span className="faint">–</span>}
                  {duration(r) && <div className="faint">{duration(r)}</div>}
                </td>
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function Runs({ me, tenantId, tenantLabel }: { me: Me; tenantId: number | null; tenantLabel: string }) {
  const [status, setStatus] = useState("");
  const [systemId, setSystemId] = useState("");
  const [platform, setPlatform] = useState("");
  const [trigger, setTrigger] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ runs: Run[]; total: number } | null>(null);
  const [openCount, setOpenCount] = useState<number | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    fetchSystems(tenantId)
      .then(setSystems)
      .catch(() => setSystems([]));
  }, [tenantId]);
  useEffect(() => setOffset(0), [status, systemId, platform, trigger, tenantId]);

  const load = useCallback(() => {
    setError(null);
    fetchRunsPage({
      tenantId,
      status,
      sourceSystemId: systemId ? Number(systemId) : null,
      platform,
      trigger,
      limit: PAGE,
      offset,
    })
      .then(setPage)
      .catch((e) => {
        setError(e);
        setPage({ runs: [], total: 0 });
      });
    fetchRunsPage({ tenantId, status: "open", limit: 1 })
      .then((p) => setOpenCount(p.total))
      .catch(() => setOpenCount(null));
  }, [tenantId, status, systemId, platform, trigger, offset]);
  useEffect(() => {
    setPage(null);
    load();
  }, [load]);
  // offene Läufe auf der Seite: alle 10 s aktualisieren
  const pending = (page?.runs ?? []).some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [pending, load]);

  const systemName = (id: number | null) =>
    id == null ? "–" : (systems.find((s) => s.id === id)?.name ?? `#${id}`);
  const platforms = [...new Set(systems.map((s) => s.platform))].sort();
  const total = page?.total ?? 0;

  return (
    <>
      <PageHead title={L("Läufe", "Runs")}>
        {L(
          <>
            Alle Collector-Läufe von „{tenantLabel}“, neueste zuerst
            {openCount ? ` · ${openCount} offen (wartet oder läuft)` : ""}.
          </>,
          <>
            All collector runs of “{tenantLabel}”, newest first
            {openCount ? ` · ${openCount} open (waiting or running)` : ""}.
          </>,
        )}
      </PageHead>
      <div className="filterbar">
        <select className="sel" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{L("Status: alle", "Status: all")}</option>
          <option value="open">{L("offen (wartet oder läuft)", "open (waiting or running)")}</option>
          <option value="queued">{L("wartet", "waiting")}</option>
          <option value="running">{L("läuft", "running")}</option>
          <option value="failed">{L("fehlgeschlagen", "failed")}</option>
          <option value="completed">{L("erfolgreich", "successful")}</option>
        </select>
        <select className="sel" value={systemId} onChange={(e) => setSystemId(e.target.value)}>
          <option value="">{L("Quellsystem: alle", "Source system: all")}</option>
          {[...systems]
            .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
        </select>
        <select className="sel" value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="">{L("Plattform: alle", "Platform: all")}</option>
          {platforms.map((p) => (
            <option key={p} value={p}>
              {PLATFORM_LABEL[p] ?? p}
            </option>
          ))}
        </select>
        <select className="sel" value={trigger} onChange={(e) => setTrigger(e.target.value)}>
          <option value="">{L("Auslöser: alle", "Trigger: all")}</option>
          <option value="manual">{L("von Hand", "manual")}</option>
          <option value="schedule">{L("Zeitplan", "Schedule")}</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={load}>
          {L("Aktualisieren", "Refresh")}
        </button>
        {page && <span className="count">{L(`${total} Läufe`, `${total} runs`)}</span>}
      </div>
      <ErrorBox error={error} prefix={L("Läufe laden", "Loading runs")} />
      <div className="card">
        <div className="card-body scroll">
          {page === null ? (
            <Loading />
          ) : page.runs.length === 0 ? (
            <Empty title={L("Keine Läufe", "No runs")}>
              {L("Filter anpassen oder ein Quellsystem einlesen.", "Adjust the filter or collect a source system.")}
            </Empty>
          ) : (
            <RunsTable
              runs={page.runs}
              systemName={systemName}
              canCancel={me.isOperator}
              onCancel={(id) => cancelRun(id).then(load, setError)}
              detailed
            />
          )}
        </div>
        {total > PAGE && (
          <div className="pager">
            {offset + 1}–{Math.min(offset + PAGE, total)} {L("von", "of")} {total}
            <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
              {L("Zurück", "Back")}
            </button>
            <button className="btn btn-ghost btn-sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
              {L("Weiter", "Next")}
            </button>
          </div>
        )}
      </div>
    </>
  );
}
