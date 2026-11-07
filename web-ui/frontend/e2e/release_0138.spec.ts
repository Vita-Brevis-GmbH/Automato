// 0.138.0 (Anwender 2026-11-05: „500 Firewall Rule Paare, welche ich bestätigen müsste, da eigentlich 99% stimmen … in den
// Basis Settings angeben, dass die Paare automatisch bestätigt werden und dann aber wir die Möglichkeit haben, sie
// auseinander zu nehmen“): Schalter am Mandanten, Paare „✓ automatisch“, im Detail „Kein Paar“ zum Trennen.
import { expect, rows, test } from "./fixtures";
import type { Page } from "@playwright/test";

const search = (page: Page) => page.locator(".filterbar .search input");

async function setAuto(page: Page, on: boolean) {
  await page.goto("/#tenants");
  const settings = page.locator(".card", { has: page.locator("#ts-pairs-auto") });
  const box = settings.locator("#ts-pairs-auto input");
  if ((await box.isChecked()) === on) return;
  await box.setChecked(on);
  await settings.getByRole("button", { name: "Speichern" }).click();
  const chip = page.locator(".site-card.selected");
  if (on) await expect(chip).toContainText("Paare automatisch");
  else await expect(chip).not.toContainText("Paare automatisch");
}

// IPv4-Policy „api-in“ (die IPv6-Zeile hat nur den Paar-Chip „↔ IPv4 …“); der Index folgt im Hintergrund
async function pairChip(page: Page, text: string) {
  const v4 = rows(page).filter({ has: page.locator("span.chip", { hasText: /^IPv4$/ }) });
  await expect(async () => {
    await page.goto("/#regeln");
    await search(page).fill("api-in");
    await expect(v4).toContainText(text, { timeout: 1500 });
  }).toPass({ timeout: 20_000 });
  return v4;
}

test("IPv4/IPv6-Paare automatisch bestätigen, im Detail auseinandernehmen (0.138.0)", async ({ page }) => {
  await setAuto(page, true);
  try {
    const v4 = await pairChip(page, "✓ automatisch");
    await v4.click();
    const drawer = page.locator(".drawer");
    await expect(drawer.locator("#pair-auto")).toContainText("automatisch bestätigt (Einstellung des Mandanten)");
    const split = drawer.locator("#pair-actions button", { hasText: "Kein Paar" });
    await expect(split).toHaveAttribute("title", "Paar auseinandernehmen – dauerhaft, auch nach neuen Läufen");
    // Filter „automatisch bestätigt“
    await page.goto("/#regeln");
    await page.locator(".filterbar select").filter({ hasText: "Paar: alle" }).selectOption("auto");
    await expect(rows(page).filter({ hasText: "api-in" })).toHaveCount(2);
  } finally {
    await setAuto(page, false);
    await pairChip(page, "ähnlich");
  }
});
