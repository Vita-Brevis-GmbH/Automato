"""Cisco Business-CLI (Catalyst 1200/1300, CBS 250/350) -> Netzinventar (0.68.0, ADR 0025).

Die Ausgaben sind Tabellen mit Strichzeile unter dem Kopf; lange Port-Listen bzw. Namen laufen in
Folgezeilen weiter (die Vorlagen von ntc-templates fuer `cisco_s300` kennen das nicht). Darum hier
eine eigene Auswertung: Spalten nach der Strichzeile, jedes Wort in die Spalte, ueber der es steht;
Zeilen ohne erste Spalte gehoeren zur Zeile davor. Portnamen wie im Rest (`Gi1/0/1`, `Te1/0/1`,
`Po1`). VLANs der Ports aus `show vlan` (getaggt bzw. ungetaggt je VLAN). Seit 0.94.0 die
IPv6-Nachbarn aus `show ipv6 neighbors all` (gleiche Tabellenform).

Seit 1.23.0 (Anwender 2026-11-20, Catalyst 1300 mit Firmware 4.1):
- **Spanning Tree** aus `show spanning-tree` und `show spanning-tree detail` (`stp_tree`,
  `stp_detail`): Modus (`Spanning tree enabled mode: Rapid-PVST` bzw. PVST, MSTP, RSTP, STP), je
  VLAN bzw. MST-Instanz Root-ID und Bridge-ID (`Priority:`, `Address:`, `Path Cost:`,
  `Root Port:`) und die Port-Tabelle `Name State Prio.Nbr Cost Sts Role PortFast Type`;
  Port-Channels sind Ports. RSTP/STP ohne VLAN-Abschnitt = eine Instanz fuer alle VLANs (wie
  MST0). Topologieaenderungen aus `detail` (Zahl; das Alter nur in bekannten Formen).
- **ARP**: die erste Spalte `VLAN Interface` traegt beides (`vlan 10    gi1/0/22`) - Interface
  ist das VLAN (`Vl10`), nicht der Port.
- **Ports** nur aus Zeilen mit Port: eine Tabelle ohne Spalte `Port`/`Ch` liefert keinen Port
  (vorher ein Port ohne Namen).
- **Rechte**: im User EXEC (Prompt `>`, Stufe 1) kennt die CLI MAC-Tabelle, ARP und Spanning Tree
  nicht („Unrecognized command“) - der Hinweis nennt das statt „Plattform pruefen“ (`rights`).
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
    NormalizedStpPort,
    NormalizedVlan,
)
from collectors.common.i18n import L

_DASHES = re.compile(r"^\s*-{2,}(?:\s+-{2,})*\s*$")
_PREFIX = {"gi": "Gi", "te": "Te", "fa": "Fa", "tw": "Tw", "fo": "Fo", "po": "Po", "ch": "Po",
           "oob": "oob", "vlan": "Vl"}  # fmt: skip
_TAGGED_MAX = 512


def port_name(raw: str | None) -> str:
    """`gi1/0/1` -> `Gi1/0/1`, `Po1`/`ch1` -> `Po1`, `vlan 10` -> `Vl10`."""
    text = re.sub(r"\s+", "", raw or "")
    m = re.match(r"([A-Za-z]+)(\d.*)?$", text)
    if not m:
        return text
    prefix = _PREFIX.get(m.group(1).lower(), m.group(1))
    return prefix + (m.group(2) or "")


def expand_ports(text: str | None) -> list[str]:
    """`gi1/0/2-4,te1/0/1, Po2-3` -> einzelne Ports (Kurzform)."""
    out: list[str] = []
    for part in re.split(r"[,\s]+", text or ""):
        m = re.match(r"([A-Za-z]+)((?:\d+/)*)(\d+)(?:-(?:[A-Za-z]+(?:\d+/)*)?(\d+))?$", part)
        if not m:
            if part:
                out.append(port_name(part))
            continue
        prefix, path, lo = m.group(1), m.group(2), int(m.group(3))
        hi = int(m.group(4)) if m.group(4) else lo
        out += [port_name(f"{prefix}{path}{n}") for n in range(lo, min(hi, lo + 1024) + 1)]
    return list(dict.fromkeys(out))


def tables(text: str) -> list[list[dict[str, str]]]:
    """Alle Tabellen einer Ausgabe: Kopf (eine oder zwei Zeilen) ueber der Strichzeile, Spalten
    nach den Strichen; Folgezeilen (erste Spalte leer) haengen an die Zeile davor."""
    lines = (text or "").replace("\r", "").split("\n")
    out: list[list[dict[str, str]]] = []
    for i, line in enumerate(lines):
        if not _DASHES.match(line) or i == 0:
            continue
        spans = [(m.start(), m.end()) for m in re.finditer(r"-+", line)]
        upper = lines[i - 2] if i > 1 else ""
        # zweite Kopfzeile nur ohne Doppelpunkt (sonst ist es Text ueber der Tabelle)
        heads = [lines[i - 1]] + ([upper] if upper.strip() and ":" not in upper
                                  and not _DASHES.match(upper) else [])  # fmt: skip
        names = [[] for _ in spans]
        for head in reversed(heads):
            for word, col in _words(head, spans):
                names[col].append(word)
        keys = [" ".join(n).lower() or f"col{k}" for k, n in enumerate(names)]
        rows: list[dict[str, str]] = []
        for row_line in lines[i + 1 :]:
            if not row_line.strip() or _DASHES.match(row_line):
                break
            cells: dict[int, list[str]] = {}
            for word, col in _words(row_line, spans):
                cells.setdefault(col, []).append(word)
            if 0 not in cells and rows:  # Folgezeile
                for col, words in cells.items():
                    key, before = keys[col], rows[-1].get(keys[col], "")
                    glue = "" if not before or before[-1] in ".,-_/" else " "
                    rows[-1][key] = before + glue + " ".join(words)
                continue
            if 0 not in cells:
                continue
            rows.append({keys[c]: " ".join(w) for c, w in sorted(cells.items())})
        if rows:
            out.append(rows)
    return out


def _words(line: str, spans: list[tuple[int, int]]):
    """Woerter einer Zeile mit der Spalte, ueber der sie (am meisten) stehen."""
    for m in re.finditer(r"\S+", line):
        a, b = m.start(), m.end()
        best = max(range(len(spans)),
                   key=lambda k: (min(b, spans[k][1]) - max(a, spans[k][0]),
                                  -abs((a + b) / 2 - (spans[k][0] + spans[k][1]) / 2)))  # fmt: skip
        yield m.group(), best


def _fields(text: str) -> dict[str, str]:
    """`Schluessel:   Wert` je Zeile (show system)."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and key.strip().lower() not in out:
            out[key.strip().lower()] = value.strip()
    return out


def rights(error: str) -> str:
    """Zusatz zur Fehlermeldung eines Befehls, wenn das Konto im User EXEC steht (Prompt `>`)
    und die CLI den Befehl dort nicht kennt (1.23.0) - sonst leer."""
    if "unrecognized command" not in (error or "").lower():
        return ""
    return L(
        " – im User EXEC (Prompt „>“, Stufe 1) nicht verfügbar: das Lesekonto muss nach der "
        "Anmeldung im Privileged EXEC stehen (Prompt „#“)",
        " – not available in User EXEC (prompt “>”, level 1): the read account must be in "
        "Privileged EXEC after login (prompt “#”)",
    )


def rights_short() -> str:
    """Zusatz fuer „Verbindung pruefen“ (1.23.0): Konto im User EXEC."""
    return L(
        ", Stufe 1 (Prompt „>“): MAC-Tabelle, ARP und Spanning Tree nicht lesbar",
        ", level 1 (prompt “>”): MAC table, ARP and Spanning Tree not readable",
    )


def _col(row: dict, *names: str) -> str:
    for n in names:
        for k, v in row.items():
            if k == n or k.startswith(n):
                return (v or "").strip()
    return ""


def _mac(value: str) -> str | None:
    digits = re.sub(r"[^0-9a-fA-F]", "", value or "")
    if len(digits) != 12:
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2)).lower()


def _ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def inventory(
    host: str, outputs: dict[str, str], notes: list[str] | None = None
) -> NetworkInventory:
    """Ein Switch mit Business-CLI: Ausgaben der Befehle -> Netzinventar."""
    system = _fields(outputs.get("show system", ""))
    version = _fields(outputs.get("show version", ""))
    inv_text = outputs.get("show inventory", "")
    pid = re.search(r"PID:\s*(\S+)", inv_text)
    sn = re.search(r"SN:\s*(\S+)", inv_text)
    name = system.get("system name") or host
    desc = system.get("system description") or ""
    m = re.search(r"\b(Catalyst \d{4}[\w-]*|C\d{4}[A-Z]?-[\w-]+|CBS\d{3}[\w-]*)", desc)
    model = pid.group(1) if pid else (m.group(1) if m else desc[:40] or None)
    device = NormalizedDevice(name=name, role="switch", model=model,
                              serial=sn.group(1) if sn else None,
                              version=version.get("version") or version.get("sw version"),
                              mgmt_ip=_ip(host))  # fmt: skip
    ports: dict[str, dict] = {}

    def port(raw: str) -> dict:
        key = port_name(raw)
        if not key:  # Zeile ohne Port (1.23.0): kein Port ohne Namen
            return {}
        return ports.setdefault(key, {"name": key})

    for table in tables(outputs.get("show interfaces status", "")):
        for r in table:
            p = port(_col(r, "port", "ch"))
            state = _col(r, "link state", "state").lower()
            p.update(status={"up": "up", "down": "down"}.get(state, state or None),
                     duplex=_text(_col(r, "duplex")), speed=_text(_col(r, "speed")),
                     media=_text(_col(r, "type")))  # fmt: skip
    for table in tables(outputs.get("show interfaces description", "")):
        for r in table:
            p = port(_col(r, "port", "ch"))
            p["description"] = _text(_col(r, "description"))
    members: dict[str, str] = {}
    for line in (outputs.get("show interfaces port-channel", "") or "").splitlines():
        m = re.match(r"\s*(Po\d+|ch\d+)\s+(.*)$", line, re.I)
        if not m:
            continue
        bundle = port(m.group(1))["name"]
        lists = re.findall(r"(?:In)?active\s*:\s*(.*?)(?=(?:In)?active\s*:|$)", m.group(2), re.I)
        for part in lists:
            for member in expand_ports(part):
                members[member] = bundle
    for member, bundle in members.items():
        port(member)["lag"] = bundle
    vlans: list[NormalizedVlan] = []
    for table in tables(outputs.get("show vlan", "")):
        for r in table:
            vid = _int(_col(r, "vlan"))
            if vid is None:
                continue
            vlans.append(NormalizedVlan(vid=vid, name=_col(r, "name") or str(vid)))
            for p in expand_ports(_col(r, "tagged ports", "tagged")):
                port(p).setdefault("tagged", set()).add(vid)
            for p in expand_ports(_col(r, "untagged ports", "untagged")):
                port(p)["untagged"] = vid
    neighbors: list[NormalizedNeighbor] = []
    for table in tables(outputs.get("show lldp neighbors", "")):
        for r in table:
            local = port_name(_col(r, "port"))
            if not local:
                continue
            caps = [c for c in re.split(r"[\s,]+", _col(r, "capabilities")) if c]
            neighbors.append(NormalizedNeighbor(
                device=name, port=local, protocol="lldp",
                remote_name=_col(r, "system name") or _col(r, "device id") or "?",
                remote_port=_col(r, "port id") or None, remote_caps=caps,
                remote_chassis=_col(r, "device id")[:100] or None,  # 1.23.0: Chassis-ID
            ))  # fmt: skip
    first = {n.port: n for n in reversed(neighbors)}
    for key, st in stp_ports(outputs.get("show spanning-tree", "")).items():  # 1.23.0
        if key in ports:
            if st["blocked"]:
                ports[key]["stp_blocked"] = _ranges(st["blocked"])
            if st["root"]:
                ports[key]["stp_root"] = _ranges(st["root"])
    interfaces = [_interface(name, e, first.get(e["name"])) for e in ports.values()]
    macs: list[NormalizedMac] = []
    for table in tables(outputs.get("show mac address-table", "")):
        for r in table:
            mac, raw_port = _mac(_col(r, "mac address", "mac")), _col(r, "port")
            kind = _col(r, "type").lower()
            if not mac or not re.match(r"[A-Za-z]", raw_port) or kind == "self":
                continue
            if int(mac[:2], 16) & 1:
                continue
            macs.append(NormalizedMac(device=name, mac=mac, port=port_name(raw_port),
                                      vlan=_int(_col(r, "vlan")), kind=kind or None))  # fmt: skip
    arps: list[NormalizedArp] = []
    for table in tables(outputs.get("show arp", "")):
        for r in table:
            mac, ip = _mac(_col(r, "hw address", "mac")), _ip(_col(r, "ip address", "ip"))
            if mac and ip:
                arps.append(NormalizedArp(device=name, ip=ip, mac=mac,
                                          interface=_arp_interface(_col(r, "vlan"))))  # fmt: skip
    rows = []  # IPv6-Nachbarn (0.94.0): Interface, IPv6-Adresse, HW-Adresse, Zustand
    for table in tables(outputs.get(nd.CBS, "")):
        for r in table:
            iface = _col(r, "interface").replace(" ", "")
            rows.append({"address": _col(r, "ipv6 address", "ipv6", "address"),
                         "mac": _col(r, "hw address", "mac"), "state": _col(r, "state"),
                         "interface": port_name(iface) if iface else None})  # fmt: skip
    arps += nd.entries(name, rows)
    return NetworkInventory(devices=[device], interfaces=interfaces, vlans=vlans,
                            neighbors=neighbors, macs=macs, arps=arps)  # fmt: skip


def _arp_interface(text: str) -> str | None:
    """Erste Spalte von `show arp`: `vlan 10` bzw. (Firmware 4.x) `vlan 10 gi1/0/22` - das
    L3-Interface ist das VLAN (`Vl10`); ohne VLAN der Port bzw. das Interface wie gemeldet."""
    m = re.match(r"\s*vlan\s*(\d+)", text or "", re.I)
    if m:
        return f"Vl{m.group(1)}"
    word = (text or "").split()
    return port_name(word[0]) if word else None


def _ranges(vlans: set[int]) -> str:
    from collectors.cisco_switch.normalize import vlan_ranges

    return vlan_ranges(vlans)


def _text(value: str | None) -> str | None:
    text = (value or "").strip()
    return None if not text or text in ("--", "-") else text


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _kind(name: str) -> str:
    low = name.lower()
    if low.startswith("po"):
        return "lag"
    if low.startswith("vl"):
        return "vlan"
    return "physical"


def _interface(device: str, e: dict, nb: NormalizedNeighbor | None) -> NormalizedInterface:
    from collectors.cisco_switch.normalize import vlan_ranges

    tagged = sorted(e.get("tagged") or [])
    untagged = e.get("untagged")
    mode = "tagged" if tagged else ("access" if untagged is not None else None)
    attrs = {k: e[k] for k in ("media", "stp_blocked", "stp_root") if e.get(k)}
    return NormalizedInterface(
        device=device, name=e["name"], kind=_kind(e["name"]),
        enabled=None if e.get("status") is None else e.get("status") != "disabled",
        mode=mode, untagged_vlan=untagged,
        tagged_vlans=tagged if len(tagged) <= _TAGGED_MAX else [],
        lag=e.get("lag"), description=e.get("description"),
        neighbor=nb.remote_name if nb else None, neighbor_port=nb.remote_port if nb else None,
        status=e.get("status"), speed=e.get("speed"), duplex=e.get("duplex"),
        native_vlan=untagged if mode == "tagged" else None,
        trunk_vlans=vlan_ranges(set(tagged)) if tagged else None,
        attrs=attrs or None,
    )  # fmt: skip


# -- Spanning Tree (1.23.0) -----------------------------------------------------------------

# `Spanning tree enabled mode:` -> (Protokoll wie bei IOS, Modus der Ansicht)
_STP_MODES = {"rapid-pvst": ("rstp", "rapid-pvst"), "pvst": ("ieee", "pvst"),
              "mstp": ("mstp", "mst"), "rstp": ("rstp", "rstp"),
              "stp": ("ieee", "stp")}  # fmt: skip
_STP_ROLES = {"root": "root", "desg": "designated", "altn": "alternate", "back": "backup",
              "bkup": "backup", "mstr": "master", "dsbl": "disabled",
              "disb": "disabled"}  # fmt: skip
_STP_STATES = {"frw": "forwarding", "fwd": "forwarding", "blk": "blocking", "lrn": "learning",
               "lis": "listening", "dsbl": "disabled", "dis": "disabled",
               "dsc": "discarding"}  # fmt: skip
_S_MODE = re.compile(r"Spanning tree enabled mode:\s*(\S+)", re.I)
_S_OFF = re.compile(r"Spanning tree (?:is )?disabled", re.I)
_S_VLAN = re.compile(r"^\s*VLAN\s*0*(\d+)\s*$", re.I)
_S_MST = re.compile(r"^[\s#]*MST\s*0*(\d+)\b(?:.*?Vlans?\s+Mapped:\s*(\S+))?", re.I)
_S_BLOCK = re.compile(r"^\s*(?:CST\s+|IST\s+)?(Root|Bridge|Regional Root)\s+ID\b", re.I)
_S_PRIO = re.compile(r"\bPriority\s*:?\s*(\d+)", re.I)
_S_ADDR = re.compile(r"\bAddress\s*:?\s*([0-9A-Fa-f]{2}(?:[:.-]?[0-9A-Fa-f]{2}){5})\b")
_S_COST = re.compile(r"\b(?:Path\s+|Root\s+Path\s+)?Cost\s*:?\s*(\d+)\b", re.I)
_S_RPORT = re.compile(r"\bRoot\s+Port\s*:?\s*([A-Za-z][\w/.-]*)", re.I)
_S_SELF = re.compile(r"This (?:switch|bridge) is the root", re.I)
_S_TC = re.compile(r"Number of topology changes\s*:?\s*(\d+)"
                   r"(?:\s+last change occurred\s*:?\s*(.+?)(?:\s+ago)?\s*$)?", re.I)  # fmt: skip


def _stp_mode(text: str) -> tuple[str | None, str | None]:
    m = _S_MODE.search(text or "")
    return _STP_MODES.get(m.group(1).lower(), (m.group(1).lower(), m.group(1).lower())) \
        if m else (None, None)  # fmt: skip


def _stp_sections(text: str) -> list[tuple[str, int, str | None, list[str]]]:
    """Abschnitte je Instanz: (Art, Nummer, VLANs der MST-Instanz, Zeilen). RSTP/STP ohne
    Abschnitt: eine Instanz `mst 0` ab der ersten Root-ID (ein Baum fuer alle VLANs)."""
    out: list[tuple[str, int, str | None, list[str]]] = []
    for line in (text or "").replace("\r", "").splitlines():
        v, m = _S_VLAN.match(line), _S_MST.match(line)
        if v:
            out.append(("vlan", int(v.group(1)), None, []))
        elif m and not _S_BLOCK.match(line):
            out.append(("mst", int(m.group(1)), m.group(2), []))
        elif not out and _S_BLOCK.match(line):
            out.append(("mst", 0, "1-4094", [line]))
        elif out:
            out[-1][3].append(line)
    return out


def _stp_rows(lines: list[str]) -> list[NormalizedStpPort]:
    """Port-Tabelle `Name State Prio.Nbr Cost Sts Role PortFast Type` eines Abschnitts.
    `*` markiert eine Inkonsistenz („Port Type or PVID Inconsistency“); PortFast `Yes` = Edge."""
    out: list[NormalizedStpPort] = []
    for table in tables("\n".join(lines)):
        for r in table:
            if "prio.nbr" not in r and "sts" not in r:
                continue
            raw = " ".join(r.values())
            name = port_name(_col(r, "name").lstrip("*"))
            if not name or not re.match(r"[A-Za-z]", name):
                continue
            cost = _int(_col(r, "cost"))
            ptype = re.sub(r"\s+", " ", _col(r, "type").replace("*", "")).strip()
            if ptype in ("-", "--"):
                ptype = ""
            if _col(r, "portfast").lower() == "yes":  # `P2P (rpvst)` -> `P2P Edge (rpvst)`
                head, paren, rest = ptype.partition("(")
                ptype = re.sub(r"\s+", " ", f"{head.strip()} Edge {paren}{rest}").strip()
            role, sts = _col(r, "role").lower(), _col(r, "sts").lower()
            out.append(NormalizedStpPort(
                port=name, role=_STP_ROLES.get(role, role or None),
                state=_STP_STATES.get(sts, sts or None), cost=cost,
                priority=_col(r, "prio.nbr") or None, type=ptype or None,
                inconsistent="TYPE/PVID_Inc" if "*" in raw else None,
            ))  # fmt: skip
    return out


def stp_tree(text: str) -> list[dict]:
    """`show spanning-tree` -> je Instanz {kind, number, protocol, mode, vlans, root_*,
    bridge_*, is_root, ports} wie `stp.parse_tree`."""
    protocol, mode = _stp_mode(text)
    out: list[dict] = []
    for kind, number, vlans, lines in _stp_sections(text):
        cur = {"kind": kind, "number": number, "protocol": protocol, "mode": mode,
               "vlans": vlans, "is_root": False, "root_priority": None, "root_mac": None,
               "root_cost": None, "root_port": None, "bridge_priority": None,
               "bridge_mac": None, "ports": _stp_rows(lines)}  # fmt: skip
        block = None
        for line in lines:
            b = _S_BLOCK.match(line)
            if b:
                block = "root" if b.group(1).lower() in ("root", "regional root") else "bridge"
            if _DASHES.match(line) or re.match(r"^\s*Name\s+State\b", line, re.I):
                block = None  # ab hier die Port-Tabelle
            if block is None:
                continue
            if _S_SELF.search(line):
                cur["is_root"] = True
            m = _S_PRIO.search(line)
            if m and cur[f"{block}_priority"] is None:
                cur[f"{block}_priority"] = int(m.group(1))
            m = _S_ADDR.search(line)
            if m and cur[f"{block}_mac"] is None:
                cur[f"{block}_mac"] = _mac(m.group(1))
            if block == "root":
                m = _S_RPORT.search(line)
                if m:
                    cur["root_port"] = port_name(m.group(1))
                m = _S_COST.search(line)
                if m and cur["root_cost"] is None:
                    cur["root_cost"] = int(m.group(1))
        if cur["root_mac"] and cur["root_mac"] == cur["bridge_mac"]:
            cur["is_root"] = True
        if cur["is_root"]:
            cur["bridge_mac"] = cur["bridge_mac"] or cur["root_mac"]
            cur["bridge_priority"] = cur["bridge_priority"] or cur["root_priority"]
            cur["root_cost"], cur["root_port"] = 0, None
        out.append(cur)
    return out


def stp_detail(text: str) -> dict[tuple[str, int], dict]:
    """`show spanning-tree detail` -> je Instanz die Topologieaenderungen (Zahl; Alter in
    Sekunden nur in bekannter Form wie `2d18h` bzw. `00:01:52`, sonst None - die Firmware 4.1
    meldet z. B. `01010145`, dessen Einheit nicht belegt ist)."""
    from collectors.cisco_switch.stp import seconds

    out: dict[tuple[str, int], dict] = {}
    for kind, number, _vlans, lines in _stp_sections(text):
        for line in lines:
            m = _S_TC.search(line)
            if m:
                ago = (m.group(2) or "").strip()
                out[(kind, number)] = {"tc_count": int(m.group(1)),
                                       "tc_ago": seconds(ago) if ago else None}  # fmt: skip
                break
    return out


def stp_ports(text: str) -> dict[str, dict[str, set[int]]]:
    """Je Port die VLANs, in denen er blockiert bzw. Root-Port ist (Rapid-PVST/PVST; wie die
    `attrs.stp_blocked`/`stp_root` der IOS-Switches)."""
    out: dict[str, dict[str, set[int]]] = {}
    for t in stp_tree(text):
        if t["kind"] != "vlan":
            continue
        for p in t["ports"]:
            entry = out.setdefault(p.port, {"blocked": set(), "root": set()})
            if p.state == "blocking" or (p.role in ("alternate", "backup")
                                         and p.state != "forwarding"):  # fmt: skip
                entry["blocked"].add(t["number"])
            if p.role == "root":
                entry["root"].add(t["number"])
    return out
