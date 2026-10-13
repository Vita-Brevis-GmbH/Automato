import { useEffect, useState } from "react";
import { fetchHealth, fetchTenants, HealthStatus, Tenant } from "./api";
import Applications from "./Applications";
import Findings from "./Findings";
import NatTable from "./Nat";
import Overview from "./Overview";
import RulesTable from "./RulesTable";

type Tab = "overview" | "rules" | "apps" | "nat" | "findings";

export default function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<number | null>(null);

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
    fetchTenants()
      .then(setTenants)
      .catch(() => setTenants([]));
  }, []);

  const tabs: [Tab, string][] = [
    ["overview", "Übersicht"],
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
        <div className="header-right">
          <label className="mandant">
            <span>Mandant</span>
            <select
              value={tenantId ?? ""}
              onChange={(e) => setTenantId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">Alle Mandanten</option>
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <span className="count">
            {health ? `API ${health.status} · DB ${health.db}` : "API nicht erreichbar"}
          </span>
        </div>
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

      {tab === "overview" && <Overview tenantId={tenantId} onOpenRules={() => setTab("rules")} />}
      {tab === "rules" && <RulesTable tenantId={tenantId} />}
      {tab === "apps" && <Applications />}
      {tab === "nat" && <NatTable />}
      {tab === "findings" && <Findings />}
    </div>
  );
}
