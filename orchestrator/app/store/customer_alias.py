"""Kunden-Doku ohne Geraetenamen (0.58.0, ADR 0018 Schritt 4).

Die Doku eines Kunden-Benutzers enthaelt nur seine Dinge - nie die Infrastruktur. Jede Antwort
an Kunden-Benutzer laeuft durch `Aliases.clean` (Middleware in `app/main.py`, die Exporte des
Zonenplans vor dem Zeichnen), auch kuenftige Endpunkte. Statt der Namen stehen neutrale
Bezeichnungen:

- Quellsysteme: „Firewall 1“, „Firewall 2“ … in der Reihenfolge der Anlage (ID), bei nur einer
  „Firewall“; APIC „ACI“, vCenter „vCenter“, NetBox/Infoblox „IPAM“ (mehrere je mit Nummer).
- VDOMs „VDOM 1“, „VDOM 2“ … nach Namen ueber alle Firewalls (gleicher Name = gleiche Nummer),
  `root` bleibt; Verbund paralleler Firewalls „Verbund 1“ …
- Geraete aus dem Inventar (Hostname der Firewall, Leafs, Spines, Controller) und Gegenstellen
  aus LLDP/CDP: „Firewall …“ bzw. „Leaf“, „Spine“, „Controller“, „Nachbar“.
- Felder `vdom`/`vdoms` (bzw. Schluessel von `by_vdom`) neutral; ESXi-Host, Cluster, Datacenter,
  verteilter Switch und LLDP-Nachbar (`host`, `cluster`, `datacenter`, `switch`, `neighbor`)
  leer.
- In allen Texten (Begruendungen, Saetze der Findings, Bezeichnungen der Knoten „fw-01 · VDOM
  x“, Knoten-IDs „3|x“) dieselben Ersetzungen - ganze Woerter, laengste zuerst.

Die Nummern sind stabil, solange keine Quellsysteme geloescht werden. Betreuer sehen weiter die
echten Namen. Nur lesend.
"""

import re
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvDevice, InvInterface, Rule, SourceSystem, TopoContext

HIDDEN = frozenset({"host", "cluster", "datacenter", "switch", "neighbor"})  # Felder -> leer
_KIND = {"fortigate": "Firewall", "cisco_aci": "ACI", "vmware": "vCenter", "netbox": "IPAM",
         "infoblox": "IPAM", "avi": "Load Balancer"}  # fmt: skip
_ROLE = {"leaf": "Leaf", "spine": "Spine", "controller": "Controller"}


class Aliases:
    """Neutrale Bezeichnungen der Infrastruktur der Mandanten eines Kunden-Benutzers."""

    def __init__(self, session: Session, tenant_ids: Iterable[int]):
        tenant_ids = list(tenant_ids)
        self.systems: dict[int, str] = {}
        self.names: dict[str, str] = {}  # Name -> neutrale Bezeichnung (ganze Woerter)
        vdoms: set[str] = set()
        groups: set[str] = set()
        rows = list(session.execute(
            select(SourceSystem.id, SourceSystem.tenant_id, SourceSystem.name,
                   SourceSystem.platform, SourceSystem.parallel_group)
            .where(SourceSystem.tenant_id.in_(tenant_ids))
            .order_by(SourceSystem.id)
        ))  # fmt: skip
        by_kind: dict[tuple[int, str], list[int]] = defaultdict(list)
        for sid, tid, _name, platform, group in rows:
            by_kind[(tid, _KIND.get(getattr(platform, "value", platform), "System"))].append(sid)
            if group:
                groups.add(group)
        for (_tid, kind), ids in by_kind.items():
            for n, sid in enumerate(ids, start=1):
                self.systems[sid] = kind if len(ids) == 1 else f"{kind} {n}"
        for sid, _tid, name, _p, _g in rows:
            if name:
                self.names[name] = self.systems[sid]

        for (vdom,) in session.execute(
            select(Rule.vdom).where(Rule.tenant_id.in_(tenant_ids), Rule.vdom.is_not(None))
            .distinct()
        ):  # fmt: skip
            vdoms.add(vdom)
        for (vdom,) in session.execute(
            select(InvInterface.vdom)
            .where(InvInterface.tenant_id.in_(tenant_ids), InvInterface.vdom.is_not(None))
            .distinct()
        ):
            vdoms.add(vdom)
        for node, context, kind in session.execute(
            select(TopoContext.node, TopoContext.context, TopoContext.kind)
            .where(TopoContext.tenant_id.in_(tenant_ids))
        ):  # fmt: skip
            if kind == "vdom":
                vdoms.add(context)
            elif node and node not in self.names:
                self.names[node] = "Leaf"
        for sid, name, role in session.execute(
            select(InvDevice.source_system_id, InvDevice.name, InvDevice.role)
            .where(InvDevice.tenant_id.in_(tenant_ids))
        ):  # fmt: skip
            if name and name not in self.names:
                self.names[name] = self.systems.get(sid, "Gerät") if role == "firewall" else (
                    _ROLE.get(role or "", "Gerät"))  # fmt: skip
        for (neighbor,) in session.execute(
            select(InvInterface.neighbor)
            .where(InvInterface.tenant_id.in_(tenant_ids), InvInterface.neighbor.is_not(None))
            .distinct()
        ):
            if neighbor and neighbor not in self.names:
                self.names[neighbor] = "Nachbar"
        others = sorted(v for v in vdoms if v and v != "root")
        self.vdoms: dict[str, str] = {v: str(n) for n, v in enumerate(others, start=1)}
        if "root" in vdoms:
            self.vdoms["root"] = "root"
        for n, group in enumerate(sorted(groups), start=1):
            self.names.setdefault(group, f"Verbund {n}")
        self._pattern: re.Pattern | None = None
        self._vdom_pattern: re.Pattern | None = None
        self._compiled = False

    # -- einzelne Werte ----------------------------------------------------------------

    def system(self, sid: int | None) -> str:
        return self.systems.get(sid, "System") if sid is not None else ""

    def vdom(self, vdom: str | None) -> str | None:
        """Neutraler Name eines VDOMs (ohne „VDOM“-Vorsatz): `root` bleibt, sonst die Nummer."""
        if not vdom:
            return vdom
        return self.vdoms.get(vdom, "?")

    # -- Texte -------------------------------------------------------------------------

    def _compile(self) -> None:
        def alt(words) -> str:
            return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))

        names = [n for n in self.names if n]
        # Name allein (ganzes Wort), „VDOM x“ und Knoten-IDs „3|x“
        self._pattern = re.compile(rf"(?<![\w.-])({alt(names)})(?![\w-])") if names else None
        vdoms = [v for v in self.vdoms if v]
        self._vdom_pattern = (
            re.compile(rf"(?:(VDOM )|(?<=\d\|))({alt(vdoms)})(?![\w-])") if vdoms else None
        )
        self._compiled = True

    def text(self, value: str) -> str:
        if not self._compiled:
            self._compile()
        if self._vdom_pattern is not None:
            value = self._vdom_pattern.sub(
                lambda m: f"{m.group(1) or ''}{self.vdoms[m.group(2)]}", value
            )
        if self._pattern is not None:
            value = self._pattern.sub(lambda m: self.names[m.group(1)], value)
        return value

    def clean(self, value, key: str | None = None):
        """Antwort (JSON-Daten) fuer Kunden-Benutzer: Felder und Texte neutral (siehe Modul)."""
        if isinstance(value, str):
            if key in HIDDEN:
                return None
            if key == "vdom":
                return self.vdom(value)
            return self.text(value)
        if isinstance(value, list):
            if key == "vdoms":
                return [self.vdom(v) if isinstance(v, str) else v for v in value]
            return [self.clean(v, key if key in HIDDEN else None) for v in value]
        if isinstance(value, dict):
            if key == "by_vdom":
                return {(self.vdom(k) or k): self.clean(v) for k, v in value.items()}
            return {(self.text(k) if isinstance(k, str) else k): self.clean(v, k)
                    for k, v in value.items()}  # fmt: skip
        return value


async def middleware(request, call_next):
    """Antworten an Kunden-Benutzer (JSON) ohne Geraetenamen: `require_role` merkt sich deren
    Mandanten (`request.state.customer_tenants`); hier wird die fertige Antwort neutralisiert -
    fuer jeden freigegebenen Endpunkt, auch kuenftige."""
    import json

    from starlette.concurrency import run_in_threadpool
    from starlette.responses import Response

    from app.db import get_session_factory

    response = await call_next(request)
    tenants = getattr(request.state, "customer_tenants", None)
    if tenants is None or "json" not in (response.headers.get("content-type") or ""):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])

    def clean() -> bytes:
        factory = request.app.dependency_overrides.get(get_session_factory, get_session_factory)()
        with factory() as session:
            data = Aliases(session, tenants).clean(json.loads(body)) if body else None
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()

    content = await run_in_threadpool(clean) if body else body
    out = Response(content=content, status_code=response.status_code)
    out.raw_headers = [(k, v) for k, v in response.raw_headers if k.lower() != b"content-length"]
    out.raw_headers.append((b"content-length", str(len(content)).encode()))
    return out
