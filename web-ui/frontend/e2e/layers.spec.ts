// Ansichten nach Netzwerkschichten (1.19.0, ADR 0067; Anwender 2026-11-17 Prio 3): Navigation je Schicht, neue Adressen
// `#<schicht>/<stelle>`, alte Adressen leiten um, Reiter schreiben ihre Stelle, Module je Mandant wirken weiter.
import { expect, test } from "./fixtures";

// Schicht -> Stellen in der Reihenfolge der Navigation (Mandant „Kunde A“, Admin)
const NAV: [string, string, string[]][] = [
  ["l1", "L1 · Physik", ["L1-Plan", "Verkabelung", "Ports", "Transceiver", "Kabelnummern"]],
  ["l2", "L2 · Switching", ["L2-Plan", "VLANs", "Spanning Tree", "VLAN-Reservierungen"]],
  ["l3", "L3 · Routing", ["L3-Plan", "Netze", "VRF", "Routing", "Statische Routen", "BGP", "AS-Nummern", "AS-Reservierungen"]],
  ["l4-7", "L4–7 · Firewall & Dienste", ["Regeln & Contracts", "Objekte & Inventar", "NAT / VIPs", "Load Balancer", "VPN-Tunnel"]],
];

test("Navigation je Schicht: Abschnitte L1 bis L4–7 mit ihren Stellen, Übergreifendes bleibt", async ({ page }) => {
  await page.goto("/#uebersicht");
  const secs = page.locator("nav.nav .nav-sec");
  await expect(secs).toHaveText(["Dokumentation", ...NAV.map((n) => n[1]), "Administration"]);
  for (const [layer, , labels] of NAV) {
    await expect(page.locator(`nav.nav .nav-item[data-place^='${layer}/']`)).toHaveText(labels.map((l) => new RegExp(`^${l.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}\\d*$`)));
  }
  // übergreifend (ohne Schicht) unter „Dokumentation“: Weg-Suche und Wege vergleichen nebeneinander
  const doc = page.locator("nav.nav .nav-item:not([data-place])");
  await expect(doc.filter({ hasText: "Weg-Suche" })).toHaveCount(1);
  await expect(doc.filter({ hasText: "Wege vergleichen" })).toHaveCount(1);
  await expect(doc.filter({ hasText: "Applikationen" })).toHaveCount(1);
});

test("Navigation: Klick auf eine Stelle öffnet Ansicht bzw. Reiter unter der neuen Adresse", async ({ page }) => {
  const cases: [string, RegExp, string, string | null][] = [
    ["l1/ports", /#l1\/ports$/, "Verkabelung", "Ports"],
    ["l1/transceiver", /#l1\/transceiver$/, "Verkabelung", "Transceiver"],
    ["l2/vlans", /#l2\/vlans$/, "VLANs", null],
    ["l3/bgp", /#l3\/bgp$/, "Routing", "BGP-Nachbarn"],
    ["l3/netze", /#l3\/netze$/, "Objekte & Inventar", "Subnetze"],
    ["l3/as-reservierungen", /#l3\/as-reservierungen$/, "Reservierungen", "AS-Nummern"],
    ["l4-7/loadbalancer", /#l4-7\/loadbalancer$/, "NAT / VIPs", null],
  ];
  await page.goto("/#uebersicht");
  for (const [place, url, title, tab] of cases) {
    await page.locator(`nav.nav .nav-item[data-place='${place}']`).click();
    await expect(page).toHaveURL(url);
    await expect(page.locator(".page-head h1").first()).toContainText(title);
    await expect(page.locator(`nav.nav .nav-item[data-place='${place}']`)).toHaveClass(/\bactive\b/);
    if (tab) await expect(page.locator(".tabbar button.on").first()).toHaveText(tab);
  }
  // Load Balancer: Typ-Filter der NAT / VIPs vorgewählt
  await expect(page.locator("#nat-type")).toHaveValue("lb");
});

test("Alte Adressen leiten auf die neue Stelle um (Parameter bleiben)", async ({ page }) => {
  const cases: [string, RegExp][] = [
    ["/#regeln?q=dmz", /#l4-7\/regeln\?q=dmz$/],
    ["/#nat", /#l4-7\/nat$/],
    ["/#objekte?tab=vrfs", /#l3\/vrf$/],
    ["/#routing?tab=peers", /#l3\/bgp$/],
    ["/#routing?tab=routes", /#l3\/routing\?tab=routes$/],
    ["/#statisch", /#l3\/statisch$/],
    ["/#verkabelung?tab=labels&q=Gi1", /#l1\/kabelnummern\?q=Gi1$/],
    ["/#verkabelung?tab=vlans", /#l2\/vlans$/],
    ["/#spanningtree?vlan=10", /#l2\/spanningtree\?vlan=10$/],
    ["/#reservierungen?tab=asn", /#l3\/as-reservierungen$/],
    ["/#reservierungen", /#l2\/vlan-reservierungen$/],
    ["/#asn", /#l3\/asn$/],
    ["/#vpn", /#l4-7\/vpn$/],
  ];
  for (const [from, to] of cases) {
    await page.goto(from);
    await expect(page).toHaveURL(to);
    await expect(page.locator("nav.nav .nav-item.active")).toHaveCount(1);
  }
  // übergreifende Ansichten behalten ihre Adresse; Unbekanntes landet in der Übersicht
  await page.goto("/#apps");
  await expect(page).toHaveURL(/#apps$/);
  await page.goto("/#vergleich");
  await expect(page).toHaveURL(/#vergleich$/);
  await page.goto("/#l9/gibt-es-nicht");
  await expect(page.locator(".nav-item.active")).toContainText("Übersicht");
});

test("Reiter schreiben ihre Stelle: Routing → BGP-Nachbarn markiert „BGP“", async ({ page }) => {
  await page.goto("/#l3/routing");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  await expect(page).toHaveURL(/#l3\/bgp$/);
  await expect(page.locator("nav.nav .nav-item[data-place='l3/bgp']")).toHaveClass(/\bactive\b/);
  await page.click(".tabbar button:has-text('Routen')");
  await expect(page).toHaveURL(/#l3\/routing$/);
  await expect(page.locator("nav.nav .nav-item[data-place='l3/routing']")).toHaveClass(/\bactive\b/);
  // Verkabelung: Reiter Kabelnummern = Stelle L1 · Kabelnummern
  await page.goto("/#l1/verkabelung");
  await page.click(".tabbar button:has-text('Kabelnummern')");
  await expect(page).toHaveURL(/#l1\/kabelnummern$/);
});

test("Module je Mandant wirken weiter: Analyse aus blendet L1/L2 und Wege vergleichen aus", async ({ page }) => {
  const name = "Schichten-Test";
  const old = ((await (await page.request.get("/api/tenants")).json()) as { id: number; name: string }[]).filter((t) => t.name === name);
  for (const t of old) await page.request.delete(`/api/tenants/${t.id}?confirm=${name}`);
  const created = await page.request.post("/api/tenants", { data: { name } });
  expect(created.ok()).toBeTruthy();
  const tid = ((await created.json()) as { id: number }).id;
  try {
    await page.goto("/#tenants");
    await page.locator(".mandant select").selectOption(String(tid));
    const card = page.locator("#tenant-modules");
    await expect(card.locator("tr[data-module='vergleich']")).toContainText("Wege vergleichen");
    await expect(page.locator("nav.nav .nav-sec[data-layer='l1']")).toBeVisible();
    await card.locator("tr[data-module='analyse'] button.toggle").click();
    await expect(page.locator("nav.nav .nav-sec[data-layer='l1']")).toHaveCount(0);
    await expect(page.locator("nav.nav .nav-sec[data-layer='l2']")).toHaveCount(0);
    // L3 behält Netze und VRF (Inventar im Kern), L4–7 alles ausser VPN (Analyse)
    await expect(page.locator("nav.nav .nav-item[data-place^='l3/']")).toHaveText(["Netze", "VRF"]);
    await expect(page.locator("nav.nav .nav-item[data-place='l4-7/vpn']")).toHaveCount(0);
    await expect(page.locator("nav.nav .nav-item[data-place='l4-7/regeln']")).toHaveCount(1);
    // Wege vergleichen hängt an der Analyse: mit aus
    await expect(card.locator("tr[data-module='vergleich']")).toContainText("Abhängigkeit aus");
    await expect(page.locator("nav.nav .nav-item", { hasText: "Wege vergleichen" })).toHaveCount(0);
    await card.locator("tr[data-module='analyse'] button.toggle").click();
    await expect(page.locator("nav.nav .nav-sec[data-layer='l1']")).toBeVisible();
    await expect(page.locator("nav.nav .nav-item", { hasText: "Wege vergleichen" })).toHaveCount(1);
  } finally {
    await page.request.delete(`/api/tenants/${tid}?confirm=${name}`);
  }
});
