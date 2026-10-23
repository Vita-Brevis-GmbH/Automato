"""Dienste wie Adressobjekte: Attribute, Namensschema-Pruefung, Detail mit Regeln; Regeln ohne
Quelle/Ziel (Internet Service, Contract ohne Consumer) - Dev-Befunde."""

import pytest

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}

OBJECTS = [
    {"name": "N_clients", "category": "address", "values": ["10.5.0.0/16"]},
    {"name": "FT_https", "category": "service", "obj_type": "TCP/UDP/SCTP", "values": ["tcp/443"],
     "attrs": {"protocol": "TCP/UDP/SCTP", "tcp-portrange": "443", "category": "Web Access"}},
    # Name verspricht tcp/8443, definiert ist tcp/9443 (Schema FT_<proto><port>)
    {"name": "FT_tcp8443", "category": "service", "values": ["tcp/9443"],
     "attrs": {"tcp-portrange": "9443:1024-65535"}},
    # Woerterbuch "default": FT_dns = tcp+udp/53, hier nur udp
    {"name": "FT_dns", "category": "service", "values": ["udp/53"]},
    {"name": "APP-8443", "category": "service", "values": ["tcp/8443"]},  # kein Praefix
    {"name": "HTTPS", "category": "service", "values": ["tcp/443"]},  # FortiOS vordefiniert
    {"name": "G_web", "category": "service_group", "members": ["FT_https", "APP-8443"],
     "values": ["tcp/443", "tcp/8443"]},
]  # fmt: skip


def _rule(ext_id, name, **kw):
    return {
        "source_platform": "fortigate",
        "external_id": ext_id,
        "name": name,
        "action": "allow",
        "sources": kw.get("sources", ["10.5.0.0/16"]),
        "destinations": kw.get("destinations", ["all"]),
        "services": kw.get("services", ["tcp/443"]),
        "source_refs": kw.get("source_refs", ["N_clients"]),
        "destination_refs": kw.get("destination_refs", ["all"]),
        "service_refs": kw.get("service_refs", ["G_web"]),
        "raw": kw.get("raw", {}),
    }


RULES = [
    _rule("1", "web-via-group"),
    _rule(
        "2",
        "o365",
        destinations=["isdb:Microsoft-Office365"],
        destination_refs=["Microsoft-Office365"],
        services=["internet-service"],
        service_refs=[],
        raw={"internet-service": "enable", "dstaddr-negate": "enable"},
    ),
]


@pytest.fixture
def no_sot(monkeypatch):
    class NoSoT:
        name = "netbox"

        def search_by_ip(self, ip):
            return []

        def get_by_name(self, name):
            return None

    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: NoSoT())


def _deliver(client, tid, sid, platform, **result):
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers=TOKEN,
        json={"status": "completed", **result},
    )
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup(client):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]

    def system(name, platform):
        body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x"}
        return client.post("/systems", json=body, headers=ADMIN).json()["id"]

    return tid, system("fw-01", "fortigate"), system("apic-01", "cisco_aci")


def _findings(client, tid, ftype):
    r = client.get("/findings", params={"tenant_id": tid, "type": ftype})
    return {f["subject_id"]: f for f in r.json()}


def _profile_with_dns(client, tid):
    """Namensprofil mit Woerterbuch-Eintrag FT_dns = tcp+udp/53 (wie das Profil default)."""
    pid = client.post(
        "/naming-profiles", json={"customer": "Kunde A", "name": "Std"}, headers=ADMIN
    ).json()["id"]
    for kind, key, value in (
        ("object_prefix", "N_", "network"),
        ("service", "FT_dns", "tcp+udp/53"),
    ):
        r = client.post(
            f"/naming-profiles/{pid}/dictionary",
            json={"kind": kind, "key": key, "value": value},
            headers=ADMIN,
        )
        assert r.status_code == 200, r.text
    client.patch(f"/tenants/{tid}", json={"naming_profile_id": pid}, headers=ADMIN)


def test_dienste_attribute_pruefung_und_detail(client, no_sot):
    tid, fw, _ = _setup(client)
    _profile_with_dns(client, tid)
    _deliver(client, tid, fw, "fortigate", rules=RULES, objects=OBJECTS)

    rows = client.get("/objects", params={"tenant_id": tid, "category": "service"}).json()["rows"]
    https = next(o for o in rows if o["name"] == "FT_https")
    assert https["attrs"]["category"] == "Web Access" and https["values"] == [{"value": "tcp/443"}]
    # Suche auch ueber Attribute (Quellports im Portbereich)
    hits = client.get("/objects", params={"tenant_id": tid, "q": "1024-65535"}).json()["rows"]
    assert [o["name"] for o in hits] == ["FT_tcp8443"]

    invalid = _findings(client, tid, "invalid_name")
    assert "APP-8443" in invalid and "HTTPS" not in invalid  # vordefiniert: keine Pruefung
    assert "FT_<proto><port>" in invalid["APP-8443"]["hint"]
    mismatch = _findings(client, tid, "service_mismatch")
    assert set(mismatch) == {"FT_tcp8443", "FT_dns"}
    assert mismatch["FT_tcp8443"]["message"] == (
        "Service „FT_tcp8443“ auf fw-01 definiert tcp/9443, laut Namensschema erwartet: tcp/8443."
    )
    assert mismatch["FT_dns"]["detail"]["rule"] == "Wörterbuch"
    assert mismatch["FT_dns"]["detail"]["expected"] == ["tcp/53", "udp/53"]
    assert "HTTPS" not in _findings(client, tid, "unused_object")

    # Detail: Regeln ueber die Gruppe, Mitglieder mit Werten
    (detail_id,) = (o["id"] for o in rows if o["name"] == "FT_https")
    d = client.get(f"/objects/{detail_id}").json()
    assert d["rule_count"] == 1 and [r["name"] for r in d["rules"]] == ["web-via-group"]
    grp = next(o for o in client.get("/objects", params={"tenant_id": tid}).json()["rows"]
               if o["name"] == "G_web")  # fmt: skip
    g = client.get(f"/objects/{grp['id']}").json()
    assert [m["name"] for m in g["members_detail"]] == ["FT_https", "APP-8443"]
    assert g["members_detail"][1]["values"] == ["tcp/8443"]
    assert client.get("/objects/999999").status_code == 404
    other = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid + 99)}
    assert client.get(f"/objects/{detail_id}", headers=other).status_code == 403

    # Internet Service als Ziel: kein "nicht im IPAM", Ziel sichtbar, negiert gekennzeichnet
    rule = next(r for r in client.get("/rules", params={"tenant_id": tid}).json()
                if r["name"] == "o365")  # fmt: skip
    assert rule["destinations"] == ["Microsoft-Office365"] and rule["missing"] == []
    assert rule["negate"] == ["dst"]
    assert "Microsoft-Office365" not in _findings(client, tid, "unmatched_object")


def test_aci_filter_als_dienst_und_contract_ohne_consumer(client, no_sot):
    tid, _, apic = _setup(client)
    filters = [
        {"name": "TE/FT_https", "category": "service", "obj_type": "vzFilter",
         "values": ["tcp/443"], "attrs": {"tenant": "TE", "entries": [{"prot": "tcp"}]}},
        {"name": "TE/web443", "category": "service", "obj_type": "vzFilter",
         "values": ["tcp/443"]},
    ]  # fmt: skip
    rules = [
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE/brc-CT_db/SJ_1",
            "name": "CT_db:SJ_1",
            "action": "allow",
            "sources": [],  # niemand konsumiert
            "destinations": ["epg:uni/tn-TE/ap-AP/epg-EG_db"],
            "services": ["tcp/443"],
            "service_refs": ["TE/FT_https"],
            "raw": {"tenant": "TE", "kind": "contract"},
        }
    ]
    _deliver(client, tid, apic, "cisco_aci", rules=rules, objects=filters)

    (rule,) = client.get("/rules", params={"tenant_id": tid}).json()
    assert rule["missing"] == ["src"] and rule["service_names"] == ["FT_https"]  # ohne Tenant
    (f,) = _findings(client, tid, "rule_incomplete").values()
    assert f["subject_id"] == "TE/CT_db:SJ_1" and f["severity"] == "info"
    assert f["message"] == (
        "Contract „CT_db:SJ_1“ (Tenant TE) auf apic-01 wird von keiner EPG konsumiert "
        "(kein Consumer) – er ist nicht wirksam."
    )
    # Filtername ohne Tenant geprueft: web443 ohne FT_-Praefix
    assert set(_findings(client, tid, "invalid_name")) == {"TE/web443"}
    rows = client.get("/objects", params={"tenant_id": tid, "category": "service"}).json()["rows"]
    https = next(o for o in rows if o["name"] == "TE/FT_https")
    assert https["used_in_rules"] == 1 and https["attrs"]["tenant"] == "TE"
