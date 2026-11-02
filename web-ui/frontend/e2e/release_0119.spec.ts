// 0.119.0 (Rückmeldung des Anwenders 2026-11-02): Profile des Regelwerks mit Namen und Beschreibung (auch die eingebauten),
// löschen auch eingebaute (ausgeblendet, wiederherstellbar), nie das aktive.
import { expect, test } from "./fixtures";

test("Regelwerk: Name und Beschreibung, eingebautes löschen und wiederherstellen (0.119.0)", async ({ page }) => {
  // eigenes Namensprofil ohne Mandant: wirkt auf keine Doku der anderen UI-Tests
  const list = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  let pid = list.find((p) => p.name === "Regelwerk-Namen")?.id;
  if (pid === undefined) {
    const created = await page.request.post("/api/naming-profiles", { data: { customer: "Kunde Regelwerk", name: "Regelwerk-Namen" } });
    expect(created.status()).toBe(201);
    pid = ((await created.json()) as { id: number }).id;
  }
  await page.goto("/#dict");
  await page.locator(".filterbar select.sel").first().selectOption(String(pid));
  const card = page.locator("#appdoc-ruleset");
  await expect(card.locator("#ruleset-description")).toContainText("Wie die Doku bis 0.117.0");
  await expect(card.locator("#ruleset-delete")).toBeDisabled(); // aktiv

  // eingebautes umbenennen und beschreiben
  await card.locator("#ruleset-rename").click();
  await card.locator("#ruleset-label-title").fill("Standard DC");
  await card.locator("#ruleset-label-description").fill("Gilt für die Rechenzentren");
  await card.locator("#ruleset-label-save").click();
  await expect(card.locator("#ruleset-profile")).toContainText("Standard DC (aktiv)");
  await expect(card.locator("#ruleset-description")).toContainText("Gilt für die Rechenzentren");
  await expect(card.locator("#ruleset-description")).toContainText("eingebaut");

  // Regelwerk 2 aktivieren, dann Regelwerk 1 löschen (ausgeblendet) und wiederherstellen
  await card.locator("#ruleset-profile").selectOption("v2");
  await card.locator("#ruleset-activate").click();
  await expect(card.locator("#ruleset-active")).toBeVisible();
  await card.locator("#ruleset-profile").selectOption("v1");
  page.once("dialog", (d) => void d.accept());
  await card.locator("#ruleset-delete").click();
  await expect(card.locator("#ruleset-profile option")).toHaveCount(1);
  await expect(card.locator("#ruleset-hidden")).toContainText("Standard DC");
  await card.locator("#ruleset-hidden button[data-restore='v1']").click();
  await expect(card.locator("#ruleset-profile option")).toHaveCount(2);

  // Vorgabe des Namens zurück, wieder Regelwerk 1 aktiv
  await card.locator("#ruleset-rename").click();
  await card.locator("#ruleset-label-reset").click();
  await expect(card.locator("#ruleset-profile")).toContainText("Regelwerk 1 – Stand bis 0.117.0");
  await card.locator("#ruleset-activate").click();
  await expect(card.locator("#ruleset-profile")).toContainText("Regelwerk 1 – Stand bis 0.117.0 (aktiv)");
});
