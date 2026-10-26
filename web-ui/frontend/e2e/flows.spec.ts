// Kernabläufe aus den Dev-Befunden: Suche, NAT-Verlinkung, Findings, Dienste, Standorte.
import { readFileSync } from "fs";
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
  await expect(page.locator(".fgroup")).toHaveCount(9);
  // seit 0.72.0: Policy-Route im Verbund „Süd“ je Firewall mit anderem Gateway
  await expect(page.locator(".fgroup", { hasText: "Parallele Firewalls" })).toContainText(
    "Parallele Policy-Route abweichend",
  );
  // seit 0.62.0: Verkabelung (Speed/Native VLAN zwischen den Switches der Beispieldaten)
  await expect(page.locator(".fgroup", { hasText: "Verkabelung" })).toContainText("Kabel: Speed abweichend");
  // seit 0.73.0: Wege uneinheitlich bei der Applikation Portal Süd (Verbund Süd: fw-09 erlaubt, fw-10 sperrt)
  await expect(page.locator(".fgroup", { hasText: "Applikationen" })).toContainText("Applikation: Wege uneinheitlich");
  // seit 0.50.0: Plausibilität der Topologie (statische Route ohne Wirkung in den Beispieldaten)
  await expect(page.locator(".fgroup", { hasText: "Topologie" })).toContainText("Statische Route ohne Wirkung");
  // seit 0.51.0: Regeln gegen die Absicht der Zonenmatrix (LAN -> DMZ gesperrt)
  await expect(page.locator(".fgroup", { hasText: "Zonen" })).toContainText("Zone: gegen die Absicht");
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

test("System: Verbindungen prüfen mit ufw-Regel zum Kopieren (0.40.0)", async ({ page }) => {
  // Antwort gestellt: die echte Prüfung ginge im Test an Adressen ohne Gegenstelle
  const command = "sudo ufw allow proto tcp from 172.20.0.0/16 to 172.17.0.1 port 8080 comment 'Automato -> nb'";
  await page.route("**/api/system/connections*", (route) =>
    route.fulfill({
      json: {
        status: "error", problems: 1, checked_at: "2026-10-23T12:00:00Z",
        container: { network: "172.20.0.0/16", host: "orchestrator" },
        items: [
          { key: "database", area: "Plattform", name: "Datenbank", target: null, status: "ok",
            steps: [{ step: "sql", ok: true, detail: "SELECT 1 (2 ms)" }], hint: null, ufw: null },
          { key: "source:1", area: "Quellsysteme", name: "nb (netbox)", target: "http://host.docker.internal:8080",
            status: "error", hint: "Keine Antwort vom Docker-Host: meist sperrt die Host-Firewall (ufw) den Weg",
            steps: [{ step: "dns", ok: true, detail: "host.docker.internal → 172.17.0.1" },
                    { step: "tcp", ok: false, detail: "172.17.0.1:8080 keine Antwort nach 5 s" }],
            ufw: { from: "172.20.0.0/16", to: "172.17.0.1", port: 8080, command } },
        ],
      },
    }),
  );
  await page.goto("/#system");
  const card = page.locator("#connections");
  await card.locator("button", { hasText: "Jetzt prüfen" }).click();
  await expect(card.locator(".card-head .chip")).toHaveText("1 Problem");
  await expect(card).toContainText("Container-Netz 172.20.0.0/16");
  const nb = card.locator("tbody tr", { hasText: "nb (netbox)" });
  await expect(nb).toContainText("Fehler");
  await expect(nb.locator(".conn-fail")).toContainText("172.17.0.1:8080 keine Antwort");
  await expect(nb.locator(".conn-cmd code")).toHaveText(command);
  await expect(card.locator("tbody tr", { hasText: "Datenbank" })).toContainText("ok");
  await expect(card.locator("button", { hasText: "Erneut prüfen" })).toBeVisible();
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
  // die Liste dahinter ist auf genau dieses Objekt gefiltert (0.36.0)
  await page.locator(".drawer button.close").click();
  await expect(page.locator(".filterbar .chip", { hasText: "nur „V-EXT_shop.example.ch“" })).toBeVisible();
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("V-EXT_shop.example.ch");
  await page.getByLabel("Filter aufheben").click();
  await expect(rows(page).filter({ hasText: "H_shop01" })).toBeVisible();
});

test("Objekte: Standardobjekte zuunterst bzw. ausgeblendet, Typ-Filter für dynamische Objekte", async ({ page }) => {
  await page.goto("/#objekte");
  await page.click(".tabbar button:has-text('Services')");
  const head = page.locator("tr.group-row", { hasText: "Standardobjekte des Herstellers" });
  await expect(head).toBeVisible();
  const https = rows(page).filter({ hasText: /^HTTPS/ });
  await expect(https.first()).toContainText("Standard"); // nicht grün
  await expect(https.first().locator(".chip.ok")).toHaveCount(0);
  await page.click("button.toggle:has-text('Standardobjekte ausblenden')");
  await expect(head).toHaveCount(0);
  await expect(rows(page).filter({ hasText: "FT_tcp8443" })).toBeVisible();
  // Adressobjekte: Typ „dynamisch (SDN-Connector)“
  await page.click(".tabbar button:has-text('Adressobjekte')");
  const type = page.locator(".filterbar select").filter({ hasText: "Typ: alle" });
  await type.selectOption({ label: "dynamisch (SDN-Connector) (1)" });
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("TE_web_epg");
});

test("Regeln: IPv4/IPv6-Paar steht zusammen, Namensprofil mit FT_ und ohne Projektnummern", async ({ page }) => {
  await page.goto("/#regeln");
  await search(page).fill("web-in");
  await expect(rows(page)).toHaveCount(2);
  await expect(rows(page).nth(0)).toHaveClass(/pair-head/);
  await expect(rows(page).nth(1)).toHaveClass(/pair-tail/);
  await expect(rows(page).nth(1).locator("td").first()).toContainText("↳");
  // Rahmen um beide Regeln, je Paar getönt, Marke „Paar“ an der ersten (0.38.0)
  await expect(rows(page).nth(0)).toHaveClass(/pair-a/);
  await expect(rows(page).nth(1)).toHaveClass(/pair-a/);
  await expect(rows(page).nth(0).locator(".pair-tag")).toHaveText("Paar");
  const border = await rows(page).nth(0).locator("td").nth(1).evaluate((td) => getComputedStyle(td).borderTopWidth);
  expect(border).toBe("2px");
  await page.goto("/#dict");
  const prefixes = page.locator(".card", { hasText: "Objekt-Prefixe → Typ" });
  await expect(prefixes.locator("tr", { hasText: "FT_" })).toContainText("Service / ACI-Filter");
  await expect(page.getByText("Projektnummern")).toHaveCount(0);
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

test("Weg-Suche und Routing: Weg für eine IP, Backup-Pfade, HA und L3Out", async ({ page }) => {
  await page.goto("/#routing");
  const ctx = page.locator(".card", { hasText: "Gelesene Kontexte" });
  await expect(ctx).toContainText("leaf-101 · VRF TE:VRF1");
  await expect(ctx).toContainText("fw-05 · VDOM root");
  await expect(ctx.locator("tbody tr").filter({ hasText: "fw-04" })).toContainText("Firewall"); // nur root
  await expect(ctx).toContainText("BGP-Pfade IPv4 (HTTP 403)"); // nicht lesbar -> unbekannt
  // Weg-Suche als eigene Ansicht; nur Ziel: je Kontext die wirksame Route
  await page.locator("nav.nav a, nav.nav button").filter({ hasText: "Weg-Suche" }).first().click();
  await expect(page.locator("main.main h1")).toHaveText("Weg-Suche");
  const dst = page.getByPlaceholder("Ziel: IP, Netz oder Name");
  await dst.fill("10.20.0.5");
  await page.click("button:has-text('Weg suchen')");
  const way = page.locator(".card", { hasText: "Geht vor" }).locator("tbody tr");
  const root = way.filter({ hasText: "fw-05 · VDOM root" });
  await expect(root).toContainText("10.20.0.0/24");
  await expect(root).toContainText("Policy-Route #5");
  await expect(way.filter({ hasText: "leaf-101" })).toContainText("ECMP ×2");
  // mit Quelle: Weg Hop für Hop, ECMP über zwei Provider, Namen aus dem IPAM
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.20.0.5");
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
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("");
  await page.locator(".filterbar select").filter({ hasText: "Start: aus der Quelle" }).selectOption({ label: "fw-04" });
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator(".card", { hasText: "Weg 1" })).toContainText("keine Route zum Ziel"); // kein Default
  await dst.fill("10.20.0.5");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator(".card", { hasText: "Weg 1" })).toContainText("Next-Hop 10.70.0.1 gehört keinem");
  await page.goto("/#routing");
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

test("Weg-Suche: Anbindung aus einem Weg erfassen", async ({ page }) => {
  const search = async () => {
    await page.goto("/#wege");
    await page.locator(".filterbar select").filter({ hasText: "Start: aus der Quelle" }).selectOption({ label: "fw-04" });
    await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.20.0.5");
    await page.click("button:has-text('Weg suchen')");
  };
  await search();
  const card = page.locator(".card", { hasText: "Weg 1" });
  await expect(card).toContainText("Weg endet");
  await card.locator("button", { hasText: "Als Anbindung erfassen" }).click();
  await expect(page).toHaveURL(/#routing/); // im Routing, Reiter Anbindungen
  await expect(page.locator("#ex-hops")).toHaveValue("10.70.0.1");
  await page.locator("#ex-name").fill("MPLS Kunde A");
  await page.locator("#ex-kind").selectOption("mpls");
  await page.click("button:has-text('Speichern')");
  await expect(page.locator(".card", { hasText: "Manuelle Routen" }).locator("tbody tr")).toContainText("MPLS Kunde A");
  await search();
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

test("Parallele Firewalls: Policy-Routen im Verbund vergleichen, Befund öffnet das Routing (0.72.0)", async ({ page }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('Parallele Firewalls')");
  const card = page.locator(".parallel-compare", { hasText: "Verbund „Süd“" });
  await expect(card).toContainText("fw-09 · fw-10 – führend fw-09");
  const pbr = card.locator("tr", { hasText: "Policy-Routen & SD-WAN" });
  await expect(pbr.locator(".chip.warn")).toHaveText("1"); // Gateway je Firewall anders
  await pbr.locator("a", { hasText: "Befunde" }).click();
  await expect(page).toHaveURL(/#findings\?type=parallel_policy_route_mismatch/);
  const row = page.locator("tbody tr.clickable", { hasText: "Policy-Route 1 auf fw-10" });
  await expect(row).toContainText("Gateway: 10.89.0.253 ≠ 10.89.0.252");
  await row.click();
  await page.locator("button.linkchip", { hasText: "Policy-Route 1" }).click();
  await expect(page).toHaveURL(/#routing/);
  await expect(page.locator(".tabbar button.on")).toHaveText("Policy-Routen & SD-WAN");
  await expect(page.locator(".card-body.viewport tbody tr")).toHaveCount(1);
  await expect(page.locator(".card-body.viewport tbody tr")).toContainText("10.89.0.253");
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
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.20.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("203.0.113.10");
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
  // Zugehörigkeit nach dem Weg (0.58.0): Regeln über das Netz, die das Routing nicht erreicht
  const off = preview.locator("#customer-preview-offpath");
  await expect(off).toContainText("nicht auf dem Weg");
  await expect(off.locator("span[title]", { hasText: "CT_rt" })).toBeVisible(); // mit Begründung
  await modal.locator("button", { hasText: "+ Selektor" }).click();
  await modal.getByLabel("Art").nth(1).selectOption("prefix");
  await modal.getByLabel("Wert").nth(1).fill("10.20.128.0/17");
  await modal.getByLabel("ausschliessen").nth(1).check();
  await expect(preview.locator("tr", { hasText: "Netze" })).toContainText("10.20.0.0/17");
  // breite Maske ohne Querscrollen (0.36.0), der Wert ist ganz lesbar
  const box = await modal.boundingBox();
  expect(box!.width).toBeGreaterThan(900);
  expect(await modal.evaluate((m) => m.scrollWidth <= m.clientWidth)).toBe(true);
  const value = modal.getByLabel("Wert").nth(1);
  expect(await value.evaluate((i: HTMLInputElement) => i.scrollWidth <= i.clientWidth)).toBe(true);
  await modal.locator("button", { hasText: "Speichern" }).click();
  const a = page.locator("tbody tr.clickable").filter({ hasText: "Kunde A" });
  await expect(a).toContainText("ohne Netz 10.20.128.0/17");
  await a.locator("button", { hasText: "Löschen" }).click();
  await expect(page.locator("tbody tr.clickable").filter({ hasText: "Kunde A" })).toHaveCount(0);
  await expect(page.locator("#customer-unassigned")).toContainText("Keinem Kunden zugeordnet");
});

test("Kunden: Zugehörigkeit verlinkt auf die Elemente", async ({ page }) => {
  await page.goto("/#kunden");
  await page.locator("tbody tr.clickable").filter({ hasText: "ACI-Tenant TE" }).click();
  const sub = page.locator("tr.subrow");
  await sub.locator(".tabbar button", { hasText: "Regeln" }).click();
  await sub.locator("a").first().click();
  await expect(page).toHaveURL(/#regeln\?rule=\d+/);
  await expect(page.locator(".drawer")).toBeVisible();
});

test("Routing: Filter in BGP-Nachbarn und Policy-Routen, VRF an den Routen", async ({ page }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('Routen')");
  await expect(page.locator("thead", { hasText: "Präfix" })).toContainText("VRF");
  await page.click(".tabbar button:has-text('BGP-Nachbarn')");
  const peers = page.locator(".card-body.viewport tbody tr");
  await expect(peers.first()).toBeVisible();
  const all = await peers.count();
  expect(all).toBeGreaterThan(1);
  await page.getByPlaceholder("Nachbar, Name, AS, Zustand…").fill("isp-a-gw");
  await expect(peers).toHaveCount(1);
  await expect(page.locator(".filterbar .count")).toContainText(`1 von ${all}`);
  await page.click(".tabbar button:has-text('Policy-Routen & SD-WAN')");
  await page.getByLabel("Art").selectOption("sdwan");
  await expect(page.locator(".card-body.viewport tbody tr").first()).toContainText("SD-WAN");
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

test("Kunden-Benutzer: nur die Doku des eigenen Ausschnitts, ohne Betrieb und Verwaltung", async ({ page }) => {
  // dev-Modus: Kunden-Benutzer von TE vorführen (X-Dev-Customers, serverseitig eingeschränkt)
  const list = (await (await page.request.get("/api/customers?tenant_id=1")).json()) as { id: number; name: string }[];
  const te = list.find((c) => c.name === "TE")!;
  await page.addInitScript((id) => localStorage.setItem("automato_dev_customer", `1:${id}`), te.id);
  await page.goto("/#uebersicht");
  await expect(page.locator(".u-role")).toHaveText("Kunde");
  const nav = page.locator("nav.nav");
  await expect(nav).toContainText("Regeln & Contracts");
  for (const hidden of ["Routing", "Weg-Suche", "Abgleich", "Administration", "Kunden", "Läufe"])
    await expect(nav.getByText(hidden, { exact: true })).toHaveCount(0);
  await expect(page.getByText("Letzte Läufe")).toHaveCount(0);
  await expect(page.getByLabel("Kunde", { exact: true })).toHaveCount(0); // nur ein Kunde
  await page.goto("/#regeln");
  await expect(rows(page)).toHaveCount(2); // die Contracts der EPGs von TE
  // ohne Gerätenamen (0.58.0): statt „apic-01“ die neutrale Bezeichnung „ACI“
  await expect(page.locator("main.main")).not.toContainText("apic-01");
  await rows(page).filter({ hasText: "CT_rt" }).click();
  await expect(page.locator(".drawer")).toContainText("CT_rt");
  await expect(page.locator(".drawer")).toContainText("ACI");
  await expect(page.locator(".drawer")).not.toContainText("apic-01");
  await page.goto("/#objekte");
  await expect(page.locator(".tabbar")).toContainText("Adressobjekte");
  await expect(page.locator(".tabbar")).not.toContainText("Subnetze");
  for (const view of ["nat", "findings", "apps"]) {
    await page.goto(`/#${view}`);
    await expect(page.locator("main.main h1")).toBeVisible();
  }
  await page.goto("/#routing"); // nicht freigegeben -> Übersicht
  await expect(page.locator("main.main h1")).toHaveText("Übersicht");
  // seit 0.53.0: Netzwerkdesign mit den eigenen Zonen, ohne Versionen
  await page.goto("/#design");
  await expect(page.locator("main.main h1")).toHaveText("Netzwerkdesign");
  await expect(page.locator(".tabbar")).not.toContainText("Versionen");
});

test("Weg-Suche: Verbund als ein Hop mit beiden Firewalls", async ({ page }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.88.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.89.0.7");
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

test("Weg-Suche: Regeln auf dem Weg je Firewall", async ({ page }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.20.0.5");
  const dst = page.getByPlaceholder("Ziel: IP, Netz oder Name");
  await dst.fill("203.0.113.10");
  await page.click("button:has-text('Weg suchen')");
  // Servernetz -> Internet: keine Policy von „server“ nach „internet“
  const first = page.locator(".card[id^='trace-']", { hasText: "Weg 1 von 2" });
  await expect(first).toContainText("Regeln: gesperrt an fw-05 · VDOM root");
  await expect(first).toContainText("implizit gesperrt");
  await expect(page.locator(".card", { hasText: "Wege im Überblick" }).locator("thead")).toContainText("Regeln");
  // Startpunkt und Dienst: die Policy der VIP ist möglich (Eingang, Ausgang, Quelle nicht prüfbar)
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("");
  await page.locator(".filterbar select").filter({ hasText: "Start: aus der Quelle" }).selectOption({ label: "fw-05 · VDOM root" });
  await dst.fill("10.1.0.10");
  await page.getByPlaceholder("Dienst: tcp/443").fill("tcp/443");
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

test("Weg-Suche: Ziel als Name, Dienst als Objekt, mehrdeutige Namen zur Auswahl", async ({ page }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.20.0.5");
  const dst = page.getByPlaceholder("Ziel: IP, Netz oder Name");
  await dst.fill("www.exa");
  const option = page.locator(".menu.suggest button", { hasText: "www.example.ch" });
  await expect(option).toContainText("203.0.113.10");
  await expect(option).toContainText("IPAM · ddi-01");
  await option.click();
  await expect(dst).toHaveValue("www.example.ch");
  await page.getByPlaceholder("Dienst: tcp/443").fill("FT_https");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator("main.main")).toContainText("Ziel www.example.ch (203.0.113.10) · Dienst FT_https = tcp/443");
  await expect(page.locator(".card[id^='trace-']", { hasText: "Weg 1 von 2" })).toBeVisible();
  // mehrdeutig: zwei IPs für „dup“ – Auswahl statt Raten
  await dst.fill("dup");
  await dst.press("Escape");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator(".error")).toContainText("2 Treffer für „dup“");
  await expect(page.locator(".menu.suggest button[role='option']")).toHaveCount(2);
});

test("Weg-Suche: Netze in der Namenssuche, Hosts und Netze getrennt (0.38.0)", async ({ page }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.20.0.5");
  const dst = page.getByPlaceholder("Ziel: IP, Netz oder Name");
  // Anfang einer Adresse: Hosts und Netze mit Anzahl, getrennt filterbar
  await dst.fill("10.10.");
  const menu = page.locator(".menu.suggest");
  await expect(menu.locator(".suggest-group")).toHaveText(["Hosts", "Netze"]);
  await expect(menu.locator(".seg button", { hasText: "Netze" })).toContainText(/Netze [1-9]/);
  await menu.locator(".seg button", { hasText: "Netze" }).click();
  await expect(menu.locator(".suggest-group")).toHaveCount(0);
  await expect(menu.locator("button[role='option']", { hasText: "N_dmz-net" })).toContainText("10.10.20.0/24");
  await expect(menu.locator("button[role='option']").filter({ hasText: "web01" })).toHaveCount(0);
  // Netz-Objekt wählen: das Netz geht als Ganzes in die Suche (0.39.0)
  await dst.fill("dmz-net");
  const option = menu.locator("button[role='option']", { hasText: "N_dmz-net" });
  await expect(option).toContainText("Objekt · fwc11-000-infrz");
  await option.click();
  await expect(page.locator(".search", { has: dst })).toContainText("= Netz 10.10.20.0/24");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator("main.main")).toContainText("Ziel N_dmz-net = Netz 10.10.20.0/24");
  // Netz direkt eingegeben
  await dst.fill("10.10.20.0/24");
  await dst.press("Escape");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator("main.main")).toContainText("Ziel Netz 10.10.20.0/24");
});

test("Weg-Suche: Netz als Ganzes – Regel erfasst nur einen Teil, Teile anders geroutet (0.39.0)", async ({ page }) => {
  await page.goto("/#wege");
  const src = page.getByPlaceholder("Quelle: IP, Netz oder Name");
  const dst = page.getByPlaceholder("Ziel: IP, Netz oder Name");
  // Quelle /25 liegt ganz in der Regel 10.10.0.0/24, das Ziel /24 nur teilweise in 10.30.0.10
  await src.fill("10.10.0.0/25");
  await dst.fill("10.30.0.0/24");
  await page.getByPlaceholder("Dienst: tcp/443").fill("tcp/443");
  await page.click("button:has-text('Weg suchen')");
  const card = page.locator(".card[id^='trace-']").first();
  await expect(card).toContainText("Regeln: abhängig an fw-08");
  await expect(card.locator("tbody")).toContainText("„lan-to-dmz-web“ · erlauben · tcp/443 · möglich (Ziel nur teilweise)");
  // Ziel /23: Route, die das ganze Netz enthält (Default), der /24-Teil ist angeschlossen
  await dst.fill("10.30.0.0/23");
  await page.click("button:has-text('Weg suchen')");
  await expect(card.locator(".inner-routes")).toContainText("Teile anders geroutet: 10.30.0.0/24 über dmz");
  await expect(page.locator("main.main")).toContainText("Teile von 10.30.0.0/23 werden an");
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

test("System: Aufbewahrung mit Fristen und Knopf zum Aufräumen", async ({ page, problems }) => {
  await page.goto("/#system");
  const card = page.locator("#retention");
  await expect(card).toContainText("90 Tage");
  await expect(card).toContainText("365 Tage");
  await expect(card).toContainText("MAC/ARP der Switches30 Tage"); // seit 0.63.0
  await expect(card).toContainText("nichts fällig");
  // nichts über der Frist: der Knopf bleibt aus
  await expect(card.locator("button:has-text('Jetzt aufräumen')")).toBeDisabled();
  expect(problems).toEqual([]);
});

test("Routing: Topologie mit Knoten, Kanten und Herkunft", async ({ page, problems }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('Topologie')");
  await expect(page.locator("#topology-summary")).toContainText("Firewalls/VDOMs");
  const nodes = page.locator("#topology-nodes tbody tr.clickable");
  await page.getByPlaceholder("Knoten, Leaf, IP, Netz…").fill("fw-05");
  const fw = nodes.filter({ hasText: "fw-05 · VDOM root" });
  await expect(fw).toHaveCount(1);
  await fw.click();
  const detail = page.locator("tr.detail-row");
  await expect(detail.locator("table.topo-edges")).toBeVisible();
  await expect(detail).toContainText("nach aussen"); // Provider ohne gelesenes Gerät
  await expect(detail.locator(".chip", { hasText: "abgeleitet" }).first()).toBeVisible();
  // Art filtern: nur Router
  await page.getByPlaceholder("Knoten, Leaf, IP, Netz…").fill("");
  await page.locator(".filterbar select[aria-label='Art']").selectOption("router");
  await expect(nodes.first()).toContainText("Router");
  expect(problems).toEqual([]);
});

test("Netzwerkdesign: Zonen, Zonenmatrix mit Absicht und Zonenplan", async ({ page, problems }) => {
  await page.goto("/#design");
  const list = page.locator("#zone-list tbody tr.clickable");
  const zoneRow = (name: string) => list.filter({ has: page.locator("td:first-child b", { hasText: new RegExp(`^${name}$`) }) });
  await expect(zoneRow("LAN")).toContainText("Zone lan (fw-08)");
  await expect(zoneRow("Internet")).toContainText("ohne Netz 10.0.0.0/8");
  // Zonenmodell (0.56.0): fw-08 deklariert Edge Internet, LAN/DMZ/Internet sind Übergänge an ihr
  await expect(zoneRow("Edge Internet")).toContainText("fw-08");
  await expect(zoneRow("Edge Internet").locator(".chip", { hasText: "LAN-EINET" })).toBeVisible();
  // Netze einer Zone: 0.0.0.0/0 ohne 10.0.0.0/8, das WAN-Transfernetz ist ein Übergang
  await zoneRow("Internet").click();
  await expect(page.locator(".zone-nets")).toContainText("128.0.0.0/1");
  await expect(page.locator(".zone-transitions")).toContainText("EINET-Internet");
  await expect(page.locator(".zone-transitions")).toContainText("192.0.2.0/30");
  // Edge Internet: keine eigenen Netze (reiner Übergang)
  await zoneRow("Edge Internet").click();
  await expect(page.locator(".zone-detail")).toContainText("reiner Übergang");
  // Farbe aus der Vorlage: „Internet“ passt auf „Public“, im Editor fest
  await zoneRow("Internet").locator("button:has-text('Bearbeiten')").click();
  await expect(page.locator("#zo-color")).toContainText("aus der Vorlage („Public“)");
  await page.click(".modal button:has-text('Abbrechen')");
  // neue Zone mit Vorschau (Name je Versuch, die Beispieldaten bleiben stehen)
  const name = `Server ${test.info().retry}`;
  await page.click("button:has-text('+ Zone')");
  await page.fill("#zo-name", name);
  await page.locator("#zone-selectors input[aria-label='Wert']").first().fill("10.10.10.0/24");
  await expect(page.locator("#zone-preview")).toContainText("10.10.10.0/24 (Netz 10.10.10.0/24)");
  await page.click(".modal button:has-text('Speichern')");
  await expect(list.filter({ hasText: name })).toHaveCount(1);

  // Zonenmatrix: LAN -> DMZ gesperrt, zwei erlaubende Policies dagegen
  await page.click(".tabbar button:has-text('Zonenmatrix')");
  await expect(page.locator("#zone-matrix-summary")).toContainText("gegen die Absicht");
  const viol = page.locator("td.zcell.viol");
  await expect(viol).toHaveCount(1);
  await viol.click();
  const cell = page.locator("#zone-cell");
  await expect(cell.locator("h2")).toHaveText("LAN → DMZ");
  await expect(cell.locator("#zone-cell-rules")).toContainText("lan-to-dmz-web");
  await expect(cell.locator("#zone-cell-rules .chip.crit").first()).toBeVisible();
  // Absicht „eingeschränkt“: kein Verstoss mehr - danach zurück auf „gesperrt“
  await cell.locator("select[aria-label='Absicht']").selectOption("limited");
  await cell.locator("button:has-text('Absicht speichern')").click();
  await expect(viol).toHaveCount(0);
  // Absicht je Dienst (0.57.0): nur tcp/22 - die Policy mit tcp/443 ist wieder ein Verstoss
  await page.locator("td.zcell.i-warn").first().click();
  await page.locator("#zone-cell input[aria-label='Dienste']").fill("tcp/22");
  await page.locator("#zone-cell button:has-text('Absicht speichern')").click();
  await expect(viol).toHaveCount(1);
  await expect(viol).toContainText("nur tcp/22");
  await viol.click();
  await expect(page.locator("#zone-cell-rules")).toContainText("Dienste ausserhalb: tcp/443");
  await page.locator("#zone-cell select[aria-label='Absicht']").selectOption("deny");
  await page.locator("#zone-cell button:has-text('Absicht speichern')").click();
  await expect(viol).toHaveCount(1);

  // Zonenplan: Edge Internet mit fw-08, Übergänge zu LAN, DMZ und Internet an ihren Interfaces
  await page.click(".tabbar button:has-text('Zonenplan')");
  await expect(page.locator("#zone-plan-summary")).toContainText("3 Übergänge");
  await expect(page.locator("#zone-plan .zp-zone2", { hasText: "Edge Internet" })).toContainText("fw-08");
  const trans = page.locator("#zone-transitions tbody tr");
  await expect(trans.filter({ hasText: "DMZ-EINET" })).toContainText("fw-08 (Interface dmz)");
  await expect(trans.filter({ hasText: "EINET-Internet" })).toContainText("192.0.2.0/30");
  await expect(page.locator("#zone-plan .zp-zone2.pseudo", { hasText: "Knoten ohne Zone" })).toBeVisible();
  await expect(page.locator("#zone-unzoned")).toContainText("Netze ohne Zone");
  expect(problems).toEqual([]);
});

test("Zonen: Vorschläge beim Erfassen und Kommunikation im Zonenplan (0.66.0)", async ({ page, problems }) => {
  await page.goto("/#design");
  // neue Zone „DMZ“: die FortiGate-Zone dmz passt (gleicher Name), liegt aber schon in der Zone DMZ
  await page.click("button:has-text('+ Zone')");
  await page.fill("#zo-name", "DMZ");
  const assist = page.locator("#zone-assist-items tbody tr");
  const fwZone = assist.filter({ hasText: "Zone dmz · fw-08" });
  await expect(fwZone).toContainText("gleicher Name");
  await expect(fwZone).toContainText("in Zone DMZ (10.30.0.0/24)");
  await fwZone.locator("button:has-text('+ Übernehmen')").click();
  await expect(page.locator("#zone-selectors input[aria-label='Wert']").first()).toHaveValue("dmz");
  await expect(page.locator("#zone-selectors select[aria-label='Art']").first()).toHaveValue("fw_zone");
  await expect(page.locator("#zone-preview")).toContainText("Gleiches Netz schon in einer anderen Zone");
  await expect(fwZone).toHaveCount(0); // übernommen: kein Vorschlag mehr
  await page.click(".modal button:has-text('Abbrechen')");

  // Zonenplan: Kommunikation LAN -> DMZ (zwei erlaubende Policies), nur auf Wunsch
  await page.click(".tabbar button:has-text('Zonenplan')");
  await expect(page.locator("#zone-plan .zp-zone2").first()).toBeVisible();
  await expect(page.locator("#zone-plan .zp-edge.flow")).toHaveCount(0);
  await page.locator("#zone-plan-flows input").check();
  const flow = page.locator("#zone-plan .zp-edge.flow").filter({ has: page.locator("title", { hasText: "LAN → DMZ: 2" }) });
  await expect(flow).toHaveCount(1);
  await expect(flow).toContainText("2 Regeln");
  // keine Linie zu „Knoten ohne Zone“ (Adressen ohne Zone sind keine Zone)
  await expect(page.locator("#zone-plan .zp-edge.flow title", { hasText: "Knoten ohne Zone" })).toHaveCount(0);
  await page.locator("#zone-plan-flows input").uncheck();
  await expect(page.locator("#zone-plan .zp-edge.flow")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("Weg-Suche: Zonen je Hop und Absicht zwischen Quell- und Zielzone", async ({ page, problems }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.30.0.10");
  await page.getByPlaceholder("Dienst", { exact: false }).first().fill("tcp/443");
  await page.click("button:has-text('Weg suchen')");
  const card = page.locator("#path-zones");
  await expect(card).toContainText("Quelle LAN → Ziel DMZ");
  // LAN → Edge Internet (fw-08) → DMZ mit den Übergängen
  await expect(card.locator(".zone-chain")).toContainText("LAN-EINET");
  await expect(card.locator(".zone-chain")).toContainText("DMZ-EINET");
  await expect(card.locator(".zone-chain")).toContainText("Edge Internet");
  // LAN → DMZ ist gesperrt, die Policy lan-to-dmz-web erlaubt: gegen die Absicht
  const intent = card.locator("#path-zone-intents tbody tr");
  await expect(intent).toContainText("Absicht gesperrt");
  await expect(intent.locator(".chip.crit")).toContainText("gegen die Absicht");
  const hop = page.locator(".card[id^='trace-'] tbody tr", { hasText: "fw-08" }).first();
  await expect(hop.locator(".zone-chip")).toContainText("Edge Internet");
  expect(problems).toEqual([]);
});

test("Zonenplan übersichtlich: Suche, Fokus, Ebenen und Zonen ziehen (0.61.0)", async ({ page, problems }) => {
  await page.goto("/#design");
  await page.click(".tabbar button:has-text('Zonenplan')");
  const titles = page.locator("#zone-plan .zp-zone2 .zp-title");
  const zone = (name: string) => page.locator("#zone-plan .zp-zone2", { has: page.locator(".zp-title", { hasText: new RegExp(`^${name}`) }) });
  await expect(zone("LAN")).toHaveCount(1);
  const all = await titles.count();

  // Suche mit den direkten Nachbarn: DMZ und Edge Internet, nicht LAN
  await page.getByLabel("Im Plan suchen").fill("DMZ");
  await expect(page.locator("#zone-plan-filtered")).toContainText("Suche „DMZ“ + Nachbarn");
  await expect(zone("Edge Internet")).toHaveCount(1);
  await expect(zone("LAN")).toHaveCount(0);
  await page.locator("#zone-plan-filter select[aria-label='Nachbarn']").selectOption("0");
  await expect(zone("Edge Internet")).toHaveCount(0);
  await expect(zone("DMZ")).toHaveCount(1);
  // Export zeigt denselben Ausschnitt
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator(".plan-export button", { hasText: "SVG" }).click()]);
  const svg = await (await file.createReadStream())!.toArray().then((parts) => Buffer.concat(parts).toString("utf8"));
  expect(svg).toContain("Suche „DMZ“");
  expect(svg).not.toContain(">LAN<");
  await page.click("#zone-plan-filter button:has-text('Filter zurücksetzen')");
  await expect(titles).toHaveCount(all);

  // Ebenen: Edge ausblenden, Namen der Übergänge ausblenden – und wieder zeigen
  await page.click("#zone-plan-layers summary");
  await page.locator("#zone-plan-layers label", { hasText: /^ ?Edge$/ }).locator("input").uncheck();
  await expect(zone("Edge Internet")).toHaveCount(0);
  await expect(page.locator("#zone-plan-layers summary")).toContainText("1 ausgeblendet");
  await page.locator("#zone-plan-layers label", { hasText: /^ ?Edge$/ }).locator("input").check();
  await page.locator("#zone-plan-layers label", { hasText: "Namen der Übergänge" }).locator("input").uncheck();
  await expect(zone("Edge Internet")).toHaveCount(1);
  await expect(page.locator("#zone-plan .zp-edge text")).toHaveCount(0);
  await page.locator("#zone-plan-layers label", { hasText: "Namen der Übergänge" }).locator("input").check();
  await expect(page.locator("#zone-plan .zp-edge text").first()).toBeVisible();
  await page.click("#zone-plan-layers summary");

  // Doppelklick = Fokus auf die Zone (mit Nachbarn), Fokus wieder entfernen
  await zone("LAN").dblclick();
  await expect(page.locator("#zone-plan-focus")).toContainText("LAN");
  await expect(zone("DMZ")).toHaveCount(0);
  await page.locator("#zone-plan-focus button[aria-label='Fokus LAN entfernen']").click();
  await expect(titles).toHaveCount(all);

  // LAN ziehen: Anordnung gespeichert (Operator), Linien folgen; zurücksetzen
  const box = (await zone("LAN").locator("rect").boundingBox())!;
  const before = await zone("LAN").getAttribute("data-y");
  await page.mouse.move(box.x + 30, box.y + 12);
  await page.mouse.down();
  await page.mouse.move(box.x + 90, box.y + 130, { steps: 6 });
  const saved = page.waitForResponse((r) => r.url().includes("/zones/plan/arrangement") && r.request().method() === "PUT");
  await page.mouse.up();
  expect((await saved).status()).toBe(200);
  await expect(page.locator("#zone-plan-arrange")).toContainText("Anordnung gespeichert");
  await expect(zone("LAN")).not.toHaveAttribute("data-y", before ?? "");
  page.once("dialog", (d) => d.accept());
  await page.click("#zone-plan-arrange button:has-text('Anordnung zurücksetzen')");
  await expect(page.locator("#zone-plan-arrange")).not.toContainText("Anordnung gespeichert");
  await expect(zone("LAN")).toHaveAttribute("data-y", before ?? "");
  expect(problems).toEqual([]);
});

test("Netzwerkdesign: Export (draw.io, SVG, PDF) und Versionen des Zonenplans", async ({ page, problems }) => {
  await page.goto("/#design");
  await page.click(".tabbar button:has-text('Zonenplan')");
  await expect(page.locator("#zone-plan .zp-zone2", { hasText: "fw-08" })).toBeVisible();
  for (const [label, ext] of [
    ["draw.io", "drawio"],
    ["SVG", "svg"],
    ["PDF", "pdf"],
    ["Word", "docx"], // 0.71.0
  ]) {
    const [file] = await Promise.all([page.waitForEvent("download"), page.locator(".plan-export button", { hasText: label }).first().click()]);
    expect(file.suggestedFilename()).toMatch(new RegExp(`^zonenplan-\\d{4}-\\d\\d-\\d\\d\\.${ext}$`));
  }
  // Für Confluence: Word + draw.io im ZIP
  const [zip] = await Promise.all([page.waitForEvent("download"), page.locator("#zone-plan-confluence").first().click()]);
  expect(zip.suggestedFilename()).toMatch(/^zonenplan-\d{4}-\d\d-\d\d\.zip$/);
  expect(readFileSync((await zip.path())!).toString("latin1")).toMatch(/zonenplan-.*\.drawio/);

  // Version speichern: gleich wie der aktuelle Stand, Detail mit Zeichnung und Export
  await page.click(".tabbar button:has-text('Versionen')");
  const label = `Abnahme ${test.info().retry}`;
  await page.fill("#plan-version-save input[aria-label='Bezeichnung']", label);
  await page.click("#plan-version-save button:has-text('Version speichern')");
  const row = page.locator("#plan-versions tbody tr.clickable", { hasText: label });
  await expect(row).toContainText("= aktuell");
  await expect(page.locator("#plan-version-state")).toContainText("aktueller Stand = Version");
  await expect(page.locator("#plan-diff")).toContainText("Design unverändert");
  await expect(page.locator("svg[id^='plan-version-'] .zp-zone2", { hasText: "fw-08" })).toBeVisible();
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator(".plan-version .plan-export button", { hasText: "SVG" }).click()]);
  expect(file.suggestedFilename()).toMatch(/^zonenplan-v\d+\.svg$/);

  // neue Zone: der Stand weicht ab, der Vergleich nennt sie
  const zoneName = `Neu ${test.info().retry}`;
  const created = await page.request.post("/api/zones", {
    data: { tenant_id: 1, name: zoneName, kind: "internal", selectors: [{ kind: "prefix", value: "10.77.0.0/24", exclude: false, source_system_id: null }] },
  });
  expect(created.status()).toBe(201);
  await page.click(".tabbar button:has-text('Zonen')");
  await page.click(".tabbar button:has-text('Versionen')");
  await expect(page.locator("#plan-version-state")).toContainText("geändert seit Version");
  await row.click();
  await expect(page.locator("#plan-diff")).toContainText(zoneName);

  // aufräumen: Zone weg, Version löschen (Admin, mit Bestätigung)
  expect((await page.request.delete(`/api/zones/${(await created.json()).id}`)).status()).toBe(204);
  page.once("dialog", (d) => d.accept());
  await row.locator("button:has-text('Löschen')").click();
  await expect(row).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("Netzwerkdesign: Zonen-Vorschläge nach dem Zonenmodell, Knoten zuordnen", async ({ page, problems }) => {
  await page.goto("/#design");
  await page.click(".tabbar button:has-text('Vorschläge')");
  await expect(page.locator("#zone-suggestions-summary")).toContainText("nach der eingebauten Vorlage");
  // ACI-Tenants = Datacenter (gehört der Tenant einem Kunden aus früheren Tests: „DC <Kunde>“);
  // die Firewall-Zone internet ergänzt „Internet“ (= Public der Vorlage)
  const dc = page.locator("#zone-suggestions tbody tr[data-key^='dc']");
  await expect(dc).toContainText("neu");
  await expect(dc).toContainText("Datacenter");
  await expect(dc).toContainText("ACI-Tenant TE");
  const pub = page.locator("#zone-suggestions tbody tr[data-key='public']");
  await expect(pub).toContainText("ergänzt Zone");
  await expect(pub).toContainText("Zone internet · fw-08");

  // ausblenden und wieder vorschlagen
  await dc.locator("button:has-text('Ausblenden')").click();
  await expect(dc).toHaveCount(0);
  await page.locator("label.check", { hasText: "ausgeblendete zeigen" }).locator("input").check();
  await expect(dc).toContainText("ausgeblendet");
  await dc.locator("button:has-text('Wieder vorschlagen')").click();
  await expect(dc).not.toContainText("ausgeblendet");

  // filtern, alle Treffer wählen und gesammelt ausblenden (0.65.1), dann wieder vorschlagen
  const filter = page.getByLabel("Vorschläge filtern");
  await filter.fill("tenant te");
  await expect(page.locator("#zone-suggestions tbody tr")).toHaveCount(1);
  await page.locator("#zone-suggestions thead input[type='checkbox']").check();
  await page.click("#zone-suggestions-dismiss");
  await expect(page.getByText("1 Vorschlag/Vorschläge ausgeblendet")).toBeVisible();
  await expect(dc).toContainText("ausgeblendet");
  await dc.locator("button:has-text('Wieder vorschlagen')").click();
  await expect(dc).not.toContainText("ausgeblendet");
  await filter.fill("");

  // Übernehmen… öffnet den Editor mit dem Vorschlag (abbrechen)
  await dc.locator("button:has-text('Übernehmen')").click();
  await expect(page.locator(".modal")).toContainText("Vorschlag „DC");
  await expect(page.locator("#zo-kind")).toHaveValue("dc");
  await page.click(".modal button:has-text('Abbrechen')");

  // ausgewählte übernehmen: neue Zone, der Vorschlag verschwindet
  await dc.locator("input[type='checkbox']").check();
  await page.click("#zone-suggestions-accept");
  await expect(page.getByText("1 Zone(n) angelegt, 0 ergänzt")).toBeVisible();
  await expect(dc).toHaveCount(0);

  // Knoten ohne Zone: fw-06 der Zone Edge Internet zuordnen
  const fw6 = page.locator("#zone-suggestions-nodes tbody tr", { hasText: "fw-06" });
  await fw6.locator("select").selectOption({ label: "Edge Internet" });
  await fw6.locator("button:has-text('Zuordnen')").click();
  await expect(page.getByText("fw-06 → Edge Internet")).toBeVisible();
  await expect(fw6).toHaveCount(0);

  // aufräumen: neue Zone weg, Edge Internet wieder nur mit fw-08
  const zones = (await (await page.request.get("/api/zones?tenant_id=1")).json()) as LogicalZoneRow[];
  const made = zones.find((z) => z.name.startsWith("DC"))!;
  expect(made.kind).toBe("dc");
  expect((await page.request.delete(`/api/zones/${made.id}`)).status()).toBe(204);
  const edge = zones.find((z) => z.name === "Edge Internet")!;
  expect(edge.selectors).toHaveLength(2);
  const back = await page.request.put(`/api/zones/${edge.id}`, {
    data: { tenant_id: 1, name: edge.name, kind: edge.kind, code: "EINET", position: edge.position, selectors: edge.selectors.slice(0, 1) },
  });
  expect(back.status()).toBe(200);
  expect(problems).toEqual([]);
});

const SCHEMA_DRAWING = `<mxfile><diagram name="Zonenschema"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<object id="lan" label="LAN" automato_match="lan*"><mxCell vertex="1" parent="1" style="fillColor=#dae8fc;">
<mxGeometry x="20" y="20" width="140" height="60" as="geometry"/></mxCell></object>
<object id="dmz" label="DMZ" automato_kind="DMZ"><mxCell vertex="1" parent="1" style="fillColor=#ffe6cc;">
<mxGeometry x="240" y="20" width="140" height="60" as="geometry"/></mxCell></object>
<object id="inet" label="Internet" automato_kind="extern"><mxCell vertex="1" parent="1" style="fillColor=#f8cecc;">
<mxGeometry x="460" y="20" width="140" height="60" as="geometry"/></mxCell></object>
<object id="trf" label="Transfernetze" automato_kind="Transfer"><mxCell vertex="1" parent="1">
<mxGeometry x="20" y="140" width="140" height="60" as="geometry"/></mxCell></object>
<object id="prt" label="Partner" automato_kind="extern"><mxCell vertex="1" parent="1">
<mxGeometry x="460" y="140" width="140" height="60" as="geometry"/></mxCell></object>
<mxCell id="e1" value="" edge="1" parent="1" source="inet" target="dmz" style="endArrow=classic;dashed=1;"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e2" value="gesperrt" edge="1" parent="1" source="dmz" target="lan" style="endArrow=classic;"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>`;

test("Grundschema der Zonen: draw.io hochladen, Vorschläge und Absichten danach", async ({ page, problems }) => {
  await page.goto("/#dict");
  const card = page.locator("#zone-schema");
  await expect(card).toContainText("Noch kein Grundschema");
  // Vorlage herunterladen
  const [tpl] = await Promise.all([page.waitForEvent("download"), card.locator("button:has-text('Vorlage (draw.io)')").click()]);
  expect(tpl.suggestedFilename()).toBe("zonenschema-vorlage.drawio");
  // Zeichnung hochladen: Vorschau mit Unterschied, dann speichern
  await card.locator("input[type='file']").setInputFiles({ name: "zonen.drawio", mimeType: "application/xml", buffer: Buffer.from(SCHEMA_DRAWING) });
  const modal = page.locator(".modal");
  await expect(modal.locator("#zone-schema-diff")).toContainText("neu: DMZ, Internet, LAN, Partner, Transfernetze");
  await expect(modal.locator("#zone-schema-links tbody tr")).toHaveCount(2);
  await modal.locator("#zone-schema-save").click();
  await expect(card.locator("#zone-schema-zones tbody tr")).toHaveCount(5);
  await expect(card.locator("#zone-schema-drawing rect")).toHaveCount(5);
  await expect(card).toContainText("zonen.drawio");
  const [drawing] = await Promise.all([page.waitForEvent("download"), card.locator("button:has-text('Zeichnung herunterladen')").click()]);
  expect(drawing.suggestedFilename()).toBe("zonen.drawio");

  // Netzwerkdesign: die Firewall-Zone internet ergänzt „Internet“ des Schemas, fehlende Zonen und
  // Absichten aus dem Schema
  await page.goto("/#design");
  await page.click(".tabbar button:has-text('Vorschläge')");
  const inet = page.locator("#zone-suggestions tbody tr[data-key='internet']");
  await expect(inet).toContainText("ergänzt Zone");
  await expect(inet.locator(".chip", { hasText: "Schema" })).toBeVisible();
  const status = page.locator("#zone-schema-status");
  await expect(status.locator("#zone-schema-missing")).toContainText("Partner");
  await expect(status.locator("#zone-schema-missing")).toContainText("Transfernetze");
  const intents = status.locator("#zone-schema-intents tbody tr");
  await expect(intents).toHaveCount(2);
  await expect(intents.filter({ hasText: "DMZ → LAN" })).toContainText("gesperrt");
  await status.locator("#zone-schema-apply").click();
  await expect(page.getByText("2 Absicht(en) aus dem Grundschema gesetzt")).toBeVisible();
  await expect(status).toContainText("die Zonenmatrix entspricht dem Schema");
  // „anlegen…“ öffnet den Editor mit der Zone aus dem Schema (abbrechen)
  await status.locator("#zone-schema-missing .chip", { hasText: "Partner" }).locator("button").click();
  await expect(page.locator("#zo-name")).toHaveValue("Partner");
  await expect(page.locator("#zo-kind")).toHaveValue("external");
  await page.click(".modal button:has-text('Abbrechen')");

  // aufräumen: Absichten und Schema wieder weg
  const zones = (await (await page.request.get("/api/zones?tenant_id=1")).json()) as LogicalZoneRow[];
  const id = (name: string) => zones.find((z) => z.name === name)!.id;
  for (const [src, dst] of [
    ["Internet", "DMZ"],
    ["DMZ", "LAN"],
  ]) {
    const r = await page.request.put("/api/zones/policy", { data: { tenant_id: 1, src_zone_id: id(src), dst_zone_id: id(dst), intent: null } });
    expect(r.status()).toBe(200);
  }
  const profiles = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  const pid = profiles.find((p) => p.name === "default")!.id;
  expect((await page.request.delete(`/api/naming-profiles/${pid}/zone-schema`)).status()).toBe(204);
  expect(problems).toEqual([]);
});

test("Grundschema in der Oberfläche bearbeiten: Namen, Art, Absicht, neue Zone (0.70.0)", async ({ page, problems }) => {
  const profiles = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  const pid = profiles.find((p) => p.name === "default")!.id;
  const put = await page.request.put(`/api/naming-profiles/${pid}/zone-schema`, { data: { filename: "zonen.drawio", content: SCHEMA_DRAWING } });
  expect(put.status()).toBe(200);
  await page.goto("/#dict");
  const card = page.locator("#zone-schema");
  await expect(card.locator("#zone-schema-zones tbody tr")).toHaveCount(5);
  await card.locator("#zone-schema-edit").click();
  const editor = card.locator("#zone-schema-editor");
  // Partner umbenennen - der Hinweis zeigt den bisherigen Namen
  const partner = editor.locator("#zone-schema-edit-zones tbody tr[data-zone='Partner']");
  await partner.getByLabel("Name der Zone").fill("Partner extern");
  await expect(editor.locator("#zone-schema-edit-zones")).toContainText("bisher Partner");
  // Absicht DMZ → LAN: eingeschränkt auf tcp/443
  const dmzLan = editor.locator("#zone-schema-edit-links tbody tr[data-link='DMZ>LAN']");
  await dmzLan.getByLabel("Absicht").selectOption("limited");
  await dmzLan.getByLabel("Dienste").fill("tcp/443");
  // neue Zone, an den Anfang
  await editor.locator("#zone-schema-add-zone").click();
  const fresh = editor.locator("#zone-schema-edit-zones tbody tr").last();
  await fresh.getByLabel("Name der Zone").fill("Backup");
  await fresh.getByLabel("Art").selectOption("management");
  // doppelter Name sperrt das Speichern
  await fresh.getByLabel("Name der Zone").fill("dmz");
  await expect(editor.locator("#zone-schema-edit-save")).toBeDisabled();
  await fresh.getByLabel("Name der Zone").fill("Backup");
  for (let i = 0; i < 5; i++) await editor.locator("#zone-schema-edit-zones tbody tr[data-zone='Backup'] button[title='nach oben']").click();
  await editor.locator("#zone-schema-edit-save").click();
  // gespeichert: Reihenfolge, neuer Name, Absicht mit Dienst
  await expect(editor).toHaveCount(0);
  const rows = card.locator("#zone-schema-zones tbody tr");
  await expect(rows).toHaveCount(6);
  await expect(rows.first()).toContainText("Backup");
  await expect(card.locator("#zone-schema-zones")).toContainText("Partner extern");
  await expect(card.locator("#zone-schema-links tbody tr", { hasText: "DMZ" }).filter({ hasText: "LAN" })).toContainText("nur tcp/443");
  await expect(card.locator("#zone-schema-edited")).toContainText("in der Oberfläche bearbeitet");
  // Zeichnung zum Herunterladen folgt dem bearbeiteten Stand
  const [file] = await Promise.all([page.waitForEvent("download"), card.locator("button:has-text('Zeichnung herunterladen')").click()]);
  const text = readFileSync((await file.path())!, "utf-8");
  expect(text).toContain("Partner extern");
  expect(text).toContain("Backup");
  expect((await page.request.delete(`/api/naming-profiles/${pid}/zone-schema`)).status()).toBe(204);
  expect(problems).toEqual([]);
});

test("Kunden-Benutzer in Automato zuordnen: Zonen des Kunden, Anmeldung ohne Rolle sieht nur ihn", async ({ page, browser, problems }) => {
  const login = `kunde.dmz${test.info().retry}@example.ch`;
  const name = `Kunde DMZ ${test.info().retry}`;
  // eigener Kunde, dem die Zone DMZ gehört (danach zurück)
  const zones = (await (await page.request.get("/api/zones?tenant_id=1")).json()) as LogicalZoneRow[];
  const dmz = zones.find((z) => z.name === "DMZ")!;
  const created = await page.request.post("/api/customers", { data: { tenant_id: 1, name, selectors: [] } });
  expect(created.status()).toBe(201);
  const te = (await created.json()) as { id: number };
  const put = (customer: number | null) =>
    page.request.put(`/api/zones/${dmz.id}`, {
      data: { tenant_id: 1, name: dmz.name, kind: dmz.kind, color: dmz.color, position: dmz.position, customer_id: customer, selectors: dmz.selectors },
    });
  expect((await put(te.id)).status()).toBe(200);

  await page.goto("/#kunden");
  await expect(page.locator("tbody tr", { hasText: name }).first()).toContainText("DMZ"); // Zonen des Kunden
  const card = page.locator("#customer-users");
  await card.locator("input[aria-label='Benutzer']").fill(login);
  await card.locator("select[aria-label='Kunde']").selectOption({ label: name });
  await card.locator("button:has-text('Zuordnen')").click();
  const row = card.locator("#customer-user-list tbody tr", { hasText: login });
  await expect(row).toContainText(name);

  // derselbe Benutzer meldet sich an (dev: X-Dev-User, ohne Rolle aus Keycloak) - eigener
  // Browser-Kontext, sonst teilen sich beide Seiten den localStorage
  const ctx = await browser.newContext({ baseURL: "http://localhost:5173" });
  const user = await ctx.newPage();
  await user.addInitScript((u) => {
    localStorage.setItem("automato_dev_user", u);
    localStorage.setItem("automato_tenant", "1");
  }, login);
  await user.goto("/#uebersicht");
  await expect(user.locator(".u-role")).toHaveText("Kunde");
  await expect(user.locator("nav.nav")).not.toContainText("Administration");
  await user.goto("/#design");
  const own = user.locator("#zone-list tbody tr.clickable", { hasText: "DMZ" });
  await expect(own).toHaveCount(1);
  await expect(user.locator("#zone-list tbody tr", { hasText: "LAN" })).toContainText("geteilt");
  // Plan: seine Zone, die geteilten Zonen dahinter (Edge Internet ohne Knoten), keine Geräte
  await user.click(".tabbar button:has-text('Zonenplan')");
  await expect(user.locator("#zone-plan .zp-zone2", { hasText: "Edge Internet" })).toBeVisible();
  await expect(user.locator("#zone-transitions")).toContainText("DMZ-EINET");
  await expect(user.locator("#zone-plan")).not.toContainText("fw-08");
  await expect(user.locator("#zone-unzoned")).toHaveCount(0);
  await ctx.close();

  // Kunden-Filter der Kopfzeile (0.57.1): der Zonenplan zeigt die Ansicht dieses Kunden, von den
  // geteilten Zonen nur die Knoten, die er braucht
  await page.goto("/#design");
  await page.getByLabel("Kunde", { exact: true }).selectOption({ label: name });
  await page.click(".tabbar button:has-text('Zonenplan')");
  await expect(page.locator("#zone-plan-view")).toHaveValue(String(te.id));
  await expect(page.locator("#zone-plan-customer")).toContainText("die der Kunde braucht");
  await expect(page.locator("#zone-plan .zp-zone2", { hasText: "Edge Internet" })).toContainText("fw-08");
  await expect(page.locator("#zone-transitions")).toContainText("DMZ-EINET");
  await page.getByLabel("Kunde", { exact: true }).selectOption("");
  await page.goto("/#kunden");

  // aufräumen: Zuordnung entfernen, Zone wieder ohne Kunde
  page.once("dialog", (d) => d.accept());
  await row.locator("button:has-text('Entfernen')").click();
  await expect(row).toHaveCount(0);
  expect((await put(null)).status()).toBe(200);
  expect((await page.request.delete(`/api/customers/${te.id}`)).status()).toBe(204);
  expect(problems).toEqual([]);
});

interface LogicalZoneRow {
  id: number;
  name: string;
  kind: string;
  color: string | null;
  position: number;
  selectors: unknown[];
}

test("Verkabelung: Switches, Verbindungen mit Abweichungen, Ports und VLANs (0.62.0)", async ({ page, problems }) => {
  await page.goto("/#verkabelung");
  await expect(page.locator("body")).toContainText("Catalyst 1200/1300, Juniper Junos"); // 0.68.0
  await expect(page.locator("#cabling-summary")).toContainText("2 Switches");
  await expect(page.locator("#cabling-summary")).toContainText("1 einseitig");
  const rows = page.locator("#cabling-links tbody tr");
  // Gi1/0/48 <-> Eth1/2: Speed und Native VLAN abweichend, zuerst gelistet
  const bad = rows.filter({ hasText: "Gi1/0/48" });
  await expect(bad).toContainText("Eth1/2");
  await expect(bad.locator(".crit", { hasText: "Speed a-1000 ↔ 100" })).toBeVisible();
  await expect(bad.locator(".crit", { hasText: "Native VLAN 1 ↔ 99" })).toBeVisible();
  await expect(rows.first()).toContainText("Gi1/0/48");
  await expect(rows.filter({ hasText: "Gi1/0/47" }).locator(".chip.ok")).toHaveText("beidseitig");
  // Firewall ohne CDP/LLDP-Daten, ESXi-Host aus dem vCenter, ACI-Leaf meldet nicht
  await expect(rows.filter({ hasText: "fw-08" })).toContainText("Gegenstelle ohne CDP/LLDP");
  await expect(rows.filter({ hasText: "vmnic2" })).toContainText("esx01-chzrh1.example.ch (ESXi-Host)");
  await expect(rows.filter({ hasText: "leaf-101" }).locator(".chip.warn")).toHaveText("einseitig");
  await page.locator("#cabling-links label", { hasText: "nur Abweichungen" }).locator("input").check();
  await expect(rows).toHaveCount(1);

  // Geräte -> Ports des Switches; VLAN 30 nur auf den Trunks
  await page.click(".tabbar button:has-text('Geräte')");
  await page.locator("#cabling-devices tbody tr", { hasText: "sw-core-01" }).click();
  const ports = page.locator("#cabling-ports tbody tr");
  await expect(ports.filter({ hasText: "Eth1/3" })).toContainText("esx01-chzrh1.example.ch");
  await page.locator("#cabling-ports input[aria-label='VLAN']").fill("30");
  await expect(ports).toHaveCount(3);
  await expect(ports.filter({ hasText: "Eth1/2" })).toContainText("Native VLAN 1 ↔ 99");

  // VLANs: DMZ als Access an sw-acc-01, in den Trunks beider Switches
  await page.click(".tabbar button:has-text('VLANs')");
  const dmz = page.locator("#cabling-vlans tbody tr[data-vlan='30']");
  await expect(dmz).toContainText("DMZ");
  await expect(dmz).toContainText("sw-acc-01 · 2 · 2"); // seit 0.64.0 auch der DMZ-Webserver
  await expect(dmz).toContainText("sw-core-01 · 0 · 3");

  // Befunde in der Kategorie „Verkabelung“
  await page.goto("/#findings");
  await expect(page.locator("body")).toContainText("Verkabelung");
  expect(problems).toEqual([]);
});

test("L2-Sicht: Plan, Ausfall simulieren, VLAN-Weg, Befunde und L2 in der Weg-Suche (0.64.0)", async ({ page, problems }) => {
  await page.goto("/#verkabelung");
  await page.click(".tabbar button:has-text('Plan')");
  const svg = page.locator("#cabling-plan-svg");
  for (const name of ["sw-core-01", "sw-acc-01", "leaf-101", "fw-08", "esx01-chzrh1.example.ch"])
    await expect(svg.locator(`g[data-node='${name}']`)).toHaveCount(1);
  // Kern oben: sw-core-01 über sw-acc-01
  const y = async (name: string) =>
    Number((await svg.locator(`g[data-node='${name}']`).getAttribute("transform"))!.split(",")[1].replace(")", ""));
  expect(await y("sw-core-01")).toBeLessThan(await y("sw-acc-01"));
  // Ausfall des Kerns: Leaf und ESXi-Host getrennt (seit 0.70.0 Umschalter „Ausfall“)
  await page.locator("#cabling-plan-mode button", { hasText: "Ausfall" }).click();
  await svg.locator("g[data-node='sw-core-01']").click();
  const fail = page.locator("#cabling-failure");
  await expect(fail).toContainText("Fällt sw-core-01 aus");
  // esx01 hängt auch am Leaf (ACI meldet vmnic0) - beide zusammen getrennt
  await expect(fail).toContainText("ist getrennt: esx01-chzrh1.example.ch, leaf-101");
  await page.click("button:has-text('Ausfall zurücksetzen')");
  await expect(fail).toHaveCount(0);
  // VLAN 20: Access-Ports auf beiden Switches, der Port-Channel lässt es durch
  await page.getByLabel("VLAN hervorheben").fill("20");
  await expect(page.locator("#cabling-vlan-islands")).toContainText("VLAN 20: durchgehend (sw-acc-01, sw-core-01)");
  // Befunde der L2-Sicht
  await page.goto("/#findings");
  const group = page.locator(".fgroup", { hasText: "Verkabelung" });
  await expect(group).toContainText("L2: Switch als einzelner Ausfallpunkt");
  await expect(group).toContainText("L2: alle Kabel am selben Switch"); // sw-acc-01 nur am Kern
  // Weg-Suche: Ziel in der DMZ - Anschluss und Weg über die Switches ab der Firewall
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.30.0.10");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator("#trace-l2-dst")).toContainText("Ziel hängt an sw-acc-01 Gi1/0/9 (VLAN 30)");
  await expect(page.locator(".card[id^='trace-'] .l2-seg").first()).toContainText("L2: fw-08 dmz → sw-acc-01 (Te1/1/1 → Gi1/0/9)");
  // Richtung tauschen (0.65.1): Quelle und Ziel getauscht, der Rückweg gleich gesucht
  await page.click("#trace-swap");
  await expect(page.getByPlaceholder("Quelle: IP, Netz oder Name")).toHaveValue("10.30.0.10");
  await expect(page.getByPlaceholder("Ziel: IP, Netz oder Name")).toHaveValue("10.10.0.5");
  await expect(page.getByText("Quelle 10.30.0.10 → Ziel 10.10.0.5")).toBeVisible();
  await expect(page.locator("#trace-l2-src")).toContainText("sw-acc-01 Gi1/0/9");
  expect(problems).toEqual([]);
});

test("Plan: Spanning Tree, mehrere Ausfälle gleichzeitig, Export SVG/draw.io (0.65.0)", async ({ page, problems }) => {
  await page.goto("/#verkabelung");
  await page.click(".tabbar button:has-text('Plan')");
  const svg = page.locator("#cabling-plan-svg");
  // Kabel zum Leaf: Spanning Tree blockiert VLAN 30 - gestrichelt, mit VLAN 30 als Reserve
  const toLeaf = svg.locator("g.plan-edge[data-edge*='sw-core-01'][data-edge*='leaf-101']");
  await expect(toLeaf).toHaveAttribute("data-stp", "30");
  await expect(toLeaf.locator("text")).toHaveText("STP 30");
  await page.getByLabel("VLAN hervorheben").fill("30");
  await expect(toLeaf).toHaveAttribute("data-vlan", "standby");
  await page.getByLabel("VLAN hervorheben").fill("");
  await expect(toLeaf).not.toHaveAttribute("data-vlan", "standby");
  // mehrere Ausfälle: das Kabel zum Leaf trennt nur den Leaf (der ESXi-Host hängt auch am Kern) …
  await page.locator("#cabling-plan-mode button", { hasText: "Ausfall" }).click();
  await toLeaf.click();
  const fail = page.locator("#cabling-failure");
  await expect(fail).toContainText("ist getrennt: leaf-101");
  // … mit Shift-Klick auch das Kabel zum ESXi-Host: beide getrennt
  const toEsx = svg.locator("g.plan-edge[data-edge*='sw-core-01'][data-edge*='esx01']");
  await toEsx.click({ modifiers: ["Shift"] });
  await expect(fail).toContainText("Fallen das Kabel");
  await expect(fail).toContainText("ist getrennt: esx01-chzrh1.example.ch, leaf-101");
  // nochmals Shift-Klick nimmt es wieder heraus
  await toEsx.click({ modifiers: ["Shift"] });
  await expect(fail).toContainText("Fällt das Kabel");
  await expect(fail).toContainText("ist getrennt: leaf-101");
  await page.click("button:has-text('Ausfall zurücksetzen')");
  await expect(fail).toHaveCount(0);
  // Export wie in der Ansicht
  for (const [label, ext] of [["SVG", "svg"], ["draw.io", "drawio"]] as const) {
    const [file] = await Promise.all([page.waitForEvent("download"), page.locator("#cabling-plan-export button", { hasText: label }).click()]);
    expect(file.suggestedFilename()).toMatch(new RegExp(`^verkabelung-\\d{4}-\\d\\d-\\d\\d\\.${ext}$`));
    const text = readFileSync((await file.path())!, "utf-8");
    expect(text).toContain("sw-core-01");
    expect(text).toContain("STP blockiert 30");
  }
  // Ports: Spanning Tree am Port
  await page.click(".tabbar button:has-text('Ports')");
  await expect(page.locator("#cabling-ports tbody tr[data-port='Eth1/48'] .port-l2.warn")).toHaveText("STP blockiert VLAN 30");
  expect(problems).toEqual([]);
});

test("Plan: Patchungen des gewählten Geräts mit Details (0.70.0)", async ({ page, problems }) => {
  await page.goto("/#verkabelung");
  await page.click(".tabbar button:has-text('Plan')");
  const svg = page.locator("#cabling-plan-svg");
  // Standard: Klick zeigt die Patchungen (kein Ausfall)
  await expect(page.locator("#cabling-plan-mode button", { hasText: "Patchungen" })).toHaveAttribute("aria-pressed", "true");
  await svg.locator("g[data-node='sw-core-01']").click();
  await expect(page.locator("#cabling-failure")).toHaveCount(0);
  const panel = page.locator("#cabling-patches");
  await expect(panel.locator("h3")).toContainText("Patchungen sw-core-01");
  await expect(panel).toContainText("C93180YC-FX");
  // Port-Channel als Gruppe, je Kabel beide Enden mit Konfiguration
  await expect(panel.locator("tr.patch-lag")).toContainText("Port-Channel Po10");
  const eth1 = panel.locator("tbody tr[data-patch]", { hasText: "Eth1/1" }).first();
  await expect(eth1).toContainText("sw-acc-01");
  await expect(eth1).toContainText("Gi1/0/47");
  await expect(eth1).toContainText("Trunk");
  await expect(panel.locator("tbody tr[data-patch]", { hasText: "Eth1/48" })).toContainText("STP blockiert 30");
  await expect(panel.locator("tr.patch-issue").first()).toBeVisible(); // Speed-Abweichung Eth1/2
  // Klick auf ein Kabel: nur die Kabel zwischen den beiden Geräten
  await svg.locator("g.plan-edge[data-edge*='sw-core-01'][data-edge*='esx01']").click();
  await expect(panel.locator("h3")).toContainText("↔ esx01-chzrh1.example.ch");
  await expect(panel.locator("tbody tr[data-patch]")).toHaveCount(1);
  await expect(panel).toContainText("vmnic2");
  await panel.locator("button", { hasText: "alle Kabel von sw-core-01" }).click();
  await expect(panel.locator("h3")).not.toContainText("↔");
  // Gegenstelle öffnen: Patchungen von sw-acc-01
  await eth1.locator("button.patch-link", { hasText: "sw-acc-01" }).click();
  await expect(panel.locator("h3")).toContainText("Patchungen sw-acc-01");
  await expect(panel).toContainText("Te1/1/1");
  // Endpunkte am Port öffnen den Reiter Endpunkte
  await svg.locator("g[data-node='sw-core-01']").click();
  await panel.locator("tbody tr[data-patch]", { hasText: "Eth1/3" }).locator("button.patch-endpoints").click();
  await expect(page.locator("#cabling-endpoints-port")).toContainText("sw-core-01 Eth1/3");
  expect(problems).toEqual([]);
});

test("Endpunkte: Wo hängt … nach Name und IP, MACs je Port, VLAN am ESXi-Uplink (0.63.0)", async ({ page, problems }) => {
  await page.goto("/#verkabelung");
  await expect(page.locator("#cabling-summary")).toContainText("3 Endpunkte"); // seit 0.64.0 auch der DMZ-Webserver
  // Ports: Anzahl Endpunkte je Port öffnet den Reiter mit Port-Filter
  await page.click(".tabbar button:has-text('Ports')");
  const gi5 = page.locator("#cabling-ports tbody tr[data-port='Gi1/0/5']");
  await gi5.locator("button.linkchip").click();
  await expect(page.locator("#cabling-endpoints-port")).toContainText("sw-acc-01 Gi1/0/5");
  const rows = page.locator("#cabling-endpoints tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("a0:b1:c2:d3:e4:f5");
  await expect(rows.first()).toContainText("10.20.0.15");
  await page.locator("#cabling-endpoints-port button").click();
  await expect(rows).toHaveCount(3); // ohne die Uplinks Po1/Po10
  await expect(page.locator("#cabling-endpoints-summary")).toContainText("3 MACs an 3 Ports · 1 VMs");

  // Wo hängt …: VM-Name -> ESXi-Host am Core-Switch; IP aus ARP -> Access-Port
  const locate = page.locator("#cabling-locate");
  await locate.getByLabel("Wo hängt").fill("web01");
  await locate.locator("button:has-text('Suchen')").click();
  const hit = locate.locator(".locate-hit[data-mac='00:50:56:aa:00:01']");
  await expect(hit).toContainText("VM web01-007-ka");
  await expect(hit.locator(".locate-at")).toContainText("sw-core-01 Eth1/3 · VLAN 10 · über esx01-chzrh1.example.ch (vmnic2)");
  await locate.getByLabel("Wo hängt").fill("10.20.0.15");
  await locate.locator("button:has-text('Suchen')").click();
  await expect(locate.locator(".locate-hit .locate-at")).toContainText("sw-acc-01 Gi1/0/5 · VLAN 20");
  await locate.getByLabel("Wo hängt").fill("gibt-es-nicht");
  await locate.locator("button:has-text('Suchen')").click();
  await expect(locate).toContainText("keine MAC zu „gibt-es-nicht“");

  // Befund: VLAN der VM fehlt am Uplink des ESXi-Hosts
  await page.goto("/#findings");
  await expect(page.locator(".fgroup", { hasText: "Verkabelung" })).toContainText("VLAN fehlt am ESXi-Uplink");
  expect(problems).toEqual([]);
});
