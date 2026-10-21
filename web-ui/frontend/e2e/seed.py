"""Beispieldaten fuer die UI-Tests - aus denselben Testdaten wie die Orchestrator-Tests.

Ein Mandant „Kunde A" (ID 1) mit DDI (fuehrend), drei FortiGates (eine mit gleichnamigen
IPv4/IPv6-Objekten und Regelpaaren), einem APIC, virtuellen und physischen Standorten, NAT/VIPs,
Gruppen, Diensten mit Namensschema-Befunden und einem ACI-Contract ohne Consumer. Alles ueber
die oeffentliche bzw. interne API wie im Betrieb.
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
    run_result(fw3, "fortigate", rules=ipf.RULES, objects=ipf.OBJECTS)

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
    run_result(apic, "cisco_aci", rules=contracts, objects=filters, inventory=st.INVENTORY)
