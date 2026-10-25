"""Zonenplan uebersichtlich (0.61.0, ADR 0024 Nr. 14): Filter, Ebenen und Anordnung.

Bei vielen Tenants und Zonen wird der Plan unuebersichtlich. Drei Hilfen, die Oberflaeche und
Export gleich anwenden (der Export zeigt, was die Ansicht zeigt):

- **Filter** (`Filter`, `apply`): Suche (Zone, Kurzname, Kunde, Knoten, Uebergang, Netz) bzw.
  Fokus auf gewaehlte Zonen - je mit den Nachbarn bis `depth` Uebergaenge weit; die uebrigen
  Zonen und Uebergaenge fallen weg, das Layout ruckt zusammen.
- **Ebenen** ausblenden: Spalten des Plans (DC, Kunden, Edge, Transfer, Core, Distribution,
  Access, Public, Knoten ohne Zone, ausserhalb) und Elemente (Knoten in den Zonen, Namen der
  Uebergaenge, Kontakte zu Knoten ohne Zone, Zonen ohne Knoten und Uebergang). Gewaehlte bzw.
  gefundene Zonen bleiben sichtbar.
- **Anordnung** (`ZonePlanArrangement`): von Hand gezogene Positionen je Ansicht (`view_key`);
  `plan_export.layout` setzt sie ein, Zonen ohne Position kommen darunter.

Kunden-Benutzer suchen nur ueber Namen der Zonen und Kunden (keine Geraetenamen, ADR 0018).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ZonePlanArrangement

# Ebenen = Spalten des Plans (`zones.LAYER`), je mit den Arten der Zonen darin
LAYERS = {
    "dc": ("DC", ("dc", "internal", "shared")),
    "customer": ("Kunden", ("customer",)),
    "edge": ("Edge", ("edge", "dmz", "management")),
    "transfer": ("Transfer", ("transfer",)),
    "core": ("Core", ("core",)),
    "distribution": ("Distribution", ("distribution",)),
    "access": ("Access", ("access",)),
    "public": ("Public", ("external",)),
    "none": ("Knoten ohne Zone", ("none",)),
    "outside": ("ausserhalb", ("outside",)),
}
LAYER_OF = {kind: layer for layer, (_, kinds) in LAYERS.items() for kind in kinds}
ELEMENTS = {
    "nodes": "Knoten in den Zonen",
    "labels": "Namen der Übergänge",
    "contacts": "Kontakte zu Knoten ohne Zone",
    "empty": "Zonen ohne Knoten und Übergang",
}
MAX_DEPTH = 3
MAX_POSITIONS = 5000
LIMIT = 100_000  # Koordinaten (Pixel) je Richtung


@dataclass
class Filter:
    q: str = ""
    focus: list[str] = field(default_factory=list)
    depth: int = 1
    hide_layers: set[str] = field(default_factory=set)
    hide: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.q = (self.q or "").strip()
        self.focus = [str(x) for x in self.focus or [] if str(x).strip()]
        self.depth = max(0, min(MAX_DEPTH, int(self.depth)))
        self.hide_layers = {x for x in self.hide_layers or () if x in LAYERS}
        self.hide = {x for x in self.hide or () if x in ELEMENTS}

    @property
    def narrows(self) -> bool:
        """Suche bzw. Fokus: nur die Treffer mit ihren Nachbarn."""
        return bool(self.q or self.focus)

    @property
    def active(self) -> bool:
        return self.narrows or bool(self.hide_layers or self.hide)


def _layer(z: dict) -> str:
    return LAYER_OF.get(z.get("kind") or "", "dc")


def _matches(z: dict, q: str, labels: dict, devices: bool) -> bool:
    texts = [z.get("name"), z.get("code"), z.get("customer"), *(z.get("members") or [])]
    if devices:
        texts += [labels.get(n, n) for n in z.get("nodes") or []]
        texts += z.get("samples") or []
    return any(q in str(t).casefold() for t in texts if t)


def apply(plan: dict, f: Filter, devices: bool = True) -> dict:
    """Plan mit den sichtbaren Zonen und Uebergaengen; `filter` sagt, wie viele Zonen gezeigt
    sind, `zone_choices` listet alle Zonen der Ansicht (Auswahl fuer den Fokus), `display` steuert
    das Layout (Knoten, Namen der Uebergaenge). `devices=False` (Kunden-Benutzer): die Suche
    trifft keine Geraete und Netze."""
    zones = plan.get("zones") or []
    choices = [
        {"id": str(z["id"]), "name": z["name"], "kind": z.get("kind"), "layer": _layer(z)}
        for z in zones
    ]
    display = {"nodes": "nodes" not in f.hide, "labels": "labels" not in f.hide}
    out = {**plan, "zone_choices": choices, "display": display}
    real = sum(1 for z in zones if isinstance(z["id"], int))
    if not f.active:
        out["filter"] = {"active": False, "zones": real, "total": real, "hidden": 0}
        return out
    by_id = {str(z["id"]): z for z in zones}
    labels = {n["id"]: n["label"] for n in plan.get("nodes") or []}
    edges = [
        t for t in plan.get("transitions") or []
        if not (t.get("contact") and "contacts" in f.hide)
    ]  # fmt: skip
    keep = {k for k, z in by_id.items() if _layer(z) not in f.hide_layers}
    seeds: set[str] = set()
    if f.narrows:
        seeds = {k for k in f.focus if k in by_id}
        q = f.q.casefold()
        if q:
            seeds |= {k for k, z in by_id.items() if _matches(z, q, labels, devices)}
            for t in edges:  # Uebergang nach Name („srv-einet“) bzw. Netz: beide Seiten
                ends = [by_id.get(str(t[k])) or {} for k in ("a", "b")]
                own = any(q in str(z.get(k) or "").casefold() for z in ends
                          for k in ("name", "code"))  # fmt: skip
                name = t.get("name") if not own else None
                nets = [n.get("network") for n in t.get("networks") or []] if devices else []
                if any(q in str(x).casefold() for x in [name, *nets] if x):
                    seeds |= {str(t["a"]), str(t["b"])}
        shown, frontier = set(seeds), set(seeds)
        for _ in range(f.depth):
            near = set()
            for t in edges:
                a, b = str(t["a"]), str(t["b"])
                if a in frontier and b in keep:
                    near.add(b)
                if b in frontier and a in keep:
                    near.add(a)
            frontier = near - shown
            shown |= near
        keep = (keep & shown) | seeds
    edges = [t for t in edges if str(t["a"]) in keep and str(t["b"]) in keep]
    if "empty" in f.hide:
        linked = {str(x) for t in edges for x in (t["a"], t["b"])}
        keep = {k for k in keep if by_id[k].get("nodes") or k in linked or k in seeds}
    kept = [z for z in zones if str(z["id"]) in keep]
    node_ids = {n for z in kept for n in z.get("nodes") or []}
    shown_real = sum(1 for z in kept if isinstance(z["id"], int))
    out.update({
        "zones": kept,
        "transitions": edges,
        "nodes": [n for n in plan.get("nodes") or [] if n["id"] in node_ids],
        "filter": {"active": True, "zones": shown_real, "total": real,
                   "hidden": len(zones) - len(kept), "matches": len(seeds),
                   "label": describe(f, by_id)},
    })  # fmt: skip
    return out


def describe(f: Filter, by_id: dict | None = None) -> str:
    """Kurztext fuer Kopf von SVG und PDF: „Suche „dc“ · Fokus: DC Kunde A + Nachbarn · ohne
    Access, Namen der Übergänge“."""
    parts = []
    near = {0: "", 1: " + Nachbarn"}.get(f.depth, f" + {f.depth} Schritte")
    if f.q:
        parts.append(f"Suche „{f.q}“{near if not f.focus else ''}")
    if f.focus:
        names = [(by_id or {}).get(k, {}).get("name", k) for k in f.focus]
        parts.append(f"Fokus: {', '.join(names)}{near}")
    hidden = [LAYERS[x][0] for x in LAYERS if x in f.hide_layers]
    hidden += [ELEMENTS[x] for x in ELEMENTS if x in f.hide]
    if hidden:
        parts.append("ohne " + ", ".join(hidden))
    return " · ".join(parts)


# -- Anordnung ----------------------------------------------------------------------------


def view_key(plan: dict) -> str | None:
    """Ansicht, deren Anordnung gilt: Uebersicht, alle Zonen einzeln bzw. Plan eines Kunden."""
    view = plan.get("view") or {}
    mode = view.get("mode")
    if mode == "customer":
        cid = view.get("customer_id")
        return f"customer:{cid}" if cid is not None else None
    return mode if mode in ("overview", "all") else None


def get(session: Session, tenant_id: int, view: str | None) -> ZonePlanArrangement | None:
    if not view:
        return None
    return session.scalar(
        select(ZonePlanArrangement).where(
            ZonePlanArrangement.tenant_id == tenant_id, ZonePlanArrangement.view == view
        )
    )


def positions(session: Session, tenant_id: int, view: str | None) -> dict[str, list[float]]:
    row = get(session, tenant_id, view)
    return dict(row.positions or {}) if row is not None else {}


def arrangement_out(row: ZonePlanArrangement | None, view: str | None) -> dict:
    return {
        "view": view,
        "saved": row is not None and bool(row.positions),
        "count": len(row.positions or {}) if row is not None else 0,
        "updated_by": row.updated_by if row is not None else None,
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
    }


def clean_positions(raw: dict) -> dict[str, list[float]]:
    """Positionen pruefen: Schluessel = Zone (Text), Wert = [x, y] endlich und im Rahmen.
    ValueError bei ungueltigen Werten."""
    if len(raw) > MAX_POSITIONS:
        raise ValueError(f"at most {MAX_POSITIONS} positions")
    out: dict[str, list[float]] = {}
    for key, value in raw.items():
        k = str(key).strip()
        if not k or len(k) > 64:
            raise ValueError("invalid zone key")
        if not isinstance(value, list | tuple) or len(value) != 2:
            raise ValueError(f"position of {k}: [x, y] expected")
        x, y = (float(v) for v in value)
        if not all(abs(v) <= LIMIT for v in (x, y)):  # auch NaN faellt hier durch
            raise ValueError(f"position of {k} out of range")
        out[k] = [round(x, 1), round(y, 1)]
    return out


def save(session: Session, tenant_id: int, view: str, new: dict[str, list[float]],
         subject: str | None) -> ZonePlanArrangement:  # fmt: skip
    """Positionen uebernehmen: gesetzte Zonen ersetzen ihre Position, die anderen bleiben."""
    row = get(session, tenant_id, view)
    if row is None:
        row = ZonePlanArrangement(tenant_id=tenant_id, view=view, positions={})
        session.add(row)
    row.positions = {**(row.positions or {}), **new}
    row.updated_by, row.updated_at = subject, datetime.now(UTC)
    session.flush()
    return row


def reset(session: Session, tenant_id: int, view: str) -> bool:
    row = get(session, tenant_id, view)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
