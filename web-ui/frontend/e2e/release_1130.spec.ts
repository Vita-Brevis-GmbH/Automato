// 1.13.0 (Fund 2026-11-16: neuer Kunde aus dem Cockpit, Automato zeigt „insufficient role“; „Ja gerne“ – Mandant,
// Kunden und Rolle im Cockpit wählen, ADR 0054 Nachtrag): Kunde aus dem Cockpit ohne Zuordnung markiert, mit
// Zuordnung aus dem Cockpit Mandant, Kunden und Rolle nur lesbar.
import { expect, test } from "./fixtures";

const COCKPIT = { Authorization: "Bearer e2e-cockpit-token" }; // e2e/server.py

test("Kunden-Portal: Zuordnung aus dem Cockpit (1.13.0)", async ({ page }) => {
  const retry = test.info().retry;
  const created = await page.request.post("/api/customers", { data: { tenant_id: 1, name: `Zuordnung E2E ${retry}`, selectors: [] } });
  expect(created.status()).toBe(201);
  const customer = (await created.json()) as { id: number };
  // Auswahl fürs Cockpit im Inventar
  const inventar = await (await page.request.get("/api/inventory", { headers: COCKPIT })).json();
  expect(inventar.automato.rollen).toEqual(["viewer", "triage", "editor", "customer_admin"]);
  const mandant = (inventar.automato.mandanten as { id: number; kunden: { id: number }[] }[]).find((m) => m.id === 1);
  expect(mandant?.kunden.map((k) => k.id)).toContain(customer.id);

  // ohne Zuordnung: markiert – Benutzer des Realms hätten keine Rechte
  const kunde = { kurzname: "zuordnung-e2e", name: "Zuordnung E2E", werkzeuge: ["doc"], zugriff: "ueberall", netze: [], realm: "zuordnung-e2e" };
  let put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [kunde] } });
  expect(put.status()).toBe(200);
  await page.goto("/#kundenportal");
  const row = page.locator("#portal-customers tbody tr", { hasText: "zuordnung-e2e" }).first();
  await expect(row).toContainText("noch kein Mandant – Benutzer haben keine Rechte");

  // mit Zuordnung aus dem Cockpit: nicht mehr markiert, Mandant, Kunden und Rolle nur lesbar
  const zuordnung = { mandant: 1, kunden: [customer.id], rolle: "triage" };
  put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [{ ...kunde, zuordnung }] } });
  expect(put.status()).toBe(200);
  await page.reload();
  await expect(row).toContainText("triage");
  await expect(row).not.toContainText("noch kein Mandant");
  await row.locator("button:has-text('Bearbeiten')").click();
  await expect(page.locator(".modal")).toContainText("Mandant, Kunden und Rolle aus dem Cockpit");
  await expect(page.locator("#pc-tenant")).toBeDisabled();
  await expect(page.locator("#pc-role")).toBeDisabled();
  await page.click(".modal-foot button:has-text('Abbrechen')");

  // aufräumen: im Cockpit entfernt, Kunde löschen
  const leer = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [] } });
  expect((await leer.json()).entfernt).toEqual(["zuordnung-e2e"]);
  expect((await page.request.delete(`/api/customers/${customer.id}`)).status()).toBe(204);
});
