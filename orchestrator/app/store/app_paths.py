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
  Partner), beginnt der Weg seit 0.107.0 am Eingang nach dem Rueckweg (`entries_of`: wo der Weg
  vom Ziel zur Quelle das gelesene Netz verlaesst), sonst auf dem Geraet der Regel.
- **Abgleich Regel <-> Weg:** je Regel, aus der die Verbindung stammt, ob sie auf dem Weg
  entscheidet, nur moeglich ist, vorher eine andere entscheidet bzw. nicht passt, oder ob ihr
  Geraet gar nicht auf dem Weg liegt. Erlaubt eine Regel und sperrt der Weg, ist die Verbindung
  als Abweichung markiert - der Fall „Regel zeigt Zugriff, Weg-Suche sperrt“ (Befund 0.43.2).
  Liegt ihr Geraet nicht auf dem Weg bis zum Ziel, gehoert sie nicht zur Verbindung (0.107.0,
  Anwender): `unused` bzw. Befund `app_rule_unused` („Regel nie gebraucht“); Verbindungen nur
  aus solchen Regeln entfallen.
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
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.i18n import L
from app.models import (
    Application,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    NatRule,
    Rule,
    RuleDomain,
    RuleIpRange,
    TopoL3Out,
)
from app.store import (
    aci_direction,
    appdoc,
    epg_locate,
    net_assign,
    path_service,
    paths,
    policy_check,
    return_path,
)
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store.topology import endpoint, is_net, within

MAX_TRACES = 120  # Wege je Applikation in den Befunden (Vorgabe, `app_findings_traces`)
MAX_COUNTER = 6  # Gegenstellen je Regel und Seite
MAX_PARTS = 8  # genauer geroutete Teile eines Netzes als eigene Verbindungen (0.79.1)
MAX_SERVICES = 3  # Dienste je Regel
MAX_SYSTEMS = 12  # genannte Systeme je Seite
_SKIP = ("any", "vzany", "pref_group")  # unspezifisch: Regel ohne Verbindung
_BLOCK = ("deny", "implicit_deny")

log = logging.getLogger(__name__)


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
    # Gegenstelle any der Firewall-Regel (0.127.0): geprueft mit dem, was hinter ihrem Interface
    # liegt - liegt die Firewall nicht auf dem Weg, ist das kein Befund „nie gebraucht“
    probe: bool = False


PROGRESS_SECONDS = 60  # Fortschritt im Log hoechstens so oft (0.133.1)


def build(session: Session, app: Application, doc: dict | None = None,
          graph: paths.Graph | None = None,
          checks: policy_check.Checks | None = None, zones: bool = True,
          limit: int | None = None, progress=None) -> dict:  # fmt: skip
    """Verbindungen der Applikation mit Weg, Urteil je Hop und Abgleich mit den Regeln. Graph
    und Policies lassen sich fuer mehrere Applikationen teilen (Befunde, 0.59.0); `zones`:
    logische Zonen je Verbindung und Hop mit der Absicht (0.78.0, ADR 0024) und der Rückweg
    (0.136.0) - die Befunde brauchen beides nicht. `limit`: so viele Wege (Vorgabe
    `app_traces_max`, 0 = alle, 0.130.0) - Verbindungen zu any zuletzt. `progress(fertig,
    alle)` je Weg (Stand der Doku, 0.133.1).

    0.133.1 (Anwender: „woran kann es liegen, dass bei mir es ewig dauert und bei dir
    Sekunden?“): `timing.queries` = Abfragen an die Datenbank waehrend der Berechnung."""
    from sqlalchemy import event

    conn = session.connection()
    queries = [0]

    def count(*_args) -> None:
        queries[0] += 1

    event.listen(conn, "before_cursor_execute", count)
    try:
        out = _build(session, app, doc, graph, checks, zones, limit, progress)
    finally:
        event.remove(conn, "before_cursor_execute", count)
    if "timing" in out:
        out["timing"]["queries"] = queries[0]
    return out


def _build(session: Session, app: Application, doc: dict | None, graph: paths.Graph | None,
           checks: policy_check.Checks | None, zones: bool, limit: int | None,
           progress) -> dict:  # fmt: skip
    from app.config import get_settings
    from app.store import (
        applications,  # spaet: applications nutzt appdoc wie dieses Modul
        zone_path,  # spaet: Zonen nutzen Kunden, diese die Applikationen
    )

    limit = get_settings().app_traces_max if limit is None else limit
    tid = app.tenant_id
    started = time.monotonic()  # Zeit je Teil (0.132.1: „dauert sehr lange mehrere Stunden“)
    doc = doc or applications.build_doc(session, app)
    t_doc = time.monotonic()
    data = appdoc.TenantData(session, tid)
    rule_ids = {h["rule_id"] for h in doc["hits"]}
    rules = {
        r.id: r
        for r in session.scalars(
            select(Rule).where(Rule.id.in_(rule_ids)).options(selectinload(Rule.services))
        )
    }
    conns, notes, without = _candidates(session, data, doc, rules, limit)
    t_cand = time.monotonic()
    graph = graph or paths.Graph(session, tid)
    checks = checks or policy_check.Checks.load(session, tid)
    out: list[dict] = []
    cut = bool(limit) and len(conns) > limit
    l3 = L3Outs(session, {k[0] for k in graph.nodes})
    l3.locator = epg_locate.Locator(session, tid, data.aci, data.epgs_by_id)
    zctx = zone_path.ZoneContext(session, tid, graph) if zones else None
    assigner = net_assign.Assigner(session, tid)  # grosse Netze der Regeln (0.111.0)
    unused: list[dict] = []  # Regeln nie auf dem Weg ihrer Verbindung (0.107.0)

    def keep(conn: dict) -> bool:
        """Verbindung zeigen? Nicht, wenn keine ihrer Regeln auf ihrem Weg liegt."""
        hops = " → ".join(h["label"] for h in conn["hops"]) or "–"
        ends = {"src": conn["src"]["label"], "dst": conn["dst"]["label"]}
        unused.extend({"rule_id": x["rule_id"], "label": x["label"], "device": x["device"],
                       **ends, "service": conn["service"], "path": hops}
                      for x in conn["unused"])  # fmt: skip
        return bool(conn["rules"]) or not conn["unused"]

    t_setup = time.monotonic()
    slow: list[tuple[float, str]] = []
    traced = 0
    todo = list(conns.values())[: limit or None]
    logged = t_setup
    failed: list[str] = []  # Wege mit Fehler im Programm (0.133.2)

    def one(x: _Conn, with_assign: bool) -> dict | None:
        """Weg einer Verbindung; ein Fehler im Programm bricht nicht mehr die ganze Berechnung
        ab (0.133.2, Anwender: „Er bricht immer bei dieser Anzahl ab“), sondern fehlt mit einem
        Hinweis - die Datenbank (Transaktion unbrauchbar) bricht weiterhin ab."""
        try:
            return _connection(session, tid, graph, checks, data, rules, x, l3, zctx,
                               assigner if with_assign else None)  # fmt: skip
        except SQLAlchemyError:
            raise
        except Exception:
            log.exception("Applikation %s: Weg %s > %s (%s) fehlgeschlagen", app.id, x.src.ip,
                          x.dst.ip, _service_text(x.service) or "alle")  # fmt: skip
            failed.append(f"{x.src.label} → {x.dst.label} ({_service_text(x.service) or 'alle'})")
            return None

    for c in todo:
        t_one = time.monotonic()
        conn = one(c, True)
        traced += 1
        if conn is None:
            if progress is not None:
                progress(traced, len(todo))
            continue
        slow.append((time.monotonic() - t_one, conn["key"]))
        if progress is not None:
            progress(traced, len(todo))
        if time.monotonic() - logged > PROGRESS_SECONDS:  # lange Berechnung: Fortschritt im Log
            logged = time.monotonic()
            log.info("Applikation %s: %d von %d Wegen in %.0f s", app.id, traced, len(todo),
                     logged - t_setup)  # fmt: skip
        if c.probe:  # any: Firewall nicht auf dem Weg nach dem Routing - kein Befund (0.127.0)
            for x in conn["unused"]:
                without[x["rule_id"]].add(_why("probe_off_path"))
            conn["unused"] = []
            if not conn["rules"]:
                continue
        # nur die genauer gerouteten Teile zaehlen (von Hand, 0.111.0): das ganze Netz nicht
        shown = keep(conn) and (conn.get("assign") or {}).get("state") != "ignore"
        if shown:
            out.append(conn)
        # Netz als Ziel (0.79.1, Anwender): genauer geroutete Teile (z. B. ueber einen anderen
        # L3Out als die 0/0-Route, die das ganze Netz enthaelt) als eigene Verbindungen
        for prefix in conn["parts"][:MAX_PARTS]:
            part = _Conn(_copy(c.src), _copy(c.dst), c.service, c.direction, dict(c.rules),
                         list(c.starts), c.reverse)  # fmt: skip
            part.dst.ip, part.dst.label = prefix, f"{c.dst.label} › {prefix}"
            child = one(part, False)
            if child is None:
                continue
            if shown:
                child["part_of"] = conn["key"]
            if keep(child):
                out.append(child)
    if cut:
        notes.append(
            L(
                f"{len(conns)} Verbindungen – nur die ersten {limit} geprüft (Verbindungen zu "
                "any zuletzt; Grenze AUTOMATO_APP_TRACES_MAX in der .env, 0 = ohne Grenze)",
                f"{len(conns)} connections – only the first {limit} checked (connections to any "
                "last; limit AUTOMATO_APP_TRACES_MAX in the .env, 0 = no limit)",
            )
        )
    if failed:
        more = L(f" und {len(failed) - 3} weitere", f" and {len(failed) - 3} more") \
            if len(failed) > 3 else ""  # fmt: skip
        notes.append(
            L(
                f"{len(failed)} Wege nicht berechnet (Fehler im Programm, Einzelheiten im Log "
                f"des Orchestrators): {', '.join(failed[:3])}{more}",
                f"{len(failed)} paths not computed (program error, details in the orchestrator "
                f"log): {', '.join(failed[:3])}{more}",
            )
        )
    if unused:
        labels = list(dict.fromkeys(f"{x['label']} ({x['device']})" for x in unused))
        more = L(f" und {len(labels) - 3} weitere", f" and {len(labels) - 3} more") \
            if len(labels) > 3 else ""  # fmt: skip
        notes.append(
            L(
                f"Nie auf dem Weg ihres Verkehrs (Befund „Regel nie gebraucht“): "
                f"{', '.join(labels[:3])}{more}",
                f"Never on the path of their traffic (finding “rule never needed”): "
                f"{', '.join(labels[:3])}{more}",
            )
        )
    t_traces = time.monotonic()
    order = {"deny": 0, "depends": 1, "unknown": 2, "allow": 3}
    out.sort(key=lambda c: (not c["mismatch"], order.get(c["verdict"], 2)))
    # Regeln ohne Verbindung (0.127.0, Anwender: „in der Tabelle der Verbindungen … keine Rules
    # von den L3Os, nur in den Regeln und Contracts“): je Regel der Grund
    used = {x["rule_id"] for c in out for x in c["rules"]}
    used |= {x["rule_id"] for x in unused}
    missing = [{"rule_id": rid, "label": _rule_label(rules[rid]), "reasons": sorted(why)}
               for rid, why in without.items() if rid in rules and rid not in used]  # fmt: skip
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
            # Rueckweg ueber eine andere Firewall (0.136.0)
            "asymmetric": sum(
                1 for c in out if (c.get("return") or {}).get("state") == "asymmetric"
            ),  # fmt: skip
            "unused": len({x["rule_id"] for x in unused}),  # 0.107.0
        },
        "unused": unused,
        "without": sorted(missing, key=lambda x: x["label"].lower()),
        "members": members(doc),
        # 0.78.2: L3Out-Contracts, die weniger oeffnen als die Firewall (Befund)
        "narrower": doc.get("narrower") or [],
        # 0.132.1: Dauer je Teil und die langsamsten Wege (Sekunden)
        "timing": {
            "doc": round(t_doc - started, 2),
            "candidates": round(t_cand - t_doc, 2),
            "setup": round(t_setup - t_cand, 2),
            "traces": round(t_traces - t_setup, 2),
            "count": traced,
            "failed": len(failed),  # 0.133.2
            "total": round(time.monotonic() - started, 2),
            "slowest": [
                {"key": k, "seconds": round(d, 2)} for d, k in sorted(slow, reverse=True)[:5]
            ],
        },  # fmt: skip
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


def _why(key: str) -> str:
    """Grund, warum eine Regel der Doku keine Verbindung ergibt (0.127.0)."""
    return {
        "impossible": L("routingtechnisch nicht möglich", "not possible by routing"),
        "unspecific": L("unspezifisch (any, vzAny bzw. Preferred Group auf der Seite der "
                        "Applikation) – keine eigene Verbindung",
                        "unspecific (any, vzAny or preferred group on the application side) – no "
                        "connection of its own"),
        "helper": L("Hilfs-Contract über einen L3Out – geprüft mit der Firewall-Regel dahinter",
                    "helper contract via an L3Out – checked with the firewall rule behind it"),
        "replies": L("nur Antworten (Consumer → Provider)", "replies only (consumer → provider)"),
        "counter": L("Gegenstelle nicht als IP prüfbar (FQDN, ISDB, Geo)",
                     "peer cannot be checked as an IP (FQDN, ISDB, geo)"),
        "counter_aci": L("Gegenstelle ohne prüfbares Netz bzw. Endpunkt (z. B. externe EPG nur "
                         "mit Subnetzen für das Routing)", "peer without a checkable network or "
                         "endpoint (e.g. external EPG with subnets for routing only)"),
        "family": L("keine IP des Systems in der Adressfamilie der Gegenstelle",
                    "no IP of the system in the address family of the peer"),
        "probe_off_path": L("Gegenstelle any: der Weg nach dem Routing führt nicht über die "
                            "Firewall der Regel", "peer any: the path by the routing does not "
                            "pass the firewall of the rule"),
        "limit": L("mehr Verbindungen als geprüft werden", "more connections than are checked"),
    }[key]  # fmt: skip


def _candidates(
    session: Session, data: appdoc.TenantData, doc: dict, rules: dict[int, Rule],
    limit: int = MAX_TRACES,
) -> tuple[dict[tuple, _Conn], list[str], dict[int, set[str]]]:  # fmt: skip
    systems = {x["key"]: x for x in doc["systems"]} | _epg_systems(session, doc)
    ranges, anys = _ranges(session, set(rules))
    without: dict[int, set[str]] = defaultdict(set)  # Regel -> Gruende ohne Verbindung
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
        if h["relevance"] == "impossible":
            without[rule.id].add(_why("impossible"))
            continue
        if h["scope"] in _SKIP:
            without[rule.id].add(_why("unspecific"))
            continue
        if h.get("helper"):
            without[rule.id].add(_why("helper"))
            continue  # Hilfs-Contract ueber einen L3Out (ADR 0034): nur Teil des ACI-Hops
        system = systems[h["system"]]
        if rule.domain == RuleDomain.ACI_CONTRACT and not h.get("reverse"):
            names = [x.canonical_name for x in rule.services]
            if aci_direction.replies_only(rule.raw, names):
                without[rule.id].add(_why("replies"))
                continue  # Consumer -> Provider nur Antworten (0.72.1): keine Verbindung
        front = None  # VIP der Firewall vor dem System (0.137.0: Port des Clients)
        if rule.domain == RuleDomain.ACI_CONTRACT:
            key = f"aci:{rule.id}:{h['side']}:{h.get('epg') or h['system']}"
            if key not in counters:
                counters[key] = _aci_counter(session, data, h)
            own_ips = _nic_ips(system, h.get("epg"))
        else:
            key = f"fw:{rule.id}:{h['side']}"
            cp_side = _other(h["side"])
            if key not in counters:
                counters[key] = _fw_counter(ranges.get((rule.id, cp_side), []))
                if not counters[key] and anys.get((rule.id, cp_side)):
                    # any (0.127.0, Anwender: Regeln nach extern ueber L3Outs fehlten im Plan)
                    counters[key] = _fw_any(data, rule, cp_side, anys[(rule.id, cp_side)])
            own_ips = _own_ips(system, h.get("value"))
            entry = _entry(data, rule, h)  # ueber VIP bzw. Load Balancer (0.79.0)
            if entry is not None:
                own_ips = [entry]
            front = _fw_vip(data, rule, h) if entry is not None else None
        if not counters[key]:
            skipped.add(rule.name or f"#{rule.external_id}")
            aci = rule.domain == RuleDomain.ACI_CONTRACT
            without[rule.id].add(_why("counter_aci" if aci else "counter"))
            continue
        services = _named(h["services"]) if h.get("reverse") else _services(rule)
        if front is not None:
            # Port des Clients statt des Ports nach der Port-Weiterleitung (0.137.0)
            services = list(dict.fromkeys(_client_service(front, x) for x in services))
        families = {_family(i) for i in own_ips}
        if not any(_family(cp.ip) in families for cp in counters[key]):
            without[rule.id].add(_why("family"))
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
                                               reverse=bool(h.get("reverse")),
                                               probe=cp.kind == "any")  # fmt: skip
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
            merged[k].probe = merged[k].probe and c.probe
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
        if net.prefixlen == 0:
            continue  # externe EPG 0.0.0.0/0 (0.127.0): eigene Verbindung ueber ihren L3Out
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
                f"ohne prüfbare Gegenstelle (Grund unter „Regeln ohne Verbindung“): {names}",
                f"without a checkable peer (reason under “rules without a connection”): {names}",
            )
        )
    # Verbindungen zu any zuletzt (0.130.0): ueber der Grenze fallen zuerst sie weg
    merged = dict(sorted(merged.items(), key=lambda kv: kv[1].probe))
    if limit and len(merged) > limit:
        for c in list(merged.values())[limit:]:
            for rid in c.rules:
                without[rid].add(_why("limit"))
    return merged, notes, without


def _entry(data: appdoc.TenantData, rule: Rule, h: dict) -> str | None:
    """Ziel der Verbindung, wenn die Regel das System ueber einen Load Balancer erreicht
    (0.79.0, Treffer „V-EXT_x → vs-web“): die externe IP der VIP der FortiGate davor bzw. die
    VIP des Virtual Service - der Weg geht dann ueber DNAT und Load Balancer zum Server.

    0.135.0 (Anwender: „beides ja“ zur Weg-Suche über die VIP, 0.134.0): ebenso eine VIP direkt
    auf den Server (Treffer über die VIP, ohne Load Balancer) - eine Policy mit der VIP als Ziel
    trifft nur Verkehr zur externen IP, nicht zur Server-IP."""
    text = str(h.get("object") or "")
    if h.get("side") != "dst":
        return None
    vip = _fw_vip(data, rule, h)
    if vip is not None:
        return nat_store.host_ip(vip.ext_ip.split("-")[0])
    if nat_store.CHAIN not in text:
        return None
    _front, _, lb_name = text.partition(nat_store.CHAIN)
    value = str(h.get("value") or "")
    for n in data.nats:
        if nat_store.is_balancer(n) and n.name == lb_name.strip():
            if value in nat_store.internal_ips(n):
                return nat_store.host_ip(n.ext_ip)
    return None


def _fw_vip(data: appdoc.TenantData, rule: Rule, h: dict) -> NatRule | None:
    """VIP der Firewall, über die die Regel das System als Ziel erreicht: direkt (Treffer über
    die VIP, das System ist ihr internes Ziel) bzw. vor dem Load Balancer („V-EXT_x → vs-web“)."""
    text = str(h.get("object") or "")
    if h.get("side") != "dst":
        return None
    chained = nat_store.CHAIN in text
    name = text.partition(nat_store.CHAIN)[0] if chained else text
    vip = data.nat_by_name.get((rule.source_system_id, rule.vdom, name.strip().lower()))
    if vip is None or nat_store.is_balancer(vip) or not vip.ext_ip:
        return None
    if not chained:
        server = nat_store.host_ip(str(h.get("value") or ""))
        if server not in {nat_store.host_ip(v) for v in nat_store.internal_ips(vip)}:
            return None
    return vip


def _client_service(vip: NatRule | None, svc):
    """Dienst der Regel als Port des Clients (0.137.0, Anwender: „Service Feld nicht genau
    definiert, wo es hin gehört“): die FortiGate prüft die Policy nach der Port-Weiterleitung
    der VIP - der Client nutzt den externen Port (443 statt 8443)."""
    if vip is None or not svc or svc[1] is None:
        return svc
    port = path_service.reverse_vip(vip, svc[1])
    return (svc[0], port) if port is not None else svc


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


Ranges = dict[tuple[int, str], list[tuple]]


def _ranges(session: Session, rule_ids: set[int]) -> tuple[Ranges, dict[tuple[int, str], set[int]]]:
    """Bereiche je Regel und Seite (ohne any) und die Adressfamilien mit any (0.127.0)."""
    out: dict[tuple[int, str], list[tuple]] = {}
    anys: dict[tuple[int, str], set[int]] = defaultdict(set)
    if not rule_ids:
        return out, anys
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
        else:
            anys[(rid, role)].add(fam)
    return out, anys


def _fw_any(data: appdoc.TenantData, rule: Rule, side: str, families: set[int]) -> list[_Side]:
    """Gegenstelle any einer Firewall-Regel (0.127.0, Anwender: „viele Firewall Regeln nach
    extern, über L3Os, diese werden aber nicht angezeigt im Netzwerkplan“): was hinter Interface
    bzw. Zone der Regel auf dieser Seite liegt - die Netze dorthin (Routing bzw. Anbindung),
    sonst die Default-Route (0.0.0.0/0 bzw. ::/0)."""
    zone = data.zones.get(rule.src_zone_id if side == "src" else rule.dst_zone_id)
    found = data.relevance.behind(rule, side, zone)
    nets = []
    for n in (found or {}).get("nets") or []:
        try:
            net = ipaddress.ip_network(str(n), strict=False)
        except ValueError:
            continue
        if net.version in families and net.prefixlen and str(net) not in nets:
            nets.append(str(net))
    # Transfernetze (/29 und kleiner) nur, wenn sonst nichts dahinter liegt
    # 0.133.2: als Netz lesen - `endpoint` gaebe bei /32 bzw. /128 die Adresse ohne Praefixlaenge
    wide = [n for n in nets if ipaddress.ip_network(n).prefixlen < (29 if ":" not in n else 125)]
    nets = wide or nets
    if nets:
        return [_Side(n, f"any → {n}", "any") for n in nets[:MAX_COUNTER]]
    text = f"any → {found['text']}" if found and found.get("text") else "any"
    return [_Side("0.0.0.0/0" if fam == 4 else "::/0", text, "any") for fam in sorted(families)]


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
    0.0.0.0/0; nur mit 0.0.0.0/0 seit 0.127.0 dieses - der Weg ueber ihren L3Out)."""
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
            nets = _subnets(session, e.id)
            if nets and not any(n.prefixlen for n in nets):
                nets = nets[:2]  # nur 0.0.0.0/0 (Internet, 0.127.0): ueber den L3Out pruefen
            else:
                nets = [n for n in nets if n.prefixlen][:3]
            label = f"{L('externe EPG', 'external EPG')} {e.l3out}|{e.name}"
            out += [_Side(str(net), f"{label} ({net})", "external", l3out=e.l3out)
                    for net in nets]  # fmt: skip
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
    from app.store.aci_relevance import assigns

    out = []
    for ip, origin, scope in session.execute(
            select(EpgSubnet.ip, EpgSubnet.origin, EpgSubnet.scope)
            .where(EpgSubnet.epg_id == epg_id)
            .order_by(EpgSubnet.origin, EpgSubnet.ip)):  # fmt: skip
        if not assigns(origin, scope):
            continue  # nur Routing (Export Route Control) - kein Netz der externen EPG
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
    text = text.split("(↔")[0]  # Zusatz „auch vom Provider aus: …“ (0.112.0) nennt keine eigenen
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
    assigner: "net_assign.Assigner | None" = None,
) -> dict:
    res, start = connection_trace(session, tid, graph, checks, c.src.ip, c.dst.ip, c.service,
                                  c.starts)  # fmt: skip
    if zctx is not None:  # Zonen je Hop und Absicht zwischen Quell- und Zielzone (0.78.0)
        zctx.annotate(res, c.src.ip, c.dst.ip, c.service)
        # Rueckweg je aktivem Weg (0.136.0, Anwender: „Ja“ - wie in der Weg-Suche); die
        # Befunde (ohne Zonen) brauchen ihn nicht
        return_path.annotate(session, tid, graph, res, c.src.ip, c.dst.ip, nets=True,
                             backups=False)  # fmt: skip
    active = [p for p in res["paths"] if p.get("kind") != "backup"]
    first = active[0] if active else None
    hops = [_hop(h, l3) for h in first["hops"]] if first else []
    verdict, text = _verdict(active, res["notes"])
    reached = bool(active) and all(p["end"]["kind"] in paths.REACHED for p in active)
    states = [_state(rules[rid], active, data) for rid in c.rules if rid in rules]
    # Regel auf einem Geraet, ueber das der (bis zum Ziel bekannte) Weg nicht geht: diese
    # Verbindung braucht sie nie (0.107.0, Anwender: „eher ein Finding … aber eigentlich sollte
    # dies nicht dort auftauchen“) - nicht bei der Verbindung, sondern Befund `app_rule_unused`
    unused = [x for x in states if x["state"] == "off_path"] if reached else []
    states = [x for x in states if x not in unused]
    mismatch = _mismatch(states, verdict, text, reached)
    variants = path_variants(res["paths"])
    parts = _net_parts(c, first)
    assign = _assign(c, first, assigner)
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
        "unused": unused,
        "mismatch": mismatch,
        # Wege uneinheitlich (0.73.0): je Weg bzw. Firewall im Verbund das Urteil
        "variants": variants if len(variants) > 1 else [],
        "inconsistent": inconsistent(variants),
        "notes": res["notes"],
        # Rueckweg ueber dieselben Firewalls (0.136.0, ausser ACI und Verbund)
        "return": return_path.summary(active),
        # Ports entlang des Wegs, wenn sie sich aendern: Client, VIP, Load Balancer, Server
        "service_chain": res.get("service_chain"),
        "zones": _zones_out(res.get("zones"), first),
        # Teile des Ziel-Netzes, die genauer anders geroutet werden (eigene Verbindungen)
        "parts": parts,
        # Netz groesser als die Routen (0.111.0, ADR 0040): wohin es gehoert
        "assign": assign,
        # Eingaben des Wegs fuer „Warum dieser Weg?“ (0.109.0, `GET /routing/explain`)
        "trace": {
            "src": c.src.ip,
            "dst": c.dst.ip,
            "service": _service_text(c.service),
            "starts": list(c.starts),
        },  # fmt: skip
    }


def connection_trace(session: Session, tid: int, graph: paths.Graph,
                     checks: policy_check.Checks | None, src: str, dst: str,
                     service: tuple[str, int | None] | None, starts: list[str]
                     ) -> tuple[dict, str | None]:  # fmt: skip
    """Weg einer Verbindung (auch fuer „Warum dieser Weg?“, 0.109.0): ab der Quelle; haengt sie
    an keinem gelesenen Geraet, ab dem Eingang nach dem Rueckweg (0.107.0), sonst ab dem Geraet
    der Regel. Mit Backup-Wegen gleicher Laenge (slack 0): uneinheitliche Wege (0.73.0).
    -> (Ergebnis von `paths.trace`, Start falls nicht an der Quelle)."""
    res = paths.trace(session, tid, dst, src, None, 0, service, graph=graph,
                      checks=checks)  # fmt: skip
    start = None
    if not res["paths"]:
        # Quelle an keinem gelesenen Geraet (0.107.0, Anwender: Wege, „welche Routing technisch
        # keinen Sinn ergeben“): der Weg beginnt, wo der Rueckweg das gelesene Netz verlaesst -
        # nicht auf irgendeinem Geraet mit einer Regel dafuer
        entries = entries_of(session, tid, graph, checks, src, dst)
        found = paths.trace(session, tid, dst, src, None, 0, service, graph=graph,
                            checks=checks, entries=entries) if entries else None  # fmt: skip
        if found and found["paths"]:
            res = found
            start = ", ".join(dict.fromkeys(graph.nodes[k].label for k, *_ in entries))
    if not res["paths"]:
        for key in starts:
            if paths.parse_key(key) in graph.nodes:
                res = paths.trace(session, tid, dst, src, key, 0, service, graph=graph,
                                  checks=checks)  # fmt: skip
                if res["paths"]:
                    start = graph.nodes[paths.parse_key(key)].label
                    break
    return res, start


def entries_of(session: Session, tid: int, graph: paths.Graph, checks: policy_check.Checks,
               src: str, dst: str) -> list[tuple]:  # fmt: skip
    """Eingang einer Quelle ausserhalb der gelesenen Geraete nach dem Rueckweg (0.107.0): der
    Weg vom Ziel zur Quelle verlaesst das gelesene Netz ueber eine Anbindung bzw. einen Router
    ohne gelesenes Geraet - dort kommt der Verkehr der Quelle herein. Je aktivem Weg der letzte
    Knoten mit seinem Ausgang als Eingang: (Knoten, Interface, Zone, wie erreicht) fuer
    `paths.trace(entries=)`. Leer ohne Rueckweg (Routing nicht gelesen, Ziel nicht
    angeschlossen)."""
    back = paths.trace(session, tid, src, dst, None, 0, None, graph=graph, checks=checks,
                       backups=False)  # fmt: skip
    via = L("Eingang nach dem Rückweg", "entry by the return path")
    out: list[tuple] = []
    for p in back["paths"]:
        end = p.get("end") or {}
        if not p["hops"] or p.get("kind") == "backup":
            continue
        for part in _parts(p["hops"][-1]):
            egress = part.get("egress") or {}
            left = end.get("kind") in ("exit", "external") or (
                end.get("kind") == "unknown" and egress.get("next_hop"))  # fmt: skip
            key = paths.parse_key(part.get("key"))
            if not left or key not in graph.nodes:
                continue
            entry = (key, egress.get("interface"), egress.get("zone"), via)
            if entry[:2] not in [e[:2] for e in out]:
                out.append(entry)
    return out


def _assign(c: _Conn, first: dict | None, assigner) -> dict | None:
    """Ziel-Netz der Regel, das ein Geraet auf dem Weg nicht als Ganzes routet (0.111.0):
    Zuordnung von Hand, nach Zone bzw. Anbindung, sonst Teile nach dem Routing bzw. unklar."""
    if assigner is None or first is None or c.dst.kind != "object":
        return None
    net = net_assign.as_net(c.dst.ip)
    if net is None:
        return None
    for h in first["hops"]:
        route = h.get("route")
        if net_assign.wider(net, route):
            return assigner.decide(net, route).out() | {"device": h.get("label")}
    return None


def _net_parts(c: _Conn, first: dict | None) -> list[str]:
    """Genauer geroutete Teile des Ziels, wenn es ein Netz ist: die Route, die das ganze Netz
    enthaelt (z. B. 0.0.0.0/0), gilt fuer sie nicht (0.79.1). Nicht fuer any (0.127.0)."""
    if first is None or c.dst.kind != "object" or not is_net(endpoint(c.dst.ip)):
        return []  # nur Netze der Firewall-Regeln (EPG-Subnetze nicht in Host-Routen zerlegen)
    out: list[str] = []
    for h in first["hops"]:
        for r in (h.get("route") or {}).get("inner") or []:
            # Hostroute (/32 bzw. /128): `endpoint` gibt die Adresse - kein eigener Teil (0.133.2,
            # Anwender: „Er bricht bei 391 von 808 Wegen ab“ - die Adresse hat keine Praefixlaenge)
            host = not is_net(endpoint(r["prefix"]))
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
    # 0.112.0 (Anwender: „ich weiss nicht, warum es dort abhängig sagt“): je aktivem Weg (ECMP,
    # mehrere Starts) sein Urteil - der gezeigte erste kann stimmen, ein anderer nicht
    ways = []
    none = L("keine Regeln auf dem Weg", "no rules on the path")
    for p, x in zip(active, policies, strict=True):
        via = " → ".join(h["label"] for h in p["hops"] if h.get("rules")) or "–"
        ways.append(f"{via}: {x['text'] if x else none}")
    n = len(active)
    head = L(f"{n} aktive Wege verschieden – ", f"{n} active paths differ – ")
    return "depends", head + "; ".join(dict.fromkeys(ways))


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
                if r := x.get("rules"):
                    seq.append((x["label"], r["verdict"], x["kind"], r.get("text")))
            overall = policy_check._overall(seq) or {
                "verdict": "unknown",
                "text": L("keine Regeln", "no rules"),
            }
            via = " → ".join(x[0] for x in seq) or "–"
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
                        **{
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
                        },
                        # ACI: greift nicht (nur Antworten) bzw. oeffnet den Dienst nicht (0.111.0)
                        # Sperrliste (0.132.2): Internet Service bzw. Land, nicht „abhaengig“
                        **{k: e[k] for k in ("note", "closed", "blocklist") if e.get(k)},
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
        # Verbund ueber mehrere Firewalls (0.133.0): der Knoten im Netzwerkplan gehoert dem
        # Verbund, nicht der ersten Firewall darin (sonst je nach Weg zweimal „Verbund …“)
        **(
            {"group": h["group"]}
            if h.get("group") and len({p["key"] for p in _parts(h)}) > 1
            else {}
        ),  # fmt: skip
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


def rule_nodes(rule: Rule, active: list[dict]) -> list[dict]:
    """Hops der Wege (je Firewall im Verbund), die das Geraet der Regel sind - FortiGate mit
    ihrem VDOM. Leer = die Regel liegt nicht auf dem Weg (0.107.0; Doku seit 0.113.2)."""
    out: list[dict] = []
    for path in active:
        for h in path["hops"]:
            for p in _parts(h):
                if p["source_system_id"] != rule.source_system_id:
                    continue
                if rule.domain == RuleDomain.FIREWALL and rule.vdom and p["kind"] == "vdom" \
                        and p["context"] != rule.vdom:  # fmt: skip
                    continue
                out.append(p)
    return out


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
    nodes = rule_nodes(rule, active)
    for p in nodes:
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
