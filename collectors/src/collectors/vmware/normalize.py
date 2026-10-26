"""vCenter-Rohdaten (einfache Dicts aus `client.gather`) -> VMs und Portgruppen (ADR 0017).

Je VM: Datacenter und Cluster (ueber die Eltern der Hosts), NICs mit MAC, Portgruppe, Switch
und VLAN (verteilt: Portgruppe des dvSwitch; Standard: Portgruppe am Host) und den IPs aus den
VMware Tools (je NIC ueber den Geraete-Schluessel, sonst die MAC). Link-local-Adressen fallen
weg. Rein funktional - testbar ohne vCenter.
"""

import ipaddress
from collections import Counter

from collectors.common.dto import (
    NormalizedPortgroup,
    NormalizedVm,
    NormalizedVmNic,
    VirtualInventory,
)


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
    return VirtualInventory(vms=vms, portgroups=portgroups)
