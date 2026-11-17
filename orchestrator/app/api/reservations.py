"""Reservierungen von VLAN- und AS-Nummern (1.19.0, ADR 0066) - Laufzeitdaten je Mandant.

- `GET    /reservations`          Reservierungen mit Stand gegen das Gelesene (frei, jetzt belegt,
                                  Konflikt), den Stellen in Gebrauch und den Befunden (viewer)
- `GET    /reservations/options`  Standorte des Mandanten und Vorschlaege fuer die VLAN-Gruppe in
                                  NetBox (viewer)
- `POST   /reservations`          reservieren (operator): merkt die schon gelesen belegten Nummern
                                  (`used_before`) - die Antwort nennt sie und Ueberschneidungen
- `PUT    /reservations/{id}`     aendern (operator)
- `DELETE /reservations/{id}`     freigeben (operator)

Jede Aenderung mit Audit (`reservation.*` - zaehlt nicht fuer den Stand der Doku) und danach nur
die Befunde der Reservierungen neu (`store/reservations.refresh_findings`). Nur Daten der
Plattform; nach NetBox nur ueber den NetBox-Sync (Abschnitt „Reservierungen“).
"""

import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audit
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import Reservation, Site, Tenant
from app.store import reservations as store
from app.store import sites as site_store

router = APIRouter(prefix="/reservations", tags=["reservations"])

_GROUP = re.compile(r"^[a-z0-9_-]{1,100}$")


class ReservationIn(BaseModel):
    tenant_id: int
    kind: Literal["vlan", "asn"]
    low: int = Field(ge=1, le=store.LIMITS["asn"][1])
    high: int | None = Field(default=None, ge=1, le=store.LIMITS["asn"][1])  # ohne = eine
    site_id: int | None = None  # nur VLAN; ohne = alle Standorte
    vlan_group: str | None = Field(default=None, max_length=100)  # nur VLAN: Slug in NetBox
    purpose: str = Field(min_length=1, max_length=500)


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "reservations")
    return tid


def _invalid(de: str, en: str) -> HTTPException:
    return HTTPException(status_code=422, detail=L(de, en))


def _clean(session: Session, body: ReservationIn) -> dict:
    """Eingaben pruefen: Bereich je Art, Standort des Mandanten, VLAN-Gruppe als Slug."""
    lo_max, hi_max = store.LIMITS[body.kind]
    high = body.high if body.high is not None else body.low
    what = "VLAN" if body.kind == "vlan" else "AS"
    if not (lo_max <= body.low <= hi_max and lo_max <= high <= hi_max):
        raise _invalid(f"{what}: nur {lo_max}–{hi_max}", f"{what}: only {lo_max}–{hi_max}")
    if high < body.low:
        raise _invalid("Bereich: bis muss ≥ von sein", "Range: to must be ≥ from")
    site_id, group = body.site_id, (body.vlan_group or "").strip().lower() or None
    if body.kind != "vlan" and (site_id is not None or group):
        raise _invalid("Standort und VLAN-Gruppe nur bei VLANs",
                       "Site and VLAN group only for VLANs")  # fmt: skip
    if site_id is not None:
        site = session.get(Site, site_id)
        if site is None or site.tenant_id != body.tenant_id:
            raise _invalid("Standort gehört nicht zum Mandanten",
                           "Site does not belong to the tenant")  # fmt: skip
    if group is not None and not _GROUP.match(group):
        raise _invalid("VLAN-Gruppe: Slug aus a-z, 0-9, - und _",
                       "VLAN group: slug of a-z, 0-9, - and _")  # fmt: skip
    purpose = body.purpose.strip()
    if not purpose:
        raise _invalid("Zweck angeben", "Specify a purpose")
    return {"kind": body.kind, "low": body.low, "high": high, "site_id": site_id,
            "vlan_group": group, "purpose": purpose}  # fmt: skip


def _audit(session: Session, principal: Principal, action: str, r: Reservation) -> None:
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"reservation.{action}",
        subject=str(r.id),
        detail={
            "tenant_id": r.tenant_id,
            "reservation_id": r.id,
            "kind": r.kind,
            "low": r.low,
            "high": r.high,
            "site_id": r.site_id,
            "vlan_group": r.vlan_group,
            "purpose": r.purpose,
        },  # fmt: skip
    )


def _sites(session: Session, tid: int) -> dict[int, Site]:
    return {s.id: s for s in session.query(Site).filter(Site.tenant_id == tid)}


def _with_notes(session: Session, r: Reservation, usage: store.Usage) -> dict:
    """Antwort nach dem Speichern: die Reservierung, was davon schon gelesen in Gebrauch ist und
    mit welchen Reservierungen sie sich ueberschneidet."""
    sites = _sites(session, r.tenant_id)
    row = store.out(r, sites, usage, store.findings_by_reservation(session, r.tenant_id))
    row["overlaps"] = [
        {"id": o.id, "label": store.label(o.kind, o.low, o.high), "purpose": o.purpose,
         "overlap": store.numbers(*both)}
        for o in store.rows(session, r.tenant_id, r.kind)
        if o.id != r.id and (both := store.overlap(r, o)) is not None
    ]  # fmt: skip
    return row


@router.get("")
def list_reservations(
    tenant_id: int | None = None,
    kind: Literal["vlan", "asn"] | None = None,
    q: str | None = Query(None, max_length=200, description="Nummer, Zweck, Standort, wer"),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(session, principal, tenant_id)
    sites = _sites(session, tid)
    usage = store.Usage(session, tid)
    found = store.findings_by_reservation(session, tid)
    items = [store.out(r, sites, usage, found) for r in store.rows(session, tid, kind)]
    if q:
        needle = q.strip().lower()
        number = int(needle) if needle.isdigit() else None
        items = [i for i in items if (number is not None and i["low"] <= number <= i["high"])
                 or needle in " ".join(str(x) for x in (
                     i["label"], i["purpose"], i["created_by"], i["vlan_group"],
                     (i["site"] or {}).get("name"), (i["site"] or {}).get("code"))
                     if x).lower()]  # fmt: skip
    summary = {k: {"total": 0, "free": 0, "in_use": 0, "conflict": 0} for k in store.KINDS}
    for i in items:
        summary[i["kind"]]["total"] += 1
        summary[i["kind"]][i["state"]] += 1
    return {"items": items, "summary": summary}


@router.get("/options")
def options(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(session, principal, tenant_id)
    tenant = session.get(Tenant, tid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    sites = sorted(_sites(session, tid).values(), key=lambda s: (s.kind != "physical", s.name))
    return {
        "sites": [site_store.site_out(s) for s in sites],
        "vlan_groups": store.site_groups(session, tenant),
        "limits": {k: list(v) for k, v in store.LIMITS.items()},
    }


@router.post("", status_code=201)
def create_reservation(
    body: ReservationIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Reservieren (Operator). Die Nummern, die gelesen schon in Gebrauch sind, bleiben als
    `used_before` stehen - sie sind ein Konflikt, kein „jetzt belegt“."""
    tenant_scope(principal, body.tenant_id)
    r = Reservation(tenant_id=body.tenant_id, created_by=principal.subject,
                    created_at=datetime.now(UTC), **_clean(session, body))  # fmt: skip
    usage = store.Usage(session, body.tenant_id)
    r.used_before = store.snapshot(usage, r)
    session.add(r)
    session.flush()
    _audit(session, principal, "created", r)
    store.refresh_findings(session, body.tenant_id)
    return _with_notes(session, r, usage)


def _get(session: Session, principal: Principal, reservation_id: int) -> Reservation:
    r = session.get(Reservation, reservation_id)
    if r is None:
        raise HTTPException(status_code=404, detail="reservation not found")
    tenant_scope(principal, r.tenant_id)
    return r


@router.put("/{reservation_id}")
def update_reservation(
    reservation_id: int,
    body: ReservationIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    r = _get(session, principal, reservation_id)
    if body.tenant_id != r.tenant_id:
        raise HTTPException(status_code=422, detail="tenant_id must not change")
    values = _clean(session, body)
    if values["kind"] != r.kind:
        raise _invalid("Art lässt sich nicht ändern", "The kind cannot be changed")
    before = {"low": r.low, "high": r.high, "site_id": r.site_id, "used_before": r.used_before}
    for key, value in values.items():
        setattr(r, key, value)
    usage = store.Usage(session, r.tenant_id)
    r.used_before = store.snapshot(usage, r, before)
    r.changed_by, r.changed_at = principal.subject, datetime.now(UTC)
    session.flush()
    _audit(session, principal, "updated", r)
    store.refresh_findings(session, r.tenant_id)
    return _with_notes(session, r, usage)


@router.delete("/{reservation_id}", status_code=204)
def release_reservation(
    reservation_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Freigeben: die Reservierung entfaellt (Audit haelt sie fest); in NetBox entfernt sie der
    naechste Sync mit „Löschen zulassen“ (sonst veraltet)."""
    r = _get(session, principal, reservation_id)
    tid = r.tenant_id
    _audit(session, principal, "released", r)
    session.delete(r)
    session.flush()
    store.refresh_findings(session, tid)
