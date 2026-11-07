// 0.132.0 (Anwender 2026-11-04): „Eingangsfilter gibt es nur bei Verbindungen nach nicht durch mich verwaltete Geräte.
// Also WAN, MPLS und Fremde Anbindungen … Eventuell kann man bei dem Netzwerk Design angeben, welche Zonen / Geräte extern
// Managed sind“, Vorgabe „Standard immer intern Verwaltet“ – Häkchen „fremd verwaltet“ je Zone; BGP-Soll ohne Eingangsfilter
// zu einem selbst verwalteten Nachbarn ohne Abweichung, default-originate nur die Default-Route.
import { expect, test } from "./fixtures";

test("Netzwerkdesign: Zone fremd verwaltet, Vorgabe selbst verwaltet (0.132.0)", async ({ page }) => {
  const name = "WAN Provider Test";
  const zones = (await (await page.request.get("/api/zones?tenant_id=1")).json()) as { id: number; name: string }[];
  for (const z of zones.filter((x) => x.name === name)) await page.request.delete(`/api/zones/${z.id}`);
  const created = await page.request.post("/api/zones", { data: { tenant_id: 1, name, kind: "external", selectors: [] } });
  expect(created.status()).toBe(201);
  const zone = (await created.json()) as { id: number; external_managed: boolean };
  expect(zone.external_managed).toBe(false); // auch eine Zone der Art „extern“: Vorgabe selbst verwaltet
  try {
    await page.goto("/#design?tab=appdoc");
    const box = page.locator("#appdoc-zones").getByLabel(`Zone ${name} fremd verwaltet`);
    await expect(box).not.toBeChecked();
    await box.check();
    await page.click("#appdoc-save");
    await expect(page.locator("#appdoc-saved")).toBeVisible();
    await expect(box).toBeChecked();
    const after = (await (await page.request.get("/api/zones?tenant_id=1")).json()) as { id: number; external_managed: boolean }[];
    expect(after.find((z) => z.id === zone.id)?.external_managed).toBe(true);
  } finally {
    await page.request.delete(`/api/zones/${zone.id}`);
  }
});

test("BGP-Soll: selbst verwaltet ohne Eingangsfilter, default-originate (0.132.0)", async ({ page }) => {
  const inbound = {
    id: 1,
    peer_ip: "198.51.100.1",
    way: "in",
    state: "ok",
    text: "Ohne Eingangsfilter – der Nachbar ist selbst verwaltet, kein Eingangsfilter nötig (Netzwerkdesign → Zonen: „fremd verwaltet“)",
    home: { id: 1, name: "DC FW", code: null },
    neighbor_zone: { id: 2, name: "Lan", code: null },
    zones: [{ id: 2, name: "Lan", code: null }],
    families: [4],
    neighbor_external: false,
    lists: [
      {
        family: 4,
        name: "PL_LAN_IN",
        filters: {},
        open: true,
        open_ok: true,
        entries: [{ prefix: "10.50.0.0/16", action: "permit", zones: ["Lan"], verdict: "permit", decided: [] }],
        missing: [],
        missing_total: 0,
        too_open: [],
        excluded_allowed: [],
        reasons: [],
        received_total: 3,
        received_default_only: false,
        cli: 'config router prefix-list\n    edit "PL_LAN_IN"',
      },
    ],
  };
  const outbound = {
    ...inbound,
    way: "out",
    text: "default-originate (IPv4): die Firewall sendet dem Nachbarn nur die Default-Route – kein Ausgangsfilter nötig",
    lists: [],
    originate: [4],
  };
  await page.route(/\/api\/routing\/bgp\/peers\/\d+\/zones/, (route) =>
    route.fulfill({ json: route.request().url().includes("way=out") ? outbound : inbound }),
  );
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  await page.locator("tr[data-peer='198.51.100.1']").filter({ hasText: "fw-05" }).locator(".bgp-sollist").click();
  const zones = page.locator("#bgp-peer #bgp-zones");
  await expect(zones.locator("#bgp-zones-state")).toHaveAttribute("data-state", "ok");
  await expect(zones.locator("#bgp-zones-external")).toHaveCount(0);
  const list = zones.locator(".bgp-zone-list[data-family='4']");
  await expect(list).toContainText("kein Eingangsfilter nötig");
  // Vorschlag nur auf Wunsch
  await expect(list.locator("details.bgp-cli-optional pre")).not.toBeVisible();
  await list.locator("details.bgp-cli-optional summary").click();
  await expect(list.locator("details.bgp-cli-optional pre")).toContainText("PL_LAN_IN");
  await zones.locator("#bgp-zones-way button", { hasText: "Ausgang" }).click();
  await expect(zones.locator("#bgp-zones-originate")).toContainText("default-originate (IPv4): nur die Default-Route");
  await expect(zones.locator("#bgp-zones-state")).toContainText("nur die Default-Route");
  await expect(zones.locator(".bgp-zone-list")).toHaveCount(0);
});
