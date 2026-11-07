"""IPv6-Nachbarn der Switches (0.94.0, ADR 0025 Nr. 13): gesammelt in `arp_entry` wie ARP (ohne
Migration, Adresse ohne Zone), in den Endpunkten mit IPv6, Link-local getrennt und nicht als
Identitaet (kein Kunden-Ausschnitt), „Wo haengt …“ nach globaler bzw. Link-local-Adresse, Anschluss
fuer die Weg-Suche.

Aufbau wie `test_l2`: der physische Server SRV an sw-acc-01 Gi1/0/7 hat 10.20.0.15 (ARP) und
2001:db8:20::15 (NDP) sowie eine Link-local-Adresse; web01 (VM) bekommt seine IPv6 aus dem NDP.
"""

import test_l2 as tl
from app.models import ArpEntry
from app.store import l2, l2_topo
from sqlalchemy import select

OP = tl.OP
SRV = tl.SRV
SRV_LL = "fe80::a2b1:c2ff:fed3:e4f5"
WEB = "00:50:56:01:00:01"
ND = [
    {"device": "sw-core-01", "ip": "2001:DB8:20::15", "mac": SRV, "interface": "Vl20"},
    {"device": "sw-core-01", "ip": f"{SRV_LL.upper()}%Vlan20", "mac": SRV, "interface": "Vl20"},
    {"device": "sw-core-01", "ip": "2001:db8:20::5", "mac": WEB, "interface": "Vl20"},
]


def _setup(client):
    tid, sw = tl.tc._setup(client)
    body = {"tenant_id": tid, "name": "vc", "platform": "vmware", "base_url": "https://vc"}
    vc = client.post("/systems", json=body, headers=tl.ADMIN).json()
    tl._deliver(client, tid, vc["id"], "vmware", tl.VCENTER)
    tl._deliver(client, tid, sw["id"], "cisco_switch", tl._switches(tl.FIRST, tl.ARPS + ND))
    return tid, sw


def test_ipv6_endpunkte_und_suche(client, session_factory):
    tid, sw = _setup(client)
    with session_factory() as s:  # ohne Zone, einheitlich klein geschrieben
        ips = set(s.scalars(select(ArpEntry.ip)))
    assert ips == {"10.20.0.15", "2001:db8:20::15", SRV_LL, "2001:db8:20::5"}
    assert l2.link_local(SRV_LL) and not l2.link_local("2001:db8:20::15")
    assert not l2.link_local("169.254.0.1")  # nur IPv6 getrennt

    ep = client.get("/cabling/endpoints", params={"tenant_id": tid}).json()
    by_mac = {i["mac"]: i for i in ep["items"]}
    srv = by_mac[SRV]
    assert srv["ips"] == ["10.20.0.15", "2001:db8:20::15"] and srv["link_local"] == [SRV_LL]
    web = by_mac[WEB]  # VM: IPv4 aus den VMware Tools, IPv6 aus dem NDP
    assert web["ips"] == ["10.20.0.5", "2001:db8:20::5"] and web["link_local"] == []
    assert ep["summary"]["with_ip"] == 2
    found = client.get("/cabling/endpoints", params={"tenant_id": tid, "q": SRV_LL}).json()
    assert [i["mac"] for i in found["items"]] == [SRV]

    # Wo haengt …: globale IPv6 (auch gross geschrieben) und Link-local
    for q in ("2001:db8:20::15", "2001:DB8:20::15"):
        hit = client.get("/cabling/locate", params={"tenant_id": tid, "q": q}).json()
        assert hit["kind"] == "ip" and hit["ips"] == ["2001:db8:20::15"]
        (item,) = hit["items"]
        assert item["mac"] == SRV and item["reason"] == "NDP 2001:db8:20::15"
        assert [(x["device"], x["port"]) for x in item["locations"]] == [("sw-acc-01", "Gi1/0/7")]
        assert item["link_local"] == [SRV_LL]
    ll = client.get("/cabling/locate", params={"tenant_id": tid, "q": SRV_LL}).json()
    assert [i["mac"] for i in ll["items"]] == [SRV]
    vm = client.get("/cabling/locate", params={"tenant_id": tid, "q": "2001:db8:20::5"}).json()
    assert vm["items"][0]["vm"]["name"] == "web01"

    # Anschluss fuer die Weg-Suche (L2 von Quelle/Ziel) auch ueber IPv6
    with session_factory() as s:
        loc = l2.Locator(s, tid)
        where = l2_topo._located(s, tid, "2001:db8:20::15", loc)
        assert [(w["device"], w["port"], w["vlan"]) for w in where] == [("sw-acc-01", "Gi1/0/7",
                                                                         20)]  # fmt: skip
        # Link-local zaehlt nicht als Identitaet (keine IPAM-Namen, kein Kunden-Ausschnitt)
        assert SRV_LL not in loc.identity(SRV).ips

    # zweiter Lauf: IPv6 nachgefuehrt, nichts doppelt
    tl._deliver(client, tid, sw["id"], "cisco_switch", tl._switches(tl.FIRST, tl.ARPS + ND))
    with session_factory() as s:
        assert len(s.scalars(select(ArpEntry)).all()) == 4


def test_kunde_sieht_endpunkt_ueber_ipv6(client):
    tid, _ = _setup(client)

    def customer(name, prefix):
        body = {"tenant_id": tid, "name": name,
                "selectors": [{"kind": "prefix", "value": prefix}]}  # fmt: skip
        r = client.post("/customers", json=body, headers=OP)
        assert r.status_code in (200, 201), r.text
        return r.json()["id"]

    v6 = customer("Kunde A", "2001:db8:20::/64")
    ll = customer("Kunde B", "fe80::/64")  # Link-local: keine Zugehoerigkeit
    rows = client.get("/cabling/endpoints", params={"tenant_id": tid, "customer_id": v6}).json()
    assert {i["mac"] for i in rows["items"]} == {SRV, WEB}
    none = client.get("/cabling/endpoints", params={"tenant_id": tid, "customer_id": ll}).json()
    assert none["items"] == []
