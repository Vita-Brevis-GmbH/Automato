// 1.7.0 (Anwender 2026-11-11: „Wenn ich den internen Worker verwende, kann ich die Verbindung prüfen, beim collector
// Agent nicht, können wir dies mit einbringen?“): „Prüfen“ auch bei Geräten hinter einem Agenten – der Orchestrator
// legt einen Auftrag an, der Agent holt ihn beim nächsten Abruf und meldet Name, Verbindung und Anmeldung. Der Agent
// wird hier über seine Schnittstelle (/internal/agent/*) nachgespielt; das Gerät wird am Ende wieder gelöscht.
import { expect, test } from "./fixtures";

const API = `http://127.0.0.1:${Number(process.env.E2E_API_PORT || 8000)}`;
const NAME = "fw-agent-e2e";

test("Verbindung prüfen über den Agenten (1.7.0)", async ({ page, request }) => {
  const agent = await (await page.request.post("/api/agents", { data: { name: "Agent Prüfen E2E" } })).json();

  await page.goto("/#tenants");
  await page.click("button:has-text('+ Quellsystem')");
  const modal = page.locator(".modal");
  await modal.locator("#ss-name").fill(NAME);
  await modal.locator("#ss-platform").selectOption("fortigate");
  await modal.locator("#ss-url").fill("https://fw-agent.example.ch");
  await modal.locator("#ss-agent").selectOption({ label: "Agent Prüfen E2E (alle Mandanten)" });
  await modal.locator("#ss-token").click(); // erst beim Anklicken beschreibbar (kein Autofill)
  await modal.locator("#ss-token").fill("lese-token-e2e");
  const answer = page.waitForResponse((r) => r.url().endsWith("/api/systems") && r.request().method() === "POST");
  await modal.locator("button.btn-primary").click();
  const system = await (await answer).json();

  try {
    const row = page.locator("tbody tr", { hasText: NAME });
    const check = row.locator("button:has-text('Prüfen')");
    await expect(check).toHaveAttribute("title", /Vom Agenten aus prüfen/);
    await check.click();
    const box = page.locator("#system-check");
    await expect(box).toContainText("Der Agent „Agent Prüfen E2E“ prüft beim nächsten Abruf");
    await expect(check).toBeDisabled(); // kein zweiter Auftrag, solange der erste läuft

    // der Agent holt den Auftrag (samt Zugangsdaten) und meldet das Ergebnis
    const headers = { "X-Agent-Token": agent.token };
    const next = await (await request.get(`${API}/internal/agent/next-run`, { headers })).json();
    const job = next.checks.find((c: { target: { name: string } }) => c.target.name === NAME);
    expect(job.target.token).toBe("lese-token-e2e");
    await expect(box).toContainText("prüft gerade");
    const done = await request.post(`${API}/internal/agent/checks/${job.check_id}`, {
      headers,
      data: {
        ok: false,
        hint: "Token prüfen (REST-API-Admin)",
        steps: [
          { step: "dns", ok: true, detail: "fw-agent.example.ch → 10.0.5.1" },
          { step: "tcp", ok: true, detail: "10.0.5.1:443 offen (3 ms)" },
          { step: "login", ok: false, detail: "HTTP 401" },
        ],
      },
    });
    expect(done.status()).toBe(204);

    await expect(box).toContainText("nicht erreichbar");
    await expect(box).toContainText("(geprüft vom Agenten „Agent Prüfen E2E“)");
    await expect(box).toContainText("Verbindung: 10.0.5.1:443 offen");
    await expect(box).toContainText("Anmeldung: HTTP 401");
    await expect(box).toContainText("Token prüfen (REST-API-Admin)");
    await expect(check).toBeEnabled();
  } finally {
    await page.request.delete(`/api/systems/${system.id}`);
  }
});
