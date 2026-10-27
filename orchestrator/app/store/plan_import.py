"""Zonenplan aus draw.io zurueck einlesen (0.83.0, ADR 0024 Nr. 20).

Der Export des Zonenplans (`plan_export.drawio`) laesst sich in diagrams.net bearbeiten und
wieder einlesen - Korrekturen im Designer kommen so zurueck nach Automato. Gelesen wird nur, was
die Datei eindeutig sagt; uebernommen erst nach der Vorschau (`read` -> `apply`):

- **Lage:** jede Zone der Zeichnung (`automato_zone`, Form `zone-<id>` bzw. `zone-customer-<id>`
  in der Uebersicht) an ihrer Stelle - als Anordnung der Ansicht, aus der der Export stammt
  (`automato_view` am Titel, sonst „alle Zonen“; `automato_offset` = Verschiebung des Exports).
- **Name:** erste Zeile der Beschriftung, wenn sie vom Namen beim Export abweicht. Wurde die
  Zone seither in Automato umbenannt, gilt Automato (Hinweis).
- **Absicht:** neue Pfeile zwischen zwei Zonen bzw. Linien mit der Eigenschaft `automato_intent`
  - Absicht, Dienste und Richtung wie im Grundschema (Beschriftung, Strichart, Farbe,
  `automato_services`, Pfeile; ohne Pfeil beide Richtungen). Die Linien des Exports selbst
  (Uebergaenge, Kommunikation, Absichten) sind keine neue Absicht.
- **Neue Formen** ohne `automato_*` werden als Zonen ohne Selektoren vorgeschlagen (nur auf
  Wunsch). Geloeschte Formen loeschen keine Zone.

Sicherheit wie beim Grundschema: hoechstens 2 MB, keine DTD/Entitaeten. Nur Daten der Plattform.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import LogicalZone, ZonePolicy
from app.store import plan_export, plan_view, zone_schema, zone_services
from app.store import zones as zone_store
from app.store.zone_detect import classify
from app.store.zone_schema import SchemaError

VIEW = re.compile(r"^(overview|all|customer:\d+)$")
ZONE_CELL = re.compile(r"^zone-(\d+)$")
CUSTOMER_CELL = re.compile(r"^zone-customer-(\d+)$")
EXPORTED = ("automato_transition", "automato_intents", "automato_rules", "automato_networks")
MOVED = 1.0  # Pixel: weniger gilt als nicht verschoben


@dataclass
class Reading:
    view: str
    positions: dict[str, list[float]] = field(default_factory=dict)
    moved: list[str] = field(default_factory=list)
    renames: list[dict] = field(default_factory=list)
    intents: list[dict] = field(default_factory=list)
    new_zones: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page: str | None = None

    def out(self) -> dict:
        return {
            "view": self.view, "page": self.page, "positions": len(self.positions),
            "moved": self.moved, "renames": self.renames, "intents": self.intents,
            "new_zones": self.new_zones, "missing": self.missing, "warnings": self.warnings,
        }  # fmt: skip


def _offset(text: str | None) -> tuple[float, float]:
    try:
        x, y = (float(v) for v in (text or "0,0").split(","))
    except ValueError:
        return 0.0, 0.0
    return x, y


def _first_line(label: str) -> str:
    return " ".join((label.splitlines() or [""])[0].split())


def read(session: Session, tenant_id: int, content: str) -> Reading:
    """Datei lesen und mit dem Stand in Automato vergleichen (schreibt nichts)."""
    if len(content.encode("utf-8")) > zone_schema.MAX_BYTES:
        raise SchemaError(zone_schema.too_large())
    meta = zone_schema.Schema()
    model = zone_schema._model(zone_schema._xml(content), meta)
    cells = zone_schema._cells(model)
    title = cells.get("title")
    view = ((title.props.get("automato_view") if title else "") or "all").strip()
    out = Reading(view=view if VIEW.match(view) else "all", page=meta.page,
                  warnings=list(meta.warnings))  # fmt: skip
    if not VIEW.match(view):
        out.warnings.append(L(f"Ansicht „{view}“ unbekannt - Anordnung für alle Zonen",
                              f"View “{view}” unknown - arrangement for all zones"))  # fmt: skip
    dx, dy = _offset(title.props.get("automato_offset") if title else None)
    items = {z.id: z for z in zone_store.load(session, tenant_id)}
    shown = _current(session, tenant_id, out.view)

    by_cell: dict[str, LogicalZone] = {}
    seen: set[int] = set()
    for c in cells.values():
        if not c.vertex or c.parent != "1":
            continue
        m, mc = ZONE_CELL.match(c.id), CUSTOMER_CELL.match(c.id)
        key = None
        if m and "automato_zone" in c.props:
            z = items.get(int(m.group(1)))
            if z is None:
                gone = c.props["automato_zone"]
                out.warnings.append(L(f"Zone „{gone}“ gibt es nicht mehr",
                                      f"Zone “{gone}” no longer exists"))  # fmt: skip
                continue
            by_cell[c.id], key = z, str(z.id)
            seen.add(z.id)
            name = _first_line(c.label)
            exported = c.props["automato_zone"].strip()
            if name and name != exported:
                if exported != z.name:
                    out.warnings.append(L(
                        f"Zone „{exported}“ heisst in Automato inzwischen „{z.name}“ - Name "
                        "aus der Zeichnung nicht übernommen",
                        f"Zone “{exported}” is now called “{z.name}” in Automato - name from "
                        "the drawing not applied"))  # fmt: skip
                elif name != z.name:
                    out.renames.append({"id": z.id, "from": z.name, "to": name[:200]})
        elif mc:
            key = f"customer:{mc.group(1)}"
        elif _is_new(c):
            name = _first_line(c.label)[:200]
            if name and name.casefold() not in {z.name.casefold() for z in items.values()}:
                kind = zone_schema._kind_of(c.props.get("automato_kind", "")) if c.props.get(
                    "automato_kind") else None  # fmt: skip
                hit = classify(name) if kind is None else None
                out.new_zones.append({"name": name, "kind": kind or (hit[0] if hit else
                                      "internal"), "code": (c.props.get("automato_code") or
                                      "").strip()[:50] or None})  # fmt: skip
            continue
        if key is None or c.geo is None:
            continue
        x, y = round(c.geo[0] - dx, 1), round(c.geo[1] - plan_export.HEAD - dy, 1)
        out.positions[key] = [x, y]
        now = shown.get(key)  # Lage im Plan jetzt (mit derselben Verschiebung wie im Export)
        top = c.geo[1] - plan_export.HEAD
        if now is None or abs(now[0] - c.geo[0]) > MOVED or abs(now[1] - top) > MOVED:
            out.moved.append(key)
    out.missing = sorted(z.name for zid, z in items.items() if zid not in seen)
    out.intents = _intents(session, tenant_id, cells, by_cell, out)
    return out


def _current(session: Session, tenant_id: int, view: str) -> dict[str, tuple[float, float]]:
    """Lage der Zonen im Plan dieser Ansicht jetzt (Zeichnung wie im Export, ohne Filter)."""
    customer = int(view.split(":")[1]) if view.startswith("customer:") else None
    plan = zone_store.plan_with_layout(session, tenant_id, None, customer, view == "all")
    return {str(b["id"]): (b["x"], b["y"]) for b in (plan.get("layout") or {}).get("zones", [])}


def _is_new(c) -> bool:
    """Neue Form in der Zeichnung (kein Teil des Exports, kein Text, keine Beschriftung)."""
    if not c.label or any(k.startswith("automato_") for k in c.props) or c.id == "title":
        return False
    shape = c.style.get("shape", "")
    return "text" not in c.style and "edgeLabel" not in c.style and shape != "text"


def _intents(session: Session, tenant_id: int, cells: dict, by_cell: dict,
             out: Reading) -> list[dict]:  # fmt: skip
    current = {
        (p.src_zone_id, p.dst_zone_id): p
        for p in session.scalars(select(ZonePolicy).where(ZonePolicy.tenant_id == tenant_id))
    }
    labels: dict[str, list[str]] = {}
    for c in cells.values():
        if c.vertex and c.parent in cells and cells[c.parent].edge and c.label:
            labels.setdefault(c.parent, []).append(c.label)
    found: dict[tuple[int, int], dict] = {}
    for c in cells.values():
        if not c.edge:
            continue
        a, b = by_cell.get(c.source or ""), by_cell.get(c.target or "")
        if a is None or b is None or a.id == b.id:
            continue
        raw = c.props.get("automato_intent", "").strip()
        if not raw and any(k in c.props for k in EXPORTED):
            continue  # Linie des Exports: keine neue Absicht
        text = "\n".join([c.label, *labels.get(c.id, [])]).strip()
        intent, reason, services, unknown = zone_schema.edge_intent(c, text)
        if unknown:
            out.warnings.append(L(f"{a.name} → {b.name}: Absicht „{raw}“ unbekannt",
                                  f"{a.name} → {b.name}: intent “{raw}” unknown"))  # fmt: skip
            continue
        for s, d in zone_schema.edge_pairs(c, a, b):
            p = current.get((s.id, d.id))
            same = (
                p is not None
                and p.intent == intent
                and (intent != "limited" or list(p.services or []) == services)
            )
            if same:
                continue
            found[(s.id, d.id)] = {
                "src_zone_id": s.id, "src": s.name, "dst_zone_id": d.id, "dst": d.name,
                "intent": intent, "reason": zone_schema.reason_text(reason), "services": services,
                "note": text[:500] or None, "current": p.intent if p else None,
                "current_services": list(p.services or []) if p else [],
            }  # fmt: skip
    return list(found.values())


def apply(session: Session, tenant_id: int, reading: Reading, actor: str | None,
          positions: bool = True, renames: bool = True, intents: bool = True,
          new_zones: bool = False) -> dict:  # fmt: skip
    """Vorschau uebernehmen - je Teil auf Wunsch. Rueckgabe: was geaendert wurde."""
    done = {"positions": 0, "renames": [], "intents": [], "new_zones": [], "skipped": []}
    if positions and reading.positions:
        clean = plan_view.clean_positions(reading.positions)
        plan_view.save(session, tenant_id, reading.view, clean, actor)
        done["positions"] = len(clean)
    if renames:
        taken = {z.name.casefold(): z.id for z in zone_store.load(session, tenant_id)}
        for r in reading.renames:
            z = session.get(LogicalZone, r["id"])
            if z is None or z.tenant_id != tenant_id:
                continue
            other = taken.get(r["to"].casefold())
            if other is not None and other != z.id:
                to = r["to"]
                done["skipped"].append(L(f"Name „{to}“ hat schon eine andere Zone",
                                         f"Name “{to}” already used by another zone"))  # fmt: skip
                continue
            taken.pop(z.name.casefold(), None)
            z.name, taken[r["to"].casefold()] = r["to"], z.id
            done["renames"].append(r)
    if intents:
        now = datetime.now(UTC)
        for x in reading.intents:
            services = None
            if x["intent"] == "limited" and x["services"]:
                try:
                    services = zone_services.normalize(session, tenant_id, x["services"])
                except ValueError as exc:
                    done["skipped"].append(f"{x['src']} → {x['dst']}: {exc}")
                    continue
            p = session.scalar(select(ZonePolicy).where(
                ZonePolicy.tenant_id == tenant_id,
                ZonePolicy.src_zone_id == x["src_zone_id"],
                ZonePolicy.dst_zone_id == x["dst_zone_id"],
            ))  # fmt: skip
            if p is None:
                p = ZonePolicy(tenant_id=tenant_id, src_zone_id=x["src_zone_id"],
                               dst_zone_id=x["dst_zone_id"])  # fmt: skip
                session.add(p)
            p.intent, p.services = x["intent"], services
            # gespeichert: deutsch (ADR 0028)
            p.note = f"Zonenplan (draw.io): {x['note']}" if x.get("note") else "Zonenplan (draw.io)"
            p.updated_by, p.updated_at = actor, now
            done["intents"].append({"src": x["src_zone_id"], "dst": x["dst_zone_id"],
                                    "intent": x["intent"], "before": x["current"]})  # fmt: skip
    if new_zones and reading.new_zones:
        top = session.scalar(select(func.max(LogicalZone.position)).where(
            LogicalZone.tenant_id == tenant_id)) or 0  # fmt: skip
        names = {z.name.casefold() for z in zone_store.load(session, tenant_id)}
        for i, n in enumerate(reading.new_zones, 1):
            if n["name"].casefold() in names:
                continue
            session.add(LogicalZone(tenant_id=tenant_id, name=n["name"], kind=n["kind"],
                                    code=n["code"], position=top + i * 10))  # fmt: skip
            names.add(n["name"].casefold())
            done["new_zones"].append(n["name"])
    session.flush()
    return done
