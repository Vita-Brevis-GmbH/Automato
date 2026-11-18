"""Reservierungen von VLAN- und AS-Nummern (1.19.0, ADR 0066).

Anwender 2026-10-30 (Roadmap Version 2 Nr. 10): „Elemente festlegen und reservieren - erst VLANs
und BGP-AS-Nummern -, Befund bei Überschneidung (reserviert gegen gelesen bzw. doppelt)“;
2026-11-17 (Prioritaet 6): „Reservationen auch ins Netbox“ (NetBox-Sync, Abschnitt
„Reservierungen“, `store/netbox_sync_reserv`).

- **Laufzeitdaten je Mandant** (`Reservation`): Nummer bzw. Bereich, bei VLANs der Standort wie
  die VLAN-Ansicht je Standort (1.9.0; ohne = alle Standorte) und wahlweise die VLAN-Gruppe in
  NetBox, Zweck, reserviert von/am. Anlegen, Aendern und Freigeben (= Loeschen) in der App, mit
  Audit; kein CSV-Import.
- **In Gebrauch** (gelesen): VLANs aus der VLAN-Tabelle der gelesenen Geraete (`inv_vlan`: Switches,
  ACI, FortiGate) am Standort ihres Quellsystems (`sites.source_sites`); AS-Nummern aus den
  BGP-Daten (`store/as_numbers`: lokal bzw. als Nachbar).
- **Befunde** (`app/checks/analyse/reservierung_*.py`): eine reservierte Nummer ist gelesen in
  Gebrauch - war sie beim Reservieren frei (`used_before`), ist das ein Hinweis „jetzt belegt“
  (die Reservierung ist umgesetzt bzw. vergeben - freigeben), sonst ein Konflikt; zwei
  Reservierungen derselben Art ueberschneiden sich (VLANs am selben bzw. an allen Standorten).
- Reservierungen aendern weder Doku noch Wege: ihr Audit (`reservation.`) zaehlt nicht fuer den
  Stand der Doku und den Wege-Speicher (`doc_snapshots._QUIET`).

Nur Daten der Plattform; gelesen wird nichts Neues.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L, german_only
from app.models import (
    FindingType,
    InvVlan,
    Platform,
    Reservation,
    Severity,
    Site,
    SourceSystem,
    SubjectKind,
    Tenant,
)
from app.store import sites as site_store

KINDS = ("vlan", "asn")
LIMITS = {"vlan": (1, 4094), "asn": (1, 4294967295)}
STEP = "reservierungen"  # eigener Teil-Durchgang der Pruefungen (nach dem Speichern)
TYPES = frozenset({FindingType.RESERVATION_IN_USE, FindingType.RESERVATION_CONFLICT,
                   FindingType.RESERVATION_OVERLAP})  # fmt: skip
SHOW = 20  # Stellen in Gebrauch je Reservierung im Detail (gezaehlt werden alle)
# Plattformen mit VLAN-Tabelle (`inv_vlan`) - VLANs in Gebrauch
VLAN_PLATFORMS = (Platform.CISCO_SWITCH, Platform.CISCO_ACI, Platform.FORTIGATE)
_SLUG = re.compile(r"[^a-z0-9_-]+")


def numbers(low: int, high: int) -> str:
    return str(low) if low == high else f"{low}–{high}"


def label(kind: str, low: int, high: int) -> str:
    """„VLAN 120“, „VLAN 120–129“, „AS 65010“ - gleich in beiden Sprachen."""
    return f"{'VLAN' if kind == 'vlan' else 'AS'} {numbers(low, high)}"


def compact(values) -> str:
    """Nummern als Bereiche: „120–122, 125“ (hoechstens 12 Teile)."""
    ordered, parts, i = sorted(set(values)), [], 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(numbers(ordered[i], ordered[j]))
        i = j + 1
    return ", ".join(parts[:12]) + (" …" if len(parts) > 12 else "")


def group_slug(tenant_name: str, source_name: str) -> str:
    """VLAN-Gruppe eines Quellsystems in NetBox wie der Sync (`netbox_sync.group_slug`):
    `<Mandant>-<Quellsystem>` als Slug. Hier nachgebildet - die Analyse nutzt den Schreibpfad
    nicht (ADR 0036); `tests/test_reservations.py` prueft die Gleichheit."""

    def slug(value: str) -> str:
        return _SLUG.sub("-", value.lower()).strip("-")[:100] or "x"

    return slug(f"{slug(tenant_name)}-{source_name}")


# -- In Gebrauch (gelesen) -------------------------------------------------------------------


@dataclass(frozen=True)
class Use:
    number: int
    where: str  # Quellsystem bzw. Geraet (deutsch)
    name: str | None = None  # VLAN-Name
    site_id: int | None = None
    source_system_id: int | None = None

    def out(self) -> dict:
        return {k: v for k, v in (("number", self.number), ("where", self.where),
                                  ("name", self.name)) if v not in (None, "")}  # fmt: skip


def vlan_uses(session: Session, tenant_id: int) -> list[Use]:
    """VLANs der gelesenen Geraete je Standort ihres Quellsystems (wie die VLAN-Ansicht)."""
    stmt = select(SourceSystem.id, SourceSystem.name).where(
        SourceSystem.tenant_id == tenant_id, SourceSystem.platform.in_(VLAN_PLATFORMS))  # fmt: skip
    sources = dict(session.execute(stmt).all())
    if not sources:
        return []
    site_of = site_store.source_sites(session, tenant_id)
    out: dict[tuple, Use] = {}
    for vid, name, sid in session.execute(
        select(InvVlan.vid, InvVlan.name, InvVlan.source_system_id)
        .where(InvVlan.tenant_id == tenant_id, InvVlan.source_system_id.in_(list(sources)))
        .order_by(InvVlan.source_system_id, InvVlan.vid)
    ):  # fmt: skip
        site = site_of.get(sid)
        use = Use(vid, sources[sid], name or None, site.id if site else None, sid)
        out.setdefault((vid, sid, name), use)
    return list(out.values())


def asn_uses(session: Session, tenant_id: int, index=None) -> list[Use]:
    """AS-Nummern aus den BGP-Daten: lokal an gelesenen Geraeten bzw. als Nachbar."""
    from app.store import as_numbers  # spaet: baut den Graphen

    index = index or as_numbers.Index(session, tenant_id)
    out: list[Use] = []
    for number, e in sorted(index.entries.items()):
        out += [Use(number, f"{d.label} (lokal)", None, None, d.source_system_id)
                for d in e.devices]  # fmt: skip
        out += [Use(number, f"{n.at.label} → Nachbar {n.peer_ip}", None, None,
                    n.at.source_system_id) for n in e.neighbors]  # fmt: skip
    return out


class Usage:
    """Was gelesen in Gebrauch ist - je Art einmal gerechnet (Pruefungen, API)."""

    def __init__(self, session: Session, tenant_id: int):
        self.session, self.tenant_id = session, tenant_id
        self._by: dict[str, list[Use]] = {}

    def of(self, kind: str) -> list[Use]:
        if kind not in self._by:
            fn = vlan_uses if kind == "vlan" else asn_uses
            self._by[kind] = fn(self.session, self.tenant_id)
        return self._by[kind]

    def hits(self, kind: str, low: int, high: int, site_id: int | None) -> list[Use]:
        return [u for u in self.of(kind) if low <= u.number <= high
                and (kind != "vlan" or site_id is None or u.site_id == site_id)]  # fmt: skip


def used_now(usage: Usage, kind: str, low: int, high: int, site_id: int | None) -> list[int]:
    return sorted({u.number for u in usage.hits(kind, low, high, site_id)})


def snapshot(usage: Usage, r: Reservation, before: dict | None = None) -> list[int]:
    """Beim Reservieren schon belegte Nummern (`used_before`). Beim Aendern behalten Nummern,
    die die Reservierung schon hatte, ihren Stand - nur neu dazugekommene werden jetzt
    festgehalten (sonst wuerde ein „jetzt belegt“ durch das Erweitern zum Konflikt)."""
    now = used_now(usage, r.kind, r.low, r.high, r.site_id)
    if before is None:
        return now
    same_scope = before["site_id"] == r.site_id
    kept = {n for n in before.get("used_before") or [] if r.low <= n <= r.high}
    covered = range(before["low"], before["high"] + 1) if same_scope else range(0)
    return sorted(kept | {n for n in now if n not in covered})


# -- Ueberschneidungen und Befunde -------------------------------------------------------------


def overlap(a: Reservation, b: Reservation) -> tuple[int, int] | None:
    """Gemeinsamer Bereich zweier Reservierungen derselben Art (VLANs: am selben Standort bzw.
    eine fuer alle Standorte)."""
    if a.kind != b.kind:
        return None
    if a.kind == "vlan" and a.site_id is not None and b.site_id is not None \
            and a.site_id != b.site_id:  # fmt: skip
        return None
    low, high = max(a.low, b.low), min(a.high, b.high)
    return (low, high) if low <= high else None


def rows(session: Session, tenant_id: int, kind: str | None = None) -> list[Reservation]:
    stmt = select(Reservation).where(Reservation.tenant_id == tenant_id)
    if kind:
        stmt = stmt.where(Reservation.kind == kind)
    return list(session.scalars(stmt.order_by(Reservation.kind, Reservation.low,
                                              Reservation.high, Reservation.id)))  # fmt: skip


def _sites(session: Session, tenant_id: int) -> dict[int, Site]:
    return {s.id: s for s in session.scalars(select(Site).where(Site.tenant_id == tenant_id))}


def where_text(r: Reservation, sites: dict[int, Site]) -> str:
    """Standort einer VLAN-Reservierung (deutsch, gespeichert im Befund)."""
    if r.kind != "vlan":
        return ""
    if r.site_id is None:
        return "alle Standorte"
    site = sites.get(r.site_id)
    return (site.name or site.code) if site is not None else f"Standort #{r.site_id} fehlt"


def subject(r: Reservation, sites: dict[int, Site]) -> str:
    where = where_text(r, sites)
    return f"{label(r.kind, r.low, r.high)}{f' · {where}' if where else ''}"


def _detail(r: Reservation, sites: dict[int, Site]) -> dict:
    d = {"reservation_id": r.id, "kind": r.kind, "low": r.low, "high": r.high,
         "label": label(r.kind, r.low, r.high), "site": where_text(r, sites) or None,
         "site_id": r.site_id, "vlan_group": r.vlan_group, "purpose": r.purpose,
         "by": r.created_by,
         "at": r.created_at.date().isoformat() if r.created_at else None}  # fmt: skip
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class Problem:
    type: FindingType
    severity: Severity
    subject: str
    detail: dict


def problems(session: Session, tenant_id: int, usage: Usage | None = None, *,
             in_use: bool = True, overlaps: bool = True) -> list[Problem]:  # fmt: skip
    """Befunde der Reservierungen: gelesen in Gebrauch (jetzt belegt bzw. Konflikt) und
    Ueberschneidungen (doppelt reserviert)."""
    items = rows(session, tenant_id)
    if not items:
        return []
    usage = usage or Usage(session, tenant_id)
    sites = _sites(session, tenant_id)
    out: list[Problem] = []
    for r in items if in_use else []:
        hits = usage.hits(r.kind, r.low, r.high, r.site_id)
        if not hits:
            continue
        before = set(r.used_before or [])
        for conflict in (False, True):
            part = [u for u in hits if (u.number in before) == conflict]
            if not part:
                continue
            nums = sorted({u.number for u in part})
            detail = {**_detail(r, sites), "numbers": nums, "numbers_text": compact(nums),
                      "uses": [u.out() for u in part[:SHOW]], "uses_total": len(part)}  # fmt: skip
            ftype = FindingType.RESERVATION_CONFLICT if conflict else FindingType.RESERVATION_IN_USE
            sev = Severity.WARNING if conflict else Severity.INFO
            out.append(Problem(ftype, sev, subject(r, sites), detail))
    for kind in KINDS if overlaps else ():
        mine = [r for r in items if r.kind == kind]
        for i, a in enumerate(mine):
            for b in mine[i + 1 :]:
                both = overlap(a, b)
                if both is None:
                    continue
                detail = {**_detail(a, sites), "other": _detail(b, sites),
                          "overlap": numbers(*both),
                          "overlap_label": label(kind, *both)}  # fmt: skip
                text = f"{subject(a, sites)} ↔ {subject(b, sites)}"
                out.append(Problem(FindingType.RESERVATION_OVERLAP, Severity.WARNING, text,
                                   detail))  # fmt: skip
    return out


@german_only  # speichert Texte: immer deutsch (ADR 0028)
def refresh_findings(session: Session, tenant_id: int) -> int:
    """Nur die Befunde der Reservierungen neu (nach Anlegen, Aendern, Freigeben) - ohne den
    ganzen Abgleich; Triage-Status bleibt wie sonst erhalten."""
    from app.store import reconcile  # spaet: reconcile laedt die Pruefungen

    return reconcile._refresh_pass(session, tenant_id, STEP, TYPES)


# -- Ausgabe ------------------------------------------------------------------------------------


def out(r: Reservation, sites: dict[int, Site], usage: Usage | None = None,
        findings: dict[int, list[dict]] | None = None) -> dict:  # fmt: skip
    site = sites.get(r.site_id) if r.site_id is not None else None
    row = {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "kind": r.kind,
        "low": r.low,
        "high": r.high,
        "label": label(r.kind, r.low, r.high),
        "count": r.high - r.low + 1,
        "site_id": r.site_id,
        "site": site_store.site_out(site),
        "site_missing": r.site_id is not None and site is None,
        "vlan_group": r.vlan_group,
        "purpose": r.purpose,
        "used_before": r.used_before or [],
        "created_by": r.created_by,
        "created_at": r.created_at,
        "changed_by": r.changed_by,
        "changed_at": r.changed_at,
    }
    if usage is not None:
        hits = usage.hits(r.kind, r.low, r.high, r.site_id)
        before = set(r.used_before or [])
        nums = sorted({u.number for u in hits})
        row["in_use"] = [u.out() for u in hits[:SHOW]]
        row["in_use_total"] = len(hits)
        row["in_use_numbers"] = nums
        row["state"] = ("free" if not nums else
                        "conflict" if any(n in before for n in nums) else "in_use")  # fmt: skip
    if findings is not None:
        row["findings"] = findings.get(r.id, [])
    return row


def site_groups(session: Session, tenant: Tenant) -> list[dict]:
    """Vorschlaege fuer die VLAN-Gruppe in NetBox: die Gruppen, die der NetBox-Sync je
    Quellsystem mit VLANs anlegt (`<Mandant>-<Quellsystem>`), mit dem Standort."""
    site_of = site_store.source_sites(session, tenant.id)
    stmt = select(InvVlan.source_system_id).where(InvVlan.tenant_id == tenant.id).distinct()
    with_vlans = set(session.scalars(stmt))
    out = []
    for s in session.scalars(select(SourceSystem).where(
            SourceSystem.tenant_id == tenant.id, SourceSystem.platform.in_(VLAN_PLATFORMS))
            .order_by(SourceSystem.name)):  # fmt: skip
        if s.id not in with_vlans:
            continue
        site = site_of.get(s.id)
        out.append({"slug": group_slug(tenant.name, s.name), "source": s.name,
                    "site_id": site.id if site else None,
                    "site": (site.name or site.code) if site else None})  # fmt: skip
    return out


# -- Befundtexte (Findings, in der Sprache der Anfrage) ----------------------------------------


def _uses_text(d: dict) -> str:
    uses = d.get("uses") or []
    parts = []
    for u in uses[:5]:
        name = u.get("name")
        parts.append(f"{u.get('where')} („{name}“)" if name else str(u.get("where")))
    more = (d.get("uses_total") or len(uses)) - len(parts)
    text = ", ".join(parts)
    return text + (L(f" (+{more} weitere)", f" (+{more} more)") if more > 0 else "")


def _what(d: dict) -> str:
    site = d.get("site")
    at = L(f" ({site})", f" ({_site_en(site)})") if site else ""
    return f"{d.get('label') or '?'}{at}"


def _site_en(site: str) -> str:
    if site == "alle Standorte":
        return "all sites"
    m = re.fullmatch(r"Standort #(\d+) fehlt", site)
    return f"site #{m.group(1)} missing" if m else site


def _by(d: dict) -> str:
    who = d.get("by") or "?"
    at = d.get("at")
    return L(f"reserviert von {who}{f' am {at}' if at else ''}",
             f"reserved by {who}{f' on {at}' if at else ''}")  # fmt: skip


def explain(ftype: str, subject_text: str, d: dict) -> tuple[str, str]:
    """(Befund, Hinweis) - Texte der drei Befund-Typen."""
    purpose = f" „{d['purpose']}“" if d.get("purpose") else ""
    purpose_en = f" “{d['purpose']}”" if d.get("purpose") else ""
    nums = d.get("numbers_text") or ""
    unit = "VLAN" if d.get("kind") == "vlan" else "AS"
    if ftype == "reservation_in_use":
        return (
            L(f"Reservierung {_what(d)}{purpose} ({_by(d)}) ist jetzt belegt: {unit} {nums} "
              f"gelesen in Gebrauch – {_uses_text(d)}.",
              f"Reservation {_what(d)}{purpose_en} ({_by(d)}) is now in use: {unit} {nums} "
              f"read in use – {_uses_text(d)}."),
            L("Beim Reservieren war die Nummer frei. Ist die Reservierung damit umgesetzt, sie "
              "freigeben; sonst klären, wer die Nummer vergeben hat.",
              "The number was free when it was reserved. If the reservation has been put in "
              "place with this, release it; otherwise find out who assigned the number."),
        )  # fmt: skip
    if ftype == "reservation_conflict":
        return (
            L(f"Reservierung {_what(d)}{purpose} ({_by(d)}) überschneidet sich mit gelesenem "
              f"Gebrauch: {unit} {nums} war schon beim Reservieren in Gebrauch – "
              f"{_uses_text(d)}.",
              f"Reservation {_what(d)}{purpose_en} ({_by(d)}) overlaps with what is read in "
              f"use: {unit} {nums} was already in use when it was reserved – {_uses_text(d)}."),
            L("Andere, freie Nummer reservieren bzw. den Bereich der Reservierung anpassen – "
              "die Nummer ist vergeben.",
              "Reserve another, free number or adjust the range of the reservation – the "
              "number is taken."),
        )  # fmt: skip
    if ftype == "reservation_overlap":
        other = d.get("other") or {}
        theirs = other.get("purpose")
        theirs_de = f" „{theirs}“" if theirs else ""
        theirs_en = f" “{theirs}”" if theirs else ""
        both = d.get("overlap_label") or "?"
        return (
            L(f"Doppelt reserviert: {_what(d)}{purpose} und {_what(other)}{theirs_de} "
              f"überschneiden sich in {both}.",
              f"Reserved twice: {_what(d)}{purpose_en} and {_what(other)}{theirs_en} overlap "
              f"in {both}."),
            L("Eine der Reservierungen anpassen bzw. freigeben – eine Nummer gehört genau einer "
              "Reservierung.",
              "Adjust or release one of the reservations – a number belongs to exactly one "
              "reservation."),
        )  # fmt: skip
    return subject_text, ""


def state_label(state: str) -> str:
    return {"free": L("frei", "free"), "in_use": L("jetzt belegt", "now in use"),
            "conflict": L("Konflikt", "conflict")}.get(state, state)  # fmt: skip


def findings_by_reservation(session: Session, tenant_id: int) -> dict[int, list[dict]]:
    """Offene bzw. bestaetigte Befunde je Reservierung (fuer die Ansicht)."""
    from app.models import Finding, FindingStatus

    out: dict[int, list[dict]] = defaultdict(list)
    for fid, ftype, status, detail in session.execute(
        select(Finding.id, Finding.type, Finding.status, Finding.detail)
        .where(Finding.tenant_id == tenant_id, Finding.type.in_(list(TYPES)),
               Finding.status.in_((FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED)))
        .order_by(Finding.id)
    ):  # fmt: skip
        d = detail if isinstance(detail, dict) else {}
        ids = [d.get("reservation_id"), (d.get("other") or {}).get("reservation_id")]
        for rid in ids:
            if isinstance(rid, int):
                out[rid].append({"id": fid, "type": ftype.value, "status": status.value})
    return out


SUBJECT_KIND = SubjectKind.RESERVATION
