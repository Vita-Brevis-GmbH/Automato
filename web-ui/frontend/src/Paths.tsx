// Weg-Suche (0.35.0, ADR 0019 Stufe 3): eigene Ansicht statt Reiter im Routing. Der Weg einer
// Verbindung Hop für Hop ab Quelle bzw. Startpunkt – je Firewall die Policies, je ACI-VRF die
// Contracts zwischen den EPGs (Fehlersuche, Kontrolle von Arbeiten). Quelle und Ziel als IP
// oder Name/FQDN (IPAM, Objekte der Firewalls, VMs), der Dienst als tcp/443 oder Objektname.
// Seit 0.57.0 die logischen Zonen je Hop, die Übergänge dazwischen und die Absicht zwischen
// Quell- und Zielzone (auch je Dienst) mit Urteil.
// ADR 0019 Ergänzung (Test des Anwenders 2026-10-27): Eingaben und Ergebnis bleiben in der URL
// (#wege?src=…&dst=…&service=…, Zurück stellt die Suche wieder her), Details am Weg im Seitenpanel
// statt eines Sprungs in eine andere Ansicht, je Link „↗“ in einem neuen Tab, je Hop „mehr“; dazu
// Anbindungen als Quelle bzw. Ziel (Eingang der Anbindung, ihre Netze bzw. Internet). Seit ADR 0032
// auch durch gelesene Router bis an die Firmengrenze (am letzten Hop: eBGP zu fremdem AS,
// Anbindung, Default-Route zum Provider).
import { createContext, Fragment, ReactNode, useContext, useEffect, useRef, useState } from "react";
import {
  ExitKind,
  ExplainQuery,
  fetchInventory,
  fetchObjects,
  fetchRoutes,
  fetchRulePathSearch,
  fetchRoutingSummary,
  HopRule,
  HopRules,
  InvInterface,
  L2Location,
  L2Segment,
  NameHit,
  NameResult,
  PathValue,
  resolveName,
  RoutingNode,
  RulePathSearch,
  snatText,
  TopoRoute,
  TraceExitEnd,
  TraceHop,
  TracePath,
  TracePort,
  TraceResult,
  traceRoute,
  TraceZone,
  TraceZoneStep,
  VipServer,
} from "./api";
import type { View, ViewParams } from "./App";
import { NatDrawer } from "./Nat";
import PathExplainDrawer from "./PathExplain";
import { ObjectDrawer, useDebounced } from "./Objects";
import RuleDetailDrawer from "./RuleDetail";
import { Capture, CAPTURE_KEY, Hops, matchText, Missing, Names, policyText, VrfChip } from "./Routing";
import { Dash, Drawer, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";
import { FoldTitle, useFolds } from "./fold";
import { L } from "./i18n";

// -- Details am Weg (ADR 0019 Ergänzung) -------------------------------------------------------
// Ein Klick öffnet das Detail im Seitenpanel – die Weg-Suche bleibt stehen; Ctrl-/Cmd-/Mittelklick
// bzw. „↗“ öffnet dieselbe Ansicht in einem neuen Tab (gleiche Hash-URL).
type DetailRef =
  | { kind: "rule"; id: number }
  | { kind: "nat"; id: number }
  | { kind: "object"; name: string; sid: number }
  | { kind: "route"; hop: TraceHop }
  | { kind: "node"; hop: TraceHop };
const DetailCtx = createContext<(d: DetailRef) => void>(() => undefined);

const hashOf = (view: string, params: Record<string, string> = {}) => {
  const q = new URLSearchParams(params).toString();
  return `#${view}${q ? `?${q}` : ""}`;
};
const ruleHref = (id: number) => hashOf("regeln", { rule: String(id) });
const natHref = (name: string) => hashOf("nat", { q: name });
const objectHref = (name: string) => hashOf("objekte", { q: name });
const portHref = (name: string) => hashOf("objekte", { tab: "ports", q: name });
const routesHref = (h: TraceHop, q?: string) => hashOf("routing", { tab: "routes", context: h.key, ...(q ? { q } : {}) });
const EXITS_HREF = hashOf("routing", { tab: "exits" });
// andere Ansicht in einem neuen Tab (aus den Details heraus), die Weg-Suche bleibt
const openTab = (v: View, p: ViewParams = {}) => window.open(hashOf(v, p), "_blank", "noopener");

const EXIT_KIND: Record<ExitKind, string> = {
  internet: "Internet",
  mpls: "MPLS",
  wan: "WAN",
  partner: "Partner",
  other: L("Anbindung", "Exit"),
};

export function NewTab({ href }: { href: string }) {
  return (
    <a
      className="newtab"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={L("in neuem Tab öffnen", "open in new tab")}
      aria-label={L("in neuem Tab öffnen", "open in new tab")}
    >
      ↗
    </a>
  );
}

// Link am Weg: Klick = Detail im Seitenpanel, Ctrl-/Cmd-/Shift-/Mittelklick wie im Browser (neuer Tab)
function Opener({
  href,
  detail,
  title,
  className,
  children,
}: {
  href: string;
  detail: DetailRef;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  const open = useContext(DetailCtx);
  return (
    <>
      <a
        href={href}
        className={className}
        title={title ?? L("Details im Seitenpanel – Ctrl-Klick: neuer Tab", "Details in the side panel – Ctrl-click: new tab")}
        onClick={(e) => {
          if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          open(detail);
        }}
      >
        {children}
      </a>
      <NewTab href={href} />
    </>
  );
}

// Quelle/Ziel einer Policy über ein Objekt (nicht any, negiert, Netz, direkt): als Objekt öffnen
const PLAIN = new Set(["any", "all", "negiert", "negated", "netz", "network", "direkt", "direct"]);
function objectName(via: string | null): string | null {
  const name = (via ?? "").replace(/ \((Teil|part)\)$/, "").trim();
  return name && !PLAIN.has(name.toLowerCase()) ? name : null;
}

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
  if (path.exit_start && activeCount > 1)
    return (
      <span
        className="chip info"
        title={L(
          "Anbindung an mehreren Firewalls: je Firewall ein Weg",
          "Exit at several firewalls: one path per firewall",
        )}
      >
        {L("Variante ab", "Variant from")} {path.exit_start}
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
      <Opener
        href={ruleHref(p.rule_id)}
        detail={{ kind: "rule", id: p.rule_id }}
        title={L("Regel im Seitenpanel öffnen – Ctrl-Klick: neuer Tab", "Open rule in the side panel – Ctrl-click: new tab")}
      >
        {p.label ?? `Policy ${p.external_id}`}
      </Opener>
      {!p.label && p.name ? ` „${p.name}“` : ""} · {p.action === "allow" ? L("erlauben", "accept") : L("sperren", "deny")}
      {matchedVia(p)}
      {p.services.length ? ` · ${p.services.join(", ")}` : ""}
      {p.unsure.length ? L(` · möglich (${p.unsure.join(", ")})`, ` · possible (${p.unsure.join(", ")})`) : ""}
      {p.closed ? (
        <span className="aci-note aci-closed">{L(" · öffnet den Dienst nicht", " · does not open the service")}</span>
      ) : null}
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

// Ein- bzw. Ausgang eines Hops: Interface (Detail im Seitenpanel), Zone, Next-Hop, Anbindung
function HopPort({ p, hop }: { p: TracePort | undefined; hop: TraceHop }) {
  if (!p?.interface && !p?.next_hop && !p?.zone) return null;
  return (
    <div>
      {p.interface && (
        <Opener
          href={portHref(p.interface)}
          detail={{ kind: "node", hop }}
          className="mono hop-port"
          title={L("Interface im Seitenpanel – Ctrl-Klick: Ports in neuem Tab", "Interface in the side panel – Ctrl-click: ports in a new tab")}
        >
          {p.interface}
        </Opener>
      )}
      {p.zone && (
        <span className="chip" style={{ marginLeft: 4 }}>
          {p.zone}
        </span>
      )}
      {p.next_hop && (
        <span className="mono">
          {" "}
          → {p.next_hop}
          <Names r={p} />
        </span>
      )}
      {p.exit && (
        <>
          <span
            className="chip info"
            style={{ marginLeft: 4 }}
            title={L("Anbindung nach aussen (von Hand erfasst)", "Exit to the outside (captured manually)")}
          >
            {EXIT_KIND[p.exit.kind as ExitKind] ?? L("Anbindung", "Exit")} {p.exit.name}
          </span>
          <NewTab href={EXITS_HREF} />
        </>
      )}
    </div>
  );
}

function portText(p?: TracePort | null): string {
  const parts = [p?.interface, p?.zone && L(`Zone ${p.zone}`, `zone ${p.zone}`)].filter(Boolean);
  return parts.join(" · ") || "–";
}

const SERVER_STATE: Record<string, [string, string]> = {
  active: ["ok", L("aktiv", "active")],
  standby: ["info", "Standby"],
  disable: ["na", L("deaktiviert", "disabled")],
};

// Ziel-Server einer VIP bzw. eines Pools (0.114.1, Anwender: „nur einer und nur die IP“): je
// Server Name (VM, IPAM, Hostname), IP:Port, Zustand, Gewicht, Health-Check, Objekt der Firewall
// und EPG der VM; der Weg geht über den markierten Server
function VipServers({ servers, via, total, sid }: { servers?: VipServer[]; via?: string; total?: number; sid?: number }) {
  if (!servers?.length) return null;
  // ein Server nur mit IP (und Port) steht schon in der Zeile darüber
  if (servers.length === 1 && !total && Object.keys(servers[0]).every((k) => k === "ip" || k === "port")) return null;
  return (
    <ul className="vip-servers">
      {servers.map((s, k) => {
        const names = [...new Set([s.vm?.name, ...(s.names ?? []), s.hostname].filter((x): x is string => !!x))];
        const state = s.status ? (SERVER_STATE[s.status] ?? ["warn", s.status]) : null;
        const meta = [
          s.weight != null && `${L("Gewicht", "Weight")} ${s.weight}`,
          s.health?.length && `Health-Check ${s.health.join(", ")}`,
          s.vm?.epg && `EPG ${s.vm.epg.split("/").pop()}`,
          s.vm?.power_state === "poweredOff" && L("VM ausgeschaltet", "VM powered off"),
          s.pool && `Pool ${s.pool}`,
        ].filter(Boolean);
        return (
          <li key={k} className="vip-server">
            <span className="mono">
              {s.ip}
              {s.port ? `:${s.port}` : ""}
            </span>
            {names.length > 0 && (
              <>
                {" "}
                <b>{names[0]}</b>
                {names.length > 1 && <span className="faint"> ({names.slice(1).join(", ")})</span>}
              </>
            )}
            {state && <span className={`chip ${state[0]}`}>{state[1]}</span>}
            {via && s.ip === via && (
              <span className="chip info" title={L("Der gezeigte Weg führt zu diesem Server", "The path shown leads to this server")}>
                {L("Weg", "Path")}
              </span>
            )}
            {(s.object ?? []).map((o) => (
              <Fragment key={o}>
                {" "}
                {sid != null ? (
                  <Opener href={objectHref(o)} detail={{ kind: "object", name: o, sid }} className="mono faint">
                    {o}
                  </Opener>
                ) : (
                  <span className="mono faint">{o}</span>
                )}
              </Fragment>
            ))}
            {meta.length > 0 && <span className="faint"> · {meta.join(" · ")}</span>}
          </li>
        );
      })}
      {total != null && total > servers.length && (
        <li className="faint">{L(`+${total - servers.length} weitere`, `+${total - servers.length} more`)}</li>
      )}
    </ul>
  );
}

// Methode, Health-Check, Bereich und Kommentar einer VIP bzw. eines Virtual Service
function vipMeta(x: { method?: string; health?: string[]; mapped?: string; extintf?: string; comment?: string; fqdn?: string[] }): string {
  return [
    x.method && `${L("Methode", "Method")} ${x.method}`,
    x.health?.length && `Health-Check ${x.health.join(", ")}`,
    x.mapped && `${L("Bereich", "Range")} ${x.mapped}`,
    x.extintf && `${L("Eingang", "Ingress")} ${x.extintf}`,
    x.fqdn?.length && `FQDN ${x.fqdn.join(", ")}`,
    x.comment && `„${x.comment}“`,
  ]
    .filter(Boolean)
    .join(" · ");
}

// Entscheidende Regel mit Interfaces, Quelle, Ziel und Dienst; Objekte im Seitenpanel
function RuleFacts({ p, r, sid }: { p: HopRule; r: HopRules; sid: number }) {
  const side = (via: string | null) => {
    if (!via) return <span className="faint">?</span>;
    const name = p.kind ? null : objectName(via);
    return name ? (
      <Opener href={objectHref(name)} detail={{ kind: "object", name, sid }} className="mono">
        {via}
      </Opener>
    ) : (
      <span className="mono">{via}</span>
    );
  };
  return (
    <div className="rule-facts">
      <Opener href={ruleHref(p.rule_id)} detail={{ kind: "rule", id: p.rule_id }}>
        {p.label ?? `Policy ${p.external_id}`}
      </Opener>
      {!p.label && p.name ? ` „${p.name}“` : ""} · {p.action === "allow" ? L("erlauben", "accept") : L("sperren", "deny")}
      {p.kind ? (
        (r.src_epg || r.dst_epg) && (
          <div>
            EPG: {r.src_epg || "?"} → {r.dst_epg || "?"}
          </div>
        )
      ) : (
        <div>
          Interfaces: <span className="mono">{p.srcintf.join(", ") || "?"}</span> →{" "}
          <span className="mono">{p.dstintf.join(", ") || "?"}</span>
        </div>
      )}
      <div>
        {L("Quelle", "Source")}: {side(p.src)} · {L("Ziel", "Destination")}: {side(p.dst)}
      </div>
      <div>
        {L("Dienst", "Service")}: {p.services.join(", ") || "–"}
      </div>
      {p.unsure.length > 0 && (
        <div className="faint">
          {L("nicht prüfbar", "not verifiable")}: {p.unsure.join(", ")}
        </div>
      )}
    </div>
  );
}

// Ein Hop ausführlich (Anwender: „mehr Infos am Pfad“): Knoten, Ein-/Ausgang mit Zone, Route mit
// Next-Hops, die entscheidende Regel mit Quelle/Ziel/Dienst und NAT – je Firewall eines Verbunds
function HopFacts({ h, zone, title }: { h: TraceHop; zone?: TraceZone | null; title?: string }) {
  const decisive = h.rules?.rules.find((x) => x.decides);
  const r = h.route;
  const kind = h.kind === "vrf" ? "ACI-VRF" : h.kind === "vdom" ? "VDOM" : "Firewall";
  return (
    <div className="hop-facts">
      {title && <div className="hop-facts-title">{title}</div>}
      <dl className="kv tight">
        <dt>{L("Knoten", "Node")}</dt>
        <dd>
          {kind} · {h.source}
          {h.group ? L(` · Verbund ${h.group}`, ` · parallel group ${h.group}`) : ""}
          {h.kind === "vrf" && h.members.length ? ` · ${h.members.join(", ")}` : ""} <VrfChip vrf={h.vrf} />
          <Missing items={h.missing} />
        </dd>
        <dt>{L("Eingang", "Incoming")}</dt>
        <dd>
          {portText(h.ingress)} <span className="faint">· {h.via}</span>
        </dd>
        <dt>{L("Ausgang", "Outgoing")}</dt>
        <dd>
          {portText(h.egress)}
          {h.egress?.next_hop && (
            <span className="mono">
              {" "}
              → {h.egress.next_hop}
              <Names r={h.egress} />
            </span>
          )}
          {h.egress?.exit && ` · ${EXIT_KIND[h.egress.exit.kind as ExitKind] ?? ""} ${h.egress.exit.name}`}
        </dd>
        {zone && (
          <>
            <dt>{L("Zone", "Zone")}</dt>
            <dd>
              <ZoneChip z={zone} /> <NewTab href={hashOf("design")} />
            </dd>
          </>
        )}
        <dt>Route</dt>
        <dd>
          {h.policy ? (
            <>
              {policyText(h.policy)} {L("geht der Routing-Tabelle vor", "takes precedence over the routing table")}:{" "}
              <span className="mono">{[h.policy.gateway, ...h.policy.output_device].filter(Boolean).join(" · ") || "–"}</span>
            </>
          ) : r ? (
            <>
              <Opener href={routesHref(h, r.prefix)} detail={{ kind: "route", hop: h }} className="mono">
                {r.prefix}
              </Opener>{" "}
              <span className="faint">
                {[r.protocol, r.distance != null && L(`Distanz ${r.distance}`, `Distance ${r.distance}`)].filter(Boolean).join(" · ")}
              </span>
              <Hops hops={r.next_hops} />
            </>
          ) : (
            <span className="faint">{L("keine Route", "no route")}</span>
          )}
          {h.backup && (
            <div className="faint">
              Backup {h.backup.prefix}: {h.backup.reason}
            </div>
          )}
        </dd>
        {h.rules && (
          <>
            <dt>{L("Regel", "Rule")}</dt>
            <dd>
              {decisive ? <RuleFacts p={decisive} r={h.rules} sid={h.source_system_id} /> : null}
              <div className="faint">{h.rules.text}</div>
            </dd>
          </>
        )}
        {h.nat && (
          <>
            <dt>DNAT</dt>
            <dd>
              <Opener href={natHref(h.nat.name)} detail={{ kind: "nat", id: h.nat.id }} className="mono">
                {h.nat.name}
              </Opener>{" "}
              <span className="mono">
                {h.nat.ext} → {h.nat.to.join(", ")}
              </span>
              {h.nat.ports && <span className="faint"> · Port {h.nat.ports.join(" → ")}</span>}
              {vipMeta(h.nat) && <div className="faint">{vipMeta(h.nat)}</div>}
              <VipServers servers={h.nat.servers} via={h.nat.to.length > 1 ? h.nat.to[0] : undefined} sid={h.source_system_id} />
            </dd>
          </>
        )}
        {h.snat && (
          <>
            <dt>SNAT</dt>
            <dd>
              <span className="mono">{h.snat.to ?? "?"}</span> <span className="faint">({snatText(h.snat)})</span>
            </dd>
          </>
        )}
        {h.lb && (
          <>
            <dt>Load Balancer</dt>
            <dd>
              <Opener href={natHref(h.lb.name)} detail={{ kind: "nat", id: h.lb.id }} className="mono">
                {h.lb.name}
              </Opener>{" "}
              <span className="mono">
                {h.lb.ext}
                {h.lb.port ? `:${h.lb.port}` : ""} → {(h.lb.to ?? []).slice(0, 3).join(", ")}
              </span>
              {vipMeta(h.lb) && <div className="faint">{vipMeta(h.lb)}</div>}
              <VipServers
                servers={h.lb.members}
                via={(h.lb.to?.length ?? 0) > 1 ? h.lb.to?.[0] : undefined}
                total={h.lb.servers}
              />
            </dd>
          </>
        )}
        {h.dst && (
          <>
            <dt>{L("Ziel hier", "Destination here")}</dt>
            <dd className="mono">{h.dst}</dd>
          </>
        )}
        {h.l2 && (
          <>
            <dt>L2</dt>
            <dd>
              {h.l2.text}
              {h.l2.vlan ? ` · VLAN ${h.l2.vlan}` : ""} <NewTab href={hashOf("verkabelung")} />
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

function HopMore({ hop }: { hop: TraceHop }) {
  const parts = hop.parts ?? [hop];
  return (
    <div className="hop-more-body">
      {parts.map((h, k) => (
        <HopFacts key={k} h={h} zone={hop.zone} title={parts.length > 1 ? h.label : undefined} />
      ))}
    </div>
  );
}

const ROUTE_TABLE: Record<string, [string, string]> = {
  rib: ["ok", L("wirksam", "effective")],
  bgp: ["info", L("BGP-Pfad", "BGP path")],
  static: ["", L("statisch", "static")],
};

// Route eines Hops im Seitenpanel: die wirksame mit Next-Hops und alle Routen zum Ziel in diesem
// Kontext (RIB, BGP-Pfade, statische) – Backup-Wege und schwebende Routen auf einen Blick
function RouteDrawer({ hop, tenantId, dst, onClose }: { hop: TraceHop; tenantId: number; dst?: string; onClose: () => void }) {
  const ip = (hop.nat?.to[0] ?? hop.dst ?? dst ?? "").split("/")[0];
  const [rows, setRows] = useState<TopoRoute[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setRows(null);
    if (!ip) {
      setRows([]);
      return;
    }
    let alive = true;
    fetchRoutes({ tenantId, sourceId: hop.source_system_id, context: hop.context, ip, limit: 50 })
      .then((p) => alive && setRows(p.rows))
      .catch((e) => {
        if (!alive) return;
        setError(e);
        setRows([]);
      });
    return () => {
      alive = false;
    };
  }, [tenantId, hop.source_system_id, hop.context, ip]);
  const r = hop.route;
  const possible = (hop.policy_routes ?? []).filter((p) => !p.certain);
  return (
    <Drawer title={r ? `Route ${r.prefix}` : "Route"} sub={`${hop.label} · ${L("Ziel", "Destination")} ${ip || "?"}`} onClose={onClose}>
      <dl className="kv" id="route-drawer">
        <dt>{L("Wirksam", "Effective")}</dt>
        <dd>
          {r ? (
            <>
              <b className="mono">{r.prefix}</b>{" "}
              <span className="faint">
                {[r.protocol, r.distance != null && L(`Distanz ${r.distance}`, `Distance ${r.distance}`)].filter(Boolean).join(" · ")}
              </span>
            </>
          ) : (
            <span className="faint">{L("keine Route", "no route")}</span>
          )}
        </dd>
        <dt>Next-Hops</dt>
        <dd>
          <Hops hops={r?.next_hops} />
        </dd>
        {hop.kind === "vrf" && r?.members.length ? (
          <>
            <dt>Border-Leafs</dt>
            <dd>{r.members.join(", ")}</dd>
          </>
        ) : null}
        {hop.policy && (
          <>
            <dt>{L("Geht vor", "Takes precedence")}</dt>
            <dd>
              {policyText(hop.policy)}{" "}
              <span className="mono">{[hop.policy.gateway, ...hop.policy.output_device].filter(Boolean).join(" · ")}</span>
            </dd>
          </>
        )}
        {possible.length > 0 && (
          <>
            <dt>{L("Möglich", "Possible")}</dt>
            <dd>
              {possible.map((p) => (
                <div key={`${p.kind}-${p.seq}`} title={matchText(p.match)}>
                  {policyText(p)} <span className="faint">{L("(Ziel über Objekte)", "(destination via objects)")}</span>
                </div>
              ))}
            </dd>
          </>
        )}
        {hop.backup && (
          <>
            <dt>Backup</dt>
            <dd>
              <span className="mono">{hop.backup.prefix}</span> <span className="faint">{hop.backup.reason}</span>
            </dd>
          </>
        )}
        {r?.inner && r.inner.length > 0 && (
          <>
            <dt>{L("Teile anders", "Parts differ")}</dt>
            <dd>{r.inner.map((x) => `${x.prefix} ${L("über", "via")} ${x.via}`).join(", ")}</dd>
          </>
        )}
      </dl>
      <div className="drawer-h">{L("Routen zum Ziel in diesem Kontext", "Routes to the destination in this context")}</div>
      <ErrorBox error={error} />
      {rows === null ? (
        <Loading />
      ) : rows.length === 0 ? (
        <span className="faint">–</span>
      ) : (
        <table className="compact" id="route-drawer-routes">
          <thead>
            <tr>
              <th>{L("Tabelle", "Table")}</th>
              <th>{L("Präfix", "Prefix")}</th>
              <th>{L("Protokoll", "Protocol")}</th>
              <th>Next-Hop</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => {
              const [cls, label] = ROUTE_TABLE[x.table] ?? ["", x.table];
              return (
                <tr key={x.id}>
                  <td>
                    <span className={`chip ${cls}`}>{label}</span>
                    {!x.best && <div className="faint">{x.table === "static" ? L("aus", "off") : "Backup"}</div>}
                  </td>
                  <td className="mono">{x.prefix}</td>
                  <td>
                    {x.protocol}
                    {x.distance != null && <div className="faint">{L(`Distanz ${x.distance}`, `Distance ${x.distance}`)}</div>}
                  </td>
                  <td>
                    <Hops hops={x.next_hops} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div style={{ marginTop: 12 }}>
        <a href={routesHref(hop, r?.prefix)} target="_blank" rel="noopener noreferrer">
          {L("Im Routing öffnen ↗", "Open in routing ↗")}
        </a>
      </div>
    </Drawer>
  );
}

// Knoten im Seitenpanel: Quellsystem, Kontext, Ein- und Ausgang mit den Interfaces aus dem Inventar
function NodeDrawer({ hop, tenantId, onClose }: { hop: TraceHop; tenantId: number; onClose: () => void }) {
  const parts = hop.parts ?? [hop];
  const wanted = [
    ...new Set(
      parts.flatMap((h) =>
        [h.ingress?.interface, h.egress?.interface].filter((x): x is string => !!x).map((name) => `${h.source_system_id}|${name}`),
      ),
    ),
  ];
  const [ifaces, setIfaces] = useState<Record<string, InvInterface | null>>({});
  const key = wanted.join(",");
  useEffect(() => {
    let alive = true;
    for (const w of wanted) {
      const [sid, ...rest] = w.split("|");
      const name = rest.join("|");
      fetchInventory("interfaces", tenantId, { source_system_id: Number(sid), q: name })
        .then((list) => alive && setIfaces((m) => ({ ...m, [w]: list.find((i) => i.name === name) ?? null })))
        .catch(() => alive && setIfaces((m) => ({ ...m, [w]: null })));
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, key]);
  const iface = (h: TraceHop, p?: TracePort) => {
    if (!p?.interface && !p?.zone) return <Dash />;
    const i = p?.interface ? ifaces[`${h.source_system_id}|${p.interface}`] : null;
    return (
      <>
        {p?.interface && <span className="mono">{p.interface}</span>}
        {p?.zone && (
          <span className="chip" style={{ marginLeft: 4 }}>
            {p.zone}
          </span>
        )}
        {p?.interface && <NewTab href={portHref(p.interface)} />}
        {i && (
          <div className="faint">
            {[
              i.kind,
              i.ips?.join(", "),
              i.vdom && `VDOM ${i.vdom}`,
              i.untagged_vlan != null && `VLAN ${i.untagged_vlan}`,
              i.tagged_vlans?.length ? `VLANs ${i.tagged_vlans.join(", ")}` : null,
              i.description,
              i.enabled === false && L("abgeschaltet", "disabled"),
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
        )}
      </>
    );
  };
  return (
    <Drawer title={hop.label} sub={hop.parts ? L("Verbund", "Parallel group") : hop.source} onClose={onClose}>
      {parts.map((h, k) => (
        <dl className="kv" key={k} id={k === 0 ? "node-drawer" : undefined}>
          {parts.length > 1 && (
            <>
              <dt>Firewall</dt>
              <dd>
                <b>{h.label}</b>
              </dd>
            </>
          )}
          <dt>{L("Quellsystem", "Source system")}</dt>
          <dd>
            {h.source} <span className="faint">· {h.kind === "vrf" ? `VRF ${h.context}` : `VDOM ${h.context}`}</span>
            {h.group && <span className="faint">{L(` · Verbund ${h.group}`, ` · parallel group ${h.group}`)}</span>}
          </dd>
          {h.members.length > 0 && (
            <>
              <dt>{h.kind === "vrf" ? "Border-Leafs" : L("Geräte", "Devices")}</dt>
              <dd>{h.members.join(", ")}</dd>
            </>
          )}
          {h.missing.length > 0 && (
            <>
              <dt>{L("Nicht lesbar", "Not readable")}</dt>
              <dd>
                <Missing items={h.missing} />
              </dd>
            </>
          )}
          <dt>{L("Eingang", "Incoming")}</dt>
          <dd>
            {iface(h, h.ingress)}
            <div className="faint">{h.via}</div>
          </dd>
          <dt>{L("Ausgang", "Outgoing")}</dt>
          <dd>
            {iface(h, h.egress)}
            {h.egress?.next_hop && (
              <div className="mono">
                → {h.egress.next_hop}
                <Names r={h.egress} />
              </div>
            )}
          </dd>
        </dl>
      ))}
      <a href={routesHref(hop)} target="_blank" rel="noopener noreferrer">
        {L("Routen dieses Knotens im Routing ↗", "Routes of this node in routing ↗")}
      </a>
    </Drawer>
  );
}

// Objekt der Policy nach Namen (Quelle/Ziel auf dem Weg): das Objekt-Detail wie in „Objekte“
function ObjectByName({ name, sid, tenantId, onClose }: { name: string; sid: number; tenantId: number; onClose: () => void }) {
  const [id, setId] = useState<number | null | undefined>(undefined);
  useEffect(() => {
    setId(undefined);
    fetchObjects(tenantId, { q: name, source_system_id: sid, limit: 20 })
      .then((p) => setId(p.rows.find((o) => o.name === name)?.id ?? null))
      .catch(() => setId(null));
  }, [tenantId, name, sid]);
  if (id) return <ObjectDrawer id={id} onClose={onClose} onGo={openTab} />;
  return (
    <Drawer title={name} sub={L("Objekt", "Object")} onClose={onClose}>
      {id === undefined ? (
        <Loading />
      ) : (
        <Note>
          {L("Objekt nicht gefunden – ", "Object not found – ")}
          <a href={objectHref(name)} target="_blank" rel="noopener noreferrer">
            {L("in Objekte suchen ↗", "search in objects ↗")}
          </a>
        </Note>
      )}
    </Drawer>
  );
}

function PathDetailDrawer({
  d,
  tenantId,
  systemName,
  dst,
  onClose,
  onOpen,
}: {
  d: DetailRef;
  tenantId: number;
  systemName: (id: number) => string;
  dst?: string;
  onClose: () => void;
  onOpen: (d: DetailRef) => void;
}) {
  switch (d.kind) {
    case "rule":
      return (
        <RuleDetailDrawer ruleId={d.id} systemName={systemName} onClose={onClose} onGo={openTab} onRule={(id) => onOpen({ kind: "rule", id })} />
      );
    case "nat":
      return <NatDrawer key={d.id} id={d.id} onClose={onClose} onGo={openTab} />;
    case "object":
      return <ObjectByName name={d.name} sid={d.sid} tenantId={tenantId} onClose={onClose} />;
    case "route":
      return <RouteDrawer hop={d.hop} tenantId={tenantId} dst={dst} onClose={onClose} />;
    case "node":
      return <NodeDrawer hop={d.hop} tenantId={tenantId} onClose={onClose} />;
  }
}

// Anbindung als Quelle bzw. Ziel (ADR 0019 Ergänzung): Firewalls mit Ein- bzw. Ausgang, Netze und die
// geprüfte Adresse; ein anderes Netz bzw. eine Firewall wählen sucht gleich neu
function ExitEndCard({
  side,
  end,
  onPick,
}: {
  side: "src" | "dst";
  end: TraceExitEnd;
  onPick: (p: { net: string; fw: number | null }) => void;
}) {
  const internet = end.address_kind === "internet" || end.address_kind === "unknown";
  const chosen = end.address_kind === "given" ? (end.address ?? "") : "";
  const options = [...end.nets, ...(end.internet && !end.nets.includes(end.internet) ? [end.internet] : [])];
  return (
    <div className="card exit-end" id={`trace-exit-${side}`}>
      <div className="card-head">
        <h2>
          {side === "src" ? L("Quelle: Anbindung", "Source: exit") : L("Ziel: Anbindung", "Destination: exit")} {end.label}
        </h2>
        <span className="chip info">{EXIT_KIND[end.kind] ?? end.kind}</span>
        <span className="faint">{end.manual ? L("von Hand erfasst", "captured manually") : L("erkannt", "detected")}</span>
        <NewTab href={EXITS_HREF} />
      </div>
      <div className="card-body pad">
        <dl className="kv tight">
          <dt>Firewalls</dt>
          <dd>
            {end.firewalls.length
              ? end.firewalls.map((f) => `${f.label}${f.interface || f.zone ? ` (${[f.interface, f.zone].filter(Boolean).join(", ")})` : ""}`).join(" · ")
              : L("keine gelesene Firewall", "no firewall read")}
          </dd>
          <dt>{L("Geprüft mit", "Checked with")}</dt>
          <dd>
            <span className="mono">{end.address ?? "–"}</span>{" "}
            {internet ? (
              <span
                className="chip"
                title={L(
                  "Öffentliche Test-Adresse aus dem Dokumentationsbereich (RFC 5737/3849), an keinem gelesenen Gerät angeschlossen",
                  "Public test address from the documentation range (RFC 5737/3849), connected to no device read",
                )}
              >
                {end.address_kind === "unknown"
                  ? L("keine Netze bekannt – beliebige Adresse", "no networks known – any address")
                  : L("beliebige Adresse im Internet", "any address on the Internet")}
              </span>
            ) : end.address_kind === "given" ? (
              <span className="faint">{L("gewählt", "chosen")}</span>
            ) : (
              <span className="faint">{L("Netz der Anbindung, als Ganzes", "network of the exit, as a whole")}</span>
            )}
          </dd>
          <dt>{L("Netze", "Networks")}</dt>
          <dd>
            {[end.default ? L("Default-Route", "default route") : "", ...end.nets].filter(Boolean).join(", ") || "–"}
            {end.nets_total > end.nets.length ? ` (+${end.nets_total - end.nets.length})` : ""}
            {end.given && <span className="faint"> · {L("von Hand", "manual")}</span>}
          </dd>
          {end.routers.length > 0 && (
            <>
              <dt>Router</dt>
              <dd className="mono">{end.routers.join(", ")}</dd>
            </>
          )}
        </dl>
        <div className="filterbar" style={{ marginTop: 6 }}>
          {options.length > 1 || (options.length === 1 && options[0] !== end.address) ? (
            <select
              className="sel"
              id={`trace-exit-${side}-net`}
              value={chosen}
              onChange={(e) => onPick({ net: e.target.value, fw: end.system })}
              title={L("Mit einem anderen Netz der Anbindung prüfen", "Check with another network of the exit")}
            >
              <option value="">{L("Netz: automatisch", "Network: automatic")}</option>
              {options.map((n) => (
                <option key={n} value={n}>
                  {n === end.internet ? L(`${n} (beliebige Adresse im Internet)`, `${n} (any address on the Internet)`) : n}
                </option>
              ))}
            </select>
          ) : null}
          {end.choices.length > 1 && (
            <select
              className="sel"
              id={`trace-exit-${side}-fw`}
              value={end.system ?? ""}
              onChange={(e) => onPick({ net: chosen, fw: e.target.value ? Number(e.target.value) : null })}
              title={L("Anbindung an mehreren Firewalls: alle bzw. eine davon", "Exit at several firewalls: all or one of them")}
            >
              <option value="">{L(`alle ${end.choices.length} Firewalls`, `all ${end.choices.length} firewalls`)}</option>
              {end.choices.map((c) => (
                <option key={c.source_system_id} value={c.source_system_id}>
                  {c.source}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>
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
  onExplain,
}: {
  path: TracePath;
  index: number;
  title: string;
  compare?: boolean; // mehrere Wege: Länge im Vergleich zeigen
  onCapture?: (c: Capture) => void;
  onExplain?: () => void; // „Warum dieser Weg?“ (0.109.0)
}) {
  const [cls, label] = END[path.end.kind] ?? ["", path.end.kind];
  const last = path.hops[path.hops.length - 1];
  const out = last?.egress;
  // Weg endet an einem Next-Hop ohne gelesenes Gerät: als Anbindung erfassen (Internet, MPLS, …)
  const capturable = onCapture && out?.next_hop && (path.end.kind === "unknown" || path.end.kind === "external");
  // je Hop „mehr“ (ADR 0019 Ergänzung): aufgeklappte Hops
  const [more, setMore] = useState<Set<number>>(() => new Set());
  const toggle = (i: number) =>
    setMore((s) => {
      const next = new Set(s);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
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
        {path.exit_start && (
          <span className="chip info" title={L("Start an dieser Firewall der Anbindung", "Start at this firewall of the exit")}>
            {L("ab", "from")} {path.exit_start}
          </span>
        )}
        {path.via_exit != null &&
          (path.via_exit ? (
            <span className="chip ok via-exit" title={L("Der Weg geht über die gewählte Anbindung hinaus", "The path leaves via the chosen exit")}>
              {L("über die Anbindung", "via the exit")}
            </span>
          ) : (
            <span
              className="chip warn via-exit"
              title={L("Das Routing führt das Ziel nicht über die gewählte Anbindung", "The routing does not take the destination via the chosen exit")}
            >
              {L("nicht über die Anbindung", "not via the exit")}
            </span>
          ))}
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
        {onExplain && (
          <button
            className="btn btn-ghost btn-sm trace-explain"
            style={{ marginLeft: "auto" }}
            title={L(
              "Hop für Hop in Sätzen: Eingang, Policy-Routen, alle Routen zum Ziel mit Grund (gewählt bzw. verworfen), Next-Hop, Ende – und „Erwartet über …“",
              "Hop by hop in sentences: incoming, policy routes, all routes to the destination with reason (chosen or rejected), next-hop, end – and “Expected via …”",
            )}
            onClick={onExplain}
          >
            {L("Warum dieser Weg?", "Why this path?")}
          </button>
        )}
        {capturable && (
          <button
            className="btn btn-sm"
            style={onExplain ? undefined : { marginLeft: "auto" }}
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
              const isOpen = more.has(i);
              return (
                <Fragment key={i}>
                {parts.map((h, k) => {
                const possible = (h.policy_routes ?? []).filter((p) => !p.certain);
                return (
                  <tr key={`${i}-${k}`} className={isOpen ? "hop-open" : undefined}>
                    {k === 0 && (
                      <td className="num" rowSpan={parts.length}>
                        {i + 1}
                        <div>
                          <button
                            type="button"
                            className="linkbtn hop-more-toggle"
                            aria-expanded={isOpen}
                            title={L(
                              "Mehr zu diesem Hop: Ein-/Ausgang mit Zone, Route, entscheidende Regel mit Quelle/Ziel/Dienst, NAT",
                              "More on this hop: incoming/outgoing with zone, route, deciding rule with source/destination/service, NAT",
                            )}
                            onClick={() => toggle(i)}
                          >
                            {isOpen ? L("weniger", "less") : L("mehr", "more")}
                          </button>
                        </div>
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
                      <Opener
                        href={routesHref(h)}
                        detail={{ kind: "node", hop: h }}
                        className="hop-node"
                        title={L(
                          "Knoten mit Ein- und Ausgang im Seitenpanel – Ctrl-Klick: Routing in neuem Tab",
                          "Node with incoming and outgoing interface in the side panel – Ctrl-click: routing in a new tab",
                        )}
                      >
                        <b>{h.label}</b>
                      </Opener>{" "}
                      {h.kind === "firewall" && <span className="chip">Firewall</span>}
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
                    <td>{h.ingress?.interface || h.ingress?.zone ? <HopPort p={h.ingress} hop={h} /> : <Dash />}</td>
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
                            DNAT
                          </span>{" "}
                          <Opener href={natHref(h.nat.name)} detail={{ kind: "nat", id: h.nat.id }} className="mono">
                            {h.nat.name}
                          </Opener>{" "}
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
                          {vipMeta(h.nat) && <div className="faint">{vipMeta(h.nat)}</div>}
                          {h.nat.servers?.length ? (
                            <VipServers servers={h.nat.servers} via={h.nat.to.length > 1 ? h.nat.to[0] : undefined} sid={h.source_system_id} />
                          ) : (
                            h.nat.to.length > 1 && (
                              <div className="faint">{L("Weg über den ersten Real Server", "Path via the first real server")}</div>
                            )
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
                            Load Balancer
                          </span>{" "}
                          <Opener href={natHref(h.lb.name)} detail={{ kind: "nat", id: h.lb.id }} className="mono">
                            {h.lb.name}
                          </Opener>{" "}
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
                              !h.lb.members?.length &&
                                (h.lb.to?.length ?? 0) > 1 &&
                                L("Weg über den ersten aktiven Server", "Path via the first active server"),
                              vipMeta(h.lb),
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </div>
                          <VipServers
                            servers={h.lb.members}
                            via={(h.lb.to?.length ?? 0) > 1 ? h.lb.to?.[0] : undefined}
                            total={h.lb.servers}
                          />
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
                          <Opener
                            href={routesHref(h, h.route.prefix)}
                            detail={{ kind: "route", hop: h }}
                            className="hop-route"
                            title={L(
                              "Route mit Next-Hops und allen Routen zum Ziel im Seitenpanel – Ctrl-Klick: Routing in neuem Tab",
                              "Route with next-hops and all routes to the destination in the side panel – Ctrl-click: routing in a new tab",
                            )}
                          >
                            <b className="mono">{h.route.prefix}</b>
                          </Opener>
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
                      {h.egress ? <HopPort p={h.egress} hop={h} /> : <Dash />}
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
                })}
                {isOpen && (
                  <tr className="hop-more">
                    <td />
                    <td colSpan={5}>
                      <HopMore hop={hop} />
                    </td>
                  </tr>
                )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Quelle bzw. Ziel: IP, Netz, Name oder Anbindung; `hit` = gewählter Treffer der Namenssuche, bei
// einer Anbindung `over` = ein anderes ihrer Netze und `fw` = nur diese Firewall (ADR 0019 Ergänzung)
type Endpoint = { text: string; hit: NameHit | null; over?: string; fw?: number | null };
const endpointOf = (text?: string): Endpoint => ({ text: text ?? "", hit: null });

// Aus einer Regel (0.112.0): ihre Quellen, Ziele und Dienste zur Wahl - Klick sucht mit diesem Wert neu
function RuleChoice({
  r,
  src,
  dst,
  service,
  onPick,
}: {
  r: RulePathSearch;
  src: string;
  dst: string;
  service: string;
  onPick: (p: { src?: PathValue | null; dst?: PathValue | null; service?: string }) => void;
}) {
  const side = (label: string, values: PathValue[], now: string, key: "src" | "dst") => (
    <span className="rule-choice-side" data-side={key}>
      <b>{label}</b>{" "}
      {values.length === 0 ? (
        <span className="faint">
          any
          {key === "src" && r.src_exit
            ? L(` – über die Anbindung „${r.src_exit.name}“ (${r.src_exit.ports.join(", ")})`, ` – via the exit “${r.src_exit.name}” (${r.src_exit.ports.join(", ")})`)
            : key === "src" && r.start
              ? L(" – Start an der Firewall der Regel", " – start at the firewall of the rule")
              : ""}
        </span>
      ) : (
        values.map((v) => (
          <button
            key={v.value}
            type="button"
            className={`chip ${v.value === now ? "info" : ""}`}
            onClick={() => onPick({ [key]: v })}
            title={v.name ?? v.value}
          >
            {v.name ? `${v.name} (${v.value})` : v.value}
          </button>
        ))
      )}
    </span>
  );
  return (
    <div className="card" id="rule-choice" style={{ marginBottom: 10 }}>
      <div className="card-body pad" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "baseline" }}>
        <span className="faint">{L(`Aus Regel „${r.name}“:`, `From rule “${r.name}”:`)}</span>
        {side(L("Quelle", "Source"), r.src, src, "src")}
        {side(L("Ziel", "Destination"), r.dst, dst, "dst")}
        <span className="rule-choice-side" data-side="service">
          <b>{L("Dienst", "Service")}</b>{" "}
          {r.services.length === 0 ? (
            <span className="faint">{L("alle", "all")}</span>
          ) : (
            r.services.map((v) => (
              <button key={v} type="button" className={`chip ${v === service ? "info" : ""}`} onClick={() => onPick({ service: v })}>
                {v}
              </button>
            ))
          )}
        </span>
      </div>
    </div>
  );
}

// Eingabe aus der URL (#wege?src=…&src_name=…&src_exit=…) wie gewählt wiederherstellen
function endpointFrom(p: ViewParams | undefined, side: "src" | "dst"): Endpoint {
  const value = p?.[side] ?? "";
  const name = p?.[`${side}_name`];
  const exit = p?.[`${side}_exit`];
  if (exit) {
    const [ref, fw] = exit.split("@");
    const hit: NameHit = { ip: value, name: name ?? ref, kind: "exit", type: "exit", net: null, source: null, detail: null, exit: ref };
    return { text: name ?? ref, hit, over: value || undefined, fw: fw ? Number(fw) : null };
  }
  if (value && name && name !== value) {
    const net = isNet(value);
    return {
      text: name,
      hit: { ip: value, name, kind: net ? "net" : "ip", type: net ? "net" : "host", net: net ? value : null, source: null, detail: null },
    };
  }
  return endpointOf(value);
}
const exitRef = (ep: Endpoint) => (ep.hit?.exit ? `${ep.hit.exit}${ep.fw ? `@${ep.fw}` : ""}` : undefined);

const HIT_KIND: Record<NameHit["kind"], string> = {
  ip: "IP",
  ipam: "IPAM",
  object: L("Objekt", "Object"),
  vm: "VM",
  net: L("Netz", "Network"),
  subnet: L("Inventar", "Inventory"),
  exit: L("Anbindung", "Exit"),
};
const PAGE = 50;
type HitType = "" | "host" | "net" | "exit";

const isIp = (v: string) => /^\d{1,3}(\.\d{1,3}){3}$/.test(v.trim()) || /^[0-9a-f]*:[0-9a-f:.]+$/i.test(v.trim());
// Netz direkt (10.10.0.0/23): geht als Ganzes in die Weg-Suche (0.39.0)
const isNet = (v: string) => {
  const [ip, len, ...rest] = v.trim().split("/");
  return rest.length === 0 && !!len && /^\d{1,3}$/.test(len) && isIp(ip);
};
const hitKey = (h: NameHit) => (h.type === "exit" ? `exit:${h.exit}` : h.type === "net" ? `net:${h.net}` : h.ip);

// Treffer der Namenssuche: Name, IP bzw. Netz und woher; Anbindung mit Firewalls und Netzen
function HitRow({ h, onPick }: { h: NameHit; onPick: (h: NameHit) => void }) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={false}
      className={h.type === "exit" ? "hit-exit" : undefined}
      onMouseDown={(e) => {
        e.preventDefault();
        onPick(h);
      }}
    >
      <b>{h.name ?? h.net ?? h.ip}</b>{" "}
      {h.type === "exit" ? (
        h.exit_kind && <span className="chip info">{EXIT_KIND[h.exit_kind] ?? h.exit_kind}</span>
      ) : (
        <span className="mono">{h.type === "net" ? h.net : h.ip}</span>
      )}
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
  const all = (res?.counts.host ?? 0) + (res?.counts.net ?? 0) + (res?.counts.exit ?? 0);
  const shown = open && res && all > 0;
  const hosts = res?.hits.filter((h) => h.type === "host") ?? [];
  const nets = res?.hits.filter((h) => h.type === "net") ?? [];
  const exits = res?.hits.filter((h) => h.type === "exit") ?? [];
  // Überschriften je Art nur, wenn mehrere Arten zu sehen sind
  const groups = [hosts, nets, exits].filter((g) => g.length > 0).length > 1 && type === "";
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
        <div className="faint mono endpoint-hit" style={{ fontSize: 11.5, marginTop: 2 }} title={value.hit.name ?? ""}>
          {value.hit.type === "exit"
            ? L("= Anbindung", "= exit") +
              (value.over
                ? L(` · Netz ${value.over}`, ` · network ${value.over}`)
                : value.hit.internet
                  ? L(" · beliebige Adresse im Internet", " · any address on the Internet")
                  : value.hit.ip
                    ? ` · ${value.hit.ip}`
                    : "")
            : value.hit.type === "net"
              ? L(`= Netz ${value.hit.net}`, `= network ${value.hit.net}`)
              : `= ${value.hit.ip}`}
        </div>
      )}
      {shown && (
        <div className="menu suggest" role="listbox" aria-label={L("Treffer", "Matches")}>
          <div className="suggest-head" onMouseDown={(e) => e.preventDefault()}>
            <div className="seg">
              {seg("", L("Alle", "All"), all)}
              {seg("host", "Hosts", res.counts.host)}
              {seg("net", L("Netze", "Networks"), res.counts.net)}
              {(res.counts.exit ?? 0) > 0 && seg("exit", L("Anbindungen", "Exits"), res.counts.exit ?? 0)}
            </div>
            <span className="faint">
              {res.hits.length} {L("von", "of")} {res.total}
              {res.capped ? L(" – mehr gelesen als gezeigt, genauer suchen", " – more read than shown, refine the search") : ""}
            </span>
          </div>
          {hosts.length > 0 && groups && <div className="suggest-group">Hosts</div>}
          {hosts.map((h) => (
            <HitRow key={hitKey(h)} h={h} onPick={pick} />
          ))}
          {nets.length > 0 && groups && <div className="suggest-group">{L("Netze", "Networks")}</div>}
          {nets.map((h) => (
            <HitRow key={hitKey(h)} h={h} onPick={pick} />
          ))}
          {exits.length > 0 && groups && <div className="suggest-group">{L("Anbindungen", "Exits")}</div>}
          {exits.map((h) => (
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
// Ein Netz geht als Ganzes in die Weg-Suche (0.39.0, `net` für die Anzeige). Eine Anbindung geht als
// Referenz (`exit`) in die Weg-Suche, `ip` nur, wenn ein anderes ihrer Netze gewählt ist; `addr` =
// ihre Adresse aus der Namenssuche (ohne Quelle: Route je Kontext).
type Resolved = { ip: string; net?: string | null; exit?: string; addr?: string };
async function toIp(
  tenantId: number,
  ep: Endpoint,
  what: string,
  set: (v: Endpoint) => void,
  choose: (res: NameResult) => void,
): Promise<Resolved> {
  const text = ep.text.trim();
  if (!text) return { ip: "" };
  if (ep.hit?.type === "exit") return { ip: ep.over ?? "", exit: exitRef(ep), addr: ep.hit.ip };
  if (isIp(text)) return { ip: text };
  if (isNet(text)) return { ip: text, net: text };
  if (ep.hit) return { ip: ep.hit.ip, net: ep.hit.net };
  const res = await resolveName(tenantId, text, { limit: PAGE });
  const keys = new Set(res.hits.map(hitKey));
  if (keys.size === 1 && res.total === 1) {
    const hit = res.hits[0];
    set({ text, hit });
    if (hit.type === "exit") return { ip: "", exit: hit.exit, addr: hit.ip };
    return { ip: hit.ip, net: hit.net };
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

// Gefragt: IP bzw. Netz, Name der Eingabe, Anbindung (Referenz)
type Asked = { ip: string; name?: string; net?: string | null; exit?: string };

// URL der Suche (ADR 0019 Ergänzung): zuerst src, dst, service (wie die Links aus der Applikations-Doku),
// dann Start, Backup-Wege, die Namen der Eingaben und die Anbindungen
function searchUrl(src: Asked, dst: Asked, service: string, start: string, slack: string, startPort = ""): string {
  const p = new URLSearchParams();
  const put = (k: string, v?: string | null) => {
    if (v) p.set(k, v);
  };
  put("src", src.ip);
  put("dst", dst.ip);
  put("service", service.trim());
  put("start", start);
  put("start_port", start ? startPort : "");
  if (slack !== "1") p.set("slack", slack === "" ? "off" : slack);
  put("src_name", src.name);
  put("dst_name", dst.name);
  put("src_exit", src.exit);
  put("dst_exit", dst.exit);
  return hashOf("wege", Object.fromEntries(p.entries()));
}

// Weg-Suche: Quelle (IP bzw. Name) und/oder Startpunkt, dazu das Ziel. Ohne beides: je Kontext
// die Route zum Ziel.
function PathSearch({
  tenantId,
  nodes,
  onCapture,
  initial,
  canEdit = false,
}: {
  tenantId: number;
  nodes: RoutingNode[];
  onCapture?: (c: Capture) => void;
  initial?: ViewParams; // aus einem Link (#wege?src=…&dst=…&service=…), z. B. Applikations-Doku
  canEdit?: boolean; // Operator: Finding von Hand erfassen (0.109.0)
}) {
  const [src, setSrc] = useState<Endpoint>(() => endpointFrom(initial, "src"));
  const [start, setStart] = useState(initial?.start ?? "");
  // Eingang am Startpunkt (0.113.1): das Interface der Regel, deren Quelle any ist
  const [startPort, setStartPort] = useState(initial?.start_port ?? "");
  const [dst, setDst] = useState<Endpoint>(() => endpointFrom(initial, "dst"));
  const [choices, setChoices] = useState<{ src?: NameResult; dst?: NameResult }>({});
  const [trace, setTrace] = useState<TraceResult | null>(null);
  const [asked, setAsked] = useState<{ src: Asked; dst: Asked } | null>(null);
  // Backup-Wege: Hops mehr als der kürzeste aktive; "" = keine (in der URL „off“)
  const [slack, setSlack] = useState(
    initial?.slack === "off" ? "" : ["0", "1", "2"].includes(initial?.slack ?? "") ? initial!.slack : "1",
  );
  // tcp/443, udp/53 bzw. Service-Objekt/ACI-Filter (in Links auch `svc`)
  const [service, setService] = useState(initial?.service ?? initial?.svc ?? "");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  // Details am Weg im Seitenpanel (ADR 0019 Ergänzung)
  const [detail, setDetail] = useState<DetailRef | null>(null);
  // „Warum dieser Weg?“ (0.109.0): dieselben Eingaben wie die letzte Suche, gezeigter Weg
  const [asks, setAsks] = useState<ExplainQuery | null>(null);
  const [explain, setExplain] = useState<number | null>(null);
  const systemName = (id: number) => nodes.find((n) => n.source_system_id === id)?.source ?? `#${id}`;
  const srcNow = src;
  const dstNow = dst;
  const startNow = start;
  const startPortNow = startPort;
  // `over`: Werte statt des Zustands (Richtung tauschen sucht sofort mit den getauschten)
  const run = async (over?: { src: Endpoint; dst: Endpoint; start: string; service?: string; startPort?: string }) => {
    const src = over?.src ?? srcNow;
    const dst = over?.dst ?? dstNow;
    const svc = over?.service ?? service;
    if (!dst.text.trim()) return;
    setBusy(true);
    setError(null);
    setTrace(null);
    setChoices({});
    setDetail(null);
    setExplain(null);
    try {
      const [s, d] = await Promise.all([
        toIp(tenantId, src, L("Quelle", "Source"), setSrc, (h) => setChoices((c) => ({ ...c, src: h }))),
        toIp(tenantId, dst, L("Ziel", "Destination"), setDst, (h) => setChoices((c) => ({ ...c, dst: h }))),
      ]);
      // eine Anbindung als Quelle bestimmt den Start selbst
      const start = s.exit ? "" : over ? over.start : startNow;
      const named = (ep: Endpoint, r: Resolved): Asked => {
        const t = ep.text.trim();
        return { ip: r.ip, net: r.net, exit: r.exit, name: t && t !== r.ip && t !== r.net ? t : undefined };
      };
      const a = { src: named(src, s), dst: named(dst, d) };
      setAsked(a);
      // Eingaben in der URL (#wege?src=…&dst=…&service=…): Zurück bzw. ein neuer Tab stellt die Suche wieder her
      const port = start && start === (over?.start ?? startNow) ? (over?.startPort ?? startPortNow) : "";
      window.history.replaceState(null, "", searchUrl(a.src, a.dst, svc, start, slack, port));
      if (s.ip || start || s.exit) {
        const sl = slack === "" ? 0 : Number(slack);
        setAsks({ dst: d.ip, src: s.ip, start, startPort: port, service: svc, slack: sl, srcExit: s.exit, dstExit: d.exit });
        setTrace(
          await traceRoute(tenantId, {
            dst: d.ip,
            src: s.ip,
            start,
            startPort: port,
            service: svc,
            slack: slack === "" ? 0 : Number(slack),
            srcExit: s.exit,
            dstExit: d.exit,
          }),
        );
      } else {
        // 0.113.1 (Anwender: „kein Source angegeben … Liste aller Routen zu der IP, dies braucht es nicht“)
        throw new Error(
          L(
            "Quelle oder Startpunkt angeben – ohne sie sucht die Weg-Suche keinen Weg (die Routen je Gerät stehen unter Routing → Routen).",
            "Specify a source or a starting point – without them the path search finds no path (the routes per device are under Routing → Routes).",
          ),
        );
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
    if (trace && next.dst.text.trim()) void run(next);
  };
  // Anbindung: ein anderes ihrer Netze bzw. eine ihrer Firewalls wählen und gleich neu suchen
  const pickExit = (side: "src" | "dst") => (p: { net: string; fw: number | null }) => {
    const ep = side === "src" ? srcNow : dstNow;
    const next = { ...ep, over: p.net || undefined, fw: p.fw };
    if (side === "src") setSrc(next);
    else setDst(next);
    void run({ src: side === "src" ? next : srcNow, dst: side === "dst" ? next : dstNow, start: startNow });
  };
  // Link mit Ziel (auch Zurück zu einer Suche): gleich suchen (einmal beim Öffnen)
  useEffect(() => {
    if (initial?.dst || initial?.dst_exit) void run();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Knopf „Weg-Suche“ an einer Regel (0.112.0, #wege?rule=<id>): Quelle, Ziel und Dienst der Regel, die erste Wahl gleich
  // suchen; die übrigen Werte der Regel zur Auswahl
  const [fromRule, setFromRule] = useState<RulePathSearch | null>(null);
  const pickRule = (r: RulePathSearch, pick: { src?: PathValue | null; dst?: PathValue | null; service?: string }) => {
    const ep = (v: PathValue | null | undefined) =>
      v ? endpointFrom({ src: v.value, ...(v.name ? { src_name: v.name } : {}) }, "src") : endpointOf("");
    // Quelle any (0.113.1, Anwender: „hier jeweils die Quelle mindestens angeben“): die Anbindung am Eingang der Regel,
    // sonst der Start an ihrer Firewall – der Weg geht so bis zum Ziel, auch durch eine VIP (DNAT)
    const anySrc = "src" in pick && !pick.src && r.src.length === 0;
    const ingress = anySrc && r.src_exit ? endpointFrom({ src_exit: r.src_exit.ref, src_name: r.src_exit.name }, "src") : null;
    const next = {
      src: ingress ?? ("src" in pick ? ep(pick.src) : srcNow),
      dst: "dst" in pick ? ep(pick.dst) : dstNow,
      start: anySrc && !ingress && r.start ? r.start : "src" in pick ? "" : startNow,
      startPort: anySrc && !ingress && r.start ? (r.ports ?? [])[0] ?? "" : "src" in pick ? "" : startPortNow,
      service: pick.service ?? service,
    };
    setSrc(next.src);
    setDst(next.dst);
    setStart(next.start);
    setStartPort(next.startPort);
    setService(next.service);
    setFromRule(r);
    if (next.dst.text.trim()) void run(next);
  };
  useEffect(() => {
    const id = Number(initial?.rule);
    if (!id) return;
    fetchRulePathSearch(id)
      .then((r) => pickRule(r, { src: r.src[0] ?? null, dst: r.dst[0] ?? null, service: r.services[0] ?? "" }))
      .catch(setError);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const label = (a: Asked, end?: TraceExitEnd) =>
    end
      ? `${end.label} (${end.address ?? "?"}${end.address_kind === "internet" ? L(" – beliebige Adresse im Internet", " – any address on the Internet") : ""})`
      : a.net
        ? `${a.name ? `${a.name} = ` : ""}${L("Netz", "network")} ${a.net}`
        : a.name
          ? `${a.name} (${a.ip})`
          : a.ip;
  const srcIsExit = src.hit?.type === "exit";
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
            "Quelle als IP, Netz bzw. Name/FQDN (IPAM, Objekte der Firewalls, VMs, Subnetze): der Weg beginnt dort, wo sie angeschlossen ist (bzw. am Startpunkt); ein Netz gilt als Ganzes. Auch eine Anbindung („Internet“, „Anbindung“, Interface oder Firewall): Start an ihrer Firewall mit ihrem Eingang, Quelle = ihr Netz bzw. eine beliebige Adresse im Internet",
            "Source as IP, network or name/FQDN (IPAM, firewall objects, VMs, subnets): the path starts where it is connected (or at the starting point); a network counts as a whole. Also an exit (“Internet”, “exit”, interface or firewall): start at its firewall with its incoming interface, source = its network or any address on the Internet",
          )}
          forceOpen={choices.src}
        />
        <select
          className="sel"
          value={srcIsExit ? "" : start}
          onChange={(e) => {
            setStart(e.target.value);
            setStartPort("");
          }}
          disabled={srcIsExit}
          title={
            srcIsExit
              ? L("Quelle ist eine Anbindung: der Weg beginnt an ihren Firewalls", "Source is an exit: the path starts at its firewalls")
              : L(
                  "Startpunkt: Firewall, VDOM bzw. ACI-VRF – ohne Wahl ergibt er sich aus der Quelle",
                  "Starting point: firewall, VDOM or ACI VRF – if none is chosen, it follows from the source",
                )
          }
        >
          <option value="">{srcIsExit ? L("Start: an der Anbindung", "Start: at the exit") : L("Start: aus der Quelle", "Start: from the source")}</option>
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
            "Ziel als IP, Netz bzw. Name/FQDN; ein Netz gilt als Ganzes (Route und Regeln für das ganze Netz). Auch eine Anbindung: Ziel = ihr Netz bzw. eine beliebige Adresse im Internet, je Weg, ob er über sie hinausgeht",
            "Destination as IP, network or name/FQDN; a network counts as a whole (route and rules for the entire network). Also an exit: destination = its network or any address on the Internet, per path whether it leaves via the exit",
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
      {fromRule && <RuleChoice r={fromRule} src={src.hit?.ip ?? src.text} dst={dst.hit?.ip ?? dst.text} service={service} onPick={(p) => pickRule(fromRule, p)} />}
      <ErrorBox error={error} prefix={L("Weg suchen", "Search path")} />
      <DetailCtx.Provider value={setDetail}>
      {trace && (
        <>
          <div className="faint" style={{ margin: "0 0 8px" }} id="trace-asked">
            {asked && (
              <>
                {asked.src.ip || trace.exits?.src
                  ? L(`Quelle ${label(asked.src, trace.exits?.src)} → `, `Source ${label(asked.src, trace.exits?.src)} → `)
                  : ""}
                {L("Ziel", "Destination")} {label(asked.dst, trace.exits?.dst)}
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
                {trace.exits?.src && <ExitEndCard side="src" end={trace.exits.src} onPick={pickExit("src")} />}
                {trace.exits?.dst && <ExitEndCard side="dst" end={trace.exits.dst} onPick={pickExit("dst")} />}
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
                    onExplain={asks ? () => setExplain(trace.paths.indexOf(p)) : undefined}
                  />
                ))}
              </>
            );
          })()}
        </>
      )}
      </DetailCtx.Provider>
      {asks && explain !== null && (
        <PathExplainDrawer
          tenantId={tenantId}
          query={asks}
          pathIndex={explain}
          nodes={nodes}
          canCapture={canEdit}
          onClose={() => setExplain(null)}
        />
      )}
      {detail && (
        <PathDetailDrawer
          d={detail}
          tenantId={tenantId}
          systemName={systemName}
          dst={asked?.dst.ip || trace?.exits?.dst?.address || undefined}
          onClose={() => setDetail(null)}
          onOpen={setDetail}
        />
      )}
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
            Laufs. Quelle und Ziel als IP, Netz oder Name aus IPAM, Firewall-Objekten, VMs und Subnetzen – oder als
            Anbindung (Internet, MPLS …). Details am Weg öffnen im Seitenpanel, „↗“ bzw. Ctrl-Klick in einem neuen Tab; die
            Suche steht in der Adresse und bleibt beim Zurückgehen erhalten.
          </>,
          <>
            Path of a connection in “{tenantLabel}” hop by hop – through firewalls (policies, NAT) and ACI (contracts between
            the EPGs), with active and backup paths. For troubleshooting and checking work; read-only, state of the last run.
            Source and destination as IP, network or name from IPAM, firewall objects, VMs and subnets – or as an exit
            (Internet, MPLS …). Details on the path open in the side panel, “↗” or Ctrl-click in a new tab; the search is kept
            in the address and restored when going back.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("Routing laden", "Load routing")} />
      {nodes === null ? <Loading /> : <PathSearch tenantId={tenantId} nodes={nodes} onCapture={canEdit ? capture : undefined} initial={params} canEdit={canEdit} />}
    </>
  );
}
