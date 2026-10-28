"""Kategorien der Applikations-Doku als Konfiguration (0.104.0, ADR 0012 Ergaenzung 0.104.0).

Anwender 2026-10-28: „die Kategorien-Auswahl anpassen oder erweitern können – im Namenskonzept“
und „Konfiguration statt Code“. Bisher standen die Kategorien (Nummer, Titel, Spalten), die
Bereiche der Zonen (Internet, Unternehmensnetzwerk, VPN, Remote Access, andere Security Tenants,
DC Management) mit ihren Kategorien und die Merkmale im Namen der Firewall-Zone (`L3O_inet` …)
verteilt im Code. Jetzt beschreibt sie ein **Katalog**:

- **Kategorie**: Nummer, Titel (deutsch/englisch), Titel mit den Zonen („Zugriff aus {z}“),
  Durchsetzung (Firewall bzw. ACI intern), Spalten (`layout`), feste Rolle (ACI im AP/im Tenant/
  zu anderen Tenants, unspezifisch) und „nur mit Zone“.
- **Bereich** (Auswahl je Zone im Netzwerkdesign → Kategorien): Kuerzel, Bezeichnung, Kategorie
  eingehend und ausgehend, Merkmale im Namen der Firewall-Zone, Muster fuer den Vorschlag nach
  dem Zonennamen; „aus“ = nicht angeboten.
- **Optionen** der Doku: ausgehende Firewall-Regeln mit breiter Quelle ausblenden (0.103.0),
  unspezifische Kategorie nur eingehend (0.78.2).

Die **Vorgaben** stehen hier im Code - die Plattform funktioniert ohne Konfiguration (Regel der
Woerterbuecher). Das **Namensprofil** des Mandanten (`tenant.naming_profile_id`) kann sie
anpassen und erweitern: Woerterbuch-Arten `appdoc_category` (Schluessel = Nummer),
`appdoc_area` (Schluessel = Kuerzel) und `appdoc_option`. Eingebaute Eintraege lassen sich
anpassen, nicht loeschen (Bereiche „aus“); eigene dazu bzw. wieder weg. Feste Rollen (ACI
intern, unspezifisch, Internet fuer externe Gegenstellen und DNAT) bleiben an ihren Eintraegen.

Seit 0.105.0 (ADR 0035, Anwender 2026-10-28: „Kategorien als Richtung, z. B. DC zu
DC-Ressourcen, Internet zu DC“) haben Kategorien **Richtungen**: Paare Quelle → Ziel von
Zonentypen (Zonen des Grundschemas, `store/zone_types.py`; `*` = jeder). Sind Richtungen
gepflegt, entscheiden sie nach den Sonderfaellen „innerhalb der Applikation“ (Rolle `aci_ap`)
und „innerhalb der eigenen Zone bzw. des Security Tenants“ (`aci_tenant`); die Bereiche der Zonen
gelten dann nicht mehr. Ohne Richtungen bleibt alles wie bisher.

Rein Konfiguration der Plattform - nichts davon wirkt auf Geraete.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import DictionaryEntry

KIND_CATEGORY, KIND_AREA, KIND_OPTION = "appdoc_category", "appdoc_area", "appdoc_option"
KINDS = (KIND_CATEGORY, KIND_AREA, KIND_OPTION)
LAYOUTS = ("common", "internet_out", "internet_in")  # Spalten (`appdoc.LAYOUT_COLUMNS`)
ENFORCEMENTS = ("firewall", "aci")  # eigene Kategorien: Firewall (ueber die Bereiche)
# feste Rollen: ACI intern im AP (7), im Security Tenant (8), zu anderen Tenants (9),
# unspezifisch (10, Rueckfall); Bereich `external` = externe Gegenstelle bzw. DNAT (Internet)
ROLES = ("aci_ap", "aci_tenant", "aci_other", "fallback")
AREA_KEY = re.compile(r"^[a-z0-9_-]{1,8}$")  # `logical_zone.appdoc_area` ist String(8)
MAX_INDEX, MAX_LIST, MAX_TEXT = 99, 20, 200
MAX_FLOWS, ANY = 60, "*"  # Richtungen je Kategorie; `*` = jeder Zonentyp


@dataclass(frozen=True)
class Category:
    index: int
    title: str  # deutsch (gespeichert), englisch bei der Ausgabe
    title_en: str
    zone_title: str = ""  # mit „{z}“ = Namen der Zonen; leer = Titel bleibt
    zone_title_en: str = ""
    enforcement: str = "firewall"
    layout: str = "common"
    role: str = ""
    only_with_zones: bool = False  # nur mit einer Zone dieser Kategorie bzw. mit Zeilen
    builtin: bool = False
    # Richtungen (0.105.0): (Quelle, Ziel) je Paar - Zonentypen bzw. `*`
    flows: tuple[tuple[str, str], ...] = ()

    def out(self) -> dict:
        return {"index": self.index, "title": self.title, "title_en": self.title_en,
                "zone_title": self.zone_title, "zone_title_en": self.zone_title_en,
                "enforcement": self.enforcement, "layout": self.layout, "role": self.role,
                "only_with_zones": self.only_with_zones, "builtin": self.builtin,
                "flows": [list(f) for f in self.flows]}  # fmt: skip


@dataclass(frozen=True)
class Area:
    key: str
    label: str
    label_en: str
    inbound: int  # Kategorie, wenn die Gegenstelle die Quelle ist (System = Ziel)
    outbound: int  # Kategorie, wenn das System die Quelle ist
    markers: tuple[str, ...] = ()  # Teil im Namen der Firewall-Zone (`L3O_inet`)
    match: tuple[str, ...] = ()  # Muster fuer den Vorschlag nach dem Zonennamen (`*vpn*`)
    role: str = ""  # `external`: externe Gegenstelle bzw. DNAT
    off: bool = False  # nicht angeboten
    builtin: bool = False

    def tables(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys((self.inbound, self.outbound)))

    def out(self) -> dict:
        return {"key": self.key, "label": self.label, "label_en": self.label_en,
                "inbound": self.inbound, "outbound": self.outbound,
                "markers": list(self.markers), "match": list(self.match), "role": self.role,
                "off": self.off, "builtin": self.builtin}  # fmt: skip


@dataclass(frozen=True)
class Options:
    # ausgehende Firewall-Regeln, die das System nur ueber eine breite Quelle erfassen (0.103.0)
    outbound_broad_hidden: bool = True
    # unspezifische Kategorie nur eingehend (Zugriffe auf die Applikation, 0.78.2)
    fallback_inbound_only: bool = True

    def out(self) -> dict:
        return {"outbound_broad_hidden": self.outbound_broad_hidden,
                "fallback_inbound_only": self.fallback_inbound_only}  # fmt: skip


OPTION_NAMES = tuple(Options().out())

_C = Category
DEFAULT_CATEGORIES: tuple[Category, ...] = (
    _C(1, "Internet Zugriff", "Internet access", "{z} Zugriff", "{z} access",
       layout="internet_out", builtin=True),
    _C(2, "Zugriff aus dem Internet", "Access from the Internet", "Zugriff aus {z}",
       "Access from {z}", layout="internet_in", builtin=True),
    _C(3, "Zugriff in das Unternehmensnetzwerk", "Access to the corporate network",
       "Zugriff in {z}", "Access to {z}", builtin=True),
    _C(4, "Zugriff aus dem Unternehmensnetzwerk", "Access from the corporate network",
       "Zugriff aus {z}", "Access from {z}", builtin=True),
    _C(5, "Zugriff L3O_vpn", "Access L3O_vpn", "Zugriff {z}", "Access {z}", builtin=True),
    _C(6, "Zugriff L3O_sra (Remote Access)", "Access L3O_sra (remote access)", "Zugriff {z}",
       "Access {z}", builtin=True),
    _C(7, "Zugriff innerhalb des Applikationsprofils", "Access within the application profile",
       enforcement="aci", role="aci_ap", builtin=True),
    _C(8, "Zugriff innerhalb des Security Tenant", "Access within the security tenant",
       enforcement="aci", role="aci_tenant", builtin=True),
    _C(9, "Zugriff zu anderen Security Tenants", "Access to other security tenants",
       "Zugriff zu {z}", "Access to {z}", role="aci_other", builtin=True),
    _C(10, "Unspezifische Zugriffe auf die Applikation", "Unspecific access to the application",
       role="fallback", builtin=True),
    # 0.102.0: DC Management hinter einer eigenen Firewall - Firewall-Regeln und Contracts
    _C(11, "Zugriff Management", "Management access", "Zugriff {z}", "Access {z}",
       only_with_zones=True, builtin=True),
)  # fmt: skip
_A = Area
DEFAULT_AREAS: tuple[Area, ...] = (
    _A("inet", "Internet", "Internet", 2, 1, ("inet",), ("*internet*", "*inet*", "*public*"),
       role="external", builtin=True),
    _A("bit", "Unternehmensnetzwerk", "Corporate network", 3, 4, ("bit",),
       ("*office*", "*eoff*", "bit", "bit-*", "*-bit", "*büro*", "*buero*", "*unternehmen*"),
       builtin=True),
    _A("vpn", "VPN", "VPN", 5, 5, ("vpn",), ("*vpn*",), builtin=True),
    _A("sra", "Remote Access", "Remote access", 6, 6, ("sra",), ("sra", "sra-*", "*-sra",
       "*remote*"), builtin=True),
    _A("res", "andere Security Tenants", "Other security tenants", 9, 9, ("res",),
       ("res", "res-*", "res_*", "*-res", "*_res"), builtin=True),
    _A("mgmt", "DC Management", "DC management", 11, 11, (), ("*management*", "*managment*",
       "*mgmt*"), builtin=True),
)  # fmt: skip
# Reihenfolge der Merkmale im Namen der Firewall-Zone wie bisher (`L3O_inet`, `sra` vor `vpn`)
_MARKER_ORDER = ("inet", "sra", "vpn", "res", "bit")


@dataclass
class Catalog:
    categories: dict[int, Category] = field(default_factory=dict)
    areas: dict[str, Area] = field(default_factory=dict)
    options: Options = field(default_factory=Options)
    profile_id: int | None = None

    # -- Kategorien ------------------------------------------------------------------------

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

    def zone_title(self, index: int, names: list[str], shown: int = 3) -> str:
        """Titel mit den Namen der Zonen („Zugriff aus Edge Internet“); ohne Vorlage der Titel."""
        c = self.categories.get(index)
        if c is None or not names or not c.zone_title:
            return self.title(index)
        text = ", ".join(names[:shown]) + (" …" if len(names) > shown else "")
        return L(c.zone_title, c.zone_title_en or c.zone_title).replace("{z}", text)

    def only_with_zones(self) -> set[int]:
        return {c.index for c in self.categories.values() if c.only_with_zones}

    # -- Richtungen (0.105.0) --------------------------------------------------------------

    @property
    def has_flows(self) -> bool:
        """Sind Richtungen gepflegt? Dann gelten sie statt der Bereiche der Zonen."""
        return any(c.flows for c in self.categories.values())

    def by_flow(self, src: str | None, dst: str | None) -> int | None:
        """Kategorie der Richtung Zonentyp `src` → `dst`: die genaueste passende (beide genannt
        vor einem `*`), bei Gleichstand die kleinste Nummer; keine = None."""
        if not src or not dst:
            return None
        a, b = type_key(src), type_key(dst)
        best: tuple[int, int] | None = None
        for c in self.ordered():
            for s, d in c.flows:
                if (s != ANY and type_key(s) != a) or (d != ANY and type_key(d) != b):
                    continue
                score = (s != ANY) + (d != ANY)
                if best is None or score > best[0]:
                    best = (score, c.index)
        return best[1] if best else None

    # -- Bereiche --------------------------------------------------------------------------

    def active_areas(self) -> list[Area]:
        return [a for a in self.areas.values() if not a.off]

    def area(self, key: str | None) -> Area | None:
        a = self.areas.get(key or "")
        return a if a is not None and not a.off else None

    def area_tables(self, key: str | None) -> tuple[int, ...]:
        a = self.area(key)
        return a.tables() if a else ()

    def area_label(self, key: str) -> str:
        a = self.areas.get(key)
        return L(a.label, a.label_en or a.label) if a else key

    def kind_of_zone(self, name: str | None) -> str | None:
        """Bereich nach dem Merkmal im Namen der Firewall-Zone (`L3O_inet` -> inet)."""
        if not name:
            return None
        low = name.lower()
        for a in self._marker_order():
            if any(m and m.lower() in low for m in a.markers):
                return a.key
        return None

    def _marker_order(self) -> list[Area]:
        known = [self.areas[k] for k in _MARKER_ORDER if k in self.areas]
        rest = [a for k, a in self.areas.items() if k not in _MARKER_ORDER]
        return [a for a in known + rest if not a.off]

    def suggest(self, name: str, code: str | None = None) -> str | None:
        """Bereich nach dem Namen bzw. Kurznamen einer Zone (Muster je Bereich, erste passt)."""
        words = [w for w in {(name or "").lower(), (code or "").lower()} if w]
        parts = {p for w in words for p in re.split(r"[\s/]+", w) if p} | set(words)
        for a in self._suggest_order():
            for pattern in a.match:
                pat = pattern.lower()
                if any(fnmatch.fnmatchcase(p, pat) for p in parts):
                    return a.key
        return None

    def _suggest_order(self) -> list[Area]:
        # Management vor Internet/Office („Edge Management“), Remote Access vor VPN
        first = [k for k in ("mgmt", "inet", "sra", "vpn", "bit", "res") if k in self.areas]
        keys = first + [k for k in self.areas if k not in first]
        return [self.areas[k] for k in keys if not self.areas[k].off]

    # -- Zuordnung -------------------------------------------------------------------------

    def categorize(self, *, direction: str, counterpart_zone: str | None,
                   counterpart_external: bool, is_dnat_target: bool,
                   area: str | None = None) -> int:  # fmt: skip
        """Kategorie einer Firewall-Regel (pure): der Bereich der Zone der Gegenstelle geht dem
        Merkmal im Namen der Firewall-Zone vor; externe Gegenstelle bzw. DNAT = Bereich mit der
        Rolle `external` (Internet)."""
        key = area if self.area(area) else self.kind_of_zone(counterpart_zone)
        external = next((a for a in self.active_areas() if a.role == "external"), None)
        chosen = self.area(key)
        if direction == "in":
            if external and (is_dnat_target or counterpart_external):
                return external.inbound
            return chosen.inbound if chosen else self.fallback
        if external and counterpart_external:
            return external.outbound
        return chosen.outbound if chosen else self.fallback

    def categorize_aci(self, scope: str | None, same_ap: bool, same_tenant: bool) -> int:
        """ACI intern: im AP, im Security Tenant, zu anderen Security Tenants."""
        if scope == "application-profile" or same_ap:
            return self.role("aci_ap")
        if scope == "global" or not same_tenant:
            return self.role("aci_other")
        return self.role("aci_tenant")

    # -- Ausgabe ---------------------------------------------------------------------------

    def out(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "categories": [c.out() for c in self.ordered()],
            "areas": [a.out() for a in self.areas.values()],
            "options": self.options.out(),
        }


def type_key(name: str | None) -> str:
    """Zonentyp zum Vergleich: wie die Namen des Grundschemas normalisiert (`Z_DC` = `dc`)."""
    from app.store import zone_schema  # spaet: zone_schema -> naming_profiles -> ...

    return zone_schema.key_of(name)


def _flows(value) -> tuple[tuple[str, str], ...]:
    """Richtungen aus der Pflege bzw. dem Woerterbuch: [[Quelle, Ziel], …] oder „A → B“."""
    out: list[tuple[str, str]] = []
    for item in value if isinstance(value, list | tuple) else []:
        if isinstance(item, str):
            for sep in ("→", "->", ">"):
                if sep in item:
                    item = item.split(sep, 1)
                    break
        if not isinstance(item, list | tuple) or len(item) != 2:
            continue
        src, dst = (str(x).strip()[:MAX_TEXT] for x in item)
        if src and dst and (src, dst) not in out:
            out.append((src, dst))
    return tuple(out[:MAX_FLOWS])


def default() -> Catalog:
    return Catalog({c.index: c for c in DEFAULT_CATEGORIES},
                   {a.key: a for a in DEFAULT_AREAS}, Options())  # fmt: skip


def _texts(value, limit: int = MAX_LIST) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(v).strip()[:50] for v in value[:limit] if str(v).strip())


def _int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def apply_entries(cat: Catalog, entries: list[DictionaryEntry]) -> Catalog:
    """Eintraege eines Namensprofils auf die Vorgaben anwenden (unbekannte Werte: Vorgabe)."""
    for e in entries:
        x = e.extra if isinstance(e.extra, dict) else {}
        if e.kind == KIND_CATEGORY:
            index = _int(e.key, 0)
            if not 1 <= index <= MAX_INDEX:
                continue
            base = cat.categories.get(index) or Category(index, e.value or str(index), "")
            cat.categories[index] = replace(
                base,
                title=(e.value or base.title)[:MAX_TEXT],
                title_en=str(x.get("title_en") or base.title_en or "")[:MAX_TEXT],
                zone_title=str(x.get("zone_title", base.zone_title) or "")[:MAX_TEXT],
                zone_title_en=str(x.get("zone_title_en", base.zone_title_en) or "")[:MAX_TEXT],
                # Rolle und Durchsetzung eingebauter Kategorien bleiben (feste Bedeutung); eigene
                # sind Firewall-Kategorien (erreicht ueber die Bereiche der Zonen)
                enforcement=base.enforcement if base.builtin else "firewall",
                layout=x.get("layout") if x.get("layout") in LAYOUTS else base.layout,
                only_with_zones=bool(x.get("only_with_zones", base.only_with_zones)),
                flows=_flows(x["flows"]) if "flows" in x else base.flows,
            )  # fmt: skip
        elif e.kind == KIND_AREA:
            key = (e.key or "").strip().lower()
            if not AREA_KEY.match(key):
                continue
            base = cat.areas.get(key)
            inbound = _int(x.get("inbound"), base.inbound if base else 0)
            outbound = _int(x.get("outbound"), base.outbound if base else inbound)
            cat.areas[key] = Area(
                key=key,
                label=(e.value or (base.label if base else key))[:MAX_TEXT],
                label_en=str(x.get("label_en") or (base.label_en if base else ""))[:MAX_TEXT],
                inbound=inbound,
                outbound=outbound,
                markers=_texts(x["markers"]) if "markers" in x else (base.markers if base else ()),
                match=_texts(x["match"]) if "match" in x else (base.match if base else ()),
                role=base.role if base else "",
                off=bool(x.get("off", False)),
                builtin=bool(base and base.builtin),
            )
        elif e.kind == KIND_OPTION and e.key in OPTION_NAMES:
            flag = (e.value or "").lower() in ("on", "1", "true")
            cat.options = replace(cat.options, **{e.key: flag})
    # Bereiche auf unbekannte Kategorien: nicht angeboten
    for key, a in list(cat.areas.items()):
        if a.inbound not in cat.categories or a.outbound not in cat.categories:
            cat.areas[key] = replace(a, off=True)
    return cat


def for_profile(session: Session, profile_id: int | None) -> Catalog:
    cat = default()
    cat.profile_id = profile_id
    if profile_id is None:
        return cat
    entries = list(session.scalars(
        select(DictionaryEntry).where(DictionaryEntry.profile_id == profile_id,
                                      DictionaryEntry.kind.in_(KINDS))
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
    """Katalog aus der Pflege pruefen: Kategorien (Nummer 1-99, Titel), Bereiche (Kuerzel,
    Kategorien vorhanden), Optionen. Eingebaute Kategorien bzw. Bereiche fehlen nie (fehlen sie
    im Text, gilt die Vorgabe)."""
    cat = default()
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
        base = cat.categories.get(index)
        layout = item.get("layout") or (base.layout if base else "common")
        if layout not in LAYOUTS:
            raise Invalid(f"unknown layout {layout!r}")
        enforcement = base.enforcement if base else "firewall"  # eigene: ueber die Bereiche
        cat.categories[index] = Category(
            index=index, title=title[:MAX_TEXT],
            title_en=str(item.get("title_en") or "").strip()[:MAX_TEXT],
            zone_title=str(item.get("zone_title") or "").strip()[:MAX_TEXT],
            zone_title_en=str(item.get("zone_title_en") or "").strip()[:MAX_TEXT],
            enforcement=enforcement, layout=layout, role=base.role if base else "",
            only_with_zones=bool(item.get("only_with_zones")), builtin=bool(base and base.builtin),
            flows=_flows(item.get("flows")),
        )  # fmt: skip
        if len(item.get("flows") or []) > MAX_FLOWS:
            raise Invalid(f"category {index}: more than {MAX_FLOWS} directions")
    keys: set[str] = set()
    for item in body.get("areas") or []:
        key = str(item.get("key") or "").strip().lower()
        if not AREA_KEY.match(key):
            raise Invalid(f"area key {item.get('key')!r}: 1-8 letters, digits, - or _")
        if key in keys:
            raise Invalid(f"area {key!r} twice")
        keys.add(key)
        label = str(item.get("label") or "").strip()
        if not label:
            raise Invalid(f"area {key!r} without label")
        inbound, outbound = _int(item.get("inbound"), 0), _int(item.get("outbound"), 0)
        for n in (inbound, outbound):
            c = cat.categories.get(n)
            if c is None:
                raise Invalid(f"area {key!r}: category {n} does not exist")
            if c.enforcement != "firewall":
                raise Invalid(f"area {key!r}: category {n} is ACI internal")
        base = cat.areas.get(key)
        cat.areas[key] = Area(
            key=key, label=label[:MAX_TEXT],
            label_en=str(item.get("label_en") or "").strip()[:MAX_TEXT],
            inbound=inbound, outbound=outbound, markers=_texts(item.get("markers")),
            match=_texts(item.get("match")), role=base.role if base else "",
            off=bool(item.get("off")), builtin=bool(base and base.builtin),
        )  # fmt: skip
    options = body.get("options") or {}
    unknown = [k for k in options if k not in OPTION_NAMES]
    if unknown:
        raise Invalid(f"unknown option {unknown[0]!r}")
    cat.options = replace(cat.options, **{k: bool(v) for k, v in options.items()})
    return cat


def entries_of(cat: Catalog) -> list[tuple[str, str, str, dict]]:
    """Woerterbuch-Eintraege (Art, Schluessel, Wert, Zusatz) fuer die Abweichungen von den
    Vorgaben - eingebaute unveraendert nicht gespeichert."""
    base = default()
    out: list[tuple[str, str, str, dict]] = []
    for c in cat.ordered():
        d = base.categories.get(c.index)
        if d is not None and replace(c, builtin=d.builtin) == d:
            continue
        out.append((KIND_CATEGORY, str(c.index), c.title,
                    {"title_en": c.title_en, "zone_title": c.zone_title,
                     "zone_title_en": c.zone_title_en, "enforcement": c.enforcement,
                     "layout": c.layout, "only_with_zones": c.only_with_zones,
                     "flows": [list(f) for f in c.flows]}))  # fmt: skip
    for a in cat.areas.values():
        d = base.areas.get(a.key)
        if d is not None and replace(a, builtin=d.builtin, role=d.role) == d:
            continue
        extra = {"label_en": a.label_en, "inbound": a.inbound, "outbound": a.outbound,
                 "markers": list(a.markers), "match": list(a.match), "off": a.off}  # fmt: skip
        out.append((KIND_AREA, a.key, a.label, extra))
    for name, value in cat.options.out().items():
        if value != getattr(base.options, name):
            out.append((KIND_OPTION, name, "on" if value else "off", {}))
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
