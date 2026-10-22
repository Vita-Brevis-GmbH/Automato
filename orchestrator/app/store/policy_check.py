"""Regeln auf dem Weg (0.22.0, ADR 0019 Stufe 3 „Regel-Relevanz je Hop“).

Je FortiGate-Hop eines Wegs (Firewall bzw. VDOM): welche Policies passen auf Eingang und
Ausgang (Interface bzw. Zone, `any`), Quelle, Ziel und - falls angegeben - den Dienst, in der
Reihenfolge der Firewall. Wie auf der FortiGate entscheidet die erste passende Policy.

- **sicher:** alle Bedingungen erfuellt - die Policy entscheidet (erlaubt bzw. gesperrt).
- **moeglich:** keine Bedingung verletzt, aber eine nicht pruefbar - Quelle oder Interface
  unbekannt, FQDN/ISDB/Geo, Benutzer/Gruppen, Zeitplan, Dienst nicht angegeben. Sie kann vor
  der entscheidenden greifen; haben die moeglichen eine andere Aktion, ist der Hop „abhaengig“.
- **keine passende Policy:** implizit gesperrt.
- Abgeschaltete Policies zaehlen nicht.

Grenzen: NAT (VIP, SNAT) aendert die Adressen auf dem Weg nicht; ACI-Contracts werden hier
nicht geprueft. Nur lesend - es zaehlen die gelesenen Policies.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    InvInterface,
    NetworkObject,
    Proto,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleIpRange,
    ServiceObject,
    Zone,
)
from app.models.rule import EndpointKind, rule_service
from app.store.rule_index import value_range

FIREWALL_KINDS = ("firewall", "vdom")
SHOWN = 12  # moegliche Policies je Hop in der Anzeige
_OPEN = ("", "always")  # Zeitplan ohne Einschraenkung


def parse_service(text: str | None) -> tuple[str, int | None] | None:
    """„tcp/443“, „udp/53“, „tcp“, „icmp“ -> (Protokoll, Port); sonst ValueError."""
    if not text or not text.strip():
        return None
    proto, _, port = text.strip().lower().partition("/")
    if proto in ("icmp", "icmp6", "icmpv6", "ping") and not port:
        return ("icmp", None)
    if proto in ("tcp", "udp"):
        if not port:
            return (proto, None)
        if port.isdigit() and 0 < int(port) < 65536:
            return (proto, int(port))
    raise ValueError("Dienst: tcp/443, udp/53, tcp oder icmp")


@dataclass
class _Policy:
    id: int
    external_id: str
    name: str
    allow: bool
    enabled: bool = True
    srcintf: list[str] = field(default_factory=list)
    dstintf: list[str] = field(default_factory=list)
    negate: dict[str, bool] = field(default_factory=dict)
    identity: bool = False
    schedule: str | None = None
    service_negate: bool = False
    services: list[tuple[Proto, int | None, int | None, str]] = field(default_factory=list)
    ranges: dict[str, list[tuple[int, int, int, bool, str | None]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    unresolved: dict[str, bool] = field(default_factory=lambda: defaultdict(bool))
    endpoints: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _names(raw: dict, key: str) -> list[str]:
    return [str(e.get("name")) for e in raw.get(key) or [] if isinstance(e, dict) and e.get("name")]


class Checker:
    """Laedt die Policies je Firewall/VDOM einmal und prueft sie je Hop."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        self._policies: dict[tuple[int, str], list[_Policy]] = {}
        self._known: dict[int, set[str]] = {}

    # -- Laden -------------------------------------------------------------------

    def policies(self, sid: int, vdom: str) -> list[_Policy]:
        key = (sid, vdom)
        if key in self._policies:
            return self._policies[key]
        where = (
            Rule.source_system_id == sid,
            Rule.domain == RuleDomain.FIREWALL,
            or_(Rule.vdom == vdom, Rule.vdom.is_(None)),
        )
        rows = self.session.execute(
            select(
                Rule.id,
                Rule.external_id,
                Rule.name,
                Rule.action,
                Rule.raw,
                Rule.src_zone_id,
                Rule.dst_zone_id,
            )
            .where(*where)
            .order_by(Rule.sequence, Rule.id)
        ).all()
        zones = {
            z.id: z.name
            for z in self.session.scalars(
                select(Zone).where(
                    Zone.id.in_({z for r in rows for z in (r.src_zone_id, r.dst_zone_id) if z})
                )
            )
        }
        out: dict[int, _Policy] = {}
        for r in rows:
            raw = r.raw if isinstance(r.raw, dict) else {}
            p = _Policy(r.id, r.external_id, r.name or "", r.action is RuleAction.ALLOW)
            p.enabled = str(raw.get("status") or "enable") != "disable"
            # Interfaces bzw. Zonen der Policy; ohne Rohdaten die (erste) Zone der Regel
            p.srcintf = _names(raw, "srcintf") or [z for z in [zones.get(r.src_zone_id)] if z]
            p.dstintf = _names(raw, "dstintf") or [z for z in [zones.get(r.dst_zone_id)] if z]
            p.negate = {
                "src": raw.get("srcaddr-negate") == "enable",
                "dst": raw.get("dstaddr-negate") == "enable",
            }
            p.service_negate = raw.get("service-negate") == "enable"
            p.identity = any(raw.get(k) for k in ("users", "groups", "fsso-groups"))
            p.schedule = str(raw.get("schedule") or "") or None
            out[r.id] = p
        if out:
            self._load_details(where, out)
        self._policies[key] = list(out.values())
        return self._policies[key]

    def _load_details(self, where: tuple, out: dict[int, _Policy]) -> None:
        for rid, role, fam, lo, hi, is_any, via in self.session.execute(
            select(
                RuleIpRange.rule_id,
                RuleIpRange.role,
                RuleIpRange.family,
                RuleIpRange.lo,
                RuleIpRange.hi,
                RuleIpRange.is_any,
                RuleIpRange.via,
            )
            .join(Rule, Rule.id == RuleIpRange.rule_id)
            .where(*where, RuleIpRange.role.in_(("src", "dst")))
        ):
            out[rid].ranges[role].append((fam, int(lo, 16), int(hi, 16), is_any, via))
        for rid, role, kind, inline, value, epg_id in self.session.execute(
            select(
                RuleEndpoint.rule_id,
                RuleEndpoint.role,
                RuleEndpoint.kind,
                RuleEndpoint.inline_value,
                NetworkObject.value,
                RuleEndpoint.epg_id,
            )
            .join(Rule, Rule.id == RuleEndpoint.rule_id)
            .outerjoin(NetworkObject, NetworkObject.id == RuleEndpoint.network_object_id)
            .where(*where)
        ):
            p, side = out[rid], role.value
            p.endpoints[side] += 1
            if kind is EndpointKind.ANY or epg_id or value_range(value) or value_range(inline):
                continue
            p.unresolved[side] = True  # FQDN, ISDB, Geo, dynamisch: nicht als IP pruefbar
        for rid, proto, start, end, name in self.session.execute(
            select(
                rule_service.c.rule_id,
                ServiceObject.proto,
                ServiceObject.port_start,
                ServiceObject.port_end,
                ServiceObject.canonical_name,
            )
            .join(ServiceObject, ServiceObject.id == rule_service.c.service_object_id)
            .join(Rule, Rule.id == rule_service.c.rule_id)
            .where(*where)
        ):
            out[rid].services.append((proto, start, end, name))

    def known(self, sid: int) -> set[str]:
        """Interface- und Zonennamen der Firewall (unbekannte Namen = nicht pruefbar)."""
        if sid not in self._known:
            names: set[str] = set()
            for name, zone in self.session.execute(
                select(InvInterface.name, InvInterface.zone).where(
                    InvInterface.source_system_id == sid
                )
            ):
                names |= {n for n in (name, zone) if n}
            self._known[sid] = names
        return self._known[sid]

    # -- Pruefen -----------------------------------------------------------------

    def check(self, sid: int, vdom: str, ingress: dict | None, egress: dict | None,
              src, dst, service: tuple[str, int | None] | None) -> dict:  # fmt: skip
        """Urteil je Hop: allow | deny | implicit_deny | depends | unknown, mit der
        entscheidenden Policy und den moeglichen davor."""
        policies = self.policies(sid, vdom)
        if not policies:
            return {"verdict": "unknown", "text": "keine Policies gelesen", "rules": []}
        known = self.known(sid)
        shown: list[dict] = []
        decisive = None
        for p in policies:
            if not p.enabled:
                continue
            checks = {
                "Eingang": _ports(p.srcintf, ingress, known),
                "Ausgang": _ports(p.dstintf, egress, known),
            }
            src_ok, src_via = _address(p, "src", src)
            dst_ok, dst_via = _address(p, "dst", dst)
            checks["Quelle"], checks["Ziel"] = src_ok, dst_ok
            checks["Dienst"] = _service(p, service)
            checks["Benutzer/Gruppen"] = None if p.identity else True
            checks[f"Zeitplan {p.schedule}"] = (
                None if (p.schedule or "").lower() not in _OPEN else True
            )
            if False in checks.values():
                continue
            unsure = [k for k, v in checks.items() if v is None]
            entry = {
                "rule_id": p.id,
                "external_id": p.external_id,
                "name": p.name,
                "action": "allow" if p.allow else "deny",
                "src": src_via,
                "dst": dst_via,
                "srcintf": p.srcintf,
                "dstintf": p.dstintf,
                "services": [s[3] for s in p.services][:4],
                "unsure": unsure,
                "decides": not unsure,
            }
            if unsure:
                if len(shown) < SHOWN:
                    shown.append(entry)
                continue
            shown.append(entry)
            decisive = entry
            break
        return _verdict(decisive, shown)


def _verdict(decisive: dict | None, shown: list[dict]) -> dict:
    before = [e for e in shown if not e["decides"]]
    reasons = sorted({u for e in before for u in e["unsure"]})
    why = f" – abhängig von: {', '.join(reasons)}" if reasons else ""
    if decisive is not None:
        name = _label(decisive)
        other = [e for e in before if e["action"] != decisive["action"]]
        if other:
            return {"verdict": "depends", "rules": shown,
                    "text": f"{_label(other[0])} kann vor {name} greifen{why}"}  # fmt: skip
        verb = "erlaubt" if decisive["action"] == "allow" else "gesperrt"
        return {"verdict": decisive["action"], "rules": shown, "text": f"{verb} durch {name}"}
    if before:
        return {"verdict": "depends", "rules": shown,
                "text": f"nur mögliche Policies, sonst implizit gesperrt{why}"}  # fmt: skip
    return {"verdict": "implicit_deny", "rules": [],
            "text": "keine passende Policy – implizit gesperrt"}  # fmt: skip


def _label(e: dict) -> str:
    return f"Policy {e['external_id']}" + (f" „{e['name']}“" if e["name"] else "")


def _ports(names: list[str], port: dict | None, known: set[str]) -> bool | None:
    """Interface bzw. Zone der Policy passt zum Ein- bzw. Ausgang des Hops."""
    if not names:
        return None
    if any(n.lower() == "any" for n in names):
        return True
    have = {v for v in ((port or {}).get("interface"), (port or {}).get("zone")) if v}
    if not have:
        return None
    if have & set(names):
        return True
    return False if all(n in known for n in names) else None


def _address(p: _Policy, role: str, ip) -> tuple[bool | None, str | None]:
    """Quelle bzw. Ziel: (passt, ueber welches Objekt); negiert umgekehrt."""
    ranges = p.ranges.get(role, [])
    if ip is None:  # Quelle unbekannt (Start gewaehlt)
        if not p.negate.get(role) and any(r[3] for r in ranges):
            return True, "any"
        return None, None
    value = int(ip)
    hits = [r for r in ranges if r[0] == ip.version and r[1] <= value <= r[2]]
    best = min(hits, key=lambda r: r[2] - r[1]) if hits else None
    inside = best is not None
    if p.negate.get(role):
        if inside:
            return (None, None) if p.unresolved.get(role) else (False, None)
        return (None, None) if p.unresolved.get(role) else (True, "negiert")
    if inside:
        return True, "any" if best[3] else (best[4] or "Netz")
    if p.unresolved.get(role) or not p.endpoints.get(role):
        return None, None
    return False, None


def _service(p: _Policy, want: tuple[str, int | None] | None) -> bool | None:
    """Dienst der Policy passt zum angegebenen (ohne Angabe nur `ALL` sicher)."""
    if not p.services:
        return None
    results: list[bool | None] = []
    for proto, start, end, name in p.services:
        if name == "all":
            results.append(True)
        elif want is None:
            results.append(None)
        elif proto in (Proto.TCP, Proto.UDP):
            if proto.value != want[0]:
                results.append(False)
            elif want[1] is None:  # nur Protokoll: sicher nur bei allen Ports
                results.append(True if (start or 0) <= 1 and (end or 0) >= 65535 else None)
            else:
                results.append((start or 0) <= want[1] <= (end or 65535))
        elif proto in (Proto.ICMP, Proto.ICMPV6) or name.startswith("icmp"):
            results.append(want[0] == "icmp")
        else:
            results.append(None)  # Name ohne Definition, Internet-Service
    ok = True if True in results else (None if None in results else False)
    if p.service_negate and ok is not None:
        return not ok
    return None if p.service_negate else ok


def check_paths(session: Session, tenant_id: int, paths: list[dict], src, dst, service) -> None:
    """Je Weg und FortiGate-Hop das Urteil (`hop.rules`), dazu je Weg das Gesamturteil
    (`path.policy`): gesperrt am ersten sperrenden Hop, sonst abhaengig bzw. erlaubt."""
    checker = Checker(session, tenant_id)
    cache: dict[tuple, dict] = {}
    for path in paths:
        verdicts = []
        for hop in path["hops"]:
            if hop.get("kind") not in FIREWALL_KINDS:
                continue
            ingress, egress = hop.get("ingress") or {}, hop.get("egress") or {}
            key = (hop["source_system_id"], hop["context"],
                   ingress.get("interface"), ingress.get("zone"),
                   egress.get("interface"), egress.get("zone"))  # fmt: skip
            if key not in cache:
                cache[key] = checker.check(
                    hop["source_system_id"], hop["context"], ingress, egress, src, dst, service
                )
            hop["rules"] = cache[key]
            verdicts.append((hop["label"], cache[key]["verdict"]))
        path["policy"] = _overall(verdicts)


def _overall(verdicts: list[tuple[str, str]]) -> dict | None:
    if not verdicts:
        return None
    for label, v in verdicts:
        if v in ("deny", "implicit_deny"):
            return {"verdict": "deny", "text": f"gesperrt an {label}"}
    open_ = [label for label, v in verdicts if v == "depends"]
    unread = [label for label, v in verdicts if v == "unknown"]
    if open_:
        text = f"abhängig an {', '.join(open_)}"
        if unread:
            text += f"; keine Policies gelesen an {', '.join(unread)}"
        return {"verdict": "depends", "text": text}
    if unread:
        return {"verdict": "unknown", "text": f"keine Policies gelesen an {', '.join(unread)}"}
    n = len(verdicts)
    return {"verdict": "allow", "text": "erlaubt" + (f" an allen {n} Firewalls" if n > 1 else "")}
