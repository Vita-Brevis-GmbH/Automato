"""ACI ist zustandslos: Richtung der Contracts (0.69.1, Anwender).

Ein Contract-Subject mit „Apply Both Directions“ (Standard) laesst Consumer -> Provider nach
seinen Filtern zu und den Rueckweg Provider -> Consumer mit vertauschten Ports („Reverse
Filter Ports“, Standard). Ohne Zustand im Fabric heisst das:

- Filter **ohne Ports** (`ip/any`, `tcp/any`, `udp/any`, ICMP): der Rueckweg passt auf jeden
  solchen Verkehr - der Provider kann selbst Verbindungen zum Consumer aufbauen, der Contract
  gilt in **beide Richtungen**.
- Filter **mit Zielport** (`tcp/443`): zurueck nur Pakete mit Quellport 443 - Antworten, keine
  neuen Verbindungen (fast zustandsbehaftet); `stateful` an TCP-Eintraegen (zurueck nur mit
  ACK) ebenso.
- Ohne „Apply Both Directions“ gelten die Filter je Richtung (vzInTerm Consumer -> Provider,
  vzOutTerm Provider -> Consumer); ohne „Reverse Filter Ports“ die Filter unveraendert auch
  zurueck.

Der Collector rechnet je Subject die Dienste der Rueckrichtung aus (`raw.reverse`, seit
0.69.1). Fuer aeltere Laeufe gilt der Standard des APIC: die Filter ohne Ports.

**Nur Antworten** (0.72.1): Hat ein Subject seit 0.69.1 (`raw.reverse` gesetzt) keine Dienste
Consumer -> Provider, lassen seine Filter in dieser Richtung nur Antworten zu (Eintraege nur mit
Quellport, z. B. 443, bzw. TCP ohne SYN) - der Contract baut dort keine Verbindung auf
(`replies_only`); bisher war das Urteil „moeglich“. Taboos sperren
nur zum geschuetzten EPG; Preferred Groups verbinden ihre Mitglieder ohnehin in beide
Richtungen. Genutzt von der Weg-Suche (`aci_check`), der Applikations-Doku, den Verbindungen
Ende zu Ende und der Zonenmatrix.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Rule, RuleAction, RuleDomain, ServiceObject
from app.models.rule import rule_service

_ANY = ("", "any", "unspecified")
_PORTLESS = ("ip", "all", "unspecified", "tcp", "udp", "icmp", "icmpv4", "icmpv6")
# Deutsche Fassungen als Konstanten (bisherige Verwender); in Antworten `text()` bzw. `replies()`
TEXT = "ACI ist zustandslos: Filter ohne Ports gelten auch vom Provider zum Consumer"
REPLIES = "Filter nur mit Quellport bzw. ohne SYN: Consumer → Provider nur Antworten"


def text() -> str:
    """Erklaerung der Rueckrichtung in der Sprache der Anfrage."""
    return L(TEXT, "ACI is stateless: filters without ports also apply from Provider to Consumer")


def replies() -> str:
    """Hinweis „nur Antworten“ in der Sprache der Anfrage."""
    return L(
        REPLIES, "Filters only with source port or without SYN: Consumer → Provider replies only"
    )


def portless(name: str) -> bool:
    proto, _, port = (name or "").lower().partition("/")
    return proto in _PORTLESS and port in _ANY


def reverse_of(raw: dict | None, services: list[str]) -> list[str]:
    """Dienste, die der Provider zum Consumer aufbauen kann (leer = nur Antworten)."""
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("kind") in ("taboo", "preferred_group"):
        return []
    if isinstance(raw.get("reverse"), list):
        return [str(s) for s in raw["reverse"]]
    return [s for s in services if portless(s)]


def replies_only(raw: dict | None, services: list[str]) -> bool:
    """Oeffnet der Contract Consumer -> Provider nichts (nur Antworten)? Erst ab Laeufen mit
    `raw.reverse` (0.69.1) sicher - davor fehlten die Dienste bei solchen Filtern nicht."""
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("kind") not in (None, "contract"):
        return False
    return isinstance(raw.get("reverse"), list) and not services


def reverse(rule: Rule) -> list[str]:
    """Rueckrichtung einer Regel (nur erlaubende ACI-Contracts)."""
    if rule.domain is not RuleDomain.ACI_CONTRACT or rule.action is not RuleAction.ALLOW:
        return []
    return reverse_of(rule.raw, [s.canonical_name for s in rule.services])


def info(rule: Rule) -> dict | None:
    """Fuer das Regel-Detail: Dienste der Rueckrichtung mit Erklaerung."""
    if rule.domain is not RuleDomain.ACI_CONTRACT or rule.action is not RuleAction.ALLOW:
        return None
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    if raw.get("kind") in ("taboo", "preferred_group"):
        return None
    back = reverse(rule)
    only = replies_only(raw, [s.canonical_name for s in rule.services])
    names = ", ".join(back)
    if only and back:
        note = L(f"nur Provider → Consumer: {names} (Consumer → Provider nur Antworten – Filter "
                 "nur mit Quellport)",
                 f"Provider → Consumer only: {names} (Consumer → Provider replies only – filters "
                 "only with source port)")  # fmt: skip
    elif only:
        note = L("öffnet keine Verbindung: in beide Richtungen nur Antworten (Filter nur mit "
                 "Quellport)",
                 "opens no connection: replies only in both directions (filters only with "
                 "source port)")  # fmt: skip
    elif back:
        note = L(f"gilt in beide Richtungen: der Provider kann {names} zum Consumer aufbauen "
                 "(ACI ist zustandslos, Filter ohne Ports)",
                 f"applies in both directions: the Provider can open {names} to the Consumer "
                 "(ACI is stateless, filters without ports)")  # fmt: skip
    else:
        note = L("nur Consumer → Provider: zurück nur Antworten (Filter mit Zielport bzw. "
                 "stateful)",
                 "Consumer → Provider only: replies only on the way back (filters with "
                 "destination port or stateful)")  # fmt: skip
    return {"reverse": back, "both": bool(back) and not only, "text": note,
            "replies_only": only, "derived": not isinstance(raw.get("reverse"), list)}  # fmt: skip


def directions(session: Session, tenant_id: int, rule_ids=None) -> tuple[dict, set[int]]:
    """(Regel-ID -> Dienste der Rueckrichtung, Contracts nur mit Antworten Consumer ->
    Provider) fuer die erlaubenden Contracts des Mandanten - eine Abfrage fuer beides."""
    where = [Rule.tenant_id == tenant_id, Rule.domain == RuleDomain.ACI_CONTRACT,
             Rule.action == RuleAction.ALLOW]  # fmt: skip
    if rule_ids is not None:
        ids = list(rule_ids)
        if not ids:
            return {}, set()
        where.append(Rule.id.in_(ids))
    # nur die beiden Felder statt des ganzen Contracts (Consumer/Provider-Listen)
    raws = {
        rid: {"kind": kind, "reverse": back}
        for rid, kind, back in session.execute(
            select(Rule.id, Rule.raw["kind"].as_string(), Rule.raw["reverse"]).where(*where)
        )
    }
    if not raws:
        return {}, set()
    services: dict[int, list[str]] = {}
    for rid, name in session.execute(
        select(rule_service.c.rule_id, ServiceObject.canonical_name)
        .join(ServiceObject, ServiceObject.id == rule_service.c.service_object_id)
        .where(rule_service.c.rule_id.in_(list(raws)))
    ):
        services.setdefault(rid, []).append(name)
    out, only = {}, set()
    for rid, raw in raws.items():
        back = reverse_of(raw, services.get(rid, []))
        if back:
            out[rid] = back
        if replies_only(raw, services.get(rid, [])):
            only.add(rid)
    return out, only
