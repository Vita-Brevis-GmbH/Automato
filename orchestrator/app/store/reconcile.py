"""Abgleich: Findings als abgeleiteter Zustand des Mandanten (ADR 0010).

Nach jedem Lauf eines Quellsystems (und auf Anforderung) werden alle Befunde des Mandanten
aus dem aktuellen Stand neu berechnet - unabhaengig davon, welches Quellsystem zuletzt lief.
Bestehende Befunde behalten Triage-Status und Notiz; Befunde, die nicht mehr zutreffen,
fallen weg. Nur lesend gegenueber allen Quellsystemen.

Pruefungen (Inventar ↔ Konvention):
- unmatched_object      Regel-Endpunkt ohne Treffer im IPAM (nicht: oeffentliche Adressen
                        ausserhalb gemanagter Netze, `store/external`)
- invalid_name          Objektname passt zu keinem Praefix der Namenskonvention
- name_type_mismatch    Praefix sagt Host, Wert ist ein Netz (oder umgekehrt)
- duplicate_value       gleicher Wert in mehreren Adressobjekten derselben Firewall
- unused_object         Firewall-Objekt in keiner Regel und keiner Gruppe
Referenz-Pruefungen (IPAM, DDI ↔ NetBox, ACI-Subnetze) ergaenzt `references.checks`; den Stand
der Quellsysteme (veraltet, nur teilweise gelesen; ADR 0029) `data_quality.source_checks`.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import naming
from app.i18n import german_only
from app.models import (
    DeviceObject,
    Finding,
    FindingType,
    MatchStatus,
    NetworkObject,
    ObjectCategory,
    ObjectClass,
    RefRole,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleObjectRef,
    Severity,
    SourceSystem,
    SubjectKind,
)
from app.store import external, tenant_lock, virtual
from app.store import naming_profiles as profile_store


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


@german_only  # speichert Texte: immer deutsch (ADR 0028)
def reconcile_tenant(session: Session, tenant_id: int | None) -> dict[str, int]:
    """Alle Befunde des Mandanten neu berechnen; Rueckgabe: Anzahl je Befund-Typ."""
    if tenant_id is None:
        return {}
    tenant_lock.hold(session, tenant_id)  # ein Schreiber je Mandant (0.80.1)
    session.flush()
    from app.store import references  # Referenz-Ebene (IPAM), spaet importiert

    ctx = references.context(session, tenant_id)
    prefixes = profile_store.prefixes_for_tenant(session, tenant_id)
    references.rematch(session, tenant_id, ctx, prefixes)  # zuerst: Werte gegen den Stand
    external.apply(session, tenant_id)  # öffentlich ausserhalb gemanagter Netze: kein Befund
    virtual.map_epgs(session, tenant_id)  # Portgruppen -> EPGs (EPGs aendern sich je APIC-Lauf)
    # Pruefungen als steckbare Bausteine (0.110.0, ADR 0039): je Pruefung eine Datei in
    # `app/checks/<modul>/`; ausgeschaltete Pruefungen (bzw. ihr Modul) laufen nicht, ihre Befunde
    # fallen weg. Abweichungen Regel <-> Weg der Applikationen (0.59.0) brauchen den neuen
    # Regelindex - eigener Schritt (`refresh_app_findings`), hier unberuehrt
    from app import checks

    cx = checks.Context(refs=ctx, prefixes=prefixes)
    issues: list[Issue] = []
    for check in checks.active(session, tenant_id):
        issues += checks.run(check, session, tenant_id, cx)
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


ZONE_TYPES = frozenset({FindingType.ZONE_POLICY_VIOLATION, FindingType.ZONE_OVERLAP,
                        FindingType.ZONE_OUTSIDE_SCHEMA, FindingType.ZONE_NET_EXIT})  # fmt: skip


@german_only  # speichert Texte: immer deutsch (ADR 0028)
def refresh_zone_findings(session: Session, tenant_id: int) -> int:
    """Nur die Zonen-Befunde neu berechnen (nach dem Speichern einer Zone bzw. Absicht) - ohne
    den ganzen Abgleich; Triage-Status bleibt wie sonst erhalten."""
    return _refresh_pass(session, tenant_id, "zonen", ZONE_TYPES)


SOURCE_TYPES = frozenset({FindingType.SOURCE_STALE, FindingType.SOURCE_PARTIAL})


@german_only  # speichert Texte: immer deutsch (ADR 0028)
def refresh_source_findings(session: Session, tenant_id: int, settings=None) -> int:
    """Nur die Befunde der Quellsysteme neu berechnen (Zeitplan, `data_quality.maybe_refresh`):
    ein veralteter Stand wird zum Befund, auch wenn kein Lauf mehr gelingt."""
    return _refresh_pass(session, tenant_id, "quellen", SOURCE_TYPES, settings)


def _refresh_pass(session: Session, tenant_id: int, step: str, types: frozenset,
                  settings=None) -> int:  # fmt: skip
    """Teil-Durchgang: nur die Pruefungen mit `step`; ausgeschaltet -> Befunde weg."""
    from app import checks

    tenant_lock.hold(session, tenant_id)
    cx = checks.Context(settings=settings)
    issues: list[Issue] = []
    for check in checks.active(session, tenant_id, step):
        issues += checks.run(check, session, tenant_id, cx)
    _sync_findings(session, tenant_id, issues, types=types)
    return len(issues)


APP_TYPES = frozenset({FindingType.APP_CONNECTION_MISMATCH, FindingType.APP_CONTRACT_NARROWER,
                       FindingType.APP_RULE_UNUSED})  # fmt: skip
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
                result = app_paths.build(session, app, graph=graph, checks=checks,
                                         zones=False)  # fmt: skip
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
        unused: dict[int, list[dict]] = defaultdict(list)  # 0.107.0: nie auf dem Weg
        for x in result.get("unused") or []:
            unused[x["rule_id"]].append({k: x[k] for k in ("src", "dst", "service", "path")})
        narrower = result.get("narrower") or []
        wanted = set(per_rule) | set(unused) | {n["rule_id"] for n in narrower}
        wanted |= {x for n in narrower for x in n["firewall_rules"]}
        rules = {r.id: r for r in session.scalars(select(Rule).where(
            Rule.id.in_(list(wanted))))} if wanted else {}  # fmt: skip
        out += _narrower_issues(app, narrower, rules, names)
        out += _unused_issues(app, unused, rules, names)
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


def _rule_subject(r: Rule) -> tuple[str, str, dict]:
    """(Betreff, Name, Rohdaten) einer Regel fuer Befunde je Applikation."""
    raw = r.raw if isinstance(r.raw, dict) else {}
    label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
    aci = r.domain is RuleDomain.ACI_CONTRACT
    return (f"{raw.get('tenant')}/{label}" if aci and raw.get("tenant") else label), label, raw


def _unused_issues(app, unused: dict[int, list[dict]], rules: dict, names: dict) -> list[Issue]:
    """Regel nie gebraucht (0.107.0, Anwender: „Hier solle es eher ein Finding sein, dass es
    Regeln gibt, welche nie gebraucht werden“): je Regel und Applikation die Verbindungen, deren
    Weg nicht ueber das Geraet der Regel geht - mit dem Weg, den der Verkehr nimmt."""
    out = []
    for rid, conns in unused.items():
        r = rules.get(rid)
        if r is None:
            continue
        subject, label, raw = _rule_subject(r)
        seen = list({(c["src"], c["dst"], c["service"]): c for c in conns}.values())
        out.append(Issue(
            FindingType.APP_RULE_UNUSED, SubjectKind.RULE, f"{subject} · Applikation {app.name}",
            r.source_system_id, Severity.INFO,
            detail={
                "rule_id": r.id, "external_id": r.external_id, "domain": r.domain.value,
                "name": label, "source_system_id": r.source_system_id,
                "source": names.get(r.source_system_id), **({"vdom": r.vdom} if r.vdom else {}),
                "tenant": raw.get("tenant") if r.domain is RuleDomain.ACI_CONTRACT else None,
                "application_id": app.id, "application": app.name,
                "count": len(seen), "connections": seen[:APP_CONNECTIONS],
            },
        ))  # fmt: skip
    return out


def _narrower_issues(app, narrower: list[dict], rules: dict, names: dict) -> list[Issue]:
    """Contract Richtung L3Out enger als die Firewall-Regeln (0.78.2, Anwender: die Firewall
    ist fuehrend) - je Contract und Applikation ein Befund mit Systemen, Kategorien, fehlenden
    Diensten und den Firewall-Regeln."""
    out = []
    for n in narrower:
        r = rules.get(n["rule_id"])
        if r is None:
            continue
        raw = r.raw if isinstance(r.raw, dict) else {}
        label = r.name or f"#{r.external_id}"
        subject = f"{raw.get('tenant')}/{label}" if raw.get("tenant") else label
        fws = []
        for rid in n["firewall_rules"][:APP_CONNECTIONS]:
            fw = rules.get(rid)
            if fw is not None:
                fws.append(
                    {
                        "rule_id": fw.id,
                        "name": (fw.name or f"#{fw.external_id}") + vdom_suffix(fw.vdom),
                        "source": names.get(fw.source_system_id),
                    }
                )
        detail = {
            "rule_id": r.id, "external_id": r.external_id, "domain": r.domain.value,
            "name": label, "tenant": raw.get("tenant"), "source_system_id": r.source_system_id,
            "source": names.get(r.source_system_id), "application_id": app.id,
            "application": app.name, "systems": n["systems"][:APP_CONNECTIONS],
            "categories": n["categories"], "missing": n["missing"][:50],
            "firewall_rules": fws,
        }  # fmt: skip
        out.append(Issue(FindingType.APP_CONTRACT_NARROWER, SubjectKind.RULE,
                         f"{subject} · Applikation {app.name}", r.source_system_id,
                         Severity.WARNING, detail=detail))  # fmt: skip
    return out


@german_only  # speichert Texte: immer deutsch (ADR 0028)
def refresh_app_findings(session: Session, tenant_id: int | None,
                         application_id: int | None = None) -> int:  # fmt: skip
    """Abweichungen Regel <-> Weg neu berechnen: alle Applikationen des Mandanten (nach einem
    Lauf) bzw. eine (nach dem Aendern, nach dem Loeschen ihre Befunde entfernen). Triage-Status
    bleibt wie sonst erhalten."""
    from app.models import Application

    if tenant_id is None:
        return 0
    from app import modules  # 0.107.0: ohne Findings bzw. Wegsucher keine Arbeit dafuer

    if not (modules.enabled(session, tenant_id, "findings")
            and modules.enabled(session, tenant_id, "wege")):  # fmt: skip
        return 0
    import time

    from app import checks  # 0.110.0: Pruefung „applikationen“ je Mandant schaltbar

    session.flush()
    if not checks.enabled(session, tenant_id, checks.by_key("applikationen")):
        _sync_findings(session, tenant_id, [], types=APP_TYPES)  # aus: Befunde fallen weg
        return 0
    started = time.monotonic()
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
    if application_id is None:
        ms = (time.monotonic() - started) * 1000
        checks.note(tenant_id, "applikationen", ms, len({i.key for i in issues}))
    return len(issues)


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
    nat_names = _NatConvention(session, tenant_id)
    out: list[Issue] = []
    by_value: dict[tuple[int, str | None, str], list[str]] = defaultdict(list)

    for o in objects:
        base = {
            "source_system_id": o.source_system_id,
            "source": source_names.get(o.source_system_id),
            **({"vdom": o.vdom} if o.vdom else {}),
        }
        detail = {**base, "category": o.category.value, "name": o.name}
        # Konvention gilt fuer Adressen (fuer Gruppen kennt sie kein Praefix); VIPs und IP-Pools
        # seit 0.96.0 mit den Praefixen ihres Typs
        if o.category in _CONVENTION_CATEGORIES:
            parsed = naming.parse_object_name(o.name, prefixes)
            if parsed.object_class is ObjectClass.UNKNOWN and o.obj_type not in _NO_CONVENTION:
                known = {"prefixes": _prefix_names(prefixes)}
                out.append(_issue(FindingType.INVALID_NAME, o, {**detail, **known}))
            else:
                shape = _value_shape(o)
                if (parsed.object_class, shape) in _SHAPE_MISMATCH:
                    out.append(
                        _issue(
                            FindingType.NAME_TYPE_MISMATCH,
                            o,
                            {**detail, "expected": parsed.object_class.value, "value": _first(o)},
                        )
                    )
        elif o.category in _NAT_CATEGORIES:
            out += nat_names.issues(o, detail)
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


class _NatConvention:
    """Namen der VIPs und IP-Pools (0.96.0): Praefixe ihres Typs im Woerterbuch `object_prefix`
    - VIP bzw. VIP / DNAT (V-EXT_, VIP_), Virtual Server (eigener Typ, sonst wie VIPs), SNAT
    IP-Pool (IPP_). Ohne Adress-Praefixe im Profil gilt die Standard-Konvention; ohne Praefix
    des Typs im Profil kein Befund. Der NSX ALB hat keine Objekte auf der Firewall - seine Namen
    prueft Automato nicht (eigene Konvention)."""

    def __init__(self, session: Session, tenant_id: int):
        entries = profile_store.entries_for_tenant(session, tenant_id, "object_prefix")
        self.vip = naming.typed_prefixes(entries, naming.VIP_VALUES)
        self.virtual_server = naming.typed_prefixes(entries, (naming.VIRTUAL_SERVER_VALUE,))
        self.ippool = naming.typed_prefixes(entries, naming.IPPOOL_VALUES)

    def expected(self, o: DeviceObject) -> tuple[tuple[str, ...], str]:
        """(Praefixe, Typ der Konvention) fuer das Objekt."""
        if o.category is ObjectCategory.IPPOOL:
            return self.ippool, "snat_pool"
        if o.obj_type == _SLB and self.virtual_server:
            return self.virtual_server, naming.VIRTUAL_SERVER_VALUE
        return self.vip, "vip"

    def issues(self, o: DeviceObject, detail: dict) -> list[Issue]:
        prefixes, kind = self.expected(o)
        if not prefixes or o.name.startswith(prefixes):
            return []
        known = {"prefixes": list(prefixes), "convention": kind}
        if o.obj_type == _SLB:
            known["virtual_server"] = True
        return [_issue(FindingType.INVALID_NAME, o, {**detail, **known})]


_SLB = "server-load-balance"  # FortiGate Virtual Server
_CONVENTION_CATEGORIES = (ObjectCategory.ADDRESS,)
_NAT_CATEGORIES = (ObjectCategory.VIP, ObjectCategory.IPPOOL)
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


_FAMILY_CATEGORIES = (
    ObjectCategory.ADDRESS,
    ObjectCategory.ADDRESS_GROUP,
    ObjectCategory.VIP,
    ObjectCategory.IPPOOL,  # 0.96.0: IPv6-IP-Pools (ippool6) duerfen gleich heissen
)


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


# -- Findings synchronisieren (Triage bleibt erhalten) ------------------------


def _sync_findings(session: Session, tenant_id: int, issues: list[Issue],
                   types: frozenset | None = None, keep=None) -> None:  # fmt: skip
    """Gespeicherte Befunde auf `issues` bringen; mit `types` nur Befunde dieser Typen, `keep`
    (Befund -> bool) laesst gespeicherte Befunde ausserhalb des Abgleichs unberuehrt."""
    tenant_lock.hold(session, tenant_id)
    now = datetime.now(UTC)
    wanted = {i.key: i for i in issues}
    existing: dict[tuple, Finding] = {}
    stmt = select(Finding).where(Finding.tenant_id == tenant_id)
    if types is not None:
        stmt = stmt.where(Finding.type.in_(list(types)))
    for f in session.scalars(stmt):
        if f.type == FindingType.MANUAL:
            continue  # von Hand erfasst (0.109.0): kein Abgleich rechnet ihn, er bleibt
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
