"""Was Mandanten-Benutzer duerfen (ADR 0068) - fail-closed per Positivliste.

Mandanten-Benutzer sind Benutzer eines Kunden-Realms mit der Rolle `customer_operator` bzw.
`customer_admin` im **eigenen Mandanten** (den Automato fuer ihren Portal-Kunden angelegt hat,
`cockpit_tenant`): Betreuer-Sicht ohne Kunden-Filter, begrenzt auf genau diesen Mandanten
(`Principal.tenant_ids`), nie Admin. Jeder Endpunkt mit Rollenpruefung (`require_role`) prueft fuer
sie, ob Methode und Pfad hier stehen, und verlangt das genannte Recht
(`auth/capabilities.TENANT_ROLE_CAPABILITIES`):

- `mandant.read` - alles des Mandanten lesen: Doku, Schichten L1-L7, Weg-Suche, Wege vergleichen,
  Abgleich, Findings, Laeufe, Quellsysteme (ohne Zugangsdaten), Zeitplan, Standorte;
- `mandant.collect` - Einlesen anstossen (Laeufe starten, Regeln einer Applikation einlesen);
- `mandant.manage` - im Mandanten anpassen, was ein Betreuer fuer ihn tut (Applikationen,
  Findings, Pruefungen, Module, Zonen, Wege, Quellsysteme mit Grenze, Standorte, NetBox-Sync ...).

Alles andere ergibt 403 - auch Endpunkte, die spaeter dazukommen. Die Plattform-Endpunkte stehen
ausdruecklich in `PLATFORM_ROUTES` (nie fuer Benutzer eines Kunden-Realms: System, Kunden-Portal,
Kopplung, Mandanten anlegen/aendern/loeschen, Benutzer & Rollen, Kunden in Automato, Namensprofile,
Collector-Agenten verwalten); `tests/test_tenant_roles.py` verlangt, dass jeder Endpunkt mit
Rollenpruefung in genau einer der beiden Listen steht. Den Mandanten prueft jeder Endpunkt selbst
(`tenant_scope`, wie fuer Betreuer mit `tenant_ids`, ADR 0007 D3).
"""

from app.auth.capabilities import TENANT_COLLECT, TENANT_MANAGE, TENANT_READ

TENANT_ROUTE_CAPABILITIES: dict[tuple[str, str], str] = {
    # agents
    ("GET", "/agents"): TENANT_MANAGE,
    # applications
    ("GET", "/applications"): TENANT_READ,
    ("POST", "/applications"): TENANT_MANAGE,
    ("GET", "/applications/candidates"): TENANT_READ,
    ("POST", "/applications/delete"): TENANT_MANAGE,
    ("GET", "/applications/doc-states"): TENANT_READ,
    # Export der Doku im Hintergrund (1.0.1, seit 1.21.0 als Auftrag): eigene Auftraege
    ("POST", "/applications/export"): TENANT_READ,
    ("GET", "/applications/exports"): TENANT_READ,
    ("DELETE", "/applications/exports/{export_id}"): TENANT_READ,
    ("GET", "/applications/exports/{export_id}"): TENANT_READ,
    ("POST", "/applications/exports/{export_id}/cancel"): TENANT_READ,
    ("GET", "/applications/exports/{export_id}/download"): TENANT_READ,
    ("POST", "/applications/import"): TENANT_MANAGE,
    ("GET", "/applications/import/template"): TENANT_MANAGE,
    ("GET", "/applications/suggestions"): TENANT_READ,
    ("POST", "/applications/suggestions/{code}/accept"): TENANT_MANAGE,
    ("DELETE", "/applications/{application_id}"): TENANT_MANAGE,
    ("GET", "/applications/{application_id}"): TENANT_READ,
    ("PATCH", "/applications/{application_id}"): TENANT_MANAGE,
    ("GET", "/applications/{application_id}/connections"): TENANT_READ,
    ("GET", "/applications/{application_id}/connections/plan/export"): TENANT_READ,
    ("POST", "/applications/{application_id}/connections/plan/export"): TENANT_READ,
    ("GET", "/applications/{application_id}/dependencies"): TENANT_READ,
    ("GET", "/applications/{application_id}/doc"): TENANT_READ,
    ("GET", "/applications/{application_id}/doc/state"): TENANT_READ,
    ("GET", "/applications/{application_id}/epgs/{epg_id}"): TENANT_READ,
    ("GET", "/applications/{application_id}/export"): TENANT_READ,
    ("POST", "/applications/{application_id}/hidden"): TENANT_MANAGE,
    ("DELETE", "/applications/{application_id}/hidden/{hide_id}"): TENANT_MANAGE,
    ("POST", "/applications/{application_id}/members"): TENANT_MANAGE,
    ("DELETE", "/applications/{application_id}/members/{member_id}"): TENANT_MANAGE,
    ("POST", "/applications/{application_id}/read-rules"): TENANT_COLLECT,
    ("GET", "/applications/{application_id}/rule-sources"): TENANT_COLLECT,
    ("POST", "/applications/{application_id}/rules"): TENANT_MANAGE,
    ("GET", "/applications/{application_id}/ruleset-compare"): TENANT_READ,
    # as_numbers
    ("GET", "/routing/as-numbers"): TENANT_READ,
    # bgp
    ("GET", "/routing/bgp/config"): TENANT_READ,
    ("GET", "/routing/bgp/filtered"): TENANT_READ,
    ("GET", "/routing/bgp/l3outs/{l3out_id}"): TENANT_READ,
    ("GET", "/routing/bgp/l3outs/{l3out_id}/zones"): TENANT_READ,
    ("GET", "/routing/bgp/peers/{peer_id}"): TENANT_READ,
    ("GET", "/routing/bgp/peers/{peer_id}/zones"): TENANT_READ,
    # cable_labels
    ("GET", "/cabling/labels"): TENANT_READ,
    ("GET", "/cabling/labels/choices"): TENANT_READ,
    ("PUT", "/cabling/labels"): TENANT_MANAGE,
    ("DELETE", "/cabling/labels/{label_id}"): TENANT_MANAGE,
    ("GET", "/cabling/transceivers"): TENANT_READ,
    # cabling
    ("GET", "/cabling/devices"): TENANT_READ,
    ("GET", "/cabling/endpoints"): TENANT_READ,
    ("GET", "/cabling/failure"): TENANT_READ,
    ("GET", "/cabling/links"): TENANT_READ,
    ("GET", "/cabling/locate"): TENANT_READ,
    ("GET", "/cabling/patches"): TENANT_READ,
    ("GET", "/cabling/plan"): TENANT_READ,
    ("GET", "/cabling/plan/export"): TENANT_READ,
    ("GET", "/cabling/ports"): TENANT_READ,
    ("GET", "/cabling/sources"): TENANT_READ,
    ("GET", "/cabling/vlans"): TENANT_READ,
    # checks
    ("GET", "/checks"): TENANT_READ,
    ("PUT", "/checks/{key}"): TENANT_MANAGE,
    # customer_users
    ("GET", "/auth/me"): TENANT_READ,
    # customers
    ("GET", "/customers"): TENANT_READ,
    ("POST", "/customers/preview"): TENANT_READ,
    ("GET", "/customers/suggestions"): TENANT_READ,
    ("GET", "/customers/unassigned"): TENANT_READ,
    ("GET", "/customers/{customer_id}/members"): TENANT_READ,
    # data_quality
    ("GET", "/data-quality"): TENANT_READ,
    # findings
    ("GET", "/findings"): TENANT_READ,
    ("POST", "/findings"): TENANT_MANAGE,
    ("GET", "/findings/export"): TENANT_READ,  # Excel nach Kategorie (1.0.1)
    ("GET", "/findings/summary"): TENANT_READ,
    ("POST", "/findings/triage"): TENANT_MANAGE,
    ("PATCH", "/findings/{finding_id}"): TENANT_MANAGE,
    # jobs
    ("GET", "/jobs"): TENANT_READ,
    ("POST", "/jobs/collect"): TENANT_COLLECT,
    ("POST", "/jobs/collect-all"): TENANT_COLLECT,
    ("GET", "/jobs/daily"): TENANT_READ,
    ("GET", "/jobs/{run_id}"): TENANT_READ,
    ("POST", "/jobs/{run_id}/cancel"): TENANT_MANAGE,
    # layer_plans - Plaene je Schicht L1/L2/L3 (1.23.0, ADR 0067 Nachtrag)
    ("GET", "/network-plans/{layer}"): TENANT_READ,
    ("GET", "/network-plans/{layer}/export"): TENANT_READ,
    # modules
    ("GET", "/modules"): TENANT_READ,
    ("PUT", "/modules/{key}"): TENANT_MANAGE,
    # naming_profiles
    ("GET", "/naming-profiles/appdoc-catalog/default"): TENANT_READ,
    ("GET", "/naming-profiles/zone-schema/template"): TENANT_READ,
    # nat
    ("GET", "/nat"): TENANT_READ,
    ("GET", "/nat/{nat_id}"): TENANT_READ,
    # net_assign
    ("GET", "/routing/net-assignments"): TENANT_READ,
    ("PUT", "/routing/net-assignments"): TENANT_MANAGE,
    ("DELETE", "/routing/net-assignments/{assignment_id}"): TENANT_MANAGE,
    # netbox_sync
    ("GET", "/netbox-sync/plans"): TENANT_MANAGE,
    ("POST", "/netbox-sync/plans"): TENANT_MANAGE,
    ("GET", "/netbox-sync/plans/{plan_id}"): TENANT_MANAGE,
    ("POST", "/netbox-sync/plans/{plan_id}/apply"): TENANT_MANAGE,
    ("POST", "/netbox-sync/plans/{plan_id}/cancel"): TENANT_MANAGE,
    ("GET", "/netbox-sync/plans/{plan_id}/items"): TENANT_MANAGE,
    ("GET", "/netbox-sync/targets"): TENANT_MANAGE,
    # objects
    ("GET", "/inventory/{kind}"): TENANT_READ,
    ("GET", "/objects"): TENANT_READ,
    ("GET", "/objects/summary"): TENANT_READ,
    ("GET", "/objects/{object_id}"): TENANT_READ,
    # path_compare
    ("GET", "/path-compare/sections"): TENANT_READ,
    ("GET", "/path-compare/trace"): TENANT_READ,
    # reconcile
    ("POST", "/reconcile"): TENANT_MANAGE,
    ("GET", "/reconcile/ipam"): TENANT_READ,
    ("GET", "/reconcile/summary"): TENANT_READ,
    # reservations
    ("GET", "/reservations"): TENANT_READ,
    ("POST", "/reservations"): TENANT_MANAGE,
    ("GET", "/reservations/options"): TENANT_READ,
    ("DELETE", "/reservations/{reservation_id}"): TENANT_MANAGE,
    ("PUT", "/reservations/{reservation_id}"): TENANT_MANAGE,
    # routing
    ("GET", "/routing/changes"): TENANT_READ,
    ("GET", "/routing/changes/summary"): TENANT_READ,
    ("GET", "/routing/exits"): TENANT_READ,
    ("POST", "/routing/exits"): TENANT_MANAGE,
    ("POST", "/routing/exits/dismiss"): TENANT_MANAGE,
    ("POST", "/routing/exits/dismiss/undo"): TENANT_MANAGE,
    ("GET", "/routing/exits/learned"): TENANT_READ,
    ("POST", "/routing/exits/prefixes"): TENANT_READ,
    ("GET", "/routing/exits/suggestions"): TENANT_READ,
    ("DELETE", "/routing/exits/{exit_id}"): TENANT_MANAGE,
    ("PUT", "/routing/exits/{exit_id}"): TENANT_MANAGE,
    ("GET", "/routing/explain"): TENANT_READ,
    ("GET", "/routing/ha"): TENANT_READ,
    ("GET", "/routing/l3outs"): TENANT_READ,
    ("DELETE", "/routing/l3outs/firewall"): TENANT_MANAGE,
    ("PUT", "/routing/l3outs/firewall"): TENANT_MANAGE,
    ("GET", "/routing/l3outs/firewall-choices"): TENANT_READ,
    ("GET", "/routing/lookup"): TENANT_READ,
    ("GET", "/routing/peers"): TENANT_READ,
    ("GET", "/routing/policy-routes"): TENANT_READ,
    ("GET", "/routing/resolve"): TENANT_READ,
    ("GET", "/routing/routes"): TENANT_READ,
    ("GET", "/routing/summary"): TENANT_READ,
    ("GET", "/routing/trace"): TENANT_READ,
    # rules
    ("GET", "/rules"): TENANT_READ,
    ("DELETE", "/rules/pair-decisions/{decision_id}"): TENANT_MANAGE,
    ("GET", "/rules/summary"): TENANT_READ,
    ("GET", "/rules/{rule_id}"): TENANT_READ,
    ("POST", "/rules/{rule_id}/pair"): TENANT_MANAGE,
    ("GET", "/rules/{rule_id}/path-search"): TENANT_READ,
    # static_routes
    ("GET", "/static-routes"): TENANT_READ,
    # stp
    ("GET", "/stp/overview"): TENANT_READ,
    ("PUT", "/stp/settings"): TENANT_MANAGE,
    ("GET", "/stp/targets"): TENANT_READ,
    ("PUT", "/stp/targets"): TENANT_MANAGE,
    ("DELETE", "/stp/targets/{target_id}"): TENANT_MANAGE,
    ("GET", "/stp/tree"): TENANT_READ,
    ("GET", "/stp/trees"): TENANT_READ,
    # systems
    ("GET", "/systems"): TENANT_READ,
    ("POST", "/systems"): TENANT_MANAGE,
    ("GET", "/systems/labels"): TENANT_READ,
    ("GET", "/systems/parallel"): TENANT_READ,
    ("POST", "/systems/parallel-group"): TENANT_MANAGE,
    ("POST", "/systems/parallel-reject"): TENANT_MANAGE,
    ("POST", "/systems/parallel-reject/undo"): TENANT_MANAGE,
    ("GET", "/systems/parallel-suggestions"): TENANT_READ,
    ("DELETE", "/systems/{system_id}"): TENANT_MANAGE,
    ("PATCH", "/systems/{system_id}"): TENANT_MANAGE,
    ("POST", "/systems/{system_id}/check"): TENANT_MANAGE,
    ("GET", "/systems/{system_id}/check/{check_id}"): TENANT_MANAGE,
    # tenants
    ("GET", "/tenants"): TENANT_READ,
    ("GET", "/tenants/{tenant_id}/sites"): TENANT_READ,
    ("POST", "/tenants/{tenant_id}/sites"): TENANT_MANAGE,
    ("DELETE", "/tenants/{tenant_id}/sites/{site_id}"): TENANT_MANAGE,
    ("PATCH", "/tenants/{tenant_id}/sites/{site_id}"): TENANT_MANAGE,
    # topology
    ("GET", "/topology"): TENANT_READ,
    # virtual
    ("GET", "/portgroups"): TENANT_READ,
    ("GET", "/portgroups/{pg_id}"): TENANT_READ,
    ("GET", "/vms"): TENANT_READ,
    # vpn
    ("GET", "/vpn/tunnels"): TENANT_READ,
    ("GET", "/vpn/tunnels/{tunnel_id}"): TENANT_READ,
    # Kopplung der Tunnel von Hand (1.23.0, ADR 0063 Nachtrag): Laufzeitdaten, kein Geraet
    ("PUT", "/vpn/pairs"): TENANT_MANAGE,
    ("DELETE", "/vpn/pairs/{pair_id}"): TENANT_MANAGE,
    # zones
    ("GET", "/zones"): TENANT_READ,
    ("POST", "/zones"): TENANT_MANAGE,
    ("PUT", "/zones/appdoc"): TENANT_MANAGE,
    ("GET", "/zones/appdoc/catalog"): TENANT_READ,
    ("POST", "/zones/assist"): TENANT_READ,
    ("GET", "/zones/matrix"): TENANT_READ,
    ("GET", "/zones/matrix/rules"): TENANT_READ,
    ("GET", "/zones/plan"): TENANT_READ,
    ("DELETE", "/zones/plan/arrangement"): TENANT_MANAGE,
    ("PUT", "/zones/plan/arrangement"): TENANT_MANAGE,
    ("GET", "/zones/plan/export"): TENANT_READ,
    ("POST", "/zones/plan/import"): TENANT_MANAGE,
    ("POST", "/zones/plan/import/preview"): TENANT_MANAGE,
    ("GET", "/zones/plan/versions"): TENANT_READ,
    ("POST", "/zones/plan/versions"): TENANT_MANAGE,
    ("DELETE", "/zones/plan/versions/{version_id}"): TENANT_MANAGE,
    ("GET", "/zones/plan/versions/{version_id}"): TENANT_READ,
    ("GET", "/zones/plan/versions/{version_id}/diff"): TENANT_READ,
    ("PUT", "/zones/policy"): TENANT_MANAGE,
    ("POST", "/zones/preview"): TENANT_READ,
    ("POST", "/zones/schema/intents"): TENANT_MANAGE,
    ("GET", "/zones/suggestions"): TENANT_READ,
    ("POST", "/zones/suggestions/accept"): TENANT_MANAGE,
    ("POST", "/zones/suggestions/dismiss"): TENANT_MANAGE,
    ("POST", "/zones/suggestions/dismiss/undo"): TENANT_MANAGE,
    ("DELETE", "/zones/{zone_id}"): TENANT_MANAGE,
    ("PUT", "/zones/{zone_id}"): TENANT_MANAGE,
    ("GET", "/zones/{zone_id}/nets"): TENANT_READ,
    ("POST", "/zones/{zone_id}/nodes"): TENANT_MANAGE,
}

#: nie fuer Mandanten-Benutzer (Plattform-Funktionen, ADR 0068 Nr. 5)
PLATFORM_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # agents
        ("GET", "/agents/package"),
        ("GET", "/agents/package/info"),
        ("PATCH", "/agents/{agent_id}"),
        ("POST", "/agents"),
        ("POST", "/agents/{agent_id}/access"),
        # customer_users
        ("DELETE", "/customer-users/{assignment_id}"),
        ("GET", "/customer-users"),
        ("PATCH", "/customer-users/{assignment_id}"),
        ("POST", "/customer-users"),
        # customers
        ("DELETE", "/customers/{customer_id}"),
        ("POST", "/customers"),
        ("PUT", "/customers/{customer_id}"),
        # kopplung
        ("DELETE", "/kopplung/cockpit"),
        ("DELETE", "/kopplung/cockpit/code"),
        ("GET", "/kopplung/cockpit"),
        ("POST", "/kopplung/cockpit/code"),
        # naming_profiles
        ("DELETE", "/naming-profiles/dictionary/{entry_id}"),
        ("DELETE", "/naming-profiles/{profile_id}/appdoc-catalog"),
        ("DELETE", "/naming-profiles/{profile_id}/appdoc-ruleset/{key}"),
        ("DELETE", "/naming-profiles/{profile_id}/zone-schema"),
        ("GET", "/naming-profiles"),
        ("GET", "/naming-profiles/{profile_id}/appdoc-catalog"),
        ("GET", "/naming-profiles/{profile_id}/appdoc-ruleset"),
        ("GET", "/naming-profiles/{profile_id}/dictionary"),
        ("GET", "/naming-profiles/{profile_id}/zone-schema"),
        ("GET", "/naming-profiles/{profile_id}/zone-schema/drawing"),
        ("PATCH", "/naming-profiles/{profile_id}/appdoc-ruleset/{key}"),
        ("PATCH", "/naming-profiles/{profile_id}/zone-schema"),
        ("POST", "/naming-profiles"),
        ("POST", "/naming-profiles/{profile_id}/appdoc-ruleset"),
        ("POST", "/naming-profiles/{profile_id}/appdoc-ruleset/{key}/restore"),
        ("POST", "/naming-profiles/{profile_id}/dictionary"),
        ("POST", "/naming-profiles/{profile_id}/dictionary/copy"),
        ("POST", "/naming-profiles/{profile_id}/zone-schema/preview"),
        ("PUT", "/naming-profiles/{profile_id}/appdoc-catalog"),
        ("PUT", "/naming-profiles/{profile_id}/appdoc-ruleset/active"),
        ("PUT", "/naming-profiles/{profile_id}/appdoc-ruleset/{key}"),
        ("PUT", "/naming-profiles/{profile_id}/zone-schema"),
        # portal
        ("DELETE", "/portal/customers/{pc_id}"),
        ("GET", "/portal/customers"),
        ("POST", "/portal/customers"),
        ("PUT", "/portal/customers/{pc_id}"),
        # system
        ("GET", "/system/connections"),
        ("GET", "/system/health"),
        ("GET", "/system/retention"),
        ("GET", "/system/status"),
        ("POST", "/system/retention/purge"),
        # tenants
        ("DELETE", "/tenants/{tenant_id}"),
        ("PATCH", "/tenants/{tenant_id}"),
        ("POST", "/tenants"),
    }
)


def capability(method: str, path: str) -> str | None:
    """Benoetigtes Recht fuer Methode und Pfad; None = nicht freigegeben."""
    return TENANT_ROUTE_CAPABILITIES.get((method.upper(), path))
