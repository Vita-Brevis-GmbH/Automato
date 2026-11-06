import type { Capability, CustomerRole, ServerMe } from "./api";
// OIDC-Login fürs Web-UI (E1.3b, ADR 0007) via oidc-client-ts gegen Keycloak.
// Authorization Code + PKCE. Der UserManager ist die einzige Quelle für das Token:
// abgelaufene Tokens werden still per Refresh-Token erneuert, sonst geht es zurück
// zum Login (vorher: einmal gespeichertes Token lief nach 5 min ab -> alles 401).
// Im dev-Modus (auth_mode != "oidc") ist kein Manager aktiv und es gibt kein Token.
import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import type { AuthConfig } from "./api";
import { L } from "./i18n";

const redirectUri = window.location.origin + window.location.pathname;
let manager: UserManager | null = null;
let renewing: Promise<User | null> | null = null;
let renewError = "";

// Schleifenschutz: wurde gerade erst angemeldet und das Token trotzdem abgelehnt, hilft
// ein weiterer Login nicht (Keycloak-SSO schickt sofort zurück) -> Grund anzeigen.
const LOGIN_AT = "automato_login_at";
const LOOP_WINDOW_MS = 60_000;
let problemHandler: ((reason: string) => void) | null = null;

// Rückmeldung von Keycloak mit Fehler statt ?code=… (0.139.2, Befund des Anwenders:
// „?error=temporarily_unavailable&error_description=authentication_expired“ blieb in der Adresszeile
// stehen, die Anmeldung wurde nicht neu gestartet). Bei diesen Fehlern erwartet Keycloak einen neuen
// Anlauf (z. B. Anmeldung abgelaufen oder in einem anderen Tab schon abgeschlossen) - einmal, danach
// steht der Grund da.
const RESTART_ERRORS = new Set(["temporarily_unavailable", "login_required", "interaction_required"]);
const RETRY = "automato_login_retry";
let callbackError: { error: string; reason: string } | null = null;

// Alle Tabs teilen die Anmeldung (localStorage). Damit nicht jeder Tab für sich zu Keycloak umleitet
// (0.139.2): ein verdeckter Tab wartet, bis er wieder sichtbar ist oder ein anderer Tab neu
// angemeldet hat; ein sichtbarer wartet kurz, wenn gerade ein anderer Tab umgeleitet hat.
const REDIRECT_AT = "automato_login_redirect_at";
const OTHER_TAB_MS = 20_000;
let waiting: Promise<User | null> | null = null;
// Tab ist auf dem Weg zu Keycloak: abgebrochene Anfragen sind dann kein Fehler
let leaving = false;

export function onProblem(handler: (reason: string) => void): void {
  problemHandler = handler;
}

export const isLeaving = () => leaving;

function session(key: string, value?: string | null): string | null {
  return stored(() => sessionStorage, key, value);
}

function local(key: string, value?: string | null): string | null {
  return stored(() => localStorage, key, value);
}

function stored(store: () => Storage, key: string, value?: string | null): string | null {
  try {
    if (value === undefined) return store().getItem(key);
    if (value === null) store().removeItem(key);
    else store().setItem(key, value);
  } catch {
    /* Speicher nicht verfügbar */
  }
  return null;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Kennung dieses Tabs (bleibt über den Umweg zu Keycloak erhalten): die eigene Umleitung ist
// kein „anderer Tab“, auf den zu warten wäre
function tabId(): string {
  let id = session("automato_tab");
  if (!id) {
    id = Math.random().toString(36).slice(2);
    session("automato_tab", id);
  }
  return id;
}

export function init(cfg: AuthConfig): UserManager {
  // Altlast der ersten Version (Token direkt in localStorage) entfernen.
  try {
    localStorage.removeItem("automato_token");
  } catch {
    /* localStorage nicht verfügbar */
  }
  manager = new UserManager({
    authority: cfg.issuer,
    client_id: cfg.client_id,
    redirect_uri: redirectUri,
    post_logout_redirect_uri: redirectUri,
    response_type: "code",
    scope: cfg.scope || "openid profile",
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    automaticSilentRenew: true,
  });
  return manager;
}

export const isOidc = () => manager !== null;

// Verarbeitet den Rückkommer von Keycloak (?code=… bzw. ?error=…) und stellt die vorherige
// Ansicht (#hash) wieder her. true = Callback behandelt.
export async function handleRedirectCallback(): Promise<boolean> {
  if (!manager) return false;
  const params = new URLSearchParams(window.location.search);
  if (params.has("error") && params.has("state")) return handleErrorCallback(params);
  if (!params.has("code")) return false;
  const user = await manager.signinRedirectCallback();
  session(RETRY, null);
  const hash = (user.state as { hash?: string } | undefined)?.hash ?? "";
  window.history.replaceState({}, document.title, window.location.pathname + hash);
  return true;
}

// Fehler statt Anmeldung: Adresszeile bereinigen und Ansicht wiederherstellen. Ob neu angemeldet
// wird, entscheidet ensureSession - hat ein anderer Tab inzwischen eine gültige Sitzung, gilt diese.
async function handleErrorCallback(params: URLSearchParams): Promise<boolean> {
  let hash = window.location.hash;
  try {
    await manager?.signinRedirectCallback(); // entfernt den gespeicherten Zustand, wirft den Fehler
  } catch (e) {
    const state = (e as { state?: { hash?: string } }).state;
    if (state?.hash) hash = state.hash;
  }
  window.history.replaceState({}, document.title, window.location.pathname + hash);
  const error = params.get("error") ?? "";
  callbackError = {
    error,
    reason: `${L("Keycloak hat die Anmeldung nicht abgeschlossen", "Keycloak did not complete the sign-in")}: ${
      params.get("error_description") || error
    }`,
  };
  return true;
}

async function renew(): Promise<User | null> {
  if (!manager) return null;
  if (!renewing) {
    renewing = manager
      .signinSilent()
      .catch((e) => {
        renewError = e instanceof Error ? e.message : String(e);
        return null;
      })
      .finally(() => {
        renewing = null;
      });
  }
  return renewing;
}

// Gültige Sitzung liefern; sonst still erneuern; sonst die eines anderen Tabs übernehmen; sonst
// Login starten (Redirect).
export async function ensureSession(): Promise<User | null> {
  if (!manager || leaving) return null;
  const user = await manager.getUser();
  if (user && !user.expired) return valid(user);
  if (user?.refresh_token) {
    const renewed = await renew();
    if (renewed && !renewed.expired) return valid(renewed);
  }
  const other = await fromOtherTab();
  if (other) return valid(other);
  await relogin(
    user
      ? L("Token-Erneuerung bei Keycloak fehlgeschlagen", "Token renewal at Keycloak failed") +
          (renewError ? `: ${renewError}` : "")
      : "",
  );
  return null;
}

function valid(user: User): User {
  callbackError = null; // eine Fehler-Rückmeldung von Keycloak ist damit erledigt
  return user;
}

// Sitzung eines anderen Tabs abwarten (siehe REDIRECT_AT); null = selbst anmelden.
function fromOtherTab(): Promise<User | null> {
  if (!waiting) {
    waiting = (async () => {
      const [tab, at] = (local(REDIRECT_AT) ?? "").split(":");
      const until = tab && tab !== tabId() ? Number(at) + OTHER_TAB_MS : 0;
      for (;;) {
        const user = await manager?.getUser();
        if (user && !user.expired) return user;
        if (document.visibilityState !== "hidden" && Date.now() >= until) return null;
        await sleep(1000);
      }
    })().finally(() => {
      waiting = null;
    });
  }
  return waiting;
}

export async function accessToken(): Promise<string | null> {
  const user = await ensureSession();
  return user?.access_token ?? null;
}

// Neu anmelden (Redirect zu Keycloak) - ausser es wurde gerade erst angemeldet: dann
// wird der Grund gemeldet statt endlos zwischen UI und Keycloak hin und her zu leiten.
export async function relogin(reason: string): Promise<void> {
  if (leaving) return;
  if (callbackError) {
    // Keycloak hat die letzte Anmeldung mit Fehler zurückgeschickt: einmal neu, sonst Grund zeigen
    const { error, reason: why } = callbackError;
    callbackError = null;
    if (RESTART_ERRORS.has(error) && !session(RETRY)) {
      session(RETRY, "1");
      await redirect();
      return;
    }
    session(RETRY, null);
    problemHandler?.(why);
    return;
  }
  const last = Number(session(LOGIN_AT) ?? 0);
  if (last && Date.now() - last < LOOP_WINDOW_MS) {
    problemHandler?.(
      reason || L("Anmeldung wird vom Orchestrator nicht akzeptiert.", "Sign-in is not accepted by the orchestrator."),
    );
    return;
  }
  await redirect();
}

// Ausdrücklicher Login (Button „Erneut anmelden“).
export async function login(): Promise<void> {
  if (!manager) return;
  callbackError = null;
  session(RETRY, null);
  await redirect();
}

// Zu Keycloak umleiten. Die gemeinsame Sitzung der anderen Tabs bleibt stehen (0.139.2: bis 0.139.1
// löschte jeder umleitende Tab sie auch für alle anderen) - die neue ersetzt sie nach der Anmeldung.
async function redirect(): Promise<void> {
  if (!manager || leaving) return;
  session(LOGIN_AT, String(Date.now()));
  local(REDIRECT_AT, `${tabId()}:${Date.now()}`);
  leaving = true;
  try {
    await manager.signinRedirect({ state: { hash: window.location.hash } });
  } catch (e) {
    leaving = false; // Keycloak nicht erreichbar: Grund zeigen statt still zu warten
    problemHandler?.(
      `${L("Keycloak nicht erreichbar", "Keycloak not reachable")}: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
}

export async function logout(): Promise<void> {
  if (!manager) return;
  await manager.signoutRedirect();
}

// --- Anzeige: wer ist angemeldet (nur Darstellung, Prüfung macht der Orchestrator) ---

export interface Me {
  name: string;
  username: string;
  roles: string[];
  tenantIds: number[] | null; // null = alle (admin)
  isAdmin: boolean;
  isOperator: boolean; // operator oder admin
  // Kunden-Benutzer (ADR 0018): nur die Doku ihres Ausschnitts, nie Admin; null = Betreuer
  customerIds: number[] | null;
  isCustomer: boolean;
  dev: boolean;
  // feinere Rollen (ADR 0018 Ergänzung): Capabilities laut Orchestrator, Kunden-Rolle je Kunde
  // und die Kunden mit Namen – die Oberfläche blendet danach ein und aus, prüfen tut der Server
  capabilities: Capability[];
  customerRole: CustomerRole | null;
  customerRoles: Record<string, CustomerRole>;
  customers: { id: number; name: string }[];
  logins: string[]; // eigene Kennungen laut Orchestrator (eigene Zuordnung erkennen)
}

export const DEV_ME: Me = {
  name: L("Entwicklung", "Development"),
  username: "dev",
  roles: ["admin"],
  tenantIds: null,
  isAdmin: true,
  isOperator: true,
  customerIds: null,
  isCustomer: false,
  dev: true,
  capabilities: [],
  customerRole: null,
  customerRoles: {},
  customers: [],
  logins: [],
};

// Darf der Kunden-Benutzer das (für mindestens einen seiner Kunden)? Betreuer: immer false –
// für sie gelten die Rollen (isOperator/isAdmin)
export function customerCan(me: Me, cap: Capability): boolean {
  return me.isCustomer && me.capabilities.includes(cap);
}

// Kunden-Rollen aufsteigend mit Erklärung (Auswahl bei den Kunden-Benutzern)
export const CUSTOMER_ROLES: CustomerRole[] = ["viewer", "triage", "editor", "customer_admin"];
export const customerRoleRank = (role: string | null | undefined): number =>
  CUSTOMER_ROLES.indexOf((role === "operator" ? "triage" : role) as CustomerRole);
export function customerRoleLabel(role: string | null | undefined): string {
  switch (role === "operator" ? "triage" : role) {
    case "triage":
      return L("Triage", "Triage");
    case "editor":
      return L("Editor", "Editor");
    case "customer_admin":
      return L("Kunden-Admin", "Customer admin");
    default:
      return L("Lesen", "Read");
  }
}
export function customerRoleHint(role: string | null | undefined): string {
  switch (role === "operator" ? "triage" : role) {
    case "triage":
      return L("lesen + eigene Findings quittieren und kommentieren", "read + acknowledge and comment on own findings");
    case "editor":
      return L(
        "wie Triage + eigene Applikationen pflegen: Stammdaten, Regeln zuordnen/ausschliessen, Regeln je Kategorie ausblenden (nur Applikationen ganz beim Kunden)",
        "like triage + maintain own applications: master data, assign/exclude rules, hide rules per category (only applications entirely within the customer)",
      );
    case "customer_admin":
      return L(
        "wie Editor + Kunden-Benutzer des eigenen Kunden zuordnen (nie sich selbst, höchstens die eigene Rolle)",
        "like editor + assign customer users of the own customer (never oneself, at most the own role)",
      );
    default:
      return L("die Doku des Kunden lesen", "read the customer's documentation");
  }
}

// dev-Modus: Kunden-Benutzer vorführen bzw. testen (`automato_dev_customer` =
// "<Mandant>:<Kunden>[:<Kunden-Rolle>]", z. B. "1:2,3" bzw. "1:2:editor"). Schränkt nur ein – der
// Orchestrator prüft serverseitig (X-Dev-Customers, X-Dev-Customer-Role).
const DEV_CUSTOMER = "automato_dev_customer";

function devCustomer(): { tenant: number; customers: number[]; role: string } | null {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(DEV_CUSTOMER);
  } catch {
    /* localStorage nicht verfügbar */
  }
  const m = raw?.match(/^(\d+):([\d,]*)(?::([a-z_]+))?$/);
  if (!m) return null;
  return { tenant: Number(m[1]), customers: m[2].split(",").filter(Boolean).map(Number), role: m[3] ?? "viewer" };
}

// dev-Modus: angemeldeter Benutzer ohne Rollen vorführen (`automato_dev_user` = Kennung, 0.53.0) –
// Rollen und Kunden kommen dann wie mit Keycloak aus der Zuordnung in Automato (X-Dev-User)
const DEV_USER = "automato_dev_user";

function devUser(): string | null {
  try {
    return localStorage.getItem(DEV_USER)?.trim() || null;
  } catch {
    return null;
  }
}

export function devMe(): Me {
  const user = devUser();
  if (user) return { ...DEV_ME, name: user, username: user, roles: [], tenantIds: [], isAdmin: false, isOperator: false };
  const c = devCustomer();
  if (!c) return DEV_ME;
  return {
    ...DEV_ME,
    name: L("Kunde (Vorführung)", "Customer (demo)"),
    roles: ["viewer"],
    tenantIds: [c.tenant],
    isAdmin: false,
    isOperator: false,
    customerIds: c.customers,
    isCustomer: true,
  };
}

// Kopfzeilen des dev-Modus für einen vorgeführten Kunden-Benutzer
export function devHeaders(): Record<string, string> {
  const user = devUser();
  if (user) return { "X-Dev-User": user };
  const c = devCustomer();
  if (!c) return {};
  return {
    "X-Dev-Roles": "viewer",
    "X-Dev-Tenants": String(c.tenant),
    "X-Dev-Customers": c.customers.join(","),
    "X-Dev-Customer-Role": c.role,
  };
}

// IDs aus einem Claim: Zahl, Liste bzw. Text, auch kommagetrennt ("3,4") – wie auth/oidc.py
function numbers(raw: unknown): number[] {
  return (Array.isArray(raw) ? raw : raw != null ? [raw] : [])
    .flatMap((t) => (typeof t === "string" ? t.split(",") : [t]))
    .map((t) => Number(t))
    .filter((t) => Number.isFinite(t));
}

function jwtPayload(token: string): Record<string, unknown> {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(part)
        .split("")
        .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
        .join(""),
    );
    return JSON.parse(json);
  } catch {
    return {};
  }
}

export function meFromUser(user: User): Me {
  const claims = jwtPayload(user.access_token);
  const realm = claims["realm_access"] as { roles?: string[] } | undefined;
  const raw = realm?.roles ?? [];
  // Kunden-Benutzer wie im Orchestrator (auth/oidc.py): Rolle „customer“ bzw. Attribut
  // customer_ids – nie Admin, die Rolle „customer“ liest
  const isCustomer = raw.includes("customer") || numbers(claims["customer_ids"]).length > 0;
  let roles = raw.filter((r) => ["viewer", "operator", "admin"].includes(r));
  if (isCustomer) {
    roles = roles.filter((r) => r !== "admin");
    if (raw.includes("customer") && !roles.includes("viewer")) roles.push("viewer");
  }
  const isAdmin = roles.includes("admin");
  const tenantIds = isAdmin ? null : numbers(claims["tenant_ids"]);
  const profile = user.profile;
  return {
    name: String(profile.name ?? profile.preferred_username ?? "Benutzer"),
    username: String(profile.preferred_username ?? profile.email ?? profile.sub),
    roles,
    tenantIds,
    isAdmin,
    isOperator: isAdmin || roles.includes("operator"),
    customerIds: isCustomer ? numbers(claims["customer_ids"]) : null,
    isCustomer,
    dev: false,
    // Capabilities und Kunden-Rollen kennt erst der Orchestrator (withServer)
    capabilities: [],
    customerRole: null,
    customerRoles: {},
    customers: [],
    logins: [],
  };
}

// Sicht des Orchestrators übernehmen (0.53.0): die Zuordnung Benutzer → Kunde in Automato kennt
// das Token nicht – der Orchestrator entscheidet, die Oberfläche zeigt es nur an
export function withServer(me: Me, s: ServerMe): Me {
  const roles = s.roles.filter((r) => ["viewer", "operator", "admin"].includes(r));
  return {
    ...me,
    roles,
    tenantIds: s.tenant_ids,
    isAdmin: s.is_admin,
    isOperator: s.is_admin || roles.includes("operator"),
    customerIds: s.customer_ids,
    isCustomer: s.is_customer,
    capabilities: s.capabilities ?? [],
    customerRole: s.customer_role ?? null,
    customerRoles: s.customer_roles ?? {},
    customers: s.customers.map((c) => ({ id: c.id, name: c.name })),
    logins: s.logins ?? [],
  };
}

export function roleLabel(me: Me): string {
  if (me.isCustomer) {
    // feinere Rollen: höchste Kunden-Rolle (viewer = nur „Kunde“ wie bisher)
    const role = me.customerRole ?? (me.roles.includes("operator") ? "triage" : "viewer");
    return role === "viewer" ? L("Kunde", "Customer") : `${L("Kunde", "Customer")} · ${customerRoleLabel(role)}`;
  }
  if (me.isAdmin) return L("Globaler Admin", "Global admin");
  if (me.roles.includes("operator")) return "Operator";
  if (me.roles.includes("viewer")) return "Viewer";
  return L("ohne Rolle", "no role");
}
