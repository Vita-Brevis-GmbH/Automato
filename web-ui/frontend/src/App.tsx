import { useEffect, useState } from "react";
import { fetchHealth, HealthStatus } from "./api";
import Findings from "./Findings";
import RulesTable from "./RulesTable";

export default function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [tab, setTab] = useState<"rules" | "findings">("rules");

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  return (
    <div className="app">
      <div className="header">
        <h1>Automato</h1>
        <span className="sub">Firewall-Regel-Dokumentation</span>
        <span className="count">
          {health ? `API ${health.status} · DB ${health.db}` : "API nicht erreichbar"}
        </span>
      </div>

      <div className="tabs">
        <button
          className={`tab ${tab === "rules" ? "active" : ""}`}
          onClick={() => setTab("rules")}
        >
          Regeln
        </button>
        <button
          className={`tab ${tab === "findings" ? "active" : ""}`}
          onClick={() => setTab("findings")}
        >
          Findings
        </button>
      </div>

      {tab === "rules" ? <RulesTable /> : <Findings />}
    </div>
  );
}
