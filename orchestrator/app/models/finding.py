"""Datenqualitaet / Reconciliation (ADR 0004).

Abweichungen werden read-only sichtbar gemacht und sind quittierbar. Die
schreibende Bereinigung (Objekt im DDI anlegen, Regel anpassen/ablehnen/loeschen)
folgt erst mit dem Schreibpfad. Statusaenderungen laufen ins Audit-Log.

Findings sind **abgeleiteter Zustand** des Mandanten: der Abgleich (`app.store.reconcile`)
berechnet sie nach jedem Lauf neu; Triage-Status und Notiz bleiben erhalten, solange der
Befund besteht (ADR 0010).
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FindingType(enum.StrEnum):
    UNMATCHED_OBJECT = "unmatched_object"  # Name in Regel, nicht im SoT
    INVALID_NAME = "invalid_name"  # passt nicht zur Namenskonvention
    AMBIGUOUS_IP = "ambiguous_ip"  # IP auf mehreren Objekten
    DUPLICATE_VALUE = "duplicate_value"
    NAME_TYPE_MISMATCH = "name_type_mismatch"  # Praefix H_ vs. SoT-Typ Netzwerk
    EPG_NO_ENDPOINTS = "epg_no_endpoints"  # EPG ohne gelernte Endpunkte (ACI)
    NAME_MISMATCH = "name_mismatch"  # Objektname passt nicht zum DNS-Namen im IPAM
    UNUSED_OBJECT = "unused_object"  # Firewall-Objekt in keiner Regel/Gruppe verwendet
    SUBNET_NOT_IN_IPAM = "subnet_not_in_ipam"  # ACI-/FortiGate-Subnetz fehlt im IPAM
    VLAN_MISMATCH = "vlan_mismatch"  # VLAN des Subnetzes weicht vom IPAM ab
    SERVICE_MISMATCH = "service_mismatch"  # Service-Name passt nicht zu Protokoll/Port
    RULE_INCOMPLETE = "rule_incomplete"  # Regel ohne Quelle bzw. Ziel (ACI: nicht wirksam)
    VM_NOT_IN_IPAM = "vm_not_in_ipam"  # IP einer VM (vCenter) fehlt im fuehrenden IPAM
    PORTGROUP_NO_EPG = "portgroup_no_epg"  # Portgruppe nach ACI-Schema ohne passende EPG
    # Portgruppe nach ACI-Schema, nur ueber das VLAN einer anders benannten EPG zugeordnet
    PORTGROUP_NAME_MISMATCH = "portgroup_name_mismatch"
    # Parallele Firewalls (Verbund, 0.18.0)
    PARALLEL_RULE_MISMATCH = "parallel_rule_mismatch"  # gleiche Policy-ID, anderer Inhalt
    PARALLEL_RULE_MISSING = "parallel_rule_missing"  # Policy fehlt auf einer Firewall
    PARALLEL_OBJECT_MISMATCH = "parallel_object_mismatch"  # gleiches Objekt, andere Werte


class Severity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingStatus(enum.StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class SubjectKind(enum.StrEnum):
    RULE = "rule"
    NETWORK_OBJECT = "network_object"
    EPG = "epg"
    DEVICE_OBJECT = "device_object"
    SUBNET = "subnet"
    VM = "vm"
    PORTGROUP = "portgroup"


class Finding(Base):
    __tablename__ = "finding"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    type: Mapped[FindingType] = mapped_column(Enum(FindingType, native_enum=False, length=32))
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, length=8), default=Severity.WARNING
    )
    status: Mapped[FindingStatus] = mapped_column(
        Enum(FindingStatus, native_enum=False, length=16), default=FindingStatus.OPEN, index=True
    )
    subject_kind: Mapped[SubjectKind] = mapped_column(
        Enum(SubjectKind, native_enum=False, length=16)
    )
    subject_id: Mapped[str] = mapped_column(String(300))
    # Quellsystem, auf dessen Daten der Befund beruht (None = mandantenweit)
    source_system_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
