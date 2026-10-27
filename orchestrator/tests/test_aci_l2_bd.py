# ruff: noqa: S104 - 0.0.0.0/0 sind Subnetze, keine Bind-Adressen
"""Systeme im Fabric ohne BD-Subnetz (0.83.1, Anwender: „vorne 20x L3O_vpn, hinten Contracts, die
nichts mit der EPG zu tun haben“).

Netzwerk-zentrisch: EG_app liegt in einer BD ohne Subnetz (Gateway auf der Firewall, das ACI
lernt dort keine IPs); vm-app haengt an ihrer Portgruppe. Die externe EPG EN_vpn des L3O_vpn
erfasst mit 0.0.0.0/0 jede Adresse und hat einen Contract zu EG_other. Die VM gehoert nach der
Portgruppe zu EG_app - die Contracts von EN_vpn gelten nicht fuer sie. Ein Server ausserhalb des
Fabrics bekommt sie, aber in der Kategorie seines L3Outs (Zugriff L3O_vpn), nicht „ACI intern“.
"""

from app.store.topology import aci_vrf
from test_vmware import ADMIN, _deliver, _system

OP = {"X-Dev-Roles": "operator"}
TE = "uni/tn-TE"
APP = f"{TE}/ap-AP/epg-EG_app"
OTHER = f"{TE}/ap-AP2/epg-EG_other"
VPN = f"{TE}/out-L3O_vpn/instP-EN_vpn"

ACI = {
    "status": "completed",
    "epgs": [
        {"name": "EG_app", "dn": APP, "tenant": "TE", "ap": "AP", "bd": "BD_l2", "vrf": "VRF1",
         "vrf_tenant": "TE"},
        {"name": "EG_other", "dn": OTHER, "tenant": "TE", "ap": "AP2", "bd": "BD_o", "vrf": "VRF1",
         "vrf_tenant": "TE", "bd_subnets": ["10.9.0.1/24"]},
        {"name": "EN_vpn", "kind": "external", "dn": VPN, "tenant": "TE", "l3out": "L3O_vpn",
         "vrf": "VRF1", "subnets": ["0.0.0.0/0"]},
    ],
    "rules": [
        {"source_platform": "cisco_aci", "external_id": f"{TE}/brc-CT_vpn_other/SJ_1",
         "name": "CT_vpn_other", "action": "allow", "sources": [f"epg:{VPN}"],
         "destinations": [f"epg:{OTHER}"], "services": ["tcp/443"], "aci_scope": "context",
         "raw": {"name": "CT_vpn_other", "kind": "contract", "scope": "context"}},
    ],
}  # fmt: skip
VCENTER = {
    "status": "completed",
    "virtual": {
        "vms": [{"name": "vm-app", "power_state": "poweredOn",
                 "nics": [{"name": "Network adapter 1", "portgroup": "TE|AP|EG_app",
                           "switch": "DVS-A", "vlan": 1200, "ips": ["10.1.0.5"]}]}],
        "portgroups": [{"name": "TE|AP|EG_app", "kind": "dvs", "switch": "DVS-A", "vlan": 1200}],
    },
}  # fmt: skip
# Firewall-Regel, damit der Server ausserhalb des Fabrics als System bekannt ist
FW = {
    "status": "completed",
    "rules": [
        {"source_platform": "fortigate", "external_id": "1", "name": "srv-out", "action": "allow",
         "sources": ["192.0.2.10"], "destinations": ["10.9.0.20"], "services": ["tcp/443"],
         "vdom": "root", "family": "v4", "raw": {"policyid": 1, "status": "enable"}},
    ],
}  # fmt: skip


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    vc = _system(client, tid, "vc-01", "vmware", base_url="https://vc", username="ro", password="x")
    _deliver(client, tid, vc, "vmware", VCENTER)
    apic = _system(client, tid, "apic-01", "cisco_aci", base_url="https://apic")
    _deliver(client, tid, apic, "cisco_aci", ACI)
    fw = _system(client, tid, "fw-01", "fortigate", base_url="https://fw")
    _deliver(client, tid, fw, "fortigate", FW)
    return tid


def _rows(doc: dict) -> dict[str, tuple[int, dict]]:
    return {r["Description"]: (t["index"], r) for t in doc["tables"] for r in t["rows"]}


def test_vm_der_portgruppe_ohne_contracts_der_externen_epg(client):
    tid = _seed(client)
    body = {"name": "App", "members": [{"kind": "epg", "ref": APP}]}
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    doc = client.get(f"/applications/{r.json()['id']}/doc").json()
    assert [x["name"] for x in doc["systems"]] == ["vm-app"]
    assert "CT_vpn_other" not in _rows(doc)
    rules = {x["id"]: x["name"] for x in doc["rules"]}
    assert "CT_vpn_other" not in {rules.get(h["rule_id"]) for h in doc["hits"]}

    # Regel-Liste nach der IP: die externe EPG ordnet die VM nicht zu (Portgruppe)
    params = {"tenant_id": tid, "q": "10.1.0.5", "domain": "aci_contract"}
    rel = {x["name"]: x["relevance"] for x in client.get("/rules", params=params).json()}
    assert "CT_vpn_other" not in rel or rel["CT_vpn_other"]["state"] == "impossible"


def test_vrf_aus_common_fuer_alte_laeufe():
    """BD-Subnetze aus Laeufen vor 0.83.1 tragen den Tenant der BD auch fuer eine VRF aus
    `common` - gilt die VRF dort, zaehlt sie (Start im Fabric, Ort der IP)."""
    known = {(1, "common:VRF1"), (1, "TE_a:VRF_own")}
    assert aci_vrf("TE_a:VRF1", 1, known) == "common:VRF1"
    assert aci_vrf("TE_a:VRF_own", 1, known) == "TE_a:VRF_own"
    assert aci_vrf("TE_a:VRF9", 1, known) == "TE_a:VRF9"  # unbekannt: bleibt
    assert aci_vrf("TE_a:VRF1", 2, known) == "TE_a:VRF1"  # anderer APIC


def test_server_ausserhalb_in_der_kategorie_seines_l3outs(client):
    tid = _seed(client)
    systems = client.get("/appdoc/systems", params={"tenant_id": tid}).json()
    name = next(s["name"] for s in systems if s["ip"] == "192.0.2.10")
    doc = client.get(f"/appdoc/systems/{name}", params={"tenant_id": tid}).json()
    idx, row = _rows(doc)["CT_vpn_other"]
    assert idx == 5  # Zugriff L3O_vpn, nicht 8 „innerhalb des Security Tenant“
    assert row["Bezug"] == "externe EPG L3O_vpn|EN_vpn (0.0.0.0/0)"
