"""Applikations-Doku (systemzentriert, 10 Kategorien): Kategorisierung + Tabellenaufbau."""

from app.models import (
    EndpointKind,
    EndpointRole,
    Epg,
    IpamType,
    NatRule,
    NetworkObject,
    ObjectClass,
    Platform,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    Site,
    SourceSystem,
    Tenant,
)
from app.models.tenant import DnsType
from app.store import appdoc


def test_categorize_pure():
    # ausgehend ins Internet (externe Gegenstelle)
    assert (
        appdoc.categorize(
            direction="out", counterpart_zone=None, counterpart_external=True, is_dnat_target=False
        )
        == 1
    )
    # eingehend via DNAT/VIP
    assert (
        appdoc.categorize(
            direction="in", counterpart_zone=None, counterpart_external=False, is_dnat_target=True
        )
        == 2
    )
    # L3O-Zonen
    assert (
        appdoc.categorize(
            direction="in",
            counterpart_zone="L3O_bit",
            counterpart_external=False,
            is_dnat_target=False,
        )
        == 3
    )
    assert (
        appdoc.categorize(
            direction="out",
            counterpart_zone="L3O_bit",
            counterpart_external=False,
            is_dnat_target=False,
        )
        == 4
    )
    assert (
        appdoc.categorize(
            direction="out",
            counterpart_zone="L3O_vpn",
            counterpart_external=False,
            is_dnat_target=False,
        )
        == 5
    )
    assert (
        appdoc.categorize(
            direction="in",
            counterpart_zone="L3O_sra",
            counterpart_external=False,
            is_dnat_target=False,
        )
        == 6
    )
    assert (
        appdoc.categorize(
            direction="out",
            counterpart_zone="L3O_res",
            counterpart_external=False,
            is_dnat_target=False,
        )
        == 9
    )
    # Fallback: unspezifisch
    assert (
        appdoc.categorize(
            direction="out",
            counterpart_zone="trust",
            counterpart_external=False,
            is_dnat_target=False,
        )
        == 10
    )


def _seed(session):
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(name="fw", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True)
    session.add_all([t, fw])
    session.flush()

    sysobj = NetworkObject(
        tenant_id=t.id, name="H_app01.kunde.ch", object_class=ObjectClass.HOST, value="10.0.0.5"
    )
    ext = NetworkObject(
        tenant_id=t.id,
        name="H-EXT_api.partner.com",
        object_class=ObjectClass.EXTERNAL,
        value="203.0.113.9",
    )
    db = NetworkObject(
        tenant_id=t.id, name="H_db01.kunde.ch", object_class=ObjectClass.HOST, value="10.0.0.9"
    )
    session.add_all([sysobj, ext, db])
    session.flush()

    def mk_rule(ext_id, name, src_id, dst_id, protections=None):
        r = Rule(
            tenant_id=t.id,
            source_system_id=fw.id,
            domain=RuleDomain.FIREWALL,
            external_id=ext_id,
            name=name,
            action=RuleAction.ALLOW,
            protections=protections,
        )
        r.endpoints.append(
            RuleEndpoint(
                role=EndpointRole.SRC,
                kind=EndpointKind.HOST,
                network_object_id=src_id,
                matched=True,
            )
        )
        r.endpoints.append(
            RuleEndpoint(
                role=EndpointRole.DST,
                kind=EndpointKind.HOST,
                network_object_id=dst_id,
                matched=True,
            )
        )
        session.add(r)
        return r

    mk_rule("1", "out-internet", sysobj.id, ext.id, protections=["WF_default", "IPS_strict"])
    mk_rule("2", "in-dnat", ext.id, sysobj.id)
    mk_rule("3", "intern", sysobj.id, db.id)
    session.add(
        NatRule(
            tenant_id=t.id,
            source_system_id=fw.id,
            name="V-EXT_app01.kunde.ch",
            external_id="v1",
            nat_type="dnat_vip",
            ext_ip="203.0.113.5",
            mapped_ip="10.0.0.5",
        )
    )
    session.commit()
    return t.id


def _rows(doc, index):
    return next(tbl["rows"] for tbl in doc["tables"] if tbl["index"] == index)


def test_build_doc_kategorisiert_und_fuellt_spalten(session_factory):
    s = session_factory()
    tid = _seed(s)
    doc = appdoc.build_doc(s, "H_app01.kunde.ch", tid)
    assert doc is not None and doc["system_ip"] == "10.0.0.5"

    # Tabelle 1: ausgehend ins Internet, Schutzmechanismen aus protections
    t1 = _rows(doc, 1)
    assert len(t1) == 1
    assert t1[0]["Target Systems"] == "203.0.113.9"
    assert "WF_default" in t1[0]["Schutzmechanismen"]

    # Tabelle 2: eingehend via DNAT; VIP-IP + externer DNS-Name aus V-EXT_-Objekt
    t2 = _rows(doc, 2)
    assert len(t2) == 1
    assert t2[0]["VIP IP Adresse"] == "203.0.113.5"
    assert t2[0]["Externer DNS Name"] == "app01.kunde.ch"
    assert t2[0]["Server Name"] == "H_app01.kunde.ch"

    # Tabelle 10: interner Zugriff ohne Zonensignal -> unspezifisch
    assert len(_rows(doc, 10)) == 1
    # ACI-interne Tabellen bleiben in Model 2 leer
    assert _rows(doc, 7) == [] and _rows(doc, 8) == []


def test_build_doc_unbekanntes_system_none(session_factory):
    s = session_factory()
    tid = _seed(s)
    assert appdoc.build_doc(s, "H_gibtsnicht", tid) is None


def test_list_systems_und_api(session_factory, client):
    seed = session_factory()
    _seed(seed)
    rows = client.get("/appdoc/systems").json()
    names = {r["name"] for r in rows}
    assert "H_app01.kunde.ch" in names and "H_db01.kunde.ch" in names
    doc = client.get("/appdoc/systems/H_app01.kunde.ch").json()
    assert doc["system"] == "H_app01.kunde.ch"
    assert len(doc["tables"]) == 10


def test_site_verortung_und_tenant_code(session_factory, client):
    seed = session_factory()
    tid = _seed(seed)
    t = seed.get(Tenant, tid)
    t.code = "ssgch"
    site = Site(tenant_id=tid, code="000", name="RZ01 Hauptstandort")
    seed.add(site)
    seed.commit()
    sid = site.id

    # viewer darf nicht verorten
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    r = client.patch(
        f"/appdoc/systems/H_app01.kunde.ch/site?tenant_id={tid}",
        json={"site_id": sid},
        headers=viewer,
    )
    assert r.status_code == 403

    # operator verortet -> Doc zeigt tenant_code + site
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    r = client.patch(
        f"/appdoc/systems/H_app01.kunde.ch/site?tenant_id={tid}",
        json={"site_id": sid},
        headers=op,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_code"] == "ssgch"
    assert body["site"]["code"] == "000" and body["site"]["name"] == "RZ01 Hauptstandort"

    # auch im GET sichtbar
    doc = client.get(f"/appdoc/systems/H_app01.kunde.ch?tenant_id={tid}").json()
    assert doc["site"]["id"] == sid


def test_categorize_aci_pure():
    from app.store.appdoc import categorize_aci

    assert categorize_aci("application-profile", False, True) == 7
    assert categorize_aci("context", True, True) == 7  # gleiches AP
    assert categorize_aci("context", False, True) == 8  # anderes AP, gleicher Tenant
    assert categorize_aci("global", False, True) == 9
    assert categorize_aci(None, False, False) == 9  # anderer Tenant


def _seed_aci(session):
    t = Tenant(name="kunde-aci-doc", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name="apic", platform=Platform.CISCO_ACI, base_url="https://apic", enabled=True
    )
    session.add_all([t, fw])
    session.flush()
    epgs = {}
    for name, ap in [("EG_a", "AP_001"), ("EG_b", "AP_001"), ("EG_c", "AP_002")]:
        e = Epg(tenant_id=t.id, name=name, ap=ap)
        session.add(e)
        epgs[name] = e
    session.flush()

    def mk(ext, scope, src_name, dst_name):
        r = Rule(
            tenant_id=t.id,
            source_system_id=fw.id,
            domain=RuleDomain.ACI_CONTRACT,
            external_id=ext,
            name=ext,
            action=RuleAction.ALLOW,
            aci_scope=scope,
        )
        r.endpoints.append(
            RuleEndpoint(
                role=EndpointRole.SRC,
                kind=EndpointKind.EPG,
                epg_id=epgs[src_name].id if src_name in epgs else None,
                inline_value=src_name,
                matched=src_name in epgs,
            )
        )
        r.endpoints.append(
            RuleEndpoint(
                role=EndpointRole.DST,
                kind=EndpointKind.EPG,
                epg_id=epgs[dst_name].id if dst_name in epgs else None,
                inline_value=dst_name,
                matched=dst_name in epgs,
            )
        )
        session.add(r)

    mk("CT1", "context", "EG_a", "EG_b")  # gleiches AP -> 7
    mk("CT2", "context", "EG_a", "EG_c")  # anderes AP -> 8
    mk("CT3", "global", "EG_a", "EG_ext")  # global -> 9
    session.commit()
    return t.id


def test_epg_als_system_tabellen_789(session_factory, client):
    seed = session_factory()
    tid = _seed_aci(seed)

    rows = client.get(f"/appdoc/systems?tenant_id={tid}").json()
    assert any(r["name"] == "EG_a" and r["kind"] == "epg" for r in rows)

    doc = client.get(f"/appdoc/systems/EG_a?tenant_id={tid}").json()

    def rows_of(i):
        return next(t["rows"] for t in doc["tables"] if t["index"] == i)

    assert [r["Target Systems"] for r in rows_of(7)] == ["AP_001/EG_b"]  # ganzer ACI-Pfad (0.14.0)
    assert [r["Target Systems"] for r in rows_of(8)] == ["AP_002/EG_c"]
    assert [r["Target Systems"] for r in rows_of(9)] == ["EG_ext"]
