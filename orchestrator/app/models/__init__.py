"""ORM-Modelle.

Grundaufbau (M6): Audit/Betriebstabellen. M5 (ADR 0004): das Datenmodell UC1
Phase 1 - Mandant, Zonen, Adressobjekte, Services, Regeln, Findings.
Die ACI-Tabellen (epg*) und nat_rule folgen mit ihrer Phase.
"""

from app.models.agent import Agent
from app.models.application import (
    CRITICALITIES,
    MEMBER_KINDS,
    Application,
    ApplicationMember,
)
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.collector_run import CollectorRun, RunStatus
from app.models.device_object import (
    ADDRESS_CATEGORIES,
    GROUP_CATEGORIES,
    SERVICE_CATEGORIES,
    DeviceObject,
    ObjectCategory,
)
from app.models.epg import Epg, EpgEndpoint, EpgSubnet
from app.models.finding import Finding, FindingStatus, FindingType, Severity, SubjectKind
from app.models.inventory import (
    INVENTORY_MODELS,
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
)
from app.models.naming_profile import DictionaryEntry, NamingProfile
from app.models.nat_rule import NatRule
from app.models.network_object import (
    Family,
    MatchStatus,
    NetworkObject,
    ObjectClass,
    ObjectType,
    SourceOfRecord,
)
from app.models.reference_record import ReferenceRecordRow
from app.models.rule import (
    EndpointKind,
    EndpointRole,
    RefRole,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleObjectRef,
    rule_service,
)
from app.models.service_object import Proto, ServiceObject
from app.models.site import Site
from app.models.source_system import REFERENCE_PLATFORMS, Platform, SourceSystem
from app.models.source_system import RefRole as ReferenceRole
from app.models.sync_plan import SyncPlan, SyncStatus
from app.models.tenant import DnsType, IpamType, Tenant
from app.models.zone import Zone

__all__ = [
    "ADDRESS_CATEGORIES",
    "CRITICALITIES",
    "GROUP_CATEGORIES",
    "INVENTORY_MODELS",
    "MEMBER_KINDS",
    "REFERENCE_PLATFORMS",
    "SERVICE_CATEGORIES",
    "Agent",
    "Application",
    "ApplicationMember",
    "AuditLog",
    "Base",
    "CollectorRun",
    "DeviceObject",
    "DictionaryEntry",
    "DnsType",
    "EndpointKind",
    "Epg",
    "EpgEndpoint",
    "EpgSubnet",
    "EndpointRole",
    "Family",
    "Finding",
    "FindingStatus",
    "FindingType",
    "InvDevice",
    "InvInterface",
    "InvSubnet",
    "InvVlan",
    "InvVrf",
    "IpamType",
    "MatchStatus",
    "NamingProfile",
    "NatRule",
    "NetworkObject",
    "ObjectCategory",
    "ObjectClass",
    "ObjectType",
    "Platform",
    "Proto",
    "RefRole",
    "ReferenceRecordRow",
    "SyncPlan",
    "SyncStatus",
    "ReferenceRole",
    "Rule",
    "RuleAction",
    "RuleDomain",
    "RuleEndpoint",
    "RuleObjectRef",
    "RunStatus",
    "ServiceObject",
    "Severity",
    "Site",
    "SourceOfRecord",
    "SourceSystem",
    "SubjectKind",
    "Tenant",
    "Zone",
    "rule_service",
]
