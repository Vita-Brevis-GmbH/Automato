import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchFindings, fetchFindingSummary, fetchSystems, Finding, FindingSummary, patchFinding, System } from "./api";
import type { Me } from "./auth";
import { FINDING_LABEL, findingLabel } from "./refs";
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
};

function detailText(d: Record<string, unknown> | null): string {
  if (!d) return "–";
  return (
    Object.entries(d)
      .filter(([k, v]) => DETAIL_LABEL[k] !== null && v !== null && v !== "")
      .map(([k, v]) => `${DETAIL_LABEL[k] ?? k}: ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v)}`)
      .join(" · ") || "–"
  );
}

export default function Findings({
  me,
  tenantId,
  tenantLabel,
  initialType = "",
  onChanged,
}: {
  me: Me;
  tenantId: number | null;
  tenantLabel: string;
  initialType?: string; // Vorfilter, z. B. aus der Abgleich-Ansicht
  onChanged: () => void;
}) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<FindingSummary | null>(null);
  const [more, setMore] = useState(false);
  const [systems, setSystems] = useState<System[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState("open");
  const [type, setType] = useState(initialType);
  const [q, setQ] = useState("");

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
                  <th>Quelle</th>
                  <th>Detail</th>
                  <th>Zuletzt gesehen</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((f) => (
                  <tr key={f.id}>
                    <td>
                      <SeverityBadge severity={f.severity} />
                    </td>
                    <td className="nowrap" title={f.type}>
                      {findingLabel(f.type)}
                    </td>
                    <td className="mono">{f.subject_id}</td>
                    <td className="nowrap">{systemName(f.source_system_id) ?? <span className="faint">–</span>}</td>
                    <td className="muted" style={{ maxWidth: 360 }}>
                      {detailText(f.detail)}
                    </td>
                    <td className="mono">{fmtTime(f.last_seen)}</td>
                    <td>
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
                ))}
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
