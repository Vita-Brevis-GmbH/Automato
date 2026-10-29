# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""BGP-Filter Soll/Ist (0.113.0, ADR 0041): Filter je Nachbar auswerten, erwartet/erhalten/
angekuendigt, Befunde, ACI Route Control und der Vorschlag fuer „Netze dahinter“.

Eine FortiGate (AS 65001) mit drei Nachbarn: ISP (Route-Map ein sperrt 10.0.0.0/8, Prefix-Liste
aus nur 10.20.0.0/16 bis /24), Standort (Prefix-Liste ein erlaubt zwei Netze, eines kommt nicht)
und ein eBGP-Partner ohne Eingangsfilter; dazu ein abgeschalteter Nachbar ohne Sitzung. Ein APIC
mit einem L3Out mit erzwungenem Import und einem ohne.
"""

from app.store import bgp_filters

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _bgp(prefix, frm, best=True):
    return {"node": "fw-01", "context": "root", "prefix": prefix, "table": "bgp",
            "protocol": "bgp", "best": best, "next_hops": [{"ip": frm}],
            "attrs": {"learned_from": frm}}  # fmt: skip


def _obj(kind, name, rules, family=4):
    family = None if kind == "route_map" else family
    return {"node": "fw-01", "context": "root", "kind": kind, "name": name, "family": family,
            "data": {"rules": rules}}  # fmt: skip


def _neighbor(filters, remote_as):
    return {"filters": filters, "remote_as": str(remote_as)}


ISP, SITE, PARTNER, DOWN = "198.51.100.1", "10.60.1.1", "192.0.2.9", "198.51.100.13"
FW = {
    "status": "completed",
    "routing": {
        "contexts": [{"node": "fw-01", "context": "root"}],
        "routes": [
            _bgp("0.0.0.0/0", ISP), _bgp("10.5.0.0/16", ISP),  # 10.5/16: Filter sperrt
            _bgp("10.60.0.0/16", SITE), _bgp("203.0.113.0/24", PARTNER),
            {"node": "fw-01", "context": "root", "prefix": "10.20.0.0/24", "table": "rib",
             "protocol": "connected", "next_hops": [{"interface": "srv"}]},
        ],
        "peers": [
            {"node": "fw-01", "context": "root", "peer_ip": ISP, "remote_as": 64500,
             "state": "Established",
             "attrs": {"config": _neighbor({"in": {"route_map": "RM_ISP_IN"},
                                            "out": {"prefix_list": "PL_DC"}}, 64500)}},
            {"node": "fw-01", "context": "root", "peer_ip": SITE, "remote_as": 65100,
             "state": "Established",
             "attrs": {"config": {**_neighbor({"in": {"prefix_list": "PL_SITES"}}, 65100),
                                  "group": "G_SITES"}}},
            {"node": "fw-01", "context": "root", "peer_ip": PARTNER, "remote_as": 64999,
             "state": "Established", "attrs": {"config": _neighbor({}, 64999)}},
        ],
        "bgp": [
            {"node": "fw-01", "context": "root", "kind": "bgp", "name": "bgp",
             "data": {"as": "65001", "networks": [{"prefix": "10.20.0.0/16"}],
                      "redistribute": [{"name": "connected"}],
                      "neighbors": {ISP: {}, DOWN: {"shutdown": True, "remote_as": "64502"}}}},
            _obj("route_map", "RM_ISP_IN", [
                {"id": 1, "action": "deny", "match": {"ip-address": "ACL_BOGONS"}},
                {"id": 2, "action": "permit", "match": {}, "set": {"local-preference": 200}}]),
            _obj("access_list", "ACL_BOGONS", [
                {"id": 1, "action": "permit", "prefix": "10.0.0.0/8", "exact": False}]),
            _obj("prefix_list", "PL_DC", [
                {"id": 1, "action": "permit", "prefix": "10.20.0.0/16", "le": 24}]),
            _obj("prefix_list", "PL_SITES", [
                {"id": 1, "action": "permit", "prefix": "10.60.0.0/16"},
                {"id": 2, "action": "permit", "prefix": "10.61.0.0/16"},
                {"id": 3, "action": "deny", "prefix": "any"}]),
        ],
    },
}  # fmt: skip

APIC = {
    "status": "completed",
    "routing": {
        "contexts": [{"node": "leaf-101", "context": "TE:VRF1", "kind": "vrf"}],
        "routes": [{"node": "leaf-101", "context": "TE:VRF1", "prefix": "0.0.0.0/0",
                    "protocol": "bgp", "next_hops": [{"ip": "10.255.0.1"}]}],
        "l3outs": [
            {"tenant": "TE", "name": "L3O_inet", "dn": "uni/tn-TE/out-L3O_inet", "vrf": "TE:VRF1",
             "peers": [{"ip": "10.255.0.1", "asn": 65010}],
             "route_control": {
                 "enforce": ["export", "import"],
                 "import": [{"prefix": "0.0.0.0/0", "epg": "EN_inet", "aggregate": True},
                            {"prefix": "172.16.0.0/16", "epg": "EN_inet"}],
                 "export": [{"prefix": "10.20.0.0/16", "epg": "EN_inet"}]}},
            {"tenant": "TE", "name": "L3O_vpn", "dn": "uni/tn-TE/out-L3O_vpn", "vrf": "TE:VRF1",
             "route_control": {"enforce": ["export"],
                               "import": [{"prefix": "10.80.0.0/16", "epg": "EN_vpn"}]}},
        ],
    },
}  # fmt: skip


def _deliver(client, tid, sid, platform, result):
    run = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _seed(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = []
    for name, platform, result in (("fw-01", "fortigate", FW), ("apic-01", "cisco_aci", APIC)):
        body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x"}
        sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
        _deliver(client, tid, sid, platform, result)
        ids.append(sid)
    return tid, ids[0], ids[1]


def test_regeln_wie_die_fortigate():
    """Prefix-Liste: ohne ge/le genau das Netz, le/ge als Bereich; Access-Liste: das Netz und
    alles darin; Route-Map: erste passende Regel, ohne Bedingung passt alles, sonst deny."""
    net = bgp_filters._net
    rule = {"prefix": "10.20.0.0/16", "le": 24}
    assert bgp_filters.prefix_match(rule, net("10.20.5.0/24"))
    assert not bgp_filters.prefix_match(rule, net("10.20.5.0/25"))
    assert not bgp_filters.prefix_match({"prefix": "10.20.0.0/16"}, net("10.20.5.0/24"))
    assert bgp_filters.prefix_match({"prefix": "10.0.0.0/8", "ge": 24}, net("10.1.1.0/24"))
    assert not bgp_filters.prefix_match({"prefix": "10.0.0.0/8", "ge": 24}, net("10.1.0.0/16"))
    assert bgp_filters.access_match({"prefix": "10.0.0.0/8"}, net("10.1.0.0/16"))
    assert not bgp_filters.access_match({"prefix": "10.0.0.0/8", "exact": True},
                                        net("10.1.0.0/16"))  # fmt: skip
    assert bgp_filters.access_match({"prefix": "10.0.0.0/8", "wildcard": "x"}, net("10/8")) is None
    assert bgp_filters.catch_all({"prefix": "any"})
    assert bgp_filters.catch_all({"prefix": "0.0.0.0/0", "le": 32})
    assert not bgp_filters.catch_all({"prefix": "0.0.0.0/0"})  # genau die Default-Route
    cfg = bgp_filters.Config(objects={
        ("route_map", None, "RM"): {"rules": [
            {"id": 1, "action": "deny", "match": {"ip-address": "PL"}},
            {"id": 2, "action": "permit", "match": {"as-path": "AS_X"}}]},
        ("prefix_list", 4, "PL"): {"rules": [{"id": 1, "action": "permit", "prefix": "10.0.0.0/8",
                                              "le": 32}]},
    })  # fmt: skip
    assert cfg.route_map("RM", net("10.1.0.0/16")) == "deny"
    assert cfg.route_map("RM", net("192.0.2.0/24")) is None  # AS-Path: unbekannt
    assert cfg.verdict({}, net("192.0.2.0/24")) == "permit"  # ohne Filter alles
    assert cfg.verdict({"filter_list": "AS"}, net("192.0.2.0/24")) is None


def test_soll_ist_je_nachbar_befunde_und_vorschlag(client):
    tid, fw, apic = _seed(client)
    p = {"tenant_id": tid}
    peers = {x["peer_ip"]: x for x in client.get("/routing/peers", params=p).json()}
    assert peers[ISP]["attrs"]["config"]["filters"]["in"] == {"route_map": "RM_ISP_IN"}

    isp = client.get(f"/routing/bgp/peers/{peers[ISP]['id']}", params=p).json()
    assert isp["configured"] and isp["ebgp"] and isp["local_as"] == 65001
    assert {x["prefix"]: x["verdict"] for x in isp["in"]["received"]} == {
        "0.0.0.0/0": "permit", "10.5.0.0/16": "deny"}  # fmt: skip
    # angekuendigt (berechnet): network und redistribute connected durch PL_DC; Standort und
    # Partner (10.60/16, 203.0.113/24) fallen am Filter weg, die Default-Route kam vom ISP selbst
    assert [(x["prefix"], x["origin"]) for x in isp["out"]["announced"]] == [
        ("10.20.0.0/16", "network"), ("10.20.0.0/24", "redistribute connected")]  # fmt: skip
    assert isp["out"]["counts"] == {"permit": 2, "deny": 2}
    assert isp["out"]["entries"][0]["prefix"] == "10.20.0.0/16"

    site = client.get(f"/routing/bgp/peers/{peers[SITE]['id']}", params=p).json()
    entries = {e["prefix"]: e for e in site["in"]["entries"]}
    assert (entries["10.60.0.0/16"]["received"], entries["10.61.0.0/16"]["missing"]) == (1, True)
    assert site["group"] == "G_SITES"
    partner = client.get(f"/routing/bgp/peers/{peers[PARTNER]['id']}", params=p).json()
    assert partner["in"]["open"] and partner["in"]["counts"] == {"permit": 1}

    (process,) = client.get("/routing/bgp/config", params=p).json()
    assert (process["as"], process["neighbors"]) == ("65001", 2)
    assert process["objects"] == {"route_map": 1, "access_list": 1, "prefix_list": 2}
    assert [a["peer_ip"] for a in process["configured_only"]] == [DOWN]

    # ACI: Import erzwungen -> 0/0 (aggregate) erhalten, 172.16/16 erwartet, nicht erhalten
    l3 = {x["name"]: x for x in client.get("/routing/l3outs", params=p).json()}
    rc = client.get(f"/routing/bgp/l3outs/{l3['L3O_inet']['id']}", params=p).json()
    assert rc["import_enforced"] and rc["import"]["counts"] == {"permit": 1, "missing": 1}
    assert [e["missing"] for e in rc["import"]["entries"]] == [False, True]
    assert rc["export"]["entries"] == [{"prefix": "10.20.0.0/16", "epg": "EN_inet"}]

    assert client.post("/reconcile", params=p, headers=OP).status_code == 200
    found: dict[str, list] = {}
    for f in client.get("/findings", params={**p, "page_size": 200}).json():
        if f["type"].startswith(("bgp_", "aci_import")):
            found.setdefault(f["type"], []).append(f)
    assert sorted((t, sorted(x["subject_id"] for x in v)) for t, v in found.items()) == [
        ("aci_import_not_enforced", ["L3O_vpn · TE:VRF1"]),
        ("bgp_expected_missing", ["L3O_inet · TE:VRF1", f"fw-01 → {SITE}"]),
        ("bgp_no_inbound_filter", [f"fw-01 → {PARTNER}"]),
        ("bgp_outside_filter", [f"fw-01 → {ISP}"]),
    ]  # fmt: skip
    missing = next(f for f in found["bgp_expected_missing"] if SITE in f["subject_id"])
    assert "10.61.0.0/16" in missing["message"] and "PL_SITES" in missing["message"]
    outside = found["bgp_outside_filter"][0]
    assert outside["detail"]["prefixes"] == ["10.5.0.0/16"]

    # Vorschlag fuer „Netze dahinter“ einer Anbindung: Netze der Eingangsfilter, ohne „alles“
    sug = client.get("/routing/bgp/filtered", params={**p, "next_hops": [SITE, "10.255.0.1"]})
    assert sug.json()["prefixes"] == ["10.60.0.0/16", "10.61.0.0/16", "172.16.0.0/16"]
    assert sug.json()["filters"] == ["PL_SITES", "L3O_inet (import-rtctrl)"]
    none = client.get("/routing/bgp/filtered", params={**p, "next_hops": [PARTNER]}).json()
    assert none["prefixes"] == []


def test_bgp_filter_mandant_und_kunde(client):
    tid, fw, _ = _seed(client)
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    peer = client.get("/routing/peers", params={"tenant_id": tid}).json()[0]
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(other)}
    r = client.get(f"/routing/bgp/peers/{peer['id']}", params={"tenant_id": tid}, headers=viewer)
    assert r.status_code == 403
    r = client.get(f"/routing/bgp/peers/{peer['id']}", params={"tenant_id": other},
                   headers=viewer)  # fmt: skip
    assert r.status_code == 404  # Nachbar eines anderen Mandanten
