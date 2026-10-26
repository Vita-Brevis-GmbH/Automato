"""L2-Sicht (0.64.0, ADR 0025 Nr. 9): Plan der Verkabelung, Ausfall, Redundanz, VLAN-Wege und
L2-Abschnitte in der Weg-Suche - abgeleitet aus der Verkabelung (`store/cabling`), rein lesend.

**Graph**: Knoten = Geraete der Verkabelung, Kanten = alle Kabel zwischen zwei Geraeten (mehrere
Kabel bzw. ein Port-Channel = mehrere Links derselben Kante). Weiterleitend sind Switches, Leafs,
Spines und Geraete, die sich per CDP/LLDP als Switch/Bridge/Router melden
(`cabling.Device.switching`); ESXi-Hosts, Firewalls und Telefone sind Endgeraete - durch sie
fuehrt kein Weg.

**Plan** (`plan`): Ebenen ab dem Kern - Spines bzw. die weiterleitenden Geraete mit den meisten
weiterleitenden Nachbarn je Teilnetz -, darunter je Abstand die weiteren Switches, Endgeraete
eine Ebene unter ihrem obersten Nachbarn; innerhalb der Ebene nach den Nachbarn darueber sortiert.
Mit `vlan` je Kante, ob sie das VLAN durchlaesst (ja/nein/unbekannt), welche Geraete es nutzen
und in welche Inseln es zerfaellt.

**Ausfall** (`failure`): ein Geraet bzw. ein Kabel faellt weg - welche Geraete verlieren die
Verbindung zum groessten verbleibenden Teil (meiste Switches)?

**Befunde** (`checks`): Einzelkabel (`l2_single_link`), alle Kabel am selben Switch
(`l2_single_switch`), Switch als einzelner Ausfallpunkt (`l2_single_point`), Port-Channel mit
nicht verbundenen Mitgliedern (`lag_degraded`), VLAN in Inseln getrennt (`vlan_split`).

**Weg-Suche** (`annotate`): zwischen zwei Hops die Switches vom Ausgang (physischer Port hinter
VLAN-Subinterface bzw. Aggregat) bis zum naechsten Geraet, im VLAN des Ausgangs; Quelle und Ziel
mit ihrem Anschluss (MAC-Tabelle/ARP, `store/l2`) und dem Weg dorthin.
"""

import ipaddress
from collections import defaultdict, deque
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import InvInterface
from app.store import cabling as cab_store
from app.store.cabling import Cabling, Device, End, Link, device_key, port_key, vlan_set

SWITCHING_KINDS = {"switch", "leaf", "spine"}
UP = {"connected", "up"}
MAX_HOPS = 12
X_STEP, Y_STEP = 190, 130


def allows(iface: InvInterface | None, vlan: int) -> bool | None:
    """Laesst der Port das VLAN durch? None = Konfiguration unbekannt."""
    if iface is None or not iface.mode:
        return None
    if iface.mode == "access":
        return iface.untagged_vlan == vlan if iface.untagged_vlan else None
    vs = vlan_set(iface.trunk_vlans) if iface.trunk_vlans else set(iface.tagged_vlans or [])
    if vs is None:
        return None
    native = {v for v in (iface.native_vlan, iface.untagged_vlan) if v}
    if not vs and not native:
        return None
    return vlan in vs or vlan in native


def link_allows(link: Link, vlan: int) -> bool | None:
    seen = [allows(e.iface, vlan) for e in (link.a, link.b)]
    if False in seen:
        return False
    return True if True in seen else None


def _label(end: End) -> str:
    """Port bzw. Port-Channel des Endes."""
    if end.iface is not None and end.iface.lag:
        return end.iface.lag
    return end.port or "?"


def _attrs(iface: InvInterface | None) -> dict:
    return (iface.attrs or {}) if iface is not None else {}


def _edge_vlan(states: list[str]) -> str:
    """Zustand einer Kante (mehrere Kabel) fuer ein VLAN: yes, no, partial, standby (STP
    blockiert, laesst das VLAN aber zu) bzw. unknown."""
    if "yes" in states:
        return "partial" if "no" in states else "yes"
    if "standby" in states:
        return "standby"
    if "no" in states:
        return "no"
    return "unknown"


@dataclass
class Step:
    device: Device
    into: str | None = None  # Port, an dem der Weg ankommt
    out: str | None = None  # Port, ueber den er weitergeht

    def out_dict(self) -> dict:
        return {"device": self.device.name, "kind": self.device.kind, "read": self.device.read,
                "in": self.into, "out": self.out}  # fmt: skip


@dataclass
class Segment:
    steps: list[Step] = field(default_factory=list)
    vlan: int | None = None
    blocked: list[str] = field(default_factory=list)  # Kanten, die das VLAN nicht durchlassen

    @property
    def text(self) -> str:
        parts = []
        for s in self.steps:
            ports = [p for p in (s.into, s.out) if p]
            if len(ports) == 2:
                parts.append(f"{s.device.name} ({ports[0]} → {ports[1]})")
            else:
                parts.append(f"{s.device.name} {ports[0]}" if ports else s.device.name)
        return " → ".join(parts)

    def out(self) -> dict:
        return {"steps": [s.out_dict() for s in self.steps], "vlan": self.vlan, "text": self.text,
                "switches": sum(1 for s in self.steps if s.device.switching)}  # fmt: skip


class L2:
    """Graph der Verkabelung eines Mandanten."""

    def __init__(self, session: Session, tenant_id: int, cab: Cabling | None = None):
        self.session, self.tenant_id = session, tenant_id
        self.cab = cab or cab_store.build(session, tenant_id)
        self.adj: dict[str, dict[str, list[Link]]] = defaultdict(lambda: defaultdict(list))
        for link in self.cab.links.values():
            a, b = link.a.device.key, link.b.device.key
            self.adj[a][b].append(link)
            self.adj[b][a].append(link)
        self.devices = self.cab.devices
        self._members: dict[tuple[str, str], list[InvInterface]] | None = None

    # Grundlagen ------------------------------------------------------------------------------

    def switching(self, key: str) -> bool:
        dev = self.devices.get(key)
        return dev is not None and dev.switching

    # Spanning Tree und vPC (0.65.0) --------------------------------------------------------

    def _lag_iface(self, end: End) -> InvInterface | None:
        """Port-Channel des Endes - STP und vPC stehen am Port-Channel, nicht am Mitglied."""
        if end.iface is None or not end.iface.lag:
            return None
        return self.cab.ifaces.get((end.device.key, port_key(end.iface.lag)))

    def stp_blocked(self, end: End) -> set[int]:
        out: set[int] = set()
        for i in (end.iface, self._lag_iface(end)):
            text = _attrs(i).get("stp_blocked")
            if text:
                out |= vlan_set(text) or set()
        return out

    def link_blocked(self, link: Link, vlan: int) -> bool:
        """Blockiert Spanning Tree das Kabel im VLAN (an einem Ende)?"""
        return any(vlan in self.stp_blocked(e) for e in (link.a, link.b))

    def link_state(self, link: Link, vlan: int) -> str:
        ok = link_allows(link, vlan)
        if ok is False:
            return "no"
        if self.link_blocked(link, vlan):
            return "standby"
        return "yes" if ok else "unknown"

    def vpc_pairs(self) -> list[dict]:
        """vPC-Paare: der Port-Channel mit `vpc_peer_link` und das weiterleitende Geraet an
        seinen Mitgliedern (CDP/LLDP). Je Seite Domaene, Rolle, Peer- und Keepalive-Status."""
        pairs: dict[tuple[str, str], dict] = {}
        for i in self.cab.ports:
            a = _attrs(i)
            if not a.get("vpc_peer_link"):
                continue
            dk, po = device_key(i.device), port_key(i.name)
            peers = set()
            for links in self.adj.get(dk, {}).values():
                for lk in links:
                    mine = lk.a if lk.a.device.key == dk else lk.b
                    other = lk.b if mine is lk.a else lk.a
                    lag = mine.iface.lag if mine.iface is not None else None
                    if lag and port_key(lag) == po and other.device.switching:
                        peers.add(other.device.key)
            if len(peers) != 1:
                continue
            key = tuple(sorted((dk, peers.pop())))
            entry = pairs.setdefault(key, {"devices": list(key), "domain": a["vpc_peer_link"],
                                           "sides": {}})  # fmt: skip
            entry["sides"][dk] = {"peer_link": i.name, "role": a.get("vpc_role"),
                                  "peer": a.get("vpc_peer"), "keepalive": a.get("vpc_keepalive"),
                                  "peer_link_status": a.get("vpc_peer_link_status")}  # fmt: skip
        return list(pairs.values())

    def vpcs_of(self, key: str) -> dict[str, InvInterface]:
        """vPCs eines Switches: Nummer -> Port-Channel (ohne Peer-Link)."""
        return {str(_attrs(i)["vpc"]): i for i in self.cab.ports
                if device_key(i.device) == key and _attrs(i).get("vpc")
                and not _attrs(i).get("vpc_peer_link")}  # fmt: skip

    def nodes(self, ends: bool = True) -> list[str]:
        keys = {k for k in self.adj} | {k for k, d in self.devices.items() if d.read
                                        and d.kind in SWITCHING_KINDS}  # fmt: skip
        if not ends:
            keys = {k for k in keys if self.switching(k)}
        return sorted(keys)

    def components(self, keys: set[str], skip_devs: set[str] | frozenset = frozenset(),
                   skip_links: set[str] | frozenset = frozenset()) -> list[set[str]]:  # fmt: skip
        """Zusammenhaengende Teile ueber die weiterleitenden Geraete (ohne die ausgefallenen
        Geraete bzw. Kabel); ein Endgeraet gehoert zum groessten Teil, an dem es haengt - durch
        Endgeraete fuehrt kein Weg."""

        def usable(a: str, b: str) -> bool:
            return any(lk.id not in skip_links for lk in self.adj[a][b])

        comp_of: dict[str, int] = {}
        parts: list[set[str]] = []
        for start in sorted(k for k in keys if self.switching(k) and k not in skip_devs):
            if start in comp_of:
                continue
            idx, part, queue = len(parts), {start}, deque([start])
            comp_of[start] = idx
            while queue:
                cur = queue.popleft()
                for nb in self.adj.get(cur, {}):
                    if nb in comp_of or nb in skip_devs or nb not in keys:
                        continue
                    if not self.switching(nb) or not usable(cur, nb):
                        continue
                    comp_of[nb] = idx
                    part.add(nb)
                    queue.append(nb)
            parts.append(part)
        for k in sorted(keys):
            if k in skip_devs or self.switching(k):
                continue
            cands = {comp_of[nb] for nb in self.adj.get(k, {}) if nb in comp_of and usable(k, nb)}
            if cands:
                parts[max(cands, key=lambda i: (len(parts[i]), -i))].add(k)
            else:
                parts.append({k})
        return parts

    # Plan ------------------------------------------------------------------------------------

    def layers(self, keys: list[str]) -> dict[str, int]:
        """Ebene je Geraet: Kern = Spines bzw. die meistvernetzten Switches je Teilnetz."""
        sw = [k for k in keys if self.switching(k)]
        level: dict[str, int] = {}
        for part in self.components(set(sw)):
            cand = [k for k in part if self.devices.get(k) and self.devices[k].kind == "spine"]
            if not cand:
                degree = {k: sum(1 for nb in self.adj.get(k, {}) if nb in part) for k in part}
                top = max(degree.values(), default=0)
                cand = [k for k in part if degree[k] == top]
            queue = deque(cand)
            for k in cand:
                level[k] = 0
            while queue:
                cur = queue.popleft()
                for nb in self.adj.get(cur, {}):
                    if nb in part and nb not in level:
                        level[nb] = level[cur] + 1
                        queue.append(nb)
        for pr in self.vpc_pairs():  # vPC-Peers auf dieselbe Ebene
            a, b = pr["devices"]
            if a in level and b in level:
                level[a] = level[b] = min(level[a], level[b])
        for k in keys:
            if k in level:
                continue
            ups = [level[nb] for nb in self.adj.get(k, {}) if nb in level]
            level[k] = (min(ups) + 1) if ups else 0
        return level

    def plan(self, ends: bool = True, vlan: int | None = None) -> dict:
        keys = self.nodes(ends)
        level = self.layers(keys)
        rows: dict[int, list[str]] = defaultdict(list)
        for k in keys:
            rows[level[k]].append(k)
        pos: dict[str, tuple[float, float]] = {}
        width = max((len(r) for r in rows.values()), default=1)
        pairs = self.vpc_pairs()
        partner: dict[str, str] = {}
        for pr in pairs:
            a, b = pr["devices"]
            partner[a], partner[b] = b, a
        for lv in sorted(rows):
            row = rows[lv]

            def bary(k: str, lv: int = lv) -> tuple:
                ups = [pos[nb][0] for nb in self.adj.get(k, {}) if nb in pos and level[nb] < lv]
                dev = self.devices.get(k)
                kind = 0 if self.switching(k) else 1
                return (sum(ups) / len(ups) if ups else 1e9, kind, (dev.name if dev else k).lower())

            row.sort(key=bary)
            ordered: list[str] = []
            for k in row:  # vPC-Peers nebeneinander
                if k in ordered:
                    continue
                ordered.append(k)
                if partner.get(k) in row and partner[k] not in ordered:
                    ordered.append(partner[k])
            row[:] = ordered
            offset = (width - len(row)) * X_STEP / 2
            for i, k in enumerate(row):
                pos[k] = (offset + i * X_STEP + X_STEP / 2, lv * Y_STEP + 40)
        present = self.vlan_present(vlan) if vlan else set()
        nodes = []
        for k in keys:
            dev = self.devices.get(k) or Device(key=k, name=k, kind="unknown")
            nodes.append({"key": k, "name": dev.name, "kind": dev.kind, "read": dev.read,
                          "switching": dev.switching, "layer": level[k], "x": pos[k][0],
                          "y": pos[k][1], "source": dev.source,
                          "vlan_present": k in present if vlan else None})  # fmt: skip
        edges = []
        shown = set(keys)
        for a in keys:
            for b, links in self.adj.get(a, {}).items():
                if b not in shown or b < a:
                    continue
                states = [lk.state for lk in links]
                state = ("one_sided" if "one_sided" in states else
                         "unread" if "unread" in states else "both")  # fmt: skip
                edge = {"a": a, "b": b, "ids": [lk.id for lk in links], "links": len(links),
                        "state": state,
                        "issues": sum(len(lk.issues) for lk in links),
                        "lags": sorted({_label(e) for lk in links for e in (lk.a, lk.b)
                                        if e.iface is not None and e.iface.lag}),
                        "ports": [[_label(lk.a) if lk.a.device.key == a else _label(lk.b),
                                   _label(lk.b) if lk.b.device.key == b else _label(lk.a)]
                                  for lk in links]}  # fmt: skip
                blocked = set().union(*(self.stp_blocked(e) for lk in links
                                        for e in (lk.a, lk.b)))  # fmt: skip
                edge["stp"] = cab_store._ranges(blocked) if blocked else None
                if vlan:
                    edge["vlan"] = _edge_vlan([self.link_state(lk, vlan) for lk in links])
                edges.append(edge)
        height = (max(rows, default=0) + 1) * Y_STEP + 40
        out = {"nodes": nodes, "edges": edges, "width": width * X_STEP, "height": height,
               "pairs": [{"a": pr["devices"][0], "b": pr["devices"][1], "domain": pr["domain"]}
                         for pr in pairs if set(pr["devices"]) <= shown]}  # fmt: skip
        if vlan:
            islands = self.vlan_islands(vlan, present)
            names = [[self._name(k) for k in sorted(p, key=self._name)] for p in islands]
            out["vlan"] = {"vid": vlan, "present": sorted(self._name(k) for k in present),
                           "islands": names}  # fmt: skip
        return out

    def _name(self, key: str) -> str:
        dev = self.devices.get(key)
        return dev.name if dev else key

    # VLAN-Wege -------------------------------------------------------------------------------

    def vlan_present(self, vlan: int) -> set[str]:
        """Geraete, die das VLAN nutzen: verbundene Access-Ports bzw. SVI mit Adresse auf
        gelesenen Switches; Firewalls mit einem Subinterface im VLAN."""
        out: set[str] = set()
        svi = (f"vl{vlan}", f"vlan{vlan}")
        for i in self.cab.ports:
            key = device_key(i.device)
            if i.source_system_id in self.cab.switch_sources:
                if i.kind == "physical" and i.mode == "access" and i.untagged_vlan == vlan \
                        and (i.status or "") in UP:  # fmt: skip
                    out.add(key)
                elif i.kind == "vlan" and i.ips and port_key(i.name) in svi:
                    out.add(key)
            elif self.devices.get(key) and self.devices[key].kind == "firewall" and i.parent \
                    and vlan in (i.tagged_vlans or []):  # fmt: skip
                out.add(key)
        return {k for k in out if k in self.adj or self.switching(k)}

    def vlan_islands(self, vlan: int, present: set[str] | None = None) -> list[set[str]]:
        """Teile, in die das VLAN zwischen den Geraeten zerfaellt, die es nutzen (Kanten, die es
        sicher sperren, fallen weg; unbekannte zaehlen als offen)."""
        present = self.vlan_present(vlan) if present is None else present
        if len(present) < 2:
            return [present] if present else []
        seen: set[str] = set()
        islands: list[set[str]] = []
        for start in sorted(present):
            if start in seen:
                continue
            part, queue = {start}, deque([start])
            seen.add(start)
            while queue:
                cur = queue.popleft()
                if cur != start and not self.switching(cur):
                    continue
                for nb, links in self.adj.get(cur, {}).items():
                    if nb in seen or all(link_allows(lk, vlan) is False for lk in links):
                        continue
                    seen.add(nb)
                    part.add(nb)
                    queue.append(nb)
            islands.append(part & present)
        return [p for p in islands if p]

    # Ausfall ---------------------------------------------------------------------------------

    def failure(self, devices: list[str] | None = None, links: list[str] | None = None) -> dict:
        """Was ist ohne die Geraete bzw. Kabel getrennt (auch mehrere gleichzeitig)? Bezug ist
        der groesste verbleibende Teil (meiste Switches); getrennt ist, was vorher mit ihm
        verbunden war."""
        keys = set(self.nodes())
        devs = {device_key(d) for d in devices or [] if d}
        cut_links = set(links or [])
        before = self.components(keys)
        after = self.components(keys, skip_devs=devs, skip_links=cut_links)
        names = sorted(self._name(k) for k in devs)
        base = {"devices": names, "device": names[0] if len(names) == 1 else None,
                "links": sorted(cut_links), "link": next(iter(cut_links)) if len(cut_links) == 1
                else None}  # fmt: skip
        if not after:
            return {**base, "cut_off": [], "main": [], "parts": [], "cut_off_keys": []}

        def size(p: set[str]) -> tuple:
            return (sum(1 for k in p if self.switching(k)), len(p), sorted(p, reverse=True))

        main = max(after, key=size)
        homes = [p for p in before if p & main]
        home = homes[0] if homes else set()
        cut = [p for p in after if p is not main and p & home]
        return {
            **base,
            "main": sorted(self._name(k) for k in main),
            # alle Teile des Netzes, in dem die Geraete bzw. Kabel waren, groesste zuerst
            "parts": [sorted(self._name(k) for k in p)
                      for p in sorted((p for p in after if p & home), key=size, reverse=True)],
            "cut_off": [sorted(self._name(k) for k in p) for p in sorted(cut, key=size,
                                                                        reverse=True)],
            "cut_off_keys": sorted(k for p in cut for k in p),
        }  # fmt: skip

    # Befunde ---------------------------------------------------------------------------------

    def checks(self) -> list[dict]:
        out: list[dict] = []
        sources = self.cab.switch_sources
        sw_read = {k for k, d in self.devices.items()
                   if d.read and d.kind == "switch" and d.source_system_id in sources}  # fmt: skip
        if not sw_read:
            return out
        pairs = self.vpc_pairs()
        partner: dict[str, str] = {}
        for pr in pairs:
            x, y = pr["devices"]
            partner[x], partner[y] = y, x
        for key in sorted(self.adj):
            dev = self.devices.get(key)
            if dev is None or not (key in sw_read or dev.kind == "esxi"):
                continue
            ups = {nb: links for nb, links in self.adj[key].items() if self.switching(nb)}
            if not ups:
                continue
            count = sum(len(v) for v in ups.values())
            if count == 1:
                nb, links = next(iter(ups.items()))
                lk = links[0]
                mine = lk.a if lk.a.device.key == key else lk.b
                other = lk.b if mine is lk.a else lk.a
                out.append(self._issue("l2_single_link", "warning", f"{dev.name}|{nb}", dev,
                                       f"{dev.name} hängt mit einem Kabel an {other.device.name} "
                                       f"({mine.port or '?'} ↔ {other.port or '?'})",
                                       neighbors=[other.device.name]))  # fmt: skip
            elif len(ups) == 1:
                nb = next(iter(ups))
                peer = partner.get(nb)
                hint = f" – {self._name(peer)} (vPC-Peer) nicht genutzt" if peer else ""
                out.append(self._issue("l2_single_switch",
                                       "warning" if dev.kind == "esxi" else "info",
                                       f"{dev.name}|{nb}", dev,
                                       f"alle {count} Kabel von {dev.name} enden an "
                                       f"{self._name(nb)}{hint}",
                                       neighbors=[self._name(nb)], vpc_peer=self._name(peer)
                                       if peer else None))  # fmt: skip
        out += self._single_points(sw_read)
        out += self._lags()
        out += self._vlan_splits()
        out += self._vpc(pairs)
        return out

    def _vpc(self, pairs: list[dict]) -> list[dict]:
        """vPC-Paare: Peer nicht verbunden (Fehler), vPC nur auf einem Peer, vPC down bzw.
        Konsistenz nicht erfolgreich (Warnung)."""
        out = []
        for pr in pairs:
            a, b = pr["devices"]
            tag = f"vpc|{a}~{b}"
            domain = pr["domain"]
            for side, info in sorted(pr["sides"].items()):
                dev = self.devices.get(side)
                peer = (info.get("peer") or "").lower()
                if peer and "formed ok" not in peer:
                    out.append(self._issue("vpc_mismatch", "error", f"{tag}|peer|{side}", dev,
                                           f"vPC-Domäne {domain}: {self._name(side)} meldet "
                                           f"„{info['peer']}“ (Keepalive: "
                                           f"{info.get('keepalive') or '?'})", domain=domain,
                                           pair=[self._name(a), self._name(b)]))  # fmt: skip
            ids = {k: self.vpcs_of(k) for k in (a, b)}
            both_read = all(self.devices.get(k) and self.devices[k].read
                            and k in pr["sides"] for k in (a, b))  # fmt: skip
            for k in (a, b):
                other = b if k == a else a
                for vid, po in sorted(ids[k].items()):
                    dev = self.devices.get(k)
                    attrs = _attrs(po)
                    if both_read and vid not in ids[other]:
                        out.append(self._issue("vpc_mismatch", "warning", f"{tag}|{vid}|{k}",
                                               dev, f"vPC {vid} nur auf {self._name(k)} "
                                               f"({po.name}), nicht auf {self._name(other)}",
                                               domain=domain, vpc=vid))  # fmt: skip
                    status = (attrs.get("vpc_status") or "").lower()
                    consistency = (attrs.get("vpc_consistency") or "").lower()
                    if status.startswith("down") or consistency not in ("", "success"):
                        text = (f"vPC {vid} auf {self._name(k)} ({po.name}): {status or '?'}, "
                                f"Konsistenz {consistency or '?'}")  # fmt: skip
                        out.append(self._issue("vpc_mismatch", "warning", f"{tag}|{vid}|{k}|st",
                                               dev, text, domain=domain, vpc=vid))  # fmt: skip
        return out

    def _issue(self, kind: str, severity: str, subject: str, dev: Device | None, text: str,
               **detail) -> dict:  # fmt: skip
        return {"type": kind, "severity": severity, "subject": subject.lower(), "text": text,
                "device": dev.name if dev else None,
                "source_system_id": dev.source_system_id if dev else None, **detail}  # fmt: skip

    def _single_points(self, sw_read: set[str]) -> list[dict]:
        """Switch, ohne den andere Switches vom Rest getrennt sind."""
        out = []
        switching = {k for k in self.nodes() if self.switching(k)}
        if len(switching) < 3:
            return out
        keys = set(self.nodes())
        before = len([p for p in self.components(keys) if any(self.switching(k) for k in p)])
        for key in sorted(switching & sw_read):
            parts = [sorted(self._name(k) for k in p if self.switching(k))
                     for p in self.components(keys, skip_devs={key})]  # fmt: skip
            parts = sorted((p for p in parts if p), key=lambda p: (-len(p), p))
            if len(parts) <= before:
                continue
            dev = self.devices[key]
            shown = " | ".join(", ".join(p[:6]) + (" …" if len(p) > 6 else "") for p in parts[:6])
            out.append(self._issue("l2_single_point", "info", key, dev,
                                   f"fällt {dev.name} aus, zerfällt das Netz in {len(parts)} "
                                   f"Teile: {shown}", parts=parts))  # fmt: skip
        return out

    def _lags(self) -> list[dict]:
        """Port-Channel gelesener Switches mit Mitgliedern, die nicht verbunden sind."""
        out = []
        members: dict[tuple[str, str], list[InvInterface]] = defaultdict(list)
        for i in self.cab.ports:
            if i.lag and i.kind == "physical" and i.source_system_id in self.cab.switch_sources:
                members[(i.device, i.lag)].append(i)
        for (device, lag), ms in sorted(members.items()):
            up = [m for m in ms if (m.status or "") in UP]
            down = [m for m in ms if (m.status or "") not in UP]
            if not up or not down:
                continue
            dev = self.devices.get(device_key(device))
            where = ", ".join(f"{m.name} {m.status or '?'}" for m in down)
            out.append(self._issue("lag_degraded", "warning", f"{device}|{port_key(lag)}", dev,
                                   f"{device} {lag}: {len(up)} von {len(ms)} Mitgliedern "
                                   f"verbunden ({where})", lag=lag,
                                   members=[m.name for m in ms],
                                   down=[m.name for m in down]))  # fmt: skip
        return out

    def _vlan_splits(self) -> list[dict]:
        out = []
        vids: set[int] = set()
        for i in self.cab.ports:
            if i.source_system_id not in self.cab.switch_sources:
                continue
            if i.mode == "access" and i.untagged_vlan and (i.status or "") in UP:
                vids.add(i.untagged_vlan)
            if i.kind == "vlan" and i.ips:
                digits = "".join(c for c in i.name if c.isdigit())
                if digits:
                    vids.add(int(digits))
        for vid in sorted(v for v in vids if 1 < v < 4095 and not 1002 <= v <= 1005):
            islands = self.vlan_islands(vid)
            if len(islands) < 2:
                continue
            groups = [sorted(self._name(k) for k in p) for p in islands]
            groups.sort(key=lambda g: (-len(g), g))
            text = f"VLAN {vid} ist getrennt: " + " | ".join(", ".join(g) for g in groups)
            first = self.devices.get(next(iter(sorted(islands[0]))))
            out.append(self._issue("vlan_split", "warning", f"vlan|{vid}", first, text,
                                   vlan=vid, islands=groups))  # fmt: skip
        return out

    # Weg-Suche -------------------------------------------------------------------------------

    def _ports_of(self, key: str) -> dict[str, InvInterface]:
        return {pk: i for (dk, pk), i in self.cab.ifaces.items() if dk == key}

    def physical(self, device: str, interface: str | None) -> tuple[set[str], int | None]:
        """Physische Ports hinter einem Interface (VLAN-Subinterface -> Parent, Aggregat ->
        Mitglieder) und das VLAN des Subinterfaces."""
        key = device_key(device)
        if not interface:
            return set(), None
        ports = self._ports_of(key)
        row = ports.get(port_key(interface))
        vlan = None
        seen = 0
        while row is not None and row.parent and seen < 3:
            if row.tagged_vlans and len(row.tagged_vlans) == 1 and vlan is None:
                vlan = row.tagged_vlans[0]
            row = ports.get(port_key(row.parent))
            seen += 1
        if row is None:
            return {port_key(interface)}, vlan
        members = {pk for pk, i in ports.items() if i.lag and port_key(i.lag) == port_key(row.name)}
        return (members or {port_key(row.name)}), vlan

    def route(self, starts: list[tuple[End, End]], goal, vlan: int | None,
              stp: bool = True) -> Segment | None:  # fmt: skip
        """Kuerzester Weg ab den Kabeln `starts` (eigenes Ende, Gegenstelle) bis zu einem
        Geraet mit `goal(key)`; nur ueber weiterleitende Geraete und Kanten, die das VLAN nicht
        sicher sperren und (mit `stp`) nicht von Spanning Tree blockiert sind. Fuehrt ohne die
        blockierten Kabel kein Weg zum Ziel (Stand der Laeufe verschieden), dann mit ihnen."""
        best: dict[str, tuple] = {}
        queue: deque = deque()
        for mine, other in starts:
            first = self._link(mine, other)
            if vlan is not None and (link_allows(first, vlan) is False
                                     or (stp and self.link_blocked(first, vlan))):  # fmt: skip
                continue
            key = other.device.key
            if key not in best:
                best[key] = (None, mine, other)
                queue.append((key, 1))
        found = None
        while queue:
            cur, depth = queue.popleft()
            if goal(cur):
                found = cur
                break
            if depth >= MAX_HOPS or not self.switching(cur):
                continue
            for nb, links in self.adj.get(cur, {}).items():
                if nb in best:
                    continue
                usable = [
                    lk
                    for lk in links
                    if vlan is None
                    or (link_allows(lk, vlan) is not False
                        and not (stp and self.link_blocked(lk, vlan)))
                ]  # fmt: skip
                if not usable:
                    continue
                lk = usable[0]
                mine = lk.a if lk.a.device.key == cur else lk.b
                other = lk.b if mine is lk.a else lk.a
                best[nb] = (cur, mine, other)
                queue.append((nb, depth + 1))
        if found is None:
            return self.route(starts, goal, vlan, stp=False) if vlan is not None and stp else None
        chain = []
        cur = found
        while cur is not None:
            prev, mine, other = best[cur]
            chain.append((prev, mine, other))
            cur = prev
        chain.reverse()
        steps = [Step(chain[0][1].device, out=_label(chain[0][1]))]
        for i, (_prev, _mine, other) in enumerate(chain):
            step = Step(other.device, into=_label(other))
            if i + 1 < len(chain):
                step.out = _label(chain[i + 1][1])
            steps.append(step)
        return Segment(steps, vlan)

    @staticmethod
    def _link(a: End, b: End) -> Link:
        return Link("", a, b)

    def starts_at(self, device: str, ports: set[str]) -> list[tuple[End, End]]:
        key = device_key(device)
        out = []
        for links in self.adj.get(key, {}).values():
            for lk in links:
                mine = lk.a if lk.a.device.key == key else lk.b
                other = lk.b if mine is lk.a else lk.a
                if not ports or port_key(mine.port) in ports:
                    out.append((mine, other))
        return out

    def between(self, a_devices: list[str], a_iface: str | None, b_devices: list[str],
                b_iface: str | None = None) -> Segment | None:  # fmt: skip
        """L2 vom Ausgang eines Hops bis zum naechsten Geraet."""
        targets = {device_key(d) for d in b_devices}
        if not targets:
            return None
        b_ports: dict[str, set[str]] = {}
        for d in b_devices:
            b_ports[device_key(d)] = self.physical(d, b_iface)[0] if b_iface else set()
        for dev in a_devices:
            ports, vlan = self.physical(dev, a_iface)
            starts = self.starts_at(dev, ports)
            if not starts:
                continue
            seg = self.route(starts, lambda k: k in targets, vlan)
            if seg is not None:
                last = seg.steps[-1]
                want = b_ports.get(last.device.key)
                if want and last.into and port_key(last.into) not in want:
                    alt = [lk for lk in self.adj.get(seg.steps[-2].device.key, {})
                           .get(last.device.key, [])]  # fmt: skip
                    for lk in alt:
                        end = lk.a if lk.a.device.key == last.device.key else lk.b
                        if port_key(end.port) in want:
                            last.into = _label(end)
                            break
                return seg
        return None

    def to_port(self, a_devices: list[str], a_iface: str | None, device: str,
                port: str) -> Segment | None:  # fmt: skip
        """L2 vom Ausgang bis zum Anschluss (Switch, Port) eines Endpunkts."""
        target = device_key(device)
        for dev in a_devices:
            ports, vlan = self.physical(dev, a_iface)
            starts = self.starts_at(dev, ports)
            if not starts:
                continue
            seg = self.route(starts, lambda k: k == target, vlan)
            if seg is not None:
                seg.steps[-1].out = port
                return seg
        return None


# -- Weg-Suche ---------------------------------------------------------------------------------


def _single_ip(text: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((text or "").strip()))
    except ValueError:
        return None


def _located(session: Session, tenant_id: int, ip: str, loc) -> list[dict]:
    from app.store import l2

    found = l2.find_macs(session, tenant_id, ip)
    out = []
    for mac in found["macs"]:
        hit = loc.locate(mac)
        for row in hit["locations"]:
            out.append({"mac": mac, "device": row["device"], "port": row["port"],
                        "vlan": row["vlan"], "via": row["via"], "vm": hit["vm"]})  # fmt: skip
    return out


def annotate(session: Session, tenant_id: int, result: dict, src: str | None,
             dst: str | None) -> None:  # fmt: skip
    """L2-Abschnitte in das Ergebnis der Weg-Suche: `hop.l2` zwischen zwei Hops, `l2.src`/
    `l2.dst` (Anschluss der Quelle bzw. des Ziels) und der Weg vom letzten Hop zum Ziel."""
    from app.store import l2

    t = L2(session, tenant_id)
    if not t.cab.links:
        return
    loc = l2.Locator(session, tenant_id, t.cab)
    info: dict = {}
    for side, text in (("src", src), ("dst", dst)):
        ip = _single_ip(text)
        if ip:
            where = _located(session, tenant_id, ip, loc)
            if where:
                info[side] = where
    for path in result.get("paths") or []:
        hops = path.get("hops") or []
        for h, nxt in zip(hops, hops[1:], strict=False):
            out_if = (h.get("egress") or {}).get("interface")
            in_if = (nxt.get("ingress") or {}).get("interface")
            seg = t.between(h.get("members") or [], out_if, nxt.get("members") or [], in_if)
            if seg is not None:
                h["l2"] = seg.out()
        if hops and info.get("dst"):
            last = hops[-1]
            target = info["dst"][0]
            seg = t.to_port(last.get("members") or [],
                            (last.get("egress") or {}).get("interface"),
                            target["device"], target["port"])  # fmt: skip
            if seg is not None:
                last["l2"] = seg.out()
        if hops and info.get("src"):
            first = hops[0]
            origin = info["src"][0]
            seg = t.to_port(first.get("members") or [],
                            (first.get("ingress") or {}).get("interface"),
                            origin["device"], origin["port"])  # fmt: skip
            if seg is not None:
                seg.steps.reverse()
                for s in seg.steps:
                    s.into, s.out = s.out, s.into
                path["l2_src"] = seg.out()
    if info:
        result["l2"] = info


def checks(session: Session, tenant_id: int, cab: Cabling | None = None) -> list[dict]:
    return L2(session, tenant_id, cab).checks()
