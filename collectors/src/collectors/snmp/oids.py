"""Positivliste der SNMP-Abfragen (ADR 0033): nur diese Werte bzw. Tabellen-Spalten.

Gelesen wird mit GET (einzelne Werte) und GETBULK (Spalten einer Tabelle, Fortsetzung nur
innerhalb der Spalte). Ein SET gibt es im Collector nicht; jede Abfrage ausserhalb der Liste
bricht **vor dem Senden** ab (`ReadOnlyViolation`, `check`). Die Spalten sind nach MIB geordnet;
`OPTIONAL` fehlt auf vielen Geraeten ohne Hinweis, `CORE` fehlt mit Hinweis im Lauf.
"""

from sot_gateway.readonly import ReadOnlyViolation

from collectors.common.i18n import L

# Einzelne Werte (GET): SNMPv2-MIB system
SCALARS: dict[str, str] = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
}

# Spalten (GETBULK) je MIB: Name -> OID der Spalte (Index = Rest der OID)
COLUMNS: dict[str, dict[str, str]] = {
    "IF-MIB": {
        "ifDescr": "1.3.6.1.2.1.2.2.1.2",
        "ifType": "1.3.6.1.2.1.2.2.1.3",
        "ifPhysAddress": "1.3.6.1.2.1.2.2.1.6",
        "ifAdminStatus": "1.3.6.1.2.1.2.2.1.7",
        "ifOperStatus": "1.3.6.1.2.1.2.2.1.8",
        "ifName": "1.3.6.1.2.1.31.1.1.1.1",
        "ifHighSpeed": "1.3.6.1.2.1.31.1.1.1.15",
        "ifAlias": "1.3.6.1.2.1.31.1.1.1.18",
    },
    "EtherLike-MIB": {"dot3StatsDuplexStatus": "1.3.6.1.2.1.10.7.2.1.19"},
    "ENTITY-MIB": {"entPhysicalClass": "1.3.6.1.2.1.47.1.1.1.1.5"},
    "BRIDGE-MIB": {
        "dot1dBasePortIfIndex": "1.3.6.1.2.1.17.1.4.1.2",
        "dot1dTpFdbPort": "1.3.6.1.2.1.17.4.3.1.2",
        "dot1dTpFdbStatus": "1.3.6.1.2.1.17.4.3.1.3",
    },
    "Q-BRIDGE-MIB": {
        "dot1qTpFdbPort": "1.3.6.1.2.1.17.7.1.2.2.1.2",
        "dot1qTpFdbStatus": "1.3.6.1.2.1.17.7.1.2.2.1.3",
        "dot1qVlanFdbId": "1.3.6.1.2.1.17.7.1.4.2.1.3",
        "dot1qVlanCurrentEgressPorts": "1.3.6.1.2.1.17.7.1.4.2.1.4",
        "dot1qVlanCurrentUntaggedPorts": "1.3.6.1.2.1.17.7.1.4.2.1.5",
        "dot1qVlanStaticName": "1.3.6.1.2.1.17.7.1.4.3.1.1",
        "dot1qVlanStaticEgressPorts": "1.3.6.1.2.1.17.7.1.4.3.1.2",
        "dot1qVlanStaticUntaggedPorts": "1.3.6.1.2.1.17.7.1.4.3.1.4",
        "dot1qPvid": "1.3.6.1.2.1.17.7.1.4.5.1.1",
    },
    # IEEE 802.1Q-2011: wie Q-BRIDGE-MIB, mit der Komponente als erstem Teil des Index
    "IEEE8021-Q-BRIDGE-MIB": {
        "ieee8021BridgeBasePortIfIndex": "1.3.111.2.802.1.1.2.1.1.4.1.3",
        "ieee8021QBridgeTpFdbPort": "1.3.111.2.802.1.1.4.1.2.2.1.2",
        "ieee8021QBridgeTpFdbStatus": "1.3.111.2.802.1.1.4.1.2.2.1.3",
        "ieee8021QBridgeVlanStaticName": "1.3.111.2.802.1.1.4.1.4.3.1.3",
        "ieee8021QBridgeVlanStaticEgressPorts": "1.3.111.2.802.1.1.4.1.4.3.1.4",
        "ieee8021QBridgeVlanStaticUntaggedPorts": "1.3.111.2.802.1.1.4.1.4.3.1.6",
        "ieee8021QBridgePvid": "1.3.111.2.802.1.1.4.1.4.5.1.1",
    },
    "IP-MIB": {
        "ipNetToPhysicalPhysAddress": "1.3.6.1.2.1.4.35.1.4",
        "ipNetToPhysicalType": "1.3.6.1.2.1.4.35.1.6",
        "ipNetToPhysicalState": "1.3.6.1.2.1.4.35.1.7",
        "ipNetToMediaPhysAddress": "1.3.6.1.2.1.4.22.1.2",
        "ipNetToMediaType": "1.3.6.1.2.1.4.22.1.4",
    },
    "LLDP-MIB": {
        "lldpLocPortIdSubtype": "1.0.8802.1.1.2.1.3.7.1.2",
        "lldpLocPortId": "1.0.8802.1.1.2.1.3.7.1.3",
        "lldpLocPortDesc": "1.0.8802.1.1.2.1.3.7.1.4",
        "lldpRemChassisIdSubtype": "1.0.8802.1.1.2.1.4.1.1.4",
        "lldpRemChassisId": "1.0.8802.1.1.2.1.4.1.1.5",
        "lldpRemPortIdSubtype": "1.0.8802.1.1.2.1.4.1.1.6",
        "lldpRemPortId": "1.0.8802.1.1.2.1.4.1.1.7",
        "lldpRemPortDesc": "1.0.8802.1.1.2.1.4.1.1.8",
        "lldpRemSysName": "1.0.8802.1.1.2.1.4.1.1.9",
        "lldpRemSysDesc": "1.0.8802.1.1.2.1.4.1.1.10",
        "lldpRemSysCapSupported": "1.0.8802.1.1.2.1.4.1.1.11",
        "lldpRemSysCapEnabled": "1.0.8802.1.1.2.1.4.1.1.12",
        "lldpRemManAddrIfSubtype": "1.0.8802.1.1.2.1.4.2.1.3",
    },
    "IEEE8023-LAG-MIB": {
        "dot3adAggPortSelectedAggID": "1.2.840.10006.300.43.1.2.1.1.12",
        "dot3adAggPortAttachedAggID": "1.2.840.10006.300.43.1.2.1.1.13",
    },
    "CISCO-CDP-MIB": {
        "cdpCacheAddressType": "1.3.6.1.4.1.9.9.23.1.2.1.1.3",
        "cdpCacheAddress": "1.3.6.1.4.1.9.9.23.1.2.1.1.4",
        "cdpCacheDeviceId": "1.3.6.1.4.1.9.9.23.1.2.1.1.6",
        "cdpCacheDevicePort": "1.3.6.1.4.1.9.9.23.1.2.1.1.7",
        "cdpCachePlatform": "1.3.6.1.4.1.9.9.23.1.2.1.1.8",
        "cdpCacheCapabilities": "1.3.6.1.4.1.9.9.23.1.2.1.1.9",
    },
}

# Einzelne Zeilen (GET auf Spalte.Index): Modell, Seriennummer und Software des Chassis
INSTANCE_COLUMNS: dict[str, str] = {
    "entPhysicalSoftwareRev": "1.3.6.1.2.1.47.1.1.1.1.10",
    "entPhysicalSerialNum": "1.3.6.1.2.1.47.1.1.1.1.11",
    "entPhysicalModelName": "1.3.6.1.2.1.47.1.1.1.1.13",
}

# Fehlen ohne Hinweis (herstellerspezifisch, Rueckfall oder nur Zusatz)
OPTIONAL = frozenset({"EtherLike-MIB", "ENTITY-MIB", "IEEE8021-Q-BRIDGE-MIB", "IEEE8023-LAG-MIB",
                      "CISCO-CDP-MIB"})  # fmt: skip

WALKS: dict[str, str] = {name: oid for cols in COLUMNS.values() for name, oid in cols.items()}
MIB_OF: dict[str, str] = {name: mib for mib, cols in COLUMNS.items() for name in cols}
_GETS = frozenset(SCALARS.values())
_WALK_OIDS = frozenset(WALKS.values())


def _inside(oid: str, base: str) -> bool:
    return oid.startswith(base + ".")


def allowed(op: str, oid: str, base: str | None = None) -> bool:
    """Ist die Abfrage erlaubt? `get`: ein Wert der Liste bzw. eine Zeile von
    `INSTANCE_COLUMNS`; `walk`: Beginn einer Spalte; `next`: Fortsetzung innerhalb der Spalte
    `base`. Alles andere (auch jedes SET) nicht."""
    if op == "get":
        return oid in _GETS or any(_inside(oid, c) for c in INSTANCE_COLUMNS.values())
    if op == "walk":
        return oid in _WALK_OIDS
    if op == "next":
        return base in _WALK_OIDS and (oid == base or _inside(oid, base))
    return False


def check(op: str, oid: str, base: str | None = None) -> None:
    """Vor dem Senden: nur erlaubte Abfragen, sonst `ReadOnlyViolation`."""
    if not allowed(op, oid, base):
        raise ReadOnlyViolation(
            L(
                f"nur lesend: SNMP-Abfrage „{oid}“ nicht in der Positivliste",
                f"read-only: SNMP query “{oid}” not in the allow list",
            )
        )
