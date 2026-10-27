// Routing (ADR 0019): gelesene Routing-Tabellen je Firewall bzw. FortiGate-VDOM und je
// ACI-VRF auf den Border-Leafs, BGP-Pfade (auch Backup), Nachbarn, HA/Session-Sync,
// Policy-Routen/SD-WAN, L3Outs und Anbindungen; IPs mit Namen aus dem IPAM. Die Weg-Suche
// ist seit 0.35.0 eine eigene Ansicht (`Paths.tsx`). Reiter „Verlauf“ (ADR 0030): Änderungen
// am Routing von Lauf zu Lauf.
import { useEffect, useRef, useState } from "react";
import {
  ContextKind,
  createExit,
  deleteExit,
  dismissExits,
  ExitRouter,
  ExitSuggestion,
  Redundancy,
  fetchExits,
  fetchExitSuggestions,
  fetchFirewallChoices,
  fetchHa,
  fetchL3Outs,
  FirewallChoice,
  L3OutBehind,
  fetchParallelGroups,
  fetchPeers,
  fetchPolicyRoutes,
  fetchRoutes,
  fetchRoutingChanges,
  fetchRoutingSummary,
  Labeled,
  NextHop,
  Page,
  ParallelGroup,
  PolicyHit,
  Resolved,
  RoutingNode,
  RoutingSummary,
  TopoHa,
  TopoL3Out,
  TopoPeer,
  TopoPolicyRoute,
  TopoChange,
  TopoChangeKind,
  TopoChangeType,
  TopoExit,
  TopoExitIn,
  TopoRoute,
  TracePort,
  removeL3OutFirewall,
  setL3OutFirewall,
  updateExit,
} from "./api";
import { Pager, useDebounced } from "./Objects";
import TopologyTab from "./Topology";
import { FoldTitle, useFolds } from "./fold";
import { Dash, Empty, ErrorBox, fmtTime, Icon, Loading, Note, PageHead, SharedChip } from "./ui";
import { L } from "./i18n";

const PAGE = 200;

type Tab = "routes" | "changes" | "peers" | "ha" | "parallel" | "policy" | "l3out" | "exits" | "topology";
const TABS: { key: Tab; label: string }[] = [
  { key: "routes", label: L("Routen", "Routes") },
  { key: "changes", label: L("Verlauf", "History") },
  { key: "peers", label: L("BGP-Nachbarn", "BGP neighbors") },
  { key: "ha", label: L("HA & Session-Sync", "HA & session sync") },
  { key: "parallel", label: L("Parallele Firewalls", "Parallel firewalls") },
  { key: "policy", label: L("Policy-Routen & SD-WAN", "Policy routes & SD-WAN") },
  { key: "l3out", label: "L3Outs (ACI)" },
  { key: "exits", label: L("Anbindungen", "Exits") },
  { key: "topology", label: L("Topologie", "Topology") },
];

const TABLE_LABEL: Record<string, [string, string]> = {
  rib: ["ok", L("wirksam", "effective")],
  bgp: ["info", L("BGP-Pfad", "BGP path")],
  static: ["", L("statisch", "static")],
};

const KIND_LABEL: Record<ContextKind, string> = {
  firewall: "Firewall",
  vdom: "VDOM",
  vrf: L("VRF (Border-Leaf)", "VRF (border leaf)"),
  l3: L("Router/L3-Switch", "Router/L3 switch"), // ADR 0032
};

// Name(n) einer IP aus dem IPAM; ohne Namen das Netz (Beschreibung) – blass
export function Names({ r }: { r: Resolved | null | undefined }) {
  if (r?.names?.length) return <b> {r.names.join(", ")}</b>;
  if (r?.network)
    return (
      <span
        className="faint"
        title={L("kein Name zur IP im IPAM – Netz aus dem IPAM", "no name for the IP in the IPAM – network from the IPAM")}
      >
        {" "}
        {r.network}
      </span>
    );
  return null;
}

// Next-Hops: IP (Name aus dem IPAM) über Interface (Zone); ECMP = mehrere Zeilen
export function Hops({ hops }: { hops: NextHop[] | null | undefined }) {
  if (!hops?.length) return <Dash />;
  return (
    <div className="objs">
      {hops.map((h, i) => (
        <span key={i} className="mono">
          {h.ip ?? L("direkt", "direct")}
          <Names r={h} />
          {h.interface ? <span className="faint"> via {h.interface}</span> : null}
          {h.zone ? <span className="chip" style={{ marginLeft: 4 }}>{h.zone}</span> : null}
        </span>
      ))}
      {hops.length > 1 && (
        <span
          className="chip info"
          style={{ alignSelf: "flex-start" }}
          title={L(
            "Equal-Cost-Multipath: der Verkehr verteilt sich auf alle Next-Hops",
            "Equal-cost multipath: traffic is spread across all next-hops",
          )}
        >
          ECMP ×{hops.length}
        </span>
      )}
    </div>
  );
}

const FOLD_KEY = "automato_routing_folds"; // zugeklappte Bereiche im Routing (0.75.0)

export function Missing({ items }: { items: string[] | null | undefined }) {
  if (!items?.length) return null;
  return (
    <div className="chips">
      {items.map((m) => (
        <span
          key={m}
          className="chip warn"
          title={L(
            "nicht lesbar – die Aussage für diesen Kontext ist „unbekannt“",
            "not readable – the result for this context is “unknown”",
          )}
        >
          {m}
        </span>
      ))}
    </div>
  );
}

// System · Kontext: „fw-01 · VDOM root“, eine Firewall nur mit root heisst wie die Firewall,
// ACI „leaf-101 · VRF T:V“ (darunter der APIC); Router „rt-01“ bzw. „rt-01 · VRF x“ (darunter
// die Quelle Switches & Router, ADR 0032); Hostname, wenn er vom Quellsystem abweicht
export function where(x: Labeled) {
  const label = x.label || `${x.source ?? x.node} · ${x.context}`;
  const sub = x.display === "vrf" || x.display === "l3" ? x.source : x.node !== x.source ? x.node : null;
  return (
    <>
      <b>{label}</b>
      <SharedChip shared={x.shared} />
      {sub && <div className="faint">{sub}</div>}
    </>
  );
}

export function matchText(match: Record<string, unknown> | null | undefined): string {
  const m = match ?? {};
  const parts: string[] = [];
  const list = (k: string) => (Array.isArray(m[k]) ? (m[k] as unknown[]).map(String) : []);
  if (list("input_device").length) parts.push(L(`von ${list("input_device").join(", ")}`, `from ${list("input_device").join(", ")}`));
  const src = [...list("src"), ...list("srcaddr")];
  const dst = [...list("dst"), ...list("dstaddr")];
  if (src.length) parts.push(L(`Quelle ${src.join(", ")}`, `Source ${src.join(", ")}`));
  if (dst.length) parts.push(L(`Ziel ${dst.join(", ")}`, `Destination ${dst.join(", ")}`));
  if (m.protocol) parts.push(L(`Protokoll ${String(m.protocol)}`, `Protocol ${String(m.protocol)}`));
  if (list("ports").length) parts.push(`Port ${list("ports").join("-")}`);
  if (list("service").length) parts.push(L(`Dienst ${list("service").join(", ")}`, `Service ${list("service").join(", ")}`));
  return parts.join(" · ") || L("alles", "everything");
}

// 0.75.0 (Anwender: man muss scrollen, um die Reiter zu sehen): standardmässig zugeklappt,
// zugeklappt bleiben Zahlen und nicht lesbare Kontexte sichtbar
function ContextCard({ summary, onHistory }: { summary: RoutingSummary; onHistory: () => void }) {
  const { fold } = useFolds(FOLD_KEY);
  const f = fold("contexts", false);
  const unread = summary.contexts.filter((c) => c.missing?.length).length;
  const changes = summary.changes_7d ?? 0;
  return (
    <div className="card" id="routing-contexts">
      <div className="card-head">
        <FoldTitle fold={f}>{L("Gelesene Kontexte", "Contexts read")}</FoldTitle>
        <span className="faint">
          {L(
            <>
              {summary.contexts.length} Kontexte · {summary.ha} HA-Cluster · {summary.l3outs} L3Outs
            </>,
            <>
              {summary.contexts.length} contexts · {summary.ha} HA clusters · {summary.l3outs} L3Outs
            </>,
          )}
        </span>
        {unread > 0 && (
          <span
            className="chip warn"
            title={L(
              "Teile des Routings nicht lesbar – die Aussage dort ist „unbekannt“",
              "Parts of the routing not readable – the result there is “unknown”",
            )}
          >
            {unread} {L("mit nicht Lesbarem", "with unreadable parts")}
          </span>
        )}
        {changes > 0 && (
          <button
            className="chip info linkchip"
            id="routing-changes-7d"
            title={L(
              "Änderungen am Routing (Routen, BGP-Nachbarn, HA …) in den letzten 7 Tagen – Verlauf öffnen",
              "Routing changes (routes, BGP neighbors, HA …) in the last 7 days – open the history",
            )}
            onClick={onHistory}
          >
            {L(`${changes} Routing-Änderungen (7 Tage)`, `${changes} routing changes (7 days)`)}
          </button>
        )}
      </div>
      {f.open && (
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>{L("System · Kontext", "System · context")}</th>
              <th>{L("Art", "Type")}</th>
              <th className="num">{L("wirksam", "effective")}</th>
              <th className="num">{L("BGP-Pfade", "BGP paths")}</th>
              <th className="num">{L("statisch", "static")}</th>
              <th className="num">{L("BGP-Nachbarn", "BGP neighbors")}</th>
              <th className="num">Policy/SD-WAN</th>
              <th>{L("Nicht lesbar", "Not readable")}</th>
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
      )}
    </div>
  );
}

// VRF innerhalb eines VDOMs (FortiGate 7.x); "mgmt" = dediziertes Management-Interface
export function VrfChip({ vrf }: { vrf?: number | string | null }) {
  if (vrf == null || vrf === 0) return null;
  return (
    <span
      className="chip warn"
      title={L(
        "eigene Routing-Tabelle im VDOM – nicht die Standard-VRF 0",
        "separate routing table in the VDOM – not the default VRF 0",
      )}
    >
      {vrf === "mgmt" ? "Management" : `VRF ${vrf}`}
    </span>
  );
}

export function policyText(p: PolicyHit) {
  return p.kind === "sdwan"
    ? `SD-WAN ${p.name ?? `#${p.seq}`}`
    : L(`Policy-Route #${p.seq ?? "?"}`, `Policy route #${p.seq ?? "?"}`);
}

const EXIT_KIND: Record<TopoExit["kind"], string> = {
  internet: "Internet",
  mpls: "MPLS",
  wan: "WAN",
  partner: "Partner",
  other: L("Anbindung", "Exit"),
};

export function Port({ p }: { p: TracePort | undefined }) {
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
        <span
          className="chip info"
          style={{ marginLeft: 4 }}
          title={L("Anbindung nach aussen (von Hand erfasst)", "Exit to the outside (captured manually)")}
        >
          {EXIT_KIND[p.exit.kind as TopoExit["kind"]] ?? L("Anbindung", "Exit")} {p.exit.name}
        </span>
      )}
    </div>
  );
}

// Entwurf einer Anbindung (neu bzw. mit `id` eine bestehende ändern/ergänzen) – aus einem Weg,
// der an einem unbekannten Next-Hop endet, bzw. aus einer erkannten Anbindung
type ExitDraft = Partial<TopoExitIn> & { id?: number };
// Weg endet an einem Router ohne gelesenes Gerät: daraus eine Anbindung erfassen
export type Capture = { next_hop: string; source_system_id: number; interface: string | null; kind: TopoExit["kind"] };
// Weg-Suche -> Routing: Anbindung aus einem Weg erfassen (sessionStorage, einmal gelesen)
export const CAPTURE_KEY = "automato_exit_capture";

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
  active: [
    "ok",
    L("aktiv", "active"),
    L("Präfixe in der Routing-Tabelle über diesen Router", "Prefixes in the routing table via this router"),
  ],
  backup: [
    "warn",
    L("Backup", "backup"),
    L(
      "nur als BGP-Pfad bzw. statische Route (übernimmt bei Ausfall)",
      "only as BGP path or static route (takes over on failure)",
    ),
  ],
  neighbor: [
    "",
    L("BGP-Nachbar", "BGP neighbor"),
    L("BGP-Sitzung ohne Routen über diesen Router", "BGP session without routes via this router"),
  ],
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
        <span className={`chip ${down ? "crit" : ""}`} style={{ marginLeft: 4 }} title={L(`BGP-Sitzung: ${r.state ?? "?"}`, `BGP session: ${r.state ?? "?"}`)}>
          AS {r.remote_as}
          {down ? ` · ${r.state}` : ""}
        </span>
      )}
      {r.default && (
        <span className="chip" style={{ marginLeft: 4 }}>
          Default
        </span>
      )}
      {r.uses.length > 1 && (
        <span className="faint">{L(` · ${r.uses.length} Firewalls`, ` · ${r.uses.length} firewalls`)}</span>
      )}
    </span>
  );
}

// Redundanz in Worten: „4 Router · 2 aktiv (ECMP ×2) · 2 Backup · 2 Firewalls“
function redundancy(r: Redundancy): string {
  return [
    L(`${r.routers} Router`, `${r.routers} routers`),
    r.active && L(`${r.active} aktiv`, `${r.active} active`) + (r.ecmp > 1 ? ` (ECMP ×${r.ecmp})` : ""),
    r.backup && L(`${r.backup} Backup`, `${r.backup} backup`),
    r.neighbor && L(`${r.neighbor} nur BGP-Nachbar`, `${r.neighbor} BGP neighbor only`),
    r.firewalls > 1 && L(`${r.firewalls} Firewalls`, `${r.firewalls} firewalls`),
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

function RoutesTab({
  tenantId,
  nodes,
  vrfs,
  mgmt,
  initial = {},
  onHistory,
}: {
  tenantId: number;
  nodes: RoutingNode[];
  vrfs: number[];
  mgmt: boolean;
  initial?: { q?: string; table?: string; context?: string }; // aus einem Link (Befund)
  onHistory: (q: string, context: string) => void; // Verlauf einer Route öffnen (ADR 0030)
}) {
  const [q, setQ] = useState(initial.q ?? "");
  const [ip, setIp] = useState("");
  const [table, setTable] = useState(initial.table ?? "");
  const [context, setContext] = useState(initial.context ?? "");
  const [vrf, setVrf] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ rows: TopoRoute[]; total: number } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const query = useDebounced(q);
  const ipQuery = useDebounced(ip);

  useEffect(() => setOffset(0), [query, ipQuery, table, context, vrf]);
  useEffect(() => {
    setPage(null);
    setError(null);
    // nur die Antwort zur letzten Anfrage: eine ältere (z. B. ungefiltert, langsamer) darf die
    // gefilterte Liste nicht überschreiben
    let current = true;
    // Kontext = Knoten „<Quellsystem>|<VDOM bzw. Tenant:VRF>“ (ACI: alle Border-Leafs)
    const [sid, ...rest] = context ? context.split("|") : [];
    fetchRoutes({
      tenantId,
      q: query,
      ip: ipQuery,
      table,
      vrf,
      sourceId: sid ? Number(sid) : null,
      context: rest.join("|"),
      limit: PAGE,
      offset,
    })
      .then((p) => current && setPage(p))
      .catch((e) => {
        if (!current) return;
        setError(e);
        setPage({ rows: [], total: 0 });
      });
    return () => {
      current = false;
    };
  }, [tenantId, query, ipQuery, table, context, vrf, offset]);

  return (
    <>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder={L("Präfix, Next-Hop, Interface, Protokoll…", "Prefix, next-hop, interface, protocol…")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="search" style={{ flex: "0 1 210px" }}>
          <span className="ico">
            <Icon name="route" />
          </span>
          <input
            placeholder={L("enthält IP…", "contains IP…")}
            title={L(
              "Routen, deren Präfix die IP enthält – längstes Präfix zuerst",
              "Routes whose prefix contains the IP – longest prefix first",
            )}
            value={ip}
            onChange={(e) => setIp(e.target.value)}
          />
        </div>
        <select className="sel" value={table} onChange={(e) => setTable(e.target.value)}>
          <option value="">{L("Tabelle: alle", "Table: all")}</option>
          <option value="rib">{L("wirksam (RIB)", "effective (RIB)")}</option>
          <option value="bgp">{L("BGP-Pfade", "BGP paths")}</option>
          <option value="static">{L("statische Routen", "static routes")}</option>
        </select>
        <select className="sel" value={context} onChange={(e) => setContext(e.target.value)}>
          <option value="">{L("Kontext: alle", "Context: all")}</option>
          {nodes.map((n) => (
            <option key={n.key} value={n.key}>
              {n.label}
            </option>
          ))}
        </select>
        {(vrfs.some((v) => v !== 0) || mgmt) && (
          <select className="sel" aria-label="VRF" value={vrf} onChange={(e) => setVrf(e.target.value)}>
            <option value="">{L("VRF: alle", "VRF: all")}</option>
            {vrfs.map((v) => (
              <option key={v} value={String(v)}>
                {v === 0 ? L("VRF 0 (Standard)", "VRF 0 (default)") : `VRF ${v}`}
              </option>
            ))}
            {mgmt && <option value="mgmt">Management</option>}
          </select>
        )}
      </div>
      <ErrorBox error={error} prefix={L("Routen laden", "Load routes")} />
      <div className="card">
        <div className="card-body scroll viewport">
          {page === null ? (
            <Loading />
          ) : page.rows.length === 0 ? (
            <Empty title={L("Keine Routen", "No routes")}>
              {L("Filter anpassen bzw. Quellsysteme neu einlesen.", "Adjust the filter or collect the source systems again.")}
            </Empty>
          ) : (
            <table className="compact">
              <thead>
                <tr>
                  <th>{L("System · Kontext", "System · context")}</th>
                  <th>{L("Präfix", "Prefix")}</th>
                  <th>VRF</th>
                  <th>{L("Tabelle", "Table")}</th>
                  <th>{L("Protokoll", "Protocol")}</th>
                  <th className="num">{L("Distanz", "Distance")}</th>
                  <th className="num">{L("Metrik", "Metric")}</th>
                  <th>Next-Hop</th>
                  <th>{L("Weitere Angaben", "Further details")}</th>
                </tr>
              </thead>
              <tbody>
                {page.rows.map((r) => {
                  const [cls, label] = TABLE_LABEL[r.table] ?? ["", r.table];
                  const attrs = Object.entries(r.attrs ?? {}).filter(
                    ([k, v]) => v !== null && v !== "" && k !== "vrf" && k !== "mgmt",
                  );
                  const vrfOf = r.attrs?.mgmt ? "mgmt" : ((r.attrs?.vrf as number | undefined) ?? 0);
                  return (
                    <tr key={r.id}>
                      <td>{where(r)}</td>
                      <td className="mono">
                        <b>{r.prefix}</b>
                        {r.changed_at && (
                          <div>
                            <button
                              className="chip info linkchip"
                              title={L(
                                "letzte Änderung laut Verlauf (neu bzw. geändert) – Verlauf dieser Route öffnen",
                                "last change according to the history (new or changed) – open the history of this route",
                              )}
                              onClick={() => onHistory(r.prefix, `${r.source_system_id}|${r.context}`)}
                            >
                              {L("geändert", "changed")} {fmtTime(r.changed_at)}
                            </button>
                          </div>
                        )}
                      </td>
                      <td>{vrfOf === 0 ? <span className="faint">0</span> : <VrfChip vrf={vrfOf} />}</td>
                      <td>
                        <span className={`chip ${cls}`}>{label}</span>
                        {r.table !== "rib" && (
                          <span
                            className={`chip ${r.best ? "ok" : ""}`}
                            title={
                              r.table === "bgp"
                                ? L("bester Pfad bzw. Backup-Weg", "best path or backup path")
                                : L("aktiv bzw. deaktiviert", "active or disabled")
                            }
                          >
                            {r.table === "bgp"
                              ? r.best
                                ? L("bester", "best")
                                : L("Backup", "backup")
                              : r.best
                                ? L("aktiv", "active")
                                : L("inaktiv", "inactive")}
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
    let current = true; // nur die Antwort zur letzten Anfrage
    load()
      .then((r) => current && setRows(r))
      .catch((e) => {
        if (!current) return;
        setError(e);
        setRows([]);
      });
    return () => {
      current = false;
    };
  }, deps); // load haengt nur von deps ab
  return { rows, error };
}

// Filter der kleineren Listen im Browser: Freitext über alle Angaben, System, weitere Auswahl
function useRowFilter<T>(rows: T[] | null, text: (r: T) => unknown[], system: (r: T) => string, initialQ = "") {
  const [q, setQ] = useState(initialQ);
  const [sys, setSys] = useState("");
  const systems = [...new Set((rows ?? []).map(system))].sort();
  const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown =
    rows?.filter((r) => {
      if (sys && system(r) !== sys) return false;
      const hay = JSON.stringify(text(r)).toLowerCase();
      return tokens.every((t) => hay.includes(t));
    }) ?? null;
  return { q, setQ, sys, setSys, systems, shown };
}

function RowFilter({
  f,
  placeholder,
  total,
  children,
}: {
  f: { q: string; setQ: (v: string) => void; sys: string; setSys: (v: string) => void; systems: string[] };
  placeholder: string;
  total: { shown: number; all: number } | null;
  children?: React.ReactNode;
}) {
  return (
    <div className="filterbar">
      <div className="search">
        <span className="ico">
          <Icon name="search" />
        </span>
        <input placeholder={placeholder} value={f.q} onChange={(e) => f.setQ(e.target.value)} />
      </div>
      {f.systems.length > 1 && (
        <select className="sel" aria-label="System" value={f.sys} onChange={(e) => f.setSys(e.target.value)}>
          <option value="">{L("System: alle", "System: all")}</option>
          {f.systems.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      )}
      {children}
      {total && (
        <span className="count">
          {total.shown === total.all ? total.all : L(`${total.shown} von ${total.all}`, `${total.shown} of ${total.all}`)}
        </span>
      )}
    </div>
  );
}

const labelOf = (x: Labeled) => x.label || `${x.source ?? x.node} · ${x.context}`;

function PeersTab({ tenantId }: { tenantId: number }) {
  const { rows: all, error } = useList<TopoPeer>(() => fetchPeers(tenantId), [tenantId]);
  const [state, setState] = useState("");
  const f = useRowFilter(
    all,
    (p) => [labelOf(p), p.peer_ip, p.peer, p.peer_node, p.local_ip, p.remote_as, p.state],
    labelOf,
  );
  const up = (p: TopoPeer) => (p.state ?? "").toLowerCase() === "established";
  const rows = f.shown?.filter((p) => !state || (state === "up") === up(p)) ?? null;
  return (
    <>
      <RowFilter
        f={f}
        placeholder={L("Nachbar, Name, AS, Zustand…", "Neighbor, name, AS, state…")}
        total={all && rows ? { shown: rows.length, all: all.length } : null}
      >
        <select className="sel" aria-label={L("Zustand", "State")} value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">{L("Zustand: alle", "State: all")}</option>
          <option value="up">{L("aufgebaut (Established)", "up (Established)")}</option>
          <option value="down">{L("nicht aufgebaut", "not established")}</option>
        </select>
      </RowFilter>
      <List error={error} rows={rows} empty={L("Keine BGP-Nachbarn gelesen.", "No BGP neighbors read.")}>
        <thead>
          <tr>
            <th>{L("System · Kontext", "System · context")}</th>
            <th>{L("Nachbar", "Neighbor")}</th>
            <th>{L("Gelesen als", "Read as")}</th>
            <th>{L("Lokal", "Local")}</th>
            <th className="num">AS</th>
            <th>{L("Zustand", "State")}</th>
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
    </>
  );
}

function HaTab({ tenantId }: { tenantId: number }) {
  const { rows: all, error } = useList<TopoHa>(() => fetchHa(tenantId), [tenantId]);
  const f = useRowFilter(
    all,
    (h) => [h.source, h.node, h.mode, h.group_name, h.members, h.sync_peers],
    (h) => h.source ?? h.node,
  );
  const rows = f.shown;
  return (
    <>
      <RowFilter
        f={f}
        placeholder={L("System, Gruppe, Mitglied, Session-Sync-Partner…", "System, group, member, session sync partner…")}
        total={all && rows ? { shown: rows.length, all: all.length } : null}
      />
      <List error={error} rows={rows} empty={L("Keine HA-Cluster gelesen.", "No HA clusters read.")}>
        <thead>
          <tr>
            <th>System</th>
            <th>{L("Modus", "Mode")}</th>
            <th>{L("Gruppe", "Group")}</th>
            <th>{L("Mitglieder", "Members")}</th>
            <th>{L("Session-Sync (FGSP)", "Session sync (FGSP)")}</th>
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
                      {m.priority != null && (
                        <span className="faint">{L(` Prio ${m.priority}`, ` Priority ${m.priority}`)}</span>
                      )}
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
    </>
  );
}

// Parallele Firewalls (Verbund, ADR 0020; 0.72.0): je Verbund, was gleich ist und was abweicht –
// Policies, Objekte, Policy-Routen, Routing (je VDOM gegen die führende Firewall) und NAT. Die
// Abweichungen stehen als Befunde unter Findings → Parallele Firewalls.
const PARALLEL_AREAS: { key: keyof ParallelGroup["stats"]; label: string; type: string }[] = [
  { key: "rules", label: "Policies", type: "parallel_rule_mismatch" },
  { key: "objects", label: L("Objekte (gleichnamig)", "Objects (same name)"), type: "parallel_object_mismatch" },
  {
    key: "policy_routes",
    label: L("Policy-Routen & SD-WAN", "Policy routes & SD-WAN"),
    type: "parallel_policy_route_mismatch",
  },
  { key: "routes", label: L("Routen (wirksam und statisch)", "Routes (effective and static)"), type: "parallel_route_mismatch" },
  { key: "nat", label: L("NAT (VIPs, IP-Pools)", "NAT (VIPs, IP pools)"), type: "parallel_nat_mismatch" },
];

function ParallelTab({ tenantId }: { tenantId: number }) {
  const { rows, error } = useList<ParallelGroup>(() => fetchParallelGroups(tenantId), [tenantId]);
  return (
    <>
      <ErrorBox error={error} />
      {rows === null ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Note>
          {L(
            <>
              Kein Verbund paralleler Firewalls. Ein Admin fasst Firewalls mit Session-Sync bzw. gleichen Policies in
              „Regeln &amp; Contracts“ (Vorschlag) oder im Quellsystem zu einem Verbund zusammen.
            </>,
            <>
              No parallel group of firewalls. An admin combines firewalls with session sync or identical policies into a
              parallel group in “Rules &amp; contracts” (suggestion) or in the source system.
            </>,
          )}
        </Note>
      ) : (
        rows.map((g) => (
          <div key={g.group} className="card parallel-compare">
            <div className="card-head">
              <h2>{L(<>Verbund „{g.group}“</>, <>Parallel group “{g.group}”</>)}</h2>
              <span className="faint">
                {g.members.map((m) => m.name).join(" · ")} – {L("führend", "leading")} {g.members[0]?.name}
              </span>
            </div>
            <div className="card-body">
              <table>
                <thead>
                  <tr>
                    <th>{L("Bereich", "Area")}</th>
                    <th className="num">{L("gleich", "same")}</th>
                    <th className="num">{L("abweichend", "different")}</th>
                    <th className="num">{L("fehlt auf einer Firewall", "missing on one firewall")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {PARALLEL_AREAS.map((a) => {
                    const s = g.stats[a.key] ?? {};
                    const bad = (s.differs ?? 0) + (s.missing ?? 0);
                    return (
                      <tr key={a.key}>
                        <td>{a.label}</td>
                        <td className="num">{s.same ?? <Dash />}</td>
                        <td className="num">
                          {s.differs ? <span className="chip warn">{s.differs}</span> : (s.differs ?? <Dash />)}
                        </td>
                        <td className="num">
                          {s.missing ? <span className="chip warn">{s.missing}</span> : (s.missing ?? <Dash />)}
                        </td>
                        <td>
                          {bad > 0 && (
                            <a href={`#findings?type=${a.type}`} className="linkbtn">
                              {L("Befunde", "Findings")}
                            </a>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div className="faint" style={{ marginTop: 6 }}>
                {L(
                  <>
                    Routen je VDOM gegen die führende Firewall: wirksame Routen nach Protokoll und Interface (die Next-Hops
                    sind je Firewall eigene Router), statische Routen mit Distanz, Interface, Gateway und Status; nicht
                    lesbare Tabellen zählen nicht. BGP-Nachbarn werden nicht verglichen.
                  </>,
                  <>
                    Routes per VDOM against the leading firewall: effective routes by protocol and interface (the next-hops
                    are separate routers per firewall), static routes with distance, interface, gateway and status;
                    unreadable tables do not count. BGP neighbors are not compared.
                  </>,
                )}
              </div>
            </div>
          </div>
        ))
      )}
    </>
  );
}

function PolicyTab({ tenantId, initialQ = "" }: { tenantId: number; initialQ?: string }) {
  const { rows: all, error } = useList<TopoPolicyRoute>(() => fetchPolicyRoutes(tenantId), [tenantId]);
  const [kind, setKind] = useState("");
  const f = useRowFilter(
    all,
    (p) => [labelOf(p), p.name, p.seq, p.match, p.gateway, p.gateway_resolved, p.output_device, p.action],
    labelOf,
    initialQ,
  );
  const rows =
    f.shown?.filter((p) => !kind || (kind === "off" ? !p.enabled : p.kind === kind && p.enabled)) ?? null;
  return (
    <>
      <RowFilter
        f={f}
        placeholder={L("Name, Quelle, Ziel, Gateway, Interface…", "Name, source, destination, gateway, interface…")}
        total={all && rows ? { shown: rows.length, all: all.length } : null}
      >
        <select className="sel" aria-label={L("Art", "Type")} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{L("Art: alle", "Type: all")}</option>
          <option value="policy">{L("Policy-Routen (aktiv)", "Policy routes (active)")}</option>
          <option value="sdwan">{L("SD-WAN (aktiv)", "SD-WAN (active)")}</option>
          <option value="off">{L("ausgeschaltet", "disabled")}</option>
        </select>
      </RowFilter>
      <List
        error={error}
        rows={rows}
        empty={L("Keine Policy-Routen oder SD-WAN-Regeln gelesen.", "No policy routes or SD-WAN rules read.")}
      >
        <thead>
          <tr>
            <th>{L("System · Kontext", "System · context")}</th>
            <th>{L("Art", "Type")}</th>
            <th>{L("Bedingung", "Condition")}</th>
            <th>{L("Weg", "Path")}</th>
          </tr>
        </thead>
        <tbody>
          {(rows ?? []).map((p) => (
            <tr key={p.id} className={p.enabled ? "" : "faint"}>
              <td>{where(p)}</td>
              <td>
                <span className="chip">{p.kind === "sdwan" ? "SD-WAN" : L("Policy-Route", "Policy route")}</span>{" "}
                <span className="mono">#{p.seq ?? "?"}</span> {p.name && <b>{p.name}</b>}
                {!p.enabled && <span className="chip">{L("aus", "off")}</span>}
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
    </>
  );
}

function L3OutTab({ tenantId, canEdit }: { tenantId: number; canEdit: boolean }) {
  const [tick, setTick] = useState(0); // neu laden nach einer Zuordnung von Hand (ADR 0034)
  const { rows: all, error } = useList<TopoL3Out>(() => fetchL3Outs(tenantId), [tenantId, tick]);
  const [choices, setChoices] = useState<FirewallChoice[] | null>(null);
  useEffect(() => {
    if (!canEdit) return;
    fetchFirewallChoices(tenantId)
      .then(setChoices)
      .catch(() => setChoices([]));
  }, [tenantId, canEdit]);
  const f = useRowFilter(
    all,
    (o) => [o.name, o.aci_tenant, o.vrf, o.ext_epgs, o.nodes, o.paths, o.peers, o.behind?.firewalls],
    (o) => `Tenant ${o.aci_tenant}`,
  );
  const rows = f.shown;
  return (
    <>
      <RowFilter
        f={f}
        placeholder={L("L3Out, VRF, Leaf, Interface, Peer, Route, Firewall…", "L3Out, VRF, leaf, interface, peer, route, firewall…")}
        total={all && rows ? { shown: rows.length, all: all.length } : null}
      />
      <Note>
        {L(
          <>
            <b>Firewall dahinter</b> (ADR 0034): hinter einem L3Out regelt die Firewall den Verkehr – ihr Routing entscheidet,
            über welchen L3Out eine Gegenstelle kommt, und ein Contract zur externen EPG ist nur ein Hilfs-Contract an ihren
            Regeln. Abgeleitet aus dem Transfernetz bzw. BGP-Peer; von Hand bestätigen, ändern oder „keine“.
          </>,
          <>
            <b>Firewall behind</b> (ADR 0034): behind an L3Out the firewall controls the traffic – its routing decides via
            which L3Out a peer comes, and a contract to the external EPG is only a helper contract on its rules. Derived from
            the transfer network or BGP peer; confirm, change or set “none” by hand.
          </>,
        )}
      </Note>
      <List error={error} rows={rows} empty={L("Keine L3Outs gelesen.", "No L3Outs read.")}>
        <thead>
          <tr>
            <th>L3Out</th>
            <th>VRF</th>
            <th>{L("Border-Leafs", "Border leafs")}</th>
            <th>Interfaces / SVIs</th>
            <th>{L("BGP-Peers", "BGP peers")}</th>
            <th>{L("Firewall dahinter", "Firewall behind")}</th>
          </tr>
        </thead>
        <tbody>
          {(rows ?? []).map((o) => (
            <tr key={o.id} data-l3out={o.name}>
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
                      {n.router_id && (
                        <span className="faint">{L(` · Router-ID ${n.router_id}`, ` · Router ID ${n.router_id}`)}</span>
                      )}
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
              <td>
                <L3OutFirewall
                  tenantId={tenantId}
                  l3out={o}
                  canEdit={canEdit}
                  choices={choices ?? []}
                  onChanged={() => setTick((t) => t + 1)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </List>
    </>
  );
}

const ORIGIN_LABEL: Record<L3OutBehind["origin"], [string, string, string]> = {
  derived: ["info", L("abgeleitet", "derived"), L("aus Transfernetz bzw. BGP-Peer abgeleitet", "derived from the transfer network or BGP peer")],
  confirmed: ["ok", L("bestätigt", "confirmed"), L("von Hand bestätigt", "confirmed by hand")],
  manual: ["warn", L("von Hand", "by hand"), L("von Hand gewählt", "chosen by hand")],
  none: ["na", L("keine", "none"), L("von Hand: keine Firewall – der Contract regelt allein", "by hand: no firewall – the contract alone controls access")],
};

// Firewall hinter einem L3Out (ADR 0034): Anzeige mit Herkunft, für Operatoren bestätigen, ändern,
// „keine“ und zurücksetzen (wieder abgeleitet)
function L3OutFirewall({
  tenantId,
  l3out,
  canEdit,
  choices,
  onChanged,
}: {
  tenantId: number;
  l3out: TopoL3Out;
  canEdit: boolean;
  choices: FirewallChoice[];
  onChanged: () => void;
}) {
  const b = l3out.behind;
  const [edit, setEdit] = useState(false);
  const [fw, setFw] = useState("");
  const [iface, setIface] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  if (!b) return <Dash />;
  const [chip, label, title] = ORIGIN_LABEL[b.origin];
  const chosen = choices.find((c) => `${c.source_system_id}|${c.vdom}` === fw);
  const run = async (body: { confirm?: boolean; none?: boolean; firewalls?: { source_system_id: number; vdom: string; interface?: string | null }[] }) => {
    setBusy(true);
    setErr(null);
    try {
      await setL3OutFirewall({ tenant_id: tenantId, source_system_id: l3out.source_system_id, dn: l3out.dn, ...body });
      setEdit(false);
      onChanged();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    setBusy(true);
    setErr(null);
    try {
      await removeL3OutFirewall(tenantId, l3out.source_system_id, l3out.dn);
      onChanged();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="objs l3out-fw">
      {b.firewalls.length ? (
        b.firewalls.map((x) => (
          <span key={`${x.node}|${x.interface ?? ""}`} className="mono">
            <b>{x.label}</b>
            {x.interface && <span className="faint"> · {x.interface}</span>}
            {x.zone && <span className="chip" style={{ marginLeft: 4 }}>{x.zone}</span>}
          </span>
        ))
      ) : (
        <span className="faint">
          {b.origin === "none" ? L("keine Firewall", "no firewall") : L("nicht erkannt", "not detected")}
        </span>
      )}
      <span>
        <span className={`chip ${chip}`} title={title}>
          {label}
        </span>
        {b.updated_by && b.origin !== "derived" && <span className="faint"> · {b.updated_by}</span>}
      </span>
      {canEdit && !edit && (
        <span className="no-print">
          {b.origin === "derived" && b.derived.length > 0 && (
            <button className="linkbtn" disabled={busy} onClick={() => run({ confirm: true })}>
              {L("bestätigen", "confirm")}
            </button>
          )}{" "}
          <button className="linkbtn" disabled={busy} onClick={() => setEdit(true)}>
            {L("ändern", "change")}
          </button>{" "}
          {b.origin !== "derived" && (
            <button className="linkbtn" disabled={busy} onClick={reset} title={L("wieder abgeleitet", "derived again")}>
              {L("zurücksetzen", "reset")}
            </button>
          )}
        </span>
      )}
      {canEdit && edit && (
        <span className="l3out-fw-edit no-print">
          <select
            className="sel"
            aria-label={L("Firewall", "Firewall")}
            value={fw}
            onChange={(e) => {
              setFw(e.target.value);
              setIface("");
            }}
          >
            <option value="">{L("Firewall wählen…", "Choose firewall…")}</option>
            {choices.map((c) => (
              <option key={`${c.source_system_id}|${c.vdom}`} value={`${c.source_system_id}|${c.vdom}`}>
                {c.label}
              </option>
            ))}
          </select>
          <select
            className="sel"
            aria-label={L("Interface zum L3Out", "Interface to the L3Out")}
            value={iface}
            disabled={!chosen}
            onChange={(e) => setIface(e.target.value)}
          >
            <option value="">{L("Interface: –", "Interface: –")}</option>
            {(chosen?.interfaces ?? []).map((i) => (
              <option key={i.name} value={i.name}>
                {i.name}
                {i.zone ? ` (${i.zone})` : ""}
              </option>
            ))}
          </select>
          <button
            className="btn btn-sm"
            disabled={busy || !chosen}
            onClick={() => chosen && run({ firewalls: [{ source_system_id: chosen.source_system_id, vdom: chosen.vdom, interface: iface || null }] })}
          >
            {L("Speichern", "Save")}
          </button>
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => run({ none: true })}>
            {L("keine Firewall", "no firewall")}
          </button>
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => setEdit(false)}>
            {L("Abbrechen", "Cancel")}
          </button>
        </span>
      )}
      <ErrorBox error={err} onClose={() => setErr(null)} />
    </div>
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
  const card = useRef<HTMLDivElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    fetchExitSuggestions(tenantId, { covered: true, dismissed: true })
      .then(setDetected)
      .catch(() => setDetected([]));
  }, [tenantId]);
  // geöffnet (neu, übernommen, ändern): zum Formular springen, Name bereit zur Eingabe
  useEffect(() => {
    card.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    nameInput.current?.focus({ preventScroll: true });
  }, []);
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
    <div className="card" id="exit-form" ref={card} style={{ scrollMarginTop: 72 }}>
      <div className="card-head">
        <h2>
          {draft.id ? L(`Anbindung „${draft.name}“`, `Exit “${draft.name}”`) : L("Neue Anbindung", "New exit")}
        </h2>
        <span className="faint">
          {L(
            <>
              Ein Weg, der über diesen Next-Hop bzw. dieses Interface geht und dort kein gelesenes Gerät findet, endet hier
              – oder geht nach den manuellen Routen weiter.
            </>,
            <>
              A path that goes via this next-hop or this interface and finds no device read there ends here – or continues
              along the manual routes.
            </>,
          )}
        </span>
      </div>
      <div className="card-body" style={{ padding: 16 }}>
        <ErrorBox error={error} prefix={L("Anbindung speichern", "Save exit")} />
        <div className="form-grid">
          <div className="field">
            <label htmlFor="ex-name">Name</label>
            <input
              id="ex-name"
              ref={nameInput}
              className="input"
              placeholder={L("z. B. Internet Provider A", "e.g. Internet Provider A")}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ex-kind">{L("Art", "Type")}</label>
            <select id="ex-kind" className="sel input" value={kind} onChange={(e) => setKind(e.target.value as TopoExit["kind"])}>
              {Object.entries(EXIT_KIND).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ex-ifaces">{L("Interfaces bzw. Zonen", "Interfaces or zones")}</label>
            <input id="ex-ifaces" className="input" placeholder="wan1, internet" value={ifaces} onChange={(e) => setIfaces(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ex-hops">{L("Next-Hops (Gegenstelle)", "Next-hops (peer)")}</label>
            <input id="ex-hops" className="input" placeholder="198.51.100.1" value={hops} onChange={(e) => setHops(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ex-nets">{L("Netze dahinter", "Networks behind")}</label>
            <input
              id="ex-nets"
              className="input"
              placeholder={L("leer = alles, was dorthin geroutet wird", "empty = everything routed there")}
              value={prefixes}
              onChange={(e) => setPrefixes(e.target.value)}
            />
          </div>
        </div>
        <div className="field" id="ex-fws">
          <label>
            {L("Firewalls bzw. ACI", "Firewalls or ACI")}{" "}
            {sids.length ? "" : <span className="faint">{L("– keine gewählt = alle", "– none selected = all")}</span>}
          </label>
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
              {L("Erkannte Router", "Detected routers")}{" "}
              <span className="faint">
                – {redundancy(combined(related, routers))}
                {L("; ankreuzen = als Next-Hop eintragen", "; check = enter as next-hop")}
              </span>
            </label>
            {routers.map((r) => (
              <label key={r.ip} className="check">
                <input type="checkbox" checked={hopList.includes(r.ip)} onChange={() => toggle(r.ip)} />
                <RouterCell r={r} />
                {r.exit && !hopList.includes(r.ip) && (
                  <span className="faint">{L(`(über ${r.exit.name})`, `(via ${r.exit.name})`)}</span>
                )}
              </label>
            ))}
            {routers.some((r) => !hopList.includes(r.ip)) && (
              <button className="btn btn-sm" style={{ marginTop: 6 }} onClick={takeAll}>
                {L("alle Router übernehmen", "add all routers")}
              </button>
            )}
          </div>
        )}
        <div className="field">
          <label>{L("Manuelle Routen (z. B. MPLS zu einem anderen Standort)", "Manual routes (e.g. MPLS to another site)")}</label>
          {routes.map((r, i) => (
            <div key={i} className="filterbar" style={{ margin: "0 0 6px" }}>
              <input
                className="input"
                style={{ flex: "0 1 200px" }}
                placeholder="10.60.0.0/16"
                value={r.prefix}
                onChange={(e) => setRoutes(routes.map((x, j) => (j === i ? { ...x, prefix: e.target.value } : x)))}
              />
              <span className="faint">{L("weiter bei", "continue at")}</span>
              <select
                className="sel"
                value={r.target}
                onChange={(e) => setRoutes(routes.map((x, j) => (j === i ? { ...x, target: e.target.value } : x)))}
              >
                <option value="">{L("Knoten wählen…", "Select node…")}</option>
                {nodes.map((n) => (
                  <option key={n.key} value={n.key}>
                    {n.label}
                  </option>
                ))}
              </select>
              <button className="linkbtn" onClick={() => setRoutes(routes.filter((_, j) => j !== i))}>
                {L("entfernen", "remove")}
              </button>
            </div>
          ))}
          <button className="btn btn-sm" onClick={() => setRoutes([...routes, { prefix: "", target: "" }])}>
            {L("Route hinzufügen", "Add route")}
          </button>
        </div>
        <div className="field">
          <label htmlFor="ex-desc">{L("Beschreibung", "Description")}</label>
          <input id="ex-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary btn-sm" disabled={busy || !name.trim()} onClick={save}>
            {busy ? L("Speichere…", "Saving…") : L("Speichern", "Save")}
          </button>
          <button className="btn btn-sm" onClick={onCancel}>
            {L("Abbrechen", "Cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}

// Erkannte Anbindungen: Router ohne gelesenes Gerät (Next-Hops, BGP-Nachbarn), je Firewall bzw.
// Verbund/Session-Sync-Partner zusammengefasst, die noch nicht (ganz) erfasst sind; „ausblenden“,
// wenn es keine Anbindung ist (ausgeblendete auf Wunsch mit „wieder anzeigen“)
function SuggestedExits({
  rows,
  canEdit,
  onTake,
  onDismiss,
}: {
  rows: ExitSuggestion[];
  canEdit: boolean;
  onTake: (s: ExitSuggestion) => void;
  onDismiss: (s: ExitSuggestion, undo: boolean) => void;
}) {
  const [showHidden, setShowHidden] = useState(false);
  const hidden = rows.filter((s) => s.dismissed);
  const shown = showHidden ? rows : rows.filter((s) => !s.dismissed);
  if (!rows.length) return null;
  return (
    <div className="card" id="exit-suggestions">
      <div className="card-head">
        <h2>{L("Erkannte Anbindungen", "Detected exits")}</h2>
        <span className="faint">
          {L(
            <>
              Router ohne gelesenes Gerät aus Routing-Tabelle, BGP-Pfaden, statischen Routen und BGP-Nachbarn – zusammengefasst
              über gemeinsame Router, Interface bzw. Zone und BGP-AS, je Firewall bzw. Verbund und Session-Sync-Partner.
            </>,
            <>
              Routers without a device read, from routing table, BGP paths, static routes and BGP neighbors – combined by
              shared routers, interface or zone and BGP AS, per firewall or parallel group and session sync partner.
            </>,
          )}
        </span>
        {hidden.length > 0 && (
          <button className="btn btn-sm" style={{ marginLeft: "auto" }} onClick={() => setShowHidden(!showHidden)}>
            {showHidden
              ? L("Ausgeblendete verbergen", "Hide hidden suggestions")
              : L(
                  `${hidden.length} ausgeblendete${hidden.length > 1 ? "" : "r"} Vorschlag${hidden.length > 1 ? "e" : ""}`,
                  `${hidden.length} hidden suggestion${hidden.length > 1 ? "s" : ""}`,
                )}
          </button>
        )}
      </div>
      <div className="card-body scroll">
        {shown.length === 0 ? (
          <Empty title={L("Keine offenen Vorschläge", "No open suggestions")}>
            {L("Alle erkannten Router sind erfasst oder ausgeblendet.", "All detected routers are captured or hidden.")}
          </Empty>
        ) : (
          <table className="compact">
            <thead>
              <tr>
                <th>{L("Vorschlag", "Suggestion")}</th>
                <th>Firewalls</th>
                <th>{L("Interfaces / Zonen", "Interfaces / zones")}</th>
                <th>Router</th>
                <th>{L("Redundanz", "Redundancy")}</th>
                {canEdit && <th />}
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.key} className={s.dismissed ? "muted" : undefined}>
                  <td>
                    <span className="chip info">{EXIT_KIND[s.kind] ?? s.kind}</span> <b>{s.name}</b>
                    {s.dismissed && (
                      <span className="chip" style={{ marginLeft: 4 }}>
                        {L("ausgeblendet", "hidden")}
                      </span>
                    )}
                    {s.exits.length > 0 && (
                      <div className="faint">
                        {L("teilweise in", "partly in")} {s.exits.map((e) => e.name).join(", ")}
                      </div>
                    )}
                  </td>
                  <td>{s.sources.join(", ")}</td>
                  <td className="mono">{s.interfaces.join(", ") || "–"}</td>
                  <td>
                    <div className="objs">
                      {s.next_hops.map((h) => (
                        <span key={h.ip} className="nowrap">
                          <RouterCell r={h} />
                          {h.covered && <span className="faint">{L(" · erfasst", " · captured")}</span>}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>{redundancy(s.redundancy)}</td>
                  {canEdit && (
                    <td className="actions nowrap">
                      {s.dismissed ? (
                        <button
                          className="linkbtn"
                          title={L("wieder als Anbindung vorschlagen", "suggest as exit again")}
                          onClick={() => onDismiss(s, true)}
                        >
                          {L("wieder anzeigen", "show again")}
                        </button>
                      ) : (
                        <>
                          <button className="linkbtn" onClick={() => onTake(s)}>
                            {s.exits.length ? L("ergänzen", "extend") : L("übernehmen", "apply")}
                          </button>{" "}
                          <button
                            className="linkbtn"
                            title={L(
                              "keine Anbindung – nicht mehr vorschlagen (bis ein neuer Router dazukommt)",
                              "not an exit – stop suggesting (until a new router appears)",
                            )}
                            onClick={() => onDismiss(s, false)}
                          >
                            {L("ausblenden", "hide")}
                          </button>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
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
  const [opened, setOpened] = useState(0); // jedes Öffnen neu: Formular frisch, Sprung dorthin
  const edit = (d: ExitDraft) => {
    setEditing(d);
    setOpened((n) => n + 1);
  };
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    fetchExitSuggestions(tenantId, { dismissed: true })
      .then(setSuggested)
      .catch(() => setSuggested([]));
  }, [tenantId, reload]);
  // ausblenden: die offenen Router des Vorschlags; wieder anzeigen: die ausgeblendeten
  const dismiss = (s: ExitSuggestion, undo: boolean) => {
    const ips = s.next_hops.filter((h) => (undo ? h.dismissed : !h.covered)).map((h) => h.ip);
    dismissExits(tenantId, ips, undo)
      .then(() => setReload((n) => n + 1))
      .catch(setErr);
  };
  const label = (key: string) => nodes.find((n) => n.key === key)?.label ?? key;
  const done = () => {
    setEditing(null);
    onDraftUsed();
    setReload((n) => n + 1);
  };
  const remove = (e: TopoExit) => {
    if (!window.confirm(L(`Anbindung „${e.name}“ löschen?`, `Delete exit “${e.name}”?`))) return;
    deleteExit(e.id)
      .then(() => setReload((n) => n + 1))
      .catch(setErr);
  };
  return (
    <>
      <div className="filterbar">
        <span className="faint">
          {L(
            <>
              Von Hand erfasst: wo Wege nach aussen gehen (Internet, MPLS, WAN, Partner). Ohne Anbindung endet ein Weg am
              ersten Next-Hop, der keinem gelesenen Gerät gehört.
            </>,
            <>
              Captured manually: where paths go to the outside (Internet, MPLS, WAN, partner). Without an exit, a path ends
              at the first next-hop that belongs to no device read.
            </>,
          )}
        </span>
        {canEdit && editing === null && (
          <button className="btn btn-primary btn-sm" style={{ marginLeft: "auto" }} onClick={() => edit({})}>
            {L("Neue Anbindung", "New exit")}
          </button>
        )}
      </div>
      <ErrorBox error={err} prefix={L("Anbindung ändern", "Change exit")} />
      {editing !== null && (
        <ExitForm
          key={opened}
          tenantId={tenantId}
          nodes={nodes}
          draft={editing}
          onSaved={done}
          onCancel={done}
        />
      )}
      <List error={error} rows={rows} empty={L("Noch keine Anbindungen erfasst.", "No exits captured yet.")}>
        <thead>
          <tr>
            <th>{L("Anbindung", "Exit")}</th>
            <th>Firewalls</th>
            <th>{L("Interfaces / Zonen", "Interfaces / zones")}</th>
            <th>Next-Hops</th>
            <th>{L("Erkannte Router · Redundanz", "Detected routers · redundancy")}</th>
            <th>{L("Netze dahinter", "Networks behind")}</th>
            <th>{L("Manuelle Routen", "Manual routes")}</th>
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
                <td>{e.sources.length ? e.sources.join(", ") : <span className="faint">{L("alle", "all")}</span>}</td>
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
                            {!r.listed && (
                              <span className="chip warn" style={{ marginLeft: 4 }}>
                                {L("nicht eingetragen", "not entered")}
                              </span>
                            )}
                          </span>
                        ))}
                      </div>
                    </>
                  ) : (
                    <span className="faint">{L("keiner erkannt", "none detected")}</span>
                  )}
                  {st?.unseen.length ? (
                    <div
                      className="faint"
                      title={L(
                        "eingetragen, aber in keiner Route und keinem BGP-Nachbarn",
                        "entered, but in no route and no BGP neighbor",
                      )}
                    >
                      {L("nicht erkannt:", "not detected:")} {st.unseen.join(", ")}
                    </div>
                  ) : null}
                  {canEdit && st?.missing.length ? (
                    <button
                      className="linkbtn"
                      onClick={() => edit({ ...exitDraft(e), next_hops: union(e.next_hops, st.missing) })}
                    >
                      {L(`${st.missing.length} erkannte Router übernehmen`, `Add ${st.missing.length} detected routers`)}
                    </button>
                  ) : null}
                </td>
                <td className="mono">{e.prefixes.join(", ") || <span className="faint">{L("alles", "everything")}</span>}</td>
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
                    <button className="linkbtn" onClick={() => edit(exitDraft(e))}>
                      {L("ändern", "edit")}
                    </button>{" "}
                    <button className="linkbtn" onClick={() => remove(e)}>
                      {L("löschen", "delete")}
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </List>
      {/* Vorschläge zu unterst; „übernehmen“ springt hoch zum Formular */}
      <SuggestedExits
        rows={suggested}
        canEdit={canEdit}
        onTake={(s) => edit(draftFrom(s, rows ?? []))}
        onDismiss={dismiss}
      />
    </>
  );
}

// -- Verlauf des Routings (ADR 0030) ---------------------------------------------------------

const CHANGE_KIND: Record<TopoChangeKind, string> = {
  route: "Route",
  bgp_peer: L("BGP-Nachbar", "BGP neighbor"),
  ha: "HA",
  policy_route: L("Policy-Route/SD-WAN", "Policy route/SD-WAN"),
  context: L("Kontext", "Context"),
  l3out: "L3Out",
};

const CHANGE_TYPE: Record<TopoChangeType, [string, string, string]> = {
  added: ["ok", L("hinzugefügt", "added"), L("neu gegenüber dem vorigen Lauf", "new compared with the previous run")],
  removed: ["crit", L("entfernt", "removed"), L("im vorigen Lauf noch da", "still present in the previous run")],
  changed: ["warn", L("geändert", "changed"), L("verglichene Felder anders als im vorigen Lauf", "compared fields differ from the previous run")],
  baseline: ["info", L("Ausgangsstand", "Baseline"), L("erster Lauf des Quellsystems – der Verlauf beginnt hier", "first run of the source system – the history starts here")],
  unreadable: [
    "warn",
    L("nicht lesbar", "not readable"),
    L(
      "Teile des Kontexts nicht lesbar – dort nicht verglichen, nichts als entfernt gezählt",
      "Parts of the context not readable – not compared there, nothing counted as removed",
    ),
  ],
  truncated: [
    "info",
    L("Obergrenze", "Limit reached"),
    L("mehr Änderungen in diesem Lauf als einzeln gespeichert werden", "more changes in this run than are stored individually"),
  ],
};

const CHANGE_TABLE: Record<string, string> = {
  rib: L("wirksam", "effective"),
  bgp: L("BGP-Pfad", "BGP path"),
  static: L("statisch", "static"),
  policy: L("Policy-Route", "Policy route"),
  sdwan: "SD-WAN",
};

// Datum (Eingabe, Ortszeit) -> Beginn des Tages bzw. des Folgetags als ISO-Zeitpunkt
function dayStart(value: string, plus = 0): string | undefined {
  if (!value) return undefined;
  const d = new Date(`${value}T00:00`);
  if (Number.isNaN(d.getTime())) return undefined;
  d.setDate(d.getDate() + plus);
  return d.toISOString();
}

function RunLink({ c }: { c: TopoChange }) {
  if (!c.collector_run_id) return <Dash />;
  const short = `#${c.collector_run_id.slice(0, 8)}`;
  if (!c.run_exists)
    return (
      <span
        className="faint"
        title={L("Lauf nicht mehr gespeichert (Aufbewahrung)", "Run no longer stored (retention)")}
      >
        {short}
      </span>
    );
  return (
    <a
      href={`#laeufe?source_system_id=${c.source_system_id}&run=${encodeURIComponent(c.collector_run_id)}`}
      title={L("Lauf in der Ansicht Läufe zeigen", "Show the run in the runs view")}
    >
      {short}
    </a>
  );
}

function ChangeRows({ c, open, onToggle }: { c: TopoChange; open: boolean; onToggle: () => void }) {
  const [cls, label, title] = CHANGE_TYPE[c.change] ?? ["", c.change, ""];
  return (
    <>
      <tr className={open ? "selected" : undefined}>
        <td>
          {c.fields.length > 0 && (
            <button
              className="linkbtn"
              aria-expanded={open}
              title={L("Felder vorher/nachher", "Fields before/after")}
              onClick={onToggle}
            >
              {open ? "▾" : "▸"}
            </button>
          )}
        </td>
        <td className="mono">{fmtTime(c.at)}</td>
        <td>
          <b>{c.label}</b>
          <SharedChip shared={c.shared} />
          {!c.label.startsWith(c.source) && <div className="faint">{c.source}</div>}
        </td>
        <td>
          <span className="chip">{CHANGE_KIND[c.kind] ?? c.kind}</span>
          {c.table && <span className="chip">{CHANGE_TABLE[c.table] ?? c.table}</span>}
        </td>
        <td className="mono">{c.key || <Dash />}</td>
        <td>
          <span className={`chip ${cls}`} title={title}>
            {label}
          </span>
        </td>
        <td className="change-summary">{c.summary}</td>
        <td className="mono">
          <RunLink c={c} />
        </td>
      </tr>
      {open && (
        <tr className="change-detail">
          <td />
          <td colSpan={7}>
            <table className="compact">
              <thead>
                <tr>
                  <th>{L("Feld", "Field")}</th>
                  <th>{L("vorher", "before")}</th>
                  <th>{L("nachher", "after")}</th>
                </tr>
              </thead>
              <tbody>
                {c.fields.map((f) => (
                  <tr key={f.field}>
                    <td>{f.label}</td>
                    <td className="mono">{f.before ?? <Dash />}</td>
                    <td className="mono">{f.after ?? <Dash />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

// Verlauf: neuster Lauf zuerst, darin Kontexte, HA, Nachbarn, Policy-Routen, L3Outs, Routen
function ChangesTab({
  tenantId,
  nodes,
  initial = {},
}: {
  tenantId: number;
  nodes: RoutingNode[];
  initial?: { q?: string; context?: string }; // aus einer Route bzw. einem Link
}) {
  const [q, setQ] = useState(initial.q ?? "");
  const [source, setSource] = useState(initial.context ? initial.context.split("|")[0] : "");
  const [context, setContext] = useState(initial.context ?? "");
  const [kind, setKind] = useState("");
  const [change, setChange] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<TopoChange> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const query = useDebounced(q);
  const filtered = !!(query || source || context || kind || change || from || to);
  const sources = [...new Map(nodes.map((n) => [n.source_system_id, n.source])).entries()].sort((a, b) =>
    a[1].localeCompare(b[1], undefined, { numeric: true }),
  );
  const contexts = nodes.filter((n) => !source || String(n.source_system_id) === source);

  useEffect(() => setOffset(0), [query, source, context, kind, change, from, to]);
  useEffect(() => {
    setPage(null);
    setError(null);
    let current = true; // nur die Antwort zur letzten Anfrage
    // Kontext = Knoten „<Quellsystem>|<VDOM bzw. Tenant:VRF>“ (ACI: alle Border-Leafs)
    const [sid, ...rest] = context ? context.split("|") : [source];
    fetchRoutingChanges({
      tenantId,
      sourceId: sid ? Number(sid) : null,
      context: context ? rest.join("|") : undefined,
      kind,
      change,
      q: query,
      since: dayStart(from),
      until: dayStart(to, 1),
      limit: PAGE,
      offset,
    })
      .then((p) => current && setPage(p))
      .catch((e) => {
        if (!current) return;
        setError(e);
        setPage({ rows: [], total: 0 });
      });
    return () => {
      current = false;
    };
  }, [tenantId, query, source, context, kind, change, from, to, offset]);

  const toggle = (id: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <>
      <div className="filterbar" id="routing-changes-filter">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder={L("Präfix, Next-Hop, Nachbar…", "Prefix, next-hop, neighbor…")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select
          className="sel"
          aria-label={L("Quellsystem", "Source system")}
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setContext("");
          }}
        >
          <option value="">{L("Quellsystem: alle", "Source system: all")}</option>
          {sources.map(([id, name]) => (
            <option key={id} value={String(id)}>
              {name}
            </option>
          ))}
        </select>
        <select className="sel" aria-label={L("Kontext", "Context")} value={context} onChange={(e) => setContext(e.target.value)}>
          <option value="">{L("Kontext: alle", "Context: all")}</option>
          {contexts.map((n) => (
            <option key={n.key} value={n.key}>
              {n.label}
            </option>
          ))}
        </select>
        <select className="sel" aria-label={L("Art", "Kind")} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{L("Art: alle", "Kind: all")}</option>
          {(Object.keys(CHANGE_KIND) as TopoChangeKind[]).map((k) => (
            <option key={k} value={k}>
              {CHANGE_KIND[k]}
            </option>
          ))}
        </select>
        <select className="sel" aria-label={L("Änderung", "Change")} value={change} onChange={(e) => setChange(e.target.value)}>
          <option value="">{L("Änderung: alle", "Change: all")}</option>
          <option value="added">{L("hinzugefügt", "added")}</option>
          <option value="removed">{L("entfernt", "removed")}</option>
          <option value="changed">{L("geändert", "changed")}</option>
          <option value="note">{L("Vermerke (Ausgangsstand, nicht lesbar, Obergrenze)", "Notes (baseline, not readable, limit)")}</option>
        </select>
        <input
          type="date"
          className="input input-inline"
          aria-label={L("von (Datum)", "from (date)")}
          title={L("ab diesem Tag", "from this day")}
          value={from}
          max={to || undefined}
          onChange={(e) => setFrom(e.target.value)}
        />
        <span className="faint">–</span>
        <input
          type="date"
          className="input input-inline"
          aria-label={L("bis (Datum)", "to (date)")}
          title={L("bis und mit diesem Tag", "up to and including this day")}
          value={to}
          min={from || undefined}
          onChange={(e) => setTo(e.target.value)}
        />
        {page && <span className="count">{L(`${page.total} Einträge`, `${page.total} entries`)}</span>}
      </div>
      <ErrorBox error={error} prefix={L("Verlauf laden", "Load history")} />
      <div className="card" id="routing-changes">
        <div className="card-body scroll viewport">
          {page === null ? (
            <Loading />
          ) : page.rows.length === 0 ? (
            filtered ? (
              <Empty title={L("Keine Änderungen", "No changes")}>{L("Filter anpassen.", "Adjust the filter.")}</Empty>
            ) : (
              <Empty title={L("Noch kein Verlauf", "No history yet")}>
                {L(
                  "Verlauf beginnt mit dem nächsten Lauf: ab dann vergleicht jeder Lauf einer FortiGate bzw. eines APIC das Routing mit dem vorigen Stand.",
                  "The history starts with the next run: from then on every run of a FortiGate or an APIC compares the routing with the previous state.",
                )}
              </Empty>
            )
          ) : (
            <table className="compact routing-changes">
              <thead>
                <tr>
                  <th />
                  <th>{L("Zeit", "Time")}</th>
                  <th>{L("Gerät · Kontext", "Device · context")}</th>
                  <th>{L("Art", "Kind")}</th>
                  <th>{L("Schlüssel", "Key")}</th>
                  <th>{L("Änderung", "Change")}</th>
                  <th>{L("Vorher → nachher", "Before → after")}</th>
                  <th>{L("Lauf", "Run")}</th>
                </tr>
              </thead>
              <tbody>
                {page.rows.map((c) => (
                  <ChangeRows key={c.id} c={c} open={open.has(c.id)} onToggle={() => toggle(c.id)} />
                ))}
              </tbody>
            </table>
          )}
        </div>
        {page && page.total > 0 && <Pager total={page.total} offset={offset} onOffset={setOffset} pageSize={PAGE} />}
      </div>
      <div className="faint" style={{ marginTop: 8 }}>
        {L(
          <>
            Je Lauf verglichen mit dem vorigen Stand: Next-Hops, Interface, Protokoll, Distanz/Metrik, aktiv/Backup,
            BGP-Zustand und AS, HA-Mitglieder, Policy-Routen und L3Outs. Reihenfolge und Zähler zählen nicht; ein nicht lesbarer
            Kontext ist ein Vermerk statt vieler „entfernt“. Frist: <code>AUTOMATO_RETENTION_TOPO_CHANGES_DAYS</code> (180
            Tage).
          </>,
          <>
            Each run is compared with the previous state: next-hops, interface, protocol, distance/metric, active/backup, BGP
            state and AS, HA members, policy routes and L3Outs. Order and counters do not count; a context that is not readable
            is one note instead of many “removed”. Retention: <code>AUTOMATO_RETENTION_TOPO_CHANGES_DAYS</code> (180 days).
          </>,
        )}
      </div>
    </>
  );
}

export default function Routing({
  tenantId,
  tenantLabel,
  canEdit = false,
  params = {},
}: {
  tenantId: number | null;
  tenantLabel: string;
  canEdit?: boolean;
  params?: Record<string, string>; // aus einem Link: tab, context, table, q
}) {
  const [tab, setTab] = useState<Tab>(
    TABS.some((t) => t.key === params.tab) ? (params.tab as Tab) : "routes",
  );
  const [draft, setDraft] = useState<ExitDraft | null>(null);
  const [summary, setSummary] = useState<RoutingSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  // Verlauf mit Filter öffnen (aus einer Route bzw. einem Link #routing?tab=changes&q=…)
  const [history, setHistory] = useState<{ q?: string; context?: string }>(
    params.tab === "changes" ? { q: params.q, context: params.context } : {},
  );
  const openHistory = (q = "", context = "") => {
    setHistory({ q, context });
    setTab("changes");
  };

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

  // Weg endet an einem Router ohne gelesenes Gerät: die erkannte Anbindung mit allen Routern und
  // Firewalls vorschlagen (bzw. die Anbindung ergänzen, die schon einen Teil abdeckt)
  function capture(c: Capture) {
    if (tenantId === null) return;
    const plain: ExitDraft = {
      kind: c.kind,
      source_system_ids: [c.source_system_id],
      interfaces: c.interface ? [c.interface] : [],
      next_hops: [c.next_hop],
    };
    Promise.all([fetchExitSuggestions(tenantId, { covered: true, dismissed: true }), fetchExits(tenantId)])
      .then(([found, exits]) => {
        const s = found.find((x) => x.next_hops.some((h) => h.ip === c.next_hop));
        return s ? draftFrom(s, exits) : plain;
      })
      .catch(() => plain)
      .then((d) => {
        setDraft(d);
        setTab("exits");
      });
  }

  // aus der Weg-Suche: „Als Anbindung erfassen“ (einmal, dann gelöscht)
  useEffect(() => {
    if (tenantId === null || !canEdit) return;
    let raw: string | null = null;
    try {
      raw = sessionStorage.getItem(CAPTURE_KEY);
      sessionStorage.removeItem(CAPTURE_KEY);
    } catch {
      /* sessionStorage nicht verfügbar */
    }
    if (raw) capture(JSON.parse(raw) as Capture);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, canEdit]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title="Routing" />
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      </>
    );
  }
  const nodes = summary?.nodes ?? [];
  return (
    <>
      <PageHead title="Routing">
        {L(
          <>
            Routing-Tabellen von „{tenantLabel}“ je Firewall bzw. FortiGate-VDOM und je ACI-VRF auf den Border-Leafs,
            dazu BGP-Pfade (auch Backup-Wege), Nachbarn, HA und Session-Sync, Policy-Routen, L3Outs und Anbindungen nach
            aussen – rein lesend, Stand des letzten Laufs; IPs mit Namen aus dem IPAM. Den Weg einer Verbindung zeigt die{" "}
            <a href="#wege">Weg-Suche</a>.
          </>,
          <>
            Routing tables of “{tenantLabel}” per firewall or FortiGate VDOM and per ACI VRF on the border leafs, plus BGP
            paths (including backup paths), neighbors, HA and session sync, policy routes, L3Outs and exits to the outside –
            read-only, state of the last run; IPs with names from the IPAM. The path of a connection is shown by the{" "}
            <a href="#wege">path search</a>.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("Routing laden", "Load routing")} />
      {summary === null ? (
        <Loading />
      ) : summary.contexts.length === 0 && summary.l3outs === 0 && summary.ha === 0 ? (
        <Note>
          {L(
            <>
              Noch keine Routing-Daten. Ab 0.16.0 liest jeder Lauf einer FortiGate bzw. eines APIC das Routing mit –
              Quellsysteme neu einlesen.
            </>,
            <>
              No routing data yet. Since 0.16.0 every run of a FortiGate or an APIC also reads the routing – collect the
              source systems again.
            </>,
          )}
        </Note>
      ) : (
        <ContextCard summary={summary} onHistory={() => openHistory()} />
      )}
      <div className="tabbar sticky">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "routes" && (
        <RoutesTab
          tenantId={tenantId}
          nodes={nodes}
          vrfs={summary?.vrfs ?? []}
          mgmt={!!summary?.mgmt_vrf}
          initial={params.tab === "routes" ? params : {}}
          onHistory={openHistory}
        />
      )}
      {tab === "changes" && (
        <ChangesTab key={JSON.stringify(history)} tenantId={tenantId} nodes={nodes} initial={history} />
      )}
      {tab === "peers" && <PeersTab tenantId={tenantId} />}
      {tab === "ha" && <HaTab tenantId={tenantId} />}
      {tab === "parallel" && <ParallelTab tenantId={tenantId} />}
      {tab === "policy" && <PolicyTab tenantId={tenantId} initialQ={params.tab === "policy" ? params.q : ""} />}
      {tab === "l3out" && <L3OutTab tenantId={tenantId} canEdit={canEdit} />}
      {tab === "topology" && <TopologyTab tenantId={tenantId} />}
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
