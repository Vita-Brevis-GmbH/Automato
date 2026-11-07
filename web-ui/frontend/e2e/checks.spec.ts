// Prüfungen (0.110.0, ADR 0039): Reiter auf der Seite Findings mit Modul, Befund-Typen, Dauer und Anzahl; je Mandant
// ausschalten - beim nächsten Abgleich fallen ihre Befunde weg, eingeschaltet kommen sie zurück.
import { expect, test } from "./fixtures";

test("Prüfungen: Dauer und Anzahl, ausschalten und wieder einschalten (0.110.0)", async ({ page, problems }) => {
  let restored = false;
  try {
    await page.goto("/#findings");
    await page.click("#checks-tab");
    await expect(page).toHaveURL(/#findings\?tab=pruefungen/);
    const row = page.locator("#checks tr[data-check='objekte']");
    await expect(row).toContainText("Firewall-Objekte und Namenskonvention");
    await expect(row).toContainText("Kern");
    await expect(row).toContainText("nach jedem Lauf");
    await expect(page.locator("#checks tr[data-check='zonen']")).toContainText("auch beim Speichern einer Zone");

    // Neu abgleichen: Dauer und Anzahl je Prüfung
    await page.click("#checks-reconcile");
    await expect(page.locator("#checks")).toContainText("Neu abgeglichen:");
    await expect(row.locator("td.num").first()).toContainText("ms");

    // ausschalten + neu abgleichen: keine unbenutzten Objekte mehr
    await row.getByRole("button", { name: "ausschalten" }).click();
    await expect(row.getByRole("button", { name: "einschalten" })).toBeVisible();
    await expect(row.locator("td.num").nth(1)).toHaveText("–"); // läuft nicht
    await page.click("#checks-reconcile");
    await expect(page.locator("#checks")).toContainText("Neu abgeglichen:");
    await expect(row.locator("td.num").nth(1)).toHaveText("–");
    await row.getByRole("button", { name: "Unbenutztes Objekt" }).click();
    await expect(page.locator(".tabbar button.on")).toHaveText("Befunde");
    await expect(page.getByText("Keine Einträge")).toBeVisible();

    // wieder einschalten: die Befunde kommen zurück
    await page.click("#checks-tab");
    await row.getByRole("button", { name: "einschalten" }).click();
    await expect(row.getByRole("button", { name: "ausschalten" })).toBeVisible();
    await page.click("#checks-reconcile");
    await expect(page.locator("#checks")).toContainText("Neu abgeglichen:");
    await row.getByRole("button", { name: "Unbenutztes Objekt" }).click();
    await expect(page.locator("tbody tr").first()).toContainText("Unbenutztes Objekt");
    restored = true;
  } finally {
    // abgebrochen: andere Tests sehen wieder alle Befunde
    if (!restored) {
      await page.request.put("/api/checks/objekte?tenant_id=1", { data: { enabled: true } });
      await page.request.post("/api/reconcile?tenant_id=1");
    }
  }
  expect(problems).toEqual([]);
});
