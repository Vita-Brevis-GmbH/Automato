"""Verbindungen Ende zu Ende in der Applikations-Doku (0.45.0, Anwender: eine Zeile je Weg,
wenn mehrere Firewalls und das ACI hintereinander stehen; je Hop Policy bzw. Contract mit den
Objekten; Regel und Weg abgleichen).

Topologie aus `test_paths`: EPG_web (10.50.1.0/24, Endpunkt 10.50.1.10) in VRF TE:VRF1, die
VRF routet 10.60.0.0/24 ueber L3O_fw zu fw-01, fw-01 weiter zu fw-02 mit dem LAN 10.60.0.0/24.
Im ACI die externe EPG EN_lan (10.60.0.0/24) mit dem Contract CT_lan (tcp/443). Dazu fw-03,
das nicht auf dem Weg liegt.
"""

import copy

import test_aci_relevance as ar
import test_paths as tp

OP = {"X-Dev-Roles": "operator"}
TE = "uni/tn-TE"
LAN = f"{TE}/out-L3O_fw/instP-EN_lan"


def _fw(ext, name, src, dst, port, srcintf, dstintf, zones, action="allow"):
    return {"source_platform": "fortigate", "external_id": ext, "name": name, "action": action,
            "sources": [src], "destinations": [dst], "services": [f"tcp/{port}"],
            "src_zone": zones[0], "dst_zone": zones[1], "vdom": "root", "family": "v4",
            "raw": {"policyid": int(ext), "srcintf": [{"name": srcintf}],
                    "dstintf": [{"name": dstintf}], "status": "enable"}}  # fmt: skip


APIC = copy.deepcopy(tp.APIC)
APIC["routing"]["routes"] += [
    tp._r(leaf, "TE:VRF1", "10.60.0.0/24", tp.ACI_HOP, "bgp", distance=20)
    for leaf in ("leaf-101", "leaf-102")
]
APIC["epgs"] = [
    {"name": "EPG_web", "dn": ar.WEB, "tenant": "TE", "ap": "AP1", "bd": "BD_web", "vrf": "VRF1",
     "bd_subnets": ["10.50.1.1/24"], "endpoints": [{"ip": "10.50.1.10"}]},
    ar._ext("EN_lan", "L3O_fw", ["10.60.0.0/24"], {"10.60.0.0/24": "import-security"}),
]  # fmt: skip
APIC["rules"] = [
    {"source_platform": "cisco_aci", "external_id": f"{TE}/brc-CT_lan/SJ_1", "name": "CT_lan",
     "action": "allow", "sources": [f"epg:{ar.WEB}"], "destinations": [f"epg:{LAN}"],
     "services": ["tcp/443"], "aci_scope": "context",
     "raw": {"name": "CT_lan", "kind": "contract", "scope": "context"}},
]  # fmt: skip
WEB, SRV = "10.50.1.10", "10.60.0.5"
FW1 = {**tp.FW1, "rules": [
    _fw("11", "web-lan", WEB, SRV, 443, "to-aci", "to-fw02", ("aci", "transit")),
    _fw("12", "web-lan-8080", WEB, SRV, 8080, "to-aci", "to-fw02", ("aci", "transit")),
]}  # fmt: skip
FW2 = copy.deepcopy(tp.FW2)
FW2["routing"]["routes"].append(
    tp._r("fw-02", "root", "10.50.0.0/16", [{"ip": "10.70.0.1", "interface": "to-fw01"}])
)
FW2["rules"] = [
    _fw("21", "web-lan", WEB, SRV, 443, "to-fw01", "lan", ("transit", "lan")),
    _fw("22", "web-lan-zu", WEB, SRV, 443, "to-fw01", "lan", ("transit", "lan"), "deny"),
]
FW3 = {"status": "completed", "inventory": {"devices": [{"name": "fw-03", "role": "firewall"}]},
       "rules": [_fw("31", "web-lan-alt", WEB, SRV, 443, "any", "any", (None, None))]}  # fmt: skip


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    systems = (("fw-01", "fortigate", FW1), ("fw-02", "fortigate", FW2),
               ("fw-03", "fortigate", FW3), ("apic-01", "cisco_aci", APIC))  # fmt: skip
    for name, platform, result in systems:
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    return tid


def _connections(client, tid) -> dict:
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ar.WEB}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    r = client.get(f"/applications/{app.json()['id']}/connections")
    assert r.status_code == 200, r.text
    return r.json()


def test_eine_zeile_je_verbindung_ueber_aci_und_zwei_firewalls(client):
    tid = _seed(client)
    out = _connections(client, tid)
    conns = {(c["src"]["ip"], c["dst"]["ip"], c["service"]): c for c in out["connections"]}
    # CT_lan (zur externen EPG, Subnetz) gehoert zur Verbindung zum Host darin - keine eigene
    assert set(conns) == {(WEB, SRV, "tcp/443"), (WEB, SRV, "tcp/8080")}, list(conns)
    https = conns[(WEB, SRV, "tcp/443")]
    hops = [h["label"] for h in https["hops"]]
    assert hops == ["apic-01 · VRF TE:VRF1", "fw-01 · VDOM root", "fw-02"]
    assert https["verdict"] == "allow" and https["reached"]
    assert https["text"] == "erlaubt (2 Firewalls, 1 ACI-VRF)"
    assert https["src"]["label"] == "TE/AP1/EPG_web" and https["direction"] == "out"
    # je Hop die entscheidende Policy bzw. der Contract mit den Objekten
    aci, fw1, fw2 = (h["checks"][0] for h in https["hops"])
    assert aci["src_epg"] == "EPG TE/AP1/EPG_web"
    assert aci["dst_epg"] == "externe EPG L3O_fw|EN_lan (10.60.0.0/24)"
    (ct,) = (e for e in aci["rules"] if e["decides"])
    assert ct["label"] == "Contract CT_lan" and ct["services"] == ["tcp/443"]
    (p11,) = (e for e in fw1["rules"] if e["decides"])
    assert (p11["external_id"], p11["src"], p11["dst"]) == ("11", WEB, SRV)
    assert (fw1["ingress"], fw1["egress"]) == ("to-aci", "to-fw02")
    assert [e["external_id"] for e in fw2["rules"] if e["decides"]] == ["21"]
    # Abgleich mit den Regeln der Doku
    states = {s["label"]: s for s in https["rules"]}
    assert states["Contract CT_lan"]["state"] == "decides"
    assert states["Policy 11 „web-lan“"]["state"] == "decides"
    assert states["Policy 21 „web-lan“"]["state"] == "decides"
    zu = states["Policy 22 „web-lan-zu“"]
    assert (zu["state"], zu["text"]) == ("other", "vorher entscheidet Policy 21 „web-lan“")
    alt = states["Policy 31 „web-lan-alt“"]
    assert (alt["state"], alt["text"]) == (
        "off_path",
        "fw-03 liegt nicht auf dem Weg dieser Verbindung",
    )
    assert https["mismatch"] == [
        "Policy 31 „web-lan-alt“: fw-03 liegt nicht auf dem Weg dieser Verbindung"
    ]

    # fw-01 erlaubt tcp/8080, das ACI hat keinen Contract dafuer: Regel zeigt Zugriff, Weg sperrt
    alt = conns[(WEB, SRV, "tcp/8080")]
    assert alt["verdict"] == "deny" and alt["text"] == "gesperrt an apic-01 · VRF TE:VRF1"
    assert alt["hops"][0]["verdict"] == "implicit_deny"
    assert alt["mismatch"][0] == (
        "Policy 12 „web-lan-8080“ erlaubt – der Weg ist gesperrt an apic-01 · VRF TE:VRF1"
    )
    assert out["summary"] == {"total": 2, "allow": 1, "blocked": 1, "open": 0, "mismatch": 2,
                              "inconsistent": 0}  # fmt: skip
    # Abweichungen zuerst, gesperrte vor erlaubten
    assert out["connections"][0]["service"] == "tcp/8080"


def test_kunden_benutzer_ohne_verbindungen(client):
    """Der Weg zeigt die Infrastruktur - nicht freigegeben fuer Kunden-Benutzer (ADR 0018)."""
    tid = _seed(client)
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ar.WEB}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    r = client.get(f"/applications/{app['id']}/connections",
                   headers={"X-Dev-Roles": "customer", "X-Dev-Customers": "1"})  # fmt: skip
    assert r.status_code == 403


def _mismatches(client, tid) -> dict[str, dict]:
    r = client.get("/findings", params={"tenant_id": tid, "type": "app_connection_mismatch",
                                        "limit": 100})  # fmt: skip
    assert r.status_code == 200, r.text
    return {f["subject_id"]: f for f in r.json()}


def test_durchgehend_gesperrt_ist_kein_befund(client):
    """0.73.0 (Anwender): eine Verbindung, die der Weg durchgehend sperrt bzw. deren Regel nicht
    auf dem Weg liegt, hat einen Grund - in den Verbindungen „Abweichung“, aber kein Befund."""
    tid = _seed(client)
    out = _connections(client, tid)
    assert {c["service"]: c["inconsistent"] for c in out["connections"]} == {
        "tcp/443": False,
        "tcp/8080": False,
    }
    assert out["summary"]["inconsistent"] == 0 and out["summary"]["mismatch"] == 2
    assert _mismatches(client, tid) == {}


# Verbund (0.73.0): fw-r leitet 10.88.0.0/16 per ECMP an fw-p1 und fw-p2; fw-r erlaubt den
# Server 10.10.0.5 zu 10.88.0.7 tcp/443, fw-p1 laesst alles durch, fw-p2 sperrt alles
SRV_A, SRV_B = "10.10.0.5", "10.88.0.7"
FWR = {**tp.FWR, "rules": [_fw("5", "srv-https", SRV_A, SRV_B, 443, "lan", "to-par",
                                (None, None))]}  # fmt: skip
P1 = tp._par("fw-p1", "10.66.0.2/29", allow=True)
P2 = copy.deepcopy(P1)
P2["inventory"] = tp._par("fw-p2", "10.66.0.3/29")["inventory"]
P2["routing"] = tp._par("fw-p2", "10.66.0.3/29")["routing"]
P2["rules"][0] |= {"name": "alles-zu", "action": "deny"}


def _seed_verbund(client) -> tuple[int, dict, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    ids = {}
    for name, result in (("fw-r", FWR), ("fw-p1", P1), ("fw-p2", P2)):
        ids[name] = tp._system(client, tid, name, "fortigate")
        tp._deliver(client, tid, ids[name], "fortigate", result)
    body = {"name": "Srv", "members": [{"kind": "host", "ref": SRV_A}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    return tid, ids, app.json()["id"]


def test_wege_uneinheitlich_als_befund(client):
    """0.73.0: offen ueber einen Weg, gesperrt ueber einen anderen - ECMP bzw. die Firewalls
    eines Verbunds urteilen verschieden; Befund an der erlaubenden Regel der Applikation."""
    tid, ids, aid = _seed_verbund(client)
    (conn,) = client.get(f"/applications/{aid}/connections").json()["connections"]
    assert (conn["src"]["ip"], conn["dst"]["ip"], conn["service"]) == (SRV_A, SRV_B, "tcp/443")
    assert conn["inconsistent"] is True
    ways = {(v["kind"], v["via"], v["verdict"]) for v in conn["variants"]}
    assert ways == {("active", "fw-r → fw-p1", "allow"), ("active", "fw-r → fw-p2", "deny")}
    (f,) = _mismatches(client, tid).values()
    assert f["subject_id"] == "srv-https · Applikation Srv"
    assert "je nach Weg offen bzw. gesperrt" in f["message"]
    assert "über fw-r → fw-p1 erlaubt" in f["message"]
    assert "über fw-r → fw-p2 gesperrt" in f["message"]

    # Verbund: ein Weg, die Firewalls darin urteilen verschieden - weiter uneinheitlich
    body = {"tenant_id": tid, "group": "Nord", "system_ids": [ids["fw-p1"], ids["fw-p2"]]}
    assert client.post("/systems/parallel-group", json=body, headers=tp.ADMIN).status_code == 200
    (conn,) = client.get(f"/applications/{aid}/connections").json()["connections"]
    assert conn["paths"] == 1 and conn["inconsistent"] is True
    assert {v["via"] for v in conn["variants"]} == {"fw-r → fw-p1", "fw-r → fw-p2"}
    assert len(_mismatches(client, tid)) == 1

    # fw-p2 angeglichen: einheitlich erlaubt, der Befund faellt weg
    same = tp._par("fw-p2", "10.66.0.3/29", allow=True)
    tp._deliver(client, tid, ids["fw-p2"], "fortigate", same)
    (conn,) = client.get(f"/applications/{aid}/connections").json()["connections"]
    assert conn["inconsistent"] is False and conn["verdict"] == "allow"
    assert _mismatches(client, tid) == {}


def test_zeitbudget_der_neuberechnung(client, session_factory):
    """Nach dem Zeitbudget ist Schluss: die uebrigen Applikationen behalten ihre Befunde und
    sind beim naechsten Mal zuerst dran."""
    from app.models import Application
    from app.store import reconcile

    tid, _ids, aid = _seed_verbund(client)
    assert len(_mismatches(client, tid)) == 1
    s = session_factory()
    apps = list(s.query(Application).filter(Application.tenant_id == tid))
    issues, failed = reconcile._app_issues(s, tid, apps, budget=1e-9)
    assert issues == [] and failed == {aid}
    assert reconcile._app_next.pop(tid) == aid
    s.close()
    # ohne Grenze wieder alle
    s = session_factory()
    apps = list(s.query(Application).filter(Application.tenant_id == tid))
    issues, failed = reconcile._app_issues(s, tid, apps, budget=None)
    assert len(issues) == 1 and failed == set()
    s.close()
