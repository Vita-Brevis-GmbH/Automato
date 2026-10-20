import { useCallback, useEffect, useRef, useState } from "react";
import { fetchRuns, fetchSystems, Run, System, Tenant } from "./api";
import type { Stats, View } from "./App";
import type { Me } from "./auth";
import CollectDialog from "./CollectDialog";
import { ErrorBox, fmtTime, Icon, Loading, PageHead, RunStatusChip } from "./ui";

// Übersicht/Dashboard: KPIs, letzte Läufe (inkl. "Jetzt einlesen") und Regeln je Ziel-Zone.
// Respektiert den Mandanten-Switcher.
export default function Overview({
  me,
  tenantId,
  tenantLabel,
  tenants,
  stats,
  onGo,
  onReload,
}: {
  me: Me;
  tenantId: number | null;
  tenantLabel: string;
  tenants: Tenant[];
  stats: Stats;
  onGo: (v: View) => void;
  onReload: () => void;
}) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [runsErr, setRunsErr] = useState<unknown>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [collectOpen, setCollectOpen] = useState(false);

  const loadRuns = useCallback(() => {
    fetchRuns(tenantId, 8)
      .then((r) => {
        setRuns(r);
        setRunsErr(null);
      })
      .catch((e) => {
        setRuns([]);
        setRunsErr(e);
      });
  }, [tenantId]);

  useEffect(() => {
    setRuns(null);
    loadRuns();
    fetchSystems()
      .then(setSystems)
      .catch(() => setSystems([]));
  }, [loadRuns]);

  // Laufende/wartende Läufe automatisch nachladen, bis sie fertig sind.
  const pending = (runs ?? []).some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => {
      loadRuns();
    }, 5000);
    return () => clearInterval(t);
  }, [pending, loadRuns]);

  const wasPending = useRef(false);
  useEffect(() => {
    if (wasPending.current && !pending) onReload(); // Lauf fertig -> KPIs/Zähler aktualisieren
    wasPending.current = pending;
  }, [pending, onReload]);

  // Kennzahlen aller Regeln (Firewall-Policies + ACI-Contracts) aus /rules/summary
  const rules = stats.rules;
  const rulesCount = rules?.total ?? 0;
  const fwCount = rules?.by_domain.firewall ?? 0;
  const aciCount = rules?.by_domain.aci_contract ?? 0;
  const endpoints = rules?.endpoints ?? 0;
  const unmatched = rules?.unmatched ?? 0;
  const coverage = endpoints ? Math.floor(((endpoints - unmatched) / endpoints) * 100) : null;
  const openFindings = stats.findings?.open ?? 0;
  const usedSystems = rules?.source_system_ids ?? [];
  const systemName = (id: number | null) =>
    id == null ? "–" : (systems.find((s) => s.id === id)?.name ?? `#${id}`);

  const zoneRows = (rules?.by_dst_zone ?? []).slice(0, 8);
  const zoneMax = zoneRows.reduce((m, [, n]) => Math.max(m, n), 1);
  const val = (n: number) => (rules === null ? "…" : n);

  return (
    <>
      <PageHead
        title="Übersicht"
        actions={
          me.isOperator && (
            <button className="btn btn-primary btn-sm" onClick={() => setCollectOpen(true)}>
              <Icon name="play" size={13} /> Jetzt einlesen
            </button>
          )
        }
      >
        Mandant „{tenantLabel}“ – Stand der letzten Importe.
      </PageHead>

      <ErrorBox error={stats.error} prefix="Daten laden" />

      <div className="kpis">
        <button className="kpi" onClick={() => onGo("regeln")}>
          <div className="k-label">Regeln &amp; Contracts</div>
          <div className="k-val">{val(rulesCount)}</div>
          <div className="k-sub">
            {aciCount > 0 ? `${fwCount} Firewall · ${aciCount} ACI · ` : ""}
            {usedSystems.length} Quellsystem{usedSystems.length === 1 ? "" : "e"}
            {usedSystems.length > 0 && ` · ${usedSystems.map(systemName).join(", ")}`}
          </div>
        </button>
        <button className="kpi" onClick={() => onGo("nat")}>
          <div className="k-label">NAT / VIPs</div>
          <div className="k-val">{stats.nat === null ? "…" : stats.nat}</div>
          <div className="k-sub">publizierte Dienste &amp; SNAT-Pools</div>
        </button>
        <button className="kpi" onClick={() => onGo("findings")}>
          <div className="k-label">Offene Findings</div>
          <div className={`k-val ${openFindings === 0 ? "ok" : "warn"}`}>
            {stats.findings === null ? "…" : openFindings}
          </div>
          <div className="k-sub">
            {openFindings === 0 ? "alle Objekte aufgelöst" : "Datenqualität prüfen"}
          </div>
        </button>
        <div className="kpi">
          <div className="k-label">Abdeckung</div>
          <div className={`k-val ${coverage === 100 ? "ok" : coverage === null ? "" : "warn"}`}>
            {rules === null ? "…" : coverage === null ? "–" : `${coverage}%`}
          </div>
          <div className="k-sub">
            {unmatched > 0 ? `${unmatched} Endpunkte ohne SoT-Treffer` : "Endpunkte gegen die SoT"}
          </div>
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <div className="card-head">
            <h2>Letzte Läufe</h2>
            <div className="spacer" />
            <button className="btn btn-ghost btn-sm" onClick={loadRuns}>
              Aktualisieren
            </button>
            {me.isOperator && (
              <button className="btn btn-ghost btn-sm" onClick={() => setCollectOpen(true)}>
                Jetzt einlesen
              </button>
            )}
          </div>
          <div className="card-body scroll">
            <ErrorBox error={runsErr} prefix="Läufe laden" />
            {runs === null ? (
              <Loading />
            ) : runs.length === 0 ? (
              <div className="empty">
                <div className="big">Noch keine Läufe</div>
                {me.isOperator
                  ? "Mit „Jetzt einlesen“ ein Quellsystem zentral oder über einen Agenten erfassen."
                  : "Ein Operator kann Quellsysteme einlesen."}
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Lauf</th>
                    <th>System</th>
                    <th>Ausführung</th>
                    <th className="num">Regeln</th>
                    <th>Status</th>
                    <th>Zeit</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id} title={r.message ?? undefined}>
                      <td className="mono">#{r.id.slice(0, 8)}</td>
                      <td>{systemName(r.source_system_id)}</td>
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
                        {r.message && (r.status === "failed" || r.platform === "infoblox" || r.platform === "netbox") && (
                          <div
                            className={r.status === "failed" ? "run-msg err" : "run-msg"}
                            title={r.message}
                          >
                            {r.message}
                          </div>
                        )}
                      </td>
                      <td className="mono">{fmtTime(r.finished_at ?? r.queued_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {runs?.some((r) => r.status === "queued" && r.execution === "external") && (
            <div className="card-foot">
              Externe Läufe warten, bis ein Agent sie per <code>--poll</code> abholt.
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Regeln je Zone (Ziel)</h2>
          </div>
          {rules === null ? (
            <Loading />
          ) : zoneRows.length === 0 ? (
            <div className="empty">
              <div className="big">Keine Regeln</div>
              Nach dem ersten Lauf erscheinen hier die Ziel-Zonen.
            </div>
          ) : (
            <>
              <div className="bars">
                {zoneRows.map(([z, n]) => (
                  <div className="bar" key={z} title={`${z}: ${n}`}>
                    <span style={{ height: `${(n / zoneMax) * 100}%` }} />
                    <b style={{ bottom: `calc(${(n / zoneMax) * 100}% + 3px)` }}>{n}</b>
                  </div>
                ))}
              </div>
              <div className="bars-x">
                {zoneRows.map(([z]) => (
                  <span key={z} title={z}>
                    {z}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {collectOpen && (
        <CollectDialog
          tenants={tenants}
          tenantId={tenantId}
          systems={systems}
          isAdmin={me.isAdmin}
          onClose={() => setCollectOpen(false)}
          onStarted={() => {
            loadRuns();
          }}
          onGo={onGo}
        />
      )}
    </>
  );
}
