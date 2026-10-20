"""Referenz-Ebene (IPAM-Stand der Referenzquellen, ADR 0010) - Platzhalter bis A3."""

from sqlalchemy.orm import Session


def checks(session: Session, tenant_id: int, prefixes) -> list:
    """Referenz-Pruefungen fuer den Abgleich (folgen mit den Referenzquellen)."""
    return []


def describe(session: Session, objects: list) -> dict[int, tuple[dict | None, dict | None]]:
    """Referenz- und Anreicherungsdaten je Werte-Objekt (folgen mit den Referenzquellen)."""
    return {}
