// Läufe (0.13.0): alle Collector-Läufe des Mandanten seitenweise mit Gesamtzahl – nicht nur die
// letzten, damit auch ältere offene Läufe sichtbar bleiben und mit vielen Geräten. Filter Status
// (offen = wartet + läuft), Quellsystem, Plattform, Auslöser; offene Läufe abbrechen (Operator).
import { useCallback, useEffect, useState } from "react";
import { cancelRun, fetchRunsPage, fetchSystems, Run, System } from "./api";
import type { Me } from "./auth";
import { PLATFORM_LABEL } from "./refs";
import { Empty, ErrorBox, fmtTime, Loading, PageHead, RunStatusChip } from "./ui";

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
          <th>Lauf</th>
          <th>System</th>
          {detailed && <th>Plattform</th>}
          <th>Ausführung</th>
          <th className="num" title="Regeln bzw. publizierte Dienste (NSX ALB), VMs (vCenter), Geräte (Switches), Einträge (IPAM)">
            Einträge
          </th>
          <th>Status</th>
          <th>{detailed ? "Angelegt / begonnen" : "Zeit"}</th>
          {detailed && <th>Beendet</th>}
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
                  {r.execution === "external" ? "Agent" : "zentral"}
                </span>
                {r.trigger === "schedule" && (
                  <span className="chip info" title="vom Zeitplan des Quellsystems angelegt">
                    Zeitplan
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
                    {r.started_at && <div className="faint">begonnen {fmtTime(r.started_at)}</div>}
                  </>
                ) : (
                  fmtTime(r.finished_at ?? r.started_at ?? r.queued_at)
                )}
                {canCancel && open && (
                  <button
                    className="linkbtn"
                    style={{ marginLeft: 6 }}
                    title="Lauf beenden (z. B. hängt er nach einem Neustart des Agenten). Liefert der Agent doch noch ab, wird das Ergebnis eingespielt."
                    onClick={() => onCancel(r.id)}
                  >
                    Abbrechen
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
      <PageHead title="Läufe">
        Alle Collector-Läufe von „{tenantLabel}“, neueste zuerst
        {openCount ? ` · ${openCount} offen (wartet oder läuft)` : ""}.
      </PageHead>
      <div className="filterbar">
        <select className="sel" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Status: alle</option>
          <option value="open">offen (wartet oder läuft)</option>
          <option value="queued">wartet</option>
          <option value="running">läuft</option>
          <option value="failed">fehlgeschlagen</option>
          <option value="completed">erfolgreich</option>
        </select>
        <select className="sel" value={systemId} onChange={(e) => setSystemId(e.target.value)}>
          <option value="">Quellsystem: alle</option>
          {[...systems]
            .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
        </select>
        <select className="sel" value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="">Plattform: alle</option>
          {platforms.map((p) => (
            <option key={p} value={p}>
              {PLATFORM_LABEL[p] ?? p}
            </option>
          ))}
        </select>
        <select className="sel" value={trigger} onChange={(e) => setTrigger(e.target.value)}>
          <option value="">Auslöser: alle</option>
          <option value="manual">von Hand</option>
          <option value="schedule">Zeitplan</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={load}>
          Aktualisieren
        </button>
        {page && <span className="count">{total} Läufe</span>}
      </div>
      <ErrorBox error={error} prefix="Läufe laden" />
      <div className="card">
        <div className="card-body scroll">
          {page === null ? (
            <Loading />
          ) : page.runs.length === 0 ? (
            <Empty title="Keine Läufe">Filter anpassen oder ein Quellsystem einlesen.</Empty>
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
            {offset + 1}–{Math.min(offset + PAGE, total)} von {total}
            <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
              Zurück
            </button>
            <button className="btn btn-ghost btn-sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
              Weiter
            </button>
          </div>
        )}
      </div>
    </>
  );
}
