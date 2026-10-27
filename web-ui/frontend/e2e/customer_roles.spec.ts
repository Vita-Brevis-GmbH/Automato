// Feinere Rollen der Kunden-Benutzer (ADR 0018 Ergänzung): die Oberfläche blendet nach den
// Capabilities ein – Editor pflegt die Applikation seines Kunden, Kunden-Admin ordnet die
// Kunden-Benutzer seines Kunden zu (dev: X-Dev-User mit Zuordnung in Automato).
import { expect, test } from "./fixtures";

test("Kunden-Rollen: Editor pflegt seine Applikation, Kunden-Admin ordnet Benutzer zu", async ({ page, browser }) => {
  const retry = test.info().retry;
  const name = `Kunde Rollen ${retry}`;
  const login = `editor${retry}@example.ch`;
  const colleague = `kollege${retry}@example.ch`;
  // Kunde mit dem AP der Applikation „Webshop“ (TE/AP) – sie gehört ganz zu ihm
  const created = await page.request.post("/api/customers", {
    data: { tenant_id: 1, name, selectors: [{ kind: "aci_ap", value: "TE/AP" }] },
  });
  expect(created.status()).toBe(201);
  const customer = (await created.json()) as { id: number };
  const assigned = await page.request.post("/api/customer-users", { data: { customer_id: customer.id, login, role: "editor" } });
  expect(assigned.status()).toBe(201);
  const assignment = (await assigned.json()) as { id: number };
  const apps = (await (await page.request.get("/api/applications?tenant_id=1")).json()) as { id: number; name: string }[];
  const shop = apps.find((a) => a.name === "Webshop")!;

  // derselbe Benutzer meldet sich an (eigener Browser-Kontext, eigene Fehlerliste)
  const ctx = await browser.newContext({ baseURL: "http://localhost:5173" });
  const user = await ctx.newPage();
  const failed: string[] = [];
  user.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400) failed.push(`${r.status()} ${r.url()}`);
  });
  await user.addInitScript((u) => {
    localStorage.setItem("automato_dev_user", u);
    localStorage.setItem("automato_tenant", "1");
  }, login);
  await user.goto("/#apps");
  await expect(user.locator(".u-role")).toHaveText("Kunde · Editor");
  await expect(user.locator("nav.nav")).not.toContainText("Administration"); // keine Benutzerverwaltung
  const row = user.locator("#apps-list tbody tr", { hasText: "Webshop" });
  await expect(row.locator(".app-editable")).toBeVisible();
  // Anlegen, Import und Löschen bleiben den Betreuern
  await expect(user.locator("#app-import")).toHaveCount(0);
  await expect(user.locator("#apps-list input[type=checkbox]")).toHaveCount(0);
  await row.click();
  await user.click("#app-doc-edit");
  const modal = user.locator(".modal");
  await modal.locator("#ap-owner").fill("Team Rollen");
  await modal.locator("button.btn-primary").click();
  await expect(user.locator(".app-head")).toContainText("Team Rollen");

  // Kunden-Admin: eigene Seite mit den Kunden-Benutzern seines Kunden
  const promoted = await page.request.patch(`/api/customer-users/${assignment.id}`, { data: { role: "customer_admin" } });
  expect(promoted.status()).toBe(200);
  await user.goto("/#kundenbenutzer");
  await user.reload(); // Rolle neu vom Orchestrator (gilt ab der nächsten Anfrage)
  await expect(user.locator(".u-role")).toHaveText("Kunde · Kunden-Admin");
  await expect(user.locator("main.main h1")).toHaveText("Kunden-Benutzer");
  const card = user.locator("#customer-users");
  await expect(card.locator("#customer-role-legend")).toContainText("Editor");
  const own = card.locator("#customer-user-list tbody tr", { hasText: login });
  await expect(own).toContainText("Sie");
  await expect(own.locator("select")).toBeDisabled(); // eigene Rolle nicht änderbar
  await expect(own.locator("button:has-text('Entfernen')")).toHaveCount(0);
  await card.locator("input[aria-label='Benutzer']").fill(colleague);
  await card.locator("select[aria-label='Rolle']").selectOption("triage");
  await card.locator("button:has-text('Zuordnen')").click();
  const added = card.locator("#customer-user-list tbody tr", { hasText: colleague });
  await expect(added.locator("select")).toHaveValue("triage");
  user.once("dialog", (d) => d.accept());
  await added.locator("button:has-text('Entfernen')").click();
  await expect(added).toHaveCount(0);
  expect(failed).toEqual([]);
  await ctx.close();

  // Betreuer: Kunden-Benutzer-Seite gibt es für sie nicht (sie nutzen Kunden → Kunden-Benutzer)
  await page.goto("/#kundenbenutzer");
  await expect(page.locator("main.main h1")).toHaveText("Übersicht");

  // aufräumen: Verantwortlich zurück, Kunde samt Zuordnungen löschen
  expect((await page.request.patch(`/api/applications/${shop.id}`, { data: { owner: null } })).status()).toBe(200);
  expect((await page.request.delete(`/api/customers/${customer.id}`)).status()).toBe(204);
});
