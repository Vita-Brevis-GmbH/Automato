// Routing & Wege (ADR 0019): gelesene Routing-Tabellen je Firewall bzw. FortiGate-VDOM und je
// ACI-VRF auf den Border-Leafs, BGP-Pfade (auch Backup), Nachbarn, HA/Session-Sync,
// Policy-Routen/SD-WAN und L3Outs. „Weg für IP“ verfolgt den Weg Hop für Hop ab der Quelle
// bzw. einem gewählten Startpunkt (Stufe 3, erste Fassung); IPs mit Namen aus dem IPAM.
import { useEffect, useState } from "react";
import {
  ContextKind,
  createExit,
  deleteExit,
  ExitRouter,
  ExitSuggestion,
  Redundancy,
  fetchExits,
  fetchExitSuggestions,
  fetchHa,
  fetchL3Outs,
  fetchPeers,
  fetchPolicyRoutes,
  fetchRoutes,
  fetchRoutingSummary,
  Labeled,
  lookupRoute,
  NextHop,
  PolicyHit,
  Resolved,
  RouteLookup,
  RoutingNode,
  RoutingSummary,
  TopoHa,
  TopoL3Out,
  TopoPeer,
  TopoPolicyRoute,
  TopoExit,
  TopoExitIn,
  TopoRoute,
  TracePath,
  TracePort,
  TraceResult,
  traceRoute,
  updateExit,
} from "./api";
import { Pager, useDebounced } from "./Objects";
import { Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";

const PAGE = 200;

type Tab = "lookup" | "routes" | "peers" | "ha" | "policy" | "l3out" | "exits";
const TABS: { key: Tab; label: string }[] = [
  { key: "lookup", label: "Weg für IP" },
  { key: "routes", label: "Routen" },
  { key: "peers", label: "BGP-Nachbarn" },
  { key: "ha", label: "HA & Session-Sync" },
  { key: "policy", label: "Policy-Routen & SD-WAN" },
  { key: "l3out", label: "L3Outs (ACI)" },
  { key: "exits", label: "Anbindungen" },
];

const TABLE_LABEL: Record<string, [string, string]> = {
  rib: ["ok", "wirksam"],
  bgp: ["info", "BGP-Pfad"],
  static: ["", "statisch"],
};

const KIND_LABEL: Record<ContextKind, string> = {
  firewall: "Firewall",
  vdom: "VDOM",
  vrf: "VRF (Border-Leaf)",
};

// Name(n) einer IP aus dem IPAM; ohne Namen das Netz (Beschreibung) – blass
export function Names({ r }: { r: Resolved | null | undefined }) {
  if (r?.names?.length) return <b> {r.names.join(", ")}</b>;
  if (r?.network) return <span className="faint" title="kein Name zur IP im IPAM – Netz aus dem IPAM"> {r.network}</span>;
  return null;
}

// Next-Hops: IP (Name aus dem IPAM) über Interface (Zone); ECMP = mehrere Zeilen
export function Hops({ hops }: { hops: NextHop[] | null | undefined }) {
  if (!hops?.length) return <Dash />;
  return (
    <div className="objs">
      {hops.map((h, i) => (
        <span key={i} className="mono">
          {h.ip ?? "direkt"}
          <Names r={h} />
          {h.interface ? <span className="faint"> via {h.interface}</span> : null}
          {h.zone ? <span className="chip" style={{ marginLeft: 4 }}>{h.zone}</span> : null}
        </span>
      ))}
      {hops.length > 1 && (
        <span
          className="chip info"
          style={{ alignSelf: "flex-start" }}
          title="Equal-Cost-Multipath: der Verkehr verteilt sich auf alle Next-Hops"
        >
          ECMP ×{hops.length}
        </span>
      )}
    </div>
  );
}

function Missing({ items }: { items: string[] | null | undefined }) {
  if (!items?.length) return null;
  return (
    <div className="chips">
      {items.map((m) => (
        <span key={m} className="chip warn" title="nicht lesbar – die Aussage für diesen Kontext ist „unbekannt“">
          {m}
        </span>
      ))}
    </div>
  );
}

// System · Kontext: „fw-01 · VDOM root“, eine Firewall nur mit root heisst wie die Firewall,
// ACI „leaf-101 · VRF T:V“ (darunter der APIC); Hostname, wenn er vom Quellsystem abweicht
function where(x: Labeled) {
  const label = x.label || `${x.source ?? x.node} · ${x.context}`;
  const sub = x.display === "vrf" ? x.source : x.node !== x.source ? x.node : null;
  return (
    <>
      <b>{label}</b>
      {sub && <div className="faint">{sub}</div>}
    </>
  );
}

function matchText(match: Record<string, unknown> | null | undefined): string {
  const m = match ?? {};
  const parts: string[] = [];
  const list = (k: string) => (Array.isArray(m[k]) ? (m[k] as unknown[]).map(String) : []);
  if (list("input_device").length) parts.push(`von ${list("input_device").join(", ")}`);
  const src = [...list("src"), ...list("srcaddr")];
  const dst = [...list("dst"), ...list("dstaddr")];
  if (src.length) parts.push(`Quelle ${src.join(", ")}`);
  if (dst.length) parts.push(`Ziel ${dst.join(", ")}`);
  if (m.protocol) parts.push(`Protokoll ${String(m.protocol)}`);
  if (list("ports").length) parts.push(`Port ${list("ports").join("-")}`);
  if (list("service").length) parts.push(`Dienst ${list("service").join(", ")}`);
  return parts.join(" · ") || "alles";
}

function ContextCard({ summary }: { summary: RoutingSummary }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>Gelesene Kontexte</h2>
        <span className="faint">
          {summary.contexts.length} Kontexte · {summary.ha} HA-Cluster · {summary.l3outs} L3Outs
        </span>
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>System · Kontext</th>
              <th>Art</th>
              <th className="num">wirksam</th>
              <th className="num">BGP-Pfade</th>
              <th className="num">statisch</th>
              <th className="num">BGP-Nachbarn</th>
              <th className="num">Policy/SD-WAN</th>
              <th>Nicht lesbar</th>
            </tr>
          </thead>
          <tbody>
            {summary.contexts.map((c) => (
              <tr key={c.id}>
                <td>{where(c)}</td>
                <td>
                  <span className="chip">{c.display ? KIND_LABEL[c.display] : c.kind}</span>
                </td>
                <td className="num">{c.counts.rib ?? 0}</td>
                <td className="num">{c.counts.bgp ?? 0}</td>
                <td className="num">{c.counts.static ?? 0}</td>
                <td className="num">{c.counts.peers ?? 0}</td>
                <td className="num">{c.counts.policy_routes ?? 0}</td>
                <td>{c.missing?.length ? <Missing items={c.missing} /> : <span className="chip ok">✓</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// VRF innerhalb eines VDOMs (FortiGate 7.x); "mgmt" = dediziertes Management-Interface
function VrfChip({ vrf }: { vrf?: number | string | null }) {
  if (vrf == null || vrf === 0) return null;
  return (
    <span className="chip warn" title="eigene Routing-Tabelle im VDOM – nicht die Standard-VRF 0">
      {vrf === "mgmt" ? "Management" : `VRF ${vrf}`}
    </span>
  );
}

function policyText(p: PolicyHit) {
  return p.kind === "sdwan" ? `SD-WAN ${p.name ?? `#${p.seq}`}` : `Policy-Route #${p.seq ?? "?"}`;
}

const EXIT_KIND: Record<TopoExit["kind"], string> = {
  internet: "Internet",
  mpls: "MPLS",
  wan: "WAN",
  partner: "Partner",
  other: "Anbindung",
};

function Port({ p }: { p: TracePort | undefined }) {
  if (!p?.interface && !p?.next_hop) return null;
  return (
    <div>
      {p.interface && <span className="mono">{p.interface}</span>}
      {p.zone && <span className="chip" style={{ marginLeft: 4 }}>{p.zone}</span>}
      {p.next_hop && (
        <span className="mono">
          {" "}
          → {p.next_hop}
          <Names r={p} />
        </span>
      )}
      {p.exit && (
        <span className="chip info" style={{ marginLeft: 4 }} title="Anbindung nach aussen (von Hand erfasst)">
          {EXIT_KIND[p.exit.kind as TopoExit["kind"]] ?? "Anbindung"} {p.exit.name}
        </span>
      )}
    </div>
  );
}

// Entwurf einer Anbindung (neu bzw. mit `id` eine bestehende ändern/ergänzen) – aus einem Weg,
// der an einem unbekannten Next-Hop endet, bzw. aus einer erkannten Anbindung
type ExitDraft = Partial<TopoExitIn> & { id?: number };
// Weg endet an einem Router ohne gelesenes Gerät: daraus eine Anbindung erfassen
type Capture = { next_hop: string; source_system_id: number; interface: string | null; kind: TopoExit["kind"] };

const union = <T,>(a: T[], b: T[]) => [...a, ...b.filter((x) => !a.includes(x))];

// Entwurf aus einer erkannten Anbindung: alle Router und Firewalls; deckt eine Anbindung schon
// einen Teil ab, wird sie ergänzt (Router dazu, Firewalls dazu – gilt sie für alle, bleibt das)
function draftFrom(s: ExitSuggestion, exits: TopoExit[]): ExitDraft {
  const hops = s.next_hops.map((h) => h.ip);
  const ex = exits.find((e) => s.exits.some((x) => x.id === e.id));
  if (!ex)
    return { name: s.name, kind: s.kind, source_system_ids: s.source_system_ids, interfaces: s.interfaces, next_hops: hops };
  return {
    ...exitDraft(ex),
    source_system_ids: ex.source_system_ids.length ? union(ex.source_system_ids, s.source_system_ids) : [],
    next_hops: union(ex.next_hops, hops),
  };
}

function exitDraft(e: TopoExit): ExitDraft {
  const { id, tenant_id, name, kind, source_system_ids, interfaces, next_hops, prefixes, routes, description } = e;
  return { id, tenant_id, name, kind, source_system_ids, interfaces, next_hops, prefixes, routes, description };
}

const ROUTER_STATUS: Record<ExitRouter["status"], [string, string, string]> = {
  active: ["ok", "aktiv", "Präfixe in der Routing-Tabelle über diesen Router"],
  backup: ["warn", "Backup", "nur als BGP-Pfad bzw. statische Route (übernimmt bei Ausfall)"],
  neighbor: ["", "BGP-Nachbar", "BGP-Sitzung ohne Routen über diesen Router"],
};

// Ein erkannter Router: IP mit Namen, Zustand, BGP (AS, Sitzung), Default-Route, Firewalls
function RouterCell({ r }: { r: ExitRouter }) {
  const [cls, label, title] = ROUTER_STATUS[r.status];
  const down = r.state && !/^estab/i.test(r.state);
  const fws = r.uses.map((u) => `${u.label}${u.interface ? ` (${u.zone ? `${u.zone} · ` : ""}${u.interface})` : ""}`);
  return (
    <span title={[...fws, ...r.prefixes].join("\n")}>
      <span className="mono">{r.ip}</span>
      <Names r={r} /> <span className={`chip ${cls}`} title={title}>{label}</span>
      {r.remote_as != null && (
        <span className={`chip ${down ? "crit" : ""}`} style={{ marginLeft: 4 }} title={`BGP-Sitzung: ${r.state ?? "?"}`}>
          AS {r.remote_as}
          {down ? ` · ${r.state}` : ""}
        </span>
      )}
      {r.default && (
        <span className="chip" style={{ marginLeft: 4 }}>
          Default
        </span>
      )}
      {r.uses.length > 1 && <span className="faint"> · {r.uses.length} Firewalls</span>}
    </span>
  );
}

// Redundanz in Worten: „4 Router · 2 aktiv (ECMP ×2) · 2 Backup · 2 Firewalls“
function redundancy(r: Redundancy): string {
  return [
    `${r.routers} Router`,
    r.active && `${r.active} aktiv${r.ecmp > 1 ? ` (ECMP ×${r.ecmp})` : ""}`,
    r.backup && `${r.backup} Backup`,
    r.neighbor && `${r.neighbor} nur BGP-Nachbar`,
    r.firewalls > 1 && `${r.firewalls} Firewalls`,
  ]
    .filter(Boolean)
    .join(" · ");
}

// Redundanz mehrerer erkannter Anbindungen zusammen (im Formular)
function combined(rows: ExitSuggestion[], routers: ExitRouter[]): Redundancy {
  const n = (s: ExitRouter["status"]) => routers.filter((r) => r.status === s).length;
  return {
    routers: routers.length,
    active: n("active"),
    backup: n("backup"),
    neighbor: n("neighbor"),
    ecmp: Math.max(0, ...rows.map((s) => s.redundancy.ecmp)),
    firewalls: new Set(routers.flatMap((r) => r.uses.map((u) => u.source_system_id))).size,
  };
}

const END: Record<TracePath["end"]["kind"], [string, string]> = {
  connected: ["ok", "Ziel erreicht"],
  fabric: ["ok", "im Fabric"],
  exit: ["info", "Anbindung"],
  external: ["info", "extern"],
  unknown: ["warn", "Weg endet"],
  no_route: ["warn", "keine Route"],
  loop: ["crit", "Schleife"],
  max_hops: ["warn", "abgebrochen"],
};

// Titel eines Wegs: aktive Wege „Weg 1 von 2“, Backup-Wege „Backup-Weg 1“
function pathTitle(paths: TracePath[], p: TracePath): string {
  const same = paths.filter((x) => (x.kind ?? "active") === (p.kind ?? "active"));
  const n = same.indexOf(p) + 1;
  if (p.kind === "backup") return `Backup-Weg ${n}`;
  return `Weg ${n}${same.length > 1 ? ` von ${same.length}` : ""}`;
}

// Art eines Wegs in der Übersicht: aktiv, ECMP, redundant über einen Verbund, Backup
function PathKind({ path, activeCount }: { path: TracePath; activeCount: number }) {
  if (path.kind === "backup")
    return (
      <span className="chip warn" title={`weicht bei ${path.deviation?.label} ab: ${path.deviation?.reason}`}>
        Backup ab {path.deviation?.label}
      </span>
    );
  if (path.parallel?.length)
    return (
      <span className="chip info" title="gleicher Weg über eine andere Firewall desselben Verbunds">
        redundant · Verbund {path.parallel.join(", ")}
      </span>
    );
  return activeCount > 1 ? (
    <span className="chip info" title="Equal-Cost-Multipath: der Verkehr verteilt sich auf alle Wege">
      aktiv · ECMP
    </span>
  ) : (
    <span className="chip ok">aktiv</span>
  );
}

// Länge im Vergleich zum kürzesten Weg mit Ziel: „gleich lang“ bzw. „+1 Hop“
function Length({ extra }: { extra?: number | null }) {
  if (extra == null)
    return (
      <span className="faint" title="Weg ohne Ziel">
        –
      </span>
    );
  return extra === 0 ? (
    <span className="chip ok" title="so lang wie der kürzeste Weg">
      gleich lang
    </span>
  ) : (
    <span className="chip warn" title="Hops mehr als der kürzeste Weg">
      +{extra} Hop{extra > 1 ? "s" : ""}
    </span>
  );
}

// Übersicht aller Wege: Art, Knoten nacheinander, Länge (Hops, im Vergleich), Ende
function PathOverview({ trace }: { trace: TraceResult }) {
  const active = trace.paths.filter((p) => p.kind !== "backup");
  return (
    <div className="card">
      <div className="card-head">
        <h2>Wege im Überblick</h2>
        <span className="faint">
          {active.length} aktiv{active.length > 1 ? " (redundant)" : ""}
          {trace.paths.length > active.length
            ? ` · ${trace.paths.length - active.length} Backup-Weg${trace.paths.length - active.length > 1 ? "e" : ""}`
            : ""}{" "}
          – Backup-Wege mit gleicher Länge
          {trace.slack ? ` bzw. bis ${trace.slack} Hop${trace.slack > 1 ? "s" : ""} mehr` : ""}
        </span>
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>Weg</th>
              <th>Art</th>
              <th>Knoten</th>
              <th>Next-Hops</th>
              <th className="num">Hops</th>
              <th>Länge</th>
              <th>Ende</th>
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
                        <span className={p.deviation && p.deviation.hop === j ? "chip warn" : undefined}>{h.label}</span>
                      </span>
                    ))}
                  </td>
                  <td className="mono">
                    {p.hops
                      .map((h) => h.egress?.next_hop)
                      .filter(Boolean)
                      .join(" → ") || <Dash />}
                  </td>
                  <td className="num">{p.hops.length}</td>
                  <td>
                    <Length extra={p.extra} />
                  </td>
                  <td>
                    <span className={`chip ${cls}`}>{label}</span>
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
        {path.kind === "backup" && path.deviation && (
          <span className="chip warn" title="Weg, wenn der aktive Next-Hop ausfällt">
            Backup ab {path.deviation.label}: {path.deviation.reason}
          </span>
        )}
        {path.parallel?.length ? (
          <span className="chip info">redundant · Verbund {path.parallel.join(", ")}</span>
        ) : null}
        {compare && path.extra != null && <Length extra={path.extra} />}
        <span className="faint">{path.end.text}</span>
        {capturable && (
          <button
            className="btn btn-sm"
            style={{ marginLeft: "auto" }}
            title="Anbindung nach aussen erfassen – mit allen erkannten Routern und Firewalls dieser Anbindung; der Weg endet dann dort bzw. geht nach manuellen Routen weiter"
            onClick={() =>
              onCapture({
                kind: path.end.kind === "external" ? "internet" : "wan",
                source_system_id: last.source_system_id,
                interface: out?.interface ?? null,
                next_hop: out!.next_hop!,
              })
            }
          >
            Als Anbindung erfassen
          </button>
        )}
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Knoten</th>
              <th>Eingang</th>
              <th>Entscheidung</th>
              <th>Ausgang · Next-Hop</th>
            </tr>
          </thead>
          <tbody>
            {path.hops.map((h, i) => {
              const possible = (h.policy_routes ?? []).filter((p) => !p.certain);
              return (
                <tr key={i}>
                  <td className="num">{i + 1}</td>
                  <td>
                    <b>{h.label}</b> {h.kind === "firewall" && <span className="chip">Firewall</span>}
                    <VrfChip vrf={h.vrf} />
                    <div className="faint">
                      {h.via}
                      {h.kind === "vrf" && h.members.length ? ` · ${h.members.join(", ")}` : ""}
                    </div>
                    <Missing items={h.missing} />
                  </td>
                  <td>{h.ingress?.interface ? <Port p={h.ingress} /> : <Dash />}</td>
                  <td>
                    {h.backup ? (
                      <>
                        <span className="chip warn" title="Backup-Weg: anderer Next-Hop für dasselbe Präfix">
                          Backup
                        </span>{" "}
                        <b className="mono">{h.backup.prefix}</b>
                        <div className="faint">{h.backup.reason}</div>
                      </>
                    ) : h.policy ? (
                      <>
                        <span className="chip info" title="passt sicher und geht der Routing-Tabelle vor">
                          {policyText(h.policy)}
                        </span>
                        <div className="faint">geht der Routing-Tabelle vor</div>
                      </>
                    ) : h.route ? (
                      <>
                        <b className="mono">{h.route.prefix}</b>
                        <div className="faint">
                          {[h.route.protocol, h.route.distance != null && `Distanz ${h.route.distance}`]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </>
                    ) : (
                      <Dash />
                    )}
                    {possible.length > 0 && (
                      <div className="faint" title="Quelle bzw. Ziel nur über Objekte bestimmt – nicht sicher">
                        möglich: {possible.map(policyText).join(", ")}
                      </div>
                    )}
                  </td>
                  <td>{h.egress ? <Port p={h.egress} /> : <Dash />}</td>
                </tr>
              );
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
          <Empty title="Keine Routing-Daten">Noch kein Quellsystem mit Routing eingelesen.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>System · Kontext</th>
                <th>Route</th>
                <th>Next-Hop</th>
                <th>Geht vor</th>
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
                          {[r.route.protocol, r.route.distance != null && `Distanz ${r.route.distance}`]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </>
                    ) : (
                      <span className={`chip ${r.missing.length ? "warn" : ""}`}>
                        {r.missing.length ? "unbekannt" : "keine Route"}
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
                            {!p.certain && <span className="faint"> (möglich – Ziel über Objekte)</span>}
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

// Weg für IP: Quelle (IP) und/oder Startpunkt, dazu das Ziel. Ohne beides: je Kontext die Route.
function LookupTab({
  tenantId,
  nodes,
  onCapture,
}: {
  tenantId: number;
  nodes: RoutingNode[];
  onCapture?: (c: Capture) => void;
}) {
  const [src, setSrc] = useState("");
  const [start, setStart] = useState("");
  const [dst, setDst] = useState("");
  const [trace, setTrace] = useState<TraceResult | null>(null);
  const [slack, setSlack] = useState("1"); // Backup-Wege: Hops mehr als der kürzeste aktive; "" = keine
  const [rows, setRows] = useState<RouteLookup[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const run = () => {
    if (!dst.trim()) return;
    setBusy(true);
    setError(null);
    setTrace(null);
    setRows(null);
    const job =
      src.trim() || start
        ? traceRoute(tenantId, { dst, src, start, slack: slack === "" ? 0 : Number(slack) }).then(setTrace)
        : lookupRoute(tenantId, dst).then(setRows);
    job.catch(setError).finally(() => setBusy(false));
  };
  const enter = (e: React.KeyboardEvent) => e.key === "Enter" && run();
  return (
    <>
      <div className="filterbar">
        <div className="search" style={{ flex: "0 1 200px" }}>
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder="Quelle (IP)"
            title="Quell-IP: der Weg beginnt dort, wo sie angeschlossen ist (bzw. am Startpunkt)"
            value={src}
            onChange={(e) => setSrc(e.target.value)}
            onKeyDown={enter}
          />
        </div>
        <select
          className="sel"
          value={start}
          onChange={(e) => setStart(e.target.value)}
          title="Startpunkt: Firewall, VDOM bzw. ACI-VRF – ohne Wahl ergibt er sich aus der Quelle"
        >
          <option value="">Start: aus der Quelle</option>
          {nodes.map((n) => (
            <option key={n.key} value={n.key}>
              {n.label}
            </option>
          ))}
        </select>
        <div className="search" style={{ flex: "0 1 200px" }}>
          <span className="ico">
            <Icon name="route" />
          </span>
          <input
            placeholder="Ziel (IP), z. B. 10.20.0.5"
            value={dst}
            onChange={(e) => setDst(e.target.value)}
            onKeyDown={enter}
          />
        </div>
        <select
          className="sel"
          value={slack}
          onChange={(e) => setSlack(e.target.value)}
          title="Redundante Wege: Backup-Wege (BGP-Pfade, schwebende statische Routen) mit gleicher bzw. fast gleicher Länge"
        >
          <option value="0">Backup: gleich lang</option>
          <option value="1">Backup: bis +1 Hop</option>
          <option value="2">Backup: bis +2 Hops</option>
          <option value="">Backup: ausblenden</option>
        </select>
        <button className="btn btn-primary btn-sm" onClick={run} disabled={busy || !dst.trim()}>
          {busy ? "Suche…" : "Weg suchen"}
        </button>
        <span className="faint">
          Hop für Hop: Policy-Routen und SD-WAN-Regeln, die sicher passen, gehen vor; sonst das längste Präfix.
          Ohne Quelle und Start: je Kontext die wirksame Route.
        </span>
      </div>
      <ErrorBox error={error} prefix="Weg suchen" />
      {trace && (
        <>
          {trace.start.length > 0 && (
            <div className="faint" style={{ margin: "0 0 8px" }}>
              Start: {trace.start.map((s) => `${s.label} (${s.via})`).join(" · ")}
            </div>
          )}
          {trace.notes.map((n) => (
            <Note key={n} warn>
              {n}
            </Note>
          ))}
          {trace.paths.length === 0 && trace.notes.length === 0 && (
            <Empty title="Kein Weg">Kein Hop gefunden – Startpunkt wählen bzw. Quellsysteme neu einlesen.</Empty>
          )}
          {(() => {
            const shown = slack === "" ? trace.paths.filter((p) => p.kind !== "backup") : trace.paths;
            return (
              <>
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

function RoutesTab({ tenantId, nodes }: { tenantId: number; nodes: RoutingNode[] }) {
  const [q, setQ] = useState("");
  const [ip, setIp] = useState("");
  const [table, setTable] = useState("");
  const [context, setContext] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ rows: TopoRoute[]; total: number } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const query = useDebounced(q);
  const ipQuery = useDebounced(ip);

  useEffect(() => setOffset(0), [query, ipQuery, table, context]);
  useEffect(() => {
    setPage(null);
    setError(null);
    // Kontext = Knoten „<Quellsystem>|<VDOM bzw. Tenant:VRF>“ (ACI: alle Border-Leafs)
    const [sid, ...rest] = context ? context.split("|") : [];
    fetchRoutes({
      tenantId,
      q: query,
      ip: ipQuery,
      table,
      sourceId: sid ? Number(sid) : null,
      context: rest.join("|"),
      limit: PAGE,
      offset,
    })
      .then(setPage)
      .catch((e) => {
        setError(e);
        setPage({ rows: [], total: 0 });
      });
  }, [tenantId, query, ipQuery, table, context, offset]);

  return (
    <>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input placeholder="Präfix, Next-Hop, Interface, Protokoll…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="search" style={{ flex: "0 1 210px" }}>
          <span className="ico">
            <Icon name="route" />
          </span>
          <input
            placeholder="enthält IP…"
            title="Routen, deren Präfix die IP enthält – längstes Präfix zuerst"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
          />
        </div>
        <select className="sel" value={table} onChange={(e) => setTable(e.target.value)}>
          <option value="">Tabelle: alle</option>
          <option value="rib">wirksam (RIB)</option>
          <option value="bgp">BGP-Pfade</option>
          <option value="static">statische Routen</option>
        </select>
        <select className="sel" value={context} onChange={(e) => setContext(e.target.value)}>
          <option value="">Kontext: alle</option>
          {nodes.map((n) => (
            <option key={n.key} value={n.key}>
              {n.label}
            </option>
          ))}
        </select>
      </div>
      <ErrorBox error={error} prefix="Routen laden" />
      <div className="card">
        <div className="card-body scroll viewport">
          {page === null ? (
            <Loading />
          ) : page.rows.length === 0 ? (
            <Empty title="Keine Routen">Filter anpassen bzw. Quellsysteme neu einlesen.</Empty>
          ) : (
            <table className="compact">
              <thead>
                <tr>
                  <th>System · Kontext</th>
                  <th>Präfix</th>
                  <th>Tabelle</th>
                  <th>Protokoll</th>
                  <th className="num">Distanz</th>
                  <th className="num">Metrik</th>
                  <th>Next-Hop</th>
                  <th>Weitere Angaben</th>
                </tr>
              </thead>
              <tbody>
                {page.rows.map((r) => {
                  const [cls, label] = TABLE_LABEL[r.table] ?? ["", r.table];
                  const attrs = Object.entries(r.attrs ?? {}).filter(([, v]) => v !== null && v !== "");
                  return (
                    <tr key={r.id}>
                      <td>{where(r)}</td>
                      <td className="mono">
                        <b>{r.prefix}</b>
                      </td>
                      <td>
                        <span className={`chip ${cls}`}>{label}</span>
                        {r.table !== "rib" && (
                          <span
                            className={`chip ${r.best ? "ok" : ""}`}
                            title={r.table === "bgp" ? "bester Pfad bzw. Backup-Weg" : "aktiv bzw. deaktiviert"}
                          >
                            {r.table === "bgp" ? (r.best ? "bester" : "Backup") : r.best ? "aktiv" : "inaktiv"}
                          </span>
                        )}
                      </td>
                      <td>{r.protocol ?? <Dash />}</td>
                      <td className="num">{r.distance ?? "–"}</td>
                      <td className="num">{r.metric ?? "–"}</td>
                      <td>
                        <Hops hops={r.next_hops} />
                      </td>
                      <td className="faint">{attrs.map(([k, v]) => `${k} ${String(v)}`).join(" · ") || "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        {page && page.total > 0 && <Pager total={page.total} offset={offset} onOffset={setOffset} pageSize={PAGE} />}
      </div>
    </>
  );
}

function useList<T>(load: () => Promise<T[]>, deps: unknown[]) {
  const [rows, setRows] = useState<T[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setRows(null);
    load()
      .then(setRows)
      .catch((e) => {
        setError(e);
        setRows([]);
      });
  }, deps); // load haengt nur von deps ab
  return { rows, error };
}

function PeersTab({ tenantId }: { tenantId: number }) {
  const { rows, error } = useList<TopoPeer>(() => fetchPeers(tenantId), [tenantId]);
  return (
    <List error={error} rows={rows} empty="Keine BGP-Nachbarn gelesen.">
      <thead>
        <tr>
          <th>System · Kontext</th>
          <th>Nachbar</th>
          <th>Gelesen als</th>
          <th>Lokal</th>
          <th className="num">AS</th>
          <th>Zustand</th>
        </tr>
      </thead>
      <tbody>
        {(rows ?? []).map((p) => (
          <tr key={p.id}>
            <td>{where(p)}</td>
            <td className="mono">
              {p.peer_ip}
              <Names r={p.peer} />
            </td>
            <td>{p.peer_node ?? <Dash />}</td>
            <td className="mono">{p.local_ip ?? "–"}</td>
            <td className="num">{p.remote_as ?? "–"}</td>
            <td>
              <span className={`chip ${(p.state ?? "").toLowerCase() === "established" ? "ok" : "warn"}`}>
                {p.state ?? "?"}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </List>
  );
}

function HaTab({ tenantId }: { tenantId: number }) {
  const { rows, error } = useList<TopoHa>(() => fetchHa(tenantId), [tenantId]);
  return (
    <List error={error} rows={rows} empty="Keine HA-Cluster gelesen.">
      <thead>
        <tr>
          <th>System</th>
          <th>Modus</th>
          <th>Gruppe</th>
          <th>Mitglieder</th>
          <th>Session-Sync (FGSP)</th>
        </tr>
      </thead>
      <tbody>
        {(rows ?? []).map((h) => (
          <tr key={h.id}>
            <td>
              <b>{h.source ?? h.node}</b>
              <div className="faint">{h.node}</div>
            </td>
            <td>
              <span className="chip">{h.mode ?? "?"}</span>
              {h.session_pickup && <span className="chip info">Session-Pickup</span>}
            </td>
            <td>
              {h.group_name ?? "–"}
              {h.group_id != null && <span className="faint"> (ID {h.group_id})</span>}
            </td>
            <td>
              <div className="objs">
                {(h.members ?? []).map((m, i) => (
                  <span key={i}>
                    <b>{m.hostname ?? m.serial ?? "?"}</b>{" "}
                    {m.role && <span className={`chip ${m.role === "primary" ? "ok" : ""}`}>{m.role}</span>}
                    {m.priority != null && <span className="faint"> Prio {m.priority}</span>}
                  </span>
                ))}
              </div>
            </td>
            <td>
              {h.sync_peers?.length ? (
                <div className="objs">
                  {h.sync_peers.map((p, i) => (
                    <span key={i} className="mono">
                      {String(p.peerip ?? "?")}
                      <Names r={p} />
                      {p.node && <span className="faint"> · {p.node}</span>}
                      {Array.isArray(p.syncvd) && p.syncvd.length ? (
                        <span className="faint"> · VDOMs {(p.syncvd as string[]).join(", ")}</span>
                      ) : null}
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
    </List>
  );
}

function PolicyTab({ tenantId }: { tenantId: number }) {
  const { rows, error } = useList<TopoPolicyRoute>(() => fetchPolicyRoutes(tenantId), [tenantId]);
  return (
    <List error={error} rows={rows} empty="Keine Policy-Routen oder SD-WAN-Regeln gelesen.">
      <thead>
        <tr>
          <th>System · Kontext</th>
          <th>Art</th>
          <th>Bedingung</th>
          <th>Weg</th>
        </tr>
      </thead>
      <tbody>
        {(rows ?? []).map((p) => (
          <tr key={p.id} className={p.enabled ? "" : "faint"}>
            <td>{where(p)}</td>
            <td>
              <span className="chip">{p.kind === "sdwan" ? "SD-WAN" : "Policy-Route"}</span>{" "}
              <span className="mono">#{p.seq ?? "?"}</span> {p.name && <b>{p.name}</b>}
              {!p.enabled && <span className="chip">aus</span>}
            </td>
            <td>{matchText(p.match)}</td>
            <td className="mono">
              {[p.gateway, ...(p.output_device ?? [])].filter(Boolean).join(" · ") || p.action || "–"}
              <Names r={p.gateway_resolved} />
            </td>
          </tr>
        ))}
      </tbody>
    </List>
  );
}

function L3OutTab({ tenantId }: { tenantId: number }) {
  const { rows, error } = useList<TopoL3Out>(() => fetchL3Outs(tenantId), [tenantId]);
  return (
    <List error={error} rows={rows} empty="Keine L3Outs gelesen.">
      <thead>
        <tr>
          <th>L3Out</th>
          <th>VRF</th>
          <th>Border-Leafs</th>
          <th>Interfaces / SVIs</th>
          <th>BGP-Peers</th>
        </tr>
      </thead>
      <tbody>
        {(rows ?? []).map((o) => (
          <tr key={o.id}>
            <td>
              <b>{o.name}</b>
              <div className="faint">
                Tenant {o.aci_tenant}
                {o.ext_epgs?.length ? ` · ${o.ext_epgs.join(", ")}` : ""}
              </div>
            </td>
            <td className="mono">{o.vrf ?? "–"}</td>
            <td>
              <div className="objs">
                {(o.nodes ?? []).map((n) => (
                  <span key={`${n.pod}-${n.node}`} className="mono">
                    <b>{n.name ?? `node-${n.node}`}</b>
                    {n.name && <span className="faint"> (node-{n.node})</span>}
                    {n.router_id && <span className="faint"> · Router-ID {n.router_id}</span>}
                    {n.static_routes?.map((r) => (
                      <span key={r.prefix} className="faint">
                        {" "}
                        · {r.prefix} →{" "}
                        {(r.next_hops ?? []).length
                          ? (r.next_hops ?? []).map((h, i) => (
                              <span key={h}>
                                {i > 0 && ", "}
                                {h}
                                <Names r={r.resolved?.[h]} />
                              </span>
                            ))
                          : "?"}
                      </span>
                    ))}
                  </span>
                ))}
              </div>
            </td>
            <td>
              <div className="objs">
                {(o.paths ?? []).map((p, i) => (
                  <span key={i} className="mono" title={p.path}>
                    {(p.node_names?.length ? p.node_names : p.nodes ?? []).join("/")} {p.interface}
                    <span className="faint">
                      {" "}
                      · {p.kind}
                      {p.vlan ? ` · VLAN ${p.vlan}` : ""}
                      {p.ips?.length ? ` · ${p.ips.map((x) => (x.side ? `${x.side} ${x.ip}` : x.ip)).join(", ")}` : ""}
                    </span>
                  </span>
                ))}
              </div>
            </td>
            <td>
              <div className="objs">
                {(o.peers ?? []).map((p, i) => (
                  <span key={i} className="mono">
                    {p.ip}
                    <Names r={p} />
                    {p.asn != null && <span className="faint"> · AS {p.asn}</span>}
                    {p.node && <span className="faint"> · {p.node}</span>}
                  </span>
                ))}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </List>
  );
}

function List<T>({
  rows,
  error,
  empty,
  children,
}: {
  rows: T[] | null;
  error: unknown;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <ErrorBox error={error} />
      <div className="card">
        <div className="card-body scroll viewport">
          {rows === null ? <Loading /> : rows.length === 0 ? <Empty title={empty} /> : <table>{children}</table>}
        </div>
      </div>
    </>
  );
}

const csv = (v: string) =>
  v
    .split(/[\s,;]+/)
    .map((x) => x.trim())
    .filter(Boolean);

// Anbindung erfassen/ändern: Firewalls (z. B. beide Session-Sync-Partner), Next-Hops – die erkannten
// Router zum Ankreuzen – und/oder Interfaces/Zonen, Netze dahinter, manuelle Routen
function ExitForm({
  tenantId,
  nodes,
  draft,
  onSaved,
  onCancel,
}: {
  tenantId: number;
  nodes: RoutingNode[];
  draft: ExitDraft;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(draft.name ?? "");
  const [kind, setKind] = useState<TopoExit["kind"]>(draft.kind ?? "internet");
  const [sids, setSids] = useState<number[]>(draft.source_system_ids ?? []);
  const [ifaces, setIfaces] = useState((draft.interfaces ?? []).join(", "));
  const [hops, setHops] = useState((draft.next_hops ?? []).join(", "));
  const [prefixes, setPrefixes] = useState((draft.prefixes ?? []).join(", "));
  const [routes, setRoutes] = useState<{ prefix: string; target: string }[]>(draft.routes ?? []);
  const [description, setDescription] = useState(draft.description ?? "");
  const [detected, setDetected] = useState<ExitSuggestion[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    fetchExitSuggestions(tenantId, true)
      .then(setDetected)
      .catch(() => setDetected([]));
  }, [tenantId]);
  const firewalls = [...new Map(nodes.map((n) => [n.source_system_id, n.source])).entries()];
  const hopList = csv(hops);
  const portList = csv(ifaces);
  // erkannte Router dieser Anbindung: Vorschläge mit einem eingetragenen (bzw. anfangs eingetragenen)
  // Router oder mit dem Interface/der Zone an einer der Firewalls
  const known = union(draft.next_hops ?? [], hopList);
  const related = (detected ?? []).filter(
    (s) =>
      s.next_hops.some((h) => known.includes(h.ip)) ||
      (s.interfaces.some((x) => portList.includes(x)) &&
        (!sids.length || s.source_system_ids.some((i) => sids.includes(i)))),
  );
  const routers = [...new Map(related.flatMap((s) => s.next_hops).map((h) => [h.ip, h])).values()];
  const toggle = (ip: string) =>
    setHops((hopList.includes(ip) ? hopList.filter((x) => x !== ip) : [...hopList, ip]).join(", "));
  const takeAll = () => {
    setHops(union(hopList, routers.map((r) => r.ip)).join(", "));
    if (sids.length) setSids(union(sids, related.flatMap((s) => s.source_system_ids)));
  };
  const save = () => {
    const body: TopoExitIn = {
      tenant_id: tenantId,
      name: name.trim(),
      kind,
      source_system_ids: sids,
      interfaces: portList,
      next_hops: hopList,
      prefixes: csv(prefixes),
      routes: routes.filter((r) => r.prefix.trim() && r.target),
      description: description.trim() || null,
    };
    setBusy(true);
    setError(null);
    (draft.id ? updateExit(draft.id, body) : createExit(body))
      .then(onSaved)
      .catch(setError)
      .finally(() => setBusy(false));
  };
  return (
    <div className="card">
      <div className="card-head">
        <h2>{draft.id ? `Anbindung „${draft.name}“` : "Neue Anbindung"}</h2>
        <span className="faint">
          Ein Weg, der über diesen Next-Hop bzw. dieses Interface geht und dort kein gelesenes Gerät findet, endet hier
          – oder geht nach den manuellen Routen weiter.
        </span>
      </div>
      <div className="card-body" style={{ padding: 16 }}>
        <ErrorBox error={error} prefix="Anbindung speichern" />
        <div className="form-grid">
          <div className="field">
            <label htmlFor="ex-name">Name</label>
            <input id="ex-name" className="input" placeholder="z. B. Internet Provider A" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ex-kind">Art</label>
            <select id="ex-kind" className="sel input" value={kind} onChange={(e) => setKind(e.target.value as TopoExit["kind"])}>
              {Object.entries(EXIT_KIND).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ex-ifaces">Interfaces bzw. Zonen</label>
            <input id="ex-ifaces" className="input" placeholder="wan1, internet" value={ifaces} onChange={(e) => setIfaces(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ex-hops">Next-Hops (Gegenstelle)</label>
            <input id="ex-hops" className="input" placeholder="198.51.100.1" value={hops} onChange={(e) => setHops(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ex-nets">Netze dahinter</label>
            <input
              id="ex-nets"
              className="input"
              placeholder="leer = alles, was dorthin geroutet wird"
              value={prefixes}
              onChange={(e) => setPrefixes(e.target.value)}
            />
          </div>
        </div>
        <div className="field" id="ex-fws">
          <label>Firewalls bzw. ACI {sids.length ? "" : <span className="faint">– keine gewählt = alle</span>}</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
            {firewalls.map(([id, label]) => (
              <label key={id} className="check">
                <input
                  type="checkbox"
                  checked={sids.includes(id)}
                  onChange={() => setSids(sids.includes(id) ? sids.filter((x) => x !== id) : [...sids, id])}
                />
                {label}
              </label>
            ))}
          </div>
        </div>
        {routers.length > 0 && (
          <div className="field" id="ex-routers">
            <label>
              Erkannte Router{" "}
              <span className="faint">– {redundancy(combined(related, routers))}; ankreuzen = als Next-Hop eintragen</span>
            </label>
            {routers.map((r) => (
              <label key={r.ip} className="check">
                <input type="checkbox" checked={hopList.includes(r.ip)} onChange={() => toggle(r.ip)} />
                <RouterCell r={r} />
                {r.exit && !hopList.includes(r.ip) && <span className="faint">(über {r.exit.name})</span>}
              </label>
            ))}
            {routers.some((r) => !hopList.includes(r.ip)) && (
              <button className="btn btn-sm" style={{ marginTop: 6 }} onClick={takeAll}>
                alle Router übernehmen
              </button>
            )}
          </div>
        )}
        <div className="field">
          <label>Manuelle Routen (z. B. MPLS zu einem anderen Standort)</label>
          {routes.map((r, i) => (
            <div key={i} className="filterbar" style={{ margin: "0 0 6px" }}>
              <input
                className="input"
                style={{ flex: "0 1 200px" }}
                placeholder="10.60.0.0/16"
                value={r.prefix}
                onChange={(e) => setRoutes(routes.map((x, j) => (j === i ? { ...x, prefix: e.target.value } : x)))}
              />
              <span className="faint">weiter bei</span>
              <select
                className="sel"
                value={r.target}
                onChange={(e) => setRoutes(routes.map((x, j) => (j === i ? { ...x, target: e.target.value } : x)))}
              >
                <option value="">Knoten wählen…</option>
                {nodes.map((n) => (
                  <option key={n.key} value={n.key}>
                    {n.label}
                  </option>
                ))}
              </select>
              <button className="linkbtn" onClick={() => setRoutes(routes.filter((_, j) => j !== i))}>
                entfernen
              </button>
            </div>
          ))}
          <button className="btn btn-sm" onClick={() => setRoutes([...routes, { prefix: "", target: "" }])}>
            Route hinzufügen
          </button>
        </div>
        <div className="field">
          <label htmlFor="ex-desc">Beschreibung</label>
          <input id="ex-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary btn-sm" disabled={busy || !name.trim()} onClick={save}>
            {busy ? "Speichere…" : "Speichern"}
          </button>
          <button className="btn btn-sm" onClick={onCancel}>
            Abbrechen
          </button>
        </div>
      </div>
    </div>
  );
}

// Erkannte Anbindungen: Router ohne gelesenes Gerät (Next-Hops, BGP-Nachbarn), je Firewall bzw.
// Verbund/Session-Sync-Partner zusammengefasst, die noch nicht (ganz) erfasst sind
function SuggestedExits({
  rows,
  canEdit,
  onTake,
}: {
  rows: ExitSuggestion[];
  canEdit: boolean;
  onTake: (s: ExitSuggestion) => void;
}) {
  if (!rows.length) return null;
  return (
    <div className="card" id="exit-suggestions">
      <div className="card-head">
        <h2>Erkannte Anbindungen</h2>
        <span className="faint">
          Router ohne gelesenes Gerät aus Routing-Tabelle, BGP-Pfaden, statischen Routen und BGP-Nachbarn – zusammengefasst
          über gemeinsame Router, Interface bzw. Zone und BGP-AS, je Firewall bzw. Verbund und Session-Sync-Partner.
        </span>
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>Vorschlag</th>
              <th>Firewalls</th>
              <th>Interfaces / Zonen</th>
              <th>Router</th>
              <th>Redundanz</th>
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.key}>
                <td>
                  <span className="chip info">{EXIT_KIND[s.kind] ?? s.kind}</span> <b>{s.name}</b>
                  {s.exits.length > 0 && <div className="faint">teilweise in {s.exits.map((e) => e.name).join(", ")}</div>}
                </td>
                <td>{s.sources.join(", ")}</td>
                <td className="mono">{s.interfaces.join(", ") || "–"}</td>
                <td>
                  <div className="objs">
                    {s.next_hops.map((h) => (
                      <span key={h.ip} className="nowrap">
                        <RouterCell r={h} />
                        {h.covered && <span className="faint"> · erfasst</span>}
                      </span>
                    ))}
                  </div>
                </td>
                <td>{redundancy(s.redundancy)}</td>
                {canEdit && (
                  <td className="actions">
                    <button className="linkbtn" onClick={() => onTake(s)}>
                      {s.exits.length ? "ergänzen" : "übernehmen"}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ExitsTab({
  tenantId,
  nodes,
  canEdit,
  draft,
  onDraftUsed,
}: {
  tenantId: number;
  nodes: RoutingNode[];
  canEdit: boolean;
  draft: ExitDraft | null;
  onDraftUsed: () => void;
}) {
  const [reload, setReload] = useState(0);
  const { rows, error } = useList<TopoExit>(() => fetchExits(tenantId), [tenantId, reload]);
  const [suggested, setSuggested] = useState<ExitSuggestion[]>([]);
  const [editing, setEditing] = useState<ExitDraft | null>(draft);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    fetchExitSuggestions(tenantId)
      .then(setSuggested)
      .catch(() => setSuggested([]));
  }, [tenantId, reload]);
  const label = (key: string) => nodes.find((n) => n.key === key)?.label ?? key;
  const done = () => {
    setEditing(null);
    onDraftUsed();
    setReload((n) => n + 1);
  };
  const remove = (e: TopoExit) => {
    if (!window.confirm(`Anbindung „${e.name}“ löschen?`)) return;
    deleteExit(e.id)
      .then(() => setReload((n) => n + 1))
      .catch(setErr);
  };
  return (
    <>
      <div className="filterbar">
        <span className="faint">
          Von Hand erfasst: wo Wege nach aussen gehen (Internet, MPLS, WAN, Partner). Ohne Anbindung endet ein Weg am
          ersten Next-Hop, der keinem gelesenen Gerät gehört.
        </span>
        {canEdit && editing === null && (
          <button className="btn btn-primary btn-sm" style={{ marginLeft: "auto" }} onClick={() => setEditing({})}>
            Neue Anbindung
          </button>
        )}
      </div>
      <ErrorBox error={err} prefix="Anbindung löschen" />
      {editing !== null && (
        <ExitForm
          key={JSON.stringify(editing)}
          tenantId={tenantId}
          nodes={nodes}
          draft={editing}
          onSaved={done}
          onCancel={done}
        />
      )}
      <SuggestedExits rows={suggested} canEdit={canEdit} onTake={(s) => setEditing(draftFrom(s, rows ?? []))} />
      <List error={error} rows={rows} empty="Noch keine Anbindungen erfasst.">
        <thead>
          <tr>
            <th>Anbindung</th>
            <th>Firewalls</th>
            <th>Interfaces / Zonen</th>
            <th>Next-Hops</th>
            <th>Erkannte Router · Redundanz</th>
            <th>Netze dahinter</th>
            <th>Manuelle Routen</th>
            {canEdit && <th />}
          </tr>
        </thead>
        <tbody>
          {(rows ?? []).map((e) => {
            const st = e.status;
            return (
              <tr key={e.id}>
                <td>
                  <span className="chip info">{EXIT_KIND[e.kind] ?? e.kind}</span> <b>{e.name}</b>
                  {e.description && <div className="faint">{e.description}</div>}
                </td>
                <td>{e.sources.length ? e.sources.join(", ") : <span className="faint">alle</span>}</td>
                <td className="mono">{e.interfaces.join(", ") || "–"}</td>
                <td className="mono">{e.next_hops.join(", ") || "–"}</td>
                <td>
                  {st?.routers.length ? (
                    <>
                      <div>{redundancy(st.redundancy)}</div>
                      <div className="objs">
                        {st.routers.map((r) => (
                          <span key={r.ip} className="nowrap">
                            <RouterCell r={r} />
                            {!r.listed && <span className="chip warn" style={{ marginLeft: 4 }}>nicht eingetragen</span>}
                          </span>
                        ))}
                      </div>
                    </>
                  ) : (
                    <span className="faint">keiner erkannt</span>
                  )}
                  {st?.unseen.length ? (
                    <div className="faint" title="eingetragen, aber in keiner Route und keinem BGP-Nachbarn">
                      nicht erkannt: {st.unseen.join(", ")}
                    </div>
                  ) : null}
                  {canEdit && st?.missing.length ? (
                    <button
                      className="linkbtn"
                      onClick={() => setEditing({ ...exitDraft(e), next_hops: union(e.next_hops, st.missing) })}
                    >
                      {st.missing.length} erkannte Router übernehmen
                    </button>
                  ) : null}
                </td>
                <td className="mono">{e.prefixes.join(", ") || <span className="faint">alles</span>}</td>
                <td>
                  {e.routes.length ? (
                    <div className="objs">
                      {e.routes.map((r) => (
                        <span key={r.prefix + r.target} className="mono">
                          {r.prefix} <span className="faint">→</span> {label(r.target)}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <Dash />
                  )}
                </td>
                {canEdit && (
                  <td className="actions">
                    <button className="linkbtn" onClick={() => setEditing(exitDraft(e))}>
                      ändern
                    </button>{" "}
                    <button className="linkbtn" onClick={() => remove(e)}>
                      löschen
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </List>
    </>
  );
}

export default function Routing({
  tenantId,
  tenantLabel,
  canEdit = false,
}: {
  tenantId: number | null;
  tenantLabel: string;
  canEdit?: boolean;
}) {
  const [tab, setTab] = useState<Tab>("lookup");
  const [draft, setDraft] = useState<ExitDraft | null>(null);
  const [summary, setSummary] = useState<RoutingSummary | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (tenantId === null) return;
    setSummary(null);
    fetchRoutingSummary(tenantId)
      .then(setSummary)
      .catch((e) => {
        setError(e);
        setSummary({ contexts: [], nodes: [], ha: 0, l3outs: 0 });
      });
  }, [tenantId]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title="Routing & Wege" />
        <Note>Bitte oben einen Mandanten wählen.</Note>
      </>
    );
  }
  const nodes = summary?.nodes ?? [];
  // Weg endet an einem Router ohne gelesenes Gerät: die erkannte Anbindung mit allen Routern und
  // Firewalls vorschlagen (bzw. die Anbindung ergänzen, die schon einen Teil abdeckt)
  const capture = (c: Capture) => {
    const plain: ExitDraft = {
      kind: c.kind,
      source_system_ids: [c.source_system_id],
      interfaces: c.interface ? [c.interface] : [],
      next_hops: [c.next_hop],
    };
    Promise.all([fetchExitSuggestions(tenantId, true), fetchExits(tenantId)])
      .then(([found, exits]) => {
        const s = found.find((x) => x.next_hops.some((h) => h.ip === c.next_hop));
        return s ? draftFrom(s, exits) : plain;
      })
      .catch(() => plain)
      .then((d) => {
        setDraft(d);
        setTab("exits");
      });
  };

  return (
    <>
      <PageHead title="Routing & Wege">
        Routing-Tabellen von „{tenantLabel}“ je Firewall bzw. FortiGate-VDOM und je ACI-VRF auf den Border-Leafs,
        dazu BGP-Pfade (auch Backup-Wege), Nachbarn, HA und Session-Sync, Policy-Routen und L3Outs – rein lesend,
        Stand des letzten Laufs. Der Weg für eine IP folgt Hop für Hop ab Quelle bzw. Startpunkt (ADR 0019); IPs mit
        Namen aus dem IPAM.
      </PageHead>
      <ErrorBox error={error} prefix="Routing laden" />
      {summary === null ? (
        <Loading />
      ) : summary.contexts.length === 0 && summary.l3outs === 0 && summary.ha === 0 ? (
        <Note>
          Noch keine Routing-Daten. Ab 0.16.0 liest jeder Lauf einer FortiGate bzw. eines APIC das Routing mit –
          Quellsysteme neu einlesen.
        </Note>
      ) : (
        <ContextCard summary={summary} />
      )}
      <div className="tabbar">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {/* bleibt geladen: nach dem Erfassen einer Anbindung den Weg einfach neu suchen */}
      <div hidden={tab !== "lookup"}>
        <LookupTab
          tenantId={tenantId}
          nodes={nodes}
          onCapture={canEdit ? capture : undefined}
        />
      </div>
      {tab === "routes" && <RoutesTab tenantId={tenantId} nodes={nodes} />}
      {tab === "peers" && <PeersTab tenantId={tenantId} />}
      {tab === "ha" && <HaTab tenantId={tenantId} />}
      {tab === "policy" && <PolicyTab tenantId={tenantId} />}
      {tab === "l3out" && <L3OutTab tenantId={tenantId} />}
      {tab === "exits" && (
        <ExitsTab
          key={draft ? JSON.stringify(draft) : "list"}
          tenantId={tenantId}
          nodes={nodes}
          canEdit={canEdit}
          draft={draft}
          onDraftUsed={() => setDraft(null)}
        />
      )}
    </>
  );
}
