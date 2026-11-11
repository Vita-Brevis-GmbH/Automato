// 1.8.0 (Anwender 2026-11-11): Korrekturen und Erweiterungen aus dem Test von 1.7.0.
import { expect, test } from "./fixtures";

const rows = (page: import("@playwright/test").Page) => page.locator(".card tbody tr:not(.group-row)");

test("Objekte: Reiterwechsel hebt den Filter auf das geöffnete Objekt auf (1.8.0)", async ({ page }) => {
  // Anwender: „Services kommt, hatte noch einen Filter drin, welcher festgehangen ist, nach dem Anzeigen
  // eines Firewall-Objekts (Adressobjekt)“
  const page1 = await (await page.request.get("/api/objects?tenant_id=1&category=address&limit=1")).json();
  const obj = page1.rows[0];
  await page.goto(`/#objekte?object_id=${obj.id}`);
  await page.locator(".drawer button.close").click();
  const chip = page.locator(".filterbar .chip", { hasText: `nur „${obj.name}“` });
  await expect(chip).toBeVisible();
  await expect(rows(page)).toHaveCount(1);

  await page.click(".tabbar button:has-text('Services')");
  await expect(chip).toHaveCount(0);
  await expect(rows(page).filter({ hasText: "FT_https" }).first()).toBeVisible();
  await expect(page.locator(".card")).not.toContainText("Noch keine Objekte");
});
