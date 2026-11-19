// 1.17.0 (Anwender 2026-11-17: „Ein Shortcut wäre schön, wenn ich einfach wieder zu dem Kundenportal gelangen
// könnte.“): unter <kurzname>-doc.<domain> zeigt die Kopfzeile „Kundenportal“ – die Adresse der Startseite nennt
// der Server (/api/auth/config → portal_url, aus dem Kunden-Portal bzw. der .env einer eigenen Instanz), die
// Oberfläche rät sie nie aus ihrem Namen. Ohne Adresse vom Server kein Link.
import { expect, test } from "./fixtures";

test("Kopfzeile: Kundenportal mit der Adresse vom Server (1.17.0)", async ({ page }) => {
  const hosts: string[] = [];
  await page.route(/\/api\/auth\/config(\?|$)/, async (route) => {
    hosts.push(new URL(route.request().url()).searchParams.get("host") ?? "");
    const response = await route.fetch();
    const cfg = await response.json();
    await route.fulfill({ response, json: { ...cfg, portal_url: "https://kunde-a.example.ch" } });
  });
  await page.goto("/#uebersicht");
  const link = page.locator("header.topbar #portal-link");
  await expect(link).toHaveText("Kundenportal");
  await expect(link).toHaveAttribute("href", "https://kunde-a.example.ch");
  await expect(link).toHaveAttribute("title", "Zur Startseite des Kunden-Portals");
  expect(await link.getAttribute("target")).toBeNull(); // zurück im selben Tab
  // die Oberfläche nennt nur ihren Namen – welche Startseite dazu gehört, entscheidet der Server
  expect(hosts).toContain("localhost");
  // zweisprachig
  await page.evaluate(() => localStorage.setItem("automato_lang", "en"));
  await page.reload();
  await expect(link).toHaveText("Customer portal");
  await expect(link).toHaveAttribute("title", "To the start page of the customer portal");
});

test("Kopfzeile: ohne Adresse vom Server kein Kundenportal (1.17.0)", async ({ page }) => {
  await page.goto("/#uebersicht");
  await expect(page.locator("header.topbar .usermenu")).toBeVisible();
  await expect(page.locator("#portal-link")).toHaveCount(0);
});
