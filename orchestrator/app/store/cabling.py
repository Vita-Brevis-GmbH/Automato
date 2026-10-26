"""Verkabelung (0.62.0, ADR 0025): Verbindungen zwischen Geraeten aus CDP/LLDP aller Quellen.

Jede Angabe „an meinem Port P sitzt Geraet G, Port Q“ (Switches `inv_neighbor`, ACI-Leafs und
andere ueber `inv_interface.neighbor`) wird zu einer Verbindung zwischen zwei Enden. Ein Ende ist
ein gelesenes Geraet (`inv_device`, Name bzw. Management-IP), ein ESXi-Host aus dem vCenter oder
ein Geraet, das Automato nicht liest. Meldet die Gegenseite die Verbindung ebenfalls, ist sie
**beidseitig**; liest Automato die Gegenseite, ohne dass sie sie meldet, ist sie **einseitig**.

Geprueft wird nur zwischen zwei gelesenen Switches (deren Ports Status, Speed, Duplex und VLANs
tragen): Speed, Duplex, Modus/VLANs (Access-VLAN, Native VLAN, erlaubte VLANs) und Port-Channel
nur auf einer Seite. Daraus werden Befunde (`checks`). Rein lesend; nur Daten der Plattform.
"""

import ipaddress
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvDevice, InvInterface, InvNeighbor, SourceSystem
from app.models.source_system import Platform

# Langform -> Kurzform (klein) fuer den Vergleich von Portnamen ueber Hersteller und Protokolle
_PORT_PREFIX = (
    ("hundredgigabitethernet", "hu"),
    ("hundredgige", "hu"),
    ("fortygigabitethernet", "fo"),
    ("twentyfivegigabitethernet", "twe"),
    ("twentyfivegige", "twe"),
    ("tengigabitethernet", "te"),
    ("fivegigabitethernet", "fi"),
    ("twogigabitethernet", "tw"),
    ("gigabitethernet", "gi"),
    ("fastethernet", "fa"),
    ("ethernet", "eth"),
    ("port-channel", "po"),
)


def port_key(name: str | None) -> str:
    """`GigabitEthernet1/0/47`, `Gi1/0/47` -> `gi1/0/47`; `Ethernet1/1`, `eth1/1` -> `eth1/1`."""
    text = re.sub(r"\s+", "", (name or "").lower())
    for long, short in _PORT_PREFIX:
        if text.startswith(long):
            return short + text[len(long) :]
    return text


def device_key(name: str | None) -> str:
    """Geraetename zum Vergleich: klein, ohne Seriennummer in Klammern und ohne Domaene
    (`sw-core-01(FDO123)`, `sw-acc-01.example.ch` -> `sw-core-01`, `sw-acc-01`)."""
    text = re.sub(r"\(.*?\)\s*$", "", (name or "").strip()).lower()
    try:
        ipaddress.ip_address(text)
        return text
    except ValueError:
        return text.split(".", 1)[0]


def speed_mbps(value: str | None) -> int | None:
    """`a-1000`, `1000`, `10G`, `100G`, `a-10G` -> Mbit/s; `auto`/leer -> None."""
    text = (value or "").lower().removeprefix("a-").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([gm]?)(?:b(?:it|ps)?/?s?)?", text)
    if not m:
        return None
    number = float(m.group(1))
    return int(number * 1000) if m.group(2) == "g" else int(number)


def duplex_of(value: str | None) -> str | None:
    text = (value or "").lower().removeprefix("a-").strip()
    return text if text in ("full", "half") else None


def vlan_set(text: str | None) -> set[int] | None:
    """`10,20,30-40` -> Menge; None = unbekannt."""
    out: set[int] = set()
    for part in (text or "").replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        if not lo.isdigit() or (hi and not hi.isdigit()):
            return None
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def _ranges(vlans: set[int], limit: int = 12) -> str:
    ordered, out, i = sorted(vlans), [], 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        out.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ",".join(out[:limit]) + (" …" if len(out) > limit else "")


@dataclass
class Device:
    key: str
    name: str
    kind: str  # switch | leaf | spine | firewall | controller | router | esxi | unknown
    source_system_id: int | None = None
    source: str | None = None
    platform: str | None = None
    model: str | None = None
    version: str | None = None
    mgmt_ip: str | None = None
    read: bool = False  # Automato liest das Geraet (Ports bekannt)
    reports: bool = False  # liefert Nachbarn aus CDP/LLDP (Switches, ACI-Leafs/-Spines)
    # wie der Nachbar sich meldet (CDP/LLDP): Plattform und Faehigkeiten (Switch, Router, Phone)
    remote_platform: str | None = None
    caps: set[str] = field(default_factory=set)

    @property
    def switching(self) -> bool:
        """Leitet weiter (Switch, Leaf, Spine bzw. meldet Switch/Bridge/Router) - an seinem Port
        stehen fremde MACs (0.63.0). ESXi-Hosts, Firewalls und Telefone nicht."""
        if self.kind in ("switch", "leaf", "spine"):
            return True
        if self.kind in ("esxi", "firewall", "controller", "router"):  # Router (MX) seit 0.68.0
            return False
        if re.search(r"vmware|esx", self.remote_platform or "", re.I):
            return False
        if self.caps & {"phone", "telephone", "t"}:
            return False
        return bool(self.caps & {"switch", "bridge", "router", "b", "r", "trans-bridge",
                                 "source-route-bridge"})  # fmt: skip


@dataclass
class End:
    device: Device
    port: str | None
    iface: InvInterface | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.device.key, port_key(self.port) if self.port else "?")

    def out(self) -> dict:
        i = self.iface
        return {
            "device": self.device.name,
            "device_key": self.device.key,
            "kind": self.device.kind,
            "read": self.device.read,
            "source": self.device.source,
            "source_system_id": self.device.source_system_id,
            "port": self.port,
            "status": i.status if i is not None else None,
            "speed": i.speed if i is not None else None,
            "duplex": i.duplex if i is not None else None,
            "mode": i.mode if i is not None else None,
            "vlan": i.untagged_vlan if i is not None else None,
            "native_vlan": i.native_vlan if i is not None else None,
            "trunk_vlans": i.trunk_vlans if i is not None else None,
            "lag": i.lag if i is not None else None,
            "vpc": (i.attrs or {}).get("vpc") if i is not None else None,
            "description": i.description if i is not None else None,
        }


@dataclass
class Link:
    id: str
    a: End
    b: End
    protocols: set[str] = field(default_factory=set)
    reported_by: set[str] = field(default_factory=set)  # device_key der meldenden Seiten
    issues: list[dict] = field(default_factory=list)

    @property
    def state(self) -> str:
        """beidseitig (`both`); die Gegenstelle liefert sonst Nachbarn, meldet diese Verbindung
        aber nicht (`one_sided`); die Gegenstelle liefert keine Nachbar-Daten - nicht gelesen,
        Firewall, Server (`unread`)."""
        if {self.a.device.key, self.b.device.key} <= self.reported_by:
            return "both"
        silent = self.b if self.a.device.key in self.reported_by else self.a
        return "one_sided" if silent.device.reports else "unread"

    def out(self) -> dict:
        return {
            "id": self.id,
            "a": self.a.out(),
            "b": self.b.out(),
            "protocols": sorted(self.protocols),
            "state": self.state,
            "issues": self.issues,
        }


_NEIGHBOR_PLATFORMS = (Platform.CISCO_SWITCH, Platform.CISCO_ACI)


class Cabling:
    """Geraete und Verbindungen eines Mandanten."""

    def __init__(self, session: Session, tenant_id: int):
        self.devices: dict[str, Device] = {}
        self.by_ip: dict[str, Device] = {}
        sources = {s.id: s for s in session.scalars(
            select(SourceSystem).where(SourceSystem.tenant_id == tenant_id))}  # fmt: skip
        for d in session.scalars(select(InvDevice).where(InvDevice.tenant_id == tenant_id)):
            src = sources.get(d.source_system_id)
            dev = Device(
                key=device_key(d.name), name=d.name, kind=d.role or "unknown",
                source_system_id=d.source_system_id, source=src.name if src else None,
                platform=src.platform.value if src else None, model=d.model, version=d.version,
                mgmt_ip=d.mgmt_ip, read=True,
                reports=src is not None and src.platform in _NEIGHBOR_PLATFORMS,
            )  # fmt: skip
            self.devices.setdefault(dev.key, dev)
            if d.mgmt_ip:
                self.by_ip.setdefault(d.mgmt_ip, self.devices[dev.key])
        self._esxi(session, tenant_id)
        self.ifaces: dict[tuple[str, str], InvInterface] = {}
        self.ports: list[InvInterface] = []
        for i in session.scalars(select(InvInterface).where(InvInterface.tenant_id == tenant_id)):
            self.ports.append(i)
            self.ifaces.setdefault((device_key(i.device), port_key(i.name)), i)
        self.switch_sources = {sid for sid, s in sources.items()
                               if s.platform is Platform.CISCO_SWITCH}  # fmt: skip
        self.links: dict[str, Link] = {}
        claimed: set[tuple[str, str]] = set()
        for n in session.scalars(select(InvNeighbor).where(InvNeighbor.tenant_id == tenant_id)):
            claimed.add((device_key(n.device), port_key(n.port)))
            self._claim(n.device, n.port, n.remote_name, n.remote_port, n.remote_ip, n.protocol)
            remote = self.device(n.remote_name, n.remote_ip)
            if not remote.read:  # Plattform/Faehigkeiten nur fuer Geraete ohne eigene Daten
                remote.remote_platform = remote.remote_platform or n.remote_platform
                remote.caps |= {c.strip(",").lower() for c in n.remote_caps or [] if c}
        for i in self.ports:  # ACI-Leafs u. a.: Nachbar am Port (ohne eigene Nachbar-Tabelle)
            if i.neighbor and (device_key(i.device), port_key(i.name)) not in claimed:
                self._claim(i.device, i.name, i.neighbor, i.neighbor_port, None, "lldp")
        for link in self.links.values():
            link.issues = self._check(link)

    def _esxi(self, session: Session, tenant_id: int) -> None:
        from app.models import Vm

        hosts = session.scalars(
            select(Vm.host).where(Vm.tenant_id == tenant_id, Vm.host.is_not(None)).distinct()
        )
        for host in hosts:
            key = device_key(host)
            self.devices.setdefault(key, Device(key=key, name=host, kind="esxi", source="vCenter"))

    def device(self, name: str, ip: str | None = None) -> Device:
        key = device_key(name)
        if key in self.devices:
            return self.devices[key]
        if ip and ip in self.by_ip:
            return self.by_ip[ip]
        dev = Device(key=key, name=name, kind="unknown", mgmt_ip=ip)
        self.devices[key] = dev
        return dev

    def _end(self, dev: Device, port: str | None) -> End:
        iface = self.ifaces.get((dev.key, port_key(port))) if port else None
        return End(dev, iface.name if iface is not None else port, iface)

    def _claim(self, local: str, port: str, remote: str, remote_port: str | None,
               remote_ip: str | None, protocol: str) -> None:  # fmt: skip
        a = self._end(self.device(local), port)
        b = self._end(self.device(remote, remote_ip), remote_port)
        if a.device.key == b.device.key:
            return  # eigener Port (z. B. Loopback-Test)
        first, second = sorted((a, b), key=lambda e: e.key)
        lid = f"{first.key[0]}|{first.key[1]}~{second.key[0]}|{second.key[1]}"
        link = self.links.get(lid)
        if link is None:
            link = self.links[lid] = Link(lid, first, second)
        link.protocols.add(protocol or "lldp")
        link.reported_by.add(a.device.key)

    def _check(self, link: Link) -> list[dict]:
        a, b = link.a.iface, link.b.iface
        if a is None or b is None:
            return []
        if (
            a.source_system_id not in self.switch_sources
            or b.source_system_id not in self.switch_sources
        ):
            return []
        issues: list[dict] = []
        sa, sb = speed_mbps(a.speed), speed_mbps(b.speed)
        if sa and sb and sa != sb:
            issues.append({"type": "link_speed_mismatch",
                           "text": f"Speed {a.speed} ↔ {b.speed}"})  # fmt: skip
        da, db = duplex_of(a.duplex), duplex_of(b.duplex)
        if da and db and da != db:
            issues.append({"type": "link_duplex_mismatch",
                           "text": f"Duplex {a.duplex} ↔ {b.duplex}"})  # fmt: skip
        vlan = self._vlans(a, b)
        if vlan:
            issues.append({"type": "link_vlan_mismatch", "text": vlan})
        if bool(a.lag) != bool(b.lag) and a.kind == "physical" and b.kind == "physical":
            side = link.a if a.lag else link.b
            text = f"Port-Channel nur auf {side.device.name} ({side.iface.lag})"
            issues.append({"type": "link_lag_mismatch", "text": text})
        return issues

    @staticmethod
    def _vlans(a: InvInterface, b: InvInterface) -> str | None:
        if not a.mode or not b.mode:
            return None
        if a.mode != b.mode:
            mode = {"access": "Access", "tagged": "Trunk"}
            return f"Modus {mode.get(a.mode, a.mode)} ↔ {mode.get(b.mode, b.mode)}"
        if a.mode == "access":
            if a.untagged_vlan and b.untagged_vlan and a.untagged_vlan != b.untagged_vlan:
                return f"Access-VLAN {a.untagged_vlan} ↔ {b.untagged_vlan}"
            return None
        parts = []
        if a.native_vlan and b.native_vlan and a.native_vlan != b.native_vlan:
            parts.append(f"Native VLAN {a.native_vlan} ↔ {b.native_vlan}")
        va, vb = vlan_set(a.trunk_vlans), vlan_set(b.trunk_vlans)
        if va is not None and vb is not None and va != vb:
            if va - vb:
                parts.append(f"nur auf {a.device}: {_ranges(va - vb)}")
            if vb - va:
                parts.append(f"nur auf {b.device}: {_ranges(vb - va)}")
        return "; ".join(parts) or None


def build(session: Session, tenant_id: int) -> Cabling:
    return Cabling(session, tenant_id)


_FINDINGS = {
    "link_speed_mismatch": "warning",
    "link_duplex_mismatch": "error",
    "link_vlan_mismatch": "warning",
    "link_lag_mismatch": "warning",
}


def checks(session: Session, tenant_id: int, cab: Cabling | None = None) -> list[dict]:
    """Befunde der Verkabelung: Abweichungen zwischen zwei gelesenen Switches und Verbindungen,
    die nur eine von zwei gelesenen Seiten meldet. Je Verbindung und Art ein Eintrag."""
    cab = cab or build(session, tenant_id)
    out: list[dict] = []
    for link in cab.links.values():
        ends = {"a": link.a.out(), "b": link.b.out()}
        base = {"subject": link.id, "link": link.id, **ends,
                "source_system_id": link.a.device.source_system_id
                or link.b.device.source_system_id}  # fmt: skip
        for issue in link.issues:
            out.append({**base, "type": issue["type"], "severity": _FINDINGS[issue["type"]],
                        "text": issue["text"]})  # fmt: skip
        if link.state == "one_sided":
            silent = link.b if link.a.device.key in link.reported_by else link.a
            text = f"{silent.device.name} meldet die Verbindung nicht (CDP/LLDP aus?)"
            out.append({**base, "type": "link_one_sided", "severity": "info", "text": text})
    return out
