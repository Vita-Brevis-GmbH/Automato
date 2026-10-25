# ruff: noqa: S314 - XML aus dem Upload: ohne DTD/Entitaeten (abgewiesen), Groesse begrenzt
"""Grundschema der Zonen (0.55.0, ADR 0024 Nr. 11): die moeglichen Zonen und die Absicht
zwischen ihnen als draw.io-Zeichnung in der Namenskonvention.

Das Schema gehoert zum **Namensprofil** (wie die Woerterbuecher, ohne Funktionszwang): ohne
Schema arbeiten Zonen und Vorschlaege wie bisher. Gespeichert als Woerterbuch-Eintraege - Art
`zone_schema` (Zone: Name, Art, Kurzname, Farbe, Erkennungsmuster), `zone_schema_link`
(Uebergang mit Absicht) und `zone_schema_meta` (die hochgeladene Zeichnung).

**Zeichnung** (draw.io/diagrams.net, `.drawio` unkomprimiert oder komprimiert; erste Seite bzw.
die Seite mit „Zone“ oder „Schema“ im Namen):

- jede Form mit Beschriftung ist eine Zone (erste Zeile = Name, weitere = Beschreibung) -
  ausser Text, Beschriftungen von Verbindungen und Formen mit `automato_node` (Knoten aus dem
  Export des Zonenplans) bzw. `automato_ignore`. Eine Form, die andere Zonen enthaelt, ist eine
  Gruppe (Name an den Zonen), keine Zone.
- Eigenschaften (draw.io „Daten bearbeiten“): `automato_kind` (intern, DMZ, extern,
  Management, geteilt, Transfer), `automato_code` (Kurzname), `automato_match`
  (Erkennungsmuster, mit Komma getrennt, `*`/`?`), `automato_order` (Reihenfolge, sonst von
  oben nach unten, links nach rechts). Ohne Art: aus dem Namen, sonst intern. Farbe =
  Fuellfarbe.
- jede Verbindung zwischen zwei Zonen ist ein Uebergang. Absicht aus `automato_intent`, sonst
  aus der Beschriftung (erlaubt, eingeschraenkt, gesperrt), sonst gestrichelt = eingeschraenkt,
  rote Linie = gesperrt, sonst erlaubt. Richtung = Pfeil; ohne bzw. mit zwei Pfeilen in beide
  Richtungen.

Sicherheit: hoechstens 2 MB, keine DTD/Entitaeten, entpackt hoechstens 10 MB. Nur Daten der
Plattform - kein Zugriff auf Quellsysteme.
"""

import base64
import fnmatch
import hashlib
import html
import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import unquote

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import DictionaryEntry, LogicalZone, NamingProfile, ZonePolicy
from app.store import naming_profiles

KIND_ZONE, KIND_LINK, KIND_META = "zone_schema", "zone_schema_link", "zone_schema_meta"
SCHEMA_KINDS = (KIND_ZONE, KIND_LINK, KIND_META)
MAX_BYTES = 2_000_000
MAX_XML = 10_000_000
MAX_ZONES, MAX_LINKS = 300, 3000
ZONE_KINDS = ("internal", "dmz", "external", "management", "shared", "transfer")
KIND_ALIAS = {
    "internal": "internal", "intern": "internal", "innen": "internal", "inside": "internal",
    "dmz": "dmz",
    "external": "external", "extern": "external", "aussen": "external", "outside": "external",
    "management": "management", "mgmt": "management", "verwaltung": "management",
    "shared": "shared", "geteilt": "shared", "gemeinsam": "shared",
    "transfer": "transfer", "transit": "transfer",
}  # fmt: skip
KIND_LABEL = {"internal": "intern", "dmz": "DMZ", "external": "extern",
              "management": "Management", "shared": "geteilt", "transfer": "Transfer"}  # fmt: skip
INTENTS = ("allow", "limited", "deny")
INTENT_LABEL = {"allow": "erlaubt", "limited": "eingeschränkt", "deny": "gesperrt"}
INTENT_WORDS = {
    "deny": ("gesperrt", "sperren", "deny", "denied", "block", "blocked", "verboten", "drop",
             "nein", "kein", "keine", "no"),
    "limited": ("eingeschränkt", "eingeschraenkt", "beschränkt", "beschraenkt", "limited",
                "restricted", "nur", "only", "teilweise"),
    "allow": ("erlaubt", "allow", "allowed", "permit", "permitted", "offen", "open", "ja", "ok"),
}  # fmt: skip
_RANK = {"allow": 0, "limited": 1, "deny": 2}
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<\s*(br|/div|/p|/li)\s*/?\s*>", re.IGNORECASE)
_WORD = re.compile(r"[\wäöüÄÖÜ]+")
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


class SchemaError(ValueError):
    """Zeichnung nicht lesbar - Meldung fuer den Anwender."""


def key_of(name: str | None, strip_digits: bool = False) -> str:
    from app.store.zone_detect import key_of as _key  # gemeinsame Normalisierung der Namen

    return _key(name, strip_digits)


@dataclass
class SchemaZone:
    name: str
    kind: str = "internal"
    kind_reason: str = "Standard"
    code: str | None = None
    color: str | None = None
    match: list[str] = field(default_factory=list)
    position: int = 0
    group: str | None = None
    description: str | None = None
    geometry: list[float] | None = None  # x, y, Breite, Hoehe (Vorschau)

    @property
    def key(self) -> str:
        return key_of(self.name)

    def out(self) -> dict:
        return {"name": self.name, "key": self.key, "kind": self.kind,
                "kind_reason": self.kind_reason, "code": self.code, "color": self.color,
                "match": self.match, "position": self.position, "group": self.group,
                "description": self.description, "geometry": self.geometry}  # fmt: skip


@dataclass
class SchemaLink:
    src: str
    dst: str
    intent: str = "allow"
    reason: str = "Standard"
    note: str | None = None

    def out(self) -> dict:
        return {"src": self.src, "dst": self.dst, "intent": self.intent, "reason": self.reason,
                "note": self.note}  # fmt: skip


@dataclass
class Schema:
    zones: list[SchemaZone] = field(default_factory=list)
    links: list[SchemaLink] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page: str | None = None
    pages: list[str] = field(default_factory=list)

    def zone(self, name: str) -> SchemaZone | None:
        k = key_of(name)
        return next((z for z in self.zones if z.key == k), None)

    def of_kind(self, kind: str) -> SchemaZone | None:
        return next((z for z in self.zones if z.kind == kind), None)

    def match(self, *names: str | None) -> SchemaZone | None:
        """Schema-Zone zu Namen: gleicher Name bzw. Kurzname (normalisiert), sonst das erste
        Erkennungsmuster, das auf einen der Namen passt."""
        raw = [n.strip().casefold() for n in names if n and n.strip()]
        if not raw:
            return None
        keys = {k for n in raw for k in (key_of(n), key_of(n, strip_digits=True)) if k}
        for z in self.zones:
            if z.key in keys or (z.code and key_of(z.code) in keys):
                return z
        values = raw + sorted(keys)
        for z in self.zones:
            if any(fnmatch.fnmatchcase(v, p) for p in z.match for v in values):
                return z
        return None

    def out(self) -> dict:
        return {"zones": [z.out() for z in self.zones], "links": [x.out() for x in self.links],
                "warnings": self.warnings, "page": self.page, "pages": self.pages}  # fmt: skip


# -- Zeichnung lesen ------------------------------------------------------------------------


def _guard(text: str) -> None:
    head = text[:4096].casefold()
    if "<!doctype" in head or "<!entity" in text.casefold():
        raise SchemaError("DTD bzw. Entitäten sind nicht erlaubt")


def _xml(text: str) -> ET.Element:
    _guard(text)
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise SchemaError(f"kein gültiges XML ({exc})") from exc


def _inflate(text: str) -> str:
    """Komprimierte draw.io-Seite: Base64 -> Deflate (roh) -> URL-kodiertes XML."""
    try:
        raw = base64.b64decode("".join(text.split()), validate=True)
        d = zlib.decompressobj(-15)
        data = d.decompress(raw, MAX_XML)
    except (ValueError, zlib.error) as exc:
        raise SchemaError("komprimierte Seite nicht lesbar") from exc
    if d.unconsumed_tail:
        raise SchemaError("Zeichnung entpackt zu gross")
    return unquote(data.decode("utf-8", errors="replace"))


def _model(root: ET.Element, out: Schema) -> ET.Element:
    if root.tag == "mxGraphModel":
        return root
    if root.tag != "mxfile":
        raise SchemaError("keine draw.io-Zeichnung (mxfile bzw. mxGraphModel erwartet)")
    pages = root.findall("diagram")
    if not pages:
        raise SchemaError("die Zeichnung hat keine Seite")
    out.pages = [p.get("name") or f"Seite {i + 1}" for i, p in enumerate(pages)]
    pick = next((p for p in pages if re.search(r"zone|schema", p.get("name") or "", re.I)),
                pages[0])  # fmt: skip
    out.page = pick.get("name") or "Seite 1"
    model = pick.find("mxGraphModel")
    if model is None:
        text = (pick.text or "").strip()
        if not text:
            raise SchemaError(f"Seite „{out.page}“ ist leer")
        model = _xml(_inflate(text))
        if model.tag != "mxGraphModel":
            raise SchemaError(f"Seite „{out.page}“ ist keine draw.io-Seite")
    if len(pages) > 1:
        out.warnings.append(f"{len(pages)} Seiten - gelesen wird „{out.page}“")
    return model


@dataclass
class _Cell:
    id: str
    label: str
    style: dict[str, str]
    parent: str | None
    vertex: bool
    edge: bool
    source: str | None
    target: str | None
    props: dict[str, str]
    geo: list[float] | None


def _style(text: str | None) -> dict[str, str]:
    out = {}
    for part in (text or "").split(";"):
        k, sep, v = part.partition("=")
        if k.strip():
            out[k.strip()] = v.strip() if sep else ""
    return out


def _text(value: str | None) -> str:
    """Beschriftung ohne HTML (draw.io speichert sie mit `html=1` als HTML)."""
    t = _BREAK.sub("\n", value or "")
    t = html.unescape(_TAG.sub("", t)).replace("\xa0", " ")
    return "\n".join(line.strip() for line in t.splitlines() if line.strip())


def _geo(cell: ET.Element) -> list[float] | None:
    g = cell.find("mxGeometry")
    if g is None or g.get("relative") == "1":
        return None
    try:
        return [float(g.get(k) or 0) for k in ("x", "y", "width", "height")]
    except ValueError:
        return None


def _cells(model: ET.Element) -> dict[str, _Cell]:
    root = model.find("root")
    if root is None:
        raise SchemaError("die Seite hat keinen Inhalt")
    out: dict[str, _Cell] = {}
    for el in root:
        if el.tag in ("object", "UserObject"):
            props = {k: v for k, v in el.attrib.items() if k not in ("id", "label")}
            label, cell, cid = el.get("label"), el.find("mxCell"), el.get("id")
            if cell is None:
                continue
        elif el.tag == "mxCell":
            props, label, cell, cid = {}, el.get("value"), el, el.get("id")
        else:
            continue
        if not cid:
            continue
        out[cid] = _Cell(cid, _text(label), _style(cell.get("style")), cell.get("parent"),
                         cell.get("vertex") == "1", cell.get("edge") == "1", cell.get("source"),
                         cell.get("target"), props, _geo(cell))  # fmt: skip
        if len(out) > 20 * (MAX_ZONES + MAX_LINKS):
            raise SchemaError("zu viele Elemente in der Zeichnung")
    return out


def _color(value: str | None) -> str | None:
    m = _HEX.match((value or "").strip())
    if not m:
        return None
    c = m.group(1)
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return f"#{c.lower()}"


def _hue_intent(color: str | None) -> str | None:
    """Linienfarbe: rot = gesperrt, orange/gelb = eingeschraenkt (sonst keine Aussage)."""
    c = _color(color)
    if c is None:
        return None
    r, g, b = (int(c[i : i + 2], 16) for i in (1, 3, 5))
    if r >= 150 and g <= 110 and b <= 110:
        return "deny"
    if r >= 180 and 110 < g <= 210 and b <= 120:
        return "limited"
    return None


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(text or "")}


def _label_intent(text: str) -> str | None:
    words = _words(text)
    for intent in ("deny", "limited", "allow"):
        if words & set(INTENT_WORDS[intent]):
            return intent
    return None


def _kind_of(value: str) -> str | None:
    return KIND_ALIAS.get(value.strip().casefold())


def _patterns(value: str | None) -> list[str]:
    out = []
    for p in re.split(r"[,;\n]+", value or ""):
        p = p.strip().casefold()
        if p and p not in out and len(p) <= 100:
            out.append(p)
    return out[:30]


def parse(content: str) -> Schema:
    """draw.io-Zeichnung -> Schema mit Hinweisen; `SchemaError`, wenn nicht lesbar."""
    from app.store.zone_detect import classify

    if len(content.encode("utf-8", errors="ignore")) > MAX_BYTES:
        raise SchemaError("Zeichnung zu gross (höchstens 2 MB)")
    out = Schema()
    cells = _cells(_model(_xml(content.lstrip("﻿").strip()), out))
    edges = {c.id for c in cells.values() if c.edge}

    def candidate(c: _Cell) -> bool:
        return (c.vertex and bool(c.label) and c.parent not in edges
                and "text" not in c.style and "edgeLabel" not in c.style
                and c.style.get("shape") != "text"
                and not {"automato_node", "automato_ignore"} & c.props.keys())  # fmt: skip

    cands = {cid for cid, c in cells.items() if candidate(c)}
    parents = {cells[c].parent for c in cands}
    groups = {cid for cid in cands & parents if "automato_kind" not in cells[cid].props}

    def absolute(c: _Cell) -> list[float] | None:
        if c.geo is None:
            return None
        x, y, w, h = c.geo
        p = cells.get(c.parent or "")
        while p is not None and p.vertex and p.geo is not None:
            x, y = x + p.geo[0], y + p.geo[1]
            p = cells.get(p.parent or "")
        return [round(x, 1), round(y, 1), round(w, 1), round(h, 1)]

    def group_of(c: _Cell) -> str | None:
        p = cells.get(c.parent or "")
        while p is not None:
            if p.id in groups:
                return p.label.splitlines()[0]
            p = cells.get(p.parent or "")
        return None

    by_cell: dict[str, SchemaZone] = {}
    seen: dict[str, SchemaZone] = {}

    index = {cid: i for i, cid in enumerate(cells)}

    def order(cid: str) -> tuple:
        """`automato_order`, sonst von oben nach unten und links nach rechts, sonst wie in der
        Datei."""
        raw = cells[cid].props.get("automato_order", "")
        pos = (absolute(cells[cid]) or [0, 0])[:2][::-1]
        first = (0, int(raw)) if raw.strip().isdigit() else (1, 0)
        return (*first, *pos, index[cid])

    ordered = sorted(cands - groups, key=order)
    for cid in ordered:
        c = cells[cid]
        lines = c.label.splitlines()
        name = (c.props.get("automato_zone") or lines[0]).strip()[:200]
        if not name:
            continue
        z = SchemaZone(name=name, group=group_of(c), geometry=absolute(c),
                       description="\n".join(lines[1:])[:500] or None)  # fmt: skip
        raw_kind = c.props.get("automato_kind", "").strip()
        if raw_kind:
            kind = _kind_of(raw_kind)
            if kind is None:
                out.warnings.append(f"Zone „{name}“: Art „{raw_kind}“ unbekannt - intern")
            z.kind, z.kind_reason = kind or "internal", "Eigenschaft automato_kind"
        elif hit := classify(name, c.props.get("automato_code")):
            z.kind, z.kind_reason = hit[0], f"Name „{hit[1]}“"
        z.code = (c.props.get("automato_code") or "").strip()[:50] or None
        z.color = _color(c.style.get("fillColor"))
        z.match = _patterns(c.props.get("automato_match"))
        if z.key in seen:
            out.warnings.append(f"Zone „{name}“ doppelt - die erste gilt")
            by_cell[cid] = seen[z.key]
            continue
        if len(seen) >= MAX_ZONES:
            out.warnings.append(f"mehr als {MAX_ZONES} Zonen - der Rest fehlt")
            break
        z.position = (len(seen) + 1) * 10
        seen[z.key] = z
        by_cell[cid] = z
    out.zones = list(seen.values())

    def zone_at(cid: str | None) -> SchemaZone | None:
        c = cells.get(cid or "")
        while c is not None:
            if c.id in by_cell:
                return by_cell[c.id]
            c = cells.get(c.parent or "")
        return None

    labels: dict[str, list[str]] = {}
    for c in cells.values():
        if c.vertex and c.parent in edges and c.label:
            labels.setdefault(c.parent, []).append(c.label)
    links: dict[tuple[str, str], SchemaLink] = {}
    loose = 0
    for c in cells.values():
        if not c.edge:
            continue
        a, b = zone_at(c.source), zone_at(c.target)
        if a is None or b is None:
            loose += 1
            continue
        if a is b:
            continue
        text = "\n".join([c.label, *labels.get(c.id, [])]).strip()
        raw = c.props.get("automato_intent", "").strip()
        if raw:
            intent = raw.casefold() if raw.casefold() in INTENTS else _label_intent(raw)
            reason = "Eigenschaft automato_intent"
            if intent is None:
                out.warnings.append(f"Übergang {a.name} → {b.name}: Absicht „{raw}“ unbekannt")
        else:
            intent, reason = _label_intent(text), "Beschriftung"
        if intent is None and c.style.get("dashed") == "1":
            intent, reason = "limited", "gestrichelt"
        if intent is None and (hue := _hue_intent(c.style.get("strokeColor"))):
            intent, reason = hue, "Linienfarbe"
        if intent is None:
            intent, reason = "allow", "Standard"
        start = c.style.get("startArrow", "none") not in ("none", "")
        end = c.style.get("endArrow", "classic") not in ("none", "")
        pairs = (
            [(a, b)] if end and not start else [(b, a)] if start and not end else [(a, b), (b, a)]
        )
        for s, d in pairs:
            k = (s.key, d.key)
            old = links.get(k)
            if old is not None and old.intent != intent:
                out.warnings.append(f"Übergang {s.name} → {d.name} mehrfach mit anderer Absicht - "
                                    "die strengere gilt")  # fmt: skip
                if _RANK[intent] <= _RANK[old.intent]:
                    continue
            links[k] = SchemaLink(s.name, d.name, intent, reason, text[:500] or None)
    if loose:
        out.warnings.append(f"{loose} Verbindung(en) nicht zwischen zwei Zonen - nicht gelesen")
    out.links = list(links.values())[:MAX_LINKS]
    if not out.zones:
        raise SchemaError("keine Zonen in der Zeichnung gefunden")
    return out


# -- Speichern und Laden --------------------------------------------------------------------


def _link_key(link: SchemaLink) -> str:
    key = f"{key_of(link.src)}>{key_of(link.dst)}"
    return key if len(key) <= 200 else hashlib.sha256(key.encode()).hexdigest()


def save(session: Session, profile_id: int, schema: Schema, content: str, filename: str | None,
         actor: str | None) -> None:  # fmt: skip
    """Schema des Profils ersetzen (Zonen, Uebergaenge, Zeichnung)."""
    remove(session, profile_id)
    for z in schema.zones:
        session.add(
            DictionaryEntry(
                profile_id=profile_id,
                kind=KIND_ZONE,
                key=z.name,
                value=z.kind,
                extra={k: v for k, v in z.out().items() if k not in ("name", "key", "kind")},
            )
        )
    for x in schema.links:
        session.add(DictionaryEntry(profile_id=profile_id, kind=KIND_LINK, key=_link_key(x),
                                    value=x.intent, extra=x.out()))  # fmt: skip
    info = {
        "content": content,
        "uploaded_by": actor,
        "uploaded_at": datetime.now(UTC).isoformat(),
        "page": schema.page,
        "pages": schema.pages,
        "warnings": schema.warnings,
    }
    session.add(DictionaryEntry(profile_id=profile_id, kind=KIND_META, key="drawing",
                                value=(filename or "")[:500] or None, extra=info))  # fmt: skip
    session.flush()


def remove(session: Session, profile_id: int) -> int:
    n = session.execute(
        delete(DictionaryEntry).where(
            DictionaryEntry.profile_id == profile_id, DictionaryEntry.kind.in_(SCHEMA_KINDS)
        )
    ).rowcount
    session.flush()
    return n or 0


def load(session: Session, profile_id: int | None) -> Schema | None:
    """Gespeichertes Schema des Profils (None = keines)."""
    if profile_id is None:
        return None
    rows = list(session.scalars(
        select(DictionaryEntry).where(
            DictionaryEntry.profile_id == profile_id,
            DictionaryEntry.kind.in_((KIND_ZONE, KIND_LINK, KIND_META)),
        )
    ))  # fmt: skip
    zones = [r for r in rows if r.kind == KIND_ZONE]
    if not zones:
        return None
    out = Schema()
    for r in zones:
        x = r.extra or {}
        kind = r.value if r.value in ZONE_KINDS else "internal"
        out.zones.append(SchemaZone(
            name=r.key, kind=kind, kind_reason=x.get("kind_reason") or "Standard",
            code=x.get("code"), color=x.get("color"), match=list(x.get("match") or []),
            position=int(x.get("position") or 0), group=x.get("group"),
            description=x.get("description"), geometry=x.get("geometry"),
        ))  # fmt: skip
    out.zones.sort(key=lambda z: (z.position, z.name.casefold()))
    for r in rows:
        if r.kind == KIND_LINK and r.value in INTENTS and r.extra:
            out.links.append(
                SchemaLink(
                    r.extra.get("src", ""),
                    r.extra.get("dst", ""),
                    r.value,
                    r.extra.get("reason") or "Standard",
                    r.extra.get("note"),
                )
            )
        if r.kind == KIND_META and r.extra:
            out.page, out.pages = r.extra.get("page"), list(r.extra.get("pages") or [])
            out.warnings = list(r.extra.get("warnings") or [])
    return out


def meta(session: Session, profile_id: int) -> DictionaryEntry | None:
    return session.scalar(
        select(DictionaryEntry).where(
            DictionaryEntry.profile_id == profile_id, DictionaryEntry.kind == KIND_META,
            DictionaryEntry.key == "drawing",
        )
    )  # fmt: skip


def for_tenant(session: Session, tenant_id: int) -> tuple[Schema | None, NamingProfile | None]:
    """Schema aus dem Namensprofil des Mandanten (eigenes bzw. „default“)."""
    pid = naming_profiles.profile_id_for_tenant(session, tenant_id)
    if pid is None:
        return None, None
    return load(session, pid), session.get(NamingProfile, pid)


def diff(old: Schema | None, new: Schema) -> dict:
    """Was eine neue Zeichnung gegenueber dem gespeicherten Schema aendert."""
    before = {z.key: z for z in (old.zones if old else [])}
    after = {z.key: z for z in new.zones}
    changed = []
    for k in before.keys() & after.keys():
        a, b = before[k], after[k]
        what = [
            f for f in ("kind", "code", "color", "match", "group") if getattr(a, f) != getattr(b, f)
        ]
        if what:
            changed.append({"name": b.name, "fields": what})
    lb = {(key_of(x.src), key_of(x.dst)): x for x in (old.links if old else [])}
    la = {(key_of(x.src), key_of(x.dst)): x for x in new.links}
    return {
        "zones": {"added": sorted(after[k].name for k in after.keys() - before.keys()),
                  "removed": sorted(before[k].name for k in before.keys() - after.keys()),
                  "changed": sorted(changed, key=lambda c: c["name"])},
        "links": {"added": [la[k].out() for k in la.keys() - lb.keys()],
                  "removed": [lb[k].out() for k in lb.keys() - la.keys()],
                  "changed": [{**la[k].out(), "before": lb[k].intent}
                              for k in la.keys() & lb.keys() if la[k].intent != lb[k].intent]},
    }  # fmt: skip


# -- Zeichnung erzeugen (Vorlage bzw. aus den aktuellen Zonen) ------------------------------

DEFAULT_ZONES = [
    ("Internet", "external", "INET", "#f8cecc", "internet*, inet*, untrust*, outside*, wan*"),
    ("DMZ", "dmz", "DMZ", "#ffe6cc", "dmz*"),
    ("Server", "internal", "SRV", "#dae8fc", "srv*, server*"),
    ("Clients", "internal", "CLT", "#d5e8d4", "client*, lan*, user*"),
    ("Shared Services", "shared", "SHR", "#e1d5e7", "shared*, common*"),
    ("Management", "management", "MGMT", "#eeeeee", "mgmt*, oob*, management*"),
    ("Transfer", "transfer", "TRF", "#f5f5f5", "transfer*, transit*"),
]  # fmt: skip
DEFAULT_LINKS = [
    ("Clients", "Internet", "allow"), ("Clients", "Server", "limited"),
    ("Server", "Internet", "limited"), ("Internet", "DMZ", "limited"),
    ("DMZ", "Server", "limited"), ("Internet", "Server", "deny"), ("Internet", "Clients", "deny"),
    ("Server", "Shared Services", "allow"), ("Management", "Server", "allow"),
]  # fmt: skip
_COLUMNS = ("external", "dmz", "internal", "shared", "management", "transfer")
_LINE = {"allow": "#2d7d46", "limited": "#d79b00", "deny": "#b85450"}


def default_schema() -> Schema:
    out = Schema()
    for i, (name, kind, code, color, match) in enumerate(DEFAULT_ZONES):
        out.zones.append(SchemaZone(name, kind, "Vorlage", code, color, _patterns(match),
                                    (i + 1) * 10))  # fmt: skip
    out.links = [SchemaLink(s, d, i, "Vorlage") for s, d, i in DEFAULT_LINKS]
    return out


def from_zones(session: Session, tenant_id: int) -> Schema:
    """Schema aus den logischen Zonen und Absichten des Mandanten (Ausgangspunkt fuer die
    Zeichnung)."""
    items = list(session.scalars(
        select(LogicalZone).where(LogicalZone.tenant_id == tenant_id)
        .order_by(LogicalZone.position, LogicalZone.name)
    ))  # fmt: skip
    names = {z.id: z.name for z in items}
    out = Schema()
    for i, z in enumerate(items):
        out.zones.append(SchemaZone(z.name, z.kind, "Zone in Automato", z.code, z.color, [],
                                    (i + 1) * 10, description=z.description))  # fmt: skip
    for p in session.scalars(select(ZonePolicy).where(ZonePolicy.tenant_id == tenant_id)):
        if p.src_zone_id in names and p.dst_zone_id in names:
            out.links.append(SchemaLink(names[p.src_zone_id], names[p.dst_zone_id], p.intent,
                                        "Absicht in Automato", p.note))  # fmt: skip
    return out


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def drawing(schema: Schema, title: str) -> str:
    """mxGraph-Datei (unkomprimiert) mit den Zonen als Formen samt Eigenschaften und den
    Uebergaengen als Pfeilen - in diagrams.net bearbeiten und wieder hochladen."""
    mxfile = ET.Element("mxfile", {"host": "Automato", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": "zonenschema", "name": "Zonenschema"})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1", "connect": "1",
        "arrows": "1", "fold": "1", "page": "1", "pageScale": "1", "pageWidth": "1400",
        "pageHeight": "900", "math": "0", "shadow": "0",
    })  # fmt: skip
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    legend = ("<b>" + _esc(title) + "</b><br>Jede Form = Zone (erste Zeile = Name). Eigenschaften "
              "(Daten bearbeiten): automato_kind (intern, DMZ, extern, Management, geteilt, "
              "Transfer), automato_code, automato_match (Muster, mit Komma getrennt).<br>Pfeil = "
              "Übergang; Beschriftung bzw. automato_intent: erlaubt, eingeschränkt, gesperrt "
              "(gestrichelt = eingeschränkt, rot = gesperrt).")  # fmt: skip
    head = ET.SubElement(root, "mxCell", {
        "id": "legend", "value": legend, "vertex": "1", "parent": "1",
        "style": "text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;fontSize=11;",
    })  # fmt: skip
    ET.SubElement(head, "mxGeometry", {"x": "20", "y": "10", "width": "900", "height": "60",
                                       "as": "geometry"})  # fmt: skip
    col: dict[str, int] = {}
    ids: dict[str, str] = {}
    for i, z in enumerate(schema.zones):
        c = _COLUMNS.index(z.kind) if z.kind in _COLUMNS else 2
        row = col.get(z.kind, 0)
        col[z.kind] = row + 1
        # Spalten nach Art, jede zweite versetzt - Pfeile laufen nicht durch fremde Zonen
        x, y, w, h = z.geometry or [20 + c * 220, 100 + row * 130 + (c % 2) * 65, 180, 70]
        zid = f"zone-{i + 1}"
        ids[z.key] = zid
        label = f"<b>{_esc(z.name)}</b>" + (f"<br>{_esc(z.description)}" if z.description else "")
        obj = ET.SubElement(root, "object", {
            "id": zid, "label": label, "automato_kind": KIND_LABEL.get(z.kind, z.kind),
            "automato_code": z.code or "", "automato_match": ", ".join(z.match),
            "automato_order": str(i + 1),
        })  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {
            "vertex": "1", "parent": "1",
            "style": f"rounded=1;whiteSpace=wrap;html=1;fillColor={z.color or '#dae8fc'};"
                     "strokeColor=#6c8ebf;fontSize=12;",
        })  # fmt: skip
        ET.SubElement(cell, "mxGeometry", {"x": str(x), "y": str(y), "width": str(w),
                                           "height": str(h), "as": "geometry"})  # fmt: skip
    for i, x in enumerate(schema.links):
        a, b = ids.get(key_of(x.src)), ids.get(key_of(x.dst))
        if a is None or b is None:
            continue
        style = f"endArrow=classic;html=1;strokeColor={_LINE[x.intent]};strokeWidth=2;"
        if x.intent == "limited":
            style += "dashed=1;"
        obj = ET.SubElement(root, "object", {"id": f"link-{i + 1}", "label": INTENT_LABEL[x.intent],
                                             "automato_intent": x.intent})  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {"edge": "1", "parent": "1", "source": a,
                                             "target": b, "style": style})  # fmt: skip
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    return ET.tostring(mxfile, encoding="unicode")
