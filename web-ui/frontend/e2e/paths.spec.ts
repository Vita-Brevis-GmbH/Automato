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
    // Wege tragen `trace-<n>` (die Karten der Anbindung bzw. L2 `trace-exit-…`/`trace-l2`)
    const card = page.locator("#trace-0");
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
    const back = page.locator("#trace-0");
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

// „Warum dieser Weg?“ (0.109.0): derselbe Weg Hop für Hop in Sätzen, „Erwartet über …“ und ein Finding von
// Hand aus einem Schritt. Das Speichern des Findings wird hier abgefangen (der Bestand der anderen Tests bleibt
// gleich); den Weg zum Server prüft test_path_explain.py.
test("Weg-Suche: Warum dieser Weg?, Erwartet über und Finding von Hand", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await page.route("**/api/findings", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posted = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        id: 4711, tenant_id: 1, type: "manual", severity: "warning", status: "open", subject_kind: "path",
        subject_id: "10.10.0.5 → 10.30.0.10", source_system_id: null, detail: {}, message: "Route prüfen",
        hint: null, note: null, first_seen: null, last_seen: null,
      }),
    });
  });
  await page.goto("/#wege?src=10.10.0.5&dst=10.30.0.10&service=tcp%2F443");
  const card = page.locator("#trace-0");
  await expect(card).toContainText("Regeln: erlaubt");
  await card.locator("button.trace-explain").click();
  const drawer = page.locator(".drawer");
  await expect(drawer.locator("h2")).toHaveText("Warum dieser Weg?");
  await expect(drawer.locator("#xplain-start")).toContainText("Start bei fw-08");
  const hop = drawer.locator(".xplain-hop[data-hop='1']");
  await expect(hop).toContainText("Routing gelesen");
  await expect(hop).toContainText("Route für 10.30.0.10: 10.30.0.0/24");
  // alle Routen zum Ziel: die gewählte mit Grund, die Default-Route verworfen (kürzeres Präfix)
  await expect(hop.locator(".xroutes tr.chosen")).toContainText("10.30.0.0/24");
  await expect(hop.locator(".xroutes")).toContainText("0.0.0.0/0");
  await expect(hop.locator(".xroutes")).toContainText("kürzeres Präfix");
  await expect(drawer.locator("#xplain-end")).toContainText("Ziel angeschlossen an fw-08");

  // Erwartet über fw-08: liegt auf dem Weg
  await drawer.locator("#xplain-expect").selectOption({ label: "Erwartet über fw-08" });
  await expect(drawer.locator("#xplain-expect-result")).toContainText("Der Weg geht über fw-08");
  await expect(drawer.locator("#xplain-expect-result .chip")).toHaveText("auf dem Weg");

  // Finding von Hand: vorausgefüllt mit dem Weg, Titel frei
  await drawer.locator("#xplain-capture").click();
  const dialog = page.locator(".modal");
  await expect(dialog.locator("#finding-src")).toHaveValue("10.10.0.5");
  await expect(dialog.locator("#finding-dst")).toHaveValue("10.30.0.10");
  await expect(dialog.locator("#finding-save")).toBeDisabled();
  await dialog.locator("#finding-title").fill("Route prüfen");
  await dialog.locator("#finding-text").fill("Default-Route auf fw-08 ist nicht vorgesehen");
  await dialog.locator("#finding-save").click();
  await expect(dialog).toHaveCount(0);
  await expect(drawer).toContainText("Finding #4711 erfasst");
  expect(posted).toMatchObject({ tenant_id: 1, title: "Route prüfen", src: "10.10.0.5", dst: "10.30.0.10", service: "tcp/443", severity: "warning" });
  await expect(drawer.locator("a[href='#findings?type=manual']")).toBeVisible();
});

// Fenster ziehbar (0.109.1, Anwender: „Fenster zu klein“): ⤢ fast volle Breite, Rand ziehen setzt
// eine eigene Breite, die der Browser sich merkt; Doppelklick auf den Rand = Vorgabe
test("Seitenpanel: breiter mit ⤢, Rand ziehen, Breite gemerkt", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto("/#wege?src=10.10.0.5&dst=10.30.0.10&service=tcp%2F443");
  await page.locator("#trace-0 button.trace-explain").click();
  const drawer = page.locator(".drawer");
  const width = async () => (await drawer.boundingBox())!.width;
  const normal = await width();
  await drawer.locator("button.size").click();
  expect(await width()).toBeGreaterThan(1300); // 96vw
  await drawer.locator("button.size").click();
  expect(Math.round(await width())).toBe(Math.round(normal));
  // Rand nach links ziehen: 200 px breiter
  const box = (await drawer.locator(".drawer-grip").boundingBox())!;
  await page.mouse.move(box.x + 3, box.y + 200);
  await page.mouse.down();
  await page.mouse.move(box.x - 197, box.y + 200, { steps: 5 });
  await page.mouse.up();
  expect(Math.abs((await width()) - (normal + 200))).toBeLessThan(4);
  await drawer.locator("button.close").click();
  await page.locator("#trace-0 button.trace-explain").click();
  expect(Math.abs((await width()) - (normal + 200))).toBeLessThan(4); // gemerkt
  await drawer.locator(".drawer-grip").dblclick();
  expect(Math.round(await width())).toBe(Math.round(normal));
});

// 0.112.0 (Anwender: „bei den Regeln & Contracts, Firewall Regeln in der Applikation und Doku nach Kategorie auch den
// Knopf Wegsuche anbieten“): der Knopf öffnet #wege?rule=<id> in einem neuen Tab, die Weg-Suche füllt Quelle, Ziel und
// Dienst der Regel, sucht gleich und bietet die übrigen Werte der Regel zur Wahl
test("Regel in der Weg-Suche: Knopf an der Regel, Werte der Regel zur Wahl (0.112.0)", async ({ page, problems }) => {
  await page.goto("/#regeln");
  await page.locator(".filterbar .search input").fill("lan-to-dmz-web");
  const row = page.locator("tbody tr").filter({ hasText: "lan-to-dmz-web" }).first();
  const link = row.locator("a.rule-wege");
  await expect(link).toHaveAttribute("target", "_blank");
  const href = (await link.getAttribute("href"))!;
  expect(href).toMatch(/^#wege\?rule=\d+$/);
  // auch im Regel-Detail
  await row.click();
  await expect(page.locator(".drawer a.rule-wege")).toHaveAttribute("href", href);
  await page.goto(`/${href}`);
  const choice = page.locator("#rule-choice");
  await expect(choice).toContainText("Aus Regel „lan-to-dmz-web“");
  await expect(page.getByPlaceholder(SRC)).toHaveValue("10.10.0.0/24");
  await expect(page.getByPlaceholder(DST)).toHaveValue("10.30.0.10");
  await expect(page.getByPlaceholder("Dienst: tcp/443")).toHaveValue("tcp/443");
  await expect(page.locator(".card[id^='trace-']", { hasText: "Regeln: erlaubt" }).first()).toBeVisible();
  await expect(page).toHaveURL(/#wege\?src=10\.10\.0\.0%2F24&dst=10\.30\.0\.10&service=tcp%2F443$/);
  await expect(choice.locator("[data-side='service'] .chip.info")).toHaveText("tcp/443");
  expect(problems).toEqual([]);
});

test("Regel mit Quelle any: Start an der Firewall bzw. Anbindung, Weg bis zum Ziel (0.113.1)", async ({ page, problems }) => {
  const rules = (await (await page.request.get("/api/rules?tenant_id=1&q=inet-to-web")).json()) as { id: number; name: string }[];
  const rule = rules.find((r) => r.name === "inet-to-web")!;
  await page.goto(`/#wege?rule=${rule.id}`);
  const choice = page.locator("#rule-choice");
  await expect(choice.locator("[data-side='src']")).toContainText("any – ");
  // Weg bis zum Ziel statt der Liste aller Routen zur IP
  await expect(page.locator(".card[id^='trace-l2']")).toContainText("Ziel hängt an");
  await expect(page.locator(".card[id^='trace-']", { hasText: "Regeln: erlaubt" }).first()).toBeVisible();
  expect(problems).toEqual([]);
});

test("Weg-Suche nur mit Ziel: Hinweis statt Liste aller Routen (0.113.1)", async ({ page, problems }) => {
  await page.goto("/#wege");
  await page.getByPlaceholder(DST).fill("10.20.0.5");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator("main.main")).toContainText("Quelle oder Startpunkt angeben");
  await expect(page.locator(".card", { hasText: "Geht vor" })).toHaveCount(0);
  expect(problems).toEqual([]);
});
