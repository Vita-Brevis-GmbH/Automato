"""Kunden fuer den NetBox-Host (1.11.0, ADR 0054): GET /netbox-host nur mit dem eigenen Token des
NetBox-Hosts (nicht dem des Cockpits) - nur Kunden mit NetBox, nur Kurzname und Name."""

from app.config import Settings, get_settings
from app.models import PortalCustomer

TOKEN = "d" * 64
COCKPIT = "c" * 64


def _settings(**kw) -> Settings:
    base = {"front_domain": "mgmt.example.ch", "netbox_host_token": TOKEN, "cockpit_token": COCKPIT}
    base.update(kw)
    return Settings(**base)


def _get(client, token: str | None = TOKEN, **kw):
    client.app.dependency_overrides[get_settings] = lambda: _settings(**kw)
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get("/netbox-host", headers=headers)


def test_nur_mit_eigenem_token(client):
    assert _get(client, token=None).status_code == 401
    assert _get(client, token=COCKPIT).status_code == 401  # das Token des Cockpits gilt hier nicht
    assert _get(client, token="e" * 64).status_code == 401
    aus = _get(client, netbox_host_token="")
    assert aus.status_code == 404  # nicht eingerichtet: den Endpunkt gibt es nicht


def test_nur_kunden_mit_netbox(client, session_factory):
    with session_factory() as s:
        s.add_all([
            PortalCustomer(short_name="kunde-b", name="Kunde B", nb=True, realm="kb",
                           tenant_id=None, customer_ids=[], links=[]),
            PortalCustomer(short_name="kunde-a", name="Kunde A", source="cockpit",
                           customer_ids=[], links=[]),
            PortalCustomer(short_name="ohne-nb", name="Ohne", nb=False, customer_ids=[], links=[]),
        ])  # fmt: skip
        s.commit()
    r = _get(client)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "vertrag": 1,
        "domain": "mgmt.example.ch",
        "kunden": [{"kurzname": "kunde-a", "name": "Kunde A"},
                   {"kurzname": "kunde-b", "name": "Kunde B"}],
    }  # fmt: skip
    assert "kb" not in r.text  # kein Realm, kein Mandant
