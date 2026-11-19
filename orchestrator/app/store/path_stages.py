"""Stufen der Weg-Suche je Hersteller (ADR 0070): was ein Geraet in welcher Reihenfolge tut.

`paths._trace` ist die Suche (Start, Warteschlange, Ende); hier steht, was **ein** Geraet mit
einem Paket macht - als Stufen in der Reihenfolge des Herstellers, jede mit Entscheidung und
Begruendung. Das Forwarding selbst (laengstes Praefix, Distanz, Prioritaet, Null-Route,
rekursiver Next-Hop) liegt in `forwarding.py`.

**FortiGate** (FortiOS 7.x, „Life of a packet“):

1. *Reverse Path* am Eingang (`reverse_path`): ohne aktive Route zur Quelle ueber den Eingang
   verwirft die Firewall („reverse path check fail“). Abhaengig von `asymroute`, `src-check` je
   Interface und `strict-src-check` (`system settings`, je VDOM gelesen) - fehlen die Werte, gilt
   die Stelle als **nicht geprueft** (gezaehlt), nie still verworfen.
2. *DNAT* (`dnat`): VIP nach externer IP, `extintf` **und Dienst** - bei Port-Weiterleitung nur
   fuer ihr Protokoll und ihren Port, mehrere VIPs je Adresse nach Port. Ohne Central NAT nur
   VIPs, die in einer eingeschalteten Policy stehen; mit Central NAT jede eingeschaltete VIP.
3. *Policy-Routen* in der Reihenfolge der Liste (nicht `seq-num`), mit Protokoll und Port;
   `action deny` beendet das Policy-Routing (weiter in der Routing-Tabelle). Danach *SD-WAN*:
   eine Regel gilt nur, wenn ein Mitglied eine Route zum Ziel hat.
4. *Routing-Tabelle* (`forwarding.decide`).
5. *IPsec* am Ausgang (`ipsec`): ohne Phase 2, deren Selektoren Quelle **und** Ziel enthalten,
   bzw. mit Tunnel down verwirft die Firewall (ADR 0063 Nachtrag).
6. *Central SNAT* (`central_snat`) - nach der Policy, in der Reihenfolge der Tabelle.

**Load Balancer** (NSX ALB): alle Virtual Services derselben VIP und desselben Ports zusammen
(SNI: der Parent verteilt auf die Pools der Children), alle Mitglieder; eine Umleitung HTTP →
HTTPS endet am Load Balancer.

Was sich nicht sicher entscheiden laesst, kommt als `uncertain` zurueck und wird am Weg gezaehlt
(ADR 0048) - nie eine Annahme als Tatsache. Nur lesend.
"""

import ipaddress

from sqlalchemy import or_, select

from app.i18n import L
from app.models import (
    DeviceObject,
    EndpointRole,
    NatRule,
    NetworkObject,
    ObjectClass,
    Rule,
    RuleDomain,
    RuleEndpoint,
    TopoPolicyRoute,
)
from app.store import forwarding
from app.store import nat as nat_store
from app.store.topology import is_net, overlaps, span, table_of, within

FIREWALLS = ("firewall", "vdom")
_PROTO = {"icmp": 1, "tcp": 6, "udp": 17, "icmp6": 58, "icmpv6": 58, "sctp": 132, "gre": 47,
          "esp": 50, "ah": 51, "ospf": 89}  # fmt: skip
_PORTED = (6, 17, 132)
_ALL = ("all", "all6", "any")
# Arten der Unsicherheit am Weg (`result.uncertain`, in den Verbindungen gezaehlt - ADR 0048)
UNCERTAIN = (
    "truncated",
    "policy_route",
    "sdwan",
    "rpf",
    "nat",
    "vpn",
    "default",
    "onlink",
    "return",
    # Quelle als Netz, von dem nur Teile angeschlossen sind (Gegenpruefung P-13)
    "start_part",
    # ADR 0071 Nachtrag: Verkehr, den die FortiGate vielleicht ohne Policy weiterleitet
    # (`allow-traffic-redirect` bzw. `intrazone` nicht gelesen) - `policy_check.Checker.free`
    "no_policy",
)


# -- kleine Helfer (auch von `paths` genutzt) -------------------------------------------------


def bounds(text: str | None):
    """(erste, letzte) IP eines Werts: IP, Bereich a-b oder Netz; sonst None."""
    value = (text or "").strip()
    try:
        if "-" in value:
            a, _, b = value.partition("-")
            lo, hi = ipaddress.ip_address(a.strip()), ipaddress.ip_address(b.strip())
        elif "/" in value:
            net = ipaddress.ip_network(value, strict=False)
            lo, hi = net.network_address, net.broadcast_address
        else:
            lo = hi = ipaddress.ip_address(value)
    except ValueError:
        return None
    return (lo, hi) if lo.version == hi.version else None


def port_span(text: str | None) -> tuple[int, int] | None:
    """`443` bzw. `8000-8010` -> (von, bis); leer bzw. unlesbar = jeder Port."""
    lo, _, hi = (str(text) if text is not None else "").partition("-")
    try:
        return (int(lo), int(hi or lo)) if lo.strip() else None
    except ValueError:
        return None


def vip_target(n: NatRule, ip) -> list[str]:
    """Interne IP(s) einer VIP fuer das Ziel: Bereich auf Bereich mit demselben Abstand, sonst
    die zugeordnete IP bzw. alle Real Server; leer, wenn das Ziel nicht die externe IP ist."""
    ext = bounds(n.ext_ip)
    if ext is None or ext[0].version != ip.version or not ext[0] <= ip <= ext[1]:
        return []
    mapped = bounds(n.mapped_ip)
    if mapped is not None and mapped[0] != mapped[1] and not n.targets:
        moved = ipaddress.ip_address(int(mapped[0]) + int(ip) - int(ext[0]))
        if moved <= mapped[1]:
            return [str(moved)]
    return [str(bounds(t)[0]) if bounds(t) else t for t in nat_store.internal_ips(n)]


def proto_number(service) -> int | None:
    """IP-Protokoll eines Dienstes (`("tcp", 443)` -> 6); unbekannt -> None."""
    if not service or not service[0]:
        return None
    name = str(service[0]).lower()
    if name.isdigit():
        return int(name)
    return _PROTO.get(name)


# -- Einstellungen je Kontext (FortiGate: `system settings`, Interfaces) ----------------------


def settings(g, key) -> dict:
    """Gelesene Einstellungen des Kontexts (`topo_context.settings`): `central_nat`,
    `asymroute`, `strict_src_check`, `src_check_off` (Interfaces), `central_snat` (Tabelle).
    Leer = nicht gelesen (aelterer Lauf, kein Leserecht) - dann gilt „nicht geprueft“."""
    return g.settings.get(key) or {}


# Bezeichnungen des Collectors (`collectors/fortigate/routing.py`), wenn das Leserecht fehlt
SETTINGS_MISSING = ("VDOM-Einstellungen", "Central SNAT")


def is_routing_missing(text: str) -> bool:
    """Betrifft ein „nicht lesbar“ eines Kontexts die Routing-Tabelle - nicht IPsec oder die
    Einstellungen des VDOMs (die werden an ihrer Stufe als „nicht geprueft“ ausgewiesen)?"""
    from app.store import vpn as vpn_store  # spaet: vpn laedt Modelle

    return not vpn_store.is_vpn_missing(text) and not str(text).startswith(SETTINGS_MISSING)


# -- Stufe 1: Reverse Path --------------------------------------------------------------------


def reverse_path(g, key, table, src, ingress: str | None) -> dict | None:
    """Reverse Path der FortiGate am Eingang. None = besteht bzw. gilt nicht; sonst
    `{state: "dropped" | "unchecked", text}`: verworfen (Einstellungen gelesen) bzw. nach der
    Vorgabe verworfen, aber nicht geprueft (Einstellungen nicht gelesen)."""
    if src is None or not ingress:
        return None
    conf = settings(g, key)
    # IPv6 hat einen eigenen Schalter (`asymroute6`, Gegenpruefung P-10); ist er nicht gelesen
    # (aelterer Lauf), gilt fuer IPv6 „nicht geprueft“ - nie der Wert fuer IPv4
    asym = "asymroute6" if src.version == 6 else "asymroute"
    known = asym in conf or (src.version != 6 and "strict_src_check" in conf)
    if conf.get(asym):
        return None  # asymmetrisches Routing erlaubt: kein Reverse Path
    if ingress in (conf.get("src_check_off") or []):
        return None  # `src-check disable` am Interface
    state, strict = forwarding.reverse_path(g, key, table, src, ingress)
    label = g.nodes[key].label
    if state == "feasible":
        if conf.get("strict_src_check") and not strict:
            return {
                "state": "dropped",
                "text": L(
                    f"verworfen an {label}: Reverse Path (strict-src-check) – die beste Route "
                    f"zur Quelle {src} geht nicht über den Eingang {ingress}",
                    f"dropped at {label}: reverse path (strict-src-check) – the best route to "
                    f"the source {src} does not use the incoming interface {ingress}",
                ),
            }
        if not known and not strict:
            return {
                "state": "note",
                "text": L(
                    f"{label}: die beste Route zur Quelle {src} geht nicht über den Eingang "
                    f"{ingress} – mit strict-src-check würde die Firewall verwerfen "
                    "(Einstellung nicht gelesen)",
                    f"{label}: the best route to the source {src} does not use the incoming "
                    f"interface {ingress} – with strict-src-check the firewall would drop "
                    "(setting not read)",
                ),
            }
        return None
    if state == "unknown":
        return {
            "state": "note",
            "text": L(
                f"{label}: Reverse Path am Eingang {ingress} nicht prüfbar (Routen zur Quelle "
                f"{src} ohne Interface bzw. nur für Teile des Netzes)",
                f"{label}: reverse path at the incoming interface {ingress} cannot be checked "
                f"(routes to the source {src} without interface or for parts of the network "
                "only)",
            ),
        }
    if known:
        return {
            "state": "dropped",
            "text": L(
                f"verworfen an {label}: Reverse Path – keine Route zur Quelle {src} über den "
                f"Eingang {ingress}",
                f"dropped at {label}: reverse path – no route to the source {src} via the "
                f"incoming interface {ingress}",
            ),
        }
    return {
        "state": "unchecked",
        "text": L(
            f"nicht geprüft an {label}: keine Route zur Quelle {src} über den Eingang {ingress} "
            "– nach der Vorgabe verwirft die Firewall (Reverse Path); asymroute und src-check "
            "sind nicht gelesen",
            f"not checked at {label}: no route to the source {src} via the incoming interface "
            f"{ingress} – by default the firewall drops (reverse path); asymroute and src-check "
            "have not been read",
        ),
    }


# -- Stufe 2: DNAT (VIP) ----------------------------------------------------------------------


def used_vips(g, key) -> set[tuple[str, str | None]] | None:
    """(Name, externe IP) der VIPs, die in einer eingeschalteten Policy des VDOMs als Ziel
    stehen (auch ueber VIP-Gruppen - der Collector loest sie auf)."""
    cache = g.__dict__.setdefault("_used_vips", {})
    if key not in cache:
        found: set[tuple[str, str | None]] = set()
        rows = g.session.execute(
            select(NetworkObject.name, NetworkObject.value, Rule.raw)
            .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
            .join(Rule, Rule.id == RuleEndpoint.rule_id)
            .where(
                Rule.source_system_id == key[0],
                Rule.domain == RuleDomain.FIREWALL,
                or_(Rule.vdom == key[1], Rule.vdom.is_(None)),
                RuleEndpoint.role == EndpointRole.DST,
                NetworkObject.object_class == ObjectClass.VIP,
            )
        )
        for name, value, raw in rows:
            if isinstance(raw, dict) and str(raw.get("status") or "enable") == "disable":
                continue
            found.add((name, value))
        cache[key] = found
    return cache[key]


def _vips_at(g, key, ip) -> list[NatRule]:
    """VIPs des Knotens, deren externe Adresse bzw. Bereich die IP enthaelt - ueber einen Index
    je Knoten (einzelne Adressen direkt, Bereiche der Reihe nach) statt je Hop alle VIPs."""
    cache = g.__dict__.setdefault("_vip_index", {})
    if key not in cache:
        single: dict[tuple[int, int], list[tuple[int, NatRule]]] = {}
        ranges: list[tuple[int, int, int, int, NatRule]] = []
        for i, n in enumerate(g.vips(key)):
            ext = bounds(n.ext_ip)
            if ext is None:
                continue
            lo, hi = ext
            if lo == hi:
                single.setdefault((lo.version, int(lo)), []).append((i, n))
            else:
                ranges.append((lo.version, int(lo), int(hi), i, n))
        cache[key] = (single, ranges)
    single, ranges = cache[key]
    value = int(ip)
    found = list(single.get((ip.version, value), []))
    found += [(i, n) for v, lo, hi, i, n in ranges if v == ip.version and lo <= value <= hi]
    return [n for _i, n in sorted(found, key=lambda x: x[0])]


def _vip_fits_service(n: NatRule, service) -> bool | None:
    """Passt der Dienst zur VIP? Ohne Port-Weiterleitung immer; mit: Protokoll und Port der
    VIP. None = ohne Dienst bzw. Port nicht entscheidbar."""
    if not n.portforward:
        return True
    if service is None:
        return None
    want = proto_number(service)
    have = _PROTO.get(str(n.protocol or "tcp").lower())
    if want is not None and have is not None and want != have:
        return False
    ports = port_span(n.ext_port)
    if ports is None:
        return True
    if service[1] is None:
        return None if want in _PORTED or want is None else False
    return ports[0] <= service[1] <= ports[1]


def _filter_values(raw: dict, field: str, *keys: str) -> list[str]:
    """Werte eines Filters der VIP aus den Rohdaten (`[{"range": "…"}]`, auch als Text)."""
    value = raw.get(field)
    if isinstance(value, str | dict):
        value = [value]
    out: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            item = next((item[k] for k in keys if item.get(k)), None)
        if item not in (None, ""):
            out += str(item).split()
    return out


def _vip_fits_source(raw: dict, src) -> bool | None:
    """`src-filter` der VIP (Gegenpruefung P-2): mit Filter trifft der Verkehr die VIP nur, wenn
    auch die Quelle passt. Ohne Filter immer; None = ohne Quelle, nur fuer einen Teil des
    Quell-Netzes bzw. mit einem nicht lesbaren Wert nicht entscheidbar."""
    values = _filter_values(raw, "src-filter", "range", "name")
    if not values:
        return True
    if src is None:
        return None
    lo, hi = span(src)
    unsure = False
    for v in values:
        rng = bounds(v)
        if rng is None:
            unsure = True
        elif rng[0].version != src.version:
            continue
        elif int(rng[0]) <= lo and hi <= int(rng[1]):
            return True
        elif lo <= int(rng[1]) and int(rng[0]) <= hi:
            unsure = True  # Netz als Quelle, nur zum Teil im Filter
    return None if unsure else False


def _vip_fits_ingress(raw: dict, ingress: dict) -> bool | None:
    """`srcintf-filter` der VIP (P-2): nur Verkehr, der an einem dieser Interfaces ankommt.
    None = Eingang nicht bekannt."""
    names = _filter_values(raw, "srcintf-filter", "interface-name", "name")
    if not names:
        return True
    have = {v for v in (ingress.get("interface"), ingress.get("zone")) if v}
    if not have:
        return None
    return bool(have & set(names))


def source_filtered(g, key, ip) -> list[str]:
    """VIPs des Knotens fuer diese Adresse mit Quell-Filter - ihr DNAT haengt an der Quelle,
    wie sie am Geraet ankommt (nach einem SNAT davor: `paths._enforce` zaehlt)."""
    if is_net(ip):
        return []
    return [n.name for n in _vips_at(g, key, ip)
            if _filter_values(n.raw or {}, "src-filter", "range", "name")]  # fmt: skip


def dnat(g, key, ip, ingress: dict, service,
         src=None) -> tuple[NatRule | None, list[str], str | None]:  # fmt: skip
    """VIP der FortiGate, die das Ziel an diesem Knoten umschreibt:
    -> (VIP, interne Ziele, Hinweis zur Unsicherheit). Ein Netz als Ziel geht ohne DNAT weiter.
    Die VIP gilt nur, wenn auch ihre Filter passen (`src-filter`, `srcintf-filter`, `service`);
    was sich davon nicht entscheiden laesst, ist „moeglich“ und gezaehlt."""
    if is_net(ip):
        return None, [], None
    found = _vips_at(g, key, ip)
    if not found:
        return None, [], None
    ports = {v for v in (ingress.get("interface"), ingress.get("zone")) if v}
    conf = settings(g, key)
    central = conf.get("central_nat")
    used = None if central else used_vips(g, key)
    sure: list[tuple[NatRule, list[str]]] = []
    maybe: list[tuple[NatRule, list[str]]] = []
    unused: list[NatRule] = []
    filtered: dict[str, str] = {}  # VIP -> Filter, der sich nicht entscheiden laesst
    for n in found:
        to = vip_target(n, ip)
        if not to:
            continue
        raw = n.raw or {}
        extintf = str(raw.get("extintf") or "any")
        if extintf != "any" and ports and extintf not in ports:
            continue
        if str(raw.get("status") or "enable") == "disable":
            continue
        fits = _vip_fits_service(n, service)
        if fits is False:
            continue
        by_source = _vip_fits_source(raw, src)
        by_ingress = _vip_fits_ingress(raw, ingress)
        if by_source is False or by_ingress is False:
            continue
        if used is not None and not any(name == n.name for name, _ext in used):
            unused.append(n)
            continue
        # Dienst-Filter der VIP (`set service`): Dienstobjekte, hier nicht aufgeloest
        named = bool(_filter_values(raw, "service", "name"))
        if fits and by_source and by_ingress and not named:
            sure.append((n, to))
        else:
            maybe.append((n, to))
            if fits:  # der Dienst passt - offen ist ein Filter
                filtered[n.name] = (
                    L("Quell-Filter (src-filter)", "source filter (src-filter)")
                    if by_source is None
                    else L("Eingangs-Filter (srcintf-filter)", "interface filter (srcintf-filter)")
                    if by_ingress is None
                    else L("Dienst-Filter (service)", "service filter (service)")
                )
    note = None
    if unused and central is None:
        names = ", ".join(n.name for n in unused[:3])
        note = L(
            f"NAT nicht geprüft an {g.nodes[key].label}: VIP {names} steht in keiner "
            "eingeschalteten Policy – ohne Central NAT kein DNAT; ob Central NAT aktiv ist, "
            "ist nicht gelesen",
            f"NAT not checked at {g.nodes[key].label}: VIP {names} is in no enabled policy – "
            "without central NAT no DNAT; whether central NAT is active has not been read",
        )
    if sure:
        # mehrere passende: Port-Weiterleitung (genauer) vor der ganzen Adresse
        sure.sort(key=lambda x: not x[0].portforward)
        return sure[0][0], sure[0][1], note
    if maybe:
        n, to = maybe[0]
        if n.name in filtered:
            what = filtered[n.name]
            return (
                n,
                to,
                L(
                    f"NAT nicht geprüft an {g.nodes[key].label}: VIP {n.name} gilt nur mit ihrem "
                    f"{what} – hier nicht entscheidbar, DNAT nur möglich",
                    f"NAT not checked at {g.nodes[key].label}: VIP {n.name} applies with its "
                    f"{what} only – cannot be decided here, DNAT only possible",
                ),
            )
        why = L(
            f"VIP {n.name} an {g.nodes[key].label} gilt nur für ihren Dienst "
            f"({n.protocol or 'tcp'}/{n.ext_port}) – ohne Dienst nur möglich",
            f"VIP {n.name} at {g.nodes[key].label} applies to its service only "
            f"({n.protocol or 'tcp'}/{n.ext_port}) – without a service only possible",
        )
        return n, to, why
    return None, [], note


# -- Stufe 3: Policy-Routen und SD-WAN --------------------------------------------------------


def _object_spans(g, sid: int, vdom: str, names: list[str]) -> list[tuple[int, int, int]] | None:
    """Adressobjekte der Firewall (auch Gruppen, verschachtelt) -> Bereiche (Familie, von, bis);
    None, wenn eines nicht als Bereich aufloesbar ist (FQDN, Geo, unbekannt)."""
    from app.store import rule_index  # spaet: nur mit Objekten in Policy-Routen

    cache = g.__dict__.setdefault("_route_objects", {})
    if sid not in cache:
        rows: dict[str, list] = {}
        for o in g.session.scalars(
            select(DeviceObject).where(DeviceObject.source_system_id == sid)
        ):
            rows.setdefault(o.name, []).append(
                (o.vdom, str(getattr(o.category, "value", o.category)), o.values or [],
                 o.members or [])
            )  # fmt: skip
        cache[sid] = rows
    known = cache[sid]
    out: list[tuple[int, int, int]] = []

    def walk(name: str, depth: int) -> bool:
        if name.lower() in _ALL:
            out.append((4, 0, (1 << 32) - 1))
            out.append((6, 0, (1 << 128) - 1))
            return True
        found = [x for x in known.get(name, []) if x[1] in ("address", "address_group")]
        found = [x for x in found if x[0] in (vdom, None)] or found
        if not found or depth > 8:
            return False
        _vdom, _cat, values, members = found[0]
        if members:
            return all(walk(str(m), depth + 1) for m in members)
        if not values:
            return False
        for v in values:
            rng = rule_index.value_range(str(v))
            if rng is None:
                return False
            family, lo, hi, _any = rng
            out.append((family, int(lo, 16), int(hi, 16)))
        return True

    return out if all(walk(str(n), 0) for n in names) else None


def _side(g, key, kind: str, match: dict, side: str, ip) -> bool | None:
    """Passt Quelle bzw. Ziel einer Policy-Route / SD-WAN-Regel? Subnetze direkt, Adressobjekte
    ueber die Objekte der Firewall; Internet-Services nicht pruefbar (None = moeglich)."""
    nets = [] if kind == "sdwan" else list(match.get(side) or [])
    names = list(match.get(f"{side}addr") or [])
    if kind == "sdwan":
        names += match.get(side) or []
    isdb = list(match.get("service") or []) if side == "dst" else []
    if ip is None:
        named = [n for n in names if str(n).lower() not in _ALL]
        return None if nets or named or isdb else True
    partial = False
    for net in nets:
        try:
            net = ipaddress.ip_network(net, strict=False)
        except (ValueError, TypeError):
            continue
        if within(ip, net):
            return True
        partial = partial or overlaps(ip, net)
    unknown = bool(isdb)
    if names:
        spans = _object_spans(g, key[0], key[1], names)
        if spans is None:
            unknown = True
        else:
            lo, hi = span(ip)
            for family, a, b in spans:
                if family != ip.version:
                    continue
                if a <= lo and hi <= b:
                    return True
                partial = partial or (a <= hi and lo <= b)
    if unknown or partial:
        return None
    return False if nets or names else True


def _service_fits(match: dict, attrs: dict, service) -> bool | None:
    """Protokoll, Ports und TOS einer Policy-Route gegen den Dienst."""
    out: bool | None = True
    try:
        proto = int(match.get("protocol") or 0)
    except (TypeError, ValueError):
        proto = 0
    ports = match.get("ports") or []
    if proto or ports:
        have = proto_number(service)
        if service is None or have is None:
            out = None
        elif proto and have != proto:
            return False
        elif len(ports) == 2 and (have in _PORTED or not proto):
            if service[1] is None:
                out = None
            elif not int(ports[0]) <= service[1] <= int(ports[1]):
                return False
    tos_mask = str((attrs or {}).get("tos-mask") or "0x00").lower()
    if tos_mask not in ("0x00", "0", "00", ""):
        out = None  # TOS des Verkehrs ist unbekannt
    return out


def policy_rows(g, key) -> list[TopoPolicyRoute]:
    """Eingeschaltete Policy-Routen und SD-WAN-Regeln des Knotens in der Reihenfolge des
    Geraets: Policy-Routen vor SD-WAN, je Art die Position in der Liste (`attrs.position`,
    sonst die gelieferte Reihenfolge) - nicht `seq-num` (nach `move` weicht sie ab)."""
    cache = g._policy_routes
    if key not in cache:
        rows = list(g.session.scalars(
            select(TopoPolicyRoute)
            .where(
                TopoPolicyRoute.source_system_id == key[0],
                TopoPolicyRoute.context == key[1],
                TopoPolicyRoute.enabled.is_(True),
            )
            .order_by(TopoPolicyRoute.id)
        ))  # fmt: skip

        def rank(x):
            i, p = x
            pos = (p.attrs or {}).get("position")
            return (p.kind, pos if isinstance(pos, int) else i)

        cache[key] = [p for _i, p in sorted(enumerate(rows), key=rank)]
    return cache[key]


def source_rules(g, key) -> bool:
    """Hat der Knoten Policy-Routen bzw. SD-WAN-Regeln, die an der Quelle haengen?"""
    cache = g.__dict__.setdefault("_source_rules", {})
    if key not in cache:
        found = False
        for p in policy_rows(g, key):
            m = p.match or {}
            names = [
                *(m.get("srcaddr") or []),
                *((m.get("src") or []) if p.kind == "sdwan" else []),
            ]
            nets = [] if p.kind == "sdwan" else m.get("src") or []
            if nets or any(str(n).lower() not in _ALL for n in names):
                found = True
                break
        cache[key] = found
    return cache[key]


def policy_routes(g, key, src, dst, ingress: str | None, service=None,
                  table=0) -> tuple[list[dict], dict | None, str | None]:  # fmt: skip
    """Policy-Routen und SD-WAN-Regeln fuer den Verkehr, in der Reihenfolge des Geraets:
    -> (passende bzw. moegliche Regeln fuer den Hop, die Regel, der der Weg folgt, Hinweis zur
    Unsicherheit). Die erste sicher passende entscheidet: `deny` = Policy-Routing beendet
    (Routing-Tabelle), sonst ihr Gateway bzw. Ausgang. Passt davor eine nur moeglich, ist die
    Entscheidung nicht sicher - der Hinweis nennt sie (am Weg gezaehlt)."""
    rows = policy_rows(g, key)
    if not rows:
        return [], None, None
    shown: list[dict] = []
    follow: dict | None = None
    decided = False
    possible: list[dict] = []
    for p in rows:
        m = p.match or {}
        devices = m.get("input_device") or []
        checks = [
            _side(g, key, p.kind, m, "dst", dst),
            _side(g, key, p.kind, m, "src", src),
            True if not devices else (ingress in devices if ingress else None),
            _service_fits(m, p.attrs or {}, service),
        ]
        if False in checks:
            continue
        certain = all(c is True for c in checks)
        entry = {
            "kind": p.kind,
            "seq": p.seq,
            "name": p.name,
            "gateway": p.gateway,
            "output_device": list(p.output_device or []),
            "certain": certain,
        }
        if p.kind == "policy" and (p.action or "permit") == "deny":
            entry["action"] = "deny"
        if p.kind == "sdwan":
            members = _sdwan_members(g, key, p, dst, table)
            if members is not None:
                entry["output_device"] = [m_["interface"] for m_ in members]
                entry["members"] = members
                if not members:
                    # FortiOS: SD-WAN-Regel uebergangen, wenn kein Mitglied eine Route zum Ziel hat
                    entry["skipped"] = True
        shown.append(entry)
        if decided:
            continue
        if entry.get("skipped"):
            continue
        if not certain:
            possible.append(entry)
            continue
        decided = True
        if entry.get("action") == "deny":
            follow = None
            entry["decides"] = True
        elif entry["gateway"] or entry["output_device"]:
            follow = entry
    note = None
    label = g.nodes[key].label
    if possible:
        names = ", ".join(_rule_name(x) for x in possible[:3])
        note = L(
            f"{label}: {names} passt nur möglicherweise (Objekt, Dienst bzw. Eingang nicht "
            "sicher) – der Weg folgt " + ("der sicher passenden Regel" if follow else
                                           "der Routing-Tabelle"),
            f"{label}: {names} only possibly matches (object, service or incoming interface "
            "not certain) – the path follows " + ("the rule that matches for sure" if follow
                                                  else "the routing table"),
        )  # fmt: skip
    return shown, follow, note


def _rule_name(p: dict) -> str:
    kind = "SD-WAN" if p["kind"] == "sdwan" else L("Policy-Route", "policy route")
    return f"{kind} {p.get('name') or p.get('seq') or '?'}"


def _sdwan_members(g, key, p: TopoPolicyRoute, dst, table) -> list[dict] | None:
    """Mitglieder der SD-WAN-Regel (nach Vorrang), ueber die der Verkehr gehen kann, mit
    Gateway. FortiOS: die Regel wird uebergangen, wenn die beste Route zum Ziel nicht ueber ein
    SD-WAN-Mitglied geht; waehlbar sind die Mitglieder der Regel, die eine Route zum Ziel haben.
    None ohne Ziel bzw. ohne Mitglieder in der Regel (nichts zu pruefen); leer = uebergangen."""
    devices = list(p.output_device or [])
    if dst is None or not devices:
        return None
    members = [m for m in (p.attrs or {}).get("members") or [] if isinstance(m, dict)]
    gateways = {str(m.get("interface")): m.get("gateway") for m in members}
    sdwan = {str(m.get("interface")) for m in members if m.get("interface")} | set(devices)
    found = [r for r in g._routes(dst, key) if table_of(r) == table and not forwarding.is_null(r)]
    best = forwarding.winners(found, table)
    if not any(str(h.get("interface") or "") in sdwan for r in best for h in r.next_hops or []):
        return []
    via: dict[str, str | None] = {}
    for r in sorted(found, key=lambda r: -r.prefix_len):
        for h in r.next_hops or []:
            iface = str(h.get("interface") or "")
            if iface and iface not in via:
                via[iface] = h.get("ip")
    out = []
    for d in devices:
        if d in via:
            gw = via[d] or gateways.get(d)
            gw = None if str(gw or "") in ("", "0.0.0.0", "::") else gw  # noqa: S104
            out.append({"interface": d, "gateway": gw})
    return out


def follow_hops(g, key, follow: dict, dst, table) -> list[dict]:
    """Next-Hops der Regel, der der Weg folgt: Gateway und Ausgang der Policy-Route; ohne
    Gateway das der Route ueber diesen Ausgang; SD-WAN: das erste Mitglied mit Route."""
    if follow.get("members"):
        m = follow["members"][0]
        return [{"ip": m.get("gateway"), "interface": m["interface"]}]
    devices = follow["output_device"] or [None]
    gateway = follow["gateway"]
    if not gateway and devices[0] and dst is not None:
        rows = sorted((r for r in g._routes(dst, key) if table_of(r) == table),
                      key=lambda r: -r.prefix_len)  # fmt: skip
        for r in rows:
            hit = next((h for h in r.next_hops or [] if h.get("interface") == devices[0]), None)
            if hit is not None and hit.get("ip"):
                gateway = hit["ip"]
                break
    return [{"ip": gateway, "interface": devices[0]}]


# -- Stufe 5: IPsec am Ausgang ----------------------------------------------------------------


def ipsec(tunnel, inst: dict | None, src, dst, label: str) -> dict | None:
    """Traegt der Tunnel den Verkehr? None = ja (bzw. nicht pruefbar - dann ein Hinweis in
    `note`); `{state: "dropped", text}` = die Firewall verwirft: Tunnel down bzw. keine Phase 2,
    deren Selektoren Quelle **und** Ziel enthalten („no matching IPsec selector, drop“)."""
    from app.store import vpn as vpn_store  # spaet: vpn -> paths

    status = ((inst or {}).get("status") or tunnel.status) if tunnel.kind == "dialup" \
        else tunnel.status  # fmt: skip
    if status == "down":
        return {
            "state": "dropped",
            "text": L(
                f"verworfen an {label}: VPN {tunnel.name} ist down",
                f"dropped at {label}: VPN {tunnel.name} is down",
            ),
        }
    if not tunnel.phase2 or dst is None:
        return None
    full = partial = False
    for p in tunnel.phase2:
        d = vpn_store.coverage(p.get("dst") or [], dst)
        s = "full" if src is None else vpn_store.coverage(p.get("src") or [], src)
        if d == "full" and s == "full":
            full = True
            break
        partial = partial or (d != "none" and s != "none")
    if tunnel.kind == "dialup" and not full:
        # Dial-up: die Netze der verbundenen Gegenstellen zaehlen wie Selektoren
        nets = [n for i in tunnel.instances for n in i.get("nets") or []]
        if nets and vpn_store.coverage(nets, dst) == "full":
            full = True
    if full:
        return None
    if partial or tunnel.unresolved:
        if partial:
            what = L("nur teilweise in den Phase-2-Selektoren",
                     "only partly in the phase 2 selectors")  # fmt: skip
        else:
            what = L("Phase-2-Selektoren nicht auflösbar",
                     "phase 2 selectors cannot be resolved")  # fmt: skip
        return {
            "state": "note",
            "text": L(
                f"VPN {tunnel.name} an {label}: {what} – nicht geprüft",
                f"VPN {tunnel.name} at {label}: {what} – not checked",
            ),  # fmt: skip
        }
    who = L("Quelle und Ziel", "source and destination") if src is not None else L(
        "Ziel", "destination")  # fmt: skip
    return {
        "state": "dropped",
        "text": L(
            f"verworfen an {label}: VPN {tunnel.name} – keine Phase 2, deren Selektoren {who} "
            "enthalten",
            f"dropped at {label}: VPN {tunnel.name} – no phase 2 whose selectors contain {who}",
        ),
    }


# -- Stufe 6: Central SNAT --------------------------------------------------------------------


def central_snat(g, key, ingress: dict, egress: dict, src, dst, service) -> dict | None:
    """Central SNAT der FortiGate (`firewall central-snat-map`, nur mit Central NAT): die erste
    passende Zeile von oben. -> None (kein Central NAT bzw. keine Zeile passt: kein SNAT),
    sonst `{rule, pools: [Namen] | [] (Adresse des Ausgangs) | None (nat disable), sure}`."""
    conf = settings(g, key)
    if not conf.get("central_nat"):
        return None
    rows = conf.get("central_snat")
    if rows is None:
        return {"rule": None, "pools": None, "sure": False, "unread": True}
    ins = {v for v in (ingress.get("interface"), ingress.get("zone")) if v}
    outs = {v for v in (egress.get("interface"), egress.get("zone")) if v}
    sure = True
    for r in rows:
        if r.get("status") == "disable":
            continue
        if not _ports_fit(r.get("srcintf") or [], ins) or not _ports_fit(r.get("dstintf") or [],
                                                                        outs):  # fmt: skip
            continue
        checks = [
            _named(g, key, r.get("orig_addr") or [], src),
            _named(g, key, r.get("dst_addr") or [], dst),
            _snat_service(r, service),
        ]
        if False in checks:
            continue
        if None in checks:
            sure = False
            continue
        if r.get("nat") == "disable":
            return {"rule": r.get("id"), "pools": None, "sure": sure}
        return {"rule": r.get("id"), "pools": list(r.get("nat_ippool") or []), "sure": sure}
    return {"rule": None, "pools": None, "sure": sure}


def _ports_fit(names: list[str], have: set[str]) -> bool:
    return not names or "any" in names or not have or bool(set(names) & have)


def _named(g, key, names: list[str], ip) -> bool | None:
    if not names or all(str(n).lower() in _ALL for n in names):
        return True
    if ip is None:
        return None
    spans = _object_spans(g, key[0], key[1], names)
    if spans is None:
        return None
    lo, hi = span(ip)
    full = any(f == ip.version and a <= lo and hi <= b for f, a, b in spans)
    part = any(f == ip.version and a <= hi and lo <= b for f, a, b in spans)
    return True if full else (None if part else False)


def _snat_service(row: dict, service) -> bool | None:
    try:
        proto = int(row.get("protocol") or 0)
    except (TypeError, ValueError):
        proto = 0
    if not proto:
        return True
    have = proto_number(service)
    if have is None:
        return None
    return have == proto
