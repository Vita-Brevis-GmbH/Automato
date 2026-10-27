import { useCallback, useEffect, useRef, useState } from "react";
import { cancelRun, fetchOpsHealth, fetchRunsPage, fetchSystems, OpsHealth, Run, System, Tenant } from "./api";
import { HealthChip, HealthTable } from "./Health";
import type { Stats, View, ViewParams } from "./App";
import type { Me } from "./auth";
import CollectDialog, { CollectAllDialog } from "./CollectDialog";
import { DataQualityTile } from "./DataQuality";
import { RunsTable } from "./Runs";
import { ErrorBox, Icon, Loading, PageHead } from "./ui";
import { FoldTitle, useFolds } from "./fold";
import { L } from "./i18n";

const RECENT = 8; // letzte Läufe; offene kommen immer dazu (auch ältere, siehe Läufe-Ansicht)

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
  onGo: (v: View, p?: ViewParams) => void;
  onReload: () => void;
}) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const { fold } = useFolds("automato_overview_folds"); // 0.75.0: Bereiche zuklappbar
  const runsFold = fold("runs");
  const zoneFold = fold("zones");
  const [runsTotal, setRunsTotal] = useState(0);
  const [openTotal, setOpenTotal] = useState(0);
  const [runsErr, setRunsErr] = useState<unknown>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [collectOpen, setCollectOpen] = useState(false);
  const [collectAllOpen, setCollectAllOpen] = useState(false);
  const [health, setHealth] = useState<OpsHealth | null>(null);
  const customer = me.isCustomer; // Kunden-Benutzer: nur die Doku, kein Betrieb (ADR 0018)
  const quality = !customer && tenantId !== null; // Datenqualität (ADR 0029) je Mandant, nicht für Kunden-Benutzer
  useEffect(() => {
    if (customer) return;
    fetchOpsHealth(tenantId)
      .then(setHealth)
      .catch(() => setHealth(null));
  }, [tenantId, runs, customer]);

  // offene Läufe (wartet/läuft) vollständig, dazu die letzten – neueste zuerst. 0.78.5: nie
  // zwei Abfragen gleichzeitig (der Orchestrator ist beim Einspielen grosser Läufe langsam –
  // sich stauende Abfragen belegten die Verbindungen), beim Nachladen bleibt die Liste stehen
  const inFlight = useRef(false);
  const loadRuns = useCallback(
    (poll = false) => {
      if (customer) {
        setRuns([]);
        return;
      }
      if (inFlight.current) return;
      inFlight.current = true;
      Promise.all([
        fetchRunsPage({ tenantId, status: "open", limit: 100 }),
        fetchRunsPage({ tenantId, limit: RECENT }),
      ])
        .then(([open, recent]) => {
          const seen = new Set(open.runs.map((r) => r.id));
          const merged = [...open.runs, ...recent.runs.filter((r) => !seen.has(r.id))];
          merged.sort((a, b) => (b.queued_at ?? "").localeCompare(a.queued_at ?? ""));
          setRuns(merged);
          setRunsTotal(recent.total);
          setOpenTotal(open.total);
          setRunsErr(null);
        })
        .catch((e) => {
          if (!poll) setRuns([]);
          setRunsErr(e);
        })
        .finally(() => {
          inFlight.current = false;
        });
    },
    [tenantId, customer],
  );

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
      loadRuns(true);
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
        title={L("Übersicht", "Overview")}
        actions={
          me.isOperator &&
          !customer && (
            <>
              {tenantId !== null && (
                <button className="btn btn-ghost btn-sm" onClick={() => setCollectAllOpen(true)}>
                  {L("Alle einlesen", "Collect all")}
                </button>
              )}
              <button className="btn btn-primary btn-sm" onClick={() => setCollectOpen(true)}>
                <Icon name="play" size={13} /> {L("Jetzt einlesen", "Collect now")}
              </button>
            </>
          )
        }
      >
        {L(
          <>Mandant „{tenantLabel}“ – Stand der letzten Importe.</>,
          <>Tenant “{tenantLabel}” – state of the latest imports.</>,
        )}
      </PageHead>

      <ErrorBox error={stats.error} prefix={L("Daten laden", "Loading data")} />
      {health && health.problems > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <h2>{L("Betriebszustand", "Health")}</h2>
            <HealthChip health={health} />
          </div>
          <div className="card-body scroll">
            <HealthTable health={health} onlyProblems onGo={onGo} />
          </div>
        </div>
      )}

      <div className={`kpis${quality ? " five" : ""}`}>
        <button className="kpi" onClick={() => onGo("regeln")}>
          <div className="k-label">{L("Regeln & Contracts", "Rules & contracts")}</div>
          <div className="k-val">{val(rulesCount)}</div>
          <div className="k-sub">
            {aciCount > 0 ? `${fwCount} Firewall · ${aciCount} ACI · ` : ""}
            {L(
              `${usedSystems.length} Quellsystem${usedSystems.length === 1 ? "" : "e"}`,
              `${usedSystems.length} source system${usedSystems.length === 1 ? "" : "s"}`,
            )}
            {usedSystems.length > 0 && ` · ${usedSystems.map(systemName).join(", ")}`}
          </div>
        </button>
        <button className="kpi" onClick={() => onGo("nat")}>
          <div className="k-label">NAT / VIPs</div>
          <div className="k-val">{stats.nat === null ? "…" : stats.nat}</div>
          <div className="k-sub">{L("publizierte Dienste & SNAT-Pools", "published services & SNAT pools")}</div>
        </button>
        <button className="kpi" onClick={() => onGo("findings")}>
          <div className="k-label">{L("Offene Findings", "Open findings")}</div>
          <div className={`k-val ${openFindings === 0 ? "ok" : "warn"}`}>
            {stats.findings === null ? "…" : openFindings}
          </div>
          <div className="k-sub">
            {openFindings === 0
              ? L("alle Objekte aufgelöst", "all objects resolved")
              : L("Datenqualität prüfen", "Check data quality")}
          </div>
        </button>
        <div className="kpi">
          <div className="k-label">{L("Abdeckung", "Coverage")}</div>
          <div className={`k-val ${coverage === 100 ? "ok" : coverage === null ? "" : "warn"}`}>
            {rules === null ? "…" : coverage === null ? "–" : `${coverage}%`}
          </div>
          <div className="k-sub">
            {unmatched > 0
              ? L(`${unmatched} Endpunkte ohne SoT-Treffer`, `${unmatched} endpoints without SoT match`)
              : L("Endpunkte gegen die SoT", "Endpoints against the SoT")}
          </div>
        </div>
        {quality && tenantId !== null && (
          // nach einem Lauf (Zähler neu) auch die Gesamtnote neu
          <DataQualityTile key={`${tenantId}-${stats.findings?.total}`} tenantId={tenantId} onGo={onGo} />
        )}
      </div>

      <div className="two-col">
        {!customer && (
          <div className="card">
            <div className="card-head">
              <FoldTitle fold={runsFold}>{L("Letzte Läufe", "Recent runs")}</FoldTitle>
              {openTotal > 0 && (
                <span className="chip info" title={L("wartet oder läuft", "waiting or running")}>
                  {L(`${openTotal} offen`, `${openTotal} open`)}
                </span>
              )}
              <div className="spacer" />
              <button className="btn btn-ghost btn-sm" onClick={() => loadRuns()}>
                {L("Aktualisieren", "Refresh")}
              </button>
              {me.isOperator && (
                <button className="btn btn-ghost btn-sm" onClick={() => setCollectOpen(true)}>
                  {L("Jetzt einlesen", "Collect now")}
                </button>
              )}
            </div>
            {runsFold.open && (
            <>
            <div className="card-body scroll tall">
              <ErrorBox error={runsErr} prefix={L("Läufe laden", "Loading runs")} />
              {runs === null ? (
                <Loading />
              ) : runs.length === 0 ? (
                <div className="empty">
                  <div className="big">{L("Noch keine Läufe", "No runs yet")}</div>
                  {me.isOperator
                    ? L(
                        "Mit „Jetzt einlesen“ ein Quellsystem zentral oder über einen Agenten erfassen.",
                        "Use “Collect now” to collect a source system centrally or via an agent.",
                      )
                    : L("Ein Operator kann Quellsysteme einlesen.", "An operator can collect source systems.")}
                </div>
              ) : (
                <RunsTable
                  runs={runs}
                  systemName={systemName}
                  canCancel={me.isOperator}
                  onCancel={(id) => cancelRun(id).then(() => loadRuns(), setRunsErr)}
                />
              )}
            </div>
            {runsTotal > 0 && (
              <div className="card-foot">
                <button className="linkbtn" onClick={() => onGo("laeufe")}>
                  {L(`Alle Läufe (${runsTotal}) →`, `All runs (${runsTotal}) →`)}
                </button>
                {openTotal > 100 &&
                  L(
                    ` · nur die neuesten 100 offenen hier, alle in der Ansicht „Läufe“`,
                    ` · only the newest 100 open ones here, all in the “Runs” view`,
                  )}
              </div>
            )}
            {runs?.some((r) => r.status === "queued" && r.execution === "external") && (
              <div className="card-foot">
                {L(
                  <>
                    Externe Läufe warten, bis ein Agent sie per <code>--poll</code> abholt.
                  </>,
                  <>
                    External runs wait until an agent picks them up via <code>--poll</code>.
                  </>,
                )}
              </div>
            )}
            </>
            )}
          </div>
        )}

        <div className="card">
          <div className="card-head">
            <FoldTitle fold={zoneFold}>{L("Regeln je Zone (Ziel)", "Rules per zone (destination)")}</FoldTitle>
          </div>
          {!zoneFold.open ? null : rules === null ? (
            <Loading />
          ) : zoneRows.length === 0 ? (
            <div className="empty">
              <div className="big">{L("Keine Regeln", "No rules")}</div>
              {L(
                "Nach dem ersten Lauf erscheinen hier die Ziel-Zonen.",
                "The destination zones appear here after the first run.",
              )}
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

      {collectAllOpen && tenantId !== null && (
        <CollectAllDialog
          tenantId={tenantId}
          tenantLabel={tenantLabel}
          systems={systems}
          onClose={() => setCollectAllOpen(false)}
          onStarted={() => loadRuns()}
        />
      )}
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
