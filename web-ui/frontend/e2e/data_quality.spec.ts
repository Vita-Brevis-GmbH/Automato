// Datenqualität (ADR 0029): Kachel in der Übersicht → Reiter im Abgleich mit Ampeln je Bereich, Ursachen und
// Links in die Ansichten; Befund „nur teilweise gelesen“ (fw-05: BGP-Pfade des VDOMs kunde-a nicht lesbar).
import { expect, test } from "./fixtures";

test("Datenqualität: Kachel, Bereiche, Ursache und Link ins Routing", async ({ page }) => {
  await page.goto("/#uebersicht");
  const tile = page.locator("#data-quality-tile");
  await expect(tile).toContainText("Datenqualität");
  await expect(tile.locator(".k-val")).toHaveText(/^\d+%$/);
  await tile.click();
  await expect(page).toHaveURL(/#abgleich\?tab=datenqualitaet/);

  const dq = page.locator("#data-quality");
  await expect(dq.locator(".dq-score")).toContainText("Bereichen in Ordnung");
  await expect(dq.locator(".dq-area")).toHaveCount(5);
  const sources = dq.locator('.dq-area[data-area="sources"]');
  const partial = sources.locator("tr", { hasText: "fw-05: nur teilweise gelesen" });
  await expect(partial).toContainText("Routing nicht lesbar: fw-05 · kunde-a (BGP-Pfade IPv4 (HTTP 403))");
  // Prüfungen ohne Befund erst auf Wunsch
  await expect(sources.locator("tr.dq-ok")).toHaveCount(0);
  await page.check(".dq-toggle input");
  await expect(sources.locator("tr.dq-ok").first()).toBeVisible();
  await partial.locator("button.dq-link").click();
  await expect(page).toHaveURL(/#routing\?tab=routes&context=\d+%7Ckunde-a/);
});

test("Datenqualität: Reiter im Abgleich und Befund in den Findings", async ({ page }) => {
  await page.goto("/#abgleich");
  await page.click("#data-quality-tab");
  await expect(page).toHaveURL(/#abgleich\?tab=datenqualitaet/);
  const rules = page.locator('.dq-area[data-area="rules"]');
  await expect(rules.locator(".card-head")).toContainText("Firewall-Regeln mit Interfaces prüfbar");
  // zurück auf die Befunde: Kacheln wie bisher
  await page.click(".tabbar button:has-text('Befunde & IPAM')");
  await expect(page).toHaveURL(/#abgleich$/);
  await expect(page.locator(".kpis.wrap .kpi").first()).toBeVisible();

  await page.goto("/#findings");
  const group = page.locator(".fgroup", { hasText: "Datenqualität" });
  await expect(group).toContainText("Quellsystem: nur teilweise gelesen");
  await group.locator(".fline", { hasText: "nur teilweise gelesen" }).click();
  const row = page.locator("tbody tr").filter({ hasText: "fw-05" }).first();
  await expect(row).toContainText("Der letzte Lauf von „fw-05“ hat nur einen Teil gelesen");
});

test("Datenqualität auf Englisch", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#abgleich?tab=datenqualitaet");
  const dq = page.locator("#data-quality");
  await expect(dq.locator(".dq-score")).toContainText("areas in order");
  await expect(dq.locator('.dq-area[data-area="sources"] .card-head h2')).toHaveText("Source systems");
  await expect(dq).toContainText("fw-05: only partially collected");
});
