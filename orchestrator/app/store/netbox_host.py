"""Kunden fuer den NetBox-Host (1.11.0, ADR 0054): `GET /netbox-host`.

Der Agent auf dem NetBox-Host (`netbox-host.sh sync`, systemd) holt hier die Kunden mit dem
Werkzeug NetBox und legt fuer jeden ohne Instanz eine an. Nur Kurzname und Name, dazu die Domain
der Adressen (der Agent warnt, wenn sie nicht zu seiner passt) - keine Realms, keine Mandanten,
keine Geheimnisse. Entfernt wird auf dem NetBox-Host nie selbst.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PortalCustomer

#: Fassung der Antwort (Gegenstueck: NBH_KUNDEN_PY in deploy/netbox-host/netbox-host.sh)
VERTRAG = 1


def kunden(session: Session, domain: str) -> dict:
    rows = session.scalars(
        select(PortalCustomer)
        .where(PortalCustomer.nb.is_(True))
        .order_by(PortalCustomer.short_name)
    )
    return {
        "vertrag": VERTRAG,
        "domain": domain or None,
        "kunden": [{"kurzname": pc.short_name, "name": pc.name} for pc in rows],
    }
