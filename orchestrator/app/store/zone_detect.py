"""Logische Zonen erkennen und vorschlagen (0.54.0, ADR 0024 Nr. 10; seit 0.56.0 nach dem
Zonenmodell, Nr. 12).

Automato schlaegt Zonen aus dem Gelesenen vor; gespeichert wird erst, wenn der Operator einen
Vorschlag uebernimmt - als normale Zone mit Selektoren, also weiter abgeleitet. Das Zonenmodell
des Anwenders: Zonen umfassen **Knoten**, Netze zwischen Knoten verschiedener Zonen sind
**Uebergaenge** (`zone_struct`). Daraus die Vorschlaege:

1. **Datacenter aus dem ACI:** alle Tenants (ohne `infra`, `mgmt`) sind DC - je Kunde eine Zone
   „DC <Kunde>“ mit seinen Tenants (Kunde = sein Selektor ACI-Tenant), die uebrigen (z. B.
   `common`) in „DC“. Die L3Outs ergeben dann die Uebergaenge zum Edge.
2. **Firewalls:** je Firewall bzw. VDOM ohne Zone die Zone des Grundschemas (bzw. der
   eingebauten Vorlage), deren Name, Kurzname oder Muster auf Quellsystem, Hostname oder VDOM
   passt (z. B. `*inet*` -> Edge Internet); sonst Edge Internet, wenn sie ein Bein an einer
   Internet-Anbindung hat. Firewalls derselben Zone untereinander sind kein Uebergang. Ohne
   Treffer stehen sie unter „Knoten ohne Zone“ zum Zuordnen.
3. **Public aus den Anbindungen:** alle Internet-Anbindungen als eine Zone ohne die privaten
   Netze, sonst je Anbindung.
4. **IPAM-Merkmal** Zone bzw. Rolle (NetBox-Rolle, Custom Field bzw. DDI-Attribut).
5. **Firewall-Zonen und Interfaces**, deren Name auf ein Muster des Grundschemas bzw. der
   Vorlage passt (z. B. `transit` -> Edge Transfer, `mgmt` -> Edge Management).

6. **Vorlage je Kunde** (0.82.0): eine Zone des Grundschemas mit `automato_customer` = „je
   Kunde“ wird je Kunde vorgeschlagen - „<Zone> <Kunde>“ mit Kurzname „<Kurzname>-<Kuerzel>“
   und den Treffern (Interfaces, Firewall-Zonen, IPAM-Merkmale, Firewalls), die ganz in den
   Netzen bzw. VDOMs genau eines Kunden liegen. Treffer keines Kunden bleiben in der Zone selbst.

Transfernetze werden nicht mehr vorgeschlagen - sie liegen in der Zone ihrer Firewalls bzw. sind
Uebergaenge. Gleicher normalisierter Name = ein Vorschlag; Name, Art und Farbe aus der passenden
Zone des Grundschemas (sonst der Vorlage). Gibt es die Zone schon, ergaenzt der Vorschlag ihre
Selektoren. Ein Netz, das genau so schon in einer Zone liegt, schliesst der Vorschlag aus.
Ausgeblendete Vorschlaege (`zone_suggestion_dismissal`) erscheinen nur auf Wunsch.

Dazu der Abgleich mit dem gespeicherten Grundschema (0.55.0): Schema-Zonen ohne Zone und
Vorschlag, Zonen ausserhalb des Schemas, Absichten des Schemas (`schema_intents`).

Nur lesend; die Erkennung schreibt nichts.
"""

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import (
    Customer,
    LogicalZone,
    NamingProfile,
    TopoExit,
    ZonePolicy,
    ZoneSuggestionDismissal,
)
from app.store import customers, topo_model, zone_schema, zone_struct, zones
from app.store.customers import Net, Selector
from app.store.paths import Graph, Key

SOURCES = ("aci", "node", "exit", "ipam", "schema")
# Quellen fuer die Beschreibung einer uebernommenen Zone (gespeichert: deutsch)
SOURCE_LABEL = {
    "aci": "ACI-Tenants",
    "node": "Firewall",
    "exit": "Anbindung",
    "ipam": "IPAM",
    "schema": "Grundschema",
}
# Woerter im Namen -> Art der Zone (Teile des Namens, Ziffern am Ende ohne Bedeutung)
KIND_WORDS = {
    "dc": ("dc", "datacenter", "rechenzentrum", "rz"),
    "edge": ("edge", "perimeter"),
    "core": ("core",),
    "distribution": ("dist", "distribution", "verteilung"),
    "access": ("access", "zugang"),
    "management": ("mgmt", "mgt", "management", "oob", "inb", "ilo", "idrac", "ipmi"),
    "dmz": ("dmz",),
    "transfer": ("transfer", "transit", "p2p", "ptp", "interco", "interconnect"),
    "external": (
        "internet",
        "wan",
        "outside",
        "untrust",
        "untrusted",
        "extern",
        "external",
        "isp",
        "public",
        "provider",
        "upstream",
    ),  # fmt: skip
    "shared": ("shared", "common", "infra", "infrastructure"),
}
KIND_ORDER = ("dc", "internal", "shared", "edge", "dmz", "management", "transfer", "core",
              "distribution", "access", "external")  # fmt: skip
# IPAM-Merkmale, die eine Zone nennen (klein geschrieben)
ZONE_ATTRS = ("zone", "zone_name", "zonename", "security_zone", "securityzone", "sicherheitszone",
              "netzzone", "network_zone", "role", "netzrolle")  # fmt: skip
PRIVATE = {4: ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"),
           6: ("fc00::/7",)}  # fmt: skip
FABRIC_ONLY = {"infra", "mgmt"}  # ACI-Tenants der Fabric selbst - kein Datacenter
_NOISE = {"zone", "zn", "z", "vlan", "vl", "if", "intf", "iface"}
_SPLIT = re.compile(r"[_\-|/.:\s]+")
SAMPLES = 10
REST = 200
# Grund der Netze hinter einer Anbindung (`customers._label`) - in beiden Sprachen (0.87.0)
_EXIT = ("Anbindung ", "Exit ")


def _schema_reason(sz: "zone_schema.SchemaZone") -> str:
    return L(f"Grundschema „{sz.name}“", f"Base schema “{sz.name}”")


def _name_reason(word: str) -> str:
    return L(f"Name „{word}“", f"name “{word}”")


def _parts(name: str | None, strip_digits: bool = False) -> list[str]:
    out = []
    for t in _SPLIT.split((name or "").casefold()):
        if strip_digits:
            t = t.rstrip("0123456789")
        if t and not t.isdigit() and t not in _NOISE:
            out.append(t)
    return out


def key_of(name: str | None, strip_digits: bool = False) -> str:
    """Normalisierter Name: `Z_DMZ` = `dmz`; mit `strip_digits` auch `wan1` = `wan`."""
    return "-".join(_parts(name, strip_digits)) or (name or "").strip().casefold()


def classify(*names: str | None) -> tuple[str, str] | None:
    """Art aus dem Namen und das Wort, das sie ergab."""
    words = {t for n in names for t in _parts(n, strip_digits=True)}
    for kind, hints in KIND_WORDS.items():
        for w in hints:
            if w in words:
                return kind, w
    return None


def _sel_key(s: Selector) -> tuple:
    return (s.kind, s.value.strip().casefold(), s.source_system_id, s.exclude)


def _sel_out(s: Selector) -> dict:
    return {"kind": s.kind, "value": s.value, "source_system_id": s.source_system_id,
            "exclude": s.exclude}  # fmt: skip


@dataclass
class Candidate:
    key: str
    name: str
    source: str
    kind: str = "internal"
    reason: str | None = None
    code: str | None = None
    customer_id: int | None = None
    selectors: list[Selector] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    nodes: list[Key] = field(default_factory=list)
    nets: dict[Net, str] = field(default_factory=dict)
    epgs: dict[int, str] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    target: LogicalZone | None = None
    schema: "zone_schema.SchemaZone | None" = None

    @property
    def rank(self) -> int:
        return min(SOURCES.index(s) for s in self.sources)

    def add(self, s: Selector) -> None:
        if len(s.value) <= 400 and _sel_key(s) not in {_sel_key(x) for x in self.selectors}:
            self.selectors.append(s)

    def note(self, text: str) -> None:
        if text not in self.evidence:
            self.evidence.append(text)


class _Found:
    """Gesammelte Vorschlaege je normalisiertem Namen; Name, Art und Farbe aus der passenden
    Zone des Grundschemas bzw. der Vorlage."""

    def __init__(self, schema: "zone_schema.Schema") -> None:
        self.by_key: dict[str, Candidate] = {}
        self.schema = schema

    def put(self, key: str, name: str, source: str, kind: str | None, reason: str | None,
            selectors: list[Selector], evidence: str, names: tuple = (),
            sz: "zone_schema.SchemaZone | None" = None,
            customer_id: int | None = None) -> Candidate:  # fmt: skip
        if sz is None and customer_id is None:
            sz = self.schema.match(name, *names)
        if sz is not None and customer_id is None:
            key, name = sz.key, sz.name
        c = self.by_key.get(key)
        if c is None:
            c = self.by_key[key] = Candidate(key, name, source, customer_id=customer_id)
            if sz is not None:
                c.schema, c.kind, c.code = sz, sz.kind, sz.code
                c.reason = _schema_reason(sz)
        if source not in c.sources:
            c.sources.append(source)
        if c.schema is None and kind and c.kind == "internal":
            c.kind, c.reason = kind, reason
        for s in selectors:
            c.add(s)
        c.note(evidence)
        return c


# -- Quellen ------------------------------------------------------------------------------


def _from_aci(found: _Found, data: customers._Data, owners: dict[str, Customer]) -> None:
    """Alle Tenants sind Datacenter - je Kunde eine Zone, die uebrigen gemeinsam."""
    tenants: dict[str, int] = defaultdict(int)
    for e in data.epgs:
        if e.kind == "epg" and e.aci_tenant and e.aci_tenant.casefold() not in FABRIC_ONLY:
            tenants[e.aci_tenant] += 1
    if not tenants:
        return
    dc = found.schema.of_kind("dc")
    base = dc.name if dc else "DC"
    for t, n in sorted(tenants.items()):
        cust = owners.get(t.casefold())
        sel = [Selector("aci_tenant", t)]
        if cust is None:
            found.put(key_of(base), base, "aci", "dc", L("ACI-Tenant", "ACI tenant"), sel,
                      f"Tenant {t}: {n} EPGs", sz=dc)  # fmt: skip
        else:
            name = f"{base} {cust.name}"
            why = L("ACI-Tenant des Kunden", "ACI tenant of the customer")
            c = found.put(f"dc:{cust.id}", name, "aci", "dc", why, sel,
                          f"Tenant {t}: {n} EPGs", customer_id=cust.id)  # fmt: skip
            c.code = (
                f"{dc.code if dc and dc.code else 'DC'}-{cust.code.upper()}" if cust.code else None
            )
            if dc is not None:
                c.schema, c.kind, c.reason = dc, dc.kind, _schema_reason(dc)


def _internet_nodes(data: customers._Data, exits: list[TopoExit]) -> dict[Key, str]:
    """Firewalls bzw. VDOMs mit einem Bein an einer Internet-Anbindung (Next-Hop im Netz eines
    Interfaces bzw. Interface der Anbindung) - die Internet-Firewalls des Edge."""
    out: dict[Key, str] = {}
    for e in exits:
        if e.kind != "internet":
            continue
        ports = {p.casefold() for p in e.interfaces or []}
        hops = [a for h in e.next_hops or [] if (a := _ip(h)) is not None]
        for sid, name, vdom, _zone, ips in data.interfaces:
            if e.source_system_ids and sid not in e.source_system_ids:
                continue
            nets = [n for ip in ips or [] if (n := _iface_net(ip)) is not None]
            hit = any(h.version == n.version and h in n for h in hops for n in nets)
            if hit or (name or "").casefold() in ports:
                out.setdefault((sid, vdom), L(f"Anbindung {e.name} über {name}",
                                              f"Exit {e.name} via {name}"))  # fmt: skip
    return out


def _ip(value) -> "ipaddress.IPv4Address | ipaddress.IPv6Address | None":
    try:
        return ipaddress.ip_address(str(value).split("/")[0])
    except ValueError:
        return None


def _iface_net(value) -> Net | None:
    try:
        return ipaddress.ip_interface(str(value)).network
    except ValueError:
        return None


def _edge_internet(schema: "zone_schema.Schema") -> "zone_schema.SchemaZone | None":
    """Zone des Schemas fuer die Internet-Firewalls: Edge mit „inet“/„internet“ im Namen bzw.
    Muster."""
    for sz in schema.zones:
        words = " ".join([sz.name, sz.code or "", *sz.match]).casefold()
        if sz.kind == "edge" and ("inet" in words or "internet" in words):
            return sz
    return None


def _from_nodes(found: _Found, g: Graph, assigned: dict[Key, int],
                internet: dict[Key, str]) -> list[Key]:  # fmt: skip
    """Firewalls ohne Zone: Zone der Vorlage nach Quellsystem, Hostname bzw. VDOM, sonst Edge
    Internet bei einem Bein an einer Internet-Anbindung; der Rest kommt in die Liste zum
    Zuordnen."""
    rest = []
    inet = _edge_internet(found.schema)
    for key, n in sorted(g.nodes.items(), key=lambda x: x[1].label):
        if n.kind not in ("firewall", "vdom") or key in assigned:
            continue
        names = (n.source, *n.members, key[1] if n.kind == "vdom" else "")
        sz, why = found.schema.match(*names), f"Firewall {n.label}"
        if sz is None and key in internet and inet is not None:
            sz, why = inet, f"Firewall {n.label}: {internet[key]}"
        if sz is None:
            rest.append(key)
            continue
        c = found.put(sz.key, sz.name, "node", sz.kind, None,
                      [Selector("fw_vdom", key[1], key[0])], why, sz=sz)  # fmt: skip
        c.nodes.append(key)
    return rest


def _from_exits(found: _Found, exits: list[TopoExit], used: set[str]) -> None:
    public = found.schema.of_kind("external")
    for e in exits:
        if (e.name or "").casefold() in used or f"#{e.id}" in used:
            continue
        sel = [Selector("exit", e.name)]
        nets = [n for p in e.prefixes or [] if (n := customers._net(str(p))) is not None]
        if e.kind == "internet" or any(n.prefixlen == 0 for n in nets):
            fams = {n.version for n in nets if n.prefixlen == 0}
            sel += [Selector("prefix", p, None, True) for f in sorted(fams) for p in PRIVATE[f]]
        sz = public if e.kind == "internet" else None
        name = (sz.name if sz else "Internet") if e.kind == "internet" else e.name
        shown = f": {', '.join(map(str, nets[:3]))}" if nets else ""
        found.put(key_of(name), name, "exit", "external",
                  L(f"Anbindung ({e.kind})", f"Exit ({e.kind})"), sel,
                  L(f"Anbindung {e.name}{shown}", f"Exit {e.name}{shown}"), (e.name,),
                  sz=sz)  # fmt: skip


def aci_names(data: customers._Data) -> set[str]:
    """Namen der ACI-Objekte (Tenant, VRF, AP, BD, EPG, ESG, externe EPG, L3Out), klein und
    normalisiert - ein IPAM-Merkmal mit einem solchen Namen (z. B. die ESGs `SZ_…`) ist eine
    Gruppe im ACI, keine logische Zone (das ACI ist schon das Datacenter)."""
    out: set[str] = set()
    for e in data.epgs:
        for v in (e.name, e.aci_tenant, e.vrf, e.ap, e.bd, e.l3out):
            if v and v.strip():
                out |= {v.strip().casefold(), key_of(v)}
    return out


def _from_ipam(found: _Found, data: customers._Data) -> None:
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for _kind, _value, _vrf, _tenant, _tags, attrs in data.references:
        for k, v in (attrs or {}).items():
            if str(k).casefold() in ZONE_ATTRS and str(v).strip():
                seen[(str(k), str(v).strip())] += 1
    aci = aci_names(data)  # 0.65.1: Gruppen des ACI nicht als Zone
    for (k, v), n in sorted(seen.items()):
        if v.casefold() in aci or key_of(v) in aci:
            continue
        hit = classify(v)
        kind, reason = (hit[0], _name_reason(hit[1])) if hit else (None, None)
        found.put(key_of(v), v, "ipam", kind, reason, [Selector("ipam_tag", f"{k}={v}")],
                  L(f"IPAM {k}={v} ({n} Einträge)",
                    f"IPAM {k}={v} ({n} {'entry' if n == 1 else 'entries'})"),
                  (v,))  # fmt: skip


def _from_schema_interfaces(found: _Found, schema: "zone_schema.Schema", data: customers._Data,
                            g: Graph) -> None:  # fmt: skip
    """Firewall-Zonen und Interfaces, deren Name auf ein Muster des Grundschemas bzw. der
    Vorlage passt - Netze hinter einer Firewall in einer anderen Zone (Transfer, Management,
    Office-Access)."""
    for sid, name, vdom, zone, ips in data.interfaces:
        if not ips:
            continue
        label = g.nodes[(sid, vdom)].label if (sid, vdom) in g.nodes else f"#{sid}"
        if zone and (sz := schema.match(zone)) is not None:
            found.put(sz.key, sz.name, "schema", sz.kind, None, [Selector("fw_zone", zone)],
                      f"Zone {zone} · {label}", sz=sz)  # fmt: skip
        elif not zone and (sz := schema.match(name)) is not None:
            found.put(sz.key, sz.name, "schema", sz.kind, None,
                      [Selector("fw_interface", name, sid)], f"Interface {name} · {label}",
                      sz=sz)  # fmt: skip


def _per_customer(found: _Found, data: customers._Data, session: Session,
                  tenant_id: int) -> None:  # fmt: skip
    """Vorlagen je Kunde (0.82.0): Selektoren bzw. Firewalls, die ganz einem Kunden gehoeren,
    in einen eigenen Vorschlag des Kunden verschieben."""
    templates = [c for c in found.by_key.values() if c.schema is not None
                 and c.schema.per_customer and c.customer_id is None]  # fmt: skip
    if not templates:
        return
    owners = [cust for cust in session.scalars(
        select(Customer).where(Customer.tenant_id == tenant_id).order_by(Customer.name)
    ) if not cust.shared]  # fmt: skip
    if not owners:
        return
    ranges, vdoms = {}, {}
    for cust in owners:
        sel = [Selector(s.kind, s.value, s.source_system_id, s.exclude) for s in cust.selectors]
        ranges[cust.id] = customers._Ranges(customers.nets_of(data, sel)[1])
        vdoms[cust.id] = {(s.source_system_id, s.value.strip().casefold())
                          for s in sel if s.kind == "fw_vdom" and not s.exclude}  # fmt: skip
    by_id = {cust.id: cust for cust in owners}

    def owner_of_nets(s: Selector) -> int | None:
        nets = list(customers.nets_of(data, [s])[1])
        if not nets:
            return None
        hits = [cid for cid, r in ranges.items()
                if all(r.covers(n.version, int(n.network_address), int(n.broadcast_address))
                       for n in nets)]  # fmt: skip
        return hits[0] if len(hits) == 1 else None

    def owner_of_node(key: Key) -> int | None:
        hits = [cid for cid, v in vdoms.items()
                if (key[0], key[1].casefold()) in v or (None, key[1].casefold()) in v]  # fmt: skip
        return hits[0] if len(hits) == 1 else None

    for base in templates:
        sz = base.schema
        moved: dict[int, list] = defaultdict(list)
        for s in list(base.selectors):
            node = next((k for k in base.nodes if s.kind == "fw_vdom" and k == (s.source_system_id,
                         s.value)), None)  # fmt: skip
            cid = owner_of_node(node) if node is not None else owner_of_nets(s)
            if cid is not None:
                moved[cid].append((s, node))
        for cid, items in moved.items():
            cust = by_id[cid]
            name, code = sz.for_customer(cust.name, cust.code)
            c = found.by_key.get(f"{sz.key}:{cid}")
            if c is None:
                c = found.by_key[f"{sz.key}:{cid}"] = Candidate(
                    f"{sz.key}:{cid}", name, base.source, kind=sz.kind,
                    reason=L(f"Grundschema „{sz.name}“ je Kunde",
                             f"Base schema “{sz.name}” per customer"),
                    code=code, customer_id=cid,
                    schema=sz)  # fmt: skip
            for source in base.sources:
                if source not in c.sources:
                    c.sources.append(source)
            for s, node in items:
                base.selectors.remove(s)
                c.add(s)
                if node is not None:
                    base.nodes.remove(node)
                    c.nodes.append(node)
            c.note(L(f"je Kunde: {len(items)} Treffer von {cust.name}",
                     f"per customer: {len(items)} matches of {cust.name}"))  # fmt: skip
        if moved and not base.selectors and not base.nodes:
            del found.by_key[base.key]  # alles gehoert Kunden


# -- Auswertung ---------------------------------------------------------------------------


def dismissed(session: Session, tenant_id: int) -> set[str]:
    return set(session.scalars(
        select(ZoneSuggestionDismissal.key).where(ZoneSuggestionDismissal.tenant_id == tenant_id)
    ))  # fmt: skip


def _target(c: Candidate, items: list[LogicalZone],
            schema: "zone_schema.Schema | None" = None) -> LogicalZone | None:  # fmt: skip
    """Bestehende Zone, die der Vorschlag ergaenzt: gleicher Name bzw. Kurzname, sonst dieselbe
    Zone des Grundschemas bzw. der Vorlage (z. B. „Internet“ = Public)."""
    for z in items:
        if c.key == key_of(z.name) or (z.code and c.key == key_of(z.code)):
            return z
    for z in items:
        if c.schema is not None and c.customer_id is None and z.customer_id is None \
                and schema is not None and schema.match(z.name, z.code) is c.schema:  # fmt: skip
            return z
    for z in items:
        if c.customer_id is not None and z.customer_id == c.customer_id and z.kind == c.kind \
                and key_of(z.name) == key_of(c.name):  # fmt: skip
            return z
    return None


def _owners(session: Session, tenant_id: int) -> dict[str, Customer]:
    """ACI-Tenant -> Kunde (genau einer nennt ihn als Selektor)."""
    found: dict[str, list[Customer]] = defaultdict(list)
    for cust in session.scalars(select(Customer).where(Customer.tenant_id == tenant_id)):
        if cust.shared:
            continue
        for s in cust.selectors:
            if s.kind == "aci_tenant" and not s.exclude:
                found[s.value.strip().casefold()].append(cust)
    return {t: cs[0] for t, cs in found.items() if len(cs) == 1}


def node_row(g: Graph, key: Key, hint: "zone_schema.SchemaZone | None" = None) -> dict:
    n = g.nodes[key]
    hit = classify(n.source, *n.members, key[1])
    return {"id": topo_model.node_id(key), "label": n.label, "kind": n.kind,
            "source_system_id": key[0], "context": key[1], "group": n.group,
            "hint": hint.name if hint else None, "kind_hint": hit[0] if hit else None}  # fmt: skip


def suggest(session: Session, tenant_id: int, include_dismissed: bool = False) -> dict:
    """Vorschlaege fuer logische Zonen nach dem Zonenmodell (siehe Modul-Doku)."""
    data = customers._Data(session, tenant_id)
    items = zones.load(session, tenant_id)
    g = Graph(session, tenant_id)
    st = zone_struct.build(session, tenant_id, items, data, g)
    existing = zones.derive(session, tenant_id, items, data, st)
    names = {z.id: z.name for z in items}
    owned: dict[Net, int] = {}
    for zn in existing:
        for net in zn.nets:
            owned.setdefault(net, zn.id)
    exits = list(session.scalars(select(TopoExit).where(TopoExit.tenant_id == tenant_id)))
    used = {s.value.strip().casefold() for z in items for s in z.selectors
            if s.kind == "exit" and not s.exclude}  # fmt: skip

    schema, profile = zone_schema.for_tenant(session, tenant_id)
    found = _Found(schema or zone_schema.default_schema())
    _from_aci(found, data, _owners(session, tenant_id))
    rest_nodes = _from_nodes(found, g, st.node_zone, _internet_nodes(data, exits))
    _from_exits(found, exits, used)
    _from_ipam(found, data)
    _from_schema_interfaces(found, found.schema, data, g)
    _per_customer(found, data, session, tenant_id)  # Vorlagen je Kunde (0.82.0)

    # Netze je Vorschlag (ohne die Knoten-Selektoren); ein Netz, das genau so schon in einer
    # Zone bzw. einem frueheren Vorschlag liegt, schliesst der Vorschlag aus
    claimed: dict[Net, str] = {}
    cands = sorted(found.by_key.values(), key=lambda c: (c.rank, c.key))
    for c in cands:
        c.target = _target(c, items, found.schema)
        explicit = [s for s in c.selectors if s.kind != "fw_vdom"]
        c.epgs, c.nets = customers.nets_of(data, explicit)
        mine = c.target.id if c.target else None
        for net in sorted(c.nets, key=zones._net_key):
            other = owned.get(net)
            if other is not None and other != mine:
                c.conflicts.append({"network": str(net), "zone": names[other]})
            elif net in claimed:
                c.conflicts.append({"network": str(net), "suggestion": claimed[net]})
            else:
                continue
            c.add(Selector("prefix", str(net), None, True))
        if c.conflicts:
            c.epgs, c.nets = customers.nets_of(data, [s for s in c.selectors
                                                      if s.kind != "fw_vdom"])  # fmt: skip
        for net in c.nets:
            claimed.setdefault(net, c.name)

    hidden = dismissed(session, tenant_id)
    cust_names = dict(session.execute(
        select(Customer.id, Customer.name).where(Customer.tenant_id == tenant_id)
    ).all())  # fmt: skip
    colors = zones.colors(session, tenant_id, items)
    out = []
    for c in cands:
        new_nets, new_sel = c.nets, c.selectors
        target = c.target
        if target is not None:
            have = {_sel_key(s) for s in zones.selectors_of(target)}
            new_sel = [s for s in c.selectors if _sel_key(s) not in have]
            own = next((zn.nets for zn in existing if zn.id == target.id), {})
            new_nets = {n: w for n, w in c.nets.items() if n not in own}
            if not new_sel:
                continue
        elif not (c.nets or c.epgs or c.nodes or "exit" in c.sources):
            continue
        is_hidden = c.key in hidden
        if is_hidden and not include_dismissed:
            continue
        # Netze hinter einer Anbindung (0.0.0.0/0 ohne die privaten = viele Stuecke) als ein
        # Eintrag je Anbindung
        groups: dict[str, list[Net]] = defaultdict(list)
        for net, why in new_nets.items():
            groups[why if why.startswith(_EXIT) else str(net)].append(net)
        plain = sorted((n for n, why in new_nets.items() if not why.startswith(_EXIT)),
                       key=zones._net_key)  # fmt: skip
        samples = [str(n) for n in plain] + sorted(x for x in groups if x.startswith(_EXIT))
        cust_id = target.customer_id if target else c.customer_id
        if target is not None:
            color = colors.get(target.id, {}).get("color")
        else:
            color = (c.schema.color if c.schema else None) or zones.KIND_COLOR.get(c.kind)
        out.append({
            "key": c.key,
            "action": "extend" if target else "create",
            "zone_id": target.id if target else None,
            "zone": target.name if target else None,
            "name": target.name if target else c.name,
            "code": target.code if target else c.code,
            "kind": target.kind if target else c.kind,
            "kind_reason": None if target else c.reason,
            "color": color,
            "schema": c.schema.name if c.schema else None,
            "customer_id": cust_id,
            "customer": cust_names.get(cust_id) if cust_id else None,
            "sources": c.sources,
            "evidence": c.evidence[:SAMPLES],
            "evidence_total": len(c.evidence),
            "selectors": [_sel_out(s) for s in new_sel],
            "nodes": [g.nodes[k].label for k in c.nodes],
            "counts": {"nets": len(groups), "epgs": len(c.epgs), "nodes": len(c.nodes)},
            "samples": samples[:SAMPLES],
            "conflicts": c.conflicts[:SAMPLES],
            "dismissed": is_hidden,
        })  # fmt: skip
    out.sort(key=lambda s: (s["action"] != "extend", KIND_ORDER.index(s["kind"])
                            if s["kind"] in KIND_ORDER else 99, s["name"].casefold()))  # fmt: skip
    offered = {c.schema.key for c in cands if c.schema is not None}
    covered = customers._Ranges(n for c in cands for n in c.nets)
    placed = {topo_model.node_id(k) for c in cands for k in c.nodes}  # Netze ihrer Knoten
    rest = [u for u in st.unzoned
            if not _covered(covered, u["network"]) and not placed & set(u["nodes"])]  # fmt: skip
    return {
        "items": out,
        "nodes": [node_row(g, k) for k in rest_nodes],
        "schema": _schema_status(session, tenant_id, schema, profile, items, offered),
        "rest": {"total": len(rest), "items": rest[:REST]},
        "summary": {
            "suggestions": sum(1 for s in out if not s["dismissed"]),
            "create": sum(1 for s in out if s["action"] == "create" and not s["dismissed"]),
            "extend": sum(1 for s in out if s["action"] == "extend" and not s["dismissed"]),
            "dismissed": len(hidden),
            "nodes": len(rest_nodes),
            "rest": len(rest),
            "template": schema is None,
        },
    }


def _covered(ranges: customers._Ranges, network: str) -> bool:
    net = customers._net(network)
    return net is not None and ranges.covers(net.version, int(net.network_address),
                                             int(net.broadcast_address))  # fmt: skip


def _mapped(
    schema: "zone_schema.Schema", items: list[LogicalZone], owners: dict | None = None,
    manual: bool = True,
) -> dict[int, "zone_schema.SchemaZone"]:  # fmt: skip
    """Bestehende Zonen -> Zone des Grundschemas (Name, Kurzname bzw. Erkennungsmuster); Zonen
    eines Kunden auch ueber die Vorlage je Kunde ohne Name bzw. Kuerzel des Kunden (0.82.0);
    seit 0.105.0 zuerst der Zonentyp von Hand (`zone_type`, ADR 0035)."""
    out = {}
    for z in items:
        sz = schema.zone(z.zone_type) if manual and getattr(z, "zone_type", None) else None
        if sz is not None:
            out[z.id] = sz
            continue
        if z.customer_id is not None and owners and z.customer_id in owners:
            sz = schema.of_customer_zone(z.name, z.code, *owners[z.customer_id])
        sz = sz or schema.match(z.name, z.code)
        if sz is not None:
            out[z.id] = sz
    return out


def _customer_names(session: Session, tenant_id: int) -> dict[int, tuple[str, str | None]]:
    return {cid: (name, code) for cid, name, code in session.execute(
        select(Customer.id, Customer.name, Customer.code).where(Customer.tenant_id == tenant_id)
    )}  # fmt: skip


def outside_schema(session: Session, tenant_id: int) -> tuple[str | None, list[LogicalZone]]:
    """Zonen ausserhalb des Grundschemas (0.78.0): (Namensprofil, Zonen ohne passende
    Schema-Zone); ohne Grundschema keine."""
    schema, profile = zone_schema.for_tenant(session, tenant_id)
    if schema is None or profile is None or not schema.zones:
        return None, []
    items = zones.load(session, tenant_id)
    mapped = _mapped(schema, items, _customer_names(session, tenant_id))
    name = profile.name if profile.customer == profile.name else (
        f"{profile.customer} · {profile.name}")  # fmt: skip
    return name, [z for z in items if z.id not in mapped]


def schema_intents(session: Session, tenant_id: int, schema: "zone_schema.Schema | None" = None,
                   items: list[LogicalZone] | None = None) -> list[dict]:  # fmt: skip
    """Absichten, die das Grundschema fuer die bestehenden Zonen vorgibt und die in Automato
    fehlen bzw. abweichen (`current`)."""
    if schema is None:
        schema, _profile = zone_schema.for_tenant(session, tenant_id)
    if schema is None:
        return []
    items = zones.load(session, tenant_id) if items is None else items
    names = {z.id: z.name for z in items}
    owner = {z.id: z.customer_id for z in items}
    by_schema: dict[str, list[int]] = defaultdict(list)
    for zid, sz in _mapped(schema, items, _customer_names(session, tenant_id)).items():
        by_schema[sz.key].append(zid)
    current = {
        (p.src_zone_id, p.dst_zone_id): p
        for p in session.scalars(select(ZonePolicy).where(ZonePolicy.tenant_id == tenant_id))
    }
    out = []
    for link in schema.links:
        for s in by_schema.get(key_of(link.src), []):
            for d in by_schema.get(key_of(link.dst), []):
                p = current.get((s, d))
                same = (
                    p is not None
                    and p.intent == link.intent
                    and (link.intent != "limited" or list(p.services or []) == link.services)
                )
                if s == d or same:
                    continue
                if owner[s] and owner[d] and owner[s] != owner[d]:
                    continue  # Vorlagen je Kunde: die Absicht gilt im selben Kunden (0.82.0)
                out.append({"src_zone_id": s, "src": names[s], "dst_zone_id": d, "dst": names[d],
                            "intent": link.intent, "current": p.intent if p else None,
                            "services": link.services,
                            "current_services": list(p.services or []) if p else [],
                            "schema_src": link.src, "schema_dst": link.dst,
                            "note": link.note})  # fmt: skip
    return out


def _schema_status(session: Session, tenant_id: int, schema: "zone_schema.Schema | None",
                   profile: NamingProfile | None, items: list[LogicalZone],
                   offered: set[str]) -> dict | None:  # fmt: skip
    """Grundschema des Mandanten gegen die Zonen: fehlende Schema-Zonen (ohne Zone und ohne
    Vorschlag), Zonen ausserhalb des Schemas, Absichten aus dem Schema."""
    if schema is None or profile is None:
        return None
    mapped = _mapped(schema, items, _customer_names(session, tenant_id))
    have = {sz.key for sz in mapped.values()}
    return {
        "profile_id": profile.id,
        "profile": profile.name if profile.customer == profile.name
        else f"{profile.customer} · {profile.name}",
        "zones": len(schema.zones),
        "links": len(schema.links),
        "matched": {str(zid): sz.name for zid, sz in mapped.items()},
        "missing": [sz.out() for sz in schema.zones if sz.key not in have | offered],
        "outside": [{"id": z.id, "name": z.name} for z in items if z.id not in mapped],
        "intents": schema_intents(session, tenant_id, schema, items),
    }  # fmt: skip


def dismiss(session: Session, tenant_id: int, keys, actor: str | None) -> list[str]:
    have = dismissed(session, tenant_id)
    new = sorted({k for k in keys if k} - have)
    session.add_all(ZoneSuggestionDismissal(tenant_id=tenant_id, key=k, created_by=actor)
                    for k in new)  # fmt: skip
    session.flush()
    return new


def undismiss(session: Session, tenant_id: int, keys) -> list[str]:
    rows = list(session.scalars(
        select(ZoneSuggestionDismissal).where(
            ZoneSuggestionDismissal.tenant_id == tenant_id,
            ZoneSuggestionDismissal.key.in_(list(keys)),
        )
    ))  # fmt: skip
    for r in rows:
        session.delete(r)
    session.flush()
    return sorted(r.key for r in rows)
