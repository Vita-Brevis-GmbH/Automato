from dataclasses import dataclass, field


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

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles and self.customer_ids is None

    @property
    def is_customer(self) -> bool:
        return self.customer_ids is not None
