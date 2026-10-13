import { useEffect, useState } from "react";
import { fetchFindings, Finding } from "./api";
import { SeverityBadge } from "./ui";

export default function Findings() {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFindings()
      .then(setFindings)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="notice">Findings konnten nicht geladen werden: {error}</p>;
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
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => (
            <tr key={f.id} style={{ cursor: "default" }}>
              <td>
                <SeverityBadge severity={f.severity} />
              </td>
              <td>{f.type}</td>
              <td>
                <code>{f.subject_id}</code>
              </td>
              <td className="muted">{f.detail ? JSON.stringify(f.detail) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
