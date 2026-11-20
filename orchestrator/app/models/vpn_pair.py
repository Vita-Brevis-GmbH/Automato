"""Kopplung zweier IPsec-Tunnel gelesener FortiGates, von Hand (1.23.0, ADR 0063 Nachtrag 1.23.0).

Anwender 2026-11-20: „Die VPN Verbindungen werden herausgelesen aber nicht verknüpft, also bleiben
die verbundenen Standorte weiterhin getrennt“ – „Einseitig ist es dynamisch, kann man dies nicht
koppeln? Manuell, wenn es dynamisch nicht geht?“

Laufzeitdaten je Mandant, in der Ansicht „VPN-Tunnel“ gepflegt (kein Schreibweg zu einem Geraet):

- `mode = coupled`: die beiden Tunnel sind **ein** Uebergang zwischen den Firewalls (von Hand);
- `mode = separated`: eine automatisch erkannte Kopplung gilt nicht (von Hand geloest).

Die Enden stehen mit ihrem festen Schluessel (Quellsystem, VDOM, Tunnel) da - die Kennungen der
Tunnel wechseln je Lauf (`topo_vpn_tunnel`). Normalisiert: `a` ist das kleinere Ende. Ein Ende,
das kein Lauf mehr liefert, bleibt stehen und wird als verwaist gezeigt (nie still geloescht).
Geloescht mit dem Mandanten bzw. einem der beiden Quellsysteme.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

COUPLED, SEPARATED = "coupled", "separated"
MODES = (COUPLED, SEPARATED)


class VpnPair(Base):
    __tablename__ = "vpn_pair"
    __table_args__ = (
        UniqueConstraint("tenant_id", "a_system_id", "a_context", "a_tunnel", "b_system_id",
                         "b_context", "b_tunnel", name="uq_vpn_pair_ends"),
    )  # fmt: skip

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    a_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"))
    a_context: Mapped[str] = mapped_column(String(200))
    a_tunnel: Mapped[str] = mapped_column(String(200))
    b_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"))
    b_context: Mapped[str] = mapped_column(String(200))
    b_tunnel: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(16), default=COUPLED)
    changed_by: Mapped[str] = mapped_column(String(200))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    @property
    def ends(self) -> tuple[tuple[int, str, str], tuple[int, str, str]]:
        return ((self.a_system_id, self.a_context, self.a_tunnel),
                (self.b_system_id, self.b_context, self.b_tunnel))  # fmt: skip
