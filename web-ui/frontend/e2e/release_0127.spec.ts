// 0.127.0 (Anwender 2026-11-03: „in der Tabelle der Verbindungen Ende zu Ende ist auch keine Rules von den L3Os
// vorhanden, nur in den Regeln und Contracts“ und „Bei BGP Findings … sehe ich keine Details, weshalb dies so sein
// soll“): Regeln ohne Verbindung stehen mit dem Grund unter den Verbindungen; der BGP-Befund zeigt je Netz Zone und
// entscheidende Regel sowie woher die Zonen kommen.
import { expect, rows, test } from "./fixtures";

test("Verbindungen: Regeln ohne Verbindung mit dem Grund (0.127.0)", async ({ page }) => {
  await page.goto("/#apps");
  await rows(page).filter({ hasText: "Webshop" }).click();
  await expect(page.locator("h1.app-title")).toHaveText("Webshop");
  const list = page.locator("#app-connections #conn-without");
  await expect(list).toContainText("Regeln ohne Verbindung");
  await list.locator("button.linkbtn").first().click();
  // CT_rt: externe EPG nur mit einem Subnetz für das Routing – keine prüfbare Gegenstelle
  await expect(list.locator("tbody tr", { hasText: "CT_rt" })).toContainText("ohne prüfbares Netz");
});

test("BGP-Befund: je Netz Zone und entscheidende Regel, Zonenkette (0.127.0)", async ({ page }) => {
  // Befund wie ihn die Prüfung `bgp_zonen` speichert (Beispieldaten ohne Zonen mit BGP): Liste der Befunde ersetzt
  const finding = {
    id: 990127,
    tenant_id: 1,
    type: "bgp_zone_mismatch",
    severity: "info",
    status: "open",
    subject_kind: "node",
    subject_id: "fw-01 · VDOM root → 10.70.0.2",
    source_system_id: null,
    message: "fw-01 · VDOM root zum Nachbarn 10.70.0.2: der Eingangsfilter passt nicht zu den Zonen dahinter (Lan).",
    hint: null,
    note: null,
    first_seen: null,
    last_seen: null,
    detail: {
      label: "fw-01 · VDOM root",
      peer: "10.70.0.2",
      zones: ["Lan"],
      neighbor_zone: "Lan",
      missing: ["10.99.0.0/16"],
      too_open: ["172.16.0.0/12"],
      why: [
        {
          kind: "missing",
          prefix: "10.99.0.0/16",
          zones: ["Lan"],
          by: [{ kind: "prefix_list", filter: "PL_FW02", verdict: "deny", rule: null }],
          parts: ["10.99.1.0/24"],
        },
        { kind: "too_open", prefix: "172.16.0.0/12", filter: "PL_FW02", rule: "3 permit 172.16.0.0/12 le 32", zones: ["Tenant B"] },
      ],
      // not_via (0.128.0): nur über eine Zone erreichbar, die gleiche Zonen nicht verbindet
      chain: { home: "DC FW", neighbor: "Lan", network: "10.70.0.0/30", zones: [{ name: "Lan", hops: 1 }], not_via: ["Transfer Kunde B"] },
    },
  };
  await page.route(/\/api\/findings(\?|$)/, (route) =>
    route.fulfill({ json: [finding], headers: { "X-Total-Count": "1", "Content-Type": "application/json" } }),
  );
  await page.goto("/#findings");
  await page.locator("tbody tr.clickable", { hasText: "10.70.0.2" }).first().click();
  const why = page.locator("tbody tr.subrow .bgp-why");
  await expect(why.locator(".bgp-zone-chain")).toContainText("Zone der Firewall: DC FW");
  await expect(why.locator(".bgp-zone-chain")).toContainText("Übergang über 10.70.0.0/30");
  await expect(why.locator(".bgp-zone-chain")).toContainText("Zonen dahinter: Lan (1)");
  await expect(why.locator(".bgp-not-via")).toContainText("Nicht dazu (nur über eine Zone, die gleiche Zonen nicht verbindet");
  await expect(why.locator(".bgp-not-via")).toContainText("Transfer Kunde B");
  const missing = why.locator("li[data-kind='missing']");
  await expect(missing).toContainText("10.99.0.0/16");
  await expect(missing).toContainText("Netz der Zone Lan");
  await expect(missing).toContainText("Prefix-Liste PL_FW02: keine Regel passt – am Ende gesperrt");
  await expect(missing).toContainText("erlaubt sind nur Teile davon: 10.99.1.0/24");
  const open = why.locator("li[data-kind='too_open']");
  await expect(open).toContainText("PL_FW02: Regel 3 permit 172.16.0.0/12 le 32");
  await expect(open).toContainText("gehört zur Zone Tenant B, die nicht hinter diesem Nachbarn liegt");
});
