"""Abgleich: Findings als abgeleiteter Zustand des Mandanten (ADR 0010).

Nach jedem Lauf eines Quellsystems (und auf Anforderung) werden alle Befunde des Mandanten
aus dem aktuellen Stand neu berechnet - unabhaengig davon, welches Quellsystem zuletzt lief.
Bestehende Befunde behalten Triage-Status und Notiz; Befunde, die nicht mehr zutreffen,
fallen weg. Nur lesend gegenueber allen Quellsystemen.

Pruefungen (Inventar ↔ Konvention):
- unmatched_object      Regel-Endpunkt ohne Treffer im IPAM
- invalid_name          Objektname passt zu keinem Praefix der Namenskonvention
- name_type_mismatch    Praefix sagt Host, Wert ist ein Netz (oder umgekehrt)
- duplicate_value       gleicher Wert in mehreren Adressobjekten derselben Firewall
- unused_object         Firewall-Objekt in keiner Regel und keiner Gruppe
Referenz-Pruefungen (IPAM, DDI ↔ NetBox, ACI-Subnetze) ergaenzt `references.checks`.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import naming
from app.models import (
    DeviceObject,
    Epg,
    Finding,
    FindingType,
    MatchStatus,
    NetworkObject,
    ObjectCategory,
    ObjectClass,
    Portgroup,
    RefRole,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleObjectRef,
    Severity,
    SourceSystem,
    SubjectKind,
    Vm,
    VmIp,
)
from app.store import naming_profiles as profile_store
from app.store import parallel, virtual


@dataclass(frozen=True)
class Issue:
    type: FindingType
    subject_kind: SubjectKind
    subject_id: str
    source_system_id: int | None = None
    severity: Severity = Severity.WARNING
    detail: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def key(self) -> tuple:
        return (self.type, self.subject_kind, self.subject_id[:300], self.source_system_id)


def reconcile_tenant(session: Session, tenant_id: int | None) -> dict[str, int]:
    """Alle Befunde des Mandanten neu berechnen; Rueckgabe: Anzahl je Befund-Typ."""
    if tenant_id is None:
        return {}
    session.flush()
    from app.store import references  # Referenz-Ebene (IPAM), spaet importiert

    ctx = references.context(session, tenant_id)
    prefixes = profile_store.prefixes_for_tenant(session, tenant_id)
    references.rematch(session, tenant_id, ctx, prefixes)  # zuerst: Werte gegen den Stand
    virtual.map_epgs(session, tenant_id)  # Portgruppen -> EPGs (EPGs aendern sich je APIC-Lauf)
    issues: list[Issue] = []
    issues += _rule_endpoint_issues(session, tenant_id, ctx, prefixes)
    issues += _rule_incomplete_issues(session, tenant_id)
    issues += _aci_scope_issues(session, tenant_id)
    issues += _epg_conflict_issues(session, tenant_id)
    issues += _snat_pool_issues(session, tenant_id)
    issues += _topology_issues(session, tenant_id)
    issues += _cabling_issues(session, tenant_id)
    issues += _zone_issues(session, tenant_id)
    issues += _device_object_issues(session, tenant_id, prefixes)
    issues += references.checks(session, tenant_id, prefixes, ctx)
    issues += _virtual_issues(session, tenant_id, ctx)
    # Parallele Firewalls: Abweichungen als Befund; der Vergleich bleibt fuer den Regelindex
    # (Zwillinge) liegen, der gleich danach neu aufgebaut wird
    comparison = parallel.compare(session, tenant_id)
    session.info[("parallel", tenant_id)] = comparison
    issues += comparison.issues
    # Abweichungen Regel <-> Weg der Applikationen (0.59.0) brauchen den neuen Regelindex -
    # eigener Schritt (`refresh_app_findings`), hier unberuehrt
    _sync_findings(session, tenant_id, issues, keep=lambda f: f.type in APP_TYPES)
    counts: dict[str, int] = defaultdict(int)
    for issue in {i.key: i for i in issues}.values():
        counts[issue.type.value] += 1
    return dict(counts)


# -- Regel-Endpunkte (kanonische Werte-Ebene) ---------------------------------


def _rule_endpoint_issues(session: Session, tenant_id: int, ctx=None, prefixes=None) -> list[Issue]:
    """unmatched_object / invalid_name fuer Objekte, die eine Regel des Mandanten nutzt.

    Das Detail sagt genau, was fehlt: Wert, Referenzquelle, welche Firewall-Objekte und Regeln
    den Wert nutzen und - falls das IPAM den Server unter seinem Namen (ohne Praefix) mit
    einer anderen IP kennt - diese IP."""
    rows = session.execute(
        select(NetworkObject, Rule.id, Rule.name, Rule.external_id, Rule.source_system_id)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(Rule.tenant_id == tenant_id)
    ).all()
    objs: dict[int, NetworkObject] = {}
    rules_of: dict[int, dict[int, str]] = defaultdict(dict)
    sources_of: dict[int, set[int]] = defaultdict(set)
    for obj, rule_id, rule_name, ext_id, source_id in rows:
        objs[obj.id] = obj
        rules_of[obj.id][rule_id] = rule_name or f"#{ext_id}"
        if source_id is not None:
            sources_of[obj.id].add(source_id)
    unmatched = [o for o in objs.values() if o.match_status is MatchStatus.UNMATCHED]
    fw_names: dict[int, list[str]] = defaultdict(list)
    if unmatched:
        for oid, name in session.execute(
            select(DeviceObject.network_object_id, DeviceObject.name)
            .where(
                DeviceObject.tenant_id == tenant_id,
                DeviceObject.network_object_id.in_([o.id for o in unmatched]),
            )
            .order_by(DeviceObject.name)
        ):
            if name not in fw_names[oid]:
                fw_names[oid].append(name)
    source_names = _source_names(session, tenant_id)
    primary = ctx.primary if ctx is not None else None
    out: list[Issue] = []
    for obj in objs.values():
        if obj.match_status is MatchStatus.UNMATCHED:
            rules = sorted(rules_of[obj.id].values())
            detail = {
                "value": obj.value,
                "object_class": obj.object_class.value,
                "reference": primary.source.name if primary else None,
                "no_reference": primary is None or None,
                "sources": sorted(source_names.get(s, f"#{s}") for s in sources_of[obj.id]),
                "firewall_objects": fw_names.get(obj.id, [])[:10],
                "rules": len(rules),
                "rule_names": rules[:5],
            }
            if not obj.value:
                detail["server_name"] = naming.server_name(obj.name, prefixes)
                detail["ipam_candidates"] = _name_candidates(primary, detail["server_name"])
            elsewhere = _ipam_elsewhere(primary, obj, fw_names.get(obj.id, []), prefixes)
            if elsewhere:
                detail["ipam_elsewhere"] = elsewhere
            out.append(
                Issue(
                    FindingType.UNMATCHED_OBJECT,
                    SubjectKind.NETWORK_OBJECT,
                    obj.name,
                    detail={k: v for k, v in detail.items() if v not in (None, [], "")},
                )
            )
        if (
            obj.object_class is ObjectClass.UNKNOWN
            and not obj.value
            and obj.match_status not in (MatchStatus.NOT_APPLICABLE, MatchStatus.EXPECTED_EXTERNAL)
        ):
            out.append(
                Issue(
                    FindingType.INVALID_NAME,
                    SubjectKind.NETWORK_OBJECT,
                    obj.name,
                    detail={
                        "hint": "Name passt zu keinem Konventions-Praefix",
                        "prefixes": _prefix_names(prefixes),
                    },
                )
            )
    return out


def _rule_incomplete_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Regel ohne Quelle bzw. Ziel - ACI: Contract ohne Consumer/Provider, nicht wirksam."""
    roles: dict[int, set[str]] = defaultdict(set)
    for rule_id, role in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(Rule.tenant_id == tenant_id)
        .distinct()
    ):
        roles[rule_id].add(role.value)
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for r in session.scalars(select(Rule).where(Rule.tenant_id == tenant_id)):
        missing = [side for side in ("src", "dst") if side not in roles.get(r.id, set())]
        if not missing:
            continue
        aci = r.domain.value == "aci_contract"
        raw = r.raw if isinstance(r.raw, dict) else {}
        label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
        subject = f"{raw.get('tenant')}/{label}" if aci and raw.get("tenant") else label
        out.append(
            Issue(
                FindingType.RULE_INCOMPLETE,
                SubjectKind.RULE,
                subject,
                r.source_system_id,
                Severity.INFO if aci else Severity.WARNING,
                detail={
                    "rule_id": r.id,
                    "external_id": r.external_id,
                    "domain": r.domain.value,
                    "missing": missing,
                    "source_system_id": r.source_system_id,
                    "source": names.get(r.source_system_id),
                    **({"vdom": r.vdom} if r.vdom else {}),
                    "name": label,
                    "tenant": raw.get("tenant") if aci else None,
                },
            )
        )
    return out


def _snat_pool_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Erlaubende Policies mit SNAT auf einen IP-Pool, den die Firewall im VDOM nicht hat
    (0.47.0): die Quelle danach ist unbekannt."""
    from app.models import NatRule
    from app.store import rule_view

    pools = {
        (sid, vdom, name)
        for sid, vdom, name in session.execute(
            select(NatRule.source_system_id, NatRule.vdom, NatRule.name).where(
                NatRule.tenant_id == tenant_id, NatRule.nat_type == "snat_pool"
            )
        )
    }
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for r in session.scalars(
        select(Rule).where(
            Rule.tenant_id == tenant_id,
            Rule.domain == RuleDomain.FIREWALL,
            Rule.action == RuleAction.ALLOW,
        )
    ):
        missing = [
            p
            for p in rule_view.snat_pools(r) or []
            if (r.source_system_id, r.vdom, p) not in pools
            and (r.source_system_id, None, p) not in pools
        ]
        if not missing:
            continue
        label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
        out.append(
            Issue(
                FindingType.SNAT_POOL_MISSING,
                SubjectKind.RULE,
                label,
                r.source_system_id,
                Severity.WARNING,
                detail={
                    "rule_id": r.id,
                    "external_id": r.external_id,
                    "name": label,
                    "pools": missing,
                    "source": names.get(r.source_system_id),
                    **({"vdom": r.vdom} if r.vdom else {}),
                },
            )
        )
    return out


_TOPO = {  # Befund-Typ -> (Gegenstand, Schwere)
    "topo_duplicate_ip": (SubjectKind.SUBNET, Severity.WARNING),
    "topo_mask_mismatch": (SubjectKind.SUBNET, Severity.WARNING),
    "topo_route_loop": (SubjectKind.NODE, Severity.ERROR),
    "topo_static_unused": (SubjectKind.NODE, Severity.INFO),
    "topo_bgp_down": (SubjectKind.NODE, Severity.WARNING),
}


def _topology_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Plausibilitaet der Topologie (0.50.0, `store/topo_model.checks`): doppelte IP und
    abweichende Maske im selben Netz, Routing-Schleifen, statische Routen ohne Wirkung,
    BGP-Sitzungen nicht aufgebaut."""
    from app.store import topo_model

    out: list[Issue] = []
    for item in topo_model.checks(session, tenant_id):
        kind, severity = _TOPO[item["type"]]
        node = item.get("node") or next(iter(item.get("nodes") or []), None)
        sid = int(node.split("|")[0]) if isinstance(node, str) else None
        detail = {k: v for k, v in item.items() if k not in ("type", "subject")}
        if kind is not SubjectKind.NODE:
            sid = None  # mehrere Geraete im selben Netz
        out.append(Issue(FindingType(item["type"]), kind, item["subject"], sid, severity,
                         detail=detail))  # fmt: skip
    return out


def _cabling_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Verkabelung (0.62.0, `store/cabling.checks`): Speed, Duplex, VLANs bzw. Port-Channel
    zwischen zwei gelesenen Switches abweichend, Verbindung nur von einer Seite gemeldet; seit
    0.63.0 VLAN der VMs am Uplink ihres ESXi-Hosts nicht erlaubt (`store/l2.checks`), seit 0.64.0
    Redundanz und VLAN-Wege (`store/l2_topo.checks`)."""
    from app.store import cabling, l2, l2_topo

    out: list[Issue] = []
    cab = cabling.build(session, tenant_id)
    items = cabling.checks(session, tenant_id, cab) + l2.checks(session, tenant_id, cab=cab)
    items += l2_topo.checks(session, tenant_id, cab)
    for item in items:
        detail = {k: v for k, v in item.items() if k not in ("type", "subject", "severity")}
        out.append(Issue(FindingType(item["type"]), SubjectKind.LINK, item["subject"],
                         item.get("source_system_id"), Severity(item["severity"]),
                         detail=detail))  # fmt: skip
    return out


ZONE_TYPES = frozenset({FindingType.ZONE_POLICY_VIOLATION, FindingType.ZONE_OVERLAP})


def _zone_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Logische Zonen (0.51.0, `store/zones.checks`): erlaubende Regel zwischen Zonen mit
    Absicht „gesperrt“ (je Regel ein Befund mit allen Paaren), dasselbe Praefix in mehreren
    Zonen."""
    from app.store import zones

    items = zones.checks(session, tenant_id)
    rule_ids = [i["rule_id"] for i in items if i.get("rule_id")]
    rules = {
        r.id: r for r in session.scalars(select(Rule).where(Rule.id.in_(rule_ids)))
    } if rule_ids else {}  # fmt: skip
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for item in items:
        if item["type"] == "zone_overlap":
            detail = {k: v for k, v in item.items() if k not in ("type", "subject")}
            out.append(Issue(FindingType.ZONE_OVERLAP, SubjectKind.SUBNET, item["subject"],
                             None, Severity.WARNING, detail=detail))  # fmt: skip
            continue
        r = rules.get(item["rule_id"])
        if r is None:
            continue
        raw = r.raw if isinstance(r.raw, dict) else {}
        aci = r.domain is RuleDomain.ACI_CONTRACT
        label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
        subject = f"{raw.get('tenant')}/{label}" if aci and raw.get("tenant") else label
        out.append(
            Issue(
                FindingType.ZONE_POLICY_VIOLATION,
                SubjectKind.RULE,
                subject,
                r.source_system_id,
                Severity.WARNING,
                detail={
                    "rule_id": r.id,
                    "external_id": r.external_id,
                    "domain": r.domain.value,
                    "name": label,
                    "source_system_id": r.source_system_id,
                    "source": names.get(r.source_system_id),
                    **({"vdom": r.vdom} if r.vdom else {}),
                    "tenant": raw.get("tenant") if aci else None,
                    "pairs": item["pairs"],
                    "notes": item["notes"],
                },
            )
        )
    return out


def refresh_zone_findings(session: Session, tenant_id: int) -> int:
    """Nur die Zonen-Befunde neu berechnen (nach dem Speichern einer Zone bzw. Absicht) - ohne
    den ganzen Abgleich; Triage-Status bleibt wie sonst erhalten."""
    issues = _zone_issues(session, tenant_id)
    _sync_findings(session, tenant_id, issues, types=ZONE_TYPES)
    return len(issues)


APP_TYPES = frozenset({FindingType.APP_CONNECTION_MISMATCH})
APP_CONNECTIONS = 10  # Verbindungen je Befund im Detail
APP_VARIANTS = 6  # Wege je Verbindung im Detail (0.73.0)


_app_next: dict[int, int] = {}  # je Mandant: hier beginnt die naechste Neuberechnung


def _app_issues(session: Session, tenant_id: int, apps: list,
                budget: float | None = None) -> tuple[list[Issue], set[int]]:  # fmt: skip
    """Wege uneinheitlich (0.73.0, ADR 0021; bis 0.72 „Regel <-> Weg“): je erlaubender Regel und
    Applikation ein Befund mit den Verbindungen, die ueber einen Weg offen und ueber einen
    anderen gesperrt sind (aktiv, ECMP, Backup, Firewalls im Verbund - `app_paths`). Durchgehend
    gesperrte Verbindungen sind gewollt und kein Befund (Anwender). Graph und Policies einmal
    fuer alle Applikationen; nach `budget` Sekunden ist Schluss. -> (Befunde, Applikationen ohne
    Ergebnis - Fehler bzw. Zeitbudget; deren Befunde bleiben)."""
    import logging
    import time

    from app.store import app_paths, paths, policy_check

    if not apps:
        return [], set()
    started = time.monotonic()
    graph = paths.Graph(session, tenant_id)
    checks = policy_check.Checks.load(session, tenant_id)
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    failed: set[int] = set()
    for n, app in enumerate(apps):
        if budget and time.monotonic() - started > budget:
            rest = apps[n:]
            failed |= {a.id for a in rest}
            _app_next[tenant_id] = rest[0].id
            logging.getLogger(__name__).warning(
                "Abweichungen Regel <-> Weg: %d von %d Applikationen nach %.0f s (Zeitbudget), "
                "Rest beim naechsten Mal", n, len(apps), budget)  # fmt: skip
            break
        try:
            with session.begin_nested():
                result = app_paths.build(session, app, graph=graph, checks=checks)
        except Exception:  # noqa: BLE001 - eine Applikation darf den Lauf nicht scheitern lassen
            logging.getLogger(__name__).exception("Verbindungen von Applikation %s", app.id)
            failed.add(app.id)
            continue
        per_rule: dict[int, list[dict]] = defaultdict(list)
        for c in result["connections"]:
            if not c["inconsistent"]:
                continue
            variants = [{k: v.get(k) for k in ("kind", "via", "verdict", "text", "reason")}
                        for v in c["variants"][:APP_VARIANTS]]  # fmt: skip
            for st in c["rules"]:
                if st["action"] != "allow":
                    continue
                per_rule[st["rule_id"]].append({
                    "src": c["src"]["label"], "dst": c["dst"]["label"],
                    "service": c["service"], "verdict": c["verdict"],
                    "state": st["state"], "path": c["text"], "variants": variants,
                })  # fmt: skip
        rules = {r.id: r for r in session.scalars(select(Rule).where(
            Rule.id.in_(list(per_rule))))} if per_rule else {}  # fmt: skip
        for rid, conns in per_rule.items():
            r = rules.get(rid)
            if r is None:
                continue
            raw = r.raw if isinstance(r.raw, dict) else {}
            aci = r.domain is RuleDomain.ACI_CONTRACT
            label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
            subject = f"{raw.get('tenant')}/{label}" if aci and raw.get("tenant") else label
            out.append(Issue(
                FindingType.APP_CONNECTION_MISMATCH, SubjectKind.RULE,
                f"{subject} · Applikation {app.name}", r.source_system_id, Severity.WARNING,
                detail={
                    "rule_id": r.id, "external_id": r.external_id, "domain": r.domain.value,
                    "name": label, "source_system_id": r.source_system_id,
                    "source": names.get(r.source_system_id),
                    **({"vdom": r.vdom} if r.vdom else {}),
                    "tenant": raw.get("tenant") if aci else None,
                    "application_id": app.id, "application": app.name,
                    "count": len(conns), "connections": conns[:APP_CONNECTIONS],
                },
            ))  # fmt: skip
    return out, failed


def refresh_app_findings(session: Session, tenant_id: int | None,
                         application_id: int | None = None) -> int:  # fmt: skip
    """Abweichungen Regel <-> Weg neu berechnen: alle Applikationen des Mandanten (nach einem
    Lauf) bzw. eine (nach dem Aendern, nach dem Loeschen ihre Befunde entfernen). Triage-Status
    bleibt wie sonst erhalten."""
    from app.models import Application

    if tenant_id is None:
        return 0
    session.flush()
    stmt = select(Application).where(Application.tenant_id == tenant_id)
    if application_id is not None:
        stmt = stmt.where(Application.id == application_id)
    apps = list(session.scalars(stmt.order_by(Application.id)))
    budget = None
    if application_id is None:
        from app.config import get_settings

        budget = get_settings().app_findings_seconds or None
        start = _app_next.pop(tenant_id, None)  # zuerst, was beim letzten Mal nicht drankam
        if start is not None:
            apps = [a for a in apps if a.id >= start] + [a for a in apps if a.id < start]
    issues, failed = _app_issues(session, tenant_id, apps, budget)

    def keep(f: Finding) -> bool:
        app = (f.detail or {}).get("application_id") if isinstance(f.detail, dict) else None
        return app in failed or (application_id is not None and app != application_id)

    _sync_findings(session, tenant_id, issues, types=APP_TYPES, keep=keep)
    return len(issues)


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


def _aci_scope_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Contract mit Consumern und Providern, die sein Scope (AP, VRF, Tenant) nicht verbindet
    (0.45.1, Anwender): keine Gegenstelle im Scope = fuer diese EPGs ohne Wirkung. Ohne
    gelesenen Scope kein Befund."""
    from app.store import aci_relevance  # spaet: aci_relevance -> paths -> ...
    from app.store import epgs as epg_store

    cls = aci_relevance.Classifier(session, tenant_id)
    sides: dict[int, dict[str, list[Epg]]] = defaultdict(lambda: {"src": [], "dst": []})
    epgs = {e.id: e for e in session.scalars(select(Epg).where(Epg.tenant_id == tenant_id))}
    for rule_id, role, epg_id in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role, RuleEndpoint.epg_id)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.domain == RuleDomain.ACI_CONTRACT,
            Rule.aci_scope.in_(("application-profile", "context", "tenant")),
            RuleEndpoint.epg_id.is_not(None),
        )
    ):
        epg = epgs.get(epg_id)
        if epg is not None and epg not in sides[rule_id][role.value]:
            sides[rule_id][role.value].append(epg)
    if not sides:
        return []
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for r in session.scalars(select(Rule).where(Rule.id.in_(list(sides)))):
        raw = r.raw if isinstance(r.raw, dict) else {}
        both = sides[r.id]
        if raw.get("kind") in ("taboo", "preferred_group") or not (both["src"] and both["dst"]):
            continue  # ohne Consumer bzw. Provider: Befund rule_incomplete
        pairs, unpaired = cls.scope_pairs(r.aci_scope, both["src"], both["dst"])
        if not unpaired:
            continue
        label = r.name or f"#{r.external_id}"
        lone = [e for e in both["src"] + both["dst"] if e.id in unpaired]
        reason = next(
            (cls.scope_reason(r.aci_scope, e, o) for e in lone
             for o in (both["dst"] if e in both["src"] else both["src"])),
            None,
        )  # fmt: skip
        out.append(
            Issue(
                FindingType.ACI_SCOPE_UNPAIRED,
                SubjectKind.RULE,
                f"{raw.get('tenant')}/{label}" if raw.get("tenant") else label,
                r.source_system_id,
                Severity.WARNING if not pairs else Severity.INFO,
                detail={
                    "rule_id": r.id,
                    "name": label,
                    "scope": r.aci_scope,
                    "pairs": len(pairs),
                    "unpaired": len(lone),
                    "epgs": [epg_store.inventory_label(e) for e in lone[:5]],
                    "reason": reason,
                    "source": names.get(r.source_system_id),
                    "tenant": raw.get("tenant"),
                },
            )
        )
    return out


def _ipam_elsewhere(primary, obj: NetworkObject, fw_names: list[str], prefixes) -> dict | None:
    """Kennt das IPAM den Server (Name ohne Praefix, H_web01 -> web01) unter einer anderen IP?"""
    if primary is None or obj.object_class is not ObjectClass.HOST:
        return None
    for name in fw_names:
        ref = primary.lookup_name(name, prefixes)
        if ref is not None and ref.value and ref.value != (obj.value or "").split("/")[0]:
            return {"name": ref.dns_name or ref.name, "value": ref.value, "object": name}
    return None


def _name_candidates(primary, name: str) -> list[str]:
    """Eintraege mit demselben Kurznamen (dup -> dup.example.ch, dup.other.ch): warum der
    Name nicht eindeutig zugeordnet werden konnte."""
    if primary is None:
        return []
    recs = primary.by_short.get(naming.short_name(name), [])
    out = [f"{r.dns_name or r.name} → {r.value}" for r in recs]
    return list(dict.fromkeys(out))[:5]


def _prefix_names(prefixes) -> list[str]:
    return [p for p, _ in (prefixes or naming.prefixes_from_entries([]))]


def _source_names(session: Session, tenant_id: int) -> dict[int, str]:
    return {
        s.id: s.name
        for s in session.scalars(select(SourceSystem).where(SourceSystem.tenant_id == tenant_id))
    }


# -- Firewall-Objekte (Inventar-Ebene) ----------------------------------------


def _device_object_issues(session: Session, tenant_id: int, prefixes) -> list[Issue]:
    objects = list(session.scalars(select(DeviceObject).where(DeviceObject.tenant_id == tenant_id)))
    if not objects:
        return []
    source_names = _source_names(session, tenant_id)
    used = _used_object_ids(session, tenant_id, objects)
    allowed = _predefined_in_allow(session, tenant_id, objects)
    services = _ServiceConvention(session, tenant_id)
    out: list[Issue] = []
    by_value: dict[tuple[int, str | None, str], list[str]] = defaultdict(list)

    for o in objects:
        base = {
            "source_system_id": o.source_system_id,
            "source": source_names.get(o.source_system_id),
            **({"vdom": o.vdom} if o.vdom else {}),
        }
        detail = {**base, "category": o.category.value, "name": o.name}
        # Konvention gilt fuer Adressen und VIPs (fuer Gruppen kennt sie kein Praefix)
        if o.category in _CONVENTION_CATEGORIES:
            parsed = naming.parse_object_name(o.name, prefixes)
            if parsed.object_class is ObjectClass.UNKNOWN and o.obj_type not in _NO_CONVENTION:
                known = {"prefixes": _prefix_names(prefixes)}
                out.append(_issue(FindingType.INVALID_NAME, o, {**detail, **known}))
            elif o.category is ObjectCategory.ADDRESS:
                shape = _value_shape(o)
                if (parsed.object_class, shape) in _SHAPE_MISMATCH:
                    out.append(
                        _issue(
                            FindingType.NAME_TYPE_MISMATCH,
                            o,
                            {**detail, "expected": parsed.object_class.value, "value": _first(o)},
                        )
                    )
        predefined = _predefined_service(o)
        if o.category is ObjectCategory.SERVICE and not predefined:
            out += _service_issues(o, detail, services)
        if predefined and allowed.get(o.id):  # Standardobjekt in einer erlaubenden Regel
            used_by = {**detail, "rules": allowed[o.id]}
            out.append(_issue(FindingType.PREDEFINED_SERVICE_USED, o, used_by))
        if o.category is ObjectCategory.ADDRESS and _first(o):
            by_value[(o.source_system_id, o.vdom, _first(o))].append(o.name)
        derived = o.category is ObjectCategory.VIP_TARGET or bool((o.attrs or {}).get("vip_of"))
        if (
            o.id not in used
            and o.category is not ObjectCategory.IPPOOL
            and not predefined
            and not derived  # VIP-Ziel bzw. Objekt hinter einer VIP: von der VIP verwendet
        ):
            out.append(_issue(FindingType.UNUSED_OBJECT, o, detail, Severity.INFO))

    for (source_id, vdom, value), names in by_value.items():
        if len(names) > 1:  # je VDOM: gleiche IP in zwei VDOMs ist kein Doppel
            out.append(
                Issue(
                    FindingType.DUPLICATE_VALUE,
                    SubjectKind.DEVICE_OBJECT,
                    value + vdom_suffix(vdom),
                    source_id,
                    detail={
                        "source_system_id": source_id,
                        "source": source_names.get(source_id),
                        **({"vdom": vdom} if vdom else {}),
                        "value": value,
                        "names": sorted(names),
                    },
                )
            )
    return out


class _ServiceConvention:
    """Praefixe (Woerterbuch `object_prefix` mit Typ „service“ bzw. `service_prefix`, sonst FT_)
    und Soll-Ports (Woerterbuch `service`, z. B. FT_https -> tcp/443) - ohne Woerterbuch greift
    das Schema FT_<proto><port>."""

    def __init__(self, session: Session, tenant_id: int):
        self.prefixes = naming.service_prefixes(
            profile_store.entries_for_tenant(session, tenant_id, "object_prefix"),
            profile_store.entries_for_tenant(session, tenant_id, "service_prefix"),
        )
        self.catalog = {
            k.lower(): v
            for k, v in profile_store.entries_for_tenant(session, tenant_id, "service").items()
        }

    def expected(self, name: str) -> tuple[list[str], str] | None:
        if name.lower() in self.catalog:
            return naming.service_tokens(self.catalog[name.lower()]), "Wörterbuch"
        tokens = naming.expected_service(name)
        return (tokens, "Namensschema") if tokens else None


def _bare_name(o: DeviceObject) -> str:
    """ACI-Filter heissen `<Tenant>/<FT_name>` - geprueft wird der Filtername."""
    return o.name.split("/", 1)[1] if o.obj_type == "vzFilter" and "/" in o.name else o.name


def _predefined_service(o: DeviceObject) -> bool:
    return naming.predefined_service(o.category.value, o.obj_type, o.name)


def _service_issues(o: DeviceObject, detail: dict, conv: _ServiceConvention) -> list[Issue]:
    """Service/Filter nach Namensschema: Praefix FT_ und Ports passend zum Namen."""
    name = _bare_name(o)
    base = {**detail, "category": "service"}
    if not name.startswith(conv.prefixes):
        return [_issue(FindingType.INVALID_NAME, o, {**base, "prefixes": list(conv.prefixes)})]
    expected = conv.expected(name)
    if expected is None:
        return []
    tokens, rule = expected
    actual = naming.normalize_tokens(o.values or [])
    if naming.normalize_tokens(tokens) == actual:
        return []
    return [
        _issue(
            FindingType.SERVICE_MISMATCH,
            o,
            {
                **base,
                "expected": sorted(naming.normalize_tokens(tokens)),
                "values": sorted(actual),
                "rule": rule,
            },
        )
    ]


_CONVENTION_CATEGORIES = (ObjectCategory.ADDRESS, ObjectCategory.VIP)
# FortiGate-Typen ohne Namenskonvention (Geo-/MAC-/dynamische Objekte, FQDN-Wildcards)
_NO_CONVENTION = {"geography", "mac", "dynamic", "wildcard-fqdn"}
_SHAPE_MISMATCH = {(ObjectClass.HOST, "network"), (ObjectClass.NETWORK, "host")}


DEFAULT_VDOM = "root"  # FortiGate-Standard-VDOM (auch ohne VDOM-Modus)


def vdom_suffix(vdom: str | None) -> str:
    """Zusatz „ (VDOM x)“ fuer Objekte ausserhalb des Standard-VDOMs (ADR 0019)."""
    return f" (VDOM {vdom})" if vdom and vdom != DEFAULT_VDOM else ""


def subject(o: DeviceObject) -> str:
    """Befund-Bezeichnung eines Firewall-Objekts. IPv6-Adressobjekte tragen die Familie im
    Namen - auf der FortiGate darf ein IPv4- und ein IPv6-Objekt gleich heissen; ebenso zwei
    Objekte in verschiedenen VDOMs (VDOM im Namen, ausser im Standard-VDOM)."""
    name = o.name
    if o.family == "v6" and o.category in _FAMILY_CATEGORIES:
        name = f"{o.name} (IPv6)"
    return name + vdom_suffix(o.vdom)


_FAMILY_CATEGORIES = (ObjectCategory.ADDRESS, ObjectCategory.ADDRESS_GROUP, ObjectCategory.VIP)


def _issue(ftype: FindingType, o: DeviceObject, detail: dict, sev=Severity.WARNING) -> Issue:
    if o.family and o.category in _FAMILY_CATEGORIES:
        detail = {**detail, "family": o.family}
    return Issue(ftype, SubjectKind.DEVICE_OBJECT, subject(o), o.source_system_id, sev, detail)


def _first(o: DeviceObject) -> str | None:
    values = o.values or []
    return values[0] if len(values) == 1 else None


def _value_shape(o: DeviceObject) -> str | None:
    """host | network fuer IP-/CIDR-Werte; typisierte Werte (fqdn:, range:, ...) -> None."""
    value = _first(o)
    try:
        net = ipaddress.ip_network(value, strict=False) if value else None
    except ValueError:
        return None
    if net is None:
        return None
    return "host" if net.num_addresses == 1 else "network"


def _predefined_in_allow(
    session: Session, tenant_id: int, objects: list[DeviceObject]
) -> dict[int, list[str]]:
    """Standard-Services (FortiOS, ACI common) in erlaubenden Regeln - direkt bzw. als
    Mitglied einer verwendeten Service-Gruppe - mit den Regeln (Name bzw. #ID)."""
    pre = {o.id: o for o in objects if _predefined_service(o)}
    if not pre:
        return {}
    out: dict[int, set[str]] = defaultdict(set)
    via_group: dict[int, set[str]] = defaultdict(set)
    for oid, name, ext in session.execute(
        select(RuleObjectRef.device_object_id, Rule.name, Rule.external_id)
        .join(Rule, Rule.id == RuleObjectRef.rule_id)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.action == RuleAction.ALLOW,
            RuleObjectRef.role == RefRole.SERVICE,
            RuleObjectRef.device_object_id.is_not(None),
        )
    ):
        (out if oid in pre else via_group)[oid].add(name or f"#{ext}")
    if via_group:
        by_key = {(o.source_system_id, o.vdom, o.name.lower()): o.id for o in pre.values()}
        for g in objects:
            if g.id not in via_group or g.category is not ObjectCategory.SERVICE_GROUP:
                continue
            for m in g.members or []:
                mid = by_key.get((g.source_system_id, g.vdom, str(m).lower()))
                if mid is not None:
                    out[mid] |= via_group[g.id]
    return {k: sorted(v) for k, v in out.items()}


def _used_object_ids(session: Session, tenant_id: int, objects: list[DeviceObject]) -> set[int]:
    """In einer Regel referenziert oder Mitglied einer (genutzten oder nicht) Gruppe - Mitglieder
    in der Familie der Gruppe (IPv4-Gruppe -> IPv4-Objekt, auch bei gleichem Namen in IPv6)."""
    from app.store.objects import Directory  # spaet: objects importiert Modelle/Schemas

    used = set(
        session.scalars(
            select(RuleObjectRef.device_object_id)
            .join(Rule, Rule.id == RuleObjectRef.rule_id)
            .where(Rule.tenant_id == tenant_id, RuleObjectRef.device_object_id.is_not(None))
        )
    )
    directory = Directory(session, {o.source_system_id for o in objects})
    for o in objects:
        for member in o.members or []:
            m = directory.member(o, member)
            if m is not None:
                used.add(m.id)
    return used


# -- VMware (ADR 0017) -------------------------------------------------------


def _virtual_issues(session: Session, tenant_id: int, ctx) -> list[Issue]:
    """VM-IPs ohne Eintrag im fuehrenden IPAM; Portgruppen nach ACI-Schema ohne EPG."""
    out: list[Issue] = []
    names = _source_names(session, tenant_id)
    primary = ctx.primary if ctx is not None else None
    if primary is not None:
        missing: dict[int, list[str]] = defaultdict(list)
        vms: dict[int, Vm] = {}
        for ip, vm in session.execute(
            select(VmIp.ip, Vm)
            .join(Vm, Vm.id == VmIp.vm_id)
            .where(VmIp.tenant_id == tenant_id, Vm.template.is_(False))
            .order_by(Vm.name, VmIp.ip)
        ):
            if not primary.ip_records(ip):
                missing[vm.id].append(ip)
                vms[vm.id] = vm
        for vm_id, ips in missing.items():
            vm = vms[vm_id]
            out.append(
                Issue(
                    FindingType.VM_NOT_IN_IPAM,
                    SubjectKind.VM,
                    vm.name,
                    vm.source_system_id,
                    detail={
                        "source": names.get(vm.source_system_id),
                        "ips": ips,
                        "reference": primary.source.name,
                        "cluster": vm.cluster,
                        "hostname": vm.hostname,
                    },
                )
            )
    has_aci = session.scalar(select(Epg.id).where(Epg.tenant_id == tenant_id).limit(1))
    if has_aci:
        # nur ueber das VLAN zugeordnet, obwohl der Name eine andere EPG nennt (Dev-Befund 0.33.0)
        by_vlan = list(
            session.execute(
                select(Portgroup, Epg)
                .join(Epg, Epg.id == Portgroup.epg_id)
                .where(Portgroup.tenant_id == tenant_id, Portgroup.epg_match == "vlan")
            )
        )
        for pg, epg in by_vlan:
            parts = virtual.epg_from_name(pg.name)
            if parts is None:
                continue
            out.append(
                Issue(
                    FindingType.PORTGROUP_NAME_MISMATCH,
                    SubjectKind.PORTGROUP,
                    pg.name,
                    pg.source_system_id,
                    detail={
                        "source": names.get(pg.source_system_id),
                        "tenant": parts[0],
                        "ap": parts[1],
                        "epg": parts[2],
                        "assigned": "/".join(x for x in (epg.aci_tenant, epg.ap, epg.name) if x),
                        "vlan": pg.vlan,
                        "vms": pg.vm_count,
                    },
                )
            )
        for pg in session.scalars(
            select(Portgroup).where(Portgroup.tenant_id == tenant_id, Portgroup.epg_id.is_(None))
        ):
            parts = virtual.epg_from_name(pg.name)
            if parts is None:
                continue
            out.append(
                Issue(
                    FindingType.PORTGROUP_NO_EPG,
                    SubjectKind.PORTGROUP,
                    pg.name,
                    pg.source_system_id,
                    Severity.INFO,
                    detail={
                        "source": names.get(pg.source_system_id),
                        "tenant": parts[0],
                        "ap": parts[1],
                        "epg": parts[2],
                        "vlan": pg.vlan,
                        "vms": pg.vm_count,
                    },
                )
            )
    return out


# -- Findings synchronisieren (Triage bleibt erhalten) ------------------------


def _sync_findings(session: Session, tenant_id: int, issues: list[Issue],
                   types: frozenset | None = None, keep=None) -> None:  # fmt: skip
    """Gespeicherte Befunde auf `issues` bringen; mit `types` nur Befunde dieser Typen, `keep`
    (Befund -> bool) laesst gespeicherte Befunde ausserhalb des Abgleichs unberuehrt."""
    now = datetime.now(UTC)
    wanted = {i.key: i for i in issues}
    existing: dict[tuple, Finding] = {}
    stmt = select(Finding).where(Finding.tenant_id == tenant_id)
    if types is not None:
        stmt = stmt.where(Finding.type.in_(list(types)))
    for f in session.scalars(stmt):
        key = (f.type, f.subject_kind, f.subject_id, f.source_system_id)
        if keep is not None and key not in wanted and keep(f):
            continue
        if key in wanted and key not in existing:
            existing[key] = f
        else:
            session.delete(f)  # trifft nicht mehr zu (oder Dublette)
    session.flush()
    for key, issue in wanted.items():
        f = existing.get(key)
        if f is None:
            session.add(
                Finding(
                    tenant_id=tenant_id,
                    type=issue.type,
                    severity=issue.severity,
                    subject_kind=issue.subject_kind,
                    subject_id=issue.subject_id[:300],
                    source_system_id=issue.source_system_id,
                    detail=issue.detail or None,
                    first_seen=now,
                    last_seen=now,
                )
            )
        else:
            f.severity = issue.severity
            f.detail = issue.detail or None
            f.last_seen = now
    session.flush()
