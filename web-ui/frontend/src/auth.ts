import type { ServerMe } from "./api";
// OIDC-Login fürs Web-UI (E1.3b, ADR 0007) via oidc-client-ts gegen Keycloak.
// Authorization Code + PKCE. Der UserManager ist die einzige Quelle für das Token:
// abgelaufene Tokens werden still per Refresh-Token erneuert, sonst geht es zurück
// zum Login (vorher: einmal gespeichertes Token lief nach 5 min ab -> alles 401).
// Im dev-Modus (auth_mode != "oidc") ist kein Manager aktiv und es gibt kein Token.
import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import type { AuthConfig } from "./api";
import { L } from "./i18n";

const redirect = window.location.origin + window.location.pathname;
let manager: UserManager | null = null;
let renewing: Promise<User | null> | null = null;
let renewError = "";

// Schleifenschutz: wurde gerade erst angemeldet und das Token trotzdem abgelehnt, hilft
// ein weiterer Login nicht (Keycloak-SSO schickt sofort zurück) -> Grund anzeigen.
const LOGIN_AT = "automato_login_at";
const LOOP_WINDOW_MS = 60_000;
let problemHandler: ((reason: string) => void) | null = null;

export function onProblem(handler: (reason: string) => void): void {
  problemHandler = handler;
}

function session(key: string, value?: string | null): string | null {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* sessionStorage nicht verfügbar */
  }
  return null;
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
    redirect_uri: redirect,
    post_logout_redirect_uri: redirect,
    response_type: "code",
    scope: cfg.scope || "openid profile",
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    automaticSilentRenew: true,
  });
  return manager;
}

export const isOidc = () => manager !== null;

// Verarbeitet den Rückkommer von Keycloak (?code=…) und stellt die vorherige Ansicht
// (#hash) wieder her. true = Callback behandelt.
export async function handleRedirectCallback(): Promise<boolean> {
  if (!manager || !window.location.search.includes("code=")) return false;
  const user = await manager.signinRedirectCallback();
  const hash = (user.state as { hash?: string } | undefined)?.hash ?? "";
  window.history.replaceState({}, document.title, window.location.pathname + hash);
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

// Gültige Sitzung liefern; sonst still erneuern; sonst Login starten (Redirect).
export async function ensureSession(): Promise<User | null> {
  if (!manager) return null;
  const user = await manager.getUser();
  if (user && !user.expired) return user;
  if (user?.refresh_token) {
    const renewed = await renew();
    if (renewed && !renewed.expired) return renewed;
  }
  await relogin(
    user
      ? L("Token-Erneuerung bei Keycloak fehlgeschlagen", "Token renewal at Keycloak failed") +
          (renewError ? `: ${renewError}` : "")
      : "",
  );
  return null;
}

export async function accessToken(): Promise<string | null> {
  const user = await ensureSession();
  return user?.access_token ?? null;
}

// Neu anmelden (Redirect zu Keycloak) - ausser es wurde gerade erst angemeldet: dann
// wird der Grund gemeldet statt endlos zwischen UI und Keycloak hin und her zu leiten.
export async function relogin(reason: string): Promise<void> {
  const last = Number(session(LOGIN_AT) ?? 0);
  if (last && Date.now() - last < LOOP_WINDOW_MS) {
    problemHandler?.(
      reason || L("Anmeldung wird vom Orchestrator nicht akzeptiert.", "Sign-in is not accepted by the orchestrator."),
    );
    return;
  }
  await login();
}

// Ausdrücklicher Login (Button oder erster Aufruf ohne Sitzung).
export async function login(): Promise<void> {
  if (!manager) return;
  session(LOGIN_AT, String(Date.now()));
  await manager.removeUser();
  await manager.signinRedirect({ state: { hash: window.location.hash } });
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
};

// dev-Modus: Kunden-Benutzer vorführen bzw. testen (`automato_dev_customer` = "<Mandant>:<Kunden>",
// z. B. "1:2,3"). Schränkt nur ein – der Orchestrator prüft serverseitig (X-Dev-Customers).
const DEV_CUSTOMER = "automato_dev_customer";

function devCustomer(): { tenant: number; customers: number[] } | null {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(DEV_CUSTOMER);
  } catch {
    /* localStorage nicht verfügbar */
  }
  const m = raw?.match(/^(\d+):([\d,]*)$/);
  if (!m) return null;
  return { tenant: Number(m[1]), customers: m[2].split(",").filter(Boolean).map(Number) };
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
  return { "X-Dev-Roles": "viewer", "X-Dev-Tenants": String(c.tenant), "X-Dev-Customers": c.customers.join(",") };
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
  };
}

export function roleLabel(me: Me): string {
  if (me.isCustomer) return me.roles.includes("operator") ? L("Kunde · Operator", "Customer · operator") : L("Kunde", "Customer");
  if (me.isAdmin) return L("Globaler Admin", "Global admin");
  if (me.roles.includes("operator")) return "Operator";
  if (me.roles.includes("viewer")) return "Viewer";
  return L("ohne Rolle", "no role");
}
