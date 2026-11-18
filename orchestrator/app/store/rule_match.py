"""Zuordnung Regel <-> Applikation nach echtem Treffer (ADR 0071).

Anwender 2026-11-18: „Es soll absolut der Wahrheit entsprechen, wie das Netzwerk in Echt
funktioniert“ und „Ich bin für ersetzen, da das alte Verhalten nicht immer richtig lag“.

Bis 1.21 fragte die Doku „welche Regel **nennt** die Adresse“ (`rule_index.covers`: eine SQL-
Abfrage je Adresse über die Bereiche aller Firewalls - ohne Negation, ohne Reihenfolge, ohne
Dienst). Hier steht, welche Regel den Verkehr eines Systems **trifft** - für alle Regelwerk-
Profile; die Profile entscheiden weiter nur, was die Doku davon zeigt (ADR 0043).

- **Wirksame Menge je Policy und Seite** (`Matcher.covers`): die Bereiche der Policy; eine
  negierte Seite (`srcaddr-negate`/`dstaddr-negate`) ist ihr **Komplement** - die Policy trifft
  das System, wenn keiner ihrer Bereiche es berührt, und sie trifft es nicht, wenn sie es nennt.
  FQDN-Objekte treffen die Adressen, auf die der Name laut IPAM auflöst (mit Herkunft im Bezug).
- **Wirksamer Rest** (`Matcher.shadow`): je Firewall/VDOM die Policies in Listen-Reihenfolge.
  Eine Policy ist für ein System **überdeckt**, wenn frühere sichere Policies allen Verkehr des
  Systems, den sie trifft, schon entscheiden: jede von ihnen enthält Ein- und Ausgang, die
  Gegenstellen und das System, zusammen decken sie alle Dienste (Protokoll und Ports als
  Intervalle). „Sicher“ heisst: eingeschaltet, ohne Benutzer/Gruppen, Zeitplan `always`, beide
  Seiten als Adressen bekannt (kein FQDN, Internet Service, Land), Dienst bekannt - was nicht
  prüfbar ist, überdeckt nie (sonst verschwänden echte Regeln). Gerechnet je **Adressklasse**
  (`policy_check.Checker.addr_class`): Systeme zwischen denselben Grenzen aller Bereiche einer
  Firewall haben dasselbe Ergebnis.
- **Ganz überdeckte Policy** (`Matcher.shadowed_rules`, Befund `rule_shadowed`): dasselbe für
  die ganze Policy statt für ein System.
- **Weg** als harte Bedingung: `appdoc_path.OnPath` - nur Firewalls auf dem Weg tragen bei; Weg
  unbekannt: die Regel bleibt, markiert „Weg nicht geprüft“ und gezählt.

Grenzen (bewusst, nie zu Lasten einer echten Regel): Überdeckung, die sich nur aus mehreren
Policies mit je einem Teil der Gegenstellen bzw. Interfaces ergibt, wird nicht erkannt (die Regel
bleibt); Policies mit einer VIP im Ziel werden nicht als überdeckt gerechnet.

Im Speicher je Datenstand (ADR 0049): die Policies teilt der Wege-Speicher des Mandanten
(`path_store`), der Index und die Ergebnisse je Adressklasse liegen daneben - keine SQL-Abfrage
je Adresse. Nur lesend.
"""

from bisect import bisect_right
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import DeviceObject, Proto, Rule, RuleDomain, RuleObjectUsage
from app.store import policy_check, references
from app.store.rule_index import Cover, range_text, value_range
from app.store.topology import endpoint, span

# Fassung der Zuordnung - Teil des Fingerabdrucks der Doku (ADR 0037): eine neue Fassung
# rechnet jeden Stand einmal neu
VERSION = 2  # 2: Gegenpruefung (ADR 0071 Nachtrag) - belegter Teil des Wegs, Verkehr ohne Policy

ANY = "any"  # Seite ohne Einschraenkung
_SHIFT = {4: 16, 6: 112}
_FULL = {4: (1 << 32) - 1, 6: (1 << 128) - 1}
_ALL_PORTS = (0, 65535)
SHOWN = 3  # ueberdeckende Policies in der Anzeige


def _first(via: str | None) -> str:
    return policy_check._first(via)


def _desc(p, side: str, fam: int):
    """Menge einer Seite der Policy in einer Familie: `ANY`, zusammengefasste Intervalle,
    [] (nichts in dieser Familie) bzw. None (nicht als Adressen bekannt: negiert, FQDN,
    Internet Service, Land, VIP im Ziel)."""
    unknown = p.negate.get(side) or p.unresolved.get(side) or p.public_only.get(side)
    if unknown or side in p.isdb or not p.endpoints.get(side) or (side == "dst" and p.vips):
        return None
    found = sorted((r[1], r[2]) for r in p.ranges.get(side, []) if r[0] == fam)
    if any(r[0] == fam and r[3] for r in p.ranges.get(side, [])):
        return ANY
    merged: list[tuple[int, int]] = []
    for lo, hi in found:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _inside(outer, inner) -> bool:
    """Enthaelt die Menge `outer` die Menge `inner` ganz?"""
    if outer is ANY:
        return True
    if inner is ANY or not inner:
        return False
    starts = [lo for lo, _hi in outer]
    for lo, hi in inner:
        i = bisect_right(starts, lo) - 1
        if i < 0 or outer[i][1] < hi:
            return False
    return True


def _services(p):
    """Dienste als (alle, Protokoll -> Port-Intervalle); None = nicht als Menge bekannt."""
    if p.service_negate or not p.services:
        return None
    every, by_proto = False, defaultdict(list)
    for proto, start, end, name in p.services:
        if name == "all":
            every = True
        elif proto in (Proto.TCP, Proto.UDP):
            by_proto[proto.value].append((start or 0, end or 65535))
        elif proto in (Proto.ICMP, Proto.ICMPV6) or str(name).startswith("icmp"):
            by_proto["icmp"].append(_ALL_PORTS)
        else:
            return None
    return every, by_proto


def _covers_services(need, have: list) -> bool:
    """Decken die Dienste `have` (frueherer Policies) zusammen die Dienste `need`?"""
    if any(h[0] for h in have):
        return True
    if need is None or need[0]:
        return False  # alles bzw. unbekannt: nur `ALL` deckt
    for proto, wanted in need[1].items():
        spans = sorted(iv for h in have for iv in h[1].get(proto, ()))
        for lo, hi in wanted:
            at = lo
            for a, b in spans:
                if a > at:
                    break
                at = max(at, b + 1)
                if at > hi:
                    break
            if at <= hi:
                return False
    return True


def _ports_cover(outer: list[str], inner: list[str]) -> bool:
    """Enthalten Interfaces/Zonen `outer` die von `inner` (nach Namen; `any` enthaelt alles)?"""
    if any(n.lower() == ANY for n in outer):
        return True
    return bool(inner) and not any(n.lower() == ANY for n in inner) and set(inner) <= set(outer)


def _sure(p) -> bool:
    """Kann die Policy als fruehere sicher entscheiden (ohne nicht pruefbare Bedingung)?"""
    return (p.enabled and not p.identity and bool(p.srcintf) and bool(p.dstintf)
            and (p.schedule or "").lower() in policy_check._OPEN)  # fmt: skip


class _Stab:
    """Bereiche nach Faechern (/16): welche Eintraege enthalten einen Bereich?"""

    def __init__(self, fam: int) -> None:
        self.shift = _SHIFT[fam]
        self.buckets: dict[int, list[tuple]] = defaultdict(list)
        self.wide: list[tuple] = []

    def add(self, lo: int, hi: int, item) -> None:
        if lo >> self.shift == hi >> self.shift:
            self.buckets[lo >> self.shift].append((lo, hi, item))
        else:
            self.wide.append((lo, hi, item))

    def containing(self, lo: int, hi: int):
        for a, b, item in self.wide:
            if a <= lo and hi <= b:
                yield a, b, item
        if lo >> self.shift == hi >> self.shift:
            for a, b, item in self.buckets.get(lo >> self.shift, ()):
                if a <= lo and hi <= b:
                    yield a, b, item

    def overlapping(self, lo: int, hi: int):
        for a, b, item in self.wide:
            if a <= hi and lo <= b:
                yield a, b, item
        first, last = lo >> self.shift, hi >> self.shift
        keys = range(first, last + 1) if last - first <= 4096 else [
            k for k in self.buckets if first <= k <= last]  # fmt: skip
        for k in keys:
            for a, b, item in self.buckets.get(k, ()):
                if a <= hi and lo <= b:
                    yield a, b, item


def _sweep(policies: list, fam: int, forced: str | None = None,
           memo: dict | None = None) -> dict[int, list[int]]:  # fmt: skip
    """Policies in Listen-Reihenfolge: ID -> IDs der frueheren, die sie zusammen ganz
    ueberdecken. `forced`: diese Seite gilt bei allen als erfuellt (das System steht dort).
    `memo`: Mengen und Dienste je Policy - ueber die Adressklassen hinweg gleich."""
    memo = memo if memo is not None else {}

    def desc(p, s: str):
        key = (p.id, s, fam)
        if key not in memo:
            memo[key] = _desc(p, s, fam)
        return memo[key]

    def services(p):
        key = (p.id, "svc")
        if key not in memo:
            memo[key] = _services(p)
        return memo[key]

    out: dict[int, list[int]] = {}
    open_: list[tuple] = []  # fruehere mit beiden Seiten ANY: (Policy, Dienste)
    stab = {"src": _Stab(fam), "dst": _Stab(fam)}
    info: dict[int, tuple] = {}
    for p in policies:
        d = {s: ANY if s == forced else desc(p, s) for s in ("src", "dst")}
        svc = services(p)
        vip = bool(p.vips)
        if p.enabled and not vip and all(v is None or v for v in d.values()):
            # nicht bekannte Seite der geprueften Policy: nur eine fruehere mit ANY deckt sie
            want = {s: ANY if v is None else v for s, v in d.items()}
            cands: dict[int, object] = {}
            if want["src"] is not ANY:
                lo, hi = want["src"][0]
                cands.update((q.id, q) for _a, _b, q in stab["src"].containing(lo, hi))
            if want["dst"] is not ANY:
                lo, hi = want["dst"][0]
                cands.update((q.id, q) for _a, _b, q in stab["dst"].containing(lo, hi))
            have, by = [], []
            for q, qsvc in open_:
                if _ports_cover(q.srcintf, p.srcintf) and _ports_cover(q.dstintf, p.dstintf):
                    have.append(qsvc)
                    by.append(q.id)
            for q in cands.values():
                qd, qsvc = info[q.id]
                if (_inside(qd["src"], want["src"]) and _inside(qd["dst"], want["dst"])
                        and _ports_cover(q.srcintf, p.srcintf)
                        and _ports_cover(q.dstintf, p.dstintf)):  # fmt: skip
                    have.append(qsvc)
                    by.append(q.id)
            if have and _covers_services(svc, have):
                out[p.id] = sorted(by)
        # als fruehere fuer die folgenden: nur sicher und mit bekannten Mengen
        if not _sure(p) or svc is None or vip or any(not v for v in d.values()):
            continue
        info[p.id] = (d, svc)
        if d["src"] is ANY and d["dst"] is ANY:
            open_.append((p, svc))
            continue
        for s in ("src", "dst"):
            if d[s] is not ANY:
                for lo, hi in d[s]:
                    stab[s].add(lo, hi, p)
    return out


class Matcher:
    """Zuordnung eines Mandanten; der Zustand (reine Daten) liegt im Wege-Speicher."""

    def __init__(self, session: Session, tenant_id: int, checker: policy_check.Checker,
                 state: dict | None = None, dns=None) -> None:  # fmt: skip
        self.session = session
        self.tenant_id = tenant_id
        self.checker = checker
        self.state = state if state is not None else {}
        self._dns = dns

    # -- Aufbau (einmal je Datenstand) ---------------------------------------------------

    @property
    def index(self) -> dict:
        if "index" not in self.state:
            self.state["index"] = self._build()
        return self.state["index"]

    def scopes(self) -> list[tuple[int, str | None]]:
        if "scopes" not in self.state:
            rows = self.session.execute(
                select(Rule.source_system_id, Rule.vdom)
                .where(Rule.tenant_id == self.tenant_id, Rule.domain == RuleDomain.FIREWALL)
                .distinct()
            ).all()
            self.state["scopes"] = sorted(
                {(s, v) for s, v in rows}, key=lambda x: (x[0], x[1] or "")
            )
        return self.state["scopes"]

    def _build(self) -> dict:
        pol: dict[int, tuple] = {}  # Policy-ID -> (Policy, Quellsystem, VDOM)
        anys = defaultdict(list)  # (Seite, Familie) -> [(ID, Bezug)]
        stabs: dict[tuple, _Stab] = {}
        neg = defaultdict(list)  # Seite -> [ID] mit negierter Seite
        fqdn: dict[str, dict[str, set[int]]] = {"src": defaultdict(set), "dst": defaultdict(set)}
        for sid, vdom in self.scopes():
            for p in self.checker.policies(sid, vdom):
                if p.id in pol:
                    continue
                pol[p.id] = (p, sid, vdom)
                for side in ("src", "dst"):
                    for name in p.fqdns.get(side, ()):
                        if not p.negate.get(side):
                            fqdn[side][name].add(p.id)
                    if p.negate.get(side):
                        neg[side].append(p.id)
                        continue
                    for fam, lo, hi, is_any, via in p.ranges.get(side, []):
                        if fam not in _SHIFT:
                            continue
                        if is_any:
                            anys[(side, fam)].append((p.id, via))
                        else:
                            stabs.setdefault((side, fam), _Stab(fam)).add(lo, hi, (p.id, via))
        # FQDN-Objekte in Gruppen (Objekte der Firewall): ueber die Verwendung je Regel
        if pol:
            rows = self.session.execute(
                select(RuleObjectUsage.rule_id, RuleObjectUsage.role, DeviceObject.fqdn)
                .join(DeviceObject, DeviceObject.id == RuleObjectUsage.device_object_id)
                .join(Rule, Rule.id == RuleObjectUsage.rule_id)
                .where(Rule.tenant_id == self.tenant_id, Rule.domain == RuleDomain.FIREWALL,
                       DeviceObject.fqdn.is_not(None), RuleObjectUsage.role.in_(("src", "dst")))
            )  # fmt: skip
            for rid, role, name in rows:
                host = policy_check._fqdn(name)
                if host and rid in pol and not pol[rid][0].negate.get(role):
                    fqdn[role][host].add(rid)
        return {"pol": pol, "anys": dict(anys), "stabs": stabs, "neg": dict(neg), "fqdn": fqdn}

    def policy(self, rule_id: int):
        entry = self.index["pol"].get(rule_id)
        return entry[0] if entry else None

    # -- Welche Regeln treffen eine Adresse ---------------------------------------------

    def covers(self, values: list[str], overlap: bool = False) -> dict[int, Cover]:
        """Regel-ID -> Abdeckung (Quelle/Ziel) fuer IPs bzw. Netze - wie `rule_index.covers`
        fuer Firewall-Regeln, aber nach der wirksamen Menge: eine negierte Seite ist ihr
        Komplement, FQDN-Objekte ueber das IPAM. `overlap`: Ueberschneidung genuegt (EPG)."""
        ix = self.index
        out: dict[int, Cover] = {}
        for value in dict.fromkeys(values):
            parsed = value_range(value)
            if parsed is None:
                continue
            fam, lo, hi = parsed[0], int(parsed[1], 16), int(parsed[2], 16)
            names = self._names(value) if lo == hi else []
            for role in ("src", "dst"):
                for pid, via in ix["anys"].get((role, fam), ()):
                    out.setdefault(pid, Cover()).add(role, _FULL[fam], via, True, False, "")
                stab = ix["stabs"].get((role, fam))
                if stab is not None:
                    found = stab.overlapping(lo, hi) if overlap else stab.containing(lo, hi)
                    for a, b, (pid, via) in found:
                        inside = lo <= a and b <= hi
                        out.setdefault(pid, Cover()).add(role, b - a, via, False, inside,
                                                         range_text(fam, a, b))  # fmt: skip
                for pid in ix["neg"].get(role, ()):
                    p = ix["pol"][pid][0]
                    if p.unresolved.get(role) or p.public_only.get(role) or role in p.isdb:
                        continue  # negierter FQDN bzw. Internet Service: nicht bestimmbar
                    same = [r for r in p.ranges.get(role, []) if r[0] == fam]
                    if any(r[1] <= hi and lo <= r[2] for r in same):
                        continue  # die Policy nennt das System: sie trifft es gerade nicht
                    listed = ", ".join(dict.fromkeys(str(r[4]) for r in same if r[4]))[:120]
                    via = L(f"nicht {listed}", f"not {listed}") if listed else L(
                        "negiert", "negated")  # fmt: skip
                    out.setdefault(pid, Cover()).add(role, _FULL[fam] - 1, via, False, False, "")
                for name in names:
                    for pid in ix["fqdn"][role].get(name, ()):
                        via = L(f"{name} (FQDN laut IPAM)", f"{name} (FQDN per IPAM)")
                        out.setdefault(pid, Cover()).add(role, 0, via, False, True,
                                                         value.split("/")[0])  # fmt: skip
        return out

    def _names(self, value: str) -> list[str]:
        """DNS-Namen einer IP aus dem IPAM (klein) - nur, wenn Policies FQDN-Objekte nennen."""
        fq = self.index["fqdn"]
        if not fq["src"] and not fq["dst"]:
            return []
        if callable(self._dns):
            self._dns = self._dns()
        if self._dns is None:
            self._dns = references.context(self.session, self.tenant_id)
        return [str(n).lower().rstrip(".") for n in references.dns_names(self._dns, value)]

    def negated(self, rule_id: int, role: str) -> bool:
        p = self.policy(rule_id)
        return bool(p and p.negate.get(role))

    def derived(self, rule_id: int, via: str | None) -> bool:
        """Erfasst die Regel das System nur ueber eine VIP bzw. einen Load Balancer dahinter?"""
        p = self.policy(rule_id)
        if p is None or not via:
            return False
        return _first(via) in p.vips or " › " in via

    # -- Wirksamer Rest ------------------------------------------------------------------

    def shadow(self, rule_id: int, side: str, values: list[str]) -> list[int] | None:
        """IDs der frueheren Policies, die die Regel fuer **alle** Adressen des Systems auf
        `side` ganz ueberdecken - None, wenn sie fuer eine davon (noch) Verkehr trifft.

        Nennt die Regel nur einen Teil eines Werts (Host im Subnetz einer EPG), zaehlen ihre
        eigenen Bereiche darin - der Verkehr des Systems, den sie trifft."""
        entry = self.index["pol"].get(rule_id)
        if entry is None:
            return None
        p, sid, vdom = entry
        if not p.enabled or p.vips:
            return None
        by: list[int] = []
        seen = False
        for value in dict.fromkeys(values):
            try:
                ip = endpoint(value)
            except ValueError:
                continue
            if ip is None:
                continue
            ok, _via, part = policy_check._address(p, side, ip)
            if ok is False:
                continue  # diese Adresse trifft die Regel nicht (z. B. nur ueber FQDN)
            parts = [ip] if ok else (self._parts(p, side, ip) if part else None)
            if not parts:
                return None  # nur moeglich und nicht als eigene Bereiche bestimmbar
            for own in parts:
                found = self._class(sid, vdom, side, own).get(rule_id)
                if found is None:
                    return None
                seen = True
                by += [x for x in found if x not in by]
        return by if seen else None

    @staticmethod
    def _parts(p, side: str, ip) -> list | None:
        """Bereiche der Policy auf `side` innerhalb des Netzes `ip` (als Netze) - None, wenn
        einer ueber das Netz hinausragt bzw. kein Netz ist."""
        lo, hi = span(ip)
        out = []
        for fam, a, b, _any, _via in p.ranges.get(side, []):
            if fam != ip.version or b < lo or hi < a:
                continue
            if a < lo or hi < b or p.negate.get(side):
                return None
            try:
                out.append(endpoint(range_text(fam, a, b)))
            except ValueError:
                return None
            if out[-1] is None or span(out[-1]) != (a, b):
                return None  # Bereich a-b, der kein Netz ist
        return out

    def _class(self, sid: int, vdom: str | None, side: str, ip) -> dict[int, list[int]]:
        """Ueberdeckte Policies fuer die Adressklasse von `ip` auf `side` dieser Firewall/VDOM."""
        cache = self.state.setdefault("classes", {})
        key = (sid, vdom, side, self.checker.addr_class(sid, vdom, side, ip))
        if key not in cache:
            # nur Policies, die die Adresse treffen koennen (Index), in Listen-Reihenfolge
            order = self._order(sid, vdom)
            lo, hi = span(ip)
            ix, fam = self.index, ip.version
            ids = {pid for pid, _via in ix["anys"].get((side, fam), ()) if pid in order}
            stab = ix["stabs"].get((side, fam))
            if stab is not None:
                ids |= {pid for _a, _b, (pid, _via) in stab.containing(lo, hi) if pid in order}
            ids |= {pid for pid in ix["neg"].get(side, ()) if pid in order}
            mine = [order[pid][1] for pid in sorted(ids, key=lambda i: order[i][0])
                    if policy_check._address(order[pid][1], side, ip)[0] is True]  # fmt: skip
            cache[key] = _sweep(mine, fam, forced=side, memo=self.state.setdefault("memo", {}))
        return cache[key]

    def _order(self, sid: int, vdom: str | None) -> dict[int, tuple]:
        """Policy-ID -> (Stelle in der Liste, Policy) einer Firewall/VDOM."""
        cache = self.state.setdefault("order", {})
        if (sid, vdom) not in cache:
            policies = self.checker.policies(sid, vdom)
            cache[(sid, vdom)] = {p.id: (i, p) for i, p in enumerate(policies)}
        return cache[(sid, vdom)]

    def shadowed_rules(self, sid: int, vdom: str | None) -> dict[int, list[int]]:
        """Ganz ueberdeckte Policies einer Firewall/VDOM: ID -> fruehere, die sie ueberdecken
        (in jeder Familie, in der sie Adressen nennt)."""
        cache = self.state.setdefault("whole", {})
        key = (sid, vdom)
        if key not in cache:
            policies = self.checker.policies(sid, vdom)
            per_fam = {fam: _sweep(policies, fam) for fam in (4, 6)}
            out: dict[int, list[int]] = {}
            for p in policies:
                fams = {r[0] for side in ("src", "dst") for r in p.ranges.get(side, [])
                        if r[0] in _SHIFT}  # fmt: skip
                if fams and all(p.id in per_fam[f] for f in fams):
                    out[p.id] = sorted({x for f in fams for x in per_fam[f][p.id]})
            cache[key] = out
        return cache[key]

    def labels(self, ids: list[int]) -> str:
        """„Policy 3 „name“, Policy 7“ - die ueberdeckenden in Listen-Reihenfolge."""
        pol = self.index["pol"]
        names = [policy_check._label({"external_id": pol[i][0].external_id, "name": pol[i][0].name})
                 for i in ids[:SHOWN] if i in pol]  # fmt: skip
        return ", ".join(names) + (f" +{len(ids) - SHOWN}" if len(ids) > SHOWN else "")


def for_tenant(session: Session, tenant_id: int, dns=None) -> Matcher:
    """Zuordnung des Mandanten mit dem Wege-Speicher seines Datenstands (ADR 0048/0049)."""
    from app.store import path_store

    store = path_store.current(tenant_id) or path_store.store_for(session, tenant_id)
    checker = policy_check.Checker(session, tenant_id)
    checker.share(store.checker)
    return Matcher(session, tenant_id, checker, store.checker.setdefault("_match", {}), dns)
