"""Netzinventar je Quellsystem: Einspielen, Ersetzen je Lauf, Loeschen (ADR 0010)."""

from app.models import (
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    IpamType,
    Platform,
    SourceSystem,
    Tenant,
)
from app.models.tenant import DnsType

TOKEN = {"X-Internal-Token": "dev-only-token"}

INVENTORY = {
    "devices": [
        {"name": "leaf-101", "role": "leaf", "model": "N9K", "serial": "S1", "node_id": "101"}
    ],
    "interfaces": [
        {
            "device": "leaf-101",
            "name": "VPC_srv01",
            "kind": "lag",
            "mode": "tagged",
            "tagged_vlans": [110],
            "epgs": ["TE/AP/EG_web"],
        },
        {
            "device": "leaf-101",
            "name": "eth1/10",
            "kind": "physical",
            "lag": "VPC_srv01",
            "neighbor": "srv-web01",
            "neighbor_port": "ens192",
        },
    ],
    "vlans": [{"vid": 110, "name": "EG_web", "epgs": ["TE/AP/EG_web"]}],
    "subnets": [
        {
            "prefix": "10.20.0.0/24",
            "gateway": "10.20.0.1/24",
            "vrf": "TE:VRF",
            "vlan": 110,
            "context": "bd",
            "context_name": "BD_app",
            "aci_tenant": "TE",
            "epg": "EG_web",
            "bd": "BD_app",
        }
    ],
    "vrfs": ["TE:VRF"],
}


def _setup(session_factory, monkeypatch):
    class NoSoT:
        name = "netbox"

        def search_by_ip(self, ip):
            return []

        def get_by_name(self, name):
            return None

    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: NoSoT())
    s = session_factory()
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.flush()
    apic = SourceSystem(tenant_id=t.id, name="apic-01", platform=Platform.CISCO_ACI, base_url="")
    s.add(apic)
    s.commit()
    ids = t.id, apic.id
    s.close()
    return ids


def _deliver(client, tid, sid, inventory):
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    result = {"status": "completed", "inventory": inventory}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _counts(session_factory):
    s = session_factory()
    out = [s.query(m).count() for m in (InvDevice, InvInterface, InvVlan, InvSubnet, InvVrf)]
    s.close()
    return out


def test_inventar_wird_eingespielt_und_je_lauf_ersetzt(client, session_factory, monkeypatch):
    tid, sid = _setup(session_factory, monkeypatch)
    _deliver(client, tid, sid, INVENTORY)
    assert _counts(session_factory) == [1, 2, 1, 1, 1]
    s = session_factory()
    vpc = s.query(InvInterface).filter_by(name="VPC_srv01").one()
    assert vpc.tagged_vlans == [110] and vpc.epgs == ["TE/AP/EG_web"] and vpc.tenant_id == tid
    member = s.query(InvInterface).filter_by(name="eth1/10").one()
    assert (member.lag, member.neighbor) == (
        "VPC_srv01",
        "srv-web01",
    ) and member.tagged_vlans is None
    s.close()

    _deliver(client, tid, sid, {**INVENTORY, "interfaces": INVENTORY["interfaces"][:1]})
    assert _counts(session_factory) == [1, 1, 1, 1, 1]  # ersetzt, nicht angehaengt


def test_lauf_ohne_inventar_laesst_bestand_stehen(client, session_factory, monkeypatch):
    tid, sid = _setup(session_factory, monkeypatch)
    _deliver(client, tid, sid, INVENTORY)
    _deliver(client, tid, sid, None)  # aelterer Collector ohne Inventar
    assert _counts(session_factory) == [1, 2, 1, 1, 1]


def test_quellsystem_loeschen_entfernt_inventar(client, session_factory, monkeypatch):
    tid, sid = _setup(session_factory, monkeypatch)
    _deliver(client, tid, sid, INVENTORY)
    assert client.delete(f"/systems/{sid}", headers={"X-Dev-Roles": "admin"}).status_code == 200
    assert _counts(session_factory) == [0, 0, 0, 0, 0]
