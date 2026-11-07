"""Datenqualitaet (ADR 0029): `GET /data-quality` - wo Doku, Weg-Suche, Zonen und Kunden
unsicher sind, weil Daten fehlen, veraltet oder widerspruechlich sind; je Bereich Ampel,
Kennzahl, Ursachen und Links in die Ansichten (`store/data_quality.py`).

Lesen = `viewer`, mandantsgescoped, mit `customer_id` der Ausschnitt eines Kunden (Regeln, VMs,
Netze, Befunde; Quellsysteme und IPAM sind geteilt). Nicht fuer Kunden-Benutzer (nicht in der
Freigabeliste: zeigt Quellsysteme, Laeufe und die ganze Umgebung). Nur lesend.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.customer_filter import customer_view
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.config import get_settings
from app.db import get_session
from app.store import data_quality

router = APIRouter(prefix="/data-quality", tags=["data-quality"])


@router.get("")
def report(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    view = customer_view(session, principal, tid, customer_id)
    return data_quality.report(session, tid, view, get_settings())
