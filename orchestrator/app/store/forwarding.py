"""Forwarding-Modell je Knoten und VRF (ADR 0070): was ein Geraet mit einem Paket wirklich tut.

Bis 1.20 war die Entscheidung je Hop eine Zeile in `paths._trace` („laengstes Praefix, sonst
angeschlossen“) - ohne Begriff von *verwerfen*, *lokal* oder *Leitung*. Hier steht sie als
Modell, getrennt von der Suche:

- **Auswahl** wie das Geraet: laengstes Praefix, dann kleinste Distanz, dann (FortiGate) kleinste
  Prioritaet; was danach gleich ist, ist ECMP (alle Next-Hops).
- **Aktion** der gewaehlten Route:
  - `connected` - Ziel angeschlossen (Protokoll `connected`/`local`);
  - `drop` - Null-/Blackhole-Route (`Null0`, `blackhole`, Junos `discard`/`reject`): das Geraet
    verwirft, der Weg ist **nicht** erreicht;
  - `forward` - weiter an einen Next-Hop; steht er ohne Interface da (iBGP ueber Loopback,
    statische Route nur mit Gateway), wird er **rekursiv** ueber die Routing-Tabelle aufgeloest
    (RFC 4271 §5.1.3) - der Weg geht ueber das Geraet dazwischen, nicht direkt zum Besitzer der
    Adresse;
  - `link` - Route nur mit Interface (Tunnel, Punkt-zu-Punkt, Inter-VDOM-Link): das Paket geht
    an die Gegenseite der Leitung; das Ziel ist **nicht** angeschlossen.
- **Reverse Path** (FortiGate): gibt es eine aktive Route zur Quelle ueber den Eingang
  (`feasible`), bzw. ist es die beste (`strict`)? Aktiv sind auch die Routen gleicher Distanz
  mit hoeherer Prioritaet, ueber die das Geraet nicht weiterleitet (`best=False`).

Die Entscheidung je Menge gleicher Routen wird einmal je Datenstand gerechnet und im
Wege-Speicher geteilt (`shared["decisions"]`) - viele Ziele treffen dieselbe Route, also dieselbe
Aeqivalenzklasse. Nur lesend; das Modul kennt keine Suche und keine Texte der Oberflaeche.
"""

import ipaddress
import re

from app.store.topology import Table, is_net, table_of

CONNECTED = ("connected", "local")
# Null-/Blackhole-Routen: Cisco `Null0`, FortiGate `blackhole` (CLI: „is a summary, Null“),
# Junos `discard`/`reject` (Protokoll bzw. Next-Hop)
_NULL = re.compile(r"^(null\d*|discard|blackhole|reject|dsc|rej)$", re.I)
_DROP_PROTOCOLS = ("blackhole", "discard", "reject", "null")
MAX_RECURSION = 4  # Tiefe der rekursiven Aufloesung eines Next-Hops


def is_null(route) -> bool:
    """Route verwirft (Null0, Blackhole, discard) - nie ein Weg zum Ziel."""
    attrs = route.attrs or {}
    if attrs.get("blackhole") or attrs.get("discard"):
        return True
    if (route.protocol or "").lower() in _DROP_PROTOCOLS:
        return True
    hops = route.next_hops or []
    return bool(hops) and all(
        not h.get("ip") and _NULL.match(str(h.get("interface") or "")) for h in hops
    )


def _priority(route) -> int:
    """FortiGate-Prioritaet einer Route (kleiner gewinnt); ohne Angabe 0 = wie alle anderen."""
    try:
        return int((route.attrs or {}).get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def winners(rows: list, table: Table) -> list:
    """Wirksame Routen aus allen passenden der RIB: laengstes Praefix, kleinste Distanz,
    kleinste Prioritaet (FortiOS: ECMP nur bei gleicher Distanz **und** Prioritaet)."""
    rows = [r for r in rows if table_of(r) == table]
    if not rows:
        return []
    length = max(r.prefix_len for r in rows)
    rows = [r for r in rows if r.prefix_len == length]
    distance = min((r.distance or 0) for r in rows)
    rows = [r for r in rows if (r.distance or 0) == distance]
    priority = min(_priority(r) for r in rows)
    return [r for r in rows if _priority(r) == priority]


_NONE = object()


def decide(g, key, ip, table: Table = 0) -> dict | None:
    """Entscheidung des Knotens fuer das Ziel in der VRF `table` - das Format von
    `Graph.route` (Praefix, Protokoll, Distanz, Next-Hops, Mitglieder) plus `action` und
    `branches` (aufgeloeste Next-Hops). None = keine Route.

    Mit dem Index der RIB im Speicher: vom laengsten Praefix abwaerts, je Praefix und VRF
    einmal je Datenstand gerechnet - alle Ziele derselben Route (Aequivalenzklasse) teilen die
    Entscheidung, ohne die Routen je Ziel neu zu laden und zu vergleichen."""
    memo = g._decisions
    rib = g._index(key)
    if rib is None:  # Index (noch) nicht geladen: aus der Datenbank
        rows = winners(g._routes(ip, key), table)
        if not rows:
            return None
        ident = (key, table, tuple(sorted(r.id for r in rows)))
        found = memo.get(ident)
        if found is None:
            found = memo[ident] = _compile(g, key, table, rows)
        return dict(found)
    version = ip.version
    bits = 32 if version == 4 else 128
    if is_net(ip):
        start, longest = int(ip.network_address), ip.prefixlen
    else:
        start, longest = int(ip), bits
    for length in reversed(rib.lengths.get(version, ())):
        if length > longest:
            continue
        masked = start & (((1 << length) - 1) << (bits - length)) if length else 0
        slot = rib.by[(version, length)].get(masked)
        if not slot:
            continue
        ident = (key, table, version, length, masked)
        found = memo.get(ident, _NONE)
        if found is _NONE:
            rows = winners(g._route_rows([rid for rid, _proto in slot]), table)
            found = memo[ident] = _compile(g, key, table, rows) if rows else None
        if found is not None:
            return dict(found)
    return None


def _compile(g, key, table: Table, rows: list) -> dict:
    hops: list[dict] = []
    for r in rows:
        for h in r.next_hops or []:
            if h not in hops:
                hops.append(h)
    first = rows[0]
    out = {
        "prefix": first.prefix,
        "prefix_len": first.prefix_len,
        "protocol": first.protocol,
        "distance": first.distance,
        "next_hops": hops,
        "members": sorted({r.node for r in rows}),
    }
    if any(is_null(r) for r in rows) or _configured_blackhole(g, key, table, first):
        out["action"] = "drop"
        out["branches"] = []
        return out
    if first.protocol in CONNECTED:
        out["action"] = "connected"
        out["branches"] = []
        return out
    branches: list[dict] = []
    for h in hops:
        for b in _resolve(g, key, table, h, {first.prefix}, 0):
            if b not in branches:
                branches.append(b)
    out["branches"] = branches
    out["action"] = "forward" if any(b.get("ip") for b in branches) else "link"
    if any(b.get("recursive") for b in branches):
        out["recursive"] = True
    return out


def _configured_blackhole(g, key, table: Table, route) -> bool:
    """FortiGate: die wirksame statische Route ist laut Konfiguration (`router static`,
    `blackhole enable`) eine Blackhole-Route - welche Felder der Monitor dafuer liefert, ist
    nicht belegt, die Konfiguration schon. Gleiche VRF, gleiches Praefix, gleiche Distanz."""
    if route.protocol != "static":
        return False
    return (route.prefix, table, route.distance or 0) in g.blackholes(key)


def _resolve(g, key, table: Table, hop: dict, seen: set, depth: int) -> list[dict]:
    """Next-Hop ohne Interface rekursiv aufloesen: die Route zu seiner Adresse nennt das
    Interface bzw. den wirklichen Nachbarn. Mit Interface (FortiGate `gateway` + `device`, IOS
    `via x, Gi0/1`) geht das Paket direkt an die Adresse auf diesem Interface - keine Rekursion.
    Ueber die Default-Route bzw. ohne Route bleibt der Next-Hop, wie er dasteht (Ende sichtbar
    „gehoert keinem gelesenen Geraet“ bzw. Besitzer der Adresse)."""
    ip, iface = hop.get("ip"), hop.get("interface")
    if not ip or iface:
        return [dict(hop)]
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return [dict(hop)]
    rows = winners(g._routes(addr, key), table)
    if not rows:
        return [dict(hop)]
    via = rows[0]
    if via.protocol in CONNECTED:
        port = next((h.get("interface") for h in via.next_hops or [] if h.get("interface")), None)
        return [{**hop, **({"interface": port} if port else {})}]
    if via.prefix_len == 0 or via.prefix in seen or depth >= MAX_RECURSION or is_null(via):
        return [dict(hop)]
    out: list[dict] = []
    for r in rows:
        for h in r.next_hops or []:
            for b in _resolve(g, key, table, h, seen | {via.prefix}, depth + 1):
                chain = [ip, *(b.get("recursive") or [])]
                found = {**b, "recursive": chain, "recursive_via": via.prefix}
                if found not in out:
                    out.append(found)
    return out or [dict(hop)]


def reverse_path(g, key, table: Table, src, ingress: str | None) -> tuple[str, bool]:
    """Reverse Path der FortiGate fuer die Quelle am Eingang `ingress`:
    -> (`feasible` | `none` | `unknown`, strikt = die beste Route geht ueber den Eingang).

    `feasible`: mindestens eine aktive Route zur Quelle (jede Praefixlaenge, auch die
    Default-Route) zeigt ueber den Eingang; `none`: Routen zur Quelle gibt es, keine ueber den
    Eingang (bzw. gar keine); `unknown`: nicht entscheidbar (Routen ohne Interface)."""
    if src is None or not ingress:
        return "unknown", False
    memo = g._decisions
    ident = ("rpf", key, table, ingress, str(src))
    if ident not in memo:  # dieselbe Quelle kommt in vielen Wegen vor: einmal je Datenstand
        memo[ident] = _reverse_path(g, key, table, src, ingress)
    return memo[ident]


def _reverse_path(g, key, table: Table, src, ingress: str) -> tuple[str, bool]:
    # der uebliche Fall zuerst, aus der gemerkten Entscheidung: die beste Route zur Quelle geht
    # ueber den Eingang (auch `strict-src-check` besteht) - ohne die uebrigen Routen zu laden
    best_route = decide(g, key, src, table)
    if best_route is not None and best_route["action"] != "drop":
        if any(_via(h, ingress) for h in best_route["next_hops"]):
            return "feasible", True
    rows = [r for r in g._routes(src, key) if table_of(r) == table and not is_null(r)]
    best = winners(rows, table)
    strict = any(_via(h, ingress) for r in best for h in r.next_hops or [])
    if strict:
        return "feasible", True
    blind = False
    # dazu die Routen, die in der Routing-Tabelle stehen, ueber die aber nicht weitergeleitet
    # wird (FortiOS: gleiche Distanz, hoehere Prioritaet - ueblich bei Dual-WAN): fuer
    # „feasible“ genuegt eine aktive Route ueber den Eingang, die beste muss es nicht sein
    standby = [r for r in g.standby_routes(src, key) if table_of(r) == table and not is_null(r)]
    for r in [*rows, *standby]:
        for h in r.next_hops or []:
            if _via(h, ingress):
                return "feasible", False
            blind = blind or not h.get("interface")
    # ein Netz als Quelle: Routen fuer Teile davon koennen ueber den Eingang gehen - fuer das
    # ganze Netz nicht entscheidbar, nie „verworfen“
    return ("unknown" if blind or is_net(src) else "none"), False


def _via(hop: dict, ingress: str) -> bool:
    """Next-Hop ueber das Interface - auch die Instanz eines Dial-up-Tunnels (`<tunnel>_<n>`)."""
    iface = str(hop.get("interface") or "")
    return bool(iface) and (
        iface == ingress or iface.startswith(f"{ingress}_") or ingress.startswith(f"{iface}_")
    )


def classes(g, key, table: Table, addresses: list) -> list[list]:
    """Adressen nach ihrer Entscheidung an diesem Knoten gruppiert (Aeqivalenzklasse = gleiche
    wirksame Routen): je Klasse wird der Weg einmal gerechnet, nicht je Adresse."""
    order: dict[tuple, list] = {}
    for a in addresses:
        rows = winners(g._routes(a, key), table)
        ident = tuple(sorted(r.id for r in rows)) or ("-", str(a) if not is_net(a) else "net")
        order.setdefault(ident, []).append(a)
    return list(order.values())
