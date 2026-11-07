"""Objekte nach Präfix der Namenskonvention filtern (0.117.0, Anwender: „schön wäre auch filtern
nach Typ gemäss Namenskonvention (V-EXT, CVS, usw.)“) und Anzahl je Kategorie/Typ/Präfix für
die Übersicht."""

from app.models import DeviceObject, IpamType, Platform, SourceSystem, Tenant
from app.models.device_object import ObjectCategory
from app.models.tenant import DnsType
from app.store import naming_profiles as store

NAMES = [
    ("V-EXT_www.example.ch", ObjectCategory.VIP),
    ("VIP_intern", ObjectCategory.VIP),
    ("H_web01", ObjectCategory.ADDRESS),
    ("H-EXT_partner", ObjectCategory.ADDRESS),
    ("HX_web02", ObjectCategory.ADDRESS),  # kein H_: `_` ist kein Platzhalter
    ("N_dmz", ObjectCategory.ADDRESS),
    ("all", ObjectCategory.ADDRESS),
    ("FT_https", ObjectCategory.SERVICE),
]


def _seed(session_factory) -> int:
    s = session_factory()
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.flush()
    src = SourceSystem(tenant_id=t.id, name="fw-01", platform=Platform.FORTIGATE, base_url="")
    s.add(src)
    s.flush()
    for name, cat in NAMES:
        s.add(DeviceObject(tenant_id=t.id, source_system_id=src.id, category=cat, name=name,
                           obj_type="ipmask" if cat is ObjectCategory.ADDRESS else None,
                           values=["10.0.0.1"]))  # fmt: skip
    s.commit()
    tid = t.id
    s.close()
    return tid


def _names(client, tid, **params) -> tuple[list[str], dict]:
    r = client.get("/objects", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    return sorted(o["name"] for o in body["rows"]), body


def test_filter_nach_praefix(client, session_factory):
    tid = _seed(session_factory)
    names, body = _names(client, tid)
    assert body["prefixes"] == {"V-EXT_": 1, "VIP_": 1, "H_": 1, "H-EXT_": 1, "N_": 1,
                                "FT_": 1, "": 2}  # fmt: skip
    assert _names(client, tid, prefix="V-EXT_")[0] == ["V-EXT_www.example.ch"]
    assert _names(client, tid, prefix="H_")[0] == ["H_web01"]
    assert _names(client, tid, prefix="V-EXT_,VIP_")[0] == ["V-EXT_www.example.ch", "VIP_intern"]
    assert _names(client, tid, prefix="-")[0] == ["HX_web02", "all"]
    # mit anderen Filtern: Anzahl je Präfix in der gefilterten Menge
    _, body = _names(client, tid, category="vip")
    assert body["prefixes"] == {"V-EXT_": 1, "VIP_": 1}


def test_eigenes_praefix_aus_dem_namensprofil(client, session_factory):
    tid = _seed(session_factory)
    s = session_factory()
    profile = store.get_or_create_profile(s, customer="Kunde A", name="Standard")
    store.upsert_entry(s, profile.id, "object_prefix", "H_", "host")
    store.upsert_entry(s, profile.id, "object_prefix", "HX_", "cvs")  # eigenes, unbekannter Wert
    s.get(Tenant, tid).naming_profile_id = profile.id
    s.commit()
    s.close()
    names, body = _names(client, tid, prefix="HX_")
    assert names == ["HX_web02"]
    assert body["prefixes"] == {"H_": 1, "HX_": 1, "": 6}  # nur die Präfixe des Profils
    r = client.get("/objects/summary", params={"tenant_id": tid})
    assert r.json()["prefixes"] == {"H_": "host", "HX_": "cvs"}


def test_uebersicht_objekte(client, session_factory):
    tid = _seed(session_factory)
    r = client.get("/objects/summary", params={"tenant_id": tid})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total"] == len(NAMES)
    assert s["by_category"] == {"vip": 2, "address": 5, "service": 1}
    assert s["by_type"]["ipmask"] == 5
    assert s["by_prefix"]["V-EXT_"] == 1 and s["by_prefix"][""] == 2
    assert s["prefixes"]["V-EXT_"] == "vip_dnat"
