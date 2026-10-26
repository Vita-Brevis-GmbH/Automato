"""Standorte virtuell/physisch (ADR 0015): Code-Pruefung, Zuordnung und Erkennung aus Namen.

- Virtuell: dreistellige Nummer (`000`, `001`, ...), physisch: Ort-Code (`chzrh1`).
- Ein virtueller Standort laeuft an keinem, einem oder mehreren physischen (`site_link`).
- Erkennung: der Standort-Block in Netz- und Geraete-/Cluster-Namen (`N_<tenant>-000-...`,
  `fwc11-000-<tenant>`, `evh01-chzrh1-<tenant>`) wird gegen die erfassten Standorte des
  Mandanten gelegt. Nur Anzeige/Vorschlag - ohne erfasste Standorte funktioniert alles.
  VM-Namen (`H_app01-002-...`) tragen an dieser Stelle das ACI-Applikationsprofil, nicht den
  Standort; Hosts werden deshalb nicht ausgewertet.
"""

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import naming
from app.models import ObjectClass, Site, SiteLink

VIRTUAL_CODE = re.compile(r"^[0-9]{3}$")
PHYSICAL_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{2,15}$")
KIND_LABEL = {"virtual": "virtuell", "physical": "physisch"}


def infer_kind(code: str) -> str:
    """Art aus dem Code: drei Ziffern = virtuell, sonst physisch."""
    return "virtual" if VIRTUAL_CODE.match(code or "") else "physical"


def code_error(kind: str, code: str) -> str | None:
    """Fehlermeldung, wenn der Code nicht zur Art passt (None = gueltig)."""
    if kind == "virtual" and not VIRTUAL_CODE.match(code):
        return "virtual site code must be three digits (e.g. 000)"
    if kind == "physical" and (VIRTUAL_CODE.match(code) or not PHYSICAL_CODE.match(code)):
        return "physical site code must start with a letter, 3-16 characters (e.g. chzrh1)"
    return None


def links(session: Session, site_ids: set[int] | None = None) -> list[SiteLink]:
    stmt = select(SiteLink)
    if site_ids is not None:
        if not site_ids:
            return []
        stmt = stmt.where(SiteLink.virtual_id.in_(site_ids) | SiteLink.physical_id.in_(site_ids))
    return list(session.scalars(stmt))


def set_physical(session: Session, virtual: Site, physical: list[Site]) -> None:
    """Physische Standorte eines virtuellen Standorts ersetzen."""
    for link in session.scalars(select(SiteLink).where(SiteLink.virtual_id == virtual.id)):
        session.delete(link)
    session.flush()
    for p in {p.id: p for p in physical}.values():
        session.add(SiteLink(virtual_id=virtual.id, physical_id=p.id))
    session.flush()


class SiteIndex:
    """Standorte eines Mandanten mit Zuordnung - fuer Anzeige und Erkennung aus Namen."""

    def __init__(self, session: Session, tenant_id: int | None):
        self.sites: list[Site] = (
            list(session.scalars(select(Site).where(Site.tenant_id == tenant_id)))
            if tenant_id is not None
            else []
        )
        self.by_id = {s.id: s for s in self.sites}
        self.by_code = {s.code.lower(): s for s in self.sites}
        self.physical_of: dict[int, list[int]] = defaultdict(list)
        self.virtual_of: dict[int, list[int]] = defaultdict(list)
        for link in links(session, set(self.by_id)):
            if link.virtual_id in self.by_id and link.physical_id in self.by_id:
                self.physical_of[link.virtual_id].append(link.physical_id)
                self.virtual_of[link.physical_id].append(link.virtual_id)

    def _codes(self, ids: list[int]) -> list[str]:
        return sorted(self.by_id[i].code for i in ids)

    def ref(self, site: Site | int | None) -> dict | None:
        """Standort fuer die Anzeige: Code, Name, Art und die zugeordneten Standorte."""
        if isinstance(site, int):
            site = self.by_id.get(site)
        if site is None:
            return None
        return {
            "id": site.id,
            "code": site.code,
            "name": site.name,
            "kind": site.kind,
            "physical": self._codes(self.physical_of.get(site.id, [])),
            "virtual": self._codes(self.virtual_of.get(site.id, [])),
        }

    def from_name(self, name: str | None, prefixes=None, network_only=False) -> dict | None:
        """Standort aus einem Netz- bzw. Geraete-/Cluster-Namen (Vorschlag).

        `{"code", "kind", "known", ...}`; known=False, wenn der Code (noch) nicht erfasst ist.
        Hosts/VMs (H_...) liefern None - dort steht das Applikationsprofil. `network_only`:
        nur Netz-Objekte der Konvention (N_, T_, IPP_, R_) auswerten (Firewall-Objekte)."""
        if not name:
            return None
        parsed = naming.parse_object_name(name, prefixes)
        if parsed.object_class in (ObjectClass.HOST, ObjectClass.EXTERNAL, ObjectClass.VIP):
            return None
        if network_only and parsed.object_class not in (ObjectClass.NETWORK, ObjectClass.RANGE):
            return None
        token = site_token(parsed.remainder)
        if token is None:
            return None
        site = self.by_code.get(token)
        if site is not None:
            return {**self.ref(site), "known": True}
        if not VIRTUAL_CODE.match(token):
            return None  # unbekannter Ort-Code: zu unsicher fuer einen Vorschlag
        return {"code": token, "kind": "virtual", "known": False}


_TOKEN = re.compile(r"^(?:[0-9]{3}|[a-z]+[0-9]+)$")


def site_token(rest: str | None) -> str | None:
    """Zweiter Block eines Namens ohne Praefix: test1-000-dmz01 -> 000,
    fwc11-000-infrz -> 000, evh01-chzrh1-infrz_vmnic4 -> chzrh1; sonst None."""
    blocks = (rest or "").lower().split("-")
    if len(blocks) < 3:
        return None
    token = blocks[1].split("_", 1)[0]
    return token if _TOKEN.match(token) else None
