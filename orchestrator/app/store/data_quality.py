"""Datenqualitaet (ADR 0029, Roadmap Schritt 6 „Daten verschaerfen“, erste Fassung).

Wo sind Ergebnisse (Doku, Weg-Suche, Zonen, Kunden) unsicher, weil Daten fehlen, veraltet oder
widerspruechlich sind? Je Mandant (optional Kunde) Bereiche mit Ampel `ok` | `warn` | `crit`,
Kennzahl und Eintraegen mit Ursache, Hinweis und Link in die Ansicht, in der sich nachbessern
laesst; dazu eine Gesamtnote (Anteil der Bereiche ohne Warnung).

Nur Zaehler und Abfragen auf dem gespeicherten Stand; Befunde werden gezaehlt, nicht neu
gerechnet. Einzige Ausnahme sind die Ursachen „Relevanz unbekannt“ der Firewall-Regeln, die vom
Routing abhaengen (keine Route, Interface der Route unbekannt, Policy-Route): sie stammen aus
einer Stichprobe (`SAMPLE_RULES` Regeln, je Seite die erste Adresse der Regel,
`relevance.Relevance.check`) - das Ergebnis nennt ihre Groesse (`sample`).

Bereiche:
- `sources`    Quellsysteme: Alter des letzten erfolgreichen Laufs (Grenze `stale_limit`,
               Zeitplan beachtet), letzter Lauf fehlgeschlagen, Teilfehler aus der Meldung des
               Laufs (Geraete nicht erreicht, nicht lesbar) und Routing-Kontexte „nicht lesbar“.
- `rules`      Regeln: Firewall bzw. VDOM ohne gelesenes Routing, Interface/Zone der Regel nicht
               im Inventar, Objekte ohne Wert, Stichprobe der Relevanz, externe EPGs ohne Subnetz
               bzw. ohne gelesenen Scope, VRF ohne Routing bei gleichen Subnetzen in mehreren
               L3Outs; Befunde nicht im IPAM, Regel nicht wirksam, SNAT-Pool fehlt.
- `vms`        VMs: ohne IP, ohne Portgruppe, Portgruppe ohne EPG; Befunde VM-IP nicht im IPAM,
               EPG gelernt ≠ konfiguriert, Portgruppe ohne EPG bzw. Name ≠ EPG.
- `ipam`       IPAM: fuehrende Referenz fehlt, nicht eingelesen bzw. veraltet; Befunde Subnetz
               fehlt im IPAM, IP mehrdeutig, VLAN weicht ab.
- `assignment` Zonen und Kunden: gelesene Netze ohne Zone, EPGs/Regeln/VMs ohne Kunde
               (`customers.unassigned`); Befund Netz in mehreren Zonen.

Befunde `source_stale` und `source_partial` (`source_checks`) erzeugt der Abgleich; der Zeitplan
rechnet sie stuendlich nach (`maybe_refresh`) - auch dann, wenn kein Lauf mehr gelingt und
deshalb kein Abgleich laeuft.
"""

import ipaddress
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import schedule
from app.config import Settings, get_settings
from app.i18n import L
from app.models import (
    REFERENCE_PLATFORMS,
    CollectorRun,
    Customer,
    Epg,
    EpgSubnet,
    Finding,
    FindingStatus,
    InvInterface,
    InvSubnet,
    MatchStatus,
    NetworkObject,
    Rule,
    RuleDomain,
    RuleEndpoint,
    RuleIpRange,
    RunStatus,
    SourceSystem,
    TopoContext,
    Vm,
    VmIp,
    VmNic,
    Zone,
)
from app.store import finding_text, tenant_lock
from app.store.rules import not_twin

OK, INFO, WARN, CRIT = "ok", "info", "warn", "crit"
RANK = {OK: 0, INFO: 0, WARN: 1, CRIT: 2}
GRACE = timedelta(days=1)  # mit Zeitplan: ein Termin darf sich um so viel verspaeten
STALE_CRIT = 4  # Stand aelter als so viele Grenzen: rot
CRIT_SHARE = 0.5  # ab diesem Anteil betroffener Regeln, VMs bzw. Netze: rot
SAMPLE_RULES = 300  # Firewall-Regeln in der Stichprobe „Relevanz“
SAMPLES = 5  # Beispiele je Eintrag
REFRESH_SECONDS = 3600  # Zeitplan: Befunde der Quellsysteme so oft nachrechnen
CONTEXTS = 10  # Routing-Kontexte „nicht lesbar“ im Detail eines Befunds

# Teilfehler in der Meldung eines erfolgreichen Laufs (Texte der Collectors, deutsch gespeichert)
PARTIAL = (
    ("unreached", "nicht gelesen: "),  # Switches einer Gruppe nicht erreicht (cisco_switch)
    ("unreadable", "nicht lesbar"),  # VDOM, Routing bzw. Objekte ohne Leserecht
    ("notes", "Hinweise: "),  # Befehle ohne auswertbare Ausgabe (Switches)
)
ROUTING_PART = "Routing nicht lesbar"  # FortiGate/ACI: dasselbe wie `topo_context.missing`

# Befunde, die die Bereiche zaehlen: Typ -> (Bereich, Titel de, Titel en)
FINDINGS = {
    "unmatched_object": ("rules", "Objekte nicht im IPAM", "Objects not in the IPAM"),
    "rule_incomplete": ("rules", "Regeln nicht wirksam (Quelle/Ziel fehlt)",
                        "Rules not effective (source/destination missing)"),
    "snat_pool_missing": ("rules", "SNAT-Pool fehlt", "SNAT pool missing"),
    "vm_not_in_ipam": ("vms", "VM-IPs nicht im IPAM", "VM IPs not in the IPAM"),
    "epg_assignment_conflict": ("vms", "EPG gelernt ≠ konfiguriert",
                                "EPG learned ≠ configured"),
    "portgroup_no_epg": ("vms", "Portgruppen nach ACI-Schema ohne EPG",
                         "Port groups following the ACI schema without EPG"),
    "portgroup_name_mismatch": ("vms", "Portgruppe: Name ≠ EPG", "Port group: name ≠ EPG"),
    "subnet_not_in_ipam": ("ipam", "Subnetze fehlen im IPAM", "Subnets missing in the IPAM"),
    "ambiguous_ip": ("ipam", "IPs mehrdeutig im IPAM", "Ambiguous IPs in the IPAM"),
    "vlan_mismatch": ("ipam", "VLAN weicht vom IPAM ab", "VLAN differs from the IPAM"),
    "zone_overlap": ("assignment", "Netze in mehreren Zonen", "Networks in several zones"),
}  # fmt: skip

_CAUSE = {  # Ursache der Relevanz „unbekannt“ (`relevance`) -> (de, en)
    "no_ip": ("keine IP zum Prüfen", "no IP to check"),
    "no_routing": ("Routing der Firewall nicht gelesen", "routing of the firewall not read"),
    "no_route": ("keine Route zur Adresse", "no route to the address"),
    "route_interface": ("Interface der Route unbekannt", "interface of the route unknown"),
    "policy_route": ("Policy-Route bzw. SD-WAN kann anders leiten",
                     "policy route or SD-WAN can route differently"),
    "interface_unknown": ("Interface bzw. Zone unbekannt", "interface or zone unknown"),
}  # fmt: skip


@dataclass
class Entry:
    key: str
    status: str
    title: str
    count: int | None = None
    cause: str | None = None  # was fehlt bzw. widerspricht sich
    hint: str | None = None  # was tun
    link: dict | None = None  # {view, params} - Ansicht im UI
    samples: list[str] = field(default_factory=list)


@dataclass
class Area:
    key: str
    title: str
    metric: str
    entries: list[Entry]
    applicable: bool = True  # ohne Daten (z. B. keine VMs): zaehlt nicht zur Gesamtnote
    note: str | None = None

    @property
    def status(self) -> str:
        worst = max((RANK[e.status] for e in self.entries), default=0)
        return {0: OK, 1: WARN, 2: CRIT}[worst]


def _link(view: str, **params) -> dict:
    return {"view": view, "params": {k: str(v) for k, v in params.items() if v is not None}}


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _ago(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes < 60:
        return L(f"vor {minutes} min", f"{minutes} min ago")
    if minutes < 48 * 60:
        return L(f"vor {minutes // 60} h", f"{minutes // 60} h ago")
    days = minutes // (24 * 60)
    return L(f"vor {days} Tagen", f"{days} days ago")


def _days(delta: timedelta) -> int:
    return max(1, round(delta.total_seconds() / 86400))


def _level(count: int, total: int) -> str:
    """Ampel nach Anteil: nichts betroffen ok, ab `CRIT_SHARE` rot, sonst gelb."""
    if count <= 0:
        return OK
    return CRIT if total and count / total >= CRIT_SHARE else WARN


def _listed(counter: Counter, label) -> list[str]:
    return [f"{label(k)} ({n})" for k, n in counter.most_common(SAMPLES)]


# -- Quellsysteme -------------------------------------------------------------------------


def _period(expr: str | None, tz: str, now: datetime) -> timedelta | None:
    """Abstand zweier Termine des Zeitplans (der letzte und der naechste danach)."""
    if not expr:
        return None
    try:
        cron = schedule.parse(expr)
        zone = ZoneInfo(tz)
    except (ValueError, KeyError):  # ungueltiger Zeitplan bzw. Zeitzone
        return None
    prev = cron.previous(now.astimezone(zone))
    nxt = cron.next_after(prev) if prev is not None else None
    return nxt - prev if prev is not None and nxt is not None else None


def stale_limit(src: SourceSystem, settings: Settings,
                now: datetime) -> tuple[timedelta | None, timedelta | None]:  # fmt: skip
    """(Grenze, Abstand der Termine): ab der Grenze gilt der Stand als veraltet -
    `stale_days` (0 = nie), mit Zeitplan mindestens ein Abstand plus `GRACE`."""
    if settings.stale_days <= 0:
        return None, None
    limit = timedelta(days=settings.stale_days)
    period = _period(src.schedule, settings.scheduler_timezone, now)
    if period is not None:
        limit = max(limit, period + GRACE)
    return limit, period


def partial_parts(message: str | None) -> list[dict]:
    """Teilfehler aus der Meldung eines erfolgreichen Laufs: [{kind, text}] (Text wie
    gespeichert, Abschnitte der Meldung durch „ · “ getrennt)."""
    out = []
    for part in (message or "").split(" · "):
        for kind, marker in PARTIAL:
            if marker in part:
                out.append({"kind": kind, "text": part.strip()[:300]})
                break
    return out


@dataclass
class SourceState:
    src: SourceSystem
    good: CollectorRun | None  # Lauf des aktuellen Stands (Referenzquelle: `reference_run_id`)
    last: CollectorRun | None  # letzter beendeter Lauf
    limit: timedelta | None
    period: timedelta | None
    parts: list[dict]
    contexts: list[dict]  # Routing-Kontexte mit „nicht lesbar“

    @property
    def good_at(self) -> datetime | None:
        if self.good is None:
            return None
        return _aware(self.good.finished_at or self.good.queued_at)

    def stale(self, now: datetime) -> bool:
        if self.limit is None:
            return False
        if self.good_at is None:  # nie eingelesen: erst nach der Grenze ab dem Anlegen
            created = _aware(self.src.created_at)
            return created is not None and now - created > self.limit
        return now - self.good_at > self.limit

    @property
    def failed(self) -> bool:
        return self.last is not None and self.last.status is RunStatus.FAILED

    @property
    def partial(self) -> bool:
        return bool(self.parts or self.contexts)

    @property
    def only_notes(self) -> bool:
        """Nur Hinweise (Befehle ohne auswertbare Ausgabe) - kein Teilfehler im engeren Sinn."""
        return all(p["kind"] == "notes" for p in self.parts) and not self.contexts


def states(session: Session, tenant_id: int, settings: Settings, now: datetime,
           enabled_only: bool = True) -> list[SourceState]:  # fmt: skip
    """Stand je Quellsystem des Mandanten (eine Abfrage je Quellsystem - es sind wenige)."""
    missing: dict[int, list[dict]] = defaultdict(list)
    for c in session.scalars(
        select(TopoContext)
        .where(TopoContext.tenant_id == tenant_id)
        .order_by(TopoContext.source_system_id, TopoContext.node, TopoContext.context)
    ):
        if c.missing:  # JSON: leer bzw. null gilt als vollstaendig
            missing[c.source_system_id].append(
                {"node": c.node, "context": c.context, "missing": list(c.missing)[:5]}
            )
    stmt = select(SourceSystem).where(SourceSystem.tenant_id == tenant_id)
    if enabled_only:
        stmt = stmt.where(SourceSystem.enabled.is_(True))
    out = []
    for src in session.scalars(stmt.order_by(SourceSystem.name)):
        if src.platform in REFERENCE_PLATFORMS:  # Stand = der uebernommene Referenzlauf
            good = session.get(CollectorRun, src.reference_run_id) if src.reference_run_id else None
        else:
            good = session.scalars(
                select(CollectorRun)
                .where(CollectorRun.source_system_id == src.id,
                       CollectorRun.status == RunStatus.COMPLETED)
                .order_by(CollectorRun.queued_at.desc())
                .limit(1)
            ).first()  # fmt: skip
        last = session.scalars(
            select(CollectorRun)
            .where(CollectorRun.source_system_id == src.id,
                   CollectorRun.status.in_((RunStatus.COMPLETED, RunStatus.FAILED)))
            .order_by(CollectorRun.queued_at.desc())
            .limit(1)
        ).first()  # fmt: skip
        limit, period = stale_limit(src, settings, now)
        parts = partial_parts(good.message) if good is not None else []
        contexts = missing.get(src.id, [])
        if contexts:  # „Routing nicht lesbar“ der Meldung steht genauer an den Kontexten
            parts = [p for p in parts if not p["text"].startswith(ROUTING_PART)]
        out.append(SourceState(src, good, last, limit, period, parts, contexts))
    return out


def source_checks(session: Session, tenant_id: int, settings: Settings | None = None,
                  now: datetime | None = None) -> list[dict]:  # fmt: skip
    """Befunde `source_stale` und `source_partial` je eingeschaltetem Quellsystem (fuer den
    Abgleich, `reconcile._source_issues`): {type, subject, source_system_id, severity, detail}.
    Das Detail haelt nur Werte - den Satz baut `finding_text` beim Abruf."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    out = []
    for st in states(session, tenant_id, settings, now):
        src = st.src
        base = {"source": src.name, "platform": src.platform.value}
        if st.stale(now):
            at = st.good_at
            message = (st.last.message or "")[:300] if st.failed and st.last else ""
            detail = {
                **base,
                "last_success": at.isoformat() if at else None,
                "never": True if at is None else None,
                "age_days": (now - at).days if at else None,
                "limit_days": _days(st.limit),
                "schedule": src.schedule or None,
                "last_failed": True if st.failed else None,
                "last_message": message or None,
            }
            out.append({"type": "source_stale", "subject": src.name, "source_system_id": src.id,
                        "severity": "warning",
                        "detail": {k: v for k, v in detail.items() if v is not None}})  # fmt: skip
        if st.partial:
            out.append({"type": "source_partial", "subject": src.name,
                        "source_system_id": src.id,
                        "severity": "info" if st.only_notes else "warning",
                        "detail": _partial_detail(st)})  # fmt: skip
    return out


def _partial_detail(st: SourceState) -> dict:
    detail = {
        "source": st.src.name,
        "platform": st.src.platform.value,
        "run_id": st.good.id if st.good else None,
        "finished_at": st.good_at.isoformat() if st.good_at else None,
        "parts": st.parts,
        "contexts": st.contexts[:CONTEXTS],
        "context_count": len(st.contexts) or None,
    }
    return {k: v for k, v in detail.items() if v not in (None, [])}


_last_refresh: float | None = None  # je Prozess (Zeitplan)


def maybe_refresh(factory, settings: Settings | None = None) -> int | None:
    """Zeitplan: Befunde der Quellsysteme hoechstens alle `REFRESH_SECONDS` je Prozess
    nachrechnen - sonst bliebe ein veralteter Stand ohne Befund, solange kein Lauf gelingt. Ein
    Mandant, der gerade eingespielt wird, kommt beim naechsten Mal dran. -> Anzahl der Befunde
    bzw. None (nicht faellig)."""
    from app.store import reconcile  # spaet: reconcile importiert dieses Modul

    global _last_refresh
    now = time.monotonic()
    if _last_refresh is not None and now - _last_refresh < REFRESH_SECONDS:
        return None
    _last_refresh = now
    settings = settings or get_settings()
    with factory() as session:
        tenants = list(
            session.scalars(
                select(SourceSystem.tenant_id).where(SourceSystem.tenant_id.is_not(None)).distinct()
            )
        )
    total = 0
    for tid in tenants:
        with factory() as session:
            if not tenant_lock.try_hold(session, tid):
                continue
            total += reconcile.refresh_source_findings(session, tid, settings)
            session.commit()
    return total


def _sources_area(sts: list[SourceState], off: list[SourceState], now: datetime) -> Area:
    entries: list[Entry] = []
    for st in sts:
        entries += _source_entries(st, now)
    for st in off:  # ausgeschaltet: Daten bleiben, werden aber nicht mehr aktualisiert
        if st.good_at is not None:
            entries.append(Entry(
                f"source:{st.src.id}:off", INFO,
                L(f"{st.src.name}: ausgeschaltet, Stand {_ago(now - st.good_at)}",
                  f"{st.src.name}: switched off, data from {_ago(now - st.good_at)}"),
                link=_link("tenants")))  # fmt: skip
    current = sum(1 for st in sts if st.good_at is not None and not st.stale(now))
    if not sts:
        return Area("sources", L("Quellsysteme", "Source systems"),
                    L("keine Quellsysteme", "no source systems"), entries,
                    applicable=False)  # fmt: skip
    entries.sort(key=lambda e: -RANK[e.status])
    metric = L(f"{current} von {len(sts)} aktuell", f"{current} of {len(sts)} current")
    return Area("sources", L("Quellsysteme", "Source systems"), metric, entries)


def _source_entries(st: SourceState, now: datetime) -> list[Entry]:
    src, link = st.src, _link("laeufe", system=st.src.id)
    name = src.name
    failed = (st.last.message or "")[:300] if st.failed and st.last else None
    hint_run = L(
        "Verbindung prüfen (Mandanten & Standorte → Quellsystem → Verbindung prüfen), Agent bzw. "
        "zentralen Worker prüfen (System → Betriebszustand) und neu einlesen.",
        "Check the connection (Tenants & sites → source system → Check connection), the agent "
        "or central worker (System → Health) and collect again.",
    )
    out: list[Entry] = []
    if st.good_at is None:
        cause = (L(f"letzter Lauf fehlgeschlagen: {failed}", f"last run failed: {failed}")
                 if failed else None)  # fmt: skip
        out.append(Entry(f"source:{src.id}", CRIT,
                         L(f"{name}: noch nie eingelesen", f"{name}: never collected"),
                         cause=cause, hint=hint_run, link=link))  # fmt: skip
    elif st.stale(now):
        age = now - st.good_at
        limit = _days(st.limit)
        status = CRIT if age > st.limit * STALE_CRIT else WARN
        cause = L(f"Grenze {limit} Tage", f"limit {limit} days")
        if st.period is not None:
            cause += L(f" (Zeitplan {src.schedule})", f" (schedule {src.schedule})")
        if failed:
            cause += L(f"; letzter Lauf fehlgeschlagen: {failed}", f"; last run failed: {failed}")
        out.append(Entry(f"source:{src.id}", status,
                         L(f"{name}: Stand veraltet ({_ago(age)})",
                           f"{name}: data outdated ({_ago(age)})"),
                         cause=cause, hint=hint_run, link=link))  # fmt: skip
    elif failed:
        age = _ago(now - st.good_at)
        out.append(Entry(f"source:{src.id}", WARN,
                         L(f"{name}: letzter Lauf fehlgeschlagen", f"{name}: last run failed"),
                         cause=L(f"{failed} – es gilt der Stand {age}",
                                 f"{failed} – the data from {age} applies"),
                         hint=hint_run, link=link))  # fmt: skip
    else:
        entries = st.good.rule_count if st.good is not None else None
        out.append(Entry(f"source:{src.id}", OK,
                         L(f"{name}: eingelesen {_ago(now - st.good_at)}",
                           f"{name}: collected {_ago(now - st.good_at)}"),
                         count=entries, link=link))  # fmt: skip
    if st.partial:
        texts = [p["text"] for p in st.parts]  # Meldung des Laufs, wie gespeichert
        for c in st.contexts[:SAMPLES]:
            where, why = f"{c['node']} · {c['context']}", "; ".join(c["missing"])
            texts.append(L(f"Routing nicht lesbar: {where} ({why})",
                           f"routing not readable: {where} ({why})"))  # fmt: skip
        target = (_link("routing", tab="routes", context=f"{src.id}|{st.contexts[0]['context']}")
                  if st.contexts else link)  # fmt: skip
        _msg, hint = finding_text.explain("source_partial", name, _partial_detail(st))
        out.append(Entry(f"source:{src.id}:partial", INFO if st.only_notes else WARN,
                         L(f"{name}: nur teilweise gelesen", f"{name}: only partially collected"),
                         count=len(st.parts) + len(st.contexts), cause="; ".join(texts),
                         hint=hint, link=target))  # fmt: skip
    return out


# -- Befunde (gezaehlt) ---------------------------------------------------------------------


def _open_findings(session: Session, tenant_id: int, view) -> dict[str, int]:
    stmt = (
        select(Finding.type, func.count())
        .where(
            Finding.tenant_id == tenant_id,
            Finding.status == FindingStatus.OPEN,
            Finding.type.in_(list(FINDINGS)),
        )
        .group_by(Finding.type)
    )
    if view is not None:
        stmt = stmt.where(Finding.id.in_(view.ids("finding")))
    return {getattr(t, "value", t): n for t, n in session.execute(stmt)}


def _finding_entries(area: str, counts: dict[str, int]) -> list[Entry]:
    out = []
    for ftype, (where, de, en) in FINDINGS.items():
        if where != area:
            continue
        n = counts.get(ftype, 0)
        out.append(Entry(f"finding:{ftype}", WARN if n else OK, L(de, en), count=n,
                         link=_link("findings", type=ftype)))  # fmt: skip
    return out


# -- Regeln ---------------------------------------------------------------------------------


def _intf(raw: dict, key: str) -> list[str]:
    return [str(e["name"]) for e in raw.get(key) or [] if isinstance(e, dict) and e.get("name")]


def _first_ips(session: Session, rule_ids: list[int]) -> dict[tuple[int, str], str]:
    """Je Regel und Seite die erste Adresse ihres ersten Bereichs (ohne any)."""
    out: dict[tuple[int, str], str] = {}
    if not rule_ids:
        return out
    for rid, role, fam, lo in session.execute(
        select(RuleIpRange.rule_id, RuleIpRange.role, RuleIpRange.family, RuleIpRange.lo)
        .where(
            RuleIpRange.rule_id.in_(rule_ids),
            RuleIpRange.role.in_(("src", "dst")),
            RuleIpRange.is_any.is_(False),
        )
        .order_by(RuleIpRange.id)
    ):
        make = ipaddress.IPv4Address if fam == 4 else ipaddress.IPv6Address
        out.setdefault((rid, role), str(make(int(lo, 16))))
    return out


def _rules_area(
    session: Session,
    tenant_id: int,
    view,
    counts: dict[str, int],
    contexts: set[tuple[int, str]],
    names: dict[int, str],
) -> tuple[Area, dict]:
    from app.store import relevance  # spaet: relevance -> paths -> ...

    scope = [Rule.tenant_id == tenant_id, not_twin()]
    if view is not None:
        scope.append(Rule.id.in_(view.ids("rule")))
    total = session.scalar(select(func.count(Rule.id)).where(*scope)) or 0
    title = L("Regeln", "Rules")
    if not total:
        return Area("rules", title, L("keine Regeln", "no rules"), [], applicable=False), {}
    fw = list(session.execute(
        select(Rule.id, Rule.source_system_id, Rule.vdom, Rule.raw, Rule.src_zone_id,
               Rule.dst_zone_id).where(*scope, Rule.domain == RuleDomain.FIREWALL)
        .order_by(Rule.id)
    ))  # fmt: skip
    zone_ids = {z for r in fw for z in (r.src_zone_id, r.dst_zone_id) if z}
    zones = dict(session.execute(select(Zone.id, Zone.name).where(Zone.id.in_(zone_ids))).all()
                 ) if zone_ids else {}  # fmt: skip
    known: dict[int, set[str]] = defaultdict(set)
    for sid, iface, zone in session.execute(
        select(InvInterface.source_system_id, InvInterface.name, InvInterface.zone).where(
            InvInterface.tenant_id == tenant_id
        )
    ):
        known[sid] |= {n for n in (iface, zone) if n}
    per_source: dict[int, set[str]] = defaultdict(set)
    for sid, ctx in contexts:
        per_source[sid].add(ctx)

    def routed(sid: int, vdom: str | None) -> bool:  # wie `relevance.Relevance._node`
        return (sid, vdom or "") in contexts or (not vdom and len(per_source[sid]) == 1)

    no_routing: Counter = Counter()
    unknown: Counter = Counter()
    unknown_names: dict[int, Counter] = defaultdict(Counter)
    first: dict[str, int] = {}
    candidates = []
    specific = 0
    for r in fw:
        raw = r.raw if isinstance(r.raw, dict) else {}
        sides = {}
        for side, key, zid in (
            ("src", "srcintf", r.src_zone_id),
            ("dst", "dstintf", r.dst_zone_id),
        ):
            got = _intf(raw, key) or ([zones[zid]] if zid in zones else [])
            if got and not any(n.lower() == "any" for n in got):
                sides[side] = got
        if not sides:
            continue  # any bzw. ohne Interface: greift unabhaengig vom Routing
        specific += 1
        if not routed(r.source_system_id, r.vdom):
            no_routing[(r.source_system_id, r.vdom or "")] += 1
            first.setdefault("no_routing", r.id)
            continue
        missing = {n for got in sides.values() for n in got if n not in known[r.source_system_id]}
        if missing:
            unknown[r.source_system_id] += 1
            unknown_names[r.source_system_id].update(missing)
            first.setdefault("interface_unknown", r.id)
            continue
        candidates.append((r, sides))

    def node(key: tuple[int, str]) -> str:
        sid, vdom = key
        label = names.get(sid, f"#{sid}")
        return f"{label} · VDOM {vdom}" if vdom and vdom != "root" else label

    entries: list[Entry] = []
    if specific:
        n = sum(no_routing.values())
        entries.append(Entry(
            "rules:no_routing", _level(n, specific),
            L("Firewall-Regeln ohne gelesenes Routing ihrer Firewall",
              "Firewall rules without collected routing of their firewall"),
            count=n,
            cause=", ".join(_listed(no_routing, node)) or None,
            hint=L("Routing mitlesen (FortiGate-Profil mit Leserecht auf Routing, je VDOM) und neu "
                   "einlesen – sonst bleibt die Relevanz der Regeln in Doku, Zonen und Kunden "
                   "„unbekannt“.",
                   "Collect the routing (FortiGate profile with read access to routing, per VDOM) "
                   "and collect again – otherwise the relevance of the rules in documentation, "
                   "zones and customers stays “unknown”."),
            link=_link("regeln", rule=first.get("no_routing")) if n else _link("routing"),
        ))  # fmt: skip
        n = sum(unknown.values())
        listed = [f"{names.get(sid, f'#{sid}')}: {', '.join(sorted(c)[:SAMPLES])}"
                  for sid, c in unknown_names.items()]  # fmt: skip
        entries.append(Entry(
            "rules:interface_unknown", _level(n, specific),
            L("Firewall-Regeln mit Interface bzw. Zone, die nicht im Inventar steht",
              "Firewall rules with an interface or zone that is not in the inventory"),
            count=n, cause="; ".join(listed[:SAMPLES]) or None,
            hint=L("Interfaces und Zonen der Firewall neu einlesen; fehlt ein Name weiterhin, "
                   "liest Automato diese Art Interface noch nicht – bitte melden.",
                   "Collect the interfaces and zones of the firewall again; if a name is still "
                   "missing, Automato does not read this kind of interface yet – please report "
                   "it."),
            link=_link("regeln", rule=first.get("interface_unknown")) if n else None,
        ))  # fmt: skip
    sample = _sample(session, tenant_id, candidates, zones, relevance)
    if sample["sides"]:
        n, sides, picked = sum(sample["causes"].values()), sample["sides"], len(sample["rules"])
        causes = [f"{L(*_CAUSE.get(c, (c, c)))} ({k})" for c, k in sample["causes"].most_common()]
        causes.append(L(f"geprüft: {sides} Seiten von {picked} Regeln",
                        f"checked: {sides} sides of {picked} rules"))  # fmt: skip
        entries.append(Entry(
            "rules:sample", _level(n, sides),
            L("Stichprobe: Regelseiten mit unbekannter Relevanz",
              "Sample: rule sides with unknown relevance"),
            count=n, cause=" – ".join(causes),
            hint=L("Je Seite die erste Adresse der Regel gegen das Routing ihrer Firewall "
                   "geprüft. Fehlende Routen bzw. Policy-Routen machen die Doku unsicher: "
                   "Routing vollständig lesen bzw. Anbindungen erfassen (Routing → Anbindungen).",
                   "The first address of the rule per side checked against the routing of its "
                   "firewall. Missing routes or policy routes make the documentation uncertain: "
                   "collect the full routing or record exits (Routing → Exits)."),
            link=_link("regeln", rule=sample["first"]) if sample["first"] else None,
        ))  # fmt: skip
        if sample["impossible"]:
            k = sample["impossible"]
            entries.append(Entry(
                "rules:sample_impossible", INFO,
                L("Stichprobe: Regelseiten, deren eigene Adresse die Firewall nicht über das "
                  "Interface der Regel leitet",
                  "Sample: rule sides whose own address the firewall does not route via the "
                  "interface of the rule"),
                count=k, cause=sample["impossible_text"],
                link=_link("regeln", rule=sample["impossible_rule"]),
            ))  # fmt: skip
    entries += _object_entries(session, tenant_id, view)
    entries += _aci_entries(session, tenant_id, view, contexts, names)
    entries += _finding_entries("rules", counts)
    entries.sort(key=lambda e: -RANK[e.status])
    sure = specific - sum(no_routing.values()) - sum(unknown.values())
    metric = (
        L(f"{sure} von {specific} Firewall-Regeln mit Interfaces prüfbar",
          f"{sure} of {specific} firewall rules with interfaces verifiable")
        if specific else L(f"{total} Regeln", f"{total} rules")
    )  # fmt: skip
    info = {"rules": len(sample["rules"]), "of": len(candidates)}
    return Area("rules", title, metric, entries), info


def _sample(session: Session, tenant_id: int, candidates: list, zones: dict, relevance) -> dict:
    """Relevanz je Seite fuer hoechstens `SAMPLE_RULES` Regeln, gleichmaessig ueber alle
    verteilt (nur Routen nachschlagen, keine Wege)."""
    step = max(1, -(-len(candidates) // SAMPLE_RULES))
    picked = candidates[::step][:SAMPLE_RULES]
    out = {"rules": picked, "sides": 0, "causes": Counter(), "first": None, "impossible": 0,
           "impossible_text": None, "impossible_rule": None}  # fmt: skip
    if not picked:
        return out
    ips = _first_ips(session, [r.id for r, _s in picked])
    rel = relevance.Relevance(session, tenant_id)
    for r, sides in picked:
        for side in sides:
            ip = ips.get((r.id, side))
            if ip is None:
                continue
            zone = zones.get(r.src_zone_id if side == "src" else r.dst_zone_id)
            result = rel.check(r, side, [ip], zone)
            out["sides"] += 1
            if result["state"] == relevance.UNKNOWN:
                out["causes"][result.get("cause") or "unknown"] += 1
                out["first"] = out["first"] or r.id
            elif result["state"] == relevance.IMPOSSIBLE:
                out["impossible"] += 1
                if out["impossible_rule"] is None:
                    out["impossible_rule"], out["impossible_text"] = r.id, result["text"]
    return out


def _object_entries(session: Session, tenant_id: int, view) -> list[Entry]:
    """Regeln mit Objekten ohne Wert (Name nicht aufgeloest, nicht bewusst extern): fuer sie
    gibt es keine IP zum Pruefen - Relevanz, Zonen und Kunden bleiben offen."""
    stmt = (
        select(Rule.id, NetworkObject.name)
        .join(RuleEndpoint, RuleEndpoint.rule_id == Rule.id)
        .join(NetworkObject, NetworkObject.id == RuleEndpoint.network_object_id)
        .where(
            Rule.tenant_id == tenant_id,
            not_twin(),
            (NetworkObject.value.is_(None)) | (NetworkObject.value == ""),
            NetworkObject.match_status.not_in(
                (MatchStatus.EXPECTED_EXTERNAL, MatchStatus.NOT_APPLICABLE)
            ),
        )
    )
    if view is not None:
        stmt = stmt.where(Rule.id.in_(view.ids("rule")))
    rules: set[int] = set()
    objs: Counter = Counter()
    for rid, name in session.execute(stmt):
        rules.add(rid)
        objs[name] += 1
    first = objs.most_common(1)[0][0] if objs else None
    return [Entry(
        "rules:no_value", WARN if rules else OK,
        L("Regeln mit Objekten ohne Wert", "Rules with objects without value"),
        count=len(rules), cause=", ".join(_listed(objs, str)) or None,
        hint=L("Objekt auf der Firewall bzw. im IPAM prüfen (Name ohne IP, nicht aufgelöst); "
               "bewusst externe Ziele mit H-EXT_/N-EXT_ benennen.",
               "Check the object on the firewall or in the IPAM (name without IP, not "
               "resolved); name intentionally external destinations H-EXT_/N-EXT_."),
        link=_link("regeln", object=first) if first else None,
    )]  # fmt: skip


def _aci_entries(session: Session, tenant_id: int, view, contexts: set[tuple[int, str]],
                 names: dict[int, str]) -> list[Entry]:  # fmt: skip
    """Externe EPGs der Contracts: ohne Subnetz (der Contract erfasst keine Adresse von
    aussen), ohne gelesenen Scope (APIC-Standard angenommen) bzw. gleiches Subnetz in mehreren
    L3Outs, deren VRF kein gelesenes Routing hat (welcher L3Out gilt, bleibt „unbekannt“)."""
    from app.store import aci_relevance  # spaet: aci_relevance -> paths -> ...

    used = (
        select(RuleEndpoint.epg_id)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.domain == RuleDomain.ACI_CONTRACT,
            RuleEndpoint.epg_id.is_not(None),
        )
    )
    if view is not None:
        used = used.where(Rule.id.in_(view.ids("rule")))
    ext = list(session.scalars(
        select(Epg).where(Epg.id.in_(used), Epg.kind == "external").order_by(Epg.id)
    ))  # fmt: skip
    if not ext:
        return []
    subnets: dict[int, list[EpgSubnet]] = defaultdict(list)
    for s in session.scalars(
        select(EpgSubnet).where(
            EpgSubnet.epg_id.in_([e.id for e in ext]), EpgSubnet.origin == "ext"
        )
    ):
        subnets[s.epg_id].append(s)
    label = aci_relevance.ext_label
    bare = [label(e) for e in ext if not subnets[e.id]]
    unread = [label(e) for e in ext if any(s.scope is None for s in subnets[e.id])]
    cls = aci_relevance.Classifier(session, tenant_id)
    shared: dict[tuple, set[str]] = defaultdict(set)
    for e in ext:
        vrf = cls.vrf_of(e)
        for s in subnets[e.id]:
            if vrf and aci_relevance.classifies(s.scope):
                shared[(e.source_system_id, vrf, s.ip)].add(e.l3out or e.name)
    vrfs = sorted({f"{names.get(sid, f'#{sid}')} · {vrf}" for (sid, vrf, _p), outs in shared.items()
                   if len(outs) > 1 and (sid, vrf) not in contexts})  # fmt: skip
    return [
        Entry("aci:ext_no_subnet", WARN if bare else OK,
              L("Externe EPGs in Contracts ohne Subnetz",
                "External EPGs in contracts without subnet"),
              count=len(bare), cause=", ".join(bare[:SAMPLES]) or None,
              hint=L("Ohne Subnetz ordnet das ACI der externen EPG keine Adresse zu – ihre "
                     "Contracts gelten für niemanden von aussen. Subnetz am APIC prüfen.",
                     "Without a subnet the ACI classifies no address into the external EPG – its "
                     "contracts apply to nobody from outside. Check the subnet on the APIC."),
              link=_link("regeln", q=bare[0].split("|")[-1]) if bare else None),
        Entry("aci:ext_no_scope", INFO if unread else OK,
              L("Externe EPGs mit Subnetz ohne gelesenen Scope",
                "External EPGs with subnet without collected scope"),
              count=len(unread), cause=", ".join(unread[:SAMPLES]) or None,
              hint=L("Automato nimmt den APIC-Standard an (import-security = ordnet zu). APIC neu "
                     "einlesen; bleibt es so, Konto mit Leserecht auf l3extSubnet prüfen.",
                     "Automato assumes the APIC default (import-security = classifies). Collect "
                     "the APIC again; if it stays, check read access to l3extSubnet."),
              link=None),
        Entry("aci:vrf_no_routing", WARN if vrfs else OK,
              L("VRFs mit gleichem Subnetz in mehreren L3Outs ohne gelesenes Routing",
                "VRFs with the same subnet in several L3Outs without collected routing"),
              count=len(vrfs), cause=", ".join(vrfs[:SAMPLES]) or None,
              hint=L("Welcher L3Out gilt, entscheidet die Route der VRF – Routing der Border-Leafs "
                     "mitlesen, sonst bleiben diese Contracts „unbekannt“.",
                     "The route of the VRF decides which L3Out applies – collect the routing of "
                     "the border leafs, otherwise these contracts stay “unknown”."),
              link=_link("routing", tab="l3out") if vrfs else None),
    ]  # fmt: skip


# -- VMs ------------------------------------------------------------------------------------


def _vms_area(session: Session, tenant_id: int, view, counts: dict[str, int]) -> Area:
    from app.store import virtual

    stmt = select(Vm.id, Vm.name, Vm.power_state, Vm.source_system_id).where(
        Vm.tenant_id == tenant_id, Vm.template.is_(False)
    )
    if view is not None:
        stmt = stmt.where(Vm.id.in_(view.ids("vm")))
    vms = list(session.execute(stmt.order_by(Vm.name)))
    title = L("VMs", "VMs")
    if not vms:
        entries = [e for e in _finding_entries("vms", counts) if e.count]
        return Area("vms", title, L("keine VMs", "no VMs"), entries, applicable=bool(entries))
    with_ip = set(session.scalars(select(VmIp.vm_id).where(VmIp.tenant_id == tenant_id).distinct()))
    nics: dict[int, list[VmNic]] = defaultdict(list)
    for nic in session.scalars(
        select(VmNic).join(Vm, Vm.id == VmNic.vm_id).where(Vm.tenant_id == tenant_id)
    ):
        nics[nic.vm_id].append(nic)
    has_aci = session.scalar(
        select(Epg.id).where(Epg.tenant_id == tenant_id, Epg.kind == "epg").limit(1)
    ) is not None  # fmt: skip
    index = virtual.PortgroupIndex.load(session, tenant_id) if has_aci else None
    no_ip_on, no_ip_off, no_pg, no_epg = [], [], [], []
    for vm in vms:
        if vm.id not in with_ip:
            (no_ip_on if vm.power_state == "poweredOn" else no_ip_off).append(vm.name)
        named = [n for n in nics[vm.id] if n.portgroup]
        if not named:
            no_pg.append(vm.name)
        elif index is not None:
            groups = [index.of(vm.source_system_id, n) for n in named]
            if not any(g is not None and g.epg_id for g in groups):
                no_epg.append(vm.name)
    total = len(vms)
    vm_link = _link("objekte", tab="vms")
    entries = [
        Entry("vms:no_ip", _level(len(no_ip_on), total),
              L("Eingeschaltete VMs ohne IP", "Powered-on VMs without IP"),
              count=len(no_ip_on), samples=no_ip_on[:SAMPLES],
              hint=L("VMware Tools meldet keine Adresse – Tools in der VM prüfen. Ohne IP findet "
                     "Automato die VM in Regeln, Weg-Suche und Doku nicht.",
                     "VMware Tools reports no address – check the tools in the VM. Without IP "
                     "Automato does not find the VM in rules, path search and documentation."),
              link=vm_link),
        Entry("vms:no_ip_off", INFO if no_ip_off else OK,
              L("Ausgeschaltete VMs ohne IP", "Powered-off VMs without IP"),
              count=len(no_ip_off), samples=no_ip_off[:SAMPLES], link=vm_link),
        Entry("vms:no_portgroup", _level(len(no_pg), total),
              L("VMs ohne Portgruppe", "VMs without port group"),
              count=len(no_pg), samples=no_pg[:SAMPLES],
              hint=L("Netzwerkkarte ohne Portgruppe (nicht verbunden bzw. nicht gelesen) – die VM "
                     "lässt sich keinem Netz bzw. keiner EPG zuordnen.",
                     "Network adapter without port group (not connected or not read) – the VM "
                     "cannot be assigned to a network or EPG."),
              link=vm_link),
    ]  # fmt: skip
    if index is not None:
        entries.append(Entry(
            "vms:no_epg", INFO if no_epg else OK,
            L("VMs an Portgruppen ohne EPG", "VMs on port groups without EPG"),
            count=len(no_epg), samples=no_epg[:SAMPLES],
            hint=L("Portgruppe ausserhalb des ACI (z. B. Management) bzw. ohne passende EPG – die "
                   "VM zählt nur über ihre IP (EPG-Subnetz).",
                   "Port group outside the ACI (e.g. management) or without a matching EPG – the "
                   "VM only counts via its IP (EPG subnet)."),
            link=_link("objekte", tab="portgroups"),
        ))  # fmt: skip
    entries += _finding_entries("vms", counts)
    entries.sort(key=lambda e: -RANK[e.status])
    placed = total - len(no_ip_on) - len(no_ip_off)
    metric = L(f"{placed} von {total} VMs mit IP", f"{placed} of {total} VMs with IP")
    return Area("vms", title, metric, entries)


# -- IPAM -----------------------------------------------------------------------------------


def _ipam_area(sts: list[SourceState], counts: dict[str, int], now: datetime) -> Area:
    title = L("IPAM", "IPAM")
    refs = [st for st in sts if st.src.ref_role]
    others = [st for st in sts if not st.src.ref_role]
    primary = [st for st in refs if st.src.ref_role == "primary"]
    link = _link("tenants")
    entries: list[Entry] = []
    if not primary:
        if not others and not refs:
            return Area("ipam", title, L("keine Quellsysteme", "no source systems"), [],
                        applicable=False)  # fmt: skip
        entries.append(Entry(
            "ipam:primary", CRIT,
            L("Keine führende Referenzquelle (IPAM)", "No primary reference source (IPAM)"),
            cause=L("Objekte, Netze und VMs lassen sich nicht gegen das IPAM prüfen; Namen in Doku "
                    "und Weg-Suche fehlen.",
                    "Objects, networks and VMs cannot be checked against the IPAM; names in "
                    "documentation and path search are missing."),
            hint=L("Infoblox DDI bzw. NetBox als Quellsystem mit Rolle „IPAM führend“ anlegen "
                   "und einlesen.",
                   "Create Infoblox DDI or NetBox as source system with role “primary IPAM” and "
                   "collect it."),
            link=link))  # fmt: skip
    for st in primary:
        name = st.src.name
        if st.good_at is None:
            entries.append(Entry(f"ipam:{st.src.id}", CRIT,
                                 L(f"{name}: führendes IPAM nicht eingelesen",
                                   f"{name}: primary IPAM not collected"),
                                 link=_link("laeufe", system=st.src.id)))  # fmt: skip
        elif st.stale(now):
            entries.append(Entry(f"ipam:{st.src.id}", WARN,
                                 L(f"{name}: führendes IPAM veraltet ({_ago(now - st.good_at)})",
                                   f"{name}: primary IPAM outdated ({_ago(now - st.good_at)})"),
                                 cause=L(f"Grenze {_days(st.limit)} Tage",
                                         f"limit {_days(st.limit)} days"),
                                 link=_link("laeufe", system=st.src.id)))  # fmt: skip
        else:
            entries.append(Entry(f"ipam:{st.src.id}", OK,
                                 L(f"{name}: führendes IPAM, Stand {_ago(now - st.good_at)}",
                                   f"{name}: primary IPAM, data from {_ago(now - st.good_at)}"),
                                 count=st.good.rule_count if st.good else None,
                                 link=_link("abgleich")))  # fmt: skip
    entries += _finding_entries("ipam", counts)
    entries.sort(key=lambda e: -RANK[e.status])
    metric = (
        L(f"führend: {', '.join(st.src.name for st in primary)}",
          f"primary: {', '.join(st.src.name for st in primary)}")
        if primary else L("ohne führendes IPAM", "without primary IPAM")
    )  # fmt: skip
    return Area("ipam", title, metric, entries)


# -- Zonen und Kunden -----------------------------------------------------------------------


def _nets(values) -> list:
    out: dict = {}
    for v in values:
        try:
            net = ipaddress.ip_network(str(v), strict=False)
        except ValueError:
            continue
        if net.prefixlen == net.max_prefixlen or net.is_loopback or net.is_link_local:
            continue  # Host-Adressen, Loopbacks: kein Netz einer Zone
        out[net] = None
    return sorted(out, key=lambda n: (n.version, int(n.network_address), n.prefixlen))


def _assignment_area(session: Session, tenant_id: int, view, counts: dict[str, int]) -> Area:
    from app.store import customers, zones

    title = L("Zonen & Kunden", "Zones & customers")
    entries: list[Entry] = []
    items = zones.load(session, tenant_id)
    metric_parts = []
    if items:
        if view is not None:
            values = view.nets(session)
        else:  # gelesene Netze: Interfaces/BDs (Inventar) und Subnetze der EPGs im Fabric
            values = [*session.scalars(
                select(InvSubnet.prefix).where(InvSubnet.tenant_id == tenant_id).distinct()
            ), *session.scalars(
                select(EpgSubnet.ip).join(Epg, Epg.id == EpgSubnet.epg_id)
                .where(Epg.tenant_id == tenant_id, EpgSubnet.origin != "ext").distinct()
            )]  # fmt: skip
        nets = _nets(values)
        index = zones.ZoneIndex(zones.derive(session, tenant_id, items))
        none = [str(n) for n in nets if index.of_net(n) == frozenset({zones.NONE})]
        entries.append(Entry(
            "zones:unzoned", _level(len(none), len(nets)),
            L("Gelesene Netze ohne Zone", "Collected networks without zone"),
            count=len(none), samples=none[:SAMPLES],
            hint=L("Netz einer logischen Zone zuordnen (Netzwerkdesign → Zonen bzw. Vorschläge) – "
                   "Regeln dorthin stehen sonst in der Zonenmatrix unter „ohne Zone“.",
                   "Assign the network to a logical zone (Network design → Zones or Suggestions) – "
                   "otherwise rules to it appear under “without zone” in the zone matrix."),
            link=_link("design"),
        ))  # fmt: skip
        inside = len(nets) - len(none)
        metric_parts.append(L(f"{inside} von {len(nets)} Netzen in Zonen",
                              f"{inside} of {len(nets)} networks in zones"))  # fmt: skip
    else:
        entries.append(Entry("zones:none", INFO, L("Keine logischen Zonen erfasst",
                                                   "No logical zones recorded"),
                             link=_link("design")))  # fmt: skip
    has_customers = session.scalar(
        select(Customer.id).where(Customer.tenant_id == tenant_id, Customer.shared.is_(False))
        .limit(1)
    ) is not None  # fmt: skip
    if has_customers and view is None:
        what = {"epg": ("EPGs", "EPGs"), "rule": ("Regeln", "Rules"), "vm": ("VMs", "VMs")}
        free = 0
        for kind, data in customers.unassigned(session, tenant_id, SAMPLES).items():
            n = data["count"]
            free += n
            de, en = what[kind]
            entries.append(Entry(
                f"customers:{kind}", WARN if n else OK,
                L(f"{de} ohne Kunde", f"{en} without customer"),
                count=n, samples=[str(s) for s in data["samples"]][:SAMPLES],
                hint=L("Selektor beim passenden Kunden ergänzen bzw. einen geteilten Kunden "
                       "(z. B. für common) anlegen – sonst fehlt es in der Sicht jedes Kunden.",
                       "Add a selector to the matching customer or create a shared customer "
                       "(e.g. for common) – otherwise it is missing in every customer's view."),
                link=_link("kunden"),
            ))  # fmt: skip
        metric_parts.append(L(f"ohne Kunde: {free}", f"without customer: {free}"))
    elif not has_customers:
        entries.append(Entry("customers:none", INFO, L("Keine Kunden erfasst",
                                                       "No customers recorded"),
                             link=_link("kunden")))  # fmt: skip
    entries += _finding_entries("assignment", counts)
    entries.sort(key=lambda e: -RANK[e.status])
    applicable = bool(items) or has_customers
    metric = "; ".join(metric_parts) or (
        L("Kunde gewählt", "customer selected") if has_customers
        else L("ohne Zonen und Kunden", "without zones and customers"))  # fmt: skip
    return Area("assignment", title, metric, entries, applicable=applicable)


# -- Bericht --------------------------------------------------------------------------------


def report(session: Session, tenant_id: int, view=None, settings: Settings | None = None,
           now: datetime | None = None) -> dict:  # fmt: skip
    """Datenqualitaet des Mandanten (mit `view` = Ausschnitt eines Kunden, `customer_scope`):
    Bereiche, Eintraege, Gesamtnote. Quellsysteme und IPAM gelten fuer alle Kunden (geteilt)."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    names = dict(session.execute(
        select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    ).all())  # fmt: skip
    contexts = set(session.execute(
        select(TopoContext.source_system_id, TopoContext.context).where(
            TopoContext.tenant_id == tenant_id)
    ).all())  # fmt: skip
    every = states(session, tenant_id, settings, now, enabled_only=False)
    sts = [st for st in every if st.src.enabled]
    off = [st for st in every if not st.src.enabled]
    counts = _open_findings(session, tenant_id, view)
    rules, sample = _rules_area(session, tenant_id, view, counts, contexts, names)
    areas = [
        _sources_area(sts, off, now),
        rules,
        _vms_area(session, tenant_id, view, counts),
        _ipam_area(sts, counts, now),
        _assignment_area(session, tenant_id, view, counts),
    ]
    if view is not None:
        shared = L("gilt für alle Kunden (geteilt)", "applies to all customers (shared)")
        for a in areas:
            if a.key in ("sources", "ipam"):
                a.note = shared
    counted = [a for a in areas if a.applicable]
    good = sum(1 for a in counted if a.status == OK)
    worst = max((RANK[a.status] for a in counted), default=0)
    return {
        "tenant_id": tenant_id,
        "customer_id": view.own[0] if view is not None and len(view.own) == 1 else None,
        "generated_at": now.isoformat(),
        "stale_days": settings.stale_days,
        "score": {
            "percent": round(100 * good / len(counted)) if counted else None,
            "ok": good,
            "total": len(counted),
            "status": {0: OK, 1: WARN, 2: CRIT}[worst],
            "by_status": dict(Counter(a.status for a in counted)),
        },
        "sample": sample or None,
        "areas": [
            {
                **{k: v for k, v in asdict(a).items() if k != "entries"},
                "status": a.status,
                "entries": [asdict(e) for e in a.entries],
            }
            for a in areas
        ],  # fmt: skip
    }
