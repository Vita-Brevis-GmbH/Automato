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
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_sessionmaker
from app.jobs import celery_app
from app.models import CollectorRun, DnsType, IpamType, Platform, SourceSystem, Tenant
from app.store import agent_package


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
        print("FEHLER: Mandant nicht gefunden (erst 'ensure-tenant')", file=sys.stderr)
        return 2
    src = ensure_source(session, tenant, args.name, args.platform, args.base_url)
    session.commit()
    print(f"source id={src.id} tenant={tenant.id} name={src.name} platform={src.platform}")
    return 0


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
        print("FEHLER: Mandant nicht gefunden (erst 'ensure-tenant')", file=sys.stderr)
        return 2
    if (args.source or args.source_system_id) and source is None:
        print("FEHLER: Quellsystem nicht gefunden (erst 'ensure-source')", file=sys.stderr)
        return 2
    if source is not None and tenant is not None and source.tenant_id != tenant.id:
        print("FEHLER: Quellsystem gehoert zu einem anderen Mandanten", file=sys.stderr)
        return 2
    run = queue_collect(session, args.platform, tenant, source, args.execution)
    print(f"run_id={run.id} status={run.status} -> 'automato status --run-id {run.id}'")
    if args.execution == "external":
        print("Externer Lauf: wartet auf einen Agenten ('automato-collector --poll').")
    return 0


def _cmd_status(session: Session, args: argparse.Namespace) -> int:
    run = session.get(CollectorRun, args.run_id)
    if run is None:
        print("FEHLER: Run nicht gefunden", file=sys.stderr)
        return 2
    print(
        f"run_id={run.id} status={run.status} rules={run.rule_count} "
        f"tenant={run.tenant_id} source={run.source_system_id} message={run.message or '-'}"
    )
    return 0


def _cmd_lab(session: Session, args: argparse.Namespace) -> int:
    """Model-2-Happy-Path: NetBox-Mandant + FortiGate-Quelle + ein Collect-Lauf."""
    tenant = ensure_tenant(session, args.tenant, "netbox", "active_directory")
    source = ensure_source(session, tenant, args.source, "fortigate", args.base_url)
    session.flush()
    run = queue_collect(session, "fortigate", tenant, source)
    print(
        f"Lab-Lauf gestartet: tenant={tenant.id} source={source.id} run_id={run.id}\n"
        f"Status abfragen: automato status --run-id {run.id}"
    )
    return 0


def _cmd_pkg_manifest(_session: Session, args: argparse.Namespace) -> int:
    """Nachricht zum Signieren des aktuellen Agent-Pakets (offline: scripts/agent_signing.py)."""
    info = agent_package.manifest(get_settings().agent_package_dir)
    if info is None:
        print("FEHLER: kein Agent-Paket im Orchestrator (agent_package_dir)", file=sys.stderr)
        return 2
    data = agent_package.signed_message(info)
    if args.out:
        Path(args.out).write_bytes(data)
        print(f"Nachricht fuer {info['version']} -> {args.out}")
    else:
        sys.stdout.write(data.decode())
    return 0


def _cmd_pkg_install_signature(_session: Session, args: argparse.Namespace) -> int:
    """Offline erstellte Signatur einspielen (prueft Version und - wenn hinterlegt - Schluessel)."""
    settings = get_settings()
    info = agent_package.manifest(settings.agent_package_dir)
    if info is None:
        print("FEHLER: kein Agent-Paket im Orchestrator (agent_package_dir)", file=sys.stderr)
        return 2
    try:
        sig = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FEHLER: Signaturdatei nicht lesbar: {exc}", file=sys.stderr)
        return 2
    if sig.get("version") != info["version"]:
        print(
            f"FEHLER: Signatur gilt fuer {sig.get('version')}, Paket ist {info['version']}",
            file=sys.stderr,
        )
        return 2
    if settings.agent_signing_pubkey:
        if not agent_package.verify(settings.agent_signing_pubkey, info, sig.get("signature", "")):
            print("FEHLER: Signatur passt nicht zum hinterlegten Schluessel", file=sys.stderr)
            return 2
        print("Signatur geprueft (AUTOMATO_AGENT_SIGNING_PUBKEY)")
    else:
        print("Hinweis: AUTOMATO_AGENT_SIGNING_PUBKEY fehlt - Signatur nicht geprueft")
    target = Path(settings.agent_signature_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": info["version"], "signature": sig["signature"]}))
    print(f"Signatur fuer {info['version']} eingespielt -> {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automato", description="Automato-Auslöser (CLI)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ensure-tenant", help="Mandant idempotent anlegen")
    p.add_argument("--name", required=True)
    p.add_argument("--code", default=None, help="GG-Kuerzel (optional)")
    p.add_argument("--ipam", choices=[e.value for e in IpamType], default="netbox")
    p.add_argument("--dns", choices=[e.value for e in DnsType], default="active_directory")
    p.set_defaults(func=_cmd_ensure_tenant)

    p = sub.add_parser("ensure-source", help="Quellsystem eines Mandanten idempotent anlegen")
    p.add_argument("--tenant", required=True, help="Mandantenname")
    p.add_argument("--name", required=True)
    p.add_argument("--platform", choices=[e.value for e in Platform], default="fortigate")
    p.add_argument("--base-url", default="")
    p.set_defaults(func=_cmd_ensure_source)

    p = sub.add_parser("collect", help="Collect-Lauf in die Queue stellen")
    p.add_argument("--platform", choices=[e.value for e in Platform], default="fortigate")
    p.add_argument("--tenant", help="Mandantenname (alternativ --tenant-id)")
    p.add_argument("--tenant-id", type=int)
    p.add_argument("--source", help="Quellsystemname (alternativ --source-system-id)")
    p.add_argument("--source-system-id", type=int)
    p.add_argument(
        "--execution",
        choices=["inprocess", "external"],
        default="inprocess",
        help="inprocess = zentraler Worker (Default), external = Agent holt den Lauf (ADR 0008)",
    )
    p.set_defaults(func=_cmd_collect)

    p = sub.add_parser("status", help="Status eines Laufs anzeigen")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("agent-package", help="Agent-Paket signieren (Ed25519, offline)")
    pkg = p.add_subparsers(dest="pkg_command", required=True)
    q = pkg.add_parser("manifest", help="Nachricht zum Signieren ausgeben")
    q.add_argument("--out", help="in Datei schreiben statt auf stdout")
    q.set_defaults(func=_cmd_pkg_manifest)
    q = pkg.add_parser("install-signature", help="offline erstellte Signatur einspielen")
    q.add_argument("file", help="Signaturdatei (JSON von scripts/agent_signing.py sign)")
    q.set_defaults(func=_cmd_pkg_install_signature)

    p = sub.add_parser("lab", help="Model-2-Happy-Path (NetBox+FortiGate) in einem Schritt")
    p.add_argument("--tenant", default="lab")
    p.add_argument("--source", default="fw-01")
    p.add_argument("--base-url", default="", help="FortiGate-URL (Doku; Creds liest der Collector)")
    p.set_defaults(func=_cmd_lab)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = get_sessionmaker()()
    try:
        return args.func(session, args)
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
