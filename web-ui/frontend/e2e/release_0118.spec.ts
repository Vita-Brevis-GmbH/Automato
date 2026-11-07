// 0.118.0 (Rückmeldung des Anwenders 2026-10-30): Regelwerk der Applikations-Doku in den Einstellungen – Regeln an/aus,
// Wirkung, zwei eingebaute Profile zum Umschalten, eine neue Reihenfolge nur als neues Profil.
import { expect, test } from "./fixtures";

test("Namensprofil: Regelwerk umschalten, eigenes Profil anlegen, Reihenfolge nur als neues Profil (0.118.0)", async ({ page }) => {
  // eigenes Namensprofil ohne Mandant: Umschalten wirkt auf keine Doku der anderen UI-Tests
  const list = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  let pid = list.find((p) => p.name === "Regelwerk-Test")?.id;
  if (pid === undefined) {
    const created = await page.request.post("/api/naming-profiles", { data: { customer: "Kunde Regelwerk", name: "Regelwerk-Test" } });
    expect(created.status()).toBe(201);
    pid = ((await created.json()) as { id: number }).id;
  }
  await page.goto("/#dict");
  await page.locator(".filterbar select.sel").first().selectOption(String(pid));
  const card = page.locator("#appdoc-ruleset");
  const rules = card.locator("#ruleset-rules tbody tr");
  await expect(card.locator("#ruleset-profile")).toContainText("Regelwerk 1 – Stand bis 0.117.0 (aktiv)");
  await expect(card.locator("#ruleset-active")).toBeVisible();
  await expect(rules).toHaveCount(11);

  // Regelwerk 2 ansehen und aktivieren
  await card.locator("#ruleset-profile").selectOption("v2");
  await expect(rules.nth(2)).toHaveAttribute("data-rule", "impossible");
  await expect(rules.nth(2)).toContainText("nicht gezeigt, nicht gezählt");
  await expect(rules.nth(1)).toContainText("hebt diese Regeln auf");
  await card.locator("#ruleset-activate").click();
  await expect(card.locator("#ruleset-active")).toBeVisible();
  await expect(card.locator("#ruleset-profile")).toContainText("Regelwerk 2 – Rückmeldung 2026-10-30 (aktiv)");

  // ändern: eingebaut bleibt, als neues Profil speichern
  await card.locator("tr[data-rule='out_broad'] input[type=checkbox]").first().check();
  await expect(card).toContainText("Eingebaute Profile bleiben unverändert");
  await card.locator("#ruleset-save-new").click();
  await card.locator("#ruleset-title").fill("Mein Regelwerk");
  await card.locator("#ruleset-create").click();
  await expect(card.locator("#ruleset-note")).toContainText("Als neues Profil gespeichert");
  await expect(card.locator("#ruleset-profile")).toContainText("Mein Regelwerk – eigen");
  await expect(card.locator("tr[data-rule='out_broad'] input[type=checkbox]").first()).toBeChecked();

  // neue Reihenfolge: Speichern gesperrt, nur als neues Profil
  await card.locator("tr[data-rule='disabled'] button[title='nach oben']").click();
  await expect(card.locator("#ruleset-save")).toBeDisabled();
  await expect(card.locator("#ruleset-save")).toHaveAttribute("title", "Neue Reihenfolge – als neues Profil speichern");
  await card.locator("#ruleset-discard").click();

  // eigenes Profil löschen; zurück auf Regelwerk 1
  page.once("dialog", (d) => void d.accept());
  await card.locator("#ruleset-delete").click();
  await expect(card.locator("#ruleset-profile")).not.toContainText("Mein Regelwerk");
  await card.locator("#ruleset-profile").selectOption("v1");
  await card.locator("#ruleset-activate").click();
  await expect(card.locator("#ruleset-profile")).toContainText("Regelwerk 1 – Stand bis 0.117.0 (aktiv)");
});

test("Applikations-Doku: aktives Regelwerk und ausgelassene Regeln im Kopf (0.118.0)", async ({ page }) => {
  await page.goto("/#apps");
  await page.locator("table tbody tr", { hasText: "Webshop" }).first().click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  await expect(page.locator("#app-ruleset")).toContainText("Regelwerk: Regelwerk 1 – Stand bis 0.117.0");
  await expect(page.locator("#app-ruleset")).toHaveAttribute("href", "#dict");
});
