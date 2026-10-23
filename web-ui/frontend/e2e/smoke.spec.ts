// Jede Ansicht lädt ohne Fehler (Seitenfehler/API-Fehler) und zeigt ihren Titel.
import { expect, test } from "./fixtures";

const VIEWS: [string, string][] = [
  ["uebersicht", "Übersicht"],
  ["laeufe", "Läufe"],
  ["regeln", "Regeln & Contracts"],
  ["objekte", "Objekte & Inventar"],
  ["nat", "NAT / VIPs"],
  ["wege", "Weg-Suche"],
  ["routing", "Routing"],
  ["apps", "Applikationen"],
  ["findings", "Findings"],
  ["abgleich", "Abgleich"],
  ["design", "Netzwerkdesign"],
  ["users", "Benutzer & Rollen"],
  ["tenants", "Mandanten & Standorte"],
  ["kunden", "Kunden"],
  ["dict", "Namensprofile"],
  ["agents", "Collector-Agenten"],
  ["netbox", "NetBox-Sync"],
  ["system", "System"],
];

for (const [key, title] of VIEWS) {
  test(`Ansicht ${title}`, async ({ page, problems }) => {
    await page.goto(`/#${key}`);
    await expect(page.locator(".page-head h1, h1").first()).toContainText(title);
    await page.waitForLoadState("networkidle");
    expect(problems).toEqual([]);
  });
}

test("Routing: alle Reiter laden", async ({ page, problems }) => {
  await page.goto("/#routing");
  for (const tab of ["Routen", "BGP-Nachbarn", "HA & Session-Sync", "Policy-Routen & SD-WAN", "L3Outs (ACI)", "Anbindungen", "Topologie"]) {
    await page.click(`.tabbar button:has-text('${tab}')`);
    await page.waitForLoadState("networkidle");
  }
  expect(problems).toEqual([]);
});

test("Objekte: alle Reiter laden", async ({ page, problems }) => {
  await page.goto("/#objekte");
  for (const tab of ["Adressobjekte", "Gruppen", "Services", "Subnetze", "VLANs", "Ports", "Geräte", "VRFs", "VMs", "Portgruppen"]) {
    await page.click(`.tabbar button:has-text('${tab}')`);
    await page.waitForLoadState("networkidle");
  }
  expect(problems).toEqual([]);
});
