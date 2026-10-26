"""APIC -> Netzinventar (Model 1, read-only, ADR 0010).

Aus dem Tenant-Baum (`rsp-subtree=full`) und Fabric-Klassen des APIC:

  fvCtx                         -> VRF (`<Tenant>:<VRF>`)
  fvBD fvSubnet / fvRsCtx       -> reales Subnetz (Gateway, VRF), EPG-Subnetze ebenso
  fvAEPg fvRsPathAtt (encap)    -> EPG-VLAN + VLAN am Port / Port-Channel / vPC
  fvCEp encap                   -> zusaetzlich gelernte EPG-VLANs (z. B. VMM-Domaenen)
  fabricNode                    -> Geraete (Leaf, Spine, Controller)
  infraNodeP/AccPortP/HPortS    -> Port -> Access Policy Group (PC/vPC-Mitgliedschaft)
  l1PhysIf                      -> Port-Beschreibung, Admin-Status
  lldpAdjEp / cdpAdjEp          -> Gegenstelle am Port (Server/Switch)
  vlanCktEp + l2RsPathDomAtt    -> VLANs, wie sie auf den Leafs ausgerollt sind (EPG ueber
                                   `epgDn`, auch AAEP- und VMM-Bindungen), mit ihren Ports
                                   (seit 0.69.1)
  pcAggrIf + pcRsMbrIfs         -> Port-Channel des Leafs (`po<n>` -> Policy Group, Mitglieder)

Reine Funktion; Ports ohne jede Information (kein Pfad, keine Policy, keine Gegenstelle,
keine Beschreibung) werden weggelassen.
"""

import ipaddress
import re
from collections import defaultdict
from typing import Any

from collectors.cisco_aci.mo import _attrs, _children, _dn, _roots
from collectors.common.dto import (
    NetworkInventory,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedSubnet,
    NormalizedVlan,
)

_PATH = re.compile(
    r"topology/pod-\d+/(?P<kind>paths|protpaths)-(?P<nodes>[\d-]+)"
    r"(?:/extpaths-(?P<fex>\d+))?/pathep-\[(?P<port>.+)\]$"
)
_NODE_PORT = re.compile(r"topology/pod-\d+/node-(?P<node>\d+)/.*?(?:if|phys)-\[(?P<port>[^\]]+)\]")
_NPROF = re.compile(r"uni/infra/nprof-(?P<p>[^/]+)/")
_ACCPORTPROF = re.compile(r"uni/infra/accportprof-(?P<ip>[^/\]]+)")
_HPORTS = re.compile(r"uni/infra/accportprof-(?P<ip>[^/\]]+)/hports-(?P<s>.+)-typ-[^/]+/")
_PG = re.compile(r"uni/infra/funcprof/(?P<kind>accbundle|accportgrp)-(?P<pg>[^/]+)$")
_NODE = re.compile(r"topology/pod-\d+/node-(?P<node>\d+)/")
_PATH_PORT = re.compile(r"/path-\[(?P<port>[^\]]+)\]$")
_MEMBER = re.compile(r"(?:phys-)?\[?(?P<port>eth[\d/]+)\]?$")
_EPG_DN = re.compile(r"uni/tn-(?P<t>[^/]+)/ap-(?P<a>[^/]+)/epg-(?P<e>[^/]+)$")


def _vid(encap: str | None) -> int | None:
    if encap and encap.startswith("vlan-"):
        try:
            return int(encap[len("vlan-") :])
        except ValueError:
            return None
    return None


def _rows(fabric: dict, cls: str) -> list[dict]:
    return [_attrs(r[cls]) for r in fabric.get(cls, []) or [] if cls in r]


def _network(ip: str) -> str | None:
    try:
        return str(ipaddress.ip_interface(ip).network)
    except ValueError:
        return None


class _Tenants:
    """Sammelt VRFs, BDs und EPGs aus dem Tenant-Baum (mit DN-Rekonstruktion)."""

    def __init__(self) -> None:
        self.vrfs: list[str] = []
        self.bds: dict[tuple[str, str], dict] = {}
        self.epgs: list[dict] = []

    def walk(
        self, mo: dict, tenant: str | None = None, ap: str | None = None, parent_dn=""
    ) -> None:
        for cls, body in mo.items():
            attrs = _attrs(body)
            name = attrs.get("name", "")
            kids = _children(body)
            dn = _dn(cls, attrs, parent_dn)
            t, a = tenant, ap
            if cls == "fvTenant":
                t = name
            elif cls == "fvAp":
                a = name
            elif cls == "fvCtx" and t:
                self.vrfs.append(f"{t}:{name}")
            elif cls == "fvBD" and t:
                ctx = next(
                    (_attrs(c["fvRsCtx"]).get("tnFvCtxName") for c in kids if "fvRsCtx" in c), None
                )
                self.bds[(t, name)] = {
                    "vrf": f"{t}:{ctx}" if ctx else None,
                    "subnets": [_attrs(c["fvSubnet"]).get("ip") for c in kids if "fvSubnet" in c],
                    "descr": attrs.get("descr") or None,
                }
            elif cls == "fvAEPg" and t:
                ap_name = a or _ap_from(dn)
                self.epgs.append(
                    {
                        "tenant": t,
                        "ap": ap_name,
                        "name": name,
                        "bd": next(
                            (_attrs(c["fvRsBd"]).get("tnFvBDName") for c in kids if "fvRsBd" in c),
                            None,
                        ),
                        "subnets": [
                            _attrs(c["fvSubnet"]).get("ip") for c in kids if "fvSubnet" in c
                        ],
                        "paths": [
                            (
                                _attrs(c["fvRsPathAtt"]).get("tDn", ""),
                                _vid(_attrs(c["fvRsPathAtt"]).get("encap")),
                                _attrs(c["fvRsPathAtt"]).get("mode") or "regular",
                            )
                            for c in kids
                            if "fvRsPathAtt" in c
                        ],
                        "encaps": [
                            v
                            for c in kids
                            if "fvCEp" in c
                            for v in [_vid(_attrs(c["fvCEp"]).get("encap"))]
                            if v is not None
                        ],
                    }
                )
            for child in kids:
                self.walk(child, t, a, dn)


def _nodes(fabric: dict, cls: str) -> list[tuple[dict, list[dict]]]:
    """Objekte einer Klasse mit ihren Kindern (rsp-subtree=children)."""
    return [(_attrs(r[cls]), _children(r[cls])) for r in fabric.get(cls, []) or [] if cls in r]


def _port_channels(fabric: dict) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """Port-Channels der Leafs (pcAggrIf): (Knoten, po<n>) -> Policy Group bzw. Name, dazu
    (Knoten, Mitglied) -> Policy Group."""
    names: dict[tuple[str, str], str] = {}
    members: dict[tuple[str, str], str] = {}
    for attrs, kids in _nodes(fabric, "pcAggrIf"):
        m = _NODE.match(attrs.get("dn", ""))
        po = attrs.get("id") or ""
        if not m or not po:
            continue
        name = attrs.get("name") or po
        names[(m["node"], po)] = name
        for c in kids:
            if "pcRsMbrIfs" not in c:
                continue
            a = _attrs(c["pcRsMbrIfs"])
            mm = _MEMBER.search(a.get("tSKey") or a.get("tDn") or "")
            if mm:
                members[(m["node"], mm["port"])] = name
    return names, members


def _deployed(fabric: dict, pcs: dict[tuple[str, str], str]) -> list[tuple]:
    """Ausgerollte VLANs (vlanCktEp): (Knoten, Port, Art, VLAN, EPG `Tenant/AP/EPG`)."""
    out = []
    for attrs, kids in _nodes(fabric, "vlanCktEp"):
        m, vid = _NODE.match(attrs.get("dn", "")), _vid(attrs.get("encap"))
        e = _EPG_DN.match(attrs.get("epgDn") or "")
        if not m or vid is None or not e:
            continue
        label = f"{e['t']}/{e['a']}/{e['e']}"
        for c in kids:
            if "l2RsPathDomAtt" not in c:
                continue
            p = _PATH_PORT.search(_attrs(c["l2RsPathDomAtt"]).get("tDn") or "")
            if not p:
                continue
            name, kind = p["port"], "physical"
            if not name.startswith("eth"):
                name, kind = pcs.get((m["node"], name), name), "lag"
            out.append((m["node"], name, kind, vid, label))
    return out


def _ap_from(dn: str) -> str | None:
    for part in dn.split("/"):
        if part.startswith("ap-"):
            return part[3:]
    return None


def _epg_label(e: dict) -> str:
    return "/".join(x for x in (e["tenant"], e["ap"], e["name"]) if x)


def _port_policy_groups(fabric: dict) -> tuple[dict[tuple[str, str], tuple[str, str]], dict]:
    """(node, port) -> (kind, policy group) aus den Access Policies; dazu lagT je Bundle."""
    nodes_by_profile: dict[str, set[str]] = defaultdict(set)
    for r in _rows(fabric, "infraNodeBlk"):
        m = _NPROF.match(r.get("dn", ""))
        if not m:
            continue
        try:
            lo, hi = int(r.get("from_", 0)), int(r.get("to_", r.get("from_", 0)))
        except ValueError:
            continue
        nodes_by_profile[m["p"]].update(str(n) for n in range(lo, hi + 1))
    intprofs_by_profile: dict[str, set[str]] = defaultdict(set)
    for r in _rows(fabric, "infraRsAccPortP"):
        m, t = _NPROF.match(r.get("dn", "")), _ACCPORTPROF.match(r.get("tDn", ""))
        if m and t:
            intprofs_by_profile[m["p"]].add(t["ip"])
    ports_by_selector: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in _rows(fabric, "infraPortBlk"):
        m = _HPORTS.match(r.get("dn", ""))
        if not m:
            continue
        try:
            card, lo, hi = (
                int(r.get("fromCard", 1)),
                int(r["fromPort"]),
                int(r.get("toPort", r["fromPort"])),
            )
        except (KeyError, ValueError):
            continue
        ports_by_selector[(m["ip"], m["s"])] += [f"eth{card}/{p}" for p in range(lo, hi + 1)]
    pg_by_selector: dict[tuple[str, str], tuple[str, str]] = {}
    for r in _rows(fabric, "infraRsAccBaseGrp"):
        m, t = _HPORTS.match(r.get("dn", "")), _PG.match(r.get("tDn", ""))
        if m and t:
            pg_by_selector[(m["ip"], m["s"])] = (t["kind"], t["pg"])
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for profile, intprofs in intprofs_by_profile.items():
        for node in nodes_by_profile.get(profile, set()):
            for (ip, sel), pg in pg_by_selector.items():
                if ip not in intprofs:
                    continue
                for port in ports_by_selector.get((ip, sel), []):
                    out[(node, port)] = pg
    lag_type = {r.get("name"): r.get("lagT") for r in _rows(fabric, "infraAccBndlGrp")}
    return out, lag_type


def parse_inventory(tenant_export: Any, fabric: dict | None) -> NetworkInventory:
    fabric = fabric or {}
    tenants = _Tenants()
    for root in _roots(tenant_export):
        tenants.walk(root)

    # Geraete
    node_names: dict[str, str] = {}
    devices: list[NormalizedDevice] = []
    for r in _rows(fabric, "fabricNode"):
        node_id = str(r.get("id", ""))
        name = r.get("name") or f"node-{node_id}"
        node_names[node_id] = name
        devices.append(
            NormalizedDevice(
                name=name,
                role=r.get("role") or "leaf",
                model=r.get("model") or None,
                serial=r.get("serial") or None,
                node_id=node_id,
                mgmt_ip=r.get("oobMgmtAddr") or r.get("address") or None,
                version=r.get("version") or None,
            )
        )

    def device(node: str) -> str:
        return node_names.get(node) or f"node-{node}"

    # Ports
    ifaces: dict[tuple[str, str], dict] = {}

    def port(node: str, name: str, kind: str = "physical") -> dict:
        key = (device(node), name)
        if key not in ifaces:
            ifaces[key] = {
                "device": key[0],
                "name": name,
                "kind": kind,
                "tagged": set(),
                "untagged": None,
                "epgs": [],
            }
        return ifaces[key]

    vlans: dict[int, dict] = {}
    epg_vids: dict[str, set[int]] = defaultdict(set)
    for e in tenants.epgs:
        label = _epg_label(e)
        for tdn, vid, mode in e["paths"]:
            m = _PATH.match(tdn)
            if not m:
                continue
            nodes = m["nodes"].split("-")
            name = m["port"] if not m["fex"] else f"fex{m['fex']}/{m['port']}"
            kind = "physical" if name.startswith("eth") or m["fex"] else "lag"
            for node in nodes:
                p = port(node, name, kind)
                if label not in p["epgs"]:
                    p["epgs"].append(label)
                if vid is None:
                    continue
                if mode in ("untagged", "native"):
                    p["untagged"] = vid
                else:
                    p["tagged"].add(vid)
            if vid is not None:
                epg_vids[label].add(vid)
        for vid in e["encaps"]:
            epg_vids[label].add(vid)
        for vid in sorted(epg_vids[label]):
            v = vlans.setdefault(vid, {"name": e["name"], "epgs": []})
            if label not in v["epgs"]:
                v["epgs"].append(label)

    # ausgerollte VLANs (0.69.1): auch EPGs ohne statischen Pfad (AAEP, VMM-Domaene)
    pc_names, pc_members = _port_channels(fabric)
    epg_names = {_epg_label(e): e["name"] for e in tenants.epgs}
    for node, pname, kind, vid, label in _deployed(fabric, pc_names):
        p = port(node, pname, kind)
        if label not in p["epgs"]:
            p["epgs"].append(label)
        if p["untagged"] != vid:
            p["tagged"].add(vid)
        epg_vids[label].add(vid)
        v = vlans.setdefault(vid, {"name": epg_names.get(label) or label.rsplit("/", 1)[-1],
                                   "epgs": []})  # fmt: skip
        if label not in v["epgs"]:
            v["epgs"].append(label)

    pgs, lag_type = _port_policy_groups(fabric)
    for (node, pname), (kind, pg) in pgs.items():
        p = port(node, pname)
        if kind == "accbundle":
            p["lag"] = pg
            port(node, pg, "lag")["lag_type"] = lag_type.get(pg)
        else:
            p["policy_group"] = pg
    for (node, pname), pg in pc_members.items():  # Port-Channel des Leafs (Laufzeit)
        p = port(node, pname)
        if not p.get("lag"):
            p["lag"] = pg
        port(node, pg, "lag")
    phys = {}
    for r in _rows(fabric, "l1PhysIf"):
        m = _NODE_PORT.match(r.get("dn", ""))
        if m:
            phys[(m["node"], m["port"])] = r
    for cls, sys_key, port_key in (
        ("lldpAdjEp", "sysName", "portIdV"),
        ("cdpAdjEp", "devId", "portId"),
    ):
        for r in _rows(fabric, cls):
            m = _NODE_PORT.match(r.get("dn", ""))
            if not m:
                continue
            p = port(m["node"], m["port"])
            p.setdefault("neighbor", r.get(sys_key) or None)
            p.setdefault("neighbor_port", r.get(port_key) or None)
    for (node, pname), r in phys.items():
        if r.get("descr") or (device(node), pname) in ifaces:
            p = port(node, pname)
            p["description"] = r.get("descr") or None
            p["enabled"] = (r.get("adminSt") or "up") != "down"

    interfaces = []
    for p in sorted(ifaces.values(), key=lambda x: (x["device"], x["name"])):
        tagged = sorted(p["tagged"])
        interfaces.append(
            NormalizedInterface(
                device=p["device"],
                name=p["name"],
                kind=p["kind"],
                enabled=p.get("enabled"),
                mode="tagged" if tagged else ("access" if p["untagged"] is not None else None),
                untagged_vlan=p["untagged"],
                tagged_vlans=tagged,
                lag=p.get("lag"),
                description=p.get("description")
                or (f"vPC {p['name']}" if p.get("lag_type") == "node" else None),
                neighbor=p.get("neighbor"),
                neighbor_port=p.get("neighbor_port"),
                policy_group=p.get("policy_group"),
                epgs=p["epgs"],
            )
        )

    # Subnetze (BD + EPG), VLAN nur wenn eindeutig
    epgs_by_bd: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in tenants.epgs:
        if e["bd"]:
            epgs_by_bd[(e["tenant"], e["bd"])].append(e)
    subnets: list[NormalizedSubnet] = []
    for (t, bd), info in tenants.bds.items():
        users = epgs_by_bd.get((t, bd), [])
        only = users[0] if len(users) == 1 else None
        vids = epg_vids.get(_epg_label(only), set()) if only else set()
        for ip in info["subnets"]:
            net = _network(ip) if ip else None
            if net:
                subnets.append(
                    NormalizedSubnet(
                        prefix=net,
                        gateway=ip,
                        vrf=info["vrf"],
                        vlan=next(iter(vids)) if len(vids) == 1 else None,
                        context="bd",
                        context_name=bd,
                        aci_tenant=t,
                        ap=only["ap"] if only else None,
                        epg=only["name"] if only else None,
                        bd=bd,
                        description=info["descr"],
                    )
                )
    for e in tenants.epgs:
        bd_info = tenants.bds.get((e["tenant"], e["bd"] or ""), {})
        vids = epg_vids.get(_epg_label(e), set())
        for ip in e["subnets"]:
            net = _network(ip) if ip else None
            if net:
                subnets.append(
                    NormalizedSubnet(
                        prefix=net,
                        gateway=ip,
                        vrf=bd_info.get("vrf"),
                        vlan=next(iter(vids)) if len(vids) == 1 else None,
                        context="epg",
                        context_name=e["name"],
                        aci_tenant=e["tenant"],
                        ap=e["ap"],
                        epg=e["name"],
                        bd=e["bd"],
                    )
                )

    return NetworkInventory(
        devices=devices,
        interfaces=interfaces,
        vlans=[
            NormalizedVlan(vid=vid, name=v["name"], epgs=v["epgs"])
            for vid, v in sorted(vlans.items())
        ],
        subnets=subnets,
        vrfs=sorted(set(tenants.vrfs)),
    )
