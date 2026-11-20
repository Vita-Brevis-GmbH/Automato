// Pläne je Netzwerkschicht (1.23.0, ADR 0067 Nachtrag 1.23.0; Anwender 2026-11-20: „je einen Physical Layer, Layer L2 und
// einen Layer L3 Plan … von den Daten“): L1 Physik (Geräte, Kabel mit Ports und Kabelnummer, Port-Channel gebündelt),
// L2 Switching (Trunks mit erlaubten VLANs, Root je VLAN, blockierte Ports), L3 Routing (Firewalls/VDOMs, Router, Netze,
// Transfernetze, statische Routen, BGP, IPsec). Für den ganzen Mandanten, Filter nach Standort (Adresse `?site=`), Lage
// serverseitig (je Standort ein Rahmen, Grenze gezählt), Ebenen ein-/ausblendbar, Klick zeigt Details mit Link in die
// Detailansicht, Export draw.io. Rein lesend.
import { useEffect, useMemo, useState } from "react";
import { exportLayerPlan, fetchLayerPlan, LayerPlan as Plan, LayerPlanEdge, LayerPlanKey, LayerPlanNode } from "../../api";
import { usePanZoom } from "../../AppConnections";
import { L } from "../../i18n";
import { hashOf, replaceHash } from "../../places";
import { download, ErrorBox, ExportLangSelect, Loading, Note, PageHead } from "../../ui";
import type { View, ViewParams } from "../types";

const KEYS: LayerPlanKey[] = ["l1", "l2", "l3"];
const STORE = "automato_layer_plan_hidden_"; // ausgeblendete Ebenen je Plan (nur dieser Browser)

const TONE: Record<string, string> = {
  line: "var(--muted)",
  muted: "var(--faint)",
  warn: "var(--warn)",
  bad: "var(--crit)",
  stp: "var(--warn)",
  netbox: "#9c5bb5",
  bgp: "var(--info)",
  vpn: "var(--accent)",
  static: "#3b82c4",
  route: "#a0785a",
};
const STROKE: Record<string, string> = {
  switch: "var(--info)",
  leaf: "var(--info)",
  spine: "var(--info)",
  firewall: "var(--crit)",
  vdom: "var(--crit)",
  router: "#8e5bb5",
  l3: "#8e5bb5",
  vrf: "var(--good)",
  esxi: "var(--good)",
  segment: "var(--warn)",
  net: "var(--warn)",
  more: "var(--warn)",
  exit: "#8e5bb5",
  vpn_peer: "var(--accent)",
};
const BADGE: Record<string, string> = { root: "var(--good)", warn: "var(--warn)", info: "var(--info)", muted: "var(--faint)" };

function intro(layer: LayerPlanKey, tenant: string) {
  if (layer === "l1")
    return L(
      <>
        Physischer Plan von „{tenant}“: Geräte der <a href={hashOf("verkabelung")}>Verkabelung</a> (Switches, Router, Firewalls,
        ESXi-Hosts, weitere aus CDP/LLDP) und je Kabel die Ports an beiden Enden mit Kabelnummer; ein Port-Channel ist eine Linie
        mit seinen Mitgliedern. Gestrichelt: nur einseitig gemeldet bzw. nur in NetBox. Rein lesend.
      </>,
      <>
        Physical plan of “{tenant}”: devices of the <a href={hashOf("verkabelung")}>cabling</a> (switches, routers, firewalls, ESXi
        hosts, more from CDP/LLDP) and per cable the ports at both ends with the cable number; a port channel is one line with its
        members. Dashed: reported by one side only or only in NetBox. Read-only.
      </>,
    );
  if (layer === "l2")
    return L(
      <>
        Switching-Plan von „{tenant}“: weiterleitende Geräte mit Trunks bzw. Port-Channels und den erlaubten VLANs (beide Enden,
        gekürzt mit „+n“), Root je VLAN bzw. MST-Instanz und blockierte Ports aus dem <a href={hashOf("spanningtree")}>Spanning
        Tree</a>. Rein lesend.
      </>,
      <>
        Switching plan of “{tenant}”: forwarding devices with trunks or port channels and the allowed VLANs (both ends, shortened
        with “+n”), root per VLAN or MST instance and blocked ports from the <a href={hashOf("spanningtree")}>Spanning Tree</a>.
        Read-only.
      </>,
    );
  return L(
    <>
      Routing-Plan von „{tenant}“: Firewalls/VDOMs, gelesene Router und ACI-VRFs mit Transfernetzen und Segmenten, Netze an einem
      Knoten, Router und Anbindungen nach aussen, statische Routen, BGP-Sitzungen und IPsec-Tunnel (zur gelesenen Gegenstelle
      bzw. zu ihrer Adresse) – aus dem <a href={hashOf("routing")}>Routing</a> und den{" "}
      <a href={hashOf("vpn")}>VPN-Tunneln</a>. Rein lesend.
    </>,
    <>
      Routing plan of “{tenant}”: firewalls/VDOMs, read routers and ACI VRFs with transfer networks and segments, networks at one
      node, routers and exits to the outside, static routes, BGP sessions and IPsec tunnels (to the read peer or to its address) –
      from <a href={hashOf("routing")}>routing</a> and the <a href={hashOf("vpn")}>VPN tunnels</a>. Read-only.
    </>,
  );
}

function loadHidden(layer: LayerPlanKey): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(STORE + layer) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

const short = (t: string, n = 26) => (t.length <= n ? t : `${t.slice(0, n - 1)}…`);
const on = (hidden: Set<string>, layer: string | null | undefined) => !layer || !hidden.has(layer);
type Focus = { kind: "node" | "edge"; id: string } | null;

export default function LayerPlanView({
  tenantId,
  tenantLabel,
  params,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params: ViewParams;
}) {
  const layer: LayerPlanKey = KEYS.includes(params.plan as LayerPlanKey) ? (params.plan as LayerPlanKey) : "l1";
  const [site, setSite] = useState<string>(params.site ?? "");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [hidden, setHidden] = useState<Set<string>>(() => loadHidden(layer));
  const [focus, setFocus] = useState<Focus>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (tenantId === null) return;
    let live = true;
    setPlan(null);
    setErr(null);
    setFocus(null);
    fetchLayerPlan(tenantId, layer, site === "" ? null : Number(site))
      .then((d) => live && setPlan(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, layer, site]);
  const pickSite = (v: string) => {
    setSite(v);
    replaceHash("plaene", v === "" ? { plan: layer } : { plan: layer, site: v });
  };
  const toggle = (key: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      try {
        localStorage.setItem(STORE + layer, JSON.stringify([...next]));
      } catch {
        // ohne Speicher: nur für diese Sitzung
      }
      return next;
    });
  const doExport = async () => {
    if (tenantId === null) return;
    setBusy(true);
    setErr(null);
    try {
      const { blob, filename } = await exportLayerPlan(tenantId, layer, site === "" ? null : Number(site), [...hidden]);
      download(filename, blob);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const title = plan?.title ?? { l1: L("L1-Plan · Physik", "L1 plan · Physical"), l2: L("L2-Plan · Switching", "L2 plan · Switching"), l3: L("L3-Plan · Routing", "L3 plan · Routing") }[layer];
  return (
    <>
      <PageHead title={title}>{intro(layer, tenantLabel)}</PageHead>
      {tenantId === null ? (
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      ) : (
        <div className="card" id="layer-plan" data-plan={layer}>
          <ErrorBox error={err} onClose={() => setErr(null)} />
          <div className="app-plan-bar">
            <label className="check">
              {L("Standort", "Site")}{" "}
              <select id="layer-plan-site" className="sel" value={site} onChange={(e) => pickSite(e.target.value)}>
                <option value="">{L("alle Standorte", "all sites")}</option>
                {(plan?.sites ?? []).map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.code} · {s.name}
                  </option>
                ))}
                <option value="0">{L("ohne Standort", "without site")}</option>
                {site !== "" && site !== "0" && plan && !plan.sites.some((s) => String(s.id) === site) && <option value={site}>#{site}</option>}
              </select>
            </label>
            {plan && <Counts plan={plan} />}
            <span className="plan-export" id="layer-plan-export">
              <span className="faint">Export:</span>
              <button className="btn btn-sm" id="layer-plan-export-drawio" disabled={busy || plan === null} onClick={() => void doExport()}>
                {busy ? "…" : "draw.io"}
              </button>
              <ExportLangSelect />
            </span>
          </div>
          {plan === null && !err ? <Loading /> : plan && <PlanBody plan={plan} hidden={hidden} toggle={toggle} focus={focus} setFocus={setFocus} />}
        </div>
      )}
    </>
  );
}

function Counts({ plan }: { plan: Plan }) {
  const n = Object.values(plan.summary.nodes).reduce((a, b) => a + b, 0);
  const e = Object.values(plan.summary.edges).reduce((a, b) => a + b, 0);
  return (
    <span className="faint" id="layer-plan-counts">
      {L(`${n} Elemente, ${e} Linien`, `${n} elements, ${e} lines`)}
      {plan.summary.foreign ? L(` · ${plan.summary.foreign} an anderen Standorten (blass)`, ` · ${plan.summary.foreign} at other sites (faded)`) : ""}
    </span>
  );
}

function Limits({ plan }: { plan: Plan }) {
  const lim = plan.limits;
  const cut = lim.nodes_shown < lim.nodes_total || lim.edges_shown < lim.edges_total;
  if (!cut && !plan.notes.length) return null;
  const dropped = Object.entries(lim.dropped)
    .map(([k, v]) => `${v} ${k}`)
    .join(", ");
  return (
    <div id="layer-plan-limits">
      {cut && (
        <Note warn>
          {L(
            `Grenze erreicht: ${lim.nodes_shown} von ${lim.nodes_total} Elementen und ${lim.edges_shown} von ${lim.edges_total} Linien gezeichnet (höchstens ${lim.max_nodes} bzw. ${lim.max_edges})${dropped ? ` – nicht gezeichnet: ${dropped}` : ""}. Standort wählen, um alles zu sehen.`,
            `Limit reached: ${lim.nodes_shown} of ${lim.nodes_total} elements and ${lim.edges_shown} of ${lim.edges_total} lines drawn (at most ${lim.max_nodes} and ${lim.max_edges})${dropped ? ` – not drawn: ${dropped}` : ""}. Choose a site to see everything.`,
          )}
        </Note>
      )}
      {plan.notes.map((t) => (
        <Note key={t}>{t}</Note>
      ))}
    </div>
  );
}

function PlanBody({
  plan,
  hidden,
  toggle,
  focus,
  setFocus,
}: {
  plan: Plan;
  hidden: Set<string>;
  toggle: (k: string) => void;
  focus: Focus;
  setFocus: (f: Focus) => void;
}) {
  const width = Math.max(plan.width, 400);
  const pz = usePanZoom(width);
  const byId = useMemo(() => new Map(plan.nodes.map((n) => [n.id, n])), [plan]);
  const shownNode = (n: LayerPlanNode | undefined) => !!n && on(hidden, n.layer);
  const nodes = plan.nodes.filter((n) => shownNode(n));
  const edges = plan.edges.filter((e) => on(hidden, e.layer) && shownNode(byId.get(e.a)) && shownNode(byId.get(e.b)));
  // Hervorheben: das gewählte Element mit seinen Nachbarn bzw. die gewählte Linie mit ihren Enden
  const lit = useMemo(() => {
    if (!focus) return null;
    const ids = new Set<string>([focus.id]);
    for (const e of plan.edges) {
      if (focus.kind === "node" && (e.a === focus.id || e.b === focus.id)) {
        ids.add(e.id);
        ids.add(e.a);
        ids.add(e.b);
      }
      if (focus.kind === "edge" && e.id === focus.id) {
        ids.add(e.a);
        ids.add(e.b);
      }
    }
    return ids;
  }, [focus, plan]);
  const dim = (id: string) => (lit && !lit.has(id) ? " dim" : "");
  const pick = (kind: "node" | "edge", id: string) => setFocus(focus && focus.kind === kind && focus.id === id ? null : { kind, id });
  const used = plan.layers.filter((l) => l.used);
  const name = (id: string) => byId.get(id)?.label ?? id;
  return (
    <>
      <Limits plan={plan} />
      <div className="app-plan-tools">
        <span className="seg" id="layer-plan-zoom">
          <button title={L("verkleinern", "zoom out")} onClick={pz.zoomOut}>
            −
          </button>
          <button title="100 %" onClick={pz.reset}>
            {Math.round(pz.zoom * 100)} %
          </button>
          <button title={L("vergrössern", "zoom in")} onClick={pz.zoomIn}>
            +
          </button>
          <button id="layer-plan-fit" title={L("in die Breite einpassen", "fit to width")} onClick={pz.fit}>
            {L("einpassen", "fit")}
          </button>
        </span>
        {used.length > 0 && <span className="faint">{L("Ebenen:", "Layers:")}</span>}
        {used.map((l) => (
          <label key={l.key} className="check">
            <input type="checkbox" id={`layer-plan-layer-${l.key}`} checked={!hidden.has(l.key)} onChange={() => toggle(l.key)} /> {l.label}
          </label>
        ))}
      </div>
      {plan.nodes.length === 0 ? (
        <Note>
          {plan.site !== null
            ? L("An diesem Standort ist nichts gelesen.", "Nothing read at this site.")
            : L("Für diese Schicht ist noch nichts gelesen.", "Nothing read for this layer yet.")}
        </Note>
      ) : (
        <div className="app-plan-scroll" ref={pz.box} {...pz.handlers}>
          <svg
            id="layer-plan-svg"
            width={width * pz.zoom}
            height={plan.height * pz.zoom}
            viewBox={`0 0 ${width} ${plan.height}`}
            role="img"
            aria-label={plan.title}
          >
            {plan.groups.map((g) => (
              <g key={g.id} className="layer-plan-group" data-group={g.title}>
                <rect x={g.x} y={g.y} width={g.w} height={g.h} rx={8} fill="var(--surface-2)" stroke="var(--border-strong)" strokeDasharray="5 3" />
                <text x={g.x + 10} y={g.y + 18} className="app-plan-group-title">
                  {g.title}
                </text>
              </g>
            ))}
            {edges.map((e) => (
              <PlanEdge key={e.id} e={e} hidden={hidden} cls={dim(e.id)} picked={focus?.id === e.id} name={name} onPick={() => pick("edge", e.id)} />
            ))}
            {nodes.map((n) => (
              <PlanNode key={n.id} n={n} hidden={hidden} cls={dim(n.id)} picked={focus?.id === n.id} onPick={() => pick("node", n.id)} />
            ))}
          </svg>
        </div>
      )}
      <Legend layer={plan.layer} />
      {focus && <FocusPanel plan={plan} focus={focus} byId={byId} onClose={() => setFocus(null)} />}
    </>
  );
}

function PlanEdge({ e, hidden, cls, picked, name, onPick }: { e: LayerPlanEdge; hidden: Set<string>; cls: string; picked: boolean; name: (id: string) => string; onPick: () => void }) {
  const color = TONE[e.tone] ?? TONE.line;
  const pts = e.points.map(([x, y]) => `${x},${y}`).join(" ");
  const texts = e.texts.filter((t) => on(hidden, t.layer));
  const ports = on(hidden, e.ends_layer);
  return (
    <g className={`app-plan-edge clickable${cls}${picked ? " picked" : ""}`} data-edge={`${name(e.a)} → ${name(e.b)}`} data-kind={e.kind} data-tone={e.tone} onClick={onPick}>
      <title>{[`${name(e.a)} ↔ ${name(e.b)}${e.label ? `: ${e.label}` : ""}`, ...e.texts.map((t) => t.text), ...e.info].join("\n")}</title>
      <polyline points={pts} fill="none" stroke="transparent" strokeWidth={12} />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={picked ? 3.2 : 1.8} strokeDasharray={e.dash ? "6 4" : undefined} />
      {e.label && (
        <text x={e.lx} y={e.ly} textAnchor="middle" fill={color} className="app-plan-label">
          {short(e.label, 30)}
        </text>
      )}
      {texts.map((t, i) => (
        <text key={i} x={e.lx} y={e.ly + 12 * (i + 1)} textAnchor="middle" fill={color} className="app-plan-port" data-layer={t.layer ?? undefined}>
          {short(t.text, 40)}
        </text>
      ))}
      {ports &&
        [e.pa, e.pb].map((p, i) =>
          p ? (
            <text key={i} x={p.x} y={p.y} textAnchor={p.anchor} className="app-plan-port layer-plan-end">
              {short(p.text, 28)}
            </text>
          ) : null,
        )}
    </g>
  );
}

function PlanNode({ n, hidden, cls, picked, onPick }: { n: LayerPlanNode; hidden: Set<string>; cls: string; picked: boolean; onPick: () => void }) {
  const badges = n.badges.filter((b) => on(hidden, b.layer)).slice(0, 3);
  return (
    <g
      className={`app-plan-node clickable${cls}${n.foreign ? " foreign" : ""}`}
      data-node={n.label}
      data-kind={n.kind}
      data-foreign={n.foreign ? "1" : undefined}
      transform={`translate(${n.x},${n.y})`}
      onClick={onPick}
      opacity={n.foreign ? 0.5 : undefined}
    >
      <title>{[n.label, n.sub, ...n.badges.map((b) => b.text), ...n.info].filter(Boolean).join("\n")}</title>
      <rect
        width={n.w}
        height={n.h}
        rx={6}
        fill={n.read ? "var(--surface)" : "var(--surface-2)"}
        stroke={STROKE[n.kind] ?? "var(--border-strong)"}
        strokeWidth={picked ? 3 : n.read ? 1.8 : 1.2}
        strokeDasharray={n.read && !n.foreign ? undefined : "4 3"}
      />
      <text x={10} y={18} fontSize={12} fontWeight={600} fill="var(--fg)">
        {short(n.label)}
      </text>
      <text x={10} y={33} fontSize={10} fill="var(--muted)">
        {short(n.sub, 30)}
      </text>
      {badges.map((b, i) => (
        <text key={i} x={10} y={47 + i * 13} fontSize={10} fontWeight={600} fill={BADGE[b.tone] ?? "var(--muted)"} className="layer-plan-badge">
          {short(b.text, 30)}
        </text>
      ))}
    </g>
  );
}

function Legend({ layer }: { layer: LayerPlanKey }) {
  const items: [string, string, boolean][] =
    layer === "l1"
      ? [
          ["line", L("Kabel (beidseitig gemeldet)", "Cable (reported by both sides)"), false],
          ["warn", L("Abweichung (Speed, Duplex, VLANs, Port-Channel)", "Mismatch (speed, duplex, VLANs, port channel)"), false],
          ["line", L("nur einseitig gemeldet", "reported by one side only"), true],
          ["netbox", L("nur in NetBox", "only in NetBox"), true],
          ["muted", L("Gegenstelle ohne CDP/LLDP", "Peer without CDP/LLDP"), false],
        ]
      : layer === "l2"
        ? [
            ["line", "Trunk / Access", false],
            ["warn", L("VLANs bzw. Modus abweichend", "VLANs or mode differ"), false],
            ["stp", L("Spanning Tree blockiert", "Spanning Tree blocks"), true],
          ]
        : [
            ["line", L("Transfernetz / Interface", "Transfer network / interface"), false],
            ["route", L("Route über einen anderen Knoten", "Route via another node"), true],
            ["static", L("statische Route", "static route"), true],
            ["bgp", "BGP", true],
            ["vpn", L("IPsec-Tunnel up", "IPsec tunnel up"), false],
            ["bad", L("Tunnel bzw. BGP down", "Tunnel or BGP down"), true],
            ["muted", L("Backup, Verbund, Inter-VDOM", "Backup, group, inter-VDOM"), true],
          ];
  return (
    <div className="app-plan-legend" id="layer-plan-legend">
      {items.map(([tone, text, dash]) => (
        <span key={text}>
          <i style={{ borderTopColor: TONE[tone], borderTopStyle: dash ? "dashed" : "solid" }} />
          {text}
        </span>
      ))}
      <span className="faint">{L("Gestrichelter Rahmen: nicht gelesen bzw. anderer Standort", "Dashed frame: not read or other site")}</span>
    </div>
  );
}

const VIEW_NAME: Record<string, string> = {
  verkabelung: L("Verkabelung", "Cabling"),
  spanningtree: "Spanning Tree",
  routing: "Routing",
  statisch: L("Statische Routen", "Static routes"),
  vpn: L("VPN-Tunnel", "VPN tunnels"),
  objekte: L("Netze", "Networks"),
};

function FocusPanel({ plan, focus, byId, onClose }: { plan: Plan; focus: NonNullable<Focus>; byId: Map<string, LayerPlanNode>; onClose: () => void }) {
  const node = focus.kind === "node" ? byId.get(focus.id) : undefined;
  const edge = focus.kind === "edge" ? plan.edges.find((e) => e.id === focus.id) : undefined;
  const link = node?.link ?? edge?.link ?? null;
  const head = node ? node.label : edge ? `${byId.get(edge.a)?.label ?? edge.a} ↔ ${byId.get(edge.b)?.label ?? edge.b}` : "";
  const sub = node ? [node.sub, node.foreign ? L("anderer Standort", "other site") : ""].filter(Boolean).join(" · ") : edge?.label ?? "";
  const lines = node
    ? [...node.badges.map((b) => b.text), ...node.info]
    : edge
      ? [
          ...(edge.end_a ? [`${byId.get(edge.a)?.label}: ${edge.end_a}`] : []),
          ...(edge.end_b ? [`${byId.get(edge.b)?.label}: ${edge.end_b}`] : []),
          ...edge.texts.map((t) => t.text),
          ...edge.info,
        ]
      : [];
  return (
    <div className="app-plan-focus" id="layer-plan-focus">
      <div>
        <b>{head}</b> {sub && <span className="faint"> – {sub}</span>}{" "}
        {link && (
          <a id="layer-plan-open" href={hashOf(link.view as View, link.params)}>
            {L(`öffnen in ${VIEW_NAME[link.view] ?? link.view}`, `open in ${VIEW_NAME[link.view] ?? link.view}`)}
          </a>
        )}{" "}
        <button className="linkbtn" onClick={onClose}>
          {L("Auswahl aufheben", "Clear selection")}
        </button>
      </div>
      {lines.length > 0 && (
        <ul>
          {[...new Set(lines)].map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
