"""vCenter-Rohdaten (einfache Dicts aus `client.gather`) -> VMs, Portgruppen und Hosts (ADR 0017).

Je VM: Datacenter und Cluster (ueber die Eltern der Hosts), NICs mit MAC, Portgruppe, Switch
und VLAN (verteilt: Portgruppe des dvSwitch; Standard: Portgruppe am Host) und den IPs aus den
VMware Tools (je NIC ueber den Geraete-Schluessel, sonst die MAC). Link-local-Adressen fallen
weg. Je ESXi-Host (1.8.0): Hersteller, Modell, Seriennummer, Version, Verwaltungs-Adresse und die
vmnics mit Speed und dem Nachbarn aus CDP (bevorzugt) bzw. LLDP. Rein funktional - testbar ohne
vCenter.
"""

import ipaddress
import re
from collections import Counter

from collectors.common.dto import (
    NormalizedHost,
    NormalizedHostNeighbor,
    NormalizedHostNic,
    NormalizedPortgroup,
    NormalizedVm,
    NormalizedVmNic,
    VirtualInventory,
)

_MAC = re.compile(r"^[0-9a-f]{2}([:-]?[0-9a-f]{2}){5}$|^([0-9a-f]{4}\.){2}[0-9a-f]{4}$", re.I)


def _usable(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    return not (addr.is_link_local or addr.is_loopback or addr.is_unspecified)


class _Tree:
    """Eltern-Beziehungen: Host -> Cluster/ComputeResource -> Ordner -> Datacenter."""

    def __init__(self, data: dict[str, list[dict]]):
        self.nodes = {
            o["moid"]: (kind, o)
            for kind in ("Folder", "Datacenter", "ComputeResource", "HostSystem")
            for o in data.get(kind, [])
        }

    def datacenter(self, moid: str | None) -> str | None:
        seen: set[str] = set()
        while moid and moid not in seen:
            seen.add(moid)
            kind, obj = self.nodes.get(moid, (None, {}))
            if kind == "Datacenter":
                return obj.get("name")
            moid = obj.get("parent")
        return None

    def cluster(self, host_moid: str | None) -> str | None:
        _, host = self.nodes.get(host_moid or "", (None, {}))
        kind, parent = self.nodes.get(host.get("parent") or "", (None, {}))
        return parent.get("name") if kind == "ComputeResource" else None


def normalize(data: dict[str, list[dict]]) -> VirtualInventory:
    tree = _Tree(data)
    hosts = {h["moid"]: h for h in data.get("HostSystem", [])}
    switches = {s["moid"]: s.get("name") for s in data.get("DistributedVirtualSwitch", [])}
    dvpgs = {p["key"]: p for p in data.get("DistributedVirtualPortgroup", []) if p.get("key")}
    standard = {
        (h["moid"], pg["name"]): pg for h in hosts.values() for pg in h.get("portgroups") or []
    }
    used: Counter = Counter()
    vms: list[NormalizedVm] = []
    for v in data.get("VirtualMachine", []):
        guest = v.get("guest_net") or []
        nics: list[NormalizedVmNic] = []
        for n in v.get("nics") or []:
            dvpg = dvpgs.get(n.get("portgroup_key") or "")
            if dvpg is not None:
                portgroup, switch, vlan = (
                    dvpg["name"],
                    switches.get(dvpg.get("switch")),
                    dvpg.get("vlan"),
                )
            else:
                portgroup = n.get("network_name")
                std = standard.get((v.get("host"), portgroup), {})
                switch, vlan = std.get("vswitch"), std.get("vlan") or None
            ips = [
                ip
                for g in guest
                if g.get("device_key") == n.get("key")
                or (g.get("device_key") in (None, -1) and g.get("mac") == n.get("mac"))
                for ip in g.get("ips") or []
                if _usable(ip)
            ]
            if portgroup:
                used[portgroup] += 1
            nics.append(
                NormalizedVmNic(
                    name=n.get("label") or f"NIC {n.get('key')}",
                    mac=n.get("mac"),
                    portgroup=portgroup,
                    switch=switch,
                    vlan=vlan,
                    connected=n.get("connected"),
                    ips=list(dict.fromkeys(ip.split("%")[0] for ip in ips)),
                )
            )
        host = hosts.get(v.get("host") or "", {})
        vms.append(
            NormalizedVm(
                name=v["name"],
                uuid=v.get("uuid"),
                power_state=v.get("power_state"),
                template=bool(v.get("template")),
                guest_os=v.get("guest_os"),
                hostname=v.get("hostname"),
                datacenter=tree.datacenter(v.get("host")),
                cluster=tree.cluster(v.get("host")),
                host=host.get("name"),
                vcpus=v.get("vcpus"),
                memory_mb=v.get("memory_mb"),
                disk_gb=v.get("disk_gb"),
                annotation=v.get("annotation"),
                nics=nics,
            )
        )
    portgroups = [
        NormalizedPortgroup(
            name=p["name"],
            kind="dvs",
            switch=switches.get(p.get("switch")),
            key=p.get("key"),
            vlan=p.get("vlan"),
            vlan_ranges=p.get("vlan_ranges"),
            vm_count=used.get(p["name"], 0),
        )
        for p in data.get("DistributedVirtualPortgroup", [])
    ]
    # Standard-Portgruppen je Name und VLAN: gleicher Name mit anderem VLAN auf einem anderen
    # Host ist ein anderes Netz (Dev-Befund 0.43.0 - sonst erbte die NIC das VLAN, und damit
    # die EPG, des ersten Hosts)
    seen = {(p.name, None) for p in portgroups}
    dvs = {p.name for p in portgroups}
    for (_host, name), pg in sorted(standard.items(), key=lambda x: x[0][1]):
        key = (name, pg.get("vlan") or None)
        if name not in dvs and key not in seen:
            seen.add(key)
            portgroups.append(
                NormalizedPortgroup(
                    name=name,
                    kind="standard",
                    switch=pg.get("vswitch"),
                    vlan=pg.get("vlan") or None,
                    vm_count=used.get(name, 0),
                )
            )
    return VirtualInventory(vms=vms, portgroups=portgroups, hosts=hosts_of(data))


def _speed(mb) -> str | None:
    """Mbit/s im Stil der Switches (NX-OS): bis 5000 als Zahl, ab 10 Gbit/s „10G“, „25G“ …"""
    if not isinstance(mb, int) or isinstance(mb, bool) or mb <= 0:
        return None
    return f"{mb // 1000}G" if mb >= 10000 and mb % 1000 == 0 else str(mb)


def _first_line(text: str | None) -> str | None:
    lines = (text or "").strip().splitlines()
    return (lines[0].strip() or None) if lines else None


def _ip_or_none(text: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((text or "").strip()))
    except ValueError:
        return None


def _lldp_caps(text: str | None) -> list[str]:
    """„System Capabilities“ (falls der Host sie liefert): „Bridge, Router“ -> bridge, router."""
    return [c.lower() for c in re.split(r"[,\s]+", text or "") if c]


def neighbor_of(nic: dict) -> NormalizedHostNeighbor | None:
    """Nachbar einer vmnic: CDP, wenn der Host ein CDP-Paket gehoert hat, sonst LLDP. Die
    LLDP-Port-ID ist bei manchen Switches die MAC des Ports - dann die Port-Beschreibung.

    CDP-Name (1.9.0): der Systemname vor der Geraete-ID - Anwender: „Die Gegenstelle vom VMware
    Host wird nicht korrekt erkannt, obwohl im CDP zwar die Geräte-ID nicht den Switchnamen steht,
    sondern eine ID. Aber im Systemname wäre der korrekte Name zu finden“."""
    cdp = nic.get("cdp") or {}
    name = (cdp.get("system_name") or "").strip() or cdp.get("dev_id")
    if name:
        return NormalizedHostNeighbor(
            protocol="cdp",
            remote_name=name,
            remote_port=cdp.get("port_id"),
            remote_ip=cdp.get("mgmt_addr") or cdp.get("address"),
            remote_platform=cdp.get("platform"),
            remote_caps=list(cdp.get("caps") or []),
        )
    lldp = nic.get("lldp") or {}
    params = lldp.get("params") or {}
    name = params.get("System Name") or lldp.get("chassis_id")
    if not name:
        return None
    port = lldp.get("port_id")
    if (not port or _MAC.match(port)) and params.get("Port Description"):
        port = params["Port Description"]
    return NormalizedHostNeighbor(
        protocol="lldp",
        remote_name=name,
        remote_port=port,
        remote_ip=params.get("Management Address"),
        remote_platform=_first_line(params.get("System Description")),
        remote_caps=_lldp_caps(params.get("System Capabilities")),
    )


def hosts_of(data: dict[str, list[dict]]) -> list[NormalizedHost]:
    """ESXi-Hosts mit vmnics und Nachbarn (1.8.0) - auch ohne Nachbarn (Abfrage gescheitert)."""
    out: list[NormalizedHost] = []
    for h in data.get("HostSystem", []):
        if not h.get("name"):
            continue
        nics = []
        for p in h.get("pnics") or []:
            if not p.get("name"):
                continue
            speed = _speed(p.get("speed_mb"))
            duplex = p.get("duplex")
            nics.append(
                NormalizedHostNic(
                    name=p["name"],
                    mac=p.get("mac"),
                    speed=speed,
                    duplex=("full" if duplex else "half") if speed and duplex is not None else None,
                    status="up" if speed else "down",
                    neighbor=neighbor_of(p),
                )
            )
        out.append(
            NormalizedHost(
                name=h["name"],
                vendor=h.get("vendor"),
                model=h.get("model"),
                serial=h.get("serial"),
                version=h.get("version"),
                # ohne VMkernel-Adresse: der Name, wenn der Host per IP im vCenter steht
                mgmt_ip=h.get("mgmt_ip") or _ip_or_none(h["name"]),
                pnics=nics,
            )
        )
    return out
