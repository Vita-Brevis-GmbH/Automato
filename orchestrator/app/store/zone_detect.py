"""Logische Zonen erkennen und vorschlagen (0.54.0, ADR 0024 Nr. 10).

Automato schlaegt Zonen aus dem Gelesenen vor; gespeichert wird erst, wenn der Operator einen
Vorschlag uebernimmt - dann als normale Zone mit Selektoren, also weiter abgeleitet: neue
Interfaces bzw. EPGs gehoeren ohne Pflege dazu. Quellen in dieser Reihenfolge:

1. **Transfernetze** zwischen gelesenen Knoten (Interface-Netze bzw. L3Out-SVIs zweier
   Firewalls/VDOMs/VRFs, `topo_model`) -> eine Zone „Transfer“ (Netze von Hand).
2. **FortiGate-Zonen**: gleicher Name auf mehreren Firewalls bzw. VDOMs = ein Vorschlag mit dem
   Selektor Zone fuer alle Firewalls.
3. **Interfaces ohne Firewall-Zone** mit sprechendem Namen (`dmz-web`, `wan1`), der FortiOS-Rolle
   dmz/wan bzw. Management-Interfaces (eigene Tabelle) - Selektor Interface je Firewall.
4. **IPAM-Merkmal** Zone bzw. Rolle (NetBox-Rolle des Prefixes, Custom Field bzw. DDI-Attribut
   `zone`, `role`, ...).
5. **ACI-VRFs** (`Tenant:VRF` mit EPG-/BD-Subnetzen): `common` geteilt, `mgmt` Management.
6. **Anbindungen** (von Hand): alle Internet-Anbindungen als eine Zone ohne die privaten Netze,
   sonst je Anbindung.

Gleicher normalisierter Name (`key_of`) = ein Vorschlag mit allen Selektoren; Management und
Transfer je eine Zone. Die **Art** folgt aus dem Namen (dmz; internet/wan/outside; mgmt/oob;
transfer/transit; shared/common), der FortiOS-Rolle bzw. der Anbindung, sonst intern. Ein
**Kunde**, wenn alle Netze und EPGs in genau einem Kunden liegen (ADR 0018). Gibt es schon eine
Zone mit dem Namen (bzw. die Transfer- oder Management-Zone), ergaenzt der Vorschlag ihre
Selektoren. Ein Netz, das genau so schon in einer Zone bzw. einem frueheren Vorschlag liegt,
schliesst der Vorschlag aus - kein `zone_overlap` nach dem Uebernehmen. Ausgeblendete Vorschlaege
(`zone_suggestion_dismissal`) erscheinen nur auf Wunsch. Dazu die gelesenen Netze, fuer die es
weder Zone noch Vorschlag gibt („nicht erkannt“).

Nur lesend; die Erkennung schreibt nichts.
"""

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    InvInterface,
    InvSubnet,
    LogicalZone,
    SourceSystem,
    TopoExit,
    ZoneSuggestionDismissal,
)
from app.store import customers, topo_model, zones
from app.store.customers import Net, Selector
from app.store.paths import Graph, Key
from app.store.topology import MGMT

SOURCES = ("transfer", "fw_zone", "interface", "ipam", "aci_vrf", "exit")
SOURCE_LABEL = {
    "transfer": "Transfernetz",
    "fw_zone": "FortiGate-Zone",
    "interface": "Interface",
    "ipam": "IPAM",
    "aci_vrf": "ACI-VRF",
    "exit": "Anbindung",
}
# Woerter im Namen -> Art der Zone (Teile des Namens, Ziffern am Ende ohne Bedeutung)
KIND_WORDS = {
    "management": ("mgmt", "mgt", "management", "oob", "inb", "ilo", "idrac", "ipmi"),
    "dmz": ("dmz", "perimeter"),
    "transfer": ("transfer", "transit", "p2p", "ptp", "interco", "interconnect"),
    "external": (
        "internet",
        "inet",
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
ROLE_KIND = {"dmz": "dmz", "wan": "external", "management": "management"}
# Arten mit genau einer Zone (alle Transfernetze, alles Management)
SINGLE = {"transfer": "Transfer", "management": "Management"}
KIND_COLOR = {
    "internal": "#3b82c4",
    "dmz": "#d9822b",
    "external": "#cc3333",
    "management": "#7a5cc4",
    "shared": "#2f9e8f",
    "transfer": "#8a93a3",
}
KIND_ORDER = ("external", "dmz", "internal", "shared", "management", "transfer")
# IPAM-Merkmale, die eine Zone nennen (klein geschrieben)
ZONE_ATTRS = ("zone", "zone_name", "zonename", "security_zone", "securityzone", "sicherheitszone",
              "netzzone", "network_zone", "role", "netzrolle")  # fmt: skip
PRIVATE = {4: ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"),
           6: ("fc00::/7",)}  # fmt: skip
_NOISE = {"zone", "zn", "z", "vlan", "vl", "if", "intf", "iface"}
_SPLIT = re.compile(r"[_\-|/.:\s]+")
SAMPLES = 10
REST = 200
_EXIT = "Anbindung "  # Grund der Netze hinter einer Anbindung (`customers._label`)


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


def _nice(name: str) -> str:
    """`dmz` -> `DMZ`, `server` -> `Server`; gemischte Schreibweise bleibt."""
    if name != name.casefold():
        return name
    return name.upper() if len(name) <= 3 else name[:1].upper() + name[1:]


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
    selectors: list[Selector] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    nets: dict[Net, str] = field(default_factory=dict)
    epgs: dict[int, str] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    target: LogicalZone | None = None

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
    """Gesammelte Vorschlaege je normalisiertem Namen."""

    # Name aus dieser Quelle zuerst (sprechender als Interface- bzw. VRF-Namen)
    NAME_ORDER = ("exit", "ipam", "fw_zone", "interface", "aci_vrf", "transfer")

    def __init__(self) -> None:
        self.by_key: dict[str, Candidate] = {}
        self._named: dict[str, int] = {}

    def put(self, key: str, name: str, source: str, kind: str | None, reason: str | None,
            selectors: list[Selector], evidence: str) -> Candidate:  # fmt: skip
        if kind in SINGLE:
            key, name = kind, SINGLE[kind]
        c = self.by_key.get(key)
        if c is None:
            c = self.by_key[key] = Candidate(key, _nice(name), source)
            self._named[key] = self.NAME_ORDER.index(source)
        elif self.NAME_ORDER.index(source) < self._named[key]:
            c.name, self._named[key] = _nice(name), self.NAME_ORDER.index(source)
        if source not in c.sources:
            c.sources.append(source)
        if kind and kind != "internal" and c.kind == "internal":
            c.kind, c.reason = kind, reason
        for s in selectors:
            c.add(s)
        c.note(evidence)
        return c


# -- Quellen ------------------------------------------------------------------------------


def _nets_of_ips(ips) -> list[Net]:
    out = []
    for text in ips or []:
        try:
            iface = ipaddress.ip_interface(text)
        except ValueError:
            continue
        net = iface.network
        if net.prefixlen < net.max_prefixlen and not iface.ip.is_link_local:
            out.append(net)
    return out


@dataclass
class _Iface:
    sid: int
    key: Key | None
    label: str
    name: str
    zone: str | None
    role: str | None
    description: str | None
    nets: list[Net]
    mgmt: bool


def _interfaces(session: Session, tenant_id: int, g: Graph) -> list[_Iface]:
    """FortiGate-Interfaces mit Netz, je mit Knoten (Firewall bzw. VDOM) und Management."""
    names = dict(session.execute(
        select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    ).all())  # fmt: skip
    ctxs: dict[int, list[str]] = defaultdict(list)
    for key in g.nodes:
        ctxs[key[0]].append(key[1])
    out = []
    for i in session.scalars(
        select(InvInterface).where(
            InvInterface.tenant_id == tenant_id, InvInterface.vdom.is_not(None)
        )
    ):
        nets = _nets_of_ips(i.ips)
        if not nets:
            continue
        own = ctxs.get(i.source_system_id, [])
        key = (i.source_system_id, i.vdom or (own[0] if len(own) == 1 else ""))
        known = key in g.nodes
        label = g.nodes[key].label if known else names.get(i.source_system_id, "?")
        mgmt = i.role == "management" or (known and g.table(key, i.name) == MGMT)
        out.append(_Iface(i.source_system_id, key if known else None, label, i.name, i.zone,
                          i.role, i.description, nets, mgmt))  # fmt: skip
    return out


def _from_transfer(found: _Found, nets: dict[str, list[str]]) -> None:
    for net in sorted(nets, key=lambda n: zones._net_key(ipaddress.ip_network(n))):
        found.put("transfer", "Transfer", "transfer", "transfer", "Netz zwischen gelesenen Knoten",
                  [Selector("prefix", net)], f"{net}: {' ↔ '.join(nets[net])}")  # fmt: skip


def _from_fw_zones(found: _Found, ifaces: list[_Iface]) -> None:
    groups: dict[str, list[_Iface]] = defaultdict(list)
    for i in ifaces:
        if i.zone and not i.mgmt:
            groups[key_of(i.zone)].append(i)
    for key, members in groups.items():
        zone_names = list(dict.fromkeys(i.zone for i in members if i.zone))
        hit = classify(*zone_names)
        kind, reason = (hit[0], f"Name „{hit[1]}“") if hit else (None, None)
        roles = {i.role for i in members}
        if hit is None and len(roles) == 1 and (role := roles.pop()) in ROLE_KIND:
            kind, reason = ROLE_KIND[role], f"FortiOS-Rolle {role}"
        sel = [Selector("fw_zone", n) for n in zone_names]
        for i in members:
            found.put(key, zone_names[0], "fw_zone", kind, reason, sel,
                      f"Zone {i.zone} · {i.label}")  # fmt: skip


def _from_interfaces(found: _Found, ifaces: list[_Iface]) -> None:
    """Interfaces ohne Firewall-Zone mit sprechendem Namen bzw. Rolle; der Rest bleibt „nicht
    erkannt“."""
    for i in ifaces:
        if i.zone:
            continue
        sel = [Selector("fw_interface", i.name, i.sid)]
        where = f"Interface {i.name} · {i.label}" + (f" ({i.description})" if i.description else "")
        if i.mgmt:
            found.put("management", "Management", "interface", "management",
                      "Management-Interface", sel, where)  # fmt: skip
            continue
        hit = classify(i.name, i.description)
        if hit is not None:
            label = i.description if classify(i.description) else i.name
            found.put(key_of(label, strip_digits=True), label, "interface", hit[0],
                      f"Name „{hit[1]}“", sel, where)  # fmt: skip
        elif i.role in ("dmz", "wan"):
            name = "DMZ" if i.role == "dmz" else "WAN"
            found.put(i.role, name, "interface", ROLE_KIND[i.role], f"FortiOS-Rolle {i.role}",
                      sel, where)  # fmt: skip


def _from_ipam(found: _Found, data: customers._Data) -> None:
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for _kind, _value, _vrf, _tenant, _tags, attrs in data.references:
        for k, v in (attrs or {}).items():
            if str(k).casefold() in ZONE_ATTRS and str(v).strip():
                seen[(str(k), str(v).strip())] += 1
    for (k, v), n in sorted(seen.items()):
        hit = classify(v)
        kind, reason = (hit[0], f"Name „{hit[1]}“") if hit else (None, None)
        found.put(key_of(v), v, "ipam", kind, reason, [Selector("ipam_tag", f"{k}={v}")],
                  f"IPAM {k}={v} ({n} Einträge)")  # fmt: skip


def _from_aci(found: _Found, data: customers._Data) -> None:
    by_vrf: dict[str, list[int]] = defaultdict(list)
    for e in data.epgs:
        vrf = data.vrf.get(e.id)
        if e.kind == "epg" and vrf and data.epg_nets.get(e.id):
            by_vrf[vrf].append(e.id)
    for vrf, ids in sorted(by_vrf.items()):
        tenant, _, name = vrf.partition(":")
        t = tenant.casefold()
        if t == "infra":
            continue
        if t == "common":
            kind, reason = "shared", "ACI-Tenant common"
        elif t == "mgmt":
            kind, reason = "management", "ACI-Tenant mgmt"
        elif hit := classify(name):
            kind, reason = hit[0], f"Name „{hit[1]}“"
        else:
            kind, reason = None, None
        found.put(f"aci:{vrf.casefold()}", vrf, "aci_vrf", kind, reason,
                  [Selector("aci_vrf", vrf)], f"VRF {vrf}: {len(ids)} EPGs")  # fmt: skip


def _from_exits(found: _Found, exits: list[TopoExit], used: set[str]) -> None:
    for e in exits:
        if (e.name or "").casefold() in used or f"#{e.id}" in used:
            continue
        sel = [Selector("exit", e.name)]
        nets = [n for p in e.prefixes or [] if (n := customers._net(str(p))) is not None]
        if e.kind == "internet" or any(n.prefixlen == 0 for n in nets):
            fams = {n.version for n in nets if n.prefixlen == 0}
            sel += [Selector("prefix", p, None, True) for f in sorted(fams) for p in PRIVATE[f]]
        key, name = ("internet", "Internet") if e.kind == "internet" else (key_of(e.name), e.name)
        found.put(
            key,
            name,
            "exit",
            "external",
            f"Anbindung ({e.kind})",
            sel,
            f"Anbindung {e.name}" + (f": {', '.join(map(str, nets[:3]))}" if nets else ""),
        )


# -- Auswertung ---------------------------------------------------------------------------


def dismissed(session: Session, tenant_id: int) -> set[str]:
    return set(session.scalars(
        select(ZoneSuggestionDismissal.key).where(ZoneSuggestionDismissal.tenant_id == tenant_id)
    ))  # fmt: skip


def _target(c: Candidate, items: list[LogicalZone]) -> LogicalZone | None:
    """Bestehende Zone, die der Vorschlag ergaenzt: gleicher Name bzw. Kurzname, bei Transfer
    und Management die erste Zone dieser Art."""
    for z in items:
        if c.key == key_of(z.name) or (z.code and c.key == key_of(z.code)):
            return z
    if c.key in SINGLE:
        return next((z for z in items if z.kind == c.key), None)
    return None


def _owner(c: Candidate, owners: list[tuple[Customer, set[int], customers._Ranges]]
           ) -> Customer | None:  # fmt: skip
    if c.key in SINGLE or "exit" in c.sources or not (c.nets or c.epgs):
        return None
    hits = [
        cust for cust, epgs, ranges in owners
        if set(c.epgs) <= epgs and all(
            ranges.covers(n.version, int(n.network_address), int(n.broadcast_address))
            for n in c.nets
        )
    ]  # fmt: skip
    return hits[0] if len(hits) == 1 else None


def suggest(session: Session, tenant_id: int, include_dismissed: bool = False) -> dict:
    """Vorschlaege fuer logische Zonen aus dem Gelesenen (siehe Modul-Doku)."""
    data = customers._Data(session, tenant_id)
    items = zones.load(session, tenant_id)
    existing = zones.derive(session, tenant_id, items, data)
    idx = zones.ZoneIndex(existing)
    names = {z.id: z.name for z in items}
    owned: dict[Net, int] = {}
    for zn in existing:
        for net in zn.nets:
            owned.setdefault(net, zn.id)
    g = Graph(session, tenant_id)
    ifaces = _interfaces(session, tenant_id, g)
    exits = list(session.scalars(select(TopoExit).where(TopoExit.tenant_id == tenant_id)))
    used = {s.value.strip().casefold() for z in items for s in z.selectors
            if s.kind == "exit" and not s.exclude}  # fmt: skip

    found = _Found()
    _from_transfer(found, topo_model.transfer_nets(session, tenant_id, g))
    _from_fw_zones(found, ifaces)
    _from_interfaces(found, ifaces)
    _from_ipam(found, data)
    _from_aci(found, data)
    _from_exits(found, exits, used)

    # Netze je Vorschlag; ein Netz, das genau so schon in einer Zone bzw. einem frueheren
    # Vorschlag liegt, schliesst der Vorschlag aus
    claimed: dict[Net, str] = {}
    cands = sorted(found.by_key.values(), key=lambda c: (c.rank, c.key))
    for c in cands:
        c.target = _target(c, items)
        c.epgs, c.nets = customers.nets_of(data, c.selectors)
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
            c.epgs, c.nets = customers.nets_of(data, c.selectors)
        for net in c.nets:
            claimed.setdefault(net, c.name)

    owners = []
    for cust in session.scalars(select(Customer).where(Customer.tenant_id == tenant_id)):
        if cust.shared:
            continue
        epgs, nets = customers.nets_of(data, customers.selectors_of(cust))
        owners.append((cust, set(epgs), customers._Ranges(nets)))
    hidden = dismissed(session, tenant_id)
    cust_names = dict(session.execute(
        select(Customer.id, Customer.name).where(Customer.tenant_id == tenant_id)
    ).all())  # fmt: skip

    out, covered = [], []
    for c in cands:
        new_nets, new_sel = c.nets, c.selectors
        target = c.target
        if target is not None:
            have = {_sel_key(s) for s in zones.selectors_of(target)}
            new_sel = [s for s in c.selectors if _sel_key(s) not in have]
            own = next((zn.nets for zn in existing if zn.id == target.id), {})
            new_nets = {n: w for n, w in c.nets.items() if n not in own}
            if not new_sel or not (new_nets or any(s.kind == "exit" for s in new_sel)):
                continue
        elif not (c.nets or "exit" in c.sources):
            continue  # ohne eigenes Netz keine Zone (EPGs allein zaehlen in Matrix und Plan nicht)
        covered += list(c.nets)
        is_hidden = c.key in hidden
        if is_hidden and not include_dismissed:
            continue
        # Netze hinter einer Anbindung (0.0.0.0/0 ohne die privaten = viele Stuecke) als ein
        # Eintrag je Anbindung
        groups: dict[str, list[Net]] = defaultdict(list)
        for net, why in new_nets.items():
            groups[why if why.startswith(_EXIT) else str(net)].append(net)
        open_, refines = 0, set()
        for label, members in groups.items():
            zs = set().union(*(idx.of_net(n) for n in members))
            open_ += zones.NONE in zs if label.startswith(_EXIT) else zs == {zones.NONE}
            refines |= {
                names[z]
                for z in zs
                if isinstance(z, int) and z in names and (target is None or z != target.id)
            }
        plain = sorted((n for n, why in new_nets.items() if not why.startswith(_EXIT)),
                       key=zones._net_key)  # fmt: skip
        samples = [str(n) for n in plain] + sorted(g for g in groups if g.startswith(_EXIT))
        cust_id = target.customer_id if target else getattr(_owner(c, owners), "id", None)
        out.append({
            "key": c.key,
            "action": "extend" if target else "create",
            "zone_id": target.id if target else None,
            "zone": target.name if target else None,
            "name": target.name if target else c.name,
            "kind": target.kind if target else c.kind,
            "kind_reason": None if target else c.reason,
            "color": target.color if target else KIND_COLOR[c.kind],
            "customer_id": cust_id,
            "customer": cust_names.get(cust_id) if cust_id else None,
            "sources": c.sources,
            "evidence": c.evidence[:SAMPLES],
            "evidence_total": len(c.evidence),
            "selectors": [_sel_out(s) for s in new_sel],
            "counts": {"nets": len(groups), "open": open_, "epgs": len(c.epgs)},
            "samples": samples[:SAMPLES],
            "refines": sorted(refines),
            "conflicts": c.conflicts[:SAMPLES],
            "dismissed": is_hidden,
        })  # fmt: skip
    out.sort(key=lambda s: (s["action"] != "extend", KIND_ORDER.index(s["kind"]),
                            s["name"].casefold()))  # fmt: skip
    rest = _rest(session, tenant_id, ifaces, idx, customers._Ranges(covered))
    return {
        "items": out,
        "rest": {"total": len(rest), "items": rest[:REST]},
        "summary": {
            "suggestions": sum(1 for s in out if not s["dismissed"]),
            "create": sum(1 for s in out if s["action"] == "create" and not s["dismissed"]),
            "extend": sum(1 for s in out if s["action"] == "extend" and not s["dismissed"]),
            "dismissed": len(hidden),
            "open": sum(s["counts"]["open"] for s in out if not s["dismissed"]),
            "rest": len(rest),
        },
    }


def _rest(session: Session, tenant_id: int, ifaces: list[_Iface], idx: zones.ZoneIndex,
          covered: customers._Ranges) -> list[dict]:  # fmt: skip
    """Gelesene Netze (Interfaces ohne Management, EPG-/BD-Subnetze) ohne Zone und ohne
    Vorschlag."""
    found: dict[Net, dict] = {}

    def check(net: Net, where: str, node: str) -> None:
        if net in found or zones.NONE not in idx.of_net(net):
            return
        if covered.covers(net.version, int(net.network_address), int(net.broadcast_address)):
            return
        found[net] = {"network": str(net), "node": node, "where": where}

    for i in ifaces:
        if not i.mgmt:
            for net in i.nets:
                check(net, f"Interface {i.name}" + (f" ({i.description})" if i.description
                                                    else ""), i.label)  # fmt: skip
    for s in session.scalars(
        select(InvSubnet).where(
            InvSubnet.tenant_id == tenant_id, InvSubnet.context.in_(("bd", "epg"))
        )
    ):
        net = customers._net(s.prefix)
        if net is not None:
            if s.epg and s.ap:
                where = f"EPG {s.aci_tenant}|{s.ap}|{s.epg}"
            elif s.bd:
                where = f"BD {s.aci_tenant}|{s.bd}"
            else:
                where = s.context_name or "Fabric"
            check(net, where, f"VRF {s.vrf}" if s.vrf else "ACI")
    return [found[n] for n in sorted(found, key=zones._net_key)]


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
