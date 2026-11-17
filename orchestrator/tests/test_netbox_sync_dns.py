"""NetBox-Sync, Abschnitt „DNS-Namen aus dem AD“ (1.19.0, ADR 0045 Nachtrag) gegen die Fake-NetBox.

Anwender 2026-11-17 (Prioritaet 7 „DNS aus AD“): der Sync setzt `dns_name` der bestehenden
IP-Adressen aus den A-Records der AD-DNS-Zonen - keine neuen IPs; mehrere A-Records: der PTR
entscheidet, sonst „mehrdeutig“; leer -> setzen; abweichend -> beide Werte in der Vorschau,
geschrieben nur nach Bestaetigung je Eintrag.

AD-DNS `ad` mit den Zonen example.ch und 20.10.in-addr.arpa (10.30.0.0/16 ohne Reverse-Zone);
NetBox (fuehrendes IPAM) mit von Hand gepflegten IPs:

- 10.20.0.5 leer, A app01 (+ Alias www per CNAME) -> setzen
- 10.20.0.6 web02, A web02 -> gleich
- 10.20.0.7 db-alt, A db01 -> abweichend (bestaetigen)
- 10.20.0.8 leer, A mail01 + smtp, kein PTR -> mehrdeutig (kein PTR)
- 10.20.0.9 leer, A print01 + scan01, PTR print01 -> setzen (PTR entscheidet)
- 10.20.0.10 scan02, A scan02 + fax02 -> gleich (NetBox hat einen der A-Records)
- 10.20.0.11 leer, Mandant einer anderen Automato-Mandanten -> unveraendert (gezaehlt)
- 10.20.0.12 zweimal (VRF A, VRF B), A dup -> mehrdeutig (mehrfach in NetBox)
- 10.30.0.5 leer, A web30 + web31, keine Reverse-Zone -> mehrdeutig (Rueckwaertszone fehlt)
- 2001:db8::5 leer, AAAA app6 -> setzen
- 10.20.0.99 A nur-dns -> NetBox kennt die IP nicht (nur gezaehlt, nicht angelegt)
"""

import json

import pytest
from app.models import AuditLog
from test_netbox_sync import FW_INVENTORY, _apply, _items, _plan, _result, _tagged, fake_netbox
from test_reference_sources import TOKEN, _run, _system, _tenant

ADMIN = {"X-Dev-Roles": "admin"}
ADMIN_EN = {**ADMIN, "Accept-Language": "en"}
Z = {"zone": "example.ch"}
R = {"zone": "20.10.in-addr.arpa"}


def _a(ip, name, **attrs):
    return {"kind": "a_record", "value": ip, "dns_name": name, "attrs": {**Z, **attrs}}


def _ptr(ip, name):
    return {"kind": "ptr_record", "value": ip, "dns_name": name, "attrs": R}


DNS = [
    _a("10.20.0.5", "app01.example.ch"),
    _a("10.20.0.5", "www.example.ch", cname="app01.example.ch"),  # Alias: nie der Name
    _ptr("10.20.0.5", "app01.example.ch"),
    _a("10.20.0.6", "web02.example.ch"),
    _a("10.20.0.7", "db01.example.ch"),
    _a("10.20.0.8", "mail01.example.ch"),
    _a("10.20.0.8", "smtp.example.ch"),
    _a("10.20.0.9", "print01.example.ch"),
    _a("10.20.0.9", "scan01.example.ch"),
    _ptr("10.20.0.9", "Print01.example.ch."),
    _a("10.20.0.10", "scan02.example.ch"),
    _a("10.20.0.10", "fax02.example.ch"),
    _a("10.20.0.11", "fremd.example.ch"),
    _a("10.20.0.12", "dup.example.ch"),
    _a("10.30.0.5", "web30.example.ch"),
    _a("10.30.0.5", "web31.example.ch"),
    _a("2001:db8::5", "app6.example.ch"),
    _a("10.20.0.99", "nur-dns.example.ch"),
]


@pytest.fixture
def netbox(monkeypatch):
    return fake_netbox(monkeypatch)


def _patches(netbox) -> list[tuple[str, list[dict]]]:
    """PATCH-Koerper je Endpunkt mitschreiben."""
    seen: list[tuple[str, list[dict]]] = []
    handler = netbox.handler

    def spy(request):
        if request.method == "PATCH":
            seen.append((request.url.path.removeprefix("/api/").strip("/"),
                         json.loads(request.content)))  # fmt: skip
        return handler(request)

    netbox.handler = spy
    return seen


def _ip(netbox, address, **fields) -> dict:
    return netbox.add("ipam/ip-addresses", {"address": address, "status": "active", **fields})


def _seed(client, netbox, records=DNS) -> dict:
    tid = _tenant(client)
    client.post("/tenants", json={"name": "kunde-b"}, headers=ADMIN)  # zweiter Mandant
    nb = _system(client, tid, name="netbox", platform="netbox", base_url="http://netbox:8080",
                 token="nb-lesen", write_token="nb-schreiben", ref_role="primary")  # fmt: skip
    ad = _system(client, tid, name="ad", platform="ad_dns", base_url="dc01.example.ch",
                 read_scope="example.ch\n20.10.in-addr.arpa")  # fmt: skip
    _run(client, tid, ad["id"], "ad_dns", records)
    other = netbox.add("tenancy/tenants", {"name": "kunde-b", "slug": "kunde-b"})
    vrf_a = netbox.add("ipam/vrfs", {"name": "VRF-A"})
    vrf_b = netbox.add("ipam/vrfs", {"name": "VRF-B"})
    _ip(netbox, "10.20.0.5/24", dns_name="")
    _ip(netbox, "10.20.0.6/24", dns_name="web02.example.ch")
    _ip(netbox, "10.20.0.7/24", dns_name="db-alt.example.ch", tags=[{"slug": "von-hand"}])
    _ip(netbox, "10.20.0.8/24")
    _ip(netbox, "10.20.0.9/24", dns_name="")
    _ip(netbox, "10.20.0.10/24", dns_name="scan02.example.ch")
    _ip(netbox, "10.20.0.11/24", tenant=other["id"])
    _ip(netbox, "10.20.0.12/24", vrf=vrf_a["id"])
    _ip(netbox, "10.20.0.12/24", vrf=vrf_b["id"])
    _ip(netbox, "10.30.0.5/16", dns_name="")
    _ip(netbox, "2001:db8::5/64", dns_name="")
    return {"tid": tid, "nb": nb["id"], "ad": ad["id"]}


def _dns_items(client, plan_id, **params) -> dict[str, dict]:
    return {i["key"]: i for i in _items(client, plan_id, kind="dns_name", limit=500, **params)}


def test_vorschau_leer_gleich_abweichend_mehrdeutig_unbekannt(client, netbox):
    ids = _seed(client, netbox)
    plan = _plan(client, ids, sections=["dns"])
    assert not netbox.writes  # Vorschau liest nur
    rows = _dns_items(client, plan["id"])
    actions = {k: i["action"] for k, i in rows.items()}
    assert actions == {
        "10.20.0.5": "update",  # leer -> setzen (der Alias www zaehlt nicht)
        "10.20.0.7": "update",  # abweichend -> nur nach Bestaetigung (`confirm`)
        "10.20.0.8": "ambiguous",  # zwei A-Records, kein PTR
        "10.20.0.9": "update",  # zwei A-Records, der PTR entscheidet
        "10.20.0.12": "ambiguous",  # zweimal in NetBox (VRF A, VRF B)
        "10.30.0.5": "ambiguous",  # zwei A-Records, Rueckwaertszone nicht gelesen
        "2001:db8::5": "update",  # AAAA
    }, actions
    # gleich (auch wenn NetBox einen von mehreren A-Records traegt) nur gezaehlt
    assert plan["summary"]["dns_name"] == {
        "create": 0, "update": 4, "unchanged": 2, "conflict": 0, "delete": 0, "obsolete": 0,
        "ambiguous": 3,
    }  # fmt: skip
    assert plan["can_apply"]
    # nur die Abweichung braucht die Bestaetigung (wie die Kabelnummern)
    assert {k for k, i in rows.items() if i.get("confirm")} == {"10.20.0.7"}
    assert rows["10.20.0.5"]["changes"] == [
        {"field": "dns_name", "old": "", "new": "app01.example.ch"}
    ]
    assert rows["10.20.0.5"]["dns"]["by"] == "a" and rows["10.20.0.5"]["manual"]
    assert rows["10.20.0.9"]["changes"][0]["new"] == "print01.example.ch"
    assert rows["10.20.0.9"]["dns"]["by"] == "ptr"
    # abweichend: beide Werte
    assert rows["10.20.0.7"]["changes"] == [
        {"field": "dns_name", "old": "db-alt.example.ch", "new": "db01.example.ch"}
    ]
    assert rows["10.20.0.8"]["dns"] == {
        "names": ["mail01.example.ch", "smtp.example.ch"], "ptr": [], "why": "no_ptr"
    }  # fmt: skip
    assert rows["10.20.0.8"]["changes"] == [{"field": "dns_name", "old": "", "new": None}]
    assert rows["10.20.0.12"]["dns"]["why"] == "netbox_several"
    assert rows["10.20.0.12"]["label"] == "DNS-Name 10.20.0.12 (VRF VRF-A)"
    assert rows["10.30.0.5"]["dns"]["why"] == "no_reverse"
    # unbekannte IP und fremder Mandant: nur gezaehlt
    warnings = plan["warnings"]
    assert (
        "DNS-Namen aus dem AD: 1 A-Records zeigen auf IPs, die NetBox nicht kennt – keine neuen"
        " IP-Adressen (z. B. 10.20.0.99)." in warnings
    ), warnings
    assert any("1 IPs gehören in NetBox einem anderen Mandanten" in w for w in warnings)
    assert "10.20.0.11" not in rows and "10.20.0.99" not in rows
    # Filter der Vorschau
    assert set(_dns_items(client, plan["id"], action="ambiguous")) == {
        "10.20.0.8", "10.20.0.12", "10.30.0.5"
    }  # fmt: skip
    # Englisch: Art, Bezeichnung und Hinweise
    en = client.get(f"/netbox-sync/plans/{plan['id']}", headers=ADMIN_EN).json()
    assert any(w.startswith("DNS names from the AD: 1 A records point to IPs that NetBox does")
               for w in en["warnings"])  # fmt: skip
    params = {"kind": "dns_name", "action": "update"}
    url = f"/netbox-sync/plans/{plan['id']}/items"
    rows_en = client.get(url, headers=ADMIN_EN, params=params).json()["rows"]
    assert rows_en[0]["label"] == "DNS name 10.20.0.5"


def test_uebernehmen_nur_bestaetigte_abweichungen(client, netbox, session_factory):
    ids = _seed(client, netbox)
    patches = _patches(netbox)
    plan = _plan(client, ids, sections=["dns"])
    done = _apply(client, plan["id"])  # ohne Bestaetigung
    assert done["status"] == "applied", done
    # neben Tag `automato` und Tenant (immer) nur die DNS-Namen
    assert done["result"]["dns_name"] == {"create": 0, "update": 3, "delete": 0}
    assert set(done["result"]) == {"tag", "tenant", "dns_name"}
    assert done["verification"]["deviation_count"] == 0, done["verification"]
    find = netbox.find
    assert find("ipam/ip-addresses", address="10.20.0.5/24")["dns_name"] == "app01.example.ch"
    assert find("ipam/ip-addresses", address="10.20.0.9/24")["dns_name"] == "print01.example.ch"
    assert find("ipam/ip-addresses", address="2001:db8::5/64")["dns_name"] == "app6.example.ch"
    # nicht bestaetigt: NetBox bleibt, wie es ist; mehrdeutig: nichts geschrieben
    db = find("ipam/ip-addresses", address="10.20.0.7/24")
    assert db["dns_name"] == "db-alt.example.ch"
    assert find("ipam/ip-addresses", address="10.20.0.8/24").get("dns_name") is None
    # nur das Feld dns_name - kein Tag, keine Beschreibung, auch an von Hand gepflegten IPs
    assert [p for p, _ in patches] == ["ipam/ip-addresses"]
    assert all(set(o) == {"id", "dns_name"} for _, body in patches for o in body)
    assert not _tagged(find("ipam/ip-addresses", address="10.20.0.5/24"))
    assert ("POST", "ipam/ip-addresses") not in {(m, p) for m, p, _ in netbox.writes}

    plan = _plan(client, ids, sections=["dns"])  # die Abweichung steht weiter zur Auswahl
    assert plan["totals"]["update"] == 1 and plan["can_apply"]
    assert _dns_items(client, plan["id"], action="update")["10.20.0.7"]["confirm"]
    # ohne Auswahl nichts zu tun; nur Abweichungen des Plans lassen sich bestaetigen
    url = f"/netbox-sync/plans/{plan['id']}/apply"
    r = client.post(url, headers=ADMIN)
    assert r.status_code == 409 and "confirm" in r.text
    assert client.post(url, json={"confirm": ["10.20.0.8"]}, headers=ADMIN).status_code == 409
    r = client.post(url, json={"confirm": ["10.20.0.7"]}, headers=ADMIN)
    assert r.status_code == 202, r.text
    done = client.get(f"/netbox-sync/plans/{plan['id']}", headers=ADMIN).json()
    assert done["status"] == "applied" and done["options"]["confirm"] == ["10.20.0.7"]
    assert done["verification"]["deviation_count"] == 0 and done["verification"]["checked"] == 1
    db = find("ipam/ip-addresses", address="10.20.0.7/24")
    assert db["dns_name"] == "db01.example.ch" and db["tags"] == [
        {"slug": "von-hand", "name": "von-hand"}
    ]
    s = session_factory()
    applied = {a.subject: a.detail for a in s.query(AuditLog).all()
               if a.action == "netbox_sync.applied"}  # fmt: skip
    s.close()
    assert applied[str(plan["id"])]["dns_confirmed"] == 1
    assert applied[str(plan["id"])]["labels_confirmed"] == 0
    assert applied[str(plan["id"])]["update"] == 1

    again = _plan(client, ids, sections=["dns"])  # stabil
    assert again["totals"]["update"] == 0
    assert again["totals"]["ambiguous"] == 3 and not again["can_apply"]


def test_bestaetigung_gilt_nur_fuer_den_gezeigten_stand(client, netbox):
    """Aendert sich der Name im AD nach der Vorschau, wird nichts geschrieben."""
    ids = _seed(client, netbox)
    plan = _plan(client, ids, sections=["dns"])
    changed = [r for r in DNS if r["value"] != "10.20.0.7"] + [_a("10.20.0.7", "db02.example.ch")]
    _run(client, ids["tid"], ids["ad"], "ad_dns", changed)
    r = client.post(f"/netbox-sync/plans/{plan['id']}/apply", json={"confirm": ["10.20.0.7"]},
                    headers=ADMIN)  # fmt: skip
    assert r.status_code == 202
    done = client.get(f"/netbox-sync/plans/{plan['id']}", headers=ADMIN).json()
    assert done["status"] == "failed" and "geändert" in done["message"]
    assert netbox.find("ipam/ip-addresses", address="10.20.0.7/24")["dns_name"] == (
        "db-alt.example.ch"
    )


def test_nur_mit_netbox_als_ipam_und_gelesenem_ad(client, netbox):
    tid = _tenant(client)
    _system(client, tid, name="ddi", platform="infoblox", base_url="https://ddi",
            username="u", password="p", ref_role="primary")  # fmt: skip
    ddi = client.get("/systems", headers=ADMIN).json()[0]["id"]
    _run(client, tid, ddi, "infoblox", [{"kind": "prefix", "value": "10.20.0.0/24"}])
    nb = _system(client, tid, name="netbox", platform="netbox", base_url="http://netbox:8080",
                 token="nb-lesen", write_token="nb-schreiben", ref_role="enrichment")  # fmt: skip
    _ip(netbox, "10.20.0.5/24", dns_name="")
    plan = _plan(client, {"tid": tid, "nb": nb["id"]}, sections=["dns"])
    assert plan["warnings"] == [
        "DNS-Namen aus dem AD: führendes IPAM ist „ddi“ – die DNS-Namen gehören dorthin,"
        " nicht übernommen."
    ]
    assert "dns_name" not in (plan["summary"] or {})

    tid2 = client.post("/tenants", json={"name": "kunde-c"}, headers=ADMIN).json()["id"]
    nb2 = _system(client, tid2, name="netbox-c", platform="netbox", base_url="http://nb-c:8080",
                  token="nb-lesen", write_token="nb-schreiben")  # fmt: skip
    plan = _plan(client, {"tid": tid2, "nb": nb2["id"]}, sections=["dns"])
    assert plan["warnings"] == [
        "DNS-Namen aus dem AD: keine AD-DNS-Quelle – Quellsystem „AD-DNS“ mit Zonen anlegen und"
        " lesen."
    ]
    # AD-DNS angelegt, aber nicht gelesen bzw. letzter Lauf fehlgeschlagen; Zone ohne Eintraege
    ad = _system(client, tid2, name="ad-c", platform="ad_dns", base_url="dc01.example.ch",
                 read_scope="example.ch\nleer.example.ch")  # fmt: skip
    plan = _plan(client, {"tid": tid2, "nb": nb2["id"]}, sections=["dns"])
    assert plan["warnings"] == [
        "DNS-Namen aus dem AD: ad-c: noch nicht gelesen – Zonen nicht geprüft: example.ch,"
        " leer.example.ch."
    ]
    _run(client, tid2, ad["id"], "ad_dns", [_a("10.20.0.5", "app01.example.ch")])
    body = {"platform": "ad_dns", "tenant_id": tid2, "source_system_id": ad["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN,
                json={"status": "failed", "message": "Zonentransfer abgelehnt"})  # fmt: skip
    plan = _plan(client, {"tid": tid2, "nb": nb2["id"]}, sections=["dns"])
    assert plan["warnings"][0].startswith(
        "DNS-Namen aus dem AD: letzter Lauf von ad-c fehlgeschlagen – es gilt der Stand vom"
    )
    assert plan["warnings"][1] == (
        "DNS-Namen aus dem AD: ad-c: Zonen ohne Einträge bzw. noch nicht gelesen:"
        " leer.example.ch."
    )
    en = client.get(f"/netbox-sync/plans/{plan['id']}", headers=ADMIN_EN).json()["warnings"]
    assert en[1] == (
        "DNS names from the AD: ad-c: zones without entries or not collected yet: leer.example.ch."
    )


def test_mit_netzinventar_ohne_hin_und_her(client, netbox):
    """Interface-IPs des Netzinventars: der Abschnitt entscheidet ueber ihren Namen allein - eine
    neue IP bekommt ihn gleich beim Anlegen (mehrdeutig: keinen, Hinweis); ein spaeterer Sync nur
    mit dem Netzinventar loescht ihn nicht wieder."""
    tid = _tenant(client)
    site = client.post(f"/tenants/{tid}/sites", json={"code": "ZRH", "name": "Zürich"},
                       headers=ADMIN).json()["id"]  # fmt: skip
    fw = _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw",
                 site_id=site)  # fmt: skip
    nb = _system(client, tid, name="netbox", platform="netbox", base_url="http://netbox:8080",
                 token="nb-lesen", write_token="nb-schreiben", ref_role="primary")  # fmt: skip
    ad = _system(client, tid, name="ad", platform="ad_dns", base_url="dc01.example.ch",
                 read_scope="example.ch")  # fmt: skip
    ids = {"tid": tid, "fw": fw["id"], "nb": nb["id"]}
    inventory = {**FW_INVENTORY, "interfaces": [*FW_INVENTORY["interfaces"], {
        "device": "fw-01", "name": "srv-vl11", "kind": "vlan", "parent": "port3",
        "ips": ["10.10.11.1/24"]}]}  # fmt: skip
    _result(client, ids, "fw", "fortigate", {"inventory": inventory})
    records = [_a("10.10.10.1", "fw-01-srv.example.ch"), _a("10.10.11.1", "gw-a.example.ch"),
               _a("10.10.11.1", "gw-b.example.ch")]  # fmt: skip
    _run(client, tid, ad["id"], "ad_dns", records)

    plan = _plan(client, ids, sections=["inventory", "dns"])
    assert (
        "DNS-Namen aus dem AD: 1 IPs legt ein anderer Abschnitt in diesem Sync an – ohne"
        " DNS-Namen, mehrere A-Records (z. B. 10.10.11.1)." in plan["warnings"]
    ), plan["warnings"]
    created = {i["key"]: i for i in _items(client, plan["id"], kind="ip_address")}
    assert created["10.10.10.1"]["fields"]["dns_name"] == "fw-01-srv.example.ch"
    assert "dns_name" not in created["10.10.11.1"]["fields"]
    assert _apply(client, plan["id"])["status"] == "applied"
    gw = netbox.find("ipam/ip-addresses", address="10.10.10.1/24")
    assert _tagged(gw) and gw["dns_name"] == "fw-01-srv.example.ch"

    plan = _plan(client, ids, sections=["inventory", "dns"])
    rows = _dns_items(client, plan["id"])
    assert {k: i["action"] for k, i in rows.items()} == {"10.10.11.1": "ambiguous"}
    assert plan["totals"]["update"] == plan["totals"]["create"] == 0
    # ein Name von Hand in NetBox bleibt auch im Sync nur mit dem Netzinventar stehen
    second = netbox.find("ipam/ip-addresses", address="10.10.11.1/24")
    netbox.db["ipam/ip-addresses"][second["id"]]["dns_name"] = "gw-b.example.ch"
    for sections in (["inventory"], ["inventory", "dns"]):
        again = _plan(client, ids, sections=sections)
        assert again["totals"]["update"] == again["totals"]["create"] == 0, sections
        assert again["totals"]["ambiguous"] == 0, sections  # einer der A-Records: gleich
