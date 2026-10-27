"""Namenskonvention für VIPs und IP-Pools (0.95.0).

Rückmeldung des Anwenders (Test 2026-10-27): „In den NAT / VIPs hat es Namen, welche nicht in
die Namenskonvention passen, diese sollte auch geprüft werden.“ Bisher galt für VIPs jedes
Präfix der Konvention (auch H_), IP-Pools wurden nicht geprüft. Jetzt: Präfix ihres Typs im
Namensprofil (Objekt-Präfix → „VIP“ bzw. „VIP / DNAT“, „Virtual Server“, „SNAT IP-Pool“);
ohne gepflegtes Präfix eines Typs kein Befund; Namen des NSX ALB werden nicht geprüft.
"""

from app.i18n import use
from app.store.finding_text import explain
from test_avi import _deliver
from test_policy_check import ADMIN

FW = {
    "status": "completed",
    "objects": [
        {"name": "V-EXT_www.example.ch", "category": "vip", "obj_type": "static-nat",
         "family": "v4", "values": ["203.0.113.10"]},
        {"name": "webshop", "category": "vip", "obj_type": "static-nat", "family": "v4",
         "values": ["203.0.113.11"]},
        # Adress-Präfix an einer VIP: passt nicht zur Konvention der VIPs
        {"name": "H_web02", "category": "vip", "obj_type": "static-nat", "family": "v4",
         "values": ["203.0.113.12"]},
        {"name": "vs_shop", "category": "vip", "obj_type": "server-load-balance",
         "family": "v4", "values": ["203.0.113.13"]},
        {"name": "VIP_mail", "category": "vip", "obj_type": "static-nat", "family": "v6",
         "values": ["2001:db8::25"]},
        {"name": "IPP_inet01", "category": "ippool", "obj_type": "overload", "family": "v4",
         "values": ["198.51.100.10"]},
        {"name": "natpool1", "category": "ippool", "obj_type": "overload", "family": "v4",
         "values": ["198.51.100.20"]},
        {"name": "natpool6", "category": "ippool", "obj_type": "overload", "family": "v6",
         "values": ["2001:db8::100"]},
    ],
    "nat_rules": [
        {"source_platform": "fortigate", "external_id": "webshop", "name": "webshop",
         "ext_ip": "203.0.113.11", "mapped_ip": "10.10.0.20", "family": "v4",
         "raw": {"type": "static-nat"}},
        {"source_platform": "fortigate", "external_id": "natpool1", "name": "natpool1",
         "nat_type": "snat_pool", "ext_ip": "198.51.100.20", "family": "v4",
         "attrs": {"type": "overload"}},
    ],
}  # fmt: skip
AVI = {
    "status": "completed",
    "nat_rules": [
        {"source_platform": "avi", "external_id": "admin|Shop Web|10.30.0.50|443",
         "name": "Shop Web", "ext_ip": "10.30.0.50", "ext_port": "443", "portforward": True,
         "vip_type": "server-load-balance", "family": "v4",
         "targets": [{"ip": "10.10.0.20", "port": 8443, "status": "active"}],
         "attrs": {"platform": "avi", "tenant": "admin", "pool": "pool-web"}},
    ],
}  # fmt: skip


def _seed(client, profile: list[tuple[str, str]] | None = None) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    if profile is not None:
        body = {"customer": "Kunde A", "name": "Eigen"}
        pid = client.post("/naming-profiles", json=body, headers=ADMIN).json()["id"]
        for key, value in profile:
            entry = {"kind": "object_prefix", "key": key, "value": value}
            r = client.post(f"/naming-profiles/{pid}/dictionary", json=entry, headers=ADMIN)
            assert r.status_code == 200, r.text
        client.patch(f"/tenants/{tid}", json={"naming_profile_id": pid}, headers=ADMIN)
    body = {"tenant_id": tid, "name": "fw-01", "platform": "fortigate", "base_url": "https://x"}
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "fortigate", FW)  # fmt: skip
    body = {"tenant_id": tid, "name": "avi-01", "platform": "avi", "base_url": "https://avi",
            "username": "ro", "password": "x", "read_scope": "admin"}  # fmt: skip
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "avi", AVI)  # fmt: skip
    return tid


def _invalid(client, tid, lang: str | None = None) -> dict:
    params = {"tenant_id": tid, "type": "invalid_name", **({"lang": lang} if lang else {})}
    return {f["subject_id"]: f for f in client.get("/findings", params=params).json()}


def test_standard_konvention_fuer_vips_und_pools(client):
    tid = _seed(client)
    found = _invalid(client, tid)
    assert set(found) == {"webshop", "H_web02", "vs_shop", "natpool1", "natpool6 (IPv6)"}
    web = found["webshop"]
    assert web["detail"]["prefixes"] == ["V-EXT_", "VIP_"]
    assert web["detail"]["convention"] == "vip" and web["detail"]["category"] == "vip"
    assert web["message"] == (
        "VIP „webshop“ auf fw-01 beginnt mit keinem VIP-Präfix der Namenskonvention "
        "(erwartet: V-EXT_, VIP_)."
    )
    assert "Objekt-Präfix → Typ „VIP“ bzw. „VIP / DNAT“" in web["hint"]
    assert found["vs_shop"]["message"].startswith("Virtual Server „vs_shop“ auf fw-01")
    pool = found["natpool1"]
    assert pool["detail"]["prefixes"] == ["IPP_"] and pool["detail"]["convention"] == "snat_pool"
    assert pool["message"] == (
        "IP-Pool „natpool1“ auf fw-01 beginnt mit keinem Pool-Präfix der Namenskonvention "
        "(erwartet: IPP_)."
    )
    # NSX ALB: eigene Konvention, kein Objekt auf der Firewall - nicht geprüft
    assert "Shop Web" not in found

    # NAT / VIPs zeigt den Befund am Eintrag (VIP und Pool)
    rows = {n["name"]: n for n in client.get("/nat", params={"tenant_id": tid}).json()}
    assert "invalid_name" in rows["webshop"]["issues"]
    assert "invalid_name" in rows["natpool1"]["issues"]
    assert "invalid_name" not in rows["Shop Web"]["issues"]
    bad = client.get("/nat", params={"tenant_id": tid, "issues_only": "true"}).json()
    assert {"webshop", "natpool1"} <= {n["name"] for n in bad}


def test_englisch(client):
    tid = _seed(client)
    found = _invalid(client, tid, "en")
    assert found["webshop"]["message"] == (
        "VIP “webshop” on fw-01 does not start with any VIP prefix of the naming convention "
        "(expected: V-EXT_, VIP_)."
    )
    assert "object prefix → type “SNAT IP pool”" in found["natpool1"]["hint"]
    with use("en"):
        msg, _ = explain(
            "invalid_name",
            "vs1",
            {
                "category": "vip",
                "convention": "virtual_server",
                "virtual_server": True,
                "prefixes": ["VS_"],
            },
        )
    assert msg == (
        "Virtual Server “vs1” does not start with any prefix for virtual servers of the naming "
        "convention (expected: VS_)."
    )


def test_ohne_gepflegtes_praefix_kein_befund(client):
    """Eigenes Profil mit Adress-Präfixen, aber ohne VIP-/Pool-Typ: keine Prüfung dieser Namen
    (die Plattform funktioniert ohne befüllte Wörterbücher)."""
    tid = _seed(client, [("H_", "host"), ("N_", "network")])
    found = _invalid(client, tid)
    assert not set(found) & {"webshop", "H_web02", "vs_shop", "natpool1", "natpool6 (IPv6)"}


def test_eigene_praefixe_je_typ(client):
    """Präfix je Typ im Namensprofil: VIPs nur PUB_, Virtual Server VS_ bzw. vs_, Pools SNAT_."""
    profile = [("H_", "host"), ("PUB_", "vip"), ("vs_", "virtual_server"), ("SNAT_", "snat_pool")]
    tid = _seed(client, profile)
    found = _invalid(client, tid)
    assert {"V-EXT_www.example.ch", "webshop", "H_web02", "VIP_mail (IPv6)"} <= set(found)
    assert found["V-EXT_www.example.ch"]["detail"]["prefixes"] == ["PUB_"]
    assert "vs_shop" not in found  # Virtual Server mit eigenem Präfix
    assert {"IPP_inet01", "natpool1"} <= set(found)
    assert found["IPP_inet01"]["detail"]["prefixes"] == ["SNAT_"]
