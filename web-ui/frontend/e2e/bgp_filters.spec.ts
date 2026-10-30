// 0.113.0 (ADR 0041, Roadmap Version 1 Nr. 3): BGP-Filter und Route Control lesen, Soll/Ist je Nachbar bzw. L3Out,
// Befund und Vorschlag „Netze dahinter“ aus den Filtern. Beispieldaten: fw-05 mit Prefix-Listen am ISP-Nachbarn,
// L3O_inet mit erzwungenem Import.
import { expect, test } from "./fixtures";

test("BGP-Nachbar: Filter, Soll/Ist, BGP-Konfiguration und Befund (0.113.0)", async ({ page, problems }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  const config = page.locator("#bgp-config");
  await expect(config).toContainText("BGP-Konfiguration gelesen: 1 Firewall-Kontext");
  await config.locator("summary").click();
  await expect(config.locator("tbody tr")).toContainText("65001");
  await expect(config.locator("tbody tr")).toContainText("198.51.100.13 (abgeschaltet)");
  const isp = page.locator("tr[data-peer='198.51.100.1']").filter({ hasText: "fw-05" });
  await expect(isp.locator(".bgp-filters [data-way='in']")).toContainText("PL_ISP_IN");
  await expect(isp.locator(".bgp-filters [data-way='out']")).toContainText("PL_DC");
  await isp.locator(".bgp-sollist").click();
  const drawer = page.locator("#bgp-peer");
  await expect(drawer).toContainText("AS 65001 → AS 64500");
  await expect(drawer).toContainText("eBGP");
  // erwartet: Default-Route angekommen, 203.0.113.0/24 nicht
  await expect(drawer.locator("#bgp-in-entries tr[data-prefix='0.0.0.0/0']")).toContainText("1 Netz");
  await expect(drawer.locator("#bgp-in-entries tr[data-prefix='203.0.113.0/24']")).toContainText("nichts angekommen");
  await expect(drawer.locator("#bgp-in-received tr[data-prefix='0.0.0.0/0']")).toContainText("erlaubt");
  // angekündigt (berechnet): redistribute connected durch PL_DC
  await expect(drawer.locator("#bgp-out-announced tr[data-prefix='10.20.0.0/24']")).toContainText("redistribute connected");
  // Soll aus den Zonen (0.114.0): fw-05 liegt in keiner Zone - Grund statt Vorschlag
  await expect(drawer.locator("#bgp-zones-state")).toHaveAttribute("data-state", "no_zone");
  await expect(drawer.locator("#bgp-zones-state")).toContainText("gehört zu keiner Zone");
  // Ausgang (0.116.0): Umschalter, gleiche Begründung
  await drawer.locator("#bgp-zones-way button", { hasText: "Ausgang" }).click();
  await expect(drawer.locator("#bgp-zones")).toHaveAttribute("data-way", "out");
  await expect(drawer.locator("#bgp-zones-state")).toHaveAttribute("data-state", "no_zone");
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);

  await page.goto("/#findings");
  await expect(page.locator(".fgroup", { hasText: "BGP-Filter" })).toContainText("BGP: erwartetes Netz kommt nicht an");
  // 0.113.2: Detail mit Beschriftungen und den Netzen als Liste - keine leere Routentabelle
  await page.locator("tbody tr.clickable", { hasText: "erwartetes Netz" }).first().click();
  const detail = page.locator("tbody tr.subrow");
  await expect(detail.locator("dl")).toContainText("Filter");
  await expect(detail.locator(".kv-list")).toContainText("Netze: 203.0.113.0/24");
  await expect(detail.locator("table.parallel-routes")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("L3Out: Route Control Soll/Ist (0.113.0)", async ({ page, problems }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('L3Outs (ACI)')");
  const row = page.locator("tr[data-l3out='L3O_inet']");
  await expect(row.locator(".rc-cell")).toContainText("import");
  await expect(row.locator(".rc-cell")).toContainText("Import 1 · Export 1 Subnetze");
  await row.locator(".rc-sollist").click();
  const drawer = page.locator("#l3out-rc");
  await expect(drawer.locator("#rc-import tr[data-prefix='0.0.0.0/0']")).toContainText("1 Route");
  await expect(drawer.locator("#rc-received tr[data-prefix='0.0.0.0/0']")).toContainText("erlaubt");
  await expect(drawer.locator("#rc-export")).toContainText("10.20.0.0/24");
  // Soll aus den Zonen (0.116.0): Zustand mit Begründung
  await expect(drawer.locator("#l3out-zones-state")).toHaveAttribute("data-state", /^(no_zones|no_zone|no_neighbor_zone|no_nets|ok|differs)$/);
  expect(problems).toEqual([]);
});

test("Anbindung: Netze dahinter aus den BGP-Filtern (0.113.0)", async ({ page, problems }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('Anbindungen')");
  await page.click("button:has-text('Neue Anbindung')");
  await page.locator("#ex-hops").fill("198.51.100.1");
  await page.click("#ex-nets-filters");
  const list = page.locator("#ex-nets-filters-list");
  await expect(list).toContainText("1 Netze aus PL_ISP_IN"); // ohne die Default-Route
  await expect(list).toContainText("203.0.113.0/24");
  await list.locator("button", { hasText: "einzeln übernehmen" }).click();
  await expect(page.locator("#ex-nets")).toHaveValue("203.0.113.0/24");
  expect(problems).toEqual([]);
});
