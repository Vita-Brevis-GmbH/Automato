"""Namensprofile & Wörterbücher (ADR 0004-Ergänzung).

Lesen = `viewer`, Schreiben (Profile/Einträge pflegen) = `admin` (RBAC, E1/ADR 0007).
`kind` ist ein offener String → neue Namenskonventionsvorgaben ohne Schema-Änderung.

Seit 0.55.0 das **Grundschema der Zonen** als draw.io-Zeichnung je Profil
(`store/zone_schema.py`): Vorschau, speichern und entfernen = `admin`, lesen, Zeichnung und
Vorlage herunterladen = `viewer`.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import DictionaryEntry, Tenant
from app.schemas.naming_profile import (
    DictionaryCopyIn,
    DictionaryCopyOut,
    DictionaryEntryIn,
    DictionaryEntryOut,
    NamingProfileIn,
    NamingProfileOut,
)
from app.store import naming_profiles as store
from app.store import zone_schema

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
    session: Session = Depends(get_session, scope="function"),
):
    return [
        NamingProfileOut(id=p.id, customer=p.customer, name=p.name)
        for p in store.list_profiles(session)
    ]


@router.post("", response_model=NamingProfileOut, status_code=201)
def create_profile(
    body: NamingProfileIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
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
    session: Session = Depends(get_session, scope="function"),
):
    if store.get_profile(session, profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return [_entry_out(e) for e in store.list_entries(session, profile_id, kind=kind)]


@router.post("/{profile_id}/dictionary", response_model=DictionaryEntryOut)
def upsert_entry(
    profile_id: int,
    body: DictionaryEntryIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
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
    session: Session = Depends(get_session, scope="function"),
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
    session: Session = Depends(get_session, scope="function"),
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


# -- Grundschema der Zonen (0.55.0, ADR 0024 Nr. 11) -------------------------------------


class ZoneSchemaIn(BaseModel):
    filename: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1, max_length=zone_schema.MAX_BYTES)


def _profile(session: Session, profile_id: int):
    p = store.get_profile(session, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return p


def _parse(body: ZoneSchemaIn) -> zone_schema.Schema:
    try:
        return zone_schema.parse(body.content)
    except zone_schema.SchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _schema_out(session: Session, profile_id: int) -> dict:
    schema = zone_schema.load(session, profile_id)
    m = zone_schema.meta(session, profile_id)
    info = (m.extra or {}) if m else {}
    return {
        "profile_id": profile_id,
        "schema": schema.out() if schema else None,
        "drawing": {"filename": m.value, "uploaded_by": info.get("uploaded_by"),
                    "uploaded_at": info.get("uploaded_at")} if m else None,
    }  # fmt: skip


def _file(content: str, name: str) -> Response:
    return Response(content=content, media_type="application/vnd.jgraph.mxfile",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


@router.get("/zone-schema/template")
def zone_schema_template(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Vorlage als draw.io: Beispiel-Zonen mit Eigenschaften und Uebergaengen - bzw. mit
    `tenant_id` die logischen Zonen und Absichten dieses Mandanten als Ausgangspunkt."""
    if tenant_id is None:
        return _file(zone_schema.drawing(zone_schema.default_schema(), "Zonenschema (Vorlage)"),
                     "zonenschema-vorlage.drawio")  # fmt: skip
    tid = tenant_scope(principal, tenant_id)
    tenant = session.get(Tenant, tid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    schema = zone_schema.from_zones(session, tid)
    return _file(zone_schema.drawing(schema, f"Zonenschema {tenant.name}"),
                 "zonenschema-aus-zonen.drawio")  # fmt: skip


@router.get("/{profile_id}/zone-schema")
def get_zone_schema(
    profile_id: int,
    _: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Grundschema des Profils: Zonen, Uebergaenge, Hinweise und die Zeichnung."""
    _profile(session, profile_id)
    return _schema_out(session, profile_id)


@router.post("/{profile_id}/zone-schema/preview")
def preview_zone_schema(
    profile_id: int,
    body: ZoneSchemaIn,
    _: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zeichnung lesen, ohne zu speichern: Schema, Hinweise, Unterschied zum gespeicherten."""
    _profile(session, profile_id)
    schema = _parse(body)
    return {"schema": schema.out(),
            "diff": zone_schema.diff(zone_schema.load(session, profile_id), schema)}  # fmt: skip


@router.put("/{profile_id}/zone-schema")
def save_zone_schema(
    profile_id: int,
    body: ZoneSchemaIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zeichnung als Grundschema des Profils speichern (Admin) - ersetzt das bisherige."""
    _profile(session, profile_id)
    schema = _parse(body)
    zone_schema.save(session, profile_id, schema, body.content, body.filename, principal.subject)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="zone_schema.saved",
        subject=str(profile_id),
        detail={"profile_id": profile_id, "zones": len(schema.zones), "links": len(schema.links)},
    )
    return _schema_out(session, profile_id)


@router.delete("/{profile_id}/zone-schema", status_code=204)
def delete_zone_schema(
    profile_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Grundschema entfernen (Admin) - Zonen und Absichten in Automato bleiben."""
    _profile(session, profile_id)
    if zone_schema.remove(session, profile_id):
        audit.record(
            session,
            actor=f"user:{principal.subject}",
            action="zone_schema.deleted",
            subject=str(profile_id),
            detail={"profile_id": profile_id},
        )


@router.get("/{profile_id}/zone-schema/drawing")
def zone_schema_drawing(
    profile_id: int,
    _: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Die hochgeladene Zeichnung (zum Weiterbearbeiten in draw.io)."""
    p = _profile(session, profile_id)
    m = zone_schema.meta(session, profile_id)
    if m is None or not (m.extra or {}).get("content"):
        schema = zone_schema.load(session, profile_id)
        if schema is None:
            raise HTTPException(status_code=404, detail="no zone schema")
        return _file(zone_schema.drawing(schema, f"Zonenschema {p.name}"), "zonenschema.drawio")
    name = (m.value or "zonenschema.drawio").replace('"', "").replace("\n", " ")
    return _file(
        m.extra["content"], name if name.endswith((".drawio", ".xml")) else f"{name}.drawio"
    )
