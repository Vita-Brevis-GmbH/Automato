import { useEffect, useState } from "react";
import { fetchHealth, HealthStatus } from "./api";
import Applications from "./Applications";
import Findings from "./Findings";
import NatTable from "./Nat";
import RulesTable from "./RulesTable";

type Tab = "rules" | "apps" | "nat" | "findings";

export default function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [tab, setTab] = useState<Tab>("rules");

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const tabs: [Tab, string][] = [
    ["rules", "Regeln"],
    ["apps", "Applikationen"],
    ["nat", "NAT / VIPs"],
    ["findings", "Findings"],
  ];

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
        {tabs.map(([key, label]) => (
          <button
            key={key}
            className={`tab ${tab === key ? "active" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "rules" && <RulesTable />}
      {tab === "apps" && <Applications />}
      {tab === "nat" && <NatTable />}
      {tab === "findings" && <Findings />}
    </div>
  );
}
