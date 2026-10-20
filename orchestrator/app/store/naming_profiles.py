"""Namensprofil-Store: Wörterbücher pro Kunde (ADR 0004-Ergänzung).

Lesen/Schreiben der anwendungsverwalteten, erweiterbaren Wörterbücher. `kind` ist ein
offener String; `upsert_entry` aktualisiert einen bestehenden (profile, kind, key)
statt zu duplizieren. Das Standardprofil „default" (Werte: `app.naming_defaults`,
menschenlesbar: `docs/naming-conventions.md`) gilt für Mandanten ohne eigenes Profil.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import naming
from app.models import DictionaryEntry, NamingProfile, Tenant
from app.naming_defaults import DEFAULT_DICTIONARY, DEFAULT_PROFILE


def list_profiles(session: Session) -> list[NamingProfile]:
    return list(session.scalars(select(NamingProfile).order_by(NamingProfile.id)))


def get_profile(session: Session, profile_id: int) -> NamingProfile | None:
    return session.get(NamingProfile, profile_id)


def find_profile(session: Session, customer: str, name: str) -> NamingProfile | None:
    stmt = select(NamingProfile).where(
        NamingProfile.customer == customer, NamingProfile.name == name
    )
    return session.scalars(stmt).first()


def get_or_create_profile(session: Session, customer: str, name: str) -> NamingProfile:
    profile = find_profile(session, customer, name)
    if profile is None:
        profile = NamingProfile(customer=customer, name=name)
        session.add(profile)
        session.flush()
    return profile


def list_entries(
    session: Session, profile_id: int, kind: str | None = None
) -> list[DictionaryEntry]:
    stmt = select(DictionaryEntry).where(DictionaryEntry.profile_id == profile_id)
    if kind is not None:
        stmt = stmt.where(DictionaryEntry.kind == kind)
    return list(session.scalars(stmt.order_by(DictionaryEntry.kind, DictionaryEntry.key)))


def upsert_entry(
    session: Session,
    profile_id: int,
    kind: str,
    key: str,
    value: str | None = None,
    extra: dict | None = None,
) -> DictionaryEntry:
    stmt = select(DictionaryEntry).where(
        DictionaryEntry.profile_id == profile_id,
        DictionaryEntry.kind == kind,
        DictionaryEntry.key == key,
    )
    entry = session.scalars(stmt).first()
    if entry is None:
        entry = DictionaryEntry(profile_id=profile_id, kind=kind, key=key, value=value, extra=extra)
        session.add(entry)
    else:
        entry.value = value
        entry.extra = extra
    session.flush()
    return entry


def delete_entry(session: Session, entry_id: int) -> bool:
    entry = session.get(DictionaryEntry, entry_id)
    if entry is None:
        return False
    session.delete(entry)
    return True


def copy_entries(
    session: Session,
    source_profile_id: int,
    target_profile_id: int,
    source_kind: str | None = None,
    target_kind: str | None = None,
) -> int:
    """Einträge eines Profils (optional nur eine Art) in ein Profil kopieren (upsert).

    Mit `target_kind` landet die Quell-Art unter neuem Namen - so entsteht eine neue
    Namenskonvention aus einer bestehenden. Rückgabe: Anzahl kopierter Einträge.
    """
    entries = list_entries(session, source_profile_id, kind=source_kind)
    for e in entries:
        kind = target_kind if target_kind else e.kind
        upsert_entry(session, target_profile_id, kind, e.key, e.value, e.extra)
    return len(entries)


def get_default_profile(session: Session) -> NamingProfile | None:
    """Das Standardprofil „default" (gilt für Mandanten ohne eigenes Profil)."""
    stmt = select(NamingProfile).where(
        NamingProfile.customer == DEFAULT_PROFILE, NamingProfile.name == DEFAULT_PROFILE
    )
    return session.scalars(stmt).first()


def apply_defaults(session: Session, profile_id: int) -> None:
    """Standardwerte in ein Profil übernehmen (idempotent, optional).

    Quelle ist das gepflegte Profil „default" (falls vorhanden), sonst die eingebaute
    Vorgabe - so übernehmen neue Profile auch Anpassungen am Standard.
    """
    default = get_default_profile(session)
    if default is not None and default.id != profile_id:
        pairs = [(e.kind, e.key, e.value) for e in list_entries(session, default.id)]
    else:
        pairs = [(k, key, value) for k, items in DEFAULT_DICTIONARY.items() for key, value in items]
    for kind, key, value in pairs:
        upsert_entry(session, profile_id, kind, key, value)


def seed_default_profile(session: Session) -> NamingProfile:
    """Legt das Standardprofil „default" mit den Vorgabewerten idempotent an."""
    profile = get_or_create_profile(session, customer=DEFAULT_PROFILE, name=DEFAULT_PROFILE)
    apply_defaults(session, profile.id)
    return profile


def prefixes_for_tenant(session: Session, tenant_id: int | None) -> naming.PrefixList | None:
    """Praefix-Liste aus dem Namensprofil des Mandanten.

    Ohne eigenes Profil gilt das Standardprofil „default"; fehlt auch das (oder ist es
    leer), None = eingebaute Standard-Konvention (keine Funktionsabhaengigkeit von Daten).
    """
    profile_id = profile_id_for_tenant(session, tenant_id)
    if profile_id is None:
        return None
    entries = list_entries(session, profile_id, kind="object_prefix")
    return naming.prefixes_from_entries((e.key, e.value) for e in entries)


def profile_id_for_tenant(session: Session, tenant_id: int | None) -> int | None:
    """Eigenes Namensprofil des Mandanten, sonst „default"; None = keines vorhanden."""
    tenant = session.get(Tenant, tenant_id) if tenant_id is not None else None
    profile_id = getattr(tenant, "naming_profile_id", None)
    if profile_id:
        return profile_id
    default = get_default_profile(session)
    return default.id if default else None


def entries_for_tenant(session: Session, tenant_id: int | None, kind: str) -> dict[str, str]:
    """Woerterbuch-Art des Mandanten-Profils als key -> value (leer, wenn nicht gepflegt)."""
    profile_id = profile_id_for_tenant(session, tenant_id)
    if profile_id is None:
        return {}
    return {e.key: e.value or "" for e in list_entries(session, profile_id, kind=kind)}
