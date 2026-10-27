// Weg-Suche (0.35.0, ADR 0019 Stufe 3): eigene Ansicht statt Reiter im Routing. Der Weg einer
// Verbindung Hop für Hop ab Quelle bzw. Startpunkt – je Firewall die Policies, je ACI-VRF die
// Contracts zwischen den EPGs (Fehlersuche, Kontrolle von Arbeiten). Quelle und Ziel als IP
// oder Name/FQDN (IPAM, Objekte der Firewalls, VMs), der Dienst als tcp/443 oder Objektname.
// Seit 0.57.0 die logischen Zonen je Hop, die Übergänge dazwischen und die Absicht zwischen
// Quell- und Zielzone (auch je Dienst) mit Urteil. Seit ADR 0032 auch durch gelesene Router bis
// an die Firmengrenze (am letzten Hop: eBGP zu fremdem AS, Anbindung, Default-Route zum Provider).
import { useEffect, useRef, useState } from "react";
import {
  fetchRoutingSummary,
  HopRule,
  HopRules,
  L2Location,
  L2Segment,
  lookupRoute,
  NameHit,
  NameResult,
  resolveName,
  RouteLookup,
  RoutingNode,
  snatText,
  TracePath,
  TraceResult,
  traceRoute,
  TraceZone,
  TraceZoneStep,
} from "./api";
import type { ViewParams } from "./App";
import { useDebounced } from "./Objects";
import { Capture, CAPTURE_KEY, Hops, matchText, Missing, policyText, Port, VrfChip, where } from "./Routing";
import { Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";
import { FoldTitle, useFolds } from "./fold";
import { L } from "./i18n";

const END: Record<TracePath["end"]["kind"], [string, string]> = {
  connected: ["ok", L("Ziel erreicht", "Destination reached")],
  fabric: ["ok", L("im Fabric", "in the fabric")],
  exit: ["info", L("Anbindung", "Exit")],
  external: ["info", L("extern", "external")],
  unknown: ["warn", L("Weg endet", "Path ends")],
  no_route: ["warn", L("keine Route", "no route")],
  loop: ["crit", L("Schleife", "Loop")],
  max_hops: ["warn", L("abgebrochen", "aborted")],
};

// Firmengrenze (ADR 0032): hier verlässt der Weg die Firma – Grund und AS am Chip
function BoundaryChip({ b, id }: { b: TracePath["boundary"]; id?: string }) {
  if (!b) return null;
  return (
    <span className="chip boundary trace-boundary" id={id} title={b.text}>
      {L("Firmengrenze", "Company boundary")}
      {b.as ? ` · AS ${b.as}` : ""}
      {b.private_as ? L(" (privat)", " (private)") : ""}
    </span>
  );
}

// Titel eines Wegs: aktive Wege „Weg 1 von 2“, Backup-Wege „Backup-Weg 1“
function pathTitle(paths: TracePath[], p: TracePath): string {
  const same = paths.filter((x) => (x.kind ?? "active") === (p.kind ?? "active"));
  const n = same.indexOf(p) + 1;
  if (p.kind === "backup") return L(`Backup-Weg ${n}`, `Backup path ${n}`);
  return L(`Weg ${n}${same.length > 1 ? ` von ${same.length}` : ""}`, `Path ${n}${same.length > 1 ? ` of ${same.length}` : ""}`);
}

// Art eines Wegs in der Übersicht: aktiv, ECMP, redundant über einen Verbund, Backup
function PathKind({ path, activeCount }: { path: TracePath; activeCount: number }) {
  if (path.kind === "backup")
    return (
      <span
        className="chip warn"
        title={L(
          `weicht bei ${path.deviation?.label} ab: ${path.deviation?.reason}`,
          `deviates at ${path.deviation?.label}: ${path.deviation?.reason}`,
        )}
      >
        {L("Backup ab", "Backup from")} {path.deviation?.label}
      </span>
    );
  if (path.parallel?.length)
    return (
      <span
        className="chip info"
        title={L(
          "die Firewalls des Verbunds (z. B. Session-Sync-Paar) sind ein Hop",
          "the firewalls of the parallel group (e.g. session sync pair) are one hop",
        )}
      >
        {L("redundant · Verbund", "redundant · parallel group")} {path.parallel.join(", ")}
      </span>
    );
  return activeCount > 1 ? (
    <span
      className="chip info"
      title={L(
        "Equal-Cost-Multipath: der Verkehr verteilt sich auf alle Wege",
        "Equal-cost multipath: traffic is spread across all paths",
      )}
    >
      {L("aktiv · ECMP", "active · ECMP")}
    </span>
  ) : (
    <span className="chip ok">{L("aktiv", "active")}</span>
  );
}

// Regeln auf dem Weg (0.22.0): Urteil je FortiGate-Hop bzw. je Weg
export const VERDICT: Record<string, [string, string]> = {
  allow: ["ok", L("erlaubt", "allowed")],
  deny: ["crit", L("gesperrt", "blocked")],
  implicit_deny: ["crit", L("implizit gesperrt", "implicitly denied")],
  depends: ["warn", L("abhängig", "depends")],
  unknown: ["", L("unbekannt", "unknown")],
};

export function Verdict({ v, title }: { v: string; title?: string }) {
  const [cls, label] = VERDICT[v] ?? ["", v];
  return (
    <span className={`chip ${cls}`} title={title}>
      {label}
    </span>
  );
}

// Quelle/Ziel einer FortiGate-Policy: über welches Objekt sie passen (0.78.4)
function matchedVia(p: HopRule): string {
  if (p.kind) return p.src && p.src !== "direkt" ? L(` · über ${p.src}`, ` · via ${p.src}`) : "";
  const parts = [p.src && L(`Quelle ${p.src}`, `Source ${p.src}`), p.dst && L(`Ziel ${p.dst}`, `Destination ${p.dst}`)].filter(
    Boolean,
  );
  return parts.length ? ` · ${parts.join(", ")}` : "";
}

function HopRuleLine({ p }: { p: HopRule }) {
  return (
    <span className={p.decides ? undefined : "faint"}>
      <a href={`#regeln?rule=${p.rule_id}`} title={L("Regel öffnen", "Open rule")}>
        {p.label ?? `Policy ${p.external_id}`}
      </a>
      {!p.label && p.name ? ` „${p.name}“` : ""} · {p.action === "allow" ? L("erlauben", "accept") : L("sperren", "deny")}
      {matchedVia(p)}
      {p.services.length ? ` · ${p.services.join(", ")}` : ""}
      {p.unsure.length ? L(` · möglich (${p.unsure.join(", ")})`, ` · possible (${p.unsure.join(", ")})`) : ""}
      {p.note ? (
        <span className="aci-note">
          {L(" · greift nicht: ", " · does not apply: ")}
          {p.note}
        </span>
      ) : null}
      {p.reverse && (
        <>
          {" "}
          <span
            className="chip warn aci-reverse"
            title={L(
              "ACI ist zustandslos: Filter ohne Ports gelten auch vom Provider zum Consumer",
              "ACI is stateless: filters without ports also apply from provider to consumer",
            )}
          >
            {L("↩ Rückrichtung", "↩ Reverse direction")}
          </span>
        </>
      )}
    </span>
  );
}

// Regeln eines Hops: FortiGate die entscheidende Policy und die möglichen davor (mit dem, was
// nicht prüfbar ist), seit 0.78.4 auch die passenden dahinter (nicht erreicht); ACI-VRF (0.35.0)
// die Zuordnung von Quelle und Ziel und die Contracts
function HopRulesCell({ r }: { r?: HopRules }) {
  if (!r) return <Dash />;
  return (
    <div>
      <Verdict v={r.verdict} title={r.text} />
      {(r.src_epg || r.dst_epg) && (
        <div
          className="faint"
          style={{ marginTop: 2 }}
          title={L(
            "Zuordnung im ACI: EPG im Fabric bzw. externe EPG (längstes Subnetz)",
            "Assignment in the ACI: EPG in the fabric or external EPG (longest subnet)",
          )}
        >
          {r.src_epg || "?"} → {r.dst_epg || "?"}
        </div>
      )}
      <div className="objs" style={{ marginTop: 2 }}>
        {r.rules.map((p) => (
          <HopRuleLine key={p.rule_id} p={p} />
        ))}
      </div>
      {r.after && r.after.length > 0 && (
        <div
          className="hop-after"
          title={L(
            "Diese Policies passen auch, liegen aber hinter der entscheidenden – die Firewall erreicht sie für diesen Verkehr nicht",
            "These policies also match but come after the deciding one – the firewall does not reach them for this traffic",
          )}
        >
          <div className="faint">{L("dahinter, nicht erreicht:", "after it, not reached:")}</div>
          <div className="objs">
            {r.after.map((p) => (
              <HopRuleLine key={p.rule_id} p={p} />
            ))}
          </div>
        </div>
      )}
      {r.rules.length === 0 && <div className="faint">{r.text}</div>}
    </div>
  );
}

// Zonen (0.57.0): Zone als Chip in ihrer Farbe, Kette des Wegs mit den Übergängen
function ZoneChip({ z }: { z?: TraceZone | null }) {
  if (!z) return null;
  return (
    <span className="chip zone-chip" title={z.code ? `${z.name} (${z.code})` : z.name}>
      <span className="zone-swatch" style={{ background: z.color ?? "var(--surface-3)" }} /> {z.name}
    </span>
  );
}

function ZoneChain({ steps }: { steps?: TraceZoneStep[] }) {
  if (!steps || steps.length === 0) return <Dash />;
  return (
    <span className="zone-chain">
      {steps.map((z, i) => (
        <span key={i}>
          {z.transition && (
            <span className="faint mono" title={L("Übergang zwischen den Zonen", "Transition between the zones")}>
              {" "}
              →{z.transition}→{" "}
            </span>
          )}
          <span
            title={
              z.hops.length
                ? z.hops.join(", ")
                : z.role === "src"
                  ? L("Quelle", "Source")
                  : z.role === "dst"
                    ? L("Ziel", "Destination")
                    : undefined
            }
          >
            <ZoneChip z={z} />
          </span>
        </span>
      ))}
    </span>
  );
}

const ZONE_VERDICT: Record<string, string> = { ok: "ok", violation: "crit", warn: "warn", info: "" };
const INTENT_LABEL: Record<string, string> = {
  allow: L("erlaubt", "allow"),
  limited: L("eingeschränkt", "limited"),
  deny: L("gesperrt", "deny"),
};

// Quell- und Zielzone mit der Absicht zwischen ihnen (je Dienst) und dem Urteil gegenüber dem Weg
function ZoneIntentCard({ trace }: { trace: TraceResult }) {
  const f = useFolds("automato_paths_zones").fold("open"); // 0.75.0: zuklappbar
  const z = trace.zones;
  if (!z) return null;
  const first = trace.paths.find((p) => p.kind !== "backup");
  return (
    <div className="card" id="path-zones">
      <div className="card-head">
        <FoldTitle fold={f}>{L("Zonen", "Zones")}</FoldTitle>
        <span className="faint">
          {L("Quelle", "Source")} {z.src.length ? z.src.map((x) => x.name).join(", ") : L("ohne Zone", "no zone")} →{" "}
          {L("Ziel", "Destination")} {z.dst.length ? z.dst.map((x) => x.name).join(", ") : L("ohne Zone", "no zone")}
        </span>
      </div>
      {f.open && (
      <div className="card-body pad">
        {first?.zones && first.zones.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <ZoneChain steps={first.zones} />
          </div>
        )}
        {z.intents.length === 0 ? (
          <span className="faint">
            {L("keine Absicht für dieses Zonenpaar in der Zonenmatrix", "no intent for this zone pair in the zone matrix")}
          </span>
        ) : (
          <table className="compact" id="path-zone-intents">
            <tbody>
              {z.intents.map((x) => (
                <tr key={`${x.src}>${x.dst}`}>
                  <td className="nowrap">
                    {x.src_name} → {x.dst_name}
                  </td>
                  <td className="nowrap">
                    {L("Absicht", "Intent")} {INTENT_LABEL[x.intent] ?? x.intent}
                    {x.services.length > 0 && (
                      <span className="faint">
                        {L(" · nur ", " · only ")}
                        {x.services.join(", ")}
                      </span>
                    )}
                  </td>
                  <td>
                    <span className={`chip ${ZONE_VERDICT[x.verdict] ?? ""}`}>{x.text}</span>
                    {x.note && <div className="faint">{x.note}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      )}
    </div>
  );
}

// Länge im Vergleich zum kürzesten Weg mit Ziel: „gleich lang“ bzw. „+1 Hop“
function Length({ extra }: { extra?: number | null }) {
  if (extra == null)
    return (
      <span className="faint" title={L("Weg ohne Ziel", "Path without destination")}>
        –
      </span>
    );
  return extra === 0 ? (
    <span className="chip ok" title={L("so lang wie der kürzeste Weg", "as long as the shortest path")}>
      {L("gleich lang", "same length")}
    </span>
  ) : (
    <span className="chip warn" title={L("Hops mehr als der kürzeste Weg", "hops more than the shortest path")}>
      {L(`+${extra} Hop${extra > 1 ? "s" : ""}`, `+${extra} hop${extra > 1 ? "s" : ""}`)}
    </span>
  );
}

// Übersicht aller Wege: Art, Knoten nacheinander, Länge (Hops, im Vergleich), Ende
function PathOverview({ trace }: { trace: TraceResult }) {
  const active = trace.paths.filter((p) => p.kind !== "backup");
  return (
    <div className="card">
      <div className="card-head">
        <h2>{L("Wege im Überblick", "Paths at a glance")}</h2>
        <span className="faint">
          {active.length} {L("aktiv", "active")}
          {active.length > 1 ? " (redundant)" : ""}
          {trace.paths.length > active.length
            ? L(
                ` · ${trace.paths.length - active.length} Backup-Weg${trace.paths.length - active.length > 1 ? "e" : ""}`,
                ` · ${trace.paths.length - active.length} backup path${trace.paths.length - active.length > 1 ? "s" : ""}`,
              )
            : ""}{" "}
          {L("– Backup-Wege mit gleicher Länge", "– backup paths of equal length")}
          {trace.slack
            ? L(
                ` bzw. bis ${trace.slack} Hop${trace.slack > 1 ? "s" : ""} mehr`,
                ` or up to ${trace.slack} hop${trace.slack > 1 ? "s" : ""} more`,
              )
            : ""}
        </span>
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>{L("Weg", "Path")}</th>
              <th>{L("Art", "Type")}</th>
              <th>{L("Knoten", "Nodes")}</th>
              <th>Next-Hops</th>
              <th className="num">Hops</th>
              <th>{L("Länge", "Length")}</th>
              <th>{L("Regeln", "Rules")}</th>
              {trace.zones && <th>{L("Zonen", "Zones")}</th>}
              <th>{L("Ende", "End")}</th>
            </tr>
          </thead>
          <tbody>
            {trace.paths.map((p, i) => {
              const [cls, label] = END[p.end.kind] ?? ["", p.end.kind];
              return (
                <tr key={i}>
                  <td className="nowrap">
                    <button
                      className="linkbtn"
                      onClick={() => document.getElementById(`trace-${i}`)?.scrollIntoView({ behavior: "smooth" })}
                    >
                      {pathTitle(trace.paths, p)}
                    </button>
                  </td>
                  <td>
                    <PathKind path={p} activeCount={active.length} />
                    {p.kind === "backup" && <div className="faint">{p.deviation?.reason}</div>}
                  </td>
                  <td>
                    {p.hops.map((h, j) => (
                      <span key={j}>
                        {j > 0 && <span className="faint"> → </span>}
                        <span
                          className={p.deviation && p.deviation.hop === j ? "chip warn" : undefined}
                          title={h.parts ? [...new Set(h.parts.map((x) => x.label))].join(", ") : undefined}
                        >
                          {h.label}
                        </span>
                        {h.nat && (
                          <span className="chip info" style={{ marginLeft: 4 }} title={`VIP → ${h.nat.to.join(", ")}`}>
                            DNAT
                          </span>
                        )}
                        {h.lb && (
                          <span
                            className="chip info"
                            style={{ marginLeft: 4 }}
                            title={`Load Balancer ${h.lb.name}: ${h.lb.ext} → ${(h.lb.to ?? []).join(", ")}`}
                          >
                            LB
                          </span>
                        )}
                        {h.leak && (
                          <span className="chip local" style={{ marginLeft: 4 }} title={h.leak.text}>
                            → VRF {h.leak.vrf}
                          </span>
                        )}
                      </span>
                    ))}
                  </td>
                  <td className="mono">
                    {p.hops
                      .map((h) => [...new Set((h.parts ?? [h]).map((x) => x.egress?.next_hop).filter(Boolean))].join(" | "))
                      .filter(Boolean)
                      .join(" → ") || <Dash />}
                  </td>
                  <td className="num">{p.hops.length}</td>
                  <td>
                    <Length extra={p.extra} />
                  </td>
                  <td>{p.policy ? <Verdict v={p.policy.verdict} title={p.policy.text} /> : <Dash />}</td>
                  {trace.zones && (
                    <td>
                      <ZoneChain steps={p.zones} />
                    </td>
                  )}
                  <td>
                    <span className={`chip ${cls}`}>{label}</span>
                    <BoundaryChip b={p.boundary} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// L2 (0.64.0): Switches zwischen zwei Hops bzw. bis zum Anschluss des Ziels (aus CDP/LLDP und MAC-Tabelle)
function L2Line({ seg, prefix = "L2" }: { seg: L2Segment; prefix?: string }) {
  return (
    <div
      className="faint l2-seg"
      title={L(
        "Weg über die Switches (CDP/LLDP), im VLAN des Ausgangs",
        "Path via the switches (CDP/LLDP), in the VLAN of the outgoing interface",
      )}
    >
      {prefix}: {seg.text}
      {seg.vlan ? ` · VLAN ${seg.vlan}` : ""}
    </div>
  );
}

function L2Ends({ trace }: { trace: TraceResult }) {
  const f = useFolds("automato_paths_l2").fold("open"); // 0.75.0: zuklappbar
  const ends = trace.l2;
  const src = trace.paths.find((p) => p.l2_src)?.l2_src;
  if (!ends?.src && !ends?.dst && !src) return null;
  const where = (l: L2Location) =>
    `${l.device} ${l.port}${l.vlan ? ` (VLAN ${l.vlan})` : ""}${l.via ? ` ${L("über", "via")} ${l.via.device}${l.via.port ? ` ${l.via.port}` : ""}` : ""}`;
  return (
    <div className="card" id="trace-l2">
      <div className="card-head">
        <FoldTitle fold={f}>{L("Anschluss (L2)", "Access port (L2)")}</FoldTitle>
      </div>
      {f.open && (
      <div className="card-body pad">
        {ends?.src && (
          <div id="trace-l2-src">
            {L(
              <>
                Quelle hängt an <b>{ends.src.map(where).join(" bzw. ")}</b>
              </>,
              <>
                Source is connected to <b>{ends.src.map(where).join(" or ")}</b>
              </>,
            )}
            {ends.src[0].vm ? <span className="faint"> · VM {ends.src[0].vm.name}</span> : null}
          </div>
        )}
        {src && <L2Line seg={src} prefix={L("Weg zum ersten Hop", "Path to the first hop")} />}
        {ends?.dst && (
          <div id="trace-l2-dst">
            {L(
              <>
                Ziel hängt an <b>{ends.dst.map(where).join(" bzw. ")}</b>
              </>,
              <>
                Destination is connected to <b>{ends.dst.map(where).join(" or ")}</b>
              </>,
            )}
            {ends.dst[0].vm ? <span className="faint"> · VM {ends.dst[0].vm.name}</span> : null}
          </div>
        )}
      </div>
      )}
    </div>
  );
}

// Ein Weg Hop für Hop: Knoten, Eingang, Entscheidung (Policy-Route bzw. Route), Ausgang
function TraceCard({
  path,
  index,
  title,
  compare,
  onCapture,
}: {
  path: TracePath;
  index: number;
  title: string;
  compare?: boolean; // mehrere Wege: Länge im Vergleich zeigen
  onCapture?: (c: Capture) => void;
}) {
  const [cls, label] = END[path.end.kind] ?? ["", path.end.kind];
  const last = path.hops[path.hops.length - 1];
  const out = last?.egress;
  // Weg endet an einem Next-Hop ohne gelesenes Gerät: als Anbindung erfassen (Internet, MPLS, …)
  const capturable = onCapture && out?.next_hop && (path.end.kind === "unknown" || path.end.kind === "external");
  return (
    <div className="card" id={`trace-${index}`}>
      <div className="card-head">
        <h2>{title}</h2>
        <span className={`chip ${cls}`}>{label}</span>
        <BoundaryChip b={path.boundary} id={`path-boundary-${index}`} />
        {path.kind === "backup" && path.deviation && (
          <span className="chip warn" title={L("Weg, wenn der aktive Next-Hop ausfällt", "Path if the active next-hop fails")}>
            {L("Backup ab", "Backup from")} {path.deviation.label}: {path.deviation.reason}
          </span>
        )}
        {path.parallel?.length ? (
          <span className="chip info">
            {L("redundant · Verbund", "redundant · parallel group")} {path.parallel.join(", ")}
          </span>
        ) : null}
        {compare && path.extra != null && <Length extra={path.extra} />}
        {path.zones && path.zones.length > 0 && <ZoneChain steps={path.zones} />}
        {path.policy && (
          <span className={`chip ${VERDICT[path.policy.verdict]?.[0] ?? ""}`} title={L("Regeln auf dem Weg", "Rules on the path")}>
            {L("Regeln:", "Rules:")} {path.policy.text}
          </span>
        )}
        <span className="faint">
          {path.end.text}
          {path.end.also?.length ? L(` bzw. ${path.end.also.join(" bzw. ")}`, ` or ${path.end.also.join(" or ")}`) : ""}
        </span>
        {capturable && (
          <button
            className="btn btn-sm"
            style={{ marginLeft: "auto" }}
            title={L(
              "Anbindung nach aussen erfassen – mit allen erkannten Routern und Firewalls dieser Anbindung; der Weg endet dann dort bzw. geht nach manuellen Routen weiter",
              "Capture exit to the outside – with all detected routers and firewalls of this exit; the path then ends there or continues along manual routes",
            )}
            onClick={() =>
              onCapture({
                kind: path.end.kind === "external" ? "internet" : "wan",
                source_system_id: last.source_system_id,
                interface: out?.interface ?? null,
                next_hop: out!.next_hop!,
              })
            }
          >
            {L("Als Anbindung erfassen", "Capture as exit")}
          </button>
        )}
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>{L("Knoten", "Node")}</th>
              <th>{L("Eingang", "Incoming")}</th>
              <th>{L("Entscheidung", "Decision")}</th>
              <th>{L("Ausgang · Next-Hop", "Outgoing · next-hop")}</th>
              <th>{L("Regeln", "Rules")}</th>
            </tr>
          </thead>
          <tbody>
            {path.hops.map((hop, i) => {
              // Verbund als ein Hop (0.29.0): eine Zeile je Firewall bzw. Variante
              const parts = hop.parts ?? [hop];
              return parts.map((h, k) => {
                const possible = (h.policy_routes ?? []).filter((p) => !p.certain);
                return (
                  <tr key={`${i}-${k}`}>
                    {k === 0 && (
                      <td className="num" rowSpan={parts.length}>
                        {i + 1}
                      </td>
                    )}
                    <td>
                      {k === 0 && hop.parts && hop.label !== h.label && (
                        <div>
                          <span
                            className="chip info"
                            title={L(
                              "Firewalls desselben Verbunds (z. B. Session-Sync-Paar) – ein Hop",
                              "Firewalls of the same parallel group (e.g. session sync pair) – one hop",
                            )}
                          >
                            {hop.label}
                          </span>
                        </div>
                      )}
                      <b>{h.label}</b> {h.kind === "firewall" && <span className="chip">Firewall</span>}
                      {h.kind === "l3" && <span className="chip">Router</span>}
                      {h.boundary && k === 0 && (
                        <span className="chip boundary" title={path.boundary?.text}>
                          {L("Firmengrenze", "Company boundary")}
                        </span>
                      )}
                      <VrfChip vrf={h.vrf} />
                      {k === 0 && hop.zone && (
                        <div>
                          <ZoneChip z={hop.zone} />
                        </div>
                      )}
                      {h.dst && (
                        <div className="faint" title={L("Ziel nach NAT auf einem Hop davor", "Destination after NAT on a previous hop")}>
                          {L("Ziel", "Destination")} <span className="mono">{h.dst}</span>
                        </div>
                      )}
                      <div className="faint">
                        {h.via}
                        {h.kind === "vrf" && h.members.length ? ` · ${h.members.join(", ")}` : ""}
                      </div>
                      <Missing items={h.missing} />
                    </td>
                    <td>{h.ingress?.interface ? <Port p={h.ingress} /> : <Dash />}</td>
                    <td>
                      {h.leak && (
                        <div style={{ marginBottom: 4 }} className="hop-leak">
                          <span
                            className="chip local"
                            title={L(
                              "Ziel in einer anderen VRF – das Fabric leakt es in diese VRF",
                              "Destination in another VRF – the fabric leaks it into this VRF",
                            )}
                          >
                            {(h.leak.kind ? h.leak.kind === "leak" : h.leak.text.startsWith("Route-Leaking")) ? "Route-Leaking" : "Shared Service"}
                          </span>{" "}
                          <span className="mono">VRF {h.leak.vrf}</span> <span className="faint">{h.leak.where}</span>
                        </div>
                      )}
                      {h.nat && (
                        <div style={{ marginBottom: 4 }}>
                          <span
                            className="chip info"
                            title={L(
                              "VIP (DNAT): der Weg geht ab hier zur internen IP",
                              "VIP (DNAT): from here the path continues to the internal IP",
                            )}
                          >
                            DNAT {h.nat.name}
                          </span>{" "}
                          <span className="mono">
                            {h.nat.ext} → {h.nat.to.join(", ")}
                          </span>
                          {h.nat.ports && <span className="faint"> · Port {h.nat.ports.join(" → ")}</span>}
                          {h.nat.balancer && (
                            <span
                              className="faint nat-balancer"
                              title={L(
                                "Ziel der VIP ist ein Virtual Service des NSX ALB",
                                "Destination of the VIP is a Virtual Service of the NSX ALB",
                              )}
                            >
                              {" "}
                              · NSX ALB {h.nat.balancer}
                            </span>
                          )}
                          {h.nat.to.length > 1 && (
                            <div className="faint">{L("Weg über den ersten Real Server", "Path via the first real server")}</div>
                          )}
                        </div>
                      )}
                      {h.lb && (
                        <div style={{ marginBottom: 4 }} className="trace-lb">
                          <span
                            className={`chip ${h.lb.down ? "warn" : "info"}`}
                            title={L(
                              "NSX ALB: die VIP ist hier angeschlossen, der Weg geht vom Load Balancer zum Server weiter",
                              "NSX ALB: the VIP is attached here, the path continues from the load balancer to the server",
                            )}
                          >
                            Load Balancer {h.lb.name}
                          </span>{" "}
                          <span className="mono">
                            {h.lb.ext}
                            {h.lb.port ? `:${h.lb.port}` : ""} → {(h.lb.to ?? []).slice(0, 3).join(", ")}
                            {h.lb.server_port ? `:${h.lb.server_port}` : ""}
                            {(h.lb.to?.length ?? 0) > 3 ? ` (+${(h.lb.to?.length ?? 0) - 3})` : ""}
                          </span>
                          <div className="faint">
                            {[
                              h.lb.source,
                              h.lb.pool && `Pool ${h.lb.pool}`,
                              h.lb.down && L("Zustand: down bzw. aus", "State: down or off"),
                              h.lb.snat === "client"
                                ? L("Quelle zum Server: Client-IP", "Source to the server: client IP")
                                : L("Quelle zum Server: Service Engine (SNAT)", "Source to the server: Service Engine (SNAT)"),
                              (h.lb.to?.length ?? 0) > 1 && L("Weg über den ersten aktiven Server", "Path via the first active server"),
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </div>
                        </div>
                      )}
                      {h.backup ? (
                        <>
                          <span
                            className="chip warn"
                            title={L(
                              "Backup-Weg: anderer Next-Hop für dasselbe Präfix",
                              "Backup path: different next-hop for the same prefix",
                            )}
                          >
                            Backup
                          </span>{" "}
                          <b className="mono">{h.backup.prefix}</b>
                          <div className="faint">{h.backup.reason}</div>
                        </>
                      ) : h.policy ? (
                        <>
                          <span
                            className="chip info"
                            title={L(
                              "passt sicher und geht der Routing-Tabelle vor",
                              "matches for certain and takes precedence over the routing table",
                            )}
                          >
                            {policyText(h.policy)}
                          </span>
                          <div className="faint">{L("geht der Routing-Tabelle vor", "takes precedence over the routing table")}</div>
                        </>
                      ) : h.route ? (
                        <>
                          <b className="mono">{h.route.prefix}</b>
                          <div className="faint">
                            {[h.route.protocol, h.route.distance != null && L(`Distanz ${h.route.distance}`, `Distance ${h.route.distance}`)]
                              .filter(Boolean)
                              .join(" · ")}
                          </div>
                          {h.route.inner && h.route.inner.length > 0 && (
                            <div
                              className="inner-routes"
                              title={L(
                                "Teile des Netzes mit einer genaueren Route über einen anderen Next-Hop",
                                "Parts of the network with a more specific route via another next-hop",
                              )}
                            >
                              {L("Teile anders geroutet:", "Parts routed differently:")}{" "}
                              {h.route.inner.map((r) => `${r.prefix} ${L("über", "via")} ${r.via}`).join(", ")}
                              {(h.route.inner_total ?? 0) > h.route.inner.length
                                ? ` (+${(h.route.inner_total ?? 0) - h.route.inner.length})`
                                : ""}
                            </div>
                          )}
                        </>
                      ) : (
                        <Dash />
                      )}
                      {possible.length > 0 && (
                        <div
                          className="faint"
                          title={L(
                            "Quelle bzw. Ziel nur über Objekte bestimmt – nicht sicher",
                            "Source or destination determined only via objects – not certain",
                          )}
                        >
                          {L("möglich:", "possible:")} {possible.map(policyText).join(", ")}
                        </div>
                      )}
                    </td>
                    <td>
                      {h.egress ? <Port p={h.egress} /> : <Dash />}
                      {h.boundary && path.boundary && (
                        <div className="faint trace-boundary-text">{path.boundary.text}</div>
                      )}
                      {h.l2 && <L2Line seg={h.l2} />}
                    </td>
                    <td>
                      <HopRulesCell r={h.rules} />
                      {h.snat && (
                        <div
                          className="faint"
                          title={L(
                            "SNAT der erlaubenden Policy: Quelle für die Firewalls danach",
                            "SNAT of the allowing policy: source for the firewalls after it",
                          )}
                        >
                          SNAT → <span className="mono">{h.snat.to ?? "?"}</span>
                          {` (${snatText(h.snat)})`}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              });
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function LookupTable({ rows }: { rows: RouteLookup[] }) {
  return (
    <div className="card">
      <div className="card-body scroll viewport">
        {rows.length === 0 ? (
          <Empty title={L("Keine Routing-Daten", "No routing data")}>
            {L("Noch kein Quellsystem mit Routing eingelesen.", "No source system with routing collected yet.")}
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{L("System · Kontext", "System · context")}</th>
                <th>Route</th>
                <th>Next-Hop</th>
                <th>{L("Geht vor", "Takes precedence")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.source_system_id}|${r.node}|${r.context}|${r.vrf ?? 0}`}>
                  <td>
                    {where(r)}
                    <VrfChip vrf={r.vrf} />
                    <Missing items={r.missing} />
                  </td>
                  <td>
                    {r.route ? (
                      <>
                        <b className="mono">{r.route.prefix}</b>
                        <div className="faint">
                          {[r.route.protocol, r.route.distance != null && L(`Distanz ${r.route.distance}`, `Distance ${r.route.distance}`)]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </>
                    ) : (
                      <span className={`chip ${r.missing.length ? "warn" : ""}`}>
                        {r.missing.length ? L("unbekannt", "unknown") : L("keine Route", "no route")}
                      </span>
                    )}
                  </td>
                  <td>{r.route ? <Hops hops={r.route.next_hops} /> : <Dash />}</td>
                  <td>
                    {r.policy_routes.length ? (
                      <div className="objs">
                        {r.policy_routes.map((p, i) => (
                          <span key={i} title={matchText(p.match)}>
                            <span className={`chip ${p.certain ? "info" : ""}`}>{policyText(p)}</span>{" "}
                            <span className="mono">
                              {[p.gateway, ...p.output_device].filter(Boolean).join(" · ") || "–"}
                            </span>
                            {!p.certain && (
                              <span className="faint">
                                {L(" (möglich – Ziel über Objekte)", " (possible – destination via objects)")}
                              </span>
                            )}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <Dash />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// Quelle bzw. Ziel: IP, Netz oder Name; `hit` = gewählter Treffer der Namenssuche
type Endpoint = { text: string; hit: NameHit | null };
const endpointOf = (text?: string): Endpoint => ({ text: text ?? "", hit: null });

const HIT_KIND: Record<NameHit["kind"], string> = {
  ip: "IP",
  ipam: "IPAM",
  object: L("Objekt", "Object"),
  vm: "VM",
  net: L("Netz", "Network"),
  subnet: L("Inventar", "Inventory"),
};
const PAGE = 50;
type HitType = "" | "host" | "net";

const isIp = (v: string) => /^\d{1,3}(\.\d{1,3}){3}$/.test(v.trim()) || /^[0-9a-f]*:[0-9a-f:.]+$/i.test(v.trim());
// Netz direkt (10.10.0.0/23): geht als Ganzes in die Weg-Suche (0.39.0)
const isNet = (v: string) => {
  const [ip, len, ...rest] = v.trim().split("/");
  return rest.length === 0 && !!len && /^\d{1,3}$/.test(len) && isIp(ip);
};
const hitKey = (h: NameHit) => (h.type === "net" ? `net:${h.net}` : h.ip);

// Treffer der Namenssuche: Name, IP bzw. Netz und woher
function HitRow({ h, onPick }: { h: NameHit; onPick: (h: NameHit) => void }) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={false}
      onMouseDown={(e) => {
        e.preventDefault();
        onPick(h);
      }}
    >
      <b>{h.name ?? h.net ?? h.ip}</b> <span className="mono">{h.type === "net" ? h.net : h.ip}</span>
      <div className="faint">
        {HIT_KIND[h.kind]}
        {h.source ? ` · ${h.source}` : ""}
        {h.detail ? ` · ${h.detail}` : ""}
        {h.also?.length ? L(` · auch ${h.also.join(", ")}`, ` · also ${h.also.join(", ")}`) : ""}
      </div>
    </button>
  );
}

// Eingabe mit Vorschlägen aus IPAM, Objekten, VMs und dem Inventar (ab 2 Zeichen, keine IP):
// Hosts und Netze getrennt, mit Anzahl je Art, alle Treffer seitenweise (0.38.0)
function EndpointInput({
  tenantId,
  value,
  onChange,
  onEnter,
  placeholder,
  title,
  icon,
  forceOpen,
}: {
  tenantId: number;
  value: Endpoint;
  onChange: (v: Endpoint) => void;
  onEnter: () => void;
  placeholder: string;
  title: string;
  icon: string;
  forceOpen?: NameResult | null; // mehrere Treffer beim Suchen: Auswahl zeigen
}) {
  const [res, setRes] = useState<NameResult | null>(null);
  const [type, setType] = useState<HitType>("");
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const blur = useRef<number | undefined>(undefined);
  const q = useDebounced(value.text, 300);
  useEffect(() => {
    const t = q.trim();
    if (value.hit || t.length < 2 || isIp(t) || isNet(t)) {
      setRes(null);
      return;
    }
    let alive = true;
    resolveName(tenantId, t, { type, limit: PAGE })
      .then((r) => alive && setRes(r))
      .catch(() => alive && setRes(null));
    return () => {
      alive = false;
    };
  }, [q, tenantId, value.hit, type]);
  useEffect(() => {
    if (forceOpen?.hits.length) {
      window.clearTimeout(blur.current); // Klick auf „Weg suchen“ schliesst die Auswahl nicht
      setType("");
      setRes(forceOpen);
      setOpen(true);
    }
  }, [forceOpen]);
  const loadMore = async () => {
    if (!res) return;
    setMore(true);
    try {
      const next = await resolveName(tenantId, value.text, { type, limit: PAGE, offset: res.hits.length });
      setRes({ ...next, hits: [...res.hits, ...next.hits] });
    } finally {
      setMore(false);
    }
  };
  const pick = (h: NameHit) => {
    onChange({ text: h.name ?? h.net ?? h.ip, hit: h });
    setOpen(false);
  };
  const all = (res?.counts.host ?? 0) + (res?.counts.net ?? 0);
  const shown = open && res && all > 0;
  const hosts = res?.hits.filter((h) => h.type === "host") ?? [];
  const nets = res?.hits.filter((h) => h.type === "net") ?? [];
  const seg = (t: HitType, label: string, n: number) => (
    <button
      type="button"
      className={type === t ? "on" : ""}
      disabled={n === 0 && t !== ""}
      onMouseDown={(e) => {
        e.preventDefault();
        setType(t);
      }}
    >
      {label} {n}
    </button>
  );
  return (
    <div className="search" style={{ flex: "0 1 260px" }}>
      <span className="ico">
        <Icon name={icon} />
      </span>
      <input
        placeholder={placeholder}
        title={title}
        value={value.text}
        onChange={(e) => {
          onChange({ text: e.target.value, hit: null });
          setOpen(true);
        }}
        onFocus={() => {
          window.clearTimeout(blur.current);
          setOpen(true);
        }}
        onBlur={() => {
          blur.current = window.setTimeout(() => setOpen(false), 150);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            setOpen(false);
            onEnter();
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {value.hit && (
        <div className="faint mono" style={{ fontSize: 11.5, marginTop: 2 }} title={value.hit.name ?? ""}>
          {value.hit.type === "net" ? L(`= Netz ${value.hit.net}`, `= network ${value.hit.net}`) : `= ${value.hit.ip}`}
        </div>
      )}
      {shown && (
        <div className="menu suggest" role="listbox" aria-label={L("Treffer", "Matches")}>
          <div className="suggest-head" onMouseDown={(e) => e.preventDefault()}>
            <div className="seg">
              {seg("", L("Alle", "All"), all)}
              {seg("host", "Hosts", res.counts.host)}
              {seg("net", L("Netze", "Networks"), res.counts.net)}
            </div>
            <span className="faint">
              {res.hits.length} {L("von", "of")} {res.total}
              {res.capped ? L(" – mehr gelesen als gezeigt, genauer suchen", " – more read than shown, refine the search") : ""}
            </span>
          </div>
          {hosts.length > 0 && type === "" && nets.length > 0 && <div className="suggest-group">Hosts</div>}
          {hosts.map((h) => (
            <HitRow key={hitKey(h)} h={h} onPick={pick} />
          ))}
          {nets.length > 0 && type === "" && hosts.length > 0 && <div className="suggest-group">{L("Netze", "Networks")}</div>}
          {nets.map((h) => (
            <HitRow key={hitKey(h)} h={h} onPick={pick} />
          ))}
          {res.hits.length < res.total && (
            <button
              type="button"
              className="suggest-more"
              disabled={more}
              onMouseDown={(e) => {
                e.preventDefault();
                void loadMore();
              }}
            >
              {more
                ? L("Lade…", "Loading…")
                : L(`Weitere laden (${res.total - res.hits.length})`, `Load more (${res.total - res.hits.length})`)}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// Name -> IP bzw. Netz: direkt, gewählter Treffer, sonst genau ein Treffer; mehrere: Auswahl.
// Ein Netz geht als Ganzes in die Weg-Suche (0.39.0, `net` für die Anzeige).
async function toIp(
  tenantId: number,
  ep: Endpoint,
  what: string,
  set: (v: Endpoint) => void,
  choose: (res: NameResult) => void,
): Promise<{ ip: string; net?: string | null }> {
  const text = ep.text.trim();
  if (!text) return { ip: "" };
  if (isIp(text)) return { ip: text };
  if (isNet(text)) return { ip: text, net: text };
  if (ep.hit) return { ip: ep.hit.ip, net: ep.hit.net };
  const res = await resolveName(tenantId, text, { limit: PAGE });
  const keys = new Set(res.hits.map(hitKey));
  if (keys.size === 1 && res.total === 1) {
    set({ text, hit: res.hits[0] });
    return { ip: res.hits[0].ip, net: res.hits[0].net };
  }
  if (!res.hits.length)
    throw new Error(
      L(
        `${what}: „${text}“ nicht gefunden (IPAM, Objekte, VMs, Netze)`,
        `${what}: “${text}” not found (IPAM, objects, VMs, networks)`,
      ),
    );
  choose(res);
  throw new Error(
    L(
      `${what}: ${res.total} Treffer für „${text}“ – bitte einen wählen`,
      `${what}: ${res.total} matches for “${text}” – please choose one`,
    ),
  );
}

// Weg-Suche: Quelle (IP bzw. Name) und/oder Startpunkt, dazu das Ziel. Ohne beides: je Kontext
// die Route zum Ziel.
function PathSearch({
  tenantId,
  nodes,
  onCapture,
  initial,
}: {
  tenantId: number;
  nodes: RoutingNode[];
  onCapture?: (c: Capture) => void;
  initial?: ViewParams; // aus einem Link (#wege?src=…&dst=…&service=…), z. B. Applikations-Doku
}) {
  const [src, setSrc] = useState<Endpoint>(endpointOf(initial?.src));
  const [start, setStart] = useState(initial?.start ?? "");
  const [dst, setDst] = useState<Endpoint>(endpointOf(initial?.dst));
  const [choices, setChoices] = useState<{ src?: NameResult; dst?: NameResult }>({});
  const [trace, setTrace] = useState<TraceResult | null>(null);
  type Asked = { ip: string; name?: string; net?: string | null };
  const [asked, setAsked] = useState<{ src: Asked; dst: Asked } | null>(null);
  const [slack, setSlack] = useState("1"); // Backup-Wege: Hops mehr als der kürzeste aktive; "" = keine
  const [service, setService] = useState(initial?.service ?? ""); // tcp/443, udp/53 bzw. Service-Objekt/ACI-Filter
  const [rows, setRows] = useState<RouteLookup[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const srcNow = src;
  const dstNow = dst;
  const startNow = start;
  // `over`: Werte statt des Zustands (Richtung tauschen sucht sofort mit den getauschten)
  const run = async (over?: { src: Endpoint; dst: Endpoint; start: string }) => {
    const src = over?.src ?? srcNow;
    const dst = over?.dst ?? dstNow;
    const start = over ? over.start : startNow;
    if (!dst.text.trim()) return;
    setBusy(true);
    setError(null);
    setTrace(null);
    setRows(null);
    setChoices({});
    try {
      const [s, d] = await Promise.all([
        toIp(tenantId, src, L("Quelle", "Source"), setSrc, (h) => setChoices((c) => ({ ...c, src: h }))),
        toIp(tenantId, dst, L("Ziel", "Destination"), setDst, (h) => setChoices((c) => ({ ...c, dst: h }))),
      ]);
      const named = (ep: Endpoint, r: { ip: string; net?: string | null }): Asked => {
        const t = ep.text.trim();
        return { ...r, name: t && t !== r.ip && t !== r.net ? t : undefined };
      };
      setAsked({ src: named(src, s), dst: named(dst, d) });
      if (s.ip || start) {
        setTrace(
          await traceRoute(tenantId, { dst: d.ip, src: s.ip, start, service, slack: slack === "" ? 0 : Number(slack) }),
        );
      } else {
        setRows(await lookupRoute(tenantId, d.ip));
      }
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  // Richtung tauschen (0.65.1): Quelle <-> Ziel, der Startpunkt gehörte zur alten Quelle; war schon gesucht,
  // gleich den Rückweg suchen
  const swap = () => {
    const next = { src: dst, dst: src, start: "" };
    setSrc(next.src);
    setDst(next.dst);
    setStart("");
    setChoices({});
    if ((trace || rows) && next.dst.text.trim()) void run(next);
  };
  // Link mit Ziel: gleich suchen (einmal beim Öffnen)
  useEffect(() => {
    if (initial?.dst) void run();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const label = (a: Asked) =>
    a.net ? `${a.name ? `${a.name} = ` : ""}${L("Netz", "network")} ${a.net}` : a.name ? `${a.name} (${a.ip})` : a.ip;
  return (
    <>
      <div className="filterbar" style={{ alignItems: "flex-start" }}>
        <EndpointInput
          tenantId={tenantId}
          value={src}
          onChange={setSrc}
          onEnter={() => void run()}
          icon="search"
          placeholder={L("Quelle: IP, Netz oder Name", "Source: IP, network or name")}
          title={L(
            "Quelle als IP, Netz bzw. Name/FQDN (IPAM, Objekte der Firewalls, VMs, Subnetze): der Weg beginnt dort, wo sie angeschlossen ist (bzw. am Startpunkt); ein Netz gilt als Ganzes",
            "Source as IP, network or name/FQDN (IPAM, firewall objects, VMs, subnets): the path starts where it is connected (or at the starting point); a network counts as a whole",
          )}
          forceOpen={choices.src}
        />
        <select
          className="sel"
          value={start}
          onChange={(e) => setStart(e.target.value)}
          title={L(
            "Startpunkt: Firewall, VDOM bzw. ACI-VRF – ohne Wahl ergibt er sich aus der Quelle",
            "Starting point: firewall, VDOM or ACI VRF – if none is chosen, it follows from the source",
          )}
        >
          <option value="">{L("Start: aus der Quelle", "Start: from the source")}</option>
          {nodes.map((n) => (
            <option key={n.key} value={n.key}>
              {n.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          id="trace-swap"
          onClick={swap}
          disabled={busy || (!src.text.trim() && !dst.text.trim())}
          title={L(
            "Richtung tauschen: Quelle und Ziel tauschen (Start aus der neuen Quelle) und – wenn schon gesucht – den Rückweg suchen",
            "Swap direction: swap source and destination (start from the new source) and – if already searched – search the return path",
          )}
          aria-label={L("Richtung tauschen", "Swap direction")}
        >
          ⇄
        </button>
        <EndpointInput
          tenantId={tenantId}
          value={dst}
          onChange={setDst}
          onEnter={() => void run()}
          icon="route"
          placeholder={L("Ziel: IP, Netz oder Name, z. B. web01", "Destination: IP, network or name, e.g. web01")}
          title={L(
            "Ziel als IP, Netz bzw. Name/FQDN; ein Netz gilt als Ganzes (Route und Regeln für das ganze Netz)",
            "Destination as IP, network or name/FQDN; a network counts as a whole (route and rules for the entire network)",
          )}
          forceOpen={choices.dst}
        />
        <div className="search" style={{ flex: "0 1 190px" }}>
          <input
            placeholder={L("Dienst: tcp/443 oder FT_https", "Service: tcp/443 or FT_https")}
            title={L(
              "Dienst für Policies und Contracts: tcp/443, udp/53, tcp, icmp bzw. ein Service-Objekt oder ACI-Filter (FT_https) – leer: nur Regeln mit allen Diensten sicher",
              "Service for policies and contracts: tcp/443, udp/53, tcp, icmp or a service object or ACI filter (FT_https) – empty: only rules with all services are certain",
            )}
            value={service}
            onChange={(e) => setService(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
        </div>
        <select
          className="sel"
          value={slack}
          onChange={(e) => setSlack(e.target.value)}
          title={L(
            "Redundante Wege: Backup-Wege (BGP-Pfade, schwebende statische Routen) mit gleicher bzw. fast gleicher Länge",
            "Redundant paths: backup paths (BGP paths, floating static routes) of equal or nearly equal length",
          )}
        >
          <option value="0">{L("Backup: gleich lang", "Backup: same length")}</option>
          <option value="1">{L("Backup: bis +1 Hop", "Backup: up to +1 hop")}</option>
          <option value="2">{L("Backup: bis +2 Hops", "Backup: up to +2 hops")}</option>
          <option value="">{L("Backup: ausblenden", "Backup: hide")}</option>
        </select>
        <button className="btn btn-primary btn-sm" onClick={() => void run()} disabled={busy || !dst.text.trim()}>
          {busy ? L("Suche…", "Searching…") : L("Weg suchen", "Search path")}
        </button>
      </div>
      <div className="faint" style={{ margin: "-4px 0 10px" }}>
        {L(
          <>
            Hop für Hop: Policy-Routen und SD-WAN-Regeln, die sicher passen, gehen vor; sonst das längste Präfix. Je Firewall
            die passenden Policies (erste sichere entscheidet), je ACI-VRF die Contracts zwischen den EPGs von Quelle und Ziel
            (Taboo zuerst, vzAny, ESG, Preferred Group). Ein <b>Netz</b> gilt als Ganzes: die Route, die das ganze Netz enthält
            (anders geroutete Teile als Hinweis), Regeln nur sicher, wenn sie das ganze Netz erfassen – sonst „nur teilweise“.
            Ohne Quelle und Start: je Kontext die wirksame Route.
          </>,
          <>
            Hop by hop: policy routes and SD-WAN rules that match for certain take precedence; otherwise the longest prefix.
            Per firewall the matching policies (the first certain one decides), per ACI VRF the contracts between the EPGs of
            source and destination (Taboo first, vzAny, ESG, Preferred Group). A <b>network</b> counts as a whole: the route
            that contains the entire network (parts routed differently as a note), rules only certain if they cover the
            entire network – otherwise “only partially”. Without source and start: the effective route per context.
          </>,
        )}
      </div>
      <ErrorBox error={error} prefix={L("Weg suchen", "Search path")} />
      {trace && (
        <>
          <div className="faint" style={{ margin: "0 0 8px" }}>
            {asked && (
              <>
                {asked.src.ip ? L(`Quelle ${label(asked.src)} → `, `Source ${label(asked.src)} → `) : ""}
                {L("Ziel", "Destination")} {label(asked.dst)}
                {trace.service
                  ? ` · ${L("Dienst", "Service")} ${trace.service_name ? `${trace.service_name} = ` : ""}${trace.service}`
                  : ""}
                {trace.start.length > 0 ? " · " : ""}
              </>
            )}
            {trace.start.length > 0 && `Start: ${trace.start.map((s) => `${s.label} (${s.via})`).join(" · ")}`}
          </div>
          {trace.notes.map((n) => (
            <Note key={n} warn>
              {n}
            </Note>
          ))}
          {trace.paths.length === 0 && trace.notes.length === 0 && (
            <Empty title={L("Kein Weg", "No path")}>
              {L(
                "Kein Hop gefunden – Startpunkt wählen bzw. Quellsysteme neu einlesen.",
                "No hop found – choose a starting point or collect the source systems again.",
              )}
            </Empty>
          )}
          {(() => {
            const shown = slack === "" ? trace.paths.filter((p) => p.kind !== "backup") : trace.paths;
            return (
              <>
                <ZoneIntentCard trace={trace} />
                <L2Ends trace={trace} />
                {shown.length > 1 && <PathOverview trace={{ ...trace, paths: shown }} />}
                {shown.map((p, i) => (
                  <TraceCard
                    key={i}
                    path={p}
                    index={i}
                    title={pathTitle(shown, p)}
                    compare={shown.length > 1}
                    onCapture={onCapture}
                  />
                ))}
              </>
            );
          })()}
        </>
      )}
      {rows && <LookupTable rows={rows} />}
    </>
  );
}

export default function Paths({
  tenantId,
  tenantLabel,
  canEdit = false,
  params,
}: {
  tenantId: number | null;
  tenantLabel: string;
  canEdit?: boolean;
  params?: ViewParams;
}) {
  const [nodes, setNodes] = useState<RoutingNode[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (tenantId === null) return;
    setNodes(null);
    fetchRoutingSummary(tenantId)
      .then((s) => setNodes(s.nodes))
      .catch((e) => {
        setError(e);
        setNodes([]);
      });
  }, [tenantId]);
  if (tenantId === null) {
    return (
      <>
        <PageHead title={L("Weg-Suche", "Path search")} />
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      </>
    );
  }
  // Weg endet an einem Router ohne gelesenes Gerät: im Routing als Anbindung erfassen
  const capture = (c: Capture) => {
    try {
      sessionStorage.setItem(CAPTURE_KEY, JSON.stringify(c));
    } catch {
      /* sessionStorage nicht verfügbar */
    }
    window.location.hash = "routing";
  };
  return (
    <>
      <PageHead title={L("Weg-Suche", "Path search")}>
        {L(
          <>
            Weg einer Verbindung in „{tenantLabel}“ Hop für Hop – über Firewalls (Policies, NAT) und ACI (Contracts zwischen
            den EPGs), mit aktiven und Backup-Wegen. Für Fehlersuche und Kontrolle von Arbeiten; rein lesend, Stand des letzten
            Laufs. Quelle und Ziel als IP, Netz oder Name aus IPAM, Firewall-Objekten, VMs und Subnetzen.
          </>,
          <>
            Path of a connection in “{tenantLabel}” hop by hop – through firewalls (policies, NAT) and ACI (contracts between
            the EPGs), with active and backup paths. For troubleshooting and checking work; read-only, state of the last run.
            Source and destination as IP, network or name from IPAM, firewall objects, VMs and subnets.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("Routing laden", "Load routing")} />
      {nodes === null ? <Loading /> : <PathSearch tenantId={tenantId} nodes={nodes} onCapture={canEdit ? capture : undefined} initial={params} />}
    </>
  );
}
