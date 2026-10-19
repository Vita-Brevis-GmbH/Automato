"""Namensprofile & Wörterbücher (ADR 0004-Ergänzung).

Lesen = `viewer`, Schreiben (Profile/Einträge pflegen) = `admin` (RBAC, E1/ADR 0007).
`kind` ist ein offener String → neue Namenskonventionsvorgaben ohne Schema-Änderung.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin, require_viewer
from app.auth.principal import Principal
from app.db import get_session
from app.models import DictionaryEntry
from app.schemas.naming_profile import (
    DictionaryEntryIn,
    DictionaryEntryOut,
    NamingProfileIn,
    NamingProfileOut,
)
from app.store import naming_profiles as store

router = APIRouter(prefix="/naming-profiles", tags=["naming-profiles"])


def _entry_out(e: DictionaryEntry) -> DictionaryEntryOut:
    return DictionaryEntryOut(
        id=e.id,
        profile_id=e.profile_id,
        kind=e.kind,
        key=e.key,
        value=e.value,
        extra=e.extra,
        updated_at=e.updated_at,
    )


@router.get("", response_model=list[NamingProfileOut])
def list_profiles(
    _: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    return [
        NamingProfileOut(id=p.id, customer=p.customer, name=p.name)
        for p in store.list_profiles(session)
    ]


@router.post("", response_model=NamingProfileOut, status_code=201)
def create_profile(
    body: NamingProfileIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    p = store.get_or_create_profile(session, customer=body.customer, name=body.name)
    if body.with_defaults:
        store.apply_defaults(session, p.id)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="naming_profile.created",
        subject=str(p.id),
        detail={"profile_id": p.id, "customer": p.customer, "name": p.name},
    )
    return NamingProfileOut(id=p.id, customer=p.customer, name=p.name)


@router.get("/{profile_id}/dictionary", response_model=list[DictionaryEntryOut])
def list_entries(
    profile_id: int,
    kind: str | None = None,
    _: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    if store.get_profile(session, profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return [_entry_out(e) for e in store.list_entries(session, profile_id, kind=kind)]


@router.post("/{profile_id}/dictionary", response_model=DictionaryEntryOut)
def upsert_entry(
    profile_id: int,
    body: DictionaryEntryIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if store.get_profile(session, profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    e = store.upsert_entry(session, profile_id, body.kind, body.key, body.value, body.extra)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="dictionary_entry.upserted",
        subject=str(e.id),
        detail={"profile_id": profile_id, "kind": body.kind, "key": body.key},
    )
    return _entry_out(e)


@router.delete("/dictionary/{entry_id}", status_code=204)
def delete_entry(
    entry_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not store.delete_entry(session, entry_id):
        raise HTTPException(status_code=404, detail="entry not found")
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="dictionary_entry.deleted",
        subject=str(entry_id),
        detail={"entry_id": entry_id},
    )
