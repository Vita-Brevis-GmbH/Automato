# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Warum dieser Weg? (0.109.0, Anwender: „weshalb dieser Pfad gewählt wird … dass ich es als
Mensch verstehen kann“): Hop für Hop in Sätzen - Start, Eingang und Tabelle, Policy-Routen,
Routen gewählt bzw. verworfen mit Grund, Next-Hop, Ende mit Hinweis; „Erwartet über …“.

Topologie wie `test_paths.py` (fw-01 → fw-02 zum Servernetz 10.60.0.0/24), dazu fw-03 am
Transfernetz der ACI (10.255.0.4) mit einer kürzeren Route von fw-01 über fw-03.
"""

import copy

import test_paths as tp
from test_app_paths import _connections
from test_app_paths import _seed as _app_seed

SRC, DST = "10.20.0.10", "10.60.0.5"
FW1 = copy.deepcopy(tp.FW1)
FW1["routing"]["routes"].append(
    tp._r("fw-01", "root", "10.60.0.0/16", [{"ip": "10.255.0.4", "interface": "to-aci"}])
)
FW3 = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-03", "role": "firewall"}],
        "interfaces": [tp._if("fw-03", "to-aci", "10.255.0.4/29", "aci"),
                       tp._if("fw-03", "lan", "10.61.0.1/24", "lan")],
    },
    "routing": {
        "contexts": [{"node": "fw-03", "context": "root"}],
        "routes": [
            tp._r("fw-03", "root", "10.255.0.0/29", [{"interface": "to-aci"}], "connected"),
            tp._r("fw-03", "root", "10.61.0.0/24", [{"interface": "lan"}], "connected"),
            tp._r("fw-03", "root", "10.60.0.0/24", [{"ip": "10.255.0.1", "interface": "to-aci"}]),
        ],
    },
}  # fmt: skip


def _seed(client) -> tuple[int, dict[str, int]]:
    tid = tp._seed(client)
    listed = client.get(f"/systems?tenant_id={tid}", headers=tp.ADMIN).json()
    systems = {s["name"]: s["id"] for s in listed}
    tp._deliver(client, tid, systems["fw-01"], "fortigate", FW1)
    systems["fw-03"] = tp._system(client, tid, "fw-03", "fortigate")
    tp._deliver(client, tid, systems["fw-03"], "fortigate", FW3)
    return tid, systems


def _explain(client, tid, **params) -> dict:
    r = client.get("/routing/explain", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _texts(steps) -> list[str]:
    return [s["text"] for s in steps]


def test_weg_hop_fuer_hop_erklaert(client):
    tid, _ids = _seed(client)
    out = _explain(client, tid, src=SRC, dst=DST)
    assert _texts(out["start"]) == [
        "Start bei fw-01 · VDOM root: die Quelle 10.20.0.10 liegt im angeschlossenen Netz "
        "10.20.0.0/24 an Interface srv"
    ]
    (path,) = (p for p in out["paths"] if p["kind"] == "active")
    fw1, fw2 = path["hops"]
    assert fw1["label"] == "fw-01 · VDOM root" and fw1["read_at"] and fw1["stale"] is False
    kinds = [s["kind"] for s in fw1["steps"]]
    assert kinds == ["table", "policy", "routes", "next_hop"]
    assert fw1["steps"][0]["text"] == "Eingang srv (Zone server) – Standard-Routing-Tabelle"
    assert fw1["steps"][1]["text"] == ("1 Policy-Route geprüft – keine passt auf Quelle, Ziel und "
                                       "Eingang")  # fmt: skip
    routes = fw1["steps"][2]
    assert routes["text"] == "Route für 10.60.0.5: 10.60.0.0/24 (static) über 10.70.0.2"
    rows = {r["prefix"]: r for r in routes["items"]}
    chosen = rows["10.60.0.0/24"]
    assert chosen["chosen"] and chosen["next_hops"][0]["owner"] == "fw-02"
    assert rows["10.60.0.0/16"]["why"] == ("kürzeres Präfix /16 – 10.60.0.0/24 ist genauer (das "
                                           "längste passende Präfix gewinnt)")  # fmt: skip
    assert rows["0.0.0.0/0"]["chosen"] is False
    assert fw1["steps"][3]["text"] == (
        "Next-Hop 10.70.0.2 über to-fw02 (Zone transit) gehört fw-02, Interface to-fw01 – dort "
        "geht es weiter")  # fmt: skip
    assert fw2["steps"][0]["text"] == ("Erreicht über Adresse des Nachbarn: 10.70.0.2 ist die "
                                       "Adresse von Interface to-fw01")  # fmt: skip
    assert path["end"] == {"kind": "end", "status": "ok",
                           "text": "Ziel angeschlossen an fw-02 (lan)"}  # fmt: skip

    # Policy-Route geht vor (Quelle im Servernetz, Ziel 192.0.2.0/24): erklärt
    out = _explain(client, tid, src=SRC, dst="192.0.2.10")
    (fw1,) = out["paths"][0]["hops"]
    policy = next(s for s in fw1["steps"] if s["kind"] == "policy")
    assert policy["status"] == "ok" and "geht vor dem Routing: weiter über 198.51.100.5 · wan2" in \
        policy["text"]  # fmt: skip
    routes = next(s for s in fw1["steps"] if s["kind"] == "routes")
    assert all(not r["chosen"] for r in routes["items"])
    assert "die Policy-Route geht aber vor" in routes["items"][0]["why"]
    # Ende an einem Next-Hop ohne gelesenes Gerät: Hinweis mit IPAM-Namen und dem Gerät am Netz
    end = out["paths"][0]["end"]
    assert end["status"] == "stop"
    assert "Kein gelesenes Gerät hat die Adresse 198.51.100.5. Im IPAM: Transfer ISP B " \
        "(198.51.100.4/30)." in end["hint"]  # fmt: skip
    assert "dann als Anbindung erfassen" in end["hint"]


def test_ende_ohne_geraet_und_ohne_route(client):
    tid, _ids = _seed(client)
    # Default-Route ins Internet: Hinweis, dass ggf. eine genauere Route fehlt
    out = _explain(client, tid, src=SRC, dst="203.0.113.9")
    ends = [p["end"] for p in out["paths"] if p["kind"] == "active"]
    assert ends and all(e["status"] == "info" for e in ends)
    assert "fehlt auf diesem Gerät eine genauere Route" in ends[0]["hint"]
    # fw-02 kennt das Netz von fw-03 nicht: keine Route, Hinweis
    out = _explain(client, tid, start="2|root", dst="10.61.0.5")
    (path,) = out["paths"]
    routes = next(s for s in path["hops"][0]["steps"] if s["kind"] == "routes")
    assert routes["status"] == "stop" and "Keine Route enthält 10.61.0.5" in routes["text"]
    assert path["end"]["status"] == "stop"
    assert "fehlt eine Route für das Ziel" in path["end"]["hint"]


def test_erwartet_ueber_ein_geraet(client):
    tid, ids = _seed(client)
    # auf dem Weg
    out = _explain(client, tid, src=SRC, dst=DST, expect=ids["fw-02"])
    assert out["expect"]["on_path"] is True
    assert out["expect"]["steps"][0]["text"] == "Der Weg geht über fw-02 (Hop 2)."
    # nicht auf dem Weg: fw-01 ist mit fw-03 verbunden, die Route über fw-03 verliert
    out = _explain(client, tid, src=SRC, dst=DST, expect=ids["fw-03"])
    ex = out["expect"]
    assert ex["device"] == "fw-03" and ex["on_path"] is False
    texts = _texts(ex["steps"])
    assert texts[0] == (
        "fw-01 · VDOM root (Hop 1) ist direkt mit fw-03 verbunden: Netz "
        "10.255.0.0/29 an to-aci, fw-03 hat dort 10.255.0.4 (Interface to-aci)."
    )
    assert texts[1].startswith(
        "Eine Route über fw-03 gibt es: 10.60.0.0/16 (static) – gewählt ist "
        "aber 10.60.0.0/24 (static) über 10.70.0.2: kürzeres Präfix /16"
    )
    assert "muss diese Route auf fw-01 · VDOM root gewinnen" in ex["steps"][1]["hint"]
    # Sicht von fw-03 selbst: Ziel und Rückweg
    assert ("fw-03 routet das Ziel 10.60.0.5 über 10.60.0.0/24 (static) → 10.255.0.1 (fw-01 · "
            "VDOM root).") in texts  # fmt: skip
    assert "fw-03 kennt keine Route für die Quelle (Rückweg) 10.20.0.10." in texts
    # ohne Routing gelesen: fremdes bzw. unbekanntes Gerät
    ddi = ids["ddi"]
    out = _explain(client, tid, src=SRC, dst=DST, expect=ddi)
    assert out["expect"]["steps"][0]["status"] == "stop"
    assert client.get("/routing/explain", params={"tenant_id": tid, "src": SRC, "dst": DST,
                                                  "expect": 99999}).status_code == 404  # fmt: skip


def test_verbindung_wie_ende_zu_ende(client):
    tid = _app_seed(client)
    conns = _connections(client, tid)["connections"]
    c = conns[0]
    assert set(c["trace"]) == {"src", "dst", "service", "starts"}
    params = {"connection": "true", "src": c["trace"]["src"], "dst": c["trace"]["dst"],
              "service": c["trace"]["service"], "starts": c["trace"]["starts"]}  # fmt: skip
    out = _explain(client, tid, **params)
    hops = [h["label"] for h in out["paths"][0]["hops"]]
    assert hops == [h["label"] for h in c["hops"]]
    assert out["paths"][0]["end"]["status"] == "ok"
    # Englisch
    r = client.get("/routing/explain", params={"tenant_id": tid, **params},
                   headers={"Accept-Language": "en"})  # fmt: skip
    first = r.json()["paths"][0]["hops"][0]["steps"]
    assert any(s["text"].startswith("Route for ") for s in first)


def test_finding_von_hand_bleibt_nach_dem_abgleich(client, session_factory):
    """„Als Finding erfassen“ (0.109.0): Titel, Text, Schweregrad, Weg und Gerät; kein Abgleich
    löscht ihn, Triage wie bei allen Befunden, im Audit ohne die freien Texte."""
    from app.models import AuditLog

    tid, ids = _seed(client)
    body = {"tenant_id": tid, "title": "Route nach 10.60.0.0/24 über fw-03 fehlt auf fw-01",
            "text": "Statische Route ergänzen", "severity": "error",
            "source_system_id": ids["fw-01"], "hop": "fw-01 · VDOM root", "src": SRC,
            "dst": DST, "service": "tcp/443"}  # fmt: skip
    assert client.post("/findings", json=body, headers={"X-Dev-Roles": "viewer"}).status_code == 403
    assert client.post("/findings", json={**body, "source_system_id": 9999},
                       headers=OP).status_code == 404  # fmt: skip
    r = client.post("/findings", json=body, headers=OP)
    assert r.status_code == 201, r.text
    f = r.json()
    assert (f["type"], f["severity"], f["status"]) == ("manual", "error", "open")
    assert f["subject_kind"] == "path" and f["subject_id"] == f"{SRC} → {DST}"
    assert f["message"] == body["title"]
    assert f["hint"] == ("Statische Route ergänzen · Weg: 10.20.0.10 → 10.60.0.5 (tcp/443) · Bei: "
                         "fw-01 · VDOM root · Erfasst von dev")  # fmt: skip
    # neuer Lauf (Abgleich der Befunde): der Befund bleibt
    tp._deliver(client, tid, ids["fw-01"], "fortigate", FW1)
    listed = client.get("/findings", params={"tenant_id": tid, "type": "manual"}).json()
    assert [x["id"] for x in listed] == [f["id"]]
    r = client.patch(f"/findings/{f['id']}", json={"status": "resolved", "note": "ergänzt"},
                     headers=OP)  # fmt: skip
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    s = session_factory()
    (row,) = (a for a in s.query(AuditLog) if a.action == "finding.created")
    s.close()
    assert row.detail["severity"] == "error" and "title" not in row.detail


OP = {"X-Dev-Roles": "operator"}
