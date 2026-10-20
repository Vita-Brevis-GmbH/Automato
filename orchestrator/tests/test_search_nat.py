"""Regelsuche findet alles (Gruppenmitglieder, IP im Netz, Kommentar, NAT), exakte Filter
object/nat, NAT angereichert (DNS aus der DDI, Server, Regeln) und durchsuchbar (Dev-Befund)."""

from test_reference_sources import TOKEN, _ddi, _run, _system, _tenant

VIP = "V-EXT_www.example.ch"
POOL = "IPP_inet01"

OBJECTS = [
    {"name": "N_dmz-net", "category": "address", "obj_type": "ipmask", "values": ["10.10.20.0/24"]},
    {"name": "H_web01", "category": "address", "obj_type": "ipmask", "values": ["10.10.10.5"]},
    {
        "name": "N_server-net",
        "category": "address",
        "obj_type": "ipmask",
        "values": ["10.10.10.0/24"],
    },
    {
        "name": "G_web",
        "category": "address_group",
        "members": ["H_web01", "G_inner"],
        "values": ["10.10.10.5"],
    },
    {"name": "G_inner", "category": "address_group", "members": ["N_server-net"]},
    {"name": VIP, "category": "vip", "values": ["203.0.113.10"]},
    {"name": POOL, "category": "ippool", "values": ["198.51.100.10-198.51.100.12"]},
    {"name": "HTTPS", "category": "service", "values": ["tcp/443"]},
]


def _rule(ext_id, name, src_refs, dst_refs, raw=None, **kw):
    return {
        "source_platform": "fortigate",
        "external_id": ext_id,
        "name": name,
        "action": "allow",
        "sources": kw.get("sources", ["10.10.20.0/24"]),
        "destinations": kw.get("destinations", ["10.10.10.5"]),
        "services": ["tcp/443"],
        "src_zone": kw.get("src_zone"),
        "dst_zone": kw.get("dst_zone"),
        "source_refs": src_refs,
        "destination_refs": dst_refs,
        "service_refs": ["HTTPS"],
        "raw": raw or {},
    }


RULES = [
    _rule(
        "1",
        "dmz-to-web",
        ["N_dmz-net"],
        ["G_web"],
        {"comments": "Webshop Frontend"},
        src_zone="dmz",
        dst_zone="server",
    ),
    _rule(
        "2",
        "web-to-internet",
        ["H_web01"],
        ["all"],
        {"nat": "enable", "ippool": "enable", "poolname": [{"name": POOL}]},
        sources=["10.10.10.5"],
        destinations=["all"],
    ),
    _rule(
        "3",
        "internet-to-www",
        ["all"],
        [VIP],
        sources=["all"],
        destinations=[f"vip:{VIP}=203.0.113.10"],
    ),
    _rule(
        "4",
        "server-out-iface",
        ["H_web01"],
        ["all"],
        {"nat": "enable"},
        sources=["10.10.10.5"],
        destinations=["all"],
    ),
]

NAT = [
    {
        "source_platform": "fortigate",
        "external_id": VIP,
        "name": VIP,
        "ext_ip": "203.0.113.10",
        "ext_port": "443",
        "mapped_ip": "10.10.10.5",
        "mapped_port": "8443",
        "protocol": "tcp",
        "portforward": True,
        "raw": {"extintf": "wan1", "comment": "Webshop extern", "type": "static-nat"},
    },
    {
        "source_platform": "fortigate",
        "external_id": POOL,
        "name": POOL,
        "nat_type": "snat_pool",
        "ext_ip": "198.51.100.10-198.51.100.12",
        "raw": {"type": "overload", "startip": "198.51.100.10", "endip": "198.51.100.12"},
    },
]

DDI = [
    {"kind": "prefix", "value": "10.10.10.0/24", "vlan_vid": 110, "site": "RZ1"},
    {"kind": "host", "value": "10.10.10.5", "dns_name": "web01.example.ch"},
    {"kind": "a_record", "value": "203.0.113.10", "dns_name": "www.example.ch"},
]


def _seed(client) -> int:
    tid = _tenant(client)
    ddi = _ddi(client, tid)
    _run(client, tid, ddi["id"], "infoblox", DDI)
    fw = _system(client, tid, name="fw", platform="fortigate", base_url="https://fw")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    result = {"status": "completed", "rules": RULES, "objects": OBJECTS, "nat_rules": NAT}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.json()["status"] == "completed", r.text
    return tid


def _names(client, tid, **params) -> list[str]:
    r = client.get("/rules", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return sorted(x["name"] for x in r.json())


def test_suche_findet_alles(client):
    tid = _seed(client)
    # Mitglied einer verschachtelten Gruppe (G_web -> G_inner -> N_server-net)
    assert _names(client, tid, q="N_server-net") == ["dmz-to-web"]
    assert "dmz-to-web" in _names(client, tid, q="h_web01")  # gross/klein egal
    # IP, die nur in einem Netz der Regel liegt; 'all' trifft nicht jede IP
    assert _names(client, tid, q="10.10.10.77") == ["dmz-to-web"]
    assert _names(client, tid, q="Webshop") == ["dmz-to-web"]  # Kommentar
    assert _names(client, tid, q="dmz https") == ["dmz-to-web"]  # alle Woerter, Zone + Service
    assert _names(client, tid, q="dmz internet") == []
    # NAT: SNAT-Pool per Name und per IP aus seinem Bereich, VIP per externer IP
    assert _names(client, tid, q=POOL) == ["web-to-internet"]
    assert _names(client, tid, q="198.51.100.11") == ["web-to-internet"]
    assert _names(client, tid, q="203.0.113.10") == ["internet-to-www"]
    r = client.get("/rules", params={"tenant_id": tid, "q": "10.10.10.5"})
    assert r.headers["X-Total-Count"] == "4"  # direkt, ueber die Gruppe und als VIP-Ziel


def test_filter_objekt_nat_zone(client):
    tid = _seed(client)
    assert _names(client, tid, object="N_server-net") == ["dmz-to-web"]  # ueber zwei Gruppen
    assert _names(client, tid, object="H_web01") == [
        "dmz-to-web",
        "server-out-iface",
        "web-to-internet",
    ]
    assert _names(client, tid, nat=POOL) == ["web-to-internet"]
    assert _names(client, tid, nat=VIP) == ["internet-to-www"]
    assert _names(client, tid, src_zone="dmz") == ["dmz-to-web"]
    assert _names(client, tid, dst_zone="server", action="allow") == ["dmz-to-web"]

    rows = {x["name"]: x for x in client.get("/rules", params={"tenant_id": tid}).json()}
    assert rows["web-to-internet"]["nat"] == {"snat": True, "pools": [POOL], "vips": []}
    assert rows["server-out-iface"]["nat"] == {"snat": True, "pools": [], "vips": []}
    assert rows["internet-to-www"]["nat"] == {"snat": False, "pools": [], "vips": [VIP]}
    assert rows["dmz-to-web"]["nat"] is None

    summary = client.get("/rules/summary", params={"tenant_id": tid}).json()
    assert ("dmz", 1) in [tuple(x) for x in summary["by_src_zone"]]
    assert summary["by_action"] == {"allow": 4}

    detail = client.get(f"/rules/{rows['internet-to-www']['id']}").json()
    (entry,) = detail["nat_entries"]
    assert entry["name"] == VIP and entry["mapped_dns"] == ["web01.example.ch"]
    assert entry["ext_dns"] == ["www.example.ch"]
    assert client.get(f"/rules/{rows['dmz-to-web']['id']}").json()["comment"] == "Webshop Frontend"


def test_nat_angereichert_durchsuchbar_mit_regeln(client):
    tid = _seed(client)
    rows = {x["name"]: x for x in client.get("/nat", params={"tenant_id": tid}).json()}
    vip = rows[VIP]
    assert vip["external_name"] == "www.example.ch" and vip["interface"] == "wan1"
    assert vip["ext_dns"] == ["www.example.ch"] and vip["mapped_dns"] == ["web01.example.ch"]
    assert vip["server"] == "H_web01" and vip["rule_count"] == 1
    assert vip["comment"] == "Webshop extern"
    assert rows[POOL]["rule_count"] == 1

    def hits(q):
        r = client.get("/nat", params={"tenant_id": tid, "q": q})
        return sorted(x["name"] for x in r.json())

    assert hits("web01.example.ch") == [VIP]  # DNS-Name der internen IP aus der DDI
    assert hits("198.51.100.12") == [POOL]  # IP im Pool-Bereich
    assert hits("8443") == [VIP]
    assert hits("wan1 webshop") == [VIP]
    assert hits("nichts-da") == []

    detail = client.get(f"/nat/{vip['id']}").json()
    assert [r["name"] for r in detail["rules"]] == ["internet-to-www"]
    (mapped,) = detail["mapped_info"]
    assert mapped["dns_names"] == ["web01.example.ch"] and mapped["prefix"]["vlan"] == 110
    assert detail["settings"]["Typ"] == "static-nat"
    pool = client.get(f"/nat/{rows[POOL]['id']}").json()
    assert [r["name"] for r in pool["rules"]] == ["web-to-internet"]
    assert pool["settings"]["Start-IP"] == "198.51.100.10"
    assert client.get("/nat/999999").status_code == 404
