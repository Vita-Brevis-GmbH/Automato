"""EPG einer VM als Mitglied (0.126.1, Anwender 2026-11-03: im Netzwerkplan nur interne
Verbindungen, „die EPG wird auch nicht ausgefüllt“ – auf den Vorschlag „So umsetzen“): die EPG der
VM steht als „EPG der VM“ in der Doku und im Netzwerkplan; Firewall-Regeln mit dem Netz dieser
EPG gelten fuer die VM spezifisch (wie fuer die EPG) - ohne die uebrigen VMs der EPG und ohne
Regeln, die nur andere Hosts der EPG nennen.

Umgebung aus `test_vmware` (web01-007-ka 10.20.0.5 an der Portgruppe der EPG EG_web01 mit dem
Subnetz 10.20.0.1/24), dazu eine zweite Firewall mit Regeln ueber Netze.
"""

from app.models import Application
from app.store import app_paths
from test_app_vm_members import _create
from test_vmware import _deliver, _seed, _system

FW2 = {
    "status": "completed",
    "rules": [
        # ausgehend ueber das Netz der EPG: fuer die VM bisher „breite Quelle“ (nicht gezeigt)
        {"source_platform": "fortigate", "external_id": "11", "name": "web-net-out",
         "action": "allow", "sources": ["10.20.0.0/24"], "destinations": ["10.9.0.0/16"],
         "services": ["tcp/8443"], "source_refs": ["N_web"], "destination_refs": ["N_clients"],
         "family": "v4"},
        # ausgehend ueber ein breites Netz: bleibt unspezifisch
        {"source_platform": "fortigate", "external_id": "12", "name": "wide-out",
         "action": "allow", "sources": ["10.0.0.0/8"], "destinations": ["10.9.0.0/16"],
         "services": ["tcp/22"], "family": "v4"},
        # ein anderer Host im Netz der EPG: gehoert nicht zur VM
        {"source_platform": "fortigate", "external_id": "13", "name": "other-host",
         "action": "allow", "sources": ["10.9.0.0/16"], "destinations": ["10.20.0.9"],
         "services": ["tcp/80"], "family": "v4"},
    ],
    "objects": [
        {"name": "N_web", "category": "address", "family": "v4", "values": ["10.20.0.0/24"]},
        {"name": "N_clients", "category": "address", "family": "v4", "values": ["10.9.0.0/16"]},
    ],
}  # fmt: skip


def _setup(client) -> tuple[int, dict]:
    ids = _seed(client)
    tid = ids["tid"]
    _deliver(client, tid, _system(client, tid, "fw-02", "fortigate", base_url="https://fw2"),
             "fortigate", FW2)  # fmt: skip
    return tid, _create(client, tid, "Nur VM", [{"kind": "vm", "ref": "web01-007-ka"}])


def test_epg_der_vm_in_der_doku(client):
    tid, app = _setup(client)
    doc = client.get(f"/applications/{app['id']}/doc").json()
    (vm,) = doc["systems"]
    assert vm["epgs"] == ["TE_ka/AP_007/EG_web01"]
    (epg,) = doc["epgs"]
    assert (epg["label"], epg["of_vm"], epg["vm_count"]) == ("TE_ka/AP_007/EG_web01", True, 1)
    assert epg["member"] == "EPG der VM web01-007-ka"
    assert epg["subnets"] == ["10.20.0.1/24"]
    names = {r["name"] for r in doc["rules"]}
    # Netz der EPG: spezifisch wie bei der EPG, mit Bezug; breites Netz und andere Hosts nicht
    assert "web-net-out" in names
    assert "wide-out" not in names and "other-host" not in names
    rid = next(r["id"] for r in doc["rules"] if r["name"] == "web-net-out")
    (hit,) = (h for h in doc["hits"] if h["rule_id"] == rid)
    assert (hit["system"], hit["scope"], hit["direction"]) == (vm["key"], "specific", "out")
    assert hit["via"] == "N_web · Netz der EPG TE_ka/AP_007/EG_web01"
    # keine Zeilen der EPG selbst (die ganze EPG ist kein Mitglied)
    assert not any(h["system"] == epg["key"] for h in doc["hits"])

    en = client.get(f"/applications/{app['id']}/doc", headers={"Accept-Language": "en"}).json()
    assert en["epgs"][0]["member"] == "EPG of the VM web01-007-ka"


def test_netzwerkplan_mit_der_epg_der_vm(client, session_factory):
    tid, app = _setup(client)
    out = client.get(f"/applications/{app['id']}/connections").json()
    # die Verbindung ueber das Netz der EPG ist da, die VM steht im Plan in ihrer EPG
    conns = {(c["src"]["label"], c["service"]) for c in out["connections"]}
    assert ("web01-007-ka", "tcp/8443") in conns, conns
    assert [e["label"] for e in out["members"]["epgs"]] == ["TE_ka/AP_007/EG_web01"]
    own = [n for n in out["plan"]["nodes"] if n["role"] == "own"]
    assert {n["id"] for n in own} == {"epg:TE_ka/AP_007/EG_web01"}
    assert own[0]["lines"] == ["web01-007-ka"]  # nur die VM der Applikation
    # dieselbe Liste fuer die Befunde
    s = session_factory()
    built = app_paths.build(s, s.get(Application, app["id"]))
    assert any(c["service"] == "tcp/8443" for c in built["connections"])
    s.close()
