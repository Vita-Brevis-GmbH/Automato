# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Weg-Suche mit einem Netz als Quelle bzw. Ziel (0.39.0, Anwender): das Netz als Ganzes.

- Routing: es gilt die Route, die das ganze Netz enthaelt (`10.30.0.0/23` -> `10.30.0.0/16`);
  genauere Routen innerhalb des Netzes mit anderem Next-Hop als Hinweis.
- Policies: sicher nur, wenn ein Objekt das ganze Netz enthaelt (`10.10.0.0/23` in
  `10.10.0.0/16`); erfasst es nur einen Teil, ist die Policy „moeglich“ („nur teilweise“).
- ACI: EPG bzw. externe EPG ueber ein Subnetz, das das ganze Netz enthaelt.
"""

import test_aci_check as ac
import test_policy_check as pc

FW = {
    "status": "completed",
    "rules": [
        pc._policy(1, "net-web", "lan", "dmz", ["10.10.0.0/16"], ["10.30.0.0/16"], ["tcp/443"]),
        pc._policy(2, "host-ssh", "lan", "dmz", ["10.10.0.5"], ["10.30.0.10"], ["tcp/22"]),
        pc._policy(3, "deny", "lan", "dmz", ["all"], ["all"], ["all"], "deny"),
    ],
    "inventory": {
        "devices": [{"name": "fw-r", "role": "firewall"}],
        "interfaces": [
            pc._iface("lan", "10.10.0.1/22", "lan"),
            pc._iface("dmz", "10.30.0.1/24", "dmz"),
            pc._iface("wan", "198.51.100.2/30", "internet"),
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-r", "context": "root"}],
        "routes": [
            pc._route("10.10.0.0/22", [{"interface": "lan"}], "connected"),
            pc._route("10.30.0.0/24", [{"interface": "dmz"}], "connected"),
            pc._route("10.30.0.0/16", [{"ip": "10.30.0.254", "interface": "dmz"}]),
            pc._route("198.51.100.0/30", [{"interface": "wan"}], "connected"),
            pc._route("0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan"}]),
        ],
    },
}


def _seed(client) -> int:
    old = pc.FW
    pc.FW = FW
    try:
        return pc._seed(client)
    finally:
        pc.FW = old


def _trace(client, tid, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_netz_als_ganzes_route_und_policy(client):
    tid = _seed(client)
    nets = {"src": "10.10.0.0/23", "dst": "10.30.0.0/23"}

    # /23 liegt ganz in der Regel /16: Policy 1 erlaubt sicher
    out = _trace(client, tid, **nets, service="tcp/443")
    (path,) = out["paths"]
    (hop,) = path["hops"]
    assert hop["via"] == "Quelle angeschlossen" and hop["ingress"]["interface"] == "lan"
    assert hop["rules"]["text"] == "erlaubt durch Policy 1 „net-web“"
    assert path["policy"] == {"verdict": "allow", "text": "erlaubt"}
    # Route, die das ganze Netz enthält; der genauere Teil geht anders (angeschlossen an dmz)
    assert hop["route"]["prefix"] == "10.30.0.0/16"
    assert hop["route"]["inner"] == [
        {"prefix": "10.30.0.0/24", "protocol": "connected", "via": "dmz"}
    ]
    assert out["notes"] == [
        "Teile von 10.30.0.0/23 werden an fw-r anders geroutet: 10.30.0.0/24 über dmz"
    ]

    # Policy 2 erfasst nur einzelne Hosts: nur teilweise - möglich vor der sperrenden
    rules = _trace(client, tid, **nets, service="tcp/22")["paths"][0]["hops"][0]["rules"]
    assert rules["verdict"] == "depends"
    assert [(r["external_id"], r["unsure"]) for r in rules["rules"]] == [
        ("2", ["Quelle nur teilweise", "Ziel nur teilweise"]),
        ("3", []),
    ]
    assert rules["rules"][0]["src"] == "10.10.0.5 (Teil)"  # das Objekt der Policy
    assert rules["text"].endswith("abhängig von: Quelle nur teilweise, Ziel nur teilweise")

    # ein Netz, das kein Gerät ganz angeschlossen hat: Startpunkt wählen
    out = _trace(client, tid, src="10.10.0.0/16", dst="10.30.0.0/23")
    assert out["paths"] == []
    assert out["notes"][0].startswith("Kein gelesenes Gerät hat 10.10.0.0/16 (das ganze Netz)")


def test_netz_in_lookup_und_validierung(client):
    tid = _seed(client)
    rows = client.get("/routing/lookup", params={"tenant_id": tid, "ip": "10.30.0.0/23"}).json()
    (row,) = rows
    assert row["route"]["prefix"] == "10.30.0.0/16"
    # einzelne IP wie bisher: das längste Präfix
    (row,) = client.get("/routing/lookup", params={"tenant_id": tid, "ip": "10.30.0.7"}).json()
    assert row["route"]["prefix"] == "10.30.0.0/24"
    bad = client.get("/routing/trace", params={"tenant_id": tid, "src": "x", "dst": "10.30.0.0/23"})
    assert bad.status_code == 422 and "Netz" in bad.json()["detail"]


def test_netz_ueber_firewall_und_aci(client):
    """Netz hinter der Firewall ins EPG-Subnetz: im ACI erfasst EN_srv (/24) die Quelle /25
    ganz, EPG_web (/24) das Ziel /25 - CT_srv erlaubt; die Firewall-Regel nennt nur Hosts."""
    tid = ac._seed(client)
    out = _trace(client, tid, src="10.20.0.0/25", dst="10.50.1.0/25", service="tcp/443")
    (path,) = out["paths"]
    fw, aci = path["hops"]
    assert fw["rules"]["rules"][0]["unsure"] == ["Quelle nur teilweise", "Ziel nur teilweise"]
    assert aci["rules"]["src_epg"] == "externe EPG L3O_fw|EN_srv (10.20.0.0/24)"
    assert aci["rules"]["dst_epg"] == "EPG TE/AP1/EPG_web"
    assert aci["rules"]["text"] == "erlaubt durch Contract CT_srv"

    # Ziel umfasst mehrere EPGs: Zuordnung nur für Teile - nicht sicher
    out = _trace(client, tid, src="10.20.0.0/25", dst="10.50.0.0/22", service="tcp/443")
    hop = next(h for p in out["paths"] for h in p["hops"] if h["kind"] == "vrf")
    assert hop["rules"]["dst_epg"].startswith("Teile von 10.50.0.0/22: EPG TE/AP1/EPG_web")
