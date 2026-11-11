"""ESXi-Hosts im NetBox-Sync, Abschnitt Verkabelung (1.8.0).

Anwender 2026-11-11: „Wird in VMware die Ports und Nachbarschaft nicht ausgelesen von den
Hosts?“ - „Nur wegen den Verbindungen“. Die gelesenen Hosts des vCenters kommen mit Hersteller und
Modell der Hardware und ihren vmnics nach NetBox; Kabel Switch-Port <-> vmnic, die der Host per
CDP/LLDP bestaetigt, ebenso - auch wenn der Switch den Host nicht sieht (ESXi „Zuhören“)."""

from test_netbox_sync import _apply
from test_netbox_sync_cabling_apps import (
    ADMIN,
    _deliver,
    _items,
    _plan,
    _system,
    netbox,  # noqa: F401  (Fixture: NetBox mit Kabeln)
)
from test_vmware_hosts import SWITCH, VCENTER

CABLE_HEARD = "esx01.example.ch/vmnic0~sw-acc-01/Gi1/0/10"  # nur der Host meldet
CABLE_BOTH = "esx02.example.ch/vmnic0~sw-acc-01/Gi1/0/12"  # beide melden


def _setup(client, netbox) -> dict:  # noqa: F811
    netbox.add("dcim/sites", {"name": "Zürich", "slug": "zrh", "status": "active"})
    tid = client.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=ADMIN).json()
    tid = tid["id"]
    client.post(f"/tenants/{tid}/sites", json={"code": "ZRH", "name": "Zürich"}, headers=ADMIN)
    ids = {"tid": tid}
    ids["sw"] = _system(client, tid, name="Switches A", platform="cisco_switch",
                        read_scope="10.0.0.11", username="ro", password="x")  # fmt: skip
    ids["vc"] = _system(client, tid, name="vc-01", platform="vmware",
                        base_url="https://vc.example.ch", username="ro", password="x")  # fmt: skip
    ids["nb"] = _system(client, tid, name="netbox", platform="netbox",
                        base_url="https://nb.example.ch", token="nb-read",
                        write_token="nb-write")  # fmt: skip
    _deliver(client, tid, ids["sw"], "cisco_switch", SWITCH)
    _deliver(client, tid, ids["vc"], "vmware", VCENTER)
    return ids


def test_esxi_hosts_mit_vmnics_und_kabeln(client, netbox):  # noqa: F811
    ids = _setup(client, netbox)
    plan = _plan(client, ids, sections=["cabling"])
    creates = {(i["kind"], i["key"]) for i in _items(client, plan["id"], action="create")}
    assert {("device", "esx01.example.ch"), ("device", "esx02.example.ch"),
            ("manufacturer", "dell-inc"), ("manufacturer", "hpe"),
            ("device_type", "poweredge r650"), ("device_role", "esxi"),
            ("interface", "esx01.example.ch/vmnic0"), ("interface", "esx01.example.ch/vmnic3"),
            ("cable", CABLE_HEARD), ("cable", CABLE_BOTH)} <= creates  # fmt: skip
    # esx09 kennt Automato nur als Host einer VM: kein Geraet, kein Kabel
    assert not [k for _kind, k in creates if "esx09" in k]
    assert not any(k == "virtual_machine" for k, _ in creates)  # VMs: anderer Abschnitt

    assert _apply(client, plan["id"])["status"] == "applied"
    host = netbox.find("dcim/devices", name="esx01.example.ch")
    assert host["role"]["slug"] == "esxi" and host["serial"] == "SN-ESX01"
    assert "Automato: gelesen aus vc-01" in host["comments"]
    dtype = netbox.find("dcim/device-types", slug="poweredge-r650")
    assert dtype["manufacturer"]["slug"] == "dell-inc"
    cables = {c["description"] for c in netbox.objects("dcim/cables")}
    assert "Automato: CDP über den ESXi-Host bestätigt" in cables
    assert "Automato: CDP beidseitig" in cables

    again = _plan(client, ids, sections=["cabling"])  # stabil
    assert again["totals"]["create"] == again["totals"]["update"] == 0, again["totals"]
    # Hosts gehoeren zum Abschnitt Verkabelung: ohne ihn bleiben sie, auch mit „Löschen zulassen“
    other = _plan(client, ids, sections=["inventory"], allow_delete=True)
    gone = {i["key"] for i in _items(client, other["id"], action="delete")}
    assert not [k for k in gone if "esx0" in k], gone


def test_esxi_host_ohne_hersteller_nur_hinweis(client, netbox):  # noqa: F811
    ids = _setup(client, netbox)
    vc = {**VCENTER, "virtual": {**VCENTER["virtual"], "hosts": [
        {**VCENTER["virtual"]["hosts"][1], "vendor": None}]}}  # fmt: skip
    _deliver(client, ids["tid"], ids["vc"], "vmware", vc)
    plan = _plan(client, ids, sections=["cabling"], headers=ADMIN)
    assert (
        "ESXi-Host esx02.example.ch (vc-01): Hersteller nicht gelesen – ohne Gerätetyp mit Ports"
        " und Kabeln nicht übernommen." in plan["warnings"]
    )
    keys = {i["key"] for i in _items(client, plan["id"], action="create")}
    assert "esx02.example.ch" not in keys and CABLE_BOTH not in keys
