"""NetBox-Sync: Abschnitte Verkabelung und Applikationen (ADR 0031) gegen die Fake-NetBox.

Die Fake-NetBox der bestehenden Sync-Tests wird um Kabel erweitert: Enden als
`a_terminations`/`b_terminations` mit dem Port (`object`), am Port das Feld `cable`; ein Port
mit Kabel nimmt kein zweites (HTTP 400 wie NetBox). Tags an Objekten muessen als Tag existieren.

Verkabelung: sw-acc-01 und sw-core-01 (Switches & Router) und leaf-101 (ACI, Netzinventar).
Gi1/0/47 <-> Eth1/1 und Eth1/10 <-> leaf-101 eth1/10 melden beide Seiten (Kabel), Eth1/3 ->
Gi1/0/5 nur sw-core-01 (einseitig), Te1/1/1 -> fw-08 (nicht gelesen). sw-old-01 ohne Modell.
VLANs in NetBox: 10 global, 20 am Standort ZRH, 30 in zwei Gruppen (nicht eindeutig), 99 fehlt.
"""

import copy
import json

import httpx
import pytest
from app.api import netbox_sync as api
from app.models import AuditLog, InvInterface
from app.store import netbox_sync
from sot_gateway.adapters.netbox_writer import NetBoxWriter
from tests.test_netbox_sync import FakeNetBox, _apply, _reader, _tagged
from tests.test_vmware import BACKUP, DB, WEB
from tests.test_vmware import _seed as seed_vmware

ADMIN = {"X-Dev-Roles": "admin"}
ADMIN_EN = {**ADMIN, "Accept-Language": "en"}
OP = {"X-Dev-Roles": "operator"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


class CableNetBox(FakeNetBox):
    """Fake-NetBox mit Kabeln und Pruefung der Tags."""

    def _check_tags(self, body: list[dict]) -> str | None:
        known = {t["slug"] for t in self.objects("extras/tags")}
        for o in body:
            for t in o.get("tags") or []:
                if t["slug"] not in known:
                    return f"tag {t['slug']} missing"
        return None

    def _end(self, term: dict) -> dict:
        port = self.db["dcim/interfaces"][term["object_id"]]
        obj = {"id": port["id"], "name": port["name"], "device": port["device"]}
        return {"object_type": term["object_type"], "object_id": port["id"], "object": obj}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/").strip("/")
        if request.method in ("POST", "PATCH"):
            body = json.loads(request.content)
            error = self._check_tags(body)
            if error:
                return httpx.Response(400, json={"tags": [error]})
        if path != "dcim/cables":
            return super().handler(request)
        if request.method == "GET":
            ids = set(request.url.params.get_list("device_id"))
            rows = [
                c for c in self.objects(path)
                if any(str(t["object"]["device"]["id"]) in ids
                       for side in ("a_terminations", "b_terminations") for t in c[side])
            ]  # fmt: skip
            return httpx.Response(200, json={"count": len(rows), "results": rows})
        body = json.loads(request.content)
        self.writes.append((request.method, path, len(body)))
        if request.method == "POST":
            out = []
            for o in body:
                ends = [self._end(t) for side in ("a_terminations", "b_terminations")
                        for t in o[side]]  # fmt: skip
                if any(self.db["dcim/interfaces"][e["object_id"]].get("cable") for e in ends):
                    return httpx.Response(400, json={"a_terminations": ["already cabled"]})
                raw = {k: v for k, v in o.items() if k not in ("a_terminations", "b_terminations")}
                cable = self.add(path, raw)
                cable["a_terminations"] = [self._end(t) for t in o["a_terminations"]]
                cable["b_terminations"] = [self._end(t) for t in o["b_terminations"]]
                for e in ends:
                    self.db["dcim/interfaces"][e["object_id"]]["cable"] = {"id": cable["id"]}
                out.append(cable)
            return httpx.Response(201, json=out)
        if request.method == "DELETE":
            for o in body:
                cable = self.db[path].pop(o["id"])
                for side in ("a_terminations", "b_terminations"):
                    for t in cable[side]:
                        self.db["dcim/interfaces"][t["object_id"]]["cable"] = None
            return httpx.Response(204)
        return super().handler(request)


@pytest.fixture
def netbox(monkeypatch) -> CableNetBox:
    fake = CableNetBox()

    def make_writer(target):
        token = api.credentials.decrypt(target.credentials).get("write_token")
        client = httpx.Client(transport=httpx.MockTransport(fake.handler))
        return NetBoxWriter(target.base_url, token, client=client)

    monkeypatch.setattr(api, "make_writer", make_writer)
    _reader(monkeypatch, fake)
    return fake


def _port(device, name, **kw):
    return {"device": device, "name": name, "kind": "physical", "status": "connected",
            "enabled": True, "speed": "a-1000", "duplex": "a-full", **kw}  # fmt: skip


SWITCHES = {
    "status": "completed",
    "inventory": {
        "devices": [
            {"name": "sw-acc-01", "role": "switch", "model": "C9300-48P", "serial": "FOC1",
             "mgmt_ip": "10.0.0.11", "version": "17.9.4a"},
            {"name": "sw-core-01", "role": "switch", "model": "C93180YC-FX",
             "mgmt_ip": "10.0.0.2"},
            {"name": "sw-old-01", "role": "switch", "mgmt_ip": "10.0.0.3"},  # ohne Modell
            {"name": "mx-01", "role": "router", "model": "MX204", "mgmt_ip": "10.0.0.4"},
        ],
        "interfaces": [
            _port("sw-acc-01", "Gi1/0/47", lag="Po1", mode="tagged", native_vlan=1,
                  untagged_vlan=1, trunk_vlans="1,10,20,40", tagged_vlans=[1, 10, 20, 40],
                  description="Uplink core"),
            _port("sw-acc-01", "Po1", kind="lag", mode="tagged", native_vlan=1,
                  trunk_vlans="1,10,20,40"),
            _port("sw-acc-01", "Gi1/0/5", mode="access", untagged_vlan=20),
            _port("sw-acc-01", "Gi1/0/6", mode="access", untagged_vlan=99),  # fehlt in NetBox
            _port("sw-acc-01", "Gi1/0/7", mode="access", untagged_vlan=30),  # nicht eindeutig
            _port("sw-acc-01", "Te1/1/1", mode="access", untagged_vlan=10, speed="10G",
                  attrs={"media": "SFP-10GBase-SR"}),
            _port("sw-acc-01", "Vlan10", kind="vlan", status=None, speed=None, duplex=None),
            _port("sw-core-01", "Eth1/1", speed="10G", mode="tagged", native_vlan=1,
                  trunk_vlans="1-4094"),
            _port("sw-core-01", "Eth1/3", mode="access", untagged_vlan=20, enabled=False),
            _port("sw-core-01", "Eth1/10", speed="10G", mode="access", untagged_vlan=10),
            _port("sw-old-01", "Gi0/1"),
            _port("mx-01", "xe-0/0/0", speed=None),
        ],
        "vlans": [{"vid": 1, "name": "default"}, {"vid": 10, "name": "Server"},
                  {"vid": 20, "name": "Clients"}, {"vid": 30, "name": "DMZ"},
                  {"vid": 99, "name": "Alt"}],
        "neighbors": [
            {"device": "sw-acc-01", "port": "Gi1/0/47", "protocol": "cdp",
             "remote_name": "sw-core-01", "remote_port": "Ethernet1/1"},
            {"device": "sw-core-01", "port": "Eth1/1", "protocol": "cdp",
             "remote_name": "sw-acc-01.example.ch", "remote_port": "GigabitEthernet1/0/47"},
            {"device": "sw-core-01", "port": "Eth1/3", "protocol": "lldp",
             "remote_name": "sw-acc-01", "remote_port": "Gi1/0/5"},  # meldet nur sw-core-01
            {"device": "sw-core-01", "port": "Eth1/10", "protocol": "lldp",
             "remote_name": "leaf-101", "remote_port": "Ethernet1/10"},
            {"device": "sw-acc-01", "port": "Te1/1/1", "protocol": "lldp", "remote_name": "fw-08",
             "remote_port": "port5"},
        ],
    },
}  # fmt: skip
ACI = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "leaf-101", "role": "leaf", "model": "N9K-C93180YC-FX",
                     "node_id": "101"}],
        "interfaces": [{"device": "leaf-101", "name": "eth1/10", "kind": "physical",
                        "neighbor": "sw-core-01", "neighbor_port": "Eth1/10"}],
    },
}  # fmt: skip
CABLE_UPLINK = "sw-acc-01/Gi1/0/47~sw-core-01/Eth1/1"
CABLE_LEAF = "leaf-101/eth1/10~sw-core-01/Eth1/10"


def _system(client, tid, **body) -> int:
    r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _deliver(client, tid, sid, platform, result) -> None:
    req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup_cabling(client, netbox) -> dict:
    # in NetBox von Hand: Standort ZRH, VLAN 10 global, VLAN 20 am Standort, VLAN 30 zweimal
    site = netbox.add("dcim/sites", {"name": "Zürich", "slug": "zrh", "status": "active"})
    netbox.add("ipam/vlans", {"vid": 10, "name": "Server", "status": "active"})
    netbox.add("ipam/vlans", {"vid": 20, "name": "Clients", "site": site["id"]})
    for slug in ("dmz-a", "dmz-b"):
        group = netbox.add("ipam/vlan-groups", {"name": slug, "slug": slug})
        netbox.add("ipam/vlans", {"vid": 30, "name": "DMZ", "group": group["id"]})
    tid = client.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=ADMIN).json()[
        "id"
    ]
    client.post(f"/tenants/{tid}/sites", json={"code": "ZRH", "name": "Zürich"}, headers=ADMIN)
    ids = {"tid": tid}
    ids["sw"] = _system(client, tid, name="Switches A", platform="cisco_switch",
                        read_scope="10.0.0.11\n10.0.0.2", username="ro", password="x")  # fmt: skip
    ids["apic"] = _system(client, tid, name="apic-01", platform="cisco_aci",
                          base_url="https://apic")  # fmt: skip
    ids["nb"] = _system(client, tid, name="netbox", platform="netbox",
                        base_url="https://nb.example.ch", token="nb-read",
                        write_token="nb-write")  # fmt: skip
    _deliver(client, tid, ids["sw"], "cisco_switch", SWITCHES)
    _deliver(client, tid, ids["apic"], "cisco_aci", ACI)
    return ids


def _plan(client, ids, headers=ADMIN, **opts) -> dict:
    body = {"tenant_id": ids["tid"], "source_system_id": ids["nb"], **opts}
    r = client.post("/netbox-sync/plans", json=body, headers=headers)
    assert r.status_code == 202, r.text
    plan = client.get(f"/netbox-sync/plans/{r.json()['id']}", headers=headers).json()
    assert plan["status"] == "ready", plan
    return plan


def _items(client, plan_id, headers=ADMIN, **params) -> list[dict]:
    params.setdefault("limit", 2000)
    r = client.get(f"/netbox-sync/plans/{plan_id}/items", params=params, headers=headers)
    return r.json()["rows"]


def _port_of(netbox, device, name) -> dict:
    return next(
        i
        for i in netbox.objects("dcim/interfaces")
        if i["name"] == name and i["device"]["name"] == device
    )


def _vids(port) -> list[int]:
    return sorted(v["vid"] for v in port.get("tagged_vlans") or [])


def test_abschnitte_standard_aus_und_waehlbar(client, netbox):
    ids = _setup_cabling(client, netbox)
    plan = _plan(client, ids)  # ohne Angabe: die bisherigen Abschnitte
    assert plan["options"]["sections"] == ["inventory", "mirror", "virtualization"]
    kinds = {i["kind"] for i in _items(client, plan["id"], action="create", limit=2000)}
    assert "cable" not in kinds and "sw-acc-01" not in {
        i["key"] for i in _items(client, plan["id"], kind="device")
    }
    body = {"tenant_id": ids["tid"], "source_system_id": ids["nb"], "sections": ["x"]}
    assert client.post("/netbox-sync/plans", json=body, headers=ADMIN).status_code == 422
    body["sections"] = ["cabling"]
    assert client.post("/netbox-sync/plans", json=body, headers=OP).status_code == 403


def test_verkabelung_ports_und_kabel(client, netbox, session_factory):
    ids = _setup_cabling(client, netbox)
    plan = _plan(client, ids, sections=["inventory", "cabling"])
    assert not netbox.writes  # Vorschau liest nur
    warnings = plan["warnings"]
    assert any(w.startswith("Gerät sw-old-01 (Switches A): Modell nicht gelesen") for w in warnings)
    assert "sw-acc-01: Modus/VLANs an 1 Ports weggelassen – VLAN 99 nicht in NetBox." in warnings
    assert (
        "sw-acc-01: Modus/VLANs an 1 Ports weggelassen – VLAN 30 in NetBox nicht eindeutig."
        in warnings
    )
    assert any(w.startswith("Verkabelung: 1 einseitig gemeldete Verbindungen") for w in warnings)
    unread = "Verkabelung: 1 Verbindungen zu Geräten ohne CDP/LLDP"
    assert any(w.startswith(unread) for w in warnings)
    cables = {i["key"]: i for i in _items(client, plan["id"], kind="cable")}
    assert set(cables) == {CABLE_UPLINK, CABLE_LEAF}  # einseitig/nicht gelesen: kein Kabel
    assert cables[CABLE_UPLINK]["fields"]["a_terminations"] == ["sw-acc-01/Gi1/0/47"]
    devices = {i["key"] for i in _items(client, plan["id"], kind="device")}
    assert devices == {"sw-acc-01", "sw-core-01", "mx-01", "leaf-101"}  # sw-old-01 ohne Modell

    done = _apply(client, plan["id"])
    assert done["status"] == "applied", done
    assert done["result"]["cable"]["create"] == 2
    acc = netbox.find("dcim/devices", name="sw-acc-01")
    assert acc["site"]["slug"] == "zrh" and acc["role"]["slug"] == "switch" and _tagged(acc)
    assert acc["device_type"]["model"] == "C9300-48P"
    mx = netbox.find("dcim/devices", name="mx-01")
    mx_type = netbox.db["dcim/device-types"][mx["device_type"]["id"]]
    assert netbox.db["dcim/manufacturers"][mx_type["manufacturer"]["id"]]["name"] == "Juniper"
    assert netbox.find("dcim/devices", name="sw-old-01") is None
    # Ports: Typ soweit ableitbar, LAG, Modus/VLANs nur mit vorhandenen VLANs
    up = _port_of(netbox, "sw-acc-01", "Gi1/0/47")
    assert (up["type"]["value"], up["mode"]["value"], _vids(up)) == ("1000base-t", "tagged",
                                                                      [10, 20])  # fmt: skip
    assert up["lag"]["name"] == "Po1" and up["description"] == "Uplink core"
    assert not up.get("untagged_vlan")  # Native VLAN 1 fehlt in NetBox: weggelassen
    assert _port_of(netbox, "sw-acc-01", "Po1")["type"]["value"] == "lag"
    access = _port_of(netbox, "sw-acc-01", "Gi1/0/5")
    assert access["mode"]["value"] == "access" and access["untagged_vlan"]["vid"] == 20
    for name in ("Gi1/0/6", "Gi1/0/7"):  # VLAN fehlt bzw. nicht eindeutig: ohne Modus/VLAN
        port = _port_of(netbox, "sw-acc-01", name)
        assert not port.get("mode") and not port.get("untagged_vlan")
    sfp = _port_of(netbox, "sw-acc-01", "Te1/1/1")
    assert sfp["type"]["value"] == "10gbase-x-sfpp" and sfp["untagged_vlan"]["vid"] == 10
    assert _port_of(netbox, "sw-acc-01", "Vlan10")["type"]["value"] == "virtual"
    core = _port_of(netbox, "sw-core-01", "Eth1/1")
    assert core["mode"]["value"] == "tagged-all" and core["type"]["value"] == "10gbase-x-sfpp"
    assert _port_of(netbox, "sw-core-01", "Eth1/3")["enabled"] is False
    assert _port_of(netbox, "mx-01", "xe-0/0/0")["type"]["value"] == "10gbase-x-sfpp"
    # Kabel: beide Enden gelesen, auch zum ACI-Leaf (Port aus dem Netzinventar)
    cable = next(c for c in netbox.objects("dcim/cables")
                 if c["a_terminations"][0]["object"]["name"] == "Gi1/0/47")  # fmt: skip
    assert cable["b_terminations"][0]["object_id"] == core["id"] and _tagged(cable)
    assert cable["status"]["value"] == "connected" and cable["tenant"]["slug"] == "kunde-a"
    assert core["cable"] == {"id": cable["id"]}
    leaf_port = _port_of(netbox, "leaf-101", "eth1/10")
    assert leaf_port["cable"] is not None
    s = session_factory()
    detail = next(a.detail for a in s.query(AuditLog) if a.action == "netbox_sync.applied")
    s.close()
    assert detail["sections"] == ["inventory", "cabling"] and detail["create"] > 0

    again = _plan(client, ids, sections=["inventory", "cabling"])  # idempotent
    assert again["totals"]["create"] == again["totals"]["update"] == 0, _items(
        client, again["id"], action="create,update"
    )
    # ohne den Abschnitt bleiben Switches, ihre Ports und Kabel (nicht veraltet)
    other = _plan(client, ids, sections=["inventory"], allow_delete=True)
    gone = _items(client, other["id"], action="delete", limit=2000)
    assert not [i for i in gone if i["kind"] in ("device", "interface", "cable")], gone


def test_umgesteckter_port_und_belegter_port(client, netbox, session_factory):
    ids = _setup_cabling(client, netbox)
    assert (
        _apply(client, _plan(client, ids, sections=["inventory", "cabling"])["id"])["status"]
        == "applied"
    )
    # Uplink umgesteckt: Gi1/0/47 jetzt an Eth1/10 (bisher leaf-101) - beide Ports belegt
    moved = copy.deepcopy(SWITCHES)
    moved["inventory"]["neighbors"] = [
        {"device": "sw-acc-01", "port": "Gi1/0/47", "protocol": "cdp",
         "remote_name": "sw-core-01", "remote_port": "Ethernet1/10"},
        {"device": "sw-core-01", "port": "Eth1/10", "protocol": "cdp",
         "remote_name": "sw-acc-01", "remote_port": "GigabitEthernet1/0/47"},
    ]  # fmt: skip
    _deliver(client, ids["tid"], ids["sw"], "cisco_switch", moved)
    s = session_factory()  # leaf-101 meldet sw-core-01 nicht mehr
    for i in s.query(InvInterface).filter(InvInterface.device == "leaf-101"):
        i.neighbor = i.neighbor_port = None
    s.commit()
    s.close()
    new = "sw-acc-01/Gi1/0/47~sw-core-01/Eth1/10"
    plan = _plan(client, ids, sections=["inventory", "cabling"])
    (conflict,) = _items(client, plan["id"], kind="cable", action="conflict")
    assert conflict["key"] == new  # Ports tragen noch die alten Kabel (nur veraltet)
    assert {c["field"] for c in conflict["changes"]} == {"sw-acc-01/Gi1/0/47",
                                                        "sw-core-01/Eth1/10"}  # fmt: skip
    obsolete = {i["key"] for i in _items(client, plan["id"], kind="cable", action="obsolete")}
    assert obsolete == {CABLE_UPLINK, CABLE_LEAF}
    # mit Löschen: erst die alten Kabel weg, dann das neue
    plan = _plan(client, ids, sections=["inventory", "cabling"], allow_delete=True)
    assert [i["key"] for i in _items(client, plan["id"], kind="cable", action="create")] == [new]
    done = _apply(client, plan["id"])
    assert done["status"] == "applied", done
    assert done["result"]["cable"] == {"create": 1, "update": 0, "delete": 2}
    (cable,) = netbox.objects("dcim/cables")
    assert cable["b_terminations"][0]["object"]["name"] == "Eth1/10"


def test_vlan_aus_der_gruppe_des_quellsystems(client, netbox):
    """Zuerst die VLAN-Gruppe des Quellsystems: VLAN 30 (sonst in zwei Gruppen) ist eindeutig."""
    ids = _setup_cabling(client, netbox)
    own = netbox.add("ipam/vlan-groups", {"name": "Switches A", "slug": "kunde-a-switches-a"})
    vlan = netbox.add("ipam/vlans", {"vid": 30, "name": "DMZ", "group": own["id"]})
    plan = _plan(client, ids, sections=["inventory", "cabling"])
    assert not any("nicht eindeutig" in w for w in plan["warnings"])
    assert _apply(client, plan["id"])["status"] == "applied"
    port = _port_of(netbox, "sw-acc-01", "Gi1/0/7")
    assert port["mode"]["value"] == "access" and port["untagged_vlan"]["id"] == vlan["id"]


def test_von_hand_gepflegtes_kabel_bleibt(client, netbox):
    ids = _setup_cabling(client, netbox)
    assert (
        _apply(client, _plan(client, ids, sections=["inventory", "cabling"])["id"])["status"]
        == "applied"
    )
    # Kabel von Hand (ohne Tag) zwischen denselben Ports: unveraendert, nie geloescht
    for c in netbox.objects("dcim/cables"):
        c["tags"] = []
        c["description"] = "von Hand"
    plan = _plan(client, ids, sections=["inventory", "cabling"], allow_delete=True)
    assert not _items(client, plan["id"], kind="cable", action="create,update,conflict,delete")
    assert plan["summary"]["cable"]["unchanged"] == 2


def _app(client, tid, name, refs, **extra) -> dict:
    body = {"name": name, "members": [{"kind": "epg", "ref": r} for r in refs], **extra}
    r = client.post("/applications", params={"tenant_id": tid}, json=body, headers=OP)
    assert r.status_code == 201, r.text
    return r.json()


def _tags(obj) -> set[str]:
    return {t["slug"] for t in obj["tags"]}


WEBSHOP = "automato-app-kunde-a-007"
DATENBANK = "automato-app-kunde-a-datenbank"


def test_applikations_tags_setzen_und_entfernen(client, netbox):
    # von Hand: IP (NetBox als IPAM), ein fremder Tag und ein Tag mit dem Namen "Webshop"
    netbox.add("ipam/ip-addresses", {"address": "10.20.1.7/16", "description": "von Hand"})
    netbox.add("extras/tags", {"name": "manuell", "slug": "manuell"})
    netbox.add("extras/tags", {"name": "Webshop", "slug": "webshop-alt"})
    ids = seed_vmware(client, netbox_target=True)
    tid = ids["tid"]
    shop = _app(client, tid, "Webshop", [WEB], code="007", criticality="high", purpose="prod")
    _app(client, tid, "Datenbank", [DB])

    plan = _plan(client, ids, sections=["virtualization", "applications"])
    assert not netbox.writes
    tags = {i["key"]: i for i in _items(client, plan["id"], kind="tag", action="create")}
    assert set(tags) == {"automato", WEBSHOP, DATENBANK}
    assert tags[WEBSHOP]["fields"]["name"] == "Webshop (Kunde A)"  # Name in NetBox vergeben
    assert tags[WEBSHOP]["fields"]["description"] == (
        "Automato: Applikation · Kürzel 007 · Zweck Prod · Kritikalität hoch"
    )
    vms = {i["key"]: i for i in _items(client, plan["id"], kind="app_tag_vm")}
    assert vms["web01-007-ka"]["changes"] == [{"field": "tags", "old": [], "new": [WEBSHOP]}]
    assert vms["db01-007-ka"]["changes"][0]["new"] == [DATENBANK]
    ips = {i["key"]: i for i in _items(client, plan["id"], kind="app_tag_ip", action="update")}
    assert set(ips) == {"10.20.0.5", "10.95.0.9"}
    (manual,) = _items(client, plan["id"], kind="app_tag_ip", action="conflict")
    assert manual["key"] == "10.20.1.7" and manual["manual"]  # fremdes Objekt: nicht aendern

    done = _apply(client, plan["id"])
    assert done["status"] == "applied", done
    tag = netbox.find("extras/tags", slug=WEBSHOP)
    assert tag["name"] == "Webshop (Kunde A)" and tag["color"] == "ff9800"
    web = netbox.find("virtualization/virtual-machines", name="web01-007-ka")
    assert _tags(web) == {"automato", WEBSHOP}
    assert _tags(netbox.find("ipam/ip-addresses", address="10.20.0.5/24")) == {"automato", WEBSHOP}
    backup_ip = netbox.find("ipam/ip-addresses", address="10.95.0.9/32")
    assert _tags(backup_ip) == {"automato", DATENBANK}
    assert _tags(netbox.find("ipam/ip-addresses", address="10.20.1.7/16")) == set()
    again = _plan(client, ids, sections=["virtualization", "applications"])
    assert again["totals"]["create"] == again["totals"]["update"] == 0

    # fremder Tag an der VM (von Hand) bleibt; Webshop zieht von web01 auf db01 um
    web["tags"].append({"slug": "manuell", "name": "manuell"})
    member = next(m for m in shop["members"] if m["ref"] == WEB)
    client.delete(f"/applications/{shop['id']}/members/{member['id']}", headers=OP)
    r = client.post(f"/applications/{shop['id']}/members", headers=OP,
                    json={"members": [{"kind": "epg", "ref": BACKUP}]})  # fmt: skip
    assert r.status_code == 200, r.text
    plan = _plan(client, ids, sections=["virtualization", "applications"])
    vms = {i["key"]: i for i in _items(client, plan["id"], kind="app_tag_vm")}
    assert vms["web01-007-ka"]["changes"] == [{"field": "tags", "old": [WEBSHOP], "new": []}]
    assert vms["db01-007-ka"]["changes"][0]["new"] == [WEBSHOP, DATENBANK]
    assert _apply(client, plan["id"])["status"] == "applied"
    assert _tags(netbox.find("virtualization/virtual-machines", name="web01-007-ka")) == {
        "automato",
        "manuell",
    }
    db = netbox.find("virtualization/virtual-machines", name="db01-007-ka")
    assert _tags(db) == {"automato", WEBSHOP, DATENBANK}

    # Applikation geloescht: Tag veraltet, an den Objekten entfernt; geloescht nur mit Freigabe
    apps = client.get("/applications", params={"tenant_id": tid}, headers=OP).json()
    datenbank = next(a for a in apps if a["name"] == "Datenbank")
    assert client.delete(f"/applications/{datenbank['id']}", headers=OP).status_code == 204
    plan = _plan(client, ids, sections=["virtualization", "applications"])
    assert [i["key"] for i in _items(client, plan["id"], kind="tag", action="obsolete")] == [
        DATENBANK
    ]
    plan = _plan(client, ids, sections=["virtualization", "applications"], allow_delete=True)
    assert _apply(client, plan["id"])["status"] == "applied"
    assert netbox.find("extras/tags", slug=DATENBANK) is None
    assert netbox.find("extras/tags", slug="manuell") is not None  # fremder Tag bleibt
    assert netbox.find("extras/tags", slug="webshop-alt") is not None
    db = netbox.find("virtualization/virtual-machines", name="db01-007-ka")
    assert _tags(db) == {"automato", WEBSHOP}


def test_applikations_tags_fingerabdruck(client, netbox):
    ids = seed_vmware(client, netbox_target=True)
    _app(client, ids["tid"], "Webshop", [WEB], code="007")
    plan = _plan(client, ids, sections=["virtualization", "applications"])
    _app(client, ids["tid"], "Datenbank", [DB])  # Automato aendert sich nach der Vorschau
    done = _apply(client, plan["id"])
    assert done["status"] == "failed" and "geändert" in done["message"]
    assert not netbox.writes  # nichts geschrieben


def test_vorschau_englisch_gespeichert_deutsch(client, netbox, session_factory):
    from app.models import SyncPlan

    ids = seed_vmware(client, netbox_target=True)
    netbox.add("ipam/ip-addresses", {"address": "10.20.1.7/16", "description": "von Hand"})
    _app(client, ids["tid"], "Datenbank", [DB])
    plan = _plan(client, ids, headers=ADMIN_EN, sections=["applications"])
    (hint,) = plan["warnings"]
    assert hint.startswith("Applications: 1 VMs and 1 IP addresses not in NetBox – without "
                           "application tag")  # fmt: skip
    rows = _items(client, plan["id"], kind="app_tag_ip", headers=ADMIN_EN)
    assert [r["label"] for r in rows] == ["Application tag on IP address 10.20.1.7"]
    s = session_factory()
    stored = s.get(SyncPlan, plan["id"])
    assert stored.warnings[0].startswith("Applikationen: 1 VMs und 1 IP-Adressen nicht in NetBox")
    s.close()


def test_verkabelung_hinweise_englisch(client, netbox):
    ids = _setup_cabling(client, netbox)
    plan = _plan(client, ids, headers=ADMIN_EN, sections=["cabling"])
    w = plan["warnings"]
    assert "sw-acc-01: mode/VLANs left out on 1 ports – VLAN 99 not in NetBox." in w
    assert "sw-acc-01: mode/VLANs left out on 1 ports – VLAN 30 not unique in NetBox." in w
    assert any(x.startswith("Device sw-old-01 (Switches A): model not read") for x in w)
    assert any(x.startswith("Cabling: 1 links reported by one side only") for x in w)
    # ohne Netzinventar fehlt der Port des Leafs: Kabel nicht uebernommen, Hinweis
    assert any(x.startswith("Cabling: 1 cables not applied – port not in NetBox") for x in w)
    rows = _items(client, plan["id"], kind="cable", headers=ADMIN_EN)
    assert [r["label"] for r in rows] == [f"Cable {CABLE_UPLINK.replace('~', ' ↔ ')}"]


def test_hilfen():
    assert netbox_sync.app_tag_owner("automato-app-kunde-a-b-x", ["kunde-a", "kunde-a-b"]) == (
        "kunde-a-b"
    )
    assert netbox_sync.app_tag_owner("automato-app-kunde-a-x", ["kunde-a", "kunde-a-b"]) == (
        "kunde-a"
    )
    assert netbox_sync.app_tag_owner("automato-app-fremd-x", ["kunde-a"]) is None
    assert netbox_sync.switch_manufacturer("EX4300-48T") == "Juniper"
    assert netbox_sync.switch_manufacturer("C1300-24P-4X") == "Cisco"

    def port(name, kind="physical", speed=None, media=None):
        return InvInterface(device="d", name=name, kind=kind, speed=speed,
                            attrs={"media": media} if media else None)  # fmt: skip

    cases = {
        ("Gi1/0/1", None, None): "1000base-t",
        ("Gi1/1/1", None, "1000BaseSX SFP"): "1000base-x-sfp",
        ("Te1/1/1", None, "10GBase-T"): "10gbase-t",
        ("Ethernet1/49", "100G", None): "100gbase-x-qsfp28",
        ("Ethernet1/1", "auto", None): "other",
        ("ge-0/0/1", None, None): "1000base-t",
    }
    for (name, speed, media), want in cases.items():
        assert netbox_sync.port_type(port(name, speed=speed, media=media)) == want, name
    assert netbox_sync.port_type(port("Po1", "lag")) == "lag"
    assert netbox_sync.port_type(port("Vlan10", "vlan")) == "virtual"
