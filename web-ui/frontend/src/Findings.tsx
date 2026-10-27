// Findings (ADR 0004/0010): Übersicht nach Kategorien, je Befund ein Satz "was genau nicht
// stimmt" + Hinweis, aufklappbar mit allen Angaben und Links auf die betroffenen Regeln.
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { fetchFindings, fetchFindingSummary, fetchSystems, Finding, FindingSummary, patchFinding, System } from "./api";
import type { View, ViewParams } from "./App";
import type { Me } from "./auth";
import { FINDING_GROUPS, FINDING_HINT, FINDING_LABEL, findingLabel } from "./refs";
import { Empty, ErrorBox, fmtTime, Icon, Loading, PageHead, SeverityBadge } from "./ui";

const PAGE = 500; // Findings je Seite

const STATUS_LABEL: Record<string, string> = {
  open: "offen",
  acknowledged: "bestätigt",
  resolved: "erledigt",
  ignored: "ignoriert",
};

// Lesbare Bezeichnungen der Detail-Felder; technische Felder (Quelle steht in eigener Spalte) aus
const DETAIL_LABEL: Record<string, string | null> = {
  source_system_id: null,
  source: null,
  value: "Wert",
  values: "Werte",
  dns_name: "DNS-Name im IPAM",
  reference: "Referenz",
  vlan: "VLAN",
  ipam_vlan: "VLAN im IPAM",
  context: "Kontext",
  context_name: "an",
  epg: "EPG",
  objects: "Objekte",
  expected: "erwartet",
  category: "Art",
  name: null, // = Objekt-Spalte
  names: "Objekte",
  object_class: "Klasse",
  hint: "Hinweis",
  prefix: null, // = Objekt-Spalte
  gateway: "Gateway",
  vrf: "VRF",
  sources: "Firewall",
  firewall_objects: "Firewall-Objekte",
  rules: "Regeln",
  rule_names: "z. B.",
  server_name: "Name im IPAM gesucht",
  ipam_elsewhere: "IPAM kennt den Server unter",
  ipam_candidates: "Kandidaten im IPAM",
  no_reference: null,
  prefixes: "bekannte Präfixe",
  compared: "verglichen",
  missing: "fehlt",
  domain: null,
  rule_id: null,
  external_id: "ID",
  tenant: "ACI-Tenant",
  rule: "Prüfung",
  // Topologie (0.50.0): Knoten stehen im Satz, die Adressen sind lesbar
  nodes: null,
  node: null,
  label: null,
  labels: null,
  network: "Netz",
  next_hops: "Next-Hop",
  distance: "Distanz",
  back: "Rückweg über",
  peer: "Nachbar",
  peer_names: "Name im IPAM",
  remote_as: "AS",
  state: "Zustand",
  // Zonen (0.51.0): Paare und Absicht stehen im Satz
  pairs: null,
  notes: null,
  zones: "Zonen",
  // Wege uneinheitlich (0.73.0, vorher Regel ↔ Weg): Verbindungen stehen im Satz
  application: "Applikation",
  application_id: null,
  connections: null,
  count: "Verbindungen",
  // Parallele Firewalls (0.18.0/0.72.0): Felder und Werte stehen im Satz, Routen als Tabelle
  group: "Verbund",
  other: "verglichen mit",
  fields: "Felder",
  missing_on: "fehlt auf",
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
};

// Routing-Befund im Verbund (0.72.0): je Präfix, was auf der führenden bzw. dieser Firewall steht
const ROUTE_KIND: Record<string, string> = {
  differs: "anders",
  only_lead: "fehlt hier",
  only_here: "nur hier",
};
const ROUTE_TABLE: Record<string, string> = { rib: "wirksam", static: "statisch", vdom: "VDOM" };
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
  const entries = (Array.isArray(d.entries) ? d.entries : []) as RouteEntry[];
  if (!entries.length) return null;
  const count = typeof d.count === "number" ? d.count : entries.length;
  return (
    <div className="scroll" style={{ maxHeight: 320, marginTop: 6 }}>
      <table className="parallel-routes">
        <thead>
          <tr>
            <th>Tabelle</th>
            <th>Präfix</th>
            <th>Abweichung</th>
            <th>{String(d.other ?? "führend")}</th>
            <th>{String(d.source ?? "diese Firewall")}</th>
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
      {count > entries.length && <div className="faint">… und {count - entries.length} weitere</div>}
    </div>
  );
}

const SIDE: Record<string, string> = { src: "Quelle / Consumer", dst: "Ziel / Provider" };
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
    return [{ label: `Routen ${String(d.source ?? "")} · ${String(d.vdom)}`, view: "routing",
              params: { tab: "routes", context: `${sid}|${String(d.vdom)}` } }];
  if (f.type === "parallel_policy_route_mismatch")
    return [{ label: String(d.label ?? "Policy-Route"), view: "routing",
              params: { tab: "policy", q: `${String(d.source ?? "")} ${String(d.name ?? d.seq ?? "")}`.trim() } }];
  if (f.type === "parallel_nat_mismatch" && d.name)
    return [{ label: String(d.name), view: "nat", params: { q: String(d.name) } }];
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
  if (["rule_incomplete", "aci_scope_unpaired", "aci_contract_both_sides", "snat_pool_missing", "app_connection_mismatch", "app_contract_narrower"].includes(f.type) && d.rule_id != null)
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
  onChanged,
  onGo,
}: {
  me: Me;
  tenantId: number | null;
  tenantLabel: string;
  initialType?: string; // Vorfilter, z. B. aus der Abgleich-Ansicht
  initialQuery?: string; // Suche aus einem Link (#findings?q=…, z. B. aus der Applikations-Doku)
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

  return (
    <>
      <PageHead title="Findings">
        Datenqualität für „{tenantLabel}“: Abgleich der Firewall-/ACI-Daten mit dem IPAM (DDI/NetBox) und der
        Namenskonvention. Wird nach jedem Lauf neu berechnet; Triage (Status setzen) erfordert die Rolle Operator
        und bleibt erhalten.
      </PageHead>
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
                  <span className={`chip ${openN ? "warn" : "ok"}`}>{openN} offen</span>
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
                      title={`${FINDING_HINT[t] ?? t} – Klick filtert`}
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
              {s ? STATUS_LABEL[s] : "alle"} ({s ? (counts[s] ?? 0) : (summary?.total ?? 0)})
            </button>
          ))}
        </div>
        <select className="sel" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">Typ: alle</option>
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
          <input placeholder="Objekt oder Detail…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <span className="count">
          {total} Treffer{total > loaded ? ` · ${loaded} geladen` : ""}
        </span>
      </div>
      <ErrorBox error={error} prefix="Findings" onClose={() => setError(null)} />
      <div className="card">
        <div className="card-body scroll">
          {findings === null ? (
            <Loading>Lade Findings…</Loading>
          ) : shown.length === 0 ? (
            <Empty title={status === "open" ? "Keine offenen Findings 🎉" : "Keine Einträge"}>
              {status === "open"
                ? "Alle Endpunkte wurden gegen die Source of Truth aufgelöst oder als extern bestätigt."
                : "Für diesen Status gibt es keine Findings."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Schwere</th>
                  <th>Typ</th>
                  <th>Objekt</th>
                  <th>Befund</th>
                  <th>Quelle</th>
                  <th>Zuletzt gesehen</th>
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
                        <td className="nowrap" title={FINDING_HINT[f.type] ?? f.type}>
                          {findingLabel(f.type)}
                        </td>
                        <td className="mono">{f.subject_id}</td>
                        <td style={{ maxWidth: 520 }}>
                          <div>{f.message ?? detailText(f.detail)}</div>
                          {f.hint && <div className="faint">→ {f.hint}</div>}
                        </td>
                        <td className="nowrap">
                          {systemName(f.source_system_id) ?? sources ?? <span className="faint">–</span>}
                        </td>
                        <td className="mono">{fmtTime(f.last_seen)}</td>
                        <td onClick={(e) => e.stopPropagation()}>
                          {me.isOperator ? (
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
                              <dt>erstmals gesehen</dt>
                              <dd className="mono">{fmtTime(f.first_seen)}</dd>
                            </dl>
                            <RouteEntries f={f} />
                            {others.length > 0 && (
                              <div style={{ marginTop: 6 }}>
                                <span className="faint">Anzeigen: </span>
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
                                <span className="faint">Regeln anzeigen: </span>
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
            {loaded} von {total} geladen – Typ oder Suche eingrenzen oder{" "}
            <button className="btn btn-ghost btn-sm" onClick={loadMore} disabled={more}>
              {more ? "Lade…" : `weitere ${Math.min(PAGE, total - loaded)} laden`}
            </button>
          </div>
        )}
      </div>
    </>
  );
}
