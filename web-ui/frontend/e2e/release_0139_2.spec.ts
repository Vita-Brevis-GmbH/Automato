// 0.139.2 (Befund des Anwenders 2026-11-06: „Doku laden: Failed to fetch“, in der Adresszeile
// „?error=temporarily_unavailable&error_description=authentication_expired“): Keycloak schickt statt
// ?code=… einen Fehler zurück. Die Oberfläche bereinigt die Adresszeile, stellt die Ansicht wieder
// her, übernimmt die Sitzung eines anderen Tabs bzw. meldet sich einmal neu an, danach steht der
// Grund da. Keycloak ist gespielt (page.route), der Orchestrator läuft wie immer im dev-Modus.
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const ISSUER = "https://idp.example.ch/realms/test";
const CLIENT = "automato-ui";
const CORS = { "Access-Control-Allow-Origin": "*" };
const BACK = "/?error=temporarily_unavailable&error_description=authentication_expired&state=abc123#apps";

async function keycloak(page: Page): Promise<string[]> {
  const authorize: string[] = [];
  await page.route(/\/api\/auth\/config$/, (route) =>
    route.fulfill({ json: { auth_mode: "oidc", issuer: ISSUER, client_id: CLIENT, scope: "openid", admin_console_url: "" } }),
  );
  await page.route(`${ISSUER}/.well-known/openid-configuration`, (route) =>
    route.fulfill({
      headers: CORS,
      json: {
        issuer: ISSUER,
        authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`,
        token_endpoint: `${ISSUER}/protocol/openid-connect/token`,
        end_session_endpoint: `${ISSUER}/protocol/openid-connect/logout`,
        jwks_uri: `${ISSUER}/protocol/openid-connect/certs`,
      },
    }),
  );
  await page.route(`${ISSUER}/protocol/openid-connect/auth**`, (route) => {
    authorize.push(route.request().url());
    return route.fulfill({ contentType: "text/html", body: "<h1>Keycloak</h1>" });
  });
  return authorize;
}

const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");

test("Keycloak-Fehler: Adresszeile bereinigt, einmal neu angemeldet (0.139.2)", async ({ page }) => {
  const authorize = await keycloak(page);
  await page.goto(BACK);
  await expect(page.locator("h1")).toHaveText("Keycloak");
  expect(authorize).toHaveLength(1);
  expect(authorize[0]).toContain(`client_id=${CLIENT}`);
});

test("Keycloak-Fehler zum zweiten Mal: Grund statt Schleife (0.139.2)", async ({ page }) => {
  const authorize = await keycloak(page);
  await page.addInitScript(() => sessionStorage.setItem("automato_login_retry", "1"));
  await page.goto(BACK);
  await expect(page.locator(".splash")).toContainText("Keycloak hat die Anmeldung nicht abgeschlossen: authentication_expired");
  expect(page.url()).not.toContain("error=");
  expect(new URL(page.url()).hash).toBe("#apps");
  expect(authorize).toHaveLength(0);
});

test("Keycloak-Fehler, anderer Tab schon angemeldet: dessen Sitzung gilt (0.139.2)", async ({ page }) => {
  const authorize = await keycloak(page);
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const claims = { sub: "u1", preferred_username: "anna", name: "Anna", realm_access: { roles: ["admin"] }, exp };
  const user = {
    access_token: `${b64({ alg: "none" })}.${b64(claims)}.x`,
    token_type: "Bearer",
    scope: "openid",
    profile: { sub: "u1", preferred_username: "anna", name: "Anna" },
    expires_at: exp,
  };
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [`oidc.user:${ISSUER}:${CLIENT}`, JSON.stringify(user)],
  );
  await page.goto(BACK);
  await expect(page.locator("header.topbar")).toBeVisible();
  await expect(page).toHaveURL(/\/#apps$/);
  expect(authorize).toHaveLength(0);
});
