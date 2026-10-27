"""Was Kunden-Benutzer duerfen (ADR 0018 Schritt 3) - fail-closed per Allowlist.

Jeder Endpunkt mit Rollenpruefung (`require_role`) prueft fuer Kunden-Benutzer, ob Methode
und Pfad hier stehen; alles andere ergibt 403 - auch Endpunkte, die spaeter dazukommen.
Freigegeben sind nur Lese-Endpunkte der Doku (und die Triage eigener Findings); jeder davon
schraenkt selbst auf den Ausschnitt des Kunden ein (`api/customer_filter.py`).

Von den Quellsystemen nur die Bezeichnungen (`/systems/labels`). Nicht freigegeben: die
Konfiguration der Quellsysteme, Agenten, Laeufe, Zeitplaene, NetBox-Sync, Namensprofile,
Abgleich, Routing & Wege (Topologie der ganzen Umgebung), Inventar, publizierte Dienste,
System, Versionen des Zonenplans, Vorschauen und das Anlegen bzw. Aendern von Kunden,
Zonen, Mandanten und Applikationen.
"""

CUSTOMER_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/tenants"),
        ("GET", "/tenants/{tenant_id}/sites"),
        ("GET", "/systems/labels"),
        ("GET", "/customers"),
        ("GET", "/customers/{customer_id}/members"),
        ("GET", "/rules"),
        ("GET", "/rules/summary"),
        ("GET", "/rules/{rule_id}"),
        ("GET", "/findings"),
        ("GET", "/findings/summary"),
        ("PATCH", "/findings/{finding_id}"),
        ("GET", "/nat"),
        ("GET", "/nat/{nat_id}"),
        ("GET", "/objects"),
        ("GET", "/objects/{object_id}"),
        ("GET", "/vms"),
        ("GET", "/portgroups"),
        ("GET", "/portgroups/{pg_id}"),
        ("GET", "/appdoc/systems"),
        ("GET", "/appdoc/systems/{system}"),
        ("GET", "/applications"),
        ("GET", "/applications/{application_id}"),
        ("GET", "/applications/{application_id}/doc"),
        ("GET", "/applications/{application_id}/dependencies"),
        # 0.53.0: wer bin ich; die eigenen Zonen, Matrix und Plan (ohne fremde Kunden, ohne
        # Netze ohne Zone; Gegenseite fremder Kunden als „anderer Kunde“)
        ("GET", "/auth/me"),
        ("GET", "/zones"),
        ("GET", "/zones/{zone_id}/nets"),
        ("GET", "/zones/matrix"),
        ("GET", "/zones/matrix/rules"),
        ("GET", "/zones/plan"),
        ("GET", "/zones/plan/export"),
    }
)


def allowed(method: str, path: str) -> bool:
    return (method.upper(), path) in CUSTOMER_ROUTES
