# ruff: noqa: S104 - 0.0.0.0/0 sind Routen bzw. Selektoren, keine Bind-Adressen
"""IPsec-Tunnel in Weg-Suche, Verbindungen, Netzwerkplan und Befunden (1.19.0, ADR 0063).

Topologie (Beispiele aus den Dokumentationsbereichen):
- fw-a: LAN 10.10.0.0/24, wan1 203.0.113.1/30; Tunnel `vpn-b` zur Gegenstelle 198.51.100.2
  (wan1 von fw-b), Phase 2 10.10.0.0/24 <-> 10.20.0.0/16 und 10.21.0.0/17; Routen ueber vpn-b
  nach 10.20.0.0/16 (gedeckt), 10.21.0.0/16 (nur teilweise) und 10.30.0.0/16 (ohne Selektor);
  Tunnel `vpn-partner` zu 192.0.2.77 (kein gelesenes Geraet, down) mit 192.0.2.128/26; Dial-up
  `dialup` mit einer verbundenen Gegenstelle (10.77.0.0/24).
- fw-b: LAN 10.20.0.0/24, wan1 198.51.100.2/30; Tunnel `vpn-a` zur Gegenstelle 203.0.113.1.
"""

import copy

import pytest
import test_paths as tp
from app.models import DataChange
from sqlalchemy import select

OP = {"X-Dev-Roles": "operator"}
FW_A, FW_B = "fw-a", "fw-b"
LAN_A, LAN_B, PARTNER, DIALUP = "10.10.0.5", "10.20.0.5", "192.0.2.130", "10.77.0.9"


def _p2(name, src, dst, status="up"):
    return {"name": name, "src": src, "dst": dst, "status": status}


def _tunnel(node, name, gw, phase2, status="up", kind="static", **kw):
    return {"node": node, "context": "root", "name": name, "kind": kind, "interface": "wan1",
            "remote_gw": gw, "ike_version": 2, "status": status, "phase2": phase2,
            "attrs": {"proposal": "aes256gcm-prfsha384"}, **kw}  # fmt: skip


def _policy(pid, src_if, dst_if, src, dst):
    return {"source_platform": "fortigate", "external_id": str(pid), "name": f"p{pid}",
            "action": "allow", "sources": [src], "destinations": [dst],
            "services": ["tcp/443"], "src_zone": src_if, "dst_zone": dst_if, "vdom": "root",
            "family": "v4",
            "raw": {"policyid": pid, "srcintf": [{"name": src_if}], "dstintf": [{"name": dst_if}],
                    "status": "enable", "schedule": "always"}}  # fmt: skip


FWA = {
    "status": "completed",
    "rules": [
        _policy(1, "lan", "vpn-b", LAN_A, "10.20.0.0/24"),
        _policy(2, "lan", "vpn-partner", LAN_A, "192.0.2.128/26"),
    ],
    "inventory": {
        "devices": [{"name": FW_A, "role": "firewall"}],
        "interfaces": [
            tp._if(FW_A, "lan", "10.10.0.1/24", "lan"),
            tp._if(FW_A, "wan1", "203.0.113.1/30", "internet"),
            {"device": FW_A, "name": "vpn-b", "kind": "tunnel", "vdom": "root"},
            {"device": FW_A, "name": "vpn-partner", "kind": "tunnel", "vdom": "root"},
        ],
    },
    "routing": {
        "contexts": [{"node": FW_A, "context": "root"}],
        "routes": [
            tp._r(FW_A, "root", "10.10.0.0/24", [{"interface": "lan"}], "connected"),
            tp._r(FW_A, "root", "203.0.113.0/30", [{"interface": "wan1"}], "connected"),
            tp._r(FW_A, "root", "0.0.0.0/0", [{"ip": "203.0.113.2", "interface": "wan1"}]),
            tp._r(FW_A, "root", "10.20.0.0/16", [{"interface": "vpn-b"}]),
            tp._r(FW_A, "root", "10.21.0.0/16", [{"interface": "vpn-b"}]),
            tp._r(FW_A, "root", "10.30.0.0/16", [{"interface": "vpn-b"}]),
            tp._r(FW_A, "root", "192.0.2.128/26", [{"interface": "vpn-partner"}]),
            tp._r(FW_A, "root", "10.77.0.0/24", [{"interface": "dialup_0"}],
                  attrs={"is_tunnel_route": True, "tunnel_parent": "dialup"}),
        ],
        "vpn": [
            _tunnel(FW_A, "vpn-b", "198.51.100.2", [
                _p2("vpn-b-p2", ["10.10.0.0/24"], ["10.20.0.0/16"]),
                _p2("vpn-b-p2b", ["10.10.0.0/24"], ["10.21.0.0/17"])]),
            _tunnel(FW_A, "vpn-partner", "192.0.2.77", [
                _p2("vpn-partner-p2", ["10.10.0.0/24"], ["192.0.2.128/26"], "down")],
                status="down"),
            _tunnel(FW_A, "dialup", None, [_p2("dialup-p2", ["0.0.0.0/0"], ["0.0.0.0/0"])],
                    kind="dialup", instances=[{"name": "dialup_0", "remote_gw": "192.0.2.150",
                                               "status": "up", "phase2": {"dialup-p2": "up"},
                                               "nets": ["10.77.0.0/24"]}]),
        ],
    },
}  # fmt: skip

FWB = {
    "status": "completed",
    "rules": [_policy(5, "vpn-a", "lan", "10.10.0.0/24", "10.20.0.0/24")],
    "inventory": {
        "devices": [{"name": FW_B, "role": "firewall"}],
        "interfaces": [
            tp._if(FW_B, "lan", "10.20.0.1/24", "lan"),
            tp._if(FW_B, "wan1", "198.51.100.2/30", "internet"),
            {"device": FW_B, "name": "vpn-a", "kind": "tunnel", "vdom": "root"},
        ],
    },
    "routing": {
        "contexts": [{"node": FW_B, "context": "root"}],
        "routes": [
            tp._r(FW_B, "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
            tp._r(FW_B, "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
            tp._r(FW_B, "root", "10.10.0.0/24", [{"interface": "vpn-a"}]),
        ],
        "vpn": [_tunnel(FW_B, "vpn-a", "203.0.113.1",
                        [_p2("vpn-a-p2", ["10.20.0.0/16"], ["10.10.0.0/24"])])],
    },
}  # fmt: skip


def _partner_up() -> dict:
    """fw-a mit dem Tunnel zum Partner up - ein Tunnel, der down ist, traegt nichts (ADR 0063
    Nachtrag): der Weg endet dann als „verworfen“, nicht an der Gegenstelle."""
    fwa = copy.deepcopy(FWA)
    fwa["routing"]["vpn"][1]["status"] = "up"
    fwa["routing"]["vpn"][1]["phase2"][0]["status"] = "up"
    return fwa


def _seed(client, fwa: dict = FWA, fwb: dict | None = FWB,
          tenant: str = "Kunde A") -> tuple[int, dict[str, int]]:  # fmt: skip
    tid = client.post("/tenants", json={"name": tenant}, headers=tp.ADMIN).json()["id"]
    sids = {}
    for name, result in ((FW_A, fwa), (FW_B, fwb)):
        if result is None:
            continue
        sids[name] = tp._system(client, tid, name, "fortigate")
        tp._deliver(client, tid, sids[name], "fortigate", result)
    return tid, sids


def _vpn(hop: dict) -> dict:
    return (hop.get("egress") or {}).get("vpn") or {}


def test_weg_ueber_tunnel_zwischen_zwei_fortigates(client):
    tid, sids = _seed(client)
    res = tp._trace(client, tid, src=LAN_A, dst=LAN_B, service="tcp/443")
    (path,) = res["paths"]
    assert tp._labels(path) == [FW_A, FW_B]
    a, b = path["hops"]
    v = _vpn(a)
    assert (a["egress"]["interface"], v["name"], v["status"], v["remote_gw"]) == (
        "vpn-b", "vpn-b", "up", "198.51.100.2")  # fmt: skip
    assert (v["covered"], v["phase2"], v["kind"]) == ("full", "vpn-b-p2", "static")
    assert v["peer"] == {
        "key": f"{sids[FW_B]}|root",
        "source_system_id": sids[FW_B],
        "context": "root",
        "label": FW_B,
        "tunnel": "vpn-a",
        "how": "gw",
        "pair": "auto",  # 1.23.0: beide Enden gelesen - automatisch gekoppelt
    }
    # Eingang auf fw-b ist sein Tunnel zu fw-a - die Policies pruefen ihn
    assert b["ingress"]["interface"] == "vpn-a"
    assert b["via"] == "über VPN vpn-b (Gegenstelle 198.51.100.2)"
    assert path["end"]["kind"] == "connected"
    assert a["rules"]["verdict"] == "allow" and b["rules"]["verdict"] == "allow"
    # Rueckrichtung: dieselbe Kennung des Tunnels (Netzwerkplan zeichnet ihn einmal)
    back = tp._trace(client, tid, src=LAN_B, dst=LAN_A)["paths"][0]
    assert tp._labels(back) == [FW_B, FW_A] and _vpn(back["hops"][0])["id"] == v["id"]
    assert tp._trace(client, tid, dst=LAN_B, src=LAN_A)["notes"] == []


def test_weg_endet_sichtbar_an_fremder_gegenstelle(client):
    tid, _sids = _seed(client)
    (path,) = tp._trace(client, tid, src=LAN_A, dst=PARTNER)["paths"]
    assert tp._labels(path) == [FW_A]
    v = _vpn(path["hops"][0])
    assert (v["name"], v["status"], "peer" in v) == ("vpn-partner", "down", False)
    # ADR 0063 Nachtrag (ADR 0070): Tunnel down - die Firewall verwirft, nie „erreicht“
    assert path["end"] == {"kind": "dropped",
                           "text": "verworfen an fw-a: VPN vpn-partner ist down"}  # fmt: skip
    assert path["hops"][0]["dropped"]["stage"] == "ipsec"
    # derselbe Tunnel up: der Weg endet sichtbar an der Gegenstelle ohne gelesenes Geraet
    tid_up, _ = _seed(client, _partner_up(), tenant="Kunde C")
    (up,) = tp._trace(client, tid_up, src=LAN_A, dst=PARTNER)["paths"]
    assert up["end"]["kind"] == "vpn"
    assert up["end"]["text"] == (
        "weiter über VPN vpn-partner zur Gegenstelle 192.0.2.77 – kein gelesenes Gerät, der Weg "
        "dahinter ist nicht geprüft"
    )
    # Dial-up: Instanz in der Routing-Tabelle, Gegenstelle der verbundenen Instanz
    (dial,) = tp._trace(client, tid, src=LAN_A, dst=DIALUP)["paths"]
    d = _vpn(dial["hops"][0])
    assert (d["kind"], d["instance"], d["remote_gw"], d["covered"]) == (
        "dialup", "dialup_0", "192.0.2.150", "full")  # fmt: skip
    assert dial["end"]["kind"] == "vpn" and "Dial-up-VPN dialup (dialup_0)" in dial["end"]["text"]
    # Route ueber den Tunnel ohne passenden Selektor: verworfen wie am Geraet (ADR 0063
    # Nachtrag; bis 1.20 nur ein Hinweis, der Weg ging weiter)
    res = tp._trace(client, tid, src=LAN_A, dst="10.30.0.5")
    assert _vpn(res["paths"][0]["hops"][0])["covered"] == "none"
    assert tp._labels(res["paths"][0]) == [FW_A]
    assert res["paths"][0]["end"] == {
        "kind": "dropped",
        "text": "verworfen an fw-a: VPN vpn-b – keine Phase 2, deren Selektoren Quelle und Ziel "
                "enthalten",
    }  # fmt: skip
    # englisch
    en = client.get("/routing/trace", params={"tenant_id": tid_up, "src": LAN_A, "dst": PARTNER},
                    headers={"Accept-Language": "en"}).json()  # fmt: skip
    assert en["paths"][0]["end"]["text"].startswith("onward via VPN vpn-partner to the peer")
    en = client.get("/routing/trace", params={"tenant_id": tid, "src": LAN_A, "dst": PARTNER},
                    headers={"Accept-Language": "en"}).json()  # fmt: skip
    assert en["paths"][0]["end"]["text"] == "dropped at fw-a: VPN vpn-partner is down"


def test_gegenstelle_hinter_nat_ueber_ihren_tunnel_erkannt(client):
    """Die Adresse der Gegenstelle gehoert keinem gelesenen Geraet (NAT davor), aber der Tunnel
    von fw-b zeigt auf die Adresse von fw-a - eindeutig, also dort weiter."""
    fwa = copy.deepcopy(FWA)
    fwa["routing"]["vpn"][0]["remote_gw"] = "198.51.100.200"  # oeffentliche Adresse vor fw-b
    tid, sids = _seed(client, fwa)
    (path,) = tp._trace(client, tid, src=LAN_A, dst=LAN_B)["paths"]
    assert tp._labels(path) == [FW_A, FW_B]
    v = _vpn(path["hops"][0])
    assert (v["peer"]["how"], v["peer"]["tunnel"]) == ("reverse", "vpn-a")
    assert "erkannt am Tunnel der Gegenstelle" in path["hops"][1]["via"]


def test_ohne_gegenstelle_wie_bisher_und_fw_b_ohne_tunnel(client):
    """fw-b gelesen, aber ohne IPsec (z. B. nicht lesbar): der Weg geht trotzdem zu fw-b, Eingang
    unbekannt; ohne fw-b endet er an der Gegenstelle."""
    fwb = copy.deepcopy(FWB)
    fwb["routing"]["vpn"] = []
    fwb["routing"]["contexts"][0]["missing"] = ["IPsec Phase 1 (HTTP 403)"]
    tid, _sids = _seed(client, FWA, fwb)
    (path,) = tp._trace(client, tid, src=LAN_A, dst=LAN_B)["paths"]
    assert tp._labels(path) == [FW_A, FW_B]
    assert "dort kein passender Tunnel gelesen" in path["hops"][1]["via"]
    assert path["hops"][1]["missing"] == ["IPsec Phase 1 (HTTP 403)"]
    # „nicht lesbar“ des IPsec sperrt keinen Vergleich der Routen im Verlauf (ADR 0030)
    more = copy.deepcopy(fwb)
    more["routing"]["routes"].append(tp._r(FW_B, "root", "10.99.0.0/16", [{"interface": "lan"}]))
    tp._deliver(client, tid, _sids[FW_B], "fortigate", more)
    changes = client.get("/routing/changes", params={"tenant_id": tid}).json()
    assert [(c["kind"], c["change"], c["prefix"]) for c in changes if c["kind"] == "route"] == [
        ("route", "added", "10.99.0.0/16")]  # fmt: skip


def test_ansicht_vpn_tunnel(client):
    tid, sids = _seed(client)
    r = client.get("/vpn/tunnels", params={"tenant_id": tid})
    assert r.status_code == 200, r.text
    out = r.json()
    rows = {(t["source"], t["name"]): t for t in out["tunnels"]}
    assert set(rows) == {(FW_A, "vpn-b"), (FW_A, "vpn-partner"), (FW_A, "dialup"),
                         (FW_B, "vpn-a")}  # fmt: skip
    b = rows[(FW_A, "vpn-b")]
    assert (b["status"], b["phase2_up"], b["phase2_total"], b["routes"]) == ("up", 2, 2, 3)
    assert (b["uncovered"], b["partial"]) == (1, 1)
    assert [(p["label"], p["tunnel"]) for p in b["peers"]] == [(FW_B, "vpn-a")]
    assert rows[(FW_A, "vpn-partner")]["peers"] == []
    assert rows[(FW_A, "dialup")]["kind"] == "dialup" and rows[(FW_A, "dialup")]["routes"] == 1
    assert out["counts"]["up"] == 3 and out["counts"]["down"] == 1
    # Filter: Zustand, Suche (auch in den Netzen dahinter)
    down = client.get("/vpn/tunnels", params={"tenant_id": tid, "status": "down"}).json()
    assert [t["name"] for t in down["tunnels"]] == ["vpn-partner"]
    found = client.get("/vpn/tunnels", params={"tenant_id": tid, "q": "192.0.2.128"}).json()
    assert [t["name"] for t in found["tunnels"]] == ["vpn-partner"]

    detail = client.get(f"/vpn/tunnels/{b['id']}", params={"tenant_id": tid}).json()
    routes = {x["prefix"]: x["covered"] for x in detail["route_list"]}
    assert routes == {"10.20.0.0/16": "full", "10.21.0.0/16": "partial", "10.30.0.0/16": "none"}
    behind = {x["net"]: x["from"] for x in detail["behind"]}
    assert behind["10.20.0.0/16"] == ["phase2:vpn-b-p2", "route:rib"]
    assert behind["10.30.0.0/16"] == ["route:rib"]
    assert [p["name"] for p in detail["phase2"]] == ["vpn-b-p2", "vpn-b-p2b"]
    assert detail["attrs"] == {"proposal": "aes256gcm-prfsha384"}
    dial = client.get(f"/vpn/tunnels/{rows[(FW_A, 'dialup')]['id']}",
                      params={"tenant_id": tid}).json()  # fmt: skip
    assert dial["dialup"][0]["remote_gw"] == "192.0.2.150"
    assert client.get("/vpn/tunnels/99999", params={"tenant_id": tid}).status_code == 404
    # Modul Analyse aus: keine Ansicht
    assert client.put("/modules/analyse", params={"tenant_id": tid}, json={"enabled": False},
                      headers=tp.ADMIN).status_code == 200  # fmt: skip
    assert client.get("/vpn/tunnels", params={"tenant_id": tid}).status_code == 403


def _findings(client, tid) -> dict[str, list[dict]]:
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    out: dict[str, list[dict]] = {}
    for f in client.get("/findings", params={"tenant_id": tid, "limit": 5000}).json():
        if f["type"].startswith("vpn_"):
            out.setdefault(f["type"], []).append(f)
    return out


def test_befunde_der_tunnel(client):
    tid, _sids = _seed(client)
    found = _findings(client, tid)
    assert set(found) == {"vpn_tunnel_down", "vpn_selector_partial", "vpn_route_no_selector"}
    (down,) = found["vpn_tunnel_down"]
    assert down["subject_id"] == "fw-a · VPN vpn-partner"
    assert down["message"] == "VPN „vpn-partner“ auf fw-a zur Gegenstelle 192.0.2.77 ist down."
    (partial,) = found["vpn_selector_partial"]
    assert partial["detail"]["prefixes"] == ["10.21.0.0/16"]
    assert "nur teilweise: 10.21.0.0/16" in partial["message"]
    (none,) = found["vpn_route_no_selector"]
    assert none["detail"]["prefixes"] == ["10.30.0.0/16"]
    assert "ohne passenden Phase-2-Selektor" in none["message"]
    # Dial-up ohne verbundene Gegenstelle ist kein „down“; Zustand unbekannt: kein Befund
    fwa = copy.deepcopy(FWA)
    fwa["routing"]["vpn"][2]["instances"] = []
    fwa["routing"]["vpn"][2]["status"] = "idle"
    fwa["routing"]["vpn"][1]["status"] = None
    tid2, _ = _seed(client, fwa, None, "Kunde B")
    assert "vpn_tunnel_down" not in _findings(client, tid2)


def _app_connections(client, tid, host=LAN_A) -> dict:
    body = {"name": "Web", "members": [{"kind": "host", "ref": host}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    r = client.get(f"/applications/{app.json()['id']}/connections")
    assert r.status_code == 200, r.text
    return app.json()["id"], r.json()


def test_verbindungen_netzwerkplan_und_zaehlung(client):
    tid, sids = _seed(client, _partner_up())  # Tunnel down = verworfen (ADR 0063 Nachtrag)
    app_id, out = _app_connections(client, tid)
    conns = {c["dst"]["ip"]: c for c in out["connections"]}
    lan_b = conns["10.20.0.0/24"]
    assert [h["label"] for h in lan_b["hops"]] == [FW_A, FW_B]
    assert lan_b["hops"][0]["vpn"]["name"] == "vpn-b" and lan_b["reached"]
    partner = conns["192.0.2.128/26"]
    assert partner["vpn_end"] == [{"name": "vpn-partner", "remote_gw": "192.0.2.77",
                                   "kind": "static"}]  # fmt: skip
    # gezaehlt (ADR 0048): unvollstaendig geprueft, mit Hinweis - nie still
    assert out["coverage"]["complete"] is False
    assert out["coverage"]["cuts"]["vpn"] == {"rules": 1, "missing": 1}
    assert any("enden an der Gegenstelle eines VPN-Tunnels" in n for n in out["notes"])
    assert out["summary"]["vpn"] == 2
    assert [(x["name"], x["connections"]) for x in out["vpn"]] == [("vpn-b", 1), ("vpn-partner", 1)]
    # Netzwerkplan: der Tunnel als eigener Knoten zwischen den Firewalls
    plan = out["plan"]
    vpn_nodes = {n["title"]: n for n in plan["nodes"] if n["kind"] == "vpn"}
    assert set(vpn_nodes) == {"VPN vpn-b", "VPN vpn-partner"}
    assert vpn_nodes["VPN vpn-b"]["sub"].startswith("VPN · up")
    ids = {n["id"]: n["title"] for n in plan["nodes"]}
    edges = {(ids[e["a"]], ids[e["b"]]) for e in plan["edges"]}
    assert (FW_A, "VPN vpn-b") in edges and ("VPN vpn-b", FW_B) in edges
    # Befund „unvollstaendig geprueft“ mit dem Hinweis auf die Gegenstelle
    (f,) = client.get("/findings", params={"tenant_id": tid, "type": "app_coverage"}).json()
    assert "enden an der Gegenstelle eines VPN-Tunnels" in f["message"]
    assert f["hint"].startswith("VPN-Gegenstelle ohne gelesenes Gerät")


def test_datenstand_zustand_nur_das_geraet_konfiguration_alle(client, session_factory):
    tid, sids = _seed(client)
    s = session_factory()
    before = s.scalar(select(DataChange.seq).where(DataChange.tenant_id == tid)
                      .order_by(DataChange.seq.desc()).limit(1))  # fmt: skip
    s.close()
    flapped = copy.deepcopy(FWA)
    flapped["routing"]["vpn"][0]["status"] = "down"
    tp._deliver(client, tid, sids[FW_A], "fortigate", flapped)
    changed = copy.deepcopy(flapped)
    changed["routing"]["vpn"][0]["phase2"][1]["dst"] = ["10.21.0.0/16"]
    tp._deliver(client, tid, sids[FW_A], "fortigate", changed)
    s = session_factory()
    rows = list(s.scalars(select(DataChange).where(DataChange.tenant_id == tid,
                                                    DataChange.seq > before)
                          .order_by(DataChange.seq)))  # fmt: skip
    s.close()
    assert [(r.parts, r.all, r.devices) for r in rows] == [
        (["routing.vpn_state"], False, [sids[FW_A]]),  # Zustand: nur Applikationen ueber fw-a
        (["routing.vpn"], True, None),  # Konfiguration: die Gegenstelle kann anders sein
    ]


@pytest.mark.parametrize("lang", ["de", "en"])
def test_texte_zweisprachig(client, lang):
    tid, _sids = _seed(client)
    headers = {"Accept-Language": lang}
    res = client.get("/routing/trace", params={"tenant_id": tid, "src": LAN_A, "dst": LAN_B},
                     headers=headers).json()  # fmt: skip
    via = res["paths"][0]["hops"][1]["via"]
    assert via.startswith("über VPN" if lang == "de" else "via VPN")


def test_erklaerung_und_export(client):
    tid, _sids = _seed(client)
    out = client.get("/routing/explain", params={"tenant_id": tid, "src": LAN_A, "dst": LAN_B})
    assert out.status_code == 200, out.text
    (path,) = out.json()["paths"]
    step = path["hops"][0]["steps"][-1]
    assert step["kind"] == "next_hop" and step["text"].startswith(
        "Weiter durch VPN vpn-b (Gegenstelle 198.51.100.2) zu fw-b")  # fmt: skip
    out = client.get("/routing/explain", params={"tenant_id": tid, "src": LAN_A, "dst": PARTNER})
    end = out.json()["paths"][0]["end"]
    # Tunnel down (ADR 0063 Nachtrag): verworfen, mit Erklaerung
    assert end["status"] == "stop" and "nur, solange er up ist" in end["hint"]
    tid_up, _ = _seed(client, _partner_up(), tenant="Kunde C")
    out = client.get("/routing/explain",
                     params={"tenant_id": tid_up, "src": LAN_A, "dst": PARTNER})  # fmt: skip
    end = out.json()["paths"][0]["end"]
    assert end["status"] == "info" and "durch einen VPN-Tunnel" in end["hint"]

    import io
    import zipfile

    app_id, _out = _app_connections(client, tid)
    r = client.get(f"/applications/{app_id}/export", params={"format": "docx"})
    assert r.status_code == 200, r.text
    xml = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode()
    assert "VPN vpn-b (198.51.100.2)" in xml and "VPN vpn-partner (192.0.2.77)" in xml
