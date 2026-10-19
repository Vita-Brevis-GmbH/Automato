"""Namensprofil-Store: Wörterbücher pro Kunde (ADR 0004-Ergänzung).

Lesen/Schreiben der anwendungsverwalteten, erweiterbaren Wörterbücher. `kind` ist ein
offener String; `upsert_entry` aktualisiert einen bestehenden (profile, kind, key)
statt zu duplizieren. Das Standardprofil „default" (Werte: `app.naming_defaults`,
menschenlesbar: `docs/naming-conventions.md`) gilt für Mandanten ohne eigenes Profil.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DictionaryEntry, NamingProfile
from app.naming_defaults import DEFAULT_DICTIONARY, DEFAULT_PROFILE


def list_profiles(session: Session) -> list[NamingProfile]:
    return list(session.scalars(select(NamingProfile).order_by(NamingProfile.id)))


def get_profile(session: Session, profile_id: int) -> NamingProfile | None:
    return session.get(NamingProfile, profile_id)


def get_or_create_profile(session: Session, customer: str, name: str) -> NamingProfile:
    stmt = select(NamingProfile).where(
        NamingProfile.customer == customer, NamingProfile.name == name
    )
    profile = session.scalars(stmt).first()
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
