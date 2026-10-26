// Verkabelung (0.62.0, ADR 0025): Verbindungen zwischen Geräten aus CDP/LLDP aller Quellen (Switches,
// ACI-Leafs), beidseitig bzw. einseitig gemeldet, mit Abweichungen zwischen zwei gelesenen Switches
// (Speed, Duplex, VLANs, Port-Channel); Geräte, Ports der Switches und VLANs. Seit 0.63.0 Endpunkte aus
// MAC-Tabelle und ARP: „Wo hängt …“ und die MACs je Port (ohne Uplinks). Seit 0.64.0 der Plan (L2-Sicht):
// Geräte nach Ebenen, VLAN-Wege mit Inseln, Ausfall eines Geräts bzw. Kabels simulieren. Rein lesend.
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  CablingDevice,
  CablingEnd,
  CablingEndpoint,
  CablingLink,
  CablingPort,
  CablingSummary,
  CablingVlan,
  EndpointLocation,
  fetchCablingDevices,
  fetchCablingEndpoints,
  fetchCablingLinks,
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
import { Dash, Empty, ErrorBox, fmtTime, Loading, Note, PageHead } from "./ui";

const PAGE = 200;
type Tab = "plan" | "links" | "devices" | "ports" | "endpoints" | "vlans";
const TABS: { key: Tab; label: string }[] = [
  { key: "links", label: "Verbindungen" },
  { key: "plan", label: "Plan" },
  { key: "devices", label: "Geräte" },
  { key: "ports", label: "Ports" },
  { key: "endpoints", label: "Endpunkte" },
  { key: "vlans", label: "VLANs" },
];
const STATE: Record<LinkState, [string, string, string]> = {
  both: ["ok", "beidseitig", "beide Geräte melden die Verbindung per CDP/LLDP"],
  one_sided: ["warn", "einseitig", "das Gerät gegenüber wird gelesen, meldet die Verbindung aber nicht"],
  unread: ["na", "Gegenstelle ohne CDP/LLDP", "vom Gerät gegenüber liegen keine Nachbar-Daten vor (nicht gelesen, Firewall, Server)"],
};
const KIND: Record<string, string> = {
  switch: "Switch",
  leaf: "ACI-Leaf",
  spine: "ACI-Spine",
  controller: "APIC",
  firewall: "Firewall",
  esxi: "ESXi-Host",
  unknown: "nicht gelesen",
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
  if (e.mode === "tagged") parts.push(`Trunk ${e.trunk_vlans ?? "?"}${e.native_vlan ? ` (nativ ${e.native_vlan})` : ""}`);
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
      <PageHead title="Verkabelung">
        Verbindungen zwischen Geräten aus CDP/LLDP von {tenantLabel}: Switches (Cisco IOS, IOS-XE, NX-OS, per SSH gelesen),
        ACI-Leafs und weitere Geräte. Beidseitig = beide Seiten melden die Verbindung; Abweichungen (Speed, Duplex, VLANs,
        Port-Channel) zwischen zwei gelesenen Switches sind Befunde. Rein lesend.
      </PageHead>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {data === null ? (
        <Loading />
      ) : s && s.links === 0 && s.switches === 0 ? (
        <Note>
          Noch keine Verkabelung. Unter „Mandanten &amp; Standorte“ ein Quellsystem „Cisco-Switches“ mit den Adressen der
          Switches und einem Konto nur mit Leserechten anlegen und einlesen; ACI-Leafs bringen ihre LLDP-Nachbarn mit.
        </Note>
      ) : (
        s && (
          <p className="faint" id="cabling-summary">
            {s.switches} Switches · {s.devices} gelesene Geräte · {s.links} Verbindungen ({s.states.both} beidseitig,{" "}
            {s.states.one_sided} einseitig, {s.states.unread} Gegenstelle ohne CDP/LLDP) ·{" "}
            <span className={s.issues ? "crit" : ""}>{s.issues} Abweichungen</span>
            {s.endpoints ? <> · {s.endpoints} Endpunkte (MACs an den Ports)</> : null}
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
      {tenantId !== null && tab === "plan" && <PlanTab tenantId={tenantId} />}
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
    <select className="sel" aria-label="Gerät" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">alle Geräte</option>
      {devices
        .filter((d) => !readOnly || d.kind === "switch")
        .map((d) => (
          <option key={d.key} value={d.key}>
            {d.name}
            {d.read ? "" : " (nicht gelesen)"}
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
        <h2>Verbindungen</h2>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <DeviceSelect devices={devices} value={device} onChange={onDevice} />
          <select className="sel" aria-label="Zustand" value={state} onChange={(e) => setState(e.target.value as LinkState | "")}>
            <option value="">alle Zustände</option>
            {(Object.keys(STATE) as LinkState[]).map((k) => (
              <option key={k} value={k}>
                {STATE[k][1]}
              </option>
            ))}
          </select>
          <label className="check">
            <input type="checkbox" checked={issues} onChange={(e) => setIssues(e.target.checked)} /> nur Abweichungen
          </label>
          <input className="input" type="search" aria-label="Suchen" placeholder="Gerät, Port, Beschreibung" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
      </div>
      <div className="card-body scroll">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {data === null ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <Empty title="keine Verbindungen" />
        ) : (
          <table className="compact">
            <thead>
              <tr>
                <th>Gerät</th>
                <th>Port</th>
                <th />
                <th>Gegenstelle</th>
                <th>Port</th>
                <th>Zustand</th>
                <th>Abweichung / Konfiguration</th>
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
  if (items.length === 0) return <Empty title="keine Geräte" />;
  return (
    <div className="card" id="cabling-devices">
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>Gerät</th>
              <th>Art</th>
              <th>Quelle</th>
              <th>Modell / Version</th>
              <th>Ports</th>
              <th>Verbindungen</th>
              <th>Abweichungen</th>
            </tr>
          </thead>
          <tbody>
            {items.map((d) => (
              <tr key={d.key} className="clickable" onClick={() => onOpen(d.key, d.kind === "switch" ? "ports" : "links")}>
                <td className="nowrap">
                  <b>{d.name}</b> {d.mgmt_ip && <span className="faint mono">{d.mgmt_ip}</span>}
                </td>
                <td>{KIND[d.kind] ?? d.kind}</td>
                <td className="faint">{d.source ?? (d.read ? "" : "nur von Nachbarn gemeldet")}</td>
                <td>
                  {d.model ?? <Dash />} {d.version && <span className="faint">· {d.version}</span>}
                </td>
                <td>{d.read && d.ports ? `${d.connected} von ${d.ports} verbunden` : <Dash />}</td>
                <td>
                  {d.links}
                  {d.one_sided ? <span className="faint"> ({d.one_sided} einseitig)</span> : ""}
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
        <h2>Ports der Switches</h2>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <DeviceSelect devices={devices} value={device} onChange={onDevice} readOnly />
          <select className="sel" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">alle Status</option>
            <option value="up">verbunden</option>
            <option value="down">nicht verbunden</option>
            <option value="disabled">abgeschaltet</option>
          </select>
          <input className="input" style={{ width: 90 }} aria-label="VLAN" placeholder="VLAN" value={vlan} onChange={(e) => setVlan(e.target.value)} />
          <input className="input" type="search" aria-label="Suchen" placeholder="Port, Beschreibung, Nachbar" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
      </div>
      <div className="card-body scroll">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {data === null ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <Empty title="keine Ports" />
        ) : (
          <table className="compact">
            <thead>
              <tr>
                <th>Switch</th>
                <th>Port</th>
                <th>Status</th>
                <th>Konfiguration</th>
                <th>Beschreibung</th>
                <th>Gegenstelle</th>
                <th title="MACs am Port (ohne Uplinks)">Endpunkte</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((p) => (
                <tr key={`${p.device}|${p.name}`} data-port={p.name}>
                  <td className="nowrap">{p.device}</td>
                  <td className="mono nowrap">{p.name}</td>
                  <td className="nowrap">
                    <span className={`chip ${p.status === "connected" || p.status === "up" ? "ok" : p.enabled === false ? "na" : ""}`}>
                      {p.enabled === false ? "abgeschaltet" : (p.status ?? "?")}
                    </span>
                  </td>
                  <td className="faint">{portText(p) || <Dash />}</td>
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
                      <button className="chip linkchip" onClick={() => onEndpoints(p.device, p.name)} title="Endpunkte am Port">
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

// Wer ist das: VM (mit Host), sonst IPAM-Namen, IPs, EPG
function Who({ e }: { e: { vm: CablingEndpoint["vm"]; names: string[]; ips: string[]; epgs: string[] } }) {
  return (
    <>
      {e.vm ? (
        <>
          <b>{e.vm.name}</b>{" "}
          <span className="faint">
            VM{e.vm.host ? ` auf ${e.vm.host}` : ""}
            {e.vm.portgroup ? ` · ${e.vm.portgroup}` : ""}
          </span>
        </>
      ) : e.names.length ? (
        <b>{e.names.join(", ")}</b>
      ) : (
        <span className="faint">unbekannt</span>
      )}
      {e.ips.length > 0 && <span className="mono faint"> · {e.ips.join(", ")}</span>}
      {e.epgs.length > 0 && <span className="faint"> · EPG {e.epgs.join(", ")}</span>}
    </>
  );
}

function at(e: CablingEndpoint): string {
  const via = e.via ? ` · über ${e.via.device}${e.via.port ? ` (${e.via.port})` : ""}${KIND[e.via.kind] && !e.via.read ? ` – ${KIND[e.via.kind]}` : ""}` : "";
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
        <h2>Wo hängt …</h2>
        <form onSubmit={run} style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
          <input className="input" type="search" aria-label="Wo hängt" placeholder="MAC, IP oder Name (VM, DNS)" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 280 }} />
          <button className="btn btn-sm" type="submit" disabled={busy || q.trim().length < 2}>
            {busy ? "Suche…" : "Suchen"}
          </button>
        </form>
      </div>
      <div className="card-body pad">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {res === null ? (
          <span className="faint">
            Sucht die MAC über das vCenter (VM), ARP der Switches, ACI-Endpunkte bzw. das IPAM und zeigt den Port, an dem sie
            gelernt ist – nicht die Uplinks zwischen den Switches.
          </span>
        ) : res.items.length === 0 ? (
          <Empty title={`keine MAC zu „${res.query}“ in den MAC-Tabellen`} />
        ) : (
          res.items.map((it) => (
            <div key={it.mac} className="locate-hit" data-mac={it.mac} style={{ marginBottom: 12 }}>
              <div>
                <span className="mono">{it.mac}</span> <span className="chip">{it.reason}</span> <Who e={it} />
              </div>
              {it.locations.map((l) => (
                <div key={`${l.device}|${l.port}|${l.vlan}`} className="locate-at">
                  ▸ <b>{at(l)}</b> <span className="faint">· zuletzt {fmtTime(l.last_seen)}</span>
                  {l.port_description && <span className="faint"> · „{l.port_description}“</span>}
                </div>
              ))}
              {it.locations.length === 0 && it.behind.length > 0 && (
                <div className="locate-behind faint">
                  nur auf Uplinks gesehen – dahinter:{" "}
                  {it.behind.map((l) => (
                    <span key={`${l.device}|${l.port}`}>
                      {l.via ? `${l.via.device} (${KIND[l.via.kind] ?? l.via.kind})` : "?"} an {l.device} {l.port}{" "}
                    </span>
                  ))}
                </div>
              )}
              {it.locations.length === 0 && it.behind.length === 0 && <div className="faint">zurzeit in keiner MAC-Tabelle</div>}
              {it.earlier.length > 0 && (
                <div className="locate-earlier faint">
                  früher: {it.earlier.map((l) => `${l.device} ${l.port} (${fmtTime(l.last_seen)})`).join(", ")}
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
          <h2>Endpunkte an den Ports</h2>
          {port && (
            <span className="chip" id="cabling-endpoints-port">
              {port.device} {port.port}{" "}
              <button className="linkchip" style={{ border: 0, background: "none", padding: 0 }} onClick={onClearAt} aria-label="Port-Filter entfernen">
                ✕
              </button>
            </span>
          )}
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {!port && <DeviceSelect devices={devices} value={device} onChange={setDevice} readOnly />}
            <input className="input" style={{ width: 90 }} aria-label="VLAN" placeholder="VLAN" value={vlan} onChange={(e) => setVlan(e.target.value)} />
            <input className="input" type="search" aria-label="Suchen" placeholder="MAC, VM, IP, Port" value={q} onChange={(e) => setQ(e.target.value)} />
          </span>
        </div>
        <div className="card-body scroll">
          <ErrorBox error={err} onClose={() => setErr(null)} />
          {data === null ? (
            <Loading />
          ) : data.items.length === 0 ? (
            <Empty title="keine Endpunkte" />
          ) : (
            <>
              <p className="faint" id="cabling-endpoints-summary">
                {data.summary.macs} MACs an {data.summary.ports} Ports · {data.summary.vms} VMs · {data.summary.with_ip} mit IP
              </p>
              <table className="compact">
                <thead>
                  <tr>
                    <th>Switch</th>
                    <th>Port</th>
                    <th>VLAN</th>
                    <th>MAC</th>
                    <th>Gerät</th>
                    <th>über</th>
                    <th>zuletzt</th>
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
  if (items.length === 0) return <Empty title="keine VLANs auf den Switches" />;
  return (
    <div className="card" id="cabling-vlans">
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>VLAN</th>
              <th>Name</th>
              <th>Switches (Access-Ports · Trunks)</th>
            </tr>
          </thead>
          <tbody>
            {items.map((v) => (
              <tr key={v.vid} data-vlan={v.vid}>
                <td className="mono">{v.vid}</td>
                <td>{v.names.join(" / ") || <Dash />}</td>
                <td>
                  {v.devices.length === 0 ? (
                    <span className="faint">an keinem Port</span>
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
// eines Geräts (Klick) bzw. Kabels (Klick auf die Linie) simulieren - getrennte Geräte blass.
const NODE_W = 160;
const NODE_H = 44;
const VLAN_EDGE: Record<string, [string, string]> = {
  yes: ["var(--good)", "lässt das VLAN durch"],
  no: ["var(--crit)", "sperrt das VLAN"],
  partial: ["var(--warn)", "nur ein Teil der Kabel lässt das VLAN durch"],
  unknown: ["var(--faint)", "Konfiguration unbekannt"],
};

function edgeColor(e: L2PlanEdge): string {
  if (e.vlan) return VLAN_EDGE[e.vlan][0];
  if (e.issues) return "var(--crit)";
  if (e.state === "one_sided") return "var(--warn)";
  return "var(--faint)";
}

function PlanTab({ tenantId }: { tenantId: number }) {
  const [ends, setEnds] = useState(true);
  const [vlan, setVlan] = useState("");
  const vlanNo = useDebounced(vlan);
  const [plan, setPlan] = useState<L2Plan | null>(null);
  const [fail, setFail] = useState<L2Failure | null>(null);
  const [failed, setFailed] = useState<{ device?: string; edge?: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    const v = /^\d+$/.test(vlanNo) ? Number(vlanNo) : null;
    setPlan(null);
    fetchL2Plan(tenantId, { ends, vlan: v })
      .then((d) => live && setPlan(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, ends, vlanNo]);
  const byKey = useMemo(() => new Map((plan?.nodes ?? []).map((n) => [n.key, n])), [plan]);
  const main = useMemo(() => new Set(fail?.main ?? []), [fail]);
  const simulate = (f: { device?: string; link?: string; edge?: string }) => {
    setErr(null);
    setFailed({ device: f.device, edge: f.edge });
    fetchL2Failure(tenantId, { device: f.device, link: f.link }).then(setFail).catch(setErr);
  };
  const reset = () => {
    setFail(null);
    setFailed(null);
  };
  const dim = (name: string) => fail !== null && !main.has(name) && name !== failed?.device;
  return (
    <div className="card" id="cabling-plan">
      <div className="card-head">
        <h2>Plan</h2>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <label className="check">
            <input type="checkbox" checked={ends} onChange={(e) => setEnds(e.target.checked)} /> Endgeräte
          </label>
          <input className="input" style={{ width: 110 }} aria-label="VLAN hervorheben" placeholder="VLAN" value={vlan} onChange={(e) => setVlan(e.target.value)} />
          {fail && (
            <button className="btn btn-sm btn-ghost" onClick={reset}>
              Ausfall zurücksetzen
            </button>
          )}
        </span>
      </div>
      <div className="card-body pad">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        <p className="faint" style={{ marginTop: 0 }}>
          Kern oben (meistvernetzte Switches bzw. Spines), darunter je Abstand die weiteren Switches, Endgeräte unter ihrem
          obersten Nachbarn. Klick auf ein Gerät bzw. ein Kabel simuliert dessen Ausfall.
        </p>
        {fail && (
          <div className="note" id="cabling-failure">
            {failed?.device ? <>Fällt <b>{failed.device}</b> aus</> : <>Fällt das Kabel <b>{failed?.edge}</b> aus</>}
            {fail.cut_off.length === 0 ? (
              <>: nichts getrennt – es gibt einen anderen Weg.</>
            ) : (
              <>
                , ist getrennt: {fail.cut_off.map((p) => p.join(", ")).join(" | ")}
                {fail.parts.length > 2 && <span className="faint"> ({fail.parts.length} Teile)</span>}
              </>
            )}
          </div>
        )}
        {plan?.vlan && (
          <div className={`note ${plan.vlan.islands.length > 1 ? "warn" : ""}`} id="cabling-vlan-islands">
            VLAN {plan.vlan.vid}:{" "}
            {plan.vlan.present.length === 0
              ? "an keinem Access-Port bzw. SVI der gelesenen Switches"
              : plan.vlan.islands.length > 1
                ? `getrennt in ${plan.vlan.islands.length} Inseln – ${plan.vlan.islands.map((i) => i.join(", ")).join(" | ")}`
                : `durchgehend (${plan.vlan.present.join(", ")})`}
          </div>
        )}
        {plan === null ? (
          <Loading />
        ) : plan.nodes.length === 0 ? (
          <Empty title="keine Geräte mit Verbindungen" />
        ) : (
          <div style={{ overflow: "auto", maxHeight: "70vh" }}>
            <svg width={Math.max(plan.width, 400)} height={plan.height} id="cabling-plan-svg" role="img" aria-label="Plan der Verkabelung">
              {plan.edges.map((e) => {
                const a = byKey.get(e.a);
                const b = byKey.get(e.b);
                if (!a || !b) return null;
                const label = `${a.name} ↔ ${b.name}`;
                const off = dim(a.name) || dim(b.name);
                const isFailed = failed?.edge === label;
                const title = `${label}: ${e.links} Kabel${e.lags.length ? ` (${e.lags.join(", ")})` : ""}${e.vlan ? ` – ${VLAN_EDGE[e.vlan][1]}` : ""}${e.issues ? ` – ${e.issues} Abweichungen` : ""}`;
                return (
                  <g
                    key={`${e.a}~${e.b}`}
                    className="plan-edge clickable"
                    data-edge={label}
                    opacity={off ? 0.25 : 1}
                    onClick={() => {
                      if (e.links === 1) {
                        simulate({ link: e.ids[0], edge: label });
                      } else {
                        setFail(null);
                        setFailed({ edge: label });
                      }
                    }}
                  >
                    <title>{title}</title>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="transparent" strokeWidth={12} />
                    <line
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke={isFailed ? "var(--crit)" : edgeColor(e)}
                      strokeWidth={1 + Math.min(e.links, 4)}
                      strokeDasharray={e.vlan === "no" || isFailed ? "6 4" : e.vlan === "unknown" ? "2 4" : undefined}
                    />
                    {e.links > 1 && (
                      <text x={(a.x + b.x) / 2 + 6} y={(a.y + b.y) / 2 - 4} className="plan-edge-label" fontSize={11} fill="var(--muted)">
                        {e.links}×
                      </text>
                    )}
                  </g>
                );
              })}
              {plan.nodes.map((n) => {
                const off = dim(n.name);
                const isFailed = failed?.device === n.name;
                return (
                  <g
                    key={n.key}
                    className="plan-node clickable"
                    data-node={n.name}
                    transform={`translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})`}
                    opacity={off ? 0.3 : 1}
                    onClick={() => simulate({ device: n.name })}
                  >
                    <title>{`${n.name} – ${KIND[n.kind] ?? n.kind}${n.source ? ` (${n.source})` : ""}`}</title>
                    <rect
                      width={NODE_W}
                      height={NODE_H}
                      rx={6}
                      fill={n.switching ? "var(--surface-2)" : "var(--surface)"}
                      stroke={isFailed ? "var(--crit)" : n.vlan_present ? "var(--good)" : n.switching ? "var(--accent)" : "var(--border-strong)"}
                      strokeWidth={isFailed || n.vlan_present ? 3 : 1.5}
                      strokeDasharray={n.read ? undefined : "4 3"}
                    />
                    <text x={NODE_W / 2} y={19} textAnchor="middle" fontSize={12} fontWeight={600} fill="var(--fg)">
                      {n.name.length > 22 ? `${n.name.slice(0, 21)}…` : n.name}
                    </text>
                    <text x={NODE_W / 2} y={35} textAnchor="middle" fontSize={10} fill="var(--muted)">
                      {isFailed ? "fällt aus" : (KIND[n.kind] ?? n.kind)}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        )}
        {failed?.edge && !fail && (
          <p className="faint" id="cabling-failure-multi">
            {failed.edge}: mehrere Kabel – fällt eines aus, bleiben die anderen.
          </p>
        )}
      </div>
    </div>
  );
}
