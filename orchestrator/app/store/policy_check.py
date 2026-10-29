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

Netz als Quelle bzw. Ziel (0.39.0): sicher nur, wenn ein Objekt der Policy das ganze Netz
enthaelt; erfasst sie nur einen Teil, ist sie „moeglich“ („Quelle nur teilweise“).

NAT (0.23.0): Am Hop mit einer VIP gilt fuer die Policy das urspruengliche Ziel (die VIP) und
- bei Port-Weiterleitung - der interne Port; danach das interne Ziel. SNAT der erlaubenden
Policy (IP-Pool bzw. IP des Ausgangs-Interfaces) ist die Quelle fuer die Firewalls danach.

ACI (0.35.0): je ACI-VRF-Hop die Contracts zwischen den EPGs von Quelle und Ziel
(`store/aci_check.py`), im selben Format. Nur lesend - es zaehlen die gelesenen Policies und
Contracts.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import (
    DeviceObject,
    InvInterface,
    NatRule,
    NetworkObject,
    Proto,
    RefRole,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleIpRange,
    RuleObjectRef,
    ServiceObject,
    Zone,
)
from app.models.rule import EndpointKind, rule_service
from app.store import nat as nat_store
from app.store import rule_view
from app.store.rule_index import value_range
from app.store.topology import endpoint, is_net, is_private, span

FIREWALL_KINDS = ("firewall", "vdom")
ACI_KINDS = ("vrf",)
SHOWN = 12  # moegliche Policies je Hop in der Anzeige
AFTER = 5  # Weg-Suche (0.78.4): passende Policies hinter der entscheidenden
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
    raise ValueError(
        L("Dienst: tcp/443, udp/53, tcp oder icmp", "Service: tcp/443, udp/53, tcp or icmp")
    )


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
    # Geo-Objekte und Internet Services (FortiGuard) - nur oeffentliche Adressen (0.112.0)
    public_only: dict[str, bool] = field(default_factory=lambda: defaultdict(bool))
    # Internet Service statt Adressen je Seite: True = nur FortiGuard-Dienste (oeffentlich),
    # False = auch eigene (`custom`, koennen private Netze enthalten), fehlt = Adressen
    isdb: dict[str, bool] = field(default_factory=dict)
    endpoints: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    snat: list[str] | None = None  # None = kein NAT, [] = IP des Ausgangs, sonst IP-Pools
    snat6: list[str] | None = None  # dasselbe fuer IPv6 (nat6/poolname6, 0.47.0)
    names: list[str] = field(default_factory=list)  # Service-Objekte wie auf der Firewall


def _names(raw: dict, key: str) -> list[str]:
    return [str(e.get("name")) for e in raw.get(key) or [] if isinstance(e, dict) and e.get("name")]


class Checker:
    """Laedt die Policies je Firewall/VDOM einmal und prueft sie je Hop."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        self._policies: dict[tuple[int, str], list[_Policy]] = {}
        self._known: dict[int, set[str]] = {}
        self._ifaces: dict[int, dict[str, list[str]]] = {}
        self._pool_cache: dict[tuple[int, str], dict[str, NatRule]] = {}
        self._vip_cache: dict[tuple[int, str], list[NatRule]] = {}

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
            for side, mid in (("src", "-src"), ("dst", "")):
                if any(raw.get(f"internet-service{v}{mid}") == "enable" for v in ("", "6")):
                    custom = any(raw.get(f"internet-service{v}{mid}-{k}") for v in ("", "6")
                                 for k in ("custom", "custom-group"))  # fmt: skip
                    p.isdb[side] = not custom
            p.identity = any(raw.get(k) for k in ("users", "groups", "fsso-groups"))
            p.schedule = str(raw.get("schedule") or "") or None
            p.snat = rule_view.snat_pools(r, "v4")  # SNAT je Familie (0.47.0)
            p.snat6 = rule_view.snat_pools(r, "v6")
            out[r.id] = p
        if out:
            self._load_details(sid, where, out)
        self._policies[key] = list(out.values())
        return self._policies[key]

    def _load_details(self, sid: int, where: tuple, out: dict[int, _Policy]) -> None:
        # Geo-Adressobjekte der Firewall (Typ `geography`): nur oeffentliche Adressen (0.112.0)
        geo = set(self.session.scalars(select(DeviceObject.name).where(
            DeviceObject.source_system_id == sid,
            DeviceObject.obj_type == "geography")))  # fmt: skip
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
        for rid, role, kind, inline, value, name, epg_id in self.session.execute(
            select(
                RuleEndpoint.rule_id,
                RuleEndpoint.role,
                RuleEndpoint.kind,
                RuleEndpoint.inline_value,
                NetworkObject.value,
                NetworkObject.name,
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
            if p.isdb.get(side) or name in geo:
                # Land bzw. Internet Service: trifft keine private Adresse (0.112.0)
                p.public_only[side] = True
                continue
            p.unresolved[side] = True  # FQDN, eigene ISDB, dynamisch: nicht als IP pruefbar
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
        for rid, name in self.session.execute(
            select(RuleObjectRef.rule_id, RuleObjectRef.name)
            .join(Rule, Rule.id == RuleObjectRef.rule_id)
            .where(*where, RuleObjectRef.role == RefRole.SERVICE)
            .order_by(RuleObjectRef.rule_id, RuleObjectRef.position)
        ):
            if name not in out[rid].names:
                out[rid].names.append(name)

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

    def _pools(self, sid: int, vdom: str) -> dict[str, NatRule]:
        key = (sid, vdom)
        if key not in self._pool_cache:
            self._pool_cache[key] = {
                n.name: n
                for n in self.session.scalars(
                    select(NatRule)
                    .where(
                        NatRule.source_system_id == sid,
                        NatRule.nat_type == "snat_pool",
                        or_(NatRule.vdom == vdom, NatRule.vdom.is_(None)),
                    )
                    .order_by(NatRule.id)
                )
            }
            self._vip_cache[key] = [
                n
                for n in self.session.scalars(
                    select(NatRule).where(
                        NatRule.source_system_id == sid,
                        NatRule.nat_type == "dnat_vip",
                        or_(NatRule.vdom == vdom, NatRule.vdom.is_(None)),
                    )
                )
                if str((n.raw or {}).get("nat-source-vip") or "").lower() == "enable"
            ]
        return self._pool_cache[key]

    def snat(self, sid: int, vdom: str, pools: list[str], egress: dict | None, src=None) -> dict:
        """Quelle nach SNAT (0.47.0 genauer):

        - `nat-source-vip` einer VIP, deren interne IP die Quelle ist: die externe IP der VIP
          (vor dem NAT der Policy);
        - IP-Pool: `overload`, `one-to-one`, `port-block-allocation` - der ganze Bereich des
          Pools (als Netz, wenn er eines ist; welche IP, entscheidet die FortiGate);
          `fixed-port-range` - die IP, die der Quelle fest zugeordnet ist (interner Bereich
          gleichmaessig auf den externen verteilt);
        - sonst die IP des Ausgangs-Interfaces in der Familie der Quelle.
        `to` ist die Quelle danach (IP bzw. Netz), `sure` sagt, ob es genau diese ist."""
        family = 6 if getattr(src, "version", 4) == 6 else 4
        by_name = self._pools(sid, vdom)
        vip = self._vip_source(sid, vdom, src, egress)
        if vip is not None:
            return vip
        if pools:
            name = next((n for n in pools if n in by_name and _family_of(by_name[n]) == family),
                        next((n for n in pools if n in by_name), pools[0]))  # fmt: skip
            pool = by_name.get(name)
            out = {"pool": name, "to": None, "sure": False}
            if len(pools) > 1:
                out["more"] = [p for p in pools if p != name]
            if pool is None or not pool.ext_ip:
                return out | {"missing": True}
            kind = str((pool.attrs or {}).get("type") or (pool.raw or {}).get("type") or "overload")
            first, last = _range(pool.ext_ip)
            out |= {"type": kind, "range": pool.ext_ip}
            if first is None:
                return out
            if first == last:
                return out | {"to": str(first), "sure": True}
            if kind == "fixed-port-range" and src is not None and not is_net(src):
                mapped = _fixed(src, (pool.attrs or {}).get("source"), first, last)
                if mapped is not None:
                    return out | {"to": str(mapped), "sure": True}
            nets = list(ipaddress.summarize_address_range(first, last))
            to = str(nets[0]) if len(nets) == 1 else str(first)
            return out | {"to": to}
        iface = (egress or {}).get("interface")
        if sid not in self._ifaces:
            self._ifaces[sid] = {
                name: [str(ip).split("/")[0] for ip in ips]
                for name, ips in self.session.execute(
                    select(InvInterface.name, InvInterface.ips).where(
                        InvInterface.source_system_id == sid
                    )
                )
                if ips
            }
        ips = self._ifaces[sid].get(iface or "", [])
        ip = next((x for x in ips if (":" in x) == (family == 6)), None)
        return {"interface": iface, "to": ip, "sure": ip is not None}

    def _vip_source(self, sid: int, vdom: str, src, egress: dict | None) -> dict | None:
        """VIP mit `nat-source-vip`: Verkehr von ihrer internen IP geht mit der externen IP
        hinaus (alle Ausgaenge bzw. `extintf`)."""
        if src is None or is_net(src):
            return None
        self._pools(sid, vdom)
        out = (egress or {}).get("interface"), (egress or {}).get("zone")
        for n in self._vip_cache.get((sid, vdom), []):
            mapped = [str(x).split("/")[0].split("-")[0] for x in nat_store.internal_ips(n)]
            if str(src) not in mapped or not n.ext_ip:
                continue
            extintf = str((n.raw or {}).get("extintf") or "any")
            if extintf != "any" and extintf not in out:
                continue
            first, _last = _range(n.ext_ip)
            return {"vip": n.name, "to": str(first) if first else None, "sure": first is not None}
        return None

    # -- Pruefen -----------------------------------------------------------------

    def check(self, sid: int, vdom: str, ingress: dict | None, egress: dict | None, src, dst,
              service: tuple[str, int | None] | None, after: int = 0) -> dict:  # fmt: skip
        """Urteil je Hop: allow | deny | implicit_deny | depends | unknown, mit der
        entscheidenden Policy und den moeglichen davor; mit `after` bis zu so viele passende
        Policies dahinter (`after`, greifen nicht mehr - z. B. hinter einer Sperre zuoberst)."""
        policies = self.policies(sid, vdom)
        if not policies:
            return {"verdict": "unknown", "text": L("keine Policies gelesen", "no policies read"),
                    "rules": []}  # fmt: skip
        known = self.known(sid)
        shown: list[dict] = []
        later: list[dict] = []
        decisive = None
        # Bedingungen (Anzeige in `unsure`): Bezeichnung in der Sprache der Anfrage
        words = {k: L(*v) for k, v in _CHECKS.items()}
        for p in policies:
            if not p.enabled:
                continue
            checks = {
                words["in"]: _ports(p.srcintf, ingress, known),
                words["out"]: _ports(p.dstintf, egress, known),
            }
            src_ok, src_via, src_part = _address(p, "src", src)
            dst_ok, dst_via, dst_part = _address(p, "dst", dst)
            # Netz (0.39.0): erfasst die Policy nur einen Teil, ist sie nur „moeglich“
            checks[words["src_part"] if src_part else words["src"]] = src_ok
            checks[words["dst_part"] if dst_part else words["dst"]] = dst_ok
            checks[words["service"]] = _service(p, service)
            checks[words["identity"]] = None if p.identity else True
            checks[f"{words['schedule']} {p.schedule}"] = (
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
                "services": (p.names or [s[3] for s in p.services])[:4],
                "unsure": unsure,
                "decides": not unsure,
                "snat": p.snat6 if getattr(src, "version", 4) == 6 else p.snat,
            }
            if decisive is not None:  # dahinter (0.78.4): passt, wird aber nicht erreicht
                later.append(entry | {"decides": False, "after": True})
                if len(later) >= after:
                    break
                continue
            if unsure:
                if len(shown) < SHOWN:
                    shown.append(entry)
                continue
            shown.append(entry)
            decisive = entry
            if not after:
                break
        result = _verdict(decisive, shown)
        if later:
            result["after"] = later
            # Sperre zuoberst (z. B. „Default Block …“): die erlaubende dahinter nennen
            allow = next((e for e in later if e["action"] == "allow"), None)
            if decisive["action"] == "deny" and allow is not None:
                result["text"] += L(
                    f" – dahinter würde {_label(allow)} erlauben (nicht erreicht)",
                    f" – {_label(allow)} behind it would allow (not reached)",
                )
        return result


# Bedingungen einer Policy (nicht pruefbar = `unsure`) - (Deutsch, Englisch)
_CHECKS = {
    "in": ("Eingang", "Incoming"),
    "out": ("Ausgang", "Outgoing"),
    "src": ("Quelle", "Source"),
    "src_part": ("Quelle nur teilweise", "Source only partially"),
    "dst": ("Ziel", "Destination"),
    "dst_part": ("Ziel nur teilweise", "Destination only partially"),
    "service": ("Dienst", "Service"),
    "identity": ("Benutzer/Gruppen", "Users/groups"),
    "schedule": ("Zeitplan", "Schedule"),
}


def _family_of(n: NatRule) -> int:
    if n.family:
        return 6 if n.family == "v6" else 4
    return 6 if ":" in str(n.ext_ip or "") else 4


def _range(text: str | None):
    """`a-b` bzw. `a` (IP-Pool, VIP) -> (erste, letzte) Adresse; ungueltig -> (None, None)."""
    a, _, b = str(text or "").partition("-")
    try:
        first = ipaddress.ip_address(a.strip())
        last = ipaddress.ip_address(b.strip()) if b.strip() else first
    except ValueError:
        return None, None
    return (first, last) if first.version == last.version and first <= last else (first, first)


def _fixed(src, source: str | None, first, last):
    """fixed-port-range: der interne Bereich verteilt sich gleichmaessig auf den externen -
    die externe IP der Quelle; ausserhalb des internen Bereichs None."""
    lo, hi = _range(source)
    if lo is None or src.version != lo.version or not lo <= src <= hi:
        return None
    n_int, n_ext = int(hi) - int(lo) + 1, int(last) - int(first) + 1
    block = -(-n_int // n_ext)  # aufgerundet: interne IPs je externer IP
    return first + (int(src) - int(lo)) // block


def _verdict(decisive: dict | None, shown: list[dict]) -> dict:
    before = [e for e in shown if not e["decides"]]
    reasons = sorted({u for e in before for u in e["unsure"]})
    listed = ", ".join(reasons)
    why = L(f" – abhängig von: {listed}", f" – depends on: {listed}") if reasons else ""
    if decisive is not None:
        name = _label(decisive)
        other = [e for e in before if e["action"] != decisive["action"]]
        if other:
            first = _label(other[0])
            text = L(
                f"{first} kann vor {name} greifen{why}", f"{first} can apply before {name}{why}"
            )
            return {"verdict": "depends", "rules": shown, "text": text}
        if decisive["action"] == "allow":
            text = L(f"erlaubt durch {name}", f"allowed by {name}")
        else:
            text = L(f"gesperrt durch {name}", f"blocked by {name}")
        return {"verdict": decisive["action"], "rules": shown, "text": text}
    if before:
        text = L(f"nur mögliche Policies, sonst implizit gesperrt{why}",
                 f"only possible policies, otherwise implicitly denied{why}")  # fmt: skip
        return {"verdict": "depends", "rules": shown, "text": text}
    text = L("keine passende Policy – implizit gesperrt", "no matching policy – implicitly denied")
    return {"verdict": "implicit_deny", "rules": [], "text": text}


def _label(e: dict) -> str:
    if e.get("label"):
        return e["label"]
    name = L(f" „{e['name']}“", f" “{e['name']}”") if e["name"] else ""
    return f"Policy {e['external_id']}{name}"


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


def _address(p: _Policy, role: str, ip) -> tuple[bool | None, str | None, bool]:
    """Quelle bzw. Ziel: (passt, ueber welches Objekt, nur teilweise); negiert umgekehrt.

    Ein Netz (0.39.0) passt, wenn ein Objekt der Policy es ganz enthaelt (`10.10.0.0/23` in
    `10.10.0.0/16`); ueberschneiden sie sich nur, erfasst die Policy einen Teil - „moeglich“."""
    ranges = p.ranges.get(role, [])
    if ip is None:  # Quelle unbekannt (Start gewaehlt)
        if not p.negate.get(role) and any(r[3] for r in ranges):
            return True, "any", False
        return None, None, False
    lo, hi = span(ip)
    same = [r for r in ranges if r[0] == ip.version]
    hits = [r for r in same if r[1] <= lo and hi <= r[2]]
    part = [] if hits else [r for r in same if r[1] <= hi and lo <= r[2]]
    best = min(hits, key=lambda r: r[2] - r[1]) if hits else None
    # Land bzw. Internet Service (0.112.0): offen nur fuer oeffentliche Adressen - eine private
    # Quelle bzw. ein privates Ziel trifft er nicht (Anwender: „abhängig“ trotz offener Regeln)
    unresolved = p.unresolved.get(role) or (p.public_only.get(role) and not is_private(ip))
    if p.negate.get(role):
        if best is not None:
            return (None, None, False) if unresolved else (False, None, False)
        if part:
            return None, None, True
        return (None, None, False) if unresolved else (True, L("negiert", "negated"), False)
    if best is not None:
        return True, "any" if best[3] else (best[4] or L("Netz", "network")), False
    if part:
        widest = max(part, key=lambda r: r[2] - r[1])
        return None, f"{widest[4] or L('Netz', 'network')} {L('(Teil)', '(part)')}", True
    if unresolved or not p.endpoints.get(role):
        return None, None, False
    return False, None, False


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


@dataclass
class Checks:
    """Geladene Policies und Contracts samt Urteilen je Hop - fuer viele Wege eines Mandanten
    nur einmal (Verbindungen der Applikations-Doku, 0.45.0)."""

    checker: Checker
    aci: object | None = None
    cache: dict[tuple, dict] = field(default_factory=dict)
    after: int = 0  # passende Policies hinter der entscheidenden (nur die Weg-Suche zeigt sie)

    @classmethod
    def load(cls, session: Session, tenant_id: int, after: int = 0) -> "Checks":
        return cls(Checker(session, tenant_id), after=after)


def check_paths(session: Session, tenant_id: int, paths: list[dict], src, dst, service,
                checks: Checks | None = None) -> None:  # fmt: skip
    """Je Weg und FortiGate- bzw. ACI-VRF-Hop das Urteil (`hop.rules`), dazu je Weg das
    Gesamturteil (`path.policy`): gesperrt am ersten sperrenden Hop, sonst abhaengig bzw.
    erlaubt. NAT: Ziel je Hop aus dem Weg (`hop.dst` nach einer VIP davor), interner Port bei
    Port-Weiterleitung, Quelle nach SNAT (`hop.snat`) fuer die Hops danach. Ohne `checks`
    (Weg-Suche) auch die passenden Policies hinter der entscheidenden (`rules.after`)."""
    from app.store.aci_check import AciChecker  # zirkulaer: aci_relevance -> paths

    checks = checks or Checks.load(session, tenant_id, after=AFTER)
    checker, cache = checks.checker, checks.cache
    for path in paths:
        verdicts = []
        cur_src, cur_svc = src, service
        lb = None
        for hop in path["hops"]:
            if lb is not None:  # nach dem Load Balancer (NSX ALB): Quelle und Port zum Server
                cur_src, cur_svc = _behind_balancer(lb, cur_src, cur_svc)
            lb = hop.get("lb")
            kind = hop.get("kind")
            if kind not in FIREWALL_KINDS + ACI_KINDS:
                continue
            hop_dst = endpoint(hop["dst"]) if hop.get("dst") else dst
            if kind in ACI_KINDS:
                checks.aci = checks.aci or AciChecker(session, tenant_id)
                sid, vrf = hop["source_system_id"], hop["context"]
                into = (hop.get("leak") or {}).get("vrf")  # Ziel in geleakter VRF (0.44.0)
                key = ("aci", sid, vrf, cur_src, hop_dst, cur_svc, into)
                if key not in cache:
                    cache[key] = checks.aci.check(sid, vrf, cur_src, hop_dst, cur_svc, into)
                hop["rules"] = cache[key]
                verdicts.append((hop["label"], cache[key]["verdict"], kind, cache[key]["text"]))
                continue
            cur_svc = _forwarded(cur_svc, hop.get("nat"))
            ingress, egress = hop.get("ingress") or {}, hop.get("egress") or {}
            sid, vdom = hop["source_system_id"], hop["context"]
            ports = (ingress.get("interface"), ingress.get("zone"),
                     egress.get("interface"), egress.get("zone"))  # fmt: skip
            key = (sid, vdom, *ports, cur_src, hop_dst, cur_svc)
            if key not in cache:
                cache[key] = checker.check(sid, vdom, ingress, egress, cur_src, hop_dst, cur_svc,
                                           checks.after)  # fmt: skip
            result = hop["rules"] = cache[key]
            verdicts.append((hop["label"], result["verdict"], kind, result["text"]))
            decisive = next((r for r in result["rules"] if r["decides"]), None)
            if result["verdict"] == "allow" and decisive:
                # Quelle danach (0.47.0): VIP mit nat-source-vip, sonst das NAT der Policy
                snat = None
                if cur_src is not None:
                    snat = checker._vip_source(sid, vdom, cur_src, egress)
                if snat is None and decisive.get("snat") is not None:
                    snat = checker.snat(sid, vdom, decisive["snat"], egress, cur_src)
                if snat is not None:
                    hop["snat"] = snat
                    to = snat.get("to")
                    cur_src = endpoint(to) if to else None  # unbekannt: nur moeglich
        path["policy"] = _overall(verdicts)


def _behind_balancer(lb: dict, src, service):
    """Verkehr vom Load Balancer zum Server (ADR 0027): Quelle = Service Engine (SNAT, Adresse
    unbekannt - die VIP steht fuer ihr Netz) bzw. der Client; Port = Port des Servers."""
    if lb.get("snat") != "client":
        src = endpoint(lb["ext"]) if lb.get("ext") else None
    port = lb.get("server_port")
    if service and port not in (None, ""):
        try:
            service = (service[0], int(port))
        except (TypeError, ValueError):
            pass
    return src, service


def _forwarded(service, nat: dict | None):
    """Port-Weiterleitung einer VIP: externer Port -> interner Port (Bereich mit Abstand)."""
    if not service or service[1] is None or not nat or not nat.get("ports"):
        return service
    ext, mapped = (_port_range(p) for p in nat["ports"])
    if ext is None or mapped is None or not ext[0] <= service[1] <= ext[1]:
        return service
    return (service[0], mapped[0] + service[1] - ext[0])


def _port_range(text: str | None) -> tuple[int, int] | None:
    a, _, b = str(text or "").partition("-")
    if not a.strip().isdigit():
        return None
    lo = int(a)
    return lo, int(b) if b.strip().isdigit() else lo


def _overall(verdicts: list[tuple]) -> dict | None:
    """Urteil eines Wegs aus (Hop, Urteil, Art[, Text]) je Hop. Abhaengig (0.112.0, Anwender:
    „ich weiss nicht, warum es dort abhängig sagt“): mit dem Grund je Hop."""
    if not verdicts:
        return None
    for label, v, *_rest in verdicts:
        if v in ("deny", "implicit_deny"):
            return {"verdict": "deny", "text": L(f"gesperrt an {label}", f"blocked at {label}")}
    open_ = [(x[0], x[3] if len(x) > 3 else None) for x in verdicts if x[1] == "depends"]
    at = L("an", "at")
    unread = "; ".join(
        f"{what} {at} {', '.join(labels)}"
        for what, kinds in (
            (L("keine Policies gelesen", "no policies read"), FIREWALL_KINDS),
            (L("Contracts nicht prüfbar", "contracts not verifiable"), ACI_KINDS),
        )
        if (labels := [x[0] for x in verdicts if x[1] == "unknown" and x[2] in kinds])
    )
    if open_:
        listed = "; ".join(f"{label} ({why})" if why else label for label, why in open_)
        text = L(f"abhängig an {listed}", f"depends at {listed}")
        if unread:
            text += f"; {unread}"
        return {"verdict": "depends", "text": text}
    if unread:
        return {"verdict": "unknown", "text": unread}
    fw = sum(1 for x in verdicts if x[2] in FIREWALL_KINDS)
    vrfs = len(verdicts) - fw
    allowed = L("erlaubt", "allowed")
    if not vrfs:
        every = L(f" an allen {fw} Firewalls", f" at all {fw} firewalls") if fw > 1 else ""
        return {"verdict": "allow", "text": allowed + every}
    s_fw, s_vrf = ("s" if fw > 1 else ""), ("s" if vrfs > 1 else "")
    parts = [L(f"{fw} Firewall{s_fw}", f"{fw} firewall{s_fw}") if fw else "",
             L(f"{vrfs} ACI-VRF{s_vrf}", f"{vrfs} ACI VRF{s_vrf}")]  # fmt: skip
    return {"verdict": "allow", "text": f"{allowed} ({', '.join(p for p in parts if p)})"}
