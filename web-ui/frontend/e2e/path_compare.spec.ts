// Wege vergleichen (1.19.0, ADR 0067): ein L3-Weg gegen L1/L2 – je Abschnitt VLAN erlaubt, Kabel und Nachbarn, Spanning
// Tree; nicht Prüfbares mit Grund. Beispieldaten: fw-08 (dmz) an sw-acc-01 Te1/1/1, der DMZ-Webserver 10.30.0.10 an
// sw-acc-01 Gi1/0/9 (VLAN 30), keine Kabel in NetBox.
import { expect, test } from "./fixtures";

test("Weg vergleichen: Anschluss des Ziels geprüft, Kabel ohne NetBox gezählt", async ({ page }) => {
  await page.goto("/#vergleich");
  await expect(page.locator(".page-head h1")).toContainText("Wege vergleichen");
  await expect(page.locator("nav.nav .nav-item.active")).toContainText("Wege vergleichen");
  await page.fill("#compare-src", "10.10.0.5");
  await page.fill("#compare-dst", "10.30.0.10");
  await page.click("#compare-run");
  await expect(page).toHaveURL(/#vergleich\?src=10\.10\.0\.5&dst=10\.30\.0\.10$/);
  const sec = page.locator(".compare-section").first();
  await expect(sec).toContainText("Anschluss · fw-08 dmz → sw-acc-01 Gi1/0/9");
  await expect(sec).toContainText("VLAN 30");
  await expect(sec.locator(".card-head .compare-state")).toHaveText("teilweise geprüft");
  await expect(sec.locator(".compare-segment")).toContainText("fw-08 dmz → sw-acc-01 (Te1/1/1 → Gi1/0/9)");
  await expect(sec.locator(".compare-check[data-kind='vlan'] .compare-state")).toHaveText("passt");
  await expect(sec.locator(".compare-check[data-kind='stp'] .compare-state")).toHaveText("passt");
  await expect(sec.locator(".compare-check[data-kind='cable'] .compare-reason[data-reason='no_netbox']")).toContainText(
    "Kabel nicht in NetBox",
  );
  await expect(page.locator(".compare-summary")).toContainText("1 Abschnitte: 0 passen, 0 mit Abweichung, 1 teilweise geprüft");
  // die Adresse öffnet den Vergleich direkt
  await page.reload();
  await expect(page.locator(".compare-section").first()).toContainText("fw-08 dmz");
});

test("Weg-Suche verlinkt den Vergleich des Wegs", async ({ page }) => {
  await page.goto("/#wege?src=10.10.0.5&dst=10.30.0.10");
  const link = page.locator("#trace-compare");
  await expect(link).toHaveAttribute("href", "#vergleich?src=10.10.0.5&dst=10.30.0.10");
});

test("Abweichungen am Weg: VLAN gesperrt, Kabel passt nicht zu NetBox, Spanning Tree blockiert", async ({ page }) => {
  // Anzeige einer Antwort mit allen drei Abweichungen (die Prüfungen selbst testen die Python-Tests)
  const port = (device: string, p: string, extra = {}) => ({ device, port: p, mode: "tagged", vlans: "130", native_vlan: 1, ...extra });
  const body = {
    notes: [],
    summary: { sections: 1, states: { deviation: 1 }, deviations: { vlan: 1, cable: 1, stp: 1 }, unchecked: [] },
    paths: [
      {
        kind: "active",
        hops: ["fw-a", "fw-b"],
        end: { kind: "connected", text: "Ziel angeschlossen an fw-b" },
        summary: { sections: 1, states: { deviation: 1 }, deviations: { vlan: 1, cable: 1, stp: 1 }, unchecked: [] },
        sections: [
          {
            label: "fw-a transit → fw-b transit",
            kind: "l2",
            a: { label: "fw-a", devices: ["fw-a"], interface: "transit", key: "1|root" },
            b: { label: "fw-b", devices: ["fw-b"], interface: "transit", key: "2|root" },
            vlan: 120,
            vlan_from: "subinterface",
            segment: { steps: [], text: "fw-a port1 → sw-1 (Gi1/0/1 → Gi1/0/48) → sw-2 (Gi1/0/48 → Gi1/0/1) → fw-b port2", switches: 2 },
            state: "deviation",
            reasons: [],
            unchecked: [],
            checks: {
              vlan: { state: "deviation", count: 3, reasons: [], items: [{ kind: "not_allowed", link: "x", ports: [port("sw-1", "Gi1/0/48")] }] },
              cable: {
                state: "deviation",
                count: 3,
                reasons: [],
                items: [
                  {
                    kind: "netbox",
                    link: "x",
                    cable: "K-3",
                    netbox: "netbox",
                    wrong: [{ at: { device: "sw-1", port: "Gi1/0/48" }, netbox: { device: "sw-2", port: "Gi1/0/46" }, seen: [{ device: "sw-2", port: "Gi1/0/48" }] }],
                  },
                ],
              },
              stp: { state: "deviation", count: 4, reasons: [], items: [{ kind: "blocked", link: "x", ...port("sw-2", "Gi1/0/48", { state: "blocking", role: "alternate" }) }] },
            },
          },
        ],
      },
    ],
  };
  await page.route("**/api/path-compare/trace**", (route) => route.fulfill({ json: body }));
  await page.goto("/#vergleich?src=10.10.0.5&dst=10.50.0.10");
  const sec = page.locator(".compare-section[data-state='deviation']");
  await expect(sec.locator(".card-head .compare-state")).toHaveText("Abweichung");
  await expect(sec.locator(".compare-check[data-kind='vlan'] .compare-item")).toHaveText("sw-1 Gi1/0/48: erlaubt 130");
  await expect(sec.locator(".compare-check[data-kind='cable'] .compare-item")).toContainText(
    "Kabel K-3 (netbox) passt nicht: an sw-1 Gi1/0/48 NetBox sw-2 Gi1/0/46, CDP/LLDP sw-2 Gi1/0/48",
  );
  await expect(sec.locator(".compare-check[data-kind='stp'] .compare-item")).toHaveText("sw-2 Gi1/0/48: blocking (alternate)");
  await expect(page.locator(".compare-summary")).toContainText("VLAN erlaubt 1 · Kabel & Nachbarn 1 · Spanning Tree 1");
});

test("Applikation: Verbindungen nacheinander vergleichen, gezählt", async ({ page }) => {
  await page.goto("/#vergleich");
  await page.click("#compare-tab-app");
  await expect(page).toHaveURL(/#vergleich\?tab=app$/);
  await page.locator("#compare-app").selectOption({ label: "Webshop" });
  const rows = page.locator("#compare-connections tbody tr");
  await expect(rows.first()).toBeVisible();
  const n = await rows.count();
  await expect(page.locator("#compare-app-count")).toHaveText(`0 von ${n} Verbindungen verglichen`);
  await page.click("#compare-all");
  await expect(page.locator("#compare-app-count")).toHaveText(`${n} von ${n} Verbindungen verglichen`);
  await expect(page).toHaveURL(/#vergleich\?tab=app&app=\d+$/);
});

test("Alle Abschnitte: Liste mit Zustand und Filter", async ({ page }) => {
  await page.goto("/#vergleich?tab=abschnitte");
  await expect(page.locator("#compare-tab-abschnitte")).toHaveClass(/\bon\b/);
  await expect(page.locator(".compare-summary")).toContainText("Abschnitte:");
  await page.selectOption("#compare-state", "deviation");
  await expect(page.locator(".compare-summary")).toBeVisible();
});
