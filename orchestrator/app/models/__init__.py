"""ORM-Modelle.

Grundaufbau (M6): Audit/Betriebstabellen. M5 (ADR 0004): das Datenmodell UC1
Phase 1 - Mandant, Zonen, Adressobjekte, Services, Regeln, Findings.
Die ACI-Tabellen (epg*) und nat_rule folgen mit ihrer Phase.
"""

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.collector_run import CollectorRun, RunStatus
from app.models.finding import Finding, FindingStatus, FindingType, Severity, SubjectKind
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
from app.models.rule import (
    EndpointKind,
    EndpointRole,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    rule_service,
)
from app.models.service_object import Proto, ServiceObject
from app.models.site import Site
from app.models.source_system import Platform, SourceSystem
from app.models.tenant import DnsType, IpamType, Tenant
from app.models.zone import Zone

__all__ = [
    "AuditLog",
    "Base",
    "CollectorRun",
    "DictionaryEntry",
    "DnsType",
    "EndpointKind",
    "EndpointRole",
    "Family",
    "Finding",
    "FindingStatus",
    "FindingType",
    "IpamType",
    "MatchStatus",
    "NamingProfile",
    "NatRule",
    "NetworkObject",
    "ObjectClass",
    "ObjectType",
    "Platform",
    "Proto",
    "Rule",
    "RuleAction",
    "RuleDomain",
    "RuleEndpoint",
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
