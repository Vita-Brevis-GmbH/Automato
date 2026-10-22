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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import naming
from app.models import (
    Application,
    ApplicationMember,
    DeviceObject,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    Finding,
    FindingStatus,
    NetworkObject,
    ObjectCategory,
    ObjectClass,
    Portgroup,
    Rule,
    RuleDomain,
    Site,
    SubjectKind,
    Tenant,
    Vm,
    VmIp,
    VmNic,
)
from app.store import appdoc, finding_text, parallel, references, rule_index, sites
from app.store import naming_profiles as profile_store
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
        return Resolved(label=ref, exists=False)


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


def suggestions(session: Session, tenant_id: int) -> list[dict]:
    """Nicht zugeordnete APs und Server, gruppiert nach Projektnummer."""
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates(session, tenant_id):
        if c.kind in ("ap", "host") and c.project and not c.applications:
            groups[c.project].append(c)
    if not groups:
        return []
    by_code = {
        a.code: a
        for a in session.scalars(
            select(Application).where(Application.tenant_id == tenant_id).order_by(Application.id)
        )
        if a.code
    }
    names = profile_store.entries_for_tenant(session, tenant_id, PROJECT_DICTIONARY)
    out = []
    for nr in sorted(groups):
        app = by_code.get(nr)
        out.append(
            {
                "code": nr,
                "name": app.name if app else (names.get(nr) or f"Projekt {nr}"),
                "application_id": app.id if app else None,
                "members": groups[nr],
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
    bestehenden ergaenzen. Der Vorschlag wird serverseitig neu berechnet. None = keiner."""
    sug = next((s for s in suggestions(session, tenant_id) if s["code"] == code), None)
    if sug is None:
        return None
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


def _systems(session: Session, app: Application, resolver: Resolver | None = None):
    res = resolver or Resolver(session, app.tenant_id)
    hosts: dict[int, NetworkObject] = {}
    epgs: dict[int, Epg] = {}
    for m in app.members:
        r = res.resolve(m.kind, m.ref)
        hosts.update((h.id, h) for h in r.hosts)
        epgs.update((e.id, e) for e in r.epgs)
    return list(hosts.values()), list(epgs.values())


KIND_LABEL = {"ap": "AP", "epg": "EPG", "host": "Server"}
# Reihenfolge der Bezugsarten: eine Regel zaehlt mit ihrem engsten Bezug
_SCOPE_RANK = {"specific": 0, "subnet": 1, "vzany": 2, "pref_group": 3, "any": 4}
_OPEN = (FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED)


def _origins(session: Session, app: Application, res: Resolver):
    """Server und EPGs der Applikation mit dem Mitglied, ueber das sie dazugehoeren."""
    hosts: dict[int, tuple[NetworkObject, str]] = {}
    epgs: dict[int, tuple[Epg, str]] = {}
    for m in app.members:
        r = res.resolve(m.kind, m.ref)
        why = f"{KIND_LABEL.get(m.kind, m.kind)} {r.label}"
        for h in r.hosts:
            hosts.setdefault(h.id, (h, why))
        for e in r.epgs:
            epgs.setdefault(e.id, (e, why))
    return hosts, epgs


def _ip(value: str | None) -> str | None:
    return value.split("/")[0] if value else None


def _app_vms(session: Session, tenant_id: int, epgs: dict, hosts: dict) -> dict[int, dict]:
    """VMs der Applikation: an den Portgruppen ihrer EPGs und hinter ihren Servern (gleiche IP),
    je VM mit den Gruenden (Portgruppe -> EPG bzw. IP = Server)."""
    out: dict[int, dict] = {}

    def entry(vm: Vm) -> dict:
        return out.setdefault(vm.id, {"vm": vm, "epg_ids": set(), "hosts": {}, "why": []})

    if epgs:
        rows = session.execute(
            select(Portgroup, Vm)
            .join(Vm, Vm.source_system_id == Portgroup.source_system_id)
            .join(VmNic, (VmNic.vm_id == Vm.id) & (VmNic.portgroup == Portgroup.name))
            .where(Portgroup.epg_id.in_(list(epgs)), Vm.template.is_(False))
        )
        for pg, vm in rows:
            e = entry(vm)
            e["epg_ids"].add(pg.epg_id)
            label = inventory_label(epgs[pg.epg_id][0])
            e["why"].append(f"Portgruppe {pg.name} → EPG {label} ({epgs[pg.epg_id][1]})")
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


def build_doc(session: Session, app: Application) -> dict:
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
    - `tables`: die 10 Kategorien fuer Export und Druck (gleiche Zeilen einmal).
    """
    tid = app.tenant_id
    res = Resolver(session, tid)
    hosts, epgs = _origins(session, app, res)
    data = appdoc.TenantData(session, tid)
    ctx = references.context(session, tid)
    site_index = sites.SiteIndex(session, tid)
    vms = _app_vms(session, tid, epgs, hosts)
    portgroups = {
        (pg.source_system_id, pg.name): pg
        for pg in session.scalars(select(Portgroup).where(Portgroup.tenant_id == tid))
    }

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
        nets = []
        for n in vm.nics:
            pg = portgroups.get((vm.source_system_id, n.portgroup)) if n.portgroup else None
            if pg is not None and pg.epg_id:
                nic_epgs.add(pg.epg_id)
            if n.portgroup:
                nets.append(f"{n.portgroup}" + (f" (VLAN {n.vlan})" if n.vlan is not None else ""))
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
            session, data, vm.name, ips, list(servers.values()), nic_epgs
        ):
            rows.append((key, idx, row))
    for hid, (h, why) in sorted(hosts.items(), key=lambda kv: kv[1][0].name):
        if hid in covered_hosts:
            continue  # steht als VM da
        key = f"host:{hid}"
        doc = appdoc.host_doc(session, h, data, full=True)
        ip = _ip(h.value)
        systems.append(
            {
                "key": key,
                "kind": "host",
                "name": h.name,
                "ips": [ip] if ip else [],
                "dns": dns([ip] if ip else []),
                "servers": [h.name],
                "epgs": sorted(inventory_label(x) for x in appdoc.host_epgs(session, data, h)),
                "portgroups": [],
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

    # parallele Firewalls (0.18.0): gleiche Policies nur einmal - die der fuehrenden Firewall
    twins = parallel.twin_ids(session, {row.get(appdoc.RULE) for _, _, row in rows})
    rows = [r for r in rows if r[2].get(appdoc.RULE) not in twins]
    hits, tables = _hits_and_tables(rows)
    rule_ids = {h["rule_id"] for h in hits}
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
    # Kennzahlen nur aus Treffern, die das Routing zulaesst (bzw. unbekannt); Regeln, die nur
    # routingtechnisch unmoegliche Treffer haben, zaehlen separat (0.24.0)
    possible = [h for h in hits if h["relevance"] != "impossible"]
    impossible = {h["rule_id"] for h in hits} - {h["rule_id"] for h in possible}
    best: dict[int, str] = {}
    for h in possible:
        cur = best.get(h["rule_id"])
        if cur is None or _SCOPE_RANK.get(h["scope"], 9) < _SCOPE_RANK.get(cur, 9):
            best[h["rule_id"]] = h["scope"]
    for item in systems + epg_out:
        mine = {h["rule_id"] for h in possible if h["system"] == item["key"]}
        spec = {
            h["rule_id"]
            for h in possible
            if h["system"] == item["key"] and h["scope"] == "specific"
        }
        item["rules"] = {"specific": len(spec), "unspecific": len(mine - spec)}
    by_scope = {k: sum(1 for v in best.values() if v == k) for k in appdoc.UNSPECIFIC}
    findings = _app_findings(session, tid, systems, epg_out, set(ordered))
    tenant = session.get(Tenant, tid)
    return {
        "tenant_code": tenant.code if tenant else None,
        "summary": {
            "vms": sum(1 for x in systems if x["kind"] == "vm"),
            "servers": sum(1 for x in systems if x["kind"] == "host"),
            "epgs": len(epg_out),
            "rules": len(ordered) - len(impossible),
            "firewall": sum(
                1 for r in rules if r.domain == RuleDomain.FIREWALL.value and r.id not in impossible
            ),
            "aci": sum(1 for r in rules if r.domain == RuleDomain.ACI_CONTRACT.value),
            "impossible": len(impossible),
            "specific": sum(1 for v in best.values() if v == "specific"),
            "unspecific": sum(by_scope.values()),
            "by_scope": by_scope,
            "findings": len(findings),
        },
        "systems": systems,
        "epgs": epg_out,
        "hits": hits,
        "rules": rules,
        "findings": findings,
        "tables": tables,
    }


def _object_label(row: dict) -> str:
    obj, value = row.get(appdoc.OBJECT) or "", row.get(appdoc.OBJECT_VALUE) or ""
    if not obj:
        return value
    return f"{obj} ({value})" if value and value != obj else obj


def _hits_and_tables(rows: list[tuple[str, int, dict]]) -> tuple[list[dict], list[dict]]:
    """Treffer je (Regel, System, Seite) und die 10 Kategorien-Tabellen.

    In den Tabellen steht eine Regel je Seite und Objekt der Regel nur einmal: erfasst ein Netz
    bzw. any mehrere Systeme, nennt der Server Name das Objekt wie in der Regel und die Systeme
    darin (`N_srv16 (10.20.0.0/16): web01, db01`) statt einer Zeile je System (0.14.1)."""
    tables = {t[0]: {**_table_meta(t), "rows": []} for t in appdoc.TABLES}
    groups: dict[tuple, dict] = {}
    seen_hits: set[tuple] = set()
    hits: list[dict] = []
    for key, idx, row in rows:
        rid = row.get(appdoc.RULE)
        gkey = (idx, rid, row.get(appdoc.SIDE), row.get(appdoc.OBJECT))
        if rid is None:
            gkey = (idx, None, len(groups))
        group = groups.setdefault(gkey, {"row": row, "systems": []})
        name = row.get("Server Name")
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
                "counterpart": row.get("Target Systems") or row.get("Source") or "",
                "services": row.get("TCP/UDP") or "",
                # Routing (0.24.0): possible | impossible | unknown, mit Begruendung
                "relevance": row.get(appdoc.REL) or "unknown",
                "relevance_text": row.get(appdoc.REL_TEXT) or "",
            }
        )
    seen_rows: set[tuple] = set()
    for (idx, *_), group in groups.items():
        row = dict(group["row"])
        if row.get(appdoc.REL) == "impossible":
            continue  # Export/Druck: nur, was das Routing zulaesst
        systems = group["systems"]
        if len(systems) > 1 or row.get(appdoc.SCOPE) not in (None, "specific"):
            label = _object_label(row)
            inside = [x for x in systems if x not in label]
            row["Server Name"] = f"{label}: {', '.join(inside)}" if inside else label
        visible = (idx, tuple(row.get(c) for c in appdoc.TABLE_COLUMNS[idx]))
        if visible not in seen_rows:
            seen_rows.add(visible)
            tables[idx]["rows"].append(row)
    return hits, [tables[i] for i, _, _ in appdoc.TABLES]


def _app_findings(
    session: Session, tenant_id: int, systems: list[dict], epgs: list[dict], rule_ids: set[int]
) -> list[dict]:
    """Offene (bzw. bestaetigte) Befunde der Applikation: zu ihren Regeln, Servern (Objekt im
    IPAM bzw. auf der Firewall), VMs, Portgruppen und den Subnetzen ihrer EPGs."""
    wanted: set[tuple[SubjectKind, str]] = set()
    for x in systems:
        for name in x["servers"]:
            wanted |= {(SubjectKind.NETWORK_OBJECT, name), (SubjectKind.DEVICE_OBJECT, name)}
        if x["kind"] == "vm":
            wanted.add((SubjectKind.VM, x["name"]))
            wanted |= {(SubjectKind.PORTGROUP, pg.split(" (VLAN")[0]) for pg in x["portgroups"]}
    for e in epgs:
        for ip in e["subnets"]:
            try:
                wanted.add((SubjectKind.SUBNET, str(ipaddress.ip_interface(ip).network)))
            except ValueError:
                continue
    out = []
    for f in session.scalars(
        select(Finding)
        .where(Finding.tenant_id == tenant_id, Finding.status.in_(_OPEN))
        .order_by(Finding.type, Finding.subject_id)
    ):
        detail = f.detail if isinstance(f.detail, dict) else {}
        rule_id = detail.get("rule_id") if f.subject_kind == SubjectKind.RULE else None
        if (f.subject_kind, f.subject_id) not in wanted and rule_id not in rule_ids:
            continue
        message, hint = finding_text.explain(f.type.value, f.subject_id, f.detail)
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
            }
        )
    return out


def _table_meta(t: tuple[int, str, str]) -> dict:
    index, title, enforcement = t
    return {
        "index": index,
        "title": title,
        "enforcement": enforcement,
        "columns": appdoc.TABLE_COLUMNS[index],
    }
