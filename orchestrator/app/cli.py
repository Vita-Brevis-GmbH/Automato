"""Minimal-CLI zum Anstossen von Collector-Laeufen (M7-Auslöser).

Legt idempotent Mandant und Quellsystem an und stellt einen Collect-Lauf in die
Queue - derselbe Pfad wie ``POST /jobs/collect``, nur ohne HTTP. Gedacht fuer den
Lab-Durchstich und als Betriebs-/Cron-Auslöser, solange das Web-UI noch fehlt.

Im Orchestrator-Container (Beispiele):
  automato ensure-tenant --name kunde-a --ipam netbox --dns active_directory
  automato ensure-source --tenant kunde-a --name fw-01 --platform fortigate --base-url https://fw-01
  automato collect --platform fortigate --tenant kunde-a --source fw-01
  automato status --run-id <id>
  automato lab            # Model-2-Happy-Path (NetBox + FortiGate) in einem Schritt
  automato check-connections [--json]   # Verbindungen aus dem Container (deploy/aufbau.sh)
  automato db-roles       # App-Rolle ohne DDL einrichten (Dienst migrate, nach alembic)
  automato purge [--dry-run]   # alte Laeufe/Syncs/Audit nach Frist loeschen (0.48.0)
  automato tls-init --dir /pki [--name <vm>] [--renew]   # interne CA + Zertifikate (0.49.0)

Sprache der Ausgaben (0.88.0, ADR 0028 Schritt 4): `--lang de|en` (vor oder nach dem Befehl)
bzw. Umgebungsvariable `AUTOMATO_LANG`, Standard Deutsch. Was gespeichert wird (Laeufe, Audit),
bleibt deutsch (`@german_only`); `status` zeigt die gespeicherte Meldung uebersetzt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_sessionmaker
from app.i18n import LANGS, L, german, german_only, use
from app.jobs import celery_app
from app.models import CollectorRun, DnsType, IpamType, Platform, SourceSystem, Tenant
from app.store import agent_package, runs


def _error(de: str, en: str) -> None:
    print(f"{L('FEHLER', 'ERROR')}: {L(de, en)}", file=sys.stderr)


@german_only
def ensure_tenant(
    session: Session, name: str, ipam: str, dns: str, code: str | None = None
) -> Tenant:
    tenant = session.scalar(select(Tenant).where(Tenant.name == name))
    if tenant is None:
        tenant = Tenant(name=name, ipam_type=IpamType(ipam), dns_type=DnsType(dns), code=code)
        session.add(tenant)
        session.flush()
    elif code is not None:
        tenant.code = code  # Kuerzel aktuell halten
    return tenant


@german_only
def ensure_source(
    session: Session, tenant: Tenant, name: str, platform: str, base_url: str
) -> SourceSystem:
    """Quellsystem des Mandanten idempotent anlegen (Name je Mandant eindeutig)."""
    src = _find_source(session, name, tenant)
    if src is None:
        src = SourceSystem(
            tenant_id=tenant.id, name=name, platform=Platform(platform), base_url=base_url
        )
        session.add(src)
        session.flush()
    elif base_url:
        src.base_url = base_url  # base_url aktuell halten
    return src


@german_only
def queue_collect(
    session: Session,
    platform: str,
    tenant: Tenant | None,
    source: SourceSystem | None,
    execution: str = "inprocess",
) -> CollectorRun:
    """Wie der /jobs/collect-Endpunkt: Run anlegen, auditieren, dann dispatchen.

    execution="external" (ADR 0008): kein Celery-Dispatch - ein externer Agent holt den
    Lauf per Pull (`automato-collector --poll`).
    """
    run = CollectorRun(
        platform=platform,
        tenant_id=tenant.id if tenant else None,
        source_system_id=source.id if source else None,
        execution=execution,
    )
    session.add(run)
    session.flush()
    audit.record(
        session,
        actor="cli",
        action="collector_run.queued",
        subject=run.id,
        detail={
            "platform": platform,
            "tenant_id": run.tenant_id,
            "source_system_id": run.source_system_id,
            "execution": execution,
        },
    )
    session.commit()  # Run muss sichtbar sein, bevor der Worker das Ergebnis zurueckmeldet
    if execution == "inprocess":
        celery_app.dispatch_collect(platform, run.id)
    return run


def _find_tenant(session: Session, name: str | None) -> Tenant | None:
    return session.scalar(select(Tenant).where(Tenant.name == name)) if name else None


def _find_source(session: Session, name: str | None, tenant: Tenant | None) -> SourceSystem | None:
    if not name:
        return None
    stmt = select(SourceSystem).where(SourceSystem.name == name)
    if tenant is not None:
        stmt = stmt.where(SourceSystem.tenant_id == tenant.id)
    return session.scalars(stmt).first()


def _cmd_ensure_tenant(session: Session, args: argparse.Namespace) -> int:
    tenant = ensure_tenant(session, args.name, args.ipam, args.dns, code=args.code)
    session.commit()
    print(f"tenant id={tenant.id} name={tenant.name} code={tenant.code} ipam={tenant.ipam_type}")
    return 0


def _cmd_ensure_source(session: Session, args: argparse.Namespace) -> int:
    tenant = _find_tenant(session, args.tenant)
    if tenant is None:
        _no_tenant()
        return 2
    src = ensure_source(session, tenant, args.name, args.platform, args.base_url)
    session.commit()
    print(f"source id={src.id} tenant={tenant.id} name={src.name} platform={src.platform}")
    return 0


def _no_tenant() -> None:
    _error("Mandant nicht gefunden (erst 'ensure-tenant')",
           "tenant not found (run 'ensure-tenant' first)")  # fmt: skip


def _cmd_collect(session: Session, args: argparse.Namespace) -> int:
    tenant = (
        session.get(Tenant, args.tenant_id)
        if args.tenant_id
        else _find_tenant(session, args.tenant)
    )
    source = (
        session.get(SourceSystem, args.source_system_id)
        if args.source_system_id
        else (_find_source(session, args.source, tenant))
    )
    if (args.tenant or args.tenant_id) and tenant is None:
        _no_tenant()
        return 2
    if (args.source or args.source_system_id) and source is None:
        _error("Quellsystem nicht gefunden (erst 'ensure-source')",
               "source system not found (run 'ensure-source' first)")  # fmt: skip
        return 2
    if source is not None and tenant is not None and source.tenant_id != tenant.id:
        _error("Quellsystem gehoert zu einem anderen Mandanten",
               "source system belongs to another tenant")  # fmt: skip
        return 2
    run = queue_collect(session, args.platform, tenant, source, args.execution)
    print(f"run_id={run.id} status={run.status} -> 'automato status --run-id {run.id}'")
    if args.execution == "external":
        print(L("Externer Lauf: wartet auf einen Agenten ('automato-collector --poll').",
                "External run: waiting for an agent ('automato-collector --poll')."))  # fmt: skip
    return 0


def _cmd_status(session: Session, args: argparse.Namespace) -> int:
    run = session.get(CollectorRun, args.run_id)
    if run is None:
        _error("Run nicht gefunden", "run not found")
        return 2
    message = runs.show_message(run.message)  # gespeichert deutsch, Anzeige in --lang
    print(
        f"run_id={run.id} status={run.status} rules={run.rule_count} "
        f"tenant={run.tenant_id} source={run.source_system_id} message={message or '-'}"
    )
    return 0


def _cmd_lab(session: Session, args: argparse.Namespace) -> int:
    """Model-2-Happy-Path: NetBox-Mandant + FortiGate-Quelle + ein Collect-Lauf."""
    tenant = ensure_tenant(session, args.tenant, "netbox", "active_directory")
    source = ensure_source(session, tenant, args.source, "fortigate", args.base_url)
    session.flush()
    run = queue_collect(session, "fortigate", tenant, source)
    print(
        L(f"Lab-Lauf gestartet: tenant={tenant.id} source={source.id} run_id={run.id}\n"
          f"Status abfragen: automato status --run-id {run.id}",
          f"Lab run started: tenant={tenant.id} source={source.id} run_id={run.id}\n"
          f"Query the status: automato status --run-id {run.id}")
    )  # fmt: skip
    return 0


def _cmd_pkg_manifest(_session: Session, args: argparse.Namespace) -> int:
    """Nachricht zum Signieren des aktuellen Agent-Pakets (offline: scripts/agent_signing.py)."""
    info = agent_package.manifest(get_settings().agent_package_dir)
    if info is None:
        _no_package()
        return 2
    data = agent_package.signed_message(info)
    if args.out:
        Path(args.out).write_bytes(data)
        version = info["version"]
        print(L(f"Nachricht fuer {version} -> {args.out}", f"Message for {version} -> {args.out}"))
    else:
        sys.stdout.write(data.decode())
    return 0


def _no_package() -> None:
    _error("kein Agent-Paket im Orchestrator (agent_package_dir)",
           "no agent package in the orchestrator (agent_package_dir)")  # fmt: skip


def _cmd_pkg_install_signature(_session: Session, args: argparse.Namespace) -> int:
    """Offline erstellte Signatur einspielen (prueft Version und - wenn hinterlegt - Schluessel)."""
    settings = get_settings()
    info = agent_package.manifest(settings.agent_package_dir)
    if info is None:
        _no_package()
        return 2
    try:
        sig = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _error(f"Signaturdatei nicht lesbar: {exc}", f"signature file not readable: {exc}")
        return 2
    version, signed = info["version"], sig.get("version")
    if signed != version:
        _error(f"Signatur gilt fuer {signed}, Paket ist {version}",
               f"signature is for {signed}, package is {version}")  # fmt: skip
        return 2
    if settings.agent_signing_pubkey:
        if not agent_package.verify(settings.agent_signing_pubkey, info, sig.get("signature", "")):
            _error("Signatur passt nicht zum hinterlegten Schluessel",
                   "signature does not match the stored key")  # fmt: skip
            return 2
        print(L("Signatur geprueft (AUTOMATO_AGENT_SIGNING_PUBKEY)",
                "Signature verified (AUTOMATO_AGENT_SIGNING_PUBKEY)"))  # fmt: skip
    else:
        print(
            L(
                "Hinweis: AUTOMATO_AGENT_SIGNING_PUBKEY fehlt - Signatur nicht geprueft",
                "Note: AUTOMATO_AGENT_SIGNING_PUBKEY missing - signature not verified",
            )
        )
    target = Path(settings.agent_signature_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": version, "signature": sig["signature"]}))
    print(L(f"Signatur fuer {version} eingespielt -> {target}",
            f"Signature for {version} installed -> {target}"))  # fmt: skip
    return 0


# Marke je Zustand, 6 Zeichen breit (deutsch, englisch)
MARK = {"ok": ("OK", "OK"), "info": ("INFO", "INFO"), "warn": ("WARN", "WARN"),
        "error": ("FEHLER", "ERROR")}  # fmt: skip


def _mark(status: str) -> str:
    return f"{L(*MARK[status]):<6}"


def _cmd_check_connections(session: Session, args: argparse.Namespace) -> int:
    """Verbindungen vom Container aus (0.40.0) - wie System → Verbindungen; Exit 1 bei Fehler."""
    from app.store import connections

    result = connections.run(session, get_settings())
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for item in result["items"]:
            target = f"  {item['target']}" if item["target"] else ""
            print(f"{_mark(item['status'])} {item['name']}{target}")
            for step in item["steps"]:
                print(f"         {'✓' if step['ok'] else '✗'} {step['step']}: {step['detail']}")
            if item["hint"] and item["status"] != "ok":
                print(f"         → {item['hint']}")
    return 1 if result["status"] == "error" else 0


def _cmd_db_roles(_session: Session, args: argparse.Namespace) -> int:
    """Getrennte DB-Rollen (0.48.0): als Eigentuemer (Dienst migrate) die App-Rolle einrichten."""
    from sqlalchemy import create_engine

    from app.secrets import resolve
    from app.store import db_roles

    settings = get_settings()
    role = args.role or settings.db_app_role
    if not role:
        print(
            L(
                "INFO   keine App-Rolle gesetzt (AUTOMATO_DB_USER in .env) - der Orchestrator",
                "INFO   no app role set (AUTOMATO_DB_USER in .env) - the orchestrator",
            )
        )
        print(
            L(
                "       arbeitet als Eigentuemer der Tabellen; einrichten:",
                "       works as owner of the tables; to set up:",
            )
        )
        print("       deploy/aufbau.sh db-rollen")
        return 0
    from app import tls

    engine = create_engine(settings.database_url, connect_args=tls.db_connect_args(settings))
    try:
        with engine.begin() as conn:
            owner = conn.exec_driver_sql("SELECT current_user").scalar()
            if role == owner:
                print(L(f"INFO   App-Rolle = Eigentuemer ({owner}) - keine getrennte Rolle",
                        f"INFO   app role = owner ({owner}) - no separate role"))  # fmt: skip
                return 0
            password = resolve(settings.db_app_password, settings)
            steps = db_roles.ensure_app_role(conn, role, password)
    except db_roles.RoleError as exc:
        print(f"{_mark('error')} {exc}")
        return 1
    finally:
        engine.dispose()
    for step in steps:
        print(f"OK     {step}")
    return 0


def _cmd_purge(session: Session, args: argparse.Namespace) -> int:
    """Aufbewahrung (0.48.0): was ueber der Frist ist loeschen bzw. mit --dry-run nur zaehlen."""
    from app.store import retention

    policy = retention.Policy.of(get_settings())
    if args.dry_run:
        counts = retention.due(session, policy)
    else:
        with german():  # Audit `retention.purged` (gespeichert)
            counts = retention.purge(session, policy, actor="cli:purge")
        session.commit()
    spans = f"{policy.runs_days}/{policy.sync_days}/{policy.audit_days}/{policy.l2_days}"
    c = counts
    print(
        L(f"{'faellig' if args.dry_run else 'geloescht'}: {c['runs']} Laeufe, "
          f"{c['sync_plans']} NetBox-Sync-Vorschauen, {c['audit']} Audit-Eintraege, "
          f"{c['l2']} MAC-/ARP-Eintraege (Fristen {spans} Tage, 0 = nie)",
          f"{'due' if args.dry_run else 'deleted'}: {c['runs']} runs, "
          f"{c['sync_plans']} NetBox sync previews, {c['audit']} audit entries, "
          f"{c['l2']} MAC/ARP entries (retention {spans} days, 0 = never)")
    )  # fmt: skip
    return 0


def _cmd_tls_init(_session: Session, args: argparse.Namespace) -> int:
    """TLS intern (0.49.0): CA und Server-Zertifikate je Dienst (als root im Dienst migrate)."""
    from app import tls

    for step in tls.issue(args.dir, args.name, renew=args.renew):
        print(f"OK     {step}")
    return 0


def _lang_option() -> argparse.ArgumentParser:
    """`--lang` vor oder nach dem Befehl (ohne Vorgabe: `AUTOMATO_LANG`, sonst Deutsch)."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--lang", choices=LANGS, default=argparse.SUPPRESS,
        help=L("Sprache der Ausgaben (Standard: AUTOMATO_LANG, sonst de)",
               "language of the output (default: AUTOMATO_LANG, else de)"),
    )  # fmt: skip
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _lang_option()
    parser = argparse.ArgumentParser(
        prog="automato", description=L("Automato-Auslöser (CLI)", "Automato trigger (CLI)"),
        parents=[common],
    )  # fmt: skip
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, de: str, en: str, parent=sub) -> argparse.ArgumentParser:
        return parent.add_parser(name, help=L(de, en), parents=[common])

    p = command("ensure-tenant", "Mandant idempotent anlegen", "create a tenant idempotently")
    p.add_argument("--name", required=True)
    p.add_argument("--code", default=None, help=L("GG-Kuerzel (optional)", "GG code (optional)"))
    p.add_argument("--ipam", choices=[e.value for e in IpamType], default="netbox")
    p.add_argument("--dns", choices=[e.value for e in DnsType], default="active_directory")
    p.set_defaults(func=_cmd_ensure_tenant)

    p = command("ensure-source", "Quellsystem eines Mandanten idempotent anlegen",
                "create a source system of a tenant idempotently")  # fmt: skip
    p.add_argument("--tenant", required=True, help=L("Mandantenname", "tenant name"))
    p.add_argument("--name", required=True)
    p.add_argument("--platform", choices=[e.value for e in Platform], default="fortigate")
    p.add_argument("--base-url", default="")
    p.set_defaults(func=_cmd_ensure_source)

    p = command("collect", "Collect-Lauf in die Queue stellen", "queue a collect run")
    p.add_argument("--platform", choices=[e.value for e in Platform], default="fortigate")
    p.add_argument("--tenant", help=L("Mandantenname (alternativ --tenant-id)",
                                      "tenant name (or --tenant-id)"))  # fmt: skip
    p.add_argument("--tenant-id", type=int)
    p.add_argument("--source", help=L("Quellsystemname (alternativ --source-system-id)",
                                      "source system name (or --source-system-id)"))  # fmt: skip
    p.add_argument("--source-system-id", type=int)
    p.add_argument(
        "--execution",
        choices=["inprocess", "external"],
        default="inprocess",
        help=L("inprocess = zentraler Worker (Default), external = Agent holt den Lauf (ADR 0008)",
               "inprocess = central worker (default), external = an agent fetches the run "
               "(ADR 0008)"),
    )  # fmt: skip
    p.set_defaults(func=_cmd_collect)

    p = command("status", "Status eines Laufs anzeigen", "show the status of a run")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=_cmd_status)

    p = command("agent-package", "Agent-Paket signieren (Ed25519, offline)",
                "sign the agent package (Ed25519, offline)")  # fmt: skip
    pkg = p.add_subparsers(dest="pkg_command", required=True)
    q = command("manifest", "Nachricht zum Signieren ausgeben", "print the message to sign", pkg)
    q.add_argument("--out", help=L("in Datei schreiben statt auf stdout",
                                   "write to a file instead of stdout"))  # fmt: skip
    q.set_defaults(func=_cmd_pkg_manifest)
    q = command("install-signature", "offline erstellte Signatur einspielen",
                "install a signature created offline", pkg)  # fmt: skip
    q.add_argument(
        "file",
        help=L(
            "Signaturdatei (JSON von scripts/agent_signing.py sign)",
            "signature file (JSON from scripts/agent_signing.py sign)",
        ),
    )
    q.set_defaults(func=_cmd_pkg_install_signature)

    p = command("lab", "Model-2-Happy-Path (NetBox+FortiGate) in einem Schritt",
                "model 2 happy path (NetBox+FortiGate) in one step")  # fmt: skip
    p.add_argument("--tenant", default="lab")
    p.add_argument("--source", default="fw-01")
    p.add_argument(
        "--base-url",
        default="",
        help=L(
            "FortiGate-URL (Doku; Creds liest der Collector)",
            "FortiGate URL (documentation; the collector reads the credentials)",
        ),
    )
    p.set_defaults(func=_cmd_lab)

    p = command("check-connections", "Verbindungen aus dem Container pruefen",
                "check the connections from the container")  # fmt: skip
    p.add_argument("--json", action="store_true",
                   help=L("Ergebnis als JSON (deploy/aufbau.sh)",
                          "result as JSON (deploy/aufbau.sh)"))  # fmt: skip
    p.set_defaults(func=_cmd_check_connections)

    p = command("db-roles", "App-Rolle ohne DDL einrichten (als Eigentuemer)",
                "set up the app role without DDL (as owner)")  # fmt: skip
    p.add_argument("--role", help=L("Rollenname (Standard AUTOMATO_DB_APP_ROLE)",
                                    "role name (default AUTOMATO_DB_APP_ROLE)"))  # fmt: skip
    p.set_defaults(func=_cmd_db_roles)

    p = command("tls-init", "interne CA und Server-Zertifikate anlegen/erneuern",
                "create/renew the internal CA and server certificates")  # fmt: skip
    p.add_argument(
        "--dir",
        required=True,
        help=L(
            "Ablage (deploy/tls/pki im Container)", "location (deploy/tls/pki in the container)"
        ),
    )
    p.add_argument("--name", action="append", default=[],
                   help=L("weitere Namen/IPs fuer Orchestrator und Web-UI (VM)",
                          "further names/IPs for orchestrator and web UI (VM)"))  # fmt: skip
    p.add_argument(
        "--renew",
        action="store_true",
        help=L("Server-Zertifikate neu ausstellen", "reissue the server certificates"),
    )
    p.set_defaults(func=_cmd_tls_init)

    p = command("purge", "alte Laeufe, Sync-Vorschauen und Audit nach Frist loeschen",
                "delete old runs, sync previews and audit after the retention period")  # fmt: skip
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=L("nur zaehlen, nichts loeschen", "only count, delete nothing"),
    )
    p.set_defaults(func=_cmd_purge)

    return parser


def language(argv: list[str] | None) -> str:
    """Sprache des Aufrufs: `--lang` (irgendwo), sonst `AUTOMATO_LANG`, sonst Deutsch."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", default=None)
    known, _ = pre.parse_known_args(argv)
    for code in (known.lang, os.environ.get("AUTOMATO_LANG")):
        code = (code or "").strip().lower().split("-")[0]
        if code in LANGS:
            return code
    return "de"


def main(argv: list[str] | None = None) -> int:
    with use(language(argv)):
        args = build_parser().parse_args(argv)
        session = get_sessionmaker()()
        try:
            return args.func(session, args)
        finally:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
