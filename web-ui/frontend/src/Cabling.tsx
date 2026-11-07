// Verkabelung (0.62.0, ADR 0025): Verbindungen zwischen Geräten aus CDP/LLDP aller Quellen (Switches,
// ACI-Leafs), beidseitig bzw. einseitig gemeldet, mit Abweichungen zwischen zwei gelesenen Switches
// (Speed, Duplex, VLANs, Port-Channel); Geräte, Ports der Switches und VLANs. Seit 0.63.0 Endpunkte aus
// MAC-Tabelle und ARP: „Wo hängt …“ und die MACs je Port (ohne Uplinks). Seit 0.64.0 der Plan (L2-Sicht):
// Geräte nach Ebenen, VLAN-Wege mit Inseln, Ausfall eines Geräts bzw. Kabels simulieren. Rein lesend.
import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  CablingDevice,
  CablingEnd,
  CablingEndpoint,
  CablingLink,
  CablingPatch,
  CablingPatchEnd,
  CablingPatches,
  CablingPort,
  CablingSummary,
  CablingVlan,
  EndpointLocation,
  exportL2Plan,
  fetchCablingDevices,
  fetchCablingEndpoints,
  fetchCablingLinks,
  fetchCablingPatches,
  fetchCablingPorts,
  fetchCablingVlans,
  fetchL2Failure,
  fetchL2Plan,
  L2Failure,
  L2Plan,
  L2PlanEdge,
  LinkState,
  locateEndpoint,
} from "./api";
import { Pager, useDebounced } from "./Objects";
import { Dash, download, Empty, ErrorBox, ExportLangSelect, fmtTime, Loading, Note, PageHead, SharedChip } from "./ui";
import { L } from "./i18n";

const PAGE = 200;
type Tab = "plan" | "links" | "devices" | "ports" | "endpoints" | "vlans";
const TABS: { key: Tab; label: string }[] = [
  { key: "links", label: L("Verbindungen", "Links") },
  { key: "plan", label: "Plan" },
  { key: "devices", label: L("Geräte", "Devices") },
  { key: "ports", label: "Ports" },
  { key: "endpoints", label: L("Endpunkte", "Endpoints") },
  { key: "vlans", label: "VLANs" },
];
const STATE: Record<LinkState, [string, string, string]> = {
  both: ["ok", L("beidseitig", "two-sided"), L("beide Geräte melden die Verbindung per CDP/LLDP", "both devices report the link via CDP/LLDP")],
  one_sided: [
    "warn",
    L("einseitig", "one-sided"),
    L("das Gerät gegenüber wird gelesen, meldet die Verbindung aber nicht", "the device opposite is read but does not report the link"),
  ],
  unread: [
    "na",
    L("Gegenstelle ohne CDP/LLDP", "Peer without CDP/LLDP"),
    L(
      "vom Gerät gegenüber liegen keine Nachbar-Daten vor (nicht gelesen, Firewall, Server)",
      "no neighbor data from the device opposite (not read, firewall, server)",
    ),
  ],
};
const KIND: Record<string, string> = {
  switch: "Switch",
  leaf: L("ACI-Leaf", "ACI leaf"),
  spine: L("ACI-Spine", "ACI spine"),
  controller: "APIC",
  firewall: "Firewall",
  router: "Router",
  esxi: L("ESXi-Host", "ESXi host"),
  unknown: L("nicht gelesen", "not read"),
};

function StateChip({ state }: { state: LinkState }) {
  const [cls, label, title] = STATE[state];
  return (
    <span className={`chip ${cls}`} title={title}>
      {label}
    </span>
  );
}

function portText(e: CablingEnd | CablingPort): string {
  const parts: string[] = [];
  const speed = [e.speed, e.duplex].filter(Boolean).join("/");
  if (speed) parts.push(speed);
  if (e.mode === "access") parts.push(`Access ${e.vlan ?? "?"}`);
  if (e.mode === "tagged") parts.push(`Trunk ${e.trunk_vlans ?? "?"}${e.native_vlan ? L(` (nativ ${e.native_vlan})`, ` (native ${e.native_vlan})`) : ""}`);
  if (e.lag) parts.push(e.lag + (e.vpc ? ` vPC ${e.vpc}` : ""));
  return parts.join(" · ");
}

export default function Cabling({ tenantId, tenantLabel }: { tenantId: number | null; tenantLabel: string }) {
  const [tab, setTab] = useState<Tab>("links");
  const [data, setData] = useState<{ summary: CablingSummary; items: CablingDevice[] } | null>(null);
  const [device, setDevice] = useState("");
  const [epPort, setEpPort] = useState<{ device: string; port: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (tenantId === null) return;
    setData(null);
    fetchCablingDevices(tenantId).then(setData).catch(setErr);
  }, [tenantId]);
  const s = data?.summary;
  const open = (key: string, to: Tab) => {
    setDevice(key);
    setTab(to);
  };
  const endpointsAt = (dev: string, port: string) => {
    setEpPort({ device: dev, port });
    setTab("endpoints");
  };
  return (
    <>
      <PageHead title={L("Verkabelung", "Cabling")}>
        {L(
          <>
            Verbindungen zwischen Geräten aus CDP/LLDP von {tenantLabel}: Switches und Router (Cisco IOS/IOS-XE, NX-OS, Catalyst 1200/1300, Juniper Junos, Arista EOS, Aruba CX – per SSH gelesen),
            ACI-Leafs und weitere Geräte. Beidseitig = beide Seiten melden die Verbindung; Abweichungen (Speed, Duplex, VLANs,
            Port-Channel) zwischen zwei gelesenen Switches sind Befunde. Rein lesend.
          </>,
          <>
            Links between devices from CDP/LLDP of {tenantLabel}: switches and routers (Cisco IOS/IOS-XE, NX-OS, Catalyst 1200/1300, Juniper Junos, Arista EOS, Aruba CX – read via SSH),
            ACI leafs and other devices. Two-sided = both sides report the link; deviations (speed, duplex, VLANs,
            Port-Channel) between two read switches are findings. Read-only.
          </>,
        )}
      </PageHead>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {data === null ? (
        <Loading />
      ) : s && s.links === 0 && s.switches === 0 ? (
        <Note>
          {L(
            <>
              Noch keine Verkabelung. Unter „Mandanten &amp; Standorte“ ein Quellsystem „Switches &amp; Router“ mit den Adressen der
              Switches und einem Konto nur mit Leserechten anlegen und einlesen; ACI-Leafs bringen ihre LLDP-Nachbarn mit.
            </>,
            <>
              No cabling yet. Under “Tenants &amp; sites” create a source system “Switches &amp; routers” with the addresses of the
              switches and a read-only account, then collect; ACI leafs bring their LLDP neighbors along.
            </>,
          )}
        </Note>
      ) : (
        s && (
          <p className="faint" id="cabling-summary">
            {L(
              <>
                {s.switches} Switches · {s.devices} gelesene Geräte · {s.links} Verbindungen ({s.states.both} beidseitig,{" "}
                {s.states.one_sided} einseitig, {s.states.unread} Gegenstelle ohne CDP/LLDP) ·{" "}
                <span className={s.issues ? "crit" : ""}>{s.issues} Abweichungen</span>
                {s.endpoints ? <> · {s.endpoints} Endpunkte (MACs an den Ports)</> : null}
              </>,
              <>
                {s.switches} switches · {s.devices} read devices · {s.links} links ({s.states.both} two-sided,{" "}
                {s.states.one_sided} one-sided, {s.states.unread} peer without CDP/LLDP) ·{" "}
                <span className={s.issues ? "crit" : ""}>{s.issues} deviations</span>
                {s.endpoints ? <> · {s.endpoints} endpoints (MACs on the ports)</> : null}
              </>,
            )}
          </p>
        )
      )}
      <div className="tabbar sticky">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {tenantId !== null && tab === "plan" && <PlanTab tenantId={tenantId} onEndpoints={endpointsAt} />}
      {tenantId !== null && tab === "links" && (
        <LinksTab tenantId={tenantId} devices={data?.items ?? []} device={device} onDevice={setDevice} />
      )}
      {tab === "devices" && <DevicesTab items={data?.items ?? []} onOpen={open} />}
      {tenantId !== null && tab === "ports" && (
        <PortsTab tenantId={tenantId} devices={data?.items ?? []} device={device} onDevice={setDevice} onEndpoints={endpointsAt} />
      )}
      {tenantId !== null && tab === "endpoints" && (
        <EndpointsTab tenantId={tenantId} devices={data?.items ?? []} at={epPort} onClearAt={() => setEpPort(null)} />
      )}
      {tenantId !== null && tab === "vlans" && <VlansTab tenantId={tenantId} />}
    </>
  );
}

function DeviceSelect({ devices, value, onChange, readOnly = false }: {
  devices: CablingDevice[];
  value: string;
  onChange: (v: string) => void;
  readOnly?: boolean;
}) {
  return (
    <select className="sel" aria-label={L("Gerät", "Device")} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{L("alle Geräte", "all devices")}</option>
      {devices
        .filter((d) => !readOnly || d.kind === "switch")
        .map((d) => (
          <option key={d.key} value={d.key}>
            {d.name}
            {d.read ? "" : L(" (nicht gelesen)", " (not read)")}
          </option>
        ))}
    </select>
  );
}

function LinksTab({ tenantId, devices, device, onDevice }: {
  tenantId: number;
  devices: CablingDevice[];
  device: string;
  onDevice: (v: string) => void;
}) {
  const [state, setState] = useState<LinkState | "">("");
  const [issues, setIssues] = useState(false);
  const [q, setQ] = useState("");
  const query = useDebounced(q);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; items: CablingLink[] } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => setOffset(0), [device, state, issues, query]);
  useEffect(() => {
    let live = true;
    fetchCablingLinks(tenantId, { device, state, issues, q: query, offset, limit: PAGE })
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, device, state, issues, query, offset]);
  return (
    <div className="card" id="cabling-links">
      <div className="card-head">
        <h2>{L("Verbindungen", "Links")}</h2>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <DeviceSelect devices={devices} value={device} onChange={onDevice} />
          <select className="sel" aria-label={L("Zustand", "State")} value={state} onChange={(e) => setState(e.target.value as LinkState | "")}>
            <option value="">{L("alle Zustände", "all states")}</option>
            {(Object.keys(STATE) as LinkState[]).map((k) => (
              <option key={k} value={k}>
                {STATE[k][1]}
              </option>
            ))}
          </select>
          <label className="check">
            <input type="checkbox" checked={issues} onChange={(e) => setIssues(e.target.checked)} />{" "}
            {L("nur Abweichungen", "deviations only")}
          </label>
          <input className="input" type="search" aria-label={L("Suchen", "Search")} placeholder={L("Gerät, Port, Beschreibung", "Device, port, description")} value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
      </div>
      <div className="card-body scroll">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {data === null ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <Empty title={L("keine Verbindungen", "no links")} />
        ) : (
          <table className="compact">
            <thead>
              <tr>
                <th>{L("Gerät", "Device")}</th>
                <th>Port</th>
                <th />
                <th>{L("Gegenstelle", "Peer")}</th>
                <th>Port</th>
                <th>{L("Zustand", "State")}</th>
                <th>{L("Abweichung / Konfiguration", "Deviation / configuration")}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((l) => (
                <tr key={l.id} data-link={l.id} className={l.issues.length ? "row-crit" : undefined}>
                  <td className="nowrap">
                    <b>{l.a.device}</b>
                    {!l.a.read && <span className="faint"> ({KIND[l.a.kind] ?? l.a.kind})</span>}
                  </td>
                  <td className="mono nowrap" title={l.a.description ?? undefined}>
                    {l.a.port ?? <Dash />}
                  </td>
                  <td className="faint">↔</td>
                  <td className="nowrap">
                    <b>{l.b.device}</b>
                    {!l.b.read && <span className="faint"> ({KIND[l.b.kind] ?? l.b.kind})</span>}
                  </td>
                  <td className="mono nowrap" title={l.b.description ?? undefined}>
                    {l.b.port ?? <Dash />}
                  </td>
                  <td className="nowrap">
                    <StateChip state={l.state} /> <span className="faint">{l.protocols.join("/").toUpperCase()}</span>
                  </td>
                  <td>
                    {l.issues.map((i) => (
                      <div key={i.type} className="crit">
                        {i.text}
                      </div>
                    ))}
                    {!l.issues.length && <span className="faint">{portText(l.a) || <Dash />}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && <Pager total={data.total} offset={offset} onOffset={setOffset} pageSize={PAGE} />}
      </div>
    </div>
  );
}

function DevicesTab({ items, onOpen }: { items: CablingDevice[]; onOpen: (key: string, tab: Tab) => void }) {
  if (items.length === 0) return <Empty title={L("keine Geräte", "no devices")} />;
  return (
    <div className="card" id="cabling-devices">
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>{L("Gerät", "Device")}</th>
              <th>{L("Art", "Type")}</th>
              <th>{L("Quelle", "Source")}</th>
              <th>{L("Modell / Version", "Model / version")}</th>
              <th>Ports</th>
              <th>{L("Verbindungen", "Links")}</th>
              <th>{L("Abweichungen", "Deviations")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((d) => (
              <tr key={d.key} className="clickable" onClick={() => onOpen(d.key, d.kind === "switch" ? "ports" : "links")}>
                <td className="nowrap">
                  <b>{d.name}</b> {d.mgmt_ip && <span className="faint mono">{d.mgmt_ip}</span>}
                  <SharedChip shared={d.shared} />
                </td>
                <td>{KIND[d.kind] ?? d.kind}</td>
                <td className="faint">{d.source ?? (d.read ? "" : L("nur von Nachbarn gemeldet", "reported by neighbors only"))}</td>
                <td>
                  {d.model ?? <Dash />} {d.version && <span className="faint">· {d.version}</span>}
                </td>
                <td>{d.read && d.ports ? L(`${d.connected} von ${d.ports} verbunden`, `${d.connected} of ${d.ports} connected`) : <Dash />}</td>
                <td>
                  {d.links}
                  {d.one_sided ? <span className="faint">{L(` (${d.one_sided} einseitig)`, ` (${d.one_sided} one-sided)`)}</span> : ""}
                </td>
                <td className={d.issues ? "crit" : "faint"}>{d.issues || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PortsTab({ tenantId, devices, device, onDevice, onEndpoints }: {
  tenantId: number;
  devices: CablingDevice[];
  device: string;
  onDevice: (v: string) => void;
  onEndpoints: (device: string, port: string) => void;
}) {
  const [status, setStatus] = useState("");
  const [vlan, setVlan] = useState("");
  const [q, setQ] = useState("");
  const query = useDebounced(q);
  const vlanNo = useDebounced(vlan);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; items: CablingPort[] } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => setOffset(0), [device, status, vlanNo, query]);
  useEffect(() => {
    let live = true;
    const v = /^\d+$/.test(vlanNo) ? Number(vlanNo) : null;
    fetchCablingPorts(tenantId, { device, status, vlan: v, q: query, offset, limit: PAGE })
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, device, status, vlanNo, query, offset]);
  return (
    <div className="card" id="cabling-ports">
      <div className="card-head">
        <h2>{L("Ports der Switches", "Ports of the switches")}</h2>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <DeviceSelect devices={devices} value={device} onChange={onDevice} readOnly />
          <select className="sel" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">{L("alle Status", "all statuses")}</option>
            <option value="up">{L("verbunden", "connected")}</option>
            <option value="down">{L("nicht verbunden", "not connected")}</option>
            <option value="disabled">{L("abgeschaltet", "disabled")}</option>
          </select>
          <input className="input" style={{ width: 90 }} aria-label="VLAN" placeholder="VLAN" value={vlan} onChange={(e) => setVlan(e.target.value)} />
          <input className="input" type="search" aria-label={L("Suchen", "Search")} placeholder={L("Port, Beschreibung, Nachbar", "Port, description, neighbor")} value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
      </div>
      <div className="card-body scroll">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {data === null ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <Empty title={L("keine Ports", "no ports")} />
        ) : (
          <table className="compact">
            <thead>
              <tr>
                <th>Switch</th>
                <th>Port</th>
                <th>Status</th>
                <th>{L("Konfiguration", "Configuration")}</th>
                <th>{L("Beschreibung", "Description")}</th>
                <th>{L("Gegenstelle", "Peer")}</th>
                <th title={L("MACs am Port (ohne Uplinks)", "MACs on the port (without uplinks)")}>{L("Endpunkte", "Endpoints")}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((p) => (
                <tr key={`${p.device}|${p.name}`} data-port={p.name}>
                  <td className="nowrap">{p.device}</td>
                  <td className="mono nowrap">{p.name}</td>
                  <td className="nowrap">
                    <span className={`chip ${p.status === "connected" || p.status === "up" ? "ok" : p.enabled === false ? "na" : ""}`}>
                      {p.enabled === false ? L("abgeschaltet", "disabled") : (p.status ?? "?")}
                    </span>
                  </td>
                  <td className="faint">
                    {portText(p) || <Dash />}
                    {p.vpc_peer_link && (
                      <div className="port-l2">
                        {L(<>vPC-Peer-Link (Domäne {p.vpc_peer_link})</>, <>vPC peer link (domain {p.vpc_peer_link})</>)}
                      </div>
                    )}
                    {p.stp_blocked && (
                      <div
                        className="port-l2 warn"
                        title={L("Spanning Tree blockiert diese VLANs am Port (Reserve)", "Spanning Tree blocks these VLANs on the port (standby)")}
                      >
                        {L(<>STP blockiert VLAN {p.stp_blocked}</>, <>STP blocks VLAN {p.stp_blocked}</>)}
                      </div>
                    )}
                    {p.stp_root && <div className="port-l2">{L(<>Root-Port VLAN {p.stp_root}</>, <>Root port VLAN {p.stp_root}</>)}</div>}
                  </td>
                  <td>{p.description ?? <Dash />}</td>
                  <td>
                    {p.peer ? (
                      <>
                        {p.peer.device} <span className="mono">{p.peer.port}</span>{" "}
                        {p.link_state && <StateChip state={p.link_state} />}
                        {p.issues.map((i) => (
                          <div key={i} className="crit">
                            {i}
                          </div>
                        ))}
                      </>
                    ) : (
                      <Dash />
                    )}
                  </td>
                  <td>
                    {p.endpoints ? (
                      <button className="chip linkchip" onClick={() => onEndpoints(p.device, p.name)} title={L("Endpunkte am Port", "Endpoints on the port")}>
                        {p.endpoints}
                      </button>
                    ) : (
                      <span className="faint">–</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && <Pager total={data.total} offset={offset} onOffset={setOffset} pageSize={PAGE} />}
      </div>
    </div>
  );
}

// Wer ist das: VM (mit Host), sonst IPAM-Namen, IPs (IPv4/IPv6), EPG; Link-local getrennt (0.94.0)
function Who({ e }: { e: { vm: CablingEndpoint["vm"]; names: string[]; ips: string[]; epgs: string[]; link_local?: string[] } }) {
  return (
    <>
      {e.vm ? (
        <>
          <b>{e.vm.name}</b>{" "}
          <span className="faint">
            VM{e.vm.host ? L(` auf ${e.vm.host}`, ` on ${e.vm.host}`) : ""}
            {e.vm.portgroup ? ` · ${e.vm.portgroup}` : ""}
          </span>
        </>
      ) : e.names.length ? (
        <b>{e.names.join(", ")}</b>
      ) : (
        <span className="faint">{L("unbekannt", "unknown")}</span>
      )}
      {e.ips.length > 0 && <span className="mono faint"> · {e.ips.join(", ")}</span>}
      {(e.link_local ?? []).length > 0 && (
        <span
          className="mono faint link-local"
          title={L("IPv6 Link-local – gilt nur am Link, nicht als Identität", "IPv6 link-local – valid on the link only, not used as identity")}
        >
          {" "}
          · link-local {(e.link_local ?? []).join(", ")}
        </span>
      )}
      {e.epgs.length > 0 && <span className="faint"> · EPG {e.epgs.join(", ")}</span>}
    </>
  );
}

function at(e: CablingEndpoint): string {
  const via = e.via ? ` · ${L("über", "via")} ${e.via.device}${e.via.port ? ` (${e.via.port})` : ""}${KIND[e.via.kind] && !e.via.read ? ` – ${KIND[e.via.kind]}` : ""}` : "";
  return `${e.device} ${e.port}${e.vlan ? ` · VLAN ${e.vlan}` : ""}${via}`;
}

function Locate({ tenantId }: { tenantId: number }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ query: string; items: EndpointLocation[] } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const run = (ev: FormEvent) => {
    ev.preventDefault();
    if (q.trim().length < 2) return;
    setBusy(true);
    setErr(null);
    locateEndpoint(tenantId, q.trim())
      .then(setRes)
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  return (
    <div className="card" id="cabling-locate">
      <div className="card-head">
        <h2>{L("Wo hängt …", "Where is … connected?")}</h2>
        <form onSubmit={run} style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
          <input className="input" type="search" aria-label={L("Wo hängt", "Where is connected")} placeholder={L("MAC, IP (v4/v6) oder Name (VM, DNS)", "MAC, IP (v4/v6) or name (VM, DNS)")} value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 280 }} />
          <button className="btn btn-sm" type="submit" disabled={busy || q.trim().length < 2}>
            {busy ? L("Suche…", "Searching…") : L("Suchen", "Search")}
          </button>
        </form>
      </div>
      <div className="card-body pad">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {res === null ? (
          <span className="faint">
            {L(
              <>
                Sucht die MAC über das vCenter (VM), ARP und IPv6-Nachbarn (NDP) der Switches, ACI-Endpunkte bzw. das IPAM und
                zeigt den Port, an dem sie gelernt ist – nicht die Uplinks zwischen den Switches.
              </>,
              <>
                Searches the MAC via the vCenter (VM), ARP and IPv6 neighbors (NDP) of the switches, ACI endpoints or the IPAM and
                shows the port where it is learned – not the uplinks between the switches.
              </>,
            )}
          </span>
        ) : res.items.length === 0 ? (
          <Empty title={L(`keine MAC zu „${res.query}“ in den MAC-Tabellen`, `no MAC for “${res.query}” in the MAC tables`)} />
        ) : (
          res.items.map((it) => (
            <div key={it.mac} className="locate-hit" data-mac={it.mac} style={{ marginBottom: 12 }}>
              <div>
                <span className="mono">{it.mac}</span> <span className="chip">{it.reason}</span> <Who e={it} />
              </div>
              {it.locations.map((l) => (
                <div key={`${l.device}|${l.port}|${l.vlan}`} className="locate-at">
                  ▸ <b>{at(l)}</b> <span className="faint">{L(<>· zuletzt {fmtTime(l.last_seen)}</>, <>· last seen {fmtTime(l.last_seen)}</>)}</span>
                  {l.port_description && <span className="faint"> · „{l.port_description}“</span>}
                </div>
              ))}
              {it.locations.length === 0 && it.behind.length > 0 && (
                <div className="locate-behind faint">
                  {L("nur auf Uplinks gesehen – dahinter:", "seen on uplinks only – behind:")}{" "}
                  {it.behind.map((l) => (
                    <span key={`${l.device}|${l.port}`}>
                      {l.via ? `${l.via.device} (${KIND[l.via.kind] ?? l.via.kind})` : "?"} {L("an", "on")} {l.device} {l.port}{" "}
                    </span>
                  ))}
                </div>
              )}
              {it.locations.length === 0 && it.behind.length === 0 && <div className="faint">{L("zurzeit in keiner MAC-Tabelle", "currently in no MAC table")}</div>}
              {it.earlier.length > 0 && (
                <div className="locate-earlier faint">
                  {L("früher:", "earlier:")} {it.earlier.map((l) => `${l.device} ${l.port} (${fmtTime(l.last_seen)})`).join(", ")}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function EndpointsTab({ tenantId, devices, at: port, onClearAt }: {
  tenantId: number;
  devices: CablingDevice[];
  at: { device: string; port: string } | null;
  onClearAt: () => void;
}) {
  const [device, setDevice] = useState("");
  const [vlan, setVlan] = useState("");
  const [q, setQ] = useState("");
  const query = useDebounced(q);
  const vlanNo = useDebounced(vlan);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchCablingEndpoints>> | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => setOffset(0), [device, vlanNo, query, port]);
  useEffect(() => {
    let live = true;
    const v = /^\d+$/.test(vlanNo) ? Number(vlanNo) : null;
    const dev = port ? port.device : device;
    fetchCablingEndpoints(tenantId, { device: dev, port: port?.port, vlan: v, q: query, offset, limit: PAGE })
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, device, vlanNo, query, offset, port]);
  return (
    <>
      <Locate tenantId={tenantId} />
      <div className="card" id="cabling-endpoints">
        <div className="card-head">
          <h2>{L("Endpunkte an den Ports", "Endpoints on the ports")}</h2>
          {port && (
            <span className="chip" id="cabling-endpoints-port">
              {port.device} {port.port}{" "}
              <button className="linkchip" style={{ border: 0, background: "none", padding: 0 }} onClick={onClearAt} aria-label={L("Port-Filter entfernen", "Remove port filter")}>
                ✕
              </button>
            </span>
          )}
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {!port && <DeviceSelect devices={devices} value={device} onChange={setDevice} readOnly />}
            <input className="input" style={{ width: 90 }} aria-label="VLAN" placeholder="VLAN" value={vlan} onChange={(e) => setVlan(e.target.value)} />
            <input className="input" type="search" aria-label={L("Suchen", "Search")} placeholder="MAC, VM, IP, Port" value={q} onChange={(e) => setQ(e.target.value)} />
          </span>
        </div>
        <div className="card-body scroll">
          <ErrorBox error={err} onClose={() => setErr(null)} />
          {data === null ? (
            <Loading />
          ) : data.items.length === 0 ? (
            <Empty title={L("keine Endpunkte", "no endpoints")} />
          ) : (
            <>
              <p className="faint" id="cabling-endpoints-summary">
                {L(
                  <>
                    {data.summary.macs} MACs an {data.summary.ports} Ports · {data.summary.vms} VMs · {data.summary.with_ip} mit IP
                  </>,
                  <>
                    {data.summary.macs} MACs on {data.summary.ports} ports · {data.summary.vms} VMs · {data.summary.with_ip} with IP
                  </>,
                )}
              </p>
              <table className="compact">
                <thead>
                  <tr>
                    <th>Switch</th>
                    <th>Port</th>
                    <th>VLAN</th>
                    <th>MAC</th>
                    <th>{L("Gerät", "Device")}</th>
                    <th>{L("über", "via")}</th>
                    <th>{L("zuletzt", "last seen")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((e) => (
                    <tr key={`${e.device}|${e.port}|${e.vlan}|${e.mac}`} data-mac={e.mac}>
                      <td className="nowrap">{e.device}</td>
                      <td className="mono nowrap" title={e.port_description ?? undefined}>
                        {e.port}
                      </td>
                      <td className="mono">{e.vlan ?? <Dash />}</td>
                      <td className="mono nowrap">{e.mac}</td>
                      <td>
                        <Who e={e} />
                      </td>
                      <td className="faint">{e.via ? `${e.via.device}${e.via.port ? ` ${e.via.port}` : ""}` : <Dash />}</td>
                      <td className="faint nowrap">{fmtTime(e.last_seen)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {data && <Pager total={data.total} offset={offset} onOffset={setOffset} pageSize={PAGE} />}
        </div>
      </div>
    </>
  );
}

function VlansTab({ tenantId }: { tenantId: number }) {
  const [items, setItems] = useState<CablingVlan[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    fetchCablingVlans(tenantId).then(setItems).catch(setErr);
  }, [tenantId]);
  if (err) return <ErrorBox error={err} onClose={() => setErr(null)} />;
  if (items === null) return <Loading />;
  if (items.length === 0) return <Empty title={L("keine VLANs auf den Switches bzw. im ACI", "no VLANs on the switches or in the ACI")} />;
  return (
    <div className="card" id="cabling-vlans">
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>VLAN</th>
              <th>Name</th>
              <th>EPGs (ACI)</th>
              <th>{L("Geräte (Access-Ports · Trunks)", "Devices (access ports · trunks)")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((v) => (
              <tr key={v.vid} data-vlan={v.vid}>
                <td className="mono">
                  {v.vid}
                  {(v.sources ?? []).map((s) => (
                    <span key={s} className="chip" style={{ marginLeft: 4 }} title={s === "aci" ? L("aus dem APIC", "from the APIC") : L("von den Switches", "from the switches")}>
                      {s === "aci" ? "ACI" : "Switch"}
                    </span>
                  ))}
                </td>
                <td>{v.names.join(" / ") || <Dash />}</td>
                <td className="mono">{v.epgs?.length ? v.epgs.join(", ") : <Dash />}</td>
                <td>
                  {v.devices.length === 0 ? (
                    <span className="faint">{L("an keinem Port", "on no port")}</span>
                  ) : (
                    v.devices.map((d) => (
                      <span key={d.device} className="chip" style={{ marginRight: 4 }}>
                        {d.device} · {d.access} · {d.trunks}
                      </span>
                    ))
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Plan (0.64.0): Geräte nach Ebenen ab dem Kern, Kabel je Gerätepaar; VLAN hervorheben (Inseln), Ausfall
// eines Geräts (Klick) bzw. Kabels (Klick auf die Linie) simulieren - getrennte Geräte blass. Seit 0.65.0
// vPC-Paare nebeneinander mit Rahmen, von Spanning Tree blockierte Kabel gestrichelt, mit Shift-Klick
// mehrere gleichzeitige Ausfälle, Export als SVG bzw. draw.io.
const NODE_W = 160;
const NODE_H = 44;
const VLAN_EDGE: Record<string, [string, string]> = {
  yes: ["var(--good)", L("lässt das VLAN durch", "passes the VLAN")],
  no: ["var(--crit)", L("sperrt das VLAN", "blocks the VLAN")],
  partial: ["var(--warn)", L("nur ein Teil der Kabel lässt das VLAN durch", "only some of the cables pass the VLAN")],
  standby: ["#7b1fa2", L("Spanning Tree blockiert das VLAN hier – Reserve", "Spanning Tree blocks the VLAN here – standby")],
  unknown: ["var(--faint)", L("Konfiguration unbekannt", "configuration unknown")],
};

function edgeColor(e: L2PlanEdge): string {
  if (e.vlan) return VLAN_EDGE[e.vlan][0];
  if (e.issues) return "var(--crit)";
  if (e.state === "one_sided") return "var(--warn)";
  return "var(--faint)";
}

function edgeDash(e: L2PlanEdge): string | undefined {
  if (e.vlan === "no") return "6 4";
  if (e.vlan === "standby" || (!e.vlan && e.stp)) return "3 3";
  if (e.vlan === "unknown") return "2 4";
  return undefined;
}

// Ausgewählte Ausfälle: Geräte (Name) und Kabel je Gerätepaar (alle Link-IDs des Paars)
type FailSel = { devices: string[]; edges: { label: string; ids: string[] }[] };
const NO_FAIL: FailSel = { devices: [], edges: [] };

function failText(s: FailSel): ReactNode {
  const items = [
    ...s.devices.map((d) => <b key={`d:${d}`}>{d}</b>),
    ...s.edges.map((e) => (
      <span key={`e:${e.label}`}>
        {L(
          <>
            das Kabel{e.ids.length > 1 ? ` (alle ${e.ids.length})` : ""} <b>{e.label}</b>
          </>,
          <>
            the cable{e.ids.length > 1 ? `s (all ${e.ids.length})` : ""} <b>{e.label}</b>
          </>,
        )}
      </span>
    )),
  ];
  const list = (and: string) =>
    items.map((x, i) => (
      <span key={i}>
        {i > 0 && (i === items.length - 1 ? and : ", ")}
        {x}
      </span>
    ));
  return L(
    <>
      {items.length > 1 ? "Fallen " : "Fällt "}
      {list(" und ")}{" "}
      aus
    </>,
    <>Failure of {list(" and ")}</>,
  );
}

function PlanTab({ tenantId, onEndpoints }: { tenantId: number; onEndpoints?: (device: string, port: string) => void }) {
  const [ends, setEnds] = useState(true);
  // Klick zeigt die Patchungen (0.70.0, Standard) bzw. simuliert den Ausfall
  const [mode, setMode] = useState<"patch" | "fail">("patch");
  const [pick, setPick] = useState<{ device: string; peer?: string } | null>(null);
  const [vlan, setVlan] = useState("");
  const vlanNo = useDebounced(vlan);
  const vlanId = /^\d+$/.test(vlanNo) ? Number(vlanNo) : null;
  const [plan, setPlan] = useState<L2Plan | null>(null);
  const [fail, setFail] = useState<L2Failure | null>(null);
  const [sel, setSel] = useState<FailSel>(NO_FAIL);
  const [multi, setMulti] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const seq = useRef(0);
  useEffect(() => {
    let live = true;
    setPlan(null);
    fetchL2Plan(tenantId, { ends, vlan: vlanId })
      .then((d) => live && setPlan(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, ends, vlanId]);
  const byKey = useMemo(() => new Map((plan?.nodes ?? []).map((n) => [n.key, n])), [plan]);
  const main = useMemo(() => new Set(fail?.main ?? []), [fail]);
  const run = (s: FailSel) => {
    const n = ++seq.current;
    setErr(null);
    setMulti(null);
    setSel(s);
    if (!s.devices.length && !s.edges.length) {
      setFail(null);
      return;
    }
    fetchL2Failure(tenantId, { devices: s.devices, links: s.edges.flatMap((e) => e.ids) })
      .then((d) => n === seq.current && setFail(d))
      .catch((e) => n === seq.current && setErr(e));
  };
  // Klick = nur dieser Ausfall; Shift-Klick = hinzufügen bzw. wieder entfernen
  const clickDevice = (name: string, add: boolean) => {
    if (mode === "patch") return setPick({ device: name });
    if (!add) return run({ devices: [name], edges: [] });
    const has = sel.devices.includes(name);
    run({ ...sel, devices: has ? sel.devices.filter((d) => d !== name) : [...sel.devices, name] });
  };
  const clickEdge = (label: string, ids: string[], add: boolean, a: string, b: string) => {
    if (mode === "patch") return setPick(pick?.device === b ? { device: b, peer: a } : { device: a, peer: b });
    if (add) {
      const has = sel.edges.some((e) => e.label === label);
      return run({ ...sel, edges: has ? sel.edges.filter((e) => e.label !== label) : [...sel.edges, { label, ids }] });
    }
    if (ids.length === 1) return run({ devices: [], edges: [{ label, ids }] });
    seq.current++;
    setFail(null);
    setSel(NO_FAIL);
    setMulti(label);
  };
  const reset = () => run(NO_FAIL);
  const switchMode = (m: "patch" | "fail") => {
    setMode(m);
    setPick(null);
    run(NO_FAIL);
  };
  const exportPlan = async (format: "svg" | "drawio") => {
    setBusy(format);
    setErr(null);
    try {
      const { blob, filename } = await exportL2Plan(tenantId, format, { ends, vlan: vlanId });
      download(filename, blob);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };
  const failedDevs = new Set(sel.devices);
  const failedEdges = new Set(sel.edges.map((e) => e.label));
  const dim = (name: string) => fail !== null && !main.has(name) && !failedDevs.has(name);
  const picked = (a: string, b: string) =>
    pick !== null && (pick.peer ? new Set([a, b]).has(pick.device) && new Set([a, b]).has(pick.peer) : a === pick.device || b === pick.device);
  return (
    <div className="card" id="cabling-plan">
      <div className="card-head">
        <h2>Plan</h2>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <label className="check">
            <input type="checkbox" checked={ends} onChange={(e) => setEnds(e.target.checked)} /> {L("Endgeräte", "End devices")}
          </label>
          <input className="input" style={{ width: 110 }} aria-label={L("VLAN hervorheben", "Highlight VLAN")} placeholder="VLAN" value={vlan} onChange={(e) => setVlan(e.target.value)} />
          <span className="faint">{L("Klick:", "Click:")}</span>
          <span className="seg" id="cabling-plan-mode" role="group" aria-label={L("Klick zeigt", "Click shows")}>
            <button className={mode === "patch" ? "on" : ""} aria-pressed={mode === "patch"} onClick={() => switchMode("patch")}>
              {L("Patchungen", "Patching")}
            </button>
            <button className={mode === "fail" ? "on" : ""} aria-pressed={mode === "fail"} onClick={() => switchMode("fail")}>
              {L("Ausfall", "Failure")}
            </button>
          </span>
          {fail && (
            <button className="btn btn-sm btn-ghost" onClick={reset}>
              {L("Ausfall zurücksetzen", "Reset failure")}
            </button>
          )}
          <span className="plan-export" id="cabling-plan-export">
            <span className="faint">Export:</span>
            {(
              [
                ["drawio", "draw.io"],
                ["svg", "SVG"],
              ] as const
            ).map(([f, label]) => (
              <button key={f} className="btn btn-sm" disabled={busy !== null || plan === null} onClick={() => void exportPlan(f)}>
                {busy === f ? "…" : label}
              </button>
            ))}
            <ExportLangSelect />
          </span>
        </span>
      </div>
      <div className="card-body pad">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        <p className="faint" style={{ marginTop: 0 }}>
          {L(
            <>
              Kern oben (meistvernetzte Switches bzw. Spines), darunter je Abstand die weiteren Switches, Endgeräte unter ihrem
              obersten Nachbarn; vPC-Paare nebeneinander im Rahmen, von Spanning Tree blockierte Kabel gestrichelt.{" "}
            </>,
            <>
              Core at the top (most connected switches or spines), below by distance the other switches, end devices below their
              topmost neighbor; vPC pairs side by side in a frame, cables blocked by Spanning Tree dashed.{" "}
            </>,
          )}
          {mode === "patch"
            ? L(
                <>
                  Klick auf ein Gerät zeigt darunter seine <b>Patchungen</b> (Ports beider Seiten mit Konfiguration), Klick auf ein Kabel nur die
                  Kabel zwischen den beiden Geräten. Umschalten auf „Ausfall“, um Ausfälle zu simulieren.
                </>,
                <>
                  Click on a device shows its <b>patching</b> below (ports of both sides with configuration), click on a cable only the
                  cables between the two devices. Switch to “Failure” to simulate failures.
                </>,
              )
            : L(
                <>
                  Klick auf ein Gerät bzw. ein Kabel simuliert dessen Ausfall, <b>Shift-Klick</b> nimmt weitere hinzu (mehrere gleichzeitig,
                  bei mehreren Kabeln zwischen zwei Geräten alle).
                </>,
                <>
                  Click on a device or a cable simulates its failure, <b>Shift-click</b> adds more (several at once, with
                  several cables between two devices all of them).
                </>,
              )}
        </p>
        {fail && (
          <div className="note" id="cabling-failure">
            {failText(sel)}
            {fail.cut_off.length === 0 ? (
              L(<>: nichts getrennt – es gibt einen anderen Weg.</>, <>: nothing cut off – there is another path.</>)
            ) : (
              <>
                {L(", ist getrennt: ", " cuts off: ")}
                {fail.cut_off.map((p) => p.join(", ")).join(" | ")}
                {fail.parts.length > 2 && <span className="faint">{L(` (${fail.parts.length} Teile)`, ` (${fail.parts.length} parts)`)}</span>}
              </>
            )}
          </div>
        )}
        {plan?.vlan && (
          <div className={`note ${plan.vlan.islands.length > 1 ? "warn" : ""}`} id="cabling-vlan-islands">
            VLAN {plan.vlan.vid}:{" "}
            {plan.vlan.present.length === 0
              ? L("an keinem Access-Port bzw. SVI der gelesenen Switches", "on no access port or SVI of the read switches")
              : plan.vlan.islands.length > 1
                ? L(
                    `getrennt in ${plan.vlan.islands.length} Inseln – ${plan.vlan.islands.map((i) => i.join(", ")).join(" | ")}`,
                    `split into ${plan.vlan.islands.length} islands – ${plan.vlan.islands.map((i) => i.join(", ")).join(" | ")}`,
                  )
                : L(`durchgehend (${plan.vlan.present.join(", ")})`, `continuous (${plan.vlan.present.join(", ")})`)}
          </div>
        )}
        {plan === null ? (
          <Loading />
        ) : plan.nodes.length === 0 ? (
          <Empty title={L("keine Geräte mit Verbindungen", "no devices with links")} />
        ) : (
          <div style={{ overflow: "auto", maxHeight: "70vh" }}>
            <svg width={Math.max(plan.width, 400)} height={plan.height} id="cabling-plan-svg" role="img" aria-label={L("Plan der Verkabelung", "Cabling plan")}>
              {plan.pairs.map((pr) => {
                const a = byKey.get(pr.a);
                const b = byKey.get(pr.b);
                if (!a || !b) return null;
                const x0 = Math.min(a.x, b.x) - NODE_W / 2 - 8;
                const y0 = Math.min(a.y, b.y) - NODE_H / 2 - 16;
                return (
                  <g key={`${pr.a}~${pr.b}`} className="plan-vpc" data-vpc={pr.domain}>
                    <title>
                      {L(
                        `vPC-Paar ${a.name} + ${b.name} (Domäne ${pr.domain}) – zusammen ein logischer Switch`,
                        `vPC pair ${a.name} + ${b.name} (domain ${pr.domain}) – together one logical switch`,
                      )}
                    </title>
                    <rect
                      x={x0}
                      y={y0}
                      width={Math.abs(a.x - b.x) + NODE_W + 16}
                      height={Math.abs(a.y - b.y) + NODE_H + 24}
                      rx={10}
                      fill="none"
                      stroke="var(--accent)"
                      strokeDasharray="4 3"
                    />
                    <text x={x0 + 6} y={y0 + 11} fontSize={10} fill="var(--accent)">
                      {L(<>vPC-Domäne {pr.domain}</>, <>vPC domain {pr.domain}</>)}
                    </text>
                  </g>
                );
              })}
              {plan.edges.map((e) => {
                const a = byKey.get(e.a);
                const b = byKey.get(e.b);
                if (!a || !b) return null;
                const label = `${a.name} ↔ ${b.name}`;
                const isPicked = picked(a.name, b.name);
                const off = dim(a.name) || dim(b.name) || (pick !== null && !isPicked);
                const isFailed = failedEdges.has(label);
                const title =
                  `${label}: ${e.links} ${L("Kabel", e.links === 1 ? "cable" : "cables")}${e.lags.length ? ` (${e.lags.join(", ")})` : ""}` +
                  `${e.vlan ? ` – ${VLAN_EDGE[e.vlan][1]}` : ""}${e.stp ? L(` – Spanning Tree blockiert VLAN ${e.stp}`, ` – Spanning Tree blocks VLAN ${e.stp}`) : ""}` +
                  `${e.issues ? L(` – ${e.issues} Abweichungen`, ` – ${e.issues} deviations`) : ""}`;
                return (
                  <g
                    key={`${e.a}~${e.b}`}
                    className="plan-edge clickable"
                    data-edge={label}
                    data-stp={e.stp ?? undefined}
                    data-vlan={e.vlan}
                    opacity={off ? 0.25 : 1}
                    data-picked={isPicked || undefined}
                    onClick={(ev) => clickEdge(label, e.ids, ev.shiftKey, a.name, b.name)}
                  >
                    <title>{title}</title>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="transparent" strokeWidth={12} />
                    <line
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke={isFailed ? "var(--crit)" : isPicked && !e.vlan ? "var(--accent)" : edgeColor(e)}
                      strokeWidth={1 + Math.min(e.links, 4) + (isPicked ? 2 : 0)}
                      strokeDasharray={isFailed ? "6 4" : edgeDash(e)}
                    />
                    {(e.links > 1 || e.stp) && (
                      <text x={(a.x + b.x) / 2 + 6} y={(a.y + b.y) / 2 - 4} className="plan-edge-label" fontSize={11} fill="var(--muted)">
                        {[e.links > 1 ? `${e.links}×` : "", e.stp ? `STP ${e.stp}` : ""].filter(Boolean).join(" · ")}
                      </text>
                    )}
                  </g>
                );
              })}
              {plan.nodes.map((n) => {
                const isPick = pick !== null && (pick.device === n.name || pick.peer === n.name);
                const off = dim(n.name);
                const isFailed = failedDevs.has(n.name);
                return (
                  <g
                    key={n.key}
                    className="plan-node clickable"
                    data-node={n.name}
                    transform={`translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})`}
                    opacity={off ? 0.3 : 1}
                    onClick={(ev) => clickDevice(n.name, ev.shiftKey)}
                  >
                    <title>{`${n.name} – ${KIND[n.kind] ?? n.kind}${n.source ? ` (${n.source})` : ""}`}</title>
                    <rect
                      width={NODE_W}
                      height={NODE_H}
                      rx={6}
                      fill={n.switching ? "var(--surface-2)" : "var(--surface)"}
                      stroke={isFailed ? "var(--crit)" : n.vlan_present ? "var(--good)" : n.switching || isPick ? "var(--accent)" : "var(--border-strong)"}
                      strokeWidth={isFailed || n.vlan_present || isPick ? 3 : 1.5}
                      strokeDasharray={n.read ? undefined : "4 3"}
                    />
                    <text x={NODE_W / 2} y={19} textAnchor="middle" fontSize={12} fontWeight={600} fill="var(--fg)">
                      {n.name.length > 22 ? `${n.name.slice(0, 21)}…` : n.name}
                    </text>
                    <text x={NODE_W / 2} y={35} textAnchor="middle" fontSize={10} fill="var(--muted)">
                      {isFailed ? L("fällt aus", "fails") : (KIND[n.kind] ?? n.kind)}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        )}
        {multi && !fail && (
          <p className="faint" id="cabling-failure-multi">
            {multi}:{" "}
            {L(
              "mehrere Kabel – fällt eines aus, bleiben die anderen (Shift-Klick: alle fallen aus).",
              "several cables – if one fails, the others remain (Shift-click: all fail).",
            )}
          </p>
        )}
        {pick && (
          <PatchPanel
            tenantId={tenantId}
            device={pick.device}
            peer={pick.peer ?? null}
            onPick={(device, peer) => setPick({ device, peer })}
            onClose={() => setPick(null)}
            onEndpoints={onEndpoints}
          />
        )}
      </div>
    </div>
  );
}

// Patchungen (0.70.0): die Kabel des gewählten Geräts - eigenes Ende links, Gegenstelle rechts,
// je Ende Port, Beschreibung und Konfiguration; Port-Channel als Gruppe, Abweichungen darunter
function endText(e: CablingPatchEnd): string {
  const parts = [portText(e)];
  if (e.status) parts.push(e.enabled === false ? L("deaktiviert", "disabled") : e.status);
  if (e.media) parts.push(e.media);
  if (e.stp_blocked) parts.push(L(`STP blockiert ${e.stp_blocked}`, `STP blocks ${e.stp_blocked}`));
  if (e.epgs?.length) parts.push(`EPG ${e.epgs.slice(0, 3).join(", ")}${e.epgs.length > 3 ? ` +${e.epgs.length - 3}` : ""}`);
  return parts.filter(Boolean).join(" · ");
}

function PatchPanel({ tenantId, device, peer, onPick, onClose, onEndpoints }: {
  tenantId: number;
  device: string;
  peer: string | null;
  onPick: (device: string, peer?: string) => void;
  onClose: () => void;
  onEndpoints?: (device: string, port: string) => void;
}) {
  const [data, setData] = useState<CablingPatches | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    setErr(null);
    fetchCablingPatches(tenantId, device, peer)
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, device, peer]);
  const d = data?.device;
  let lag: string | null | undefined;
  const rows: ReactNode[] = [];
  for (const l of data?.items ?? []) {
    if (l.a.lag !== lag) {
      lag = l.a.lag;
      if (lag)
        rows.push(
          <tr key={`lag:${lag}`} className="patch-lag">
            <td colSpan={8}>
              Port-Channel <b className="mono">{lag}</b>
              {l.a.vpc ? ` · vPC ${l.a.vpc}` : ""}
            </td>
          </tr>,
        );
    }
    rows.push(<PatchRow key={l.id} l={l} onPick={onPick} onEndpoints={onEndpoints} />);
  }
  return (
    <div className="patch-panel" id="cabling-patches" data-device={device}>
      <div className="patch-head">
        <h3>
          {L("Patchungen", "Patching")} <span className="mono">{device}</span>
          {peer && (
            <>
              {" "}
              ↔ <span className="mono">{peer}</span>
            </>
          )}
        </h3>
        {d && (
          <span className="faint">
            {KIND[d.kind] ?? d.kind}
            {d.model ? ` · ${d.model}` : ""}
            {d.mgmt_ip ? ` · ${d.mgmt_ip}` : ""}
            {d.source ? ` · ${d.source}` : ""}
            {data && L(` · ${data.total} Kabel`, ` · ${data.total} ${data.total === 1 ? "cable" : "cables"}`)}
            {data && data.issues > 0 && <span className="crit">{L(` · ${data.issues} mit Abweichung`, ` · ${data.issues} with deviation`)}</span>}
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6 }}>
          {peer && (
            <button className="btn btn-sm btn-ghost" onClick={() => onPick(device)}>
              {L(<>alle Kabel von {device}</>, <>all cables of {device}</>)}
            </button>
          )}
          <button className="btn btn-sm btn-ghost" aria-label={L("Patchungen schliessen", "Close patching")} onClick={onClose}>
            ✕
          </button>
        </span>
      </div>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {data === null ? (
        !err && <Loading />
      ) : data.items.length === 0 ? (
        <Empty
          title={
            d?.read
              ? L("keine Kabel aus CDP/LLDP", "no cables from CDP/LLDP")
              : L("Gerät nicht gelesen – keine Ports bekannt", "device not read – no ports known")
          }
        />
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="compact patch-table">
            <thead>
              <tr>
                <th>Port</th>
                <th>{L("Konfiguration", "Configuration")}</th>
                <th />
                <th>{L("Gegenstelle", "Peer")}</th>
                <th>Port</th>
                <th>{L("Konfiguration", "Configuration")}</th>
                <th>{L("Zustand", "State")}</th>
                <th title={L("MACs am eigenen Port (ohne Uplinks)", "MACs on the own port (without uplinks)")}>{L("Endpunkte", "Endpoints")}</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function PatchRow({ l, onPick, onEndpoints }: {
  l: CablingPatch;
  onPick: (device: string, peer?: string) => void;
  onEndpoints?: (device: string, port: string) => void;
}) {
  return (
    <>
      <tr data-patch={l.id} className={l.issues.length ? "row-crit" : undefined}>
        <td className="mono nowrap">
          {l.a.port ?? <Dash />}
          {l.a.description && <div className="faint patch-descr">{l.a.description}</div>}
        </td>
        <td className="faint">{endText(l.a) || <Dash />}</td>
        <td className="faint">↔</td>
        <td className="nowrap">
          <button className="linkbtn patch-link" title={L("Patchungen dieses Geräts", "Patching of this device")} onClick={() => onPick(l.b.device)}>
            <b>{l.b.device}</b>
          </button>
          {!l.b.read && <span className="faint"> ({KIND[l.b.kind] ?? l.b.kind})</span>}
          {l.b.model && <div className="faint">{l.b.model}</div>}
        </td>
        <td className="mono nowrap">
          {l.b.port ?? <Dash />}
          {l.b.description && <div className="faint patch-descr">{l.b.description}</div>}
        </td>
        <td className="faint">{l.b.read ? endText(l.b) || <Dash /> : <span>{L("nicht gelesen", "not read")}</span>}</td>
        <td className="nowrap">
          <StateChip state={l.state} /> <span className="faint">{l.protocols.join("/").toUpperCase()}</span>
        </td>
        <td className="nowrap">
          {l.a.endpoints > 0 && l.a.port && onEndpoints ? (
            <button className="linkbtn patch-link patch-endpoints" title={L("Endpunkte an diesem Port", "Endpoints on this port")} onClick={() => onEndpoints(l.a.device, l.a.port!)}>
              {l.a.endpoints}
            </button>
          ) : (
            <span className="faint">{l.a.endpoints || "–"}</span>
          )}
        </td>
      </tr>
      {l.issues.map((i) => (
        <tr key={`${l.id}:${i.type}`} className="row-crit patch-issue">
          <td />
          <td colSpan={7} className="crit">
            {i.text}
          </td>
        </tr>
      ))}
    </>
  );
}
