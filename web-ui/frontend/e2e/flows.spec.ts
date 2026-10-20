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
