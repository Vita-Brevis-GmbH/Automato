// 0.111.0 (Rückmeldung des Anwenders): grosse Netzlisten bei Anbindungen einfügen und grosse Netze der Regeln zuordnen
// (ADR 0040). Beide Tests räumen am Ende auf – die übrigen Tests sehen die Beispieldaten unverändert.
import { expect, test } from "./fixtures";

test("Anbindung: Netzliste einfügen, prüfen, zusammenfassen (0.111.0)", async ({ page, problems }) => {
  try {
    await page.goto("/#routing");
    await page.click(".tabbar button:has-text('Anbindungen')");
    await page.click("button:has-text('Neue Anbindung')");
    await page.locator("#ex-name").fill("Netzliste Test");
    await page.locator("#ex-hops").fill("10.70.0.1");
    const list = [
      "MPLS Kunde A: 10.80.0.0/16, 10.81.0.0 255.255.0.0",
      "10.80.0.0/16 (doppelt)",
      ...[0, 1, 2, 3].map((i) => `172.16.${i}.0/24 Standort ${i}`),
      "10.1.2.300/24",
    ].join("\n");
    await page.locator("#ex-nets").fill(list);
    await page.click("#ex-nets-check");
    const summary = page.locator("#ex-nets-summary");
    await expect(summary).toContainText("6 Netze · 1 doppelt");
    await expect(summary).toContainText("ungültig: 10.1.2.300/24");
    // Speichern mit Ungültigem: Hinweis, nichts gespeichert
    await page.click("#exit-form button:has-text('Speichern')");
    await expect(page.locator("#exit-form")).toContainText("Netze dahinter: ungültig – 10.1.2.300/24");
    // ohne den Fehler: zusammenfassen 6 → 2
    await page.locator("#ex-nets").fill(list.replace("10.1.2.300/24", ""));
    await page.click("#ex-nets-check");
    await page.click("#ex-nets-collapse");
    await expect(page.locator("#ex-nets")).toHaveValue("10.80.0.0/15\n172.16.0.0/22");
    // Vorschlag aus dem Routing (über den Next-Hop gelernte Netze)
    await page.click("#ex-nets-learned");
    await expect(page.locator("#ex-nets-learned-list")).toBeVisible();
    await page.click("#exit-form button:has-text('Speichern')");
    const row = page.locator(".card", { hasText: "Manuelle Routen" }).locator("tbody tr", { hasText: "Netzliste Test" });
    await expect(row).toContainText("10.80.0.0/15");
  } finally {
    const exits = (await (await page.request.get("/api/routing/exits?tenant_id=1")).json()) as { id: number; name: string }[];
    for (const e of exits.filter((x) => x.name === "Netzliste Test")) await page.request.delete(`/api/routing/exits/${e.id}`);
  }
  expect(problems).toEqual([]);
});

test("Netz der Regel grösser als die Routen: Befund, zuordnen, entfernen (0.111.0)", async ({ page, problems }) => {
  try {
    await page.goto("/#findings?type=net_wider_than_routes");
    const row = page.locator("tbody tr.clickable", { hasText: "10.1.0.0/24" });
    await expect(row).toContainText("Netz der Regel grösser als die Routen");
    await expect(row).toContainText("routet 10.1.0.0/24 nicht als Ganzes");
    await row.click();
    await page.click("button:has-text('Netz zuordnen')");
    await expect(page.locator(".modal")).toContainText("Netz 10.1.0.0/24 zuordnen");
    await page.locator("#net-assign-kind label", { hasText: "Weg nach dem Routing stimmt" }).locator("input").check();
    await page.locator("#net-assign-note").fill("Test");
    await page.click("#net-assign-save");
    await expect(page.locator(".modal")).toHaveCount(0);
    // Liste unter Routing → Anbindungen; ändern öffnet denselben Dialog mit „Zuordnung entfernen“
    await page.goto("/#routing");
    await page.click(".tabbar button:has-text('Anbindungen')");
    const assigned = page.locator("#net-assignments tr[data-prefix='10.1.0.0/24']");
    await expect(assigned).toContainText("Weg nach dem Routing stimmt");
    await expect(assigned).toContainText("Test");
    await assigned.locator("button", { hasText: "ändern" }).click();
    await page.click("#net-assign-delete");
    await expect(page.locator("#net-assignments")).toHaveCount(0);
  } finally {
    const rows = (await (await page.request.get("/api/routing/net-assignments?tenant_id=1")).json()) as { id: number }[];
    for (const r of rows) await page.request.delete(`/api/routing/net-assignments/${r.id}`);
  }
  expect(problems).toEqual([]);
});
