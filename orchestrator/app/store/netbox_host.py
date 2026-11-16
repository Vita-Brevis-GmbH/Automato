"""Kunden fuer den NetBox-Host (1.11.0, ADR 0054): `GET /netbox-host`.

Der Agent auf dem NetBox-Host (`netbox-host.sh sync`, systemd) holt hier die Kunden mit dem
Werkzeug NetBox und legt fuer jeden ohne Instanz eine an. Nur Kurzname und Name, dazu die Domain
der Adressen (der Agent warnt, wenn sie nicht zu seiner passt) - keine Realms, keine Mandanten,
keine Geheimnisse. Entfernt wird auf dem NetBox-Host nie selbst.

Zugang fuer Automato (1.13.0, ADR 0056): je Kunde sagt `zugang`, ob Automato einen Zugang zu
dieser NetBox annimmt (`fehlt`) - nur dann legt der Agent die Benutzer `automato-collector` und
`automato-sync` mit neuen Tokens an und liefert sie mit `PUT /netbox-host/zugang`. Automato
speichert sie wie von Hand eingetragene Zugangsdaten verschluesselt am Quellsystem NetBox des
Mandanten aus dem Kunden-Portal (`app.credentials`). Vorhandene Zugangsdaten bleiben immer: eine
NetBox gilt als angebunden, sobald irgendein Quellsystem NetBox mit ihrem Namen
`<kurzname>-nb.<domain>` Zugangsdaten hat (auch von Hand, auch bei einem anderen Mandanten).
Ein Quellsystem NetBox des Mandanten mit einer anderen Adresse aendert der NetBox-Host nie.

Loeschauftraege (ADR 0059): `loeschen` nennt die NetBox-Instanzen, die das Cockpit nach seiner
Frist abbauen laesst (`store/abbau.py`, je `kurzname` und `auftrag`); der Agent sichert, loescht
und meldet seinen Stand mit `PUT /netbox-host/stand`. Selbst entfernt er weiterhin nie etwas.
"""

import hmac
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, credentials
from app.i18n import L
from app.models import PortalCustomer, ReferenceRole, SourceSystem
from app.models.source_system import Platform
from app.store import abbau

#: Fassung der Antwort (Gegenstueck: NBH_KUNDEN_PY in deploy/netbox-host/netbox-host.sh);
#: `zugang` je Kunde ist seit 1.13.0 ein optionales Feld (aeltere Agenten ignorieren es), ebenso
#: `loeschen` (ADR 0059)
VERTRAG = 1

# Zugang fuer Automato je Kunde (1.13.0, ADR 0056)
FEHLT = "fehlt"  # Automato nimmt einen Zugang an - der Agent legt die Tokens an
VORHANDEN = "vorhanden"  # Quellsystem NetBox dieser NetBox mit Zugangsdaten (Hand oder Agent)
KEIN_MANDANT = "kein_mandant"  # Kunde im Portal noch ohne Mandant
ANDERE_ADRESSE = "andere_adresse"  # Mandant hat ein Quellsystem NetBox mit anderer Adresse
KEINE_ADRESSE = "keine_adresse"  # Domain des Front-Proxys fehlt (AUTOMATO_FRONT_DOMAIN)
# nur als Antwort auf PUT /netbox-host/zugang
UEBERNOMMEN = "uebernommen"
UNVERAENDERT = "unveraendert"  # genau diese Tokens sind schon gespeichert

AKTEUR = "netbox-host"
AKTION = "source.netbox_host_access"  # Audit: nur Kunde, Quellsystem, Herkunft - nie Tokens


def adresse(kurzname: str, domain: str) -> str | None:
    """Adresse der NetBox, wie Automato sie erreicht: ueber den Front-Proxy (1.10.0, ADR 0053)."""
    return f"https://{kurzname}-nb.{domain}" if domain else None


def _host(url: str) -> str:
    url = (url or "").strip()
    if url and "//" not in url:
        url = "//" + url
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _netbox_quellen(session: Session) -> list[SourceSystem]:
    return list(
        session.scalars(
            select(SourceSystem)
            .where(SourceSystem.platform == Platform.NETBOX)
            .order_by(SourceSystem.id)
        )
    )


@dataclass(frozen=True)
class Zugang:
    stand: str
    ziel: SourceSystem | None = None  # bei FEHLT: Quellsystem ohne Zugangsdaten (sonst neu)


def zugang(pc: PortalCustomer, domain: str, quellen: list[SourceSystem]) -> Zugang:
    """Zugang fuer Automato zur NetBox eines Kunden. Erkannt wird die NetBox am Namen
    `<kurzname>-nb.<domain>` (Port und Pfad zaehlen nicht) - so bleibt auch ein von Hand
    eingetragener Weg direkt zum NetBox-Host (Port in der Adresse) unberuehrt."""
    url = adresse(pc.short_name, domain)
    if url is None:
        return Zugang(KEINE_ADRESSE)
    name = _host(url)
    diese = [q for q in quellen if _host(q.base_url) == name]
    if any(q.credentials for q in diese):
        return Zugang(VORHANDEN)
    if pc.tenant_id is None:
        return Zugang(KEIN_MANDANT)
    eigene = [q for q in quellen if q.tenant_id == pc.tenant_id]
    ziel = next((q for q in eigene if _host(q.base_url) == name), None)
    if ziel is None and eigene:
        return Zugang(ANDERE_ADRESSE)
    return Zugang(FEHLT, ziel)


def kunden(session: Session, domain: str) -> dict:
    rows = session.scalars(
        select(PortalCustomer)
        .where(PortalCustomer.nb.is_(True))
        .order_by(PortalCustomer.short_name)
    )
    quellen = _netbox_quellen(session)
    return {
        "vertrag": VERTRAG,
        "domain": domain or None,
        "kunden": [
            {"kurzname": pc.short_name, "name": pc.name,
             "zugang": zugang(pc, domain, quellen).stand}
            for pc in rows
        ],
        # ADR 0059: Loeschauftraege aus dem Cockpit (nur offene, nie fuer einen Portal-Kunden)
        "loeschen": abbau.netbox_loeschen(session),
    }  # fmt: skip


@dataclass(frozen=True)
class Uebergabe:
    status: str
    http: int
    meldung: str

    def antwort(self) -> dict:
        return {"status": self.status, "detail": self.meldung}


def kunde(session: Session, kurzname: str) -> PortalCustomer | None:
    """Kunde mit dem Werkzeug NetBox - andere gibt es fuer den NetBox-Host nicht."""
    return session.scalar(
        select(PortalCustomer).where(
            PortalCustomer.short_name == kurzname, PortalCustomer.nb.is_(True)
        )
    )


def _gleich(q: SourceSystem, lesen: str, sync: str) -> bool:
    try:
        daten = credentials.decrypt(q.credentials or "")
    except credentials.CredentialsUnavailable:
        return False
    token, write = str(daten.get("token", "")), str(daten.get("write_token", ""))
    return hmac.compare_digest(token, lesen) and hmac.compare_digest(write, sync)


def _freier_name(session: Session, tenant_id: int, kurzname: str) -> str | None:
    for name in ("NetBox", f"NetBox {kurzname}", f"NetBox {kurzname}-nb"):
        taken = session.scalar(
            select(SourceSystem.id).where(
                SourceSystem.tenant_id == tenant_id, SourceSystem.name == name
            )
        )
        if taken is None:
            return name
    return None


def _rolle(session: Session, src: SourceSystem) -> str:
    """Rolle einer neuen Referenzquelle wie beim Anlegen in der Oberflaeche ohne Angabe
    (`api/systems._apply_ref_role`): NetBox fuehrend nur ohne andere fuehrende Quelle."""
    andere = session.scalar(
        select(SourceSystem.id).where(
            SourceSystem.tenant_id == src.tenant_id,
            SourceSystem.ref_role == ReferenceRole.PRIMARY.value,
            SourceSystem.id != src.id,
        )
    )
    return ReferenceRole.ENRICHMENT.value if andere is not None else ReferenceRole.PRIMARY.value


def uebernehmen(
    session: Session, pc: PortalCustomer, lesen: str, sync: str, domain: str
) -> Uebergabe:
    """Zugang aus dem NetBox-Host speichern, wenn Automato ihn annimmt (ohne commit).

    Vorhandene Zugangsdaten werden nie ueberschrieben (`vorhanden`; `unveraendert`, wenn genau
    diese Tokens schon gespeichert sind - zweimal dieselbe Anfrage aendert nichts)."""
    quellen = _netbox_quellen(session)
    z = zugang(pc, domain, quellen)
    if z.stand == VORHANDEN:
        name = _host(adresse(pc.short_name, domain) or "")
        if any(q.credentials and _host(q.base_url) == name and _gleich(q, lesen, sync)
               for q in quellen):  # fmt: skip
            return Uebergabe(UNVERAENDERT, 200, L(
                "Zugang schon so gespeichert", "Access already stored like this"))  # fmt: skip
        return Uebergabe(VORHANDEN, 200, L(
            "Automato hat schon einen Zugang zu dieser NetBox – er bleibt (nicht überschrieben)",
            "Automato already has access to this NetBox – it stays (not overwritten)",
        ))  # fmt: skip
    if z.stand == KEIN_MANDANT:
        return Uebergabe(KEIN_MANDANT, 409, L(
            "Kunde im Kunden-Portal noch ohne Mandant – nicht übernommen",
            "Customer has no tenant in the customer portal yet – not taken over",
        ))  # fmt: skip
    if z.stand == ANDERE_ADRESSE:
        return Uebergabe(ANDERE_ADRESSE, 409, L(
            "Der Mandant hat ein Quellsystem NetBox mit einer anderen Adresse – nicht übernommen, "
            "von Hand klären",
            "The tenant has a NetBox source system with another address – not taken over, "
            "clarify by hand",
        ))  # fmt: skip
    if z.stand == KEINE_ADRESSE:
        return Uebergabe(KEINE_ADRESSE, 409, L(
            "Domain des Front-Proxys fehlt (AUTOMATO_FRONT_DOMAIN) – nicht übernommen",
            "Front proxy domain missing (AUTOMATO_FRONT_DOMAIN) – not taken over",
        ))  # fmt: skip
    src = z.ziel
    angelegt = src is None
    if src is None:
        name = _freier_name(session, pc.tenant_id, pc.short_name)
        if name is None:
            return Uebergabe(ANDERE_ADRESSE, 409, L(
                "Kein freier Name für das Quellsystem NetBox – von Hand anlegen",
                "No free name for the NetBox source system – create it by hand",
            ))  # fmt: skip
        src = SourceSystem(
            tenant_id=pc.tenant_id,
            name=name,
            platform=Platform.NETBOX,
            base_url=adresse(pc.short_name, domain),
            verify_tls=True,
        )
        session.add(src)
        session.flush()
        src.ref_role = _rolle(session, src)
    # wie von Hand (`api/systems._apply_credentials`): Lese-Token und Schreib-Token des Syncs
    src.credentials = credentials.encrypt({"token": lesen, "write_token": sync})
    session.flush()
    audit.record(
        session,
        actor=AKTEUR,
        action=AKTION,
        subject=str(src.id),
        detail={"kunde": pc.short_name, "source_id": src.id, "tenant_id": src.tenant_id,
                "name": src.name, "angelegt": angelegt, "quelle": AKTEUR},
    )  # fmt: skip
    return Uebergabe(UEBERNOMMEN, 200, L(
        "Zugang übernommen (Quellsystem NetBox beim Mandanten des Kunden)",
        "Access taken over (NetBox source system at the customer's tenant)",
    ))  # fmt: skip
