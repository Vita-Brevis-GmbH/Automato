"""IPv4/IPv6 getrennt, Regel-Zahl ueber Gruppen und IPv4/IPv6-Paare (Dev-Befunde 0.9.0).

FortiGate fuehrt address/addrgrp (IPv4) und address6/addrgrp6 (IPv6) getrennt - gleiche Namen
sind verschiedene Objekte. Regeln je Familie, die dasselbe abdecken, werden nur markiert.
"""

from app.models import RuleIndex
from app.store import rule_pairs
from sqlalchemy import delete

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}

OBJECTS = [
    {"name": "H_web01", "category": "address", "family": "v4", "values": ["10.10.10.5"]},
    {"name": "H_web01", "category": "address", "family": "v6", "values": ["2001:db8:10::5"]},
    {"name": "G_web", "category": "address_group", "family": "v4", "members": ["H_web01"],
     "values": ["10.10.10.5"]},
    {"name": "G_web", "category": "address_group", "family": "v6", "members": ["H_web01"],
     "values": ["2001:db8:10::5"]},
    {"name": "N_clients", "category": "address", "family": "v4", "values": ["10.5.0.0/16"]},
    {"name": "N_clients", "category": "address", "family": "v6", "values": ["2001:db8:5::/64"]},
    {"name": "H_old", "category": "address", "family": "v6", "values": ["2001:db8:10::99"]},
    {"name": "HTTPS", "category": "service", "values": ["tcp/443"]},
    {"name": "FT_tcp9000", "category": "service", "values": ["tcp/9000"]},
]  # fmt: skip

V4 = {"N_clients": ["10.5.0.0/16"], "G_web": ["10.10.10.5"], "H_web01": ["10.10.10.5"]}
V6 = {"N_clients": ["2001:db8:5::/64"], "G_web": ["2001:db8:10::5"], "H_web01": ["2001:db8:10::5"]}


def _rule(ext_id, name, src, dst, svc, family, src6=(), dst6=()):
    return {
        "source_platform": "fortigate",
        "external_id": ext_id,
        "name": name,
        "action": "allow",
        "sources": [v for n in src for v in V4[n]] + [v for n in src6 for v in V6[n]],
        "destinations": [v for n in dst for v in V4[n]] + [v for n in dst6 for v in V6[n]],
        "services": ["tcp/443"] if svc == "HTTPS" else ["tcp/9000"],
        "src_zone": "wan",
        "dst_zone": "dmz",
        "source_refs": list(src),
        "destination_refs": list(dst),
        "source_refs6": list(src6),
        "destination_refs6": list(dst6),
        "service_refs": [svc],
        "family": family,
    }


RULES = [
    _rule("1", "web-in", ["N_clients"], ["G_web"], "HTTPS", "v4"),
    _rule("2", "web-in-v6", [], [], "HTTPS", "v6", ["N_clients"], ["G_web"]),
    _rule("3", "api-in", ["N_clients"], ["H_web01"], "FT_tcp9000", "v4"),
    _rule("4", "api-in", [], [], "HTTPS", "v6", ["N_clients"], ["H_web01"]),
    _rule("5", "dual", ["N_clients"], ["H_web01"], "HTTPS", "dual", ["N_clients"], ["H_web01"]),
]


def _seed(client) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-01", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    result = {"status": "completed", "rules": RULES, "objects": OBJECTS}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return tid, sid


def _objects(client, tid, **params):
    rows = client.get("/objects", params={"tenant_id": tid, **params}).json()["rows"]
    return {(o["name"], o["family"]): o for o in rows}


def _rules(client, tid, **params):
    return client.get("/rules", params={"tenant_id": tid, **params}).json()


def test_gleichnamige_objekte_getrennt_und_regel_zahl_ueber_gruppen(client):
    tid, _ = _seed(client)
    objs = _objects(client, tid)
    v4, v6 = objs[("H_web01", "v4")], objs[("H_web01", "v6")]
    assert v4["values"][0]["value"] == "10.10.10.5"
    assert v6["values"][0]["value"] == "2001:db8:10::5"
    # Regel-Zahl: direkt und ueber die Gruppe der eigenen Familie
    assert (v4["used_in_rules"], v6["used_in_rules"]) == (3, 3)
    assert v4["member_of"] == ["G_web"] and v6["member_of"] == ["G_web"]
    assert objs[("G_web", "v4")]["used_in_rules"] == 1

    detail = client.get(f"/objects/{v6['id']}").json()
    assert detail["rule_count"] == 3
    assert sorted(r["name"] for r in detail["rules"]) == ["api-in", "dual", "web-in-v6"]
    grp = client.get(f"/objects/{objs[('G_web', 'v6')]['id']}").json()
    assert grp["members_detail"][0]["family"] == "v6"
    assert grp["members_detail"][0]["values"] == ["2001:db8:10::5"]

    # Link aus der Objekt-Ansicht: genau dieses Objekt
    names = [r["name"] for r in _rules(client, tid, object_id=v4["id"])]
    assert names == ["web-in", "api-in", "dual"]
    # Name allein trifft beide Familien
    assert len(_rules(client, tid, object="h_web01")) == 5

    # Verweise der IPv6-Regel zeigen auf die IPv6-Objekte
    web6 = next(r for r in _rules(client, tid) if r["name"] == "web-in-v6")
    refs = client.get(f"/rules/{web6['id']}").json()["refs"]
    assert {r["name"]: r["family"] for r in refs if r["role"] != "service"} == {
        "N_clients": "v6",
        "G_web": "v6",
    }
    g = next(r for r in refs if r["name"] == "G_web")
    assert g["members"][0]["values"][0]["value"] == "2001:db8:10::5"

    # Befund: ungenutztes IPv6-Objekt mit Familie in der Bezeichnung
    found = client.get("/findings", params={"tenant_id": tid, "type": "unused_object"}).json()
    assert [f["subject_id"] for f in found] == ["H_old (IPv6)"]
    flagged = _objects(client, tid, issues_only="true")
    assert "unused_object" in flagged[("H_old", "v6")]["issues"]
    assert "unused_object" not in flagged.get(("H_web01", "v6"), {}).get("issues", [])


def test_regelpaare_familie_und_suche(client):
    tid, _ = _seed(client)
    rules = {(r["name"], r["family"]): r for r in _rules(client, tid)}
    web4, web6 = rules[("web-in", "v4")], rules[("web-in-v6", "v6")]
    assert web4["pair"] == {
        "rule_id": web6["id"],
        "name": "web-in-v6",
        "family": "v6",
        "match": "same",
        "diff": [],  # Name ohne IPv4/IPv6-Kennung gleich
    }
    assert web6["pair"]["rule_id"] == web4["id"]
    api4, api6 = rules[("api-in", "v4")], rules[("api-in", "v6")]
    assert api4["pair"]["rule_id"] == api6["id"]
    assert (api4["pair"]["match"], api4["pair"]["diff"]) == ("similar", ["service"])
    assert rules[("dual", "dual")]["pair"] is None
    # gleichnamige Objekte stehen in der Liste einmal
    assert rules[("dual", "dual")]["destinations"] == ["H_web01"]

    assert [r["name"] for r in _rules(client, tid, family="v6")] == ["web-in-v6", "api-in"]
    # IPv6-Adresse im Netz der Quelle (nur IPv6-Regeln und die Dual-Stack-Regel)
    hits = sorted(r["name"] for r in _rules(client, tid, q="2001:db8:5::77"))
    assert hits == ["api-in", "dual", "web-in-v6"]
    assert [r["name"] for r in _rules(client, tid, q="tcp/9000")] == ["api-in"]


def test_index_wird_nachgeholt(client, session_factory):
    """Nach dem Update (Migration ohne Index) baut die erste Anfrage den Index auf."""
    tid, _ = _seed(client)
    with session_factory() as s:
        s.execute(delete(RuleIndex))
        s.commit()
    r = client.get("/rules", params={"tenant_id": tid, "q": "web-in-v6"})
    assert [x["name"] for x in r.json()] == ["web-in-v6"]
    assert r.headers["X-Total-Count"] == "1"


def test_paare_reine_funktion():
    assert rule_pairs.bare_name("web-in-v6") == "web-in"
    assert rule_pairs.bare_name("Web_In_IPv6") == "web_in"
    assert rule_pairs.bare_name("v6-only") == "only"

    def info(rid, fam, name, pos, **kw):
        base = {"src": frozenset({"n"}), "dst": frozenset({"h"}), "service": frozenset({"s"})}
        return rule_pairs.PairInfo(rid, 1, fam, "allow", name, pos, 1, 2, **{**base, **kw})

    # zwei gleiche IPv4-Kandidaten: die naechstgelegene Regel gewinnt
    pairs = rule_pairs.pair([info(1, "v4", "a", 0), info(2, "v4", "b", 5), info(3, "v6", "c", 4)])
    assert pairs[3].partner_id == 2 and 1 not in pairs
    # verschiedene Interfaces und Namen: kein Paar
    other = info(5, "v6", "x", 1)
    other = rule_pairs.PairInfo(**{**other.__dict__, "src_zone": 9})
    assert rule_pairs.pair([info(4, "v4", "y", 0), other]) == {}
