"""AD-DNS als Referenzquelle per Zonentransfer (ADR 0045): Anlegen, Rolle, Kontext, Vergleich
der DNS-Namen gegen das fuehrende IPAM, Pruefung der Erreichbarkeit."""

import socket

import dns.message
import dns.rcode
import dns.rrset
import dns.xfr
from app.store import reachability, references

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}

NETBOX = [
    {"kind": "prefix", "value": "10.20.0.0/24"},
    {"kind": "ip_address", "value": "10.20.0.5", "dns_name": "app01.example.ch"},
    {"kind": "ip_address", "value": "10.20.0.6", "dns_name": "web02.example.ch"},
    {"kind": "ip_address", "value": "10.20.0.7"},  # ohne DNS-Namen
    {"kind": "ip_address", "value": "10.20.0.8", "dns_name": "ohne-dns.example.ch"},
]
DNS = [
    {"kind": "a_record", "value": "10.20.0.5", "dns_name": "app01.example.ch",
     "attrs": {"zone": "example.ch"}},
    {"kind": "a_record", "value": "10.20.0.6", "dns_name": "web-alt.example.ch"},
    {"kind": "a_record", "value": "10.20.0.6", "dns_name": "WEB02.example.ch"},  # Alias passt
    {"kind": "a_record", "value": "10.20.0.7", "dns_name": "db01.example.ch"},
    {"kind": "ptr_record", "value": "10.20.0.7", "dns_name": "db01.example.ch"},
    {"kind": "a_record", "value": "10.20.0.99", "dns_name": "nur-dns.example.ch"},
]  # fmt: skip


def _tenant(client) -> int:
    return client.post("/tenants", json={"name": "kunde-a"}, headers=ADMIN).json()["id"]


def _system(client, tid, **body):
    return client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)


def _refs(client, tid, sid, platform, records):
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    client.post(
        f"/internal/collector-runs/{run_id}/references", headers=TOKEN, json={"records": records}
    )
    result = {"status": "completed", "reference_count": len(records)}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.json()["status"] == "completed", r.text


def _ad(client, tid, **kw):
    body = {"name": "ad", "platform": "ad_dns", "base_url": "DC01.example.ch",
            "read_scope": "Example.CH.\n20.10.in-addr.arpa, example.ch"} | kw  # fmt: skip
    return _system(client, tid, **body)


def test_anlegen_mit_zonen_nur_anreicherung(client):
    tid = _tenant(client)
    r = _ad(client, tid)
    assert r.status_code == 201, r.text
    ad = r.json()
    assert ad["base_url"] == "dns://dc01.example.ch"
    assert ad["read_scope"] == "example.ch\n20.10.in-addr.arpa"
    assert ad["ref_role"] == "enrichment" and ad["has_credentials"] is True  # keine noetig
    # nie fuehrend: ein DNS-Server kennt keine Netze
    assert _ad(client, tid, name="ad2", ref_role="primary").status_code == 422
    r = client.patch(f"/systems/{ad['id']}", json={"ref_role": "primary"}, headers=ADMIN)
    assert r.status_code == 422
    # Zonen Pflicht und gueltig, Adresse gueltig, keine Zugangsdaten
    assert _ad(client, tid, name="ad3", read_scope="").status_code == 422
    assert _ad(client, tid, name="ad4", read_scope="exa$mple.ch").status_code == 422
    assert _ad(client, tid, name="ad5", base_url="https://dc01").status_code == 422
    assert _ad(client, tid, name="ad6", base_url="").status_code == 422
    assert _ad(client, tid, name="ad7", token="x").status_code == 422
    change = {"base_url": "10.0.0.10:5353", "read_scope": "a.example.ch"}
    r = client.patch(f"/systems/{ad['id']}", json=change, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["base_url"] == "dns://10.0.0.10:5353"
    assert r.json()["read_scope"] == "a.example.ch"
    r = client.patch(f"/systems/{ad['id']}", json={"read_scope": " "}, headers=ADMIN)
    assert r.status_code == 422


def test_dns_namen_und_vergleich_gegen_netbox(client, session_factory):
    tid = _tenant(client)
    nb = _system(client, tid, name="netbox", platform="netbox", token="t").json()["id"]
    ad = _ad(client, tid).json()["id"]
    _refs(client, tid, nb, "netbox", NETBOX)
    _refs(client, tid, ad, "ad_dns", DNS)

    s = session_factory()
    ctx = references.context(s, tid)
    assert ctx.primary.source.name == "netbox" and [e.source.name for e in ctx.enrichments] == [
        "ad"
    ]
    assert ctx.prefix_enrichment() is None  # Netz-Infos nie aus dem DNS
    # DNS-Namen aus dem AD auch dort, wo NetBox keinen kennt
    assert references.dns_names(ctx, "10.20.0.7") == ["db01.example.ch"]
    assert references.dns_names(ctx, "10.20.0.99/32") == ["nur-dns.example.ch"]
    s.close()

    summary = client.get(f"/reconcile/summary?tenant_id={tid}").json()["ipam"]
    assert summary["mode"] == "dns" and summary["enrichment"]["platform"] == "ad_dns"
    # 10.20.0.8 ohne DNS-Eintrag ist kein Fehler; .6 passt ueber den zweiten Namen
    assert summary["summary"] == {"missing": 0, "different": 1, "only_enrichment": 1, "equal": 2}
    rows = client.get(f"/reconcile/ipam?tenant_id={tid}").json()["rows"]
    assert [(r["status"], r["value"]) for r in rows] == [
        ("different", "10.20.0.7"),
        ("only_enrichment", "10.20.0.99"),
    ]
    assert rows[0]["primary"]["dns_name"] == "" and rows[0]["enrichment"]["dns_names"] == [
        "db01.example.ch"
    ]
    assert client.get(f"/reconcile/ipam?tenant_id={tid}&q=nur-dns").json()["total"] == 1


def test_mit_ddi_netbox_zuerst_ad_waehlbar(client):
    tid = _tenant(client)
    ad = _ad(client, tid).json()["id"]  # zuerst angelegt, trotzdem zuletzt
    ddi = _system(client, tid, name="ddi", platform="infoblox", username="u", password="p")
    nb = _system(client, tid, name="netbox", platform="netbox", token="t").json()["id"]
    ddi = ddi.json()["id"]
    _refs(client, tid, ddi, "infoblox", [{"kind": "host", "value": "10.20.0.5",
                                          "dns_name": "app01.example.ch"}])  # fmt: skip
    _refs(client, tid, nb, "netbox", NETBOX[:2])
    _refs(client, tid, ad, "ad_dns", DNS)
    ipam = client.get(f"/reconcile/ipam?tenant_id={tid}").json()
    assert ipam["mode"] == "ipam" and ipam["enrichment"]["name"] == "netbox"
    assert [e["name"] for e in ipam["enrichments"]] == ["netbox", "ad"]
    dns = client.get(f"/reconcile/ipam?tenant_id={tid}&enrichment_id={ad}").json()
    assert dns["mode"] == "dns" and dns["enrichment"]["id"] == ad
    assert {r["value"] for r in dns["rows"]} == {"10.20.0.6", "10.20.0.7", "10.20.0.99"}


def _soa(zone: str, serial: int):
    query = dns.message.make_query(zone, "AXFR")
    msg = dns.message.make_response(query)
    soa = f"dc01.{zone}. h.{zone}. {serial} 900 600 86400 3600"
    msg.answer = [dns.rrset.from_text(f"{zone}.", 3600, "IN", "SOA", soa)]
    return msg


def test_pruefung_mit_zonentransfer(monkeypatch):
    def fake(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.10", port))]

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = []
    monkeypatch.setattr(reachability.socket, "getaddrinfo", fake)
    monkeypatch.setattr(reachability.socket, "create_connection",
                        lambda addr, timeout: seen.append(addr) or Conn())  # fmt: skip

    def allowed(address, zone, port, timeout, lifetime):
        return iter([_soa(zone, 42)])

    r = reachability.check("dns://dc01.example.ch", "ad_dns", zones=["example.ch"],
                           transfer=allowed)  # fmt: skip
    assert r.ok and [s.step for s in r.steps] == ["dns", "tcp", "axfr"]
    assert seen == [("10.0.0.10", 53)] and "example.ch (SOA 42)" in r.steps[-1].detail

    def refused(address, zone, port, timeout, lifetime):
        raise dns.xfr.TransferError(dns.rcode.REFUSED)
        yield  # Generator wie dns.query.xfr

    r = reachability.check("dns://dc01.example.ch", "ad_dns", zones=["example.ch"],
                           transfer=refused)  # fmt: skip
    assert not r.ok and r.failure == "axfr" and "Zonenübertragungen" in r.hint
    assert r.steps[-1].detail == "Zonentransfer example.ch: refused REFUSED"
    r = reachability.check("dns://dc01.example.ch", "ad_dns", zones=[])
    assert not r.ok and "keine Zonen" in r.hint
