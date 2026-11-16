"""Freigaben aus dem Cockpit (1.11.0, ADR 0054): `PUT /freigaben`.

Das Kundenregister des Cockpits schickt den ganzen Stand seiner Kunden fuer diese Installation
(Vertrag im Cockpit: docs/vertraege/freigaben.md, v1); Automato fuehrt damit sein Kunden-Portal
nach (ADR 0050). Das Cockpit ist Autor fuer Kurzname, Name, Werkzeuge, Zugriff und Realm;
Mandant, Kunden, Standard-Rolle und weitere Kacheln pflegt Automato selbst.

- **Ganzer Stand, idempotent:** dieselbe Anfrage zweimal aendert nichts (kein Audit, der
  Soll-Stand bleibt).
- **Alles oder nichts:** ein ungueltiger Kunde oder ein Realm, der einem Kunden aus der
  Oberflaeche gehoert, aendert nichts (`FreigabenFehler` mit allen Gruenden).
- **Uebernahme:** gibt es den Kurznamen schon aus der Oberflaeche, wird die Herkunft Cockpit -
  Mandant, Kunden, Rolle und Kacheln bleiben.
- **Entfernen** nur von Kunden mit Herkunft Cockpit, die nicht mehr in der Anfrage stehen;
  Kunden aus der Oberflaeche bleiben unberuehrt.

Geprueft wird mit denselben Funktionen wie in der Oberflaeche (`store/portal.py`); `front.sh`
prueft den Soll-Stand danach noch einmal (Vertrauensgrenze). Ein Eingang in Automatos eigene
Datenbank - kein Schreibweg nach aussen (Nur-Lese-Regel unberuehrt).

Seit 1.13.0 (ADR 0055) optional `tor_geheimnisse`: je Realm eines Kunden der Anfrage das
Client-Secret des Anmelde-Tors (`automato-front`), versiegelt mit dem Empfangsschluessel der VM.
Automato prueft nur die Form (alles oder nichts wie bei `kunden`) und die Kennung des Schluessels
(eine veraltete lehnt nur diesen Eintrag ab) und haelt nur das Chiffrat
(`portal_customer.tor_secret`, im Soll-Stand `tor_geheimnisse`) - entschluesselt wird nur in
`front.sh sync`.

Seit 1.13.0 (ADR 0054 Nachtrag) je Kunde optional `zuordnung` (Mandant, Kunden, Rolle): dann ist
das Cockpit Autor dieser drei Felder (`scope_source = cockpit`, in Automato nur lesbar); ohne
`zuordnung` bleibt der Stand in Automato und die Felder sind dort wieder aenderbar.
"""

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import capabilities as caps
from app.i18n import L
from app.models import PortalCustomer
from app.store import portal

#: Fassung der Anfrage und Antwort (Vertrag v1)
VERTRAG = 1
MAX_KUNDEN = 1000
#: Werkzeuge des Kundenpfads (Adresse `<kurzname>-<werkzeug>.<domain>`)
WERKZEUGE = ("doc", "nb", "um", "cmdb")
#: Akteur im Audit
AKTEUR = "cockpit"
#: Felder, deren Autor das Cockpit ist (alles andere bleibt in Automato); mit `zuordnung` auch
#: Mandant, Kunden und Standard-Rolle (1.13.0) - `scope_source` haelt fest, wer sie setzt
FELDER = ("name", "doc", "nb", "um", "cmdb", "realm", "access", "access_nets", "scope_source")
FELDER_ZUORDNUNG = ("tenant_id", "customer_ids", "default_role")
MAX_ZUORDNUNG_KUNDEN = 2000
#: versiegelte Client-Secrets (1.13.0, ADR 0055): Kennung des Empfangsschluessels, Chiffrat
KENNUNG = re.compile(r"[0-9a-f]{64}")
BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
SIEGEL_MAX = 1024
#: Gruende, aus denen ein Eintrag nicht uebernommen wird (die Anfrage scheitert deshalb nicht)
SCHLUESSEL_VERALTET, KEIN_EMPFANG = "schluessel_veraltet", "kein_empfang"


class FreigabenFehler(ValueError):
    """Anfrage ungueltig - nichts geaendert. `fehler` = [{"kurzname", "grund"}]."""

    def __init__(self, fehler: list[dict]):
        super().__init__("; ".join(f"{f['kurzname']}: {f['grund']}" for f in fehler))
        self.fehler = fehler


@dataclass(frozen=True)
class Freigabe:
    """Ein gepruefter Kunde aus der Anfrage: Kurzname und die Werte der `FELDER`."""

    kurzname: str
    werte: dict


@dataclass(frozen=True)
class TorGeheimnis:
    """Versiegeltes Client-Secret des Anmelde-Tors fuer den Realm eines Kunden der Anfrage."""

    realm: str
    kurzname: str
    kennung: str
    versiegelt: str

    def wert(self) -> dict:
        return {"realm": self.realm, "kennung": self.kennung, "versiegelt": self.versiegelt}


@dataclass
class Ergebnis:
    angelegt: list[str] = field(default_factory=list)
    geaendert: list[str] = field(default_factory=list)
    uebernommen: list[str] = field(default_factory=list)
    entfernt: list[str] = field(default_factory=list)
    unveraendert: int = 0
    #: 1.13.0: {"uebernommen": [Realm], "abgelehnt": [{"realm", "grund"}]}; None = nicht gesendet
    tor: dict | None = None
    tor_geaendert: bool = False

    @property
    def etwas_geaendert(self) -> bool:
        return bool(self.angelegt or self.geaendert or self.uebernommen or self.entfernt
                    or self.tor_geaendert)  # fmt: skip

    def antwort(self) -> dict:
        return {
            "vertrag": VERTRAG,
            "angelegt": self.angelegt,
            "geaendert": self.geaendert,
            "uebernommen": self.uebernommen,
            "entfernt": self.entfernt,
            "unveraendert": self.unveraendert,
            "tor_geheimnisse": self.tor,
        }


def _fehler(kurzname: object, grund: str) -> dict:
    name = kurzname[:60] if isinstance(kurzname, str) and kurzname.strip() else None
    return {"kurzname": name, "grund": grund}


def _name(value: object) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > 200 or any(c in name for c in "\t\r\n"):
        raise portal.PortalError(L(
            "Name fehlt, ist länger als 200 Zeichen oder enthält Tabulatoren/Umbrüche",
            "Name missing, longer than 200 characters or contains tabs/line breaks",
        ))  # fmt: skip
    return name


def _werkzeuge(value: object) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise portal.PortalError(L("werkzeuge ist keine Liste", "werkzeuge is not a list"))
    out: set[str] = set()
    for tool in value:
        if not isinstance(tool, str) or tool not in WERKZEUGE:
            raise portal.PortalError(L(
                f"Werkzeug '{str(tool)[:20]}' unbekannt ({', '.join(WERKZEUGE)})",
                f"Tool '{str(tool)[:20]}' unknown ({', '.join(WERKZEUGE)})",
            ))  # fmt: skip
        out.add(tool)
    return out


def _id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _zuordnung(session: Session, roh: object) -> dict:
    """`zuordnung` eines Kunden pruefen (1.13.0): bestehender Mandant, Kunden nur dieses
    Mandanten (eindeutig, darf leer sein), Kunden-Rolle (nie admin). Ohne bzw. null: keine
    Aussage - die Felder bleiben in Automato aenderbar."""
    if roh is None:
        return {"scope_source": portal.SCOPE_AUTOMATO}
    if not isinstance(roh, dict):
        raise portal.PortalError(L("zuordnung ist kein Objekt", "zuordnung is not an object"))
    mandant, kunden, rolle = roh.get("mandant"), roh.get("kunden"), roh.get("rolle")
    if not _id(mandant):
        raise portal.PortalError(L("zuordnung: mandant ist keine Id (Zahl > 0)",
                                   "zuordnung: mandant is not an id (number > 0)"))  # fmt: skip
    if kunden is None:
        kunden = []
    if not isinstance(kunden, list) or len(kunden) > MAX_ZUORDNUNG_KUNDEN:
        raise portal.PortalError(L(
            f"zuordnung: kunden ist keine Liste (höchstens {MAX_ZUORDNUNG_KUNDEN})",
            f"zuordnung: kunden is not a list (at most {MAX_ZUORDNUNG_KUNDEN})"))  # fmt: skip
    if not all(_id(k) for k in kunden) or len(set(kunden)) != len(kunden):
        raise portal.PortalError(L("zuordnung: kunden nur eindeutige Ids (Zahl > 0)",
                                   "zuordnung: kunden only unique ids (number > 0)"))  # fmt: skip
    if not isinstance(rolle, str) or rolle not in caps.CUSTOMER_ROLES:
        raise portal.PortalError(L(
            f"zuordnung: Rolle '{str(rolle)[:20]}' unbekannt ({', '.join(caps.CUSTOMER_ROLES)})",
            f"zuordnung: role '{str(rolle)[:20]}' unknown ({', '.join(caps.CUSTOMER_ROLES)})",
        ))  # fmt: skip
    ids = portal.check_scope(session, mandant, kunden)
    return {"tenant_id": mandant, "customer_ids": ids, "default_role": rolle,
            "scope_source": portal.SCOPE_COCKPIT}  # fmt: skip


def _freigabe(session: Session, roh: dict) -> Freigabe:
    """Einen Kunden der Anfrage pruefen (wirft PortalError)."""
    kurz_roh = roh.get("kurzname")
    if not isinstance(kurz_roh, str):
        raise portal.PortalError(L("Kurzname fehlt", "Short name missing"))
    kurz = portal.check_short_name(kurz_roh)
    name = _name(roh.get("name"))
    tools = _werkzeuge(roh.get("werkzeuge"))
    zugriff, netze = roh.get("zugriff"), roh.get("netze")
    if zugriff is not None and not isinstance(zugriff, str):
        raise portal.PortalError(L("zugriff ist kein Text", "zugriff is not a string"))
    if netze is not None and not isinstance(netze, list):
        raise portal.PortalError(L("netze ist keine Liste", "netze is not a list"))
    access, nets = portal.check_access(zugriff, netze)
    realm = roh.get("realm")
    if realm is not None and not isinstance(realm, str):
        raise portal.PortalError(L("realm ist kein Text", "realm is not a string"))
    werte = {"name": name, "doc": "doc" in tools, "nb": "nb" in tools, "um": "um" in tools,
             "cmdb": "cmdb" in tools, "realm": portal.check_realm(realm), "access": access,
             "access_nets": nets, **_zuordnung(session, roh.get("zuordnung"))}  # fmt: skip
    return Freigabe(kurz, werte)


def _siegel_ok(value: object) -> bool:
    """Base64 (Standard, mit Padding), hoechstens `SIEGEL_MAX` Zeichen."""
    if not isinstance(value, str) or not value or len(value) > SIEGEL_MAX or len(value) % 4:
        return False
    if not BASE64.fullmatch(value):
        return False
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _tor_geheimnisse(roh: object, realms: dict[str, str], alle_realms: set[str],
                     anzahl: int, fehler: list[dict]) -> list[TorGeheimnis] | None:  # fmt: skip
    """`tor_geheimnisse` der Anfrage pruefen (Form und Realm eines Kunden derselben Anfrage);
    Fehler kommen in `fehler` (alles oder nichts). None = Feld fehlt bzw. null."""
    if roh is None:
        return None
    if not isinstance(roh, list):
        fehler.append(_fehler(None, L("tor_geheimnisse ist keine Liste",
                                      "tor_geheimnisse is not a list")))  # fmt: skip
        return None
    if len(roh) > anzahl:
        fehler.append(_fehler(None, L(
            f"tor_geheimnisse: höchstens so viele Einträge wie Kunden ({anzahl})",
            f"tor_geheimnisse: at most as many entries as customers ({anzahl})")))  # fmt: skip
        return None
    out: list[TorGeheimnis] = []
    gesehen: set[str] = set()
    for nr, e in enumerate(roh, start=1):
        if not isinstance(e, dict):
            fehler.append(_fehler(None, L(
                f"tor_geheimnisse: Eintrag {nr} ist kein Objekt",
                f"tor_geheimnisse: entry {nr} is not an object")))  # fmt: skip
            continue
        realm = e.get("realm")
        kurz = realms.get(realm) if isinstance(realm, str) else None
        if kurz is None:
            # Realm eines ungueltigen Kunden: dessen Fehler steht schon in der Liste
            if not (isinstance(realm, str) and realm in alle_realms):
                name = str(realm)[:64]
                fehler.append(_fehler(None, L(
                    f"tor_geheimnisse: Realm '{name}' gehört zu keinem Kunden der Anfrage",
                    f"tor_geheimnisse: realm '{name}' belongs to no customer of the request",
                )))  # fmt: skip
            continue
        if realm in gesehen:
            fehler.append(_fehler(kurz, L(f"tor_geheimnisse: Realm '{realm}' doppelt",
                                          f"tor_geheimnisse: realm '{realm}' twice")))  # fmt: skip
            continue
        gesehen.add(realm)
        kennung, versiegelt = e.get("kennung"), e.get("versiegelt")
        if not isinstance(kennung, str) or not KENNUNG.fullmatch(kennung):
            fehler.append(_fehler(kurz, L(
                f"tor_geheimnisse '{realm}': kennung ist kein SHA-256 (64 Zeichen hex, klein)",
                f"tor_geheimnisse '{realm}': kennung is not a SHA-256 (64 lowercase hex)",
            )))  # fmt: skip
            continue
        if not _siegel_ok(versiegelt):
            fehler.append(_fehler(kurz, L(
                f"tor_geheimnisse '{realm}': versiegelt ist kein Base64 (höchstens "
                f"{SIEGEL_MAX} Zeichen)",
                f"tor_geheimnisse '{realm}': versiegelt is not base64 (at most {SIEGEL_MAX} "
                "characters)")))  # fmt: skip
            continue
        out.append(TorGeheimnis(realm, kurz, kennung, versiegelt))
    return out


def pruefen(session: Session, data: object) -> list[Freigabe]:
    """Ganze Anfrage pruefen; wirft `FreigabenFehler` mit allen Gruenden (dann aendert sich
    nichts)."""
    return _pruefen(session, data)[0]


def _pruefen(session: Session, data: object) -> tuple[list[Freigabe], list[TorGeheimnis] | None]:
    """Wie `pruefen`, dazu die versiegelten Client-Secrets (None = nicht gesendet)."""
    if not isinstance(data, dict):
        raise FreigabenFehler([_fehler(None, L("Anfrage ist kein JSON-Objekt",
                                               "request is not a JSON object"))])  # fmt: skip
    vertrag = data.get("vertrag")
    if isinstance(vertrag, bool) or vertrag != VERTRAG:
        raise FreigabenFehler([_fehler(None, L(
            f"vertrag {str(vertrag)[:20]} nicht unterstützt (erwartet {VERTRAG})",
            f"vertrag {str(vertrag)[:20]} not supported (expected {VERTRAG})"))])  # fmt: skip
    kunden = data.get("kunden")
    if not isinstance(kunden, list):
        raise FreigabenFehler([_fehler(None, L("kunden ist keine Liste", "kunden is not a list"))])
    if len(kunden) > MAX_KUNDEN:
        raise FreigabenFehler([_fehler(None, L(f"höchstens {MAX_KUNDEN} Kunden",
                                               f"at most {MAX_KUNDEN} customers"))])  # fmt: skip
    fehler: list[dict] = []
    out: list[Freigabe] = []
    kurznamen: set[str] = set()
    realms: dict[str, str] = {}
    alle_realms = {r.get("realm") for r in kunden if isinstance(r, dict)
                   and isinstance(r.get("realm"), str)}  # fmt: skip
    for nr, roh in enumerate(kunden, start=1):
        if not isinstance(roh, dict):
            fehler.append(_fehler(None, L(f"Eintrag {nr} ist kein Objekt",
                                          f"entry {nr} is not an object")))  # fmt: skip
            continue
        try:
            f = _freigabe(session, roh)
        except portal.PortalError as exc:
            fehler.append(_fehler(roh.get("kurzname"), str(exc)))
            continue
        if f.kurzname in kurznamen:
            fehler.append(_fehler(f.kurzname, L("Kurzname doppelt in der Anfrage",
                                                "short name twice in the request")))  # fmt: skip
            continue
        realm = f.werte["realm"]
        if realm and realm in realms:
            fehler.append(_fehler(f.kurzname, L(
                f"Realm '{realm}' doppelt in der Anfrage (auch '{realms[realm]}')",
                f"realm '{realm}' twice in the request (also '{realms[realm]}')")))  # fmt: skip
            continue
        kurznamen.add(f.kurzname)
        if realm:
            realms[realm] = f.kurzname
        out.append(f)
    # Realm eines Kunden aus der Oberflaeche, der nicht in der Anfrage steht (bleibt also, wie
    # er ist): nicht wegnehmen - Kunden aus dem Cockpit, die wegfallen, geben ihren frei
    for pc in session.scalars(select(PortalCustomer).where(PortalCustomer.realm.is_not(None))):
        owner = realms.get(pc.realm or "")
        if owner and owner != pc.short_name and pc.short_name not in kurznamen \
                and pc.source != portal.SOURCE_COCKPIT:  # fmt: skip
            fehler.append(_fehler(owner, L(
                f"Realm '{pc.realm}' gehört schon zu '{pc.short_name}' (Kunde aus der "
                "Oberfläche von Automato)",
                f"realm '{pc.realm}' already belongs to '{pc.short_name}' (customer from the "
                "Automato UI)")))  # fmt: skip
    tor = _tor_geheimnisse(data.get("tor_geheimnisse"), realms, alle_realms, len(kunden), fehler)
    if fehler:
        raise FreigabenFehler(fehler)
    return out, tor


def _wert(pc: PortalCustomer, feld: str) -> object:
    value = getattr(pc, feld)
    if feld == "access_nets":
        return list(value or [])
    if feld == "access":
        return value or "ueberall"
    if feld == "customer_ids":
        return sorted(int(c) for c in value or [])
    if feld == "scope_source":
        return value or portal.SCOPE_AUTOMATO
    return value


def anwenden(session: Session, freigaben: list[Freigabe], akteur: str = AKTEUR) -> Ergebnis:
    """Gepruefte Freigaben uebernehmen (ohne commit). Audit je Aenderung mit Akteur `cockpit`
    und `quelle: cockpit` im Detail - wie die Oberflaeche (`portal.customer.*`)."""
    erg = Ergebnis()
    vorhanden = {pc.short_name: pc for pc in session.scalars(select(PortalCustomer))}
    gewollt = {f.kurzname for f in freigaben}
    jetzt = datetime.now(UTC)
    # 1. Kunden aus dem Cockpit, die nicht mehr in der Anfrage stehen
    for kurz in sorted(vorhanden):
        pc = vorhanden[kurz]
        if pc.source == portal.SOURCE_COCKPIT and kurz not in gewollt:
            audit.record(session, actor=akteur, action="portal.customer.deleted", subject=kurz,
                         detail={"realm": pc.realm, "quelle": portal.SOURCE_COCKPIT})  # fmt: skip
            session.delete(pc)
            erg.entfernt.append(kurz)
    # 2. Plan je Kunde: was sich aendert (gegen den Stand vor dieser Anfrage)
    plan: list[tuple[Freigabe, PortalCustomer | None, dict, str]] = []
    for f in freigaben:
        pc = vorhanden.get(f.kurzname)
        if pc is None:
            plan.append((f, None, dict(f.werte), "angelegt"))
            continue
        changed = {k: v for k, v in f.werte.items() if _wert(pc, k) != v}
        if pc.source != portal.SOURCE_COCKPIT:
            plan.append((f, pc, changed, "uebernommen"))
        elif changed:
            plan.append((f, pc, changed, "geaendert"))
        else:
            erg.unveraendert += 1
    # 3. Realms, die den Kunden wechseln, erst freigeben (eindeutig auch beim Tauschen)
    for _, pc, changed, _ in plan:
        if pc is not None and "realm" in changed and pc.realm:
            pc.realm = None
    session.flush()
    # 4. uebernehmen
    for f, pc, changed, art in plan:
        detail = {**changed, "quelle": portal.SOURCE_COCKPIT}
        if pc is None:
            pc = PortalCustomer(short_name=f.kurzname, links=[],
                                source=portal.SOURCE_COCKPIT,
                                **{"customer_ids": [], **f.werte})  # fmt: skip
            session.add(pc)
            action = "portal.customer.created"
        else:
            for key, value in f.werte.items():
                setattr(pc, key, value)
            if "realm" in changed:  # das Siegel gilt nur fuer den Realm, fuer den es kam
                pc.tor_secret = None
            pc.source = portal.SOURCE_COCKPIT
            action = "portal.customer.updated"
            if art == "uebernommen":
                detail["uebernommen"] = True
        pc.updated_by, pc.updated_at = akteur, jetzt
        audit.record(session, actor=akteur, action=action, subject=f.kurzname, detail=detail)
        getattr(erg, art).append(f.kurzname)
    session.flush()
    return erg


def tor_anwenden(session: Session, eintraege: list[TorGeheimnis], empfang: str | None,
                 erg: Ergebnis, akteur: str = AKTEUR) -> None:  # fmt: skip
    """Versiegelte Client-Secrets uebernehmen (nach `anwenden`, ohne commit): nur mit der Kennung
    des aktuellen Empfangsschluessels, sonst abgelehnt. Audit nur bei neuem Chiffrat - im Detail
    Realm und Kennung, nie das Chiffrat."""
    erg.tor = {"uebernommen": [], "abgelehnt": []}
    if not eintraege:
        return
    kunden = {pc.short_name: pc for pc in session.scalars(
        select(PortalCustomer).where(PortalCustomer.short_name.in_([e.kurzname for e in eintraege]))
    )}  # fmt: skip
    for e in eintraege:
        pc = kunden.get(e.kurzname)
        if not empfang:
            erg.tor["abgelehnt"].append({"realm": e.realm, "grund": KEIN_EMPFANG})
            continue
        if e.kennung != empfang:
            erg.tor["abgelehnt"].append({"realm": e.realm, "grund": SCHLUESSEL_VERALTET})
            continue
        if pc is None or pc.realm != e.realm:  # nach `anwenden` nicht moeglich
            continue
        erg.tor["uebernommen"].append(e.realm)
        if pc.tor_secret == e.wert():
            continue
        pc.tor_secret = e.wert()
        audit.record(session, actor=akteur, action="portal.tor_secret.updated",
                     subject=e.kurzname,
                     detail={"realm": e.realm, "kennung": e.kennung})  # fmt: skip
        erg.tor_geaendert = True
    session.flush()


def uebernehmen(session: Session, data: object, akteur: str = AKTEUR,
                empfang: str | None = None) -> Ergebnis:  # fmt: skip
    """Anfrage pruefen und uebernehmen (ohne commit) - wirft `FreigabenFehler`. `empfang` =
    Kennung des aktuellen Empfangsschluessels der VM (aus dem Front-Status, None = keiner)."""
    freigaben, tor = _pruefen(session, data)
    erg = anwenden(session, freigaben, akteur)
    if tor is not None:
        tor_anwenden(session, tor, empfang, erg, akteur)
    return erg


def front(state_dir: str, hinweis: str | None = None) -> dict:
    """Ob der Front-Proxy den Soll-Stand schon uebernommen hat (`aktuell`) bzw. warum nicht."""
    if hinweis:
        return {"aktuell": None, "hinweis": hinweis}
    stand = portal.agent_status(state_dir)
    if not stand["configured"]:
        return {"aktuell": None, "hinweis": L(
            "Kein Front-Proxy eingerichtet (AUTOMATO_FRONT_STATE_DIR leer) – Kunden nur in der "
            "Datenbank von Automato",
            "No front proxy set up (AUTOMATO_FRONT_STATE_DIR empty) – customers only in the "
            "Automato database")}  # fmt: skip
    pending = stand.get("pending")
    if pending is None:
        return {"aktuell": None, "hinweis": None}
    status = stand.get("status") or {}
    if not pending and status.get("ok") is False:
        meldung = str(status.get("meldung") or "")[:300]
        hinweis = L(f"Host-Agent meldet einen Fehler: {meldung}",
                    f"Host agent reports an error: {meldung}")  # fmt: skip
        return {"aktuell": False, "hinweis": hinweis}
    return {"aktuell": not pending, "hinweis": None}
