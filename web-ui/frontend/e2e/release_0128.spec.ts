// 0.128.0 (Anwender 2026-11-03: „Eine Internet Firewall sieht zwar vielleicht alle Tenants, soll sie aber nicht mit
// einander verbinden“, „Eventuell kannst du dies im Namensprofil einsetzen im Design“, „Generell sollte der Schalter aus
// sein als Default“): im Grundschema je Zone „verbindet gleiche Zonen“ (Vorgabe aus). Das Grundschema des Profils
// „default“ wird am Ende wieder entfernt (gemeinsame Beispieldaten).
import { readFileSync } from "fs";
import { expect, test } from "./fixtures";

const DRAWING = `<mxfile host="drawio"><diagram id="a" name="Zonen"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<object id="edge" label="Edge Internet" automato_kind="edge">
  <mxCell style="rounded=1;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="120" height="60" as="geometry"/></mxCell></object>
<object id="core" label="Core" automato_kind="core">
  <mxCell style="rounded=1;" vertex="1" parent="1"><mxGeometry x="200" y="0" width="120" height="60" as="geometry"/></mxCell></object>
<object id="tr" label="Transfer" automato_kind="transfer" automato_customer="je Kunde">
  <mxCell style="rounded=1;" vertex="1" parent="1"><mxGeometry x="400" y="0" width="120" height="60" as="geometry"/></mxCell></object>
</root></mxGraphModel></diagram></mxfile>`;

test("Grundschema: „verbindet gleiche Zonen“ je Zone, Vorgabe aus (0.128.0)", async ({ page, problems }) => {
  const profiles = (await (await page.request.get("/api/naming-profiles")).json()) as { id: number; name: string }[];
  const pid = profiles.find((p) => p.name === "default")!.id;
  const put = await page.request.put(`/api/naming-profiles/${pid}/zone-schema`, { data: { filename: "zonen.drawio", content: DRAWING } });
  expect(put.status()).toBe(200);
  try {
    await page.goto("/#dict");
    const card = page.locator("#zone-schema");
    await expect(card.locator("#zone-schema-zones tbody tr")).toHaveCount(3);
    await expect(card.locator(".schema-joins-same")).toHaveCount(0); // Vorgabe aus
    await card.locator("#zone-schema-edit").click();
    const editor = card.locator("#zone-schema-editor");
    const core = editor.locator("#zone-schema-edit-zones tbody tr[data-zone='Core']");
    await expect(core.getByLabel("verbindet gleiche Zonen")).not.toBeChecked();
    await core.getByLabel("verbindet gleiche Zonen").check(); // die Zonen-Firewall verbindet die Kunden
    await editor.locator("#zone-schema-edit-save").click();
    await expect(editor).toHaveCount(0);
    const rows = card.locator("#zone-schema-zones tbody tr");
    await expect(rows.filter({ hasText: "Core" }).locator(".schema-joins-same")).toHaveText("verbindet gleiche Zonen");
    await expect(rows.filter({ hasText: "Edge Internet" }).locator(".schema-joins-same")).toHaveCount(0);
    // Zeichnung zum Herunterladen trägt die Eigenschaft
    const [file] = await Promise.all([page.waitForEvent("download"), card.locator("button:has-text('Zeichnung herunterladen')").click()]);
    const text = readFileSync((await file.path())!, "utf-8");
    expect(text).toContain('automato_join_same="ja"');
    expect(text.match(/automato_join_same=/g)).toHaveLength(1);
  } finally {
    expect((await page.request.delete(`/api/naming-profiles/${pid}/zone-schema`)).status()).toBe(204);
  }
  expect(problems).toEqual([]);
});
