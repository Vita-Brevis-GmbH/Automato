from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.db import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session, scope="function")) -> dict:
    try:
        session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:  # noqa: BLE001 - Health darf nie eine Exception durchreichen
        db_status = "error"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "version": __version__,
    }
