// Lage eines ACI-Contract-Consumers/-Providers: wo liegt die EPG (Tenant, AP, BD, VRF),
// welche Netze und Endpunkte gehören dazu - Grundlage für die Zuordnung zu Applikationen.
import { useState } from "react";
import { EpgLocation } from "./api";
import { Dash } from "./ui";

const KIND: Record<string, string> = { epg: "EPG", external: "Externe EPG", vzany: "vzAny" };
const ORIGIN: Record<string, string> = { epg: "EPG", bd: "BD", ext: "extern" };
const SHOWN = 8;

export function EpgCard({ epg }: { epg: EpgLocation }) {
  const [all, setAll] = useState(false);
  const eps = all ? epg.endpoints : epg.endpoints.slice(0, SHOWN);
  return (
    <div className="refcard">
      <div className="refcard-head">
        <b className="mono">{epg.name}</b>
        <span className={`chip ${epg.kind === "epg" ? "local" : "info"}`}>{KIND[epg.kind] ?? epg.kind}</span>
        {epg.server && (
          <span className="chip ok" title="verknüpfter Server (Host-Objekt)">
            Server {epg.server.name}
            {epg.server.site ? ` · ${epg.server.site}` : ""}
          </span>
        )}
      </div>
      <dl className="kv tight">
        <dt>ACI-Tenant</dt>
        <dd className="mono">{epg.aci_tenant ?? <Dash />}</dd>
        {epg.kind === "epg" && (
          <>
            <dt>Applikations&shy;profil</dt>
            <dd className="mono">{epg.ap ?? <Dash />}</dd>
            <dt>Bridge Domain</dt>
            <dd className="mono">{epg.bd ?? <Dash />}</dd>
          </>
        )}
        {epg.kind === "external" && (
          <>
            <dt>L3Out</dt>
            <dd className="mono">{epg.l3out ?? <Dash />}</dd>
          </>
        )}
        <dt>VRF</dt>
        <dd className="mono">{epg.vrf ?? <Dash />}</dd>
        <dt>Subnetze</dt>
        <dd>
          {epg.subnets.length ? (
            epg.subnets.map((s) => (
              <div key={`${s.origin}-${s.ip}`}>
                <span className="mono">{s.ip}</span> <span className="faint">({ORIGIN[s.origin] ?? s.origin})</span>
                {s.ipam && (
                  <div className="faint">
                    IPAM {s.ipam.prefix}
                    {[s.ipam.description, s.ipam.vlan != null ? `VLAN ${s.ipam.vlan}` : null, s.ipam.site]
                      .filter(Boolean)
                      .map((x) => ` · ${x}`)
                      .join("")}
                  </div>
                )}
              </div>
            ))
          ) : (
            <Dash />
          )}
        </dd>
        {epg.kind !== "vzany" && (
          <>
            <dt>Endpunkte ({epg.endpoint_count})</dt>
            <dd>
              {eps.length ? (
                <>
                  {eps.map((e) => (
                    <div key={e.ip}>
                      <span className="mono">{e.ip}</span>
                      {e.dns_name ? <span className="muted"> · {e.dns_name}</span> : null}
                    </div>
                  ))}
                  {epg.endpoints.length > SHOWN && (
                    <button className="linkbtn" type="button" onClick={() => setAll((v) => !v)}>
                      {all ? "weniger" : `alle ${epg.endpoints.length} zeigen`}
                    </button>
                  )}
                  {epg.endpoint_count > epg.endpoints.length && (
                    <div className="faint">
                      {epg.endpoint_count - epg.endpoints.length} weitere nicht angezeigt
                    </div>
                  )}
                </>
              ) : (
                <span className="faint">{epg.kind === "external" ? "über die Subnetze bestimmt" : "keine gelernt"}</span>
              )}
            </dd>
          </>
        )}
        {epg.kind === "vzany" && (
          <>
            <dt>Umfang</dt>
            <dd className="faint">alle EPGs der VRF</dd>
          </>
        )}
      </dl>
    </div>
  );
}
