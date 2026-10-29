// „Warum dieser Weg?“ (0.109.0): derselbe Weg wie in der Weg-Suche bzw. bei den Verbindungen Ende zu Ende,
// Hop für Hop in Sätzen – Start, Eingang und Tabelle, Policy-Routen, alle Routen zum Ziel (gewählt bzw.
// verworfen mit Grund), Next-Hop, Ende mit Hinweis; „Erwartet über …“ zeigt, wo der Weg abbiegt. An jedem
// Schritt mit einem Problem lässt sich ein Finding von Hand erfassen (rein lesend, nur Automato).
import { useEffect, useMemo, useState } from "react";
import {
  explainPath,
  ExplainHop,
  ExplainQuery,
  ExplainRoute,
  ExplainStep,
  fetchRoutingSummary,
  PathExplanation,
  RoutingNode,
} from "./api";
import FindingDialog, { FindingDraft } from "./FindingDialog";
import { Drawer, ErrorBox, fmtTime, Loading, Note } from "./ui";
import { L } from "./i18n";

const MARK: Record<ExplainStep["status"], [string, string, string]> = {
  ok: ["okfg", "✓", L("passt", "fine")],
  info: ["faint", "·", L("Hinweis", "note")],
  warn: ["warnfg", "!", L("prüfen", "check")],
  stop: ["critfg", "✕", L("hier endet bzw. bricht der Weg", "the path ends or breaks here")],
};
const SEVERITY: Record<ExplainStep["status"], FindingDraft["severity"]> = {
  stop: "error",
  warn: "warning",
  info: "info",
  ok: "info",
};
const PATH_KIND: Record<string, string> = { active: L("aktiv", "active"), backup: "Backup" };

function RouteTable({ items }: { items: ExplainRoute[] }) {
  return (
    <table className="compact xroutes">
      <thead>
        <tr>
          <th>{L("Route", "Route")}</th>
          <th>{L("Protokoll", "Protocol")}</th>
          <th className="num" title={L("Distanz / Metrik", "Distance / metric")}>
            {L("Dist./Metr.", "Dist./metr.")}
          </th>
          <th>{L("Next-Hop", "Next-hop")}</th>
          <th>{L("Ergebnis", "Result")}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((r, i) => (
          <tr key={i} className={r.chosen ? "chosen" : ""}>
            <td className="mono">{r.prefix}</td>
            <td className="mono">
              {r.protocol ?? "–"}
              {r.table !== "rib" && <span className="faint"> · {r.table === "bgp" ? L("BGP-Tabelle", "BGP table") : L("statisch", "static")}</span>}
            </td>
            <td className="num mono">{r.distance == null && r.metric == null ? "–" : `${r.distance ?? "–"}/${r.metric ?? "–"}`}</td>
            <td className="mono">
              {r.next_hops.length
                ? r.next_hops.map((h, n) => (
                    <div key={n}>
                      {h.ip ?? L("direkt", "direct")}
                      {h.interface ? ` · ${h.interface}` : ""}
                      {h.owner && <span className="faint"> ({h.owner})</span>}
                    </div>
                  ))
                : "–"}
            </td>
            <td>
              {r.chosen ? <span className="okfg">✓ </span> : <span className="faint">✕ </span>}
              {r.why}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Step({ s, onCapture }: { s: ExplainStep; onCapture?: () => void }) {
  const [cls, mark, title] = MARK[s.status] ?? MARK.info;
  return (
    <div className={`xstep ${s.status}`} data-kind={s.kind}>
      <span className={`xmark ${cls}`} title={title}>
        {mark}
      </span>
      <div className="xtext">
        <div>{s.text}</div>
        {s.hint && <div className="xhint">→ {s.hint}</div>}
        {s.items && s.items.length > 0 && <RouteTable items={s.items} />}
        {onCapture && (s.status === "warn" || s.status === "stop") && (
          <button className="linkbtn capture" onClick={onCapture}>
            + {L("Als Finding erfassen", "Capture as finding")}
          </button>
        )}
      </div>
    </div>
  );
}

function hopClass(h: ExplainHop): string {
  if (h.steps.some((s) => s.status === "stop")) return "bad";
  if (h.steps.some((s) => s.status === "warn")) return "warn";
  return "";
}

export default function PathExplainDrawer({
  tenantId,
  query,
  pathIndex = 0,
  nodes: given,
  canCapture = false,
  onClose,
}: {
  tenantId: number;
  query: ExplainQuery; // dieselben Eingaben wie die Weg-Suche bzw. die Verbindung
  pathIndex?: number; // gezeigter Weg (Reihenfolge wie im Ergebnis der Weg-Suche)
  nodes?: RoutingNode[]; // Geräte für „Erwartet über …“ (sonst geladen)
  canCapture?: boolean; // Operator: Finding von Hand erfassen
  onClose: () => void;
}) {
  const [data, setData] = useState<PathExplanation | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [idx, setIdx] = useState(pathIndex);
  const [expect, setExpect] = useState<number | null>(null);
  const [nodes, setNodes] = useState<RoutingNode[]>(given ?? []);
  const [draft, setDraft] = useState<FindingDraft | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const key = JSON.stringify(query);
  useEffect(() => {
    if (given) return;
    fetchRoutingSummary(tenantId)
      .then((s) => setNodes(s.nodes))
      .catch(() => setNodes([]));
  }, [tenantId, given]);
  useEffect(() => {
    setData(null);
    setError(null);
    explainPath(tenantId, query, expect)
      .then(setData)
      .catch(setError);
  }, [tenantId, key, expect]); // eslint-disable-line react-hooks/exhaustive-deps
  // Geräte (Quellsysteme) mit gelesenem Routing, je eines
  const devices = useMemo(() => {
    const seen = new Map<number, string>();
    for (const n of nodes) if (!seen.has(n.source_system_id)) seen.set(n.source_system_id, n.source);
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [nodes]);
  const path = data?.paths[Math.min(idx, Math.max(0, (data?.paths.length ?? 1) - 1))];
  const way = { src: query.src || undefined, dst: query.dst || undefined, service: query.service || undefined };
  const capture = (s: ExplainStep, hop?: ExplainHop | null) =>
    canCapture
      ? () =>
          setDraft({
            title: s.text,
            text: s.hint ?? "",
            severity: SEVERITY[s.status],
            hop: hop?.label,
            source_system_id: hop?.source_system_id ?? null,
            ...way,
          })
      : undefined;
  const last = path?.hops[path.hops.length - 1] ?? null;
  return (
    <Drawer
      wide
      title={L("Warum dieser Weg?", "Why this path?")}
      sub={`${query.src || query.start || "?"} → ${query.dst || "?"}${query.service ? ` · ${query.service}` : ""}`}
      onClose={draft ? () => setDraft(null) : onClose} // Escape schliesst zuerst den Dialog
    >
      <div className="xplain-tools">
        {data && data.paths.length > 1 && (
          <div className="seg" id="xplain-paths">
            {data.paths.map((p, i) => (
              <button key={i} className={i === idx ? "on" : ""} onClick={() => setIdx(i)}>
                {L("Weg", "Path")} {i + 1} · {PATH_KIND[p.kind] ?? p.kind}
              </button>
            ))}
          </div>
        )}
        <select
          className="sel"
          id="xplain-expect"
          value={expect ?? ""}
          onChange={(e) => setExpect(e.target.value ? Number(e.target.value) : null)}
          title={L(
            "Gerät, über das der Verkehr gehen müsste: zeigt, wo der Weg abbiegt und welche Route fehlt bzw. gewinnt",
            "Device the traffic should pass: shows where the path turns away and which route is missing or wins",
          )}
        >
          <option value="">{L("Erwartet über …", "Expected via …")}</option>
          {devices.map(([id, name]) => (
            <option key={id} value={id}>
              {L("Erwartet über", "Expected via")} {name}
            </option>
          ))}
        </select>
        {canCapture && (
          <button
            className="btn btn-sm"
            id="xplain-capture"
            style={{ marginLeft: "auto" }}
            onClick={() => setDraft({ title: "", text: "", severity: "warning", ...way })}
          >
            + {L("Finding erfassen", "Capture finding")}
          </button>
        )}
      </div>
      {saved && (
        <Note>
          {saved}{" "}
          <a href="#findings?type=manual" onClick={onClose}>
            {L("Zu den Findings", "Go to findings")}
          </a>
        </Note>
      )}
      <ErrorBox error={error} prefix={L("Weg erklären", "Explain path")} onClose={() => setError(null)} />
      {!data && !error && <Loading>{L("Erkläre den Weg…", "Explaining the path…")}</Loading>}
      {data && (
        <>
          {data.expect && (
            <div className="xexpect" id="xplain-expect-result">
              <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>
                {L("Erwartet über", "Expected via")} {data.expect.device}
                {data.expect.on_path ? (
                  <span className="chip ok" style={{ marginLeft: 8 }}>
                    {L("auf dem Weg", "on the path")}
                  </span>
                ) : (
                  <span className="chip warn" style={{ marginLeft: 8 }}>
                    {L("nicht auf dem Weg", "not on the path")}
                  </span>
                )}
              </h3>
              {data.expect.steps.map((s, i) => (
                <Step
                  key={i}
                  s={s}
                  onCapture={capture(s, { label: `${L("Erwartet über", "Expected via")} ${data.expect!.device}`, key: "", source_system_id: data.expect!.source_system_id, steps: [] })}
                />
              ))}
            </div>
          )}
          <div className="xplain-sec" id="xplain-start">
            <h3>Start</h3>
            {data.start.map((s, i) => (
              <Step key={i} s={s} onCapture={capture(s, path?.hops[0])} />
            ))}
          </div>
          {!path ? (
            <Note warn>
              {L(
                "Kein Weg gefunden – siehe Start. Ohne Hop gibt es nichts zu erklären.",
                "No path found – see start. Without a hop there is nothing to explain.",
              )}
            </Note>
          ) : (
            <>
              {path.hops.map((h, i) => (
                <div key={i} className={`xplain-hop ${hopClass(h)}`} data-hop={i + 1}>
                  <div className="xplain-sec" style={{ margin: 0 }}>
                    <h3>
                      <span>
                        {i + 1}. {h.label}
                      </span>
                      {h.read_at !== undefined && (
                        <span
                          className={`chip ${h.stale ? "warn" : "na"}`}
                          title={L(
                            "Stand des Routings dieses Geräts (letzter erfolgreicher Lauf)",
                            "State of this device's routing (last successful run)",
                          )}
                        >
                          {L("Routing gelesen", "Routing read")} {fmtTime(h.read_at ?? null)}
                          {h.stale ? L(" – alt", " – old") : ""}
                        </span>
                      )}
                    </h3>
                    {h.steps.map((s, n) => (
                      <Step key={n} s={s} onCapture={capture(s, h)} />
                    ))}
                  </div>
                </div>
              ))}
              <div className="xplain-sec" id="xplain-end">
                <h3>{L("Ende", "End")}</h3>
                <Step s={path.end} onCapture={capture(path.end, last)} />
              </div>
            </>
          )}
        </>
      )}
      {draft && (
        <FindingDialog
          tenantId={tenantId}
          draft={draft}
          onClose={() => setDraft(null)}
          onSaved={(f) => {
            setDraft(null);
            setSaved(L(`Finding #${f.id} erfasst: ${f.message ?? f.subject_id}.`, `Finding #${f.id} captured: ${f.message ?? f.subject_id}.`));
          }}
        />
      )}
    </Drawer>
  );
}
