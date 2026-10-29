"""FortiGate-Routing (ADR 0019 Stufe 2): Rohdaten je VDOM -> RoutingInventory.

Gelesen wird nur lesend:
- die wirksame Routing-Tabelle (Monitor `router/ipv4`/`ipv6`, ECMP = mehrere Next-Hops),
- alle BGP-Pfade (`router/bgp/paths`/`paths6`, auch nicht beste = Backup-Wege) und die
  BGP-Nachbarn mit Zustand,
- statische Routen (`router/static`/`static6`, auch inaktive bzw. mit hoeherer Distanz),
- Policy-Routen (`router/policy`/`policy6`) und SD-WAN-Regeln (`system/sdwan`),
- HA (`system/ha`, `system/ha-peer`) und Session-Sync zu anderen Clustern (FGSP:
  `system/standalone-cluster` bzw. `system/cluster-sync`),
- die BGP-Konfiguration (`router/bgp`: AS, Nachbarn bzw. Nachbar-Gruppen mit ihren Filtern ein
  und aus, network, aggregate, redistribute) und die Filter (`router/prefix-list`/`6`,
  `router/route-map`, `router/access-list`/`6`) - 0.113.0, ADR 0041.

Felder werden nur ueber eine Positivliste uebernommen: Passwoerter, Pre-Shared-Keys und
Schluessel (z. B. `system/ha` `password`, FGSP `psksecret`) kommen nie mit.

VRF (FortiOS 7.x, Dev-Befund 0.17.0): Routen gelten je VRF des VDOMs - eine Default-Route in
der Management-VRF ist kein Weg fuer den Datenverkehr. Jede Route traegt ihre VRF (`attrs.vrf`,
aus der Route bzw. dem Interface; fehlt = 0); Routen ueber ein dediziertes
Management-Interface (`dedicated-to management`) tragen `attrs.mgmt`.
"""

import ipaddress
from typing import Any

from collectors.common.dto import (
    NormalizedBgpObject,
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
    "bgp_config": "BGP-Konfiguration",
    "prefix_lists4": "Prefix-Listen IPv4",
    "prefix_lists6": "Prefix-Listen IPv6",
    "route_maps": "Route-Maps",
    "access_lists4": "Access-Listen IPv4",
    "access_lists6": "Access-Listen IPv6",
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


Ports = dict[str, dict[str, Any]]  # Interface -> {"vrf": int, "mgmt": bool}


def ports(interfaces: list[dict] | None) -> Ports:
    """VRF und Management-Kennzeichen je Interface (cmdb system/interface)."""
    out: Ports = {}
    for i in interfaces or []:
        if isinstance(i, dict) and i.get("name"):
            out[str(i["name"])] = {
                "vrf": _int(i.get("vrf")) or 0,
                "mgmt": i.get("dedicated-to") == "management",
            }
    return out


def _table(row: dict, interface: Any, ifaces: Ports | None) -> dict[str, Any]:
    """VRF (aus der Route, sonst dem Interface) und Management-Kennzeichen einer Route."""
    port = (ifaces or {}).get(str(interface or ""), {})
    vrf = _int(row.get("vrf"))
    vrf = port.get("vrf", 0) if vrf is None else vrf
    out: dict[str, Any] = {"vrf": vrf} if vrf else {}
    if port.get("mgmt"):
        out["mgmt"] = True
    return out


def rib(
    rows: list[dict], node: str, vdom: str, ifaces: Ports | None = None
) -> list[NormalizedRoute]:
    """Wirksame Routing-Tabelle: Eintraege je Praefix, Protokoll, Distanz und VRF
    zusammengefasst - mehrere Gateways sind ECMP."""
    grouped: dict[tuple, NormalizedRoute] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        net = prefix(r.get("ip_mask") or r.get("ip"))
        if net is None:
            continue
        proto = str(r.get("type") or "").lower() or None
        proto = _PROTOCOL.get(proto or "", proto)
        table = _table(r, r.get("interface"), ifaces)
        key = (net, proto, _int(r.get("distance")), table.get("vrf", 0), bool(table.get("mgmt")))
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
                attrs={**_keep(r, ("priority", "is_tunnel_route", "tunnel_parent")), **table},
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
        attrs.update(_table(r, None, None))
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


def static(
    rows: list[dict], node: str, vdom: str, ifaces: Ports | None = None
) -> list[NormalizedRoute]:
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
        attrs.update(_table(r, r.get("device"), ifaces))
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


def normalize(
    vdoms: list[dict], global_ha: dict | None, node: str, interfaces: list[dict] | None = None
) -> RoutingInventory:
    """Routing aller VDOMs (`vdoms[i]["routing"]` = Rohtabellen + `missing`) und HA - auch leer,
    damit der Orchestrator den Stand des Quellsystems ersetzt. `interfaces` (cmdb) liefern VRF
    und Management-Kennzeichen der Routen."""
    inv = RoutingInventory()
    ifaces = ports(interfaces)
    for v in vdoms:
        raw = v.get("routing")
        if raw is None:
            continue
        vdom = str(v.get("vdom") or "root")
        inv.contexts.append(
            NormalizedRoutingContext(node=node, context=vdom, missing=raw.get("missing", []))
        )
        inv.routes += rib(raw.get("rib4"), node, vdom, ifaces)
        inv.routes += rib(raw.get("rib6"), node, vdom, ifaces)
        inv.routes += bgp_paths(raw.get("bgp_paths4"), node, vdom)
        inv.routes += bgp_paths(raw.get("bgp_paths6"), node, vdom)
        inv.routes += static(raw.get("static4"), node, vdom, ifaces)
        inv.routes += static(raw.get("static6"), node, vdom, ifaces)
        inv.peers += peers(raw.get("bgp_neighbors4"), node, vdom)
        inv.peers += peers(raw.get("bgp_neighbors6"), node, vdom)
        inv.policy_routes += policy_routes(raw.get("policy4"), node, vdom)
        inv.policy_routes += policy_routes(raw.get("policy6"), node, vdom)
        inv.policy_routes += sdwan(raw.get("sdwan"), node, vdom)
        process, neighbors = bgp_config(raw.get("bgp_config"), node, vdom)
        if process is not None:
            inv.bgp.append(process)
        inv.bgp += bgp_filters(raw, node, vdom)
        _attach_config(inv.peers, neighbors, node, vdom)
    if global_ha:
        cluster = ha(global_ha.get("ha"), global_ha.get("peers"), global_ha.get("sync"), node)
        if cluster is not None:
            inv.ha.append(cluster)
    return inv


# -- BGP-Konfiguration und Filter (0.113.0, ADR 0041) ------------------------------------------

# Filter eines Nachbarn je Richtung: Feld der FortiGate -> (Richtung, Art)
_NEIGHBOR_FILTERS = {
    f"{kind}-{way}{six}": (f"{way}{six}", kind.replace("-", "_"))
    for kind in ("route-map", "prefix-list", "distribute-list", "filter-list")
    for way in ("in", "out")
    for six in ("", "6")
}
# Positivliste je Nachbar (nie `password`, `auth-*`)
_NEIGHBOR_KEEP = (
    "description", "update-source", "interface", "ebgp-enforce-multihop", "next-hop-self",
    "soft-reconfiguration", "remove-private-as", "allowas-in-enable", "activate", "activate6",
    "route-reflector-client", "default-originate-routemap",
)  # fmt: skip
# Bedingungen und Aenderungen einer Route-Map-Regel (Positivliste)
_MATCH = ("match-ip-address", "match-ip6-address", "match-ip-nexthop", "match-ip6-nexthop",
          "match-as-path", "match-community", "match-extcommunity", "match-interface",
          "match-metric", "match-origin", "match-route-type", "match-tag", "match-vrf")  # fmt: skip
_SET = ("set-local-preference", "set-metric", "set-weight", "set-origin", "set-tag",
        "set-ip-nexthop", "set-ip6-nexthop", "set-aspath", "set-community", "set-route-tag",
        "set-community-additive", "set-metric-type")  # fmt: skip


def _flat(value: Any) -> Any:
    """Listen der FortiGate ([{"as": "65000"}], [{"community": "65000:1"}]) -> Werte."""
    if isinstance(value, list):
        return [next(iter(v.values()), None) if isinstance(v, dict) else v for v in value]
    return value


def _neighbor(row: dict) -> dict[str, Any]:
    filters: dict[str, dict[str, str]] = {}
    for field, (way, kind) in _NEIGHBOR_FILTERS.items():
        if row.get(field):
            filters.setdefault(way, {})[kind] = str(row[field])
    out: dict[str, Any] = {"filters": filters, **_keep(row, _NEIGHBOR_KEEP)}
    if row.get("remote-as"):
        out["remote_as"] = str(row["remote-as"])
    if row.get("shutdown") == "enable":
        out["shutdown"] = True
    return out


def _single(cfg: Any) -> dict:
    """`router/bgp` ist eine Einzeltabelle: dict (bzw. Liste mit einem Eintrag)."""
    if isinstance(cfg, list):
        cfg = cfg[0] if cfg else {}
    return cfg if isinstance(cfg, dict) else {}


def bgp_config(cfg: Any, node: str, vdom: str) -> tuple[NormalizedBgpObject | None, dict]:
    """BGP-Prozess (AS, Router-ID, network, aggregate, redistribute) und die Konfiguration je
    Nachbar (IP -> Filter ein/aus); Nachbar-Gruppen und -Bereiche (dynamische Nachbarn) unter
    `groups`/`ranges`."""
    cfg = _single(cfg)
    if not cfg or not (cfg.get("as") or cfg.get("neighbor")):
        return None, {}
    neighbors = {str(n["ip"]): _neighbor(n) for n in cfg.get("neighbor") or []
                 if isinstance(n, dict) and n.get("ip")}  # fmt: skip
    groups = {str(g["name"]): _neighbor(g) for g in cfg.get("neighbor-group") or []
              if isinstance(g, dict) and g.get("name")}  # fmt: skip
    rows = [*(cfg.get("neighbor-range") or []), *(cfg.get("neighbor-range6") or [])]
    ranges = [
        {"prefix": p, "group": str(r.get("neighbor-group") or "")}
        for r in rows
        if isinstance(r, dict) and (p := prefix(r.get("prefix") or r.get("prefix6")))
    ]
    networks = [{"prefix": p, **({"route_map": str(n["route-map"])} if n.get("route-map") else {})}
                for key, field in (("network", "prefix"), ("network6", "prefix6"))
                for n in cfg.get(key) or []
                if isinstance(n, dict) and (p := prefix(n.get(field)))]  # fmt: skip
    aggregates = [{"prefix": p, "summary_only": a.get("summary-only") == "enable"}
                  for key, field in (("aggregate-address", "prefix"),
                                     ("aggregate-address6", "prefix6"))
                  for a in cfg.get(key) or []
                  if isinstance(a, dict) and (p := prefix(a.get(field)))]  # fmt: skip
    redistribute = [{"name": str(r["name"]),
                     **({"route_map": str(r["route-map"])} if r.get("route-map") else {})}
                    for key in ("redistribute", "redistribute6")
                    for r in cfg.get(key) or []
                    if isinstance(r, dict) and r.get("name")
                    and r.get("status") == "enable"]  # fmt: skip
    data = {
        "as": str(cfg["as"]) if cfg.get("as") else None,
        "router_id": cfg.get("router-id") or None,
        "networks": networks,
        "aggregates": aggregates,
        "redistribute": redistribute,
        "neighbors": neighbors,
        "groups": groups,
        "ranges": ranges,
    }
    data = {k: v for k, v in data.items() if v not in (None, [], {})}
    process = NormalizedBgpObject(node=node, context=vdom, kind="bgp", name="bgp", data=data)
    return process, {"neighbors": neighbors, "groups": groups, "ranges": ranges}


def _rule_prefix(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().lower() in ("any", ""):
        return "any" if value.strip() else None
    return prefix(value)


def _length(value: Any) -> int | None:
    n = _int(value)
    return n if n else None  # 0 = nicht gesetzt


def bgp_filters(raw: dict, node: str, vdom: str) -> list[NormalizedBgpObject]:
    """Prefix-Listen, Access-Listen und Route-Maps mit ihren Regeln (Reihenfolge nach ID)."""
    out: list[NormalizedBgpObject] = []
    for key, family, field in (("prefix_lists4", 4, "prefix"), ("prefix_lists6", 6, "prefix6")):
        for pl in raw.get(key) or []:
            if not isinstance(pl, dict) or not pl.get("name"):
                continue
            rules = [{"id": _int(r.get("id")), "action": r.get("action") or "permit",
                      "prefix": _rule_prefix(r.get(field)), "ge": _length(r.get("ge")),
                      "le": _length(r.get("le"))}
                     for r in pl.get("rule") or [] if isinstance(r, dict)]  # fmt: skip
            out.append(_object(node, vdom, "prefix_list", pl, rules, family))
    for key, family, field in (("access_lists4", 4, "prefix"), ("access_lists6", 6, "prefix6")):
        for acl in raw.get(key) or []:
            if not isinstance(acl, dict) or not acl.get("name"):
                continue
            rules = [{"id": _int(r.get("id")), "action": r.get("action") or "permit",
                      "prefix": _rule_prefix(r.get(field)),
                      "exact": r.get("exact-match") == "enable",
                      **({"wildcard": r["wildcard"]} if r.get("wildcard") else {})}
                     for r in acl.get("rule") or [] if isinstance(r, dict)]  # fmt: skip
            out.append(_object(node, vdom, "access_list", acl, rules, family))
    for rm in raw.get("route_maps") or []:
        if not isinstance(rm, dict) or not rm.get("name"):
            continue
        rules = []
        for r in rm.get("rule") or []:
            if not isinstance(r, dict):
                continue
            match = {k[6:]: _flat(r[k]) for k in _MATCH if r.get(k) not in (None, "", [], 0)}
            sets = {k[4:]: _flat(r[k]) for k in _SET if r.get(k) not in (None, "", [], 0)}
            rules.append({"id": _int(r.get("id")), "action": r.get("action") or "permit",
                          "match": match, "set": sets})  # fmt: skip
        out.append(_object(node, vdom, "route_map", rm, rules))
    return out


def _object(node: str, vdom: str, kind: str, row: dict, rules: list[dict],
            family: int | None = None) -> NormalizedBgpObject:  # fmt: skip
    data = {"rules": sorted(rules, key=lambda r: r["id"] or 0), **_keep(row, ("comments",))}
    return NormalizedBgpObject(node=node, context=vdom, kind=kind, name=str(row["name"]),
                               family=family, data=data)  # fmt: skip


def _in_range(ip: str, ranges: list[dict]) -> str | None:
    """Nachbar-Gruppe eines dynamischen Nachbarn (neighbor-range)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for r in ranges:
        net = ipaddress.ip_network(r["prefix"])
        if addr.version == net.version and addr in net and r.get("group"):
            return r["group"]
    return None


def _attach_config(peers: list[NormalizedPeer], cfg: dict, node: str, vdom: str) -> None:
    """Konfiguration je Nachbar an die gelesenen Nachbarn (Monitor) haengen. Konfigurierte ohne
    Eintrag im Monitor (abgeschaltet, nie verbunden) stehen nur im BGP-Prozess (`neighbors`) -
    keine Nachbarn ohne Zustand (Verlauf, Anbindungen und Befunde bleiben wie bisher)."""
    if not cfg:
        return
    neighbors, groups, ranges = cfg["neighbors"], cfg["groups"], cfg["ranges"]
    for p in peers:
        if p.node != node or p.context != vdom:
            continue
        conf = neighbors.get(p.peer_ip)
        if conf is None and (group := _in_range(p.peer_ip, ranges)) in groups:
            conf = {**groups[group], "group": group}
        if conf is not None:
            p.attrs["config"] = conf
