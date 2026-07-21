"""Regel-Endpunkte - Platzhalter.

Liefert erst Daten, wenn das Datenmodell (M5) steht und Collectors Regeln
persistieren. Der Router existiert bereits, damit Web-UI und API-Vertrag
frueh andocken koennen.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("")
def list_rules() -> list:
    return []
