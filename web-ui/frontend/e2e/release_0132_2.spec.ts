// 0.132.2 (Anwender 2026-11-04: „die Regel mit Deny beinhaltet Botnet-C&C.Server usw. als Source … er zeigt nur den Weg,
// welches die gesperrte Regel hätte, was aber ja bereits bei sich sperren ist“): eine Sperre mit Internet Service bzw. Land
// vor der erlaubenden Policy ist eine Sperrliste – erlaubt ausser der Liste, in der Zeile der Policy als „Sperrliste“.
import { expect, test } from "./fixtures";

test("Weg-Suche: Sperrliste vor der VIP-Policy, erlaubt ausser der Liste (0.132.2)", async ({ page }) => {
  // echter Weg aus den Beispieldaten; an der ersten Firewall die Regeln wie mit Sperrliste davor
  await page.route(/\/api\/routing\/trace(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const hop = body.paths[0].hops.find((h: { kind?: string }) => h.kind === "firewall" || h.kind === "vdom");
    const entry = { rule_id: 990001, external_id: "5", name: "block-bad", action: "deny", src: null, dst: "any",
      srcintf: ["wan"], dstintf: ["any"], services: ["ALL"], unsure: ["Quelle"], decides: false, blocklist: true };
    const allow = { rule_id: 990002, external_id: "10", name: "inet-to-app", action: "allow", src: "any", dst: "VIP_app",
      srcintf: ["wan"], dstintf: ["transit"], services: ["HTTPS"], unsure: [], decides: true };
    hop.rules = {
      verdict: "allow",
      text: "erlaubt durch Policy 10 „inet-to-app“ – ausser Sperrliste Policy 5 „block-bad“ (Internet Service bzw. Land)",
      rules: [entry, allow],
      blocklists: [entry],
    };
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.10.10.80");
  await page.click("button:has-text('Weg suchen')");
  const mark = page.locator(".hop-blocklist").first();
  await expect(mark).toContainText("Sperrliste (Quelle)");
  await expect(mark).toHaveAttribute("title", /sperrt nur die Adressen ihres Internet Service bzw. Landes/);
});
