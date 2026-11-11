// 1.5.1 (Anwender 2026-11-10: Collector-Agent erreicht den Server nicht – die agent.env zeigte auf :8000, obwohl nur
// das mTLS-Agent-Gateway Agenten annimmt; „ich würde gerne einen Random Port nehmen: 46100“): die Einrichtung schlägt
// die Adresse des Gateways vor und trägt CA und Client-Zertifikat des Agenten ein.
import { expect, test } from "./fixtures";

const envText = (page: import("@playwright/test").Page) =>
  page.locator("pre", { hasText: "AUTOMATO_ORCHESTRATOR_URL=" }).first();

test("Agent-Einrichtung über das mTLS-Agent-Gateway (1.5.1)", async ({ page }) => {
  await page.route(/\/api\/agents\/package\/info$/, async (route) => {
    const res = await route.fetch();
    await route.fulfill({ response: res, json: { ...(await res.json()), gateway_url: "https://automato.example.ch:46100" } });
  });
  await page.goto("/#agents");
  await expect(page.locator("#ag-orch")).toHaveValue("https://automato.example.ch:46100");
  await expect(page.locator("#ag-gateway")).toContainText("make-certs.sh agent");

  await page.fill("#ag-name", "agent-e2e");
  const env = envText(page);
  await expect(env).toContainText("AUTOMATO_ORCHESTRATOR_URL=https://automato.example.ch:46100");
  await expect(env).toContainText("AUTOMATO_ORCHESTRATOR_CA_BUNDLE=C:\\Automato\\pki\\agents-ca.crt");
  await expect(env).toContainText("AUTOMATO_CLIENT_CERT=C:\\Automato\\pki\\agent-agent-e2e.crt");
  await expect(env).toContainText("AUTOMATO_CLIENT_KEY=C:\\Automato\\pki\\agent-agent-e2e.key");
  await expect(env).not.toContainText("# AUTOMATO_CLIENT_CERT");
  await expect(page.locator("#ag-gateway")).toContainText("make-certs.sh agent agent-e2e");

  // Name, den make-certs.sh nicht annimmt
  await page.fill("#ag-name", "Agent mit Leerzeichen");
  await expect(page.locator("#ag-gateway")).toContainText(/nur A-Z a-z 0-9|only A-Z a-z 0-9/);

  // direkt auf :8000 (z. B. im Labor): keine Gateway-Zeilen, Hinweis als Warnung
  await page.fill("#ag-orch", "http://10.0.12.12:8000");
  await expect(env).toContainText("# AUTOMATO_CLIENT_CERT=");
  await expect(env).not.toContainText("agents-ca.crt");
});

test("Agent-Einrichtung ohne Gateway bleibt wie bisher (1.5.1)", async ({ page }) => {
  await page.goto("/#agents");
  await expect(page.locator("#ag-orch")).toHaveValue(/:8000$/);
  await expect(page.locator("#ag-gateway")).toHaveCount(0);
  await expect(page.locator("#ag-name")).toHaveCount(0);
  await expect(envText(page)).toContainText("# AUTOMATO_CLIENT_CERT=C:\\Automato\\pki\\agent.crt");
});
