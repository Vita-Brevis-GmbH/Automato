"""Objekte (0.36.0): Standardobjekte des Herstellers zuunterst bzw. ausgeblendet und als Befund,
wenn eine erlaubende Regel sie verwendet; Filter nach Typ (dynamische SDN-Objekte); das
Service-Praefix FT_ in der Tabelle „Objekt-Praefix -> Typ“ pflegbar."""

import test_services as sv

ADMIN = sv.ADMIN

OBJECTS = [
    {"name": "FT_https", "category": "service", "values": ["tcp/443"]},
    {"name": "SVC_web", "category": "service", "values": ["tcp/80"]},
    {"name": "HTTPS", "category": "service", "values": ["tcp/443"]},  # FortiOS-Standard
    {"name": "ALL", "category": "service", "values": ["all"]},  # FortiOS-Standard
    {"name": "SSH", "category": "service", "values": ["tcp/22"]},  # Standard, ungenutzt
    {"name": "G_admin", "category": "service_group", "members": ["SSH", "FT_https"],
     "values": ["tcp/22", "tcp/443"]},
    {"name": "N_clients", "category": "address", "obj_type": "ipmask",
     "values": ["10.5.0.0/16"]},
    # FortiManager/ACI-Integration: dynamische Adresse aus dem SDN-Connector
    {"name": "TE_web_epg", "category": "address", "obj_type": "dynamic",
     "values": ["dynamic:TE_web_epg"], "attrs": {"sub-type": "sdn", "sdn": "aci-fmg"}},
]  # fmt: skip

RULES = [
    sv._rule("1", "web", service_refs=["HTTPS"], services=["tcp/443"]),
    sv._rule("2", "admin", service_refs=["G_admin"], services=["tcp/22", "tcp/443"]),
    {**sv._rule("3", "block-all", service_refs=["ALL"], services=["all"]), "action": "deny"},
    sv._rule("4", "svc", service_refs=["SVC_web", "FT_https"], services=["tcp/80", "tcp/443"]),
]


def _rows(client, tid, **params) -> dict:
    r = client.get("/objects", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_standardobjekte_unten_ausblenden_und_befund(client):
    tid, fw, _ = sv._setup(client)
    sv._deliver(client, tid, fw, "fortigate", rules=RULES, objects=OBJECTS)

    page = _rows(client, tid, category="service")
    names = [o["name"] for o in page["rows"]]
    assert names == ["FT_https", "SVC_web", "ALL", "HTTPS", "SSH"]  # Standardobjekte zuunterst
    assert [o["predefined"] for o in page["rows"]] == [False, False, True, True, True]
    assert page["predefined"] == 3
    hidden = _rows(client, tid, category="service", predefined="exclude")
    assert [o["name"] for o in hidden["rows"]] == ["FT_https", "SVC_web"]
    only = _rows(client, tid, category="service", predefined="only")
    assert only["total"] == 3

    # Befund: Standard-Service in einer erlaubenden Regel (direkt bzw. über eine Gruppe);
    # ALL nur in der sperrenden Regel, SSH über G_admin
    used = sv._findings(client, tid, "predefined_service_used")
    assert set(used) == {"HTTPS", "SSH"}
    assert used["HTTPS"]["detail"]["rules"] == ["web"]
    assert used["SSH"]["message"].startswith("Standardobjekt „SSH“ auf fw-01 wird in 1 erlaubenden")
    assert "unused_object" not in {f["type"] for f in used.values()}

    # Ohne Präfix FT_: SVC_web verletzt die Konvention (Standardprofil: FT_ als Service-Typ)
    assert "SVC_web" in sv._findings(client, tid, "invalid_name")


def test_typ_filter_dynamische_objekte(client):
    tid, fw, _ = sv._setup(client)
    sv._deliver(client, tid, fw, "fortigate", rules=RULES, objects=OBJECTS)

    page = _rows(client, tid, category="address")
    assert page["types"] == {"ipmask": 1, "dynamic": 1}
    dyn = _rows(client, tid, category="address", obj_type="dynamic")
    (row,) = dyn["rows"]
    assert (row["name"], row["attrs"]["sdn"]) == ("TE_web_epg", "aci-fmg")
    assert dyn["types"] == page["types"]  # Anzahl je Typ ohne den Typ-Filter
    rest = _rows(client, tid, category="address", obj_type="ipmask,-")
    assert [o["name"] for o in rest["rows"]] == ["N_clients"]
    # „Objekt öffnen“: genau dieses Objekt
    one = _rows(client, tid, category="address", object_id=row["id"])
    assert [o["name"] for o in one["rows"]] == ["TE_web_epg"] and one["total"] == 1


def test_service_praefix_im_namensprofil(client):
    """Ein eigenes Präfix vom Typ „service“ ersetzt FT_ (FT_https verletzt dann die
    Konvention, SVC_web nicht)."""
    tid, fw, _ = sv._setup(client)
    pid = client.post(
        "/naming-profiles", json={"customer": "Kunde A", "name": "SVC"}, headers=ADMIN
    ).json()["id"]
    r = client.post(
        f"/naming-profiles/{pid}/dictionary",
        json={"kind": "object_prefix", "key": "SVC_", "value": "service"},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    client.patch(f"/tenants/{tid}", json={"naming_profile_id": pid}, headers=ADMIN)
    sv._deliver(client, tid, fw, "fortigate", rules=RULES, objects=OBJECTS)

    invalid = sv._findings(client, tid, "invalid_name")
    assert "FT_https" in invalid and "SVC_web" not in invalid
    assert invalid["FT_https"]["detail"]["prefixes"] == ["SVC_"]
    # das Service-Präfix ist kein Adress-Präfix: N_clients bleibt ohne Befund
    assert "N_clients" not in invalid


def test_standardprofil_hat_ft_als_service_praefix(client):
    from app.naming import SERVICE_PREFIXES, service_prefixes
    from app.naming_defaults import DEFAULT_DICTIONARY

    assert ("FT_", "service") in DEFAULT_DICTIONARY["object_prefix"]
    assert SERVICE_PREFIXES == ("FT_",)
    assert service_prefixes({"H_": "host"}) == ("FT_",)  # ohne Service-Eintrag: Standard
    assert service_prefixes({"S_": "service"}, ["FT_"]) == ("S_", "FT_")
