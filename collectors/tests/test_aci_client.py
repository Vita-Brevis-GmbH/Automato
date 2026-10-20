"""APIC-REST-Client + ACI-Lauf mit Geraete-Zugangsdaten (ADR 0009), ohne Lab.

MockTransport spielt den APIC: aaaLogin -> Token/Cookie, Tenant-Liste, je Tenant der
MO-Baum (rsp-subtree=full) aus dem Beispiel-Export, aaaLogout.
"""

import httpx
import pytest

from collectors.cisco_aci.client import ApicClient, parse_scope
from collectors.cisco_aci.tasks import fetch_mo_export
from collectors.common.dto import Target
from collectors.config import Settings

APIC_PW = "pw"  # noqa: S105 - Testwert


def _apic(calls: list, *, login_status: int = 200) -> httpx.Client:
    tree = fetch_mo_export()["imdata"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path, dict(request.url.params)))
        if path == "/api/aaaLogin.json":
            if login_status != 200:
                return httpx.Response(login_status, json={"imdata": []})
            body = {"imdata": [{"aaaLogin": {"attributes": {"token": "tok-1"}}}]}
            return httpx.Response(200, json=body)
        if path == "/api/aaaLogout.json":
            return httpx.Response(200, json={"imdata": []})
        assert request.headers.get("cookie") == "APIC-cookie=tok-1"  # Sitzung genutzt
        if path == "/api/class/fvTenant.json":
            names = ["common", "infra", "mgmt", "TE_share"]
            rows = [{"fvTenant": {"attributes": {"name": n}}} for n in names]
            return httpx.Response(200, json={"imdata": rows})
        if path == "/api/mo/uni/tn-TE_share.json":
            assert request.url.params.get("rsp-subtree") == "full"
            return httpx.Response(200, json={"imdata": tree})
        if path.startswith("/api/mo/uni/tn-"):
            return httpx.Response(200, json={"imdata": []})
        return httpx.Response(404, json={"imdata": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scope_parsen():
    assert parse_scope(" TE_a, ,TE_b ") == ["TE_a", "TE_b"] and parse_scope("") == []


def test_export_alle_tenants_ohne_infra_mgmt_mit_logout():
    calls: list = []
    with ApicClient("https://apic", "ro", APIC_PW, client=_apic(calls)) as apic:
        export = apic.export()
    read = [p for _, p, _ in calls if p.startswith("/api/mo/")]
    assert read == ["/api/mo/uni/tn-TE_share.json", "/api/mo/uni/tn-common.json"]
    assert export["imdata"] and "fvTenant" in export["imdata"][0]
    assert calls[0][1] == "/api/aaaLogin.json" and calls[-1][1] == "/api/aaaLogout.json"


def test_export_nur_gewaehlte_tenants_und_unbekannter_tenant():
    calls: list = []
    with ApicClient("https://apic", "ro", APIC_PW, client=_apic(calls)) as apic:
        apic.export("TE_share")
        with pytest.raises(RuntimeError, match="TE_fehlt"):
            apic.export("TE_share, TE_fehlt")
    assert [p for _, p, _ in calls if p.startswith("/api/mo/")] == ["/api/mo/uni/tn-TE_share.json"]


def test_login_fehler():
    with pytest.raises(httpx.HTTPStatusError):
        ApicClient("https://apic", "ro", "falsch", client=_apic([], login_status=401)).login()


def _target(**kw) -> Target:
    base = {
        "source_system_id": 3,
        "name": "apic-01",
        "platform": "cisco_aci",
        "base_url": "https://apic",
        "verify_tls": False,
        "read_scope": "TE_share",
        "username": "ro",
        "password": APIC_PW,
    }
    return Target(**(base | kw))


def _patch_client(monkeypatch, calls: list, seen: dict):
    import collectors.cisco_aci.tasks as t

    def factory(url, username, password, verify_tls=True):
        seen.update(url=url, username=username, verify_tls=verify_tls)
        return ApicClient(url, username, password, client=_apic(calls))

    monkeypatch.setattr(t, "ApicClient", factory)
    return t


def test_lauf_mit_zugangsdaten_vom_orchestrator(monkeypatch):
    calls: list = []
    seen: dict = {}
    t = _patch_client(monkeypatch, calls, seen)
    sent = {}
    monkeypatch.setattr(
        t, "submit_result", lambda run_id, result, via_agent=False: sent.update(via=via_agent)
    )
    result = t.collect_once("r1", _target(), via_agent=True)
    assert result.status == "completed" and result.rules and result.epgs
    assert seen == {"url": "https://apic", "username": "ro", "verify_tls": False}
    assert sent == {"via": True}


def test_agent_ohne_apic_zugangsdaten_liest_keine_beispieldaten(monkeypatch):
    import collectors.cisco_aci.tasks as t

    monkeypatch.setattr(t, "get_settings", lambda: Settings(agent_token="a"))  # noqa: S106
    monkeypatch.setattr(t, "submit_result", lambda run_id, result, via_agent=False: None)
    result = t.collect_once("r1", _target(password=None), via_agent=True)
    assert result.status == "failed" and "apic-01" in result.message and not result.rules


def test_check_mit_zugangsdaten(monkeypatch):
    calls: list = []
    t = _patch_client(monkeypatch, calls, {})
    ok, msg = t.check(_target())
    assert ok is True and "4 Tenants" in msg
    monkeypatch.setattr(t, "get_settings", lambda: Settings(aci_url=""))
    ok, msg = t.check()
    assert ok is False and "Creds" in msg


# Echte APIC-Antwort (rsp-subtree=full): dn nur am Tenant, Kinder nur mit rn
APIC_TREE = {
    "imdata": [
        {
            "fvTenant": {
                "attributes": {"name": "TE_x", "dn": "uni/tn-TE_x"},
                "children": [
                    {
                        "fvAp": {
                            "attributes": {"name": "AP_007", "rn": "ap-AP_007"},
                            "children": [
                                {
                                    "fvAEPg": {
                                        "attributes": {"name": "EG_app", "rn": "epg-EG_app"},
                                        "children": [
                                            {
                                                "fvRsCons": {
                                                    "attributes": {"tnVzBrCPName": "CT_web"}
                                                }
                                            },
                                            {
                                                "fvCEp": {
                                                    "attributes": {"mac": "AA", "ip": "10.7.0.5"},
                                                    "children": [
                                                        {
                                                            "fvIp": {
                                                                "attributes": {"addr": "10.7.0.5"}
                                                            }
                                                        }
                                                    ],
                                                }
                                            },
                                            {
                                                "fvCEp": {
                                                    "attributes": {"mac": "BB", "ip": "10.7.0.5"}
                                                }
                                            },
                                        ],
                                    }
                                },
                                {
                                    "fvAEPg": {
                                        "attributes": {
                                            "name": "EG_web"
                                        },  # Export: weder dn noch rn
                                        "children": [
                                            {"fvRsProv": {"attributes": {"tnVzBrCPName": "CT_web"}}}
                                        ],
                                    }
                                },
                            ],
                        }
                    },
                    {
                        "vzBrCP": {
                            "attributes": {"name": "CT_web", "rn": "brc-CT_web", "scope": "tenant"},
                            "children": [
                                {
                                    "vzSubj": {
                                        "attributes": {"name": "SJ_web", "rn": "subj-SJ_web"},
                                        "children": [
                                            {
                                                "vzRsSubjFiltAtt": {
                                                    "attributes": {"tnVzFilterName": "FT_web"}
                                                }
                                            }
                                        ],
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "vzFilter": {
                            "attributes": {"name": "FT_web", "rn": "flt-FT_web"},
                            "children": [
                                {
                                    "vzEntry": {
                                        "attributes": {
                                            "name": "a",
                                            "prot": "tcp",
                                            "dFromPort": "https",
                                            "dToPort": "https",
                                        }
                                    }
                                },
                                {
                                    "vzEntry": {
                                        "attributes": {
                                            "name": "b",
                                            "prot": "udp",
                                            "dFromPort": "dns",
                                            "dToPort": "dns",
                                        }
                                    }
                                },
                                {
                                    "vzEntry": {
                                        "attributes": {
                                            "name": "c",
                                            "prot": "tcp",
                                            "dFromPort": "8000",
                                            "dToPort": "8080",
                                        }
                                    }
                                },
                                {
                                    "vzEntry": {
                                        "attributes": {
                                            "name": "d",
                                            "etherT": "ip",
                                            "prot": "unspecified",
                                        }
                                    }
                                },
                                {
                                    "vzEntry": {
                                        "attributes": {
                                            "name": "e",
                                            "etherT": "arp",
                                            "prot": "unspecified",
                                        }
                                    }
                                },
                            ],
                        }
                    },
                ],
            }
        }
    ]
}


def test_parser_echte_apic_antwort_ohne_dn_an_kindern():
    from collectors.cisco_aci.mo import parse_epgs, parse_mo_export

    epgs = {e["name"]: e for e in parse_epgs(APIC_TREE)}
    assert epgs["EG_app"]["ap"] == "AP_007" and epgs["EG_web"]["ap"] == "AP_007"
    assert epgs["EG_app"]["endpoints"] == [{"ip": "10.7.0.5", "mac": "AA"}]  # je IP einmal

    (contract,) = parse_mo_export(APIC_TREE)
    assert contract["dn"] == "uni/tn-TE_x/brc-CT_web"
    assert contract["consumer_epgs"] == ["EG_app"] and contract["provider_epgs"] == ["EG_web"]
    assert contract["subjects"][0]["services"] == [
        "tcp/443",
        "udp/53",
        "tcp/8000-8080",
        "ip/any",
        "arp/any",
    ]
