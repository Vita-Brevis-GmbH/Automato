"""Verwaltung nur aus dem Cockpit (1.14.0, ADR 0058).

Anwender 2026-11-16: „die Rollen und User … sollen nur noch im Cockpit sein. Die Kunden sollen
dies nicht machen können, da es zu gefährlich ist, dass wir neuen Benutzern bei einem Problem von
aussen [Rechte] geben können“ und „Weiter sollen die Kunden und Portale im Automato durch das
Cockpit erstellt und verwaltet werden, nicht ein Mix wie heute.“

- **Cockpit verbunden** (`cockpit_verbunden`): gekoppelt (ADR 0057) oder `AUTOMATO_COCKPIT_TOKEN`
  gesetzt. Dann entstehen und aendern sich die Kunden des Kunden-Portals nur noch ueber
  `PUT /freigaben`; die Oberflaeche zeigt sie nur lesbar (409 beim Anlegen, Aendern, Entfernen),
  der Soll-Stand fuer den Front-Proxy nennt es (`verwaltung: cockpit`), damit `front.sh` von Hand
  nur noch mit `--trotzdem` aendert. Ohne Cockpit bleibt alles wie bisher.
- **Benutzer und Rollen** (`benutzer` je Kunde in `PUT /freigaben`): Keycloak-Benutzername des
  Kunden-Realms -> Kunden-Rolle. Gesetzt haben nur diese Benutzer Rechte (`auth/oidc.py`). Dieselbe
  Regel fuer Benutzernamen wie Magister: klein, 1-255 Zeichen, druckbar, ohne Leerraum.
- **Kunden aus dem Cockpit** (`cockpit_kunden`): Kunden in Automato (ADR 0018), die ein Portal-Kunde
  aus dem Cockpit nennt oder die Automato fuer das Cockpit angelegt hat. Bei ihnen vergeben Kunden
  keine Rechte mehr (`auth/verwaltung.py`): keine Zuordnung von Kunden-Benutzern durch den
  Kunden-Admin (409), Zuordnungen eines Kunden-Admins wirken nicht mehr, die Rolle aus dem Token
  eines Kunden-Realms zaehlt nicht. Der globale Admin bleibt Notzugang.
- **Kunde anlegen** (`zuordnung.kunde_anlegen`): Automato legt im Mandanten einen Kunden mit dem
  Namen des Portal-Kunden an (idempotent, `cockpit_customer`, Audit `customer.created` mit Akteur
  `cockpit`). Gibt es den Namen im Mandanten schon, heisst der neue Kunde „<Name> (<Kurzname>)“ -
  ein bestehender Kunde wird nie still uebernommen; dafuer waehlt das Cockpit ihn in `kunden`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import capabilities as caps
from app.config import Settings
from app.i18n import L
from app.models import CockpitCustomer, Customer, PortalCustomer
from app.store import kopplung, portal

#: hoechstens so viele Benutzer je Kunde (Vertrag mit dem Cockpit)
MAX_BENUTZER = 2000
#: laengster Keycloak-Benutzername (wie Magister, ADR-0025 dort)
LOGIN_MAX = 255
#: Wert im Soll-Stand fuer den Front-Proxy (`soll.json`, nur wenn ein Cockpit verbunden ist)
VERWALTUNG_COCKPIT = "cockpit"
#: Akteur im Audit
AKTEUR = "cockpit"
#: Laenge des Kundennamens (`customer.name`)
NAME_MAX = 200


def cockpit_verbunden(session: Session, settings: Settings) -> bool:
    """Ist Automato mit einem Cockpit verbunden (Token aus `.env` oder gekoppelt)?"""
    return bool(settings.cockpit_token) or kopplung.coupled(session, kopplung.COCKPIT)


def soll_schreiben(session: Session, settings: Settings, only_if_changed: bool = False,
                   ) -> str | None:  # fmt: skip
    """Soll-Stand fuer den Front-Proxy schreiben (`portal.write_desired`) - mit
    `verwaltung: cockpit`, solange ein Cockpit verbunden ist. Wirft OSError."""
    return portal.write_desired(session, settings.front_state_dir, only_if_changed,
                                cockpit=cockpit_verbunden(session, settings))  # fmt: skip


def portal_text() -> str:
    """Antwort, wenn die Oberflaeche bei verbundenem Cockpit Kunden des Portals aendern will."""
    return L(
        "Automato ist mit dem Cockpit verbunden: Kunden des Kunden-Portals entstehen und ändern "
        "sich nur noch im Cockpit (Kundenregister). Kunden aus der Oberfläche übernimmt das "
        "Cockpit dort („Übernehmen“).",
        "Automato is connected to the cockpit: customers of the customer portal are only created "
        "and changed in the cockpit (customer register). The cockpit takes over customers from "
        "the UI there (“Take over”).",
    )


def benutzer_text() -> str:
    """Antwort, wenn ein Kunden-Benutzer bei einem Kunden aus dem Cockpit Rechte vergeben will."""
    return L(
        "Benutzer und Rollen dieses Kunden vergibt der Betreiber im Cockpit.",
        "Users and roles of this customer are assigned by the operator in the cockpit.",
    )


# --- Benutzer und Rollen aus dem Cockpit ----------------------------------------------------


def check_login(value: object) -> str:
    """Keycloak-Benutzername wie in Magister: klein, 1-255 Zeichen, druckbar, ohne Leerraum."""
    if not isinstance(value, str):
        raise portal.PortalError(L("benutzer ist kein Text", "benutzer is not a string"))
    name = value.strip().lower()
    if not name or len(name) > LOGIN_MAX:
        raise portal.PortalError(L(
            f"Benutzername leer oder länger als {LOGIN_MAX} Zeichen",
            f"user name empty or longer than {LOGIN_MAX} characters"))  # fmt: skip
    if not name.isprintable() or any(c.isspace() for c in name):
        raise portal.PortalError(L(
            f"Benutzername '{name[:40]}' nur druckbare Zeichen ohne Leerraum",
            f"user name '{name[:40]}' only printable characters without whitespace",
        ))  # fmt: skip
    return name


def check_benutzer(roh: object) -> list[dict] | None:
    """`benutzer` eines Kunden pruefen: None = keine Aussage; sonst je Benutzer hoechstens ein
    Eintrag mit einer Kunden-Rolle (nie admin), nach Benutzername sortiert (gleicher Stand =
    gleicher Wert, auch in anderer Reihenfolge)."""
    if roh is None:
        return None
    if not isinstance(roh, list) or len(roh) > MAX_BENUTZER:
        raise portal.PortalError(L(f"benutzer ist keine Liste (höchstens {MAX_BENUTZER})",
                                   f"benutzer is not a list (at most {MAX_BENUTZER})"))  # fmt: skip
    out: dict[str, str] = {}
    for nr, e in enumerate(roh, start=1):
        if not isinstance(e, dict):
            raise portal.PortalError(L(f"benutzer: Eintrag {nr} ist kein Objekt",
                                       f"benutzer: entry {nr} is not an object"))  # fmt: skip
        try:
            name = check_login(e.get("benutzer"))
        except portal.PortalError as exc:
            raise portal.PortalError(L(f"benutzer: Eintrag {nr} – {exc}",
                                       f"benutzer: entry {nr} – {exc}")) from exc  # fmt: skip
        rolle = e.get("rolle")
        if not isinstance(rolle, str) or rolle not in caps.CUSTOMER_ROLES:
            raise portal.PortalError(L(
                f"benutzer '{name[:40]}': Rolle '{str(rolle)[:20]}' unbekannt "
                f"({', '.join(caps.CUSTOMER_ROLES)})",
                f"benutzer '{name[:40]}': role '{str(rolle)[:20]}' unknown "
                f"({', '.join(caps.CUSTOMER_ROLES)})"))  # fmt: skip
        if name in out:
            raise portal.PortalError(L(f"benutzer '{name[:40]}' doppelt",
                                       f"benutzer '{name[:40]}' twice"))  # fmt: skip
        out[name] = rolle
    return [{"benutzer": n, "rolle": out[n]} for n in sorted(out)]


def benutzer_detail(alt: list | None, neu: list | None) -> dict:
    """Audit-Detail einer Aenderung der Benutzer: Anzahl und was sich aendert (je hoechstens 50
    Namen) - nie mehr als Benutzername und Rolle."""
    vorher, nachher = portal.users_of(alt) or {}, portal.users_of(neu) or {}
    detail: dict = {"benutzer": None if neu is None else len(nachher)}
    neu_ = [f"{n}:{r}" for n, r in sorted(nachher.items()) if n not in vorher]
    weg = sorted(n for n in vorher if n not in nachher)
    rolle = [f"{n}:{r}" for n, r in sorted(nachher.items()) if n in vorher and vorher[n] != r]
    for key, werte in (("benutzer_neu", neu_), ("benutzer_entfernt", weg),
                       ("benutzer_rolle", rolle)):  # fmt: skip
        if werte:
            detail[key] = werte[:50]
    return detail


# --- Kunden in Automato, die Automato fuer das Cockpit anlegt -------------------------------


def angelegt_je_kunde(session: Session, short_names: Iterable[str] | None = None,
                      ) -> dict[tuple[str, int], int]:  # fmt: skip
    """(Kurzname, Mandant) -> Kunde, den Automato fuer diesen Portal-Kunden angelegt hat."""
    stmt = select(CockpitCustomer.short_name, CockpitCustomer.tenant_id,
                  CockpitCustomer.customer_id)  # fmt: skip
    if short_names is not None:
        stmt = stmt.where(CockpitCustomer.short_name.in_(list(short_names)))
    return {(kurz, tid): cid for kurz, tid, cid in session.execute(stmt)}


def angelegt(session: Session, short_name: str, tenant_id: int | None) -> int | None:
    """Kunde, den Automato fuer diesen Portal-Kunden im Mandanten angelegt hat (oder None)."""
    if tenant_id is None:
        return None
    return angelegt_je_kunde(session, [short_name]).get((short_name, tenant_id))


@dataclass(frozen=True)
class Anlegen:
    """Plan fuer einen Portal-Kunden mit `kunde_anlegen`: vorhandener Kunde (`kunde`) bzw. Name
    des neuen."""

    kurzname: str
    tenant_id: int
    kunde: int | None
    name: str | None


def _namen(session: Session, tenant_ids: set[int]) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {t: set() for t in tenant_ids}
    if tenant_ids:
        for tid, name in session.execute(
            select(Customer.tenant_id, Customer.name).where(Customer.tenant_id.in_(tenant_ids))
        ):
            out[tid].add(name)
    return out


def planen(
    session: Session, auftraege: list[tuple[str, str, int]]
) -> tuple[list[Anlegen], list[tuple[str, str]]]:
    """Je (Kurzname, Name, Mandant): vorhandener angelegter Kunde oder Name fuer einen neuen. Gibt
    es den Namen im Mandanten schon, „<Name> (<Kurzname>)“; gibt es auch den: Fehler (Kurzname,
    Grund) - die Anfrage aendert dann nichts."""
    vorhanden = angelegt_je_kunde(session, [k for k, _, _ in auftraege])
    belegt = _namen(session, {t for _, _, t in auftraege})
    plan: list[Anlegen] = []
    fehler: list[tuple[str, str]] = []
    for kurz, name, tid in auftraege:
        cid = vorhanden.get((kurz, tid))
        if cid is not None:
            plan.append(Anlegen(kurz, tid, cid, None))
            continue
        zusatz = f" ({kurz})"
        kandidaten = (name[:NAME_MAX], f"{name[: NAME_MAX - len(zusatz)].rstrip()}{zusatz}")
        frei = next((n for n in kandidaten if n not in belegt[tid]), None)
        if frei is None:
            fehler.append((kurz, L(
                f"zuordnung.kunde_anlegen: Kunden '{kandidaten[0]}' und '{kandidaten[1]}' gibt "
                f"es im Mandanten {tid} schon – den bestehenden in zuordnung.kunden wählen",
                f"zuordnung.kunde_anlegen: customers '{kandidaten[0]}' and '{kandidaten[1]}' "
                f"already exist in tenant {tid} – choose the existing one in zuordnung.kunden",
            )))  # fmt: skip
            continue
        belegt[tid].add(frei)
        plan.append(Anlegen(kurz, tid, None, frei))
    return plan, fehler


def anlegen(session: Session, p: Anlegen, akteur: str = AKTEUR) -> int:
    """Geplanten Kunden anlegen (ohne commit) bzw. den vorhandenen liefern; Audit
    `customer.created` mit Akteur `cockpit` und dem Portal-Kunden."""
    if p.kunde is not None:
        return p.kunde
    jetzt = datetime.now(UTC)
    c = Customer(tenant_id=p.tenant_id, name=p.name, created_at=jetzt, updated_at=jetzt)
    session.add(c)
    session.flush()
    session.add(CockpitCustomer(short_name=p.kurzname, tenant_id=p.tenant_id, customer_id=c.id,
                                created_at=jetzt))  # fmt: skip
    audit.record(session, actor=akteur, action="customer.created", subject=str(c.id),
                 detail={"tenant_id": p.tenant_id, "customer_id": c.id, "name": c.name,
                         "selectors": 0, "quelle": "cockpit",
                         "portal_kunde": p.kurzname})  # fmt: skip
    session.flush()
    return c.id


# --- Kunden aus dem Cockpit: hier vergeben Kunden keine Rechte ------------------------------


def cockpit_kunden(session: Session, tenant_ids: Iterable[int] | None = None) -> frozenset[int]:
    """Kunden in Automato, deren Benutzer und Rollen das Cockpit fuehrt: die ein Portal-Kunde
    aus dem Cockpit in seinem Mandanten nennt, und die Automato fuer das Cockpit angelegt hat.
    `tenant_ids` schraenkt auf diese Mandanten ein (None = alle)."""
    tids = None if tenant_ids is None else {int(t) for t in tenant_ids}
    if tids is not None and not tids:
        return frozenset()
    stmt = select(PortalCustomer.tenant_id, PortalCustomer.customer_ids).where(
        PortalCustomer.source == portal.SOURCE_COCKPIT, PortalCustomer.tenant_id.is_not(None)
    )
    if tids is not None:
        stmt = stmt.where(PortalCustomer.tenant_id.in_(tids))
    genannt: dict[int, set[int]] = {}
    for tid, ids in session.execute(stmt):
        genannt.setdefault(tid, set()).update(int(c) for c in ids or [])
    out: set[int] = set()
    if genannt:  # nur Kunden, die wirklich in diesem Mandanten liegen
        for tid, cid in session.execute(
            select(Customer.tenant_id, Customer.id).where(Customer.tenant_id.in_(list(genannt)))
        ):
            if cid in genannt[tid]:
                out.add(cid)
    stmt2 = select(CockpitCustomer.customer_id)
    if tids is not None:
        stmt2 = stmt2.where(CockpitCustomer.tenant_id.in_(tids))
    out.update(session.scalars(stmt2))
    return frozenset(out)
