"""Abgeleiteter Regelindex (R18, Dev-Befund „grosse Regelwerke").

Je Regel einmal beim Einlesen berechnet statt bei jeder Anfrage: Suchtext, Listenzeile,
welche Firewall-Objekte die Regel verwendet (direkt oder ueber Gruppen) und welche IP-Bereiche
sie abdeckt. Listen, Suche, Objekt-Detail, NAT und Applikations-Doku lesen daraus per SQL.
Wird je Quellsystem nach dessen Lauf neu aufgebaut (`app.store.rule_index`) und nach einem
Referenzlauf fuer den ganzen Mandanten (DNS-Namen aus dem IPAM im Suchtext).
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# IP als Hex fester Breite (32 Zeichen, IPv4 und IPv6): Vergleich als Text = numerisch.
# Postgres vergleicht byteweise (Collation "C"), SQLite ohnehin.
IpHex = String(32).with_variant(postgresql.VARCHAR(32, collation="C"), "postgresql")


class RuleIndex(Base):
    __tablename__ = "rule_index"

    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rule.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    source_system_id: Mapped[int] = mapped_column(Integer, index=True)
    # Suchtext (klein): Regel, Objekte inkl. Gruppenmitglieder, Werte, Services, Zonen,
    # EPG-Lage, NAT, DNS-Namen aus dem IPAM
    text: Mapped[str] = mapped_column(Text, default="")
    # "\nname\n...": Objekt-/EPG-Namen (Filter object=) bzw. VIP-/Pool-Namen (Filter nat=)
    names: Mapped[str] = mapped_column(Text, default="")
    nat: Mapped[str] = mapped_column(Text, default="")
    # Listenzeile wie /rules sie liefert (ohne die live gezaehlten nicht aufgeloesten Objekte)
    row: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # IPv4/IPv6-Gegenstueck derselben Firewall (nur Markierung): same | similar | confirmed
    pair_rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pair_match: Mapped[str | None] = mapped_column(String(12), nullable=True)
    pair_diff: Mapped[list | None] = mapped_column(JSON, nullable=True)


class RuleObjectUsage(Base):
    """Regel verwendet Firewall-Objekt: direkt oder ueber (verschachtelte) Gruppen."""

    __tablename__ = "rule_object_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rule.id", ondelete="CASCADE"), index=True)
    device_object_id: Mapped[int] = mapped_column(
        ForeignKey("device_object.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(8))  # src | dst | service | snat
    direct: Mapped[bool] = mapped_column(Boolean, default=True)


class RuleIpRange(Base):
    """IP-Bereich, den eine Regel als Quelle bzw. Ziel abdeckt (Host, Netz, Range, any)."""

    __tablename__ = "rule_ip_range"
    __table_args__ = (Index("ix_rule_ip_range_lookup", "tenant_id", "family", "lo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rule.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    source_system_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(4))  # src | dst
    family: Mapped[int] = mapped_column(SmallInteger)  # 4 | 6
    lo: Mapped[str] = mapped_column(IpHex)
    hi: Mapped[str] = mapped_column(IpHex)
    is_any: Mapped[bool] = mapped_column(Boolean, default=False)  # 0.0.0.0/0 bzw. ::/0
    via: Mapped[str | None] = mapped_column(String(300), nullable=True)  # Objekt/Wert


class RulePairDecision(Base):
    """IPv4/IPv6-Paar von Hand bestaetigt bzw. abgelehnt (0.12.0). Gespeichert ueber die
    Policy-IDs der FortiGate (`Rule.external_id`), damit es jedes neue Einlesen uebersteht."""

    __tablename__ = "rule_pair_decision"
    __table_args__ = (UniqueConstraint("source_system_id", "v4_external_id", "v6_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    source_system_id: Mapped[int] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), index=True
    )
    v4_external_id: Mapped[str] = mapped_column(String(200))
    v6_external_id: Mapped[str] = mapped_column(String(200))
    decision: Mapped[str] = mapped_column(String(12))  # confirmed | rejected
    decided_by: Mapped[str] = mapped_column(String(200))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
