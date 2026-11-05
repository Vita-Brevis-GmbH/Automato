// Bereiche der Übersicht (0.117.0, Anwender: „Dashboard erweitern mit Schnellansichten, Grafen, eventuell Auswahl an Content
// wo ich an- und abwählen kann“ – gewählt: Findings & Läufe grafisch, Schnellansichten, Routing-Änderungen, Applikationen &
// Regeln, Diagramm mit Objekttypen). Jeder Bereich ist an- und abwählbar und verschiebbar; die Auswahl merkt sich der Browser.
// 0.122.0 (Anwender 2026-11-03: „statische Boxen, welche nicht zueinander passen und zeilenweise seitlich gescrollt werden
// muss … etwas dynamischer und mit besseren Ansichten“): Raster mit gleich hohen Bereichen je Zeile, Breite je Bereich
// wählbar (⅓, ½, ganz), Verschieben per Ziehen, Läufe als Liste ohne Querscrollen, Zonen als waagrechte Balken.
import { useEffect, useState } from "react";
import {
  fetchApps,
  fetchDocStates,
  fetchObjectSummary,
  fetchRoutingChangeSummary,
  fetchRunsDaily,
  type Application,
  type DocState,
  type DocStates,
  type FindingSummary,
  type ModuleKey,
  type ObjectSummary,
  type RoutingChangeSummary,
  type RuleSummary,
  type Run,
  type RunsDay,
  type System,
} from "../../api";
import { L } from "../../i18n";
import { FINDING_GROUPS, findingLabel } from "../../refs";
import { fmtTime, Loading, RunStatusChip } from "../../ui";
import { SUMMARY_PLATFORMS } from "../../Runs";
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
  | "objects"
  | "docs";

// Breite im Raster: s = ein Drittel, m = halb, l = ganze Breite (schmale Fenster: s und m werden breiter)
export type WidgetSize = "s" | "m" | "l";
export const SIZES: { key: WidgetSize; label: string; title: string }[] = [
  { key: "s", label: "⅓", title: L("ein Drittel der Breite", "one third of the width") },
  { key: "m", label: "½", title: L("halbe Breite", "half width") },
  { key: "l", label: L("ganz", "full"), title: L("ganze Breite", "full width") },
];

export interface WidgetMeta {
  key: WidgetKey;
  label: string;
  module?: ModuleKey; // nur, wenn das Modul für den Mandanten an ist
  staff?: boolean; // nicht für Kunden-Benutzer (Betrieb)
  tenant?: boolean; // braucht einen gewählten Mandanten
  size: WidgetSize; // Vorgabe der Breite
}

export const WIDGETS: WidgetMeta[] = [
  { key: "health", label: L("Betriebszustand", "Health"), staff: true, size: "l" },
  { key: "kpis", label: L("Kennzahlen", "Key figures"), size: "l" },
  { key: "quick", label: L("Schnellansichten", "Quick views"), size: "l" },
  { key: "findings", label: L("Offene Findings je Gruppe", "Open findings per group"), module: "findings", size: "m" },
  { key: "runschart", label: L("Läufe der letzten 14 Tage", "Runs of the last 14 days"), module: "worker", staff: true, size: "m" },
  { key: "runs", label: L("Letzte Läufe", "Recent runs"), module: "worker", staff: true, size: "m" },
  { key: "zones", label: L("Regeln je Zone (Ziel)", "Rules per zone (destination)"), size: "m" },
  { key: "routing", label: L("Routing-Änderungen (7 Tage)", "Routing changes (7 days)"), module: "analyse", staff: true, tenant: true, size: "m" },
  { key: "apps", label: L("Applikationen", "Applications"), module: "doku", tenant: true, size: "m" },
  { key: "rules", label: L("Regeln je Firewall und Aktion", "Rules per firewall and action"), size: "m" },
  { key: "objects", label: L("Objekte nach Typ", "Objects by type"), tenant: true, size: "m" },
  // 0.129.0 (Anwender: „wie der Stand der Neuberechnungen der Dokumentationen ist“)
  { key: "docs", label: L("Stand der Doku", "Documentation state"), module: "doku", staff: true, tenant: true, size: "m" },
];

const STORE = "automato_overview_layout";

interface Layout {
  order: WidgetKey[];
  hidden: WidgetKey[];
  sizes: Partial<Record<WidgetKey, WidgetSize>>;
}

function readLayout(): Layout | null {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<Layout>;
    const sizes = v.sizes && typeof v.sizes === "object" ? v.sizes : {};
    return { order: Array.isArray(v.order) ? v.order : [], hidden: Array.isArray(v.hidden) ? v.hidden : [], sizes };
  } catch {
    return null;
  }
}

// Reihenfolge und ausgeblendete Bereiche (je Browser); neue Bereiche erscheinen hinten
export function useLayout() {
  const all = WIDGETS.map((w) => w.key);
  const [layout, setLayout] = useState<Layout>(() => readLayout() ?? { order: all, hidden: [], sizes: {} });
  const order = [...layout.order.filter((k) => all.includes(k)), ...all.filter((k) => !layout.order.includes(k))];
  const save = (next: Layout) => {
    setLayout(next);
    try {
      localStorage.setItem(STORE, JSON.stringify(next));
    } catch {
      // ohne Speicher (privates Fenster): nur für diese Sitzung
    }
  };
  const sizes = layout.sizes ?? {};
  return {
    order,
    hidden: new Set(layout.hidden),
    size: (k: WidgetKey): WidgetSize => sizes[k] ?? WIDGETS.find((w) => w.key === k)?.size ?? "m",
    setSize: (k: WidgetKey, sz: WidgetSize) => save({ ...layout, order, sizes: { ...sizes, [k]: sz } }),
    toggle: (k: WidgetKey) =>
      save({ ...layout, order, hidden: layout.hidden.includes(k) ? layout.hidden.filter((x) => x !== k) : [...layout.hidden, k] }),
    move: (k: WidgetKey, by: -1 | 1) => {
      const i = order.indexOf(k);
      const j = i + by;
      if (i < 0 || j < 0 || j >= order.length) return;
      const next = [...order];
      [next[i], next[j]] = [next[j], next[i]];
      save({ ...layout, order: next });
    },
    // per Ziehen: k vor target setzen (auf sich selbst: nichts)
    moveTo: (k: WidgetKey, target: WidgetKey) => {
      if (k === target) return;
      const next = order.filter((x) => x !== k);
      const at = next.indexOf(target);
      next.splice(at < 0 ? next.length : at, 0, k);
      save({ ...layout, order: next });
    },
    reset: () => save({ order: all, hidden: [], sizes: {} }),
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
        <span className="faint">
          {L(
            "an- und abwählen, Breite wählen, verschieben (ziehen oder ↑ ↓) – gilt für diesen Browser",
            "show/hide, choose the width, reorder (drag or ↑ ↓) – for this browser",
          )}
        </span>
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
              <li
                key={k}
                data-widget={k}
                className={off ? "faint" : ""}
                draggable
                onDragStart={(e) => e.dataTransfer.setData("text/plain", k)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const from = e.dataTransfer.getData("text/plain") as WidgetKey;
                  if (from) layout.moveTo(from, k);
                }}
              >
                <span className="drag-grip" aria-hidden>
                  ⠿
                </span>
                <label>
                  <input type="checkbox" checked={!layout.hidden.has(k)} onChange={() => layout.toggle(k)} /> {w.label}
                </label>
                {off && <span className="chip">{L("Modul aus bzw. nicht für diese Rolle", "module off or not for this role")}</span>}
                <span className="spacer" />
                <span className="seg" role="group" aria-label={L("Breite", "Width")}>
                  {SIZES.map((sz) => (
                    <button
                      key={sz.key}
                      className={layout.size(k) === sz.key ? "on" : ""}
                      data-size={sz.key}
                      title={sz.title}
                      onClick={() => layout.setSize(k, sz.key)}
                    >
                      {sz.label}
                    </button>
                  ))}
                </span>
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
      <div className="dash-body">{children}</div>
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
            {days.map((d, i) => (
              <span key={d.day}>{(days.length - 1 - i) % 2 === 0 ? `${d.day.slice(8, 10)}.${d.day.slice(5, 7)}` : ""}</span>
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

const DOC_STATE: Record<DocState, { label: string; chip: string; title: string }> = {
  building: {
    label: L("rechnet gerade", "computing"),
    chip: "info",
    title: L("Ein Stand dieser Applikation wird gerade neu berechnet", "A state of this application is being recomputed"),
  },
  stale: {
    label: L("veraltet", "outdated"),
    chip: "warn",
    title: L(
      "Seit der letzten Berechnung hat sich etwas geändert (Lauf, Konfiguration, Applikation) – neu beim Vorrechnen bzw. beim nächsten Öffnen",
      "Something changed since the last computation (run, configuration, application) – recomputed by the pre-computation or on next opening",
    ),
  },
  never: {
    label: L("noch nie berechnet", "never computed"),
    chip: "na",
    title: L(
      "Doku bzw. Verbindungen noch nie berechnet – im Hintergrund vorgerechnet (wenn eingeschaltet), sonst beim ersten Öffnen",
      "Documentation or connections never computed – pre-computed in the background (if enabled), otherwise on first opening",
    ),
  },
  fresh: { label: L("aktuell", "current"), chip: "ok", title: L("Alle Stände passen", "All states are current") },
};
const DOC_ORDER: DocState[] = ["building", "stale", "never", "fresh"];

// Fortschritt einer Berechnung (0.133.1, Anwender: „der "rechnet gerade" verschwindet immer wieder … er aktuell bei den
// Verbindungen am Messen ist“): Wege fertig von allen und seit wann
function DocProgress({ p }: { p: { done: number; total: number; seconds: number } }) {
  const min = Math.floor(p.seconds / 60);
  const since = min >= 1 ? `${min} min` : `${p.seconds} s`;
  return (
    <span className="faint doc-progress" title={L("Verbindungen: Wege fertig von allen", "Connections: paths done of all")}>
      {L(` · ${p.done} von ${p.total} Wegen · seit ${since}`, ` · ${p.done} of ${p.total} paths · for ${since}`)}
    </span>
  );
}

// Berechnung fehlgeschlagen (0.133.2, Anwender: „Er bricht immer bei dieser Anzahl ab“): Fehler sichtbar statt still neu zu
// beginnen – vorgerechnet wird erst wieder, wenn sich die Eingaben ändern; Einzelheiten im Log des Orchestrators
function DocError({ a }: { a: DocStates["apps"][number] }) {
  const kind = (["connections", "doc"] as const).find((k) => a.kinds[k].error);
  const what = kind === "doc" ? L("Doku", "documentation") : L("Verbindungen", "connections");
  const at = kind ? a.kinds[kind].error?.at : undefined;
  return (
    <span
      className="chip crit doc-error"
      style={{ marginLeft: 6 }}
      title={L(
        `${what}: ${a.error}${at ? ` (${fmtTime(at)})` : ""} – erneut, sobald sich etwas ändert; Einzelheiten im Log des Orchestrators`,
        `${what}: ${a.error}${at ? ` (${fmtTime(at)})` : ""} – again once something changes; details in the orchestrator log`,
      )}
    >
      {L("Fehler", "error")}
    </span>
  );
}

// Stand der Doku (0.129.0): je Applikation, ob Doku und Verbindungen aktuell sind, gerade rechnen, veraltet sind bzw. noch
// nie berechnet wurden – zuerst die, die nicht aktuell sind; solange etwas rechnet, alle 5 s neu
export function DocsCard({ tenantId, onGo }: { tenantId: number; onGo: Go }) {
  const [data, setData] = useState<DocStates | null>(null);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);
  const [all, setAll] = useState(false);
  useEffect(() => {
    let alive = true;
    fetchDocStates(tenantId)
      .then((d) => {
        if (alive) {
          setData(d);
          setError(false);
        }
      })
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [tenantId, tick]);
  const busy = !!data && (data.warming || data.counts.building > 0 || (data.refreshing ?? 0) > 0);
  useEffect(() => {
    if (!busy) return;
    const t = setTimeout(() => setTick((n) => n + 1), 5000);
    return () => clearTimeout(t);
  }, [busy, tick]);
  const title = L("Stand der Doku", "Documentation state");
  if (error) return <Card id="dash-docs" title={title}>{empty(L("Stand nicht lesbar", "State not readable"))}</Card>;
  if (data === null) return <Card id="dash-docs" title={title}><Loading /></Card>;
  if (!data.enabled) {
    return (
      <Card id="dash-docs" title={title}>
        {empty(L("Doku als Stand ist ausgeschaltet – sie wird bei jedem Öffnen berechnet", "Documentation states are off – computed on every opening"))}
      </Card>
    );
  }
  const rank = (s: DocState) => DOC_ORDER.indexOf(s);
  const apps = [...data.apps].sort((a, b) => rank(a.state) - rank(b.state) || a.name.localeCompare(b.name));
  const open = apps.filter((a) => a.state !== "fresh");
  const shown = all ? apps : open.slice(0, 8);
  const time = (a: DocStates["apps"][number]) => {
    const t = [a.kinds.doc.built_at, a.kinds.connections.built_at].filter(Boolean).sort().pop() ?? null;
    return t ? fmtTime(t) : "–";
  };
  return (
    <Card
      id="dash-docs"
      title={title}
      foot={
        <span className="faint">
          {data.last_built_at
            ? L(`zuletzt berechnet ${fmtTime(data.last_built_at)}`, `last computed ${fmtTime(data.last_built_at)}`)
            : L("noch nichts berechnet", "nothing computed yet")}
          {data.warm_days > 0 &&
            (data.warm_all
              ? L(
                  ` · vorgerechnet werden Doku und Verbindungen aller Applikationen, weitere Stände (Sprache, Namen, Kunde) die in den letzten ${data.warm_days} Tagen geöffneten`,
                  ` · pre-computed: documentation and connections of all applications, further states (language, names, customer) those opened in the last ${data.warm_days} days`,
                )
              : L(
                  ` · vorgerechnet werden die in den letzten ${data.warm_days} Tagen geöffneten`,
                  ` · pre-computed: those opened in the last ${data.warm_days} days`,
                ))}
        </span>
      }
    >
      <div className="dash-docs-state" style={{ padding: "8px 16px 0", display: "flex", gap: 6, flexWrap: "wrap" }}>
        {data.warming && <span className="chip info" id="docs-warming">{L("Vorrechnen läuft", "Pre-computing")}</span>}
        {data.round && data.round.apps_total > 0 && (
          <span
            className="chip info"
            id="docs-round"
            title={L(
              "Was beim Start des Durchgangs ausstand, wird je Applikation einmal gerechnet – neue Läufe brechen nichts ab, danach folgt ein weiterer Durchgang mit dem neuesten Stand",
              "What was pending when the round started is computed once per application – new runs do not abort it, another round with the latest data follows",
            )}
          >
            {L(
              `Durchgang ${data.round.number}: ${data.round.apps_done} von ${data.round.apps_total}`,
              `Round ${data.round.number}: ${data.round.apps_done} of ${data.round.apps_total}`,
            )}
            {data.round.again && L(" · danach erneut", " · then again")}
          </span>
        )}
        {(data.refreshing ?? 0) > 0 && (
          <span className="chip info" id="docs-refreshing" title={L("Befunde der Applikationen werden nachgezogen", "Findings of the applications are being updated")}>
            {L("Befunde nachziehen", "Updating findings")}
          </span>
        )}
        <button className="linkbtn" onClick={() => setTick((n) => n + 1)}>
          {L("Aktualisieren", "Refresh")}
        </button>
      </div>
      <div className="kpis wrap" style={{ margin: 12 }}>
        {DOC_ORDER.map((s) => (
          <div key={s} className="kpi" data-tile={s} title={DOC_STATE[s].title}>
            <div className="k-label">{DOC_STATE[s].label}</div>
            <div className={`k-val sm ${s === "stale" && data.counts.stale ? "warn" : ""}`}>{data.counts[s]}</div>
          </div>
        ))}
      </div>
      {open.length === 0 && !all ? (
        <div className="empty" style={{ padding: "0 16px 12px" }}>
          {L(`Alle ${data.total} Applikationen aktuell.`, `All ${data.total} applications current.`)}{" "}
          {data.total > 0 && (
            <button className="linkbtn" onClick={() => setAll(true)}>
              {L("alle zeigen", "show all")}
            </button>
          )}
        </div>
      ) : (
        <table className="compact" id="docs-apps" style={{ margin: "0 0 8px" }}>
          <thead>
            <tr>
              <th>{L("Applikation", "Application")}</th>
              <th>{L("Zustand", "State")}</th>
              <th title={L("Doku · Verbindungen: aktuelle von allen Ständen", "Documentation · connections: current of all states")}>
                {L("Doku · Verbindungen", "Doc · connections")}
              </th>
              <th>{L("berechnet", "computed")}</th>
              <th title={L("längste Berechnung", "longest computation")}>{L("Dauer", "Duration")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((a) => (
              <tr key={a.id} data-app={a.name} data-state={a.state}>
                <td>
                  <button className="linkbtn" onClick={() => onGo("apps", { app: String(a.id) })}>
                    {a.name}
                  </button>
                </td>
                <td>
                  <span className={`chip ${DOC_STATE[a.state].chip}`} title={DOC_STATE[a.state].title}>
                    {DOC_STATE[a.state].label}
                  </span>
                  {a.kinds.connections.progress && <DocProgress p={a.kinds.connections.progress} />}
                  {a.error && <DocError a={a} />}
                  {a.auto && (
                    <span className="faint" title={L("wird im Hintergrund vorgerechnet", "pre-computed in the background")}>
                      {" "}
                      {L("· wird vorgerechnet", "· pre-computing")}
                    </span>
                  )}
                </td>
                <td className="mono">
                  {(["doc", "connections"] as const).map((k) => `${a.kinds[k].fresh}/${a.kinds[k].variants}`).join(" · ")}
                </td>
                <td className="nowrap">{time(a)}</td>
                <td className="num">{Math.max(a.kinds.doc.seconds, a.kinds.connections.seconds).toFixed(1)} s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(all || apps.length > shown.length) && open.length > 0 && (
        <div style={{ padding: "0 16px 10px" }}>
          <button className="linkbtn" onClick={() => setAll((v) => !v)}>
            {all ? L("nur nicht aktuelle", "only not current") : L(`alle ${apps.length} zeigen`, `show all ${apps.length}`)}
          </button>
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

// Letzte Läufe als Liste (0.122.0): Status, System mit Ausführung und Einträgen, Meldung umbrochen, Zeit – kein Querscrollen
export function RunsList({
  runs,
  systemName,
  canCancel,
  onCancel,
}: {
  runs: Run[];
  systemName: (id: number | null) => string;
  canCancel: boolean;
  onCancel: (id: string) => void;
}) {
  return (
    <ul className="run-list">
      {runs.map((r) => {
        const open = r.status === "queued" || r.status === "running";
        const failed = r.status === "failed";
        const facts = [
          r.execution === "external" ? "Agent" : L("zentral", "central"),
          r.trigger === "schedule" ? L("Zeitplan", "schedule") : "",
          r.rule_count != null ? L(`${r.rule_count} ${r.rule_count === 1 ? "Eintrag" : "Einträge"}`, `${r.rule_count} ${r.rule_count === 1 ? "entry" : "entries"}`) : "",
        ].filter(Boolean);
        return (
          <li key={r.id} data-run={r.id} title={`#${r.id.slice(0, 8)}${r.message ? ` – ${r.message}` : ""}`}>
            <RunStatusChip status={r.status} />
            <div className="run-main">
              <div className="run-line">
                <b>{systemName(r.source_system_id)}</b> <span className="faint">· {facts.join(" · ")}</span>
              </div>
              {r.message && (failed || open || SUMMARY_PLATFORMS.has(r.platform)) && (
                <div className={failed ? "run-msg err" : "run-msg"}>{r.message}</div>
              )}
            </div>
            <div className="run-when mono">
              {fmtTime(r.finished_at ?? r.started_at ?? r.queued_at)}
              {canCancel && open && (
                <div>
                  <button
                    className="linkbtn"
                    title={L(
                      "Lauf beenden (z. B. hängt er nach einem Neustart des Agenten). Liefert der Agent doch noch ab, wird das Ergebnis eingespielt.",
                      "End the run (e.g. if it hangs after an agent restart). If the agent still delivers, the result is ingested.",
                    )}
                    onClick={() => onCancel(r.id)}
                  >
                    {L("Abbrechen", "Cancel")}
                  </button>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// Regeln je Ziel-Zone als waagrechte Balken (0.122.0, vorher senkrecht mit abgeschnittenen Namen); Klick sucht die Zone
export function ZonesCard({ rules, onGo }: { rules: RuleSummary | null; onGo: Go }) {
  const rows = (rules?.by_dst_zone ?? []).slice(0, 12).map(([z, n]) => ({ key: z || "-", label: z || "–", value: n }));
  return (
    <Card
      id="dash-zones"
      title={L("Regeln je Zone (Ziel)", "Rules per zone (destination)")}
      foot={
        <button className="linkbtn" onClick={() => onGo("regeln")}>
          {L("Regeln & Contracts →", "Rules & contracts →")}
        </button>
      }
    >
      {rules === null ? (
        <Loading />
      ) : rows.length === 0 ? (
        empty(L("Nach dem ersten Lauf erscheinen hier die Ziel-Zonen.", "The destination zones appear here after the first run."))
      ) : (
        <HBars rows={rows} onPick={(z) => onGo("regeln", z !== "-" ? { q: z } : undefined)} />
      )}
    </Card>
  );
}
