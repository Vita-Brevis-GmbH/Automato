"""Parallele Firewalls (Verbund, 0.18.0): gleiche Regeln zusammenfassen, Abweichungen melden.

Dev-Befund 0.17.0: Zwei parallele Firewalls (z. B. zwei Cluster mit Session-Sync) tragen
dieselben Regeln - die Liste zeigte jede Regel doppelt. Quellsysteme mit demselben
`parallel_group` bilden einen Verbund (vom Admin gesetzt; Vorschlag ueber Session-Sync bzw.
gleiche Regeln, `suggestions`).

- **Gleiche Regel:** je Verbund gilt die Firewall mit dem kleinsten Namen als fuehrend. Eine
  Policy einer anderen Firewall mit derselben ID im selben VDOM (sonst demselben Namen) und
  gleichem Inhalt ist ein Zwilling (`RuleIndex.twin_of`) - Listen zeigen nur die fuehrende
  Regel, mit allen Firewalls, auf denen sie gleich steht.
- **Abweichend:** gleiche ID, anderer Inhalt (Quelle, Ziel, Dienst, Aktion, Interfaces,
  Status, Zeitplan, NAT, Schutzprofile, Logging, Name) - beide bleiben in der Liste, dazu ein
  Befund `parallel_rule_mismatch` mit den Feldern.
- **Fehlt:** die Policy fehlt auf einer Firewall des Verbunds - Befund `parallel_rule_missing`.
- **Objekte:** gleichnamige Adress-/Service-Objekte mit anderen Werten bzw. Mitgliedern -
  Befund `parallel_object_mismatch` (die Regeln sind dann trotz gleicher Namen nicht gleich).

Rein lesend; abgeleiteter Zustand, nach jedem Lauf und jeder Aenderung des Verbunds neu.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import null, select, update
from sqlalchemy.orm import Session

from app.models import (
    DeviceObject,
    FindingType,
    ParallelRejection,
    Platform,
    Rule,
    RuleDomain,
    RuleIndex,
    Severity,
    SourceSystem,
    SubjectKind,
    TopoHa,
)

# Policy-Felder der FortiGate (cmdb firewall/policy), die gleich sein muessen -> Bezeichnung.
# Nicht verglichen: uuid, Kommentare, Zaehler (je Firewall verschieden).
FIELDS = {
    "name": "Name",
    "status": "Status",
    "action": "Aktion",
    "srcintf": "Quell-Interface",
    "dstintf": "Ziel-Interface",
    "srcaddr": "Quelle",
    "srcaddr6": "Quelle",
    "dstaddr": "Ziel",
    "dstaddr6": "Ziel",
    "srcaddr-negate": "Quelle negiert",
    "dstaddr-negate": "Ziel negiert",
    "service": "Dienst",
    "service-negate": "Dienst negiert",
    "internet-service": "Internet-Service",
    "internet-service-name": "Internet-Service",
    "internet-service-src": "Internet-Service (Quelle)",
    "internet-service-src-name": "Internet-Service (Quelle)",
    "users": "Benutzer",
    "groups": "Gruppen",
    "schedule": "Zeitplan",
    "nat": "NAT",
    "ippool": "IP-Pool",
    "poolname": "IP-Pool",
    "utm-status": "Schutzprofile",
    "profile-group": "Schutzprofile",
    "av-profile": "Schutzprofile",
    "webfilter-profile": "Schutzprofile",
    "ips-sensor": "Schutzprofile",
    "application-list": "Schutzprofile",
    "ssl-ssh-profile": "Schutzprofile",
    "dnsfilter-profile": "Schutzprofile",
    "logtraffic": "Logging",
}


def _value(raw) -> str | frozenset:
    """Vergleichbarer Wert: Namenslisten als Menge (klein), sonst Text."""
    if isinstance(raw, list):
        names = (e.get("name") if isinstance(e, dict) else e for e in raw)
        return frozenset(str(n).strip().lower() for n in names if n not in (None, ""))
    if raw is None:
        return ""
    return str(raw).strip().lower()


def signature(rule: Rule) -> dict[str, str | frozenset]:
    """Inhalt einer Policy fuer den Vergleich (ohne uuid/Kommentar)."""
    raw = rule.raw or {}
    out = {k: _value(raw.get(k)) for k in FIELDS}
    out["action"] = out["action"] or rule.action.value
    out["name"] = out["name"] or (rule.name or "").strip().lower()
    return out


def differences(a: dict, b: dict) -> list[str]:
    """Bezeichnungen der abweichenden Felder (ohne Doppelte, Reihenfolge wie FIELDS)."""
    out: list[str] = []
    for key, label in FIELDS.items():
        if a.get(key) != b.get(key) and label not in out:
            out.append(label)
    return out


def _shown(value) -> str:
    if isinstance(value, frozenset):
        return ", ".join(sorted(value)) or "–"
    return value or "–"


@dataclass
class Comparison:
    """Ergebnis je Mandant: Zwillinge, Angaben je Regel (RuleIndex.parallel) und Befunde."""

    twins: dict[int, int] = field(default_factory=dict)  # Regel -> fuehrende Regel
    info: dict[int, dict] = field(default_factory=dict)
    issues: list = field(default_factory=list)


def groups(session: Session, tenant_id: int) -> dict[str, list[SourceSystem]]:
    """Verbuende des Mandanten mit mindestens zwei Firewalls, Mitglieder nach Name."""
    out: dict[str, list[SourceSystem]] = defaultdict(list)
    for s in session.scalars(
        select(SourceSystem)
        .where(SourceSystem.tenant_id == tenant_id, SourceSystem.parallel_group.is_not(None))
        .order_by(SourceSystem.name, SourceSystem.id)
    ):
        if (s.parallel_group or "").strip():
            out[s.parallel_group.strip()].append(s)
    return {g: members for g, members in out.items() if len(members) > 1}


def _key_maps(rules: list[Rule]) -> tuple[dict, dict]:
    """(VDOM, Policy-ID) -> Regel und (VDOM, Name) -> Regel (nur eindeutige Namen)."""
    by_id = {(r.vdom, r.external_id): r for r in rules}
    names: dict[tuple, list[Rule]] = defaultdict(list)
    for r in rules:
        if r.name:
            names[(r.vdom, r.name.strip().lower())].append(r)
    return by_id, {k: v[0] for k, v in names.items() if len(v) == 1}


def compare(session: Session, tenant_id: int | None) -> Comparison:
    """Regeln und Objekte aller Verbuende des Mandanten vergleichen."""
    from app.store.reconcile import Issue  # Befund-Typ des Abgleichs (spaet importiert)

    result = Comparison()
    if tenant_id is None:
        return result
    for group, members in groups(session, tenant_id).items():
        ids = [m.id for m in members]
        names = {m.id: m.name for m in members}
        rules: dict[int, list[Rule]] = defaultdict(list)
        for r in session.scalars(
            select(Rule)
            .where(Rule.source_system_id.in_(ids), Rule.domain == RuleDomain.FIREWALL)
            .order_by(Rule.source_system_id, Rule.sequence, Rule.id)
        ):
            rules[r.source_system_id].append(r)
        maps = {sid: _key_maps(rules[sid]) for sid in ids}
        claimed: set[int] = set()  # schon einer fuehrenden Regel zugeordnet
        for pos, sid in enumerate(ids):
            for lead in rules[sid]:
                if lead.id in claimed:
                    continue
                sig = signature(lead)
                same, differs, missing = [], [], []
                for other in ids[pos + 1 :]:
                    by_id, by_name = maps[other]
                    twin = by_id.get((lead.vdom, lead.external_id))
                    if twin is None and lead.name:
                        twin = by_name.get((lead.vdom, lead.name.strip().lower()))
                    if twin is None or twin.id in claimed:
                        missing.append(names[other])
                        continue
                    claimed.add(twin.id)
                    fields = differences(sig, signature(twin))
                    if not fields:
                        result.twins[twin.id] = lead.id
                        same.append(names[other])
                        continue
                    differs.append(names[other])
                    result.info[twin.id] = {"group": group, "lead": names[sid],
                                            "lead_rule_id": lead.id, "fields": fields}  # fmt: skip
                    result.issues.append(_mismatch(Issue, group, lead, twin, names, fields))
                # Firewalls vor dieser (kleinerer Name) ohne die Policy fehlen ebenfalls
                missing = [names[o] for o in ids[:pos]] + missing
                result.info[lead.id] = {"group": group, "same": same, "differs": differs,
                                        "missing": missing}  # fmt: skip
                if missing:
                    result.issues.append(_missing(Issue, group, lead, names, missing))
        result.issues += _object_issues(session, Issue, group, ids, names)
    return result


def _rule_detail(group: str, rule: Rule, names: dict[int, str]) -> dict:
    return {
        "group": group,
        "name": rule.name or f"#{rule.external_id}",
        "external_id": rule.external_id,
        "vdom": rule.vdom,
        "source": names.get(rule.source_system_id),
    }


def _mismatch(Issue, group, lead: Rule, twin: Rule, names, fields: list[str]):
    a, b = signature(lead), signature(twin)
    values = {FIELDS[k]: [_shown(a[k]), _shown(b[k])] for k in FIELDS if a.get(k) != b.get(k)}
    return Issue(
        type=FindingType.PARALLEL_RULE_MISMATCH,
        subject_kind=SubjectKind.RULE,
        subject_id=str(twin.id),
        source_system_id=twin.source_system_id,
        severity=Severity.WARNING,
        detail={
            **_rule_detail(group, twin, names),
            "other": names[lead.source_system_id],
            "other_rule_id": lead.id,
            "fields": fields,
            "values": values,
        },  # fmt: skip
    )


def _missing(Issue, group, lead: Rule, names, missing: list[str]):
    return Issue(
        type=FindingType.PARALLEL_RULE_MISSING,
        subject_kind=SubjectKind.RULE,
        subject_id=str(lead.id),
        source_system_id=lead.source_system_id,
        severity=Severity.WARNING,
        detail={**_rule_detail(group, lead, names), "missing_on": missing},
    )


def _object_issues(session: Session, Issue, group: str, ids: list[int], names) -> list:
    """Gleichnamige Objekte (je VDOM, Kategorie, Familie) mit anderen Werten/Mitgliedern."""
    from app.store.reconcile import subject

    seen: dict[tuple, DeviceObject] = {}
    out = []
    for o in session.scalars(
        select(DeviceObject)
        .where(DeviceObject.source_system_id.in_(ids))
        .order_by(DeviceObject.source_system_id, DeviceObject.id)
    ):
        key = (o.vdom, o.category, o.family, o.name.lower())
        first = seen.setdefault(key, o)
        if first is o or first.source_system_id == o.source_system_id:
            continue
        mine = (sorted(o.values or []), sorted(o.members or []), o.fqdn or "")
        theirs = (sorted(first.values or []), sorted(first.members or []), first.fqdn or "")
        if mine == theirs:
            continue
        out.append(
            Issue(
                type=FindingType.PARALLEL_OBJECT_MISMATCH,
                subject_kind=SubjectKind.DEVICE_OBJECT,
                subject_id=subject(o),
                source_system_id=o.source_system_id,
                severity=Severity.WARNING,
                detail={
                    "group": group,
                    "object_id": o.id,
                    "category": o.category.value,
                    "vdom": o.vdom,
                    "source": names.get(o.source_system_id),
                    "other": names.get(first.source_system_id),
                    "other_object_id": first.id,
                    "values": [
                        ", ".join(mine[0] or mine[1]) or mine[2] or "–",
                        ", ".join(theirs[0] or theirs[1]) or theirs[2] or "–",
                    ],
                },  # fmt: skip
            )
        )
    return out


def mark(session: Session, tenant_id: int | None, comparison: Comparison | None = None) -> int:
    """Zwillinge und Verbund-Angaben in den Regelindex schreiben (nach dem Index-Aufbau);
    Rueckgabe: Anzahl Zwillinge."""
    if tenant_id is None:
        return 0
    session.flush()
    comparison = comparison or session.info.pop(("parallel", tenant_id), None)
    if comparison is None:
        flagged = session.scalar(
            select(RuleIndex.rule_id)
            .where(
                RuleIndex.tenant_id == tenant_id,
                (RuleIndex.twin_of.is_not(None)) | (RuleIndex.parallel.is_not(None)),
            )
            .limit(1)
        )
        if not groups(session, tenant_id) and flagged is None:
            return 0  # kein Verbund und nichts zurueckzusetzen
        comparison = compare(session, tenant_id)
    session.execute(
        update(RuleIndex)
        .where(
            RuleIndex.tenant_id == tenant_id,
            (RuleIndex.twin_of.is_not(None)) | (RuleIndex.parallel.is_not(None)),
        )
        .values(twin_of=None, parallel=null())
        .execution_options(synchronize_session=False)
    )
    present = set(
        session.scalars(
            select(RuleIndex.rule_id).where(
                RuleIndex.rule_id.in_(set(comparison.twins) | set(comparison.info))
            )
        )
    )
    rows = [
        {"rule_id": rid, "twin_of": comparison.twins.get(rid), "parallel": comparison.info.get(rid)}
        for rid in set(comparison.twins) | set(comparison.info)
        if rid in present
    ]
    if rows:
        session.execute(update(RuleIndex), rows)
    session.flush()
    return len(comparison.twins)


def twin_ids(session: Session, rule_ids) -> set[int]:
    """Welche dieser Regeln Zwillinge sind (in Listen und Doku zusammengefasst)."""
    ids = {i for i in rule_ids if i is not None}
    if not ids:
        return set()
    return set(
        session.scalars(
            select(RuleIndex.rule_id).where(
                RuleIndex.rule_id.in_(ids), RuleIndex.twin_of.is_not(None)
            )
        )
    )


# -- Vorschlaege ----------------------------------------------------------------------


def _pairs(ids) -> list[tuple[int, int]]:
    ids = sorted(set(ids))
    return [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]]


def rejected(session: Session, tenant_id: int) -> set[tuple[int, int]]:
    """Abgelehnte Paare (0.19.0): werden nicht mehr vorgeschlagen."""
    return {
        (r.system_a_id, r.system_b_id)
        for r in session.scalars(
            select(ParallelRejection).where(ParallelRejection.tenant_id == tenant_id)
        )
    }


def reject(session: Session, tenant_id: int, system_ids) -> list[tuple[int, int]]:
    """Firewalls sind nicht parallel: Paare merken; ein gemeinsamer Verbund wird aufgeloest.
    Rueckgabe: neu abgelehnte Paare."""
    known = rejected(session, tenant_id)
    new = [p for p in _pairs(system_ids) if p not in known]
    for a, b in new:
        session.add(ParallelRejection(tenant_id=tenant_id, system_a_id=a, system_b_id=b))
    members = list(
        session.scalars(
            select(SourceSystem).where(
                SourceSystem.tenant_id == tenant_id, SourceSystem.id.in_(set(system_ids))
            )
        )
    )
    shared = {s.parallel_group for s in members if s.parallel_group}
    for group in shared:
        inside = [s for s in members if s.parallel_group == group]
        if len(inside) > 1:  # gemeinsamer Verbund dieser Firewalls: aufloesen
            for s in inside:
                s.parallel_group = None
    session.flush()
    return new


def unreject(session: Session, tenant_id: int, system_ids) -> int:
    """Ablehnung aufheben (Paare wieder vorschlagen bzw. von Hand gesetzter Verbund)."""
    pairs = set(_pairs(system_ids))
    gone = 0
    for r in session.scalars(
        select(ParallelRejection).where(ParallelRejection.tenant_id == tenant_id)
    ):
        if (r.system_a_id, r.system_b_id) in pairs:
            session.delete(r)
            gone += 1
    session.flush()
    return gone


def suggestions(session: Session, tenant_id: int, include_rejected: bool = False) -> list[dict]:
    """Firewalls, die parallel aussehen und noch nicht im selben Verbund sind: Session-Sync
    (FGSP) zwischen ihnen bzw. mindestens die Haelfte gleicher Policies. Abgelehnte Paare
    nur auf Wunsch (`rejected: true`)."""
    from app.store import paths  # Adressen der Knoten (Interfaces) - spaet importiert

    systems = {
        s.id: s
        for s in session.scalars(
            select(SourceSystem).where(
                SourceSystem.tenant_id == tenant_id, SourceSystem.platform == Platform.FORTIGATE
            )
        )
    }
    if len(systems) < 2:
        return []
    reasons: dict[tuple[int, int], list[str]] = defaultdict(list)
    owners = paths.Graph(session, tenant_id).owners()
    for h in session.scalars(select(TopoHa).where(TopoHa.tenant_id == tenant_id)):
        for peer in h.sync_peers or []:
            for (sid, _ctx), _iface, _zone in owners.get(str(peer.get("peerip") or ""), []):
                if sid != h.source_system_id and sid in systems:
                    pair = tuple(sorted((h.source_system_id, sid)))
                    if "Session-Sync (FGSP)" not in reasons[pair]:
                        reasons[pair].append("Session-Sync (FGSP)")
    sigs: dict[int, dict[tuple, dict]] = defaultdict(dict)
    for r in session.scalars(
        select(Rule).where(Rule.source_system_id.in_(systems), Rule.domain == RuleDomain.FIREWALL)
    ):
        sigs[r.source_system_id][(r.vdom, r.external_id)] = signature(r)
    ids = sorted(systems)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            common = sigs[a].keys() & sigs[b].keys()
            same = sum(1 for k in common if sigs[a][k] == sigs[b][k])
            smaller = min(len(sigs[a]), len(sigs[b]))
            if smaller and same >= 3 and same * 2 >= smaller:
                reasons[(a, b)].append(f"{same} von {smaller} Policies gleich")
    no = rejected(session, tenant_id)
    if include_rejected:
        for pair in no:
            if pair[0] in systems and pair[1] in systems:
                reasons.setdefault(pair, [])
    out = []
    for (a, b), why in sorted(reasons.items()):
        sa, sb = systems[a], systems[b]
        if sa.parallel_group and sa.parallel_group == sb.parallel_group:
            continue
        if (a, b) in no and not include_rejected:
            continue
        group = sa.parallel_group or sb.parallel_group or f"{sa.name} + {sb.name}"
        out.append({"system_ids": [a, b], "names": [sa.name, sb.name], "group": group,
                    "reasons": why, "rejected": (a, b) in no})  # fmt: skip
    return out
