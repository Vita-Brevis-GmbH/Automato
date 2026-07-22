"""Kanonisches Service-Objekt, global dedupliziert (ADR 0004).

Vendor-Definitionen (FortiGate "HTTPS", ACI-Filter) werden auf einen kanonischen
Service abgebildet (z. B. tcp/443). Dual-Stack ueber proto (icmp/icmpv6) + family.
TCP-Flags/established werden nicht gespeichert (aus Richtung ableitbar).
"""

import enum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Proto(enum.StrEnum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    ICMPV6 = "icmpv6"
    IP = "ip"  # generisches IP-Protokoll (Protokollnummer in port_start abgelegt)


class ServiceObject(Base):
    __tablename__ = "service_object"

    id: Mapped[int] = mapped_column(primary_key=True)
    proto: Mapped[Proto] = mapped_column(Enum(Proto, native_enum=False, length=8))
    port_start: Mapped[int | None] = mapped_column(nullable=True)
    port_end: Mapped[int | None] = mapped_column(nullable=True)
    icmp_type: Mapped[int | None] = mapped_column(nullable=True)
    # Kanonischer, eindeutiger Name zur Deduplizierung, z. B. "tcp/443", "icmp/echo"
    canonical_name: Mapped[str] = mapped_column(String(64), unique=True)
