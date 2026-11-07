"""Gleiche Zonen nicht ueber eine Zone dazwischen (0.128.0, Anwender 2026-11-03: „Eine Internet
Firewall sieht zwar vielleicht alle Tenants, soll sie aber nicht mit einander verbinden“, auf den
Vorschlag eines Schalters: „er muss einfach nur die Rückwege zu einer gleichen Zone ein oder
ausschalten … es betrifft nur die Verbindungen zu gleichen Zonen unterschiedlicher Tenants“,
„Eventuell kannst du dies im Namensprofil einsetzen im Design“, „Generell sollte der Schalter aus
sein als Default“).

Zonenplan: die Zone der Internet-Firewall „Edge“ grenzt an „Transfer Kunde A“, „Transfer Kunde
B“ (beide Zonentyp Transfer) und „Internet“; „Server Kunde B“ haengt an „Transfer Kunde B“, „Core“
verbindet die beiden Transferzonen (der Weg ueber die Zonen-Firewall).
"""

from types import SimpleNamespace

from app.store import bgp_zones, zone_schema
from test_zone_schema_customer import SCHEMA
from test_zones import ADMIN

EDGE, TA, TB, INET, SRV_B, CORE = 1, 2, 3, 4, 5, 6


def _soll(joins: set[int] | None = None) -> bgp_zones.ZoneSoll:
    zs = bgp_zones.ZoneSoll.__new__(bgp_zones.ZoneSoll)
    names = {EDGE: "Edge", TA: "Transfer Kunde A", TB: "Transfer Kunde B", INET: "Internet",
             SRV_B: "Server Kunde B", CORE: "Core"}  # fmt: skip
    zs.zones = {i: SimpleNamespace(id=i, name=n) for i, n in names.items()}
    zs.types = {EDGE: "Edge", TA: "Transfer", TB: "Transfer", INET: "Internet", SRV_B: "Server",
                CORE: "Core"}  # fmt: skip
    zs.joins = joins or set()
    zs.adj = {}
    for a, b in ((EDGE, TA), (EDGE, TB), (EDGE, INET), (TB, SRV_B), (TA, CORE), (TB, CORE)):
        zs.adj.setdefault(a, set()).add(b)
        zs.adj.setdefault(b, set()).add(a)
    return zs


def test_internet_firewall_verbindet_gleiche_zonen_nicht():
    zs = _soll()
    # Ausgang der Internet-Firewall zu Kunde A (bzw. Eingang der Firewall von Kunde A von ihr):
    # Edge und Internet - nicht Kunde B (Transfer -> Edge -> Transfer ist gesperrt)
    assert zs.behind(TA, EDGE) == [EDGE, INET]
    # ohne Sperre kaemen Kunde B und seine Server dazu (gleich lang wie ueber den Core)
    assert set(zs.behind(TA, EDGE, strict=False)) == {EDGE, INET, TB, SRV_B}
    # Vorgabe aus: auch der Core verbindet die Transferzonen nicht - Kunde B ist nicht erreichbar
    assert SRV_B not in zs.dist(TA)
    # ins Internet ist kein Rueckweg zu einer gleichen Zone: offen
    assert zs.behind(INET, EDGE) == [EDGE, TA, TB, CORE, SRV_B]


def test_schalter_verbindet_gleiche_zonen():
    # Zonen-Firewall (Core) mit „verbindet gleiche Zonen“: Kunde B ueber sie, nicht ueber Edge
    zs = _soll(joins={CORE})
    assert zs.dist(TA)[SRV_B] == 3
    assert zs.behind(TA, EDGE) == [EDGE, INET]
    assert zs.behind(TA, CORE) == [CORE, TB, SRV_B]
    # auch die Internet-Firewall mit dem Schalter: Kunde B gleich lang ueber beide
    zs = _soll(joins={EDGE, CORE})
    assert set(zs.behind(TA, EDGE)) == {EDGE, INET, TB, SRV_B}


def test_abstand_je_weg():
    """0.130.0: die DMZ grenzt an Transfer Kunde A und Edge. Edge ist zuerst direkt erreicht
    (dort ist Transfer -> Edge -> Transfer gesperrt), Transfer Kunde B erst ueber den Umweg
    DMZ -> Edge - mit der Laenge des Umwegs (3), nicht der des ersten Wegs zu Edge (2)."""
    dmz = 7
    zs = _soll()
    zs.st = None
    zs.zones[dmz] = SimpleNamespace(id=dmz, name="DMZ")
    zs.types[dmz] = "DMZ"
    for other in (TA, EDGE):
        zs.adj.setdefault(dmz, set()).add(other)
        zs.adj[other].add(dmz)
    dist, path = zs.walk(TA)
    assert (dist[EDGE], dist[TB], dist[SRV_B]) == (1, 3, 4)
    assert path[TB] == [TA, dmz, EDGE, TB]
    # Weg je Zone in der Begruendung: Zone je Schritt (ohne Zonenplan ohne Netz)
    steps = zs._paths(TA, dmz, out=False)[TB]
    assert [s["zone"] for s in steps] == ["Transfer Kunde A", "DMZ", "Edge", "Transfer Kunde B"]


def test_zonen_ohne_zonentyp_ohne_sperre():
    zs = _soll()
    zs.types = {}
    assert set(zs.behind(TA, EDGE)) == {EDGE, INET, TB, SRV_B}


def test_grundschema_verbindet_gleiche_zonen(client):
    """Merkmal im Grundschema (Namensprofil → Design): Vorgabe aus, `automato_join_same` = ja,
    Zeichnung hin und zurueck, Bearbeiten in der Oberflaeche."""
    s = zone_schema.parse(SCHEMA)
    assert not any(z.joins_same for z in s.zones)  # Vorgabe aus
    marked = SCHEMA.replace(
        'label="Public" automato_kind="extern"',
        'label="Public" automato_kind="extern" automato_join_same="ja"',
    )
    by = {z.name: z for z in zone_schema.parse(marked).zones}
    assert by["Public"].joins_same and not by["Labor"].joins_same
    again = {z.name: z for z in zone_schema.parse(zone_schema.drawing(
        zone_schema.parse(marked), "Test")).zones}  # fmt: skip
    assert again["Public"].joins_same and not again["Server"].joins_same

    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Zonen"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    client.put(f"/naming-profiles/{pid}/zone-schema", json={"content": marked}, headers=ADMIN)
    got = client.get(f"/naming-profiles/{pid}/zone-schema", headers=ADMIN).json()
    assert {z["name"]: z["joins_same"] for z in got["schema"]["zones"]} == {
        "Labor": False, "Server": False, "Public": True}  # fmt: skip
    body = {"zones": [{"name": "Labor", "kind": "internal", "joins_same": True},
                      {"name": "Server", "kind": "internal"}], "links": []}  # fmt: skip
    r = client.patch(f"/naming-profiles/{pid}/zone-schema", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    got = client.get(f"/naming-profiles/{pid}/zone-schema", headers=ADMIN).json()
    assert {z["name"]: z["joins_same"] for z in got["schema"]["zones"]} == {
        "Labor": True, "Server": False}  # fmt: skip
