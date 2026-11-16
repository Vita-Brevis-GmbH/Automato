"""Ist-Stand der Instanzen fuers Cockpit (1.14.0, ADR 0059): `GET /inventory` -> `ist`.

Anwender 2026-11-16: „Dann sollten wir auch noch die Instanzen auslesen, damit wir sehen können,
ob wir altlasten haben“. Das Cockpit vergleicht den Ist-Stand aller Werkzeuge mit seinem
Kundenregister (Seite „Aufräumen“); Automato liefert seinen Teil - nur Namen, keine Geheimnisse:

- `front`: was der Front-Proxy auf der VM hat (`status.json` von `front.sh`, Schluessel `ist`):
  Kunden, gesperrte Kunden, Realms mit Tor-Geheimnis (`tor/<realm>.env`), Realms mit Container
  des Tors, Container ohne bekannten Realm, verwaiste Tor-Dateien (ohne Kunden - nur gemeldet,
  nie still geloescht) und was der letzte Abgleich auf Auftrag entfernt bzw. abgelehnt hat.
- `netbox_host`: letzte Meldung des Agenten des NetBox-Hosts (`PUT /netbox-host/stand` nach jedem
  `sync`): Instanzen, Instanzen ohne Freigabe, auf Auftrag geloeschte bzw. nicht geloeschte.
- `automato_kunden`: die Kunden in Automato (ADR 0018) mit Mandant, fuer welchen Portal-Kunden
  Automato sie angelegt hat (`von_cockpit`, `zuordnung.kunde_anlegen`, ADR 0058) und ob ihnen
  Daten zugeordnet sind (`hat_daten`, `daten`).
- `entfernen`: Stand der Loeschauftraege aus dem Cockpit (`store/abbau.py`).

Ob ein Kunde in Automato Daten hat, entscheidet `daten`: Selektoren (sein Ausschnitt), abgeleitete
Zugehoerigkeit, Benutzer-Zuordnungen, eigene logische Zonen und eine eigene Anordnung des
Zonenplans. Gelesene Daten der Quellsysteme gehoeren dem Mandanten, nicht dem Kunden (ADR 0018) -
sie bleiben beim Entfernen eines Kunden ohnehin.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.i18n import L
from app.models import (
    Customer,
    CustomerMember,
    CustomerSelector,
    CustomerUser,
    IstMeldung,
    LogicalZone,
    PortalCustomer,
    ZonePlanArrangement,
)
from app.store import portal, verwaltung

VERTRAG = 1
NETBOX_HOST = "netbox_host"  # Quelle der Meldung des Agenten des NetBox-Hosts
MAX_LISTE = 1000
MAX_AUTOMATO_KUNDEN = 5000
MAX_NAME = 200
# was einem Kunden in Automato zugeordnet sein kann (Reihenfolge der Ausgabe)
SELEKTOREN, ZUGEHOERIGKEIT, BENUTZER, ZONEN, ZONENPLAN = (
    "selektoren", "zugehoerigkeit", "benutzer", "zonen", "zonenplan")  # fmt: skip
GRUENDE = (SELEKTOREN, ZUGEHOERIGKEIT, BENUTZER, ZONEN, ZONENPLAN)
# Listen der Meldung des NetBox-Hosts (nur Kurznamen)
NETBOX_LISTEN = ("instanzen", "ohne_freigabe", "geloescht", "loeschen_fehlgeschlagen")
_DIENST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_GRUND = re.compile(r"^[a-z_]{1,40}$")


class MeldungFehler(ValueError):
    """Meldung ungueltig - Text nennt nur das Feld, nie Werte."""


def _utc(value: datetime | None) -> datetime | None:
    """SQLite liefert Zeiten ohne Zone - als UTC lesen (wie `store/kopplung.py`)."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def iso(value: datetime | None) -> str | None:
    value = _utc(value)
    return value.isoformat(timespec="seconds") if value is not None else None


# --- Daten eines Kunden in Automato ------------------------------------------------------------
def angelegte_kunden(session: Session, kurzname: str | None = None) -> dict[int, str]:
    """Je Kunde in Automato, den Automato auf Wunsch des Cockpits fuer einen Portal-Kunden angelegt
    hat (`zuordnung.kunde_anlegen`, ADR 0058), dessen Kurzname (`{customer_id: kurzname}`; mit
    `kurzname` nur die fuer diesen Portal-Kunden - in allen Mandanten, ein frueherer Mandant
    gehoert dazu).

    Grundlage ist die Verknuepfung `cockpit_customer` (`store/verwaltung.py`): sie haengt am
    Kurznamen, nicht am Portal-Kunden - sie bleibt also, wenn die Freigaben den Portal-Kunden
    entfernen, und faellt erst mit dem Kunden in Automato (bzw. seinem Mandanten). Ohne
    Verknuepfung entfernt der Abbau keinen Kunden in Automato (Teil `automato_kunde` erledigt mit
    `keiner_angelegt`), und das Inventar meldet `von_cockpit: false`."""
    namen = None if kurzname is None else [kurzname]
    return {cid: kurz for (kurz, _tid), cid in verwaltung.angelegt_je_kunde(session, namen).items()}


def daten(session: Session, ids: Iterable[int] | None = None) -> dict[int, list[str]]:
    """Je Kunde, was ihm in Automato zugeordnet ist (`GRUENDE`); Kunden ohne Daten fehlen."""
    nur = None if ids is None else {int(i) for i in ids}
    if nur is not None and not nur:
        return {}
    out: dict[int, set[str]] = defaultdict(set)
    for spalte, grund in ((CustomerSelector.customer_id, SELEKTOREN),
                          (CustomerMember.customer_id, ZUGEHOERIGKEIT),
                          (CustomerUser.customer_id, BENUTZER),
                          (LogicalZone.customer_id, ZONEN)):  # fmt: skip
        stmt = select(spalte).where(spalte.is_not(None)).distinct()
        if nur is not None:
            stmt = stmt.where(spalte.in_(nur))
        for cid in session.scalars(stmt):
            out[int(cid)].add(grund)
    # eigene Anordnung des Zonenplans (Ansicht `customer:<id>`)
    for view in session.scalars(
        select(ZonePlanArrangement.view).where(ZonePlanArrangement.view.like("customer:%"))
    ):
        try:
            cid = int(view.split(":", 1)[1])
        except ValueError:
            continue
        if nur is None or cid in nur:
            out[cid].add(ZONENPLAN)
    return {cid: [g for g in GRUENDE if g in gruende] for cid, gruende in out.items()}


def zugeordnet(session: Session) -> dict[int, list[str]]:
    """Je Kunde in Automato die Portal-Kunden, deren Zuordnung ihn enthaelt (Kurznamen)."""
    out: dict[int, list[str]] = defaultdict(list)
    for pc in session.scalars(select(PortalCustomer).order_by(PortalCustomer.short_name)):
        for cid in pc.customer_ids or []:
            if isinstance(cid, int) and pc.short_name not in out[cid]:
                out[cid].append(pc.short_name)
    return dict(out)


def automato_kunden(session: Session) -> list[dict]:
    """Kunden in Automato mit Mandant, Herkunft und ob ihnen Daten zugeordnet sind."""
    angelegt = angelegte_kunden(session)
    zug = zugeordnet(session)
    mit_daten = daten(session)
    rows = session.execute(
        select(Customer.id, Customer.name, Customer.tenant_id)
        .order_by(Customer.tenant_id, Customer.name, Customer.id)
        .limit(MAX_AUTOMATO_KUNDEN)
    )
    out = []
    for cid, name, tid in rows:
        portal_kunde = angelegt.get(cid) or next(iter(zug.get(cid) or []), None)
        out.append({
            "id": cid, "name": (name or "")[:MAX_NAME], "mandant": tid,
            "portal_kunde": portal_kunde, "von_cockpit": cid in angelegt,
            "hat_daten": bool(mit_daten.get(cid)), "daten": mit_daten.get(cid, []),
            "zugeordnet": zug.get(cid, []),
        })  # fmt: skip
    return out


# --- Front-Proxy --------------------------------------------------------------------------------
def _namen(value: object, muster) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return sorted({v for v in value[:MAX_LISTE] if isinstance(v, str) and muster(v)})


def _realm(value: str) -> bool:
    return bool(portal.REALM.fullmatch(value)) and value != "master"


def front(state_dir: str) -> dict | None:
    """Ist-Stand des Front-Proxys aus `status.json` (front.sh, Schluessel `ist`); None ohne
    Front-Proxy bzw. ohne Meldung. Listen ohne Meldung (aelteres front.sh) sind null."""
    if not state_dir:
        return None
    status = portal.agent_status(state_dir).get("status")
    if not isinstance(status, dict) or "zeit" not in status:
        return None
    teil = status.get("ist") if isinstance(status.get("ist"), dict) else {}
    kunden = teil.get("kunden", status.get("kunden"))
    abgelehnt = []
    for e in (teil.get("tor_entfernen_abgelehnt") or [])[:MAX_LISTE]:
        if isinstance(e, dict) and isinstance(e.get("realm"), str) and _realm(e["realm"]) \
                and isinstance(e.get("grund"), str) and _GRUND.fullmatch(e["grund"]):  # fmt: skip
            abgelehnt.append({"realm": e["realm"], "grund": e["grund"]})
    zeit = status.get("zeit")
    return {
        "stand": zeit if isinstance(zeit, str) and len(zeit) <= 40 else None,
        "kunden": _namen(kunden, portal.SHORT_NAME.match),
        "gesperrt": _namen(teil.get("gesperrt"), portal.SHORT_NAME.match),
        "tor_realms_dateien": _namen(teil.get("tor_realms_dateien"), _realm),
        "tor_dienste": _namen(teil.get("tor_dienste"), _realm),
        "tor_dienste_fremd": _namen(teil.get("tor_dienste_fremd"), _DIENST.match),
        "tor_verwaist": _namen(teil.get("tor_verwaist"), _realm),
        "tor_entfernt": _namen(teil.get("tor_entfernt"), _realm),
        "tor_entfernen_abgelehnt": abgelehnt if "ist" in status else None,
    }


# --- NetBox-Host --------------------------------------------------------------------------------
def netbox_host_pruefen(data: object) -> dict:
    """Meldung des Agenten pruefen (`PUT /netbox-host/stand`): `vertrag` 1, `zeit` (ISO, Zeit
    des Agenten), Listen von Kurznamen (`NETBOX_LISTEN`, je hoechstens 1000). Wirft
    `MeldungFehler` mit dem Feld."""
    if not isinstance(data, dict):
        raise MeldungFehler(L("Objekt erwartet", "Object expected"))
    vertrag = data.get("vertrag")
    if isinstance(vertrag, bool) or vertrag != VERTRAG:
        raise MeldungFehler(L(f"vertrag nicht unterstützt (erwartet {VERTRAG})",
                              f"vertrag not supported (expected {VERTRAG})"))  # fmt: skip
    zeit = data.get("zeit")
    if zeit is not None:
        try:
            ok = isinstance(zeit, str) and len(zeit) <= 40
            ok = ok and datetime.fromisoformat(zeit) is not None
        except ValueError:
            ok = False
        if not ok:
            raise MeldungFehler(L("zeit ist keine ISO-Zeit", "zeit is not an ISO time"))
    out: dict = {"zeit": zeit}
    for feld in NETBOX_LISTEN:
        werte = data.get(feld)
        if werte is None:
            werte = []
        ok = isinstance(werte, list) and len(werte) <= MAX_LISTE
        if not ok or not all(isinstance(k, str) and portal.SHORT_NAME.match(k) for k in werte):
            raise MeldungFehler(L(
                f"{feld}: Liste von Kurznamen erwartet (höchstens {MAX_LISTE})",
                f"{feld}: list of short names expected (at most {MAX_LISTE})"))  # fmt: skip
        out[feld] = sorted(set(werte))
    return out


def netbox_meldung(session: Session) -> IstMeldung | None:
    return session.scalar(select(IstMeldung).where(IstMeldung.quelle == NETBOX_HOST))


def netbox_host_melden(session: Session, meldung: dict) -> IstMeldung:
    """Meldung speichern (ersetzt die vorige, ohne commit); `empfangen` = Zeit von Automato."""
    row = netbox_meldung(session)
    if row is None:
        row = IstMeldung(quelle=NETBOX_HOST)
        session.add(row)
    row.daten = dict(meldung)
    row.empfangen = datetime.now(UTC)
    session.flush()
    return row


def netbox_host(session: Session) -> dict | None:
    row = netbox_meldung(session)
    if row is None:
        return None
    d = row.daten if isinstance(row.daten, dict) else {}
    return {"stand": d.get("zeit"), "empfangen": iso(row.empfangen),
            **{feld: list(d.get(feld) or []) for feld in NETBOX_LISTEN}}  # fmt: skip


# --- Inventar -----------------------------------------------------------------------------------
def ist(session: Session, settings: Settings) -> dict:
    """`ist` im Inventar (Vertrag v1, optionales Feld)."""
    from app.store import abbau  # spaet: abbau nutzt ist_stand

    return {
        "front": front(settings.front_state_dir),
        "netbox_host": netbox_host(session),
        "automato_kunden": automato_kunden(session),
        "entfernen": abbau.stand(session),
    }
