"""Zonen je Hop in der Weg-Suche und Absicht je Dienst (0.57.0, ADR 0024 Nr. 13).

Zonenmodell wie in test_zones (`_model`): DC (ACI-Tenant TE), Edge Internet (fw-01 und fw-02),
Office (10.60.0.0/24), Server (Interface srv), Public (Anbindung Internet). Auf fw-01 u. a. die
Policies lan-to-srv und inet-to-srv (tcp/443).
"""

from app.store import zone_services
from test_zones import ADMIN, OP, VIEW, _model, _seed

NAMES = """<mxfile><diagram name="Zonenschema"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<object id="p" label="Public" automato_kind="extern"><mxCell vertex="1" parent="1"/></object>
<object id="s" label="Server" automato_code="SRV"><mxCell vertex="1" parent="1"/></object>
<object id="e" label="" automato_services="tcp/443, FT_ssh"><mxCell edge="1" parent="1"
  source="p" target="s"/></object>
</root></mxGraphModel></diagram></mxfile>"""


def _policy(client, tid, src, dst, intent, services=None, headers=OP):
    body = {"tenant_id": tid, "src_zone_id": src, "dst_zone_id": dst, "intent": intent,
            "services": services}  # fmt: skip
    return client.put("/zones/policy", json=body, headers=headers)


def _trace(client, tid, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_dienste_lesen_und_abdecken():
    assert zone_services.parse("tcp/443") == ("tcp", 443, 443)
    assert zone_services.parse("TCP/8000-8080") == ("tcp", 8000, 8080)
    assert zone_services.parse("udp") == ("udp", 1, 65535)
    assert zone_services.parse("ping") == ("icmp", None, None)
    assert zone_services.parse("any") == ("any", None, None)
    assert zone_services.parse("tcp/0") is None and zone_services.parse("FT_https") is None
    allow = zone_services.Allowed(
        ["tcp/443", "tcp/8000-8080", "icmp"],
        [("tcp", 443, 443), ("tcp", 8000, 8080), ("icmp", None, None)],
    )
    assert allow.covers("tcp", 443, 443, "tcp/443") and allow.covers("tcp", 8001, 8002, None)
    assert not allow.covers("tcp", 22, 22, "tcp/22") and not allow.covers("tcp", None, None, "tcp")
    assert allow.covers("icmpv6", None, None, "icmp6") and not allow.covers("udp", 53, 53, None)
    rs = zone_services.RuleServices({1: [("tcp", 443, 443, "tcp/443"), ("tcp", 22, 22, "SSH")],
                                     2: [("tcp", 1, 65535, "all")]}, {3})  # fmt: skip
    assert rs.outside(1, allow) == ["SSH (tcp/22)"]
    assert rs.outside(2, allow) == ["ALL"] and rs.outside(9, allow) is None


def test_absicht_je_dienst_in_matrix_und_befund(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    pub, srv = z["public"]["id"], z["server"]["id"]
    # nur Operator; unbekannter Dienst 422
    assert _policy(client, tid, pub, srv, "limited", ["tcp/22"], VIEW).status_code == 403
    bad = _policy(client, tid, pub, srv, "limited", ["tcp/22", "kein-dienst"])
    assert bad.status_code == 422 and "kein-dienst" in bad.json()["detail"]
    r = _policy(client, tid, pub, srv, "limited", ["tcp/22", " tcp/22", "icmp"])
    assert r.status_code == 200 and r.json()["services"] == ["tcp/22", "icmp"]

    # inet-to-srv (tcp/443) liegt ausserhalb von tcp/22 - Verstoss in Matrix und Befund
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    cell = next(c for c in m["cells"] if (c["src"], c["dst"]) == (pub, srv))
    assert (cell["intent"], cell["services"], cell["violations"]) == (
        "limited", ["tcp/22", "icmp"], 1)  # fmt: skip
    rules = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": pub,
                                                      "dst": srv}).json()  # fmt: skip
    (rule,) = (x for x in rules["items"] if x["violation"])
    assert rule["name"] == "inet-to-srv" and rule["outside"] == ["tcp/443"]
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_policy_violation"}).json()
    items = f["items"] if isinstance(f, dict) else f
    assert [i["subject_id"] for i in items] == ["inet-to-srv"]
    assert "öffnet zwischen Public → Server tcp/443 – erlaubt sind nur tcp/22, icmp" in (
        items[0]["message"])  # fmt: skip

    # Dienst dazu: kein Verstoss mehr; Dienste nur bei „eingeschraenkt“
    _policy(client, tid, pub, srv, "limited", ["tcp/22", "tcp/443"])
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    assert m["summary"]["violations"] == 0
    r = _policy(client, tid, pub, srv, "allow", ["tcp/22"])
    assert r.json()["services"] == []
    assert client.get("/zones/matrix", params={"tenant_id": tid}).json()["intents"][0][
        "services"] == []  # fmt: skip


def test_weg_suche_mit_zonen_und_absicht(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    start = f"{ids['fw-01']}|root"
    out = _trace(client, tid, src="203.0.113.7", dst="10.20.0.5", start=start, service="tcp/443")
    assert [x["name"] for x in out["zones"]["src"]] == ["Public"]
    assert [x["name"] for x in out["zones"]["dst"]] == ["Server"]
    (path,) = (p for p in out["paths"] if p["kind"] == "active")
    assert path["policy"]["verdict"] == "allow"
    chain = [(c["name"], c["role"], c.get("transition"), c["hops"]) for c in path["zones"]]
    assert chain == [
        ("Public", "src", None, []),
        ("Edge Internet", "hop", "EINET-PUB", ["fw-01 · VDOM root"]),
        ("Server", "dst", "SRV-EINET", []),
    ]
    assert path["hops"][0]["zone"]["name"] == "Edge Internet"
    assert out["zones"]["intents"] == []  # keine Absicht

    # gesperrt: der Weg erlaubt -> gegen die Absicht
    _policy(client, tid, z["public"]["id"], z["server"]["id"], "deny")
    (it,) = _trace(client, tid, src="203.0.113.7", dst="10.20.0.5", start=start,
                   service="tcp/443")["zones"]["intents"]  # fmt: skip
    assert (it["intent"], it["verdict"]) == ("deny", "violation")
    assert it["src_name"] == "Public" and "gegen die Absicht" in it["text"]
    # eingeschraenkt: Dienst erlaubt bzw. nicht; ohne Dienst nur Hinweis
    _policy(client, tid, z["public"]["id"], z["server"]["id"], "limited", ["tcp/443"])
    (it,) = _trace(client, tid, src="203.0.113.7", dst="10.20.0.5", start=start,
                   service="tcp/443")["zones"]["intents"]  # fmt: skip
    assert it["verdict"] == "ok" and "tcp/443 ist erlaubt" in it["text"]
    (it,) = _trace(client, tid, src="203.0.113.7", dst="10.20.0.5", start=start,
                   service="tcp/22")["zones"]["intents"]  # fmt: skip
    # tcp/22 sperrt der Weg ohnehin (keine passende Policy): nur ein Hinweis
    assert (it["verdict"], it["text"]) == (
        "warn", "eingeschränkt auf tcp/443 – tcp/22 gehört nicht dazu")  # fmt: skip
    (it,) = _trace(client, tid, src="203.0.113.7", dst="10.20.0.5",
                   start=start)["zones"]["intents"]  # fmt: skip
    assert it["verdict"] == "info" and "Dienst angeben" in it["text"]


def test_weg_suche_ohne_zonen_und_knoten_ohne_zone(client):
    tid, ids = _seed(client)
    out = _trace(client, tid, src="10.60.0.5", dst="10.20.0.5")
    assert out["zones"] is None and all("zones" not in p for p in out["paths"])
    # nur Netz-Zonen: Knoten ohne Zone erscheinen als „ohne Zone“
    client.post("/zones", json={"tenant_id": tid, "name": "Office", "selectors": [
        {"kind": "prefix", "value": "10.60.0.0/24"}]}, headers=OP)  # fmt: skip
    out = _trace(client, tid, src="10.60.0.5", dst="10.20.0.5")
    (path,) = (p for p in out["paths"] if p["kind"] == "active")
    assert [(c["name"], c.get("transition")) for c in path["zones"]] == [
        ("Office", None), ("ohne Zone", "Office-ohne Zone")]  # fmt: skip
    assert path["zones"][1]["hops"] == ["fw-02", "fw-01 · VDOM root"]


def test_grundschema_mit_diensten(client):
    tid, ids = _seed(client)
    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Zonen"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    client.patch(f"/tenants/{tid}", json={"naming_profile_id": pid}, headers=ADMIN)
    r = client.post(f"/naming-profiles/{pid}/zone-schema/preview", json={"content": NAMES},
                    headers=ADMIN)  # fmt: skip
    (link,) = r.json()["schema"]["links"]
    assert (link["intent"], link["services"]) == ("limited", ["tcp/443", "FT_ssh"])
    assert client.put(f"/naming-profiles/{pid}/zone-schema", json={"content": NAMES},
                      headers=ADMIN).status_code == 200  # fmt: skip
    z = _model(client, tid, ids)
    sch = client.get("/zones/suggestions", params={"tenant_id": tid}).json()["schema"]
    (x,) = (i for i in sch["intents"] if i["src"] == "Public")
    assert (x["intent"], x["services"]) == ("limited", ["tcp/443", "FT_ssh"])
    done = client.post("/zones/schema/intents", json={"tenant_id": tid}, headers=OP).json()
    assert done["changed"][0]["intent"] == "limited"
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    cell = next(c for c in m["cells"] if (c["src"], c["dst"]) == (z["public"]["id"],
                                                                  z["server"]["id"]))  # fmt: skip
    assert cell["services"] == ["tcp/443", "FT_ssh"] and cell["violations"] == 0
    # Vorlage aus den Zonen traegt die Dienste weiter
    t = client.get("/naming-profiles/zone-schema/template", params={"tenant_id": tid})
    assert 'automato_services="tcp/443, FT_ssh"' in t.text
