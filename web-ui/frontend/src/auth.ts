// OIDC-Login fürs Web-UI (E1.3b, ADR 0007) via oidc-client-ts gegen Keycloak.
// Authorization Code + PKCE. Im dev-Modus (auth_mode != "oidc") wird nichts davon
// verwendet. End-to-End nur gegen ein laufendes Keycloak verifizierbar.
import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { AuthConfig, clearAuthToken, setAuthToken } from "./api";

const redirect = window.location.origin + window.location.pathname;

export function createManager(cfg: AuthConfig): UserManager {
  return new UserManager({
    authority: cfg.issuer,
    client_id: cfg.client_id,
    redirect_uri: redirect,
    post_logout_redirect_uri: redirect,
    response_type: "code",
    scope: cfg.scope || "openid profile",
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    automaticSilentRenew: true,
  });
}

// Verarbeitet den Rückkommer von Keycloak (?code=…). Gibt true zurück, wenn ein
// Callback behandelt wurde (Token gespeichert, URL bereinigt).
export async function handleRedirectCallback(mgr: UserManager): Promise<boolean> {
  if (!window.location.search.includes("code=")) return false;
  const user = await mgr.signinRedirectCallback();
  if (user?.access_token) setAuthToken(user.access_token);
  window.history.replaceState({}, document.title, window.location.pathname);
  return true;
}

export function login(mgr: UserManager): Promise<void> {
  return mgr.signinRedirect();
}

export async function logout(mgr: UserManager): Promise<void> {
  clearAuthToken();
  await mgr.signoutRedirect();
}
