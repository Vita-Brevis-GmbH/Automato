"""0.78.2 (Rueckmeldung des Anwenders): Kategorien der Applikations-Doku Richtung L3Out.

- Die Firewall ist fuehrend: ein Contract zu einer externen EPG (L3Out) steht nicht separat in
  den Kategorien, solange er die Dienste der Firewall-Regeln derselben Kategorie und Richtung
  abdeckt. Oeffnet er weniger, steht er da (mit den fehlenden Diensten) und ist ein Befund
  `app_contract_narrower`.
- Kategorie 10 („Unspezifische Zugriffe auf die Applikation“) zeigt nur Zugriffe auf die
  Applikation, nicht ihre Zugriffe auf andere Systeme.
"""

import copy

from system_doc import system_doc
from test_vmware import ACI, FW, WEB, _deliver, _seed

OP = {"X-Dev-Roles": "operator"}
INET = "uni/tn-TE_ka/out-L3O_inet/instP-EN_inet"


def _aci() -> dict:
    aci = copy.deepcopy(ACI)
    aci["epgs"].append(
        {"name": "EN_inet", "kind": "external", "dn": INET, "tenant": "TE_ka",
         "l3out": "L3O_inet", "vrf": "VRF1", "subnets": ["0.0.0.0/0"]},  # noqa: S104
    )  # fmt: skip
    aci["rules"].append(
        {"source_platform": "cisco_aci", "external_id": "uni/tn-TE_ka/brc-CT_inet/SJ",
         "name": "CT_inet:SJ", "action": "allow", "sources": [f"epg:{WEB}"],
         "destinations": [f"epg:{INET}"], "services": ["tcp/443"], "aci_scope": "context",
         "raw": {"tenant": "TE_ka", "kind": "contract", "dn": "uni/tn-TE_ka/brc-CT_inet"}},
    )  # fmt: skip
    return aci


def _fw(services: list[str]) -> dict:
    fw = copy.deepcopy(FW)
    fw["rules"] += [
        {"source_platform": "fortigate", "external_id": "20", "name": "web-inet",
         "action": "allow", "sources": ["10.20.0.5"], "destinations": ["all"],
         "services": services, "source_refs": ["H_web01"], "destination_refs": ["all"],
         "dst_zone": "inet", "family": "v4"},
        # Zugriff des Servers auf ein anderes System ohne Zone: nicht in Kategorie 10
        {"source_platform": "fortigate", "external_id": "21", "name": "web-tool",
         "action": "allow", "sources": ["10.20.0.5"], "destinations": ["10.50.0.9"],
         "services": ["tcp/9100"], "source_refs": ["H_web01"], "destination_refs": ["H_tool"],
         "family": "v4"},
    ]  # fmt: skip
    fw["objects"].append(
        {"name": "H_tool", "category": "address", "family": "v4", "values": ["10.50.0.9"]}
    )
    return fw


def _rows(doc: dict, index: int) -> dict[str, dict]:
    return {r["Description"]: r for t in doc["tables"] if t["index"] == index for r in t["rows"]}


def _narrower(client, tid) -> list[dict]:
    r = client.get("/findings", params={"tenant_id": tid, "type": "app_contract_narrower"})
    assert r.status_code == 200, r.text
    return r.json()


def test_l3out_contract_nur_wenn_enger_als_die_firewall(client):
    ids = _seed(client)
    tid = ids["tid"]
    _deliver(client, tid, ids["apic"], "cisco_aci", _aci())
    _deliver(client, tid, ids["fw"], "fortigate", _fw(["tcp/443"]))
    body = {"name": "Webshop", "members": [{"kind": "ap", "ref": "TE_ka/AP_007"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()

    doc = client.get(f"/applications/{app['id']}/doc").json()
    internet = _rows(doc, 1)
    # Firewall-Regel steht da; der Contract deckt tcp/443 ab -> nicht separat
    assert "web-inet" in internet and not any(k.startswith("CT_inet") for k in internet)
    assert doc["narrower"] == [] and _narrower(client, tid) == []
    # als Treffer bleibt der Contract (Regeln & Contracts, Verbindungen)
    assert any(h["counterpart"].startswith("TE_ka/L3O_inet") or "EN_inet" in h["counterpart"]
               for h in doc["hits"] if h["table"] == 1)  # fmt: skip
    # Kategorie 10: der Zugriff des Servers auf das Tool nicht (nur Zugriffe auf die Applikation)
    assert "web-tool" not in _rows(doc, 10)
    assert any(h["counterpart"] == "10.50.0.9" for h in doc["hits"])

    # Firewall oeffnet zusaetzlich tcp/8443 - der Contract nicht: er steht da, mit Befund
    _deliver(client, tid, ids["fw"], "fortigate", _fw(["tcp/443", "tcp/8443"]))
    doc = client.get(f"/applications/{app['id']}/doc").json()
    internet = _rows(doc, 1)
    (contract,) = (r for k, r in internet.items() if k.startswith("CT_inet"))
    assert "öffnet weniger als die Firewall-Regeln, es fehlt tcp/8443" in contract["Description"]
    (entry,) = doc["narrower"]
    assert entry["missing"] == ["tcp/8443"] and entry["categories"] == [
        "1 Internet Zugriff (ausgehend)"]  # fmt: skip
    found = _narrower(client, tid)
    assert len(found) == 1, found
    f = found[0]
    assert f["subject_id"] == "TE_ka/CT_inet:SJ · Applikation Webshop"
    assert f["severity"] == "warning" and f["detail"]["missing"] == ["tcp/8443"]
    assert [x["name"] for x in f["detail"]["firewall_rules"]] == ["web-inet"]
    assert "es fehlt tcp/8443" in f["message"]
    # Doku je System: gleiche Regel
    system = system_doc(client, "10.20.0.5", tid)
    assert any(k.startswith("CT_inet") for k in _rows(system, 1))
    # Doku der Applikation listet den Befund
    assert any(x["type"] == "app_contract_narrower" for x in doc["findings"])

    # wieder abgedeckt: Befund weg
    _deliver(client, tid, ids["fw"], "fortigate", _fw(["tcp/443"]))
    assert _narrower(client, tid) == []
