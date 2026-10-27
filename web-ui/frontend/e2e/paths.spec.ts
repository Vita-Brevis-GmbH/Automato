// Weg-Suche (ADR 0019 Ergänzung, Test des Anwenders 2026-10-27): Details am Weg im Seitenpanel statt
// eines Sprungs in eine andere Ansicht, „↗“ bzw. Ctrl-Klick in einem neuen Tab, je Hop „mehr“, die
// Suche in der URL (Zurück stellt sie wieder her) und Anbindungen als Quelle bzw. Ziel.
// Beispieldaten: fw-08 mit LAN, DMZ und Internet (Zone internet an wan, Default über 192.0.2.1),
// Policy 1 lan -> dmz Web, Policy 6 internet -> dmz Web (seed.py, test_policy_check.FW).
import { expect, test } from "./fixtures";

const SRC = "Quelle: IP, Netz oder Name";
const DST = "Ziel: IP, Netz oder Name";

test("Weg-Suche: Details im Seitenpanel, neuer Tab, je Hop „mehr“ und Suche in der URL", async ({ page }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder(SRC).fill("10.10.0.5");
  await page.getByPlaceholder(DST).fill("10.30.0.10");
  await page.getByPlaceholder("Dienst: tcp/443").fill("tcp/443");
  await page.click("button:has-text('Weg suchen')");
  await expect(page).toHaveURL(/#wege\?src=10\.10\.0\.5&dst=10\.30\.0\.10&service=tcp%2F443$/);
  const card = page.locator(".card[id^='trace-']", { hasText: "Regeln: erlaubt" }).first();
  await expect(card).toBeVisible();

  // Policy im Seitenpanel – die Weg-Suche bleibt stehen
  await card.locator("a", { hasText: "Policy 1" }).click();
  await expect(page.locator(".drawer")).toContainText("lan-to-dmz-web");
  await expect(page).toHaveURL(/#wege\?src=10\.10\.0\.5/);
  await page.locator(".drawer button.close").click();
  await expect(page.locator(".drawer")).toHaveCount(0);

  // „↗“ daneben: dieselbe Hash-URL in einem neuen Tab; Ctrl-Klick auf den Link ebenso
  const arrow = card.locator("a.newtab[href^='#regeln?rule=']").first();
  await expect(arrow).toHaveAttribute("target", "_blank");
  const [tab] = await Promise.all([
    page.context().waitForEvent("page"),
    card.locator("a", { hasText: "Policy 1" }).click({ modifiers: ["Control"] }),
  ]);
  await tab.waitForLoadState();
  await expect(tab).toHaveURL(/#regeln\?rule=\d+/);
  await expect(tab.locator(".drawer")).toContainText("lan-to-dmz-web");
  await tab.close();
  await expect(page.locator(".drawer")).toHaveCount(0); // hier nichts geöffnet

  // je Hop „mehr“: Ein-/Ausgang mit Zone, Route, entscheidende Regel mit Quelle/Ziel/Dienst
  await card.locator("button.hop-more-toggle").first().click();
  const more = card.locator("tr.hop-more");
  await expect(more).toContainText("Eingang");
  await expect(more).toContainText("lan · Zone lan");
  await expect(more).toContainText("Ausgang");
  await expect(more).toContainText("dmz · Zone dmz");
  await expect(more).toContainText("Interfaces: lan → dmz");
  await expect(more).toContainText("Dienst: tcp/443");
  await card.locator("button.hop-more-toggle").first().click();
  await expect(card.locator("tr.hop-more")).toHaveCount(0);

  // Route im Seitenpanel mit allen Routen zum Ziel in diesem Kontext
  await card.locator("a.hop-route").first().click();
  await expect(page.locator("#route-drawer")).toContainText("10.30.0.0/24");
  await expect(page.locator(".drawer")).toContainText("Routen zum Ziel in diesem Kontext");
  await expect(page.locator("#route-drawer-routes tbody tr").first()).toContainText("wirksam");
  await page.locator(".drawer button.close").click();

  // Knoten bzw. Interface im Seitenpanel (Interface aus dem Inventar)
  await card.locator("a.hop-port", { hasText: "dmz" }).first().click();
  await expect(page.locator("#node-drawer")).toContainText("10.30.0.1/24");
  await page.locator(".drawer button.close").click();

  // in eine andere Ansicht und zurück: die Suche ist wieder da (Eingaben und Ergebnis)
  await page.goto("/#routing");
  await expect(page.locator("main.main h1")).toHaveText("Routing");
  await page.goBack();
  await expect(page).toHaveURL(/#wege\?src=10\.10\.0\.5&dst=10\.30\.0\.10&service=tcp%2F443/);
  await expect(page.getByPlaceholder(SRC)).toHaveValue("10.10.0.5");
  await expect(page.getByPlaceholder(DST)).toHaveValue("10.30.0.10");
  await expect(page.getByPlaceholder("Dienst: tcp/443")).toHaveValue("tcp/443");
  await expect(page.locator(".card[id^='trace-']", { hasText: "Regeln: erlaubt" }).first()).toBeVisible();
});

test("Weg-Suche: Name bleibt in der URL, Anbindung als Quelle und Ziel", async ({ page }) => {
  // Anbindung „Internet ISP Edge“ an fw-08 (Interface wan) – am Ende wieder gelöscht
  const systems = (await (await page.request.get("/api/systems?tenant_id=1")).json()) as { id: number; name: string }[];
  const fw8 = systems.find((s) => s.name === "fw-08")!.id;
  const created = await page.request.post("/api/routing/exits", {
    data: { tenant_id: 1, name: "ISP Edge", kind: "internet", source_system_ids: [fw8], interfaces: ["wan"] },
  });
  expect(created.status()).toBe(201);
  const exitId = ((await created.json()) as { id: number }).id;
  try {
    await page.goto("/#wege");
    const src = page.getByPlaceholder(SRC);
    await src.fill("isp edge");
    const option = page.locator(".menu.suggest button[role='option']", { hasText: "Internet ISP Edge" });
    await expect(option).toContainText("fw-08 wan");
    await expect(option).toContainText("beliebige Adresse im Internet");
    await option.click();
    await expect(page.locator(".search", { has: src })).toContainText("= Anbindung · beliebige Adresse im Internet");
    // die Anbindung bestimmt den Start
    await expect(page.locator(".filterbar select").filter({ hasText: "Start: an der Anbindung" })).toBeDisabled();
    await page.getByPlaceholder(DST).fill("10.30.0.10");
    await page.getByPlaceholder("Dienst: tcp/443").fill("tcp/443");
    await page.click("button:has-text('Weg suchen')");
    const exitCard = page.locator("#trace-exit-src");
    await expect(exitCard).toContainText("Quelle: Anbindung Internet ISP Edge");
    await expect(exitCard).toContainText("beliebige Adresse im Internet");
    await expect(exitCard).toContainText("fw-08 (wan, internet)");
    await expect(page).toHaveURL(new RegExp(`#wege\\?dst=10\\.30\\.0\\.10&service=tcp%2F443&src_name=.+&src_exit=exit%3A${exitId}$`));
    // Eingang wan: Policy 6 (internet -> dmz) entscheidet
    const card = page.locator(".card[id^='trace-']").first();
    await expect(card).toContainText("Regeln: erlaubt");
    await expect(card).toContainText("inet-to-web");
    await expect(card.locator("tbody tr").first()).toContainText("über Internet ISP Edge (wan)");

    // neu laden: die Suche kommt aus der URL zurück
    await page.reload();
    await expect(page.locator("#trace-exit-src")).toContainText("Internet ISP Edge");
    await expect(page.getByPlaceholder(SRC)).toHaveValue("Internet ISP Edge");

    // Richtung tauschen: die Anbindung ist jetzt das Ziel, der Weg geht über sie hinaus
    await page.click("#trace-swap");
    await expect(page.locator("#trace-exit-dst")).toContainText("Ziel: Anbindung Internet ISP Edge");
    const back = page.locator(".card[id^='trace-']").first();
    await expect(back.locator(".via-exit")).toHaveText("über die Anbindung");
    await expect(back).toContainText("weiter über Internet ISP Edge");
    await expect(page).toHaveURL(/dst_exit=exit%3A\d+/);
  } finally {
    expect((await page.request.delete(`/api/routing/exits/${exitId}`)).status()).toBe(204);
  }
});

test("Weg-Suche: Host-Name bleibt beim Zurückgehen gewählt", async ({ page }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder(SRC).fill("10.20.0.5");
  const dst = page.getByPlaceholder(DST);
  await dst.fill("www.exa");
  await page.locator(".menu.suggest button", { hasText: "www.example.ch" }).click();
  await page.click("button:has-text('Weg suchen')");
  await expect(page).toHaveURL(/#wege\?src=10\.20\.0\.5&dst=203\.0\.113\.10&dst_name=www\.example\.ch$/);
  await page.goto("/#findings");
  await page.goBack();
  await expect(dst).toHaveValue("www.example.ch");
  await expect(page.locator(".search", { has: dst })).toContainText("= 203.0.113.10");
  await expect(page.locator("#trace-asked")).toContainText("Ziel www.example.ch (203.0.113.10)");
});
