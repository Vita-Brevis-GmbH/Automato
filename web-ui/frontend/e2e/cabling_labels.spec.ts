// Verkabelung im Detail (1.19.0, Anwender 2026-11-17 Nr. 4, ADR 0031 Nachtrag 1.19.0): je Port Transceiver,
// Speed/Duplex und Kabelnummer mit Herkunft (NetBox bzw. Automato), Dialog zum Erfassen je Port-Paar und das
// Bestätigen der Kabelnummern im NetBox-Sync. Jeder Test läuft für sich (der erste räumt seine Erfassung auf).
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const UPLINK = "sw-acc-01|gi1/0/47~sw-core-01|eth1/1";

test("Kabelnummer: erfassen, ändern und löschen an einer Verbindung (1.19.0)", async ({ page }) => {
  await page.goto("/#verkabelung");
  await expect(page.locator("h1")).toHaveText("Verkabelung");
  const row = page.locator(`#cabling-links tr[data-link='${UPLINK}']`);
  await expect(row).toBeVisible();
  // erfassen: Enden aus der Verbindung, nicht änderbar
  await row.locator(".cable-label-edit").click();
  const dialog = page.locator("#cable-label-dialog");
  await expect(dialog.locator("#cl-a-device")).toHaveValue("sw-acc-01");
  await expect(dialog.locator("#cl-b-port")).toHaveValue("Eth1/1");
  await expect(dialog.locator("#cl-a-port")).toHaveAttribute("readonly", "");
  await dialog.locator("#cl-label").fill("K-E2E-1");
  await page.locator("#cable-label-save").click();
  await expect(dialog).toHaveCount(0);
  await expect(row.locator(".cable-label")).toContainText("K-E2E-1");
  await expect(row.locator(".cable-label .chip.info")).toHaveText("Automato");
  // Reiter „Kabelnummern“: dieselbe Nummer, Gegenstelle per CDP
  await page.getByRole("button", { name: "Kabelnummern", exact: true }).click();
  const listed = page.locator(`#cabling-labels tr[data-pair='${UPLINK}']`);
  await expect(listed).toContainText("K-E2E-1");
  await expect(page.locator("#cabling-labels-summary")).toContainText("1 in Automato");
  // ändern und löschen (räumt auf)
  await listed.locator(".cable-label-edit").click();
  await expect(page.locator("#cl-label")).toHaveValue("K-E2E-1");
  await page.locator("#cl-label").fill("K-E2E-2");
  await page.locator("#cable-label-save").click();
  await expect(listed).toContainText("K-E2E-2");
  await listed.locator(".cable-label-edit").click();
  await page.locator("#cable-label-delete").click();
  await expect(page.locator("#cabling-labels")).toContainText("keine Kabelnummern");
});

const PORT = {
  device: "sw-acc-01", name: "Te1/1/1", kind: "physical", status: "connected", enabled: true, speed: "10G",
  duplex: "half", mode: "access", vlan: 30, native_vlan: null, trunk_vlans: null, lag: null, description: "fw-08 dmz",
  media: "SFP-10GBase-SR", vpc: null, peer: { device: "fw-08", port: "dmz", read: false, kind: "unknown" },
  link_state: "unread", issues: [], endpoints: 0,
  transceiver: { type: "SFP-10GBase-SR", part: "SFP-10G-SR", serial: "AVD2345A1B2" },
  pair: "fw-08|dmz~sw-acc-01|te1/1/1",
  netbox_cable: null,
  label: {
    state: "differs",
    automato: { id: 5, label: "K-0817", note: null, changed_by: "user:dev", changed_at: null,
                a: { device: "sw-acc-01", port: "Te1/1/1" }, b: { device: "fw-08", port: "dmz" } },
    netbox: [{ id: "41", label: "K-0815", source: "netbox", source_system_id: 9, url: null, status: "connected",
               a: { device: "sw-acc-01", port: "TenGigabitEthernet1/1/1" }, b: { device: "fw-08", port: "dmz" } }],
  },
};

test("Ports: Transceiver, Speed/Duplex und Kabelnummer mit Herkunft (1.19.0)", async ({ page }) => {
  await page.route(/\/api\/cabling\/ports(\?|$)/, (route) => route.fulfill({ json: { total: 1, items: [PORT] } }));
  await page.goto("/#verkabelung");
  await page.getByRole("button", { name: "Ports", exact: true }).click();
  const row = page.locator("#cabling-ports tr[data-port='Te1/1/1']");
  await expect(row.locator(".port-speed")).toHaveText("10G/half");
  await expect(row.locator(".transceiver")).toHaveText("SFP-10GBase-SR · SFP-10G-SR · S/N AVD2345A1B2");
  const label = row.locator(".cable-label");
  await expect(label).toHaveAttribute("data-state", "differs");
  await expect(label).toContainText("K-0817 (Automato) · K-0815 (NetBox)");
  // Dialog: Wert aus NetBox als Hinweis
  await label.locator(".cable-label-edit").click();
  await expect(page.locator("#cable-label-dialog")).toContainText("In NetBox: K-0815");
  await expect(page.locator("#cl-label")).toHaveValue("K-0817");
  await page.keyboard.press("Escape");
  // Geräte: Spalte mit dem Lesestatus der Transceiver je Switch
  await page.getByRole("button", { name: "Geräte", exact: true }).click();
  await expect(page.locator("#cabling-devices thead")).toContainText("Transceiver");
});

async function mockSync(page: Page, applied: unknown[]) {
  const plan = {
    id: 77, tenant_id: 1, source_system_id: 9, target: "netbox", target_url: "https://nb.example.ch", status: "ready",
    options: { sections: ["inventory", "cabling"], allow_delete: false }, summary: {
      cable: { create: 0, update: 0, unchanged: 2, conflict: 0, delete: 0, obsolete: 0 },
      cable_label: { create: 0, update: 2, unchanged: 0, conflict: 0, delete: 0, obsolete: 0 },
    },
    totals: { create: 0, update: 2, unchanged: 2, conflict: 0, delete: 0, obsolete: 0 },
    warnings: ["Kabelnummern: 1 nur in NetBox – bleiben, wie sie sind."], message: null, result: null,
    verification: null, created_by: "dev", created_at: new Date().toISOString(), applied_by: null, applied_at: null,
    can_apply: true,
  };
  const items = [
    { kind: "cable_label", key: UPLINK, label: "Kabelnummer sw-acc-01 Gi1/0/47 ↔ sw-core-01 Eth1/1", action: "update",
      id: 41, manual: false, confirm: true, changes: [{ field: "label", old: "K-ALT", new: "K-100" }] },
    { kind: "cable_label", key: "leaf-101|eth1/10~sw-core-01|eth1/10", label: "Kabelnummer leaf-101 eth1/10 ↔ sw-core-01 Eth1/10",
      action: "update", id: 42, manual: true, confirm: true, changes: [{ field: "label", old: null, new: "K-200" }] },
  ];
  await page.route(/\/api\/netbox-sync\/targets(\?|$)/, (r) =>
    r.fulfill({ json: [{ id: 9, name: "netbox", base_url: "https://nb.example.ch", ref_role: "primary", has_write_token: true }] }));
  await page.route(/\/api\/netbox-sync\/plans(\?|$)/, (r) => r.fulfill({ json: [plan] }));
  await page.route(/\/api\/netbox-sync\/plans\/77(\?|$)/, (r) => r.fulfill({ json: plan }));
  await page.route(/\/api\/netbox-sync\/plans\/77\/items/, (r) => r.fulfill({ json: { total: items.length, rows: items } }));
  await page.route(/\/api\/netbox-sync\/plans\/77\/apply/, (r) => {
    applied.push(r.request().postDataJSON());
    return r.fulfill({ status: 202, json: { ...plan, status: "applying" } });
  });
}

test("NetBox-Sync: Kabelnummern je Kabel bestätigen, nur Bestätigtes geht mit (1.19.0)", async ({ page }) => {
  const applied: unknown[] = [];
  await mockSync(page, applied);
  await page.goto("/#netbox");
  const card = page.locator("#sync-cable-labels");
  await expect(card.locator("tbody tr")).toHaveCount(2);
  await expect(card.locator(`tr[data-key='${UPLINK}']`)).toContainText("K-ALT");
  await expect(card.locator(`tr[data-key='${UPLINK}']`)).toContainText("K-100");
  await expect(card).toContainText("von Hand gepflegt");
  await expect(card).toContainText("0 von 2 bestätigt");
  await card.locator(`tr[data-key='${UPLINK}'] input[type=checkbox]`).check();
  await expect(card).toContainText("1 von 2 bestätigt");
  await page.locator(".card-head button.btn-primary", { hasText: "Übernehmen" }).click();
  const modal = page.locator(".modal");
  await expect(modal).toContainText("Kabelnummern: 1 von 2 bestätigt – nur diese werden geschrieben.");
  await modal.getByRole("button", { name: "Übernehmen" }).click();
  await expect.poll(() => applied.length).toBe(1);
  expect(applied[0]).toEqual({ confirm: [UPLINK] });
});
