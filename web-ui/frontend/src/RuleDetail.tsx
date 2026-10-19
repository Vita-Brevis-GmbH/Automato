import { useEffect, useState } from "react";
import { Endpoint, fetchRuleDetail, RuleDetail } from "./api";
import { ActionBadge, Dash, Drawer, ErrorBox, Loading, MatchBadge, Note } from "./ui";

function EndpointList({ items }: { items: Endpoint[] }) {
  if (!items.length) return <Dash />;
  return (
    <table>
      <tbody>
        {items.map((e, i) => (
          <tr key={i}>
            <td>
              <span className="mono">{e.value ?? e.name ?? "–"}</span>
              {e.nat_target ? <span className="muted"> → {e.nat_target}</span> : null}
              {e.name && e.value && e.name !== e.value ? <div className="faint mono">{e.name}</div> : null}
            </td>
            <td>
              <span className="faint">{e.kind}</span>
            </td>
            <td>
              <MatchBadge status={e.match_status} />
            </td>
            <td>{e.zone ? <span className="chip">{e.zone}</span> : <Dash />}</td>
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
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    fetchRuleDetail(ruleId)
      .then(setDetail)
      .catch(setError);
  }, [ruleId]);

  const src = detail?.endpoints.filter((e) => e.role === "src") ?? [];
  const dst = detail?.endpoints.filter((e) => e.role === "dst") ?? [];

  return (
    <Drawer
      title={detail?.name ?? `Regel #${ruleId}`}
      sub={detail ? `Regel #${detail.id} · ${systemName(detail.source_system_id)}` : undefined}
      onClose={onClose}
    >
      <ErrorBox error={error} />
      {!detail && !error && <Loading />}
      {detail && (
        <>
          <dl className="kv">
            <dt>Aktion</dt>
            <dd>
              <ActionBadge action={detail.action} />
            </dd>
            <dt>Zonen</dt>
            <dd>
              {detail.src_zone ? <span className="chip">{detail.src_zone}</span> : <Dash />} →{" "}
              {detail.dst_zone ? <span className="chip">{detail.dst_zone}</span> : <Dash />}
            </dd>
            <dt>Dienste</dt>
            <dd className="chips">
              {detail.services.length ? (
                detail.services.map((s) => (
                  <span className="chip" key={s}>
                    {s}
                  </span>
                ))
              ) : (
                <Dash />
              )}
            </dd>
            <dt>Schutz</dt>
            <dd className="chips">
              {detail.protections?.length ? (
                detail.protections.map((p) => (
                  <span className="chip local" key={p}>
                    {p}
                  </span>
                ))
              ) : (
                <Dash />
              )}
            </dd>
            <dt>Policy-ID</dt>
            <dd className="mono">{detail.external_id}</dd>
            <dt>Domäne</dt>
            <dd>{detail.domain}</dd>
          </dl>

          <div className="sub-h">Quellen ({detail.num_src})</div>
          <EndpointList items={src} />

          <div className="sub-h">Ziele ({detail.num_dst})</div>
          <EndpointList items={dst} />

          {dst.some((e) => e.nat_target) && (
            <div style={{ marginTop: 18 }}>
              <Note>VIP/DNAT: die externe IP wird auf das interne Ziel abgebildet (Port-Forward).</Note>
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
