// Findings (ADR 0004/0010): Übersicht nach Kategorien, je Befund ein Satz "was genau nicht
// stimmt" + Hinweis, aufklappbar mit allen Angaben und Links auf die betroffenen Regeln.
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { fetchFindings, fetchFindingSummary, fetchSystems, Finding, FindingSummary, patchFinding, System } from "./api";
import type { View, ViewParams } from "./App";
import { customerCan, type Me } from "./auth";
import FindingDialog from "./FindingDialog";
import Checks from "./modules/findings/Checks";
import { NetAssignDialog } from "./modules/wege/NetAssign";
import { FINDING_GROUPS, FINDING_HINT, FINDING_LABEL, findingLabel } from "./refs";
import { Empty, ErrorBox, fmtTime, Icon, Loading, PageHead, SeverityBadge } from "./ui";
import { L } from "./i18n";

const PAGE = 500; // Findings je Seite

const STATUS_LABEL: Record<string, string> = {
  open: L("offen", "open"),
  acknowledged: L("bestätigt", "acknowledged"),
  resolved: L("erledigt", "resolved"),
  ignored: L("ignoriert", "ignored"),
};

// Lesbare Bezeichnungen der Detail-Felder; technische Felder (Quelle steht in eigener Spalte) aus
const DETAIL_LABEL: Record<string, string | null> = {
  source_system_id: null,
  source: null,
  value: L("Wert", "Value"),
  values: L("Werte", "Values"),
  dns_name: L("DNS-Name im IPAM", "DNS name in IPAM"),
  reference: L("Referenz", "Reference"),
  vlan: "VLAN",
  ipam_vlan: L("VLAN im IPAM", "VLAN in IPAM"),
  context: L("Kontext", "Context"),
  context_name: L("an", "on"),
  epg: "EPG",
  objects: L("Objekte", "Objects"),
  expected: L("erwartet", "expected"),
  category: L("Art", "Type"),
  name: null, // = Objekt-Spalte
  names: L("Objekte", "Objects"),
  object_class: L("Klasse", "Class"),
  hint: L("Hinweis", "Note"),
  prefix: null, // = Objekt-Spalte
  gateway: "Gateway",
  vrf: "VRF",
  sources: "Firewall",
  firewall_objects: L("Firewall-Objekte", "Firewall objects"),
  rules: L("Regeln", "Rules"),
  rule_names: L("z. B.", "e.g."),
  server_name: L("Name im IPAM gesucht", "Name searched in IPAM"),
  ipam_elsewhere: L("IPAM kennt den Server unter", "IPAM knows the server as"),
  ipam_candidates: L("Kandidaten im IPAM", "Candidates in IPAM"),
  no_reference: null,
  prefixes: L("bekannte Präfixe", "known prefixes"),
  compared: L("verglichen", "compared"),
  missing: L("fehlt", "missing"),
  domain: null,
  rule_id: null,
  external_id: "ID",
  tenant: L("ACI-Tenant", "ACI tenant"),
  rule: L("Prüfung", "Check"),
  // Topologie (0.50.0): Knoten stehen im Satz, die Adressen sind lesbar
  nodes: null,
  node: null,
  label: null,
  labels: null,
  network: L("Netz", "Network"),
  next_hops: "Next-Hop",
  distance: L("Distanz", "Distance"),
  back: L("Rückweg über", "Return path via"),
  peer: L("Nachbar", "Neighbor"),
  peer_names: L("Name im IPAM", "Name in IPAM"),
  remote_as: "AS",
  state: L("Zustand", "State"),
  // Zonen (0.51.0): Paare und Absicht stehen im Satz
  pairs: null,
  notes: null,
  zones: L("Zonen", "Zones"),
  // Wege uneinheitlich (0.73.0, vorher Regel ↔ Weg): Verbindungen stehen im Satz
  application: L("Applikation", "Application"),
  application_id: null,
  connections: null,
  count: L("Verbindungen", "Connections"),
  // Parallele Firewalls (0.18.0/0.72.0): Felder und Werte stehen im Satz, Routen als Tabelle
  group: L("Verbund", "Parallel group"),
  other: L("verglichen mit", "compared with"),
  fields: L("Felder", "Fields"),
  missing_on: L("fehlt auf", "missing on"),
  other_rule_id: null,
  object_id: null,
  other_object_id: null,
  nat_id: null,
  other_nat_id: null,
  nat_type: null,
  vdom: "VDOM",
  seq: null,
  entries: null,
  counts: null,
  // Datenqualität (ADR 0029): Teilfehler und Kontexte stehen im Satz
  platform: null,
  last_success: L("zuletzt erfolgreich", "last successful"),
  never: null,
  age_days: L("Alter (Tage)", "Age (days)"),
  limit_days: L("Grenze (Tage)", "Limit (days)"),
  schedule: L("Zeitplan", "Schedule"),
  last_failed: null,
  last_message: L("letzte Meldung", "last message"),
  run_id: null,
  finished_at: L("Lauf beendet", "Run finished"),
  parts: null,
  contexts: null,
  context_count: null,
  // BGP-Filter (0.113.0): Netze stehen als Liste darunter (`entries` als Präfixe)
  total: L("Anzahl", "Count"),
  filters: L("Filter", "Filters"),
  received: L("erhalten", "received"),
  l3out: "L3Out",
  aci_tenant: L("ACI-Tenant", "ACI tenant"),
};

// Routing-Befund im Verbund (0.72.0): je Präfix, was auf der führenden bzw. dieser Firewall steht
const ROUTE_KIND: Record<string, string> = {
  differs: L("anders", "different"),
  only_lead: L("fehlt hier", "missing here"),
  only_here: L("nur hier", "only here"),
};
const ROUTE_TABLE: Record<string, string> = {
  rib: L("wirksam", "effective"),
  static: L("statisch", "static"),
  vdom: "VDOM",
};
type RouteEntry = {
  table: string;
  prefix: string;
  kind: string;
  vrf?: string | number;
  fields?: string[];
  lead?: string;
  here?: string;
};

function RouteEntries({ f }: { f: Finding }) {
  const d = (f.detail ?? {}) as Record<string, unknown>;
  const all = Array.isArray(d.entries) ? (d.entries as unknown[]) : [];
  if (!all.length) return null;
  // BGP-Filter (0.113.2): Netze als Präfixe - keine Routentabelle (zeigte leere Spalten)
  if (all.every((e) => typeof e === "string")) {
    const total = typeof d.total === "number" ? d.total : all.length;
    return (
      <div className="kv-list" style={{ marginTop: 6 }}>
        <span className="faint">{L("Netze: ", "Networks: ")}</span>
        <span className="mono">{(all as string[]).join(", ")}</span>
        {total > all.length && (
          <span className="faint">{L(` … und ${total - all.length} weitere`, ` … and ${total - all.length} more`)}</span>
        )}
      </div>
    );
  }
  const entries = all.filter((e) => e && typeof e === "object") as RouteEntry[];
  if (!entries.length) return null;
  const count = typeof d.count === "number" ? d.count : entries.length;
  return (
    <div className="scroll" style={{ maxHeight: 320, marginTop: 6 }}>
      <table className="parallel-routes">
        <thead>
          <tr>
            <th>{L("Tabelle", "Table")}</th>
            <th>{L("Präfix", "Prefix")}</th>
            <th>{L("Abweichung", "Deviation")}</th>
            <th>{String(d.other ?? L("führend", "leading"))}</th>
            <th>{String(d.source ?? L("diese Firewall", "this firewall"))}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i}>
              <td>{ROUTE_TABLE[e.table] ?? e.table}</td>
              <td className="mono">
                {e.prefix}
                {e.vrf != null && <span className="faint"> · VRF {e.vrf}</span>}
              </td>
              <td>
                <span className="chip warn">{ROUTE_KIND[e.kind] ?? e.kind}</span>
                {e.fields?.length ? <span className="faint"> {e.fields.join(", ")}</span> : null}
              </td>
              <td className="mono">{e.lead ?? (e.kind === "only_here" ? "–" : "")}</td>
              <td className="mono">{e.here ?? (e.kind === "only_lead" ? "–" : "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {count > entries.length && (
        <div className="faint">
          {L(<>… und {count - entries.length} weitere</>, <>… and {count - entries.length} more</>)}
        </div>
      )}
    </div>
  );
}

const SIDE: Record<string, string> = {
  src: L("Quelle / Consumer", "Source / Consumer"),
  dst: L("Ziel / Provider", "Destination / Provider"),
};
const fmtValue = (k: string, v: unknown): string => {
  if (k === "missing" && Array.isArray(v)) return v.map((x) => SIDE[x] ?? x).join(", ");
  if (Array.isArray(v)) return v.join(k === "compared" ? " ≠ " : ", ");
  // Abweichung im Verbund: {Feld: [hier, dort]}
  if (k === "values" && v && typeof v === "object")
    return Object.entries(v as Record<string, unknown>)
      .map(([f, x]) => `${f}: ${Array.isArray(x) ? x.join(" ≠ ") : String(x)}`)
      .join(" · ");
  if (k === "ipam_elsewhere" && v && typeof v === "object") {
    const o = v as Record<string, string>;
    return `${o.name} → ${o.value}`;
  }
  return typeof v === "object" ? JSON.stringify(v) : String(v);
};

// Weitere Ziele eines Befunds (0.72.0): Routen bzw. Policy-Routen im Routing, NAT
function viewLinks(f: Finding): { label: string; view: View; params: ViewParams }[] {
  const d = (f.detail ?? {}) as Record<string, unknown>;
  const sid = f.source_system_id;
  if (f.type === "parallel_route_mismatch" && sid != null && d.vdom)
    return [{ label: L(`Routen ${String(d.source ?? "")} · ${String(d.vdom)}`,
                       `Routes ${String(d.source ?? "")} · ${String(d.vdom)}`), view: "routing",
              params: { tab: "routes", context: `${sid}|${String(d.vdom)}` } }];
  if (f.type === "parallel_policy_route_mismatch")
    return [{ label: String(d.label ?? L("Policy-Route", "Policy route")), view: "routing",
              params: { tab: "policy", q: `${String(d.source ?? "")} ${String(d.name ?? d.seq ?? "")}`.trim() } }];
  if (f.type === "parallel_nat_mismatch" && d.name)
    return [{ label: String(d.name), view: "nat", params: { q: String(d.name) } }];
  // 0.96.0: VIP bzw. IP-Pool (z. B. Name ohne Präfix seines Typs) in NAT / VIPs öffnen
  if (f.subject_kind === "device_object" && (d.category === "vip" || d.category === "ippool") && d.name)
    return [{ label: L(`${String(d.name)} in NAT / VIPs`, `${String(d.name)} in NAT / VIPs`), view: "nat",
              params: { q: String(d.name) } }];
  // Datenqualität (ADR 0029): Läufe des Quellsystems, nicht lesbarer Routing-Kontext
  if ((f.type === "source_stale" || f.type === "source_partial") && sid != null) {
    const out: { label: string; view: View; params: ViewParams }[] = [
      { label: L(`Läufe von ${f.subject_id}`, `Runs of ${f.subject_id}`), view: "laeufe",
        params: { system: String(sid) } },
    ];
    const ctx = (Array.isArray(d.contexts) ? d.contexts : []) as { context?: string }[];
    if (ctx[0]?.context)
      out.push({ label: `Routing ${ctx[0].context}`, view: "routing",
                 params: { tab: "routes", context: `${sid}|${ctx[0].context}` } });
    return out;
  }
  return [];
}

// Objekt-Namen eines Befunds, für die ein Link "Regeln mit diesem Objekt" sinnvoll ist
function ruleLinks(f: Finding): { label: string; params: ViewParams }[] {
  const d = (f.detail ?? {}) as Record<string, unknown>;
  const names = (k: string) => (Array.isArray(d[k]) ? (d[k] as string[]) : []);
  if (f.type === "unmatched_object" || (f.type === "invalid_name" && f.subject_kind === "network_object")) {
    const objs = names("firewall_objects");
    if (objs.length) return objs.slice(0, 5).map((n) => ({ label: n, params: { object: n } }));
    return [{ label: f.subject_id, params: { q: String(d.value ?? f.subject_id) } }];
  }
  if (f.type === "duplicate_value") return names("names").map((n) => ({ label: n, params: { object: n } }));
  if (["rule_incomplete", "aci_scope_unpaired", "aci_contract_both_sides", "snat_pool_missing", "app_connection_mismatch", "app_contract_narrower", "app_rule_unused"].includes(f.type) && d.rule_id != null)
    return [{ label: String(d.name ?? f.subject_id), params: { rule: String(d.rule_id) } }];
  if (f.subject_kind === "device_object" && f.type !== "unused_object")
    return [{ label: f.subject_id, params: { object: f.subject_id } }];
  return [];
}

function detailText(d: Record<string, unknown> | null): string {
  if (!d) return "–";
  return (
    Object.entries(d)
      .filter(([k, v]) => DETAIL_LABEL[k] !== null && v !== null && v !== "")
      .map(([k, v]) => `${DETAIL_LABEL[k] ?? k}: ${fmtValue(k, v)}`)
      .join(" · ") || "–"
  );
}

export default function Findings({
  me,
  tenantId,
  tenantLabel,
  initialType = "",
  initialQuery = "",
  initialTab = "befunde",
  onChanged,
  onGo,
}: {
  me: Me;
  tenantId: number | null;
  tenantLabel: string;
  initialType?: string; // Vorfilter, z. B. aus der Abgleich-Ansicht
  initialQuery?: string; // Suche aus einem Link (#findings?q=…, z. B. aus der Applikations-Doku)
  initialTab?: "befunde" | "pruefungen"; // 0.110.0: Reiter Prüfungen (#findings?tab=pruefungen)
  onChanged: () => void;
  onGo?: (v: View, p?: ViewParams) => void; // Links auf die betroffenen Regeln
}) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<FindingSummary | null>(null);
  const [more, setMore] = useState(false);
  const [systems, setSystems] = useState<System[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState("open");
  const [type, setType] = useState(initialType);
  const [q, setQ] = useState(initialQuery);
  const [open, setOpen] = useState<number | null>(null); // aufgeklappter Befund
  const [adding, setAdding] = useState(false); // Finding von Hand (0.109.0)
  const [assigning, setAssigning] = useState<string | null>(null); // grosses Netz zuordnen (0.111.0)
  const [tab, setTab] = useState(initialTab);
  const choose = (t: "befunde" | "pruefungen") => {
    setTab(t);
    window.history.replaceState(null, "", t === "befunde" ? "#findings" : "#findings?tab=pruefungen");
  };

  useEffect(() => setType(initialType), [initialType]);

  useEffect(() => {
    fetchSystems(tenantId)
      .then(setSystems)
      .catch(() => setSystems([]));
  }, [tenantId]);

  // Status, Typ und Suche filtert der Orchestrator über alle Findings; Zähler aus der Summary
  const query = useMemo(() => ({ tenantId, status, type, q, limit: PAGE }), [tenantId, status, type, q]);
  const reload = useCallback(() => {
    setError(null);
    return Promise.all([fetchFindings(query), fetchFindingSummary(tenantId, type, q)])
      .then(([page, s]) => {
        setFindings(page.rows);
        setTotal(page.total);
        setSummary(s);
      })
      .catch((e) => {
        setError(e);
        setFindings([]);
        setTotal(0);
      });
  }, [query, tenantId, type, q]);
  useEffect(() => {
    setFindings(null);
    const timer = setTimeout(reload, 250);
    return () => clearTimeout(timer);
  }, [reload]);

  const loadMore = () => {
    if (!findings) return;
    setMore(true);
    fetchFindings({ ...query, offset: findings.length })
      .then((page) => {
        setFindings([...findings, ...page.rows]);
        setTotal(page.total);
      })
      .catch(setError)
      .finally(() => setMore(false));
  };

  const systemName = (id: number | null) =>
    id == null ? null : (systems.find((s) => s.id === id)?.name ?? `#${id}`);

  const triage = async (id: number, next: string) => {
    setError(null);
    try {
      const updated = await patchFinding(id, { status: next });
      setFindings((prev) => prev?.map((f) => (f.id === id ? updated : f)) ?? prev);
      fetchFindingSummary(tenantId, type, q)
        .then(setSummary)
        .catch(() => undefined);
      onChanged();
    } catch (e) {
      setError(e);
    }
  };

  // Nach einer Triage kann ein Eintrag den Statusfilter verlassen - bis zum Neuladen ausblenden
  const shown = (findings ?? []).filter((f) => !status || f.status === status);
  const counts = summary?.by_status ?? {};
  const types = [...new Set([...Object.keys(summary?.by_type ?? {}), ...(type ? [type] : [])])].sort((a, b) =>
    findingLabel(a).localeCompare(findingLabel(b)),
  );
  const loaded = findings?.length ?? 0;

  const canAdd = me.isOperator && !me.isCustomer && tenantId !== null;
  return (
    <>
      <PageHead
        title="Findings"
        actions={
          canAdd && tab === "befunde" ? (
            <button
              className="btn btn-sm"
              id="finding-add"
              onClick={() => setAdding(true)}
              title={L(
                "Befund von Hand erfassen (Typ „Von Hand erfasst“): bleibt nach dem Abgleich bestehen, Triage wie jedes Finding",
                "Capture a finding manually (type “Captured manually”): stays after the reconciliation, triage like any finding",
              )}
            >
              + {L("Finding erfassen", "Capture finding")}
            </button>
          ) : undefined
        }
      >
        {L(
          <>
            Datenqualität für „{tenantLabel}“: Abgleich der Firewall-/ACI-Daten mit dem IPAM (DDI/NetBox) und der
            Namenskonvention. Wird nach jedem Lauf neu berechnet; Triage (Status setzen) erfordert die Rolle Operator
            und bleibt erhalten.
          </>,
          <>
            Data quality for “{tenantLabel}”: reconciliation of the firewall/ACI data with the IPAM (DDI/NetBox) and the
            naming convention. Recalculated after every run; triage (setting the status) requires the operator role
            and is kept.
          </>,
        )}
      </PageHead>
      <div className="tabbar">
        <button className={tab === "befunde" ? "on" : ""} onClick={() => choose("befunde")}>
          {L("Befunde", "Findings")}
        </button>
        <button id="checks-tab" className={tab === "pruefungen" ? "on" : ""} onClick={() => choose("pruefungen")}>
          {L("Prüfungen", "Checks")}
        </button>
      </div>
      {tab === "pruefungen" ? (
        <Checks
          me={me}
          tenantId={tenantId}
          onChanged={() => {
            void reload();
            onChanged();
          }}
          onShowType={(t) => {
            setType(t);
            setStatus("");
            choose("befunde");
          }}
        />
      ) : (
        <>
          {summary && Object.keys(summary.by_type).length > 0 && (
            <div className="fgroups">
              {FINDING_GROUPS.map((g) => {
                const types = g.types.filter((t) => summary.by_type[t]);
                if (!types.length) return null;
                const openN = types.reduce((n, t) => n + summary.by_type[t].open, 0);
                return (
                  <div key={g.key} className="card fgroup">
                    <div className="fgroup-head">
                      <b>{g.label}</b>
                      <span className={`chip ${openN ? "warn" : "ok"}`}>{L(<>{openN} offen</>, <>{openN} open</>)}</span>
                    </div>
                    <div className="faint" style={{ fontSize: 12, marginBottom: 4 }}>
                      {g.help}
                    </div>
                    {types.map((t) => {
                      const c = summary.by_type[t];
                      return (
                        <button
                          key={t}
                          className={`fline ${type === t ? "on" : ""}`}
                          title={L(`${FINDING_HINT[t] ?? t} – Klick filtert`, `${FINDING_HINT[t] ?? t} – click to filter`)}
                          onClick={() => setType(type === t ? "" : t)}
                        >
                          <span>{findingLabel(t)}</span>
                          <span className="mono">
                            <b className={c.open ? "warnfg" : "okfg"}>{c.open}</b>
                            {c.total > c.open && <span className="faint"> / {c.total}</span>}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
          <div className="filterbar">
            <div className="seg">
              {["open", "acknowledged", "resolved", "ignored", ""].map((s) => (
                <button key={s || "all"} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>
                  {s ? STATUS_LABEL[s] : L("alle", "all")} ({s ? (counts[s] ?? 0) : (summary?.total ?? 0)})
                </button>
              ))}
            </div>
            <select className="sel" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">{L("Typ: alle", "Type: all")}</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {FINDING_LABEL[t] ?? t}
                </option>
              ))}
            </select>
            <div className="search">
              <span className="ico">
                <Icon name="search" />
              </span>
              <input placeholder={L("Objekt oder Detail…", "Object or detail…")} value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <span className="count">
              {L(
                <>
                  {total} Treffer{total > loaded ? ` · ${loaded} geladen` : ""}
                </>,
                <>
                  {total} match{total === 1 ? "" : "es"}
                  {total > loaded ? ` · ${loaded} loaded` : ""}
                </>,
              )}
            </span>
          </div>
          <ErrorBox error={error} prefix="Findings" onClose={() => setError(null)} />
          {assigning && tenantId !== null && (
          <NetAssignDialog
            tenantId={tenantId}
            prefix={assigning}
            onClose={() => setAssigning(null)}
            onSaved={() => setAssigning(null)}
          />
        )}
        {adding && tenantId !== null && (
            <FindingDialog
              tenantId={tenantId}
              draft={{}}
              systems={systems.map((x) => ({ id: x.id, name: x.name }))}
              onClose={() => setAdding(false)}
              onSaved={() => {
                setAdding(false);
                void reload();
                onChanged();
              }}
            />
          )}
          <div className="card">
            <div className="card-body scroll">
              {findings === null ? (
                <Loading>{L("Lade Findings…", "Loading findings…")}</Loading>
              ) : shown.length === 0 ? (
                <Empty
                  title={
                    status === "open" ? L("Keine offenen Findings 🎉", "No open findings 🎉") : L("Keine Einträge", "No entries")
                  }
                >
                  {status === "open"
                    ? L(
                        "Alle Endpunkte wurden gegen die Source of Truth aufgelöst oder als extern bestätigt.",
                        "All endpoints were resolved against the source of truth or acknowledged as external.",
                      )
                    : L("Für diesen Status gibt es keine Findings.", "There are no findings for this status.")}
                </Empty>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>{L("Schwere", "Severity")}</th>
                      <th>{L("Typ", "Type")}</th>
                      <th>{L("Objekt", "Object")}</th>
                      <th>{L("Befund", "Finding")}</th>
                      <th>{L("Quelle", "Source")}</th>
                      <th>{L("Zuletzt gesehen", "Last seen")}</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((f) => {
                      const links = onGo ? ruleLinks(f) : [];
                      const others = onGo ? viewLinks(f) : [];
                      const sources = Array.isArray(f.detail?.sources) ? (f.detail!.sources as string[]).join(", ") : null;
                      return (
                        <Fragment key={f.id}>
                          <tr className="clickable" onClick={() => setOpen(open === f.id ? null : f.id)}>
                            <td>
                              <SeverityBadge severity={f.severity} />
                            </td>
                            <td className="finding-type" title={FINDING_HINT[f.type] ?? f.type}>
                              {findingLabel(f.type)}
                            </td>
                            <td className="mono finding-subject">{f.subject_id}</td>
                            <td className="finding-text">
                              <div>{f.message ?? detailText(f.detail)}</div>
                              {f.hint && <div className="faint">→ {f.hint}</div>}
                            </td>
                            <td className="nowrap">
                              {systemName(f.source_system_id) ?? sources ?? <span className="faint">–</span>}
                            </td>
                            <td className="mono">{fmtTime(f.last_seen)}</td>
                            <td onClick={(e) => e.stopPropagation()}>
                              {/* Triage: Operator bzw. Kunden-Benutzer mit der Kunden-Rolle triage (feinere Rollen) */}
                              {(me.isCustomer ? customerCan(me, "findings.triage") : me.isOperator) ? (
                                <select className="sel" value={f.status} onChange={(e) => triage(f.id, e.target.value)}>
                                  {Object.entries(STATUS_LABEL).map(([k, v]) => (
                                    <option key={k} value={k}>
                                      {v}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <span className="chip">{STATUS_LABEL[f.status] ?? f.status}</span>
                              )}
                            </td>
                          </tr>
                          {open === f.id && (
                            <tr className="subrow">
                              <td colSpan={7}>
                                <dl className="kv tight">
                                  {Object.entries(f.detail ?? {})
                                    .filter(([k, v]) => DETAIL_LABEL[k] !== null && v !== null && v !== "")
                                    .map(([k, v]) => (
                                      <Fragment key={k}>
                                        <dt>{DETAIL_LABEL[k] ?? k}</dt>
                                        <dd className="mono">{fmtValue(k, v)}</dd>
                                      </Fragment>
                                    ))}
                                  <dt>{L("erstmals gesehen", "first seen")}</dt>
                                  <dd className="mono">{fmtTime(f.first_seen)}</dd>
                                </dl>
                                <RouteEntries f={f} />
                              {f.type === "net_wider_than_routes" && canAdd && (
                                <div style={{ marginTop: 6 }}>
                                  <button className="btn btn-sm" onClick={() => setAssigning(String(f.detail?.value ?? ""))}>
                                    {L("Netz zuordnen", "Assign network")}
                                  </button>{" "}
                                  <span className="faint">
                                    {L("gilt ab dem nächsten Abgleich", "applies from the next reconciliation")}
                                  </span>
                                </div>
                              )}
                                {others.length > 0 && (
                                  <div style={{ marginTop: 6 }}>
                                    <span className="faint">{L("Anzeigen: ", "Show: ")}</span>
                                    {others.map((l) => (
                                      <button
                                        key={l.label}
                                        className="chip local linkchip"
                                        onClick={() => onGo?.(l.view, l.params)}
                                      >
                                        {l.label}
                                      </button>
                                    ))}
                                  </div>
                                )}
                                {links.length > 0 && (
                                  <div style={{ marginTop: 6 }}>
                                    <span className="faint">{L("Regeln anzeigen: ", "Show rules: ")}</span>
                                    {links.map((l) => (
                                      <button
                                        key={l.label}
                                        className="chip local linkchip"
                                        onClick={() => onGo?.("regeln", l.params)}
                                      >
                                        {l.label}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
            {findings !== null && total > loaded && (
              <div className="card-foot">
                {L(
                  <>
                    {loaded} von {total} geladen – Typ oder Suche eingrenzen oder{" "}
                  </>,
                  <>
                    {loaded} of {total} loaded – narrow down type or search, or{" "}
                  </>,
                )}
                <button className="btn btn-ghost btn-sm" onClick={loadMore} disabled={more}>
                  {more
                    ? L("Lade…", "Loading…")
                    : L(`weitere ${Math.min(PAGE, total - loaded)} laden`, `load ${Math.min(PAGE, total - loaded)} more`)}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}
