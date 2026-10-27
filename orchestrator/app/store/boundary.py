"""Firmengrenze (ADR 0032): wo ein Weg die Firma verlaesst.

Seit Automato auch die Aussen- und Core-Router liest (Switches & Router, Kontext-Art `l3`), geht
ein Weg bis an deren Ausgang. Dort - am Next-Hop bzw. BGP-Nachbarn, der **keinem gelesenen Geraet
gehoert** - liegt die Firmengrenze, wenn einer dieser Gruende passt (in dieser Reihenfolge):

- `ebgp`: der Next-Hop ist BGP-Nachbar des Knotens mit einem **fremden AS**. Eigene AS sind die
  lokalen AS der gelesenen BGP-Konfigurationen (`attrs.local_as` der Router), die AS der
  iBGP-Nachbarn und die AS von Nachbarn, die selbst gelesene Geraete sind. Ein privates AS
  (64512-65534, 4200000000-4294967294) steht dabei (`private_as`) - es kann auch ein nicht
  gelesener eigener Router sein.
- `exit`: eine von Hand erfasste Anbindung der Art Internet, MPLS bzw. Partner.
- `default`: die Default-Route geht ueber einen Next-Hop mit oeffentlicher Adresse bzw. ueber ein
  WAN-Interface (FortiGate-Rolle `wan`) - Richtung Provider.

Sonst endet der Weg wie bisher (Next-Hop ohne gelesenes Geraet, „extern“), ohne Grenze. Nur
lesend: alles stammt aus den gelesenen Routing-Daten und den Anbindungen von Hand.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.i18n import L
from app.models import InvInterface, TopoExit, TopoPeer

if TYPE_CHECKING:  # nur fuer Typen - paths importiert dieses Modul
    from app.store.paths import Graph, Key

EXIT_KINDS = ("internet", "mpls", "partner")  # Anbindungen von Hand, die aus der Firma fuehren
REASONS = ("ebgp", "exit", "default")
# keine oeffentlichen Adressen (Dokumentationsnetze gelten in Beispielen als oeffentlich)
_INTERNAL = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "0.0.0.0/8", "fc00::/7", "fe80::/10", "::1/128",
))  # fmt: skip


def public(ip: str | None) -> bool:
    """Oeffentliche Adresse (nicht RFC 1918, CGNAT, Loopback, Link-local, ULA)."""
    try:
        addr = ipaddress.ip_address(str(ip or "").split("%", 1)[0])
    except ValueError:
        return False
    return not any(addr.version == n.version and addr in n for n in _INTERNAL)


def private_as(asn: int | None) -> bool:
    return asn is not None and (64512 <= asn <= 65534 or 4200000000 <= asn <= 4294967294)


class Boundary:
    """Firmengrenze fuer die Knoten eines Graphen: BGP-Nachbarn je Knoten, eigene AS, Rollen
    der Interfaces - einmal geladen."""

    def __init__(self, g: Graph):
        self.g = g
        self._peers: dict[Key, dict[str, TopoPeer]] | None = None
        self._own: set[int] | None = None
        self._roles: dict[tuple[int, str], str] | None = None

    def peers(self) -> dict[Key, dict[str, TopoPeer]]:
        if self._peers is None:
            out: dict = defaultdict(dict)
            for p in self.g.session.scalars(
                select(TopoPeer).where(TopoPeer.tenant_id == self.g.tenant_id)
            ):
                key = (p.source_system_id, p.context)
                if key in self.g.nodes and p.peer_ip:
                    out[key].setdefault(str(p.peer_ip).split("%", 1)[0], p)
            self._peers = out
        return self._peers

    def own_as(self) -> set[int]:
        """AS der Firma: lokale AS gelesener BGP-Konfigurationen, iBGP-Nachbarn und Nachbarn,
        die selbst gelesene Geraete sind."""
        if self._own is None:
            own: set[int] = set()
            owners = self.g.owners()
            for key, by_ip in self.peers().items():
                for ip, p in by_ip.items():
                    attrs = p.attrs or {}
                    if isinstance(attrs.get("local_as"), int):
                        own.add(attrs["local_as"])
                    if p.remote_as is None:
                        continue
                    if attrs.get("type") == "ibgp" or any(k != key for k, _i, _z in
                                                          owners.get(ip, [])):  # fmt: skip
                        own.add(int(p.remote_as))
            self._own = own
        return self._own

    def role(self, source_system_id: int, interface: str | None) -> str | None:
        if self._roles is None:
            sources = {k[0] for k in self.g.nodes}
            rows = self.g.session.execute(
                select(InvInterface.source_system_id, InvInterface.name, InvInterface.role).where(
                    InvInterface.source_system_id.in_(sources), InvInterface.role.is_not(None)
                )
            ) if sources else []  # fmt: skip
            self._roles = {(sid, name): role for sid, name, role in rows}
        return self._roles.get((source_system_id, interface or ""))

    def judge(self, key: Key, ip: str | None, interface: str | None = None,
              zone: str | None = None, exit_row: TopoExit | None = None,
              default: bool = False) -> dict | None:  # fmt: skip
        """Grenze am Ausgang des Knotens `key` ueber den Next-Hop `ip`, der keinem gelesenen
        Geraet gehoert (Aufrufer prueft das) - mit Grund, AS, Knoten und Interface; sonst None."""
        node = self.g.nodes[key]
        base = {"key": node.key, "label": node.label, "interface": interface, "zone": zone,
                "next_hop": ip}  # fmt: skip
        base = {k: v for k, v in base.items() if v is not None}
        peer = self.peers().get(key, {}).get(ip or "")
        if peer is not None and peer.remote_as is not None and peer.remote_as not in self.own_as():
            asn = int(peer.remote_as)
            text = L(
                f"Firmengrenze: eBGP zu AS {asn} ({ip})",
                f"Company boundary: eBGP to AS {asn} ({ip})",
            )
            out = {**base, "reason": "ebgp", "as": asn, "state": peer.state, "text": text}
            if private_as(asn):
                out["private_as"] = True
            return out
        if exit_row is not None and exit_row.kind in EXIT_KINDS:
            from app.store.paths import _exit_label

            name = _exit_label(exit_row)
            return {**base, "reason": "exit", "text": L(f"Firmengrenze: {name}",
                                                        f"Company boundary: {name}"),
                    "exit": {"id": exit_row.id, "name": exit_row.name,
                             "kind": exit_row.kind}}  # fmt: skip
        if default and ip and (public(ip) or self.role(key[0], interface) == "wan"):
            text = L(f"Firmengrenze: Default-Route Richtung Provider über {ip}",
                     f"Company boundary: default route towards the provider via {ip}")  # fmt: skip
            return {**base, "reason": "default", "text": text}
        return None
