"""FortiGate-Routing (ADR 0019 Stufe 2): Rohdaten je VDOM -> RoutingInventory.

Gelesen wird nur lesend:
- die wirksame Routing-Tabelle (Monitor `router/ipv4`/`ipv6`, ECMP = mehrere Next-Hops),
- alle BGP-Pfade (`router/bgp/paths`/`paths6`, auch nicht beste = Backup-Wege) und die
  BGP-Nachbarn mit Zustand,
- statische Routen (`router/static`/`static6`, auch inaktive bzw. mit hoeherer Distanz),
- Policy-Routen (`router/policy`/`policy6`) und SD-WAN-Regeln (`system/sdwan`),
- HA (`system/ha`, `system/ha-peer`) und Session-Sync zu anderen Clustern (FGSP:
  `system/standalone-cluster` bzw. `system/cluster-sync`).

Felder werden nur ueber eine Positivliste uebernommen: Passwoerter, Pre-Shared-Keys und
Schluessel (z. B. `system/ha` `password`, FGSP `psksecret`) kommen nie mit.
"""

import ipaddress
from typing import Any

from collectors.common.dto import (
    NormalizedHa,
    NormalizedNextHop,
    NormalizedPeer,
    NormalizedPolicyRoute,
    NormalizedRoute,
    NormalizedRoutingContext,
    RoutingInventory,
)

# Rohtabellen je VDOM -> Bezeichnung in der Lauf-Meldung, wenn das Leserecht fehlt
TABLE_LABELS = {
    "rib4": "Routing-Tabelle IPv4",
    "rib6": "Routing-Tabelle IPv6",
    "bgp_paths4": "BGP-Pfade IPv4",
    "bgp_paths6": "BGP-Pfade IPv6",
    "bgp_neighbors4": "BGP-Nachbarn IPv4",
    "bgp_neighbors6": "BGP-Nachbarn IPv6",
    "static4": "statische Routen IPv4",
    "static6": "statische Routen IPv6",
    "policy4": "Policy-Routen IPv4",
    "policy6": "Policy-Routen IPv6",
    "sdwan": "SD-WAN",
}

_PROTOCOL = {"connect": "connected", "connected": "connected"}
_NO_GATEWAY = {"", "0.0.0.0", "::"}  # noqa: S104 - kein Bind, nur Vergleichswert


def prefix(value: Any) -> str | None:
    """CIDR aus "10.0.0.0/24", "10.0.0.0/255.255.255.0" oder "10.0.0.0 255.255.255.0"."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", "/", 1) if " " in value.strip() else value.strip()
    try:
        return str(ipaddress.ip_network(text, strict=False))
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _gateway(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if text in _NO_GATEWAY else text


def _names(entries: Any) -> list[str]:
    """[{"name": "port1"}, ...] bzw. ["port1"] -> Namen."""
    out = []
    for e in entries or []:
        name = e.get("name") if isinstance(e, dict) else e
        if name not in (None, ""):
            out.append(str(name))
    return out


def _subnets(entries: Any) -> list[str]:
    """Policy-Route `src`/`dst`: [{"subnet": "10.0.0.0 255.0.0.0"}] bzw. Strings -> CIDR."""
    out = []
    for e in entries or []:
        raw = e.get("subnet") or e.get("addr6") if isinstance(e, dict) else e
        net = prefix(raw)
        if net:
            out.append(net)
    return out


def _keep(row: dict, keys: tuple[str, ...]) -> dict[str, Any]:
    """Nur diese Felder, ohne leere Werte (Positivliste)."""
    return {k: row[k] for k in keys if row.get(k) not in (None, "", [], {})}


def _ports(row: dict) -> list[int]:
    """Ziel-Ports einer Policy-Route bzw. SD-WAN-Regel; 1-65535 (Standard) = alle -> []."""
    ports = [_int(row.get("start-port")), _int(row.get("end-port"))]
    if None in ports or ports == [1, 65535] or ports == [0, 0]:
        return []
    return ports


def rib(rows: list[dict], node: str, vdom: str) -> list[NormalizedRoute]:
    """Wirksame Routing-Tabelle: Eintraege je Praefix, Protokoll und Distanz zusammengefasst -
    mehrere Gateways sind ECMP."""
    grouped: dict[tuple, NormalizedRoute] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        net = prefix(r.get("ip_mask") or r.get("ip"))
        if net is None:
            continue
        proto = str(r.get("type") or "").lower() or None
        proto = _PROTOCOL.get(proto or "", proto)
        key = (net, proto, _int(r.get("distance")))
        route = grouped.get(key)
        if route is None:
            route = grouped[key] = NormalizedRoute(
                node=node,
                context=vdom,
                prefix=net,
                table="rib",
                protocol=proto,
                distance=_int(r.get("distance")),
                metric=_int(r.get("metric")),
                attrs=_keep(r, ("priority", "is_tunnel_route", "tunnel_parent")),
            )
        hop = NormalizedNextHop(ip=_gateway(r.get("gateway")), interface=r.get("interface"))
        if hop not in route.next_hops:
            route.next_hops.append(hop)
    return list(grouped.values())


def bgp_paths(rows: list[dict], node: str, vdom: str) -> list[NormalizedRoute]:
    """Alle BGP-Pfade (auch nicht beste) je Praefix und Nachbar."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        net = prefix(f"{r.get('nlri_prefix')}/{r.get('nlri_prefix_len')}")
        if net is None:
            continue
        attrs = _keep(r, ("as_path", "local_pref", "origin", "learned_from", "weight", "path_id"))
        out.append(
            NormalizedRoute(
                node=node,
                context=vdom,
                prefix=net,
                table="bgp",
                protocol="bgp",
                best=bool(r.get("is_best")),
                metric=_int(r.get("med")),
                next_hops=[NormalizedNextHop(ip=_gateway(r.get("next_hop")))],
                attrs=attrs,
            )
        )
    return out


def static(rows: list[dict], node: str, vdom: str) -> list[NormalizedRoute]:
    """Konfigurierte statische Routen, auch inaktive bzw. mit hoeherer Distanz (Backup)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        net = prefix(r.get("dst")) or ("0.0.0.0/0" if r.get("dst") is None else None)  # noqa: S104
        if net is None:
            continue
        attrs = _keep(r, ("seq-num", "priority", "weight", "comment", "dstaddr", "sdwan-zone"))
        attrs.update({k: True for k in ("blackhole", "dynamic-gateway") if r.get(k) == "enable"})
        out.append(
            NormalizedRoute(
                node=node,
                context=vdom,
                prefix=net,
                table="static",
                protocol="static",
                best=r.get("status", "enable") != "disable",
                distance=_int(r.get("distance")),
                next_hops=[
                    NormalizedNextHop(
                        ip=_gateway(r.get("gateway")), interface=r.get("device") or None
                    )
                ],
                attrs={k: v for k, v in attrs.items() if k != "dstaddr" or v},
            )
        )
    return out


def peers(rows: list[dict], node: str, vdom: str) -> list[NormalizedPeer]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("neighbor_ip"):
            continue
        out.append(
            NormalizedPeer(
                node=node,
                context=vdom,
                peer_ip=str(r["neighbor_ip"]),
                local_ip=r.get("local_ip") or None,
                remote_as=_int(r.get("remote_as")),
                state=r.get("state") or None,
                attrs=_keep(r, ("admin_status", "type")),
            )
        )
    return out


def policy_routes(rows: list[dict], node: str, vdom: str) -> list[NormalizedPolicyRoute]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        match = {
            "input_device": _names(r.get("input-device")),
            "src": _subnets(r.get("src")),
            "srcaddr": _names(r.get("srcaddr")),
            "dst": _subnets(r.get("dst")),
            "dstaddr": _names(r.get("dstaddr")),
            "protocol": r.get("protocol"),
            "ports": _ports(r),
            "service": _names(r.get("internet-service-id") or r.get("internet-service-custom")),
        }
        out.append(
            NormalizedPolicyRoute(
                node=node,
                context=vdom,
                kind="policy",
                seq=_int(r.get("seq-num")),
                enabled=r.get("status", "enable") != "disable",
                match={k: v for k, v in match.items() if v not in (None, [], "", 0)},
                action=r.get("action") or "permit",
                gateway=_gateway(r.get("gateway")),
                output_device=[r["output-device"]] if r.get("output-device") else [],
                attrs=_keep(r, ("comments", "tos", "tos-mask")),
            )
        )
    return out


def sdwan(cfg: Any, node: str, vdom: str) -> list[NormalizedPolicyRoute]:
    """SD-WAN-Regeln mit ihren Mitgliedern in Vorrang-Reihenfolge."""
    if not isinstance(cfg, dict) or cfg.get("status") == "disable":
        return []
    members = {_int(m.get("seq-num")): m for m in cfg.get("members") or [] if isinstance(m, dict)}
    member_list = [
        _keep(m, ("seq-num", "interface", "gateway", "zone", "priority", "cost", "status"))
        for m in members.values()
    ]
    out = []
    for s in cfg.get("service") or []:
        if not isinstance(s, dict):
            continue
        order = [members.get(_int(p.get("seq-num"))) for p in s.get("priority-members") or []]
        devices = [str(m["interface"]) for m in order if m and m.get("interface")]
        match = {
            "input_device": _names(s.get("input-device")),
            "src": _names(s.get("src")),
            "dst": _names(s.get("dst")),
            "protocol": s.get("protocol"),
            "ports": _ports(s),
            "service": _names(s.get("internet-service-name") or s.get("internet-service-custom")),
        }
        out.append(
            NormalizedPolicyRoute(
                node=node,
                context=vdom,
                kind="sdwan",
                seq=_int(s.get("id")),
                name=s.get("name") or None,
                enabled=s.get("status", "enable") != "disable",
                match={k: v for k, v in match.items() if v not in (None, [], "", 0)},
                action=s.get("mode") or None,  # auto | manual | priority | sla | load-balance
                output_device=devices,
                attrs={
                    "zones": _names(s.get("priority-zone")),
                    "members": member_list,
                },
            )
        )
    return out


def ha(cfg: Any, ha_peers: list[dict] | None, cluster: Any, node: str) -> NormalizedHa | None:
    """HA-Cluster (Modus, Gruppe, Mitglieder mit Rolle) und Session-Sync (FGSP)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    mode = cfg.get("mode") or None
    members = []
    for p in ha_peers or []:
        if not isinstance(p, dict):
            continue
        primary = p.get("primary", p.get("master"))
        members.append(
            {
                k: v
                for k, v in {
                    "hostname": p.get("hostname"),
                    "serial": p.get("serial_no") or p.get("serial"),
                    "priority": _int(p.get("priority")),
                    "vcluster": _int(p.get("vcluster_id")),
                    "role": None if primary is None else ("primary" if primary else "secondary"),
                }.items()
                if v is not None
            }
        )
    sync = _sync_peers(cluster)
    if not members and not sync and mode in (None, "standalone"):
        return None
    return NormalizedHa(
        node=node,
        mode=mode,
        group_name=cfg.get("group-name") or None,
        group_id=_int(cfg.get("group-id")),
        members=members,
        session_pickup=(cfg.get("session-pickup") == "enable")
        if cfg.get("session-pickup")
        else None,
        sync_peers=sync,
        attrs=_keep(
            cfg,
            (
                "override",
                "priority",
                "session-pickup-connectionless",
                "session-pickup-nat",
                "session-pickup-expectation",
                "vcluster-status",
                "ha-mgmt-status",
            ),
        ),  # fmt: skip
    )


def _sync_peers(cluster: Any) -> list[dict[str, Any]]:
    """FGSP: `system/standalone-cluster` (cluster-peer) bzw. `system/cluster-sync` (Liste)."""
    if isinstance(cluster, dict):
        entries = cluster.get("cluster-peer") or []
        group = _keep(cluster, ("standalone-group-id", "group-member-id", "layer2-connection"))
    else:
        entries, group = cluster or [], {}
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        out.append(
            {
                **group,
                **_keep(e, ("sync-id", "peervd", "peerip", "ipsec-tunnel-sync")),
                "syncvd": _names(e.get("syncvd")),
                "down_intfs": _names(e.get("down-intfs-before-sess-sync")),
            }
        )
    return out


def normalize(vdoms: list[dict], global_ha: dict | None, node: str) -> RoutingInventory:
    """Routing aller VDOMs (`vdoms[i]["routing"]` = Rohtabellen + `missing`) und HA - auch leer,
    damit der Orchestrator den Stand des Quellsystems ersetzt."""
    inv = RoutingInventory()
    for v in vdoms:
        raw = v.get("routing")
        if raw is None:
            continue
        vdom = str(v.get("vdom") or "root")
        inv.contexts.append(
            NormalizedRoutingContext(node=node, context=vdom, missing=raw.get("missing", []))
        )
        inv.routes += rib(raw.get("rib4"), node, vdom) + rib(raw.get("rib6"), node, vdom)
        inv.routes += bgp_paths(raw.get("bgp_paths4"), node, vdom)
        inv.routes += bgp_paths(raw.get("bgp_paths6"), node, vdom)
        inv.routes += static(raw.get("static4"), node, vdom) + static(
            raw.get("static6"), node, vdom
        )
        inv.peers += peers(raw.get("bgp_neighbors4"), node, vdom)
        inv.peers += peers(raw.get("bgp_neighbors6"), node, vdom)
        inv.policy_routes += policy_routes(raw.get("policy4"), node, vdom)
        inv.policy_routes += policy_routes(raw.get("policy6"), node, vdom)
        inv.policy_routes += sdwan(raw.get("sdwan"), node, vdom)
    if global_ha:
        cluster = ha(global_ha.get("ha"), global_ha.get("peers"), global_ha.get("sync"), node)
        if cluster is not None:
            inv.ha.append(cluster)
    return inv
