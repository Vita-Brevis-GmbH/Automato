"""Kategorien der Applikations-Doku als Konfiguration (0.104.0, seit 0.106.0 einfach, ADR 0035).

Anwender 2026-10-28: „Kategorien im Namenskonzept mit den Spalten Titel, Titel English, Source
Zone, Dest Zone“ und „einfacher ist besser, in allem“. Eine **Kategorie** ist:

- Nummer, Titel (deutsch/englisch);
- eine **Richtung** Zonentyp der Quelle → Zonentyp des Ziels (`src`, `dst`; Zonen des
  Grundschemas im Namensprofil, `store/zone_types.py`; je Seite einer oder mehrere, z. B. Edge
  Internet und Public fuer „Internet“; `*` = jeder Zonentyp).

Seit 0.125.0 (ADR 0046, Anwender 2026-11-03: Firewall-Regeln mit Zielen in mehreren Zonen
standen nicht am richtigen Ort) je Seite ausserdem:

- ein Zonentyp mit **Zusatz** „(Applikation)“ bzw. „(andere)“ - die Seite, auf der die Systeme
  der Applikation stehen, bzw. die Gegenstelle (`DC (Applikation)` → `DC (andere)` = Zugriffe der
  Applikation auf andere DC-Zonen); ohne Zusatz beide;
- welche Zeilen die Kategorie zeigt (`src_scope`, `dst_scope`): **spezifisch** (Host bzw. Netz
  ganz in einer Zone; auf der Seite der Applikation das System direkt) und/oder
  **unspezifisch** (any, Netz ueber mehrere Zonen; bei der Applikation ein Subnetz, any, vzAny,
  Preferred Group). Abgewaehlte Zeilen stehen nur als Treffer unter „Regeln & Contracts“.

Drei Kategorien sind fest (Rolle; nur Titel und die Auswahl spezifisch/unspezifisch aenderbar):
**innerhalb der Applikation** (`aci_ap`, Server intern bzw. ACI im selben Application Profile),
**innerhalb des Security Tenants** (`aci_tenant`, ACI im selben Tenant bzw. Gegenstelle in der
eigenen Zone) und **unspezifisch** (`fallback`, keine Richtung passt).

Die **Vorgaben** stehen hier im Code - Richtungen auf der eingebauten Vorlage des Grundschemas
(DC, Edge Internet, Edge Office, …); die Plattform funktioniert ohne Konfiguration. Das
**Namensprofil** des Mandanten (`tenant.naming_profile_id`) passt sie an (Woerterbuch-Art
`appdoc_category`, Schluessel = Nummer; gespeichert nur Abweichungen, entfernte Vorgaben mit
`off`). Bereiche je Zone, Optionen, Spalten und Titel mit Zonen (0.104.0/0.105.0) gibt es nicht
mehr: die Spalten ergeben sich aus den Zeilen (`appdoc.table_columns`).

Rein Konfiguration der Plattform - nichts davon wirkt auf Geraete.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import DictionaryEntry

KIND_CATEGORY = "appdoc_category"
# Arten aus 0.104.0/0.105.0 (Bereiche, Optionen): nicht mehr gelesen, beim Speichern entfernt
LEGACY_KINDS = ("appdoc_area", "appdoc_option")
KINDS = (KIND_CATEGORY, *LEGACY_KINDS)
# feste Rollen: innerhalb der Applikation (7), innerhalb des Security Tenants (8), unspezifisch (10)
ROLES = ("aci_ap", "aci_tenant", "fallback")
MAX_INDEX, MAX_TEXT, MAX_TYPES = 99, 200, 20
ANY = "*"  # jeder Zonentyp
Types = tuple[str, ...]
SPECIFIC, UNSPECIFIC = "specific", "unspecific"
SCOPES: Types = (SPECIFIC, UNSPECIFIC)  # Vorgabe je Seite: beides zeigen
# Zusatz am Zonentyp (0.125.0): Seite der Applikation bzw. Gegenstelle - gespeichert deutsch
APP_SIDE, OTHER_SIDE = "app", "other"
_QUALIFIERS = {"applikation": APP_SIDE, "application": APP_SIDE, "andere": OTHER_SIDE,
               "other": OTHER_SIDE}  # fmt: skip


def split_type(name: str) -> tuple[str, str | None]:
    """Zonentyp und Zusatz: „DC (Applikation)“ → („DC“, app), „DC (andere)“ → („DC“, other)."""
    text = (name or "").strip()
    if text.endswith(")") and "(" in text:
        base, _, rest = text[:-1].rpartition("(")
        qual = _QUALIFIERS.get(rest.strip().lower())
        if qual and base.strip():
            return base.strip(), qual
    return text, None


def scopes_of(value) -> Types:
    """Auswahl einer Seite: spezifisch und/oder unspezifisch (Reihenfolge fest); sonst beides."""
    items = value if isinstance(value, list | tuple) else str(value or "").split(",")
    chosen = {str(x).strip().lower() for x in items}
    out = tuple(x for x in SCOPES if x in chosen)
    return out if value is not None else SCOPES


@dataclass(frozen=True)
class Category:
    index: int
    title: str  # deutsch (gespeichert), englisch bei der Ausgabe
    title_en: str
    src: Types = ()  # Zonentypen der Quelle bzw. `*`; leer bei festen Rollen
    dst: Types = ()
    role: str = ""
    builtin: bool = False
    src_scope: Types = SCOPES  # welche Quellen die Kategorie zeigt (0.125.0)
    dst_scope: Types = SCOPES

    def shows(self, src: str, dst: str) -> bool:
        """Zeigt die Kategorie eine Zeile mit dieser Quelle bzw. diesem Ziel (spezifisch bzw.
        unspezifisch)?"""
        return src in self.src_scope and dst in self.dst_scope

    @property
    def enforcement(self) -> str:
        """Anzeige in der Doku: ACI intern (innerhalb Applikation/Security Tenant) bzw. Firewall."""
        return "aci" if self.role in ("aci_ap", "aci_tenant") else "firewall"

    def out(self) -> dict:
        return {"index": self.index, "title": self.title, "title_en": self.title_en,
                "src": list(self.src), "dst": list(self.dst), "role": self.role,
                "enforcement": self.enforcement, "builtin": self.builtin,
                "src_scope": list(self.src_scope), "dst_scope": list(self.dst_scope)}  # fmt: skip


_C = Category
_INET = ("Edge Internet", "Public")  # Kante zum Internet und das Internet selbst
_CORP = ("Edge Office", "Core", "Office Distribution", "Office Access", "Branch Distribution",
         "Branch Access")  # Unternehmensnetzwerk  # fmt: skip
DEFAULT_CATEGORIES: tuple[Category, ...] = (
    _C(1, "Internet Zugriff", "Internet access", ("DC",), _INET, builtin=True),
    _C(2, "Zugriff aus dem Internet", "Access from the Internet", _INET, ("DC",), builtin=True),
    _C(3, "Zugriff in das Unternehmensnetzwerk", "Access to the corporate network", ("DC",),
       _CORP, builtin=True),
    _C(4, "Zugriff aus dem Unternehmensnetzwerk", "Access from the corporate network", _CORP,
       ("DC",), builtin=True),
    _C(5, "Zugriff aus dem VPN", "Access from the VPN", ("Edge VPN",), ("DC",), builtin=True),
    _C(6, "Zugriff in das VPN", "Access to the VPN", ("DC",), ("Edge VPN",), builtin=True),
    _C(7, "Zugriff innerhalb der Applikation", "Access within the application", role="aci_ap",
       builtin=True),
    _C(8, "Zugriff innerhalb des Security Tenant", "Access within the security tenant",
       role="aci_tenant", builtin=True),
    _C(9, "Zugriff zu anderen Security Tenants", "Access to other security tenants", ("DC",),
       ("DC",), builtin=True),
    _C(10, "Unspezifische Zugriffe auf die Applikation", "Unspecific access to the application",
       role="fallback", builtin=True),
    _C(11, "Zugriff Management", "Management access", ("Edge Management",), ("DC",),
       builtin=True),
)  # fmt: skip


@dataclass
class Catalog:
    categories: dict[int, Category] = field(default_factory=dict)
    profile_id: int | None = None

    def ordered(self) -> list[Category]:
        return [self.categories[i] for i in sorted(self.categories)]

    def get(self, index: int) -> Category | None:
        return self.categories.get(index)

    def role(self, role: str) -> int:
        """Nummer der Kategorie mit einer festen Rolle (eingebaut, immer vorhanden)."""
        return next(c.index for c in self.categories.values() if c.role == role)

    @property
    def fallback(self) -> int:
        return self.role("fallback")

    def title(self, index: int) -> str:
        """Titel in der Sprache der Anfrage."""
        c = self.categories.get(index)
        return L(c.title, c.title_en or c.title) if c else str(index)

    def by_flow(self, src: str | None, dst: str | None, app_side: str | None = None) -> int | None:
        """Kategorie der Richtung Zonentyp `src` → `dst`: die genaueste passende (beide genannt
        vor einem `*`, mit Zusatz „(Applikation)“/„(andere)“ vor ohne), bei Gleichstand die
        kleinste Nummer; keine = None. `app_side` = Seite der Applikation (`src`/`dst`)."""
        if not src or not dst:
            return None
        sides = ((type_key(src), APP_SIDE if app_side == "src" else OTHER_SIDE),
                 (type_key(dst), APP_SIDE if app_side == "dst" else OTHER_SIDE))  # fmt: skip
        best: tuple[int, int] | None = None
        for c in self.ordered():
            if c.role or not c.src or not c.dst:
                continue
            score = 0
            for (key, where), wanted in zip(sides, (c.src, c.dst), strict=True):
                points = _side_score(key, where if app_side else None, wanted)
                if points is None:
                    break
                score += points
            else:
                if best is None or score > best[0]:
                    best = (score, c.index)
        return best[1] if best else None

    def out(self) -> dict:
        return {"profile_id": self.profile_id, "categories": [c.out() for c in self.ordered()]}


def _side_score(key: str, where: str | None, wanted: Types) -> int | None:
    """Passt eine Seite (Zonentyp `key`, Seite der Applikation bzw. Gegenstelle) zu den
    Zonentypen einer Kategorie? -> Punkte (`*` 0, Zonentyp 2, mit passendem Zusatz 3) bzw. None.
    Ohne bekannte Seite zaehlt ein Zusatz nicht."""
    if ANY in wanted:
        return 0
    best = None
    for t in wanted:
        base, qual = split_type(t)
        if type_key(base) != key:
            continue
        if qual is None:
            best = max(best or 0, 2)
        elif where is not None and qual == where:
            best = 3
    return best


def type_key(name: str | None) -> str:
    """Zonentyp zum Vergleich: wie die Namen des Grundschemas normalisiert (`Z_DC` = `dc`)."""
    from app.store import zone_schema  # spaet: zone_schema -> naming_profiles -> ...

    return zone_schema.key_of(name)


def default() -> Catalog:
    return Catalog({c.index: c for c in DEFAULT_CATEGORIES})


def _int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def types_of(value) -> Types:
    """Zonentypen einer Seite: Liste bzw. Text mit Komma („Edge Internet, Public“), ohne doppelte;
    `*` allein, wenn genannt."""
    items = value if isinstance(value, list | tuple) else str(value or "").split(",")
    out: list[str] = []
    for item in items:
        t = str(item or "").strip()[:MAX_TEXT]
        if t and t not in out:
            out.append(t)
    return (ANY,) if ANY in out else tuple(out[:MAX_TYPES])


def _direction(x: dict, base: Category | None) -> tuple[Types, Types]:
    """Richtung aus dem Woerterbuch: `src`/`dst`, sonst die Richtungen aus 0.105.0 (`flows`)
    zusammen, sonst die Vorgabe."""
    if "src" in x or "dst" in x:
        return types_of(x.get("src")), types_of(x.get("dst"))
    flows = x.get("flows")
    if isinstance(flows, list) and flows:
        src: list[str] = []
        dst: list[str] = []
        for item in flows:
            if isinstance(item, str):
                for sep in ("→", "->", ">"):
                    if sep in item:
                        item = item.split(sep, 1)
                        break
            if isinstance(item, list | tuple) and len(item) == 2:
                src.append(str(item[0]))
                dst.append(str(item[1]))
        return types_of(src), types_of(dst)
    return (base.src, base.dst) if base else ((), ())


def apply_entries(cat: Catalog, entries: list[DictionaryEntry]) -> Catalog:
    """Eintraege eines Namensprofils auf die Vorgaben anwenden (unbekannte Werte: Vorgabe)."""
    for e in entries:
        if e.kind != KIND_CATEGORY:
            continue  # Bereiche und Optionen aus 0.104.0/0.105.0 gelten nicht mehr
        x = e.extra if isinstance(e.extra, dict) else {}
        index = _int(e.key, 0)
        if not 1 <= index <= MAX_INDEX:
            continue
        base = cat.categories.get(index)
        if x.get("off"):  # entfernte Vorgabe; die festen Kategorien bleiben
            if base is not None and not base.role:
                del cat.categories[index]
            continue
        src, dst = _direction(x, base)
        if base is not None and base.role:  # feste Rolle: nur Titel und Auswahl
            src, dst = (), ()
        cat.categories[index] = Category(
            index=index,
            title=(e.value or (base.title if base else str(index)))[:MAX_TEXT],
            title_en=str(x.get("title_en") or (base.title_en if base else "") or "")[:MAX_TEXT],
            src=src, dst=dst, role=base.role if base else "", builtin=bool(base and base.builtin),
            src_scope=scopes_of(x.get("src_scope")), dst_scope=scopes_of(x.get("dst_scope")),
        )  # fmt: skip
    return cat


def for_profile(session: Session, profile_id: int | None) -> Catalog:
    cat = default()
    cat.profile_id = profile_id
    if profile_id is None:
        return cat
    entries = list(session.scalars(
        select(DictionaryEntry).where(DictionaryEntry.profile_id == profile_id,
                                      DictionaryEntry.kind == KIND_CATEGORY)
        .order_by(DictionaryEntry.id)
    ))  # fmt: skip
    return apply_entries(cat, entries)


def for_tenant(session: Session, tenant_id: int | None) -> Catalog:
    """Katalog des Mandanten: Vorgaben, angepasst im Namensprofil des Mandanten (eigenes, sonst
    „default“ - wie die uebrigen Woerterbuecher)."""
    from app.store import naming_profiles  # spaet: naming_profiles -> ... -> appdoc

    return for_profile(session, naming_profiles.profile_id_for_tenant(session, tenant_id))


# -- Pflege im Namensprofil (API) ------------------------------------------------------------


class Invalid(ValueError):
    """Ungueltiger Katalog (422)."""


def validate(body: dict) -> Catalog:
    """Katalog aus der Pflege pruefen: Nummer 1-99, Titel, Quelle und Ziel (Zonentyp bzw. `*`).
    Die festen Kategorien fehlen nie (fehlen sie im Text, gilt die Vorgabe); die uebrigen
    Vorgaben fallen weg, wenn sie fehlen."""
    base = default()
    cat = Catalog({c.index: c for c in base.categories.values() if c.role})
    seen: set[int] = set()
    for item in body.get("categories") or []:
        index = _int(item.get("index"), 0)
        title = str(item.get("title") or "").strip()
        if not 1 <= index <= MAX_INDEX:
            raise Invalid(f"category number {item.get('index')!r} outside 1-{MAX_INDEX}")
        if index in seen:
            raise Invalid(f"category {index} twice")
        if not title:
            raise Invalid(f"category {index} without title")
        seen.add(index)
        known = base.categories.get(index)
        role = known.role if known else ""
        src, dst = ((), ()) if role else (types_of(item.get("src")), types_of(item.get("dst")))
        if not role and not (src and dst):
            raise Invalid(f"category {index} without source and destination zone")
        src_scope, dst_scope = scopes_of(item.get("src_scope")), scopes_of(item.get("dst_scope"))
        if not (src_scope and dst_scope):
            raise Invalid(f"category {index}: choose specific and/or unspecific on each side")
        cat.categories[index] = Category(
            index=index, title=title[:MAX_TEXT],
            title_en=str(item.get("title_en") or "").strip()[:MAX_TEXT],
            src=src, dst=dst, role=role, builtin=bool(known and known.builtin),
            src_scope=src_scope, dst_scope=dst_scope,
        )  # fmt: skip
    return cat


def entries_of(cat: Catalog) -> list[tuple[str, str, str, dict]]:
    """Woerterbuch-Eintraege (Art, Schluessel, Wert, Zusatz) fuer die Abweichungen von den
    Vorgaben - eingebaute unveraendert nicht gespeichert, entfernte mit `off`."""
    base = default()
    out: list[tuple[str, str, str, dict]] = []
    for c in cat.ordered():
        if base.categories.get(c.index) == c:
            continue
        extra = {"title_en": c.title_en, "src": list(c.src), "dst": list(c.dst)}
        if c.src_scope != SCOPES:
            extra["src_scope"] = list(c.src_scope)
        if c.dst_scope != SCOPES:
            extra["dst_scope"] = list(c.dst_scope)
        out.append((KIND_CATEGORY, str(c.index), c.title, extra))
    for d in base.ordered():
        if d.index not in cat.categories and not d.role:
            out.append((KIND_CATEGORY, str(d.index), d.title, {"off": True}))
    return out


def save(session: Session, profile_id: int, cat: Catalog) -> int:
    """Eintraege des Katalogs im Namensprofil ersetzen; Anzahl gespeicherter Abweichungen."""
    session.execute(
        DictionaryEntry.__table__.delete().where(
            DictionaryEntry.profile_id == profile_id, DictionaryEntry.kind.in_(KINDS)
        )
    )
    rows = entries_of(cat)
    for kind, key, value, extra in rows:
        session.add(DictionaryEntry(profile_id=profile_id, kind=kind, key=key, value=value,
                                    extra=extra))  # fmt: skip
    session.flush()
    return len(rows)
