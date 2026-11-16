// 1.13.0 (Anwender 2026-11-16, Cockpit ADR 0011 Stufe C: „Automato mit dem Cockpit koppeln – bisher Token
// kopieren – künftig Kopplung mit Einmal-Code“; ADR 0057): im Kunden-Portal einen Einmal-Code erzeugen, das
// Cockpit löst ihn mit POST /api/kopplung ein und holt mit seinem neuen Token das Inventar; entkoppeln.
// Seit 1.14.0 (ADR 0058): solange gekoppelt, ist das Kunden-Portal nur lesbar.
import { expect, testOhneCockpit as test } from "./cockpit";

test("Kunden-Portal: Kopplung mit dem Cockpit über einen Einmal-Code (1.13.0)", async ({ page }) => {
  await page.goto("/#kundenportal");
  const card = page.locator("#kopplung-cockpit");
  await expect(card.locator("#kopplung-stand")).toHaveText("Nicht gekoppelt");
  // ohne Cockpit: Kunden in der Oberfläche anlegen
  await expect(page.locator("#portal-nur-cockpit")).toHaveCount(0);
  await expect(page.locator("button:has-text('+ Kunde')")).toBeVisible();

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
  // gekoppelt (1.14.0): Kunden nur noch im Cockpit – das Kunden-Portal ist nur lesbar
  await page.reload();
  await expect(page.locator("#portal-nur-cockpit")).toContainText("nur noch im Cockpit");
  await expect(page.locator("button:has-text('+ Kunde')")).toHaveCount(0);

  // entkoppeln: das Token gilt nicht mehr, das Kunden-Portal ist wieder in der Oberfläche pflegbar
  page.once("dialog", (d) => d.accept());
  await card.locator("button:has-text('Entkoppeln')").click();
  await expect(card.locator("#kopplung-stand")).toHaveText("Nicht gekoppelt");
  const danach = await page.request.get("/api/inventory", { headers: { Authorization: `Bearer ${token}` } });
  expect(danach.status()).toBe(404); // ohne Kopplung und ohne Token aus .env gibt es den Endpunkt nicht
  await page.reload();
  await expect(page.locator("button:has-text('+ Kunde')")).toBeVisible();
});
