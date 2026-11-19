// AS-Nummern im Routing einzeln (1.19.0, ADR 0066; Anwender 2026-10-30 Roadmap Version 2 Nr. 10: „AS-Nummern im Routing
// einzeln darstellen (je AS die Nachbarn, Firewalls und Router)“). Je AS die gelesenen Geräte mit diesem AS (Firewalls,
// Router) und die BGP-Nachbarn mit diesem AS – an welchem Gerät, Zustand, eBGP/iBGP, Name aus dem IPAM, gelesenes Gerät
// dahinter –, eigen bzw. fremd, privat und die Reservierungen. Nur aus den schon gelesenen BGP-Daten.
import { useEffect, useState } from "react";
import { L } from "../../i18n";
import { Names } from "../../Routing";
import { Dash, Empty, ErrorBox, Loading, Note, PageHead } from "../../ui";
import type { View, ViewParams } from "../types";
import { AsDevice, AsEntry, AsNeighbor, AsNumbers as Data, fetchAsNumbers } from "./reservationsApi";

const KIND: Record<AsDevice["kind"], string> = {
  firewall: "Firewall",
  router: "Router",
  fabric: L("ACI-Fabric", "ACI fabric"),
};

function setHash(params: Record<string, string | null>) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const s = q.toString();
  window.history.replaceState(null, "", `#asn${s ? `?${s}` : ""}`);
}

export default function AsNumbers({
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
  const [q, setQ] = useState(params.q ?? "");
  const [scope, setScope] = useState<"" | "own" | "foreign">(params.scope === "own" || params.scope === "foreign" ? params.scope : "");
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (tenantId === null) return;
    let live = true;
    setError(null);
    const t = window.setTimeout(() => {
      fetchAsNumbers(tenantId, { q: q.trim() || undefined, scope })
        .then((d) => live && setData(d))
        .catch((e) => live && setError(e));
      setHash({ q: q.trim() || null, scope: scope || null });
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [tenantId, q, scope]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title={L("AS-Nummern", "AS numbers")} />
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      </>
    );
  }
  const s = data?.summary;
  const unused = (data?.reservations ?? []).filter((r) => r.used.length === 0);
  return (
    <>
      <PageHead title={L("AS-Nummern", "AS numbers")}>
        {L(
          <>
            AS-Nummern im Routing von „{tenantLabel}“ einzeln: je AS die gelesenen Firewalls und Router mit diesem AS und die
            BGP-Nachbarn mit diesem AS – an welchem Gerät die Sitzung steht, Zustand, eBGP/iBGP, Name aus dem IPAM und das
            gelesene Gerät dahinter. Eigen sind die AS der gelesenen Geräte und ihrer iBGP-Nachbarn (wie die Firmengrenze).
            Aus den schon gelesenen BGP-Daten; reservieren unter „Reservierungen“.
          </>,
          <>
            AS numbers in the routing of “{tenantLabel}” one by one: per AS the read firewalls and routers with this AS and the
            BGP neighbors with this AS – on which device the session is, state, eBGP/iBGP, name from the IPAM and the read
            device behind it. Own are the AS of the read devices and their iBGP neighbors (like the company boundary). From
            the BGP data already read; reserve under “Reservations”.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("AS-Nummern laden", "Load AS numbers")} />
      {s && (
        <div className="kpis" id="asn-kpis">
          <button className="kpi" onClick={() => setScope("")}>
            <div className="k-label">{L("AS-Nummern", "AS numbers")}</div>
            <div className="k-val">{s.total}</div>
            <div className="k-sub">{L(`${s.private} privat`, `${s.private} private`)}</div>
          </button>
          <button className="kpi" onClick={() => setScope("own")}>
            <div className="k-label">{L("Eigen", "Own")}</div>
            <div className="k-val">{s.own}</div>
            <div className="k-sub">{L("lokal an gelesenen Geräten bzw. iBGP", "local on read devices or iBGP")}</div>
          </button>
          <button className="kpi" onClick={() => setScope("foreign")}>
            <div className="k-label">{L("Fremd", "Foreign")}</div>
            <div className="k-val">{s.foreign}</div>
            <div className="k-sub">{L("nur als Nachbar", "only as neighbor")}</div>
          </button>
          <button className="kpi" onClick={() => go("reservierungen", { tab: "asn" })}>
            <div className="k-label">{L("Reserviert", "Reserved")}</div>
            <div className="k-val">{s.reserved}</div>
            <div className="k-sub">{L(`in Gebrauch · ${unused.length} Reservierungen frei`, `in use · ${unused.length} reservations free`)}</div>
          </button>
        </div>
      )}
      <div className="card" id="asn-list">
        <div className="card-head">
          <h2>{L("Je AS", "Per AS")}</h2>
          <span className="chips">
            <input
              id="asn-q"
              className="input input-inline"
              placeholder={L("AS, Gerät, Nachbar, L3Out …", "AS, device, neighbor, L3Out …")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <select id="asn-scope" className="sel" aria-label={L("Eigen bzw. fremd", "Own or foreign")} value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
              <option value="">{L("alle", "all")}</option>
              <option value="own">{L("eigene", "own")}</option>
              <option value="foreign">{L("fremde", "foreign")}</option>
            </select>
          </span>
        </div>
        <div className="card-body">
          {data === null ? (
            error ? null : <Loading />
          ) : data.items.length === 0 ? (
            <Empty title={L("Keine AS-Nummern", "No AS numbers")}>
              <span className="faint">
                {q || scope
                  ? L("Für diesen Filter gibt es keine.", "There are none for this filter.")
                  : L(
                      "Noch keine BGP-Daten gelesen (FortiGate, Router, ACI-L3Outs).",
                      "No BGP data read yet (FortiGate, routers, ACI L3Outs).",
                    )}
              </span>
            </Empty>
          ) : (
            data.items.map((e) => <AsCard key={e.asn} e={e} />)
          )}
        </div>
      </div>
      {unused.length > 0 && (
        <div className="card" id="asn-reserved-free" style={{ marginTop: 16 }}>
          <div className="card-head">
            <h2>{L("Reserviert, nicht in Gebrauch", "Reserved, not in use")}</h2>
          </div>
          <div className="card-body pad">
            {unused.map((r) => (
              <div key={r.id}>
                <b className="mono">{r.label}</b> {r.purpose} <span className="faint">({r.by})</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function AsCard({ e }: { e: AsEntry }) {
  return (
    <div className="asn-card" data-asn={e.asn} style={{ borderTop: "1px solid var(--border)", padding: "12px 16px" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <h3 className="mono" style={{ margin: 0 }}>
          AS {e.asn}
        </h3>
        <span className={`chip ${e.own ? "ok" : "info"}`}>{e.own ? L("eigen", "own") : L("fremd", "foreign")}</span>
        {e.private && (
          <span className="chip na" title={L("privates AS (64512–65534, 4200000000–4294967294)", "private AS (64512–65534, 4200000000–4294967294)")}>
            {L("privat", "private")}
          </span>
        )}
        {e.reservations.map((r) => (
          <span key={r.id} className="chip warn asn-reserved" title={`${r.label} · ${r.by}`}>
            {L("reserviert", "reserved")}: {r.purpose}
          </span>
        ))}
        <span className="faint">
          {L(
            `${e.devices.length} Geräte · ${e.neighbors.length} Nachbarn (${e.established} aufgebaut)`,
            `${e.devices.length} devices · ${e.neighbors.length} neighbors (${e.established} established)`,
          )}
        </span>
      </div>
      {e.devices.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <span className="faint">{L("Mit diesem AS: ", "With this AS: ")}</span>
          {e.devices.map((d, i) => (
            <span key={i} className="chip asn-device" title={d.router_id ? `Router-ID ${d.router_id}` : undefined} style={{ marginRight: 4 }}>
              {KIND[d.kind]} {d.label}
            </span>
          ))}
        </div>
      )}
      {e.neighbors.length > 0 && (
        <table className="compact" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>{L("Nachbar", "Neighbor")}</th>
              <th>{L("an", "at")}</th>
              <th>{L("Sitzung", "Session")}</th>
              <th>{L("Zustand", "State")}</th>
            </tr>
          </thead>
          <tbody>
            {e.neighbors.map((n, i) => (
              <NeighborRow key={i} n={n} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function NeighborRow({ n }: { n: AsNeighbor }) {
  return (
    <tr className="asn-neighbor">
      <td>
        <span className="mono">{n.peer_ip}</span>
        <Names r={n} />
        {n.peer_node && <div className="faint">→ {n.peer_node}</div>}
        {n.description && <div className="faint">{n.description}</div>}
      </td>
      <td>
        {KIND[n.at.kind]} {n.at.label}
        {n.at.l3out && !n.at.label.includes(n.at.l3out) && <span className="faint"> · L3Out {n.at.l3out}</span>}
      </td>
      <td className="nowrap">
        {n.session ? (n.session === "ebgp" ? "eBGP" : "iBGP") : <Dash />}
        {n.local_as != null && <span className="faint"> · {L(`lokal AS ${n.local_as}`, `local AS ${n.local_as}`)}</span>}
      </td>
      <td className="nowrap">
        {n.configured_only ? (
          <span className="chip na" title={L("nur in der Konfiguration – keine Sitzung gelesen", "only in the configuration – no session read")}>
            {n.shutdown ? L("abgeschaltet", "shut down") : L("nur konfiguriert", "configured only")}
          </span>
        ) : (
          <span className={`chip ${n.established ? "ok" : "warn"}`}>{n.state ?? L("unbekannt", "unknown")}</span>
        )}
      </td>
    </tr>
  );
}
