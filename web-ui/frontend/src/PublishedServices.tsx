import { useEffect, useState } from "react";
import { fetchPublishedService, fetchPublishedServices, PublishedService, PublishedServiceDetail } from "./api";
import { PLATFORM_LABEL } from "./refs";
import { ActionBadge, Dash, download, Drawer, Empty, ErrorBox, Icon, Loading } from "./ui";

function ext(a: PublishedService): string {
  let e = a.ext_ip ?? "?";
  if (a.ext_port) e += `:${a.ext_port}`;
  return e;
}

function target(a: PublishedService): string {
  let t = a.mapped_ip ?? "?";
  if (a.mapped_port) t += `:${a.mapped_port}`;
  if (a.protocol) t += `/${a.protocol}`;
  return t;
}

// Pool bzw. Real Server: „pool-web · 2 Server“ (NSX ALB) bzw. „3 Real Server“ (FortiGate)
function pool(a: PublishedService): string | null {
  const n = a.servers ?? 0;
  if (a.pool) return `${a.pool} · ${n} Server`;
  return n > 1 ? `${n} Real Server` : null;
}

function toMarkdown(d: PublishedServiceDetail): string {
  const lines = [
    `# Publizierter Dienst: ${d.name}`,
    "",
    `- **Extern:** ${ext(d)}`,
    `- **Internes Ziel:** ${target(d)}`,
    ...(d.source ? [`- **Quelle:** ${d.source} (${PLATFORM_LABEL[d.platform ?? ""] ?? d.platform})`] : []),
    ...(pool(d) ? [`- **Pool:** ${pool(d)}`] : []),
    ...(d.balancers ?? []).map(
      (lb) =>
        `- **Load Balancer:** NSX ALB ${lb.name} ${lb.ext_ip ?? ""}${lb.ext_port ? `:${lb.ext_port}` : ""}` +
        `${lb.pool ? ` (Pool ${lb.pool})` : ""}${lb.servers?.length ? ` → ${lb.servers.join(", ")}` : ""}`,
    ),
    ...(d.fronted_by ?? []).map((f) => `- **Über VIP:** ${f.name} ${f.ext_ip ?? ""}${f.ext_port ? `:${f.ext_port}` : ""}`),
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
  const [d, setD] = useState<PublishedServiceDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setD(null);
    fetchPublishedService(name, tenantId)
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
            {d.source && (
              <>
                <dt>Quelle</dt>
                <dd>
                  {d.source} <span className="faint">{PLATFORM_LABEL[d.platform ?? ""] ?? d.platform}</span>
                </dd>
              </>
            )}
            {pool(d) && (
              <>
                <dt>Pool</dt>
                <dd>{pool(d)}</dd>
              </>
            )}
            {(d.balancers ?? []).length > 0 && (
              <>
                <dt>Load Balancer</dt>
                <dd className="vip-balancers">
                  {(d.balancers ?? []).map((lb) => (
                    <div key={lb.id}>
                      <span className="chip info">NSX ALB</span> <b>{lb.name}</b>{" "}
                      <span className="mono">
                        {lb.ext_ip}
                        {lb.ext_port ? `:${lb.ext_port}` : ""}
                      </span>
                      {lb.pool && <span className="faint"> · Pool {lb.pool}</span>}
                      {lb.servers && lb.servers.length > 0 && <div className="mono faint">→ {lb.servers.join(", ")}</div>}
                    </div>
                  ))}
                </dd>
              </>
            )}
            {(d.fronted_by ?? []).length > 0 && (
              <>
                <dt>Über VIP</dt>
                <dd className="vip-fronted">
                  {(d.fronted_by ?? []).map((f) => (
                    <div key={f.name}>
                      <b>{f.name}</b>{" "}
                      <span className="mono">
                        {f.ext_ip}
                        {f.ext_port ? `:${f.ext_port}` : ""}
                      </span>
                      {f.source && <span className="faint"> · {f.source}</span>}
                    </div>
                  ))}
                </dd>
              </>
            )}
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

export default function PublishedServices({ tenantId }: { tenantId: number | null }) {
  const [apps, setApps] = useState<PublishedService[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setApps(null);
    setError(null);
    fetchPublishedServices(tenantId)
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
                  <th>Quelle</th>
                  <th className="num">Regeln</th>
                </tr>
              </thead>
              <tbody>
                {apps.map((a, i) => (
                  <tr
                    key={`${a.name}|${a.ext_ip}|${a.ext_port}|${i}`}
                    className="clickable"
                    data-platform={a.platform ?? undefined}
                    onClick={() => setSelected(a.name)}
                  >
                    <td>
                      <b>{a.name}</b>
                    </td>
                    <td className="mono">{ext(a)}</td>
                    <td className="mono">
                      {target(a)}
                      {pool(a) && <div className="faint">{pool(a)}</div>}
                      {(a.balancers ?? []).map((lb) => (
                        <div key={lb.id} className="faint vip-chain" title="Ziel ist ein Virtual Service des NSX ALB">
                          → NSX ALB {lb.name} · {(lb.servers ?? []).length} Server
                        </div>
                      ))}
                      {(a.fronted_by ?? []).length > 0 && (
                        <div className="faint vip-chain" title="Die FortiGate veröffentlicht diesen Virtual Service über ihre VIP">
                          über {(a.fronted_by ?? []).map((f) => `${f.name} (${f.ext_ip ?? "?"})`).join(", ")}
                        </div>
                      )}
                    </td>
                    <td>
                      {a.source ?? <Dash />}
                      {a.platform === "avi" && <span className="chip info" style={{ marginLeft: 6 }}>NSX ALB</span>}
                    </td>
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
