"""Freigaben aus dem Cockpit (1.11.0, ADR 0054): `PUT /freigaben`.

Nur mit dem Token des Cockpits (`AUTOMATO_COCKPIT_TOKEN`, Bearer, `auth/cockpit.py`) - wie das
Inventar (ADR 0053): ohne eingerichtetes Token 404, mit falschem 401, nie mit der Anmeldung einer
Person. Das Cockpit schickt den ganzen Stand seiner Kunden; Automato fuehrt damit sein
Kunden-Portal nach (`store/freigaben.py`), schreibt danach den Soll-Stand fuer den Front-Proxy
(`soll.json`, nur bei neuem Inhalt) und meldet, ob der Host-Agent ihn schon uebernommen hat.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import realms
from app.auth.cockpit import require_cockpit_token
from app.config import Settings, get_settings
from app.db import get_session
from app.i18n import L
from app.store import freigaben, portal

router = APIRouter(prefix="/freigaben", tags=["freigaben"])


def _soll_stand(session: Session, settings: Settings) -> str | None:
    """Soll-Stand nur bei neuem Inhalt schreiben (zweimal dieselbe Anfrage = keine neue Datei,
    der Host-Agent rechnet nicht neu); Fehler als Hinweis - die Daten sind gespeichert."""
    try:
        portal.write_desired(session, settings.front_state_dir, only_if_changed=True)
    except OSError as exc:
        return L(f"Gespeichert, aber Soll-Stand nicht geschrieben ({settings.front_state_dir}): "
                 f"{exc} – auf der VM: sudo deploy/front/front.sh agent",
                 f"Saved, but the desired state was not written ({settings.front_state_dir}): "
                 f"{exc} – on the VM: sudo deploy/front/front.sh agent")  # fmt: skip
    return None


@router.put("", dependencies=[Depends(require_cockpit_token)])
def put_freigaben(
    data: Annotated[Any, Body()] = None,
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    try:
        ergebnis = freigaben.uebernehmen(session, data)
    except freigaben.FreigabenFehler as exc:
        session.rollback()
        return JSONResponse(
            status_code=422, content={"vertrag": freigaben.VERTRAG, "fehler": exc.fehler}
        )
    if ergebnis.etwas_geaendert:
        try:
            session.commit()
        except IntegrityError as exc:  # gleichzeitig in der Oberflaeche geaendert
            session.rollback()
            raise HTTPException(409, L(
                "Kunden-Portal gleichzeitig geändert – Freigaben erneut senden",
                "Customer portal changed at the same time – send the releases again",
            )) from exc  # fmt: skip
        realms.invalidate()
    hinweis = _soll_stand(session, settings)
    return {**ergebnis.antwort(), "front": freigaben.front(settings.front_state_dir, hinweis)}
