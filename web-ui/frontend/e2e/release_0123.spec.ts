// 0.123.0 (ADR 0045, Anwender 2026-11-03: „Zonen Transfer ist ok“): AD-DNS als Referenzquelle – im
// Quellsystem-Dialog DNS-Server und Zonen, keine Zugangsdaten, keine TLS-Prüfung, Rolle fest
// „Anreicherung“. Das angelegte Quellsystem wird am Ende wieder gelöscht (gemeinsame Beispieldaten).
import { expect, test } from "./fixtures";

const NAME = "AD-DNS e2e";

test("Quellsystem AD-DNS: DNS-Server und Zonen, ohne Zugangsdaten (0.123.0)", async ({ page }) => {
  await page.goto("/#tenants");
  await page.click("button:has-text('+ Quellsystem')");
  const modal = page.locator(".modal");
  await modal.locator("#ss-name").fill(NAME);
  await modal.locator("#ss-platform").selectOption("ad_dns");
  await expect(modal.locator("label[for='ss-url']")).toHaveText("DNS-Server");
  await expect(modal.locator("#ss-url")).toHaveAttribute("placeholder", "dc01.kunde.lan");
  await expect(modal.locator("#ss-role-dns")).toContainText("Anreicherung");
  await expect(modal.locator("#ss-role")).toHaveCount(0); // nie führend
  await expect(modal.locator("#ss-user, #ss-password, #ss-token")).toHaveCount(0); // kein Konto
  await expect(modal).not.toContainText("TLS-Zertifikat des Geräts prüfen");
  await expect(modal).toContainText("Zonenübertragungen");

  await modal.locator("#ss-url").fill("DC01.example.ch");
  await modal.locator("#ss-zones").fill("Example.CH.\n10.in-addr.arpa");
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/systems") && r.method() === "POST");
  const answer = page.waitForResponse((r) => r.url().endsWith("/api/systems") && r.request().method() === "POST");
  await modal.locator("button.btn-primary").click();
  const body = (await sent).postDataJSON();
  expect(body).toMatchObject({ platform: "ad_dns", base_url: "DC01.example.ch" });
  expect(body.ref_role).toBeUndefined();
  const created = await (await answer).json();
  expect(created).toMatchObject({
    base_url: "dns://dc01.example.ch",
    read_scope: "example.ch\n10.in-addr.arpa",
    ref_role: "enrichment",
  });

  try {
    const row = page.locator("tbody tr", { hasText: NAME });
    await expect(row).toContainText("dns://dc01.example.ch");
    await expect(row.locator(".chip", { hasText: "keine nötig" })).toBeVisible();
    await expect(row).toContainText("noch nicht eingelesen");
    // Bearbeiten: Zonen je Zeile
    await row.locator("button:has-text('Bearbeiten')").click();
    await expect(modal.locator("#ss-zones")).toHaveValue("example.ch\n10.in-addr.arpa");
    await modal.locator("button:has-text('Abbrechen')").click();
    await expect(modal).toHaveCount(0);
  } finally {
    const removed = await page.request.delete(`/api/systems/${created.id}`);
    expect(removed.ok()).toBe(true);
  }
});

test("Englisch: AD-DNS im Quellsystem-Dialog", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#tenants");
  await page.click("button:has-text('+ Source system')");
  const modal = page.locator(".modal");
  await modal.locator("#ss-platform").selectOption("ad_dns");
  await expect(modal.locator("label[for='ss-url']")).toHaveText("DNS server");
  await expect(modal.locator("label[for='ss-zones']")).toHaveText("Zones");
  await expect(modal).toContainText("Only to the following servers");
  await modal.locator("button:has-text('Cancel')").click();
});
