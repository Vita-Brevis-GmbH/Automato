// 0.126.0 (Anwender 2026-11-03: „Ordnen wie beim Regelwerk“, „Nummerierung fortlaufend“): Reihenfolge der Kategorien
// im Namenskonzept mit ↑/↓, die Doku zeigt sie so mit fortlaufenden Nummern. Der Katalog des Profils „default“ wird am
// Ende zurückgesetzt (gemeinsame Beispieldaten).
import { expect, test } from "./fixtures";

type Doc = { tables: { index: number; number?: number; title: string }[] };

test("Namenskonzept: Kategorien mit ↑/↓ ordnen, Doku fortlaufend nummeriert (0.126.0)", async ({ page }) => {
  const profiles = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  const url = `/api/naming-profiles/${profiles.find((p) => p.name === "default")!.id}/appdoc-catalog`;
  const apps = (await (await page.request.get("/api/applications?tenant_id=1")).json()) as { id: number; name: string }[];
  const appId = apps.find((a) => a.name === "Webshop")!.id;
  await page.request.delete(url);
  try {
    await page.goto("/#dict");
    const card = page.locator("#appdoc-catalog");
    const rows = card.locator("#appdoc-catalog-categories tbody tr");
    await expect(rows).toHaveCount(11);
    await expect(card.getByLabel("Kategorie 1 nach oben")).toBeDisabled();
    await expect(card.getByLabel("Kategorie 11 nach unten")).toBeDisabled();
    // 9 („Zugriff zu anderen Security Tenants“) auf Platz 3, die feste 10 ganz nach oben
    for (let i = 0; i < 6; i++) await card.getByLabel("Kategorie 9 nach oben").click();
    for (let i = 0; i < 9; i++) await card.getByLabel("Kategorie 10 nach oben").click();
    await expect(rows.nth(0).locator(".cat-no")).toHaveText("1");
    await expect(rows.nth(0).locator("input").first()).toHaveValue("Unspezifische Zugriffe auf die Applikation");
    await expect(rows.nth(3).locator("input").first()).toHaveValue("Zugriff zu anderen Security Tenants");
    await expect(rows.nth(3).locator(".cat-no")).toHaveText("4");

    const sent = page.waitForRequest((r) => r.url().endsWith("/appdoc-catalog") && r.method() === "PUT");
    await page.click("#appdoc-catalog-save");
    const body = (await sent).postDataJSON() as { categories: { index: number }[] };
    expect(body.categories.map((c) => c.index)).toEqual([10, 1, 2, 9, 3, 4, 5, 6, 7, 8, 11]);
    await expect(page.locator("#appdoc-catalog-saved")).toBeVisible();
    await page.reload();
    await expect(rows.nth(3).locator("input").first()).toHaveValue("Zugriff zu anderen Security Tenants");

    // Doku: Kategorien in der Reihenfolge, Nummern fortlaufend (Kennung bleibt)
    await expect
      .poll(async () => {
        const doc = (await (await page.request.get(`/api/applications/${appId}/doc`)).json()) as Doc;
        return doc.tables.slice(0, 4).map((t) => [t.index, t.number]);
      })
      .toEqual([
        [10, 1],
        [1, 2],
        [2, 3],
        [9, 4],
      ]);
    await page.goto(`/#apps?app=${appId}`);
    await expect(page.locator("h1.app-title")).toHaveText("Webshop");
    if ((await page.locator("#app-categories .stack").count()) === 0) await page.locator("#app-categories h2.fold-title").first().click();
    const heads = page.locator("#app-categories .stack > .card h2");
    await expect(heads.first()).toHaveText("1. Unspezifische Zugriffe auf die Applikation");
  } finally {
    await page.request.delete(url);
  }
});
