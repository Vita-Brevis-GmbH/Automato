"""NetBox als IPAM ohne DDI (1.8.0).

Anwender 2026-11-11: „Bei den Befunden kommt viele Meldungen, dass ein Objekt nicht im IPAM ist,
da wir kein Infoblox bei dem Kunden haben, sollte das IPAM die Netbox übernehmen und mit dem Sync
sollte das IPAM auch befüllt werden, daher ist der Fehler irgendwie verwirrend.“

A: Ist das führende IPAM die NetBox, die der Sync füllt, sagt der Befund „fehlt noch in der
NetBox … kommt mit dem NetBox-Sync“. B: Der Abschnitt „Firewall-Objekte“ schreibt Hosts, Netze und
Bereiche der Firewall-Objekte nach NetBox - nur wenn diese NetBox das IPAM ist."""

import pytest
from test_findings_explain import _findings
from test_netbox_sync import _apply, _items, _plan, _tagged, fake_netbox
from test_reference_sources import TOKEN, _ddi, _run, _system, _tenant

OBJECTS = [
    {"name": "H_web01", "category": "address", "obj_type": "ipmask", "values": ["10.20.0.5"]},
    {"name": "N_clients", "category": "address", "obj_type": "ipmask", "values": ["10.9.0.0/16"]},
    {"name": "R_dhcp", "category": "address", "obj_type": "iprange",
     "values": ["10.9.1.10-10.9.1.50"]},
    {"name": "H-EXT_dns", "category": "address", "obj_type": "ipmask", "values": ["9.9.9.9"]},
]  # fmt: skip
RULES = [
    {"source_platform": "fortigate", "external_id": "1", "name": "clients-web", "action": "allow",
     "sources": ["10.9.0.0/16", "10.9.1.10-10.9.1.50"], "destinations": ["10.20.0.5"],
     "services": ["tcp/443"], "source_refs": ["N_clients", "R_dhcp"],
     "destination_refs": ["H_web01"], "service_refs": ["HTTPS"]},
    {"source_platform": "fortigate", "external_id": "2", "name": "web-dns", "action": "allow",
     "sources": ["10.20.0.5"], "destinations": ["9.9.9.9"], "services": ["udp/53"],
     "source_refs": ["H_web01"], "destination_refs": ["H-EXT_dns"], "service_refs": ["DNS"]},
]  # fmt: skip
INVENTORY = {"subnets": [{"prefix": "10.20.0.0/24", "context": "interface",
                          "context_name": "srv", "vlan": 20}]}  # fmt: skip


@pytest.fixture
def netbox(monkeypatch):
    return fake_netbox(monkeypatch)


def _firewall(client, tid) -> None:
    fw = _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    result = {"status": "completed", "rules": RULES, "objects": OBJECTS, "inventory": INVENTORY}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.json()["status"] == "completed", r.text


def _seed(client) -> dict:
    tid = _tenant(client)
    nb = _system(client, tid, name="netbox", platform="netbox", base_url="http://netbox:8080",
                 token="nb-lesen", write_token="nb-schreiben", ref_role="primary")  # fmt: skip
    _run(client, tid, nb["id"], "netbox", [])  # NetBox eingelesen, noch leer
    _firewall(client, tid)
    return {"tid": tid, "nb": nb["id"]}


def test_befund_sagt_kommt_mit_dem_sync(client):
    ids = _seed(client)
    f = _findings(client, ids["tid"], "unmatched_object")
    web = f["10.20.0.5"]
    assert web["detail"]["sync_target"] == "netbox"
    assert web["message"].startswith(
        "10.20.0.5 fehlt noch in der NetBox „netbox“ – sie ist das IPAM und wird von Automato"
        " befüllt."
    )
    assert "Abschnitt „Firewall-Objekte“" in web["hint"]
    assert "9.9.9.9" not in f  # bewusst extern: kein Befund
    dhcp = f["10.9.1.10-10.9.1.50"]  # Bereich: legt der Sync ebenfalls an
    assert dhcp["message"].startswith("10.9.1.10-10.9.1.50 fehlt noch in der NetBox")


def test_ohne_fuehrendes_ipam_mit_einer_netbox(client):
    tid = _tenant(client)
    _system(client, tid, name="netbox", platform="netbox", base_url="http://netbox:8080",
            token="nb-lesen", write_token="nb-schreiben", ref_role="enrichment")  # fmt: skip
    _firewall(client, tid)
    web = _findings(client, tid, "unmatched_object")["10.20.0.5"]
    assert "die NetBox „netbox“ ist noch nicht als führendes IPAM" in web["message"]
    assert "als führend eintragen" in web["hint"]


def test_sync_schreibt_die_firewall_objekte(client, netbox):
    ids = _seed(client)
    body = {"sections": ["firewall_objects"]}
    plan = _plan(client, ids, **body)
    creates = {(i["kind"], i["key"]): i for i in _items(client, plan["id"], action="create")}
    assert ("ip_address", "10.20.0.5") in creates
    assert ("prefix", "10.9.0.0/16") in creates
    assert ("ip_range", "10.9.1.10-10.9.1.50") in creates
    assert not any(k == "9.9.9.9" for _kind, k in creates)  # bewusst extern
    assert _apply(client, plan["id"])["status"] == "applied"
    ip = netbox.find("ipam/ip-addresses", address="10.20.0.5/32")
    assert ip["description"] == "FW-Objekt H_web01" and _tagged(ip)
    assert netbox.find("ipam/prefixes", prefix="10.9.0.0/16")["description"] == (
        "FW-Objekt N_clients"
    )
    again = _plan(client, ids, **body)  # stabil
    assert again["totals"]["create"] == again["totals"]["update"] == 0
    # ohne den Abschnitt bleiben die Werte stehen - auch mit „Löschen zulassen“
    other = _plan(client, ids, sections=["inventory", "mirror", "virtualization"],
                  allow_delete=True)  # fmt: skip
    gone = _items(client, other["id"], action="delete")
    assert not [i for i in gone if i["kind"] in ("ip_address", "prefix", "ip_range")], gone


def test_anderes_fuehrendes_ipam_nur_hinweis(client, netbox):
    tid = _tenant(client)
    ddi = _ddi(client, tid)
    _run(client, tid, ddi["id"], "infoblox", [{"kind": "prefix", "value": "10.20.0.0/24"}])
    nb = _system(client, tid, name="netbox", platform="netbox", base_url="http://netbox:8080",
                 token="nb-lesen", write_token="nb-schreiben", ref_role="enrichment")  # fmt: skip
    _firewall(client, tid)
    plan = _plan(client, {"tid": tid, "nb": nb["id"]}, sections=["firewall_objects"])
    assert "Firewall-Objekte: führendes IPAM ist „ddi-01“ – die Werte gehören dorthin, nicht" \
        " übernommen." in plan["warnings"]  # fmt: skip
    kinds = {i["kind"] for i in _items(client, plan["id"], action="create")}
    assert not kinds & {"ip_address", "prefix", "ip_range"}
