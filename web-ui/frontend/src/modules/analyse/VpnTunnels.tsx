// VPN-Tunnel (IPsec, 1.19.0, ADR 0063): Tunnel der FortiGates im Interface-Modus mit Zustand, Gegenstelle
// (gelesene FortiGate bzw. nicht gelesen), Phase 2 mit Selektoren, Routen über den Tunnel (deckt ein Selektor
// sie?) und den Netzen dahinter; Dial-up-Tunnel mit den verbundenen Gegenstellen. Rein lesend.
import { useEffect, useMemo, useState } from "react";
import { fetchVpnTunnel, fetchVpnTunnels, VpnList, VpnTunnel, VpnTunnelDetail } from "../../api";
import { L } from "../../i18n";
import { useDebounced } from "../../Objects";
import { Dash, Drawer, Empty, ErrorBox, Loading, Note, PageHead } from "../../ui";
import type { View, ViewParams } from "../types";

const STATUS: Record<string, [string, string]> = {
  up: ["ok", "up"],
  partial: ["warn", L("teilweise up", "partially up")],
  down: ["crit", "down"],
  idle: ["na", L("keine Gegenstelle verbunden", "no peer connected")],
  unknown: ["na", L("Zustand unbekannt", "state unknown")],
};
const KIND: Record<string, string> = {
  static: L("statisch", "static"),
  dialup: L("Dial-up", "Dial-up"),
  ddns: "DDNS",
};
const COVERED: Record<string, [string, string]> = {
  full: ["ok", L("gedeckt", "covered")],
  partial: ["warn", L("nur teilweise", "only partly")],
  none: ["crit", L("kein Selektor", "no selector")],
};
const FROM: Record<string, string> = {
  phase2: "Phase 2",
  dialup: L("Dial-up-Gegenstelle", "dial-up peer"),
  route: L("Route", "route"),
};

const hashOf = (view: string, params: Record<string, string> = {}) => {
  const q = new URLSearchParams(params).toString();
  return `#${view}${q ? `?${q}` : ""}`;
};

export function VpnStatus({ status }: { status: string | null | undefined }) {
  const [cls, text] = STATUS[status ?? "unknown"] ?? STATUS.unknown;
  return <span className={`chip ${cls}`}>{text}</span>;
}

function where(t: { source: string; context: string }): string {
  return t.context && t.context !== "root" ? `${t.source} · VDOM ${t.context}` : t.source;
}

function Peer({ t }: { t: VpnTunnel }) {
  if (t.kind === "dialup" && !t.instances) return <span className="faint">{L("wechselnd, keine verbunden", "changing, none connected")}</span>;
  return (
    <>
      <span className="mono">{t.remote_gw ?? (t.kind === "dialup" ? L(`${t.instances} verbunden`, `${t.instances} connected`) : "?")}</span>
      {t.peers.length ? (
        t.peers.map((p, i) => (
          <div key={i} className="faint vpn-peer">
            → {p.label}
            {p.tunnel ? ` (${p.tunnel})` : ""}
            {p.how === "reverse" ? L(" – über ihren Tunnel erkannt", " – recognized by its tunnel") : ""}
          </div>
        ))
      ) : (
        <div className="faint vpn-peer">{L("nicht gelesen – der Weg endet hier", "not read – the path ends here")}</div>
      )}
    </>
  );
}

export default function VpnTunnels({
  tenantId,
  tenantLabel,
  params,
  go,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params: ViewParams;
  go: (v: View, p?: ViewParams) => void;
}) {
  const [q, setQ] = useState(params.q ?? "");
  const query = useDebounced(q);
  const [status, setStatus] = useState(params.status ?? "");
  const [data, setData] = useState<VpnList | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    if (tenantId == null) return;
    let live = true;
    setData(null);
    fetchVpnTunnels(tenantId, { q: query, status })
      .then((d) => {
        if (!live) return;
        setData(d);
        // Link aus einem Befund bzw. dem Weg: Tunnel über seinen festen Schlüssel öffnen
        const hit = params.tunnel ? d.tunnels.find((t) => t.ref === params.tunnel) : undefined;
        if (hit) setOpen(hit.id);
      })
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, query, status, params.tunnel]);
  if (tenantId == null) return <Empty title={L("Mandant wählen", "Choose a tenant")} />;
  const c = data?.counts ?? {};
  return (
    <>
      <PageHead title={L("VPN-Tunnel", "VPN tunnels")}>
        {L(
          `IPsec-Tunnel der FortiGates von ${tenantLabel} (Interface-Modus, nur gelesen): Zustand, Gegenstelle, Phase 2 mit Selektoren und die Routen über den Tunnel. Ist die Gegenstelle eine gelesene FortiGate, geht der Weg dort weiter – sonst endet er sichtbar an der Gegenstelle. SSL-VPN wird nicht gelesen.`,
          `IPsec tunnels of the FortiGates of ${tenantLabel} (interface mode, read only): state, peer, phase 2 with selectors and the routes via the tunnel. If the peer is a FortiGate that is read, the path continues there – otherwise it visibly ends at the peer. SSL VPN is not read.`,
        )}
      </PageHead>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {(data?.missing ?? []).map((m) => (
        <Note key={`${m.source_system_id}|${m.context}`} warn>
          {L(
            `IPsec nicht lesbar auf ${m.source ?? "?"} (VDOM ${m.context}): ${m.missing.join(", ")} – das Profil des REST-API-Benutzers braucht Lesezugriff auf VPN.`,
            `IPsec not readable on ${m.source ?? "?"} (VDOM ${m.context}): ${m.missing.join(", ")} – the profile of the REST API user needs read access to VPN.`,
          )}
        </Note>
      ))}
      <div className="card" id="vpn-tunnels">
        <div className="card-head">
          <h2>{L("Tunnel", "Tunnels")}</h2>
          {data && (
            <span className="chips" id="vpn-counts">
              <span className="chip">{L(`${data.total} Tunnel`, `${data.total} tunnels`)}</span>
              {(c.up ?? 0) > 0 && <span className="chip ok">{c.up} up</span>}
              {(c.partial ?? 0) > 0 && <span className="chip warn">{L(`${c.partial} teilweise`, `${c.partial} partial`)}</span>}
              {(c.down ?? 0) > 0 && <span className="chip crit">{c.down} down</span>}
            </span>
          )}
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select id="vpn-status" className="sel" aria-label={L("Zustand", "State")} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">{L("alle Zustände", "all states")}</option>
              {Object.entries(STATUS).map(([k, [, text]]) => (
                <option key={k} value={k}>
                  {text}
                </option>
              ))}
            </select>
            <input
              id="vpn-q"
              className="input"
              type="search"
              aria-label={L("Suchen", "Search")}
              placeholder={L("Firewall, Tunnel, Gegenstelle, Netz", "Firewall, tunnel, peer, network")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </span>
        </div>
        <div className="card-body scroll">
          {data === null ? (
            <Loading />
          ) : data.tunnels.length === 0 ? (
            <Empty title={L("keine VPN-Tunnel", "no VPN tunnels")}>
              {L("Die FortiGates haben keine IPsec-Tunnel bzw. der Filter passt auf keinen.", "The FortiGates have no IPsec tunnels or the filter matches none.")}
            </Empty>
          ) : (
            <table className="compact" id="vpn-table">
              <thead>
                <tr>
                  <th>Firewall</th>
                  <th>Tunnel</th>
                  <th>{L("Art", "Kind")}</th>
                  <th>{L("Gegenstelle", "Peer")}</th>
                  <th>Interface</th>
                  <th>IKE</th>
                  <th>{L("Zustand", "State")}</th>
                  <th className="num">Phase 2</th>
                  <th className="num" title={L("Routen über den Tunnel (RIB und statisch)", "Routes via the tunnel (RIB and static)")}>
                    {L("Routen", "Routes")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.tunnels.map((t) => (
                  <tr
                    key={t.id}
                    data-tunnel={t.name}
                    className={`clickable${t.status === "down" || t.uncovered ? " row-crit" : ""}`}
                    onClick={() => setOpen(t.id)}
                  >
                    <td className="nowrap">{where(t)}</td>
                    <td className="mono nowrap">
                      <button className="linkbtn" onClick={() => setOpen(t.id)}>
                        {t.name}
                      </button>
                    </td>
                    <td>{KIND[t.kind] ?? t.kind}</td>
                    <td>
                      <Peer t={t} />
                    </td>
                    <td className="mono">{t.interface ?? <Dash />}</td>
                    <td>{t.ike_version ? `v${t.ike_version}` : <Dash />}</td>
                    <td className="nowrap">
                      <VpnStatus status={t.status} />
                    </td>
                    <td className="num">{t.phase2_total ? `${t.phase2_up}/${t.phase2_total}` : <Dash />}</td>
                    <td className="num">
                      {t.routes}
                      {t.uncovered > 0 && (
                        <span className="chip crit" style={{ marginLeft: 4 }} title={L("Routen ohne passenden Phase-2-Selektor", "Routes without a matching phase 2 selector")}>
                          {L(`${t.uncovered} ohne Selektor`, `${t.uncovered} without selector`)}
                        </span>
                      )}
                      {t.partial > 0 && (
                        <span className="chip warn" style={{ marginLeft: 4 }} title={L("Selektor deckt die Route nur teilweise", "Selector covers the route only partly")}>
                          {L(`${t.partial} teilweise`, `${t.partial} partly`)}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {open != null && <TunnelDetail tenantId={tenantId} id={open} onClose={() => setOpen(null)} go={go} />}
    </>
  );
}

function Selectors({ values, names }: { values: string[]; names: string[] }) {
  if (!values.length && !names.length) return <Dash />;
  return (
    <>
      {names.length > 0 && <div>{names.join(", ")}</div>}
      <div className={`mono${names.length ? " faint" : ""}`}>{values.join(", ") || "–"}</div>
    </>
  );
}

function TunnelDetail({ tenantId, id, onClose, go }: { tenantId: number; id: number; onClose: () => void; go: (v: View, p?: ViewParams) => void }) {
  const [t, setT] = useState<VpnTunnelDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    fetchVpnTunnel(tenantId, id)
      .then((d) => live && setT(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, id]);
  const attrs = useMemo(() => Object.entries(t?.attrs ?? {}).filter(([k]) => k !== "proposal"), [t]);
  return (
    <Drawer
      title={t ? `VPN ${t.name}` : L("VPN-Tunnel", "VPN tunnel")}
      sub={t ? `${where(t)} · ${KIND[t.kind] ?? t.kind}` : undefined}
      onClose={onClose}
      wide
    >
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {t === null ? (
        <Loading />
      ) : (
        <div id="vpn-detail">
          <dl className="kv tight">
            <dt>{L("Zustand", "State")}</dt>
            <dd>
              <VpnStatus status={t.status} />
            </dd>
            <dt>{L("Gegenstelle", "Peer")}</dt>
            <dd>
              <Peer t={t} />
            </dd>
            <dt>Interface</dt>
            <dd className="mono">
              {t.interface ?? "–"}
              {t.local_gw ? ` · ${t.local_gw}` : ""}
            </dd>
            <dt>IKE</dt>
            <dd>{t.ike_version ? `v${t.ike_version}` : "–"}</dd>
            <dt>Proposal</dt>
            <dd className="mono">{t.proposal ?? "–"}</dd>
            {attrs.length > 0 && (
              <>
                <dt>{L("Weitere", "More")}</dt>
                <dd className="mono faint">{attrs.map(([k, v]) => `${k} ${String(v)}`).join(" · ")}</dd>
              </>
            )}
          </dl>
          {t.unresolved.length > 0 && (
            <Note warn>
              {L(
                `Selektoren nicht auflösbar: ${t.unresolved.join(", ")} – ob eine Route gedeckt ist, bleibt offen.`,
                `Selectors not resolvable: ${t.unresolved.join(", ")} – whether a route is covered stays open.`,
              )}
            </Note>
          )}
          <h3>Phase 2</h3>
          {t.phase2.length === 0 ? (
            <Empty title={L("keine Phase 2", "no phase 2")} />
          ) : (
            <table className="compact" id="vpn-phase2">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>{L("lokal", "local")}</th>
                  <th>{L("Gegenstelle", "Peer")}</th>
                  <th>{L("Zustand", "State")}</th>
                </tr>
              </thead>
              <tbody>
                {t.phase2.map((p) => (
                  <tr key={p.name}>
                    <td className="mono">{p.name}</td>
                    <td>
                      <Selectors values={p.src} names={p.src_names} />
                    </td>
                    <td>
                      <Selectors values={p.dst} names={p.dst_names} />
                    </td>
                    <td>
                      <VpnStatus status={p.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {t.kind === "dialup" && (
            <>
              <h3>{L("Verbundene Gegenstellen", "Connected peers")}</h3>
              {t.dialup.length === 0 ? (
                <Empty title={L("keine verbunden", "none connected")} />
              ) : (
                <table className="compact" id="vpn-dialup">
                  <thead>
                    <tr>
                      <th>{L("Instanz", "Instance")}</th>
                      <th>{L("Gegenstelle", "Peer")}</th>
                      <th>{L("Netze", "Networks")}</th>
                      <th>{L("Zustand", "State")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {t.dialup.map((i) => (
                      <tr key={i.name}>
                        <td className="mono">{i.name}</td>
                        <td className="mono">{i.remote_gw ?? "?"}</td>
                        <td className="mono">{i.nets.join(", ") || "–"}</td>
                        <td>
                          <VpnStatus status={i.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
          <h3>{L("Routen über den Tunnel", "Routes via the tunnel")}</h3>
          {t.route_list.length === 0 ? (
            <Empty title={L("keine Routen über den Tunnel", "no routes via the tunnel")} />
          ) : (
            <table className="compact" id="vpn-routes">
              <thead>
                <tr>
                  <th>{L("Präfix", "Prefix")}</th>
                  <th>{L("Tabelle", "Table")}</th>
                  <th>{L("Protokoll", "Protocol")}</th>
                  <th>{L("Phase-2-Selektor", "Phase 2 selector")}</th>
                </tr>
              </thead>
              <tbody>
                {t.route_list.map((r) => (
                  <tr key={`${r.prefix}|${r.table}`} data-prefix={r.prefix}>
                    <td className="mono">
                      {r.prefix}
                      {r.instance ? <span className="faint"> ({r.instance})</span> : null}
                    </td>
                    <td>{r.table === "rib" ? "RIB" : L("statisch", "static")}</td>
                    <td>{r.protocol ?? "–"}</td>
                    <td>{r.covered ? <span className={`chip ${COVERED[r.covered][0]}`}>{COVERED[r.covered][1]}</span> : <span className="faint">{L("unbekannt", "unknown")}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {t.routes > t.route_list.length && (
            <div className="faint">{L(`+${t.routes - t.route_list.length} weitere Routen`, `+${t.routes - t.route_list.length} more routes`)}</div>
          )}
          <h3>{L("Netze dahinter", "Networks behind")}</h3>
          {t.behind.length === 0 ? (
            <Empty title={L("keine bekannt", "none known")} />
          ) : (
            <ul id="vpn-behind">
              {t.behind.map((b) => (
                <li key={b.net}>
                  <span className="mono">{b.net}</span>{" "}
                  <span className="faint">
                    ({[...new Set(b.from.map((f) => FROM[f.split(":")[0]] ?? f))].join(", ")})
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p>
            <a
              href={hashOf("routing", { tab: "routes", context: `${t.source_system_id}|${t.context}`, q: t.name })}
              onClick={(e) => {
                e.preventDefault();
                go("routing", { tab: "routes", context: `${t.source_system_id}|${t.context}`, q: t.name });
              }}
            >
              {L("Routen dieses Tunnels im Routing", "Routes of this tunnel in routing")}
            </a>
          </p>
        </div>
      )}
    </Drawer>
  );
}
