// Switches & Router über SNMPv3 (ADR 0033): Zugang im Quellsystem-Dialog wählen, SNMPv3-Zugangsdaten
// (nur authPriv) anlegen – gesendet werden sie, zurück kommen nur die Protokolle; beim Bearbeiten
// stehen die Protokolle vorgewählt, Passwörter bleiben leer („hinterlegt“), ein Wechsel des Zugangs
// warnt. Das angelegte Quellsystem wird am Ende wieder gelöscht (gemeinsame Beispieldaten).
import type { Locator } from "@playwright/test";
import { expect, test } from "./fixtures";

const NAME = "Switches SNMP e2e";
const AUTH = "e2e-auth-pw1";
const PRIV = "e2e-priv-pw2";

// Zugangsdaten-Felder sind bis zum Fokus schreibgeschützt (kein Autofill)
async function typeInto(field: Locator, value: string) {
  await field.click();
  await field.fill(value);
}

test("Quellsystem Switches & Router: Zugang SNMPv3 anlegen und bearbeiten", async ({ page }) => {
  await page.goto("/#tenants");
  await page.click("button:has-text('+ Quellsystem')");
  const modal = page.locator(".modal");
  await modal.locator("#ss-name").fill(NAME);
  await modal.locator("#ss-platform").selectOption("cisco_switch");
  await expect(modal.locator("#ss-access")).toHaveValue("ssh"); // Standard bleibt SSH
  await expect(modal.locator("#ss-user")).toBeVisible();
  await modal.locator("#ss-access").selectOption("snmp");
  await expect(modal.locator("#ss-snmp-mibs")).toContainText("Q-BRIDGE-MIB");
  await expect(modal.locator("#ss-switch-products")).toHaveCount(0); // Produktliste nur für SSH
  await expect(modal.locator("#ss-password")).toHaveCount(0); // kein SSH-Passwort
  await expect(modal.locator("#ss-snmp")).toContainText("authPriv");
  await expect(modal.locator("#ss-snmp-auth")).toHaveValue("sha256");
  await expect(modal.locator("#ss-snmp-priv")).toHaveValue("aes128");

  await modal.locator("#ss-switches").fill("10.0.0.31\n10.0.0.32:1161");
  await typeInto(modal.locator("#ss-snmp-user"), "ro-e2e");
  await modal.locator("#ss-snmp-auth").selectOption("sha512");
  await typeInto(modal.locator("#ss-snmp-authpass"), AUTH);
  await modal.locator("#ss-snmp-priv").selectOption("aes256");
  await typeInto(modal.locator("#ss-snmp-privpass"), PRIV);

  const sent = page.waitForRequest((r) => r.url().endsWith("/api/systems") && r.method() === "POST");
  const answer = page.waitForResponse((r) => r.url().endsWith("/api/systems") && r.request().method() === "POST");
  await modal.locator("button.btn-primary").click();
  const body = (await sent).postDataJSON();
  expect(body).toMatchObject({
    platform: "cisco_switch",
    access: "snmp",
    username: "ro-e2e",
    auth_protocol: "sha512",
    auth_password: AUTH,
    priv_protocol: "aes256",
    priv_password: PRIV,
  });
  expect(body.password).toBeUndefined();
  const created = await (await answer).json();
  expect(created).toMatchObject({
    access: "snmp",
    base_url: "snmp://10.0.0.31",
    has_credentials: true,
    snmp: { auth_protocol: "sha512", priv_protocol: "aes256", has_context: false },
  });
  // Passwörter (und Benutzer) kommen nie zurück – weder beim Anlegen noch in der Liste
  const listed = await (await page.request.get("/api/systems?tenant_id=1")).text();
  for (const text of [JSON.stringify(created), listed]) {
    expect(text).not.toContain(AUTH);
    expect(text).not.toContain(PRIV);
    expect(text).not.toContain("ro-e2e");
  }

  try {
    const row = page.locator("tbody tr", { hasText: NAME });
    await expect(row.locator(".chip", { hasText: "SNMPv3" })).toBeVisible();
    await expect(row).toContainText("snmp://10.0.0.31");
    await expect(row).toContainText("hinterlegt");

    // Bearbeiten: Zugang und Protokolle vorgewählt, Passwörter leer mit „hinterlegt“
    await row.locator("button:has-text('Bearbeiten')").click();
    await expect(modal.locator("#ss-access")).toHaveValue("snmp");
    await expect(modal.locator("#ss-snmp-auth")).toHaveValue("sha512");
    await expect(modal.locator("#ss-snmp-priv")).toHaveValue("aes256");
    await expect(modal.locator("#ss-snmp-authpass")).toHaveValue("");
    await expect(modal.locator("#ss-snmp-privpass")).toHaveAttribute("placeholder", /hinterlegt/);
    await expect(modal.locator("#ss-snmp-context")).toHaveAttribute("placeholder", "leer = Standard-Context");
    await expect(modal.locator("#ss-hostkeys")).toHaveCount(0);
    // Wechsel zu SSH: Warnung, SSH-Felder ohne „hinterlegt“
    await modal.locator("#ss-access").selectOption("ssh");
    await expect(modal).toContainText("Beim Wechsel des Zugangs werden die hinterlegten Zugangsdaten verworfen");
    await expect(modal.locator("#ss-password")).toHaveAttribute("placeholder", "Passwort des SSH-Benutzers");
    await modal.locator("button:has-text('Abbrechen')").click();
    await expect(modal).toHaveCount(0);
  } finally {
    const removed = await page.request.delete(`/api/systems/${created.id}`);
    expect(removed.ok()).toBe(true);
  }
});

test("Englisch: Zugang SNMPv3 im Quellsystem-Dialog", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("automato_lang", "en"));
  await page.goto("/#tenants");
  await page.click("button:has-text('+ Source system')");
  const modal = page.locator(".modal");
  await modal.locator("#ss-platform").selectOption("cisco_switch");
  await expect(modal.locator("label[for='ss-access']")).toHaveText("Access");
  await modal.locator("#ss-access").selectOption("snmp");
  await expect(modal.locator("#ss-snmp-mibs")).toContainText("Only standard MIBs are read");
  await expect(modal.locator("label[for='ss-snmp-user']")).toHaveText("SNMPv3 user (read view only)");
  await expect(modal.locator("#ss-snmp")).toContainText("writing (SET) does not exist in the collector");
  await modal.locator("button:has-text('Cancel')").click();
});
