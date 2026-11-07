// 0.120.0 (Rückmeldung des Anwenders 2026-11-02): Regelwerke in der Doku vergleichen – gerechnet nur auf Knopfdruck.
import { expect, test } from "./fixtures";

test("Applikations-Doku: Regelwerk vergleichen erst auf Knopfdruck (0.120.0)", async ({ page }) => {
  let computed = 0;
  page.on("request", (r) => {
    if (r.url().includes("/ruleset-compare?key=")) computed += 1;
  });
  await page.goto("/#apps");
  await page.locator("table tbody tr", { hasText: "Webshop" }).first().click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  await page.click("#app-ruleset-compare");
  const box = page.locator("#ruleset-compare");
  await expect(box).toContainText("Regelwerk 1 – Stand bis 0.117.0");
  await expect(box.locator("#ruleset-compare-key")).toHaveValue("v2");
  await expect(box).toContainText("erst auf Knopfdruck");
  expect(computed).toBe(0); // Fenster öffnen rechnet nichts
  await box.locator("#ruleset-compare-run").click();
  const result = page.locator("#ruleset-compare-result");
  await expect(result).toContainText("Regelwerk 2 – Rückmeldung 2026-10-30");
  await expect(result.locator("#ruleset-compare-rules-added")).toBeVisible();
  await expect(result.locator("#ruleset-compare-cat-removed")).toBeVisible();
  expect(computed).toBe(1);
  // die Doku bleibt beim aktiven Regelwerk
  await page.keyboard.press("Escape");
  await expect(page.locator("#ruleset-compare")).toHaveCount(0);
  await expect(page.locator("#app-ruleset")).toContainText("Regelwerk 1");
});
