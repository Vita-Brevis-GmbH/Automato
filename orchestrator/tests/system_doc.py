"""Doku eines Systems fuer die Tests (0.107.0).

Die Ansicht „Doku je System“ samt API (`/appdoc/systems/...`) ist entfernt (Anwender
2026-10-28). Ihre Bausteine (`appdoc.build_doc` mit `host_doc`/`epg_doc`) baut die
Applikations-Doku weiter - die Tests pruefen sie direkt, mit Zonen im Kopf und den Namen der
Gegenstellen wie in der Applikations-Doku.
"""

import json

from app.db import get_session_factory
from app.models import Epg, NetworkObject, ObjectClass, RuleEndpoint
from app.store import appdoc, appdoc_zones, peer_names
from sqlalchemy import select


def system_doc(client, name: str, tenant_id: int | None) -> dict | None:
    """Tabellen eines Systems (Host bzw. EPG) als JSON wie bisher die API; None = unbekannt."""
    factory = client.app.dependency_overrides[get_session_factory]()
    session = factory()
    try:
        doc = appdoc.build_doc(session, name, tenant_id)
        if doc is None:
            return None
        rows = [r for t in doc["tables"] for r in t["rows"]]
        tid = tenant_id if tenant_id is not None else peer_names.tenant_of(session, rows)
        if peer_names.enabled(session, tid, False):
            peer_names.apply(session, tid, rows)
        if tid is not None:
            appdoc_zones.heads(appdoc_zones.ZoneMap(session, tid), doc["tables"])
        return json.loads(json.dumps(doc, default=str))
    finally:
        session.rollback()
        session.close()


def system_list(client, tenant_id: int | None) -> list[dict]:
    """Systeme mit Regelbezug wie bisher `GET /appdoc/systems`: Host-Objekte (Firewall) und EPGs
    (ACI, bei gleichem Namen mit Tenant und AP) - Namen wie fuer `system_doc`."""
    factory = client.app.dependency_overrides[get_session_factory]()
    session = factory()
    try:
        stmt = (
            select(NetworkObject.name, NetworkObject.value, RuleEndpoint.rule_id)
            .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
            .where(NetworkObject.object_class == ObjectClass.HOST)
        )
        epgs = select(Epg).where(Epg.kind == "epg")
        if tenant_id is not None:
            stmt = stmt.where(NetworkObject.tenant_id == tenant_id)
            epgs = epgs.where(Epg.tenant_id == tenant_id)
        hosts: dict[str, tuple[str | None, set[int]]] = {}
        for name, value, rid in session.execute(stmt):
            hosts.setdefault(name, (value, set()))[1].add(rid)
        out = [{"name": n, "ip": v, "rule_count": len(r), "kind": "host"}
               for n, (v, r) in hosts.items()]  # fmt: skip
        found = list(session.scalars(epgs))
        names = [(e.tenant_id, e.name) for e in found]
        rules: dict[int, set[int]] = {}
        for eid, rid in session.execute(select(RuleEndpoint.epg_id, RuleEndpoint.rule_id).where(
                RuleEndpoint.epg_id.in_([e.id for e in found]))):  # fmt: skip
            rules.setdefault(eid, set()).add(rid)
        for e in found:
            name = appdoc.epg_system_name(e) if names.count((e.tenant_id, e.name)) > 1 else e.name
            out.append({"name": name, "ip": None, "rule_count": len(rules.get(e.id, ())),
                        "kind": "epg"})  # fmt: skip
        return sorted(out, key=lambda x: (x["kind"], x["name"]))
    finally:
        session.close()
