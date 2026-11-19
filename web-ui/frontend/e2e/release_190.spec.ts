// 1.9.0 (Anwender 2026-11-11): VLANs je Standort, Native VLAN der Firewall-Netze vom Switch.
import { expect, test } from "./fixtures";

test("L2 · VLANs: je Standort aufklappbar (1.9.0)", async ({ page, problems }) => {
  // Anwender: „die VLANs sind entweder physischer Standort spezifisch oder für einen virtuellen Standort
  // spezifisch … pro Standort aufklappen“
  await page.goto("/#l2/vlans"); // seit 1.19.0 (ADR 0067) eigene Ansicht in L2
  const groups = page.locator("#cabling-vlans tbody tr.group-row");
  await expect(groups.first()).toBeVisible();
  await expect(groups.first()).toContainText(/Standort|without site|site/);
  if ((await groups.count()) > 1) await page.click("#cabling-vlans button:has-text('alle aufklappen')");
  const dmz = page.locator("#cabling-vlans tbody tr[data-vlan='30']").first();
  await expect(dmz).toBeVisible();
  // zuklappen: die VLAN-Zeilen der ersten Gruppe verschwinden
  const vlanRows = page.locator("#cabling-vlans tbody tr[data-vlan]");
  const before = await vlanRows.count();
  await groups.first().click();
  await expect(vlanRows).not.toHaveCount(before);
  expect(problems).toEqual([]);
});
