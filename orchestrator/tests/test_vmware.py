"""VMware-Inventar (ADR 0017): Ingest, Portgruppe -> EPG (Name, ohne Praefixe, VLAN), VMs in
Regeln, Suche, EPGs und Doku, Befunde und der NetBox-Sync-Abschnitt `virtualization`."""

import copy

import pytest
from tests.test_netbox_sync import _apply, _items, _tagged, fake_netbox

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
WEB = "uni/tn-TE_ka/ap-AP_007/epg-EG_web01"
DB = "uni/tn-TE_ka/ap-AP_007/epg-EPG_db01"  # anderes Praefix als die Portgruppe
BACKUP = "uni/tn-TE_ka/ap-AP_007/epg-EG_backup"

ACI = {
    "status": "completed",
    "epgs": [
        {"name": "EG_web01", "dn": WEB, "tenant": "TE_ka", "ap": "AP_007", "vrf": "VRF1",
         "subnets": ["10.20.0.1/24"]},
        {"name": "EPG_db01", "dn": DB, "tenant": "TE_ka", "ap": "AP_007", "vrf": "VRF1"},
        {"name": "EG_backup", "dn": BACKUP, "tenant": "TE_ka", "ap": "AP_007", "vrf": "VRF1"},
    ],
    "rules": [
        {"source_platform": "cisco_aci", "external_id": "uni/tn-TE_ka/brc-CT_app/SJ",
         "name": "CT_app:SJ", "action": "allow", "sources": [f"epg:{WEB}"],
         "destinations": [f"epg:{DB}"], "services": ["tcp/5432"], "aci_scope": "context",
         "raw": {"tenant": "TE_ka", "kind": "contract", "dn": "uni/tn-TE_ka/brc-CT_app"}},
    ],
    "inventory": {"vlans": [{"vid": 950, "name": "EG_backup",
                             "epgs": ["TE_ka/AP_007/EG_backup"]}]},
}  # fmt: skip
FW = {
    "status": "completed",
    "rules": [
        {"source_platform": "fortigate", "external_id": "1", "name": "web-in", "action": "allow",
         "sources": ["10.9.0.0/16"], "destinations": ["10.20.0.5"], "services": ["tcp/443"],
         "source_refs": ["N_clients"], "destination_refs": ["H_web01"], "service_refs": ["HTTPS"],
         "family": "v4"},
    ],
    "objects": [
        {"name": "N_clients", "category": "address", "family": "v4", "values": ["10.9.0.0/16"]},
        {"name": "H_web01", "category": "address", "family": "v4", "values": ["10.20.0.5"]},
    ],
}  # fmt: skip


def _nic(name, pg, vlan, ips, mac):
    return {"name": name, "mac": mac, "portgroup": pg, "switch": "DVS-ACI-A", "vlan": vlan,
            "connected": True, "ips": ips}  # fmt: skip


VCENTER = {
    "status": "completed",
    "virtual": {
        "vms": [
            {"name": "web01-007-ka", "uuid": "u-1", "power_state": "poweredOn",
             "guest_os": "Ubuntu Linux (64-bit)", "hostname": "web01", "datacenter": "DC-A",
             "cluster": "clu01-chzrh1", "host": "esx01-chzrh1.example.ch", "vcpus": 2,
             "memory_mb": 4096, "disk_gb": 40.0,
             "nics": [_nic("Network adapter 1", "TE_ka|AP_007|EG_web01", 1107, ["10.20.0.5"],
                           "00:50:56:aa:00:01")]},
            {"name": "db01-007-ka", "uuid": "u-2", "power_state": "poweredOff",
             "datacenter": "DC-A", "cluster": "clu01-chzrh1", "vcpus": 4, "memory_mb": 8192,
             "nics": [_nic("Network adapter 1", "TE_ka|AP_007|EG_db01", 1108, ["10.20.1.7"],
                           "00:50:56:aa:00:02"),
                      _nic("Network adapter 2", "PG_backup", 950, ["10.95.0.9"],
                           "00:50:56:aa:00:03")]},
            {"name": "tpl-ubuntu", "template": True, "datacenter": "DC-A",
             "cluster": "clu01-chzrh1", "nics": []},
        ],
        "portgroups": [
            {"name": "TE_ka|AP_007|EG_web01", "vlan": 1107, "vm_count": 1},
            {"name": "TE_ka|AP_007|EG_db01", "vlan": 1108, "vm_count": 1},
            {"name": "PG_backup", "kind": "standard", "switch": "vSwitch1", "vlan": 950,
             "vm_count": 1},
            {"name": "TE_ka|AP_007|EG_gone", "vlan": 1109},
        ],
    },
}  # fmt: skip
DDI = [
    {"kind": "prefix", "value": "10.20.0.0/24", "description": "Web"},
    {"kind": "prefix", "value": "10.20.1.0/24", "description": "DB"},
    {"kind": "host", "value": "10.20.0.5", "dns_name": "web01.example.ch"},
    {"kind": "host", "value": "10.95.0.9", "dns_name": "db01-bkp.example.ch"},
]


def _system(client, tid, name, platform, **extra) -> int:
    body = {"tenant_id": tid, "name": name, "platform": platform, **extra}
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _run(client, tid, sid, platform) -> str:
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    return client.post("/jobs/collect", json=body).json()["run_id"]


def _deliver(client, tid, sid, platform, result) -> None:
    run_id = _run(client, tid, sid, platform)
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _seed(client, *, netbox_target=False) -> dict:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = {"tid": tid}
    ids["ddi"] = _system(client, tid, "ddi", "infoblox", username="u", password="p")
    run_id = _run(client, tid, ids["ddi"], "infoblox")
    client.post(
        f"/internal/collector-runs/{run_id}/references", headers=TOKEN, json={"records": DDI}
    )
    done = {"status": "completed", "reference_count": len(DDI)}
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=done)
    ids["vc"] = _system(
        client,
        tid,
        "vc-01",
        "vmware",
        base_url="https://vc.example.ch",
        username="ro",
        password="x",
    )
    _deliver(client, tid, ids["vc"], "vmware", VCENTER)  # vor dem APIC: Zuordnung im Abgleich
    ids["apic"] = _system(client, tid, "apic-01", "cisco_aci", base_url="https://apic")
    _deliver(client, tid, ids["apic"], "cisco_aci", ACI)
    ids["fw"] = _system(client, tid, "fw-01", "fortigate", base_url="https://fw")
    _deliver(client, tid, ids["fw"], "fortigate", FW)
    if netbox_target:
        ids["nb"] = _system(
            client,
            tid,
            "netbox",
            "netbox",
            base_url="https://nb.kunde.lan",
            token="nb-read",
            write_token="nb-write",
        )
    return ids


def test_vms_portgruppen_und_zuordnung_zur_epg(client):
    ids = _seed(client)
    vms = client.get("/vms", params={"tenant_id": ids["tid"]}).json()
    assert vms["total"] == 3
    rows = {r["name"]: r for r in vms["rows"]}
    web = rows["web01-007-ka"]
    (nic,) = web["nics"]
    assert (nic["epg"], nic["epg_match"]) == ("TE_ka/AP_007/EG_web01", "name")
    assert nic["ips"] == [{"ip": "10.20.0.5", "dns": ["web01.example.ch"], "in_ipam": True}]
    db = {n["portgroup"]: n for n in rows["db01-007-ka"]["nics"]}
    # ohne Praefixe gleich (EG_db01 ~ EPG_db01); PG_backup ueber das eindeutige VLAN 950
    assert (db["TE_ka|AP_007|EG_db01"]["epg"], db["TE_ka|AP_007|EG_db01"]["epg_match"]) == (
        "TE_ka/AP_007/EPG_db01",
        "name",
    )
    assert (db["PG_backup"]["epg"], db["PG_backup"]["epg_match"]) == (
        "TE_ka/AP_007/EG_backup",
        "vlan",
    )
    assert db["TE_ka|AP_007|EG_db01"]["ips"][0]["in_ipam"] is False
    search = client.get("/vms", params={"tenant_id": ids["tid"], "q": "db01-bkp"}).json()
    assert [r["name"] for r in search["rows"]] == ["db01-007-ka"]  # DNS-Name aus dem IPAM
    by_epg = client.get("/vms", params={"tenant_id": ids["tid"], "epg": "TE_ka/AP_007/EG_web01"})
    assert [r["name"] for r in by_epg.json()["rows"]] == ["web01-007-ka"]

    groups = {
        g["name"]: g for g in client.get("/portgroups", params={"tenant_id": ids["tid"]}).json()
    }
    assert groups["TE_ka|AP_007|EG_gone"]["epg"] is None
    assert groups["PG_backup"]["kind"] == "standard" and groups["PG_backup"]["epg_match"] == "vlan"


def test_vm_in_regeln_suche_epg_und_doku(client):
    ids = _seed(client)
    tid = ids["tid"]
    page = client.get("/rules", params={"tenant_id": tid, "q": "web01-007-ka"}).json()
    assert [r["name"] for r in page] == ["web-in"]  # Suche findet den VM-Namen
    rule = client.get(f"/rules/{page[0]['id']}").json()
    (dst,) = (e for e in rule["endpoints"] if e["role"] == "dst")
    assert dst["vm"]["name"] == "web01-007-ka" and dst["vm"]["epg"] == "TE_ka/AP_007/EG_web01"
    (ref,) = (r for r in rule["refs"] if r["name"] == "H_web01")
    assert ref["values"][0]["vm"]["name"] == "web01-007-ka"  # auch am Firewall-Objekt

    contract = client.get("/rules", params={"tenant_id": tid, "q": "CT_app"}).json()[0]
    detail = client.get(f"/rules/{contract['id']}").json()
    (src,) = (e for e in detail["endpoints"] if e["role"] == "src")
    assert [v["name"] for v in src["epg"]["vms"]] == ["web01-007-ka"]

    # Doku je EPG nennt die VMs; Doku je Server bringt die EPG ueber die Portgruppe der VM mit
    epg_doc = client.get("/appdoc/systems/EG_web01", params={"tenant_id": tid}).json()
    assert [v["name"] for v in epg_doc["vms"]] == ["web01-007-ka"]
    host_doc = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    assert [v["name"] for v in host_doc["vms"]] == ["web01-007-ka"]
    rows = [r for t in host_doc["tables"] for r in t["rows"]]
    aci = [r for r in rows if r["Description"] == "CT_app:SJ"]
    assert aci and aci[0]["Bezug"].startswith("EPG ")  # kein gelernter Endpunkt noetig


def test_befunde_vm_ohne_ipam_und_portgruppe_ohne_epg(client):
    ids = _seed(client)
    found = client.get("/findings", params={"tenant_id": ids["tid"], "type": "vm_not_in_ipam"})
    (vm,) = found.json()
    assert vm["subject_id"] == "db01-007-ka" and vm["detail"]["ips"] == ["10.20.1.7"]
    assert "10.20.1.7" in vm["message"] and vm["hint"]
    found = client.get("/findings", params={"tenant_id": ids["tid"], "type": "portgroup_no_epg"})
    (pg,) = found.json()
    assert pg["subject_id"] == "TE_ka|AP_007|EG_gone" and pg["detail"]["epg"] == "EG_gone"
    assert pg["severity"] == "info" and pg["message"]


@pytest.fixture
def netbox(monkeypatch):
    return fake_netbox(monkeypatch)


def _plan(client, ids, **opts) -> dict:
    body = {"tenant_id": ids["tid"], "source_system_id": ids["nb"], **opts}
    r = client.post("/netbox-sync/plans", json=body, headers=ADMIN)
    assert r.status_code == 202, r.text
    plan = client.get(f"/netbox-sync/plans/{r.json()['id']}", headers=ADMIN).json()
    assert plan["status"] == "ready", plan
    return plan


def test_netbox_sync_virtualisierung(client, netbox):
    # von Hand gepflegte IP (NetBox als IPAM): nur die fehlende Zuordnung ist ein Konflikt
    netbox.add("ipam/ip-addresses", {"address": "10.20.1.7/16", "description": "von Hand"})
    ids = _seed(client, netbox_target=True)
    plan = _plan(client, ids, sections=["virtualization"])
    assert not netbox.writes
    (conflict,) = _items(client, plan["id"], action="conflict")
    assert conflict["key"] == "10.20.1.7"
    assert [c["field"] for c in conflict["changes"]] == ["assigned_object_id"]
    kinds = {i["kind"] for i in _items(client, plan["id"], action="create", limit=500)}
    assert {"cluster_type", "cluster_group", "cluster", "virtual_machine", "vm_interface"} <= kinds
    assert "device" not in kinds and "prefix" not in kinds  # nur der gewaehlte Abschnitt

    done = _apply(client, plan["id"])
    assert done["status"] == "applied", done
    cluster = netbox.find("virtualization/clusters", name="clu01-chzrh1")
    assert cluster["type"]["slug"] == "vmware-vsphere" and cluster["tenant"]["slug"] == "kunde-a"
    assert cluster["group"]["name"] == "DC-A (Kunde A)" and _tagged(cluster)
    web = netbox.find("virtualization/virtual-machines", name="web01-007-ka")
    assert (web["status"]["value"], web["vcpus"], web["memory"], web["disk"]) == (
        "active",
        2,
        4096,
        40960,
    )
    assert "EPG TE_ka/AP_007/EG_web01" in web["comments"] and web["cluster"]["id"] == cluster["id"]
    db = netbox.find("virtualization/virtual-machines", name="db01-007-ka")
    assert db["status"]["value"] == "offline" and "disk" not in db
    assert netbox.find("virtualization/virtual-machines", name="tpl-ubuntu") is None  # Vorlage
    nic = netbox.find("virtualization/interfaces", name="Network adapter 2")
    assert nic["virtual_machine"]["name"] == "db01-007-ka"
    assert nic["description"] == (
        "MAC 00:50:56:aa:00:03 · Portgruppe PG_backup · VLAN 950 · EPG TE_ka/AP_007/EG_backup"
    )
    ip = netbox.find("ipam/ip-addresses", address="10.20.0.5/24")  # Praefix aus dem IPAM
    web_nic = next(
        i
        for i in netbox.objects("virtualization/interfaces")
        if i["virtual_machine"]["name"] == "web01-007-ka"
    )
    assert ip["assigned_object_type"] == "virtualization.vminterface"
    assert ip["assigned_object_id"] == web_nic["id"] and _tagged(ip)
    manual = netbox.find("ipam/ip-addresses", address="10.20.1.7/16")
    assert manual["description"] == "von Hand" and "assigned_object_id" not in manual
    assert {w[1] for w in netbox.writes} <= {
        "virtualization/cluster-types",
        "virtualization/cluster-groups",
        "virtualization/clusters",
        "virtualization/virtual-machines",
        "virtualization/interfaces",
        "ipam/ip-addresses",
        "extras/tags",
        "tenancy/tenants",
    }

    again = _plan(client, ids, sections=["virtualization"])  # idempotent
    assert again["totals"]["create"] == again["totals"]["update"] == 0
    # ohne den Abschnitt bleiben VMs unangetastet (nicht "veraltet")
    other = _plan(client, ids, sections=["inventory"], allow_delete=True)
    assert not [i for i in _items(client, other["id"], action="delete", limit=500)
                if i["kind"] in ("virtual_machine", "vm_interface", "cluster")]  # fmt: skip


def test_netbox_sync_vm_entfernt_nur_mit_freigabe(client, netbox):
    ids = _seed(client, netbox_target=True)
    assert _apply(client, _plan(client, ids, sections=["virtualization"])["id"])["status"] == (
        "applied"
    )
    gone = copy.deepcopy(VCENTER)
    gone["virtual"]["vms"] = [v for v in gone["virtual"]["vms"] if v["name"] != "db01-007-ka"]
    gone["virtual"]["vms"][0]["vcpus"] = 4
    _deliver(client, ids["tid"], ids["vc"], "vmware", gone)
    plan = _plan(client, ids, sections=["virtualization"])
    (update,) = _items(client, plan["id"], action="update")
    assert update["key"] == "web01-007-ka" and update["changes"] == [
        {"field": "vcpus", "old": 2, "new": 4}
    ]
    obsolete = {i["kind"] for i in _items(client, plan["id"], action="obsolete")}
    assert {"virtual_machine", "vm_interface"} <= obsolete
    plan = _plan(client, ids, sections=["virtualization"], allow_delete=True)
    assert _apply(client, plan["id"])["status"] == "applied"
    assert netbox.find("virtualization/virtual-machines", name="db01-007-ka") is None
    assert netbox.find("virtualization/virtual-machines", name="web01-007-ka")["vcpus"] == 4
    names = {i["name"] for i in netbox.objects("virtualization/interfaces")}
    assert names == {"Network adapter 1"}


def test_portgruppe_im_detail_mit_subnetzen_und_vms(client):
    """Dev-Befund 0.13.0: Portgruppe mit Subnetz (ACI, Inventar, IPAM) und VMs im Detail."""
    ids = _seed(client)
    groups = {
        g["name"]: g for g in client.get("/portgroups", params={"tenant_id": ids["tid"]}).json()
    }
    web = client.get(f"/portgroups/{groups['TE_ka|AP_007|EG_web01']['id']}").json()
    assert web["epg"]["label"] == "TE_ka/AP_007/EG_web01" and web["epg"]["vrf"] == "VRF1"
    (subnet,) = web["subnets"]
    assert subnet["prefix"] == "10.20.0.0/24"
    assert set(subnet["sources"]) == {"ACI-EPG", "IPAM ddi"}  # EPG-Subnetz = IPAM-Netz der VM
    assert subnet["gateway"] == "10.20.0.1/24" and subnet["description"] == "Web"
    (vm,) = web["vms"]
    assert (vm["name"], vm["nic"], vm["power_state"]) == (
        "web01-007-ka",
        "Network adapter 1",
        "poweredOn",
    )
    assert vm["ips"] == [{"ip": "10.20.0.5", "dns": ["web01.example.ch"], "in_ipam": True}]
    gone = client.get(f"/portgroups/{groups['TE_ka|AP_007|EG_gone']['id']}").json()
    assert gone["epg"] is None and gone["subnets"] == [] and gone["vms"] == []
    assert client.get("/portgroups/999999").status_code == 404
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(other)}
    assert client.get(f"/portgroups/{web['id']}", headers=viewer).status_code == 403
