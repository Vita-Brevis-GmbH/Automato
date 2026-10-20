"""Meldung bei abgelehnter Geraete-Anmeldung (401/403): Ursache + Hinweis, nie das Passwort."""

import httpx

from collectors.common import diagnose
from collectors.common.dto import Target
from collectors.config import Settings

SECRET = "Geh€im "  # noqa: S105 - Testwert: Nicht-ASCII + Leerzeichen am Ende


def _error(url: str, status: int, **kwargs) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    response = httpx.Response(status, request=request, **kwargs)
    return httpx.HTTPStatusError("abgelehnt", request=request, response=response)


def _target(platform: str, username: str) -> Target:
    return Target(name="dev", platform=platform, username=username, password=SECRET)


def test_apic_fehlertext_und_hinweis_lokal_mit_benutzer(monkeypatch):
    monkeypatch.setattr(diagnose, "get_settings", lambda: Settings())
    body = {
        "imdata": [
            {"error": {"attributes": {"code": "401", "text": "FAILED local authentication"}}}
        ]
    }
    exc = _error("https://apic.example.ch/api/aaaLogin.json", 401, json=body)
    msg = diagnose.device_error(exc, "cisco_aci", _target("cisco_aci", "DOM\\svc"), local=True)
    assert msg.startswith("Anmeldung abgelehnt (HTTP 401 https://apic.example.ch/api/aaaLogin")
    assert "Geraet meldet: FAILED local authentication" in msg
    assert "Benutzer: 'DOM\\svc'" in msg and "apic#<Login-Domaene>" in msg
    assert "Leerzeichen" in msg and "ASCII" in msg
    assert SECRET.strip() not in msg  # Passwort erscheint nie


def test_infoblox_html_antwort_lauf_meldung_ohne_zugangsdaten(monkeypatch):
    monkeypatch.setattr(diagnose, "get_settings", lambda: Settings())
    html = "<html><body><h1>Authorization   Required</h1></body></html>"
    exc = _error("https://ddi.example.ch/wapi/v2.12/network", 401, text=html)
    msg = diagnose.device_error(exc, "infoblox", _target("infoblox", "ro-automato"))
    assert "Geraet meldet: Authorization Required" in msg and "Allowed Interfaces" in msg
    assert "ro-automato" not in msg and "Leerzeichen" not in msg  # nur lokal (--check)


def test_orchestrator_und_andere_fehler_unveraendert(monkeypatch):
    settings = Settings(orchestrator_url="https://automato.example.ch:8000")
    monkeypatch.setattr(diagnose, "get_settings", lambda: settings)
    upload = _error("https://automato.example.ch:8000/internal/collector-runs/r/references", 403)
    assert diagnose.device_error(upload, "infoblox").startswith("HTTP 403")
    server = _error("https://ddi.example.ch/wapi/v2.12/network", 500)
    assert diagnose.device_error(server, "infoblox").startswith("HTTP 500")
    assert diagnose.device_error(RuntimeError("kaputt"), "fortigate") == "kaputt"
