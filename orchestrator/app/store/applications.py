"""Applikationen (ADR 0012): Mitglieder aufloesen, Vorschlaege per Projektnummer, Doku.

Mitglieder sind Text-Verweise (siehe `app.models.application`). Aufgeloest wird gegen den
aktuellen Stand: ein Application Profile (`<ACI-Tenant>/<AP>`) umfasst alle seine EPGs, eine
EPG steht per DN, ein Server per Name des Host-Objekts. Was nicht mehr gelesen wird, bleibt
als Mitglied stehen und wird als „nicht gefunden" gezeigt.

Vorschlaege (ADR 0004 E4, ~90 % automatisch): die 3-stellige Projektnummer aus `AP_<nr>`
bzw. aus dem Hostnamen `H_<funktion+nr>-<nr>-<firma>.<domain>` gruppiert noch nicht
zugeordnete APs und Server; `000` (Standort/extern) gilt nicht als Projekt. Den Namen nimmt
der Vorschlag aus dem Woerterbuch `project` des Namensprofils, sonst „Projekt <nr>"; gibt es
schon eine Applikation mit diesem Kuerzel, ergaenzt der Vorschlag deren Mitglieder.
"""

import ipaddress
import re
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import naming
from app.i18n import L, german
from app.models import (
    Application,
    ApplicationMember,
    ApplicationRuleHide,
    DeviceObject,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    Finding,
    FindingStatus,
    FindingType,
    NetworkObject,
    ObjectCategory,
    ObjectClass,
    Rule,
    RuleDomain,
    RuleIndex,
    Site,
    SourceSystem,
    SubjectKind,
    Tenant,
    Vm,
    VmIp,
    VmNic,
)
from app.store import (
    appdoc,
    appdoc_filter,
    appdoc_zones,
    customer_scope,
    finding_text,
    parallel,
    peer_names,
    references,
    rule_index,
    sites,
    virtual,
)
from app.store import naming_profiles as profile_store
from app.store import nat as nat_store
from app.store import rules as rules_store
from app.store.epgs import inventory_label

NO_PROJECT = "000"  # Standort/extern unbekannt - keine Projektklammer
PROJECT_DICTIONARY = "project"  # optionale Woerterbuch-Art: Projektnummer -> Name

_AP_NR = re.compile(r"^AP_(\d{3})(?!\d)", re.IGNORECASE)
_HOST_NR = re.compile(r"^(?:H_)?[a-z]{2,4}\d{0,3}-(\d{3})-", re.IGNORECASE)


def project_number(kind: str, name: str | None) -> str | None:
    """Projektnummer aus dem Namen (pure): `AP_002` bzw. `H_app01-002-firma.domain`."""
    pattern = {"ap": _AP_NR, "host": _HOST_NR}.get(kind)
    hit = pattern.match(name) if pattern and name else None
    if hit is None or hit[1] == NO_PROJECT:
        return None
    return hit[1]


def ap_ref(aci_tenant: str | None, ap: str) -> str:
    return f"{aci_tenant}/{ap}" if aci_tenant else ap


def epg_ref(epg: Epg) -> str:
    return epg.dn or epg.name


@dataclass
class Candidate:
    kind: str
    ref: str
    label: str
    detail: str | None = None
    project: str | None = None
    applications: list[int] = field(default_factory=list)


@dataclass
class Resolved:
    label: str
    exists: bool
    epgs: list[Epg] = field(default_factory=list)
    hosts: list[NetworkObject] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)


# Regeln direkt zuordnen bzw. ausschliessen (0.60.0): Verweis `<Quellsystem>|<VDOM>|<ID>` -
# Regeln werden je Lauf ersetzt, Quellsystem, VDOM und Policy-ID bzw. DN des Contracts bleiben
RULE_KINDS = ("rule", "norule")


def rule_ref(rule: Rule) -> str:
    return f"{rule.source_system_id}|{rule.vdom or ''}|{rule.external_id}"


def _rule_label(rule: Rule, source: str | None) -> str:
    if rule.domain == RuleDomain.ACI_CONTRACT:
        name = f"Contract {rule.name or rule.external_id}"
    else:
        quoted = L(f" „{rule.name}“", f" “{rule.name}”") if rule.name else ""
        name = f"Policy {rule.external_id}" + quoted
    vdom = rule.vdom if rule.domain == RuleDomain.FIREWALL and rule.vdom != "root" else None
    where = (source or f"#{rule.source_system_id}") + (f" · VDOM {vdom}" if vdom else "")
    return f"{name} ({where})"


class Resolver:
    """Loest Mitglieder eines Mandanten gegen den aktuellen Stand auf (einmal geladen)."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        stmt = select(Epg).where(Epg.tenant_id == tenant_id, Epg.kind == "epg")
        self.epgs = list(session.scalars(stmt.order_by(Epg.aci_tenant, Epg.ap, Epg.name)))
        self.by_dn = {e.dn: e for e in self.epgs if e.dn}
        self.by_name: dict[str, list[Epg]] = defaultdict(list)
        self.by_ap: dict[str, list[Epg]] = defaultdict(list)
        for e in self.epgs:
            self.by_name[e.name].append(e)
            if e.ap:
                self.by_ap[ap_ref(e.aci_tenant, e.ap)].append(e)
        self._hosts: dict[str, NetworkObject | None] = {}

    def host(self, name: str) -> NetworkObject | None:
        if name not in self._hosts:
            self._hosts[name] = self.session.scalars(
                select(NetworkObject).where(
                    NetworkObject.tenant_id == self.tenant_id,
                    NetworkObject.object_class == ObjectClass.HOST,
                    NetworkObject.name == name,
                )
            ).first()
        return self._hosts[name]

    def resolve(self, kind: str, ref: str) -> Resolved:
        if kind == "ap":
            epgs = self.by_ap.get(ref, [])
            return Resolved(label=ref, exists=bool(epgs), epgs=list(epgs))
        if kind == "epg":
            epg = self.by_dn.get(ref) if ref.startswith("uni/") else None
            if epg is None and not ref.startswith("uni/"):  # Altbestand ohne DN
                epg = next(iter(self.by_name.get(ref, [])), None)
            if epg is None:
                return Resolved(label=_dn_label(ref), exists=False)
            return Resolved(label=inventory_label(epg), exists=True, epgs=[epg])
        if kind == "host":
            obj = self.host(ref)
            return Resolved(label=ref, exists=obj is not None, hosts=[obj] if obj else [])
        if kind in RULE_KINDS:
            rule = self.rule(ref)
            if rule is None:
                return Resolved(label=f"{L('Regel', 'Rule')} {ref.split('|')[-1]}", exists=False)
            return Resolved(label=_rule_label(rule, self.sources.get(rule.source_system_id)),
                            exists=True, rules=[rule])  # fmt: skip
        return Resolved(label=ref, exists=False)

    @cached_property
    def sources(self) -> dict[int, str]:
        return dict(self.session.execute(
            select(SourceSystem.id, SourceSystem.name).where(
                SourceSystem.tenant_id == self.tenant_id)).all())  # fmt: skip

    def rule(self, ref: str) -> Rule | None:
        """Aktuelle Regel zu `<Quellsystem>|<VDOM>|<ID>` (die neuste, falls mehrere)."""
        sid, _, rest = ref.partition("|")
        vdom, _, ext = rest.partition("|")
        if not sid.isdigit() or not ext:
            return None
        stmt = select(Rule).where(
            Rule.tenant_id == self.tenant_id,
            Rule.source_system_id == int(sid),
            Rule.external_id == ext,
        )
        stmt = stmt.where(Rule.vdom == vdom) if vdom else stmt.where(Rule.vdom.is_(None))
        return self.session.scalars(stmt.order_by(Rule.id.desc())).first()


def _dn_label(dn: str) -> str:
    """`uni/tn-TE/ap-AP/epg-EG` -> `TE/AP/EG` (fuer nicht mehr gelesene EPGs)."""
    parts = [p.split("-", 1)[1] for p in dn.split("/")[1:] if "-" in p]
    return "/".join(parts) or dn


def _assignments(session: Session, tenant_id: int) -> dict[tuple[str, str], list[int]]:
    rows = session.execute(
        select(ApplicationMember.kind, ApplicationMember.ref, ApplicationMember.application_id)
        .join(Application, Application.id == ApplicationMember.application_id)
        .where(Application.tenant_id == tenant_id)
    )
    out: dict[tuple[str, str], list[int]] = defaultdict(list)
    for kind, ref, app_id in rows:
        out[(kind, ref)].append(app_id)
    return out


def candidates(
    session: Session, tenant_id: int, resolver: Resolver | None = None
) -> list[Candidate]:
    """Alles, was Mitglied werden kann: APs, EPGs und Server des Mandanten."""
    res = resolver or Resolver(session, tenant_id)
    assigned = _assignments(session, tenant_id)
    out: list[Candidate] = []
    for ref, epgs in sorted(res.by_ap.items()):
        ap = epgs[0].ap
        out.append(
            Candidate(
                kind="ap",
                ref=ref,
                label=ref,
                detail=f"{len(epgs)} EPG" + ("" if len(epgs) == 1 else "s"),
                project=project_number("ap", ap),
            )
        )
    for e in res.epgs:
        out.append(
            Candidate(
                kind="epg",
                ref=epg_ref(e),
                label=inventory_label(e),
                detail=e.bd,
                project=project_number("ap", e.ap),
            )
        )
    hosts = session.execute(
        select(NetworkObject.name, NetworkObject.value)
        .where(
            NetworkObject.tenant_id == tenant_id,
            NetworkObject.object_class == ObjectClass.HOST,
        )
        .order_by(NetworkObject.name)
    )
    for name, value in hosts:
        out.append(
            Candidate(
                kind="host",
                ref=name,
                label=name,
                detail=value,
                project=project_number("host", name),
            )
        )
    for c in out:
        c.applications = sorted(assigned.get((c.kind, c.ref), []))
    return out


def _tenant_of(c: Candidate) -> str | None:
    """ACI-Tenant eines Kandidaten (AP `Tenant/AP`), Server ohne Tenant."""
    return c.ref.split("/", 1)[0] if c.kind == "ap" and "/" in c.ref else None


def _app_tenants(app: Application) -> set[str]:
    """ACI-Tenants der Mitglieder einer Applikation (AP `Tenant/AP`, EPG `uni/tn-<T>/…`)."""
    out = set()
    for m in app.members:
        if m.kind == "ap" and "/" in m.ref:
            out.add(m.ref.split("/", 1)[0])
        elif m.kind == "epg" and m.ref.startswith("uni/tn-"):
            out.add(m.ref[len("uni/tn-") :].split("/", 1)[0])
    return out


def suggestions(session: Session, tenant_id: int) -> list[dict]:
    """Nicht zugeordnete APs und Server, gruppiert nach Projektnummer - je ACI-Tenant getrennt
    (0.74.1, Anwender: `AP_001` in verschiedenen Tenants hat ausser der Nummer nichts gemein).
    Server ohne Tenant gehoeren zur Gruppe ihrer Nummer, wenn es genau eine gibt, sonst eine
    eigene Gruppe. Schluessel `key` = `<Tenant>|<Nummer>` bzw. die Nummer."""
    groups: dict[tuple[str | None, str], list[Candidate]] = defaultdict(list)
    for c in candidates(session, tenant_id):
        if c.kind in ("ap", "host") and c.project and not c.applications:
            groups[(_tenant_of(c), c.project)].append(c)
    if not groups:
        return []
    tenants: dict[str, set[str]] = defaultdict(set)
    for t, nr in groups:
        if t is not None:
            tenants[nr].add(t)
    for t, nr in list(groups):
        if t is None and len(tenants[nr]) == 1:
            groups[(next(iter(tenants[nr])), nr)] += groups.pop((None, nr))
    by_code: dict[str, list[Application]] = defaultdict(list)
    for a in session.scalars(
        select(Application).where(Application.tenant_id == tenant_id).order_by(Application.id)
    ):
        if a.code:
            by_code[a.code].append(a)
    names = profile_store.entries_for_tenant(session, tenant_id, PROJECT_DICTIONARY)
    out = []
    for t, nr in sorted(groups, key=lambda k: (k[1], k[0] or "")):
        apps = by_code.get(nr, [])
        # geteilt: die Nummer gibt es in mehreren Tenants - auch bei bestehenden Applikationen
        known = tenants[nr].union(*(_app_tenants(a) for a in apps)) | ({t} if t else set())
        shared = len(known) + (1 if (None, nr) in groups else 0) > 1
        base = names.get(nr) or f"Projekt {nr}"
        name = f"{base} · {t}" if shared and t else base
        if shared:  # die Applikation dieses Tenants bzw. mit diesem Namen
            app = next((a for a in apps if a.name == name), None) or next(
                (a for a in apps if t and _app_tenants(a) == {t}), None
            )
        else:
            app = apps[0] if apps else None
        out.append(
            {
                "key": f"{t}|{nr}" if t else nr,
                "code": nr,
                "tenant": t,
                "name": app.name if app else name,
                "application_id": app.id if app else None,
                "members": groups[(t, nr)],
            }
        )
    return out


def get(session: Session, application_id: int) -> Application | None:
    return session.get(Application, application_id)


def list_applications(session: Session, tenant_id: int | None) -> list[Application]:
    stmt = select(Application).order_by(Application.name)
    if tenant_id is not None:
        stmt = stmt.where(Application.tenant_id == tenant_id)
    return list(session.scalars(stmt))


def by_name(session: Session, tenant_id: int, name: str) -> Application | None:
    return session.scalars(
        select(Application).where(Application.tenant_id == tenant_id, Application.name == name)
    ).first()


def check_site(session: Session, tenant_id: int, site_id: int | None) -> None:
    if site_id is None:
        return
    site = session.get(Site, site_id)
    if site is None or site.tenant_id != tenant_id:
        raise ValueError("site not found for tenant")


def add_members(
    session: Session,
    app: Application,
    members: list[tuple[str, str]],
    origin: str = "manual",
    resolver: Resolver | None = None,
) -> int:
    """Mitglieder ergaenzen; vorhandene werden uebersprungen. Unbekannte Verweise -> ValueError
    (nur was aktuell gelesen ist, kann zugeordnet werden)."""
    validate_members(session, app.tenant_id, members, resolver)
    have = {(m.kind, m.ref) for m in app.members}
    added = 0
    for kind, ref in members:
        if (kind, ref) in have:
            continue
        app.members.append(ApplicationMember(kind=kind, ref=ref, origin=origin))
        have.add((kind, ref))
        added += 1
    if added:
        touch(app)
    session.flush()
    return added


def validate_members(
    session: Session,
    tenant_id: int,
    members: list[tuple[str, str]],
    resolver: Resolver | None = None,
) -> None:
    """Alle Verweise muessen aktuell gelesen sein, sonst ValueError (nichts wird angelegt)."""
    res = resolver or Resolver(session, tenant_id)
    for kind, ref in members:
        if not res.resolve(kind, ref).exists:
            raise ValueError(f"unknown {kind}: {ref}")


def touch(app: Application) -> None:
    app.updated_at = datetime.now(UTC)


def accept_suggestion(
    session: Session, tenant_id: int, code: str, name: str | None = None
) -> tuple[Application, int, bool] | None:
    """Vorschlag uebernehmen: neue Applikation (Kuerzel = Projektnummer) bzw. Mitglieder der
    bestehenden ergaenzen. `code` = Schluessel des Vorschlags (`<Tenant>|<Nummer>`) bzw. die
    Nummer, wenn sie eindeutig ist. Der Vorschlag wird serverseitig neu berechnet. None = keiner."""
    found = suggestions(session, tenant_id)
    sug = next((s for s in found if s["key"] == code), None)
    if sug is None:  # nur die Nummer: eindeutig, wenn sie in einem Tenant vorkommt
        same = [s for s in found if s["code"] == code]
        sug = same[0] if len(same) == 1 else None
    if sug is None:
        return None
    code = sug["code"]
    created = sug["application_id"] is None
    if created:
        app_name = (name or sug["name"]).strip()
        if by_name(session, tenant_id, app_name) is not None:
            raise LookupError("application name already exists")
        app = Application(tenant_id=tenant_id, name=app_name, code=code)
        session.add(app)
        session.flush()
    else:
        app = session.get(Application, sug["application_id"])
    added = add_members(session, app, [(m.kind, m.ref) for m in sug["members"]], "suggestion")
    return app, added, created


def resolved_members(session: Session, app: Application, resolver: Resolver | None = None):
    res = resolver or Resolver(session, app.tenant_id)
    out = []
    for m in app.members:
        r = res.resolve(m.kind, m.ref)
        systems = [h.name for h in r.hosts] + [inventory_label(e) for e in r.epgs]
        out.append((m, r, systems))
    return out


def system_count(session: Session, app: Application, resolver: Resolver | None = None) -> int:
    hosts, epgs = _systems(session, app, resolver)
    return len(hosts) + len(epgs)


class VmCounter:
    """VMs je Applikation fuer die Liste (0.75.0, Anwender: Applikationen ohne VMs ausblenden
    bzw. loeschen) - wie `_app_vms` (Portgruppe einer EPG bzw. IP eines Servers), aber fuer
    alle Applikationen mit einmal geladenen VMs statt zwei Abfragen je Applikation."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id

    @cached_property
    def _index(self) -> tuple[dict[int, set[int]], dict[str, set[int]]]:
        groups = virtual.PortgroupIndex.load(self.session, self.tenant_id)
        by_epg: dict[int, set[int]] = defaultdict(set)
        for vid, sid, nic in self.session.execute(
            select(Vm.id, Vm.source_system_id, VmNic)
            .join(VmNic, VmNic.vm_id == Vm.id)
            .where(Vm.tenant_id == self.tenant_id, Vm.template.is_(False))
        ):
            pg = groups.of(sid, nic)
            if pg is not None and pg.epg_id is not None:
                by_epg[pg.epg_id].add(vid)
        by_ip: dict[str, set[int]] = defaultdict(set)
        for ip, vid in self.session.execute(
            select(VmIp.ip, Vm.id)
            .join(Vm, Vm.id == VmIp.vm_id)
            .where(VmIp.tenant_id == self.tenant_id, Vm.template.is_(False))
        ):
            by_ip[ip].add(vid)
        return by_epg, by_ip

    def count(self, app: Application, resolver: Resolver | None = None) -> int:
        hosts, epgs = _systems(self.session, app, resolver)
        by_epg, by_ip = self._index
        vms: set[int] = set()
        for e in epgs:
            vms |= by_epg.get(e.id, set())
        for h in hosts:
            vms |= by_ip.get(_ip(h.value) or "", set())
        return len(vms)


def systems_by_application(
    session: Session, tenant_id: int, apps: list[Application]
) -> dict[int, tuple[set[str], set[str]]]:
    """VM-Namen und IP-Adressen der Systeme je Applikation (NetBox-Sync, ADR 0031) - wie die
    Doku (`build_doc`): VMs an den Portgruppen ihrer EPGs und hinter ihren Servern (gleiche
    IP), dazu Server ohne VM; IPs = Adressen dieser VMs (NICs) und Server. Ohne Vorlagen."""
    res = Resolver(session, tenant_id)
    groups = virtual.PortgroupIndex.load(session, tenant_id)
    names: dict[int, str] = {}
    vm_ips: dict[int, set[str]] = defaultdict(set)
    by_epg: dict[int, set[int]] = defaultdict(set)
    for vid, name, sid, nic in session.execute(
        select(Vm.id, Vm.name, Vm.source_system_id, VmNic)
        .join(VmNic, VmNic.vm_id == Vm.id)
        .where(Vm.tenant_id == tenant_id, Vm.template.is_(False))
    ):
        names[vid] = name
        vm_ips[vid] |= set(nic.ips or [])
        pg = groups.of(sid, nic)
        if pg is not None and pg.epg_id is not None:
            by_epg[pg.epg_id].add(vid)
    by_ip: dict[str, set[int]] = defaultdict(set)
    for ip, vid in session.execute(
        select(VmIp.ip, Vm.id)
        .join(Vm, Vm.id == VmIp.vm_id)
        .where(VmIp.tenant_id == tenant_id, Vm.template.is_(False))
    ):
        by_ip[ip].add(vid)
    out: dict[int, tuple[set[str], set[str]]] = {}
    for app in apps:
        hosts, epgs = _systems(session, app, res)
        vms: set[int] = set()
        for e in epgs:
            vms |= by_epg.get(e.id, set())
        ips = {ip for h in hosts if (ip := _ip(h.value))}
        for ip in ips:
            vms |= by_ip.get(ip, set())
        for vid in vms:
            ips |= vm_ips.get(vid, set())
        out[app.id] = ({names[v] for v in vms if v in names}, ips)
    return out


def app_systems(session: Session, app: Application, resolver: Resolver | None = None):
    """Server (Host-Objekte) und EPGs der Applikation aus ihren Mitgliedern."""
    return _systems(session, app, resolver)


def _systems(session: Session, app: Application, resolver: Resolver | None = None):
    res = resolver or Resolver(session, app.tenant_id)
    hosts: dict[int, NetworkObject] = {}
    epgs: dict[int, Epg] = {}
    for m in app.members:
        r = res.resolve(m.kind, m.ref)
        hosts.update((h.id, h) for h in r.hosts)
        epgs.update((e.id, e) for e in r.epgs)
    return list(hosts.values()), list(epgs.values())


KIND_LABEL = {"ap": "AP", "epg": "EPG", "host": "Server", "rule": "Regel",
              "norule": "ohne Regel"}  # fmt: skip
_KIND_LABEL_EN = {"rule": "Rule", "norule": "excluded rule"}  # Anzeige Englisch (ADR 0028)


def kind_label(kind: str) -> str:
    de = KIND_LABEL.get(kind, kind)
    return L(de, _KIND_LABEL_EN.get(kind, de))


# Reihenfolge der Bezugsarten: eine Regel zaehlt mit ihrem engsten Bezug; direkt zugeordnet
# (0.60.0) vor allem
_SCOPE_RANK = {"assigned": -1, "specific": 0, "subnet": 1, "vzany": 2, "pref_group": 3, "any": 4}
_OPEN = (FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED)


def _origins(session: Session, app: Application, res: Resolver):
    """Server und EPGs der Applikation mit dem Mitglied, ueber das sie dazugehoeren."""
    hosts: dict[int, tuple[NetworkObject, str]] = {}
    epgs: dict[int, tuple[Epg, str]] = {}
    for m in app.members:
        r = res.resolve(m.kind, m.ref)
        why = f"{kind_label(m.kind)} {r.label}"
        for h in r.hosts:
            hosts.setdefault(h.id, (h, why))
        for e in r.epgs:
            epgs.setdefault(e.id, (e, why))
    return hosts, epgs


def _ip(value: str | None) -> str | None:
    return value.split("/")[0] if value else None


def _app_vms(
    session: Session, tenant_id: int, epgs: dict, hosts: dict, groups: virtual.PortgroupIndex
) -> dict[int, dict]:
    """VMs der Applikation: an den Portgruppen ihrer EPGs und hinter ihren Servern (gleiche IP),
    je VM mit den Gruenden (Portgruppe -> EPG bzw. IP = Server). Die Portgruppe einer NIC ueber
    Name, Switch und VLAN (`virtual.PortgroupIndex`, Dev-Befund 0.43.0)."""
    out: dict[int, dict] = {}

    def entry(vm: Vm) -> dict:
        return out.setdefault(vm.id, {"vm": vm, "epg_ids": set(), "hosts": {}, "why": []})

    names = {pg.name for pg in groups.groups if pg.epg_id in epgs}
    if names:
        rows = session.execute(
            select(Vm, VmNic)
            .join(VmNic, VmNic.vm_id == Vm.id)
            .where(Vm.tenant_id == tenant_id, VmNic.portgroup.in_(names), Vm.template.is_(False))
        )
        for vm, nic in rows:
            pg = groups.of(vm.source_system_id, nic)
            if pg is None or pg.epg_id not in epgs:
                continue
            e = entry(vm)
            e["epg_ids"].add(pg.epg_id)
            label = inventory_label(epgs[pg.epg_id][0])
            e["why"].append(f"{L('Portgruppe', 'Port group')} {pg.name} → EPG {label} "
                            f"({epgs[pg.epg_id][1]})")  # fmt: skip
    by_ip = {_ip(h.value): (h, why) for h, why in hosts.values() if h.value}
    if by_ip:
        rows = session.execute(
            select(VmIp.ip, Vm)
            .join(Vm, Vm.id == VmIp.vm_id)
            .where(VmIp.tenant_id == tenant_id, VmIp.ip.in_(list(by_ip)), Vm.template.is_(False))
        )
        for ip, vm in rows:
            h, why = by_ip[ip]
            e = entry(vm)
            e["hosts"][h.id] = h
            e["why"].append(f"IP {ip} = Server {h.name} ({why})")
    return out


def _project_hint(vm_name: str, portgroup: str | None, epg: Epg) -> str | None:
    """Hinweis, wenn der VM-Name ein anderes Projekt nennt als das AP der EPG ihrer Portgruppe
    (Dev-Befund 0.43.0: VM `…-000-…` an der Portgruppe einer EPG in `AP_001`). Nur ein Hinweis
    aus der Namenskonvention - die Zuordnung folgt der Portgruppe."""
    vm_nr = _HOST_NR.match(vm_name or "")
    ap_nr = _AP_NR.match(epg.ap or "")
    if vm_nr is None or ap_nr is None or vm_nr[1] == ap_nr[1]:
        return None
    return L(
        f"Name nennt Projekt {vm_nr[1]}, die Portgruppe {portgroup} gehört zur EPG "
        f"{inventory_label(epg)} (Projekt {ap_nr[1]})",
        f"Name refers to project {vm_nr[1]}, the port group {portgroup} belongs to the EPG "
        f"{inventory_label(epg)} (project {ap_nr[1]})",
    )


def _address_objects(session: Session, tenant_id: int, ips: set[str]) -> dict[str, list]:
    """IP -> Firewall-Adressobjekte mit genau dieser IP (auch /32 bzw. /128)."""
    if not ips:
        return {}
    out: dict[str, list[DeviceObject]] = defaultdict(list)
    for o in session.scalars(
        select(DeviceObject).where(
            DeviceObject.tenant_id == tenant_id,
            DeviceObject.category == ObjectCategory.ADDRESS,
        )
    ):
        values = [str(v).removesuffix("/32").removesuffix("/128") for v in o.values or []]
        if len(values) == 1 and values[0] in ips:
            out[values[0]].append(o)
    return out


def _object_groups(objs: list[DeviceObject], dns: list[str], prefixes) -> list[dict]:
    """Firewall-Objekte je Name: wo der Name steht (Objekt-ID, Firewall) und ob er zum DNS-Namen
    passt (`H_web01` ~ `web01.example.ch`; None = kein DNS-Name im IPAM)."""
    shorts = {naming.short_name(n) for n in dns}
    by_name: dict[str, list[DeviceObject]] = defaultdict(list)
    for o in objs:
        by_name[o.name].append(o)
    out = []
    for name, group in by_name.items():
        rest = naming.parse_object_name(name, prefixes).remainder or name
        ok = naming.short_name(rest) in shorts if shorts else None
        where = [
            {"id": o.id, "source_system_id": o.source_system_id, "vdom": o.vdom}
            for o in sorted(group, key=lambda o: (o.source_system_id, o.vdom or "", o.id))
        ]
        out.append({"name": name, "name_ok": ok, "where": where})
    return sorted(out, key=lambda g: (g["name_ok"] is not True, g["name"]))


def _hosts_with_ips(session: Session, tenant_id: int, ips: list[str]) -> list[NetworkObject]:
    if not ips:
        return []
    values = set(ips) | {f"{ip}/32" for ip in ips} | {f"{ip}/128" for ip in ips}
    return list(
        session.scalars(
            select(NetworkObject).where(
                NetworkObject.tenant_id == tenant_id,
                NetworkObject.object_class == ObjectClass.HOST,
                NetworkObject.value.in_(values),
            )
        )
    )


def build_doc(session: Session, app: Application, rule_ids: set[int] | None = None,
              names: bool = False) -> dict:  # fmt: skip
    """Applikations-Doku (Dev-Befund 0.14.0): die Doku steht im Mittelpunkt.

    - `systems`: alle VMs der Applikation (Portgruppen ihrer EPGs, VMs hinter ihren Servern)
      und Server ohne VM - je mit IPs, DNS-Namen, EPG (ganzer ACI-Pfad), Lage und dem Grund,
      warum sie dazugehoeren;
    - `epgs`: die EPGs der Applikation im ACI (BD, VRF, Subnetze, VMs, Endpunkte);
    - `hits`: je Regel, System und Seite ein Treffer mit Richtung (aus Sicht des Systems),
      Bezug (direkt, Gruppe, Netz, any, vzAny, ESG, EPG) und Art des Bezugs
      (spezifisch bzw. unspezifisch: Subnetz, any, vzAny, Preferred Group);
    - `rules`: die Regeln wie in der Regel-Ansicht (Aktion, Quelle, Ziel, Dienste, NAT);
    - `summary`: Kennzahlen (VMs, Systeme, EPGs, Regeln spezifisch/unspezifisch);
    - `findings`: offene Befunde zu Regeln, Servern, VMs, Portgruppen und Subnetzen;
    - `tables`: die 10 Kategorien fuer Export und Druck (gleiche Zeilen einmal); mit `names`
      (Einstellung des Mandanten, nur Betreuer - 0.95.0) Gegenstellen nur mit Adresse samt Namen
      (`peer_names`).
    """
    tid = app.tenant_id
    restricted = rule_ids is not None  # Kunden-Benutzer: nur ihre Regeln (ADR 0018)
    res = Resolver(session, tid)
    hosts, epgs = _origins(session, app, res)
    data = appdoc.TenantData(session, tid)
    ctx = references.context(session, tid)
    site_index = sites.SiteIndex(session, tid)
    portgroups = virtual.PortgroupIndex.load(session, tid)
    vms = _app_vms(session, tid, epgs, hosts, portgroups)

    # VRFs der Applikation: fuer Systeme ohne Portgruppe zaehlen gleiche IPs in anderen
    # ACI-Tenants/VRFs nicht (Dev-Befund 0.42.2, appdoc.fabric_epgs)
    prefer = {appdoc.vrf_key(data, e) for e, _why in epgs.values() if e.kind == "epg"}

    def dns(ips: list[str]) -> list[str]:
        return list(dict.fromkeys(n for ip in ips for n in references.dns_names(ctx, ip)))

    def site_of(objs: list[NetworkObject]) -> dict | None:
        site = next((session.get(Site, o.site_id) for o in objs if o.site_id), None)
        return site_index.ref(site) if site else None

    systems: list[dict] = []
    rows: list[tuple[str, int, dict]] = []  # (System-Schluessel, Tabelle, Zeile)
    covered_hosts: set[int] = set()
    for vm_id, e in sorted(vms.items(), key=lambda kv: kv[1]["vm"].name):
        vm: Vm = e["vm"]
        ips = list(dict.fromkeys(ip for n in vm.nics for ip in n.ips or []))
        servers = {h.id: h for h in _hosts_with_ips(session, tid, ips)} | e["hosts"]
        covered_hosts |= set(servers)
        nic_epgs = set(e["epg_ids"])
        nets: list[str] = []
        nics: list[dict] = []
        mapped: set[str] = set()  # IPs an einer NIC mit EPG (Portgruppe) - Vorgabe vor Gelerntem
        hints: list[str] = []
        for n in vm.nics:
            pg = portgroups.of(vm.source_system_id, n) if n.portgroup else None
            epg = data.epgs_by_id.get(pg.epg_id) if pg is not None and pg.epg_id else None
            if epg is not None:
                nic_epgs.add(epg.id)
                mapped |= set(n.ips or [])
                hint = _project_hint(vm.name, n.portgroup, epg)
                if hint:
                    hints.append(hint)
            if n.portgroup:
                nets.append(f"{n.portgroup}" + (f" (VLAN {n.vlan})" if n.vlan is not None else ""))
            nics.append(
                {
                    "name": n.name,
                    "portgroup": n.portgroup,
                    "vlan": n.vlan,
                    "ips": n.ips or [],
                    "epg": inventory_label(epg) if epg is not None else None,
                    # wie die Portgruppe zur EPG kam: name | vlan (Befund „nur ueber VLAN“)
                    "match": pg.epg_match if epg is not None else None,
                }
            )
        key = f"vm:{vm_id}"
        systems.append(
            {
                "key": key,
                "kind": "vm",
                "name": vm.name,
                "ips": ips,
                "dns": dns(ips),
                "servers": sorted(h.name for h in servers.values()),
                "epgs": sorted(
                    inventory_label(data.epgs_by_id[i]) for i in nic_epgs if i in data.epgs_by_id
                ),
                "portgroups": nets,
                "nics": nics,
                "hints": list(dict.fromkeys(hints)),
                "vm_id": vm.id,
                "power_state": vm.power_state,
                "guest_os": vm.guest_os,
                "cluster": vm.cluster,
                "host": vm.host,
                "site": site_of(list(servers.values())),
                "member": "; ".join(dict.fromkeys(e["why"])),
            }
        )
        for idx, row in appdoc.vm_rows(
            session, data, vm.name, ips, list(servers.values()), nic_epgs, prefer, mapped
        ):
            rows.append((key, idx, row))
    for hid, (h, why) in sorted(hosts.items(), key=lambda kv: kv[1][0].name):
        if hid in covered_hosts:
            continue  # steht als VM da
        key = f"host:{hid}"
        doc = appdoc.host_doc(session, h, data, full=True, prefer=prefer)
        ip = _ip(h.value)
        systems.append(
            {
                "key": key,
                "kind": "host",
                "name": h.name,
                "ips": [ip] if ip else [],
                "dns": dns([ip] if ip else []),
                "servers": [h.name],
                "epgs": sorted(
                    inventory_label(x) for x in appdoc.host_epgs(session, data, h, prefer=prefer)
                ),
                "portgroups": [],
                "nics": [],
                "hints": [],
                "vm_id": None,
                "power_state": None,
                "guest_os": None,
                "cluster": None,
                "host": None,
                "site": doc["site"],
                "member": why,
            }
        )
        rows += [(key, t["index"], r) for t in doc["tables"] for r in t["rows"]]

    # Firewall-Adressobjekte mit genau diesen IPs, je Name zusammengefasst mit den Firewalls,
    # auf denen er steht, und ob er zum DNS-Namen im IPAM passt (Dev-Befund 0.14.1)
    by_ip = _address_objects(session, tid, {ip for x in systems for ip in x["ips"]})
    prefixes = profile_store.prefixes_for_tenant(session, tid)
    for x in systems:
        found = {o.id: o for ip in x["ips"] for o in by_ip.get(ip, [])}
        x["objects"] = _object_groups(list(found.values()), x["dns"], prefixes)

    vm_count: dict[int, int] = defaultdict(int)
    for e in vms.values():
        for i in e["epg_ids"]:
            vm_count[i] += 1
    epg_out: list[dict] = []
    for eid, (epg, why) in sorted(epgs.items(), key=lambda kv: inventory_label(kv[1][0])):
        key = f"epg:{eid}"
        doc = appdoc.epg_doc(session, epg, data, full=True)
        rows += [(key, t["index"], r) for t in doc["tables"] for r in t["rows"]]
        subnets = list(
            session.scalars(
                select(EpgSubnet.ip).where(EpgSubnet.epg_id == eid).order_by(EpgSubnet.ip)
            )
        )
        endpoints = session.scalar(
            select(func.count(EpgEndpoint.id)).where(EpgEndpoint.epg_id == eid)
        )
        epg_out.append(
            {
                "key": key,
                "id": eid,
                "label": inventory_label(epg),
                "dn": epg.dn,
                "tenant": epg.aci_tenant,
                "ap": epg.ap,
                "name": epg.name,
                "bd": epg.bd,
                "vrf": epg.vrf,
                "pref_group": bool(epg.pref_group),
                "subnets": subnets,
                "vm_count": vm_count.get(eid, 0),
                "endpoint_count": endpoints or 0,
                "member": why,
            }
        )

    if rule_ids is not None:  # Kunden-Benutzer: nur ihre Regeln (ADR 0018)
        rows = [r for r in rows if r[2].get(appdoc.RULE) in rule_ids]
    # Regeln direkt zugeordnet bzw. ausgeschlossen (0.60.0)
    assigned, excluded = _rule_members(app, res, rule_ids)
    rows = [r for r in rows if r[2].get(appdoc.RULE) not in excluded]
    # parallele Firewalls (0.18.0): gleiche Policies nur einmal - die der fuehrenden Firewall
    twins = parallel.twin_ids(session, {row.get(appdoc.RULE) for _, _, row in rows})
    rows = [r for r in rows if r[2].get(appdoc.RULE) not in twins]
    # innerhalb der Applikation (0.105.0, ADR 0035): Zugriffe, deren Gegenstellen alle Systeme
    # bzw. EPGs der Applikation sind, stehen in der Kategorie mit der Rolle `aci_ap` (Server intern)
    mine = {ip for x in systems for ip in x["ips"]} | {e["dn"] for e in epg_out if e["dn"]}
    inside = data.catalog.role("aci_ap")
    rows = [(k, inside if _inside(row, mine) else idx, row) for k, idx, row in rows]
    # ausgehende Firewall-Regeln mit breiter Quelle (0.103.0, `appdoc_filter.DROPPED`): kein
    # Zugriff dieser Applikation - nicht in der Doku, nur gezaehlt; direkt zugeordnete bleiben
    dropped = [r for r in rows if appdoc_filter.place(r[2], assigned) == appdoc_filter.DROPPED]
    rows = [r for r in rows if appdoc_filter.place(r[2], assigned) != appdoc_filter.DROPPED]
    broad = {r[2].get(appdoc.RULE) for r in dropped} - {r[2].get(appdoc.RULE) for r in rows}
    if names and not restricted:  # Namen der Gegenstellen (0.95.0) - nie fuer Kunden-Benutzer
        peer_names.apply(session, tid, [row for _, _, row in rows])
    hits, tables = _hits_and_tables(rows, _pairs(session, {r[2].get(appdoc.RULE) for r in rows}),
                                    data.catalog, assigned)  # fmt: skip
    narrower = _narrower(rows, systems, epg_out, data.catalog)  # 0.78.2: enger als die Firewall
    # Routingtechnisch unmoegliche Treffer gehoeren nicht in die Applikations-Doku (Dev-Befund
    # 0.33.0, z. B. intern -> Internet ueber any im Ziel: die Antwort braucht keine Regel, ein
    # Zugriff von dort braeuchte eine eigene) - nicht einblendbar, nur gezaehlt
    possible = [h for h in hits if h["relevance"] != "impossible"]
    impossible = {h["rule_id"] for h in hits} - {h["rule_id"] for h in possible}
    possible += [_assigned_hit(rid, m) for rid, m in assigned.items() if rid not in excluded]
    hits = possible
    # L3Outs (ADR 0034): Contracts zu externen EPGs mit Firewall dahinter sind Hilfs-Contracts -
    # an der Firewall-Regel vermerkt, nicht als eigene Regel (Treffer bleiben, `helper`)
    helper_ids = _helper_ids(hits)
    helpers = _helpers_out(hits, helper_ids, systems, epg_out, rows)
    rule_ids = {h["rule_id"] for h in hits} - helper_ids
    ordered = (
        list(
            session.scalars(
                select(Rule.id).where(Rule.id.in_(rule_ids)).order_by(*rules_store.ORDER)
            )
        )
        if rule_ids
        else []
    )
    rules = rule_index.outs(session, rule_index.load_rules(session, ordered))
    best: dict[int, str] = {}
    for h in possible:
        if h["rule_id"] in helper_ids:
            continue
        cur = best.get(h["rule_id"])
        if cur is None or _SCOPE_RANK.get(h["scope"], 9) < _SCOPE_RANK.get(cur, 9):
            best[h["rule_id"]] = h["scope"]
    for item in systems + epg_out:
        mine = {h["rule_id"] for h in possible if h["system"] == item["key"]} - helper_ids
        spec = {
            h["rule_id"]
            for h in possible
            if h["system"] == item["key"] and h["scope"] == "specific"
        }
        item["rules"] = {"specific": len(spec), "unspecific": len(mine - spec)}
    by_scope = {k: sum(1 for v in best.values() if v == k) for k in appdoc.UNSPECIFIC}
    # EPGs je Regel (ueber die Systeme ihrer Treffer) - fuer den Filter der Findings
    epgs_of: dict[str, list[str]] = {x["key"]: x["epgs"] for x in systems}
    epgs_of |= {e["key"]: [e["label"]] for e in epg_out}
    rule_epgs: dict[int, set[str]] = defaultdict(set)
    for h in possible:
        rule_epgs[h["rule_id"]] |= set(epgs_of.get(h["system"], []))
    findings = _app_findings(session, tid, systems, epg_out, set(ordered) | helper_ids,
                             rule_epgs, restricted=restricted, app_id=app.id)  # fmt: skip
    hidden, hides = _apply_hides(session, app, tables, restricted)  # 0.69.0
    # Zonen je Kategorie (0.101.0): im Kopf die Zonen mit ihren Netzen; Kunden-Benutzer ohne
    # Zonen und ohne Spalte „Zone“ (Netze der Infrastruktur)
    zone_heads(data, tables, restricted)
    tenant = session.get(Tenant, tid)
    return {
        "tenant_code": tenant.code if tenant else None,
        "summary": {
            "vms": sum(1 for x in systems if x["kind"] == "vm"),
            "servers": sum(1 for x in systems if x["kind"] == "host"),
            "epgs": len(epg_out),
            "rules": len(ordered),
            "firewall": sum(1 for r in rules if r.domain == RuleDomain.FIREWALL.value),
            "aci": sum(1 for r in rules if r.domain == RuleDomain.ACI_CONTRACT.value),
            "impossible": len(impossible),
            "outbound_broad": len(broad),  # 0.103.0: ausgehend mit breiter Quelle, nicht da
            "specific": sum(1 for v in best.values() if v in ("specific", "assigned")),
            "assigned": sum(1 for v in best.values() if v == "assigned"),
            "excluded": len(excluded),
            "hidden": hidden,
            "unspecific": sum(by_scope.values()),
            "by_scope": by_scope,
            "findings": len(findings),
            "helpers": len(helpers),  # Hilfs-Contracts ueber L3Outs (ADR 0034)
        },
        "systems": systems,
        "epgs": epg_out,
        "hits": hits,
        "rules": rules,
        "findings": findings,
        "tables": tables,
        "narrower": narrower,
        # Hilfs-Contracts ueber L3Outs mit den Firewall-Regeln, an denen sie stehen (ADR 0034)
        "helpers": helpers,
        # zugeordnete bzw. ausgeschlossene Regeln mit dem Mitglied (zum Rueckgaengigmachen)
        "rule_members": [
            {"member_id": m.id, "kind": m.kind, "label": label, "rule_id": rid}
            for rid, (m, label) in (assigned | excluded).items()
        ],
        "hides": hides,
        # Eingaben der Befunde (0.108.0): aus dem gespeicherten Stand aktuell nachgerechnet
        "finding_inputs": {
            "rule_ids": sorted(set(ordered) | helper_ids),
            "rule_epgs": [[rid, sorted(v)] for rid, v in sorted(rule_epgs.items())],
        },
    }


def doc_findings(session: Session, app: Application, doc: dict, restricted: bool) -> list[dict]:
    """Befunde der Doku aktuell (0.108.0, ADR 0037): der Stand der Doku bleibt, die Befunde
    (Triage, neue Laeufe der Befunde) kommen bei jedem Aufruf aus der Datenbank."""
    inputs = doc.get("finding_inputs") or {}
    rule_epgs = {int(rid): set(epgs) for rid, epgs in inputs.get("rule_epgs") or []}
    return _app_findings(session, app.tenant_id, doc["systems"], doc["epgs"],
                         set(inputs.get("rule_ids") or []), rule_epgs, restricted=restricted,
                         app_id=app.id)  # fmt: skip


def zone_heads(data: appdoc.TenantData, tables: list[dict], restricted: bool) -> None:
    """Zonen je Kategorie (0.101.0) in den Kopf der Tabellen; Kunden-Benutzer ohne Zonen."""
    if restricted:
        appdoc_zones.strip(tables, appdoc.ZONE_COL)
        return
    appdoc_zones.heads(data.zone_map, tables)


def hide_out(h: ApplicationRuleHide) -> dict:
    return {"id": h.id, "rule_ref": h.rule_ref, "category": h.category, "reason": h.reason,
            "label": h.label, "by": h.created_by,
            "at": h.created_at.isoformat() if h.created_at else None}  # fmt: skip


def hide_rule(session: Session, app: Application, rule: Rule, category: int | None, reason: str,
              actor: str) -> ApplicationRuleHide:  # fmt: skip
    """Regel in einer Kategorie (leer = alle) ausblenden (0.69.0); schon ausgeblendet = neue
    Begruendung."""
    ref = rule_ref(rule)
    hide = next((h for h in app.hides if h.rule_ref == ref and h.category == category), None)
    source = session.get(SourceSystem, rule.source_system_id)
    if hide is None:
        with german():  # gespeichert: immer deutsch (ADR 0028)
            label = _rule_label(rule, source.name if source else None)
        hide = ApplicationRuleHide(rule_ref=ref, category=category, reason=reason, label=label,
                                   created_by=actor)  # fmt: skip
        app.hides.append(hide)
    else:
        hide.reason, hide.created_by, hide.created_at = reason, actor, datetime.now(UTC)
    session.flush()
    return hide


def _apply_hides(session: Session, app: Application, tables: list[dict],
                 restricted: bool) -> tuple[int, list[dict]]:  # fmt: skip
    """Ausgeblendete Regeln (0.69.0): Zeilen der Kategorie mit einer ihrer Regeln -> `hidden`
    (mit `_hides`). Dazu alle Eintraege mit `active` (steht in einer Kategorie); Kunden-Benutzer
    sehen nur die ihrer Regeln."""
    for t in tables:
        t["hidden"] = []
    if not app.hides:
        return 0, []
    ids = {rid for t in tables for r in t["rows"] for rid in r.get("_rules") or [] if rid}
    refs = {r.id: rule_ref(r) for r in session.scalars(select(Rule).where(Rule.id.in_(ids)))}
    by_ref: dict[str, list[ApplicationRuleHide]] = defaultdict(list)
    for h in app.hides:
        by_ref[h.rule_ref].append(h)
    used: set[int] = set()
    count = 0
    for t in tables:
        keep = []
        for row in t["rows"]:
            hs = [h for rid in row.get("_rules") or [] for h in by_ref.get(refs.get(rid), [])
                  if h.category in (None, t["index"])]  # fmt: skip
            if not hs:
                keep.append(row)
                continue
            used |= {h.id for h in hs}
            t["hidden"].append({**row, "_hides": [hide_out(h) for h in dict.fromkeys(hs)]})
            count += 1
        t["rows"] = keep
    listed = [hide_out(h) | {"active": h.id in used} for h in app.hides
              if not restricted or h.id in used]  # fmt: skip
    return count, listed


def _rule_members(app: Application, res: Resolver, allowed: set[int] | None):
    """Direkt zugeordnete bzw. ausgeschlossene Regeln der Applikation (0.60.0): je aktuelle
    Regel das Mitglied und die Bezeichnung; Kunden-Benutzer nur ihre Regeln."""
    assigned: dict[int, tuple] = {}
    excluded: dict[int, tuple] = {}
    for m in app.members:
        if m.kind not in RULE_KINDS:
            continue
        r = res.resolve(m.kind, m.ref)
        for rule in r.rules:
            if allowed is not None and rule.id not in allowed:
                continue
            (excluded if m.kind == "norule" else assigned)[rule.id] = (m, r.label)
    return assigned, excluded


def _assigned_hit(rule_id: int, member: tuple) -> dict:
    """Treffer einer direkt zugeordneten Regel - gilt fuer die ganze Applikation."""
    via = L("direkt zugeordnet", "assigned directly")
    return {"rule_id": rule_id, "system": "app", "table": 0, "direction": "assigned",
            "side": "app", "object": "", "value": "", "via": via, "scope": "assigned",
            "counterpart": "", "services": "", "epg": None, "parties": None,
            "relevance": "possible", "relevance_text": "", "member_id": member[0].id}  # fmt: skip


def _object_label(row: dict) -> str:
    obj, value = row.get(appdoc.OBJECT) or "", row.get(appdoc.OBJECT_VALUE) or ""
    if not obj:
        return value
    return f"{obj} ({value})" if value and value != obj else obj


def _pairs(session: Session, rule_ids: set) -> dict[int, int]:
    """IPv4/IPv6-Paare (gleich bzw. von Hand bestaetigt) -> die IPv4-Regel des Paars: in den
    Kategorien eine Zeile fuer beide (Dev-Befund 0.33.0)."""
    ids = [i for i in rule_ids if i is not None]
    if not ids:
        return {}
    out: dict[int, int] = {}
    for rid, partner, match, row in session.execute(
        select(
            RuleIndex.rule_id, RuleIndex.pair_rule_id, RuleIndex.pair_match, RuleIndex.row
        ).where(RuleIndex.rule_id.in_(ids), RuleIndex.pair_rule_id.is_not(None))
    ):
        if match in ("same", "confirmed") and partner in rule_ids:
            v4 = rid if (row or {}).get("family") == "v4" else partner
            out[rid] = v4
    return out


def _helper_ids(hits: list[dict]) -> set[int]:
    """Contracts, deren Treffer alle Hilfs-Contracts sind (ADR 0034)."""
    by_rule: dict[int, list[bool]] = defaultdict(list)
    for h in hits:
        by_rule[h["rule_id"]].append(bool(h.get("helper")))
    return {rid for rid, flags in by_rule.items() if flags and all(flags)}


def _helpers_out(hits: list[dict], ids: set[int], systems: list[dict], epgs: list[dict],
                 rows: list[tuple[str, int, dict]]) -> list[dict]:  # fmt: skip
    """Hilfs-Contracts der Applikation: je Contract die L3Outs, die Firewall-Regeln, an denen er
    steht, und die Systeme (ADR 0034)."""
    names = {x["key"]: x["name"] for x in systems} | {e["key"]: e["label"] for e in epgs}
    labels = {r.get(appdoc.RULE): r.get(appdoc.DESCRIPTION) for _k, _i, r in rows}
    out: dict[int, dict] = {}
    for h in hits:
        if h["rule_id"] not in ids:
            continue
        entry = out.setdefault(h["rule_id"], {"rule_id": h["rule_id"],
                                              "name": labels.get(h["rule_id"]) or "",
                                              "l3outs": [], "firewall_rules": [],
                                              "systems": []})  # fmt: skip
        if h.get("l3out") and h["l3out"] not in entry["l3outs"]:
            entry["l3outs"].append(h["l3out"])
        for rid in h.get("helper_for") or []:
            if rid not in entry["firewall_rules"]:
                entry["firewall_rules"].append(rid)
        system = names.get(h["system"], h["system"])
        if system not in entry["systems"]:
            entry["systems"].append(system)
    return sorted(out.values(), key=lambda x: x["name"].lower())


def _narrower(rows: list[tuple[str, int, dict]], systems: list[dict], epgs: list[dict],
              catalog=None) -> list[dict]:  # fmt: skip
    """L3Out-Contracts, die weniger oeffnen als die Firewall-Regeln (0.78.2, `appdoc.NARROW`):
    je Contract die Systeme mit Kategorie, Richtung, fehlenden Diensten und Firewall-Regeln."""
    names = {x["key"]: x["name"] for x in systems} | {e["key"]: e["label"] for e in epgs}
    catalog = catalog or appdoc.DEFAULT_CATALOG
    titles = {c.index: catalog.title(c.index) for c in catalog.ordered()}
    out: dict[int, dict] = {}
    for key, idx, row in rows:
        if not row.get(appdoc.NARROW) or row.get(appdoc.REL) == "impossible":
            continue
        rid = row[appdoc.RULE]
        entry = out.setdefault(rid, {"rule_id": rid, "systems": [], "missing": [],
                                     "firewall_rules": [], "categories": []})  # fmt: skip
        system = names.get(key, key)
        if system not in entry["systems"]:
            entry["systems"].append(system)
        for x in row[appdoc.NARROW]:
            if x not in entry["missing"]:
                entry["missing"].append(x)
        for x in row.get(appdoc.FW_RULES) or []:
            if x not in entry["firewall_rules"]:
                entry["firewall_rules"].append(x)
        cat = f"{idx} {titles.get(idx, '')}".strip()
        if row.get(appdoc.DIRECTION) == "out":
            cat += L(" (ausgehend)", " (outbound)")
        if cat not in entry["categories"]:
            entry["categories"].append(cat)
    return list(out.values())


def _inside(row: dict, mine: set[str]) -> bool:
    """Gegenstellen der Zeile alle in der Applikation (Adressen bzw. DNs)?"""
    refs = row.get(appdoc.CP_REFS) or []
    return bool(refs) and all(r in mine for r in refs)


def _hits_and_tables(
    rows: list[tuple[str, int, dict]], pairs: dict[int, int] | None = None, catalog=None,
    assigned: Collection[int] = (),
) -> tuple[list[dict], list[dict]]:  # fmt: skip
    """Treffer je (Regel, System, Seite) und die Kategorien-Tabellen (Katalog des Mandanten).

    In den Tabellen steht eine Regel je Seite und Objekt der Regel nur einmal: erfasst ein Netz
    bzw. any mehrere Systeme, nennt die Seite der Applikation (`appdoc.APP`, je nach Richtung in
    Source bzw. Destination) das Objekt wie in der Regel statt einer Zeile je System (0.14.1).
    Seit 0.37.0 (Anwender) steht bei unspezifischen Regeln (any,
    Netz, vzAny, Preferred Group) - typisch Zugriffe ins bzw. aus dem Internet oder MPLS - nur
    die Regel mit ihrem Objekt (`any`, `N_srv16 (10.20.0.0/16)`); welche Systeme der
    Applikation sie nutzen koennen, steht in `_systems` (Anzeige auf Knopfdruck). Spezifische
    Objekte (Server, Gruppe) nennen ihre Systeme weiter direkt. Ein IPv4/IPv6-Paar (`pairs`)
    steht als eine Zeile da, beide Regeln in `_rules`."""
    pairs = pairs or {}
    catalog = catalog or appdoc.DEFAULT_CATALOG
    tables = {c.index: {**_table_meta(c, catalog), "rows": []} for c in catalog.ordered()}
    groups: dict[tuple, dict] = {}
    seen_hits: set[tuple] = set()
    hits: list[dict] = []
    for key, idx, row in rows:
        rid = row.get(appdoc.RULE)
        # nur Zeilen der Kategorien (`appdoc_filter`, 0.104.0): Hilfs-Contracts, Contracts nicht
        # enger als die Firewall, unspezifisch ausgehend und routingtechnisch unmoegliche nicht -
        # Treffer bleiben (unmoegliche nur gezaehlt)
        if appdoc_filter.in_categories(row, assigned):
            gkey = (idx, pairs.get(rid, rid), row.get(appdoc.SIDE), row.get(appdoc.OBJECT))
            if rid is None:
                gkey = (idx, None, len(groups))
            group = groups.setdefault(gkey, {"row": row, "systems": [], "rules": {}})
            if rid is not None:
                group["rules"].setdefault(rid, row.get(appdoc.DESCRIPTION) or "")
            if row.get(appdoc.REVERSE) and group["row"] is not row:
                # ACI (0.69.1): Rueckrichtung in derselben Kategorie - mit ihren Diensten
                group["both"] = row[appdoc.REVERSE]
            name = row.get(appdoc.APP)
            if name and name not in group["systems"]:
                group["systems"].append(name)
        if rid is None:
            continue
        hit = (rid, key, row.get(appdoc.DIRECTION), row.get(appdoc.VIA))
        if hit in seen_hits:
            continue
        seen_hits.add(hit)
        hits.append(
            {
                "rule_id": rid,
                "system": key,
                "table": idx,
                "direction": row.get(appdoc.DIRECTION),
                "side": row.get(appdoc.SIDE)
                or ("dst" if row.get(appdoc.DIRECTION) == "in" else "src"),
                "object": row.get(appdoc.OBJECT) or "",
                "value": row.get(appdoc.OBJECT_VALUE) or "",
                "via": row.get(appdoc.VIA) or "",
                "scope": row.get(appdoc.SCOPE) or "specific",
                "counterpart": (row.get(appdoc.PEER) or "").replace("\n", ", "),
                "services": row.get(appdoc.SERVICE) or "",
                # ACI: die EPG, ueber die der Contract fuer das System gilt (Filter, 0.42.3)
                "epg": row.get(appdoc.EPG),
                # ACI (0.43.2): DNs des eigenen Endpunkts und der Gegenstellen im Scope - die
                # Doku zeigt im Contract nur diese, nicht alle Consumer bzw. Provider
                "parties": row.get(appdoc.PARTIES),
                # Routing (0.24.0): possible | impossible | unknown, mit Begruendung
                "relevance": row.get(appdoc.REL) or "unknown",
                "relevance_text": row.get(appdoc.REL_TEXT) or "",
                # ACI ist zustandslos (0.69.1): Treffer der Rueckrichtung (Provider -> Consumer)
                "reverse": bool(row.get(appdoc.REVERSE)),
                # L3Outs (ADR 0034): ueber welchen L3Out der Zugriff kommt; Contract zu externen
                # EPGs mit Firewall dahinter = Hilfs-Contract (keine eigene Regel der Doku), an
                # der Firewall-Regel seine Hilfs-Contracts; Hinweis ohne Firewall dahinter
                "l3out": row.get(appdoc.L3OUT_IN),
                "helper": bool(row.get(appdoc.HELPER)),
                "helper_for": list((row.get(appdoc.HELPER) or {}).get("for") or []),
                "helpers": [
                    {
                        "l3out": x["l3out"],
                        "rule_ids": [c["rule_id"] for c in x["contracts"]],
                        "names": [c["name"] for c in x["contracts"]],
                        "missing": x["missing"],
                    }
                    for x in row.get(appdoc.HELPERS) or []
                ],  # fmt: skip
                "l3out_note": row.get(appdoc.L3OUT_NOTE) or "",
            }
        )
    seen_rows: dict[tuple, dict] = {}
    for (idx, *_), group in groups.items():
        row = dict(group["row"])
        for key in appdoc.INTERNAL:  # nur fuer die Treffer
            row.pop(key, None)
        if group.get("both") and not row.get(appdoc.REVERSE):
            mark = appdoc.both_mark(group["both"] if isinstance(group["both"], list) else None)
            row[appdoc.SERVICE] = f"{row.get(appdoc.SERVICE) or ''} {mark}".strip()
        row["_rules"] = list(group["rules"])
        names = list(dict.fromkeys(n for n in group["rules"].values() if n))
        if len(group["rules"]) > 1:  # IPv4/IPv6-Paar
            row[appdoc.DESCRIPTION] = " / ".join(names) + " (IPv4 + IPv6)"
        systems = group["systems"]
        broad = row.get(appdoc.SCOPE) in appdoc.UNSPECIFIC
        if len(systems) > 1 or broad:
            label = _object_label(row)
            inside = [x for x in systems if x not in label]
            if broad:  # nur die Regel; die Systeme auf Knopfdruck
                row[appdoc.APP] = label
                row["_systems"] = inside
            elif nat_store.CHAIN in str(row.get(appdoc.OBJECT) or ""):
                # ueber VIP und Load Balancer (0.84.0): VIP und Load Balancer stehen in eigenen
                # Spalten, hier nur die Server mit Name und IP
                row[appdoc.APP] = appdoc.lines(systems)
            else:  # Gruppe bzw. Netz mit mehreren Systemen: das Objekt, darunter die Systeme
                row[appdoc.APP] = appdoc.lines([f"{label}:", *inside]) if inside else label
        appdoc.place(row)
        visible = (idx, tuple(row.get(c) for c in appdoc.ALL_COLUMNS))
        if visible in seen_rows:  # gleiche Zeile (z. B. VM und ihre EPG): Systeme zusammen
            first = seen_rows[visible]
            if "_systems" in row:
                more = [x for x in row["_systems"] if x not in first.get("_systems", [])]
                first["_systems"] = [*first.get("_systems", []), *more]
            continue
        seen_rows[visible] = row
        tables[idx]["rows"].append(row)
    for t in tables.values():  # leere optionale Spalten (NAT, VIP, Load Balancer …) aus
        t["rows"] = appdoc.by_l3out(t["rows"])  # unspezifische nach L3Out (ADR 0034)
        t["columns"] = appdoc.visible_columns(t["rows"])
        t["labels"] = appdoc.column_labels(t["columns"])  # Anzeige (0.87.0, ADR 0028)
    return hits, [tables[c.index] for c in catalog.ordered()]


def _app_findings(
    session: Session,
    tenant_id: int,
    systems: list[dict],
    epgs: list[dict],
    rule_ids: set[int],
    rule_epgs: dict[int, set[str]] | None = None,
    restricted: bool = False,
    app_id: int | None = None,
) -> list[dict]:
    """Offene (bzw. bestaetigte) Befunde der Applikation: zu ihren Regeln, Servern (Objekt im
    IPAM bzw. auf der Firewall), VMs, Portgruppen und den Subnetzen ihrer EPGs - je Befund die
    EPGs (`Tenant/AP/EPG`), zu denen er gehoert (Filter nach Tenant, AP, EPG; 0.38.0).
    `restricted` (Ausschnitt eines Kunden, 0.58.0): Saetze ohne Angaben zu fremden Regeln."""
    wanted: dict[tuple[SubjectKind, str], set[str]] = defaultdict(set)
    for x in systems:
        mine = set(x["epgs"])
        for name in x["servers"]:
            wanted[(SubjectKind.NETWORK_OBJECT, name)] |= mine
            wanted[(SubjectKind.DEVICE_OBJECT, name)] |= mine
        if x["kind"] == "vm":
            wanted[(SubjectKind.VM, x["name"])] |= mine
            for pg in x["portgroups"]:
                wanted[(SubjectKind.PORTGROUP, pg.split(" (VLAN")[0])] |= mine
    for e in epgs:
        for ip in e["subnets"]:
            try:
                wanted[(SubjectKind.SUBNET, str(ipaddress.ip_interface(ip).network))].add(
                    e["label"]
                )
            except ValueError:
                continue
    out = []
    for f in session.scalars(
        select(Finding)
        .where(Finding.tenant_id == tenant_id, Finding.status.in_(_OPEN))
        .order_by(Finding.type, Finding.subject_id)
    ):
        detail = f.detail if isinstance(f.detail, dict) else {}
        if (f.type in (FindingType.APP_CONNECTION_MISMATCH, FindingType.APP_CONTRACT_NARROWER,
                       FindingType.APP_RULE_UNUSED)
                and detail.get("application_id") != app_id):  # fmt: skip
            continue  # Befund einer anderen Applikation (0.59.0, 0.78.2, 0.107.0)
        rule_id = detail.get("rule_id") if f.subject_kind == SubjectKind.RULE else None
        if (f.subject_kind, f.subject_id) not in wanted and rule_id not in rule_ids:
            continue
        scope = set(wanted.get((f.subject_kind, f.subject_id), set()))
        if rule_id is not None:
            scope |= (rule_epgs or {}).get(rule_id, set())
        if restricted:
            detail = {k: v for k, v in detail.items()
                      if k not in customer_scope.FINDING_DETAIL_HIDDEN}  # fmt: skip
        message, hint = finding_text.explain(
            f.type.value, f.subject_id, detail if restricted else f.detail
        )
        out.append(
            {
                "id": f.id,
                "type": f.type.value,
                "severity": f.severity.value,
                "status": f.status.value,
                "subject_kind": f.subject_kind.value,
                "subject_id": f.subject_id,
                "message": message,
                "hint": hint or None,
                "rule_id": rule_id,
                "epgs": sorted(scope),
            }
        )
    return out


def _table_meta(c, catalog) -> dict:
    columns = appdoc.table_columns()
    return {
        "index": c.index,
        "title": catalog.title(c.index),
        "enforcement": c.enforcement,
        "columns": columns,
        "labels": appdoc.column_labels(columns),
    }


# -- Abhaengigkeiten zwischen Applikationen (0.60.0, ADR 0012) ------------------------------


@dataclass
class Footprint:
    """Was eine Applikation im Netz ausmacht: ihre EPGs, die IPs ihrer Systeme (VMs, Server)
    und die Subnetze ihrer EPGs."""

    epgs: set[int] = field(default_factory=set)
    ips: set[str] = field(default_factory=set)
    nets: list = field(default_factory=list)

    def matches(self, epg_ids: set[int], ranges: list[tuple]) -> str | None:
        """Gegenseite einer Regel spezifisch bei dieser Applikation? EPG, Host mit einer IP
        ihrer Systeme bzw. Netz bzw. Bereich innerhalb eines Subnetzes ihrer EPGs; any und
        groessere Netze zaehlen nicht. -> Bezug (Objekt) bzw. None."""
        if epg_ids & self.epgs:
            return "EPG"
        for fam, lo, hi, via in ranges:
            if lo == hi and str(ipaddress.ip_address(lo) if fam == 4 else
                                ipaddress.IPv6Address(lo)) in self.ips:  # fmt: skip
                return via or "Host"
            for net in self.nets:
                if net.version == fam and int(net.network_address) <= lo \
                        and hi <= int(net.broadcast_address):  # fmt: skip
                    return via or str(net)
        return None


def footprint(session: Session, app: Application, res: Resolver,
              groups: virtual.PortgroupIndex) -> Footprint:  # fmt: skip
    hosts, epgs = _origins(session, app, res)
    out = Footprint(epgs=set(epgs))
    out.ips |= {ip for h, _w in hosts.values() if (ip := _ip(h.value))}
    for e in _app_vms(session, app.tenant_id, epgs, hosts, groups).values():
        out.ips |= {ip for n in e["vm"].nics for ip in n.ips or []}
    if epgs:
        stmt = select(EpgSubnet.ip).where(
            EpgSubnet.epg_id.in_(list(epgs)), EpgSubnet.origin.in_(("epg", "bd"))
        )
        for ip in session.scalars(stmt):
            try:
                out.nets.append(ipaddress.ip_interface(ip).network)
            except ValueError:
                continue
    return out


def rule_objects(rules: list) -> list:
    """Regeln der Doku als `RuleOut` - auch aus dem gespeicherten Stand (JSON, 0.108.0)."""
    from app.schemas.rules import RuleOut  # spaet: Schemas nutzen den Store nicht

    return [r if isinstance(r, RuleOut) else RuleOut.model_validate(r) for r in rules]


def dependencies(session: Session, app: Application, doc: dict,
                 visible: set[int] | None = None) -> dict:  # fmt: skip
    """Abhaengigkeiten der Applikation (0.60.0): Regeln, die ein System von ihr spezifisch mit
    einem System einer anderen Applikation verbinden (EPG, Host bzw. Netz in deren EPG-Subnetz -
    nicht any, nicht groessere Netze). Eigene Seite Quelle bzw. Consumer -> sie **nutzt** die
    andere; Ziel bzw. Provider -> sie **wird genutzt**. `visible` = sichtbare Applikationen
    (Kunden-Benutzer). -> {uses, used_by} je Applikation mit den Regeln; seit 0.88.0 je
    Eintrag `infrastructure` (Monitoring, Backup, DNS u. a. - in der Doku getrennt von den
    fachlichen)."""
    from app.models import RuleEndpoint, RuleIpRange

    tid = app.tenant_id
    res = Resolver(session, tid)
    groups = virtual.PortgroupIndex.load(session, tid)
    others = [a for a in session.scalars(
        select(Application).where(Application.tenant_id == tid, Application.id != app.id)
        .order_by(Application.name)) if visible is None or a.id in visible]  # fmt: skip
    empty: dict = {"uses": [], "used_by": []}
    sides: dict[int, set[str]] = defaultdict(set)
    both: set[int] = set()  # ACI zustandslos (0.69.1): Contract auch in der Rueckrichtung
    for h in doc["hits"]:
        if h["scope"] == "specific" and h["side"] in ("src", "dst"):
            sides[h["rule_id"]].add(h["side"])
            if h.get("reverse"):
                both.add(h["rule_id"])
    if not others or not sides:
        return empty
    own = footprint(session, app, res, groups)
    prints = {a.id: footprint(session, a, res, groups) for a in others}
    epgs_of: dict[tuple[int, str], set[int]] = defaultdict(set)
    for rid, role, eid in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role, RuleEndpoint.epg_id).where(
            RuleEndpoint.rule_id.in_(list(sides)), RuleEndpoint.epg_id.is_not(None))
    ):  # fmt: skip
        epgs_of[(rid, getattr(role, "value", role))].add(eid)
    ranges_of: dict[tuple[int, str], list[tuple]] = defaultdict(list)
    for rid, role, fam, lo, hi, is_any, via in session.execute(
        select(RuleIpRange.rule_id, RuleIpRange.role, RuleIpRange.family, RuleIpRange.lo,
               RuleIpRange.hi, RuleIpRange.is_any, RuleIpRange.via)
        .where(RuleIpRange.rule_id.in_(list(sides)))
    ):  # fmt: skip
        if not is_any:
            ranges_of[(rid, role)].append((fam, int(lo, 16), int(hi, 16), via))
    rules = {r.id: r for r in rule_objects(doc["rules"])}
    found: dict[str, dict[int, dict]] = {"uses": {}, "used_by": {}}
    names = {a.id: a.name for a in others}
    infra = {a.id for a in others if a.infrastructure}
    for rid, mine in sides.items():
        r = rules.get(rid)
        if r is None or getattr(r, "action", "allow") != "allow":
            continue
        for side in mine:
            other = "dst" if side == "src" else "src"
            epg_ids, ranges = epgs_of.get((rid, other), set()), ranges_of.get((rid, other), [])
            if own.matches(epg_ids, ranges):
                continue  # Gegenseite gehoert auch zur Applikation selbst (intern)
            for aid, fp in prints.items():
                via = fp.matches(epg_ids, ranges)
                if via is None:
                    continue
                kinds = ["uses", "used_by"] if side == "src" else ["used_by", "uses"]
                for kind in kinds[: 2 if rid in both else 1]:
                    entry = found[kind].setdefault(
                        aid, {"application_id": aid, "name": names[aid],
                              "infrastructure": aid in infra, "rules": {}}
                    )  # fmt: skip
                    entry["rules"].setdefault(rid, {
                        "rule_id": rid, "name": r.aci.contract if r.aci else r.name,
                        "domain": r.domain, "via": via, "services": list(r.service_names),
                        "reverse": kind != kinds[0]})  # fmt: skip
    return {
        kind: [{**e, "rules": list(e["rules"].values())}
               for e in sorted(items.values(), key=lambda x: x["name"].casefold())]
        for kind, items in found.items()
    }  # fmt: skip
