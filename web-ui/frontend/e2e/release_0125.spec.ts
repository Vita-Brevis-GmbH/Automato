// 0.125.0 (ADR 0046, Anwender 2026-11-03): Kategorien im Namenskonzept je Seite mit der Auswahl spezifisch/unspezifisch
// (auch die festen) und Zonentypen mit Zusatz „(Applikation)“ bzw. „(andere)“. Der Katalog des Profils „default“ wird am
// Ende zurückgesetzt (gemeinsame Beispieldaten).
import { expect, test } from "./fixtures";

async function catalogUrl(page: import("@playwright/test").Page): Promise<string> {
  const profiles = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  return `/api/naming-profiles/${profiles.find((p) => p.name === "default")!.id}/appdoc-catalog`;
}

test("Namenskonzept: spezifisch/unspezifisch je Seite und DC (Applikation) → DC (andere) (0.125.0)", async ({ page }) => {
  const url = await catalogUrl(page);
  await page.request.delete(url);
  try {
    await page.goto("/#dict");
    const card = page.locator("#appdoc-catalog");
    await expect(card.locator("#appdoc-catalog-categories tbody tr")).toHaveCount(11);
    await expect(card).toContainText("(Applikation)");
    // Vorgabe: beides; das letzte Häkchen einer Seite bleibt
    await expect(card.locator("#cat-6-dst-specific")).toBeChecked();
    await expect(card.locator("#cat-6-dst-unspecific")).toBeChecked();
    await card.locator("#cat-6-dst-specific").uncheck();
    await expect(card.locator("#cat-6-dst-unspecific")).toBeDisabled();
    // feste Kategorie: Auswahl ebenfalls
    await card.locator("#cat-10-src-unspecific").uncheck();
    await expect(card.locator("#cat-10-src-specific")).toBeDisabled();
    // eigene Kategorie: Applikation im DC → anderes DC
    await page.click("#appdoc-catalog-add-category");
    await card.getByLabel("Titel der Kategorie 12").fill("Zugriff auf andere DC");
    await card.getByLabel("Source-Zone der Kategorie 12").selectOption("DC (Applikation)");
    await card.getByLabel("Dest-Zone der Kategorie 12").selectOption("DC (andere)");
    const row = card.locator("#appdoc-catalog-categories tbody tr").nth(11);
    await expect(row.locator(".chip.info")).toContainText(["DC (Applikation)", "DC (andere)"]);

    const sent = page.waitForRequest((r) => r.url().endsWith("/appdoc-catalog") && r.method() === "PUT");
    await page.click("#appdoc-catalog-save");
    const body = (await sent).postDataJSON() as { categories: { index: number; src: string[]; dst: string[]; src_scope: string[]; dst_scope: string[] }[] };
    const by = new Map(body.categories.map((c) => [c.index, c]));
    expect(by.get(6)!.dst_scope).toEqual(["unspecific"]);
    expect(by.get(6)!.src_scope).toEqual(["specific", "unspecific"]);
    expect(by.get(10)!.src_scope).toEqual(["specific"]);
    expect(by.get(12)).toMatchObject({ src: ["DC (Applikation)"], dst: ["DC (andere)"] });
    await expect(page.locator("#appdoc-catalog-saved")).toBeVisible();
    // nach dem Laden wie gespeichert
    await page.reload();
    await expect(page.locator("#cat-6-dst-specific")).not.toBeChecked();
    await expect(page.locator("#cat-10-src-unspecific")).not.toBeChecked();
    await expect(page.locator("#cat-12-src-specific")).toBeChecked();
  } finally {
    await page.request.delete(url);
  }
});

test("Englisch: Zusatz und Auswahl im Namenskonzept", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  const url = await catalogUrl(page);
  const saved = await page.request.put(url, {
    data: { categories: [{ index: 12, title: "Andere DC", title_en: "Other DC", src: ["DC (Applikation)"], dst: ["DC (andere)"], dst_scope: ["specific"] }] },
  });
  expect(saved.ok()).toBe(true);
  try {
    await page.goto("/#dict");
    const card = page.locator("#appdoc-catalog");
    const row = card.locator("#appdoc-catalog-categories tbody tr", { has: page.locator("#cat-12-src-specific") });
    await expect(row.locator(".chip.info")).toContainText(["DC (application)", "DC (other)"]);
    await expect(row).toContainText("Destinations:");
    await expect(card.locator("#cat-12-dst-unspecific")).not.toBeChecked();
    await expect(card.locator("#cat-12-dst-specific")).toBeDisabled();
  } finally {
    await page.request.delete(url);
  }
});
