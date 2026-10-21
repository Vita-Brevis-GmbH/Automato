import { useEffect, useState } from "react";
import { Endpoint, fetchRuleDetail, NatBrief, RuleDetail, RuleRef } from "./api";
import type { View, ViewParams } from "./App";
import { EpgCard } from "./EpgCard";
import { FamilyMarks, RefCard, SideMarks } from "./refs";
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
function Side({
  refs,
  endpoints,
  onObject,
}: {
  refs: RuleRef[];
  endpoints: Endpoint[];
  onObject?: (name: string) => void;
}) {
  if (refs.length) return <>{refs.map((r, i) => <RefCard key={`${r.name}-${i}`} r={r} onObject={onObject} />)}</>;
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

// VIP (DNAT) bzw. SNAT-Pool der Regel: externe → interne IP mit Ports und DNS-Namen aus dem IPAM
function NatList({ items, onOpen }: { items: NatBrief[]; onOpen?: (name: string) => void }) {
  return (
    <table>
      <tbody>
        {items.map((n) => (
          <tr key={n.id}>
            <td>
              {onOpen ? (
                <button className="linkbtn mono objlink" title="In NAT / VIPs öffnen" onClick={() => onOpen(n.name)}>
                  {n.name}
                </button>
              ) : (
                <span className="mono">{n.name}</span>
              )}
              <div>
                <span className={`chip ${n.nat_type === "snat_pool" ? "info" : "local"}`}>
                  {n.nat_type === "snat_pool" ? "SNAT-Pool" : "VIP / DNAT"}
                </span>
              </div>
            </td>
            <td>
              <span className="mono">
                {n.ext_ip ?? "–"}
                {n.ext_port ? `:${n.ext_port}` : ""}
              </span>
              {(n.external_name || n.ext_dns.length > 0) && (
                <div className="faint">{[n.external_name, ...n.ext_dns].filter((x, i, a) => x && a.indexOf(x) === i).join(", ")}</div>
              )}
            </td>
            <td>
              {n.mapped_ip ? (
                <>
                  <span className="mono">
                    → {n.mapped_ip}
                    {n.mapped_port ? `:${n.mapped_port}` : ""}
                    {n.protocol ? `/${n.protocol}` : ""}
                  </span>
                  {n.mapped_dns.length > 0 && <div className="faint">{n.mapped_dns.join(", ")}</div>}
                </>
              ) : (
                <span className="faint">{n.nat_type === "snat_pool" ? "ausgehende Quell-IP" : "–"}</span>
              )}
            </td>
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
  onGo,
  onRule,
}: {
  ruleId: number;
  systemName: (id: number) => string;
  onClose: () => void;
  onGo?: (v: View, p?: ViewParams) => void;
  onRule?: (id: number) => void; // andere Regel öffnen (IPv4/IPv6-Gegenstück)
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
  const onObject = onGo ? (name: string) => onGo("regeln", { object: name }) : undefined;
  const onNat = onGo ? (name: string) => onGo("nat", { q: name }) : undefined;

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
            {(detail.family || detail.pair) && (
              <>
                <dt>Familie</dt>
                <dd>
                  <FamilyMarks r={detail} onPair={onRule} />
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
                <dt>NAT</dt>
                <dd>
                  {detail.nat?.snat ? (
                    <span className="chip info">
                      SNAT {detail.nat.pools.length ? detail.nat.pools.join(", ") : "Interface-IP"}
                    </span>
                  ) : (
                    <span className="faint">kein Source-NAT</span>
                  )}
                  {detail.nat?.vips.length ? <span className="chip local">VIP {detail.nat.vips.join(", ")}</span> : null}
                </dd>
                {detail.comment && (
                  <>
                    <dt>Kommentar</dt>
                    <dd>{detail.comment}</dd>
                  </>
                )}
              </>
            )}
          </dl>

          {/* ACI: Consumer-EPGs greifen auf die Provider-EPGs zu */}
          <div className="sub-h">
            {aci ? (aci.kind === "taboo" ? "Quelle" : aci.kind === "preferred_group" ? "Mitglieder" : "Consumer-EPGs") : "Quellen"} ({refs("src").length || detail.num_src})
          </div>
          <SideMarks r={detail} side="src" />
          <Side refs={refs("src")} endpoints={src} onObject={onObject} />

          <div className="sub-h">
            {aci ? (aci.kind === "taboo" ? "Geschützte EPGs" : aci.kind === "preferred_group" ? "Mitglieder" : "Provider-EPGs") : "Ziele"} (
            {refs("dst").length || detail.num_dst})
          </div>
          <SideMarks r={detail} side="dst" />
          <Side refs={refs("dst")} endpoints={dst} onObject={onObject} />

          {(detail.nat_entries?.length ?? 0) > 0 && (
            <>
              <div className="sub-h">NAT ({detail.nat_entries!.length})</div>
              <NatList items={detail.nat_entries!} onOpen={onNat} />
            </>
          )}

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
