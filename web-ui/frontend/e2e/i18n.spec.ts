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

// 0.95.0: Detail einer EPG in der Applikations-Doku und Einstellung „Namen der Gegenstellen“
test("Englisch: EPG-Detail der Applikation und Namen der Gegenstellen am Mandanten", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#tenants");
  await expect(page.locator("#ts-foreign-names")).toContainText("Application documentation: show the names of peers");
  await page.goto("/#apps");
  await page.locator("tbody tr", { hasText: "Webshop" }).click();
  await page.locator("#app-epgs tbody tr", { hasText: "TE/AP/EG_db" }).click();
  await expect(page.locator(".drawer .sub")).toHaveText("EPG of the application");
  await expect(page.locator("#app-epg-rules")).toHaveText("Show rules of this EPG");
  await expect(page.locator("#app-epg-objects")).toHaveText("Open in Objects & inventory");
});

// 0.96.0: Kette VIP der Firewall → Load Balancer → Server in NAT / VIPs auf Englisch
test("Englisch: NAT-Kette über den Load Balancer", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#nat");
  await page.locator(".filterbar .search input").fill("203.0.113.30");
  const front = page.locator("tr[data-nat='V-EXT_lb.example.ch']");
  await expect(front.locator(".nat-via")).toContainText("via vs-shop");
  await expect(page.locator("tr[data-nat='vs-shop'] .nat-front")).toContainText("in front 203.0.113.30:443");
  await expect(page.locator("thead")).toContainText("Destination servers");
  await front.click();
  await expect(page.locator(".drawer")).toContainText("Chain via the load balancer (1)");
});

// 0.102.0: Kategorien der Applikations-Doku im Netzwerkdesign
test("Englisch: Kategorien der Applikations-Doku im Netzwerkdesign", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#design?tab=appdoc");
  await expect(page.locator(".tabbar button.on")).toHaveText("Categories");
  await expect(page.locator("#appdoc-zones h2")).toHaveText("Categories of the application documentation");
  await expect(page.locator("#appdoc-zones thead")).toContainText("without (exclusions)");
  await expect(page.locator("#appdoc-zones thead")).toContainText("Zone type"); // 0.105.0
  await expect(page.locator("#appdoc-zones thead")).toContainText("Exits");
  await expect(page.getByLabel("Category of zone LAN").locator("option", { hasText: "Internet (2/1)" })).toHaveCount(1);
});

// 0.104.0: Kategorien der Applikations-Doku im Namenskonzept
test("Englisch: Kategorien der Applikations-Doku im Namenskonzept", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#dict");
  await expect(page.locator("#appdoc-catalog h2")).toHaveText("Categories of the application documentation");
  await expect(page.locator("#appdoc-catalog-areas thead")).toContainText("Category inbound");
  await expect(page.getByLabel("Title of category 1", { exact: true })).toHaveValue("Internet Zugriff"); // gespeichert deutsch
  await expect(page.locator("#appdoc-catalog-categories tbody tr").nth(6)).toContainText("ACI within the application profile");
  await expect(page.locator("#appdoc-catalog-categories thead")).toContainText("Directions"); // 0.105.0
  await expect(page.locator("#appdoc-catalog-types")).toContainText("Zone types in the base schema: ");
});
