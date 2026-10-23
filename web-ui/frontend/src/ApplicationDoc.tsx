// Applikations-Doku (Dev-Befund 0.14.0): Klick auf eine Applikation öffnet ihre Doku. Oben die
// Übersicht (VMs, EPGs, Regeln spezifisch/unspezifisch, Befunde), darunter alle VMs der
// Applikation, ihre EPGs im ACI und die Regeln wie in „Regeln & Contracts“ – filterbar nach
// System/VM, EPG, Richtung (aus Sicht des Systems), Art und Bezug. Das Regel-Detail erklärt,
// warum die Regel für die Applikation gilt; Links in Regeln, Objekte und Findings.
import { ReactNode, useEffect, useMemo, useState } from "react";
import {
  AppDocEpg,
  AppDocFinding,
  AppDocHit,
  AppDocSystem2,
  ApplicationDoc,
  DocScope,
  fetchAppDoc,
  fetchSystems,
  Purpose,
  Rule,
  System,
} from "./api";
import type { View, ViewParams } from "./App";
import { DocTableView, tablesMarkdown } from "./AppDoc";
import type { Me } from "./auth";
import { findingLabel, serviceNames } from "./refs";
import RuleDetailDrawer from "./RuleDetail";
import { NameCell } from "./RulesTable";
import { siteLabel } from "./sites";
import { ActionBadge, Dash, download, Empty, ErrorBox, Expandable, Icon, Loading } from "./ui";

type Go = (v: View, p?: ViewParams) => void;

export const PURPOSE: Record<Purpose, [string, string]> = {
  dev: ["info", "Dev"],
  test: ["warn", "Test"],
  prod: ["local", "Prod"],
};

export function PurposeChip({ value }: { value: Purpose | null }) {
  if (!value) return <Dash />;
  const [cls, label] = PURPOSE[value];
  return (
    <span className={`chip ${cls}`} title="Verwendungszweck">
      {label}
    </span>
  );
}

// Art des Bezugs: spezifisch = das System bzw. die EPG selbst; sonst unspezifisch
const SCOPE: Record<DocScope, [string, string, string]> = {
  specific: ["ok", "spezifisch", "nennt das System bzw. die EPG selbst (direkt, Gruppe, EPG, ESG)"],
  subnet: ["warn", "Subnetz", "gilt über ein Netz bzw. einen Bereich, der das System enthält"],
  any: ["warn", "any", "gilt für alle Adressen (any)"],
  vzany: ["warn", "vzAny", "gilt für alle EPGs der VRF (vzAny)"],
  pref_group: ["warn", "Preferred Group", "die Mitglieder der Preferred Group sprechen frei miteinander"],
};
const RANK: Record<DocScope, number> = { specific: 0, subnet: 1, vzany: 2, pref_group: 3, any: 4 };
const DIRECTION: Record<string, [string, string]> = {
  in: ["↓ eingehend", "das System ist Ziel bzw. Provider"],
  out: ["↑ ausgehend", "das System ist Quelle bzw. Consumer"],
};
const POWER: Record<string, [string, string]> = {
  poweredOn: ["ok", "an"],
  poweredOff: ["na", "aus"],
  suspended: ["warn", "pausiert"],
};
const CRIT_LABEL: Record<string, string> = { low: "niedrig", medium: "mittel", high: "hoch", critical: "kritisch" };

function ScopeChip({ scope }: { scope: DocScope }) {
  const [cls, label, title] = SCOPE[scope] ?? ["na", scope, ""];
  return (
    <span className={`chip ${cls}`} title={title}>
      {label}
    </span>
  );
}

function best(hits: AppDocHit[]): DocScope {
  return hits.reduce<DocScope>((b, h) => (RANK[h.scope] < RANK[b] ? h.scope : b), "any");
}

function Kpi({ label, value, sub, tone, onClick }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string; onClick?: () => void }) {
  const body = (
    <>
      <div className="k-label">{label}</div>
      <div className={`k-val ${tone ?? ""}`}>{value}</div>
      {sub && <div className="k-sub">{sub}</div>}
    </>
  );
  return onClick ? (
    <button className="kpi" onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="kpi">{body}</div>
  );
}

interface Filters {
  system: string;
  epg: string;
  direction: string;
  domain: string;
  scope: string; // "" | unspecific | DocScope
  q: string;
}
const NO_FILTER: Filters = { system: "", epg: "", direction: "", domain: "", scope: "", q: "" };

export default function ApplicationDocView({
  me,
  id,
  reload,
  onBack,
  onGo,
  onManage,
  onEdit,
}: {
  me: Me;
  id: number;
  reload: number; // nach Änderungen an Stammdaten/Mitgliedern neu laden
  onBack: () => void;
  onGo?: Go;
  onManage: () => void; // Mitglieder (Drawer)
  onEdit: () => void; // Stammdaten bearbeiten
}) {
  const [doc, setDoc] = useState<ApplicationDoc | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [f, setF] = useState<Filters>(NO_FILTER);
  const [rule, setRule] = useState<number | null>(null);
  const [showTables, setShowTables] = useState(false);
  const [showEmpty, setShowEmpty] = useState(false);

  useEffect(() => {
    setErr(null);
    fetchAppDoc(id)
      .then(setDoc)
      .catch(setErr);
  }, [id, reload]);
  useEffect(() => {
    fetchSystems()
      .then(setSystems)
      .catch(() => setSystems([]));
  }, []);

  const names = useMemo(() => {
    const m = new Map<string, string>();
    doc?.systems.forEach((s) => m.set(s.key, s.name));
    doc?.epgs.forEach((e) => m.set(e.key, e.label));
    return m;
  }, [doc]);
  const byKey = useMemo(() => {
    const m = new Map<string, AppDocSystem2 | AppDocEpg>();
    doc?.systems.forEach((s) => m.set(s.key, s));
    doc?.epgs.forEach((e) => m.set(e.key, e));
    return m;
  }, [doc]);

  if (err) return <ErrorBox error={err} prefix="Doku laden" />;
  if (!doc) return <Loading>Erzeuge Doku…</Loading>;
  const a = doc.application;
  const s = doc.summary;
  const sourceName = (sid: number) => systems.find((x) => x.id === sid)?.name ?? `#${sid}`;
  const titles = new Map(doc.tables.map((t) => [t.index, t.title]));
  const epgLabel = f.epg ? names.get(f.epg) : null;

  // Treffer nach den Filtern; EPG = die EPG selbst oder Systeme in ihr – ACI-Contracts eines
  // Systems nur über diese EPG (0.42.3: eine VM in mehreren EPGs)
  const hits = doc.hits.filter((h) => {
    if (f.system && h.system !== f.system) return false;
    if (f.epg) {
      const sys = byKey.get(h.system);
      const inEpg = sys && "epgs" in sys && epgLabel ? sys.epgs.includes(epgLabel) : false;
      if (h.system !== f.epg && !inEpg) return false;
      if (h.epg && epgLabel && h.epg !== epgLabel) return false;
    }
    if (f.direction && h.direction !== f.direction) return false;
    if (f.scope === "unspecific" && h.scope === "specific") return false;
    if (f.scope && f.scope !== "unspecific" && h.scope !== f.scope) return false;
    return true;
  });
  const hitsOf = new Map<number, AppDocHit[]>();
  hits.forEach((h) => hitsOf.set(h.rule_id, [...(hitsOf.get(h.rule_id) ?? []), h]));
  const needle = f.q.trim().toLowerCase();
  const rules = doc.rules.filter((r) => {
    if (!hitsOf.has(r.id)) return false;
    if (f.domain && r.domain !== f.domain) return false;
    if (!needle) return true;
    const text = [r.name, r.aci?.contract, r.aci?.subject, ...r.sources, ...r.destinations, ...r.service_names, ...(hitsOf.get(r.id) ?? []).map((h) => `${h.via} ${h.counterpart}`)]
      .join("\n")
      .toLowerCase();
    return text.includes(needle);
  });
  const findingsOf = (rid: number) => doc.findings.filter((x) => x.rule_id === rid);
  const set = (patch: Partial<Filters>) => setF((prev) => ({ ...prev, ...patch }));
  const filtered = JSON.stringify(f) !== JSON.stringify(NO_FILTER);
  const scrollToRules = () => document.getElementById("app-rules")?.scrollIntoView({ behavior: "smooth", block: "start" });
  const print = () => {
    setShowTables(true);
    setTimeout(() => window.print(), 50);
  };
  const tables = doc.tables.filter((t) => showEmpty || t.rows.length > 0);
  const selected = rule != null ? doc.rules.find((r) => r.id === rule) ?? null : null;

  return (
    <div className="printable">
      <div className="filterbar no-print">
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          ← Applikationen
        </button>
        <div className="grow" />
        <button className="btn btn-ghost btn-sm" onClick={onManage} title="Mitglieder (APs, EPGs, Server) ansehen und pflegen">
          Mitglieder
        </button>
        {me.isOperator && (
          <button className="btn btn-ghost btn-sm" onClick={onEdit}>
            Bearbeiten
          </button>
        )}
        <button className="btn btn-ghost btn-sm" onClick={() => download(`${a.name}.md`, docMarkdown(doc, sourceName))}>
          <Icon name="download" size={14} /> Export (Markdown)
        </button>
        <button className="btn btn-ghost btn-sm" onClick={print} title="Drucken oder als PDF sichern">
          Drucken / PDF
        </button>
      </div>

      <div className="card app-head">
        <div className="card-body pad">
          <h1 className="app-title">{a.name}</h1>
          {(a.external_name || a.internal_name) && (
            <div className="app-names">
              {a.external_name && (
                <span>
                  <span className="faint">extern</span> {a.external_name}
                </span>
              )}
              {a.internal_name && (
                <span>
                  <span className="faint">intern</span> <span className="mono">{a.internal_name}</span>
                </span>
              )}
            </div>
          )}
          <div className="chips" style={{ marginTop: 8 }}>
            {a.purpose && <PurposeChip value={a.purpose} />}
            {a.code && <span className="chip local">Kürzel {a.code}</span>}
            {doc.tenant_code && <span className="chip">GG {doc.tenant_code}</span>}
            {a.criticality && <span className="chip warn">Kritikalität {CRIT_LABEL[a.criticality] ?? a.criticality}</span>}
            {a.owner && <span className="chip">Verantwortlich: {a.owner}</span>}
            {a.site ? <span className="chip">Standort {siteLabel(a.site)}</span> : <span className="chip na">ohne Standort</span>}
          </div>
          {a.description && <p style={{ whiteSpace: "pre-wrap", margin: "10px 0 0" }}>{a.description}</p>}
        </div>
      </div>

      <div className="kpis wrap">
        <Kpi label="VMs" value={s.vms} sub={s.servers ? `+ ${s.servers} Server ohne VM` : "aus dem vCenter"} />
        <Kpi
          label="EPGs im ACI"
          value={s.epgs}
          sub={`${s.rules} Regeln · ${s.firewall} Firewall · ${s.aci} ACI${s.impossible ? ` · ${s.impossible} nicht möglich` : ""}`}
        />
        <Kpi
          label="Regeln spezifisch"
          value={s.specific}
          tone="ok"
          sub="nennen System bzw. EPG selbst"
          onClick={() => {
            setF({ ...NO_FILTER, scope: "specific" });
            scrollToRules();
          }}
        />
        <Kpi
          label="Regeln unspezifisch"
          value={s.unspecific}
          tone={s.unspecific ? "warn" : ""}
          sub={
            (["subnet", "any", "vzany", "pref_group"] as DocScope[])
              .filter((k) => s.by_scope[k])
              .map((k) => `${SCOPE[k][1]} ${s.by_scope[k]}`)
              .join(" · ") || "Subnetz, any, vzAny"
          }
          onClick={() => {
            setF({ ...NO_FILTER, scope: "unspecific" });
            scrollToRules();
          }}
        />
        <Kpi
          label="Findings"
          value={s.findings}
          tone={s.findings ? "warn" : "ok"}
          sub={s.findings ? "offene Befunde der Applikation" : "keine offenen Befunde"}
          onClick={() => document.getElementById("app-findings")?.scrollIntoView({ behavior: "smooth" })}
        />
      </div>

      <SystemsCard
        items={doc.systems}
        sourceName={sourceName}
        onGo={onGo}
        onPick={(key) => {
          setF({ ...NO_FILTER, system: key });
          scrollToRules();
        }}
      />
      <EpgsCard
        items={doc.epgs}
        onPick={(key) => {
          setF({ ...NO_FILTER, epg: key });
          scrollToRules();
        }}
      />

      <div className="card" id="app-rules" style={{ marginTop: 14 }}>
        <div className="card-head">
          <h2>Regeln &amp; Contracts</h2>
          <span className="mono faint" style={{ fontSize: 12 }}>
            {rules.length}
            {rules.length !== doc.rules.length ? ` von ${doc.rules.length}` : ""}
          </span>
          <div className="spacer" />
          {s.impossible ? (
            <span
              className="faint"
              style={{ fontSize: 12 }}
              title="Regeln, die die Systeme nach dem Routing ihrer Firewall nicht sehen können (Ein-/Ausgang passt nicht) – sie gehören nicht zur Applikation"
            >
              {s.impossible} routingtechnisch unmögliche ausgelassen
            </span>
          ) : null}
          {filtered && (
            <button className="btn btn-ghost btn-sm no-print" onClick={() => setF(NO_FILTER)}>
              Filter zurücksetzen
            </button>
          )}
        </div>
        <div className="card-body pad no-print" style={{ paddingBottom: 0 }}>
          <div className="filterbar" style={{ marginBottom: 10 }}>
            <div className="search">
              <span className="ico">
                <Icon name="search" />
              </span>
              <input placeholder="Regel, Objekt, Dienst, Gegenstelle…" value={f.q} onChange={(e) => set({ q: e.target.value })} />
            </div>
            <select className="sel" value={f.system} onChange={(e) => set({ system: e.target.value })} title="Regeln eines Systems bzw. einer VM">
              <option value="">System / VM: alle</option>
              {doc.systems.map((x) => (
                <option key={x.key} value={x.key}>
                  {x.kind === "vm" ? "VM" : "Server"} {x.name}
                </option>
              ))}
            </select>
            <select className="sel" value={f.epg} onChange={(e) => set({ epg: e.target.value })} title="Regeln einer EPG und der Systeme in ihr">
              <option value="">EPG: alle</option>
              {doc.epgs.map((x) => (
                <option key={x.key} value={x.key}>
                  {x.label}
                </option>
              ))}
            </select>
            <select
              className="sel"
              value={f.direction}
              onChange={(e) => set({ direction: e.target.value })}
              title="Aus Sicht des Systems bzw. der VM: eingehend = Ziel/Provider, ausgehend = Quelle/Consumer"
            >
              <option value="">Richtung: beide</option>
              <option value="in">eingehend</option>
              <option value="out">ausgehend</option>
            </select>
            <select className="sel" value={f.domain} onChange={(e) => set({ domain: e.target.value })}>
              <option value="">Art: alle</option>
              <option value="firewall">Firewall-Regeln</option>
              <option value="aci_contract">ACI-Contracts</option>
            </select>
            <select className="sel" value={f.scope} onChange={(e) => set({ scope: e.target.value })} title="Wie die Regel das System erfasst">
              <option value="">Bezug: alle</option>
              <option value="specific">spezifisch</option>
              <option value="unspecific">unspezifisch (Subnetz, any, vzAny)</option>
              <option value="subnet">nur Subnetz</option>
              <option value="any">nur any</option>
              <option value="vzany">nur vzAny</option>
              <option value="pref_group">nur Preferred Group</option>
            </select>
          </div>
          {f.system || f.epg ? (
            <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
              Richtung aus Sicht von <b className="mono">{names.get(f.system || f.epg)}</b>.
            </div>
          ) : null}
        </div>
        <div className="card-body scroll viewport">
          {rules.length === 0 ? (
            <Empty title={doc.rules.length ? "Keine Treffer" : "Keine Regeln"}>
              {doc.rules.length ? "Filter anpassen." : "Für die Systeme dieser Applikation gibt es keine Regeln."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>Aktion</th>
                  <th>Herkunft</th>
                  <th>Richtung</th>
                  <th title="wie in der Regel; darunter die Systeme der Applikation, die das Objekt erfasst">Quelle / Consumer</th>
                  <th title="wie in der Regel; darunter die Systeme der Applikation, die das Objekt erfasst">Ziel / Provider</th>
                  <th>Dienste</th>
                  <th>Bezug</th>
                  <th title="Kategorie der Applikations-Doku (UC1) – Titel im Tooltip">Kat.</th>
                  <th title="nicht aufgelöste Objekte bzw. Befunde der Regel"></th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <RuleRow
                    key={r.id}
                    r={r}
                    hits={hitsOf.get(r.id) ?? []}
                    names={names}
                    titles={titles}
                    source={sourceName(r.source_system_id)}
                    findings={findingsOf(r.id).length}
                    onOpen={() => setRule(r.id)}
                    onGo={onGo}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <FindingsCard doc={doc} onRule={setRule} onGo={onGo} />

      <div className="card" style={{ marginTop: 14 }}>
        <div className="card-head">
          <h2>Doku nach Kategorien</h2>
          <span className="faint" style={{ fontSize: 12 }}>
            10 Kategorien nach UC1 – für Export und Druck
          </span>
          <div className="spacer" />
          {showTables && (
            <button className={`toggle no-print ${showEmpty ? "on" : ""}`} onClick={() => setShowEmpty((v) => !v)}>
              leere Kategorien zeigen
            </button>
          )}
          <button className="btn btn-ghost btn-sm no-print" onClick={() => setShowTables((v) => !v)}>
            {showTables ? "ausblenden" : "anzeigen"}
          </button>
        </div>
      </div>
      {showTables && (
        <div className="stack" style={{ marginTop: 10 }}>
          {tables.length === 0 ? (
            <div className="card">
              <Empty title="Noch keine Zugriffe kategorisiert">Die Systeme dieser Applikation haben keine Regeln.</Empty>
            </div>
          ) : (
            tables.map((t) => <DocTableView key={t.index} table={t} onRule={setRule} />)
          )}
        </div>
      )}

      {rule != null && (
        <RuleDetailDrawer
          ruleId={rule}
          systemName={sourceName}
          onClose={() => setRule(null)}
          onGo={onGo}
          onRule={setRule}
          intro={
            selected ? (
              <WhyBlock
                r={selected}
                hits={doc.hits.filter((h) => h.rule_id === selected.id)}
                byKey={byKey}
                titles={titles}
                source={sourceName(selected.source_system_id)}
                findings={findingsOf(selected.id)}
                onGo={onGo}
              />
            ) : null
          }
        />
      )}
    </div>
  );
}

// Objekte einer Seite, über die die Regel Systeme der Applikation erfasst – je Objekt (wie in der
// Regel: H_web01, N_srv16 mit Netz, any, EPG) die Systeme darin
interface SideGroup {
  object: string;
  value: string;
  scope: DocScope;
  systems: string[];
  hits: AppDocHit[];
}
function sideGroups(hits: AppDocHit[], side: string, names: Map<string, string>): SideGroup[] {
  const out = new Map<string, SideGroup>();
  for (const h of hits.filter((x) => x.side === side)) {
    const key = `${h.object}\u0000${h.value}`;
    const g = out.get(key) ?? { object: h.object || h.via, value: h.value, scope: h.scope, systems: [], hits: [] };
    const name = names.get(h.system) ?? h.system;
    if (!g.systems.includes(name)) g.systems.push(name);
    if (RANK[h.scope] < RANK[g.scope]) g.scope = h.scope;
    g.hits.push(h);
    out.set(key, g);
  }
  return [...out.values()];
}

function SideCell({ items, groups }: { items: string[]; groups: SideGroup[] }) {
  return (
    <>
      <div className="mono">
        <List items={items} />
      </div>
      {groups.map((g) => (
        <div key={`${g.object}-${g.value}`} className="appside" title="Objekt der Regel, das Systeme der Applikation erfasst">
          <span className="mono">{g.object}</span>
          {g.value && g.value !== g.object && <span className="faint mono"> {g.value}</span>} <ScopeChip scope={g.scope} />
          {g.scope === "specific" ? (
            <div className="faint mono">
              ↳ <List items={g.systems} max={4} inline />
            </div>
          ) : (
            // unspezifisch (any, Netz, vzAny): nur die Regel – die Systeme auf Knopfdruck (0.37.0)
            <Expandable items={g.systems} noun={["System", "Systeme"]} />
          )}
        </div>
      ))}
    </>
  );
}

function RuleRow({
  r,
  hits,
  names,
  titles,
  source,
  findings,
  onOpen,
  onGo,
}: {
  r: Rule;
  hits: AppDocHit[];
  names: Map<string, string>;
  titles: Map<number, string>;
  source: string;
  findings: number;
  onOpen: () => void;
  onGo?: Go;
}) {
  const directions = [...new Set(hits.map((h) => h.direction))];
  const tables = [...new Set(hits.map((h) => h.table))].sort((x, y) => x - y);
  const aci = r.domain === "aci_contract";
  const impossible = hits.filter((h) => h.relevance === "impossible");
  return (
    <tr className="clickable" onClick={onOpen}>
      <td className="mono">{r.id}</td>
      <td>
        <NameCell r={r} onGo={onGo} />
      </td>
      <td>
        <ActionBadge action={r.action} />
      </td>
      <td className="nowrap">
        <span className={`chip ${aci ? "local" : ""}`}>{aci ? "ACI" : "Firewall"}</span>
        <div className="faint">{source}</div>
      </td>
      <td className="nowrap">
        {directions.map((d) => (
          <div key={d} title={DIRECTION[d]?.[1]}>
            {DIRECTION[d]?.[0] ?? d}
          </div>
        ))}
      </td>
      <td>
        <SideCell items={r.sources} groups={sideGroups(hits, "src", names)} />
      </td>
      <td>
        <SideCell items={r.destinations} groups={sideGroups(hits, "dst", names)} />
      </td>
      <td className="mono">
        <List items={r.service_names.length ? serviceNames(r) : [...new Set(hits.map((h) => h.services).filter(Boolean))]} />
      </td>
      <td>
        <ScopeChip scope={best(hits)} />
        {impossible.length > 0 && (
          <div>
            <span className="chip crit" title={[...new Set(impossible.map((h) => h.relevance_text))].join("\n")}>
              {impossible.length === hits.length ? "nicht möglich" : "teils nicht möglich"}
            </span>
          </div>
        )}
      </td>
      <td className="nowrap">
        {tables.map((t) => (
          <span key={t} className="chip na" title={`${t}. ${titles.get(t)}`}>
            {t}
          </span>
        ))}
      </td>
      <td>
        {r.num_unmatched + findings > 0 ? (
          <span className="chip warn" title="nicht aufgelöste Objekte bzw. Befunde der Regel">
            {r.num_unmatched + findings}
          </span>
        ) : (
          <span className="chip ok">✓</span>
        )}
      </td>
    </tr>
  );
}

function List({ items, max = 3, inline }: { items: string[]; max?: number; inline?: boolean }) {
  if (!items.length) return <Dash />;
  const rest = items.length > max && (
    <span className="faint" title={items.slice(max).join("\n")}>
      {inline ? " " : ""}+{items.length - max} weitere
    </span>
  );
  if (inline)
    return (
      <span>
        {items.slice(0, max).join(", ")}
        {rest}
      </span>
    );
  return (
    <div className="objs">
      {items.slice(0, max).map((x) => (
        <span key={x}>{x}</span>
      ))}
      {rest}
    </div>
  );
}

// Im Regel-Detail: warum die Regel für die Applikation gilt – je System Richtung, Bezug und
// wie das System zur Applikation gehört; Name des Contracts bzw. der Firewall-Regel mit Link
function WhyBlock({
  r,
  hits,
  byKey,
  titles,
  source,
  findings,
  onGo,
}: {
  r: Rule;
  hits: AppDocHit[];
  byKey: Map<string, AppDocSystem2 | AppDocEpg>;
  titles: Map<number, string>;
  source: string;
  findings: ApplicationDoc["findings"];
  onGo?: Go;
}) {
  const aci = r.domain === "aci_contract";
  return (
    <div className="refblock why">
      <div className="faint" style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".04em" }}>
        Warum diese Regel für die Applikation gilt
      </div>
      <div className="refline" style={{ margin: "6px 0" }}>
        <span className={`chip ${aci ? "local" : ""}`}>{aci ? "ACI-Contract" : "Firewall-Regel"}</span>
        {onGo ? (
          <button className="linkbtn mono" title="In Regeln & Contracts öffnen" onClick={() => onGo("regeln", { rule: String(r.id) })}>
            {aci ? (r.aci?.contract ?? r.name) : r.name}
          </button>
        ) : (
          <b className="mono">{aci ? (r.aci?.contract ?? r.name) : r.name}</b>
        )}
        <span className="faint">
          {aci
            ? [r.aci?.subject && `Subject ${r.aci.subject}`, r.aci?.tenant && `Tenant ${r.aci.tenant}`, r.aci?.scope, source].filter(Boolean).join(" · ")
            : `${source} · #${r.id}`}
        </span>
        <ActionBadge action={r.action} />
      </div>
      <ul className="why-list">
        {(["dst", "src"] as const).flatMap((side) => {
          const role = side === "dst" ? (aci ? "Provider" : "Ziel") : aci ? "Consumer" : "Quelle";
          return sideGroups(hits, side, new Map()).map((g) => {
            const tables = [...new Set(g.hits.map((h) => h.table))];
            const counterparts = [...new Set(g.hits.flatMap((h) => h.counterpart.split(", ")).filter(Boolean))];
            return (
              <li key={`${side}-${g.object}-${g.value}`}>
                <b>
                  {role}: <span className="mono">{g.object}</span>
                </b>
                {g.value && g.value !== g.object && <span className="mono"> {g.value}</span>} <ScopeChip scope={g.scope} />{" "}
                <span className="faint">{DIRECTION[side === "dst" ? "in" : "out"][0]}</span>
                {counterparts.length > 0 && (
                  <span className="faint">
                    {" "}
                    · Gegenstelle <span className="mono">{counterparts.slice(0, 3).join(", ")}</span>
                    {counterparts.length > 3 ? ` +${counterparts.length - 3}` : ""}
                  </span>
                )}
                <div className="faint">Kategorie {tables.map((t) => `${t}. ${titles.get(t)}`).join(", ")}</div>
                {[...new Set(g.hits.filter((h) => h.relevance === "impossible").map((h) => h.relevance_text))].map((t) => (
                  <div key={t}>
                    <span className="chip crit">routingtechnisch nicht möglich</span> <span className="faint">{t}</span>
                  </div>
                ))}
                <ul className="why-systems">
                  {[...new Set(g.hits.map((h) => h.system))].map((key) => {
                    const sys = byKey.get(key);
                    const kind = !sys ? "" : "label" in sys ? "EPG" : sys.kind === "vm" ? "VM" : "Server";
                    const name = !sys ? key : "label" in sys ? sys.label : sys.name;
                    return (
                      <li key={key}>
                        {kind} <span className="mono">{name}</span>
                        {sys && <span className="faint"> – gehört zur Applikation über {sys.member}</span>}
                      </li>
                    );
                  })}
                </ul>
              </li>
            );
          });
        })}
      </ul>
      {findings.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {findings.map((x) => (
            <div key={x.id} className="refline">
              <span className="chip warn">{findingLabel(x.type)}</span> <span>{x.message}</span>
            </div>
          ))}
        </div>
      )}
      {onGo && (
        <div className="chips" style={{ marginTop: 6 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => onGo("regeln", { rule: String(r.id) })}>
            In Regeln &amp; Contracts
          </button>
          {(findings.length > 0 || r.num_unmatched > 0) && (
            <button className="btn btn-ghost btn-sm" onClick={() => onGo("findings", { q: r.name })}>
              Findings dazu
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function SystemsCard({
  items,
  sourceName,
  onGo,
  onPick,
}: {
  items: AppDocSystem2[];
  sourceName: (id: number) => string;
  onGo?: Go;
  onPick: (key: string) => void;
}) {
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="card-head">
        <h2>Systeme</h2>
        <span className="mono faint" style={{ fontSize: 12 }}>
          {items.length}
        </span>
        <span className="faint" style={{ fontSize: 12 }}>
          alle VMs der Applikation (Portgruppen ihrer EPGs, VMs hinter ihren Servern) und Server ohne VM – Klick zeigt
          ihre Regeln
        </span>
      </div>
      <div className="card-body scroll">
        {items.length === 0 ? (
          <div className="loading">Keine Systeme – Mitglieder hinzufügen.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>System</th>
                <th>IPs / DNS</th>
                <th>EPG</th>
                <th>Portgruppe</th>
                <th>Cluster / Host</th>
                <th title="Firewall-Objekte mit dieser IP je Name – darunter die Firewalls, auf denen er steht">Firewall-Objekt</th>
                <th>Standort</th>
                <th>Regeln</th>
              </tr>
            </thead>
            <tbody>
              {items.map((x) => {
                const [tone, state] = POWER[x.power_state ?? ""] ?? ["na", x.power_state ?? ""];
                return (
                  <tr key={x.key} className="clickable" title="Regeln dieses Systems zeigen" onClick={() => onPick(x.key)}>
                    <td>
                      <span className={`chip ${x.kind === "vm" ? "info" : ""}`}>{x.kind === "vm" ? "VM" : "Server"}</span>{" "}
                      <b className="mono">{x.name}</b>
                      {x.kind === "vm" && state && <span className={`chip ${tone}`}>{state}</span>}
                      {x.guest_os && <div className="faint">{x.guest_os}</div>}
                      <div className="faint" style={{ fontSize: 11.5 }}>
                        {x.member}
                      </div>
                    </td>
                    <td className="mono">
                      {x.ips.length ? x.ips.map((ip) => <div key={ip}>{ip}</div>) : <Dash />}
                      {x.dns.length > 0 && <div className="faint">{x.dns.join(", ")}</div>}
                    </td>
                    <td className="mono">{x.epgs.length ? x.epgs.map((e) => <div key={e}>{e}</div>) : <Dash />}</td>
                    <td className="mono">{x.portgroups.length ? x.portgroups.map((p) => <div key={p}>{p}</div>) : <Dash />}</td>
                    <td className="mono">
                      {x.cluster ?? <Dash />}
                      {x.host && <div className="faint">{x.host}</div>}
                    </td>
                    <td className="mono">
                      {x.objects.length ? (
                        x.objects.map((o) => (
                          <div key={o.name} className="objgroup">
                            <span className="mono">{o.name}</span>{" "}
                            {o.name_ok === true && (
                              <span className="chip ok" title="Name passt zum DNS-Namen im IPAM">
                                ✓
                              </span>
                            )}
                            {o.name_ok === false && (
                              <span className="chip warn" title={`Name passt nicht zum DNS-Namen im IPAM (${x.dns.join(", ")})`}>
                                Name ≠ DNS
                              </span>
                            )}
                            <div className="chips" title="auf diesen Firewalls erfasst – Klick öffnet das Objekt">
                              {o.where.map((w) => (
                                <button
                                  key={w.id}
                                  className="chip linkchip"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onGo?.("objekte", { object_id: String(w.id) });
                                  }}
                                >
                                  {sourceName(w.source_system_id)}
                                  {w.vdom && w.vdom !== "root" ? ` · VDOM ${w.vdom}` : ""}
                                </button>
                              ))}
                            </div>
                          </div>
                        ))
                      ) : (
                        <Dash />
                      )}
                    </td>
                    <td>{x.site ? siteLabel(x.site) : <Dash />}</td>
                    <td className="nowrap">
                      <span className="chip ok" title="spezifische Regeln">
                        {x.rules.specific}
                      </span>{" "}
                      <span className={`chip ${x.rules.unspecific ? "warn" : "na"}`} title="unspezifische Regeln (Subnetz, any, vzAny)">
                        {x.rules.unspecific}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function EpgsCard({ items, onPick }: { items: AppDocEpg[]; onPick: (key: string) => void }) {
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="card-head">
        <h2>EPGs im ACI</h2>
        <span className="mono faint" style={{ fontSize: 12 }}>
          {items.length}
        </span>
        <span className="faint" style={{ fontSize: 12 }}>
          ganzer Pfad Tenant/AP/EPG – Klick zeigt die Regeln der EPG und ihrer Systeme
        </span>
      </div>
      <div className="card-body scroll">
        {items.length === 0 ? (
          <div className="loading">Keine EPGs – Application Profile oder EPG als Mitglied hinzufügen.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>EPG</th>
                <th>BD / VRF</th>
                <th>Subnetze</th>
                <th className="num">VMs</th>
                <th className="num">Endpunkte</th>
                <th>Mitglied über</th>
                <th>Regeln</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.key} className="clickable" title="Regeln dieser EPG zeigen" onClick={() => onPick(e.key)}>
                  <td>
                    <b className="mono">{e.label}</b>
                    {e.pref_group && (
                      <span className="chip info" title="Mitglied der Preferred Group seiner VRF">
                        Preferred Group
                      </span>
                    )}
                    {e.dn && <div className="faint mono" style={{ fontSize: 11.5 }}>{e.dn}</div>}
                  </td>
                  <td className="mono">
                    {e.bd ?? <Dash />}
                    {e.vrf && <div className="faint">{e.vrf}</div>}
                  </td>
                  <td className="mono">{e.subnets.length ? e.subnets.map((x) => <div key={x}>{x}</div>) : <Dash />}</td>
                  <td className="num">{e.vm_count}</td>
                  <td className="num">{e.endpoint_count}</td>
                  <td className="faint">{e.member}</td>
                  <td className="nowrap">
                    <span className="chip ok" title="spezifische Regeln">
                      {e.rules.specific}
                    </span>{" "}
                    <span className={`chip ${e.rules.unspecific ? "warn" : "na"}`} title="unspezifische Regeln (Subnetz, any, vzAny)">
                      {e.rules.unspecific}
                    </span>
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

// Filter der Findings nach Tenant, AP und EPG (0.38.0): je Befund die EPGs seiner Systeme
type Scope = { tenant: string; ap: string; epg: string };
const parts = (label: string): Scope => {
  const [tenant = "", ap = "", epg = ""] = label.split("/");
  return { tenant, ap, epg };
};

function FindingsCard({ doc, onRule, onGo }: { doc: ApplicationDoc; onRule: (id: number) => void; onGo?: Go }) {
  const [sel, setSel] = useState<Scope>({ tenant: "", ap: "", epg: "" });
  const labels = [...new Set(doc.findings.flatMap((x) => x.epgs ?? []))].sort();
  const scopes = labels.map(parts);
  const tenants = [...new Set(scopes.map((p) => p.tenant))].filter(Boolean);
  const aps = [...new Set(scopes.filter((p) => !sel.tenant || p.tenant === sel.tenant).map((p) => p.ap))].filter(Boolean);
  const epgs = scopes.filter((p) => (!sel.tenant || p.tenant === sel.tenant) && (!sel.ap || p.ap === sel.ap));
  const matches = (x: AppDocFinding) =>
    (!sel.tenant && !sel.ap && !sel.epg) ||
    (x.epgs ?? []).map(parts).some(
      (p) => (!sel.tenant || p.tenant === sel.tenant) && (!sel.ap || p.ap === sel.ap) && (!sel.epg || p.epg === sel.epg),
    );
  const shown = doc.findings.filter(matches);
  return (
    <div className="card" id="app-findings" style={{ marginTop: 14 }}>
      <div className="card-head">
        <h2>Findings</h2>
        <span className="mono faint" style={{ fontSize: 12 }}>
          {shown.length}
          {shown.length !== doc.findings.length ? ` von ${doc.findings.length}` : ""}
        </span>
        <span className="faint" style={{ fontSize: 12 }}>
          offene Befunde zu Regeln, Servern, VMs, Portgruppen und Subnetzen der Applikation
        </span>
      </div>
      {labels.length > 0 && (
        <div className="card-body pad no-print" style={{ paddingBottom: 0 }}>
          <div className="filterbar" style={{ marginBottom: 10 }} id="finding-scope">
            <select
              className="sel"
              aria-label="Tenant"
              value={sel.tenant}
              onChange={(e) => setSel({ tenant: e.target.value, ap: "", epg: "" })}
            >
              <option value="">Tenant: alle</option>
              {tenants.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select className="sel" aria-label="AP" value={sel.ap} onChange={(e) => setSel({ ...sel, ap: e.target.value, epg: "" })}>
              <option value="">AP: alle</option>
              {aps.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <select className="sel" aria-label="EPG" value={sel.epg} onChange={(e) => setSel({ ...sel, epg: e.target.value })}>
              <option value="">EPG: alle</option>
              {[...new Set(epgs.map((p) => p.epg))].filter(Boolean).map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
            {(sel.tenant || sel.ap || sel.epg) && (
              <button className="btn btn-ghost btn-sm" onClick={() => setSel({ tenant: "", ap: "", epg: "" })}>
                Filter zurücksetzen
              </button>
            )}
          </div>
        </div>
      )}
      <div className="card-body scroll">
        {shown.length === 0 ? (
          <div className="loading">{doc.findings.length ? "Keine Befunde für diese Auswahl." : "Keine offenen Befunde."}</div>
        ) : (
          <table>
            <tbody>
              {shown.map((x) => (
                <tr key={x.id}>
                  <td className="nowrap">
                    <span className={`chip ${x.severity === "info" ? "na" : "warn"}`}>{findingLabel(x.type)}</span>
                  </td>
                  <td>
                    {x.message}
                    {x.hint && <div className="faint">{x.hint}</div>}
                    {x.epgs && x.epgs.length > 0 && (
                      <div className="faint mono" style={{ fontSize: 11.5 }} title="EPGs, zu denen der Befund gehört">
                        {x.epgs.join(", ")}
                      </div>
                    )}
                  </td>
                  <td className="nowrap no-print">
                    {x.rule_id != null && (
                      <button className="linkbtn" onClick={() => onRule(x.rule_id!)}>
                        Regel
                      </button>
                    )}{" "}
                    {onGo && (
                      <button className="linkbtn" title="In Findings öffnen" onClick={() => onGo("findings", { q: x.subject_id })}>
                        In Findings
                      </button>
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

const md = (v: string) => v.replace(/\|/g, "\\|").replace(/\n/g, " ");

function docMarkdown(doc: ApplicationDoc, sourceName: (id: number) => string): string {
  const a = doc.application;
  const s = doc.summary;
  const lines = [`# Applikation ${a.name}`, ""];
  const meta = [
    a.external_name && `Extern: ${a.external_name}`,
    a.internal_name && `Intern: ${a.internal_name}`,
    a.purpose && `Zweck: ${PURPOSE[a.purpose][1]}`,
    a.code && `Kürzel: ${a.code}`,
    doc.tenant_code && `GG: ${doc.tenant_code}`,
    a.criticality && `Kritikalität: ${CRIT_LABEL[a.criticality] ?? a.criticality}`,
    a.owner && `Verantwortlich: ${a.owner}`,
    a.site && `Standort: ${siteLabel(a.site)}`,
  ].filter(Boolean);
  if (meta.length) lines.push(meta.join(" · "), "");
  if (a.description) lines.push(a.description, "");
  lines.push(
    `**Übersicht:** ${s.vms} VMs${s.servers ? ` + ${s.servers} Server` : ""} · ${s.epgs} EPGs · ${s.rules} Regeln (${s.specific} spezifisch, ${s.unspecific} unspezifisch) · ${s.findings} Findings`,
    "",
  );
  lines.push("## Systeme", "", "| System | IPs | EPG | Portgruppe | Regeln spez./unspez. |", "| --- | --- | --- | --- | --- |");
  for (const x of doc.systems) {
    lines.push(
      `| ${x.kind === "vm" ? "VM" : "Server"} ${md(x.name)} | ${md(x.ips.join(", "))} | ${md(x.epgs.join(", "))} | ${md(x.portgroups.join(", "))} | ${x.rules.specific} / ${x.rules.unspecific} |`,
    );
  }
  lines.push("");
  if (doc.epgs.length) {
    lines.push("## EPGs im ACI", "", "| EPG | BD | VRF | Subnetze | VMs |", "| --- | --- | --- | --- | --- |");
    for (const e of doc.epgs) {
      lines.push(`| ${md(e.label)} | ${md(e.bd ?? "")} | ${md(e.vrf ?? "")} | ${md(e.subnets.join(", "))} | ${e.vm_count} |`);
    }
    lines.push("");
  }
  const names = new Map<string, string>([...doc.systems.map((x) => [x.key, x.name] as [string, string]), ...doc.epgs.map((e) => [e.key, e.label] as [string, string])]);
  lines.push("## Regeln & Contracts", "", "| Regel | Aktion | Herkunft | Richtung | System | Bezug |", "| --- | --- | --- | --- | --- | --- |");
  for (const r of doc.rules) {
    // routingtechnisch unmögliche Treffer gehören nicht in die Doku (0.24.0)
    const hits = doc.hits.filter((h) => h.rule_id === r.id && h.relevance !== "impossible");
    if (!hits.length) continue;
    // unspezifisch (any, Netz): nur die Regel – statt aller Systeme ihre Anzahl (0.37.0)
    const all = [...new Set(hits.map((h) => names.get(h.system) ?? h.system))];
    const sys = best(hits) === "specific" ? all.join(", ") : `${all.length} System${all.length === 1 ? "" : "e"}`;
    const dir = [...new Set(hits.map((h) => (h.direction === "in" ? "eingehend" : "ausgehend")))].join(", ");
    const via = [...new Set(hits.map((h) => h.via))].join(", ");
    lines.push(
      `| ${md(r.aci?.contract ?? r.name)} | ${r.action} | ${r.domain === "aci_contract" ? "ACI" : "Firewall"} ${md(sourceName(r.source_system_id))} | ${dir} | ${md(sys)} | ${SCOPE[best(hits)][1]}: ${md(via)} |`,
    );
  }
  lines.push("");
  return lines.concat(tablesMarkdown(doc.tables)).join("\n");
}
