# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Netzwerkplan je Applikation (0.67.0, ADR 0021 Nr. 6): Systeme der Applikation -> Knoten der
Wege -> Gegenstellen, je Linie die Dienste und an den Enden die Interfaces.

Umgebung aus `test_app_paths`: EPG_web -> apic-01 (VRF TE:VRF1) -> fw-01 -> fw-02 -> 10.60.0.5;
tcp/443 erlaubt, tcp/8080 im ACI gesperrt.
"""

import xml.etree.ElementTree as ET

from app.store import app_plan
from test_app_paths import OP, SRV, WEB, _seed


def _app(client, tid) -> int:
    body = {"name": "Web Shop", "members": [{"kind": "epg", "ref": "uni/tn-TE/ap-AP1/epg-EPG_web"}]}
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_plan_der_applikation(client):
    tid = _seed(client)
    aid = _app(client, tid)
    out = client.get(f"/applications/{aid}/connections").json()
    plan = out["plan"]
    nodes = {n["id"]: n for n in plan["nodes"]}
    # Spalten: Applikation, Weg (apic, fw-01, fw-02), Gegenstellen
    order = sorted(nodes.values(), key=lambda n: n["x"])
    assert [n["title"] for n in order] == ["TE/AP1/EPG_web", "apic-01 · VRF TE:VRF1",
                                           "fw-01 · VDOM root", "fw-02", SRV]  # fmt: skip
    assert [c["title"] for c in plan["columns"]] == ["Applikation", "Weg", "", "", "Gegenstellen"]
    own = nodes["sys:TE/AP1/EPG_web"]
    assert own["role"] == "own" and own["ips"] == [WEB] and own["sub"] == WEB
    # Urteil je Knoten nur aus den Verbindungen, die ihn erreichen: tcp/8080 endet im ACI
    aci = next(n for n in order if n["kind"] == "vrf")
    assert aci["verdict"] == "depends" and aci["sub"] == "ACI-VRF · abhängig"
    assert [n["verdict"] for n in order[2:4]] == ["allow", "allow"]
    # Linien: Dienste je Richtung, hinter der Sperre durchgestrichen, Interfaces an den Enden
    by = {(nodes[e["a"]]["title"], nodes[e["b"]]["title"]): e for e in plan["edges"]}
    first = by[("TE/AP1/EPG_web", "apic-01 · VRF TE:VRF1")]
    assert first["label"] == "→ tcp/443, tcp/8080" and first["verdict"] == "depends"
    fw = by[("fw-01 · VDOM root", "fw-02")]
    assert fw["label"] == "→ tcp/443 · ✕ tcp/8080" and fw["verdict"] == "allow"
    assert (fw["ports_a"], fw["ports_b"]) == (["to-fw02"], ["to-fw01"])
    assert fw["port_a"]["anchor"] == "start" and fw["port_b"]["anchor"] == "end"
    assert fw["port_a"]["x"] > nodes[fw["a"]]["x"] + nodes[fw["a"]]["w"]
    lan = by[("fw-02", SRV)]
    assert lan["ports_a"] == ["lan"] and lan["port_b"] is None and not lan["dashed"]
    assert len(lan["conns"]) == 2 and len(lan["points"]) == 17
    assert plan["summary"] == {"connections": 2, "total": 2, "nodes": 5, "edges": 4}


def test_export_svg_und_drawio(client):
    tid = _seed(client)
    aid = _app(client, tid)
    base = f"/applications/{aid}/connections/plan/export"
    svg = client.get(base)
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert 'filename="netzwerkplan-web-shop-' in svg.headers["content-disposition"]
    root = ET.fromstring(svg.text)
    texts = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")]
    assert "Netzwerkplan Web Shop" in texts and "to-fw02" in texts
    assert "→ tcp/443 · ✕ tcp/8080" in texts
    # nur eine Verbindung (Filter der Ansicht)
    key = f"{WEB}>{SRV}>tcp/443"
    one = client.get(base, params={"format": "drawio", "key": key})
    assert one.status_code == 200 and one.headers["content-disposition"].endswith('.drawio"')
    doc = ET.fromstring(one.text)
    edges = [o for o in doc.iter("UserObject") if o.get("automato_services_out") is not None]
    assert {o.get("label") for o in edges} == {"→ tcp/443"}
    assert any(o.get("automato_ports_a") == "to-fw02" for o in edges)
    assert any("1 Verbindungen (von 2, gefiltert)" in (c.get("value") or "")
               for c in doc.iter("mxCell"))  # fmt: skip
    ports = [c for c in doc.iter("mxCell") if (c.get("style") or "").startswith("edgeLabel")]
    assert {c.get("value") for c in ports} >= {"to-fw02", "to-fw01", "lan"}
    assert client.get(base, params={"format": "pdf"}).status_code == 422
    # nicht fuer Kunden-Benutzer
    cust = {"X-Dev-Roles": "customer", "X-Dev-Customers": "1"}
    assert client.get(base, headers=cust).status_code == 403


def _side(ip, label, kind="object"):
    return {"ip": ip, "label": label, "kind": kind, "name": None, "systems": [label], "more": 0,
            "keys": []}  # fmt: skip


def _hop(key, label, ingress, egress, verdict="allow"):
    check = {"label": label, "verdict": verdict, "text": "", "rules": [], "ingress": ingress,
             "egress": egress}  # fmt: skip
    return {"label": label, "kind": "firewall", "key": key, "source_system_id": 1,
            "context": "root", "verdict": verdict, "checks": [check]}  # fmt: skip


def _conn(key, direction, src, dst, hops, service="tcp/22", verdict="allow", reached=True,
          start=None):  # fmt: skip
    return {"key": key, "direction": direction, "src": src, "dst": dst, "service": service,
            "verdict": verdict, "reached": reached, "start": start, "hops": hops,
            "mismatch": []}  # fmt: skip


def test_eingehend_offen_und_ohne_weg():
    app = _side("10.1.0.10", "vm-app", "system")
    admin, net = _side("10.9.0.5", "Admin"), _side("192.0.2.0/24", "Partner")
    conns = [
        # eingehend: Admin -> fw-a (outside -> inside) -> vm-app; Linie von der App aus
        _conn("in", "in", admin, app, [_hop("1|root", "fw-a", "outside", "inside")]),
        _conn(
            "out",
            "out",
            app,
            admin,
            [_hop("1|root", "fw-a", "inside", "outside")],
            service="tcp/443",
        ),  # fmt: skip
        # Quelle an keinem gelesenen Geraet, Weg ab der Firewall der Regel
        _conn(
            "partner",
            "in",
            net,
            app,
            [_hop("1|root", "fw-a", "wan", "inside")],
            service="udp/53",
            start="fw-a",
        ),  # fmt: skip
        # kein Weg bekannt
        _conn(
            "lost", "out", app, _side("10.8.0.1", "Backup"), [], verdict="unknown", reached=False
        ),  # fmt: skip
    ]
    plan = app_plan.build(conns)
    nodes = {n["id"]: n for n in plan["nodes"]}
    by = {(e["a"], e["b"]): e for e in plan["edges"]}
    inner = by[("sys:vm-app", "hop:1|root")]
    assert inner["label"] == "→ tcp/443 · ← tcp/22, udp/53" and inner["ports_b"] == ["inside"]
    admin_line = by[("hop:1|root", "peer:10.9.0.5")]
    assert admin_line["ports_a"] == ["outside"] and admin_line["label"] == "→ tcp/443 · ← tcp/22"
    partner = by[("hop:1|root", "peer:192.0.2.0/24")]
    assert partner["dashed"] and partner["ports_a"] == ["wan"]
    lost = by[("sys:vm-app", "peer:10.8.0.1")]
    assert lost["dashed"] and lost["verdict"] == "unknown"
    # ueber die Spalte der Firewall: eigene Spur, an der Firewall vorbei
    fw = nodes["hop:1|root"]
    ((x, y),) = lost["via"]
    assert x == fw["x"] + fw["w"] / 2 and not fw["y"] <= y <= fw["y"] + fw["h"]
    assert all(not (fw["x"] < px < fw["x"] + fw["w"] and fw["y"] < py < fw["y"] + fw["h"])
               for px, py in lost["points"])  # fmt: skip
    assert {nodes[k]["x"] for k in ("peer:10.9.0.5", "peer:10.8.0.1", "peer:192.0.2.0/24")} == {
        nodes["hop:1|root"]["x"] + app_plan.NODE_W + app_plan.COL_GAP
    }
    # nur ausgewaehlte Verbindungen (Export mit Filter)
    sub = app_plan.build(conns, {"lost"})
    assert sub["summary"] == {"connections": 1, "total": 4, "nodes": 2, "edges": 1}
    assert "1 Verbindungen (von 4, gefiltert)" in app_plan.svg(sub, "Plan")
