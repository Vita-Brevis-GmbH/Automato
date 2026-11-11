"""vCenter-Client (pyVmomi), rein lesend (ADR 0017).

Liest mit dem PropertyCollector in wenigen Aufrufen alle VMs, Hosts, Cluster, Datacenter,
Ordner und Portgruppen und gibt sie als einfache Dicts zurueck (`normalize` braucht kein
pyVmomi). vSphere spricht SOAP (nur POST) - der Nur-Lese-Schutz sitzt deshalb an den
Methoden: erlaubt sind Anmelden/Abmelden, Service-Content, Container-View anlegen/entfernen,
Eigenschaften abrufen und (1.8.0) die Netzwerk-Hinweise eines Hosts; jeder andere Aufruf bricht
vor dem Senden ab (`ReadOnlyViolation`). Das Konto braucht im vCenter nur die Rolle Read-only.

ESXi-Hosts (1.8.0, Anwender: „Wird in VMware die Ports und Nachbarschaft nicht ausgelesen von
den Hosts?“): Hardware (Hersteller, Modell, Seriennummer), Version, Verwaltungs-Adresse, die
physischen NICs (vmnic, MAC, Speed) und je vmnic der Nachbar, den der Host per CDP bzw. LLDP
hoert (`HostNetworkSystem.QueryNetworkHint`). Scheitert diese Abfrage fuer einen Host (Recht,
Host getrennt, Zeitueberschreitung), bekommt er keine Nachbarn (`neighbors_error`) - der Lauf
geht weiter.
"""

import ssl
from typing import Any
from urllib.parse import urlparse

from sot_gateway.readonly import ReadOnlyViolation

from collectors.common.i18n import L

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
        # 1.8.0: lesende Abfrage - was der Host je vmnic per CDP/LLDP hoert (und welche Netze er
        # dort sieht); aendert nichts am Host. Laut vSphere-API braucht sie nur das Recht
        # System.Read, das die Rolle Read-only enthaelt (pyVmomi: `privId` der Methode)
        "QueryNetworkHint",
    }
)
# Hosts in Folge, deren Abfrage an der Verbindung scheitert (keine vSphere-Meldung, z. B.
# Zeitueberschreitung), bevor die uebrigen nicht mehr abgefragt werden - sonst wartete der Lauf
# bei einem haengenden vCenter je Host bis zum Timeout
MAX_TRANSPORT_ERRORS = 3
NOT_QUERIED = "not queried"  # Hosts nach MAX_TRANSPORT_ERRORS (`neighbors_error`)
_CDP_CAPS = (
    ("networkSwitch", "switch"),
    ("router", "router"),
    ("transparentBridge", "trans-bridge"),
    ("sourceRouteBridge", "source-route-bridge"),
    ("host", "host"),
    ("repeater", "repeater"),
)
# Seriennummer im Hardware-Inventar aelterer vSphere-Versionen (ohne `serialNumber`)
_SERIAL_TAGS = ("SerialNumberTag", "ServiceTag", "EnclosureSerialNumberTag")

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
    "HostSystem": [
        "name",
        "parent",
        "config.network.portgroup",
        # 1.8.0: Hardware, Version, physische NICs, VMkernel-NICs (Verwaltungs-Adresse), Zustand
        # der Verbindung und die Referenz fuer die Netzwerk-Hinweise (CDP/LLDP)
        "runtime.connectionState",
        "hardware.systemInfo",
        "config.product.version",
        "config.network.pnic",
        "config.network.vnic",
        "configManager.networkSystem",
    ],
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
            raise ReadOnlyViolation(
                L(
                    f"nur lesend: vSphere-Methode {method} blockiert",
                    f"read-only: vSphere method {method} blocked",
                )
            )
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


def _serial(info) -> str | None:
    """Seriennummer des Hosts: `serialNumber` (ab vSphere 6.7), sonst aus den Kennungen."""
    if getattr(info, "serialNumber", None):
        return info.serialNumber
    tags = {}
    for i in getattr(info, "otherIdentifyingInfo", None) or []:
        key = getattr(getattr(i, "identifierType", None), "key", None)
        tags[key] = getattr(i, "identifierValue", None)
    return next((tags[t] for t in _SERIAL_TAGS if tags.get(t)), None)


def _mgmt_ip(vnics) -> str | None:
    """Verwaltungs-Adresse: IPv4 der VMkernel-NIC an der Portgruppe „Management …“, sonst vmk0,
    sonst die erste VMkernel-NIC mit Adresse (die Rolle „Management“ fragt der Collector nicht
    ab - das waere eine weitere Methode)."""
    found = []
    for v in vnics or []:
        ip = getattr(getattr(getattr(v, "spec", None), "ip", None), "ipAddress", None)
        if ip:
            found.append((getattr(v, "portgroup", None) or "", getattr(v, "device", None), ip))
    for check in (lambda pg, dev: "management" in pg.lower(), lambda pg, dev: dev == "vmk0"):
        hit = next((ip for pg, dev, ip in found if check(pg, dev)), None)
        if hit:
            return hit
    return found[0][2] if found else None


def _pnic(p) -> dict:
    """Physische NIC: Name (vmnic), MAC, Speed in Mbit/s und Duplex (ohne Link: leer)."""
    link = getattr(p, "linkSpeed", None)
    return {
        "name": getattr(p, "device", None),
        "mac": getattr(p, "mac", None),
        "speed_mb": getattr(link, "speedMb", None) if link is not None else None,
        "duplex": getattr(link, "duplex", None) if link is not None else None,
    }


def _text(value) -> str | None:
    """Wert eines LLDP-Parameters als Text (nur einfache Werte)."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    return str(value).strip() or None


def _cdp(info) -> dict | None:
    """CDP-Daten des Switches an einer vmnic (`PhysicalNicCdpInfo`) - ohne Geraete-ID und ohne
    Systemnamen keine. 1.9.0: ein Systemname allein reicht (Anwender: „im CDP zwar die
    Geräte-ID nicht den Switchnamen steht, sondern eine ID. Aber im Systemname wäre der korrekte
    Name zu finden“) - `normalize.neighbor_of` nimmt ihn vor der Geraete-ID."""
    if info is None or not (getattr(info, "devId", None) or getattr(info, "systemName", None)):
        return None
    caps = getattr(info, "deviceCapability", None)
    return {
        "dev_id": getattr(info, "devId", None),
        "port_id": getattr(info, "portId", None),
        "address": getattr(info, "address", None),
        "mgmt_addr": getattr(info, "mgmtAddr", None),
        "platform": getattr(info, "hardwarePlatform", None),
        "system_name": getattr(info, "systemName", None),
        "caps": [name for attr, name in _CDP_CAPS if getattr(caps, attr, False) is True],
    }


def _lldp(info) -> dict | None:
    """LLDP-Daten an einer vmnic (`LinkLayerDiscoveryProtocolInfo`): Chassis, Port, Parameter
    wie „System Name“, „Management Address“, „System Description“."""
    if info is None:
        return None
    params = {
        str(p.key): _text(getattr(p, "value", None))
        for p in getattr(info, "parameter", None) or []
        if getattr(p, "key", None)
    }
    out = {
        "chassis_id": getattr(info, "chassisId", None),
        "port_id": getattr(info, "portId", None),
        "params": {k: v for k, v in params.items() if v is not None},
    }
    return out if out["chassis_id"] or out["port_id"] or out["params"] else None


def hint_plain(hint) -> dict:
    """Netzwerk-Hinweis einer vmnic (`PhysicalNicHintInfo`) -> einfaches Dict."""
    return {
        "device": getattr(hint, "device", None),
        "cdp": _cdp(getattr(hint, "connectedSwitchPort", None)),
        "lldp": _lldp(getattr(hint, "lldpInfo", None)),
    }


def _reason(exc: Exception) -> str:
    """Kurzer Grund fuer den Hinweis im Lauf: Art der vSphere-Meldung bzw. Ausnahme."""
    return type(exc).__name__.rsplit(".", 1)[-1]


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
        info = props.get("hardware.systemInfo")
        state = props.get("runtime.connectionState")
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
            # 1.8.0: Hardware, Version, Verwaltungs-Adresse, vmnics (Nachbarn: `gather`)
            "vendor": getattr(info, "vendor", None),
            "model": getattr(info, "model", None),
            "serial": _serial(info),
            "version": props.get("config.product.version"),
            "mgmt_ip": _mgmt_ip(props.get("config.network.vnic")),
            "connection": str(state) if state is not None else None,
            "network_system": _moid(props.get("configManager.networkSystem")),
            "pnics": [_pnic(p) for p in props.get("config.network.pnic") or []],
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
                L(
                    "pyvmomi fehlt - Agent neu installieren bzw. Selbst-Update abwarten",
                    "pyvmomi missing - reinstall the agent or wait for the self-update",
                )
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
        """Alle Objekte der Arten in PROPS als einfache Dicts, je Art eine Liste; an den vmnics
        der Hosts die Nachbarn aus CDP/LLDP (1.8.0)."""
        data = self._retrieve(PROPS)
        self.neighbors(data.get("HostSystem") or [])
        return data

    def neighbors(self, hosts: list[dict]) -> None:
        """Je verbundenem Host `QueryNetworkHint` (ohne Argument: alle vmnics) - die vmnics
        bekommen `cdp`/`lldp`. Scheitert die Abfrage, steht der Grund in `neighbors_error`
        und der Host bleibt ohne Nachbarn; der Lauf geht weiter. Nach MAX_TRANSPORT_ERRORS
        Verbindungsfehlern in Folge werden die uebrigen Hosts nicht mehr abgefragt."""
        from pyVmomi import vim, vmodl

        failed = 0
        for host in hosts:
            if failed >= MAX_TRANSPORT_ERRORS:
                host["neighbors_error"] = NOT_QUERIED
                continue
            state = host.get("connection")
            if state not in (None, "connected"):  # getrennt bzw. antwortet nicht
                host["neighbors_error"] = state
                continue
            if not host.get("network_system"):
                continue
            system = vim.host.NetworkSystem(host["network_system"], self._si._stub)
            try:
                hints = system.QueryNetworkHint()
            except ReadOnlyViolation:
                raise  # Fehler im Code, nicht im vCenter - nie still uebergehen
            except Exception as exc:  # noqa: BLE001 - je Host: Hinweis statt Abbruch
                host["neighbors_error"] = _reason(exc)
                failed = 0 if isinstance(exc, vmodl.MethodFault) else failed + 1
                continue
            failed = 0
            by_device = {h["device"]: h for h in map(hint_plain, hints or []) if h["device"]}
            for nic in host.get("pnics") or []:
                hint = by_device.get(nic.get("name"))
                if hint is not None:
                    nic["cdp"], nic["lldp"] = hint["cdp"], hint["lldp"]

    def probe(self) -> int:
        """Leserecht im Inventar pruefen wie beim Lauf (Container-View ab dem Wurzelordner,
        braucht System.View): Anzahl sichtbarer VMs."""
        return len(self._retrieve({"VirtualMachine": ["name"]})["VirtualMachine"])

    def _retrieve(self, wanted: dict[str, list[str]]) -> dict[str, list[dict]]:
        from pyVmomi import vim, vmodl

        content = self._si.RetrieveContent()
        types = {name: getattr(vim, name) for name in wanted}
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
                propSet=[pc.PropertySpec(type=t, pathSet=wanted[n]) for n, t in types.items()],
            )
            out: dict[str, list[dict]] = {name: [] for name in wanted}
            result = content.propertyCollector.RetrievePropertiesEx(
                specSet=[spec], options=pc.RetrieveOptions(maxObjects=500)
            )
            while result is not None:
                for obj in result.objects:
                    kind = next((n for n, t in types.items() if isinstance(obj.obj, t)), None)
                    if kind is None:  # nicht angefragte Art
                        continue
                    props = {p.name: p.val for p in obj.propSet or []}
                    out[kind].append(to_plain(kind, obj.obj._moId, props))
                token = result.token
                result = (
                    content.propertyCollector.ContinueRetrievePropertiesEx(token) if token else None
                )
            return out
        finally:
            view.DestroyView()
