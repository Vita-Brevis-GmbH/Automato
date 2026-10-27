# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Weg-Suche von bzw. zu einer Anbindung (ADR 0019 Ergaenzung, Test des Anwenders 2026-10-27).

fw-01 mit LAN, DMZ, Internet an `wan1` (Default-Route ueber 198.51.100.1) und MPLS an `mpls`
(10.80.0.0/16 und 10.81.0.0/16 per BGP ueber 10.90.0.2). Policies in dieser Reihenfolge:
1. wan1 -> dmz Web (tcp/443) erlauben   2. wan1 -> any alles sperren
3. mpls -> lan aus 10.80.0.0/16 erlauben   4. lan -> wan1 erlauben
Die Anbindung „Internet ISP-A“ ist von Hand erfasst (wan1), MPLS nur erkannt.
"""

import test_customers as tc
import test_policy_check as pc

ADMIN = {"X-Dev-Roles": "admin"}
OPERATOR = {"X-Dev-Roles": "operator"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
EN = {"Accept-Language": "en"}


def _edge(name: str, wan_ip: str = "198.51.100.2/29", mpls: bool = True) -> dict:
    policies = [
        pc._policy(1, "inet-to-web", "wan1", "dmz", ["all"], ["10.30.0.10"], ["tcp/443"]),
        pc._policy(2, "inet-deny", "wan1", "any", ["all"], ["all"], ["all"], "deny"),
        pc._policy(3, "mpls-to-lan", "mpls", "lan", ["10.80.0.0/16"], ["all"], ["all"]),
        pc._policy(4, "lan-to-inet", "lan", "wan1", ["10.10.0.0/24"], ["all"], ["all"]),
    ]
    interfaces = [
        pc._iface("lan", "10.10.0.1/24", None),
        pc._iface("dmz", "10.30.0.1/24", None),
        pc._iface("wan1", wan_ip, None),
    ]
    routes = [
        pc._route("10.10.0.0/24", [{"interface": "lan"}], "connected"),
        pc._route("10.30.0.0/24", [{"interface": "dmz"}], "connected"),
        pc._route("198.51.100.0/29", [{"interface": "wan1"}], "connected"),
        pc._route("0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}]),
    ]
    if mpls:
        interfaces.append(pc._iface("mpls", "10.90.0.1/30", None))
        routes += [
            pc._route("10.90.0.0/30", [{"interface": "mpls"}], "connected"),
            pc._route("10.80.0.0/16", [{"ip": "10.90.0.2", "interface": "mpls"}], "bgp"),
            pc._route("10.81.0.0/16", [{"ip": "10.90.0.2", "interface": "mpls"}], "bgp"),
        ]
    return pc._fw(name, policies, interfaces, routes)


def _system(client, tid: int, name: str, result: dict) -> int:
    body = {"tenant_id": tid, "name": name, "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return sid


def _seed(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sid = _system(client, tid, "fw-01", _edge("fw-01"))
    inet = {"tenant_id": tid, "name": "ISP-A", "kind": "internet",
            "source_system_ids": [sid], "interfaces": ["wan1"]}  # fmt: skip
    r = client.post("/routing/exits", json=inet, headers=OPERATOR)
    assert r.status_code == 201, r.text
    return tid, sid, r.json()["id"]


def _trace(client, tid, headers=None, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _resolve(client, tid, q, **params) -> dict:
    r = client.get("/routing/resolve", params={"tenant_id": tid, "q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_anbindungen_in_der_namenssuche(client):
    tid, sid, exit_id = _seed(client)
    out = _resolve(client, tid, "internet")
    (hit,) = (h for h in out["hits"] if h["type"] == "exit")
    assert out["counts"]["exit"] == 1
    assert {k: hit[k] for k in ("name", "kind", "exit", "exit_kind", "ip", "internet", "source",
                                "detail")} == {
        "name": "Internet ISP-A",
        "kind": "exit",
        "exit": f"exit:{exit_id}",
        "exit_kind": "internet",
        "ip": "198.51.100.10",  # beliebige Adresse im Internet (Dokumentationsbereich)
        "internet": True,
        "source": "fw-01 wan1",
        "detail": "beliebige Adresse im Internet · von Hand",
    }  # fmt: skip
    # erkannt (ueber das Interface mpls): die gelernten Netze, das erste als Ganzes
    (mpls,) = _resolve(client, tid, "mpls", type="exit")["hits"]
    assert (mpls["exit"], mpls["ip"], mpls["net"], mpls["source"], mpls["detail"]) == (
        "router:10.90.0.2",
        "10.80.0.0/16",
        "10.80.0.0/16",
        "fw-01 mpls",
        "2 Netze · erkannt",
    )
    # die von Hand erfasste deckt den Internet-Router ab: kein zweiter Vorschlag dafuer
    assert [h["exit"] for h in _resolve(client, tid, "anbindung")["hits"]] == [
        f"exit:{exit_id}",
        "router:10.90.0.2",
    ]
    # ohne Erkennen (kein Wort der Art, keine Firewall/kein Interface): nur die von Hand
    cheap = _resolve(client, tid, "isp-a")
    assert [(h["exit"], h["source"], h["detail"]) for h in cheap["hits"]] == [
        (f"exit:{exit_id}", "fw-01 wan1", "beliebige Adresse im Internet · von Hand")
    ]
    # Adressen: keine Anbindungen
    assert _resolve(client, tid, "10.80.")["counts"]["exit"] == 0
    assert _resolve(client, tid, "10.80.0.0/16")["counts"]["exit"] == 0
    # Kunde oben gewaehlt (0.76.0): nur Anbindungen an seinen Firewalls, geteilt markiert
    own = tc._create(client, tid, "Kunde A", [{"kind": "fw_vdom", "value": "root",
                                                "source_system_id": sid}])  # fmt: skip
    none = tc._create(client, tid, "Kunde B", [{"kind": "prefix", "value": "172.16.0.0/24"}])
    seen = _resolve(client, tid, "anbindung", customer_id=own["id"])["hits"]
    assert [(h["exit"], h["shared"]) for h in seen] == [
        (f"exit:{exit_id}", True),
        ("router:10.90.0.2", True),
    ]
    assert _resolve(client, tid, "anbindung", customer_id=none["id"])["hits"] == []


def test_anbindung_als_quelle_prueft_den_eingang(client):
    """Quelle Internet: Start an fw-01 mit Eingang wan1 - Policy 1 (wan1 -> dmz) entscheidet."""
    tid, _sid, exit_id = _seed(client)
    out = _trace(client, tid, src_exit=f"exit:{exit_id}", dst="10.30.0.10", service="tcp/443")
    assert [(s["label"], s["via"]) for s in out["start"]] == [
        ("fw-01", "über Internet ISP-A (wan1)")
    ]
    src = out["exits"]["src"]
    fw = src["firewalls"][0]
    assert (src["address"], src["address_kind"], src["label"], fw["interface"]) == (
        "198.51.100.10",
        "internet",
        "Internet ISP-A",
        "wan1",
    )
    (path,) = out["paths"]
    (hop,) = path["hops"]
    assert hop["ingress"] == {"interface": "wan1"}
    assert hop["rules"]["verdict"] == "allow"
    assert [r["external_id"] for r in hop["rules"]["rules"] if r["decides"]] == ["1"]
    assert path["policy"]["verdict"] == "allow"
    # anderer Dienst: die Sperre von wan1 greift
    (path,) = _trace(client, tid, src_exit=f"exit:{exit_id}", dst="10.30.0.10",
                     service="tcp/22")["paths"]  # fmt: skip
    assert path["hops"][0]["rules"]["text"] == "gesperrt durch Policy 2 „inet-deny“"
    # dieselbe Adresse ohne Anbindung: kein gelesenes Geraet hat sie angeschlossen
    plain = _trace(client, tid, src="198.51.100.10", dst="10.30.0.10")
    assert plain["paths"] == [] and "Kein gelesenes Gerät hat 198.51.100.10" in plain["notes"][0]


def test_erkannte_anbindung_als_quelle_mit_ihren_netzen(client):
    """MPLS (erkannt): Quelle = gelerntes Netz 10.80.0.0/16 als Ganzes; ein anderes Netz der
    Anbindung waehlen (`src`) - Policy 3 erfasst es nicht."""
    tid, _sid, _exit = _seed(client)
    out = _trace(client, tid, src_exit="router:10.90.0.2", dst="10.10.0.5")
    src = out["exits"]["src"]
    assert (src["address"], src["address_kind"], src["nets"], src["manual"]) == (
        "10.80.0.0/16",
        "net",
        ["10.80.0.0/16", "10.81.0.0/16"],
        False,
    )
    (path,) = out["paths"]
    assert path["hops"][0]["ingress"]["interface"] == "mpls"
    assert path["hops"][0]["rules"]["text"] == "erlaubt durch Policy 3 „mpls-to-lan“"
    assert src["internet"] is None  # keine Default-Route ueber MPLS
    other = _trace(client, tid, src_exit="router:10.90.0.2", src="10.81.0.0/16", dst="10.10.0.5")
    assert other["exits"]["src"]["address_kind"] == "given"
    assert other["paths"][0]["hops"][0]["rules"]["verdict"] == "implicit_deny"


def test_anbindung_als_ziel(client):
    tid, _sid, exit_id = _seed(client)
    out = _trace(client, tid, src="10.10.0.5", dst_exit=f"exit:{exit_id}")
    dst = out["exits"]["dst"]
    assert (dst["address"], dst["address_kind"]) == ("198.51.100.10", "internet")
    (path,) = out["paths"]
    assert path["via_exit"] is True
    assert path["end"] == {"kind": "exit", "text": "weiter über Internet ISP-A"}
    assert path["hops"][0]["egress"]["interface"] == "wan1"
    assert path["policy"]["verdict"] == "allow"  # Policy 4 lan -> wan1
    # erkannt: Ziel = gelerntes Netz, Weg ueber den Router der Anbindung
    (path,) = _trace(client, tid, src="10.10.0.5", dst_exit="router:10.90.0.2")["paths"]
    assert (path["via_exit"], path["hops"][0]["egress"]["next_hop"]) == (True, "10.90.0.2")
    # eigenes Ziel, das das Routing anders hinausfuehrt: Hinweis
    out = _trace(client, tid, src="10.10.0.5", dst_exit=f"exit:{exit_id}", dst="10.80.0.5")
    assert out["exits"]["dst"]["address_kind"] == "given"
    assert out["paths"][0]["via_exit"] is False
    assert out["notes"][-1] == (
        "Kein Weg führt über Internet ISP-A – das Routing nimmt einen anderen Weg"
    )
    # Anbindung als Quelle und Ziel: MPLS -> Internet
    out = _trace(client, tid, src_exit="router:10.90.0.2", dst_exit=f"exit:{exit_id}")
    assert (out["exits"]["src"]["address"], out["exits"]["dst"]["address"]) == (
        "10.80.0.0/16",
        "198.51.100.10",
    )
    assert out["paths"][0]["via_exit"] is True


def test_mehrere_firewalls_einer_anbindung(client):
    """Anbindung an zwei Firewalls: je eine ein Start; im Verbund ein Weg mit beiden Firewalls
    als ein Hop; `@<Quellsystem>` waehlt eine."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    a = _system(client, tid, "fw-a", _edge("fw-a", "198.51.100.2/29", mpls=False))
    b = _system(client, tid, "fw-b", _edge("fw-b", "198.51.100.3/29", mpls=False))
    body = {"tenant_id": tid, "name": "Internet Nord", "kind": "internet",
            "source_system_ids": [a, b], "interfaces": ["wan1"]}  # fmt: skip
    exit_id = client.post("/routing/exits", json=body, headers=OPERATOR).json()["id"]
    ref = f"exit:{exit_id}"
    out = _trace(client, tid, src_exit=ref, dst="10.30.0.10", service="tcp/443")
    assert [s["label"] for s in out["start"]] == ["fw-a", "fw-b"]
    assert out["exits"]["src"]["systems"] == sorted([a, b])
    assert [[h["label"] for h in p["hops"]] for p in out["paths"]] == [["fw-a"], ["fw-b"]]
    assert [p["exit_start"] for p in out["paths"]] == ["fw-a", "fw-b"]  # Variante je Firewall
    # Verbund: ein Weg, die Firewalls ein Hop
    group = {"tenant_id": tid, "group": "Nord", "system_ids": [a, b]}
    client.post("/systems/parallel-group", json=group, headers=ADMIN)
    (path,) = _trace(client, tid, src_exit=ref, dst="10.30.0.10", service="tcp/443")["paths"]
    (hop,) = path["hops"]
    assert [(x["label"], x["ingress"]["interface"]) for x in hop["parts"]] == [
        ("fw-a", "wan1"),
        ("fw-b", "wan1"),
    ]
    assert (path["policy"]["verdict"], path["exit_start"]) == ("allow", "Verbund Nord")
    # eine Firewall waehlen
    one = _trace(client, tid, src_exit=f"{ref}@{b}", dst="10.30.0.10")
    assert [s["label"] for s in one["start"]] == ["fw-b"]
    assert (one["exits"]["src"]["system"], one["exits"]["src"]["ref"]) == (b, f"{ref}@{b}")
    # zur Auswahl in der Oberflaeche: alle Firewalls der Anbindung
    assert [c["source"] for c in one["exits"]["src"]["choices"]] == ["fw-a", "fw-b"]
    # als Ziel nur ueber die gewaehlte Firewall
    out = _trace(client, tid, src="10.10.0.5", start=f"{a}|root", dst_exit=f"{ref}@{b}")
    assert out["paths"][0]["via_exit"] is False


def test_anbindung_fehler_und_englisch(client):
    tid, _sid, exit_id = _seed(client)
    r = client.get("/routing/trace", params={"tenant_id": tid, "src": "10.10.0.5"})
    assert r.status_code == 422 and r.json()["detail"] == "dst oder dst_exit angeben"
    r = client.get("/routing/trace", params={"tenant_id": tid, "dst": "10.0.0.1",
                                             "src_exit": "exit:999"})  # fmt: skip
    assert r.status_code == 422 and "Anbindung „exit:999“ unbekannt" in r.json()["detail"]
    bad = {"tenant_id": tid, "dst": "10.0.0.1", "src_exit": "fw-01"}
    assert client.get("/routing/trace", params=bad).status_code == 422
    # Anbindung ohne gelesene Firewall (Interface gibt es nicht): kein Start, mit Hinweis
    body = {"tenant_id": tid, "name": "Partner", "kind": "partner", "interfaces": ["port9"]}
    other = client.post("/routing/exits", json=body, headers=OPERATOR).json()["id"]
    out = _trace(client, tid, src_exit=f"exit:{other}", dst="10.30.0.10")
    assert out["paths"] == []
    assert out["notes"][0] == "Partner: keine gelesene Firewall mit dieser Anbindung – kein Start"
    # Englisch: Texte nach Accept-Language, Werte bleiben
    out = _trace(client, tid, EN, src_exit=f"exit:{exit_id}", dst="10.30.0.10")
    assert out["start"][0]["via"] == "via Internet ISP-A (wan1)"
    assert out["exits"]["src"]["address_kind"] == "internet"
    r = client.get("/routing/resolve", params={"tenant_id": tid, "q": "internet"}, headers=EN)
    (hit,) = (h for h in r.json()["hits"] if h["type"] == "exit")
    assert hit["detail"] == "any address on the Internet · manual"
