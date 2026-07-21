import { useEffect, useState } from "react";
import { fetchHealth, HealthStatus } from "./api";
import RulesTable from "./RulesTable";

export default function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchHealth().then(setHealth).catch((e) => setError(String(e)));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", margin: "2rem" }}>
      <h1>Automato – Firewall-Regel-Dokumentation</h1>
      <section>
        <h2>Systemstatus</h2>
        {error && <p>Orchestrator nicht erreichbar: {error}</p>}
        {health && (
          <p>
            API: <strong>{health.status}</strong> · Datenbank:{" "}
            <strong>{health.db}</strong>
          </p>
        )}
        {!health && !error && <p>Prüfe…</p>}
      </section>
      <section>
        <h2>Regeln</h2>
        <RulesTable />
      </section>
    </main>
  );
}
