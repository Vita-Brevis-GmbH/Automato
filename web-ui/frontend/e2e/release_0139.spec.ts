// 0.139.0 (Anwender 2026-11-05: „Das Neuberechnen der Apps und Wege muss besser gesteuert werden … vorberechnen
// ungeachtet der Daten mal starten und dann wenn alle vorberechnet sind, die komplette Berechnung durchführen“ und „nicht
// alle Firewalls und ACI neu integrieren, sondern nur die Regeln neu berechnen, welche geändert haben“): Durchgang des
// Vorrechnens in der Übersicht, „Regeln neu einlesen“ in der Applikation.
import { expect, rows, test } from "./fixtures";

test("Übersicht: Durchgang des Vorrechnens (0.139.0)", async ({ page }) => {
  await page.route(/\/api\/applications\/doc-states/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.warming = true;
    body.round = { number: 2, apps_done: 340, apps_total: 808, done: 700, total: 1616, since: 0, application_id: null, again: true };
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#uebersicht");
  const chip = page.locator("#dash-docs #docs-round");
  await expect(chip).toHaveText("Durchgang 2: 340 von 808 · danach erneut");
  await expect(chip).toHaveAttribute("title", /neue Läufe brechen nichts ab/);
});

test("Applikation: Regeln neu einlesen – nur die Geräte der Applikation (0.139.0)", async ({ page }) => {
  let asked: unknown = null;
  let polls = 0;
  await page.route(/\/api\/applications\/\d+\/rule-sources/, (route) =>
    route.fulfill({
      json: [
        { source_system_id: 10, name: "fw-01", platform: "fortigate", own: true, reasons: ["rules", "path"], open_run: null, last_full_run: "2026-11-05T08:00:00+00:00" },
        { source_system_id: 11, name: "apic-01", platform: "cisco_aci", own: false, reasons: [], open_run: null, last_full_run: null },
      ],
    }),
  );
  await page.route(/\/api\/applications\/\d+\/read-rules/, (route) => {
    asked = route.request().postDataJSON();
    return route.fulfill({
      status: 202,
      json: { queued: 1, items: [{ source_system_id: 10, name: "fw-01", run_id: "r-139", execution: "inprocess", skipped: null }] },
    });
  });
  await page.route(/\/api\/jobs\/r-139$/, (route) => {
    polls += 1;
    const status = polls > 1 ? "completed" : "running";
    return route.fulfill({
      json: { id: "r-139", platform: "fortigate", status, tenant_id: 1, source_system_id: 10, rule_count: 3, message: "nur Regeln", execution: "inprocess", trigger: "manual", queued_at: null, finished_at: null, scope: "rules", application_id: 1 },
    });
  });
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Webshop" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  await page.click("#app-read-rules");
  const dialog = page.locator("#read-rules-dialog");
  const fw = dialog.locator("label[data-source='fw-01']");
  await expect(fw.locator("input")).toBeChecked();
  await expect(fw).toContainText("Regeln der Doku");
  await expect(fw).toContainText("auf dem Weg");
  await expect(dialog.locator("label[data-source='apic-01'] input")).not.toBeChecked();
  await expect(dialog).toContainText("Interfaces, Routing und Fabric bleiben vom letzten vollen Lauf");
  await page.click("#read-rules-start");
  expect(asked).toEqual({ source_system_ids: [10] });
  await expect(page.locator("#read-rules-result li[data-source='fw-01']")).toContainText("eingelesen", { timeout: 15_000 });
  await expect(page.locator("#read-rules-finished")).toContainText("Eingelesen – die Applikation rechnet neu");
});
