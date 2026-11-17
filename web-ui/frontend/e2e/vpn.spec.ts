// 1.19.0 (Anwender 2026-11-17: „VPN integrieren“ – „IPsec reicht, ssl-vpn wird nicht mehr unterstützt“, ADR 0063):
// Ansicht „VPN-Tunnel“ im Modul Analyse – Liste mit Zustand, Gegenstelle (gelesen bzw. nicht), Phase 2 und Routen;
// Detail mit Selektoren, Routen über den Tunnel (deckt ein Selektor sie?) und den Netzen dahinter.
import { expect, rows, test } from "./fixtures";
import type { Page } from "@playwright/test";

const TUNNELS = [
  {
    id: 11, ref: "3|root|vpn-b", source_system_id: 3, source: "fw-a", node: "fw-a", context: "root", name: "vpn-b",
    kind: "static", interface: "wan1", remote_gw: "198.51.100.2", local_gw: null, ike_version: 2, status: "up",
    phase2_total: 2, phase2_up: 2, instances: 0, routes: 3, uncovered: 1, partial: 1,
    peers: [{ source_system_id: 4, context: "root", tunnel: "vpn-a", interface: "wan1", how: "gw", label: "fw-b" }],
    proposal: "aes256gcm-prfsha384",
  },
  {
    id: 12, ref: "3|root|vpn-partner", source_system_id: 3, source: "fw-a", node: "fw-a", context: "root", name: "vpn-partner",
    kind: "static", interface: "wan1", remote_gw: "192.0.2.77", local_gw: null, ike_version: 1, status: "down",
    phase2_total: 1, phase2_up: 0, instances: 0, routes: 1, uncovered: 0, partial: 0, peers: [], proposal: "aes256-sha256",
  },
  {
    id: 13, ref: "3|root|dialup", source_system_id: 3, source: "fw-a", node: "fw-a", context: "root", name: "dialup",
    kind: "dialup", interface: "wan1", remote_gw: null, local_gw: null, ike_version: 2, status: "up",
    phase2_total: 1, phase2_up: 1, instances: 1, routes: 1, uncovered: 0, partial: 0, peers: [], proposal: null,
  },
];

const DETAIL = {
  ...TUNNELS[0],
  phase2: [
    { name: "vpn-b-p2", src: ["10.10.0.0/24"], dst: ["10.20.0.0/16"], src_names: [], dst_names: [], unresolved: [], status: "up", attrs: {} },
    { name: "vpn-b-p2b", src: ["10.10.0.5/32"], dst: ["10.21.0.0/17"], src_names: ["H_app"], dst_names: [], unresolved: [], status: "up", attrs: {} },
  ],
  dialup: [],
  route_list: [
    { prefix: "10.20.0.0/16", family: 4, table: "rib", protocol: "static", distance: 10, covered: "full" },
    { prefix: "10.21.0.0/16", family: 4, table: "rib", protocol: "static", distance: 10, covered: "partial" },
    { prefix: "10.30.0.0/16", family: 4, table: "static", protocol: "static", distance: 10, covered: "none" },
  ],
  behind: [
    { net: "10.20.0.0/16", from: ["phase2:vpn-b-p2", "route:rib"] },
    { net: "10.30.0.0/16", from: ["route:static"] },
  ],
  unresolved: [],
  attrs: { proposal: "aes256gcm-prfsha384", dhgrp: "20" },
};

async function mock(page: Page, asked: string[] = []) {
  await page.route(/\/api\/vpn\/tunnels(\?|$)/, (route) => {
    const url = new URL(route.request().url());
    asked.push(url.search);
    const status = url.searchParams.get("status");
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const shown = TUNNELS.filter((t) => (!status || t.status === status) && (!q || `${t.name} ${t.remote_gw}`.toLowerCase().includes(q)));
    return route.fulfill({
      json: {
        tunnels: shown,
        total: shown.length,
        counts: { up: shown.filter((t) => t.status === "up").length, down: shown.filter((t) => t.status === "down").length },
        missing: [{ source_system_id: 5, source: "fw-c", context: "root", missing: ["IPsec Phase 1 (HTTP 403)"] }],
      },
    });
  });
  await page.route(/\/api\/vpn\/tunnels\/11(\?|$)/, (route) => route.fulfill({ json: DETAIL }));
}

test("VPN-Tunnel: Ansicht im Modul Analyse, echte API ohne Tunnel (1.19.0)", async ({ page }) => {
  await page.goto("/#vpn");
  await expect(page.locator("h1")).toHaveText("VPN-Tunnel");
  await expect(page.locator("nav.nav .nav-item.active")).toHaveText("VPN-Tunnel");
  // Beispieldaten ohne IPsec: leere Liste statt Fehler
  await expect(page.locator("#vpn-tunnels")).toContainText("keine VPN-Tunnel");
});

test("VPN-Tunnel: Liste, Filter und Detail (1.19.0)", async ({ page }) => {
  const asked: string[] = [];
  await mock(page, asked);
  await page.goto("/#vpn");
  const table = page.locator("#vpn-table");
  await expect(rows(page)).toHaveCount(3);
  await expect(page.locator("#vpn-counts")).toContainText("3 Tunnel");
  // nicht lesbar: Hinweis mit dem Recht, das fehlt
  await expect(page.locator(".note").filter({ hasText: "IPsec nicht lesbar auf fw-c" })).toContainText("Lesezugriff auf VPN");
  const b = table.locator("tr[data-tunnel='vpn-b']");
  await expect(b).toContainText("198.51.100.2");
  await expect(b).toContainText("→ fw-b (vpn-a)");
  await expect(b).toContainText("2/2");
  await expect(b).toContainText("1 ohne Selektor");
  await expect(b).toContainText("1 teilweise");
  const partner = table.locator("tr[data-tunnel='vpn-partner']");
  await expect(partner).toContainText("nicht gelesen – der Weg endet hier");
  await expect(partner.locator(".chip.crit")).toHaveText("down");
  await expect(table.locator("tr[data-tunnel='dialup']")).toContainText("Dial-up");

  // Filter: Zustand und Suche gehen an den Server
  await page.selectOption("#vpn-status", "down");
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("vpn-partner");
  expect(asked.some((s) => s.includes("status=down"))).toBeTruthy();
  await page.selectOption("#vpn-status", "");
  await page.fill("#vpn-q", "198.51");
  await expect(rows(page)).toHaveCount(1);
  await page.fill("#vpn-q", "");
  await expect(rows(page)).toHaveCount(3);

  // Detail: Phase 2 mit Namen, Routen über den Tunnel mit Selektor, Netze dahinter
  await b.getByRole("button", { name: "vpn-b" }).click();
  const detail = page.locator("#vpn-detail");
  await expect(page.locator(".drawer h2")).toHaveText("VPN vpn-b");
  await expect(detail.locator("#vpn-phase2 tbody tr")).toHaveCount(2);
  await expect(detail.locator("#vpn-phase2")).toContainText("H_app");
  await expect(detail.locator("#vpn-routes tr[data-prefix='10.20.0.0/16']")).toContainText("gedeckt");
  await expect(detail.locator("#vpn-routes tr[data-prefix='10.21.0.0/16']")).toContainText("nur teilweise");
  await expect(detail.locator("#vpn-routes tr[data-prefix='10.30.0.0/16']")).toContainText("kein Selektor");
  await expect(detail.locator("#vpn-behind")).toContainText("10.20.0.0/16 (Phase 2, Route)");
  await expect(detail).toContainText("dhgrp 20");
  await page.keyboard.press("Escape");
  await expect(detail).toHaveCount(0);
});

test("VPN-Tunnel: Link mit festem Schlüssel öffnet den Tunnel (1.19.0)", async ({ page }) => {
  await mock(page);
  await page.goto(`/#vpn?tunnel=${encodeURIComponent("3|root|vpn-b")}`);
  await expect(page.locator(".drawer h2")).toHaveText("VPN vpn-b");
  await expect(page.locator("#vpn-detail #vpn-routes tbody tr")).toHaveCount(3);
});
