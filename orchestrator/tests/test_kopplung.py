"""Kopplung mit dem Cockpit ueber einen Einmal-Code (1.13.0, ADR 0057).

Code erzeugen (nur Admin, einmal angezeigt, nur als Hash gespeichert), einloesen ohne andere
Anmeldung (`POST /kopplung`, einmal, 15 Minuten, hoechstens 5 Fehlversuche, gedrosselt, gleiche
Antwort fuer jeden Fehlschlag), das gekoppelte Token gilt fuer `GET /inventory` und
`PUT /freigaben` neben dem Token aus `.env`, Entkoppeln. Code und Token nie in Audit, Log oder
einer anderen Antwort. Vertrag fuer die Gegenseite: `docs/vertraege/kopplung.md` im Cockpit."""

import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from app import __version__, cli
from app.api import kopplung as kopplung_api
from app.audit import decode_detail
from app.config import Settings, get_settings
from app.models import AuditLog, Coupling
from app.store import kopplung

CODE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")
ENV_TOKEN = "e" * 64


def _settings(tmp_path, **kw) -> Settings:
    base = {"front_state_dir": str(tmp_path), "front_domain": "mgmt.example.ch",
            "cockpit_token": "", "environment": "test"}  # fmt: skip
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _drossel():
    kopplung_api.DROSSEL.reset()
    yield
    kopplung_api.DROSSEL.reset()


@pytest.fixture
def api(client, tmp_path):
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    return client


def _env_token(client, tmp_path, token: str = ENV_TOKEN) -> None:
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, cockpit_token=token)


def _code(client) -> str:
    r = client.post("/kopplung/cockpit/code")
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()["code"]


def _einloesen(client, code: str, name: str = "Cockpit Dev", **extra):
    return client.post("/kopplung", json={"vertrag": 1, "code": code, "cockpit": {"name": name},
                                          **extra})  # fmt: skip


def _koppeln(client, name: str = "Cockpit Dev") -> str:
    r = _einloesen(client, _code(client), name)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _audit(session_factory) -> list[tuple[str, str, str, dict | None]]:
    with session_factory() as s:
        rows = s.query(AuditLog).order_by(AuditLog.id).all()
        return [(a.actor, a.action, a.subject, decode_detail(a)) for a in rows]


def _row(session_factory) -> Coupling:
    with session_factory() as s:
        return s.query(Coupling).filter_by(peer="cockpit").one()


def test_code_erzeugen_und_einloesen(api, session_factory):
    r = api.post("/kopplung/cockpit/code")
    assert r.status_code == 201
    body = r.json()
    assert CODE.match(body["code"]) and (body["minutes"], body["max_failures"]) == (15, 5)
    bis = datetime.fromisoformat(body["expires"])
    assert timedelta(minutes=14) < bis - datetime.now(UTC) <= timedelta(minutes=15)
    stand = api.get("/kopplung/cockpit").json()
    assert stand["coupled"] is False and stand["code"]["failures"] == 0
    assert stand["env_token"] is False
    # nur als Hash gespeichert
    row = _row(session_factory)
    roh = body["code"].replace("-", "")
    assert row.code_hash == kopplung._hash(roh) and roh not in str(vars(row))

    # klein geschrieben, ohne Trennzeichen, O statt 0 - gilt trotzdem
    eingabe = roh.lower().replace("0", "o")
    r = _einloesen(api, eingabe, "Cockpit Dev")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    data = r.json()
    assert data["vertrag"] == 1 and data["ersetzt"] is False
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", data["token"])  # 32 Byte, URL-sicher
    assert data["automato"] == {"version": __version__, "umgebung": "test", "oberflaeche": None}
    assert data["endpunkte"] == {"inventar": "/api/inventory", "freigaben": "/api/freigaben"}

    row = _row(session_factory)
    assert row.token_hash == kopplung._hash(data["token"]) and row.code_hash is None
    stand = api.get("/kopplung/cockpit").json()
    assert (stand["coupled"], stand["name"], stand["coupled_by"]) == (True, "Cockpit Dev",
                                                                      "dev-admin")  # fmt: skip
    assert stand["code"] is None and stand["coupled_at"]


def test_token_gilt_fuer_inventar_und_freigaben(api, tmp_path):
    # ohne Kopplung und ohne Token aus .env gibt es die Endpunkte nicht
    assert api.get("/inventory", headers=_bearer("x")).status_code == 404
    assert api.put("/freigaben", json={"vertrag": 1, "kunden": []}).status_code == 404
    token = _koppeln(api)
    inv = api.get("/inventory", headers=_bearer(token))
    assert inv.status_code == 200 and inv.json()["automato"]["version"] == __version__
    put = api.put("/freigaben", json={"vertrag": 1, "kunden": []}, headers=_bearer(token))
    assert put.status_code == 200, put.text
    # falsch bzw. ohne Token: 401 (jetzt gibt es die Endpunkte)
    assert api.get("/inventory", headers=_bearer("falsch")).status_code == 401
    assert api.get("/inventory").status_code == 401
    assert api.get("/inventory", headers={"Authorization": f"Token {token}"}).status_code == 401
    # Anmeldung einer Person zaehlt nicht
    assert api.get("/inventory", headers={"X-Dev-Roles": "admin"}).status_code == 401


def test_code_nur_einmal(api):
    code = _code(api)
    assert _einloesen(api, code).status_code == 200
    zweimal = _einloesen(api, code)
    assert zweimal.status_code == 401


def test_code_abgelaufen(api, session_factory):
    code = _code(api)
    with session_factory() as s:
        row = s.query(Coupling).one()
        row.code_expires = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    assert api.get("/kopplung/cockpit").json()["code"] is None
    assert _einloesen(api, code).status_code == 401
    assert _row(session_factory).code_hash is None
    assert ("anonym", "cockpit.kopplung.abgelaufen") in [a[:2] for a in _audit(session_factory)]


def test_fuenf_fehlversuche_sperren_den_code(api, session_factory):
    code = _code(api)
    falsch = "ZZZZ-ZZZZ-ZZZZ" if code != "ZZZZ-ZZZZ-ZZZZ" else "0000-0000-0000"
    for versuch in range(1, 5):
        assert _einloesen(api, falsch).status_code == 401
        assert api.get("/kopplung/cockpit").json()["code"]["failures"] == versuch
    assert _einloesen(api, "kaputt").status_code == 401  # zaehlt auch: der fuenfte
    assert api.get("/kopplung/cockpit").json()["code"] is None
    # danach auch der richtige Code nicht mehr
    assert _einloesen(api, code).status_code == 401
    fehl = [a[3] for a in _audit(session_factory) if a[1] == "cockpit.kopplung.fehlversuch"]
    assert fehl == [{"versuch": i, "gesperrt": i == 5} for i in range(1, 6)]
    assert api.get("/kopplung/cockpit").json()["coupled"] is False


def test_gleiche_antwort_ohne_code_und_mit_falschem(api):
    ohne = _einloesen(api, "ABCD-EFGH-JKMN")
    _code(api)
    falsch = _einloesen(api, "ABCD-EFGH-JKMN")
    assert (ohne.status_code, ohne.json()) == (falsch.status_code, falsch.json())
    assert ohne.status_code == 401


def test_neuer_code_ersetzt_den_offenen(api, session_factory):
    alt = _code(api)
    neu = _code(api)
    assert _einloesen(api, alt).status_code == 401 or alt == neu
    assert _einloesen(api, neu).status_code == 200
    codes = [a[3] for a in _audit(session_factory) if a[1] == "cockpit.kopplung.code"]
    assert [c["ersetzt"] for c in codes] == [False, True]


def test_neues_koppeln_ersetzt_das_token(api):
    alt = _koppeln(api)
    r = _einloesen(api, _code(api), "Cockpit Neu")
    assert r.status_code == 200 and r.json()["ersetzt"] is True
    neu = r.json()["token"]
    assert api.get("/inventory", headers=_bearer(alt)).status_code == 401
    assert api.get("/inventory", headers=_bearer(neu)).status_code == 200
    assert api.get("/kopplung/cockpit").json()["name"] == "Cockpit Neu"


def test_entkoppeln(api):
    token = _koppeln(api)
    r = api.delete("/kopplung/cockpit")
    assert r.status_code == 200 and r.json()["coupled"] is False
    # ohne Token aus .env gibt es die Endpunkte danach nicht mehr
    assert api.get("/inventory", headers=_bearer(token)).status_code == 404
    # offenen Code verwerfen
    code = _code(api)
    assert api.delete("/kopplung/cockpit/code").json()["code"] is None
    assert _einloesen(api, code).status_code == 401


def test_token_aus_env_gilt_weiter(api, tmp_path):
    _env_token(api, tmp_path)
    assert api.get("/kopplung/cockpit").json()["env_token"] is True
    assert api.get("/inventory", headers=_bearer(ENV_TOKEN)).status_code == 200
    token = _koppeln(api)
    assert api.get("/inventory", headers=_bearer(ENV_TOKEN)).status_code == 200
    assert api.get("/inventory", headers=_bearer(token)).status_code == 200
    api.delete("/kopplung/cockpit")
    assert api.get("/inventory", headers=_bearer(token)).status_code == 401
    assert api.get("/inventory", headers=_bearer(ENV_TOKEN)).status_code == 200


def test_zuletzt_benutzt_hoechstens_alle_fuenf_minuten(api, session_factory):
    token = _koppeln(api)
    assert _row(session_factory).last_used is None
    api.get("/inventory", headers=_bearer(token))
    erst = _row(session_factory).last_used
    assert erst is not None
    api.get("/inventory", headers=_bearer(token))
    assert _row(session_factory).last_used == erst
    with session_factory() as s:
        s.query(Coupling).one().last_used = datetime.now(UTC) - timedelta(minutes=6)
        s.commit()
    api.get("/inventory", headers=_bearer(token))
    assert kopplung._aware(_row(session_factory).last_used) > datetime.now(UTC) - timedelta(
        minutes=1
    )


def test_nur_admin(api):
    wege = (("get", "/kopplung/cockpit"), ("post", "/kopplung/cockpit/code"),
            ("delete", "/kopplung/cockpit/code"), ("delete", "/kopplung/cockpit"))  # fmt: skip
    for method, path in wege:
        for kopf in ({"X-Dev-Roles": "operator"}, {"X-Dev-Customers": "1"}):
            r = getattr(api, method)(path, headers=kopf)
            assert r.status_code == 403, (method, path, kopf)


def test_anfrage_pruefen(api):
    code = _code(api)
    assert _einloesen(api, code, vertrag=2).status_code == 422
    assert _einloesen(api, code, name="mit\nUmbruch").status_code == 422
    assert _einloesen(api, code, name="x" * 101).status_code == 422
    assert api.post("/kopplung", json={"code": code}).status_code == 422
    # keiner davon zaehlt als Fehlversuch
    assert api.get("/kopplung/cockpit").json()["code"]["failures"] == 0
    # unbekannte Felder einer neueren Gegenseite stoeren nicht
    assert _einloesen(api, code, neu={"x": 1}).status_code == 200


def test_drossel(api, monkeypatch):
    monkeypatch.setattr(kopplung_api, "DROSSEL", kopplung_api.Drossel(anzahl=3, fenster=60))
    code = _code(api)
    for _ in range(3):
        assert _einloesen(api, "0000-0000-0001").status_code == 401
    r = _einloesen(api, code)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    # gedrosselt zaehlt nicht als Fehlversuch
    assert api.get("/kopplung/cockpit").json()["code"]["failures"] == 3


def test_drossel_fenster():
    d = kopplung_api.Drossel(anzahl=2, fenster=60)
    assert d.warten(0) == 0 and d.warten(10) == 0
    assert d.warten(20) == 41  # bis zum Ende des ersten Versuchs
    assert d.warten(60) == 0  # der erste ist aus dem Fenster
    assert d.warten(61) > 0


def test_nichts_geheimes_in_audit_log_und_antworten(api, session_factory, caplog):
    caplog.set_level(logging.DEBUG)
    code = _code(api)
    _einloesen(api, "0000-0000-0001")
    r = _einloesen(api, code, "Cockpit Dev")
    token = r.json()["token"]
    inv = api.get("/inventory", headers=_bearer(token))
    stand = api.get("/kopplung/cockpit")
    entkoppelt = api.delete("/kopplung/cockpit")
    geheim = (code, code.replace("-", ""), token)
    texte = [repr(_audit(session_factory)), caplog.text, inv.text, stand.text, entkoppelt.text]
    for text in texte:
        for g in geheim:
            assert g not in text
    with session_factory() as s:
        roh = repr([vars(c) for c in s.query(Coupling).all()])
    assert not any(g in roh for g in geheim)
    aktionen = [a[1] for a in _audit(session_factory)]
    assert aktionen == ["cockpit.kopplung.code", "cockpit.kopplung.fehlversuch",
                        "cockpit.kopplung.gekoppelt", "cockpit.kopplung.entkoppelt"]  # fmt: skip
    gekoppelt = next(a for a in _audit(session_factory) if a[1] == "cockpit.kopplung.gekoppelt")
    assert gekoppelt[0] == "cockpit"
    assert gekoppelt[3] == {"name": "Cockpit Dev", "ersetzt": False, "code_von": "dev-admin"}


def test_code_normalisieren():
    assert kopplung.normalize_code("abcd-efgh-jkmn") == "ABCDEFGHJKMN"
    assert kopplung.normalize_code(" o1L1 IiOo 2345 ") == "011111002345"
    assert kopplung.normalize_code("ABCD EFGH JKMN") == "ABCDEFGHJKMN"
    assert kopplung.normalize_code("OIL0-0000-0000") == "011000000000"
    assert kopplung.normalize_code("ABCD-EFGH-JKM") is None  # zu kurz
    assert kopplung.normalize_code("ABCD-EFGH-JKMU") is None  # U gibt es nicht
    assert kopplung.format_code("ABCDEFGHJKMN") == "ABCD-EFGH-JKMN"


def test_cli(session_factory, monkeypatch, capsys):
    session = session_factory()
    args = cli.build_parser().parse_args(["cockpit-kopplung"])
    assert cli._cmd_cockpit_kopplung(session, args) == 0
    out = capsys.readouterr().out
    (code,) = re.findall(r"^\s+([0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4})$", out, re.M)
    assert CODE.match(code) and "nie in Chat" in out
    token, _ = kopplung.redeem(session, kopplung.COCKPIT, code, "Cockpit CLI")
    session.commit()

    args = cli.build_parser().parse_args(["cockpit-kopplung", "--status"])
    assert cli._cmd_cockpit_kopplung(session, args) == 0
    out = capsys.readouterr().out
    assert "gekoppelt mit „Cockpit CLI“" in out and token not in out

    args = cli.build_parser().parse_args(["cockpit-kopplung", "--entkoppeln"])
    assert cli._cmd_cockpit_kopplung(session, args) == 0
    assert "entkoppelt" in capsys.readouterr().out
    assert not kopplung.coupled(session, kopplung.COCKPIT)
    actors = {a[0] for a in _audit(session_factory)}
    assert "cli:cockpit-kopplung" in actors
    session.close()
