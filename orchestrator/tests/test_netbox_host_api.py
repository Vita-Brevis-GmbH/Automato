"""Kunden fuer den NetBox-Host (1.11.0, ADR 0054): GET /netbox-host nur mit dem eigenen Token des
NetBox-Hosts (nicht dem des Cockpits) - nur Kunden mit NetBox, nur Kurzname und Name.

Seit 1.13.0 (ADR 0056) je Kunde der Stand des Zugangs fuer Automato und PUT /netbox-host/zugang:
der Agent liefert Lese- und Sync-Token, Automato speichert sie verschluesselt am Quellsystem
NetBox des Mandanten aus dem Kunden-Portal - nie ueber vorhandene Zugangsdaten, nie ohne
Mandanten, die Tokens nie in Antwort, Audit oder Log."""

import logging

from app import credentials
from app.audit import decode_detail
from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    DnsType,
    IpamType,
    PortalCustomer,
    SourceSystem,
    Tenant,
)
from app.models.source_system import Platform

TOKEN = "d" * 64
COCKPIT = "c" * 64
LESEN = "0123456789abcdef0123456789abcdef01234567"
SYNC = "fedcba9876543210fedcba9876543210fedcba98"
ADRESSE = "https://kunde-a-nb.mgmt.example.ch"


def _settings(**kw) -> Settings:
    base = {"front_domain": "mgmt.example.ch", "netbox_host_token": TOKEN, "cockpit_token": COCKPIT}
    base.update(kw)
    return Settings(**base)


def _get(client, token: str | None = TOKEN, **kw):
    client.app.dependency_overrides[get_settings] = lambda: _settings(**kw)
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get("/netbox-host", headers=headers)


def _put(client, body: object = None, token: str | None = TOKEN, **kw):
    client.app.dependency_overrides[get_settings] = lambda: _settings(**kw)
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    if body is None:
        body = {"kurzname": "kunde-a", "lesen": LESEN, "sync": SYNC}
    return client.put("/netbox-host/zugang", json=body, headers=headers)


def _zugang(client, **kw) -> dict[str, str]:
    r = _get(client, **kw)
    assert r.status_code == 200, r.text
    return {k["kurzname"]: k["zugang"] for k in r.json()["kunden"]}


def _kunde(session_factory, tenant: bool = True, nb: bool = True, kurz: str = "kunde-a") -> int:
    """Kunde im Portal, auf Wunsch mit Mandant; gibt die Mandanten-ID zurueck (oder 0)."""
    with session_factory() as s:
        tid = None
        if tenant:
            t = Tenant(name=f"m-{kurz}", ipam_type=IpamType.NETBOX,
                       dns_type=DnsType.ACTIVE_DIRECTORY)  # fmt: skip
            s.add(t)
            s.flush()
            tid = t.id
        s.add(PortalCustomer(short_name=kurz, name=kurz.title(), nb=nb, tenant_id=tid,
                             customer_ids=[], links=[]))  # fmt: skip
        s.commit()
        return tid or 0


def _quelle(session_factory, tid: int, url: str, name: str = "NetBox", daten: dict | None = None,
            platform: Platform = Platform.NETBOX, ref_role: str | None = None) -> int:  # fmt: skip
    with session_factory() as s:
        q = SourceSystem(tenant_id=tid, name=name, platform=platform, base_url=url,
                         ref_role=ref_role,
                         credentials=credentials.encrypt(daten) if daten else None)  # fmt: skip
        s.add(q)
        s.commit()
        return q.id


def _netbox(session_factory) -> list[SourceSystem]:
    with session_factory() as s:
        return list(s.query(SourceSystem).filter(SourceSystem.platform == Platform.NETBOX)
                    .order_by(SourceSystem.id))  # fmt: skip


def _audit(session_factory) -> list[tuple[str, str, str, dict | None]]:
    with session_factory() as s:
        rows = s.query(AuditLog).order_by(AuditLog.id).all()
        return [(a.actor, a.action, a.subject, decode_detail(a)) for a in rows]


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
        "kunden": [{"kurzname": "kunde-a", "name": "Kunde A", "zugang": "kein_mandant"},
                   {"kurzname": "kunde-b", "name": "Kunde B", "zugang": "kein_mandant"}],
        "loeschen": [],  # ADR 0059: keine Loeschauftraege
    }  # fmt: skip
    assert "kb" not in r.text  # kein Realm, kein Mandant


def test_zugang_nur_mit_eigenem_token(client, session_factory):
    _kunde(session_factory)
    assert _put(client, token=None).status_code == 401
    assert _put(client, token=COCKPIT).status_code == 401
    assert _put(client, netbox_host_token="").status_code == 404
    assert _netbox(session_factory) == [] and _audit(session_factory) == []


def test_zugang_pruefen_ohne_werte_in_der_antwort(client, session_factory):
    _kunde(session_factory)
    for body in (
        ["kein Objekt"],
        {"kurzname": "Kunde A", "lesen": LESEN, "sync": SYNC},
        {"kurzname": "kunde-a", "lesen": "kurz", "sync": SYNC},
        {"kurzname": "kunde-a", "lesen": LESEN, "sync": SYNC + " x"},
        {"kurzname": "kunde-a", "lesen": LESEN, "sync": LESEN},
        {"kurzname": "kunde-a", "lesen": LESEN},
    ):
        r = _put(client, body)
        assert r.status_code == 422, (body, r.text)
        assert LESEN not in r.text and SYNC not in r.text  # Fehler nennen nur das Feld
    assert _netbox(session_factory) == []


def test_zugang_nur_fuer_kunden_mit_netbox(client, session_factory):
    _kunde(session_factory, nb=False)
    assert _put(client).status_code == 404  # ohne NetBox gibt es den Kunden hier nicht
    unbekannt = _put(client, {"kurzname": "kunde-x", "lesen": LESEN, "sync": SYNC})
    assert unbekannt.status_code == 404
    assert _netbox(session_factory) == [] and _audit(session_factory) == []


def test_ohne_mandant_nicht_uebernommen(client, session_factory):
    _kunde(session_factory, tenant=False)
    assert _zugang(client) == {"kunde-a": "kein_mandant"}  # der Agent erzeugt keine Tokens
    r = _put(client)
    assert r.status_code == 409
    assert r.json()["status"] == "kein_mandant"
    assert _netbox(session_factory) == [] and _audit(session_factory) == []


def test_uebernimmt_zugang_beim_mandanten_und_ist_idempotent(client, session_factory, caplog):
    caplog.set_level(logging.DEBUG)
    tid = _kunde(session_factory)
    _quelle(session_factory, tid, "https://ddi.example.ch", name="DDI",
            platform=Platform.INFOBLOX, ref_role="primary")  # fmt: skip
    assert _zugang(client) == {"kunde-a": "fehlt"}
    r = _put(client)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "uebernommen"
    (q,) = _netbox(session_factory)
    assert (q.tenant_id, q.name, q.base_url, q.verify_tls, q.agent_id) == (
        tid, "NetBox", ADRESSE, True, None)  # fmt: skip
    assert q.ref_role == "enrichment"  # Infoblox bleibt fuehrend (wie in der Oberflaeche)
    assert credentials.decrypt(q.credentials) == {"token": LESEN, "write_token": SYNC}
    assert LESEN not in q.credentials  # verschluesselt wie von Hand eingetragen
    ((akteur, aktion, subjekt, detail),) = _audit(session_factory)
    assert (akteur, aktion, subjekt) == ("netbox-host", "source.netbox_host_access", str(q.id))
    assert detail == {"kunde": "kunde-a", "source_id": q.id, "tenant_id": tid, "name": "NetBox",
                      "angelegt": True, "quelle": "netbox-host"}  # fmt: skip
    assert _zugang(client) == {"kunde-a": "vorhanden"}

    nochmal = _put(client)  # gleiche Anfrage: nichts Neues, kein zweites Audit
    assert nochmal.status_code == 200 and nochmal.json()["status"] == "unveraendert"
    assert len(_audit(session_factory)) == 1 and len(_netbox(session_factory)) == 1

    andere = _put(client, {"kurzname": "kunde-a", "lesen": "1" * 40, "sync": "2" * 40})
    assert andere.status_code == 200 and andere.json()["status"] == "vorhanden"
    (q,) = _netbox(session_factory)
    assert credentials.decrypt(q.credentials) == {"token": LESEN, "write_token": SYNC}
    assert len(_audit(session_factory)) == 1
    for text in (r.text, nochmal.text, andere.text, caplog.text):
        assert LESEN not in text and SYNC not in text


def test_handeintrag_bleibt(client, session_factory):
    """Von Hand eingetragener Weg direkt zum NetBox-Host (Port) - gleiche NetBox, bleibt."""
    tid = _kunde(session_factory)
    hand = {"token": "a" * 40}
    _quelle(session_factory, tid, "https://kunde-a-nb.mgmt.example.ch:8444/", daten=hand)
    assert _zugang(client) == {"kunde-a": "vorhanden"}
    r = _put(client)
    assert r.status_code == 200 and r.json()["status"] == "vorhanden"
    (q,) = _netbox(session_factory)
    assert credentials.decrypt(q.credentials) == hand
    assert q.base_url == "https://kunde-a-nb.mgmt.example.ch:8444/"
    assert _audit(session_factory) == []


def test_handeintrag_bei_anderem_mandanten_bleibt(client, session_factory):
    _kunde(session_factory)
    anderer = _kunde(session_factory, kurz="kunde-b")
    _quelle(session_factory, anderer, ADRESSE, daten={"token": "a" * 40, "write_token": "b" * 40})
    assert _zugang(client)["kunde-a"] == "vorhanden"  # sonst ersetzte der Agent die Tokens
    assert _put(client).json()["status"] == "vorhanden"
    assert len(_netbox(session_factory)) == 1


def test_quellsystem_ohne_zugangsdaten_bekommt_ihn(client, session_factory):
    tid = _kunde(session_factory)
    qid = _quelle(session_factory, tid, ADRESSE + "/", name="NB Kunde A", ref_role="enrichment")
    assert _zugang(client) == {"kunde-a": "fehlt"}
    r = _put(client)
    assert r.status_code == 200 and r.json()["status"] == "uebernommen"
    (q,) = _netbox(session_factory)
    assert (q.id, q.name, q.ref_role) == (qid, "NB Kunde A", "enrichment")
    assert q.base_url == ADRESSE + "/"  # Adresse bleibt, wie sie eingetragen wurde
    assert credentials.decrypt(q.credentials) == {"token": LESEN, "write_token": SYNC}
    assert _audit(session_factory)[0][3]["angelegt"] is False


def test_andere_netbox_des_mandanten_bleibt(client, session_factory):
    tid = _kunde(session_factory)
    _quelle(session_factory, tid, "https://netbox.example.ch", daten={"token": "a" * 40})
    _kunde(session_factory, kurz="kunde-b")
    leer = _kunde(session_factory, kurz="kunde-c")
    _quelle(session_factory, leer, "https://ipam.example.ch")  # ohne Zugangsdaten, andere Adresse
    assert _zugang(client) == {"kunde-a": "andere_adresse", "kunde-b": "fehlt",
                               "kunde-c": "andere_adresse"}  # fmt: skip
    r = _put(client)
    assert r.status_code == 409 and r.json()["status"] == "andere_adresse"
    assert len(_netbox(session_factory)) == 2 and _audit(session_factory) == []


def test_freier_name_und_rolle(client, session_factory):
    tid = _kunde(session_factory)
    fw = Platform.FORTIGATE
    _quelle(session_factory, tid, "https://fw.example.ch", name="NetBox", platform=fw)
    assert _put(client).json()["status"] == "uebernommen"
    (q,) = _netbox(session_factory)
    assert (q.name, q.ref_role) == ("NetBox kunde-a", "primary")  # einzige Referenzquelle


def test_ohne_domain_nicht_uebernommen(client, session_factory):
    _kunde(session_factory)
    assert _zugang(client, front_domain="") == {"kunde-a": "keine_adresse"}
    r = _put(client, front_domain="")
    assert r.status_code == 409 and r.json()["status"] == "keine_adresse"
    assert _netbox(session_factory) == []
