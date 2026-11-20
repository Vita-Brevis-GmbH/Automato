# ruff: noqa: S104 - 0.0.0.0/0 sind Selektoren, keine Bind-Adressen
"""VPN-Tunnel zweier gelesener FortiGates koppeln (1.23.0, ADR 0063 Nachtrag 1.23.0).

Anwender 2026-11-20: „Die VPN Verbindungen werden herausgelesen aber nicht verknüpft, also bleiben
die verbundenen Standorte weiterhin getrennt“ – „Beide eingelesen. Einseitig ist es dynamisch, kann
man dies nicht koppeln? Manuell, wenn es dynamisch nicht geht?“

Nachgestellt (anonymisiert, Dokumentationsbereiche):
- fw-spoke (Standort): LAN 10.10.0.0/24, wan1 203.0.113.1/30; statischer Tunnel `to-hub` zur
  Gegenstelle des Hubs, Phase 2 10.10.0.0/24 <-> 10.20.0.0/24, Route 10.20.0.0/24 ueber den Tunnel.
- fw-hub (Zentrale): LAN 10.20.0.0/24, wan1 198.51.100.2/30; Dial-up `dial-in` auf wan1, Phase 2
  gespiegelt 10.20.0.0/24 <-> 10.10.0.0/24, Route zum Standort ueber die verbundene Instanz.
- fw-edge (vor dem Hub, nur fuer die VIP): VIP 198.51.100.200 -> 192.168.50.2 (wan1 des Hubs).
"""

import copy

import test_paths as tp
from app.audit import decode_detail
from app.models import AuditLog, VpnPair
from sqlalchemy import func, select

OP = {"X-Dev-Roles": "operator"}
VIEWER = {"X-Dev-Roles": "viewer"}
SPOKE, HUB, EDGE = "fw-spoke", "fw-hub", "fw-edge"
LAN_S, LAN_H = "10.10.0.5", "10.20.0.5"


def _p2(name, src, dst):
    return {"name": name, "src": src, "dst": dst, "status": "up"}


def _policy(pid, src_if, dst_if, src, dst):
    return {"source_platform": "fortigate", "external_id": str(pid), "name": f"p{pid}",
            "action": "allow", "sources": [src], "destinations": [dst],
            "services": ["tcp/443"], "src_zone": src_if, "dst_zone": dst_if, "vdom": "root",
            "family": "v4",
            "raw": {"policyid": pid, "srcintf": [{"name": src_if}], "dstintf": [{"name": dst_if}],
                    "status": "enable", "schedule": "always"}}  # fmt: skip


def _spoke(gw: str = "198.51.100.2") -> dict:
    return {
        "status": "completed",
        "rules": [_policy(1, "lan", "to-hub", LAN_S, "10.20.0.0/24"),
                  _policy(2, "to-hub", "lan", "10.20.0.0/24", "10.10.0.0/24")],
        "inventory": {
            "devices": [{"name": SPOKE, "role": "firewall"}],
            "interfaces": [
                tp._if(SPOKE, "lan", "10.10.0.1/24", "lan"),
                tp._if(SPOKE, "wan1", "203.0.113.1/30", "internet"),
                {"device": SPOKE, "name": "to-hub", "kind": "tunnel", "vdom": "root"},
            ],
        },
        "routing": {
            "contexts": [{"node": SPOKE, "context": "root"}],
            "routes": [
                tp._r(SPOKE, "root", "10.10.0.0/24", [{"interface": "lan"}], "connected"),
                tp._r(SPOKE, "root", "203.0.113.0/30", [{"interface": "wan1"}], "connected"),
                tp._r(SPOKE, "root", "10.20.0.0/24", [{"interface": "to-hub"}]),
            ],
            "vpn": [{"node": SPOKE, "context": "root", "name": "to-hub", "kind": "static",
                     "interface": "wan1", "remote_gw": gw, "ike_version": 2, "status": "up",
                     "phase2": [_p2("to-hub-p2", ["10.10.0.0/24"], ["10.20.0.0/24"])]}],
        },
    }  # fmt: skip


def _hub(wan: str = "198.51.100.2/30", instances: list | None = None, dialups: int = 1) -> dict:
    inst = instances if instances is not None else [
        {"name": "dial-in_0", "remote_gw": "203.0.113.1", "status": "up",
         "phase2": {"dial-in-p2": "up"}, "nets": ["10.10.0.0/24"]}]  # fmt: skip
    tunnels = [{"node": HUB, "context": "root", "name": "dial-in" if i == 0 else f"dial-in{i}",
                "kind": "dialup", "interface": "wan1", "remote_gw": None, "ike_version": 2,
                "status": "up" if inst else "idle",
                "phase2": [_p2("dial-in-p2", ["10.20.0.0/24"], ["10.10.0.0/24"])],
                "instances": inst if i == 0 else []} for i in range(dialups)]  # fmt: skip
    routes = [
        tp._r(HUB, "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
        tp._r(HUB, "root", wan.replace(".2/30", ".0/30").replace(".2/24", ".0/24"),
              [{"interface": "wan1"}], "connected"),
    ]  # fmt: skip
    if inst:
        routes.append(tp._r(HUB, "root", "10.10.0.0/24", [{"interface": "dial-in_0"}],
                            attrs={"is_tunnel_route": True,
                                   "tunnel_parent": "dial-in"}))  # fmt: skip
    return {
        "status": "completed",
        "rules": [_policy(5, "dial-in", "lan", "10.10.0.0/24", "10.20.0.0/24"),
                  _policy(6, "lan", "dial-in", LAN_H, "10.10.0.0/24")],
        "inventory": {
            "devices": [{"name": HUB, "role": "firewall"}],
            "interfaces": [
                tp._if(HUB, "lan", "10.20.0.1/24", "lan"),
                tp._if(HUB, "wan1", wan, "internet"),
                *({"device": HUB, "name": t["name"], "kind": "tunnel", "vdom": "root"}
                  for t in tunnels),
            ],
        },
        "routing": {"contexts": [{"node": HUB, "context": "root"}], "routes": routes,
                    "vpn": tunnels},
    }  # fmt: skip


EDGE_FW = {
    "status": "completed",
    "rules": [],
    "nat_rules": [{"source_platform": "fortigate", "external_id": "vip-hub", "name": "vip-hub",
                   "nat_type": "dnat_vip", "ext_ip": "198.51.100.200",
                   "mapped_ip": "192.168.50.2", "vdom": "root"}],
    "inventory": {"devices": [{"name": EDGE, "role": "firewall"}],
                  "interfaces": [tp._if(EDGE, "wan1", "198.51.100.201/24", "internet"),
                                 tp._if(EDGE, "dmz", "192.168.50.1/24", "dmz")]},
    "routing": {"contexts": [{"node": EDGE, "context": "root"}], "routes": [
        tp._r(EDGE, "root", "192.168.50.0/24", [{"interface": "dmz"}], "connected")]},
}  # fmt: skip


def _seed(client, spoke: dict, hub: dict, edge: dict | None = None,
          tenant: str = "Kunde A") -> tuple[int, dict[str, int]]:  # fmt: skip
    tid = client.post("/tenants", json={"name": tenant}, headers=tp.ADMIN).json()["id"]
    sids = {}
    for name, result in ((SPOKE, spoke), (HUB, hub), (EDGE, edge)):
        if result is None:
            continue
        sids[name] = tp._system(client, tid, name, "fortigate")
        tp._deliver(client, tid, sids[name], "fortigate", result)
    return tid, sids


def _vpn(hop: dict) -> dict:
    return (hop.get("egress") or {}).get("vpn") or {}


def _tunnels(client, tid) -> dict[str, dict]:
    r = client.get("/vpn/tunnels", params={"tenant_id": tid})
    assert r.status_code == 200, r.text
    return {t["name"]: t for t in r.json()["tunnels"]}


def test_statisch_zu_dialup_automatisch_gekoppelt(client):
    """Die statische Seite zeigt auf den Hub, dort ist ein Dial-up auf diesem Interface mit
    gespiegelten Selektoren: ein Uebergang - der Weg geht in beide Richtungen weiter."""
    tid, sids = _seed(client, _spoke(), _hub())
    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H, service="tcp/443")["paths"]
    assert tp._labels(path) == [SPOKE, HUB]
    v = _vpn(path["hops"][0])
    assert (v["peer"]["tunnel"], v["peer"]["how"], v["peer"]["pair"]) == ("dial-in", "gw", "auto")
    assert path["hops"][1]["ingress"]["interface"] == "dial-in"  # Policies des Hubs pruefen ihn
    assert path["end"]["kind"] == "connected"
    assert [h["rules"]["verdict"] for h in path["hops"]] == ["allow", "allow"]
    # zurueck ueber die verbundene Instanz: das Gegenstueck ist der statische Tunnel des Standorts
    (back,) = tp._trace(client, tid, src=LAN_H, dst=LAN_S, service="tcp/443")["paths"]
    assert tp._labels(back) == [HUB, SPOKE]
    b = _vpn(back["hops"][0])
    assert (b["instance"], b["peer"]["tunnel"], b["peer"]["how"]) == ("dial-in_0", "to-hub",
                                                                      "spoke")  # fmt: skip
    assert "erkannt am statischen Tunnel der Gegenstelle" in back["hops"][1]["via"]
    assert back["hops"][1]["ingress"]["interface"] == "to-hub"
    assert b["id"] == v["id"]  # Netzwerkplan: ein Tunnel, gleich welche Richtung
    # Ansicht: Gegenstelle mit Herkunft und Kennung des anderen Tunnels (Link)
    rows = _tunnels(client, tid)
    (peer,) = rows["to-hub"]["peers"]
    assert (peer["label"], peer["tunnel"], peer["pair"], peer["tunnel_id"]) == (
        HUB, "dial-in", "auto", rows["dial-in"]["id"])  # fmt: skip
    assert [(p["tunnel"], p["pair"]) for p in rows["dial-in"]["peers"]] == [("to-hub", "auto")]
    # Topologie: die Standorte haengen ueber den Tunnel zusammen
    topo = client.get("/topology", params={"tenant_id": tid}).json()
    (edge,) = (e for e in topo["edges"] if e["kind"] == "vpn")
    assert {edge["a"], edge["b"]} == {f"{sids[SPOKE]}|root", f"{sids[HUB]}|root"}
    assert edge["origin"] == "derived" and edge["tunnels"][0]["pair"] == "auto"


def test_vip_vor_der_gegenstelle(client):
    """Die statische Seite zeigt auf eine oeffentliche Adresse, die eine gelesene FortiGate per
    VIP auf das Interface des Hubs weiterleitet: automatisch gekoppelt (`vip`)."""
    tid, _sids = _seed(client, _spoke("198.51.100.200"), _hub("192.168.50.2/24"), EDGE_FW)
    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H)["paths"]
    assert tp._labels(path) == [SPOKE, HUB]
    peer = _vpn(path["hops"][0])["peer"]
    assert (peer["tunnel"], peer["how"], peer["pair"]) == ("dial-in", "vip", "auto")
    assert "erkannt an der VIP vor der Gegenstelle" in path["hops"][1]["via"]
    # ohne die VIP (Port-Weiterleitung ohne IKE): nicht belegt - der Weg endet sichtbar
    edge = copy.deepcopy(EDGE_FW)
    edge["nat_rules"][0] |= {"portforward": True, "ext_port": "443", "mapped_port": "443"}
    tid2, _ = _seed(client, _spoke("198.51.100.200"), _hub("192.168.50.2/24", instances=[]),
                    edge, tenant="Kunde B")  # fmt: skip
    (path,) = tp._trace(client, tid2, src=LAN_S, dst=LAN_H)["paths"]
    assert path["end"]["kind"] == "vpn" and tp._labels(path) == [SPOKE]


# verbundene Gegenstelle hinter NAT: ihre Adresse gehoert keinem gelesenen Geraet
NATTED = [{"name": "dial-in_0", "remote_gw": "192.0.2.99", "status": "up",
           "phase2": {"dial-in-p2": "up"}, "nets": ["10.10.0.0/24"]}]  # fmt: skip


def test_von_hand_koppeln_und_loesen(client, session_factory):
    """Dynamisch nicht belegbar (NAT vor beiden Seiten): Vorschlag aus den gespiegelten
    Selektoren, von Hand koppeln - der Weg geht dann in beide Richtungen weiter; loesen - er
    endet wieder sichtbar an der Gegenstelle."""
    tid, sids = _seed(client, _spoke("198.51.100.200"), _hub(instances=NATTED))
    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H)["paths"]
    assert path["end"]["kind"] == "vpn" and tp._labels(path) == [SPOKE]
    rows = _tunnels(client, tid)
    spoke, hub = rows["to-hub"], rows["dial-in"]
    assert spoke["peers"] == [] and spoke["suggestions"] == 1
    detail = client.get(f"/vpn/tunnels/{spoke['id']}", params={"tenant_id": tid}).json()
    (s,) = detail["suggestion_list"]
    assert (s["tunnel"], s["tunnel_id"], s["why"], s["exact"], s["label"]) == (
        "dial-in", hub["id"], "mirror", True, HUB)  # fmt: skip
    assert s["text"] == "Phase-2-Selektoren gespiegelt"
    assert [c["tunnel"] for c in detail["choices"]] == ["dial-in"]

    body = {"tenant_id": tid, "a": spoke["id"], "b": hub["id"]}
    assert client.put("/vpn/pairs", json=body, headers=VIEWER).status_code == 403
    s0 = session_factory()
    audits = s0.scalar(select(func.count()).select_from(AuditLog))
    s0.close()
    r = client.put("/vpn/pairs", json=body, headers=OP)
    assert r.status_code == 200, r.text
    pair_id = r.json()["pair_id"]
    assert r.json()["previous"] is None and r.json()["mode"] == "coupled"
    s1 = session_factory()
    (log,) = s1.scalars(select(AuditLog).where(AuditLog.action == "vpn_pair.set")).all()
    detail = decode_detail(log)
    assert detail["a"] == f"{sids[SPOKE]}|root|to-hub" and detail["mode"] == "coupled"
    assert s1.scalar(select(func.count()).select_from(AuditLog)) == audits + 1
    s1.close()

    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H)["paths"]
    assert tp._labels(path) == [SPOKE, HUB] and path["end"]["kind"] == "connected"
    peer = _vpn(path["hops"][0])["peer"]
    assert (peer["tunnel"], peer["how"], peer["pair"]) == ("dial-in", "manual", "manual")
    want = "über VPN to-hub (Gegenstelle 198.51.100.200) – von Hand gekoppelt mit dial-in"
    assert path["hops"][1]["via"] == want
    en = client.get("/routing/trace", params={"tenant_id": tid, "src": LAN_S, "dst": LAN_H},
                    headers={"Accept-Language": "en"}).json()  # fmt: skip
    assert en["paths"][0]["hops"][1]["via"].endswith("– coupled manually with dial-in")
    # zurueck ueber die verbundene Instanz: ihre Netze passen zum gekoppelten Standort
    (back,) = tp._trace(client, tid, src=LAN_H, dst=LAN_S)["paths"]
    assert tp._labels(back) == [HUB, SPOKE] and back["end"]["kind"] == "connected"
    assert _vpn(back["hops"][0])["peer"]["tunnel"] == "to-hub"
    rows = _tunnels(client, tid)
    (peer,) = rows["to-hub"]["peers"]
    assert (peer["pair"], peer["pair_id"], peer["how"]) == ("manual", pair_id, "manual")
    assert rows["to-hub"]["suggestions"] == 0
    # Verbindungen der Applikation: am Hub erreicht, nicht mehr „unvollstaendig“ wegen VPN
    web = {"name": "Web", "members": [{"kind": "host", "ref": LAN_S}]}
    app = client.post(f"/applications?tenant_id={tid}", headers=OP, json=web)
    conns = client.get(f"/applications/{app.json()['id']}/connections").json()
    lan_h = next(c for c in conns["connections"] if c["dst"]["ip"] == "10.20.0.0/24")
    assert [h["label"] for h in lan_h["hops"]] == [SPOKE, HUB] and lan_h["reached"]
    assert "vpn_end" not in lan_h and "vpn" not in conns["coverage"].get("cuts", {})
    assert lan_h["hops"][0]["vpn"]["peer"]["pair"] == "manual"
    # Topologie: Kante von Hand
    topo = client.get("/topology", params={"tenant_id": tid}).json()
    (edge,) = (e for e in topo["edges"] if e["kind"] == "vpn")
    assert edge["origin"] == "manual"

    # ein statischer Tunnel hat hoechstens eine Kopplung von Hand; zwei Dial-up nie
    hub2 = _hub(instances=[], dialups=2)
    tp._deliver(client, tid, sids[HUB], "fortigate", hub2)
    rows = _tunnels(client, tid)
    r = client.put("/vpn/pairs", headers=OP, json={"tenant_id": tid, "a": rows["to-hub"]["id"],
                                                   "b": rows["dial-in1"]["id"]})  # fmt: skip
    assert r.status_code == 409 and "zuerst lösen" in r.json()["detail"]
    r = client.put("/vpn/pairs", headers=OP, json={"tenant_id": tid, "a": rows["dial-in"]["id"],
                                                   "b": rows["dial-in1"]["id"]})  # fmt: skip
    assert r.status_code == 409
    # die Kopplung gilt auch nach dem naechsten Lauf (neue Kennungen der Tunnel)
    (peer,) = rows["to-hub"]["peers"]
    assert (peer["tunnel"], peer["pair"]) == ("dial-in", "manual")

    # loesen: der Weg endet wieder sichtbar an der Gegenstelle
    assert client.delete(f"/vpn/pairs/{pair_id}", params={"tenant_id": tid},
                         headers=VIEWER).status_code == 403  # fmt: skip
    r = client.delete(f"/vpn/pairs/{pair_id}", params={"tenant_id": tid}, headers=OP)
    assert r.status_code == 200, r.text
    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H)["paths"]
    assert path["end"]["kind"] == "vpn"
    assert client.delete(f"/vpn/pairs/{pair_id}", params={"tenant_id": tid},
                         headers=OP).status_code == 404  # fmt: skip


def test_automatische_kopplung_loesen(client):
    """Eine automatische Kopplung von Hand loesen (`separated`): der Weg geht zur FortiGate der
    Adresse, der Tunnel dort gilt nicht mehr als Gegenstueck; Loesung entfernen = wieder auto."""
    tid, _sids = _seed(client, _spoke(), _hub(instances=[]))
    rows = _tunnels(client, tid)
    body = {"tenant_id": tid, "a": rows["dial-in"]["id"], "b": rows["to-hub"]["id"],
            "mode": "separated"}  # fmt: skip
    r = client.put("/vpn/pairs", json=body, headers=OP)
    assert r.status_code == 200, r.text
    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H)["paths"]
    assert tp._labels(path) == [SPOKE, HUB]
    assert _vpn(path["hops"][0])["peer"]["tunnel"] is None
    assert "dort kein passender Tunnel gelesen" in path["hops"][1]["via"]
    detail = client.get(f"/vpn/tunnels/{rows['to-hub']['id']}",
                        params={"tenant_id": tid}).json()  # fmt: skip
    assert [(x["tunnel"], x["pair_id"]) for x in detail["separated"]] == [
        ("dial-in", r.json()["pair_id"])]  # fmt: skip
    # nur eine automatische Kopplung laesst sich loesen
    again = client.put("/vpn/pairs", json=body, headers=OP)
    assert again.status_code == 409
    assert client.delete(f"/vpn/pairs/{r.json()['pair_id']}", params={"tenant_id": tid},
                         headers=OP).status_code == 200  # fmt: skip
    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H)["paths"]
    assert _vpn(path["hops"][0])["peer"]["pair"] == "auto"


def test_mehrdeutig_nur_vorschlag(client):
    """Zwei Dial-up-Tunnel mit gespiegelten Selektoren auf dem Interface: nicht automatisch -
    der Weg geht zur Firewall der Adresse, der Tunnel dort bleibt offen, beide sind Vorschlaege."""
    tid, _sids = _seed(client, _spoke(), _hub(instances=[], dialups=2))
    (path,) = tp._trace(client, tid, src=LAN_S, dst=LAN_H)["paths"]
    assert tp._labels(path) == [SPOKE, HUB]
    peer = _vpn(path["hops"][0])["peer"]
    assert peer["tunnel"] is None and peer["open"] == 2 and "pair" not in peer
    assert "Tunnel dort nicht eindeutig, in „VPN-Tunnel“ koppeln" in path["hops"][1]["via"]
    rows = _tunnels(client, tid)
    assert rows["to-hub"]["suggestions"] == 2
    detail = client.get(f"/vpn/tunnels/{rows['to-hub']['id']}", params={"tenant_id": tid}).json()
    assert {(s["tunnel"], s["why"]) for s in detail["suggestion_list"]} == {
        ("dial-in", "gw"), ("dial-in1", "gw")}  # fmt: skip


def test_fingerabdruck_und_loeschen(client, session_factory):
    """Eine Kopplung aendert den Stand der Doku (Audit, ADR 0037) - und faellt mit dem
    Quellsystem weg."""
    from app.models import Application
    from app.store import doc_snapshots

    tid, sids = _seed(client, _spoke("198.51.100.200"), _hub(instances=[]))
    web = {"name": "Web", "members": [{"kind": "host", "ref": LAN_S}]}
    app = client.post(f"/applications?tenant_id={tid}", headers=OP, json=web)
    s = session_factory()
    before = doc_snapshots.inputs(s, s.get(Application, app.json()["id"]), "doc").fp
    s.close()
    rows = _tunnels(client, tid)
    r = client.put("/vpn/pairs", headers=OP, json={"tenant_id": tid, "a": rows["to-hub"]["id"],
                                                   "b": rows["dial-in"]["id"]})  # fmt: skip
    assert r.status_code == 200, r.text
    s = session_factory()
    after = doc_snapshots.inputs(s, s.get(Application, app.json()["id"]), "doc").fp
    s.close()
    assert after != before
    # Quellsystem geloescht: die Kopplung faellt mit ihm weg (nicht verwaist stehen gelassen)
    assert client.delete(f"/systems/{sids[HUB]}", headers=tp.ADMIN).status_code in (200, 204)
    s = session_factory()
    assert s.scalar(select(func.count()).select_from(VpnPair)) == 0
    s.close()


def test_verwaiste_kopplung_sichtbar(client):
    """Liefert ein Lauf einen gekoppelten Tunnel nicht mehr, bleibt die Kopplung als verwaist
    sichtbar und loesbar - nie still geloescht."""
    tid, sids = _seed(client, _spoke("198.51.100.200"), _hub(instances=[]))
    rows = _tunnels(client, tid)
    r = client.put("/vpn/pairs", headers=OP, json={"tenant_id": tid, "a": rows["to-hub"]["id"],
                                                   "b": rows["dial-in"]["id"]})  # fmt: skip
    gone = _hub(instances=[])
    gone["routing"]["vpn"] = []
    tp._deliver(client, tid, sids[HUB], "fortigate", gone)
    out = client.get("/vpn/tunnels", params={"tenant_id": tid}).json()
    (orphan,) = out["orphans"]
    assert orphan["pair_id"] == r.json()["pair_id"]
    assert [e["tunnel"] for e in orphan["ends"]] == ["to-hub", "dial-in"] or [
        e["tunnel"] for e in orphan["ends"]] == ["dial-in", "to-hub"]  # fmt: skip
    assert client.delete(f"/vpn/pairs/{orphan['pair_id']}", params={"tenant_id": tid},
                         headers=OP).status_code == 200  # fmt: skip
