// NetBox-Sync, Abschnitt „DNS-Namen aus dem AD“ (1.19.0, Anwender 2026-11-17 Priorität 7 „DNS aus AD“, ADR 0045
// Nachtrag): Abschnitt wählbar; in der Vorschau die Abweichungen (NetBox ≠ A-Record) mit beiden Werten und Auswahl je
// Eintrag (Aktion „ändern“ mit `confirm` wie die Kabelnummern), mehrdeutige IPs mit Kandidaten und Grund; übernommen
// werden nur die bestätigten Abweichungen. Die Sync-API ist simuliert (keine NetBox in den Beispieldaten), jeder Test
// läuft für sich.
import { expect, test } from "./fixtures";
import type { Page } from "@playwright/test";

const TARGET = { id: 7, name: "netbox", base_url: "http://netbox.example.ch", ref_role: "primary", has_write_token: true };

const ITEMS = [
  {
    kind: "dns_name", key: "10.20.0.5", label: "DNS-Name 10.20.0.5", action: "update", id: 5, manual: true,
    changes: [{ field: "dns_name", old: "", new: "app01.example.ch" }], dns: { names: ["app01.example.ch"], ptr: [], by: "a" },
  },
  {
    kind: "dns_name", key: "10.20.0.7", label: "DNS-Name 10.20.0.7", action: "update", confirm: true, id: 7, manual: true,
    changes: [{ field: "dns_name", old: "db-alt.example.ch", new: "db01.example.ch" }],
    dns: { names: ["db01.example.ch"], ptr: [], by: "a" },
  },
  {
    kind: "dns_name", key: "10.20.0.9", label: "DNS-Name 10.20.0.9", action: "update", confirm: true, id: 9, manual: false,
    changes: [{ field: "dns_name", old: "print-alt.example.ch", new: "print01.example.ch" }],
    dns: { names: ["print01.example.ch", "scan01.example.ch"], ptr: ["print01.example.ch"], by: "ptr" },
  },
  {
    kind: "dns_name", key: "10.20.0.8", label: "DNS-Name 10.20.0.8", action: "ambiguous", id: 8, manual: true,
    changes: [{ field: "dns_name", old: "", new: null }],
    dns: { names: ["mail01.example.ch", "smtp.example.ch"], ptr: [], why: "no_ptr" },
  },
];

// `empty`: IPs mit leerem Namen in NetBox (werden ohne Rückfrage gesetzt); dazu immer zwei Abweichungen
function plan(empty: number, extra: Record<string, unknown> = {}) {
  const update = empty + 2;
  return {
    id: 41, tenant_id: 1, source_system_id: 7, target: "netbox", target_url: TARGET.base_url, status: "ready",
    options: { sections: ["dns"], allow_delete: false },
    summary: { dns_name: { create: 0, update, unchanged: 3, conflict: 0, delete: 0, obsolete: 0, ambiguous: 1 } },
    totals: { create: 0, update, unchanged: 3, conflict: 0, delete: 0, obsolete: 0, ambiguous: 1 },
    warnings: ["DNS-Namen aus dem AD: 1 A-Records zeigen auf IPs, die NetBox nicht kennt – keine neuen IP-Adressen (z. B. 10.20.0.99)."],
    message: null, result: null, verification: null, created_by: "admin", created_at: new Date().toISOString(),
    applied_by: null, applied_at: null, can_apply: true, ...extra,
  };
}

async function mock(page: Page, current: Record<string, unknown>, applied: unknown[] = []) {
  let state = current;
  await page.route(/\/api\/netbox-sync\//, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/netbox-sync\//, "");
    if (path === "targets") return route.fulfill({ json: [TARGET] });
    if (path === "plans") return route.fulfill({ json: [state] });
    if (path === "plans/41/apply" && req.method() === "POST") {
      const body = req.postDataJSON() as { confirm: string[] };
      applied.push(body);
      state = {
        ...state, status: "applied", applied_by: "admin", applied_at: new Date().toISOString(), can_apply: false,
        options: { ...(state.options as object), confirm: body.confirm },
        result: { dns_name: { create: 0, update: 1 + body.confirm.length, delete: 0 } },
        message: "Übernommen und nachkontrolliert: geschrieben = gelesen (2 Objekte) – NetBox wird neu eingelesen.",
      };
      return route.fulfill({ status: 202, json: state });
    }
    if (path === "plans/41") return route.fulfill({ json: state });
    if (path === "plans/41/items") {
      const action = url.searchParams.get("action");
      const kind = url.searchParams.get("kind");
      const empty = (state.totals as Record<string, number>).update - 2;
      const rows = ITEMS.filter(
        (i) => (!action || action.split(",").includes(i.action)) && (!kind || i.kind === kind) && (empty || i.key !== "10.20.0.5"),
      );
      return route.fulfill({ json: { total: rows.length, rows } });
    }
    return route.fallback();
  });
}

test("NetBox-Sync: Abschnitt „DNS-Namen aus dem AD“ wählbar, standardmässig aus (1.19.0)", async ({ page }) => {
  await mock(page, plan(1, { status: "applied", can_apply: false }));
  await page.goto("/#netbox");
  const dns = page.locator("#sync-section-dns");
  await expect(dns).not.toBeChecked();
  await expect(page.locator("label.check", { has: dns })).toContainText("DNS-Namen aus dem AD");
  await dns.check();
  await expect(page.locator("#sync-section-dns-more")).toContainText("übernommen wird nur, was Sie dort je Eintrag bestätigen");
});

test("NetBox-Sync: DNS-Namen – Abweichungen bestätigen, mehrdeutig ohne Vorschlag, nur Bestätigtes übernommen (1.19.0)", async ({ page }) => {
  const applied: { confirm: string[] }[] = [];
  await mock(page, plan(1), applied);
  await page.goto("/#netbox");
  const panel = page.locator("#sync-dns");
  await expect(panel.locator("#sync-dns-counts")).toContainText("1 setzen (leer in NetBox)");
  await expect(panel.locator("#sync-dns-counts")).toContainText("2 abweichend");
  await expect(panel.locator("#sync-dns-counts")).toContainText("1 mehrdeutig");
  // Hinweis: A-Record auf eine IP, die NetBox nicht kennt (nur gezählt)
  await expect(page.locator(".card", { hasText: "Vorschau #41" })).toContainText("die NetBox nicht kennt");
  // Abweichung: beide Werte, Grundlage
  const db = panel.locator('#sync-dns-confirm tr[data-ip="10.20.0.7"]');
  await expect(db).toContainText("db-alt.example.ch");
  await expect(db).toContainText("db01.example.ch");
  await expect(db).toContainText("A-Record · von Hand gepflegt");
  await expect(panel.locator('#sync-dns-confirm tr[data-ip="10.20.0.9"]')).toContainText("PTR entscheidet");
  // mehrdeutig: Kandidaten und Grund, keine Auswahl
  const mail = panel.locator('#sync-dns-ambiguous tr[data-ip="10.20.0.8"]');
  await expect(mail).toContainText("mail01.example.ch, smtp.example.ch");
  await expect(mail).toContainText("mehrere A-Records, kein PTR");
  await expect(mail.locator("input")).toHaveCount(0);
  // Übersicht je Art mit der Spalte „mehrdeutig“
  await expect(page.locator("th.num", { hasText: "mehrdeutig" })).toBeVisible();

  // eine Abweichung bestätigen
  await expect(panel.locator("#sync-dns-picked")).toHaveText("0 von 2 bestätigt");
  await db.locator("input[type=checkbox]").check();
  await expect(panel.locator("#sync-dns-picked")).toHaveText("1 von 2 bestätigt");
  await page.locator(".card-head button", { hasText: "Übernehmen" }).click();
  const modal = page.locator(".modal");
  await expect(modal.locator("#sync-confirm-dns")).toContainText("1 von 2 abweichenden DNS-Namen bestätigt");
  await modal.locator("button", { hasText: "Übernehmen" }).click();
  await expect.poll(() => applied).toEqual([{ confirm: ["10.20.0.7"] }]);
  // übernommen: Auswahl bleibt sichtbar, nicht mehr änderbar
  await expect(page.locator(".card-head .chip", { hasText: "übernommen" })).toBeVisible();
  await expect(panel.locator('#sync-dns-confirm tr[data-ip="10.20.0.7"] input')).toBeChecked();
  await expect(panel.locator('#sync-dns-confirm tr[data-ip="10.20.0.7"] input')).toBeDisabled();
  await expect(panel.locator('#sync-dns-confirm tr[data-ip="10.20.0.9"] input')).not.toBeChecked();
});

test("NetBox-Sync: nur Abweichungen – Übernehmen erst mit einer Bestätigung (1.19.0)", async ({ page }) => {
  const applied: { confirm: string[] }[] = [];
  await mock(page, plan(0), applied);
  await page.goto("/#netbox");
  const apply = page.locator(".card-head button", { hasText: "Übernehmen" });
  await expect(page.locator("#sync-dns-confirm tbody tr")).toHaveCount(2);
  await expect(apply).toBeDisabled();
  await page.locator("#sync-dns-all").check(); // alle bestätigen
  await expect(page.locator("#sync-dns-picked")).toHaveText("2 von 2 bestätigt");
  await expect(apply).toBeEnabled();
  await apply.click();
  await page.locator(".modal button", { hasText: "Übernehmen" }).click();
  await expect.poll(() => applied).toEqual([{ confirm: ["10.20.0.7", "10.20.0.9"] }]);
});

test("NetBox-Sync: DNS-Namen auf Englisch (1.19.0)", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await mock(page, plan(1));
  await page.goto("/#netbox");
  const panel = page.locator("#sync-dns");
  await expect(panel.locator("h3")).toHaveText("DNS names from the AD");
  await expect(panel.locator("#sync-dns-counts")).toContainText("2 differing");
  await expect(panel.locator('#sync-dns-ambiguous tr[data-ip="10.20.0.8"]')).toContainText("several A records, no PTR");
  await expect(page.locator("label.check", { has: page.locator("#sync-section-dns") })).toContainText("DNS names from the AD");
});
