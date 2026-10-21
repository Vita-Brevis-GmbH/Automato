"""vCenter-Client (pyVmomi), rein lesend (ADR 0017).

Liest mit dem PropertyCollector in wenigen Aufrufen alle VMs, Hosts, Cluster, Datacenter,
Ordner und Portgruppen und gibt sie als einfache Dicts zurueck (`normalize` braucht kein
pyVmomi). vSphere spricht SOAP (nur POST) - der Nur-Lese-Schutz sitzt deshalb an den
Methoden: erlaubt sind Anmelden/Abmelden, Service-Content, Container-View anlegen/entfernen
und Eigenschaften abrufen; jeder andere Aufruf bricht vor dem Senden ab (`ReadOnlyViolation`).
Das Konto braucht im vCenter nur die Rolle Read-only.
"""

import ssl
from typing import Any
from urllib.parse import urlparse

from sot_gateway.readonly import ReadOnlyViolation

# Lesende vSphere-Methoden (SOAP-/WSDL-Name - eindeutig; der Python-Name nicht: "Destroy" ist
# sowohl View.DestroyView als auch ManagedEntity.Destroy_Task); alles andere wird blockiert
READ_METHODS = frozenset(
    {
        "Login",
        "Logout",
        "RetrieveServiceContent",
        "CurrentTime",
        "CreateContainerView",
        "DestroyView",
        "RetrievePropertiesEx",
        "ContinueRetrievePropertiesEx",
        "CancelRetrievePropertiesEx",
        "RetrieveProperties",
    }
)

# Eigenschaften je Objektart (PropertyCollector)
PROPS: dict[str, list[str]] = {
    "VirtualMachine": [
        "name",
        "config.instanceUuid",
        "config.template",
        "config.guestFullName",
        "config.annotation",
        "config.hardware.numCPU",
        "config.hardware.memoryMB",
        "config.hardware.device",
        "runtime.powerState",
        "runtime.host",
        "guest.hostName",
        "guest.net",
    ],
    "HostSystem": ["name", "parent", "config.network.portgroup"],
    "ComputeResource": ["name", "parent"],
    "Folder": ["name", "parent"],
    "Datacenter": ["name", "parent"],
    "DistributedVirtualPortgroup": [
        "name",
        "key",
        "config.defaultPortConfig",
        "config.distributedVirtualSwitch",
    ],
    "DistributedVirtualSwitch": ["name"],
}


def guard(stub) -> None:
    """Nur-Lese-Schutz an der Verbindung: nicht lesende Methoden brechen vor dem Senden ab."""
    invoke = stub.InvokeMethod

    def read_only(mo, info, args, *rest, **kw):
        method = getattr(info, "wsdlName", None) or info.name
        if method not in READ_METHODS:
            raise ReadOnlyViolation(f"nur lesend: vSphere-Methode {method} blockiert")
        return invoke(mo, info, args, *rest, **kw)

    stub.InvokeMethod = read_only


def _moid(value) -> str | None:
    return getattr(value, "_moId", None) if value is not None else None


def _vlan(port_config) -> tuple[int | None, str | None]:
    """VLAN einer verteilten Portgruppe: (VLAN-ID, Trunk-Bereiche bzw. PVLAN)."""
    spec = getattr(port_config, "vlan", None)
    vid = getattr(spec, "vlanId", None)
    if isinstance(vid, int):
        return (vid or None), None
    if isinstance(vid, list):  # Trunk: NumericRange[]
        ranges = [
            f"{r.start}-{r.end}" if r.start != r.end else str(r.start)
            for r in vid
            if hasattr(r, "start")
        ]
        return None, ",".join(ranges) or None
    pvlan = getattr(spec, "pvlanId", None)
    return None, (f"PVLAN {pvlan}" if pvlan is not None else None)


def _devices(devices) -> tuple[list[dict], float | None]:
    """Netzwerkkarten (MAC, Portgruppe) und Summe der Disks einer VM."""
    nics: list[dict] = []
    disk_kb = 0
    for d in devices or []:
        if hasattr(d, "capacityInKB") and type(d).__name__.endswith("VirtualDisk"):
            disk_kb += int(getattr(d, "capacityInKB", 0) or 0)
            continue
        if not hasattr(d, "macAddress"):
            continue
        backing = getattr(d, "backing", None)
        port = getattr(backing, "port", None)
        nics.append(
            {
                "key": getattr(d, "key", None),
                "label": getattr(getattr(d, "deviceInfo", None), "label", None),
                "mac": getattr(d, "macAddress", None),
                "connected": getattr(getattr(d, "connectable", None), "connected", None),
                "portgroup_key": getattr(port, "portgroupKey", None),
                "network_name": getattr(backing, "deviceName", None),
            }
        )
    return nics, (round(disk_kb / 1024 / 1024, 1) if disk_kb else None)


def _guest_net(nets) -> list[dict]:
    return [
        {
            "device_key": getattr(n, "deviceConfigId", None),
            "network": getattr(n, "network", None),
            "mac": getattr(n, "macAddress", None),
            "connected": getattr(n, "connected", None),
            "ips": [str(ip) for ip in getattr(n, "ipAddress", None) or []],
        }
        for n in nets or []
    ]


def to_plain(kind: str, moid: str, props: dict[str, Any]) -> dict:
    """pyVmomi-Eigenschaften -> einfaches Dict (fuer `normalize`)."""
    if kind == "VirtualMachine":
        nics, disk_gb = _devices(props.get("config.hardware.device"))
        return {
            "moid": moid,
            "name": props.get("name"),
            "uuid": props.get("config.instanceUuid"),
            "template": bool(props.get("config.template")),
            "guest_os": props.get("config.guestFullName"),
            "annotation": props.get("config.annotation") or None,
            "vcpus": props.get("config.hardware.numCPU"),
            "memory_mb": props.get("config.hardware.memoryMB"),
            "disk_gb": disk_gb,
            "power_state": str(props["runtime.powerState"])
            if props.get("runtime.powerState") is not None
            else None,
            "host": _moid(props.get("runtime.host")),
            "hostname": props.get("guest.hostName"),
            "nics": nics,
            "guest_net": _guest_net(props.get("guest.net")),
        }
    if kind == "HostSystem":
        pgs = props.get("config.network.portgroup") or []
        return {
            "moid": moid,
            "name": props.get("name"),
            "parent": _moid(props.get("parent")),
            "portgroups": [
                {
                    "name": getattr(getattr(p, "spec", None), "name", None),
                    "vlan": getattr(getattr(p, "spec", None), "vlanId", None),
                    "vswitch": getattr(getattr(p, "spec", None), "vswitchName", None),
                }
                for p in pgs
            ],
        }
    if kind == "DistributedVirtualPortgroup":
        vlan, ranges = _vlan(props.get("config.defaultPortConfig"))
        return {
            "moid": moid,
            "name": props.get("name"),
            "key": props.get("key"),
            "vlan": vlan,
            "vlan_ranges": ranges,
            "switch": _moid(props.get("config.distributedVirtualSwitch")),
        }
    return {"moid": moid, "name": props.get("name"), "parent": _moid(props.get("parent"))}


class VCenterClient:
    def __init__(self, base_url: str, username: str, password: str, *, verify_tls: bool = True):
        url = urlparse(base_url if "://" in base_url else f"https://{base_url}")
        self.host = url.hostname or base_url
        self.port = url.port or 443
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self._si = None

    def __enter__(self) -> "VCenterClient":
        try:
            from pyVim.connect import SmartConnect
        except ImportError as exc:  # pragma: no cover - Installation ohne pyvmomi
            raise RuntimeError(
                "pyvmomi fehlt - Agent neu installieren bzw. Selbst-Update abwarten"
            ) from exc
        context = ssl.create_default_context()
        if not self.verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._si = SmartConnect(
            host=self.host,
            port=self.port,
            user=self.username,
            pwd=self.password,
            sslContext=context,
            httpConnectionTimeout=30,
        )
        guard(self._si._stub)
        return self

    def __exit__(self, *exc) -> None:
        if self._si is not None:
            from pyVim.connect import Disconnect

            try:
                Disconnect(self._si)
            finally:
                self._si = None

    def ping(self) -> str:
        """Leichter Lesezugriff: Zeit des vCenters (Anmeldung ist damit geprueft)."""
        return str(self._si.CurrentTime())

    def gather(self) -> dict[str, list[dict]]:
        """Alle Objekte der Arten in PROPS als einfache Dicts, je Art eine Liste."""
        from pyVmomi import vim, vmodl

        content = self._si.RetrieveContent()
        types = {name: getattr(vim, name) for name in PROPS}
        view = content.viewManager.CreateContainerView(
            content.rootFolder, list(types.values()), True
        )
        pc = vmodl.query.PropertyCollector
        try:
            traverse = pc.TraversalSpec(
                name="view", path="view", skip=False, type=vim.view.ContainerView
            )
            spec = pc.FilterSpec(
                objectSet=[pc.ObjectSpec(obj=view, skip=True, selectSet=[traverse])],
                propSet=[pc.PropertySpec(type=t, pathSet=PROPS[n]) for n, t in types.items()],
            )
            out: dict[str, list[dict]] = {name: [] for name in PROPS}
            result = content.propertyCollector.RetrievePropertiesEx(
                specSet=[spec], options=pc.RetrieveOptions(maxObjects=500)
            )
            while result is not None:
                for obj in result.objects:
                    kind = next((n for n, t in types.items() if isinstance(obj.obj, t)), "Folder")
                    props = {p.name: p.val for p in obj.propSet or []}
                    out[kind].append(to_plain(kind, obj.obj._moId, props))
                token = result.token
                result = (
                    content.propertyCollector.ContinueRetrievePropertiesEx(token) if token else None
                )
            return out
        finally:
            view.DestroyView()
