"""ACI-Routing (ADR 0019 Stufe 2): L3Outs aus dem Tenant-Baum, Routen der Border-Leafs.

- L3Out (`l3extOut`) mit VRF (`l3extRsEctx`), Border-Leafs (`l3extRsNodeL3OutAtt`: Knoten,
  Router-ID, statische Routen `ipRouteP`/`ipNexthopP`), Interfaces bzw. SVIs
  (`l3extRsPathL3OutAtt`: Pfad, Art, Encap, Adressen inkl. vPC-Seiten `l3extMember`),
  BGP-Peers (`bgpPeerP` mit `bgpAsP`) und externen EPGs - alles schon im gelesenen Baum; dazu
  Route Control (0.113.0): `enforceRtctrl` und die Subnetze mit `export-`/`import-rtctrl`.
- Je Border-Leaf und VRF dazu die Routing-Tabelle (`uribv4Route`/`uribv6Route` mit Next-Hops)
  und die BGP-Nachbarn mit Zustand (`bgpPeer`/`bgpPeerEntry`) - nur lesend.

Positivliste: `bgpPeerP` traegt ein Passwort-Attribut; es wird nie uebernommen.
"""

import re
from typing import Any

from collectors.cisco_aci.mo import _attrs, _children, _roots
from collectors.common.dto import (
    NormalizedL3Out,
    NormalizedNextHop,
    NormalizedPeer,
    NormalizedRoute,
    NormalizedRoutingContext,
    RoutingInventory,
)

_NODE = re.compile(r"pod-(\d+)/node-(\d+)")
_PATH = re.compile(r"pod-(\d+)/(?:paths-(\d+)|protpaths-(\d+)-(\d+))/pathep-\[(.+)\]$")
_VLAN = re.compile(r"vlan-(\d+)")
_NO_IP = {"", "0.0.0.0", "::", "unspecified"}  # noqa: S104 - kein Bind, nur Vergleichswert
_PROTOCOL = {"direct": "connected", "local": "local", "static": "static"}

# Klassen der Border-Leaf-Abfragen (je Knoten und VRF)
RIB_CLASSES = {4: ("uribv4Route", "uribv4Nexthop"), 6: ("uribv6Route", "uribv6Nexthop")}
BGP_CLASSES = ("bgpPeer", "bgpPeerEntry")
ROUTE_LIMIT = 100_000  # Routen + Next-Hops je Abfrage


def _first(kids: list[dict], cls: str, attr: str) -> str | None:
    for c in kids:
        if cls in c:
            value = _attrs(c[cls]).get(attr)
            if value:
                return str(value)
    return None


def _of(kids: list[dict], cls: str) -> list[tuple[dict, list[dict]]]:
    return [(_attrs(c[cls]), _children(c[cls])) for c in kids if cls in c]


def _node_ref(tdn: str) -> dict[str, str] | None:
    m = _NODE.search(tdn or "")
    return {"pod": m.group(1), "node": m.group(2)} if m else None


def path_ref(tdn: str) -> dict[str, Any]:
    """Pfad eines L3Out-Interfaces: Knoten (vPC: beide) und Interface bzw. Policy-Gruppe."""
    m = _PATH.search(tdn or "")
    if not m:
        return {"path": tdn, "nodes": [], "interface": None}
    nodes = [n for n in m.group(2, 3, 4) if n]
    return {"path": tdn, "pod": m.group(1), "nodes": nodes, "interface": m.group(5)}


def _ip(value: Any) -> str | None:
    text = str(value or "").strip()
    host = text.split("/")[0] if text.endswith(("/32", "/128")) else text
    return None if host in _NO_IP else host


def _static_routes(kids: list[dict]) -> list[dict[str, Any]]:
    out = []
    for attrs, rkids in _of(kids, "ipRouteP"):
        hops = [a.get("nhAddr") for a, _ in _of(rkids, "ipNexthopP") if a.get("nhAddr")]
        out.append(
            {
                k: v
                for k, v in {
                    "prefix": attrs.get("ip"),
                    "pref": attrs.get("pref"),
                    "next_hops": [h for h in (_ip(x) for x in hops) if h],
                    "descr": attrs.get("descr") or None,
                }.items()
                if v not in (None, [], "")
            }
        )
    return out


def _peers(kids: list[dict], path: str | None) -> list[dict[str, Any]]:
    out = []
    for attrs, pkids in _of(kids, "bgpPeerP"):
        if not attrs.get("addr"):
            continue
        asn = _first(pkids, "bgpAsP", "asn")
        peer = {"ip": _ip(attrs["addr"]) or attrs["addr"], "asn": int(asn) if asn else None}
        if path:
            peer["path"] = path
        if attrs.get("ttl"):
            peer["ttl"] = attrs["ttl"]
        out.append(peer)
    return out


def _path(attrs: dict, kids: list[dict]) -> dict[str, Any]:
    """Interface bzw. SVI eines L3Outs: Pfad, Art, Encap und Adressen (vPC: je Seite)."""
    vlan = _VLAN.search(attrs.get("encap") or "")
    ips = [{"ip": attrs["addr"]}] if attrs.get("addr") and attrs["addr"] not in _NO_IP else []
    ips += [{"side": a.get("side"), "ip": a["addr"]} for a, _ in _of(kids, "l3extMember")
            if a.get("addr")]  # fmt: skip
    ips += [{"ip": a["addr"], "secondary": True} for a, _ in _of(kids, "l3extIp")
            if a.get("addr")]  # fmt: skip
    path = {
        **path_ref(attrs.get("tDn", "")),
        "kind": attrs.get("ifInstT") or None,  # sub-interface | ext-svi | l3-port
        "encap": attrs.get("encap") or None,
        "vlan": int(vlan.group(1)) if vlan else None,
        "ips": ips,
        "mtu": attrs.get("mtu") or None,
    }
    return {k: v for k, v in path.items() if v not in (None, [])}


def _vrf(tenant: str, name: str | None, vrfs: set[tuple[str, str]]) -> str | None:
    """VRF des L3Outs: im eigenen Tenant, sonst wie der APIC in `common`."""
    if not name:
        return None
    return f"{tenant}:{name}" if (tenant, name) in vrfs or not vrfs else f"common:{name}"


def parse_l3outs(export: Any) -> list[NormalizedL3Out]:
    """L3Outs aller gelesenen Tenants mit Knoten, Interfaces, Peers und statischen Routen."""
    tenants = []
    vrfs: set[tuple[str, str]] = set()
    for root in _roots(export):
        if "fvTenant" not in root:
            continue
        body = root["fvTenant"]
        name = _attrs(body).get("name") or ""
        tenants.append((name, _children(body)))
        vrfs |= {(name, a.get("name")) for a, _ in _of(_children(body), "fvCtx")}
    out = []
    for tenant, kids in tenants:
        for attrs, okids in _of(kids, "l3extOut"):
            name = attrs.get("name") or ""
            l3 = NormalizedL3Out(
                tenant=tenant,
                name=name,
                dn=attrs.get("dn") or f"uni/tn-{tenant}/out-{name}",
                vrf=_vrf(tenant, _first(okids, "l3extRsEctx", "tnFvCtxName"), vrfs),
                ext_epgs=[a.get("name") for a, _ in _of(okids, "l3extInstP") if a.get("name")],
                route_control=_route_control(attrs, okids),
            )
            for _np, nkids in _of(okids, "l3extLNodeP"):
                for att, akids in _of(nkids, "l3extRsNodeL3OutAtt"):
                    ref = _node_ref(att.get("tDn", ""))
                    if ref is None:
                        continue
                    node = {**ref, "router_id": att.get("rtrId") or None}
                    routes = _static_routes(akids)
                    if routes:
                        node["static_routes"] = routes
                    l3.nodes.append({k: v for k, v in node.items() if v is not None})
                l3.peers += _peers(nkids, None)  # Loopback-Peers auf Knotenebene
                for _ifp, ikids in _of(nkids, "l3extLIfP"):
                    for patt, pkids in _of(ikids, "l3extRsPathL3OutAtt"):
                        l3.paths.append(_path(patt, pkids))
                        l3.peers += _peers(pkids, patt.get("tDn"))
            out.append(l3)
    return out


def _route_control(attrs: dict, okids: list[dict]) -> dict[str, Any]:
    """Route Control eines L3Outs (0.113.0, ADR 0041): `enforce` (export, import) und die
    Subnetze der externen EPGs mit `export-rtctrl` (angekuendigt) bzw. `import-rtctrl` (erwartet,
    wirkt nur mit enforce import); `aggregate` = alle Laengen darin."""
    enforce = [x for x in (attrs.get("enforceRtctrl") or "export").split(",") if x]
    sides: dict[str, list[dict[str, Any]]] = {"export": [], "import": []}
    for epg, ikids in _of(okids, "l3extInstP"):
        for sub, _ in _of(ikids, "l3extSubnet"):
            ip, scope, agg = sub.get("ip"), sub.get("scope") or "", sub.get("aggregate") or ""
            for side in sides:
                if ip and f"{side}-rtctrl" in scope:
                    entry = {"prefix": ip, "epg": epg.get("name")}
                    if f"{side}-rtctrl" in agg:
                        entry["aggregate"] = True
                    sides[side].append(entry)
    return {"enforce": enforce, **{k: v for k, v in sides.items() if v}}


def border_contexts(l3outs: list[NormalizedL3Out]) -> list[tuple[str, str, str]]:
    """(Pod, Knoten, Tenant:VRF) der Border-Leafs aller L3Outs - einmal je Knoten und VRF."""
    seen: dict[tuple[str, str, str], None] = {}
    for l3 in l3outs:
        if not l3.vrf:
            continue
        for n in l3.nodes:
            seen.setdefault((n["pod"], n["node"], l3.vrf), None)
        for p in l3.paths:
            for node in p.get("nodes", []):
                seen.setdefault((p.get("pod", "1"), node, l3.vrf), None)
    return list(seen)


def _protocol(owner: str | None) -> str | None:
    if not owner:
        return None
    base = owner.split("-")[0].lower()
    return _PROTOCOL.get(base, base)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def rib(mos: list[dict], family: int, node: str, vrf: str) -> list[NormalizedRoute]:
    """Routen eines Leafs in einer VRF; Next-Hops haengen per DN an ihrer Route."""
    route_cls, hop_cls = RIB_CLASSES[family]
    routes: dict[str, NormalizedRoute] = {}
    hops: list[dict] = []
    for mo in mos:
        if route_cls in mo:
            a = _attrs(mo[route_cls])
            if a.get("dn") and a.get("prefix"):
                routes[a["dn"]] = NormalizedRoute(
                    node=node, context=vrf, prefix=a["prefix"], table="rib"
                )
        elif hop_cls in mo:
            hops.append(_attrs(mo[hop_cls]))
    for h in hops:
        dn = str(h.get("dn") or "")
        route = routes.get(dn.rsplit("/nh-", 1)[0]) if "/nh-" in dn else None
        if route is None:
            continue
        if route.protocol is None:
            route.protocol = _protocol(h.get("owner"))
            route.distance = _int(h.get("pref"))
            route.metric = _int(h.get("metric"))
            route.attrs = {
                k: v
                for k, v in {"owner": h.get("owner"), "route_type": h.get("routeType")}.items()
                if v
            }
        iface = h.get("if")
        hop = NormalizedNextHop(ip=_ip(h.get("addr")), interface=None if iface in _NO_IP else iface)
        if hop not in route.next_hops:
            route.next_hops.append(hop)
    return list(routes.values())


def bgp_peers(mos: list[dict], node: str, vrf: str) -> list[NormalizedPeer]:
    peers: dict[str, dict] = {}
    out = []
    for mo in mos:
        if "bgpPeer" in mo:
            a = _attrs(mo["bgpPeer"])
            peers[str(a.get("dn") or "")] = a
    for mo in mos:
        if "bgpPeerEntry" not in mo:
            continue
        e = _attrs(mo["bgpPeerEntry"])
        parent = peers.get(str(e.get("dn") or "").rsplit("/ent-", 1)[0], {})
        ip = _ip(e.get("addr")) or _ip(parent.get("addr"))
        if not ip:
            continue
        out.append(
            NormalizedPeer(
                node=node,
                context=vrf,
                peer_ip=ip,
                local_ip=_ip(e.get("localIp")),
                remote_as=_int(parent.get("asn")),
                state=e.get("operSt") or None,
                attrs={k: v for k, v in {"type": e.get("type")}.items() if v},
            )
        )
    return out


def node_names(fabric: dict | None) -> dict[str, str]:
    """Knoten-ID -> Name (fabricNode), fuer die Anzeige der Border-Leafs."""
    out = {}
    for row in (fabric or {}).get("fabricNode", []):
        a = _attrs(row.get("fabricNode", {}))
        if a.get("id"):
            out[str(a["id"])] = a.get("name") or f"node-{a['id']}"
    return out


def normalize(export: Any, fabric: dict | None, border: dict | None) -> RoutingInventory:
    """L3Outs aus dem Baum, dazu Routen und BGP-Nachbarn der Border-Leafs (`border` je
    (Pod, Knoten, VRF): {rib4, rib6, bgp, missing}) - auch leer (Stand ersetzen)."""
    names = node_names(fabric)
    inv = RoutingInventory(l3outs=parse_l3outs(export))
    for (_pod, node_id, vrf), data in (border or {}).items():
        node = names.get(node_id, f"node-{node_id}")
        inv.contexts.append(
            NormalizedRoutingContext(
                node=node, context=vrf, kind="vrf", missing=data.get("missing", [])
            )
        )
        inv.routes += rib(data.get("rib4") or [], 4, node, vrf)
        inv.routes += rib(data.get("rib6") or [], 6, node, vrf)
        inv.peers += bgp_peers(data.get("bgp") or [], node, vrf)
    return inv
