"""Reihenfolge der Kategorien (0.126.0, Anwender 2026-11-03: „die Reihenfolge noch anpassen“ –
„Ordnen wie beim Regelwerk“, „Wirkung ja“, „Bei Gleichstand ja“, „Nummerierung fortlaufend“):
im Namensprofil mit ↑/↓, Doku und Word-Export in dieser Reihenfolge mit fortlaufenden Nummern, bei
gleich genauer Richtung gewinnt die weiter oben; die Kennung bleibt (ausgeblendete Regeln).

Umgebung wie in `test_appdoc_split` (Webshop mit web01, fw-04, Zonen mit Zonentypen).
"""

import re

from app.store import appdoc_catalog
from test_appdoc_catalog import VIEW, _default_profile
from test_appdoc_names import ADMIN, OP
from test_appdoc_split import _setup
from test_appdoc_zones import _where
from test_word_export import _text


def _cats(cat: dict) -> list[dict]:
    keys = ("index", "title", "title_en", "src", "dst", "src_scope", "dst_scope")
    return [{k: c[k] for k in keys} for c in cat["categories"]]


def _moved(cats: list[dict], index: int, before: int) -> list[dict]:
    """Kategorie `index` vor die Kategorie `before` schieben."""
    item = next(c for c in cats if c["index"] == index)
    rest = [c for c in cats if c["index"] != index]
    at = next(i for i, c in enumerate(rest) if c["index"] == before)
    return [*rest[:at], item, *rest[at:]]


def test_reihenfolge_im_katalog():
    cats = [{"index": c.index, "title": c.title, "title_en": c.title_en, "src": list(c.src),
             "dst": list(c.dst)} for c in appdoc_catalog.default().ordered()]  # fmt: skip
    # ohne Aenderung: nach Nummer, nichts gespeichert
    plain = appdoc_catalog.validate({"categories": cats})
    assert plain.order == () and appdoc_catalog.entries_of(plain) == []
    # 9 vor 3: Reihenfolge, fortlaufende Nummern, gespeichert als ein Eintrag
    cat = appdoc_catalog.validate({"categories": _moved(cats, 9, 3)})
    assert [c.index for c in cat.ordered()] == [1, 2, 9, 3, 4, 5, 6, 7, 8, 10, 11]
    assert cat.numbers()[9] == 3 and cat.number(3) == 4 and cat.number(11) == 11
    assert cat.out()["categories"][2]["index"] == 9 and cat.out()["categories"][2]["number"] == 3
    stored = appdoc_catalog.entries_of(cat)
    assert stored == [("appdoc_category", "order", "Reihenfolge",
                       {"order": [1, 2, 9, 3, 4, 5, 6, 7, 8, 10, 11]})]  # fmt: skip
    entries = [appdoc_catalog.DictionaryEntry(kind=k, key=key, value=v, extra=x)
               for k, key, v, x in stored]  # fmt: skip
    back = appdoc_catalog.apply_entries(appdoc_catalog.default(), entries)
    assert [c.index for c in back.ordered()] == [c.index for c in cat.ordered()]
    # fehlende feste Kategorien am Ende; unbekannte Nummern in der Reihenfolge uebergangen
    short = appdoc_catalog.validate({"categories": [cats[10], cats[0]]})
    assert [c.index for c in short.ordered()] == [11, 1, 7, 8, 10]
    odd = appdoc_catalog.apply_entries(appdoc_catalog.default(), [
        appdoc_catalog.DictionaryEntry(kind="appdoc_category", key="order", value="",
                                       extra={"order": [3, "x", 3, 50, 1]})])  # fmt: skip
    assert [c.index for c in odd.ordered()][:3] == [3, 1, 2]


def test_gleichstand_weiter_oben():
    cats = [{"index": c.index, "title": c.title, "title_en": c.title_en, "src": list(c.src),
             "dst": list(c.dst)} for c in appdoc_catalog.default().ordered()]  # fmt: skip
    other = {"index": 12, "title": "Andere DC", "src": ["DC"], "dst": ["DC"]}
    cat = appdoc_catalog.validate({"categories": [*cats, other]})
    assert cat.by_flow("DC", "DC") == 9  # gleich genau: die weiter oben (hier 9)
    moved = appdoc_catalog.validate({"categories": _moved([*cats, other], 12, 9)})
    assert moved.by_flow("DC", "DC") == 12
    assert moved.by_flow("Edge VPN", "DC") == 5  # genauer bleibt genauer


def test_reihenfolge_in_doku_und_export(client, session_factory):
    tid, app, ids = _setup(client)
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-catalog"
    cat = client.get(url, headers=VIEW).json()
    assert [c["number"] for c in cat["categories"]] == list(range(1, 12))
    # ausgeblendet in Kategorie 9 (Kennung) - bleibt nach dem Verschieben
    doc = client.get(f"/applications/{app['id']}/doc").json()
    rid = next(r["id"] for r in doc["rules"] if r["name"] == "web-to-many")
    hide = {"rule_ids": [rid], "category": 9, "reason": "anderes DC separat"}
    assert (
        client.post(f"/applications/{app['id']}/hidden", json=hide, headers=OP).status_code == 201
    )
    # 9 vor 3 und 10 an den Anfang (auch feste); nur Admin
    body = {"categories": _moved(_moved(_cats(cat), 9, 3), 10, 1)}
    assert client.put(url, json=body, headers=OP).status_code == 403
    r = client.put(url, json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    got = [(c["index"], c["number"]) for c in r.json()["categories"]]
    assert got[:5] == [(10, 1), (1, 2), (2, 3), (9, 4), (3, 5)]
    assert [c["index"] for c in client.get(url, headers=VIEW).json()["categories"]][:4] == [
        10, 1, 2, 9]  # fmt: skip

    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert [(t["index"], t["number"]) for t in doc["tables"]][:4] == [(10, 1), (1, 2), (2, 3),
                                                                      (9, 4)]  # fmt: skip
    t9 = next(t for t in doc["tables"] if t["index"] == 9)
    assert [h["Description"] for h in t9["hidden"]] == ["web-to-many"]  # Kennung bleibt
    assert _where(doc)["web-to-many"] == {3, 6}  # ohne die ausgeblendete in 9
    # Word: Ueberschriften fortlaufend in der Reihenfolge
    word = client.get(f"/applications/{app['id']}/export", params={"format": "docx"})
    text, heads = _text(word.content)
    numbered = [h for h in heads if re.match(r"\d+\. ", h)]
    assert numbered[:3] == ["1. Unspezifische Zugriffe auf die Applikation",
                            "4. Zugriff zu anderen Security Tenants",
                            "5. Zugriff in das Unternehmensnetzwerk"], heads  # fmt: skip
    assert "2. Internet Zugriff" in text  # leere Kategorien mit ihrer Nummer
    # zuruecksetzen: wieder nach Nummer
    assert client.delete(url, headers=ADMIN).status_code == 200
    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert [t["index"] for t in doc["tables"]] == list(range(1, 12))
    assert all(t["number"] == t["index"] for t in doc["tables"])
