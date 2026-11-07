// 0.141.1 (Anwender 2026-11-06: „Rückweg über eine andere Firewall: Bei mir sehe ich die Meldung, aber es zeigt mir nicht
// den Rückweg an, welcher falsch sein soll“): je Abschnitt zeigen Weg-Suche und Verbindungen den Rückweg Hop für Hop, die
// Firewall, die nicht auf dem Hinweg liegt, rot markiert.
import { expect, rows, test } from "./fixtures";

const TEXT = "Rückweg über andere Firewalls – Antwort von 10.10.10.80 an 10.10.0.5 über fw-b";
const RETURN = {
  state: "asymmetric",
  text: TEXT,
  firewalls: ["fw-a"],
  legs: [
    {
      from: "10.10.10.80",
      to: "10.10.0.5",
      forward: ["fw-a"],
      paths: [
        [
          { label: "leaf-101 · VRF1", kind: "aci", firewall: false, other: false },
          { label: "fw-b", kind: "firewall", firewall: true, other: true },
        ],
        [{ label: "fw-a", kind: "firewall", firewall: true, other: false }],
      ],
      more: 2,
      state: "asymmetric",
    },
  ],
};

test("Weg-Suche: Rückweg Hop für Hop (0.141.1)", async ({ page }) => {
  await page.route(/\/api\/routing\/trace(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.paths[0].return = RETURN;
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#wege");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.10.10.80");
  await page.click("button:has-text('Weg suchen')");
  const leg = page.locator(".return-legs .return-leg").first();
  await expect(leg).toHaveAttribute("data-state", "asymmetric");
  await expect(leg).toContainText("Rückweg 10.10.10.80 → 10.10.0.5:");
  await expect(leg).toContainText("leaf-101 · VRF1");
  await expect(leg.locator(".return-fw[data-other='1']")).toHaveText("fw-b");
  await expect(leg.locator(".return-fw[data-other='0']")).toHaveText("fw-a");
  await expect(leg).toContainText("+2 weitere");
  await expect(leg.locator(".return-forward")).toHaveText("Hinweg über fw-a");
});

test("Verbindungen: Rückweg Hop für Hop im Detail (0.141.1)", async ({ page }) => {
  await page.route(/\/api\/applications\/\d+\/connections(\?|$)/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.connections.forEach((c: { return?: unknown }, i: number) => {
      c.return = i === 0 ? RETURN : { state: "symmetric", text: "Rückweg über dieselben Firewalls" };
    });
    body.summary.asymmetric = 1;
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Webshop" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  const card = page.locator("#app-connections");
  await card.locator("#app-conn-filter").getByText("Rückweg über andere Firewall").click();
  await card.locator("tbody tr.clickable").first().click();
  const line = card.locator(".conn-return-line");
  await expect(line.locator(".return-fw[data-other='1']")).toHaveText("fw-b");
  await expect(line.locator(".return-forward")).toHaveText("Hinweg über fw-a");
});
