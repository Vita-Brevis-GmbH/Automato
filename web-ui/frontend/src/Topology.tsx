// Topologie (0.50.0, ADR 0019): das Modell der Umgebung als Liste - Knoten (Firewall/VDOM,
// ACI-VRF, Segment, Anbindung, Router) mit ihren Kanten (Transfernetz, Inter-VDOM-Link, BGP,
// Anbindung, Verbund …), Herkunft (abgeleitet, von Hand, bestätigt) und den Befunden der
// Plausibilität. Zeichnung und Export folgen (0.52.0); rein lesend.
import { Fragment, useEffect, useState } from "react";
import { fetchTopology, TopoEdge, TopologyModel, TopoNode } from "./api";
import { findingLabel } from "./refs";
import { Dash, Empty, ErrorBox, Icon, Loading, Note } from "./ui";
import { L } from "./i18n";

export const NODE_KIND: Record<string, string> = {
  firewall: "Firewall",
  vdom: "VDOM",
  vrf: L("ACI-VRF", "ACI VRF"),
  segment: "Segment",
  exit: L("Anbindung", "Exit"),
  router: "Router",
};
const EDGE_KIND: Record<string, string> = {
  transfer: L("Transfernetz", "Transfer network"),
  member: L("am Segment", "on segment"),
  vdom_link: L("Inter-VDOM-Link", "Inter-VDOM link"),
  route: L("Route ohne gemeinsames Netz", "Route without shared network"),
  bgp: "BGP",
  uplink: L("nach aussen", "to the outside"),
  exit_route: L("manuelle Route", "manual route"),
  parallel: L("Verbund", "Parallel group"),
  sync: L("Session-Sync", "Session sync"),
};
const ORIGIN: Record<string, [string, string]> = {
  derived: ["na", L("abgeleitet", "derived")],
  manual: ["info", L("von Hand", "manual")],
  confirmed: ["ok", L("bestätigt", "confirmed")],
};
const STATUS: Record<string, [string, string]> = {
  active: ["ok", L("aktiv", "active")],
  backup: ["info", L("Backup", "backup")],
  neighbor: ["na", L("nur Nachbar", "neighbor only")],
};

// Routen über die Kante aus Sicht des Knotens: hinaus (→) bzw. herein (←), dazu Backup-Wege
function Routes({ e, me }: { e: TopoEdge; me: string }) {
  const out = me === e.a ? "a>b" : "b>a";
  const back = me === e.a ? "b>a" : "a>b";
  const a = e.routes.active[out] ?? 0;
  const b = e.routes.active[back] ?? 0;
  const bk = (e.routes.backup[out] ?? 0) + (e.routes.backup[back] ?? 0);
  if (!a && !b && !bk) return <Dash />;
  const samples = e.routes.samples[out] ?? [];
  return (
    <span title={samples.length ? L(`z. B. ${samples.join(", ")}`, `e.g. ${samples.join(", ")}`) : undefined}>
      {a ? `→ ${a}` : ""}
      {a && b ? " · " : ""}
      {b ? `← ${b}` : ""}
      {bk ? <span className="faint">{L(` (+${bk} Backup)`, ` (+${bk} backup)`)}</span> : null}
    </span>
  );
}

function EdgeDetail({ e, me }: { e: TopoEdge; me: string }) {
  const parts: React.ReactNode[] = [];
  const mine = e.via.filter((v) => v.node === me);
  const other = e.via.filter((v) => v.node !== me);
  if (mine.length || other.length)
    parts.push(
      <span key="via" className="mono">
        {mine.map((v) => `${v.interface ?? ""} ${v.address}`.trim()).join(", ") || "–"}
        {other.length ? ` ↔ ${other.map((v) => v.address).join(", ")}` : ""}
      </span>,
    );
  for (const s of e.sessions ?? [])
    parts.push(
      <span key={`bgp-${s.from}-${s.peer}`}>
        {s.peer} AS {s.remote_as ?? "?"}{" "}
        <span className={`chip ${(s.state ?? "").toLowerCase() === "established" ? "ok" : "warn"}`}>{s.state ?? "?"}</span>
      </span>,
    );
  for (const r of e.routers ?? [])
    parts.push(
      <span key={`r-${r.ip}`}>
        <span className="mono">{r.ip}</span> <span className={`chip ${STATUS[r.status]?.[0] ?? ""}`}>{STATUS[r.status]?.[1] ?? r.status}</span>
        {r.default ? L(" Default-Route", " default route") : ""}
        {r.remote_as ? ` · AS ${r.remote_as}` : ""}
        {r.interface ? ` · ${r.interface}` : ""}
        {r.zone && r.zone !== r.interface ? ` (${r.zone})` : ""}
      </span>,
    );
  for (const l of e.links ?? [])
    parts.push(
      <span key={`l-${l.a}`} className="mono">
        {l.a} ↔ {l.b}
      </span>,
    );
  if (e.ports?.length) parts.push(<span key="ports">{L("Interface/Zone", "Interface/zone")} {e.ports.join(", ")}</span>);
  if (e.prefixes?.length) parts.push(<span key="pfx" className="mono">{e.prefixes.join(", ")}</span>);
  if (e.next_hops?.length) parts.push(<span key="nh" className="mono">{L("über", "via")} {e.next_hops.join(", ")}</span>);
  if (e.group) parts.push(<span key="grp">{L("Verbund", "Parallel group")} {e.group}</span>);
  if (e.peer) parts.push(<span key="peer" className="mono">Peer {e.peer}</span>);
  if (e.to)
    parts.push(
      <span key="to">
        {L("weiter zu", "continues to")}{" "}
        {Object.entries(e.to)
          .map(([k, n]) => `${k} (${n})`)
          .join(", ")}
      </span>,
    );
  return parts.length ? (
    <div className="topo-detail">
      {parts.map((p, i) => (
        <div key={i}>{p}</div>
      ))}
    </div>
  ) : (
    <Dash />
  );
}

function NodeEdges({ node, model }: { node: TopoNode; model: TopologyModel }) {
  const byId = new Map(model.nodes.map((n) => [n.id, n]));
  const edges = model.edges.filter((e) => e.a === node.id || e.b === node.id);
  if (!edges.length) return <div className="faint">{L("Keine Verbindungen gelesen.", "No connections read.")}</div>;
  return (
    <table className="topo-edges">
      <thead>
        <tr>
          <th>{L("Verbindung", "Connection")}</th>
          <th>{L("Gegenstelle", "Peer")}</th>
          <th>{L("Netz", "Network")}</th>
          <th>{L("Routen", "Routes")}</th>
          <th>{L("Herkunft", "Origin")}</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {edges.map((e) => {
          const other = byId.get(e.a === node.id ? e.b : e.a);
          return (
            <tr key={e.id}>
              <td className="nowrap">{EDGE_KIND[e.kind] ?? e.kind}</td>
              <td>
                {other ? (
                  <>
                    {other.label} <span className="faint">{NODE_KIND[other.kind] ?? other.kind}</span>
                  </>
                ) : (
                  <Dash />
                )}
              </td>
              <td className="mono">{e.network ?? <Dash />}</td>
              <td className="nowrap">
                <Routes e={e} me={node.id} />
              </td>
              <td>
                <span className={`chip ${ORIGIN[e.origin]?.[0] ?? ""}`}>{ORIGIN[e.origin]?.[1] ?? e.origin}</span>
              </td>
              <td>
                <EdgeDetail e={e} me={node.id} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Members({ n }: { n: TopoNode }) {
  const bits: string[] = [];
  if (n.kind === "vrf" && n.members?.length) bits.push(L(`Border-Leafs ${n.members.join(", ")}`, `Border leafs ${n.members.join(", ")}`));
  if (n.kind === "segment" && n.members?.length) bits.push(n.members.join(", "));
  if (n.ha?.mode && n.ha.mode !== "standalone")
    bits.push(`HA ${n.ha.mode}${n.ha.members.length ? `: ${n.ha.members.join(", ")}` : ""}`);
  if (n.group) bits.push(L(`Verbund ${n.group}`, `Parallel group ${n.group}`));
  if (n.kind === "router") bits.push([n.ip, n.remote_as ? `AS ${n.remote_as}` : ""].filter(Boolean).join(" · "));
  if (n.kind === "exit" && n.prefixes?.length) bits.push(L(`Netze ${n.prefixes.join(", ")}`, `Networks ${n.prefixes.join(", ")}`));
  return bits.length ? <span className="faint">{bits.join(" · ")}</span> : <Dash />;
}

export default function TopologyTab({ tenantId }: { tenantId: number }) {
  const [model, setModel] = useState<TopologyModel | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    setModel(null);
    fetchTopology(tenantId)
      .then((m) => current && setModel(m))
      .catch((e) => current && setError(e));
    return () => {
      current = false;
    };
  }, [tenantId]);
  if (error) return <ErrorBox error={error} prefix={L("Topologie laden", "Load topology")} />;
  if (!model) return <Loading />;
  if (!model.nodes.length)
    return (
      <Note>
        {L(
          "Noch keine Topologie – Routing und Inventar der Firewalls bzw. des APIC einlesen.",
          "No topology yet – collect routing and inventory of the firewalls or the APIC.",
        )}
      </Note>
    );
  const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
  const nodes = model.nodes.filter((n) => {
    if (kind && (kind === "firewall" ? !["firewall", "vdom"].includes(n.kind) : n.kind !== kind)) return false;
    const hay = JSON.stringify([n.label, n.members, n.ip, n.network, n.ha?.members]).toLowerCase();
    return tokens.every((t) => hay.includes(t));
  });
  const s = model.summary;
  const count = (k: string) => s.nodes[k] ?? 0;
  return (
    <>
      <div className="card" id="topology-summary" style={{ marginBottom: 12 }}>
        <div className="card-body pad">
          <span className="chip">
            {count("firewall") + count("vdom")} {L("Firewalls/VDOMs", "firewalls/VDOMs")}
          </span>{" "}
          <span className="chip">
            {count("vrf")} {L("ACI-VRFs", "ACI VRFs")}
          </span>{" "}
          <span className="chip">
            {count("segment")} {L("Segmente", "segments")}
          </span>{" "}
          <span className="chip">
            {count("exit")} {L("Anbindungen", "exits")}
          </span>{" "}
          <span className="chip">
            {count("router")} {L("Router", "routers")}
          </span>{" "}
          <span className="faint">
            · {model.edges.length} {L("Verbindungen", "connections")}
          </span>
          {model.issues.length ? (
            <span>
              {" · "}
              <a href="#findings">
                {L(
                  `${model.issues.length} Befund${model.issues.length === 1 ? "" : "e"} zur Plausibilität`,
                  `${model.issues.length} plausibility finding${model.issues.length === 1 ? "" : "s"}`,
                )}
              </a>
              :{" "}
              {Object.entries(s.issues)
                .map(([t, n]) => `${findingLabel(t)} (${n})`)
                .join(", ")}
            </span>
          ) : (
            <span className="faint">{L(" · keine Befunde zur Plausibilität", " · no plausibility findings")}</span>
          )}
        </div>
      </div>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input placeholder={L("Knoten, Leaf, IP, Netz…", "Node, leaf, IP, network…")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="sel" aria-label={L("Art", "Type")} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{L("Art: alle", "Type: all")}</option>
          <option value="firewall">Firewalls/VDOMs</option>
          <option value="vrf">{L("ACI-VRFs", "ACI VRFs")}</option>
          <option value="segment">{L("Segmente", "Segments")}</option>
          <option value="exit">{L("Anbindungen", "Exits")}</option>
          <option value="router">{L("Router", "Routers")}</option>
        </select>
        <span className="count">
          {nodes.length === model.nodes.length
            ? nodes.length
            : L(`${nodes.length} von ${model.nodes.length}`, `${nodes.length} of ${model.nodes.length}`)}
        </span>
      </div>
      <div className="card">
        <div className="card-body scroll viewport">
          {nodes.length === 0 ? (
            <Empty title={L("Kein Knoten passt zum Filter.", "No node matches the filter.")} />
          ) : (
            <table id="topology-nodes">
              <thead>
                <tr>
                  <th>{L("Knoten", "Node")}</th>
                  <th>{L("Art", "Type")}</th>
                  <th>{L("Mitglieder · HA · Verbund", "Members · HA · parallel group")}</th>
                  <th className="num">{L("Verbindungen", "Connections")}</th>
                  <th className="num">{L("Befunde", "Findings")}</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <Fragment key={n.id}>
                    <tr className="clickable" onClick={() => setOpen(open === n.id ? null : n.id)}>
                      <td>
                        <b>{n.label}</b>
                        {n.missing?.length ? <div className="faint">{L("nicht lesbar:", "not readable:")} {n.missing.join(", ")}</div> : null}
                      </td>
                      <td className="nowrap">
                        {NODE_KIND[n.kind] ?? n.kind}
                        {n.origin !== "derived" && (
                          <>
                            {" "}
                            <span className={`chip ${ORIGIN[n.origin][0]}`}>{ORIGIN[n.origin][1]}</span>
                          </>
                        )}
                      </td>
                      <td>
                        <Members n={n} />
                      </td>
                      <td className="num">{n.degree}</td>
                      <td className="num">{n.issues ? <span className="chip warn">{n.issues}</span> : <Dash />}</td>
                    </tr>
                    {open === n.id && (
                      <tr className="detail-row">
                        <td colSpan={5}>
                          <NodeEdges node={n} model={model} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
