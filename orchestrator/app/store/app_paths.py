"""Verbindungen Ende zu Ende einer Applikation (0.45.0, ADR 0021 Entwurf).

Die Applikations-Doku listet die Regeln je Firewall bzw. ACI. Geht eine Verbindung ueber
mehrere Firewalls und das ACI, sind das mehrere Zeilen. Hier ist jede Verbindung eine Zeile
mit ihrem Weg; je Hop die Policy bzw. der Contract, der entscheidet, mit den Objekten (Anwender,
2026-10-23).

- **Verbindung:** Quelle -> Ziel mit Dienst, abgeleitet aus den Treffern der Doku. Eigene Seite
  ist ein System der Applikation (VM bzw. Server) mit seiner IP; die Gegenseite die Objekte der
  Regel (Host, Netz, Bereich - Netz als Ganzes, 0.39.0) bzw. beim Contract die Gegenstellen im
  Scope (EPG mit der IP ihrer ersten VM bzw. ihres ersten Endpunkts, sonst ihr Subnetz;
  externe EPG mit ihren Subnetzen). Gegenstelle `any`, vzAny und Preferred Group sind
  unspezifisch - sie bleiben Regeln, ohne Verbindung. Je Regel hoechstens `MAX_SERVICES`
  Dienste; ACI-Contracts derselben EPG gelten fuer alle Systeme darin (ein System pruefen).
- **Weg:** `paths.trace` - wie die Weg-Suche (Routing, Policy-Routen, NAT, Verbund als ein Hop,
  ACI zwischen VRFs). Ist die Quelle an keinem gelesenen Geraet angeschlossen (Internet,
  Partner), beginnt der Weg auf dem Geraet der Regel.
- **Abgleich Regel <-> Weg:** je Regel, aus der die Verbindung stammt, ob sie auf dem Weg
  entscheidet, nur moeglich ist, vorher eine andere entscheidet bzw. nicht passt, oder ob ihr
  Geraet gar nicht auf dem Weg liegt. Erlaubt eine Regel und sperrt der Weg (bzw. liegt sie
  nicht auf dem Weg), ist die Verbindung als Abweichung markiert - der Fall „Regel zeigt
  Zugriff, Weg-Suche sperrt“ (Befund 0.43.2).
- **Wege uneinheitlich (0.73.0, Anwender):** je Verbindung alle Wege - aktiv, ECMP, Backup und
  je Firewall eines Verbunds (`variants`). Offen ueber einen Weg und gesperrt ueber einen
  anderen (`inconsistent`) ist der Befund „Applikation: Wege uneinheitlich“; durchgehend
  gesperrt ist gewollt und kein Befund mehr.
- **Hilfs-Contracts (ADR 0034):** ein Contract zu externen EPGs mit einer Firewall hinter dem
  L3Out ist keine eigene Verbindung; er prueft im ACI-Hop der Verbindung der Firewall-Regel mit
  und steht bei ihren Regeln, wenn er ihren Dienst oeffnet.
- **Fuer den Netzwerkplan (0.74.0):** je ACI-Hop der L3Out des Ein- bzw. Ausgangs nach dem
  Routing (`l3out_in`/`l3out_out`: Next-Hop im Netz bzw. Peer eines L3Out-Pfads, Interface des
  Pfads), dazu die L3Outs der externen EPGs der Pruefung (`l3outs`), und der Aufbau der
  Applikation (`members`: EPGs mit Tenant/AP, Systeme mit ihren EPGs).

Nur lesend; Stand der letzten Laeufe. Nicht fuer Kunden-Benutzer: der Weg zeigt die
Infrastruktur (ADR 0018).
"""

import ipaddress
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.i18n import L
from app.models import (
    Application,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    Rule,
    RuleDomain,
    RuleIpRange,
    TopoL3Out,
)
from app.store import aci_direction, appdoc, epg_locate, paths, policy_check
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store.topology import endpoint, is_net, within

MAX_TRACES = 120  # Wege je Applikation
MAX_COUNTER = 6  # Gegenstellen je Regel und Seite
MAX_PARTS = 8  # genauer geroutete Teile eines Netzes als eigene Verbindungen (0.79.1)
MAX_SERVICES = 3  # Dienste je Regel
MAX_SYSTEMS = 12  # genannte Systeme je Seite
_SKIP = ("any", "vzany", "pref_group")  # unspezifisch: Regel ohne Verbindung
_BLOCK = ("deny", "implicit_deny")


@dataclass
class _Side:
    ip: str  # IP bzw. Netz fuer den Weg
    label: str
    kind: str  # system | object | epg | external
    systems: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)  # Systeme der Applikation (Doku-Schluessel)
    l3out: str | None = None  # externe EPG: ihr L3Out (Netzwerkplan, 0.74.0)


@dataclass
class _Conn:
    src: _Side
    dst: _Side
    service: tuple[str, int | None] | None
    direction: str
    rules: dict[int, None] = field(default_factory=dict)  # Regeln der Doku, aus denen sie kommt
    starts: list[str] = field(default_factory=list)  # Geraet der Regel (Quelle unbekannt)
    # ACI ist zustandslos (0.69.1): nur aus der Rueckrichtung eines Contracts (Provider ->
    # Consumer mit Filtern ohne Ports)
    reverse: bool = False


def build(session: Session, app: Application, doc: dict | None = None,
          graph: paths.Graph | None = None,
          checks: policy_check.Checks | None = None, zones: bool = True) -> dict:  # fmt: skip
    """Verbindungen der Applikation mit Weg, Urteil je Hop und Abgleich mit den Regeln. Graph
    und Policies lassen sich fuer mehrere Applikationen teilen (Befunde, 0.59.0); `zones`:
    logische Zonen je Verbindung und Hop mit der Absicht (0.78.0, ADR 0024) - die Befunde
    brauchen sie nicht."""
    from app.store import (
        applications,  # spaet: applications nutzt appdoc wie dieses Modul
        zone_path,  # spaet: Zonen nutzen Kunden, diese die Applikationen
    )

    tid = app.tenant_id
    doc = doc or applications.build_doc(session, app)
    data = appdoc.TenantData(session, tid)
    rule_ids = {h["rule_id"] for h in doc["hits"]}
    rules = {
        r.id: r
        for r in session.scalars(
            select(Rule).where(Rule.id.in_(rule_ids)).options(selectinload(Rule.services))
        )
    }
    conns, notes = _candidates(session, data, doc, rules)
    graph = graph or paths.Graph(session, tid)
    checks = checks or policy_check.Checks.load(session, tid)
    out: list[dict] = []
    cut = len(conns) > MAX_TRACES
    l3 = L3Outs(session, {k[0] for k in graph.nodes})
    l3.locator = epg_locate.Locator(session, tid, data.aci, data.epgs_by_id)
    zctx = zone_path.ZoneContext(session, tid, graph) if zones else None
    for c in list(conns.values())[:MAX_TRACES]:
        conn = _connection(session, tid, graph, checks, data, rules, c, l3, zctx)
        out.append(conn)
        # Netz als Ziel (0.79.1, Anwender): genauer geroutete Teile (z. B. ueber einen anderen
        # L3Out als die 0/0-Route, die das ganze Netz enthaelt) als eigene Verbindungen
        for prefix in conn["parts"][:MAX_PARTS]:
            part = _Conn(_copy(c.src), _copy(c.dst), c.service, c.direction, dict(c.rules),
                         list(c.starts), c.reverse)  # fmt: skip
            part.dst.ip, part.dst.label = prefix, f"{c.dst.label} › {prefix}"
            child = _connection(session, tid, graph, checks, data, rules, part, l3, zctx)
            child["part_of"] = conn["key"]
            out.append(child)
    if cut:
        notes.append(
            L(
                f"{len(conns)} Verbindungen – nur die ersten {MAX_TRACES} geprüft",
                f"{len(conns)} connections – only the first {MAX_TRACES} checked",
            )
        )
    order = {"deny": 0, "depends": 1, "unknown": 2, "allow": 3}
    out.sort(key=lambda c: (not c["mismatch"], order.get(c["verdict"], 2)))
    return {
        "connections": out,
        "notes": notes,
        "summary": {
            "total": len(out),
            "allow": sum(1 for c in out if c["verdict"] == "allow" and c["reached"]),
            "blocked": sum(1 for c in out if c["verdict"] == "deny"),
            "open": sum(
                1 for c in out if c["verdict"] in ("depends", "unknown") or not c["reached"]
            ),  # fmt: skip
            "mismatch": sum(1 for c in out if c["mismatch"]),
            "inconsistent": sum(1 for c in out if c["inconsistent"]),
            # gegen die Absicht der Zonen (0.78.0)
            "zone_violations": sum(1 for c in out if (c.get("zones") or {}).get("violation")),
        },
        "members": members(doc),
        # 0.78.2: L3Out-Contracts, die weniger oeffnen als die Firewall (Befund)
        "narrower": doc.get("narrower") or [],
    }


def members(doc: dict) -> dict:
    """Aufbau der Applikation fuer den Netzwerkplan (0.74.0): EPGs mit Tenant/AP und VRF,
    Systeme mit ihren EPGs."""
    return {
        "epgs": [
            {k: e.get(k) for k in ("key", "label", "name", "tenant", "ap", "vrf")}
            for e in doc["epgs"]
        ],  # fmt: skip
        "systems": [
            {
                "key": x["key"],
                "name": x["name"],
                "kind": x["kind"],
                "ips": x["ips"][:4],
                "epgs": x["epgs"],
            }
            for x in doc["systems"]
        ],  # fmt: skip
    }


class L3Outs:
    """L3Outs der ACI-Quellsysteme je VRF mit den Netzen und Interfaces ihrer Pfade und den
    Peers - welcher L3Out einen Hop verlaesst bzw. betritt (0.74.0)."""

    def __init__(self, session: Session, sources: set[int]):
        self.by_vrf: dict[tuple[int, str], list[tuple[str, list, set[str], set[str]]]] = {}
        self.locator = None  # EPG der VIP eines Load Balancers im Fabric (0.85.0)
        if not sources:
            return
        for o in session.scalars(select(TopoL3Out).where(TopoL3Out.source_system_id.in_(sources))):
            nets, ifaces = [], set()
            for p in o.paths or []:
                if p.get("interface"):
                    ifaces.add(p["interface"])
                for e in p.get("ips") or []:
                    try:
                        nets.append(ipaddress.ip_interface(str(e.get("ip") or "")).network)
                    except ValueError:
                        continue
            peers = {str(p.get("ip")) for p in o.peers or [] if p.get("ip")}
            entry = (o.name, nets, ifaces, peers)
            self.by_vrf.setdefault((o.source_system_id, o.vrf or ""), []).append(entry)

    def match(self, sid: int, vrf: str, port: dict | None) -> str | None:
        if not port:
            return None
        hop, iface = port.get("next_hop"), port.get("interface")
        try:
            ip = ipaddress.ip_address(hop) if hop else None
        except ValueError:
            ip = None
        for name, nets, ifaces, peers in self.by_vrf.get((sid, vrf), []):
            if hop and hop in peers:
                return name
            if ip is not None and any(ip.version == n.version and ip in n for n in nets):
                return name
            if iface and iface in ifaces:
                return name
        return None


# -- Verbindungen aus den Treffern ------------------------------------------------------


def _candidates(
    session: Session, data: appdoc.TenantData, doc: dict, rules: dict[int, Rule]
) -> tuple[dict[tuple, _Conn], list[str]]:
    systems = {x["key"]: x for x in doc["systems"]} | _epg_systems(session, doc)
    ranges = _ranges(session, set(rules))
    counters: dict[str, list[_Side]] = {}
    conns: dict[tuple, _Conn] = {}
    skipped: set[str] = set()
    by_label = {epg_store.inventory_label(e): e for e in data.epgs_by_id.values()
                if e.kind == "epg"}  # fmt: skip
    # spezifische Treffer zuerst, je Regel in der Reihenfolge der Doku
    order = {r.id: i for i, r in enumerate(doc["rules"])}
    hits = sorted(
        (h for h in doc["hits"] if h["system"] in systems and h["rule_id"] in rules),
        key=lambda h: (
            h["scope"] != "specific",
            order.get(h["rule_id"], 1 << 30),
            h["system"].startswith("epg:"),  # VMs bzw. Server vor der EPG als System
            systems[h["system"]]["name"],
        ),  # fmt: skip
    )
    for h in hits:
        rule = rules[h["rule_id"]]
        if h["relevance"] == "impossible" or h["scope"] in _SKIP:
            continue
        if h.get("helper"):
            continue  # Hilfs-Contract ueber einen L3Out (ADR 0034): nur Teil des ACI-Hops
        system = systems[h["system"]]
        if rule.domain == RuleDomain.ACI_CONTRACT and not h.get("reverse"):
            names = [x.canonical_name for x in rule.services]
            if aci_direction.replies_only(rule.raw, names):
                continue  # Consumer -> Provider nur Antworten (0.72.1): keine Verbindung
        if rule.domain == RuleDomain.ACI_CONTRACT:
            key = f"aci:{rule.id}:{h['side']}:{h.get('epg') or h['system']}"
            if key not in counters:
                counters[key] = _aci_counter(session, data, h)
            own_ips = _nic_ips(system, h.get("epg"))
        else:
            key = f"fw:{rule.id}:{h['side']}"
            if key not in counters:
                counters[key] = _fw_counter(ranges.get((rule.id, _other(h["side"])), []))
            own_ips = _own_ips(system, h.get("value"))
            entry = _entry(data, rule, h)  # ueber VIP bzw. Load Balancer (0.79.0)
            if entry is not None:
                own_ips = [entry]
        if not counters[key]:
            skipped.add(rule.name or f"#{rule.external_id}")
            continue
        services = _named(h["services"]) if h.get("reverse") else _services(rule)
        for cp in counters[key]:
            ip = next((i for i in own_ips if _family(i) == _family(cp.ip)), None)
            if ip is None:
                continue
            own = _Side(ip, system["name"], "system", [system["name"]], [system["key"]])
            src, dst = (cp, own) if h["direction"] == "in" else (own, cp)
            for svc in services:
                ckey = _group_key(rule, h, cp, svc)
                conn = conns.get(ckey)
                if conn is None:
                    conn = conns[ckey] = _Conn(_copy(src), _copy(dst), svc, h["direction"],
                                               reverse=bool(h.get("reverse")))  # fmt: skip
                else:
                    _add_system(conn.dst if h["direction"] == "in" else conn.src, system)
                conn.rules.setdefault(rule.id)
                # Hilfs-Contracts der Firewall-Regel (ADR 0034) mit diesem Dienst: pruefen mit,
                # wie bisher der Contract zum Netz der externen EPG
                for x in h.get("helpers") or []:
                    for helper in x.get("rule_ids") or []:
                        offered = _services(rules[helper]) if helper in rules else []
                        if None in offered or svc in offered:
                            conn.rules.setdefault(helper)
                start = _start(data, rule, h, by_label)
                if start and start not in conn.starts:
                    conn.starts.append(start)
    # gleiche Verbindung (Quelle, Ziel, Dienst) aus verschiedenen Regeln: eine Zeile
    merged: dict[tuple, _Conn] = {}
    for c in conns.values():
        k = (c.src.ip, c.dst.ip, c.service)
        if k in merged:
            _merge(merged[k], c)
        else:
            merged[k] = c
    # Contract zu einer EPG bzw. externen EPG (geprueft mit ihrem Subnetz): gehoert zu den
    # Verbindungen derselben Quelle bzw. desselben Ziels zu einem Host darin (Firewall-Regel)
    for k, c in list(merged.items()):
        cp = c.src if c.direction == "in" else c.dst
        if cp.kind not in ("epg", "external") or not is_net(endpoint(cp.ip)):
            continue
        net = endpoint(cp.ip)
        hosts = [m for m in merged.values() if m is not c and _same_end(m, c, net)]
        for m in hosts:
            m.rules.update(c.rules)
            m.starts += [s for s in c.starts if s not in m.starts]
        if hosts:
            del merged[k]
    notes = []
    if skipped:
        names = ", ".join(sorted(skipped)[:5]) + (" …" if len(skipped) > 5 else "")
        notes.append(
            L(
                f"ohne prüfbare Gegenstelle (FQDN, ISDB, any): {names}",
                f"without a checkable peer (FQDN, ISDB, any): {names}",
            )
        )
    return merged, notes


def _entry(data: appdoc.TenantData, rule: Rule, h: dict) -> str | None:
    """Ziel der Verbindung, wenn die Regel das System ueber einen Load Balancer erreicht
    (0.79.0, Treffer „V-EXT_x → vs-web“): die externe IP der VIP der FortiGate davor bzw. die
    VIP des Virtual Service - der Weg geht dann ueber DNAT und Load Balancer zum Server."""
    text = str(h.get("object") or "")
    if h.get("side") != "dst" or nat_store.CHAIN not in text:
        return None
    front, _, lb_name = text.partition(nat_store.CHAIN)
    vip = data.nat_by_name.get((rule.source_system_id, rule.vdom, front.strip().lower()))
    if vip is not None and not nat_store.is_balancer(vip) and vip.ext_ip:
        return nat_store.host_ip(vip.ext_ip.split("-")[0])
    value = str(h.get("value") or "")
    for n in data.nats:
        if nat_store.is_balancer(n) and n.name == lb_name.strip():
            if value in nat_store.internal_ips(n):
                return nat_store.host_ip(n.ext_ip)
    return None


def _merge(m: _Conn, c: _Conn) -> None:
    m.reverse = m.reverse and c.reverse
    m.rules.update(c.rules)
    m.starts += [s for s in c.starts if s not in m.starts]
    for a, b in ((m.src, c.src), (m.dst, c.dst)):
        for name, key in zip(b.systems, b.keys or [None] * len(b.systems), strict=False):
            if key and key.startswith("epg:") and a.systems:
                continue  # die EPG als System nur ohne VM bzw. Server darin
            if name not in a.systems:
                a.systems.append(name)
            if key and key not in a.keys:
                a.keys.append(key)


def _same_end(m: _Conn, c: _Conn, net) -> bool:
    """`m` hat dieselbe eigene Seite und denselben Dienst wie `c`, die Gegenseite ist ein Host
    im Netz `net` der Gegenseite von `c`."""
    other = endpoint(_counter(m).ip)
    return (m.service == c.service and m.direction == c.direction
            and _own(m).ip == _own(c).ip and not is_net(other) and within(other, net))  # fmt: skip


def _own(c: _Conn) -> _Side:
    return c.dst if c.direction == "in" else c.src


def _counter(c: _Conn) -> _Side:
    return c.src if c.direction == "in" else c.dst


def _epg_systems(session: Session, doc: dict) -> dict[str, dict]:
    """EPGs der Applikation als System (Applikation ohne VMs): mit der IP ihrer ersten VM
    bzw. ihres ersten Endpunkts."""
    ids = [e["id"] for e in doc["epgs"]]
    vms = epg_store.vms_of(session, ids)
    out = {}
    for e in doc["epgs"]:
        ip = next((i for v in vms.get(e["id"], []) for i in v["ips"]), None)
        if ip is None:
            ip = session.scalar(select(EpgEndpoint.ip).where(EpgEndpoint.epg_id == e["id"])
                                .order_by(EpgEndpoint.ip).limit(1))  # fmt: skip
        if ip is not None:
            nics = [{"epg": e["label"], "ips": [ip]}]
            out[e["key"]] = {"key": e["key"], "name": e["label"], "ips": [ip], "nics": nics}
    return out


def _group_key(rule: Rule, h: dict, cp: _Side, svc) -> tuple:
    """Contract: alle Systeme einer EPG sind eine Verbindung (geprueft mit dem ersten);
    Firewall-Regel ueber ein Netz bzw. eine Gruppe ebenso, ein Host je System."""
    own = h.get("epg") if rule.domain == RuleDomain.ACI_CONTRACT else None
    if own is None and h["scope"] == "specific":
        own = h["system"]
    return (rule.id, h["direction"], own or f"{h['object']}|{h['value']}", cp.ip, svc)


def _copy(s: _Side) -> _Side:
    return _Side(s.ip, s.label, s.kind, list(s.systems), list(s.keys), s.l3out)


def _add_system(side: _Side, system: dict) -> None:
    if system["key"].startswith("epg:") and side.systems:
        return  # die EPG als System nur ohne VM bzw. Server darin
    if system["name"] not in side.systems:
        side.systems.append(system["name"])
        side.keys.append(system["key"])


def _other(side: str) -> str:
    return "src" if side == "dst" else "dst"


def _family(text: str) -> int:
    return endpoint(text).version


def _own_ips(system: dict, value: str | None) -> list[str]:
    """IPs des Systems im Objekt der Regel (ein Host bzw. Netz) - sonst alle, IPv4 zuerst."""
    ips = sorted(dict.fromkeys(system["ips"]), key=lambda i: ":" in i)
    try:
        net = endpoint(str(value or "").split(" ")[0]) if value else None
    except ValueError:
        net = None
    if net is not None:
        inside = [i for i in ips if _inside(i, net)]
        if inside:
            return inside
    return ips


def _inside(ip: str, net) -> bool:
    try:
        x = endpoint(ip)
    except ValueError:
        return False
    return within(x, net) if is_net(net) else x == net


def _nic_ips(system: dict, epg: str | None) -> list[str]:
    """IPs der NICs in der EPG des Contracts (Portgruppe), sonst alle des Systems."""
    ips = [ip for n in system.get("nics") or [] if epg and n.get("epg") == epg for ip in n["ips"]]
    return ips or sorted(dict.fromkeys(system["ips"]), key=lambda i: ":" in i)


def _ranges(session: Session, rule_ids: set[int]) -> dict[tuple[int, str], list[tuple]]:
    out: dict[tuple[int, str], list[tuple]] = {}
    if not rule_ids:
        return out
    for rid, role, fam, lo, hi, is_any, via in session.execute(
        select(
            RuleIpRange.rule_id,
            RuleIpRange.role,
            RuleIpRange.family,
            RuleIpRange.lo,
            RuleIpRange.hi,
            RuleIpRange.is_any,
            RuleIpRange.via,
        )
        .where(RuleIpRange.rule_id.in_(rule_ids))
        .order_by(RuleIpRange.id)
    ):
        if not is_any:
            out.setdefault((rid, role), []).append((fam, int(lo, 16), int(hi, 16), via))
    return out


def _fw_counter(ranges: list[tuple]) -> list[_Side]:
    """Gegenstellen einer Firewall-Regel: Host bzw. Netz (als Ganzes); ein Bereich, der kein
    Netz ist, mit seiner ersten Adresse."""
    out: list[_Side] = []
    seen: set[str] = set()
    for fam, lo, hi, via in ranges:
        cls = ipaddress.IPv4Address if fam == 4 else ipaddress.IPv6Address
        first, last = cls(lo), cls(hi)
        nets = list(ipaddress.summarize_address_range(first, last))
        if len(nets) == 1:
            net = nets[0]
            text = str(first) if net.num_addresses == 1 else str(net)
            label = via or text
        else:
            checked = L("geprüft mit", "checked with")
            text, label = str(first), f"{via or first} ({first}–{last}, {checked} {first})"
        if text in seen:
            continue
        seen.add(text)
        if label != text and text not in label:
            label = f"{label} ({text})"
        out.append(_Side(text, label, "object"))
        if len(out) >= MAX_COUNTER:
            break
    return out


def _aci_counter(session: Session, data: appdoc.TenantData, h: dict) -> list[_Side]:
    """Gegenstellen eines Contracts im Scope (`parties`, 0.43.2): EPG mit einer IP ihrer VMs
    (Portgruppe) bzw. Endpunkte, sonst ihr Subnetz; externe EPG mit ihren Subnetzen (ohne
    0.0.0.0/0)."""
    dns = list(dict.fromkeys((h.get("parties") or {}).get("counter") or []))[:MAX_COUNTER]
    if not dns:
        return []
    epgs = {e.dn: e for e in session.scalars(select(Epg).where(Epg.tenant_id == data.tenant_id,
                                                                Epg.dn.in_(dns)))}  # fmt: skip
    vms = epg_store.vms_of(session, [e.id for e in epgs.values() if e.kind == "epg"])
    out: list[_Side] = []
    for dn in dns:
        e = epgs.get(dn)
        if e is None or e.kind not in ("epg", "esg", "external"):
            continue
        label = epg_store.inventory_label(e)
        if e.kind == "external":
            for net in _subnets(session, e.id)[:3]:
                if net.prefixlen:
                    out.append(
                        _Side(
                            str(net),
                            f"{L('externe EPG', 'external EPG')} {e.l3out}|{e.name} ({net})",
                            "external",
                            l3out=e.l3out,
                        )  # fmt: skip
                    )
            continue
        names = [v["name"] for v in vms.get(e.id, []) if v["ips"]]
        ip = next((i for v in vms.get(e.id, []) for i in v["ips"]), None)
        if ip is None:
            ip = session.scalar(select(EpgEndpoint.ip).where(EpgEndpoint.epg_id == e.id)
                                .order_by(EpgEndpoint.ip).limit(1))  # fmt: skip
        if ip is None:
            nets = _subnets(session, e.id)
            ip = str(nets[0]) if nets else None
        if ip is None:
            continue
        side = _Side(ip, f"EPG {label}", "epg", names[:MAX_SYSTEMS])
        out.append(side)
    return out


def _subnets(session: Session, epg_id: int) -> list:
    out = []
    for ip in session.scalars(select(EpgSubnet.ip).where(EpgSubnet.epg_id == epg_id)
                              .order_by(EpgSubnet.origin, EpgSubnet.ip)):  # fmt: skip
        try:
            out.append(ipaddress.ip_interface(ip).network)
        except ValueError:
            continue
    return out


def _services(rule: Rule) -> list[tuple[str, int | None] | None]:
    """Dienste fuer den Weg: tcp/udp mit dem ersten Port, icmp; alle Dienste = ohne."""
    out: list = []
    for s in sorted(rule.services, key=lambda x: (x.proto.value, x.port_start or 0)):
        proto = s.proto.value
        if proto in ("tcp", "udp"):
            svc = (proto, s.port_start) if s.port_start else (proto, None)
        elif proto in ("icmp", "icmpv6"):
            svc = ("icmp", None)
        else:
            svc = None
        if svc not in out:
            out.append(svc)
    if not out or None in out:
        return [None]
    return out[:MAX_SERVICES]


def _named(text: str) -> list[tuple[str, int | None] | None]:
    """Dienste der Rueckrichtung (`ip/any`, `tcp/any`, `icmp/any` … aus dem Treffer, 0.69.1);
    der Zusatz „(Rueckrichtung)“ in jeder Sprache (`appdoc.REVERSE_MARKS`)."""
    out: list = []
    text = text or ""
    for mark in appdoc.REVERSE_MARKS:
        text = text.replace(mark, "")
    for name in text.split(","):
        proto, _, port = name.strip().lower().partition("/")
        if proto in ("tcp", "udp"):
            svc = (proto, int(port)) if port.isdigit() else (proto, None)
        elif proto.startswith("icmp"):
            svc = ("icmp", None)
        else:
            svc = None
        if svc not in out:
            out.append(svc)
    if not out or None in out:
        return [None]
    return out[:MAX_SERVICES]


def _start(data: appdoc.TenantData, rule: Rule, h: dict, by_label: dict[str, Epg]) -> str | None:
    """Startpunkt, falls die Quelle an keinem gelesenen Geraet haengt: das Geraet der Regel
    (FortiGate: VDOM; ACI: VRF der EPG des Systems)."""
    if rule.domain == RuleDomain.FIREWALL:
        return f"{rule.source_system_id}|{rule.vdom or 'root'}"
    epg = by_label.get(h.get("epg") or "")
    vrf = data.aci.vrf_of(epg) if epg is not None else None
    return f"{rule.source_system_id}|{vrf}" if vrf else None


# -- Weg und Abgleich -------------------------------------------------------------------


def _service_text(svc) -> str | None:
    if svc is None:
        return None
    return svc[0] if svc[1] is None else f"{svc[0]}/{svc[1]}"


def _connection(
    session: Session,
    tid: int,
    graph: paths.Graph,
    checks: policy_check.Checks,
    data: appdoc.TenantData,
    rules: dict[int, Rule],
    c: _Conn,
    l3: "L3Outs | None" = None,
    zctx=None,
) -> dict:
    # mit Backup-Wegen gleicher Laenge (slack 0): uneinheitliche Wege (0.73.0)
    res = paths.trace(session, tid, c.dst.ip, c.src.ip, None, 0, c.service, graph=graph,
                      checks=checks)  # fmt: skip
    start = None
    if not res["paths"]:
        for key in c.starts:
            if paths.parse_key(key) in graph.nodes:
                res = paths.trace(session, tid, c.dst.ip, c.src.ip, key, 0, c.service,
                                  graph=graph, checks=checks)  # fmt: skip
                if res["paths"]:
                    start = graph.nodes[paths.parse_key(key)].label
                    break
    if zctx is not None:  # Zonen je Hop und Absicht zwischen Quell- und Zielzone (0.78.0)
        zctx.annotate(res, c.src.ip, c.dst.ip, c.service)
    active = [p for p in res["paths"] if p.get("kind") != "backup"]
    first = active[0] if active else None
    hops = [_hop(h, l3) for h in first["hops"]] if first else []
    verdict, text = _verdict(active, res["notes"])
    reached = bool(active) and all(p["end"]["kind"] in paths.REACHED for p in active)
    states = [_state(rules[rid], active, data) for rid in c.rules if rid in rules]
    mismatch = _mismatch(states, verdict, text, reached)
    variants = path_variants(res["paths"])
    parts = _net_parts(c, first)
    return {
        "key": f"{c.src.ip}>{c.dst.ip}>{_service_text(c.service) or 'alle'}",
        "direction": c.direction,
        "reverse": c.reverse,
        "src": _side_out(c.src, graph),
        "dst": _side_out(c.dst, graph),
        "service": _service_text(c.service),
        "verdict": verdict,
        "text": text,
        "reached": reached,
        "end": first["end"] if first else None,
        "start": start,
        "paths": len(active),
        "parallel": first.get("parallel") if first else None,
        "hops": hops,
        "rules": states,
        "mismatch": mismatch,
        # Wege uneinheitlich (0.73.0): je Weg bzw. Firewall im Verbund das Urteil
        "variants": variants if len(variants) > 1 else [],
        "inconsistent": inconsistent(variants),
        "notes": res["notes"],
        "zones": _zones_out(res.get("zones"), first),
        # Teile des Ziel-Netzes, die genauer anders geroutet werden (eigene Verbindungen)
        "parts": parts,
    }


def _net_parts(c: _Conn, first: dict | None) -> list[str]:
    """Genauer geroutete Teile des Ziels, wenn es ein Netz ist: die Route, die das ganze Netz
    enthaelt (z. B. 0.0.0.0/0), gilt fuer sie nicht (0.79.1)."""
    if first is None or c.dst.kind != "object" or not is_net(endpoint(c.dst.ip)):
        return []  # nur Netze der Firewall-Regeln (EPG-Subnetze nicht in Host-Routen zerlegen)
    out: list[str] = []
    for h in first["hops"]:
        for r in (h.get("route") or {}).get("inner") or []:
            net = endpoint(r["prefix"])
            host = net.prefixlen == (32 if net.version == 4 else 128)
            if not host and r["prefix"] not in out and r["prefix"] != c.dst.ip:
                out.append(r["prefix"])
    return out


def _zones_out(zones: dict | None, first: dict | None) -> dict | None:
    """Zonen einer Verbindung kompakt: Quelle/Ziel, Kette des Wegs mit Uebergaengen, Absichten
    mit Urteil; `violation` = mindestens eine Absicht verletzt."""
    if not zones:
        return None
    steps = (first or {}).get("zones") or []
    # nur „ohne Zone“ sagt nichts: dann ohne Kette (Quelle/Ziel reichen)
    chain = [
        {k: e.get(k) for k in ("name", "code", "color", "transition", "role")}
        for e in steps
    ] if any(e.get("id") is not None for e in steps) else []  # fmt: skip
    intents = [
        {k: i.get(k) for k in ("src_name", "dst_name", "intent", "services", "verdict", "text")}
        for i in zones.get("intents") or []
    ]
    if not chain and not intents and not zones.get("src") and not zones.get("dst"):
        return None
    return {
        "src": [z["name"] for z in zones.get("src") or []],
        "dst": [z["name"] for z in zones.get("dst") or []],
        "chain": chain,
        "intents": intents,
        "violation": any(i["verdict"] == "violation" for i in intents),
    }


def _side_out(s: _Side, graph: paths.Graph) -> dict:
    names = graph.names(s.ip) if s.kind != "system" else []
    return {
        "ip": s.ip,
        "label": s.label,
        "kind": s.kind,
        "name": names[0] if names else None,
        "systems": s.systems[:MAX_SYSTEMS],
        "more": max(0, len(s.systems) - MAX_SYSTEMS),
        "keys": s.keys,
        "l3out": s.l3out,
    }


def _verdict(active: list[dict], notes: list[str]) -> tuple[str, str]:
    if not active:
        return "unknown", "; ".join(notes) or L("kein Weg gefunden", "no path found")
    policies = [p.get("policy") for p in active]
    verdicts = {x["verdict"] if x else "unknown" for x in policies}
    if len(verdicts) == 1:
        one = policies[0]
        if one:
            return one["verdict"], one["text"]
        return "unknown", L("keine Regeln auf dem Weg", "no rules on the path")
    if "allow" in verdicts and verdicts & {"deny"}:
        return "depends", L(
            "je nach Weg (ECMP) erlaubt bzw. gesperrt",
            "allowed or blocked depending on the path (ECMP)",
        )
    return "depends", "; ".join(dict.fromkeys(x["text"] for x in policies if x))


def path_variants(found: list[dict]) -> list[dict]:
    """Urteil je Weg (aktiv, ECMP, Backup) und - wenn die Firewalls eines Verbunds verschieden
    urteilen - je Firewall: [{kind, via, verdict, text, reason}] (0.73.0)."""
    out: list[dict] = []
    for p in found:
        kind = "backup" if p.get("kind") == "backup" else "active"
        reason = (p.get("deviation") or {}).get("reason")
        checked = [h for h in p["hops"] if h.get("rules")]
        split = next((h for h in checked if len({_part_verdict(x) for x in _parts(h)}) > 1), None)
        for part in _parts(split) if split else [None]:
            seq = []
            for h in checked:
                x = part if h is split else h
                if x.get("rules"):
                    seq.append((x["label"], x["rules"]["verdict"], x["kind"]))
            overall = policy_check._overall(seq) or {
                "verdict": "unknown",
                "text": L("keine Regeln", "no rules"),
            }
            via = " → ".join(label for label, _v, _k in seq) or "–"
            out.append({"kind": kind, "via": via, "verdict": overall["verdict"],
                        "text": overall["text"], "reason": reason})  # fmt: skip
    return out


def _part_verdict(x: dict) -> str | None:
    return (x.get("rules") or {}).get("verdict")


def inconsistent(variants: list[dict]) -> bool:
    """Offen ueber einen Weg, gesperrt ueber einen anderen (0.73.0)."""
    verdicts = {v["verdict"] for v in variants}
    return "allow" in verdicts and bool(verdicts & set(_BLOCK))


def _parts(h: dict) -> list[dict]:
    return h.get("parts") or [h]


def _hop(h: dict, l3: L3Outs | None = None) -> dict:
    """Hop fuer die Anzeige: je Firewall (bzw. Variante im Verbund) das Urteil mit der
    entscheidenden Policy bzw. dem Contract und den Objekten (Quelle, Ziel, Dienste); im ACI
    die genutzten L3Outs (Routing vor den externen EPGs der Pruefung, 0.74.0)."""
    checks = []
    for p in _parts(h):
        r = p.get("rules")
        if not r:
            continue
        # ACI: L3Out des Ein- bzw. Ausgangs nach dem Routing (0.74.0)
        vrf = h["kind"] == "vrf" and l3 is not None
        l3_in = l3.match(h["source_system_id"], h["context"], p.get("ingress")) if vrf else None
        l3_out = l3.match(h["source_system_id"], h["context"], p.get("egress")) if vrf else None
        checks.append(
            {
                "label": p["label"],
                "verdict": r["verdict"],
                "text": r["text"],
                "src_epg": r.get("src_epg"),
                "dst_epg": r.get("dst_epg"),
                "rules": [
                    {
                        k: e.get(k)
                        for k in (
                            "rule_id",
                            "external_id",
                            "name",
                            "label",
                            "kind",
                            "action",
                            "src",
                            "dst",
                            "services",
                            "unsure",
                            "decides",
                        )
                    }  # fmt: skip
                    for e in r["rules"]
                ],
                "ingress": (p.get("ingress") or {}).get("interface"),
                "egress": (p.get("egress") or {}).get("interface"),
                "nat": p.get("nat"),
                "snat": p.get("snat"),
                "leak": p.get("leak"),
                "l3out_in": l3_in,
                "l3out_out": l3_out,
                "l3outs": list(r.get("l3outs") or []),  # der externen EPGs der Pruefung
            }
        )
    return {
        "label": h["label"],
        "kind": h["kind"],
        "key": h["key"],
        "source_system_id": h["source_system_id"],
        "context": h["context"],
        "verdict": _hop_verdict(checks),
        "checks": checks,
        **({"zone": h["zone"]["name"]} if h.get("zone") else {}),
        # NSX ALB (0.77.0): an diesem Hop vom Load Balancer weiter zum Server; seit 0.85.0 mit
        # der EPG bzw. dem Interface, an dem seine VIP liegt (Netzwerkplan)
        **({"lb": _balancer(h, checks, l3)} if h.get("lb") else {}),
    }


def _balancer(h: dict, checks: list[dict], l3: "L3Outs | None") -> dict:
    """Load Balancer am Hop mit dem Ort seiner VIP: im ACI die EPG (statischer Endpunkt,
    Portgruppe, gelernt, Subnetz), sonst das Interface der Firewall zur VIP."""
    lb = dict(h["lb"])
    ip = nat_store.host_ip(lb.get("ext"))
    loc = l3.locator if l3 is not None else None
    if h["kind"] == "vrf" and loc is not None and ip:
        epgs, _how = loc.locate(ip, h["source_system_id"], h["context"])
        if epgs:
            lb["epg"] = epg_store.inventory_label(epgs[0])
    elif checks and checks[0].get("egress"):
        lb["interface"] = checks[0]["egress"]
    return lb


def _hop_verdict(checks: list[dict]) -> str | None:
    verdicts = {c["verdict"] for c in checks}
    if not verdicts:
        return None
    if len(verdicts) == 1:
        return verdicts.pop()
    return "deny" if verdicts <= set(_BLOCK) else "depends"


def _rule_label(rule: Rule) -> str:
    if rule.domain == RuleDomain.FIREWALL:
        quoted = L(f" „{rule.name}“", f" “{rule.name}”") if rule.name else ""
        return f"Policy {rule.external_id}" + quoted
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    if raw.get("kind") == "taboo":
        return f"Taboo {rule.name}"
    return rule.name if raw.get("kind") == "preferred_group" else f"Contract {rule.name}"


def _state(rule: Rule, active: list[dict], data: appdoc.TenantData) -> dict:
    """Regel der Doku auf dem Weg: decides | possible | other (andere entscheidet vorher bzw.
    sie passt nicht) | off_path (ihr Geraet liegt nicht auf dem Weg) | unknown (kein Weg
    bekannt - Routing nicht gelesen bzw. Quelle an keinem gelesenen Geraet)."""
    label = _rule_label(rule)
    base = {"rule_id": rule.id, "label": label, "action": rule.action.value,
            "device": data.device(rule)}  # fmt: skip
    if not active:
        text = L("Weg nicht bekannt – nicht prüfbar", "path not known – not checkable")
        return base | {"state": "unknown", "text": text}
    found = None
    nodes: list[dict] = []
    for path in active:
        for h in path["hops"]:
            for p in _parts(h):
                if p["source_system_id"] != rule.source_system_id:
                    continue
                if rule.domain == RuleDomain.FIREWALL and rule.vdom and p["kind"] == "vdom" \
                        and p["context"] != rule.vdom:  # fmt: skip
                    continue
                nodes.append(p)
                for e in (p.get("rules") or {}).get("rules") or []:
                    if e.get("rule_id") == rule.id:
                        found = found if found and found["decides"] else e
    if found is not None:
        state = "decides" if found["decides"] else "possible"
        if state == "decides":
            text = L("entscheidet auf dem Weg", "decides on the path")
        else:
            text = L("auf dem Weg nur möglich – ", "only possible on the path – ") + ", ".join(
                found.get("unsure") or [])  # fmt: skip
        return base | {"state": state, "text": text}
    if not nodes:
        text = L(f"{base['device']} liegt nicht auf dem Weg dieser Verbindung",
                 f"{base['device']} is not on the path of this connection")  # fmt: skip
        return base | {"state": "off_path", "text": text}
    r = nodes[0].get("rules") or {}
    deciding = next((e for e in r.get("rules") or [] if e.get("decides")), None)
    if deciding is not None:
        label = policy_check._label(deciding)
        text = L(f"vorher entscheidet {label}", f"{label} decides earlier")
    else:
        why = r.get("text") or L(
            "Ein-/Ausgang, Quelle, Ziel, Dienst",
            "incoming/outgoing interface, source, destination, service",
        )
        text = L(f"passt auf dem Weg nicht ({why})", f"does not match on the path ({why})")
    return base | {"state": "other", "text": text}


def deviates(state: dict, verdict: str, reached: bool) -> bool:
    """Weicht diese Regel der Verbindung vom Weg ab (Anzeige „Abweichung“, 0.59.0)? Eine
    erlaubende Regel, deren Weg sperrt, die nicht auf dem Weg liegt, dort nicht greift (Weg
    nicht erlaubt) bzw. deren erlaubter Weg vor dem Ziel endet - wie `_mismatch`. Der Befund
    seit 0.73.0: nur uneinheitliche Wege (`inconsistent`)."""
    if state["action"] != "allow":
        return False
    return (verdict == "deny" or state["state"] == "off_path"
            or (state["state"] == "other" and verdict != "allow")
            or (verdict == "allow" and not reached))  # fmt: skip


def _mismatch(states: list[dict], verdict: str, text: str, reached: bool) -> list[str]:
    """Abweichungen Regel <-> Weg: eine erlaubende Regel, deren Verbindung der Weg sperrt bzw.
    die nicht auf dem Weg liegt oder dort nicht greift."""
    out = []
    allowing = [s for s in states if s["action"] == "allow"]
    if allowing and verdict == "deny":
        who = ", ".join(s["label"] for s in allowing[:3])
        out.append(L(f"{who} erlaubt – der Weg ist {text}", f"{who} allows – the path is {text}"))
    for s in allowing:
        if s["state"] == "off_path":
            out.append(f"{s['label']}: {s['text']}")
        elif s["state"] == "other" and verdict != "allow":
            out.append(f"{s['label']}: {s['text']}")
    if allowing and verdict == "allow" and not reached:
        out.append(
            L(
                "erlaubt, aber der Weg endet vor dem Ziel",
                "allowed, but the path ends before the destination",
            )
        )
    return out
