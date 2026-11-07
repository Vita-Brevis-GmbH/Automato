// 0.126.1 (Anwender 2026-11-03: Netzwerkplan mit nur der VM als Mitglied ohne Firewall, „die EPG wird auch nicht
// ausgefüllt“ – „So umsetzen“): die EPG der VM steht als „EPG der VM“ im Abschnitt EPGs, selbst kein Mitglied. Die
// angelegte Applikation wird am Ende wieder gelöscht (gemeinsame Beispieldaten).
import { expect, test } from "./fixtures";

test("Applikation nur mit einer VM: EPG der VM in der Doku (0.126.1)", async ({ page }) => {
  const created = await page.request.post("/api/applications?tenant_id=1", {
    data: { name: "VM-EPG e2e", members: [{ kind: "vm", ref: "web01-007-ka" }] },
  });
  expect(created.ok()).toBe(true);
  const app = await created.json();
  try {
    await page.goto(`/#apps?app=${app.id}`);
    await expect(page.locator("h1.app-title")).toHaveText("VM-EPG e2e");
    const card = page.locator("#app-epgs");
    if ((await card.locator("tbody tr").count()) === 0) await card.locator("h2.fold-title").first().click();
    const row = card.locator("tbody tr", { hasText: "EG_db" });
    await expect(row.locator(".epg-of-vm")).toHaveText("EPG der VM web01-007-ka");
    await expect(row.locator(".epg-of-vm")).toHaveAttribute("title", /Nicht Mitglied/);
    // beim System die EPG
    const systems = page.locator(".card").filter({ has: page.locator("h2", { hasText: /^Systeme$/ }) });
    await expect(systems.locator("tbody tr", { hasText: "web01-007-ka" })).toContainText("TE/AP/EG_db");
  } finally {
    const removed = await page.request.delete(`/api/applications/${app.id}`);
    expect(removed.ok()).toBe(true);
  }
});
