import { useEffect, useMemo, useState } from "react";
import { AppDocSystem, DocTable, fetchAppDocSystems, fetchSystemDoc, SystemDoc } from "./api";

// Applikations-Doku: pro System (Host) die 10 Kategorien-Tabellen (Spec
// uc1-applikations-doku-spec.md) + Markdown-Export. tenantId kommt vom Switcher.
export default function AppDoc({ tenantId }: { tenantId: number | null }) {
  const [systems, setSystems] = useState<AppDocSystem[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [doc, setDoc] = useState<SystemDoc | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
    setDoc(null);
    fetchAppDocSystems(tenantId)
      .then((s) => {
        setSystems(s);
        setSelected(s[0]?.name ?? null);
      })
      .catch((e) => setErr(`Systeme laden fehlgeschlagen: ${e}`));
  }, [tenantId]);

  useEffect(() => {
    if (!selected) return;
    fetchSystemDoc(selected, tenantId)
      .then(setDoc)
      .catch((e) => setErr(`Doku laden fehlgeschlagen: ${e}`));
  }, [selected, tenantId]);

  const markdown = useMemo(() => (doc ? toMarkdown(doc) : ""), [doc]);

  if (err) return <p className="err">{err}</p>;
  if (systems === null) return <p className="notice">Lade Systeme…</p>;
  if (systems.length === 0)
    return <p className="notice">Keine Systeme (Host-Objekte in Regeln) für diesen Mandanten.</p>;

  const download = () => {
    const blob = new Blob([markdown], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${doc?.system ?? "applikation"}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      <div className="form-row">
        <span className="muted">System</span>
        <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value)}>
          {systems.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name} ({s.rule_count})
            </option>
          ))}
        </select>
        <button className="btn" onClick={download} disabled={!doc}>
          Markdown-Export
        </button>
      </div>

      {doc &&
        doc.tables
          .filter((t) => t.rows.length > 0)
          .map((t) => <DocTableView key={t.index} table={t} />)}
      {doc && doc.tables.every((t) => t.rows.length === 0) && (
        <p className="notice">Für dieses System sind noch keine Zugriffe kategorisiert.</p>
      )}
    </div>
  );
}

function DocTableView({ table }: { table: DocTable }) {
  return (
    <div style={{ marginBottom: "1.2rem" }}>
      <div className="section-title" style={{ padding: "0 .2rem" }}>
        {table.index}. {table.title}
        {table.enforcement === "aci" && <span className="muted"> · ACI</span>}
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr>
              {table.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {table.columns.map((c) => (
                  <td key={c}>{row[c] || <span className="muted">–</span>}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function toMarkdown(doc: SystemDoc): string {
  const lines: string[] = [`# Applikations-Doku – ${doc.system}`, ""];
  if (doc.system_ip) lines.push(`IP: ${doc.system_ip}`, "");
  for (const t of doc.tables) {
    if (t.rows.length === 0) continue;
    lines.push(`## ${t.index}. ${t.title}`, "");
    lines.push(`| ${t.columns.join(" | ")} |`);
    lines.push(`| ${t.columns.map(() => "---").join(" | ")} |`);
    for (const row of t.rows) {
      lines.push(`| ${t.columns.map((c) => (row[c] ?? "").replace(/\|/g, "\\|")).join(" | ")} |`);
    }
    lines.push("");
  }
  return lines.join("\n");
}
