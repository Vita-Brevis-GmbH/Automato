"""Applikations-Doku ohne ACI: Netz statt EPG (1.8.0).

Anwender 2026-11-11: „Bei den Applikationen kommt die EPG, obwohl ich kein ACI verbunden habe,
hier sollte besser das Netzwerk stehen, wo wir connected sind.“ Je System das engste Subnetz des
Netzinventars (FortiGate-Interface mit VLAN und Gerät) und die Portgruppe; `aci` sagt der
Oberfläche, ob es EPGs gibt."""

import copy

from test_vmware import ADMIN, FW, VCENTER, _deliver, _seed, _system

OP = {"X-Dev-Roles": "operator"}

FW_NET = copy.deepcopy(FW)
FW_NET["inventory"] = {
    "devices": [{"name": "fw-01", "role": "firewall", "model": "FGVM64"}],
    "subnets": [
        {"prefix": "10.20.0.0/16", "context": "interface", "context_name": "transit"},
        {"prefix": "10.20.0.0/24", "gateway": "10.20.0.1/24", "vlan": 20,
         "context": "interface", "context_name": "srv-vl20"},
    ],
}  # fmt: skip


def _doc(client, tid) -> dict:
    body = {"name": "Web", "members": [{"kind": "host", "ref": "10.20.0.5"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    return client.get(f"/applications/{app['id']}/doc").json()


def test_ohne_aci_netz_statt_epg(client):
    tid = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    vc = _system(client, tid, "vc-01", "vmware", base_url="https://vc.example.ch",
                 username="ro", password="x")  # fmt: skip
    _deliver(client, tid, vc, "vmware", VCENTER)
    fw = _system(client, tid, "fw-01", "fortigate", base_url="https://fw")
    _deliver(client, tid, fw, "fortigate", FW_NET)

    doc = _doc(client, tid)
    assert doc["aci"] is False and doc["epgs"] == []
    (web,) = doc["systems"]
    assert web["name"] == "web01-007-ka" and web["epgs"] == []
    # das engste Netz (nicht das /16), mit Interface, VLAN und Gerät; dazu die Portgruppe
    assert web["networks"] == ["10.20.0.0/24 · srv-vl20 · VLAN 20 (fw-01)"]
    assert web["portgroups"] == ["TE_ka|AP_007|EG_web01 (VLAN 1107)"]


def test_mit_aci_bleibt_die_epg(client):
    ids = _seed(client)
    doc = _doc(client, ids["tid"])
    assert doc["aci"] is True
    (web,) = doc["systems"]
    assert web["epgs"] == ["TE_ka/AP_007/EG_web01"]
