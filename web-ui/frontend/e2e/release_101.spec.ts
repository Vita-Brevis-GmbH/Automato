// 1.0.1 (Anwender 2026-11-18): Doku aller Applikationen als ZIP je Applikation mit Export-Passwort und globaler
// Excel-Export der Findings nach Kategorie. Seit 1.21.0 läuft der Export der Applikationen im Hintergrund
// (Auswahl, Fortschritt und Download: release_1210.spec.ts).
import { expect, test } from "./fixtures";

test("Applikationen: alle exportieren mit Export-Passwort (1.0.1)", async ({ page }) => {
  await page.goto("/#apps");
  await page.locator("#apps-export-all").click();
  const run = page.locator("#apps-export-all-run");
  await expect(run).toBeDisabled();
  await page.locator("#exp-pw").fill("kurz");
  await expect(page.getByText("mindestens 8 Zeichen")).toBeVisible();
  await page.locator("#exp-pw").fill("geheim-1234");
  await page.locator("#exp-pw2").fill("geheim-12345");
  await expect(page.getByText("Passwörter stimmen nicht überein")).toBeVisible();
  await expect(run).toBeDisabled();
  await page.locator("#exp-pw2").fill("geheim-1234");
  await expect(run).toBeEnabled();
  const started = page.waitForResponse((r) => r.url().endsWith("/api/applications/export") && r.status() === 202);
  await run.click();
  const job = await (await started).json();
  const row = page.locator(`#my-exports [data-export-id="${job.id}"]`);
  await expect(row).toHaveAttribute("data-status", "done", { timeout: 60_000 });
  const [dl] = await Promise.all([page.waitForEvent("download"), row.locator(".export-download").click()]);
  expect(dl.suggestedFilename()).toMatch(/^applikationen-kunde-a-\d{4}-\d{2}-\d{2}-\d{4}\.zip$/);
  await row.locator(".export-remove").click();
  await expect(row).toHaveCount(0);
});

test("Findings: Excel-Export aller Findings (1.0.1)", async ({ page }) => {
  await page.goto("/#findings");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.locator("#findings-export").click()]);
  expect(dl.suggestedFilename()).toMatch(/^findings-\d{4}-\d{2}-\d{2}\.xlsx$/);
});
