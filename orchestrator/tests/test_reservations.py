"""Reservierungen von VLAN- und AS-Nummern (1.19.0, ADR 0066; Anwender 2026-10-30 Roadmap
Version 2 Nr. 10 und 2026-11-17 Prioritaet 6 „Reservationen auch ins Netbox“).

Umgebung: Mandant „Kunde A“ mit den Standorten Zürich und Bern; Switches in Zürich (VLAN 50, 120)
und Bern (VLAN 50, 220); eine FortiGate (AS 65001) mit Nachbarn ISP (AS 64500), Standort (AS
65100) und einem abgeschalteten, nur konfigurierten Nachbarn (AS 64502); ein Router (lokal AS
65001, Nachbar AS 65200) und ein APIC mit einem L3Out-Peer (AS 65010).
"""

import httpx
import pytest
from app.i18n import use
from app.models import AuditLog, Reservation, Tenant
from app.store import as_numbers, data_state, doc_snapshots, netbox_sync, reservations
from app.store.finding_text import explain
from test_netbox_sync import _FK, FakeNetBox, _reader
from test_netbox_sync import api as sync_api

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
VIEWER = {"X-Dev-Roles": "viewer"}
TOKEN = {"X-Internal-Token": "dev-only-token"}

ISP, SITE, DOWN, RT_PEER = "198.51.100.1", "10.60.1.1", "198.51.100.13", "10.3.0.2"


def _switches(device: str, vlans: list[tuple[int, str]]) -> dict:
    return {
        "status": "completed",
        "inventory": {
            "devices": [{"name": device, "role": "switch", "model": "C9300-48P"}],
            "vlans": [{"vid": vid, "name": name} for vid, name in vlans],
        },
    }


FW = {
    "status": "completed",
    "routing": {
        "contexts": [{"node": "fw-01", "context": "root"}],
        "routes": [],
        "peers": [
            {"node": "fw-01", "context": "root", "peer_ip": ISP, "remote_as": 64500,
             "state": "Established", "attrs": {"config": {"remote_as": "64500",
                                                          "description": "Provider"}}},
            {"node": "fw-01", "context": "root", "peer_ip": SITE, "remote_as": 65100,
             "state": "Idle", "attrs": {"config": {"remote_as": "65100"}}},
        ],
        "bgp": [
            {"node": "fw-01", "context": "root", "kind": "bgp", "name": "bgp",
             "data": {"as": "65001", "router_id": "10.0.0.1",
                      "neighbors": {ISP: {}, DOWN: {"shutdown": True, "remote_as": "64502"}}}},
        ],
    },
}  # fmt: skip

ROUTER = {
    "status": "completed",
    "routing": {
        "contexts": [{"node": "rt-01", "context": "rt-01", "kind": "l3"}],
        "routes": [],
        "peers": [{"node": "rt-01", "context": "rt-01", "peer_ip": RT_PEER, "remote_as": 65200,
                   "state": "Established", "attrs": {"local_as": 65001}}],
    },
}  # fmt: skip

APIC = {
    "status": "completed",
    "routing": {
        "contexts": [{"node": "leaf-101", "context": "TE:VRF1", "kind": "vrf"}],
        "routes": [],
        "l3outs": [{"tenant": "TE", "name": "L3O_wan", "dn": "uni/tn-TE/out-L3O_wan",
                    "vrf": "TE:VRF1", "peers": [{"ip": "10.255.0.1", "asn": 65010}]}],
    },
}  # fmt: skip


def _system(client, tid, **body) -> int:
    r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _deliver(client, tid, sid, platform, result) -> None:
    req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup(client, *, bgp: bool = True) -> dict:
    tid = client.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=ADMIN).json()
    tid = tid["id"]
    ids: dict = {"tid": tid}
    for code, name in (("chzrh1", "Zürich"), ("chbrn1", "Bern")):
        r = client.post(f"/tenants/{tid}/sites", json={"code": code, "name": name}, headers=ADMIN)
        ids[code] = r.json()["id"]
    for code, device, vlans in (
        ("chzrh1", "sw-zrh-01", [(50, "Server"), (120, "Clients")]),
        ("chbrn1", "sw-brn-01", [(50, "Server"), (220, "Drucker")]),
    ):
        sid = _system(
            client,
            tid,
            name=f"Switches {code}",
            platform="cisco_switch",
            read_scope="10.0.0.11",
            username="ro",
            password="x",
            site_id=ids[code],
        )
        ids[f"sw-{code}"] = sid
        _deliver(client, tid, sid, "cisco_switch", _switches(device, vlans))  # fmt: skip
    if bgp:
        ids["fw"] = _system(client, tid, name="fw-01", platform="fortigate",
                            base_url="https://fw.example.ch")  # fmt: skip
        _deliver(client, tid, ids["fw"], "fortigate", FW)
        ids["rt"] = _system(client, tid, name="rt-01", platform="cisco_switch",
                            read_scope="10.3.0.1", username="ro", password="x")  # fmt: skip
        _deliver(client, tid, ids["rt"], "cisco_switch", ROUTER)
        ids["apic"] = _system(client, tid, name="apic-01", platform="cisco_aci",
                              base_url="https://apic.example.ch")  # fmt: skip
        _deliver(client, tid, ids["apic"], "cisco_aci", APIC)
    return ids


def _reserve(client, tid, kind="vlan", low=300, high=None, headers=OP, **kw):
    body = {"tenant_id": tid, "kind": kind, "low": low, "high": high,
            "purpose": kw.pop("purpose", "Projekt X"), **kw}  # fmt: skip
    return client.post("/reservations", json=body, headers=headers)


def _list(client, tid, **params) -> dict:
    r = client.get("/reservations", params={"tenant_id": tid, **params}, headers=VIEWER)
    assert r.status_code == 200, r.text
    return r.json()


def _findings(client, tid) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for f in client.get("/findings", params={"tenant_id": tid, "limit": 5000}).json():
        if f["type"].startswith("reservation_"):
            out.setdefault(f["type"], {})[f["subject_id"]] = f
    return out


# -- API ------------------------------------------------------------------------------------


def test_anlegen_aendern_freigeben(client, session_factory):
    ids = _setup(client, bgp=False)
    tid, zrh = ids["tid"], ids["chzrh1"]
    r = _reserve(client, tid, low=300, high=309, site_id=zrh, vlan_group="kunde-a-zrh")
    assert r.status_code == 201, r.text
    row = r.json()
    assert (row["label"], row["count"], row["state"]) == ("VLAN 300–309", 10, "free")
    assert row["site"]["name"] == "Zürich" and row["created_by"] == "dev"
    assert row["used_before"] == [] and row["in_use"] == [] and row["overlaps"] == []
    rid = row["id"]

    # aendern: Zweck und Bereich; wer und wann
    body = {"tenant_id": tid, "kind": "vlan", "low": 300, "high": 305, "site_id": zrh,
            "purpose": "Projekt Y"}  # fmt: skip
    r = client.put(f"/reservations/{rid}", json=body, headers=OP)
    assert r.status_code == 200, r.text
    assert (r.json()["label"], r.json()["purpose"], r.json()["vlan_group"]) == (
        "VLAN 300–305", "Projekt Y", None)  # fmt: skip
    assert r.json()["changed_by"] == "dev"

    # AS ohne Standort; Liste mit Filter und Zusammenfassung
    assert _reserve(client, tid, kind="asn", low=65500, purpose="Neuer Standort").status_code == 201
    data = _list(client, tid)
    assert [i["label"] for i in data["items"]] == ["AS 65500", "VLAN 300–305"]
    assert data["summary"]["vlan"]["free"] == 1 and data["summary"]["asn"]["total"] == 1
    assert [i["label"] for i in _list(client, tid, q="302")["items"]] == ["VLAN 300–305"]
    assert [i["label"] for i in _list(client, tid, q="standort")["items"]] == ["AS 65500"]
    assert [i["kind"] for i in _list(client, tid, kind="asn")["items"]] == ["asn"]

    # freigeben
    assert client.delete(f"/reservations/{rid}", headers=OP).status_code == 204
    assert [i["label"] for i in _list(client, tid)["items"]] == ["AS 65500"]
    with session_factory() as s:
        actions = [a.action for a in s.query(AuditLog).order_by(AuditLog.id)
                   if a.action.startswith("reservation.")]  # fmt: skip
        assert actions == ["reservation.created", "reservation.updated", "reservation.created",
                           "reservation.released"]  # fmt: skip


def test_eingaben_und_rechte(client):
    ids = _setup(client, bgp=False)
    tid = ids["tid"]
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    foreign = client.post(f"/tenants/{other}/sites", json={"code": "chbsl1", "name": "Basel"},
                          headers=ADMIN).json()["id"]  # fmt: skip
    bad = [
        {"low": 5000},  # VLAN nur bis 4094
        {"low": 310, "high": 300},  # bis < von
        {"kind": "asn", "low": 65000, "site_id": ids["chzrh1"]},  # Standort nur bei VLANs
        {"kind": "asn", "low": 65000, "vlan_group": "x"},
        {"site_id": foreign},  # Standort eines anderen Mandanten
        {"vlan_group": "Mit Leerzeichen"},
        {"purpose": "   "},
    ]
    for case in bad:
        assert _reserve(client, tid, **case).status_code == 422, case
    assert _reserve(client, tid, headers=VIEWER).status_code == 403  # nur Operator
    rid = _reserve(client, tid).json()["id"]
    body = {"tenant_id": tid, "kind": "asn", "low": 65000, "purpose": "x"}
    assert client.put(f"/reservations/{rid}", json=body, headers=OP).status_code == 422  # Art
    assert client.delete(f"/reservations/{rid}", headers=VIEWER).status_code == 403
    assert client.delete("/reservations/99999", headers=OP).status_code == 404
    opts = client.get("/reservations/options", params={"tenant_id": tid}, headers=VIEWER).json()
    assert [s["name"] for s in opts["sites"]] == ["Bern", "Zürich"]
    assert {(g["slug"], g["site"]) for g in opts["vlan_groups"]} == {
        ("kunde-a-switches-chbrn1", "Bern"), ("kunde-a-switches-chzrh1", "Zürich")}  # fmt: skip
    assert opts["limits"] == {"vlan": [1, 4094], "asn": [1, 4294967295]}


def test_vlan_gruppe_wie_der_sync():
    """Die Analyse bildet die VLAN-Gruppe des Sync nach (sie nutzt den Schreibpfad nicht)."""
    tenant = Tenant(name="Kunde A / Ost")
    for source in ("Switches ZRH", "fw-01", "ÄÖÜ core"):
        src = type("S", (), {"name": source})()
        assert reservations.group_slug(tenant.name, source) == netbox_sync.group_slug(tenant, src)


def test_modul_analyse_aus(client):
    ids = _setup(client, bgp=False)
    tid = ids["tid"]
    r = client.put("/modules/analyse", params={"tenant_id": tid}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200
    for path in ("/reservations", "/routing/as-numbers"):
        r = client.get(path, params={"tenant_id": tid})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "module_disabled", path


def test_mandant_loeschen_mit_reservierungen(client, session_factory):
    ids = _setup(client, bgp=False)
    tid = ids["tid"]
    assert _reserve(client, tid, site_id=ids["chzrh1"]).status_code == 201
    r = client.delete(f"/tenants/{tid}", params={"confirm": "Kunde A"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert s.query(Reservation).count() == 0


def test_standort_geloescht_bleibt_sichtbar(client):
    ids = _setup(client, bgp=False)
    tid = ids["tid"]
    site = client.post(f"/tenants/{tid}/sites", json={"code": "chgva1", "name": "Genf"},
                       headers=ADMIN).json()["id"]  # fmt: skip
    assert _reserve(client, tid, site_id=site).status_code == 201
    assert client.delete(f"/tenants/{tid}/sites/{site}", headers=ADMIN).status_code == 204
    (row,) = _list(client, tid)["items"]
    assert row["site_missing"] and row["site_id"] == site and row["site"] is None


# -- Befunde --------------------------------------------------------------------------------


def test_befunde_jetzt_belegt_und_konflikt(client):
    ids = _setup(client, bgp=False)
    tid, zrh, brn = ids["tid"], ids["chzrh1"], ids["chbrn1"]
    # VLAN 120 ist in Zürich schon gelesen in Gebrauch -> Konflikt
    r = _reserve(client, tid, low=120, site_id=zrh, purpose="DMZ neu")
    assert r.json()["used_before"] == [120] and r.json()["state"] == "conflict"
    assert r.json()["in_use"] == [{"number": 120, "where": "Switches chzrh1", "name": "Clients"}]
    # VLAN 120 in Bern ist frei (anderer Standort), 130 in Zürich auch
    assert _reserve(client, tid, low=120, site_id=brn).json()["state"] == "free"
    assert _reserve(client, tid, low=130, high=131, site_id=zrh).json()["state"] == "free"
    # fuer alle Standorte: VLAN 220 (Bern) -> Konflikt
    assert _reserve(client, tid, low=220, purpose="global").json()["state"] == "conflict"
    found = _findings(client, tid)
    assert set(found) == {"reservation_conflict"}
    assert set(found["reservation_conflict"]) == {"VLAN 120 · Zürich", "VLAN 220 · alle Standorte"}
    conflict = found["reservation_conflict"]["VLAN 120 · Zürich"]
    assert conflict["severity"] == "warning" and conflict["subject_kind"] == "reservation"
    assert "war schon beim Reservieren in Gebrauch" in conflict["message"]
    assert "Switches chzrh1 („Clients“)" in conflict["message"]

    # danach liest der Switch in Zürich VLAN 130: die Reservierung ist jetzt belegt (Hinweis)
    _deliver(
        client,
        tid,
        ids["sw-chzrh1"],
        "cisco_switch",
        _switches("sw-zrh-01", [(50, "Server"), (120, "Clients"), (130, "Projekt")]),
    )
    found = _findings(client, tid)
    belegt = found["reservation_in_use"]["VLAN 130–131 · Zürich"]
    assert belegt["severity"] == "info" and belegt["detail"]["numbers"] == [130]
    assert "ist jetzt belegt" in belegt["message"] and "freigeben" in belegt["hint"]
    row = next(i for i in _list(client, tid)["items"] if i["low"] == 130)
    assert row["state"] == "in_use" and row["findings"][0]["type"] == "reservation_in_use"

    # Erweitern behaelt „jetzt belegt“: 130 bleibt frei beim Reservieren, nur Neues zaehlt
    body = {"tenant_id": tid, "kind": "vlan", "low": 128, "high": 131, "site_id": zrh,
            "purpose": "Projekt"}  # fmt: skip
    r = client.put(f"/reservations/{row['id']}", json=body, headers=OP)
    assert r.json()["state"] == "in_use" and r.json()["used_before"] == []

    # freigeben: der Befund entfaellt sofort
    assert client.delete(f"/reservations/{row['id']}", headers=OP).status_code == 204
    assert "reservation_in_use" not in _findings(client, tid)


def test_befund_doppelt_reserviert(client):
    ids = _setup(client, bgp=False)
    tid, zrh, brn = ids["tid"], ids["chzrh1"], ids["chbrn1"]
    _reserve(client, tid, low=300, high=309, site_id=zrh, purpose="A")
    r = _reserve(client, tid, low=305, high=312, purpose="B")  # alle Standorte
    assert r.json()["overlaps"][0]["overlap"] == "305–309"
    _reserve(client, tid, low=300, high=309, site_id=brn, purpose="C")  # anderer Standort
    _reserve(client, tid, kind="asn", low=65100, high=65110, purpose="D")
    _reserve(client, tid, kind="asn", low=65120, purpose="E")  # keine Ueberschneidung
    _reserve(client, tid, kind="asn", low=65105, purpose="F")
    found = _findings(client, tid)["reservation_overlap"]
    assert set(found) == {
        "VLAN 300–309 · Zürich ↔ VLAN 305–312 · alle Standorte",
        "VLAN 300–309 · Bern ↔ VLAN 305–312 · alle Standorte",
        "AS 65100–65110 ↔ AS 65105",
    }
    one = found["AS 65100–65110 ↔ AS 65105"]
    assert one["severity"] == "warning" and "überschneiden sich in AS 65105" in one["message"]
    with use("en"):
        msg, hint = explain("reservation_overlap", one["subject_id"], one["detail"])
    assert msg == "Reserved twice: AS 65100–65110 “D” and AS 65105 “F” overlap in AS 65105."
    assert hint.startswith("Adjust or release one of the reservations")


def test_pruefung_abschaltbar(client):
    ids = _setup(client, bgp=False)
    tid = ids["tid"]
    _reserve(client, tid, low=50)  # alle Standorte, gelesen in Gebrauch
    assert "reservation_conflict" in _findings(client, tid)
    r = client.put("/checks/reservierung_gebrauch", params={"tenant_id": tid},
                   json={"enabled": False}, headers=ADMIN)  # fmt: skip
    assert r.status_code == 200, r.text
    client.post("/reconcile", params={"tenant_id": tid}, headers=OP)
    assert "reservation_conflict" not in _findings(client, tid)


def test_as_reservierung_gegen_bgp(client):
    ids = _setup(client)
    tid = ids["tid"]
    assert _reserve(client, tid, kind="asn", low=64500).json()["state"] == "conflict"  # Nachbar
    assert _reserve(client, tid, kind="asn", low=65001).json()["state"] == "conflict"  # lokal
    assert _reserve(client, tid, kind="asn", low=64502).json()["state"] == "conflict"  # konfig.
    assert _reserve(client, tid, kind="asn", low=65500, high=65509).json()["state"] == "free"
    found = _findings(client, tid)["reservation_conflict"]
    assert set(found) == {"AS 64500", "AS 65001", "AS 64502"}
    assert "fw-01 (lokal)" in found["AS 65001"]["message"]
    assert "rt-01 (lokal)" in found["AS 65001"]["message"]


def test_texte_englisch():
    d = {"kind": "vlan", "label": "VLAN 130–131", "site": "alle Standorte", "purpose": "Projekt",
         "by": "anna", "at": "2026-11-17", "numbers_text": "130",
         "uses": [{"number": 130, "where": "Switches chzrh1", "name": "Projekt"}],
         "uses_total": 3}  # fmt: skip
    with use("en"):
        msg, hint = explain("reservation_in_use", "x", d)
    assert msg == (
        "Reservation VLAN 130–131 (all sites) “Projekt” (reserved by anna on 2026-11-17) is now "
        "in use: VLAN 130 read in use – Switches chzrh1 („Projekt“) (+2 more)."
    )
    assert hint.startswith("The number was free when it was reserved")
    msg, _ = explain("reservation_conflict", "x", d)
    assert msg.startswith("Reservierung VLAN 130–131 (alle Standorte) „Projekt“ (reserviert von "
                          "anna am 2026-11-17) überschneidet sich")  # fmt: skip


# -- AS-Nummern -----------------------------------------------------------------------------


def test_as_nummern_einzeln(client):
    ids = _setup(client)
    tid = ids["tid"]
    _reserve(client, tid, kind="asn", low=65100, purpose="Standort Bern")
    _reserve(client, tid, kind="asn", low=65500, high=65501, purpose="frei")
    r = client.get("/routing/as-numbers", params={"tenant_id": tid}, headers=VIEWER)
    assert r.status_code == 200, r.text
    data = r.json()
    by = {i["asn"]: i for i in data["items"]}
    assert sorted(by) == [64500, 64502, 65001, 65010, 65100, 65200]
    own = by[65001]
    assert own["own"] and own["private"]
    assert {(d["kind"], d["label"]) for d in own["devices"]} == {
        ("firewall", "fw-01"),
        ("router", "rt-01"),
    }
    assert [d.get("router_id") for d in own["devices"] if d["kind"] == "firewall"] == ["10.0.0.1"]
    isp = by[64500]
    assert not isp["own"] and isp["established"] == 1
    (n,) = isp["neighbors"]
    assert (n["peer_ip"], n["at"]["label"], n["session"], n["description"]) == (
        ISP, "fw-01", "ebgp", "Provider")  # fmt: skip
    (down,) = by[64502]["neighbors"]
    assert down["configured_only"] and down["shutdown"] and down["established"] is False
    (aci,) = by[65010]["neighbors"]
    assert aci["at"]["kind"] == "fabric" and aci["at"]["l3out"] == "L3O_wan"
    assert by[65200]["neighbors"][0]["at"]["kind"] == "router" and by[65200]["private"]
    assert by[65100]["reservations"][0]["purpose"] == "Standort Bern"
    assert data["summary"] == {"total": 6, "own": 1, "foreign": 5, "private": 4, "reserved": 1}
    free = {x["label"]: x["used"] for x in data["reservations"]}
    assert free == {"AS 65100": [65100], "AS 65500–65501": []}
    # Filter: Nummer, Text (Geraet, Nachbar), eigene bzw. fremde
    q = client.get("/routing/as-numbers", params={"tenant_id": tid, "q": "AS64500"}).json()
    assert [i["asn"] for i in q["items"]] == [64500]
    q = client.get("/routing/as-numbers", params={"tenant_id": tid, "q": "l3o_wan"}).json()
    assert [i["asn"] for i in q["items"]] == [65010]
    q = client.get("/routing/as-numbers", params={"tenant_id": tid, "scope": "own"}).json()
    assert [i["asn"] for i in q["items"]] == [65001]


def test_as_index_unit(client, session_factory):
    ids = _setup(client)
    with session_factory() as s:
        index = as_numbers.Index(s, ids["tid"])
        used = index.used()
    assert used[65001] == ["fw-01 (lokal)", "rt-01 (lokal)"]
    assert used[65200] == [f"rt-01 → Nachbar {RT_PEER}"]
    assert as_numbers.asn("1.10") == 65546 and as_numbers.asn("x") is None
    assert as_numbers.asn(0) is None and as_numbers.asn(True) is None


# -- Datenstand -----------------------------------------------------------------------------


def test_datenstand_und_doku_bleiben(client, session_factory):
    """Reservierungen aendern weder Datenstand noch Doku: kein neuer Stand, Abdruck der Doku
    gleich (Audit `reservation.` zaehlt nicht, ADR 0037/0049); die Befunde dagegen sofort."""
    ids = _setup(client, bgp=False)
    tid = ids["tid"]
    app = client.post("/applications", params={"tenant_id": tid}, json={"name": "Web"},
                      headers=OP)  # fmt: skip
    assert app.status_code == 201, app.text

    def state():
        with session_factory() as s:
            from app.models import Application

            a = s.get(Application, app.json()["id"])
            return data_state.generation(s, tid), doc_snapshots.fingerprint(s, a, "de|-|all")

    before = state()
    rid = _reserve(client, tid, low=50).json()["id"]
    body = {"tenant_id": tid, "kind": "vlan", "low": 50, "high": 51, "purpose": "neu"}
    assert client.put(f"/reservations/{rid}", json=body, headers=OP).status_code == 200
    assert "reservation_conflict" in _findings(client, tid)
    assert client.delete(f"/reservations/{rid}", headers=OP).status_code == 204
    assert state() == before
    assert "reservation_conflict" not in _findings(client, tid)


# -- NetBox-Sync ----------------------------------------------------------------------------


@pytest.fixture
def netbox(monkeypatch) -> FakeNetBox:
    """Fake-NetBox wie die Sync-Tests, dazu RIR als verschachtelter Verweis (wie NetBox)."""
    fake = FakeNetBox()
    monkeypatch.setitem(_FK, "rir", "ipam/rirs")

    def make_writer(target):
        token = sync_api.credentials.decrypt(target.credentials).get("write_token")
        client = httpx.Client(transport=httpx.MockTransport(fake.handler))
        return sync_api.NetBoxWriter(target.base_url, token, client=client)

    monkeypatch.setattr(sync_api, "make_writer", make_writer)
    _reader(monkeypatch, fake)
    return fake


def _nb(client, ids) -> None:
    ids["nb"] = _system(client, ids["tid"], name="netbox", platform="netbox",
                        base_url="https://nb.example.ch", token="nb-read",
                        write_token="nb-write")  # fmt: skip


def _plan(client, ids, **opts) -> dict:
    body = {"tenant_id": ids["tid"], "source_system_id": ids["nb"], **opts}
    r = client.post("/netbox-sync/plans", json=body, headers=ADMIN)
    assert r.status_code == 202, r.text
    plan = client.get(f"/netbox-sync/plans/{r.json()['id']}", headers=ADMIN).json()
    assert plan["status"] == "ready", plan
    return plan


def _items(client, plan_id, **params) -> list[dict]:
    params.setdefault("limit", 2000)
    r = client.get(f"/netbox-sync/plans/{plan_id}/items", params=params, headers=ADMIN)
    return r.json()["rows"]


def _apply(client, plan_id) -> dict:
    r = client.post(f"/netbox-sync/plans/{plan_id}/apply", headers=ADMIN)
    assert r.status_code == 202, r.text
    return client.get(f"/netbox-sync/plans/{plan_id}", headers=ADMIN).json()


def _tagged(obj) -> bool:
    return any(t["slug"] == "automato" for t in obj["tags"])


RES = ["reservations"]


def test_sync_vorschau_und_uebernehmen(client, netbox):
    ids = _setup(client, bgp=False)
    _nb(client, ids)
    tid, zrh = ids["tid"], ids["chzrh1"]
    # in NetBox von Hand: RIR privat und oeffentlich, eine ASN, eine Gruppe mit VLAN 500
    private = netbox.add("ipam/rirs", {"name": "RFC 6996", "slug": "rfc6996", "is_private": True})
    netbox.add("ipam/rirs", {"name": "RIPE", "slug": "ripe", "is_private": False})
    netbox.add("ipam/asns", {"asn": 65501, "rir": private["id"], "description": "von Hand"})
    group = netbox.add("ipam/vlan-groups", {"name": "Manuell", "slug": "manuell"})
    netbox.add("ipam/vlans", {"vid": 500, "name": "Bestand", "group": group["id"],
                              "status": "active"})  # fmt: skip
    _reserve(client, tid, low=300, high=301, site_id=zrh, purpose="Projekt X")
    _reserve(client, tid, low=400, purpose="Global")
    _reserve(client, tid, low=500, vlan_group="manuell", purpose="Belegt")
    _reserve(client, tid, kind="asn", low=65500, high=65501, purpose="Neuer Standort")
    _reserve(client, tid, kind="asn", low=64496, purpose="Provider-AS")

    plan = _plan(client, ids, sections=RES)
    assert not netbox.writes  # Vorschau liest nur
    creates = {(i["kind"], i["key"]) for i in _items(client, plan["id"], action="create")}
    assert {("vlan_group", "kunde-a-reservierungen-chzrh1"),
            ("vlan_group", "kunde-a-reservierungen"),
            ("vlan", "kunde-a-reservierungen-chzrh1/300"),
            ("vlan", "kunde-a-reservierungen-chzrh1/301"), ("vlan", "kunde-a-reservierungen/400"),
            ("asn", "65500"), ("asn", "64496")} <= creates  # fmt: skip
    assert not {k for k, _ in creates} & {"device", "interface", "prefix"}  # nur der Abschnitt
    notes = {i["key"]: i for i in _items(client, plan["id"], action="conflict")
             if i.get("note") == "exists"}  # fmt: skip
    assert set(notes) == {"manuell/500 #reserviert", "65501 #reserviert"}
    changes = {
        c["field"]: (c["old"], c["new"]) for c in notes["manuell/500 #reserviert"]["changes"]
    }
    assert changes == {"name": ("Bestand", "Belegt"), "status": ("active", "reserved")}
    assert notes["65501 #reserviert"]["manual"] is True
    assert any("2 Nummern in NetBox schon vorhanden" in w for w in plan["warnings"])

    done = _apply(client, plan["id"])
    assert done["status"] == "applied", done
    vlan = netbox.find("ipam/vlans", vid=300)
    assert vlan["status"]["value"] == "reserved" and _tagged(vlan)
    assert (
        vlan["group"]["slug"] == "kunde-a-reservierungen-chzrh1"
        and vlan["name"] == "Projekt X (300)"
    )
    assert vlan["description"].startswith("Reservierung: Projekt X · reserviert von dev am ")
    assert vlan["tenant"]["slug"] == "kunde-a"
    grp = netbox.find("ipam/vlan-groups", slug="kunde-a-reservierungen-chzrh1")
    site = netbox.find("dcim/sites", slug="chzrh1")
    assert (grp["scope_type"], grp["scope_id"]) == ("dcim.site", site["id"])
    assert netbox.find("ipam/vlan-groups", slug="kunde-a-reservierungen").get("scope_type") is None
    asn = netbox.find("ipam/asns", asn=65500)
    assert asn["rir"]["slug"] == "rfc6996" and _tagged(asn)
    assert netbox.find("ipam/asns", asn=64496)["rir"]["slug"] == "ripe"
    # nie etwas Gelesenes ueberschrieben
    manual = netbox.find("ipam/vlans", vid=500)
    assert manual["name"] == "Bestand" and manual["status"]["value"] == "active"
    assert not _tagged(manual)
    assert netbox.find("ipam/asns", asn=65501)["description"] == "von Hand"

    again = _plan(client, ids, sections=RES)  # nichts mehr zu schreiben
    assert again["totals"]["create"] == again["totals"]["update"] == 0, again["totals"]
    assert not again["can_apply"]

    # andere Abschnitte lassen die reservierten VLANs und ihre Gruppen stehen
    other = _plan(client, ids, allow_delete=True)
    gone = {(i["kind"], i["key"]) for i in _items(client, other["id"], action="delete")}
    assert not {k for k in gone if "reservierungen" in k[1] or k[0] == "asn"}, gone


def test_sync_freigeben_entfernt_nur_mit_freigabe(client, netbox):
    ids = _setup(client, bgp=False)
    _nb(client, ids)
    tid, zrh = ids["tid"], ids["chzrh1"]
    netbox.add("ipam/rirs", {"name": "RFC 6996", "slug": "rfc6996", "is_private": True})
    vlan = _reserve(client, tid, low=300, site_id=zrh).json()["id"]
    asn = _reserve(client, tid, kind="asn", low=65500).json()["id"]
    assert _apply(client, _plan(client, ids, sections=RES)["id"])["status"] == "applied"
    for rid in (vlan, asn):
        assert client.delete(f"/reservations/{rid}", headers=OP).status_code == 204

    plan = _plan(client, ids, sections=RES)
    obsolete = {(i["kind"], i["key"]) for i in _items(client, plan["id"], action="obsolete")}
    assert obsolete == {("vlan", "kunde-a-reservierungen-chzrh1/300"), ("asn", "65500"),
                        ("vlan_group", "kunde-a-reservierungen-chzrh1")}  # fmt: skip
    assert not plan["can_apply"]
    plan = _plan(client, ids, sections=RES, allow_delete=True)
    assert _apply(client, plan["id"])["status"] == "applied"
    assert netbox.find("ipam/vlans", vid=300) is None
    assert netbox.find("ipam/asns", asn=65500) is None
    assert netbox.find("ipam/vlan-groups", slug="kunde-a-reservierungen-chzrh1") is None


def test_sync_gruppe_am_standort_und_hinweise(client, netbox):
    ids = _setup(client, bgp=False)
    _nb(client, ids)
    tid, zrh, brn = ids["tid"], ids["chzrh1"], ids["chbrn1"]
    # Zürich: eine Gruppe am Standort (von Hand) - dort hinein; Bern: zwei - Hinweis
    zsite = netbox.add("dcim/sites", {"name": "Zürich", "slug": "chzrh1", "status": "active"})
    bsite = netbox.add("dcim/sites", {"name": "Bern", "slug": "chbrn1", "status": "active"})
    netbox.add("ipam/vlan-groups", {"name": "ZRH", "slug": "zrh-dc", "scope_type": "dcim.site",
                                    "scope_id": zsite["id"]})  # fmt: skip
    for slug in ("brn-a", "brn-b"):
        netbox.add("ipam/vlan-groups", {"name": slug, "slug": slug, "scope_type": "dcim.site",
                                        "scope_id": bsite["id"]})  # fmt: skip
    _reserve(client, tid, low=300, site_id=zrh)
    _reserve(client, tid, low=310, site_id=brn)
    _reserve(client, tid, low=320, vlan_group="gibts-nicht")
    _reserve(client, tid, kind="asn", low=64496)  # keine RIR in NetBox
    _reserve(client, tid, kind="asn", low=4200000000, high=4200002000)  # zu gross
    plan = _plan(client, ids, sections=RES)
    creates = {(i["kind"], i["key"]) for i in _items(client, plan["id"], action="create")}
    assert ("vlan", "zrh-dc/300") in creates
    assert not {k for k in creates if k[0] == "asn" or k[1].endswith(("/310", "/320"))}
    warnings = " | ".join(plan["warnings"])
    assert "Reservierung VLAN 310: mehrere VLAN-Gruppen am Standort Bern (brn-a, brn-b)" in warnings
    assert "Reservierung VLAN 320: VLAN-Gruppe gibts-nicht nicht in NetBox" in warnings
    assert "keine eindeutige RIR in NetBox für AS 64496 (keine RIR)" in warnings
    assert "2001 AS-Nummern – mehr als 1024" in warnings
    r = client.get(f"/netbox-sync/plans/{plan['id']}",
                   headers={**ADMIN, "Accept-Language": "en"}).json()  # fmt: skip
    assert any("several VLAN groups at site Bern" in w for w in r["warnings"]), r["warnings"]


def test_sync_reserviertes_vlan_geht_ins_gelesene_ueber(client, netbox):
    """Liest der Switch das reservierte VLAN, will der Abschnitt Verkabelung es: das eigene
    reservierte VLAN in der Gruppe der Switches wird aktiv (gelesen gewinnt), und Ports nehmen
    nie ein reserviertes VLAN."""
    ids = _setup(client, bgp=False)
    _nb(client, ids)
    tid, zrh = ids["tid"], ids["chzrh1"]
    _reserve(client, tid, low=130, site_id=zrh, purpose="Projekt")
    # erst Verkabelung: die Gruppe der Switches am Standort entsteht, dann die Reservierung dort
    first = _plan(client, ids, sections=["cabling", "reservations"])
    creates = {(i["kind"], i["key"]) for i in _items(client, first["id"], action="create")}
    assert ("vlan", "kunde-a-switches-chzrh1/130") in creates, creates
    assert _apply(client, first["id"])["status"] == "applied"
    assert netbox.find("ipam/vlans", vid=130)["status"]["value"] == "reserved"

    _deliver(
        client,
        tid,
        ids["sw-chzrh1"],
        "cisco_switch",
        _switches("sw-zrh-01", [(50, "Server"), (120, "Clients"), (130, "Projekt-LAN")]),
    )
    plan = _plan(client, ids, sections=["cabling", "reservations"])
    (upd,) = (i for i in _items(client, plan["id"], action="update") if i["kind"] == "vlan")
    assert upd["key"] == "kunde-a-switches-chzrh1/130"
    assert {c["field"]: c["new"] for c in upd["changes"]}["status"] == "active"
    assert any("VLAN 130 Projekt-LAN (kunde-a-switches-chzrh1)" in w for w in plan["warnings"])
    assert _apply(client, plan["id"])["status"] == "applied"
    vlan = netbox.find("ipam/vlans", vid=130)
    assert (vlan["status"]["value"], vlan["name"]) == ("active", "Projekt-LAN")


def test_reserviertes_vlan_kein_kandidat_fuer_ports():
    from app.store import netbox_sync_reserv as reserv

    obj = {
        "id": 1,
        "vid": 130,
        "status": {"value": "reserved"},
        "tags": [{"slug": "automato"}],
        "description": "Reservierung: Projekt · reserviert von dev",
        "group": {"slug": "g"},
    }
    assert reserv.owned_vlan(obj)
    assert not reserv.owned_vlan({**obj, "status": {"value": "active"}})
    assert not reserv.owned_vlan({**obj, "tags": []})
    state = {k.name: {} for k in netbox_sync.KINDS}
    state["vlan_ref"] = {"g/130 #1": netbox_sync.Have(1, "g/130 #1", obj, True)}
    assert netbox_sync._vlan_candidates(netbox_sync.Wants(), state) == {}
