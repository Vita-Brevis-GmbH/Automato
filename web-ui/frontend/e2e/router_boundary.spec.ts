// Routing bis an die Firmengrenze (ADR 0032): eigene Umgebung „Umgebung Router“ (ID 2, seed.py) –
// fw-01 leitet über den gelesenen Core-Router rt-core-01 zum Aussenrouter rt-edge-01, der per
// eBGP zu zwei Providern geht (AS 64500 aktiv, AS 64511 Backup); dazu ein Partnernetz über einen
// nicht gelesenen Router ohne BGP.
import { expect, test } from "./fixtures";

test.beforeEach(async ({ page }) => {
  // nach der Grundeinstellung der Fixtures (Mandant 1): die Umgebung mit den Routern
  await page.addInitScript(() => localStorage.setItem("automato_tenant", "2"));
});

test("Weg-Suche: durch gelesene Router bis an die Firmengrenze", async ({ page, problems }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.20.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("203.0.113.50");
  await page.click("button:has-text('Weg suchen')");
  const card = page.locator("#trace-0");
  await expect(card.locator("#path-boundary-0")).toHaveText("Firmengrenze · AS 64500");
  const hops = card.locator("tbody tr");
  await expect(hops).toHaveCount(3);
  await expect(hops.nth(1)).toContainText("rt-core-01");
  await expect(hops.nth(1).locator(".chip", { hasText: /^Router$/ })).toBeVisible();
  await expect(hops.nth(1).locator(".chip.boundary")).toHaveCount(0);
  // am letzten Hop: Firmengrenze mit Grund, AS und Next-Hop
  const last = hops.nth(2);
  await expect(last).toContainText("rt-edge-01");
  await expect(last.locator(".chip.boundary")).toHaveText("Firmengrenze");
  await expect(last.locator(".trace-boundary-text")).toHaveText("Firmengrenze: eBGP zu AS 64500 (198.51.100.1)");
  // Backup-Weg über den zweiten Provider, ebenfalls an der Grenze; Übersicht mit Chip am Ende
  await expect(page.locator("#trace-1 .trace-boundary")).toHaveText("Firmengrenze · AS 64511");
  const overview = page.locator(".card", { hasText: "Wege im Überblick" });
  await expect(overview.locator(".chip.boundary")).toHaveCount(2);

  // Partnernetz über einen Router ohne gelesenes Gerät und ohne BGP: keine Firmengrenze
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("172.16.1.1");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator("#trace-0")).toContainText("gehört keinem gelesenen Gerät");
  await expect(page.locator("#trace-0 .chip.boundary")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("Routing: Router-Kontexte und Topologie mit hervorgehobener Firmengrenze", async ({ page, problems }) => {
  await page.goto("/#routing");
  const ctx = page.locator("#routing-contexts");
  await expect(ctx).toContainText("3 Kontexte");
  await ctx.locator(".fold-title").click();
  const edgeRow = ctx.locator("tbody tr", { hasText: "rt-edge-01" });
  await expect(edgeRow).toContainText("Router/L3-Switch");
  await expect(edgeRow).toContainText("Router Zürich"); // Quelle Switches & Router

  await page.click(".tabbar button:has-text('Topologie')");
  await expect(page.locator("#topology-summary")).toContainText("2 Router/L3-Switches gelesen");
  await expect(page.locator("#topology-boundary")).toHaveText("Firmengrenze: 2 aussen · 1 Grenzknoten");
  await page.locator(".filterbar select[aria-label='Art']").selectOption("boundary");
  const nodes = page.locator("#topology-nodes tbody tr.clickable");
  await expect(nodes).toHaveCount(3); // zwei Provider aussen, der Aussenrouter innen
  const edge = nodes.filter({ hasText: "rt-edge-01" });
  await expect(edge).toHaveClass(/topo-boundary/);
  await expect(edge.locator(".chip.boundary")).toHaveText("Grenzknoten");
  await expect(edge).toContainText("Router (gelesen)");
  await expect(nodes.filter({ hasText: "198.51.100.1" }).locator(".chip.boundary")).toHaveText("Firmengrenze · AS 64500");
  // Kante nach aussen mit dem Grund
  await edge.click();
  const detail = page.locator("tr.detail-row");
  await expect(detail.locator(".chip.boundary", { hasText: "AS 64500" })).toBeVisible();
  // Art „Router (gelesen)“: Core und Aussenrouter
  await page.locator(".filterbar select[aria-label='Art']").selectOption("l3");
  await expect(nodes).toHaveCount(2);
  expect(problems).toEqual([]);
});

test("Englisch: Firmengrenze in Weg-Suche und Topologie", async ({ page, problems }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#routing?tab=topology");
  await expect(page.locator("#topology-boundary")).toHaveText("Company boundary: 2 outside · 1 boundary nodes");
  await page.goto("/#wege");
  await page.getByPlaceholder("Source: IP, network or name").fill("10.20.0.5");
  await page.getByPlaceholder("Destination: IP, network or name").fill("203.0.113.50");
  await page.click("button:has-text('Search path')");
  await expect(page.locator("#path-boundary-0")).toHaveText("Company boundary · AS 64500");
  await expect(page.locator("#trace-0 .trace-boundary-text")).toHaveText("Company boundary: eBGP to AS 64500 (198.51.100.1)");
  expect(problems).toEqual([]);
});
