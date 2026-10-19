from datetime import datetime

from pydantic import BaseModel


class NamingProfileOut(BaseModel):
    id: int
    customer: str
    name: str


class NamingProfileIn(BaseModel):
    customer: str
    name: str
    # Optional mit den Werten des Standardprofils „default" vorbefüllen.
    with_defaults: bool = False


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
