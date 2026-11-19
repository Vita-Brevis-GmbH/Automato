// 1.14.0, ADR 0059 (Anwender 2026-11-16: Abbau mit Frist „zum Entfernen vorgemerkt“ – „Einstellbar, aber sonst ok“): ein
// Kunde, den das Cockpit zum Entfernen vormerkt, ist im Kunden-Portal als gesperrt markiert; nach der Frist nimmt das
// Cockpit ihn heraus und schickt den Löschauftrag – der Ist-Stand im Inventar zeigt ihn.
import { COCKPIT } from "./cockpit"; // Test-Cockpit, mit dem der UI-Server gekoppelt ist (1.14.0)
import { expect, test } from "./fixtures";

test("Kunden-Portal: Kunde zum Entfernen vorgemerkt (1.14.0, ADR 0059)", async ({ page }) => {
  const kunde = { kurzname: "abbau-e2e", name: "Abbau E2E", werkzeuge: ["nb"], zugriff: "ueberall", netze: [] };
  let put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [{ ...kunde, gesperrt: true }] } });
  expect(put.status()).toBe(200);
  await page.goto("/#kundenportal");
  const row = page.locator("#portal-customers tbody tr", { hasText: "abbau-e2e" }).first();
  await expect(row.locator(".portal-gesperrt")).toHaveText("Zugang gesperrt");

  // rückgängig während der Frist: nicht mehr markiert
  put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [kunde] } });
  expect(put.status()).toBe(200);
  await page.reload();
  await expect(row).toBeVisible();
  await expect(row.locator(".portal-gesperrt")).toHaveCount(0);

  // nach der Frist: heraus und Löschauftrag – im Ist-Stand des Inventars
  put = await page.request.put("/api/freigaben", {
    headers: COCKPIT,
    data: { vertrag: 1, kunden: [], entfernen: [{ kurzname: "abbau-e2e", netbox: true, automato_kunde: true, tor: true }] },
  });
  expect(put.status()).toBe(200);
  expect((await put.json()).entfernen).toEqual({ angenommen: ["abbau-e2e"], abgelehnt: [] });
  const inventar = await (await page.request.get("/api/inventory", { headers: COCKPIT })).json();
  const auftrag = (inventar.ist.entfernen as { kurzname: string; stand: string }[]).find((e) => e.kurzname === "abbau-e2e");
  expect(auftrag?.stand).toBe("offen");

  // aufräumen: Auftrag nicht mehr senden
  put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [], entfernen: [] } });
  expect(put.status()).toBe(200);
});
