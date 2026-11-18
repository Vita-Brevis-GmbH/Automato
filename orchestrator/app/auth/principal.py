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
    # Benutzer eines Kunden-Realms (1.13.0): (Kurzname, Anzeigename) des Kunden im Kunden-Portal
    realm_customer: tuple[str, str] | None = None
    # Kunden, deren Benutzer und Rollen das Cockpit fuehrt (1.14.0, ADR 0058): hier vergibt der
    # Kunden-Benutzer keine Rechte (kein `customer_users.manage`, auch nicht als Kunden-Admin)
    cockpit_customers: tuple[int, ...] = ()
    # Benutzer eines Kunden-Realms, dem das Cockpit keine Rolle gibt (1.14.0, `benutzer`): keine
    # Rechte, eine klare Meldung (wie ohne Zuordnung)
    realm_role_missing: bool = False
    # Mandanten-Rolle (ADR 0068): Benutzer eines Kunden-Realms mit `customer_operator` bzw.
    # `customer_admin` im eigenen Mandanten (`cockpit_tenant`) - Betreuer-Sicht ohne Kunden-Filter,
    # nur `tenant_ids` (genau dieser Mandant), nie Admin; Endpunkte nur nach der Positivliste
    # `auth/tenant_routes.py` (`deps._tenant_gate`)
    tenant_role: str | None = None
    # Benutzer eines Kunden-Realms, dessen Name auf `@betrieb` endet (ADR 0068): nur fuer die
    # Anzeige des Links „Ganze Plattform“ - gibt keine Rechte
    betreiber: bool = False

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles and self.customer_ids is None and self.tenant_role is None

    @property
    def is_tenant_user(self) -> bool:
        """Mandanten-Benutzer (ADR 0068): Kunden-Realm im eigenen Mandanten, Operator bzw. Admin."""
        return self.tenant_role is not None

    @property
    def tenant_capabilities(self) -> frozenset[str]:
        """Rechte im eigenen Mandanten (leer = kein Mandanten-Benutzer)."""
        return caps.tenant_capabilities(self.tenant_role) if self.tenant_ids else frozenset()

    @property
    def assignment_missing(self) -> bool:
        """Benutzer eines Kunden-Realms, dessen Kunde in Automato noch keinen Mandanten bzw.
        keine Kunden hat (1.13.0): keine Rechte (fail-closed), aber eine klare Meldung. Ohne Rolle
        aus dem Cockpit (1.14.0) gilt stattdessen `realm_role_missing`."""
        if self.tenant_role is not None:  # Mandanten-Benutzer: zugeordnet, wenn der Mandant steht
            return not self.tenant_ids
        return (self.realm_customer is not None and not self.realm_role_missing
                and not (self.tenant_ids and self.customer_ids))  # fmt: skip

    @property
    def without_rights(self) -> bool:
        """Kunden-Realm ohne Zuordnung bzw. ohne Rolle aus dem Cockpit: nur `/auth/me`."""
        return self.assignment_missing or self.realm_role_missing

    @property
    def is_customer(self) -> bool:
        return self.customer_ids is not None

    def customer_role(self, customer_id: int) -> str | None:
        """Kunden-Rolle fuer diesen Kunden (None = keine)."""
        if not self.is_customer or customer_id not in (self.customer_ids or ()):
            return None
        return caps.highest(r for c, r in self.customer_roles if c == customer_id)

    def _caps(self, customer_id: int) -> frozenset[str]:
        """Capabilities fuer diesen Kunden - bei Kunden aus dem Cockpit (1.14.0) ohne das
        Zuordnen von Kunden-Benutzern."""
        out = caps.capabilities(self.customer_role(customer_id))
        return out - {caps.USERS_MANAGE} if customer_id in self.cockpit_customers else out

    @property
    def capabilities(self) -> frozenset[str]:
        """Capabilities ueber alle Kunden (Kunden-Benutzer); Betreuer: leer (Rollen gelten)."""
        if not self.is_customer:
            return frozenset()
        out: set[str] = set()
        for cid in self.customer_ids or ():
            out |= self._caps(cid)
        return frozenset(out)

    def can(self, capability: str, customer_id: int | None = None) -> bool:
        """Kunden-Benutzer: Recht fuer diesen Kunden bzw. fuer mindestens einen."""
        if customer_id is None:
            return capability in self.capabilities
        return capability in self._caps(customer_id)

    def customers_with(self, capability: str) -> tuple[int, ...]:
        """Kunden, fuer die der Kunden-Benutzer dieses Recht hat."""
        return tuple(c for c in self.customer_ids or () if self.can(capability, c))
