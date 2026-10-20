import { useEffect, useState } from "react";
import { Endpoint, fetchRuleDetail, RuleDetail, RuleRef } from "./api";
import { EpgCard } from "./EpgCard";
import { RefCard } from "./refs";
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

// Verweise wie auf der Firewall (Objektnamen, Gruppen) mit IPAM-Referenzdaten; ältere
// Läufe ohne Verweise zeigen die aufgelösten Endpunkte.
function Side({ refs, endpoints }: { refs: RuleRef[]; endpoints: Endpoint[] }) {
  if (refs.length) return <>{refs.map((r, i) => <RefCard key={`${r.name}-${i}`} r={r} />)}</>;
  // ACI: je Consumer/Provider die Lage der EPG (Tenant, AP, BD, VRF, Subnetze, Endpunkte)
  const located = endpoints.filter((e) => e.epg);
  const rest = endpoints.filter((e) => !e.epg);
  return (
    <>
      {located.map((e, i) => (
        <EpgCard key={`${e.epg!.dn ?? e.epg!.name}-${i}`} epg={e.epg!} />
      ))}
      {(rest.length > 0 || located.length === 0) && <EndpointList items={rest} />}
    </>
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
  const refs = (role: string) => detail?.refs?.filter((r) => r.role === role) ?? [];
  const svcRefs = refs("service");
  const aci = detail?.aci ?? null;

  return (
    <Drawer
      title={detail?.name ?? `Regel #${ruleId}`}
      sub={
        detail
          ? `${aci ? "ACI-Contract" : "Regel"} #${detail.id} · ${systemName(detail.source_system_id)}`
          : undefined
      }
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
            {/* ACI: eine Zone gibt es nur bei externen EPGs (L3Out) */}
            {!(aci && !detail.src_zone && !detail.dst_zone) && (
              <>
                <dt>{aci ? "L3Out" : "Zonen"}</dt>
                <dd>
                  {detail.src_zone ? <span className="chip">{detail.src_zone}</span> : <Dash />} →{" "}
                  {detail.dst_zone ? <span className="chip">{detail.dst_zone}</span> : <Dash />}
                </dd>
              </>
            )}
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
            {aci ? (
              <>
                {aci.kind !== "contract" && (
                  <>
                    <dt>Art</dt>
                    <dd>
                      {aci.kind === "taboo"
                        ? "Taboo-Contract – verbietet diesen Verkehr zu den geschützten EPGs (jede Quelle)"
                        : "Preferred Group – alle Mitglieder der VRF sprechen frei miteinander"}
                    </dd>
                  </>
                )}
                <dt>{aci.kind === "taboo" ? "Taboo" : aci.kind === "preferred_group" ? "Gruppe" : "Contract"}</dt>
                <dd className="mono">{aci.contract ?? <Dash />}</dd>
                {aci.kind !== "preferred_group" && (
                  <>
                    <dt>Subject</dt>
                    <dd className="mono">{aci.subject ?? <Dash />}</dd>
                  </>
                )}
                <dt>ACI-Tenant</dt>
                <dd className="mono">{aci.tenant ?? <Dash />}</dd>
                <dt>Scope</dt>
                <dd>{aci.scope ?? <Dash />}</dd>
                <dt>DN</dt>
                <dd className="mono">{aci.dn ?? detail.external_id}</dd>
              </>
            ) : (
              <>
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
              </>
            )}
          </dl>

          {/* ACI: Consumer-EPGs greifen auf die Provider-EPGs zu */}
          <div className="sub-h">
            {aci ? (aci.kind === "taboo" ? "Quelle" : aci.kind === "preferred_group" ? "Mitglieder" : "Consumer-EPGs") : "Quellen"} ({refs("src").length || detail.num_src})
          </div>
          <Side refs={refs("src")} endpoints={src} />

          <div className="sub-h">
            {aci ? (aci.kind === "taboo" ? "Geschützte EPGs" : aci.kind === "preferred_group" ? "Mitglieder" : "Provider-EPGs") : "Ziele"} (
            {refs("dst").length || detail.num_dst})
          </div>
          <Side refs={refs("dst")} endpoints={dst} />

          {svcRefs.length > 0 && (
            <>
              <div className="sub-h">Dienste wie auf der Firewall ({svcRefs.length})</div>
              {svcRefs.map((r, i) => (
                <RefCard key={`${r.name}-${i}`} r={r} />
              ))}
            </>
          )}

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
