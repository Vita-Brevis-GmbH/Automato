"""BGP-Filter auswerten: erwartet, erhalten und angekuendigt je Nachbar (0.113.0, ADR 0041).

Anwender 2026-10-29 (0.112.0): Backup-Wege, die nie funktionieren wuerden - „die Firewalls davor
wuerden das Netzwerk niemals lernen, da sie in den Filtern gesperrt wurden“. Roadmap Version 1
Nr. 3, Stufe 1: die Filter strikt lesend einlesen und je Nachbar Soll und Ist zeigen.

- **FortiGate** je Nachbar (`topo_peer.attrs.config`, Prozess und Filter in `topo_bgp_object`):
  - **ein**: die Filter der Richtung (distribute-list = Access-Liste, prefix-list, filter-list =
    AS-Path, route-map; IPv6 mit den `…6`-Filtern) - alle muessen erlauben. **Erwartet** sind die
    Netze, die sie ausdruecklich erlauben (permit-Regeln, auch ueber `match ip-address` einer
    Route-Map); **erhalten** die BGP-Pfade, die der Nachbar geliefert hat (`learned_from`).
    Erwartet, aber nichts erhalten bzw. erhalten, obwohl der Filter sperrt = Abweichung.
  - **aus** (berechnet): was die FortiGate dem Nachbarn ankuendigen wuerde - beste BGP-Pfade
    (nicht vom Nachbarn selbst, von iBGP nur an Route-Reflector-Clients), `network`, `aggregate`
    und `redistribute` (Routen der RIB des Protokolls, mit dessen Route-Map) durch die Filter.
- **ACI** je L3Out (`topo_l3out.route_control`): `import-rtctrl` wirkt nur mit `enforce import`
  (erwartet), `export-rtctrl` ist, was ACI ankuendigt; erhalten = Routen der VRF ueber die Peers
  des L3Outs.

Auswertung je Netz: `permit`, `deny` bzw. None (unbekannt: AS-Path, Community, Wildcard, Filter
nicht gelesen) - wie die FortiGate: erste passende Regel entscheidet, keine passt = deny; eine
Route-Map-Regel ohne Bedingung passt immer. Nur gelesene Daten, kein Schreibweg.
"""

import ipaddress
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import TopoBgpObject, TopoL3Out, TopoPeer, TopoRoute
from app.store import net_lists

PERMIT, DENY = "permit", "deny"
SHOW = 200  # Netze je Liste im Detail
Key = tuple[int, str, str]  # (Quellsystem, Knoten, Kontext)
Net = ipaddress.IPv4Network | ipaddress.IPv6Network


def _net(text) -> Net | None:
    try:
        return ipaddress.ip_network(str(text), strict=False)
    except ValueError:
        return None


def _bare(ip) -> str:
    return str(ip or "").split("%")[0].split("/")[0]


# -- Regeln -------------------------------------------------------------------------------


def prefix_match(rule: dict, net: Net) -> bool | None:
    """Regel einer Prefix-Liste: ohne ge/le genau das Netz, `le` = Laenge bis le, `ge` = ab ge
    bis ganz; `any` = alle."""
    if rule.get("prefix") == "any":
        return True
    base = _net(rule.get("prefix"))
    if base is None:
        return None
    if base.version != net.version or not net.subnet_of(base):
        return False
    ge, le = rule.get("ge"), rule.get("le")
    low = ge or base.prefixlen
    high = le or (net.max_prefixlen if ge else base.prefixlen)
    return low <= net.prefixlen <= high


def access_match(rule: dict, net: Net) -> bool | None:
    """Regel einer Access-Liste: das Netz und alles darin (`exact` = nur genau das Netz)."""
    if rule.get("wildcard"):
        return None
    if rule.get("prefix") == "any":
        return True
    base = _net(rule.get("prefix"))
    if base is None:
        return None
    if base.version != net.version:
        return False
    return net == base if rule.get("exact") else net.subnet_of(base)


_MATCH = {"prefix_list": prefix_match, "access_list": access_match}


def list_verdict(kind: str, rules: list[dict], net: Net) -> str | None:
    for r in rules:
        hit = _MATCH[kind](r, net)
        if hit is None:
            return None
        if hit:
            return r.get("action") or PERMIT
    return DENY


def catch_all(rule: dict) -> bool:
    """Regel, die alle Netze erlaubt (`any`, 0.0.0.0/0 le 32) - kein bestimmtes Soll."""
    if rule.get("prefix") == "any":
        return True
    base = _net(rule.get("prefix"))
    if base is None or base.prefixlen:
        return False
    return (
        (rule.get("le") or 0) >= base.max_prefixlen
        or bool(rule.get("ge"))
        or ("exact" in rule and not rule.get("exact"))
    )


def _covers(entry: dict, net: Net) -> bool:
    return bool(_MATCH[entry["kind"]](entry, net))


# -- Konfiguration je Kontext ---------------------------------------------------------------


@dataclass
class Config:
    process: dict = field(default_factory=dict)
    objects: dict[tuple[str, int | None, str], dict] = field(default_factory=dict)

    def rules(self, kind: str, family: int | None, name: str) -> list[dict] | None:
        obj = self.objects.get((kind, None if kind == "route_map" else family, name))
        return None if obj is None else list(obj.get("rules") or [])

    def match_list(self, name: str, family: int) -> tuple[str, list[dict]] | None:
        """`match ip-address`: Prefix-Liste bzw. Access-Liste dieses Namens."""
        for kind in ("prefix_list", "access_list"):
            rules = self.rules(kind, family, name)
            if rules is not None:
                return kind, rules
        return None

    def route_map(self, name: str, net: Net) -> str | None:
        rules = self.rules("route_map", None, name)
        if rules is None:
            return None
        key = "ip6-address" if net.version == 6 else "ip-address"
        for r in rules:
            match = r.get("match") or {}
            if set(match) - {"ip-address", "ip6-address"}:
                return None  # AS-Path, Community, Next-Hop ... nicht ausgewertet
            if key in match:
                found = self.match_list(str(match[key]), net.version)
                if found is None:
                    return None
                verdict = list_verdict(*found, net)
                if verdict is None:
                    return None
                if verdict != PERMIT:
                    continue
            elif match:
                continue  # nur die andere Familie
            return r.get("action") or PERMIT
        return DENY

    def verdict(self, filters: dict[str, str], net: Net) -> str | None:
        """Alle Filter einer Richtung muessen erlauben; ohne Filter alles."""
        out: list[str | None] = []
        for kind, name in filters.items():
            if kind == "route_map":
                out.append(self.route_map(name, net))
            elif kind in ("prefix_list", "distribute_list"):
                lk = "prefix_list" if kind == "prefix_list" else "access_list"
                rules = self.rules(lk, net.version, name)
                out.append(None if rules is None else list_verdict(lk, rules, net))
            else:
                out.append(None)  # filter-list (AS-Path)
        if DENY in out:
            return DENY
        return None if None in out else PERMIT

    def entries(self, filters: dict[str, str], family: int) -> list[dict]:
        """Netze, die die Filter einer Richtung ausdruecklich erlauben (Soll)."""
        out: list[dict] = []

        def add(kind: str, rules: list[dict], label: str) -> None:
            for r in rules:
                if (r.get("action") or PERMIT) == PERMIT and r.get("prefix"):
                    out.append({**r, "kind": kind, "filter": label, "all": catch_all(r)})

        for kind, name in filters.items():
            if kind in ("prefix_list", "distribute_list"):
                lk = "prefix_list" if kind == "prefix_list" else "access_list"
                add(lk, self.rules(lk, family, name) or [], name)
            elif kind == "route_map":
                for r in self.rules("route_map", None, name) or []:
                    target = (r.get("match") or {}).get("ip6-address" if family == 6
                                                        else "ip-address")  # fmt: skip
                    found = self.match_list(str(target), family) if target else None
                    if (r.get("action") or PERMIT) == PERMIT and found is not None:
                        add(found[0], found[1], f"{name} → {target}")
        return out


def _ways(conf: dict, family: int) -> tuple[dict[str, str], dict[str, str]]:
    filters = conf.get("filters") or {}
    six = "6" if family == 6 else ""
    return dict(filters.get(f"in{six}") or {}), dict(filters.get(f"out{six}") or {})


def _int(value) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


class Filters:
    """Prozess, Filter, BGP-Pfade und RIB der gelesenen FortiGates eines Mandanten."""

    def __init__(self, session: Session, tenant_id: int):
        self.session, self.tenant_id = session, tenant_id
        self.configs: dict[Key, Config] = {}
        for o in session.scalars(select(TopoBgpObject).where(TopoBgpObject.tenant_id == tenant_id)
                                 .order_by(TopoBgpObject.id)):  # fmt: skip
            cfg = self.configs.setdefault((o.source_system_id, o.node, o.context), Config())
            if o.kind == "bgp":
                cfg.process = o.data or {}
            else:
                family = None if o.kind == "route_map" else o.family
                cfg.objects[(o.kind, family, o.name)] = o.data or {}
        self._paths: dict[Key, list[tuple[str, bool, str]]] = defaultdict(list)
        self._as_paths: dict[tuple[Key, str], set[int]] = {}  # AS-Pfad des besten Pfads
        self._rib: dict[Key, list[tuple[str, str | None]]] = defaultdict(list)
        self._loaded = False
        self._remote: dict[Key, dict[str, int | None]] | None = None
        self._candidates: dict[Key, list[tuple[str, str, str | None]]] = {}
        self._by_key: dict[tuple, TopoPeer] | None = None

    def peers(self) -> list[TopoPeer]:
        """Nachbarn mit gelesener Konfiguration."""
        if not self.configs:
            return []
        rows = self.session.scalars(select(TopoPeer).where(TopoPeer.tenant_id == self.tenant_id)
                                    .order_by(TopoPeer.id))  # fmt: skip
        out = []
        self._remote = defaultdict(dict)
        for p in rows:
            key = (p.source_system_id, p.node, p.context)
            self._remote[key][p.peer_ip] = p.remote_as
            if key in self.configs and (p.attrs or {}).get("config") is not None:
                out.append(p)
        return out

    def blocked(self, key: Key, peer_ip: str, prefix: str,
                neighbors: Collection[tuple[Key, str]] = ()) -> str | None:  # fmt: skip
        """Sperrt ein Filter den BGP-Pfad fuer `prefix` vom Nachbarn `peer_ip` (0.114.0, Stufe
        2)? Der Eingangsfilter dieses Geraets bzw. der Ausgangsfilter des Nachbarn, wenn er eine
        gelesene FortiGate ist (`neighbors`: dessen Kontext und sein Nachbar = dieses Geraet).
        -> Grund (Anzeige) bzw. None (erlaubt, unbekannt oder nicht gelesen)."""
        net = _net(prefix)
        if net is None:
            return None
        if self._by_key is None:
            self._by_key = {(p.source_system_id, p.node, p.context, p.peer_ip): p
                            for p in self.peers()}  # fmt: skip
        p = self._by_key.get((*key, peer_ip))
        if p is not None:
            fin = _ways((p.attrs or {}).get("config") or {}, net.version)[0]
            if fin and self.configs[key].verdict(fin, net) == DENY:
                names = ", ".join(fin.values())
                return L(
                    f"Eingangsfilter {names} vom Nachbarn {peer_ip} sperrt {net}",
                    f"inbound filter {names} from neighbor {peer_ip} denies {net}",
                )
        for other, ip in neighbors:
            q = self._by_key.get((*other, ip))
            if q is None:
                continue
            fout = _ways((q.attrs or {}).get("config") or {}, net.version)[1]
            if fout and self.configs[other].verdict(fout, net) == DENY:
                names = ", ".join(fout.values())
                return L(f"Ausgangsfilter {names} des Nachbarn {peer_ip} sperrt {net}",
                         f"outbound filter {names} of neighbor {peer_ip} denies {net}")  # fmt: skip
        return None

    def remote_as(self, key: Key, ip: str) -> int | None:
        if self._remote is None:
            self.peers()
        return (self._remote or {}).get(key, {}).get(ip)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        sources = sorted({k[0] for k in self.configs})
        if not sources:
            return
        stmt = select(TopoRoute.source_system_id, TopoRoute.node, TopoRoute.context,
                      TopoRoute.table, TopoRoute.prefix, TopoRoute.best, TopoRoute.protocol,
                      TopoRoute.attrs).where(
            TopoRoute.tenant_id == self.tenant_id, TopoRoute.source_system_id.in_(sources),
            TopoRoute.table.in_(("bgp", "rib")))  # fmt: skip
        for sid, node, ctx, table, prefix, best, protocol, attrs in self.session.execute(
            stmt.order_by(TopoRoute.lo, TopoRoute.prefix_len)
        ):
            key = (sid, node, ctx)
            if key not in self.configs:
                continue
            if table == "bgp":
                self._paths[key].append(
                    (prefix, bool(best), _bare((attrs or {}).get("learned_from")))
                )
                if best:
                    self._as_paths[(key, prefix)] = _as_numbers((attrs or {}).get("as_path"))
            elif best:
                self._rib[key].append((prefix, protocol))

    def received(self, key: Key, ip: str) -> list[tuple[str, bool]]:
        self._load()
        return [(prefix, best) for prefix, best, frm in self._paths.get(key, []) if frm == ip]

    def candidates(self, key: Key) -> list[tuple[str, str, str | None]]:
        """Was die FortiGate ankuendigen kann: (Netz, Herkunft, gelernt von)."""
        if key in self._candidates:
            return self._candidates[key]
        self._load()
        process = self.configs[key].process
        out: dict[str, tuple[str, str, str | None]] = {}
        for prefix, best, frm in self._paths.get(key, []):
            if best:
                out.setdefault(prefix, (prefix, "bgp", frm or None))
        for n in process.get("networks") or []:
            out.setdefault(n["prefix"], (n["prefix"], "network", None))
        for a in process.get("aggregates") or []:
            out.setdefault(a["prefix"], (a["prefix"], "aggregate", None))
        cfg = self.configs[key]
        for r in process.get("redistribute") or []:
            for prefix, protocol in self._rib.get(key, []):
                net = _net(prefix)
                if protocol != r["name"] or net is None or prefix in out:
                    continue
                if r.get("route_map") and cfg.route_map(r["route_map"], net) != PERMIT:
                    continue
                out[prefix] = (prefix, f"redistribute {r['name']}", None)
        self._candidates[key] = list(out.values())
        return self._candidates[key]

    # -- je Nachbar ------------------------------------------------------------------------

    def report(self, p: TopoPeer, limit: int = SHOW) -> dict:
        key = (p.source_system_id, p.node, p.context)
        cfg = self.configs[key]
        conf = (p.attrs or {}).get("config") or {}
        local_as = _int(cfg.process.get("as"))
        remote_as = _int(conf.get("remote_as")) or p.remote_as
        ebgp = None if local_as is None or remote_as is None else local_as != remote_as
        established = (p.state or "").lower() == "established"
        received = self.received(key, p.peer_ip)
        families = _families(p.peer_ip, conf, received)
        inbound = _side()
        outbound = _side()
        for family in families:
            fin, fout = _ways(conf, family)
            inbound["filters"].update({f"{k}{'6' if family == 6 else ''}": v
                                       for k, v in fin.items()})  # fmt: skip
            outbound["filters"].update({f"{k}{'6' if family == 6 else ''}": v
                                        for k, v in fout.items()})  # fmt: skip
            nets = [(n, best) for prefix, best in received
                    if (n := _net(prefix)) is not None and n.version == family]  # fmt: skip
            for net, best in nets:
                verdict = cfg.verdict(fin, net)
                inbound["counts"][verdict or "unknown"] += 1
                inbound["received"].append({"prefix": str(net), "best": best, "verdict": verdict})
            for e in cfg.entries(fin, family):
                hits = [str(n) for n, _ in nets if _covers(e, n)]
                missing = not hits and not e["all"]
                inbound["entries"].append({**_entry(e), "received": len(hits),
                                           "missing": missing})  # fmt: skip
                if missing:
                    inbound["counts"]["missing"] += 1
            default = "0.0.0.0/0" if family == 4 else "::/0"
            originate = conf.get(f"capability-default-originate{'6' if family == 6 else ''}")
            if originate == "enable":
                # default-originate (0.114.1): die Default-Route geht immer an den Nachbarn,
                # unabhaengig vom Ausgangsfilter (bzw. bedingt ueber default-originate-routemap)
                outbound["default_originate"] = True
                outbound["counts"]["permit"] += 1
                outbound["announced"].append({
                    "prefix": default, "origin": "default-originate", "verdict": PERMIT,
                    **({"route_map": conf["default-originate-routemap"]}
                       if conf.get("default-originate-routemap") else {})})  # fmt: skip
            for prefix, origin, frm in self.candidates(key):
                net = _net(prefix)
                if net is None or net.version != family or frm == p.peer_ip:
                    continue
                if originate == "enable" and net.prefixlen == 0:
                    continue  # schon als default-originate
                if ebgp and remote_as is not None and origin == "bgp" \
                        and remote_as in self._as_paths.get((key, prefix), set()):  # fmt: skip
                    # AS des Nachbarn im AS-Pfad: er verwirft den Pfad (Schleifenschutz) - die
                    # Routen, die er selbst geliefert hat (z. B. andere Border-Leafs desselben AS)
                    outbound["counts"]["loop"] += 1
                    continue
                if frm and ebgp is False and not conf.get("route-reflector-client") == "enable":
                    other = self.remote_as(key, frm)
                    if other is not None and other == local_as:
                        continue  # iBGP an iBGP nur ueber Route-Reflector
                verdict = cfg.verdict(fout, net)
                if verdict == DENY:
                    outbound["counts"]["deny"] += 1
                    continue
                outbound["counts"][verdict or "unknown"] += 1
                outbound["announced"].append({"prefix": prefix, "origin": origin,
                                              "verdict": verdict})  # fmt: skip
            outbound["entries"] += [{**_entry(e)} for e in cfg.entries(fout, family)]
        for side in (inbound, outbound):
            side["open"] = not side["filters"]
            side["counts"] = dict(side["counts"])
        inbound["received_total"] = len(inbound["received"])
        outbound["announced_total"] = len(outbound["announced"])
        inbound["received"] = inbound["received"][:limit]
        outbound["announced"] = outbound["announced"][:limit]
        return {
            "peer_ip": p.peer_ip,
            "remote_as": remote_as,
            "local_as": local_as,
            "ebgp": ebgp,
            "state": p.state,
            "established": established,
            "group": conf.get("group"),
            "description": conf.get("description"),
            "default_originate": bool(outbound.get("default_originate")),
            "in": inbound,
            "out": outbound,
        }


def _as_numbers(value) -> set[int]:
    """AS-Pfad der FortiGate („65010 65020“, Liste bzw. {as: …}) -> AS-Nummern."""
    items = value if isinstance(value, list) else str(value or "").replace(",", " ").split()
    out: set[int] = set()
    for item in items:
        n = _int(item.get("as") if isinstance(item, dict) else str(item).strip("{}()[]"))
        if n is not None:
            out.add(n)
    return out


def _families(ip: str, conf: dict, received: list[tuple[str, bool]]) -> list[int]:
    """Familien der Sitzung: die der Nachbar-IP, die andere nur mit Filtern bzw. Routen dafuer."""
    try:
        own = ipaddress.ip_address(ip).version
    except ValueError:
        own = 4
    other = 6 if own == 4 else 4
    filters = conf.get("filters") or {}
    six = "6" if other == 6 else ""
    used = bool(filters.get(f"in{six}") or filters.get(f"out{six}")) or any(
        (n := _net(prefix)) is not None and n.version == other for prefix, _ in received
    )
    return sorted({own, other} if used else {own})


def _side() -> dict:
    return {"filters": {}, "entries": [], "received": [], "announced": [],
            "counts": defaultdict(int)}  # fmt: skip


def _entry(e: dict) -> dict:
    keep = ("filter", "kind", "id", "prefix", "ge", "le", "exact", "all")
    return {k: e[k] for k in keep if e.get(k) not in (None, False)}


def configured_only(f: Filters, peers: list[TopoPeer]) -> list[dict]:
    """Konfigurierte Nachbarn ohne Eintrag im Monitor (abgeschaltet bzw. nie verbunden)."""
    seen = {(p.source_system_id, p.node, p.context, p.peer_ip) for p in peers}
    out = []
    for (sid, node, ctx), cfg in sorted(f.configs.items()):
        for ip, conf in sorted((cfg.process.get("neighbors") or {}).items()):
            if (sid, node, ctx, ip) not in seen:
                out.append({"source_system_id": sid, "node": node, "context": ctx,
                            "peer_ip": ip, "config": conf})  # fmt: skip
    return out


# -- ACI: Route Control je L3Out -------------------------------------------------------------


def aci_entry_covers(entry: dict, net: Net) -> bool:
    base = _net(entry.get("prefix"))
    if base is None or base.version != net.version:
        return False
    return net.subnet_of(base) if entry.get("aggregate") else net == base


def l3out_report(session: Session, o: TopoL3Out, limit: int = SHOW) -> dict:
    """Route Control eines L3Outs: Import (erwartet, nur mit enforce import), erhaltene Routen
    der VRF ueber die Peers des L3Outs, Export (angekuendigt)."""
    rc = o.route_control or {}
    enforce = list(rc.get("enforce") or ["export"])
    peers = {_bare(p.get("ip")) for p in o.peers or [] if p.get("ip")}
    received: dict[str, Net] = {}
    if peers and o.vrf:
        for prefix, hops in session.execute(
            select(TopoRoute.prefix, TopoRoute.next_hops).where(
                TopoRoute.source_system_id == o.source_system_id, TopoRoute.context == o.vrf,
                TopoRoute.table == "rib", TopoRoute.best.is_(True))
            .order_by(TopoRoute.lo, TopoRoute.prefix_len)
        ):  # fmt: skip
            net = _net(prefix)
            if net is not None and any(_bare(h.get("ip")) in peers for h in hops or []):
                received.setdefault(str(net), net)
    imports = list(rc.get("import") or [])
    enforced = "import" in enforce
    rows = []
    counts: dict[str, int] = defaultdict(int)
    for prefix, net in received.items():
        inside = any(aci_entry_covers(e, net) for e in imports)
        verdict = (PERMIT if inside else DENY) if enforced else PERMIT
        counts[verdict] += 1
        rows.append({"prefix": prefix, "verdict": verdict})
    entries = []
    for e in imports:
        hits = sum(1 for net in received.values() if aci_entry_covers(e, net))
        everything = (_net(e.get("prefix")) or ipaddress.ip_network("0.0.0.0/32")).prefixlen == 0 \
            and bool(e.get("aggregate"))  # fmt: skip
        missing = enforced and not hits and not everything
        counts["missing"] += int(missing)
        entries.append({**e, "received": hits, "missing": missing, "all": everything})
    return {
        "id": o.id,
        "l3out": o.name,
        "aci_tenant": o.aci_tenant,
        "vrf": o.vrf,
        "enforce": enforce,
        "import_enforced": enforced,
        "peers": sorted(peers),
        "import": {
            "entries": entries,
            "received": rows[:limit],
            "received_total": len(rows),
            "counts": dict(counts),
        },  # fmt: skip
        "export": {"entries": list(rc.get("export") or [])},
    }


# -- Vorschlag fuer „Netze dahinter“ einer Anbindung ----------------------------------------


def filtered(session: Session, tenant_id: int, source_ids: list[int], next_hops: list[str],
             limit: int = net_lists.LEARNED) -> dict:  # fmt: skip
    """Netze, die die Eingangsfilter fuer die Router der Anbindung ausdruecklich erlauben
    (FortiGate: permit-Regeln der Filter je Nachbar; ACI: import-rtctrl der L3Outs mit diesem
    Peer) - Vorschlag fuer „Netze dahinter“, ohne „alles“-Regeln und Default-Route."""
    hops = {_bare(h) for h in next_hops if h.strip()}
    empty = {"prefixes": [], "collapsed": [], "total": 0, "cut": False, "filters": []}
    if not hops:
        return empty
    f = Filters(session, tenant_id)
    found: dict[str, None] = {}
    names: dict[str, None] = {}
    for p in f.peers():
        if p.peer_ip not in hops or (source_ids and p.source_system_id not in source_ids):
            continue
        cfg = f.configs[(p.source_system_id, p.node, p.context)]
        conf = (p.attrs or {}).get("config") or {}
        for family in (4, 6):
            fin, _ = _ways(conf, family)
            for e in cfg.entries(fin, family):
                net = _net(e.get("prefix"))
                if net is not None and net.prefixlen and not e["all"]:  # ohne Default-Route
                    found.setdefault(str(net), None)
                    names.setdefault(e["filter"], None)
    stmt = select(TopoL3Out).where(TopoL3Out.tenant_id == tenant_id).order_by(TopoL3Out.id)
    for o in session.scalars(stmt):
        if source_ids and o.source_system_id not in source_ids:
            continue
        if not {_bare(p.get("ip")) for p in o.peers or []} & hops:
            continue
        for e in (o.route_control or {}).get("import") or []:
            net = _net(e.get("prefix"))
            if net is not None and net.prefixlen:
                found.setdefault(str(net), None)
                names.setdefault(f"{o.name} (import-rtctrl)", None)
    if not found:
        return empty
    prefixes = list(found)
    return {"prefixes": prefixes[:limit], "collapsed": net_lists.collapse(prefixes)[:limit],
            "total": len(prefixes), "cut": len(prefixes) > limit,
            "filters": list(names)}  # fmt: skip
