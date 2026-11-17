"""Transceiver je Port der Switches (1.19.0, ADR 0025 Nachtrag, ADR 0031 Nachtrag), nur lesend.

Anwender 2026-11-17 („Verkabelung im Detail“): je Port Typ, Seriennummer und - wo das Geraet ihn
nennt - Hersteller bzw. Teilenummer der steckbaren Optik (SFP, SFP+, QSFP …). Nur `show`-Befehle,
die es auf der Plattform gibt:

- Cisco IOS/IOS-XE: `show inventory` (NAME = Port, DESCR = Typ, PID = Teilenummer, SN);
  Router mit `subslot x/y transceiver z` werden dem Port `…x/y/z` zugeordnet.
- Cisco NX-OS: `show interface transceiver` (Typ, Hersteller `name`, Teilenummer, Seriennummer,
  Cisco-PID).
- Catalyst 1200/1300 (Business-CLI): `show inventory` - liest der Switch ohnehin.
- Juniper Junos: `show chassis hardware` (Xcvr je FPC/PIC) - liest der Switch ohnehin.
- Arista EOS: `show inventory | json` (`xcvrSlots`).
- Aruba CX: `show interface transceiver`.
- SNMPv3 (ADR 0033): nicht unterstuetzt - die Entity-MIB ordnet Optiken nur ueber Umwege den
  Ports zu; der Switch steht dann mit Status `unsupported` im Ergebnis (gezaehlt, nie still).

Je Switch ein Lesestatus (`NormalizedTransceiverRead`): `read` (auch ohne Optik - nur Kupfer),
`unreadable` (Befehl gescheitert bzw. Fehlermeldung des Geraets, Grund in `note`), `unsupported`.
Zugeordnet wird nur einem gelesenen Port des Geraets; Eintraege fuer Gehaeuse, Netzteile,
Luefter und Module bleiben draussen.
"""

import json
import re

from collectors.common.dto import (
    NetworkInventory,
    NormalizedTransceiver,
    NormalizedTransceiverRead,
)

CMD_INVENTORY = "show inventory"
CMD_NXOS = "show interface transceiver"
CMD_EOS = "show inventory | json"
CMD_AOSCX = "show interface transceiver"
CMD_JUNOS = "show chassis hardware | display json | no-more"
# zusaetzliche Befehle je Plattform (Catalyst 1200/1300 und Junos lesen ihre Quelle schon)
COMMANDS: dict[str, tuple[str, ...]] = {
    "ios": (CMD_INVENTORY,),
    "nxos": (CMD_NXOS,),
    "eos": (CMD_EOS,),
    "aoscx": (CMD_AOSCX,),
}
ALL = frozenset(c for cmds in COMMANDS.values() for c in cmds)
# Ausgabe, aus der die Transceiver einer Plattform kommen
SOURCE: dict[str, str] = {"ios": CMD_INVENTORY, "nxos": CMD_NXOS, "cbs": CMD_INVENTORY,
                          "junos": CMD_JUNOS, "eos": CMD_EOS, "aoscx": CMD_AOSCX}  # fmt: skip
READ, UNREADABLE, UNSUPPORTED = "read", "unreadable", "unsupported"
_EMPTY = {"", "--", "n/a", "na", "none", "unspecified", "not present", "unknown"}


def _text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in _EMPTY else text[:100]


def port_key(name: str | None) -> str:
    """Portname zum Vergleich: Kurzform, klein, ohne Leerzeichen (`TenGigabitEthernet1/1/1`,
    `Te1/1/1` -> `te1/1/1`); `GE1/0/25` der Business-CLI wie `Gi1/0/25`."""
    from collectors.cisco_switch.normalize import short_name

    text = re.sub(r"\s+", "", short_name(name or "")).lower()
    if re.match(r"ge\d", text):
        return "gi" + text[2:]
    if re.match(r"et\d", text):  # Arista `Et1` wie `Ethernet1` (`Eth1`)
        return "eth" + text[2:]
    return text


def _numbers(name: str) -> str:
    """Zahlenpfad eines Ports (`Eth1/49/1` -> `1/49/1`, `xe-0/1/2` -> `0/1/2`)."""
    m = re.search(r"(\d+(?:/\d+)*)(?::\d+)?$", name or "")
    return m.group(1) if m else ""


class _Ports:
    """Physische Ports eines Geraets: Suche ueber den Namen bzw. den Zahlenpfad."""

    def __init__(self, names: list[str]) -> None:
        self.by_key = {port_key(n): n for n in names}
        self.by_numbers: dict[str, list[str]] = {}
        for n in names:
            self.by_numbers.setdefault(_numbers(n), []).append(n)

    def name(self, raw: str | None) -> str | None:
        return self.by_key.get(port_key(raw)) if raw else None

    def numbers(self, path: str, prefixes: tuple[str, ...] = ()) -> str | None:
        """Einziger Port mit diesem Zahlenpfad (bei Junos nur mit den Praefixen der Ports)."""
        found = [n for n in self.by_numbers.get(path, [])
                 if not prefixes or n.lower().startswith(prefixes)]  # fmt: skip
        return found[0] if len(found) == 1 else None


# -- Ausgaben je Plattform -------------------------------------------------------------------

_NAME = re.compile(r'NAME:\s*"(?P<name>[^"]*)"\s*,?\s*DESCR:\s*"(?P<descr>[^"]*)"', re.I)
_PID = re.compile(r"PID:\s*(?P<pid>.*?)\s*,?\s*VID:\s*(?P<vid>.*?)\s*,?\s*SN:\s*(?P<sn>\S*)", re.I)
_SUBSLOT = re.compile(r"subslot\s+(\d+/\d+)\s+transceiver\s+(\d+)", re.I)


def inventory_rows(text: str) -> list[dict]:
    """`show inventory` (IOS/IOS-XE, Business-CLI): je Eintrag NAME, DESCR, PID, VID, SN."""
    out: list[dict] = []
    current: dict | None = None
    for line in (text or "").splitlines():
        m = _NAME.search(line)
        if m:
            current = {"name": m.group("name").strip(), "descr": m.group("descr").strip()}
            out.append(current)
            continue
        m = _PID.search(line)
        if m and current is not None:
            current.update(pid=m.group("pid").strip(" ,"), vid=m.group("vid").strip(" ,"),
                           sn=m.group("sn").strip(" ,"))  # fmt: skip
            current = None
    return out


def _from_inventory(text: str, ports: _Ports) -> list[dict]:
    out = []
    for r in inventory_rows(text):
        port = ports.name(r["name"])
        if port is None:  # Router: „subslot 0/0 transceiver 0“ -> Port …0/0/0
            m = _SUBSLOT.search(r["name"])
            port = ports.numbers(f"{m.group(1)}/{m.group(2)}") if m else None
        if port is None:
            continue  # Gehaeuse, Netzteil, Luefter, Modul
        out.append({"port": port, "type": _text(r.get("descr")), "part": _text(r.get("pid")),
                    "serial": _text(r.get("sn"))})  # fmt: skip
    return out


_NX_FIELD = (
    (re.compile(r"^\s+type is\s*(.*)$", re.I), "type"),
    (re.compile(r"^\s+name is\s*(.*)$", re.I), "vendor"),
    (re.compile(r"^\s+(?:manufacturer's\s+)?part number is\s*(.*)$", re.I), "vendor_part"),
    (re.compile(r"^\s+serial number is\s*(.*)$", re.I), "serial"),
    (re.compile(r"^\s+cisco product id is\s*(.*)$", re.I), "pid"),
)


def nxos_blocks(text: str) -> list[dict]:
    """`show interface transceiver` (NX-OS): je Port ein Block, nur mit gestecktem Transceiver."""
    out: list[dict] = []
    current: dict | None = None
    for line in (text or "").replace("\r", "").splitlines():
        if line and not line[0].isspace():
            current = {"port": line.strip()}
            out.append(current)
            continue
        if current is None:
            continue
        low = line.strip().lower()
        if low.startswith("transceiver is"):
            current["present"] = low == "transceiver is present"
            continue
        for pattern, key in _NX_FIELD:
            m = pattern.match(line)
            if m and key not in current:
                current[key] = m.group(1).strip()
                break
    return [b for b in out if b.get("present")]


def _from_nxos(text: str, ports: _Ports) -> list[dict]:
    out = []
    for b in nxos_blocks(text):
        port = ports.name(b["port"])
        if port is None:
            continue
        out.append({"port": port, "type": _text(b.get("type")), "vendor": _text(b.get("vendor")),
                    "part": _text(b.get("pid")) or _text(b.get("vendor_part")),
                    "serial": _text(b.get("serial"))})  # fmt: skip
    return out


def _from_eos(text: str, ports: _Ports) -> list[dict]:
    """`show inventory | json` (EOS): `xcvrSlots` je Slot; Slot 49 eines QSFP = `Eth49/1`."""
    raw = (text or "").strip()
    start = raw.find("{")
    data = json.loads(raw[start:]) if start >= 0 else {}
    out = []
    for slot, x in sorted((data.get("xcvrSlots") or {}).items()):
        if not isinstance(x, dict) or not _text(x.get("mfgName")) or not _text(x.get("serialNum")):
            continue  # leerer Schacht („Not Present“)
        port = ports.numbers(str(slot)) or ports.numbers(f"{slot}/1")
        if port is None:
            continue
        out.append({"port": port, "type": _text(x.get("modelName")),
                    "vendor": _text(x.get("mfgName")), "part": _text(x.get("modelName")),
                    "serial": _text(x.get("serialNum"))})  # fmt: skip
    return out


_AOS_ROW = re.compile(r"^\s*(\d+/\d+/\d+)\s+(.*\S)\s*$")


def _from_aoscx(text: str, ports: _Ports) -> list[dict]:
    """`show interface transceiver` (AOS-CX): Port, Typ, Product Number, Serial, Part Number."""
    out = []
    for line in (text or "").splitlines():
        m = _AOS_ROW.match(line)
        if not m:
            continue
        cols = m.group(2).split()
        if len(cols) < 4 or _text(cols[0]) is None:
            continue  # kein Transceiver gesteckt (`--`)
        port = ports.name(m.group(1))
        if port is None:
            continue
        out.append({"port": port, "type": _text(" ".join(cols[:-3])), "part": _text(cols[-3]),
                    "serial": _text(cols[-2])})  # fmt: skip
    return out


_XCVR = re.compile(r"^xcvr\s+(\d+)$", re.I)
_JUNOS_PORTS = ("ge-", "xe-", "et-", "mge-")


def _from_junos(text: str, ports: _Ports) -> list[dict]:
    """`show chassis hardware` (Junos, JSON): FPC f / PIC p / Xcvr n -> Port `xe-f/p/n` u. a."""
    from collectors.cisco_switch import junos

    data = junos.load(text)
    out = []
    for fpc in junos.nodes(data, "chassis-module"):
        fm = re.match(r"^FPC\s+(\d+)$", junos.v(fpc, "name") or "", re.I)
        if not fm:
            continue
        for pic in fpc.get("chassis-sub-module") or []:
            pm = re.match(r"^PIC\s+(\d+)$", junos.v(pic, "name") or "", re.I)
            if not pm:
                continue
            for x in pic.get("chassis-sub-sub-module") or []:
                xm = _XCVR.match(junos.v(x, "name") or "")
                if not xm:
                    continue
                path = f"{fm.group(1)}/{pm.group(1)}/{xm.group(1)}"
                port = ports.numbers(path, _JUNOS_PORTS)
                if port is None:
                    continue
                out.append({"port": port, "type": _text(junos.v(x, "description")),
                            "part": _text(junos.v(x, "part-number")),
                            "serial": _text(junos.v(x, "serial-number"))})  # fmt: skip
    return out


_PARSERS = {"ios": _from_inventory, "cbs": _from_inventory, "nxos": _from_nxos,
            "eos": _from_eos, "aoscx": _from_aoscx, "junos": _from_junos}  # fmt: skip


def read(
    platform: str, outputs: dict[str, str], device: str, ports: list[str]
) -> tuple[list[NormalizedTransceiver], NormalizedTransceiverRead]:
    """Transceiver eines Switches und sein Lesestatus. `ports` = physische Ports (Kurzform)."""
    from collectors.cisco_switch.normalize import cli_error

    cmd = SOURCE.get(platform)
    if cmd is None:
        note = ("über SNMPv3 nicht lesbar (nur per SSH)" if platform == "snmp"
                else f"Plattform {platform} nicht unterstützt")  # fmt: skip
        return [], NormalizedTransceiverRead(device=device, status=UNSUPPORTED, note=note)
    text = outputs.get(cmd)
    if text is None:
        return [], NormalizedTransceiverRead(device=device, status=UNREADABLE,
                                             note=f"„{cmd}“ nicht gelesen")  # fmt: skip
    error = cli_error(text) if text.strip() else None
    if error:
        return [], NormalizedTransceiverRead(device=device, status=UNREADABLE,
                                             note=f"„{cmd}“: {error}"[:300])  # fmt: skip
    try:
        rows = _PARSERS[platform](text, _Ports(ports))
    except ValueError as exc:  # JSON nicht lesbar
        return [], NormalizedTransceiverRead(
            device=device, status=UNREADABLE,
            note=f"„{cmd}“: Ausgabe nicht auswertbar ({type(exc).__name__})")  # fmt: skip
    seen: set[str] = set()
    out: list[NormalizedTransceiver] = []
    for r in rows:
        if r["port"] in seen:
            continue
        seen.add(r["port"])
        out.append(NormalizedTransceiver(device=device, **r))
    return out, NormalizedTransceiverRead(device=device, status=READ, count=len(out))


def add(inv: NetworkInventory, part: NetworkInventory, sw: dict) -> None:
    """Transceiver eines gelesenen Switches ins Ergebnis (`normalize`)."""
    if not part.devices:
        return
    device = part.devices[0].name
    ports = [i.name for i in part.interfaces if i.kind == "physical" and i.name.strip()]
    items, status = read(sw.get("platform") or "", sw.get("outputs") or {}, device, ports)
    if inv.transceivers is None:
        inv.transceivers = []
    if inv.transceiver_reads is None:
        inv.transceiver_reads = []
    inv.transceivers += items
    inv.transceiver_reads.append(status)


def summary(inv: NetworkInventory) -> str | None:
    """Kurz fuer die Meldung des Laufs (deutsch, gespeichert): gelesen, nicht lesbar, nicht
    unterstuetzt - je mit Anzahl Switches."""
    reads = inv.transceiver_reads or []
    if not reads or (not inv.transceivers and all(r.status == READ for r in reads)):
        return None  # nichts gelesen und nichts ausgefallen (nur Kupfer): keine Zeile
    ok = [r for r in reads if r.status == READ]
    text = f"Transceiver: {len(inv.transceivers or [])} an {len(ok)} Switches"
    bad = [r for r in reads if r.status == UNREADABLE]
    if bad:
        names = ", ".join(r.device for r in bad[:5]) + (" …" if len(bad) > 5 else "")
        text += f"; nicht lesbar auf {len(bad)} ({names})"
    unsupported = [r for r in reads if r.status == UNSUPPORTED]
    if unsupported:
        text += f"; nicht unterstützt auf {len(unsupported)} ({unsupported[0].note})"
    return text
