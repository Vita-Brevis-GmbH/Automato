// Statische Routen (1.19.0, Anwender 2026-11-17 Nr. 2 „statisches Routing“): eigene Umgebung „Umgebung Statisch“
// (seed.py, Daten aus test_static_routes) – fw-01 mit jedem Zustand (aktiv, Backup, überdeckt, nicht installiert,
// deaktiviert, Adressobjekt, Priorität, doppelt, Blackhole), fw-02 mit nicht lesbarer Routing-Tabelle, rt-01 nur mit
// Routing-Tabelle (Null0) und ein APIC mit statischen Routen am L3Out. Jeder Test läuft für sich.
import type { Page } from "@playwright/test";
import { expect, rows, test } from "./fixtures";

// Mandant „Umgebung Statisch“ wählen (Kennung über die API – unabhängig von der Reihenfolge im Seed)
async function useStatic(page: Page) {
  const tenants = (await (await page.request.get("/api/tenants")).json()) as { id: number; name: string }[];
  const id = tenants.find((t) => t.name === "Umgebung Statisch")?.id;
  expect(id, "Mandant „Umgebung Statisch“ im Seed").toBeTruthy();
  await page.addInitScript((tid) => localStorage.setItem("automato_tenant", tid), String(id));
}

const route = (page: Page, device: string, seq: string) =>
  page.locator(`#static-routes tr[data-seq='${seq}']`).filter({ hasText: device });

test("Statische Routen: Zustand je Route mit Begründung, Next-Hop und Grenzen (1.19.0)", async ({ page, problems }) => {
  await useStatic(page);
  await page.goto("/#statisch");
  await expect(page.locator("h1", { hasText: "Statische Routen" })).toBeVisible();
  const summary = page.locator("#static-summary");
  await expect(summary.locator("button[data-state='backup']")).toHaveText("4 Backup");
  await expect(summary.locator("button[data-state='shadowed']")).toHaveText("3 überdeckt");
  // bewusste Backup-Route: Distanz über dem Standard, BGP gewinnt
  const backup = route(page, "fw-01", "2");
  await expect(backup.locator(".static-state .chip")).toHaveText("Backup");
  await expect(backup.locator(".static-reason")).toContainText("Distanz 200 bewusst über dem Standard 10");
  // überdeckt: spezifischere Routen über das ganze Netz
  const covered = route(page, "fw-01", "7");
  await expect(covered.locator(".static-state .chip")).toHaveText("überdeckt");
  await expect(covered.locator(".static-reason")).toContainText("10.80.0.0/25, 10.80.0.128/25");
  // Backup-Route, deren Next-Hop nur über die Default-Route erreichbar ist
  const unreachable = route(page, "fw-01", "12");
  await expect(unreachable.locator(".static-nexthop")).toContainText("nur über die Default-Route");
  await expect(unreachable).toContainText("Statische Route: Next-Hop nicht erreichbar");
  // doppelt und Blackhole
  await expect(route(page, "fw-01", "5")).toContainText("Statische Route doppelt ×2");
  await expect(route(page, "fw-01", "10")).toContainText("Blackhole");
  // keine stillen Grenzen: nicht geprüft und nur die Routing-Tabelle gelesen
  await expect(page.locator("#static-unchecked")).toContainText("Routing-Tabelle nicht (ganz) lesbar");
  await expect(page.locator("#static-unchecked")).toContainText("Ziel ist ein Adressobjekt");
  await expect(page.locator("#static-rib-only")).toContainText("rt-01");
  // ACI: L3Out-Route, nicht das Bridge-Domain-Subnetz
  await expect(page.locator("#static-routes tr[data-prefix='10.95.0.0/16']")).toContainText("L3O_fw");
  await expect(page.locator("#static-routes tr[data-prefix='10.50.1.0/24']")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("Statische Routen: Filter nach Zustand, Suche, IP und Befund (1.19.0)", async ({ page, problems }) => {
  await useStatic(page);
  await page.goto("/#statisch");
  const table = page.locator("#static-routes tbody tr");
  await expect(table.first()).toBeVisible();
  // Zustand über die Auswahl
  await page.locator(".filterbar select[aria-label='Zustand']").selectOption("shadowed");
  await expect(table).toHaveCount(3);
  // Zustand über die Übersicht (Klick filtert)
  await page.locator(".filterbar select[aria-label='Zustand']").selectOption("");
  await page.locator("#static-summary button[data-state='backup']").click();
  await expect(table).toHaveCount(4);
  await expect(page.locator(".filterbar select[aria-label='Zustand']")).toHaveValue("backup");
  await page.locator(".filterbar select[aria-label='Zustand']").selectOption("");
  // Suche: Adressobjekt mit seinen Netzen
  await page.getByPlaceholder("Präfix, Next-Hop, Gerät, Kommentar…").fill("N_partner");
  await expect(table).toHaveCount(1);
  await expect(table.first()).toContainText("10.85.0.0/16");
  await page.getByPlaceholder("Präfix, Next-Hop, Gerät, Kommentar…").fill("");
  // enthält IP: längstes Präfix zuerst
  await page.locator(".filterbar select[aria-label='Kontext']").selectOption({ label: "fw-01" });
  await page.getByPlaceholder("enthält IP…").fill("10.80.0.5");
  await expect(table).toHaveCount(3);
  await expect(table.nth(0)).toHaveAttribute("data-prefix", "10.80.0.0/25");
  await expect(table.nth(1)).toHaveAttribute("data-prefix", "10.80.0.0/24");
  await page.getByPlaceholder("enthält IP…").fill("");
  // nur mit Befund
  await page.locator("#static-issues").check();
  await expect(table.filter({ hasText: "Statische Route überdeckt" })).toHaveCount(3);
  await expect(table.filter({ hasText: "10.60.0.0/24" })).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("Statische Routen: Befund öffnet die Route (1.19.0)", async ({ page, problems }) => {
  await useStatic(page);
  await page.goto("/#findings");
  const group = page.locator(".fgroup", { hasText: "Statische Routen" });
  await expect(group).toContainText("Statische Route doppelt");
  await expect(group).toContainText("Blackhole-Route");
  await page.locator("tbody tr.clickable", { hasText: "10.70.0.0/24" }).first().click();
  await page.locator("tbody tr.subrow button", { hasText: "Statische Routen fw-01" }).click();
  await expect(page).toHaveURL(/#statisch/);
  const table = page.locator("#static-routes tbody tr");
  await expect(table).toHaveCount(2);
  await expect(table.first()).toContainText("Statische Route doppelt ×2");
  expect(problems).toEqual([]);
});

test("Englisch: statische Routen (1.19.0)", async ({ page, problems }) => {
  await useStatic(page);
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#statisch");
  await expect(page.locator("h1", { hasText: "Static routes" })).toBeVisible();
  await expect(page.locator("#static-summary button[data-state='backup']")).toHaveText("4 backup");
  await expect(route(page, "fw-01", "2").locator(".static-reason")).toContainText("deliberately above the default 10");
  expect(problems).toEqual([]);
});

test("Applikation: statische Routen auf den Wegen (1.19.0)", async ({ page, problems }) => {
  await useStatic(page);
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Statisch App" }).click();
  const list = page.locator("#app-connections #conn-statics");
  await expect(list).toContainText("Statische Routen auf den Wegen (1)");
  await list.locator("button.linkbtn").click();
  const row = list.locator("tbody tr");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("fw-01");
  await expect(row).toContainText("10.2.0.254");
  await expect(row).toContainText("Seq 4");
  await expect(row.locator(".chip")).toHaveText("aktiv");
  // Link öffnet die Route in „Statische Routen“
  await row.locator("a", { hasText: "10.60.0.0/24" }).click();
  await expect(page).toHaveURL(/#statisch/);
  await expect(page.locator("#static-routes tbody tr")).toHaveCount(1);
  await expect(page.locator("#static-routes tbody tr")).toHaveAttribute("data-seq", "4");
  expect(problems).toEqual([]);
});
