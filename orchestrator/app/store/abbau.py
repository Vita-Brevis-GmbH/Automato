"""Abbau aus dem Cockpit (1.14.0, ADR 0059): Loeschauftraege mit Stand je Teil.

Anwender 2026-11-16 auf den Vorschlag „Abbau-Plan, Daten nie ohne Sicherung, Frist 7 Tage ‚zum
Entfernen vorgemerkt‘, für Dev ‚sofort‘“: „Einstellbar, aber sonst ok“. Das Cockpit sperrt einen
Kunden waehrend seiner Frist (`gesperrt`, `store/freigaben.py`); nach der Frist nimmt es ihn aus
`kunden[]` und schickt mit `PUT /freigaben` einen Loeschauftrag (`entfernen`), so lange, bis das
Inventar (`ist.entfernen`) meldet, dass er erledigt ist.

- **Nur Kunden aus dem Cockpit:** angenommen wird ein Kurzname, der nicht (mehr) in `kunden[]`
  steht und den das Cockpit selbst entfernt hat - in derselben Anfrage (vorher ein Kunde aus dem
  Cockpit) oder zuvor (letzter Eintrag `portal.customer.*` im Audit: `deleted` durch das Cockpit) -
  bzw. Teile eines bleibenden Kunden aus dem Cockpit (unten). Kunden aus der Oberflaeche bzw.
  Kurznamen, die Automato nie vom Cockpit hatte, nie (`abgelehnt`). Gibt es den Kurznamen wieder
  als Portal-Kunden, sind die Teile aufgehoben, deren Werkzeug er nutzt.
- **Automato aendert nichts auf den Hosts** (ADR 0050): die NetBox loescht der Agent des
  NetBox-Hosts (`GET /netbox-host` → `loeschen`, mit Sicherung vorher), das Tor-Geheimnis entfernt
  `front.sh sync` (`soll.json` → `tor_entfernen`). Beide pruefen noch einmal selbst und merken sich
  die Kennung des Auftrags - erledigt ist erledigt, auch wenn danach etwas mit diesem Namen neu
  entsteht.
- **Kunde in Automato:** nur der, den Automato fuer diesen Portal-Kunden angelegt hat
  (`zuordnung.kunde_anlegen`, ADR 0058: `ist_stand.angelegte_kunden` ueber `cockpit_customer`;
  beim Annehmen gemerkt, bevor die Freigaben den Portal-Kunden entfernen), und nur ohne Daten
  (`ist_stand.daten`) und ohne Zuordnung zu einem anderen Portal-Kunden - sonst bleibt er und der
  Teil bleibt offen (`hat_daten`, `zugeordnet`).
- **Stand je Teil** (`teile`): `offen` mit dem Grund, worauf er wartet, oder `erledigt`; erledigt
  bleibt erledigt. Fortgeschrieben bei jeder Anfrage des Cockpits (`anwenden`) und bei jeder
  Meldung des NetBox-Hosts (Teil `netbox`).

**Teile bleibender Kunden** (1.14.0, ADR 0059 Nachtrag; Anwender 2026-11-16: „dass das Cockpit
alles orchestriert, also die Tools zufügt aber auch entfernt“): ein Eintrag darf auch einen Kunden
nennen, der in `kunden[]` derselben Anfrage steht. Dann gilt ein Teil nur, wenn sein Werkzeug bei
diesem Kunden aus ist - `netbox` ohne `nb`, `automato_kunde` ohne `doc` (und ohne
`zuordnung.kunde_anlegen`), `tor` fuer den frueheren Realm des Kunden (Realm gewechselt bzw.
entfernt, `_frueherer_realm`). Ein Teil, dessen Werkzeug an ist, wird abgelehnt (`aktiv`, mit
`teil`); die uebrigen Teile desselben Eintrags gelten. Ist das Werkzeug wieder an, bevor der Teil
ausgefuehrt ist, endet der Auftrag fuer diesen Teil (`erledigt` mit Grund `aufgehoben`); schickt
das Cockpit ihn nach erneutem Abwaehlen wieder, gilt er wieder (neue Kennung). Ein schon
ausgefuehrter Teil, dessen Werkzeug wieder an ist, faellt aus dem Auftrag (die neue Instanz ist
eine andere). Gilt fuer jeden Auftrag, auch fuer den Abbau eines ganzen Kunden, dessen Kurzname
wieder als Portal-Kunde erscheint: nie ein Teil, dessen Werkzeug er nutzt.
"""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit
from app.audit import decode_detail
from app.config import Settings
from app.i18n import L
from app.models import (
    AbbauAuftrag,
    AuditLog,
    CockpitCustomer,
    Customer,
    IstMeldung,
    PortalCustomer,
)
from app.store import ist_stand, portal
from app.store.freigaben import AKTEUR, MAX_KUNDEN, FreigabenFehler, _fehler

OFFEN, ERLEDIGT, ABGELEHNT = "offen", "erledigt", "abgelehnt"
TEILE = ("netbox", "automato_kunde", "tor")
# Ablehnung eines Eintrags (Antwort von PUT /freigaben, ist.entfernen)
OBERFLAECHE = "oberflaeche"  # Kunde aus der Oberflaeche von Automato - nie
NICHT_VOM_COCKPIT = "nicht_vom_cockpit"  # nie ein Kunde aus dem Cockpit dieses Namens
SCHON_ERLEDIGT = "schon_erledigt"  # Abbau dieses Kunden schon erledigt (nie ein zweites Mal)
# Ablehnung eines Teils (bleibender Kunde): sein Werkzeug ist an - mit `teil` im Eintrag
AKTIV = "aktiv"
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
#: Teil endete, bevor er ausgefuehrt war: sein Werkzeug ist wieder an (erledigt mit diesem Grund)
AUFGEHOBEN = "aufgehoben"
#: Werkzeug des Kunden-Portals je Teil (das Tor haengt am Realm, `_aktiv`)
WERKZEUG = {"netbox": "nb", "automato_kunde": "doc"}
#: so weit liest `_frueherer_realm` den Verlauf eines Portal-Kunden im Audit zurueck
MAX_VERLAUF = 500
PORTAL_AKTIONEN = ("portal.customer.created", "portal.customer.updated",
                   "portal.customer.deleted")  # fmt: skip
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
    #: Kurznamen mit `zuordnung.kunde_anlegen` in der Anfrage - das Cockpit will den Kunden in
    #: Automato, der Teil `automato_kunde` ist also aktiv (auch ohne `doc`)
    anlegen: set[str] = field(default_factory=set)


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
    kunden = data.get("kunden") if isinstance(data, dict) else None
    kunden = [k for k in kunden if isinstance(k, dict) and isinstance(k.get("kurzname"), str)] \
        if isinstance(kunden, list) else []  # fmt: skip
    anfrage = {k["kurzname"].strip() for k in kunden}
    anlegen = {k["kurzname"].strip() for k in kunden if isinstance(k.get("zuordnung"), dict)
               and k["zuordnung"].get("kunde_anlegen") is True}  # fmt: skip
    if eintraege is None:
        return Plan(None, anfrage, anlegen=anlegen)
    namen = [e.kurzname for e in eintraege]
    vorher = {}
    if namen:
        stmt = select(PortalCustomer).where(PortalCustomer.short_name.in_(namen))
        for pc in session.scalars(stmt):
            angelegt = tuple(sorted(ist_stand.angelegte_kunden(session, pc.short_name)))
            vorher[pc.short_name] = Vorher(pc.source or portal.SOURCE_UI, pc.realm, angelegt)
    return Plan(eintraege, anfrage, vorher, anlegen)


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


def _frueherer_realm(session: Session, kurzname: str, aktuell: str | None) -> str | None:
    """Realm, den der Portal-Kunde vor seinem jetzigen hatte (bzw. zuletzt, wenn er jetzt keinen
    hat) - aus dem Verlauf `portal.customer.*` im Audit (Anlegen nennt den Realm, Aendern nur einen
    geaenderten, Entfernen den letzten). None = keiner bekannt."""
    rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.action.in_(PORTAL_AKTIONEN), AuditLog.subject == kurzname)
        .order_by(AuditLog.id.desc())
        .limit(MAX_VERLAUF)
    )
    for row in rows:
        realm = (decode_detail(row) or {}).get("realm")
        if isinstance(realm, str) and realm != aktuell and portal.REALM.fullmatch(realm):
            return realm
    return None


def _aktiv(teil: str, kurzname: str, pc: PortalCustomer | None, anlegen: set[str],
           realm: str | None) -> bool:  # fmt: skip
    """Nutzt der Portal-Kunde dieses Kurznamens das Werkzeug des Teils (bzw. beim Tor den Realm
    des Auftrags)? Dann wird dieser Teil nie abgebaut."""
    if pc is None:
        return False
    if teil == "tor":
        return bool(realm) and pc.realm == realm
    return bool(getattr(pc, WERKZEUG[teil])) or (teil == "automato_kunde" and kurzname in anlegen)


# --- Auftraege annehmen und beenden -------------------------------------------------------------
def _offene_teile(a: AbbauAuftrag) -> list[str]:
    return [t for t in TEILE if _offen(a, t)]


def _aufheben(session: Session, a: AbbauAuftrag, grund: str, akteur: str) -> None:
    """Auftrag beenden: offene Teile = aufgehoben (Audit), erledigte nur vergessen."""
    offen = _offene_teile(a)
    if offen:
        audit.record(session, actor=akteur, action="portal.abbau.aufgehoben", subject=a.short_name,
                     detail={"grund": grund, **{t: t in offen for t in TEILE}})  # fmt: skip
    session.delete(a)


def _wieder_aktiv(session: Session, a: AbbauAuftrag, pc: PortalCustomer | None, anlegen: set[str],
                  akteur: str) -> bool:  # fmt: skip
    """Teile, deren Werkzeug der Kunde dieses Kurznamens (wieder) nutzt, enden: offene sind
    aufgehoben (erledigt mit Grund `aufgehoben`, Audit), ausgefuehrte fallen aus dem Auftrag (die
    Instanz von jetzt ist eine andere). True = geaendert."""
    stand = dict(a.teile or {})
    aufgehoben: list[str] = []
    geaendert = False
    for teil in TEILE:
        if not getattr(a, teil) or not _aktiv(teil, a.short_name, pc, anlegen, a.realm):
            continue
        t = stand.get(teil) or {}
        if t.get("stand") == ERLEDIGT:
            if t.get("grund") != AUFGEHOBEN:
                setattr(a, teil, False)
                stand.pop(teil, None)
                geaendert = True
            continue
        stand[teil] = {"stand": ERLEDIGT, "grund": AUFGEHOBEN,
                       "zeit": _jetzt().isoformat(timespec="seconds")}  # fmt: skip
        aufgehoben.append(teil)
    if aufgehoben:
        detail = {"grund": WIEDER_DA, **{t: t in aufgehoben for t in TEILE}}
        audit.record(session, actor=akteur, action="portal.abbau.aufgehoben", subject=a.short_name,
                     detail=detail)  # fmt: skip
    if stand != (a.teile or {}):
        a.teile = stand
        geaendert = True
    return geaendert


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


def _teile_pruefen(
    session: Session, e: Eintrag, pc: PortalCustomer, a: AbbauAuftrag | None, plan: Plan,
    erg: Ergebnis,
) -> tuple[dict[str, bool], str | None, set[str], bool]:  # fmt: skip
    """Eintrag fuer einen Kunden, der in `kunden[]` bleibt: nur Teile, deren Werkzeug aus ist
    (sonst `aktiv`, in `erg.abgelehnt`). Liefert die geltenden Teile, den Realm fuer das Tor (den
    frueheren des Kunden), die abgelehnten Teile, die der Auftrag schon erledigt hat (sie bleiben
    sichtbar, z. B. `aufgehoben`, und werden nie wieder scharf), und ob der Eintrag gilt (nicht,
    wenn alle gesendeten Teile abgelehnt sind)."""
    gesendet = e.wuensche()
    wuensche = dict(gesendet)
    realm = None
    halten: set[str] = set()
    for teil in TEILE:
        if not wuensche[teil]:
            continue
        alt = ((a.teile or {}).get(teil) or {}) if a is not None else {}
        if teil == "tor":
            if a is not None and a.tor and a.realm and a.realm != pc.realm \
                    and alt.get("stand") != ERLEDIGT:  # fmt: skip
                realm = a.realm  # nie mitten im Abbau auf einen anderen Realm umstellen
            else:
                realm = _frueherer_realm(session, e.kurzname, pc.realm)
            aktiv = realm is None and bool(pc.realm)  # sein Realm ist noch sein Tor
        else:
            aktiv = _aktiv(teil, e.kurzname, pc, plan.anlegen, None)
        if not aktiv:
            continue
        erg.abgelehnt.append({"kurzname": e.kurzname, "grund": AKTIV, "teil": teil})
        if a is not None and getattr(a, teil) and alt.get("stand") == ERLEDIGT:
            halten.add(teil)
            if teil == "tor":
                realm = a.realm
        else:
            wuensche[teil] = False
    gilt = not any(gesendet.values()) or any(wuensche[t] for t in TEILE if t not in halten)
    return wuensche, realm, halten, gilt


def _nachfuehren(
    session: Session, a: AbbauAuftrag, wuensche: dict[str, bool], realm: str | None,
    angelegt: set[int], akteur: str, halten: set[str] | None = None,
) -> bool:  # fmt: skip
    """Bestehenden Auftrag auf die gesendeten Teile bringen. Ein Teil wird (wieder) scharf, wenn er
    neu dazukommt, nach `aufgehoben` erneut gesendet wird bzw. das Tor einen anderen Realm meint -
    dann mit neuer Kennung (die Hosts fuehren jede Kennung nur einmal aus); nie ein Teil in
    `halten` (Werkzeug an). True = geaendert."""
    halten = halten or set()
    stand = dict(a.teile or {})
    scharf: list[str] = []
    if wuensche["tor"] and "tor" not in halten and realm != a.realm:
        tor = stand.get("tor") or {}
        if not (a.tor and a.realm and tor.get("stand") != ERLEDIGT):
            a.realm = realm
            stand.pop("tor", None)
            scharf.append("tor")
    for teil in TEILE:
        t = stand.get(teil) or {}
        if not wuensche[teil] or teil in scharf or teil in halten:
            continue
        if not getattr(a, teil) and t.get("stand") != ERLEDIGT:
            stand.pop(teil, None)
            scharf.append(teil)
        elif getattr(a, teil) and t.get("stand") == ERLEDIGT and t.get("grund") == AUFGEHOBEN:
            stand.pop(teil, None)  # nach erneutem Abwaehlen wieder gesendet
            scharf.append(teil)
    geaendert = {t: getattr(a, t) for t in TEILE} != wuensche or bool(scharf)
    gemerkt = set(a.automato_kunden or [])
    if not angelegt <= gemerkt:
        a.automato_kunden = sorted(gemerkt | angelegt)
    if not geaendert:
        return a.automato_kunden != sorted(gemerkt)
    for teil, wert in wuensche.items():
        setattr(a, teil, wert)
    if scharf:
        a.kennung = secrets.token_hex(16)
        a.angenommen_am = _jetzt()
        a.erledigt_am = None
    a.teile = stand
    audit.record(session, actor=akteur, action="portal.abbau.geaendert", subject=a.short_name,
                 detail={**wuensche, "realm": a.realm})  # fmt: skip
    return True


def anwenden(session: Session, plan: Plan, settings: Settings, akteur: str = AKTEUR) -> Ergebnis:
    """Nach dem Anwenden der Freigaben (ohne commit): Auftraege annehmen, nachfuehren, beenden und
    alle offenen Teile fortschreiben."""
    erg = Ergebnis(gesendet=plan.eintraege is not None)
    session.flush()
    kunden = {pc.short_name: pc for pc in session.scalars(select(PortalCustomer))}
    auftraege = {a.short_name: a for a in session.scalars(select(AbbauAuftrag))}
    # 1. der Kunde dieses Kurznamens nutzt das Werkzeug (wieder): diesen Teil nie abbauen
    for kurz in sorted(auftraege):
        a = auftraege[kurz]
        if _wieder_aktiv(session, a, kunden.get(kurz), plan.anlegen, akteur):
            erg.geaendert = True
            if not any(getattr(a, t) for t in TEILE):  # alle Teile waren ausgefuehrt
                session.delete(auftraege.pop(kurz))
    if plan.eintraege is not None:
        gewollt: set[str] = set()
        for e in plan.eintraege:
            grund = None
            realm = None
            vorher = plan.vorher.get(e.kurzname)
            pc = kunden.get(e.kurzname)
            a = auftraege.get(e.kurzname)
            wuensche, halten, gilt = e.wuensche(), set(), True
            if e.kurzname in plan.anfrage:
                # bleibender Kunde: nur Teile, deren Werkzeug aus ist (nie aus der Oberflaeche)
                ui = vorher is not None and vorher.quelle != portal.SOURCE_COCKPIT
                if pc is None or pc.source != portal.SOURCE_COCKPIT or ui:
                    grund = OBERFLAECHE
                else:
                    wuensche, realm, halten, gilt = _teile_pruefen(session, e, pc, a, plan, erg)
                    if not gilt and a is None:
                        continue  # alle Teile aktiv, kein Auftrag
            elif pc is not None or (vorher and vorher.quelle != portal.SOURCE_COCKPIT):
                grund = OBERFLAECHE  # Kunden aus dem Cockpit sind nach dem Anwenden entfernt
            elif vorher is not None:
                realm = vorher.realm  # in dieser Anfrage entfernt: sein Realm
            elif a is None:
                grund, realm = _vom_cockpit_entfernt(session, e.kurzname)
            else:
                realm = a.realm
            if grund:
                erg.abgelehnt.append({"kurzname": e.kurzname, "grund": grund})
                continue
            gewollt.add(e.kurzname)  # auch ganz abgelehnt: der Auftrag bleibt sichtbar
            if gilt:
                erg.angenommen.append(e.kurzname)
            angelegt = set(vorher.angelegt if vorher else ())
            angelegt |= set(ist_stand.angelegte_kunden(session, e.kurzname))
            if a is None:
                a = AbbauAuftrag(short_name=e.kurzname, kennung=secrets.token_hex(16), realm=realm,
                                 automato_kunden=sorted(angelegt), teile={}, angenommen_am=_jetzt(),
                                 **wuensche)  # fmt: skip
                session.add(a)
                auftraege[e.kurzname] = a
                audit.record(session, actor=akteur, action="portal.abbau.angenommen",
                             subject=e.kurzname,
                             detail={**wuensche, "realm": realm})  # fmt: skip
                erg.geaendert = True
            else:
                erg.geaendert |= _nachfuehren(session, a, wuensche, realm, angelegt, akteur,
                                              halten)  # fmt: skip
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
def automato_kunde_entfernen(session: Session, kunde: Customer, akteur: str = AKTEUR,
                             eigen: PortalCustomer | None = None) -> str | None:  # fmt: skip
    """Einen Kunden in Automato entfernen, den Automato fuer einen Portal-Kunden aus dem Cockpit
    angelegt hat - nur ohne Daten und ohne Zuordnung zu einem (anderen) Portal-Kunden. None =
    entfernt (Audit `customer.deleted`, Akteur `cockpit`), sonst der Grund, warum er bleibt.

    `eigen` (Teil-Abbau): der bleibende Portal-Kunde, fuer den er angelegt wurde, ohne `doc` und
    mit Zuordnung aus Automato (das Cockpit sendet keine) - seine Zuordnung zaehlt nicht, die Id
    faellt dort mit weg. Nennt das Cockpit den Kunden selbst in `zuordnung.kunden`, bleibt er
    (`zugeordnet`): sonst waere seine naechste Anfrage ungueltig."""
    if ist_stand.daten(session, [kunde.id]).get(kunde.id):
        return HAT_DATEN
    zug = ist_stand.zugeordnet(session).get(kunde.id) or []
    if [k for k in zug if eigen is None or k != eigen.short_name]:
        return ZUGEORDNET
    if eigen is not None and kunde.id in (eigen.customer_ids or []):
        eigen.customer_ids = [c for c in eigen.customer_ids or [] if c != kunde.id]
    audit.record(session, actor=akteur, action="customer.deleted", subject=str(kunde.id),
                 detail={"tenant_id": kunde.tenant_id, "customer_id": kunde.id,
                         "name": kunde.name, "selectors": 0})  # fmt: skip
    # Verknuepfung `cockpit_customer` (ADR 0058) faellt mit - auch ohne FK (wie DELETE /customers)
    session.execute(delete(CockpitCustomer).where(CockpitCustomer.customer_id == kunde.id))
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


def _doch_geloescht(a: AbbauAuftrag, alt: dict, meldung: IstMeldung | None) -> bool:
    """Teil NetBox `aufgehoben`, aber der Agent hatte den Auftrag schon abgeholt: meldet er die
    Instanz danach als geloescht, gilt das (Werkzeug genau waehrend des Loeschens wieder gewaehlt).
    Nach dem Aufheben nennt Automato den Auftrag nicht mehr - ein spaeteres `geloescht` stammt also
    von diesem Auftrag."""
    if alt.get("grund") != AUFGEHOBEN or meldung is None:
        return False
    d = meldung.daten if isinstance(meldung.daten, dict) else {}
    try:
        seit = datetime.fromisoformat(str(alt.get("zeit")))
    except ValueError:
        return False
    return a.short_name in (d.get("geloescht") or []) and _utc(meldung.empfangen) > _utc(seit)


def _automato_kunde(session: Session, a: AbbauAuftrag, akteur: str,
                    aktiv: bool = False) -> tuple[str, str]:  # fmt: skip
    """Entfernt werden nur Kunden, die (noch) fuer diesen Portal-Kunden angelegt sind (Verknuepfung
    `cockpit_customer`, 1.14.0): eine beim Annehmen gemerkte Id ohne Verknuepfung ist weg - auch
    wenn es die Id wieder gibt (SQLite vergibt Ids geloeschter Kunden neu), nie diesen Kunden. Nie,
    solange der Portal-Kunde dieses Kurznamens `doc` hat (`aktiv`; Schutz, `anwenden` hebt den Teil
    dann schon auf)."""
    if aktiv:
        return OFFEN, AKTIV
    angelegt = set(ist_stand.angelegte_kunden(session, a.short_name))
    if not (set(a.automato_kunden or []) | angelegt):
        return ERLEDIGT, KEINER_ANGELEGT
    vorhanden = list(session.scalars(select(Customer).where(Customer.id.in_(angelegt))
                                     .order_by(Customer.id))) if angelegt else []  # fmt: skip
    if not vorhanden:
        return ERLEDIGT, NICHT_VORHANDEN
    pc = session.scalar(select(PortalCustomer).where(PortalCustomer.short_name == a.short_name))
    eigen = pc if pc is not None and pc.scope_source != portal.SCOPE_COCKPIT else None
    gruende = [g for g in (automato_kunde_entfernen(session, c, akteur, eigen) for c in vorhanden)
               if g]  # fmt: skip
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
    mit_doc = set(session.scalars(select(PortalCustomer.short_name)
                                  .where(PortalCustomer.doc.is_(True))))  # fmt: skip
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
            if teil not in teile:
                continue
            if alt.get("stand") == ERLEDIGT:
                if not (teil == "netbox" and _doch_geloescht(a, alt, meldung)):
                    continue
                neu = (ERLEDIGT, GELOESCHT)
            elif teil == "netbox":
                neu = _netbox(a, meldung, settings)
            elif teil == "automato_kunde":
                neu = _automato_kunde(session, a, akteur, a.short_name in mit_doc)
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
    offene, nie fuer einen Kurznamen, den es als Portal-Kunden mit NetBox gibt (ein bleibender Kunde
    ohne `nb` darf seine NetBox verlieren, Teil-Abbau). Der Agent prueft es noch einmal: er loescht
    nie einen Kunden, den `kunden` dieser Antwort nennt."""
    mit_nb = set(session.scalars(select(PortalCustomer.short_name)
                                 .where(PortalCustomer.nb.is_(True))))  # fmt: skip
    return [{"kurzname": a.short_name, "auftrag": a.kennung}
            for a in session.scalars(select(AbbauAuftrag).order_by(AbbauAuftrag.short_name))
            if _offen(a, "netbox") and a.short_name not in mit_nb]  # fmt: skip


def tor_entfernen(session: Session) -> list[dict]:
    """Tor-Geheimnisse, die `front.sh sync` entfernen soll (`soll.json` → `tor_entfernen`): nur
    offene, nur mit Realm, nie ein Realm eines Portal-Kunden."""
    realms = {r for r in session.scalars(select(PortalCustomer.realm)) if r}
    return [{"realm": a.realm, "auftrag": a.kennung}
            for a in session.scalars(select(AbbauAuftrag).order_by(AbbauAuftrag.realm))
            if a.realm and _offen(a, "tor") and a.realm not in realms]  # fmt: skip


def stand(session: Session) -> list[dict]:
    """`ist.entfernen`: je Auftrag Gesamtstand, Grund (erster offener Teil) und Teile; dazu die
    zuletzt abgelehnten Eintraege (`abgelehnt`) bzw. Teile (`aktiv`, in `teile` mit Stand
    `abgelehnt`)."""
    row = session.scalar(select(IstMeldung).where(IstMeldung.quelle == ABGELEHNT_QUELLE))
    ganz: dict[str, str] = {}
    teilweise: dict[str, dict[str, str]] = {}
    for e in ((row.daten or {}).get("abgelehnt") or []) if row is not None else []:
        if not isinstance(e, dict) or not isinstance(e.get("kurzname"), str):
            continue
        if e.get("teil") in TEILE:
            teilweise.setdefault(e["kurzname"], {})[e["teil"]] = e.get("grund")
        else:
            ganz.setdefault(e["kurzname"], e.get("grund"))
    out = []
    for a in session.scalars(select(AbbauAuftrag).order_by(AbbauAuftrag.short_name)):
        teile = {}
        for teil in TEILE:
            if getattr(a, teil):
                t = (a.teile or {}).get(teil) or {}
                teile[teil] = {"stand": t.get("stand") or OFFEN, "grund": t.get("grund")}
        for teil, grund in (teilweise.pop(a.short_name, None) or {}).items():
            teile.setdefault(teil, {"stand": ABGELEHNT, "grund": grund})
        offen = [t for t in teile.values() if t["stand"] == OFFEN]
        out.append({
            "kurzname": a.short_name,
            "stand": OFFEN if offen else ERLEDIGT,
            "grund": offen[0]["grund"] if offen else None,
            "teile": teile,
            "angenommen": ist_stand.iso(a.angenommen_am),
            "erledigt": ist_stand.iso(a.erledigt_am),
        })  # fmt: skip
    bekannt = {e["kurzname"] for e in out}
    for kurz in [*ganz, *teilweise]:
        if kurz in bekannt:
            continue
        bekannt.add(kurz)
        teile = {t: {"stand": ABGELEHNT, "grund": g} for t, g in teilweise.get(kurz, {}).items()}
        grund = ganz.get(kurz) or next((t["grund"] for t in teile.values()), None)
        out.append({"kurzname": kurz, "stand": ABGELEHNT, "grund": grund,
                    "teile": teile if kurz not in ganz else {}, "angenommen": None,
                    "erledigt": None})  # fmt: skip
    return out
