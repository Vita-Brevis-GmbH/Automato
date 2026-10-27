// Verlauf des Routings (ADR 0030): fw-05 hat in den Beispieldaten zwei Läufe (seed.py) - davor
// Default nur über wan1, BGP-Nachbar Idle und 10.98.0.0/16; danach ECMP, Established, ohne
// 10.98.0.0/16. Die übrigen Quellsysteme haben nur den Ausgangsstand.
import { expect, test } from "./fixtures";

test("Routing: Verlauf mit Änderungen von Lauf zu Lauf, Filter, Detail und Link zum Lauf", async ({ page }) => {
  await page.goto("/#routing");
  // Zahl im Kopf (7 Tage) öffnet den Reiter Verlauf
  await page.locator("#routing-changes-7d").click();
  await expect(page.locator(".tabbar button.on")).toHaveText("Verlauf");
  const list = page.locator("#routing-changes tbody tr");
  await expect(list.filter({ hasText: "Ausgangsstand" }).first()).toBeVisible();
  const ecmp = list.filter({ hasText: "fw-05 · VDOM root" }).filter({ hasText: "0.0.0.0/0" });
  await expect(ecmp).toContainText("198.51.100.1 via wan1 → 198.51.100.1 via wan1, 198.51.100.5 via wan2");
  await expect(list.filter({ hasText: "198.51.100.1" }).filter({ hasText: "BGP-Nachbar" })).toContainText(
    "Zustand: Idle → Established",
  );

  // Filter: nur Entfernte; Detail mit vorher/nachher je Feld
  await page.getByLabel("Änderung").selectOption("removed");
  await expect(list).toHaveCount(1);
  await expect(list.first()).toContainText("10.98.0.0/16 seq=9");
  await list.first().locator("button[aria-expanded]").click();
  const detail = page.locator("#routing-changes tr.change-detail");
  await expect(detail).toContainText("Distanz");
  await expect(detail).toContainText("10.20.0.254 via srv");

  // Suche nach einem Präfix, Vermerke
  await page.getByLabel("Änderung").selectOption("");
  await page.getByPlaceholder("Präfix, Next-Hop, Nachbar…").fill("wan2");
  await expect(list).toHaveCount(1);
  await page.getByPlaceholder("Präfix, Next-Hop, Nachbar…").fill("");
  await page.getByLabel("Änderung").selectOption("note");
  await expect(list.first()).toContainText("Ausgangsstand");
  await page.getByLabel("Art").selectOption("route");
  await expect(page.locator("#routing-changes")).toContainText("Keine Änderungen");

  // Link zum Lauf: Ansicht Läufe, Quellsystem gewählt, Lauf hervorgehoben
  await page.getByLabel("Art").selectOption("");
  await page.getByLabel("Änderung").selectOption("changed");
  await list.first().locator("a").click();
  await expect(page).toHaveURL(/#laeufe\?source_system_id=\d+&run=/);
  await expect(page.locator("tbody tr.selected")).toHaveCount(1);
});

test("Routing: Route mit „geändert“ öffnet ihren Verlauf", async ({ page }) => {
  await page.goto("/#routing");
  await page.click(".tabbar button:has-text('Routen')");
  const row = page.locator("tbody tr").filter({ hasText: "fw-05 · VDOM root" }).filter({ has: page.locator("button.linkchip") });
  await expect(row).toHaveCount(1); // nur die Default-Route der RIB hat sich geändert
  await row.locator("button.linkchip").click();
  await expect(page.locator(".tabbar button.on")).toHaveText("Verlauf");
  await expect(page.getByPlaceholder("Präfix, Next-Hop, Nachbar…")).toHaveValue("0.0.0.0/0");
  await expect(page.locator("#routing-changes tbody tr").first()).toContainText("Next-Hops");
});

test("Englisch: Verlauf des Routings mit Texten vom Server", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#routing?tab=changes");
  const list = page.locator("#routing-changes tbody tr");
  // die Zeile „Ausgangsstand“ nennt auch „1 BGP neighbor“ – daher Art und Änderung zusammen
  const peer = list.filter({ has: page.locator(".chip", { hasText: /^BGP neighbor$/ }) }).filter({ hasText: "changed" });
  await expect(peer.first()).toContainText("State: Idle → Established");
  await expect(list.filter({ hasText: "Baseline" }).first()).toContainText("the history starts here");
});
