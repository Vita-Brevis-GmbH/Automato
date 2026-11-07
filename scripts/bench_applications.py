# ruff: noqa: E501 - Messskript mit langen Testdaten-Zeilen
"""Leistungsmessung Applikationen (0.74.1, Anwender: Doku laedt 3-5 min, Speichern traege) - nur
lokal.

Legt in einer LEEREN Datenbank einen Mandanten mit APIC (viele Tenants, APs, EPGs, Contracts,
externe EPGs), vCenter (VMs in den Portgruppen der EPGs), FortiGate (Regeln auf die Netze der
EPGs) und DDI an - erzeugte, anonymisierte Daten - und misst Liste, Doku, Verbindungen,
Bearbeiten und Mitglied hinzufuegen einer Applikation. Die Datenbank wird neu angelegt - nie
gegen eine Betriebsdatenbank laufen lassen.

    AUTOMATO_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/automato_bench \\
      uv run --package orchestrator python scripts/bench_applications.py [tenants] [contracts]

Mit BENCH_PROFILE=doc|connections|patch eine Abfrage mit cProfile (die 25 teuersten Aufrufe).
"""

import cProfile
import os
import pstats
import random
import sys
import time

os.environ.setdefault("AUTOMATO_CREDENTIALS_KEY", "bench")  # nur Testdaten

from app.api import jobs  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

N_TENANTS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
N_CONTRACTS = int(sys.argv[2]) if len(sys.argv) > 2 else 100  # je Tenant
N_APS, N_EPGS, N_VMS, N_RULES = 5, 6, 5, 3000
N_ROUTES = int(os.environ.get("BENCH_ROUTES", "20000"))  # FortiGate-RIB
N_CUSTOMERS = int(os.environ.get("BENCH_CUSTOMERS", str(N_TENANTS)))  # je ACI-Tenant ein Kunde
TOKEN = {"X-Internal-Token": "dev-only-token"}
H = {"X-Dev-Roles": "admin"}
rnd = random.Random(7)  # noqa: S311 - reproduzierbare Testdaten, keine Kryptografie

SKIP_SEED = os.environ.get("BENCH_SKIP_SEED") == "1"  # vorhandenen Bestand nur messen
engine = get_engine()
if not SKIP_SEED:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
jobs.celery_app.dispatch_collect = lambda platform, run_id: None
app = create_app()
c = TestClient(app)


def ok(r, code=200):
    if r.status_code != code:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def timed(label, fn):
    t = time.perf_counter()
    out = fn()
    print(f"{label:<48} {(time.perf_counter() - t) * 1000:9.0f} ms", flush=True)
    return out


def system(name, platform, **extra):
    body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x", **extra}
    return ok(c.post("/systems", json=body, headers=H), 201)["id"]


def deliver(sid, platform, result):
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = ok(c.post("/jobs/collect", json=body, headers=H), 202)["run_id"]
    ok(c.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN,
              json={"status": "completed", **result}))  # fmt: skip


def seed():
    global tid
    tid = ok(c.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=H), 201)["id"]
    epgs, vms, groups, rules, records = [], [], [], [], []
    dns = []
    for t in range(N_TENANTS):
        tenant, vrf = f"TE_{t:02d}", f"VRF_{t:02d}"
        l3o = f"L3O_{t:02d}"
        epgs.append({"name": "EN_ext", "kind": "external", "tenant": tenant, "l3out": l3o,
                     "dn": f"uni/tn-{tenant}/out-{l3o}/instP-EN_ext", "vrf": vrf,
                     "subnets": ["172.16.0.0/12"],
                     "subnet_scopes": {"172.16.0.0/12": "import-security"}})  # fmt: skip
        own = []
        for a in range(N_APS):
            ap = f"AP_{a + 1:03d}"  # gleiche AP-Namen in allen Tenants (Vorschlaege je Projekt)
            for e in range(N_EPGS):
                name = f"EPG_{e:02d}"
                dn = f"uni/tn-{tenant}/ap-{ap}/epg-{name}"
                net = f"10.{t}.{a * 10 + e}"
                epgs.append({"name": name, "dn": dn, "tenant": tenant, "ap": ap, "bd": f"BD_{a}_{e}",
                             "vrf": vrf, "bd_subnets": [f"{net}.1/24"],
                             "endpoints": [{"ip": f"{net}.{i + 10}"} for i in range(3)]})  # fmt: skip
                own.append(dn)
                pg = f"{tenant}|{ap}|{name}"
                groups.append({"name": pg, "vm_count": N_VMS})
                for v in range(N_VMS):
                    vm = f"vm{t:02d}{a}{e}{v}-{ap[3:]}"
                    ip = f"{net}.{v + 20}"
                    vms.append({"name": vm, "power_state": "poweredOn",
                                "nics": [{"name": "Network adapter 1", "portgroup": pg,
                                          "connected": True, "ips": [ip]}]})  # fmt: skip
                    records.append({"kind": "host", "value": ip, "dns_name": f"{vm}.example.ch"})
                records.append({"kind": "prefix", "value": f"{net}.0/24", "description": dn})
                dns.append(net)
        for k in range(N_CONTRACTS):
            src, dst = rnd.sample(own, 2)
            if k % 10 == 0:
                dst = f"uni/tn-{tenant}/out-{l3o}/instP-EN_ext"
            rules.append({"source_platform": "cisco_aci",
                          "external_id": f"uni/tn-{tenant}/brc-CT_{k}/SJ_1", "name": f"CT_{k}",
                          "action": "allow", "sources": [f"epg:{src}"],
                          "destinations": [f"epg:{dst}"], "services": [f"tcp/{8000 + k % 50}"],
                          "aci_scope": "context",
                          "raw": {"dn": f"uni/tn-{tenant}/brc-CT_{k}", "name": f"CT_{k}",
                                  "tenant": tenant, "kind": "contract", "scope": "context"}})  # fmt: skip
    # Routing: je VRF zwei Border-Leafs, L3Out zur FortiGate (10.255.<t>.0/29), Default-Route
    # hinaus, die EPG-Netze im Fabric; die FortiGate mit vielen Routen
    contexts, routes, l3outs = [], [], []
    for t in range(N_TENANTS):
        ctx = f"TE_{t:02d}:VRF_{t:02d}"
        hop = [{"ip": f"10.255.{t}.1", "interface": f"vlan-{3000 + t}"}]
        for leaf in ("leaf-101", "leaf-102"):
            contexts.append({"node": leaf, "context": ctx, "kind": "vrf"})
            routes.append({"node": leaf, "context": ctx, "prefix": "0.0.0.0/0", "protocol": "bgp",
                           "next_hops": hop, "distance": 20})  # fmt: skip
            for a in range(N_APS):
                for e in range(N_EPGS):
                    routes.append({"node": leaf, "context": ctx, "prefix": f"10.{t}.{a * 10 + e}.0/24",
                                   "protocol": "static",
                                   "next_hops": [{"ip": "10.0.72.64"}]})  # fmt: skip
        l3outs.append({"tenant": f"TE_{t:02d}", "name": f"L3O_{t:02d}",
                       "dn": f"uni/tn-TE_{t:02d}/out-L3O_{t:02d}", "vrf": ctx,
                       "nodes": [{"pod": "1", "node": "101"}],
                       "paths": [{"path": "topology/pod-1/protpaths-101-102/pathep-[vpc_fw]",
                                  "nodes": ["101", "102"], "interface": "vpc_fw", "kind": "ext-svi",
                                  "vlan": 3000 + t,
                                  "ips": [{"side": "A", "ip": f"10.255.{t}.2/29"}]}],
                       "peers": [{"ip": f"10.255.{t}.1", "asn": 65000}],
                       "ext_epgs": ["EN_ext"]})  # fmt: skip
    apic_inv = {"devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"},
                            {"name": "leaf-102", "role": "leaf", "node_id": "102"}]}  # fmt: skip
    fw_routes = [{"node": "fw-01", "context": "root", "prefix": f"10.{t}.0.0/16", "protocol": "bgp",
                  "next_hops": [{"ip": f"10.255.{t}.2", "interface": f"aci-{t}"}]}
                 for t in range(N_TENANTS)]  # fmt: skip
    fw_routes += [{"node": "fw-01", "context": "root",
                   "prefix": f"192.{168 + i // 65536 % 20}.{i // 256 % 256}.{i % 256}/32",
                   "protocol": "bgp", "next_hops": [{"ip": "10.255.1.254", "interface": "lan"}]}
                  for i in range(N_ROUTES)]  # fmt: skip
    fw_rules = []
    for i in range(N_RULES):
        a, b = rnd.sample(dns, 2)
        src = f"{a}.{rnd.randint(20, 24)}" if i % 3 else f"{a}.0/24"
        fw_rules.append({"source_platform": "fortigate", "external_id": str(i + 1), "name": f"r{i}",
                         "action": "allow", "sources": [src], "destinations": [f"{b}.0/24"],
                         "services": [f"tcp/{443 + i % 20}"], "vdom": "root", "family": "v4",
                         "raw": {"policyid": i + 1, "srcintf": [{"name": "lan"}],
                                 "dstintf": [{"name": "dc"}], "status": "enable"}})  # fmt: skip

    print(f"Bestand: {len(epgs)} EPGs, {len(rules)} Contracts, {len(vms)} VMs, "
          f"{len(fw_rules)} Firewall-Regeln", flush=True)  # fmt: skip
    apic = system("apic-01", "cisco_aci")
    vc = system("vc-01", "vmware", username="ro", password="x")  # noqa: S106 - Testwert
    fw = system("fw-01", "fortigate")
    timed("APIC einlesen", lambda: deliver(apic, "cisco_aci", {
        "epgs": epgs, "rules": rules, "inventory": apic_inv,
        "routing": {"contexts": contexts, "routes": routes, "l3outs": l3outs}}))  # fmt: skip
    timed("vCenter einlesen",
          lambda: deliver(vc, "vmware", {"virtual": {"vms": vms, "portgroups": groups}}))  # fmt: skip
    fw_inv = {"devices": [{"name": "fw-01", "role": "firewall"}],
              "interfaces": [{"device": "fw-01", "name": "lan", "ips": ["10.255.1.1/24"],
                              "vdom": "root"},
                             {"device": "fw-01", "name": "dc", "ips": ["10.254.2.1/24"],
                              "vdom": "root"},
                             *({"device": "fw-01", "name": f"aci-{t}", "ips": [f"10.255.{t}.1/29"],
                                "vdom": "root"} for t in range(N_TENANTS))]}  # fmt: skip
    timed("FortiGate einlesen",
          lambda: deliver(fw, "fortigate", {
              "rules": fw_rules, "inventory": fw_inv,
              "routing": {"contexts": [{"node": "fw-01", "context": "root"}],
                          "routes": fw_routes}}))  # fmt: skip
    print(f"Routing: {len(routes)} ACI-Routen, {len(fw_routes)} FortiGate-Routen", flush=True)
    for k in range(N_CUSTOMERS):
        body = {"tenant_id": tid, "name": f"Kunde {k:02d}",
                "selectors": [{"kind": "aci_tenant", "value": f"TE_{k:02d}"}]}  # fmt: skip
        timed(f"POST /customers ({k})" if k < 2 else "", lambda b=body: ok(
            c.post("/customers", json=b, headers=H), 201)) if k < 2 else ok(
            c.post("/customers", json=body, headers=H), 201)  # fmt: skip


if SKIP_SEED:
    tid = 1
else:
    seed()
body = {"name": "Projekt 001", "code": "001", "members": [{"kind": "ap", "ref": "TE_01/AP_001"}]}
if SKIP_SEED:
    aid = ok(c.get("/applications", params={"tenant_id": tid}))[0]["id"]
else:
    aid = timed("POST /applications (AP)",
                lambda: ok(c.post(f"/applications?tenant_id={tid}", json=body, headers=H),
                           201))["id"]  # fmt: skip
timed("GET /applications", lambda: ok(c.get("/applications", params={"tenant_id": tid})))
timed("GET /applications/suggestions",
      lambda: ok(c.get("/applications/suggestions", params={"tenant_id": tid})))  # fmt: skip
timed("GET /applications/{id}", lambda: ok(c.get(f"/applications/{aid}")))
timed("GET /applications/{id}/doc", lambda: ok(c.get(f"/applications/{aid}/doc")))
timed("GET /applications/{id}/connections", lambda: ok(c.get(f"/applications/{aid}/connections")))
# Namen der Gegenstellen (0.95.0, Anwender 0.106.0: „es braucht enorm lange, dann sind die Namen da“)
timed("PATCH /tenants/{id} (Namen an)",
      lambda: ok(c.patch(f"/tenants/{tid}", json={"appdoc_foreign_names": True}, headers=H)))  # fmt: skip
timed("GET /applications/{id}/doc (Namen)", lambda: ok(c.get(f"/applications/{aid}/doc")))
ok(c.patch(f"/tenants/{tid}", json={"appdoc_foreign_names": False}, headers=H))
timed("PATCH /applications/{id} (Beschreibung)",
      lambda: ok(c.patch(f"/applications/{aid}", json={"description": "neu"}, headers=H)))  # fmt: skip
member = {"members": [{"kind": "epg", "ref": "uni/tn-TE_02/ap-AP_002/epg-EPG_01"}]}
timed("POST /applications/{id}/members",
      lambda: ok(c.post(f"/applications/{aid}/members", json=member, headers=H)))  # fmt: skip

which = os.environ.get("BENCH_PROFILE")
if which:
    calls = {
        "doc": lambda: c.get(f"/applications/{aid}/doc"),
        "connections": lambda: c.get(f"/applications/{aid}/connections"),
        "patch": lambda: c.patch(f"/applications/{aid}", json={"description": "x"}, headers=H),
    }
    prof = cProfile.Profile()
    prof.runcall(calls[which])
    pstats.Stats(prof).sort_stats("cumulative").print_stats(25)
