"""Prüfung: IPsec-Tunnel der FortiGates (1.19.0, ADR 0063, `store/vpn.problems`).

Steckbare Prüfung (ADR 0039): Tunnel bzw. eine Phase 2 down (nicht Dial-up - dort kommen und
gehen die Gegenstellen); ein Phase-2-Selektor deckt eine Route über den Tunnel nur teilweise;
Route über den Tunnel ohne passenden Selektor. Ist der Zustand bzw. ein Selektor nicht lesbar
bzw. nicht auflösbar, kein Befund (unbekannt statt falsch)."""

from app.checks import Check
from app.models import FindingType, Severity, SubjectKind

_TYPE = {
    "down": (FindingType.VPN_TUNNEL_DOWN, Severity.WARNING),
    "partial": (FindingType.VPN_SELECTOR_PARTIAL, Severity.WARNING),
    "none": (FindingType.VPN_ROUTE_NO_SELECTOR, Severity.WARNING),
}


def _run(session, tenant_id, cx):
    from app.store import vpn
    from app.store.reconcile import Issue

    out = []
    for p in vpn.problems(session, tenant_id):
        ftype, severity = _TYPE[p["problem"]]
        vdom = f" · VDOM {p['vdom']}" if p.get("vdom") not in (None, "root") else ""
        subject = f"{p.get('source') or p['node']}{vdom} · VPN {p['tunnel']}"
        out.append(Issue(ftype, SubjectKind.NODE, subject, p["source_system_id"], severity,
                         detail=p))  # fmt: skip
    return out


CHECK = Check(
    "vpn",
    "analyse",
    ("VPN-Tunnel (IPsec)", "VPN tunnels (IPsec)"),
    (
        "Tunnel bzw. Phase 2 down, Phase-2-Selektor deckt eine Route über den Tunnel nicht, "
        "Route über den Tunnel ohne passenden Selektor",
        "Tunnel or phase 2 down, phase 2 selector does not cover a route via the tunnel, route "
        "via the tunnel without a matching selector",
    ),
    frozenset(_TYPE[k][0] for k in _TYPE),
    _run,
    order=65,
)
