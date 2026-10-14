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
    "collector_run.queued": frozenset({"platform", "tenant_id", "source_system_id", "execution"}),
    "collector_run.completed": frozenset({"rule_count", "persisted"}),
    "collector_run.failed": frozenset({"rule_count", "persisted"}),
    "collector_run.claimed": frozenset({"run_id", "platform"}),
    "agent.created": frozenset({"agent_id", "name", "tenant_id"}),
    "naming_profile.created": frozenset({"profile_id", "customer", "name"}),
    "dictionary_entry.upserted": frozenset({"profile_id", "kind", "key"}),
    "dictionary_entry.deleted": frozenset({"entry_id"}),
    "finding.triaged": frozenset({"finding_id", "status"}),
    "tenant.updated": frozenset({"tenant_id", "code", "audit_reads"}),
    "site.created": frozenset({"tenant_id", "code"}),
    "site.deleted": frozenset({"tenant_id", "site_id"}),
    "system.site_assigned": frozenset({"tenant_id", "site_id"}),
    # Lesezugriffs-Audit (per Mandant zuschaltbar, ADR 0007 D5)
    "read.rules": frozenset({"tenant_id", "resource"}),
    "read.nat": frozenset({"tenant_id", "resource"}),
    "read.findings": frozenset({"tenant_id", "resource"}),
    "read.applications": frozenset({"tenant_id", "resource"}),
    "read.appdoc": frozenset({"tenant_id", "resource"}),
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
