"""Unspezifische Firewall-Regeln nur von Firewalls auf dem Weg (0.113.2).

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
"""

import ipaddress
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Rule, RuleDomain, RuleIpRange
from app.store import paths
from app.store.relevance import IMPOSSIBLE

OFF_PATH = "off_path"  # Ursache (`check(...)["cause"]`) neben denen der Relevanz
MAX_PEERS = 3  # Gegenstellen je Regel und Seite, die geprueft werden
MAX_VALUES = 2  # Adressen des Systems
MAX_TRACES = 400  # Wege je Doku; danach bleiben die Regeln ungeprueft stehen


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
            # any in der Gegenstelle: der Weg ist nicht bestimmt - nicht pruefen
            found = [] if any(r[3] for r in rows) else [
                _peer(fam, int(lo, 16), int(hi, 16)) for fam, lo, hi, _any in rows]  # fmt: skip
            self._peers[key] = list(dict.fromkeys(found))
        return self._peers[key]

    def _paths(self, src: str, dst: str) -> list[dict] | None:
        """Aktive Wege, die ihr Ziel erreichen - None, wenn keiner bekannt ist bzw. einer vor
        dem Ziel endet."""
        key = (src, dst)
        if key not in self._active:
            if len(self._active) >= MAX_TRACES:
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
        return self._active[key]

    def check(self, rule: Rule, side: str, values: list[str], device: str) -> dict | None:
        """Relevanz `impossible` mit Begruendung, wenn kein Weg zwischen den Gegenstellen der
        Regel und dem System (`values` auf `side`) ueber ihr Geraet fuehrt; sonst None."""
        from app.store import app_paths  # spaet: app_paths nutzt appdoc

        if rule.domain != RuleDomain.FIREWALL:
            return None
        peers = self.peers(rule.id, "src" if side == "dst" else "dst")[:MAX_PEERS]
        own = list(dict.fromkeys(v for v in values if v))[:MAX_VALUES]
        if not peers or not own:
            return None
        for peer in peers:
            for ip in own:
                src, dst = (peer, ip) if side == "dst" else (ip, peer)
                active = self._paths(src, dst)
                if active is None or app_paths.rule_nodes(rule, active):
                    return None  # Weg unbekannt bzw. ueber das Geraet der Regel
        a, b = (peers[0], own[0]) if side == "dst" else (own[0], peers[0])
        text = L(
            f"{device} liegt nicht auf dem Weg von {a} zu {b} – die Regel braucht dieses System "
            "nicht",
            f"{device} is not on the path from {a} to {b} – this system does not need the rule",
        )
        return {"state": IMPOSSIBLE, "text": text, "cause": OFF_PATH}
