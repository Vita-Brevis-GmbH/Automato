import { useEffect, useState } from "react";
import { Application, ApplicationDetail, fetchApplication, fetchApplications } from "./api";
import { ActionBadge } from "./ui";

function ext(a: Application): string {
  let e = a.ext_ip ?? "?";
  if (a.ext_port) e += `:${a.ext_port}`;
  return e;
}

function target(a: Application): string {
  let t = a.mapped_ip ?? "?";
  if (a.mapped_port) t += `:${a.mapped_port}`;
  if (a.protocol) t += `/${a.protocol}`;
  return t;
}

function toMarkdown(d: ApplicationDetail): string {
  const lines = [
    `# Applikation: ${d.name}`,
    "",
    `- **Extern:** ${ext(d)}`,
    `- **Internes Ziel:** ${target(d)}`,
    `- **Port-Forward:** ${d.portforward ? "an" : "aus"}`,
    "",
    `## Regeln (${d.rules.length})`,
    "",
    "| Aktion | Name | Quellen | Dienste |",
    "|---|---|---|---|",
    ...d.rules.map(
      (r) =>
        `| ${r.action} | ${r.name} | ${r.sources.join(", ") || "–"} | ${
          r.services.join(", ") || "–"
        } |`,
    ),
    "",
    `_Erzeugt von Automato – ${new Date().toISOString().slice(0, 10)}_`,
    "",
  ];
  return lines.join("\n");
}

function download(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function Drawer({ name, onClose }: { name: string; onClose: () => void }) {
  const [d, setD] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setD(null);
    fetchApplication(name)
      .then(setD)
      .catch((e) => setError(String(e)));
  }, [name]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose} aria-label="Schliessen">
          ×
        </button>
        {error && <p className="notice">Fehler: {error}</p>}
        {!d && !error && <p className="notice">Lade…</p>}
        {d && (
          <>
            <h2>{d.name}</h2>
            <dl className="kv">
              <dt>Extern</dt>
              <dd>
                <code>{ext(d)}</code>
              </dd>
              <dt>Intern</dt>
              <dd>
                <code>{target(d)}</code>
              </dd>
              <dt>Port-Forward</dt>
              <dd>{d.portforward ? "an" : "aus"}</dd>
            </dl>
            <button onClick={() => download(`${d.name}.md`, toMarkdown(d))}>
              ⬇ Export (Markdown)
            </button>
            <div className="section-title">Regeln ({d.rules.length})</div>
            <table>
              <thead>
                <tr>
                  <th>Aktion</th>
                  <th>Name</th>
                  <th>Quellen</th>
                  <th>Dienste</th>
                </tr>
              </thead>
              <tbody>
                {d.rules.map((r) => (
                  <tr key={r.id} style={{ cursor: "default" }}>
                    <td>
                      <ActionBadge action={r.action} />
                    </td>
                    <td>{r.name}</td>
                    <td>
                      {r.sources.map((s) => (
                        <span className="chip" key={s}>
                          {s}
                        </span>
                      ))}
                    </td>
                    <td>
                      {r.services.map((s) => (
                        <span className="chip" key={s}>
                          {s}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

export default function Applications() {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    fetchApplications()
      .then(setApps)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="notice">Applikationen konnten nicht geladen werden: {error}</p>;
  if (apps === null) return <p className="notice">Lade Applikationen…</p>;
  if (apps.length === 0)
    return <p className="notice">Keine publizierten Dienste (VIPs) erfasst.</p>;

  return (
    <>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Applikation</th>
              <th>Extern</th>
              <th>→ Intern</th>
              <th className="num">Regeln</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => (
              <tr key={a.name} onClick={() => setSelected(a.name)}>
                <td>{a.name}</td>
                <td>
                  <code>{ext(a)}</code>
                </td>
                <td>
                  <code>{target(a)}</code>
                </td>
                <td className="num">{a.rule_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && <Drawer name={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
