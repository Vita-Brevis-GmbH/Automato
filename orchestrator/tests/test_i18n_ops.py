"""Texte vom Server auf Englisch (0.87.0, ADR 0028) - Gruppe E: Betrieb, Verkabelung, Kunden,
Fehlermeldungen der API. Stichproben mit `Accept-Language: en` bzw. `use("en")`; ohne Sprache
bleibt alles deutsch, Gespeichertes (Plan des NetBox-Syncs, Laufmeldungen) ebenso."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import test_connections as tc
import test_netbox_sync as tn
import test_reachability as tr
from app.api import jobs as jobs_api
from app.api import netbox_sync as nb_api
from app.i18n import use
from app.models import SourceSystem, SyncPlan
from app.store import cabling, connections, customers, health, l2_export, netbox_sync, reachability
from app.store.customer_alias import Aliases
from test_auth_oidc import _settings, _signed
from test_reference_sources import _agent, _ddi, _system, _tenant

ADMIN = {"X-Dev-Roles": "admin"}
EN = {"Accept-Language": "en"}
ADMIN_EN = {**ADMIN, **EN}


def test_betriebszustand_englisch_bereich_bleibt(client):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sid = client.post("/systems", json={"tenant_id": tid, "name": "fw-01",
                                        "platform": "fortigate", "base_url": "https://fw"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    de = {c["key"]: c for c in client.get("/system/health", params={"tenant_id": tid},
                                          headers=ADMIN).json()["checks"]}  # fmt: skip
    en = {c["key"]: c for c in client.get("/system/health", params={"tenant_id": tid},
                                          headers=ADMIN_EN).json()["checks"]}  # fmt: skip
    assert de[f"source:{sid}"]["title"] == "fw-01: noch nie eingelesen"
    assert en[f"source:{sid}"]["title"] == "fw-01: never collected"
    assert en["database"]["title"] == "Database reachable, migrations current"
    # Bereich bleibt deutsch: die Oberflaeche ordnet danach
    assert en[f"source:{sid}"]["area"] == de[f"source:{sid}"]["area"] == "Quellsysteme"
    now = datetime.now(UTC)
    with use("en"):
        assert health._ago(now - timedelta(minutes=5), now) == "5 min ago"
        assert health._ago(now - timedelta(days=3), now) == "3 days ago"
        assert health._ago(None, now) == "never"
    assert health._ago(now - timedelta(days=3), now) == "vor 3 Tagen"


def test_erreichbarkeit_englisch_und_merkmal_statt_text(monkeypatch):
    tr._dns(monkeypatch)
    tr._tcp(monkeypatch, TimeoutError())
    with use("en"):
        r = reachability.check("http://host.docker.internal:8080", "netbox", token="t")
    assert r.failure == "timeout"
    assert "no response after 5 s" in r.steps[1].detail and "Docker network" in r.hint
    r = reachability.check("http://host.docker.internal:8080", "netbox", token="t")
    assert "keine Antwort nach 5 s" in r.steps[1].detail and r.failure == "timeout"
    with use("en"):
        assert reachability.check("", "netbox").hint == (
            "No valid address stored on the source system."
        )


def test_verbindungen_englisch_ufw_regel_auch_ohne_deutschen_text(client, monkeypatch, tmp_path):
    """Die ufw-Regel hing am Text „keine Antwort“ des Schritts - jetzt am Merkmal `failure`;
    die Quellsysteme werden in Threads geprueft und folgen trotzdem der Sprache."""
    tc._setup(client)
    tc._routes(monkeypatch, tmp_path)
    tc._redis_ok(monkeypatch)
    tr._dns(monkeypatch)
    tr._tcp(monkeypatch, TimeoutError())
    out = client.get("/system/connections", headers=ADMIN_EN).json()
    items = {i["key"].split(":")[0]: i for i in out["items"]}
    assert (items["database"]["name"], items["database"]["area"]) == ("Database", "Platform")
    nb = items["source"]
    assert nb["area"] == "Source systems" and nb["ufw"]["port"] == 8080
    assert "no response after 5 s" in nb["steps"][1]["detail"]
    assert nb["hint"].startswith("No response from the Docker host")
    de = client.get("/system/connections", headers=ADMIN).json()
    nb_de = next(i for i in de["items"] if i["key"].startswith("source:"))
    assert nb_de["area"] == "Quellsysteme" and nb_de["hint"].startswith("Keine Antwort vom")
    assert connections._redis_name() == "Redis (Warteschlange zum zentralen Worker)"


def test_alle_einlesen_grund_englisch(client):
    tid = _tenant(client)
    agent_id, _ = _agent(client, tid)
    _ddi(client, tid, agent_id=agent_id)
    _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw")
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator)
    again = client.post("/jobs/collect-all", json={"tenant_id": tid},
                        headers={**operator, **EN}).json()  # fmt: skip
    skipped = {i["name"]: i["skipped"] for i in again["items"]}
    assert skipped["ddi-01"].startswith("Run waiting since ")
    assert skipped["ddi-01"].endswith("for agent “agent-a”")
    assert skipped["fw-01"].endswith("for the central worker")


def test_laufmeldung_beim_abruf_englisch_gespeicherte_bleibt(monkeypatch):
    run = SimpleNamespace(id="r1", status=jobs_api.runs.OPEN[0], message="abgebrochen von x")
    monkeypatch.setattr(jobs_api.tenant_lock, "state", lambda rid: "waiting")
    monkeypatch.setattr(jobs_api.tenant_lock, "waiting_before", lambda rid: 2)
    with use("en"):
        assert jobs_api._message(run) == "Result arrived – waiting to be ingested (2 ahead)"
    assert jobs_api._message(run) == "Ergebnis angekommen – wartet aufs Einspielen (2 davor)"
    monkeypatch.setattr(jobs_api.tenant_lock, "state", lambda rid: None)
    with use("en"):  # seit 0.90.0 beim Abruf uebersetzt (test_i18n_runs.py)
        assert jobs_api._message(run) == "cancelled by x"
    assert run.message == "abgebrochen von x"  # gespeichert: deutsch


def _port(**kw):
    base = {"speed": None, "duplex": None, "mode": None, "untagged_vlan": None,
            "native_vlan": None, "trunk_vlans": None, "lag": None, "kind": "physical",
            "source_system_id": 1, "device": "sw-a"}  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def test_verkabelung_abweichungen_englisch():
    cab = cabling.Cabling.__new__(cabling.Cabling)
    cab.switch_sources = {1}
    a = _port(mode="access", untagged_vlan=10, lag="Po1")
    b = _port(mode="tagged", trunk_vlans="10,20", device="sw-b")
    dev_a = cabling.Device("sw-a", "sw-a", "switch", read=True)
    dev_b = cabling.Device("sw-b", "sw-b", "switch", read=True)
    link = cabling.Link("x", cabling.End(dev_a, "Gi1/0/1", a), cabling.End(dev_b, "Gi1/0/2", b))
    with use("en"):
        texts = [i["text"] for i in cab._check(link)]
    assert texts == ["Mode Access ↔ Trunk", "Port-Channel only on sw-a (Po1)"]
    assert [i["text"] for i in cab._check(link)] == [
        "Modus Access ↔ Trunk", "Port-Channel nur auf sw-a (Po1)"]  # fmt: skip
    trunk = _port(mode="tagged", trunk_vlans="10-12", device="sw-b")
    with use("en"):
        assert cabling.Cabling._vlans(_port(mode="tagged", trunk_vlans="10"), trunk) == (
            "only on sw-b: 11-12"
        )


PLAN = {
    "width": 400, "height": 200, "vlan": {"vid": 30, "islands": [["a"], ["b"]]},
    "nodes": [
        {"key": "a", "name": "sw-a", "kind": "switch", "switching": True, "read": True,
         "x": 100, "y": 50},
        {"key": "b", "name": "nb-x", "kind": "unknown", "switching": False, "read": False,
         "x": 300, "y": 50},
    ],
    "edges": [{"a": "a", "b": "b", "links": 2, "issues": [], "state": "both", "stp": "30"}],
    "pairs": [{"a": "a", "b": "b", "domain": 100}],
}  # fmt: skip


def test_export_der_verkabelung_englisch():
    with use("en"):
        svg = l2_export.svg(PLAN, "Cabling")
        drawio = l2_export.drawio(PLAN, "Cabling")
    assert "vPC domain 100" in svg and "STP blocked 30" in svg and "2 islands" in svg
    assert "not read" in svg and "1 forwarding devices" in svg and "As of" in svg
    assert 'name="Cabling"' in drawio and "vPC domain 100" in drawio
    assert 'automato_kind="nicht gelesen"' in drawio  # Wert bleibt deutsch
    de = l2_export.svg(PLAN, "Verkabelung")
    assert "vPC-Domäne 100" in de and "STP blockiert 30" in de and "nicht gelesen" in de


def test_netbox_sync_anzeige_englisch():
    with use("en"):
        assert netbox_sync.show_label("Gerät fw-01") == "Device fw-01"
        assert netbox_sync.show_label("Standort ZRH Zürich (physisch)") == (
            "Site ZRH Zürich (physical)"
        )
        assert netbox_sync.show_label("Standort-Gruppe standorte-virtuell") == (
            "Site group standorte-virtuell"
        )
        assert netbox_sync.show_label("Virtuelle Standorte") == "Virtuelle Standorte"  # Name
        assert netbox_sync.kind_label("ip_address") == "IP address"
        assert (
            nb_api.show_message(
                "Übernahme abgebrochen: Orchestrator wurde neu gestartet" + nb_api.PARTIAL
            )
            == "Apply aborted: orchestrator was restarted" + nb_api.PARTIAL_EN
        )
        assert nb_api.show_message("schreibt: Gerät") == "writing: Device"
        assert nb_api.show_message(
            "Vorschau fehlgeschlagen: NetBox nicht erreichbar (nb): ConnectError – Verbindung "
            "abgelehnt bzw. Name unbekannt (Quellsystem → Prüfen)"
        ) == ("Preview failed: NetBox not reachable (nb): ConnectError – connection refused or "
              "name unknown (source system → Check)")  # fmt: skip
        assert nb_api.show_message("unbekannte Meldung") == "unbekannte Meldung"
    assert netbox_sync.show_label("Gerät fw-01") == "Gerät fw-01"
    assert nb_api.show_message("schreibt: Gerät") == "schreibt: Gerät"


def test_netbox_sync_plan_deutsch_gespeichert_anzeige_englisch(
    client, monkeypatch, session_factory
):
    """Vorschau in einer englischen Anfrage: Plan und was nach NetBox ginge bleiben deutsch
    (Hintergrundaufgabe `@german_only`), Hinweise und Bezeichnungen werden bei der Ausgabe
    uebersetzt."""
    nb = tn.fake_netbox(monkeypatch)
    ids = tn._setup(client)
    client.post(f"/tenants/{ids['tid']}/sites", json={"code": "BRN", "name": "Bern"}, headers=ADMIN)
    body = {"tenant_id": ids["tid"], "source_system_id": ids["nb"]}
    r = client.post("/netbox-sync/plans", json=body, headers=ADMIN_EN)
    assert r.status_code == 202, r.text
    plan = client.get(f"/netbox-sync/plans/{r.json()['id']}", headers=ADMIN_EN).json()
    assert plan["status"] == "ready", plan
    assert any(w.startswith("apic-01: no site set") for w in plan["warnings"])
    s = session_factory()
    stored = s.get(SyncPlan, plan["id"])
    assert any("kein Standort gesetzt" in w for w in stored.warnings)
    assert any(i["label"].startswith("Gerät ") for i in stored.items)
    s.close()
    rows = client.get(f"/netbox-sync/plans/{plan['id']}/items", params={"kind": "device"},
                      headers=ADMIN_EN).json()["rows"]  # fmt: skip
    assert [i["label"] for i in rows] == ["Device fw-01"]
    rows_de = client.get(f"/netbox-sync/plans/{plan['id']}/items", params={"kind": "device"},
                         headers=ADMIN).json()["rows"]  # fmt: skip
    assert [i["label"] for i in rows_de] == ["Gerät fw-01"]
    # Uebernahme in einer englischen Anfrage: nach NetBox geht deutsch
    r = client.post(f"/netbox-sync/plans/{plan['id']}/apply", headers=ADMIN_EN)
    assert r.status_code == 202, r.text
    done = client.get(f"/netbox-sync/plans/{plan['id']}", headers=ADMIN_EN).json()
    assert done["status"] == "applied", done
    assert done["message"] == "Applied – NetBox is being collected again."
    tag = nb.find("extras/tags", slug="automato")
    assert tag["description"] == "Von Automato verwaltet (ADR 0010) - nicht von Hand ändern"


def test_gruende_der_kunden_englisch(client):
    tid = client.post("/tenants", json={"name": "Umgebung K"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "Kunde A",
            "selectors": [{"kind": "prefix", "value": "10.20.0.0/16"}]}  # fmt: skip
    cid = client.post("/customers", json=body, headers=ADMIN).json()["id"]
    en = client.get(f"/customers/{cid}/members", params={"kind": "net"}, headers=ADMIN_EN).json()
    de = client.get(f"/customers/{cid}/members", params={"kind": "net"}, headers=ADMIN).json()
    assert [m["via"] for m in en] == ["Network 10.20.0.0/16"]
    assert [m["via"] for m in de] == ["Netz 10.20.0.0/16"]  # gespeichert und deutsch
    with use("en"):
        assert customers.show_reason("Kürzel im Namen") == "Code in the name"
        assert customers.show_reason("Kürzel ka") == "Code ka"
        assert customers.show_reason("EPG TE/AP/EPG") == "EPG TE/AP/EPG"
    # Grund bleibt intern deutsch (zone_detect vergleicht `Anbindung …`)
    with use("en"):
        assert customers._label(customers.Selector("exit", "Internet")) == "Anbindung Internet"


def test_neutrale_namen_englisch(client, session_factory):
    tid = client.post("/tenants", json={"name": "Umgebung N"}, headers=ADMIN).json()["id"]
    ids = [client.post("/systems", json={"tenant_id": tid, "name": n, "platform": "fortigate",
                                         "base_url": f"https://{n}"}, headers=ADMIN).json()["id"]
           for n in ("fw-a", "fw-b")]  # fmt: skip
    s = session_factory()
    for sid in ids:
        s.get(SourceSystem, sid).parallel_group = "pg-dc"
    s.commit()
    with use("en"):
        assert Aliases(s, [tid]).names["pg-dc"] == "Parallel group 1"
    assert Aliases(s, [tid]).names["pg-dc"] == "Verbund 1"
    s.close()


def test_fehlermeldungen_der_api_englisch(client, monkeypatch):
    tid = client.post("/tenants", json={"name": "Umgebung F"}, headers=ADMIN).json()["id"]
    r = client.get("/cabling/failure", params={"tenant_id": tid}, headers=ADMIN_EN)
    assert r.status_code == 422 and r.json()["detail"] == "specify device or link"
    r = client.get("/cabling/failure", params={"tenant_id": tid}, headers=ADMIN)
    assert r.json()["detail"] == "device oder link angeben"
    two = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": f"{tid},{tid + 1}", **EN}
    r = client.get("/cabling/devices", headers=two)
    assert r.status_code == 400 and r.json()["detail"] == "tenant_id required (several tenants)"
    # OIDC: Grund in der Sprache der Anfrage (das Log bleibt deutsch)
    iss = "https://kc/realms/automato-dev"
    settings = _settings(oidc_issuer=iss, oidc_audience="automato-api")
    monkeypatch.setattr("app.auth.deps.get_settings", lambda: settings)
    claims = {"sub": "u", "iss": iss, "aud": "account", "realm_access": {"roles": ["admin"]}}
    token = _signed(claims, monkeypatch)
    r = client.get("/tenants", headers={"Authorization": f"Bearer {token}", **EN})
    assert r.status_code == 401
    assert r.json()["detail"] == "token audience invalid (expected: automato-api)"
    r = client.get("/tenants", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["detail"] == "token audience invalid (erwartet: automato-api)"
