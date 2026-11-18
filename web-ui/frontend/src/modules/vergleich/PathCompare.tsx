// Wege vergleichen (1.19.0, ADR 0067; Anwender 2026-11-17: „Modul nach Netzwerkschichten“ – ein L3-Weg gegen L1/L2):
// je L3-Abschnitt (Ausgang eines Hops bis zum Eingang des nächsten bzw. bis zum Anschluss von Quelle/Ziel) drei Prüfungen –
// VLAN auf allen Trunks erlaubt, Kabel und Nachbarn (NetBox, Verkabelung), Spanning Tree blockiert nicht. Was nicht
// prüfbar ist, steht mit Grund da (ADR 0048: nie still). Drei Reiter: ein Weg wie in der Weg-Suche, die Verbindungen
// einer Applikation (je Verbindung bzw. alle nacheinander) und alle Abschnitte des Mandanten (Grundlage der Befunde).
import { FormEvent, useEffect, useRef, useState } from "react";
import { AppConnection, Application, fetchAppConnections, fetchApps } from "../../api";
import { L } from "../../i18n";
import type { View, ViewParams } from "../types";
import { replaceHash } from "../../places";
import { Empty, ErrorBox, Icon, Loading, Note, PageHead } from "../../ui";
import {
  CompareCheck,
  CompareItem,
  CompareKind,
  CompareQuery,
  CompareSection,
  CompareSections,
  CompareState,
  CompareSummary,
  CompareTrace,
  fetchCompareSections,
  fetchCompareTrace,
} from "./compareApi";

type Tab = "weg" | "app" | "abschnitte";
const TABS: { key: Tab; label: string }[] = [
  { key: "weg", label: L("Weg", "Path") },
  { key: "app", label: L("Applikation", "Application") },
  { key: "abschnitte", label: L("Alle Abschnitte", "All sections") },
];

export const STATE: Record<CompareState, [string, string, string]> = {
  ok: ["ok", L("passt", "matches"), L("alle drei Prüfungen ohne Abweichung", "all three checks without deviation")],
  deviation: ["crit", L("Abweichung", "deviation"), L("mindestens eine Prüfung weicht ab", "at least one check deviates")],
  partial: ["info", L("teilweise geprüft", "partly checked"), L("ohne Abweichung, aber nicht alles prüfbar – Grund am Abschnitt", "no deviation, but not everything checkable – reason at the section")],
  unchecked: ["na", L("nicht prüfbar", "not checkable"), L("der Abschnitt lässt sich nicht gegen L1/L2 prüfen – Grund am Abschnitt", "the section cannot be checked against L1/L2 – reason at the section")],
  vpn: ["na", "VPN", L("über einen IPsec-Tunnel – kein L2-Abschnitt", "via an IPsec tunnel – not an L2 section")],
  internal: ["na", L("intern", "internal"), L("Inter-VDOM-Link bzw. im selben Gerät – kein L2-Abschnitt", "inter-VDOM link or within the same device – not an L2 section")],
};
const KIND: Record<CompareKind, string> = {
  vlan: L("VLAN erlaubt", "VLAN allowed"),
  cable: L("Kabel & Nachbarn", "Cables & neighbors"),
  stp: "Spanning Tree",
};
const VLAN_FROM: Record<string, string> = {
  subinterface: L("VLAN-Subinterface", "VLAN subinterface"),
  svi: "SVI",
  access: L("Access-VLAN des Switch-Ports", "access VLAN of the switch port"),
  native: L("Native VLAN des Switch-Ports", "native VLAN of the switch port"),
  target: L("VLAN am Eingang", "VLAN at the ingress"),
};

function StateChip({ state }: { state: CompareState }) {
  const [cls, label, title] = STATE[state] ?? ["", state, ""];
  return (
    <span className={`chip ${cls} compare-state`} data-state={state} title={title}>
      {label}
    </span>
  );
}

const port = (p: { device: string; port?: string | null }) => `${p.device} ${p.port ?? ""}`.trim();

function itemText(it: CompareItem): string {
  if (it.kind === "not_allowed") {
    return (it.ports ?? [])
      .map((p) => `${port(p)}: ${p.mode === "access" ? L(`Access-VLAN ${p.vlans ?? "?"}`, `access VLAN ${p.vlans ?? "?"}`) : L(`erlaubt ${p.vlans ?? "?"}`, `allowed ${p.vlans ?? "?"}`)}`)
      .join(" · ");
  }
  if (it.kind === "other_vlan") return L(`Eingang ${port({ device: it.device ?? "?", port: it.port })} in VLAN ${it.vlan}`, `ingress ${port({ device: it.device ?? "?", port: it.port })} in VLAN ${it.vlan}`);
  if (it.kind === "blocked") {
    const how = it.state === "err-disabled" ? `err-disabled${it.reason ? ` (${it.reason})` : ""}` : `${it.state ?? ""}${it.role ? ` (${it.role})` : ""}`;
    return `${port({ device: it.device ?? "?", port: it.port })}: ${how}`;
  }
  if (it.kind === "unread") return L(`ohne gelesenen Spanning Tree: ${(it.devices ?? []).join(", ")}`, `without read Spanning Tree: ${(it.devices ?? []).join(", ")}`);
  if (it.kind === "netbox") {
    const seen = (it.wrong ?? []).map((w) => L(`an ${port(w.at)} NetBox ${port(w.netbox)}, CDP/LLDP ${w.seen.map(port).join(", ")}`, `at ${port(w.at)} NetBox ${port(w.netbox)}, CDP/LLDP ${w.seen.map(port).join(", ")}`));
    return L(`Kabel ${it.cable} (${it.netbox}) passt nicht: `, `cable ${it.cable} (${it.netbox}) does not match: `) + seen.join("; ");
  }
  if (it.kind === "link_half_duplex") return L(`Duplex nicht voll: ${(it.devices ?? []).join(", ")}`, `duplex not full: ${(it.devices ?? []).join(", ")}`);
  const ends = it.a && it.b ? ` (${port(it.a)} ↔ ${port(it.b)})` : "";
  return `${it.text ?? it.kind}${ends}`;
}

function CheckCell({ kind, check }: { kind: CompareKind; check: CompareCheck }) {
  const items = check.items.filter((i) => i.kind !== "unread" || check.state !== "deviation");
  return (
    <div className="compare-check" data-kind={kind}>
      <div>
        <StateChip state={check.state} /> <b>{KIND[kind]}</b>
        {check.count > 0 && <span className="faint"> · {L(`${check.count} geprüft`, `${check.count} checked`)}</span>}
      </div>
      {items.map((it, i) => (
        <div key={i} className={`compare-item ${it.kind === "unread" ? "faint" : ""}`} data-item={it.kind}>
          {itemText(it)}
        </div>
      ))}
      {check.reasons.map((r) => (
        <div key={r.key} className="faint compare-reason" data-reason={r.key}>
          {L("nicht geprüft: ", "not checked: ")}
          {r.text}
        </div>
      ))}
    </div>
  );
}

export function SectionCard({ s }: { s: CompareSection }) {
  const l2 = s.kind === "l2" || s.kind === "attach";
  return (
    <div className="card compare-section" data-state={s.state} style={{ marginBottom: 10 }}>
      <div className="card-head">
        <h3 style={{ margin: 0, fontSize: 14 }}>
          {s.kind === "attach" && <span className="faint">{L("Anschluss · ", "Attachment · ")}</span>}
          {s.label}
        </h3>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {s.vlan != null && (
            <span className="chip info" title={s.vlan_from ? VLAN_FROM[s.vlan_from] ?? s.vlan_from : undefined}>
              VLAN {s.vlan}
            </span>
          )}
          <StateChip state={s.state} />
        </span>
      </div>
      <div className="card-body">
        {s.segment && (
          <div className="mono compare-segment" style={{ fontSize: 12, marginBottom: 8 }}>
            <Icon name="cable" size={13} /> {s.segment.text}
          </div>
        )}
        {s.reasons.map((r) => (
          <div key={r.key} className="faint compare-reason" data-reason={r.key}>
            {L("nicht prüfbar: ", "not checkable: ")}
            {r.text}
          </div>
        ))}
        {l2 && !s.reasons.length && (
          <div className="compare-checks" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
            {(["vlan", "cable", "stp"] as CompareKind[]).map((k) => (
              <CheckCell key={k} kind={k} check={s.checks[k]} />
            ))}
          </div>
        )}
        {!l2 && <div className="faint">{STATE[s.state]?.[2]}</div>}
      </div>
    </div>
  );
}

export function SummaryLine({ s }: { s: CompareSummary }) {
  const n = (k: CompareState) => s.states[k] ?? 0;
  return (
    <p className="faint compare-summary">
      {L(
        `${s.sections} Abschnitte: ${n("ok")} passen, ${n("deviation")} mit Abweichung, ${n("partial")} teilweise geprüft, ${n("unchecked")} nicht prüfbar`,
        `${s.sections} sections: ${n("ok")} match, ${n("deviation")} with deviation, ${n("partial")} partly checked, ${n("unchecked")} not checkable`,
      )}
      {n("vpn") + n("internal") > 0 && L(`, ${n("vpn") + n("internal")} ohne L2 (VPN, intern)`, `, ${n("vpn") + n("internal")} without L2 (VPN, internal)`)}
      {" · "}
      {(["vlan", "cable", "stp"] as CompareKind[]).map((k) => `${KIND[k]} ${s.deviations[k]}`).join(" · ")}
      {s.unchecked.length > 0 && (
        <>
          <br />
          {L("Nicht (ganz) geprüft: ", "Not (fully) checked: ")}
          {s.unchecked.map((u) => `${u.text} (${u.count})`).join(" · ")}
        </>
      )}
    </p>
  );
}

function TraceResult({ data }: { data: CompareTrace }) {
  if (!data.paths.length) {
    return (
      <Empty title={L("Kein Weg gefunden", "No path found")}>
        {data.notes.map((n, i) => (
          <div key={i}>{n}</div>
        ))}
      </Empty>
    );
  }
  return (
    <div id="compare-result">
      <SummaryLine s={data.summary} />
      {data.paths.map((p, i) => (
        <div key={i} className="compare-path" style={{ marginBottom: 18 }}>
          <h3 style={{ fontSize: 14, margin: "8px 0" }}>
            {p.kind === "backup" ? L("Backup-Weg", "Backup path") : L("Weg", "Path")} {i + 1}: {p.hops.join(" → ") || "–"}
            {p.end ? <span className="faint"> · {p.end.text}</span> : null}
          </h3>
          {p.sections.length === 0 ? (
            <Note>{L("Kein Abschnitt zwischen zwei Geräten – nur ein Hop.", "No section between two devices – only one hop.")}</Note>
          ) : (
            p.sections.map((s, j) => <SectionCard key={j} s={s} />)
          )}
        </div>
      ))}
    </div>
  );
}

function PathTab({ tenantId, params }: { tenantId: number; params: ViewParams }) {
  const [src, setSrc] = useState(params.src ?? "");
  const [dst, setDst] = useState(params.dst ?? "");
  const [service, setService] = useState(params.service ?? "");
  const [data, setData] = useState<CompareTrace | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const run = (q: CompareQuery) => {
    setBusy(true);
    setErr(null);
    setData(null);
    fetchCompareTrace(tenantId, q)
      .then(setData)
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  const first = useRef(true);
  useEffect(() => {
    if (!first.current) return;
    first.current = false;
    if (params.dst && (params.src || params.start)) {
      run({
        src: params.src,
        dst: params.dst,
        service: params.service,
        start: params.start,
        connection: params.connection === "1" || params.connection === "true",
        starts: params.starts ? params.starts.split(",") : [],
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    if (!dst.trim() || !src.trim()) return;
    const q = { src: src.trim(), dst: dst.trim(), ...(service.trim() ? { service: service.trim() } : {}) };
    replaceHash("vergleich", q);
    run(q);
  };
  return (
    <>
      <form className="filterbar" onSubmit={submit} id="compare-form">
        <input className="input" id="compare-src" placeholder={L("Quelle (IP bzw. Netz)", "Source (IP or network)")} value={src} onChange={(e) => setSrc(e.target.value)} />
        <input className="input" id="compare-dst" placeholder={L("Ziel (IP bzw. Netz)", "Destination (IP or network)")} value={dst} onChange={(e) => setDst(e.target.value)} />
        <input className="input" id="compare-service" placeholder={L("Dienst, z. B. tcp/443", "Service, e.g. tcp/443")} value={service} onChange={(e) => setService(e.target.value)} />
        <button className="btn btn-sm" id="compare-run" type="submit" disabled={busy || !src.trim() || !dst.trim()}>
          {L("Vergleichen", "Compare")}
        </button>
      </form>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {busy && <Loading>{L("Weg suchen und vergleichen…", "Searching and comparing the path…")}</Loading>}
      {data && <TraceResult data={data} />}
      {!data && !busy && !err && (
        <Note>
          {L(
            "Quelle und Ziel wie in der Weg-Suche eingeben – je Abschnitt zwischen zwei Hops prüft der Vergleich VLAN, Kabel und Spanning Tree.",
            "Enter source and destination as in the path search – per section between two hops the comparison checks VLAN, cables and Spanning Tree.",
          )}
        </Note>
      )}
    </>
  );
}

interface ConnRow {
  conn: AppConnection;
  state: "open" | "busy" | "done" | "error";
  data?: CompareTrace;
}

function AppTab({ tenantId, params }: { tenantId: number; params: ViewParams }) {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [appId, setAppId] = useState(params.app ?? "");
  const [rows, setRows] = useState<ConnRow[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);
  const live = useRef(0);
  useEffect(() => {
    fetchApps(tenantId).then(setApps).catch(setErr);
  }, [tenantId]);
  useEffect(() => {
    setRows(null);
    setNote(null);
    if (!appId) return;
    const mine = ++live.current;
    fetchAppConnections(Number(appId))
      .then((d) => {
        if (mine !== live.current) return;
        setRows(d.connections.filter((c) => !!c.trace || (c.src.ip && c.dst.ip)).map((conn) => ({ conn, state: "open" })));
        if (d.snapshot && (d.snapshot.building || !d.snapshot.built_at) && !d.connections.length) {
          setNote(L("Die Verbindungen werden noch gerechnet – später erneut öffnen.", "The connections are still being computed – open again later."));
        }
      })
      .catch(setErr);
  }, [appId]);
  const query = (c: AppConnection): CompareQuery => {
    const t = c.trace ?? { src: c.src.ip, dst: c.dst.ip, service: c.service, starts: [] };
    return { src: t.src, dst: t.dst, service: t.service, connection: true, starts: t.starts };
  };
  const compare = async (keys: string[]) => {
    for (const key of keys) {
      const row = rows?.find((r) => r.conn.key === key);
      if (!row || live.current < 0) continue;
      setRows((rs) => rs && rs.map((r) => (r.conn.key === key ? { ...r, state: "busy" } : r)));
      try {
        const data = await fetchCompareTrace(tenantId, query(row.conn));
        setRows((rs) => rs && rs.map((r) => (r.conn.key === key ? { ...r, state: "done", data } : r)));
      } catch (e) {
        setErr(e);
        setRows((rs) => rs && rs.map((r) => (r.conn.key === key ? { ...r, state: "error" } : r)));
      }
    }
  };
  const done = (rows ?? []).filter((r) => r.state === "done");
  const total = (k: CompareState) => done.reduce((n, r) => n + (r.data?.summary.states[k] ?? 0), 0);
  return (
    <>
      <div className="filterbar">
        <select
          className="sel"
          id="compare-app"
          aria-label={L("Applikation", "Application")}
          value={appId}
          onChange={(e) => {
            setAppId(e.target.value);
            replaceHash("vergleich", e.target.value ? { tab: "app", app: e.target.value } : { tab: "app" });
          }}
        >
          <option value="">{L("Applikation wählen…", "Choose application…")}</option>
          {(apps ?? []).map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        {rows && rows.length > 0 && (
          <button className="btn btn-sm" id="compare-all" onClick={() => compare(rows.filter((r) => r.state !== "done").map((r) => r.conn.key))}>
            {L("Alle vergleichen", "Compare all")}
          </button>
        )}
        {rows && (
          <span className="count" id="compare-app-count">
            {L(`${done.length} von ${rows.length} Verbindungen verglichen`, `${done.length} of ${rows.length} connections compared`)}
          </span>
        )}
      </div>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {note && <Note>{note}</Note>}
      {done.length > 0 && (
        <p className="faint" id="compare-app-summary">
          {L(
            `Abschnitte der verglichenen Verbindungen: ${total("ok")} passen, ${total("deviation")} mit Abweichung, ${total("partial")} teilweise, ${total("unchecked")} nicht prüfbar`,
            `Sections of the compared connections: ${total("ok")} match, ${total("deviation")} with deviation, ${total("partial")} partly, ${total("unchecked")} not checkable`,
          )}
        </p>
      )}
      {appId && rows === null && !err && <Loading />}
      {rows && rows.length === 0 && !note && <Empty title={L("Keine Verbindungen", "No connections")} />}
      {rows && rows.length > 0 && (
        <table className="compact" id="compare-connections">
          <thead>
            <tr>
              <th>{L("Verbindung", "Connection")}</th>
              <th>{L("Dienst", "Service")}</th>
              <th>{L("Vergleich", "Comparison")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const s = r.data?.summary;
              const worst: CompareState | null = s
                ? (["deviation", "unchecked", "partial", "ok"] as CompareState[]).find((k) => (s.states[k] ?? 0) > 0) ?? null
                : null;
              return (
                <tr key={r.conn.key} data-conn={r.conn.key} className={open === r.conn.key ? "selected" : ""}>
                  <td>
                    {r.conn.src.label} → {r.conn.dst.label}
                    {open === r.conn.key && r.data && (
                      <div style={{ marginTop: 8 }}>
                        <TraceResult data={r.data} />
                      </div>
                    )}
                  </td>
                  <td className="mono">{r.conn.service ?? L("alle", "all")}</td>
                  <td>
                    {r.state === "busy" && <span className="faint">{L("vergleicht…", "comparing…")}</span>}
                    {r.state === "error" && <span className="chip warn">{L("Fehler", "error")}</span>}
                    {worst && <StateChip state={worst} />}
                    {s && <span className="faint"> {L(`${s.sections} Abschnitte`, `${s.sections} sections`)}</span>}
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {r.state === "done" ? (
                      <button className="btn btn-ghost btn-sm compare-open" onClick={() => setOpen(open === r.conn.key ? null : r.conn.key)}>
                        {open === r.conn.key ? L("zuklappen", "collapse") : L("Abschnitte", "sections")}
                      </button>
                    ) : (
                      <button
                        className="btn btn-ghost btn-sm compare-one"
                        disabled={r.state === "busy"}
                        onClick={() => compare([r.conn.key]).then(() => setOpen(r.conn.key))}
                      >
                        {L("Vergleichen", "Compare")}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}

function SectionsTab({ tenantId, params }: { tenantId: number; params: ViewParams }) {
  const [state, setState] = useState(params.state ?? "");
  const [q, setQ] = useState(params.q ?? "");
  const [data, setData] = useState<CompareSections | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    const t = window.setTimeout(() => {
      fetchCompareSections(tenantId, state || undefined, q.trim() || undefined)
        .then((d) => live && setData(d))
        .catch((e) => live && setErr(e));
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [tenantId, state, q]);
  return (
    <>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input id="compare-q" placeholder={L("Gerät, Interface, VLAN…", "Device, interface, VLAN…")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="sel" id="compare-state" aria-label={L("Zustand", "State")} value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">{L("Zustand: alle", "State: all")}</option>
          {(Object.keys(STATE) as CompareState[]).map((k) => (
            <option key={k} value={k}>
              {STATE[k][1]}
            </option>
          ))}
        </select>
        {data && <span className="count">{data.total}</span>}
      </div>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {data === null ? (
        !err && <Loading />
      ) : (
        <>
          {!data.cabling && (
            <Note warn>
              {L(
                "Keine Verkabelung gelesen – ohne Kabel (CDP/LLDP der Switches) lässt sich kein Abschnitt gegen L1/L2 prüfen; dafür gibt es keine Befunde.",
                "No cabling read – without cables (CDP/LLDP of the switches) no section can be checked against L1/L2; there are no findings for it.",
              )}
            </Note>
          )}
          <SummaryLine s={data.summary} />
          {Object.keys(data.not_l2).length > 0 && (
            <p className="faint">
              {L("Ohne L2 (nicht geprüft, nur ausgewiesen): ", "Without L2 (not checked, only listed): ")}
              {Object.entries(data.not_l2)
                .map(([k, n]) => `${STATE[k as CompareState]?.[1] ?? k} ${n}`)
                .join(" · ")}
            </p>
          )}
          {data.items.length === 0 ? (
            <Empty title={L("Keine L3-Abschnitte", "No L3 sections")}>
              {L(
                "Kein Next-Hop der Routing-Tabellen gehört einem anderen gelesenen Gerät (bzw. der Filter passt auf keinen).",
                "No next-hop of the routing tables belongs to another read device (or the filter matches none).",
              )}
            </Empty>
          ) : (
            <div id="compare-sections">
              {data.items.map((s) => (
                <SectionCard key={s.label} s={s} />
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

export default function PathCompare({
  tenantId,
  tenantLabel,
  params,
  go,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params: ViewParams;
  go: (v: View, p?: ViewParams) => void;
}) {
  const initial: Tab = TABS.some((t) => t.key === params.tab) ? (params.tab as Tab) : params.app ? "app" : "weg";
  const [tab, setTab] = useState<Tab>(initial);
  const pick = (t: Tab) => {
    setTab(t);
    replaceHash("vergleich", t === "weg" ? {} : { tab: t });
  };
  return (
    <>
      <PageHead title={L("Wege vergleichen", "Compare paths")}>
        {L(
          <>
            Ein L3-Weg von „{tenantLabel}“ gegen L1/L2: je Abschnitt vom Ausgang eines Hops bis zum Eingang des nächsten (bzw. bis
            zum Anschluss von Quelle und Ziel) – ist das VLAN auf allen Trunks erlaubt, passen Kabel und Nachbarn (CDP/LLDP gegen
            NetBox, Befunde der Verkabelung), blockiert Spanning Tree nicht? Was nicht prüfbar ist, steht mit Grund da. Die
            Befunde rechnet der Abgleich über alle Abschnitte des Mandanten; den Weg selbst zeigt die{" "}
            <a href="#wege">Weg-Suche</a>. Rein lesend.
          </>,
          <>
            An L3 path of “{tenantLabel}” against L1/L2: per section from the egress of a hop to the ingress of the next (or to
            the attachment of source and destination) – is the VLAN allowed on all trunks, do cables and neighbors match
            (CDP/LLDP against NetBox, cabling findings), does Spanning Tree not block? Whatever cannot be checked is shown with
            the reason. The findings come from the reconciliation over all sections of the tenant; the path itself is shown by
            the <a href="#wege">path search</a>. Read-only.
          </>,
        )}
      </PageHead>
      {tenantId === null ? (
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      ) : (
        <>
          <div className="tabbar sticky">
            {TABS.map((t) => (
              <button key={t.key} id={`compare-tab-${t.key}`} className={tab === t.key ? "on" : ""} onClick={() => pick(t.key)}>
                {t.label}
              </button>
            ))}
            <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} onClick={() => go("findings")}>
              {L("Befunde →", "Findings →")}
            </button>
          </div>
          {tab === "weg" && <PathTab tenantId={tenantId} params={params} />}
          {tab === "app" && <AppTab tenantId={tenantId} params={params} />}
          {tab === "abschnitte" && <SectionsTab tenantId={tenantId} params={params} />}
        </>
      )}
    </>
  );
}
