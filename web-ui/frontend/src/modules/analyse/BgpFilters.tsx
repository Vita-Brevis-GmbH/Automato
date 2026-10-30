// BGP-Filter und Route Control (0.113.0, ADR 0041): je BGP-Nachbar die Filter ein/aus und Soll/Ist
// (erwartet, erhalten, angekündigt), je L3Out die Route Control, dazu die gelesene BGP-Konfiguration
// je Firewall. Nur gelesene Daten – Routing → BGP-Nachbarn bzw. L3Outs.
import { useEffect, useState } from "react";
import {
  BgpEntry,
  BgpNeighborConfig,
  BgpPeerReport,
  BgpProcess,
  BgpVerdict,
  BgpZoneSoll,
  fetchBgpConfig,
  fetchBgpPeer,
  fetchBgpPeerZones,
  fetchL3OutRouteControl,
  fetchL3OutZones,
  L3OutRouteControl,
  L3OutZoneSoll,
  RouteControl,
} from "../../api";
import { L } from "../../i18n";
import { Drawer, ErrorBox, Loading } from "../../ui";

const KIND: Record<string, string> = {
  route_map: "Route-Map",
  prefix_list: L("Prefix-Liste", "Prefix list"),
  distribute_list: L("Distribute-Liste", "Distribute list"),
  filter_list: L("Filter-Liste (AS-Path)", "Filter list (AS path)"),
  access_list: L("Access-Liste", "Access list"),
};
const kindOf = (k: string) => KIND[k.replace(/6$/, "")] ?? k;

export function Verdict({ v }: { v: BgpVerdict }) {
  if (v === "permit") return <span className="chip ok">{L("erlaubt", "permitted")}</span>;
  if (v === "deny") return <span className="chip crit">{L("gesperrt", "denied")}</span>;
  return (
    <span
      className="chip warn"
      title={L(
        "Bedingung, die die Plattform nicht auswertet (AS-Path, Community …) bzw. Filter nicht gelesen",
        "Condition the platform does not evaluate (AS path, community …) or filter not read",
      )}
    >
      {L("unbekannt", "unknown")}
    </span>
  );
}

// Filter eines Nachbarn in der Liste: „ein: RM_ISP_IN · aus: PL_DC“
export function FilterCell({ config }: { config: BgpNeighborConfig | undefined }) {
  if (!config) return <span className="faint">{L("Konfiguration nicht gelesen", "configuration not read")}</span>;
  const filters = config.filters ?? {};
  const ways: [string, string][] = [
    ["in", L("ein", "in")],
    ["out", L("aus", "out")],
  ];
  return (
    <div className="objs bgp-filters">
      {ways.map(([way, label]) => {
        const names = Object.entries({ ...(filters[way] ?? {}), ...(filters[`${way}6`] ?? {}) });
        return (
          <span key={way} className="mono" data-way={way}>
            <span className="faint">{label}: </span>
            {names.length ? (
              names.map(([kind, name], i) => (
                <span key={kind} title={kindOf(kind)}>
                  {i > 0 && ", "}
                  {name}
                </span>
              ))
            ) : (
              <span className="faint">{L("ohne Filter", "no filter")}</span>
            )}
          </span>
        );
      })}
      {config.group && <span className="faint">{L(`Gruppe ${config.group}`, `group ${config.group}`)}</span>}
    </div>
  );
}

const range = (e: BgpEntry) =>
  `${e.prefix}${e.ge ? ` ge ${e.ge}` : ""}${e.le ? ` le ${e.le}` : ""}${e.exact ? " exact" : ""}`;

function Count({ n, total }: { n: number; total?: number }) {
  return total != null && total > n ? (
    <span className="faint">{L(` (${n} von ${total} gezeigt)`, ` (${n} of ${total} shown)`)}</span>
  ) : null;
}

function Entries({ id, entries, received }: { id: string; entries: BgpEntry[]; received: boolean }) {
  if (!entries.length) return <div className="faint">{L("keine Regel erlaubt Netze ausdrücklich", "no rule explicitly permits networks")}</div>;
  return (
    <table className="compact" id={id}>
      <thead>
        <tr>
          <th>Filter</th>
          <th className="num">{L("Regel", "Rule")}</th>
          <th>{L("Netz", "Network")}</th>
          {received && <th>{L("Erhalten", "Received")}</th>}
        </tr>
      </thead>
      <tbody>
        {entries.map((e, i) => (
          <tr key={i} data-prefix={e.prefix} className={e.missing ? "bgp-missing" : ""}>
            <td>
              {e.filter} <span className="faint">({kindOf(e.kind)})</span>
            </td>
            <td className="num">{e.id ?? "–"}</td>
            <td className="mono">
              {range(e)}
              {e.all && <span className="faint">{L(" – alles", " – everything")}</span>}
            </td>
            {received && (
              <td>
                {e.missing ? (
                  <span className="chip warn">{L("nichts angekommen", "nothing arrived")}</span>
                ) : e.all ? (
                  <span className="faint">–</span>
                ) : (
                  L(`${e.received} Netz${e.received === 1 ? "" : "e"}`, `${e.received} network${e.received === 1 ? "" : "s"}`)
                )}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Soll/Ist eines BGP-Nachbarn: Filter, erwartet, erhalten, angekündigt (berechnet)
export function BgpPeerDrawer({ tenantId, peerId, onClose }: { tenantId: number; peerId: number; onClose: () => void }) {
  const [r, setR] = useState<BgpPeerReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    fetchBgpPeer(tenantId, peerId).then(setR).catch(setError);
  }, [tenantId, peerId]);
  const title = r ? `${r.label} → ${r.peer_ip}` : L("BGP-Nachbar", "BGP neighbor");
  return (
    <Drawer title={title} sub={L("Filter Soll/Ist (nur gelesen)", "Filters expected/actual (read only)")} onClose={onClose} wide>
      <div id="bgp-peer">
        <ErrorBox error={error} />
        {!r && !error && <Loading />}
        {r && !r.configured && (
          <div className="note">
            {L(
              "Für diesen Nachbarn ist keine BGP-Konfiguration gelesen (anderes Gerät als FortiGate, älterer Collector bzw. kein Leserecht auf router bgp).",
              "No BGP configuration read for this neighbor (device other than FortiGate, older collector or no read permission on router bgp).",
            )}
          </div>
        )}
        {r?.configured && r.in && r.out && (
          <>
            <div className="chips" style={{ marginBottom: 8 }}>
              <span className="chip">
                AS {r.local_as ?? "?"} → AS {r.remote_as ?? "?"}
              </span>
              {r.ebgp != null && <span className="chip info">{r.ebgp ? "eBGP" : "iBGP"}</span>}
              <span className={`chip ${r.established ? "ok" : "warn"}`}>{r.state ?? "?"}</span>
              {r.group && <span className="chip">{L(`Gruppe ${r.group}`, `group ${r.group}`)}</span>}
              {r.default_originate && (
                <span
                  className="chip info"
                  id="bgp-default-originate"
                  title={L(
                    "capability-default-originate: die Firewall sendet dem Nachbarn immer die Default-Route",
                    "capability-default-originate: the firewall always sends the default route to the neighbor",
                  )}
                >
                  default-originate
                </span>
              )}
              {r.description && <span className="faint">{r.description}</span>}
            </div>
            <h3>{L("Eingang – erwartet (was die Filter erlauben)", "Inbound – expected (what the filters permit)")}</h3>
            {r.in.open ? (
              <div className="note" id="bgp-in-open">
                {L(
                  "Ohne Eingangsfilter: die Firewall übernimmt alles, was der Nachbar ankündigt.",
                  "No inbound filter: the firewall accepts everything the neighbor announces.",
                )}
              </div>
            ) : (
              <>
                <div className="faint" style={{ marginBottom: 4 }}>
                  {Object.entries(r.in.filters).map(([k, v]) => `${kindOf(k)} ${v}`).join(" · ")}
                </div>
                <Entries id="bgp-in-entries" entries={r.in.entries} received />
              </>
            )}
            <h3>
              {L("Eingang – erhalten", "Inbound – received")}
              <Count n={r.in.received.length} total={r.in.received_total} />
            </h3>
            {r.in.received.length === 0 ? (
              <div className="faint">{L("Keine BGP-Pfade von diesem Nachbarn.", "No BGP paths from this neighbor.")}</div>
            ) : (
              <table className="compact" id="bgp-in-received">
                <thead>
                  <tr>
                    <th>{L("Netz", "Network")}</th>
                    <th>{L("Bester Pfad", "Best path")}</th>
                    <th>{L("Nach dem Filter", "By the filter")}</th>
                  </tr>
                </thead>
                <tbody>
                  {r.in.received.map((x) => (
                    <tr key={x.prefix} data-prefix={x.prefix}>
                      <td className="mono">{x.prefix}</td>
                      <td>{x.best ? L("ja", "yes") : <span className="faint">{L("Backup", "backup")}</span>}</td>
                      <td>
                        <Verdict v={x.verdict} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h3>
              {L("Ausgang – angekündigt (berechnet)", "Outbound – announced (computed)")}
              <Count n={r.out.announced.length} total={r.out.announced_total} />
            </h3>
            <div className="faint" style={{ marginBottom: 4 }}>
              {r.out.open
                ? L("Ohne Ausgangsfilter. ", "No outbound filter. ")
                : `${Object.entries(r.out.filters).map(([k, v]) => `${kindOf(k)} ${v}`).join(" · ")}. `}
              {L(
                "Beste BGP-Pfade (nicht vom Nachbarn selbst), network, aggregate und redistribute durch die Filter – die FortiGate meldet nicht, was sie ankündigt.",
                "Best BGP paths (not from the neighbor itself), network, aggregate and redistribute through the filters – the FortiGate does not report what it announces.",
              )}
              {r.out.counts.deny ? L(` ${r.out.counts.deny} gesperrt.`, ` ${r.out.counts.deny} denied.`) : ""}
              {r.out.counts.loop
                ? L(
                    ` ${r.out.counts.loop} nicht angekündigt: AS des Nachbarn im AS-Pfad (er würde sie verwerfen).`,
                    ` ${r.out.counts.loop} not announced: the neighbor's AS is in the AS path (it would discard them).`,
                  )
                : ""}
            </div>
            {r.out.announced.length === 0 ? (
              <div className="faint">{L("Nichts.", "Nothing.")}</div>
            ) : (
              <table className="compact" id="bgp-out-announced">
                <thead>
                  <tr>
                    <th>{L("Netz", "Network")}</th>
                    <th>{L("Herkunft", "Origin")}</th>
                    <th>{L("Nach dem Filter", "By the filter")}</th>
                  </tr>
                </thead>
                <tbody>
                  {r.out.announced.map((x) => (
                    <tr key={x.prefix} data-prefix={x.prefix}>
                      <td className="mono">{x.prefix}</td>
                      <td>{x.origin}</td>
                      <td>
                        <Verdict v={x.verdict} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {r.out.entries.length > 0 && (
              <>
                <h3>{L("Ausgang – was die Filter erlauben", "Outbound – what the filters permit")}</h3>
                <Entries id="bgp-out-entries" entries={r.out.entries} received={false} />
              </>
            )}
            <ZoneSoll tenantId={tenantId} peerId={peerId} />
          </>
        )}
      </div>
    </Drawer>
  );
}

// Soll aus den Zonen (0.114.0, ADR 0041 Stufe 2/3): Prefix-Liste aus Netzen und Ausschlüssen der
// Zonen, Vergleich mit dem Filter der Richtung und CLI-Vorschlag zum Kopieren (kein Schreibweg).
// Eingang = Zonen dahinter, Ausgang (0.116.0) = Zonen diesseits mit dem, was heute angekündigt wird
function ZoneSoll({ tenantId, peerId }: { tenantId: number; peerId: number }) {
  const [way, setWay] = useState<"in" | "out">("in");
  const [z, setZ] = useState<BgpZoneSoll | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState<number | null>(null);
  useEffect(() => {
    setZ(null);
    setError(null);
    setCopied(null);
    fetchBgpPeerZones(tenantId, peerId, way).then(setZ).catch(setError);
  }, [tenantId, peerId, way]);
  const copy = (family: number, text: string) => {
    void navigator.clipboard?.writeText(text).then(() => setCopied(family));
  };
  const out = way === "out";
  return (
    <div id="bgp-zones" data-way={way}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <h3 style={{ margin: 0 }}>{L("Soll aus den Zonen", "Target from the zones")}</h3>
        <div className="seg" id="bgp-zones-way">
          <button className={out ? "" : "on"} onClick={() => setWay("in")}>
            {L("Eingang", "Inbound")}
          </button>
          <button className={out ? "on" : ""} onClick={() => setWay("out")}>
            {L("Ausgang", "Outbound")}
          </button>
        </div>
      </div>
      <div className="faint" style={{ margin: "4px 0" }}>
        {out
          ? L(
              "Was die Firewall dem Nachbarn ankündigen soll: die Netze der Zonen, die er über sie erreicht.",
              "What the firewall should announce to the neighbor: the networks of the zones it reaches through it.",
            )
          : L(
              "Was die Firewall vom Nachbarn übernehmen soll: die Netze der Zonen dahinter.",
              "What the firewall should accept from the neighbor: the networks of the zones behind.",
            )}
      </div>
      <ErrorBox error={error} />
      {!z && !error && <Loading />}
      {z && (
        <>
          <div className={`note ${z.state === "differs" ? "warn" : ""}`} id="bgp-zones-state" data-state={z.state}>
            {z.text}
          </div>
          {z.neighbor_zone && (
            <div className="faint" style={{ margin: "4px 0" }}>
              {L("Zone der Firewall", "Zone of the firewall")}: <b>{z.home?.name}</b> · {L("Zone am Nachbarn", "Zone at the neighbor")}:{" "}
              <b>{z.neighbor_zone.name}</b> · {out ? L("Zonen diesseits", "Zones on this side") : L("Zonen dahinter", "Zones behind")}:{" "}
              {(z.zones ?? []).map((x) => x.name).join(", ")}
              {z.public_only && (
                <span className="chip info" style={{ marginLeft: 6 }}>
                  {L("nur öffentliche Netze (Nachbar ausserhalb)", "public networks only (neighbor outside)")}
                </span>
              )}
            </div>
          )}
          {(z.lists ?? []).map((x) => (
            <div key={x.family} className="bgp-zone-list" data-family={x.family}>
              <div className="faint" style={{ margin: "6px 0 4px" }}>
                {x.open
                  ? out
                    ? L("Heute ohne Ausgangsfilter – kündigt alles an.", "No outbound filter today – announces everything.")
                    : L("Heute ohne Eingangsfilter – erlaubt alles.", "No inbound filter today – permits everything.")
                  : `${L("Heute", "Today")}: ${Object.entries(x.filters).map(([k, v]) => `${kindOf(k)} ${v}`).join(" · ")}`}
              </div>
              <table className="compact">
                <thead>
                  <tr>
                    <th>{L("Netz", "Network")}</th>
                    <th>{L("Soll", "Target")}</th>
                    <th>{L("Zone", "Zone")}</th>
                    <th>{L("Filter heute", "Filter today")}</th>
                  </tr>
                </thead>
                <tbody>
                  {x.entries.map((e) => (
                    <tr key={`${e.action}-${e.prefix}`} data-prefix={e.prefix}>
                      <td className="mono">{e.prefix}</td>
                      <td>{e.action === "permit" ? L("erlauben", "permit") : L("sperren (Ausschluss)", "deny (exclusion)")}</td>
                      <td>{e.zones.join(", ")}</td>
                      <td>
                        <Verdict v={e.verdict} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {x.missing.length > 0 && (
                <div className="bgp-zone-missing" style={{ marginTop: 4 }}>
                  <span className="chip warn">{L("fehlt im Filter", "missing in the filter")}</span>{" "}
                  <span className="mono">{x.missing.join(", ")}</span>
                  {x.missing_total > x.missing.length && <span className="faint"> +{x.missing_total - x.missing.length}</span>}
                </div>
              )}
              {x.too_open.length > 0 && (
                <div className="bgp-zone-open" style={{ marginTop: 4 }}>
                  <span className="chip warn">{L("zu offen", "too open")}</span>{" "}
                  {x.too_open.map((t) => (t.all ? L(`${t.filter}: alles`, `${t.filter}: everything`) : `${t.filter}: ${t.prefix}`)).join(" · ")}
                </div>
              )}
              {x.excluded_allowed.length > 0 && (
                <div style={{ marginTop: 4 }}>
                  <span className="chip warn">{L("Ausschluss erlaubt", "exclusion permitted")}</span>{" "}
                  <span className="mono">{x.excluded_allowed.join(", ")}</span>
                </div>
              )}
              {(x.announced_outside ?? []).length > 0 && (
                <div className="bgp-zone-outside" style={{ marginTop: 4 }}>
                  <span className="chip warn">{L("angekündigt ausserhalb der Zonen", "announced outside the zones")}</span>{" "}
                  <span className="mono">{(x.announced_outside ?? []).join(", ")}</span>
                  {(x.announced_outside_total ?? 0) > (x.announced_outside ?? []).length && (
                    <span className="faint"> +{(x.announced_outside_total ?? 0) - (x.announced_outside ?? []).length}</span>
                  )}
                </div>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "8px 0 4px" }}>
                <b>{L(`Vorschlag ${x.name} (FortiOS-CLI)`, `Proposal ${x.name} (FortiOS CLI)`)}</b>
                <button className="btn btn-sm" onClick={() => copy(x.family, x.cli)}>
                  {copied === x.family ? L("kopiert", "copied") : L("Kopieren", "Copy")}
                </button>
                <span className="faint">{L("nur Text – Automato schreibt nichts auf die Firewall", "text only – Automato writes nothing to the firewall")}</span>
              </div>
              <pre className="block bgp-cli">{x.cli}</pre>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// Gelesene BGP-Konfiguration je Firewall (Prozess, Filter, Nachbarn ohne Sitzung)
export function BgpConfig({ tenantId }: { tenantId: number }) {
  const [rows, setRows] = useState<BgpProcess[] | null>(null);
  useEffect(() => {
    fetchBgpConfig(tenantId)
      .then(setRows)
      .catch(() => setRows([]));
  }, [tenantId]);
  if (!rows?.length) return null;
  const list = (xs: { prefix?: string; name?: string; route_map?: string }[] | undefined) =>
    (xs ?? []).map((x) => `${x.prefix ?? x.name}${x.route_map ? ` (${x.route_map})` : ""}`).join(", ") || "–";
  return (
    <details className="note" id="bgp-config" style={{ marginBottom: 8 }}>
      <summary>
        {L(
          `BGP-Konfiguration gelesen: ${rows.length} Firewall-Kontext${rows.length === 1 ? "" : "e"}`,
          `BGP configuration read: ${rows.length} firewall context${rows.length === 1 ? "" : "s"}`,
        )}
      </summary>
      <table className="compact">
        <thead>
          <tr>
            <th>{L("System · Kontext", "System · context")}</th>
            <th>AS</th>
            <th>network</th>
            <th>aggregate</th>
            <th>redistribute</th>
            <th>Filter</th>
            <th>{L("Konfiguriert, ohne Sitzung", "Configured, no session")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.source_system_id}-${p.node}-${p.context}`} data-context={p.label}>
              <td>
                <b>{p.label}</b>
                {p.router_id && <div className="faint">Router-ID {p.router_id}</div>}
              </td>
              <td className="mono">{p.as ?? "–"}</td>
              <td className="mono">{list(p.networks)}</td>
              <td className="mono">{list(p.aggregates)}</td>
              <td className="mono">{list(p.redistribute)}</td>
              <td>
                {Object.entries(p.objects)
                  .map(([k, n]) => `${n} ${kindOf(k)}`)
                  .join(", ") || "–"}
              </td>
              <td className="mono">
                {p.configured_only.length
                  ? p.configured_only.map((a) => `${a.peer_ip}${a.config.shutdown ? L(" (abgeschaltet)", " (shut down)") : ""}`).join(", ")
                  : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

// Route Control eines L3Outs in der Liste: erzwungen je Richtung, Anzahl Subnetze
export function RouteControlCell({ rc }: { rc: RouteControl | null | undefined }) {
  const enforce = rc?.enforce ?? ["export"];
  const imports = rc?.import?.length ?? 0;
  const exports = rc?.export?.length ?? 0;
  return (
    <div className="objs rc-cell">
      <span>
        {enforce.map((e) => (
          <span key={e} className="chip" style={{ marginRight: 4 }}>
            {e}
          </span>
        ))}
      </span>
      <span className="faint">
        {L(`Import ${imports} · Export ${exports} Subnetze`, `import ${imports} · export ${exports} subnets`)}
      </span>
      {imports > 0 && !enforce.includes("import") && (
        <span className="chip warn" title={L("import-rtctrl wirkt nur mit enforce import", "import-rtctrl only works with enforce import")}>
          {L("Import nicht erzwungen", "import not enforced")}
        </span>
      )}
    </div>
  );
}

export function RouteControlDrawer({ tenantId, l3outId, onClose }: { tenantId: number; l3outId: number; onClose: () => void }) {
  const [r, setR] = useState<L3OutRouteControl | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    fetchL3OutRouteControl(tenantId, l3outId).then(setR).catch(setError);
  }, [tenantId, l3outId]);
  return (
    <Drawer
      title={r ? `${r.l3out} · ${r.vrf ?? r.aci_tenant}` : "L3Out"}
      sub={L("Route Control Soll/Ist (nur gelesen)", "Route control expected/actual (read only)")}
      onClose={onClose}
      wide
    >
      <div id="l3out-rc">
        <ErrorBox error={error} />
        {!r && !error && <Loading />}
        {r && (
          <>
            <div className="chips" style={{ marginBottom: 8 }}>
              {r.enforce.map((e) => (
                <span key={e} className="chip info">
                  enforce {e}
                </span>
              ))}
              <span className="faint">
                {L(`Peers: ${r.peers.join(", ") || "–"}`, `peers: ${r.peers.join(", ") || "–"}`)}
              </span>
            </div>
            <h3>{L("Import – erwartet (import-rtctrl)", "Import – expected (import-rtctrl)")}</h3>
            {!r.import_enforced && r.import.entries.length > 0 && (
              <div className="note">
                {L(
                  "Import ist am L3Out nicht erzwungen – diese Subnetze wirken nicht, der L3Out übernimmt alle Routen.",
                  "Import is not enforced on the L3Out – these subnets have no effect, the L3Out accepts all routes.",
                )}
              </div>
            )}
            {r.import.entries.length === 0 ? (
              <div className="faint">{L("Keine Subnetze mit import-rtctrl.", "No subnets with import-rtctrl.")}</div>
            ) : (
              <table className="compact" id="rc-import">
                <thead>
                  <tr>
                    <th>{L("Subnetz", "Subnet")}</th>
                    <th>{L("Externe EPG", "External EPG")}</th>
                    <th>{L("Erhalten", "Received")}</th>
                  </tr>
                </thead>
                <tbody>
                  {r.import.entries.map((e) => (
                    <tr key={e.prefix} data-prefix={e.prefix}>
                      <td className="mono">
                        {e.prefix}
                        {e.aggregate && <span className="faint"> aggregate</span>}
                      </td>
                      <td>{e.epg ?? "–"}</td>
                      <td>
                        {e.missing ? (
                          <span className="chip warn">{L("nichts angekommen", "nothing arrived")}</span>
                        ) : (
                          L(`${e.received} Route${e.received === 1 ? "" : "n"}`, `${e.received} route${e.received === 1 ? "" : "s"}`)
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h3>
              {L("Erhalten über die Peers des L3Outs", "Received via the peers of the L3Out")}
              <Count n={r.import.received.length} total={r.import.received_total} />
            </h3>
            {r.import.received.length === 0 ? (
              <div className="faint">{L("Keine Routen über diese Peers.", "No routes via these peers.")}</div>
            ) : (
              <table className="compact" id="rc-received">
                <tbody>
                  {r.import.received.map((x) => (
                    <tr key={x.prefix} data-prefix={x.prefix}>
                      <td className="mono">{x.prefix}</td>
                      <td>
                        <Verdict v={x.verdict} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h3>{L("Export – angekündigt (export-rtctrl)", "Export – announced (export-rtctrl)")}</h3>
            {r.export.entries.length === 0 ? (
              <div className="faint">{L("Keine Subnetze mit export-rtctrl.", "No subnets with export-rtctrl.")}</div>
            ) : (
              <div className="mono" id="rc-export">
                {r.export.entries.map((e) => `${e.prefix}${e.aggregate ? " aggregate" : ""}`).join(", ")}
              </div>
            )}
            <L3OutZones tenantId={tenantId} l3outId={l3outId} />
          </>
        )}
      </div>
    </Drawer>
  );
}

// Import Route Control gegen die Zonen hinter dem L3Out (0.116.0, ADR 0041 Stufe 3): Netze der
// Zonen, Urteil der Route Control heute, Abweichungen und Subnetze als Vorschlag (nur Text)
function L3OutZones({ tenantId, l3outId }: { tenantId: number; l3outId: number }) {
  const [z, setZ] = useState<L3OutZoneSoll | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    fetchL3OutZones(tenantId, l3outId).then(setZ).catch(setError);
  }, [tenantId, l3outId]);
  return (
    <div id="l3out-zones">
      <h3>{L("Soll aus den Zonen dahinter (Import)", "Target from the zones behind (import)")}</h3>
      <ErrorBox error={error} />
      {!z && !error && <Loading />}
      {z && (
        <>
          <div className={`note ${z.state === "differs" ? "warn" : ""}`} id="l3out-zones-state" data-state={z.state}>
            {z.text}
          </div>
          {z.neighbor_zone && (
            <div className="faint" style={{ margin: "4px 0" }}>
              {L("Zone der VRF", "Zone of the VRF")}: <b>{z.home?.name}</b> · {L("Zone am Peer", "Zone at the peer")}:{" "}
              <b>{z.neighbor_zone.name}</b> · {L("Zonen dahinter", "Zones behind")}: {(z.zones ?? []).map((x) => x.name).join(", ")}
            </div>
          )}
          {z.import_enforced === false && (
            <div className="note warn">
              {L(
                "Import ist am L3Out nicht erzwungen – er übernimmt alle Routen, nicht nur die der Zonen.",
                "Import is not enforced on the L3Out – it accepts all routes, not only those of the zones.",
              )}
            </div>
          )}
          {(z.entries ?? []).length > 0 && (
            <table className="compact" id="l3out-zones-table">
              <thead>
                <tr>
                  <th>{L("Netz", "Network")}</th>
                  <th>{L("Zone", "Zone")}</th>
                  <th>{L("Route Control heute", "Route control today")}</th>
                </tr>
              </thead>
              <tbody>
                {(z.entries ?? []).map((e) => (
                  <tr key={e.prefix} data-prefix={e.prefix}>
                    <td className="mono">{e.prefix}</td>
                    <td>{e.zones.join(", ")}</td>
                    <td>
                      <Verdict v={e.verdict} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {(z.missing ?? []).length > 0 && (
            <div style={{ marginTop: 4 }}>
              <span className="chip warn">{L("ohne Import Route Control", "without import route control")}</span>{" "}
              <span className="mono">{(z.missing ?? []).join(", ")}</span>
            </div>
          )}
          {(z.too_open ?? []).length > 0 && (
            <div style={{ marginTop: 4 }}>
              <span className="chip warn">{L("importiert ausserhalb der Zonen", "imported outside the zones")}</span>{" "}
              <span className="mono">{(z.too_open ?? []).map((t) => t.prefix).join(", ")}</span>
            </div>
          )}
          {(z.excluded_allowed ?? []).length > 0 && (
            <div style={{ marginTop: 4 }}>
              <span className="chip warn">{L("Ausschluss importiert", "exclusion imported")}</span>{" "}
              <span className="mono">{(z.excluded_allowed ?? []).join(", ")}</span>
            </div>
          )}
          {z.proposal && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "8px 0 4px" }}>
                <b>{L("Vorschlag Subnetze der externen EPG", "Proposal subnets of the external EPG")}</b>
                <button
                  className="btn btn-sm"
                  onClick={() => void navigator.clipboard?.writeText(z.proposal ?? "").then(() => setCopied(true))}
                >
                  {copied ? L("kopiert", "copied") : L("Kopieren", "Copy")}
                </button>
                <span className="faint">{L("nur Text – Automato schreibt nichts ins ACI", "text only – Automato writes nothing to the ACI")}</span>
              </div>
              <pre className="block bgp-cli" id="l3out-zones-proposal">
                {z.proposal}
              </pre>
            </>
          )}
        </>
      )}
    </div>
  );
}
