// 1.13.0 (Anwender 2026-11-16, Cockpit ADR 0011 Stufe C: „Automato mit dem Cockpit koppeln – bisher Token
// kopieren – künftig Kopplung mit Einmal-Code“; ADR 0057): im Kunden-Portal einen Einmal-Code erzeugen, das
// Cockpit löst ihn mit POST /api/kopplung ein und holt mit seinem neuen Token das Inventar; entkoppeln.
import { expect, test } from "./fixtures";

test("Kunden-Portal: Kopplung mit dem Cockpit über einen Einmal-Code (1.13.0)", async ({ page }) => {
  await page.goto("/#kundenportal");
  const card = page.locator("#kopplung-cockpit");
  await expect(card.locator("#kopplung-stand")).toHaveText("Nicht gekoppelt");
  // e2e/server.py setzt ein Token in der Umgebung (früherer Weg) – es gilt zusätzlich
  await expect(card).toContainText("AUTOMATO_COCKPIT_TOKEN");

  await card.locator("#kopplung-neu").click();
  const code = (await card.locator("#kopplung-code").innerText()).trim();
  expect(code).toMatch(/^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$/);
  await expect(card).toContainText("höchstens 5 Fehlversuche");

  // wie das Cockpit: falscher Code abgelehnt, richtiger eingelöst – einmal
  const einloesen = (c: string) =>
    page.request.post("/api/kopplung", { data: { vertrag: 1, code: c, cockpit: { name: "Cockpit E2E" } } });
  expect((await einloesen("0000-0000-0001")).status()).toBe(401);
  const ok = await einloesen(code);
  expect(ok.status()).toBe(200);
  const { token } = await ok.json();
  expect((await einloesen(code)).status()).toBe(401);

  const inventar = await page.request.get("/api/inventory", { headers: { Authorization: `Bearer ${token}` } });
  expect(inventar.status()).toBe(200);

  // die Karte merkt die Kopplung von selbst (fragt nach, solange der Code angezeigt ist)
  await expect(card.locator("#kopplung-stand")).toContainText("Gekoppelt mit „Cockpit E2E“", { timeout: 15_000 });
  await expect(card.locator("#kopplung-code")).toHaveCount(0);
  await expect(card.locator("#kopplung-zuletzt")).toContainText("Zuletzt benutzt");

  // entkoppeln: das Token gilt nicht mehr (das aus der Umgebung weiter)
  page.once("dialog", (d) => d.accept());
  await card.locator("button:has-text('Entkoppeln')").click();
  await expect(card.locator("#kopplung-stand")).toHaveText("Nicht gekoppelt");
  const danach = await page.request.get("/api/inventory", { headers: { Authorization: `Bearer ${token}` } });
  expect(danach.status()).toBe(401);
});
