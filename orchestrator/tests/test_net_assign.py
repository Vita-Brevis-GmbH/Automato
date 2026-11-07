"""Grosse Netze der Regeln zuordnen (0.111.0, ADR 0040; Anwender 2026-10-29: ein Objekt
10.0.0.0/8 in der Regel, dessen Teile einzeln geroutet werden, landete bei 0.0.0.0/0 - gewaehlt:
kombiniert). Reihenfolge: von Hand, Zone, Anbindung, Teile nach dem Routing; sonst unklar mit
Befund `net_wider_than_routes`."""

import copy

from test_policy_check import ADMIN, FW, TOKEN, _policy

OP = {"X-Dev-Roles": "operator"}
VIEWER = {"X-Dev-Roles": "viewer"}
EN = {"Accept-Language": "en"}


def _seed(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = copy.deepcopy(FW)
    # alle privaten Netze: 10/8 nur ueber die Default-Route, das DMZ-Netz darin genauer
    fw["rules"] = [
        _policy(40, "lan-to-private", "lan", "any", ["10.10.0.5"], ["10.0.0.0/8"],
                ["tcp/443"]) | {"destination_refs": ["N_private"]},
        *fw["rules"],
    ]  # fmt: skip
    fw["objects"] = [{"name": "N_private", "category": "address", "values": ["10.0.0.0/8"]}]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=fw)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    body = {"name": "Server", "members": [{"kind": "host", "ref": "10.10.0.5"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    return tid, sid, app["id"]


def _conns(client, aid, headers=None) -> dict[str, dict]:
    r = client.get(f"/applications/{aid}/connections", headers=headers or {})
    assert r.status_code == 200, r.text
    return {c["dst"]["ip"]: c for c in r.json()["connections"]}


def _findings(client, tid) -> list[dict]:
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    return client.get("/findings", params={"tenant_id": tid,
                                           "type": "net_wider_than_routes"}).json()  # fmt: skip


def _assign(client, tid, headers=OP, **body):
    return client.put("/routing/net-assignments", json={"tenant_id": tid, **body},
                      headers=headers)  # fmt: skip


def test_netz_groesser_als_die_routen(client, session_factory):
    tid, sid, aid = _seed(client)

    # ohne Zuordnung: Teile nach dem Routing, der Rest unklar - mit Befund
    conns = _conns(client, aid)
    whole = conns["10.0.0.0/8"]
    assert whole["assign"]["state"] == "split" and whole["assign"]["how"] == "routing"
    assert "Teile genauer geroutet, der Rest nur über 0.0.0.0/0" in whole["assign"]["text"]
    assert whole["assign"]["open"] and "10.30.0.0/24" in conns
    (f,) = _findings(client, tid)
    assert f["subject_id"].startswith("10.0.0.0/8 · ") and f["severity"] == "warning"
    assert f["detail"]["route"] == "0.0.0.0/0" and "10.30.0.0/24" in f["detail"]["parts"]
    assert f["detail"]["rules"] == ["lan-to-private"]
    assert "routet 10.0.0.0/8 nicht als Ganzes, verwendet in 1 Regel" in f["message"]
    assert "zuordnen" in f["hint"]

    # Fehler und Rechte
    assert _assign(client, tid, prefix="10.0.0.5", kind="ignore").status_code == 422  # Host
    assert _assign(client, tid, prefix="0.0.0.0/0", kind="ignore").status_code == 422  # any
    assert _assign(client, tid, prefix="10.0.0.0/8", kind="zone").status_code == 422
    assert _assign(client, tid, prefix="10.0.0.0/8", kind="exit").status_code == 422
    assert _assign(client, tid, VIEWER, prefix="10.0.0.0/8", kind="ignore").status_code == 403

    # von Hand: nur die Teile zaehlen - das ganze Netz ist keine Verbindung mehr, kein Befund
    r = _assign(client, tid, prefix="10.1.2.3/8", kind="ignore", note="RFC 1918")
    assert r.status_code == 200, r.text
    assert (r.json()["prefix"], r.json()["kind"]) == ("10.0.0.0/8", "ignore")
    conns = _conns(client, aid)
    assert "10.0.0.0/8" not in conns and "10.30.0.0/24" in conns
    assert _findings(client, tid) == []

    # von Hand: der Weg nach dem Routing stimmt (dieselbe Zeile, geaendert)
    r = _assign(client, tid, prefix="10.0.0.0/8", kind="route")
    assert r.status_code == 200
    whole = _conns(client, aid)["10.0.0.0/8"]
    assert (whole["assign"]["state"], whole["assign"]["how"]) == ("route", "manual")
    listed = client.get("/routing/net-assignments", params={"tenant_id": tid}).json()
    assert [(a["prefix"], a["kind"], a["note"]) for a in listed] == [
        ("10.0.0.0/8", "route", None)]  # PUT ersetzt die Zeile ganz  # fmt: skip

    # Anbindung mit dem Netz dahinter (nach dem Loeschen der Zuordnung von Hand)
    r = client.delete(f"/routing/net-assignments/{listed[0]['id']}", headers=OP)
    assert r.status_code == 204
    body = {"tenant_id": tid, "name": "MPLS Kunde A", "kind": "mpls", "interfaces": ["wan"],
            "prefixes": ["10.0.0.0/8"]}  # fmt: skip
    ex = client.post("/routing/exits", json=body, headers=OP)
    assert ex.status_code == 201, ex.text
    whole = _conns(client, aid)["10.0.0.0/8"]
    assert (whole["assign"]["state"], whole["assign"]["exit"]) == ("exit", "MPLS Kunde A")
    assert _findings(client, tid) == []
    # von Hand ueber die Anbindung, Englisch
    r = _assign(client, tid, prefix="10.0.0.0/8", kind="exit", exit_id=ex.json()["id"])
    assert r.json()["exit"] == "MPLS Kunde A"
    en = _conns(client, aid, EN)["10.0.0.0/8"]
    assert en["assign"]["text"] == "via exit MPLS Kunde A (manual)"

    # Audit ohne Notiz
    from app.models import AuditLog

    s = session_factory()
    rows = [a for a in s.query(AuditLog) if a.action.startswith("net_assignment.")]
    s.close()
    assert [a.action for a in rows] == ["net_assignment.set", "net_assignment.set",
                                        "net_assignment.deleted", "net_assignment.set"]  # fmt: skip
    assert set(rows[0].detail) == {"tenant_id", "prefix", "kind", "zone_id", "exit_id"}


def test_wegsucher_aus(client):
    tid, _, _ = _seed(client)
    client.put("/modules/wege", params={"tenant_id": tid}, json={"enabled": False},
               headers=ADMIN)  # fmt: skip
    r = client.get("/routing/net-assignments", params={"tenant_id": tid})
    assert r.status_code == 403 and r.json()["detail"]["module"] == "wege"
    assert _findings(client, tid) == []  # die Pruefung gehoert zum Wegsucher
