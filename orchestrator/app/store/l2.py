"""Endpunkte der Switches (0.63.0, ADR 0025 Nr. 8): MAC-Tabelle und ARP, „Wo hängt …“.

**Sammeln** (`merge`): Je Lauf eines Quellsystems `cisco_switch` werden die gemeldeten MAC- und
ARP-Eintraege der gelesenen Switches nachgefuehrt - bekannte bekommen ein neues `last_seen`,
neue kommen dazu, nichts wird ersetzt. Aktuell ist je Switch, was sein letzter Lauf gesehen hat;
aeltere Eintraege sind „frueher gesehen“ und fallen nach der Frist weg (`store/retention`).

**Anschluss eines Endpunkts** (`Locator`): Eine MAC steht in der Tabelle jedes Switches auf dem
Weg - auf den Uplinks und am Port, an dem das Geraet haengt. Ein Port ist ein **Uplink**, wenn an
ihm (bzw. an einem Mitglied seines Port-Channels) laut CDP/LLDP ein weiterleitendes Geraet haengt
(Switch, Leaf, Spine, Bridge/Router - `cabling.Device.switching`; nicht ESXi, Firewall, Telefon).
Der **Anschluss** ist jeder Port mit der MAC, der kein Uplink ist (mit vPC je Switch des Paars
einer). Steht die MAC nur auf Uplinks, haengt das Geraet hinter einem Geraet ohne MAC-Tabelle in
Automato (ACI, Switch ohne Zugang) - gezeigt wird die Gegenstelle.

**Wer ist das**: VM-NIC aus dem vCenter (MAC), IPs aus ARP, VMware Tools und ACI-Endpunkten,
Namen aus dem IPAM. Seit 0.94.0 auch IPv6-Adressen aus den IPv6-Nachbarn (NDP, in `arp_entry` wie
ARP); Link-local-Adressen (`fe80::/10`) stehen getrennt (`link_local`) und zaehlen nicht als
Identitaet (keine IPAM-Namen, kein Kunden-Ausschnitt) - sie gelten nur am Link. Gesucht werden
kann auch nach ihnen („Wo haengt fe80::…“).

**Befund** `uplink_vlan_missing` (`checks`): Die Ports, an denen VMs eines ESXi-Hosts gesehen
werden, sind seine Uplinks fuer diesen vSwitch/DVS. Jedes VLAN, das VMs des Clusters auf diesem
vSwitch/DVS nutzen, muss dort erlaubt sein - sonst verliert eine VM nach vMotion bzw. beim
Ausfall eines Uplinks das Netz. Nur Ports gelesener Switches mit bekannter VLAN-Konfiguration.

Rein lesend; nichts davon geht an Kunden-Benutzer.
"""

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import ArpEntry, Epg, EpgEndpoint, MacEntry, SourceSystem, Vm, VmIp, VmNic
from app.models.source_system import Platform
from app.store import cabling as cab_store
from app.store import references

_HEX = re.compile(r"[^0-9a-f]")


def mac_key(value: str | None) -> str | None:
    """`0050.5601.0001`, `00-50-56-01-00-01`, `00:50:56:01:00:01` -> `00:50:56:01:00:01`."""
    digits = _HEX.sub("", (value or "").lower())
    if len(digits) != 12:
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def _ip(value: str | None) -> str | None:
    """IP ohne Laenge und ohne IPv6-Zone (`fe80::1%Vlan10` -> `fe80::1`)."""
    try:
        text = (value or "").split("/", 1)[0].split("%", 1)[0].strip()
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def link_local(ip: str | None) -> bool:
    """IPv6-Link-local (`fe80::/10`, 0.94.0) - gilt nur am Link, keine Identitaet."""
    try:
        return ipaddress.ip_address(ip or "").is_link_local and ":" in (ip or "")
    except ValueError:
        return False


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


# -- Sammeln -----------------------------------------------------------------------------------


def merge(session: Session, run, inv, now: datetime | None = None) -> dict[str, int]:
    """MAC- und ARP-Eintraege des Laufs nachfuehren (nur Quellsysteme `cisco_switch`)."""
    src = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
    if src is None or src.platform is not Platform.CISCO_SWITCH or run.tenant_id is None:
        return {"macs": 0, "arps": 0}
    now = now or datetime.now(UTC)
    base = {"tenant_id": run.tenant_id, "source_system_id": src.id}
    known = {
        (m.device, m.vlan, m.mac, m.port): m
        for m in session.scalars(select(MacEntry).where(MacEntry.source_system_id == src.id))
    }
    added = 0
    for e in inv.macs or []:
        mac = mac_key(e.mac)
        if mac is None:
            continue
        row = known.get((e.device, e.vlan, mac, e.port))
        if row is None:
            row = MacEntry(**base, device=e.device, mac=mac, vlan=e.vlan, port=e.port,
                           kind=e.kind, first_seen=now, last_seen=now)  # fmt: skip
            session.add(row)
            known[(e.device, e.vlan, mac, e.port)] = row
            added += 1
        else:
            row.last_seen, row.kind = now, e.kind or row.kind
    arps = {
        (a.device, a.vrf, a.ip, a.mac): a
        for a in session.scalars(select(ArpEntry).where(ArpEntry.source_system_id == src.id))
    }
    for e in inv.arps or []:
        mac, ip = mac_key(e.mac), _ip(e.ip)
        if mac is None or ip is None:
            continue
        row = arps.get((e.device, e.vrf, ip, mac))
        if row is None:
            row = ArpEntry(**base, device=e.device, ip=ip, mac=mac, interface=e.interface,
                           vrf=e.vrf, first_seen=now, last_seen=now)  # fmt: skip
            session.add(row)
            arps[(e.device, e.vrf, ip, mac)] = row
        else:
            row.last_seen, row.interface = now, e.interface or row.interface
    session.flush()
    return {"macs": len(inv.macs or []), "arps": len(inv.arps or []), "new": added}


def _current(model, tenant_id: int, *conds):
    """Eintraege, die der letzte Lauf des jeweiligen Switches gesehen hat."""
    latest = (
        select(model.source_system_id, model.device, func.max(model.last_seen).label("at"))
        .where(model.tenant_id == tenant_id)
        .group_by(model.source_system_id, model.device)
        .subquery()
    )
    return (
        select(model)
        .join(latest, and_(model.source_system_id == latest.c.source_system_id,
                           model.device == latest.c.device, model.last_seen == latest.c.at))
        .where(model.tenant_id == tenant_id, *conds)
    )  # fmt: skip


# -- Anschluss und Identitaet -----------------------------------------------------------------


@dataclass
class Identity:
    """Was hinter einer MAC steckt."""

    ips: list[str] = field(default_factory=list)  # IPv4 und IPv6 (global/ULA)
    names: list[str] = field(default_factory=list)  # IPAM-Namen der IPs
    vm: dict | None = None  # {id, name, nic, portgroup, vlan, switch, host, cluster}
    epgs: list[str] = field(default_factory=list)
    link_local: list[str] = field(default_factory=list)  # IPv6 fe80::/10 (0.94.0), getrennt

    def add_ip(self, ip: str | None) -> None:
        """IP ergaenzen - Link-local getrennt (nicht als Identitaet)."""
        if not ip:
            return
        target = self.link_local if link_local(ip) else self.ips
        if ip not in target:
            target.append(ip)

    def out(self) -> dict:
        return {"ips": self.ips, "names": self.names, "vm": self.vm, "epgs": self.epgs,
                "link_local": self.link_local}  # fmt: skip


class Locator:
    """Uplinks aus der Verkabelung, aktuelle MAC-Eintraege und Identitaet je MAC."""

    def __init__(self, session: Session, tenant_id: int, cab: cab_store.Cabling | None = None):
        self.session, self.tenant_id = session, tenant_id
        self.cab = cab or cab_store.build(session, tenant_id)
        self.uplinks: set[tuple[str, str]] = set()
        self.peer_at: dict[tuple[str, str], cab_store.End] = {}
        lags = {(cab_store.device_key(i.device), cab_store.port_key(i.name)): i.lag
                for i in self.cab.ports if i.lag}  # fmt: skip
        for link in self.cab.links.values():
            for end, other in ((link.a, link.b), (link.b, link.a)):
                key = end.key
                self.peer_at.setdefault(key, other)
                lag = lags.get(key)
                lag_key = (key[0], cab_store.port_key(lag)) if lag else None
                if lag_key:
                    self.peer_at.setdefault(lag_key, other)
                if other.device.switching:
                    self.uplinks.add(key)
                    if lag_key:
                        self.uplinks.add(lag_key)
        self._ctx = None
        self._ids: dict[str, Identity] | None = None

    # Eintraege ---------------------------------------------------------------------------

    def current(self, *conds) -> list[MacEntry]:
        return list(self.session.scalars(_current(MacEntry, self.tenant_id, *conds)))

    def is_uplink(self, e: MacEntry) -> bool:
        return (cab_store.device_key(e.device), cab_store.port_key(e.port)) in self.uplinks

    def peer(self, e: MacEntry) -> cab_store.End | None:
        return self.peer_at.get((cab_store.device_key(e.device), cab_store.port_key(e.port)))

    def iface(self, e: MacEntry):
        return self.cab.ifaces.get((cab_store.device_key(e.device), cab_store.port_key(e.port)))

    # Identitaet --------------------------------------------------------------------------

    def identities(self, macs: set[str] | None = None) -> dict[str, Identity]:
        """Identitaet je MAC (alle bzw. nur `macs`)."""
        if self._ids is None:
            self._ids = self._load_identities()
        if macs is None:
            return self._ids
        return {m: self._ids[m] for m in macs if m in self._ids}

    def identity(self, mac: str) -> Identity:
        return self.identities().get(mac) or Identity()

    def _names(self, ip: str) -> list[str]:
        if self._ctx is None:
            self._ctx = references.context(self.session, self.tenant_id)
        return references.dns_names(self._ctx, ip)

    def _load_identities(self) -> dict[str, Identity]:
        ids: dict[str, Identity] = defaultdict(Identity)
        rows = self.session.execute(
            select(VmNic, Vm).join(Vm, Vm.id == VmNic.vm_id)
            .where(Vm.tenant_id == self.tenant_id, VmNic.mac.is_not(None))
        )  # fmt: skip
        for nic, vm in rows:
            mac = mac_key(nic.mac)
            if mac is None:
                continue
            ident = ids[mac]
            ident.vm = {"id": vm.id, "name": vm.name, "nic": nic.name, "portgroup": nic.portgroup,
                        "vlan": nic.vlan, "switch": nic.switch, "host": vm.host,
                        "cluster": vm.cluster, "power_state": vm.power_state}  # fmt: skip
            for ip in nic.ips or []:
                ident.add_ip(_ip(ip))
        for a in self.session.scalars(_current(ArpEntry, self.tenant_id)):
            ids[a.mac].add_ip(_ip(a.ip))  # ARP und IPv6-Nachbarn (0.94.0)
        endpoints = self.session.execute(
            select(EpgEndpoint.mac, EpgEndpoint.ip, Epg.name)
            .join(Epg, Epg.id == EpgEndpoint.epg_id)
            .where(Epg.tenant_id == self.tenant_id, EpgEndpoint.mac.is_not(None))
        )  # fmt: skip
        for raw, ip, epg in endpoints:
            mac = mac_key(raw)
            if mac is None:
                continue
            ident = ids[mac]
            ident.add_ip(_ip(ip))
            if epg and epg not in ident.epgs:
                ident.epgs.append(epg)
        return ids

    def named(self, ident: Identity) -> Identity:
        """IPAM-Namen der IPs ergaenzen (nur fuer gezeigte Zeilen)."""
        if ident.ips and not ident.names:
            for ip in ident.ips:
                for name in self._names(ip):
                    if name not in ident.names:
                        ident.names.append(name)
        return ident

    # Ausgabe -----------------------------------------------------------------------------

    def row(self, e: MacEntry) -> dict:
        """Ein MAC-Eintrag mit Port, Gegenstelle am Port und Identitaet."""
        iface = self.iface(e)
        peer = self.peer(e)
        ident = self.named(self.identity(e.mac))
        return {
            "mac": e.mac, "vlan": e.vlan, "device": e.device, "port": e.port, "kind": e.kind,
            "source_system_id": e.source_system_id,
            "first_seen": _aware(e.first_seen), "last_seen": _aware(e.last_seen),
            "uplink": self.is_uplink(e),
            "port_description": iface.description if iface is not None else None,
            "port_mode": iface.mode if iface is not None else None,
            "via": ({"device": peer.device.name, "port": peer.port, "kind": peer.device.kind,
                     "read": peer.device.read} if peer is not None else None),
            **ident.out(),
        }  # fmt: skip

    def locate(self, mac: str) -> dict:
        """Wo haengt die MAC: Anschluss (Ports, die keine Uplinks sind), sonst nur auf Uplinks
        gesehen (hinter der Gegenstelle); dazu frueher gesehene Ports."""
        now_rows = self.current(MacEntry.mac == mac)
        edge = [e for e in now_rows if not self.is_uplink(e)]
        behind = [e for e in now_rows if self.is_uplink(e)] if not edge else []
        current_ids = {e.id for e in now_rows}
        earlier = [
            e for e in self.session.scalars(
                select(MacEntry).where(MacEntry.tenant_id == self.tenant_id, MacEntry.mac == mac)
                .order_by(MacEntry.last_seen.desc()).limit(50))
            if e.id not in current_ids and not self.is_uplink(e)
        ]  # fmt: skip
        ident = self.named(self.identity(mac))
        return {
            "mac": mac,
            **ident.out(),
            "locations": [self.row(e) for e in _sorted(edge)],
            "behind": [self.row(e) for e in _sorted(behind)],
            "earlier": [self.row(e) for e in earlier[:10]],
            "seen_on": len(now_rows),
        }

    def edge_entries(self, *conds) -> list[MacEntry]:
        return [e for e in self.current(*conds) if not self.is_uplink(e)]


def _sorted(rows: list[MacEntry]) -> list[MacEntry]:
    return sorted(rows, key=lambda e: (e.device.casefold(), cab_store.port_key(e.port)))


# -- Suche „Wo haengt …“ ----------------------------------------------------------------------


def find_macs(session: Session, tenant_id: int, query: str, limit: int = 20) -> dict:
    """MACs zu einer Suche: MAC direkt, IP (ARP bzw. IPv6-Nachbarn, VM, ACI-Endpunkt, sonst
    IPAM-Name -> IP) bzw. Name (VM, IPAM-Name). Ergebnis {kind, macs: {mac: Grund}, ips}."""
    from app.store import resolve

    q = (query or "").strip()
    if not q:
        return {"kind": None, "macs": {}, "ips": []}
    mac = mac_key(q)
    if mac is not None and not _ip(q):
        return {"kind": "mac", "macs": {mac: "MAC"}, "ips": []}
    out: dict[str, str] = {}
    ips: list[str] = []
    kind = "ip" if _ip(q) else "name"
    if kind == "ip":
        ips = [_ip(q)]
    else:
        like = f"%{q.lower()}%"
        rows = session.execute(
            select(VmNic.mac, Vm.name).join(Vm, Vm.id == VmNic.vm_id)
            .where(Vm.tenant_id == tenant_id, VmNic.mac.is_not(None),
                   or_(func.lower(Vm.name).like(like), func.lower(Vm.hostname).like(like)))
            .limit(limit * 4)
        )  # fmt: skip
        for raw, name in rows:
            if (m := mac_key(raw)) is not None:
                out.setdefault(m, f"VM {name}")
        hits = resolve.resolve(session, tenant_id, q, limit=limit, only="host")["hits"]
        ips = [h["ip"] for h in hits if h.get("ip")]
    for ip in ips:
        for m in session.scalars(
            select(ArpEntry.mac).where(ArpEntry.tenant_id == tenant_id, ArpEntry.ip == ip)
            .order_by(ArpEntry.last_seen.desc()).limit(5)
        ):  # fmt: skip
            out.setdefault(m, f"{'NDP' if ':' in ip else 'ARP'} {ip}")  # IPv6 seit 0.94.0
        for raw, name in session.execute(
            select(VmNic.mac, Vm.name).join(VmIp, VmIp.nic_id == VmNic.id)
            .join(Vm, Vm.id == VmNic.vm_id)
            .where(VmIp.tenant_id == tenant_id, VmIp.ip == ip, VmNic.mac.is_not(None))
        ):  # fmt: skip
            if (m := mac_key(raw)) is not None:
                out.setdefault(m, f"VM {name} ({ip})")
        for raw in session.scalars(
            select(EpgEndpoint.mac).join(Epg, Epg.id == EpgEndpoint.epg_id)
            .where(Epg.tenant_id == tenant_id, EpgEndpoint.ip == ip,
                   EpgEndpoint.mac.is_not(None))
        ):  # fmt: skip
            if (m := mac_key(raw)) is not None:
                out.setdefault(m, L(f"ACI-Endpunkt {ip}", f"ACI endpoint {ip}"))
    return {"kind": kind, "macs": dict(list(out.items())[:limit]), "ips": ips}


# -- Befund: VLAN fehlt am Uplink eines ESXi-Hosts ---------------------------------------------


def _allowed(iface) -> set[int] | None:
    """VLANs, die der Port durchlaesst; None = unbekannt."""
    if iface is None or not iface.mode:
        return None
    if iface.mode == "access":
        return {iface.untagged_vlan} if iface.untagged_vlan else None
    vs = cab_store.vlan_set(iface.trunk_vlans)
    if vs is None or not iface.trunk_vlans:
        return None
    return vs | ({iface.native_vlan} if iface.native_vlan else set())


def checks(session: Session, tenant_id: int, loc: Locator | None = None, cab=None) -> list[dict]:
    """`uplink_vlan_missing` je Port eines gelesenen Switches, an dem VMs eines ESXi-Hosts
    gesehen werden und VLANs ihres Clusters/vSwitches fehlen (Text in der Sprache der Anfrage;
    der Abgleich speichert deutsch)."""
    if not session.scalar(select(MacEntry.id).where(MacEntry.tenant_id == tenant_id).limit(1)):
        return []
    loc = loc or Locator(session, tenant_id, cab)
    nics = session.execute(
        select(VmNic, Vm).join(Vm, Vm.id == VmNic.vm_id)
        .where(Vm.tenant_id == tenant_id, VmNic.mac.is_not(None), Vm.template.is_(False))
    ).all()  # fmt: skip
    if not nics:
        return []
    by_mac: dict[str, tuple[VmNic, Vm]] = {}
    needed: dict[tuple, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for nic, vm in nics:
        mac = mac_key(nic.mac)
        group = (vm.cluster or vm.host or "", nic.switch or "")
        if mac:
            by_mac[mac] = (nic, vm)
        if nic.vlan and 1 <= nic.vlan <= 4094:
            needed[group][nic.vlan].add(nic.portgroup or str(nic.vlan))
    # Ports gelesener Switches, an denen VMs gesehen werden: je (Port) Gruppe, Hosts, VMs
    ports: dict[tuple, dict] = {}
    for e in loc.edge_entries(MacEntry.mac.in_(list(by_mac))):
        if e.source_system_id not in loc.cab.switch_sources:
            continue
        nic, vm = by_mac[e.mac]
        group = (vm.cluster or vm.host or "", nic.switch or "")
        key = (e.source_system_id, e.device, e.port)
        entry = ports.setdefault(key, {"groups": set(), "hosts": set(), "vms": set()})
        entry["groups"].add(group)
        if vm.host:
            entry["hosts"].add(vm.host)
        entry["vms"].add(vm.name)
    out: list[dict] = []
    for (source_id, device, port), seen in sorted(ports.items(), key=lambda x: x[0][1:]):
        iface = loc.cab.ifaces.get((cab_store.device_key(device), cab_store.port_key(port)))
        allowed = _allowed(iface)
        if allowed is None:
            continue
        missing: dict[int, set[str]] = {}
        for group in seen["groups"]:
            for vlan, portgroups in needed[group].items():
                if vlan not in allowed:
                    missing.setdefault(vlan, set()).update(portgroups)
        if not missing:
            continue
        clusters = sorted({g[0] for g in seen["groups"] if g[0]})
        switches = sorted({g[1] for g in seen["groups"] if g[1]})
        vlans = sorted(missing)
        hosts = ", ".join(sorted(seen["hosts"])) or "?"
        shown = ", ".join(map(str, vlans[:12])) + (" …" if len(vlans) > 12 else "")
        text = L(f"VLAN {shown} fehlt an {device} {port} – Uplink von {hosts}",
                 f"VLAN {shown} missing on {device} {port} – uplink of {hosts}")  # fmt: skip
        out.append({
            "type": "uplink_vlan_missing", "severity": "warning",
            "subject": f"{cab_store.device_key(device)}|{cab_store.port_key(port)}",
            "source_system_id": source_id, "device": device, "port": port, "text": text,
            "vlans": vlans,
            "portgroups": {str(v): sorted(missing[v]) for v in vlans},
            "hosts": sorted(seen["hosts"]), "vms": sorted(seen["vms"])[:20],
            "clusters": clusters, "vswitches": switches,
            "mode": iface.mode, "allowed": iface.trunk_vlans if iface.mode == "tagged"
            else str(iface.untagged_vlan),
        })  # fmt: skip
    return out
