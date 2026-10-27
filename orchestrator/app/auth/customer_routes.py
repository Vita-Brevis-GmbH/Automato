"""Was Kunden-Benutzer duerfen (ADR 0018 Schritt 3) - fail-closed per Allowlist.

Jeder Endpunkt mit Rollenpruefung (`require_role`) prueft fuer Kunden-Benutzer, ob Methode
und Pfad hier stehen; alles andere ergibt 403 - auch Endpunkte, die spaeter dazukommen.
Seit den feineren Rollen (ADR 0018 Ergaenzung) nennt jeder Eintrag die benoetigte
**Capability** (`auth/capabilities.py`); der Kunden-Benutzer braucht sie fuer mindestens einen
seiner Kunden, der Endpunkt prueft dann, dass das Objekt (Finding, Applikation, Zuordnung) zu
einem Kunden gehoert, fuer den er sie hat. Jeder Endpunkt schraenkt selbst auf den Ausschnitt
des Kunden ein (`api/customer_filter.py`).

Freigegeben sind die Lese-Endpunkte der Doku (`doc.read`), die Triage eigener Findings
(`findings.triage`), die Pflege eigener Applikationen (`applications.edit`: Stammdaten, Regeln
zuordnen bzw. ausschliessen, Regeln je Kategorie ausblenden) und die Kunden-Benutzer des eigenen
Kunden (`customer_users.manage`). Von den Quellsystemen nur die Bezeichnungen
(`/systems/labels`). Nicht freigegeben: die Konfiguration der Quellsysteme, Agenten, Laeufe,
Zeitplaene, NetBox-Sync, Namensprofile, Abgleich, Routing & Wege (Topologie der ganzen
Umgebung), Inventar, publizierte Dienste, System, Versionen des Zonenplans, Vorschauen, das
Anlegen bzw. Loeschen von Applikationen und ihrer Mitglieder (AP, EPG, Server) und das Anlegen
bzw. Aendern von Kunden, Zonen und Mandanten.
"""

from app.auth.capabilities import APPS_EDIT, READ, TRIAGE, USERS_MANAGE

CUSTOMER_ROUTE_CAPABILITIES: dict[tuple[str, str], str] = {
    ("GET", "/tenants"): READ,
    ("GET", "/tenants/{tenant_id}/sites"): READ,
    ("GET", "/systems/labels"): READ,
    ("GET", "/customers"): READ,
    ("GET", "/customers/{customer_id}/members"): READ,
    ("GET", "/rules"): READ,
    ("GET", "/rules/summary"): READ,
    ("GET", "/rules/{rule_id}"): READ,
    ("GET", "/findings"): READ,
    ("GET", "/findings/summary"): READ,
    ("PATCH", "/findings/{finding_id}"): TRIAGE,
    ("GET", "/nat"): READ,
    ("GET", "/nat/{nat_id}"): READ,
    ("GET", "/objects"): READ,
    ("GET", "/objects/{object_id}"): READ,
    ("GET", "/vms"): READ,
    ("GET", "/portgroups"): READ,
    ("GET", "/portgroups/{pg_id}"): READ,
    ("GET", "/appdoc/systems"): READ,
    ("GET", "/appdoc/systems/{system}"): READ,
    ("GET", "/applications"): READ,
    ("GET", "/applications/{application_id}"): READ,
    ("GET", "/applications/{application_id}/doc"): READ,
    ("GET", "/applications/{application_id}/dependencies"): READ,  # 0.88.0
    # 0.53.0: wer bin ich; die eigenen Zonen, Matrix und Plan (ohne fremde Kunden, ohne
    # Netze ohne Zone; Gegenseite fremder Kunden als „anderer Kunde“)
    ("GET", "/auth/me"): READ,
    ("GET", "/zones"): READ,
    ("GET", "/zones/{zone_id}/nets"): READ,
    ("GET", "/zones/matrix"): READ,
    ("GET", "/zones/matrix/rules"): READ,
    ("GET", "/zones/plan"): READ,
    ("GET", "/zones/plan/export"): READ,
    # Feinere Rollen: eigene Applikationen pflegen (nur ganz im Ausschnitt des Kunden) -
    # Stammdaten, Regeln zuordnen/ausschliessen (und rueckgaengig), je Kategorie ausblenden
    ("PATCH", "/applications/{application_id}"): APPS_EDIT,
    ("POST", "/applications/{application_id}/rules"): APPS_EDIT,
    ("DELETE", "/applications/{application_id}/members/{member_id}"): APPS_EDIT,  # nur Regeln
    ("POST", "/applications/{application_id}/hidden"): APPS_EDIT,
    ("DELETE", "/applications/{application_id}/hidden/{hide_id}"): APPS_EDIT,
    # Kunden-Benutzer des eigenen Kunden (Kunden-Admin)
    ("GET", "/customer-users"): USERS_MANAGE,
    ("POST", "/customer-users"): USERS_MANAGE,
    ("PATCH", "/customer-users/{assignment_id}"): USERS_MANAGE,
    ("DELETE", "/customer-users/{assignment_id}"): USERS_MANAGE,
}

CUSTOMER_ROUTES: frozenset[tuple[str, str]] = frozenset(CUSTOMER_ROUTE_CAPABILITIES)


def capability(method: str, path: str) -> str | None:
    """Benoetigte Capability fuer Methode und Pfad; None = nicht freigegeben."""
    return CUSTOMER_ROUTE_CAPABILITIES.get((method.upper(), path))


def allowed(method: str, path: str) -> bool:
    return capability(method, path) is not None
