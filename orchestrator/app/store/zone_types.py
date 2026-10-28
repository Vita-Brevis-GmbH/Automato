"""Zonentyp je Zone (0.105.0, ADR 0035).

Anwender 2026-10-28: „Ich weise einer Zone einen Zonentyp zu, also DC, DC-Ressourcen, Edge
Internet usw.“ und „Zonentyp aus dem Grundschema, welches ich in das Namensprofil hochlade“.
Der Zonentyp einer Zone ist die **Zone des Grundschemas**, zu der sie gehoert:

- von Hand (`logical_zone.zone_type`), wenn die Erkennung nicht passt;
- sonst automatisch wie beim Hinweis „Zone ausserhalb des Schemas“: gleicher Name bzw.
  Kurzname, Erkennungsmuster, bei Zonen eines Kunden die Vorlage je Kunde ohne Name bzw.
  Kuerzel des Kunden („DC Kunde A“ → „DC“);
- ohne hochgeladenes Grundschema die eingebaute Vorlage.

Die Kategorien der Applikations-Doku nennen Richtungen zwischen Zonentypen
(`appdoc_catalog.Category.flows`). Rein lesend.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import LogicalZone


def schema_for(session: Session, tenant_id: int | None):
    """Grundschema des Mandanten (Namensprofil), sonst die eingebaute Vorlage."""
    from app.store import zone_schema  # spaet: zone_schema -> naming_profiles -> ...

    schema = zone_schema.for_tenant(session, tenant_id)[0] if tenant_id is not None else None
    return schema if schema is not None and schema.zones else zone_schema.default_schema()


def names(session: Session, tenant_id: int | None) -> list[str]:
    """Zonentypen zur Auswahl: die Zonen des Grundschemas in ihrer Reihenfolge."""
    return [z.name for z in schema_for(session, tenant_id).zones]


def types(session: Session, tenant_id: int | None, items: list[LogicalZone],
          schema=None, manual: bool = True) -> dict[int, str | None]:  # fmt: skip
    """Zonentyp je Zone: von Hand (ohne `manual` nur erkannt), sonst die erkannte Zone des
    Grundschemas, sonst keiner."""
    from app.store import zone_detect  # spaet: zone_detect -> zones -> ...

    schema = schema or schema_for(session, tenant_id)
    owners = zone_detect._customer_names(session, tenant_id) if tenant_id is not None else {}
    mapped = zone_detect._mapped(schema, items, owners, manual)
    return {z.id: (sz.name if (sz := mapped.get(z.id)) else None) for z in items}
