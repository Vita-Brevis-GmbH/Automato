"""Neuer Mandant je Portal-Kunde aus dem Cockpit (1.19.2, ADR 0058 Nachtrag).

Befund des Anwenders 2026-11-18: in der gemeinsamen Instanz gab es nur einen Mandanten, und jeder
neue Kunde aus dem Cockpit landete darin („alle zusätzlich erstellten werden dann dem ersten
zugewiesen“) - „Es sollte gemäss mir einen neuen Mandanten angelegt und mit dem Account verknüpft
werden, sowie die anderen Mandanten nicht sichtbar machen.“

- **Wunsch des Cockpits:** `zuordnung.mandant_anlegen: true` (statt `mandant`), optional
  `mandant_name` (Vorgabe: Name des Portal-Kunden). Automato legt fuer den Portal-Kunden einen
  eigenen Mandanten an, darin wie bei `kunde_anlegen` einen Kunden mit dem Namen des Portal-Kunden,
  und ordnet beides zu. Benutzer des Kunden-Realms bekommen Mandant, Kunden und Rolle wie bisher nur
  aus dem Kunden-Portal (`auth/oidc.py`) - andere Mandanten sehen sie nicht.
- **Idempotent je Kurzname:** `cockpit_tenant` haelt Portal-Kunde -> Mandant fest; ein zweites
  `PUT /freigaben` nimmt denselben. Der Name gilt nur beim Anlegen (spaeter nie umbenannt).
- **Name belegt:** Mandanten-Namen sind eindeutig; gibt es den Namen schon, heisst der neue
  „<Name> (<Kurzname>)“ - ein bestehender Mandant wird nie still uebernommen (sonst saehen die
  Benutzer eines Kunden die Daten eines anderen). Ist auch das belegt: Fehler, die Anfrage aendert
  nichts (alles oder nichts).
- **Leer:** der neue Mandant hat keine Quellsysteme, Standorte oder Module-Abweichungen - die
  gehoeren dem Mandanten und kommen beim Umzug nicht mit.
- **Umzug:** stand der Portal-Kunde vorher in einem anderen Mandanten X, entfernt Automato den
  Kunden, den es dort fuer ihn angelegt hat (`kunde_anlegen`), nur ohne Daten und ohne andere
  Zuordnung (`abbau.automato_kunde_entfernen`, ADR 0059: Kunden in Automato nur ohne Daten) - sonst
  bleibt er und wird gemeldet (Antwort `umzug`, Audit `portal.customer.umzug`, im Inventar unter
  `ist.automato_kunden`). Nur einmal, beim Wechsel: spaeter entfernt Automato ihn nicht still.
  Von Hand gewaehlte Kunden in X bleiben immer.
- **Nie automatisch geloescht:** einen so angelegten Mandanten entfernt Automato nie selbst; der
  Abbau aus dem Cockpit (ADR 0059) kennt keinen Teil fuer Mandanten. Er entfernt nur den Kunden
  darin (Teil `automato_kunde`, ohne Daten) - den leeren Mandanten loescht ein Admin unter
  Administration -> Mandanten (das Inventar nennt ihn mit `portal_kunde`).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.i18n import L
from app.models import CockpitCustomer, CockpitTenant, Customer, Tenant
from app.models.tenant import DnsType, IpamType

#: Akteur im Audit (wie `store/verwaltung.py`)
AKTEUR = "cockpit"
#: Laenge des Mandanten-Namens (`tenant.name`)
NAME_MAX = 200
#: Faehigkeit im Inventar (`automato.funktionen`): das Cockpit sendet `mandant_anlegen` nur dann
FUNKTION = "mandant_anlegen"
#: Gruende, aus denen der frueher angelegte Kunde beim Umzug bleibt (wie der Abbau, ADR 0059)
ENTFERNT, BLEIBT = "entfernt", "bleibt"


def angelegt_je_kunde(session: Session, short_names: Iterable[str] | None = None,
                      ) -> dict[str, int]:  # fmt: skip
    """Kurzname -> Mandant, den Automato fuer diesen Portal-Kunden angelegt hat."""
    stmt = select(CockpitTenant.short_name, CockpitTenant.tenant_id)
    if short_names is not None:
        stmt = stmt.where(CockpitTenant.short_name.in_(list(short_names)))
    return {kurz: tid for kurz, tid in session.execute(stmt)}


def angelegt(session: Session, short_name: str) -> int | None:
    """Mandant, den Automato fuer diesen Portal-Kunden angelegt hat (oder None)."""
    return angelegt_je_kunde(session, [short_name]).get(short_name)


def portal_kunden(session: Session) -> dict[int, str]:
    """Mandant -> Kurzname des Portal-Kunden, fuer den Automato ihn angelegt hat."""
    return {tid: kurz for kurz, tid in angelegt_je_kunde(session).items()}


def mandanten(session: Session, short_names: Iterable[str] | None = None,
              ) -> dict[str, dict]:  # fmt: skip
    """Kurzname -> {"id", "name"} des fuer ihn angelegten Mandanten (Inventar, Kunden-Portal)."""
    je_kunde = angelegt_je_kunde(session, short_names)
    if not je_kunde:
        return {}
    namen = {tid: name for tid, name in session.execute(
        select(Tenant.id, Tenant.name).where(Tenant.id.in_(list(je_kunde.values())))
    )}  # fmt: skip
    return {kurz: {"id": tid, "name": (namen.get(tid) or "")[:NAME_MAX]}
            for kurz, tid in je_kunde.items() if tid in namen}  # fmt: skip


@dataclass(frozen=True)
class Anlegen:
    """Plan fuer einen Portal-Kunden mit `mandant_anlegen`: Name des neuen Mandanten."""

    kurzname: str
    name: str


def planen(session: Session, auftraege: list[tuple[str, str]],
           ) -> tuple[list[Anlegen], list[tuple[str, str]]]:  # fmt: skip
    """Je (Kurzname, gewuenschter Name) ohne angelegten Mandanten: freier Name - der gewuenschte
    bzw. „<Name> (<Kurzname>)“; sind beide belegt: Fehler (Kurzname, Grund)."""
    if not auftraege:
        return [], []
    belegt = set(session.scalars(select(Tenant.name)))
    plan: list[Anlegen] = []
    fehler: list[tuple[str, str]] = []
    for kurz, name in auftraege:
        zusatz = f" ({kurz})"
        kandidaten = (name[:NAME_MAX], f"{name[: NAME_MAX - len(zusatz)].rstrip()}{zusatz}")
        frei = next((n for n in kandidaten if n not in belegt), None)
        if frei is None:
            fehler.append((kurz, L(
                f"zuordnung.mandant_anlegen: Mandanten '{kandidaten[0]}' und '{kandidaten[1]}' "
                "gibt es schon – einen anderen Namen (mandant_name) senden",
                f"zuordnung.mandant_anlegen: tenants '{kandidaten[0]}' and '{kandidaten[1]}' "
                "already exist – send another name (mandant_name)",
            )))  # fmt: skip
            continue
        belegt.add(frei)
        plan.append(Anlegen(kurz, frei))
    return plan, fehler


def anlegen(session: Session, p: Anlegen, akteur: str = AKTEUR) -> int:
    """Geplanten Mandanten anlegen (ohne commit): Vorgaben wie in der Oberflaeche (NetBox,
    Active Directory), Verknuepfung `cockpit_tenant`, Audit `tenant.created` mit Akteur `cockpit`
    und dem Portal-Kunden."""
    t = Tenant(name=p.name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    session.add(CockpitTenant(short_name=p.kurzname, tenant_id=t.id, created_at=datetime.now(UTC)))
    audit.record(session, actor=akteur, action="tenant.created", subject=str(t.id),
                 detail={"tenant_id": t.id, "name": t.name, "code": None, "quelle": "cockpit",
                         "portal_kunde": p.kurzname})  # fmt: skip
    session.flush()
    return t.id


def umziehen(session: Session, kurzname: str, alt: int, neu: int,
             akteur: str = AKTEUR) -> list[dict]:  # fmt: skip
    """Der Portal-Kunde ist von Mandant `alt` in seinen neuen Mandanten `neu` gezogen (nach dem
    Anwenden der Freigaben, ohne commit): die Kunden, die Automato in `alt` fuer ihn angelegt hat,
    entfernen - nur ohne Daten und ohne andere Zuordnung, sonst bleiben sie. Liefert je Kunde
    {"kurzname", "kunde", "name", "mandant", "stand", "grund"}; Audit `portal.customer.umzug`."""
    from app.store import abbau  # spaet: abbau nutzt store/freigaben, das dieses Modul nutzt

    ids = list(session.scalars(select(CockpitCustomer.customer_id).where(
        CockpitCustomer.short_name == kurzname, CockpitCustomer.tenant_id == alt)))  # fmt: skip
    kunden = list(session.scalars(select(Customer).where(Customer.id.in_(ids))
                                  .order_by(Customer.id))) if ids else []  # fmt: skip
    if not kunden:  # nichts angelegt: der Wechsel selbst steht im Audit `portal.customer.updated`
        return []
    out: list[dict] = []
    for kunde in kunden:
        cid, name = kunde.id, kunde.name
        grund = abbau.automato_kunde_entfernen(session, kunde, akteur)
        out.append({"kurzname": kurzname, "kunde": cid, "name": (name or "")[:NAME_MAX],
                    "mandant": alt, "stand": BLEIBT if grund else ENTFERNT,
                    "grund": grund})  # fmt: skip
    audit.record(session, actor=akteur, action="portal.customer.umzug", subject=kurzname,
                 detail={"von_mandant": alt, "nach_mandant": neu, "quelle": "cockpit",
                         "entfernt": [e["kunde"] for e in out if e["stand"] == ENTFERNT],
                         "bleibt": [{"kunde": e["kunde"], "grund": e["grund"]}
                                    for e in out if e["stand"] == BLEIBT]})  # fmt: skip
    session.flush()
    return out
