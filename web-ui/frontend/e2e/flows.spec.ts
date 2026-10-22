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

test("Contract: Subnetz einer externen EPG nur für das Routing", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("CT_rt");
  await rows(page).filter({ hasText: "L3O_fw/EN_rt" }).click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("EN_rt");
  await expect(drawer.locator(".chip", { hasText: "nur Routing" })).toBeVisible();
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
  const group = page.locator("tr.group-row", { hasText: "fw-03" });
  await expect(group).toContainText("3 Ports");
  await group.click(); // mehr als drei Geräte: Gruppen zu, ein Klick öffnet
  const sub = rows(page).filter({ hasText: "srv-vl10" });
  await expect(sub).toContainText("└");
  await expect(sub).toContainText("getaggt über internal");
  await expect(sub).not.toContainText("untagged");
});

test("Ports kompakt: lange VLAN-/EPG-Listen gekürzt, alles im Detail", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Ports')");
  for (const device of ["leaf-101", "fw-03"]) await page.locator("tr.group-row", { hasText: device }).click();
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
  // FortiGate nur mit root: die Firewall, ohne „VDOM root“ (0.17.0)
  await search(page).fill("to-web-net");
  await expect(rows(page).first()).toContainText("fw-04");
  await expect(rows(page).first()).not.toContainText("VDOM");
  await search(page).fill("");
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

test("Routing: Weg für eine IP, Backup-Pfade, HA und L3Out", async ({ page }) => {
  await page.goto("/#routing");
  const ctx = page.locator(".card", { hasText: "Gelesene Kontexte" });
  await expect(ctx).toContainText("leaf-101 · VRF TE:VRF1");
  await expect(ctx).toContainText("fw-05 · VDOM root");
  await expect(ctx.locator("tbody tr").filter({ hasText: "fw-04" })).toContainText("Firewall"); // nur root
  await expect(ctx).toContainText("BGP-Pfade IPv4 (HTTP 403)"); // nicht lesbar -> unbekannt
  // nur Ziel: je Kontext die wirksame Route
  const dst = page.getByPlaceholder("Ziel (IP)");
  await dst.fill("10.20.0.5");
  await page.click("button:has-text('Weg suchen')");
  const way = page.locator(".card", { hasText: "Geht vor" }).locator("tbody tr");
  const root = way.filter({ hasText: "fw-05 · VDOM root" });
  await expect(root).toContainText("10.20.0.0/24");
  await expect(root).toContainText("Policy-Route #5");
  await expect(way.filter({ hasText: "leaf-101" })).toContainText("ECMP ×2");
  // mit Quelle: Weg Hop für Hop, ECMP über zwei Provider, Namen aus dem IPAM
  await page.getByPlaceholder("Quelle (IP)").fill("10.20.0.5");
  await dst.fill("203.0.113.10");
  await page.click("button:has-text('Weg suchen')");
  const first = page.locator(".card[id^='trace-']", { hasText: "Weg 1 von 2" });
  await expect(first).toContainText("extern");
  await expect(first).toContainText("isp-a-gw.example.ch");
  await expect(first).toContainText("fw-05 · VDOM root");
  // redundante Wege: ECMP aktiv, dazu der BGP-Backup-Pfad gleicher Länge
  const overview = page.locator(".card", { hasText: "Wege im Überblick" });
  await expect(overview).toContainText("aktiv · ECMP");
  await expect(overview).toContainText("Backup ab fw-05 · VDOM root");
  await expect(page.locator(".card[id^='trace-']", { hasText: "Backup-Weg 1" })).toContainText("198.51.100.9");
  // Startpunkt gewählt: Firewall nur mit root
  await page.getByPlaceholder("Quelle (IP)").fill("");
  await page.locator(".filterbar select").filter({ hasText: "Start: aus der Quelle" }).selectOption({ label: "fw-04" });
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator(".card", { hasText: "Weg 1" })).toContainText("keine Route zum Ziel"); // kein Default
  await dst.fill("10.20.0.5");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator(".card", { hasText: "Weg 1" })).toContainText("Next-Hop 10.70.0.1 gehört keinem");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  await expect(page.locator(".card", { hasText: "Gelesen als" })).toContainText("isp-a-gw.example.ch");
  await page.click(".tabbar button:has-text('Routen')");
  await page.locator(".filterbar select").filter({ hasText: "Tabelle: alle" }).selectOption("bgp");
  const routes = page.locator(".card", { hasText: "Weitere Angaben" }).locator("tbody tr");
  await expect(routes).toHaveCount(2);
  await expect(routes.filter({ hasText: "Backup" })).toContainText("64500 64501");
  await page.locator(".filterbar select").filter({ hasText: "Kontext: alle" }).selectOption({ label: "fw-04" });
  await page.locator(".filterbar select").filter({ hasText: "Tabelle: alle" }).selectOption("");
  await expect(routes).toHaveCount(3);
  await page.click(".tabbar button:has-text('HA & Session-Sync')");
  await expect(page.locator(".card", { hasText: "Session-Sync (FGSP)" })).toContainText("cluster-a");
  await page.click(".tabbar button:has-text('L3Outs (ACI)')");
  await expect(page.locator(".card", { hasText: "Border-Leafs" })).toContainText("L3O_inet");
});

test("Parallele Firewalls: Vorschlag zusammenfassen, Abweichungen als Findings", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("par-");
  await expect(rows(page)).toHaveCount(9); // noch doppelt
  const hint = page.locator(".note", { hasText: "fw-06 und fw-07" });
  await expect(hint).toContainText("Session-Sync (FGSP)");
  await hint.locator("button", { hasText: "zusammenfassen" }).click();
  await expect(rows(page)).toHaveCount(6); // gleiche Policies einmal, abweichende beide
  await expect(rows(page).filter({ hasText: "par-web" })).toContainText("+ fw-07");
  await expect(rows(page).filter({ hasText: "par-db" }).filter({ hasText: "≠ fw-06: Ziel" })).toHaveCount(1);
  await expect(rows(page).filter({ hasText: "par-nur-06" })).toContainText("fehlt auf fw-07");
  await rows(page).filter({ hasText: "par-web" }).click();
  await expect(page.locator(".drawer")).toContainText("gleich auf:");
  await page.goto("/#findings");
  await expect(page.locator(".fgroup", { hasText: "Parallele Firewalls" })).toBeVisible();
});

test("Routing: Anbindung aus einem Weg erfassen", async ({ page }) => {
  await page.goto("/#routing");
  await page.locator(".filterbar select").filter({ hasText: "Start: aus der Quelle" }).selectOption({ label: "fw-04" });
  await page.getByPlaceholder("Ziel (IP)").fill("10.20.0.5");
  await page.click("button:has-text('Weg suchen')");
  const card = page.locator(".card", { hasText: "Weg 1" });
  await expect(card).toContainText("Weg endet");
  await card.locator("button", { hasText: "Als Anbindung erfassen" }).click();
  await expect(page.locator("#ex-hops")).toHaveValue("10.70.0.1");
  await page.locator("#ex-name").fill("MPLS Kunde A");
  await page.locator("#ex-kind").selectOption("mpls");
  await page.click("button:has-text('Speichern')");
  await expect(page.locator(".card", { hasText: "Manuelle Routen" }).locator("tbody tr")).toContainText("MPLS Kunde A");
  await page.click(".tabbar button:has-text('Weg für IP')");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator(".card", { hasText: "Weg 1" })).toContainText("weiter über MPLS Kunde A");
});

test("Parallele Firewalls: Verbund auflösen, Vorschlag abgelehnt und wieder vorschlagen", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await page.goto("/#regeln");
  await search(page).fill("par-");
  await expect(rows(page)).toHaveCount(6); // aus dem Test davor zusammengefasst
  await rows(page).filter({ hasText: "par-web" }).click();
  await page.locator(".drawer button", { hasText: "Verbund auflösen" }).click();
  await page.locator(".drawer button.close").click();
  await page.reload();
  await search(page).fill("par-");
  await expect(rows(page)).toHaveCount(9); // wieder einzeln
  await expect(page.locator(".note", { hasText: "fw-06 und fw-07" })).toHaveCount(0); // abgelehnt
  await page.click("button:has-text('1 abgelehnter Vorschlag')");
  await page.click("button:has-text('wieder vorschlagen')");
  await expect(page.locator(".note", { hasText: "fw-06 und fw-07" })).toContainText("Ablehnen");
});

test("Routing: erkannte Anbindung mit allen Routern übernehmen, Wege gleich lang", async ({ page }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('Anbindungen')");
  // fw-05: zwei Provider (ECMP) und IPv6 über dieselbe Zone – ein Vorschlag mit drei Routern
  const found = page.locator("#exit-suggestions tbody tr", { hasText: "198.51.100.5" });
  await expect(found).toContainText("198.51.100.1");
  await expect(found).toContainText("3 Router · 3 aktiv (ECMP ×2)");
  await expect(found).toContainText("AS 64500");
  // keine Anbindung: ausblenden, auf Wunsch wieder anzeigen
  const cards = page.locator(".card");
  await expect(cards.last()).toHaveAttribute("id", "exit-suggestions"); // zu unterst
  const server = page.locator("#exit-suggestions tbody tr", { hasText: "10.20.0.254" });
  await server.locator("button", { hasText: "ausblenden" }).click();
  await expect(server).toHaveCount(0);
  await page.click("button:has-text('1 ausgeblendeter Vorschlag')");
  await expect(server).toContainText("ausgeblendet");
  await server.locator("button", { hasText: "wieder anzeigen" }).click();
  await expect(server.locator("button", { hasText: "ausblenden" })).toBeVisible();
  // übernehmen springt hoch zum Formular
  await found.locator("button", { hasText: "übernehmen" }).click();
  await expect(page.locator("#exit-form")).toBeInViewport();
  await expect(page.locator("#ex-name")).toBeFocused();
  const hops = page.locator("#ex-hops");
  await expect(hops).toHaveValue("198.51.100.1, 198.51.100.5, 2001:db8::1");
  await expect(page.locator("#ex-fws input:checked")).toHaveCount(1);
  await page.locator("#ex-routers label", { hasText: "2001:db8::1" }).locator("input").uncheck();
  await expect(hops).toHaveValue("198.51.100.1, 198.51.100.5");
  await page.click("button:has-text('alle Router übernehmen')");
  await expect(hops).toHaveValue("198.51.100.1, 198.51.100.5, 2001:db8::1");
  await page.locator("#ex-name").fill("Internet Provider");
  await page.click("button:has-text('Speichern')");
  const row = page.locator(".card", { hasText: "Manuelle Routen" }).locator("tbody tr", { hasText: "Internet Provider" });
  await expect(row).toContainText("3 Router · 3 aktiv (ECMP ×2)");
  await expect(page.locator("#exit-suggestions tbody tr", { hasText: "198.51.100.5" })).toHaveCount(0);
  // Weg: beide Provider enden an der Anbindung, dazu der BGP-Backup – alle gleich lang
  await page.click(".tabbar button:has-text('Weg für IP')");
  await page.getByPlaceholder("Quelle (IP)").fill("10.20.0.5");
  await page.getByPlaceholder("Ziel (IP)").fill("203.0.113.10");
  await page.click("button:has-text('Weg suchen')");
  const first = page.locator(".card[id^='trace-']", { hasText: "Weg 1 von 2" });
  await expect(first).toContainText("weiter über Internet Provider");
  await expect(first).toContainText("Router 1 von 3");
  const overview = page.locator(".card", { hasText: "Wege im Überblick" });
  await expect(overview.locator("tbody tr", { hasText: "gleich lang" })).toHaveCount(3);
});

test("Kunden: Vorschlag übernehmen, Selektoren mit Vorschau, löschen", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await page.goto("/#kunden");
  // Vorschlag je ACI-Tenant: TE mit seinen EPGs
  const sug = page.locator("#customer-suggestions tbody tr").filter({ hasText: "Tenant TE" });
  await sug.locator("button", { hasText: "Übernehmen" }).click();
  const te = page.locator("tbody tr.clickable").filter({ hasText: "ACI-Tenant TE" });
  await expect(te).toContainText("TE");
  await te.click(); // Zugehörigkeit je Art
  await expect(page.locator("tr.subrow")).toContainText("TE/AP/EG_db");
  // neuer Kunde über ein Netz, ein Teil davon ausgeschlossen - Vorschau vor dem Speichern
  await page.click("button:has-text('+ Kunde')");
  const modal = page.locator(".modal");
  await modal.locator("#cu-name").fill("Kunde A");
  await modal.getByLabel("Art").selectOption("prefix");
  await modal.getByLabel("Wert").fill("10.20.0.0/16");
  const preview = modal.locator("#customer-preview");
  await expect(preview.locator("tr", { hasText: "Netze" })).toContainText("10.20.0.0/16");
  await modal.locator("button", { hasText: "+ Selektor" }).click();
  await modal.getByLabel("Art").nth(1).selectOption("prefix");
  await modal.getByLabel("Wert").nth(1).fill("10.20.128.0/17");
  await modal.getByLabel("ausschliessen").nth(1).check();
  await expect(preview.locator("tr", { hasText: "Netze" })).toContainText("10.20.0.0/17");
  await modal.locator("button", { hasText: "Speichern" }).click();
  const a = page.locator("tbody tr.clickable").filter({ hasText: "Kunde A" });
  await expect(a).toContainText("ohne Netz 10.20.128.0/17");
  await a.locator("button", { hasText: "Löschen" }).click();
  await expect(page.locator("tbody tr.clickable").filter({ hasText: "Kunde A" })).toHaveCount(0);
  await expect(page.locator("#customer-unassigned")).toContainText("Keinem Kunden zugeordnet");
});

test("Kunden-Filter: Umschalter zeigt nur den Ausschnitt des Kunden", async ({ page }) => {
  await page.goto("/#regeln");
  await expect(rows(page).first()).toBeVisible();
  await page.getByLabel("Kunde", { exact: true }).selectOption({ label: "TE" }); // aus dem Test davor
  await expect(rows(page)).toHaveCount(2); // die Contracts der EPGs von TE
  await expect(rows(page).filter({ hasText: "CT_rt" })).toHaveCount(1);
  await page.goto("/#findings"); // bleibt gewählt (je Umgebung gemerkt)
  await expect(page.getByLabel("Kunde", { exact: true })).toHaveValue(/\d+/);
  await page.getByLabel("Kunde", { exact: true }).selectOption("");
  await page.goto("/#regeln");
  await expect(rows(page)).not.toHaveCount(2);
});

test("Routing: Verbund als ein Hop mit beiden Firewalls", async ({ page }) => {
  await page.goto("/#routing");
  await page.getByPlaceholder("Quelle (IP)").fill("10.88.0.5");
  await page.getByPlaceholder("Ziel (IP)").fill("10.89.0.7");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator(".card[id^='trace-']")).toHaveCount(1); // ein Weg statt zwei
  const card = page.locator(".card[id^='trace-']").first();
  await expect(card).toContainText("redundant · Verbund Süd");
  await expect(card.locator(".chip", { hasText: "Verbund Süd" }).first()).toBeVisible();
  await expect(card.locator("tbody tr")).toHaveCount(2); // eine Zeile je Firewall
  await expect(card).toContainText("fw-09");
  await expect(card).toContainText("fw-10");
  await expect(card).toContainText("Ziel angeschlossen an fw-09 (srv) bzw. Ziel angeschlossen an fw-10 (srv)");
});

test("Routing: Regeln auf dem Weg je Firewall", async ({ page }) => {
  await page.goto("/#routing");
  await page.getByPlaceholder("Quelle (IP)").fill("10.20.0.5");
  const dst = page.getByPlaceholder("Ziel (IP)");
  await dst.fill("203.0.113.10");
  await page.click("button:has-text('Weg suchen')");
  // Servernetz -> Internet: keine Policy von „server“ nach „internet“
  const first = page.locator(".card[id^='trace-']", { hasText: "Weg 1 von 2" });
  await expect(first).toContainText("Regeln: gesperrt an fw-05 · VDOM root");
  await expect(first).toContainText("implizit gesperrt");
  await expect(page.locator(".card", { hasText: "Wege im Überblick" }).locator("thead")).toContainText("Regeln");
  // Startpunkt und Dienst: die Policy der VIP ist möglich (Eingang, Ausgang, Quelle nicht prüfbar)
  await page.getByPlaceholder("Quelle (IP)").fill("");
  await page.locator(".filterbar select").filter({ hasText: "Start: aus der Quelle" }).selectOption({ label: "fw-05 · VDOM root" });
  await dst.fill("10.1.0.10");
  await page.getByPlaceholder("Dienst, z. B. tcp/443").fill("tcp/443");
  await page.click("button:has-text('Weg suchen')");
  const card = page.locator(".card[id^='trace-']").first();
  await expect(card).toContainText("abhängig");
  await expect(card).toContainText("möglich (Eingang, Ausgang, Quelle)");
  // Ziel ist die VIP: DNAT an fw-05, der Weg geht zur internen IP weiter
  await dst.fill("203.0.113.1");
  await page.click("button:has-text('Weg suchen')");
  await expect(card).toContainText("DNAT VIP_web");
  await expect(card).toContainText("203.0.113.1 → 10.1.0.10");
  await card.locator("a", { hasText: "Policy 1" }).click();
  await expect(page).toHaveURL(/#regeln\?rule=\d+/);
  await expect(page.locator(".drawer")).toContainText("web");
});

test("Regeln: Suche nach einer IP mit Relevanz nach dem Routing", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("10.30.0.10");
  const wrong = rows(page).filter({ hasText: "lan-to-dmz-falsch" });
  await expect(wrong).toContainText("für 10.30.0.10 nicht möglich");
  await expect(rows(page).filter({ hasText: "lan-to-dmz-web" })).not.toContainText("nicht möglich");
  await page.click("button:has-text('routingtechnisch unmögliche ausblenden')");
  await expect(wrong).toHaveCount(0);
  await expect(rows(page).filter({ hasText: "lan-to-dmz-web" })).toHaveCount(1);
});
