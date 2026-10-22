// Applikationen (0.14.0): Klick öffnet die Doku – Übersicht, VMs, EPGs, Regeln mit Filtern und
// Erklärung im Detail; Stammdaten mit externem/internem Namen und Verwendungszweck.
import { expect, rows, test } from "./fixtures";

const open = async (page: import("@playwright/test").Page) => {
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Webshop" }).click();
  await expect(page).toHaveURL(/#apps\?app=\d+/);
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
};

test("Applikation: Doku mit Übersicht, VMs und EPGs mit ganzem ACI-Pfad", async ({ page }) => {
  await open(page);
  await expect(page.locator(".app-names")).toContainText("Shop Kunde A");
  await expect(page.locator(".app-head")).toContainText("Prod");
  const kpi = (label: string) => page.locator(".kpi", { hasText: label });
  await expect(kpi("VMs").locator(".k-val")).toHaveText("1");
  await expect(kpi("Regeln spezifisch").locator(".k-val")).toHaveText(/^\d+$/);
  await expect(kpi("Regeln unspezifisch")).toContainText("any");
  const vm = page.locator(".card", { hasText: "Systeme" }).locator("tbody tr", { hasText: "web01-007-ka" });
  await expect(vm).toContainText("TE/AP/EG_db");
  await expect(vm).toContainText("10.10.10.5");
  await expect(vm).toContainText("Portgruppe TE|AP|EG_db → EPG TE/AP/EG_db (AP TE/AP)");
  const epg = page.locator(".card", { hasText: "EPGs im ACI" }).locator("tbody tr");
  await expect(epg).toContainText("TE/AP/EG_db");
  // Firewall-Objekte je Name zusammengefasst, mit den Firewalls und der Namensprüfung
  const web01 = vm.locator(".objgroup", { hasText: "H_web01" });
  await expect(web01).toHaveCount(1);
  await expect(web01).toContainText("fwc11-000-infrz");
  await expect(web01).toContainText("fw-03");
  await expect(web01).not.toContainText("Name ≠ DNS");
  await expect(vm.locator(".objgroup", { hasText: "H_web03" })).toContainText("Name ≠ DNS");
  await web01.locator("button:has-text('fw-03')").click();
  await expect(page).toHaveURL(/#objekte\?object_id=\d+/);
  await expect(page.locator(".drawer h2")).toHaveText("H_web01");
});

test("Applikation: Regeln filtern (System, Richtung, Bezug) und Erklärung im Detail", async ({ page }) => {
  await open(page);
  const card = page.locator("#app-rules");
  const ruleRows = card.locator("tbody tr");
  const total = await ruleRows.count();
  expect(total).toBeGreaterThan(3);
  // ACI-Contract mit Herkunft und Aktion wie in den Regeln
  const contract = ruleRows.filter({ hasText: "CT_db" });
  await expect(contract).toContainText("ACI");
  await expect(contract).toContainText("allow");
  await expect(contract).toContainText("TE/AP/EG_db");
  // Richtung aus Sicht der VM, Bezug unspezifisch
  await card.locator("select").nth(0).selectOption({ label: "VM web01-007-ka" });
  await card.locator("select").nth(2).selectOption("out");
  await expect(ruleRows.filter({ hasText: "web-to-internet" })).toHaveCount(1);
  await expect(ruleRows.filter({ hasText: "dmz-to-web" })).toHaveCount(0);
  await expect(card).toContainText("Richtung aus Sicht von web01-007-ka");
  await card.locator("button:has-text('Filter zurücksetzen')").click();
  await card.locator("select").nth(4).selectOption("unspecific");
  await expect(ruleRows.first()).toContainText("any");
  expect(await ruleRows.count()).toBeLessThan(total);
  await card.locator("button:has-text('Filter zurücksetzen')").click();
  // Objekt der Regel (any bzw. Netz) mit den VMs darin statt einer Zeile je VM
  const viaAny = ruleRows.filter({ hasText: "web-via-group" });
  await expect(viaAny).toHaveCount(1);
  await expect(viaAny.locator(".appside")).toContainText("any");
  await expect(viaAny.locator(".appside")).toContainText("↳ web01-007-ka");
  // Detail: warum die Regel gilt, Link in die Regel-Ansicht
  await contract.click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("Warum diese Regel für die Applikation gilt");
  await expect(drawer).toContainText("gehört zur Applikation über AP TE/AP");
  await expect(drawer).toContainText("Provider");
  await drawer.locator("button:has-text('In Regeln & Contracts')").click();
  await expect(page).toHaveURL(/#regeln\?rule=\d+/);
});

test("Applikation: Stammdaten bearbeiten (Zweck, Namen)", async ({ page }) => {
  await open(page);
  await page.click("button:has-text('Bearbeiten')");
  const modal = page.locator(".modal");
  await modal.locator("#ap-purpose").selectOption("test");
  await modal.locator("#ap-int").fill("webshop-test");
  await modal.locator("button.btn-primary").click();
  await expect(page.locator(".app-head")).toContainText("Test");
  await expect(page.locator(".app-names")).toContainText("webshop-test");
  // zurück: Zweck in der Liste
  await page.click("button:has-text('← Applikationen')");
  await expect(rows(page).filter({ hasText: "Webshop" })).toContainText("Test");
  // aufräumen für andere Tests
  await rows(page).filter({ hasText: "Webshop" }).click();
  await page.click("button:has-text('Bearbeiten')");
  await modal.locator("#ap-purpose").selectOption("prod");
  await modal.locator("#ap-int").fill("webshop-ka");
  await modal.locator("button.btn-primary").click();
  await expect(page.locator(".app-head")).toContainText("Prod");
});

test("Doku je System: routingtechnisch unmögliche Regeln stehen nicht in den Kategorien", async ({ page }) => {
  await page.goto("/#apps");
  await page.click("button:has-text('Doku je System')");
  const pick = page.locator(".filterbar select").first();
  await expect(page.locator(".stack")).toBeVisible(); // erstes System geladen
  await expect(pick).toContainText("10.30.0.10");
  await pick.selectOption("10.30.0.10");
  const gone = page.locator("#doc-impossible");
  await expect(gone).toContainText("Routingtechnisch nicht möglich");
  await gone.locator("summary").click();
  await expect(gone).toContainText("lan-to-inet-users");
  await expect(gone).toContainText("fw-08 leitet 10.30.0.10 über dmz (Zone dmz) – die Regel gilt nur für den Ausgang internet");
  await expect(page.locator(".stack")).toContainText("lan-to-dmz-web");
  await expect(page.locator(".stack")).not.toContainText("lan-to-inet-users");
  // Gegenstelle any: was hinter dem Interface der Regel liegt (0.25.0)
  await expect(page.locator(".stack")).toContainText("all → Netze hinter lan: 10.10.0.0/24");
  await expect(page.locator(".stack")).toContainText("all → extern (Default-Route über internet)");
});
