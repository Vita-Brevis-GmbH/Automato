"""Befunde zu DNS aus dem AD (1.19.0, ADR 0045 Nachtrag): PTR widerspricht A-Record, A-Records
auf IPs, die NetBox nicht kennt (nur gezaehlt), und was nicht pruefbar ist.

AD-DNS `ad` mit example.ch, 20.10.in-addr.arpa und der leeren Zone leer.example.ch:

- 10.20.0.5 A app01, PTR app01 -> passt
- 10.20.0.7 A db01, PTR db-alt (ohne A-Record) -> Warnung (die IP hat einen anderen A-Record)
- 10.20.0.8 A mail01 + smtp, PTR mail01 -> passt (ein PTR genuegt)
- 10.20.0.9 ohne A, PTR old-host (A-Record zeigt auf 10.20.0.50) -> Warnung
- 10.20.0.11 ohne A, PTR gone (ohne A-Record in example.ch) -> Hinweis (veralteter PTR)
- 10.20.0.12 ohne A, PTR host.other.example.net (Zone nicht gelesen) -> nicht pruefbar
- 10.30.0.5 A web30 + web31 (keine Reverse-Zone) -> nicht pruefbar
- 10.20.0.99 A nur-dns -> NetBox kennt die IP nicht (Hinweis, gezaehlt)
"""

from app.store.finding_text import explain
from test_reference_sources import TOKEN, _run, _system, _tenant

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
EN = {"Accept-Language": "en"}
Z = {"zone": "example.ch"}
R = {"zone": "20.10.in-addr.arpa"}

DNS = [
    {"kind": "a_record", "value": "10.20.0.5", "dns_name": "app01.example.ch", "attrs": Z},
    {"kind": "ptr_record", "value": "10.20.0.5", "dns_name": "app01.example.ch", "attrs": R},
    {"kind": "a_record", "value": "10.20.0.7", "dns_name": "db01.example.ch", "attrs": Z},
    {"kind": "ptr_record", "value": "10.20.0.7", "dns_name": "db-alt.example.ch", "attrs": R},
    {"kind": "a_record", "value": "10.20.0.8", "dns_name": "mail01.example.ch", "attrs": Z},
    {"kind": "a_record", "value": "10.20.0.8", "dns_name": "smtp.example.ch", "attrs": Z},
    {"kind": "ptr_record", "value": "10.20.0.8", "dns_name": "mail01.example.ch", "attrs": R},
    {"kind": "a_record", "value": "10.20.0.50", "dns_name": "old-host.example.ch", "attrs": Z},
    {"kind": "ptr_record", "value": "10.20.0.9", "dns_name": "old-host.example.ch", "attrs": R},
    {"kind": "ptr_record", "value": "10.20.0.11", "dns_name": "gone.example.ch", "attrs": R},
    {"kind": "ptr_record", "value": "10.20.0.12", "dns_name": "host.other.example.net",
     "attrs": R},
    {"kind": "a_record", "value": "10.30.0.5", "dns_name": "web30.example.ch", "attrs": Z},
    {"kind": "a_record", "value": "10.30.0.5", "dns_name": "web31.example.ch", "attrs": Z},
    {"kind": "a_record", "value": "10.20.0.99", "dns_name": "nur-dns.example.ch", "attrs": Z},
]  # fmt: skip
NETBOX = [{"kind": "ip_address", "value": ip}
          for ip in ("10.20.0.5", "10.20.0.7", "10.20.0.8", "10.20.0.50", "10.30.0.5")]  # fmt: skip


def _seed(client, records=DNS, netbox=NETBOX) -> dict:
    tid = _tenant(client)
    ad = _system(client, tid, name="ad", platform="ad_dns", base_url="dc01.example.ch",
                 read_scope="example.ch\n20.10.in-addr.arpa\nleer.example.ch")  # fmt: skip
    nb = _system(client, tid, name="netbox", platform="netbox", base_url="http://netbox:8080",
                 token="nb-lesen", ref_role="primary")  # fmt: skip
    _run(client, tid, ad["id"], "ad_dns", records)
    if netbox is not None:
        _run(client, tid, nb["id"], "netbox", netbox)
    return {"tid": tid, "ad": ad["id"], "nb": nb["id"]}


def _findings(client, tid, ftype, headers=None) -> dict[str, dict]:
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    r = client.get("/findings", params={"tenant_id": tid, "type": ftype, "status": "open"},
                   headers=headers or {})  # fmt: skip
    assert r.status_code == 200, r.text
    return {f["subject_id"]: f for f in r.json()}


def test_ptr_widerspricht_a_record(client):
    ids = _seed(client)
    found = _findings(client, ids["tid"], "dns_ptr_mismatch")
    assert set(found) == {"10.20.0.7", "10.20.0.9", "10.20.0.11"}, found
    db = found["10.20.0.7"]
    assert db["severity"] == "warning" and db["source_system_id"] == ids["ad"]
    assert db["detail"]["a"] == ["db01.example.ch"] and db["detail"]["ptr"] == ["db-alt.example.ch"]
    assert db["message"] == (
        "Der PTR von 10.20.0.7 zeigt auf db-alt.example.ch; die A-Records der IP nennen"
        " db01.example.ch; db-alt.example.ch ohne A-Record in den gelesenen Zonen."
    )
    assert "entscheidet der PTR" in db["hint"]
    old = found["10.20.0.9"]
    assert old["severity"] == "warning"
    assert old["detail"]["elsewhere"] == {"old-host.example.ch": ["10.20.0.50"]}
    assert "old-host.example.ch zeigt per A-Record auf 10.20.0.50" in old["message"]
    assert found["10.20.0.11"]["severity"] == "info"  # veralteter PTR ohne A-Record
    en = _findings(client, ids["tid"], "dns_ptr_mismatch", EN)["10.20.0.7"]
    assert en["message"].startswith("The PTR of 10.20.0.7 points to db-alt.example.ch")


def test_ip_nicht_in_netbox_nur_gezaehlt(client):
    ids = _seed(client)
    (unknown,) = _findings(client, ids["tid"], "dns_ip_unknown").values()
    assert unknown["subject_id"] == "ad" and unknown["severity"] == "info"
    assert unknown["detail"]["count"] == 1 and unknown["detail"]["examples"] == ["10.20.0.99"]
    assert unknown["detail"]["zones"] == {"example.ch": 1}
    assert unknown["message"] == (
        "1 A-Records von ad zeigen auf IPs, die NetBox (netbox) nicht kennt (je Zone:"
        " example.ch: 1), z. B. 10.20.0.99."
    )
    assert "legt keine IP-Adressen an" in unknown["hint"]
    # NetBox kennt alle: kein Befund
    _run(client, ids["tid"], ids["nb"], "netbox",
         [*NETBOX, {"kind": "ip_address", "value": "10.20.0.99"}])  # fmt: skip
    assert _findings(client, ids["tid"], "dns_ip_unknown") == {}


def test_nicht_pruefbar_wird_ausgewiesen(client):
    ids = _seed(client)
    (unchecked,) = _findings(client, ids["tid"], "dns_unchecked").values()
    d = unchecked["detail"]
    assert unchecked["severity"] == "info" and unchecked["subject_id"] == "ad"
    assert d["empty"] == ["leer.example.ch"]
    assert d["no_reverse"] == 1 and d["no_reverse_nets"] == ["10.30.0.0/24"]
    assert d["no_reverse_several"] == 1  # der Sync entscheidet dort nicht
    assert d["ptr_outside"] == 1 and d["ptr_outside_examples"] == ["host.other.example.net"]
    assert "state" not in d and "netbox_unread" not in d
    msg = unchecked["message"]
    assert msg.startswith("DNS aus dem AD (ad) nicht ganz geprüft: Zonen ohne Einträge")
    assert "1 A-Records ohne gelesene Rückwärtszone – PTR nicht prüfbar (z. B. 10.30.0.0/24)" in msg
    assert "davon 1 mit mehreren A-Records" in msg and "Grenze der Prüfung" in unchecked["hint"]

    # letzter Lauf fehlgeschlagen; NetBox angelegt, aber nicht gelesen; zweite Quelle nie gelesen
    body = {"platform": "ad_dns", "tenant_id": ids["tid"], "source_system_id": ids["ad"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    failed = {"status": "failed", "message": "Zonentransfer von example.ch abgelehnt"}
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=failed)
    _system(client, ids["tid"], name="ad2", platform="ad_dns", base_url="dc02.example.ch",
            read_scope="example.ch")  # fmt: skip
    _system(client, ids["tid"], name="netbox-2", platform="netbox", base_url="http://nb2:8080",
            token="nb-lesen")  # fmt: skip
    found = _findings(client, ids["tid"], "dns_unchecked")
    assert found["ad"]["detail"]["state"] == "stale"
    msg = found["ad"]["message"]
    assert "letzter Lauf fehlgeschlagen (Zonentransfer von example.ch abgelehnt)" in msg
    assert found["ad"]["detail"]["netbox_unread"] == ["netbox-2"]
    assert found["ad2"]["detail"]["state"] == "never"
    msg = found["ad2"]["message"]
    assert "ad2) nicht ganz geprüft: noch nicht gelesen – Zonen example.ch" in msg
    en = _findings(client, ids["tid"], "dns_unchecked", EN)["ad2"]
    assert en["message"].startswith("DNS from the AD (ad2) not fully checked: not collected yet")


def test_ohne_netbox_kein_befund_unbekannt_und_pruefung_abschaltbar(client):
    tid = _tenant(client)
    ad = _system(client, tid, name="ad", platform="ad_dns", base_url="dc01.example.ch",
                 read_scope="example.ch\n20.10.in-addr.arpa")  # fmt: skip
    _run(client, tid, ad["id"], "ad_dns", DNS)
    assert _findings(client, tid, "dns_ip_unknown") == {}  # keine NetBox: nichts zu vergleichen
    assert set(_findings(client, tid, "dns_ptr_mismatch")) == {"10.20.0.7", "10.20.0.9",
                                                              "10.20.0.11"}  # fmt: skip
    r = client.put("/checks/dns_ptr", params={"tenant_id": tid}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200, r.text
    assert _findings(client, tid, "dns_ptr_mismatch") == {}
    # ohne AD-DNS-Daten: keine Befunde
    _run(client, tid, ad["id"], "ad_dns", [])
    assert _findings(client, tid, "dns_unchecked")["ad"]["detail"]["empty"] == [
        "20.10.in-addr.arpa", "example.ch"
    ]  # fmt: skip


def test_texte_robust():
    for ftype in ("dns_ptr_mismatch", "dns_ip_unknown", "dns_unchecked"):
        msg, hint = explain(ftype, "x", None)
        assert msg and hint
