"""Beispieldaten fuer die UI-Tests - aus denselben Testdaten wie die Orchestrator-Tests.

Ein Mandant „Kunde A" (ID 1) mit DDI (fuehrend), acht FortiGates (eine mit gleichnamigen
IPv4/IPv6-Objekten und Regelpaaren, eine mit Virtual Server und Routing nur in root, eine mit
zwei VDOMs und Routing, zwei parallele mit Session-Sync, eine mit LAN/DMZ/Internet und
Routing fuer die Relevanz in der Doku), einem APIC,
virtuellen und physischen Standorten, NAT/VIPs, Gruppen, Diensten mit Namensschema-Befunden,
einem ACI-Contract ohne Consumer, einem vCenter mit einer VM hinter H_web01 und einer
Applikation (AP mit EPG und VM). Alles ueber die oeffentliche bzw. interne API wie im Betrieb.
"""

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "orchestrator" / "tests"))
import test_findings_explain as fe  # noqa: E402
import test_ip_family as ipf  # noqa: E402
import test_parallel as par  # noqa: E402
import test_paths as tp  # noqa: E402
import test_policy_check as pc  # noqa: E402
import test_routing as rt  # noqa: E402
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
    # Standardprofil „default“ wie nach den Migrationen (Namensprofile, FT_ als Service-Präfix)
    ok(c.post("/naming-profiles", json={"customer": "default", "name": "default",
                                        "with_defaults": True}), 201)  # fmt: skip

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
    # dazu die Namen der Provider-Nachbarn (Weg fuer IP, BGP-Nachbarn)
    isp = [r for r in tp.DDI if r["value"].startswith("198.51.100.")]
    for r in sn.DDI + fe.DDI + isp:
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
    # 0.79.0: VIP der FortiGate vor dem Virtual Service vs-shop des NSX ALB (10.10.10.80)
    lb_vip = {"source_platform": "fortigate", "external_id": "V-EXT_lb.example.ch",
              "name": "V-EXT_lb.example.ch", "ext_ip": "203.0.113.30", "mapped_ip": "10.10.10.80",
              "raw": {"extintf": "any"}}  # fmt: skip
    run_result(fw, "fortigate", rules=rules, objects=list(objects.values()),
               nat_rules=[*sn.NAT, lb_vip])  # fmt: skip

    # FortiGate 2: Dienste mit Attributen, Internet Service, negiertes Ziel; dazu eine
    # dynamische Adresse aus dem SDN-Connector (ACI über FortiManager, 0.36.0)
    fw2 = system(name="fw-02", platform="fortigate", base_url="https://fw-02.example.ch")
    sdn = {"sub-type": "sdn", "sdn": "aci-fmg"}
    dynamic = {"name": "TE_web_epg", "category": "address", "obj_type": "dynamic",
               "values": ["dynamic:TE_web_epg"], "attrs": sdn}  # fmt: skip
    run_result(fw2, "fortigate", rules=sv.RULES, objects=[*sv.OBJECTS, dynamic])

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
                "source_refs": [], "destination_refs": [], "vdom": "root"}  # fmt: skip
    # nur VDOM root: im Routing „die Firewall“ (0.17.0)
    routing4 = json.loads(json.dumps(tp.FW2["routing"]).replace('"fw-02"', '"fw-04"'))
    run_result(
        fw4, "fortigate", rules=[*vs.RULES, net_rule], objects=vs.OBJECTS, nat_rules=vs.NAT,
        routing=routing4,
    )  # fmt: skip

    # FortiGate 5: zwei VDOMs (root, kunde-a) mit gleichen Policy-IDs, Objekt- und VIP-Namen
    fw5 = system(name="fw-05", platform="fortigate", base_url="https://fw-05.example.ch")
    # dazu Routing, BGP, HA und Policy-Routen (ADR 0019 Stufe 2)
    ports = [
        {**i, "device": "fw-05"}
        for i in vd.RESULT["inventory"]["interfaces"] + rt.FW["inventory"]["interfaces"]
    ]
    inventory = {"devices": [{"name": "fw-05", "role": "firewall"}], "interfaces": ports}
    routing = json.loads(json.dumps(rt.FW["routing"]).replace('"fw-01"', '"fw-05"'))
    # Verlauf des Routings (ADR 0030): ein Lauf davor mit anderem Stand - Default nur ueber
    # wan1, BGP-Nachbar Idle, dazu 10.98.0.0/16; der Lauf danach ist der Stand der Tests
    before = json.loads(json.dumps(routing))
    default = next(
        r
        for r in before["routes"]
        if r["context"] == "root" and r["prefix"] == "0.0.0.0/0" and r["table"] == "rib"
    )
    default["next_hops"] = default["next_hops"][:1]
    before["peers"][0]["state"] = "Idle"
    before["routes"].append({"node": "fw-05", "context": "root", "prefix": "10.98.0.0/16",
                             "table": "static", "protocol": "static", "distance": 10,
                             "next_hops": [{"ip": "10.20.0.254", "interface": "srv"}],
                             "attrs": {"seq-num": 9}})  # fmt: skip
    run_result(fw5, "fortigate", inventory=inventory, routing=before)
    run_result(
        fw5, "fortigate", rules=vd.RESULT["rules"], objects=vd.RESULT["objects"],
        nat_rules=vd.RESULT["nat_rules"], inventory=inventory, routing=routing,
    )  # fmt: skip

    # FortiGate 6 und 7: parallele Firewalls (Session-Sync, gleiche Policies) - noch kein
    # Verbund, der UI-Test fasst sie per Vorschlag zusammen (0.18.0)
    renamed = json.dumps([par.FWA, par.FWB])
    for old, new in (("H_web01", "H_par01"), ("H_web02", "H_par02"), ("N_clients", "N_par"),
                     ('"fw-a"', '"fw-06"'), ('"fw-b"', '"fw-07"'), ('"web"', '"par-web"'),
                     ('"api"', '"par-api"'), ('"mail"', '"par-mail"'), ('"db"', '"par-db"'),
                     ('"nur-a"', '"par-nur-06"')):  # fmt: skip
        renamed = renamed.replace(old, new)
    for name, result in zip(("fw-06", "fw-07"), json.loads(renamed), strict=True):
        sid = system(name=name, platform="fortigate", base_url=f"https://{name}.example.ch")
        run_result(sid, "fortigate", **{k: v for k, v in result.items() if k != "status"})

    # FortiGate 8: LAN, DMZ und Internet mit Zonen und Routing - Regeln auf dem Weg, Relevanz in
    # der Doku (0.24.0: „lan -> internet, alles“ erfasst den DMZ-Server nur ueber any); Internet
    # ueber 192.0.2.1, nicht ueber die Provider von fw-05
    raw8 = json.dumps(pc.FW).replace('"fw-r"', '"fw-08"').replace("198.51.100.", "192.0.2.")
    fw8 = system(name="fw-08", platform="fortigate", base_url="https://fw-08.example.ch")
    run_result(fw8, "fortigate", **{k: v for k, v in json.loads(raw8).items() if k != "status"})

    # FortiGate 9 und 10: Verbund „Süd“ (Session-Sync-Paar), beide am selben LAN und Servernetz -
    # im Weg ein Hop mit beiden Firewalls (0.29.0); die Policy-Route 1 hat je Firewall ein anderes
    # Gateway (Abweichung im Verbund, 0.72.0); Policy 5 zum Portal erlaubt fw-09, fw-10 sperrt -
    # Wege uneinheitlich (0.73.0, Befund der Applikation „Portal Süd“)
    south = []
    for name, own in (("fw-09", 2), ("fw-10", 3)):
        portal = {"source_platform": "fortigate", "external_id": "5", "name": "portal-https",
                  "action": "allow" if own == 2 else "deny", "sources": ["10.88.0.6"],
                  "destinations": ["10.89.0.8"], "services": ["tcp/443"], "vdom": "root",
                  "family": "v4",
                  "raw": {"policyid": 5, "srcintf": [{"name": "lan"}],
                          "dstintf": [{"name": "srv"}], "status": "enable"}}  # fmt: skip
        sid = system(name=name, platform="fortigate", base_url=f"https://{name}.example.ch")
        south.append(sid)
        nets = (
            ("lan", "10.88.0.0/24", f"10.88.0.{own}/24"),
            ("srv", "10.89.0.0/24", f"10.89.0.{own}/24"),
        )
        run_result(
            sid, "fortigate", rules=[portal],
            inventory={"devices": [{"name": name, "role": "firewall"}],
                       "interfaces": [{"device": name, "name": i, "ips": [ip], "vdom": "root"}
                                      for i, _p, ip in nets]},
            routing={"contexts": [{"node": name, "context": "root"}],
                     "routes": [{"node": name, "context": "root", "prefix": p,
                                 "protocol": "connected", "next_hops": [{"interface": i}]}
                                for i, p, _ip in nets],
                     "policy_routes": [{"node": name, "context": "root", "seq": 1,
                                        "gateway": f"10.89.0.{250 + own}", "output_device": ["srv"],
                                        "match": {"dst": ["203.0.113.0/24"]}}]},
        )  # fmt: skip
    ok(
        c.post(
            "/systems/parallel-group", json={"tenant_id": tid, "group": "Süd", "system_ids": south}
        )
    )

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
        },
        # externe EPG mit einem Subnetz nur fuer das Routing (0.28.0: ordnet nichts zu)
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE/brc-CT_rt/SJ_1",
            "name": "CT_rt:SJ_1",
            "action": "allow",
            "sources": ["epg:uni/tn-TE/out-L3O_fw/instP-EN_rt"],
            "destinations": ["epg:uni/tn-TE/ap-AP/epg-EG_db"],
            "services": ["tcp/22"],
            "raw": {"tenant": "TE", "kind": "contract"},
        },
    ]
    epgs = [{"name": "EG_db", "dn": "uni/tn-TE/ap-AP/epg-EG_db", "tenant": "TE", "ap": "AP",
             "vrf": "VRF1"},
            {"name": "EN_rt", "kind": "external", "dn": "uni/tn-TE/out-L3O_fw/instP-EN_rt",
             "tenant": "TE", "l3out": "L3O_fw", "vrf": "VRF1", "subnets": ["10.20.0.0/16"],
             "subnet_scopes": {"10.20.0.0/16": "export-rtctrl"}}]  # fmt: skip
    # Leaf-Port mit vielen VLANs und EPGs: in der Liste gekuerzt, alles im Detail (0.13.0)
    trunk = {"device": "leaf-101", "name": "eth1/1", "kind": "physical", "mode": "tagged",
             "tagged_vlans": [*range(100, 121), 300, 305, 310],
             "epgs": ["TE/AP/EG_db", "TE/AP/EG_web", "TE/AP/EG_app"],
             "neighbor": "esx01-chzrh1", "neighbor_port": "vmnic0"}  # fmt: skip
    inventory = {**st.INVENTORY, "interfaces": [trunk]}
    run_result(
        apic, "cisco_aci", rules=contracts, objects=filters, inventory=inventory, epgs=epgs,
        routing=rt.APIC["routing"],
    )  # fmt: skip

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

    # NSX ALB (0.77.0, ADR 0027): Virtual Service vs-shop mit der VIP 10.10.10.80 auf web01
    avi = system(name="avi-01", platform="avi", base_url="https://avi.example.ch", username="ro",
                 password="e2e", read_scope="admin")  # fmt: skip
    avi_vs = {
        "source_platform": "avi", "external_id": "admin|vs-shop|10.10.10.80|443",
        "name": "vs-shop", "ext_ip": "10.10.10.80", "ext_port": "443",
        "mapped_ip": "10.10.10.5", "mapped_port": "8443", "protocol": "tcp",
        "portforward": True, "vip_type": "server-load-balance", "family": "v4",
        "targets": [{"ip": "10.10.10.5", "port": 8443, "status": "active", "pool": "pool-shop"}],
        "attrs": {"platform": "avi", "tenant": "admin", "pool": "pool-shop",
                  "lb_algorithm": "LB_ALGORITHM_ROUND_ROBIN", "snat": "service_engine",
                  "oper_status": "OPER_UP"},
    }  # fmt: skip
    run_result(avi, "avi", nat_rules=[avi_vs])

    # Switches (0.62.0, ADR 0025): zwei Cisco-Switches per SSH; Gi1/0/48 <-> Eth1/2 mit
    # abweichendem Speed und Native VLAN, Uplink zu fw-08 (dmz), ESXi-Host, ein Port zu leaf-101
    sw = system(name="Switches Zürich", platform="cisco_switch", read_scope="10.0.0.11\n10.0.0.2",
                username="ro", password="x")  # fmt: skip

    def port(device, name, **kw):
        return {"device": device, "name": name, "kind": "physical", "status": "connected",
                "enabled": True, "speed": "a-1000", "duplex": "a-full", **kw}  # fmt: skip

    trunk = {"mode": "tagged", "untagged_vlan": 1, "native_vlan": 1, "trunk_vlans": "10,20,30",
             "tagged_vlans": [10, 20, 30]}  # fmt: skip

    def nb(device, p, remote, rport, proto="cdp"):
        return {"device": device, "port": p, "protocol": proto, "remote_name": remote,
                "remote_port": rport}  # fmt: skip

    switches = {
        "devices": [
            {"name": "sw-acc-01", "role": "switch", "model": "C9300-48P", "mgmt_ip": "10.0.0.11",
             "version": "17.9.4a"},
            {"name": "sw-core-01", "role": "switch", "model": "C93180YC-FX",
             "mgmt_ip": "10.0.0.2", "version": "10.3(4a)"},
        ],
        "interfaces": [
            port("sw-acc-01", "Gi1/0/47", lag="Po1", description="Uplink sw-core-01", **trunk),
            port("sw-acc-01", "Gi1/0/48", lag="Po1", description="Uplink sw-core-01", **trunk),
            port("sw-acc-01", "Te1/1/1", mode="access", untagged_vlan=30, speed="10G",
                 duplex="full", description="fw-08 dmz"),
            port("sw-acc-01", "Gi1/0/2", mode="access", untagged_vlan=20, status="notconnect",
                 description="Drucker 2.OG"),
            port("sw-acc-01", "Gi1/0/5", mode="access", untagged_vlan=20,
                 description="Server srv-phys"),
            port("sw-acc-01", "Gi1/0/9", mode="access", untagged_vlan=30, description="DMZ web"),
            port("sw-core-01", "Eth1/5", mode="access", untagged_vlan=20, description="Drucker"),
            port("sw-core-01", "Eth1/1", lag="Po10", speed="1000", duplex="full", **trunk),
            port("sw-core-01", "Eth1/2", lag="Po10", speed="100", duplex="full",
                 **{**trunk, "native_vlan": 99, "untagged_vlan": 99}),
            port("sw-core-01", "Eth1/3", mode="access", untagged_vlan=10, speed="10G",
                 duplex="full", description="esx01 vmnic2"),
            # Spanning Tree (0.65.0): VLAN 30 zum Leaf blockiert (Reserve)
            port("sw-core-01", "Eth1/48", speed="10G", duplex="full", attrs={"stp_blocked": "30"},
                 **trunk),
        ],
        "vlans": [{"vid": 10, "name": "Server"}, {"vid": 20, "name": "Clients"},
                  {"vid": 30, "name": "DMZ"}],
        "neighbors": [
            nb("sw-acc-01", "Gi1/0/47", "sw-core-01", "Ethernet1/1"),
            nb("sw-acc-01", "Gi1/0/48", "sw-core-01", "Ethernet1/2"),
            nb("sw-acc-01", "Te1/1/1", "fw-08", "dmz", "lldp"),
            nb("sw-core-01", "Eth1/1", "sw-acc-01", "GigabitEthernet1/0/47"),
            nb("sw-core-01", "Eth1/2", "sw-acc-01", "GigabitEthernet1/0/48"),
            nb("sw-core-01", "Eth1/3", "esx01-chzrh1.example.ch", "vmnic2", "lldp"),
            nb("sw-core-01", "Eth1/48", "leaf-101", "Ethernet1/48"),
        ],
        # Endpunkte (0.63.0): web01 am ESXi-Host (VLAN 1107 am Access-Port 10 nicht erlaubt ->
        # Befund), ein physischer Server mit IP aus ARP, derselbe auf den Uplinks
        "macs": [
            {"device": "sw-core-01", "port": "Eth1/3", "mac": "00:50:56:aa:00:01", "vlan": 10},
            {"device": "sw-acc-01", "port": "Po1", "mac": "00:50:56:aa:00:01", "vlan": 10},
            {"device": "sw-acc-01", "port": "Gi1/0/5", "mac": "a0:b1:c2:d3:e4:f5", "vlan": 20},
            {"device": "sw-core-01", "port": "Po10", "mac": "a0:b1:c2:d3:e4:f5", "vlan": 20},
            # L2 in der Weg-Suche (0.64.0): Webserver der DMZ an sw-acc-01
            {"device": "sw-acc-01", "port": "Gi1/0/9", "mac": "a0:b1:c2:00:30:10", "vlan": 30},
        ],
        "arps": [{"device": "sw-core-01", "ip": "10.20.0.15", "mac": "a0:b1:c2:d3:e4:f5",
                  "interface": "Vl20"},
                 {"device": "sw-core-01", "ip": "10.30.0.10", "mac": "a0:b1:c2:00:30:10",
                  "interface": "Vl30"},
                 # IPv6-Nachbarn (0.94.0): global und Link-local des physischen Servers
                 {"device": "sw-core-01", "ip": "2001:db8:20::15", "mac": "a0:b1:c2:d3:e4:f5",
                  "interface": "Vl20"},
                 {"device": "sw-core-01", "ip": "fe80::a2b1:c2ff:fed3:e4f5",
                  "mac": "a0:b1:c2:d3:e4:f5", "interface": "Vl20"}],
    }  # fmt: skip
    run_result(sw, "cisco_switch", inventory=switches, message="Switches: 2 gelesen")

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
    portal = {"name": "Portal Süd", "members": [{"kind": "host", "ref": "10.89.0.8"}]}
    ok(c.post(f"/applications?tenant_id={tid}", json=portal), 201)

    # Logische Zonen (0.51.0): LAN, DMZ und Internet an fw-08; LAN -> DMZ gesperrt - die zwei
    # erlaubenden Policies dorthin sind Verstoesse (Befund); Internet ohne die privaten Netze
    def sel(kind, value, exclude=False, source_system_id=None):
        return {"kind": kind, "value": value, "exclude": exclude,
                "source_system_id": source_system_id}  # fmt: skip

    def zone(name, kind, color, selectors):
        body = {"tenant_id": tid, "name": name, "kind": kind, "color": color,
                "selectors": selectors}  # fmt: skip
        return ok(c.post("/zones", json=body), 201)["id"]

    lan = zone("LAN", "internal", "#3b82c4", [sel("fw_zone", "lan", source_system_id=fw8)])
    dmz = zone("DMZ", "dmz", "#d08a1e", [sel("fw_interface", "dmz", source_system_id=fw8)])
    zone("Internet", "external", "#c0392b",
         [sel("prefix", "0.0.0.0/0"), sel("prefix", "10.0.0.0/8", exclude=True)])  # fmt: skip
    # Zonenmodell (0.56.0): fw-08 deklariert die Zone Edge Internet - LAN, DMZ und Internet sind
    # Uebergaenge an ihren Interfaces
    edge = {"tenant_id": tid, "name": "Edge Internet", "kind": "edge", "code": "EINET",
            "selectors": [sel("fw_vdom", "root", source_system_id=fw8)]}  # fmt: skip
    ok(c.post("/zones", json=edge), 201)
    policy = {"tenant_id": tid, "src_zone_id": lan, "dst_zone_id": dmz, "intent": "deny",
              "note": "nur über den Proxy"}  # fmt: skip
    ok(c.put("/zones/policy", json=policy))
