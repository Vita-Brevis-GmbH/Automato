"""IPv4/IPv6-Regelpaare erkennen (Dev-Befund: FortiGate fuehrt je Familie eigene Policies, die
meist dasselbe abdecken - gleiche Objektnamen, Services und Interfaces, mit Ausnahmen).

Nur zur Orientierung: beide bleiben eigene Regeln (wie auf der FortiGate), die Liste markiert
das Gegenstueck. `same` = Quelle, Ziel, Services, Interfaces und Aktion gleich (Objektnamen;
IPv4- und IPv6-Objekt heissen gleich); `similar` = gleicher Regelname (ohne IPv4/IPv6-Kennung)
oder nur eine Seite weicht ab - die Abweichungen stehen in `diff`. Reine Funktion.
"""

import re
from dataclasses import dataclass, field

_FAMILY_TOKEN = re.compile(r"(?:^|[-_ .])(?:ipv?[46]|v[46])(?=$|[-_ .])")


@dataclass(frozen=True)
class PairInfo:
    rule_id: int
    source_id: int
    family: str  # v4 | v6
    action: str
    name: str
    position: int  # Reihenfolge auf der Firewall
    src_zone: int | None = None
    dst_zone: int | None = None
    src: frozenset[str] = field(default_factory=frozenset)
    dst: frozenset[str] = field(default_factory=frozenset)
    service: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Pair:
    partner_id: int
    match: str  # same | similar
    diff: tuple[str, ...]


def bare_name(name: str) -> str:
    """Regelname ohne IPv4/IPv6-Kennung: `web-in-v6`, `web_in_IPv6` -> `web-in`."""
    return _FAMILY_TOKEN.sub("", name.lower()).strip("-_ .")


def _score(a: PairInfo, b: PairInfo) -> tuple[int, tuple[str, ...]] | None:
    if a.action != b.action:
        return None
    diff = tuple(
        aspect
        for aspect, x, y in (
            ("src", a.src, b.src),
            ("dst", a.dst, b.dst),
            ("service", a.service, b.service),
        )
        if x != y
    )
    same_zone = (a.src_zone, a.dst_zone) == (b.src_zone, b.dst_zone)
    same_name = bool(bare_name(a.name)) and bare_name(a.name) == bare_name(b.name)
    if not same_zone and not same_name:
        return None
    if not same_zone:
        diff = ("zone", *diff)
    if not same_name:
        diff = (*diff, "name")
    if same_zone and not any(d in ("src", "dst", "service") for d in diff):
        return 100 + (10 if same_name else 0), diff
    if same_name or len([d for d in diff if d != "name"]) <= 1:
        return 50 - 10 * len(diff) + (10 if same_name else 0), diff
    return None


def pair(rules: list[PairInfo]) -> dict[int, Pair]:
    """Gegenstuecke je Regel-ID (beidseitig). Je Firewall; jede Regel hoechstens ein Paar -
    zuerst die gleichen, dann die aehnlichsten, bei Gleichstand die naechstgelegene."""
    out: dict[int, Pair] = {}
    by_source: dict[int, list[PairInfo]] = {}
    for r in rules:
        by_source.setdefault(r.source_id, []).append(r)
    for items in by_source.values():
        v4 = [r for r in items if r.family == "v4"]
        v6 = [r for r in items if r.family == "v6"]
        if not v4 or not v6:
            continue
        by_key: dict[tuple, list[PairInfo]] = {}
        by_name: dict[str, list[PairInfo]] = {}
        for r in v4:
            by_key.setdefault((r.action, r.src_zone, r.dst_zone), []).append(r)
            by_name.setdefault(bare_name(r.name), []).append(r)
        candidates = []
        for b in v6:
            seen: set[int] = set()
            pool = by_key.get((b.action, b.src_zone, b.dst_zone), [])
            pool = pool + by_name.get(bare_name(b.name), []) if bare_name(b.name) else pool
            for a in pool:
                if a.rule_id in seen:
                    continue
                seen.add(a.rule_id)
                scored = _score(a, b)
                if scored is not None:
                    score, diff = scored
                    candidates.append((-score, abs(a.position - b.position), a, b, diff))
        candidates.sort(key=lambda c: (c[0], c[1], c[2].rule_id, c[3].rule_id))
        for neg, _, a, b, diff in candidates:
            if a.rule_id in out or b.rule_id in out:
                continue
            match = "same" if -neg >= 100 else "similar"
            out[a.rule_id] = Pair(b.rule_id, match, diff)
            out[b.rule_id] = Pair(a.rule_id, match, diff)
    return out
