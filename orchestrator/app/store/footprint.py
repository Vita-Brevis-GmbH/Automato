"""Fussabdruck eines Stands der Doku - und ob eine Aenderung der Daten ihn betrifft (0.141.0,
ADR 0049).

Anwender 2026-11-06: „Die Berechnungen nur anstossen, wenn was geändert hat, also nur die Wege
berechnen, welche von der geänderten Firewall Regel betroffen sind.“

Beim Speichern eines Stands (Doku bzw. Verbindungen) haelt `of` fest, was er benutzt - aus dem
Ergebnis selbst, ohne die Berechnung zu aendern:

- `rules`: Regeln und Contracts, die er zeigt bzw. auf seinen Wegen prueft (Kennungen bleiben je
  Regel gleich, `stable_ids`);
- `devices`: Quellsysteme seiner Hops und Regeln;
- `epgs`: EPGs (DN) der Applikation, ihrer Gegenstellen und Contracts;
- `members`: Adressen der eigenen Systeme (EPG-Subnetze, Server, VMs, Werte der Treffer);
- `addrs`: alle Adressen im Ergebnis (Gegenstellen, NAT, Next-Hops), ohne sehr grosse Netze;
- `pairs` (Verbindungen): je Verbindung Quelle und Ziel samt den Adressen ihrer Hops.

`affected` prueft eine Aenderung (`DataChange`) dagegen - im Zweifel betroffen:

- jede Applikation: Aenderung mit `all`, fehlender Fussabdruck;
- Geraet: NAT, Routen, Policy-Routen eines Geraets, das der Stand nutzt;
- Adressen bzw. EPGs: geaenderte Objekte, VIPs, VMs, Endpunkte mit einer Adresse des Stands;
- Regel: der Stand zeigt bzw. prueft sie (auch weggefallene); ihre Bereiche treffen ein eigenes
  System (neuer Treffer der Doku); sie nennt eine EPG des Stands; sie liegt auf einem Geraet des
  Stands und passt zu Quelle und Ziel einer Verbindung (Urteil je Hop). Regeln mit any auf einer
  Seite: jede Applikation, deren Wege ueber ihr Geraet gehen.
"""

import ipaddress
import json
import re
import zlib
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Epg, Rule, RuleEndpoint, RuleIpRange
from app.store import data_state

VERSION = 1
CONNECTIONS = "connections"
_IP = re.compile(
    r"(?<![\w.:])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\w.])"
    r"|(?<![\w:.])(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?:/\d{1,3})?(?![\w:])"
)
_NODE = re.compile(r"^(\d+)\|")
_RULE_LISTS = ("rule_ids", "helper_for", "firewall_rules")
_WIDE = {4: 8, 6: 16}  # kuerzere Praefixe zaehlen nicht als Adresse des Stands (any, 0/0, /4)
_CHUNK = 500


# -- Bereiche ------------------------------------------------------------------------------


Span = tuple[int, int, int]  # Familie, von, bis


def _spans(text: str) -> list[Span]:
    out = []
    for m in _IP.findall(text):
        try:
            net = ipaddress.ip_network(m, strict=False)
        except ValueError:
            continue
        out.append((net.version, int(net.network_address), int(net.broadcast_address)))
    return out


def _wide(s: Span) -> bool:
    fam, lo, hi = s
    bits = 32 if fam == 4 else 128
    return hi - lo + 1 > (1 << (bits - _WIDE[fam]))


class Ranges:
    """Bereiche je Familie, zusammengefasst und sortiert - Schnitt per Suche."""

    def __init__(self, spans) -> None:
        by: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for fam, lo, hi in spans:
            by[fam].append((lo, hi))
        self.by: dict[int, tuple[list[int], list[int]]] = {}
        for fam, items in by.items():
            items.sort()
            los: list[int] = []
            his: list[int] = []
            for lo, hi in items:
                if his and lo <= his[-1] + 1:
                    his[-1] = max(his[-1], hi)
                else:
                    los.append(lo)
                    his.append(hi)
            self.by[fam] = (los, his)

    def __bool__(self) -> bool:
        return bool(self.by)

    def hits(self, fam: int, lo: int, hi: int) -> bool:
        found = self.by.get(fam)
        if not found:
            return False
        los, his = found
        i = bisect_right(los, hi) - 1
        return i >= 0 and his[i] >= lo

    def meets(self, other: "Ranges") -> bool:
        return any(self.hits(fam, lo, hi) for fam, (los, his) in other.by.items()
                   for lo, hi in zip(los, his, strict=True))  # fmt: skip

    def out(self) -> list[str]:
        return [f"{fam}:{lo:x}-{hi:x}" for fam, (los, his) in sorted(self.by.items())
                for lo, hi in zip(los, his, strict=True)]  # fmt: skip

    @classmethod
    def read(cls, items) -> "Ranges":
        return cls(data_state.parse(r) for r in items or [])


# -- Fussabdruck ---------------------------------------------------------------------------


@dataclass
class _Acc:
    rules: set[int] = field(default_factory=set)
    devices: set[int] = field(default_factory=set)
    epgs: set[str] = field(default_factory=set)
    spans: list[Span] = field(default_factory=list)


def _walk(obj, acc: _Acc) -> None:
    if isinstance(obj, dict):
        if (
            isinstance(obj.get("id"), int)
            and isinstance(obj.get("source_system_id"), int)
            and ("external_id" in obj or "action" in obj)
        ):
            acc.rules.add(obj["id"])  # Regel in der Liste der Doku
        for k, v in obj.items():
            if k == "rule_id" and isinstance(v, int):
                acc.rules.add(v)
            elif k in _RULE_LISTS and isinstance(v, list):
                acc.rules.update(x for x in v if isinstance(x, int))
            elif k == "source_system_id" and isinstance(v, int):
                acc.devices.add(v)
            elif k == "key" and isinstance(v, str) and (m := _NODE.match(v)):
                acc.devices.add(int(m.group(1)))
            _walk(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, acc)
    elif isinstance(obj, str):
        if obj.startswith("uni/"):
            acc.epgs.add(obj)
        elif any(c.isdigit() for c in obj) and ("." in obj or ":" in obj):
            acc.spans.extend(_spans(obj))


def _spans_of(obj) -> list[Span]:
    acc = _Acc()
    _walk(obj, acc)
    return acc.spans


def _members(kind: str, result: dict) -> list[Span]:
    """Adressen der eigenen Systeme: EPGs, Systeme und Werte der Treffer (Doku) bzw. Mitglieder
    und eigene Enden (Verbindungen)."""
    out: list[Span] = []
    if kind == CONNECTIONS:
        out += _spans_of(result.get("members"))
        for c in result.get("connections") or []:
            for end in ("src", "dst"):
                e = c.get(end) or {}
                if e.get("kind") == "system":
                    out += _spans_of(e)
    else:
        out += _spans_of(result.get("systems"))
        out += _spans_of(result.get("epgs"))
        for h in result.get("hits") or []:
            out += _spans_of(h.get("value"))
    return out


def _pairs(result: dict) -> list[list[list[str]]]:
    """Je Verbindung [Quelle, Ziel] als Bereiche - mit den Adressen ihrer Hops (NAT) auf beiden
    Seiten; ohne Adresse (any) = leer (passt zu allem)."""
    seen: set[str] = set()
    out = []
    for c in result.get("connections") or []:
        hops = _spans_of([c.get("hops"), c.get("paths"), c.get("return")])
        sides = []
        for end in ("src", "dst"):
            own = _spans_of(c.get(end))
            sides.append([] if not own or any(_wide(s) for s in own) else Ranges(own + hops).out())
        key = json.dumps(sides)
        if key not in seen:
            seen.add(key)
            out.append(sides)
    return out


def of(kind: str, result: dict) -> bytes:
    """Fussabdruck eines Ergebnisses (gepackt)."""
    acc = _Acc()
    _walk(result, acc)
    fp = {
        "v": VERSION,
        "kind": kind,
        "rules": sorted(acc.rules),
        "devices": sorted(acc.devices),
        "epgs": sorted(acc.epgs),
        "members": Ranges(_members(kind, result)).out(),
        "addrs": Ranges(s for s in acc.spans if not _wide(s)).out(),
    }
    if kind == CONNECTIONS:
        fp["pairs"] = _pairs(result)
    return zlib.compress(json.dumps(fp, separators=(",", ":")).encode())


def read(data: bytes | None) -> dict | None:
    if not data:
        return None
    try:
        fp = json.loads(zlib.decompress(data))
    except (ValueError, zlib.error):
        return None
    return fp if fp.get("v") == VERSION else None


# -- Betroffen? ----------------------------------------------------------------------------


@dataclass
class _Rule:
    sid: int
    src: Ranges | None  # None = any (bzw. ohne Bereiche)
    dst: Ranges | None
    epgs: set[str]


class Context:
    """Die geaenderten Regeln im aktuellen Stand (Bereiche, EPGs) - einmal je Pruefung geladen."""

    def __init__(self, session: Session, changes) -> None:
        ids = sorted({r for c in changes for r in c.rules or []})
        self.rules: dict[int, _Rule] = {}
        spans: dict[tuple[int, str], list[Span]] = defaultdict(list)
        anys: set[tuple[int, str]] = set()
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i : i + _CHUNK]
            sids = dict(session.execute(select(Rule.id, Rule.source_system_id)
                                        .where(Rule.id.in_(chunk))).all())  # fmt: skip
            for rid, role, fam, lo, hi, any_ in session.execute(
                select(RuleIpRange.rule_id, RuleIpRange.role, RuleIpRange.family,
                       RuleIpRange.lo, RuleIpRange.hi, RuleIpRange.is_any)
                .where(RuleIpRange.rule_id.in_(chunk))
            ):  # fmt: skip
                if any_:
                    anys.add((rid, role))
                    continue
                try:
                    spans[(rid, role)].append((int(fam), int(lo, 16), int(hi, 16)))
                except (TypeError, ValueError):
                    anys.add((rid, role))
            epgs: dict[int, set[str]] = defaultdict(set)
            for rid, dn in session.execute(
                select(RuleEndpoint.rule_id, Epg.dn)
                .join(Epg, Epg.id == RuleEndpoint.epg_id)
                .where(RuleEndpoint.rule_id.in_(chunk), Epg.dn.is_not(None))
            ):  # fmt: skip
                epgs[rid].add(dn)
            for rid, sid in sids.items():
                sides = []
                for role in ("src", "dst"):
                    found = spans.get((rid, role))
                    sides.append(None if (rid, role) in anys or not found else Ranges(found))
                self.rules[rid] = _Rule(sid, sides[0], sides[1], epgs.get(rid, set()))


class Print:
    """Gelesener Fussabdruck eines Stands (Bereiche einmal aufbereitet)."""

    def __init__(self, fp: dict, devices: set[int]) -> None:
        self.kind = fp.get("kind")
        self.rules = set(fp.get("rules") or [])
        self.devices = set(fp.get("devices") or []) | devices  # alle Staende der Applikation
        self.epgs = set(fp.get("epgs") or [])
        self.members = Ranges.read(fp.get("members"))
        self.addrs = Ranges.read([*(fp.get("addrs") or []), *(fp.get("members") or [])])
        self.pairs = [(Ranges.read(s) if s else None, Ranges.read(d) if d else None)
                      for s, d in fp.get("pairs") or []]  # fmt: skip


def _side(rule: Ranges | None, conn: Ranges | None) -> bool:
    return rule is None or conn is None or rule.meets(conn)


def affected(p: Print | None, change, ctx: Context) -> tuple[bool, str]:
    """Betrifft die Aenderung den Stand? -> (ja/nein, Grund)."""
    if p is None:
        return True, "ohne Fussabdruck"
    if change.all:
        return True, "alle"
    if set(change.devices or []) & p.devices:
        return True, "Geraet"
    if set(change.epgs or []) & p.epgs:
        return True, "EPG"
    if change.ips and Ranges.read(change.ips).meets(p.addrs):
        return True, "Adresse"
    for rid in change.rules or []:
        if rid in p.rules:
            return True, f"Regel {rid}"
        r = ctx.rules.get(rid)
        if r is None:
            continue  # weggefallen und vom Stand nicht benutzt
        if r.epgs & p.epgs:
            return True, f"Regel {rid} (EPG)"
        if any(side is not None and side.meets(p.members) for side in (r.src, r.dst)):
            return True, f"Regel {rid} (eigenes System)"
        if r.sid in p.devices:
            if r.src is None or r.dst is None:
                if p.kind != CONNECTIONS or any(_side(r.src, s) and _side(r.dst, d)
                                                for s, d in p.pairs):  # fmt: skip
                    return True, f"Regel {rid} (any auf dem Weg)"
            elif p.kind == CONNECTIONS and any(_side(r.src, s) and _side(r.dst, d)
                                               for s, d in p.pairs):  # fmt: skip
                return True, f"Regel {rid} (Urteil auf dem Weg)"
    return False, ""
