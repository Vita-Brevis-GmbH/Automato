"""Kunden-Portal (1.3.0, ADR 0050): Pruefen, Soll-Stand fuer den Host-Agenten, Realm je Kunde.

Die Oberflaeche pflegt die Kunden in der Datenbank. Nach jeder Aenderung schreibt der
Orchestrator den Soll-Stand als `soll.json` in den Ordner `AUTOMATO_FRONT_STATE_DIR`; der
Host-Agent (`deploy/front/front.sh sync`, systemd) uebernimmt ihn in den Front-Proxy und meldet
das Ergebnis in `status.json`. Der Orchestrator aendert nie selbst etwas auf dem Host - er
schreibt nur diese eine Datei; `front.sh` prueft alles noch einmal (Vertrauensgrenze).

Die Pruefungen entsprechen `front.sh` (Kurzname als DNS-Label, reservierte Namen und Endungen,
Kacheln nur mit https://) - so meldet die Oberflaeche einen Fehler sofort statt erst der Agent.

Kacheln aus dem Cockpit (1.13.0, ADR 0054 zweiter Nachtrag): eine Kachel prueft `check_tile` -
dieselbe Pruefung fuer die Oberflaeche und `PUT /freigaben` (Grenzen wie im Vertrag mit dem
Cockpit: Titel 1-60 Zeichen, Adresse mit https:// hoechstens 500 Zeichen ohne Leerraum, Text
hoechstens 200 Zeichen, nirgends Steuerzeichen; hoechstens 20 Kacheln).
"""

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import capabilities as caps
from app.i18n import L
from app.models import Customer, PortalCustomer, Tenant

SHORT_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$")
# wie front.sh (seit 1.10.0 auch konsole und connect: Namen der Plattform dahinter)
RESERVED = frozenset({"start", "www", "proxy", "alle", "konfig", "mgmt", "konsole", "connect"})
SUFFIXES = ("-nb", "-doc", "-um", "-cmdb")
REALM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Kacheln (1.13.0: Grenzen wie im Vertrag mit dem Cockpit, ADR 0054 zweiter Nachtrag)
MAX_LINKS, MAX_TILE_TITLE, MAX_TILE_URL, MAX_TILE_TEXT = 20, 60, 500, 200
SOLL, STATUS = "soll.json", "status.json"
# Zugriff je Kunde (1.5.0): von wo die Adressen des Kunden erreichbar sind
ACCESS = ("ueberall", "intern", "netze")
MAX_NETS = 50
# Herkunft je Kunde (1.11.0, ADR 0054): in Automato angelegt bzw. vom Kundenregister des Cockpits
SOURCE_UI, SOURCE_COCKPIT = "oberflaeche", "cockpit"
# wer Mandant, Kunden und Standard-Rolle setzt (1.13.0, ADR 0054 Nachtrag) - dieselben Werte fuer
# die Kacheln (`links_source`, ADR 0054 zweiter Nachtrag)
SCOPE_AUTOMATO, SCOPE_COCKPIT = "automato", "cockpit"


class PortalError(ValueError):
    """Ungueltige Angabe (Text fuer die Antwort, zweisprachig zur Laufzeit)."""


@dataclass(frozen=True)
class RealmScope:
    """Was Benutzer eines Kunden-Realms sehen - aus dem Portal, nie aus dem Token."""

    realm: str
    short_name: str
    tenant_id: int | None
    customer_ids: tuple[int, ...]
    default_role: str
    name: str = ""  # Anzeigename (Meldung bei fehlender Zuordnung, 1.13.0)
    # 1.14.0 (ADR 0058): Kunde aus dem Cockpit - die Rolle aus dem Token zaehlt nicht
    from_cockpit: bool = False
    # 1.14.0: Benutzername -> Rolle aus dem Cockpit (`benutzer`); None = keine Aussage, gesetzt
    # haben nur diese Benutzer Rechte
    users: dict[str, str] | None = None

    @property
    def assigned(self) -> bool:
        """Mandant und mindestens ein Kunde - sonst hat ein Benutzer des Realms keine Rechte."""
        return self.tenant_id is not None and bool(self.customer_ids)


def check_short_name(name: str) -> str:
    name = (name or "").strip()
    if not SHORT_NAME.match(name):
        raise PortalError(L(
            f"Kurzname '{name}' ungültig: nur a-z, 0-9 und '-', nicht am Anfang/Ende, "
            "höchstens 42 Zeichen",
            f"Short name '{name}' invalid: only a-z, 0-9 and '-', not at the start/end, "
            "at most 42 characters",
        ))  # fmt: skip
    if name in RESERVED:
        raise PortalError(
            L(f"Kurzname '{name}' ist reserviert", f"Short name '{name}' is reserved")
        )
    if name.endswith(SUFFIXES):
        raise PortalError(L(
            f"Kurzname '{name}' ist reserviert (endet wie ein Werkzeug: -nb, -doc, -um, -cmdb)",
            f"Short name '{name}' is reserved (ends like a tool: -nb, -doc, -um, -cmdb)",
        ))  # fmt: skip
    return name


def check_realm(realm: str | None) -> str | None:
    realm = (realm or "").strip()
    if not realm:
        return None
    if not REALM.match(realm) or realm == "master":
        raise PortalError(L(
            f"Realm '{realm}' ungültig (Buchstaben, Ziffern, . _ -, nicht 'master')",
            f"Realm '{realm}' invalid (letters, digits, . _ -, not 'master')",
        ))  # fmt: skip
    return realm


def _control(value: str) -> bool:
    """Enthaelt Steuerzeichen (Tabulator, Umbruch, C0/C1, DEL)?"""
    return any(unicodedata.category(c) == "Cc" for c in value)


def check_tile(title: str, url: str, text: str = "") -> dict:
    """Eine Kachel pruefen - dieselbe Pruefung fuer die Oberflaeche und die Kacheln aus dem
    Cockpit (1.13.0): Titel 1-60 Zeichen, Adresse beginnt mit https:// (hoechstens 500 Zeichen,
    ohne Leerraum), Text hoechstens 200 Zeichen, nirgends Steuerzeichen. Leerraum am Rand faellt
    weg."""
    title, url, text = title.strip(), url.strip(), text.strip()
    if not title or len(title) > MAX_TILE_TITLE or _control(title):
        raise PortalError(L(
            f"Kachel ohne Titel bzw. Titel länger als {MAX_TILE_TITLE} Zeichen oder mit "
            "Steuerzeichen",
            f"Tile without a title or title longer than {MAX_TILE_TITLE} characters or with "
            "control characters",
        ))  # fmt: skip
    if (not url.startswith("https://") or len(url) == len("https://") or len(url) > MAX_TILE_URL
            or _control(url) or any(c.isspace() for c in url)):  # fmt: skip
        raise PortalError(L(
            f"Kachel „{title}“: Adresse muss mit https:// beginnen (höchstens {MAX_TILE_URL} "
            "Zeichen, ohne Leerzeichen)",
            f"Tile “{title}”: the address must start with https:// (at most {MAX_TILE_URL} "
            "characters, no spaces)",
        ))  # fmt: skip
    if len(text) > MAX_TILE_TEXT or _control(text):
        raise PortalError(L(
            f"Kachel „{title}“: Beschreibung höchstens {MAX_TILE_TEXT} Zeichen, ohne "
            "Tabulatoren/Zeilenumbrüche",
            f"Tile “{title}”: description at most {MAX_TILE_TEXT} characters, no tabs/line "
            "breaks",
        ))  # fmt: skip
    return {"title": title, "url": url, "text": text}


def check_links(links: list[dict] | None) -> list[dict]:
    """Kacheln aus der Oberflaeche: ganz leere Zeilen fallen weg, jede andere wie `check_tile`."""
    out: list[dict] = []
    for item in links or []:
        title = str(item.get("title") or "")
        url = str(item.get("url") or "")
        if not title.strip() and not url.strip():
            continue
        out.append(check_tile(title, url, str(item.get("text") or "")))
    if len(out) > MAX_LINKS:
        raise PortalError(L(f"höchstens {MAX_LINKS} Kacheln", f"at most {MAX_LINKS} tiles"))
    return out


def check_scope(session: Session, tenant_id: int | None, customer_ids: list[int]) -> list[int]:
    """Kunden muessen zum Mandanten gehoeren (sonst saehe ein Realm fremde Kunden)."""
    ids = sorted({int(c) for c in customer_ids or []})
    if tenant_id is None:
        if ids:
            raise PortalError(L("Kunden nur zusammen mit dem Mandanten",
                                "Customers only together with the tenant"))  # fmt: skip
        return []
    if session.get(Tenant, tenant_id) is None:
        raise PortalError(L(f"Mandant {tenant_id} unbekannt", f"Tenant {tenant_id} unknown"))
    if ids:
        found = set(
            session.scalars(
                select(Customer.id).where(Customer.id.in_(ids), Customer.tenant_id == tenant_id)
            )
        )
        missing = [c for c in ids if c not in found]
        if missing:
            raise PortalError(L(
                f"Kunden {missing} gehören nicht zum Mandanten {tenant_id}",
                f"Customers {missing} do not belong to tenant {tenant_id}",
            ))  # fmt: skip
    return ids


def check_access(access: str | None, nets: list[str] | None) -> tuple[str, list[str]]:
    """Zugriff je Kunde (1.5.0): `ueberall`, `intern` (Management-Netz) oder `netze` (nur aus
    der Liste). Netze werden normalisiert (Adresse ohne Maske = Einzeladresse); nur bei `netze`
    gespeichert."""
    value = str(access or "ueberall").strip().lower()
    if value not in ACCESS:
        raise PortalError(L(f"Zugriff '{value}' unbekannt ({', '.join(ACCESS)})",
                            f"Access '{value}' unknown ({', '.join(ACCESS)})"))  # fmt: skip
    if value != "netze":
        return value, []
    out: list[str] = []
    for raw in nets or []:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            net = str(ipaddress.ip_network(text, strict=False))
        except ValueError as exc:
            raise PortalError(L(f"Netz '{text}' ungültig (z. B. 203.0.113.0/24 oder 2001:db8::/48)",
                                f"Network '{text}' invalid (e.g. 203.0.113.0/24 or 2001:db8::/48)")
                              ) from exc  # fmt: skip
        if net not in out:
            out.append(net)
    if not out:
        raise PortalError(L(
            "Zugriff „nur aus diesen Netzen“ braucht mindestens ein Netz",
            "Access “only from these networks” needs at least one network",
        ))  # fmt: skip
    if len(out) > MAX_NETS:
        raise PortalError(L(f"höchstens {MAX_NETS} Netze", f"at most {MAX_NETS} networks"))
    return value, out


def check_role(role: str | None) -> str:
    value = str(role or caps.VIEWER).strip().lower()
    if value not in caps.CUSTOMER_ROLES:
        raise PortalError(
            L(
                f"Rolle '{value}' unbekannt ({', '.join(caps.CUSTOMER_ROLES)})",
                f"Role '{value}' unknown ({', '.join(caps.CUSTOMER_ROLES)})",
            )
        )
    return value


def realm_of(issuer: str) -> tuple[str, str] | None:
    """(Basis, Realm) aus einer Keycloak-Issuer-Adresse `<basis>/realms/<realm>`."""
    head, sep, realm = (issuer or "").rstrip("/").rpartition("/realms/")
    return (head, realm) if sep and head and realm and "/" not in realm else None


def live_customers(session: Session, tenant_ids: Iterable[int | None]) -> dict[int, set[int]]:
    """Kunden, die es in Automato gibt, je Mandant (1.13.0, Fund aus der Kette mit dem Cockpit):
    eine Zuordnung zaehlt nur sie. Ein geloeschter Kunde bleibt in `customer_ids` stehen (wie
    bisher; das Cockpit meldet die Abweichung, ADR 0054 Nachtrag), gibt aber keine Rechte und
    zaehlt nicht als zugeordnet - sonst saehe ein Benutzer des Realms ein leeres Automato statt
    der Meldung „noch kein Mandant zugeordnet“."""
    ids = {int(t) for t in tenant_ids if t is not None}
    out: dict[int, set[int]] = {t: set() for t in ids}
    if ids:
        rows = session.execute(
            select(Customer.tenant_id, Customer.id).where(Customer.tenant_id.in_(ids))
        )
        for tid, cid in rows:
            out[tid].add(cid)
    return out


def live_ids(pc: PortalCustomer, live: dict[int, set[int]] | None = None) -> list[int]:
    """Kunden der Zuordnung (sortiert) - mit `live` (`live_customers`) nur die, die es im
    Mandanten noch gibt; ohne Mandant keine."""
    ids = sorted({int(c) for c in pc.customer_ids or []})
    if live is None:
        return ids
    vorhanden = live.get(pc.tenant_id, set()) if pc.tenant_id is not None else set()
    return [c for c in ids if c in vorhanden]


def realm_scopes(session: Session, main_realm: str | None) -> dict[str, RealmScope]:
    """Realm -> Ausschnitt fuer alle Kunden mit eigenem Realm (ohne den Realm von Automato
    selbst: dessen Benutzer melden sich wie bisher an). Nur Kunden, die es noch gibt
    (`live_customers`)."""
    out: dict[str, RealmScope] = {}
    # gesperrt (1.14.0, ADR 0059, zum Entfernen vorgemerkt): der Realm gibt in Automato keine
    # Rechte mehr
    kunden = [pc for pc in session.scalars(
        select(PortalCustomer).where(PortalCustomer.realm.is_not(None))
    ) if pc.realm and pc.realm != main_realm and not pc.gesperrt]  # fmt: skip
    live = live_customers(session, (pc.tenant_id for pc in kunden))
    for pc in kunden:
        out[pc.realm] = RealmScope(
            realm=pc.realm,
            short_name=pc.short_name,
            tenant_id=pc.tenant_id,
            customer_ids=tuple(live_ids(pc, live)),
            default_role=caps.normalize(pc.default_role),
            name=pc.name or pc.short_name,
            from_cockpit=pc.source == SOURCE_COCKPIT,
            users=users_of(pc.users),
        )
    return out


def users_of(users: list | None) -> dict[str, str] | None:
    """Benutzer aus dem Cockpit (1.14.0, ADR 0058): gespeicherte Liste -> Benutzername ->
    Kunden-Rolle (unbekannte Rolle = viewer, nie admin); None = keine Aussage."""
    if users is None:
        return None
    out: dict[str, str] = {}
    for e in users:
        if isinstance(e, dict) and isinstance(e.get("benutzer"), str):
            out[e["benutzer"].strip().lower()] = caps.normalize(e.get("rolle"))
    return out


def assigned(pc: PortalCustomer, live: dict[int, set[int]] | None = None) -> bool | None:
    """Hat der Kunde in Automato einen Ausschnitt (Mandant und mindestens ein Kunde - mit `live`
    einer, den es noch gibt)? None = ohne Werkzeug Doku (seine Benutzer melden sich nicht in
    Automato an)."""
    if not pc.doc:
        return None
    return pc.tenant_id is not None and bool(live_ids(pc, live))


def scope_of(pc: PortalCustomer, live: dict[int, set[int]] | None = None) -> dict:
    """Zuordnung im Kunden-Portal (Inventar, 1.13.0): Mandant, Kunden (mit `live` nur die, die es
    noch gibt - der neue Stand nach dem Loeschen), Rolle und wer sie setzt."""
    return {
        "mandant": pc.tenant_id,
        "kunden": live_ids(pc, live),
        "rolle": pc.default_role or caps.VIEWER,
        "von": pc.scope_source or SCOPE_AUTOMATO,
    }


def realm_for_host(session: Session, host: str | None) -> str | None:
    """Realm zur Adresse der Oberflaeche: `<kurzname>-doc.<domain>` -> Realm dieses Kunden."""
    label = (host or "").strip().lower().split(":")[0].split(".")[0]
    if not label.endswith("-doc"):
        return None
    pc = session.scalar(
        select(PortalCustomer).where(PortalCustomer.short_name == label[: -len("-doc")])
    )
    return pc.realm if pc is not None and pc.doc and pc.realm else None


def out(pc: PortalCustomer, domain: str = "", live: dict[int, set[int]] | None = None) -> dict:
    addresses = {}
    if domain:
        addresses = {"start": f"https://{pc.short_name}.{domain}"}
        # NetBox seit 1.11.0 waehlbar (`nb`, Vorgabe an)
        for key, tool, on in (("netbox", "nb", pc.nb), ("doc", "doc", pc.doc),
                              ("um", "um", pc.um), ("cmdb", "cmdb", pc.cmdb)):  # fmt: skip
            if on:
                addresses[key] = f"https://{pc.short_name}-{tool}.{domain}"
    return {
        "id": pc.id,
        "short_name": pc.short_name,
        "name": pc.name,
        "doc": pc.doc,
        "nb": pc.nb,
        "um": pc.um,
        "cmdb": pc.cmdb,
        "links": list(pc.links or []),
        "realm": pc.realm,
        "tenant_id": pc.tenant_id,
        "customer_ids": list(pc.customer_ids or []),
        "default_role": pc.default_role,
        "access": pc.access or "ueberall",
        "access_nets": list(pc.access_nets or []),
        "source": pc.source or SOURCE_UI,
        # 1.13.0: Mandant, Kunden und Rolle aus dem Cockpit (dann nur lesbar), Ausschnitt vorhanden
        "scope_source": pc.scope_source or SCOPE_AUTOMATO,
        # 1.13.0: Kacheln aus dem Cockpit (dann nur lesbar)
        "links_source": pc.links_source or SCOPE_AUTOMATO,
        # 1.14.0 (ADR 0058): Benutzer des Realms mit Rolle aus dem Cockpit (None = alle mit der
        # Standard-Rolle)
        "users": list(pc.users) if pc.users is not None else None,
        # 1.14.0 (ADR 0059): vom Cockpit zum Entfernen vorgemerkt - Front-Proxy sperrt alle Adressen
        "gesperrt": bool(pc.gesperrt),
        "assigned": assigned(pc, live),
        "addresses": addresses,
        "updated_by": pc.updated_by,
        "updated_at": pc.updated_at,
    }


def listing(session: Session) -> list[PortalCustomer]:
    return list(
        session.scalars(
            select(PortalCustomer).order_by(PortalCustomer.position, PortalCustomer.short_name)
        )
    )


def desired(session: Session, cockpit: bool = False) -> dict:
    """Soll-Stand fuer den Front-Proxy (nur, was front.sh braucht - kein Mandant). Seit 1.4.0
    mit dem Realm: das Anmelde-Tor fragt je Kunde in dessen Realm (ADR 0051); ohne Realm
    gesperrt. Seit 1.5.0 mit dem Zugriff je Kunde (`zugriff`, `netze`), seit 1.11.0 mit NetBox
    als Werkzeug (`nb`) und der Herkunft (`quelle`: oberflaeche | cockpit, ADR 0054). Seit 1.13.0
    (ADR 0055) mit den versiegelten Client-Secrets des Anmelde-Tors aus dem Cockpit
    (`tor_geheimnisse`, nur Chiffrat; nur, wenn es welche gibt). Seit 1.14.0 (ADR 0058) mit
    `verwaltung: cockpit`, wenn ein Cockpit verbunden ist (`cockpit`) - dann aendert `front.sh`
    Kunden von Hand nur noch mit `--trotzdem`. Ebenfalls seit 1.14.0 (ADR 0059) je Kunde
    `gesperrt` (nur, wenn gesperrt) und die Tor-Geheimnisse, die `front.sh sync` auf Auftrag des
    Cockpits entfernt (`tor_entfernen`, nur, wenn es welche gibt)."""
    from app.store import abbau  # spaet: abbau nutzt portal

    kunden = listing(session)
    data = {
        "version": 1,
        "erzeugt": datetime.now(UTC).isoformat(timespec="seconds"),
        "kunden": [
            {"kurzname": pc.short_name, "name": pc.name, "doc": pc.doc, "nb": pc.nb,
             "um": pc.um, "cmdb": pc.cmdb, "realm": pc.realm or "",
             "zugriff": pc.access or "ueberall", "netze": list(pc.access_nets or []),
             "links": list(pc.links or []), "quelle": pc.source or SOURCE_UI,
             **({"gesperrt": True} if pc.gesperrt else {})}
            for pc in kunden
        ],
    }  # fmt: skip
    tor = sorted(
        (
            {k: pc.tor_secret.get(k) for k in ("realm", "kennung", "versiegelt")}
            for pc in kunden
            if pc.realm and pc.source == SOURCE_COCKPIT and isinstance(pc.tor_secret, dict)
            and pc.tor_secret.get("realm") == pc.realm
        ),
        key=lambda e: e["realm"],
    )  # fmt: skip
    if tor:
        data["tor_geheimnisse"] = tor
    entfernen = abbau.tor_entfernen(session)
    if entfernen:
        data["tor_entfernen"] = entfernen
    if cockpit:
        data["verwaltung"] = "cockpit"
    return data


def _content(data: dict) -> dict:
    """Soll-Stand ohne Zeitstempel (zum Vergleich mit der vorhandenen Datei)."""
    return {k: v for k, v in data.items() if k != "erzeugt"}


def write_desired(session: Session, state_dir: str, only_if_changed: bool = False,
                  cockpit: bool = False) -> str | None:  # fmt: skip
    """`soll.json` atomar schreiben; ohne Ordner (nicht eingerichtet) nichts. Wirft OSError.

    `only_if_changed` (beim Start, 1.5.0): nur schreiben, wenn sich der Inhalt gegenueber der
    vorhandenen Datei geaendert hat - z. B. nach einem Update, das neue Felder mitgibt (1.4.0:
    Realm). Sonst bliebe der alte Soll-Stand liegen, bis jemand im Portal speichert."""
    if not state_dir:
        return None
    folder = Path(state_dir)
    soll = desired(session, cockpit)
    if only_if_changed:
        try:
            current = json.loads((folder / SOLL).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if current is None and not soll["kunden"]:
            return None  # nie eingerichtet und keine Kunden: nichts anzulegen
        if isinstance(current, dict) and _content(current) == _content(soll):
            return None
    data = json.dumps(soll, ensure_ascii=False, indent=1)
    fd, tmp = tempfile.mkstemp(prefix=".soll-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, folder / SOLL)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return str(folder / SOLL)


def agent_status(state_dir: str) -> dict:
    """Stand des Host-Agenten (`status.json` von front.sh sync) und ob der Ordner bereit ist."""
    if not state_dir:
        return {"configured": False, "writable": False, "status": None}
    folder = Path(state_dir)
    status = None
    try:
        status = json.loads((folder / STATUS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        status = None
    except (OSError, ValueError) as exc:
        status = {"ok": False, "meldung": L(f"status.json nicht lesbar: {exc}",
                                            f"status.json not readable: {exc}")}  # fmt: skip
    # noch nicht uebernommen: der Agent meldet die Pruefsumme des Soll-Stands, den er anwandte
    try:
        current = hashlib.sha256((folder / SOLL).read_bytes()).hexdigest()
        pending = not status or status.get("soll_sha256") != current
    except OSError:
        pending = None
    return {
        "configured": True,
        "writable": folder.is_dir() and os.access(folder, os.W_OK),
        "status": status,
        "pending": pending,
    }
