// Reservierungen von VLAN- und AS-Nummern und AS-Nummern einzeln (1.19.0, ADR 0066; Anwender 2026-10-30 Roadmap Version 2
// Nr. 10, 2026-11-17 Prio 6). Beispieldaten: Switches Zürich mit VLAN 10/20/30, fw-05 mit AS 65001 und dem ISP-Nachbarn
// (AS 64500). Jeder Test legt seine Reservierungen selbst an und gibt sie am Ende wieder frei.
import { expect, test } from "./fixtures";

test("Reservierungen: VLAN reservieren, Konflikt, ändern, Befund und freigeben", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await page.goto("/#reservierungen");
  await expect(page.locator(".page-head h1")).toContainText("Reservierungen");
  // VLAN 20 lesen die Switches schon: Konflikt
  await page.fill("#reservation-low", "20");
  await page.fill("#reservation-purpose", "E2E Konflikt");
  await page.click("#reservation-save");
  await expect(page.locator("#reservation-saved")).toContainText("schon gelesen in Gebrauch: 20");
  const conflict = page.locator("#reservations tbody tr", { hasText: "E2E Konflikt" });
  await expect(conflict.locator(".reservation-state")).toHaveText("Konflikt");
  await expect(conflict).toContainText("Switches Zürich");
  // ein freier Bereich an einem Standort
  await page.fill("#reservation-low", "3900");
  await page.fill("#reservation-high", "3909");
  await page.selectOption("#reservation-site", { label: "chzrh1 · Zürich RZ1" });
  await page.fill("#reservation-purpose", "E2E Projekt");
  await page.click("#reservation-save");
  await expect(page.locator("#reservation-saved")).toContainText("VLAN 3900–3909 reserviert – gelesen frei");
  const free = page.locator("#reservations tbody tr", { hasText: "E2E Projekt" });
  await expect(free.locator(".reservation-state")).toHaveText("frei");
  await expect(free).toContainText("Zürich RZ1");
  await expect(page.locator("#reservation-kpis")).toContainText("1 frei · 0 jetzt belegt · 1 Konflikt");
  // ändern
  const id = await free.getAttribute("data-reservation");
  await free.locator(".reservation-edit").click();
  await page.fill(`#reservation-edit-${id}-purpose`, "E2E Projekt neu");
  await page.click(`#reservation-edit-${id}-save`);
  await expect(page.locator("#reservations tbody tr", { hasText: "E2E Projekt neu" })).toContainText("3900–3909");

  // Befund mit Link zurück in die Ansicht
  await page.goto("/#findings");
  const group = page.locator(".fgroup", { hasText: "Reservierungen" });
  await expect(group).toContainText("Reservierung in Gebrauch (Konflikt)");
  await group.locator(".fline", { hasText: "Reservierung in Gebrauch" }).click();
  const row = page.locator("tbody tr", { hasText: "VLAN 20" }).first();
  await expect(row).toContainText("war schon beim Reservieren in Gebrauch");
  await row.click();
  await page.click("button.linkchip:has-text('Reservierung VLAN 20 öffnen')");
  await expect(page).toHaveURL(/#reservierungen\?tab=vlan&id=\d+/);
  await expect(page.locator("#reservations tbody tr.selected")).toContainText("E2E Konflikt");

  // freigeben: beide weg, der Befund auch
  for (const purpose of ["E2E Konflikt", "E2E Projekt neu"]) {
    await page.locator("#reservations tbody tr", { hasText: purpose }).locator(".reservation-release").click();
    await expect(page.locator("#reservations tbody tr", { hasText: purpose })).toHaveCount(0);
  }
  await page.goto("/#findings");
  await expect(page.locator(".fgroup", { hasText: "Reservierungen" })).toHaveCount(0);
});

test("AS-Nummern: je AS Nachbarn, Firewalls und Router; AS reservieren", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await page.goto("/#asn");
  const own = page.locator('.asn-card[data-asn="65001"]');
  await expect(own).toContainText("eigen");
  await expect(own.locator(".asn-device")).toContainText(["Firewall fw-05"]);
  const isp = page.locator('.asn-card[data-asn="64500"]');
  await expect(isp).toContainText("fremd");
  await expect(isp.locator(".asn-neighbor").first()).toContainText("198.51.100.1");
  await expect(isp.locator(".asn-neighbor").first()).toContainText("fw-05");
  // Filter nach Nummer
  await page.fill("#asn-q", "64500");
  await expect(page.locator(".asn-card")).toHaveCount(1);
  await page.fill("#asn-q", "");

  // AS reservieren: frei und in der AS-Ansicht als „Reserviert, nicht in Gebrauch“; 64500 ist ein Konflikt
  await page.goto("/#reservierungen?tab=asn");
  await page.fill("#reservation-low", "65530");
  await page.fill("#reservation-purpose", "E2E Standort");
  await page.click("#reservation-save");
  await expect(page.locator("#reservation-saved")).toContainText("AS 65530 reserviert – gelesen frei");
  await page.fill("#reservation-low", "64500");
  await page.fill("#reservation-purpose", "E2E ISP");
  await page.click("#reservation-save");
  await expect(page.locator("#reservations tbody tr", { hasText: "E2E ISP" }).locator(".reservation-state")).toHaveText("Konflikt");
  await page.goto("/#asn");
  await expect(page.locator("#asn-reserved-free")).toContainText("AS 65530 E2E Standort");
  await expect(page.locator('.asn-card[data-asn="64500"] .asn-reserved')).toContainText("E2E ISP");

  // aufräumen
  await page.goto("/#reservierungen?tab=asn");
  for (const purpose of ["E2E Standort", "E2E ISP"]) {
    await page.locator("#reservations tbody tr", { hasText: purpose }).locator(".reservation-release").click();
    await expect(page.locator("#reservations tbody tr", { hasText: purpose })).toHaveCount(0);
  }
});

test("Reservierungen und AS-Nummern auf Englisch", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#reservierungen");
  await expect(page.locator(".page-head h1")).toContainText("Reservations");
  await expect(page.locator("#reservation-kpis")).toContainText("VLANs reserved");
  await page.goto("/#asn");
  await expect(page.locator('.asn-card[data-asn="64500"]')).toContainText("foreign");
});
