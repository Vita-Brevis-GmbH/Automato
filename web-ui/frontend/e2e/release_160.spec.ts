// 1.6.0 (Anwender 2026-11-11: „ich will das Zertifikat und die .env Config im WebUI herunterladen können und es muss
// funktionieren“, ADR 0052): je Agent ein Zugang als ZIP – agent.env mit neuem Token und, mit dem mTLS-Agent-Gateway,
// Zertifikat, Schlüssel und Agent-CA. Fehlt die Freigabe der Agent-CA, sagt die Einrichtung, was auf der VM zu tun ist.
import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";

test("Zugang je Agent herunterladen (1.6.0, ohne Gateway)", async ({ page }) => {
  await page.goto("/#agents");
  await page.click("button:has-text('+ Agent')");
  await page.fill("#na-name", "zugang-e2e");
  await page.click(".modal-foot button:has-text('Anlegen')");
  await expect(page.locator("#agent-created")).toContainText("zugang-e2e");
  await page.click("#agent-created button");
  const chosen = page.locator("#ag-agent option:checked");
  await expect(chosen).toHaveText("zugang-e2e");
  // ohne Gateway: Adresse des Orchestrators vom Agent-Rechner aus
  await expect(page.locator("#ag-orch")).toHaveValue(/:8000$/);
  await expect(page.locator("#ag-gateway-url")).toHaveCount(0);
  await page.fill("#ag-orch", "http://10.0.12.12:8000");
  page.once("dialog", (d) => d.accept()); // „das bisherige Token gilt nicht mehr“
  const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#ag-access-btn")]);
  expect(dl.suggestedFilename()).toBe("automato-zugang-zugang-e2e.zip");
  const zip = readFileSync((await dl.path())!).toString("latin1");
  expect(zip).toContain("agent.env");
  expect(zip).toContain("LIESMICH.txt");
  await expect(page.locator("#ag-access-done")).toContainText("zugang-e2e");

  // „Zugang“ in der Liste wählt den Agenten in der Einrichtung
  await page.selectOption("#ag-agent", "");
  await page.locator("tbody tr", { hasText: "zugang-e2e" }).locator("button:has-text('Zugang')").click();
  await expect(chosen).toHaveText("zugang-e2e");
});

test("Zugang mit mTLS-Agent-Gateway: Freigabe der Agent-CA und Gründe (1.6.0)", async ({ page, problems }) => {
  let state = "ca_denied";
  await page.route(/\/api\/agents\/package\/info$/, (route) =>
    route.fulfill({ json: { internal_tls: false, gateway_url: "https://automato.example.ch:46100", agent_ca: state } }),
  );
  await page.request.post("/api/agents", { data: { name: "Agent Gateway E2E" } });
  await page.goto("/#agents");
  await page.selectOption("#ag-agent", { label: "Agent Gateway E2E" });
  await expect(page.locator("#ag-gateway-url")).toHaveText("https://automato.example.ch:46100");
  await expect(page.locator("#ag-orch")).toHaveCount(0); // mit Gateway zählt nur dessen Adresse
  await expect(page.locator("#ag-ca-state")).toContainText("make-certs.sh oberflaeche");
  await expect(page.locator("#ag-access-btn")).toBeDisabled();

  // freigegeben, der Orchestrator lehnt trotzdem ab (z. B. CA abgelaufen): Grund statt „HTTP 409“
  state = "ok";
  await page.reload();
  await page.selectOption("#ag-agent", { label: "Agent Gateway E2E" });
  await expect(page.locator("#ag-ca-state")).toHaveCount(0);
  await page.route(/\/api\/agents\/\d+\/access$/, (route) =>
    route.fulfill({ status: 409, json: { detail: { reason: "ca_invalid", message: "agent CA has expired" } } }),
  );
  page.once("dialog", (d) => d.accept());
  await page.click("#ag-access-btn");
  await expect(page.locator(".note", { hasText: "abgelaufen" })).toBeVisible();
  // die Ablehnung (409) ist hier gewollt
  problems.splice(0, problems.length, ...problems.filter((p) => !/^409 .*\/api\/agents\/\d+\/access$/.test(p)));
});

test("Agent-Name, der in kein Zertifikat passt (1.6.0)", async ({ page }) => {
  await page.route(/\/api\/agents\/package\/info$/, (route) =>
    route.fulfill({ json: { internal_tls: false, gateway_url: "https://automato.example.ch:46100", agent_ca: "ok" } }),
  );
  await page.request.post("/api/agents", { data: { name: "Agent, mit Komma" } });
  await page.goto("/#agents");
  await page.selectOption("#ag-agent", { label: "Agent, mit Komma" });
  await expect(page.locator("#ag-name-bad")).toContainText("neuem Namen");
  await expect(page.locator("#ag-access-btn")).toBeDisabled();
});
