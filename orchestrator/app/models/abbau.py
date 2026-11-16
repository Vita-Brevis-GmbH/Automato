"""Abbau und Ist-Stand (ADR 0059): Loeschauftraege aus dem Cockpit und Meldungen der Werkzeuge.

`AbbauAuftrag` - ein Kunde aus dem Cockpit, den das Cockpit nach Ablauf seiner Frist entfernen
laesst (`PUT /freigaben`, `entfernen`): je Teil (NetBox, Kunde in Automato, Anmelde-Tor), ob er
gewuenscht ist und wie weit er ist. Ein Auftrag gilt nur fuer diesen einen Abbau (`kennung`,
zufaellig): Front-Proxy und NetBox-Host merken sich, welche Auftraege sie schon ausgefuehrt haben,
und loeschen nie zweimal - auch nicht etwas, das danach wieder angelegt wurde.

`IstMeldung` - letzte Meldung eines Werkzeugs zu seinem Ist-Stand (heute nur der Agent des
NetBox-Hosts, `PUT /netbox-host/stand`): nur Kurznamen, Zeit und Ergebnis der Loeschauftraege.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AbbauAuftrag(Base):
    __tablename__ = "abbau_auftrag"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_name: Mapped[str] = mapped_column(String(42), unique=True, index=True)
    # je Auftrag zufaellig (32 hex): erledigte Auftraege merken sich Front-Proxy und NetBox-Host
    kennung: Mapped[str] = mapped_column(String(32))
    # Realm des Kunden beim Entfernen (Tor-Geheimnis auf der VM); None = ohne Realm
    realm: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # gewuenschte Teile (das Cockpit schickt sie mit jedem Abgleich, der letzte Stand gilt)
    netbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    automato_kunde: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    tor: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # beim Annehmen gemerkt: Kunden in Automato, die Automato fuer diesen Portal-Kunden anlegte
    automato_kunden: Mapped[list] = mapped_column(JSON, default=list)
    # je Teil {"stand": offen|erledigt, "grund": str|None, "zeit": ISO}; erledigt bleibt erledigt
    teile: Mapped[dict] = mapped_column(JSON, default=dict)
    angenommen_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    erledigt_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IstMeldung(Base):
    __tablename__ = "ist_meldung"

    id: Mapped[int] = mapped_column(primary_key=True)
    quelle: Mapped[str] = mapped_column(String(32), unique=True)  # netbox_host
    daten: Mapped[dict] = mapped_column(JSON, default=dict)
    empfangen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
