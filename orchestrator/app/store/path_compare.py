"""Wege vergleichen (1.19.0, ADR 0067): ein L3-Weg gegen L1/L2.

Anwender 2026-11-17 (Prioritaet 3, „Modul nach Netzwerkschichten“): Modul „Wege vergleichen“ - ein
L3-Weg gegen L1/L2: VLAN auf allen Trunks des Wegs erlaubt, Kabel und Nachbarn passen, Spanning
Tree blockiert nicht; Befunde je Abweichung.

Ein **L3-Abschnitt** reicht vom Ausgang eines Hops (Geraete, Interface) bis zum Eingang des
naechsten Hops bzw. bis zum Anschluss von Quelle oder Ziel (MAC/ARP). Je Abschnitt:

- **VLAN** des Abschnitts: das VLAN-Subinterface bzw. die SVI des Ausgangs, sonst das Access- bzw.
  Native VLAN des Switch-Ports dahinter (wie `cabling.port_vlans`). Gesucht wird der Weg ueber die
  Kabel der Verkabelung (CDP/LLDP, Graph aus `l2_topo`) - zuerst nur ueber Kabel, die das VLAN
  zulassen und die Spanning Tree nicht blockiert; gelingt das nicht, ohne Spanning Tree, dann
  ohne VLAN (nur die Kabel).
- **VLAN erlaubt** (`vlan`): fuehrt nur ein Weg ueber Kabel, die das VLAN sperren (Access-VLAN
  anders, nicht unter den erlaubten VLANs des Trunks), ist das eine Abweichung mit den Ports;
  verschiedene VLANs am Aus- und Eingang ebenso. Kabel ohne bekannte VLAN-Konfiguration zaehlen.
- **Kabel und Nachbarn** (`cable`): je Kabel auf dem Weg die Befunde der Verkabelung - CDP/LLDP
  gegen das Kabel in NetBox (`cable_labels.neighbor_state`), Speed, Duplex, VLANs, Port-Channel
  abweichend (`cabling`), Duplex nicht voll. Ohne Kabel aus NetBox: gezaehlt.
- **Spanning Tree** (`stp`): fuehrt jeder Weg im VLAN ueber einen blockierten Port (Zustand je VLAN
  bzw. MST-Instanz aus `stp_instance`, sonst die blockierten VLANs am Port, err-disabled), ist
  das eine Abweichung mit den Ports. Switches ohne gelesenen Spanning Tree: gezaehlt.

Was nicht geprueft werden kann, wird je Grund gezaehlt (`REASONS`, ADR 0048: nie still). Tunnel
(VPN) und Inter-VDOM-Links sind keine L2-Abschnitte und stehen als solche da.

`sections` rechnet alle L3-Abschnitte eines Mandanten (Next-Hops der Routing-Tabellen, die einem
anderen gelesenen Geraet gehoeren, je Paar einmal) - Grundlage der Befunde
(`app/checks/vergleich/`); `Comparer.trace` vergleicht ein Ergebnis der Weg-Suche. Rein lesend,
nichts davon wird gespeichert (ausser den Befunden des Abgleichs).
"""

import json
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from sqlalchemy import Text, cast, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import StpDevice, StpInstance, TopoRoute
from app.store import cable_labels, cabling, l2_topo
from app.store.cabling import End, Link, device_key, port_key, vlan_set

KINDS = ("vlan", "cable", "stp")
OK, DEVIATION, PARTIAL, UNCHECKED = "ok", "deviation", "partial", "unchecked"
# Gruende, warum ein Abschnitt bzw. eine Pruefung nicht (ganz) geprueft ist
REASONS = ("no_interface", "fabric", "no_cabling", "no_path", "vlan_unknown", "vlan_ports",
           "no_netbox", "stp_unread")  # fmt: skip
NOT_L2 = ("vpn", "internal")  # keine L2-Abschnitte (nur ausgewiesen)
BLOCKED_STATES = frozenset({"blocking", "broken", "discarding"})
MAX_HOPS = 12
EXAMPLES = 10  # Abschnitte je Grund im Detail eines Befunds
_SVI = re.compile(r"^(?:vlan|vl|irb\.|vlanif)\s*(\d{1,4})$", re.I)


def reason_text(key: str) -> str:
    """Grund, warum etwas nicht geprueft ist - in der Sprache der Anfrage."""
    return {
        "no_interface": L("Aus- bzw. Eingang ohne Interface (rekursiver Next-Hop)",
                          "egress or ingress without interface (recursive next-hop)"),
        "fabric": L("im ACI-Fabric – L2 dort nicht abgebildet", "inside the ACI fabric – L2 not "
                    "modelled there"),
        "no_cabling": L("kein Kabel am Ausgang bekannt (CDP/LLDP)",
                        "no cable known at the egress (CDP/LLDP)"),
        "no_path": L("kein Weg über die Kabel – Switch dazwischen nicht gelesen",
                     "no path via the cables – switch in between not read"),
        "vlan_unknown": L("VLAN des Abschnitts unbekannt", "VLAN of the section unknown"),
        "vlan_ports": L("Kabel ohne bekannte VLAN-Konfiguration", "cables without known VLAN "
                        "configuration"),
        "no_netbox": L("Kabel ohne Kabel in NetBox", "cables without a cable in NetBox"),
        "stp_unread": L("Switch ohne gelesenen Spanning Tree", "switch without read Spanning Tree"),
    }.get(key, key)  # fmt: skip


def kind_text(kind: str) -> str:
    return {"vlan": L("VLAN erlaubt", "VLAN allowed"),
            "cable": L("Kabel und Nachbarn", "cables and neighbors"),
            "stp": "Spanning Tree"}.get(kind, kind)  # fmt: skip


def _label(end: End) -> str:
    """Port bzw. Port-Channel des Endes (wie `l2_topo`)."""
    if end.iface is not None and end.iface.lag:
        return end.iface.lag
    return end.port or "?"


def _port_out(end: End, **extra) -> dict:
    i = end.iface
    vlans = None
    if i is not None:
        if i.mode == "access":
            vlans = str(i.untagged_vlan) if i.untagged_vlan else None
        else:
            vlans = i.trunk_vlans or (",".join(map(str, i.tagged_vlans)) if i.tagged_vlans
                                      else None)  # fmt: skip
    return {"device": end.device.name, "port": end.port, "mode": i.mode if i is not None else None,
            "vlans": vlans, "native_vlan": i.native_vlan if i is not None else None,
            **extra}  # fmt: skip


@dataclass
class Check:
    state: str = OK
    items: list[dict] = field(default_factory=list)  # Abweichungen (Ports, Kabel)
    reasons: list[str] = field(default_factory=list)  # nicht (ganz) geprueft
    count: int = 0  # geprueft (Kabel bzw. Ports)

    def out(self) -> dict:
        return {"state": self.state, "items": self.items,
                "reasons": [{"key": r, "text": reason_text(r)} for r in self.reasons],
                "count": self.count}  # fmt: skip

    def miss(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)
        if self.state == OK:
            self.state = PARTIAL


@dataclass
class Section:
    """Ein L3-Abschnitt mit dem Ergebnis der drei Pruefungen."""

    a: dict  # {"label", "devices", "interface", "key"}
    b: dict
    kind: str = "l2"  # l2 | attach (Anschluss von Quelle/Ziel) | vpn | internal
    vlan: int | None = None
    vlan_from: str | None = None  # subinterface | svi | access | native | target
    segment: dict | None = None  # Schritte wie `hop.l2` der Weg-Suche
    checks: dict[str, Check] = field(default_factory=lambda: {k: Check() for k in KINDS})
    reasons: list[str] = field(default_factory=list)  # ganzer Abschnitt nicht pruefbar
    source_system_id: int | None = None

    @property
    def label(self) -> str:
        def side(x: dict) -> str:
            return f"{x['label']} {x['interface']}" if x.get("interface") else x["label"]

        return f"{side(self.a)} → {side(self.b)}"

    @property
    def state(self) -> str:
        if self.kind in NOT_L2:
            return self.kind
        if self.reasons:
            return UNCHECKED
        states = {c.state for c in self.checks.values()}
        if DEVIATION in states:
            return DEVIATION
        if states == {UNCHECKED}:
            return UNCHECKED
        return PARTIAL if states & {PARTIAL, UNCHECKED} else OK

    def unchecked(self) -> list[str]:
        """Alle Gruende (Abschnitt und Pruefungen)."""
        out = list(self.reasons)
        for c in self.checks.values():
            out += [r for r in c.reasons if r not in out]
        return out

    def skip(self, reason: str) -> "Section":
        self.reasons.append(reason)
        for c in self.checks.values():
            c.state = UNCHECKED
        return self

    def out(self) -> dict:
        return {"label": self.label, "kind": self.kind, "a": self.a, "b": self.b,
                "vlan": self.vlan, "vlan_from": self.vlan_from, "segment": self.segment,
                "state": self.state, "checks": {k: c.out() for k, c in self.checks.items()},
                "reasons": [{"key": r, "text": reason_text(r)} for r in self.reasons],
                "unchecked": self.unchecked()}  # fmt: skip


def _side(label: str, devices: list[str], interface: str | None, key: str | None = None) -> dict:
    return {"label": label, "devices": list(devices), "interface": interface, "key": key}


class Comparer:
    """Verkabelung, Spanning Tree und Kabel der NetBox eines Mandanten - einmal je Anfrage bzw.
    Durchgang des Abgleichs."""

    def __init__(self, session: Session, tenant_id: int, cab: cabling.Cabling | None = None):
        self.session, self.tenant_id = session, tenant_id
        self.topo = l2_topo.L2(session, tenant_id, cab)
        self.cab = self.topo.cab
        self._stp: dict | None = None
        self._netbox: dict | None = None

    # -- Daten ------------------------------------------------------------------------------------

    def _stp_index(self) -> dict:
        if self._stp is not None:
            return self._stp
        read: set[str] = set()
        errdisabled: dict[tuple[str, str], dict] = {}
        for d in self.session.scalars(select(StpDevice).where(
                StpDevice.tenant_id == self.tenant_id)):  # fmt: skip
            dk = device_key(d.device)
            if d.status == "read":
                read.add(dk)
            for e in d.errdisabled or []:
                if e.get("port"):
                    errdisabled[(dk, port_key(e["port"]))] = e
        per_vlan: dict[tuple[str, int], dict[str, dict]] = {}
        per_mst: dict[str, list[tuple[set[int], dict[str, dict]]]] = defaultdict(list)
        for inst in self.session.scalars(select(StpInstance).where(
                StpInstance.tenant_id == self.tenant_id)):  # fmt: skip
            ports = {port_key(p["port"]): p for p in inst.ports or [] if p.get("port")}
            dk = device_key(inst.device)
            if inst.mst is not None:
                per_mst[dk].append((vlan_set(inst.vlans) or set(), ports))
            elif inst.vlan is not None:
                per_vlan[(dk, inst.vlan)] = ports
        self._stp = {"read": read, "errdisabled": errdisabled, "vlan": per_vlan, "mst": per_mst}
        return self._stp

    def _netbox_index(self) -> dict:
        """Kabel der NetBox je Ende und ihr Abgleich mit CDP/LLDP (`cable_labels`)."""
        if self._netbox is not None:
            return self._netbox
        index = cable_labels.Index(self.session, self.tenant_id)
        seen: dict[str, list[End]] = defaultdict(list)
        for link in self.cab.links.values():
            for end, other in ((link.a, link.b), (link.b, link.a)):
                if end.port:
                    seen[cable_labels.end_key(end.device.name, end.port)].append(other)
        state: dict[str, tuple[str, list[dict], cable_labels.NbCable]] = {}
        for cable in index.cables:
            found, wrong = cable_labels.neighbor_state(cable, seen)
            for end in (cable.a, cable.b):
                state[cable_labels.end_key(*end)] = (found, wrong, cable)
        self._netbox = {"state": state, "any": bool(index.cables)}
        return self._netbox

    # -- Spanning Tree ----------------------------------------------------------------------------

    def _port_keys(self, end: End) -> list[str]:
        names = [end.port] + ([end.iface.lag] if end.iface is not None and end.iface.lag else [])
        return [port_key(n) for n in names if n]

    def blocked(self, end: End, vlan: int) -> dict | None:
        """Blockiert Spanning Tree den Port im VLAN? -> Grund (Rolle, Zustand) bzw. None."""
        if not end.device.switching:
            return None
        idx = self._stp_index()
        dk = end.device.key
        keys = self._port_keys(end)
        for k in keys:
            e = idx["errdisabled"].get((dk, k))
            if e is not None and (not e.get("vlans") or vlan in (vlan_set(e["vlans"]) or set())):
                return {"state": "err-disabled", "reason": e.get("reason")}
        ports = idx["vlan"].get((dk, vlan))
        if ports is None:
            ports = next((p for vs, p in idx["mst"].get(dk, []) if vlan in vs), None)
        for k in keys:
            p = (ports or {}).get(k)
            if p is not None and p.get("state") in BLOCKED_STATES:
                return {"state": p.get("state"), "role": p.get("role")}
        if vlan in self.topo.stp_blocked(end):  # blockierte VLANs am Port (0.65.0)
            return {"state": "blocking"}
        return None

    def _link_blocked(self, link: Link, vlan: int) -> list[tuple[End, dict]]:
        return [(e, why) for e in (link.a, link.b) if (why := self.blocked(e, vlan)) is not None]

    def stp_read(self, end: End) -> bool:
        return end.device.key in self._stp_index()["read"]

    # -- Weg ueber die Kabel ----------------------------------------------------------------------

    def _starts(self, device: str, ports: set[str]) -> list[tuple[Link, End, End]]:
        key = device_key(device)
        out = []
        for links in self.topo.adj.get(key, {}).values():
            for lk in links:
                mine = lk.a if lk.a.device.key == key else lk.b
                other = lk.b if mine is lk.a else lk.a
                if not ports or port_key(mine.port) in ports:
                    out.append((lk, mine, other))
        return out

    def _origin(self, devices: list[str], interface: str | None
                ) -> tuple[list[tuple[Link, End, End]], int | None, str | None]:  # fmt: skip
        """Kabel am Ausgang und das VLAN: VLAN-Subinterface (Parent, Aggregat) bzw. SVI eines
        L3-Switches (alle Kabel des Geraets)."""
        starts: list[tuple[Link, End, End]] = []
        vlan, how = None, None
        for dev in devices:
            key = device_key(dev)
            row = self.cab.ifaces.get((key, port_key(interface))) if interface else None
            svi = _SVI.match((interface or "").strip())
            if row is not None and row.kind == "vlan" and not row.parent and svi:
                found, v, h = self._starts(dev, set()), int(svi.group(1)), "svi"
            else:
                ports, v = self.topo.physical(dev, interface)
                found, h = self._starts(dev, ports), "subinterface" if v is not None else None
            starts += found
            if vlan is None and v is not None:
                vlan, how = v, h
        return starts, vlan, how

    @staticmethod
    def _port_vlan(end: End | None) -> tuple[int | None, str | None]:
        """VLAN, das ein Switch-Port einem Ausgang ohne eigenes VLAN gibt: Access-VLAN bzw.
        Native VLAN des Trunks."""
        i = end.iface if end is not None else None
        if i is None or not end.device.switching:
            return None, None
        if i.mode == "access" and i.untagged_vlan:
            return i.untagged_vlan, "access"
        if i.mode == "tagged":
            v = i.native_vlan if i.native_vlan is not None else i.untagged_vlan
            return (v, "native") if v else (None, None)
        return None, None

    def _bfs(self, starts: list[tuple[Link, End, End]], goal, usable
             ) -> list[tuple[Link, End, End]] | None:  # fmt: skip
        """Kuerzester Weg ueber Kabel mit `usable(link)` bis zu einem Geraet mit `goal(key)` -
        nur durch weiterleitende Geraete (wie `l2_topo.L2.route`)."""
        best: dict[str, tuple] = {}
        queue: deque = deque()
        for lk, mine, other in starts:
            if not usable(lk) or other.device.key in best:
                continue
            best[other.device.key] = (None, lk, mine, other)
            queue.append((other.device.key, 1))
        found = None
        while queue:
            cur, depth = queue.popleft()
            if goal(cur):
                found = cur
                break
            if depth >= MAX_HOPS or not self.topo.switching(cur):
                continue
            for nb, links in self.topo.adj.get(cur, {}).items():
                if nb in best:
                    continue
                lk = next((x for x in links if usable(x)), None)
                if lk is None:
                    continue
                mine = lk.a if lk.a.device.key == cur else lk.b
                best[nb] = (cur, lk, mine, lk.b if mine is lk.a else lk.a)
                queue.append((nb, depth + 1))
        if found is None:
            return None
        chain = []
        cur = found
        while cur is not None:
            prev, lk, mine, other = best[cur]
            chain.append((lk, mine, other))
            cur = prev
        chain.reverse()
        return chain

    def _end_port(self, chain: list[tuple[Link, End, End]], want: set[str], usable) -> None:
        """Am Ziel den Port des Eingangs nehmen, wenn ein anderes Kabel zwischen denselben
        Geraeten dort ankommt (wie `l2_topo.L2.between`)."""
        if not want or not chain:
            return
        lk, mine, other = chain[-1]
        if port_key(other.port) in want:
            return
        for alt in self.topo.adj.get(mine.device.key, {}).get(other.device.key, []):
            a_mine = alt.a if alt.a.device.key == mine.device.key else alt.b
            a_other = alt.b if a_mine is alt.a else alt.a
            if port_key(a_other.port) in want and usable(alt):
                chain[-1] = (alt, a_mine, a_other)
                return

    @staticmethod
    def _segment(chain: list[tuple[Link, End, End]], vlan: int | None,
                 last_out: str | None = None) -> dict:  # fmt: skip
        steps = [l2_topo.Step(chain[0][1].device, out=_label(chain[0][1]))]
        for i, (_lk, _mine, other) in enumerate(chain):
            step = l2_topo.Step(other.device, into=_label(other))
            if i + 1 < len(chain):
                step.out = _label(chain[i + 1][1])
            steps.append(step)
        if last_out:
            steps[-1].out = last_out
        return l2_topo.Segment(steps, vlan).out()

    # -- Abschnitt --------------------------------------------------------------------------------

    def section(self, a: dict, b: dict, b_port: str | None = None,
                b_vlan: int | None = None) -> Section:  # fmt: skip
        """Abschnitt vom Ausgang `a` (Geraete, Interface) bis zum Eingang `b`; mit `b_port`
        bis zu diesem Port des Geraets `b` (Anschluss eines Endpunkts, VLAN laut MAC-Tabelle)."""
        sec = Section(a, b, kind="attach" if b_port else "l2")
        if not a.get("interface") or (not b_port and not b.get("interface")):
            return sec.skip("no_interface")
        starts, vlan, how = self._origin(a["devices"], a["interface"])
        if not starts:
            return sec.skip("no_cabling")
        targets = {device_key(d) for d in b["devices"]}
        want: set[str] = set()
        to_vlan = b_vlan
        if b_port:
            want = {port_key(b_port)}
        else:
            for d in b["devices"]:
                ports, v = self.topo.physical(d, b["interface"])
                want |= ports
                if to_vlan is None and v is not None:
                    to_vlan = v

        def goal(key: str) -> bool:
            return key in targets

        phys = self._bfs(starts, goal, lambda lk: True)
        if phys is None:
            return sec.skip("no_path")
        if vlan is None:
            vlan, how = self._port_vlan(phys[0][2])
        if vlan is None and to_vlan is not None:
            vlan, how = to_vlan, "target"
        sec.vlan, sec.vlan_from = vlan, how
        chk = sec.checks
        if vlan is None:
            chain = phys
            chk["vlan"].state = chk["stp"].state = UNCHECKED
            chk["vlan"].reasons.append("vlan_unknown")
            chk["stp"].reasons.append("vlan_unknown")
        else:

            def allowed(lk: Link) -> bool:
                return l2_topo.link_allows(lk, vlan) is not False

            def open_(lk: Link) -> bool:
                return allowed(lk) and not self._link_blocked(lk, vlan)

            chain = self._bfs(starts, goal, open_)
            usable = open_
            if chain is None:
                chain = self._bfs(starts, goal, allowed)
                usable = allowed
            if chain is None:
                chain, usable = phys, (lambda lk: True)
            self._end_port(chain, want, usable)
            self._vlan(sec, chain, vlan, to_vlan, b_port)
            self._stp(sec, chain, vlan)
        if vlan is None:
            self._end_port(chain, want, lambda lk: True)
        self._cables(sec, chain)
        sec.segment = self._segment(chain, vlan, b_port)
        return sec

    def _vlan(self, sec: Section, chain, vlan: int, to_vlan: int | None,
              b_port: str | None) -> None:  # fmt: skip
        c = sec.checks["vlan"]
        if to_vlan is not None and to_vlan != vlan:
            c.state = DEVIATION
            c.items.append(
                {
                    "kind": "other_vlan",
                    "vlan": to_vlan,
                    "device": sec.b["label"],
                    "port": b_port or sec.b.get("interface"),
                }
            )
        for lk, _mine, _other in chain:
            c.count += 1
            ok = l2_topo.link_allows(lk, vlan)
            if ok is None:
                c.miss("vlan_ports")
                continue
            if ok is False:
                c.state = DEVIATION
                ports = [_port_out(e) for e in (lk.a, lk.b)
                         if l2_topo.allows(e.iface, vlan) is False]  # fmt: skip
                c.items.append({"kind": "not_allowed", "link": lk.id, "ports": ports})
        if b_port and chain:  # Port des Endpunkts selbst
            end = chain[-1][2]
            port = self.cab.ifaces.get((end.device.key, port_key(b_port)))
            if port is not None and l2_topo.allows(port, vlan) is False:
                c.state = DEVIATION
                c.items.append(
                    {
                        "kind": "not_allowed",
                        "link": None,
                        "ports": [_port_out(End(end.device, port.name, port))],
                    }
                )

    def _stp(self, sec: Section, chain, vlan: int) -> None:
        c = sec.checks["stp"]
        unread: set[str] = set()
        for lk, _mine, _other in chain:
            for end, why in self._link_blocked(lk, vlan):
                c.state = DEVIATION
                c.items.append({"kind": "blocked", "link": lk.id, **_port_out(end, **why)})
            for end in (lk.a, lk.b):
                if not end.device.switching:
                    continue
                c.count += 1
                if not self.stp_read(end):
                    unread.add(end.device.name)
        if unread:
            c.miss("stp_unread")
            c.items.append({"kind": "unread", "devices": sorted(unread)})

    def _cables(self, sec: Section, chain) -> None:
        c = sec.checks["cable"]
        nb = self._netbox_index()
        for lk, _mine, _other in chain:
            c.count += 1
            for issue in lk.issues:  # Verkabelung: Speed, Duplex, VLANs, Port-Channel
                c.state = DEVIATION
                c.items.append({"kind": issue["type"], "link": lk.id, "text": issue["text"],
                                "a": _port_out(lk.a), "b": _port_out(lk.b)})  # fmt: skip
            half = [
                e.device.name
                for e in (lk.a, lk.b)
                if e.iface is not None and cabling.duplex_of(e.iface.duplex) == "half"
            ]
            if half and not any(i["type"] == "link_duplex_mismatch" for i in lk.issues):
                c.state = DEVIATION
                c.items.append({"kind": "link_half_duplex", "link": lk.id, "devices": half,
                                "a": _port_out(lk.a), "b": _port_out(lk.b)})  # fmt: skip
            hits = [nb["state"].get(cable_labels.end_key(e.device.name, e.port))
                    for e in (lk.a, lk.b) if e.port]  # fmt: skip
            hits = [h for h in hits if h is not None]
            if not hits:
                c.miss("no_netbox")
                continue
            wrong = next((h for h in hits if h[0] == cable_labels.NEIGHBOR_MISMATCH), None)
            if wrong is not None:
                c.state = DEVIATION
                cable = wrong[2]
                c.items.append({"kind": "netbox", "link": lk.id, "cable": cable.label or cable.id,
                                "url": cable.url, "netbox": cable.source, "wrong": wrong[1],
                                "a": _port_out(lk.a), "b": _port_out(lk.b)})  # fmt: skip

    # -- Weg der Weg-Suche ------------------------------------------------------------------------

    def trace(self, result: dict) -> dict:
        """Vergleich eines Ergebnisses der Weg-Suche (`paths.trace`, mit `l2_topo.annotate`):
        je Weg die Abschnitte zwischen den Hops und zum Anschluss von Quelle bzw. Ziel."""
        loc = result.get("l2") or {}
        out = []
        for p in result.get("paths") or []:
            hops = p.get("hops") or []
            secs: list[Section] = []
            if hops and loc.get("src"):
                first, at = hops[0], loc["src"][0]
                a = _side(
                    first["label"],
                    first.get("members") or [],
                    (first.get("ingress") or {}).get("interface"),
                    first.get("key"),
                )
                b = _side(at["device"], [at["device"]], at["port"])
                secs.append(self.section(a, b, at["port"], at.get("vlan")))
            for h, nxt in zip(hops, hops[1:], strict=False):
                secs.append(self.hop_pair(h, nxt))
            if hops and loc.get("dst") and (p.get("end") or {}).get("kind") == "connected":
                last, at = hops[-1], loc["dst"][0]
                a = _side(
                    last["label"],
                    last.get("members") or [],
                    (last.get("egress") or {}).get("interface"),
                    last.get("key"),
                )
                b = _side(at["device"], [at["device"]], at["port"])
                secs.append(self.section(a, b, at["port"], at.get("vlan")))
            out.append({"kind": p.get("kind") or "active",
                        "hops": [h.get("label") for h in hops],
                        "end": p.get("end"), "sections": [s.out() for s in secs],
                        "summary": summary(secs)})  # fmt: skip
        everything = [s for p in out for s in p["sections"]]
        return {"paths": out, "summary": summary_out(everything)}

    def hop_pair(self, h: dict, nxt: dict) -> Section:
        egress, ingress = h.get("egress") or {}, nxt.get("ingress") or {}
        a = _side(h["label"], h.get("members") or [], egress.get("interface"), h.get("key"))
        b = _side(nxt["label"], nxt.get("members") or [], ingress.get("interface"), nxt.get("key"))
        sec = Section(a, b, source_system_id=h.get("source_system_id"))
        if egress.get("vpn"):
            sec.kind = "vpn"
            return sec
        if h.get("source_system_id") is not None and \
                h.get("source_system_id") == nxt.get("source_system_id"):  # fmt: skip
            sec.kind = "internal"  # Inter-VDOM-Link bzw. VRF-Leaking im selben Geraet
            return sec
        if h.get("kind") == "vrf" and not egress.get("interface"):
            return sec.skip("fabric")
        out = self.section(a, b)
        out.source_system_id = sec.source_system_id
        return out


def summary(secs: list[Section]) -> dict:
    return summary_out([s.out() for s in secs])


def summary_out(secs: list[dict]) -> dict:
    """Zaehler: Abschnitte je Zustand, Abweichungen je Pruefung, Gruende (nicht geprueft)."""
    states = Counter(s["state"] for s in secs)
    devs = Counter(k for s in secs for k, c in s["checks"].items() if c["state"] == DEVIATION)
    reasons = Counter(r for s in secs for r in s["unchecked"])
    return {"sections": len(secs), "states": dict(states),
            "deviations": {k: devs.get(k, 0) for k in KINDS},
            "unchecked": [{"key": k, "text": reason_text(k), "count": n}
                          for k, n in sorted(reasons.items())]}  # fmt: skip


# -- alle L3-Abschnitte eines Mandanten ------------------------------------------------------------


@dataclass
class Sections:
    items: list[Section]
    not_l2: Counter  # vpn | internal -> Anzahl

    def out(self) -> dict:
        data = [s.out() for s in self.items]
        return {"items": data, "summary": summary_out(data), "not_l2": dict(self.not_l2)}


def sections(session: Session, tenant_id: int, graph=None,
             comparer: Comparer | None = None) -> Sections:  # fmt: skip
    """Alle L3-Abschnitte des Mandanten: je Paar (Knoten, Interface) und (Knoten des Next-Hops,
    sein Interface) einmal - aus den Next-Hops der Routing-Tabellen, deren Adresse einem anderen
    gelesenen Geraet gehoert (`paths.Graph.owners`)."""
    from app.store import paths

    graph = graph or paths.Graph(session, tenant_id)
    owners = graph.owners()
    pairs: dict[tuple, tuple] = {}
    not_l2: Counter = Counter()
    seen_not_l2: set[tuple] = set()
    rows = session.execute(
        select(TopoRoute.source_system_id, TopoRoute.context, cast(TopoRoute.next_hops, Text))
        .where(TopoRoute.tenant_id == tenant_id, TopoRoute.table == "rib")
        .distinct())  # fmt: skip
    for sid, context, text in rows:
        key = (sid, context)
        if key not in graph.nodes or not text:
            continue
        try:
            hops = json.loads(text) or []
        except ValueError:
            continue
        for hop in hops if isinstance(hops, list) else []:
            ip, iface = (hop or {}).get("ip"), (hop or {}).get("interface")
            if not ip:
                continue
            for nkey, niface, _zone in owners.get(str(ip), []):
                if nkey == key or nkey not in graph.nodes:
                    continue
                ends = tuple(sorted(((key, iface or ""), (nkey, niface or ""))))
                if nkey[0] == sid or graph.tunnel(key, iface) is not None:
                    if ends not in seen_not_l2:
                        seen_not_l2.add(ends)
                        not_l2["internal" if nkey[0] == sid else "vpn"] += 1
                    continue
                pairs.setdefault(ends, ends)
    comparer = comparer or Comparer(session, tenant_id)
    items = []
    for (ka, ia), (kb, ib) in sorted(
        pairs,
        key=lambda e: (graph.nodes[e[0][0]].label, e[0][1], graph.nodes[e[1][0]].label, e[1][1]),
    ):
        na, nb = graph.nodes[ka], graph.nodes[kb]
        a = _side(na.label, na.members, ia or None, na.key)
        b = _side(nb.label, nb.members, ib or None, nb.key)
        if na.kind == "vrf" and not ia:
            sec = Section(a, b).skip("fabric")
        else:
            sec = comparer.section(a, b)
        sec.source_system_id = ka[0]
        items.append(sec)
    return Sections(items, not_l2)


def of_check(session: Session, tenant_id: int, cx=None) -> Sections:
    """Einmal je Durchgang des Abgleichs (alle Pruefungen von „Wege vergleichen“ teilen es)."""
    if cx is None:
        return sections(session, tenant_id)
    found = cx.extra.get("path_compare")
    if found is None or found[0] != tenant_id:
        graph = cx.extra.get("graph")
        found = cx.extra["path_compare"] = (tenant_id, sections(session, tenant_id, graph))
    return found[1]


# -- Texte der Befunde ----------------------------------------------------------------------------


def _ports_text(items: list[dict]) -> str:
    out = []
    for it in items:
        for p in it.get("ports") or ([it] if it.get("device") and it.get("port") else []):
            out.append(f"{p['device']} {p.get('port') or ''}".strip())
    return ", ".join(dict.fromkeys(out)) or "?"


def explain(ftype: str, subject: str, d: dict) -> tuple[str, str]:
    """(Befund, Hinweis) fuer die Befunde von „Wege vergleichen“ - in der Sprache der Anfrage."""
    where = d.get("section") or subject
    vlan = d.get("vlan")
    if ftype == "path_vlan_missing":
        other = next((i for i in d.get("items") or [] if i.get("kind") == "other_vlan"), None)
        ports = _ports_text([i for i in d.get("items") or [] if i.get("kind") == "not_allowed"])
        if other is not None and ports == "?":
            return (
                L(f"{where}: Ausgang in VLAN {vlan}, Eingang in VLAN {other['vlan']}.",
                  f"{where}: egress in VLAN {vlan}, ingress in VLAN {other['vlan']}."),
                L("Beide Enden eines L3-Abschnitts brauchen dasselbe VLAN – Subinterfaces bzw. "
                  "Access-Ports angleichen.",
                  "Both ends of an L3 section need the same VLAN – align the subinterfaces or "
                  "access ports."),
            )  # fmt: skip
        return (
            L(f"{where}: VLAN {vlan} ist auf dem Weg nicht überall erlaubt – gesperrt an {ports}.",
              f"{where}: VLAN {vlan} is not allowed everywhere on the path – blocked at "
              f"{ports}."),
            L(f"VLAN {vlan} auf den Trunks zulassen (`switchport trunk allowed vlan add {vlan}`) "
              "bzw. das Access-VLAN der Ports prüfen – sonst erreicht der Verkehr den nächsten "
              "Hop nicht, obwohl das Routing stimmt.",
              f"Allow VLAN {vlan} on the trunks (`switchport trunk allowed vlan add {vlan}`) or "
              "check the access VLAN of the ports – otherwise traffic does not reach the next "
              "hop although routing is correct."),
        )  # fmt: skip
    if ftype == "path_stp_blocked":
        ports = _ports_text([i for i in d.get("items") or [] if i.get("kind") == "blocked"])
        return (
            L(f"{where}: Spanning Tree blockiert VLAN {vlan} auf jedem Weg – an {ports}.",
              f"{where}: Spanning Tree blocks VLAN {vlan} on every path – at {ports}."),
            L("Kein offener Weg im VLAN: Root und Kosten im Spanning Tree prüfen bzw. warum der "
              "Port blockiert (Schutzfunktion, err-disabled) – unter L2 · Spanning Tree.",
              "No open path in the VLAN: check root and costs in the Spanning Tree or why the "
              "port blocks (protection feature, err-disabled) – under L2 · Spanning Tree."),
        )  # fmt: skip
    if ftype == "path_cable_mismatch":
        kinds = []
        for it in d.get("items") or []:
            if it.get("kind") == "netbox":
                kinds.append(
                    L(
                        f"Kabel {it.get('cable')} in NetBox passt nicht zu CDP/LLDP",
                        f"cable {it.get('cable')} in NetBox does not match CDP/LLDP",
                    )
                )
            elif it.get("text"):
                kinds.append(str(it["text"]))
            elif it.get("kind") == "link_half_duplex":
                kinds.append(L("Duplex nicht voll", "duplex not full"))
        text = "; ".join(dict.fromkeys(kinds)) or "?"
        return (
            L(f"{where}: Kabel auf dem Weg weichen ab – {text}.",
              f"{where}: cables on the path deviate – {text}."),
            L("Die Befunde der Verkabelung zu diesen Kabeln zuerst beheben (L1 · Verkabelung bzw. "
              "Kabelnummern); der Weg nutzt sie.",
              "Fix the cabling findings of these cables first (L1 · Cabling or cable numbers); "
              "the path uses them."),
        )  # fmt: skip
    if ftype == "path_l2_unchecked":
        examples = ", ".join(d.get("sections") or [])
        return (
            L(f"Wege vergleichen: {d.get('count')} L3-Abschnitte nicht (ganz) geprüft – "
              f"{reason_text(d.get('reason') or '')}.",
              f"Compare paths: {d.get('count')} L3 sections not (fully) checked – "
              f"{reason_text(d.get('reason') or '')}."),
            L(f"Beispiele: {examples}. Fehlende Switches bzw. CDP/LLDP einlesen, Kabel in NetBox "
              "pflegen bzw. Spanning Tree lesen – dann prüft der Vergleich auch diese Teile.",
              f"Examples: {examples}. Collect missing switches or CDP/LLDP, maintain cables in "
              "NetBox or read the Spanning Tree – the comparison then also checks these parts."),
        )  # fmt: skip
    return subject, ""
