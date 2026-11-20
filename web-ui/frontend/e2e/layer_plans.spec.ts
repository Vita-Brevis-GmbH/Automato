// Pläne je Netzwerkschicht (1.23.0, ADR 0067 Nachtrag 1.23.0; Anwender 2026-11-20: „je einen Physical Layer, Layer L2 und
// einen Layer L3 Plan“): je Schicht eine Stelle, gezeichnet aus den gelesenen Daten (Beispieldaten seed.py: zwei Switches mit
// Po1/Po10, fw-08, ESXi-Host, Leaf; Spanning Tree mit Root und blockierten Ports; Firewalls mit Routing). Ebenen ein- und
// ausblendbar, Klick zeigt Details mit Link in die Detailansicht, Filter nach Standort in der Adresse, Export draw.io.
import { expect, test } from "./fixtures";

const plan = (page: import("@playwright/test").Page) => page.locator("#layer-plan-svg");

test("L1-Plan: Geräte und Kabel mit Ports, Port-Channel gebündelt, Ebenen, Detail mit Link in die Verkabelung", async ({ page }) => {
  await page.goto("/#uebersicht");
  await page.locator("nav.nav .nav-item[data-place='l1/plan-physik']").click();
  await expect(page).toHaveURL(/#l1\/plan-physik$/);
  await expect(page.locator(".page-head h1").first()).toContainText("L1-Plan · Physik");
  await expect(plan(page).locator('[data-node="sw-acc-01"]')).toBeVisible();
  await expect(plan(page).locator('[data-node="fw-08"]')).toHaveAttribute("data-kind", "firewall");
  // Port-Channel: eine Linie mit den Mitgliedern an beiden Enden
  const lag = plan(page).locator('[data-edge="sw-acc-01 → sw-core-01"]');
  await expect(lag).toHaveAttribute("data-kind", "lag");
  await expect(lag.locator(".layer-plan-end")).toHaveText(["Po1: Gi1/0/47, Gi1/0/48", "Po10: Eth1/1, Eth1/2"]);
  // gelesen, aber ohne Kabel: ausgewiesen
  await expect(plan(page).locator('[data-node="fw-05"]')).toContainText("kein Kabel gelesen");
  // Ebenen: Ports und Endgeräte ausblenden
  await page.uncheck("#layer-plan-layer-ports");
  await expect(lag.locator(".layer-plan-end")).toHaveCount(0);
  const esx = plan(page).locator('[data-node="esx01-chzrh1.example.ch"]');
  await expect(esx).toHaveCount(1);
  await page.uncheck("#layer-plan-layer-ends");
  await expect(esx).toHaveCount(0);
  // Klick: Details mit Link in die Verkabelung (Gerät vorgewählt)
  await plan(page).locator('[data-node="sw-acc-01"]').click();
  const focus = page.locator("#layer-plan-focus");
  await expect(focus).toContainText("sw-acc-01");
  await expect(focus).toContainText("Management: 10.0.0.11");
  await expect(page.locator("#layer-plan-open")).toHaveAttribute("href", "#l1/verkabelung?device=sw-acc-01");
  await page.locator("#layer-plan-open").click();
  await expect(page).toHaveURL(/#l1\/verkabelung\?device=sw-acc-01$/);
  await expect(page.locator("select[aria-label='Gerät']").first()).toHaveValue("sw-acc-01");
});

test("L2-Plan: Trunks mit VLANs, Root je VLAN, blockierte Ports; Ebene Spanning Tree ausblendbar", async ({ page }) => {
  await page.goto("/#l2/plan-switching");
  await expect(page.locator("nav.nav .nav-item[data-place='l2/plan-switching']")).toHaveClass(/\bactive\b/);
  await expect(plan(page).locator('[data-node="sw-acc-01"]')).toContainText("Root VLAN 10,30");
  await expect(plan(page).locator('[data-node="sw-core-01"]')).toContainText("Root VLAN 20");
  await expect(plan(page).locator('[data-edge="sw-acc-01 → sw-core-01"]')).toContainText("Trunk 10,20,30");
  const blocked = plan(page).locator('[data-edge="leaf-101 → sw-core-01"]');
  await expect(blocked).toHaveAttribute("data-tone", "stp");
  await expect(blocked).toContainText("✕ STP blockiert sw-core-01 Eth1/48: VLAN 30");
  await page.uncheck("#layer-plan-layer-stp");
  await expect(blocked.locator("text[data-layer='stp']")).toHaveCount(0);
  await expect(blocked.locator("text[data-layer='vlans']")).toHaveText("Trunk 10,20,30");
  await expect(plan(page).locator('[data-node="sw-acc-01"] .layer-plan-badge')).toHaveCount(0);
  // Klick auf die Linie: Ports beider Enden und Link in die Ports der Verkabelung
  await page.check("#layer-plan-layer-stp");
  await blocked.click();
  await expect(page.locator("#layer-plan-focus")).toContainText("Spanning Tree blockiert sw-core-01 Eth1/48 (VLAN 30)");
  await expect(page.locator("#layer-plan-open")).toHaveAttribute("href", "#l1/ports?device=leaf-101");
});

test("L3-Plan: Firewalls, Router, Netze; Standort-Filter in der Adresse; Export draw.io mit Schicht und Standort", async ({ page }) => {
  await page.goto("/#l3/plan-routing");
  await expect(page.locator(".page-head h1").first()).toContainText("L3-Plan · Routing");
  await expect(plan(page).locator('[data-node="fw-08"]')).toBeVisible();
  await expect(plan(page).locator('[data-node="10.10.0.0/24"]')).toHaveAttribute("data-kind", "net");
  await expect(plan(page).locator('[data-edge="fw-09 → fw-10"], [data-edge="fw-10 → fw-09"]').first()).toBeVisible();
  await expect(page.locator("#layer-plan-counts")).toContainText("Elemente");
  // Netze ausblenden
  await page.uncheck("#layer-plan-layer-nets");
  await expect(plan(page).locator('[data-node="10.10.0.0/24"]')).toHaveCount(0);
  // Standort: Zürich hat nichts gelesen; die Auswahl steht in der Adresse
  await page.selectOption("#layer-plan-site", { label: "chzrh1 · Zürich RZ1" });
  await expect(page).toHaveURL(/#l3\/plan-routing\?site=\d+$/);
  await expect(page.locator("#layer-plan")).toContainText("An diesem Standort ist nichts gelesen.");
  await page.selectOption("#layer-plan-site", "0");
  await expect(page).toHaveURL(/#l3\/plan-routing\?site=0$/);
  await expect(plan(page).locator('[data-node="fw-08"]')).toBeVisible();
  await page.reload();
  await expect(page.locator("#layer-plan-site")).toHaveValue("0");
  // Export: draw.io, Dateiname mit Schicht und Standort
  const download = page.waitForEvent("download");
  await page.click("#layer-plan-export-drawio");
  expect((await download).suggestedFilename()).toMatch(/^plan-l3-routing-ohne-standort-\d{4}-\d{2}-\d{2}\.drawio$/);
  // Klick auf eine Firewall: Link ins Routing (Kontext vorgewählt)
  await plan(page).locator('[data-node="fw-08"]').click();
  await expect(page.locator("#layer-plan-open")).toHaveAttribute("href", /^#l3\/routing\?tab=routes&context=\d+%7Croot$/);
});
