// Sprache der Oberfläche (0.86.0): Englisch im Benutzermenü wählen, im Browser gemerkt; jede Ansicht
// lädt auf Englisch ohne Fehler, Titel = Eintrag in der Navigation.
import { expect, test } from "./fixtures";

const VIEWS: [string, string][] = [
  ["uebersicht", "Overview"],
  ["laeufe", "Runs"],
  ["regeln", "Rules & contracts"],
  ["objekte", "Objects & inventory"],
  ["nat", "NAT / VIPs"],
  ["wege", "Path search"],
  ["routing", "Routing"],
  ["verkabelung", "Cabling"],
  ["apps", "Applications"],
  ["findings", "Findings"],
  ["abgleich", "Reconciliation"],
  ["design", "Network design"],
  ["users", "Users & roles"],
  ["tenants", "Tenants & sites"],
  ["kunden", "Customers"],
  ["dict", "Naming profiles"],
  ["agents", "Collector agents"],
  ["netbox", "NetBox-Sync"],
  ["system", "System"],
];

test("Sprache: Englisch im Benutzermenü wählen, gemerkt, zurück auf Deutsch", async ({ page }) => {
  await page.goto("/#uebersicht");
  await expect(page.locator(".nav-sec").first()).toHaveText("Dokumentation");
  await page.click(".usermenu-btn");
  await page.click("#lang-switch button:has-text('English')");
  await expect(page.locator(".nav-sec").first()).toHaveText("Documentation");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.reload();
  await expect(page.locator(".nav-item.active")).toContainText("Overview");
  await page.click(".usermenu-btn");
  await page.click("#lang-switch button:has-text('Deutsch')");
  await expect(page.locator(".nav-sec").first()).toHaveText("Dokumentation");
  await expect(page.locator("html")).toHaveAttribute("lang", "de");
});

for (const [key, title] of VIEWS) {
  test(`Englisch: ${title}`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
    await page.goto(`/#${key}`);
    await expect(page.locator(".nav-item.active")).toContainText(title);
    await expect(page.locator(".page-head h1, h1").first()).toContainText(title);
    await page.waitForLoadState("networkidle");
  });
}

// 0.87.0: Texte vom Server folgen der Sprache (Accept-Language), die Logik bleibt gleich
test("Englisch: Befundtext vom Server", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#findings");
  await page.click(".fline:has-text('Not in IPAM')");
  const row = page.locator("tbody tr").filter({ hasText: "172.16.5.9" });
  await expect(row).toContainText("The IPAM knows the server db01.example.ch as 10.30.0.9");
});

test("Englisch: Sprache der Datei im Export-Dialog gemerkt", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#design");
  await page.click(".tabbar button:has-text('Zone plan')");
  const select = page.locator(".plan-export .export-lang-select");
  await expect(select).toHaveValue("en");
  await select.selectOption("de");
  await page.reload();
  await page.click(".tabbar button:has-text('Zone plan')");
  await expect(page.locator(".plan-export .export-lang-select")).toHaveValue("de");
});
