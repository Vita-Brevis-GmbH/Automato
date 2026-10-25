"""Versionen des Zonenplans (0.52.0, ADR 0024): Stand speichern, vergleichen, exportieren.

Eine Version haelt, was beim Speichern abgeleitet war: die Zonen mit ihren Selektoren, den
Zonenplan und die Zonenmatrix samt Absichten (`snapshot`). Der **Fingerabdruck** fasst das
Design zusammen - Zonen (Name, Art, Kunde, Farbe, Selektoren), Absichten, Zonen an den Knoten
(seit 0.56.0 Knoten der Zonen und Uebergaenge mit ihren Netzen) und Netze ohne Zone, ohne
Regel-Zahlen -, damit sichtbar ist, ob
sich der Plan seit einer Version geaendert hat. Der **Vergleich** (`diff`) zeigt das je Teil,
dazu geaenderte Zahlen in der Matrix. Versionen bleiben bei Aufraeumen und Loeschen von Zonen
stehen; nur Daten der Plattform.
"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.models import Customer, SourceSystem, ZonePlanVersion
from app.store import zones as zone_store

FORMAT = 2  # 0.56.0: Plan nach dem Zonenmodell (Knoten, Uebergaenge)
_AXIS = {"any": "any", "none": "ohne Zone"}


def snapshot(session: Session, tenant_id: int) -> dict:
    """Aktueller Stand: Zonen mit Selektoren (Quellsystem als Name), Plan, Matrix."""
    items = zone_store.load(session, tenant_id)
    systems = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
        ).all()
    )
    customers = dict(
        session.execute(
            select(Customer.id, Customer.name).where(Customer.tenant_id == tenant_id)
        ).all()
    )
    plan = zone_store.plan(session, tenant_id, everything=True)
    nets = {z["id"]: z for z in plan["zones"]}
    zones = [
        {
            **{k: v for k, v in nets.get(z.id, {}).items() if k != "samples"},
            "id": z.id, "name": z.name, "kind": z.kind, "color": z.color, "code": z.code,
            "description": z.description,
            "customer": customers.get(z.customer_id) if z.customer_id else None,
            "selectors": [
                {"kind": s.kind, "value": s.value, "exclude": s.exclude,
                 "source": systems.get(s.source_system_id) if s.source_system_id else None}
                for s in z.selectors
            ],
        }
        for z in items
    ]  # fmt: skip
    data = {
        "format": FORMAT,
        "app_version": __version__,
        "zones": zones,
        "plan": plan,
        "matrix": zone_store.matrix(session, tenant_id),
    }
    # als JSON speicherbar (Zeitpunkte der Absichten als ISO-Text)
    return json.loads(json.dumps(data, default=_plain))


def _plain(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def design(data: dict) -> dict:
    """Was das Design ausmacht (ohne Regel-Zahlen) - mit Namen statt IDs."""
    names = {z["id"]: z["name"] for z in data.get("zones") or []}
    plan = data.get("plan") or {}
    nodes = {n["id"]: n["label"] for n in plan.get("nodes") or []}
    intents = (data.get("matrix") or {}).get("intents") or []
    return {
        "zones": {
            z["name"]: {
                "kind": z.get("kind"),
                "customer": z.get("customer"),
                "color": z.get("color"),
                "selectors": sorted(
                    f"{'ohne ' if s.get('exclude') else ''}{s['kind']} {s['value']}"
                    + (f" ({s['source']})" if s.get("source") else "")
                    for s in z.get("selectors") or []
                ),
            }
            for z in data.get("zones") or []
        },
        "intents": {
            f"{names.get(i['src'], i['src'])} → {names.get(i['dst'], i['dst'])}": {
                "intent": i["intent"],
                "note": i.get("note"),
                **({"services": i["services"]} if i.get("services") else {}),
            }
            for i in intents
        },  # fmt: skip
        "attachments": _attachments(plan, names, nodes),
        "unzoned": sorted(u["network"] for u in (plan.get("unzoned") or {}).get("items") or []),
    }


def _attachments(plan: dict, names: dict, nodes: dict) -> dict:
    """Zonen an den Knoten: bis 0.55.0 mit Weg und Interfaces, seit 0.56.0 (Format 2) die Knoten
    je Zone und die Uebergaenge mit ihren Netzen."""
    if plan.get("format") != 2:
        return {
            f"{names.get(a['zone'], a['zone'])} @ {nodes.get(a['node'], a['node'])}": {
                "via": list(a.get("via") or []),
                "interfaces": list(a.get("interfaces") or []),
            }
            for a in plan.get("attachments") or []
        }  # fmt: skip
    zname = {z["id"]: z["name"] for z in plan.get("zones") or []}
    out: dict = {}
    for z in plan.get("zones") or []:
        for n in z.get("nodes") or []:
            out[f"{z['name']} @ {nodes.get(n, n)}"] = {"via": ["node"], "interfaces": []}
    for t in plan.get("transitions") or []:
        key = f"{zname.get(t['a'], t['a'])} ↔ {zname.get(t['b'], t['b'])}"
        out[key] = {
            "via": ["transition"],
            "interfaces": sorted(n["network"] for n in t.get("networks") or []),
        }
    return out


def fingerprint(data: dict) -> str:
    text = json.dumps(design(data), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _counts(data: dict) -> dict[str, tuple[int, int, int]]:
    """Regeln je Zelle (Namen): erlaubend, sperrend, gegen die Absicht."""
    m = data.get("matrix") or {}
    names = {z["id"]: z["name"] for z in m.get("zones") or []} | _AXIS
    return {
        f"{names.get(c['src'], c['src'])} → {names.get(c['dst'], c['dst'])}": (
            c.get("allow", 0), c.get("deny", 0), c.get("violations", 0))
        for c in m.get("cells") or []
        if c.get("allow") or c.get("deny")
    }  # fmt: skip


def _changes(old: dict, new: dict) -> dict:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = [{"key": k, "old": old[k], "new": new[k]}
               for k in sorted(set(old) & set(new)) if old[k] != new[k]]  # fmt: skip
    return {"added": added, "removed": removed, "changed": changed}


def diff(old: dict, new: dict) -> dict:
    """Unterschiede von `old` zu `new`: Zonen, Absichten, Zonen an den Knoten, Netze ohne
    Zone; dazu die Zellen der Matrix mit anderen Zahlen."""
    a, b = design(old), design(new)
    ca, cb = _counts(old), _counts(new)
    cells = [
        {"cell": k, "old": list(ca.get(k, (0, 0, 0))), "new": list(cb.get(k, (0, 0, 0)))}
        for k in sorted(set(ca) | set(cb))
        if ca.get(k, (0, 0, 0)) != cb.get(k, (0, 0, 0))
    ]
    out = {key: _changes(a[key], b[key]) for key in ("zones", "intents", "attachments")}
    out["unzoned"] = {"added": sorted(set(b["unzoned"]) - set(a["unzoned"])),
                      "removed": sorted(set(a["unzoned"]) - set(b["unzoned"]))}  # fmt: skip
    out["matrix"] = cells
    out["same"] = a == b
    return out


def version_out(v: ZonePlanVersion, with_data: bool = False) -> dict:
    s = (v.data or {}).get("plan", {}).get("summary") or {}
    out = {
        "id": v.id,
        "number": v.number,
        "label": v.label,
        "note": v.note,
        "fingerprint": v.fingerprint,
        "created_by": v.created_by,
        "created_at": v.created_at,
        "app_version": (v.data or {}).get("app_version"),
        "summary": s,
    }
    if with_data:
        out["data"] = v.data
    return out


def save(session: Session, tenant_id: int, label: str, note: str | None,
         actor: str | None) -> ZonePlanVersion:  # fmt: skip
    data = snapshot(session, tenant_id)
    last = session.scalar(
        select(func.max(ZonePlanVersion.number)).where(ZonePlanVersion.tenant_id == tenant_id)
    )
    v = ZonePlanVersion(
        tenant_id=tenant_id,
        number=(last or 0) + 1,
        label=label,
        note=note,
        fingerprint=fingerprint(data),
        created_by=actor,
        created_at=datetime.now(UTC),
        data=data,
    )
    session.add(v)
    session.flush()
    return v
