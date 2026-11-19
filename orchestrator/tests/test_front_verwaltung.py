"""deploy/front/front.sh mit verbundenem Cockpit (1.14.0, ADR 0058): `kunde`, `link` und
`entfernen` von Hand nur noch mit ausdruecklichem `--trotzdem` (Notweg, mit Warnung); ohne Cockpit
wie bisher. Ob ein Cockpit verbunden ist, steht im Soll-Stand (`soll.json`: `verwaltung`)."""

import json

from test_front_script import _soll, front  # noqa: F401 - Fixture


def _verbunden(front, kunden: list[dict] | None = None) -> None:  # noqa: F811
    datei = _soll(front, kunden or [])
    data = json.loads(datei.read_text(encoding="utf-8"))
    datei.write_text(json.dumps({**data, "verwaltung": "cockpit"}), encoding="utf-8")


def test_von_hand_nur_mit_trotzdem(front):  # noqa: F811 - Fixture
    assert front("kunde", "kunde-c", "Kunde C").returncode == 0  # vor der Kopplung von Hand
    _verbunden(front)
    nein = front("kunde", "kunde-a", "Kunde A", "--doc")
    assert nein.returncode != 0 and "nur noch im Cockpit" in nein.stderr
    assert "--trotzdem" in nein.stderr
    assert not (front.dir / "kunden" / "kunde-a.conf").exists()
    for args in (("link", "kunde-c", "Handbuch", "https://h.example.ch"),
                 ("entfernen", "kunde-c", "--ja")):  # fmt: skip
        out = front(*args)
        assert out.returncode != 0 and "nur noch im Cockpit" in out.stderr, args
    assert (front.gen / "sites" / "kunde-c.caddy").exists()

    # Notweg: --trotzdem an beliebiger Stelle, mit Warnung
    ja = front("kunde", "kunde-a", "--trotzdem", "Kunde A", "--doc")
    assert ja.returncode == 0, ja.stdout + ja.stderr
    assert "am Cockpit vorbei (--trotzdem)" in ja.stdout
    assert "kunde-a-doc.mgmt.example.ch {" in (front.gen / "sites" / "kunde-a.caddy").read_text()
    out = front("link", "kunde-a", "Handbuch", "https://h.example.ch", "--trotzdem")
    assert out.returncode == 0 and "am Cockpit vorbei" in out.stdout
    out = front("entfernen", "kunde-c", "--ja", "--trotzdem")
    assert out.returncode == 0 and not (front.gen / "sites" / "kunde-c.caddy").exists()
    # Kunden aus dem Cockpit bleiben auch mit --trotzdem unberuehrt
    _verbunden(front, [{"kurzname": "kunde-b", "name": "B", "quelle": "cockpit", "links": []}])
    assert front("sync").returncode == 0
    out = front("kunde", "kunde-b", "Anders", "--trotzdem")
    assert out.returncode != 0 and "im Cockpit gepflegt" in out.stderr


def test_ohne_cockpit_wie_bisher(front):  # noqa: F811 - Fixture
    _soll(front, [])  # Soll-Stand ohne `verwaltung`
    out = front("kunde", "kunde-a", "Kunde A")
    assert out.returncode == 0 and "am Cockpit vorbei" not in out.stdout
    assert front("entfernen", "kunde-a", "--ja").returncode == 0
