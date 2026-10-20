import { ReactNode, useEffect, useMemo, useState } from "react";
import {
  AppDocSystem,
  assignSystemSite,
  DocTable,
  fetchAppDocSystems,
  fetchSites,
  fetchSystemDoc,
  Site,
  SystemDoc,
} from "./api";
import Apps from "./Apps";
import type { Me } from "./auth";
import PublishedServices from "./PublishedServices";
import { download, Empty, ErrorBox, Icon, Loading, PageHead } from "./ui";

type Mode = "apps" | "doc" | "vips";

// Applikationen: (a) Applikationen als fachliche Klammer (ADR 0012) mit Doku je Applikation,
// (b) Applikations-Doku pro System (10 Kategorien-Tabellen nach uc1-applikations-doku-spec.md)
// inkl. Markdown-Export, (c) publizierte Dienste (VIPs).
export default function AppDoc({ me, tenantId }: { me: Me; tenantId: number | null }) {
  const [mode, setMode] = useState<Mode>("apps");
  const intro: Record<Mode, ReactNode> = {
    apps: (
      <>
        Eine <b>Applikation</b> fasst Application Profiles, EPGs und Server zusammen; ihre Doku zeigt die
        Zugriffe aller Systeme.
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
    <button className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
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
      {mode === "apps" && <Apps me={me} tenantId={tenantId} />}
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
                {(tenantId == null && doc.site ? [doc.site] : sites).map((s) => (
                  <option key={s.id} value={s.id}>
                    Standort: {s.code} {s.name}
                  </option>
                ))}
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
                <span className="chip">
                  Standort {doc.site.code} · {doc.site.name}
                </span>
              ) : (
                <span className="chip na">ohne Standort</span>
              )}
            </div>
          )}

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

export function DocTableView({ table }: { table: DocTable }) {
  const aci = table.enforcement === "aci";
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
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, i) => (
                <tr key={i}>
                  {table.columns.map((c, ci) => (
                    <td key={c} className={ci === 1 ? "mono" : ""}>
                      {row[c] || <span className="faint">–</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function toMarkdown(doc: SystemDoc): string {
  const lines: string[] = [`# Applikations-Doku – ${doc.system}`, ""];
  const meta: string[] = [];
  if (doc.system_ip) meta.push(`IP: ${doc.system_ip}`);
  if (doc.tenant_code) meta.push(`GG: ${doc.tenant_code}`);
  if (doc.site) meta.push(`Standort: ${doc.site.code} ${doc.site.name}`);
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
      lines.push(`| ${t.columns.map((c) => mdCell(row[c] ?? "")).join(" | ")} |`);
    }
    lines.push("");
  }
  lines.push(`_Erzeugt von Automato – ${new Date().toISOString().slice(0, 10)}_`, "");
  return lines;
}
