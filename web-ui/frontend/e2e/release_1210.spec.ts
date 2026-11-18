// 1.21.0 (ADR 0069, Anwender 2026-11-18: „eine Auswahl treffen …, welche Applikationen ich herunterladen
// möchte“, „Zudem geht es sehr lange, bis der Export steht, könnte man dies als Hintergrunddienst laufen
// lassen?“): Auswahl der Applikationen im Export-Dialog, Export im Hintergrund mit Fortschritt (auch nach dem
// Neuladen) und Download unter „Meine Exporte“.
import { readFileSync } from "fs";
import { expect, test } from "./fixtures";

const PW = "geheim-1234";

test("Export: Auswahl mit Suche, alle/keine, nur die gewählten im Download (1.21.0)", async ({ page }) => {
  await page.goto("/#apps");
  await page.locator("#apps-export-all").click();
  const box = (name: string) => page.locator("#exp-apps label", { hasText: name }).locator("input");
  const run = page.locator("#apps-export-all-run");
  await expect(box("Webshop")).toBeChecked(); // ohne Auswahl in der Liste: alle
  await expect(box("Portal Süd")).toBeChecked();
  await page.locator("#exp-none").click();
  await expect(box("Webshop")).not.toBeChecked();
  await page.locator("#exp-pw").fill(PW);
  await page.locator("#exp-pw2").fill(PW);
  await expect(run).toBeDisabled(); // nichts gewählt
  await expect(run).toHaveText("0 exportieren");
  // Suche: „alle gefundenen“ wählt nur die gefundenen
  await page.locator("#exp-search").fill("websh");
  await expect(page.locator("#exp-apps label")).toHaveCount(1);
  await page.locator("#exp-all").click();
  await page.locator("#exp-search").fill("");
  await expect(box("Webshop")).toBeChecked();
  await expect(box("Portal Süd")).not.toBeChecked();
  await expect(run).toHaveText("1 exportieren");
  const started = page.waitForResponse((r) => r.url().endsWith("/api/applications/export") && r.status() === 202);
  await run.click();
  const job = await (await started).json();
  expect(job.total).toBe(1);
  await expect(page.locator("#exp-pw")).toHaveValue(""); // das Passwort bleibt nicht stehen
  const row = page.locator(`#my-exports [data-export-id="${job.id}"]`);
  await expect(row).toHaveAttribute("data-status", "done", { timeout: 60_000 });
  await expect(row.locator(".export-status")).toHaveText("fertig");
  const [dl] = await Promise.all([page.waitForEvent("download"), row.locator(".export-download").click()]);
  const body = readFileSync((await dl.path())!).toString("latin1");
  expect(body).toContain("webshop.zip"); // je Applikation ein ZIP, nur die gewählte
  expect(body).not.toContain("portal-sued.zip");
  // nach dem Schliessen auf der Seite Applikationen
  await page.locator(".modal-foot button", { hasText: "Schliessen" }).click();
  const onPage = page.locator(`#my-exports-page [data-export-id="${job.id}"]`);
  await expect(onPage).toHaveAttribute("data-status", "done");
  await onPage.locator(".export-remove").click();
  await expect(onPage).toHaveCount(0);
});

test("Export: Fortschritt im Hintergrund, auch nach dem Neuladen (1.21.0)", async ({ page }) => {
  // laufender Auftrag (Antwort des Servers nachgestellt - ein echter Export ist in den Beispieldaten zu schnell)
  const now = new Date().toISOString();
  const running = {
    id: "0123456789abcdef0123456789abcdef",
    kind: "applications",
    tenant_id: 1,
    status: "running",
    total: 12,
    done: 5,
    current: "Webshop",
    errors: [{ application_id: 99, name: "Portal Süd", error: "RuntimeError: kaputt" }],
    message: null,
    filename: "applikationen-kunde-a-2026-11-18-1200.zip",
    size: null,
    created_at: now,
    started_at: now,
    finished_at: null,
    expires_at: now,
    cancelling: false,
  };
  let polls = 0;
  await page.route("**/api/applications/exports?*", async (route) => {
    polls += 1;
    await route.fulfill({ json: [running] });
  });
  await page.goto("/#apps");
  const row = page.locator('#my-exports-page [data-export-id="0123456789abcdef0123456789abcdef"]');
  await expect(row.locator(".export-status")).toHaveText("läuft");
  await expect(row.locator(".export-count")).toHaveText("5 von 12 Applikationen");
  await expect(row.locator(".export-current")).toContainText("Webshop");
  await expect(row.locator("progress")).toHaveAttribute("value", "5");
  await row.getByRole("button", { name: /1 Applikation mit Fehler/ }).click();
  await expect(row.locator(".export-error-list")).toContainText("Portal Süd");
  await expect.poll(() => polls, { timeout: 10_000 }).toBeGreaterThan(1); // fragt nach, solange er läuft
  await page.reload();
  await expect(row.locator(".export-count")).toHaveText("5 von 12 Applikationen");
  // fertig: Download statt Fortschritt
  await page.unroute("**/api/applications/exports?*");
  await page.route("**/api/applications/exports?*", (route) =>
    route.fulfill({ json: [{ ...running, status: "done", done: 12, current: null, size: 2048 }] }),
  );
  await expect(row).toHaveAttribute("data-status", "done");
  await expect(row.locator(".export-download")).toBeVisible();
  await expect(row.locator("progress")).toHaveCount(0);
});
