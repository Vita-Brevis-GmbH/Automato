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
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
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
#: Felder, deren Autor das Cockpit ist (alles andere bleibt in Automato)
FELDER = ("name", "doc", "nb", "um", "cmdb", "realm", "access", "access_nets")


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


@dataclass
class Ergebnis:
    angelegt: list[str] = field(default_factory=list)
    geaendert: list[str] = field(default_factory=list)
    uebernommen: list[str] = field(default_factory=list)
    entfernt: list[str] = field(default_factory=list)
    unveraendert: int = 0

    @property
    def etwas_geaendert(self) -> bool:
        return bool(self.angelegt or self.geaendert or self.uebernommen or self.entfernt)

    def antwort(self) -> dict:
        return {
            "vertrag": VERTRAG,
            "angelegt": self.angelegt,
            "geaendert": self.geaendert,
            "uebernommen": self.uebernommen,
            "entfernt": self.entfernt,
            "unveraendert": self.unveraendert,
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


def _freigabe(roh: dict) -> Freigabe:
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
             "access_nets": nets}  # fmt: skip
    return Freigabe(kurz, werte)


def pruefen(session: Session, data: object) -> list[Freigabe]:
    """Ganze Anfrage pruefen; wirft `FreigabenFehler` mit allen Gruenden (dann aendert sich
    nichts)."""
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
    for nr, roh in enumerate(kunden, start=1):
        if not isinstance(roh, dict):
            fehler.append(_fehler(None, L(f"Eintrag {nr} ist kein Objekt",
                                          f"entry {nr} is not an object")))  # fmt: skip
            continue
        try:
            f = _freigabe(roh)
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
    if fehler:
        raise FreigabenFehler(fehler)
    return out


def _wert(pc: PortalCustomer, feld: str) -> object:
    value = getattr(pc, feld)
    if feld == "access_nets":
        return list(value or [])
    if feld == "access":
        return value or "ueberall"
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
            pc = PortalCustomer(short_name=f.kurzname, links=[], customer_ids=[],
                                source=portal.SOURCE_COCKPIT, **f.werte)  # fmt: skip
            session.add(pc)
            action = "portal.customer.created"
        else:
            for key, value in f.werte.items():
                setattr(pc, key, value)
            pc.source = portal.SOURCE_COCKPIT
            action = "portal.customer.updated"
            if art == "uebernommen":
                detail["uebernommen"] = True
        pc.updated_by, pc.updated_at = akteur, jetzt
        audit.record(session, actor=akteur, action=action, subject=f.kurzname, detail=detail)
        getattr(erg, art).append(f.kurzname)
    session.flush()
    return erg


def uebernehmen(session: Session, data: object, akteur: str = AKTEUR) -> Ergebnis:
    """Anfrage pruefen und uebernehmen (ohne commit) - wirft `FreigabenFehler`."""
    return anwenden(session, pruefen(session, data), akteur)


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
