// 0.122.0 (Anwender 2026-11-03: „statische Boxen, welche nicht zueinander passen und zeilenweise seitlich gescrollt werden
// muss … etwas dynamischer und mit besseren Ansichten“): Bereiche einer Zeile gleich hoch und ohne Lücke, kein Querscrollen,
// Läufe als Liste, Zonen als waagrechte Balken, Breite je Bereich wählbar, Verschieben per Ziehen.
import { expect, test } from "./fixtures";

test("Übersicht: gleich hoch, ohne Querscrollen, Breite wählbar, ziehen (0.122.0)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/#uebersicht");
  const grid = page.locator("#overview-grid");
  const cell = (w: string) => grid.locator(`[data-widget='${w}']`);
  await expect(page.locator("#dash-runs .run-list li").first()).toBeVisible();
  await expect(page.locator("#dash-runs table")).toHaveCount(0);
  await expect(page.locator("#dash-zones .hbar-row").first()).toBeVisible();
  await expect(page.locator("#dash-objects .hbar-row").first()).toBeVisible();
  // kein Bereich breiter als sein Platz
  const overflow = await grid.evaluate(
    (g) => [...g.querySelectorAll(".dash-cell, .card-body, .dash-body")].filter((e) => e.scrollWidth > e.clientWidth + 1).length,
  );
  expect(overflow).toBe(0);
  // Findings und Läufe-Grafik in einer Zeile, gleich hoch
  const a = (await cell("findings").boundingBox())!;
  const b = (await cell("runschart").boundingBox())!;
  expect(Math.abs(a.y - b.y)).toBeLessThan(2);
  expect(Math.abs(a.height - b.height)).toBeLessThan(2);

  // Breite: Findings ganz – bleibt nach dem Neuladen
  await page.click("#overview-customize");
  await page.locator("#overview-layout li[data-widget='findings'] button[data-size='l']").click();
  const full = (await grid.boundingBox())!;
  await expect.poll(async () => (await cell("findings").boundingBox())!.width).toBeGreaterThan(full.width - 2);
  // ⅓ und ½ in einer Zeile: zusammen die ganze Breite (keine Lücke)
  await page.locator("#overview-layout li[data-widget='runschart'] button[data-size='s']").click();
  await expect.poll(async () => {
    const r = (await cell("runschart").boundingBox())!;
    const s = (await cell("runs").boundingBox())!;
    return Math.round(r.width + s.width + 16 - full.width);
  }).toBe(0);
  // ziehen: Objekte vor die Kennzahlen
  await page.locator("#overview-layout li[data-widget='objects']").dragTo(page.locator("#overview-layout li[data-widget='kpis']"));
  await expect(grid.locator(".dash-cell").first()).toHaveAttribute("data-widget", "objects");
  await page.reload();
  await expect(grid.locator(".dash-cell").first()).toHaveAttribute("data-widget", "objects");
  await expect(cell("findings")).toHaveClass(/\bl\b/);
  // Vorgabe setzt Reihenfolge und Breite zurück
  await page.click("#overview-customize");
  await page.click("#overview-layout button:has-text('Vorgabe')");
  await expect(cell("findings")).toHaveClass(/\bm\b/);
  await expect(grid.locator(".dash-cell").first()).toHaveAttribute("data-widget", "kpis");
  await page.click("#overview-layout button:has-text('Fertig')");
  // Zone anklicken: Regeln mit dieser Suche
  await page.locator("#dash-zones .hbar-row[data-key='dmz']").click();
  await expect(page).toHaveURL(/#regeln\?q=dmz/);
});
