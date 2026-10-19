import { useEffect, useState } from "react";
import { fetchFindings, Finding, patchFinding } from "./api";
import type { Me } from "./auth";
import { Empty, ErrorBox, fmtTime, Loading, PageHead, SeverityBadge } from "./ui";

const STATUS_LABEL: Record<string, string> = {
  open: "offen",
  acknowledged: "bestätigt",
  resolved: "erledigt",
  ignored: "ignoriert",
};

function detailText(d: Record<string, unknown> | null): string {
  if (!d) return "–";
  return Object.entries(d)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

export default function Findings({
  me,
  tenantId,
  tenantLabel,
  onChanged,
}: {
  me: Me;
  tenantId: number | null;
  tenantLabel: string;
  onChanged: () => void;
}) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState("open");

  useEffect(() => {
    setFindings(null);
    setError(null);
    fetchFindings(tenantId)
      .then(setFindings)
      .catch((e) => {
        setError(e);
        setFindings([]);
      });
  }, [tenantId]);

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

  const shown = (findings ?? []).filter((f) => !status || f.status === status);
  const counts = (findings ?? []).reduce<Record<string, number>>((acc, f) => {
    acc[f.status] = (acc[f.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <>
      <PageHead title="Findings">
        Datenqualität für „{tenantLabel}“: nicht aufgelöste Objekte und Auffälligkeiten. Triage (Status
        setzen) erfordert die Rolle Operator.
      </PageHead>
      <div className="filterbar">
        <div className="seg">
          {["open", "acknowledged", "resolved", "ignored", ""].map((s) => (
            <button key={s || "all"} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>
              {s ? STATUS_LABEL[s] : "alle"} ({s ? (counts[s] ?? 0) : (findings?.length ?? 0)})
            </button>
          ))}
        </div>
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
                    <td>{f.type}</td>
                    <td className="mono">{f.subject_id}</td>
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
