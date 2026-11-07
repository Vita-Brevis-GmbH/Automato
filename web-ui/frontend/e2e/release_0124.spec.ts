// 0.124.0 (Anwender 2026-11-03: „Bei den Applikationen kann ich nicht einzelne VMs zufügen, dies muss
// möglich sein“): Mitglied-Art „VM“ im Mitglieder-Dialog – die VM steht danach als System in der Doku.
// Die angelegte Applikation wird am Ende wieder gelöscht (gemeinsame Beispieldaten).
import { expect, test } from "./fixtures";

const NAME = "VM-App e2e";

test("Applikation: einzelne VM als Mitglied hinzufügen (0.124.0)", async ({ page }) => {
  const created = await page.request.post("/api/applications?tenant_id=1", { data: { name: NAME } });
  expect(created.ok()).toBe(true);
  const app = await created.json();
  try {
    await page.goto(`/#apps?app=${app.id}`);
    await page.locator("button", { hasText: /^Mitglieder$/ }).click();
    const drawer = page.locator(".drawer");
    await expect(drawer).toContainText("einzelne VMs hinzufügen");
    await drawer.locator("button", { hasText: "Mitglied" }).click();
    const modal = page.locator(".modal");
    await modal.locator("select.sel").selectOption("vm");
    const row = modal.locator("tbody tr", { hasText: "web01-007-ka" });
    await expect(row.locator(".chip").first()).toHaveText("VM");
    await expect(row).toContainText("10.10.10.5");
    await row.click();
    const sent = page.waitForRequest((r) => r.url().includes(`/api/applications/${app.id}/members`) && r.method() === "POST");
    await modal.locator("button.btn-primary").click();
    expect((await sent).postDataJSON()).toEqual({ members: [{ kind: "vm", ref: "web01-007-ka" }] });
    const member = drawer.locator("tbody tr", { hasText: "web01-007-ka" });
    await expect(member.locator(".chip").first()).toHaveText("VM");
    await expect(drawer.locator("dd").filter({ hasText: /^1$/ }).first()).toBeVisible(); // Systeme
    // Doku: die VM als System
    await page.keyboard.press("Escape");
    await page.reload();
    const systems = page.locator(".card").filter({ has: page.locator("h2", { hasText: /^Systeme$/ }) });
    await expect(systems.locator("tbody tr", { hasText: "web01-007-ka" })).toBeVisible();
  } finally {
    const removed = await page.request.delete(`/api/applications/${app.id}`);
    expect(removed.ok()).toBe(true);
  }
});
