"""Module der Plattform (0.107.0, ADR 0036).

Anwender 2026-10-28: „Mehr modularisieren und Elemente mit Schnittstellen besser bauen, dass wir
die kleinen Module einfacher anpassen können“ - „Ich würde ganz klar die Module Worker, Gateway,
Doku Modul, Weg sucher, Findings und Analyse erstellen. Damit man die auch modular deaktivieren
kann“ - die Zonen als eigenes Modul „Design“, „je Mandant wählbar“ - und: die lesenden Module
„sollen immer starr getrennt bleiben von dem schreibenden Pfad“.

- **Verzeichnis:** je Modul Zweck, Art (Basis, lesend, schreibend), Abhängigkeiten, die Dateien
  in `app/api` und `app/store` und die Ansichten der Oberfläche. Jede Datei gehört genau einem
  Modul; `tests/test_module_boundaries.py` prüft das und die Grenzen (der Schreibpfad ist der
  einzige, der `sot_gateway.adapters.netbox_writer` bzw. `store/netbox_sync` nutzt).
- **Je Mandant schaltbar:** die Fachmodule (alles ausser Kern und Einstellungen). Aus heisst:
  keine Ansicht, keine API (403 `module_disabled`), keine Arbeit im Hintergrund dafür - die
  gelesenen Daten bleiben. Ein Modul, dessen Abhängigkeit aus ist, ist auch aus.
- **Kern und Einstellungen** sind immer an: Mandanten, Quellsysteme, Regeln, Objekte, NAT,
  Kunden, Benutzer bzw. Vorgaben (Namensprofile) und die Schaltung der Module - die Vorstufe des
  Cockpits (Basis-Settings je Mandant).

Nur Konfiguration in Automato (Tabelle `tenant_module`); kein neuer Schreibweg.
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session_factory
from app.i18n import L

BASIS, READ, WRITE = "basis", "lesen", "schreiben"


@dataclass(frozen=True)
class Module:
    key: str
    title: tuple[str, str]  # deutsch, englisch
    purpose: tuple[str, str]
    role: str = READ  # basis | lesen | schreiben
    depends: tuple[str, ...] = ()
    api: tuple[str, ...] = ()  # Dateien in app/api (ohne .py)
    store: tuple[str, ...] = ()  # Dateien in app/store
    views: tuple[str, ...] = ()  # Ansichten der Oberflaeche (View in App.tsx)
    packages: tuple[str, ...] = ()  # weitere Pakete des Workspace

    @property
    def switchable(self) -> bool:
        return self.role != BASIS

    def out(self) -> dict:
        return {
            "key": self.key,
            "title": L(*self.title),
            "purpose": L(*self.purpose),
            "role": self.role,
            "switchable": self.switchable,
            "depends": list(self.depends),
            "views": list(self.views),
        }


MODULES: tuple[Module, ...] = (
    Module(
        "kern",
        ("Kern", "Core"),
        (
            "Mandanten, Quellsysteme, Regeln & Contracts, Objekte, NAT/VIPs, Kunden, Benutzer",
            "Tenants, source systems, rules & contracts, objects, NAT/VIPs, customers, users",
        ),
        role=BASIS,
        api=(
            "tenants",
            "systems",
            "rules",
            "objects",
            "nat",
            "virtual",
            "customers",
            "customer_users",
            "customer_filter",
            "auth_config",
            "system",
            "health",
        ),
        store=(
            "aci_direction",
            "aci_relevance",
            "aci_shared",
            "connections",
            "customer_alias",
            "customer_infra",
            "customer_scope",
            "customers",
            "db_roles",
            "deletion",
            "epgs",
            "epg_locate",
            "external",
            "health",
            "nat",
            "objects",
            "parallel",
            "reachability",
            "relevance",
            "retention",
            "rule_index",
            "rule_pairs",
            "rule_view",
            "rules",
            "sites",
            "tenant_lock",
            "topology",
            "virtual",
        ),
        views=(
            "uebersicht",
            "regeln",
            "objekte",
            "nat",
            "users",
            "tenants",
            "kunden",
            "kundenbenutzer",
            "system",
        ),
    ),  # fmt: skip
    Module(
        "einstellungen",
        ("Einstellungen", "Settings"),
        (
            "Vorgaben und Basis-Settings: Namensprofile, Kategorien, Grundschema, Module je "
            "Mandant (Vorstufe des Cockpits)",
            "Defaults and base settings: naming profiles, categories, base schema, modules per "
            "tenant (first step towards the cockpit)",
        ),
        role=BASIS,
        api=("naming_profiles", "modules"),
        store=("naming_profiles", "appdoc_catalog", "zone_schema"),
        views=("dict",),
    ),  # fmt: skip
    Module(
        "worker",
        ("Worker", "Worker"),
        (
            "Liest die Geräte (Collector, Agenten, Läufe, Zeitplan) - nur lesend",
            "Reads the devices (collector, agents, runs, schedule) - read-only",
        ),
        api=("jobs", "agents", "internal"),
        store=("agents", "agent_package", "ingest", "runs", "run_text"),
        views=("laeufe", "agents"),
        packages=("collectors",),
    ),  # fmt: skip
    Module(
        "gateway",
        ("Gateway", "Gateway"),
        (
            "Referenzquellen lesen: IPAM/DNS (Infoblox, NetBox) - nur lesend",
            "Reads reference sources: IPAM/DNS (Infoblox, NetBox) - read-only",
        ),
        store=("references",),
        packages=(
            "sot_gateway.adapters.infoblox",
            "sot_gateway.adapters.netbox",
            "sot_gateway.readonly",
        ),
    ),  # fmt: skip
    Module(
        "analyse",
        ("Analyse", "Analysis"),
        (
            "Routing, Verkabelung, Abgleich IPAM, Topologie, Datenqualität",
            "Routing, cabling, IPAM reconciliation, topology, data quality",
        ),
        api=("routing", "cabling", "reconcile", "topology", "data_quality"),
        store=(
            "boundary",
            "cabling",
            "data_quality",
            "exits",
            "l2",
            "l2_export",
            "l2_topo",
            "l3out_firewall",
            "parallel_net",
            "reconcile",
            "topo_changes",
            "topo_model",
        ),
        views=("routing", "verkabelung", "abgleich"),
    ),  # fmt: skip
    Module(
        "wege",
        ("Wegsucher", "Path finder"),
        (
            "Weg-Suche und Verbindungen Ende zu Ende (Routing, Policies, Contracts je Hop)",
            "Path search and end-to-end connections (routing, policies, contracts per hop)",
        ),
        store=(
            "paths",
            "policy_check",
            "aci_check",
            "exit_ends",
            "resolve",
            "app_paths",
            "app_plan",
            "zone_path",
            "path_explain",  # Warum dieser Weg? (0.109.0)
        ),
        views=("wege",),
    ),  # fmt: skip
    Module(
        "findings",
        ("Findings", "Findings"),
        (
            "Befunde mit Triage; Befunde der Applikationen im Hintergrund",
            "Findings with triage; findings of the applications in the background",
        ),
        api=("findings",),
        store=("findings", "finding_text"),
        views=("findings",),
    ),  # fmt: skip
    Module(
        "doku",
        ("Doku", "Documentation"),
        (
            "Applikationen und ihre Doku nach Kategorien, Export Word/Markdown",
            "Applications and their documentation by category, export Word/Markdown",
        ),
        api=("applications",),
        store=(
            "applications",
            "appdoc",
            "appdoc_filter",
            "appdoc_zones",
            "peer_names",
            "app_import",
            "app_refresh",
            "doc_snapshots",  # Stand der Doku im Hintergrund (0.108.0, ADR 0037)
            "docx_writer",
            "md_writer",
            "word_export",
        ),
        views=("apps",),
    ),  # fmt: skip
    Module(
        "design",
        ("Design", "Design"),
        (
            "Netzwerkdesign: logische Zonen, Zonentyp, Geräte, Zonenmatrix, Zonenplan",
            "Network design: logical zones, zone type, devices, zone matrix, zone plan",
        ),
        api=("zones",),
        store=(
            "zones",
            "zone_assist",
            "zone_detect",
            "zone_exits",
            "zone_services",
            "zone_struct",
            "zone_types",
            "plan_export",
            "plan_import",
            "plan_versions",
            "plan_view",
        ),
        views=("design",),
    ),  # fmt: skip
    Module(
        "schreibpfad",
        ("Schreibpfad", "Write path"),
        (
            "Einziger Schreibweg: NetBox-Sync (Vorschau → Übernehmen), getrennt von allem Lesen",
            "The only write path: NetBox sync (preview → apply), separate from all reading",
        ),
        role=WRITE,
        depends=("gateway",),
        api=("netbox_sync",),
        store=("netbox_sync",),
        views=("netbox",),
        packages=("sot_gateway.adapters.netbox_writer",),
    ),  # fmt: skip
)
BY_KEY = {m.key: m for m in MODULES}
# Teile eines Moduls, die bei einem anderen liegen: Pfad-Teil -> Modul (Verbindungen der
# Applikations-Doku sind der Wegsucher, die Weg-Suche liegt in der Routing-API)
PATHS = {"/connections": "wege", "/routing/trace": "wege", "/routing/resolve": "wege",
         "/routing/explain": "wege"}  # fmt: skip


def _rows(session: Session, tenant_id: int) -> dict[str, bool]:
    from app.models import TenantModule  # spaet: Modelle laden app.db

    key = ("modules", tenant_id)
    if key not in session.info:
        session.info[key] = {
            m: on for m, on in session.execute(
                select(TenantModule.module, TenantModule.enabled)
                .where(TenantModule.tenant_id == tenant_id))
        }  # fmt: skip
    return session.info[key]


def states(session: Session, tenant_id: int | None) -> dict[str, bool]:
    """Modul -> an? Ohne Mandant (alle) bzw. ohne Eintrag an; mit einer ausgeschalteten
    Abhaengigkeit aus."""
    rows = _rows(session, tenant_id) if tenant_id is not None else {}
    out: dict[str, bool] = {}
    for m in MODULES:  # Abhaengigkeiten stehen vorher
        own = True if not m.switchable else rows.get(m.key, True)
        out[m.key] = own and all(out.get(d, True) for d in m.depends)
    return out


def enabled(session: Session, tenant_id: int | None, key: str) -> bool:
    return states(session, tenant_id).get(key, True)


def own_state(session: Session, tenant_id: int, key: str) -> bool:
    """Eigene Schaltung (ohne Abhaengigkeiten)."""
    return _rows(session, tenant_id).get(key, True)


class Invalid(ValueError):
    pass


def switch(session: Session, tenant_id: int, key: str, on: bool, actor: str) -> None:
    """Modul fuer den Mandanten ein- bzw. ausschalten (Admin, Audit beim Aufrufer)."""
    from datetime import UTC, datetime

    from app.models import TenantModule

    m = BY_KEY.get(key)
    if m is None:
        raise KeyError(key)
    if not m.switchable:
        raise Invalid(L(f"{L(*m.title)} ist immer an", f"{L(*m.title)} is always on"))
    if on:
        off = [d for d in m.depends if not enabled(session, tenant_id, d)]
        if off:
            names = ", ".join(L(*BY_KEY[d].title) for d in off)
            raise Invalid(L(f"zuerst {names} einschalten", f"switch on {names} first"))
    row = session.get(TenantModule, (tenant_id, key))
    if row is None:
        row = TenantModule(tenant_id=tenant_id, module=key)
        session.add(row)
    row.enabled, row.changed_by, row.changed_at = on, actor, datetime.now(UTC)
    session.flush()
    session.info.pop(("modules", tenant_id), None)


def disabled_text(key: str) -> str:
    m = BY_KEY[key]
    return L(f"Modul „{L(*m.title)}“ ist für diesen Mandanten ausgeschaltet",
             f"Module “{L(*m.title)}” is switched off for this tenant")  # fmt: skip


def module_of(path: str, default: str | None) -> str | None:
    return next((k for part, k in PATHS.items() if part in path), default)


def _tenant_of(request: Request, session: Session, principal) -> int | None:
    raw = request.query_params.get("tenant_id")
    if raw and raw.isdigit():
        return int(raw)
    from app.models import Application, Finding, LogicalZone

    for param, model in (("application_id", Application), ("zone_id", LogicalZone),
                         ("finding_id", Finding)):  # fmt: skip
        value = str(request.path_params.get(param, ""))
        if value.isdigit():
            obj = session.get(model, int(value))
            return obj.tenant_id if obj is not None else None
    allowed = getattr(principal, "tenant_ids", None)
    return allowed[0] if allowed and len(allowed) == 1 else None


def guard(key: str | None):
    """Dependency fuer die Router eines Moduls: aus fuer den Mandanten der Anfrage -> 403
    (`module_disabled`). Den Mandanten nimmt sie aus `tenant_id`, dem Objekt im Pfad
    (Applikation, Zone, Befund) bzw. dem einzigen des Benutzers. `key` None: nur die Teile
    anderer Module (`PATHS`, z. B. die Weg-Suche in der Routing-API des Kerns)."""
    from app.auth.deps import get_principal  # spaet: auth nutzt Modelle

    def check(request: Request, principal=Depends(get_principal),
              factory: sessionmaker = Depends(get_session_factory)) -> None:  # fmt: skip
        want = module_of(request.url.path, key)
        if want is None:
            return
        with factory() as session:
            tid = _tenant_of(request, session, principal)
            if tid is None or enabled(session, tid, want):
                return
        detail = {"code": "module_disabled", "module": want, "message": disabled_text(want)}
        raise HTTPException(status_code=403, detail=detail)

    return check
