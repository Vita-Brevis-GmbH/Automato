"""Switches & Router ueber SNMPv3 lesen (ADR 0033) - Zugang „SNMPv3“ des Quellsystems.

Dasselbe Quellsystem wie per SSH (`cisco_switch`, „Switches & Router“): Adressen im Lesebereich
(`host` bzw. `host:port`, Standard UDP 161), ein SNMPv3-Benutzer fuer alle Geraete, bis zu 8
parallel. Je Geraet die Werte und Spalten der Positivliste (`oids.py`); fehlende Tabellen stehen
als Hinweis im Lauf, ein Geraet ohne Antwort als Teilfehler. Ergebnis wie per SSH
(`cisco_switch/tasks.collect_once`), Auswertung in `snmp/normalize.py`.
"""

from concurrent.futures import ThreadPoolExecutor

from collectors.cisco_switch import normalize as ssh_normalize
from collectors.common import diagnose
from collectors.common.i18n import L, carry
from collectors.snmp import client as sc
from collectors.snmp import oids
from collectors.snmp.normalize import vendor

PARALLEL = 8  # Geraete gleichzeitig
MAX_ENTITIES = 10_000  # Zeilen der ENTITY-MIB bis zum Chassis

_Q_VLAN = ("dot1qVlanStaticName", "dot1qVlanStaticEgressPorts", "dot1qVlanStaticUntaggedPorts",
           "dot1qPvid")  # fmt: skip
_IEEE_VLAN = ("ieee8021BridgeBasePortIfIndex", "ieee8021QBridgeVlanStaticName",
              "ieee8021QBridgeVlanStaticEgressPorts", "ieee8021QBridgeVlanStaticUntaggedPorts",
              "ieee8021QBridgePvid")  # fmt: skip
_FDB = (("dot1qTpFdbPort", "dot1qTpFdbStatus"),
        ("ieee8021QBridgeTpFdbPort", "ieee8021QBridgeTpFdbStatus"),
        ("dot1dTpFdbPort", "dot1dTpFdbStatus"))  # fmt: skip
_LLDP_LOCAL = ("lldpLocPortIdSubtype", "lldpLocPortId", "lldpLocPortDesc")
_LLDP_REMOTE = tuple(n for n in oids.COLUMNS["LLDP-MIB"] if n.startswith("lldpRem"))
_ARP = ("ipNetToPhysicalPhysAddress", "ipNetToPhysicalType", "ipNetToPhysicalState")
_ARP_OLD = ("ipNetToMediaPhysAddress", "ipNetToMediaType")


def has_credentials(target) -> bool:
    return bool(target is not None and target.username and target.auth_password
                and target.priv_password)  # fmt: skip


def read(client: sc.SnmpClient, notes: list[str]) -> dict:
    """Werte und Spalten eines Geraets (Positivliste); Hinweise nach `notes` (deutsch, Lauf)."""
    scalars = client.get("sysName", "sysDescr", "sysObjectID")
    tables: dict[str, dict] = {}

    def walk(*names: str, limit: int = sc.MAX_ROWS) -> None:
        for name in names:
            try:
                rows, cut = client.walk(name, limit)
            except sc.SnmpError as exc:  # Spalte mit Fehlermeldung: Teil leer, Rest weiter
                notes.append(f"{name}: SNMP-Fehler: {exc}")
                continue
            tables[name] = rows
            if cut and len(rows) < limit:
                notes.append(f"„{name}“: keine vollstaendige Antwort")
            elif cut and name not in {p for p, _ in _FDB} | {s for _, s in _FDB}:
                notes.append(f"{name}: mehr als {limit} Eintraege")

    walk(*oids.COLUMNS["IF-MIB"], "dot3StatsDuplexStatus", "dot1dBasePortIfIndex", *_Q_VLAN)
    if not tables.get("dot1qVlanStaticEgressPorts"):
        walk("dot1qVlanCurrentEgressPorts", "dot1qVlanCurrentUntaggedPorts")
    if not any(tables.get(n) for n in (*_Q_VLAN, "dot1qVlanCurrentEgressPorts")):
        walk(*_IEEE_VLAN)
    walk("dot1qVlanFdbId")
    limit = ssh_normalize.MAX_MACS
    for port_col, status_col in _FDB:
        walk(port_col, limit=limit)
        if tables.get(port_col):
            walk(status_col, limit=limit)
            if len(tables[port_col]) >= limit:
                notes.append(f"MAC-Tabelle: mehr als {limit} Eintraege, nur die ersten uebernommen")
            break
    walk(*_ARP)
    if not any(i.split(".")[1:2] in (["1"], ["3"]) for i in tables.get(_ARP[0]) or {}):
        walk(*_ARP_OLD)  # aeltere Agenten: IPv4 nur in ipNetToMediaTable
    walk(*_LLDP_LOCAL)
    if any(tables.get(n) for n in _LLDP_LOCAL):
        walk(*_LLDP_REMOTE)
    walk(*oids.COLUMNS["IEEE8023-LAG-MIB"], *oids.COLUMNS["CISCO-CDP-MIB"])
    entity = _entity(client, notes)

    missing = []
    if not (tables.get("ifName") or tables.get("ifDescr")):
        missing.append("IF-MIB")
    vlan_cols = (*_Q_VLAN, "dot1qVlanCurrentEgressPorts", *_IEEE_VLAN[1:])
    if not any(tables.get(n) for n in vlan_cols):
        missing.append("Q-BRIDGE-MIB")
    if not any(tables.get(p) for p, _ in _FDB):
        missing.append("BRIDGE-MIB")
    if not any(tables.get(n) for n in _LLDP_LOCAL):
        missing.append("LLDP-MIB")
    if missing:
        notes.append(f"ohne Antwort (Tabelle fehlt bzw. nicht in der View): {', '.join(missing)}")
    return {"scalars": scalars, "tables": tables, "entity": entity, "missing": missing}


def _entity(client: sc.SnmpClient, notes: list[str]) -> dict:
    """Modell, Seriennummer und Software des (ersten) Chassis aus der ENTITY-MIB - optional."""
    try:
        classes, _ = client.walk("entPhysicalClass", MAX_ENTITIES)
    except sc.SnmpError:
        return {}
    chassis = sorted((i for i, v in classes.items() if v == 3),
                     key=lambda i: [int(p) for p in i.split(".") if p.isdigit()])  # fmt: skip
    if not chassis:
        return {}
    out = {}
    for key, column in (("model", "entPhysicalModelName"), ("serial", "entPhysicalSerialNum"),
                        ("version", "entPhysicalSoftwareRev")):  # fmt: skip
        try:
            out[key] = client.get_row(column, chassis[0])
        except sc.SnmpError as exc:
            notes.append(f"{column}: SNMP-Fehler: {exc}")
    return out


def read_switch(entry: str, target) -> dict:
    """Ein Geraet: gelesene Tabellen und Hinweise (wie `cisco_switch.tasks.read_switch`)."""
    creds = sc.credentials(target)  # nur authPriv - sonst vor dem ersten Paket abbrechen
    notes: list[str] = []
    with sc.SnmpClient(entry, creds) as client:
        raw = read(client, notes)
    return {"host": entry, "platform": "snmp", "vendor": vendor(raw["scalars"].get("sysObjectID")),
            "outputs": raw, "notes": notes}  # fmt: skip


def gather(target, entries: list[str]) -> dict:
    """Alle Geraete des Quellsystems; `errors` je Adresse, die nicht gelesen wurde."""
    sc.credentials(target)  # Zugangsdaten einmal pruefen (sonst je Geraet derselbe Fehler)
    switches, errors = [], []

    @carry  # Threads: Meldungen in der Sprache des Aufrufers (im Lauf deutsch)
    def one(entry: str):
        try:
            return read_switch(entry, target), None
        except Exception as exc:  # noqa: BLE001 - je Geraet melden, die anderen weiter lesen
            return None, f"{entry}: {diagnose.device_error(exc, 'cisco_switch')}"

    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(entries))) as pool:
        for sw, err in pool.map(one, entries):
            if sw is not None:
                switches.append(sw)
            if err:
                errors.append(err)
    return {"switches": switches, "errors": errors}


def check(target, entries: list[str]) -> tuple[bool, str]:
    """Verbindung pruefen: je Geraet ein GET auf `sysName` mit SNMPv3 authPriv (kein Ingest)."""
    try:
        creds = sc.credentials(target)
    except sc.SnmpConfigError as exc:
        return False, str(exc)
    ok, lines = True, []
    for entry in entries:
        try:
            with sc.SnmpClient(entry, creds) as client:
                name = client.get("sysName").get("sysName")
        except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
            ok = False
            lines.append(f"{entry}: {diagnose.device_error(exc, 'cisco_switch', target, True)}")
            continue
        shown = (name.decode("utf-8", errors="replace") if isinstance(name, bytes) else name) or "?"
        lines.append(L(f"{entry}: erreichbar (SNMPv3 authPriv, sysName {shown})",
                       f"{entry}: reachable (SNMPv3 authPriv, sysName {shown})"))  # fmt: skip
    return ok, "; ".join(lines)
