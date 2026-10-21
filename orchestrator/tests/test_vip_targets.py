"""VIP-Ziele als Objekte (Dev-Befund 0.12.0): die interne Ziel-IP einer VIP ohne eigenes
Adressobjekt wird ein Objekt "VIP-Ziel"; Adressobjekte hinter einer VIP (FQDN-VIP mapped-addr,
Real Server, gleiche IP) tragen "Ziel von VIP". Beide zaehlen die Regeln der VIP."""

from test_virtual_server import NAT as SLB_NAT
from test_virtual_server import OBJECTS as SLB_OBJECTS
from test_virtual_server import RULES as SLB_RULES
from test_virtual_server import VS

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}

OBJECTS = [
    *SLB_OBJECTS,
    {"name": "VIP_web", "category": "vip", "family": "v4", "obj_type": "static-nat",
     "values": ["203.0.113.30", "10.10.40.5"]},
    {"name": "FQDN_app", "category": "address", "family": "v4", "obj_type": "fqdn",
     "fqdn": "app.example.ch"},
    {"name": "VIP_app", "category": "vip", "family": "v4", "obj_type": "fqdn",
     "values": ["203.0.113.31"]},
]  # fmt: skip


def _rule(ext_id, name, vip, ext_ip):
    return {**SLB_RULES[0], "external_id": ext_id, "name": name,
            "destinations": [f"vip:{vip}={ext_ip}"], "destination_refs": [vip]}  # fmt: skip


RULES = [*SLB_RULES, _rule("2", "web-in", "VIP_web", "203.0.113.30"),
         _rule("3", "app-in", "VIP_app", "203.0.113.31")]  # fmt: skip
NAT = [
    *SLB_NAT,
    {"source_platform": "fortigate", "external_id": "VIP_web", "name": "VIP_web",
     "ext_ip": "203.0.113.30", "mapped_ip": "10.10.40.5", "vip_type": "static-nat",
     "family": "v4", "raw": {"name": "VIP_web", "type": "static-nat"}},
    {"source_platform": "fortigate", "external_id": "VIP_app", "name": "VIP_app",
     "ext_ip": "203.0.113.31", "vip_type": "fqdn", "family": "v4",
     "raw": {"name": "VIP_app", "type": "fqdn", "mapped-addr": "FQDN_app"}},
]  # fmt: skip


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-01", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    result = {"status": "completed", "rules": RULES, "objects": OBJECTS, "nat_rules": NAT}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return tid


def _objects(client, tid, **params):
    rows = client.get("/objects", params={"tenant_id": tid, **params}).json()["rows"]
    return {o["name"]: o for o in rows}


def test_vip_ziele_als_objekte_mit_regeln(client):
    tid = _seed(client)
    objs = _objects(client, tid, category="address,vip,vip_target,ippool")
    # Mapped-IP ohne Adressobjekt: eigenes Objekt "VIP-Ziel"
    target = objs["VIP_web → 10.10.40.5"]
    assert target["category"] == "vip_target" and target["attrs"]["vip"] == "VIP_web"
    assert target["used_in_rules"] == 1
    # Real Server mit gleichnamiger IP als Adressobjekt: kein Doppel, das Objekt ist das Ziel
    assert objs["H_shop01"]["attrs"]["vip_of"] == [VS] and objs["H_shop01"]["used_in_rules"] == 1
    assert not [n for n in objs if n.startswith(f"{VS} →")]
    # FQDN-VIP: das Adressobjekt hinter mapped-addr
    assert objs["FQDN_app"]["attrs"]["vip_of"] == ["VIP_app"]
    assert objs["FQDN_app"]["used_in_rules"] == 1
    # "Regeln" eines VIP-Ziels = Regeln der VIP; Suche nach der internen IP findet das Ziel
    rules = client.get("/rules", params={"tenant_id": tid, "object_id": target["id"]}).json()
    assert [r["name"] for r in rules] == ["web-in"]
    hits = _objects(client, tid, category="vip_target", q="10.10.40.5")
    assert list(hits) == ["VIP_web → 10.10.40.5"]
    # kein Befund "unbenutzt" fuer Ziele
    unused = client.get("/findings", params={"tenant_id": tid, "type": "unused_object"}).json()
    names = {f["subject_id"] for f in unused}
    assert not names & {"VIP_web → 10.10.40.5", "FQDN_app", "H_shop01", "H_shop02"}
