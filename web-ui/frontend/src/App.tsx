import { useCallback, useEffect, useRef, useState } from "react";
import {
  AuthConfig,
  Customer,
  fetchAuthConfig,
  fetchCustomers,
  fetchFindingSummary,
  fetchHealth,
  fetchNat,
  fetchRuleSummary,
  fetchServerMe,
  fetchTenants,
  FindingSummary,
  HealthStatus,
  RuleSummary,
  setCustomerFilter,
  setCustomerUser,
  Tenant,
} from "./api";
import Agents from "./Agents";
import AppDoc from "./AppDoc";
import Customers from "./Customers";
import * as auth from "./auth";
import Design from "./Design";
import Findings from "./Findings";
import NamingProfiles from "./NamingProfiles";
import NatTable from "./Nat";
import NetBoxSync from "./NetBoxSync";
import Objects from "./Objects";
import Overview from "./Overview";
import Reconcile from "./Reconcile";
import Paths from "./Paths";
import Routing from "./Routing";
import RulesTable from "./RulesTable";
import Runs from "./Runs";
import System from "./System";
import Tenants from "./Tenants";
import { ErrorBox, Icon, Note } from "./ui";
import Users from "./Users";

export type View =
  | "uebersicht"
  | "laeufe"
  | "regeln"
  | "objekte"
  | "nat"
  | "routing"
  | "wege"
  | "apps"
  | "findings"
  | "abgleich"
  | "design"
  | "users"
  | "tenants"
  | "kunden"
  | "dict"
  | "agents"
  | "netbox"
  | "system";

interface NavEntry {
  key: View;
  label: string;
  icon: string;
  section: "doc" | "admin";
  adminOnly?: boolean;
  customer?: boolean; // auch für Kunden-Benutzer (ADR 0018; serverseitig eingeschränkt)
}

const NAV: NavEntry[] = [
  { key: "uebersicht", label: "Übersicht", icon: "overview", section: "doc", customer: true },
  { key: "regeln", label: "Regeln & Contracts", icon: "rules", section: "doc", customer: true },
  { key: "objekte", label: "Objekte & Inventar", icon: "objects", section: "doc", customer: true },
  { key: "nat", label: "NAT / VIPs", icon: "nat", section: "doc", customer: true },
  { key: "wege", label: "Weg-Suche", icon: "path", section: "doc" },
  { key: "routing", label: "Routing", icon: "route", section: "doc" },
  { key: "apps", label: "Applikationen", icon: "apps", section: "doc", customer: true },
  { key: "findings", label: "Findings", icon: "findings", section: "doc", customer: true },
  { key: "abgleich", label: "Abgleich", icon: "reconcile", section: "doc" },
  { key: "design", label: "Netzwerkdesign", icon: "design", section: "doc", customer: true },
  { key: "users", label: "Benutzer & Rollen", icon: "users", section: "admin" },
  { key: "tenants", label: "Mandanten & Standorte", icon: "tenants", section: "admin" },
  { key: "kunden", label: "Kunden", icon: "customers", section: "admin" },
  { key: "dict", label: "Namensprofile", icon: "dict", section: "admin" },
  { key: "agents", label: "Collector-Agenten", icon: "agents", section: "admin", adminOnly: true },
  { key: "laeufe", label: "Läufe", icon: "play", section: "admin" },
  { key: "netbox", label: "NetBox-Sync", icon: "upload", section: "admin", adminOnly: true },
  { key: "system", label: "System", icon: "server", section: "admin", adminOnly: true },
];

// Daten der Übersicht + Zähler in der Navigation (je Mandant geladen).
export interface Stats {
  rules: RuleSummary | null; // Kennzahlen aller Regeln (Firewall + ACI)
  nat: number | null; // Anzahl VIPs/SNAT-Pools
  findings: FindingSummary | null; // Zähler (offen/gesamt/je Typ)
  error: unknown;
}

const EMPTY_FINDINGS: FindingSummary = { total: 0, open: 0, by_status: {}, by_type: {} };

const EMPTY_RULES: RuleSummary = {
  total: 0,
  by_domain: {},
  source_system_ids: [],
  endpoints: 0,
  unmatched: 0,
  by_dst_zone: [],
};

type Boot =
  | { state: "loading" }
  | { state: "redirect" }
  | { state: "error"; error: unknown; oidc: boolean }
  | { state: "ready"; cfg: AuthConfig; me: auth.Me };

const VIEW_KEYS = NAV.map((n) => n.key) as string[];

function viewFromHash(): View {
  const h = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return (VIEW_KEYS.includes(h) ? h : "uebersicht") as View;
}

// Parameter hinter der Ansicht (#regeln?object=H_web01) - Verlinkung zwischen den Ansichten
export type ViewParams = Record<string, string>;
function paramsFromHash(): ViewParams {
  const query = window.location.hash.split("?")[1] ?? "";
  return Object.fromEntries(new URLSearchParams(query).entries());
}

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* localStorage nicht verfügbar */
  }
}

function initials(name: string): string {
  const parts = name.split(/[\s._@-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

// Rollen und Kunden laut Orchestrator (0.53.0: auch die Zuordnung Benutzer → Kunde in Automato);
// ohne Antwort (z. B. Konto ohne Rolle) bleibt die Sicht aus dem Token
async function withServerMe(me: auth.Me): Promise<auth.Me> {
  try {
    return auth.withServer(me, await fetchServerMe());
  } catch {
    return me;
  }
}

export default function App() {
  const [boot, setBoot] = useState<Boot>({ state: "loading" });
  const [problem, setProblem] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => auth.onProblem(setProblem), []);

  // AuthN-Bootstrap: dev -> Standard-Admin; oidc -> Callback verarbeiten, gültige
  // Sitzung sicherstellen (still erneuern) oder zu Keycloak umleiten.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      let cfg: AuthConfig;
      try {
        cfg = await fetchAuthConfig();
      } catch (e) {
        setBoot({ state: "error", error: e, oidc: false });
        return;
      }
      if (cfg.auth_mode !== "oidc") {
        const me = await withServerMe(auth.devMe());
        setCustomerUser(me.isCustomer);
        setBoot({ state: "ready", cfg, me });
        return;
      }
      auth.init(cfg);
      try {
        await auth.handleRedirectCallback();
        const user = await auth.ensureSession();
        if (!user) {
          setBoot({ state: "redirect" });
          return;
        }
        const me = await withServerMe(auth.meFromUser(user));
        setCustomerUser(me.isCustomer);
        setBoot({ state: "ready", cfg, me });
      } catch (e) {
        window.history.replaceState({}, document.title, window.location.pathname);
        setBoot({ state: "error", error: e, oidc: true });
      }
    })();
  }, []);

  if (problem) {
    return (
      <Splash>
        <ErrorBox error={problem} prefix="Anmeldung nicht möglich" />
        <Note>
          Häufige Ursachen: im Keycloak-Realm fehlt der Audience-Mapper <code>automato-api</code>,{" "}
          <code>AUTOMATO_OIDC_ISSUER</code>/<code>AUTOMATO_OIDC_JWKS_URL</code> passen nicht zum Realm,
          der Orchestrator erreicht Keycloak nicht, oder die Uhrzeit der VMs weicht ab (NTP).
          Details:{" "}
          <code>docker compose logs orchestrator | grep OIDC</code>
        </Note>
        <div style={{ display: "flex", gap: 9 }}>
          <button className="btn btn-primary" onClick={() => auth.login()}>
            Erneut anmelden
          </button>
          <button className="btn btn-ghost" onClick={() => auth.logout()}>
            Abmelden
          </button>
        </div>
      </Splash>
    );
  }
  if (boot.state === "loading" || boot.state === "redirect") {
    return (
      <Splash>
        {boot.state === "loading" ? "Verbindung wird aufgebaut…" : "Weiterleitung zur Anmeldung…"}
      </Splash>
    );
  }
  if (boot.state === "error") {
    return (
      <Splash>
        <ErrorBox error={boot.error} prefix="Anmeldung fehlgeschlagen" />
        <button
          className="btn btn-primary btn-block"
          onClick={() => (boot.oidc ? auth.login() : window.location.reload())}
        >
          {boot.oidc ? "Erneut anmelden" : "Neu laden"}
        </button>
      </Splash>
    );
  }
  return <Shell cfg={boot.cfg} me={boot.me} />;
}

function Splash({ children }: { children: React.ReactNode }) {
  return (
    <section className="splash">
      <div className="splash-card">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div className="brand-name">Automato</div>
        </div>
        <p className="splash-sub">Automatisierungs- und Dokumentationsplattform.</p>
        {children}
        <div className="splash-foot">
          <Icon name="lock" size={13} />
          {window.location.protocol === "https:" ? "Verbindung über HTTPS" : "Verbindung ohne HTTPS"}
        </div>
      </div>
    </section>
  );
}

function Shell({ cfg, me }: { cfg: AuthConfig; me: auth.Me }) {
  const [view, setView] = useState<View>(viewFromHash);
  const [params, setParams] = useState<ViewParams>(paramsFromHash);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [tenantsErr, setTenantsErr] = useState<unknown>(null);
  const [tenantId, setTenantIdRaw] = useState<number | null>(() => {
    const v = stored("automato_tenant");
    return v ? Number(v) : null;
  });
  const [stats, setStats] = useState<Stats>({ rules: null, nat: null, findings: null, error: null });
  // Kunden-Filter (ADR 0018 Schritt 2): je Umgebung gemerkt, gilt für die Doku-Ansichten
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customerId, setCustomerIdRaw] = useState<number | null>(null);
  const [theme, setTheme] = useState<string | null>(() => stored("automato_theme"));
  const [menuOpen, setMenuOpen] = useState(false);
  const [findingType, setFindingType] = useState(""); // Vorfilter aus der Abgleich-Ansicht

  const setTenantId = useCallback((id: number | null) => {
    setTenantIdRaw(id);
    store("automato_tenant", id === null ? null : String(id));
  }, []);

  const setCustomerId = useCallback(
    (id: number | null) => {
      setCustomerFilter(id);
      setCustomerIdRaw(id);
      if (tenantId !== null) store(`automato_customer_${tenantId}`, id === null ? null : String(id));
    },
    [tenantId],
  );

  // Kunden der Umgebung laden; der gemerkte Kunde gilt nur, solange es ihn gibt
  const [customersVersion, setCustomersVersion] = useState(0);
  useEffect(() => {
    setCustomerFilter(null);
    setCustomerIdRaw(null);
    setCustomers([]);
    if (tenantId === null) return;
    fetchCustomers(tenantId)
      .then((list) => {
        setCustomers(list.filter((c) => !c.shared));
        const saved = Number(stored(`automato_customer_${tenantId}`));
        if (saved && list.some((c) => c.id === saved && !c.shared)) {
          setCustomerFilter(saved);
          setCustomerIdRaw(saved);
        }
      })
      .catch(() => setCustomers([]));
  }, [tenantId, customersVersion]);

  const go = useCallback((v: View, p: ViewParams = {}) => {
    setView(v);
    setParams(p);
    setFindingType("");
    const query = new URLSearchParams(p).toString();
    window.history.replaceState(null, "", `#${v}${query ? `?${query}` : ""}`);
  }, []);

  useEffect(() => {
    const onHash = () => {
      setView(viewFromHash());
      setParams(paramsFromHash());
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
  }, [theme]);

  const toggleTheme = () => {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = theme ? theme === "dark" : prefersDark;
    const next = isDark ? "light" : "dark";
    setTheme(next);
    store("automato_theme", next);
  };

  const loadTenants = useCallback(async (): Promise<Tenant[]> => {
    try {
      const list = await fetchTenants();
      setTenants(list);
      setTenantsErr(null);
      return list;
    } catch (e) {
      setTenants([]);
      setTenantsErr(e);
      return [];
    }
  }, []);

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
    loadTenants();
  }, [loadTenants]);

  // Gewählten Mandanten gegen die erlaubte Liste prüfen. Nicht-Admins brauchen immer
  // einen konkreten Mandanten (die API verlangt ihn bei mehreren Zuordnungen).
  useEffect(() => {
    if (tenants === null) return;
    const known = tenants.some((t) => t.id === tenantId);
    if (tenantId !== null && !known) setTenantId(me.isAdmin ? null : (tenants[0]?.id ?? null));
    else if (tenantId === null && !me.isAdmin && tenants.length) setTenantId(tenants[0].id);
  }, [tenants, tenantId, me.isAdmin, setTenantId]);

  const noTenant = !me.isAdmin && tenants !== null && tenants.length === 0;
  const tenantReady = tenants !== null && (me.isAdmin || tenantId !== null);

  const loadStats = useCallback(() => {
    if (!tenantReady) return;
    setStats({ rules: null, nat: null, findings: null, error: null });
    Promise.all([fetchRuleSummary(tenantId), fetchNat({ tenantId, limit: 1 }), fetchFindingSummary(tenantId)])
      .then(([rules, nat, findings]) => setStats({ rules, nat: nat.total, findings, error: null }))
      .catch((e) => setStats({ rules: EMPTY_RULES, nat: 0, findings: EMPTY_FINDINGS, error: e }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, tenantReady, customerId]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const tenant = tenants?.find((t) => t.id === tenantId) ?? null;
  const tenantLabel = tenant ? tenant.name : "Alle Mandanten";
  const openFindings = stats.findings?.open;
  const badges: Partial<Record<View, { n: number | undefined; warn?: boolean }>> = {
    regeln: { n: stats.rules?.total },
    nat: { n: stats.nat ?? undefined },
    findings: { n: openFindings, warn: (openFindings ?? 0) > 0 },
  };

  // Kunden-Benutzer sehen nur die freigegebenen Doku-Ansichten
  const shown = (n: NavEntry) => (!n.adminOnly || me.isAdmin) && (!me.isCustomer || !!n.customer);
  const entry = NAV.find((n) => n.key === view);
  const current: View = entry && shown(entry) ? view : "uebersicht";
  // Applikationen: Kunden-Benutzer pflegen nichts (Triage eigener Findings bleibt)
  const docMe = me.isCustomer ? { ...me, isOperator: false } : me;
  const docView = NAV.find((n) => n.key === current)?.section === "doc";
  const https = window.location.protocol === "https:";

  const renderNav = (section: "doc" | "admin") =>
    NAV.filter((n) => n.section === section && shown(n)).map((n) => {
      const b = badges[n.key];
      return (
        <button
          key={n.key}
          className={`nav-item ${current === n.key ? "active" : ""}`}
          onClick={() => go(n.key)}
        >
          <Icon name={n.icon} className="ico" size={17} />
          {n.label}
          {b && b.n !== undefined && (
            <span className={`badge-n ${b.warn ? "warn" : ""}`}>{b.n}</span>
          )}
        </button>
      );
    });

  return (
    <div className="app">
      <div className="topbrand">
        <div className="brand-mark">A</div>
        <div className="brand-name">Automato</div>
      </div>

      <header className="topbar">
        <label className="mandant">
          <span className="label">Mandant</span>
          <select
            value={tenantId ?? ""}
            onChange={(e) => setTenantId(e.target.value ? Number(e.target.value) : null)}
            disabled={tenants === null || (!me.isAdmin && (tenants?.length ?? 0) <= 1)}
          >
            {me.isAdmin && <option value="">Alle Mandanten</option>}
            {tenants?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.code ? `${t.name} · ${t.code}` : t.name}
              </option>
            ))}
            {noTenant && <option value="">kein Mandant zugeordnet</option>}
          </select>
        </label>
        {customers.length > (me.isCustomer ? 1 : 0) && (
          <label className="mandant" title="Ausschnitt eines Kunden samt geteiltem Teil (ADR 0018)">
            <span className="label">Kunde</span>
            <select
              aria-label="Kunde"
              value={customerId ?? ""}
              onChange={(e) => setCustomerId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">alle</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.code ? `${c.name} · ${c.code}` : c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="spacer" />
        <span
          className={`https ${https ? "ok" : "bad"}`}
          title={https ? "Verbindung verschlüsselt" : "Unverschlüsselte Verbindung"}
        >
          <Icon name="lock" size={14} />
          <span>{https ? "HTTPS" : "HTTP"}</span>
        </span>
        <button className="iconbtn" onClick={toggleTheme} title="Hell/Dunkel wechseln">
          <Icon name="sun" />
        </button>
        <div className="usermenu">
          <button className="usermenu-btn" onClick={() => setMenuOpen((o) => !o)}>
            <span className="avatar">{initials(me.name)}</span>
            <span className="u-text">
              <div className="u-name">{me.name}</div>
              <div className="u-role">{auth.roleLabel(me)}</div>
            </span>
          </button>
          {menuOpen && (
            <UserMenu cfg={cfg} me={me} tenants={tenants ?? []} onClose={() => setMenuOpen(false)} onGo={go} />
          )}
        </div>
      </header>

      <nav className="nav">
        <div className="nav-sec">Dokumentation</div>
        {renderNav("doc")}
        {!me.isCustomer && <div className="nav-sec">Administration</div>}
        {renderNav("admin")}
        <div className="nav-foot">
          <span className={`dot ${health?.status === "ok" ? "g" : "r"}`} />
          {health
            ? `API ${health.status} · DB ${health.db}${health.version ? ` · v${health.version}` : ""}`
            : "API nicht erreichbar"}
        </div>
      </nav>

      <main className="main" key={`${tenantId ?? "alle"}-${customerId ?? "alle"}`}>
        {tenantsErr ? <ErrorBox error={tenantsErr} prefix="Mandanten laden" /> : null}
        {noTenant && docView ? (
          <Note warn>
            Ihrem Konto ist noch kein Mandant zugeordnet. Bitte den Administrator, in Keycloak das
            Attribut <code>tenant_ids</code> zu setzen, und danach neu anmelden.
          </Note>
        ) : me.isCustomer && !me.customerIds?.length ? (
          <Note warn>
            Ihrem Konto ist noch kein Kunde zugeordnet. Bitte den Betreuer, Sie in Automato (Administration →
            Kunden → Kunden-Benutzer) einem Kunden zuzuordnen – oder in Keycloak das Attribut <code>customer_ids</code>{" "}
            zu setzen und danach neu anmelden.
          </Note>
        ) : (
          <>
            {current === "uebersicht" && (
              <Overview
                me={me}
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                tenants={tenants ?? []}
                stats={stats}
                onGo={go}
                onReload={loadStats}
              />
            )}
            {current === "laeufe" && <Runs me={me} tenantId={tenantId} tenantLabel={tenantLabel} />}
            {current === "regeln" && tenantReady && (
              <RulesTable
                key={JSON.stringify(params)}
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                params={params}
                onGo={go}
                isAdmin={me.isAdmin}
                customer={me.isCustomer}
              />
            )}
            {current === "objekte" && tenantReady && (
              <Objects
                key={JSON.stringify(params)}
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                params={params}
                onGo={go}
                customer={me.isCustomer}
              />
            )}
            {current === "nat" && tenantReady && (
              <NatTable
                key={JSON.stringify(params)}
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                params={params}
                onGo={go}
              />
            )}
            {current === "apps" && tenantReady && (
              <AppDoc me={docMe} tenantId={tenantId} params={params} onGo={go} />
            )}
            {current === "findings" && tenantReady && (
              <Findings
                key={JSON.stringify(params)}
                me={me}
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                initialType={findingType || params.type || ""}
                initialQuery={params.q ?? ""}
                onChanged={loadStats}
                onGo={go}
              />
            )}
            {current === "abgleich" && tenantReady && (
              <Reconcile
                me={me}
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                onChanged={loadStats}
                onOpenFindings={(t) => {
                  go("findings");
                  setFindingType(t);
                }}
              />
            )}
            {current === "wege" && (
              <Paths
                key={JSON.stringify(params)}
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                canEdit={me.isAdmin || me.isOperator}
                params={params}
              />
            )}
            {current === "routing" && (
              <Routing tenantId={tenantId} tenantLabel={tenantLabel} canEdit={me.isAdmin || me.isOperator} />
            )}
            {current === "design" && (
              <Design
                tenantId={tenantId}
                tenantLabel={tenantLabel}
                canEdit={!me.isCustomer && (me.isAdmin || me.isOperator)}
                isAdmin={me.isAdmin}
                isCustomer={me.isCustomer}
              />
            )}
            {current === "users" && <Users cfg={cfg} me={me} tenants={tenants ?? []} />}
            {current === "tenants" && (
              <Tenants
                me={me}
                tenants={tenants}
                tenantId={tenantId}
                onSelectTenant={setTenantId}
                onTenantsChanged={loadTenants}
              />
            )}
            {current === "kunden" && (
              <Customers
                tenantId={tenantId}
                canEdit={me.isAdmin || me.isOperator}
                isAdmin={me.isAdmin}
                onChanged={() => setCustomersVersion((v) => v + 1)}
              />
            )}
            {current === "dict" && <NamingProfiles me={me} tenants={tenants ?? []} />}
            {current === "agents" && <Agents tenants={tenants ?? []} tenantId={tenantId} onGo={go} />}
            {current === "netbox" && <NetBoxSync tenantId={tenantId} tenantLabel={tenantLabel} onGo={go} />}
            {current === "system" && <System />}
          </>
        )}
      </main>
    </div>
  );
}

function UserMenu({
  cfg,
  me,
  tenants,
  onClose,
  onGo,
}: {
  cfg: AuthConfig;
  me: auth.Me;
  tenants: Tenant[];
  onClose: () => void;
  onGo: (v: View) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const t = setTimeout(() => document.addEventListener("mousedown", onDown));
    return () => {
      clearTimeout(t);
      document.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  const scope = me.tenantIds === null ? "alle Mandanten" : tenants.map((t) => t.name).join(", ") || "keine";
  return (
    <div className="menu" ref={ref}>
      <div className="menu-head">
        <b>{me.name}</b>
        {me.username} · {auth.roleLabel(me)}
        <div>Mandanten: {scope}</div>
      </div>
      {!me.isCustomer && (
        <button
          onClick={() => {
            onGo("users");
            onClose();
          }}
        >
          Benutzer &amp; Rollen
        </button>
      )}
      {cfg.auth_mode === "oidc" ? (
        <>
          <a href={`${cfg.issuer}/account`} target="_blank" rel="noreferrer">
            Mein Konto (Passwort, OTP)
          </a>
          <button onClick={() => auth.logout()}>Abmelden</button>
        </>
      ) : (
        <div className="menu-head" style={{ border: "none", margin: 0 }}>
          Entwicklungsmodus (auth_mode=dev) – keine Anmeldung aktiv.
        </div>
      )}
    </div>
  );
}
