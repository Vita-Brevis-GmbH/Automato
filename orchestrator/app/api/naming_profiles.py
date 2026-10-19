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
    DictionaryCopyIn,
    DictionaryCopyOut,
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
    customer, name = body.customer.strip(), body.name.strip()
    if not customer or not name:
        raise HTTPException(status_code=422, detail="customer and name must not be empty")
    if store.find_profile(session, customer, name) is not None:
        raise HTTPException(status_code=409, detail="naming profile already exists")
    source = None
    if body.copy_from_profile_id is not None:
        source = store.get_profile(session, body.copy_from_profile_id)
        if source is None:
            raise HTTPException(status_code=422, detail="source profile not found")
    p = store.get_or_create_profile(session, customer=customer, name=name)
    if source is not None:
        store.copy_entries(session, source.id, p.id)
    elif body.with_defaults:
        store.apply_defaults(session, p.id)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="naming_profile.created",
        subject=str(p.id),
        detail={
            "profile_id": p.id,
            "customer": p.customer,
            "name": p.name,
            "copied_from": source.id if source else None,
        },
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


@router.post("/{profile_id}/dictionary/copy", response_model=DictionaryCopyOut)
def copy_dictionary(
    profile_id: int,
    body: DictionaryCopyIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Eine bestehende Wörterbuch-Art (auch aus einem anderen Profil) als Ausgangspunkt für
    eine Art in diesem Profil übernehmen - Admin. Bestehende Schlüssel werden aktualisiert."""
    if store.get_profile(session, profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    if store.get_profile(session, body.source_profile_id) is None:
        raise HTTPException(status_code=422, detail="source profile not found")
    kind = body.kind.strip()
    if body.source_profile_id == profile_id and body.source_kind == kind:
        raise HTTPException(status_code=422, detail="source and target are identical")
    n = store.copy_entries(
        session, body.source_profile_id, profile_id, source_kind=body.source_kind, target_kind=kind
    )
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="dictionary.copied",
        subject=str(profile_id),
        detail={
            "profile_id": profile_id,
            "kind": kind,
            "source_profile_id": body.source_profile_id,
            "source_kind": body.source_kind,
            "count": n,
        },
    )
    return DictionaryCopyOut(profile_id=profile_id, kind=kind, copied=n)


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
