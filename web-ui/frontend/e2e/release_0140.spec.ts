// 0.140.0 (Anwender 2026-11-06: „Wir rechnen heute nicht alle Wege durch und lassen auch sonst
// immer wieder viel liegen, dies ist nicht zuverlässig und daher gefährlich“): greift eine Grenze,
// steht es da – in den Verbindungen, in der Doku und als Befund „Applikation: unvollständig geprüft“.
import { expect, rows, test } from "./fixtures";

test("Verbindungen und Doku: unvollständig geprüft sichtbar (0.140.0)", async ({ page }) => {
  await page.route(/\/api\/applications\/\d+\/connections(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.coverage = { complete: false, checked: 2000, total: 2610, cuts: { limit: { rules: 0, missing: 610 } } };
    await route.fulfill({ response: res, json: body });
  });
  await page.route(/\/api\/applications\/\d+\/doc(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.summary.path_unchecked = 3;
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Portal Süd" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Portal Süd");
  const chip = page.locator("#app-connections #conn-coverage");
  await expect(chip).toHaveText("unvollständig geprüft: 2000 von 2610 Verbindungen");
  await expect(chip).toHaveAttribute("title", /für diesen Teil fehlen Befunde/);
  await expect(page.locator("#app-path-unchecked")).toHaveText("3 Regeln: Weg nicht geprüft");
});

test("Findings: Applikation unvollständig geprüft mit Link zur Applikation (0.140.0)", async ({ page }) => {
  await page.route(/\/api\/findings(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const one = {
      id: 990001, tenant_id: 1, type: "app_coverage", severity: "warning", status: "open",
      subject_kind: "application", subject_id: "Portal Süd", source_system_id: null,
      message: "Applikation „Portal Süd“ nicht vollständig geprüft: 610 Verbindungen nicht geprüft (Grenze AUTOMATO_APP_TRACES_MAX).",
      hint: "Für den nicht geprüften Teil fehlen Befunde.",
      detail: { application_id: 1, application: "Portal Süd", checked: 2000, total: 2610, cuts: { limit: { rules: 0, missing: 610 } } },
      first_seen: "2026-11-06T08:00:00+00:00", last_seen: "2026-11-06T08:00:00+00:00",
    };
    await route.fulfill({ response: res, json: Array.isArray(body) ? [one, ...body] : body });
  });
  await page.goto("/#findings");
  const row = page.locator("tr[data-finding='990001']");
  await expect(row).toContainText("Applikation: unvollständig geprüft");
  await row.click();
  await expect(page.getByText("Applikation Portal Süd öffnen")).toBeVisible();
});
