"""Beispieldaten fuer die UI-Tests - aus denselben Testdaten wie die Orchestrator-Tests.

Ein Mandant „Kunde A" (ID 1) mit DDI (fuehrend), fuenf FortiGates (eine mit gleichnamigen
IPv4/IPv6-Objekten und Regelpaaren, eine mit Virtual Server, eine mit zwei VDOMs), einem APIC,
virtuellen und physischen Standorten, NAT/VIPs, Gruppen, Diensten mit Namensschema-Befunden,
einem ACI-Contract ohne Consumer, einem vCenter mit einer VM hinter H_web01 und einer
Applikation (AP mit EPG und VM). Alles ueber die oeffentliche bzw. interne API wie im Betrieb.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "orchestrator" / "tests"))
import test_findings_explain as fe  # noqa: E402
import test_ip_family as ipf  # noqa: E402
import test_search_nat as sn  # noqa: E402
import test_services as sv  # noqa: E402
import test_sites as st  # noqa: E402
import test_vdom as vd  # noqa: E402
import test_virtual_server as vs  # noqa: E402

TOKEN = {"X-Internal-Token": "dev-only-token"}


def run(app) -> None:
    c = TestClient(app)

    def ok(r, code=200):
        assert r.status_code == code, r.text
        return r.json()

    tid = ok(c.post("/tenants", json={"name": "Kunde A", "code": "KA"}), 201)["id"]

    def system(**body):
        return ok(c.post("/systems", json={"tenant_id": tid, **body}), 201)["id"]

    def run_result(sid, platform, **result):
        body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
        run_id = ok(c.post("/jobs/collect", json=body), 202)["run_id"]
        res = {"status": "completed", **result}
        r = ok(c.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=res))
        assert r["status"] == "completed", r

    # Standorte: physisch zuerst, virtuell mit Zuordnung
    zrh = ok(c.post(f"/tenants/{tid}/sites", json={"code": "chzrh1", "name": "Zürich RZ1"}), 201)
    bsl = ok(c.post(f"/tenants/{tid}/sites", json={"code": "chbsl1", "name": "Basel RZ2"}), 201)
    ok(
        c.post(
            f"/tenants/{tid}/sites",
            json={
                "code": "000",
                "name": "Stretched DC",
                "physical_site_ids": [zrh["id"], bsl["id"]],
            },
        ),
        201,
    )
    ok(c.post(f"/tenants/{tid}/sites", json={"code": "001", "name": "RZ1 lokal"}), 201)

    # DDI als fuehrendes IPAM
    ddi = system(
        name="ddi-01", platform="infoblox", base_url="https://ddi.example.ch", username="ro",
        password="e2e",
    )  # fmt: skip
    records, seen = [], set()
    for r in sn.DDI + fe.DDI:
        if (r["kind"], r["value"]) not in seen:
            seen.add((r["kind"], r["value"]))
            records.append(r)
    body = {"platform": "infoblox", "tenant_id": tid, "source_system_id": ddi}
    run_id = ok(c.post("/jobs/collect", json=body), 202)["run_id"]
    c.post(
        f"/internal/collector-runs/{run_id}/references", headers=TOKEN, json={"records": records}
    )
    res = {"status": "completed", "reference_count": len(records)}
    ok(c.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=res))

    # FortiGate 1: Regeln mit Gruppen, SNAT/VIP, Befunde zu Namen und IPAM
    objects = {o["name"]: o for o in sn.OBJECTS + fe.OBJECTS + st.OBJECTS}
    rules = sn.RULES + [{**r, "external_id": f"1{r['external_id']}"} for r in fe.RULES]
    fw = system(name="fwc11-000-infrz", platform="fortigate", base_url="https://fw-01.example.ch")
    run_result(fw, "fortigate", rules=rules, objects=list(objects.values()), nat_rules=sn.NAT)

    # FortiGate 2: Dienste mit Attributen, Internet Service, negiertes Ziel
    fw2 = system(name="fw-02", platform="fortigate", base_url="https://fw-02.example.ch")
    run_result(fw2, "fortigate", rules=sv.RULES, objects=sv.OBJECTS)

    # FortiGate 3: IPv4/IPv6 getrennt - gleichnamige Objekte, Regelpaare (gleich/aehnlich)
    fw3 = system(name="fw-03", platform="fortigate", base_url="https://fw-03.example.ch")
    ports = [  # Ports je Geraet: VLAN-Subinterface getaggt ueber den Parent (0.11.4/0.12.0)
        {"device": "fw-03", "name": "internal", "kind": "physical", "mode": "tagged",
         "tagged_vlans": [10]},
        {"device": "fw-03", "name": "srv-vl10", "kind": "vlan", "mode": "tagged",
         "tagged_vlans": [10], "parent": "internal"},
        {"device": "fw-03", "name": "wan1", "kind": "physical"},
    ]  # fmt: skip
    inventory = {"devices": [{"name": "fw-03", "role": "firewall"}], "interfaces": ports}
    run_result(fw3, "fortigate", rules=ipf.RULES, objects=ipf.OBJECTS, inventory=inventory)

    # FortiGate 4: Virtual Server mit Real Servern; Regel ins BD-Subnetz der APIC (Regeln je
    # Subnetz)
    fw4 = system(name="fw-04", platform="fortigate", base_url="https://fw-04.example.ch")
    net_rule = {**vs.RULES[0], "external_id": "2", "name": "to-web-net",
                "sources": ["10.9.0.0/16"], "destinations": ["10.20.0.0/24"],
                "source_refs": [], "destination_refs": []}  # fmt: skip
    run_result(fw4, "fortigate", rules=[*vs.RULES, net_rule], objects=vs.OBJECTS, nat_rules=vs.NAT)

    # FortiGate 5: zwei VDOMs (root, kunde-a) mit gleichen Policy-IDs, Objekt- und VIP-Namen
    fw5 = system(name="fw-05", platform="fortigate", base_url="https://fw-05.example.ch")
    ports = [{**i, "device": "fw-05"} for i in vd.RESULT["inventory"]["interfaces"]]
    inventory = {"devices": [{"name": "fw-05", "role": "firewall"}], "interfaces": ports}
    run_result(
        fw5, "fortigate", rules=vd.RESULT["rules"], objects=vd.RESULT["objects"],
        nat_rules=vd.RESULT["nat_rules"], inventory=inventory,
    )  # fmt: skip

    # APIC: Inventar, Filter als Dienste, Contract ohne Consumer
    apic = system(name="apic-01", platform="cisco_aci", base_url="https://apic.example.ch")
    filters = [
        {"name": "TE/FT_https", "category": "service", "obj_type": "vzFilter",
         "values": ["tcp/443"],
         "attrs": {"tenant": "TE", "dn": "uni/tn-TE/flt-FT_https",
                   "entries": [{"name": "https", "etherT": "ip", "prot": "tcp",
                                "dFromPort": "443", "dToPort": "443", "stateful": "yes"}]}},
        {"name": "TE/web443", "category": "service", "obj_type": "vzFilter", "values": ["tcp/443"]},
    ]  # fmt: skip
    contracts = [
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE/brc-CT_db/SJ_1",
            "name": "CT_db:SJ_1",
            "action": "allow",
            "sources": [],
            "destinations": ["epg:uni/tn-TE/ap-AP/epg-EG_db"],
            "services": ["tcp/443"],
            "service_refs": ["TE/FT_https"],
            "raw": {"tenant": "TE", "kind": "contract"},
        }
    ]
    epgs = [{"name": "EG_db", "dn": "uni/tn-TE/ap-AP/epg-EG_db", "tenant": "TE", "ap": "AP",
             "vrf": "VRF1"}]  # fmt: skip
    # Leaf-Port mit vielen VLANs und EPGs: in der Liste gekuerzt, alles im Detail (0.13.0)
    trunk = {"device": "leaf-101", "name": "eth1/1", "kind": "physical", "mode": "tagged",
             "tagged_vlans": [*range(100, 121), 300, 305, 310],
             "epgs": ["TE/AP/EG_db", "TE/AP/EG_web", "TE/AP/EG_app"],
             "neighbor": "esx01-chzrh1", "neighbor_port": "vmnic0"}  # fmt: skip
    inventory = {**st.INVENTORY, "interfaces": [trunk]}
    run_result(apic, "cisco_aci", rules=contracts, objects=filters, inventory=inventory, epgs=epgs)

    # vCenter: VM hinter H_web01 (10.10.10.5) an der Portgruppe der EPG EG_db (ACI-VMM-Name)
    vc = system(
        name="vc-01", platform="vmware", base_url="https://vc.example.ch", username="ro",
        password="e2e",
    )  # fmt: skip
    nic = {
        "name": "Network adapter 1",
        "mac": "00:50:56:aa:00:01",
        "portgroup": "TE|AP|EG_db",
        "switch": "DVS-ACI-A",
        "vlan": 1107,
        "connected": True,
        "ips": ["10.10.10.5"],
    }
    vm = {
        "name": "web01-007-ka",
        "power_state": "poweredOn",
        "guest_os": "Ubuntu Linux (64-bit)",
        "datacenter": "DC-A",
        "cluster": "clu01-chzrh1",
        "host": "esx01-chzrh1.example.ch",
        "vcpus": 2,
        "memory_mb": 4096,
        "disk_gb": 40.0,
        "nics": [nic],
    }
    groups = [{"name": "TE|AP|EG_db", "vlan": 1107, "vm_count": 1}]  # fmt: skip
    run_result(vc, "vmware", virtual={"vms": [vm], "portgroups": groups})

    # Applikation (0.14.0): AP TE/AP mit der EPG EG_db und der VM dahinter
    app = {
        "name": "Webshop",
        "external_name": "Shop Kunde A",
        "internal_name": "webshop-ka",
        "purpose": "prod",
        "code": "007",
        "members": [{"kind": "ap", "ref": "TE/AP"}],
    }
    ok(c.post(f"/applications?tenant_id={tid}", json=app), 201)
