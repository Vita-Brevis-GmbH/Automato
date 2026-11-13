"""Kunden-Portal (1.3.0, ADR 0050): Kunden des Front-Proxys und ihre Anmeldung.

Ein Eintrag beschreibt einen Kunden der Plattform: Kurzname (Adressen `<kurzname>.<domain>`,
`<kurzname>-nb`, `-doc`, `-um`, `-cmdb`), Anzeigename, freigeschaltete Werkzeuge und weitere
Kacheln der Startseite. Der Orchestrator schreibt daraus den Soll-Stand, den der Host-Agent auf
der VM uebernimmt (`front.sh sync`) - die Oberflaeche aendert nie direkt etwas auf dem Host.

Anmeldung je Kunde: `realm` = eigener Keycloak-Realm des Kunden (z. B. mit Azure/Entra oder
LDAPS). Benutzer aus diesem Realm sind immer Kunden-Benutzer - Mandant und Kunden kommen aus
`tenant_id`/`customer_ids` dieses Eintrags, nie aus dem Token (`auth/oidc.py`). Ohne Realm bzw.
mit dem Realm von Automato selbst gilt die bisherige Anmeldung.

Zugriff je Kunde (1.5.0): `access` = von wo die Adressen des Kunden erreichbar sind – `ueberall`
(Vorgabe, über WAF/FortiGate aus dem Internet), `intern` (nur Management-Netz) oder `netze` (nur
aus `access_nets`). Der Front-Proxy antwortet allen anderen wie bei einer unbekannten Adresse.

Herkunft (1.11.0, ADR 0054): `source` = `oberflaeche` (in Automato angelegt) oder `cockpit` (vom
Kundenregister des Cockpits mit `PUT /freigaben`). Bei Kunden aus dem Cockpit sind Kurzname, Name,
Werkzeuge, Zugriff und Realm nur dort aenderbar; Mandant, Kunden, Rolle und Kacheln pflegt
Automato. `nb` = NetBox (`-nb`) ist seit 1.11.0 ein waehlbares Werkzeug (Vorgabe an).
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalCustomer(Base):
    __tablename__ = "portal_customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_name: Mapped[str] = mapped_column(String(42), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    doc: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # NetBox (-nb) als Werkzeug (1.11.0): Vorgabe an - bestehende Kunden behalten ihre NetBox
    nb: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    um: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    cmdb: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # weitere Kacheln: [{"title", "url", "text"}]
    links: Mapped[list] = mapped_column(JSON, default=list)
    realm: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL"), nullable=True, index=True
    )
    customer_ids: Mapped[list] = mapped_column(JSON, default=list)
    # Kunden-Rolle fuer Benutzer des Realms ohne eigene Rolle im Token (viewer, triage, …)
    default_role: Mapped[str] = mapped_column(String(16), default="viewer")
    access: Mapped[str] = mapped_column(String(16), default="ueberall", server_default="ueberall")
    access_nets: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Herkunft (1.11.0, ADR 0054): oberflaeche | cockpit
    source: Mapped[str] = mapped_column(
        String(16), default="oberflaeche", server_default="oberflaeche"
    )
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
