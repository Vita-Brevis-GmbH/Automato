"""Kopplung mit dem Cockpit ueber einen Einmal-Code (1.13.0, ADR 0057).

- `POST /kopplung` (ohne andere Anmeldung): das Cockpit loest den Einmal-Code ein und bekommt
  **einmal** ein neues Token fuer `GET /inventory` und `PUT /freigaben`. Gleiche Antwort (401)
  fuer „kein Code offen“, falsch, abgelaufen, schon benutzt oder gesperrt; gedrosselt
  (`DROSSEL`, 429). Vertrag fuer die Gegenseite: `docs/vertraege/kopplung.md` im Cockpit.
- `GET /kopplung/cockpit`, `POST /kopplung/cockpit/code`, `DELETE /kopplung/cockpit/code`,
  `DELETE /kopplung/cockpit` (nur Admin): Stand, Code erzeugen (einmal angezeigt), Code
  verwerfen, entkoppeln.

Code und Token stehen nie im Audit, im Log oder in einer anderen Antwort (`store/kopplung.py`).
"""

import threading
import time
from collections import deque
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.db import get_session
from app.i18n import L
from app.store import inventory, kopplung

router = APIRouter(prefix="/kopplung", tags=["kopplung"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class Drossel:
    """Hoechstens `anzahl` Versuche je `fenster` Sekunden - ueber alle Absender (hinter dem Proxy
    ist die Adresse des Absenders nicht verlaesslich); je Orchestrator-Prozess. Begrenzt das
    Raten zusaetzlich zu den Fehlversuchen je Code und schont Datenbank und Audit."""

    def __init__(self, anzahl: int = 10, fenster: float = 60.0) -> None:
        self.anzahl, self.fenster = anzahl, fenster
        self._zeiten: deque[float] = deque()
        self._lock = threading.Lock()

    def warten(self, jetzt: float | None = None) -> int:
        """0 = Versuch zaehlt und darf; sonst Sekunden bis zum naechsten freien Versuch."""
        jetzt = time.monotonic() if jetzt is None else jetzt
        with self._lock:
            while self._zeiten and jetzt - self._zeiten[0] >= self.fenster:
                self._zeiten.popleft()
            if len(self._zeiten) >= self.anzahl:
                return max(1, int(self.fenster - (jetzt - self._zeiten[0])) + 1)
            self._zeiten.append(jetzt)
            return 0

    def reset(self) -> None:
        with self._lock:
            self._zeiten.clear()


DROSSEL = Drossel()


class CockpitIn(BaseModel):
    name: str = Field(min_length=1, max_length=kopplung.NAME_LAENGE)


class EinloesenIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # neue Felder einer neueren Gegenseite

    vertrag: int = kopplung.VERTRAG
    code: str = Field(min_length=1, max_length=64)
    cockpit: CockpitIn


def _abgelehnt() -> HTTPException:
    return HTTPException(
        401, L("Code ungültig oder abgelaufen – im Automato einen neuen erzeugen",
               "Code invalid or expired – create a new one in Automato"))  # fmt: skip


@router.post("")
def einloesen(
    data: EinloesenIn,
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    if data.vertrag != kopplung.VERTRAG:
        raise HTTPException(422, L(f"Vertrag {data.vertrag} unbekannt (erwartet 1)",
                                   f"Contract {data.vertrag} unknown (expected 1)"))  # fmt: skip
    name = kopplung.check_name(data.cockpit.name)
    if name is None:
        raise HTTPException(422, L("Name des Cockpits: 1–100 druckbare Zeichen",
                                   "Cockpit name: 1–100 printable characters"))  # fmt: skip
    warten = DROSSEL.warten()
    if warten:
        raise HTTPException(
            429, L(f"Zu viele Versuche – in {warten} s erneut",
                   f"Too many attempts – retry in {warten} s"),
            headers={"Retry-After": str(warten)},
        )  # fmt: skip
    try:
        token, replaced = kopplung.redeem(session, kopplung.COCKPIT, data.code, name)
    except kopplung.Abgelehnt:
        session.commit()  # Fehlversuch bzw. abgelaufener Code zaehlt
        raise _abgelehnt() from None
    session.commit()
    body = {
        "vertrag": kopplung.VERTRAG,
        "token": token,
        "ersetzt": replaced,
        "automato": inventory.installation(settings),
        "endpunkte": {"inventar": "/api/inventory", "freigaben": "/api/freigaben"},
    }
    return JSONResponse(body, headers=_NO_STORE)


@router.get("/cockpit")
def cockpit_status(
    _: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {**kopplung.status(session, kopplung.COCKPIT),
            "env_token": bool(settings.cockpit_token)}  # fmt: skip


@router.post("/cockpit/code", status_code=201)
def cockpit_code(
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    code, expires = kopplung.create_code(session, kopplung.COCKPIT, principal.subject)
    session.commit()
    body = {
        "code": code,
        "expires": expires.astimezone(UTC).isoformat(),
        "minutes": kopplung.CODE_MINUTEN,
        "max_failures": kopplung.MAX_FEHLVERSUCHE,
    }
    return JSONResponse(body, status_code=201, headers=_NO_STORE)


@router.delete("/cockpit/code")
def cockpit_code_withdraw(
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    kopplung.withdraw_code(session, kopplung.COCKPIT, principal.subject)
    session.commit()
    return cockpit_status(principal, session, settings)


@router.delete("/cockpit")
def cockpit_uncouple(
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    kopplung.uncouple(session, kopplung.COCKPIT, principal.subject)
    session.commit()
    return cockpit_status(principal, session, settings)
