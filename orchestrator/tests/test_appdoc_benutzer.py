"""Remote-Access-Regel mit Benutzergruppen in der Doku einer Applikation (1.23.0).

Anwender 2026-11-20: „Die Firewall Regeln für die Remote Access VPN (Client VPN) sind zwei mal in
den Firewall Regeln drin. Einmal mit den User, welcher zugreifen dürfen und einmal als Client VPN
Range“ – „Die Rule ist in den Applikations-Firewall-Regeln für die Doku zweimal erschienen“.

Ursache: der Collector liefert Benutzer und Gruppen einer FortiGate-Policy als zusaetzliche Quelle
(`usergroup:<Name>`); die Doku teilte die Gegenstellen je Zone bzw. Kategorie auf (0.125.0) - die
Gruppe ohne Adresse landete in „unspezifisch“, der Adressbereich in „Remote Access“. Auf dem Geraet
sind die Gruppen eine Bedingung an die Quelle (Adresse **und** Benutzer): eine Zeile je Policy mit
„<Adressbereich> (Benutzer: <Gruppen>)“.

Umgebung wie in `test_appdoc_split` (Webshop mit web01 10.20.0.5, fw-04, Zonen Clients = Office
Access, Remote Access = Edge VPN, Partner = DC); anonymisiert.
"""

import pytest
from test_appdoc_names import OP, _env
from test_appdoc_zones import ZONES, _rule, _types
from test_vmware import _deliver, _system

RANGE = "10.77.9.1-10.77.9.254"  # Client-VPN-Bereich in der Zone Remote Access


def _ra(pid, name, sources, refs, groups, **kw):
    """Remote-Access-Policy: vom Tunnel der Clients (Dial-up) zum Server, mit Benutzergruppen."""
    raw = {"policyid": pid, "srcintf": [{"name": "ra-tunnel"}], "dstintf": [{"name": "srv"}],
           "srcaddr": [{"name": r} for r in refs if r not in groups],
           "groups": [{"name": g} for g in groups], "status": "enable",
           "schedule": "always"}  # fmt: skip
    return _rule(pid, name, [*sources, *(f"usergroup:{g}" for g in groups)], ["10.20.0.5"],
                 source_refs=refs, src_zone="ra-tunnel", raw=raw, **kw)  # fmt: skip


FW = {
    "status": "completed",
    "rules": [
        _ra(71, "ra-to-web", [f"range:{RANGE}"], ["RA-Bereich", "VPN-Benutzer"],
            ["VPN-Benutzer"], services=["tcp/8071"]),
        # zwei Bereiche (beide Remote Access) und zwei Gruppen: eine Zeile, Gruppen am Ende
        _ra(72, "ra2-to-web", ["10.77.10.0/24", "10.77.11.0/24"],
            ["RA-Netz-1", "RA-Netz-2", "VPN-Admins", "VPN-Support"],
            ["VPN-Admins", "VPN-Support"], services=["tcp/8072"]),
    ],
    "objects": [],
}  # fmt: skip


def _setup(client) -> tuple[int, dict]:
    tid, app = _env(client)
    fw4 = _system(client, tid, "fw-04", "fortigate", base_url="https://fw4")
    _deliver(client, tid, fw4, "fortigate", FW)
    ids = {}
    for name, nets in ZONES.items():
        body = {"tenant_id": tid, "name": name, "kind": "access",
                "selectors": [{"kind": "prefix", "value": n} for n in nets]}  # fmt: skip
        r = client.post("/zones", json=body, headers=OP)
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    types = {ids["Clients"]: "Office Access", ids["Remote Access"]: "Edge VPN",
             ids["Partner"]: "DC"}  # fmt: skip
    assert _types(client, tid, types).status_code == 200
    return tid, app


def _rows(doc: dict, name: str) -> list[tuple[int, dict]]:
    return [(t["index"], r) for t in doc["tables"] for r in t["rows"] if r["Description"] == name]


@pytest.mark.parametrize("lang", ["de", "en"])
def test_remote_access_eine_zeile_mit_benutzern(client, lang):
    _tid, app = _setup(client)
    doc = client.get(f"/applications/{app['id']}/doc", headers={"Accept-Language": lang}).json()
    who = "Benutzer" if lang == "de" else "users"
    # eine Zeile, in der Kategorie der Adresse (Remote Access → DC), nicht zusaetzlich die
    # Gruppe allein in „unspezifisch“
    ((idx, row),) = _rows(doc, "ra-to-web")
    assert idx == 5 and row["Zone"] == "Remote Access"
    assert row["Source"] == f"{RANGE} ({who}: VPN-Benutzer)"
    ((idx, row),) = _rows(doc, "ra2-to-web")
    assert idx == 5
    assert row["Source"] == f"10.77.10.0/24\n10.77.11.0/24 ({who}: VPN-Admins, VPN-Support)"


def test_ohne_benutzer_wie_bisher(client):
    """Gleiche Policy ohne Gruppen: unveraendert eine Zeile mit dem Bereich."""
    tid, app = _env(client)
    fw4 = _system(client, tid, "fw-04", "fortigate", base_url="https://fw4")
    rule = _rule(73, "plain-to-web", [f"range:{RANGE}"], ["10.20.0.5"], services=["tcp/8073"])
    _deliver(client, tid, fw4, "fortigate", {"status": "completed", "rules": [rule],
                                             "objects": []})  # fmt: skip
    doc = client.get(f"/applications/{app['id']}/doc").json()
    ((_idx, row),) = _rows(doc, "plain-to-web")
    assert row["Source"] == RANGE
