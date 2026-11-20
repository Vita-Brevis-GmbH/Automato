// Kabelnummern erfassen mit Auswahl (1.23.0, Anwender 2026-11-20: „bitte ermöglichen, dass ich die Geräte und
// Ports auswählen kann“): Gerät mit Suche, dann die Ports dieses Geräts; die bekannte Gegenstelle (CDP/LLDP)
// wird vorgeschlagen; frei nur, wo kein gelesenes Gerät existiert (z. B. Patchpanel) – gekennzeichnet.
// Jeder Test läuft für sich (räumt seine Erfassung auf).
import { expect, test } from "./fixtures";

test("Kabelnummer: Gerät und Port wählen, Gegenstelle vorgeschlagen, Patchpanel frei (1.23.0)", async ({ page }) => {
  await page.goto("/#verkabelung");
  await page.locator(".tabbar").getByRole("button", { name: "Kabelnummern", exact: true }).click();
  await page.locator("#cabling-labels-new").click();
  const dialog = page.locator("#cable-label-dialog");
  // Gerät A mit Suche: gelesene Geräte zuerst, mit Herkunft
  await dialog.locator("#cl-a-device").fill("acc");
  const option = dialog.locator("#cl-a-devices .cl-device-option[data-name='sw-acc-01']");
  await expect(option).toContainText("gelesen");
  await option.click();
  await expect(dialog.locator("#cl-a-device")).toHaveValue("sw-acc-01");
  await expect(dialog.locator("[data-end='a'] .cl-origin")).toHaveText("gelesen");
  // gelesenes Gerät: Port nur aus der Liste (keine freie Eingabe), ohne Port-Channel
  const portA = dialog.locator("#cl-a-port");
  await expect(portA).toHaveJSProperty("tagName", "SELECT");
  await expect(portA.locator("option[value='Po1']")).toHaveCount(0);
  await portA.selectOption("Gi1/0/47");
  // Gegenstelle aus CDP vorgeschlagen und eingetragen
  await expect(dialog.locator("#cl-b-device")).toHaveValue("sw-core-01");
  await expect(dialog.locator("#cl-b-port")).toHaveValue("Eth1/1");
  await expect(dialog.locator(".cl-peer[data-device='sw-core-01']")).toContainText("CDP");
  // Ende B ändern: kein gelesenes Gerät (Patchpanel) – frei, gekennzeichnet
  await dialog.locator("#cl-b-device").fill("pp-e2e-01");
  await expect(dialog.locator("#cl-b-devices")).toContainText("kein gelesenes Gerät");
  await expect(dialog.locator("[data-end='b'] .cl-free")).toBeVisible();
  const portB = dialog.locator("#cl-b-port");
  await expect(portB).toHaveJSProperty("tagName", "INPUT");
  await portB.fill("12");
  // Vorschlag wieder übernehmen und zurück zum Patchpanel
  await dialog.locator(".cl-peer[data-device='sw-core-01']").click();
  await expect(dialog.locator("#cl-b-device")).toHaveValue("sw-core-01");
  await dialog.locator("#cl-b-device").fill("pp-e2e-01");
  await portB.fill("12");
  await dialog.locator("#cl-label").fill("K-E2E-PP");
  await page.locator("#cable-label-save").click();
  await expect(dialog).toHaveCount(0);
  // Liste: das frei erfasste Ende ist gekennzeichnet
  const row = page.locator("#cabling-labels tr[data-pair='pp-e2e-01|12~sw-acc-01|gi1/0/47']");
  await expect(row).toContainText("K-E2E-PP");
  await expect(row.locator(".cl-free")).toHaveCount(1);
  // erneut erfassen: das Patchpanel steht jetzt in der Auswahl (frei erfasst), mit seiner Gegenstelle
  await page.locator("#cabling-labels-new").click();
  await dialog.locator("#cl-a-device").fill("pp-e2e");
  const pp = dialog.locator("#cl-a-devices .cl-device-option[data-name='pp-e2e-01']");
  await expect(pp).toContainText("frei erfasst");
  await pp.click();
  await dialog.locator("#cl-a-port").fill("12");
  await expect(dialog.locator(".cl-peer[data-device='sw-acc-01']")).toContainText("K-E2E-PP");
  await expect(dialog.locator("#cl-b-device")).toHaveValue("sw-acc-01");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  // aufräumen
  await row.locator(".cable-label-edit").click();
  await page.locator("#cable-label-delete").click();
  await expect(row).toHaveCount(0);
});

test("Kabelnummer am Port ohne Gegenstelle: Ende A fest, Ende B wählen (1.23.0)", async ({ page }) => {
  await page.goto("/#verkabelung");
  await page.locator(".tabbar").getByRole("button", { name: "Ports", exact: true }).click();
  const row = page.locator("#cabling-ports tr[data-port='Gi1/0/9']");
  await row.locator(".cable-label-edit").click();
  const dialog = page.locator("#cable-label-dialog");
  await expect(dialog.locator("#cl-a-device")).toHaveValue("sw-acc-01");
  await expect(dialog.locator("#cl-a-port")).toHaveAttribute("readonly", "");
  await expect(dialog.locator("#cl-b-device")).not.toHaveAttribute("readonly", "");
  await dialog.locator("#cl-b-device").fill("core");
  await dialog.locator("#cl-b-devices .cl-device-option[data-name='sw-core-01']").click();
  await expect(dialog.locator("#cl-b-port")).toHaveJSProperty("tagName", "SELECT");
  await dialog.locator("#cl-b-port").selectOption("Eth1/5");
  await expect(page.locator("#cable-label-save")).toBeDisabled();
  await dialog.locator("#cl-label").fill("K-E2E-9");
  await expect(page.locator("#cable-label-save")).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});
