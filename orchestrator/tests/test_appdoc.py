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
    RuleIndex,
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

    def mk_rule(ext_id, name, src_id, dst_id, protections=None, action=RuleAction.ALLOW, nat=None):
        r = Rule(
            tenant_id=t.id,
            source_system_id=fw.id,
            domain=RuleDomain.FIREWALL,
            external_id=ext_id,
            name=name,
            action=action,
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
        session.flush()
        # NAT der Regel wie im Regelindex (SNAT-Pools bzw. Interface-IP, VIPs im Ziel)
        session.add(RuleIndex(rule_id=r.id, tenant_id=t.id, source_system_id=fw.id,
                              row={"nat": nat, "family": "v4"} if nat else {}))  # fmt: skip
        return r

    snat = {"snat": True, "pools": ["IPP_inet"], "vips": []}
    mk_rule("1", "out-internet", sysobj.id, ext.id, protections=["WF_default", "IPS_strict"],
            nat=snat)  # fmt: skip
    vip = {"snat": False, "pools": [], "vips": ["V-EXT_app01.kunde.ch"]}
    mk_rule("2", "in-dnat", ext.id, sysobj.id, nat=vip)
    mk_rule("3", "intern", sysobj.id, db.id)
    # sperrende Regel mit VIP und SNAT: kein NAT in der Doku (Dev-Befund 0.33.0)
    both = {"snat": True, "pools": [], "vips": ["V-EXT_app01.kunde.ch"]}
    mk_rule("4", "in-deny", ext.id, sysobj.id, action=RuleAction.DENY, nat=both)
    session.add(
        NatRule(
            tenant_id=t.id,
            source_system_id=fw.id,
            name="IPP_inet",
            external_id="p1",
            nat_type="snat_pool",
            ext_ip="198.51.100.7",
        )
    )
    session.add(  # ein weiterer Pool, den keine Regel nutzt
        NatRule(
            tenant_id=t.id,
            source_system_id=fw.id,
            name="IPP_intern",
            external_id="p2",
            nat_type="snat_pool",
            ext_ip="10.99.0.1",
        )
    )
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
    # Quelle und Ziel mit Name und IP (0.84.0)
    assert t1[0]["Source"] == "H_app01.kunde.ch (10.0.0.5)"
    assert t1[0]["Destination"] == "H-EXT_api.partner.com (203.0.113.9)"
    assert "WF_default" in t1[0]["Schutzmechanismen"]
    # SNAT nur aus der Regel: ihr Pool, nicht alle Pools des Mandanten (Dev-Befund 0.33.0)
    assert t1[0]["NAT Externe IPv4"] == "198.51.100.7"
    assert (t1[0]["Aktion"], t1[0]["Gerät"]) == ("Allow", "fw")
    assert "Bezug" not in doc["tables"][0]["columns"]

    # Tabelle 2: eingehend via DNAT; VIP-IP + externer DNS-Name aus V-EXT_-Objekt
    t2 = {r["Description"]: r for r in _rows(doc, 2)}
    assert set(t2) == {"in-dnat", "in-deny"}
    assert t2["in-dnat"]["VIP (Firewall)"] == "V-EXT_app01.kunde.ch: 203.0.113.5 → 10.0.0.5"
    assert t2["in-dnat"]["Externer DNS Name"] == "app01.kunde.ch"
    assert t2["in-dnat"]["Destination"] == "H_app01.kunde.ch (10.0.0.5)"
    assert t2["in-dnat"]["Source"] == "H-EXT_api.partner.com (203.0.113.9)"
    t2_cols = next(t["columns"] for t in doc["tables"] if t["index"] == 2)
    assert t2_cols[:5] == ["Source", "Externer DNS Name", "VIP (Firewall)", "Destination",
                          "TCP/UDP"]  # fmt: skip
    assert "Load Balancer" not in t2_cols  # ohne NSX ALB: leere Spalte ausgeblendet
    # sperrende Regel: Deny, ohne VIP
    deny = t2["in-deny"]
    assert deny["Aktion"] == "Deny" and not deny.get("VIP (Firewall)")
    assert not deny.get("Load Balancer")

    # Tabelle 10 (seit 0.78.2 nur Zugriffe auf das System): der Zugriff des Systems auf die DB
    # ohne Zonensignal steht nicht mehr da
    assert _rows(doc, 10) == []
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

    assert [r["Destination"] for r in rows_of(7)] == ["AP_001/EG_b"]  # ganzer ACI-Pfad (0.14.0)
    assert [r["Destination"] for r in rows_of(8)] == ["AP_002/EG_c"]
    assert [r["Destination"] for r in rows_of(9)] == ["EG_ext"]
