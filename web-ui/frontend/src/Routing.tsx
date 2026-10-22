// Routing & Wege (ADR 0019 Stufe 2): gelesene Routing-Tabellen je FortiGate-VDOM und je
// ACI-VRF auf den Border-Leafs, BGP-Pfade (auch Backup), Nachbarn, HA/Session-Sync,
// Policy-Routen/SD-WAN und L3Outs. „Weg für IP“ zeigt je Kontext die wirksame Route.
import { useEffect, useState } from "react";
import {
  fetchHa,
  fetchL3Outs,
  fetchPeers,
  fetchPolicyRoutes,
  fetchRoutes,
  fetchRoutingSummary,
  lookupRoute,
  NextHop,
  RouteLookup,
  RoutingSummary,
  TopoHa,
  TopoL3Out,
  TopoPeer,
  TopoPolicyRoute,
  TopoRoute,
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

// Next-Hops: IP über Interface (Zone); ECMP = mehrere Zeilen
export function Hops({ hops }: { hops: NextHop[] | null | undefined }) {
  if (!hops?.length) return <Dash />;
  return (
    <div className="objs">
      {hops.map((h, i) => (
        <span key={i} className="mono">
          {h.ip ?? "direkt"}
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

function where(x: { source: string | null; node: string; context: string }) {
  return (
    <>
      <b>{x.source ?? x.node}</b>
      <div className="faint">
        {x.node !== x.source ? `${x.node} · ` : ""}
        {x.context}
      </div>
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
                  <span className="chip">{c.kind === "vrf" ? "VRF (Border-Leaf)" : "VDOM"}</span>
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

function LookupTab({ tenantId }: { tenantId: number }) {
  const [ip, setIp] = useState("");
  const [rows, setRows] = useState<RouteLookup[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const run = () => {
    if (!ip.trim()) return;
    setBusy(true);
    setError(null);
    lookupRoute(tenantId, ip)
      .then(setRows)
      .catch((e) => {
        setError(e);
        setRows(null);
      })
      .finally(() => setBusy(false));
  };
  return (
    <>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder="Ziel-IP, z. B. 10.20.0.5"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
        </div>
        <button className="btn btn-primary btn-sm" onClick={run} disabled={busy || !ip.trim()}>
          {busy ? "Suche…" : "Weg suchen"}
        </button>
        <span className="faint">
          Je Kontext die wirksame Route (längstes Präfix) – Policy-Routen und SD-WAN-Regeln gehen ihr vor.
        </span>
      </div>
      <ErrorBox error={error} prefix="Weg suchen" />
      {rows && (
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
                                <span className={`chip ${p.certain ? "info" : ""}`}>
                                  {p.kind === "sdwan" ? `SD-WAN ${p.name ?? `#${p.seq}`}` : `Policy-Route #${p.seq ?? "?"}`}
                                </span>{" "}
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
      )}
    </>
  );
}

function RoutesTab({ tenantId, contexts }: { tenantId: number; contexts: string[] }) {
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
    fetchRoutes({ tenantId, q: query, ip: ipQuery, table, context, limit: PAGE, offset })
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
          {contexts.map((c) => (
            <option key={c} value={c}>
              {c}
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
          <th>Lokal</th>
          <th className="num">AS</th>
          <th>Zustand</th>
        </tr>
      </thead>
      <tbody>
        {(rows ?? []).map((p) => (
          <tr key={p.id}>
            <td>{where(p)}</td>
            <td className="mono">{p.peer_ip}</td>
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
                    node-{n.node}
                    {n.router_id && <span className="faint"> · Router-ID {n.router_id}</span>}
                    {n.static_routes?.map((r) => (
                      <span key={r.prefix} className="faint">
                        {" "}
                        · {r.prefix} → {(r.next_hops ?? []).join(", ") || "?"}
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
                    {(p.nodes ?? []).join("/")} {p.interface}
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
                    {p.asn != null && <span className="faint"> · AS {p.asn}</span>}
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
        setSummary({ contexts: [], ha: 0, l3outs: 0 });
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
  const contexts = [...new Set((summary?.contexts ?? []).map((c) => c.context))].sort();

  return (
    <>
      <PageHead title="Routing & Wege">
        Routing-Tabellen von „{tenantLabel}“ je FortiGate-VDOM und je ACI-VRF auf den Border-Leafs, dazu BGP-Pfade
        (auch Backup-Wege), Nachbarn, HA und Session-Sync, Policy-Routen und L3Outs – rein lesend, Stand des letzten
        Laufs. Grundlage für die Pfade (ADR 0019).
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
      {tab === "lookup" && <LookupTab tenantId={tenantId} />}
      {tab === "routes" && <RoutesTab tenantId={tenantId} contexts={contexts} />}
      {tab === "peers" && <PeersTab tenantId={tenantId} />}
      {tab === "ha" && <HaTab tenantId={tenantId} />}
      {tab === "policy" && <PolicyTab tenantId={tenantId} />}
      {tab === "l3out" && <L3OutTab tenantId={tenantId} />}
    </>
  );
}
