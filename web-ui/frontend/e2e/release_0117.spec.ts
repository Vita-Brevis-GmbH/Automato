// 0.117.0 (Rückmeldung des Anwenders 2026-10-30): Übersicht mit wählbaren Bereichen, Mehrfachauswahl der Findings, Ziel hinter
// VIP/Load Balancer in der Weg-Suche, Objekte nach Präfix der Namenskonvention.
import { expect, rows, test } from "./fixtures";

test("Übersicht: Bereiche an- und abwählen, verschieben, Vorgabe (0.117.0)", async ({ page }) => {
  await page.goto("/#uebersicht");
  const grid = page.locator("#overview-grid");
  for (const w of ["kpis", "quick", "findings", "runschart", "runs", "zones", "routing", "apps", "rules", "objects"])
    await expect(grid.locator(`[data-widget='${w}']`)).toHaveCount(1);
  await expect(page.locator("#dash-objects")).toContainText("je Präfix der Namenskonvention");
  await expect(page.locator("#dash-rules .hbar-row").first()).toBeVisible();
  // ausblenden: bleibt nach dem Neuladen weg (Browser merkt es sich)
  await page.click("#overview-customize");
  await page.locator("#overview-layout li[data-widget='quick'] input[type=checkbox]").uncheck();
  await expect(grid.locator("[data-widget='quick']")).toHaveCount(0);
  // verschieben: Objekte ganz nach oben
  const objects = page.locator("#overview-layout li[data-widget='objects']");
  for (let i = 0; i < 12; i++) {
    const up = objects.locator("button[title='nach oben']");
    if (await up.isDisabled()) break;
    await up.click();
  }
  await expect(grid.locator(".dash-cell").first()).toHaveAttribute("data-widget", "objects");
  await page.reload();
  await expect(grid.locator("[data-widget='quick']")).toHaveCount(0);
  await expect(grid.locator(".dash-cell").first()).toHaveAttribute("data-widget", "objects");
  await page.click("#overview-customize");
  await page.click("#overview-layout button:has-text('Vorgabe')");
  await expect(grid.locator("[data-widget='quick']")).toHaveCount(1);
  // Schnellansicht: VIPs nach Präfix der Namenskonvention
  await page.click("#dash-quick button[data-quick='vips']");
  await expect(page).toHaveURL(/#objekte\?.*prefix=V-EXT_/);
});

test("Findings: mehrere auswählen und ignorieren, dann wieder öffnen (0.117.0)", async ({ page }) => {
  await page.goto("/#findings");
  await page.click(".fline:has-text('Kabel: Speed abweichend')");
  const picked = rows(page).filter({ has: page.locator("td.pick") });
  await expect(picked.first()).toContainText("Kabel: Speed abweichend");
  const open = await picked.count();
  expect(open).toBeGreaterThan(0);
  await page.check("#findings-pick-all");
  await expect(page.locator("#findings-bulk")).toContainText(`${open} ausgewählt`);
  await page.selectOption("#findings-bulk-status", "ignored");
  await page.fill("#findings-bulk-note", "bekannt, wird nicht umgesetzt");
  await page.click("#findings-bulk-apply");
  await expect(page.locator("#findings-bulk")).toHaveCount(0);
  await expect(page.locator("body")).toContainText("Keine offenen Findings");
  // zurück auf offen (die anderen UI-Tests sehen den Befund weiter)
  await page.click(".filterbar .seg button:has-text('ignoriert')");
  await expect(rows(page).filter({ has: page.locator("td.pick") })).toHaveCount(open);
  await page.check("#findings-pick-all");
  await page.selectOption("#findings-bulk-status", "open");
  await page.click("#findings-bulk-apply");
  await page.click(".filterbar .seg button:has-text('offen')");
  await expect(rows(page).filter({ has: page.locator("td.pick") })).toHaveCount(open);
});

test("Weg-Suche: Ziel hinter dem Load Balancer ist die EPG der Server (0.117.0)", async ({ page }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.10.10.80");
  await page.click("button:has-text('Weg suchen')");
  const target = page.locator("#path-target");
  await expect(target).toContainText("EPG TE|AP|EG_db");
  await expect(target).toContainText("hinter Load Balancer vs-shop");
  await expect(target).toContainText("10.10.10.5");
});

test("Objekte: nach Präfix der Namenskonvention filtern (0.117.0)", async ({ page }) => {
  await page.goto("/#objekte");
  const prefix = page.locator("#objects-prefix");
  await expect(prefix).toBeVisible();
  await prefix.selectOption("H_");
  await expect(rows(page).first()).toContainText("H_");
  const names = await rows(page).locator("td").first().allInnerTexts();
  expect(names.every((n) => n.includes("H_"))).toBeTruthy();
  await prefix.selectOption("-");
  await expect(rows(page).filter({ hasText: "H_web01" })).toHaveCount(0);
});
