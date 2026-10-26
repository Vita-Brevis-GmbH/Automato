"""Externe Adressen (0.76.1, Rückmeldung des Anwenders): öffentliche Netze und Hosts, die keinem
gemanagten Netz angehören, erwartet niemand im IPAM – kein Befund „nicht im IPAM“.

Gemanagt sind die Netze der gelesenen Geräte (`inv_subnet`: Interface-Netze der Firewalls inkl.
WAN, ACI-BD/-EPG-Subnetze, Interfaces der Router). Eine öffentliche Adresse in einem solchen Netz
(z. B. im Interface-Netz am WAN-Anschluss) bleibt ein Befund, ebenso private Adressen, VIPs und
Objekte ohne Wert. Werte-Objekte, die so extern sind, stehen auf `expected_external`; der Stand
wird nach jedem Abgleich neu berechnet (ein neues Interface-Netz macht sie wieder „gemanagt“).
"""

import bisect
import ipaddress

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvSubnet, MatchStatus, NetworkObject, ObjectClass

# Werte-Objekte (IP, Netz, Bereich); VIPs sind eigene, publizierte Adressen und bleiben Befund
VALUE_CLASSES = (ObjectClass.HOST, ObjectClass.NETWORK, ObjectClass.RANGE)


def _bounds(value: str) -> tuple[int, int, int, bool] | None:
    """(Familie, erste, letzte Adresse, öffentlich) eines Werts (IP, CIDR, Bereich a-b)."""
    start, sep, end = value.partition("-")
    try:
        if sep:
            a, b = ipaddress.ip_address(start.strip()), ipaddress.ip_address(end.strip())
            if a.version != b.version or int(a) > int(b):
                return None
        else:
            net = ipaddress.ip_network(value.strip(), strict=False)
            a, b = net.network_address, net.broadcast_address
    except ValueError:
        return None
    return a.version, int(a), int(b), a.is_global and b.is_global


class Managed:
    """Gemanagte Netze des Mandanten, je Familie als zusammengefasste, sortierte Bereiche."""

    def __init__(self, prefixes) -> None:
        spans: dict[int, list[tuple[int, int]]] = {4: [], 6: []}
        for prefix in prefixes:
            b = _bounds(prefix) if prefix else None
            if b is not None and "-" not in prefix:
                spans[b[0]].append((b[1], b[2]))
        self._lo: dict[int, list[int]] = {}
        self._hi: dict[int, list[int]] = {}
        for version, items in spans.items():
            merged: list[list[int]] = []
            for lo, hi in sorted(items):
                if merged and lo <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            self._lo[version] = [m[0] for m in merged]
            self._hi[version] = [m[1] for m in merged]

    def overlaps(self, version: int, lo: int, hi: int) -> bool:
        los, his = self._lo.get(version, []), self._hi.get(version, [])
        i = bisect.bisect_right(los, hi) - 1  # letzter Bereich, der vor dem Ende beginnt
        return i >= 0 and his[i] >= lo

    def external(self, value: str | None) -> bool:
        """Öffentlich und in keinem gemanagten Netz."""
        b = _bounds(value) if value else None
        return b is not None and b[3] and not self.overlaps(b[0], b[1], b[2])


def managed(session: Session, tenant_id: int) -> Managed:
    return Managed(
        session.scalars(select(InvSubnet.prefix).where(InvSubnet.tenant_id == tenant_id)).all()
    )


def apply(session: Session, tenant_id: int, nets: Managed | None = None) -> int:
    """Werte-Objekte ohne IPAM-Treffer: extern -> `expected_external`, sonst (wieder)
    `unmatched`. Rückgabe: Anzahl externer Objekte."""
    nets = nets or managed(session, tenant_id)
    count = 0
    for obj in session.scalars(
        select(NetworkObject).where(
            NetworkObject.tenant_id == tenant_id,
            NetworkObject.value.is_not(None),
            NetworkObject.object_class.in_(VALUE_CLASSES),
            NetworkObject.match_status.in_([MatchStatus.UNMATCHED, MatchStatus.EXPECTED_EXTERNAL]),
        )
    ):
        outside = nets.external(obj.value)
        count += outside
        status = MatchStatus.EXPECTED_EXTERNAL if outside else MatchStatus.UNMATCHED
        if obj.match_status is not status:
            obj.match_status = status
    session.flush()
    return count
