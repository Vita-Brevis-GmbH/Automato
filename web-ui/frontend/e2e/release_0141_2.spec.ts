// 0.141.2 (Anwender 2026-11-06: Klick auf die Firewall-Regel in den Findings – „/api/rules/… 404“): gibt es die Regel
// nicht mehr, sagt das Detail das statt eines Fehlers.
import { expect, test } from "./fixtures";

test("Regel gibt es nicht mehr: klare Meldung statt 404 (0.141.2)", async ({ page, problems }) => {
  await page.goto("/#regeln?rule=987654321");
  await expect(page.locator("#rule-gone")).toContainText("Diese Regel gibt es nicht mehr (Kennung 987654321)");
  await expect(page.locator(".error")).toHaveCount(0);
  // erwartet: das Detail fragt die Regel ab (404) - nur das, sonst kein Fehler
  const gone = problems.filter((p) => /^404 .*\/api\/rules\/987654321$/.test(p));
  expect(gone.length).toBeGreaterThan(0);
  problems.splice(0, problems.length, ...problems.filter((p) => !gone.includes(p)));
});
