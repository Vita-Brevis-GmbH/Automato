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
  // zugeklappt, bis man sie öffnet (0.73.0)
  await expect(page.locator("#app-categories .stack")).toHaveCount(0);
  await page.locator("#app-categories h2.fold-title").click();
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
  // Export (Markdown) mit den Verbindungen und dem Weg je Hop (0.59.0) – seit 0.73.0 im Dialog
  await page.locator("#app-doc-export").click();
  const [file] = await Promise.all([page.waitForEvent("download"), page.locator("#app-export-md").click()]);
  const text = readFileSync((await file.path())!, "utf-8");
  expect(text).toContain("## Verbindungen Ende zu Ende");
  expect(text).toContain("| Richtung | Quelle | Ziel | Dienst | Weg (entscheidende Regel) | Urteil | Abweichung |");
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
  expect(text).toContain("## Findings");
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
  // Abhängigkeiten seit 0.73.0 nicht mehr in der Doku (Anwender: vorerst entfernt)
  const created = await page.request.post("/api/applications?tenant_id=1", {
    data: { name: `Datenbank ${test.info().retry}`, members: [{ kind: "host", ref: "172.16.5.9" }] },
  });
  expect(created.status()).toBe(201);
  const db = (await created.json()) as { id: number; name: string };
  await open(page);
  await expect(page.locator("#app-rules")).toBeVisible();
  await expect(page.locator("#app-dependencies")).toHaveCount(0);

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
  await row.click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("Quelle");
  await expect(drawer).toContainText("NSX ALB (Avi)");
});
