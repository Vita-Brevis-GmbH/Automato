"""Zone - gruppiert mehrere Subnetze (DMZ, Server, Internet, Edge, ...).

Zonen-Zugehoerigkeit wird im SoT am Subnetz gepflegt und von Automato gelesen
(ADR 0004). tenant_id ist nullable: manche Zonen (Internet, Management) sind
mandantenuebergreifend geteilt.
"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Zone(Base):
    __tablename__ = "zone"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
