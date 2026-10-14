import { useEffect, useState } from "react";
import { fetchFindings, Finding, patchFinding } from "./api";
import { SeverityBadge } from "./ui";

const STATUSES = ["open", "acknowledged", "resolved", "ignored"];

export default function Findings() {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFindings()
      .then(setFindings)
      .catch((e) => setError(String(e)));
  }, []);

  const triage = async (id: number, status: string) => {
    try {
      const updated = await patchFinding(id, { status });
      setFindings((prev) => prev?.map((f) => (f.id === id ? updated : f)) ?? prev);
    } catch (e) {
      setError(`Triage fehlgeschlagen: ${e} (erfordert operator)`);
    }
  };

  if (error) return <p className="err">{error}</p>;
  if (findings === null) return <p className="notice">Lade Findings…</p>;
  if (findings.length === 0)
    return <p className="notice">Keine offenen Findings – alle Objekte aufgelöst. 🎉</p>;

  return (
    <div className="panel">
      <table>
        <thead>
          <tr>
            <th>Schwere</th>
            <th>Typ</th>
            <th>Objekt</th>
            <th>Status</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => (
            <tr key={f.id}>
              <td>
                <SeverityBadge severity={f.severity} />
              </td>
              <td>{f.type}</td>
              <td>
                <code>{f.subject_id}</code>
              </td>
              <td>
                <select value={f.status} onChange={(e) => triage(f.id, e.target.value)}>
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </td>
              <td className="muted">{f.detail ? JSON.stringify(f.detail) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
