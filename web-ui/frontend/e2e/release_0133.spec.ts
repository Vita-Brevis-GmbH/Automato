// 0.133.0 (Anwender 2026-11-04): „sehe ich den BGP Neighbors nicht an, ob sie eine Filter Verletzung haben oder nicht, kann
// man dies mit Grünem oder Gelbem Zeichen, wie bei den Firewall Rules anzeigen?“ – je Nachbar mit gelesener Konfiguration
// ✓ ohne offene Befunde der Routing-Filter bzw. ⚠ mit ihrer Zahl und dem Link in die Findings.
import { expect, test } from "./fixtures";

test("BGP-Nachbarn: Zeichen je Nachbar und Link zu den Befunden (0.133.0)", async ({ page, problems }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  await expect(page.locator("table th", { hasText: "Prüfung" })).toBeVisible();
  // Beispieldaten: am ISP-Nachbarn von fw-05 kommt ein erwartetes Netz nicht an (Befund)
  const isp = page.locator("tr[data-peer='198.51.100.1']").filter({ hasText: "fw-05" });
  const mark = isp.locator(".bgp-check");
  await expect(mark).toHaveAttribute("data-check", "findings");
  await expect(mark).toContainText("⚠");
  await expect(mark).toHaveAttribute("title", /BGP: erwartetes Netz kommt nicht an/);
  await mark.click();
  await expect(page).toHaveURL(/#findings\?q=198\.51\.100\.1/);
  await expect(page.locator("tbody tr.clickable", { hasText: "198.51.100.1" }).first()).toBeVisible();
  expect(problems).toEqual([]);
});

test("BGP-Nachbarn: ohne offene Befunde grünes Zeichen (0.133.0)", async ({ page }) => {
  await page.route(/\/api\/routing\/peers(\?|$)/, async (route) => {
    const res = await route.fetch();
    const rows = (await res.json()) as { attrs?: { config?: unknown }; findings?: unknown[] }[];
    for (const r of rows) r.findings = [];
    await route.fulfill({ response: res, json: rows });
  });
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  const isp = page.locator("tr[data-peer='198.51.100.1']").filter({ hasText: "fw-05" });
  await expect(isp.locator(".bgp-check")).toHaveAttribute("data-check", "ok");
  await expect(isp.locator(".bgp-check")).toHaveText("✓");
});
