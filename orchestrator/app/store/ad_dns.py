"""DNS aus dem AD (1.19.0, ADR 0045 Nachtrag): gelesener Stand der AD-DNS-Quellen eines Mandanten
je IP - A/AAAA-Records, PTR, gelesene Zonen und was sich nicht pruefen laesst.

Gemeinsame Grundlage fuer den Abschnitt „DNS-Namen aus dem AD“ des NetBox-Syncs
(`store/netbox_sync_dns.py`) und die Pruefungen `app/checks/analyse/dns_*.py`
(`store/dns_checks.py`).

- **A-Records** (`a`): Name je IP aus den Forward-Zonen; Aliase per CNAME (`attrs.cname`, ADR 0045
  Nr. 2) stehen getrennt (`alias`) - ein Alias ist nie der Name der IP.
- **PTR** (`ptr`): Ziel je IP aus den Reverse-Zonen - nur zur Kontrolle bzw. zum Entscheiden,
  wenn eine IP mehrere A-Records hat (`decide`).
- **Gelesen** ist eine Zone, wenn der Lauf ihrer Quelle sie uebertragen hat (seit 1.23.0
  `collector_run.zones`, auch ohne Eintraege - eine Reverse-Zone nur mit SOA/NS ist gelesen, ihre
  Adressen haben keinen PTR; Anwender 2026-11-20). Aeltere Laeufe ohne diese Angabe: wie bisher,
  wenn der Stand Eintraege aus ihr hat. Ein Zonentransfer, der scheitert, bricht den ganzen Lauf
  ab (kein halber Stand); eingetragene, aber noch nicht gelesene Zonen, Quellen ohne Stand bzw.
  mit fehlgeschlagenem letztem Lauf stehen in `sources` (keine stillen Grenzen).

Nur lesend: liest den gespeicherten Stand der Referenzquellen, nie den DNS-Server.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CollectorRun, Platform, ReferenceRecordRow, RunStatus, SourceSystem

A_RECORD, PTR_RECORD = "a_record", "ptr_record"
REVERSE = (".in-addr.arpa", ".ip6.arpa")


def norm_ip(value: str | None) -> str | None:
    try:
        return ipaddress.ip_interface(str(value or "").strip()).ip.compressed
    except ValueError:
        return None


def norm_name(value: str | None) -> str:
    return (value or "").strip().lower().rstrip(".")


def is_reverse(zone: str) -> bool:
    return f".{zone}".endswith(REVERSE) or zone in ("in-addr.arpa", "ip6.arpa")


def _within(name: str, zone: str) -> bool:
    return name == zone or name.endswith(f".{zone}")


@dataclass
class Source:
    """Eine AD-DNS-Quelle mit ihrem Lesestand."""

    id: int
    name: str
    zones: list[str]  # konfiguriert (Lesebereich)
    read: bool = False  # Stand vorhanden
    read_at: datetime | None = None
    failed: str | None = None  # letzter Lauf fehlgeschlagen (Meldung des Laufs)
    failed_at: datetime | None = None
    empty: list[str] = field(default_factory=list)  # eingetragen, (noch) nicht gelesen
    # gelesen ohne Eintraege (1.23.0, nur Laeufe mit `zones`) - kein Mangel, nur zur Erklaerung
    blank: list[str] = field(default_factory=list)
    moved: bool = False  # der Lauf nennt die uebertragenen Zonen (1.23.0)


@dataclass(frozen=True)
class Decision:
    """Name einer IP: `name` (oder None), `names` = alle A-Records, `by` = a | ptr, `why`
    (ohne Namen) = no_ptr | ptr_other | ptr_several | no_reverse."""

    name: str | None
    names: tuple[str, ...]
    ptr: tuple[str, ...]
    by: str = ""
    why: str = ""


class Model:
    """Stand aller eingeschalteten AD-DNS-Quellen eines Mandanten (zusammengefuehrt)."""

    def __init__(self, session: Session, tenant_id: int) -> None:
        self.tenant_id = tenant_id
        self.sources: list[Source] = []
        self.a: dict[str, set[str]] = defaultdict(set)  # IP -> Namen (ohne Aliase)
        self.alias: dict[str, set[str]] = defaultdict(set)  # IP -> Aliase (CNAME)
        self.ptr: dict[str, set[str]] = defaultdict(set)  # IP -> PTR-Ziele
        self.hosts: dict[str, set[str]] = defaultdict(set)  # Name -> IPs (A, ohne Aliase)
        self.zone_of: dict[str, str] = {}  # IP -> Zone des (ersten) A-Records
        self.a_sources: dict[str, set[int]] = defaultdict(set)  # IP -> Quellen mit A-Record
        self.ptr_sources: dict[str, set[int]] = defaultdict(set)  # IP -> Quellen mit PTR
        self.forward: set[str] = set()  # gelesene Forward-Zonen
        self.reverse: set[str] = set()  # gelesene Reverse-Zonen
        self.configured: set[str] = set()  # eingetragene Reverse-Zonen (gelesen oder nicht)
        rows = session.scalars(
            select(SourceSystem)
            .where(
                SourceSystem.tenant_id == tenant_id,
                SourceSystem.platform == Platform.AD_DNS,
                SourceSystem.enabled.is_(True),
            )
            .order_by(SourceSystem.id)
        ).all()
        for s in rows:
            self._load(session, s)

    def _load(self, session: Session, s: SourceSystem) -> None:
        scope = (s.read_scope or "").replace(",", " ").split()
        src = Source(s.id, s.name, sorted({norm_name(z) for z in scope}))
        self.sources.append(src)
        self.configured |= {z for z in src.zones if is_reverse(z)}
        last = session.scalars(
            select(CollectorRun)
            .where(CollectorRun.source_system_id == s.id)
            .order_by(CollectorRun.queued_at.desc())
            .limit(1)
        ).first()
        if last is not None and last.status == RunStatus.FAILED and last.id != s.reference_run_id:
            src.failed, src.failed_at = (last.message or "")[:300], last.finished_at
        if not s.reference_run_id:
            return
        current = session.get(CollectorRun, s.reference_run_id)
        src.read, src.read_at = True, current.finished_at if current else None
        seen: set[str] = set()
        for kind, value, name, attrs, tags in session.execute(
            select(
                ReferenceRecordRow.kind,
                ReferenceRecordRow.value,
                ReferenceRecordRow.dns_name,
                ReferenceRecordRow.attrs,
                ReferenceRecordRow.tags,
            ).where(
                ReferenceRecordRow.collector_run_id == s.reference_run_id,
                ReferenceRecordRow.kind.in_((A_RECORD, PTR_RECORD)),
            )
        ):
            ip, host = norm_ip(value), norm_name(name)
            if ip is None or not host:
                continue
            attrs = attrs or {}
            zone = norm_name(attrs.get("zone")) or next(
                (norm_name(t[5:]) for t in tags or [] if str(t).startswith("zone=")), ""
            )
            if zone:
                seen.add(zone)
            if kind == PTR_RECORD:
                self.ptr[ip].add(host)
                self.ptr_sources[ip].add(s.id)
            elif attrs.get("cname"):
                self.alias[ip].add(host)
            else:
                self.a[ip].add(host)
                self.hosts[host].add(ip)
                self.a_sources[ip].add(s.id)
                if zone:
                    self.zone_of.setdefault(ip, zone)
        # gelesen = uebertragen (1.23.0, auch ohne Eintraege); aeltere Laeufe ohne Angabe: nur
        # Zonen mit Eintraegen (leer bzw. erst nach dem Lauf eingetragen: `empty`)
        moved = current.zones if current is not None and isinstance(current.zones, dict) else None
        done = seen | {norm_name(z) for z in moved or {}}
        src.moved = moved is not None
        src.empty = [z for z in src.zones if z not in done]
        src.blank = [z for z in src.zones if moved is not None and z in done and z not in seen]
        for z in src.zones:
            if z in done:
                (self.reverse if is_reverse(z) else self.forward).add(z)

    # -- Abfragen ---------------------------------------------------------------------------

    @property
    def read(self) -> bool:
        """Mindestens eine Quelle mit Stand."""
        return any(s.read for s in self.sources)

    def reverse_read(self, ip: str) -> bool:
        """Liegt die IP in einer gelesenen Reverse-Zone (ist ihr PTR pruefbar)?"""
        try:
            pointer = ipaddress.ip_address(ip).reverse_pointer.lower()
        except ValueError:
            return False
        return any(_within(pointer, z) for z in self.reverse)

    def reverse_configured(self, ip: str) -> bool:
        """Liegt die IP in einer eingetragenen Reverse-Zone (gelesen oder nicht, 1.23.0)?"""
        try:
            pointer = ipaddress.ip_address(ip).reverse_pointer.lower()
        except ValueError:
            return False
        return any(_within(pointer, z) for z in self.configured)

    def reverse_zone(self, ip: str) -> str | None:
        """Gelesene Reverse-Zone der IP (die genaueste)."""
        try:
            pointer = ipaddress.ip_address(ip).reverse_pointer.lower()
        except ValueError:
            return None
        hits = [z for z in self.reverse if _within(pointer, z)]
        return max(hits, key=len) if hits else None

    def forward_read(self, name: str) -> bool:
        """Liegt der Name in einer gelesenen Forward-Zone (ist sein A-Record pruefbar)?"""
        return any(_within(name, z) for z in self.forward)

    def decide(self, ip: str) -> Decision:
        """Name der IP: ein A-Record -> sein Name; mehrere -> der PTR entscheidet, wenn er genau
        einen davon nennt; sonst kein Name (mehrdeutig, mit Grund)."""
        names = tuple(sorted(self.a.get(ip, ())))
        ptr = tuple(sorted(self.ptr.get(ip, ())))
        if not names:
            return Decision(None, names, ptr)
        if len(names) == 1:
            return Decision(names[0], names, ptr, by="a")
        hits = [n for n in names if n in ptr]
        if len(hits) == 1:
            return Decision(hits[0], names, ptr, by="ptr")
        if hits:
            why = "ptr_several"
        elif ptr:
            why = "ptr_other"
        elif not self.reverse_read(ip):
            why = "no_reverse"
        else:
            why = "no_ptr"
        return Decision(None, names, ptr, why=why)
