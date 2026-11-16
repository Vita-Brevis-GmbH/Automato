"""Inventar fuers Cockpit (1.10.0, ADR 0053): was diese Automato-Installation betreibt.

Der Front-Proxy (`front.sh erzeugen`, auch ueber den Host-Agenten) schreibt seine Adressen und
Ziele nach `topologie.json` in den Ordner des Host-Agenten; hier kommen Version, Umgebung und
Keycloak dazu. Das Cockpit holt das mit einem eigenen Token (`GET /inventory`) und fuehrt daraus
sein Inventar nach (Hosts, Instanzen je Kunde). Keine Geheimnisse und keine Fachdaten: nur
Namen und Adressen, jeder Wert geprueft und gekuerzt - die Datei schreibt root auf dem Host,
gelesen wird sie trotzdem wie fremde Eingabe.

Seit 1.11.0 (ADR 0054, Gegenrichtung der Freigaben) je Kunde auch Herkunft (`quelle`),
Werkzeuge, Zugriff, Netze und Realm - so uebernimmt das Cockpit Kunden, die es noch nicht kennt,
ohne Realm oder Zugriff zu verlieren. Vertrag bleibt v1 (nur neue optionale Felder).

Seit 1.13.0 (ADR 0055) unter `front` auch der Empfangsschluessel der VM (`tor_empfang`: nur der
oeffentliche RSA-Schluessel und seine Kennung) - damit versiegelt das Cockpit das Client-Secret des
Anmelde-Tors - und je Realm, ob ein Client-Secret da ist und ob es aus dem Cockpit kam
(`tor_realms`, aus `status.json` des Host-Agenten).

Ebenfalls seit 1.13.0 (ADR 0054 Nachtrag) die Auswahl fuer die Zuordnung im Cockpit: alle
Mandanten mit ihren Kunden (`automato.mandanten`, nur Id und Name) und die Kunden-Rollen
(`automato.rollen`); je Kunde, ob er in Automato zugeordnet ist (`automato_zugeordnet`) und die
Zuordnung im Kunden-Portal (`zuordnung`).

Ebenfalls seit 1.13.0 (ADR 0054 zweiter Nachtrag) je Kunde die weiteren Kacheln der Startseite
(`kacheln`: Titel, Adresse, Text - Grenzen wie in `PUT /freigaben`) und wer sie setzt
(`kacheln_von`: `cockpit` | `automato`) - so verliert das Cockpit beim Uebernehmen keine Kacheln.
Ohne Eintrag im Kunden-Portal beides `null` (unbekannt).

Seit 1.14.0 (ADR 0058) je Kunde in `zuordnung` der Kunde, den Automato fuer das Cockpit im
Mandanten angelegt hat (`kunde_angelegt`, Id oder null), und die Benutzer mit Rolle aus dem
Cockpit (`benutzer`: aktueller Stand bzw. null = keine Aussage).

Ebenfalls seit 1.14.0 (ADR 0059) der Ist-Stand der Instanzen (`ist`: Front-Proxy, NetBox-Host,
Kunden in Automato, Loeschauftraege - `store/ist_stand.py`) und je Kunde, ob der Front-Proxy ihn
sperrt (`gesperrt`, aus `topologie.json`; null bei einem aelteren front.sh).
"""

import hashlib
import ipaddress
import json
import re
import unicodedata
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.auth import capabilities as caps
from app.config import Settings
from app.models import Customer, PortalCustomer, Tenant
from app.store import ist_stand, portal, verwaltung

TOPOLOGIE = "topologie.json"
#: Fassung der Antwort (Vertrag im Cockpit: docs/vertraege/inventar.md)
VERTRAG = 1
_KUNDE = ("kurzname", "name", "startseite", "netbox", "automato", "magister", "cmdb")
_ADRESSE = re.compile(r"^[0-9A-Fa-f.:]{2,45}$")
#: Herkunft je Kunde: Cockpit (Freigaben), Oberflaeche (Kunden-Portal), Befehl (front.sh kunde)
_QUELLEN = ("cockpit", "oberflaeche", "befehl")
_WERKZEUGE = ("doc", "nb", "um", "cmdb")
#: Siegel der Client-Secrets aus dem Cockpit (ADR 0055): RSA-OAEP mit SHA-256 und MGF1-SHA-256
TOR_ALG = "RSA-OAEP-256"
TOR_PEM_MAX = 1000
TOR_BITS_MIN = 3072
KENNUNG = re.compile(r"[0-9a-f]{64}")
#: Auswahl fuer die Zuordnung (1.13.0): Grenzen wie im Vertrag
MAX_MANDANTEN, MAX_KUNDEN_JE_MANDANT, MAX_NAME = 500, 2000, 200


def _datei(state_dir: str, name: str) -> dict:
    """JSON-Datei des Host-Agenten (schreibt root) - gelesen wie fremde Eingabe."""
    if not state_dir:
        return {}
    try:
        data = json.loads((Path(state_dir) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _topologie(state_dir: str) -> dict:
    return _datei(state_dir, TOPOLOGIE)


def _realm(value: object) -> str | None:
    ok = isinstance(value, str) and portal.REALM.fullmatch(value) and value != "master"
    return value if ok else None


def _tor_empfang(topo: dict) -> dict | None:
    """Empfangsschluessel fuer Client-Secrets aus dem Cockpit (1.13.0, ADR 0055): nur mit
    Anmelde-Tor, nur ein oeffentlicher RSA-Schluessel (mindestens 3072 Bit), Kennung = SHA-256
    ueber seine DER-Form - sonst null."""
    roh = topo.get("tor_empfang")
    if topo.get("tor") is not True or not isinstance(roh, dict) or roh.get("alg") != TOR_ALG:
        return None
    pem, kennung = roh.get("pem"), roh.get("kennung")
    if not isinstance(pem, str) or len(pem) > TOR_PEM_MAX or not pem.isascii():
        return None
    if not isinstance(kennung, str) or not KENNUNG.fullmatch(kennung):
        return None
    if not pem.startswith("-----BEGIN PUBLIC KEY-----"):
        return None
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return None
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < TOR_BITS_MIN:
        return None
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if hashlib.sha256(der).hexdigest() != kennung:
        return None
    return {"alg": TOR_ALG, "pem": pem, "kennung": kennung}


def empfang_kennung(state_dir: str) -> str | None:
    """Kennung des aktuellen Empfangsschluessels der VM (`PUT /freigaben` vergleicht die
    Kennung der versiegelten Client-Secrets damit); None = keiner bzw. Anmelde-Tor aus."""
    empfang = _tor_empfang(_topologie(state_dir))
    return empfang["kennung"] if empfang else None


def _tor_realms(state_dir: str) -> dict | None:
    """Je Realm, ob ein Client-Secret da ist und ob es zuletzt aus dem Cockpit kam (1.13.0,
    aus `status.json` des Host-Agenten); null, wenn unbekannt."""
    tor = _datei(state_dir, portal.STATUS).get("tor")
    if not isinstance(tor, dict):
        return None

    def realms(feld: str) -> list[str]:
        werte = tor.get(feld)
        if not isinstance(werte, list):
            return []
        return sorted({r for r in werte[:1000] if _realm(r)})

    return {
        "bereit": realms("realms_bereit"),
        "ohne_secret": realms("realms_ohne_secret"),
        "vom_cockpit": realms("vom_cockpit"),
    }


def _text(value: object, laenge: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(value) <= laenge and value.isprintable() else None


def _front(topo: dict, state_dir: str = "") -> dict | None:
    if not topo:
        return None
    vm = topo.get("vm") if isinstance(topo.get("vm"), dict) else {}
    adressen = vm.get("adressen") if isinstance(vm.get("adressen"), list) else []
    return {
        "stand": _text(topo.get("zeit"), 40),
        "vm": {
            "name": _text(vm.get("name"), 253),
            "adressen": [a for a in adressen if isinstance(a, str) and _ADRESSE.match(a)][:16],
        },
        "portal": _text(topo.get("portal")),
        "domain": _text(topo.get("domain"), 253),
        "netbox_host": _text(topo.get("netbox_host"), 260),
        "uebrige": _text(topo.get("uebrige"), 260),
        "magister": _text(topo.get("magister")),
        "cmdb": _text(topo.get("cmdb")),
        "tor": topo.get("tor") is True,
        # 1.13.0 (ADR 0055): Client-Secrets des Anmelde-Tors versiegelt aus dem Cockpit
        "tor_empfang": _tor_empfang(topo),
        "tor_realms": _tor_realms(state_dir),
    }


def _netze(value: object) -> list[str]:
    out: list[str] = []
    for raw in (value if isinstance(value, list) else [])[: portal.MAX_NETS]:
        if not isinstance(raw, str):
            continue
        try:
            net = str(ipaddress.ip_network(raw.strip(), strict=False))
        except ValueError:
            continue
        if net not in out:
            out.append(net)
    return out


def _herkunft(k: dict) -> dict:
    """Herkunft, Werkzeuge, Zugriff, Netze und Realm (1.11.0) - nur bekannte bzw. gueltige
    Werte, sonst null/leer (aeltere topologie.json ohne die Felder: null)."""
    werkzeuge = k.get("werkzeuge")
    zugriff = k.get("zugriff") if k.get("zugriff") in portal.ACCESS else None
    return {
        "quelle": k.get("quelle") if k.get("quelle") in _QUELLEN else None,
        "werkzeuge": [w for w in _WERKZEUGE if w in werkzeuge]
        if isinstance(werkzeuge, list)
        else None,
        "zugriff": zugriff,
        "netze": _netze(k.get("netze")) if zugriff == "netze" else [],
        "realm": _realm(k.get("realm")),
        # 1.14.0 (ADR 0059): vom Front-Proxy gesperrt (zum Entfernen vorgemerkt); null = unbekannt
        "gesperrt": k.get("gesperrt") if isinstance(k.get("gesperrt"), bool) else None,
    }


def _zuordnung(pc: PortalCustomer | None, live: dict[int, set[int]],
               angelegt: dict[tuple[str, int], int] | None = None) -> dict:  # fmt: skip
    """Zuordnung in Automato je Kunde (1.13.0): ob ein Ausschnitt da ist und der Stand im
    Kunden-Portal - nur mit Kunden, die es noch gibt (`portal.live_customers`); ohne Eintrag im
    Kunden-Portal unbekannt (null). Seit 1.14.0 (ADR 0058) in der Zuordnung der Kunde, den
    Automato fuer das Cockpit im Mandanten angelegt hat (`kunde_angelegt`, sonst null), und die
    Benutzer mit Rolle aus dem Cockpit (`benutzer`, null = keine Aussage)."""
    if pc is None:
        return {"automato_zugeordnet": None, "zuordnung": None, "benutzer": None}
    kunde = (angelegt or {}).get((pc.short_name, pc.tenant_id)) if pc.tenant_id else None
    return {"automato_zugeordnet": portal.assigned(pc, live),
            "zuordnung": {**portal.scope_of(pc, live), "kunde_angelegt": kunde},
            "benutzer": _benutzer(pc)}  # fmt: skip


def _benutzer(pc: PortalCustomer) -> list[dict] | None:
    """Benutzer mit Rolle aus dem Cockpit (1.14.0) in der Form des Vertrags."""
    users = portal.users_of(pc.users)
    if users is None:
        return None
    return [{"benutzer": n, "rolle": r} for n, r in sorted(users.items())]


def _kachel(link: object) -> dict | None:
    """Eine gespeicherte Kachel in der Form des Vertrags (1.13.0); aeltere Eintraege werden auf
    die Grenzen gekuerzt (Steuerzeichen weg, Titel bzw. Text gekuerzt), eine ungueltige Adresse
    faellt weg - so nimmt `PUT /freigaben` jede gelieferte Kachel wieder an."""
    if not isinstance(link, dict):
        return None

    def rein(value: object, laenge: int) -> str:
        text = value if isinstance(value, str) else ""
        ohne = "".join(c for c in text if unicodedata.category(c) != "Cc")
        return ohne.strip()[:laenge].strip()

    try:
        k = portal.check_tile(rein(link.get("title"), portal.MAX_TILE_TITLE),
                              link.get("url") if isinstance(link.get("url"), str) else "",
                              rein(link.get("text"), portal.MAX_TILE_TEXT))  # fmt: skip
    except portal.PortalError:
        return None
    return {"titel": k["title"], "adresse": k["url"], "text": k["text"]}


def _kacheln(pc: PortalCustomer | None) -> dict:
    """Weitere Kacheln der Startseite je Kunde (1.13.0) und wer sie setzt; ohne Eintrag im
    Kunden-Portal unbekannt (null)."""
    if pc is None:
        return {"kacheln": None, "kacheln_von": None}
    kacheln = [k for k in map(_kachel, list(pc.links or [])[: portal.MAX_LINKS]) if k]
    return {"kacheln": kacheln, "kacheln_von": pc.links_source or portal.SCOPE_AUTOMATO}


def _kunden(topo: dict, portal_kunden: dict[str, PortalCustomer],
            live: dict[int, set[int]],
            angelegt: dict[tuple[str, int], int] | None = None) -> list[dict]:  # fmt: skip
    kunden = topo.get("kunden") if isinstance(topo.get("kunden"), list) else []
    out = []
    for k in kunden[:1000]:
        kurz = _text(k.get("kurzname"), 63) if isinstance(k, dict) else None
        if kurz:
            pc = portal_kunden.get(kurz)
            out.append({**{feld: _text(k.get(feld)) for feld in _KUNDE}, **_herkunft(k),
                        **_zuordnung(pc, live, angelegt), **_kacheln(pc)})  # fmt: skip
    return out


def _mandanten(session: Session) -> list[dict]:
    """Alle Mandanten mit ihren Kunden (nur Id und Name), nach Name - die Auswahl fuer die
    Zuordnung im Cockpit (1.13.0)."""
    mandanten = session.scalars(select(Tenant).order_by(Tenant.name).limit(MAX_MANDANTEN)).all()
    kunden: dict[int, list[dict]] = {t.id: [] for t in mandanten}
    if kunden:
        rows = session.execute(
            select(Customer.id, Customer.name, Customer.tenant_id)
            .where(Customer.tenant_id.in_(list(kunden)))
            .order_by(Customer.name, Customer.id)
        )
        for cid, name, tid in rows:
            if len(kunden[tid]) < MAX_KUNDEN_JE_MANDANT:
                kunden[tid].append({"id": cid, "name": (name or "")[:MAX_NAME]})
    return [{"id": t.id, "name": (t.name or "")[:MAX_NAME], "kunden": kunden[t.id]}
            for t in mandanten]  # fmt: skip


def installation(settings: Settings, topo: dict | None = None) -> dict:
    """Version, Umgebung und Adresse der Oberflaeche (auch in der Antwort der Kopplung, 1.13.0)."""
    topo = _topologie(settings.front_state_dir) if topo is None else topo
    return {
        "version": __version__,
        "umgebung": settings.environment,
        "oberflaeche": _text(topo.get("oberflaeche")),
    }


def inventory(settings: Settings, session: Session | None = None) -> dict:
    """Antwort von `GET /inventory` - Vertrag v1 (`VERTRAG`)."""
    topo = _topologie(settings.front_state_dir)
    issuer = settings.oidc_issuer if settings.auth_mode == "oidc" else ""
    portal_kunden = (
        {pc.short_name: pc for pc in session.scalars(select(PortalCustomer))} if session else {}
    )
    live = (
        portal.live_customers(session, (pc.tenant_id for pc in portal_kunden.values()))
        if session
        else {}
    )
    return {
        "vertrag": VERTRAG,
        "automato": {
            **installation(settings, topo),
            # 1.13.0 (ADR 0054 Nachtrag): Auswahl fuer die Zuordnung im Cockpit
            "mandanten": _mandanten(session) if session else [],
            "rollen": list(caps.CUSTOMER_ROLES),
        },
        "keycloak": {"issuer": _text(issuer)},
        "front": _front(topo, settings.front_state_dir),
        "kunden": _kunden(topo, portal_kunden, live,
                          verwaltung.angelegt_je_kunde(session) if session else None),
        # 1.14.0 (ADR 0059): Ist-Stand der Instanzen (Altlasten, Stand der Loeschauftraege)
        "ist": ist_stand.ist(session, settings) if session else None,
    }  # fmt: skip
