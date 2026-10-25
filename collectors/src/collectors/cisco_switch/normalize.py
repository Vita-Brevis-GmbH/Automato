"""Ausgaben der `show`-Befehle -> Netzinventar (ADR 0025).

Geparst wird mit den TextFSM-Vorlagen von ntc-templates (Apache-2.0) je Plattform (`cisco_ios`
fuer IOS/IOS-XE, `cisco_nxos`). Ergebnis je Switch: das Geraet (Rolle `switch`), die Ports mit
Status, Speed/Duplex, Modus und VLANs, Port-Channel und vPC, die VLANs und die Nachbarn aus
CDP/LLDP. Portnamen in der Kurzform des Switches (`Gi1/0/1`, `Eth1/1`, `Po10`).
"""

import ipaddress
import re

from collectors.common.dto import (
    NetworkInventory,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedNeighbor,
    NormalizedVlan,
)

NTC_PLATFORM = {"ios": "cisco_ios", "nxos": "cisco_nxos"}
# Langform -> Kurzform (wie in `show interfaces status`); laengste zuerst
_SHORT = (
    ("hundredgigabitethernet", "Hu"),
    ("hundredgige", "Hu"),
    ("fortygigabitethernet", "Fo"),
    ("twentyfivegigabitethernet", "Twe"),
    ("twentyfivegige", "Twe"),
    ("tengigabitethernet", "Te"),
    ("fivegigabitethernet", "Fi"),
    ("twogigabitethernet", "Tw"),
    ("appgigabitethernet", "Ap"),
    ("gigabitethernet", "Gi"),
    ("fastethernet", "Fa"),
    ("ethernet", "Eth"),
    ("port-channel", "Po"),
    ("loopback", "Lo"),
    ("tunnel", "Tu"),
    ("vlan", "Vl"),
)
_DEFAULT_VLANS = {1002, 1003, 1004, 1005}  # fddi-/token-ring-default (IOS)
TAGGED_MAX = 512  # bis zu so vielen VLANs je Trunk auch als Liste


def short_name(name: str | None) -> str:
    """`GigabitEthernet1/0/1` -> `Gi1/0/1`, `Ethernet1/1` -> `Eth1/1`, `port-channel10` ->
    `Po10`; Statusmarken wie `(P)` faellt weg."""
    text = re.sub(r"\(.*?\)$", "", (name or "").strip())
    low = text.lower()
    for long, short in _SHORT:
        if low.startswith(long):
            return short + text[len(long) :]
    return text


def _kind(name: str) -> str:
    low = name.lower()
    if low.startswith("po"):
        return "lag"
    if low.startswith("vl"):
        return "vlan"
    if low.startswith("lo"):
        return "loopback"
    if low.startswith("tu"):
        return "tunnel"
    return "physical"


def vlan_set(text: str | None) -> set[int] | None:
    """`1-4094`, `ALL`, `10,20,30-40` -> Menge; None = nicht lesbar."""
    raw = (text or "").replace(" ", "").strip().upper()
    if not raw or raw in ("NONE", "--"):
        return set()
    if raw == "ALL":
        return set(range(1, 4095))
    out: set[int] = set()
    for part in raw.split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        if not lo.isdigit() or (hi and not hi.isdigit()):
            return None
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def vlan_ranges(vlans: set[int]) -> str:
    """Menge -> `10,20,30-40` (aufsteigend)."""
    out, ordered = [], sorted(vlans)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        out.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ",".join(out)


def device_name(name: str | None) -> str:
    """Name eines Nachbarn ohne Seriennummer in Klammern (NX-OS `N9K(FDO123)`)."""
    return re.sub(r"\(.*?\)\s*$", "", (name or "").strip())


def cli_error(text: str) -> str | None:
    """Fehlermeldung des Switches statt einer Ausgabe (`% Invalid input`, `LLDP is not
    enabled`) - None, wenn die Ausgabe brauchbar ist."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    low = first.lower()
    if first.startswith("%") or "invalid command" in low or "not enabled" in low:
        return first.lstrip("% ").strip()
    return None


def parse(platform: str, command: str, text: str) -> list[dict]:
    """Ausgabe eines Befehls mit der ntc-Vorlage parsen (Schluessel klein)."""
    if not text.strip() or cli_error(text):
        return []
    from ntc_templates.parse import parse_output

    rows = parse_output(platform=NTC_PLATFORM[platform], command=command, data=text)
    return [{k.lower(): v for k, v in r.items()} for r in rows]


def detect_platform(version_text: str) -> str:
    return "nxos" if re.search(r"NX-OS|Nexus", version_text) else "ios"


def _first(value) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    value = (value or "").strip() if isinstance(value, str) else value
    return value or None


def _text(value) -> str | None:
    """Text ohne Platzhalter (`--` bei NX-OS)."""
    text = (value or "").strip() if isinstance(value, str) else ""
    return None if not text or text == "--" else text


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _ip(value) -> str | None:
    text = _first(value)
    try:
        return str(ipaddress.ip_address(text)) if text else None
    except ValueError:
        return None


def switch_inventory(host: str, platform: str, outputs: dict[str, str]) -> NetworkInventory:
    """Ein Switch: Ausgaben der Befehle (`COMMANDS`) -> Netzinventar."""
    from collectors.cisco_switch.client import COMMANDS

    cmds = COMMANDS[platform]
    p: dict[str, list[dict]] = {c: parse(platform, c, outputs.get(c, "")) for c in cmds}
    ver = (p["show version"] or [{}])[0]
    nx = platform == "nxos"
    name = _first(ver.get("hostname")) or host
    device = NormalizedDevice(
        name=name,
        role="switch",
        model=_first(ver.get("platform") if nx else ver.get("hardware")),
        serial=_first(ver.get("serial")),
        version=re.sub(r"\s*\[.*\]$", "", _first(ver.get("os") if nx else ver.get("version")) or "")
        or None,
        mgmt_ip=_ip(host),
    )
    ports: dict[str, dict] = {}

    def port(raw: str) -> dict:
        key = short_name(raw)
        return ports.setdefault(key, {"name": key})

    for r in p[cmds[1]]:  # status
        e = port(r.get("port", ""))
        e.update(status=_text(r.get("status")), duplex=_text(r.get("duplex")),
                 speed=_text(r.get("speed")), media=_text(r.get("type")),
                 desc_short=_text(r.get("name")), vlan_col=r.get("vlan_id"))  # fmt: skip
    for r in p[cmds[2]]:  # description (voller Text)
        e = port(r.get("port", ""))
        e["description"] = _text(r.get("description"))
        if not nx and r.get("status"):
            e.setdefault("status", "up" if r["status"] == "up" else r["status"])
            e["admin_down"] = r["status"] == "admin down"
    for r in p[cmds[3]]:  # switchport
        if str(r.get("switchport", "")).lower().startswith("disabled"):
            continue
        e = port(r.get("interface", ""))
        trunk = r.get("trunking_vlans")
        e.update(sw_mode=(r.get("mode") or "").lower(),
                 admin_mode=(r.get("admin_mode") or "").lower(),
                 access_vlan=_int(r.get("access_vlan")), native_vlan=_int(r.get("native_vlan")),
                 trunk_vlans=",".join(trunk) if isinstance(trunk, list) else trunk)  # fmt: skip
    for r in p[cmds[5]]:  # Port-Channel
        bundle = short_name(r.get("bundle_name", ""))
        if not bundle:
            continue
        port(bundle)
        for m in r.get("member_interface") or []:
            port(m)["lag"] = bundle
    if nx:
        for r in p["show vpc"]:  # vPC (und Peer-Link) je vorhandenem Port-Channel
            key = short_name(r.get("port"))
            if key in ports:
                ports[key]["vpc"] = r.get("id")

    neighbors: list[NormalizedNeighbor] = []
    for r in p["show cdp neighbors detail"]:
        local = short_name(r.get("local_interface"))
        if not local:
            continue
        neighbors.append(NormalizedNeighbor(
            device=name, port=local, protocol="cdp",
            remote_name=device_name(r.get("neighbor_name")) or "?",
            remote_port=(r.get("neighbor_interface") or "").strip() or None,
            remote_ip=_ip(r.get("mgmt_address")) or _ip(r.get("interface_ip")),
            remote_platform=(r.get("platform") or "").strip() or None,
            remote_caps=[c for c in re.split(r"[\s,]+", r.get("capabilities") or "") if c],
            remote_description=(r.get("neighbor_description") or "").strip()[:500] or None,
        ))  # fmt: skip
    for r in p["show lldp neighbors detail"]:
        local = short_name(r.get("local_interface"))
        if not local:
            continue
        remote_port = r.get("neighbor_port_id") or r.get("neighbor_interface")
        neighbors.append(NormalizedNeighbor(
            device=name, port=local, protocol="lldp",
            remote_name=device_name(r.get("neighbor_name")) or (r.get("chassis_id") or "?"),
            remote_port=(remote_port or "").strip() or None,
            remote_ip=_ip(r.get("mgmt_address")),
            remote_platform=(r.get("platform") or "").strip() or None,
            remote_caps=[c for c in re.split(r"[\s,]+", r.get("capabilities") or "") if c],
            remote_description=(r.get("neighbor_description") or "").strip()[:500] or None,
        ))  # fmt: skip
    first_neighbor: dict[str, NormalizedNeighbor] = {}
    for n in sorted(neighbors, key=lambda n: n.protocol):  # CDP vor LLDP
        first_neighbor.setdefault(n.port, n)

    interfaces = [_interface(name, e, first_neighbor.get(e["name"])) for e in ports.values()]
    vlans = []
    for r in p["show vlan brief"]:
        vid = _int(r.get("vlan_id"))
        if vid is None or vid in _DEFAULT_VLANS:
            continue
        vlans.append(NormalizedVlan(vid=vid, name=(r.get("vlan_name") or str(vid)).strip()))
    return NetworkInventory(devices=[device], interfaces=interfaces, vlans=vlans,
                            neighbors=neighbors)  # fmt: skip


def _interface(device: str, e: dict, nb: NormalizedNeighbor | None) -> NormalizedInterface:
    name = e["name"]
    status = e.get("status")
    mode_text = e.get("sw_mode") or ""
    if mode_text in ("down", "") and e.get("admin_mode"):
        mode_text = e["admin_mode"]
    vlan_col = str(e.get("vlan_col") or "")
    mode = None
    if "trunk" in mode_text or vlan_col == "trunk":
        mode = "tagged"
    elif "access" in mode_text or vlan_col.isdigit():
        mode = "access"
    untagged = None
    if mode == "access":
        untagged = e.get("access_vlan") or (int(vlan_col) if vlan_col.isdigit() else None)
    elif mode == "tagged":
        untagged = e.get("native_vlan")
    trunk_text = None
    tagged: list[int] = []
    if mode == "tagged":
        vset = vlan_set(e.get("trunk_vlans") or "ALL")
        if vset is not None:
            trunk_text = vlan_ranges(vset)
            tagged = sorted(vset) if len(vset) <= TAGGED_MAX else []
    attrs = {k: e[k] for k in ("media", "vpc") if e.get(k)}
    enabled = None
    if status is not None or e.get("admin_down") is not None:
        enabled = not (status == "disabled" or e.get("admin_down"))
    return NormalizedInterface(
        device=device, name=name, kind=_kind(name), enabled=enabled, mode=mode,
        untagged_vlan=untagged, tagged_vlans=tagged, lag=e.get("lag"),
        description=e.get("description") or e.get("desc_short"),
        neighbor=nb.remote_name if nb else None, neighbor_port=nb.remote_port if nb else None,
        status=status, speed=e.get("speed"), duplex=e.get("duplex"),
        native_vlan=e.get("native_vlan") if mode == "tagged" else None, trunk_vlans=trunk_text,
        attrs=attrs or None,
    )  # fmt: skip


def normalize(raw: dict) -> NetworkInventory:
    """Alle gelesenen Switches eines Quellsystems zusammen."""
    inv = NetworkInventory()
    seen_vlans: dict[int, NormalizedVlan] = {}
    for sw in raw.get("switches") or []:
        part = switch_inventory(sw["host"], sw["platform"], sw.get("outputs") or {})
        inv.devices += part.devices
        inv.interfaces += part.interfaces
        inv.neighbors += part.neighbors
        for v in part.vlans:  # VLAN-ID je Quellsystem einmal (erster Name)
            seen_vlans.setdefault(v.vid, v)
    inv.vlans = sorted(seen_vlans.values(), key=lambda v: v.vid)
    return inv
