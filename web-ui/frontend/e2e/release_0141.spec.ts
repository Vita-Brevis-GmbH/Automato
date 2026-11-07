// 0.141.0 (Anwender 2026-11-06: „Dies erst ausführen, wenn die Läufe durch sind … nur die Wege berechnen, welche
// von der geänderten Firewall Regel betroffen sind“): die Übersicht zeigt, dass Befunde und Vorrechnen auf offene
// Läufe warten, und je veralteter Applikation, welche Änderung sie betrifft.
import { expect, test } from "./fixtures";

test("Übersicht: wartet auf Läufe und Grund der Neuberechnung (0.141.0)", async ({ page }) => {
  const kind = (reason?: string) => ({ variants: 1, fresh: 0, building: false, built_at: null, seconds: 4, reason });
  const states = {
    enabled: true,
    warming: false,
    warm_days: 7,
    refreshing: 0,
    waiting_runs: 3,
    generation: 12,
    total: 1,
    counts: { building: 0, stale: 1, never: 0, fresh: 0 },
    last_built_at: null,
    apps: [
      {
        id: 1,
        name: "Portal Süd",
        state: "stale",
        stale: 2,
        auto: true,
        kinds: { doc: kind(), connections: kind("Regel 812 (Urteil auf dem Weg)") },
      },
    ],
  };
  await page.route(/\/api\/applications\/doc-states/, (route) => route.fulfill({ json: states }));
  await page.goto("/#uebersicht");
  await expect(page.locator("#dash-docs #docs-waiting")).toHaveText("wartet auf 3 Läufe");
  const row = page.locator("#dash-docs #docs-apps tbody tr[data-app='Portal Süd']");
  await expect(row.locator(".doc-reason")).toContainText("Regel 812 (Urteil auf dem Weg)");
});
