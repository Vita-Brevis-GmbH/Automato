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

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationMember,
    Epg,
    NetworkObject,
    ObjectClass,
    Site,
    Tenant,
)
from app.store import appdoc
from app.store import naming_profiles as profile_store
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


def build_doc(session: Session, app: Application) -> dict:
    """Applikations-Doku: die Tabellen aller Systeme der Applikation (Server -> Firewall-
    Tabellen, EPGs -> ACI-Tabellen), gleiche Zeilen einmal."""
    hosts, epgs = _systems(session, app)
    data = appdoc.TenantData(session, app.tenant_id)
    docs = [appdoc.host_doc(session, h, data) for h in sorted(hosts, key=lambda h: h.name)]
    docs += [
        {**appdoc.epg_doc(session, e, data), "system": inventory_label(e), "kind": "epg"}
        for e in sorted(epgs, key=inventory_label)
    ]
    tables = {t[0]: {**_table_meta(t), "rows": []} for t in appdoc.TABLES}
    seen: set[tuple] = set()
    for d in docs:
        for t in d["tables"]:
            for row in t["rows"]:
                key = (t["index"], tuple(sorted(row.items())))
                if key not in seen:
                    seen.add(key)
                    tables[t["index"]]["rows"].append(row)
    tenant = session.get(Tenant, app.tenant_id)
    return {
        "tenant_code": tenant.code if tenant else None,
        "systems": [
            {
                "name": d["system"],
                "kind": d.get("kind", "host"),
                "ip": d["system_ip"],
                "site": d["site"],
            }
            for d in docs
        ],
        "tables": [tables[i] for i, _, _ in appdoc.TABLES],
    }


def _table_meta(t: tuple[int, str, str]) -> dict:
    index, title, enforcement = t
    return {
        "index": index,
        "title": title,
        "enforcement": enforcement,
        "columns": appdoc.TABLE_COLUMNS[index],
    }
