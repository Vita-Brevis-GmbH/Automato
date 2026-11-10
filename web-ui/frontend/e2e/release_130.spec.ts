// 1.3.0 (Anwender 2026-11-10: „Ich möchte das Erstellen und Verwalten von Kunden im Cockpit haben“ und „für das
// Login jeweils einen eigenen Realm“): Kunden-Portal unter Administration – Kunde mit Werkzeugen, Realm und Kachel
// anlegen, Einrichtungs-Befehle (NetBox, Keycloak-Realm) sehen, ändern und entfernen.
import { expect, test } from "./fixtures";

test("Kunden-Portal: anlegen, Einrichtung, ändern, entfernen (1.3.0)", async ({ page, problems }) => {
  await page.goto("/#kundenportal");
  await expect(page.getByRole("heading", { name: "Kunden-Portal" })).toBeVisible();
  // ohne Ordner des Host-Agenten: nur gespeichert, mit Hinweis
  await expect(page.locator("#portal-agent")).toContainText("Host-Agent nicht eingerichtet");

  await page.click("button:has-text('+ Kunde')");
  await page.fill("#pc-short", "kunde-e2e");
  await page.fill("#pc-name", "Kunde E2E");
  await page.getByLabel("Doku (Automato, -doc)").check();
  await page.fill("#pc-realm", "kunde-e2e");
  await page.click("button:has-text('+ Kachel')");
  await page.locator("#portal-links input").nth(0).fill("Handbuch");
  await page.locator("#portal-links input").nth(1).fill("https://handbuch.example.ch");
  await page.click(".modal-foot button:has-text('Speichern')");

  const row = page.locator("#portal-customers tbody tr", { hasText: "kunde-e2e" }).first();
  await expect(row).toContainText("Kunde E2E");
  await expect(row).toContainText("NetBox, Doku");
  await expect(row).toContainText("Realm kunde-e2e");

  await row.locator("button:has-text('Einrichtung')").click();
  const setup = page.locator(".portal-setup");
  await expect(setup).toContainText("netbox-host.sh neu kunde-e2e");
  await expect(setup).toContainText("make_realm.py --realm kunde-e2e");
  // 1.4.0 (ADR 0051): Anmelde-Tor – Client des Realms mit allen Werkzeugen, Secret auf der VM
  await expect(setup).toContainText("--front-kunde kunde-e2e");
  await expect(setup).toContainText("front.sh tor kunde-e2e");
  await expect(setup).toContainText("netbox-host.sh sso kunde-e2e");

  // reservierte Kurznamen lehnt der Server ab
  await row.locator("button:has-text('Bearbeiten')").click();
  await page.fill("#pc-short", "kunde-doc");
  await page.click(".modal-foot button:has-text('Speichern')");
  await expect(page.locator(".modal .error")).toContainText("reserviert");
  // die abgelehnte Änderung (422) ist hier gewollt
  problems.splice(0, problems.length, ...problems.filter((p) => !/^422 .*\/api\/portal\/customers\/\d+$/.test(p)));
  await page.fill("#pc-short", "kunde-e2e");
  await page.getByLabel("Benutzerverwaltung (-um)").check();
  await page.click(".modal-foot button:has-text('Speichern')");
  await expect(row).toContainText("NetBox, Doku, UM");

  page.once("dialog", (d) => d.accept());
  await row.locator("button:has-text('Entfernen')").click();
  await expect(page.locator("#portal-customers tbody tr", { hasText: "kunde-e2e" })).toHaveCount(0);
});
