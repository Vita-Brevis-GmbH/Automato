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
  await expect(vm).toContainText("Portgruppe TE|AP|EG_db → EPG TE/AP/EG_db (AP TE/AP)");
  // je NIC die Portgruppe mit ihrer EPG (0.43.1)
  await expect(vm).toContainText("→ TE/AP/EG_db");
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

test("Applikation: Doku nach Kategorien mit Aktion, Gerät und Link zur Regel", async ({ page }) => {
  await open(page);
  const head = page.locator(".card-head", { hasText: "Doku nach Kategorien" });
  await head.locator("button:has-text('anzeigen')").click();
  const tables = page.locator(".stack .card table");
  await expect(tables.first().locator("thead")).toContainText("Aktion");
  await expect(tables.first().locator("thead")).toContainText("Gerät");
  await expect(tables.first().locator("thead")).not.toContainText("Bezug");
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
      json: [{ code: "042", name: "Projekt 042", application_id: null,
               members: [{ kind: "ap", ref: "TE/AP_042", label: "TE/AP_042", detail: null, project: "042", applications: [] }] }],
    }),
  );
  await page.goto("/#apps");
  const list = page.locator(".card", { has: page.locator("h2", { hasText: /^Applikationen$/ }) });
  const suggestions = page.locator("#app-suggestions");
  await expect(suggestions).toContainText("Projekt 042");
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
  // Export (Markdown) mit den Verbindungen und dem Weg je Hop (0.59.0)
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator("#app-doc-export").click()]);
  const text = readFileSync((await file.path())!, "utf-8");
  expect(text).toContain("## Verbindungen Ende zu Ende");
  expect(text).toContain("| Richtung | Quelle | Ziel | Dienst | Weg (entscheidende Regel) | Urteil | Abweichung |");
  expect(text).toMatch(/EN_rt.*apic-01 · VRF TE:VRF1.*Contract CT_rt:SJ_1 erlaubt – der Weg ist gesperrt/);
  // in die Weg-Suche mit Quelle, Ziel und Dienst
  await card.locator("button", { hasText: "In der Weg-Suche öffnen" }).click();
  await expect(page).toHaveURL(/#wege\?src=10\.10\.10\.5&dst=172\.16\.5\.9&service=tcp%2F443/);
  await expect(page.getByPlaceholder("Ziel: IP, Netz oder Name")).toHaveValue("172.16.5.9");
  await expect(page.getByPlaceholder("Dienst: tcp/443 oder FT_https")).toHaveValue("tcp/443");

  // Abweichung als Befund (0.59.0): Kategorie Applikationen, Satz mit der Verbindung
  await page.goto("/#findings");
  const group = page.locator(".card.fgroup", { hasText: "Applikationen" });
  await expect(group).toContainText("Applikation: Regel ↔ Weg");
  await group.locator(".fline", { hasText: "Applikation: Regel ↔ Weg" }).click();
  const finding = page.locator("tbody tr", { hasText: "CT_rt" }).first();
  await expect(finding).toContainText("erlaubt für die Applikation „Webshop“");
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
  // links das System, in der Mitte die ACI-VRF (sperrt), rechts die Gegenstellen
  const node = (name: string) => svg.locator(`g.app-plan-node[data-node="${name}"]`);
  const own = await node("web01-007-ka").boundingBox();
  const aci = await node("apic-01 · VRF TE:VRF1").boundingBox();
  const peer = await node("externe EPG L3O_fw|EN_rt (10.20.0.0/16)").boundingBox();
  expect(own!.x).toBeLessThan(aci!.x);
  expect(aci!.x).toBeLessThan(peer!.x);
  await expect(node("apic-01 · VRF TE:VRF1")).toHaveAttribute("data-verdict", "deny");
  // je Linie die Dienste (← zur Applikation hin, ✕ vorher gesperrt) und die Interfaces
  const edge = (a: string, b: string) => svg.locator(`g.app-plan-edge[data-edge="${a} → ${b}"]`);
  const ext = edge("apic-01 · VRF TE:VRF1", "externe EPG L3O_fw|EN_rt (10.20.0.0/16)");
  await expect(ext).toHaveAttribute("data-verdict", "deny");
  await expect(ext.locator(".app-plan-label")).toHaveText("← tcp/22");
  const inner = edge("web01-007-ka", "apic-01 · VRF TE:VRF1");
  await expect(inner).toHaveAttribute("data-verdict", "blocked");
  await expect(inner.locator(".app-plan-label")).toHaveText("✕ tcp/22");
  await expect(inner.locator(".app-plan-port")).toHaveText("vlan5");
  await expect(edge("web01-007-ka", "N_clients (10.5.0.0/16)").locator(".app-plan-label")).toHaveText("← tcp/443, tcp/9000");
  // Suche der Karte hebt hervor, der Rest wird blass
  await card.getByPlaceholder("System, Gegenstelle, Firewall, Regel…").fill("EN_rt");
  await expect(svg.locator("g.app-plan-edge.dim")).toHaveCount(4);
  await expect(card.locator("#app-plan-export")).toContainText("Export (1 Verbindungen)");
  await card.getByPlaceholder("System, Gegenstelle, Firewall, Regel…").fill("");
  await expect(svg.locator("g.app-plan-edge.dim")).toHaveCount(0);
  // Klick auf einen Knoten: seine Verbindungen, Export nur mit ihnen
  await node("apic-01 · VRF TE:VRF1").click();
  const focus = card.locator("#app-plan-focus");
  await expect(focus).toContainText("apic-01 · VRF TE:VRF1 – 1 Verbindungen");
  const [file] = await Promise.all([
    page.waitForEvent("download"),
    card.locator("#app-plan-export button", { hasText: "draw.io" }).click(),
  ]);
  expect(file.suggestedFilename()).toMatch(/^netzwerkplan-webshop-\d{4}-\d{2}-\d{2}\.drawio$/);
  const xml = readFileSync((await file.path())!, "utf-8");
  expect(xml).toContain("Netzwerkplan Webshop");
  expect(xml).toContain("1 Verbindungen (von 6, gefiltert)");
  expect(xml).toContain('automato_ports_b="vlan5"');
  // Details öffnen die Verbindung in der Tabelle
  await focus.locator("button", { hasText: "Details" }).click();
  await expect(card.locator("#app-conn-table")).toHaveClass(/on/);
  await expect(card.locator("tr.detail-row")).toContainText("Contract CT_rt:SJ_1 erlaubt – der Weg ist gesperrt");
  // die gewählte Ansicht bleibt
  await card.locator("#app-conn-plan").click();
  await page.reload();
  await expect(page.locator("#app-plan-svg")).toBeVisible();
});

test("Applikation: Regel in einer Kategorie ausblenden mit Begründung, Export, einblenden (0.69.0)", async ({ page }) => {
  await open(page);
  const head = page.locator(".card", { has: page.locator("h2", { hasText: "Doku nach Kategorien" }) });
  await head.locator("button", { hasText: "anzeigen" }).click();
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
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator("#app-doc-export").click()]);
  const text = readFileSync((await file.path())!, "utf-8");
  expect(text).toContain("## Ausgeblendete Regeln");
  expect(text).toContain("Grundregel, nicht applikationsspezifisch");
  // wieder einblenden
  await hidden.locator("button", { hasText: "einblenden" }).click();
  await expect(card.locator(".doc-hidden")).toHaveCount(0);
  await expect(card.locator(".card-body > table tbody tr")).toHaveCount(before);
});

test("Applikation: Regeln zuordnen bzw. ausschliessen, Abhängigkeiten (0.60.0)", async ({ page, problems }) => {
  // zweite Applikation mit dem Server H_db01 (172.16.5.9) – Webshop nutzt sie (Policy to-db)
  const created = await page.request.post("/api/applications?tenant_id=1", {
    data: { name: `Datenbank ${test.info().retry}`, members: [{ kind: "host", ref: "172.16.5.9" }] },
  });
  expect(created.status()).toBe(201);
  const db = (await created.json()) as { id: number; name: string };
  await open(page);
  await expect(page.locator("#app-dependencies #app-deps-uses")).toContainText(db.name);

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
