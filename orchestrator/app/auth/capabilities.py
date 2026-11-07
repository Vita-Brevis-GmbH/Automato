"""Feinere Rollen der Kunden-Benutzer (ADR 0018, Ergaenzung „Feinere Rollen“).

Kunden-Benutzer werden nicht ueber Rollennamen geprueft, sondern ueber **Capabilities**: jede
Kunden-Rolle loest sich ueber `ROLE_CAPABILITIES` in eine Menge von Rechten auf, die
Freigabeliste (`auth/customer_routes.py`) nennt je Methode und Pfad das benoetigte Recht. Die
Rolle gilt **je Kunde** (`Principal.customer_roles`): wer fuer Kunde A Editor und fuer Kunde B
nur Viewer ist, pflegt nur die Applikationen von A.

Rollen (aufsteigend, jede schliesst die vorherige ein):

- `viewer` - die Doku des eigenen Kunden lesen (wie bis 0.87.0),
- `triage` - zusaetzlich eigene Findings quittieren und kommentieren,
- `editor` - zusaetzlich eigene Applikationen pflegen: Stammdaten, Regeln zuordnen bzw.
  ausschliessen, Regeln je Kategorie ausblenden (mit Begruendung) - nur Applikationen, die
  ganz zum Kunden gehoeren,
- `customer_admin` (Kunden-Admin) - zusaetzlich die Kunden-Benutzer des eigenen Kunden zuordnen
  und entfernen (nie das eigene Konto, nie ueber die eigene Rolle hinaus, nie andere Kunden).

Der Kunden-Admin heisst bewusst nicht `admin`: der Wert bleibt dem globalen Admin vorbehalten
und wird fuer Kunden-Benutzer weiter abgelehnt (Zuordnung: 422; Claim: unbekannt = viewer).

Abbildung der bisherigen Werte (0.53.0): `viewer` bleibt `viewer`, `operator` (lesen + eigene
Findings triagieren) entspricht `triage` - kein bestehendes Recht wird erweitert. Unbekannte
Rollen gelten als `viewer` (fail-closed). Kunden-Benutzer sind nie globaler Admin bzw.
Operator: die Capabilities gelten nur fuer die freigegebenen Endpunkte und nur im Ausschnitt
ihres Kunden.

Muster wie in Magister (Rolle -> Capabilities), eigener Code.
"""

from collections.abc import Iterable

# Capabilities (Rechte) der Kunden-Benutzer
READ = "doc.read"  # Doku des eigenen Kunden lesen
TRIAGE = "findings.triage"  # eigene Findings quittieren bzw. kommentieren
APPS_EDIT = "applications.edit"  # eigene Applikationen pflegen
USERS_MANAGE = "customer_users.manage"  # Kunden-Benutzer des eigenen Kunden zuordnen

CAPABILITIES = (READ, TRIAGE, APPS_EDIT, USERS_MANAGE)

VIEWER = "viewer"
TRIAGE_ROLE = "triage"
EDITOR = "editor"
CUSTOMER_ADMIN = "customer_admin"  # nie `admin` (globaler Admin)

# Kunden-Rollen aufsteigend
CUSTOMER_ROLES: tuple[str, ...] = (VIEWER, TRIAGE_ROLE, EDITOR, CUSTOMER_ADMIN)
# frueher gespeicherte Werte (0.53.0) -> Rolle; bleiben gueltig und werden so angezeigt
LEGACY_ROLES: dict[str, str] = {"operator": TRIAGE_ROLE}

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    VIEWER: frozenset({READ}),
    TRIAGE_ROLE: frozenset({READ, TRIAGE}),
    EDITOR: frozenset({READ, TRIAGE, APPS_EDIT}),
    CUSTOMER_ADMIN: frozenset({READ, TRIAGE, APPS_EDIT, USERS_MANAGE}),
}

# Nur zur Anzeige (`GET /auth/me`): was Betreuer mit ihren Rollen entsprechend duerfen - sie
# werden weiter ueber die Rollen geprueft (`require_role`), nicht ueber diese Tabelle
STAFF_CAPABILITIES: dict[str, frozenset[str]] = {
    "viewer": frozenset({READ}),
    "operator": frozenset({READ, TRIAGE, APPS_EDIT}),
    "admin": frozenset(CAPABILITIES),
}

_RANK = {role: i for i, role in enumerate(CUSTOMER_ROLES)}


def normalize(role: object) -> str:
    """Gespeicherte bzw. gemeldete Rolle -> Kunden-Rolle; Unbekanntes = viewer (fail-closed)."""
    value = str(role or "").strip().lower()
    value = LEGACY_ROLES.get(value, value)
    return value if value in _RANK else VIEWER


def rank(role: str | None) -> int:
    """Rang einer (normalisierten) Rolle; ohne Rolle -1."""
    return -1 if role is None else _RANK[normalize(role)]


def higher(a: str | None, b: str | None) -> str | None:
    """Die hoehere zweier Rollen (Vereinigung Claim und Zuordnung)."""
    if a is None or b is None:
        return None if a is None and b is None else normalize(b if a is None else a)
    a, b = normalize(a), normalize(b)
    return a if rank(a) >= rank(b) else b


def highest(roles: Iterable[str | None]) -> str | None:
    out: str | None = None
    for role in roles:
        out = higher(out, role)
    return out


def capabilities(role: str | None) -> frozenset[str]:
    return frozenset() if role is None else ROLE_CAPABILITIES[normalize(role)]


def staff_capabilities(roles: Iterable[str]) -> frozenset[str]:
    out: set[str] = set()
    for role in roles:
        out |= STAFF_CAPABILITIES.get(role, frozenset())
    return frozenset(out)
