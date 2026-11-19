"""Spanning Tree der Switches (Automato 1.19.0), nur lesend - Cisco IOS/IOS-XE und NX-OS.

Anwender 2026-11-17 (Prioritaet 5 „Spanningtree“): je VLAN bzw. MST-Instanz Root-Bridge, eigene
Bridge-ID/Prioritaet, Rollen und Zustaende der Ports, Modus (PVST/Rapid-PVST/MST), Zaehler und
Zeit der Topologieaenderungen, err-disabled durch BPDU-Guard.

Gelesen wird nur mit `show`-Befehlen, die es auf beiden Plattformen gibt (keine Konfiguration,
keine Rueckfragen):

- `show spanning-tree` (seit 0.65.0): je Instanz Protokoll (`ieee` = PVST+, `rstp` =
  Rapid-PVST, `mstp` = MST), Root-ID (Prioritaet, MAC, Kosten, Root-Port), Bridge-ID und die
  Port-Tabelle (Rolle, Zustand, Kosten, Prio.Nbr, Typ mit Grenzen `Bound(PVST)`/`Peer(STP)` und
  Inkonsistenzen `*ROOT_Inc`, `*LOOP_Inc`, `*BA_Inc` …);
- `show spanning-tree detail`: je Instanz Zahl und Alter der letzten Topologieaenderung und der
  Port, ueber den sie kam (`from …`) - Bridge und Root als Rueckfall;
- `show spanning-tree mst configuration`: Region, Revision und VLANs je MST-Instanz;
- `show interfaces status err-disabled` (IOS) bzw. `show interface status err-disabled`
  (NX-OS): gesperrte Ports mit Grund (`bpduguard`, `BPDUGuard errDisable`). Fehlt der Befehl,
  zaehlen die Ports mit Status err-disabled aus `show interfaces status` ohne Grund.

Andere Plattformen (Catalyst 1200/1300, Junos, Arista EOS, Aruba CX, SNMPv3) liest Automato hier
nicht - der Switch steht dann mit Status `unsupported` im Ergebnis und wird in der Ansicht
gezaehlt (keine stille Grenze).
"""

import re

from collectors.common.dto import NormalizedStp, NormalizedStpDevice, NormalizedStpPort

TREE = "show spanning-tree"
DETAIL = "show spanning-tree detail"
MST_CONFIG = "show spanning-tree mst configuration"
ERRDISABLED = {"ios": "show interfaces status err-disabled",
               "nxos": "show interface status err-disabled"}  # fmt: skip
# zusaetzliche Befehle je Plattform (stehen in `client.COMMANDS`); eigene Auswertung, keine ntc
COMMANDS: dict[str, tuple[str, ...]] = {
    p: (DETAIL, MST_CONFIG, ERRDISABLED[p]) for p in ERRDISABLED
}
OWN = frozenset(c for cmds in COMMANDS.values() for c in cmds)
SUPPORTED = frozenset(COMMANDS)

PROTOCOL_MODE = {"ieee": "pvst", "rstp": "rapid-pvst", "mstp": "mst"}
ROLES = {"root": "root", "desg": "designated", "altn": "alternate", "back": "backup",
         "bkup": "backup", "mstr": "master", "disb": "disabled", "none": "disabled"}  # fmt: skip
STATES = {"fwd": "forwarding", "blk": "blocking", "lrn": "learning", "lis": "listening",
          "lsn": "listening", "dis": "disabled", "bkn": "broken"}  # fmt: skip

_HEAD = re.compile(r"^\s*(VLAN|MST)0*(\d+)\s*$", re.I)
_PROTO = re.compile(r"Spanning tree enabled protocol\s+(\S+)", re.I)
_BLOCK = re.compile(r"^\s*(Root|Bridge) ID\b", re.I)
_PRIO = re.compile(r"\bPriority\s+(\d+)", re.I)
_ADDR = re.compile(r"^\s*Address\s+([0-9A-Fa-f.:-]+)")
_COST = re.compile(r"^\s*Cost\s+(\d+)")
_RPORT = re.compile(r"^\s*Port\s+\d+\s*\(([^)]+)\)")
_ROW = re.compile(
    r"^(\S+)\s+(Root|Desg|Altn|Back|Bkup|Mstr|Disb|None)\s+([A-Z]{3})\*?\s*(\d+)\s+(\d+\.\d+)\s*(.*?)\s*$",
    re.I,
)
_INC = re.compile(r"\*\s*(\w+_Inc)\b", re.I)
_NO_STP = re.compile(r"no spanning tree instance|spanning tree .*not enabled|not running", re.I)

_D_HEAD = re.compile(r"^\s*(VLAN|MST)0*(\d+)\s+is executing the\s+(\S+)\s+compatible", re.I)
_D_BRIDGE = re.compile(
    r"Bridge Identifier has priority\s+(\d+),\s*sysid\s+(\d+),\s*address\s+(\S+)", re.I
)
_D_ROOT = re.compile(r"Current root has priority\s+(\d+),\s*address\s+(\S+)", re.I)
_D_RPORT = re.compile(r"Root port is\s+\d+\s*\(([^)]+)\),\s*cost of root path is\s+(\d+)", re.I)
_D_SELF = re.compile(r"We are the root of the spanning tree", re.I)
_D_TC = re.compile(
    r"Number of topology changes\s+(\d+)" r"(?:\s+last change occurred\s+(.+?)(?:\s+ago)?\s*$)?",
    re.I,
)
_D_FROM = re.compile(r"^\s*from\s+(\S+)\s*$", re.I)
_D_PORT = re.compile(r"^\s*Port\s+\d+\s*\(", re.I)  # Beginn der Port-Bloecke einer Instanz


def _short(name: str | None) -> str:
    from collectors.cisco_switch.normalize import short_name

    return short_name(name)


def _mac(value: str | None) -> str | None:
    from collectors.cisco_switch.normalize import mac_text

    return mac_text(value)


def _key(kind: str, number: str) -> tuple[str, int]:
    return ("mst" if kind.lower() == "mst" else "vlan", int(number))


def _name(kind: str, number: int) -> str:
    return f"MST{number}" if kind == "mst" else f"VLAN{number:04d}"


def seconds(text: str | None) -> int | None:
    """Alter der letzten Aenderung: `00:01:52`, `142:31:17` (NX-OS), `2d03h`, `1w2d`, `1y2w`
    -> Sekunden; `never`/unlesbar -> None."""
    t = (text or "").strip().lower().removesuffix("ago").strip()
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})", t)
    if m:
        h, mi, s = (int(x) for x in m.groups())
        return h * 3600 + mi * 60 + s
    m = re.fullmatch(r"(?:(\d+)y)?(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if not t or not m or not any(m.groups()):
        return None
    y, w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((y * 365 + w * 7 + d) * 24 + h) * 3600 + mi * 60 + s


def _port(m: re.Match) -> NormalizedStpPort:
    raw_type = m.group(6) or ""
    inc = _INC.search(raw_type)
    ptype = _INC.sub("", raw_type).strip() or None
    return NormalizedStpPort(
        port=_short(m.group(1)),
        role=ROLES.get(m.group(2).lower(), m.group(2).lower()),
        state=STATES.get(m.group(3).lower(), m.group(3).lower()),
        cost=int(m.group(4)),
        priority=m.group(5),
        type=re.sub(r"\s+", " ", ptype) if ptype else None,
        inconsistent=inc.group(1).upper().replace("_INC", "_Inc") if inc else None,
    )


def parse_tree(text: str) -> list[dict]:
    """`show spanning-tree` -> je Instanz {kind, number, protocol, root_*, bridge_*, ports}."""
    out: list[dict] = []
    cur: dict | None = None
    block: str | None = None
    for line in (text or "").splitlines():
        head = _HEAD.match(line)
        if head:
            kind, number = _key(*head.groups())
            cur = {"kind": kind, "number": number, "protocol": None, "is_root": False,
                   "root_priority": None, "root_mac": None, "root_cost": None,
                   "root_port": None, "bridge_priority": None, "bridge_mac": None,
                   "ports": []}  # fmt: skip
            out.append(cur)
            block = None
            continue
        if cur is None:
            continue
        m = _PROTO.search(line)
        if m:
            cur["protocol"] = m.group(1).lower()
            continue
        m = _BLOCK.match(line)
        if m:
            block = m.group(1).lower()
        m = _PRIO.search(line)
        if m and block:
            cur[f"{block}_priority"] = int(m.group(1))
            continue
        m = _ADDR.match(line)
        if m and block:
            cur[f"{block}_mac"] = _mac(m.group(1))
            continue
        if "this bridge is the root" in line.lower():
            cur["is_root"] = True
            continue
        m = _COST.match(line)
        if m and block == "root":
            cur["root_cost"] = int(m.group(1))
            continue
        m = _RPORT.match(line)
        if m and block == "root":
            cur["root_port"] = _short(m.group(1))
            continue
        m = _ROW.match(line)
        if m:
            block = None
            cur["ports"].append(_port(m))
    for inst in out:
        if inst["is_root"]:  # Root = diese Bridge; Bridge-ID fehlt in manchen Ausgaben
            inst["bridge_mac"] = inst["bridge_mac"] or inst["root_mac"]
            inst["bridge_priority"] = inst["bridge_priority"] or inst["root_priority"]
            inst["root_cost"], inst["root_port"] = inst["root_cost"] or 0, None
    return out


def parse_detail(text: str) -> dict[tuple[str, int], dict]:
    """`show spanning-tree detail` -> je Instanz Topologieaenderungen (Zahl, Alter in Sekunden,
    Port) sowie Bridge/Root als Rueckfall. Die Port-Bloecke werden uebersprungen."""
    out: dict[tuple[str, int], dict] = {}
    cur: dict | None = None
    in_ports = False
    for line in (text or "").splitlines():
        head = _D_HEAD.match(line)
        if head:
            cur = out.setdefault(_key(head.group(1), head.group(2)), {})
            cur["protocol"] = head.group(3).lower()
            in_ports = False
            continue
        if cur is None or in_ports:
            continue
        if _D_PORT.match(line):
            in_ports = True  # ab hier nur Ports - bis zur naechsten Instanz
            continue
        m = _D_BRIDGE.search(line)
        if m:
            cur["bridge_priority"] = int(m.group(1)) + int(m.group(2))
            cur["bridge_mac"] = _mac(m.group(3))
            continue
        m = _D_ROOT.search(line)
        if m:
            cur["root_priority"], cur["root_mac"] = int(m.group(1)), _mac(m.group(2))
            continue
        m = _D_RPORT.search(line)
        if m:
            cur["root_port"], cur["root_cost"] = _short(m.group(1)), int(m.group(2))
            continue
        if _D_SELF.search(line):
            cur["is_root"] = True
            continue
        m = _D_TC.search(line)
        if m:
            cur["tc_count"] = int(m.group(1))
            cur["tc_ago"] = seconds(m.group(2)) if m.group(2) else None
            continue
        m = _D_FROM.match(line)
        if m and "tc_count" in cur and "tc_from" not in cur:
            cur["tc_from"] = _short(m.group(1))
    return out


def parse_mst(text: str) -> dict:
    """`show spanning-tree mst configuration` -> {region, revision, instances: {Nr: VLANs}}.
    Lange VLAN-Listen gehen auf der naechsten Zeile weiter (eingerueckt)."""
    out: dict = {"region": None, "revision": None, "instances": {}}
    last: int | None = None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*Name\s+\[(.*)\]", line)
        if m:
            out["region"] = m.group(1).strip() or None
            continue
        m = re.match(r"^\s*Revision\s+(\d+)", line)
        if m:
            out["revision"] = int(m.group(1))
            continue
        m = re.match(r"^\s*(\d+)\s+([\d,\-\s]+)$", line)
        if m:
            last = int(m.group(1))
            out["instances"][last] = re.sub(r"\s+", "", m.group(2))
            continue
        m = re.match(r"^\s+([\d,\-]+)\s*$", line)
        if m and last is not None:
            joined = out["instances"][last].rstrip(",") + "," + m.group(1).lstrip(",")
            out["instances"][last] = joined
            continue
        if line.strip().startswith("---") and out["instances"]:
            last = None
    return out


def parse_errdisabled(text: str) -> list[dict]:
    """`show interface(s) status err-disabled` -> [{port, reason, vlans}] - Spalten nach der
    Kopfzeile (Name mit Leerzeichen; NX-OS ohne Spalte der VLANs)."""
    out: list[dict] = []
    cols: dict[str, int] | None = None
    for line in (text or "").splitlines():
        if not line.strip() or set(line.strip()) <= {"-"}:
            continue
        low = line.lower()
        if cols is None:
            if low.lstrip().startswith("port") and "status" in low and "reason" in low:
                cols = {k: low.index(k) for k in ("status", "reason")}
                if "err-disabled vlans" in low:
                    cols["vlans"] = low.index("err-disabled vlans")
            continue
        port = line.split()[0]
        reason = line[cols["reason"] : cols.get("vlans", len(line))].strip()
        if not reason:  # Spalte verschoben (langer Name): Rest hinter dem Status
            m = re.search(r"\b(err-?disabled|down)\s+(.+)$", line, re.I)
            reason = m.group(2).strip() if m else ""
        vlans = line[cols["vlans"] :].strip() if "vlans" in cols else ""
        out.append({"port": _short(port), "reason": reason or None,
                    "vlans": vlans or None})  # fmt: skip
    return out


def bpdu_guard(reason: str | None) -> bool:
    """err-disabled durch BPDU-Guard (`bpduguard`, `BPDUGuard errDisable`)."""
    return "bpdu" in (reason or "").lower()


def _error(text: str | None) -> str | None:
    from collectors.cisco_switch.normalize import cli_error

    return cli_error(text) if text and text.strip() else None


def inventory(
    device: str, platform: str, outputs: dict[str, str], statuses: dict[str, str] | None = None
) -> tuple[NormalizedStpDevice, list[NormalizedStp]]:
    """Spanning Tree eines Switches -> (Geraet mit Modus, Status und err-disabled, Instanzen).
    `statuses`: Port -> Status aus `show interfaces status` (Rueckfall fuer err-disabled)."""
    if platform not in SUPPORTED:
        return NormalizedStpDevice(device=device, status="unsupported"), []
    tree_text = outputs.get(TREE)
    if tree_text is None:
        return NormalizedStpDevice(device=device, status="unreadable",
                                   note=f"{TREE}: nicht gelesen"), []  # fmt: skip
    error = _error(tree_text)
    if error and not _NO_STP.search(error):
        return NormalizedStpDevice(device=device, status="unreadable", note=f"{TREE}: {error}"), []
    trees = [] if error else parse_tree(tree_text)
    detail = {} if _error(outputs.get(DETAIL)) else parse_detail(outputs.get(DETAIL, ""))
    mst = parse_mst("" if _error(outputs.get(MST_CONFIG)) else outputs.get(MST_CONFIG, ""))
    instances: list[NormalizedStp] = []
    for t in trees:
        d = detail.get((t["kind"], t["number"]), {})
        for k in ("protocol", "root_priority", "root_mac", "root_port", "bridge_priority",
                  "bridge_mac"):  # fmt: skip
            if t.get(k) is None and d.get(k) is not None:
                t[k] = d[k]
        if t.get("root_cost") is None and d.get("root_cost") is not None:
            t["root_cost"] = d["root_cost"]
        is_root = t["is_root"] or bool(d.get("is_root"))
        mst_no = t["number"] if t["kind"] == "mst" else None
        instances.append(NormalizedStp(
            device=device, instance=_name(t["kind"], t["number"]),
            vlan=t["number"] if t["kind"] == "vlan" else None, mst=mst_no,
            vlans=mst["instances"].get(mst_no) if mst_no is not None else None,
            protocol=t["protocol"], root_priority=t["root_priority"], root_mac=t["root_mac"],
            root_cost=0 if is_root else t["root_cost"],
            root_port=None if is_root else t["root_port"], is_root=is_root,
            bridge_priority=t["bridge_priority"],
            bridge_mac=t["bridge_mac"] or (t["root_mac"] if is_root else None),
            tc_count=d.get("tc_count"), tc_ago=d.get("tc_ago"), tc_from=d.get("tc_from"),
            ports=t["ports"],
        ))  # fmt: skip
    modes = {PROTOCOL_MODE.get(i.protocol or "") for i in instances} - {None}
    errtext = outputs.get(ERRDISABLED[platform])
    if errtext is not None and not _error(errtext):
        errdisabled = parse_errdisabled(errtext)
    else:  # Befehl fehlt: Ports mit Status err-disabled ohne Grund
        errdisabled = [{"port": p, "reason": None, "vlans": None}
                       for p, s in (statuses or {}).items()
                       if re.fullmatch(r"err-?disabled?", (s or "").lower())]  # fmt: skip
    status = "read" if instances else "off"
    note = None
    if not instances and error:
        note = error
    return NormalizedStpDevice(
        device=device, status=status,
        mode=sorted(modes)[0] if len(modes) == 1 else ("/".join(sorted(modes)) or None),
        region=mst["region"] if "mst" in modes else None,
        revision=mst["revision"] if "mst" in modes else None,
        errdisabled=errdisabled, note=note,
    ), instances  # fmt: skip


def add(inv, part, sw: dict) -> None:
    """Spanning Tree eines gelesenen Switches an das Netzinventar haengen (`normalize`)."""
    if not part.devices:
        return
    name = part.devices[0].name
    statuses = {i.name: i.status or "" for i in part.interfaces}
    dev, instances = inventory(name, sw.get("platform") or "", sw.get("outputs") or {}, statuses)
    inv.stp_devices.append(dev)
    inv.stp.extend(instances)
