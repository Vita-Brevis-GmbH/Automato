"""IPv6-Nachbarn (NDP) der Switches und Router (0.88.x, ADR 0025 Nr. 13).

Wie ARP: je Eintrag IPv6-Adresse -> MAC mit L3-Interface (und VRF). Uebernommen werden nur
Unicast-Adressen mit bekannter MAC (nicht Multicast, Unspezifiziert, Loopback, unvollstaendige
Eintraege); die Zone (`fe80::1%Vlan10`) faellt weg. Link-local-Adressen (`fe80::/10`) bleiben
erhalten - der Orchestrator kennzeichnet sie und nimmt sie nicht als Identitaet eines Geraets
(sie gelten nur am Link, viele Router nutzen dieselbe).

Auswertung je Plattform: IOS/IOS-XE mit der ntc-Vorlage `cisco_ios_show_ipv6_neighbors` (sonst
`table`), NX-OS, AOS-CX und weitere Tabellen mit `table` (NX-OS bricht lange Adressen in eine
eigene Zeile um, Abschnitte je VRF), NX-OS-Detailform mit `detail`, Junos/Arista aus JSON in
ihren Modulen, Catalyst 1200/1300 mit `cbs.tables`.
"""

import ipaddress
import re

from collectors.common.dto import NormalizedArp

# Befehle je Plattform (Positivliste in `client.COMMANDS`); fehlt einer (alte Software, IPv6
# aus), bleibt der Teil ohne Hinweis leer
IOS = "show ipv6 neighbors"
NXOS = "show ipv6 neighbor vrf all"
CBS = "show ipv6 neighbors all"
JUNOS = "show ipv6 neighbors | display json | no-more"
EOS = "show ipv6 neighbors vrf all | json"
AOSCX = "show ipv6 neighbors all-vrfs"
COMMANDS = frozenset({IOS, NXOS, CBS, JUNOS, EOS, AOSCX})

# Zustaende ohne gueltige Zuordnung IP -> MAC
_INVALID = {"incmp", "incomplete", "failed", "fail", "none"}
# Spalten aus zwei Woertern im Kopf der Tabelle
_PAIRS = {
    ("ipv6", "address"),
    ("mac", "address"),
    ("link-layer", "addr"),
    ("hw", "address"),
    ("physical", "interface"),
    ("physical", "port"),
    ("mobility", "flags"),
}
_VRF = re.compile(r"(?:Table|Adjacency Table)\s+for\s+(?:VRF|context)\s+(\S+)", re.I)


def address(value) -> str | None:
    """IPv6-Unicast-Adresse ohne Zone, sonst None (auch fuer IPv4)."""
    text = (value if isinstance(value, str) else "").strip().split("%", 1)[0].split("/", 1)[0]
    try:
        ip = ipaddress.IPv6Address(text)
    except ValueError:
        return None
    if ip.is_multicast or ip.is_unspecified or ip.is_loopback:
        return None
    return str(ip)


def link_local(ip: str | None) -> bool:
    try:
        return ipaddress.ip_address((ip or "").split("%", 1)[0]).is_link_local
    except ValueError:
        return False


def entries(device: str, rows) -> list[NormalizedArp]:
    """Zeilen {address, mac, interface, vrf, state} -> Eintraege (ohne Doppelte)."""
    from collectors.cisco_switch.normalize import mac_text

    out: list[NormalizedArp] = []
    seen: set[tuple] = set()
    for r in rows:
        ip, mac = address(r.get("address")), mac_text(r.get("mac"))
        state = (r.get("state") or "").strip().lower()
        if not ip or not mac or int(mac[:2], 16) & 1 or state in _INVALID:
            continue
        vrf = (r.get("vrf") or "").strip() or None
        if vrf == "default":
            vrf = None
        iface = (r.get("interface") or "").strip() or None
        key = (ip, mac, iface, vrf)
        if key in seen:
            continue
        seen.add(key)
        out.append(NormalizedArp(device=device, ip=ip, mac=mac, interface=iface, vrf=vrf))
    return out


def _columns(header: str) -> list[str]:
    """Kopfzeile -> Spaltennamen (klein); `MAC Address`, `Physical Interface` je eine Spalte."""
    words = header.lower().split()
    out: list[str] = []
    i = 0
    while i < len(words):
        if i + 1 < len(words) and (words[i], words[i + 1]) in _PAIRS:
            out.append(f"{words[i]} {words[i + 1]}")
            i += 2
        else:
            out.append(words[i])
            i += 1
    return out


def _pick(row: dict, *names: str) -> str | None:
    for n in names:
        for key, value in row.items():
            if key == n or key.startswith(n):
                return value
    return None


def table(text: str) -> list[dict]:
    """Tabelle der IPv6-Nachbarn (NX-OS, AOS-CX, IOS ohne Vorlage): Kopf mit Adresse und MAC,
    je Zeile ein Wort je Spalte; eine Adresse allein in der Zeile gehoert zur naechsten Zeile
    (NX-OS bei langen Adressen). „… Table for VRF x“ setzt die VRF der folgenden Zeilen."""
    cols: list[str] = []
    vrf: str | None = None
    pending: str | None = None
    out: list[dict] = []
    for line in (text or "").replace("\r", "").splitlines():
        m = _VRF.search(line)
        if m:
            vrf, cols, pending = m.group(1), [], None
            continue
        words = line.split()
        if not words:
            continue
        low = line.lower()
        if not cols and "address" in low and re.search(r"\b(mac|link-layer|hw)\b", low):
            cols = _columns(line)
            continue
        if not cols:
            continue
        if address(words[0]) and len(words) == 1:
            pending = words[0]  # Adresse umgebrochen: Rest in der naechsten Zeile
            continue
        if pending and line[:1].isspace():
            words = [pending, *words]
        pending = None
        if not address(words[0]):
            continue
        row = dict(zip(cols, words, strict=False))
        out.append({
            "address": words[0],
            "mac": _pick(row, "mac", "link-layer", "hw"),
            "interface": _pick(row, "interface", "port"),
            "state": _pick(row, "state"),
            "vrf": _pick(row, "vrf") or vrf,
        })  # fmt: skip
    return out


def detail(text: str) -> list[dict]:
    """NX-OS-Detailform (`Address : …`, darunter `MacAddr : …`, `Interface : …`)."""
    out: list[dict] = []
    vrf: str | None = None
    cur: dict | None = None
    for line in (text or "").replace("\r", "").splitlines():
        m = _VRF.search(line)
        if m:
            vrf = m.group(1)
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "address" and address(value):
            cur = {"address": value, "vrf": vrf}
            out.append(cur)
        elif cur is not None and key in ("macaddr", "mac address", "mac"):
            cur["mac"] = value
        elif cur is not None and key == "interface":
            cur["interface"] = value
    return out


def nxos(text: str) -> list[dict]:
    """NX-OS: Tabelle bzw. Detailform."""
    if re.search(r"^\s*Address\s*:\s*\S", text or "", re.M):
        return detail(text)
    return table(text)
