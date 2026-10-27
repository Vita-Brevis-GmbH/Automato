// Lage eines ACI-Contract-Consumers/-Providers: wo liegt die EPG (Tenant, AP, BD, VRF),
// welche Netze und Endpunkte gehören dazu, an welchen Leaf-Ports/VLANs hängt sie -
// Grundlage für die Zuordnung zu Applikationen.
import { useState } from "react";
import { EpgLocation } from "./api";
import { Dash } from "./ui";
import { L } from "./i18n";

const KIND: Record<string, string> = {
  epg: "EPG",
  esg: "ESG",
  external: L("Externe EPG", "External EPG"),
  vzany: "vzAny",
};
const ORIGIN: Record<string, string> = {
  epg: "EPG",
  bd: "BD",
  ext: L("extern", "external"),
  esg: L("IP-Selektor", "IP selector"),
};
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
          <span
            className="chip warn scope-unpaired"
            title={L(
              "Der Scope des Contracts verbindet diese EPG mit keiner Gegenstelle (anderes AP, andere VRF bzw. anderer Tenant) – für sie ohne Wirkung",
              "The scope of the contract connects this EPG with no peer (different AP, VRF or tenant) – no effect for it",
            )}
          >
            {L("ohne Gegenstelle im Scope", "no peer in scope")}
          </span>
        )}
        {epg.pref_group && (
          <span
            className="chip info"
            title={L("Mitglied der Preferred Group seiner VRF", "Member of the Preferred Group of its VRF")}
          >
            Preferred Group
          </span>
        )}
        {epg.server && (
          <span className="chip ok" title={L("verknüpfter Server (Host-Objekt)", "linked server (host object)")}>
            Server {epg.server.name}
            {epg.server.site ? ` · ${epg.server.site}` : ""}
          </span>
        )}
      </div>
      <dl className="kv tight">
        <dt>{L("ACI-Tenant", "ACI tenant")}</dt>
        <dd className="mono">{epg.aci_tenant ?? <Dash />}</dd>
        {epg.kind === "esg" && (
          <>
            <dt>{L(<>Applikations&shy;profil</>, <>Application profile</>)}</dt>
            <dd className="mono">{epg.ap ?? <Dash />}</dd>
            <dt>{L("Selektoren", "Selectors")}</dt>
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
            <dt>{L(<>Applikations&shy;profil</>, <>Application profile</>)}</dt>
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
        <dt>{L("Subnetze", "Subnets")}</dt>
        <dd>
          {epg.subnets.length ? (
            epg.subnets.map((s) => (
              <div key={`${s.origin}-${s.ip}`}>
                <span className="mono">{s.ip}</span> <span className="faint">({ORIGIN[s.origin] ?? s.origin})</span>
                {s.origin === "ext" && routingOnly(s.scope) && (
                  <span className="chip" title={L(`Scope ${s.scope} – ordnet keinen Verkehr der EPG zu`, `Scope ${s.scope} – assigns no traffic to the EPG`)}>
                    {L("nur Routing", "routing only")}
                  </span>
                )}
                {s.origin !== "ext" && (s.scope ?? "").split(",").map((x) => x.trim()).includes("shared") && (
                  <span className="chip local" title={L(
                    "zwischen VRFs geteilt (Shared Services) – das ACI leakt das Subnetz in die VRF der Contract-Partner",
                    "shared between VRFs (shared services) – the ACI leaks the subnet into the VRF of the contract partners",
                  )}>
                    {L("geteilt", "shared")}
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
            <dt>{L("Endpunkte", "Endpoints")} ({epg.endpoint_count})</dt>
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
                      {all
                        ? L("weniger", "fewer")
                        : L(`alle ${epg.endpoints.length} zeigen`, `show all ${epg.endpoints.length}`)}
                    </button>
                  )}
                  {epg.endpoint_count > epg.endpoints.length && (
                    <div className="faint">
                      {L(
                        `${epg.endpoint_count - epg.endpoints.length} weitere nicht angezeigt`,
                        `${epg.endpoint_count - epg.endpoints.length} more not shown`,
                      )}
                    </div>
                  )}
                </>
              ) : (
                <span className="faint">
                  {epg.kind === "external"
                    ? L("über die Subnetze bestimmt", "determined by the subnets")
                    : epg.kind === "esg"
                      ? L("über die Selektoren bestimmt", "determined by the selectors")
                      : L("keine gelernt", "none learned")}
                </span>
              )}
            </dd>
          </>
        )}
        {epg.kind === "epg" && (epg.vlans.length > 0 || epg.port_count > 0) && (
          <>
            <dt>VLAN</dt>
            <dd className="mono">{epg.vlans.length ? epg.vlans.join(", ") : <Dash />}</dd>
            <dt>{L("Leaf-Ports", "Leaf ports")} ({epg.port_count})</dt>
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
                <div className="faint">
                  {L(
                    `${epg.port_count - epg.ports.length} weitere nicht angezeigt`,
                    `${epg.port_count - epg.ports.length} more not shown`,
                  )}
                </div>
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
                  {v.power_state === "poweredOff" && <span className="chip na">{L("aus", "off")}</span>}
                  <span
                    className="faint"
                    title={L(`Portgruppe ${v.portgroup ?? ""}`, `Port group ${v.portgroup ?? ""}`)}
                  >
                    {" "}
                    · {v.match === "vlan" ? L("über VLAN", "via VLAN") : L("Portgruppe", "Port group")}
                  </span>
                </div>
              ))}
            </dd>
          </>
        )}
        {epg.kind === "vzany" && (
          <>
            <dt>{L("Umfang", "Scope")}</dt>
            <dd className="faint">{L("alle EPGs der VRF", "all EPGs of the VRF")}</dd>
          </>
        )}
      </dl>
    </div>
  );
}
