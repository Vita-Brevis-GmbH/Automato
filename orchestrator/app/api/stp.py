"""Spanning Tree (1.19.0, Anwender 2026-11-17 Prioritaet 5): Ansicht je VLAN bzw. Standort und
Soll-Root.

- `GET  /stp/overview`      Kennzahlen, Switches mit Status/Modus/err-disabled, Standorte, Soll.
- `GET  /stp/trees`         Baeume (VLAN bzw. MST-Instanz und Root) mit Root, Soll, Standorten,
                            blockierten und unerwartet blockierten Ports; Filter VLAN, Standort,
                            Suche, nur Auffaelliges.
- `GET  /stp/tree`          ein Baum mit Switches, Ports (Rolle, Zustand, Gegenstelle) und Kanten
                            fuer die Darstellung.
- `GET  /stp/targets`       Soll-Root je VLAN-Bereich bzw. Standort.
- `PUT  /stp/targets`       Soll-Eintrag anlegen bzw. aendern (operator, Audit) - danach rechnen
                            die Befunde des Spanning Tree neu.
- `DELETE /stp/targets/{id}` Soll-Eintrag loeschen (operator, Audit).
- `PUT  /stp/settings`      Schwelle der Topologieaenderungen je 24 h (operator, Audit; ohne Wert
                            gilt die Vorgabe im Code).
Lesen = `viewer`, mandantsgescoped; nicht fuer Kunden-Benutzer (Infrastruktur). Nur lesend
gegenueber den Geraeten - der Soll ist Laufzeitdaten in Automato.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import Site, StpSetting, StpTarget, Tenant
from app.store import cabling, stp, stp_findings

router = APIRouter(prefix="/stp", tags=["stp"])


def _tenant(principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    return tid


def _model(session: Session, tid: int) -> stp.Model:
    return stp.Model(session, tid)


@router.get("/overview")
def overview(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Kennzahlen, Switches und Soll des Mandanten."""
    tid = _tenant(principal, tenant_id)
    m = _model(session, tid)
    limit = stp.tc_per_day(session, tid)
    return {
        "summary": stp.summary(m, limit),
        "devices": [m.device_out(d) for d in m.devices],
        "sites": stp.site_choices(session, tid),
        "targets": [m.target_out(t) for t in m.targets],
        "switch_names": sorted({d.device for d in m.devices}),
    }


@router.get("/trees")
def trees(
    tenant_id: int | None = None,
    vlan: int | None = None,
    site_id: int | None = None,
    q: str | None = None,
    only: str | None = None,  # problems: Root nicht am Soll, unerwartet blockiert
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(principal, tenant_id)
    m = _model(session, tid)
    needle = (q or "").strip().casefold()
    out = []
    for tree in m.trees.values():
        if vlan is not None and vlan not in tree.vlans:
            continue
        if site_id is not None and site_id not in m.site_ids(tree):
            continue
        if needle and not any(needle in (x or "").casefold()
                              for x in [tree.label, m.root_label(tree), tree.root_mac,
                                        *(i.device for i in tree.members)]):  # fmt: skip
            continue
        brief = m.tree_brief(tree)
        if only == "problems" and not (
            brief["unexpected"] or brief["target_state"] in ("mismatch", "backup")
        ):
            continue
        out.append(brief)
    return out


@router.get("/tree")
def tree(
    key: str,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    m = _model(session, tid)
    found = m.trees.get(key)
    if found is None:
        raise HTTPException(status_code=404, detail=L("Baum nicht gefunden", "tree not found"))
    return m.tree_out(found)


# -- Soll-Root ------------------------------------------------------------------------------------


class TargetIn(BaseModel):
    tenant_id: int
    id: int | None = None
    site_id: int | None = None
    vlans: str | None = Field(default=None, max_length=500)
    root: str = Field(min_length=1, max_length=200)
    backup: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)


def _audit(session: Session, principal: Principal, action: str, t: StpTarget) -> None:
    audit.record(session, actor=f"user:{principal.subject}", action=f"stp_target.{action}",
                 subject=str(t.id),
                 detail={"tenant_id": t.tenant_id, "site_id": t.site_id, "vlans": t.vlans,
                         "root": t.root, "backup": t.backup})  # fmt: skip


@router.get("/targets")
def list_targets(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(principal, tenant_id)
    m = _model(session, tid)
    return [m.target_out(t) for t in m.targets]


@router.put("/targets")
def set_target(
    body: TargetIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = tenant_scope(principal, body.tenant_id)
    if tid is None or session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    try:
        vlans, _ = stp.vlan_text(body.vlans)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if body.site_id is not None:
        site = session.get(Site, body.site_id)
        if site is None or site.tenant_id != tid:
            raise HTTPException(status_code=422, detail=L("Standort wählen", "choose a site"))
    root, backup = body.root.strip(), (body.backup or "").strip() or None
    if not root:
        raise HTTPException(status_code=422, detail=L("Root-Switch wählen", "choose a root switch"))
    if backup and cabling.device_key(backup) == cabling.device_key(root):
        same = L("Ersatz-Root ist derselbe Switch", "backup root is the same switch")
        raise HTTPException(status_code=422, detail=same)
    if body.id is not None:
        t = session.get(StpTarget, body.id)
        if t is None or t.tenant_id != tid:
            raise HTTPException(status_code=404, detail="target not found")
        action = "changed"
    else:  # gleicher Standort und gleiche VLANs: derselbe Eintrag
        rows = session.scalars(select(StpTarget).where(StpTarget.tenant_id == tid))
        t = next((x for x in rows if x.site_id == body.site_id and (x.vlans or None) == vlans),
                 None)  # fmt: skip
        action = "changed" if t is not None else "created"
        if t is None:
            t = StpTarget(tenant_id=tid)
            session.add(t)
    t.site_id, t.vlans, t.root, t.backup = body.site_id, vlans, root, backup
    t.note = (body.note or "").strip() or None
    t.changed_by, t.changed_at = f"user:{principal.subject}", datetime.now(UTC)
    session.flush()
    _audit(session, principal, action, t)
    stp_findings.refresh(session, tid)
    m = _model(session, tid)
    known = {cabling.device_key(d.device) for d in m.devices}
    return {**m.target_out(t), "root_known": cabling.device_key(root) in known,
            "backup_known": backup is None or cabling.device_key(backup) in known}  # fmt: skip


@router.delete("/targets/{target_id}", status_code=204)
def delete_target(
    target_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    t = session.get(StpTarget, target_id)
    if t is None or tenant_scope(principal, t.tenant_id) != t.tenant_id:
        raise HTTPException(status_code=404, detail="target not found")
    tid = t.tenant_id
    _audit(session, principal, "deleted", t)
    session.delete(t)
    session.flush()
    stp_findings.refresh(session, tid)
    return Response(status_code=204)


# -- Einstellungen ------------------------------------------------------------------------------


class SettingsIn(BaseModel):
    tenant_id: int
    tc_per_day: int | None = Field(default=None, ge=1, le=stp.TC_PER_DAY_MAX)  # None = Vorgabe


@router.put("/settings")
def set_settings(
    body: SettingsIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = tenant_scope(principal, body.tenant_id)
    if tid is None or session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    row = session.get(StpSetting, tid)
    if body.tc_per_day is None or body.tc_per_day == stp.TC_PER_DAY:
        if row is not None:
            session.delete(row)  # Vorgabe im Code
    else:
        if row is None:
            row = StpSetting(tenant_id=tid, tc_per_day=body.tc_per_day)
            session.add(row)
        row.tc_per_day = body.tc_per_day
        row.changed_by, row.changed_at = f"user:{principal.subject}", datetime.now(UTC)
    session.flush()
    value = stp.tc_per_day(session, tid)
    audit.record(session, actor=f"user:{principal.subject}", action="stp_settings.changed",
                 subject=str(tid), detail={"tenant_id": tid, "tc_per_day": value})  # fmt: skip
    stp_findings.refresh(session, tid)
    return {"tc_per_day": value, "tc_default": stp.TC_PER_DAY}
