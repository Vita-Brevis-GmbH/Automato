// Rechte je Rolle im eigenen Mandanten (ADR 0068): Navigation je Rolle – Operator und Admin im eigenen Mandanten sehen
// alle Schichten L1–L7, Läufe und Quellsysteme, der Admin dazu NetBox-Sync und Bearbeiten; Plattform-Ansichten nie.
// Viewer und der gemeinsame Mandant bleiben bei der Kunden-Sicht. Ohne Antwort von /auth/me keine Sicht aus dem
// Token (fail-open behoben); Betreiber sehen den Link „Ganze Plattform“. dev: `automato_dev_customer` =
// "<Mandant>:<Kunden>:<Rolle>[:eigen]" (X-Dev-Own-Tenant wie ein Kunden-Realm im eigenen Mandanten).
import { test as base } from "@playwright/test";
import { expect, test } from "./fixtures";

type Page = import("@playwright/test").Page;

const PLATFORM = ["Benutzer & Rollen", "Kunden-Portal", "System", "Collector-Agenten", "Namensprofile"];

async function as(page: Page, value: string, hash = "#uebersicht") {
  await page.addInitScript((v) => {
    localStorage.setItem("automato_dev_customer", v);
    localStorage.setItem("automato_tenant", "1");
  }, value);
  await page.goto(`/${hash}`);
}

async function customerId(page: Page, name: string): Promise<number> {
  const r = await page.request.post("/api/customers", {
    data: { tenant_id: 1, name, selectors: [{ kind: "aci_ap", value: "TE/AP" }] },
  });
  expect(r.status()).toBe(201);
  return ((await r.json()) as { id: number }).id;
}

test("Operator im eigenen Mandanten: sieht alles, stösst nur das Einlesen an", async ({ page }) => {
  await as(page, "1::customer_operator:eigen");
  await expect(page.locator(".u-role")).toHaveText("Kunde · Operator");
  const nav = page.locator("nav.nav");
  // alle Schichten, auch die bisher nur für Betreuer (L1–L3, Netze, VRF)
  for (const place of ["l1/verkabelung", "l2/vlans", "l3/netze", "l3/vrf", "l3/routing", "l4-7/regeln"]) {
    await expect(nav.locator(`[data-place="${place}"]`)).toHaveCount(1);
  }
  await expect(nav).toContainText("Läufe");
  await expect(nav).toContainText("Mandanten & Standorte");
  for (const label of [...PLATFORM, "NetBox-Sync", "Kunden"]) {
    await expect(nav.locator(".nav-item", { hasText: new RegExp(`^${label}$`) })).toHaveCount(0);
  }
  // Einlesen anstossen: ja; Bearbeiten: nein
  await expect(page.locator("main.main button", { hasText: "Jetzt einlesen" }).first()).toBeVisible();
  await page.goto("/#tenants");
  await expect(page.locator("main.main h2", { hasText: "Quellsysteme" })).toBeVisible();
  await expect(page.locator("button", { hasText: "+ Quellsystem" })).toHaveCount(0);
  await expect(page.locator("button", { hasText: "+ Standort" })).toHaveCount(0);
  await expect(page.locator("button", { hasText: "+ Mandant" })).toHaveCount(0);
  // Plattform-Ansicht über die Adresse: zurück zur Übersicht
  await page.goto("/#system");
  await expect(page.locator("main.main h1")).toHaveText("Übersicht");
  await expect(page.locator("#platform-link")).toHaveCount(0);
});

test("Admin im eigenen Mandanten: alles im Mandanten anpassen, nie die Plattform", async ({ page }) => {
  await as(page, "1::customer_admin:eigen", "#tenants");
  await expect(page.locator(".u-role")).toHaveText("Kunde · Admin");
  const nav = page.locator("nav.nav");
  await expect(nav).toContainText("NetBox-Sync");
  await expect(nav.locator('[data-place="l1/verkabelung"]')).toHaveCount(1);
  for (const label of [...PLATFORM, "Kunden"]) {
    await expect(nav.locator(".nav-item", { hasText: new RegExp(`^${label}$`) })).toHaveCount(0);
  }
  await expect(page.locator("button", { hasText: "+ Quellsystem" })).toBeVisible();
  await expect(page.locator("button", { hasText: "+ Standort" })).toBeVisible();
  await expect(page.locator("button", { hasText: "+ Mandant" })).toHaveCount(0); // Mandanten anlegen: Plattform
  // Benutzermenü ohne „Benutzer & Rollen“
  await page.locator(".usermenu-btn").click();
  await expect(page.locator(".menu")).not.toContainText("Benutzer & Rollen");
});

test("Viewer im eigenen Mandanten und Admin im gemeinsamen: Kunden-Sicht wie bisher", async ({ page, browser, baseURL }) => {
  const retry = test.info().retry;
  const id = await customerId(page, `Kunde Rechte ${retry}`);
  for (const [value, label] of [
    [`1:${id}:viewer:eigen`, "Kunde"],
    [`1:${id}:customer_admin`, "Kunde · Admin"],
    [`1:${id}:customer_operator`, "Kunde · Operator"],
  ]) {
    const ctx = await browser.newContext({ baseURL });
    const user = await ctx.newPage();
    await as(user, value);
    await expect(user.locator(".u-role")).toHaveText(label);
    const nav = user.locator("nav.nav");
    await expect(nav.locator('[data-place="l4-7/regeln"]')).toHaveCount(1);
    await expect(nav.locator('[data-place="l1/verkabelung"]')).toHaveCount(0);
    await expect(nav.locator('[data-place="l3/netze"]')).toHaveCount(0);
    await expect(nav).not.toContainText("Läufe");
    await expect(nav).not.toContainText("NetBox-Sync");
    await ctx.close();
  }
  expect((await page.request.delete(`/api/customers/${id}`)).status()).toBe(204);
});

test("Betreiber: Link „Ganze Plattform“ nur, wenn der Server ihn nennt", async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    const r = await route.fetch();
    const body = { ...(await r.json()), betreiber: true, plattform_url: "https://automato.int.example.ch" };
    await route.fulfill({ response: r, json: body });
  });
  await as(page, "1::customer_admin:eigen");
  await expect(page.locator(".u-role")).toHaveText("Betreiber");
  const link = page.locator("#platform-link");
  await expect(link).toHaveAttribute("href", "https://automato.int.example.ch");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toContainText("Ganze Plattform");
});

// eigener Test ohne die Fixture (sie lässt jede fehlgeschlagene API-Anfrage scheitern – hier ist sie gewollt)
base("ohne /auth/me keine Sicht aus dem Token (fail-open behoben)", async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({ status: 503, json: { detail: "nicht erreichbar" } }));
  await page.goto("/#uebersicht");
  await base.expect(page.locator("#rechte-fehlen")).toContainText("Rechte nicht lesbar");
  await base.expect(page.locator("nav.nav")).toHaveCount(0);
  await base.expect(page.locator("button", { hasText: "Neu laden" })).toBeVisible();
});
