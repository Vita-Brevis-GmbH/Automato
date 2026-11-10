"""deploy/keycloak/make_realm.py: Realm je Umgebung bzw. Kunde aus der Vorlage – seit 1.4.0 (ADR
0051) mit dem Client automato-front des Anmelde-Tors (Rückkehr-Adressen aller Werkzeuge, kein
Secret aus einer Datei)."""

import importlib.util
import json
from pathlib import Path

import pytest

PFAD = Path(__file__).resolve().parents[2] / "deploy" / "keycloak" / "make_realm.py"
_spec = importlib.util.spec_from_file_location("make_realm", PFAD)
make_realm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_realm)  # type: ignore[union-attr]

VORLAGE = json.loads(make_realm.TEMPLATE.read_text(encoding="utf-8"))


def _clients(realm: dict) -> dict:
    return {c["clientId"]: c for c in realm["clients"]}


def test_kunden_realm_mit_anmelde_tor():
    front = make_realm.front_urls(
        "kunde-a", "mgmt.example.ch", ["https://start.mgmt.int.example.ch/x"]
    )
    realm = make_realm.build_realm(VORLAGE, "kunde-a", ["https://kunde-a-doc.mgmt.example.ch"],
                                   "Kunde A", front=front)  # fmt: skip
    c = _clients(realm)
    assert realm["realm"] == "kunde-a" and realm["displayName"] == "Kunde A"
    assert c["automato-web"]["redirectUris"] == ["https://kunde-a-doc.mgmt.example.ch/*"]
    tor = c["automato-front"]
    assert tor["publicClient"] is False and tor["standardFlowEnabled"] is True
    assert tor["directAccessGrantsEnabled"] is False
    assert tor["attributes"]["pkce.code.challenge.method"] == "S256"
    assert tor["redirectUris"] == [
        f"https://kunde-a{w}.mgmt.example.ch/oauth2/callback"
        for w in ("", "-nb", "-doc", "-um", "-cmdb")
    ] + ["https://start.mgmt.int.example.ch/oauth2/callback"]
    assert "secret" not in tor and tor["webOrigins"] == []
    # 1.5.0: wie von oauth2-proxy für Keycloak empfohlen - der Client steht in der Audience
    aud = {m["name"]: m["config"] for m in tor["protocolMappers"]}["aud-automato-front"]
    assert aud["included.client.audience"] == "automato-front"
    assert aud["access.token.claim"] == "true" and aud["id.token.claim"] == "true"
    # die Vorlage selbst bleibt unverändert
    assert _clients(VORLAGE)["automato-front"]["redirectUris"] == []


def test_ohne_tor_keine_rueckkehr_adressen():
    realm = make_realm.build_realm(VORLAGE, "automato-dev", ["https://10.0.12.12"])
    assert _clients(realm)["automato-front"]["redirectUris"] == []


@pytest.mark.parametrize(
    ("kunde", "domain", "extra"),
    [("kunde-a", None, []), ("Kunde_A", "mgmt.example.ch", []), ("kunde-a", "mgmt example", []),
     (None, None, ["http://start.mgmt.int.example.ch"])],
)  # fmt: skip
def test_ungueltige_angaben(kunde, domain, extra):
    with pytest.raises(ValueError):
        make_realm.front_urls(kunde, domain, extra)


def test_aufruf_schreibt_json(capsys):
    argv = ["--realm", "vb", "--ui-url", "https://vb-doc.mgmt.example.ch",
            "--front-kunde", "vb", "--front-domain", "mgmt.example.ch"]  # fmt: skip
    assert make_realm.main(argv) == 0
    realm = json.loads(capsys.readouterr().out)
    adressen = _clients(realm)["automato-front"]["redirectUris"]
    assert "https://vb-nb.mgmt.example.ch/oauth2/callback" in adressen
