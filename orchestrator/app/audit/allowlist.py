"""Payload-Allowlist fürs Audit-Log (E1.4, Magister-Muster).

Die inhaltliche Secret-Garantie des Audit-Logs liegt hier: Pro Aktion ist genau
definiert, welche `detail`-Keys gespeichert werden dürfen. Alles andere wird
verworfen (fail-closed) - so können Secrets (Tokens, psksecret, Passwörter)
strukturell nie im Log landen, selbst wenn eine Aufrufstelle sie versehentlich
mitgibt. Verworfene Keys werden nur als *Name* vermerkt (`_dropped`), nie als Wert.

Neue Aktionen ohne Eintrag hier verlieren ihr `detail` (nur `_dropped` bleibt) -
bewusst: lieber ein lückenhaftes als ein undichtes Log.
"""

# action -> erlaubte detail-Keys. Nur unkritische, benannte Felder.
ALLOWLIST: dict[str, frozenset[str]] = {
    "collector_run.queued": frozenset(
        {"platform", "tenant_id", "source_system_id", "execution", "trigger"}
    ),
    "collector_run.completed": frozenset({"rule_count", "persisted"}),
    "collector_run.failed": frozenset({"rule_count", "persisted"}),
    "collector_run.claimed": frozenset({"run_id", "platform"}),
    "collector_run.cancelled": frozenset({"platform", "source_system_id"}),
    "agent.created": frozenset({"agent_id", "name", "tenant_id"}),
    "agent.updated": frozenset({"agent_id", "enabled", "auto_update"}),
    "agent.update_downloaded": frozenset({"agent_id", "from_version", "to"}),
    "naming_profile.created": frozenset({"profile_id", "customer", "name", "copied_from"}),
    "zone_schema.saved": frozenset({"profile_id", "zones", "links"}),
    "zone_schema.deleted": frozenset({"profile_id"}),
    "zone_schema.edited": frozenset(
        {"profile_id", "zones", "links", "added", "removed", "changed", "links_changed"}
    ),
    "dictionary.copied": frozenset(
        {"profile_id", "kind", "source_profile_id", "source_kind", "count"}
    ),
    "dictionary_entry.upserted": frozenset({"profile_id", "kind", "key"}),
    "dictionary_entry.deleted": frozenset({"entry_id"}),
    "finding.triaged": frozenset({"finding_id", "status", "customer_roles"}),
    "tenant.created": frozenset({"tenant_id", "name", "code"}),
    "tenant.updated": frozenset(
        {"tenant_id", "code", "audit_reads", "naming_profile_id", "appdoc_foreign_names"}
    ),
    "tenant.deleted": frozenset(
        {
            "tenant_id",
            "name",
            "rules",
            "nat_rules",
            "findings",
            "epgs",
            "objects",
            "zones",
            "applications",
            "sites",
            "source_systems",
            "runs",
            "agents",
            "references",
        }
    ),
    "source.created": frozenset(
        {
            "source_id",
            "tenant_id",
            "name",
            "platform",
            "agent_id",
            "credentials_set",
            "ref_role",
            "site_id",
            "schedule",
            "access",  # Switches & Router: Zugang ssh | snmp (ADR 0033)
        }
    ),
    "source.updated": frozenset(
        {
            "source_id",
            "tenant_id",
            "name",
            "enabled",
            "agent_id",
            "credentials_set",
            "ref_role",
            "site_id",
            "schedule",
            "parallel_group",
            "access",  # Wechsel des Zugangs (ADR 0033)
        }
    ),
    # Parallele Firewalls (0.18.0): Verbund gesetzt bzw. aufgeloest
    "source.parallel_group": frozenset({"tenant_id", "group", "source_ids"}),
    "source.parallel_rejected": frozenset({"tenant_id", "source_ids", "pairs"}),
    "source.parallel_unrejected": frozenset({"tenant_id", "source_ids", "pairs"}),
    # Anbindungen nach aussen (0.18.0): von Hand gepflegte Topologie
    "routing.exit.created": frozenset({"tenant_id", "exit_id", "name", "kind"}),
    "routing.exit.updated": frozenset({"tenant_id", "exit_id", "name", "kind"}),
    "routing.exit.deleted": frozenset({"tenant_id", "exit_id", "name", "kind"}),
    # erkannte Anbindungen ausblenden bzw. wieder anzeigen (0.21.0): nur die Router-IPs
    "routing.exit.dismissed": frozenset({"tenant_id", "ips"}),
    "routing.exit.undismissed": frozenset({"tenant_id", "ips"}),
    # Firewall hinter einem L3Out von Hand (ADR 0034): L3Out, Herkunft, Firewalls (ID, VDOM,
    # Interface)
    "routing.l3out_firewall.set": frozenset(
        {"tenant_id", "source_system_id", "dn", "l3out", "origin", "firewalls"}
    ),
    "routing.l3out_firewall.removed": frozenset({"tenant_id", "source_system_id", "dn", "l3out"}),
    "reconcile.run": frozenset({"tenant_id", "findings"}),
    # NetBox-Sync (ADR 0010): nur Anzahlen und die gewaehlten Abschnitte (ADR 0031), keine Inhalte
    "netbox_sync.planned": frozenset(
        {
            "plan_id",
            "tenant_id",
            "source_system_id",
            "sections",
            "create",
            "update",
            "delete",
            "conflict",
        }
    ),
    "netbox_sync.applied": frozenset(
        {
            "plan_id",
            "tenant_id",
            "source_system_id",
            "status",
            "sections",
            "create",
            "update",
            "delete",
        }
    ),
    "netbox_sync.cancelled": frozenset({"plan_id", "tenant_id"}),
    "rule_pair.decided": frozenset({"source_system_id", "vdom", "decision"}),
    "rule_pair.reset": frozenset({"source_system_id"}),
    "source.deleted": frozenset(
        {"source_id", "tenant_id", "name", "rules", "nat_rules", "findings", "references"}
    ),
    "site.created": frozenset({"tenant_id", "code", "kind"}),
    "site.updated": frozenset({"tenant_id", "site_id", "fields"}),
    "site.deleted": frozenset({"tenant_id", "site_id"}),
    "system.site_assigned": frozenset({"tenant_id", "site_id"}),
    # Applikationen (ADR 0012): Stammdaten-Felder, Mitglieder nur als Art/Verweis; handelt ein
    # Kunden-Benutzer (feinere Rollen), seine Kunden-Rollen je Kunde (`customer_roles`)
    "application.created": frozenset(
        {"application_id", "tenant_id", "name", "code", "members", "suggestion"}
    ),
    "application.updated": frozenset(
        {
            "application_id",
            "tenant_id",
            "name",
            "code",
            "criticality",
            "purpose",
            "site_id",
            "infrastructure",
            "fields",
            "customer_roles",
        }
    ),
    "application.deleted": frozenset({"application_id", "tenant_id", "name", "members"}),
    # 0.75.0: Stammdaten aus CSV - nur Zahlen und Kennungen, keine Inhalte der Datei
    "application.imported": frozenset(
        {"tenant_id", "created", "updated", "errors", "application_ids"}
    ),
    "application.member_added": frozenset(
        {"application_id", "tenant_id", "kind", "ref", "count", "suggestion", "customer_roles"}
    ),
    "application.member_removed": frozenset(
        {"application_id", "tenant_id", "kind", "ref", "customer_roles"}
    ),
    # 0.69.0: Regel in den Kategorien der Doku aus-/eingeblendet, mit Begruendung
    "application.rule_hidden": frozenset(
        {"application_id", "tenant_id", "rule_ref", "category", "reason", "customer_roles"}
    ),
    "application.rule_shown": frozenset(
        {"application_id", "tenant_id", "rule_ref", "category", "reason", "customer_roles"}
    ),
    # Kunden als Ausschnitt der Umgebung (ADR 0018): Name und Anzahl der Selektoren
    "customer.created": frozenset({"tenant_id", "customer_id", "name", "selectors"}),
    "customer.updated": frozenset({"tenant_id", "customer_id", "name", "selectors"}),
    "customer.deleted": frozenset({"tenant_id", "customer_id", "name", "selectors"}),
    # Zuordnung Benutzer -> Kunde in Automato (0.53.0, ADR 0018 Schritt 4); seit den feineren
    # Rollen mit Herkunft (Betreuer bzw. Kunden-Admin) und den Kunden-Rollen des Handelnden
    "customer_user.assigned": frozenset(
        {"tenant_id", "customer_id", "login", "role", "origin", "customer_roles"}
    ),
    "customer_user.changed": frozenset(
        {"tenant_id", "customer_id", "login", "role", "previous_role", "origin", "customer_roles"}
    ),
    "customer_user.removed": frozenset(
        {"tenant_id", "customer_id", "login", "role", "origin", "customer_roles"}
    ),
    # Logische Zonen und Absicht je Zonenpaar (0.51.0, ADR 0024)
    "zone.created": frozenset({"tenant_id", "zone_id", "name", "kind", "selectors", "via"}),
    "zone.updated": frozenset({"tenant_id", "zone_id", "name", "kind", "selectors", "via"}),
    "zone.deleted": frozenset({"tenant_id", "zone_id", "name", "kind", "selectors"}),
    # Zonen den Kategorien der Applikations-Doku zugeordnet (0.101.0): je Zone [ID, Bereich]
    "zone.appdoc_areas_set": frozenset({"tenant_id", "changes"}),
    "zone_policy.set": frozenset(
        {"tenant_id", "src_zone_id", "dst_zone_id", "intent", "services", "via"}
    ),
    # Versionen des Zonenplans (0.52.0)
    "zone_suggestion.dismissed": frozenset({"tenant_id", "keys"}),
    "zone_suggestion.undismissed": frozenset({"tenant_id", "keys"}),
    "zone_plan.version_saved": frozenset({"tenant_id", "version_id", "number", "label"}),
    "zone_plan.version_deleted": frozenset({"tenant_id", "version_id", "number", "label"}),
    # Lesezugriffs-Audit (per Mandant zuschaltbar, ADR 0007 D5)
    # Kunden-Benutzer (ADR 0018): immer, mit ihren Kunden-IDs
    "read.rules": frozenset({"tenant_id", "resource", "customers"}),
    "read.nat": frozenset({"tenant_id", "resource", "customers"}),
    "read.findings": frozenset({"tenant_id", "resource", "customers"}),
    "read.applications": frozenset({"tenant_id", "resource", "customers"}),
    "read.published_services": frozenset({"tenant_id", "resource"}),
    "read.appdoc": frozenset({"tenant_id", "resource", "customers"}),
    "read.routing": frozenset({"tenant_id", "resource"}),
    "read.objects": frozenset({"tenant_id", "resource", "customers"}),
    "read.virtual": frozenset({"tenant_id", "resource", "customers"}),
    "read.customers": frozenset({"tenant_id", "resource", "customers"}),
    "read.zones": frozenset({"tenant_id", "resource"}),
    "retention.purged": frozenset(
        {"runs", "sync_plans", "audit", "l2", "topo_changes"}
        | {"runs_days", "sync_days", "audit_days", "l2_days", "topo_changes_days"}
    ),
}

_DROPPED_KEY = "_dropped"


def sanitize(action: str, detail: dict | None) -> dict | None:
    """Filtert `detail` gegen die Allowlist der Aktion.

    Behält nur erlaubte Keys; verworfene werden (nur als Name) unter `_dropped`
    vermerkt, damit Lücken sichtbar sind, ohne Werte preiszugeben. Leeres/`None`
    Ergebnis -> `None`.
    """
    if not detail:
        return None
    allowed = ALLOWLIST.get(action, frozenset())
    kept = {k: v for k, v in detail.items() if k in allowed}
    dropped = sorted(k for k in detail if k not in allowed)
    if dropped:
        kept[_DROPPED_KEY] = dropped
    return kept or None
