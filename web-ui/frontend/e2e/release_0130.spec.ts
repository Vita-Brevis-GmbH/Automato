// 0.130.0 (Anwender 2026-11-03): „Die Reihenfolge der Dokumentation nach Kategorie wird nicht in der Reihenfolge des
// Namensprofils angezeigt“ – die Doku nennt das Namensprofil ihrer Kategorien, das Profil die Mandanten, die es nutzen;
// „Es zeigt mir nun auch andere Zonen an … Session Links“ – im BGP-Befund je Zone der Weg mit dem Netz je Übergang.
import { expect, rows, test } from "./fixtures";

test("Doku: Namensprofil der Kategorien, Profil mit seinen Mandanten (0.130.0)", async ({ page }) => {
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Webshop" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  await expect(page.locator("#app-categories > .card")).toBeVisible();
  if ((await page.locator("#app-categories .stack").count()) === 0) await page.locator("#app-categories h2.fold-title").first().click();
  await expect(page.locator("#app-catalog-profile")).toContainText("aus dem Namensprofil „default / default“");

  await page.goto("/#dict");
  await expect(page.locator("#appdoc-catalog #appdoc-catalog-tenants")).toContainText("Gilt für die Doku der Mandanten:");
});

test("BGP-Befund: je Zone der Weg mit dem Netz je Übergang (0.130.0)", async ({ page }) => {
  const finding = {
    id: 990130,
    tenant_id: 1,
    type: "bgp_zone_mismatch",
    severity: "info",
    status: "open",
    subject_kind: "node",
    subject_id: "fw-01 · VDOM root → 10.70.0.2",
    source_system_id: null,
    message: "fw-01 · VDOM root zum Nachbarn 10.70.0.2: der Eingangsfilter passt nicht zu den Zonen dahinter (Lan, Aussen).",
    hint: null,
    note: null,
    first_seen: null,
    last_seen: null,
    detail: {
      label: "fw-01 · VDOM root",
      peer: "10.70.0.2",
      zones: ["Lan", "Aussen"],
      neighbor_zone: "Lan",
      missing: ["10.80.0.0/16"],
      too_open: [],
      why: [],
      chain: {
        home: "DC FW",
        neighbor: "Lan",
        device: true,
        zones: [
          { name: "Lan", hops: 1, path: [{ zone: "DC FW", network: null }, { zone: "Lan", network: "10.70.0.0/30", more: 0 }] },
          {
            name: "Aussen",
            hops: 2,
            path: [
              { zone: "DC FW", network: null },
              { zone: "Lan", network: "10.70.0.0/30", more: 0 },
              { zone: "Aussen", network: "10.90.0.0/30", more: 1 },
            ],
          },
        ],
        not_via: [],
      },
    },
  };
  await page.route(/\/api\/findings(\?|$)/, (route) =>
    route.fulfill({ json: [finding], headers: { "X-Total-Count": "1", "Content-Type": "application/json" } }),
  );
  await page.goto("/#findings");
  await page.locator("tbody tr.clickable", { hasText: "10.70.0.2" }).first().click();
  const paths = page.locator("tbody tr.subrow .bgp-why .bgp-zone-paths");
  await paths.locator("summary").click();
  const aussen = paths.locator("li[data-zone='Aussen']");
  await expect(aussen).toContainText("DC FW → Lan (über 10.70.0.0/30) → Aussen (über 10.90.0.0/30 +1)");
});
