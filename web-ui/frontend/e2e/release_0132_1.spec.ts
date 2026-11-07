// 0.132.1 (Anwender 2026-11-04: „Das Berechnen einer Applikation dauert sehr lange mehrere Stunden, wenn es 800 Firewall
// Regeln sind“): unter den Verbindungen steht, wie lange die Berechnung gedauert hat – je Teil und die langsamsten Wege.
import { expect, rows, test } from "./fixtures";

test("Verbindungen: Dauer der Berechnung je Teil (0.132.1)", async ({ page }) => {
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Webshop" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  const timing = page.locator("#app-connections #app-conn-timing");
  await expect(timing.locator("summary")).toContainText(/Berechnet in [\d.,]+ s: \d+ Wege in [\d.,]+ s/);
  await timing.locator("summary").click();
  await expect(timing).toContainText("Verbindungen bilden");
  await expect(timing).toContainText("Langsamste Wege:");
});
