"""Regelwerk der Applikations-Doku als Einstellung (0.118.0, ADR 0043).

Anwender 2026-10-30: „Können wir die Regeln in die Settings übernehmen und entsprechend aktivieren
oder deaktivieren? Und die Wirkung einstellen … die beiden Regelsets hinterlegen, damit man
umschalten kann. Wenn es neue Reihenfolgen gibt, immer dort ein neues Profil anlegen.“

Bisher entschied `appdoc_filter.place` fest im Code, welche Zeilen der Doku in den Kategorien
stehen, nur Treffer sind oder wegfallen. Jetzt ist das ein **Regelwerk** - wie ein Regelwerk
einer Firewall von oben nach unten gelesen:

- eine **Regel** = eine **Bedingung** (fest im Code, `CONDITIONS`: z. B. „nicht auf dem Weg“,
  „ins Internet“) mit Schalter (`on`) und **Wirkung** (`effect`, je Bedingung erlaubte aus
  `EFFECTS`); die erste eingeschaltete Regel, deren Bedingung eine Zeile trifft, entscheidet;
  trifft keine, steht die Zeile in ihrer Kategorie.
- **Ausnahme** (`except`, nur „direkt zugeordnet“): hebt die genannten Regeln (`lifts`)
  darunter fuer die Zeilen der direkt zugeordneten Regeln auf.
- ein **Profil** = die Regeln in ihrer Reihenfolge. Eingebaut (`BUILTIN`, unveraenderlich):
  Regelwerk 1 = Stand bis 0.117.0, Regelwerk 2 = Rueckmeldung 2026-10-30. Eigene Profile im
  Namensprofil des Mandanten (Woerterbuch-Art `appdoc_ruleset`, Schluessel = Profil), das aktive
  in `appdoc_ruleset_active`. Eine neue Reihenfolge = ein neues Profil (Regel des Anwenders);
  neue Bedingungen kommen in alten Profilen ausgeschaltet ans Ende.

Vorgabe ohne Einstellung: Regelwerk 1 - die Plattform laeuft ohne Konfiguration. Rein
Konfiguration der Plattform - nichts davon wirkt auf Geraete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import DictionaryEntry

KIND, KIND_ACTIVE = "appdoc_ruleset", "appdoc_ruleset_active"
KINDS = (KIND, KIND_ACTIVE)
ACTIVE_KEY = "active"
MAX_TEXT, MAX_PROFILES = 200, 30

# Wirkungen: wo eine Zeile steht
TABLE = "table"  # keine Regel trifft: in der Kategorie (nur Ergebnis, keine Wirkung einer Regel)
MARK = "mark"  # in der Kategorie, markiert
HIT = "hit"  # nur als Treffer („Regeln & Contracts“, Verbindungen), nicht in den Kategorien
NOTE = "note"  # nur als Vermerk an der Firewall-Regel (Spalte L3Out)
COUNTED = "counted"  # nicht gezeigt, nur gezaehlt
DROP = "drop"  # nicht gezeigt, nicht gezaehlt
EXCEPT = "except"  # Ausnahme: hebt die genannten Regeln darunter auf
EFFECTS = (MARK, HIT, NOTE, COUNTED, DROP, EXCEPT)
SHOWN = (TABLE, MARK)  # in den Kategorien
KEPT = (TABLE, MARK, HIT, NOTE)  # bleibt ein Treffer der Applikation
PATH = ("impossible", "off_path", "off_path_specific")  # nicht im Pfad (routingtechnisch)


def effect_label(effect: str) -> str:
    return {
        TABLE: L("in der Kategorie", "in the category"),
        MARK: L("in der Kategorie, markiert", "in the category, marked"),
        HIT: L("nur als Treffer (Regeln & Contracts)", "only as a hit (rules & contracts)"),
        NOTE: L("nur als Vermerk in der Spalte L3Out", "only as a note in the L3Out column"),
        COUNTED: L("nicht gezeigt, nur gezählt", "not shown, only counted"),
        DROP: L("nicht gezeigt, nicht gezählt", "not shown, not counted"),
        EXCEPT: L("Ausnahme für die Regeln darunter", "exception for the rules below"),
    }.get(effect, effect)


@dataclass(frozen=True)
class Condition:
    key: str
    title: str  # deutsch, englisch bei der Ausgabe
    title_en: str
    text: str
    text_en: str
    effects: tuple[str, ...]
    since: str = ""

    def out(self) -> dict:
        effects = [{"key": e, "label": effect_label(e)} for e in self.effects]
        return {"key": self.key, "title": L(self.title, self.title_en),
                "text": L(self.text, self.text_en), "since": self.since,
                "effects": effects}  # fmt: skip


_C = Condition
CONDITIONS: tuple[Condition, ...] = (
    _C("excluded", "Ausgeschlossene Regeln", "Excluded rules",
       "Der Applikation von Hand ausgeschlossen („Regel ausschliessen“).",
       "Excluded from the application by hand (“exclude rule”).", (DROP, COUNTED), "0.60.0"),
    _C("assigned", "Direkt zugeordnete Regeln", "Rules assigned directly",
       "Der Applikation von Hand zugeordnet – heben die gewählten Regeln darunter auf.",
       "Assigned to the application by hand – lift the selected rules below.", (EXCEPT,),
       "0.60.0"),
    _C("impossible", "Nicht im Pfad: Routing der Firewall", "Not on the path: firewall routing",
       "Ein- bzw. Ausgang der Firewall passt nicht zur IP des Systems – die Regel kann den "
       "Verkehr nie sehen (ADR 0019).",
       "Incoming or outgoing interface of the firewall does not match the IP of the system – the "
       "rule can never see the traffic (ADR 0019).", (COUNTED, DROP, HIT), "0.24.0"),
    _C("off_path", "Nicht im Pfad: Firewall nicht auf dem Weg (unspezifische Regel)",
       "Not on the path: firewall not on the path (unspecific rule)",
       "Regel über ein Netz bzw. any; der Weg zwischen Gegenstelle und System führt nicht über "
       "diese Firewall. Weg unbekannt bzw. Gegenstelle any: die Regel bleibt.",
       "Rule via a network or any; the path between counterpart and system does not pass this "
       "firewall. Path unknown or counterpart any: the rule stays.", (COUNTED, DROP, HIT),
       "0.113.2"),
    _C("off_path_specific", "Nicht im Pfad: Firewall nicht auf dem Weg (konkrete Regel)",
       "Not on the path: firewall not on the path (specific rule)",
       "Regel nennt das System (Host, Gruppe); der Weg zwischen Gegenstelle und System führt "
       "nicht über diese Firewall. Weg unbekannt bzw. Gegenstelle any: die Regel bleibt.",
       "Rule names the system (host, group); the path between counterpart and system does not "
       "pass this firewall. Path unknown or counterpart any: the rule stays.",
       (COUNTED, DROP, HIT), "0.118.0"),
    _C("to_internet", "Über das Internet-Gateway hinaus", "Beyond the internet gateway",
       "Das System steht nur über „all“ im Ziel einer Regel, deren Ausgang ins Internet führt "
       "(Anbindung Internet, Default-Route, SD-WAN) – der Verkehr müsste aus dem Internet "
       "zurück zur Applikation.",
       "The system is only covered via “all” in the destination of a rule whose exit leads to "
       "the internet (internet connection, default route, SD-WAN) – the traffic would have to "
       "come back from the internet to the application.", (COUNTED, DROP, HIT), "0.114.1"),
    _C("out_broad", "Ausgehend mit breiter Quelle", "Outbound with a broad source",
       "Ausgehende Firewall-Regel, die das System nur über ein Netz, einen Bereich bzw. any in "
       "der Quelle erfasst (z. B. 10.0.0.0/8) – kein Zugriff dieser Applikation.",
       "Outbound firewall rule that only covers the system via a network, a range or any in the "
       "source (e.g. 10.0.0.0/8) – no access of this application.", (COUNTED, DROP, HIT),
       "0.103.0"),
    _C("disabled", "Deaktivierte Firewall-Regeln", "Disabled firewall rules",
       "FortiGate-Policy mit Status „disable“ – lässt keinen Verkehr durch.",
       "FortiGate policy with status “disable” – passes no traffic.", (MARK, HIT, COUNTED, DROP),
       "0.118.0"),
    _C("l3out_helper", "L3Out-Contract mit Firewall dahinter (Hilfs-Contract)",
       "L3Out contract with a firewall behind it (helper contract)",
       "Contract zu externen EPGs, hinter deren L3Out eine Firewall steht – die Firewall "
       "entscheidet (ADR 0034).",
       "Contract to external EPGs with a firewall behind their L3Out – the firewall decides "
       "(ADR 0034).", (NOTE, HIT), "0.97.0"),
    _C("l3out_covered", "L3Out-Contract ohne bekannte Firewall, nicht enger als die Firewall",
       "L3Out contract without a known firewall, not narrower than the firewall",
       "Firewall hinter dem L3Out nicht bekannt; der Contract öffnet mindestens, was die "
       "Firewall-Regeln derselben Kategorie öffnen. Ein engerer Contract steht in der Kategorie "
       "(mit Befund).",
       "Firewall behind the L3Out not known; the contract opens at least what the firewall rules "
       "of the same category open. A narrower contract stays in the category (with finding).",
       (HIT, COUNTED, DROP), "0.78.2"),
    _C("cat10_out", "Unspezifische Kategorie: ausgehend", "Unspecific category: outbound",
       "Die unspezifische Kategorie zeigt nur Zugriffe auf die Applikation (eingehend), nicht "
       "ihre Zugriffe auf andere Systeme.",
       "The unspecific category only shows access to the application (inbound), not its access "
       "to other systems.", (HIT, COUNTED, DROP), "0.78.2"),
)  # fmt: skip
BY_KEY = {c.key: c for c in CONDITIONS}


@dataclass(frozen=True)
class Rule:
    key: str  # Bedingung
    on: bool = True
    effect: str = ""
    lifts: tuple[str, ...] = ()  # nur Ausnahme: aufgehobene Regeln

    def out(self) -> dict:
        c = BY_KEY[self.key]
        return {**c.out(), "on": self.on, "effect": self.effect,
                "effect_label": effect_label(self.effect), "lifts": list(self.lifts)}  # fmt: skip


@dataclass(frozen=True)
class Profile:
    key: str
    title: str
    title_en: str = ""
    rules: tuple[Rule, ...] = ()
    builtin: bool = False
    based_on: str = ""

    def active(self) -> list[Rule]:
        return [r for r in self.rules if r.on]

    def uses(self, key: str) -> bool:
        """Ist die Bedingung eingeschaltet (ausser als aufgehobene)?"""
        return any(r.on and r.key == key for r in self.rules)

    def position(self, key: str) -> int:
        return next((i for i, r in enumerate(self.rules) if r.key == key), len(self.rules))

    def out(self) -> dict:
        return {"key": self.key, "title": title(self), "title_de": self.title,
                "title_en": self.title_en, "builtin": self.builtin, "based_on": self.based_on,
                "rules": [r.out() for r in self.rules]}  # fmt: skip


_R = Rule
V1 = Profile(
    "v1", "Regelwerk 1 – Stand bis 0.117.0", "Ruleset 1 – as of 0.117.0",
    (
        _R("excluded", effect=DROP),
        _R("assigned", effect=EXCEPT, lifts=("out_broad", "to_internet")),
        _R("out_broad", effect=COUNTED),
        _R("to_internet", effect=COUNTED),
        _R("impossible", effect=COUNTED),
        _R("off_path", effect=COUNTED),
        _R("off_path_specific", on=False, effect=COUNTED),  # neu (0.118.0), aus
        _R("disabled", on=False, effect=MARK),  # neu (0.118.0), aus
        _R("l3out_helper", effect=NOTE),
        _R("l3out_covered", effect=HIT),
        _R("cat10_out", effect=HIT),
    ),
    builtin=True,
)  # fmt: skip
# Rueckmeldung 2026-10-30: 1 nicht im Pfad (routingtechnisch), 2 ueber das Internet-Gateway
# hinaus - beide nicht gezeigt, nicht gezaehlt; 3 direkt zugeordnete heben 1 und 2 auf,
# ausgeschlossene fallen weg; 4-6 wie bisher. Ausgehend mit breiter Quelle und deaktivierte
# Regeln (Vorschlaege) stehen ausgeschaltet vor 4-6 - dahinter wuerden sie z. B. von „nur als
# Treffer“ der unspezifischen Kategorie verdeckt.
V2 = Profile(
    "v2", "Regelwerk 2 – Rückmeldung 2026-10-30", "Ruleset 2 – feedback 2026-10-30",
    (
        _R("excluded", effect=DROP),
        _R("assigned", effect=EXCEPT,
           lifts=("impossible", "off_path", "off_path_specific", "to_internet")),
        _R("impossible", effect=DROP),
        _R("off_path", effect=DROP),
        _R("off_path_specific", effect=DROP),
        _R("to_internet", effect=DROP),
        _R("out_broad", on=False, effect=COUNTED),
        _R("disabled", on=False, effect=MARK),
        _R("l3out_helper", effect=NOTE),
        _R("l3out_covered", effect=HIT),
        _R("cat10_out", effect=HIT),
    ),
    builtin=True,
)  # fmt: skip
BUILTIN: tuple[Profile, ...] = (V1, V2)
DEFAULT = V1


def title(profile: Profile) -> str:
    """Titel in der Sprache der Anfrage."""
    return L(profile.title, profile.title_en or profile.title)


class Invalid(ValueError):
    """Ungueltiges Profil (422)."""


def _complete(rules: list[Rule]) -> tuple[Rule, ...]:
    """Bedingungen, die im Profil fehlen (neuer als das Profil): ausgeschaltet ans Ende, mit der
    ersten erlaubten Wirkung."""
    seen = {r.key for r in rules}
    more = [Rule(c.key, on=False, effect=c.effects[0]) for c in CONDITIONS if c.key not in seen]
    return (*rules, *more)


def validate_rules(items: list[dict]) -> tuple[Rule, ...]:
    """Regeln aus der Pflege pruefen: bekannte Bedingung, je einmal, erlaubte Wirkung; Ausnahmen
    nur fuer Bedingungen, die darunter stehen."""
    out: list[Rule] = []
    seen: set[str] = set()
    for item in items or []:
        key = str(item.get("key") or "")
        cond = BY_KEY.get(key)
        if cond is None:
            raise Invalid(f"unknown condition {key!r}")
        if key in seen:
            raise Invalid(f"condition {key!r} twice")
        seen.add(key)
        effect = str(item.get("effect") or cond.effects[0])
        if effect not in cond.effects:
            raise Invalid(f"effect {effect!r} not allowed for {key!r}")
        lifts: tuple[str, ...] = ()
        if effect == EXCEPT:
            lifts = tuple(dict.fromkeys(str(x) for x in item.get("lifts") or []))
            unknown = [x for x in lifts if x not in BY_KEY or x == key]
            if unknown:
                raise Invalid(f"exception lifts unknown conditions {unknown}")
        out.append(Rule(key, on=bool(item.get("on", True)), effect=effect, lifts=lifts))
    rules = _complete(out)
    for i, r in enumerate(rules):  # Ausnahme wirkt nur auf Regeln darunter
        below = {x.key for x in rules[i + 1 :]}
        if any(x not in below for x in r.lifts):
            raise Invalid(f"exception {r.key!r} lifts rules above it")
    return rules


def _from_entry(e: DictionaryEntry) -> Profile | None:
    x = e.extra if isinstance(e.extra, dict) else {}
    try:
        rules = validate_rules(x.get("rules") or [])
    except Invalid:
        return None  # gespeichert, aber nicht mehr gueltig: nicht anbieten
    return Profile(key=e.key, title=(e.value or e.key)[:MAX_TEXT],
                   title_en=str(x.get("title_en") or "")[:MAX_TEXT], rules=rules,
                   based_on=str(x.get("based_on") or ""))  # fmt: skip


def _entries(session: Session, profile_id: int | None, kind: str) -> list[DictionaryEntry]:
    if profile_id is None:
        return []
    stmt = (
        select(DictionaryEntry)
        .where(DictionaryEntry.profile_id == profile_id, DictionaryEntry.kind == kind)
        .order_by(DictionaryEntry.id)
    )
    return list(session.scalars(stmt))


def profiles(session: Session, profile_id: int | None) -> list[Profile]:
    """Eingebaute und eigene Profile des Namensprofils (eingebaute zuerst)."""
    own = [p for e in _entries(session, profile_id, KIND) if (p := _from_entry(e)) is not None]
    return [*(replace(p, rules=_complete(list(p.rules))) for p in BUILTIN), *own]


def active_key(session: Session, profile_id: int | None) -> str:
    entry = next(iter(_entries(session, profile_id, KIND_ACTIVE)), None)
    return entry.value if entry is not None and entry.value else DEFAULT.key


def for_profile(session: Session, profile_id: int | None) -> Profile:
    """Aktives Profil des Namensprofils; unbekannt bzw. ohne Profil: Vorgabe."""
    key = active_key(session, profile_id)
    found = {p.key: p for p in profiles(session, profile_id)}
    return found.get(key) or found[DEFAULT.key]


def for_tenant(session: Session, tenant_id: int | None) -> Profile:
    """Regelwerk des Mandanten: aktives Profil seines Namensprofils (eigenes, sonst „default“)."""
    from app.store import naming_profiles  # spaet: naming_profiles -> ... -> appdoc

    return for_profile(session, naming_profiles.profile_id_for_tenant(session, tenant_id))


# -- Pflege im Namensprofil (API) ------------------------------------------------------------


def set_active(session: Session, profile_id: int, key: str) -> Profile:
    found = {p.key: p for p in profiles(session, profile_id)}
    if key not in found:
        raise Invalid(f"unknown ruleset {key!r}")
    for e in _entries(session, profile_id, KIND_ACTIVE):
        session.delete(e)
    session.flush()
    session.add(DictionaryEntry(profile_id=profile_id, kind=KIND_ACTIVE, key=ACTIVE_KEY, value=key))
    session.flush()
    return found[key]


def _slug(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "regelwerk"
    key, n = f"p-{base}", 2
    while key in taken:
        key, n = f"p-{base}-{n}", n + 1
    return key


def save(session: Session, profile_id: int, body: dict, key: str | None = None) -> Profile:
    """Eigenes Profil speichern: neu (ohne `key`) bzw. ein eigenes ersetzen - eingebaute bleiben
    unveraendert. Eine geaenderte Reihenfolge nur als neues Profil (Anwender 2026-10-30)."""
    title = str(body.get("title") or "").strip()[:MAX_TEXT]
    if not title:
        raise Invalid("ruleset without title")
    rules = validate_rules(body.get("rules") or [])
    existing = {p.key: p for p in profiles(session, profile_id)}
    if key is not None:
        old = existing.get(key)
        if old is None or old.builtin:
            raise Invalid(f"ruleset {key!r} cannot be changed - save it as a new ruleset")
        if [r.key for r in old.rules] != [r.key for r in rules]:
            raise Invalid("new order - save it as a new ruleset")
    elif sum(1 for p in existing.values() if not p.builtin) >= MAX_PROFILES:
        raise Invalid(f"at most {MAX_PROFILES} own rulesets")
    based_on = str(body.get("based_on") or "")[:40]
    extra = {"title_en": str(body.get("title_en") or "").strip()[:MAX_TEXT],
             "based_on": based_on if based_on in existing else "",
             "rules": [{"key": r.key, "on": r.on, "effect": r.effect, "lifts": list(r.lifts)}
                       for r in rules]}  # fmt: skip
    entry = None
    if key is not None:
        entry = next((e for e in _entries(session, profile_id, KIND) if e.key == key), None)
    if entry is None:
        key = _slug(title, set(existing))
        entry = DictionaryEntry(profile_id=profile_id, kind=KIND, key=key)
        session.add(entry)
    entry.value, entry.extra = title, extra
    session.flush()
    return next(p for p in profiles(session, profile_id) if p.key == key)


def delete(session: Session, profile_id: int, key: str) -> None:
    """Eigenes Profil loeschen; war es aktiv, gilt wieder die Vorgabe."""
    entry = next((e for e in _entries(session, profile_id, KIND) if e.key == key), None)
    if entry is None:
        raise Invalid(f"ruleset {key!r} cannot be deleted")
    session.delete(entry)
    if active_key(session, profile_id) == key:
        for e in _entries(session, profile_id, KIND_ACTIVE):
            session.delete(e)
    session.flush()


def out(session: Session, profile_id: int | None) -> dict:
    """Pflege: Bedingungen, Profile und das aktive."""
    found = profiles(session, profile_id)
    return {"profile_id": profile_id, "active": for_profile(session, profile_id).key,
            "default": DEFAULT.key, "conditions": [c.out() for c in CONDITIONS],
            "profiles": [p.out() for p in found]}  # fmt: skip


@dataclass
class Counted:
    """Gezaehlte Zeilen je Bedingung (Wirkung `counted`) - je Regel einmal, bei der ersten
    Bedingung des Profils, und nur, wenn keine Zeile der Regel bleibt."""

    profile: Profile
    rows: dict[int, set[str]] = field(default_factory=dict)

    def add(self, rule_id: int | None, key: str) -> None:
        if rule_id is not None:
            self.rows.setdefault(rule_id, set()).add(key)

    def by_condition(self, kept: set[int]) -> dict[str, set[int]]:
        out: dict[str, set[int]] = {}
        for rid, keys in self.rows.items():
            if rid in kept:
                continue
            first = min(keys, key=self.profile.position)
            out.setdefault(first, set()).add(rid)
        return out
