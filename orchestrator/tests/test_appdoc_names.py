"""Applikations-Doku (0.95.0, ADR 0012 Ergaenzung 0.95.0, Test des Anwenders 2026-10-27):

- Gegenstellen, von denen die Firewall-Regel nur die Adresse kennt, nennen in den Kategorien
  ihren Namen (IPAM, VM, EPG bzw. Firewall-Objekt), wenn der Admin es am Mandanten einstellt -
  nur fuer Betreuer, Kunden-Benutzer sehen weiter nur die Adressen.
- EPG der Doku im Detail (`GET /applications/{id}/epgs/{epg_id}`) statt zu den Regeln springen.

Umgebung wie in `test_application_doc` (Webshop mit den VMs web01/db01 in TE_ka/AP_007), dazu
eine FortiGate fw-03 mit Regeln zu Gegenstellen nur mit Adresse und ein zweiter APIC mit einem
anderen ACI-Tenant (Endpunkt bzw. Subnetz einer fremden EPG).
"""

import io
import zipfile

from app import audit
from app.models import AuditLog
from test_application_doc import _app
from test_vmware import DDI, TOKEN, _deliver, _run, _system

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}


def _rule(pid, name, src, dst, **kw):
    inbound = src[0] != "10.20.0.5"
    return {"source_platform": "fortigate", "external_id": str(pid), "name": name,
            "action": "allow", "sources": src, "destinations": dst, "services": ["tcp/443"],
            "family": "v4", "src_zone": "L3O_bit" if inbound else "lan",
            "dst_zone": "lan" if inbound else "L3O_bit", **kw}  # fmt: skip


FW3 = {
    "status": "completed",
    "rules": [
        _rule(31, "to-partner", ["10.20.0.5"], ["10.30.0.8"]),  # IPAM: DNS-Name
        _rule(32, "to-db", ["10.20.0.5"], ["10.20.1.7"]),  # VM db01 (nicht im IPAM)
        _rule(33, "from-epg", ["10.40.0.9"], ["10.20.0.5"]),  # Endpunkt einer fremden EPG
        _rule(34, "from-db-net", ["10.20.1.0/24"], ["10.20.0.5"]),  # IPAM-Netz „DB“
        _rule(35, "from-other", ["10.41.0.0/24"], ["10.20.0.5"]),  # Subnetz einer fremden EPG
        _rule(36, "to-tool", ["10.20.0.5"], ["10.50.0.3"], destination_refs=["H_tool01"]),
        _rule(37, "to-unknown", ["10.20.0.5"], ["10.60.0.1"]),  # ohne Namen
    ],
    "objects": [
        {"name": "H_tool01", "category": "address", "family": "v4", "values": ["10.50.0.3"]},
    ],
}
OTHER_ACI = {
    "status": "completed",
    "epgs": [
        {"name": "EG_tool", "dn": "uni/tn-TE_kx/ap-AP_x/epg-EG_tool", "tenant": "TE_kx",
         "ap": "AP_x", "vrf": "VRF_x", "endpoints": [{"ip": "10.40.0.9"}]},
        {"name": "EG_other", "dn": "uni/tn-TE_kx/ap-AP_x/epg-EG_other", "tenant": "TE_kx",
         "ap": "AP_x", "vrf": "VRF_x", "subnets": ["10.41.0.1/24"]},
    ],
}  # fmt: skip
NAMED = {
    "to-partner": "partner01.example.ch (10.30.0.8)",
    "to-db": "db01-007-ka (10.20.1.7)",
    "from-epg": "TE_kx/AP_x/EG_tool (10.40.0.9)",
    "from-db-net": "DB (10.20.1.0/24)",
    "from-other": "TE_kx/AP_x/EG_other (10.41.0.0/24)",
    "to-tool": "H_tool01 (10.50.0.3)",
    "to-unknown": "10.60.0.1",
}
BARE = {k: v.rsplit("(", 1)[-1].rstrip(")") for k, v in NAMED.items()}


def _env(client):
    tid, app = _app(client)
    # IPAM mit einem weiteren Server (neuer Referenzstand des DDI)
    ddi = next(s["id"] for s in client.get(f"/systems?tenant_id={tid}", headers=ADMIN).json()
               if s["platform"] == "infoblox")  # fmt: skip
    records = [*DDI, {"kind": "host", "value": "10.30.0.8", "dns_name": "partner01.example.ch"}]
    run_id = _run(client, tid, ddi, "infoblox")
    client.post(f"/internal/collector-runs/{run_id}/references", headers=TOKEN,
                json={"records": records})  # fmt: skip
    done = {"status": "completed", "reference_count": len(records)}
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=done)
    apic2 = _system(client, tid, "apic-02", "cisco_aci", base_url="https://apic2")
    _deliver(client, tid, apic2, "cisco_aci", OTHER_ACI)
    fw3 = _system(client, tid, "fw-03", "fortigate", base_url="https://fw3")
    _deliver(client, tid, fw3, "fortigate", FW3)
    return tid, app


def _peers(doc: dict) -> dict[str, set[str]]:
    """Gegenstellen je Regel (Description) aus den Kategorien - Source bzw. Destination; eine
    Regel zwischen zwei Systemen der Applikation (to-db) steht je System einmal da."""
    out: dict[str, set[str]] = {k: set() for k in NAMED}
    for t in doc["tables"]:
        for r in t["rows"]:
            if r["Description"] in NAMED:
                peer = r["Source"] if r.get("_dir") == "in" else r["Destination"]
                out[r["Description"]].add(peer)
    return out


def _bare(doc: dict) -> None:
    peers = _peers(doc)
    for key, named in NAMED.items():
        assert BARE[key] in peers[key], (key, peers[key])
        assert named == BARE[key] or named not in peers[key], (key, peers[key])


def test_namen_der_gegenstellen_nur_mit_einstellung(client):
    tid, app = _env(client)
    url = f"/applications/{app['id']}/doc"
    # Standard: aus - die Doku nennt nur die Adressen (wie bisher)
    assert client.get("/tenants", headers=ADMIN).json()[0]["appdoc_foreign_names"] is False
    _bare(client.get(url).json())

    r = client.patch(f"/tenants/{tid}", json={"appdoc_foreign_names": True}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["appdoc_foreign_names"] is True
    doc = client.get(url).json()
    peers = _peers(doc)
    assert all(named in peers[key] for key, named in NAMED.items()), peers
    assert "web01.example.ch (10.20.0.5)" in peers["to-db"]  # Doku von db01: web01 aus dem IPAM
    # auch im Treffer (Gegenstelle) und unveraendert bei Gegenstellen mit eigenem Namen
    hits = {h["counterpart"] for h in doc["hits"]}
    assert "partner01.example.ch (10.30.0.8)" in hits
    # Netz der Regel ueber das Firewall-Objekt (Bezug); die Seite der Applikation bleibt
    rows = [r for t in doc["tables"] for r in t["rows"] if r["Description"] == "web-in"]
    assert {r["Source"] for r in rows} == {"N_clients (10.9.0.0/16)"}
    assert any(r["Destination"].startswith("H_web01 (") for r in rows)

    # Word-Export (Confluence) ebenso
    body = client.get(f"/applications/{app['id']}/export", params={"format": "docx"}).content
    xml = zipfile.ZipFile(io.BytesIO(body)).read("word/document.xml").decode()
    assert "partner01.example.ch (10.30.0.8)" in xml and "H_tool01 (10.50.0.3)" in xml

    # Doku je System (Server 10.20.0.5): gleich
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    assert "partner01.example.ch (10.30.0.8)" in {
        r["Destination"] for t in system["tables"] for r in t["rows"]
    }

    # wieder aus: nur die Adressen
    client.patch(f"/tenants/{tid}", json={"appdoc_foreign_names": False}, headers=ADMIN)
    _bare(client.get(url).json())


def test_kunden_benutzer_sehen_nur_adressen(client):
    tid, app = _env(client)
    body = {"tenant_id": tid, "name": "Kunde A",
            "selectors": [{"kind": "aci_tenant", "value": "TE_ka"}]}  # fmt: skip
    customer = client.post("/customers", json=body, headers=OP).json()["id"]
    client.patch(f"/tenants/{tid}", json={"appdoc_foreign_names": True}, headers=ADMIN)
    user = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(customer)}
    r = client.get(f"/applications/{app['id']}/doc", headers=user)
    assert r.status_code == 200, r.text
    peers = _peers(r.json())
    assert peers["to-partner"] == {"10.30.0.8"}  # die Regel gehoert zum Kunden, ohne Namen
    for key, named in NAMED.items():
        assert named == BARE[key] or named not in peers[key], (key, peers[key])
    assert "partner01.example.ch" not in r.text and "H_tool01 (" not in r.text
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}, headers=user)
    assert system.status_code == 200, system.text
    assert "10.30.0.8" in system.text and "partner01.example.ch" not in system.text
    # Betreuer sehen die Namen
    assert "partner01.example.ch (10.30.0.8)" in client.get(f"/applications/{app['id']}/doc").text


def test_nur_admin_stellt_ein_mit_audit(client, session_factory):
    tid, _app_ = _env(client)
    url = f"/tenants/{tid}"
    body = {"appdoc_foreign_names": True}
    assert client.patch(url, json=body, headers=OP).status_code == 403
    assert client.patch(url, json=body, headers={"X-Dev-Roles": "viewer"}).status_code == 403
    customer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": "1"}
    assert client.patch(url, json=body, headers=customer).status_code == 403
    assert client.get("/tenants", headers=ADMIN).json()[0]["appdoc_foreign_names"] is False
    # andere Felder lassen die Einstellung stehen
    assert client.patch(url, json=body, headers=ADMIN).status_code == 200
    assert client.patch(url, json={"code": "ka"}, headers=ADMIN).json()["appdoc_foreign_names"]
    s = session_factory()
    rows = [r for r in s.query(AuditLog).order_by(AuditLog.id) if r.action == "tenant.updated"]
    assert audit.decode_detail(rows[0])["appdoc_foreign_names"] is True
    s.close()


def _epg_id(client, tid, name) -> int:
    """ID einer EPG ausserhalb des Webshops - ueber die Doku einer zweiten Applikation."""
    body = {
        "name": f"Hilf {name}",
        "members": [{"kind": "epg", "ref": f"uni/tn-TE_kx/ap-AP_x/epg-{name}"}],
    }
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    doc = client.get(f"/applications/{r.json()['id']}/doc").json()
    return next(e["id"] for e in doc["epgs"] if e["name"] == name)


def test_epg_der_applikation_im_detail(client):
    tid, app = _env(client)
    doc = client.get(f"/applications/{app['id']}/doc").json()
    web = next(e for e in doc["epgs"] if e["label"] == "TE_ka/AP_007/EG_web01")
    r = client.get(f"/applications/{app['id']}/epgs/{web['id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["kind"], d["aci_tenant"], d["ap"], d["vrf"]) == ("epg", "TE_ka", "AP_007", "VRF1")
    assert [s["ip"] for s in d["subnets"]] == ["10.20.0.1/24"]
    assert [v["name"] for v in d["vms"]] == ["web01-007-ka"]
    # EPG ausserhalb der Applikation (anderer ACI-Tenant) bzw. unbekannt: nicht auffindbar
    tool = _epg_id(client, tid, "EG_tool")
    assert client.get(f"/applications/{app['id']}/epgs/{tool}").status_code == 404
    assert client.get(f"/applications/{app['id']}/epgs/999999").status_code == 404
    # Kunden-Benutzer: nur EPGs ihres Ausschnitts
    a = client.post("/customers", json={"tenant_id": tid, "name": "Kunde A", "selectors": [
        {"kind": "aci_tenant", "value": "TE_ka"}]}, headers=OP).json()["id"]  # fmt: skip
    b = client.post("/customers", json={"tenant_id": tid, "name": "Kunde X", "selectors": [
        {"kind": "aci_tenant", "value": "TE_kx"}]}, headers=OP).json()["id"]  # fmt: skip

    def user(c):
        return {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(c)}

    url = f"/applications/{app['id']}/epgs/{web['id']}"
    own = client.get(url, headers=user(a))
    assert own.status_code == 200 and own.json()["label"] == "AP_007/EG_web01"
    assert client.get(url, headers=user(b)).status_code == 404  # Webshop gehoert nicht zu X
