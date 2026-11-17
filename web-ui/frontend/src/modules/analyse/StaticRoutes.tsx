// Statische Routen je Gerät und VRF (1.19.0, Anwender 2026-11-17 Nr. 2 „statisches Routing“): je Route der Zustand
// gegen die Routing-Tabelle (aktiv, Backup, überdeckt, nicht installiert, deaktiviert, unbekannt) mit Begründung,
// Next-Hop mit Gegenstelle, Befunde je Route und was nicht prüfbar war (keine stillen Grenzen, ADR 0048). Dazu
// `UsedStatics`: die statischen Routen auf den Wegen einer Applikation (Doku, Verbindungen Ende zu Ende).
import { useEffect, useState } from "react";
import { AppStaticRoute, fetchStaticRoutes, StaticRouteRow, StaticRoutes as Data, StaticState } from "../../api";
import { L } from "../../i18n";
import { Pager, useDebounced } from "../../Objects";
import { Names, VrfChip } from "../../Routing";
import { findingLabel } from "../../refs";
import { Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead, SharedChip } from "../../ui";

const PAGE = 200;

// Zustand -> (Farbe, Bezeichnung, Erklärung)
export const STATIC_STATE: Record<StaticState, [string, string, string]> = {
  active: ["ok", L("aktiv", "active"), L("steht in der Routing-Tabelle", "is in the routing table")],
  backup: [
    "info",
    L("Backup", "backup"),
    L(
      "bewusste Backup-Route: Distanz über dem Standard des Geräts (bzw. höhere Priorität) – greift, wenn die bessere Route wegfällt",
      "deliberate backup route: distance above the device default (or higher priority) – takes over when the better route disappears",
    ),
  ],
  shadowed: [
    "warn",
    L("überdeckt", "shadowed"),
    L(
      "wird nie aktiv bzw. nie genutzt – eine bessere Route bzw. spezifischere Routen über das ganze Netz",
      "never becomes active or is never used – a better route or more specific routes over the whole network",
    ),
  ],
  not_installed: [
    "warn",
    L("nicht installiert", "not installed"),
    L(
      "eingeschaltet, aber nicht in der Routing-Tabelle (Next-Hop bzw. Interface prüfen)",
      "enabled but not in the routing table (check next-hop or interface)",
    ),
  ],
  disabled: ["", L("deaktiviert", "disabled"), L("in der Konfiguration ausgeschaltet", "switched off in the configuration")],
  unknown: ["", L("unbekannt", "unknown"), L("nicht prüfbar – Grund in der Zeile", "not checkable – reason in the row")],
};
const STATES = Object.keys(STATIC_STATE) as StaticState[];

const ORIGIN: Record<StaticRouteRow["origin"], [string, string]> = {
  config: [L("Konfiguration", "Configuration"), L("statische Route der Konfiguration", "static route of the configuration")],
  rib: [
    L("nur Routing-Tabelle", "routing table only"),
    L(
      "steht nur in der Routing-Tabelle (z. B. IPsec add-route bzw. Router ohne gelesene Konfiguration)",
      "only in the routing table (e.g. IPsec add-route or router without configuration read)",
    ),
  ],
  l3out: ["L3Out", L("statische Route eines ACI-L3Outs (Border-Leaf)", "static route of an ACI L3Out (border leaf)")],
};

function StateChip({ state }: { state: StaticState }) {
  const [cls, label, title] = STATIC_STATE[state] ?? ["", state, ""];
  return (
    <span className={`chip ${cls}`} title={title}>
      {label}
    </span>
  );
}

function vrfOf(r: { vrf: number | string }) {
  return r.vrf === 0 || r.vrf === "0" ? null : r.vrf === "mgmt" ? "mgmt" : r.vrf;
}

function NextHopCell({ r }: { r: StaticRouteRow }) {
  const hop = r.nexthop;
  return (
    <div className="objs">
      {r.blackhole ? (
        <span className="chip" style={{ alignSelf: "flex-start" }} title={L("Verkehr in dieses Netz wird verworfen", "traffic to this network is dropped")}>
          Blackhole
        </span>
      ) : r.target?.kind === "sdwan" ? (
        <span className="chip info" style={{ alignSelf: "flex-start" }}>
          SD-WAN {r.target.name}
        </span>
      ) : r.target?.kind === "dynamic" ? (
        <span className="chip info" style={{ alignSelf: "flex-start" }}>
          {L("dynamisches Gateway", "dynamic gateway")}
        </span>
      ) : (
        <span className="mono">
          {r.next_hop ?? L("direkt", "direct")}
          {r.next_hop && <Names r={r.resolved_next_hop} />}
          {r.interface ? <span className="faint"> via {r.interface}</span> : null}
          {r.zone ? <span className="chip" style={{ marginLeft: 4 }}>{r.zone}</span> : null}
        </span>
      )}
      {hop?.state === "unreachable" && <span className="crit static-nexthop">{hop.text}</span>}
      {hop?.state === "ok" && r.state !== "active" && <span className="faint static-nexthop">{hop.text}</span>}
      {hop?.owner && <span className="faint">→ {hop.owner}</span>}
      {hop?.remote && (
        <span
          className="faint"
          title={L(
            "Der Next-Hop gehört keinem gelesenen Gerät – geprüft ist er nur bis ins angeschlossene Netz",
            "The next-hop belongs to no device read – it is only checked up to the connected network",
          )}
        >
          {L("Gegenstelle nicht gelesen", "peer not read")}
        </span>
      )}
    </div>
  );
}

function Summary({ data, issues, onIssues, onState }: { data: Data; issues: boolean; onIssues: (on: boolean) => void; onState: (s: string) => void }) {
  const s = data.summary;
  const findings = Object.entries(s.issues).sort();
  return (
    <div className="card" id="static-summary">
      <div className="card-head">
        <h2>{L("Übersicht", "Overview")}</h2>
        <span className="chips">
          <span className="chip">{L(`${s.total} statische Routen`, `${s.total} static routes`)}</span>
          {STATES.filter((k) => s.states[k]).map((k) => (
            <button
              key={k}
              className={`chip linkchip ${STATIC_STATE[k][0]}`}
              data-state={k}
              title={`${STATIC_STATE[k][2]} – ${L("Klick filtert", "click to filter")}`}
              onClick={() => onState(k)}
            >
              {s.states[k]} {STATIC_STATE[k][1]}
            </button>
          ))}
        </span>
      </div>
      <div className="card-body pad">
        {findings.length > 0 && (
          <div className="chips" id="static-findings">
            <span className="faint">{L("Befunde:", "Findings:")}</span>
            {findings.map(([t, n]) => (
              <button
                key={t}
                className={`chip linkchip ${t === "static_blackhole" || t === "static_unchecked" ? "info" : "warn"}`}
                onClick={() => onIssues(!issues)}
                title={L("nur Routen mit Befund zeigen", "show only routes with findings")}
              >
                {n} {findingLabel(t)}
              </button>
            ))}
          </div>
        )}
        {(s.unchecked.length > 0 || s.remote > 0) && (
          <div className="chips" id="static-unchecked" style={{ marginTop: 6 }}>
            <span className="faint">{L("Nicht (ganz) geprüft:", "Not (fully) checked:")}</span>
            {s.unchecked.map((u) => (
              <span key={u.reason} className="chip info" title={u.text}>
                {u.count} · {u.text}
              </span>
            ))}
            {s.remote > 0 && (
              <span
                className="chip"
                title={L(
                  "Next-Hop gehört keinem gelesenen Gerät – geprüft bis ins angeschlossene Netz, die Gegenstelle selbst nicht",
                  "Next-hop belongs to no device read – checked up to the connected network, not the peer itself",
                )}
              >
                {L(`${s.remote} Next-Hops nur bis ins angeschlossene Netz`, `${s.remote} next-hops only up to the connected network`)}
              </span>
            )}
          </div>
        )}
        {s.rib_only.length > 0 && (
          <div id="static-rib-only" style={{ marginTop: 6 }}>
            <span className="faint">{L("Nur die Routing-Tabelle gelesen:", "Only the routing table read:")} </span>
            {s.rib_only.map((x) => (
              <span key={x.node_key} className="chip warn" title={x.text} style={{ marginRight: 4 }}>
                {x.label}
              </span>
            ))}
            <span className="faint">
              {L(
                " – inaktive bzw. schwebende statische Routen sind dort nicht sichtbar, „überdeckt“ und „doppelt“ nicht prüfbar.",
                " – inactive or floating static routes are not visible there, “shadowed” and “duplicate” not checkable.",
              )}
            </span>
          </div>
        )}
        {findings.length === 0 && s.unchecked.length === 0 && s.rib_only.length === 0 && s.total > 0 && (
          <span className="chip ok">{L("keine Befunde", "no findings")}</span>
        )}
      </div>
    </div>
  );
}

export default function StaticRoutes({
  tenantId,
  tenantLabel,
  params = {},
}: {
  tenantId: number | null;
  tenantLabel: string;
  params?: Record<string, string>; // aus einem Link (Befund, Doku): context, q, state, ip
}) {
  const [q, setQ] = useState(params.q ?? "");
  const [ip, setIp] = useState(params.ip ?? "");
  const [context, setContext] = useState(params.context ?? "");
  const [vrf, setVrf] = useState("");
  const [state, setState] = useState(params.state ?? "");
  const [issues, setIssues] = useState(params.issues === "1");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<unknown>(null);
  const query = useDebounced(q);
  const ipQuery = useDebounced(ip);

  useEffect(() => setOffset(0), [query, ipQuery, context, vrf, state, issues]);
  useEffect(() => {
    if (tenantId === null) return;
    setError(null);
    let current = true; // nur die Antwort zur letzten Anfrage
    fetchStaticRoutes({ tenantId, q: query, ip: ipQuery, context, vrf, state, issues, limit: PAGE, offset })
      .then((d) => current && setData(d))
      .catch((e) => {
        if (!current) return;
        setError(e);
      });
    return () => {
      current = false;
    };
  }, [tenantId, query, ipQuery, context, vrf, state, issues, offset]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title={L("Statische Routen", "Static routes")} />
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      </>
    );
  }
  return (
    <>
      <PageHead title={L("Statische Routen", "Static routes")}>
        {L(
          <>
            Statische Routen von „{tenantLabel}“ je Gerät und VRF – FortiGate und Junos aus der Konfiguration (auch
            deaktivierte und schwebende), Cisco und Arista aus der Routing-Tabelle, ACI aus den L3Outs. Je Route der
            Zustand gegen die Routing-Tabelle: <b>Backup</b> ist eine bewusste Backup-Route (Distanz über dem Standard des
            Geräts: FortiGate 10, Junos 5, Cisco und ACI 1), <b>überdeckt</b> eine, die nie aktiv wird. Rein lesend, Stand
            des letzten Laufs; alle Routen samt BGP im <a href="#routing">Routing</a>.
          </>,
          <>
            Static routes of “{tenantLabel}” per device and VRF – FortiGate and Junos from the configuration (including
            disabled and floating ones), Cisco and Arista from the routing table, ACI from the L3Outs. Per route the state
            against the routing table: <b>backup</b> is a deliberate backup route (distance above the device default:
            FortiGate 10, Junos 5, Cisco and ACI 1), <b>shadowed</b> one that never becomes active. Read-only, state of the
            last run; all routes including BGP under <a href="#routing">Routing</a>.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("Statische Routen laden", "Load static routes")} />
      {data && <Summary data={data} issues={issues} onIssues={setIssues} onState={(s) => setState(state === s ? "" : s)} />}
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder={L("Präfix, Next-Hop, Gerät, Kommentar…", "Prefix, next-hop, device, comment…")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="search" style={{ flex: "0 1 210px" }}>
          <span className="ico">
            <Icon name="route" />
          </span>
          <input
            placeholder={L("enthält IP…", "contains IP…")}
            title={L("Routen, deren Präfix die IP enthält – längstes Präfix zuerst", "Routes whose prefix contains the IP – longest prefix first")}
            value={ip}
            onChange={(e) => setIp(e.target.value)}
          />
        </div>
        <select className="sel" aria-label={L("Kontext", "Context")} value={context} onChange={(e) => setContext(e.target.value)}>
          <option value="">{L("Gerät: alle", "Device: all")}</option>
          {(data?.contexts ?? []).map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
          {context && !(data?.contexts ?? []).some((c) => c.key === context) && <option value={context}>{context}</option>}
        </select>
        {(data?.vrfs.length ?? 0) > 0 && (
          <select className="sel" aria-label="VRF" value={vrf} onChange={(e) => setVrf(e.target.value)}>
            <option value="">{L("VRF: alle", "VRF: all")}</option>
            <option value="0">{L("VRF 0 (Standard)", "VRF 0 (default)")}</option>
            {data?.vrfs.map((v) => (
              <option key={v} value={v}>
                {v === "mgmt" ? "Management" : `VRF ${v}`}
              </option>
            ))}
          </select>
        )}
        <select className="sel" aria-label={L("Zustand", "State")} value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">{L("Zustand: alle", "State: all")}</option>
          {STATES.map((k) => (
            <option key={k} value={k}>
              {STATIC_STATE[k][1]}
            </option>
          ))}
        </select>
        <label className="check">
          <input type="checkbox" id="static-issues" checked={issues} onChange={(e) => setIssues(e.target.checked)} />{" "}
          {L("nur mit Befund", "with findings only")}
        </label>
        {data && <span className="count">{data.total}</span>}
      </div>
      <div className="card">
        <div className="card-body scroll viewport">
          {data === null ? (
            <Loading />
          ) : data.rows.length === 0 ? (
            <Empty title={L("Keine statischen Routen", "No static routes")}>
              {L("Filter anpassen bzw. Quellsysteme neu einlesen.", "Adjust the filter or collect the source systems again.")}
            </Empty>
          ) : (
            <table className="compact" id="static-routes">
              <thead>
                <tr>
                  <th>{L("Gerät · Kontext", "Device · context")}</th>
                  <th>{L("Ziel", "Destination")}</th>
                  <th>{L("Next-Hop / Interface", "Next-hop / interface")}</th>
                  <th className="num">{L("Distanz", "Distance")}</th>
                  <th>{L("Zustand", "State")}</th>
                  <th>{L("Quelle", "Source")}</th>
                  <th>{L("Befunde", "Findings")}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.key} data-prefix={r.prefix} data-seq={r.seq ?? undefined} className={r.state === "disabled" ? "faint" : undefined}>
                    <td>
                      <b>{r.label}</b>
                      <SharedChip shared={r.shared} />
                      {vrfOf(r) != null && (
                        <>
                          {" "}
                          <VrfChip vrf={vrfOf(r)} />
                        </>
                      )}
                      {r.source !== r.node && <div className="faint">{r.source}</div>}
                    </td>
                    <td className="mono">
                      {r.target?.kind === "object" ? (
                        <>
                          <b>{r.target.name}</b>{" "}
                          <span className="chip info" title={L("Ziel ist ein Adressobjekt", "destination is an address object")}>
                            {L("Objekt", "object")}
                          </span>
                          {r.resolved.length > 0 && <div className="faint">{r.resolved.join(", ")}</div>}
                        </>
                      ) : (
                        <b>{r.prefix}</b>
                      )}
                    </td>
                    <td>
                      <NextHopCell r={r} />
                    </td>
                    <td className="num">
                      {r.distance ?? "–"}
                      {r.priority != null && <div className="faint">{L(`Prio ${r.priority}`, `prio ${r.priority}`)}</div>}
                    </td>
                    <td className="static-state">
                      <StateChip state={r.state} />
                      {r.reason && r.state !== "active" && <div className="faint static-reason">{r.reason}</div>}
                      {r.state === "active" && r.reason && r.reason !== r.state_text && r.resolved.length > 0 && (
                        <div className="faint static-reason">{r.reason}</div>
                      )}
                    </td>
                    <td title={ORIGIN[r.origin]?.[1]}>
                      {ORIGIN[r.origin]?.[0] ?? r.origin}
                      {r.seq && <span className="faint"> · Seq {r.seq}</span>}
                      {r.l3out && <span className="faint"> · {r.l3out}</span>}
                      {r.comment && <div className="faint">{r.comment}</div>}
                    </td>
                    <td>
                      <div className="chips">
                        {r.issues
                          .filter((t) => t !== "static_unchecked")
                          .map((t) => (
                            <span key={t} className={`chip ${t === "static_blackhole" ? "info" : "warn"}`}>
                              {findingLabel(t)}
                              {t === "static_duplicate" ? ` ×${r.duplicate}` : ""}
                            </span>
                          ))}
                        {r.unchecked.map((u) => (
                          <span key={u.reason} className="chip" title={L("nicht geprüft", "not checked")}>
                            {u.text}
                          </span>
                        ))}
                        {!r.issues.length && <Dash />}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {data && data.total > 0 && <Pager total={data.total} offset={offset} onOffset={setOffset} pageSize={PAGE} />}
      </div>
    </>
  );
}

const href = (s: AppStaticRoute) => `#statisch?${new URLSearchParams({ context: s.node_key, q: s.prefix }).toString()}`;

// Doku der Applikation (1.19.0): statische Routen, die die Wege ihrer Verbindungen nutzen – aus der Spur der
// Weg-Suche, mit den Angaben der Konfiguration; je Route, wie viele Verbindungen sie nutzen (aktiv bzw. nur Backup)
export function UsedStatics({ items }: { items: AppStaticRoute[] }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  return (
    <div className="card-body pad" id="conn-statics">
      <button className="linkbtn" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "▾" : "▸"} {L(`Statische Routen auf den Wegen (${items.length})`, `Static routes on the paths (${items.length})`)}
      </button>
      {open && (
        <table className="compact" style={{ marginTop: 6 }}>
          <thead>
            <tr>
              <th>{L("Gerät", "Device")}</th>
              <th>{L("Ziel", "Destination")}</th>
              <th>Next-Hop</th>
              <th className="num">{L("Distanz", "Distance")}</th>
              <th>{L("Quelle", "Source")}</th>
              <th>{L("Genutzt", "Used")}</th>
              <th className="num">{L("Verbindungen", "Connections")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.key}>
                <td>
                  <b>{s.label}</b>
                  {vrfOf(s) != null && (
                    <>
                      {" "}
                      <VrfChip vrf={vrfOf(s)} />
                    </>
                  )}
                </td>
                <td className="mono">
                  <a href={href(s)} title={L("in „Statische Routen“ öffnen", "open in “Static routes”")}>
                    {s.prefix}
                  </a>
                </td>
                <td className="mono">
                  {s.next_hop ?? L("direkt", "direct")}
                  {s.interface && <span className="faint"> via {s.interface}</span>}
                </td>
                <td className="num">{s.distance ?? "–"}</td>
                <td title={ORIGIN[s.origin]?.[1]}>
                  {ORIGIN[s.origin]?.[0] ?? s.origin}
                  {s.seq && <span className="faint"> · Seq {s.seq}</span>}
                  {s.l3out && <span className="faint"> · {s.l3out}</span>}
                  {s.comment && <div className="faint">{s.comment}</div>}
                </td>
                <td>
                  {s.use === "active" ? (
                    <span className="chip ok">{L("aktiv", "active")}</span>
                  ) : (
                    <span className="chip info" title={L("nur auf Backup-Wegen", "only on backup paths")}>
                      {L("Backup-Weg", "backup path")}
                    </span>
                  )}
                </td>
                <td className="num" title={s.connections.join("\n") + (s.count > s.connections.length ? `\n… +${s.count - s.connections.length}` : "")}>
                  {s.count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
