import type { UserManager } from "oidc-client-ts";
import { useEffect, useRef, useState } from "react";
import {
  fetchAuthConfig,
  fetchHealth,
  fetchTenants,
  getAuthToken,
  HealthStatus,
  Tenant,
} from "./api";
import Applications from "./Applications";
import * as auth from "./auth";
import Findings from "./Findings";
import NatTable from "./Nat";
import Overview from "./Overview";
import RulesTable from "./RulesTable";

type Tab = "overview" | "rules" | "apps" | "nat" | "findings";

export default function App() {
  const [ready, setReady] = useState(false);
  const [oidc, setOidc] = useState(false);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<number | null>(null);
  const mgrRef = useRef<UserManager | null>(null);
  const startedRef = useRef(false);

  // AuthN-Bootstrap: dev -> direkt bereit; oidc -> Callback verarbeiten bzw. zu
  // Keycloak umleiten, wenn kein Token vorliegt.
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    (async () => {
      let cfg;
      try {
        cfg = await fetchAuthConfig();
      } catch {
        setReady(true);
        return;
      }
      if (cfg.auth_mode !== "oidc") {
        setReady(true);
        return;
      }
      setOidc(true);
      const mgr = auth.createManager(cfg);
      mgrRef.current = mgr;
      try {
        if (await auth.handleRedirectCallback(mgr)) {
          setReady(true);
          return;
        }
        if (getAuthToken()) {
          setReady(true);
          return;
        }
        await auth.login(mgr); // leitet zu Keycloak um
      } catch {
        setReady(true); // Fehler sichtbar machen (API liefert dann ggf. 401)
      }
    })();
  }, []);

  useEffect(() => {
    if (!ready) return;
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
    fetchTenants()
      .then(setTenants)
      .catch(() => setTenants([]));
  }, [ready]);

  if (!ready) {
    return (
      <div className="app">
        <p className="notice">Anmeldung…</p>
      </div>
    );
  }

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
          {oidc && mgrRef.current && (
            <button className="tab" onClick={() => auth.logout(mgrRef.current!)}>
              Abmelden
            </button>
          )}
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
