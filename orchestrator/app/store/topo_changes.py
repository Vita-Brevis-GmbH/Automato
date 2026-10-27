"""Verlauf des Routings (ADR 0030): Aenderungen von Lauf zu Lauf je Quellsystem.

`topology.replace` ersetzt den Routing-Stand eines Quellsystems je Lauf. Vorher vergleicht
`record` den alten mit dem neuen Stand und schreibt die Aenderungen nach `topo_change`:

- **Arten** (`kind`): Routen (RIB, BGP-Pfade, statische), BGP-Nachbarn, HA, Policy-Routen bzw.
  SD-WAN-Regeln, Kontexte (VDOM bzw. VRF auf dem Border-Leaf) und L3Outs.
- **Schluessel** je Art: Route = Tabelle, VRF im Kontext und Praefix, beim BGP-Pfad dazu der
  Nachbar, bei der statischen Route die Sequenz; Nachbar = IP; Policy-Route = Art und Sequenz;
  L3Out = DN; HA einmal je Quellsystem. Gleicher Schluessel mehrfach = eine Liste (Reihenfolge
  egal).
- **Verglichen** wird nur eine Positivliste der Felder (Next-Hops mit Interface, Protokoll,
  Distanz, Metrik, aktiv/Backup, BGP-Zustand, AS, AS-Pfad, Local Preference ...) in fester Form:
  Listen sortiert, wo die Reihenfolge nichts bedeutet (Next-Hops, Mitglieder, Bedingungen); die
  Reihenfolge der SD-WAN-Ausgaenge bleibt (Vorrang). Zeitstempel und Zaehler zaehlen nie - der
  Collector liest keine, und neue Felder kommen erst mit der Positivliste dazu.
- **Erster Lauf** eines Quellsystems (kein Stand, kein Verlauf): nur ein Vermerk
  „Ausgangsstand“ mit den Zahlen.
- **Nicht lesbar** (`topo_context.missing`): Tabellen, die in einem der beiden Laeufe nicht
  lesbar waren, werden nicht einzeln verglichen - statt einer Flut „entfernt“ (bzw. danach
  „hinzugefuegt“) ein Vermerk am Kontext mit der Anzahl. Ein neuer bzw. nicht mehr gelesener
  Kontext ist ein Eintrag mit den Zahlen, nicht jede Route darin.
- **Geraetename** gewechselt (FortiGate-HA: Hostname des aktiven Mitglieds) bei genau einem
  Knoten vorher und nachher: ein Eintrag, verglichen wird unter dem neuen Namen.
- **Obergrenze** `LIMIT` je Lauf - Wichtiges zuerst (Kontexte, HA, Nachbarn, Policy-Routen,
  L3Outs, dann Routen); der Rest ist ein Sammel-Eintrag mit der Anzahl je Art.

Gespeichert werden nur Daten (Arten, Schluessel, Felder); Texte entstehen beim Abruf
(`describe`, Sprache der Anfrage).
"""

import ipaddress
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import (
    TopoChange,
    TopoContext,
    TopoHa,
    TopoL3Out,
    TopoPeer,
    TopoPolicyRoute,
    TopoRoute,
)
from app.store.topology import ip_hex, table_from

LIMIT = 5000  # Eintraege je Lauf, danach ein Sammel-Eintrag
KINDS = ("context", "ha", "bgp_peer", "policy_route", "l3out", "route")  # Reihenfolge = Vorrang
CHANGES = ("added", "removed", "changed")
NOTES = ("baseline", "unreadable", "truncated")
IN_CONTEXT = ("route", "bgp_peer", "policy_route")  # Daten eines Kontexts (VDOM bzw. VRF)
_TABLES = {"rib": 0, "static": 1, "bgp": 2}

# Attribute je Tabelle, die eine Route aendern (Positivliste; Kommentar, Pfad-ID und
# Tunnel-Angaben nicht)
_ROUTE_ATTRS = {
    "rib": ("priority", "owner", "route_type"),
    "bgp": ("as_path", "local_pref", "origin", "weight"),
    "static": ("priority", "weight", "blackhole", "dynamic-gateway", "sdwan-zone", "dstaddr"),
}
_PEER_ATTRS = ("admin_status", "type")
_HA_ATTRS = ("override", "priority", "session-pickup-connectionless", "session-pickup-nat",
             "session-pickup-expectation", "vcluster-status", "ha-mgmt-status")  # fmt: skip
_HA_MEMBER = ("hostname", "serial", "priority", "vcluster", "role")

# Was „nicht lesbar“ war (Bezeichnung der Collectors ohne Grund in Klammern) -> (Art, Tabelle,
# Familie); unbekannte Bezeichnung = der ganze Kontext
_MISSING = {
    "Routing-Tabelle IPv4": ("route", "rib", 4),
    "Routing-Tabelle IPv6": ("route", "rib", 6),
    "BGP-Pfade IPv4": ("route", "bgp", 4),
    "BGP-Pfade IPv6": ("route", "bgp", 6),
    "statische Routen IPv4": ("route", "static", 4),
    "statische Routen IPv6": ("route", "static", 6),
    "BGP-Nachbarn IPv4": ("bgp_peer", None, 4),
    "BGP-Nachbarn IPv6": ("bgp_peer", None, 6),
    "BGP-Nachbarn": ("bgp_peer", None, None),
    "Policy-Routen IPv4": ("policy_route", "policy", None),
    "Policy-Routen IPv6": ("policy_route", "policy", None),
    "SD-WAN": ("policy_route", "sdwan", None),
}
_ALL = ("*", None, None)

# (Art, Knoten, Kontext, Tabelle, Schluessel, Praefix)
Ident = tuple[str, str | None, str | None, str | None, str, str | None]
CtxKey = tuple[str, str]


# -- feste Form der verglichenen Felder ----------------------------------------------------


def _dump(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _canon(value):
    """Listen ohne Bedeutung der Reihenfolge sortiert, leere Werte weg (rekursiv)."""
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, list | tuple):
        return sorted((_canon(v) for v in value), key=_dump)
    return value


def _clean(record: dict) -> dict:
    return {k: v for k, v in record.items() if v not in (None, "", [], {})}


def _hops(hops) -> list[dict]:
    out = [
        {k: h[k] for k in ("ip", "interface") if h.get(k)}
        for h in hops or []
        if isinstance(h, dict)
    ]
    return sorted(out, key=lambda h: (h.get("ip") or "", h.get("interface") or ""))


def route_key(table: str | None, prefix: str, attrs: dict | None, next_hops) -> str:
    """Schluessel einer Route: Praefix, VRF im Kontext (ausser 0), beim BGP-Pfad der Nachbar,
    bei der statischen Route die Sequenz (sonst Gateway bzw. Interface)."""
    attrs = attrs or {}
    parts = [prefix]
    vrf = table_from(attrs)
    if vrf != 0:
        parts.append(f"vrf={vrf}")
    first = next((h for h in next_hops or [] if isinstance(h, dict)), {})
    if table == "bgp":
        parts.append(f"from={attrs.get('learned_from') or first.get('ip') or '-'}")
    elif table == "static":
        seq = attrs.get("seq-num")
        if seq not in (None, ""):
            parts.append(f"seq={seq}")
        else:
            parts.append(f"via={first.get('ip') or first.get('interface') or '-'}")
    return " ".join(parts)


def _route_record(r) -> dict:
    attrs = r.get("attrs") or {}
    table = r.get("table")
    record = {
        "protocol": r.get("protocol"),
        "distance": r.get("distance"),
        "metric": r.get("metric"),
        "next_hops": _hops(r.get("next_hops")),
    }
    if table != "rib":  # BGP: bester Pfad bzw. Backup; statisch: aktiv bzw. inaktiv
        record["best"] = bool(r.get("best", True))
    for key in _ROUTE_ATTRS.get(table or "", ()):
        record[key] = _canon(attrs.get(key))
    return _clean(record)


def _peer_record(p) -> dict:
    attrs = p.get("attrs") or {}
    record = {
        "state": p.get("state"),
        "remote_as": p.get("remote_as"),
        "local_ip": p.get("local_ip"),
    }
    record.update({k: attrs.get(k) for k in _PEER_ATTRS})
    return _clean(record)


def _ha_record(h) -> dict:
    attrs = h.get("attrs") or {}
    members = [
        {k: m[k] for k in _HA_MEMBER if m.get(k) is not None}
        for m in h.get("members") or []
        if isinstance(m, dict)
    ]
    record = {
        "mode": h.get("mode"),
        "group_name": h.get("group_name"),
        "group_id": h.get("group_id"),
        "members": sorted(
            members, key=lambda m: (str(m.get("serial") or ""), str(m.get("hostname") or ""))
        ),  # fmt: skip
        "session_pickup": h.get("session_pickup"),
        "sync_peers": _canon(h.get("sync_peers") or []),
    }
    record.update({k: attrs.get(k) for k in _HA_ATTRS})
    return _clean(record)


def _policy_record(p) -> dict:
    attrs = p.get("attrs") or {}
    match = {
        k: (list(v) if k == "ports" else _canon(v)) if isinstance(v, list) else v
        for k, v in (p.get("match") or {}).items()
    }
    members = [m for m in attrs.get("members") or [] if isinstance(m, dict)]
    record = {
        "enabled": bool(p.get("enabled", True)),
        "name": p.get("name"),
        "action": p.get("action"),
        "gateway": p.get("gateway"),
        "output_device": list(p.get("output_device") or []),  # SD-WAN: Vorrang = Reihenfolge
        "match": _clean(match),
        "tos": attrs.get("tos"),
        "tos-mask": attrs.get("tos-mask"),
        "zones": _canon(attrs.get("zones")),
        "members": sorted((_canon(m) for m in members), key=lambda m: str(m.get("seq-num", ""))),
    }
    return _clean(record)


def _l3out_record(o) -> dict:
    return _clean(
        {
            "vrf": o.get("vrf"),
            "nodes": _canon(o.get("nodes") or []),
            "paths": _canon(o.get("paths") or []),
            "peers": _canon(o.get("peers") or []),
            "ext_epgs": _canon(o.get("ext_epgs") or []),
        }
    )


def _label(missing) -> str:
    """„BGP-Pfade IPv4 (HTTP 403)“ -> „BGP-Pfade IPv4“ (der Grund aendert sich, z. B. Anzahl)."""
    return str(missing).split(" (", 1)[0].strip()


def _labels_of(missing) -> list[str]:
    return sorted({_label(m) for m in missing or []})


def _family(text: str | None) -> int | None:
    return None if not text else (6 if ":" in text else 4)


# -- Stand eines Quellsystems ----------------------------------------------------------------


class State:
    """Verglichener Stand: je Schluessel die feste Form als JSON-Text, dazu die Kontexte."""

    def __init__(self):
        self.items: dict[Ident, list[str]] = defaultdict(list)
        self.contexts: dict[CtxKey, dict] = {}
        self.ha_node: str | None = None

    @property
    def empty(self) -> bool:
        return not self.items and not self.contexts

    def value(self, ident: Ident) -> str | None:
        parts = self.items.get(ident)
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else "[" + ",".join(sorted(parts)) + "]"

    def _add(self, ident: Ident, record: dict) -> None:
        self.items[ident].append(_dump(record))

    def context(self, c) -> None:
        self.contexts[(c.get("node"), c.get("context"))] = {
            "kind": c.get("kind"),
            "missing": list(c.get("missing") or []),
        }

    def route(self, r) -> None:
        table, prefix = r.get("table"), r.get("prefix")
        key = route_key(table, prefix, r.get("attrs"), r.get("next_hops"))
        self._add(("route", r.get("node"), r.get("context"), table, key, prefix), _route_record(r))

    def peer(self, p) -> None:
        ident = ("bgp_peer", p.get("node"), p.get("context"), None, str(p.get("peer_ip")), None)
        self._add(ident, _peer_record(p))

    def ha(self, h) -> None:
        self.ha_node = self.ha_node or h.get("node")
        self._add(("ha", None, None, None, "", None), _ha_record(h))

    def policy(self, p) -> None:
        seq, name = p.get("seq"), p.get("name")
        key = f"#{seq}" if seq is not None else (name or "?")
        self._add(("policy_route", p.get("node"), p.get("context"), p.get("kind"), key, None),
                  _policy_record(p))  # fmt: skip

    def l3out(self, o) -> None:
        self._add(("l3out", None, None, None, str(o.get("dn")), None), _l3out_record(o))

    def counts(self, ctx: CtxKey | None = None) -> dict[str, int]:
        """Anzahl je Art wie in der Routing-Uebersicht (Routen je Tabelle) - eines Kontexts bzw.
        des ganzen Quellsystems."""
        out: Counter = Counter()
        for (kind, node, context, table, _, _), parts in self.items.items():
            if ctx is not None and (kind not in IN_CONTEXT or (node, context) != ctx):
                continue
            name = {"route": table, "bgp_peer": "peers", "policy_route": "policy_routes",
                    "ha": "ha", "l3out": "l3outs"}[kind]  # fmt: skip
            out[name] += len(parts)
        if ctx is None:
            out["contexts"] = len(self.contexts)
        return {k: v for k, v in out.items() if v}

    def nodes(self) -> set[str]:
        return {n for n, _ in self.contexts} | {
            i[1] for i in self.items if i[0] in IN_CONTEXT and i[1]
        }

    def rename(self, old: str, new: str) -> None:
        self.contexts = {(new if n == old else n, c): v for (n, c), v in self.contexts.items()}
        items: dict[Ident, list[str]] = defaultdict(list)
        for (kind, node, *rest), parts in self.items.items():
            items[(kind, new if node == old else node, *rest)] += parts  # type: ignore[index]
        self.items = items


def from_db(session: Session, source_system_id: int) -> State:
    """Gespeicherter Stand des Quellsystems (nur die verglichenen Spalten)."""
    st = State()
    sid = source_system_id
    for model, add, cols in (
        (TopoContext, st.context, ("node", "context", "kind", "missing")),
        (TopoRoute, st.route, ("node", "context", "prefix", "table", "protocol", "best",
                               "distance", "metric", "next_hops", "attrs")),
        (TopoPeer, st.peer, ("node", "context", "peer_ip", "local_ip", "remote_as", "state",
                             "attrs")),
        (TopoHa, st.ha, ("node", "mode", "group_name", "group_id", "members", "session_pickup",
                         "sync_peers", "attrs")),
        (TopoPolicyRoute, st.policy, ("node", "context", "kind", "seq", "name", "enabled",
                                      "match", "action", "gateway", "output_device", "attrs")),
        (TopoL3Out, st.l3out, ("dn", "vrf", "nodes", "paths", "peers", "ext_epgs")),
    ):  # fmt: skip
        stmt = select(*(getattr(model, c) for c in cols)).where(model.source_system_id == sid)
        for row in session.execute(stmt).mappings():
            add(row)
    return st


def from_rows(rows: dict) -> State:
    """Neuer Stand aus den Zeilen, die `topology.replace` gleich einfuegt."""
    st = State()
    for model, add in (
        (TopoContext, st.context),
        (TopoRoute, st.route),
        (TopoPeer, st.peer),
        (TopoHa, st.ha),
        (TopoPolicyRoute, st.policy),
        (TopoL3Out, st.l3out),
    ):
        for row in rows.get(model, []):
            add(row)
    return st


# -- Vergleich -------------------------------------------------------------------------------


def _entry(kind, node, context, table, key, change, before=None, after=None, prefix=None) -> dict:
    family = lo = hi = None
    if prefix:
        try:
            net = ipaddress.ip_network(prefix, strict=False)
            family = net.version
            lo, hi = ip_hex(net.network_address), ip_hex(net.broadcast_address)
        except ValueError:
            pass
    return {
        "kind": kind,
        "node": node,
        "context": context,
        "table": table,
        "key": (key or "")[:400],
        "prefix": prefix,
        "family": family,
        "lo": lo,
        "hi": hi,
        "change": change,
        "before": before,
        "after": after,
    }


def _blocked(ident: Ident, scopes: list[tuple]) -> bool:
    kind, _, _, table, key, prefix = ident
    family = _family(prefix if kind == "route" else key if kind == "bgp_peer" else None)
    for k, t, f in scopes:
        if k == "*":
            return True
        if k == kind and t in (None, table) and f in (None, family):
            return True
    return False


def _order(ident: Ident) -> tuple:
    kind, node, context, table, key, _ = ident
    return (KINDS.index(kind), _TABLES.get(table or "", 9), node or "", context or "", key)


def _load(text: str | None):
    return None if text is None else json.loads(text)


def diff(old: State, new: State, first: bool = False) -> list[dict]:
    """Aenderungen alter -> neuer Stand (Reihenfolge = Vorrang fuer die Obergrenze)."""
    if first:
        return [_entry("context", None, None, None, "", "baseline", after={"counts": new.counts()})]
    out: list[dict] = []
    before_nodes, after_nodes = old.nodes(), new.nodes()
    if len(before_nodes) == 1 and len(after_nodes) == 1 and before_nodes != after_nodes:
        (a,), (b,) = before_nodes, after_nodes
        old.rename(a, b)  # z. B. HA-Wechsel: Hostname des aktiven Mitglieds
        out.append(_entry("context", b, None, None, "", "changed", {"node": a}, {"node": b}))
    added = new.contexts.keys() - old.contexts.keys()
    removed = old.contexts.keys() - new.contexts.keys()
    common = old.contexts.keys() & new.contexts.keys()
    blocked: dict[CtxKey, list[tuple]] = {}
    for c in common:
        scopes = [
            _MISSING.get(_label(m), _ALL)
            for m in [*old.contexts[c]["missing"], *new.contexts[c]["missing"]]
        ]
        if scopes:
            blocked[c] = scopes
    skipped: Counter = Counter()
    items: list[dict] = []
    for ident in sorted(old.items.keys() | new.items.keys(), key=_order):
        kind, node, context, table, key, prefix = ident
        c = (node, context)
        if kind in IN_CONTEXT and (c in added or c in removed):
            continue  # neuer bzw. nicht mehr gelesener Kontext: ein Eintrag mit Zahlen
        if kind in IN_CONTEXT and c in blocked and _blocked(ident, blocked[c]):
            skipped[c] += 1
            continue
        a, b = old.value(ident), new.value(ident)
        if a == b:
            continue
        change = "added" if a is None else "removed" if b is None else "changed"
        before, after = _load(a), _load(b)
        if kind == "ha":
            node = new.ha_node or old.ha_node
        if kind == "l3out":  # Kontext eines L3Outs = seine VRF
            context = next((x.get("vrf") for x in (after, before) if isinstance(x, dict)), None)
        items.append(_entry(kind, node, context, table, key, change, before, after, prefix))
    for c in sorted(removed):
        info = {**old.contexts[c], "counts": old.counts(c)}
        out.append(_entry("context", c[0], c[1], None, c[1], "removed", before=info))
    for c in sorted(added):
        info = {**new.contexts[c], "counts": new.counts(c)}
        out.append(_entry("context", c[0], c[1], None, c[1], "added", after=info))
    for c in sorted(common):
        was, now = old.contexts[c]["missing"], new.contexts[c]["missing"]
        if _labels_of(was) == _labels_of(now):
            continue
        change = "unreadable" if now else "changed"  # sonst: wieder lesbar
        out.append(_entry("context", c[0], c[1], None, c[1], change, {"missing": was},
                          {"missing": now, "not_compared": skipped[c]}))  # fmt: skip
    return out + items


def limit(entries: list[dict], size: int | None = None) -> list[dict]:
    """Hoechstens `size` (Standard `LIMIT`) Eintraege; der Rest als ein Sammel-Eintrag."""
    size = LIMIT if size is None else size
    if len(entries) <= size:
        return entries
    rest = entries[size:]
    after = {
        "count": len(rest),
        "limit": size,
        "kinds": dict(Counter(e["kind"] for e in rest)),
        "changes": dict(Counter(e["change"] for e in rest)),
    }
    return [*entries[:size], _entry("context", None, None, None, "", "truncated", after=after)]


def record(session: Session, run, rows: dict) -> int:
    """Vor dem Ersetzen: alter Stand gegen die neuen Zeilen, Aenderungen speichern. Rueckgabe =
    Anzahl gespeicherter Eintraege."""
    sid = run.source_system_id
    old = from_db(session, sid)
    first = (
        old.empty
        and session.scalar(select(TopoChange.id).where(TopoChange.source_system_id == sid).limit(1))
        is None
    )
    entries = limit(diff(old, from_rows(rows), first=first))
    if entries:
        base = {
            "tenant_id": run.tenant_id,
            "source_system_id": sid,
            "collector_run_id": getattr(run, "id", None),
            "at": datetime.now(UTC),
        }
        session.execute(insert(TopoChange), [{**base, **e} for e in entries])
    return len(entries)


def last_changed(session: Session, routes: list[TopoRoute]) -> dict[int, datetime]:
    """Je Route (ID) die letzte Aenderung (neu bzw. geaendert) aus dem Verlauf."""
    want: dict[tuple, int] = {}
    prefixes: dict[int, set[str]] = defaultdict(set)
    for r in routes:
        key = route_key(r.table, r.prefix, r.attrs, r.next_hops)
        want[(r.source_system_id, r.node, r.context, r.table, key)] = r.id
        prefixes[r.source_system_id].add(r.prefix)
    out: dict[int, datetime] = {}
    for sid, values in prefixes.items():
        ordered = sorted(values)
        for i in range(0, len(ordered), 500):
            stmt = (
                select(TopoChange.node, TopoChange.context, TopoChange.table, TopoChange.key,
                       func.max(TopoChange.at))
                .where(
                    TopoChange.source_system_id == sid,
                    TopoChange.kind == "route",
                    TopoChange.change.in_(("added", "changed")),
                    TopoChange.prefix.in_(ordered[i : i + 500]),
                )
                .group_by(TopoChange.node, TopoChange.context, TopoChange.table, TopoChange.key)
            )  # fmt: skip
            for node, context, table, key, at in session.execute(stmt):
                rid = want.get((sid, node, context, table, key))
                if rid is not None and at is not None:
                    out[rid] = aware(at)
    return out


def aware(value: datetime | None) -> datetime | None:
    """SQLite liefert ohne Zeitzone - gespeichert ist UTC."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


# -- Texte beim Abruf (Sprache der Anfrage) --------------------------------------------------

_FIELD_ORDER = (
    "next_hops", "protocol", "distance", "metric", "best", "as_path", "local_pref", "origin",
    "weight", "priority", "owner", "route_type", "blackhole", "dynamic-gateway", "sdwan-zone",
    "dstaddr", "state", "remote_as", "local_ip", "admin_status", "type", "mode", "group_name",
    "group_id", "members", "session_pickup", "sync_peers", "enabled", "name", "action",
    "gateway", "output_device", "match", "zones", "tos", "tos-mask", "vrf", "nodes", "paths",
    "peers", "ext_epgs",
)  # fmt: skip


def _field_labels() -> dict[str, str]:
    return {
        "next_hops": "Next-Hops",
        "protocol": L("Protokoll", "Protocol"),
        "distance": L("Distanz", "Distance"),
        "metric": L("Metrik", "Metric"),
        "best": L("aktiv/Backup", "active/backup"),
        "as_path": L("AS-Pfad", "AS path"),
        "local_pref": "Local Preference",
        "origin": "Origin",
        "weight": "Weight",
        "priority": L("Priorität", "Priority"),
        "owner": "Owner",
        "route_type": L("Routen-Typ", "Route type"),
        "blackhole": "Blackhole",
        "dynamic-gateway": "Dynamic Gateway",
        "sdwan-zone": L("SD-WAN-Zone", "SD-WAN zone"),
        "dstaddr": L("Zieladresse", "Destination address"),
        "state": L("Zustand", "State"),
        "remote_as": "Remote AS",
        "local_ip": L("Lokale IP", "Local IP"),
        "admin_status": L("Admin-Status", "Admin status"),
        "type": L("Typ", "Type"),
        "mode": L("Modus", "Mode"),
        "group_name": L("Gruppe", "Group"),
        "group_id": L("Gruppen-ID", "Group ID"),
        "members": L("Mitglieder", "Members"),
        "session_pickup": "Session Pickup",
        "sync_peers": L("Session-Sync", "Session sync"),
        "enabled": L("eingeschaltet", "enabled"),
        "name": "Name",
        "action": L("Aktion", "Action"),
        "gateway": "Gateway",
        "output_device": L("Ausgang", "Outgoing interface"),
        "match": L("Bedingung", "Match"),
        "zones": L("Zonen", "Zones"),
        "tos": "ToS",
        "tos-mask": L("ToS-Maske", "ToS mask"),
        "vrf": "VRF",
        "nodes": L("Border-Leafs", "Border leafs"),
        "paths": "Interfaces/SVIs",
        "peers": L("BGP-Peers", "BGP peers"),
        "ext_epgs": L("Externe EPGs", "External EPGs"),
        "entries": L("Einträge", "Entries"),
    }


def _hop_text(h: dict) -> str:
    via = f" via {h['interface']}" if h.get("interface") else ""
    return f"{h.get('ip') or L('direkt', 'direct')}{via}"


def _text(field: str, value, table: str | None) -> str | None:
    if value is None:
        return None
    if field == "next_hops" and isinstance(value, list):
        return ", ".join(_hop_text(h) for h in value if isinstance(h, dict)) or "–"
    if field == "best":
        if table == "bgp":
            return L("bester Pfad", "best path") if value else L("Backup", "backup")
        return L("aktiv", "active") if value else L("inaktiv", "inactive")
    if field == "members" and table is None and isinstance(value, list):  # HA
        return ", ".join(
            f"{m.get('hostname') or m.get('serial') or '?'}"
            + (f" ({m['role']})" if m.get("role") else "")
            for m in value
            if isinstance(m, dict)
        )
    if isinstance(value, bool):
        return L("ja", "yes") if value else L("nein", "no")
    if isinstance(value, list) and all(isinstance(v, str | int | float) for v in value):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
    return str(value)


def fields(row) -> list[dict]:
    """Verglichene Felder einer Aenderung als Text: geaendert nur die abweichenden, neu bzw.
    entfernt alle. Vermerke und Kontexte haben keine (siehe `summary`)."""
    if row.change not in CHANGES or row.kind == "context":
        return []
    before, after = row.before, row.after
    labels = _field_labels()
    if not isinstance(before, dict | None) or not isinstance(after, dict | None):
        # gleicher Schluessel mehrfach: die Eintraege als Ganzes
        def text(value) -> str | None:
            return None if value is None else json.dumps(value, ensure_ascii=False)

        return [{"field": "entries", "label": labels["entries"], "before": text(before),
                 "after": text(after)}]  # fmt: skip
    before, after = before or {}, after or {}
    keys = [
        k for k in (*before, *after) if row.change != "changed" or before.get(k) != after.get(k)
    ]
    order = {k: i for i, k in enumerate(_FIELD_ORDER)}
    out = []
    for k in sorted(dict.fromkeys(keys), key=lambda k: (order.get(k, len(order)), k)):
        out.append(
            {
                "field": k,
                "label": labels.get(k, k),
                "before": _text(k, before.get(k), row.table),
                "after": _text(k, after.get(k), row.table),
            }
        )
    return out


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _counts_text(counts: dict | None) -> str:
    counts = counts or {}
    names = (
        ("contexts", L("Kontext", "context"), L("Kontexte", "contexts")),
        ("rib", L("wirksame Route", "effective route"), L("wirksame Routen", "effective routes")),
        ("bgp", L("BGP-Pfad", "BGP path"), L("BGP-Pfade", "BGP paths")),
        ("static", L("statische Route", "static route"), L("statische Routen", "static routes")),
        ("peers", L("BGP-Nachbar", "BGP neighbor"), L("BGP-Nachbarn", "BGP neighbors")),
        ("policy_routes", L("Policy-Route/SD-WAN", "policy route/SD-WAN"),
         L("Policy-Routen/SD-WAN", "policy routes/SD-WAN")),
        ("ha", L("HA-Cluster", "HA cluster"), L("HA-Cluster", "HA clusters")),
        ("l3outs", "L3Out", "L3Outs"),
    )  # fmt: skip
    parts = [_count(counts[k], one, many) for k, one, many in names if counts.get(k)]
    return ", ".join(parts) or L("keine Einträge", "no entries")


def _kinds_text(kinds: dict | None) -> str:
    names = {
        "route": (L("Route", "route"), L("Routen", "routes")),
        "bgp_peer": (L("BGP-Nachbar", "BGP neighbor"), L("BGP-Nachbarn", "BGP neighbors")),
        "policy_route": (L("Policy-Route/SD-WAN", "policy route/SD-WAN"),
                         L("Policy-Routen/SD-WAN", "policy routes/SD-WAN")),
        "context": (L("Kontext", "context"), L("Kontexte", "contexts")),
        "ha": ("HA", "HA"),
        "l3out": ("L3Out", "L3Outs"),
    }  # fmt: skip
    return ", ".join(_count(n, *names.get(k, (k, k))) for k, n in sorted((kinds or {}).items()))


def summary(row, row_fields: list[dict] | None = None) -> str:
    """Kurztext „vorher → nachher“ bzw. der Vermerk."""
    before = row.before if isinstance(row.before, dict) else {}
    after = row.after if isinstance(row.after, dict) else {}
    if row.change == "baseline":
        counts = _counts_text(after.get("counts"))
        return L(f"Ausgangsstand: {counts} – der Verlauf beginnt hier",
                 f"Baseline: {counts} – the history starts here")  # fmt: skip
    if row.change == "truncated":
        n, size = after.get("count", 0), after.get("limit", LIMIT)
        kinds = _kinds_text(after.get("kinds"))
        return L(f"{n} weitere Änderungen nicht einzeln gespeichert (Obergrenze {size} je Lauf): "
                 f"{kinds}",
                 f"{n} more changes not stored individually (limit {size} per run): "
                 f"{kinds}")  # fmt: skip
    if row.kind == "context":
        if "node" in before or "node" in after:
            return L(f"Gerät heisst jetzt {after.get('node')} (vorher {before.get('node')}), "
                     "z. B. nach einem HA-Wechsel",
                     f"Device is now named {after.get('node')} (previously {before.get('node')}), "
                     "e.g. after an HA failover")  # fmt: skip
        if row.change == "added":
            return L(f"Kontext neu gelesen: {_counts_text(after.get('counts'))}",
                     f"Context newly read: {_counts_text(after.get('counts'))}")  # fmt: skip
        if row.change == "removed":
            return L(f"Kontext nicht mehr gelesen: {_counts_text(before.get('counts'))}",
                     f"Context no longer read: {_counts_text(before.get('counts'))}")  # fmt: skip
        n = after.get("not_compared", 0)
        entries = _count(n, L("Eintrag", "entry"), L("Einträge", "entries"))
        if row.change == "unreadable":
            what = ", ".join(_labels_of(after.get("missing")))
            return L(f"nicht lesbar: {what} – {entries} nicht verglichen (keine Entfernung)",
                     f"not readable: {what} – {entries} not compared (no removal)")  # fmt: skip
        what = ", ".join(_labels_of(before.get("missing")))
        return L(f"wieder lesbar: {what} – {entries} als neuer Ausgangsstand",
                 f"readable again: {what} – {entries} as new baseline")  # fmt: skip
    row_fields = fields(row) if row_fields is None else row_fields
    parts = []
    for f in row_fields[:3]:
        if row.change == "changed":
            parts.append(f"{f['label']}: {f['before'] or '–'} → {f['after'] or '–'}")
        else:
            parts.append(
                f"{f['label']} {(f['after'] if row.change == 'added' else f['before']) or '–'}"
            )
    more = len(row_fields) - 3
    tail = L(f" (+{more} weitere)", f" (+{more} more)") if more > 0 else ""
    return " · ".join(parts) + tail
