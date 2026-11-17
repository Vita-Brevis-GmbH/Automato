"""NetBox-Sync: Kabelnummer als Label des Kabels (1.19.0, ADR 0031 Nachtrag 1.19.0) gegen die
Fake-NetBox der Sync-Tests.

Neue Kabel bekommen die in Automato erfasste Nummer gleich mit; an bestehenden Kabeln (eigenen
und von Hand gepflegten) zeigt die Vorschau beide Werte, uebernommen wird nur, was der Betreiber
bestaetigt; was nur in NetBox steht, bleibt.
"""

from app import audit
from app.models import AuditLog
from tests.test_netbox_sync_cabling_apps import (
    ADMIN,
    ADMIN_EN,
    CABLE_LEAF,
    CABLE_UPLINK,
    OP,
    _items,
    _plan,
    _port_of,
    _setup_cabling,
    _tagged,
    netbox,  # noqa: F401 - Fixture
)

UPLINK = "sw-acc-01|gi1/0/47~sw-core-01|eth1/1"
LEAF = "leaf-101|eth1/10~sw-core-01|eth1/10"
SECTIONS = ["inventory", "cabling"]


def _label(client, tid, a, b, label):
    body = {"tenant_id": tid, "a_device": a[0], "a_port": a[1], "b_device": b[0],
            "b_port": b[1], "label": label}  # fmt: skip
    r = client.put("/cabling/labels", json=body, headers=OP)
    assert r.status_code == 200, r.text


def _apply(client, plan_id, confirm=None):
    body = {"confirm": confirm} if confirm is not None else None
    r = client.post(f"/netbox-sync/plans/{plan_id}/apply", json=body, headers=ADMIN)
    return r, client.get(f"/netbox-sync/plans/{plan_id}", headers=ADMIN).json()


def _cable(fake, key):
    a, b = key.split("~")
    for c in fake.objects("dcim/cables"):
        ends = {f"{t['object']['device']['name']}/{t['object']['name']}"
                for side in ("a_terminations", "b_terminations") for t in c[side]}  # fmt: skip
        if ends == {a, b}:
            return c
    return None


def test_neue_kabel_mit_nummer_bestehende_nur_bestaetigt(client, netbox, session_factory):  # noqa: F811
    ids = _setup_cabling(client, netbox)
    tid = ids["tid"]
    _label(client, tid, ("sw-core-01", "Ethernet1/1"), ("sw-acc-01", "GigabitEthernet1/0/47"),
           "K-100")  # fmt: skip
    _label(client, tid, ("leaf-101", "eth1/10"), ("sw-core-01", "Eth1/10"), "K-200")
    _label(client, tid, ("sw-core-01", "Eth1/3"), ("sw-acc-01", "Gi1/0/5"), "K-300")  # einseitig

    # 1. Sync: die Kabel entstehen mit ihrer Nummer; die Nummer ohne Kabel bleibt in Automato
    plan = _plan(client, ids, sections=SECTIONS)
    cables = {i["key"]: i for i in _items(client, plan["id"], kind="cable")}
    assert cables[CABLE_UPLINK]["fields"]["label"] == "K-100"
    assert cables[CABLE_LEAF]["fields"]["label"] == "K-200"
    assert not _items(client, plan["id"], kind="cable_label")
    assert any(
        w.startswith(
            "Kabelnummern: 1 ohne Kabel in NetBox nicht übernommen (z. B. "
            "sw-core-01 Eth1/3 ↔ sw-acc-01 Gi1/0/5)"
        )
        for w in plan["warnings"]
    )
    r, done = _apply(client, plan["id"])
    assert r.status_code == 202 and done["status"] == "applied", done
    assert _cable(netbox, CABLE_UPLINK)["label"] == "K-100"
    assert _cable(netbox, CABLE_LEAF)["label"] == "K-200"
    assert done["verification"]["deviation_count"] == 0

    # in NetBox geaendert: am eigenen Kabel eine andere Nummer, das Leaf-Kabel von Hand gepflegt,
    # dazu ein Kabel von Hand mit Nummer, die Automato nicht kennt
    _cable(netbox, CABLE_UPLINK)["label"] = "K-ALT"
    leaf = _cable(netbox, CABLE_LEAF)
    leaf["tags"], leaf["label"] = [], "M-200"
    a, b = _port_of(netbox, "sw-acc-01", "Gi1/0/6"), _port_of(netbox, "mx-01", "xe-0/0/0")
    manual = netbox.add("dcim/cables", {"label": "M-1", "status": "connected"})
    for side, port in (("a_terminations", a), ("b_terminations", b)):
        manual[side] = [netbox._end({"object_type": "dcim.interface", "object_id": port["id"]})]
    a["cable"], b["cable"] = {"id": manual["id"]}, {"id": manual["id"]}

    # 2. Vorschau: beide Werte, je Kabel zu bestaetigen; nur in NetBox: bleibt
    plan = _plan(client, ids, sections=SECTIONS, allow_delete=True)
    labels = {i["key"]: i for i in _items(client, plan["id"], kind="cable_label")}
    assert set(labels) == {UPLINK, LEAF}
    up, lf = labels[UPLINK], labels[LEAF]
    assert (up["action"], up["confirm"], up["manual"]) == ("update", True, False)
    assert up["changes"] == [{"field": "label", "old": "K-ALT", "new": "K-100"}]
    assert (lf["action"], lf["manual"], lf["changes"][0]["old"]) == ("update", True, "M-200")
    assert "Kabelnummern: 1 nur in NetBox – bleiben, wie sie sind." in plan["warnings"]
    assert not _items(client, plan["id"], kind="cable", action="update,conflict,delete")

    # ohne Bestaetigung: nichts zu uebernehmen; unbekannte Schluessel zaehlen nicht
    r, _ = _apply(client, plan["id"])
    assert r.status_code == 409 and "confirm" in r.text
    r, _ = _apply(client, plan["id"], confirm=["gibts|nicht~x|y"])
    assert r.status_code == 409
    # nur die eigene bestaetigt: das von Hand gepflegte Kabel bleibt
    r, done = _apply(client, plan["id"], confirm=[UPLINK])
    assert r.status_code == 202 and done["status"] == "applied", done
    assert done["result"]["cable_label"]["update"] == 1
    assert done["options"]["confirm"] == [UPLINK]
    assert _cable(netbox, CABLE_UPLINK)["label"] == "K-100"
    leaf = _cable(netbox, CABLE_LEAF)
    assert leaf["label"] == "M-200" and not _tagged(leaf)
    assert netbox.db["dcim/cables"][manual["id"]]["label"] == "M-1"  # nie geloescht
    assert done["verification"]["deviation_count"] == 0  # Unbestaetigtes zaehlt nicht
    s = session_factory()
    detail = [audit.decode_detail(x) for x in s.query(AuditLog).order_by(AuditLog.id)
              if x.action == "netbox_sync.applied"][-1]  # fmt: skip
    s.close()
    assert detail["labels_confirmed"] == 1

    # 3. von Hand gepflegtes Kabel bestaetigt: nur das Label, Tags bleiben
    plan = _plan(client, ids, sections=SECTIONS)
    assert [i["key"] for i in _items(client, plan["id"], kind="cable_label")] == [LEAF]
    r, done = _apply(client, plan["id"], confirm=[LEAF])
    assert done["status"] == "applied", done
    leaf = _cable(netbox, CABLE_LEAF)
    assert leaf["label"] == "K-200" and leaf["tags"] == []
    again = _plan(client, ids, sections=SECTIONS)
    assert not _items(client, again["id"], kind="cable_label")
    assert again["summary"]["cable_label"]["unchanged"] == 2


def test_kabelnummer_englisch_und_ohne_verkabelung(client, netbox):  # noqa: F811
    ids = _setup_cabling(client, netbox)
    tid = ids["tid"]
    _label(client, tid, ("sw-core-01", "Eth1/3"), ("sw-acc-01", "Gi1/0/5"), "K-300")
    # ohne den Abschnitt Verkabelung: keine Kabelnummern
    plan = _plan(client, ids, sections=["inventory"])
    assert not _items(client, plan["id"], kind="cable_label")
    assert not any(w.startswith("Kabelnummern") for w in plan["warnings"])
    en = _plan(client, ids, headers=ADMIN_EN, sections=SECTIONS)["warnings"]
    assert any(w.startswith("Cable numbers: 1 without a cable in NetBox not applied") for w in en)
