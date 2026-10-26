// Topologie (0.50.0, ADR 0019): das Modell der Umgebung als Liste - Knoten (Firewall/VDOM,
// ACI-VRF, Segment, Anbindung, Router) mit ihren Kanten (Transfernetz, Inter-VDOM-Link, BGP,
// Anbindung, Verbund …), Herkunft (abgeleitet, von Hand, bestätigt) und den Befunden der
// Plausibilität. Zeichnung und Export folgen (0.52.0); rein lesend.
import { Fragment, useEffect, useState } from "react";
import { fetchTopology, TopoEdge, TopologyModel, TopoNode } from "./api";
import { findingLabel } from "./refs";
import { Dash, Empty, ErrorBox, Icon, Loading, Note } from "./ui";

export const NODE_KIND: Record<string, string> = {
  firewall: "Firewall",
  vdom: "VDOM",
  vrf: "ACI-VRF",
  segment: "Segment",
  exit: "Anbindung",
  router: "Router",
};
const EDGE_KIND: Record<string, string> = {
  transfer: "Transfernetz",
  member: "am Segment",
  vdom_link: "Inter-VDOM-Link",
  route: "Route ohne gemeinsames Netz",
  bgp: "BGP",
  uplink: "nach aussen",
  exit_route: "manuelle Route",
  parallel: "Verbund",
  sync: "Session-Sync",
};
const ORIGIN: Record<string, [string, string]> = {
  derived: ["na", "abgeleitet"],
  manual: ["info", "von Hand"],
  confirmed: ["ok", "bestätigt"],
};
const STATUS: Record<string, [string, string]> = {
  active: ["ok", "aktiv"],
  backup: ["info", "Backup"],
  neighbor: ["na", "nur Nachbar"],
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
    <span title={samples.length ? `z. B. ${samples.join(", ")}` : undefined}>
      {a ? `→ ${a}` : ""}
      {a && b ? " · " : ""}
      {b ? `← ${b}` : ""}
      {bk ? <span className="faint"> (+{bk} Backup)</span> : null}
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
        {r.default ? " Default-Route" : ""}
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
  if (e.ports?.length) parts.push(<span key="ports">Interface/Zone {e.ports.join(", ")}</span>);
  if (e.prefixes?.length) parts.push(<span key="pfx" className="mono">{e.prefixes.join(", ")}</span>);
  if (e.next_hops?.length) parts.push(<span key="nh" className="mono">über {e.next_hops.join(", ")}</span>);
  if (e.group) parts.push(<span key="grp">Verbund {e.group}</span>);
  if (e.peer) parts.push(<span key="peer" className="mono">Peer {e.peer}</span>);
  if (e.to)
    parts.push(
      <span key="to">
        weiter zu{" "}
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
  if (!edges.length) return <div className="faint">Keine Verbindungen gelesen.</div>;
  return (
    <table className="topo-edges">
      <thead>
        <tr>
          <th>Verbindung</th>
          <th>Gegenstelle</th>
          <th>Netz</th>
          <th>Routen</th>
          <th>Herkunft</th>
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
  if (n.kind === "vrf" && n.members?.length) bits.push(`Border-Leafs ${n.members.join(", ")}`);
  if (n.kind === "segment" && n.members?.length) bits.push(n.members.join(", "));
  if (n.ha?.mode && n.ha.mode !== "standalone")
    bits.push(`HA ${n.ha.mode}${n.ha.members.length ? `: ${n.ha.members.join(", ")}` : ""}`);
  if (n.group) bits.push(`Verbund ${n.group}`);
  if (n.kind === "router") bits.push([n.ip, n.remote_as ? `AS ${n.remote_as}` : ""].filter(Boolean).join(" · "));
  if (n.kind === "exit" && n.prefixes?.length) bits.push(`Netze ${n.prefixes.join(", ")}`);
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
  if (error) return <ErrorBox error={error} prefix="Topologie laden" />;
  if (!model) return <Loading />;
  if (!model.nodes.length)
    return <Note>Noch keine Topologie – Routing und Inventar der Firewalls bzw. des APIC einlesen.</Note>;
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
          <span className="chip">{count("firewall") + count("vdom")} Firewalls/VDOMs</span>{" "}
          <span className="chip">{count("vrf")} ACI-VRFs</span> <span className="chip">{count("segment")} Segmente</span>{" "}
          <span className="chip">{count("exit")} Anbindungen</span> <span className="chip">{count("router")} Router</span>{" "}
          <span className="faint">· {model.edges.length} Verbindungen</span>
          {model.issues.length ? (
            <span>
              {" · "}
              <a href="#findings">
                {model.issues.length} Befund{model.issues.length === 1 ? "" : "e"} zur Plausibilität
              </a>
              :{" "}
              {Object.entries(s.issues)
                .map(([t, n]) => `${findingLabel(t)} (${n})`)
                .join(", ")}
            </span>
          ) : (
            <span className="faint"> · keine Befunde zur Plausibilität</span>
          )}
        </div>
      </div>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input placeholder="Knoten, Leaf, IP, Netz…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="sel" aria-label="Art" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Art: alle</option>
          <option value="firewall">Firewalls/VDOMs</option>
          <option value="vrf">ACI-VRFs</option>
          <option value="segment">Segmente</option>
          <option value="exit">Anbindungen</option>
          <option value="router">Router</option>
        </select>
        <span className="count">{nodes.length === model.nodes.length ? nodes.length : `${nodes.length} von ${model.nodes.length}`}</span>
      </div>
      <div className="card">
        <div className="card-body scroll viewport">
          {nodes.length === 0 ? (
            <Empty title="Kein Knoten passt zum Filter." />
          ) : (
            <table id="topology-nodes">
              <thead>
                <tr>
                  <th>Knoten</th>
                  <th>Art</th>
                  <th>Mitglieder · HA · Verbund</th>
                  <th className="num">Verbindungen</th>
                  <th className="num">Befunde</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <Fragment key={n.id}>
                    <tr className="clickable" onClick={() => setOpen(open === n.id ? null : n.id)}>
                      <td>
                        <b>{n.label}</b>
                        {n.missing?.length ? <div className="faint">nicht lesbar: {n.missing.join(", ")}</div> : null}
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
