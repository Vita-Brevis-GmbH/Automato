"""Weg-Suche und Routing auf Englisch (0.87.0, ADR 0028 Schritt 2): Texte im Weg (Start, Ende,
Urteile, Gruende, Hinweise), Relevanz und Fehlermeldungen folgen `Accept-Language`; Werte und
Schluessel (`verdict`, `kind`, `src` = „direkt“) bleiben. Ohne Sprache bleibt alles deutsch."""

from types import SimpleNamespace

import test_aci_check as ac
import test_aci_shared as acs
import test_parallel as par
import test_paths as tp
import test_policy_check as pc
from app.i18n import use
from app.store import aci_direction, epg_locate, paths, policy_check, zone_path

EN = {"Accept-Language": "en"}


def _trace(client, tid, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params}, headers=EN)
    assert r.status_code == 200, r.text
    return r.json()


def test_weg_auf_englisch(client):
    tid = tp._seed(client)
    out = _trace(client, tid, src="10.20.0.5", dst="10.50.1.10")
    assert [(s["label"], s["via"]) for s in out["start"]] == [
        ("fw-01 · VDOM root", "Source connected")
    ]
    (path,) = out["paths"]
    assert path["hops"][1]["via"] == "Neighbor address"
    assert path["end"] == {
        "kind": "fabric",
        "text": "Destination in fabric: EPG TE|AP1|EPG_web (10.50.1.0/24)",
    }
    back = _trace(client, tid, src="10.50.1.10", dst="10.20.0.5")
    assert back["start"][0]["via"] == "Source in fabric: EPG TE|AP1|EPG_web (10.50.1.0/24)"

    # ohne Sprache: deutsch wie bisher
    de = tp._trace(client, tid, src="10.20.0.5", dst="10.50.1.10")
    assert de["start"][0]["via"] == "Quelle angeschlossen"
    assert de["paths"][0]["end"]["text"].startswith("Ziel im Fabric: ")

    # Fehlermeldungen
    r = client.get("/routing/trace", params={"tenant_id": tid, "dst": "x"}, headers=EN)
    assert r.status_code == 422 and r.json()["detail"] == "dst: not a valid IP or network"
    r = client.get("/routing/trace", params={"tenant_id": tid, "dst": "10.0.0.1"}, headers=EN)
    assert r.json()["detail"] == "specify src or start"
    r = client.get("/routing/trace", params={"tenant_id": tid, "dst": "10.0.0.1"})
    assert r.json()["detail"] == "src oder start angeben"


def test_contracts_auf_englisch(client):
    tid = ac._seed(client)
    web = {"src": "10.20.0.5", "dst": "10.50.1.10"}
    (path,) = _trace(client, tid, **web, service="tcp/443")["paths"]
    (hop,) = (h for h in path["hops"] if h["kind"] == "vrf")
    rules = hop["rules"]
    assert (rules["verdict"], rules["text"]) == ("allow", "allowed by Contract CT_srv")
    assert rules["src_epg"] == "external EPG L3O_fw|EN_srv (10.20.0.0/24)"
    (ct,) = rules["rules"]
    assert (ct["src"], ct["dst"]) == ("direkt", "direkt")  # Werte bleiben
    assert path["policy"] == {"verdict": "allow", "text": "allowed (1 firewall, 1 ACI VRF)"}

    (path,) = _trace(client, tid, **web)["paths"]
    (hop,) = (h for h in path["hops"] if h["kind"] == "vrf")
    assert hop["rules"]["verdict"] == "depends" and hop["rules"]["rules"][0]["unsure"] == [
        "Service"
    ]

    out = _trace(client, tid, **web, service="TE/FT_web")
    assert out["notes"][0] == "TE/FT_web: checked tcp/80 – more services: tcp/443"
    r = client.get("/routing/trace", params={"tenant_id": tid, **web, "service": "FT_nix"},
                   headers=EN)  # fmt: skip
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "Service: tcp/443, udp/53, tcp or icmp; no service object or ACI filter “FT_nix”"
    )


def test_leak_mit_art_statt_text(client):
    """Die Oberflaeche unterscheidet Shared Service und Route-Leaking ueber `leak.kind`."""
    tid = acs._seed(client, acs._apic(shared=True))
    (path,) = _trace(client, tid, src="10.50.1.10", dst="10.60.0.5", service="tcp/443")["paths"]
    (hop,) = (h for h in path["hops"] if h["kind"] == "vrf")
    assert hop["leak"]["kind"] == "shared"
    assert hop["leak"]["text"].startswith("Shared Service: 10.60.0.0/24 shared by ")
    assert path["end"]["text"].startswith("Destination in the fabric of VRF TE2:VRF2: ")
    de = tp._trace(client, tid, src="10.50.1.10", dst="10.60.0.5", service="tcp/443")
    leak = next(h for h in de["paths"][0]["hops"] if h["kind"] == "vrf")["leak"]
    assert leak["kind"] == "shared" and " geteilt, Contract mit " in leak["text"]


def test_route_leaking_mit_art(client):
    tid = acs._seed(client, acs._apic(shared=False, leak=True))
    (path,) = _trace(client, tid, src="10.50.1.10", dst="10.60.0.5", service="tcp/443")["paths"]
    (hop,) = (h for h in path["hops"] if h["kind"] == "vrf")
    assert hop["leak"]["kind"] == "leak"
    assert hop["leak"]["text"] == "Route-Leaking 10.60.0.0/24 from VRF TE2:VRF2 to TE:VRF1"


def test_relevanz_in_der_regel_liste(client):
    tid = pc._seed(client)
    params = {"tenant_id": tid, "q": "10.30.0.10"}
    rel = {
        x["name"]: x["relevance"] for x in client.get("/rules", params=params, headers=EN).json()
    }
    assert rel["lan-to-dmz-falsch"]["text"] == (
        "fw-r routes 10.30.0.10 via dmz (Zone dmz) – the rule only applies to the outgoing "
        "interface internet"
    )
    assert rel["lan-to-dmz-falsch"]["state"] == "impossible"


def test_parallele_firewalls_vorschlag(client):
    tid, _fwa, _fwb = par._seed(client)
    (s,) = client.get("/systems/parallel-suggestions", params={"tenant_id": tid},
                      headers=EN).json()  # fmt: skip
    assert s["reasons"] == ["Session sync (FGSP)", "3 of 4 policies identical"]


def test_reine_funktionen():
    with use("en"):
        assert aci_direction.text().startswith("ACI is stateless")
        assert aci_direction.replies().endswith("Consumer → Provider replies only")
        assert aci_direction.TEXT.startswith("ACI ist zustandslos")  # Konstante bleibt deutsch
        assert epg_locate.label(epg_locate.STATIC) == "static endpoint"
        assert epg_locate.LABEL[epg_locate.STATIC] == "statischer Endpunkt"
        assert paths.exit_kind("other") == "Exit"
        assert paths.exit_kind("mpls") == "MPLS"
        assert policy_check._overall([("fw", "deny", "firewall")]) == {
            "verdict": "deny",
            "text": "blocked at fw",
        }
        assert policy_check._overall([("fw", "unknown", "firewall")])["text"] == (
            "no policies read at fw"
        )
        verdict = policy_check._verdict(None, [])
        assert verdict["text"] == "no matching policy – implicitly denied"
        assert zone_path._none()["name"] == "no zone"
        deny = SimpleNamespace(intent="deny", services=[])
        assert zone_path._judge(None, 0, deny, True, False, ["allow"], None, None) == (
            "violation",
            "against the intent – deny, the path allows",
        )
    assert paths.exit_kind("other") == "Anbindung"
    assert zone_path._none()["name"] == "ohne Zone"
    assert policy_check._verdict(None, [])["text"] == "keine passende Policy – implizit gesperrt"
