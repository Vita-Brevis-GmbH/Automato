from dataclasses import dataclass, field

from app.auth import capabilities as caps


@dataclass(frozen=True)
class Principal:
    """Authentifizierter Aufrufer.

    tenant_ids=None bedeutet "alle Mandanten" (nur Admin/Global-Admin-Gesamtsicht,
    ADR-0004-Ergaenzung). Eine Liste schraenkt auf diese Mandanten (Gruppengesellschaften)
    ein.

    customer_ids (ADR 0018 Schritt 3): None = Betreuer (sieht die ganze Umgebung). Ein Tupel -
    auch ein leeres - macht den Aufrufer zum Kunden-Benutzer: nur die freigegebenen
    Lese-Endpunkte, eingeschraenkt auf den Ausschnitt dieser Kunden samt geteiltem Teil; ohne
    Kunden sieht er nichts (fail-closed). Seit 0.53.0 auch ueber die Zuordnung in Automato
    (`customer_user`, `auth/assignments.py`).

    customer_roles (feinere Rollen, ADR 0018 Ergaenzung): Kunden-Rolle je Kunde
    (`auth/capabilities.py`: viewer, triage, editor, customer_admin); daraus die Capabilities.
    Ein Kunde
    ohne Eintrag gibt keine Rechte (fail-closed). `roles` bleibt fuer die Anzeige und die
    Pruefung der Betreuer.
    """

    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)
    tenant_ids: tuple[int, ...] | None = None
    customer_ids: tuple[int, ...] | None = None
    # Kennungen fuer die Zuordnung Benutzer -> Kunde in Automato (0.53.0): sub,
    # Benutzername, E-Mail - klein geschrieben
    logins: tuple[str, ...] = ()
    # woher die Kunden kommen: claim | automato | claim+automato (None = Betreuer)
    customer_source: str | None = None
    # (Kunde, Kunden-Rolle) - nur fuer Kunden aus customer_ids
    customer_roles: tuple[tuple[int, str], ...] = ()

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles and self.customer_ids is None

    @property
    def is_customer(self) -> bool:
        return self.customer_ids is not None

    def customer_role(self, customer_id: int) -> str | None:
        """Kunden-Rolle fuer diesen Kunden (None = keine)."""
        if not self.is_customer or customer_id not in (self.customer_ids or ()):
            return None
        return caps.highest(r for c, r in self.customer_roles if c == customer_id)

    @property
    def capabilities(self) -> frozenset[str]:
        """Capabilities ueber alle Kunden (Kunden-Benutzer); Betreuer: leer (Rollen gelten)."""
        if not self.is_customer:
            return frozenset()
        out: set[str] = set()
        for cid in self.customer_ids or ():
            out |= caps.capabilities(self.customer_role(cid))
        return frozenset(out)

    def can(self, capability: str, customer_id: int | None = None) -> bool:
        """Kunden-Benutzer: Recht fuer diesen Kunden bzw. fuer mindestens einen."""
        if customer_id is None:
            return capability in self.capabilities
        return capability in caps.capabilities(self.customer_role(customer_id))

    def customers_with(self, capability: str) -> tuple[int, ...]:
        """Kunden, fuer die der Kunden-Benutzer dieses Recht hat."""
        return tuple(c for c in self.customer_ids or () if self.can(capability, c))
