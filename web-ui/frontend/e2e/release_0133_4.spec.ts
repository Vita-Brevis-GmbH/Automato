// 0.133.4 (Anwender 2026-11-05: „Policy 145 · erlauben · Quelle any … möglich (Eingang)“): bei „möglich“ zeigt der Hinweis,
// welche Interfaces bzw. Zonen die Policy als Ein- und Ausgang erwartet (z. B. eine SD-WAN-Zone) – zum Vergleich mit dem Hop.
import { expect, test } from "./fixtures";

test("Weg-Suche: möglich (Eingang) mit Ein- und Ausgang der Policy (0.133.4)", async ({ page }) => {
  await page.route(/\/api\/routing\/trace(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const hop = body.paths[0].hops.find((h: { kind?: string }) => h.kind === "firewall" || h.kind === "vdom");
    const entry = { rule_id: 990003, external_id: "145", name: "inet-to-web", action: "allow", src: "any", dst: "VIP_web",
      srcintf: ["virtual-wan-link"], dstintf: ["transit"], services: ["HTTPS"], unsure: ["Eingang"], decides: false };
    hop.rules = { verdict: "depends", text: "nur mögliche Policies, sonst implizit gesperrt – abhängig von: Eingang", rules: [entry] };
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.10.10.80");
  await page.click("button:has-text('Weg suchen')");
  const mark = page.locator(".hop-unsure").first();
  await expect(mark).toContainText("möglich (Eingang)");
  await expect(mark).toHaveAttribute("title", "Eingang der Policy: virtual-wan-link · Ausgang der Policy: transit");
});
