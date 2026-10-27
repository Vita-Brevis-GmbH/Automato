"""Cisco Business-CLI (Catalyst 1200/1300, CBS 250/350) -> Netzinventar (0.68.0, ADR 0025).

Die Ausgaben sind Tabellen mit Strichzeile unter dem Kopf; lange Port-Listen bzw. Namen laufen in
Folgezeilen weiter (die Vorlagen von ntc-templates fuer `cisco_s300` kennen das nicht). Darum hier
eine eigene Auswertung: Spalten nach der Strichzeile, jedes Wort in die Spalte, ueber der es steht;
Zeilen ohne erste Spalte gehoeren zur Zeile davor. Portnamen wie im Rest (`Gi1/0/1`, `Te1/0/1`,
`Po1`). VLANs der Ports aus `show vlan` (getaggt bzw. ungetaggt je VLAN). Seit 0.88.x die
IPv6-Nachbarn aus `show ipv6 neighbors all` (gleiche Tabellenform).
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
            ))  # fmt: skip
    first = {n.port: n for n in reversed(neighbors)}
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
                vlan = _col(r, "vlan").replace(" ", "")
                arps.append(NormalizedArp(device=name, ip=ip, mac=mac,
                                          interface=port_name(vlan) if vlan else None))  # fmt: skip
    rows = []  # IPv6-Nachbarn (0.88.x): Interface, IPv6-Adresse, HW-Adresse, Zustand
    for table in tables(outputs.get(nd.CBS, "")):
        for r in table:
            iface = _col(r, "interface").replace(" ", "")
            rows.append({"address": _col(r, "ipv6 address", "ipv6", "address"),
                         "mac": _col(r, "hw address", "mac"), "state": _col(r, "state"),
                         "interface": port_name(iface) if iface else None})  # fmt: skip
    arps += nd.entries(name, rows)
    return NetworkInventory(devices=[device], interfaces=interfaces, vlans=vlans,
                            neighbors=neighbors, macs=macs, arps=arps)  # fmt: skip


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
        attrs={"media": e["media"]} if e.get("media") else None,
    )  # fmt: skip
