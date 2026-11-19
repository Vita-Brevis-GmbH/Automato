"""Firewall-Regeln nur von Firewalls auf dem Weg (0.113.2; seit ADR 0071 fuer jede Regel).

ADR 0071 (Regel-Zuordnung nach echtem Treffer, Anwender 2026-11-18): der Weg ist eine harte
Bedingung - auch fuer Regeln, die das System nennen (bis 1.21 nur fuer unspezifische bzw. im
Regelwerk 2). Ist der Weg nicht bekannt, bleibt die Regel, **markiert** (`unknown`, „Weg nicht
geprueft“) und gezaehlt - nie still. Gegenstelle `any` bzw. negiert: der Weg ist nicht bestimmt,
es gilt das Routing der Firewall (`relevance`). Regeln, die das System ueber eine VIP bzw. einen
Load Balancer erfassen, stehen auf der Firewall, die umschreibt - sie liegt auf dem Weg.


Rueckmeldung des Anwenders zu 0.113: in der Doku nach Kategorie und oben standen unspezifische
Firewall-Regeln je Firewall, obwohl der Weg zwischen Gegenstelle und System nicht ueber jede
dieser Firewalls fuehrt („nur wenn es im Pfad waere, ist es notwendig, dass es angezeigt wird“).
Die Relevanz (`store/relevance.py`) prueft nur das Routing der Firewall selbst (Interface der
Regel zur IP des Systems) - eine Firewall abseits des Wegs mit passender Route bzw. Interface
`any` blieb stehen.

Hier zaehlt der Weg wie bei den Verbindungen (`app_paths.connection_trace`): von der Gegenstelle
der Regel (Host bzw. Netz, nicht any) zum System bzw. vom System zur Gegenstelle. Liegt das Geraet
der Regel (FortiGate mit VDOM) auf keinem aktiven Weg, der sein Ziel erreicht, braucht dieses
System die Regel nicht - sie gilt als routingtechnisch nicht moeglich, mit Begruendung. Ist ein
Weg nicht bekannt bzw. endet er vor dem Ziel, bleibt die Regel (keine Vermutung). Nur lesend.

Nachtrag „Gegenpruefung“ (ADR 0071, P-4, Stichprobe S2 des Anwenders): endet ein Weg vor dem
Ziel (z. B. an einem nicht gelesenen Uebergang), zaehlt sein **belegter Teil**:

- eine Firewall auf dem belegten Teil liegt auf dem Weg - ohne Kennzeichen;
- eine Firewall abseits davon kann nur hinter dem Ende liegen. Dazu muss sie das Ziel des Wegs
  selbst kennen: angeschlossen bzw. ueber eine eigene Route - fuer ein privates Ziel nicht nur
  ueber die Default-Route (dorthin ginge es ins Internet). Dann bleibt die Regel, markiert „Weg
  nicht geprueft“; sonst liegt sie nicht auf dem Weg (gezaehlt, nie still). Ist das Routing der
  Firewall nicht gelesen, bleibt die Regel markiert.
"""

import ipaddress
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Rule, RuleDomain, RuleIpRange, TopoRoute
from app.store import paths
from app.store.relevance import IMPOSSIBLE
from app.store.topology import endpoint, is_net, is_private

OFF_PATH = "off_path"  # Ursache (`check(...)["cause"]`) neben denen der Relevanz
# 0.140.0 (Anwender: „nicht zuverlaessig“): angehoben, seit die Wege je Mandant gemerkt werden
# (`path_store`); bleibt eine Regel deshalb ungeprueft stehen, zaehlt die Doku sie
# (`summary.path_unchecked`) - bis 0.139 3 Gegenstellen, 2 Adressen, 400 Wege, still
MAX_PEERS = 64  # Gegenstellen je Regel und Seite, die geprueft werden
MAX_VALUES = 8  # Adressen des Systems
MAX_TRACES = 20000  # Wege je Doku; danach bleiben die Regeln ungeprueft stehen
MAX_UNKNOWN = 4  # Paare ohne bekannten Weg je Regel, danach „Weg nicht geprueft“


def _peer(fam: int, lo: int, hi: int) -> str:
    """Host bzw. Netz (als Ganzes, wie die Verbindungen); ein Bereich, der kein Netz ist, mit
    seiner ersten Adresse."""
    cls = ipaddress.IPv4Address if fam == 4 else ipaddress.IPv6Address
    first, last = cls(lo), cls(hi)
    nets = list(ipaddress.summarize_address_range(first, last))
    if len(nets) == 1 and nets[0].num_addresses > 1:
        return str(nets[0])
    return str(first)


class OnPath:
    """Liegt das Geraet einer Regel auf dem Weg zwischen Gegenstelle und System? Wege je Paar
    (Quelle, Ziel) einmal - dieselbe Regel auf mehreren Firewalls teilt sie."""

    def __init__(self, session: Session, tenant_id: int, graph: Callable[[], paths.Graph]):
        self.session = session
        self.tenant_id = tenant_id
        self._graph = graph
        self._checks = None
        self._peers: dict[tuple[int, str], list[str]] = {}
        self._active: dict[tuple[str, str], list[dict] | None] = {}
        # Wege, die vor dem Ziel enden (Nachtrag P-4): ihr belegter Teil zaehlt
        self._partial: dict[tuple[str, str], list[dict]] = {}
        self._beyond: dict[tuple, bool | None] = {}
        self.unchecked: set[int] = set()  # Regeln, die eine Grenze ungeprueft stehen liess
        # (Regel, Seite) ohne bekannten Weg (ADR 0071): die Regel bleibt, markiert und gezaehlt
        self.unknown: set[tuple[int, str]] = set()
        self._cut = False
        self.matcher = None  # `rule_match.Matcher` (negierte Gegenstelle = nicht bestimmt)

    def peers(self, rule_id: int, role: str) -> list[str]:
        """Gegenstellen der Regel auf `role` (Hosts bzw. Netze, ohne any)."""
        key = (rule_id, role)
        if key not in self._peers:
            stmt = (
                select(RuleIpRange.family, RuleIpRange.lo, RuleIpRange.hi, RuleIpRange.is_any)
                .where(RuleIpRange.rule_id == rule_id, RuleIpRange.role == role)
                .order_by(RuleIpRange.id)
            )
            rows = list(self.session.execute(stmt))
            # any in der Gegenstelle bzw. negiert (ihr Komplement, ADR 0071): der Weg ist nicht
            # bestimmt - nicht pruefen
            negated = self.matcher is not None and self.matcher.negated(rule_id, role)
            found = [] if negated or any(r[3] for r in rows) else [
                _peer(fam, int(lo, 16), int(hi, 16)) for fam, lo, hi, _any in rows]  # fmt: skip
            self._peers[key] = list(dict.fromkeys(found))
        return self._peers[key]

    def _paths(self, src: str, dst: str) -> list[dict] | None:
        """Aktive Wege, die ihr Ziel erreichen - None, wenn keiner bekannt ist bzw. einer vor
        dem Ziel endet."""
        key = (src, dst)
        if key not in self._active:
            if len(self._active) >= MAX_TRACES:
                self._cut = True
                return None
            from app.store import app_paths, policy_check  # spaet: app_paths nutzt appdoc

            if self._checks is None:
                self._checks = policy_check.Checks.load(self.session, self.tenant_id)
            res, _start = app_paths.connection_trace(
                self.session, self.tenant_id, self._graph(), self._checks, src, dst, None, []
            )
            active = [p for p in res["paths"] if p.get("kind") != "backup"]
            reached = bool(active) and all(p["end"]["kind"] in paths.REACHED for p in active)
            self._active[key] = active if reached else None
            if active and not reached:
                self._partial[key] = active
        return self._active[key]

    def _has_routes(self, node: paths.Key) -> bool:
        """Ist die Routing-Tabelle des Knotens gelesen (mindestens eine Route)?"""
        key = ("routes", node)
        if key not in self._beyond:
            self._beyond[key] = self.session.scalars(
                select(TopoRoute.id).where(
                    TopoRoute.source_system_id == node[0], TopoRoute.context == node[1],
                    TopoRoute.table == "rib").limit(1)
            ).first() is not None  # fmt: skip
        return bool(self._beyond[key])

    def _may_be_beyond(self, rule: Rule, dst: str) -> bool | None:
        """Kann die Firewall der Regel hinter dem Ende eines Wegs zu `dst` liegen? Nur, wenn sie
        das Ziel selbst kennt: angeschlossen bzw. eigene Route - fuer ein privates Ziel nicht
        nur die Default-Route. None = ihr Routing ist nicht gelesen (keine Aussage)."""
        key = (rule.source_system_id, rule.vdom, dst)
        if key not in self._beyond:
            g = self._graph()
            nodes = [k for k in g.nodes if k[0] == rule.source_system_id
                     and (not rule.vdom or k[1] == rule.vdom)]  # fmt: skip
            target = endpoint(dst)
            found: bool | None = None
            for node in nodes:
                rows = g._routes(target, node)
                if any(r.prefix_len > 0 for r in rows) or (rows and not is_private(target)):
                    found = True  # angeschlossen bzw. eigene Route (oeffentlich: auch Default)
                    break
                if is_net(target) and g._inner(node, target, 0, []):
                    found = True  # genauere Routen fuer Teile des Netzes
                    break
                if rows or self._has_routes(node):
                    found = False
            self._beyond[key] = found
        return self._beyond[key]

    def check(self, rule: Rule, side: str, values: list[str], device: str) -> dict | None:
        """Relevanz `impossible` mit Begruendung, wenn kein Weg zwischen den Gegenstellen der
        Regel und dem System (`values` auf `side`) ueber ihr Geraet fuehrt; sonst None."""
        from app.store import app_paths  # spaet: app_paths nutzt appdoc

        if rule.domain != RuleDomain.FIREWALL:
            return None
        peers = self.peers(rule.id, "src" if side == "dst" else "dst")
        own = list(dict.fromkeys(v for v in values if v))
        if not peers or not own:
            return None
        if len(peers) > MAX_PEERS or len(own) > MAX_VALUES:
            self.unchecked.add(rule.id)  # nur ein Teil pruefbar: die Regel bleibt, gezaehlt
            return None
        unknown = 0
        ended: tuple[str, str, str] | None = None  # Paar mit Ende vor dem Ziel, Firewall abseits
        for peer in peers:
            for ip in own:
                src, dst = (peer, ip) if side == "dst" else (ip, peer)
                self._cut = False
                active = self._paths(src, dst)
                if self._cut:
                    self.unchecked.add(rule.id)
                partial = self._partial.get((src, dst)) if active is None else None
                if partial:
                    # Nachtrag P-4: der Weg endet vor dem Ziel - sein belegter Teil zaehlt
                    if app_paths.rule_nodes(rule, partial):
                        return None  # auf dem belegten Teil: auf dem Weg, ohne Kennzeichen
                    if self._may_be_beyond(rule, dst) is False:
                        # abseits und ohne eigene Route zum Ziel: auch nicht hinter dem Ende
                        ended = ended or (src, dst, partial[0]["end"].get("text") or "")
                        continue
                if active is None:  # Weg unbekannt: weiter, ob ein anderes Paar ihn zeigt
                    unknown += 1
                    if unknown >= MAX_UNKNOWN:
                        self.unknown.add((rule.id, side))
                        return None
                    continue
                if app_paths.rule_nodes(rule, active):
                    return None  # ueber das Geraet der Regel
        if unknown:  # kein Paar zeigt die Firewall, aber nicht jeder Weg ist bekannt
            self.unknown.add((rule.id, side))
            return None
        if ended is not None:
            a, b, end = ended
            text = L(
                f"{device} liegt nicht auf dem belegten Teil des Wegs von {a} zu {b} (endet vor "
                f"dem Ziel) und hat keine eigene Route zu {b} – die Regel braucht dieses System "
                "nicht",
                f"{device} is not on the proven part of the path from {a} to {b} (ends before "
                f"the destination) and has no route of its own to {b} – this system does not "
                "need the rule",
            )
            return {"state": IMPOSSIBLE, "text": text, "cause": OFF_PATH}
        a, b = (peers[0], own[0]) if side == "dst" else (own[0], peers[0])
        text = L(
            f"{device} liegt nicht auf dem Weg von {a} zu {b} – die Regel braucht dieses System "
            "nicht",
            f"{device} is not on the path from {a} to {b} – this system does not need the rule",
        )
        return {"state": IMPOSSIBLE, "text": text, "cause": OFF_PATH}
