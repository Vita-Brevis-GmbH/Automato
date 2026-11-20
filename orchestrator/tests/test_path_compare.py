"""Wege vergleichen (1.19.0, ADR 0067): ein L3-Weg gegen L1/L2 - VLAN auf allen Trunks erlaubt,
Kabel und Nachbarn (NetBox, Verkabelung), Spanning Tree; je Prüfung positiv und negativ, das
Nicht-Prüfbare gezählt, das Modul je Mandant schaltbar.

Aufbau: fw-a (FortiGate) mit Subinterface `transit` (VLAN 120) auf port1 an sw-1 Gi1/0/1; sw-1 mit
zwei Kabeln an sw-2 (Gi1/0/47 und Gi1/0/48, Trunks 120,130); sw-2 Gi1/0/1 an fw-b port2, dort
`transit` (VLAN 120). fw-a routet 10.50.0.0/24 über 10.120.0.2 (fw-b), fw-b 10.10.0.0/16 über
10.120.0.1. Spanning Tree: sw-2 Gi1/0/47 blockiert VLAN 120 (Reserve). NetBox kennt alle vier
Kabel.
"""

import copy

from app.models import Finding
from app.store import path_compare
from sqlalchemy import select
from test_cable_labels import _cable, _netbox
from test_cabling import _port

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
TRUNK = {"mode": "tagged", "untagged_vlan": 1, "native_vlan": 1, "trunk_vlans": "120,130",
         "tagged_vlans": [120, 130]}  # fmt: skip


def _fw(name, port, ip, routes, extra=()):
    return {
        "status": "completed",
        "inventory": {
            "devices": [{"name": name, "role": "firewall"}],
            "interfaces": [
                {"device": name, "name": port, "kind": "physical", "mode": "tagged",
                 "tagged_vlans": [120], "vdom": "root"},
                {"device": name, "name": "transit", "kind": "vlan", "mode": "tagged",
                 "tagged_vlans": [120], "parent": port, "ips": [ip], "vdom": "root"},
                *extra,
            ],
        },
        "routing": {"contexts": [{"node": name, "context": "root"}], "routes": routes},
    }  # fmt: skip


def _r(node, prefix, hops, protocol="static"):
    return {"node": node, "context": "root", "prefix": prefix, "protocol": protocol,
            "next_hops": hops}  # fmt: skip


FW_A = _fw("fw-a", "port1", "10.120.0.1/29", [
    _r("fw-a", "10.120.0.0/29", [{"interface": "transit"}], "connected"),
    _r("fw-a", "10.10.0.0/16", [{"interface": "lan"}], "connected"),
    _r("fw-a", "10.50.0.0/24", [{"ip": "10.120.0.2", "interface": "transit"}]),
], [{"device": "fw-a", "name": "lan", "kind": "physical", "ips": ["10.10.0.1/16"],
     "vdom": "root"}])  # fmt: skip
FW_B = _fw("fw-b", "port2", "10.120.0.2/29", [
    _r("fw-b", "10.120.0.0/29", [{"interface": "transit"}], "connected"),
    _r("fw-b", "10.50.0.0/24", [{"interface": "srv"}], "connected"),
    _r("fw-b", "10.10.0.0/16", [{"ip": "10.120.0.1", "interface": "transit"}]),
], [{"device": "fw-b", "name": "srv", "kind": "physical", "ips": ["10.50.0.1/24"],
     "vdom": "root"}])  # fmt: skip


def _nb(device, port, remote, remote_port):
    return {"device": device, "port": port, "protocol": "cdp", "remote_name": remote,
            "remote_port": remote_port}  # fmt: skip


def _stp_port(port, role, state):
    return {"port": port, "role": role, "state": state, "cost": 4, "priority": "128.1",
            "type": "P2p"}  # fmt: skip


def _inst(device, vlan, ports, root=False):
    mac = "52:54:00:aa:00:01" if device == "sw-1" else "52:54:00:aa:00:02"
    return {"device": device, "instance": f"VLAN{vlan:04d}", "vlan": vlan, "mst": None,
            "protocol": "rstp", "root_priority": 24576, "root_mac": "52:54:00:aa:00:01",
            "root_cost": 0 if root else 4, "root_port": None if root else "Gi1/0/48",
            "is_root": root, "bridge_priority": 32768, "bridge_mac": mac,
            "ports": ports}  # fmt: skip


def _switches(acc_vlans="120,130", blocked=("Gi1/0/47",), stp_data=True):
    """sw-1 und sw-2; `acc_vlans`: erlaubte VLANs der Trunks zwischen sw-1 und sw-2,
    `blocked`: Ports von sw-2, die VLAN 120 blockieren."""
    low = {**TRUNK, "trunk_vlans": acc_vlans,
           "tagged_vlans": [int(v) for v in acc_vlans.split(",")]}  # fmt: skip
    result = {
        "status": "completed",
        "inventory": {
            "devices": [{"name": "sw-1", "role": "switch", "mgmt_ip": "10.0.0.11"},
                        {"name": "sw-2", "role": "switch", "mgmt_ip": "10.0.0.12"}],
            "interfaces": [
                _port("sw-1", "Gi1/0/1", **TRUNK),
                _port("sw-1", "Gi1/0/47", **low),
                _port("sw-1", "Gi1/0/48", **low),
                _port("sw-2", "Gi1/0/1", **TRUNK),
                _port("sw-2", "Gi1/0/47", **low),
                _port("sw-2", "Gi1/0/48", **low),
            ],
            "neighbors": [
                _nb("sw-1", "Gi1/0/1", "fw-a", "port1"),
                _nb("sw-1", "Gi1/0/47", "sw-2", "GigabitEthernet1/0/47"),
                _nb("sw-1", "Gi1/0/48", "sw-2", "GigabitEthernet1/0/48"),
                _nb("sw-2", "Gi1/0/47", "sw-1", "GigabitEthernet1/0/47"),
                _nb("sw-2", "Gi1/0/48", "sw-1", "GigabitEthernet1/0/48"),
                _nb("sw-2", "Gi1/0/1", "fw-b", "port2"),
            ],
        },
    }  # fmt: skip
    if stp_data:
        inv = result["inventory"]
        inv["stp_devices"] = [
            {"device": "sw-1", "status": "read", "mode": "rapid-pvst"},
            {"device": "sw-2", "status": "read", "mode": "rapid-pvst"},
        ]
        sw2 = [_stp_port(p, "alternate" if p in blocked else "root",
                         "blocking" if p in blocked else "forwarding")
               for p in ("Gi1/0/47", "Gi1/0/48")]  # fmt: skip
        inv["stp"] = [
            _inst("sw-1", 120, [_stp_port("Gi1/0/47", "designated", "forwarding"),
                                _stp_port("Gi1/0/48", "designated", "forwarding")], root=True),
            _inst("sw-2", 120, sw2 + [_stp_port("Gi1/0/1", "designated", "forwarding")]),
        ]  # fmt: skip
    return result


CABLES = [
    _cable(51, "K-1", ("fw-a", "port1"), ("sw-1", "GigabitEthernet1/0/1")),
    _cable(52, "K-2", ("sw-1", "GigabitEthernet1/0/47"), ("sw-2", "GigabitEthernet1/0/47")),
    _cable(53, "K-3", ("sw-1", "GigabitEthernet1/0/48"), ("sw-2", "GigabitEthernet1/0/48")),
    _cable(54, "K-4", ("sw-2", "GigabitEthernet1/0/1"), ("fw-b", "port2")),
]  # fmt: skip


def _deliver(client, tid, sid, platform, result):
    req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup(client, switches=None, cables=CABLES, firewalls=(("fw-a", FW_A), ("fw-b", FW_B))):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]

    def system(**body) -> int:
        r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    for name, result in firewalls:
        _deliver(client, tid, system(name=name, platform="fortigate",
                                     base_url=f"https://{name}.example.ch"),
                 "fortigate", result)  # fmt: skip
    sw = system(name="Switches A", platform="cisco_switch", read_scope="10.0.0.11\n10.0.0.12",
                username="ro", password="x")  # fmt: skip
    _deliver(client, tid, sw, "cisco_switch", switches or _switches())
    if cables is not None:
        nb = system(name="netbox", platform="netbox", base_url="https://nb.example.ch",
                    token="nb-read")  # fmt: skip
        _netbox(client, tid, nb, cables)
    r = client.post("/reconcile", params={"tenant_id": tid}, headers=OP)
    assert r.status_code == 200, r.text
    return tid


def _sections(client, tid, **params):
    r = client.get("/path-compare/sections", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _findings(client, tid, ftype):
    r = client.get("/findings", params={"tenant_id": tid, "type": ftype, "limit": 500})
    assert r.status_code == 200, r.text
    return r.json()


def _path_types(client, tid) -> set[str]:
    r = client.get("/findings", params={"tenant_id": tid, "limit": 5000})
    return {f["type"] for f in r.json() if f["type"].startswith("path_")}


def test_alles_passt(client, session_factory):
    """Positiv: VLAN 120 auf allen Trunks erlaubt, Kabel passen zu NetBox, Spanning Tree
    blockiert nur die Reserve (Gi1/0/47) - der Weg läuft über Gi1/0/48."""
    tid = _setup(client)
    data = _sections(client, tid)
    (sec,) = data["items"]
    assert sec["label"] == "fw-a transit → fw-b transit"
    assert (sec["state"], sec["vlan"], sec["vlan_from"]) == ("ok", 120, "subinterface")
    assert sec["segment"]["text"] == ("fw-a port1 → sw-1 (Gi1/0/1 → Gi1/0/48) → sw-2 (Gi1/0/48 "
                                      "→ Gi1/0/1) → fw-b port2")  # fmt: skip
    checks = sec["checks"]
    assert {k: c["state"] for k, c in checks.items()} == {"vlan": "ok", "cable": "ok",
                                                          "stp": "ok"}  # fmt: skip
    assert checks["vlan"]["count"] == 3 and checks["cable"]["count"] == 3
    assert sec["unchecked"] == []
    assert data["summary"]["deviations"] == {"vlan": 0, "cable": 0, "stp": 0}
    assert _path_types(client, tid) == set()

    # Weg der Weg-Suche: derselbe Abschnitt zwischen den Hops
    r = client.get("/path-compare/trace", params={"tenant_id": tid, "src": "10.10.0.5",
                                                  "dst": "10.50.0.10"})  # fmt: skip
    assert r.status_code == 200, r.text
    out = r.json()
    (path,) = (p for p in out["paths"] if p["kind"] == "active")
    assert path["hops"] == ["fw-a", "fw-b"]
    (hop,) = path["sections"]
    assert hop["state"] == "ok" and hop["segment"]["switches"] == 2
    assert out["summary"]["states"] == {"ok": 1}
    # wie eine Verbindung einer Applikation
    r = client.get(
        "/path-compare/trace",
        params={"tenant_id": tid, "src": "10.10.0.5", "dst": "10.50.0.10", "connection": "true"},
    )
    assert r.status_code == 200 and r.json()["summary"]["states"] == {"ok": 1}


def test_vlan_nicht_erlaubt(client):
    """Negativ: die Trunks zwischen sw-1 und sw-2 lassen VLAN 120 nicht zu."""
    tid = _setup(client, _switches(acc_vlans="130"))
    (sec,) = _sections(client, tid)["items"]
    assert sec["state"] == "deviation" and sec["checks"]["vlan"]["state"] == "deviation"
    (item,) = (i for i in sec["checks"]["vlan"]["items"] if i["kind"] == "not_allowed")
    assert {p["device"] for p in item["ports"]} == {"sw-1", "sw-2"}
    assert {p["port"] for p in item["ports"]} == {"Gi1/0/48"}  # die Reserve blockiert
    assert {p["vlans"] for p in item["ports"]} == {"130"}
    assert sec["checks"]["stp"]["state"] == "ok"
    (f,) = _findings(client, tid, "path_vlan_missing")
    assert f["subject_id"] == "fw-a transit → fw-b transit" and f["severity"] == "warning"
    assert "VLAN 120 ist auf dem Weg nicht überall erlaubt" in f["message"]
    assert "sw-1 Gi1/0/4" in f["message"]
    assert _path_types(client, tid) == {"path_vlan_missing"}


def test_vlan_am_eingang_anders(client):
    """Negativ: fw-b hat `transit` in VLAN 130 - beide Enden in verschiedenen VLANs."""
    fw_b = copy.deepcopy(FW_B)
    for i in fw_b["inventory"]["interfaces"]:
        if i["name"] in ("port2", "transit"):
            i["tagged_vlans"] = [130]
    tid = _setup(client, firewalls=(("fw-a", FW_A), ("fw-b", fw_b)))
    (sec,) = _sections(client, tid)["items"]
    kinds = [i["kind"] for i in sec["checks"]["vlan"]["items"]]
    assert "other_vlan" in kinds
    (f,) = _findings(client, tid, "path_vlan_missing")
    assert "Eingang in VLAN 130" in f["message"] or "VLAN 120" in f["message"]


def test_spanning_tree_blockiert(client):
    """Negativ: sw-2 blockiert VLAN 120 auf beiden Kabeln zu sw-1 - kein offener Weg."""
    tid = _setup(client, _switches(blocked=("Gi1/0/47", "Gi1/0/48")))
    (sec,) = _sections(client, tid)["items"]
    stp = sec["checks"]["stp"]
    assert stp["state"] == "deviation" and sec["checks"]["vlan"]["state"] == "ok"
    blocked = [i for i in stp["items"] if i["kind"] == "blocked"]
    assert {(b["device"], b["port"], b["state"], b["role"]) for b in blocked} == {
        ("sw-2", "Gi1/0/47", "blocking", "alternate"),
        ("sw-2", "Gi1/0/48", "blocking", "alternate")}  # fmt: skip
    (f,) = _findings(client, tid, "path_stp_blocked")
    assert "Spanning Tree blockiert VLAN 120 auf jedem Weg" in f["message"]
    assert _path_types(client, tid) == {"path_stp_blocked"}


def test_err_disabled_blockiert(client):
    """Negativ: der einzige offene Port ist err-disabled (BPDU-Guard)."""
    sw = _switches()
    sw["inventory"]["stp_devices"][1]["errdisabled"] = [{"port": "Gi1/0/48", "reason": "bpduguard"}]
    tid = _setup(client, sw)
    (sec,) = _sections(client, tid)["items"]
    states = {i.get("state") for i in sec["checks"]["stp"]["items"] if i["kind"] == "blocked"}
    assert "err-disabled" in states
    assert _findings(client, tid, "path_stp_blocked")


def test_kabel_passt_nicht_zu_netbox(client):
    """Negativ: NetBox führt K-3 von sw-1 Gi1/0/48 zu sw-2 Gi1/0/46, CDP meldet Gi1/0/48."""
    cables = copy.deepcopy(CABLES)
    cables[2] = _cable(53, "K-3", ("sw-1", "GigabitEthernet1/0/48"),
                       ("sw-2", "GigabitEthernet1/0/46"))  # fmt: skip
    tid = _setup(client, cables=cables)
    (sec,) = _sections(client, tid)["items"]
    cable = sec["checks"]["cable"]
    assert cable["state"] == "deviation"
    (item,) = cable["items"]
    assert (item["kind"], item["cable"]) == ("netbox", "K-3")
    (f,) = _findings(client, tid, "path_cable_mismatch")
    assert "Kabel K-3 in NetBox passt nicht zu CDP/LLDP" in f["message"]
    # derselbe Befund der Verkabelung
    assert _findings(client, tid, "cable_neighbor_mismatch")


def test_kabel_befund_der_verkabelung(client):
    """Negativ: Speed an den Enden von K-3 verschieden (Befund der Verkabelung auf dem Weg)."""
    sw = _switches()
    for p in sw["inventory"]["interfaces"]:
        if (p["device"], p["name"]) == ("sw-2", "Gi1/0/48"):
            p["speed"] = "100"
    tid = _setup(client, sw)
    (sec,) = _sections(client, tid)["items"]
    kinds = {i["kind"] for i in sec["checks"]["cable"]["items"]}
    assert "link_speed_mismatch" in kinds
    (f,) = _findings(client, tid, "path_cable_mismatch")
    assert "Speed" in f["message"]


def test_nicht_pruefbar_gezaehlt(client, session_factory):
    """Ohne NetBox und Spanning Tree, dazu fw-c ohne Kabel: nichts still - je Grund gezählt."""
    fw_c = _fw("fw-c", "port9", "10.120.0.3/29", [
        _r("fw-c", "10.120.0.0/29", [{"interface": "transit"}], "connected"),
        _r("fw-c", "10.10.0.0/16", [{"ip": "10.120.0.1", "interface": "transit"}]),
    ])  # fmt: skip
    tid = _setup(client, _switches(stp_data=False), cables=None,
                 firewalls=(("fw-a", FW_A), ("fw-b", FW_B), ("fw-c", fw_c)))  # fmt: skip
    data = _sections(client, tid)
    by_label = {s["label"]: s for s in data["items"]}
    ab = by_label["fw-a transit → fw-b transit"]
    assert ab["state"] == "partial"
    assert {r["key"] for r in ab["checks"]["cable"]["reasons"]} == {"no_netbox"}
    assert {r["key"] for r in ab["checks"]["stp"]["reasons"]} == {"stp_unread"}
    cut = next(s for label, s in by_label.items() if "fw-c" in label)
    assert cut["state"] == "unchecked" and cut["unchecked"] == ["no_cabling"]
    counts = {u["key"]: u["count"] for u in data["summary"]["unchecked"]}
    assert counts == {"no_cabling": 1, "no_netbox": 1, "stp_unread": 1}
    found = {f["detail"]["reason"]: f for f in _findings(client, tid, "path_l2_unchecked")}
    assert set(found) == {"no_cabling", "no_netbox", "stp_unread"}
    assert found["no_cabling"]["severity"] == "info"
    assert "1 L3-Abschnitte nicht (ganz) geprüft" in found["no_cabling"]["message"]
    assert "kein Kabel am Aus- bzw. Eingang bekannt" in found["no_cabling"]["message"]
    # Filter der Liste
    assert [s["label"] for s in _sections(client, tid, state="unchecked")["items"]] == [
        cut["label"]]  # fmt: skip
    assert _sections(client, tid, q="fw-c")["total"] == 1
    assert client.get("/path-compare/sections",
                      params={"tenant_id": tid, "state": "x"}).status_code == 422  # fmt: skip
    # nichts gespeichert ausser den Befunden
    with session_factory() as s:
        assert {f.type.value for f in s.scalars(select(Finding))
                if f.type.value.startswith("path_")} == {"path_l2_unchecked"}  # fmt: skip


def test_tunnel_und_inter_vdom_sind_kein_l2():
    sec = path_compare.Section(
        {"label": "a", "devices": [], "interface": "x"},
        {"label": "b", "devices": [], "interface": "y"},
        kind="vpn",
    )
    assert sec.state == "vpn" and sec.unchecked() == []


def test_modul_schaltbar_und_nicht_fuer_kunden(client):
    tid = _setup(client, _switches(acc_vlans="130"))
    assert _findings(client, tid, "path_vlan_missing")
    states = {m["key"]: m for m in client.get("/modules", params={"tenant_id": tid}).json()}
    assert states["vergleich"]["switchable"] and states["vergleich"]["depends"] == [
        "analyse",
        "wege",
    ]
    assert states["vergleich"]["views"] == ["vergleich"]
    # Kunden-Benutzer: nie (Infrastruktur)
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": "1"}
    assert client.get("/path-compare/sections", params={"tenant_id": tid},
                      headers=who).status_code == 403  # fmt: skip
    # Analyse aus: Wege vergleichen ist mit aus (Abhängigkeit), API 403, Befunde fallen weg
    put = client.put("/modules/analyse", params={"tenant_id": tid}, json={"enabled": False},
                     headers=ADMIN)  # fmt: skip
    assert put.status_code == 200
    states = {m["key"]: m for m in put.json()}
    assert states["vergleich"]["enabled"] and not states["vergleich"]["effective"]
    r = client.get("/path-compare/sections", params={"tenant_id": tid})
    assert r.status_code == 403 and r.json()["detail"]["module"] == "vergleich"
    client.put("/modules/analyse", params={"tenant_id": tid}, json={"enabled": True},
               headers=ADMIN)  # fmt: skip
    # selbst aus: 403, die Prüfungen laufen nicht mehr
    assert client.put("/modules/vergleich", params={"tenant_id": tid}, json={"enabled": False},
                      headers=ADMIN).status_code == 200  # fmt: skip
    r = client.get("/path-compare/trace", params={"tenant_id": tid, "dst": "10.50.0.10",
                                                  "src": "10.10.0.5"})  # fmt: skip
    assert r.status_code == 403 and r.json()["detail"]["code"] == "module_disabled"
    client.post("/reconcile", params={"tenant_id": tid}, headers=OP)
    assert _findings(client, tid, "path_vlan_missing") == []
    # andere Module unverändert an
    states = {
        m["key"]: m["effective"] for m in client.get("/modules", params={"tenant_id": tid}).json()
    }
    assert all(on for key, on in states.items() if key != "vergleich")


def test_ohne_verkabelung_keine_befunde(client):
    """Ohne gelesene Verkabelung gibt es nichts zu vergleichen: keine Befunde, die Ansicht zeigt
    die Abschnitte als nicht prüfbar."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    for name, result in (("fw-a", FW_A), ("fw-b", FW_B)):
        body = {"tenant_id": tid, "name": name, "platform": "fortigate",
                "base_url": f"https://{name}.example.ch"}  # fmt: skip
        _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
                 "fortigate", result)  # fmt: skip
    client.post("/reconcile", params={"tenant_id": tid}, headers=OP)
    data = _sections(client, tid)
    assert data["cabling"] is False
    assert [(s["state"], s["unchecked"]) for s in data["items"]] == [("unchecked", ["no_cabling"])]
    assert _path_types(client, tid) == set()


def test_vlan_durch_das_aci_fabric_darf_anders_heissen(client):
    """1.23.0 (Anwender: „VLAN … nur innerhalb des Standortes und BridgeDomain gleich“): sw-1 und
    sw-2 haengen am Leaf leaf-101 - am einen Port VLAN 120, am anderen VLAN 130 (dieselbe Bridge
    Domain). Hinter dem Fabric gilt das VLAN des Eingangs: keine Abweichung, kein „anderes VLAN“."""
    fw_b = copy.deepcopy(FW_B)
    for i in fw_b["inventory"]["interfaces"]:
        if i["name"] in ("port2", "transit"):
            i["tagged_vlans"] = [130]
    sw = _switches(stp_data=False)
    inv = sw["inventory"]
    inv["interfaces"] = [i for i in inv["interfaces"] if i["name"] != "Gi1/0/47"]
    inv["neighbors"] = [
        _nb("sw-1", "Gi1/0/1", "fw-a", "port1"),
        _nb("sw-1", "Gi1/0/48", "leaf-101", "Ethernet1/1"),
        _nb("sw-2", "Gi1/0/48", "leaf-101", "Ethernet1/2"),
        _nb("sw-2", "Gi1/0/1", "fw-b", "port2"),
    ]
    tid = _setup(client, sw, cables=None, firewalls=(("fw-a", FW_A), ("fw-b", fw_b)))
    apic = client.post(
        "/systems",
        json={
            "tenant_id": tid,
            "name": "apic",
            "platform": "cisco_aci",
            "base_url": "https://apic",
        },
        headers=ADMIN,
    ).json()
    _deliver(
        client,
        tid,
        apic["id"],
        "cisco_aci",
        {
            "status": "completed",
            "inventory": {
                "devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"}],
                "interfaces": [
                    {
                        "device": "leaf-101",
                        "name": "eth1/1",
                        "kind": "physical",
                        "mode": "tagged",
                        "tagged_vlans": [120],
                        "neighbor": "sw-1",
                        "neighbor_port": "Gi1/0/48",
                    },
                    {
                        "device": "leaf-101",
                        "name": "eth1/2",
                        "kind": "physical",
                        "mode": "tagged",
                        "tagged_vlans": [130],
                        "neighbor": "sw-2",
                        "neighbor_port": "Gi1/0/48",
                    },
                ],
            },
        },
    )
    (sec,) = _sections(client, tid)["items"]
    assert "leaf-101" in sec["segment"]["text"]
    vlan = sec["checks"]["vlan"]
    assert vlan["state"] == "ok" and vlan["items"] == [], vlan
    assert vlan["count"] == 4
