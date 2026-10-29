"""Prüfung: Zuordnung zu einer EPG widersprüchlich (konfiguriert vs. gelernt).

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    FindingType,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue, _source_names


def _epg_conflict_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Konfiguration vor Gelerntem (0.46.0): IPs, die das ACI in einer anderen EPG gelernt hat,
    als die Portgruppe ihrer VM bzw. ein statischer Endpunkt vorgibt (gleiche VRF). Je VM bzw.
    konfigurierter EPG ein Befund mit allen IPs."""
    from app.store import epg_locate  # spaet: epg_locate -> aci_relevance -> paths

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for c in epg_locate.Locator(session, tenant_id).conflicts():
        kind = SubjectKind.VM if c["vm"] else SubjectKind.EPG
        grouped[(kind, c["vm"] or c["configured"], c["source_system_id"])].append(c)
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for (kind, subject, sid), items in grouped.items():
        first = items[0]
        out.append(
            Issue(
                FindingType.EPG_ASSIGNMENT_CONFLICT,
                kind,
                subject,
                sid,
                Severity.WARNING,
                detail={
                    "ips": [c["ip"] for c in items][:10],
                    "configured": first["configured"],
                    "via": first["via"],
                    "vm": first["vm"],
                    "learned": list(dict.fromkeys(e for c in items for e in c["learned"]))[:5],
                    "vrf": first["vrf"],
                    "source": names.get(sid),
                },
            )
        )
    return out


CHECK = Check(
    "epg_zuordnung",
    "kern",
    ("EPG-Zuordnung widersprüchlich", "EPG assignment conflicting"),
    (
        "VM bzw. IP gehört nach Konfiguration einer anderen EPG als gelernt",
        "VM or IP belongs to a different EPG by configuration than learned",
    ),
    frozenset({FindingType.EPG_ASSIGNMENT_CONFLICT}),
    lambda session, tenant_id, cx: _epg_conflict_issues(session, tenant_id),
    order=42,
)
