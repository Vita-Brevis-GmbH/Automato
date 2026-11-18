// 1.19.2 (Anwender 2026-11-18: „Es sollte gemäss mir einen neuen Mandanten angelegt und mit dem Account verknüpft
// werden, sowie die anderen Mandanten nicht sichtbar machen.“; ADR 0058 Nachtrag): das Cockpit schickt
// `zuordnung.mandant_anlegen` – Automato legt einen eigenen Mandanten mit einem Kunden an; das Kunden-Portal zeigt
// ihn unter „Ansehen“ als „für das Cockpit angelegt“.
import { COCKPIT, expect, testCockpit as test } from "./cockpit";

test("Kunden-Portal: eigener Mandant aus dem Cockpit (1.19.2)", async ({ page }) => {
  const retry = test.info().retry;
  const name = `Mandant 1192 E2E ${retry}`;
  const kunde = {
    kurzname: `mandant-1192-${retry}`,
    name,
    werkzeuge: ["doc"],
    zugriff: "ueberall",
    netze: [],
    realm: `mandant-1192-${retry}`,
    zuordnung: { mandant_anlegen: true, kunden: [], rolle: "viewer" },
  };
  const put = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [kunde] } });
  expect(put.status()).toBe(200);
  const antwort = await put.json();
  expect(antwort.mandanten_angelegt).toHaveLength(1);
  const mandant = antwort.mandanten_angelegt[0] as { kurzname: string; id: number; name: string };
  expect(mandant.name).toBe(name);
  // ein zweites Senden legt keinen weiteren an
  const nochmal = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [kunde] } });
  expect((await nochmal.json()).mandanten_angelegt).toEqual([]);

  await page.goto("/#kundenportal");
  const row = page.locator("#portal-customers tbody tr", { hasText: kunde.kurzname }).first();
  await row.locator("button:has-text('Ansehen')").click();
  await expect(page.locator("#pc-tenant")).toHaveValue(String(mandant.id));
  await expect(page.locator("#pc-tenant")).toBeDisabled();
  await expect(page.locator("#pc-tenant-angelegt")).toContainText("für das Cockpit angelegt");
  await expect(page.locator(".modal")).toContainText(name); // der Kunde im neuen Mandanten
  await page.click(".modal-foot button:has-text('Schliessen')");

  // aufräumen: im Cockpit entfernt, angelegten Kunden und Mandanten löschen (Automato löscht ihn nie selbst)
  const listing = await (await page.request.get("/api/portal/customers")).json();
  const eintrag = listing.customers.find((c: { short_name: string }) => c.short_name === kunde.kurzname);
  expect(eintrag.mandant_angelegt).toEqual({ id: mandant.id, name });
  const leer = await page.request.put("/api/freigaben", { headers: COCKPIT, data: { vertrag: 1, kunden: [] } });
  expect((await leer.json()).entfernt).toEqual([kunde.kurzname]);
  const weg = await page.request.delete(`/api/tenants/${mandant.id}?confirm=${encodeURIComponent(name)}`);
  expect(weg.status()).toBe(200);
});
