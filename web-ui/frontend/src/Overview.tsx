import { useEffect, useState } from "react";
import { fetchFindings, fetchNat, fetchRules, fetchSystems, Finding, Rule } from "./api";

// Übersicht/Dashboard: KPIs aus den bestehenden Read-Endpunkten abgeleitet
// (W1 - zunächst ohne eigenen Stats-Endpunkt). Respektiert den Mandanten-Switcher.
export default function Overview({
  tenantId,
  onOpenRules,
}: {
  tenantId: number | null;
  onOpenRules: () => void;
}) {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [systemsCount, setSystemsCount] = useState(0);
  const [natCount, setNatCount] = useState(0);

  useEffect(() => {
    fetchRules(undefined, tenantId)
      .then(setRules)
      .catch(() => setRules([]));
    fetchFindings()
      .then(setFindings)
      .catch(() => setFindings([]));
    fetchSystems()
      .then((s) => setSystemsCount(s.length))
      .catch(() => setSystemsCount(0));
    fetchNat()
      .then((n) => setNatCount(n.length))
      .catch(() => setNatCount(0));
  }, [tenantId]);

  const rulesCount = rules?.length ?? 0;
  const unmatched = (rules ?? []).reduce((a, r) => a + r.num_unmatched, 0);
  const openFindings = findings.filter((f) => f.status === "open");

  const byZone = new Map<string, number>();
  (rules ?? []).forEach((r) => {
    const z = r.dst_zone ?? "—";
    byZone.set(z, (byZone.get(z) ?? 0) + 1);
  });
  const zoneRows = [...byZone.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  const zoneMax = zoneRows.reduce((m, [, n]) => Math.max(m, n), 1);

  return (
    <>
      <div className="kpis">
        <button className="kpi" onClick={onOpenRules}>
          <div className="k-label">Firewall-Regeln</div>
          <div className="k-val">{rules === null ? "…" : rulesCount}</div>
          <div className="k-sub">{systemsCount} Quellsystem(e)</div>
        </button>
        <div className="kpi">
          <div className="k-label">NAT / VIPs</div>
          <div className="k-val">{natCount}</div>
          <div className="k-sub">publizierte Dienste</div>
        </div>
        <div className="kpi">
          <div className="k-label">Offene Findings</div>
          <div className={`k-val ${openFindings.length === 0 ? "ok" : "warn"}`}>
            {openFindings.length}
          </div>
          <div className="k-sub">Datenqualität</div>
        </div>
        <div className="kpi">
          <div className="k-label">Nicht aufgelöst</div>
          <div className={`k-val ${unmatched === 0 ? "ok" : "warn"}`}>{unmatched}</div>
          <div className="k-sub">Endpunkte ohne SoT-Treffer</div>
        </div>
      </div>

      <div className="ov-grid">
        <div className="panel ov-card">
          <div className="section-title">Regeln je Ziel-Zone</div>
          {zoneRows.length === 0 ? (
            <p className="notice">Keine Regeln.</p>
          ) : (
            <div className="bars">
              {zoneRows.map(([z, n]) => (
                <div className="bar-row" key={z}>
                  <span className="bar-label" title={z}>
                    {z}
                  </span>
                  <span className="bar-track">
                    <span className="bar-fill" style={{ width: `${(n / zoneMax) * 100}%` }} />
                  </span>
                  <span className="bar-num">{n}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="panel ov-card">
          <div className="section-title">Neueste Findings</div>
          {openFindings.length === 0 ? (
            <p className="notice">Keine offenen Findings 🎉</p>
          ) : (
            <ul className="ov-findings">
              {openFindings.slice(0, 6).map((f) => (
                <li key={f.id}>
                  <code>{f.subject_id}</code>
                  <span className="muted"> · {f.type}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}
