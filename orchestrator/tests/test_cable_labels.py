"""Verkabelung im Detail (1.19.0, ADR 0031 Nachtrag 1.19.0): Transceiver je Port, Kabelnummern
aus NetBox (Referenzquelle) und in Automato erfasst, Befunde und Datenstand.

Grundlage sind die Switches aus `test_cabling` (sw-acc-01, sw-core-01, fw-08, leaf-101) mit
Transceivern und dem Lesestatus je Switch. In NetBox: Kabel K-0815 Gi1/0/47 ↔ Eth1/1 (passt zu
CDP), K-0816 Gi1/0/48 ↔ Eth1/9 (CDP meldet an Gi1/0/48 Eth1/2 - Befund), ein Kabel ohne Label zu
einem Patchpanel (ohne Nachbar-Daten - kein Befund).
"""

import copy

from app import audit
from app.models import AuditLog, CableLabel, DataChange, InvDevice, InvTransceiver
from app.store import cable_labels, references
from sqlalchemy import select
from test_cabling import ACI, SWITCHES, TOKEN

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
VIEWER = {"X-Dev-Roles": "viewer"}


def _switches(half: bool = False) -> dict:
    result = copy.deepcopy(SWITCHES)
    inv = result["inventory"]
    inv["transceivers"] = [
        {"device": "sw-acc-01", "port": "Te1/1/1", "type": "SFP-10GBase-SR", "part": "SFP-10G-SR",
         "serial": "AVD2345A1B2"},
        {"device": "sw-core-01", "port": "Eth1/1", "type": "10Gbase-SR", "vendor": "CISCO-FINISAR",
         "part": "SFP-10G-SR", "serial": "FNS2345A0AA"},
    ]  # fmt: skip
    inv["transceiver_reads"] = [
        {"device": "sw-acc-01", "status": "read", "count": 1},
        {"device": "sw-core-01", "status": "unreadable", "count": 1,
         "note": "„show interface transceiver“: Invalid command"},
    ]  # fmt: skip
    if half:  # Gi1/0/47 <-> Eth1/1 beide halb; Te1/1/1 halb, aber zur nicht gelesenen fw-08
        for port in inv["interfaces"]:
            if port["name"] in ("Gi1/0/47", "Eth1/1", "Te1/1/1"):
                port["duplex"] = "a-half" if port["name"] == "Gi1/0/47" else "half"
    return result


def _deliver(client, tid, sid, platform, result) -> None:
    req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _cable(cid, label, a, b, **attrs):
    return {"kind": "cable", "value": str(cid), "name": label, "status": "connected",
            "ref_id": str(cid), "url": f"https://nb.example.ch/dcim/cables/{cid}/",
            "attrs": {"a_device": a[0], "a_port": a[1], "b_device": b[0], "b_port": b[1],
                      **attrs}}  # fmt: skip


CABLES = [
    _cable(41, "K-0815", ("sw-acc-01", "GigabitEthernet1/0/47"), ("sw-core-01", "Ethernet1/1"),
           type="smf"),
    _cable(42, "K-0816", ("sw-acc-01", "GigabitEthernet1/0/48"), ("sw-core-01", "Ethernet1/9")),
    _cable(43, None, ("sw-acc-01", "Gi1/0/2"), ("patch-01", "1")),
]  # fmt: skip
PREFIX = {"kind": "prefix", "value": "10.0.0.0/24", "name": "Switches"}


def _netbox(client, tid, sid, records) -> None:
    req = {"platform": "netbox", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id}/references", headers=TOKEN,
                json={"records": records})  # fmt: skip
    result = {"status": "completed", "reference_count": len(records)}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.json()["status"] == "completed", r.text


def _setup(client, half: bool = False) -> dict:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]

    def system(**body) -> int:
        r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    ids = {"tid": tid,
           "sw": system(name="Switches A", platform="cisco_switch",
                        read_scope="10.0.0.11\n10.0.0.2", username="ro", password="x"),
           "apic": system(name="apic", platform="cisco_aci", base_url="https://apic"),
           "nb": system(name="netbox", platform="netbox", base_url="https://nb.example.ch",
                        token="nb-read")}  # fmt: skip
    _deliver(client, tid, ids["sw"], "cisco_switch", _switches(half))
    _deliver(client, tid, ids["apic"], "cisco_aci", ACI)
    _netbox(client, tid, ids["nb"], [PREFIX, *CABLES])
    return ids


def _put(client, tid, a, b, label, headers=OP, **extra):
    body = {"tenant_id": tid, "a_device": a[0], "a_port": a[1], "b_device": b[0],
            "b_port": b[1], "label": label, **extra}  # fmt: skip
    return client.put("/cabling/labels", json=body, headers=headers)


def _findings(client, tid, prefix=("cable_", "link_half")) -> list[dict]:
    rows = client.get("/findings", params={"tenant_id": tid, "limit": 500}).json()
    items = rows["items"] if isinstance(rows, dict) else rows
    return [f for f in items if f["type"].startswith(prefix)]


def test_transceiver_eingelesen_und_ausgewiesen(client, session_factory):
    ids = _setup(client)
    tid = ids["tid"]
    s = session_factory()
    rows = {(t.device, t.port): t for t in s.scalars(select(InvTransceiver))}
    assert set(rows) == {("sw-acc-01", "Te1/1/1"), ("sw-core-01", "Eth1/1")}
    assert rows[("sw-core-01", "Eth1/1")].vendor == "CISCO-FINISAR"
    status = dict(s.execute(select(InvDevice.name, InvDevice.transceivers)).all())
    assert status["sw-acc-01"] == "read" and status["sw-core-01"] == "unreadable"
    assert status["leaf-101"] is None  # ACI liefert keine Transceiver: nicht geliefert
    s.close()
    out = client.get("/cabling/transceivers", params={"tenant_id": tid}).json()
    assert [(d["device"], d["status"], d["count"]) for d in out["devices"]] == [
        ("sw-acc-01", "read", 1), ("sw-core-01", "unreadable", 1)]  # fmt: skip
    only = client.get("/cabling/transceivers", params={"tenant_id": tid, "device": "SW-CORE-01"})
    assert [i["port"] for i in only.json()["items"]] == ["Eth1/1"]
    # Ports, Geraete und Verbindungen zeigen den Transceiver
    ports = client.get("/cabling/ports", params={"tenant_id": tid, "q": "Te1/1/1"}).json()
    assert ports["items"][0]["transceiver"] == {"type": "SFP-10GBase-SR", "part": "SFP-10G-SR",
                                                "serial": "AVD2345A1B2"}  # fmt: skip
    listed = client.get("/cabling/devices", params={"tenant_id": tid}).json()["items"]
    devices = {d["name"]: d for d in listed}
    assert devices["sw-core-01"]["transceivers"] == "unreadable"
    assert devices["sw-acc-01"]["transceiver_count"] == 1
    links = client.get("/cabling/links", params={"tenant_id": tid, "q": "Gi1/0/47"}).json()
    (link,) = links["items"]
    ends = {link["a"]["port"]: link["a"], link["b"]["port"]: link["b"]}
    assert ends["Eth1/1"]["transceiver"]["serial"] == "FNS2345A0AA"
    assert ends["Gi1/0/47"]["transceiver"] is None
    # ohne Lieferung (aelterer Agent): Transceiver und Status leer - nichts geraten
    older = copy.deepcopy(SWITCHES)
    _deliver(client, tid, ids["sw"], "cisco_switch", older)
    s = session_factory()
    assert s.scalars(select(InvTransceiver)).all() == []
    assert s.scalar(select(InvDevice.transceivers).where(InvDevice.name == "sw-acc-01")) is None
    s.close()


def test_kabel_aus_netbox_nicht_im_ipam(client, session_factory):
    ids = _setup(client)
    s = session_factory()
    from app.models import SourceSystem

    nb = s.get(SourceSystem, ids["nb"])
    index = references.load_index(s, nb)
    assert not index.by_name.get("k-0815")  # Kabel sind kein IPAM-Eintrag (Namen, Netze)
    assert index.prefix("10.0.0.0/24") is not None
    cables = {c.id: c for c in cable_labels.netbox_cables(s, ids["tid"])}
    assert set(cables) == {"41", "42", "43"}
    assert cables["41"].pair == "sw-acc-01|gi1/0/47~sw-core-01|eth1/1"  # wie die Verbindung
    s.close()
    out = client.get("/cabling/labels", params={"tenant_id": ids["tid"]}).json()
    by = {r["pair"]: r for r in out["items"]}
    row = by["sw-acc-01|gi1/0/47~sw-core-01|eth1/1"]
    assert (row["state"], row["neighbor"], row["netbox"][0]["label"]) == ("netbox", "ok", "K-0815")
    assert row["link"]["state"] == "both" and row["netbox"][0]["type"] == "smf"
    wrong = by["sw-acc-01|gi1/0/48~sw-core-01|eth1/9"]
    assert wrong["neighbor"] == "mismatch" and wrong["link"] is None
    assert wrong["wrong"][0]["seen"] == [{"device": "sw-core-01", "port": "Eth1/2"}]
    patch = by["patch-01|1~sw-acc-01|gi1/0/2"]
    assert (patch["state"], patch["neighbor"]) == (None, "unknown")  # Kabel ohne Label
    assert out["summary"]["netbox_total"] == 3 and out["summary"]["mismatch"] == 1
    mismatch = client.get("/cabling/labels", params={"tenant_id": ids["tid"],
                                                     "state": "mismatch"}).json()  # fmt: skip
    assert [r["pair"] for r in mismatch["items"]] == [wrong["pair"]]


def test_kabelnummer_erfassen_aendern_loeschen(client, session_factory):
    ids = _setup(client)
    tid = ids["tid"]
    # nur Operator bzw. Admin; Leser 403
    assert _put(client, tid, ("sw-core-01", "Eth1/1"), ("sw-acc-01", "Gi1/0/47"), "K-0815",
                headers=VIEWER).status_code == 403  # fmt: skip
    r = _put(client, tid, ("sw-core-01", "Ethernet1/1"), ("sw-acc-01", "Gi1/0/47"), " K-0815 ")
    assert r.status_code == 200, r.text
    first = r.json()
    assert (first["label"], first["state"], first["previous"]) == ("K-0815", "same", None)
    assert first["pair"] == "sw-acc-01|gi1/0/47~sw-core-01|eth1/1"
    # dasselbe Paar andersherum und in Langform: dieselbe Erfassung (eine je Paar)
    r = _put(client, tid, ("SW-ACC-01", "GigabitEthernet1/0/47"), ("sw-core-01", "Eth1/1"),
             "K-0817", note="neu gepatcht")  # fmt: skip
    assert r.json()["id"] == first["id"] and r.json()["previous"] == "K-0815"
    assert r.json()["state"] == "differs" and r.json()["note"] == "neu gepatcht"
    s = session_factory()
    assert s.query(CableLabel).count() == 1
    actions = [(a.action, audit.decode_detail(a).get("old"))
               for a in s.query(AuditLog).order_by(AuditLog.id)
               if a.action.startswith("cable_label.")]  # fmt: skip
    assert actions == [("cable_label.set", None), ("cable_label.set", "K-0815")]
    s.close()
    # ungueltig: gleicher Port an beiden Enden, leer, zu lang
    bad = _put(client, tid, ("sw-acc-01", "Gi1/0/5"), ("sw-acc-01", "GigabitEthernet1/0/5"), "X")
    assert bad.status_code == 422 and "derselbe Port" in bad.text
    assert _put(client, tid, ("a", "1"), ("b", "2"), "   ").status_code == 422
    en = _put(client, tid, ("a", "1"), ("a", "1"), "X", headers={**OP, "Accept-Language": "en"})
    assert "same port" in en.text
    assert _put(client, tid, ("a", "1"), ("b", "2"), "x" * 101).status_code == 422
    # Liste: Nummer mit Herkunft
    out = client.get("/cabling/labels", params={"tenant_id": tid, "q": "K-0817"}).json()
    (row,) = out["items"]
    assert row["automato"]["label"] == "K-0817" and row["netbox"][0]["label"] == "K-0815"
    links = client.get("/cabling/links", params={"tenant_id": tid, "q": "Gi1/0/47"}).json()
    assert links["items"][0]["label"]["state"] == "differs"
    # anderer Mandant: nicht gefunden; loeschen nur Operator
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    url = f"/cabling/labels/{first['id']}"
    assert client.delete(url, params={"tenant_id": other}, headers=OP).status_code == 404
    assert client.delete(url, params={"tenant_id": tid}, headers=VIEWER).status_code == 403
    assert client.delete(url, params={"tenant_id": tid}, headers=OP).status_code == 200
    # was in NetBox steht, bleibt (angezeigt)
    out = client.get("/cabling/labels", params={"tenant_id": tid, "q": "K-0815"}).json()
    assert out["items"][0]["state"] == "netbox" and out["items"][0]["automato"] is None
    s = session_factory()
    assert s.query(CableLabel).count() == 0
    assert any(a.action == "cable_label.deleted" for a in s.query(AuditLog))
    s.close()


def test_auswahl_geraete_ports_und_vorschlag(client):
    """1.23.0 (Anwender 2026-11-20: „Geräte und Ports auswählen“): Geraete mit Suche, Ports je
    Geraet ohne logische Interfaces, Gegenstelle als Vorschlag mit Herkunft; frei nur ohne
    gelesenes Geraet."""
    ids = _setup(client)
    tid = ids["tid"]
    url = "/cabling/labels/choices"
    out = client.get(url, params={"tenant_id": tid}, headers=VIEWER).json()
    by = {d["name"]: d for d in out["items"]}
    assert by["sw-acc-01"]["origin"] == "inventory" and not by["sw-acc-01"]["free_ports"]
    assert by["fw-08"]["origin"] == "neighbor" and by["fw-08"]["free_ports"]  # nur aus LLDP
    assert by["patch-01"]["origin"] == "netbox"  # nur am Kabel in NetBox
    origins = [d["origin"] for d in out["items"]]
    assert origins == sorted(origins, key=["inventory", "neighbor", "netbox", "free"].index)
    found = client.get(url, params={"tenant_id": tid, "q": "CORE"}).json()
    assert [d["name"] for d in found["items"]] == ["sw-core-01"] and found["total"] == 1
    # Ports eines Geraets: gelesen, ohne Port-Channel; Zahlen numerisch sortiert
    ports = client.get(url, params={"tenant_id": tid, "device": "SW-ACC-01"}).json()
    assert ports["device"]["name"] == "sw-acc-01"
    names = [p["name"] for p in ports["ports"]]
    assert names == ["Gi1/0/2", "Gi1/0/5", "Gi1/0/47", "Gi1/0/48", "Te1/1/1"]
    port = {p["name"]: p for p in ports["ports"]}
    # Vorschlag: CDP (passt zum Kabel in NetBox K-0815) - eine Gegenstelle, beide Herkuenfte
    (peer,) = port["Gi1/0/47"]["peers"]
    assert peer == {"device": "sw-core-01", "port": "Eth1/1", "via": ["cdp", "netbox"],
                    "labels": ["K-0815"]}  # fmt: skip
    # CDP und NetBox verschieden: beide als Vorschlag, CDP zuerst
    assert [(p["device"], p["port"], p["via"]) for p in port["Gi1/0/48"]["peers"]] == [
        ("sw-core-01", "Eth1/2", ["cdp"]), ("sw-core-01", "Ethernet1/9", ["netbox"])]  # fmt: skip
    core = client.get(url, params={"tenant_id": tid, "device": "sw-core-01"}).json()
    assert "Ethernet1/9" not in {p["name"] for p in core["ports"]}  # nur gelesene Ports
    assert port["Gi1/0/2"]["peers"][0]["device"] == "patch-01"
    # nur die Gegenseite meldet (LLDP an sw-core-01 Eth1/3): auch ein Vorschlag
    assert [(p["device"], p["port"], p["via"]) for p in port["Gi1/0/5"]["peers"]] == [
        ("sw-core-01", "Eth1/3", ["lldp"])]  # fmt: skip
    assert port["Te1/1/1"]["description"] == "fw-08 port5" and port["Te1/1/1"]["read"]
    fw = client.get(url, params={"tenant_id": tid, "device": "fw-08"}).json()
    assert fw["device"]["free_ports"] and [p["name"] for p in fw["ports"]] == ["port5"]
    assert not fw["ports"][0]["read"]
    unknown = client.get(url, params={"tenant_id": tid, "device": "pp-unbekannt"}).json()
    assert unknown == {"device": None, "ports": []}
    # Erfassen: an einem gelesenen Geraet nur ein gelesener Port (neues Paar)
    bad = _put(client, tid, ("sw-acc-01", "Gi1/0/99"), ("patch-02", "12"), "K-1")
    assert bad.status_code == 422 and "nicht gelesen" in bad.text
    en = _put(client, tid, ("sw-acc-01", "Gi1/0/99"), ("patch-02", "12"), "K-1",
              headers={**OP, "Accept-Language": "en"})  # fmt: skip
    assert "choose a port from the list" in en.text
    # frei ohne gelesenes Geraet; Namen in der Schreibweise der Plattform
    ok = _put(client, tid, ("SW-ACC-01", "GigabitEthernet1/0/5"), ("patch-02", "12"), "K-1")
    assert ok.status_code == 200, ok.text
    assert (ok.json()["a"], ok.json()["b"]) == ({"device": "sw-acc-01", "port": "Gi1/0/5"},
                                                {"device": "patch-02", "port": "12"})  # fmt: skip
    listed = client.get("/cabling/labels", params={"tenant_id": tid, "q": "K-1"}).json()
    (row,) = listed["items"]
    assert (row["a"]["free"], row["b"]["free"]) == (False, True)  # frei erfasst gekennzeichnet
    # frei erfasstes Geraet steht danach in der Auswahl (als frei erfasst), mit Vorschlag
    again = client.get(url, params={"tenant_id": tid, "q": "patch-02"}).json()
    assert [(d["name"], d["origin"]) for d in again["items"]] == [("patch-02", "free")]
    pp = client.get(url, params={"tenant_id": tid, "device": "patch-02"}).json()
    assert pp["ports"][0]["peers"] == [{"device": "sw-acc-01", "port": "Gi1/0/5",
                                        "via": ["automato"], "labels": ["K-1"]}]  # fmt: skip
    # bestehende Erfassung bleibt aenderbar, auch wenn der Port nicht mehr gelesen wird
    s_ports = copy.deepcopy(_switches())
    s_ports["inventory"]["interfaces"] = [
        i for i in s_ports["inventory"]["interfaces"] if i["name"] != "Gi1/0/5"
    ]
    _deliver(client, tid, ids["sw"], "cisco_switch", s_ports)
    assert _put(client, tid, ("sw-acc-01", "Gi1/0/5"), ("patch-02", "12"),
                "K-2").status_code == 200  # fmt: skip
    # ein Mandanten-fremder Leser sieht nichts
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    assert client.get(url, params={"tenant_id": other}).json()["items"] == []


def test_befunde_positiv_und_negativ(client):
    ids = _setup(client, half=True)
    tid = ids["tid"]
    found = {(f["type"], f["subject_id"]) for f in _findings(client, tid)}
    # Nachbar passt nicht: K-0816 endet laut NetBox an Eth1/9, CDP meldet Eth1/2
    assert ("cable_neighbor_mismatch", "sw-acc-01|gi1/0/48~sw-core-01|eth1/9") in found
    # Kabel, das zu CDP passt, und Kabel ohne Nachbar-Daten: kein Befund
    assert not any(t == "cable_neighbor_mismatch" and "eth1/1" in s for t, s in found)
    assert not any("patch-01" in s for _, s in found)
    # Duplex halb an beiden gelesenen Enden; zur nicht gelesenen fw-08 kein Befund
    half = [s for t, s in found if t == "link_half_duplex"]
    assert half == ["sw-acc-01|gi1/0/47~sw-core-01|eth1/1"]
    # Duplex verschieden zwischen zwei Switches: nur der bisherige Befund (kein zweiter)
    assert not any("gi1/0/48" in s and "eth1/2" in s for t, s in found if t == "link_half_duplex")
    assert not any(t == "cable_label_mismatch" for t, _ in found)
    # Kabelnummer: gleich -> kein Befund, verschieden -> Befund, NetBox ohne Label -> keiner
    _put(client, tid, ("sw-acc-01", "Gi1/0/47"), ("sw-core-01", "Eth1/1"), "K-0815")
    _put(client, tid, ("sw-acc-01", "Gi1/0/2"), ("patch-01", "1"), "P-1")
    assert not any(f["type"] == "cable_label_mismatch" for f in _findings(client, tid))
    r = _put(client, tid, ("sw-acc-01", "Gi1/0/48"), ("sw-core-01", "Eth1/9"), "K-0900")
    label_id = r.json()["id"]
    (f,) = (f for f in _findings(client, tid) if f["type"] == "cable_label_mismatch")
    assert f["subject_kind"] == "link" and f["severity"] == "warning"
    assert "„K-0816“, in Automato „K-0900“" in f["message"] and "Kabelnummer" in f["message"]
    (nb,) = (f for f in _findings(client, tid) if f["type"] == "cable_neighbor_mismatch")
    assert "sw-acc-01 GigabitEthernet1/0/48: sw-core-01 Eth1/2" in nb["message"]
    en = client.get("/findings", params={"tenant_id": tid, "limit": 500},
                    headers={"Accept-Language": "en"}).json()  # fmt: skip
    rows = en["items"] if isinstance(en, dict) else en
    assert any("Cable number" in x["message"] for x in rows if x["type"] == "cable_label_mismatch")
    # Erfassung geloescht: Befund faellt sofort weg (ohne neuen Lauf)
    client.delete(f"/cabling/labels/{label_id}", params={"tenant_id": tid}, headers=OP)
    assert not any(f["type"] == "cable_label_mismatch" for f in _findings(client, tid))
    # Pruefung aus: ihre Befunde fallen weg
    r = client.put("/checks/kabel", params={"tenant_id": tid}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200, r.text
    client.post("/reconcile/run", params={"tenant_id": tid}, headers=OP)
    _put(client, tid, ("sw-acc-01", "Gi1/0/48"), ("sw-core-01", "Eth1/9"), "K-0901")
    assert _findings(client, tid) == []


def test_datenstand_transceiver_und_kabel_ohne_wirkung(client, session_factory):
    ids = _setup(client)
    tid = ids["tid"]
    s = session_factory()
    before = s.query(DataChange).count()
    s.close()
    # nur ein anderer Transceiver: Aenderung ohne Wirkung auf Doku und Wege
    changed = _switches()
    changed["inventory"]["transceivers"][0]["serial"] = "AVD2345A1B9"
    _deliver(client, tid, ids["sw"], "cisco_switch", changed)
    # nur ein anderes Label in NetBox: ebenso
    cables = copy.deepcopy(CABLES)
    cables[0]["name"] = "K-0815-neu"
    _netbox(client, tid, ids["nb"], [PREFIX, *cables])
    s = session_factory()
    rows = s.query(DataChange).order_by(DataChange.seq).all()[before:]
    assert [(r.parts, r.all) for r in rows] == [(["inventory.transceivers"], False),
                                                (["reference.cables"], False)]  # fmt: skip
    s.close()
    # ein anderes Netz in NetBox wirkt wie bisher ueberall
    _netbox(client, tid, ids["nb"], [{**PREFIX, "name": "Switches neu"}, *cables])
    s = session_factory()
    last = s.query(DataChange).order_by(DataChange.seq.desc()).first()
    assert (last.parts, last.all) == (["reference"], True)
    s.close()
