// 1.14.0 (ADR 0058): der UI-Testserver ist mit einem Test-Cockpit gekoppelt (e2e/server.py) – dann ist
// das Kunden-Portal nur lesbar. Tests der Oberfläche ohne Cockpit (wie eine Installation ohne Cockpit)
// nehmen `testOhneCockpit`; Tests, die innerhalb umschalten (`cockpit(request, false)`), nehmen
// `testCockpit`. Beide koppeln am Ende wieder (auch wenn der Test scheitert) – die Tests eines Shards
// laufen nacheinander auf demselben Server.
import type { APIRequestContext } from "@playwright/test";
import { expect, test as base } from "./fixtures";

export { expect };

// Test-Token des lokalen UI-Servers (kein Geheimnis), gilt nur, solange gekoppelt ist
export const COCKPIT = { Authorization: "Bearer e2e-cockpit-token" };

export async function cockpit(request: APIRequestContext, gekoppelt: boolean): Promise<void> {
  const r = gekoppelt ? await request.put("/api/e2e/cockpit") : await request.delete("/api/e2e/cockpit");
  expect(r.ok()).toBe(true);
}

// am Ende wieder gekoppelt (Ausgangslage aller Tests)
export const testCockpit = base.extend<{ cockpitDanach: void }>({
  cockpitDanach: [
    async ({ request }, use) => {
      await use();
      await cockpit(request, true);
    },
    { auto: true },
  ],
});

// ohne Cockpit (Kunden-Portal in der Oberfläche pflegen), am Ende wieder gekoppelt
export const testOhneCockpit = base.extend<{ ohneCockpit: void }>({
  ohneCockpit: [
    async ({ request }, use) => {
      await cockpit(request, false);
      await use();
      await cockpit(request, true);
    },
    { auto: true },
  ],
});
