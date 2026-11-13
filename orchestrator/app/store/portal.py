"""Kunden-Portal (1.3.0, ADR 0050): Pruefen, Soll-Stand fuer den Host-Agenten, Realm je Kunde.

Die Oberflaeche pflegt die Kunden in der Datenbank. Nach jeder Aenderung schreibt der
Orchestrator den Soll-Stand als `soll.json` in den Ordner `AUTOMATO_FRONT_STATE_DIR`; der
Host-Agent (`deploy/front/front.sh sync`, systemd) uebernimmt ihn in den Front-Proxy und meldet
das Ergebnis in `status.json`. Der Orchestrator aendert nie selbst etwas auf dem Host - er
schreibt nur diese eine Datei; `front.sh` prueft alles noch einmal (Vertrauensgrenze).

Die Pruefungen entsprechen `front.sh` (Kurzname als DNS-Label, reservierte Namen und Endungen,
Kacheln nur mit https://) - so meldet die Oberflaeche einen Fehler sofort statt erst der Agent.
"""

import hashlib
import ipaddress
import json
import os
import re
import tempfile
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
URL = re.compile(r"^https://[^\s\t\"<>]{1,500}$")
MAX_LINKS = 20
SOLL, STATUS = "soll.json", "status.json"
# Zugriff je Kunde (1.5.0): von wo die Adressen des Kunden erreichbar sind
ACCESS = ("ueberall", "intern", "netze")
MAX_NETS = 50
# Herkunft je Kunde (1.11.0, ADR 0054): in Automato angelegt bzw. vom Kundenregister des Cockpits
SOURCE_UI, SOURCE_COCKPIT = "oberflaeche", "cockpit"


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


def check_links(links: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for item in links or []:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        text = str(item.get("text") or "").strip()
        if not title and not url:
            continue
        if not title or len(title) > 80:
            raise PortalError(L("Kachel ohne Titel (bzw. länger als 80 Zeichen)",
                                "Tile without a title (or longer than 80 characters)"))  # fmt: skip
        if not URL.match(url):
            raise PortalError(
                L(
                    f"Kachel „{title}“: Adresse muss mit https:// beginnen",
                    f"Tile “{title}”: the address must start with https://",
                )
            )
        if any("\t" in v or "\n" in v for v in (title, url, text)) or len(text) > 200:
            raise PortalError(L(f"Kachel „{title}“: keine Tabulatoren/Zeilenumbrüche, "
                                "Beschreibung höchstens 200 Zeichen",
                                f"Tile “{title}”: no tabs/line breaks, description at most "
                                "200 characters"))  # fmt: skip
        out.append({"title": title, "url": url, "text": text})
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


def realm_scopes(session: Session, main_realm: str | None) -> dict[str, RealmScope]:
    """Realm -> Ausschnitt fuer alle Kunden mit eigenem Realm (ohne den Realm von Automato
    selbst: dessen Benutzer melden sich wie bisher an)."""
    out: dict[str, RealmScope] = {}
    for pc in session.scalars(select(PortalCustomer).where(PortalCustomer.realm.is_not(None))):
        if not pc.realm or pc.realm == main_realm:
            continue
        out[pc.realm] = RealmScope(
            realm=pc.realm,
            short_name=pc.short_name,
            tenant_id=pc.tenant_id,
            customer_ids=tuple(int(c) for c in pc.customer_ids or ()),
            default_role=caps.normalize(pc.default_role),
        )
    return out


def realm_for_host(session: Session, host: str | None) -> str | None:
    """Realm zur Adresse der Oberflaeche: `<kurzname>-doc.<domain>` -> Realm dieses Kunden."""
    label = (host or "").strip().lower().split(":")[0].split(".")[0]
    if not label.endswith("-doc"):
        return None
    pc = session.scalar(
        select(PortalCustomer).where(PortalCustomer.short_name == label[: -len("-doc")])
    )
    return pc.realm if pc is not None and pc.doc and pc.realm else None


def out(pc: PortalCustomer, domain: str = "") -> dict:
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


def desired(session: Session) -> dict:
    """Soll-Stand fuer den Front-Proxy (nur, was front.sh braucht - kein Mandant). Seit 1.4.0
    mit dem Realm: das Anmelde-Tor fragt je Kunde in dessen Realm (ADR 0051); ohne Realm
    gesperrt. Seit 1.5.0 mit dem Zugriff je Kunde (`zugriff`, `netze`), seit 1.11.0 mit NetBox
    als Werkzeug (`nb`) und der Herkunft (`quelle`: oberflaeche | cockpit, ADR 0054)."""
    return {
        "version": 1,
        "erzeugt": datetime.now(UTC).isoformat(timespec="seconds"),
        "kunden": [
            {"kurzname": pc.short_name, "name": pc.name, "doc": pc.doc, "nb": pc.nb,
             "um": pc.um, "cmdb": pc.cmdb, "realm": pc.realm or "",
             "zugriff": pc.access or "ueberall", "netze": list(pc.access_nets or []),
             "links": list(pc.links or []), "quelle": pc.source or SOURCE_UI}
            for pc in listing(session)
        ],
    }  # fmt: skip


def _content(data: dict) -> dict:
    """Soll-Stand ohne Zeitstempel (zum Vergleich mit der vorhandenen Datei)."""
    return {k: v for k, v in data.items() if k != "erzeugt"}


def write_desired(session: Session, state_dir: str, only_if_changed: bool = False) -> str | None:
    """`soll.json` atomar schreiben; ohne Ordner (nicht eingerichtet) nichts. Wirft OSError.

    `only_if_changed` (beim Start, 1.5.0): nur schreiben, wenn sich der Inhalt gegenueber der
    vorhandenen Datei geaendert hat - z. B. nach einem Update, das neue Felder mitgibt (1.4.0:
    Realm). Sonst bliebe der alte Soll-Stand liegen, bis jemand im Portal speichert."""
    if not state_dir:
        return None
    folder = Path(state_dir)
    soll = desired(session)
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
