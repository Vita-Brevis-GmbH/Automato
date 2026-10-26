# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Parallele Firewalls (0.72.0): Policy-Routen, Routing und NAT im Verbund vergleichen.

fw-a und fw-b bilden den Verbund „Cluster Nord“ (fuehrend fw-a). Policy-Route 1 ist gleich,
2 hat auf fw-b ein anderes Gateway, 3 fehlt auf fw-b; die SD-WAN-Regel „web“ nimmt auf fw-b
die Mitglieder in anderer Reihenfolge. Routing in root: 10.50.0.0/16 auf fw-a ueber BGP, auf
fw-b statisch; 10.60.0.0/24 nur auf fw-a; die Default-Route hat je Firewall einen eigenen
Next-Hop (zaehlt nicht); die statische Route 10.99.0.0/16 hat andere Distanzen. Der VDOM
kunde-a fehlt auf fw-b; im VDOM dmz ist die Routing-Tabelle von fw-b nicht lesbar (kein
Vergleich). NAT: VIP_web gleich, VIP_db mit anderer interner IP, IPP_out nur auf fw-b.
"""

from test_parallel import ADMIN, _deliver


def _r(node, ctx, prefix, hops, protocol="static", table="rib", **kw):
    return {
        "node": node,
        "context": ctx,
        "prefix": prefix,
        "protocol": protocol,
        "table": table,
        "next_hops": hops,
        **kw,
    }


def _pbr(node, seq, gateway, device="wan1", kind="policy", **kw):
    return {
        "node": node,
        "context": "root",
        "kind": kind,
        "seq": seq,
        "gateway": gateway,
        "output_device": [device] if isinstance(device, str) else device,
        "match": {"src": ["10.20.0.0/24"], "dst": ["192.0.2.0/24"]},
        **kw,
    }


def _vip(name, mapped, **kw):
    return {
        "source_platform": "fortigate",
        "external_id": name,
        "name": name,
        "vdom": "root",
        "nat_type": "dnat_vip",
        "ext_ip": "198.51.100.20",
        "mapped_ip": mapped,
        "family": "v4",
        "raw": {"mappedip": [{"range": mapped}], "extintf": "wan1"},
        **kw,
    }


def _fw(node: str, b: bool) -> dict:
    hop = "198.51.100.5" if b else "198.51.100.1"
    routes = [
        _r(node, "root", "10.20.0.0/24", [{"interface": "srv"}], "connected"),
        _r(node, "root", "0.0.0.0/0", [{"ip": hop, "interface": "wan1"}], "bgp"),
        _r(
            node,
            "root",
            "10.50.0.0/16",
            [{"ip": "10.255.0.2", "interface": "to-aci"}],
            "static" if b else "bgp",
        ),
        _r(
            node,
            "root",
            "10.99.0.0/16",
            [{"ip": "10.255.0.2", "interface": "to-aci"}],
            table="static",
            distance=20 if b else 10,
        ),
        _r(node, "dmz", "10.30.0.0/24", [{"interface": "dmz"}], "connected"),
    ]
    if not b:
        routes += [
            _r(node, "root", "10.60.0.0/24", [{"ip": "10.70.0.2", "interface": "to-fw02"}]),
            _r(node, "kunde-a", "0.0.0.0/0", [{"ip": "10.255.1.1", "interface": "vl-kunde"}]),
        ]
    contexts = [
        {"node": node, "context": "root"},
        {
            "node": node,
            "context": "dmz",
            "missing": ["Routing-Tabelle IPv4 (HTTP 403)"] if b else [],
        },
    ]
    if not b:
        contexts.append({"node": node, "context": "kunde-a"})
    pbr = [_pbr(node, 1, "198.51.100.1"), _pbr(node, 2, "198.51.100.9" if b else "198.51.100.1")]
    if not b:
        pbr.append(_pbr(node, 3, "198.51.100.1"))
    pbr.append(
        _pbr(
            node,
            1,
            None,
            ["wan2", "wan1"] if b else ["wan1", "wan2"],
            kind="sdwan",
            name="web",
            action="priority",
        )
    )
    nat = [_vip("VIP_web", "10.20.0.10"), _vip("VIP_db", "10.20.0.12" if b else "10.20.0.11")]
    if b:
        nat.append(
            {
                "source_platform": "fortigate",
                "external_id": "IPP_out",
                "name": "IPP_out",
                "vdom": "root",
                "nat_type": "snat_pool",
                "ext_ip": "198.51.100.30",
                "family": "v4",
                "attrs": {"type": "overload"},
            }
        )
    return {
        "status": "completed",
        "inventory": {"devices": [{"name": node, "role": "firewall"}]},
        "routing": {"contexts": contexts, "routes": routes, "policy_routes": pbr},
        "nat_rules": nat,
    }


def _seed(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = []
    for name in ("fw-a", "fw-b"):
        body = {"tenant_id": tid, "name": name, "platform": "fortigate", "base_url": "https://x"}
        sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
        _deliver(client, tid, sid, _fw(name, name == "fw-b"))
        ids.append(sid)
    return tid, ids[0], ids[1]


def _findings(client, tid) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in client.get("/findings", params={"tenant_id": tid, "limit": 500}).json():
        out.setdefault(f["type"], []).append(f)
    return out


def test_policy_routen_routing_und_nat_im_verbund(client):
    tid, fwa, fwb = _seed(client)
    types = {"parallel_policy_route_mismatch", "parallel_route_mismatch", "parallel_nat_mismatch"}
    assert not types & set(_findings(client, tid))  # ohne Verbund kein Vergleich

    body = {"tenant_id": tid, "group": "Cluster Nord", "system_ids": [fwa, fwb]}
    assert client.post("/systems/parallel-group", json=body, headers=ADMIN).status_code == 200
    found = _findings(client, tid)

    # Policy-Routen: Gateway weicht ab, Policy-Route 3 fehlt, SD-WAN in anderer Reihenfolge
    pbr = {
        (f["source_system_id"], f["detail"]["label"]): f
        for f in found["parallel_policy_route_mismatch"]
    }
    assert set(pbr) == {
        (fwb, "Policy-Route 2"),
        (fwa, "Policy-Route 3"),
        (fwb, "SD-WAN-Regel 1 „web“"),
    }
    gw = pbr[(fwb, "Policy-Route 2")]
    assert gw["detail"]["fields"] == ["Gateway"] and gw["subject_kind"] == "node"
    assert gw["subject_id"] == f"{fwb}|root|policy|2"
    assert "198.51.100.9 ≠ 198.51.100.1" in gw["message"]
    assert "fehlt auf fw-b" in pbr[(fwa, "Policy-Route 3")]["message"]
    sd = pbr[(fwb, "SD-WAN-Regel 1 „web“")]
    assert sd["detail"]["values"]["Ausgang"] == ["wan2 → wan1", "wan1 → wan2"]

    # Routing: ein Befund je Firewall und VDOM mit den Praefixen
    routes = {f["subject_id"]: f for f in found["parallel_route_mismatch"]}
    assert set(routes) == {f"{fwb}|root", f"{fwb}|kunde-a"}  # dmz: nicht lesbar
    root = routes[f"{fwb}|root"]["detail"]
    got = {(e["table"], e["prefix"], e["kind"]) for e in root["entries"]}
    assert got == {
        ("rib", "10.50.0.0/16", "differs"),
        ("rib", "10.60.0.0/24", "only_lead"),
        ("static", "10.99.0.0/16", "differs"),
    }
    differs = next(e for e in root["entries"] if e["prefix"] == "10.50.0.0/16")
    assert differs["fields"] == ["Protokoll"] and differs["lead"].startswith("bgp · to-aci")
    assert root["other"] == "fw-a" and root["count"] == 3
    msg = routes[f"{fwb}|root"]["message"]
    assert "VDOM root" in msg and "10.60.0.0/24" in msg and "fw-a" in msg
    vdom = routes[f"{fwb}|kunde-a"]["detail"]["entries"]
    assert vdom == [{"table": "vdom", "prefix": "kunde-a", "kind": "only_lead"}]

    # NAT: VIP mit anderer interner IP, IP-Pool nur auf fw-b
    nat = {(f["source_system_id"], f["detail"]["name"]): f for f in found["parallel_nat_mismatch"]}
    assert set(nat) == {(fwb, "VIP_db"), (fwb, "IPP_out")}
    db = nat[(fwb, "VIP_db")]
    assert db["subject_kind"] == "nat" and set(db["detail"]["fields"]) == {
        "Interne IP",
        "Interne Ziele",
    }
    assert "10.20.0.12 ≠ 10.20.0.11" in db["message"]
    assert "fehlt auf fw-a" in nat[(fwb, "IPP_out")]["message"]

    # Uebersicht je Verbund
    (g,) = client.get("/systems/parallel", params={"tenant_id": tid}).json()
    assert g["group"] == "Cluster Nord" and [m["name"] for m in g["members"]] == ["fw-a", "fw-b"]
    assert g["stats"]["policy_routes"] == {"same": 1, "differs": 2, "missing": 1}
    assert g["stats"]["nat"] == {"same": 1, "differs": 1, "missing": 1}
    assert g["stats"]["routes"] == {"same": 2, "differs": 2, "missing": 2}
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert (
        client.get("/systems/parallel", params={"tenant_id": tid}, headers=viewer).status_code
        == 200
    )
    customer = {"X-Dev-Roles": "customer", "X-Dev-Customers": "1", "X-Dev-Tenants": str(tid)}
    assert (
        client.get("/systems/parallel", params={"tenant_id": tid}, headers=customer).status_code
        == 403
    )

    # neuer Lauf mit angeglichenem Gateway: der Befund entfaellt, die anderen bleiben
    fixed = _fw("fw-b", True)
    fixed["routing"]["policy_routes"][1]["gateway"] = "198.51.100.1"
    _deliver(client, tid, fwb, fixed)
    left = _findings(client, tid)["parallel_policy_route_mismatch"]
    labels = {f["detail"]["label"] for f in left}
    assert labels == {"Policy-Route 3", "SD-WAN-Regel 1 „web“"}

    # Verbund aufloesen: keine Befunde mehr
    body = {"tenant_id": tid, "group": "Cluster Nord", "system_ids": []}
    client.post("/systems/parallel-group", json=body, headers=ADMIN)
    assert not types & set(_findings(client, tid))
    assert client.get("/systems/parallel", params={"tenant_id": tid}).json() == []
