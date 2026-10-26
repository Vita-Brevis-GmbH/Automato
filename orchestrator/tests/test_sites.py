"""Standorte virtuell (000, 001) und physisch (chzrh1) mit n:m-Zuordnung (ADR 0015)."""

import pytest
from app.models import IpamType, Site, SourceSystem, Tenant
from app.models.tenant import DnsType
from app.store import netbox_sync

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _tenant(client) -> int:
    return client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]


def _site(client, tid, code, name, **extra):
    r = client.post(
        f"/tenants/{tid}/sites", json={"code": code, "name": name, **extra}, headers=ADMIN
    )
    assert r.status_code == 201, r.text
    return r.json()


def _sites(client, tid) -> dict[str, dict]:
    return {s["code"]: s for s in client.get(f"/tenants/{tid}/sites", headers=ADMIN).json()}


def test_arten_pruefung_und_zuordnung(client):
    tid = _tenant(client)
    zrh = _site(client, tid, "chzrh1", "Zürich RZ1", kind="physical")
    bsl = _site(client, tid, "chbsl1", "Basel RZ2")  # Art aus dem Code
    assert zrh["kind"] == bsl["kind"] == "physical"
    stretched = _site(client, tid, "000", "Stretched DC", physical_site_ids=[zrh["id"], bsl["id"]])
    local = _site(client, tid, "001", "RZ1 lokal", kind="virtual", physical_site_ids=[zrh["id"]])
    assert stretched["kind"] == "virtual"
    assert stretched["physical_site_ids"] == sorted([zrh["id"], bsl["id"]])

    def post(**body):
        return client.post(f"/tenants/{tid}/sites", json=body, headers=ADMIN).status_code

    assert post(code="abc", name="x", kind="virtual") == 422  # virtuell = drei Ziffern
    assert post(code="123", name="x", kind="physical") == 422
    assert post(code="002", name="x", physical_site_ids=[local["id"]]) == 422  # nicht physisch
    assert post(code="chgva1", name="x", physical_site_ids=[zrh["id"]]) == 422  # nur virtuell
    assert post(code="000", name="doppelt") == 409

    rows = _sites(client, tid)
    assert list(rows) == ["000", "001", "chbsl1", "chzrh1"]  # virtuell zuerst
    assert rows["chzrh1"]["virtual_site_ids"] == sorted([stretched["id"], local["id"]])
    assert rows["chbsl1"]["virtual_site_ids"] == [stretched["id"]]

    # Zuordnung aendern, physischen Standort loeschen -> Zuordnung faellt weg
    r = client.patch(
        f"/tenants/{tid}/sites/{local['id']}",
        json={"physical_site_ids": [bsl["id"]], "name": "RZ2 lokal"},
        headers=ADMIN,
    )
    assert r.status_code == 200 and r.json()["physical_site_ids"] == [bsl["id"]]
    assert r.json()["name"] == "RZ2 lokal"
    phys_patch = client.patch(
        f"/tenants/{tid}/sites/{zrh['id']}", json={"physical_site_ids": []}, headers=ADMIN
    )
    assert phys_patch.status_code == 422
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    no = client.patch(f"/tenants/{tid}/sites/{local['id']}", json={"name": "x"}, headers=viewer)
    assert no.status_code == 403
    assert client.delete(f"/tenants/{tid}/sites/{bsl['id']}", headers=ADMIN).status_code == 204
    rows = _sites(client, tid)
    assert (
        rows["000"]["physical_site_ids"] == [zrh["id"]] and rows["001"]["physical_site_ids"] == []
    )

    # Mandant loeschen nimmt Standorte samt Zuordnung mit
    r = client.delete(f"/tenants/{tid}", params={"confirm": "Kunde A"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["sites"] == 3


OBJECTS = [
    {"name": "N_test1-000-dmz01", "category": "address", "values": ["10.10.20.0/24"]},
    {"name": "N_test1-007-lab", "category": "address", "values": ["10.10.70.0/24"]},
    # VM: der Block ist das ACI-Applikationsprofil, kein Standort
    {"name": "H_app01-001-test1.example.ch", "category": "address", "values": ["10.10.10.5"]},
]
INVENTORY = {
    "devices": [
        {"name": "fwc11-000-infrz", "role": "firewall"},
        {"name": "evh01-chzrh1-infrz", "role": "host"},
        {"name": "leaf-101", "role": "leaf"},
    ],
    "subnets": [
        {"prefix": "10.20.0.0/24", "context": "bd", "context_name": "N_test1-001-web"},
    ],
}


def _deliver(client, tid, sid, platform, **result):
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers=TOKEN,
        json={"status": "completed", **result},
    )
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


@pytest.fixture
def no_sot(monkeypatch):
    class NoSoT:
        name = "netbox"

        def search_by_ip(self, ip):
            return []

        def get_by_name(self, name):
            return None

    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: NoSoT())


def test_standort_aus_namen(client, no_sot):
    tid = _tenant(client)
    zrh = _site(client, tid, "chzrh1", "Zürich RZ1")
    _site(client, tid, "000", "Stretched DC", physical_site_ids=[zrh["id"]])
    _site(client, tid, "001", "RZ1 lokal")

    def system(name, platform):
        body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x"}
        r = client.post("/systems", json=body, headers=ADMIN)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    fw = system("fwc11-000-infrz", "fortigate")
    apic = system("apic-01", "cisco_aci")
    _deliver(client, tid, fw, "fortigate", rules=[], objects=OBJECTS)
    _deliver(client, tid, apic, "cisco_aci", inventory=INVENTORY)

    objs = client.get("/objects", params={"tenant_id": tid}).json()["rows"]
    site = {o["name"]: o["site"] for o in objs}
    assert site["N_test1-000-dmz01"]["code"] == "000" and site["N_test1-000-dmz01"]["known"]
    assert site["N_test1-000-dmz01"]["physical"] == ["chzrh1"]
    assert site["N_test1-007-lab"] == {"code": "007", "kind": "virtual", "known": False}
    assert site["H_app01-001-test1.example.ch"] is None  # VM: kein Standort im Namen

    devices = {
        d["name"]: d for d in client.get("/inventory/devices", params={"tenant_id": tid}).json()
    }
    assert devices["fwc11-000-infrz"]["site"]["code"] == "000"
    assert devices["evh01-chzrh1-infrz"]["site"]["kind"] == "physical"
    assert devices["leaf-101"]["site"] is None and devices["leaf-101"]["site_from"] is None
    (subnet,) = client.get("/inventory/subnets", params={"tenant_id": tid}).json()
    assert subnet["site"]["code"] == "001"

    hints = {
        s["name"]: s["site_hint"] for s in client.get("/systems", params={"tenant_id": tid}).json()
    }
    assert hints["fwc11-000-infrz"]["code"] == "000" and hints["apic-01"] is None

    # Ohne Standort am APIC: Geraete ohne Namens-Standort nehmen den einzigen physischen
    client.patch(f"/systems/{apic}", json={"site_id": zrh["id"]}, headers=ADMIN)
    devices = {
        d["name"]: d for d in client.get("/inventory/devices", params={"tenant_id": tid}).json()
    }
    assert devices["leaf-101"]["site"]["code"] == "chzrh1"
    assert devices["leaf-101"]["site_from"] == "system"


def test_netbox_standort_gruppen_und_kommentar(session_factory):
    s = session_factory()
    t = Tenant(name="Kunde A", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.flush()
    zrh = Site(tenant_id=t.id, code="chzrh1", kind="physical", name="Zürich RZ1")
    bsl = Site(tenant_id=t.id, code="chbsl1", kind="physical", name="Basel RZ2")
    v0 = Site(tenant_id=t.id, code="000", kind="virtual", name="Stretched DC")
    s.add_all([zrh, bsl, v0])
    s.flush()
    from app.store import sites

    sites.set_physical(s, v0, [zrh, bsl])
    target = SourceSystem(tenant_id=t.id, name="nb", platform="netbox", base_url="http://nb")
    s.add(target)
    s.flush()

    w = netbox_sync.build_wants(s, t, target, [])
    groups = {k: w.get("site_group", k).fields["name"] for k in w.keys_of("site_group")}
    assert groups == {
        "standorte-virtuell": "Virtuelle Standorte",
        "standorte-physisch": "Physische Standorte",
    }
    virtual = w.get("site", "000")
    assert virtual.refs["group"] == ("site_group", "standorte-virtuell")
    assert virtual.fields["comments"] == (
        "Virtueller Standort 000 von Kunde A. Läuft an den physischen Standorten: "
        "chbsl1 (Basel RZ2), chzrh1 (Zürich RZ1). (Automato)"
    )
    physical = w.get("site", "chzrh1")
    assert physical.refs["group"] == ("site_group", "standorte-physisch")
    assert "Virtuelle Standorte hier: 000 (Stretched DC)" in physical.fields["comments"]
    s.close()
