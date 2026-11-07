// 0.131.0 (Anwender 2026-11-04): „In den FIndings, bei den Routingfilters, sehe ich immer noch nicht, welche Netzwerke
// da betroffen sind … oder per Link zu der Info gelangen“ – betroffene Netze mit Gesamtzahl und Link zum BGP-Nachbarn;
// Soll aus den Zonen mit den Adressfamilien der Sitzung, Netzen von Hand nur als Rahmen und dem, was heute ankommt.
import { expect, test } from "./fixtures";

const finding = {
  id: 990131,
  tenant_id: 1,
  type: "bgp_zone_mismatch",
  severity: "info",
  status: "open",
  subject_kind: "node",
  subject_id: "fw-05 · VDOM root → 198.51.100.1",
  source_system_id: null,
  message: "fw-05 · VDOM root zum Nachbarn 198.51.100.1: der Eingangsfilter passt nicht zu den Zonen dahinter (Lan).",
  hint: null,
  note: null,
  first_seen: null,
  last_seen: null,
  detail: {
    label: "fw-05 · VDOM root",
    peer: "198.51.100.1",
    context: "root",
    zones: ["Lan"],
    neighbor_zone: "Lan",
    missing: ["10.80.0.0/16", "10.81.0.0/16"],
    missing_total: 7,
    too_open: ["172.16.0.0/12"],
    too_open_total: 1,
    excluded_allowed: [],
    why: [],
  },
};

test("Befund Routing-Filter: betroffene Netze und Link zum BGP-Nachbarn (0.131.0)", async ({ page }) => {
  await page.route(/\/api\/findings(\?|$)/, (route) =>
    route.fulfill({ json: [finding], headers: { "X-Total-Count": "1", "Content-Type": "application/json" } }),
  );
  await page.goto("/#findings");
  await page.locator("tbody tr.clickable", { hasText: "198.51.100.1" }).first().click();
  const nets = page.locator("tbody tr.subrow .bgp-finding-nets");
  await expect(nets).toContainText("Betroffene Netze");
  await expect(nets.locator("[data-list='missing']")).toContainText("10.80.0.0/16, 10.81.0.0/16");
  await expect(nets.locator("[data-list='missing']")).toContainText("… und 5 weitere");
  await expect(nets.locator("[data-list='too_open']")).toContainText("172.16.0.0/12");
  // die Netze stehen nicht noch einmal als rohe Liste in den Details
  await expect(page.locator("tbody tr.subrow dl.kv")).not.toContainText("missing_total");
  await page.locator("tbody tr.subrow button.linkchip", { hasText: "BGP-Nachbar 198.51.100.1 öffnen" }).click();
  await expect(page.locator(".tabbar button.on, .tabbar button.active").first()).toContainText("BGP-Nachbarn");
  const drawer = page.locator("#bgp-peer");
  await expect(drawer).toContainText("AS 65001 → AS 64500");
});

test("Soll aus den Zonen: Adressfamilien, Rahmen und was heute ankommt (0.131.0)", async ({ page }) => {
  const soll = {
    id: 1,
    peer_ip: "198.51.100.1",
    way: "in",
    state: "differs",
    text: "1 Abweichungen zu den Zonen dahinter",
    home: { id: 1, name: "DC FW", code: null },
    neighbor_zone: { id: 2, name: "Lan", code: null },
    zones: [{ id: 2, name: "Lan", code: null }],
    families: [4],
    frames: [{ prefix: "192.168.0.0/16", zone: "Lan" }],
    public_only: false,
    lists: [
      {
        family: 4,
        name: "PL_LAN_IN",
        filters: {},
        open: true,
        entries: [{ prefix: "10.50.0.0/16", action: "permit", zones: ["Lan"], verdict: "permit", decided: [] }],
        missing: [],
        missing_total: 0,
        too_open: [],
        excluded_allowed: [],
        reasons: [],
        received_total: 1,
        received_default_only: true,
        cli: 'config router prefix-list\n    edit "PL_LAN_IN"',
      },
    ],
  };
  await page.route(/\/api\/routing\/bgp\/peers\/\d+\/zones/, (route) => route.fulfill({ json: soll }));
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  const isp = page.locator("tr[data-peer='198.51.100.1']").filter({ hasText: "fw-05" });
  await isp.locator(".bgp-sollist").click();
  const zones = page.locator("#bgp-peer #bgp-zones");
  await expect(zones.locator("#bgp-zones-families")).toContainText("Adressfamilien der Sitzung: IPv4");
  await expect(zones.locator(".bgp-zone-frames")).toContainText("192.168.0.0/16 (Lan)");
  const list = zones.locator(".bgp-zone-list[data-family='4']");
  await expect(list).toContainText("Heute ohne Eingangsfilter – die Firewall übernimmt alles, was der Nachbar ankündigt.");
  await expect(list.locator(".bgp-zone-received")).toHaveAttribute("data-default-only", "1");
  await expect(list.locator(".bgp-zone-received")).toContainText("Heute erhalten: nur die Default-Route.");
});
