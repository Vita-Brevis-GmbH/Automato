// OIDC-Login fürs Web-UI (E1.3b, ADR 0007) via oidc-client-ts gegen Keycloak.
// Authorization Code + PKCE. Der UserManager ist die einzige Quelle für das Token:
// abgelaufene Tokens werden still per Refresh-Token erneuert, sonst geht es zurück
// zum Login (vorher: einmal gespeichertes Token lief nach 5 min ab -> alles 401).
// Im dev-Modus (auth_mode != "oidc") ist kein Manager aktiv und es gibt kein Token.
import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import type { AuthConfig } from "./api";

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
    user ? `Token-Erneuerung bei Keycloak fehlgeschlagen${renewError ? `: ${renewError}` : ""}` : "",
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
    problemHandler?.(reason || "Anmeldung wird vom Orchestrator nicht akzeptiert.");
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
  dev: boolean;
}

export const DEV_ME: Me = {
  name: "Entwicklung",
  username: "dev",
  roles: ["admin"],
  tenantIds: null,
  isAdmin: true,
  isOperator: true,
  dev: true,
};

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
  const roles = (realm?.roles ?? []).filter((r) => ["viewer", "operator", "admin"].includes(r));
  const isAdmin = roles.includes("admin");
  const rawTenants = claims["tenant_ids"];
  const tenantIds = isAdmin
    ? null
    : (Array.isArray(rawTenants) ? rawTenants : rawTenants != null ? [rawTenants] : [])
        .map((t) => Number(t))
        .filter((t) => Number.isFinite(t));
  const profile = user.profile;
  return {
    name: String(profile.name ?? profile.preferred_username ?? "Benutzer"),
    username: String(profile.preferred_username ?? profile.email ?? profile.sub),
    roles,
    tenantIds,
    isAdmin,
    isOperator: isAdmin || roles.includes("operator"),
    dev: false,
  };
}

export function roleLabel(me: Me): string {
  if (me.isAdmin) return "Globaler Admin";
  if (me.roles.includes("operator")) return "Operator";
  if (me.roles.includes("viewer")) return "Viewer";
  return "ohne Rolle";
}
