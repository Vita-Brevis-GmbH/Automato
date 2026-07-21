import { useEffect, useState } from "react";
import { fetchRules, Rule } from "./api";

// Platzhalter fuer die Regel-Tabelle aus UC1: Filter nach System, Netzwerk
// und Zone folgen, sobald das Datenmodell (M5) steht und die API Daten liefert.
export default function RulesTable() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchRules().then(setRules).catch((e) => setError(String(e)));
  }, []);

  if (error) return <p>Regeln konnten nicht geladen werden: {error}</p>;
  if (rules === null) return <p>Lade Regeln…</p>;
  if (rules.length === 0)
    return <p>Noch keine Regeln erfasst – Collector-Lauf starten (siehe /jobs API).</p>;

  return (
    <table>
      <thead>
        <tr>
          <th>Name</th>
        </tr>
      </thead>
      <tbody>
        {rules.map((rule) => (
          <tr key={rule.id}>
            <td>{rule.name}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
