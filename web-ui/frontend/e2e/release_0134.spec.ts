// 0.134.0 (Anwender 2026-11-05: „Er will direkt zum Server, auch wenn die zweite Policy den richtigen Weg wählt“ und
// „Hin und Rückweg geprüft … dieser muss gleich sein, ausnahme ACI Nodes und Firewall Verbunde“): ein Server hinter einer
// VIP wird von aussen über die VIP gesucht (Hinweis mit der Kette), je Weg zeigt ein Zeichen den Rückweg.
import { expect, test } from "./fixtures";

test("Weg-Suche: über die VIP und Rückweg je Weg (0.134.0)", async ({ page }) => {
  await page.route(/\/api\/routing\/trace(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.via_vip = { name: "VIP_web", ext_ip: "203.0.113.80", ext_port: "443", balancer: "vs-web", server: "10.10.10.80",
      others: 0, text: "Ziel 10.10.10.80 ist über VIP VIP_web (203.0.113.80) → vs-web veröffentlicht – Weg von aussen über die VIP" };
    body.src_assumed = { address: "198.18.0.1", text: "Quelle: beliebige Adresse im Internet (198.18.0.1)" };
    body.paths[0].return = { state: "asymmetric", text: "Rückweg über andere Firewalls – Antwort von 10.10.10.80 an 198.18.0.1 über fw-b",
      firewalls: ["fw-a"] };
    if (body.paths[1]) body.paths[1].return = { state: "symmetric", text: "Rückweg über dieselben Firewalls" };
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.10.10.80");
  await page.click("button:has-text('Weg suchen')");
  const banner = page.locator(".trace-via-vip");
  await expect(banner).toContainText("über VIP VIP_web (203.0.113.80:443) → vs-web → 10.10.10.80");
  await expect(banner).toContainText("Quelle: beliebige Adresse im Internet (198.18.0.1)");
  const chip = page.locator(".path-return").first();
  await expect(chip).toHaveAttribute("data-return", "asymmetric");
  await expect(chip).toContainText("Rückweg über andere Firewall");
  await expect(chip).toHaveAttribute("title", /Antwort von 10\.10\.10\.80 an 198\.18\.0\.1 über fw-b/);
});
