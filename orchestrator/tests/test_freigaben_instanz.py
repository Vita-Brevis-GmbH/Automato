"""Eigene Automato-Instanz je Kunde (1.16.0, ADR 0061) in der Hauptinstanz: `automato_instanz` in
den Freigaben des Cockpits (optional, Vertrag v1 bleibt) -> Kunden-Portal -> Soll-Stand des
Front-Proxys; im Inventar je Kunde und unter `ist.front.instanzen`; der Einmal-Code der Kopplung,
den `betrieb.sh installieren` in einer neuen Instanz hinterlegt (nur als Hash)."""

import io
import json

import pytest
from app import cli
from app.api import kopplung as kopplung_api
from app.audit import decode_detail
from app.auth import realms
from app.config import Settings, get_settings
from app.models import AuditLog, Coupling, PortalCustomer
from app.store import kopplung

TOKEN = "d" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CODE = "ABCD-EFGH-JKMN"  # Einmal-Code in Testform (Crockford-Base32)


@pytest.fixture
def api(client, tmp_path):
    settings = Settings(front_state_dir=str(tmp_path), front_domain="mgmt.example.ch",
                        cockpit_token=TOKEN)  # fmt: skip
    client.app.dependency_overrides[get_settings] = lambda: settings
    client.state_dir = tmp_path  # type: ignore[attr-defined]
    realms.invalidate()
    kopplung_api.DROSSEL.reset()
    yield client
    kopplung_api.DROSSEL.reset()


def _kunde(kurz: str, **kw) -> dict:
    return {"kurzname": kurz, "name": f"Kunde {kurz}", "werkzeuge": kw.pop("werkzeuge", ["doc"]),
            "zugriff": "ueberall", "netze": [], "realm": kw.pop("realm", kurz), **kw}  # fmt: skip


def _put(client, kunden: list[dict]):
    return client.put("/freigaben", json={"vertrag": 1, "kunden": kunden}, headers=AUTH)


def _soll(client) -> dict:
    data = json.loads((client.state_dir / "soll.json").read_text(encoding="utf-8"))
    return {k["kurzname"]: k for k in data["kunden"]}


def _audit(session_factory, action: str) -> list[tuple[str, str, dict | None]]:
    with session_factory() as s:
        rows = s.query(AuditLog).filter(AuditLog.action == action).order_by(AuditLog.id).all()
        return [(a.actor, a.subject, decode_detail(a)) for a in rows]


def test_freigabe_mit_eigener_instanz(api, session_factory):
    r = _put(api, [_kunde("kunde-a", automato_instanz="kunde-a"), _kunde("kunde-b")])
    assert r.status_code == 200, r.text
    assert r.json()["angelegt"] == ["kunde-a", "kunde-b"]
    with session_factory() as s:
        werte = {pc.short_name: pc.automato_instanz for pc in s.query(PortalCustomer)}
    assert werte == {"kunde-a": "kunde-a", "kunde-b": None}
    # Soll-Stand: nur mit eigener Instanz (sonst unveraendert wie bis 1.15)
    soll = _soll(api)
    assert soll["kunde-a"]["automato_instanz"] == "kunde-a"
    assert "automato_instanz" not in soll["kunde-b"]
    created = {subj: d for _, subj, d in _audit(session_factory, "portal.customer.created")}
    assert created["kunde-a"]["automato_instanz"] == "kunde-a"
    # gleiche Anfrage: nichts geaendert
    r = _put(api, [_kunde("kunde-a", automato_instanz="kunde-a"), _kunde("kunde-b")])
    assert r.json()["unveraendert"] == 2 and r.json()["geaendert"] == []
    # Instanz wechselt bzw. faellt weg (Feld fehlt): wieder die gemeinsame Instanz
    r = _put(api, [_kunde("kunde-a"), _kunde("kunde-b", automato_instanz="b-eigen")])
    assert sorted(r.json()["geaendert"]) == ["kunde-a", "kunde-b"]
    soll = _soll(api)
    assert "automato_instanz" not in soll["kunde-a"]
    assert soll["kunde-b"]["automato_instanz"] == "b-eigen"
    updated = {subj: d for _, subj, d in _audit(session_factory, "portal.customer.updated")}
    assert updated["kunde-a"] == {"automato_instanz": None, "quelle": "cockpit"}
    # Kunden-Portal (nur lesbar) und Inventar zeigen sie
    portal = api.get("/portal/customers", headers={"X-Dev-Roles": "admin"})
    assert portal.status_code == 200, portal.text
    eintraege = portal.json()
    eintraege = eintraege["customers"] if isinstance(eintraege, dict) else eintraege
    assert {e["short_name"]: e["automato_instanz"] for e in eintraege} == {
        "kunde-a": None, "kunde-b": "b-eigen"}  # fmt: skip


@pytest.mark.parametrize(
    ("kunden", "grund"),
    [
        ([_kunde("kunde-a", automato_instanz="Kunde_A")], "automato_instanz ungültig"),
        ([_kunde("kunde-a", automato_instanz="a")], "automato_instanz ungültig"),
        ([_kunde("kunde-a", automato_instanz="1a")], "automato_instanz ungültig"),
        ([_kunde("kunde-a", automato_instanz="x" * 32)], "automato_instanz ungültig"),
        ([_kunde("kunde-a", automato_instanz=["a"])], "automato_instanz ungültig"),
        (
            [
                _kunde("kunde-a", automato_instanz="gemeinsam"),
                _kunde("kunde-b", automato_instanz="gemeinsam"),
            ],
            "doppelt in der Anfrage",
        ),
    ],
)
def test_instanz_wird_geprueft_alles_oder_nichts(api, session_factory, kunden, grund):
    r = _put(api, [*kunden, _kunde("kunde-c")])
    assert r.status_code == 422, r.text
    assert any(grund in f["grund"] for f in r.json()["fehler"]), r.json()
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 0  # alles oder nichts


def test_inventar_je_kunde_und_ist(api, session_factory):
    assert _put(api, [_kunde("kunde-a", automato_instanz="kunde-a")]).status_code == 200
    topo = {"zeit": "2026-11-16T10:00:00+00:00", "domain": "mgmt.example.ch",
            "kunden": [{"kurzname": "kunde-a", "automato_instanz": "anders"},
                       {"kurzname": "von-hand", "automato_instanz": "hand-1"},
                       {"kurzname": "kaputt", "automato_instanz": "Böse; rm"},
                       {"kurzname": "ohne"}]}  # fmt: skip
    (api.state_dir / "topologie.json").write_text(json.dumps(topo), encoding="utf-8")
    status = {"ok": True, "zeit": "2026-11-16T10:00:00+00:00", "soll_sha256": "x", "kunden": [],
              "ist": {"kunden": [], "instanzen": ["kunde-a", "alt", "../x", "Kaputt"]}}  # fmt: skip
    (api.state_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    inv = api.get("/inventory", headers=AUTH).json()
    # aus dem Kunden-Portal (Soll), sonst aus dem Front-Proxy - nur gueltige Namen
    assert {k["kurzname"]: k["automato_instanz"] for k in inv["kunden"]} == {
        "kunde-a": "kunde-a", "von-hand": "hand-1", "kaputt": None, "ohne": None}  # fmt: skip
    assert inv["ist"]["front"]["instanzen"] == ["alt", "kunde-a"]


def test_kopplungs_code_hinterlegen(api, session_factory, monkeypatch, capsys):
    """`automato cockpit-kopplung --hinterlegen` (von betrieb.sh installieren): der Code des
    Cockpits kommt von stdin, nur der Hash bleibt; danach wie ein hier erzeugter Code einloesbar."""
    session = session_factory()
    monkeypatch.setattr("sys.stdin", io.StringIO(CODE.lower() + "\n"))
    args = cli.build_parser().parse_args(["cockpit-kopplung", "--hinterlegen"])
    assert cli._cmd_cockpit_kopplung(session, args) == 0
    out = capsys.readouterr().out
    assert "hinterlegt" in out and CODE not in out
    with session_factory() as s:
        row = s.query(Coupling).one()
        assert row.code_hash == kopplung._hash("ABCDEFGHJKMN") and CODE not in str(vars(row))
    audit = _audit(session_factory, "cockpit.kopplung.code")
    assert audit[0][0] == "cli:betrieb-installieren" and set(audit[0][2]) == {"bis", "ersetzt"}
    r = api.post("/kopplung", json={"vertrag": 1, "code": CODE, "cockpit": {"name": "Cockpit"}})
    assert r.status_code == 200, r.text
    assert r.json()["token"]
    # ungueltiger Code: abgelehnt, nichts gespeichert
    monkeypatch.setattr("sys.stdin", io.StringIO("ABCD-EFGH-JKMU\n"))
    assert cli._cmd_cockpit_kopplung(session, args) == 2
    assert "kein gültiger Einmal-Code" in capsys.readouterr().err
    with session_factory() as s:
        assert s.query(Coupling).one().code_hash is None
    session.close()
