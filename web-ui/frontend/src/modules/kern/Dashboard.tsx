// Bereiche der Übersicht (0.117.0, Anwender: „Dashboard erweitern mit Schnellansichten, Grafen, eventuell Auswahl an Content
// wo ich an- und abwählen kann“ – gewählt: Findings & Läufe grafisch, Schnellansichten, Routing-Änderungen, Applikationen &
// Regeln, Diagramm mit Objekttypen). Jeder Bereich ist an- und abwählbar und verschiebbar; die Auswahl merkt sich der Browser.
import { useEffect, useState } from "react";
import {
  fetchApps,
  fetchObjectSummary,
  fetchRoutingChangeSummary,
  fetchRunsDaily,
  type Application,
  type FindingSummary,
  type ModuleKey,
  type ObjectSummary,
  type RoutingChangeSummary,
  type RuleSummary,
  type RunsDay,
  type System,
} from "../../api";
import { L } from "../../i18n";
import { FINDING_GROUPS, findingLabel } from "../../refs";
import { Loading } from "../../ui";
import type { View, ViewParams } from "../types";

type Go = (v: View, p?: ViewParams) => void;

export type WidgetKey =
  | "health"
  | "kpis"
  | "quick"
  | "findings"
  | "runschart"
  | "runs"
  | "zones"
  | "routing"
  | "apps"
  | "rules"
  | "objects";

export interface WidgetMeta {
  key: WidgetKey;
  label: string;
  module?: ModuleKey; // nur, wenn das Modul für den Mandanten an ist
  staff?: boolean; // nicht für Kunden-Benutzer (Betrieb)
  tenant?: boolean; // braucht einen gewählten Mandanten
  wide?: boolean; // ganze Breite
}

export const WIDGETS: WidgetMeta[] = [
  { key: "health", label: L("Betriebszustand", "Health"), staff: true, wide: true },
  { key: "kpis", label: L("Kennzahlen", "Key figures"), wide: true },
  { key: "quick", label: L("Schnellansichten", "Quick views"), wide: true },
  { key: "findings", label: L("Offene Findings je Gruppe", "Open findings per group"), module: "findings" },
  { key: "runschart", label: L("Läufe der letzten 14 Tage", "Runs of the last 14 days"), module: "worker", staff: true },
  { key: "runs", label: L("Letzte Läufe", "Recent runs"), module: "worker", staff: true },
  { key: "zones", label: L("Regeln je Zone (Ziel)", "Rules per zone (destination)") },
  { key: "routing", label: L("Routing-Änderungen (7 Tage)", "Routing changes (7 days)"), module: "analyse", staff: true, tenant: true },
  { key: "apps", label: L("Applikationen", "Applications"), module: "doku", tenant: true },
  { key: "rules", label: L("Regeln je Firewall und Aktion", "Rules per firewall and action") },
  { key: "objects", label: L("Objekte nach Typ", "Objects by type"), tenant: true },
];

const STORE = "automato_overview_layout";

interface Layout {
  order: WidgetKey[];
  hidden: WidgetKey[];
}

function readLayout(): Layout | null {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<Layout>;
    return { order: Array.isArray(v.order) ? v.order : [], hidden: Array.isArray(v.hidden) ? v.hidden : [] };
  } catch {
    return null;
  }
}

// Reihenfolge und ausgeblendete Bereiche (je Browser); neue Bereiche erscheinen hinten
export function useLayout() {
  const all = WIDGETS.map((w) => w.key);
  const [layout, setLayout] = useState<Layout>(() => readLayout() ?? { order: all, hidden: [] });
  const order = [...layout.order.filter((k) => all.includes(k)), ...all.filter((k) => !layout.order.includes(k))];
  const save = (next: Layout) => {
    setLayout(next);
    try {
      localStorage.setItem(STORE, JSON.stringify(next));
    } catch {
      // ohne Speicher (privates Fenster): nur für diese Sitzung
    }
  };
  return {
    order,
    hidden: new Set(layout.hidden),
    toggle: (k: WidgetKey) =>
      save({ order, hidden: layout.hidden.includes(k) ? layout.hidden.filter((x) => x !== k) : [...layout.hidden, k] }),
    move: (k: WidgetKey, by: -1 | 1) => {
      const i = order.indexOf(k);
      const j = i + by;
      if (i < 0 || j < 0 || j >= order.length) return;
      const next = [...order];
      [next[i], next[j]] = [next[j], next[i]];
      save({ order: next, hidden: layout.hidden });
    },
    reset: () => save({ order: all, hidden: [] }),
  };
}

export function LayoutEditor({
  layout,
  available,
  onClose,
}: {
  layout: ReturnType<typeof useLayout>;
  available: (w: WidgetMeta) => boolean;
  onClose: () => void;
}) {
  return (
    <div className="card" id="overview-layout" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <h2>{L("Ansicht anpassen", "Customize view")}</h2>
        <span className="faint">{L("an- und abwählen, verschieben – gilt für diesen Browser", "show/hide, reorder – for this browser")}</span>
        <div className="spacer" />
        <button className="btn btn-ghost btn-sm" onClick={layout.reset}>
          {L("Vorgabe", "Default")}
        </button>
        <button className="btn btn-sm" onClick={onClose}>
          {L("Fertig", "Done")}
        </button>
      </div>
      <div className="card-body">
        <ul className="layout-list">
          {layout.order.map((k, i) => {
            const w = WIDGETS.find((x) => x.key === k)!;
            const off = !available(w);
            return (
              <li key={k} data-widget={k} className={off ? "faint" : ""}>
                <label>
                  <input type="checkbox" checked={!layout.hidden.has(k)} onChange={() => layout.toggle(k)} /> {w.label}
                </label>
                {off && <span className="chip">{L("Modul aus bzw. nicht für diese Rolle", "module off or not for this role")}</span>}
                <span className="spacer" />
                <button className="btn btn-ghost btn-sm" disabled={i === 0} title={L("nach oben", "up")} onClick={() => layout.move(k, -1)}>
                  ↑
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={i === layout.order.length - 1}
                  title={L("nach unten", "down")}
                  onClick={() => layout.move(k, 1)}
                >
                  ↓
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

// Waagrechte Balken: Name, Balken, Zahl (Klick filtert)
function HBars({
  rows,
  onPick,
  tone,
}: {
  rows: { key: string; label: string; value: number; sub?: string }[];
  onPick?: (key: string) => void;
  tone?: "warn";
}) {
  const max = rows.reduce((m, r) => Math.max(m, r.value), 1);
  return (
    <div className="hbars">
      {rows.map((r) => (
        <button
          key={r.key}
          className="hbar-row"
          data-key={r.key}
          disabled={!onPick}
          title={r.sub ? `${r.label}: ${r.value} · ${r.sub}` : `${r.label}: ${r.value}`}
          onClick={() => onPick?.(r.key)}
        >
          <span className="hbar-label">{r.label}</span>
          <span className={`hbar ${tone ?? ""}`}>
            <span style={{ width: `${(r.value / max) * 100}%` }} />
          </span>
          <span className="hbar-val mono">{r.value}</span>
        </button>
      ))}
    </div>
  );
}

function Card({ id, title, children, foot }: { id: string; title: string; children: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <div className="card" id={id}>
      <div className="card-head">
        <h2>{title}</h2>
      </div>
      {children}
      {foot && <div className="card-foot">{foot}</div>}
    </div>
  );
}

const empty = (text: string) => <div className="empty">{text}</div>;

// Offene Findings je Gruppe (Klick: die Liste mit dem häufigsten Typ der Gruppe)
export function FindingsChart({ summary, onGo }: { summary: FindingSummary | null; onGo: Go }) {
  if (summary === null) return <Card id="dash-findings" title={L("Offene Findings je Gruppe", "Open findings per group")}><Loading /></Card>;
  const rows = FINDING_GROUPS.map((g) => {
    const types = g.types.filter((t) => summary.by_type[t]?.open);
    const top = [...types].sort((a, b) => summary.by_type[b].open - summary.by_type[a].open)[0];
    return {
      key: top ?? g.key,
      label: g.label,
      value: types.reduce((n, t) => n + summary.by_type[t].open, 0),
      sub: top ? `${L("am häufigsten", "most frequent")}: ${findingLabel(top)}` : undefined,
    };
  }).filter((r) => r.value > 0);
  rows.sort((a, b) => b.value - a.value);
  return (
    <Card
      id="dash-findings"
      title={L("Offene Findings je Gruppe", "Open findings per group")}
      foot={
        <button className="linkbtn" onClick={() => onGo("findings")}>
          {L(`Alle offenen Findings (${summary.open}) →`, `All open findings (${summary.open}) →`)}
        </button>
      }
    >
      {rows.length === 0 ? empty(L("Keine offenen Findings", "No open findings")) : <HBars rows={rows} tone="warn" onPick={(t) => onGo("findings", { type: t })} />}
    </Card>
  );
}

// Läufe je Tag: erfolgreich, Fehler, offen (gestapelt)
export function RunsChart({ tenantId, onGo, refresh }: { tenantId: number | null; onGo: Go; refresh: unknown }) {
  const [days, setDays] = useState<RunsDay[] | null>(null);
  useEffect(() => {
    fetchRunsDaily(tenantId)
      .then(setDays)
      .catch(() => setDays([]));
  }, [tenantId, refresh]);
  const max = (days ?? []).reduce((m, d) => Math.max(m, d.ok + d.failed + d.open), 1);
  const failed = (days ?? []).reduce((n, d) => n + d.failed, 0);
  const total = (days ?? []).reduce((n, d) => n + d.ok + d.failed + d.open, 0);
  return (
    <Card
      id="dash-runschart"
      title={L("Läufe der letzten 14 Tage", "Runs of the last 14 days")}
      foot={
        <>
          <span className="legend ok">{L("erfolgreich", "succeeded")}</span> <span className="legend crit">{L("Fehler", "failed")}</span>{" "}
          <span className="legend open">{L("offen", "open")}</span>
          <span className="faint"> · {L(`${total} Läufe, ${failed} mit Fehler`, `${total} runs, ${failed} failed`)} · </span>
          <button className="linkbtn" onClick={() => onGo("laeufe")}>
            {L("Läufe →", "Runs →")}
          </button>
        </>
      }
    >
      {days === null ? (
        <Loading />
      ) : (
        <>
          <div className="vstack">
            {days.map((d) => {
              const n = d.ok + d.failed + d.open;
              return (
                <div
                  key={d.day}
                  className="vcol"
                  data-day={d.day}
                  title={`${d.day}: ${d.ok} ${L("erfolgreich", "succeeded")}, ${d.failed} ${L("Fehler", "failed")}, ${d.open} ${L("offen", "open")}`}
                >
                  <div className="vbar" style={{ height: `${(n / max) * 100}%` }}>
                    {d.failed > 0 && <span className="crit" style={{ flex: d.failed }} />}
                    {d.open > 0 && <span className="open" style={{ flex: d.open }} />}
                    {d.ok > 0 && <span className="ok" style={{ flex: d.ok }} />}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="bars-x">
            {days.map((d) => (
              <span key={d.day}>{`${d.day.slice(8, 10)}.${d.day.slice(5, 7)}`}</span>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

// Schnellansichten: häufig gebrauchte Ansichten mit fertigem Filter
export function QuickViews({
  summary,
  on,
  staff,
  onGo,
}: {
  summary: FindingSummary | null;
  on: (m?: ModuleKey) => boolean;
  staff: boolean;
  onGo: Go;
}) {
  const open = (t: string) => summary?.by_type[t]?.open;
  const items: { key: string; label: string; view: View; params?: ViewParams; count?: number; module?: ModuleKey; staff?: boolean }[] = [
    { key: "findings", label: L("Offene Findings", "Open findings"), view: "findings", count: summary?.open, module: "findings" },
    {
      key: "unused",
      label: L("Regel nie auf dem Weg", "Rule never on the path"),
      view: "findings",
      params: { type: "app_rule_unused" },
      count: open("app_rule_unused"),
      module: "findings",
    },
    {
      key: "appdev",
      label: L("Applikationen mit Abweichung", "Applications with deviation"),
      view: "findings",
      params: { type: "app_connection_mismatch" },
      count: open("app_connection_mismatch"),
      module: "findings",
    },
    {
      key: "unmatched",
      label: L("Objekte ohne IPAM-Treffer", "Objects without IPAM match"),
      view: "findings",
      params: { type: "unmatched_object" },
      count: open("unmatched_object"),
      module: "findings",
    },
    { key: "vips", label: L("VIPs (V-EXT_)", "VIPs (V-EXT_)"), view: "objekte", params: { tab: "addr", prefix: "V-EXT_" } },
    { key: "wege", label: L("Weg-Suche", "Path search"), view: "wege", module: "wege" },
    { key: "apps", label: L("Applikationen", "Applications"), view: "apps", module: "doku" },
    { key: "changes", label: L("Routing-Verlauf", "Routing history"), view: "routing", params: { tab: "changes" }, module: "analyse", staff: true },
  ];
  return (
    <Card id="dash-quick" title={L("Schnellansichten", "Quick views")}>
    <div className="quick">
      {items
        .filter((x) => on(x.module) && (!x.staff || staff))
        .map((x) => (
          <button key={x.key} className="btn quick-btn" data-quick={x.key} onClick={() => onGo(x.view, x.params)}>
            {x.label}
            {x.count !== undefined && <span className={`chip ${x.count ? "warn" : "ok"}`}>{x.count}</span>}
          </button>
        ))}
    </div>
    </Card>
  );
}

const CHANGE_KIND: Record<string, string> = {
  route: L("Routen", "Routes"),
  bgp_peer: L("BGP-Nachbarn", "BGP neighbors"),
  ha: "HA",
  policy_route: L("Policy-Routen", "Policy routes"),
  context: L("Kontexte", "Contexts"),
  l3out: "L3Outs",
};

export function RoutingChanges({ tenantId, onGo }: { tenantId: number; onGo: Go }) {
  const [data, setData] = useState<RoutingChangeSummary | null>(null);
  useEffect(() => {
    fetchRoutingChangeSummary(tenantId)
      .then(setData)
      .catch(() => setData({ days: 7, since: "", total: 0, by_kind: {} }));
  }, [tenantId]);
  const rows = Object.entries(data?.by_kind ?? {})
    .map(([k, c]) => ({
      key: k,
      label: CHANGE_KIND[k] ?? k,
      value: Object.values(c).reduce((n, x) => n + x, 0),
      sub: `+${c.added ?? 0} −${c.removed ?? 0} ~${c.changed ?? 0}`,
    }))
    .sort((a, b) => b.value - a.value);
  return (
    <Card
      id="dash-routing"
      title={L("Routing-Änderungen (7 Tage)", "Routing changes (7 days)")}
      foot={
        <button className="linkbtn" onClick={() => onGo("routing", { tab: "changes" })}>
          {L(`Verlauf (${data?.total ?? 0}) →`, `History (${data?.total ?? 0}) →`)}
        </button>
      }
    >
      {data === null ? (
        <Loading />
      ) : rows.length === 0 ? (
        empty(L("Keine Änderungen in den letzten 7 Tagen", "No changes in the last 7 days"))
      ) : (
        <HBars rows={rows} onPick={() => onGo("routing", { tab: "changes" })} />
      )}
    </Card>
  );
}

export function AppsCard({ tenantId, summary, onGo }: { tenantId: number; summary: FindingSummary | null; onGo: Go }) {
  const [apps, setApps] = useState<Application[] | null>(null);
  useEffect(() => {
    fetchApps(tenantId)
      .then(setApps)
      .catch(() => setApps([]));
  }, [tenantId]);
  const appTypes = FINDING_GROUPS.find((g) => g.key === "apps")?.types ?? [];
  const openApp = appTypes.reduce((n, t) => n + (summary?.by_type[t]?.open ?? 0), 0);
  const list = apps ?? [];
  const tiles = [
    { key: "total", label: L("Applikationen", "Applications"), value: list.filter((a) => !a.infrastructure).length, go: () => onGo("apps") },
    { key: "vms", label: L("mit VMs", "with VMs"), value: list.filter((a) => a.vm_count > 0).length, go: () => onGo("apps") },
    { key: "infra", label: L("Infrastruktur", "Infrastructure"), value: list.filter((a) => a.infrastructure).length, go: () => onGo("apps") },
    {
      key: "dev",
      label: L("offene Findings", "open findings"),
      value: openApp,
      go: () => onGo("findings", { type: "app_connection_mismatch" }),
      warn: openApp > 0,
    },
  ];
  return (
    <Card id="dash-apps" title={L("Applikationen", "Applications")}>
      {apps === null ? (
        <Loading />
      ) : (
        <div className="kpis wrap" style={{ margin: 12 }}>
          {tiles.map((t) => (
            <button key={t.key} className="kpi" data-tile={t.key} onClick={t.go}>
              <div className="k-label">{t.label}</div>
              <div className={`k-val sm ${t.warn ? "warn" : ""}`}>{t.value}</div>
            </button>
          ))}
        </div>
      )}
    </Card>
  );
}

const ACTION: Record<string, string> = {
  accept: L("erlaubt", "allowed"),
  permit: L("erlaubt", "allowed"),
  deny: L("gesperrt", "denied"),
  ipsec: "IPsec",
};

export function RulesCard({ rules, systems, onGo }: { rules: RuleSummary | null; systems: System[]; onGo: Go }) {
  if (rules === null) return <Card id="dash-rules" title={L("Regeln je Firewall und Aktion", "Rules per firewall and action")}><Loading /></Card>;
  const name = (id: number) => systems.find((s) => s.id === id)?.name ?? `#${id}`;
  const bySource = (rules.by_source ?? []).slice(0, 10).map(([id, n]) => ({ key: String(id), label: name(id), value: n }));
  const byAction = Object.entries(rules.by_action ?? {})
    .map(([a, n]) => ({ key: a, label: ACTION[a] ?? a, value: n }))
    .sort((a, b) => b.value - a.value);
  return (
    <Card id="dash-rules" title={L("Regeln je Firewall und Aktion", "Rules per firewall and action")}>
      {rules.total === 0 ? (
        empty(L("Keine Regeln", "No rules"))
      ) : (
        <>
          <div className="dash-sub">{L("je Quellsystem", "per source system")}</div>
          <HBars rows={bySource} onPick={() => onGo("regeln")} />
          <div className="dash-sub">{L("je Aktion", "per action")}</div>
          <HBars rows={byAction} onPick={() => onGo("regeln")} />
        </>
      )}
    </Card>
  );
}

const CATEGORY: Record<string, string> = {
  address: L("Adressen", "Addresses"),
  address_group: L("Adressgruppen", "Address groups"),
  vip: "VIPs",
  vip_target: L("VIP-Ziele", "VIP targets"),
  ippool: L("IP-Pools", "IP pools"),
  service: "Services",
  service_group: L("Service-Gruppen", "Service groups"),
};
const TAB_OF: Record<string, string> = {
  address: "addr",
  vip: "addr",
  vip_target: "addr",
  ippool: "addr",
  address_group: "groups",
  service_group: "groups",
  service: "services",
};

// Objekte nach Kategorie und nach Präfix der Namenskonvention (Klick: Objekte mit diesem Filter)
export function ObjectsCard({ tenantId, onGo }: { tenantId: number; onGo: Go }) {
  const [data, setData] = useState<ObjectSummary | null>(null);
  useEffect(() => {
    fetchObjectSummary(tenantId)
      .then(setData)
      .catch(() => setData({ total: 0, by_category: {}, by_type: {}, by_prefix: {}, prefixes: {} }));
  }, [tenantId]);
  const cats = Object.entries(data?.by_category ?? {})
    .map(([c, n]) => ({ key: c, label: CATEGORY[c] ?? c, value: n }))
    .sort((a, b) => b.value - a.value);
  const prefixes = Object.entries(data?.by_prefix ?? {})
    .map(([p, n]) => ({ key: p || "-", label: p || L("ohne Präfix", "no prefix"), value: n, sub: p ? data?.prefixes[p] : undefined }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 10);
  return (
    <Card
      id="dash-objects"
      title={L("Objekte nach Typ", "Objects by type")}
      foot={
        <button className="linkbtn" onClick={() => onGo("objekte")}>
          {L(`Objekte (${data?.total ?? 0}) →`, `Objects (${data?.total ?? 0}) →`)}
        </button>
      }
    >
      {data === null ? (
        <Loading />
      ) : data.total === 0 ? (
        empty(L("Keine Objekte", "No objects"))
      ) : (
        <>
          <div className="dash-sub">{L("je Kategorie", "per category")}</div>
          <HBars rows={cats} onPick={(c) => onGo("objekte", { tab: TAB_OF[c] ?? "addr" })} />
          <div className="dash-sub">{L("je Präfix der Namenskonvention", "per naming convention prefix")}</div>
          <HBars rows={prefixes} onPick={(p) => onGo("objekte", { tab: data.prefixes[p] === "service" ? "services" : "addr", prefix: p })} />
        </>
      )}
    </Card>
  );
}
