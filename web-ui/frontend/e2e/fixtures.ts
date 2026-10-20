// Gemeinsame Test-Basis: Mandant „Kunde A“ (ID 1) gewählt, Seitenfehler und fehlgeschlagene
// API-Aufrufe (>= 400) lassen jeden Test scheitern.
import { expect, test as base } from "@playwright/test";

export const test = base.extend<{ problems: string[] }>({
  problems: [
    async ({ page }, use) => {
    const problems: string[] = [];
    page.on("pageerror", (e) => problems.push(`Seitenfehler: ${e.message}`));
    page.on("response", (r) => {
      if (r.url().includes("/api/") && r.status() >= 400) problems.push(`${r.status()} ${r.url()}`);
    });
    await page.addInitScript(() => localStorage.setItem("automato_tenant", "1"));
    await use(problems);
    expect(problems, "keine Seitenfehler und keine fehlgeschlagenen API-Aufrufe").toEqual([]);
    },
    { auto: true }, // für jeden Test, auch ohne ausdrückliche Anforderung
  ],
});

export { expect };

// Zeilen der (ersten) Tabelle als Text
export const rows = (page: import("@playwright/test").Page) => page.locator("tbody tr");
