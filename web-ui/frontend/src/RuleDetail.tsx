import { useEffect, useState } from "react";
import { Endpoint, fetchRuleDetail, RuleDetail } from "./api";
import { ActionBadge, MatchBadge } from "./ui";

function EndpointList({ items }: { items: Endpoint[] }) {
  if (!items.length) return <div className="muted">–</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>Wert / Name</th>
          <th>Art</th>
          <th>Status</th>
          <th>Zone</th>
        </tr>
      </thead>
      <tbody>
        {items.map((e, i) => (
          <tr key={i} style={{ cursor: "default" }}>
            <td>
              <code>{e.value ?? e.name ?? "–"}</code>
              {e.name && e.value && e.name !== e.value ? (
                <div className="muted">{e.name}</div>
              ) : null}
            </td>
            <td>{e.kind}</td>
            <td>
              <MatchBadge status={e.match_status} />
            </td>
            <td>{e.zone ?? <span className="muted">–</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function RuleDetailDrawer({
  ruleId,
  systemName,
  onClose,
}: {
  ruleId: number;
  systemName: (id: number) => string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<RuleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    fetchRuleDetail(ruleId)
      .then(setDetail)
      .catch((e) => setError(String(e)));
  }, [ruleId]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose} aria-label="Schliessen">
          ×
        </button>
        {error && <p className="notice">Fehler: {error}</p>}
        {!detail && !error && <p className="notice">Lade…</p>}
        {detail && (
          <>
            <h2>
              {detail.name} <ActionBadge action={detail.action} />
            </h2>
            <dl className="kv">
              <dt>System</dt>
              <dd>{systemName(detail.source_system_id)}</dd>
              <dt>Domäne</dt>
              <dd>{detail.domain}</dd>
              <dt>Policy-ID</dt>
              <dd>
                <code>{detail.external_id}</code>
              </dd>
              <dt>Quell-Zone</dt>
              <dd>{detail.src_zone ?? "–"}</dd>
              <dt>Ziel-Zone</dt>
              <dd>{detail.dst_zone ?? "–"}</dd>
            </dl>

            <div className="section-title">Quellen ({detail.num_src})</div>
            <EndpointList items={detail.endpoints.filter((e) => e.role === "src")} />

            <div className="section-title">Ziele ({detail.num_dst})</div>
            <EndpointList items={detail.endpoints.filter((e) => e.role === "dst")} />

            <div className="section-title">Dienste</div>
            <div>
              {detail.services.length ? (
                detail.services.map((s) => (
                  <span className="chip" key={s}>
                    {s}
                  </span>
                ))
              ) : (
                <span className="muted">–</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
