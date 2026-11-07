// Prüfungen (0.110.0, ADR 0039; Anwender: „Findings als steckbare Prüfungen“): je Prüfung Modul, Befund-Typen, Dauer und
// Anzahl des letzten Durchgangs; je Mandant ein- bzw. ausschalten (Admin, Audit). Aus = läuft nicht, die Befunde fallen
// beim nächsten Abgleich weg. Ist das Modul der Prüfung aus, läuft sie auch nicht.
import { useCallback, useEffect, useState } from "react";
import { CheckInfo, fetchChecks, runReconcile, switchCheck } from "../../api";
import type { Me } from "../../auth";
import { L } from "../../i18n";
import { useModule } from "../../moduleState";
import { findingLabel } from "../../refs";
import { ErrorBox, fmtTime, Loading, Note } from "../../ui";

const STEP: Record<string, string> = {
  zonen: L("auch beim Speichern einer Zone", "also when a zone is saved"),
  quellen: L("auch im Zeitplan (Stand der Quellsysteme)", "also on schedule (state of the source systems)"),
  applikationen: L("im Hintergrund nach dem Abgleich", "in the background after the reconciliation"),
};

export default function Checks({
  me,
  tenantId,
  onShowType,
  onChanged,
}: {
  me: Me;
  tenantId: number | null;
  onShowType: (type: string) => void; // Befunde dieses Typs zeigen
  onChanged: () => void;
}) {
  const [checks, setChecks] = useState<CheckInfo[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const analyse = useModule("analyse"); // „Neu abgleichen“ gehört zur Analyse

  const load = useCallback(() => {
    if (tenantId === null) return;
    fetchChecks(tenantId)
      .then(setChecks)
      .catch((e) => {
        setErr(e);
        setChecks([]);
      });
  }, [tenantId]);
  useEffect(load, [load]);

  if (tenantId === null)
    return <Note>{L("Für die Prüfungen oben einen Mandanten wählen.", "Choose a tenant at the top for the checks.")}</Note>;

  const toggle = (c: CheckInfo) => {
    setBusy(c.key);
    setErr(null);
    setDone(null);
    switchCheck(tenantId, c.key, !c.enabled)
      .then(setChecks)
      .catch(setErr)
      .finally(() => setBusy(null));
  };

  const reconcile = () => {
    setBusy("reconcile");
    setErr(null);
    runReconcile(tenantId)
      .then((r) => {
        const n = Object.values(r.findings).reduce((a, b) => a + b, 0);
        setDone(L(`Neu abgeglichen: ${n} Befunde.`, `Reconciled again: ${n} findings.`));
        load();
        onChanged();
      })
      .catch(setErr)
      .finally(() => setBusy(null));
  };

  const total = (checks ?? []).reduce((n, c) => n + (c.effective ? (c.stats?.ms ?? 0) : 0), 0);
  return (
    <div className="card" id="checks">
      <div className="card-head">
        <h2>{L("Prüfungen", "Checks")}</h2>
        <span className="faint">
          {L(
            "Je Prüfung eine Datei im Paket ihres Moduls. Dauer und Anzahl: letzter Durchgang seit dem Start des Orchestrators.",
            "One file per check in the package of its module. Duration and count: last pass since the orchestrator started.",
          )}
        </span>
      </div>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {checks === null ? (
        <Loading />
      ) : (
        <div className="card-body scroll">
          <table className="compact">
            <thead>
              <tr>
                <th>{L("Prüfung", "Check")}</th>
                <th>{L("Modul", "Module")}</th>
                <th>{L("Befund-Typen", "Finding types")}</th>
                <th>{L("Läuft", "Runs")}</th>
                <th className="num">{L("Dauer", "Duration")}</th>
                <th className="num">{L("Anzahl", "Count")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {checks.map((c) => {
                const stats = c.effective ? c.stats : null; // läuft nicht: keine Zahlen
                return (
                  <tr key={c.key} data-check={c.key} className={c.effective ? "" : "faint"}>
                    <td style={{ maxWidth: 360 }}>
                      <b>{c.title}</b>
                      <div className="faint" style={{ fontSize: 12 }}>
                        {c.purpose}
                      </div>
                    </td>
                    <td className="nowrap">{c.module_title}</td>
                    <td>
                      {c.types.map((t) => (
                        <button key={t} className="chip local linkchip" onClick={() => onShowType(t)} title={t}>
                          {findingLabel(t)}
                        </button>
                      ))}
                    </td>
                    <td className="faint" style={{ fontSize: 12 }}>
                      {c.in_reconcile ? L("nach jedem Lauf", "after every run") : ""}
                      {c.step && (
                        <div>
                          {STEP[c.step] ?? c.step}
                        </div>
                      )}
                    </td>
                    <td className="num mono" title={stats ? fmtTime(stats.ran_at) : undefined}>
                      {stats ? `${stats.ms} ms` : <span className="faint">–</span>}
                    </td>
                    <td className="num mono">{stats ? stats.count : <span className="faint">–</span>}</td>
                    <td className="nowrap">
                      {!c.effective && c.enabled ? (
                        <span className="chip na" title={L("Das Modul der Prüfung ist aus", "The module of the check is off")}>
                          {L("Modul aus", "module off")}
                        </span>
                      ) : me.isAdmin ? (
                        <button
                          className={`btn btn-sm ${c.enabled ? "" : "btn-primary"}`}
                          disabled={busy !== null}
                          onClick={() => toggle(c)}
                        >
                          {busy === c.key ? "…" : c.enabled ? L("ausschalten", "switch off") : L("einschalten", "switch on")}
                        </button>
                      ) : (
                        <span className={`chip ${c.enabled ? "ok" : "na"}`}>{c.enabled ? L("an", "on") : L("aus", "off")}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="card-foot">
        {L(
          <>
            Gesamt {total} ms. Ausgeschaltet: die Prüfung läuft nicht, ihre Befunde fallen beim nächsten Abgleich weg
            (nach dem nächsten Lauf oder mit „Neu abgleichen“).
          </>,
          <>
            Total {total} ms. Switched off: the check does not run, its findings disappear at the next reconciliation
            (after the next run or with “Reconcile again”).
          </>,
        )}{" "}
        {me.isOperator && !me.isCustomer && analyse && (
          <button className="btn btn-ghost btn-sm" id="checks-reconcile" disabled={busy !== null} onClick={reconcile}>
            {busy === "reconcile" ? L("Gleiche ab…", "Reconciling…") : L("Neu abgleichen", "Reconcile again")}
          </button>
        )}
        {done && <span className="okfg"> {done}</span>}
      </div>
    </div>
  );
}
