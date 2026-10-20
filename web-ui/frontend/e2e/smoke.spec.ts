// Jede Ansicht lädt ohne Fehler (Seitenfehler/API-Fehler) und zeigt ihren Titel.
import { expect, test } from "./fixtures";

const VIEWS: [string, string][] = [
  ["uebersicht", "Übersicht"],
  ["regeln", "Regeln & Contracts"],
  ["objekte", "Objekte & Inventar"],
  ["nat", "NAT / VIPs"],
  ["apps", "Applikationen"],
  ["findings", "Findings"],
  ["abgleich", "Abgleich"],
  ["design", "Netzwerkdesign"],
  ["users", "Benutzer & Rollen"],
  ["tenants", "Mandanten & Standorte"],
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

test("Objekte: alle Reiter laden", async ({ page, problems }) => {
  await page.goto("/#objekte");
  for (const tab of ["Adressobjekte", "Gruppen", "Services", "Subnetze", "VLANs", "Ports", "Geräte", "VRFs"]) {
    await page.click(`.tabbar button:has-text('${tab}')`);
    await page.waitForLoadState("networkidle");
  }
  expect(problems).toEqual([]);
});
