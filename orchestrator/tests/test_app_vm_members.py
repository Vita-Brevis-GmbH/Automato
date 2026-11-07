"""Applikationen (0.124.0, Anwender 2026-11-03): einzelne VMs als Mitglied und die Liste ohne
Abfrage je Mitglied („Das Laden der Applikationen extrem langsam“)."""

from app.models import Rule
from app.store.applications import rule_ref
from sqlalchemy import event, select
from test_vmware import _seed

OP = {"X-Dev-Roles": "operator"}


def _create(client, tid, name, members):
    r = client.post(f"/applications?tenant_id={tid}", json={"name": name, "members": members},
                    headers=OP)  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def _add(client, app_id: int, vm: str):
    body = {"members": [{"kind": "vm", "ref": vm}]}
    return client.post(f"/applications/{app_id}/members", json=body, headers=OP)


def test_vm_als_mitglied(client):
    ids = _seed(client)
    tid = ids["tid"]
    # Kandidaten: VMs aus dem vCenter mit IPs und Cluster, ohne Vorlagen
    vms = client.get(f"/applications/candidates?tenant_id={tid}&kind=vm").json()
    by_ref = {c["ref"]: c for c in vms}
    assert set(by_ref) == {"web01-007-ka", "db01-007-ka"}  # tpl-ubuntu ist eine Vorlage
    assert by_ref["db01-007-ka"]["detail"] == "10.20.1.7, 10.95.0.9 · clu01-chzrh1"
    assert by_ref["web01-007-ka"]["project"] == "007"
    found = client.get(f"/applications/candidates?tenant_id={tid}&q=db01").json()
    assert ("vm", "db01-007-ka") in {(c["kind"], c["ref"]) for c in found}

    app = _create(client, tid, "Nur VM", [{"kind": "vm", "ref": "web01-007-ka"}])
    (member,) = app["members"]
    assert (member["kind"], member["label"], member["exists"]) == ("vm", "web01-007-ka", True)
    assert member["systems"] == ["web01-007-ka"]
    assert (app["system_count"], app["vm_count"]) == (1, 1)
    listed = {a["name"]: a for a in client.get(f"/applications?tenant_id={tid}").json()}
    assert (listed["Nur VM"]["system_count"], listed["Nur VM"]["vm_count"]) == (1, 1)
    # bereits zugeordnet
    vms = client.get(f"/applications/candidates?tenant_id={tid}&kind=vm").json()
    assert {c["ref"]: c["applications"] for c in vms}["web01-007-ka"] == [app["id"]]

    # Doku: die VM als System mit Grund, ihre Regeln (10.20.0.5) wie bei einer VM ueber EPG
    doc = client.get(f"/applications/{app['id']}/doc").json()
    (vm,) = doc["systems"]
    assert (vm["kind"], vm["name"], vm["ips"]) == ("vm", "web01-007-ka", ["10.20.0.5"])
    assert vm["member"] == "VM web01-007-ka"
    assert doc["summary"]["vms"] == 1
    assert any(h["system"] == vm["key"] for h in doc["hits"])
    assert any(r["name"] == "web-in" for r in doc["rules"])

    # unbekannte VM: abgelehnt; weitere VM hinzufuegen; Vorlagen sind keine Systeme
    assert _add(client, app["id"], "gibt-es-nicht").status_code == 422
    more = _add(client, app["id"], "db01-007-ka")
    assert more.status_code == 200 and more.json()["vm_count"] == 2
    assert _add(client, app["id"], "tpl-ubuntu").status_code == 422


def test_vm_und_server_zaehlen_einmal(client):
    """Server mit derselben IP wie die VM: eine VM, zwei Systeme (Server + VM-Mitglied)."""
    ids = _seed(client)
    tid = ids["tid"]
    app = _create(client, tid, "Beides", [{"kind": "host", "ref": "10.20.0.5"},
                                          {"kind": "vm", "ref": "web01-007-ka"}])  # fmt: skip
    assert app["vm_count"] == 1
    doc = client.get(f"/applications/{app['id']}/doc").json()
    (vm,) = doc["systems"]
    assert vm["member"] == "VM web01-007-ka; IP 10.20.0.5 = Server 10.20.0.5 (Server 10.20.0.5)"


def test_liste_ohne_abfrage_je_mitglied(client, session_factory):
    """Die Liste fragt Server, Regeln und VMs gebuendelt ab - die Zahl der Abfragen haengt nicht
    von der Zahl der Applikationen und Mitglieder ab (vorher je Mitglied eine, Regeln doppelt)."""
    ids = _seed(client)
    tid = ids["tid"]
    s = session_factory()
    ref = rule_ref(s.scalars(select(Rule).where(Rule.tenant_id == tid)).first())
    s.close()

    def queries() -> int:
        engine = session_factory.kw["bind"]
        count = [0]

        def hit(*_a, **_k):
            count[0] += 1

        event.listen(engine, "before_cursor_execute", hit)
        try:
            r = client.get(f"/applications?tenant_id={tid}")
        finally:
            event.remove(engine, "before_cursor_execute", hit)
        assert r.status_code == 200
        return count[0]

    members = [{"kind": "host", "ref": "10.20.0.5"}, {"kind": "vm", "ref": "db01-007-ka"},
               {"kind": "rule", "ref": ref}, {"kind": "ap", "ref": "TE_ka/AP_007"}]  # fmt: skip
    for i in range(3):
        _create(client, tid, f"App {i}", members)
    few = queries()
    for i in range(3, 30):
        _create(client, tid, f"App {i}", members)
    many = queries()
    assert many <= few + 2, (few, many)
