// 0.133.1 (Anwender 2026-11-04: „Mir fällt auf, dass der "rechnet gerade" immer wieder im Dashboard verschwindet und wieder
// kommt … Es scheint auch, dass er aktuell bei den Verbindungen am Messen ist“): beim Rechnen der Verbindungen steht, wie weit
// (Wege fertig von allen) und seit wann.
import { expect, test } from "./fixtures";

test("Übersicht: Stand der Doku mit Fortschritt der Verbindungen (0.133.1)", async ({ page }) => {
  const kind = (seconds: number) => ({ variants: 1, fresh: 0, building: false, built_at: null, seconds });
  const states = {
    enabled: true,
    warming: true,
    warm_days: 7,
    refreshing: 0,
    total: 1,
    counts: { building: 1, stale: 0, never: 0, fresh: 0 },
    last_built_at: null,
    apps: [
      {
        id: 1,
        name: "Riesen-App",
        state: "building",
        stale: 2,
        auto: false,
        kinds: {
          doc: kind(12),
          connections: { ...kind(3600), building: true, progress: { done: 340, total: 2000, seconds: 754 } },
        },
      },
    ],
  };
  await page.route(/\/api\/applications\/doc-states/, (route) => route.fulfill({ json: states }));
  await page.goto("/#uebersicht");
  const row = page.locator("#dash-docs #docs-apps tbody tr[data-app='Riesen-App']");
  await expect(row).toHaveAttribute("data-state", "building");
  await expect(row.locator(".doc-progress")).toContainText("340 von 2000 Wegen · seit 12 min");
});
