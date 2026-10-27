// Verbindungen Ende zu Ende (0.45.0, Anwender): eine Zeile je Verbindung der Applikation, auch
// wenn mehrere Firewalls und das ACI hintereinander stehen – im Detail je Hop die Policy bzw.
// der Contract, der entscheidet, mit den Objekten; dazu der Abgleich mit den Regeln der Doku
// (Regel erlaubt, Weg sperrt bzw. Gerät nicht auf dem Weg). Seit 0.67.0 auch als Netzwerkplan:
// Systeme der Applikation → Knoten der Wege → Gegenstellen, je Linie Dienste und Interfaces;
// seit 0.74.0 kompakt (EPGs mit VMs im Rahmen des APs, L3Outs, Regeln als Ebene, Zoom).
import { Fragment, useEffect, useRef, useState } from "react";
import type { View, ViewParams } from "./App";
import {
  AppConnection,
  AppConnections,
  AppConnHop,
  AppConnSide,
  AppConnState,
  AppConnZones,
  AppPlan,
  AppPlanVerdict,
  exportAppPlan,
  fetchAppConnections,
  snatText,
} from "./api";
import { Fold, FoldTitle } from "./fold";
import { Verdict, VERDICT } from "./Paths";
import { Dash, download, Empty, ErrorBox, Icon, Loading, Note } from "./ui";
import { L } from "./i18n";

type Go = (v: View, p?: ViewParams) => void;

const STATE: Record<AppConnState["state"], [string, string]> = {
  decides: ["ok", L("entscheidet", "decides")],
  possible: ["warn", L("möglich", "possible")],
  other: ["", L("greift nicht", "does not apply")],
  off_path: ["crit", L("nicht auf dem Weg", "not on the path")],
  unknown: ["na", L("nicht prüfbar", "not checkable")],
};

// Filter nach Art (0.80.0, Anwender): Urteil des Wegs, Aktion der Regeln, Regel auf dem Weg,
// Richtung, Hinweise – in einer Gruppe „oder“, zwischen den Gruppen „und“, leere Gruppe = alle
type Facet = { key: string; label: string; title: string; test: (c: AppConnection) => boolean };
const state = (s: AppConnState["state"]) => (c: AppConnection) => c.rules.some((r) => r.state === s);
const FACETS: { key: string; label: string; items: Facet[] }[] = [
  {
    key: "verdict",
    label: L("Urteil", "Verdict"),
    items: [
      {
        key: "allow",
        label: L("erlaubt", "allowed"),
        title: L("der Weg erlaubt die Verbindung bis zum Ziel", "the path allows the connection up to the destination"),
        test: (c) => c.verdict === "allow" && c.reached,
      },
      {
        key: "deny",
        label: L("gesperrt", "blocked"),
        title: L("eine Policy bzw. ein Contract auf dem Weg sperrt", "a policy or contract on the path blocks"),
        test: (c) => c.verdict === "deny",
      },
      {
        key: "depends",
        label: L("abhängig", "depends"),
        title: L("je nach Weg bzw. Policy, die nicht sicher prüfbar ist", "depending on the path or a policy that cannot be checked reliably"),
        test: (c) => c.verdict === "depends",
      },
      {
        key: "open",
        label: L("offen", "open"),
        title: L("nicht prüfbar bzw. der Weg endet vor dem Ziel", "not checkable or the path ends before the destination"),
        test: (c) => c.verdict === "unknown" || (c.verdict === "allow" && !c.reached),
      },
    ],
  },
  {
    key: "action",
    label: L("Regeln", "Rules"),
    items: [
      {
        key: "allow",
        label: L("erlaubende", "allowing"),
        title: L("aus einer erlaubenden Regel bzw. einem Contract der Doku", "from an allowing rule or contract of the documentation"),
        test: (c) => c.rules.some((r) => r.action === "allow"),
      },
      {
        key: "deny",
        label: L("sperrende", "denying"),
        title: L("aus einer sperrenden Regel der Doku", "from a denying rule of the documentation"),
        test: (c) => c.rules.some((r) => r.action === "deny"),
      },
    ],
  },
  {
    key: "state",
    label: L("Regel auf dem Weg", "Rule on the path"),
    items: (Object.keys(STATE) as AppConnState["state"][]).map((s) => ({
      key: s,
      label: STATE[s][1],
      title: L("Abgleich einer Regel der Doku mit dem Weg", "Comparison of a documentation rule with the path"),
      test: state(s),
    })),
  },
  {
    key: "direction",
    label: L("Richtung", "Direction"),
    items: [
      {
        key: "in",
        label: L("eingehend", "inbound"),
        title: L("zur Applikation", "to the application"),
        test: (c) => c.direction === "in",
      },
      {
        key: "out",
        label: L("ausgehend", "outbound"),
        title: L("von der Applikation", "from the application"),
        test: (c) => c.direction === "out",
      },
    ],
  },
  {
    key: "flag",
    label: L("Hinweise", "Notes"),
    items: [
      {
        key: "mismatch",
        label: L("Abweichungen", "Deviations"),
        title: L(
          "Regel erlaubt, der Weg sperrt bzw. ihr Gerät liegt nicht auf dem Weg",
          "Rule allows, the path blocks or its device is not on the path",
        ),
        test: (c) => c.mismatch.length > 0,
      },
      {
        key: "inconsistent",
        label: L("Wege uneinheitlich", "Paths inconsistent"),
        title: L("je nach Weg erlaubt bzw. gesperrt", "allowed or blocked depending on the path"),
        test: (c) => c.inconsistent,
      },
      {
        key: "zones",
        label: L("gegen die Zonen-Absicht", "against the zone intent"),
        title: L("der Weg erlaubt, was die Absicht zwischen den Zonen sperrt", "the path allows what the intent between the zones denies"),
        test: (c) => !!c.zones?.violation,
      },
    ],
  },
];
type Picked = Record<string, string[]>;

function inGroup(c: AppConnection, g: (typeof FACETS)[number], picked: Picked): boolean {
  const sel = picked[g.key];
  return !sel?.length || g.items.some((f) => sel.includes(f.key) && f.test(c));
}

function Facets({ conns, base, picked, onPick }: { conns: AppConnection[]; base: AppConnection[]; picked: Picked; onPick: (p: Picked) => void }) {
  const any = Object.values(picked).some((v) => v.length);
  return (
    <div className="conn-facets" id="app-conn-filter">
      {FACETS.map((g) => {
        const sel = picked[g.key] ?? [];
        // nur Arten, die es in der Applikation gibt; Zahl = Treffer mit den übrigen Filtern
        const items = g.items.filter((f) => sel.includes(f.key) || conns.some(f.test));
        if (!items.length) return null;
        const others = base.filter((c) => FACETS.every((o) => o === g || inGroup(c, o, picked)));
        return (
          <span key={g.key} className="facet-group">
            <span className="facet-label">{g.label}</span>
            <span className="seg">
              {items.map((f) => (
                <button
                  key={f.key}
                  id={`conn-filter-${g.key}-${f.key}`}
                  className={sel.includes(f.key) ? "on" : ""}
                  title={f.title}
                  onClick={() => onPick({ ...picked, [g.key]: sel.includes(f.key) ? sel.filter((k) => k !== f.key) : [...sel, f.key] })}
                >
                  {f.label} <span className="facet-count">{others.filter(f.test).length}</span>
                </button>
              ))}
            </span>
          </span>
        );
      })}
      {any && (
        <button className="linkbtn" id="conn-filter-reset" onClick={() => onPick({})}>
          {L("Filter zurücksetzen", "Reset filter")}
        </button>
      )}
    </div>
  );
}

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
        <div className="faint" title={L("VMs der EPG (Portgruppe) – geprüft mit der ersten", "VMs of the EPG (port group) – checked with the first")}>
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
    c.rules.filter((r) => r.decides).map((r) => (h.checks.length > 1 ? `${c.label}: ` : "") + (r.label || r.name || r.external_id || L("Regel", "Rule"))),
  );
  const text = deciding.length ? `${h.label} (${[...new Set(deciding)].join(", ")})` : h.label;
  return h.lb ? `${text} → LB ${h.lb.name}` : text;
}

const sideText = (s: AppConnSide) => {
  const more = s.kind === "system" && s.systems.length + s.more > 1 ? ` +${s.systems.length + s.more - 1}` : "";
  return `${s.label}${more} (${s.ip}${s.name ? ` · ${s.name}` : ""})`;
};

const mdCell = (v: string) => v.replace(/\|/g, "\\|").replace(/\n/g, " ");

// Zonen einer Verbindung als Text (0.78.0): „Web → LAN · Absicht gesperrt: gegen die Absicht …“
export function zonesText(z: AppConnZones | null | undefined): string {
  if (!z) return "";
  const names = z.chain.length ? z.chain.map((e) => e.name) : [...z.src, ...z.dst];
  const way = [...new Set(names)].join(" → ");
  const intents = z.intents.map((i) => i.text).join("; ");
  return [way, intents].filter(Boolean).join(" · ");
}

// Verbindungen Ende zu Ende als Markdown (Export der Applikations-Doku, 0.59.0)
export function connectionsMarkdown(data: AppConnections): string[] {
  const s = data.summary;
  const lines = [L("## Verbindungen Ende zu Ende", "## End-to-end connections"), ""];
  lines.push(
    L(
      `${s.total} Verbindungen · ${s.allow} erlaubt · ${s.blocked} gesperrt · ${s.open} abhängig bzw. offen · ${s.mismatch} Abweichungen · ${s.inconsistent ?? 0} Wege uneinheitlich`,
      `${s.total} connections · ${s.allow} allowed · ${s.blocked} blocked · ${s.open} depends or open · ${s.mismatch} deviations · ${s.inconsistent ?? 0} paths inconsistent`,
    ),
    "",
  );
  data.notes.forEach((n) => lines.push(`> ${n}`, ""));
  if (!data.connections.length) return [...lines, L("Keine prüfbaren Verbindungen.", "No checkable connections."), ""];
  lines.push(
    L(
      "| Richtung | Quelle | Ziel | Dienst | Weg (entscheidende Regel) | Urteil | Zonen | Abweichung |",
      "| Direction | Source | Destination | Service | Path (deciding rule) | Verdict | Zones | Deviation |",
    ),
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
  );
  for (const c of data.connections) {
    const way = c.hops.map(hopText).join(" → ") + (!c.reached && c.end ? ` – ${c.end.text}` : "");
    const verdict = c.text || (VERDICT[c.verdict]?.[1] ?? c.verdict);
    lines.push(
      `| ${c.direction === "in" ? L("eingehend", "inbound") : L("ausgehend", "outbound")} | ${mdCell(sideText(c.src))} | ${mdCell(sideText(c.dst))} | ${c.service ?? L("alle", "all")} | ${mdCell(way || "–")} | ${mdCell(verdict)} | ${mdCell(zonesText(c.zones) || "–")} | ${mdCell([...(c.inconsistent ? [variantsText(c)] : []), ...c.mismatch].join("; "))} |`,
    );
  }
  lines.push("");
  return lines;
}

// Wege uneinheitlich (0.73.0): je Weg (aktiv, ECMP, Backup) bzw. Firewall im Verbund das Urteil
const VARIANT_KIND = { active: L("aktiv", "active"), backup: "Backup" };
function variantsText(c: AppConnection): string {
  const parts = c.variants.map(
    (v) => `${VARIANT_KIND[v.kind]} ${L("über", "via")} ${v.via}: ${VERDICT[v.verdict]?.[1] ?? v.verdict}`,
  );
  return L(`Wege uneinheitlich (${parts.join(", ")})`, `Paths inconsistent (${parts.join(", ")})`);
}

function Variants({ c }: { c: AppConnection }) {
  if (c.variants.length < 2) return null;
  return (
    <div className="conn-variants" id="conn-variants">
      <b>{L("Wege", "Paths")}</b>{" "}
      <span className="faint">
        {c.inconsistent
          ? L(
              "– je nach Weg offen bzw. gesperrt: Regeln auf den Wegen angleichen",
              "– open or blocked depending on the path: align the rules on the paths",
            )
          : L("– alle Wege mit gleichem Urteil", "– all paths with the same verdict")}
      </span>
      <table className="compact">
        <tbody>
          {c.variants.map((v, i) => (
            <tr key={i}>
              <td>
                <span className={`chip ${v.kind === "backup" ? "na" : "info"}`}>{VARIANT_KIND[v.kind]}</span>
              </td>
              <td className="mono">{v.via}</td>
              <td>
                <Verdict v={v.verdict} title={v.text} />
              </td>
              <td className="faint">{[v.text, v.reason].filter(Boolean).join(" · ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Weg als Folge der Knoten, je Knoten nach seinem Urteil eingefärbt
const INTENT_CHIP: Record<string, string> = { ok: "ok", violation: "crit", warn: "warn", info: "na" };

// Zonen der Verbindung unter dem Weg (0.78.0): Kette mit Farben, Absicht mit Urteil
function ZoneLine({ z }: { z: AppConnZones }) {
  const names = z.chain.length ? z.chain : [...z.src, ...z.dst].map((name) => ({ name, color: null }));
  return (
    <div className="faint conn-zones" style={{ marginTop: 2 }}>
      {L("Zonen:", "Zones:")}{" "}
      {names.map((e, i) => (
        <span key={i}>
          {i > 0 && " → "}
          <span style={e.color ? { borderBottom: `2px solid ${e.color}` } : undefined}>{e.name}</span>
        </span>
      ))}
      {z.intents.map((it, i) => (
        <span key={`i${i}`} className={`chip ${INTENT_CHIP[it.verdict] ?? "na"}`} style={{ marginLeft: 6 }} title={it.text}>
          {L("Absicht", "Intent")}{" "}
          {it.intent === "deny"
            ? L("gesperrt", "deny")
            : it.intent === "limited"
              ? L("eingeschränkt", "limited")
              : L("erlaubt", "allow")}
        </span>
      ))}
    </div>
  );
}

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
        {c.end ? L(` · Ende: ${c.end.text}`, ` · End: ${c.end.text}`) : ""}
        {c.start
          ? L(
              ` · Start an ${c.start} (Quelle an keinem gelesenen Gerät)`,
              ` · Start at ${c.start} (source at no collected device)`,
            )
          : ""}
        {c.paths > 1
          ? L(` · ${c.paths} aktive Wege (ECMP), gezeigt der erste`, ` · ${c.paths} active paths (ECMP), showing the first`)
          : ""}
        {c.parallel?.length
          ? L(
              ` · redundant über Verbund ${c.parallel.join(", ")}`,
              ` · redundant via parallel group ${c.parallel.join(", ")}`,
            )
          : ""}
      </div>
      <Variants c={c} />
      <table className="compact">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>{L("Knoten", "Node")}</th>
            <th>{L("Urteil", "Verdict")}</th>
            <th
              title={L(
                "die entscheidende Policy bzw. der Contract, davor die möglichen (blass)",
                "the deciding policy or contract, before it the possible ones (faded)",
              )}
            >
              Policy / Contract
            </th>
            <th
              title={L(
                "Objekt der Policy bzw. EPG, über die die Quelle passt",
                "Object of the policy or EPG through which the source matches",
              )}
            >
              {L("Quelle", "Source")}
            </th>
            <th
              title={L(
                "Objekt der Policy bzw. EPG, über die das Ziel passt",
                "Object of the policy or EPG through which the destination matches",
              )}
            >
              {L("Ziel", "Destination")}
            </th>
            <th>{L("Dienste", "Services")}</th>
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
                            <button className="linkbtn" onClick={() => onRule(r.rule_id!)} title={L("Regel öffnen", "Open rule")}>
                              {r.label ?? `Policy ${r.external_id}`}
                            </button>
                          ) : (
                            r.label
                          )}
                          {!r.label && r.name ? L(` „${r.name}“`, ` “${r.name}”`) : ""} ·{" "}
                          {r.action === "allow" ? L("erlauben", "accept") : L("sperren", "deny")}
                          {r.unsure.length ? L(` · möglich (${r.unsure.join(", ")})`, ` · possible (${r.unsure.join(", ")})`) : ""}
                          {r.note ? L(` · greift nicht: ${r.note}`, ` · does not apply: ${r.note}`) : ""}
                        </div>
                      ))
                    )}
                    {k && (k.src_epg || k.dst_epg) && (
                      <div className="faint" title={L("Zuordnung im ACI", "Assignment in the ACI")}>
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
        <b>{L("Regeln der Doku", "Rules of the documentation")}</b>{" "}
        <span className="faint">{L("– aus ihnen stammt die Verbindung", "– the connection comes from them")}</span>
        <ul className="conn-rules">
          {c.rules.map((r) => (
            <li key={r.rule_id}>
              <span className={`chip ${STATE[r.state][0]}`}>{STATE[r.state][1]}</span>{" "}
              <button className="linkbtn" onClick={() => onRule(r.rule_id)}>
                {r.label}
              </button>{" "}
              <span className="faint">
                {r.device} · {r.action === "allow" ? L("erlauben", "accept") : L("sperren", "deny")} · {r.text}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="no-print" style={{ marginTop: 6 }}>
        {onGo ? (
          <button className="btn btn-ghost btn-sm" onClick={() => onGo("wege", params)}>
            <Icon name="route" size={14} /> {L("In der Weg-Suche öffnen", "Open in path search")}
          </button>
        ) : (
          <a href={`#wege?${new URLSearchParams(params).toString()}`}>
            {L("In der Weg-Suche öffnen", "Open in path search")}
          </a>
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
  allow: L("erlaubt", "allowed"),
  deny: L("gesperrt", "blocked"),
  depends: L("abhängig", "depends"),
  unknown: L("unbekannt", "unknown"),
  blocked: L("vorher gesperrt", "blocked earlier"),
};
const PLAN_FILL = { own: "var(--info-weak)", hop: "var(--surface-2)", peer: "var(--surface)" };
const PLAN_STROKE = { own: "var(--info)", hop: "var(--accent)", peer: "var(--border-strong)" };
const short = (t: string, n = 30) => (t.length <= n ? t : `${t.slice(0, n - 1)}…`);
const VIEW_KEY = "automato_app_conn_view";
const LAYER_KEY = "automato_app_plan_layers"; // Ebenen Regeln/Dienste (0.74.0)
const ZOOM = { min: 0.3, max: 2.5, step: 1.2 };
const NODE_H = 44;
const LINE_H = 14;

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

type Layers = { rules: boolean; services: boolean };
function loadLayers(): Layers {
  try {
    return { rules: true, services: true, ...(JSON.parse(localStorage.getItem(LAYER_KEY) ?? "{}") as Partial<Layers>) };
  } catch {
    return { rules: true, services: true };
  }
}

type Focus = { kind: "node" | "edge"; id: string } | null;

// Zoom und Verschieben (0.74.0): Knöpfe, Strg/⌘ + Mausrad, Ziehen auf freier Fläche
function usePanZoom(width: number) {
  const box = useRef<HTMLDivElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const clamp = (z: number) => Math.min(ZOOM.max, Math.max(ZOOM.min, z));
  const fit = () => {
    const el = box.current;
    if (el && width > 0) setZoom(clamp(Math.min(1, (el.clientWidth - 8) / width)));
  };
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return; // ohne Taste: normal scrollen
      e.preventDefault();
      setZoom((z) => clamp(e.deltaY < 0 ? z * 1.1 : z / 1.1));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);
  const handlers = {
    onPointerDown: (e: React.PointerEvent) => {
      const el = box.current;
      // nur auf freier Fläche: Knoten und Linien bleiben klickbar
      if (!el || e.button !== 0 || (e.target as Element).closest(".app-plan-node, .app-plan-edge")) return;
      drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
      el.setPointerCapture?.(e.pointerId);
      el.classList.add("panning");
    },
    onPointerMove: (e: React.PointerEvent) => {
      const el = box.current;
      const d = drag.current;
      if (!el || !d) return;
      el.scrollLeft = d.left - (e.clientX - d.x);
      el.scrollTop = d.top - (e.clientY - d.y);
    },
    onPointerUp: () => {
      drag.current = null;
      box.current?.classList.remove("panning");
    },
  };
  return {
    box,
    zoom,
    handlers,
    zoomIn: () => setZoom((z) => clamp(z * ZOOM.step)),
    zoomOut: () => setZoom((z) => clamp(z / ZOOM.step)),
    reset: () => setZoom(1),
    fit,
  };
}

// Plan der Verbindungen: Filter der Karte bzw. Klick auf Knoten/Linie hebt hervor, der Rest wird
// blass; der Export nimmt die hervorgehobenen Verbindungen. Seit 0.74.0 kompakt: EPGs mit ihren
// VMs im Rahmen ihres APs, das ACI als L3Out, je Knoten des Wegs die Regeln als eigene Ebene.
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
  const [layers, setLayers] = useState<Layers>(loadLayers);
  const width = Math.max(plan.width, 400);
  const pz = usePanZoom(width);
  const toggleLayer = (k: keyof Layers) =>
    setLayers((prev) => {
      const next = { ...prev, [k]: !prev[k] };
      try {
        localStorage.setItem(LAYER_KEY, JSON.stringify(next));
      } catch {
        // ohne Speicher: nur für diese Sitzung
      }
      return next;
    });
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
      const { blob, filename } = await exportAppPlan(appId, format, narrowed ? [...active] : undefined, layers.rules);
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
          {L(
            "Links die EPGs der Applikation mit ihren VMs im Rahmen ihres Application Profiles, in der Mitte die L3Outs, " +
              "Firewalls und VDOMs auf den Wegen, rechts die Gegenstellen. Unter jedem Knoten des Wegs die entscheidenden " +
              "Regeln mit ihren Diensten (Ebene „Regeln“), an den Linien die Dienste (→ von der Applikation weg, ← zu ihr hin, " +
              "✕ vorher gesperrt). Klick hebt hervor; ziehen verschiebt, Strg + Mausrad zoomt.",
            "On the left the EPGs of the application with their VMs in the frame of their application profile, in the middle " +
              "the L3Outs, firewalls and VDOMs on the paths, on the right the peers. Below each node of the path the deciding " +
              "rules with their services (layer “Rules”), on the lines the services (→ away from the application, ← towards " +
              "it, ✕ blocked earlier). Click highlights; drag pans, Ctrl + mouse wheel zooms.",
          )}
        </span>
        <span className="plan-export" id="app-plan-export">
          <span className="faint">
            Export{narrowed ? L(` (${active.size} Verbindungen)`, ` (${active.size} connections)`) : ""}:
          </span>
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
      <div className="app-plan-tools">
        <span className="seg" id="app-plan-zoom">
          <button id="app-plan-zoom-out" title={L("verkleinern", "zoom out")} onClick={pz.zoomOut}>
            −
          </button>
          <button id="app-plan-zoom-reset" title="100 %" onClick={pz.reset}>
            {Math.round(pz.zoom * 100)} %
          </button>
          <button id="app-plan-zoom-in" title={L("vergrössern", "zoom in")} onClick={pz.zoomIn}>
            +
          </button>
          <button id="app-plan-zoom-fit" title={L("in die Breite einpassen", "fit to width")} onClick={pz.fit}>
            {L("einpassen", "fit")}
          </button>
        </span>
        <span className="faint">{L("Ebenen:", "Layers:")}</span>
        <label className="check">
          <input type="checkbox" id="app-plan-rules" checked={layers.rules} onChange={() => toggleLayer("rules")} />{" "}
          {L("Regeln", "Rules")}
        </label>
        <label className="check">
          <input type="checkbox" id="app-plan-services" checked={layers.services} onChange={() => toggleLayer("services")} />{" "}
          {L("Dienste und Interfaces", "Services and interfaces")}
        </label>
      </div>
      <div className="app-plan-scroll" ref={pz.box} {...pz.handlers}>
        <svg
          id="app-plan-svg"
          width={width * pz.zoom}
          height={plan.height * pz.zoom}
          viewBox={`0 0 ${width} ${plan.height}`}
          role="img"
          aria-label={L("Netzwerkplan der Applikation", "Network diagram of the application")}
        >
          {plan.columns.map((c) =>
            c.title ? (
              <text key={c.x} x={c.x} y={18} className="app-plan-col">
                {c.title}
              </text>
            ) : null,
          )}
          {(plan.groups ?? []).map((g) => (
            <g key={g.title} className="app-plan-group" data-group={g.title}>
              <rect x={g.x} y={g.y} width={g.w} height={g.h} rx={8} fill="var(--surface-2)" stroke="var(--border-strong)" strokeDasharray="5 3" />
              <text x={g.x + 8} y={g.y + 14} className="app-plan-group-title">
                {short(g.title, 32)}
              </text>
            </g>
          ))}
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
                  {[`${title(e.a)} ↔ ${title(e.b)}: ${e.label} – ${PLAN_VERDICT[e.verdict]}`, ...ports.filter(Boolean), L(`${e.conns.length} Verbindungen`, `${e.conns.length} connections`)].join("\n")}
                </title>
                <polyline points={pts} fill="none" stroke="transparent" strokeWidth={12} />
                <polyline points={pts} fill="none" stroke={color} strokeWidth={focus?.id === e.id ? 3.2 : 1.8} strokeDasharray={e.dashed ? "5 4" : undefined} />
                {layers.services && (
                  <>
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
                  </>
                )}
              </g>
            );
          })}
          {plan.nodes.map((n) => {
            const stroke = n.role === "hop" && n.verdict && n.verdict !== "unknown" ? PLAN_COLOR[n.verdict] : PLAN_STROKE[n.role];
            const vms = [...(n.lines ?? []).map((v) => `• ${v}`), ...(n.more ? [L(`+${n.more} weitere`, `+${n.more} more`)] : [])];
            const tip =
              n.role === "hop"
                ? `${n.label}${n.via ? ` (${n.via})` : ""} – ${PLAN_VERDICT[n.verdict ?? "unknown"]}`
                : `${n.label}\n${[...n.ips, ...(n.role === "own" ? n.systems : n.name ? [n.name] : [])].join("\n")}`;
            return (
              <g
                key={n.id}
                className={`app-plan-node clickable${lit(n.conns) ? "" : " dim"}`}
                data-node={n.title}
                data-kind={n.kind}
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
                {vms.map((v, i) => (
                  <text key={i} x={14} y={NODE_H + 8 + i * LINE_H} fontSize={10.5} fill="var(--fg)" className="app-plan-vm">
                    {short(v, 32)}
                  </text>
                ))}
              </g>
            );
          })}
          {layers.rules && (
            <g className="app-plan-rules" id="app-plan-rules-layer">
              {plan.nodes
                .filter((n) => n.rules_box)
                .map((n) => {
                  const b = n.rules_box!;
                  return (
                    <g key={n.id} className={`app-plan-rulebox${lit(n.conns) ? "" : " dim"}`} data-node={n.title}>
                      <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={3} fill="var(--warn-weak)" stroke="var(--warn)" strokeWidth={0.8} />
                      {(n.rule_lines ?? []).map((line, i) => (
                        <text key={i} x={b.x + 6} y={b.y + 15 + i * LINE_H} className="app-plan-rule">
                          <title>{line}</title>
                          {short(line, 34)}
                        </text>
                      ))}
                    </g>
                  );
                })}
            </g>
          )}
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
          {L("Weg nicht bekannt", "Path not known")}
        </span>
      </div>
      {focus && (
        <div className="app-plan-focus" id="app-plan-focus">
          <div>
            <b>{pickedLabel}</b>
            <span className="faint">{L(` – ${listed.length} Verbindungen`, ` – ${listed.length} connections`)}</span>{" "}
            <button className="linkbtn" onClick={() => setFocus(null)}>
              {L("Auswahl aufheben", "Clear selection")}
            </button>
          </div>
          <ul>
            {listed.map((c) => (
              <li key={c.key}>
                <Verdict v={c.verdict} title={c.text} /> {sideText(c.src)} → {sideText(c.dst)} <span className="mono">{c.service ?? L("alle", "all")}</span>{" "}
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
  fold,
}: {
  appId: number;
  reload: number;
  onRule: (id: number) => void;
  onGo?: Go;
  onData?: (d: AppConnections | null) => void; // für Export der Doku (0.59.0)
  fold?: Fold; // 0.73.0: Bereich zuklappbar
}) {
  const [data, setData] = useState<AppConnections | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Picked>({});
  useEffect(() => setPicked({}), [appId]);
  const [view, setView] = useState<"table" | "plan">(loadView);
  const pickView = (v: "table" | "plan") => {
    setView(v);
    storeView(v);
  };
  // 0.74.1: erst laden, wenn der Bereich offen ist – die Wege sind der teuerste Teil der Doku
  const wanted = !fold || fold.open;
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const loadKey = `${appId}:${reload}`;
  useEffect(() => {
    if (!wanted || loadedFor === loadKey) return;
    let current = true;
    setLoadedFor(loadKey);
    setData(null);
    setError(null);
    onData?.(null);
    let done = false;
    fetchAppConnections(appId)
      .then((d) => {
        done = true;
        if (!current) return;
        setData(d);
        onData?.(d);
      })
      .catch((e) => {
        done = true;
        if (current) setError(e);
      });
    return () => {
      current = false;
      if (!done) setLoadedFor(null); // abgebrochen: beim nächsten Öffnen neu laden
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId, reload, wanted]);
  const needle = q.trim().toLowerCase();
  const base = (data?.connections ?? []).filter((c) => {
    if (onlyIssues && !c.mismatch.length && !c.inconsistent && c.verdict === "allow" && c.reached) return false;
    if (!needle) return true;
    const text = [c.src.label, c.src.ip, c.dst.label, c.dst.ip, c.service, ...c.hops.map((h) => h.label), ...c.rules.map((r) => r.label)]
      .join("\n")
      .toLowerCase();
    return text.includes(needle);
  });
  const rows = base.filter((c) => FACETS.every((g) => inGroup(c, g, picked)));
  const s = data?.summary;
  return (
    <div className="card" id="app-connections" style={{ marginTop: 14 }}>
      <div className="card-head">
        <FoldTitle fold={fold}>{L("Verbindungen Ende zu Ende", "End-to-end connections")}</FoldTitle>
        {s && (
          <span className="chips">
            <span className="chip">{L(`${s.total} Verbindungen`, `${s.total} connections`)}</span>
            {s.allow > 0 && <span className="chip ok">{L(`${s.allow} erlaubt`, `${s.allow} allowed`)}</span>}
            {s.blocked > 0 && <span className="chip crit">{L(`${s.blocked} gesperrt`, `${s.blocked} blocked`)}</span>}
            {s.open > 0 && (
              <span className="chip warn">{L(`${s.open} abhängig bzw. offen`, `${s.open} depends or open`)}</span>
            )}
            {s.mismatch > 0 && (
              <span
                className="chip crit"
                title={L(
                  "Regel erlaubt, der Weg sperrt bzw. ihr Gerät liegt nicht auf dem Weg",
                  "Rule allows, the path blocks or its device is not on the path",
                )}
              >
                {L(`${s.mismatch} Abweichungen`, `${s.mismatch} deviations`)}
              </span>
            )}
            {(s.inconsistent ?? 0) > 0 && (
              <span
                className="chip crit"
                id="conn-inconsistent"
                title={L(
                  "je nach Weg (aktiv, ECMP, Backup bzw. Firewall im Verbund) erlaubt bzw. gesperrt – als Befund",
                  "allowed or blocked depending on the path (active, ECMP, backup or firewall in the parallel group) – as a finding",
                )}
              >
                {L(`${s.inconsistent} Wege uneinheitlich`, `${s.inconsistent} paths inconsistent`)}
              </span>
            )}
            {(s.zone_violations ?? 0) > 0 && (
              <span
                className="chip crit"
                id="conn-zone-violations"
                title={L(
                  "der Weg erlaubt, was die Absicht zwischen den Zonen sperrt bzw. nicht vorsieht",
                  "the path allows what the intent between the zones denies or does not provide for",
                )}
              >
                {L(`${s.zone_violations} gegen die Zonen-Absicht`, `${s.zone_violations} against the zone intent`)}
              </span>
            )}
          </span>
        )}
        <div className="spacer" />
        {(!fold || fold.open) && (
        <>
        <span className="seg no-print" id="app-conn-view">
          <button className={view === "table" ? "on" : ""} id="app-conn-table" onClick={() => pickView("table")}>
            {L("Tabelle", "Table")}
          </button>
          <button className={view === "plan" ? "on" : ""} id="app-conn-plan" onClick={() => pickView("plan")}>
            {L("Plan", "Diagram")}
          </button>
        </span>
        <button className={`toggle no-print ${onlyIssues ? "on" : ""}`} onClick={() => setOnlyIssues((v) => !v)}>
          {L("nur Abweichungen und Gesperrte", "only deviations and blocked")}
        </button>
        </>
        )}
      </div>
      {(!fold || fold.open) && (
      <>
      <div className="card-body pad no-print" style={{ paddingBottom: 0 }}>
        <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
          {L(
            "Je Verbindung (Quelle → Ziel, Dienst) eine Zeile mit dem Weg über Firewalls und ACI wie in der Weg-Suche; " +
              "Klick zeigt je Hop die entscheidende Policy bzw. den Contract mit den Objekten und welche Regeln der Doku dort " +
              "greifen. Gegenstelle any, vzAny und Preferred Group bleiben Regeln ohne Verbindung.",
            "One row per connection (source → destination, service) with the path through firewalls and ACI as in the path " +
              "search; a click shows per hop the deciding policy or contract with the objects and which rules of the " +
              "documentation apply there. Peer any, vzAny and Preferred Group remain rules without a connection.",
          )}
        </div>
        <div className="filterbar" style={{ marginBottom: 10 }}>
          <div className="search">
            <span className="ico">
              <Icon name="search" />
            </span>
            <input placeholder={L("System, Gegenstelle, Firewall, Regel…", "System, peer, firewall, rule…")} value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {data && rows.length !== data.connections.length && (
            <span className="count" id="conn-filter-count">
              {L(`${rows.length} von ${data.connections.length}`, `${rows.length} of ${data.connections.length}`)}
            </span>
          )}
        </div>
        {data && data.connections.length > 0 && <Facets conns={data.connections} base={base} picked={picked} onPick={setPicked} />}
      </div>
      <ErrorBox error={error} prefix={L("Verbindungen laden", "Load connections")} />
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
          <Loading>{L("Prüfe die Wege…", "Checking the paths…")}</Loading>
        ) : rows.length === 0 ? (
          <Empty
            title={data?.connections.length ? L("Keine Treffer", "No matches") : L("Keine Verbindungen", "No connections")}
          >
            {data?.connections.length
              ? L("Filter anpassen.", "Adjust the filter.")
              : L(
                  "Die Regeln der Applikation nennen keine prüfbare Gegenstelle.",
                  "The rules of the application name no checkable peer.",
                )}
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{L("Richtung", "Direction")}</th>
                <th>{L("Quelle", "Source")}</th>
                <th>{L("Ziel", "Destination")}</th>
                <th>{L("Dienst", "Service")}</th>
                <th>{L("Weg", "Path")}</th>
                <th>{L("Urteil", "Verdict")}</th>
                <th title={L("Regeln der Doku, aus denen die Verbindung stammt", "Rules of the documentation the connection comes from")}>
                  {L("Regeln", "Rules")}
                </th>
                <th
                  title={L(
                    "Wege uneinheitlich (je nach Weg offen bzw. gesperrt) bzw. Regel erlaubt, Weg sperrt oder Gerät nicht auf dem Weg",
                    "Paths inconsistent (open or blocked depending on the path) or rule allows, path blocks or device not on the path",
                  )}
                ></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <Fragment key={c.key}>
                  <tr className="clickable" onClick={() => setOpen(open === c.key ? null : c.key)}>
                    <td>
                      <span className={`chip ${c.direction === "in" ? "info" : "local"}`}>{c.direction === "in" ? L("eingehend", "inbound") : L("ausgehend", "outbound")}</span>
                    </td>
                    <td>
                      <SideLabel s={c.src} />
                    </td>
                    <td>
                      <SideLabel s={c.dst} />
                    </td>
                    <td className="mono">{c.service ?? L("alle", "all")}</td>
                    <td>
                      <HopChain hops={c.hops} />
                      {!c.reached && c.end && <div className="faint">{c.end.text}</div>}
                      {c.parts && c.parts.length > 0 && (
                        <div
                          className="faint conn-parts"
                          title={L(
                            "Diese Teile des Netzes werden genauer anders geroutet – je eine eigene Verbindung",
                            "These parts of the network are routed differently more specifically – each its own connection",
                          )}
                        >
                          {L("ohne die genauer gerouteten Teile:", "without the more specifically routed parts:")}{" "}
                          {c.parts.join(", ")}
                        </div>
                      )}
                      {c.part_of && (
                        <div
                          className="faint conn-part-of"
                          title={L(
                            "Teil eines Netzes, das genauer anders geroutet wird als das ganze Netz",
                            "Part of a network that is routed differently more specifically than the whole network",
                          )}
                        >
                          {L("Teil der Verbindung", "Part of connection")} {c.part_of}
                        </div>
                      )}
                      {c.hops.length > 0 && <div className="print-only faint">{c.hops.map(hopText).join(" → ")}</div>}
                      {c.zones && <ZoneLine z={c.zones} />}
                    </td>
                    <td>
                      <Verdict v={c.verdict} title={c.text} />
                    </td>
                    <td className="num">{c.rules.length}</td>
                    <td>
                      {c.inconsistent && (
                        <span className="chip crit" title={variantsText(c)}>
                          {L("Wege uneinheitlich", "Paths inconsistent")}
                        </span>
                      )}
                      {c.inconsistent && <div className="print-only conn-why">{variantsText(c)}</div>}
                      {c.mismatch.length > 0 && (
                        <span className="chip crit" title={c.mismatch.join("\n")}>
                          {L("Abweichung", "Deviation")}
                        </span>
                      )}
                      {c.mismatch.length > 0 && <div className="print-only conn-why">{c.mismatch.join("; ")}</div>}
                      {c.zones?.violation && (
                        <span className="chip crit" title={c.zones.intents.map((i) => i.text).join("\n")}>
                          {L("gegen Zonen-Absicht", "against zone intent")}
                        </span>
                      )}
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
      </>
      )}
    </div>
  );
}
