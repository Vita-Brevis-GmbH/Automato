// 0.133.2 (Anwender 2026-11-04: „Er bricht bei 391 von 808 Wegen · seit 3 min ab, dann geht es eine Weile, dann kommt er
// wieder und startet bei 0 … Ich habe noch immer 83 App, welche nie berechnet wurden“): ein Fehler beim Rechnen steht in der
// Übersicht (statt still neu zu beginnen), nie berechnete Applikationen werden vorgerechnet.
import { expect, test } from "./fixtures";

test("Übersicht: Fehler beim Rechnen sichtbar, nie berechnete werden vorgerechnet (0.133.2)", async ({ page }) => {
  const kind = (seconds: number, variants = 1) => ({ variants, fresh: 0, building: false, built_at: null, seconds });
  const states = {
    enabled: true,
    warming: true,
    warm_days: 7,
    warm_all: true,
    refreshing: 0,
    total: 2,
    counts: { building: 0, stale: 1, never: 1, fresh: 0 },
    last_built_at: null,
    apps: [
      {
        id: 1,
        name: "Riesen-App",
        state: "stale",
        stale: 1,
        auto: false,
        error: "AttributeError: 'IPv4Address' object has no attribute 'prefixlen'",
        kinds: {
          doc: kind(12),
          connections: { ...kind(180), error: { text: "AttributeError: …", at: "2026-11-04T10:00:00+00:00" } },
        },
      },
      { id: 2, name: "Nie-geoeffnet", state: "never", stale: 0, auto: true, error: null, kinds: { doc: kind(0, 0), connections: kind(0, 0) } },
    ],
  };
  await page.route(/\/api\/applications\/doc-states/, (route) => route.fulfill({ json: states }));
  await page.goto("/#uebersicht");
  const big = page.locator("#dash-docs #docs-apps tbody tr[data-app='Riesen-App']");
  await expect(big.locator(".doc-error")).toHaveText("Fehler");
  await expect(big.locator(".doc-error")).toHaveAttribute("title", /Verbindungen: AttributeError.*Log des Orchestrators/);
  await expect(big).not.toContainText("wird vorgerechnet");
  const never = page.locator("#dash-docs #docs-apps tbody tr[data-app='Nie-geoeffnet']");
  await expect(never).toHaveAttribute("data-state", "never");
  await expect(never).toContainText("wird vorgerechnet");
  await expect(page.locator("#dash-docs")).toContainText("vorgerechnet werden Doku und Verbindungen aller Applikationen");
});
