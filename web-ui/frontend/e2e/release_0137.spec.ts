// 0.137.0 (Anwender 2026-11-05: „Der Dienst soll zugewiesen werden können, auswahlfeld, also Clientseitig, Eingang
// Loadbalancer oder beim Ziel Server, Standard wäre Clientseitig“): Auswahl, wo der Dienst gilt, die Ports entlang des
// Wegs und je Hop, mit welchem Port geprüft wurde.
import { expect, test } from "./fixtures";

test("Weg-Suche: Dienst je Stelle und Ports entlang des Wegs (0.137.0)", async ({ page }) => {
  let asked = "";
  await page.route(/\/api\/routing\/trace(\?|$)/, async (route) => {
    asked = route.request().url();
    const res = await route.fetch();
    const body = await res.json();
    body.service = "tcp/443";
    body.service_at = { at: "server", label: "am Ziel-Server", given: "tcp/8443", client: "tcp/443", note: "Dienst tcp/8443 am Ziel-Server = beim Client tcp/443" };
    body.service_chain = [
      { at: "client", label: "Client", service: "tcp/443" },
      { at: "vip", label: "VIP VIP_web", service: "tcp/8443" },
      { at: "server", label: "Ziel", service: "tcp/8443" },
    ];
    const hop = body.paths[0].hops.find((h: { kind?: string }) => h.kind === "firewall" || h.kind === "vdom");
    hop.checked_service = "tcp/8443";
    hop.rules = hop.rules ?? { verdict: "allow", text: "erlaubt durch Policy 1", rules: [] };
    await route.fulfill({ response: res, json: body });
  });
  await page.goto("/#wege");
  // Vorgabe: beim Client
  await expect(page.locator("#trace-service-at")).toHaveValue("client");
  await page.getByPlaceholder("Quelle: IP, Netz oder Name").fill("10.10.0.5");
  await page.getByPlaceholder("Ziel: IP, Netz oder Name").fill("10.10.10.80");
  await page.getByPlaceholder("Dienst: tcp/443 oder FT_https").fill("tcp/8443");
  await page.locator("#trace-service-at").selectOption("server");
  await page.click("button:has-text('Weg suchen')");
  await expect(page.locator("#trace-asked")).toContainText("Dienst tcp/8443 am Ziel-Server = beim Client tcp/443");
  expect(asked).toContain("service_at=server");
  await expect(page).toHaveURL(/service_at=server/);
  await expect(page.locator(".trace-service-chain")).toHaveText("Ports: Client tcp/443 → VIP VIP_web tcp/8443 → Ziel tcp/8443");
  await expect(page.locator(".trace-service-note")).toHaveText("Dienst tcp/8443 am Ziel-Server = beim Client tcp/443");
  await expect(page.locator(".hop-checked-service").first()).toHaveText("geprüft mit tcp/8443");
});
