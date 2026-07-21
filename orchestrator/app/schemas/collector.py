"""Vertrag zwischen Collectors und Orchestrator.

VORLAEUFIG: Dieses normalisierte Regel-DTO ist der Startpunkt fuer das
Datenmodell und wird in Meilenstein M5 finalisiert (Adressobjekt-Referenzen,
kanonische Services, Applikations-Zuordnung). Collectors liefern Regeln in
genau dieser Form an POST /internal/collector-runs/{run_id}/result.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class NormalizedRule(BaseModel):
    source_platform: Literal["fortigate", "cisco_aci"]
    external_id: str  # ID/Ref der Regel im Quellsystem
    name: str = ""
    action: Literal["allow", "deny"]
    sources: list[str] = Field(default_factory=list)  # IPs/Netze/Objektnamen (roh)
    destinations: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)  # z. B. "tcp/443"
    src_zone: str | None = None
    dst_zone: str | None = None
    raw: dict[str, Any] | None = None  # Original-Payload fuer Nachvollziehbarkeit


class CollectorResult(BaseModel):
    status: Literal["completed", "failed"]
    rules: list[NormalizedRule] = Field(default_factory=list)
    message: str | None = None
