"""Sicht eines Kunden auf die Umgebung (ADR 0018, Schritte 2 und 3).

Ein Kunde sieht seinen Ausschnitt (`customer_member`) und den geteilten Teil der Umgebung
(Kunden mit `shared`). `View.ids(kind)` liefert je Art die IDs als SQL-Unterabfrage - die
Listen und Zaehler filtern damit per SQL (`Rule.id.in_(view.ids("rule"))`).

Schritt 2 (0.32.0): Betreuer waehlen einen Kunden als Filter (`customer_id`). Schritt 3
(0.33.0): Kunden-Benutzer sind immer auf ihre Kunden eingeschraenkt.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    Customer,
    CustomerMember,
    DeviceObject,
    Epg,
    NetworkObject,
    RuleObjectUsage,
)


@dataclass(frozen=True)
class View:
    """Kunden (inkl. geteilter) einer Sicht."""

    tenant_id: int
    customer_ids: tuple[int, ...]

    def ids(self, kind: str) -> Select:
        """IDs einer Art (epg, object, rule, vm, nat, finding) als Unterabfrage."""
        return select(CustomerMember.ref_id).where(
            CustomerMember.customer_id.in_(self.customer_ids),
            CustomerMember.kind == kind,
            CustomerMember.ref_id.is_not(None),
        )

    def nets(self, session: Session) -> list[str]:
        """Netze des Kunden (CIDR)."""
        return sorted(
            set(
                session.scalars(
                    select(CustomerMember.value).where(
                        CustomerMember.customer_id.in_(self.customer_ids),
                        CustomerMember.kind == "net",
                    )
                )
            )
        )

    def device_objects(self) -> Select:
        """Firewall-Objekte des Kunden: zu einem Werte-Objekt des Kunden bzw. verwendet von
        einer Regel des Kunden (auch Gruppen und Dienste)."""
        by_value = select(DeviceObject.id).where(
            DeviceObject.tenant_id == self.tenant_id,
            DeviceObject.network_object_id.in_(self.ids("object")),
        )
        by_rule = select(RuleObjectUsage.device_object_id).where(
            RuleObjectUsage.rule_id.in_(self.ids("rule"))
        )
        return by_value.union(by_rule)

    def applications(self, session: Session) -> Callable[[Application], bool]:
        """Gehoert eine Applikation zum Kunden? Ein Mitglied liegt in seinem Ausschnitt: AP
        mit einer EPG des Kunden, die EPG selbst bzw. ein Server-Objekt des Kunden."""
        epgs = list(session.scalars(select(Epg).where(Epg.id.in_(self.ids("epg")))))
        aps = {f"{e.aci_tenant}/{e.ap}" for e in epgs if e.ap}
        dns = {e.dn for e in epgs if e.dn}
        names = set(
            session.scalars(
                select(NetworkObject.name).where(NetworkObject.id.in_(self.ids("object")))
            )
        )
        refs = {"ap": aps, "epg": dns, "host": names}

        def mine(app: Application) -> bool:
            return any(m.ref in refs.get(m.kind, set()) for m in app.members)

        return mine


def view(session: Session, tenant_id: int, customer_ids: list[int] | tuple[int, ...]) -> View:
    """Sicht auf die Kunden des Mandanten samt geteiltem Teil. Kunden anderer Mandanten
    zaehlen nicht (LookupError, wenn keiner passt)."""
    own = set(
        session.scalars(
            select(Customer.id).where(
                Customer.tenant_id == tenant_id, Customer.id.in_(list(customer_ids))
            )
        )
    )
    if not own:
        raise LookupError("customer not found")
    shared = set(
        session.scalars(
            select(Customer.id).where(Customer.tenant_id == tenant_id, Customer.shared.is_(True))
        )
    )
    return View(tenant_id, tuple(sorted(own | shared)))
