// 1.11.0 (Anwender 2026-11-13: „starte mal P2“ – ein Ort für Kunden: im Cockpit Kunden anlegen und Werkzeuge
// wählen, Automato übernimmt; ADR 0054): Kunden aus dem Cockpit im Kunden-Portal nur teilweise änderbar und nicht
// entfernbar, NetBox (-nb) als wählbares Werkzeug.
import { expect, test } from "./fixtures";

const COCKPIT = { Authorization: "Bearer e2e-cockpit-token" }; // e2e/server.py

test("Kunden-Portal: NetBox wählbar, Kunden aus dem Cockpit (1.11.0)", async ({ page }) => {
  // Freigabe wie aus dem Cockpit – ganzer Stand: nur dieser Kunde
  const kunde = { kurzname: "cockpit-e2e", name: "Cockpit E2E", werkzeuge: ["doc"], zugriff: "intern", netze: [], realm: null };
  const put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [kunde] } });
  expect(put.status()).toBe(200);
  expect((await put.json()).angelegt).toEqual(["cockpit-e2e"]);

  await page.goto("/#kundenportal");
  const row = page.locator("#portal-customers tbody tr", { hasText: "cockpit-e2e" }).first();
  await expect(row).toContainText("aus dem Cockpit");
  await expect(row).toContainText("Doku");
  await expect(row).not.toContainText("NetBox");
  await expect(row).toContainText("nur intern");
  await expect(row.locator("button:has-text('Entfernen')")).toHaveCount(0);

  // nur Mandant, Kunden, Rolle und Kacheln änderbar
  await row.locator("button:has-text('Bearbeiten')").click();
  await expect(page.locator(".modal")).toContainText("pflegt das Cockpit");
  await expect(page.locator("#pc-short")).toBeDisabled();
  await expect(page.locator("#pc-name")).toBeDisabled();
  await expect(page.locator("#pc-realm")).toBeDisabled();
  await expect(page.locator("#pc-access")).toBeDisabled();
  await expect(page.getByLabel("NetBox (-nb)")).toBeDisabled();
  await expect(page.getByLabel("NetBox (-nb)")).not.toBeChecked();
  await expect(page.locator("#pc-role")).toBeEnabled();
  await page.selectOption("#pc-role", "triage");
  await page.click(".modal-foot button:has-text('Speichern')");
  await expect(page.locator(".modal-scrim")).toHaveCount(0);
  await expect(row).toContainText("aus dem Cockpit");

  // neuer Kunde in der Oberfläche: NetBox vorgewählt, abwählbar
  await page.click("button:has-text('+ Kunde')");
  await expect(page.getByLabel("NetBox (-nb)")).toBeChecked();
  await page.fill("#pc-short", "ohne-nb-e2e");
  await page.fill("#pc-name", "Ohne NB E2E");
  await page.getByLabel("NetBox (-nb)").uncheck();
  await page.getByLabel("Doku (Automato, -doc)").check();
  await page.click(".modal-foot button:has-text('Speichern')");
  const own = page.locator("#portal-customers tbody tr", { hasText: "ohne-nb-e2e" }).first();
  await expect(own).toContainText("Doku");
  await expect(own).not.toContainText("NetBox");
  await expect(own).not.toContainText("aus dem Cockpit");
  page.once("dialog", (d) => d.accept());
  await own.locator("button:has-text('Entfernen')").click();
  await expect(page.locator("#portal-customers tbody tr", { hasText: "ohne-nb-e2e" })).toHaveCount(0);

  // im Cockpit entfernt: weg
  const leer = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [] } });
  expect((await leer.json()).entfernt).toEqual(["cockpit-e2e"]);
  await page.reload();
  await expect(page.locator("#portal-customers tbody tr", { hasText: "cockpit-e2e" })).toHaveCount(0);
});
