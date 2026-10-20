import { useEffect, useState } from "react";
import { fetchFindings, fetchSystems, Finding, patchFinding, System } from "./api";
import type { Me } from "./auth";
import { FINDING_LABEL, findingLabel } from "./refs";
import { Empty, ErrorBox, fmtTime, Icon, Loading, PageHead, SeverityBadge } from "./ui";

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
  const [systems, setSystems] = useState<System[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState("open");
  const [type, setType] = useState(initialType);
  const [q, setQ] = useState("");

  useEffect(() => setType(initialType), [initialType]);

  useEffect(() => {
    setFindings(null);
    setError(null);
    fetchFindings(tenantId)
      .then(setFindings)
      .catch((e) => {
        setError(e);
        setFindings([]);
      });
    fetchSystems(tenantId)
      .then(setSystems)
      .catch(() => setSystems([]));
  }, [tenantId]);

  const systemName = (id: number | null) =>
    id == null ? null : (systems.find((s) => s.id === id)?.name ?? `#${id}`);

  const triage = async (id: number, next: string) => {
    setError(null);
    try {
      const updated = await patchFinding(id, { status: next });
      setFindings((prev) => prev?.map((f) => (f.id === id ? updated : f)) ?? prev);
      onChanged();
    } catch (e) {
      setError(e);
    }
  };

  const ql = q.trim().toLowerCase();
  const ofType = (findings ?? []).filter(
    (f) =>
      (!type || f.type === type) &&
      (!ql || f.subject_id.toLowerCase().includes(ql) || detailText(f.detail).toLowerCase().includes(ql)),
  );
  const shown = ofType.filter((f) => !status || f.status === status);
  const counts = ofType.reduce<Record<string, number>>((acc, f) => {
    acc[f.status] = (acc[f.status] ?? 0) + 1;
    return acc;
  }, {});
  const types = [...new Set([...(findings ?? []).map((f) => f.type), ...(type ? [type] : [])])].sort((a, b) =>
    findingLabel(a).localeCompare(findingLabel(b)),
  );

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
              {s ? STATUS_LABEL[s] : "alle"} ({s ? (counts[s] ?? 0) : ofType.length})
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
        <span className="count">{shown.length} Treffer</span>
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
      </div>
    </>
  );
}
