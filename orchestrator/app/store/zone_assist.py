"""Vorschlaege beim Erfassen einer Zone (0.66.0, ADR 0024 Nr. 15).

Wer eine Zone von Hand erfasst, muss die passenden Netze und Firewall-Interfaces zusammensuchen.
Der Zonen-Editor fragt mit Name, Kurzname und Art der Zone; Automato sucht im Gelesenen:

- **Firewall:** FortiGate-Zonen (Selektor Firewall-Zone je Firewall), Interfaces nach Name,
  Beschreibung bzw. Rolle (Selektor Interface) und VDOMs (Selektor VDOM).
- **IPAM:** Netze nach Name, Beschreibung bzw. VLAN-Name (Selektor Netz), Merkmale und Tags
  (Selektor IPAM-Merkmal), Tenant und VRF.
- **ACI:** Tenant, VRF, AP, BD und EPG nach Namen; **Anbindungen** nach Namen.

Ein Treffer ist der gleiche normalisierte Name bzw. Kurzname, alle Woerter des Namens im Text,
ein Erkennungsmuster der passenden Zone des Grundschemas bzw. der Vorlage oder die Rolle des
Interfaces passend zur Art (dmz, wan). Je Treffer die Netze wie in der Vorschau, Netze schon in
einer anderen Zone und ob die Selektoren der Zone sie schon erfassen. Nur lesend - uebernommen
wird im Editor.
"""

import fnmatch
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import InvInterface, ReferenceRecordRow, SourceSystem, TopoExit
from app.store import customers, zone_schema, zones
from app.store import epgs as epg_store
from app.store.customers import Selector
from app.store.zone_detect import _parts, key_of

MAX = 40
SAMPLES = 4
# Rolle des FortiGate-Interfaces -> Art der Zone, zu der sie passt
ROLE_KIND = {"dmz": "dmz", "wan": "external"}
SOURCE_LABEL = {  # (deutsch, englisch) - Anzeige in der Sprache der Anfrage (0.87.0)
    "fw_zone": ("Firewall-Zone", "Firewall zone"),
    "fw_interface": ("Firewall-Interface", "Firewall interface"),
    "fw_vdom": ("VDOM", "VDOM"),
    "ipam": ("IPAM", "IPAM"),
    "aci": ("ACI", "ACI"),
    "exit": ("Anbindung", "Exit"),
}
SOURCE_ORDER = tuple(SOURCE_LABEL)


@dataclass
class Match:
    """Wie gut ein Text zur Zone passt (`score`, hoeher = besser) und warum."""

    score: int
    reason: str


class Matcher:
    def __init__(self, name: str, code: str | None, kind: str | None,
                 schema_zone: "zone_schema.SchemaZone | None") -> None:  # fmt: skip
        self.keys = {k for n in (name, code) if n for k in (key_of(n), key_of(n, True)) if k}
        self.code = key_of(code) if code else None
        self.words = [w for w in dict.fromkeys(_parts(name)) if len(w) >= 2]
        self.kind = kind
        self.patterns = list(schema_zone.match) if schema_zone else []
        self.schema = schema_zone

    def test(self, *texts: str | None) -> Match | None:
        best: Match | None = None
        for t in texts:
            m = self._one(t)
            if m is not None and (best is None or m.score > best.score):
                best = m
        return best

    def _one(self, text: str | None) -> Match | None:
        t = (text or "").strip()
        if not t:
            return None
        if key_of(t) in self.keys or key_of(t, True) in self.keys:
            return Match(100, L(f"gleicher Name „{t}“", f"same name “{t}”"))
        parts = set(_parts(t)) | set(_parts(t, True))
        if self.code and self.code in parts:
            return Match(80, L(f"Kurzname in „{t}“", f"short name in “{t}”"))
        hits = [w for w in self.words if w in parts]
        if self.words and len(hits) == len(self.words) and max(map(len, hits)) >= 3:
            return Match(60 + len(hits), L(f"Name in „{t}“", f"name in “{t}”"))
        low = t.casefold()
        for p in self.patterns:
            if fnmatch.fnmatchcase(low, p) or any(fnmatch.fnmatchcase(x, p) for x in parts):
                zone = self.schema.name if self.schema else ""
                return Match(50, L(f"Muster „{p}“ ({zone})", f"pattern “{p}” ({zone})"))
        long = [w for w in hits if len(w) >= 3]
        if long and len(hits) * 2 >= len(self.words):
            words = ", ".join(long)
            return Match(20 + len(hits), L(f"„{words}“ in „{t}“", f"“{words}” in “{t}”"))
        return None

    def role(self, role: str | None) -> Match | None:
        r = (role or "").casefold()
        if r and self.kind and ROLE_KIND.get(r) == self.kind:
            return Match(40, L(f"Rolle {r}", f"role {r}"))
        return None


@dataclass
class Candidate:
    selector: Selector
    source: str
    label: str
    match: Match

    @property
    def key(self) -> tuple:
        s = self.selector
        return (s.kind, s.value.strip().casefold(), s.source_system_id, s.exclude)


def _sel_key(s: Selector) -> tuple:
    return (s.kind, s.value.strip().casefold(), s.source_system_id, s.exclude)


def _schema_zone(session: Session, tenant_id: int, name: str, code: str | None):
    schema, _profile = zone_schema.for_tenant(session, tenant_id)
    return (schema or zone_schema.default_schema()).match(name, code)


def _firewall(session: Session, tenant_id: int, m: Matcher, put) -> None:
    names = dict(session.execute(
        select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    ).all())  # fmt: skip
    rows = session.execute(
        select(InvInterface.source_system_id, InvInterface.name, InvInterface.vdom,
               InvInterface.zone, InvInterface.description, InvInterface.role, InvInterface.ips)
        .where(InvInterface.tenant_id == tenant_id, InvInterface.vdom.is_not(None))
    )  # fmt: skip
    vdoms: set[tuple[int, str]] = set()
    for sid, name, vdom, zone, desc, role, ips in rows:
        fw = names.get(sid, f"#{sid}")
        where = fw if (vdom or "root") == "root" else f"{fw} · VDOM {vdom}"
        vdoms.add((sid, vdom or "root"))
        if zone and (hit := m.test(zone)) is not None:
            put(Selector("fw_zone", zone, sid), "fw_zone", f"Zone {zone} · {where}", hit)
            continue  # die Zone erfasst das Interface schon
        if not ips:
            continue
        hit = m.test(name, desc)
        role_hit = m.role(role)
        if role_hit is not None and (hit is None or role_hit.score > hit.score):
            hit = role_hit
        if hit is not None:
            text = f"Interface {name}" + (f" ({desc})" if desc and desc != name else "")
            put(Selector("fw_interface", name, sid), "fw_interface", f"{text} · {where}", hit)
    for sid, vdom in sorted(vdoms):
        if vdom != "root" and (hit := m.test(vdom)) is not None:
            put(Selector("fw_vdom", vdom, sid), "fw_vdom",
                f"VDOM {vdom} · {names.get(sid, f'#{sid}')}", hit)  # fmt: skip


def _ipam(session: Session, tenant_id: int, m: Matcher, put) -> None:
    rows = session.execute(
        select(ReferenceRecordRow.kind, ReferenceRecordRow.value, ReferenceRecordRow.name,
               ReferenceRecordRow.description, ReferenceRecordRow.vlan_name,
               ReferenceRecordRow.vrf, ReferenceRecordRow.tenant_name, ReferenceRecordRow.tags,
               ReferenceRecordRow.attrs)
        .join(SourceSystem, SourceSystem.id == ReferenceRecordRow.source_system_id)
        .where(ReferenceRecordRow.tenant_id == tenant_id,
               ReferenceRecordRow.collector_run_id == SourceSystem.reference_run_id,
               ReferenceRecordRow.kind == "prefix")
    )  # fmt: skip
    for _kind, value, name, desc, vlan, vrf, tenant, tags, attrs in rows:
        if (hit := m.test(name, desc, vlan)) is not None:
            text = " · ".join(x for x in (name, desc, f"VLAN {vlan}" if vlan else None) if x)
            put(Selector("prefix", value), "ipam",
                L(f"Netz {value} – {text}", f"Network {value} – {text}"), hit)  # fmt: skip
        for k, v in (attrs or {}).items():
            if isinstance(v, str | int) and (hit := m.test(str(v))) is not None:
                put(Selector("ipam_tag", f"{k}={v}"), "ipam",
                    L(f"IPAM-Merkmal {k}={v}", f"IPAM attribute {k}={v}"), hit)  # fmt: skip
        for tag in tags or []:
            if (hit := m.test(str(tag))) is not None:
                put(Selector("ipam_tag", str(tag)), "ipam", L(f"IPAM-Tag {tag}", f"IPAM tag {tag}"),
                    hit)  # fmt: skip
        if tenant and (hit := m.test(tenant)) is not None:
            put(Selector("ipam_tenant", tenant), "ipam",
                L(f"IPAM-Tenant {tenant}", f"IPAM tenant {tenant}"), hit)  # fmt: skip
        if vrf and (hit := m.test(vrf)) is not None:
            put(Selector("ipam_vrf", vrf), "ipam", L(f"IPAM-VRF {vrf}", f"IPAM VRF {vrf}"), hit)


def _aci(data: customers._Data, m: Matcher, put) -> None:
    for e in data.epgs:
        t = e.aci_tenant or ""
        if t and (hit := m.test(t)) is not None:
            put(Selector("aci_tenant", t), "aci", L(f"ACI-Tenant {t}", f"ACI tenant {t}"), hit)
        vrf = data.vrf.get(e.id)
        if vrf and (hit := m.test(vrf.split(":")[-1])) is not None:
            put(Selector("aci_vrf", vrf), "aci", f"ACI-VRF {vrf}", hit)
        if e.ap and (hit := m.test(e.ap)) is not None:
            put(Selector("aci_ap", f"{t}/{e.ap}"), "aci", f"ACI-AP {t}/{e.ap}", hit)
        if e.bd and (hit := m.test(e.bd)) is not None:
            put(Selector("aci_bd", f"{t}/{e.bd}"), "aci", f"ACI-BD {t}/{e.bd}", hit)
        if e.kind == "epg" and (hit := m.test(e.name)) is not None:
            label = epg_store.inventory_label(e)
            put(Selector("aci_epg", label), "aci", f"ACI-EPG {label}", hit)


def _exits(session: Session, tenant_id: int, m: Matcher, put) -> None:
    for e in session.scalars(select(TopoExit).where(TopoExit.tenant_id == tenant_id)):
        if (hit := m.test(e.name)) is not None:
            put(Selector("exit", e.name), "exit",
                L(f"Anbindung {e.name} ({e.kind})", f"Exit {e.name} ({e.kind})"), hit)  # fmt: skip


def _net_key(n) -> tuple:
    return (n.version, int(n.network_address), n.prefixlen)


def suggest(session: Session, tenant_id: int, name: str, code: str | None = None,
            kind: str | None = None, selectors: list[Selector] | None = None,
            zone_id: int | None = None) -> dict:  # fmt: skip
    """Selektoren, die zur Zone passen koennten - mit Grund, Netzen und Ueberschneidungen."""
    name = (name or "").strip()
    selectors = selectors or []
    sz = _schema_zone(session, tenant_id, name, code) if name else None
    m = Matcher(name, code, kind, sz)
    out = {"name": name, "schema": sz.name if sz else None, "items": [], "total": 0}
    if len(name) < 2 and not code:
        return out
    found: dict[tuple, Candidate] = {}

    def put(sel: Selector, source: str, label: str, hit: Match) -> None:
        c = Candidate(sel, source, label, hit)
        old = found.get(c.key)
        if old is None or hit.score > old.match.score:
            found[c.key] = c

    data = customers._Data(session, tenant_id)
    _firewall(session, tenant_id, m, put)
    _ipam(session, tenant_id, m, put)
    _aci(data, m, put)
    _exits(session, tenant_id, m, put)

    have = {_sel_key(s) for s in selectors}
    cands = [c for k, c in found.items() if k not in have]
    cands.sort(key=lambda c: (-c.match.score, SOURCE_ORDER.index(c.source), c.label.casefold()))
    out["total"] = len(cands)
    _epgs, mine = customers.nets_of(data, [s for s in selectors if s.value.strip()])
    others = zones.derive(
        session, tenant_id, [z for z in zones.load(session, tenant_id) if z.id != zone_id], data
    )
    owner: dict = {}
    for zn in others:
        for net in zn.nets:
            owner.setdefault(net, zn.name)
    for c in cands[:MAX]:
        epgs, nets = customers.nets_of(data, [c.selector])
        if not nets and not epgs:
            continue
        ordered = sorted(nets, key=_net_key)
        clash = [{"network": str(n), "zone": owner[n]} for n in ordered if n in owner]
        new = [n for n in ordered if n not in mine]
        out["items"].append({
            "selector": {"kind": c.selector.kind, "value": c.selector.value,
                         "source_system_id": c.selector.source_system_id, "exclude": False},
            "source": c.source,
            "source_label": L(*SOURCE_LABEL[c.source]),
            "label": c.label,
            "reason": c.match.reason,
            "score": c.match.score,
            "nets": len(nets),
            "epgs": len(epgs),
            "samples": [str(n) for n in ordered[:SAMPLES]],
            "new": len(new),
            "covered": bool(nets) and not new,
            "conflicts": clash[:SAMPLES],
            "conflicts_total": len(clash),
        })  # fmt: skip
    return out
