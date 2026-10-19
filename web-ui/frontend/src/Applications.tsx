import { useEffect, useState } from "react";
import { Application, ApplicationDetail, fetchApplication, fetchApplications } from "./api";
import { ActionBadge, Dash, download, Drawer, Empty, ErrorBox, Icon, Loading } from "./ui";

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
      (r) => `| ${r.action} | ${r.name} | ${r.sources.join(", ") || "–"} | ${r.services.join(", ") || "–"} |`,
    ),
    "",
    `_Erzeugt von Automato – ${new Date().toISOString().slice(0, 10)}_`,
    "",
  ];
  return lines.join("\n");
}

function AppDrawer({
  name,
  tenantId,
  onClose,
}: {
  name: string;
  tenantId: number | null;
  onClose: () => void;
}) {
  const [d, setD] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setD(null);
    fetchApplication(name, tenantId)
      .then(setD)
      .catch(setError);
  }, [name, tenantId]);

  return (
    <Drawer title={name} sub="Publizierter Dienst (VIP)" onClose={onClose}>
      <ErrorBox error={error} />
      {!d && !error && <Loading />}
      {d && (
        <>
          <dl className="kv">
            <dt>Extern</dt>
            <dd className="mono">{ext(d)}</dd>
            <dt>Intern</dt>
            <dd className="mono">{target(d)}</dd>
            <dt>Port-Forward</dt>
            <dd>{d.portforward ? <span className="chip ok">an</span> : "aus"}</dd>
          </dl>
          <button className="btn btn-ghost btn-sm" onClick={() => download(`${d.name}.md`, toMarkdown(d))}>
            <Icon name="download" size={14} /> Export (Markdown)
          </button>
          <div className="sub-h">Regeln ({d.rules.length})</div>
          {d.rules.length === 0 ? (
            <Dash />
          ) : (
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
                  <tr key={r.id}>
                    <td>
                      <ActionBadge action={r.action} />
                    </td>
                    <td>{r.name}</td>
                    <td>
                      <div className="objs">
                        {r.sources.map((s) => (
                          <span className="mono" key={s}>
                            {s}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="chips">
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
          )}
        </>
      )}
    </Drawer>
  );
}

export default function Applications({ tenantId }: { tenantId: number | null }) {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setApps(null);
    setError(null);
    fetchApplications(tenantId)
      .then(setApps)
      .catch((e) => {
        setError(e);
        setApps([]);
      });
  }, [tenantId]);

  return (
    <>
      <ErrorBox error={error} prefix="Dienste laden" />
      <div className="card">
        <div className="card-body scroll">
          {apps === null ? (
            <Loading>Lade Dienste…</Loading>
          ) : apps.length === 0 ? (
            <Empty title="Keine publizierten Dienste">Es wurden (noch) keine VIPs erfasst.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Dienst</th>
                  <th>Extern</th>
                  <th>→ Intern</th>
                  <th className="num">Regeln</th>
                </tr>
              </thead>
              <tbody>
                {apps.map((a) => (
                  <tr key={a.name} className="clickable" onClick={() => setSelected(a.name)}>
                    <td>
                      <b>{a.name}</b>
                    </td>
                    <td className="mono">{ext(a)}</td>
                    <td className="mono">{target(a)}</td>
                    <td className="num">{a.rule_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {selected && <AppDrawer name={selected} tenantId={tenantId} onClose={() => setSelected(null)} />}
    </>
  );
}
