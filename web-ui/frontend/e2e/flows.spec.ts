// Kernabläufe aus den Dev-Befunden: Suche, NAT-Verlinkung, Findings, Dienste, Standorte.
import { expect, rows, test } from "./fixtures";

const search = (page: import("@playwright/test").Page) => page.locator(".filterbar .search input");

test("Regelsuche findet Gruppenmitglieder und IPAM-Namen", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("N_server-net"); // nur über zwei Gruppen in der Regel
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("dmz-to-web");
  await search(page).fill("web01.example.ch"); // DNS-Name aus der DDI (ohne Präfix)
  await expect(rows(page).filter({ hasText: "internet-to-www" })).toHaveCount(1);
});

test("SNAT-Pool: Regel → NAT-Detail → zurück zu den Regeln", async ({ page }) => {
  await page.goto("/#regeln");
  await page.click("button.linkchip:has-text('IPP_inet01')");
  await expect(page).toHaveURL(/#nat\?q=IPP_inet01/);
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("198.51.100.10-198.51.100.12");
  await drawer.locator("button:has-text('In Regeln anzeigen')").click();
  await expect(page).toHaveURL(/#regeln\?nat=IPP_inet01/);
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("web-to-internet");
});

test("VIP-Detail zeigt DNS-Namen beider Seiten und den Server", async ({ page }) => {
  await page.goto("/#nat");
  await search(page).fill("web01.example.ch");
  await expect(rows(page)).toHaveCount(1);
  await rows(page).first().click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("www.example.ch");
  await expect(drawer).toContainText("web01.example.ch");
  await expect(drawer).toContainText("H_web01");
});

test("Findings: Übersicht, Satz mit Befund, Link auf die Regel", async ({ page }) => {
  await page.goto("/#findings");
  await expect(page.locator(".fgroup")).toHaveCount(4);
  await page.click(".fline:has-text('Nicht im IPAM')");
  const row = rows(page).filter({ hasText: "172.16.5.9" });
  await expect(row).toContainText("Den Server db01.example.ch kennt das IPAM unter 10.30.0.9");
  await row.click();
  await page.click("tr.subrow button.linkchip:has-text('H_db01')");
  await expect(page).toHaveURL(/#regeln\?object=H_db01/);
  await expect(rows(page).first()).toContainText("to-db");
});

test("Regel nicht wirksam: Befund öffnet den Contract", async ({ page }) => {
  await page.goto("/#findings");
  await page.click(".fline:has-text('Regel nicht wirksam')");
  const row = rows(page).filter({ hasText: "CT_db:SJ_1" });
  await expect(row).toContainText("wird von keiner EPG konsumiert");
  await row.click();
  await page.click("tr.subrow button.linkchip");
  await expect(page.locator(".drawer")).toContainText("kein Consumer · nicht wirksam");
});

test("Dienste: Attribute, Prüfung und Detail mit Regeln", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Services')");
  const svc = rows(page).filter({ hasText: "FT_tcp8443" });
  await expect(svc).toContainText("Quellport 1024-65535");
  await expect(svc).toContainText("Service ≠ Name");
  await rows(page).filter({ hasText: "TE/FT_https" }).locator("td").first().click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("Filter-Einträge (1)");
  await expect(drawer).toContainText("CT_db:SJ_1");
  await drawer.locator("button:has-text('In Regeln anzeigen')").click();
  await expect(page).toHaveURL(/#regeln\?object=TE%2FFT_https|#regeln\?object=TE\/FT_https/);
  await expect(rows(page)).toHaveCount(1);
});

test("Regeln: Internet Service und negiertes Ziel sind sichtbar", async ({ page }) => {
  await page.goto("/#regeln");
  const o365 = rows(page).filter({ hasText: "o365" });
  await expect(o365).toContainText("Microsoft-Office365");
  await expect(o365).toContainText("nicht (negiert)");
});

test("Standorte: virtuell/physisch mit Zuordnung, Vorschlag aus Gerätenamen", async ({ page }) => {
  await page.goto("/#tenants");
  const card = page.locator(".card:has(h2:text('Standorte'))");
  await expect(card).toContainText("Virtuelle Standorte (2)");
  await expect(card.locator(".site-card", { hasText: "Stretched DC" })).toContainText("läuft an: chzrh1, chbsl1");
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Geräte')");
  await expect(rows(page).filter({ hasText: "evh01-chzrh1-infrz" })).toContainText("chzrh1");
});

test("Alle einlesen legt je Quellsystem einen Lauf an", async ({ page }) => {
  await page.goto("/#uebersicht");
  await page.click("button:has-text('Alle einlesen')");
  const modal = page.locator(".modal");
  await expect(modal).toContainText("ddi-01");
  await modal.locator("button.btn-primary").click();
  await expect(modal).toContainText("Läufe angelegt");
});

test("IPv4/IPv6: Regelpaar markiert, Gegenstück öffnet sich", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("web-in");
  const v4 = rows(page).filter({ has: page.locator("b", { hasText: /^web-in$/ }) });
  await expect(v4).toContainText("IPv4");
  await v4.locator("button.linkchip:has-text('↔ IPv6')").click();
  const drawer = page.locator(".drawer");
  await expect(drawer.locator(".drawer-head, h2").first()).toContainText("web-in-v6");
  await expect(drawer).toContainText("↔ IPv4");
  await search(page).fill("api-in");
  await expect(rows(page).first()).toContainText("ähnlich");
});

test("IPv4/IPv6: gleichnamige Objekte getrennt mit eigener Regel-Zahl", async ({ page }) => {
  await page.goto("/#objekte");
  await search(page).fill("H_web01");
  const v6 = rows(page).filter({ hasText: "fw-03" }).filter({ hasText: "IPv6" });
  await expect(v6).toHaveCount(1);
  await expect(v6).toContainText("2001:db8:10::5");
  await v6.locator("button.linkchip").last().click();
  await expect(page).toHaveURL(/#regeln\?.*object_id=/);
  await expect(rows(page)).toHaveCount(3);
  await expect(page.locator(".filterbar")).toContainText("Objekt H_web01 (IPv6)");
});

test("Virtual Server: Real Server mit Status und Health-Check", async ({ page }) => {
  await page.goto("/#nat");
  await search(page).fill("10.10.30.12"); // zweiter Real Server
  const row = rows(page).filter({ hasText: "V-EXT_shop.example.ch" });
  await expect(row).toContainText("Virtual Server");
  await expect(row).toContainText("10.10.30.12:8443");
  await row.click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("Real Server (2)");
  await expect(drawer).toContainText("round-robin");
  await expect(drawer).toContainText("HC_https");
  await expect(drawer.locator("tr", { hasText: "10.10.30.12" })).toContainText("Standby");
});

test("Regeln je Subnetz: Link aus den Subnetzen, any ein-/ausblenden", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Subnetze')");
  await rows(page).filter({ hasText: "10.20.0.0/24" }).locator("button:has-text('Regeln im Subnetz')").click();
  await expect(page).toHaveURL(/#regeln\?net=10\.20\.0\.0%2F24/);
  await expect(page.locator(".filterbar")).toContainText("Subnetz 10.20.0.0/24");
  await expect(rows(page).filter({ hasText: "to-web-net" })).toHaveCount(1);
  const withAny = await rows(page).count();
  await page.click("button.toggle:has-text('inkl. any')");
  await expect(rows(page)).not.toHaveCount(withAny);
  await expect(rows(page).filter({ hasText: "to-web-net" })).toHaveCount(1);
});

test("VMware: VM mit Portgruppe → EPG, Regeln der VM", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('VMs')");
  const vm = rows(page).filter({ hasText: "web01-007-ka" });
  await expect(vm).toContainText("EPG TE/AP/EG_db");
  await expect(vm).toContainText("10.10.10.5");
  await expect(vm).toContainText("web01.example.ch"); // DNS-Name aus der DDI
  await page.click(".tabbar button:has-text('Portgruppen')");
  await expect(rows(page).filter({ hasText: "TE|AP|EG_db" })).toContainText("TE/AP/EG_db");
  await page.click(".tabbar button:has-text('VMs')");
  await vm.locator("button:has-text('Regeln der VM')").click();
  await expect(page).toHaveURL(/#regeln\?q=web01-007-ka/);
  await expect(rows(page).filter({ hasText: "dmz-to-web" })).toHaveCount(1);
});

test("Alle einlesen: offenen Lauf abbrechen und neu starten", async ({ page }) => {
  await page.goto("/#uebersicht");
  const modal = page.locator(".modal");
  for (const round of [1, 2]) {
    await page.click("button:has-text('Alle einlesen')");
    await modal.locator("button.btn-primary").click();
    await expect(modal).toContainText("Läufe angelegt");
    if (round === 1) await modal.locator("button:has-text('Schliessen')").click();
  }
  const row = modal.locator("tr", { hasText: "fw-02" }); // zentraler Worker, Lauf bleibt offen
  await expect(row).toContainText("übersprungen: Lauf wartet seit");
  await expect(row).toContainText("auf den zentralen Worker");
  await row.locator("button:has-text('Abbrechen & neu starten')").click();
  await expect(row).toContainText("läuft");
  await expect(row).not.toContainText("übersprungen");
});

test("Ports je Gerät: Subinterface getaggt unter dem Parent", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Ports')");
  await expect(page.locator("tr.group-row", { hasText: "fw-03" })).toContainText("3 Ports");
  const sub = rows(page).filter({ hasText: "srv-vl10" });
  await expect(sub).toContainText("└");
  await expect(sub).toContainText("getaggt über internal");
  await expect(sub).not.toContainText("untagged");
});

test("Ports kompakt: lange VLAN-/EPG-Listen gekürzt, alles im Detail", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Ports')");
  const trunk = rows(page).filter({ hasText: "eth1/1" });
  await expect(trunk).toContainText("tagged 24 VLANs");
  await expect(trunk).toContainText("3 EPGs");
  await expect(trunk).not.toContainText("EG_web");
  await trunk.click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("leaf-101 · eth1/1");
  await expect(drawer).toContainText("100-120, 300, 305, 310");
  await expect(drawer).toContainText("TE/AP/EG_web");
  await expect(drawer).toContainText("esx01-chzrh1 : vmnic0");
  await drawer.locator("button.close").click();
  // Parent → Subinterface im Detail
  await rows(page).filter({ hasText: "internal" }).first().click();
  await expect(drawer).toContainText("Subinterfaces");
  await drawer.locator("button:has-text('srv-vl10')").click();
  await expect(drawer).toContainText("fw-03 · srv-vl10");
  await expect(drawer).toContainText("Subinterface – sendet getaggt über internal");
});

test("Portgruppe im Detail: EPG, Subnetz aus dem IPAM, VM mit IP und DNS", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Portgruppen')");
  await rows(page).filter({ hasText: "TE|AP|EG_db" }).click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("TE/AP/EG_db");
  await expect(drawer).toContainText("10.10.10.0/24");
  await expect(drawer).toContainText("IPAM ddi-01");
  const vm = drawer.locator("tr", { hasText: "web01-007-ka" });
  await expect(vm).toContainText("10.10.10.5");
  await expect(vm).toContainText("web01.example.ch");
  await drawer.locator("button:has-text('Regeln im Subnetz')").click();
  await expect(page).toHaveURL(/#regeln\?net=10\.10\.10\.0%2F24/);
});

test("Läufe: alle Läufe mit Gesamtzahl, Filter Quellsystem und Status", async ({ page }) => {
  const count = page.locator(".filterbar .count");
  await page.goto("/#laeufe");
  await expect(count).toHaveText(/^\d+ Läufe$/);
  expect(Number((await count.innerText()).split(" ")[0])).toBeGreaterThanOrEqual(7); // je Quellsystem ein Lauf
  await page.locator(".filterbar select").nth(1).selectOption({ label: "vc-01" });
  await expect(rows(page).first()).toContainText("vc-01");
  await expect(rows(page).filter({ hasNotText: "vc-01" })).toHaveCount(0);
  await page.locator(".filterbar select").nth(1).selectOption({ label: "Quellsystem: alle" });
  await page.locator(".filterbar select").first().selectOption("completed");
  await expect(rows(page).first()).toContainText("OK");
  await expect(rows(page).filter({ hasNotText: "OK" })).toHaveCount(0);
  // Übersicht verlinkt auf alle Läufe
  await page.goto("/#uebersicht");
  await page.click("button:has-text('Alle Läufe')");
  await expect(page).toHaveURL(/#laeufe/);
  await expect(page.locator("h1")).toHaveText("Läufe");
});

test("IPv4/IPv6: ähnliches Paar bestätigen und zurücknehmen", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("api-in");
  // IPv4-Policy: Familien-Chip genau "IPv4" (die IPv6-Zeile hat nur den Paar-Chip "↔ IPv4 …")
  const v4 = () => rows(page).filter({ has: page.locator("span.chip", { hasText: /^IPv4$/ }) });
  await expect(v4()).toContainText("ähnlich");
  await v4().click();
  const drawer = page.locator(".drawer");
  await drawer.locator("button:has-text('Als Paar bestätigen')").click();
  await expect(drawer).toContainText("bestätigt von");
  await expect(v4()).toContainText("✓ bestätigt");
  await drawer.locator("button:has-text('zurücknehmen')").click();
  await expect(drawer).not.toContainText("bestätigt von");
  await expect(v4()).toContainText("ähnlich");
});

test("VIP-Ziel: Server hinter der VIP trägt „Ziel von VIP“", async ({ page }) => {
  await page.goto("/#objekte");
  await search(page).fill("H_web01");
  const row = rows(page).filter({ hasText: "fwc11-000-infrz" }).first();
  await expect(row).toContainText("Ziel von VIP V-EXT_www.example.ch");
});

test("Betriebszustand auf der System-Seite", async ({ page }) => {
  await page.goto("/#system");
  const card = page.locator(".card", { hasText: "Betriebszustand" });
  await expect(card).toContainText("Plattform");
  await expect(card).toContainText("Quellsysteme");
});

test("NAT: Korrektheitsprüfung, nur Abweichungen, Objekt öffnen", async ({ page }) => {
  await page.goto("/#nat");
  const shop = rows(page).filter({ hasText: "V-EXT_shop.example.ch" });
  await expect(shop).toContainText("Ziel nicht im IPAM");
  const all = await rows(page).count();
  await page.click("button.toggle:has-text('nur Abweichungen')");
  await expect(rows(page).filter({ hasText: "V-EXT_www.example.ch" })).toHaveCount(0);
  expect(await rows(page).count()).toBeLessThan(all);
  await shop.click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("Ziel nicht im IPAM");
  await drawer.locator("button:has-text('Objekt öffnen')").click();
  await expect(page).toHaveURL(/#objekte\?object_id=\d+/);
  await expect(page.locator(".drawer h2")).toHaveText("V-EXT_shop.example.ch");
});

test("Regel-Detail zeigt die VM mit allen Angaben aus dem vCenter", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("web01-007-ka");
  await rows(page).filter({ hasText: "dmz-to-web" }).click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("VMs (1)");
  await expect(drawer).toContainText("Ubuntu Linux (64-bit)");
  await expect(drawer).toContainText("esx01-chzrh1.example.ch");
  await expect(drawer).toContainText("4 GB RAM");
});

test("VDOMs: Filter, Kennzeichnung und Detail je VDOM", async ({ page }) => {
  await page.goto("/#regeln");
  await page.locator(".filterbar select").filter({ hasText: "VDOM: alle" }).selectOption("kunde-a");
  await expect(rows(page)).toHaveCount(2);
  await expect(rows(page).first()).toContainText("VDOM kunde-a");
  await rows(page).filter({ hasNotText: "web-v6" }).click(); // die IPv4-Regel "web"
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("VDOM kunde-a");
  await expect(drawer).toContainText("203.0.113.50"); // VIP_web dieses VDOMs, nicht der aus root
  await expect(drawer).not.toContainText("203.0.113.1/");
  await page.goto("/#objekte");
  await search(page).fill("N_clients");
  const nets = rows(page).filter({ hasText: "fw-05" }).filter({ hasText: "IPv4" });
  await expect(nets).toHaveCount(2); // gleicher Name in zwei VDOMs = zwei Objekte
  await expect(nets.filter({ hasText: "VDOM root" })).toContainText("10.1.0.0/24");
  await expect(nets.filter({ hasText: "VDOM kunde-a" })).toContainText("10.50.0.0/24");
});
