// Lage eines ACI-Contract-Consumers/-Providers: wo liegt die EPG (Tenant, AP, BD, VRF),
// welche Netze und Endpunkte gehören dazu, an welchen Leaf-Ports/VLANs hängt sie -
// Grundlage für die Zuordnung zu Applikationen.
import { useState } from "react";
import { EpgLocation } from "./api";
import { Dash } from "./ui";

const KIND: Record<string, string> = { epg: "EPG", esg: "ESG", external: "Externe EPG", vzany: "vzAny" };
const ORIGIN: Record<string, string> = { epg: "EPG", bd: "BD", ext: "extern", esg: "IP-Selektor" };
const SHOWN = 8;

// Subnetz einer externen EPG ohne import-security ordnet keinen Verkehr zu (nur Routing)
const routingOnly = (scope?: string | null) =>
  scope != null && !scope.split(",").map((x) => x.trim()).includes("import-security");

export function EpgCard({ epg, unpaired }: { epg: EpgLocation; unpaired?: boolean }) {
  const [all, setAll] = useState(false);
  const eps = all ? epg.endpoints : epg.endpoints.slice(0, SHOWN);
  return (
    <div className="refcard">
      <div className="refcard-head">
        <b className="mono">{epg.name}</b>
        <span className={`chip ${epg.kind === "epg" ? "local" : "info"}`}>{KIND[epg.kind] ?? epg.kind}</span>
        {unpaired && (
          <span className="chip warn scope-unpaired" title="Der Scope des Contracts verbindet diese EPG mit keiner Gegenstelle (anderes AP, andere VRF bzw. anderer Tenant) – für sie ohne Wirkung">
            ohne Gegenstelle im Scope
          </span>
        )}
        {epg.pref_group && (
          <span className="chip info" title="Mitglied der Preferred Group seiner VRF">
            Preferred Group
          </span>
        )}
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
        {epg.kind === "esg" && (
          <>
            <dt>Applikations&shy;profil</dt>
            <dd className="mono">{epg.ap ?? <Dash />}</dd>
            <dt>Selektoren</dt>
            <dd>
              {epg.selectors.length ? (
                epg.selectors.map((x) => (
                  <div key={x} className="mono">
                    {x}
                  </div>
                ))
              ) : (
                <Dash />
              )}
            </dd>
          </>
        )}
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
                {s.origin === "ext" && routingOnly(s.scope) && (
                  <span className="chip" title={`Scope ${s.scope} – ordnet keinen Verkehr der EPG zu`}>
                    nur Routing
                  </span>
                )}
                {s.origin !== "ext" && (s.scope ?? "").split(",").map((x) => x.trim()).includes("shared") && (
                  <span className="chip local" title="zwischen VRFs geteilt (Shared Services) – das ACI leakt das Subnetz in die VRF der Contract-Partner">
                    geteilt
                  </span>
                )}
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
                <span className="faint">
                  {epg.kind === "external"
                    ? "über die Subnetze bestimmt"
                    : epg.kind === "esg"
                      ? "über die Selektoren bestimmt"
                      : "keine gelernt"}
                </span>
              )}
            </dd>
          </>
        )}
        {epg.kind === "epg" && (epg.vlans.length > 0 || epg.port_count > 0) && (
          <>
            <dt>VLAN</dt>
            <dd className="mono">{epg.vlans.length ? epg.vlans.join(", ") : <Dash />}</dd>
            <dt>Leaf-Ports ({epg.port_count})</dt>
            <dd>
              {epg.ports.map((p) => (
                <div key={`${p.device}-${p.name}`}>
                  <span className="mono">
                    {p.device} {p.name}
                  </span>
                  {p.lag && <span className="faint"> · {p.lag}</span>}
                  {p.mode && <span className="faint"> · {p.mode === "tagged" ? "trunk" : "access"}</span>}
                  {p.neighbor && (
                    <span className="muted">
                      {" "}
                      → {p.neighbor}
                      {p.neighbor_port ? ` ${p.neighbor_port}` : ""}
                    </span>
                  )}
                </div>
              ))}
              {epg.port_count > epg.ports.length && (
                <div className="faint">{epg.port_count - epg.ports.length} weitere nicht angezeigt</div>
              )}
            </dd>
          </>
        )}
        {(epg.vms?.length ?? 0) > 0 && (
          <>
            <dt>VMs ({epg.vms!.length})</dt>
            <dd>
              {epg.vms!.map((v, i) => (
                <div key={`${v.name}-${i}`}>
                  <span className="mono">{v.name}</span>
                  {v.ips.length > 0 && <span className="faint"> · {v.ips.join(", ")}</span>}
                  {v.power_state === "poweredOff" && <span className="chip na">aus</span>}
                  <span className="faint" title={`Portgruppe ${v.portgroup ?? ""}`}>
                    {" "}
                    · {v.match === "vlan" ? "über VLAN" : "Portgruppe"}
                  </span>
                </div>
              ))}
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
