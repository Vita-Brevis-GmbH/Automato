// Verbindungen Ende zu Ende (0.45.0, Anwender): eine Zeile je Verbindung der Applikation, auch
// wenn mehrere Firewalls und das ACI hintereinander stehen – im Detail je Hop die Policy bzw.
// der Contract, der entscheidet, mit den Objekten; dazu der Abgleich mit den Regeln der Doku
// (Regel erlaubt, Weg sperrt bzw. Gerät nicht auf dem Weg). Seit 0.67.0 auch als Netzwerkplan:
// Systeme der Applikation → Knoten der Wege → Gegenstellen, je Linie Dienste und Interfaces.
import { Fragment, useEffect, useState } from "react";
import type { View, ViewParams } from "./App";
import {
  AppConnection,
  AppConnections,
  AppConnHop,
  AppConnSide,
  AppConnState,
  AppPlan,
  AppPlanVerdict,
  exportAppPlan,
  fetchAppConnections,
  snatText,
} from "./api";
import { Verdict, VERDICT } from "./Paths";
import { Dash, download, Empty, ErrorBox, Icon, Loading, Note } from "./ui";

type Go = (v: View, p?: ViewParams) => void;

const STATE: Record<AppConnState["state"], [string, string]> = {
  decides: ["ok", "entscheidet"],
  possible: ["warn", "möglich"],
  other: ["", "greift nicht"],
  off_path: ["crit", "nicht auf dem Weg"],
  unknown: ["na", "nicht prüfbar"],
};

function SideLabel({ s }: { s: AppConnSide }) {
  const many = s.systems.length + s.more;
  return (
    <div>
      <b>{s.label}</b>
      {s.kind === "system" && many > 1 && (
        <span className="faint" title={[...s.systems, s.more ? `+${s.more}` : ""].filter(Boolean).join(", ")}>
          {" "}
          +{many - 1}
        </span>
      )}
      <div className="faint mono">
        {s.ip}
        {s.name ? ` · ${s.name}` : ""}
      </div>
      {s.kind === "epg" && s.systems.length > 0 && (
        <div className="faint" title="VMs der EPG (Portgruppe) – geprüft mit der ersten">
          {s.systems.slice(0, 3).join(", ")}
          {s.systems.length + s.more > 3 ? ` +${s.systems.length + s.more - 3}` : ""}
        </div>
      )}
    </div>
  );
}

// Hop mit der entscheidenden Policy bzw. dem Contract – für Druck und Export (0.59.0)
export function hopText(h: AppConnHop): string {
  const deciding = h.checks.flatMap((c) =>
    c.rules.filter((r) => r.decides).map((r) => (h.checks.length > 1 ? `${c.label}: ` : "") + (r.label || r.name || r.external_id || "Regel")),
  );
  return deciding.length ? `${h.label} (${[...new Set(deciding)].join(", ")})` : h.label;
}

const sideText = (s: AppConnSide) => {
  const more = s.kind === "system" && s.systems.length + s.more > 1 ? ` +${s.systems.length + s.more - 1}` : "";
  return `${s.label}${more} (${s.ip}${s.name ? ` · ${s.name}` : ""})`;
};

const mdCell = (v: string) => v.replace(/\|/g, "\\|").replace(/\n/g, " ");

// Verbindungen Ende zu Ende als Markdown (Export der Applikations-Doku, 0.59.0)
export function connectionsMarkdown(data: AppConnections): string[] {
  const s = data.summary;
  const lines = ["## Verbindungen Ende zu Ende", ""];
  lines.push(
    `${s.total} Verbindungen · ${s.allow} erlaubt · ${s.blocked} gesperrt · ${s.open} abhängig bzw. offen · ${s.mismatch} Abweichungen`,
    "",
  );
  data.notes.forEach((n) => lines.push(`> ${n}`, ""));
  if (!data.connections.length) return [...lines, "Keine prüfbaren Verbindungen.", ""];
  lines.push("| Richtung | Quelle | Ziel | Dienst | Weg (entscheidende Regel) | Urteil | Abweichung |", "| --- | --- | --- | --- | --- | --- | --- |");
  for (const c of data.connections) {
    const way = c.hops.map(hopText).join(" → ") + (!c.reached && c.end ? ` – ${c.end.text}` : "");
    const verdict = c.text || (VERDICT[c.verdict]?.[1] ?? c.verdict);
    lines.push(
      `| ${c.direction === "in" ? "eingehend" : "ausgehend"} | ${mdCell(sideText(c.src))} | ${mdCell(sideText(c.dst))} | ${c.service ?? "alle"} | ${mdCell(way || "–")} | ${mdCell(verdict)} | ${mdCell(c.mismatch.join("; "))} |`,
    );
  }
  lines.push("");
  return lines;
}

// Weg als Folge der Knoten, je Knoten nach seinem Urteil eingefärbt
function HopChain({ hops }: { hops: AppConnHop[] }) {
  if (!hops.length) return <Dash />;
  return (
    <span className="conn-hops">
      {hops.map((h, i) => (
        <Fragment key={i}>
          {i > 0 && <span className="faint"> → </span>}
          <span className={`chip ${h.verdict ? VERDICT[h.verdict]?.[0] ?? "" : ""}`} title={h.checks.map((c) => `${c.label}: ${c.text}`).join("\n")}>
            {h.label}
          </span>
        </Fragment>
      ))}
    </span>
  );
}

function ConnDetail({ c, onRule, onGo }: { c: AppConnection; onRule: (id: number) => void; onGo?: Go }) {
  const params = { src: c.src.ip, dst: c.dst.ip, ...(c.service ? { service: c.service } : {}) };
  return (
    <div className="conn-detail">
      {c.mismatch.map((m) => (
        <Note key={m} warn>
          {m}
        </Note>
      ))}
      <div className="faint" style={{ marginBottom: 6 }}>
        {c.text}
        {c.end ? ` · Ende: ${c.end.text}` : ""}
        {c.start ? ` · Start an ${c.start} (Quelle an keinem gelesenen Gerät)` : ""}
        {c.paths > 1 ? ` · ${c.paths} aktive Wege (ECMP), gezeigt der erste` : ""}
        {c.parallel?.length ? ` · redundant über Verbund ${c.parallel.join(", ")}` : ""}
      </div>
      <table className="compact">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Knoten</th>
            <th>Urteil</th>
            <th title="die entscheidende Policy bzw. der Contract, davor die möglichen (blass)">Policy / Contract</th>
            <th title="Objekt der Policy bzw. EPG, über die die Quelle passt">Quelle</th>
            <th title="Objekt der Policy bzw. EPG, über die das Ziel passt">Ziel</th>
            <th>Dienste</th>
          </tr>
        </thead>
        <tbody>
          {c.hops.map((h, i) =>
            (h.checks.length ? h.checks : [null]).map((k, j) => {
              const rules = k?.rules ?? [];
              return (
                <tr key={`${i}-${j}`}>
                  {j === 0 && (
                    <td className="num" rowSpan={Math.max(1, h.checks.length)}>
                      {i + 1}
                    </td>
                  )}
                  <td>
                    <b>{k?.label ?? h.label}</b>
                    {k && (k.ingress || k.egress) && (
                      <div className="faint mono">
                        {k.ingress ?? "?"} → {k.egress ?? "?"}
                      </div>
                    )}
                    {k?.nat && (
                      <div className="faint">
                        DNAT {k.nat.name}: {k.nat.ext} → {k.nat.to.join(", ")}
                      </div>
                    )}
                    {k?.snat && (
                      <div className="faint">
                        SNAT → {k.snat.to ?? "?"} ({snatText(k.snat)})
                      </div>
                    )}
                    {k?.leak && <div className="faint">→ VRF {k.leak.vrf}</div>}
                  </td>
                  <td>{k ? <Verdict v={k.verdict} title={k.text} /> : <Dash />}</td>
                  <td>
                    {rules.length === 0 ? (
                      <span className="faint">{k?.text ?? "–"}</span>
                    ) : (
                      rules.map((r, n) => (
                        <div key={n} className={r.decides ? undefined : "faint"}>
                          {r.rule_id != null ? (
                            <button className="linkbtn" onClick={() => onRule(r.rule_id!)} title="Regel öffnen">
                              {r.label ?? `Policy ${r.external_id}`}
                            </button>
                          ) : (
                            r.label
                          )}
                          {!r.label && r.name ? ` „${r.name}“` : ""} · {r.action === "allow" ? "erlauben" : "sperren"}
                          {r.unsure.length ? ` · möglich (${r.unsure.join(", ")})` : ""}
                          {r.note ? ` · greift nicht: ${r.note}` : ""}
                        </div>
                      ))
                    )}
                    {k && (k.src_epg || k.dst_epg) && (
                      <div className="faint" title="Zuordnung im ACI">
                        {k.src_epg || "?"} → {k.dst_epg || "?"}
                      </div>
                    )}
                  </td>
                  <td className="mono">{rules.map((r, n) => <div key={n}>{r.src || "–"}</div>)}</td>
                  <td className="mono">{rules.map((r, n) => <div key={n}>{r.dst || "–"}</div>)}</td>
                  <td className="mono">{rules.map((r, n) => <div key={n}>{r.services.join(", ") || "–"}</div>)}</td>
                </tr>
              );
            }),
          )}
        </tbody>
      </table>
      <div style={{ marginTop: 8 }}>
        <b>Regeln der Doku</b>{" "}
        <span className="faint">– aus ihnen stammt die Verbindung</span>
        <ul className="conn-rules">
          {c.rules.map((r) => (
            <li key={r.rule_id}>
              <span className={`chip ${STATE[r.state][0]}`}>{STATE[r.state][1]}</span>{" "}
              <button className="linkbtn" onClick={() => onRule(r.rule_id)}>
                {r.label}
              </button>{" "}
              <span className="faint">
                {r.device} · {r.action === "allow" ? "erlauben" : "sperren"} · {r.text}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="no-print" style={{ marginTop: 6 }}>
        {onGo ? (
          <button className="btn btn-ghost btn-sm" onClick={() => onGo("wege", params)}>
            <Icon name="route" size={14} /> In der Weg-Suche öffnen
          </button>
        ) : (
          <a href={`#wege?${new URLSearchParams(params).toString()}`}>In der Weg-Suche öffnen</a>
        )}
      </div>
    </div>
  );
}

// -- Netzwerkplan (0.67.0) ------------------------------------------------------------------

const PLAN_COLOR: Record<AppPlanVerdict, string> = {
  allow: "var(--good)",
  deny: "var(--crit)",
  depends: "var(--warn)",
  unknown: "var(--faint)",
  blocked: "var(--border-strong)",
};
const PLAN_VERDICT: Record<AppPlanVerdict, string> = {
  allow: "erlaubt",
  deny: "gesperrt",
  depends: "abhängig",
  unknown: "unbekannt",
  blocked: "vorher gesperrt",
};
const PLAN_FILL = { own: "var(--info-weak)", hop: "var(--surface-2)", peer: "var(--surface)" };
const PLAN_STROKE = { own: "var(--info)", hop: "var(--accent)", peer: "var(--border-strong)" };
const short = (t: string, n = 30) => (t.length <= n ? t : `${t.slice(0, n - 1)}…`);
const VIEW_KEY = "automato_app_conn_view";

function loadView(): "table" | "plan" {
  try {
    return localStorage.getItem(VIEW_KEY) === "plan" ? "plan" : "table";
  } catch {
    return "table";
  }
}

function storeView(v: "table" | "plan") {
  try {
    localStorage.setItem(VIEW_KEY, v);
  } catch {
    // ohne Speicher: nur für diese Sitzung
  }
}

type Focus = { kind: "node" | "edge"; id: string } | null;

// Plan der Verbindungen: Filter der Karte bzw. Klick auf Knoten/Linie hebt hervor, der Rest wird
// blass; der Export nimmt die hervorgehobenen Verbindungen
function PlanView({
  appId,
  plan,
  conns,
  shown,
  filtered,
  onOpen,
}: {
  appId: number;
  plan: AppPlan;
  conns: AppConnection[];
  shown: Set<string>;
  filtered: boolean;
  onOpen: (key: string) => void;
}) {
  const [focus, setFocus] = useState<Focus>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const byId = new Map(plan.nodes.map((n) => [n.id, n]));
  const focused = focus ? (focus.kind === "node" ? byId.get(focus.id)?.conns : plan.edges.find((e) => e.id === focus.id)?.conns) ?? [] : null;
  const active = new Set((focused ?? [...shown]).filter((k) => shown.has(k)));
  const narrowed = filtered || focus !== null;
  const lit = (keys: string[]) => !narrowed || keys.some((k) => active.has(k));
  const pick = (kind: "node" | "edge", id: string) => setFocus((f) => (f && f.kind === kind && f.id === id ? null : { kind, id }));
  const title = (id: string) => byId.get(id)?.title ?? id;
  const exportPlan = async (format: "svg" | "drawio") => {
    setBusy(format);
    setErr(null);
    try {
      const { blob, filename } = await exportAppPlan(appId, format, narrowed ? [...active] : undefined);
      download(filename, blob);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };
  const listed = focus ? conns.filter((c) => active.has(c.key)) : [];
  const pickedEdge = focus?.kind === "edge" ? plan.edges.find((x) => x.id === focus.id) : undefined;
  const pickedLabel = focus === null ? "" : pickedEdge ? `${title(pickedEdge.a)} ↔ ${title(pickedEdge.b)}` : title(focus.id);
  return (
    <div id="app-plan">
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <div className="app-plan-bar">
        <span className="faint">
          Links die Systeme der Applikation, in der Mitte Firewalls und ACI auf den Wegen, rechts die Gegenstellen. An den Linien die
          Dienste (→ von der Applikation weg, ← zu ihr hin, ✕ vorher gesperrt), an den Enden die Interfaces. Klick auf einen Knoten
          bzw. eine Linie hebt seine Verbindungen hervor.
        </span>
        <span className="plan-export" id="app-plan-export">
          <span className="faint">Export{narrowed ? ` (${active.size} Verbindungen)` : ""}:</span>
          {(
            [
              ["drawio", "draw.io"],
              ["svg", "SVG"],
            ] as const
          ).map(([f, label]) => (
            <button key={f} className="btn btn-sm" disabled={busy !== null || (narrowed && active.size === 0)} onClick={() => void exportPlan(f)}>
              {busy === f ? "…" : label}
            </button>
          ))}
        </span>
      </div>
      <div className="app-plan-scroll">
        <svg id="app-plan-svg" width={Math.max(plan.width, 400)} height={plan.height} role="img" aria-label="Netzwerkplan der Applikation">
          {plan.columns.map((c) =>
            c.title ? (
              <text key={c.x} x={c.x} y={18} className="app-plan-col">
                {c.title}
              </text>
            ) : null,
          )}
          {plan.edges.map((e) => {
            const color = PLAN_COLOR[e.verdict];
            const pts = e.points.map(([x, y]) => `${x},${y}`).join(" ");
            const ports = [e.ports_a.length ? `${title(e.a)}: ${e.ports_a.join(", ")}` : "", e.ports_b.length ? `${title(e.b)}: ${e.ports_b.join(", ")}` : ""];
            return (
              <g
                key={e.id}
                className={`app-plan-edge clickable${lit(e.conns) ? "" : " dim"}${focus?.id === e.id ? " picked" : ""}`}
                data-edge={`${title(e.a)} → ${title(e.b)}`}
                data-verdict={e.verdict}
                onClick={() => pick("edge", e.id)}
              >
                <title>
                  {[`${title(e.a)} ↔ ${title(e.b)}: ${e.label} – ${PLAN_VERDICT[e.verdict]}`, ...ports.filter(Boolean), `${e.conns.length} Verbindungen`].join("\n")}
                </title>
                <polyline points={pts} fill="none" stroke="transparent" strokeWidth={12} />
                <polyline points={pts} fill="none" stroke={color} strokeWidth={focus?.id === e.id ? 3.2 : 1.8} strokeDasharray={e.dashed ? "5 4" : undefined} />
                <text x={e.lx} y={e.ly} textAnchor={e.anchor ?? "middle"} fill={color} className="app-plan-label">
                  {e.label}
                </text>
                {[e.port_a, e.port_b].map((p, i) =>
                  p ? (
                    <text key={i} x={p.x} y={p.y} textAnchor={p.anchor} className="app-plan-port">
                      {p.text}
                    </text>
                  ) : null,
                )}
              </g>
            );
          })}
          {plan.nodes.map((n) => {
            const stroke = n.role === "hop" && n.verdict && n.verdict !== "unknown" ? PLAN_COLOR[n.verdict] : PLAN_STROKE[n.role];
            const tip =
              n.role === "hop"
                ? `${n.title} – ${PLAN_VERDICT[n.verdict ?? "unknown"]}`
                : `${n.title}\n${[...n.ips, ...(n.role === "own" ? n.systems : n.name ? [n.name] : [])].join("\n")}`;
            return (
              <g
                key={n.id}
                className={`app-plan-node clickable${lit(n.conns) ? "" : " dim"}`}
                data-node={n.title}
                data-verdict={n.verdict ?? undefined}
                transform={`translate(${n.x},${n.y})`}
                onClick={() => pick("node", n.id)}
              >
                <title>{tip}</title>
                <rect
                  width={n.w}
                  height={n.h}
                  rx={6}
                  fill={PLAN_FILL[n.role]}
                  stroke={stroke}
                  strokeWidth={focus?.id === n.id ? 3 : n.role === "hop" ? 2 : 1.4}
                  strokeDasharray={n.kind === "external" ? "4 3" : undefined}
                />
                <text x={10} y={18} fontSize={12} fontWeight={600} fill="var(--fg)">
                  {short(n.title)}
                </text>
                <text x={10} y={34} fontSize={10} fill="var(--muted)">
                  {short(n.sub, 36)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="app-plan-legend">
        {(["allow", "deny", "depends", "unknown", "blocked"] as const).map((v) => (
          <span key={v}>
            <i style={{ borderTopColor: PLAN_COLOR[v] }} />
            {PLAN_VERDICT[v]}
          </span>
        ))}
        <span>
          <i style={{ borderTopColor: "var(--faint)", borderTopStyle: "dashed" }} />
          Weg nicht bekannt
        </span>
      </div>
      {focus && (
        <div className="app-plan-focus" id="app-plan-focus">
          <div>
            <b>{pickedLabel}</b>
            <span className="faint"> – {listed.length} Verbindungen</span>{" "}
            <button className="linkbtn" onClick={() => setFocus(null)}>
              Auswahl aufheben
            </button>
          </div>
          <ul>
            {listed.map((c) => (
              <li key={c.key}>
                <Verdict v={c.verdict} title={c.text} /> {sideText(c.src)} → {sideText(c.dst)} <span className="mono">{c.service ?? "alle"}</span>{" "}
                <button className="linkbtn" onClick={() => onOpen(c.key)}>
                  Details
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function ConnectionsCard({
  appId,
  reload,
  onRule,
  onGo,
  onData,
}: {
  appId: number;
  reload: number;
  onRule: (id: number) => void;
  onGo?: Go;
  onData?: (d: AppConnections | null) => void; // für Export der Doku (0.59.0)
}) {
  const [data, setData] = useState<AppConnections | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [q, setQ] = useState("");
  const [view, setView] = useState<"table" | "plan">(loadView);
  const pickView = (v: "table" | "plan") => {
    setView(v);
    storeView(v);
  };
  useEffect(() => {
    let current = true;
    setData(null);
    setError(null);
    onData?.(null);
    fetchAppConnections(appId)
      .then((d) => {
        if (!current) return;
        setData(d);
        onData?.(d);
      })
      .catch((e) => current && setError(e));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId, reload]);
  const needle = q.trim().toLowerCase();
  const rows = (data?.connections ?? []).filter((c) => {
    if (onlyIssues && !c.mismatch.length && c.verdict === "allow" && c.reached) return false;
    if (!needle) return true;
    const text = [c.src.label, c.src.ip, c.dst.label, c.dst.ip, c.service, ...c.hops.map((h) => h.label), ...c.rules.map((r) => r.label)]
      .join("\n")
      .toLowerCase();
    return text.includes(needle);
  });
  const s = data?.summary;
  return (
    <div className="card" id="app-connections" style={{ marginTop: 14 }}>
      <div className="card-head">
        <h2>Verbindungen Ende zu Ende</h2>
        {s && (
          <span className="chips">
            <span className="chip">{s.total} Verbindungen</span>
            {s.allow > 0 && <span className="chip ok">{s.allow} erlaubt</span>}
            {s.blocked > 0 && <span className="chip crit">{s.blocked} gesperrt</span>}
            {s.open > 0 && <span className="chip warn">{s.open} abhängig bzw. offen</span>}
            {s.mismatch > 0 && (
              <span className="chip crit" title="Regel erlaubt, der Weg sperrt bzw. ihr Gerät liegt nicht auf dem Weg">
                {s.mismatch} Abweichungen
              </span>
            )}
          </span>
        )}
        <div className="spacer" />
        <span className="seg no-print" id="app-conn-view">
          <button className={view === "table" ? "on" : ""} id="app-conn-table" onClick={() => pickView("table")}>
            Tabelle
          </button>
          <button className={view === "plan" ? "on" : ""} id="app-conn-plan" onClick={() => pickView("plan")}>
            Plan
          </button>
        </span>
        <button className={`toggle no-print ${onlyIssues ? "on" : ""}`} onClick={() => setOnlyIssues((v) => !v)}>
          nur Abweichungen und Gesperrte
        </button>
      </div>
      <div className="card-body pad no-print" style={{ paddingBottom: 0 }}>
        <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
          Je Verbindung (Quelle → Ziel, Dienst) eine Zeile mit dem Weg über Firewalls und ACI wie in der Weg-Suche; Klick zeigt je
          Hop die entscheidende Policy bzw. den Contract mit den Objekten und welche Regeln der Doku dort greifen. Gegenstelle any,
          vzAny und Preferred Group bleiben Regeln ohne Verbindung.
        </div>
        <div className="filterbar" style={{ marginBottom: 10 }}>
          <div className="search">
            <span className="ico">
              <Icon name="search" />
            </span>
            <input placeholder="System, Gegenstelle, Firewall, Regel…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
      </div>
      <ErrorBox error={error} prefix="Verbindungen laden" />
      {data?.notes.map((n) => (
        <div key={n} className="pad">
          <Note>{n}</Note>
        </div>
      ))}
      {view === "plan" && data && data.plan && data.plan.nodes.length > 0 ? (
        <div className="card-body pad">
          <PlanView
            appId={appId}
            plan={data.plan}
            conns={data.connections}
            shown={new Set(rows.map((c) => c.key))}
            filtered={rows.length !== data.connections.length}
            onOpen={(key) => {
              pickView("table");
              setOpen(key);
            }}
          />
        </div>
      ) : (
      <div className="card-body scroll viewport">
        {!data && !error ? (
          <Loading>Prüfe die Wege…</Loading>
        ) : rows.length === 0 ? (
          <Empty title={data?.connections.length ? "Keine Treffer" : "Keine Verbindungen"}>
            {data?.connections.length ? "Filter anpassen." : "Die Regeln der Applikation nennen keine prüfbare Gegenstelle."}
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Richtung</th>
                <th>Quelle</th>
                <th>Ziel</th>
                <th>Dienst</th>
                <th>Weg</th>
                <th>Urteil</th>
                <th title="Regeln der Doku, aus denen die Verbindung stammt">Regeln</th>
                <th title="Regel erlaubt, Weg sperrt bzw. Gerät nicht auf dem Weg"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <Fragment key={c.key}>
                  <tr className="clickable" onClick={() => setOpen(open === c.key ? null : c.key)}>
                    <td>
                      <span className={`chip ${c.direction === "in" ? "info" : "local"}`}>{c.direction === "in" ? "eingehend" : "ausgehend"}</span>
                    </td>
                    <td>
                      <SideLabel s={c.src} />
                    </td>
                    <td>
                      <SideLabel s={c.dst} />
                    </td>
                    <td className="mono">{c.service ?? "alle"}</td>
                    <td>
                      <HopChain hops={c.hops} />
                      {!c.reached && c.end && <div className="faint">{c.end.text}</div>}
                      {c.hops.length > 0 && <div className="print-only faint">{c.hops.map(hopText).join(" → ")}</div>}
                    </td>
                    <td>
                      <Verdict v={c.verdict} title={c.text} />
                    </td>
                    <td className="num">{c.rules.length}</td>
                    <td>
                      {c.mismatch.length > 0 && (
                        <span className="chip crit" title={c.mismatch.join("\n")}>
                          Abweichung
                        </span>
                      )}
                      {c.mismatch.length > 0 && <div className="print-only conn-why">{c.mismatch.join("; ")}</div>}
                    </td>
                  </tr>
                  {open === c.key && (
                    <tr className="detail-row">
                      <td colSpan={8}>
                        <ConnDetail c={c} onRule={onRule} onGo={onGo} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
      )}
    </div>
  );
}
