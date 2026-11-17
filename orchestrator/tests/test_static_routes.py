# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Statische Routen (1.19.0, Anwender 2026-11-17, Nr. 2 „statisches Routing“): Ansicht je Geraet
und VRF, Befunde (Next-Hop nicht erreichbar, ueberdeckt, doppelt, Blackhole, nicht geprueft) und
in der Doku der Applikation die statischen Routen ihrer Wege - mit Fingerabdruck (ADR 0037).

Topologie: fw-01 mit LAN (port1), WAN (port2), DMZ (port3) und einem Tunnel-Interface; seine
statischen Routen decken jeden Zustand ab (aktiv, Backup, ueberdeckt, nicht installiert,
deaktiviert, Adressobjekt, Prioritaet, doppelt, Blackhole). fw-02 mit nicht lesbarer
Routing-Tabelle, der Router rt-01 (nur Routing-Tabelle, Null0) und ein APIC mit statischen
Routen an einem L3Out (eine davon ohne Next-Hop = Null-Route) und einem Bridge-Domain-Subnetz.
"""

import copy

import pytest
import test_paths as tp
from app.config import get_settings
from app.i18n import use
from app.store import doc_snapshots, static_routes
from app.store.finding_text import explain
from test_app_paths import FW1 as APP_FW1
from test_app_paths import _connections
from test_app_paths import _seed as _seed_app

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}


def _r(prefix, hops, protocol="static", table="rib", node="fw-01", context="root", **kw):
    return {"node": node, "context": context, "prefix": prefix, "table": table,
            "protocol": protocol, "next_hops": hops, **kw}  # fmt: skip


def _st(seq, prefix, ip=None, iface=None, distance=10, best=True, **attrs):
    """Statische Route der Konfiguration (FortiGate `router/static`)."""
    return _r(prefix, [{"ip": ip, "interface": iface}], table="static", distance=distance,
              best=best, attrs={"seq-num": seq, **attrs})  # fmt: skip


def _c(prefix, iface, node="fw-01", context="root"):
    return _r(prefix, [{"interface": iface}], "connected", node=node, context=context, distance=0)


def _h(ip, iface):
    return [{"ip": ip, "interface": iface}]


FW1 = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-01", "role": "firewall"}],
        "interfaces": [
            tp._if("fw-01", "port1", "10.1.0.1/24", "lan"),
            tp._if("fw-01", "port2", "192.0.2.2/30", "wan"),
            tp._if("fw-01", "port3", "10.2.0.1/24", "dmz"),
            {"device": "fw-01", "name": "vpn-kunde", "ips": [], "vdom": "root"},
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-01", "context": "root"}],
        "routes": [
            # Routing-Tabelle (wirksam)
            _c("10.1.0.0/24", "port1"),
            _c("192.0.2.0/30", "port2"),
            _c("10.2.0.0/24", "port3"),
            _r("0.0.0.0/0", _h("192.0.2.1", "port2"), distance=10),
            _r("10.50.0.0/16", _h("10.1.0.254", "port1"), "bgp", distance=20),
            _r("10.60.0.0/24", _h("10.2.0.254", "port3"), distance=10),
            _r("10.70.0.0/24", _h("10.1.0.253", "port1"), distance=10),
            _r("10.80.0.0/24", _h("10.1.0.254", "port1"), distance=10),
            _r("10.80.0.0/25", _h("10.2.0.254", "port3"), distance=10),
            _r("10.80.0.128/25", _h("10.2.0.254", "port3"), distance=10),
            _r("10.99.0.0/16", [{"interface": "Null"}], distance=254),
            _r("10.85.0.0/16", _h("10.2.0.240", "port3"), distance=10),
            _r("10.88.0.0/16", [{"interface": "vpn-kunde"}], distance=10),
            _r("10.89.0.0/16", [{"interface": "vpn-kunde"}], distance=15),  # IPsec add-route
            _r("10.65.0.0/24", _h("10.1.0.240", "port1") + _h("10.2.0.240", "port3"),
               distance=10),
            # Konfiguration
            _st(1, "0.0.0.0/0", "192.0.2.1", "port2", comment="Internet"),
            _st(2, "10.50.0.0/16", "10.2.0.254", "port3", distance=200),  # Backup zu BGP
            _st(3, "10.1.0.0/24", "10.2.0.250", "port3"),  # angeschlossen gewinnt: ueberdeckt
            _st(4, "10.60.0.0/24", "10.2.0.254", "port3"),
            _st(5, "10.70.0.0/24", "10.1.0.253", "port1"),
            _st(6, "10.70.0.0/24", "10.1.0.253", "port1"),  # doppelt
            _st(7, "10.80.0.0/24", "10.1.0.254", "port1"),  # spezifischere decken alles ab
            _st(8, "10.80.0.0/25", "10.2.0.254", "port3"),
            _st(9, "10.80.0.128/25", "10.2.0.254", "port3"),
            _st(10, "10.99.0.0/16", distance=254, blackhole=True),
            _st(11, "172.16.0.0/16", "10.9.9.9", "port1", distance=250),  # nur ueber Default
            _st(12, "10.50.0.0/16", "10.7.7.7", "port3", distance=250),  # Backup, unerreichbar
            _st(13, "10.40.0.0/16", "10.1.0.250", "port1", best=False),  # deaktiviert
            _st(14, "0.0.0.0/0", "10.2.0.240", "port3", dstaddr="N_partner"),
            _st(15, "10.88.0.0/16", None, "vpn-kunde"),
            _st(16, "10.65.0.0/24", "10.1.0.240", "port1", priority=0),
            _st(17, "10.65.0.0/24", "10.2.0.240", "port3", priority=5),  # Backup (Prioritaet)
            _st(18, "10.66.0.0/24", "10.1.0.77", "port3"),  # Netz an port1, nicht port3
            _st(19, "10.50.0.0/16", "10.1.0.254", "port1", distance=250),  # Backup zu BGP
            _st(20, "10.99.0.0/16", "10.2.0.254", "port3", distance=255),  # Blackhole gewinnt
            _st(21, "10.67.0.0/24", "10.2.0.10", "port3"),  # nicht installiert, Next-Hop ok
        ],
    },
}  # fmt: skip

FW2 = {
    "status": "completed",
    "inventory": {"devices": [{"name": "fw-02", "role": "firewall"}]},
    "routing": {
        "contexts": [{"node": "fw-02", "context": "root",
                      "missing": ["Routing-Tabelle IPv4 (HTTP 403)"]}],
        "routes": [_st(1, "10.10.0.0/16", "10.0.0.1", "port1") | {"node": "fw-02"}],
    },
}  # fmt: skip

ROUTER = {
    "status": "completed",
    "inventory": {"devices": [{"name": "rt-01", "role": "router"}]},
    "routing": {
        "contexts": [{"node": "rt-01", "context": "rt-01", "kind": "l3"}],
        "routes": [
            _c("10.3.0.0/30", "Gi0/1", "rt-01", "rt-01"),
            _r("10.3.0.1/32", [{"interface": "Gi0/1"}], "local", node="rt-01", context="rt-01",
               distance=0),
            _r("10.30.0.0/16", _h("10.3.0.2", "Gi0/1"), node="rt-01", context="rt-01",
               distance=1),
            _r("192.0.2.128/25", [{"interface": "Null0"}], node="rt-01", context="rt-01",
               distance=1),
        ],
    },
}  # fmt: skip

APIC = {
    "status": "completed",
    "inventory": {"devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"}]},
    "routing": {
        "contexts": [{"node": "leaf-101", "context": "TE:VRF1", "kind": "vrf"}],
        "routes": [
            _r("10.95.0.0/16", _h("10.255.0.1", "vlan-3001"), node="leaf-101",
               context="TE:VRF1", distance=1),
            # Bridge-Domain-Subnetz (pervasive): keine statische Route der L3Outs
            _r("10.50.1.0/24", [{"ip": "10.0.72.64"}], node="leaf-101", context="TE:VRF1"),
        ],
        "l3outs": [
            {"tenant": "TE", "name": "L3O_fw", "dn": "uni/tn-TE/out-L3O_fw", "vrf": "TE:VRF1",
             "nodes": [{"pod": "1", "node": "101", "static_routes": [
                 {"prefix": "10.95.0.0/16", "next_hops": ["10.255.0.1"], "pref": "1"},
                 {"prefix": "10.96.0.0/16", "descr": "Null-Route"}]}],
             "paths": [{"path": "topology/pod-1/paths-101/pathep-[eth1/1]", "nodes": ["101"],
                        "interface": "eth1/1", "kind": "ext-svi", "vlan": 3001,
                        "ips": [{"ip": "10.255.0.2/29"}]}],
             "peers": [], "ext_epgs": ["EN_any"]},
        ],
    },
}  # fmt: skip


def _seed(client) -> tuple[int, dict[str, int]]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sids = {}
    for name, platform, result in (("fw-01", "fortigate", FW1), ("fw-02", "fortigate", FW2),
                                   ("rt-01", "cisco_switch", ROUTER),
                                   ("apic-01", "cisco_aci", APIC)):  # fmt: skip
        if platform == "cisco_switch":  # Switches & Router: mit Adressen und Lesekonto
            body = {"tenant_id": tid, "name": name, "platform": platform,
                    "read_scope": "10.3.0.1", "username": "ro", "password": "x"}  # fmt: skip
            r = client.post("/systems", json=body, headers=ADMIN)
            assert r.status_code == 201, r.text
            sids[name] = r.json()["id"]
        else:
            sids[name] = tp._system(client, tid, name, platform)
        tp._deliver(client, tid, sids[name], platform, result)
    return tid, sids


def _list(client, tid, **params) -> dict:
    r = client.get("/static-routes", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _by_seq(rows) -> dict[str, dict]:
    return {r["seq"]: r for r in rows if r["label"] == "fw-01" and r["seq"]}


def _findings(client, tid) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in client.get("/findings", params={"tenant_id": tid, "limit": 5000}).json():
        if f["type"].startswith("static_"):
            out.setdefault(f["type"], []).append(f)
    return out


# -- Zustand je Route ----------------------------------------------------------------------


def test_zustand_je_route(client):
    tid, _ = _seed(client)
    out = _list(client, tid)
    rows = _by_seq(out["rows"])
    state = {seq: r["state"] for seq, r in rows.items()}
    assert state == {
        "1": "active", "2": "backup", "3": "shadowed", "4": "active", "5": "active",
        "6": "active", "7": "shadowed", "8": "active", "9": "active", "10": "active",
        "11": "not_installed", "12": "backup", "13": "disabled", "14": "active", "15": "active",
        "16": "active", "17": "backup", "18": "not_installed", "19": "backup", "20": "shadowed",
        "21": "not_installed",
    }  # fmt: skip
    # Backup: Distanz bewusst ueber dem Standard, BGP gewinnt
    assert rows["2"]["winner"] == {"prefix": "10.50.0.0/16", "protocol": "bgp", "distance": 20,
                                   "via": "10.1.0.254 (port1)"}  # fmt: skip
    assert "bewusst über dem Standard 10" in rows["2"]["reason"]
    # gleicher Next-Hop wie die BGP-Route: Backup (die Sitzung kann ausfallen)
    assert rows["19"]["state"] == "backup" and "static_shadowed" not in rows["19"]["issues"]
    # ueberdeckt: Standard-Distanz, die angeschlossene Route gewinnt
    assert "Distanz 10 ist der Standard" in rows["3"]["reason"]
    assert rows["3"]["winner"]["protocol"] == "connected"
    # ueberdeckt: spezifischere Routen decken das ganze Netz ab
    assert rows["7"]["covered_by"] == ["10.80.0.0/25", "10.80.0.128/25"]
    assert "nie genutzt" in rows["7"]["reason"]
    # die Blackhole-Route gewinnt immer
    assert "Blackhole-Route 10.99.0.0/16" in rows["20"]["reason"]
    # FortiGate-Prioritaet: gleiche Distanz, die kleinere Prioritaet geht vor
    assert "Priorität 5" in rows["17"]["reason"] and rows["16"]["state"] == "active"
    # Adressobjekt: die Netze in der Routing-Tabelle, Praefix sonst unbekannt
    assert rows["14"]["target"] == {"kind": "object", "name": "N_partner"}
    assert rows["14"]["resolved"] == ["10.85.0.0/16"]
    assert [u["reason"] for u in rows["14"]["unchecked"]] == ["object"]
    # nur Routing-Tabelle (IPsec add-route) und Blackhole
    rib = [r for r in out["rows"] if r["label"] == "fw-01" and r["origin"] == "rib"]
    assert [(r["prefix"], r["interface"], r["state"]) for r in rib] == [
        ("10.89.0.0/16", "vpn-kunde", "active")]  # fmt: skip
    assert rows["10"]["blackhole"] and rows["10"]["issues"] == ["static_blackhole"]
    # Next-Hop: aufgeloest, angeschlossen, nur ueber die Default-Route, anderes Interface
    # aktiv: aufgeloest; nicht aktiv: nur bis ins angeschlossene Netz geprueft (`remote`)
    assert rows["1"]["nexthop"]["state"] == "ok" and "remote" not in rows["1"]["nexthop"]
    assert rows["2"]["nexthop"]["state"] == "ok" and rows["2"]["nexthop"]["remote"]
    assert rows["2"]["nexthop"]["text"] == "angeschlossen (10.2.0.0/24 an port3)"
    assert rows["12"]["nexthop"]["state"] == "unreachable"
    assert "nur über die Default-Route" in rows["12"]["nexthop"]["text"]
    assert rows["18"]["nexthop"]["text"] == (
        "Next-Hop liegt im Netz 10.1.0.0/24 an port1, nicht an port3"
    )
    assert rows["21"]["nexthop"]["state"] == "ok"  # nicht installiert, Next-Hop erreichbar
    assert rows["13"]["nexthop"] is None and rows["13"]["issues"] == []
    assert rows["1"]["zone"] == "wan"

    # fw-02: Routing-Tabelle nicht lesbar - unbekannt, gezaehlt
    (fw2,) = (r for r in out["rows"] if r["label"] == "fw-02")
    assert fw2["state"] == "unknown" and [u["reason"] for u in fw2["unchecked"]] == ["rib"]
    # rt-01: nur die Routing-Tabelle - ausgewiesen; Null0 = Blackhole
    rt = {r["prefix"]: r for r in out["rows"] if r["label"] == "rt-01"}
    assert rt["10.30.0.0/16"]["state"] == "active" and rt["10.30.0.0/16"]["origin"] == "rib"
    assert rt["192.0.2.128/25"]["blackhole"]
    assert [x["label"] for x in out["summary"]["rib_only"]] == ["rt-01"]
    # ACI: statische Routen des L3Outs, nicht das Bridge-Domain-Subnetz
    aci = {r["prefix"]: r for r in out["rows"] if r["display"] == "vrf"}
    assert set(aci) == {"10.95.0.0/16", "10.96.0.0/16"}
    assert aci["10.95.0.0/16"]["state"] == "active" and aci["10.95.0.0/16"]["l3out"] == "L3O_fw"
    assert aci["10.95.0.0/16"]["label"] == "leaf-101 · VRF TE:VRF1"
    assert aci["10.96.0.0/16"]["blackhole"] and aci["10.96.0.0/16"]["comment"] == "Null-Route"

    s = out["summary"]
    assert s["total"] == len(out["rows"]) == out["total"]
    assert {u["reason"]: u["count"] for u in s["unchecked"]} == {"object": 1, "rib": 1}
    assert s["states"]["backup"] == 4 and s["states"]["shadowed"] == 3
    assert s["remote"] == 7  # Seq 2, 3, 7, 17, 19, 20, 21: Gegenstelle nicht gelesen


def test_filter_und_seiten(client):
    tid, sids = _seed(client)
    assert {r["seq"] for r in _list(client, tid, state="backup")["rows"]} == {"2", "12", "17", "19"}
    assert [r["seq"] for r in _list(client, tid, q="n_partner")["rows"]] == ["14"]
    # enthaelt IP: laengstes Praefix zuerst; das Adressobjekt ueber seine Netze
    found = _list(client, tid, ip="10.80.0.5", context=f"{sids['fw-01']}|root")["rows"]
    assert [r["prefix"] for r in found] == ["10.80.0.0/25", "10.80.0.0/24", "0.0.0.0/0"]
    assert [r["seq"] for r in _list(client, tid, ip="10.85.1.1")["rows"]] == ["14", "1"]
    issues = _list(client, tid, issues="true")
    assert all(r["issues"] for r in issues["rows"])
    assert issues["total"] < _list(client, tid)["total"]
    page = _list(client, tid, limit=5, offset=5)
    assert len(page["rows"]) == 5 and page["total"] > 10
    assert {c["label"] for c in page["contexts"]} >= {"fw-01", "fw-02", "rt-01",
                                                      "apic-01 · VRF TE:VRF1"}  # fmt: skip
    r = client.get("/static-routes", params={"tenant_id": tid, "ip": "kein"})
    assert r.status_code == 422
    assert client.get("/static-routes").status_code == 422  # ohne Mandant
    # Englisch: Zustand und Begruendung in der Sprache der Anfrage
    en = client.get("/static-routes", params={"tenant_id": tid, "state": "backup"},
                    headers={"Accept-Language": "en"}).json()  # fmt: skip
    two = next(r for r in en["rows"] if r["seq"] == "2")
    assert two["state_text"] == "backup" and "deliberately above the default 10" in two["reason"]


def test_modul_analyse_aus(client):
    tid, _ = _seed(client)
    r = client.put("/modules/analyse", params={"tenant_id": tid}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200
    r = client.get("/static-routes", params={"tenant_id": tid})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "module_disabled"


# -- Befunde (je Befund positiv und negativ) -----------------------------------------------


def test_befunde(client):
    tid, sids = _seed(client)
    found = _findings(client, tid)
    assert set(found) == {"static_nexthop_unreachable", "static_shadowed", "static_duplicate",
                          "static_blackhole", "static_unchecked"}  # fmt: skip

    # Next-Hop nicht erreichbar: nur ueber die Default-Route (auch eine Backup-Route) bzw. das
    # Netz liegt an einem anderen Interface; nicht: aktive Routen, Backup mit angeschlossenem
    # Next-Hop, nicht installiert mit erreichbarem Next-Hop
    unreach = {f["subject_id"]: f for f in found["static_nexthop_unreachable"]}
    assert set(unreach) == {
        "fw-01: 172.16.0.0/16 → 10.9.9.9 (port1)",
        "fw-01: 10.50.0.0/16 → 10.7.7.7 (port3)",
        "fw-01: 10.66.0.0/24 → 10.1.0.77 (port3)",
    }
    backup = unreach["fw-01: 10.50.0.0/16 → 10.7.7.7 (port3)"]
    assert backup["severity"] == "warning" and backup["source_system_id"] == sids["fw-01"]
    assert "im Ernstfall greift sie nicht" in backup["message"]
    assert backup["detail"]["seq"] == "12" and backup["detail"]["state"] == "backup"

    # ueberdeckt: Standard-Distanz geschlagen, spezifischere Routen, Blackhole gewinnt; nicht:
    # bewusste Backup-Routen (Distanz erhoeht, Prioritaet) und doppelte (eigener Befund)
    shadowed = {f["subject_id"]: f for f in found["static_shadowed"]}
    assert set(shadowed) == {
        "fw-01: 10.1.0.0/24 → 10.2.0.250 (port3)",
        "fw-01: 10.80.0.0/24 → 10.1.0.254 (port1)",
        "fw-01: 10.99.0.0/16 → 10.2.0.254 (port3)",
    }
    cov = shadowed["fw-01: 10.80.0.0/24 → 10.1.0.254 (port1)"]
    assert cov["detail"]["covered_by"] == ["10.80.0.0/25", "10.80.0.128/25"]
    assert "wird nie aktiv" in cov["message"] and "Backup" in cov["hint"]

    # doppelt: dieselbe Route zweimal; nicht: gleiches Praefix ueber andere Next-Hops
    (dup,) = found["static_duplicate"]
    assert dup["subject_id"] == "fw-01: 10.70.0.0/24 → 10.1.0.253 (port1)"
    assert [e["seq"] for e in dup["detail"]["entries"]] == ["5", "6"]
    assert "2-mal" in dup["message"] and "(5, 6)" in dup["message"]

    # Blackhole: nur Hinweis - FortiGate, Null0 des Routers, Null-Route des L3Outs
    holes = {f["subject_id"]: f for f in found["static_blackhole"]}
    assert set(holes) == {"fw-01: 10.99.0.0/16 → Blackhole", "rt-01: 192.0.2.128/25 → Blackhole",
                          "leaf-101 · VRF TE:VRF1: 10.96.0.0/16 → Blackhole"}  # fmt: skip
    assert all(f["severity"] == "info" for f in holes.values())
    assert "verworfen" in holes["fw-01: 10.99.0.0/16 → Blackhole"]["message"]

    # nicht geprueft: je Geraet gezaehlt (Adressobjekt, Routing-Tabelle nicht lesbar); nicht
    # bei rt-01 und dem APIC (alles geprueft)
    unchecked = {f["subject_id"]: f for f in found["static_unchecked"]}
    assert set(unchecked) == {"fw-01", "fw-02"}
    assert unchecked["fw-02"]["detail"]["counts"] == {"rib": 1}
    assert unchecked["fw-01"]["detail"]["counts"] == {"object": 1}
    assert unchecked["fw-02"]["message"] == (
        "1 statische Routen auf fw-02 nicht (ganz) geprüft: Routing-Tabelle nicht (ganz) lesbar "
        "(1)."
    )
    assert all(f["severity"] == "info" for f in unchecked.values())


def test_befunde_entfallen_mit_den_daten(client):
    """Korrigiert am Geraet: der naechste Lauf ohne die doppelte und die ueberdeckte Route -
    die Befunde fallen weg."""
    tid, sids = _seed(client)
    fixed = copy.deepcopy(FW1)
    fixed["routing"]["routes"] = [
        r for r in fixed["routing"]["routes"]
        if (r.get("attrs") or {}).get("seq-num") not in (3, 6, 11, 12, 18, 20)
    ]  # fmt: skip
    tp._deliver(client, tid, sids["fw-01"], "fortigate", fixed)
    found = _findings(client, tid)
    assert "static_duplicate" not in found and "static_nexthop_unreachable" not in found
    assert {f["subject_id"] for f in found["static_shadowed"]} == {
        "fw-01: 10.80.0.0/24 → 10.1.0.254 (port1)"}  # fmt: skip


def test_pruefung_abschaltbar(client):
    tid, _ = _seed(client)
    r = client.put("/checks/statisch_blackhole", params={"tenant_id": tid},
                   json={"enabled": False}, headers=ADMIN)  # fmt: skip
    assert r.status_code == 200, r.text
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    found = _findings(client, tid)
    assert "static_blackhole" not in found and "static_shadowed" in found


def test_texte_englisch():
    d = {"label": "fw-01", "prefix": "10.50.0.0/16", "via": "10.7.7.7 (port3)",
         "state": "backup", "nexthop": "x"}  # fmt: skip
    with use("en"):
        msg, hint = explain("static_nexthop_unreachable", "s", d)
        assert msg == ("The static route 10.50.0.0/16 via 10.7.7.7 (port3) on fw-01 does not "
                       "reach its next-hop: x. It is a backup route – it will not take over when "
                       "needed.")  # fmt: skip
        assert hint.startswith("Check the next-hop")
        msg, _ = explain("static_unchecked", "fw-02", {"label": "fw-02", "counts": {"rib": 2}})
        assert msg == ("2 static routes on fw-02 not (fully) checked: routing table not (fully) "
                       "readable (2).")  # fmt: skip
        msg, _ = explain(
            "static_duplicate", "s", {**d, "count": 2, "entries": [{"seq": "5"}, {"seq": "6"}]}
        )
        assert msg.endswith("is enabled 2 times in the configuration of fw-01 (5, 6).")
    # VRF im VDOM steht am Ort
    msg, _ = explain("static_blackhole", "s", {"label": "fw-01", "prefix": "10.9.0.0/16",
                                               "vrf": 2, "distance": 254})  # fmt: skip
    assert msg == ("Blackhole-Route 10.9.0.0/16 auf fw-01 · VRF 2 (Distanz 254): Verkehr in dieses "
                   "Netz wird verworfen.")  # fmt: skip


# -- Doku der Applikation: statische Routen auf den Wegen ----------------------------------


def _app_fw1(comment: str = "LAN hinter fw-02") -> dict:
    """fw-01 aus test_app_paths: die Route 10.60.0.0/24 zu fw-02 ist eine statische Route der
    Konfiguration (Seq 7)."""
    fw1 = copy.deepcopy(APP_FW1)
    fw1["routing"] = copy.deepcopy(fw1["routing"])
    fw1["routing"]["routes"].append(
        tp._r("fw-01", "root", "10.60.0.0/24", [{"ip": "10.70.0.2", "interface": "to-fw02"}],
              table="static", distance=10, attrs={"seq-num": 7, "comment": comment}))  # fmt: skip
    return fw1


def _fw1_id(client, tid) -> int:
    return next(x["id"] for x in client.get(f"/systems?tenant_id={tid}", headers=ADMIN).json()
                if x["name"] == "fw-01")  # fmt: skip


def test_doku_statische_routen_der_wege(client):
    tid = _seed_app(client)
    tp._deliver(client, tid, _fw1_id(client, tid), "fortigate", _app_fw1())
    out = _connections(client, tid)
    (st,) = out["statics"]
    assert (st["label"], st["prefix"], st["next_hop"], st["interface"]) == (
        "fw-01 · VDOM root", "10.60.0.0/24", "10.70.0.2", "to-fw02")  # fmt: skip
    assert (st["origin"], st["seq"], st["comment"], st["distance"], st["use"]) == (
        "config", "7", "LAN hinter fw-02", 10, "active")  # fmt: skip
    # beide Verbindungen (tcp/443 und tcp/8080) gehen ueber diese Route
    assert st["count"] == 2 and len(st["connections"]) == 2
    assert all(c["statics"] == [st["key"]] for c in out["connections"])
    # die BGP-Route im ACI und das angeschlossene Ziel an fw-02 sind keine statischen Routen
    assert not any(s["label"].startswith(("apic", "fw-02")) for s in out["statics"])

    # Export (Markdown): Abschnitt in den Verbindungen
    app_id = client.get("/applications", params={"tenant_id": tid}).json()[0]["id"]
    md = client.get(
        f"/applications/{app_id}/export", params={"format": "md", "sections": "connections"}
    )
    assert md.status_code == 200, md.text
    assert "Statische Routen auf den Wegen" in md.text
    assert "10.60.0.0/24" in md.text and "Seq 7" in md.text and "LAN hinter fw-02" in md.text


def test_doku_ohne_konfiguration_nur_routing_tabelle(client):
    """Ohne Eintrag in der Konfiguration: die statische Route der Routing-Tabelle (z. B. IPsec
    add-route, Router ohne gelesene Konfiguration)."""
    tid = _seed_app(client)
    out = _connections(client, tid)
    (st,) = out["statics"]
    assert (st["prefix"], st["origin"], st.get("seq")) == ("10.60.0.0/24", "rib", None)


def test_spur_der_weg_suche_backup():
    """`on_paths`: die statische Backup-Route an der Abweichung eines Backup-Wegs; eine
    BGP-Abweichung nutzt die (statische) aktive Route dort nicht; Policy-Route und Ziel im Fabric
    ebenso nicht."""
    hop = {"key": "1|root", "source_system_id": 1, "context": "root", "kind": "firewall",
           "label": "fw-01"}  # fmt: skip
    route = {"prefix": "10.60.0.0/24", "protocol": "static", "distance": 10,
             "next_hops": [{"ip": "10.70.0.2", "interface": "a"}]}  # fmt: skip
    active = {"kind": "active", "end": {"kind": "exit"},
              "hops": [{**hop, "route": route}]}  # fmt: skip
    backup = {"kind": "backup", "end": {"kind": "exit"}, "hops": [
        {**hop, "route": route, "egress": {"next_hop": "10.70.0.6", "interface": "b"},
         "backup": {"prefix": "10.60.0.0/24", "protocol": "static", "distance": 200}}]}  # fmt: skip
    bgp = {"kind": "backup", "end": {"kind": "exit"}, "hops": [
        {**hop, "key": "2|root", "route": route, "egress": {"next_hop": "10.9.0.1"},
         "backup": {"prefix": "10.60.0.0/24", "protocol": "bgp"}}]}  # fmt: skip
    policy = {
        "kind": "active",
        "end": {"kind": "exit"},
        "hops": [{**hop, "key": "3|root", "route": route, "policy": {"seq": 1}}],
    }
    fabric = {"kind": "active", "end": {"kind": "fabric"},
              "hops": [{**hop, "key": "4|x", "route": route}]}  # fmt: skip
    refs = static_routes.on_paths([active, backup, bgp, policy, fabric])
    assert [(r["key"], r["use"], r["next_hops"]) for r in refs] == [
        ("1|root", "active", [{"ip": "10.70.0.2", "interface": "a"},
                              {"ip": "10.70.0.6", "interface": "b"}]),
    ]  # fmt: skip
    only_backup = static_routes.on_paths([backup])
    assert [(r["use"], r["distance"]) for r in only_backup] == [("backup", 200)]


@pytest.fixture
def snapshots(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "doc_snapshots", True)
    monkeypatch.setattr(settings, "doc_stale_seconds", 1000.0)  # Tests: immer schnell genug
    calls: list[str] = []
    compute = doc_snapshots.compute

    def counted(session, app, kind, params):
        calls.append(kind)
        return compute(session, app, kind, params)

    monkeypatch.setattr(doc_snapshots, "compute", counted)
    return calls


def test_fingerabdruck_statische_route_geaendert(client, snapshots):
    """Stand der Doku (ADR 0037): die statischen Routen der Wege kommen aus den Verbindungen -
    aendert sich eine genutzte statische Route am Geraet (hier ihr Kommentar), rechnet der Stand
    neu; ein Lauf mit gleichen Daten nicht."""
    tid = _seed_app(client)
    sid = _fw1_id(client, tid)
    tp._deliver(client, tid, sid, "fortigate", _app_fw1())
    first = _connections(client, tid)
    app_id = client.get("/applications", params={"tenant_id": tid}).json()[0]["id"]
    assert first["snapshot"]["fresh"] and first["statics"][0]["comment"] == "LAN hinter fw-02"

    snapshots.clear()
    tp._deliver(client, tid, sid, "fortigate", _app_fw1())  # gleiche Daten
    assert snapshots == []
    same = client.get(f"/applications/{app_id}/connections").json()
    assert same["snapshot"]["built_at"] == first["snapshot"]["built_at"] and snapshots == []

    tp._deliver(client, tid, sid, "fortigate", _app_fw1("LAN neu"))  # Kommentar geaendert
    after = client.get(f"/applications/{app_id}/connections").json()
    assert "connections" in snapshots
    assert after["snapshot"]["fresh"] and after["statics"][0]["comment"] == "LAN neu"
    assert after["snapshot"]["built_at"] != first["snapshot"]["built_at"]
