// Applikationen (0.14.0): Klick öffnet die Doku – Übersicht, VMs, EPGs, Regeln mit Filtern und
// Erklärung im Detail; Stammdaten mit externem/internem Namen und Verwendungszweck.
import { readFileSync } from "fs";
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
  const systems = page.locator(".card").filter({ has: page.locator("h2", { hasText: /^Systeme$/ }) });
  const vm = systems.locator("tbody tr", { hasText: "web01-007-ka" });
  await expect(vm).toContainText("TE/AP/EG_db");
  await expect(vm).toContainText("10.10.10.5");
  // 0.73.0: in der Übersicht kompakt, alles Weitere im Detail
  await expect(vm).not.toContainText("Portgruppe");
  await expect(vm.locator(".objgroup")).toHaveCount(0);
  const epg = page.locator(".card", { hasText: "EPGs im ACI" }).locator("tbody tr");
  await expect(epg).toContainText("TE/AP/EG_db");
  await vm.click();
  const detail = page.locator("#app-system-detail");
  await expect(page.locator(".drawer h2")).toHaveText("web01-007-ka");
  await expect(detail).toContainText("Portgruppe TE|AP|EG_db → EPG TE/AP/EG_db (AP TE/AP)");
  // je NIC die Portgruppe mit ihrer EPG (0.43.1)
  await expect(detail).toContainText("→ TE/AP/EG_db");
  await expect(detail).toContainText("Ubuntu Linux (64-bit)");
  await expect(detail).toContainText("clu01-chzrh1");
  // Firewall-Objekte je Name zusammengefasst, mit den Firewalls und der Namensprüfung
  const web01 = detail.locator(".objgroup", { hasText: "H_web01" });
  await expect(web01).toHaveCount(1);
  await expect(web01).toContainText("fwc11-000-infrz");
  await expect(web01).toContainText("fw-03");
  await expect(web01).not.toContainText("Name ≠ DNS");
  await expect(detail.locator(".objgroup", { hasText: "H_web03" })).toContainText("Name ≠ DNS");
  // Regeln des Systems: schliesst das Detail und filtert die Regeln
  await detail.locator("#app-system-rules").click();
  await expect(page.locator(".drawer")).toHaveCount(0);
  await expect(page.locator("#app-rules select").first()).toHaveValue(/^vm:/);
  await vm.click();
  await web01.locator("button:has-text('fw-03')").click();
  // 0.95.0: das Objekt im Detail in der Doku, „In Objekte & Inventar öffnen“ führt dorthin
  await expect(page.locator(".drawer h2")).toHaveText("H_web01");
  await expect(page).toHaveURL(/#apps\?app=\d+/);
  await page.locator("#app-object-open").click();
  await expect(page).toHaveURL(/#objekte\?object_id=\d+/);
  await expect(page.locator(".drawer h2")).toHaveText("H_web01");
});

test("Applikation: Klick auf eine EPG öffnet ihr Detail statt zu den Regeln zu springen (0.95.0)", async ({ page }) => {
  await open(page);
  await page.locator("#app-epgs tbody tr", { hasText: "TE/AP/EG_db" }).click();
  const detail = page.locator("#app-epg-detail");
  await expect(page.locator(".drawer h2")).toHaveText("TE/AP/EG_db");
  // Lage wie im Regel-Detail: Tenant, VRF, VMs, Leaf-Ports
  await expect(detail.locator(".refcard")).toContainText("VRF1");
  await expect(detail.locator(".refcard")).toContainText("web01-007-ka");
  await expect(detail.locator(".refcard")).toContainText("leaf-101 eth1/1");
  await expect(detail).toContainText("AP TE/AP"); // gehört dazu über
  // die Regeln filtert erst der Knopf
  const epgFilter = page.locator("#app-rules select").nth(1);
  await expect(epgFilter).toHaveValue("");
  await detail.locator("#app-epg-rules").click();
  await expect(page.locator(".drawer")).toHaveCount(0);
  await expect(epgFilter).toHaveValue(/^epg:/);
  // aus dem Detail des Systems zur EPG, von dort nach Objekte & Inventar (Portgruppen der EPG)
  await page.locator("#app-systems tbody tr", { hasText: "web01-007-ka" }).click();
  await page.locator("#app-system-detail .app-system-epg", { hasText: "TE/AP/EG_db" }).click();
  await expect(page.locator(".drawer h2")).toHaveText("TE/AP/EG_db");
  await page.locator("#app-epg-objects").click();
  await expect(page).toHaveURL(/#objekte\?/);
  await expect(page.locator(".tabbar button.on")).toHaveText("Portgruppen");
  await expect(rows(page).filter({ hasText: "TE|AP|EG_db" })).toHaveCount(1);
});

test("Applikation: Namen der Gegenstellen in den Kategorien – Einstellung des Admins, nicht für Kunden-Benutzer (0.95.0)", async ({ page, browser }) => {
  const dmz = (p: import("@playwright/test").Page) => p.locator("#app-categories tbody tr", { hasText: "dmz-to-web" }).first();
  // Kategorien aufklappen - der Zustand bleibt im Browser gespeichert, also nur wenn zu
  const categories = async (p: import("@playwright/test").Page) => {
    await expect(p.locator("#app-categories > .card")).toBeVisible();
    if ((await p.locator("#app-categories .stack").count()) === 0) await p.locator("#app-categories h2.fold-title").first().click();
    await expect(p.locator("#app-categories .stack")).toHaveCount(1);
  };
  await open(page);
  await categories(page);
  await expect(dmz(page)).toContainText("10.10.20.0/24"); // Standard: nur die Adresse
  await expect(dmz(page)).not.toContainText("N_dmz-net");
  // Kunde mit dem AP und dem Servernetz des Webshops (Regel dmz-to-web gehört dazu)
  const created = await page.request.post("/api/customers", {
    data: {
      tenant_id: 1,
      name: `Kunde Namen ${test.info().retry}`,
      selectors: [
        { kind: "aci_ap", value: "TE/AP" },
        { kind: "prefix", value: "10.10.10.0/24" },
      ],
    },
  });
  expect(created.status()).toBe(201);
  const customer = (await created.json()) as { id: number };
  try {
    await page.goto("/#tenants");
    const settings = page.locator(".card", { has: page.locator("#ts-foreign-names") });
    await settings.locator("#ts-foreign-names input").check();
    await settings.getByRole("button", { name: "Speichern" }).click();
    await expect(page.locator(".site-card.selected")).toContainText("Namen in der Doku");
    // Betreuer: Name aus dem Firewall-Objekt der Regel, dazu die Adresse
    await open(page);
    await categories(page);
    await expect(dmz(page)).toContainText("N_dmz-net (10.10.20.0/24)");
    // Kunden-Benutzer: weiter nur die Adresse
    const ctx = await browser.newContext({ baseURL: "http://localhost:5173" });
    const user = await ctx.newPage();
    await user.addInitScript((id) => {
      localStorage.setItem("automato_dev_customer", `1:${id}`);
      localStorage.setItem("automato_tenant", "1");
    }, customer.id);
    await user.goto("/#apps");
    await user.locator("tbody tr", { hasText: "Webshop" }).click();
    await categories(user);
    await expect(dmz(user)).toContainText("10.10.20.0/24");
    await expect(dmz(user)).not.toContainText("N_dmz-net");
    await ctx.close();
  } finally {
    await page.request.patch("/api/tenants/1", { data: { appdoc_foreign_names: false } });
    await page.request.delete(`/api/customers/${customer.id}`);
  }
});

test("Netzwerkdesign → Kategorien: Zonen zuordnen, Titel nach der Zone, Netze mit Ausschlüssen, Namen für den Admin (0.102.0)", async ({ page }) => {
  const categories = async () => {
    await expect(page.locator("#app-categories > .card")).toBeVisible();
    if ((await page.locator("#app-categories .stack").count()) === 0) await page.locator("#app-categories h2.fold-title").first().click();
    await expect(page.locator("#app-categories .stack")).toHaveCount(1);
  };
  const card = (index: number) =>
    page.locator("#app-categories .stack > .card", { has: page.locator("h2", { hasText: new RegExp(`^${index}\\. `) }) });
  const dmz = () => page.locator("#app-categories tbody tr", { hasText: "dmz-to-web" }).first();
  const table = page.locator("#appdoc-zones");
  // Zone mit dem Netz der Gegenstelle (10.10.20.0/24) – am Ende wieder gelöscht, ein Rest aus
  // einem abgebrochenen Versuch vorher
  const zones = (await (await page.request.get("/api/zones?tenant_id=1")).json()) as { id: number; name: string }[];
  for (const z of zones.filter((x) => x.name === "Partnernetz Test")) await page.request.delete(`/api/zones/${z.id}`);
  const created = await page.request.post("/api/zones", {
    data: { tenant_id: 1, name: "Partnernetz Test", kind: "internal", selectors: [{ kind: "prefix", value: "10.10.20.0/24" }] },
  });
  expect(created.status()).toBe(201);
  const zoneId = ((await created.json()) as { id: number }).id;
  try {
    await open(page);
    await categories();
    // ohne Kategorie an der Zone: unspezifisch, die Spalte Zone nennt sie schon
    await expect(card(10).locator("tbody tr", { hasText: "dmz-to-web" })).toHaveCount(1);
    await expect(card(10).locator("thead")).toContainText("Zone");
    await expect(dmz()).toContainText("Partnernetz Test");
    // zugeordnet wird bei den Zonen im Netzwerkdesign (Anwender 2026-10-28)
    await page.click("#app-zone-areas");
    await expect(page).toHaveURL(/#design\?tab=appdoc/);
    await expect(page.locator(".tabbar button.on")).toHaveText("Kategorien");
    const area = table.getByLabel("Kategorie der Zone Partnernetz Test");
    await area.selectOption("bit");
    await expect(area).toHaveValue("bit");
    await page.click("#appdoc-save");
    await expect(page.locator("#appdoc-saved")).toBeVisible();
    // Doku: die Kategorie heisst nach der Zone, im Kopf die Zone mit ihrem Netz
    await open(page);
    await categories();
    await expect(card(3).locator("tbody tr", { hasText: "dmz-to-web" })).toHaveCount(1);
    await expect(card(10).locator("tbody tr", { hasText: "dmz-to-web" })).toHaveCount(0);
    await expect(card(3).locator("h2")).toHaveText("3. Zugriff in Partnernetz Test");
    await expect(card(3).locator(".doc-zones")).toContainText("Partnernetz Test (10.10.20.0/24)");
    // Admin: Namen der Gegenstellen direkt in der Doku ein- und ausschalten
    const names = page.locator("#app-peer-names");
    await expect(names).not.toHaveClass(/\bon\b/);
    await names.click();
    await expect(names).toHaveClass(/\bon\b/);
    await expect(dmz()).toContainText("N_dmz-net (10.10.20.0/24)");
    await names.click();
    await expect(names).not.toHaveClass(/\bon\b/);
    await expect(dmz()).not.toContainText("N_dmz-net");
    // Netze mit Ausschluss: 10.10.16.0/20 ohne 10.10.20.0/24 – die Gegenstelle liegt nicht mehr in der Zone
    await page.goto("/#design?tab=appdoc");
    await table.getByLabel("Netze der Zone Partnernetz Test").fill("10.10.16.0/20");
    await table.getByLabel("Ausschlüsse der Zone Partnernetz Test").fill("10.10.20.0/24");
    await page.click("#appdoc-save");
    await expect(page.locator("#appdoc-saved")).toBeVisible();
    await expect(table.getByLabel("Ausschlüsse der Zone Partnernetz Test")).toHaveValue("10.10.20.0/24");
    await open(page);
    await categories();
    await expect(card(10).locator("tbody tr", { hasText: "dmz-to-web" })).toHaveCount(1);
    await expect(dmz()).not.toContainText("Partnernetz Test");
  } finally {
    await page.request.patch("/api/tenants/1", { data: { appdoc_foreign_names: false } });
    await page.request.delete(`/api/zones/${zoneId}`);
  }
});

test("Namenskonzept: eigene Kategorie mit Bereich, Auswahl im Netzwerkdesign, Titel in der Doku (0.104.0)", async ({ page }) => {
  const profiles = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  const pid = profiles.find((p) => p.name === "default")!.id;
  const catalogUrl = `/api/naming-profiles/${pid}/appdoc-catalog`;
  const zones = (await (await page.request.get("/api/zones?tenant_id=1")).json()) as { id: number; name: string }[];
  for (const z of zones.filter((x) => x.name === "Partnernetz Test")) await page.request.delete(`/api/zones/${z.id}`);
  await page.request.delete(catalogUrl);
  const created = await page.request.post("/api/zones", {
    data: { tenant_id: 1, name: "Partnernetz Test", kind: "internal", selectors: [{ kind: "prefix", value: "10.10.20.0/24" }] },
  });
  expect(created.status()).toBe(201);
  const zoneId = ((await created.json()) as { id: number }).id;
  try {
    // Namenskonzept: Kategorie 12 und Bereich „part“ dazu (Admin)
    await page.goto("/#dict");
    const card = page.locator("#appdoc-catalog");
    await expect(card.locator("#appdoc-catalog-categories tbody tr")).toHaveCount(11);
    await expect(page.locator("#appdoc-catalog-save")).toBeDisabled();
    await page.click("#appdoc-catalog-add-category");
    await card.getByLabel("Titel der Kategorie 12").fill("Zugriff Partner");
    await card.getByLabel("Titel englisch der Kategorie 12").fill("Partner access");
    await page.click("#appdoc-catalog-add-area");
    await card.getByLabel("Kürzel des Bereichs 7").fill("part");
    await card.getByLabel("Bezeichnung des Bereichs part").fill("Partner");
    await card.getByLabel("Kategorie eingehend des Bereichs part").selectOption("12");
    await card.getByLabel("Kategorie ausgehend des Bereichs part").selectOption("12");
    await card.getByLabel("Vorschlag des Bereichs part").fill("*partner*");
    await page.click("#appdoc-catalog-save");
    await expect(page.locator("#appdoc-catalog-saved")).toBeVisible();
    await expect(card.locator("#appdoc-catalog-categories tbody tr")).toHaveCount(12);

    // Netzwerkdesign: der Bereich steht zur Auswahl, Vorschlag nach dem Zonennamen
    await page.goto("/#design?tab=appdoc");
    const table = page.locator("#appdoc-zones");
    const area = table.getByLabel("Kategorie der Zone Partnernetz Test");
    await expect(area.locator("option", { hasText: "Partner (12)" })).toHaveCount(1);
    await expect(table.locator("tr", { hasText: "Partnernetz Test" })).toContainText("Vorschlag: Partner (12)");
    await page.click("#appdoc-suggest");
    await expect(area).toHaveValue("part");
    await page.click("#appdoc-save");
    await expect(page.locator("#appdoc-saved")).toBeVisible();

    // Doku: eigene Kategorie, benannt nach der Zone
    await open(page);
    await expect(page.locator("#app-categories > .card")).toBeVisible();
    if ((await page.locator("#app-categories .stack").count()) === 0) await page.locator("#app-categories h2.fold-title").first().click();
    const own = page.locator("#app-categories .stack > .card", { has: page.locator("h2", { hasText: /^12\. / }) });
    await expect(own.locator("h2")).toHaveText("12. Zugriff aus Partnernetz Test");
    await expect(own.locator("tbody tr", { hasText: "dmz-to-web" })).toHaveCount(1);
  } finally {
    await page.request.delete(`/api/zones/${zoneId}`);
    await page.request.delete(catalogUrl);
  }
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
  // Objekt der Regel (any bzw. Netz) statt einer Zeile je VM – unspezifisch nur die Regel,
  // die VMs darin auf Knopfdruck (0.37.0)
  const viaAny = ruleRows.filter({ hasText: "web-via-group" });
  await expect(viaAny).toHaveCount(1);
  await expect(viaAny.locator(".appside")).toContainText("any");
  await expect(viaAny.locator(".appside")).not.toContainText("web01-007-ka");
  await viaAny.locator(".appside .expandable button").click();
  await expect(viaAny.locator(".appside .expandable-list")).toContainText("web01-007-ka");
  await expect(page.locator(".drawer")).toHaveCount(0); // Knopf öffnet nicht das Detail
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
  await modal.locator("#ap-infra input").check(); // 0.88.0: Infrastruktur
  await modal.locator("button.btn-primary").click();
  await expect(page.locator(".app-head")).toContainText("Test");
  await expect(page.locator(".app-names")).toContainText("webshop-test");
  await expect(page.locator("#app-infra-chip")).toHaveText("Infrastruktur");
  // zurück: Zweck in der Liste
  await page.click("button:has-text('← Applikationen')");
  await expect(rows(page).filter({ hasText: "Webshop" })).toContainText("Test");
  // aufräumen für andere Tests
  await rows(page).filter({ hasText: "Webshop" }).click();
  await page.click("button:has-text('Bearbeiten')");
  await modal.locator("#ap-purpose").selectOption("prod");
  await modal.locator("#ap-int").fill("webshop-ka");
  await modal.locator("#ap-infra input").uncheck();
  await modal.locator("button.btn-primary").click();
  await expect(page.locator(".app-head")).toContainText("Prod");
  await expect(page.locator("#app-infra-chip")).toHaveCount(0);
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

test("Applikation: Doku nach Kategorien mit Aktion, Gerät und Link zur Regel", async ({ page }) => {
  await open(page);
  // zugeklappt, bis man sie öffnet (0.73.0)
  await expect(page.locator("#app-categories .stack")).toHaveCount(0);
  await page.locator("#app-categories h2.fold-title").click();
  const tables = page.locator(".stack .card table");
  await expect(tables.first().locator("thead")).toContainText("Aktion");
  await expect(tables.first().locator("thead")).toContainText("Gerät");
  await expect(tables.first().locator("thead")).not.toContainText("Bezug");
  // 0.84.0: Quelle und Ziel in Verkehrsrichtung statt „Server Name“
  await expect(tables.first().locator("thead")).toContainText("Source");
  await expect(tables.first().locator("thead")).toContainText("Destination");
  await expect(page.locator("#app-categories thead").first()).not.toContainText("Server Name");
  await expect(page.locator(".stack .chip.accept").first()).toHaveText("Allow");
  // unspezifische Regel (any, Netz): nur das Objekt der Regel, die Systeme auf Knopfdruck
  const broad = page.locator(".stack .card tbody tr", { has: page.locator(".expandable") }).first();
  await expect(broad.locator(".expandable-list")).toHaveCount(0);
  await broad.locator(".expandable button").click();
  await expect(broad.locator(".expandable-list")).toContainText("web01-007-ka");
  await page.locator(".stack .card tbody .linkbtn[title='Regel im Detail']").first().click();
  await expect(page.locator(".drawer")).toContainText("Warum diese Regel für die Applikation gilt");
});

test("Applikation: Findings nach Tenant, AP und EPG filtern (0.38.0)", async ({ page }) => {
  await open(page);
  const card = page.locator("#app-findings");
  const items = card.locator("tbody tr");
  const total = await items.count();
  expect(total).toBeGreaterThan(0);
  await expect(items.first()).toContainText("TE/AP/EG_db"); // je Befund seine EPGs
  const scope = card.locator("#finding-scope");
  await scope.getByLabel("Tenant").selectOption("TE");
  await expect(scope.getByLabel("AP").locator("option")).toHaveText(["AP: alle", "AP"]);
  await scope.getByLabel("AP").selectOption("AP");
  await scope.getByLabel("EPG").selectOption("EG_db");
  await expect(items).toHaveCount(total);
  await scope.locator("button:has-text('Filter zurücksetzen')").click();
  await expect(scope.getByLabel("Tenant")).toHaveValue("");
});

test("Applikationen: Vorschläge aus der Namenskonvention unter der Liste (0.38.0)", async ({ page }) => {
  // Vorschlag gestellt (die Beispieldaten haben alle Projekte zugeordnet)
  await page.route("**/api/applications/suggestions*", (route) =>
    route.fulfill({
      json: [
        { key: "TE|042", code: "042", tenant: "TE", name: "Projekt 042 · TE", application_id: null,
          members: [{ kind: "ap", ref: "TE/AP_042", label: "TE/AP_042", detail: null, project: "042", applications: [] }] },
        { key: "TF|042", code: "042", tenant: "TF", name: "Projekt 042 · TF", application_id: null,
          members: [{ kind: "ap", ref: "TF/AP_042", label: "TF/AP_042", detail: null, project: "042", applications: [] }] },
      ],
    }),
  );
  await page.goto("/#apps");
  const list = page.locator(".card", { has: page.locator("h2", { hasText: /^Applikationen$/ }) });
  const suggestions = page.locator("#app-suggestions");
  await expect(suggestions).toContainText("Projekt 042");
  // 0.74.1: gleiche Nummer in zwei Tenants = zwei Vorschläge
  await expect(suggestions.locator("tbody tr")).toHaveCount(2);
  await expect(suggestions.locator('tr[data-key="TF|042"]')).toContainText("TF");
  const [a, b] = [await list.boundingBox(), await suggestions.boundingBox()];
  expect(b!.y).toBeGreaterThan(a!.y + a!.height - 1);
});

test("Applikation: Verbindungen Ende zu Ende mit Weg, Regeln je Hop und Abweichung (0.45.0)", async ({ page }) => {
  await open(page);
  const card = page.locator("#app-connections");
  await expect(card).toContainText("Verbindungen Ende zu Ende");
  await expect(card.locator(".card-head")).toContainText("6 Verbindungen");
  await expect(card.locator(".card-head")).toContainText("1 Abweichungen");
  await expect(card).toContainText("ohne prüfbare Gegenstelle");
  // CT_rt: Subnetz nur für das Routing – die Doku zeigt den Contract, der Weg sperrt
  const row = card.locator("tbody tr.clickable").filter({ hasText: "EN_rt" });
  await expect(row).toContainText("Abweichung");
  await expect(row).toContainText("apic-01 · VRF TE:VRF1");
  await expect(row).toContainText("gesperrt");
  await expect(row).not.toContainText("+1"); // die EPG zählt nicht als weiteres System
  await row.click();
  const detail = card.locator("tr.detail-row");
  await expect(detail).toContainText("Contract CT_rt:SJ_1 erlaubt – der Weg ist gesperrt an apic-01 · VRF TE:VRF1");
  await expect(detail.locator(".conn-rules")).toContainText("greift nicht");
  // ohne gelesenes Routing: nicht prüfbar statt „nicht auf dem Weg“
  await card.locator("button.toggle", { hasText: "nur Abweichungen" }).click();
  await expect(card.locator("tbody tr.clickable")).toHaveCount(6);
  await card.locator("tbody tr.clickable").filter({ hasText: "H_db01" }).click();
  await expect(card.locator(".conn-rules")).toContainText("nicht prüfbar");
  // Zonen je Verbindung (0.78.0): das Ziel liegt in der Zone Internet
  await expect(card.locator("tbody tr.clickable").filter({ hasText: "H_db01" }).locator(".conn-zones")).toContainText("Zonen: Internet");
  // Export (Markdown) mit den Verbindungen und dem Weg je Hop (0.59.0) – seit 0.73.0 im Dialog
  await page.locator("#app-doc-export").click();
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator("#app-export-md").click()]);
  const text = readFileSync((await file.path())!, "utf-8");
  expect(text).toContain("## Verbindungen Ende zu Ende");
  expect(text).toContain("| Richtung | Quelle | Ziel | Dienst | Weg (entscheidende Regel) | Urteil | Zonen | Abweichung |");
  expect(text).toMatch(/H_db01.*\| Internet \|/); // Zone des Ziels (0.78.0)
  expect(text).toMatch(/EN_rt.*apic-01 · VRF TE:VRF1.*Contract CT_rt:SJ_1 erlaubt – der Weg ist gesperrt/);
  // Für Confluence (0.71.0): ZIP mit Word und dem Netzwerkplan als draw.io
  await page.locator("#app-doc-export").click();
  await expect(page.locator("#app-doc-confluence")).toHaveText("Word + draw.io (ZIP)");
  const [zip] = await Promise.all([page.waitForEvent("download"), page.locator("#app-doc-confluence").click()]);
  expect(zip.suggestedFilename()).toMatch(/^applikation-.+-\d{4}-\d\d-\d\d\.zip$/);
  const bytes = readFileSync((await zip.path())!);
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
  expect(bytes.toString("latin1")).toMatch(/\.docx/);
  expect(bytes.toString("latin1")).toMatch(/-netzwerkplan\.drawio/);
  // in die Weg-Suche mit Quelle, Ziel und Dienst
  await card.locator("button", { hasText: "In der Weg-Suche öffnen" }).click();
  await expect(page).toHaveURL(/#wege\?src=10\.10\.10\.5&dst=172\.16\.5\.9&service=tcp%2F443/);
  await expect(page.getByPlaceholder("Ziel: IP, Netz oder Name")).toHaveValue("172.16.5.9");
  await expect(page.getByPlaceholder("Dienst: tcp/443 oder FT_https")).toHaveValue("tcp/443");

  // seit 0.73.0 kein Befund: der Weg sperrt durchgehend (das hat einen Grund) – Befund nur bei
  // uneinheitlichen Wegen (Applikation „Portal Süd“)
  await page.goto("/#findings");
  const group = page.locator(".card.fgroup", { hasText: "Applikationen" });
  await expect(group).toContainText("Applikation: Wege uneinheitlich");
  await group.locator(".fline", { hasText: "Applikation: Wege uneinheitlich" }).click();
  await expect(page.locator("tbody tr", { hasText: "CT_rt" })).toHaveCount(0);
  const finding = page.locator("tbody tr", { hasText: "portal-https" }).first();
  await expect(finding).toContainText("je nach Weg offen bzw. gesperrt");
  await expect(finding).toContainText("über fw-09 erlaubt; über fw-10 gesperrt");
});

test("Applikation: Verbindungen nach Urteil, Regeln und Hinweisen filtern (0.80.0)", async ({ page }) => {
  await open(page);
  const card = page.locator("#app-connections");
  const rows = card.locator("tbody tr.clickable");
  await expect(rows).toHaveCount(6);
  const filter = card.locator("#app-conn-filter");
  await expect(filter).toContainText("Urteil");
  await expect(filter).toContainText("Regel auf dem Weg");
  // gesperrt: nur die gesperrten Verbindungen, Zahl am Knopf = Zeilen
  const deny = filter.locator("#conn-filter-verdict-deny");
  const n = Number(await deny.locator(".facet-count").innerText());
  expect(n).toBeGreaterThan(0);
  await deny.click();
  await expect(deny).toHaveClass(/on/);
  await expect(rows).toHaveCount(n);
  for (const r of await rows.all()) await expect(r.locator("td").nth(5)).toContainText("gesperrt");
  await expect(card.locator("#conn-filter-count")).toHaveText(`${n} von 6`);
  // zwischen den Gruppen „und“: gesperrt und Abweichung = CT_rt
  await filter.locator("#conn-filter-flag-mismatch").click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("EN_rt");
  // in der Gruppe „oder“: gesperrt oder abhängig
  await filter.locator("#conn-filter-flag-mismatch").click();
  const depends = filter.locator("#conn-filter-verdict-depends");
  const m = (await depends.count()) ? Number(await depends.locator(".facet-count").innerText()) : 0;
  if (m) await depends.click();
  await expect(rows).toHaveCount(n + m);
  await card.locator("#conn-filter-reset").click();
  await expect(rows).toHaveCount(6);
  // Regel auf dem Weg: „greift nicht“ (der Contract der Doku entscheidet nicht)
  await filter.locator("#conn-filter-state-other").click();
  await expect(rows.filter({ hasText: "EN_rt" })).toHaveCount(1);
  await card.locator("#conn-filter-reset").click();
  await expect(card.locator("#conn-filter-count")).toHaveCount(0);
});

test("Applikation: Wege uneinheitlich, Bereiche zuklappen, Export mit Auswahl (0.73.0)", async ({ page }) => {
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Portal Süd" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Portal Süd");
  // Verbund Süd: fw-09 erlaubt, fw-10 sperrt – je nach Weg offen bzw. gesperrt
  const card = page.locator("#app-connections");
  await expect(card.locator("#conn-inconsistent")).toHaveText("1 Wege uneinheitlich");
  const row = card.locator("tbody tr.clickable").first();
  await expect(row).toContainText("Wege uneinheitlich");
  await row.click();
  const ways = card.locator("#conn-variants");
  await expect(ways).toContainText("je nach Weg offen bzw. gesperrt");
  await expect(ways.locator("tr", { hasText: "fw-09" })).toContainText("erlaubt");
  await expect(ways.locator("tr", { hasText: "fw-10" })).toContainText("gesperrt");
  // Bereich zuklappen: bleibt nach dem Neuladen zu; alle aufklappen
  await page.locator("#app-systems h2.fold-title").click();
  await expect(page.locator("#app-systems table")).toHaveCount(0);
  await page.reload();
  await expect(page.locator("#app-systems h2.fold-title")).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#app-systems table")).toHaveCount(0);
  await page.locator("#app-folds-open").click();
  await expect(page.locator("#app-systems table")).toHaveCount(1);
  await expect(page.locator("#app-categories .stack")).toHaveCount(1);
  await page.locator("#app-folds-close").click();
  await expect(page.locator("#app-rules table")).toHaveCount(0);
  // Export mit Auswahl: ohne Regeln, die Auswahl bleibt
  await page.locator("#app-doc-export").click();
  const dialog = page.locator("#app-export-dialog");
  await dialog.locator("input[data-section='rules']").uncheck();
  await dialog.locator("input[data-section='plan']").uncheck();
  await expect(page.locator("#app-doc-confluence")).toHaveText("Word (.docx)");
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator("#app-export-md").click()]);
  const text = readFileSync((await file.path())!, "utf-8");
  expect(text).toContain("## Systeme");
  expect(text).toContain("## Verbindungen Ende zu Ende");
  expect(text).toContain("Wege uneinheitlich (aktiv über fw-09: erlaubt, aktiv über fw-10: gesperrt)");
  expect(text).toContain("## Befunde");
  expect(text).not.toContain("## Regeln & Contracts");
  await page.reload();
  await page.locator("#app-doc-export").click();
  await expect(dialog.locator("input[data-section='rules']")).not.toBeChecked();
  await expect(dialog.locator("input[data-section='systems']")).toBeChecked();
  // Word ohne Plan: nur die .docx
  const [docx] = await Promise.all([page.waitForEvent("download"), page.locator("#app-doc-confluence").click()]);
  expect(docx.suggestedFilename()).toMatch(/^applikation-portal-sued-\d{4}-\d\d-\d\d\.docx$/);
  // Drucken: gewählte Abschnitte offen, die übrigen fallen weg; danach wie vorher
  await page.evaluate(() => {
    window.print = () => document.body.setAttribute("data-printed", "1");
  });
  await page.locator("#app-doc-export").click();
  await page.locator("#app-export-print").click();
  await expect(page.locator("body")).toHaveAttribute("data-printed", "1");
  await expect(page.locator("#app-rules")).toHaveClass(/print-hide/);
  await expect(page.locator("#app-systems table")).toHaveCount(1); // zugeklappt, im Druck offen
  await page.evaluate(() => window.dispatchEvent(new Event("afterprint")));
  await expect(page.locator("#app-rules")).not.toHaveClass(/print-hide/);
  await expect(page.locator("#app-systems table")).toHaveCount(0);
});

test("Applikation: Netzwerkplan mit Knoten, Diensten, Interfaces und Export (0.67.0)", async ({ page }) => {
  await open(page);
  const card = page.locator("#app-connections");
  await expect(card.locator(".card-head")).toContainText("6 Verbindungen");
  await card.locator("#app-conn-plan").click();
  const svg = card.locator("#app-plan-svg");
  await expect(svg).toBeVisible();
  await expect(svg.locator("g.app-plan-node")).toHaveCount(7);
  await expect(svg.locator("g.app-plan-edge")).toHaveCount(6);
  await expect(svg.locator(".app-plan-col")).toHaveText(["Applikation", "Weg", "Gegenstellen"]);
  // 0.74.0 kompakt: die EPG mit ihrer VM im Rahmen des APs, das ACI als L3Out der externen EPG
  const node = (name: string) => svg.locator(`g.app-plan-node[data-node="${name}"]`);
  await expect(svg.locator('g.app-plan-group[data-group="TE/AP"]')).toBeVisible();
  await expect(node("EG_db").locator(".app-plan-vm")).toHaveText("• web01-007-ka");
  const own = await node("EG_db").boundingBox();
  const aci = await node("L3O_fw").boundingBox();
  const peer = await node("externe EPG L3O_fw|EN_rt (10.20.0.0/16)").boundingBox();
  expect(own!.x).toBeLessThan(aci!.x);
  expect(aci!.x).toBeLessThan(peer!.x);
  await expect(node("L3O_fw")).toHaveAttribute("data-kind", "l3out");
  await expect(node("L3O_fw")).toHaveAttribute("data-verdict", "deny");
  // je Linie die Dienste (← zur Applikation hin, ✕ vorher gesperrt) und die Interfaces
  const edge = (a: string, b: string) => svg.locator(`g.app-plan-edge[data-edge="${a} → ${b}"]`);
  const ext = edge("L3O_fw", "externe EPG L3O_fw|EN_rt (10.20.0.0/16)");
  await expect(ext).toHaveAttribute("data-verdict", "deny");
  await expect(ext.locator(".app-plan-label")).toHaveText("← tcp/22");
  const inner = edge("EG_db", "L3O_fw");
  await expect(inner).toHaveAttribute("data-verdict", "blocked");
  await expect(inner.locator(".app-plan-label")).toHaveText("✕ tcp/22");
  await expect(inner.locator(".app-plan-port")).toHaveText("vlan5");
  await expect(edge("EG_db", "N_clients (10.5.0.0/16)").locator(".app-plan-label")).toHaveText("← tcp/443, tcp/9000");
  // Ebene „Regeln“ unter dem L3Out; Ebenen ausblenden (Regeln, Dienste)
  const rules = svg.locator('g.app-plan-rulebox[data-node="L3O_fw"]');
  await expect(rules).toContainText("✕ implizit gesperrt");
  await card.locator("#app-plan-rules").uncheck();
  await expect(svg.locator("g.app-plan-rulebox")).toHaveCount(0);
  await card.locator("#app-plan-services").uncheck();
  await expect(svg.locator(".app-plan-label")).toHaveCount(0);
  await card.locator("#app-plan-services").check();
  // Zoom: Knöpfe ändern die Grösse, 100 % setzt zurück
  const width = Number(await svg.getAttribute("width"));
  await card.locator("#app-plan-zoom-in").click();
  expect(Number(await svg.getAttribute("width"))).toBeGreaterThan(width);
  await card.locator("#app-plan-zoom-reset").click();
  await expect(card.locator("#app-plan-zoom-reset")).toHaveText("100 %");
  expect(Number(await svg.getAttribute("width"))).toBe(width);
  // Suche der Karte hebt hervor, der Rest wird blass
  await card.getByPlaceholder("System, Gegenstelle, Firewall, Regel…").fill("EN_rt");
  await expect(svg.locator("g.app-plan-edge.dim")).toHaveCount(4);
  await expect(card.locator("#app-plan-export")).toContainText("Export (1 Verbindungen)");
  await card.getByPlaceholder("System, Gegenstelle, Firewall, Regel…").fill("");
  await expect(svg.locator("g.app-plan-edge.dim")).toHaveCount(0);
  // Klick auf einen Knoten: seine Verbindungen, Export nur mit ihnen (ohne Ebene „Regeln“)
  await node("L3O_fw").click();
  const focus = card.locator("#app-plan-focus");
  await expect(focus).toContainText("L3O_fw – 1 Verbindungen");
  const [file] = await Promise.all([
    page.waitForEvent("download"),
    card.locator("#app-plan-export button", { hasText: "draw.io" }).click(),
  ]);
  expect(file.suggestedFilename()).toMatch(/^netzwerkplan-webshop-\d{4}-\d{2}-\d{2}\.drawio$/);
  const xml = readFileSync((await file.path())!, "utf-8");
  expect(xml).toContain("Netzwerkplan Webshop");
  expect(xml).toContain("1 Verbindungen (von 6, gefiltert)");
  expect(xml).toContain('automato_ports_b="vlan5"');
  expect(xml).not.toContain("automato_rules");
  // Details öffnen die Verbindung in der Tabelle
  await focus.locator("button", { hasText: "Details" }).click();
  await expect(card.locator("#app-conn-table")).toHaveClass(/on/);
  await expect(card.locator("tr.detail-row")).toContainText("Contract CT_rt:SJ_1 erlaubt – der Weg ist gesperrt");
  // die gewählte Ansicht und die Ebenen bleiben
  await card.locator("#app-conn-plan").click();
  await page.reload();
  await expect(page.locator("#app-plan-svg")).toBeVisible();
  await expect(page.locator("#app-plan-rules")).not.toBeChecked();
});

test("Applikation: Regel in einer Kategorie ausblenden mit Begründung, Export, einblenden (0.69.0)", async ({ page }) => {
  await open(page);
  await page.locator("#app-categories h2.fold-title").click();
  // die erste Kategorie mit Regeln (über den Titel festhalten - die Zeile fällt gleich weg)
  const title = (await page.locator(".card", { has: page.locator(".doc-hide-btn") }).first().locator("h2").innerText()).trim();
  const card = page.locator(".card", { has: page.locator("h2", { hasText: title }) });
  const button = card.locator(".card-body > table tbody tr").first().locator(".doc-hide-btn");
  await expect(button).toBeVisible();
  const before = await card.locator(".card-body > table tbody tr").count();
  await button.click();
  const dialog = page.locator("#hide-dialog");
  await expect(dialog).toContainText("nur in Kategorie");
  const rule = (await dialog.locator("b").first().innerText()).trim();
  // Begründung ist Pflicht
  await expect(page.locator("#hide-submit")).toBeDisabled();
  await page.locator("#hide-reason").fill("Grundregel, nicht applikationsspezifisch");
  await page.locator("#hide-submit").click();
  await expect(dialog).toHaveCount(0);
  await expect(card.locator(".card-head")).toContainText("1 ausgeblendet");
  await expect(card.locator(".card-body > table tbody tr")).toHaveCount(before - 1);
  const hidden = card.locator(".doc-hidden");
  await hidden.locator("summary").click();
  await expect(hidden).toContainText("Grundregel, nicht applikationsspezifisch");
  await expect(hidden).toContainText(rule);
  await expect(hidden).toContainText("user:dev");
  // Export: Abschnitt „Ausgeblendete Regeln“ mit der Begründung
  await page.locator("#app-doc-export").click();
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator("#app-export-md").click()]);
  const text = readFileSync((await file.path())!, "utf-8");
  expect(text).toContain("## Ausgeblendete Regeln");
  expect(text).toContain("Grundregel, nicht applikationsspezifisch");
  // wieder einblenden
  await hidden.locator("button", { hasText: "einblenden" }).click();
  await expect(card.locator(".doc-hidden")).toHaveCount(0);
  await expect(card.locator(".card-body > table tbody tr")).toHaveCount(before);
});

test("Applikation: Regeln zuordnen bzw. ausschliessen (0.60.0)", async ({ page, problems }) => {
  // zweite Applikation mit dem Server H_db01 (172.16.5.9) – Webshop nutzt sie (Policy to-db);
  // Abhängigkeiten seit 0.88.0 wieder in der Doku: zugeklappt, erst beim Aufklappen geladen
  const created = await page.request.post("/api/applications?tenant_id=1", {
    data: { name: `Datenbank ${test.info().retry}`, members: [{ kind: "host", ref: "172.16.5.9" }] },
  });
  expect(created.status()).toBe(201);
  const db = (await created.json()) as { id: number; name: string };
  await open(page);
  await expect(page.locator("#app-rules")).toBeVisible();
  const deps = page.locator("#app-dependencies");
  await expect(deps.locator(".card-body")).toHaveCount(0);
  await deps.locator(".fold-title").click();
  await expect(page.locator("#app-deps-uses")).toContainText(db.name);
  await expect(page.locator("#app-deps-uses")).toContainText("to-db");
  await expect(page.locator("#app-deps-infra")).toHaveCount(0);
  // als Infrastruktur gekennzeichnet: steht eingeklappt im eigenen Teil
  expect((await page.request.patch(`/api/applications/${db.id}`, { data: { infrastructure: true } })).status()).toBe(200);
  await page.reload();
  await expect(page.locator("#app-deps-infra")).toBeVisible();
  await expect(page.locator("#app-deps-uses")).not.toContainText(db.name);
  await page.locator("#app-deps-infra summary").click();
  await expect(page.locator("#app-deps-infra-uses")).toContainText(db.name);
  await deps.locator(".fold-title").click(); // wieder zu (Zustand bleibt im Browser)

  // CT_rt ausschliessen: fällt aus der Liste, steht als ausgeschlossen da – und zurück
  const ct = page.locator("#app-rules tbody tr.clickable", { hasText: "CT_rt" });
  await ct.click();
  await page.locator("#app-rule-actions button", { hasText: "Aus der Applikation ausschliessen" }).click();
  await expect(page.locator("#app-rule-members")).toContainText("1 ausgeschlossen");
  await expect(ct).toHaveCount(0);
  await page.locator("#app-rule-actions button", { hasText: "Ausschluss aufheben" }).click();
  await expect(page.locator("#app-rule-members")).toHaveCount(0);
  await expect(ct).toHaveCount(1);
  await page.locator(".drawer button.close").click();

  // in Regeln & Contracts eine Regel ohne Treffer bei Webshop direkt zuordnen
  await page.goto("/#regeln");
  await rows(page).filter({ hasText: "lan-to-dmz-web" }).first().click();
  const assign = page.locator("#rule-assign-app");
  await assign.locator("select[aria-label='Applikation']").selectOption({ label: "Webshop" });
  await assign.locator("button", { hasText: "Zuordnen" }).click();
  await assign.locator("button", { hasText: "zugeordnet zu Webshop" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  const assigned = page.locator("#app-rules tbody tr.clickable", { hasText: "lan-to-dmz-web" });
  await expect(assigned).toContainText("zugeordnet");
  await page.locator("#app-rule-members button").first().click();
  await page.locator("#app-rule-members button", { hasText: "rückgängig" }).click();
  await expect(assigned).toHaveCount(0);

  expect((await page.request.delete(`/api/applications/${db.id}`)).status()).toBe(204);
  expect(problems).toEqual([]);
});

test("Applikationen: Stammdaten aus CSV, ohne VMs ausblenden und mehrere löschen (0.75.0)", async ({ page }) => {
  await page.goto("/#apps");
  const list = page.locator("#apps-list");
  await expect(list.locator("tbody tr", { hasText: "Webshop" })).not.toContainText("keine"); // hat eine VM
  await page.locator("#app-import").click();
  const csv = "Name;Verwendungszweck;Kritikalität;Kürzel\nImport A;Produktion;hoch;901\nImport B;irgendwas;;\n";
  await page.locator("#app-import-file").setInputFiles({ name: "apps.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  const counts = page.locator("#app-import-counts");
  await expect(counts).toContainText("1 neu");
  await expect(counts).toContainText("1 mit Fehler");
  await expect(page.locator('#app-import-rows tr[data-action="error"]')).toContainText("Zweck „irgendwas“ unbekannt");
  await expect(page.locator('#app-import-rows tr[data-action="create"]')).toContainText("Prod");
  await page.locator("#app-import-apply").click();
  const row = list.locator("tbody tr", { hasText: "Import A" });
  await expect(row).toContainText("901");
  await expect(row).toContainText("keine"); // ohne VMs
  // ohne VMs ausblenden (bleibt im Browser), danach wieder zeigen
  await page.locator("#apps-hide-empty").click();
  await expect(row).toHaveCount(0);
  await expect(list.locator("tbody tr", { hasText: "Webshop" })).toBeVisible();
  await page.reload();
  await expect(list.locator("tbody tr", { hasText: "Webshop" })).toBeVisible();
  await expect(list.locator("tbody tr", { hasText: "Import A" })).toHaveCount(0);
  await page.locator("#apps-hide-empty").click();
  // auswählen und löschen
  await list.locator("tbody tr", { hasText: "Import A" }).locator("td").first().click();
  await page.locator("#apps-delete").click();
  await expect(page.locator(".modal")).toContainText("Import A");
  await page.locator("#apps-delete-confirm").click();
  await expect(list.locator("tbody tr", { hasText: "Import A" })).toHaveCount(0);
  await expect(list.locator("tbody tr", { hasText: "Webshop" })).toBeVisible();
});

test("Publizierte Dienste: Virtual Service des NSX ALB mit Quelle und Pool (0.77.0)", async ({ page }) => {
  await page.goto("/#apps");
  await page.click("button:has-text('Publizierte Dienste')");
  const row = page.locator("tr[data-platform='avi']", { hasText: "vs-shop" });
  await expect(row).toContainText("10.10.10.80:443");
  await expect(row).toContainText("10.10.10.5:8443");
  await expect(row).toContainText("pool-shop · 1 Server");
  await expect(row).toContainText("avi-01");
  await expect(row).toContainText("NSX ALB");
  // 0.79.0: die VIP der FortiGate davor und umgekehrt der Virtual Service hinter ihr
  await expect(row.locator(".vip-chain")).toContainText("über V-EXT_lb.example.ch (203.0.113.30)");
  const front = page.locator("tr", { hasText: "V-EXT_lb.example.ch" }).first();
  await expect(front.locator(".vip-chain")).toContainText("→ NSX ALB vs-shop · 1 Server");
  await row.click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("Quelle");
  await expect(drawer).toContainText("NSX ALB (Avi)");
  await expect(drawer.locator(".vip-fronted")).toContainText("V-EXT_lb.example.ch");
  await page.locator(".drawer button[aria-label='Schliessen']").first().click();
  await front.click();
  await expect(page.locator(".drawer .vip-balancers")).toContainText("vs-shop");
  await expect(page.locator(".drawer .vip-balancers")).toContainText("10.10.10.5:8443");
});
