"""Absicht je Dienst (0.57.0, ADR 0024 Nr. 13): „eingeschraenkt“ = nur bestimmte Dienste.

Je Zonenpaar mit der Absicht „eingeschraenkt“ kann der Operator die erlaubten Dienste nennen:
`tcp/443`, `udp/53`, `tcp/8000-8080`, `tcp` (alle TCP-Ports), `icmp`, `any` bzw. den Namen
eines Service-Objekts oder ACI-Filters (`FT_https`, aufgeloest wie in der Weg-Suche). Eine
erlaubende Regel zwischen den Zonen, die einen anderen Dienst oeffnet, verstoesst gegen die
Absicht (Zonenmatrix, Befund `zone_policy_violation`).

- Ein Dienst der Regel ist **abgedeckt**, wenn ein erlaubter Dienst desselben Protokolls seinen
  ganzen Portbereich enthaelt (`tcp/1-65535` nur durch `tcp` bzw. `any`), ICMP durch `icmp`,
  ein Service-Objekt auch ueber seinen Namen. `ALL` bzw. ein negierter Dienst der Regel ist nur
  durch `any` abgedeckt.
- Regeln ohne gelesene Dienste werden nicht beurteilt.

Nur lesend; die Dienste pflegt der Operator in der Zonenmatrix.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Proto, Rule, ServiceObject
from app.models.rule import rule_service
from app.store import resolve

MAX = 50  # Dienste je Zonenpaar
ALL_PORTS = (1, 65535)
_ANY = ("any", "all", "alle", "alles", "*")
_ICMP = ("icmp", "icmp6", "icmpv6", "ping")

Service = tuple[str, int | None, int | None]  # Protokoll, erster und letzter Port


def parse(text: str | None) -> Service | None:
    """`tcp/443`, `tcp/8000-8080`, `udp`, `icmp`, `any` -> (Protokoll, von, bis); sonst None
    (dann ist es der Name eines Service-Objekts)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in _ANY:
        return ("any", None, None)
    proto, _, ports = t.partition("/")
    if proto in _ICMP and not ports:
        return ("icmp", None, None)
    if proto not in ("tcp", "udp"):
        return None
    if not ports:
        return (proto, *ALL_PORTS)
    a, _, b = ports.partition("-")
    if not a.isdigit() or (b and not b.isdigit()):
        return None
    lo, hi = int(a), int(b or a)
    if not 0 < lo <= hi < 65536:
        return None
    return (proto, lo, hi)


def normalize(session: Session, tenant_id: int, values: list[str] | None) -> list[str] | None:
    """Eingabe pruefen: jeder Eintrag ein Dienst bzw. ein bekanntes Service-Objekt (ValueError
    sonst); doppelte entfernt, leer = None."""
    out: list[str] = []
    for raw in values or []:
        v = (raw or "").strip()
        if not v or v.casefold() in (x.casefold() for x in out):
            continue
        if parse(v) is None and resolve.service_values(session, tenant_id, v) is None:
            raise ValueError(f"Dienst „{v}“: tcp/443, udp/53, tcp/8000-8080, icmp, any bzw. "
                             "der Name eines Service-Objekts")  # fmt: skip
        out.append(v[:100])
    if len(out) > MAX:
        raise ValueError(f"höchstens {MAX} Dienste je Zonenpaar")
    return out or None


@dataclass
class Allowed:
    """Erlaubte Dienste eines Zonenpaars (aufgeloest)."""

    texts: list[str]
    items: list[Service] = field(default_factory=list)
    names: set[str] = field(default_factory=set)

    @property
    def anything(self) -> bool:
        return any(p == "any" for p, _, _ in self.items)

    def covers(self, proto: str, lo: int | None, hi: int | None, name: str | None) -> bool:
        """Deckt die Absicht diesen Dienst der Regel ganz ab?"""
        if self.anything or (name and name.strip().lower() in self.names):
            return True
        if proto in ("tcp", "udp"):
            lo, hi = lo or ALL_PORTS[0], hi or ALL_PORTS[1]
            return any(p == proto and a <= lo and hi <= b for p, a, b in self.items)
        if proto in ("icmp", "icmpv6"):
            return any(p == "icmp" for p, _, _ in self.items)
        return False


def allowed(session: Session, tenant_id: int, texts: list[str]) -> Allowed:
    """Dienste einer Absicht aufloesen: Angaben direkt, Service-Objekte ueber ihre Werte."""
    out = Allowed(list(texts))
    for t in texts:
        parsed = parse(t)
        if parsed is not None:
            out.items.append(parsed)
            continue
        out.names.add(t.strip().lower())
        for v in resolve.service_values(session, tenant_id, t) or []:
            if (p := parse(v)) is not None:
                out.items.append(p)
    return out


@dataclass
class RuleServices:
    """Dienste je Regel (aus den Service-Objekten der Regel), fuer die Pruefung je Zonenpaar."""

    by_rule: dict[int, list[tuple[str, int | None, int | None, str]]]
    negated: set[int]

    def outside(self, rid: int, allow: Allowed) -> list[str] | None:
        """Dienste der Regel, die die Absicht nicht abdeckt (None = nicht beurteilbar)."""
        services = self.by_rule.get(rid)
        if not services:
            return None
        if rid in self.negated:
            return [] if allow.anything else ["alle ausser " + ", ".join(s[3] for s in services)]
        out = []
        for proto, lo, hi, name in services:
            if name.strip().lower() in ("all", "any"):
                if not allow.anything:
                    out.append("ALL")
                continue
            if not allow.covers(proto, lo, hi, name):
                out.append(label(proto, lo, hi, name))
        return list(dict.fromkeys(out))


def outside_of(names: list[str], allow: Allowed) -> list[str] | None:
    """Wie `RuleServices.outside` fuer Dienste als Text - die Rueckrichtung eines ACI-Contracts
    (`ip/any`, `tcp/any`, `icmp/any`; 0.69.1)."""
    services: list[tuple[str, int | None, int | None, str]] = []
    for name in names:
        proto, _, port = (name or "").lower().partition("/")
        if proto in ("ip", "all", "unspecified"):
            services.append(("all", None, None, "ALL"))
        elif (parsed := parse(proto if port in ("", "any") else name)) is not None:
            services.append((*parsed, name))
    return RuleServices({0: services}, set()).outside(0, allow)


def label(proto: str, lo: int | None, hi: int | None, name: str | None) -> str:
    if proto in ("tcp", "udp") and lo:
        ports = f"{lo}" if not hi or hi == lo else f"{lo}-{hi}"
        text = f"{proto}/{ports}"
    else:
        text = proto
    return f"{name} ({text})" if name and name.lower() != text else text


def rule_services(session: Session, rule_ids) -> RuleServices:
    ids = list(rule_ids)
    out = RuleServices({}, set())
    if not ids:
        return out
    for rid, proto, start, end, name in session.execute(
        select(
            rule_service.c.rule_id,
            ServiceObject.proto,
            ServiceObject.port_start,
            ServiceObject.port_end,
            ServiceObject.canonical_name,
        )
        .join(ServiceObject, ServiceObject.id == rule_service.c.service_object_id)
        .where(rule_service.c.rule_id.in_(ids))
    ):
        value = proto.value if isinstance(proto, Proto) else str(proto)
        out.by_rule.setdefault(rid, []).append((value, start, end, name or value))
    for rid, raw in session.execute(select(Rule.id, Rule.raw).where(Rule.id.in_(ids))):
        if isinstance(raw, dict) and raw.get("service-negate") == "enable":
            out.negated.add(rid)
    return out
