"""Rollenmodell (ADR 0007): klein starten, ausbaubar.

Hierarchie admin > operator > viewer (eine hoehere Rolle schliesst die niedrigeren
ein). Weitere Rollen/Rechte kommen additiv dazu (dynamische Rechte-Matrix spaeter).
"""

VIEWER = "viewer"
OPERATOR = "operator"
ADMIN = "admin"

_RANK = {VIEWER: 0, OPERATOR: 1, ADMIN: 2}


def satisfies(roles: frozenset[str], required: str) -> bool:
    if ADMIN in roles:
        return True
    have = max((_RANK.get(r, -1) for r in roles), default=-1)
    return have >= _RANK[required]
