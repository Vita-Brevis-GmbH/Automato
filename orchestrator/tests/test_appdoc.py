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
    SourceSystem,
    Tenant,
)
from app.models.tenant import DnsType
from app.store import appdoc
from system_doc import system_doc, system_list


def test_categorize_pure():
    """Vorgaben (0.106.0): Richtung Zonentyp → Zonentyp; ohne Zone der Typ nach dem Namen der
    Firewall-Zone bzw. des L3Outs (Muster des Grundschemas, hier der Vorlage)."""
    from app.store import appdoc_catalog, zone_schema

    cat = appdoc_catalog.default()
    assert cat.by_flow("DC", "Public") == 1  # ausgehend ins Internet
    assert cat.by_flow("Edge Internet", "DC") == 2  # eingehend aus dem Internet
    assert cat.by_flow("Office Access", "DC") == 4 and cat.by_flow("DC", "Core") == 3
    assert cat.by_flow("Edge VPN", "DC") == 5 and cat.by_flow("DC", "Edge VPN") == 6
    assert cat.by_flow("DC", "DC") == 9
    assert cat.by_flow("Edge Management", "DC") == 11
    assert cat.by_flow("Core", "Public") is None and cat.by_flow(None, "DC") is None
    schema = zone_schema.default_schema()
    assert schema.match("L3O_inet").name == "Edge Internet"
    assert schema.match("L3O_vpn").name == "Edge VPN"
    assert schema.match("internet").name == "Public"
    assert schema.match("trust") is None


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
    rows = system_list(client, None)
    names = {r["name"] for r in rows}
    assert "H_app01.kunde.ch" in names and "H_db01.kunde.ch" in names
    doc = system_doc(client, "H_app01.kunde.ch", None)
    assert doc["system"] == "H_app01.kunde.ch"
    assert len(doc["tables"]) == 11  # seit 0.106.0 alle Kategorien des Katalogs


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

    rows = system_list(client, tid)
    assert any(r["name"] == "EG_a" and r["kind"] == "epg" for r in rows)

    doc = system_doc(client, "EG_a", tid)

    def rows_of(i):
        return next(t["rows"] for t in doc["tables"] if t["index"] == i)

    assert [r["Destination"] for r in rows_of(7)] == ["AP_001/EG_b"]  # ganzer ACI-Pfad (0.14.0)
    assert [r["Destination"] for r in rows_of(8)] == ["AP_002/EG_c"]
    assert [r["Destination"] for r in rows_of(9)] == ["EG_ext"]
