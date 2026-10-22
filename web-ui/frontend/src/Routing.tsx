// Routing & Wege (ADR 0019): gelesene Routing-Tabellen je Firewall bzw. FortiGate-VDOM und je
// ACI-VRF auf den Border-Leafs, BGP-Pfade (auch Backup), Nachbarn, HA/Session-Sync,
// Policy-Routen/SD-WAN und L3Outs. „Weg für IP“ verfolgt den Weg Hop für Hop ab der Quelle
// bzw. einem gewählten Startpunkt (Stufe 3, erste Fassung); IPs mit Namen aus dem IPAM.
import { useEffect, useState } from "react";
import {
  ContextKind,
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
  TopoRoute,
  TracePath,
  TracePort,
  TraceResult,
  traceRoute,
} from "./api";
import { Pager, useDebounced } from "./Objects";
import { Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";

const PAGE = 200;

type Tab = "lookup" | "routes" | "peers" | "ha" | "policy" | "l3out";
const TABS: { key: Tab; label: string }[] = [
  { key: "lookup", label: "Weg für IP" },
  { key: "routes", label: "Routen" },
  { key: "peers", label: "BGP-Nachbarn" },
  { key: "ha", label: "HA & Session-Sync" },
  { key: "policy", label: "Policy-Routen & SD-WAN" },
  { key: "l3out", label: "L3Outs (ACI)" },
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

function policyText(p: PolicyHit) {
  return p.kind === "sdwan" ? `SD-WAN ${p.name ?? `#${p.seq}`}` : `Policy-Route #${p.seq ?? "?"}`;
}

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
    </div>
  );
}

const END: Record<TracePath["end"]["kind"], [string, string]> = {
  connected: ["ok", "Ziel erreicht"],
  fabric: ["ok", "im Fabric"],
  external: ["info", "extern"],
  unknown: ["warn", "Weg endet"],
  no_route: ["warn", "keine Route"],
  loop: ["crit", "Schleife"],
  max_hops: ["warn", "abgebrochen"],
};

// Ein Weg Hop für Hop: Knoten, Eingang, Entscheidung (Policy-Route bzw. Route), Ausgang
function TraceCard({ path, index, total }: { path: TracePath; index: number; total: number }) {
  const [cls, label] = END[path.end.kind] ?? ["", path.end.kind];
  return (
    <div className="card">
      <div className="card-head">
        <h2>
          Weg {index + 1}
          {total > 1 ? ` von ${total}` : ""}
        </h2>
        <span className={`chip ${cls}`}>{label}</span>
        <span className="faint">{path.end.text}</span>
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
                    <div className="faint">
                      {h.via}
                      {h.kind === "vrf" && h.members.length ? ` · ${h.members.join(", ")}` : ""}
                    </div>
                    <Missing items={h.missing} />
                  </td>
                  <td>{h.ingress?.interface ? <Port p={h.ingress} /> : <Dash />}</td>
                  <td>
                    {h.policy ? (
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
                <tr key={`${r.source_system_id}|${r.node}|${r.context}`}>
                  <td>
                    {where(r)}
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
function LookupTab({ tenantId, nodes }: { tenantId: number; nodes: RoutingNode[] }) {
  const [src, setSrc] = useState("");
  const [start, setStart] = useState("");
  const [dst, setDst] = useState("");
  const [trace, setTrace] = useState<TraceResult | null>(null);
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
        ? traceRoute(tenantId, { dst, src, start }).then(setTrace)
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
          {trace.paths.map((p, i) => (
            <TraceCard key={i} path={p} index={i} total={trace.paths.length} />
          ))}
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

export default function Routing({ tenantId, tenantLabel }: { tenantId: number | null; tenantLabel: string }) {
  const [tab, setTab] = useState<Tab>("lookup");
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
      {tab === "lookup" && <LookupTab tenantId={tenantId} nodes={nodes} />}
      {tab === "routes" && <RoutesTab tenantId={tenantId} nodes={nodes} />}
      {tab === "peers" && <PeersTab tenantId={tenantId} />}
      {tab === "ha" && <HaTab tenantId={tenantId} />}
      {tab === "policy" && <PolicyTab tenantId={tenantId} />}
      {tab === "l3out" && <L3OutTab tenantId={tenantId} />}
    </>
  );
}
