"""Zonentyp je Zone (0.105.0, ADR 0035).

Anwender 2026-10-28: „Ich weise einer Zone einen Zonentyp zu, also DC, DC-Ressourcen, Edge
Internet usw.“ und „Zonentyp aus dem Grundschema, welches ich in das Namensprofil hochlade“.
Der Zonentyp einer Zone ist die **Zone des Grundschemas**, zu der sie gehoert:

- von Hand (`logical_zone.zone_type`), wenn die Erkennung nicht passt;
- sonst automatisch wie beim Hinweis „Zone ausserhalb des Schemas“: gleicher Name bzw.
  Kurzname, Erkennungsmuster, bei Zonen eines Kunden die Vorlage je Kunde ohne Name bzw.
  Kuerzel des Kunden („DC Kunde A“ → „DC“);
- ohne hochgeladenes Grundschema die eingebaute Vorlage.

Die Kategorien der Applikations-Doku nennen je eine Richtung zwischen Zonentypen
(`appdoc_catalog.Category.src`/`dst`). Seit 0.106.0 auch die **Geraete** zur Auswahl je Zone
(Netzwerkdesign → Kategorien): Firewalls bzw. VDOMs, ACI-Tenants, ACI-VRFs und Anbindungen.
Rein lesend.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Epg, LogicalZone, SourceSystem, TopoContext, TopoExit


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


def devices(session: Session, tenant_id: int) -> list[dict]:
    """Geraete zur Auswahl je Zone (0.106.0): Firewall bzw. VDOM (`fw_vdom`, Kontext je
    Quellsystem), ACI-Tenant (Tenants der EPGs), ACI-VRF (`Tenant:VRF`) und die Anbindungen
    (Routing → Anbindungen) - mit Anzeige, nach Art und Name."""
    names = dict(session.execute(
        select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    ).all())  # fmt: skip
    out: dict[tuple, dict] = {}
    contexts = session.execute(
        select(TopoContext.source_system_id, TopoContext.context, TopoContext.kind)
        .where(TopoContext.tenant_id == tenant_id).distinct()
    ).all()  # fmt: skip
    vdoms: dict[int, int] = {}
    for sid, _ctx, kind in contexts:
        if kind == "vdom":
            vdoms[sid] = vdoms.get(sid, 0) + 1
    for sid, ctx, kind in contexts:
        source = names.get(sid, f"#{sid}")
        if kind == "vdom":
            label = source if vdoms.get(sid) == 1 else f"{source} · {ctx}"
            out[("fw_vdom", ctx, sid)] = {"kind": "fw_vdom", "value": ctx, "source_system_id": sid,
                                          "label": label}  # fmt: skip
        elif kind == "vrf":
            out[("aci_vrf", ctx, None)] = {"kind": "aci_vrf", "value": ctx,
                                           "source_system_id": None, "label": ctx}  # fmt: skip
    for tenant in session.scalars(
        select(Epg.aci_tenant).where(Epg.tenant_id == tenant_id, Epg.kind == "epg",
                                     Epg.aci_tenant.is_not(None)).distinct()
    ):  # fmt: skip
        out[("aci_tenant", tenant, None)] = {"kind": "aci_tenant", "value": tenant,
                                             "source_system_id": None, "label": tenant}  # fmt: skip
    for name in session.scalars(select(TopoExit.name).where(TopoExit.tenant_id == tenant_id)):
        if name:
            out[("exit", name, None)] = {"kind": "exit", "value": name, "source_system_id": None,
                                         "label": name}  # fmt: skip
    order = {"fw_vdom": 0, "aci_tenant": 1, "aci_vrf": 2, "exit": 3}
    return sorted(out.values(), key=lambda d: (order[d["kind"]], d["label"].casefold()))
