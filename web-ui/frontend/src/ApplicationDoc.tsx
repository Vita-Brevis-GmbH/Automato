// Applikations-Doku (Dev-Befund 0.14.0): Klick auf eine Applikation öffnet ihre Doku. Oben die
// Übersicht (VMs, EPGs, Regeln spezifisch/unspezifisch, Befunde), darunter alle VMs der
// Applikation, ihre EPGs im ACI und die Regeln wie in „Regeln & Contracts“ – filterbar nach
// System/VM, EPG, Richtung (aus Sicht des Systems), Art und Bezug. Das Regel-Detail erklärt,
// warum die Regel für die Applikation gilt; Links in Regeln, Objekte und Findings. Seit 0.73.0
// (Anwender: Übersicht) Bereiche zuklappbar, Systeme kompakt mit Detail auf Klick, Export und
// Druck mit wählbaren Abschnitten; Abhängigkeiten zu anderen Applikationen vorerst entfernt.
import { ReactNode, useEffect, useMemo, useState } from "react";
import {
  AppDocEpg,
  AppDocFinding,
  AppDocHit,
  AppDocSystem2,
  AppConnections,
  ApplicationDoc,
  assignAppRule,
  DocScope,
  DocTable,
  exportApplication,
  fetchAppConnections,
  fetchAppDoc,
  fetchSystems,
  hideAppRules,
  Purpose,
  removeAppMember,
  showAppRule,
  Rule,
  System,
} from "./api";
import type { View, ViewParams } from "./App";
import ConnectionsCard, { connectionsMarkdown } from "./AppConnections";
import { DocRow, DocTableView, tablesMarkdown } from "./AppDoc";
import type { Me } from "./auth";
import { Fold, FoldTitle, readStored, useFolds, writeJson } from "./fold";
import { findingLabel, serviceNames, useVdomShown } from "./refs";
import RuleDetailDrawer from "./RuleDetail";
import { NameCell } from "./RulesTable";
import { siteLabel } from "./sites";
import { ActionBadge, Dash, download, Drawer, Empty, ErrorBox, Expandable, Icon, Loading, Modal } from "./ui";

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
  assigned: ["ok", "zugeordnet", "der Applikation direkt zugeordnet (0.60.0)"],
  specific: ["ok", "spezifisch", "nennt das System bzw. die EPG selbst (direkt, Gruppe, EPG, ESG)"],
  subnet: ["warn", "Subnetz", "gilt über ein Netz bzw. einen Bereich, der das System enthält"],
  any: ["warn", "any", "gilt für alle Adressen (any)"],
  vzany: ["warn", "vzAny", "gilt für alle EPGs der VRF (vzAny)"],
  pref_group: ["warn", "Preferred Group", "die Mitglieder der Preferred Group sprechen frei miteinander"],
};
const RANK: Record<DocScope, number> = { assigned: -1, specific: 0, subnet: 1, vzany: 2, pref_group: 3, any: 4 };
const DIRECTION: Record<string, [string, string]> = {
  assigned: ["• zugeordnet", "der Applikation direkt zugeordnet"],
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

// Abschnitte der Doku (0.73.0): Export und Druck nach Wahl – gleiche Schlüssel wie der Word-Export
// (`word_export.SECTIONS`); der Kopf mit dem Steckbrief steht immer da
const DOC_SECTIONS: [string, string, string][] = [
  ["overview", "Übersicht", "Kennzahlen der Applikation"],
  ["systems", "Systeme", "VMs und Server ohne VM"],
  ["epgs", "EPGs im ACI", ""],
  ["connections", "Verbindungen Ende zu Ende", "Weg je Verbindung mit der entscheidenden Regel"],
  ["plan", "Netzwerkplan", "als draw.io-Datei – nur Word + draw.io (ZIP)"],
  ["rules", "Regeln & Contracts", ""],
  ["findings", "Findings", ""],
  ["categories", "Doku nach Kategorien", "die 10 Kategorien nach UC1"],
  ["hidden", "Ausgeblendete Regeln", "mit Begründung"],
];
const INFRA = new Set(["connections", "plan"]); // nicht für Kunden-Benutzer
const EXPORT_KEY = "automato_app_export"; // gewählte Abschnitte (Browser)
const FOLD_KEY = "automato_app_folds"; // zugeklappte Bereiche (Browser)

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
  const [showEmpty, setShowEmpty] = useState(false);
  const [printing, setPrinting] = useState<Set<string> | null>(null); // Druck: gewählte Abschnitte offen
  const [exportOpen, setExportOpen] = useState(false);
  const [systemKey, setSystemKey] = useState<string | null>(null); // Detail eines Systems (0.73.0)
  const [conns, setConns] = useState<AppConnections | null>(null); // für den Export (0.59.0)
  const [tick, setTick] = useState(0); // Regel zugeordnet bzw. ausgeschlossen (0.60.0)
  const [busy, setBusy] = useState<unknown>(null);
  const [hiding, setHiding] = useState<{ table: DocTable; row: DocRow } | null>(null); // 0.69.0
  const vdomShown = useVdomShown();
  const { fold, setAll } = useFolds(FOLD_KEY, printing);
  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(null);
    window.addEventListener("afterprint", done);
    const timer = setTimeout(() => window.print(), 80);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("afterprint", done);
    };
  }, [printing]);

  useEffect(() => {
    setErr(null);
    fetchAppDoc(id)
      .then(setDoc)
      .catch(setErr);
  }, [id, reload, tick]);
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
  // Firewall mit VDOM, wenn sie mehrere hat (0.43.2) – wie in Regeln & Contracts
  const ruleSource = (r: Rule) => sourceName(r.source_system_id) + (vdomShown(r.source_system_id, r.vdom) ? ` · VDOM ${r.vdom}` : "");
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
  // Bereich aufklappen (falls zu) und hinscrollen
  const reveal = (key: string, el: string) => {
    const x = fold(key);
    if (!x.open) x.toggle();
    setTimeout(() => document.getElementById(el)?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  };
  const scrollToRules = () => reveal("rules", "app-rules");
  // Export und Druck (0.73.0) mit den gewählten Abschnitten: Markdown mit den Verbindungen Ende
  // zu Ende (0.59.0, noch nicht geladen: jetzt holen), Word bzw. Word + draw.io (ZIP) für
  // Confluence (0.71.0, verbessert und importiert von Hand), Drucken mit offenen Abschnitten
  const runExport = async (kind: "md" | "word" | "print", sections: string[]) => {
    const want = new Set(sections);
    if (kind === "print") {
      setExportOpen(false);
      setPrinting(want);
      return;
    }
    if (kind === "word") {
      const { blob, filename } = await exportApplication(id, want.has("plan") ? "zip" : "docx", sections);
      download(filename, blob);
    } else {
      let c = me.isCustomer || !want.has("connections") ? null : conns;
      if (c == null && !me.isCustomer && want.has("connections")) c = await fetchAppConnections(id).catch(() => null);
      download(`${a.name}.md`, docMarkdown(doc, sourceName, c, want));
    }
    setExportOpen(false);
  };
  // Druck: nicht gewählte Abschnitte fallen weg
  const off = (key: string) => (printing && !printing.has(key) ? " print-hide" : "");
  // im Druck nur mit „Ausgeblendete Regeln“: nur Kategorien mit ausgeblendeten Regeln
  const onlyHidden = !!printing && !printing.has("categories");
  const tables = doc.tables.filter((t) => (t.hidden?.length ?? 0) > 0 || (!onlyHidden && (showEmpty || t.rows.length > 0)));
  const foldKeys = ["systems", "epgs", "connections", "rules", "findings", "categories", ...doc.tables.map((t) => `cat:${t.index}`)];
  const categories = fold("categories", false);
  // Ausblenden je Kategorie (0.69.0): nur Operator, Kunden-Benutzer sehen die Liste ohne Knöpfe
  const hideControls = {
    canEdit: me.isOperator && !me.isCustomer,
    onHide: (table: DocTable) => (row: DocRow) => setHiding({ table, row }),
    onShow: async (hides: { id: number }[]) => {
      setBusy(null);
      try {
        for (const h of hides) await showAppRule(id, h.id);
        setTick((t) => t + 1);
      } catch (e) {
        setBusy(e);
      }
    },
  };
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
        <button className="btn btn-ghost btn-sm" id="app-folds-open" onClick={() => setAll(foldKeys, true)} title="alle Bereiche aufklappen">
          alle aufklappen
        </button>
        <button className="btn btn-ghost btn-sm" id="app-folds-close" onClick={() => setAll(foldKeys, false)} title="alle Bereiche zuklappen">
          alle zuklappen
        </button>
        <button
          className="btn btn-ghost btn-sm"
          id="app-doc-export"
          onClick={() => setExportOpen(true)}
          title="Markdown, Word (für Confluence) oder Drucken / PDF – mit den gewählten Abschnitten"
        >
          <Icon name="download" size={14} /> Export / Drucken…
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

      <div className={`kpis wrap${off("overview")}`}>
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
          onClick={() => reveal("findings", "app-findings")}
        />
      </div>

      <div className={off("systems").trim()}>
        <SystemsCard items={doc.systems} fold={fold("systems")} onOpen={setSystemKey} />
      </div>
      <div className={off("epgs").trim()}>
        <EpgsCard
          items={doc.epgs}
          fold={fold("epgs")}
          onPick={(key) => {
            setF({ ...NO_FILTER, epg: key });
            scrollToRules();
          }}
        />
      </div>

      {/* Verbindungen Ende zu Ende (0.45.0) – zeigt die Infrastruktur, nicht für Kunden-Benutzer */}
      {!me.isCustomer && (
        <div className={off("connections").trim()}>
          <ConnectionsCard appId={id} reload={reload + tick} onRule={setRule} onGo={onGo} onData={setConns} fold={fold("connections")} />
        </div>
      )}

      <div className={`card${off("rules")}`} id="app-rules" style={{ marginTop: 14 }}>
        <div className="card-head">
          <FoldTitle fold={fold("rules")}>Regeln &amp; Contracts</FoldTitle>
          <span className="mono faint" style={{ fontSize: 12 }}>
            {rules.length}
            {rules.length !== doc.rules.length ? ` von ${doc.rules.length}` : ""}
          </span>
          {(doc.rule_members ?? []).length > 0 && (
            <RuleMembers
              members={doc.rule_members ?? []}
              canEdit={me.isOperator}
              onOpen={setRule}
              onRemove={(mid) =>
                removeAppMember(id, mid)
                  .then(() => setTick((n) => n + 1))
                  .catch(setBusy)
              }
            />
          )}
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
        {fold("rules").open && (
        <>
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
                    source={ruleSource(r)}
                    findings={findingsOf(r.id).length}
                    onOpen={() => setRule(r.id)}
                    onGo={onGo}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
        </>
        )}
      </div>

      <div className={off("findings").trim()}>
        <FindingsCard doc={doc} onRule={setRule} onGo={onGo} fold={fold("findings")} />
      </div>

      {/* Kategorien (UC1): im Druck auch nur mit „Ausgeblendete Regeln“ – dann ohne die Zeilen */}
      <div
        className={[
          printing && !printing.has("categories") && !printing.has("hidden") ? "print-hide" : "",
          printing && !printing.has("categories") ? "print-no-rows" : "",
          printing && !printing.has("hidden") ? "print-no-hidden" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        id="app-categories"
      >
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head">
            <FoldTitle fold={printing?.has("hidden") ? { ...categories, open: true } : categories}>Doku nach Kategorien</FoldTitle>
            <span className="faint" style={{ fontSize: 12 }}>
              10 Kategorien nach UC1 – für Export und Druck
            </span>
            <div className="spacer" />
            {categories.open && (
              <button className={`toggle no-print ${showEmpty ? "on" : ""}`} onClick={() => setShowEmpty((v) => !v)}>
                leere Kategorien zeigen
              </button>
            )}
          </div>
        </div>
        {(categories.open || printing?.has("hidden")) && (
          <div className="stack" style={{ marginTop: 10 }}>
            {tables.length === 0 ? (
              <div className="card">
                <Empty title="Noch keine Zugriffe kategorisiert">Die Systeme dieser Applikation haben keine Regeln.</Empty>
              </div>
            ) : (
              tables.map((t) => (
                <DocTableView
                  key={t.index}
                  table={t}
                  onRule={setRule}
                  fold={printing ? undefined : fold(`cat:${t.index}`)}
                  hiddenOpen={!!printing?.has("hidden")}
                  hide={{ canEdit: hideControls.canEdit, onHide: hideControls.onHide(t), onShow: hideControls.onShow }}
                />
              ))
            )}
          </div>
        )}
      </div>

      {exportOpen && (
        <ExportDialog customer={me.isCustomer} onClose={() => setExportOpen(false)} onRun={runExport} />
      )}
      {systemKey && (
        <SystemDrawer
          x={doc.systems.find((x) => x.key === systemKey)}
          sourceName={sourceName}
          onGo={onGo}
          onClose={() => setSystemKey(null)}
          onRules={(key) => {
            setSystemKey(null);
            setF({ ...NO_FILTER, system: key });
            scrollToRules();
          }}
        />
      )}

      {hiding && (
        <HideDialog
          table={hiding.table}
          row={hiding.row}
          onClose={() => setHiding(null)}
          onSave={async (category, reason) => {
            const ids = (hiding.row._rules as number[]) ?? [];
            await hideAppRules(id, ids, category, reason);
            setHiding(null);
            setTick((t) => t + 1);
          }}
        />
      )}

      {rule != null && (
        <RuleDetailDrawer
          ruleId={rule}
          systemName={sourceName}
          onClose={() => setRule(null)}
          onGo={onGo}
          onRule={setRule}
          focus={selected ? ruleFocus(selected, doc.hits) : undefined}
          intro={
            selected ? (
              <>
                {me.isOperator && (
                  <RuleMemberActions
                    appId={id}
                    ruleId={selected.id}
                    hits={doc.hits.filter((h) => h.rule_id === selected.id)}
                    members={doc.rule_members ?? []}
                    onDone={() => setTick((n) => n + 1)}
                    onError={setBusy}
                  />
                )}
                <WhyBlock
                r={selected}
                hits={doc.hits.filter((h) => h.rule_id === selected.id)}
                byKey={byKey}
                titles={titles}
                source={ruleSource(selected)}
                findings={findingsOf(selected.id)}
                onGo={onGo}
              />
              </>
            ) : rule != null && me.isOperator ? (
              <RuleMemberActions appId={id} ruleId={rule} hits={[]} members={doc.rule_members ?? []} onDone={() => setTick((n) => n + 1)} onError={setBusy} />
            ) : null
          }
        />
      )}
      <ErrorBox error={busy} onClose={() => setBusy(null)} prefix="Regel zuordnen" />
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

// ACI-Contract in der Applikations-Doku (0.43.2): je Seite nur die Gegenstellen der Systeme der
// Applikation im Scope des Contracts – nicht alle Consumer bzw. Provider (Contract aus common)
function sideItems(r: Rule, hits: AppDocHit[], side: "src" | "dst"): string[] {
  if (r.domain !== "aci_contract" || !hits.length) return side === "src" ? r.sources : r.destinations;
  return [...new Set(hits.filter((h) => h.side !== side).flatMap((h) => h.counterpart.split(", ")).filter(Boolean))];
}

// Im Regel-Detail nur die Beteiligten: eigene Endpunkte und Gegenstellen im Scope (DN)
function ruleFocus(r: Rule, hits: AppDocHit[]): { dns: string[]; label: string } | undefined {
  if (r.domain !== "aci_contract") return undefined;
  const mine = hits.filter((h) => h.rule_id === r.id && h.parties);
  if (!mine.length) return undefined;
  const dns = [...new Set(mine.flatMap((h) => [...h.parties!.own, ...h.parties!.counter]))];
  return { dns, label: "der Applikation (eigene EPGs und ihre Gegenstellen im Scope)" };
}

function SideCell({ items, groups, total = 0 }: { items: string[]; groups: SideGroup[]; total?: number }) {
  const hidden = total - items.length - groups.length;
  return (
    <>
      <div className="mono">
        {items.length > 0 || !groups.length ? <List items={items} /> : null}
        {hidden > 0 && (
          <div className="faint" style={{ fontSize: 11 }} title="weitere Consumer bzw. Provider des Contracts, die die Systeme der Applikation nicht betreffen">
            von {total} im Contract
          </div>
        )}
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
  const tables = [...new Set(hits.map((h) => h.table))].filter((t) => t > 0).sort((x, y) => x - y);
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
        <SideCell items={sideItems(r, hits, "src")} groups={sideGroups(hits, "src", names)} total={aci ? r.sources.length : 0} />
      </td>
      <td>
        <SideCell items={sideItems(r, hits, "dst")} groups={sideGroups(hits, "dst", names)} total={aci ? r.destinations.length : 0} />
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

// Systeme kompakt (0.73.0, Anwender: weniger Details in der Übersicht) – Klick öffnet das Detail
function SystemsCard({ items, fold, onOpen }: { items: AppDocSystem2[]; fold: Fold; onOpen: (key: string) => void }) {
  return (
    <div className="card" id="app-systems" style={{ marginTop: 14 }}>
      <div className="card-head">
        <FoldTitle fold={fold}>Systeme</FoldTitle>
        <span className="mono faint" style={{ fontSize: 12 }}>
          {items.length}
        </span>
        <span className="faint" style={{ fontSize: 12 }}>
          alle VMs der Applikation (Portgruppen ihrer EPGs, VMs hinter ihren Servern) und Server ohne VM – Klick zeigt das Detail
        </span>
      </div>
      {fold.open && (
        <div className="card-body scroll">
          {items.length === 0 ? (
            <div className="loading">Keine Systeme – Mitglieder hinzufügen.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>System</th>
                  <th>IPs</th>
                  <th>EPG</th>
                  <th>Regeln</th>
                </tr>
              </thead>
              <tbody>
                {items.map((x) => {
                  const [tone, state] = POWER[x.power_state ?? ""] ?? ["na", x.power_state ?? ""];
                  return (
                    <tr key={x.key} className="clickable app-system" title="Detail des Systems" onClick={() => onOpen(x.key)}>
                      <td>
                        <span className={`chip ${x.kind === "vm" ? "info" : ""}`}>{x.kind === "vm" ? "VM" : "Server"}</span>{" "}
                        <b className="mono">{x.name}</b>
                        {x.kind === "vm" && state && <span className={`chip ${tone}`}>{state}</span>}
                        {(x.hints ?? []).length > 0 && (
                          <span className="chip warn" title={(x.hints ?? []).join("\n")}>
                            Hinweis
                          </span>
                        )}
                      </td>
                      <td className="mono">
                        <List items={x.ips} max={2} inline />
                      </td>
                      <td className="mono">
                        <List items={x.epgs} max={2} inline />
                      </td>
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
      )}
    </div>
  );
}

// Detail eines Systems (0.73.0): alles, was die Übersicht früher zeigte – wie die Detailansichten
// der anderen Menüs; Knopf zeigt die Regeln des Systems
function SystemDrawer({
  x,
  sourceName,
  onGo,
  onClose,
  onRules,
}: {
  x: AppDocSystem2 | undefined;
  sourceName: (id: number) => string;
  onGo?: Go;
  onClose: () => void;
  onRules: (key: string) => void;
}) {
  if (!x) return null;
  const [tone, state] = POWER[x.power_state ?? ""] ?? ["na", x.power_state ?? ""];
  const row = (label: string, value: ReactNode) => (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
  return (
    <Drawer title={x.name} sub={x.kind === "vm" ? "VM der Applikation" : "Server der Applikation (ohne VM)"} onClose={onClose}>
      <div id="app-system-detail">
        <div className="chips" style={{ marginBottom: 10 }}>
          <span className={`chip ${x.kind === "vm" ? "info" : ""}`}>{x.kind === "vm" ? "VM" : "Server"}</span>
          {x.kind === "vm" && state && <span className={`chip ${tone}`}>{state}</span>}
          <span className="chip ok" title="spezifische Regeln">
            {x.rules.specific} spezifisch
          </span>
          <span className={`chip ${x.rules.unspecific ? "warn" : "na"}`} title="unspezifische Regeln (Subnetz, any, vzAny)">
            {x.rules.unspecific} unspezifisch
          </span>
        </div>
        {(x.hints ?? []).map((h) => (
          <div key={h} className="app-hint" style={{ marginBottom: 6 }}>
            <span className="chip warn">Hinweis</span> {h}
          </div>
        ))}
        <dl className="kv">
          {row("gehört dazu über", x.member)}
          {x.guest_os && row("Betriebssystem", x.guest_os)}
          {row("IPs", x.ips.length ? <span className="mono">{x.ips.join(", ")}</span> : <Dash />)}
          {row("DNS", x.dns.length ? <span className="mono">{x.dns.join(", ")}</span> : <Dash />)}
          {x.servers.length > 0 && row("Server (IPAM)", <span className="mono">{x.servers.join(", ")}</span>)}
          {row("EPG", x.epgs.length ? x.epgs.map((e) => <div key={e} className="mono">{e}</div>) : <Dash />)}
          {row(
            "Portgruppe",
            x.nics?.some((n) => n.portgroup) ? (
              x.nics
                .filter((n) => n.portgroup)
                .map((n) => (
                  <div key={n.name} title={n.name} className="mono">
                    {n.portgroup}
                    {n.vlan != null && <span className="faint"> (VLAN {n.vlan})</span>}
                    {n.ips.length > 0 && <span className="faint"> · {n.ips.join(", ")}</span>}
                    <div className="faint">
                      → {n.epg ?? "keine EPG"}
                      {n.epg && n.match === "vlan" && <span className="chip warn"> nur über VLAN</span>}
                    </div>
                  </div>
                ))
            ) : x.portgroups.length ? (
              x.portgroups.map((p) => <div key={p} className="mono">{p}</div>)
            ) : (
              <Dash />
            ),
          )}
          {row(
            "Cluster / Host",
            x.cluster || x.host ? (
              <span className="mono">
                {x.cluster ?? "–"}
                {x.host && <span className="faint"> · {x.host}</span>}
              </span>
            ) : (
              <Dash />
            ),
          )}
          {row("Standort", x.site ? siteLabel(x.site) : <Dash />)}
          {row(
            "Firewall-Objekt",
            x.objects.length ? (
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
                      <button key={w.id} className="chip linkchip" onClick={() => onGo?.("objekte", { object_id: String(w.id) })}>
                        {sourceName(w.source_system_id)}
                        {w.vdom && w.vdom !== "root" ? ` · VDOM ${w.vdom}` : ""}
                      </button>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <Dash />
            ),
          )}
        </dl>
        <div className="chips" style={{ marginTop: 12 }}>
          <button className="btn btn-sm" id="app-system-rules" onClick={() => onRules(x.key)}>
            Regeln dieses Systems zeigen
          </button>
          {onGo && x.ips[0] && (
            <button className="btn btn-ghost btn-sm" onClick={() => onGo("regeln", { q: x.ips[0] })} title="alle Regeln mit dieser IP">
              In Regeln &amp; Contracts
            </button>
          )}
        </div>
      </div>
    </Drawer>
  );
}

function EpgsCard({ items, fold, onPick }: { items: AppDocEpg[]; fold: Fold; onPick: (key: string) => void }) {
  return (
    <div className="card" id="app-epgs" style={{ marginTop: 14 }}>
      <div className="card-head">
        <FoldTitle fold={fold}>EPGs im ACI</FoldTitle>
        <span className="mono faint" style={{ fontSize: 12 }}>
          {items.length}
        </span>
        <span className="faint" style={{ fontSize: 12 }}>
          ganzer Pfad Tenant/AP/EPG – Klick zeigt die Regeln der EPG und ihrer Systeme
        </span>
      </div>
      {fold.open && (
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
      )}
    </div>
  );
}

// Filter der Findings nach Tenant, AP und EPG (0.38.0): je Befund die EPGs seiner Systeme
type Scope = { tenant: string; ap: string; epg: string };
const parts = (label: string): Scope => {
  const [tenant = "", ap = "", epg = ""] = label.split("/");
  return { tenant, ap, epg };
};

function FindingsCard({ doc, onRule, onGo, fold }: { doc: ApplicationDoc; onRule: (id: number) => void; onGo?: Go; fold: Fold }) {
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
        <FoldTitle fold={fold}>Findings</FoldTitle>
        <span className="mono faint" style={{ fontSize: 12 }}>
          {shown.length}
          {shown.length !== doc.findings.length ? ` von ${doc.findings.length}` : ""}
        </span>
        <span className="faint" style={{ fontSize: 12 }}>
          offene Befunde zu Regeln, Servern, VMs, Portgruppen und Subnetzen der Applikation
        </span>
      </div>
      {fold.open && labels.length > 0 && (
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
      {fold.open && (
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
      )}
    </div>
  );
}

// Regel direkt zuordnen bzw. ausschliessen (0.60.0, Operator) – im Regel-Detail der Doku
function RuleMemberActions({
  appId,
  ruleId,
  hits,
  members,
  onDone,
  onError,
}: {
  appId: number;
  ruleId: number;
  hits: AppDocHit[];
  members: NonNullable<ApplicationDoc["rule_members"]>;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const mine = members.find((m) => m.rule_id === ruleId);
  const viaSystems = hits.some((h) => h.scope !== "assigned");
  const run = (p: Promise<unknown>) => {
    setBusy(true);
    p.then(onDone)
      .catch(onError)
      .finally(() => setBusy(false));
  };
  return (
    <div className="app-rule-actions" id="app-rule-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
      {mine ? (
        <>
          <span className={`chip ${mine.kind === "rule" ? "ok" : "crit"}`}>{mine.kind === "rule" ? "direkt zugeordnet" : "ausgeschlossen"}</span>
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => run(removeAppMember(appId, mine.member_id))}>
            {mine.kind === "rule" ? "Zuordnung entfernen" : "Ausschluss aufheben"}
          </button>
        </>
      ) : (
        <>
          <button
            className="btn btn-ghost btn-sm"
            disabled={busy}
            title="Die Regel gehört zur Applikation, auch ohne Treffer bzw. nur über any oder ein Netz"
            onClick={() => run(assignAppRule(appId, ruleId))}
          >
            Der Applikation zuordnen
          </button>
          {viaSystems && (
            <button className="btn btn-ghost btn-sm" disabled={busy} title="Die Regel betrifft die Applikation nicht – aus ihrer Doku nehmen" onClick={() => run(assignAppRule(appId, ruleId, true))}>
              Aus der Applikation ausschliessen
            </button>
          )}
        </>
      )}
    </div>
  );
}

// Direkt zugeordnete bzw. ausgeschlossene Regeln (0.60.0) – mit Rückgängig
function RuleMembers({
  members,
  canEdit,
  onOpen,
  onRemove,
}: {
  members: NonNullable<ApplicationDoc["rule_members"]>;
  canEdit: boolean;
  onOpen: (ruleId: number) => void;
  onRemove: (memberId: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const assigned = members.filter((m) => m.kind === "rule");
  const excluded = members.filter((m) => m.kind === "norule");
  return (
    <span className="rule-members" id="app-rule-members" style={{ position: "relative" }}>
      <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)} title="Regeln direkt zugeordnet bzw. ausgeschlossen">
        {assigned.length > 0 && `${assigned.length} zugeordnet`}
        {assigned.length > 0 && excluded.length > 0 && " · "}
        {excluded.length > 0 && `${excluded.length} ausgeschlossen`} {open ? "▴" : "▾"}
      </button>
      {open && (
        <div className="card" style={{ position: "absolute", zIndex: 20, minWidth: 380, marginTop: 4, padding: 8 }}>
          <table className="compact">
            <tbody>
              {members.map((m) => (
                <tr key={m.member_id}>
                  <td>
                    <span className={`chip ${m.kind === "rule" ? "ok" : "crit"}`}>{m.kind === "rule" ? "zugeordnet" : "ausgeschlossen"}</span>
                  </td>
                  <td>
                    <button className="linkbtn objlink" onClick={() => onOpen(m.rule_id)}>
                      {m.label}
                    </button>
                  </td>
                  <td>
                    {canEdit && (
                      <button className="btn btn-ghost btn-sm" onClick={() => onRemove(m.member_id)}>
                        rückgängig
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </span>
  );
}

const md = (v: string) => v.replace(/\|/g, "\\|").replace(/\n/g, " ");

// Doku als Markdown (0.73.0: nur die gewählten Abschnitte, ohne Abhängigkeiten)
function docMarkdown(doc: ApplicationDoc, sourceName: (id: number) => string, conns: AppConnections | null, want: Set<string>): string {
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
  if (want.has("overview")) {
    lines.push(
      `**Übersicht:** ${s.vms} VMs${s.servers ? ` + ${s.servers} Server` : ""} · ${s.epgs} EPGs · ${s.rules} Regeln (${s.specific} spezifisch, ${s.unspecific} unspezifisch) · ${s.findings} Findings`,
      "",
    );
  }
  if (want.has("systems")) {
    lines.push("## Systeme", "", "| System | IPs | EPG | Portgruppe | Regeln spez./unspez. |", "| --- | --- | --- | --- | --- |");
    for (const x of doc.systems) {
      lines.push(
        `| ${x.kind === "vm" ? "VM" : "Server"} ${md(x.name)} | ${md(x.ips.join(", "))} | ${md(x.epgs.join(", "))} | ${md(x.portgroups.join(", "))} | ${x.rules.specific} / ${x.rules.unspecific} |`,
      );
    }
    lines.push("");
  }
  if (want.has("epgs") && doc.epgs.length) {
    lines.push("## EPGs im ACI", "", "| EPG | BD | VRF | Subnetze | VMs |", "| --- | --- | --- | --- | --- |");
    for (const e of doc.epgs) {
      lines.push(`| ${md(e.label)} | ${md(e.bd ?? "")} | ${md(e.vrf ?? "")} | ${md(e.subnets.join(", "))} | ${e.vm_count} |`);
    }
    lines.push("");
  }
  const names = new Map<string, string>([...doc.systems.map((x) => [x.key, x.name] as [string, string]), ...doc.epgs.map((e) => [e.key, e.label] as [string, string])]);
  if (want.has("rules")) {
    lines.push("## Regeln & Contracts", "", "| Regel | Aktion | Herkunft | Richtung | System | Bezug |", "| --- | --- | --- | --- | --- | --- |");
    for (const r of doc.rules) {
      // routingtechnisch unmögliche Treffer gehören nicht in die Doku (0.24.0)
      const hits = doc.hits.filter((h) => h.rule_id === r.id && h.relevance !== "impossible");
      if (!hits.length) continue;
      // unspezifisch (any, Netz): nur die Regel – statt aller Systeme ihre Anzahl (0.37.0)
      const all = [...new Set(hits.map((h) => names.get(h.system) ?? h.system))];
      const scope = best(hits);
      const sys =
        scope === "assigned" ? "direkt zugeordnet" : scope === "specific" ? all.join(", ") : `${all.length} System${all.length === 1 ? "" : "e"}`;
      const dir = [...new Set(hits.map((h) => DIRECTION[h.direction]?.[0].replace(/^\S+ /, "") ?? h.direction))].join(", ");
      const via = [...new Set(hits.map((h) => h.via))].join(", ");
      lines.push(
        `| ${md(r.aci?.contract ?? r.name)} | ${r.action} | ${r.domain === "aci_contract" ? "ACI" : "Firewall"} ${md(sourceName(r.source_system_id))} | ${dir} | ${md(sys)} | ${SCOPE[best(hits)][1]}: ${md(via)} |`,
      );
    }
    lines.push("");
  }
  if (conns && want.has("connections")) lines.push(...connectionsMarkdown(conns));
  if (want.has("findings")) {
    lines.push("## Findings", "");
    if (!doc.findings.length) lines.push("Keine offenen Befunde.", "");
    else {
      lines.push("| Befund | Meldung | Hinweis |", "| --- | --- | --- |");
      for (const x of doc.findings) lines.push(`| ${md(findingLabel(x.type))} | ${md(x.message)} | ${md(x.hint ?? "")} |`);
      lines.push("");
    }
  }
  return lines.concat(tablesMarkdown(doc.tables, want.has("categories"), want.has("hidden"))).join("\n");
}

// Export und Druck mit wählbaren Abschnitten (0.73.0); die Auswahl bleibt im Browser
function ExportDialog({
  customer,
  onClose,
  onRun,
}: {
  customer: boolean;
  onClose: () => void;
  onRun: (kind: "md" | "word" | "print", sections: string[]) => Promise<void>;
}) {
  const options = DOC_SECTIONS.filter(([k]) => !customer || !INFRA.has(k));
  const [sel, setSel] = useState<Set<string>>(() => {
    const stored = readStored<string[] | null>(EXPORT_KEY, null);
    const keys = options.map(([k]) => k);
    return new Set(Array.isArray(stored) ? stored.filter((k) => keys.includes(k)) : keys);
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const chosen = options.map(([k]) => k).filter((k) => sel.has(k));
  const toggle = (key: string) =>
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      writeJson(EXPORT_KEY, [...next]);
      return next;
    });
  const run = async (kind: "md" | "word" | "print") => {
    setBusy(kind);
    setErr(null);
    try {
      await onRun(kind, chosen);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };
  const pick = (keys: string[]) => {
    setSel(new Set(keys));
    writeJson(EXPORT_KEY, keys);
  };
  const empty = chosen.length === 0;
  return (
    <Modal
      title="Export / Drucken"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn" id="app-export-md" disabled={empty || busy !== null} onClick={() => void run("md")}>
            {busy === "md" ? "…" : "Markdown"}
          </button>
          {!customer && (
            <button
              className="btn"
              id="app-doc-confluence"
              disabled={empty || busy !== null}
              title="Word-Datei der Doku – mit Netzwerkplan als draw.io im ZIP – zum Verbessern und Importieren in Confluence"
              onClick={() => void run("word")}
            >
              {busy === "word" ? "…" : sel.has("plan") ? "Word + draw.io (ZIP)" : "Word (.docx)"}
            </button>
          )}
          <button className="btn btn-primary" id="app-export-print" disabled={empty || busy !== null} onClick={() => void run("print")}>
            Drucken / PDF
          </button>
        </>
      }
    >
      <div id="app-export-dialog">
        <ErrorBox error={err} prefix="Export" />
        <p className="faint" style={{ marginTop: 0, fontSize: 12 }}>
          Abschnitte für Export und Druck – der Kopf mit dem Steckbrief steht immer da. Die Auswahl bleibt in diesem Browser.
        </p>
        <div className="chips" style={{ marginBottom: 8 }}>
          <button className="linkbtn" onClick={() => pick(options.map(([k]) => k))}>
            alle
          </button>
          <button className="linkbtn" onClick={() => pick([])}>
            keine
          </button>
        </div>
        {options.map(([key, label, hint]) => (
          <label key={key} className="check" style={{ display: "block", margin: "4px 0" }}>
            <input type="checkbox" data-section={key} checked={sel.has(key)} onChange={() => toggle(key)} /> {label}
            {hint && <span className="faint"> – {hint}</span>}
          </label>
        ))}
      </div>
    </Modal>
  );
}

// Regel in einer Kategorie ausblenden (0.69.0): Begründung Pflicht, nur diese bzw. alle Kategorien
function HideDialog({
  table,
  row,
  onClose,
  onSave,
}: {
  table: DocTable;
  row: DocRow;
  onClose: () => void;
  onSave: (category: number | null, reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const ok = reason.trim().length >= 5;
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onSave(all ? null : table.index, reason.trim());
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Regel in der Kategorie ausblenden"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" id="hide-submit" disabled={!ok || busy} onClick={() => void save()}>
            {busy ? "…" : "Ausblenden"}
          </button>
        </>
      }
    >
      <div id="hide-dialog">
        <ErrorBox error={err} prefix="Ausblenden" />
        <p style={{ marginTop: 0 }}>
          <b>{String(row.Description ?? "Regel")}</b>
          <span className="faint"> · {String(row._app ?? "").replace(/\n/g, ", ")}</span>
        </p>
        <p className="faint" style={{ fontSize: 12 }}>
          Die Regel fällt aus der Kategorie (Ansicht, Export, Druck) und steht dort unter „ausgeblendet“ mit der Begründung.
          Sonst bleibt sie in der Doku; das Ausblenden steht im Audit und hält über neue Läufe.
        </p>
        <div className="field">
          <label htmlFor="hide-reason">Begründung (Pflicht)</label>
          <textarea
            id="hide-reason"
            className="input"
            rows={3}
            maxLength={1000}
            value={reason}
            placeholder="z. B. Grundregel des Rechenzentrums, nicht applikationsspezifisch"
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <label className="check">
          <input type="radio" name="hide-scope" checked={!all} onChange={() => setAll(false)} /> nur in Kategorie {table.index}. {table.title}
        </label>
        <label className="check" style={{ display: "block", marginTop: 4 }}>
          <input type="radio" name="hide-scope" id="hide-all" checked={all} onChange={() => setAll(true)} /> in allen Kategorien
        </label>
      </div>
    </Modal>
  );
}
