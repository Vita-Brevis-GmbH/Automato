// 1.14.0 (Anwender 2026-11-16: „die Rollen und User … sollen nur noch im Cockpit sein. Die Kunden sollen dies
// nicht machen können“ und „Weiter sollen die Kunden und Portale im Automato durch das Cockpit erstellt und verwaltet
// werden, nicht ein Mix wie heute.“; ADR 0058): mit verbundenem Cockpit ist das Kunden-Portal nur lesbar, Benutzer
// und Rollen kommen aus dem Cockpit, Kunden aus der Oberfläche übernimmt das Cockpit; der Kunden-Admin eines Kunden
// aus dem Cockpit ordnet keine Kunden-Benutzer mehr zu.
import { cockpit, COCKPIT, expect, testCockpit as test } from "./cockpit";

test("Kunden-Portal nur aus dem Cockpit: Benutzer, angelegter Kunde, übernehmen (1.14.0)", async ({ page }) => {
  const retry = test.info().retry;
  // ohne Cockpit: ein Kunde aus der Oberfläche
  await cockpit(page.request, false);
  const ui = await page.request.post("/api/portal/customers", { data: { short_name: `ui-1140-${retry}`, name: "UI 1140" } });
  expect(ui.status()).toBe(201);
  await cockpit(page.request, true);

  const kunde = {
    kurzname: "cockpit-1140",
    name: `Cockpit 1140 E2E ${retry}`,
    werkzeuge: ["doc"],
    zugriff: "ueberall",
    netze: [],
    realm: "cockpit-1140",
    zuordnung: { mandant: 1, kunden: [], rolle: "viewer", kunde_anlegen: true },
    benutzer: [
      { benutzer: "max.muster", rolle: "editor" },
      { benutzer: "anna", rolle: "viewer" },
    ],
  };
  const put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [kunde] } });
  expect(put.status()).toBe(200);

  await page.goto("/#kundenportal");
  await expect(page.locator("#portal-nur-cockpit")).toContainText("nur noch im Cockpit");
  await expect(page.locator("button:has-text('+ Kunde')")).toHaveCount(0);
  const alt = page.locator("#portal-customers tbody tr", { hasText: `ui-1140-${retry}` }).first();
  await expect(alt).toContainText("im Cockpit übernehmen");
  await expect(alt.locator("button:has-text('Entfernen')")).toHaveCount(0);

  const row = page.locator("#portal-customers tbody tr", { hasText: "cockpit-1140" }).first();
  await expect(row).toContainText("2 Benutzer");
  await row.locator("button:has-text('Ansehen')").click();
  const users = page.locator("#portal-users-list");
  await expect(users).toContainText("max.muster");
  await expect(users.locator("tr", { hasText: "max.muster" })).toContainText("editor");
  await expect(page.locator(".modal")).toContainText("für das Cockpit angelegt");
  await expect(page.locator("#pc-name")).toBeDisabled();
  await page.click(".modal-foot button:has-text('Schliessen')");

  // aufräumen: im Cockpit entfernt, angelegten Kunden und den Kunden aus der Oberfläche löschen
  const listing = await (await page.request.get("/api/portal/customers")).json();
  const angelegt = listing.customers.find((c: { short_name: string }) => c.short_name === "cockpit-1140").kunde_angelegt;
  const leer = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [] } });
  expect((await leer.json()).entfernt).toEqual(["cockpit-1140"]);
  expect((await page.request.delete(`/api/customers/${angelegt}`)).status()).toBe(204);
  await cockpit(page.request, false);
  expect((await page.request.delete(`/api/portal/customers/${(await ui.json()).id}`)).status()).toBe(200);
});

test("Kunden-Admin: bei Kunden aus dem Cockpit keine Kunden-Benutzer (1.14.0)", async ({ page, browser, baseURL }) => {
  const retry = test.info().retry;
  const login = `chef1140-${retry}@example.ch`;
  const created = await page.request.post("/api/customers", { data: { tenant_id: 1, name: `Admin 1140 ${retry}`, selectors: [] } });
  expect(created.status()).toBe(201);
  const customer = (await created.json()) as { id: number };
  const assigned = await page.request.post("/api/customer-users", { data: { customer_id: customer.id, login, role: "customer_admin" } });
  expect(assigned.status()).toBe(201);

  const ctx = await browser.newContext({ baseURL });
  const user = await ctx.newPage();
  await user.addInitScript((u) => {
    localStorage.setItem("automato_dev_user", u);
    localStorage.setItem("automato_tenant", "1");
  }, login);
  await user.goto("/#kundenbenutzer");
  await expect(user.locator(".u-role")).toHaveText("Kunde · Kunden-Admin");
  await expect(user.locator("main.main h1")).toHaveText("Kunden-Benutzer");

  // der Kunde kommt jetzt aus dem Cockpit: Benutzer und Rollen vergibt nur noch der Betreuer dort
  const kunde = {
    kurzname: "admin-1140",
    name: "Admin 1140",
    werkzeuge: ["doc"],
    zugriff: "ueberall",
    netze: [],
    realm: "admin-1140",
    zuordnung: { mandant: 1, kunden: [customer.id], rolle: "viewer" },
  };
  const put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [kunde] } });
  expect(put.status()).toBe(200);
  await user.reload(); // Rechte neu vom Orchestrator: die Seite „Kunden-Benutzer“ gibt es nicht mehr
  await expect(user.locator(".u-role")).toHaveText("Kunde · Kunden-Admin");
  await expect(user.locator("main.main h1")).toHaveText("Übersicht");
  const r = await page.request.post("/api/customer-users", {
    headers: { "X-Dev-User": login },
    data: { customer_id: customer.id, login: "neu@example.ch" },
  });
  expect(r.status()).toBe(409);
  expect((await r.json()).detail).toContain("Betreiber im Cockpit");
  await ctx.close();

  // aufräumen
  const leer = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [] } });
  expect((await leer.json()).entfernt).toEqual(["admin-1140"]);
  expect((await page.request.delete(`/api/customers/${customer.id}`)).status()).toBe(204);
});
