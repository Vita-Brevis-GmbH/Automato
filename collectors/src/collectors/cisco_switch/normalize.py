"""Ausgaben der `show`-Befehle -> Netzinventar (ADR 0025).

Geparst wird mit den TextFSM-Vorlagen von ntc-templates (Apache-2.0) je Plattform (`cisco_ios`
fuer IOS/IOS-XE, `cisco_nxos`). Ergebnis je Switch: das Geraet (Rolle `switch`), die Ports mit
Status, Speed/Duplex, Modus und VLANs, Port-Channel und vPC, die VLANs und die Nachbarn aus
CDP/LLDP. Portnamen in der Kurzform des Switches (`Gi1/0/1`, `Eth1/1`, `Po10`).

Seit 0.63.0 die MAC-Adress-Tabelle (nur Unicast an Ports, nicht CPU/Router/Peer-Link) und ARP
(ohne die eigenen Adressen des Switches); MACs einheitlich `aa:bb:cc:dd:ee:ff`.

Seit 0.68.0 weitere Plattformen nach `show version`: Cisco Business-CLI (Catalyst 1200/1300,
`cbs.py`) und Juniper Junos (MX, EX, QFX; `junos.py`); IOS/IOS-XE deckt Catalyst 9200L, 3650,
4500 und 9300 ab, NX-OS die Nexus 9000.

Seit 0.88.x IPv6-Nachbarn (NDP, `nd.py`) wie ARP und weitere Hersteller: Arista EOS (JSON,
`arista.py`) und HPE Aruba CX (AOS-CX, `aruba.py`).

Seit 0.65.0 Spanning Tree (je Port die VLANs, in denen er blockiert bzw. Root-Port ist, in
`attrs.stp_blocked`/`stp_root`) und `show vpc` selbst ausgewertet: Domaene, Rolle, Peer- und
Keepalive-Status am Peer-Link (`attrs.vpc_peer_link`), je vPC Status und Konsistenz.
"""

import ipaddress
import re

from collectors.cisco_switch import nd
from collectors.common.dto import (
    NetworkInventory,
    NormalizedArp,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedMac,
    NormalizedNeighbor,
    NormalizedVlan,
)

NTC_PLATFORM = {"ios": "cisco_ios", "nxos": "cisco_nxos", "aoscx": "aruba_aoscx"}
# Bezeichnung je Plattform (Meldungen, Pruefung der Verbindung)
PLATFORM_LABEL = {"ios": "IOS/IOS-XE", "nxos": "NX-OS", "cbs": "Catalyst 1200/1300 (Business-CLI)",
                  "junos": "Junos", "eos": "Arista EOS", "aoscx": "Aruba CX (AOS-CX)"}  # fmt: skip
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
MAX_MACS = 50_000  # MAC-Eintraege je Switch (grosse Cores); mehr = Hinweis im Lauf
_PORT_LIKE = re.compile(r"^[A-Za-z][A-Za-z-]*\d[\d/.:]*$")
_NOT_PORT = ("sup", "vl", "lo", "tu", "nve", "mgmt")  # Supervisor, SVI, Loopback, Tunnel, VXLAN


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
    failed = first.startswith("%") or "invalid command" in low or "not enabled" in low
    if failed or low.startswith("invalid input"):  # AOS-CX ohne „%“ (0.88.x)
        return first.lstrip("% ").strip()
    if low.startswith("error:") or low.startswith("syntax error"):  # Junos (0.68.0)
        return first.split(":", 1)[-1].strip() if low.startswith("error:") else first
    return None


def quiet(platform: str, command: str, error: str) -> bool:
    """Fehlermeldung ohne Hinweis im Lauf: Funktion aus (LLDP), Befehl gibt es auf der Plattform
    nicht (Junos: MX ohne ELS bzw. EX ohne Bridge-Domains; IPv6-Nachbarn auf alter Software bzw.
    ohne IPv6, 0.88.x)."""
    from collectors.cisco_switch import junos

    low = error.lower()
    return (
        "not enabled" in low
        or "not running" in low
        or command in nd.COMMANDS
        or (platform == "junos" and command in junos.OPTIONAL)
    )


def parse(platform: str, command: str, text: str) -> list[dict]:
    """Ausgabe eines Befehls mit der ntc-Vorlage parsen (Schluessel klein)."""
    if not text.strip() or cli_error(text):
        return []
    from ntc_templates.parse import parse_output

    try:
        rows = parse_output(platform=NTC_PLATFORM[platform], command=command, data=text)
    except Exception:  # noqa: BLE001 - keine Vorlage fuer NX-OS: gleiche Spalten wie IOS
        if platform != "nxos" or command not in _IOS_FALLBACK:
            raise
        rows = parse_output(platform="cisco_ios", command=command, data=text)
    return [{k.lower(): v for k, v in r.items()} for r in rows]


_IOS_FALLBACK = {"show spanning-tree"}
_VPC_ROW = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?")


def vpc_info(text: str) -> dict:
    """`show vpc` (NX-OS): Domaene, Rolle, Peer-/Keepalive-Status, Peer-Link und je vPC Port,
    Status und Konsistenz."""
    out: dict = {"domain": None, "role": None, "peer": None, "keepalive": None,
                 "peer_link": None, "peer_link_status": None, "vpcs": []}  # fmt: skip
    if not text.strip() or cli_error(text):
        return out
    fields = {"vpc domain id": "domain", "vpc role": "role", "peer status": "peer",
              "vpc keep-alive status": "keepalive"}  # fmt: skip
    section = None
    for line in text.splitlines():
        low = line.strip().lower()
        key, _, value = line.partition(":")
        if key.strip().lower() in fields and value.strip():
            out[fields[key.strip().lower()]] = value.strip()
            continue
        if low.startswith("vpc peer-link status"):
            section = "peer"
            continue
        if low.startswith("vpc status"):
            section = "vpc"
            continue
        m = _VPC_ROW.match(line)
        if not m or section is None:
            continue
        port = short_name(m.group(2))
        if section == "peer" and out["peer_link"] is None:
            out["peer_link"], out["peer_link_status"] = port, m.group(3).lower()
        elif section == "vpc":
            out["vpcs"].append({"id": m.group(1), "port": port, "status": m.group(3).lower(),
                                "consistency": (m.group(4) or "").lower() or None})  # fmt: skip
    return out


def _stp(rows: list[dict]) -> dict[str, dict[str, set[int]]]:
    """Spanning Tree: je Port die VLANs, in denen er blockiert (BLK) bzw. Root-Port ist."""
    out: dict[str, dict[str, set[int]]] = {}
    for r in rows:
        port = short_name(r.get("interface"))
        vlan = _int(r.get("vlan_id"))
        if not port or vlan is None:
            continue
        status = (r.get("status") or "").upper()
        role = (r.get("role") or "").lower()
        entry = out.setdefault(port, {"blocked": set(), "root": set()})
        if status.startswith("BLK") or (role in ("altn", "back") and status != "FWD"):
            entry["blocked"].add(vlan)
        if role == "root":
            entry["root"].add(vlan)
    return out


def detect_platform(version_text: str) -> str:
    """Plattform aus `show version`: NX-OS, Junos, Arista EOS, Aruba CX, Business-CLI (Catalyst
    1200/1300), sonst IOS."""
    if re.search(r"NX-OS|Nexus", version_text):
        return "nxos"
    if re.search(r"^(Junos:|JUNOS )", version_text, re.M):
        return "junos"
    if re.search(r"^Arista\b|^Software image version:|Arista Networks EOS", version_text, re.M):
        return "eos"  # 0.88.x
    if re.search(r"\bA(?:ruba)?OS-CX\b", version_text):
        return "aoscx"  # 0.88.x
    if re.search(r"^\s*(Active-image:|SW version\s)|Catalyst 1[23]00|\bC1[23]00-|\bCBS\d{3}",
                 version_text, re.M):  # fmt: skip
        return "cbs"
    return "ios"


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


def mac_text(value) -> str | None:
    """`0050.5601.0001`, `00-50-56-01-00-01`, `00:50:56:01:00:01` -> `00:50:56:01:00:01`."""
    digits = re.sub(r"[^0-9a-fA-F]", "", value if isinstance(value, str) else "")
    if len(digits) != 12:
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2)).lower()


def _l2_port(value) -> str | None:
    """Port eines MAC-Eintrags: nur ein einzelner Port (nicht CPU, Router, Supervisor,
    `vPC Peer-Link`, mehrere Ports bei Multicast)."""
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    name = short_name(value)
    if not _PORT_LIKE.match(name) or name.lower().startswith(_NOT_PORT):
        return None
    return name


def _l2(name: str, p: dict[str, list[dict]], arp_cmd: str):
    """MAC-Tabelle und ARP eines Switches -> (MACs, ARP, Anzahl ueber MAX_MACS)."""
    macs: list[NormalizedMac] = []
    seen: set[tuple] = set()
    for r in p.get("show mac address-table") or []:
        mac = mac_text(r.get("destination_address") or r.get("mac_address"))
        port = _l2_port(r.get("destination_port") or r.get("ports"))
        if not mac or not port or int(mac[:2], 16) & 1:  # Multicast/Broadcast
            continue
        vlan = _int(r.get("vlan_id"))
        if (mac, vlan, port) in seen:
            continue
        seen.add((mac, vlan, port))
        macs.append(NormalizedMac(device=name, mac=mac, port=port, vlan=vlan,
                                  kind=(r.get("type") or "").strip().lower() or None))  # fmt: skip
    arps: list[NormalizedArp] = []
    for r in p.get(arp_cmd) or []:
        mac, ip = mac_text(r.get("mac_address")), _ip(r.get("ip_address"))
        if not mac or not ip or (r.get("age") or "").strip() == "-":  # eigene Adresse (IOS)
            continue
        arps.append(NormalizedArp(device=name, ip=ip, mac=mac,
                                  interface=short_name(r.get("interface")) or None,
                                  vrf=_text(r.get("vrf"))))  # fmt: skip
    over = max(len(macs) - MAX_MACS, 0)
    return macs[:MAX_MACS], arps, over


def neighbors6(platform: str, name: str, outputs: dict[str, str]) -> list[NormalizedArp]:
    """IPv6-Nachbarn (0.88.x) von IOS/IOS-XE (ntc-Vorlage, sonst eigene Auswertung) bzw. NX-OS
    (eigene Auswertung, die Vorlagen kennen den Befehl nicht)."""
    cmd = nd.NXOS if platform == "nxos" else nd.IOS
    text = outputs.get(cmd, "")
    if not text.strip() or cli_error(text):
        return []
    if platform == "nxos":
        rows = nd.nxos(text)
    else:
        try:
            rows = [{"address": r.get("address"), "mac": r.get("mac_address"),
                     "interface": r.get("interface"), "state": r.get("type")}
                    for r in parse(platform, cmd, text)]  # fmt: skip
        except Exception:  # noqa: BLE001 - Vorlage passt nicht: gleiche Spalten selbst lesen
            rows = nd.table(text)
    for r in rows:
        r["interface"] = short_name(r.get("interface")) or None
    return nd.entries(name, rows)


def switch_inventory(
    host: str, platform: str, outputs: dict[str, str], notes: list[str] | None = None
) -> NetworkInventory:
    """Ein Switch: Ausgaben der Befehle (`COMMANDS`) -> Netzinventar; Hinweise nach `notes`."""
    from collectors.cisco_switch import arista, aruba, cbs, junos
    from collectors.cisco_switch.client import COMMANDS

    if platform == "cbs":
        return cbs.inventory(host, outputs, notes)
    if platform == "junos":
        return junos.inventory(host, outputs, notes)
    if platform == "eos":  # 0.88.x
        return arista.inventory(host, outputs, notes)
    if platform == "aoscx":  # 0.88.x
        return aruba.inventory(host, outputs, notes)
    cmds = COMMANDS[platform]
    p: dict[str, list[dict]] = {}
    for c in cmds:
        if c in nd.COMMANDS:  # IPv6-Nachbarn: eigene Auswertung unten (0.88.x)
            continue
        try:
            p[c] = parse(platform, c, outputs.get(c, ""))
        except Exception as exc:  # noqa: BLE001 - Vorlage passt nicht: Teil leer, Hinweis im Lauf
            p[c] = []
            if notes is not None:
                notes.append(f"{c}: Ausgabe nicht auswertbar ({type(exc).__name__})")
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
    if nx:  # vPC (0.65.0 selbst ausgewertet): Peer-Link und je vPC Status/Konsistenz
        vpc = vpc_info(outputs.get("show vpc", ""))
        if vpc["peer_link"]:
            port(vpc["peer_link"]).update(
                vpc_peer_link=vpc["domain"] or "?", vpc_role=vpc["role"],
                vpc_peer=vpc["peer"], vpc_keepalive=vpc["keepalive"],
                vpc_peer_link_status=vpc["peer_link_status"])  # fmt: skip
        for v in vpc["vpcs"]:
            port(v["port"]).update(vpc=v["id"], vpc_status=v["status"],
                                   vpc_consistency=v["consistency"])  # fmt: skip
    for key, st in _stp(p.get("show spanning-tree") or []).items():
        if key in ports:
            if st["blocked"]:
                ports[key]["stp_blocked"] = vlan_ranges(st["blocked"])
            if st["root"]:
                ports[key]["stp_root"] = vlan_ranges(st["root"])

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
    macs, arps, over = _l2(name, p, next(c for c in cmds if c.startswith("show ip arp")))
    arps += neighbors6(platform, name, outputs)
    if over and notes is not None:
        notes.append(f"MAC-Tabelle: {over} Eintraege ueber {MAX_MACS} nicht uebernommen")
    return NetworkInventory(devices=[device], interfaces=interfaces, vlans=vlans,
                            neighbors=neighbors, macs=macs, arps=arps)  # fmt: skip


_ATTRS = (
    "media", "vpc", "vpc_status", "vpc_consistency", "vpc_peer_link", "vpc_role", "vpc_peer",
    "vpc_keepalive", "vpc_peer_link_status", "stp_blocked", "stp_root",
)  # fmt: skip


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
    attrs = {k: e[k] for k in _ATTRS if e.get(k)}
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
        part = switch_inventory(sw["host"], sw["platform"], sw.get("outputs") or {},
                                sw.setdefault("notes", []))  # fmt: skip
        inv.devices += part.devices
        inv.interfaces += part.interfaces
        inv.neighbors += part.neighbors
        inv.macs += part.macs
        inv.arps += part.arps
        inv.subnets += part.subnets  # Junos: Netze der Interface-Adressen (0.68.0)
        for v in part.vlans:  # VLAN-ID je Quellsystem einmal (erster Name)
            seen_vlans.setdefault(v.vid, v)
    inv.vlans = sorted(seen_vlans.values(), key=lambda v: v.vid)
    return inv
