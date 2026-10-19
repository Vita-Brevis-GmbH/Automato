from datetime import datetime

from pydantic import BaseModel, Field


class NamingProfileOut(BaseModel):
    id: int
    customer: str
    name: str


class NamingProfileIn(BaseModel):
    customer: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    # Vorlage: alle Wörterbücher eines bestehenden Profils übernehmen (Kopie, danach
    # unabhängig) - oder mit den Werten des Standardprofils „default" vorbefüllen.
    copy_from_profile_id: int | None = None
    with_defaults: bool = False


class DictionaryCopyIn(BaseModel):
    """Eine Wörterbuch-Art (z. B. object_prefix) aus einem Profil in eine Art kopieren."""

    source_profile_id: int
    source_kind: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=64)


class DictionaryCopyOut(BaseModel):
    profile_id: int
    kind: str
    copied: int


class DictionaryEntryOut(BaseModel):
    id: int
    profile_id: int
    kind: str  # offen/erweiterbar
    key: str
    value: str | None = None
    extra: dict | None = None
    updated_at: datetime | None = None


class DictionaryEntryIn(BaseModel):
    kind: str
    key: str
    value: str | None = None
    extra: dict | None = None
