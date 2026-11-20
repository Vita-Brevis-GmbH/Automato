"""Plaene je Netzwerkschicht (1.23.0, ADR 0067 Nachtrag 1.23.0): L1 Physik, L2 Switching und
L3 Routing fuer den ganzen Mandanten (`store/layer_plans`), Filter nach Standort.

- `GET /network-plans/{layer}`         Plan (`l1`, `l2`, `l3`) mit Lage, Ebenen, Grenzen und
                                        den Standorten zur Auswahl; `site` = Standort (0 = ohne
                                        Standort, ohne Angabe alle).
- `GET /network-plans/{layer}/export`  draw.io wie in der Ansicht; `hide` = Ebenen, die in der
                                        Datei ausgeblendet kommen. Dateiname mit Schicht und
                                        Standort.

Lesen = `viewer`, mandantsgescoped; Mandanten-Benutzer mit `mandant.read` (ADR 0068); nicht fuer
Kunden-Benutzer (Infrastruktur, wie Verkabelung und Routing). Rein lesend.
"""

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session

from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import Site, Tenant
from app.store import layer_plan_export, layer_plans

router = APIRouter(prefix="/network-plans", tags=["network-plans"])
LAYER = Path(pattern="^l[123]$", description="l1 Physik, l2 Switching, l3 Routing")


def _tenant(session: Session, principal: Principal, tenant_id: int | None, layer: str) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing" if layer == "l3" else "cabling")
    return tid


def _site(session: Session, tid: int, site: int | None) -> Site | None:
    if site is None or site == layer_plans.NO_SITE:
        return None
    found = session.get(Site, site)
    if found is None or found.tenant_id != tid:
        raise HTTPException(status_code=404, detail=L("Standort unbekannt", "Unknown site"))
    return found


@router.get("/{layer}")
def plan(
    layer: str = LAYER,
    tenant_id: int | None = None,
    site: int | None = Query(None, ge=0, description="Standort; 0 = ohne Standort"),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(session, principal, tenant_id, layer)
    _site(session, tid, site)
    return layer_plans.build(session, tid, layer, site)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-") or "x"


@router.get("/{layer}/export")
def export(
    layer: str = LAYER,
    format: str = Query("drawio", pattern="^drawio$"),  # noqa: A002 - Name der Abfrage
    tenant_id: int | None = None,
    site: int | None = Query(None, ge=0),
    hide: str | None = Query(None, max_length=200, description="Ebenen, kommagetrennt"),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Plan als draw.io-Datei (wie in der Ansicht, Ebenen als Ebenen in draw.io)."""
    tid = _tenant(session, principal, tenant_id, layer)
    found = _site(session, tid, site)
    data = layer_plans.build(session, tid, layer, site)
    if found is not None:
        site_text, site_slug = f"{found.code} · {found.name}", _slug(found.code)
    elif site == layer_plans.NO_SITE:
        site_text = L("ohne Standort", "without site")
        site_slug = L("ohne-standort", "no-site")
    else:
        site_text, site_slug = L("alle", "all"), L("alle", "all")
    tenant = session.get(Tenant, tid)
    title = data["title"] + (f" – {tenant.name}" if tenant else "")
    when = datetime.now(UTC)
    hidden = {h.strip() for h in (hide or "").split(",") if h.strip()}
    body = layer_plan_export.drawio(data, title, site_text, when, hidden)
    word = {"l1": L("physik", "physical"), "l2": "switching", "l3": "routing"}[layer]
    name = f"plan-{layer}-{word}-{site_slug}-{when:%Y-%m-%d}.drawio"
    return Response(
        content=body,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
