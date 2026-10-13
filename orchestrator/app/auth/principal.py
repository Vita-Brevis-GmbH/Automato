from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """Authentifizierter Aufrufer.

    tenant_ids=None bedeutet "alle Mandanten" (nur Admin/Global-Admin-Gesamtsicht,
    ADR-0004-Ergaenzung). Eine Liste schraenkt auf diese Mandanten (Gruppengesellschaften)
    ein.
    """

    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)
    tenant_ids: tuple[int, ...] | None = None

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles
