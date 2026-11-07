import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  AuthConfig,
  Customer,
  fetchAuthConfig,
  fetchCustomers,
  fetchFindingSummary,
  fetchHealth,
  fetchNat,
  fetchModules,
  fetchRuleSummary,
  fetchServerMe,
  fetchTenants,
  FindingSummary,
  HealthStatus,
  RuleSummary,
  setCustomerFilter,
  setCustomerUser,
  Tenant,
  ModuleInfo,
} from "./api";
import * as auth from "./auth";
import { L, lang, setLang } from "./i18n";
import { hiddenViews, MODULES_CHANGED, ModulesContext } from "./moduleState";
import { Stats, View, ViewEntry, ViewParams, ViewProps, VIEWS } from "./modules";
import { ErrorBox, Icon, Loading, Note } from "./ui";

// Ansichten je Modul (0.110.0, ADR 0039): Navigation und Anzeige aus dem Verzeichnis `src/modules`; jede Ansicht lädt erst
// beim Öffnen (0.109.1), das erste Paket der Oberfläche bleibt klein
export type { Stats, View, ViewParams } from "./modules";

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

const VIEW_KEYS = VIEWS.map((n) => n.key) as string[];

function viewFromHash(): View {
  const h = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return (VIEW_KEYS.includes(h) ? h : "uebersicht") as View;
}

// Parameter hinter der Ansicht (#regeln?object=H_web01) - Verlinkung zwischen den Ansichten
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
        <ErrorBox error={problem} prefix={L("Anmeldung nicht möglich", "Sign-in not possible")} />
        <Note>
          {L(
            <>
              Häufige Ursachen: im Keycloak-Realm fehlt der Audience-Mapper <code>automato-api</code>,{" "}
              <code>AUTOMATO_OIDC_ISSUER</code>/<code>AUTOMATO_OIDC_JWKS_URL</code> passen nicht zum Realm,
              der Orchestrator erreicht Keycloak nicht, oder die Uhrzeit der VMs weicht ab (NTP).
              Details:{" "}
            </>,
            <>
              Common causes: the Keycloak realm lacks the audience mapper <code>automato-api</code>,{" "}
              <code>AUTOMATO_OIDC_ISSUER</code>/<code>AUTOMATO_OIDC_JWKS_URL</code> do not match the realm,
              the orchestrator cannot reach Keycloak, or the clocks of the VMs differ (NTP). Details:{" "}
            </>,
          )}
          <code>docker compose logs orchestrator | grep OIDC</code>
        </Note>
        <div style={{ display: "flex", gap: 9 }}>
          <button className="btn btn-primary" onClick={() => auth.login()}>
            {L("Erneut anmelden", "Sign in again")}
          </button>
          <button className="btn btn-ghost" onClick={() => auth.logout()}>
            {L("Abmelden", "Sign out")}
          </button>
        </div>
      </Splash>
    );
  }
  if (boot.state === "loading" || boot.state === "redirect") {
    return (
      <Splash>
        {boot.state === "loading"
          ? L("Verbindung wird aufgebaut…", "Connecting…")
          : L("Weiterleitung zur Anmeldung…", "Redirecting to sign-in…")}
      </Splash>
    );
  }
  if (boot.state === "error") {
    return (
      <Splash>
        <ErrorBox error={boot.error} prefix={L("Anmeldung fehlgeschlagen", "Sign-in failed")} />
        <button
          className="btn btn-primary btn-block"
          onClick={() => (boot.oidc ? auth.login() : window.location.reload())}
        >
          {boot.oidc ? L("Erneut anmelden", "Sign in again") : L("Neu laden", "Reload")}
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
        <p className="splash-sub">
          {L("Automatisierungs- und Dokumentationsplattform.", "Automation and documentation platform.")}
        </p>
        {children}
        <div className="splash-foot">
          <Icon name="lock" size={13} />
          {window.location.protocol === "https:"
            ? L("Verbindung über HTTPS", "Connection via HTTPS")
            : L("Verbindung ohne HTTPS", "Connection without HTTPS")}
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
  // Module je Mandant (0.107.0, ADR 0036): ausgeschaltete blendet die Navigation aus
  const [mods, setMods] = useState<ModuleInfo[] | null>(null);
  const [modsVersion, setModsVersion] = useState(0);

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

  useEffect(() => {
    if (!tenantReady) return;
    let live = true;
    fetchModules(tenantId)
      .then((m) => live && setMods(m))
      .catch(() => live && setMods(null));
    return () => {
      live = false;
    };
  }, [tenantId, tenantReady, modsVersion]);
  useEffect(() => {
    const changed = () => setModsVersion((v) => v + 1);
    window.addEventListener(MODULES_CHANGED, changed);
    return () => window.removeEventListener(MODULES_CHANGED, changed);
  }, []);

  const loadStats = useCallback(() => {
    if (!tenantReady) return;
    setStats({ rules: null, nat: null, findings: null, error: null });
    // Findings aus (0.107.0): der Server antwortet 403 – dann ohne Zähler
    const findings = fetchFindingSummary(tenantId).catch(() => EMPTY_FINDINGS);
    Promise.all([fetchRuleSummary(tenantId), fetchNat({ tenantId, limit: 1 }), findings])
      .then(([rules, nat, findings]) => setStats({ rules, nat: nat.total, findings, error: null }))
      .catch((e) => setStats({ rules: EMPTY_RULES, nat: 0, findings: EMPTY_FINDINGS, error: e }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, tenantReady, customerId]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const tenant = tenants?.find((t) => t.id === tenantId) ?? null;
  const tenantLabel = tenant ? tenant.name : L("Alle Mandanten", "All tenants");
  const openFindings = stats.findings?.open;
  const badges: Partial<Record<View, { n: number | undefined; warn?: boolean }>> = {
    regeln: { n: stats.rules?.total },
    nat: { n: stats.nat ?? undefined },
    findings: { n: openFindings, warn: (openFindings ?? 0) > 0 },
  };

  // Kunden-Benutzer sehen nur die freigegebenen Doku-Ansichten (feinere Rollen: dazu, was ihre
  // Capabilities erlauben); Betreuer nicht die Seiten nur für Kunden-Benutzer
  const hidden = hiddenViews(mods);
  const shown = (n: ViewEntry) =>
    !hidden.has(n.key) &&
    (!n.adminOnly || me.isAdmin) &&
    (me.isCustomer ? !!n.customer && (!n.cap || auth.customerCan(me, n.cap)) : !n.customerOnly);
  const entry = VIEWS.find((n) => n.key === view);
  const current: View = entry && shown(entry) ? view : "uebersicht";
  // Applikationen: Kunden-Benutzer pflegen nichts als Operator – mit der Kunden-Rolle editor nur
  // die Applikationen, die der Server als `editable` meldet (feinere Rollen)
  const docMe = me.isCustomer ? { ...me, isOperator: false } : me;
  const currentEntry = VIEWS.find((n) => n.key === current)!;
  const docView = currentEntry.section === "doc";
  const https = window.location.protocol === "https:";
  const viewProps: ViewProps = {
    cfg,
    me,
    docMe,
    tenantId,
    tenantLabel,
    tenants: tenants ?? [],
    tenantsList: tenants,
    params,
    paramsKey: JSON.stringify(params),
    stats,
    findingType,
    go,
    reloadStats: loadStats,
    reloadTenants: loadTenants,
    selectTenant: setTenantId,
    customersChanged: () => setCustomersVersion((v) => v + 1),
    openFindings: (t) => {
      go("findings");
      setFindingType(t);
    },
  };

  const renderNav = (section: "doc" | "admin") =>
    VIEWS.filter((n) => n.section === section && shown(n)).map((n) => {
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
          <span className="label">{L("Mandant", "Tenant")}</span>
          <select
            value={tenantId ?? ""}
            onChange={(e) => setTenantId(e.target.value ? Number(e.target.value) : null)}
            disabled={tenants === null || (!me.isAdmin && (tenants?.length ?? 0) <= 1)}
          >
            {me.isAdmin && <option value="">{L("Alle Mandanten", "All tenants")}</option>}
            {tenants?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.code ? `${t.name} · ${t.code}` : t.name}
              </option>
            ))}
            {noTenant && <option value="">{L("kein Mandant zugeordnet", "no tenant assigned")}</option>}
          </select>
        </label>
        {customers.length > (me.isCustomer ? 1 : 0) && (
          <label
            className="mandant"
            title={L("Ausschnitt eines Kunden samt geteiltem Teil (ADR 0018)", "Scope of a customer including the shared part (ADR 0018)")}
          >
            <span className="label">{L("Kunde", "Customer")}</span>
            <select
              aria-label={L("Kunde", "Customer")}
              value={customerId ?? ""}
              onChange={(e) => setCustomerId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">{L("alle", "all")}</option>
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
          title={https ? L("Verbindung verschlüsselt", "Connection encrypted") : L("Unverschlüsselte Verbindung", "Unencrypted connection")}
        >
          <Icon name="lock" size={14} />
          <span>{https ? "HTTPS" : "HTTP"}</span>
        </span>
        <button className="iconbtn" onClick={toggleTheme} title={L("Hell/Dunkel wechseln", "Toggle light/dark")}>
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
        <div className="nav-sec">{L("Dokumentation", "Documentation")}</div>
        {renderNav("doc")}
        {VIEWS.some((n) => n.section === "admin" && shown(n)) && (
          <div className="nav-sec">{L("Administration", "Administration")}</div>
        )}
        {renderNav("admin")}
        <div className="nav-foot">
          <span className={`dot ${health?.status === "ok" ? "g" : "r"}`} />
          {health
            ? `API ${health.status} · DB ${health.db}${health.version ? ` · v${health.version}` : ""}`
            : L("API nicht erreichbar", "API not reachable")}
        </div>
      </nav>

      <ModulesContext.Provider value={mods}>
      <main className="main" key={`${tenantId ?? "alle"}-${customerId ?? "alle"}`}>
        {tenantsErr ? <ErrorBox error={tenantsErr} prefix={L("Mandanten laden", "Loading tenants")} /> : null}
        {customerId != null && !me.isCustomer && docView && (
          // 0.76.0: der Kunde oben filtert alle Ansichten; Geteiltes ist markiert
          <div className="customer-scope" id="customer-scope">
            {L(
              <>
                Ausschnitt <b>{customers.find((c) => c.id === customerId)?.name ?? "Kunde"}</b> – alle Ansichten zeigen nur, was
                zu diesem Kunden gehört; <span className="chip na">geteilt</span> = gemeinsamer Teil der Umgebung (z. B. Tenant
                common, VDOM bzw. Firewall für mehrere Kunden).
              </>,
              <>
                Scope <b>{customers.find((c) => c.id === customerId)?.name ?? "Customer"}</b> – all views show only what
                belongs to this customer; <span className="chip na">shared</span> = common part of the environment (e.g.
                tenant common, VDOM or firewall for several customers).
              </>,
            )}
            <button className="linkbtn" onClick={() => setCustomerId(null)}>
              {L("alle Kunden", "all customers")}
            </button>
          </div>
        )}
        {noTenant && docView ? (
          <Note warn>
            {L(
              <>
                Ihrem Konto ist noch kein Mandant zugeordnet. Bitte den Administrator, in Keycloak das
                Attribut <code>tenant_ids</code> zu setzen, und danach neu anmelden.
              </>,
              <>
                No tenant is assigned to your account yet. Ask the administrator to set the attribute{" "}
                <code>tenant_ids</code> in Keycloak, then sign in again.
              </>,
            )}
          </Note>
        ) : me.isCustomer && !me.customerIds?.length ? (
          <Note warn>
            {L(
              <>
                Ihrem Konto ist noch kein Kunde zugeordnet. Bitte den Betreuer, Sie in Automato (Administration →
                Kunden → Kunden-Benutzer) einem Kunden zuzuordnen – oder in Keycloak das Attribut <code>customer_ids</code>{" "}
                zu setzen und danach neu anmelden.
              </>,
              <>
                No customer is assigned to your account yet. Ask your contact to assign you to a customer in Automato
                (Administration → Customers → Customer users) – or to set the attribute <code>customer_ids</code> in
                Keycloak, then sign in again.
              </>,
            )}
          </Note>
        ) : (
          // je Ansicht ein eigener Ladebereich: die alte verschwindet sofort, statt versteckt
          // stehen zu bleiben, bis die neue geladen ist
          <Suspense key={current} fallback={<Loading />}>
            {(!currentEntry.needsTenant || tenantReady) && currentEntry.render(viewProps)}
          </Suspense>
        )}
      </main>
      </ModulesContext.Provider>
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

  const scope =
    me.tenantIds === null ? L("alle Mandanten", "all tenants") : tenants.map((t) => t.name).join(", ") || L("keine", "none");
  return (
    <div className="menu" ref={ref}>
      <div className="menu-head">
        <b>{me.name}</b>
        {me.username} · {auth.roleLabel(me)}
        <div>
          {L("Mandanten", "Tenants")}: {scope}
        </div>
      </div>
      {/* Sprache der Oberfläche (0.86.0) – Umschalten lädt die Seite neu */}
      <div className="menu-lang" id="lang-switch">
        <span>{L("Sprache", "Language")}</span>
        <button className={lang === "de" ? "active" : ""} disabled={lang === "de"} onClick={() => setLang("de")}>
          Deutsch
        </button>
        <button className={lang === "en" ? "active" : ""} disabled={lang === "en"} onClick={() => setLang("en")}>
          English
        </button>
      </div>
      {!me.isCustomer && (
        <button
          onClick={() => {
            onGo("users");
            onClose();
          }}
        >
          {L("Benutzer & Rollen", "Users & roles")}
        </button>
      )}
      {cfg.auth_mode === "oidc" ? (
        <>
          <a href={`${cfg.issuer}/account`} target="_blank" rel="noreferrer">
            {L("Mein Konto (Passwort, OTP)", "My account (password, OTP)")}
          </a>
          <button onClick={() => auth.logout()}>{L("Abmelden", "Sign out")}</button>
        </>
      ) : (
        <div className="menu-head" style={{ border: "none", margin: 0 }}>
          {L(
            "Entwicklungsmodus (auth_mode=dev) – keine Anmeldung aktiv.",
            "Development mode (auth_mode=dev) – no sign-in active.",
          )}
        </div>
      )}
    </div>
  );
}
