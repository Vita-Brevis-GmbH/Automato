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
    # Spalten: Applikation (EPG im Rahmen ihres APs), Weg (L3Out, fw-01, fw-02), Gegenstellen
    order = sorted(nodes.values(), key=lambda n: n["x"])
    assert [n["title"] for n in order] == ["EPG_web", "L3O_fw", "fw-01 · VDOM root", "fw-02",
                                           SRV]  # fmt: skip
    assert [c["title"] for c in plan["columns"]] == ["Applikation", "Weg", "", "", "Gegenstellen"]
    own = nodes["epg:TE/AP1/EPG_web"]
    assert own["role"] == "own" and own["ips"] == [WEB] and own["sub"] == f"VRF VRF1 · {WEB}"
    (frame,) = plan["groups"]
    assert frame["title"] == "TE/AP1" and frame["nodes"] == [own["id"]]
    assert frame["x"] < own["x"] and frame["y"] < own["y"]
    assert frame["y"] + frame["h"] >= own["y"] + own["h"]
    # das ACI als der L3Out, den das Routing nimmt (Next-Hop im Netz des L3Out-Pfads); Urteil
    # je Knoten nur aus den Verbindungen, die ihn erreichen: tcp/8080 endet im ACI
    aci = order[1]
    assert aci["kind"] == "l3out" and aci["via"] == "apic-01 · VRF TE:VRF1"
    assert aci["verdict"] == "depends" and aci["sub"] == "L3Out · abhängig"
    assert [n["verdict"] for n in order[2:4]] == ["allow", "allow"]
    # Ebene „Regeln“: je Knoten des Wegs die entscheidenden Contracts bzw. Policies mit Diensten
    assert aci["rule_lines"] == ["Contract CT_lan · tcp/443", "✕ implizit gesperrt"]
    assert order[2]["rule_lines"] == ["web-lan (#11) · tcp/443"]
    box = aci["rules_box"]
    assert box["y"] > aci["y"] + aci["h"] and box["h"] > 0 and own["rules_box"] is None
    # Linien: Dienste je Richtung, hinter der Sperre durchgestrichen, Interfaces an den Enden
    by = {(nodes[e["a"]]["title"], nodes[e["b"]]["title"]): e for e in plan["edges"]}
    first = by[("EPG_web", "L3O_fw")]
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
    # Aufbau der Applikation fuer den Plan
    assert out["members"]["epgs"][0]["ap"] == "AP1"


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
    # 0.74.0: Rahmen des APs und die Ebene „Regeln“ (ausblendbar), ohne sie mit rules=false
    cells = {c.get("id"): c for c in doc.iter("mxCell") if c.get("id")}
    assert cells["regeln"].get("value") == "Regeln" and cells["regeln"].get("parent") == "0"
    rules = [o for o in doc.iter("UserObject") if o.get("automato_rules")]
    assert {o.get("automato_rules") for o in rules} >= {"Contract CT_lan", "web-lan (#11)"}
    assert all(o.find("mxCell").get("parent") == "regeln" for o in rules)
    assert any(c.get("value") == "TE/AP1" for c in doc.iter("mxCell"))
    bare = ET.fromstring(client.get(base, params={"format": "drawio", "rules": "false"}).text)
    assert not [o for o in bare.iter("UserObject") if o.get("automato_rules")]
    plain = client.get(base, params={"rules": "false"}).text
    assert 'id="regeln"' in svg.text and 'id="regeln"' not in plain
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


def test_kompakt_nach_ap_und_epg():
    """0.74.0: VMs in ihrer EPG, Rahmen je AP bzw. „ohne EPG“, eine Linie je EPG statt je VM;
    das ACI zur externen EPG als deren L3Out, wenn das Routing keinen nennt."""
    members = {
        "epgs": [
            {"key": "epg:1", "label": "TE/AP_web/EPG_web", "name": "EPG_web", "tenant": "TE",
             "ap": "AP_web", "vrf": "VRF1"},
            {"key": "epg:2", "label": "TE/AP_db/EPG_db", "name": "EPG_db", "tenant": "TE",
             "ap": "AP_db", "vrf": "VRF1"},
        ],
        "systems": [
            *({"key": f"vm:{i}", "name": f"web0{i}", "kind": "vm", "ips": [f"10.1.0.{i}"],
               "epgs": ["TE/AP_web/EPG_web"]} for i in range(1, 9)),
            {"key": "vm:20", "name": "db01", "kind": "vm", "ips": ["10.2.0.1"],
             "epgs": ["TE/AP_db/EPG_db"]},
            {"key": "host:1", "name": "srv-phys", "kind": "host", "ips": ["10.3.0.1"],
             "epgs": []},
        ],
    }  # fmt: skip

    def own(name, key, ip):
        return {**_side(ip, name, "system"), "keys": [key]}

    vrf = {**_hop("4|TE:VRF1", "apic-01 · VRF TE:VRF1", None, "vlan5"), "kind": "vrf"}
    vrf["checks"][0]["rules"] = [{"name": "CT_ext", "label": "Contract CT_ext",
                                  "kind": "contract", "action": "allow", "services": ["tcp/443"],
                                  "decides": True, "rule_id": 9}]  # fmt: skip
    ext = {**_side("192.0.2.0/24", "externe EPG L3O_inet|EN_inet", "external"),
           "l3out": "L3O_inet"}  # fmt: skip
    conns = [
        _conn(
            f"w{i}", "out", own(f"web0{i}", f"vm:{i}", f"10.1.0.{i}"), ext, [vrf], service="tcp/443"
        )  # fmt: skip
        for i in (1, 2)
    ] + [
        _conn("db", "out", own("db01", "vm:20", "10.2.0.1"), ext, [vrf], service="tcp/443"),
        _conn(
            "phys",
            "out",
            own("srv-phys", "host:1", "10.3.0.1"),
            _side("10.9.0.9", "Backup"),
            [],
            verdict="unknown",
            reached=False,
        ),  # fmt: skip
    ]
    plan = app_plan.build(conns, members=members)
    nodes = {n["id"]: n for n in plan["nodes"]}
    web = nodes["epg:TE/AP_web/EPG_web"]
    # die VMs in der EPG (hoechstens sechs, der Rest als Zahl), der Kasten waechst mit
    assert web["lines"] == [f"web0{i}" for i in range(1, 7)] and web["more"] == 2
    assert web["h"] == app_plan.NODE_H + 7 * app_plan.LINE_H + 4
    assert "sys:web01" not in nodes  # eine Linie je EPG, nicht je VM
    # externe EPG als Gegenstelle: das ACI als ihr L3Out, mit dem Contract in der Ebene „Regeln“
    l3o = nodes["l3o:4|TE:VRF1|L3O_inet"]
    assert l3o["title"] == "L3O_inet" and l3o["rule_lines"] == ["Contract CT_ext · tcp/443"]
    assert len([e for e in plan["edges"] if e["b"] == l3o["id"]]) == 2  # web, db
    # Rahmen: AP_db, AP_web, zuletzt „ohne EPG“ - untereinander, ohne Ueberlappung
    frames = plan["groups"]
    assert [f["title"] for f in frames] == ["TE/AP_db", "TE/AP_web", "ohne EPG"]
    for a, b in zip(frames, frames[1:], strict=False):
        assert a["y"] + a["h"] < b["y"]
    assert frames[2]["nodes"] == ["sys:srv-phys"]
    svg = app_plan.svg(plan, "Plan")
    assert "• web01" in svg and "+2 weitere" in svg and "TE/AP_web" in svg
    assert "Contract CT_ext · tcp/443" in svg
    assert "Contract CT_ext" not in app_plan.svg(plan, "Plan", rules=False)


def test_load_balancer_in_der_epg_seiner_vip():
    """0.85.0 (Anwender): Internet → FortiGate (VIP) → L3Out → Load Balancer in der EPG seiner
    VIP → EPG der Server. Der Weg geht nach dem Load Balancer in derselben VRF weiter - kein
    zweiter VRF-Knoten; der Contract VIP-EPG → Server-EPG steht im Kasten des Load Balancers."""
    members = {
        "epgs": [{"key": "epg:1", "label": "TE/AP_web/EPG_web", "name": "EPG_web",
                  "tenant": "TE", "ap": "AP_web", "vrf": "VRF1"}],
        "systems": [{"key": "vm:1", "name": "web01", "kind": "vm", "ips": ["10.1.0.11"],
                     "epgs": ["TE/AP_web/EPG_web"]}],
    }  # fmt: skip
    own = {**_side("203.0.113.10", "web01", "system"), "keys": ["vm:1"]}
    inet = _side("198.51.100.0/24", "Partner")
    fw = _hop("1|root", "fw-edge", "wan", "to-aci")
    fw["checks"][0]["nat"] = {"name": "V-EXT_shop", "to": ["10.30.0.50"], "balancer": "vs-web"}
    fw["checks"][0]["rules"] = [{"name": "inet-to-shop", "external_id": "30", "kind": "policy",
                                 "action": "allow", "services": ["tcp/443"], "decides": True,
                                 "rule_id": 3}]  # fmt: skip
    lb = {"name": "vs-web", "ext": "10.30.0.50", "port": "443", "pool": "pool-web",
          "to": ["10.1.0.11"], "epg": "TE/AP_lb/EPG_vip"}  # fmt: skip
    at = {**_hop("4|TE:VRF1", "apic · VRF TE:VRF1", "eth1/1", None), "kind": "vrf", "lb": lb}
    then = {**_hop("4|TE:VRF1", "apic · VRF TE:VRF1", None, None), "kind": "vrf"}
    then["checks"][0]["rules"] = [{"name": "CT_vip_web", "label": "Contract CT_vip_web",
                                   "kind": "contract", "action": "allow",
                                   "services": ["tcp/8080"], "decides": True,
                                   "rule_id": 7}]  # fmt: skip
    conn = _conn("shop", "in", inet, own, [fw, at, then], service="tcp/443")
    plan = app_plan.build([conn], members=members)
    nodes = {n["id"]: n for n in plan["nodes"]}
    lbn = nodes["lb:TE/AP_lb/EPG_vip"]
    assert (lbn["kind"], lbn["title"]) == ("lb", "EPG_vip")
    assert lbn["sub"] == "Load Balancer · VIP 10.30.0.50:443"
    assert lbn["lines"] == ["vs-web 10.30.0.50:443 · Pool pool-web"]
    assert lbn["rule_lines"] == ["apic · VRF TE:VRF1: Contract CT_vip_web · tcp/8080"]
    # die VRF nur einmal: Internet → FortiGate → VRF (Eingang) → Load Balancer → EPG der Server
    assert [n["id"] for n in plan["nodes"] if n["id"].startswith("hop:4")] == ["hop:4|TE:VRF1"]
    pairs = {(e["a"], e["b"]) for e in plan["edges"]}
    assert ("epg:TE/AP_web/EPG_web", "lb:TE/AP_lb/EPG_vip") in pairs
    assert ("lb:TE/AP_lb/EPG_vip", "hop:4|TE:VRF1") in pairs
    # VIP der Firewall als Zeile am Knoten
    assert nodes["hop:1|root"]["lines"] == ["VIP V-EXT_shop → 10.30.0.50"]
    assert lbn["verdict"] == "allow"
    assert 'automato_kind="Load Balancer"' in app_plan.drawio(plan, "Plan")
