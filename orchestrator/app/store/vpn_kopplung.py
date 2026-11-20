"""Kopplung der IPsec-Tunnel zweier gelesener FortiGates (1.23.0, ADR 0063 Nachtrag 1.23.0).

Anwender 2026-11-20: „Die VPN Verbindungen werden herausgelesen aber nicht verknüpft, also bleiben
die verbundenen Standorte weiterhin getrennt“ – „Beide eingelesen. Einseitig ist es dynamisch, kann
man dies nicht koppeln? Manuell, wenn es dynamisch nicht geht?“

Ein Tunnel, dessen beide Enden gelesen sind, ist **ein** Uebergang zwischen den beiden Firewalls:
die Weg-Suche geht durch ihn auf der anderen FortiGate weiter (Eingang = ihr Tunnel), Verbindungen,
Doku, Topologie und die Ansicht „VPN-Tunnel“ nennen die andere Firewall als Gegenstelle.

**Automatisch** (`auto`), nur wo belegt und eindeutig:

- `gw`: die statische Seite zeigt mit `remote-gw` auf eine Adresse der anderen FortiGate
  (Interface) bzw. `vip`: auf eine bekannte oeffentliche Adresse davor (VIP einer gelesenen
  FortiGate auf ihr Interface). Dort gilt der Tunnel, der zurueck auf eine eigene Adresse zeigt,
  bzw. ein Dial-up-Tunnel auf diesem Interface, dessen Phase-2-Selektoren gespiegelt sind
  (lokal ↔ Gegenstelle, gleich bzw. umfassend);
- `reverse`: die Adresse der Gegenstelle gehoert keinem gelesenen Geraet (NAT davor), aber genau
  ein Tunnel einer gelesenen FortiGate zeigt auf eine eigene Adresse (wie seit 1.19.0);
- `dialup`: eine verbundene Gegenstelle eines Dial-up-Tunnels kommt von einer Adresse einer
  gelesenen FortiGate, deren statischer Tunnel dort eindeutig ist und gespiegelte Selektoren hat.

Mehrdeutig (mehrere passende Tunnel, Selektoren nicht gespiegelt) wird **nicht** gekoppelt, sondern
als Vorschlag gezeigt. Ein statischer Tunnel hat hoechstens eine Gegenstelle; ein Dial-up-Tunnel
viele.

**Von Hand** (`manual`, `vpn_pair`): koppeln bzw. eine automatische Kopplung loesen
(`separated`). Von Hand geht vor: ein statischer Tunnel mit einer Kopplung von Hand bekommt keine
automatische. Nur Laufzeitdaten in Automato, nichts geht an ein Geraet; jede Aenderung steht im
Audit und aendert damit Fingerabdruck und Wege-Speicher (ADR 0037, 0048).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import NatRule, VpnPair
from app.models.vpn_pair import COUPLED, SEPARATED
from app.store import vpn as vpn_store
from app.store.vpn import Tunnel, coverage, span

AUTO, MANUAL = "auto", "manual"
End = tuple[int, str, str]  # Quellsystem, VDOM, Tunnel
_IKE_PORTS = (500, 4500)
SUGGESTIONS = 10  # Vorschlaege je Tunnel


def end_of(t: Tunnel) -> End:
    return (t.source_system_id, t.context, t.name)


def ordered(a: End, b: End) -> tuple[End, End]:
    return (a, b) if a <= b else (b, a)


# -- Selektoren -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Sel:
    """Selektoren eines Tunnels als Bereiche: je Seite die Bereiche (None = nicht alle
    aufloesbar) und die zusammengefassten Bereiche zum Vergleich."""

    src: frozenset | None
    dst: frozenset | None
    msrc: tuple
    mdst: tuple


def _merge(spans) -> tuple:
    out: list[list[int]] = []
    for fam, lo, hi in sorted(spans):
        if out and out[-1][0] == fam and lo <= out[-1][2] + 1:
            out[-1][2] = max(out[-1][2], hi)
        else:
            out.append([fam, lo, hi])
    return tuple(tuple(x) for x in out)


def _sel(t: Tunnel) -> _Sel:
    sides = {}
    for side in ("src", "dst"):
        parsed = [span(v) for v in t.selectors(side)]
        good = [x for x in parsed if x is not None]
        sides[side] = (frozenset(good) if len(good) == len(parsed) else None, _merge(good))
    return _Sel(sides["src"][0], sides["dst"][0], sides["src"][1], sides["dst"][1])


def _inside(values: frozenset | None, cover: tuple) -> bool:
    """Liegen alle Bereiche in den zusammengefassten Bereichen `cover`?"""
    if not values:
        return False
    return all(any(f == fam and a <= lo and hi <= b for f, a, b in cover)
               for fam, lo, hi in values)  # fmt: skip


def mirrored(a: Tunnel, b: Tunnel, cache: dict | None = None) -> str | None:
    """Sind die Phase-2-Selektoren von `a` und `b` gespiegelt (lokal von `a` = Gegenstelle von
    `b` und umgekehrt)? `exact` (gleich), `covers` (die eine Seite umfasst die andere, z. B. ein
    Dial-up mit 0.0.0.0/0) bzw. None (nicht gespiegelt bzw. nicht bekannt). `cache`: Selektoren
    je Tunnel (Ende)."""
    if not a.phase2 or not b.phase2:
        return None
    if cache is None:
        x, y = _sel(a), _sel(b)
    else:
        x = cache.get(end_of(a)) or cache.setdefault(end_of(a), _sel(a))
        y = cache.get(end_of(b)) or cache.setdefault(end_of(b), _sel(b))
    if not (x.msrc and x.mdst and y.msrc and y.mdst):
        return None
    if x.src is not None and x.dst is not None and x.src == y.dst and x.dst == y.src:
        return "exact"
    one = _inside(x.src, y.mdst) and _inside(x.dst, y.msrc)
    if one or (_inside(y.src, x.mdst) and _inside(y.dst, x.msrc)):
        return "covers"
    return None


def _specific(t: Tunnel) -> bool:
    """Nennt der Tunnel ein Netz, das nicht 0.0.0.0/0 ist? (Vorschlaege nur dann)"""
    return any(not v.endswith("/0") for side in ("src", "dst") for v in t.selectors(side))


def _overlaps(nets: list[str], selectors: list[str]) -> bool:
    return any(coverage(selectors, n) != "none" for n in nets if not str(n).endswith("/0"))


# -- Kopplung -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    """Zwei gekoppelte Tunnel: Herkunft (`auto` | `manual`), wie erkannt (`gw`, `vip`,
    `reverse`, `dialup`, `manual`), bei einer Kopplung von Hand ihre Zeile."""

    a: Tunnel
    b: Tunnel
    origin: str
    how: str  # aus Sicht von `a`
    pair_id: int | None = None
    back: str | None = None  # aus Sicht von `b` (None = wie `how`)

    def other(self, t: Tunnel) -> Tunnel:
        return self.b if end_of(t) == end_of(self.a) else self.a

    def how_for(self, t: Tunnel) -> str:
        """Wie die Kopplung aus Sicht des Tunnels `t` erkannt wurde."""
        return self.how if end_of(t) == end_of(self.a) else (self.back or self.how)


@dataclass(frozen=True)
class Hint:
    """Vorschlag fuer eine Kopplung von Hand: der andere Tunnel und warum."""

    tunnel: Tunnel
    why: str  # gw | vip | dialup | mirror
    exact: bool = False


def why_text(why: str, exact: bool = False) -> str:
    return {
        "gw": L("Gegenstelle ist eine Adresse dieser Firewall, Tunnel dort nicht eindeutig",
                "peer is an address of this firewall, tunnel there not unique"),
        "vip": L("Gegenstelle ist eine VIP vor dieser Firewall, Tunnel dort nicht eindeutig",
                 "peer is a VIP in front of this firewall, tunnel there not unique"),
        "dialup": L("verbundene Dial-up-Gegenstelle kommt von dieser Firewall",
                    "connected dial-up peer comes from this firewall"),
        "mirror": L("Phase-2-Selektoren gespiegelt" if exact else
                    "Phase-2-Selektoren passen (umfassend)",
                    "phase 2 selectors mirrored" if exact else "phase 2 selectors fit (wider)"),
    }.get(why, why)  # fmt: skip


def how_text(how: str) -> str:
    """Wie die Kopplung zustande kam (Ansicht, Weg)."""
    return {
        "gw": L("automatisch: Gegenstelle ist eine Adresse dieser Firewall",
                "automatic: peer is an address of this firewall"),
        "vip": L("automatisch: Gegenstelle ist eine VIP vor dieser Firewall",
                 "automatic: peer is a VIP in front of this firewall"),
        "reverse": L("automatisch: ihr Tunnel zeigt auf diese Firewall (NAT davor)",
                     "automatic: its tunnel points to this firewall (NAT in front)"),
        "dialup": L("automatisch: verbundene Dial-up-Gegenstelle",
                    "automatic: connected dial-up peer"),
        "spoke": L("automatisch: ihr statischer Tunnel zeigt auf diese Firewall",
                   "automatic: its static tunnel points to this firewall"),
        "manual": L("von Hand gekoppelt", "coupled manually"),
    }.get(how, how)  # fmt: skip


class Pairing:
    """Kopplungen der Tunnel eines Mandanten - einmal je `vpn.Index` gerechnet."""

    def __init__(self, index: "vpn_store.Index", rows: list[VpnPair]):
        self.index = index
        self.by_end: dict[End, Tunnel] = {end_of(t): t for t in index.tunnels}
        self.links: dict[End, list[Link]] = defaultdict(list)
        self.hints: dict[End, list[Hint]] = defaultdict(list)
        self.separated: dict[frozenset, int] = {}  # Paar -> Zeile (automatische geloest)
        self.orphans: list[VpnPair] = []
        self._vips: dict[str, list[tuple[int, str | None, str]]] | None = None
        self._sels: dict[End, _Sel] = {}  # Selektoren je Tunnel als Bereiche
        manual: list[Link] = []
        for r in rows:
            a, b = (self.by_end.get(e) for e in r.ends)
            if a is None or b is None:
                self.orphans.append(r)
                continue
            if r.mode == SEPARATED:
                self.separated[frozenset((end_of(a), end_of(b)))] = r.id
            else:
                manual.append(Link(a, b, MANUAL, "manual", r.id))
        for link in manual:
            self._add(link)
        held = {e for e, links in self.links.items() if self.by_end[e].kind != "dialup" and links}
        for link in self._auto():
            ends = (end_of(link.a), end_of(link.b))
            if frozenset(ends) in self.separated or held & set(ends):
                continue
            if any(end_of(x.other(self.by_end[ends[0]])) == ends[1]
                   for x in self.links.get(ends[0], [])):  # fmt: skip
                continue
            self._add(link)

    def mirror(self, a: Tunnel, b: Tunnel) -> str | None:
        return mirrored(a, b, self._sels)

    def _add(self, link: Link) -> None:
        self.links[end_of(link.a)].append(link)
        self.links[end_of(link.b)].append(link)

    # -- Adressen ---------------------------------------------------------------------------

    def vips(self) -> dict[str, list[tuple[int, str | None, str]]]:
        """Oeffentliche Adresse -> (Quellsystem, VDOM, Interface) der FortiGate dahinter: VIPs der
        gelesenen FortiGates, deren interne Adresse ein Interface einer gelesenen FortiGate ist
        (IKE 500/4500 bzw. ohne Port-Weiterleitung)."""
        if self._vips is None:
            out: dict[str, list[tuple[int, str | None, str]]] = defaultdict(list)
            addrs = self.index.addresses()
            stmt = select(NatRule.ext_ip, NatRule.mapped_ip, NatRule.portforward,
                          NatRule.ext_port).where(
                NatRule.tenant_id == self.index.tenant_id, NatRule.nat_type == "dnat_vip",
                NatRule.source_system_id.in_(self.index.firewalls()))  # fmt: skip
            for ext, mapped, forward, port in self.index.session.execute(stmt):
                ext, mapped = _single(ext), _single(mapped)
                if not ext or not mapped or (forward and not _ike(port)):
                    continue
                for use in addrs.get(mapped, []):
                    if use not in out[ext]:
                        out[ext].append(use)
            self._vips = out
        return self._vips

    def owners(self, ip: str | None) -> list[tuple[int, str | None, str, str]]:
        """Gelesene FortiGates einer Adresse: (Quellsystem, VDOM, Interface, `gw` | `vip`)."""
        if not ip:
            return []
        fws = self.index.firewalls()
        out = [(s, v, i, "gw") for s, v, i in self.index.addresses().get(ip, []) if s in fws]
        out += [(s, v, i, "vip") for s, v, i in self.vips().get(ip, []) if s in fws]
        return out

    def public(self, t: Tunnel) -> set[str]:
        """Adressen, unter denen die Gegenstelle den Tunnel erreicht: eigene Adressen und VIPs
        davor."""
        mine = set(self.index.own(t))
        mine |= {ext for ext, uses in self.vips().items()
                 for s, _v, i in uses if s == t.source_system_id and i == t.interface}  # fmt: skip
        return mine

    # -- automatisch ------------------------------------------------------------------------

    def _auto(self) -> list[Link]:
        idx = self.index
        fws = idx.firewalls()
        found: dict[frozenset, Link] = {}
        per_static: dict[End, list[Link]] = defaultdict(list)

        def take(t: Tunnel, x: Tunnel, how: str, back: str) -> None:
            key = frozenset((end_of(t), end_of(x)))
            if key in found:
                return
            link = Link(t, x, AUTO, how, back=back)
            found[key] = link
            for e in (t, x):
                if e.kind != "dialup":
                    per_static[end_of(e)].append(link)

        nat: list[Tunnel] = []  # Gegenstelle gehoert keinem gelesenen Geraet (NAT davor?)
        for t in idx.tunnels:
            if t.kind == "dialup" or not t.remote_gw or t.source_system_id not in fws:
                continue
            hits = [h for h in self.owners(t.remote_gw) if h[0] != t.source_system_id]
            if not hits:
                nat.append(t)
                continue
            mine = self.public(t)
            # wie die FortiGate dort die Phase 1 waehlt: der Tunnel mit der Adresse des Absenders
            # als Gegenstelle (zeigt zurueck; mit NAT davor unbekannt: gespiegelte Selektoren),
            # sonst ein Dial-up auf dem Interface (Selektoren gespiegelt)
            back: list[tuple[Tunnel, str]] = []
            nat_back: list[tuple[Tunnel, str]] = []
            dial: list[tuple[Tunnel, str]] = []
            weak: list[tuple[Tunnel, str]] = []
            for sid, vdom, iface, via in hits:
                for x in idx.by_sid.get(sid, []):
                    if vdom not in (None, x.context):
                        continue
                    if x.kind != "dialup":
                        if x.remote_gw and x.remote_gw in mine:
                            back.append((x, via))
                        elif x.interface == iface and not self.owners(x.remote_gw) \
                                and self.mirror(t, x):  # fmt: skip
                            nat_back.append((x, via))
                        elif x.interface == iface:
                            weak.append((x, via))
                    elif x.interface == iface:
                        (dial if self.mirror(t, x) else weak).append((x, via))
            chosen = None
            tiers = ((_unique(back), "gw"), (_unique(nat_back), "reverse"),
                     (_unique(dial), "spoke"))  # fmt: skip
            for tier, seen_from in tiers:
                if len(tier) > 1:  # mehrere: nur ein gleich gespiegelter entscheidet
                    exact = [x for x in tier if self.mirror(t, x[0]) == "exact"]
                    tier = exact if len(exact) == 1 else tier
                if len(tier) == 1:
                    x, via = tier[0]
                    back_how = seen_from
                    if seen_from == "gw":
                        back_how = "gw" if x.remote_gw in idx.own(t) else "vip"
                    chosen = (x, via, back_how)
                if tier:
                    break
            if chosen is not None:
                take(t, *chosen)
                continue
            for x, via in _unique(back + nat_back + dial + weak):
                self._hint(t, x, via, self.mirror(t, x) == "exact")
        linked = {e for key in found for e in key}
        for t in nat:  # danach: ihr Tunnel zeigt auf uns - nur, was noch frei ist
            if end_of(t) not in linked:
                self._reverse(t, take, linked)
        for x in idx.tunnels:  # Dial-up: verbundene Gegenstellen
            if x.kind != "dialup":
                continue
            for inst in x.instances:
                cands = []
                for sid, vdom, iface, _via in self.owners(inst.get("remote_gw")):
                    if sid == x.source_system_id:
                        continue
                    for t in idx.by_sid.get(sid, []):
                        if t.kind == "dialup" or vdom not in (None, t.context):
                            continue
                        if t.interface != iface and inst.get("remote_gw") not in idx.own(t):
                            continue
                        aim = [h[0] for h in self.owners(t.remote_gw)]
                        if aim and x.source_system_id not in aim:
                            continue  # zeigt auf eine andere gelesene Firewall
                        cands.append(t)
                cands = list({end_of(t): t for t in cands}.values())
                fit = [t for t in cands if self.mirror(t, x)]
                if len(fit) == 1:
                    take(fit[0], x, "dialup", "dialup")
                else:
                    for t in cands:
                        self._hint(x, t, "dialup", self.mirror(t, x) == "exact")
        out = []
        for link in found.values():
            ends = [e for e in (link.a, link.b) if e.kind != "dialup"]
            if any(len(per_static[end_of(e)]) > 1 for e in ends):
                # ein statischer Tunnel mit mehreren moeglichen Gegenstellen: nicht eindeutig
                self._hint(link.a, link.b, link.how, self.mirror(link.a, link.b) == "exact")
                continue
            out.append(link)
        return out

    def _reverse(self, t: Tunnel, take, linked: set) -> None:
        """NAT vor der Gegenstelle: ein Tunnel einer gelesenen FortiGate zeigt auf eine eigene
        Adresse - nur eindeutig (mehrere Tunnel auf derselben Adresse: ueber die Selektoren) und
        mit gespiegelten Selektoren; ein Tunnel, der schon gekoppelt ist, kommt nicht in Frage."""
        idx = self.index
        mine = self.public(t)
        if not mine:
            return
        found = []
        for x in idx.tunnels:
            if x.source_system_id == t.source_system_id or x.kind == "dialup":
                continue
            if x.remote_gw not in mine or end_of(x) in linked:
                continue
            rivals = [o for o in idx.by_sid[t.source_system_id]
                      if o.kind != "dialup" and x.remote_gw in self.public(o)
                      and not self.owners(o.remote_gw)]  # fmt: skip
            if len(rivals) > 1:
                rivals = [o for o in rivals if vpn_store._fits(o, x)]
            if rivals == [t]:
                found.append(x)
        if len(found) == 1 and self.mirror(t, found[0]):
            take(t, found[0], "reverse", "gw")
        else:
            for x in found:
                self._hint(t, x, "gw", self.mirror(t, x) == "exact")

    def _hint(self, t: Tunnel, x: Tunnel, why: str, exact: bool) -> None:
        for a, b in ((t, x), (x, t)):
            if not any(end_of(h.tunnel) == end_of(b) for h in self.hints[end_of(a)]):
                self.hints[end_of(a)].append(Hint(b, why, exact))

    # -- Abfragen ---------------------------------------------------------------------------

    def of(self, t: Tunnel) -> list[Link]:
        return self.links.get(end_of(t), [])

    def partner(self, t: Tunnel, inst: dict | None = None, dst=None) -> Link | None:
        """Gegenstueck des Tunnels fuer einen Weg: statisch das gekoppelte; Dial-up das der
        verbundenen Gegenstelle (Adresse, Netze), sonst das, dessen lokale Selektoren das Ziel
        enthalten - nur eindeutig, sonst None (dann gilt die Adresse der Gegenstelle)."""
        links = self.of(t)
        if not links:
            return None
        if t.kind != "dialup":
            return links[0]
        gw = (inst or {}).get("remote_gw")
        if gw:
            hit = [k for k in links if gw in self.public(k.other(t))]
            if len(hit) == 1:
                return hit[0]
            if self.owners(gw):  # eine andere gelesene Firewall: ihre Adresse entscheidet
                return None
        nets = (inst or {}).get("nets") or []
        if nets:
            hit = [k for k in links if _overlaps(nets, k.other(t).selectors("src"))]
            if len(hit) == 1:
                return hit[0]
        if dst is not None:
            hit = [k for k in links if coverage(k.other(t).selectors("src"), dst) == "full"]
            if len(hit) == 1:
                return hit[0]
        return links[0] if len(links) == 1 and not gw else None

    def suggestions(self, t: Tunnel) -> list[Hint]:
        """Vorschlaege fuer eine Kopplung von Hand: nicht eindeutige Treffer der automatischen
        Erkennung, dazu Tunnel anderer FortiGates mit gespiegelten Selektoren - ohne die schon
        gekoppelten (ein statischer Tunnel mit Kopplung kommt nicht in Frage)."""
        if t.kind != "dialup" and self.of(t):
            return []
        taken = {end_of(k.other(t)) for k in self.of(t)}
        out: list[Hint] = [h for h in self.hints.get(end_of(t), [])
                           if end_of(h.tunnel) not in taken]  # fmt: skip
        out = [h for h in out
               if frozenset((end_of(t), end_of(h.tunnel))) not in self.separated]  # fmt: skip
        for x in self.index.tunnels:
            if x.source_system_id == t.source_system_id or end_of(x) in taken:
                continue
            if frozenset((end_of(t), end_of(x))) in self.separated:
                continue
            if any(end_of(h.tunnel) == end_of(x) for h in out):
                continue
            if t.kind == "dialup" and x.kind == "dialup":
                continue
            m = self.mirror(t, x)
            if m == "exact" or (m == "covers" and _specific(t) and _specific(x)):
                out.append(Hint(x, "mirror", m == "exact"))
        out = [h for h in out if h.tunnel.kind == "dialup" or not self.of(h.tunnel)]
        out.sort(key=lambda h: (h.why == "mirror", not h.exact))
        return out[:SUGGESTIONS]


def _unique(items: list[tuple[Tunnel, str]]) -> list[tuple[Tunnel, str]]:
    out: dict[End, tuple[Tunnel, str]] = {}
    for x, via in items:
        out.setdefault(end_of(x), (x, via))
    return list(out.values())


def _single(text: str | None) -> str | None:
    """Eine einzelne Adresse (auch `a-a`), sonst None."""
    from app.store.nat import host_ip

    value = str(text or "").strip()
    if "-" in value:
        a, _, b = value.partition("-")
        if host_ip(a) is None or host_ip(a) != host_ip(b):
            return None
        value = a
    return host_ip(value)


def _ike(port: str | None) -> bool:
    """Leitet die Port-Weiterleitung einer VIP IKE weiter (UDP 500 bzw. 4500)?"""
    for part in str(port or "").replace(",", " ").split():
        lo, _, hi = part.partition("-")
        try:
            a, b = int(lo), int(hi or lo)
        except ValueError:
            continue
        if any(a <= p <= b for p in _IKE_PORTS):
            return True
    return False


# -- Pflege (von Hand) ----------------------------------------------------------------------


class Invalid(ValueError):
    """Kopplung nicht moeglich (Text deutsch, `api/vpn.py` uebersetzt)."""


def rows(session: Session, tenant_id: int) -> list[VpnPair]:
    stmt = select(VpnPair).where(VpnPair.tenant_id == tenant_id).order_by(VpnPair.id)
    return list(session.scalars(stmt))


def set_pair(session: Session, index: "vpn_store.Index", a: Tunnel, b: Tunnel, mode: str,
             actor: str) -> tuple[VpnPair, str | None]:  # fmt: skip
    """Zwei Tunnel von Hand koppeln (`coupled`) bzw. eine automatische Kopplung loesen
    (`separated`). Liefert die Zeile und den bisherigen Modus (None = neu)."""
    if a.source_system_id == b.source_system_id:
        raise Invalid("beide Tunnel sind auf derselben Firewall")
    if a.kind == "dialup" and b.kind == "dialup":
        raise Invalid("zwei Dial-up-Tunnel verbinden sich nicht miteinander")
    pairing = index.pairing()
    ea, eb = ordered(end_of(a), end_of(b))
    if mode == COUPLED:
        for t, other in ((a, b), (b, a)):
            if t.kind == "dialup":
                continue
            held = [k for k in pairing.of(t) if k.origin == MANUAL
                    and end_of(k.other(t)) != end_of(other)]  # fmt: skip
            if held:
                raise Invalid(f"Tunnel {t.name} ist schon von Hand gekoppelt – zuerst lösen")
    elif not any(end_of(k.other(a)) == end_of(b) and k.origin == AUTO for k in pairing.of(a)):
        raise Invalid("keine automatische Kopplung zwischen diesen Tunneln")
    row = session.scalars(select(VpnPair).where(
        VpnPair.tenant_id == index.tenant_id, VpnPair.a_system_id == ea[0],
        VpnPair.a_context == ea[1], VpnPair.a_tunnel == ea[2], VpnPair.b_system_id == eb[0],
        VpnPair.b_context == eb[1], VpnPair.b_tunnel == eb[2])).first()  # fmt: skip
    old = row.mode if row is not None else None
    if row is None:
        row = VpnPair(tenant_id=index.tenant_id, a_system_id=ea[0], a_context=ea[1],
                      a_tunnel=ea[2], b_system_id=eb[0], b_context=eb[1], b_tunnel=eb[2],
                      mode=mode, changed_by=actor)  # fmt: skip
        session.add(row)
    else:
        row.mode, row.changed_by, row.changed_at = mode, actor, datetime.now(UTC)
    session.flush()
    return row, old
