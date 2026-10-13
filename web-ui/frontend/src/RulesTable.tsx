import { useEffect, useMemo, useState } from "react";
import { fetchRules, fetchSystems, Rule, System } from "./api";
import RuleDetailDrawer from "./RuleDetail";
import { ActionBadge } from "./ui";

export default function RulesTable() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [system, setSystem] = useState("");
  const [zone, setZone] = useState("");
  const [action, setAction] = useState("");
  const [onlyFindings, setOnlyFindings] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    fetchSystems()
      .then(setSystems)
      .catch(() => setSystems([]));
  }, []);

  // Freitext-/IP-Suche geht an den Orchestrator (durchsucht auch Endpunkt-Werte).
  useEffect(() => {
    const timer = setTimeout(() => {
      setRules(null);
      setError(null);
      fetchRules(q)
        .then(setRules)
        .catch((e) => setError(String(e)));
    }, 300);
    return () => clearTimeout(timer);
  }, [q]);

  const systemName = (id: number) => systems.find((s) => s.id === id)?.name ?? `#${id}`;

  const zones = useMemo(() => {
    const set = new Set<string>();
    (rules ?? []).forEach((r) => {
      if (r.src_zone) set.add(r.src_zone);
      if (r.dst_zone) set.add(r.dst_zone);
    });
    return [...set].sort();
  }, [rules]);

  const filtered = (rules ?? []).filter(
    (r) =>
      (!system || String(r.source_system_id) === system) &&
      (!action || r.action === action) &&
      (!zone || r.src_zone === zone || r.dst_zone === zone) &&
      (!onlyFindings || r.num_unmatched > 0),
  );

  if (error) return <p className="notice">Regeln konnten nicht geladen werden: {error}</p>;

  return (
    <>
      <div className="filters">
        <input
          placeholder="Suche: Name, IP, Objekt…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select value={system} onChange={(e) => setSystem(e.target.value)}>
          <option value="">Alle Systeme</option>
          {systems.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select value={zone} onChange={(e) => setZone(e.target.value)}>
          <option value="">Alle Zonen</option>
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <select value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Alle Aktionen</option>
          <option value="allow">allow</option>
          <option value="deny">deny</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={onlyFindings}
            onChange={(e) => setOnlyFindings(e.target.checked)}
          />{" "}
          nur mit Findings
        </label>
        <span className="count">{filtered.length} Regeln</span>
      </div>

      {rules === null ? (
        <p className="notice">Lade Regeln…</p>
      ) : (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Aktion</th>
                <th>System</th>
                <th>Quell-Zone</th>
                <th>Ziel-Zone</th>
                <th className="num">Quellen</th>
                <th className="num">Ziele</th>
                <th className="num">Dienste</th>
                <th className="num" title="nicht aufgelöste Objekte">
                  ⚠
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id} onClick={() => setSelected(r.id)}>
                  <td>{r.name}</td>
                  <td>
                    <ActionBadge action={r.action} />
                  </td>
                  <td>{systemName(r.source_system_id)}</td>
                  <td>{r.src_zone ?? <span className="muted">–</span>}</td>
                  <td>{r.dst_zone ?? <span className="muted">–</span>}</td>
                  <td className="num">{r.num_src}</td>
                  <td className="num">{r.num_dst}</td>
                  <td className="num">{r.num_services}</td>
                  <td className="num">
                    {r.num_unmatched > 0 ? (
                      <span className="badge b-red">{r.num_unmatched}</span>
                    ) : (
                      <span className="muted">0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected !== null && (
        <RuleDetailDrawer
          ruleId={selected}
          systemName={systemName}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
