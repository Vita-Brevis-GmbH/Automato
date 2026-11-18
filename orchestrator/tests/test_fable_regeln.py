# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Prüfung B „Regeln“ (Fable, Phase 1): Abweichungen der Regel-Zuordnung vom echten Gerät.

Jeder Test verlangt das Verhalten des echten Geräts (Herstellerdoku im Grund) und schlägt mit
dem heutigen Code fehl - `xfail(strict=True)`, damit die Suite grün bleibt und der Test meldet,
sobald Phase 2 die Abweichung behebt. Beispiele anonymisiert (RFC 1918, Dokumentationsnetze,
`example.ch`).

Eine Firewall `fw-r` mit LAN (10.10.0.0/24), DMZ (10.30.0.0/24) und Internet (203.0.113.0/24).
"""

import copy

import pytest
import test_aci_check as ac
import test_paths as tp
import test_policy_check as pc
from system_doc import system_list

OP = {"X-Dev-Roles": "operator"}

IFACES = [
    pc._iface("lan", "10.10.0.1/24", "lan"),
    pc._iface("dmz", "10.30.0.1/24", "dmz"),
    pc._iface("wan", "203.0.113.1/24", "internet"),
]
ROUTES = [
    pc._route("10.10.0.0/24", [{"interface": "lan"}], "connected"),
    pc._route("10.30.0.0/24", [{"interface": "dmz"}], "connected"),
    pc._route("203.0.113.0/24", [{"interface": "wan"}], "connected"),
    pc._route("0.0.0.0/0", [{"ip": "203.0.113.254", "interface": "wan"}]),
]


def _seed(client, rules, objects=(), nat=(), name="Kunde A") -> int:
    tid = client.post("/tenants", json={"name": name}, headers=pc.ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=pc.ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    result = pc._fw("fw-r", rules, IFACES, ROUTES, objects, nat)
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=pc.TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return tid


def _hop(client, tid, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    (path,) = r.json()["paths"]
    fw = [h for h in path["hops"] if h.get("rules")]
    assert len(fw) == 1, path["hops"]
    return fw[0]["rules"]


def _deciding(rules: dict) -> str | None:
    return next((x["external_id"] for x in rules["rules"] if x.get("decides")), None)


def _doc(client, tid, ip: str, name: str = "App") -> dict:
    host = next(s["name"] for s in system_list(client, tid) if s["ip"] == ip)
    body = {"name": name, "members": [{"kind": "host", "ref": host}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    return client.get(f"/applications/{app['id']}/doc", params={"tenant_id": tid}).json()


def _shown(doc: dict) -> set[str]:
    names = {r["id"]: r["name"] for r in doc["rules"]}
    return {names.get(h["rule_id"]) for h in doc["hits"]}


# -- FortiGate: Policy-Abgleich je Hop (`policy_check`) -----------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-1: internet-service-negate wird nicht gelesen – die Policy gilt für "
        "private Adressen als nicht passend (policy_check.py:187-191, 772-778)"
    ),
)
def test_b1_internet_service_negiert_trifft_private_adressen(client):
    """`internet-service-negate enable`: die Policy trifft alles, was NICHT im Internet Service
    liegt - also auch jede private Adresse."""
    isdb = {"internet-service": "enable", "internet-service-negate": "enable",
            "internet-service-name": [{"name": "Botnet-C&C.Server"}]}  # fmt: skip
    tid = _seed(client, [
        pc._policy(1, "lan-nicht-botnet", "lan", "dmz", ["all"], ["isdb:Botnet-C&C.Server"],
                   ["internet-service"], **isdb),
    ])  # fmt: skip
    rules = _hop(client, tid, src="10.10.0.5", dst="10.30.0.10", service="tcp/443")
    assert rules["verdict"] != "implicit_deny", rules
    assert "1" in [x["external_id"] for x in rules["rules"]]


VIP = {"name": "VIP_app", "category": "vip", "values": ["198.51.100.80"], "vdom": "root"}
VIP_NAT = {"source_platform": "fortigate", "external_id": "VIP_app", "name": "VIP_app",
           "ext_ip": "198.51.100.80", "mapped_ip": "10.30.0.50", "vdom": "root",
           "raw": {"extintf": "wan"}}  # fmt: skip


def _vip_policy(pid, name, src_if="internet"):
    return pc._policy(pid, name, src_if, "dmz", ["all"], ["vip:VIP_app=198.51.100.80"],
                      ["tcp/443"]) | {"destination_refs": ["VIP_app"]}  # fmt: skip


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-2: Policy mit VIP als Ziel trifft auch Verkehr direkt zur internen "
        "Adresse (rule_index.py:240-241 legt die interne IP als Zielbereich an, "
        "policy_check._address nutzt ihn)"
    ),
)
def test_b2_policy_mit_vip_trifft_keinen_verkehr_direkt_zur_internen_adresse(client):
    """Eine Policy mit der VIP als Ziel trifft nur Verkehr, den die VIP umgeschrieben hat.
    Verkehr aus dem LAN direkt zur internen Adresse des Servers trifft sie nicht."""
    tid = _seed(client, [_vip_policy(1, "any-to-vip", "any")], [VIP], [VIP_NAT])
    rules = _hop(client, tid, src="10.10.0.5", dst="10.30.0.50", service="tcp/443")
    assert rules["verdict"] == "implicit_deny", rules


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-3: am VIP-Hop wird das Ziel vor dem DNAT (externe IP) mit Adressobjekten"
        " verglichen statt das Ziel nach dem DNAT (policy_check.py:524-527, 867)"
    ),
)
def test_b3_adresse_der_externen_ip_trifft_verkehr_zur_vip_nicht(client):
    """DNAT vor dem Policy-Abgleich: verglichen wird das Ziel nach dem DNAT. Eine erlaubende
    Policy mit einem Adressobjekt der externen IP trifft den Verkehr zur VIP nicht - es
    entscheidet die Policy mit der VIP."""
    ext = pc._policy(1, "inet-to-ext-ip", "internet", "dmz", ["all"], ["198.51.100.80"],
                     ["tcp/443"])  # fmt: skip
    tid = _seed(client, [ext, _vip_policy(2, "inet-to-vip")], [VIP], [VIP_NAT])
    rules = _hop(client, tid, src="203.0.113.50", dst="198.51.100.80", service="tcp/443")
    assert (rules["verdict"], _deciding(rules)) == ("allow", "2"), rules


# -- Applikations-Doku: welche Regeln einer Applikation zugeordnet werden ------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-4: negierte Quelle (srcaddr-negate) wird in der Doku wie eine genannte "
        "Quelle zugeordnet (rule_index.covers kennt keine Negation)"
    ),
)
def test_b4_negierte_quelle_ist_kein_zugriff_des_systems(client):
    """`srcaddr-negate enable` mit dem Server als Quelle: die Policy trifft alles ausser dem
    Server - sie ist kein Zugriff des Servers."""
    tid = _seed(client, [
        pc._policy(1, "web-to-lan", "dmz", "lan", ["10.30.0.10"], ["10.10.0.0/24"], ["tcp/22"]),
        pc._policy(2, "nicht-web-to-lan", "dmz", "lan", ["10.30.0.10"], ["10.10.0.0/24"],
                   ["tcp/3389"], **{"srcaddr-negate": "enable"}),
    ])  # fmt: skip
    shown = _shown(_doc(client, tid, "10.30.0.10"))
    assert "web-to-lan" in shown
    assert "nicht-web-to-lan" not in shown


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-5: Policy mit negiertem Ziel, die das System real trifft, fehlt in der "
        "Doku (rule_index.covers kennt keine Negation)"
    ),
)
def test_b5_negiertes_ziel_das_das_system_trifft_fehlt_nicht(client):
    """`dstaddr-negate enable` mit einem fremden Netz als Ziel: die Policy trifft den Server
    (er liegt nicht im genannten Netz) - sie gehört in seine Doku wie eine offene Regel."""
    tid = _seed(client, [
        pc._policy(1, "lan-to-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"]),
        pc._policy(2, "lan-to-nicht-mgmt", "lan", "dmz", ["10.10.0.5"], ["10.30.0.128/25"],
                   ["tcp/22"], **{"dstaddr-negate": "enable"}),
    ])  # fmt: skip
    doc = _doc(client, tid, "10.30.0.10")
    names = {r["id"]: r["name"] for r in doc["rules"]}
    known = {names.get(h["rule_id"]) for h in doc["hits"]} | set(names.values())
    assert "lan-to-nicht-mgmt" in known


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-6: vollständig überdeckte Regel (erste passende gewinnt) steht ohne "
        "Kennzeichen als Zugriff in den Kategorien (appdoc._covered_rows ohne "
        "Reihenfolge)"
    ),
)
def test_b6_ueberdeckte_regel_ist_als_ueberdeckt_ausgewiesen(client):
    """Erste passende Policy gewinnt: Policy 2 nennt den Server, wird aber von der Sperre 1
    davor (gleiche Interfaces, Quelle und Ziel umfassend, alle Dienste) vollständig überdeckt -
    sie trifft nie Verkehr. Die Doku weist das an der Regel aus (Kennzeichen `_shadowed` am
    Treffer bzw. an der Zeile; was das Regelwerk daraus macht, ist eine neue Bedingung -
    ADR 0043), statt sie ohne Hinweis als Zugriff auf den Server zu zeigen."""
    tid = _seed(
        client,
        [
            pc._policy(1, "lan-to-dmz-deny", "lan", "dmz", ["all"], ["all"], ["all"], "deny"),
            pc._policy(
                2, "lan-to-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"]
            ),
        ],
    )
    doc = _doc(client, tid, "10.30.0.10")
    rid = next(r["id"] for r in doc["rules"] if r["name"] == "lan-to-web")
    marks = [h for h in doc["hits"] if h["rule_id"] == rid]
    marks += [r for t in doc["tables"] for r in t["rows"] if r.get("Description") == "lan-to-web"]
    assert marks and all(m.get("_shadowed") for m in marks), marks


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-7: FQDN-Objekte werden bei der Zuordnung Regel-Applikation nicht "
        "aufgelöst (rule_index.value_range -> None)"
    ),
)
def test_b7_fqdn_objekt_mit_der_adresse_des_systems_gehoert_in_die_doku(client):
    """Ein FQDN-Adressobjekt trifft die Adressen, auf die der Name auflöst. Löst
    `web01.example.ch` (IPAM) auf den Server auf, ist die Policy ein Zugriff auf den Server."""
    tid = _seed(client, [
        pc._policy(1, "lan-to-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"]),
        pc._policy(2, "lan-to-web-fqdn", "lan", "dmz", ["10.10.0.0/24"],
                   ["fqdn:web01.example.ch"], ["tcp/8443"]),
    ])  # fmt: skip
    ddi = tp._system(client, tid, "ddi", "infoblox", username="u", password="p")
    run_id = tp._run(client, tid, ddi, "infoblox")
    records = [{"kind": "host", "value": "10.30.0.10", "dns_name": "web01.example.ch"}]
    client.post(f"/internal/collector-runs/{run_id}/references", headers=tp.TOKEN,
                json={"records": records})  # fmt: skip
    client.post(f"/internal/collector-runs/{run_id}/result", headers=tp.TOKEN,
                json={"status": "completed", "reference_count": 1})  # fmt: skip
    assert "lan-to-web-fqdn" in _shown(_doc(client, tid, "10.30.0.10"))


# -- Cisco ACI: Vorrang der Contracts (`aci_check`) -----------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-8: ACI-Vorrang ist „deny zuerst“ statt Priorität der Zoning-Rules "
        "(aci_check.py:196)"
    ),
)
def test_b8_aci_vorrang_epg_contract_vor_sperre_ueber_vzany(client):
    """Vorrang nach Priorität der Zoning-Rules, nicht „deny zuerst“: ein Contract zwischen zwei
    EPGs (Priorität 7) gewinnt gegen eine Sperre vzAny -> vzAny (Priorität 17, Vorgabe)."""
    apic = copy.deepcopy(ac.APIC)
    apic["rules"].append(ac._rule("CT_block", [f"epg:{ac.ANY}"], [f"epg:{ac.ANY}"],
                                  ["tcp/1433"], "deny"))  # fmt: skip
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", ac.FW1), ("apic-01", "cisco_aci", apic)):
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    rules, _policy, _out = ac._aci(client, tid, src="10.50.1.10", dst="10.50.2.20",
                                   service="tcp/1433")  # fmt: skip
    assert (rules["verdict"], rules["text"]) == ("allow", "erlaubt durch Contract CT_db"), rules


# -- Stichprobe (Muster, synthetisch): Verbund im Weg, Firewall ausserhalb mit gleichen Regeln --


def _fw_a(name: str, transit_ip: str) -> dict:
    """Mitglied des Verbunds „fw-a“: LAN, DMZ und Transfernetz zur Firewall `fw-vpn`."""
    return pc._fw(
        name,
        [pc._policy(1, "lan-to-web", "any", "any", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"])],
        [
            {"name": "lan", "ips": ["10.10.0.1/24"], "zone": "lan", "vdom": "root"},
            {"name": "dmz", "ips": ["10.30.0.1/24"], "zone": "dmz", "vdom": "root"},
            {"name": "to-vpn", "ips": [transit_ip], "zone": "transit", "vdom": "root"},
        ],
        [
            {"context": "root", "prefix": "10.10.0.0/24", "protocol": "connected",
             "next_hops": [{"interface": "lan"}]},
            {"context": "root", "prefix": "10.30.0.0/24", "protocol": "connected",
             "next_hops": [{"interface": "dmz"}]},
            {"context": "root", "prefix": "10.70.0.0/24", "protocol": "connected",
             "next_hops": [{"interface": "to-vpn"}]},
        ],
    )  # fmt: skip


FW_VPN = pc._fw(
    "fw-vpn",
    [pc._policy(1, "lan-to-web", "any", "any", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"])],
    [{"name": "to-a", "ips": ["10.70.0.9/24"], "zone": "transit", "vdom": "root"}],
    [
        {"context": "root", "prefix": "10.70.0.0/24", "protocol": "connected",
         "next_hops": [{"interface": "to-a"}]},
        {"context": "root", "prefix": "10.10.0.0/24", "protocol": "static",
         "next_hops": [{"ip": "10.70.0.1", "interface": "to-a"}]},
        {"context": "root", "prefix": "10.30.0.0/24", "protocol": "static",
         "next_hops": [{"ip": "10.70.0.1", "interface": "to-a"}]},
    ],
)  # fmt: skip


def _seed_verbund(client) -> tuple[int, dict[str, int]]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=pc.ADMIN).json()["id"]
    ids = {}
    members = [("fw-a1", _fw_a("fw-a1", "10.70.0.1/24")), ("fw-a2", _fw_a("fw-a2", "10.70.0.2/24"))]
    for name, result in (*members, ("fw-vpn", FW_VPN)):
        ids[name] = tp._system(client, tid, name, "fortigate")
        tp._deliver(client, tid, ids[name], "fortigate", result)
    body = {"tenant_id": tid, "group": "fw-a", "system_ids": [ids["fw-a1"], ids["fw-a2"]]}
    assert client.post("/systems/parallel-group", json=body, headers=pc.ADMIN).status_code == 200
    return tid, ids


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-12: Zuordnung nach Adressüberdeckung über alle Firewalls des Mandanten; eine "
        "Regel, die das System nennt, bleibt auch von einer Firewall ausserhalb des Wegs "
        "(Regelwerk 1: off_path_specific aus, appdoc.py:1487)"
    ),
)
def test_b12_firewall_ausserhalb_des_wegs_traegt_keine_regel_bei(client):
    """LAN -> Server in der DMZ geht nur über den Verbund fw-a (beide Netze direkt
    angeschlossen). `fw-vpn` hängt am Transfernetz, kennt Routen in beide Netze und trägt
    denselben Regelsatz - der Verkehr kommt dort nie vorbei. Die Doku des Servers zeigt die
    Regel des Verbunds einmal und keine Regel von `fw-vpn`."""
    tid, ids = _seed_verbund(client)
    doc = _doc(client, tid, "10.30.0.10")
    by_id = {r["id"]: r for r in doc["rules"]}
    systems = sorted(by_id[h["rule_id"]]["source_system_id"] for h in doc["hits"])
    assert systems == [ids["fw-a1"]], systems
