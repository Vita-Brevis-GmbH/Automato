"""Freigaben aus dem Cockpit (1.11.0, ADR 0054): `PUT /freigaben`.

Nur mit dem Token des Cockpits (`AUTOMATO_COCKPIT_TOKEN`, Bearer, `auth/cockpit.py`) - wie das
Inventar (ADR 0053): ohne eingerichtetes Token 404, mit falschem 401, nie mit der Anmeldung einer
Person. Das Cockpit schickt den ganzen Stand seiner Kunden; Automato fuehrt damit sein
Kunden-Portal nach (`store/freigaben.py`), schreibt danach den Soll-Stand fuer den Front-Proxy
(`soll.json`, nur bei neuem Inhalt) und meldet, ob der Host-Agent ihn schon uebernommen hat.

Seit 1.13.0 (ADR 0055) optional mit den versiegelten Client-Secrets des Anmelde-Tors
(`tor_geheimnisse`); die Kennung des Empfangsschluessels kommt aus dem Front-Status (wie das
Inventar), die Antwort nennt uebernommene und abgelehnte Realms.

Seit 1.14.0 (ADR 0059) optional `entfernen` (Loeschauftraege nach der Frist des Cockpits,
`store/abbau.py`): geprueft vor den Kunden (alles oder nichts), angenommen bzw. abgelehnt danach;
die Antwort nennt beides (`entfernen`, null ohne das Feld). Ebenfalls seit 1.14.0 (ADR 0058) mit
`zuordnung.kunde_anlegen` und `benutzer` je Kunde; der Soll-Stand nennt `verwaltung: cockpit`.
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
from app.store import abbau, freigaben, inventory, verwaltung

router = APIRouter(prefix="/freigaben", tags=["freigaben"])


def _soll_stand(session: Session, settings: Settings) -> str | None:
    """Soll-Stand nur bei neuem Inhalt schreiben (zweimal dieselbe Anfrage = keine neue Datei,
    der Host-Agent rechnet nicht neu); Fehler als Hinweis - die Daten sind gespeichert."""
    try:  # 1.14.0 (ADR 0058): mit `verwaltung: cockpit` - von Hand nur noch mit --trotzdem
        verwaltung.soll_schreiben(session, settings, only_if_changed=True)
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
    fehler: list[dict] = []
    ergebnis = None
    try:  # 1.14.0 (ADR 0059): Loeschauftraege pruefen und den Stand vor dem Anwenden merken
        plan = abbau.vorbereiten(session, data)
    except freigaben.FreigabenFehler as exc:
        fehler, plan = exc.fehler, None
    try:
        ergebnis = freigaben.uebernehmen(
            session, data, empfang=inventory.empfang_kennung(settings.front_state_dir)
        )
    except freigaben.FreigabenFehler as exc:
        fehler = exc.fehler + fehler
    if fehler or plan is None or ergebnis is None:
        session.rollback()
        return JSONResponse(
            status_code=422, content={"vertrag": freigaben.VERTRAG, "fehler": fehler}
        )
    abbau_erg = abbau.anwenden(session, plan, settings)
    if ergebnis.etwas_geaendert or abbau_erg.geaendert:
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
    return {**ergebnis.antwort(), "entfernen": abbau_erg.antwort(),
            "front": freigaben.front(settings.front_state_dir, hinweis)}  # fmt: skip
