"""Abbau aus dem Cockpit (ADR 0059): Loeschauftraege mit Stand je Teil.

Anwender 2026-11-16 auf den Vorschlag „Abbau-Plan, Daten nie ohne Sicherung, Frist 7 Tage ‚zum
Entfernen vorgemerkt‘, für Dev ‚sofort‘“: „Einstellbar, aber sonst ok“. Das Cockpit sperrt einen
Kunden waehrend seiner Frist (`gesperrt`, `store/freigaben.py`); nach der Frist nimmt es ihn aus
`kunden[]` und schickt mit `PUT /freigaben` einen Loeschauftrag (`entfernen`), so lange, bis das
Inventar (`ist.entfernen`) meldet, dass er erledigt ist.

- **Nur Kunden aus dem Cockpit:** angenommen wird ein Kurzname, der nicht (mehr) in `kunden[]`
  steht und den das Cockpit selbst entfernt hat - in derselben Anfrage (vorher ein Kunde aus dem
  Cockpit) oder zuvor (letzter Eintrag `portal.customer.*` im Audit: `deleted` durch das Cockpit).
  Kunden aus der Oberflaeche bzw. Kurznamen, die Automato nie vom Cockpit hatte, nie
  (`abgelehnt`). Gibt es den Kurznamen wieder als Portal-Kunden, ist der Auftrag aufgehoben - ein
  neuer Kunde mit demselben Namen wird nie abgebaut.
- **Automato aendert nichts auf den Hosts** (ADR 0050): die NetBox loescht der Agent des
  NetBox-Hosts (`GET /netbox-host` → `loeschen`, mit Sicherung vorher), das Tor-Geheimnis entfernt
  `front.sh sync` (`soll.json` → `tor_entfernen`). Beide pruefen noch einmal selbst und merken sich
  die Kennung des Auftrags - erledigt ist erledigt, auch wenn danach etwas mit diesem Namen neu
  entsteht.
- **Kunde in Automato:** nur der, den Automato fuer diesen Portal-Kunden angelegt hat
  (`ist_stand.angelegte_kunden`), und nur ohne Daten (`ist_stand.daten`) und ohne Zuordnung zu
  einem anderen Portal-Kunden - sonst bleibt er und der Teil bleibt offen (`hat_daten`,
  `zugeordnet`).
- **Stand je Teil** (`teile`): `offen` mit dem Grund, worauf er wartet, oder `erledigt`; erledigt
  bleibt erledigt. Fortgeschrieben bei jeder Anfrage des Cockpits (`anwenden`) und bei jeder
  Meldung des NetBox-Hosts (Teil `netbox`).
"""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.audit import decode_detail
from app.config import Settings
from app.i18n import L
from app.models import AbbauAuftrag, AuditLog, Customer, IstMeldung, PortalCustomer
from app.store import ist_stand, portal
from app.store.freigaben import AKTEUR, MAX_KUNDEN, FreigabenFehler, _fehler

OFFEN, ERLEDIGT, ABGELEHNT = "offen", "erledigt", "abgelehnt"
TEILE = ("netbox", "automato_kunde", "tor")
# Ablehnung eines Eintrags (Antwort von PUT /freigaben, ist.entfernen)
IN_KUNDEN = "in_kunden"  # steht in kunden[] derselben Anfrage
OBERFLAECHE = "oberflaeche"  # Kunde aus der Oberflaeche von Automato - nie
NICHT_VOM_COCKPIT = "nicht_vom_cockpit"  # nie ein Kunde aus dem Cockpit dieses Namens
SCHON_ERLEDIGT = "schon_erledigt"  # Abbau dieses Kunden schon erledigt (nie ein zweites Mal)
# Gruende je Teil
NETBOX_HOST = "netbox_host"  # wartet auf den Agenten des NetBox-Hosts (Instanz noch da)
KEIN_AGENT = "kein_agent"  # kein Agent des NetBox-Hosts eingerichtet - von Hand loeschen
LOESCHEN_FEHLGESCHLAGEN = "loeschen_fehlgeschlagen"  # Agent meldet: nicht geloescht (Sicherung?)
GELOESCHT, NICHT_VORHANDEN = "geloescht", "nicht_vorhanden"
HAT_DATEN, ZUGEORDNET = "hat_daten", "zugeordnet"  # Kunde in Automato bleibt
KEINER_ANGELEGT = "keiner_angelegt"  # Automato hat fuer diesen Portal-Kunden keinen angelegt
FRONT_PROXY = "front_proxy"  # wartet auf den Front-Proxy (front.sh sync)
KEIN_FRONT_PROXY = "kein_front_proxy"  # Automato fuehrt keinen Front-Proxy
KEIN_REALM, REALM_IN_VERWENDUNG, HAUPT_REALM = "kein_realm", "realm_in_verwendung", "haupt_realm"
ENTFERNT = "entfernt"
# Ende eines Auftrags ohne Erledigung
WIEDER_DA, NICHT_GESENDET = "wieder_da", "nicht_gesendet"
#: letzte abgelehnte Eintraege (ist.entfernen), als Meldung gespeichert
ABGELEHNT_QUELLE = "abbau_abgelehnt"


@dataclass(frozen=True)
class Eintrag:
    """Ein gepruefter Eintrag aus `entfernen`."""

    kurzname: str
    netbox: bool = False
    automato_kunde: bool = False
    tor: bool = False

    def wuensche(self) -> dict[str, bool]:
        return {t: getattr(self, t) for t in TEILE}


@dataclass(frozen=True)
class Vorher:
    """Ein Portal-Kunde vor dem Anwenden der Freigaben (danach ist er ggf. schon entfernt)."""

    quelle: str
    realm: str | None
    angelegt: tuple[int, ...]


@dataclass
class Plan:
    eintraege: list[Eintrag] | None  # None = `entfernen` nicht gesendet (keine Aussage)
    anfrage: set[str] = field(default_factory=set)  # Kurznamen in kunden[] der Anfrage
    vorher: dict[str, Vorher] = field(default_factory=dict)


@dataclass
class Ergebnis:
    gesendet: bool
    angenommen: list[str] = field(default_factory=list)
    abgelehnt: list[dict] = field(default_factory=list)
    geaendert: bool = False

    def antwort(self) -> dict | None:
        if not self.gesendet:
            return None
        return {"angenommen": self.angenommen, "abgelehnt": self.abgelehnt}


def _jetzt() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


# --- Anfrage pruefen ----------------------------------------------------------------------------
def pruefen(data: object) -> list[Eintrag] | None:
    """`entfernen` der Anfrage pruefen (alles oder nichts wie `kunden`); None = nicht gesendet."""
    roh = data.get("entfernen") if isinstance(data, dict) else None
    if roh is None:
        return None
    if not isinstance(roh, list):
        raise FreigabenFehler([_fehler(None, L("entfernen ist keine Liste",
                                               "entfernen is not a list"))])  # fmt: skip
    if len(roh) > MAX_KUNDEN:
        raise FreigabenFehler([_fehler(None, L(
            f"entfernen: höchstens {MAX_KUNDEN} Einträge",
            f"entfernen: at most {MAX_KUNDEN} entries"))])  # fmt: skip
    fehler: list[dict] = []
    out: list[Eintrag] = []
    gesehen: set[str] = set()
    for nr, e in enumerate(roh, start=1):
        if not isinstance(e, dict):
            fehler.append(_fehler(None, L(f"entfernen: Eintrag {nr} ist kein Objekt",
                                          f"entfernen: entry {nr} is not an object")))  # fmt: skip
            continue
        kurz = e.get("kurzname")
        try:
            if not isinstance(kurz, str):
                raise portal.PortalError(L("Kurzname fehlt", "Short name missing"))
            kurz = portal.check_short_name(kurz)
        except portal.PortalError as exc:
            fehler.append(_fehler(kurz, L(f"entfernen: {exc}", f"entfernen: {exc}")))
            continue
        werte: dict[str, bool] = {}
        for teil in TEILE:
            wert = e.get(teil)
            if wert is None:
                wert = False
            if not isinstance(wert, bool):
                fehler.append(_fehler(kurz, L(f"entfernen: {teil} ist nicht true/false",
                                              f"entfernen: {teil} is not true/false")))  # fmt: skip
                break
            werte[teil] = wert
        else:
            if kurz in gesehen:
                fehler.append(_fehler(kurz, L("entfernen: Kurzname doppelt",
                                              "entfernen: short name twice")))  # fmt: skip
                continue
            gesehen.add(kurz)
            out.append(Eintrag(kurz, **werte))
    if fehler:
        raise FreigabenFehler(fehler)
    return out


def vorbereiten(session: Session, data: object) -> Plan:
    """Vor dem Anwenden der Freigaben: `entfernen` pruefen (wirft `FreigabenFehler`) und den Stand
    der betroffenen Portal-Kunden merken - die Freigaben entfernen Kunden aus dem Cockpit, die nicht
    mehr in `kunden[]` stehen, samt Realm."""
    eintraege = pruefen(data)
    if eintraege is None:
        return Plan(None)
    kunden = data.get("kunden") if isinstance(data, dict) else None
    anfrage = {k["kurzname"].strip() for k in kunden or []
               if isinstance(k, dict) and isinstance(k.get("kurzname"), str)}  # fmt: skip
    namen = [e.kurzname for e in eintraege]
    vorher = {}
    if namen:
        stmt = select(PortalCustomer).where(PortalCustomer.short_name.in_(namen))
        for pc in session.scalars(stmt):
            angelegt = tuple(sorted(ist_stand.angelegte_kunden(session, pc.short_name)))
            vorher[pc.short_name] = Vorher(pc.source or portal.SOURCE_UI, pc.realm, angelegt)
    return Plan(eintraege, anfrage, vorher)


def _vom_cockpit_entfernt(session: Session, kurzname: str) -> tuple[str | None, str | None]:
    """Hat das Cockpit diesen Kunden zuletzt entfernt? Massgebend ist der letzte Eintrag
    `portal.customer.*` im Audit (ein spaeter in der Oberflaeche angelegter und entfernter Kunde
    gleichen Namens zaehlt also nicht); ein danach erledigter Abbau zaehlt nie ein zweites Mal.
    Liefert (Grund der Ablehnung bzw. None, Realm beim Entfernen)."""
    row = session.scalar(
        select(AuditLog)
        .where(AuditLog.action.in_(("portal.customer.created", "portal.customer.updated",
                                    "portal.customer.deleted", "portal.abbau.erledigt")),
               AuditLog.subject == kurzname)
        .order_by(AuditLog.id.desc())
        .limit(1)
    )  # fmt: skip
    if row is not None and row.action == "portal.abbau.erledigt":
        return SCHON_ERLEDIGT, None
    if row is None or row.action != "portal.customer.deleted" or row.actor != AKTEUR:
        return NICHT_VOM_COCKPIT, None
    realm = (decode_detail(row) or {}).get("realm")
    return None, realm if isinstance(realm, str) and portal.REALM.fullmatch(realm) else None


# --- Auftraege annehmen und beenden -------------------------------------------------------------
def _aufheben(session: Session, a: AbbauAuftrag, grund: str, akteur: str) -> None:
    """Auftrag beenden: offen = aufgehoben (Audit), erledigt = nur vergessen."""
    if a.erledigt_am is None:
        audit.record(session, actor=akteur, action="portal.abbau.aufgehoben", subject=a.short_name,
                     detail={"grund": grund, **{t: getattr(a, t) for t in TEILE}})  # fmt: skip
    session.delete(a)


def _abgelehnt_merken(session: Session, abgelehnt: list[dict]) -> bool:
    """Letzte abgelehnte Eintraege fuers Inventar - nur bei neuem Inhalt geschrieben."""
    row = session.scalar(select(IstMeldung).where(IstMeldung.quelle == ABGELEHNT_QUELLE))
    if row is not None and (row.daten or {}).get("abgelehnt") == abgelehnt:
        return False
    if row is None:
        if not abgelehnt:
            return False
        row = IstMeldung(quelle=ABGELEHNT_QUELLE)
        session.add(row)
    row.daten = {"abgelehnt": abgelehnt}
    row.empfangen = _jetzt()
    return True


def anwenden(session: Session, plan: Plan, settings: Settings, akteur: str = AKTEUR) -> Ergebnis:
    """Nach dem Anwenden der Freigaben (ohne commit): Auftraege annehmen, nachfuehren, beenden und
    alle offenen Teile fortschreiben."""
    erg = Ergebnis(gesendet=plan.eintraege is not None)
    session.flush()
    portal_kunden = {pc.short_name for pc in session.scalars(select(PortalCustomer))}
    auftraege = {a.short_name: a for a in session.scalars(select(AbbauAuftrag))}
    # 1. den Kurznamen gibt es wieder (bzw. noch) als Portal-Kunden: nie diesen abbauen
    for kurz in sorted(auftraege):
        if kurz in portal_kunden:
            _aufheben(session, auftraege.pop(kurz), WIEDER_DA, akteur)
            erg.geaendert = True
    if plan.eintraege is not None:
        gewollt: set[str] = set()
        for e in plan.eintraege:
            grund = None
            realm = None
            vorher = plan.vorher.get(e.kurzname)
            if e.kurzname in plan.anfrage:
                grund = IN_KUNDEN
            elif e.kurzname in portal_kunden or (vorher and vorher.quelle != portal.SOURCE_COCKPIT):
                grund = OBERFLAECHE  # Kunden aus dem Cockpit sind nach dem Anwenden entfernt
            elif e.kurzname not in auftraege and vorher is None:
                grund, realm = _vom_cockpit_entfernt(session, e.kurzname)
            if grund:
                erg.abgelehnt.append({"kurzname": e.kurzname, "grund": grund})
                continue
            gewollt.add(e.kurzname)
            erg.angenommen.append(e.kurzname)
            a = auftraege.get(e.kurzname)
            if a is None:
                if vorher is not None:
                    realm = vorher.realm
                angelegt = set(vorher.angelegt if vorher else ())
                angelegt |= set(ist_stand.angelegte_kunden(session, e.kurzname))
                a = AbbauAuftrag(short_name=e.kurzname, kennung=secrets.token_hex(16), realm=realm,
                                 automato_kunden=sorted(angelegt), teile={}, angenommen_am=_jetzt(),
                                 **e.wuensche())  # fmt: skip
                session.add(a)
                auftraege[e.kurzname] = a
                audit.record(session, actor=akteur, action="portal.abbau.angenommen",
                             subject=e.kurzname,
                             detail={**e.wuensche(), "realm": realm})  # fmt: skip
                erg.geaendert = True
            elif {t: getattr(a, t) for t in TEILE} != e.wuensche():
                for teil, wert in e.wuensche().items():
                    setattr(a, teil, wert)
                audit.record(session, actor=akteur, action="portal.abbau.geaendert",
                             subject=e.kurzname, detail=e.wuensche())  # fmt: skip
                erg.geaendert = True
        # 2. nicht mehr gesendet: das Cockpit fuehrt den Abbau nicht mehr (erledigt bzw. zurueck)
        for kurz in sorted(auftraege):
            if kurz not in gewollt:
                _aufheben(session, auftraege.pop(kurz), NICHT_GESENDET, akteur)
                erg.geaendert = True
        erg.geaendert |= _abgelehnt_merken(session, erg.abgelehnt)
    session.flush()
    erg.geaendert |= fortschreiben(session, settings, list(auftraege.values()), akteur=akteur)
    return erg


# --- Teile fortschreiben ------------------------------------------------------------------------
def automato_kunde_entfernen(session: Session, kunde: Customer, akteur: str = AKTEUR) -> str | None:
    """Einen Kunden in Automato entfernen, den Automato fuer einen Portal-Kunden aus dem Cockpit
    angelegt hat - nur ohne Daten und ohne Zuordnung zu einem (anderen) Portal-Kunden. None =
    entfernt (Audit `customer.deleted`, Akteur `cockpit`), sonst der Grund, warum er bleibt."""
    if ist_stand.daten(session, [kunde.id]).get(kunde.id):
        return HAT_DATEN
    if ist_stand.zugeordnet(session).get(kunde.id):
        return ZUGEORDNET
    audit.record(session, actor=akteur, action="customer.deleted", subject=str(kunde.id),
                 detail={"tenant_id": kunde.tenant_id, "customer_id": kunde.id,
                         "name": kunde.name, "selectors": 0})  # fmt: skip
    session.delete(kunde)
    session.flush()
    return None


def _netbox(a: AbbauAuftrag, meldung: IstMeldung | None, settings: Settings) -> tuple[str, str]:
    """Erledigt, sobald eine Meldung des NetBox-Hosts nach dem Annehmen die Instanz nicht mehr
    nennt."""
    if meldung is None or _utc(meldung.empfangen) <= _utc(a.angenommen_am):
        return OFFEN, NETBOX_HOST if settings.netbox_host_token else KEIN_AGENT
    d = meldung.daten if isinstance(meldung.daten, dict) else {}
    if a.short_name in (d.get("instanzen") or []):
        if a.short_name in (d.get("loeschen_fehlgeschlagen") or []):
            return OFFEN, LOESCHEN_FEHLGESCHLAGEN
        return OFFEN, NETBOX_HOST
    return ERLEDIGT, GELOESCHT if a.short_name in (d.get("geloescht") or []) else NICHT_VORHANDEN


def _automato_kunde(session: Session, a: AbbauAuftrag, akteur: str) -> tuple[str, str]:
    ids = set(a.automato_kunden or []) | set(ist_stand.angelegte_kunden(session, a.short_name))
    if not ids:
        return ERLEDIGT, KEINER_ANGELEGT
    vorhanden = list(session.scalars(select(Customer).where(Customer.id.in_(ids))
                                     .order_by(Customer.id)))  # fmt: skip
    if not vorhanden:
        return ERLEDIGT, NICHT_VORHANDEN
    gruende = [g for g in (automato_kunde_entfernen(session, c, akteur) for c in vorhanden) if g]
    return (OFFEN, gruende[0]) if gruende else (ERLEDIGT, GELOESCHT)


def _tor(a: AbbauAuftrag, realms: set[str], settings: Settings,
         front: dict | None) -> tuple[str, str]:  # fmt: skip
    """Erledigt, sobald der Front-Proxy kein Tor-Geheimnis fuer den Realm mehr meldet - bzw.
    nichts zu tun (kein Realm, Realm eines anderen Kunden oder von Automato selbst)."""
    if not a.realm:
        return ERLEDIGT, KEIN_REALM
    if a.realm in realms:
        return ERLEDIGT, REALM_IN_VERWENDUNG
    haupt = portal.realm_of(settings.oidc_issuer)
    if haupt and a.realm == haupt[1]:
        return ERLEDIGT, HAUPT_REALM
    if not settings.front_state_dir:
        return ERLEDIGT, KEIN_FRONT_PROXY
    if front is None or front.get("tor_realms_dateien") is None:
        return OFFEN, FRONT_PROXY
    if any(e["realm"] == a.realm for e in front.get("tor_entfernen_abgelehnt") or []):
        # front.sh: Realm von Automato selbst bzw. eines Kunden von Hand
        return ERLEDIGT, REALM_IN_VERWENDUNG
    if a.realm in front["tor_realms_dateien"]:
        return OFFEN, FRONT_PROXY
    return ERLEDIGT, ENTFERNT if a.realm in (front.get("tor_entfernt") or []) else NICHT_VORHANDEN


def fortschreiben(session: Session, settings: Settings, auftraege: list[AbbauAuftrag] | None = None,
                  teile: tuple[str, ...] = TEILE, akteur: str = AKTEUR) -> bool:  # fmt: skip
    """Offene Teile der Auftraege pruefen und den Stand speichern (ohne commit); True = geaendert.
    Erledigt bleibt erledigt."""
    if auftraege is None:
        auftraege = list(session.scalars(select(AbbauAuftrag).order_by(AbbauAuftrag.short_name)))
    if not auftraege:
        return False
    meldung = ist_stand.netbox_meldung(session) if "netbox" in teile else None
    front = ist_stand.front(settings.front_state_dir) if "tor" in teile else None
    realms = {r for r in session.scalars(select(PortalCustomer.realm)) if r}
    geaendert = False
    jetzt = _jetzt()
    for a in sorted(auftraege, key=lambda x: x.short_name):
        stand = dict(a.teile or {})
        for teil in TEILE:
            if not getattr(a, teil):
                if teil in stand and stand[teil].get("stand") != ERLEDIGT:
                    del stand[teil]  # nicht mehr gewuenscht
                continue
            alt = stand.get(teil) or {}
            if teil not in teile or alt.get("stand") == ERLEDIGT:
                continue
            if teil == "netbox":
                neu = _netbox(a, meldung, settings)
            elif teil == "automato_kunde":
                neu = _automato_kunde(session, a, akteur)
            else:
                neu = _tor(a, realms, settings, front)
            if (alt.get("stand"), alt.get("grund")) == neu:
                continue
            stand[teil] = {"stand": neu[0], "grund": neu[1],
                           "zeit": jetzt.isoformat(timespec="seconds")}  # fmt: skip
            if neu[0] == ERLEDIGT:
                audit.record(session, actor=akteur, action="portal.abbau.fortschritt",
                             subject=a.short_name,
                             detail={"teil": teil, "stand": neu[0], "grund": neu[1]})  # fmt: skip
        if stand != (a.teile or {}):
            a.teile = stand
            geaendert = True
        fertig = all((stand.get(t) or {}).get("stand") == ERLEDIGT for t in TEILE if getattr(a, t))
        if fertig and a.erledigt_am is None:
            a.erledigt_am = jetzt
            audit.record(session, actor=akteur, action="portal.abbau.erledigt",
                         subject=a.short_name,
                         detail={t: (stand.get(t) or {}).get("grund") for t in TEILE
                                 if getattr(a, t)})  # fmt: skip
            geaendert = True
    session.flush()
    return geaendert


# --- fuer die Hosts und das Inventar ------------------------------------------------------------
def _offen(a: AbbauAuftrag, teil: str) -> bool:
    return getattr(a, teil) and ((a.teile or {}).get(teil) or {}).get("stand") != ERLEDIGT


def netbox_loeschen(session: Session) -> list[dict]:
    """Loeschauftraege fuer den Agenten des NetBox-Hosts (`GET /netbox-host` → `loeschen`): nur
    offene, nie fuer einen Kurznamen, den es als Portal-Kunden gibt."""
    portal_kunden = set(session.scalars(select(PortalCustomer.short_name)))
    return [{"kurzname": a.short_name, "auftrag": a.kennung}
            for a in session.scalars(select(AbbauAuftrag).order_by(AbbauAuftrag.short_name))
            if _offen(a, "netbox") and a.short_name not in portal_kunden]  # fmt: skip


def tor_entfernen(session: Session) -> list[dict]:
    """Tor-Geheimnisse, die `front.sh sync` entfernen soll (`soll.json` → `tor_entfernen`): nur
    offene, nur mit Realm, nie ein Realm eines Portal-Kunden."""
    realms = {r for r in session.scalars(select(PortalCustomer.realm)) if r}
    return [{"realm": a.realm, "auftrag": a.kennung}
            for a in session.scalars(select(AbbauAuftrag).order_by(AbbauAuftrag.realm))
            if a.realm and _offen(a, "tor") and a.realm not in realms]  # fmt: skip


def stand(session: Session) -> list[dict]:
    """`ist.entfernen`: je Auftrag Gesamtstand, Grund (erster offener Teil) und Teile; dazu die
    zuletzt abgelehnten Eintraege (`abgelehnt`)."""
    out = []
    for a in session.scalars(select(AbbauAuftrag).order_by(AbbauAuftrag.short_name)):
        teile = {}
        for teil in TEILE:
            if getattr(a, teil):
                t = (a.teile or {}).get(teil) or {}
                teile[teil] = {"stand": t.get("stand") or OFFEN, "grund": t.get("grund")}
        offen = [t for t in teile.values() if t["stand"] != ERLEDIGT]
        out.append({
            "kurzname": a.short_name,
            "stand": OFFEN if offen else ERLEDIGT,
            "grund": offen[0]["grund"] if offen else None,
            "teile": teile,
            "angenommen": ist_stand.iso(a.angenommen_am),
            "erledigt": ist_stand.iso(a.erledigt_am),
        })  # fmt: skip
    row = session.scalar(select(IstMeldung).where(IstMeldung.quelle == ABGELEHNT_QUELLE))
    bekannt = {e["kurzname"] for e in out}
    for e in ((row.daten or {}).get("abgelehnt") or []) if row is not None else []:
        if isinstance(e, dict) and e.get("kurzname") not in bekannt:
            out.append({"kurzname": e.get("kurzname"), "stand": ABGELEHNT, "grund": e.get("grund"),
                        "teile": {}, "angenommen": None, "erledigt": None})  # fmt: skip
    return out
