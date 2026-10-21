"""VDOM-Kontext (ADR 0019 Stufe 1, 0.15.0): eine FortiGate mit zwei VDOMs.

Policy-ID, Objektname, VIP und IPv4/IPv6-Paar gelten je VDOM: gleiche Werte in zwei VDOMs sind
verschiedene Eintraege. Verweise, Gruppen, NAT-Nutzung, Paare und Befunde bleiben im VDOM.
"""

from app.models import RulePairDecision
from sqlalchemy import select

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _objects(vdom: str, net: str, vip_ext: str) -> list[dict]:
    return [
        {"name": "N_clients", "category": "address", "family": "v4", "values": [net],
         "vdom": vdom},
        {"name": "N_clients", "category": "address", "family": "v6", "values": ["2001:db8::/64"],
         "vdom": vdom},
        {"name": "G_clients", "category": "address_group", "family": "v4",
         "members": ["N_clients"], "values": [net], "vdom": vdom},
        {"name": "VIP_web", "category": "vip", "values": [vip_ext], "vdom": vdom},
        {"name": "HTTPS", "category": "service", "values": ["tcp/443"], "vdom": vdom},
    ]  # fmt: skip


def _rule(vdom, ext_id, name, family, src, vip_ext=None):
    v6 = family == "v6"
    rule = {
        "source_platform": "fortigate",
        "external_id": ext_id,
        "name": name,
        "action": "allow",
        "sources": ["2001:db8::/64"] if v6 else [src],
        "destinations": [f"vip:VIP_web={vip_ext}"] if vip_ext else ["all"],
        "services": ["tcp/443"],
        "src_zone": "lan",
        "dst_zone": "wan",
        "source_refs": [] if v6 else ["G_clients"],
        "source_refs6": ["N_clients"] if v6 else [],
        "destination_refs": ["VIP_web"] if vip_ext else ["all"],
        "service_refs": ["HTTPS"],
        "family": family,
        "vdom": vdom,
    }
    return rule


def _nat(vdom: str, ext: str, mapped: str) -> dict:
    return {
        "source_platform": "fortigate",
        "external_id": "VIP_web",
        "name": "VIP_web",
        "ext_ip": ext,
        "mapped_ip": mapped,
        "vdom": vdom,
        "raw": {"type": "static-nat"},
    }


RESULT = {
    "status": "completed",
    "objects": _objects("root", "10.1.0.0/24", "203.0.113.1")
    + _objects("kunde-a", "10.50.0.0/24", "203.0.113.50"),
    "rules": [
        _rule("root", "1", "web", "v4", "10.1.0.0/24", "203.0.113.1"),
        _rule("root", "2", "web-v6", "v6", None),
        _rule("kunde-a", "1", "web", "v4", "10.50.0.0/24", "203.0.113.50"),
        _rule("kunde-a", "2", "web-v6", "v6", None),
    ],
    "nat_rules": [
        _nat("root", "203.0.113.1", "10.1.0.10"),
        _nat("kunde-a", "203.0.113.50", "10.50.0.10"),
    ],
    "inventory": {
        "devices": [{"name": "fw-01", "role": "firewall"}],
        "interfaces": [
            {"device": "fw-01", "name": "port1", "zone": "lan", "vdom": "root"},
            {"device": "fw-01", "name": "port2", "zone": "lan", "vdom": "kunde-a"},
        ],
    },
}


def _seed(client, result=RESULT) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-01", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    _ingest(client, tid, sid, result)
    return tid, sid


def _ingest(client, tid: int, sid: int, result=RESULT) -> None:
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _rules(client, tid: int, **params) -> dict[tuple[str, str], dict]:
    r = client.get("/rules", params={"tenant_id": tid, **params})
    return {(x["vdom"], x["name"]): x for x in r.json()}


def test_gleiche_policy_id_und_objektnamen_je_vdom(client):
    tid, _ = _seed(client)
    rules = _rules(client, tid)
    assert set(rules) == {
        ("root", "web"),
        ("root", "web-v6"),
        ("kunde-a", "web"),
        ("kunde-a", "web-v6"),
    }
    summary = client.get("/rules/summary", params={"tenant_id": tid}).json()
    assert summary["by_vdom"] == [["kunde-a", 2], ["root", 2]]
    assert set(_rules(client, tid, vdom="kunde-a")) == {("kunde-a", "web"), ("kunde-a", "web-v6")}

    # Verweise loesen im VDOM der Regel auf: G_clients -> N_clients mit dem Netz des VDOMs
    detail = client.get(f"/rules/{rules[('kunde-a', 'web')]['id']}").json()
    assert detail["vdom"] == "kunde-a"
    refs = {r["name"]: r for r in detail["refs"]}
    assert [m["values"][0]["value"] for m in refs["G_clients"]["members"]] == ["10.50.0.0/24"]
    assert detail["nat_entries"][0]["ext_ip"] == "203.0.113.50"
    # gleicher VIP-Name, andere externe IP: je VDOM das eigene Ziel (auch in der IP-Suche)
    (dst,) = (e for e in detail["endpoints"] if e["role"] == "dst")
    assert (dst["name"], dst["value"], dst["nat_target"]) == (
        "VIP_web",
        "203.0.113.50",
        "10.50.0.10",
    )
    assert set(_rules(client, tid, q="203.0.113.50")) == {("kunde-a", "web")}
    assert set(_rules(client, tid, q="203.0.113.1")) == {("root", "web")}

    # Objekte: je VDOM eines, je mit seiner Regel (Gruppen und VIP ebenso)
    objs = client.get("/objects", params={"tenant_id": tid, "q": "N_clients"}).json()["rows"]
    v4 = {o["vdom"]: o for o in objs if o["family"] == "v4"}
    assert set(v4) == {"root", "kunde-a"}
    assert v4["kunde-a"]["values"][0]["value"] == "10.50.0.0/24"
    assert v4["kunde-a"]["member_of"] == ["G_clients"] and v4["kunde-a"]["used_in_rules"] == 1
    obj = client.get(f"/objects/{v4['root']['id']}").json()
    assert [(r["vdom"], r["name"]) for r in obj["rules"]] == [("root", "web")]

    # Suche nach dem VDOM-Namen
    assert set(_rules(client, tid, q="kunde-a")) == {("kunde-a", "web"), ("kunde-a", "web-v6")}

    # Interfaces mit VDOM (Zone gilt im VDOM)
    ports = client.get("/inventory/interfaces", params={"tenant_id": tid}).json()
    assert {(p["name"], p["vdom"], p["zone"]) for p in ports} == {
        ("port1", "root", "lan"),
        ("port2", "kunde-a", "lan"),
    }


def test_nat_je_vdom(client):
    tid, _ = _seed(client)
    rows = {n["vdom"]: n for n in client.get("/nat", params={"tenant_id": tid}).json()}
    assert set(rows) == {"root", "kunde-a"}
    assert rows["kunde-a"]["ext_ip"] == "203.0.113.50"
    assert rows["kunde-a"]["rule_count"] == rows["root"]["rule_count"] == 1
    detail = client.get(f"/nat/{rows['kunde-a']['id']}").json()
    assert [(r["vdom"], r["name"]) for r in detail["rules"]] == [("kunde-a", "web")]
    obj = client.get(f"/objects/{detail['object_id']}").json()
    assert obj["vdom"] == "kunde-a" and obj["values"][0]["value"] == "203.0.113.50"


def test_paare_und_entscheidungen_je_vdom(client, session_factory):
    tid, sid = _seed(client)
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    rules = _rules(client, tid)
    for vdom in ("root", "kunde-a"):  # Paar nur im eigenen VDOM (gleiche IDs in beiden)
        assert rules[(vdom, "web")]["pair"]["rule_id"] == rules[(vdom, "web-v6")]["id"]

    # Paar ueber zwei VDOMs geht nicht
    body = {"partner_id": rules[("kunde-a", "web-v6")]["id"], "decision": "confirmed"}
    r = client.post(f"/rules/{rules[('root', 'web')]['id']}/pair", json=body, headers=operator)
    assert r.status_code == 422

    # Ablehnen in root laesst kunde-a unberuehrt - auch nach einem neuen Lauf
    body = {"partner_id": rules[("root", "web-v6")]["id"], "decision": "rejected"}
    r = client.post(f"/rules/{rules[('root', 'web')]['id']}/pair", json=body, headers=operator)
    assert r.status_code == 204
    _ingest(client, tid, sid)
    rules = _rules(client, tid)
    assert rules[("root", "web")]["pair"] is None
    assert rules[("kunde-a", "web")]["pair"] is not None
    (decision,) = client.get(f"/rules/{rules[('root', 'web')]['id']}").json()["pair_decisions"]
    assert decision["decision"] == "rejected"
    assert client.get(f"/rules/{rules[('kunde-a', 'web')]['id']}").json()["pair_decisions"] == []

    # Entscheidung von vor 0.15.0 (ohne VDOM) gilt fuer jeden VDOM der Firewall
    with session_factory() as s:
        d = s.scalars(select(RulePairDecision)).one()
        d.vdom = None
        s.commit()
    _ingest(client, tid, sid)
    rules = _rules(client, tid)
    assert rules[("root", "web")]["pair"] is None and rules[("kunde-a", "web")]["pair"] is None


def test_befunde_je_vdom(client):
    """Gleiche IP in zwei VDOMs ist kein doppelter Wert; innerhalb eines VDOMs schon. Befunde
    ausserhalb des Standard-VDOMs nennen den VDOM."""
    extra = [
        {"name": "H_a", "category": "address", "family": "v4", "values": ["10.9.9.9"],
         "vdom": "kunde-a"},
        {"name": "H_b", "category": "address", "family": "v4", "values": ["10.9.9.9"],
         "vdom": "kunde-a"},
        {"name": "H_c", "category": "address", "family": "v4", "values": ["10.8.8.8"],
         "vdom": "root"},
        {"name": "H_d", "category": "address", "family": "v4", "values": ["10.8.8.8"],
         "vdom": "kunde-a"},
    ]  # fmt: skip
    tid, _ = _seed(client, {**RESULT, "objects": RESULT["objects"] + extra})
    found = client.get("/findings", params={"tenant_id": tid, "limit": 500}).json()
    dup = [f for f in found if f["type"] == "duplicate_value"]
    assert [f["subject_id"] for f in dup] == ["10.9.9.9 (VDOM kunde-a)"]
    assert "(VDOM kunde-a)" in dup[0]["message"]
    unused = {f["subject_id"] for f in found if f["type"] == "unused_object"}
    assert {"H_c", "H_d (VDOM kunde-a)"} <= unused
