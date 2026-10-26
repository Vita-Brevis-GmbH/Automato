"""Sicht eines Kunden auf die Umgebung (ADR 0018, Schritte 2 und 3).

Ein Kunde sieht seinen Ausschnitt (`customer_member`). Der geteilte Teil der Umgebung (Kunden
mit `shared`, z. B. Tenant `common`) gibt Zusammenhang: seine EPGs, Netze und Objekte zaehlen
fuer alle Kunden. Regeln, NAT, Findings und VMs zaehlen dagegen nur aus den eigenen Kunden - eine
Regel von Kunde B zum geteilten DNS betrifft Kunde A nicht („nur die Regeln, die ihn betreffen“).
`View.ids(kind)` liefert je Art die IDs als SQL-Unterabfrage - die Listen und Zaehler filtern
damit per SQL (`Rule.id.in_(view.ids("rule"))`).

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

# Angaben in Findings, die Regeln ausserhalb des Ausschnitts nennen koennten (Kunden-Sicht)
FINDING_DETAIL_HIDDEN = ("rule_names", "rules", "firewall_objects")

# Arten nur aus den eigenen Kunden (ohne geteilten Teil)
OWN_KINDS = frozenset({"rule", "nat", "finding", "vm"})


@dataclass(frozen=True)
class View:
    """Kunden einer Sicht: `own` die gewaehlten bzw. eigenen, `customer_ids` samt geteilter."""

    tenant_id: int
    customer_ids: tuple[int, ...]
    own: tuple[int, ...]

    def _of(self, kind: str) -> tuple[int, ...]:
        return self.own if kind in OWN_KINDS else self.customer_ids

    def ids(self, kind: str) -> Select:
        """IDs einer Art (epg, object, rule, vm, nat, finding) als Unterabfrage."""
        return select(CustomerMember.ref_id).where(
            CustomerMember.customer_id.in_(self._of(kind)),
            CustomerMember.kind == kind,
            CustomerMember.ref_id.is_not(None),
        )

    def contains(self, session: Session, kind: str, ref_id: int) -> bool:
        """Gehoert das Element (Art, ID) zum Ausschnitt?"""
        hit = session.scalar(
            select(CustomerMember.id)
            .where(
                CustomerMember.customer_id.in_(self._of(kind)),
                CustomerMember.kind == kind,
                CustomerMember.ref_id == ref_id,
            )
            .limit(1)
        )
        return hit is not None

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
    """Sicht auf die Kunden des Mandanten samt geteiltem Teil (fuer EPGs, Netze, Objekte).
    Kunden anderer Mandanten zaehlen nicht (LookupError, wenn keiner passt)."""
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
    return View(tenant_id, tuple(sorted(own | shared)), tuple(sorted(own)))
