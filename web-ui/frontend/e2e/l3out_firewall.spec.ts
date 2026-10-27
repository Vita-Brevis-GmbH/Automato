// L3Outs: Firewall dahinter (ADR 0034). In den Beispieldaten (seed.py) hat L3O_inet des APIC einen
// BGP-Peer ohne gelesenes Gerät – keine Firewall erkannt. Von Hand zuordnen (fw-05, wan1),
// zurücksetzen, „keine Firewall“ und wieder zurücksetzen (die übrigen Tests sehen den Ausgangsstand).
import { expect, test } from "./fixtures";

test("Routing → L3Outs: Firewall dahinter wählen, keine, zurücksetzen", async ({ page }) => {
  await page.goto("/#routing?tab=l3out");
  await expect(page.locator(".tabbar button.on")).toHaveText("L3Outs (ACI)");
  await expect(page.locator("thead")).toContainText("Firewall dahinter");
  const row = page.locator('tr[data-l3out="L3O_inet"]');
  const cell = row.locator(".l3out-fw");
  await expect(cell).toContainText("nicht erkannt");
  await expect(cell.locator(".chip", { hasText: "abgeleitet" })).toBeVisible();

  // von Hand: Firewall und Interface zum L3Out
  await cell.getByRole("button", { name: "ändern" }).click();
  await cell.getByLabel("Firewall").selectOption({ label: "fw-05 · VDOM root" });
  await cell.getByLabel("Interface zum L3Out").selectOption("wan1");
  await cell.getByRole("button", { name: "Speichern" }).click();
  await expect(cell).toContainText("fw-05 · VDOM root");
  await expect(cell).toContainText("wan1");
  await expect(cell.locator(".chip", { hasText: "von Hand" })).toBeVisible();

  // zurücksetzen: wieder abgeleitet
  await cell.getByRole("button", { name: "zurücksetzen" }).click();
  await expect(cell).toContainText("nicht erkannt");
  await expect(cell.locator(".chip", { hasText: "abgeleitet" })).toBeVisible();

  // ausdrücklich keine Firewall – der Contract regelt allein
  await cell.getByRole("button", { name: "ändern" }).click();
  await cell.getByRole("button", { name: "keine Firewall" }).click();
  await expect(cell).toContainText("keine Firewall");
  await expect(cell.locator(".chip", { hasText: /^keine$/ })).toBeVisible();
  await cell.getByRole("button", { name: "zurücksetzen" }).click();
  await expect(cell.locator(".chip", { hasText: "abgeleitet" })).toBeVisible();
});

test("Englisch: L3Outs mit Firewall dahinter", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#routing?tab=l3out");
  await expect(page.locator("thead")).toContainText("Firewall behind");
  const cell = page.locator('tr[data-l3out="L3O_inet"] .l3out-fw');
  await expect(cell).toContainText("not detected");
  await expect(cell.locator(".chip", { hasText: "derived" })).toBeVisible();
  await expect(cell.getByRole("button", { name: "change" })).toBeVisible();
});
