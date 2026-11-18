// Abmelden mit einem Klick (1.19.3, Befund des Anwenders 2026-11-18: „Auch aus dem Automato heraus
// abmelden muss ich mehrfach auf Abmelden klicken, damit ich zum Keycloak zurückkehre“).
// - hinter dem Anmelde-Tor (ADR 0051): über dessen Abmelden (/oauth2/abmelden des Front-Proxys) –
//   das beendet die Sitzung in Keycloak und löscht die Cookies des Tors auf allen Adressen des
//   Kunden; die Oberfläche löscht ihr Token und geht nicht mehr über Keycloak (end_session);
// - ohne Tor: über Keycloak mit id_token_hint (keine Rückfrage) und zurück zum Formular von
//   Keycloak – auch kurz nach der Anmeldung (bis 1.19.2 hielt der Schleifenschutz das für eine
//   Schleife: „Anmeldung nicht möglich“ mit noch einem „Abmelden“);
// - eine Anfrage während des Abmeldens meldet nicht still neu an (bis 1.19.2 gewann die Anmeldung).
// Keycloak und das Tor sind gespielt (page.route), der Orchestrator läuft wie immer im dev-Modus.
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const ISSUER = "https://idp.example.ch/realms/test";
const CLIENT = "automato-web";
const CORS = { "Access-Control-Allow-Origin": "*" };
const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");

interface Kc {
  authorize: string[];
  logout: string[];
}

// Keycloak gespielt; `discoveryMs`: so lange braucht die Konfiguration von Keycloak (Wettlauf)
async function keycloak(page: Page, discoveryMs = 0): Promise<Kc> {
  const kc: Kc = { authorize: [], logout: [] };
  await page.route(/\/api\/auth\/config(\?.*)?$/, (route) =>
    route.fulfill({ json: { auth_mode: "oidc", issuer: ISSUER, client_id: CLIENT, scope: "openid", admin_console_url: "" } }),
  );
  await page.route(`${ISSUER}/.well-known/openid-configuration`, async (route) => {
    if (discoveryMs) await new Promise((resolve) => setTimeout(resolve, discoveryMs));
    await route.fulfill({
      headers: CORS,
      json: {
        issuer: ISSUER,
        authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`,
        token_endpoint: `${ISSUER}/protocol/openid-connect/token`,
        end_session_endpoint: `${ISSUER}/protocol/openid-connect/logout`,
        jwks_uri: `${ISSUER}/protocol/openid-connect/certs`,
      },
    });
  });
  // Formular von Keycloak (keine Sitzung mehr)
  await page.route(`${ISSUER}/protocol/openid-connect/auth**`, (route) => {
    kc.authorize.push(route.request().url());
    return route.fulfill({ contentType: "text/html", body: "<h1>Keycloak</h1>" });
  });
  // Abmelden bei Keycloak: mit id_token_hint ohne Rückfrage zurück auf die Oberfläche (?state=…)
  await page.route(`${ISSUER}/protocol/openid-connect/logout**`, (route) => {
    const url = new URL(route.request().url());
    kc.logout.push(url.toString());
    const back = new URL(url.searchParams.get("post_logout_redirect_uri") ?? "/", "http://localhost");
    back.searchParams.set("state", url.searchParams.get("state") ?? "");
    return route.fulfill({
      contentType: "text/html",
      body: `<script>location.replace(${JSON.stringify(back.toString())})</script>`,
    });
  });
  return kc;
}

// Angemeldet: Benutzer in localStorage (wie oidc-client-ts), angemeldet vor `agoMs` (Zeitpunkt der
// Anmeldung im Tab); nur beim ersten Laden des Tabs, nicht nach der Rückkehr von Keycloak
async function signedIn(page: Page, agoMs = 0): Promise<void> {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const claims = { sub: "u1", preferred_username: "anna", name: "Anna", realm_access: { roles: ["admin"] }, exp };
  const user = {
    id_token: `${b64({ alg: "none" })}.${b64({ ...claims, aud: CLIENT, iss: ISSUER })}.x`,
    access_token: `${b64({ alg: "none" })}.${b64(claims)}.x`,
    refresh_token: "r1",
    token_type: "Bearer",
    scope: "openid",
    profile: { sub: "u1", preferred_username: "anna", name: "Anna" },
    expires_at: exp,
  };
  await page.addInitScript(
    ([key, value, ago]) => {
      if (sessionStorage.getItem("e2e_seeded")) return;
      sessionStorage.setItem("e2e_seeded", "1");
      localStorage.setItem(key, value);
      sessionStorage.setItem("automato_login_at", String(Date.now() - Number(ago)));
    },
    [`oidc.user:${ISSUER}:${CLIENT}`, JSON.stringify(user), String(agoMs)],
  );
}

async function clickSignOut(page: Page): Promise<void> {
  await page.locator(".usermenu-btn").click();
  await page.getByRole("button", { name: "Abmelden" }).click();
}

const storedUser = (page: Page) => page.evaluate((key) => localStorage.getItem(key), `oidc.user:${ISSUER}:${CLIENT}`);

test("hinter dem Anmelde-Tor: ein Klick meldet über das Tor ab (1.19.3)", async ({ page }) => {
  const kc = await keycloak(page);
  await signedIn(page);
  const gate: string[] = [];
  // Front-Proxy gespielt: /oauth2/abmelden leitet weiter (Kette über alle Adressen des Kunden),
  // am Ende steht die Seite „Abgemeldet“
  await page.route("**/oauth2/abmelden", (route) => {
    const nav = route.request().isNavigationRequest();
    gate.push(nav ? "navigation" : "probe");
    return nav
      ? route.fulfill({ contentType: "text/html", body: "<h1>Abgemeldet</h1>" })
      : route.fulfill({ status: 302, headers: { Location: "/oauth2/sign_out?rd=%2Fabgemeldet" } });
  });
  await page.goto("/#apps");
  await expect(page.locator("header.topbar")).toBeVisible();
  await clickSignOut(page);
  await expect(page.locator("h1")).toHaveText("Abgemeldet");
  expect(gate).toEqual(["probe", "navigation"]);
  expect(await storedUser(page)).toBeNull(); // Token der Oberfläche gelöscht
  expect(kc.logout).toHaveLength(0); // kein Umweg über Keycloak (das Tor beendet die Sitzung)
  expect(kc.authorize).toHaveLength(0); // nicht still wieder angemeldet
});

test("ohne Tor: ein Klick über Keycloak bis zum Formular, auch gleich nach der Anmeldung (1.19.3)", async ({
  page,
}) => {
  const kc = await keycloak(page);
  await signedIn(page);
  await page.goto("/#apps");
  await expect(page.locator("header.topbar")).toBeVisible();
  await clickSignOut(page);
  // zurück von Keycloak meldet die Oberfläche neu an: Formular, kein „Anmeldung nicht möglich“
  await expect(page.locator("h1")).toHaveText("Keycloak");
  expect(kc.logout).toHaveLength(1);
  const logout = new URL(kc.logout[0]);
  expect(logout.searchParams.get("id_token_hint")).toBeTruthy(); // ohne Rückfrage von Keycloak
  expect(logout.searchParams.get("post_logout_redirect_uri")).toMatch(/^http:\/\/localhost:\d+\/$/);
  expect(kc.authorize).toHaveLength(1);
});

test("Anfrage während des Abmeldens meldet nicht still neu an (1.19.3)", async ({ page }) => {
  // Keycloak antwortet langsam: in der Zeit lädt die Oberfläche eine Ansicht (Anfragen an den
  // Orchestrator) - bis 1.19.2 löste das eine Anmeldung aus, die das Abmelden überholte
  const kc = await keycloak(page, 1500);
  await signedIn(page, 5 * 60_000); // vor 5 min angemeldet: der Schleifenschutz spielt nicht mit
  await page.goto("/#apps");
  await expect(page.locator("header.topbar")).toBeVisible();
  await clickSignOut(page);
  await page.evaluate(() => {
    window.location.hash = "#findings";
  });
  await expect(page.locator("h1")).toHaveText("Keycloak", { timeout: 10_000 });
  expect(kc.logout).toHaveLength(1);
  expect(kc.authorize).toHaveLength(1); // erst nach der Rückkehr von Keycloak, nicht davor
  expect(new URL(kc.authorize[0]).searchParams.get("redirect_uri")).toMatch(/^http:\/\/localhost:\d+\/$/);
});
