"""Prüfung B „Regeln“ (Fable, Phase 1): Abweichungen der Normalisierung vom echten Gerät.

Wie `orchestrator/tests/test_fable_regeln.py`: jeder Test verlangt das echte Verhalten und
schlägt heute fehl (`xfail(strict=True)`). Beispiele anonymisiert.
"""

import pytest
from test_normalize import _epg, _filter, _mo

from collectors.cisco_aci.mo import parse_mo_export
from collectors.cisco_aci.normalize import normalize_contract
from collectors.fortigate import normalize as norm


@pytest.mark.xfail(
    strict=True,
    reason="Fable B-9: action ipsec wird als deny normalisiert (fortigate/normalize.py:337)",
)
def test_b9_policy_mit_action_ipsec_erlaubt():
    """`set action ipsec` (Policy-basiertes IPsec-VPN): die Policy lässt den Verkehr in den
    Tunnel - sie sperrt nicht."""
    policy = {"policyid": 7, "name": "lan-to-partner", "action": "ipsec", "status": "enable",
              "srcintf": [{"name": "lan"}], "dstintf": [{"name": "wan"}],
              "srcaddr": [{"name": "all"}], "dstaddr": [{"name": "all"}],
              "service": [{"name": "ALL"}], "vpntunnel": "VPN_partner"}  # fmt: skip
    (rule,) = norm.normalize([policy], [], [], [])
    assert rule.action == "allow"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-10: action deny am Filter eines Subjects wird nicht gelesen – der "
        "Contract gilt als erlaubend (cisco_aci/mo.py:303, normalize.py:64)"
    ),
)
def test_b10_aci_filter_mit_action_deny_ist_eine_sperre():
    """Filter eines Subjects mit `action="deny"` (vzRsSubjFiltAtt): der Contract sperrt den
    Dienst - er erlaubt ihn nicht."""
    subj = _mo("vzSubj", "SJ_1", [_mo("vzRsSubjFiltAtt", tnVzFilterName="FT_ssh", action="deny",
                                      priorityOverride="default")])  # fmt: skip
    export = [_mo("fvTenant", "TE_a", [
        _mo("vzBrCP", "CT_block", [subj], scope="context"),
        _filter("FT_ssh", "22"),
        _mo("fvAp", "AP_1", [_epg("EG_web", cons=["CT_block"]), _epg("EG_db", prov=["CT_block"])]),
    ])]  # fmt: skip
    rules = [r for c in parse_mo_export(export) for r in normalize_contract(c)]
    assert [(r.name, r.action) for r in rules] == [("CT_block:SJ_1", "deny")]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-11: Adressgruppen werden nur drei Stufen tief aufgelöst "
        "(fortigate/normalize.py:104)"
    ),
)
def test_b11_adressgruppen_tiefer_als_drei_stufen():
    """Verschachtelte Adressgruppen löst die FortiGate ganz auf - auch in der vierten Stufe."""
    amap = {
        "G1": {"name": "G1", "member": [{"name": "G2"}]},
        "G2": {"name": "G2", "member": [{"name": "G3"}]},
        "G3": {"name": "G3", "member": [{"name": "G4"}]},
        "G4": {"name": "G4", "member": [{"name": "H_web01"}]},
        "H_web01": {"name": "H_web01", "type": "ipmask", "subnet": "10.30.0.10 255.255.255.255"},
    }
    assert norm.resolve_address("G1", amap) == ["10.30.0.10"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fable B-13: Umleitung HTTP -> HTTPS am Load Balancer (http_to_https) wird nicht "
        "gelesen - Port 80 gilt als Weiterleitung zu den Servern des Pools (avi/normalize.py:196)"
    ),
)
def test_b13_umleitung_http_zu_https_geht_nicht_zum_server():
    """Application Profile mit `http_to_https`: auf dem Port ohne TLS antwortet der Load
    Balancer selbst mit der Umleitung - kein Verkehr zu den Servern des Pools."""
    from collectors.avi import tasks as avi_tasks
    from collectors.avi.normalize import normalize

    data = avi_tasks._sample()
    for tdata in data["tenants"].values():
        for profile in tdata.get("applicationprofile") or []:
            profile.setdefault("http_profile", {})["http_to_https"] = True
    plain = [n for n in normalize(data) if n.ext_port == "80" and not (n.attrs or {}).get("ssl")]
    assert plain, "Beispieldaten ohne Virtual Service auf Port 80"
    assert all(not n.targets for n in plain), [(n.name, n.targets) for n in plain]
