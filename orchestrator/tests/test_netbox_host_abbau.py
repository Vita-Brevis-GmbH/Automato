"""deploy/netbox-host/netbox-host.sh: Loeschauftraege aus Automato und Ist-Stand (ADR 0059) - mit
den Ersatzprogrammen aus test_netbox_host_script.py.

Der Agent loescht nur, was Automato als Loeschauftrag nennt (Abbau im Cockpit nach der Frist),
nie ohne frische Sicherung, nie einen noch freigegebenen Kunden, nie bei fremder Domain und je
Auftrag nur einmal; nach jedem Abgleich meldet er seinen Stand (nur Kurznamen und Zeit)."""

import json
from pathlib import Path

from test_netbox_host_script import TOKEN, _antwort, host  # noqa: F401 - Fixture

AUFTRAG_X = "a" * 32
AUFTRAG_Y = "b" * 32


def _antwort_mit_loeschen(host, kunden, loeschen, domain="mgmt.example.ch"):  # noqa: F811
    Path(f"{host.log}.antwort").write_text(
        json.dumps({"vertrag": 1, "domain": domain, "kunden": kunden, "loeschen": loeschen})
    )


def _stand(host) -> list[dict]:  # noqa: F811
    datei = Path(f"{host.log}.stand")
    return (
        [json.loads(z) for z in datei.read_text().splitlines() if z.strip()]
        if datei.exists()
        else []
    )


def _vorbereiten(host) -> None:  # noqa: F811
    assert host("neu", "kunde-a").returncode == 0
    assert host("neu", "kunde-x").returncode == 0
    assert host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN).returncode == 0


def test_sync_loescht_nur_auf_auftrag_mit_sicherung(host, tmp_path):  # noqa: F811
    _vorbereiten(host)
    host.log.write_text("")
    kunden = [{"kurzname": "kunde-a", "name": "Kunde A", "zugang": "vorhanden"}]
    _antwort_mit_loeschen(host, kunden, [
        {"kurzname": "kunde-x", "auftrag": AUFTRAG_X},
        {"kurzname": "kunde-a", "auftrag": AUFTRAG_Y},  # noch freigegeben: nie
        {"kurzname": "Kunde Z", "auftrag": AUFTRAG_Y},  # ungueltig: uebergangen
        {"kurzname": "kunde-z", "auftrag": "kurz"},
    ])  # fmt: skip
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr  # kunde-a: nur eine Warnung
    text = out.stdout + out.stderr
    assert "kunde-x: gesichert und gelöscht (Abbau aus dem Cockpit)" in text
    assert "kunde-a: in Automato noch freigegeben – nicht gelöscht" in text
    assert not (host.dir / "kunden" / "kunde-x").exists()
    assert (host.dir / "kunden" / "kunde-a" / "netbox").is_dir()
    assert not (host.dir / "proxy" / "sites" / "kunde-x-nb.caddy").exists()
    # Sicherung vor dem Loeschen: pg_dump vor down -v, Sicherung bleibt
    log = host.log.read_text().splitlines()
    dump = next(i for i, z in enumerate(log) if "/kunde-x/" in z and "pg_dump" in z)
    down = next(i for i, z in enumerate(log) if "/kunde-x/" in z and "down -v" in z)
    assert dump < down
    assert list((tmp_path / "backups" / "kunde-x").glob("kunde-x-*.sql.gz.age"))
    assert (host.dir / "geloescht" / "kunde-x").read_text().strip() == AUFTRAG_X
    stand = json.loads((host.dir / "sync-status.json").read_text())
    assert (stand["geloescht"], stand["loeschen_fehlgeschlagen"], stand["stand_gemeldet"]) == (
        ["kunde-x"], ["kunde-a"], True)  # fmt: skip
    assert "nicht gelöscht: kunde-a" in stand["meldung"]
    # Ist-Stand an Automato: nur Kurznamen und Zeit, das Token nur in der Kopfzeilen-Datei
    gemeldet = _stand(host)[-1]
    assert set(gemeldet) == {"vertrag", "zeit", "instanzen", "ohne_freigabe", "geloescht",
                             "loeschen_fehlgeschlagen"}  # fmt: skip
    assert (gemeldet["instanzen"], gemeldet["geloescht"], gemeldet["loeschen_fehlgeschlagen"]) == (
        ["kunde-a"], ["kunde-x"], ["kunde-a"])  # fmt: skip
    assert TOKEN not in Path(f"{host.log}.curl").read_text()
    assert "PUT" in Path(f"{host.log}.curl").read_text()

    out = host("pruefen")
    assert "auf Auftrag aus Automato gelöscht (mit Sicherung): kunde-x" in out.stdout


def test_je_auftrag_nur_einmal(host):  # noqa: F811
    _vorbereiten(host)
    _antwort_mit_loeschen(host, [{"kurzname": "kunde-a", "name": "A"}],
                          [{"kurzname": "kunde-x", "auftrag": AUFTRAG_X}])  # fmt: skip
    assert host("sync").returncode == 0
    # danach von Hand wieder angelegt: derselbe Auftrag loescht nie noch einmal
    assert host("neu", "kunde-x").returncode == 0
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "kunde-x: nach dem Löschen wieder angelegt – bleibt" in out.stdout + out.stderr
    assert (host.dir / "kunden" / "kunde-x" / "netbox").is_dir()
    assert _stand(host)[-1]["ohne_freigabe"] == ["kunde-x"]
    # ein neuer Auftrag (Abbau eines neuen Kunden gleichen Namens) loescht wieder
    _antwort_mit_loeschen(host, [{"kurzname": "kunde-a", "name": "A"}],
                          [{"kurzname": "kunde-x", "auftrag": AUFTRAG_Y}])  # fmt: skip
    assert host("sync").returncode == 0
    assert not (host.dir / "kunden" / "kunde-x").exists()


def test_ohne_sicherung_wird_nichts_geloescht(host):  # noqa: F811
    _vorbereiten(host)
    with (host.dir / "host.conf").open("a") as f:
        f.write("AGE_RECIPIENT=\n")
    _antwort_mit_loeschen(host, [{"kurzname": "kunde-a", "name": "A"}],
                          [{"kurzname": "kunde-x", "auftrag": AUFTRAG_X}])  # fmt: skip
    out = host("sync")
    assert out.returncode == 1
    assert "kunde-x: nicht gelöscht (ohne Sicherung wird nichts gelöscht)" in out.stderr
    assert (host.dir / "kunden" / "kunde-x" / "netbox").is_dir()
    assert "down -v" not in host.log.read_text()
    assert not (host.dir / "geloescht" / "kunde-x").exists()  # der naechste Abgleich versucht es
    assert _stand(host)[-1]["loeschen_fehlgeschlagen"] == ["kunde-x"]


def test_nie_bei_fremder_domain(host):  # noqa: F811
    _vorbereiten(host)
    _antwort_mit_loeschen(host, [], [{"kurzname": "kunde-x", "auftrag": AUFTRAG_X}],
                          domain="andere.example.ch")  # fmt: skip
    out = host("sync")
    assert (
        "Domain von Automato passt nicht zu diesem Host – keine Löschaufträge ausgeführt"
        in out.stdout
    )
    assert (host.dir / "kunden" / "kunde-x" / "netbox").is_dir()


def test_stand_auch_ohne_auftraege_und_mit_aelterem_automato(host):  # noqa: F811
    _vorbereiten(host)
    _antwort(host, [{"kurzname": "kunde-a", "name": "A"}])  # Automato ohne `loeschen`
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert (host.dir / "kunden" / "kunde-x" / "netbox").is_dir()  # nie selbst entfernt
    assert _stand(host)[-1]["ohne_freigabe"] == ["kunde-x"]
    assert json.loads((host.dir / "sync-status.json").read_text())["stand_gemeldet"] is True
    # Automato kennt PUT /netbox-host/stand nicht (404): nur ein Hinweis
    Path(f"{host.log}.stand-code").write_text("404")
    out = host("sync")
    assert out.returncode == 0
    assert "Automato nimmt den Ist-Stand noch nicht an" in out.stdout
    assert json.loads((host.dir / "sync-status.json").read_text())["stand_gemeldet"] is False
