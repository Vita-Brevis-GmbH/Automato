// Spanning Tree (1.19.0, Anwender 2026-11-17 Priorität 5): Ansicht je VLAN mit Root, Ports (Rolle, Zustand, Gegenstelle) und
// einfachem Baum, blockierte Ports hervorgehoben (Reserve bzw. unerwartet); Soll-Root pflegen; Befunde mit Link in die
// Ansicht; Switches mit BPDU-Guard. Beispieldaten (seed.py `_stp`): sw-acc-01 Root für VLAN 10 und 30, sw-core-01 für VLAN 20,
// sw-core-01 blockiert VLAN 10 zum ESXi-Host (unerwartet) und VLAN 30 zum Leaf (Reserve).
import { expect, rows, test } from "./fixtures";

test("Spanning Tree: Bäume, Ports mit Rolle und Zustand, unerwartet blockiert", async ({ page }) => {
  await page.goto("/#spanningtree");
  const kpis = page.locator("#stp-kpis");
  await expect(kpis.locator(".kpi", { hasText: "Blockierte Ports" }).locator(".k-val")).toHaveText("2");
  await expect(kpis.locator(".kpi", { hasText: "Blockierte Ports" })).toContainText("1 unerwartet");
  await expect(kpis.locator(".kpi", { hasText: "err-disabled" })).toContainText("1 durch BPDU-Guard");
  const trees = page.locator("#stp-trees");
  await expect(trees.locator("tbody tr")).toHaveCount(3);
  const v10 = trees.locator("tbody tr", { hasText: "VLAN 10" });
  await expect(v10).toContainText("sw-acc-01");
  await expect(v10).toContainText("ohne Soll");
  await v10.click();
  const detail = page.locator("#stp-tree");
  await expect(detail.locator(".card-head h2")).toContainText("VLAN 10");
  const eth3 = detail.locator('tr[data-port="sw-core-01|Eth1/3"]');
  await expect(eth3).toContainText("blockiert");
  await expect(eth3).toContainText("unerwartet: Gegenstelle kein Switch");
  await expect(eth3).toContainText("esx01-chzrh1.example.ch");
  await expect(detail.locator('#stp-members tr[data-device="sw-acc-01"]')).toContainText("Root");
  // einfacher Baum: Root oben, der blockierte Port zum ESXi-Host rot gestrichelt
  await expect(page.locator('#stp-diagram [data-node="sw-acc-01"].root')).toBeVisible();
  await expect(page.locator("#stp-diagram .stp-edge.unexpected")).toHaveCount(1);
  await expect(page.locator("#stp-diagram .stp-edge.root")).toHaveCount(1);

  // VLAN 30: Reserve zum Leaf ist erwartet; die Topologieänderungen am Switch
  await page.locator("#stp-filter-vlan").fill("30");
  await expect(trees.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator('tr[data-port="sw-core-01|Eth1/48"]')).toContainText("Reserve zwischen Switches (erwartet)");
  await expect(page.locator('#stp-members tr[data-device="sw-acc-01"]')).toContainText("von Te1/1/1");
  // nur Auffälliges: VLAN 10 (unerwartet blockiert)
  await page.locator("#stp-filter-vlan").fill("");
  await page.check("#stp-filter-only");
  await expect(trees.locator("tbody tr")).toHaveCount(1);
  await expect(trees.locator("tbody tr")).toContainText("VLAN 10");
});

test("Spanning Tree: Soll-Root festlegen, Befund und wieder entfernen", async ({ page }) => {
  await page.goto("/#spanningtree?tab=targets");
  const list = page.locator("#stp-targets");
  await expect(list).toContainText("kein Soll-Root festgelegt");
  await page.fill("#stp-target-root", "sw-core-01");
  await page.fill("#stp-target-vlans", "10, 30");
  await page.click("#stp-target-save");
  const row = list.locator("tbody tr").first();
  await expect(row).toContainText("sw-core-01");
  await expect(row).toContainText("10,30");
  await page.click("#stp-tab-trees");
  await expect(page.locator("#stp-trees tbody tr", { hasText: "VLAN 10" })).toContainText("nicht am Soll");
  await expect(page.locator("#stp-trees tbody tr", { hasText: "VLAN 20" })).toContainText("ohne Soll");
  await page.goto("/#findings");
  const group = page.locator(".fgroup", { hasText: "Spanning Tree" });
  await expect(group).toContainText("Spanning Tree: Root nicht am Soll");
  // aufräumen: ohne Soll wieder nur gemeldet
  await page.goto("/#spanningtree?tab=targets");
  await page.click("#stp-targets .stp-target-delete");
  await expect(page.locator("#stp-targets")).toContainText("kein Soll-Root festgelegt");
});

test("Spanning Tree: Befund mit Link in die Ansicht, Switches mit BPDU-Guard", async ({ page }) => {
  await page.goto("/#findings");
  const group = page.locator(".fgroup", { hasText: "Spanning Tree" });
  await expect(group).toContainText("Spanning Tree: unerwartet blockiert");
  await group.locator(".fline", { hasText: "unerwartet blockiert" }).click();
  const row = rows(page).filter({ hasText: "sw-core-01" }).first();
  await expect(row).toContainText("sw-core-01 Eth1/3 blockiert in VLAN 10: Gegenstelle ist kein Switch");
  await row.click();
  await page.click("button.linkchip:has-text('Spanning Tree öffnen')");
  await expect(page).toHaveURL(/#spanningtree\?vlan=10/);
  await expect(page.locator("#stp-filter-vlan")).toHaveValue("10");
  await expect(page.locator("#stp-trees tbody tr")).toHaveCount(1);

  await page.click("#stp-tab-devices");
  const acc = page.locator('#stp-devices tr[data-device="sw-acc-01"]');
  await expect(acc).toContainText("Rapid-PVST+");
  await expect(acc).toContainText("Gi1/0/12");
  await expect(acc).toContainText("BPDU-Guard");
  await expect(acc).toContainText("VLAN0010, VLAN0030");
});

test("Spanning Tree auf Englisch", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#spanningtree?vlan=10");
  await expect(page.locator("#stp-kpis")).toContainText("Blocked ports");
  await expect(page.locator('tr[data-port="sw-core-01|Eth1/3"]')).toContainText("unexpected: peer not a switch");
});
