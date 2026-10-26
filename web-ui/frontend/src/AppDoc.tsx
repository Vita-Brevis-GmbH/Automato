import { ReactNode, useEffect, useMemo, useState } from "react";
import {
  AppDocSystem,
  assignSystemSite,
  DocHide,
  DocTable,
  fetchAppDocSystems,
  fetchSites,
  fetchSystemDoc,
  Site,
  SystemDoc,
} from "./api";
import { siteLabel, SiteOptions } from "./sites";
import type { View, ViewParams } from "./App";
import Apps from "./Apps";
import type { Me } from "./auth";
import PublishedServices from "./PublishedServices";
import { download, Empty, ErrorBox, Expandable, Icon, Loading, PageHead } from "./ui";

type Mode = "apps" | "doc" | "vips";

// Applikationen: (a) Applikationen als fachliche Klammer (ADR 0012) – Klick öffnet ihre Doku
// (#apps?app=<id>), (b) Applikations-Doku pro System (10 Kategorien-Tabellen nach
// uc1-applikations-doku-spec.md) inkl. Markdown-Export, (c) publizierte Dienste (VIPs).
export default function AppDoc({
  me,
  tenantId,
  params = {},
  onGo,
}: {
  me: Me;
  tenantId: number | null;
  params?: ViewParams;
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const [mode, setMode] = useState<Mode>("apps");
  const appId = params.app ? Number(params.app) : null;
  const intro: Record<Mode, ReactNode> = {
    apps: (
      <>
        Eine <b>Applikation</b> fasst Application Profiles, EPGs und Server zusammen. Ein Klick öffnet ihre Doku:
        VMs, EPGs und alle Regeln mit Richtung und Bezug.
      </>
    ),
    doc: (
      <>
        Mittelpunkt ist das <b>System</b> (Container/VM/Server); seine Zugriffe sind nach Art gruppiert. „ACI“ =
        erscheint erst mit Cisco ACI (Model 1).
      </>
    ),
    vips: "Über VIPs publizierte Dienste mit den zugehörigen Firewall-Regeln.",
  };
  const tab = (m: Mode, label: string) => (
    <button
      className={mode === m ? "on" : ""}
      onClick={() => {
        setMode(m);
        if (appId != null) onGo?.("apps"); // Doku schliessen
      }}
    >
      {label}
    </button>
  );
  return (
    <>
      <PageHead
        title="Applikationen"
        actions={
          <div className="seg no-print">
            {tab("apps", "Applikationen")}
            {tab("doc", "Doku je System")}
            {tab("vips", "Publizierte Dienste")}
          </div>
        }
      >
        <span className="no-print">{intro[mode]}</span>
      </PageHead>
      {mode === "apps" && (
        <Apps
          me={me}
          tenantId={tenantId}
          appId={appId}
          onOpen={(id) => onGo?.("apps", { app: String(id) })}
          onBack={() => onGo?.("apps")}
          onGo={onGo}
        />
      )}
      {mode === "doc" && <SystemDocView me={me} tenantId={tenantId} />}
      {mode === "vips" && <PublishedServices tenantId={tenantId} />}
    </>
  );
}

function SystemDocView({ me, tenantId }: { me: Me; tenantId: number | null }) {
  const [systems, setSystems] = useState<AppDocSystem[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [doc, setDoc] = useState<SystemDoc | null>(null);
  const [sites, setSites] = useState<Site[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [showEmpty, setShowEmpty] = useState(false);

  useEffect(() => {
    if (tenantId == null) {
      setSites([]);
      return;
    }
    fetchSites(tenantId)
      .then(setSites)
      .catch(() => setSites([]));
  }, [tenantId]);

  useEffect(() => {
    setSystems(null);
    setSelected(null);
    setDoc(null);
    setErr(null);
    fetchAppDocSystems(tenantId)
      .then((s) => {
        setSystems(s);
        setSelected(s[0]?.name ?? null);
      })
      .catch((e) => {
        setErr(e);
        setSystems([]);
      });
  }, [tenantId]);

  useEffect(() => {
    if (!selected) return;
    setDoc(null);
    fetchSystemDoc(selected, tenantId)
      .then(setDoc)
      .catch(setErr);
  }, [selected, tenantId]);

  const markdown = useMemo(() => (doc ? toMarkdown(doc) : ""), [doc]);
  const selKind = systems?.find((s) => s.name === selected)?.kind ?? "host";

  const assign = async (siteId: number | null) => {
    if (tenantId == null || !doc) return;
    setErr(null);
    try {
      setDoc(await assignSystemSite(doc.system, tenantId, siteId));
    } catch (e) {
      setErr(e);
    }
  };

  if (systems === null) return <Loading>Lade Systeme…</Loading>;

  const tables = doc ? doc.tables.filter((t) => showEmpty || t.rows.length > 0) : [];

  return (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {systems.length === 0 ? (
        <div className="card">
          <Empty title="Keine Systeme">
            Systeme (Host-Objekte bzw. EPGs in Regeln) erscheinen nach dem ersten Lauf für diesen Mandanten.
          </Empty>
        </div>
      ) : (
        <>
          <div className="filterbar">
            <select className="sel" value={selected ?? ""} onChange={(e) => setSelected(e.target.value)}>
              {systems.map((s) => (
                <option key={s.name} value={s.name}>
                  System: {s.name}
                  {s.ip && s.ip !== s.name ? ` (${s.ip})` : ""} · {s.rule_count} Regeln{s.kind === "epg" ? " · EPG" : ""}
                </option>
              ))}
            </select>
            {doc && selKind !== "epg" && (
              <select
                className="sel"
                value={doc.site?.id ?? ""}
                disabled={tenantId == null || !me.isOperator}
                title={
                  tenantId == null
                    ? "Zum Verorten oben einen Mandanten wählen"
                    : !me.isOperator
                      ? "Verorten erfordert die Rolle Operator"
                      : "Standort des Systems"
                }
                onChange={(e) => assign(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Standort: – keiner –</option>
                {tenantId == null && doc.site ? (
                  <option value={doc.site.id}>Standort: {siteLabel(doc.site)}</option>
                ) : (
                  <SiteOptions sites={sites} />
                )}
              </select>
            )}
            <button className={`toggle ${showEmpty ? "on" : ""}`} onClick={() => setShowEmpty((v) => !v)}>
              leere Kategorien zeigen
            </button>
            <div className="grow" />
            <button
              className="btn btn-ghost btn-sm"
              disabled={!doc}
              onClick={() => doc && download(`${doc.system}.md`, markdown)}
            >
              <Icon name="download" size={14} /> Export (Markdown)
            </button>
          </div>

          {doc && (
            <div className="chips" style={{ marginBottom: 14 }}>
              {doc.system_ip && <span className="chip">IP {doc.system_ip}</span>}
              {doc.tenant_code && <span className="chip local">GG {doc.tenant_code}</span>}
              {selKind === "epg" ? (
                <span className="chip warn">EPG (ACI)</span>
              ) : doc.site ? (
                <span className="chip" title={doc.site.kind === "physical" ? "physischer Standort" : "virtueller Standort"}>
                  Standort {siteLabel(doc.site)}
                </span>
              ) : (
                <span className="chip na">ohne Standort</span>
              )}
              {(doc.vms ?? []).slice(0, MAX_VMS).map((v) => (
                <span
                  key={`${v.name}-${v.portgroup}`}
                  className={`chip ${v.power_state === "poweredOn" ? "ok" : "na"}`}
                  title={[v.portgroup && `Portgruppe ${v.portgroup}`, v.epg && `EPG ${v.epg}`, ...(v.ips ?? [])]
                    .filter(Boolean)
                    .join(" · ")}
                >
                  VM {v.name}
                </span>
              ))}
              {(doc.vms?.length ?? 0) > MAX_VMS && <span className="chip na">+{doc.vms!.length - MAX_VMS} VMs</span>}
            </div>
          )}

          {doc?.impossible?.length ? (
            <details className="card" style={{ marginBottom: 14 }} id="doc-impossible">
              <summary className="card-head" style={{ cursor: "pointer" }}>
                <h2>Routingtechnisch nicht möglich</h2>
                <span className="faint">
                  {doc.impossible.length} Treffer (Regel als Quelle bzw. Ziel) erfassen das System, die Firewall leitet es
                  aber über ein anderes Interface – nicht in den Kategorien
                </span>
              </summary>
              <div className="card-body pad">
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {doc.impossible.map((x) => (
                    <li key={`${x.rule_id}-${x.side}`}>
                      <b>{x.description}</b> <span className="faint">als {x.side === "src" ? "Quelle" : "Ziel"}:</span>{" "}
                      {x.text}
                    </li>
                  ))}
                </ul>
              </div>
            </details>
          ) : null}

          {!doc ? (
            <Loading />
          ) : tables.length === 0 ? (
            <div className="card">
              <Empty title="Noch keine Zugriffe kategorisiert">
                Für dieses System gibt es keine zugeordneten Regeln.
              </Empty>
            </div>
          ) : (
            <div className="stack">
              {tables.map((t) => (
                <DocTableView key={t.index} table={t} />
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

// Zelle der Kategorien; „Description“ öffnet die Regel (bei einem Paar je Familie ein Link)
function DocCell({
  row,
  column,
  onRule,
}: {
  row: DocTable["rows"][number];
  column: string;
  onRule?: (id: number) => void;
}) {
  const value = row[column];
  const text = Array.isArray(value) ? value.join(", ") : value;
  const systems = Array.isArray(row._systems) ? (row._systems as string[]) : [];
  if (column === "Server Name" && systems.length)
    return (
      <>
        {text || <span className="faint">–</span>}
        <Expandable items={systems} noun={["System", "Systeme"]} />
      </>
    );
  if (column !== "Description" || !onRule) return <>{text || <span className="faint">–</span>}</>;
  const ids: number[] = Array.isArray(row._rules)
    ? (row._rules as unknown[]).filter((x): x is number => typeof x === "number")
    : typeof row._rule === "number"
      ? [row._rule]
      : [];
  if (!ids.length) return <>{text || <span className="faint">–</span>}</>;
  return (
    <>
      <button className="linkbtn" title="Regel im Detail" onClick={() => onRule(ids[0])}>
        {text || `#${ids[0]}`}
      </button>
      {ids.length > 1 && (
        <span className="faint">
          {" "}
          ·{" "}
          {ids.map((id, i) => (
            <span key={id}>
              {i > 0 && " · "}
              <button className="linkbtn" onClick={() => onRule(id)}>
                {i === 0 ? "IPv4" : "IPv6"}
              </button>
            </span>
          ))}
        </span>
      )}
    </>
  );
}

export type DocRow = DocTable["rows"][number];

// Ausblenden je Kategorie (0.69.0, nur Applikations-Doku): Knopf je Zeile, Liste der ausgeblendeten
export interface HideControls {
  canEdit: boolean;
  onHide: (row: DocRow) => void;
  onShow: (hides: DocHide[]) => void;
}

const dateText = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString("de-CH") : "");

export function DocTableView({
  table,
  onRule,
  hide,
}: {
  table: DocTable;
  onRule?: (id: number) => void;
  hide?: HideControls;
}) {
  const aci = table.enforcement === "aci";
  const hidden = table.hidden ?? [];
  const editable = !!hide?.canEdit;
  return (
    <div className="card">
      <div className="card-head">
        <h2>
          {table.index}. {table.title}
        </h2>
        <span className="chip">{aci ? "ACI intern" : "Über eine Firewall"}</span>
        {aci && <span className="chip warn">ACI · Model 1</span>}
        <div className="spacer" />
        <span className="mono faint" style={{ fontSize: 12 }}>
          {table.rows.length} Regel{table.rows.length === 1 ? "" : "n"}
          {hidden.length > 0 ? ` · ${hidden.length} ausgeblendet` : ""}
        </span>
      </div>
      <div className="card-body scroll">
        {table.rows.length === 0 ? (
          <div className="loading">Keine Einträge.</div>
        ) : (
          <table>
            <thead>
              <tr>
                {table.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
                {editable && <th className="no-print" />}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, i) => (
                <tr key={i}>
                  {table.columns.map((c, ci) => (
                    <td key={c} className={ci === 1 ? "mono" : ""}>
                      {c === "Aktion" && row[c] ? (
                        <span className={`chip ${row[c] === "Allow" ? "accept" : "deny"}`}>{String(row[c])}</span>
                      ) : (
                        <DocCell row={row} column={c} onRule={onRule} />
                      )}
                    </td>
                  ))}
                  {editable && (
                    <td className="no-print">
                      {Array.isArray(row._rules) && row._rules.length > 0 && (
                        <button
                          className="linkbtn doc-hide-btn"
                          title="In dieser Kategorie ausblenden – nicht relevant für die Applikation (mit Begründung)"
                          onClick={() => hide!.onHide(row)}
                        >
                          ausblenden
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {hidden.length > 0 && (
          <details className="doc-hidden" id={`doc-hidden-${table.index}`}>
            <summary>
              {hidden.length} ausgeblendet <span className="faint">– nicht relevant für die Applikation, mit Begründung</span>
            </summary>
            <table className="compact">
              <thead>
                <tr>
                  <th>Regel</th>
                  <th>System bzw. Objekt</th>
                  <th>Begründung</th>
                  <th>von</th>
                  <th>am</th>
                  {editable && <th className="no-print" />}
                </tr>
              </thead>
              <tbody>
                {hidden.map((row, i) => {
                  const hs = row._hides ?? [];
                  return (
                    <tr key={i}>
                      <td>{String(row.Description ?? "")}</td>
                      <td>{String(row["Server Name"] ?? row.Source ?? "")}</td>
                      <td>
                        {hs.map((h) => (
                          <div key={h.id}>
                            {h.reason}
                            {h.category == null && <span className="faint"> (in allen Kategorien)</span>}
                          </div>
                        ))}
                      </td>
                      <td>{[...new Set(hs.map((h) => h.by))].join(", ")}</td>
                      <td>{dateText(hs[0]?.at)}</td>
                      {editable && (
                        <td className="no-print">
                          <button className="linkbtn" onClick={() => hide!.onShow(hs)}>
                            einblenden
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </details>
        )}
      </div>
    </div>
  );
}

const MAX_VMS = 8; // Kopf der Doku: weitere VMs nur als Anzahl

function toMarkdown(doc: SystemDoc): string {
  const lines: string[] = [`# Applikations-Doku – ${doc.system}`, ""];
  const meta: string[] = [];
  if (doc.system_ip) meta.push(`IP: ${doc.system_ip}`);
  if (doc.tenant_code) meta.push(`GG: ${doc.tenant_code}`);
  if (doc.site) meta.push(`Standort: ${siteLabel(doc.site)}`);
  if (doc.vms?.length) meta.push(`VM: ${[...new Set(doc.vms.map((v) => v.name))].join(", ")}`);
  if (meta.length) lines.push(meta.join(" · "), "");
  return lines.concat(tablesMarkdown(doc.tables)).join("\n");
}

const mdCell = (v: string) => v.replace(/\|/g, "\\|").replace(/\n/g, " ");

// Tabellen 1–10 als Markdown (leere Kategorien entfallen) samt Fusszeile.
export function tablesMarkdown(tables: DocTable[]): string[] {
  const lines: string[] = [];
  for (const t of tables) {
    if (t.rows.length === 0) continue;
    lines.push(`## ${t.index}. ${t.title}`, "");
    lines.push(`| ${t.columns.join(" | ")} |`);
    lines.push(`| ${t.columns.map(() => "---").join(" | ")} |`);
    for (const row of t.rows) {
      lines.push(`| ${t.columns.map((c) => mdCell(String(row[c] ?? ""))).join(" | ")} |`);
    }
    lines.push("");
  }
  // 0.69.0: ausgeblendete Regeln mit Begründung (Nachweis, warum sie in den Kategorien fehlen)
  const hidden = tables.flatMap((t) => (t.hidden ?? []).map((row) => ({ t, row })));
  if (hidden.length) {
    lines.push("## Ausgeblendete Regeln", "", "| Kategorie | Regel | System bzw. Objekt | Begründung | von | am |", "| --- | --- | --- | --- | --- | --- |");
    for (const { t, row } of hidden) {
      const hs = row._hides ?? [];
      lines.push(
        `| ${t.index}. ${mdCell(t.title)} | ${mdCell(String(row.Description ?? ""))} | ${mdCell(String(row["Server Name"] ?? row.Source ?? ""))} | ${mdCell(hs.map((h) => h.reason).join("; "))} | ${mdCell([...new Set(hs.map((h) => h.by))].join(", "))} | ${dateText(hs[0]?.at)} |`,
      );
    }
    lines.push("");
  }
  lines.push(`_Erzeugt von Automato – ${new Date().toISOString().slice(0, 10)}_`, "");
  return lines;
}
