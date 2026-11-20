"""Verkabelung im Detail (1.19.0, ADR 0031 Nachtrag 1.19.0): Kabelnummern, Kabel der NetBox,
Transceiver und die Befunde dazu - nur Daten der Plattform, rein lesend gegenueber den Geraeten.

Anwender 2026-11-17: „Verkabelung im Detail“ - „Kabelnummern können auch in Automato erfasst
werden, beides möglich“.

- **Kabelnummer aus NetBox:** das Label der Kabel, gelesen mit der Referenzquelle NetBox
  (`reference_record` Art `cable`, Enden in `attrs`) - nur angezeigt, nie geloescht.
- **Kabelnummer in Automato:** je Port-Paar eine Erfassung (`cable_label`, Operator, Audit). Der
  Schluessel `pair` normalisiert beide Enden wie die Verbindungen der Verkabelung
  (`cabling.device_key`/`port_key`) - Langform und Kurzform des Portnamens sind dasselbe Paar.
- **Transceiver** je Port und der Lesestatus je Switch (`inv_transceiver`,
  `inv_device.transceivers`).
- **Befunde** (`checks`, Pruefung `app/checks/analyse/kabel.py`): LLDP/CDP-Nachbar passt nicht
  zum Kabel in NetBox; Kabelnummer in NetBox und Automato verschieden; Duplex nicht voll (nur,
  wenn beide Enden gelesen sind und das Duplex melden). Speed an beiden Enden verschieden
  prueft weiter `cabling` (`link_speed_mismatch`).

Was ein Port nicht hergibt, bleibt „unbekannt“ und ist kein Befund (nie raten).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import german_only
from app.models import (
    CableLabel,
    FindingType,
    InvDevice,
    InvTransceiver,
    Platform,
    ReferenceRecordRow,
    Severity,
    SourceSystem,
)
from app.store import cabling

LABEL_MAX = 100  # NetBox: Label eines Kabels
SAME, DIFFERS, AUTOMATO, NETBOX = "same", "differs", "automato", "netbox"
NEIGHBOR_OK, NEIGHBOR_MISMATCH, NEIGHBOR_UNKNOWN = "ok", "mismatch", "unknown"
TYPES = frozenset({FindingType.CABLE_NEIGHBOR_MISMATCH, FindingType.CABLE_LABEL_MISMATCH,
                   FindingType.LINK_HALF_DUPLEX})  # fmt: skip


def end_key(device: str | None, port: str | None) -> str:
    return f"{cabling.device_key(device)}|{cabling.port_key(port) if port else '?'}"


def pair_key(a_device: str, a_port: str, b_device: str, b_port: str) -> str:
    """Beide Enden normalisiert und sortiert - wie die Kennung einer Verbindung (`Link.id`)."""
    return "~".join(sorted((end_key(a_device, a_port), end_key(b_device, b_port))))


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


@dataclass
class NbCable:
    """Kabel einer NetBox (Referenzquelle) mit je einem Port an beiden Enden."""

    id: str
    label: str | None
    a: tuple[str, str]
    b: tuple[str, str]
    source: str
    source_system_id: int
    url: str | None = None
    status: str | None = None
    attrs: dict = field(default_factory=dict)

    @property
    def pair(self) -> str:
        return pair_key(*self.a, *self.b)

    def out(self) -> dict:
        extra = {k: self.attrs[k] for k in ("type", "color", "length") if self.attrs.get(k)}
        return {"id": self.id, "label": self.label, "source": self.source,
                "source_system_id": self.source_system_id, "url": self.url,
                "status": self.status, "a": {"device": self.a[0], "port": self.a[1]},
                "b": {"device": self.b[0], "port": self.b[1]}, **extra}  # fmt: skip


def netbox_cables(session: Session, tenant_id: int) -> list[NbCable]:
    """Kabel aller NetBox-Referenzquellen des Mandanten (aktueller Stand je Quelle)."""
    sources = {s.reference_run_id: s for s in session.scalars(
        select(SourceSystem).where(SourceSystem.tenant_id == tenant_id,
                                   SourceSystem.platform == Platform.NETBOX,
                                   SourceSystem.reference_run_id.is_not(None)))}  # fmt: skip
    if not sources:
        return []
    out = []
    rows = session.scalars(select(ReferenceRecordRow).where(
        ReferenceRecordRow.collector_run_id.in_(list(sources)),
        ReferenceRecordRow.kind == "cable"))  # fmt: skip
    for r in rows:
        a = r.attrs or {}
        if not all(a.get(k) for k in ("a_device", "a_port", "b_device", "b_port")):
            continue
        src = sources[r.collector_run_id]
        out.append(NbCable(r.value, _clean(r.name) or None, (a["a_device"], a["a_port"]),
                           (a["b_device"], a["b_port"]), src.name, src.id, r.url, r.status,
                           a))  # fmt: skip
    return out


def labels(session: Session, tenant_id: int) -> dict[str, CableLabel]:
    return {c.pair: c for c in session.scalars(
        select(CableLabel).where(CableLabel.tenant_id == tenant_id))}  # fmt: skip


def label_out(c: CableLabel) -> dict:
    return {"id": c.id, "label": c.label, "note": c.note, "changed_by": c.changed_by,
            "changed_at": c.changed_at.isoformat() if c.changed_at else None,
            "a": {"device": c.a_device, "port": c.a_port},
            "b": {"device": c.b_device, "port": c.b_port}}  # fmt: skip


def state_of(automato: CableLabel | None, netbox: list[NbCable]) -> str | None:
    """Herkunft der Kabelnummer: gleich, verschieden, nur Automato, nur NetBox (None = keine).
    Ein Kabel in NetBox ohne Label zaehlt nicht als Nummer."""
    found = sorted({c.label for c in netbox if c.label})
    if automato is None:
        return NETBOX if found else None
    if not found:
        return AUTOMATO
    return SAME if found == [automato.label] else DIFFERS


class Index:
    """Kabelnummern, Kabel der NetBox und Transceiver eines Mandanten - einmal je Anfrage."""

    def __init__(self, session: Session, tenant_id: int):
        self.labels = labels(session, tenant_id)
        self.cables = netbox_cables(session, tenant_id)
        self.by_pair: dict[str, list[NbCable]] = defaultdict(list)
        self.by_end: dict[str, list[NbCable]] = defaultdict(list)
        for c in self.cables:
            self.by_pair[c.pair].append(c)
            self.by_end[end_key(*c.a)].append(c)
            self.by_end[end_key(*c.b)].append(c)
        self.transceivers: dict[str, InvTransceiver] = {}
        rows = session.scalars(select(InvTransceiver).where(InvTransceiver.tenant_id == tenant_id))
        for t in rows:
            self.transceivers.setdefault(end_key(t.device, t.port), t)
        self.reads: dict[str, str] = {
            cabling.device_key(name): status
            for name, status in session.execute(
                select(InvDevice.name, InvDevice.transceivers)
                .where(InvDevice.tenant_id == tenant_id, InvDevice.transceivers.is_not(None)))
        }  # fmt: skip

    def label(self, pair: str) -> dict | None:
        """Kabelnummer eines Paars mit Herkunft (None = weder in Automato noch in NetBox)."""
        mine, theirs = self.labels.get(pair), self.by_pair.get(pair, [])
        state = state_of(mine, theirs)
        if state is None and not theirs:
            return None
        return {"state": state, "automato": label_out(mine) if mine else None,
                "netbox": [c.out() for c in theirs]}  # fmt: skip

    def transceiver(self, device: str | None, port: str | None) -> dict | None:
        t = self.transceivers.get(end_key(device, port)) if device and port else None
        if t is None:
            return None
        return {k: getattr(t, k) for k in ("type", "vendor", "part", "serial") if getattr(t, k)}

    def end(self, out: dict) -> dict:
        """Ende einer Verbindung bzw. Port (`End.out`) um den Transceiver ergaenzen."""
        out["transceiver"] = self.transceiver(out.get("device"), out.get("port") or out.get("name"))
        return out


# -- Befunde ----------------------------------------------------------------------------------


def _ends_at(cab: cabling.Cabling) -> dict[str, list[cabling.End]]:
    """Je Port (Schluessel) die Gegenstellen, die CDP/LLDP dort meldet."""
    out: dict[str, list[cabling.End]] = defaultdict(list)
    for link in cab.links.values():
        for end, other in ((link.a, link.b), (link.b, link.a)):
            if end.port:
                out[end_key(end.device.name, end.port)].append(other)
    return out


def _matches(other: cabling.End, device: str, port: str) -> bool:
    if cabling.device_key(other.device.name) != cabling.device_key(device):
        return False
    return other.port is None or cabling.port_key(other.port) == cabling.port_key(port)


def neighbor_state(cable: NbCable, seen: dict[str, list[cabling.End]]) -> tuple[str, list[dict]]:
    """Passt CDP/LLDP zum Kabel in NetBox? Je Ende mit gemeldeten Nachbarn: einer davon muss das
    andere Ende des Kabels sein. Ohne Nachbar-Daten an beiden Enden: unbekannt (kein Befund)."""
    wrong, checked = [], False
    for mine, theirs in ((cable.a, cable.b), (cable.b, cable.a)):
        others = seen.get(end_key(*mine))
        if not others:
            continue
        checked = True
        if any(_matches(o, *theirs) for o in others):
            continue
        found = [{"device": o.device.name, "port": o.port} for o in others]
        at, netbox = {"device": mine[0], "port": mine[1]}, {"device": theirs[0], "port": theirs[1]}
        wrong.append({"at": at, "netbox": netbox, "seen": found})
    if wrong:
        return NEIGHBOR_MISMATCH, wrong
    return (NEIGHBOR_OK if checked else NEIGHBOR_UNKNOWN), []


def _ends(cable: NbCable) -> dict:
    return {"a": {"device": cable.a[0], "port": cable.a[1]},
            "b": {"device": cable.b[0], "port": cable.b[1]}}  # fmt: skip


def checks(session: Session, tenant_id: int, cab: cabling.Cabling | None = None,
           index: Index | None = None) -> list[dict]:  # fmt: skip
    """Befunde: Nachbar passt nicht zum Kabel in NetBox, Kabelnummer verschieden, Duplex nicht
    voll. Je Kabel bzw. Verbindung und Art ein Eintrag; Detail ohne uebersetzte Texte."""
    cab = cab or cabling.build(session, tenant_id)
    index = index or Index(session, tenant_id)
    seen = _ends_at(cab)
    out: list[dict] = []
    for cable in index.cables:
        state, wrong = neighbor_state(cable, seen)
        if state == NEIGHBOR_MISMATCH:
            out.append({"type": FindingType.CABLE_NEIGHBOR_MISMATCH, "subject": cable.pair,
                        "severity": Severity.WARNING, "source_system_id": cable.source_system_id,
                        "detail": {**_ends(cable), "netbox": cable.source,
                                   "cable_id": cable.id, "label": cable.label, "url": cable.url,
                                   "wrong": wrong}})  # fmt: skip
    for pair, mine in index.labels.items():
        theirs = index.by_pair.get(pair, [])
        if state_of(mine, theirs) != DIFFERS:
            continue
        other = next(c for c in theirs if c.label and c.label != mine.label)
        out.append({"type": FindingType.CABLE_LABEL_MISMATCH, "subject": pair,
                    "severity": Severity.WARNING, "source_system_id": other.source_system_id,
                    "detail": {**_ends(other), "netbox": other.source, "cable_id": other.id,
                               "url": other.url, "netbox_label": other.label,
                               "automato_label": mine.label, "label_id": mine.id}})  # fmt: skip
    for link in cab.links.values():
        half = _half_duplex(cab, link)
        if half:
            out.append({"type": FindingType.LINK_HALF_DUPLEX, "subject": link.id,
                        "severity": Severity.WARNING,
                        "source_system_id": link.a.device.source_system_id
                        or link.b.device.source_system_id,
                        "detail": {"a": link.a.out(), "b": link.b.out(), "link": link.id,
                                   "half": half}})  # fmt: skip
    return out


def _half_duplex(cab: cabling.Cabling, link: cabling.Link) -> list[str]:
    """Enden mit halbem Duplex - nur, wenn beide Enden gelesen sind und ihr Duplex melden.
    Zwischen zwei Switches mit verschiedenem Duplex meldet das schon `link_duplex_mismatch`."""
    a, b = link.a.iface, link.b.iface
    if a is None or b is None:
        return []
    da, db = cabling.duplex_of(a.duplex), cabling.duplex_of(b.duplex)
    if da is None or db is None or "half" not in (da, db):
        return []
    switches = a.source_system_id in cab.switch_sources and b.source_system_id in cab.switch_sources
    if da != db and switches:
        return []  # Abweichung: eigener Befund
    return [e.device.name for e, d in ((link.a, da), (link.b, db)) if d == "half"]


@german_only  # speichert Befunde (ADR 0028)
def refresh_findings(session: Session, tenant_id: int) -> int:
    """Nur die Befunde dieser Pruefung neu (nach dem Erfassen bzw. Loeschen einer Kabelnummer) -
    ist die Pruefung bzw. ihr Modul aus, fallen ihre Befunde weg wie im Abgleich."""
    from app import checks as registry
    from app.store import reconcile, tenant_lock

    tenant_lock.hold(session, tenant_id)
    check = registry.by_key("kabel")
    issues = []
    if check is not None and registry.enabled(session, tenant_id, check):
        issues = registry.run(check, session, tenant_id, registry.Context())
    reconcile._sync_findings(session, tenant_id, issues, types=TYPES)
    return len(issues)


# -- Auswahl im Dialog (1.23.0) ----------------------------------------------------------------

# Ports ohne Kabel: logische Interfaces der Geraete (SVI, Port-Channel, Loopback, Tunnel, ...)
LOGICAL = frozenset({"vlan", "lag", "loopback", "tunnel", "vdom-link", "virtual"})
# Herkunft eines Geraets in der Auswahl: gelesen (Ports aus dem Inventar), nur von Nachbarn
# gemeldet (CDP/LLDP), nur am Kabel einer NetBox bzw. frueher frei erfasst (z. B. Patchpanel)
INVENTORY, NEIGHBOR, FROM_NETBOX, FREE = "inventory", "neighbor", "netbox", "free"
_ORIGIN_RANK = {INVENTORY: 0, NEIGHBOR: 1, FROM_NETBOX: 2, FREE: 3}
_VIA_RANK = {"cdp": 0, "lldp": 0, "netbox": 1, "automato": 2}  # Vorschlaege: CDP/LLDP zuerst


def port_order(name: str) -> tuple:
    """Gi1/0/2 vor Gi1/0/10: Zahlen numerisch."""
    import re

    return tuple((0, int(x), "") if x.isdigit() else (1, 0, x.lower())
                 for x in re.split(r"(\d+)", name))  # fmt: skip


@dataclass
class _Dev:
    name: str
    origin: str
    kind: str | None = None
    source: str | None = None
    ports: dict[str, str] = field(default_factory=dict)  # port_key -> Name
    inventory: set[str] = field(default_factory=set)  # port_key der gelesenen Ports


class Choices:
    """Geraete und Ports zum Erfassen einer Kabelnummer (Anwender 2026-11-20: „Geräte und Ports
    auswählen“) und je Port die bekannte Gegenstelle als Vorschlag.

    - **gelesen** (`inventory`): Geraet aus dem Netzinventar mit gelesenen Ports - nur diese Ports
      (ohne logische Interfaces);
    - **von Nachbarn gemeldet** (`neighbor`): Geraet nur aus CDP/LLDP (bzw. gelesen ohne Ports) -
      die gemeldeten Ports, weitere frei;
    - **NetBox** bzw. **frei erfasst** (`netbox`/`free`): Enden der Kabel in NetBox bzw. frueherer
      Erfassungen (Patchpanel u. a.) - frei, als solche gekennzeichnet.

    Vorschlag je Port: die Gegenstelle aus CDP/LLDP, dem Kabel in NetBox bzw. einer Erfassung -
    mit Herkunft (`via`) und der Kabelnummer, falls eine besteht. Nichts geraten."""

    def __init__(self, session: Session, tenant_id: int, cab: cabling.Cabling | None = None,
                 index: Index | None = None):  # fmt: skip
        self.cab = cab or cabling.build(session, tenant_id)
        self.index = index or Index(session, tenant_id)
        self.devices: dict[str, _Dev] = {}
        for d in self.cab.devices.values():
            self.devices[d.key] = _Dev(d.name, NEIGHBOR, d.kind, d.source)
        for i in self.cab.ports:
            dev = self.devices.get(cabling.device_key(i.device))
            if dev is None or i.kind in LOGICAL:
                continue
            dev.ports.setdefault(cabling.port_key(i.name), i.name)
            dev.inventory.add(cabling.port_key(i.name))
            dev.origin = INVENTORY
        # Vorschlaege je Port: Schluessel des Endes -> Gegenstellen (Schluessel -> Eintrag)
        self.peers: dict[str, dict[str, dict]] = defaultdict(dict)
        for link in self.cab.links.values():
            via = sorted(link.protocols) or ["lldp"]
            for end, other in ((link.a, link.b), (link.b, link.a)):
                if end.port:
                    self._port(end.device.name, end.port, NEIGHBOR)
                if end.port and other.port:
                    self._peer(end.device.name, end.port, other.device.name, other.port, via)
        for c in self.index.cables:
            for mine, theirs in ((c.a, c.b), (c.b, c.a)):
                self._port(*mine, FROM_NETBOX)
                self._peer(*mine, *theirs, ["netbox"], c.label)
        for row in self.index.labels.values():
            ends = ((row.a_device, row.a_port), (row.b_device, row.b_port))
            for mine, theirs in (ends, ends[::-1]):
                self._port(*mine, FREE)
                self._peer(*mine, *theirs, ["automato"], row.label)

    def _port(self, device: str, port: str, origin: str) -> None:
        key = cabling.device_key(device)
        dev = self.devices.get(key)
        if dev is None:
            dev = self.devices[key] = _Dev(device, origin)
        if dev.origin != INVENTORY:  # gelesenes Geraet: nur gelesene Ports
            dev.ports.setdefault(cabling.port_key(port), port)

    def _peer(self, device: str, port: str, o_device: str, o_port: str, via: list[str],
              label: str | None = None) -> None:  # fmt: skip
        found = self.peers[end_key(device, port)]
        entry = found.setdefault(end_key(o_device, o_port), {
            "device": self.name(o_device), "port": self.port_name(o_device, o_port), "via": [],
            "labels": []})  # fmt: skip
        entry["via"] += [v for v in via if v not in entry["via"]]
        if label and label not in entry["labels"]:
            entry["labels"].append(label)

    def name(self, device: str) -> str:
        dev = self.devices.get(cabling.device_key(device))
        return dev.name if dev is not None else device

    def port_name(self, device: str, port: str) -> str:
        dev = self.devices.get(cabling.device_key(device))
        return dev.ports.get(cabling.port_key(port), port) if dev is not None else port

    def complete(self, device: str) -> bool:
        """Ports des Geraets gelesen: nur sie sind waehlbar (sonst auch frei)."""
        dev = self.devices.get(cabling.device_key(device))
        return dev is not None and dev.origin == INVENTORY

    def device_list(self, q: str | None = None, limit: int = 100) -> tuple[int, list[dict]]:
        needle = (q or "").strip().casefold()
        rows = [d for d in self.devices.values() if needle in d.name.casefold()]
        rows.sort(key=lambda d: (_ORIGIN_RANK[d.origin], d.name.casefold()))
        out = [{"name": d.name, "origin": d.origin, "kind": d.kind, "source": d.source,
                "ports": len(d.ports), "free_ports": d.origin != INVENTORY}
               for d in rows[:limit]]  # fmt: skip
        return len(rows), out

    def port_list(self, device: str) -> dict | None:
        dev = self.devices.get(cabling.device_key(device))
        if dev is None:
            return None
        ifaces = {cabling.port_key(i.name): i for i in self.cab.ports
                  if cabling.device_key(i.device) == cabling.device_key(device)}  # fmt: skip
        ports = []
        for key, name in sorted(dev.ports.items(), key=lambda kv: port_order(kv[1])):
            i = ifaces.get(key)
            peers = sorted(self.peers.get(end_key(device, name), {}).values(),
                           key=lambda p: (min(_VIA_RANK.get(v, 9) for v in p["via"]),
                                          p["device"].casefold()))  # fmt: skip
            ports.append({
                "name": name, "read": key in dev.inventory,
                "status": i.status if i is not None else None,
                "description": i.description if i is not None else None,
                "peers": peers,
            })  # fmt: skip
        return {"device": {"name": dev.name, "origin": dev.origin, "kind": dev.kind,
                           "source": dev.source, "free_ports": dev.origin != INVENTORY},
                "ports": ports}  # fmt: skip

    def check(self, device: str, port: str) -> tuple[str, str]:
        """Ende pruefen und in der Schreibweise der Plattform: an einem gelesenen Geraet nur ein
        gelesener Port; sonst frei."""
        dev = self.devices.get(cabling.device_key(device))
        if dev is None:
            return device, port
        key = cabling.port_key(port)
        if dev.origin == INVENTORY and key not in dev.ports:
            raise Invalid(
                f"Port {port} auf {dev.name} nicht gelesen – Port aus der Liste wählen",
                f"port {port} on {dev.name} not read – choose a port from the list",
            )
        return dev.name, dev.ports.get(key, port)


# -- Erfassen -------------------------------------------------------------------------------------


class Invalid(ValueError):
    """Ungueltige Erfassung - Text deutsch, `en` englisch (sonst ueber die Tabelle der API)."""

    def __init__(self, text: str, en: str | None = None):
        super().__init__(text)
        self.en = en


def set_label(session: Session, tenant_id: int, a_device: str, a_port: str, b_device: str,
              b_port: str, label: str, note: str | None, actor: str,
              choices: Choices | None = None) -> tuple[CableLabel, str | None]:  # fmt: skip
    """Kabelnummer eines Port-Paars erfassen bzw. aendern (eine Erfassung je Paar) -> (Zeile,
    bisherige Nummer). Mit `choices` (1.23.0): ein neues Paar an einem gelesenen Geraet nur mit
    einem gelesenen Port, Namen in der Schreibweise der Plattform; eine bestehende Erfassung und
    ein Kabel in NetBox bleiben erfassbar, auch wenn ihr Port nicht (mehr) gelesen wird."""
    ends = [_clean(x) for x in (a_device, a_port, b_device, b_port)]
    if not all(ends):
        raise Invalid("Gerät und Port an beiden Enden angeben")
    text = _clean(label)
    if not text:
        raise Invalid("Kabelnummer angeben")
    if len(text) > LABEL_MAX:
        raise Invalid(f"Kabelnummer höchstens {LABEL_MAX} Zeichen")
    if end_key(ends[0], ends[1]) == end_key(ends[2], ends[3]):
        raise Invalid("beide Enden sind derselbe Port")
    pair = pair_key(*ends)
    if choices is not None and pair not in choices.index.labels | choices.index.by_pair:
        ends[0], ends[1] = choices.check(ends[0], ends[1])
        ends[2], ends[3] = choices.check(ends[2], ends[3])
    row = session.scalar(select(CableLabel).where(CableLabel.tenant_id == tenant_id,
                                                  CableLabel.pair == pair))  # fmt: skip
    old = row.label if row is not None else None
    if row is None:
        row = CableLabel(tenant_id=tenant_id, pair=pair)
        session.add(row)
    row.a_device, row.a_port, row.b_device, row.b_port = (e[:200] for e in ends)
    row.label, row.note = text, (_clean(note)[:500] or None) if note else None
    row.changed_by, row.changed_at = actor, datetime.now(UTC)
    session.flush()
    return row, old
