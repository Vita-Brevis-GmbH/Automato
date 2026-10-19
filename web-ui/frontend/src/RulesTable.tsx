import { useEffect, useMemo, useState } from "react";
import { fetchRules, fetchSystems, Rule, System } from "./api";
import RuleDetailDrawer from "./RuleDetail";
import { ActionBadge, Dash, Empty, ErrorBox, Icon, Loading, PageHead } from "./ui";

function ObjCell({ items }: { items: string[] }) {
  if (!items.length) return <Dash />;
  const shown = items.slice(0, 4);
  return (
    <div className="objs">
      {shown.map((s, i) => (
        <span className="mono" key={i}>
          {s}
        </span>
      ))}
      {items.length > shown.length && <span className="faint">+{items.length - shown.length} weitere</span>}
    </div>
  );
}

function ZoneChip({ zone }: { zone: string | null }) {
  return zone ? <span className="chip">{zone}</span> : <Dash />;
}

export default function RulesTable({
  tenantId,
  tenantLabel,
}: {
  tenantId: number | null;
  tenantLabel: string;
}) {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [error, setError] = useState<unknown>(null);

  const [q, setQ] = useState("");
  const [system, setSystem] = useState("");
  const [srcZone, setSrcZone] = useState("");
  const [dstZone, setDstZone] = useState("");
  const [action, setAction] = useState("");
  const [onlyFindings, setOnlyFindings] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    setSystem("");
    fetchSystems(tenantId)
      .then(setSystems)
      .catch(() => setSystems([]));
  }, [tenantId]);

  // Freitext-/IP-Suche geht an den Orchestrator (durchsucht auch Endpunkt-Werte).
  useEffect(() => {
    const timer = setTimeout(() => {
      setRules(null);
      setError(null);
      fetchRules(q, tenantId)
        .then(setRules)
        .catch((e) => {
          setError(e);
          setRules([]);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [q, tenantId]);

  const systemName = (id: number) => systems.find((s) => s.id === id)?.name ?? `#${id}`;

  const { srcZones, dstZones, actions } = useMemo(() => {
    const s = new Set<string>();
    const d = new Set<string>();
    const a = new Set<string>();
    (rules ?? []).forEach((r) => {
      if (r.src_zone) s.add(r.src_zone);
      if (r.dst_zone) d.add(r.dst_zone);
      a.add(r.action);
    });
    return { srcZones: [...s].sort(), dstZones: [...d].sort(), actions: [...a].sort() };
  }, [rules]);

  const filtered = (rules ?? []).filter(
    (r) =>
      (!system || String(r.source_system_id) === system) &&
      (!action || r.action === action) &&
      (!srcZone || r.src_zone === srcZone) &&
      (!dstZone || r.dst_zone === dstZone) &&
      (!onlyFindings || r.num_unmatched > 0),
  );

  return (
    <>
      <PageHead title="Firewall-Regeln">
        {rules === null
          ? "Lade…"
          : `${rules.length} Regeln für „${tenantLabel}“, read-only eingelesen und gegen die Source of Truth aufgelöst.`}
      </PageHead>

      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder="Suche: Name, IP oder Objekt (z. B. 10.20.4.10)…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select className="sel" value={system} onChange={(e) => setSystem(e.target.value)}>
          <option value="">System: alle</option>
          {systems.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select className="sel" value={srcZone} onChange={(e) => setSrcZone(e.target.value)}>
          <option value="">Quell-Zone: alle</option>
          {srcZones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <select className="sel" value={dstZone} onChange={(e) => setDstZone(e.target.value)}>
          <option value="">Ziel-Zone: alle</option>
          {dstZones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <select className="sel" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Aktion: alle</option>
          {actions.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <button className={`toggle ${onlyFindings ? "on" : ""}`} onClick={() => setOnlyFindings((v) => !v)}>
          <span className="dot y" />
          nur mit Findings
        </button>
        <span className="count">{filtered.length} Treffer</span>
      </div>

      <ErrorBox error={error} prefix="Regeln laden" />

      <div className="card">
        <div className="card-body scroll">
          {rules === null ? (
            <Loading>Lade Regeln…</Loading>
          ) : filtered.length === 0 ? (
            <Empty title={rules.length ? "Keine Treffer" : "Noch keine Regeln"}>
              {rules.length
                ? "Filter oder Suche anpassen."
                : "Sobald ein Quellsystem eingelesen wurde, erscheinen hier die Regeln."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>Aktion</th>
                  <th>System</th>
                  <th>Quell-Zone</th>
                  <th>Ziel-Zone</th>
                  <th>Quelle</th>
                  <th>Ziel</th>
                  <th className="num">Dienste</th>
                  <th title="nicht aufgelöste Objekte"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => setSelected(r.id)}>
                    <td className="mono">{r.id}</td>
                    <td>
                      <b>{r.name}</b>
                      {r.section_label && <div className="faint">{r.section_label}</div>}
                    </td>
                    <td>
                      <ActionBadge action={r.action} />
                    </td>
                    <td>{systemName(r.source_system_id)}</td>
                    <td>
                      <ZoneChip zone={r.src_zone} />
                    </td>
                    <td>
                      <ZoneChip zone={r.dst_zone} />
                    </td>
                    <td>
                      <ObjCell items={r.sources} />
                    </td>
                    <td>
                      <ObjCell items={r.destinations} />
                    </td>
                    <td className="num">{r.num_services}</td>
                    <td>
                      {r.num_unmatched > 0 ? (
                        <span className="chip warn" title="nicht aufgelöste Objekte">
                          {r.num_unmatched}
                        </span>
                      ) : (
                        <span className="chip ok">✓</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {selected !== null && (
        <RuleDetailDrawer ruleId={selected} systemName={systemName} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
