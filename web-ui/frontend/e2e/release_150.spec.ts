// 1.5.0 (Anwender 2026-11-10: „Kann ich eventuell noch einstellen, von wo ich die einzelnen Kunden drauf zugreifen
// können? Bei VB wäre es am liebsten nur von intern möglich, andere ‚zahlende‘ Kunden von extern“): Zugriff je Kunde
// im Kunden-Portal – überall, nur intern oder nur aus bestimmten Netzen.
import { expect, test } from "./fixtures";

test("Kunden-Portal: Zugriff je Kunde (1.5.0)", async ({ page, problems }) => {
  await page.goto("/#kundenportal");
  await page.click("button:has-text('+ Kunde')");
  await page.fill("#pc-short", "zugriff-e2e");
  await page.fill("#pc-name", "Zugriff E2E");
  // Vorgabe wie bisher: überall
  await expect(page.locator("#pc-access")).toHaveValue("ueberall");
  await page.selectOption("#pc-access", "netze");
  await page.fill("#pc-access-nets", "203.0.113.0/24\nkein-netz");
  await page.click(".modal-foot button:has-text('Speichern')");
  await expect(page.locator(".modal .error")).toContainText("ungültig");
  // die abgelehnte Angabe (422) ist hier gewollt
  problems.splice(0, problems.length, ...problems.filter((p) => !/^422 .*\/api\/portal\/customers$/.test(p)));
  await page.fill("#pc-access-nets", "203.0.113.0/24, 198.51.100.7");
  await page.click(".modal-foot button:has-text('Speichern')");

  const row = page.locator("#portal-customers tbody tr", { hasText: "zugriff-e2e" }).first();
  await expect(row).toContainText("nur 203.0.113.0/24, 198.51.100.7/32");

  await row.locator("button:has-text('Bearbeiten')").click();
  await expect(page.locator("#pc-access-nets")).toHaveValue("203.0.113.0/24\n198.51.100.7/32");
  await page.selectOption("#pc-access", "intern");
  await expect(page.locator("#pc-access-nets")).toHaveCount(0);
  await page.click(".modal-foot button:has-text('Speichern')");
  await expect(row).toContainText("nur intern");

  page.once("dialog", (d) => d.accept());
  await row.locator("button:has-text('Entfernen')").click();
  await expect(page.locator("#portal-customers tbody tr", { hasText: "zugriff-e2e" })).toHaveCount(0);
});
