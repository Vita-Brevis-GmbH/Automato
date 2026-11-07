// 0.129.0 (Anwender 2026-11-03: „Kannst du mir in der Übersicht noch ein Fenster erstellen, wo man sieht, wie der Stand
// der Neuberechnungen der Dokumentationen ist?“): Bereich „Stand der Doku“ – je Applikation aktuell, veraltet, rechnet
// gerade bzw. noch nie berechnet; Klick öffnet die Applikation.
import { expect, test } from "./fixtures";

test("Übersicht: Stand der Doku je Applikation (0.129.0)", async ({ page }) => {
  // eine Doku öffnen, damit es einen Stand gibt
  const apps = (await (await page.request.get("/api/applications?tenant_id=1")).json()) as { id: number; name: string }[];
  const webshop = apps.find((a) => a.name === "Webshop")!;
  expect((await page.request.get(`/api/applications/${webshop.id}/doc`)).ok()).toBe(true);
  // 0.133.2: ohne Verbindungen gilt sie beim Vorrechnen aller Applikationen als „noch nie berechnet“
  expect((await page.request.get(`/api/applications/${webshop.id}/connections`)).ok()).toBe(true);
  await page.goto("/#uebersicht");
  const card = page.locator("#overview-grid [data-widget='docs'] #dash-docs");
  await expect(card).toContainText("Stand der Doku");
  for (const s of ["building", "stale", "never", "fresh"]) await expect(card.locator(`.kpi[data-tile='${s}']`)).toHaveCount(1);
  // aktuell nur mit allen Ständen (Doku deutsch/englisch, Verbindungen) - je nach vorherigen Tests veraltet (0.130.0)
  await expect(card.locator(".kpi[data-tile='fresh'] .k-val")).toHaveText(/^\d+$/);
  await expect(card.locator(".card-foot")).toContainText("zuletzt berechnet");
  // alle zeigen: Webshop mit seinem Stand, Klick öffnet die Doku
  // „alle zeigen“ nur, wenn nicht schon alle in der Liste stehen (alle bis 8 nicht aktuell: ohne Knopf)
  const all = card.getByRole("button", { name: /alle (\d+ )?zeigen/ });
  if (await all.isVisible()) await all.click();
  const row = card.locator("#docs-apps tbody tr[data-app='Webshop']");
  await expect(row).toHaveAttribute("data-state", /^(fresh|stale|building)$/);
  await row.getByRole("button", { name: "Webshop" }).click();
  await expect(page).toHaveURL(new RegExp(`#apps\\?app=${webshop.id}`));
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
});
