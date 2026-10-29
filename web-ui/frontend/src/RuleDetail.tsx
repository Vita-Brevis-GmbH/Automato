import { ReactNode, useEffect, useState } from "react";
import {
  decidePair,
  Endpoint,
  fetchRuleDetail,
  isCustomerUser,
  NatBrief,
  PairResult,
  rejectParallel,
  resetPairDecision,
  RuleDetail,
  RuleRef,
} from "./api";
import type { View, ViewParams } from "./App";
import { EpgCard } from "./EpgCard";
import { FamilyMarks, RefCard, serviceNames, SideMarks, useVdomShown, VdomChip, VmChip, VmSection } from "./refs";
import { ActionBadge, Dash, Drawer, ErrorBox, fmtTime, Loading, MatchBadge, Note } from "./ui";
import { L } from "./i18n";
import { RuleWegeLink } from "./modules/wege/RuleWegeLink";

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
              {e.vm ? <> <VmChip vm={e.vm} /></> : null}
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
        <EpgCard key={`${e.epg!.dn ?? e.epg!.name}-${i}`} epg={e.epg!} unpaired={e.scope_partners === 0} />
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
                <button
                  className="linkbtn mono objlink"
                  title={L("In NAT / VIPs öffnen", "Open in NAT / VIPs")}
                  onClick={() => onOpen(n.name)}
                >
                  {n.name}
                </button>
              ) : (
                <span className="mono">{n.name}</span>
              )}
              <div>
                <span className={`chip ${n.nat_type === "snat_pool" ? "info" : "local"}`}>
                  {n.nat_type === "snat_pool" ? L("SNAT-Pool", "SNAT pool") : "VIP / DNAT"}
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
                  {(n.balancers ?? []).map((lb) => (
                    <div
                      key={lb.id}
                      className="nat-balancer"
                      title={L(
                        "Ziel der VIP ist ein Virtual Service des NSX ALB – weiter zu dessen Servern",
                        "Target of the VIP is a Virtual Service of the NSX ALB – continues to its servers",
                      )}
                    >
                      <span className="chip info">NSX ALB</span> <span className="mono">{lb.name}</span>
                      {lb.ext_ip ? <span className="mono faint"> {lb.ext_ip}{lb.ext_port ? `:${lb.ext_port}` : ""}</span> : null}
                      {lb.pool ? <span className="faint"> · Pool {lb.pool}</span> : null}
                      {lb.servers && lb.servers.length > 0 && (
                        <div className="mono faint">→ {lb.servers.slice(0, 6).join(", ")}{lb.servers.length > 6 ? ` … (+${lb.servers.length - 6})` : ""}</div>
                      )}
                    </div>
                  ))}
                </>
              ) : (
                <span className="faint">{n.nat_type === "snat_pool" ? L("ausgehende Quell-IP", "outgoing source IP") : "–"}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Paar bestätigen ("ähnlich" → bestätigt), ablehnen ("Kein Paar") oder zurücknehmen
function PairActions({
  detail,
  busy,
  pending,
  error,
  onConfirm,
  onReject,
  onReset,
}: {
  detail: RuleDetail;
  busy: boolean;
  pending: boolean;
  error: unknown;
  onConfirm: (partnerId: number) => void;
  onReject: (partnerId: number) => void;
  onReset: (decisionId: number) => void;
}) {
  const p = detail.pair;
  const decisions = detail.pair_decisions ?? [];
  const confirmed = decisions.find((d) => d.decision === "confirmed" && d.partner_id === p?.rule_id);
  const rejected = decisions.filter((d) => d.decision === "rejected");
  if (!p && !rejected.length && !error) return null;
  return (
    <div style={{ marginTop: 4 }} id="pair-actions">
      <ErrorBox error={error} prefix={L("Paar entscheiden", "Decide pair")} />
      {busy && <div className="faint">{L("wird gespeichert …", "saving …")}</div>}
      {pending && !busy && (
        <div className="note" id="pair-pending">
          {L(
            "Gespeichert – die Markierung folgt, sobald das laufende Einspielen bzw. der Abgleich fertig ist.",
            "Saved – the mark follows as soon as the running import or reconciliation has finished.",
          )}
        </div>
      )}
      {p && p.match === "similar" && (
        <>
          <button
            className="linkbtn"
            disabled={busy}
            title={L(
              "Die beiden Policies decken (trotz Abweichungen) dasselbe ab – gilt auch nach neuen Läufen",
              "Both policies cover the same traffic (despite deviations) – also applies after new runs",
            )}
            onClick={() => onConfirm(p.rule_id)}
          >
            {L("Als Paar bestätigen", "Confirm as pair")}
          </button>{" "}
          ·{" "}
        </>
      )}
      {p && !confirmed && (
        <button
          className="linkbtn"
          disabled={busy}
          title={L("Kein Gegenstück – nicht mehr als Paar vorschlagen", "No counterpart – do not suggest as pair any more")}
          onClick={() => onReject(p.rule_id)}
        >
          {L("Kein Paar", "No pair")}
        </button>
      )}
      {confirmed && (
        <div className="faint">
          {L(
            <>
              bestätigt von {confirmed.decided_by} am {fmtTime(confirmed.decided_at)} ·{" "}
            </>,
            <>
              confirmed by {confirmed.decided_by}, {fmtTime(confirmed.decided_at)} ·{" "}
            </>,
          )}
          <button className="linkbtn" disabled={busy} onClick={() => onReset(confirmed.id)}>
            {L("zurücknehmen", "undo")}
          </button>
        </div>
      )}
      {rejected.map((d) => (
        <div className="faint" key={d.id}>
          {L(
            <>
              kein Paar mit {d.partner_name ? `„${d.partner_name}“` : `Policy ${d.partner_external_id}`}
              {d.partner_id ? ` (#${d.partner_id})` : ""} – von {d.decided_by} am {fmtTime(d.decided_at)} ·{" "}
            </>,
            <>
              no pair with {d.partner_name ? `“${d.partner_name}”` : `policy ${d.partner_external_id}`}
              {d.partner_id ? ` (#${d.partner_id})` : ""} – by {d.decided_by}, {fmtTime(d.decided_at)} ·{" "}
            </>,
          )}
          <button className="linkbtn" disabled={busy} onClick={() => onReset(d.id)}>
            {L("zurücknehmen", "undo")}
          </button>
        </div>
      ))}
    </div>
  );
}

export default function RuleDetailDrawer({
  ruleId,
  systemName,
  onClose,
  onGo,
  onRule,
  onChanged,
  intro,
  focus,
  isAdmin = false,
}: {
  ruleId: number;
  systemName: (id: number) => string;
  onClose: () => void;
  onGo?: (v: View, p?: ViewParams) => void;
  onRule?: (id: number) => void; // andere Regel öffnen (IPv4/IPv6-Gegenstück)
  onChanged?: () => void; // Paar entschieden: Liste neu laden
  intro?: ReactNode; // z. B. Applikations-Doku: warum die Regel für die Applikation gilt
  // Applikations-Doku (0.43.2): nur diese EPGs (DN) zeigen – die eigenen und die Gegenstellen im
  // Scope; ein Contract aus common hat sonst hunderte Consumer mit ihren VMs
  focus?: { dns: string[]; label: string };
  isAdmin?: boolean; // Verbund auflösen
}) {
  const vdomShown = useVdomShown();
  const [showAll, setShowAll] = useState(false);
  const [detail, setDetail] = useState<RuleDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!reload) setDetail(null);
    setError(null);
    setShowAll(false);
    fetchRuleDetail(ruleId)
      .then(setDetail)
      .catch(setError);
  }, [ruleId, reload]);

  // IPv4/IPv6-Paar von Hand entscheiden (operator); gilt sofort und für neue Läufe. 0.113.1 (Anwender: „kann klicken,
  // macht nichts“): während des Speicherns gesperrt mit Hinweis, Fehler beim Knopf; ist der Mandant gerade gesperrt
  // (Einspielen, Abgleich), folgt die Markierung im Hintergrund – das Detail lädt dann ein paar Mal neu
  const [pairBusy, setPairBusy] = useState(false);
  const [pairError, setPairError] = useState<unknown>(null);
  const [pairPending, setPairPending] = useState(0);
  const decide = (action: Promise<PairResult>) => {
    setPairBusy(true);
    setPairError(null);
    action
      .then((r) => {
        setPairPending(r?.pending ? 12 : 0);
        setReload((n) => n + 1);
        onChanged?.();
      }, setPairError)
      .finally(() => setPairBusy(false));
  };
  useEffect(() => {
    if (!pairPending) return;
    const t = window.setTimeout(() => {
      setPairPending((n) => n - 1);
      setReload((n) => n + 1);
      if (pairPending === 1) onChanged?.();
    }, 5000);
    return () => window.clearTimeout(t);
  }, [pairPending]); // eslint-disable-line react-hooks/exhaustive-deps

  const focused = (e: Endpoint) => !focus || showAll || !e.epg?.dn || focus.dns.includes(e.epg.dn);
  const all = detail?.endpoints ?? [];
  const src = all.filter((e) => e.role === "src" && focused(e));
  const dst = all.filter((e) => e.role === "dst" && focused(e));
  const hidden = all.length - src.length - dst.length;
  const refs = (role: string) => detail?.refs?.filter((r) => r.role === role) ?? [];
  const svcRefs = refs("service");
  const aci = detail?.aci ?? null;
  const onObject = onGo ? (name: string) => onGo("regeln", { object: name }) : undefined;
  const onNat = onGo ? (name: string) => onGo("nat", { q: name }) : undefined;
  // VMs hinter den Werten bzw. Endpunkten beider Seiten (vCenter) mit allen Angaben
  const vmItems = (["src", "dst"] as const).flatMap((role) => {
    const label =
      role === "src" ? (aci ? "Consumer" : L("Quelle", "Source")) : aci ? "Provider" : L("Ziel", "Destination");
    const values = refs(role).flatMap((r) => [...r.values, ...r.members.flatMap((m) => m.values)]);
    return [
      ...values.filter((v) => v.vm).map((v) => ({ vm: v.vm!, role: `${label} · ${v.value}` })),
      ...(role === "src" ? src : dst).filter((e) => e.vm).map((e) => ({ vm: e.vm!, role: `${label} · ${e.value ?? ""}` })),
    ];
  });

  return (
    <Drawer
      title={detail?.name ?? L(`Regel #${ruleId}`, `Rule #${ruleId}`)}
      sub={
        detail
          ? `${aci ? L("ACI-Contract", "ACI contract") : L("Regel", "Rule")} #${detail.id} · ${systemName(detail.source_system_id)}` +
            (vdomShown(detail.source_system_id, detail.vdom) ? ` · VDOM ${detail.vdom}` : "")
          : undefined
      }
      onClose={onClose}
    >
      <ErrorBox error={error} />
      {!detail && !error && <Loading />}
      {detail && (
        <>
          {intro}
          <dl className="kv">
            <dt>{L("Aktion", "Action")}</dt>
            <dd>
              <ActionBadge action={detail.action} /> <RuleWegeLink ruleId={detail.id} />
            </dd>
            {vdomShown(detail.source_system_id, detail.vdom) && (
              <>
                <dt>VDOM</dt>
                <dd>
                  <VdomChip vdom={detail.vdom} always /> <span className="faint">{L("Policy-ID", "Policy ID")} {detail.external_id}</span>
                </dd>
              </>
            )}
            {detail.parallel && (
              <>
                <dt>{L("Verbund", "Parallel group")}</dt>
                <dd>
                  <b>{detail.parallel.group}</b>{" "}
                  <span className="faint">
                    {L(
                      "parallele Firewalls – gleiche Policies sind zusammengefasst",
                      "parallel firewalls – identical policies are combined",
                    )}
                  </span>
                  {(detail.twins ?? []).length > 0 && (
                    <div>
                      {L("gleich auf:", "same on:")}{" "}
                      {(detail.twins ?? []).map((t) => (
                        <span key={t.rule_id} className="chip ok" style={{ marginRight: 4 }}>
                          {t.source}
                        </span>
                      ))}
                    </div>
                  )}
                  {(detail.parallel.differs ?? []).length > 0 && (
                    <div className="faint">
                      {L(
                        `abweichend auf ${(detail.parallel.differs ?? []).join(", ")} (siehe Findings)`,
                        `different on ${(detail.parallel.differs ?? []).join(", ")} (see findings)`,
                      )}
                    </div>
                  )}
                  {(detail.parallel.missing ?? []).length > 0 && (
                    <div className="faint">
                      {L(
                        `fehlt auf ${(detail.parallel.missing ?? []).join(", ")}`,
                        `missing on ${(detail.parallel.missing ?? []).join(", ")}`,
                      )}
                    </div>
                  )}
                  {isAdmin && (
                    <div style={{ marginTop: 4 }}>
                      <button
                        className="btn btn-sm"
                        title={L(
                          "Die Firewalls sind nicht parallel: Regeln wieder einzeln, kein neuer Vorschlag",
                          "The firewalls are not parallel: rules separate again, no new suggestion",
                        )}
                        onClick={() => {
                          const msg = L(
                            `Verbund „${detail.parallel!.group}“ auflösen und nicht mehr vorschlagen?`,
                            `Dissolve parallel group “${detail.parallel!.group}” and do not suggest it again?`,
                          );
                          if (!window.confirm(msg)) return;
                          rejectParallel(detail.tenant_id, { group: detail.parallel!.group }).then(() => {
                            setReload((n) => n + 1);
                            onChanged?.();
                          }, setError);
                        }}
                      >
                        {L("Verbund auflösen", "Dissolve parallel group")}
                      </button>
                    </div>
                  )}
                  {detail.parallel.lead && (
                    <div>
                      {L("weicht von", "differs from")}{" "}
                      {onRule && detail.parallel.lead_rule_id ? (
                        <button className="linkbtn" onClick={() => onRule(detail.parallel!.lead_rule_id!)}>
                          {detail.parallel.lead}
                        </button>
                      ) : (
                        detail.parallel.lead
                      )}
                      {L(" ab", "")}: <span className="chip warn">{(detail.parallel.fields ?? []).join(", ")}</span>
                    </div>
                  )}
                </dd>
              </>
            )}
            {/* ACI: eine Zone gibt es nur bei externen EPGs (L3Out) */}
            {!(aci && !detail.src_zone && !detail.dst_zone) && (
              <>
                <dt>{aci ? "L3Out" : L("Zonen", "Zones")}</dt>
                <dd>
                  {detail.src_zone ? <span className="chip">{detail.src_zone}</span> : <Dash />} →{" "}
                  {detail.dst_zone ? <span className="chip">{detail.dst_zone}</span> : <Dash />}
                </dd>
              </>
            )}
            {(detail.family || detail.pair) && (
              <>
                <dt>{L("Familie", "Family")}</dt>
                <dd>
                  <FamilyMarks r={detail} onPair={onRule} />
                  {!isCustomerUser() && (
                    <PairActions
                      detail={detail}
                      busy={pairBusy}
                      pending={pairPending > 0}
                      error={pairError}
                      onConfirm={(id) => decide(decidePair(detail.id, id, "confirmed"))}
                      onReject={(id) => decide(decidePair(detail.id, id, "rejected"))}
                      onReset={(id) => decide(resetPairDecision(id))}
                    />
                  )}
                </dd>
              </>
            )}
            <dt>{L("Dienste", "Services")}</dt>
            <dd>
              {/* Objektname der Konvention (FT_https), die Werte (tcp/443) darunter */}
              <div className="chips">
                {serviceNames(detail).length ? (
                  serviceNames(detail).map((s) => (
                    <span className="chip" key={s} title={detail.services.join(", ")}>
                      {s}
                    </span>
                  ))
                ) : (
                  <Dash />
                )}
              </div>
              {detail.services.length > 0 && serviceNames(detail).join() !== detail.services.join() && (
                <div className="faint mono" style={{ marginTop: 2 }}>
                  {detail.services.join(", ")}
                </div>
              )}
            </dd>
            {aci ? (
              <>
                {aci.kind !== "contract" && (
                  <>
                    <dt>{L("Art", "Type")}</dt>
                    <dd>
                      {aci.kind === "taboo"
                        ? L(
                            "Taboo-Contract – verbietet diesen Verkehr zu den geschützten EPGs (jede Quelle)",
                            "Taboo contract – denies this traffic to the protected EPGs (any source)",
                          )
                        : L(
                            "Preferred Group – alle Mitglieder der VRF sprechen frei miteinander",
                            "Preferred Group – all members of the VRF communicate freely with each other",
                          )}
                    </dd>
                  </>
                )}
                <dt>{aci.kind === "taboo" ? "Taboo" : aci.kind === "preferred_group" ? L("Gruppe", "Group") : "Contract"}</dt>
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
                <dd>
                  {aci.scope ?? <Dash />}
                  {detail.aci_scope && (
                    <div className="faint" id="rule-scope">
                      {L(
                        <>
                          {detail.aci_scope.text} · {detail.aci_scope.pairs} Paar{detail.aci_scope.pairs === 1 ? "" : "e"} Consumer → Provider
                        </>,
                        <>
                          {detail.aci_scope.text} · {detail.aci_scope.pairs} pair{detail.aci_scope.pairs === 1 ? "" : "s"} Consumer → Provider
                        </>,
                      )}
                      {detail.aci_scope.unpaired > 0 && (
                        <span className="chip warn" style={{ marginLeft: 6 }}>
                          {L(
                            `${detail.aci_scope.unpaired} ohne Gegenstelle im Scope`,
                            `${detail.aci_scope.unpaired} without peer in scope`,
                          )}
                        </span>
                      )}
                    </div>
                  )}
                </dd>
                {detail.aci_direction && (
                  <>
                    <dt>{L("Richtung", "Direction")}</dt>
                    <dd id="rule-aci-direction">
                      {detail.aci_direction.both ? (
                        <span className="chip warn">
                          {L(
                            `↔ auch vom Provider aus: ${detail.aci_direction.reverse.join(", ")}`,
                            `↔ also from the provider: ${detail.aci_direction.reverse.join(", ")}`,
                          )}
                        </span>
                      ) : detail.aci_direction.replies_only ? (
                        <span className="chip warn">
                          {detail.aci_direction.reverse.length
                            ? L("← nur Provider → Consumer", "← only Provider → Consumer")
                            : L("nur Antworten", "replies only")}
                        </span>
                      ) : (
                        <span className="chip">→ Consumer → Provider</span>
                      )}
                      <div className="faint">
                        {detail.aci_direction.text}
                        {detail.aci_direction.derived && detail.aci_direction.both
                          ? L(
                              " – aus den Filtern abgeleitet (Standard des APIC; neu einlesen für die Richtung je Subject)",
                              " – derived from the filters (APIC default; collect again for the direction per subject)",
                            )
                          : ""}
                      </div>
                    </dd>
                  </>
                )}
                <dt>DN</dt>
                <dd className="mono">{aci.dn ?? detail.external_id}</dd>
              </>
            ) : (
              <>
                <dt>{L("Schutz", "Protection")}</dt>
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
                <dt>{L("Policy-ID", "Policy ID")}</dt>
                <dd className="mono">{detail.external_id}</dd>
                <dt>NAT</dt>
                <dd>
                  {detail.nat?.snat ? (
                    <span className="chip info">
                      SNAT {detail.nat.pools.length ? detail.nat.pools.join(", ") : L("Interface-IP", "interface IP")}
                    </span>
                  ) : (
                    <span className="faint">{L("kein Source-NAT", "no source NAT")}</span>
                  )}
                  {detail.nat?.vips.length ? <span className="chip local">VIP {detail.nat.vips.join(", ")}</span> : null}
                </dd>
                {detail.comment && (
                  <>
                    <dt>{L("Kommentar", "Comment")}</dt>
                    <dd>{detail.comment}</dd>
                  </>
                )}
              </>
            )}
          </dl>

          {focus && (hidden > 0 || showAll) && (
            <div className="faint" id="rule-focus" style={{ margin: "10px 0 0" }}>
              {showAll
                ? L("Alle Consumer und Provider des Contracts. ", "All consumers and providers of the contract. ")
                : L(
                    `Nur die Beteiligten ${focus.label} – ${hidden} weitere Consumer bzw. Provider des Contracts ausgeblendet. `,
                    `Only the parties ${focus.label} – ${hidden} more consumers or providers of the contract hidden. `,
                  )}
              <button className="linkbtn" onClick={() => setShowAll((v) => !v)}>
                {showAll ? L("nur Beteiligte", "parties only") : L("alle zeigen", "show all")}
              </button>
            </div>
          )}
          {/* ACI: Consumer-EPGs greifen auf die Provider-EPGs zu */}
          <div className="sub-h">
            {aci
              ? aci.kind === "taboo"
                ? L("Quelle", "Source")
                : aci.kind === "preferred_group"
                  ? L("Mitglieder", "Members")
                  : L("Consumer-EPGs", "Consumer EPGs")
              : L("Quellen", "Sources")}{" "}
            ({refs("src").length || detail.num_src})
          </div>
          <SideMarks r={detail} side="src" />
          <Side refs={refs("src")} endpoints={src} onObject={onObject} />

          <div className="sub-h">
            {aci
              ? aci.kind === "taboo"
                ? L("Geschützte EPGs", "Protected EPGs")
                : aci.kind === "preferred_group"
                  ? L("Mitglieder", "Members")
                  : L("Provider-EPGs", "Provider EPGs")
              : L("Ziele", "Destinations")}{" "}
            ({refs("dst").length || detail.num_dst})
          </div>
          <SideMarks r={detail} side="dst" />
          <Side refs={refs("dst")} endpoints={dst} onObject={onObject} />

          <VmSection items={vmItems} />

          {(detail.nat_entries?.length ?? 0) > 0 && (
            <>
              <div className="sub-h">NAT ({detail.nat_entries!.length})</div>
              <NatList items={detail.nat_entries!} onOpen={onNat} />
            </>
          )}

          {svcRefs.length > 0 && (
            <>
              <div className="sub-h">
                {L("Dienste wie auf der Firewall", "Services as on the firewall")} ({svcRefs.length})
              </div>
              {svcRefs.map((r, i) => (
                <RefCard key={`${r.name}-${i}`} r={r} />
              ))}
            </>
          )}

          {dst.some((e) => e.nat_target) && (
            <div style={{ marginTop: 18 }}>
              <Note>
                {L(
                  "VIP/DNAT: die externe IP wird auf das interne Ziel abgebildet (Port-Forward).",
                  "VIP/DNAT: the external IP is mapped to the internal destination (port forward).",
                )}
              </Note>
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
