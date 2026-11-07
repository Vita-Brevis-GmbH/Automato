// 0.136.0 (Anwender 2026-11-05: „Ja“ – den Rückweg auch in den Verbindungen der Applikations-Doku prüfen): je Verbindung
// zeigt ein Zeichen, wenn die Antwort über eine andere Firewall zurückginge; Zahl in der Übersicht, Filter und Detail.
import { expect, rows, test } from "./fixtures";

const TEXT = "Rückweg über andere Firewalls – Antwort von 10.10.10.80 an 10.20.0.0/24 über fw-b";

test("Verbindungen: Rückweg über andere Firewall (0.136.0)", async ({ page }) => {
  await page.route(/\/api\/applications\/\d+\/connections(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.connections.forEach((c: { return?: unknown }, i: number) => {
      c.return = i === 0 ? { state: "asymmetric", text: TEXT, firewalls: ["fw-a"] } : { state: "symmetric", text: "Rückweg über dieselben Firewalls" };
    });
    body.summary.asymmetric = 1;
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Webshop" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  const card = page.locator("#app-connections");
  await expect(card.locator("#conn-asymmetric")).toHaveText("1 Rückweg über andere Firewall");
  const chip = card.locator(".conn-return").first();
  await expect(chip).toHaveText("Rückweg über andere Firewall");
  await expect(chip).toHaveAttribute("title", TEXT);
  // Filter „Rückweg über andere Firewall“: nur diese Verbindung
  await card.locator("#app-conn-filter").getByText("Rückweg über andere Firewall").click();
  const shown = card.locator("tbody tr.clickable");
  await expect(shown).toHaveCount(1);
  // Detail: Zeile mit dem Rückweg
  await shown.first().click();
  const line = card.locator(".conn-return-line");
  await expect(line).toHaveAttribute("data-return", "asymmetric");
  await expect(line).toContainText(TEXT);
});
