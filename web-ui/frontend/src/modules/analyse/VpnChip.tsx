// IPsec-Tunnel als Schritt des Wegs (1.19.0, ADR 0063): Chip mit Name, Zustand und Gegenstelle – in Weg-Suche,
// „Warum dieser Weg?“ und den Verbindungen der Applikation. Ein Klick öffnet den Tunnel in „VPN-Tunnel“; seit 1.23.0
// ist die gelesene Gegenstelle ein Link auf ihren Tunnel (gekoppelt automatisch bzw. von Hand).
import type { TraceVpn } from "../../api";
import { L } from "../../i18n";
import { hashOf } from "../../places";

const STATE: Record<string, string> = { up: "ok", partial: "warn", down: "crit" };

export function vpnText(v: Pick<TraceVpn, "name" | "kind" | "remote_gw" | "instance" | "peer" | "status">): string {
  const what = v.kind === "dialup" ? L("Dial-up-VPN", "dial-up VPN") : "VPN";
  const inst = v.instance ? ` (${v.instance})` : "";
  const gw = v.remote_gw ? ` → ${v.remote_gw}` : "";
  const peer = v.peer?.label ? ` (${v.peer.label}${v.peer.tunnel ? ` · ${v.peer.tunnel}` : ""})` : "";
  return `${what} ${v.name}${inst}${gw}${peer}`;
}

export function VpnChip({ v, sid, context }: { v: Pick<TraceVpn, "name" | "kind" | "remote_gw" | "instance" | "peer" | "status" | "covered">; sid?: number; context?: string }) {
  const covered =
    v.covered === "none"
      ? L(" – Ziel in keinem Phase-2-Selektor", " – destination in no phase 2 selector")
      : v.covered === "partial"
        ? L(" – Ziel nur teilweise in den Selektoren", " – destination only partly in the selectors")
        : "";
  const title = `${vpnText(v)} · ${v.status ?? L("Zustand unbekannt", "state unknown")}${
    v.peer ? "" : L(" – Gegenstelle nicht gelesen, der Weg endet hier", " – peer not read, the path ends here")
  }${covered}`;
  const ref = sid != null && context ? `${sid}|${context}|${v.name}` : null;
  const chip = (
    <span className={`chip vpn ${STATE[v.status ?? ""] ?? ""}${v.covered === "none" ? " crit" : ""}`} data-vpn={v.name} title={title}>
      VPN {v.name}
      {v.status && v.status !== "up" ? ` · ${v.status}` : ""}
    </span>
  );
  return (
    <span className="trace-vpn" style={{ marginLeft: 4 }}>
      {ref ? (
        <a href={`#vpn?${new URLSearchParams({ tunnel: ref }).toString()}`} target="_blank" rel="noopener noreferrer">
          {chip}
        </a>
      ) : (
        chip
      )}
      {v.remote_gw && <span className="mono faint"> → {v.remote_gw}</span>}
      {v.peer?.label && <PeerLink peer={v.peer} />}
    </span>
  );
}

// Gegenstelle des Tunnels: die andere FortiGate mit ihrem Tunnel als Link in „VPN-Tunnel“ (1.23.0)
function PeerLink({ peer }: { peer: NonNullable<TraceVpn["peer"]> }) {
  const how =
    peer.pair === "manual"
      ? L(" · von Hand gekoppelt", " · coupled manually")
      : peer.pair === "auto"
        ? L(" · automatisch gekoppelt", " · coupled automatically")
        : peer.open
          ? L(" · Tunnel dort nicht eindeutig", " · tunnel there not unique")
          : "";
  const text = `${peer.label}${peer.tunnel ? ` · ${peer.tunnel}` : ""}`;
  if (!peer.tunnel)
    return (
      <span className="faint vpn-peer-link">
        {" "}
        ({text}
        {how})
      </span>
    );
  const ref = `${peer.source_system_id}|${peer.context}|${peer.tunnel}`;
  return (
    <span className="faint vpn-peer-link">
      {" "}
      (
      <a href={hashOf("vpn", { tunnel: ref })} target="_blank" rel="noopener noreferrer" title={L("Tunnel der Gegenstelle öffnen", "Open the tunnel of the peer")}>
        {text}
      </a>
      {how})
    </span>
  );
}
