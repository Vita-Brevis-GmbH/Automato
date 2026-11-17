// Spanning Tree (1.19.0, Anwender 2026-11-17 Priorität 5 „Spanningtree“): je VLAN bzw. MST-Instanz und Standort die
// Topologie – Root, Switches mit Root-Port und Kosten, Ports mit Rolle und Zustand, blockierte Ports hervorgehoben (Reserve
// bzw. unerwartet) – als Liste und einfacher Baum; Soll-Root je VLAN-Bereich bzw. Standort (Laufzeitdaten, operator) und die
// Schwelle der Topologieänderungen; Switches mit Modus, err-disabled (BPDU-Guard) und Topologieänderungen. Gelesen werden
// Cisco IOS/IOS-XE und NX-OS; andere Plattformen werden gezählt (keine stille Grenze). Rein lesend gegenüber den Geräten.
import { useEffect, useMemo, useState } from "react";
import type { ViewParams } from "../types";
import { useDebounced } from "../../Objects";
import { L } from "../../i18n";
import { Dash, Empty, ErrorBox, fmtTime, Loading, Note, PageHead } from "../../ui";
import {
  deleteStpTarget,
  fetchStpOverview,
  fetchStpTree,
  fetchStpTrees,
  saveStpSettings,
  saveStpTarget,
  StpDeviceRow,
  StpEdge,
  StpOverview,
  StpPort,
  StpStatus,
  StpTarget,
  StpTargetState,
  StpTree,
  StpTreeBrief,
} from "./stpApi";

type Tab = "trees" | "targets" | "devices";
const TABS: { key: Tab; label: string }[] = [
  { key: "trees", label: L("Topologie", "Topology") },
  { key: "targets", label: L("Soll-Root", "Target root") },
  { key: "devices", label: "Switches" },
];

const STATUS: Record<StpStatus, [string, string, string]> = {
  read: ["ok", L("gelesen", "read"), L("Spanning Tree gelesen", "Spanning Tree read")],
  off: ["na", L("aus", "off"), L("Spanning Tree auf dem Switch aus (keine Instanz)", "Spanning Tree off on the switch (no instance)")],
  unsupported: [
    "warn",
    L("nicht unterstützt", "not supported"),
    L(
      "Plattform ohne Spanning Tree in Automato (gelesen werden Cisco IOS/IOS-XE und NX-OS)",
      "platform without Spanning Tree in Automato (Cisco IOS/IOS-XE and NX-OS are read)",
    ),
  ],
  unreadable: ["crit", L("nicht lesbar", "not readable"), L("Befehl abgelehnt bzw. nicht gelesen", "command refused or not read")],
};
const MODE: Record<string, string> = { pvst: "PVST+", "rapid-pvst": "Rapid-PVST+", mst: "MST" };
const ROLE: Record<string, string> = {
  root: "Root",
  designated: "Designated",
  alternate: L("Alternate (Reserve)", "Alternate (backup path)"),
  backup: "Backup",
  master: "Master",
  disabled: L("aus", "disabled"),
};
const STATE: Record<string, string> = {
  forwarding: L("leitet weiter", "forwarding"),
  blocking: L("blockiert", "blocking"),
  discarding: L("verwirft", "discarding"),
  broken: L("gesperrt", "broken"),
  learning: L("lernt", "learning"),
  listening: L("hört", "listening"),
  disabled: L("aus", "disabled"),
};
export const STP_REASON: Record<string, string> = {
  guard: L("Schutzfunktion (Root/Loop Guard, Bridge Assurance)", "protection feature (root/loop guard, bridge assurance)"),
  backup: L("Backup-Rolle (eigene BPDUs)", "backup role (own BPDUs)"),
  peer_link: L("vPC-Peer-Link", "vPC peer link"),
  edge: L("Edge-Port (PortFast)", "edge port (PortFast)"),
  no_bridge: L("Gegenstelle kein Switch", "peer not a switch"),
};
const KIND: Record<string, string> = {
  switch: "Switch",
  firewall: "Firewall",
  esxi: L("ESXi-Host", "ESXi host"),
  router: "Router",
  phone: L("Telefon", "phone"),
  host: L("Server/Endgerät", "server/end device"),
  controller: "Controller",
};
const TARGET: Record<StpTargetState, [string, string]> = {
  ok: ["ok", L("am Soll", "at target")],
  backup: ["info", L("Ersatz-Root", "backup root")],
  mismatch: ["warn", L("nicht am Soll", "not at target")],
  none: ["na", L("ohne Soll", "no target")],
};

const kindText = (k: string | null) => (k ? (KIND[k] ?? k) : L("unbekannt", "unknown"));
const modeText = (m: string | null) => (m ? m.split("/").map((x) => MODE[x] ?? x).join(" / ") : "–");

function setHash(params: Record<string, string | null>) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const s = q.toString();
  window.history.replaceState(null, "", `#spanningtree${s ? `?${s}` : ""}`);
}

export default function SpanningTree({
  tenantId,
  tenantLabel,
  canEdit,
  params,
  onOpenFindings,
}: {
  tenantId: number | null;
  tenantLabel: string;
  canEdit: boolean;
  params: ViewParams;
  onOpenFindings: (type: string) => void;
}) {
  const [tab, setTab] = useState<Tab>(TABS.some((t) => t.key === params.tab) ? (params.tab as Tab) : "trees");
  const [data, setData] = useState<StpOverview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [stamp, setStamp] = useState(0);

  useEffect(() => {
    if (tenantId === null) return;
    let live = true;
    setError(null);
    fetchStpOverview(tenantId)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [tenantId, stamp]);

  const choose = (t: Tab) => {
    setTab(t);
    setHash({ tab: t === "trees" ? null : t });
  };

  if (tenantId === null) {
    return (
      <>
        <PageHead title="Spanning Tree" />
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      </>
    );
  }
  const s = data?.summary;
  return (
    <>
      <PageHead title="Spanning Tree">
        {L(
          <>
            Spanning Tree der Switches von „{tenantLabel}“ je VLAN bzw. MST-Instanz: Root, Weg zur Root, Ports mit Rolle und
            Zustand, blockierte Ports (Reserve zwischen Switches bzw. unerwartet), Modus, err-disabled durch BPDU-Guard und
            Topologieänderungen. Gelesen werden Cisco IOS/IOS-XE und NX-OS; den Soll-Root je VLAN-Bereich bzw. Standort legt
            ihr hier fest. Rein lesend gegenüber den Geräten.
          </>,
          <>
            Spanning Tree of the switches of “{tenantLabel}” per VLAN or MST instance: root, path to the root, ports with role
            and state, blocked ports (backup path between switches or unexpected), mode, err-disabled by BPDU guard and topology
            changes. Cisco IOS/IOS-XE and NX-OS are read; you define the target root per VLAN range or site here. Read-only
            towards the devices.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("Spanning Tree laden", "Load Spanning Tree")} />
      {data === null || !s ? (
        error ? null : <Loading />
      ) : (
        <>
          <div className="kpis" id="stp-kpis">
            <div className="kpi">
              <div className="k-label">Switches</div>
              <div className="k-val">
                {s.status.read}
                <span className="faint" style={{ fontSize: 16 }}> / {s.switches}</span>
              </div>
              <div className="k-sub">
                {s.status.unsupported + s.status.unreadable > 0
                  ? L(
                      `${s.status.unsupported} nicht unterstützt · ${s.status.unreadable} nicht lesbar`,
                      `${s.status.unsupported} not supported · ${s.status.unreadable} not readable`,
                    )
                  : L("mit Spanning Tree gelesen", "read with Spanning Tree")}
              </div>
            </div>
            <button className="kpi" onClick={() => onOpenFindings("stp_root_mismatch")} title={L("In Findings öffnen", "Open in findings")}>
              <div className="k-label">{L("Root am Soll", "Root at target")}</div>
              <div className={`k-val ${!s.targets ? "" : s.target_states.mismatch ? "warn" : "ok"}`}>
                {s.targets ? `${s.target_states.ok + s.target_states.backup} / ${s.trees}` : "–"}
              </div>
              <div className="k-sub">
                {s.targets
                  ? L(
                      `${s.target_states.mismatch} nicht am Soll · ${s.target_states.none} ohne Soll`,
                      `${s.target_states.mismatch} not at target · ${s.target_states.none} without target`,
                    )
                  : L("ohne Soll – nur gemeldet", "no target – only reported")}
              </div>
            </button>
            <button className="kpi" onClick={() => onOpenFindings("stp_port_blocked")} title={L("In Findings öffnen", "Open in findings")}>
              <div className="k-label">{L("Blockierte Ports", "Blocked ports")}</div>
              <div className={`k-val ${s.unexpected ? "warn" : "ok"}`}>{s.blocked}</div>
              <div className="k-sub">{L(`${s.unexpected} unerwartet`, `${s.unexpected} unexpected`)}</div>
            </button>
            <button className="kpi" onClick={() => onOpenFindings("stp_bpdu_guard")} title={L("In Findings öffnen", "Open in findings")}>
              <div className="k-label">err-disabled</div>
              <div className={`k-val ${s.bpdu_guard ? "warn" : "ok"}`}>{s.errdisabled}</div>
              <div className="k-sub">{L(`${s.bpdu_guard} durch BPDU-Guard`, `${s.bpdu_guard} by BPDU guard`)}</div>
            </button>
          </div>
          {s.switches === 0 && (
            <Note>
              {L(
                "Noch kein Spanning Tree gelesen. Seit 1.19.0 liest jeder Lauf der Switches (Cisco IOS/IOS-XE, NX-OS) den Spanning Tree mit – Switches neu einlesen.",
                "No Spanning Tree read yet. Since 1.19.0 every run of the switches (Cisco IOS/IOS-XE, NX-OS) also reads the Spanning Tree – collect the switches again.",
              )}
            </Note>
          )}
          {(s.unsupported.length > 0 || s.unreadable.length > 0) && (
            <Note warn>
              <span id="stp-not-read">
                {s.unsupported.length > 0 &&
                  L(
                    `Ohne Spanning Tree (Plattform nicht unterstützt): ${s.unsupported.join(", ")}. `,
                    `Without Spanning Tree (platform not supported): ${s.unsupported.join(", ")}. `,
                  )}
                {s.unreadable.length > 0 &&
                  L(`Nicht lesbar: ${s.unreadable.join(", ")}.`, `Not readable: ${s.unreadable.join(", ")}.`)}
              </span>
            </Note>
          )}
        </>
      )}
      <div className="tabbar sticky">
        {TABS.map((t) => (
          <button key={t.key} id={`stp-tab-${t.key}`} className={tab === t.key ? "on" : ""} onClick={() => choose(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "trees" && data && <TreesTab tenantId={tenantId} data={data} params={params} stamp={stamp} />}
      {tab === "targets" && data && (
        <TargetsTab tenantId={tenantId} data={data} canEdit={canEdit} onChanged={() => setStamp((x) => x + 1)} />
      )}
      {tab === "devices" && data && <DevicesTab devices={data.devices} />}
    </>
  );
}

// -- Topologie ----------------------------------------------------------------------------------------------------------

function TargetChip({ state, target }: { state: StpTargetState; target: StpTarget | null }) {
  const [cls, label] = TARGET[state];
  const title = target ? L(`Soll ${target.root}${target.backup ? ` (Ersatz ${target.backup})` : ""}`, `target ${target.root}${target.backup ? ` (backup ${target.backup})` : ""}`) : undefined;
  return (
    <span className={`chip ${cls}`} title={title}>
      {label}
    </span>
  );
}

function TreesTab({ tenantId, data, params, stamp }: { tenantId: number; data: StpOverview; params: ViewParams; stamp: number }) {
  const [vlan, setVlan] = useState(params.vlan ?? "");
  const [site, setSite] = useState(params.site ?? "");
  const [q, setQ] = useState(params.q ?? "");
  const [only, setOnly] = useState(params.only === "problems");
  const [rows, setRows] = useState<StpTreeBrief[] | null>(null);
  const [selected, setSelected] = useState<string | null>(params.tree ?? null);
  const [error, setError] = useState<unknown>(null);
  const query = useDebounced(q);
  const vlanNo = useDebounced(vlan);

  useEffect(() => {
    let live = true;
    setRows(null);
    const v = /^\d+$/.test(vlanNo.trim()) ? Number(vlanNo.trim()) : null;
    fetchStpTrees(tenantId, { vlan: v, site_id: site ? Number(site) : null, q: query, only: only ? "problems" : undefined })
      .then((r) => {
        if (!live) return;
        setRows(r);
        setSelected((cur) => (cur && r.some((t) => t.key === cur) ? cur : r.length === 1 ? r[0].key : cur));
      })
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [tenantId, vlanNo, site, query, only, stamp]);

  const pick = (key: string) => {
    setSelected(key);
    setHash({ tree: key });
  };

  return (
    <>
      <div className="card" id="stp-trees">
        <div className="card-head">
          <h2>{L("Bäume je VLAN bzw. MST-Instanz", "Trees per VLAN or MST instance")}</h2>
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              id="stp-filter-vlan"
              className="input input-inline"
              style={{ width: 90 }}
              inputMode="numeric"
              aria-label="VLAN"
              placeholder="VLAN"
              value={vlan}
              onChange={(e) => setVlan(e.target.value)}
            />
            <select id="stp-filter-site" className="sel" aria-label={L("Standort", "Site")} value={site} onChange={(e) => setSite(e.target.value)}>
              <option value="">{L("alle Standorte", "all sites")}</option>
              {data.sites.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.code} · {x.name}
                </option>
              ))}
            </select>
            <label className="check">
              <input id="stp-filter-only" type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} />{" "}
              {L("nur Auffälliges", "issues only")}
            </label>
            <input
              className="input input-inline"
              type="search"
              aria-label={L("Suchen", "Search")}
              placeholder={L("Switch, Root, MAC", "Switch, root, MAC")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </span>
        </div>
        <div className="card-body scroll" style={{ maxHeight: 360 }}>
          <ErrorBox error={error} onClose={() => setError(null)} />
          {rows === null ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title={L("keine Bäume", "no trees")} />
          ) : (
            <table className="compact">
              <thead>
                <tr>
                  <th>VLAN / MST</th>
                  <th>Root</th>
                  <th>{L("Soll", "Target")}</th>
                  <th>{L("Standort", "Site")}</th>
                  <th className="num">Switches</th>
                  <th className="num">{L("blockiert", "blocked")}</th>
                  <th className="num">{L("unerwartet", "unexpected")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr
                    key={t.key}
                    data-tree={t.key}
                    className={`clickable${selected === t.key ? " selected" : ""}`}
                    onClick={() => pick(t.key)}
                  >
                    <td className="nowrap">
                      <b>{t.label}</b>
                      {t.kind === "mst" && <span className="faint"> · VLAN {t.vlans}</span>}
                    </td>
                    <td className="nowrap">
                      {t.root.label}
                      {!t.root.read && (
                        <span className="chip na" title={L("Root-Bridge wird nicht gelesen", "root bridge is not read")}>
                          {L("nicht gelesen", "not read")}
                        </span>
                      )}
                      {t.root.priority != null && <span className="faint mono"> · {t.root.priority}</span>}
                    </td>
                    <td className="nowrap">
                      <TargetChip state={t.target_state} target={t.target} />
                    </td>
                    <td className="faint">{t.sites.join(", ")}</td>
                    <td className="num mono">{t.switches}</td>
                    <td className="num mono">{t.blocked || ""}</td>
                    <td className={`num mono${t.unexpected ? " crit" : ""}`}>{t.unexpected || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {selected && <TreeDetail key={`${selected}-${stamp}`} tenantId={tenantId} treeKey={selected} />}
    </>
  );
}

function TreeDetail({ tenantId, treeKey }: { tenantId: number; treeKey: string }) {
  const [tree, setTree] = useState<StpTree | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    fetchStpTree(tenantId, treeKey)
      .then((t) => live && setTree(t))
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [tenantId, treeKey]);
  if (error) return <ErrorBox error={error} prefix={L("Baum laden", "Load tree")} />;
  if (!tree) return <Loading />;
  const ports = tree.members.flatMap((m) => m.ports.map((p) => ({ m, p })));
  return (
    <div className="card" id="stp-tree" data-tree={tree.key} style={{ marginTop: 16 }}>
      <div className="card-head">
        <h2>
          {tree.label}
          {tree.kind === "mst" && <span className="faint"> · VLAN {tree.vlans}</span>}
        </h2>
        <span>
          Root <b>{tree.root.label}</b>
          {tree.root.mac && <span className="faint mono"> ({tree.root.mac})</span>}
        </span>
        <TargetChip state={tree.target_state} target={tree.target} />
        {tree.target && (
          <span className="faint">
            {L("Soll", "Target")} {tree.target.root}
            {tree.target.backup && L(` · Ersatz ${tree.target.backup}`, ` · backup ${tree.target.backup}`)}
          </span>
        )}
      </div>
      <div className="card-body pad">
        <TreeDiagram tree={tree} />
        <table className="compact" id="stp-members" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Switch</th>
              <th>Bridge</th>
              <th>{L("Root-Port", "Root port")}</th>
              <th className="num">{L("Kosten", "Cost")}</th>
              <th>{L("Topologieänderungen", "Topology changes")}</th>
              <th>{L("Standort", "Site")}</th>
            </tr>
          </thead>
          <tbody>
            {tree.members.map((m) => (
              <tr key={m.device} data-device={m.device}>
                <td className="nowrap">
                  <b>{m.device}</b> {m.is_root && <span className="chip ok">Root</span>}
                  {m.protocol && <span className="faint"> · {m.protocol}</span>}
                </td>
                <td className="mono nowrap">
                  {m.bridge_priority ?? "?"} · {m.bridge_mac ?? "?"}
                </td>
                <td className="mono">{m.root_port ?? <Dash />}</td>
                <td className="num mono">{m.root_cost ?? ""}</td>
                <td className="nowrap">
                  {m.tc_count == null ? (
                    <Dash />
                  ) : (
                    <>
                      <span className="mono">{m.tc_count}</span>
                      {m.tc_last_at && <span className="faint"> · {L("zuletzt", "last")} {fmtTime(m.tc_last_at)}</span>}
                      {m.tc_from && <span className="faint"> {L(`von ${m.tc_from}`, `from ${m.tc_from}`)}</span>}
                      {m.tc_window && (
                        <span className="faint">
                          {" "}
                          · {m.tc_window.per_day} {L("je 24 h", "per 24 h")}
                        </span>
                      )}
                    </>
                  )}
                </td>
                <td className="faint">{m.site ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <table className="compact" id="stp-ports" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Switch</th>
              <th>Port</th>
              <th>{L("Rolle", "Role")}</th>
              <th>{L("Zustand", "State")}</th>
              <th className="num">{L("Kosten", "Cost")}</th>
              <th>{L("Typ", "Type")}</th>
              <th>{L("Gegenstelle", "Peer")}</th>
              <th>{L("Beurteilung", "Assessment")}</th>
            </tr>
          </thead>
          <tbody>
            {ports.map(({ m, p }) => (
              <PortRow key={`${m.device}|${p.port}`} device={m.device} p={p} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PortRow({ device, p }: { device: string; p: StpPort }) {
  const bad = p.unexpected.length > 0;
  const cls = bad ? "row-crit" : p.blocked ? "row-warn" : undefined;
  return (
    <tr
      className={cls}
      data-port={`${device}|${p.port}`}
      style={bad ? { background: "var(--crit-weak)" } : p.blocked ? { background: "var(--warn-weak)" } : undefined}
    >
      <td className="nowrap">{device}</td>
      <td className="mono nowrap">{p.port}</td>
      <td className="nowrap">{p.role ? (ROLE[p.role] ?? p.role) : <Dash />}</td>
      <td className="nowrap">
        {p.blocked || p.inconsistent ? (
          <span className={`chip ${bad ? "crit" : "warn"}`}>{p.state ? (STATE[p.state] ?? p.state) : "?"}</span>
        ) : p.state ? (
          STATE[p.state] ?? p.state
        ) : (
          <Dash />
        )}
      </td>
      <td className="num mono">{p.cost ?? ""}</td>
      <td className="faint">
        {p.type}
        {p.inconsistent && <span className="chip crit"> {p.inconsistent}</span>}
      </td>
      <td className="nowrap">
        {p.neighbors.length
          ? p.neighbors.map((n) => (
              <span key={n.device}>
                {n.device}
                {n.port && <span className="faint mono"> {n.port}</span>}
                <span className="faint"> ({kindText(n.kind)})</span>{" "}
              </span>
            ))
          : p.blocked && <span className="faint">{L("Nachbar unbekannt", "neighbor unknown")}</span>}
      </td>
      <td>
        {bad ? (
          <span className="crit stp-unexpected">
            {L("unerwartet: ", "unexpected: ")}
            {p.unexpected.map((r) => STP_REASON[r] ?? r).join(", ")}
          </span>
        ) : p.blocked ? (
          <span className="faint">
            {p.neighbors.length
              ? L("Reserve zwischen Switches (erwartet)", "backup path between switches (expected)")
              : L("ohne bekannte Gegenstelle nicht beurteilt", "not assessed without a known peer")}
          </span>
        ) : null}
      </td>
    </tr>
  );
}

// Beschriftung einer Kante (Port) am Rand des Switches, neben der Linie
function labelAt(a: { x: number; y: number }, b: { x: number; y: number }) {
  const dy = b.y - a.y;
  if (Math.abs(dy) < 1) return { x: a.x + Math.sign(b.x - a.x || 1) * 80, y: a.y - 6 };
  const t = Math.min(26 / Math.abs(dy), 0.5);
  return { x: a.x + (b.x - a.x) * t + 6, y: a.y + dy * t + 4 };
}

// Einfache Darstellung: Root oben, je Switch der Weg über seinen Root-Port zur Gegenstelle; blockierte Ports gestrichelt
// (Reserve gelb, unerwartet rot), Gegenstellen ausserhalb des Baums grau.
function TreeDiagram({ tree }: { tree: StpTree }) {
  const layout = useMemo(() => {
    const members = tree.members.map((m) => m.device);
    const inTree = new Set(members);
    const parent = new Map<string, string>();
    for (const e of tree.edges) if (e.kind === "root" && e.to && e.in_tree && !parent.has(e.from)) parent.set(e.from, e.to);
    const rootNode = tree.members.some((m) => m.is_root) ? null : tree.root.label;
    const level = new Map<string, number>();
    const roots = tree.members.filter((m) => m.is_root).map((m) => m.device);
    if (rootNode) level.set(rootNode, 0);
    roots.forEach((r) => level.set(r, 0));
    const depth = (d: string, seen: Set<string>): number => {
      if (level.has(d)) return level.get(d)!;
      const up = parent.get(d);
      if (!up || seen.has(up)) return 1;
      seen.add(d);
      return depth(up, seen) + 1;
    };
    for (const d of members) level.set(d, depth(d, new Set()));
    const outside: { name: string; kind: string }[] = [];
    const edges: { a: string; b: string; e: StpEdge | null }[] = [];
    for (const e of tree.edges) {
      if (!e.to) continue;
      if (!inTree.has(e.to) && !level.has(e.to)) {
        level.set(e.to, (level.get(e.from) ?? 1) + 1);
        outside.push({ name: e.to, kind: "outside" });
      }
      edges.push({ a: e.from, b: e.to, e });
    }
    if (rootNode) for (const d of members) if (!parent.has(d)) edges.push({ a: d, b: rootNode, e: null });
    const byLevel = new Map<number, string[]>();
    for (const [name, lv] of level) byLevel.set(lv, [...(byLevel.get(lv) ?? []), name]);
    const W = 168;
    const H = 84;
    const widest = Math.max(1, ...[...byLevel.values()].map((v) => v.length));
    const width = Math.max(widest * W + 40, 360);
    const pos = new Map<string, { x: number; y: number }>();
    for (const [lv, names] of byLevel) {
      names.sort();
      const span = names.length * W;
      names.forEach((n, i) => pos.set(n, { x: (width - span) / 2 + i * W + W / 2, y: 30 + lv * H }));
    }
    const height = 30 + Math.max(0, ...[...byLevel.keys()]) * H + 40;
    return { pos, edges, outside: new Set(outside.map((o) => o.name)), width, height, rootNode, roots: new Set(roots) };
  }, [tree]);
  const { pos, edges, width, height } = layout;
  return (
    <svg
      id="stp-diagram"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ maxWidth: "100%", height: "auto", display: "block", margin: "0 auto" }}
      role="img"
      aria-label={L(`Baum ${tree.label}`, `Tree ${tree.label}`)}
    >
      {edges.map(({ a, b, e }, i) => {
        const pa = pos.get(a);
        const pb = pos.get(b);
        if (!pa || !pb) return null;
        const blocked = e?.kind === "blocked";
        const bad = !!e && e.unexpected.length > 0;
        const color = blocked ? (bad ? "var(--crit)" : "var(--warn)") : e ? "var(--accent)" : "var(--faint)";
        return (
          <g key={i} className={`stp-edge ${blocked ? (bad ? "unexpected" : "blocked") : "root"}`}>
            <line x1={pa.x} y1={pa.y} x2={pb.x} y2={pb.y} stroke={color} strokeWidth={blocked ? 2 : 2.5} strokeDasharray={blocked || !e ? "6 4" : undefined} />
            {e && (
              // Port am Rand des Switches, neben der Linie
              <text
                x={labelAt(pa, pb).x}
                y={labelAt(pa, pb).y}
                fontSize={11}
                fill={blocked ? color : "var(--muted)"}
                textAnchor="start"
              >
                {e.port}
                {blocked ? " ✕" : ""}
              </text>
            )}
          </g>
        );
      })}
      {[...pos.entries()].map(([name, p]) => {
        const root = layout.roots.has(name) || name === layout.rootNode;
        const outside = layout.outside.has(name) || name === layout.rootNode;
        return (
          <g key={name} className={`stp-node${root ? " root" : ""}${outside ? " outside" : ""}`} data-node={name}>
            <rect
              x={p.x - 74}
              y={p.y - 15}
              width={148}
              height={30}
              rx={7}
              fill={root ? "var(--accent-weak)" : "var(--surface)"}
              stroke={root ? "var(--accent)" : outside ? "var(--faint)" : "var(--border-strong)"}
              strokeDasharray={outside ? "4 3" : undefined}
            />
            <text x={p.x} y={p.y + 4} fontSize={12} textAnchor="middle" fill={outside ? "var(--muted)" : "var(--fg)"}>
              {name.length > 22 ? `${name.slice(0, 21)}…` : name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// -- Soll-Root ----------------------------------------------------------------------------------------------------------

const EMPTY_FORM = { id: null as number | null, site_id: "", vlans: "", root: "", backup: "", note: "" };

function TargetsTab({ tenantId, data, canEdit, onChanged }: { tenantId: number; data: StpOverview; canEdit: boolean; onChanged: () => void }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<StpTarget | null>(null);
  const [limit, setLimit] = useState(String(data.summary.tc_per_day));
  useEffect(() => setLimit(String(data.summary.tc_per_day)), [data.summary.tc_per_day]);

  const run = (p: Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setError(null);
    p.then(() => {
      after?.();
      onChanged();
    })
      .catch(setError)
      .finally(() => setBusy(false));
  };
  const save = () =>
    run(
      saveStpTarget({
        tenant_id: tenantId,
        id: form.id,
        site_id: form.site_id ? Number(form.site_id) : null,
        vlans: form.vlans.trim() || null,
        root: form.root.trim(),
        backup: form.backup.trim() || null,
        note: form.note.trim() || null,
      }).then((t) => setSaved(t)),
      () => setForm(EMPTY_FORM),
    );
  const edit = (t: StpTarget) => {
    setSaved(null);
    setForm({ id: t.id, site_id: t.site_id ? String(t.site_id) : "", vlans: t.vlans ?? "", root: t.root, backup: t.backup ?? "", note: t.note ?? "" });
  };
  const field = (k: keyof typeof EMPTY_FORM) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <>
      <Note>
        {L(
          "Soll-Root je VLAN-Bereich bzw. Standort (Standort der Switches = Standort ihres Quellsystems). Es gilt der genaueste Eintrag: Standort und VLANs vor nur Standort vor nur VLANs vor allen. Ohne Eintrag meldet die Prüfung nur, wer Root ist (Info); mit Eintrag ist ein anderer Root eine Warnung, der Ersatz-Root ein Hinweis.",
          "Target root per VLAN range or site (site of the switches = site of their source system). The most specific entry applies: site and VLANs before site only before VLANs only before all. Without an entry the check only reports who is root (info); with an entry another root is a warning, the backup root a note.",
        )}
      </Note>
      <ErrorBox error={error} onClose={() => setError(null)} />
      {saved && (saved.root_known === false || saved.backup_known === false) && (
        <Note warn>
          {L(
            "Gespeichert – der Switch ist (noch) nicht unter den gelesenen Switches; die Prüfung vergleicht mit dem Namen wie gelesen.",
            "Saved – the switch is not (yet) among the read switches; the check compares with the name as read.",
          )}
        </Note>
      )}
      {canEdit && (
        <div className="card" id="stp-target-form">
          <div className="card-head">
            <h2>{form.id ? L("Soll-Root ändern", "Change target root") : L("Soll-Root festlegen", "Define target root")}</h2>
          </div>
          <div className="card-body pad" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))", gap: 10, alignItems: "end" }}>
            <div className="field">
              <label htmlFor="stp-target-site">{L("Standort", "Site")}</label>
              <select id="stp-target-site" className="sel input" value={form.site_id} onChange={field("site_id")}>
                <option value="">{L("alle Standorte", "all sites")}</option>
                {data.sites.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.code} · {x.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="stp-target-vlans">VLANs</label>
              <input id="stp-target-vlans" className="input mono" placeholder={L("alle; z. B. 10,20-30", "all; e.g. 10,20-30")} value={form.vlans} onChange={field("vlans")} />
            </div>
            <div className="field">
              <label htmlFor="stp-target-root">Root</label>
              <input id="stp-target-root" className="input" list="stp-switches" value={form.root} onChange={field("root")} />
            </div>
            <div className="field">
              <label htmlFor="stp-target-backup">{L("Ersatz-Root", "Backup root")}</label>
              <input id="stp-target-backup" className="input" list="stp-switches" value={form.backup} onChange={field("backup")} />
            </div>
            <div className="field">
              <label htmlFor="stp-target-note">{L("Notiz", "Note")}</label>
              <input id="stp-target-note" className="input" maxLength={500} value={form.note} onChange={field("note")} />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button id="stp-target-save" className="btn btn-primary" disabled={busy || !form.root.trim()} onClick={save}>
                {L("Speichern", "Save")}
              </button>
              {form.id && (
                <button className="btn btn-ghost" onClick={() => setForm(EMPTY_FORM)}>
                  {L("Abbrechen", "Cancel")}
                </button>
              )}
            </div>
            <datalist id="stp-switches">
              {data.switch_names.map((n) => (
                <option key={n} value={n} />
              ))}
            </datalist>
          </div>
        </div>
      )}
      <div className="card" id="stp-targets" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h2>{L("Soll-Root", "Target root")}</h2>
          <span className="faint">{L(`${data.targets.length} Einträge`, `${data.targets.length} entries`)}</span>
        </div>
        <div className="card-body scroll">
          {data.targets.length === 0 ? (
            <Empty title={L("kein Soll-Root festgelegt", "no target root defined")}>
              <span className="faint">{L("Ohne Soll wird nur gemeldet, wer Root ist.", "Without a target only the current root is reported.")}</span>
            </Empty>
          ) : (
            <table className="compact">
              <thead>
                <tr>
                  <th>{L("Standort", "Site")}</th>
                  <th>VLANs</th>
                  <th>Root</th>
                  <th>{L("Ersatz-Root", "Backup root")}</th>
                  <th>{L("Notiz", "Note")}</th>
                  <th>{L("Geändert", "Changed")}</th>
                  {canEdit && <th />}
                </tr>
              </thead>
              <tbody>
                {data.targets.map((t) => (
                  <tr key={t.id} data-target={t.id}>
                    <td>
                      {t.site ?? (t.site_id ? <span className="chip warn">{L("Standort fehlt", "site missing")}</span> : L("alle", "all"))}
                    </td>
                    <td className="mono">{t.vlans ?? L("alle", "all")}</td>
                    <td>
                      <b>{t.root}</b>
                    </td>
                    <td>{t.backup ?? <Dash />}</td>
                    <td className="faint">{t.note}</td>
                    <td className="faint nowrap">
                      {fmtTime(t.changed_at)} {t.changed_by?.replace(/^user:/, "")}
                    </td>
                    {canEdit && (
                      <td className="nowrap">
                        <button className="btn btn-sm btn-ghost" onClick={() => edit(t)}>
                          {L("Bearbeiten", "Edit")}
                        </button>{" "}
                        <button className="btn btn-sm btn-danger stp-target-delete" disabled={busy} onClick={() => run(deleteStpTarget(t.id))}>
                          {L("Löschen", "Delete")}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <div className="card" id="stp-settings" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h2>{L("Topologieänderungen", "Topology changes")}</h2>
          <span className="faint">
            {L(
              `Befund ab so vielen Änderungen je 24 h (Vorgabe ${data.summary.tc_default}); gerechnet aus dem Verlauf der Zähler der Läufe`,
              `finding from this many changes per 24 h (default ${data.summary.tc_default}); computed from the history of the counters of the runs`,
            )}
          </span>
        </div>
        <div className="card-body pad" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input
            id="stp-tc-limit"
            className="input input-inline mono"
            style={{ width: 110 }}
            inputMode="numeric"
            disabled={!canEdit}
            aria-label={L("Schwelle je 24 h", "Limit per 24 h")}
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
          />
          <span className="faint">{L("je 24 h", "per 24 h")}</span>
          {canEdit && (
            <>
              <button
                id="stp-tc-save"
                className="btn btn-sm btn-primary"
                disabled={busy || !/^\d+$/.test(limit.trim()) || Number(limit) < 1}
                onClick={() => run(saveStpSettings(tenantId, Number(limit)))}
              >
                {L("Speichern", "Save")}
              </button>
              <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => run(saveStpSettings(tenantId, null))}>
                {L("Vorgabe", "Default")}
              </button>
            </>
          )}
        </div>
      </div>
    </>
  );
}

// -- Switches -----------------------------------------------------------------------------------------------------------

function DevicesTab({ devices }: { devices: StpDeviceRow[] }) {
  if (!devices.length) return <Empty title={L("keine Switches mit Spanning Tree", "no switches with Spanning Tree")} />;
  return (
    <div className="card" id="stp-devices">
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>Switch</th>
              <th>{L("Quellsystem", "Source system")}</th>
              <th>{L("Status", "Status")}</th>
              <th>{L("Modus", "Mode")}</th>
              <th className="num">{L("Instanzen", "Instances")}</th>
              <th>{L("Root für", "Root for")}</th>
              <th className="num">{L("blockiert", "blocked")}</th>
              <th>err-disabled</th>
              <th>{L("Topologieänderungen", "Topology changes")}</th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => {
              const [cls, label, title] = STATUS[d.status] ?? ["na", d.status, ""];
              return (
                <tr key={`${d.source_system_id}|${d.device}`} data-device={d.device}>
                  <td className="nowrap">
                    <b>{d.device}</b>
                  </td>
                  <td className="faint">
                    {d.source}
                    {d.site && ` · ${d.site}`}
                  </td>
                  <td className="nowrap">
                    <span className={`chip ${cls}`} title={d.note ? `${title}: ${d.note}` : title}>
                      {label}
                    </span>
                  </td>
                  <td className="nowrap">
                    {modeText(d.mode)}
                    {d.region && <span className="faint"> · Region {d.region}{d.revision != null ? ` (${d.revision})` : ""}</span>}
                  </td>
                  <td className="num mono">{d.instances || ""}</td>
                  <td className="faint">{d.root_of.join(", ")}</td>
                  <td className="num mono">{d.blocked || ""}</td>
                  <td>
                    {d.errdisabled.map((e) => (
                      <div key={e.port}>
                        <span className="mono">{e.port}</span>{" "}
                        <span className={`chip ${e.bpdu_guard ? "crit" : "na"}`}>{e.bpdu_guard ? "BPDU-Guard" : (e.reason ?? L("Grund unbekannt", "reason unknown"))}</span>
                      </div>
                    ))}
                  </td>
                  <td className="nowrap">
                    {d.tc_count ? <span className="mono">{d.tc_count}</span> : ""}
                    {d.tc_busiest && (
                      <span className="faint">
                        {" "}
                        · {d.tc_busiest.instance} {d.tc_busiest.per_day} {L("je 24 h", "per 24 h")}
                      </span>
                    )}
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
