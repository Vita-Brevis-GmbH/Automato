"""Referenzquellen-Collector: Infoblox DDI und NetBox komplett lesen (read-only, ADR 0010).

Adresse und Zugangsdaten kommen vom Quellsystem (Orchestrator, ADR 0009): Infoblox mit
Benutzer/Passwort, NetBox mit dem Lese-Token. Die Eintraege gehen in Teilen an
`/internal/collector-runs/{id}/references`, danach das Ergebnis mit der Gesamtzahl. Die
Lese-Clients sind per `enforce_read_only` auf GET beschraenkt; Beispieldaten gibt es hier
nicht - ohne Zugangsdaten schlaegt der Lauf mit klarer Meldung fehl.
"""

from sot_gateway.adapters import InfobloxAdapter, NetBoxAdapter

from collectors.celery_app import app
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, ReferenceBatch, Target
from collectors.common.i18n import L, german_only
from collectors.common.upload import confirmed, submit_references, submit_result

CHUNK = 2000
PLATFORMS = ("infoblox", "netbox")


def _no_credentials(target: Target | None) -> str:
    name = _label(target)
    return L(
        f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen",
        f"no credentials for {name} - store them at the source system in the web UI",
    )


def _adapter(platform: str, target: Target | None):
    if target is None or not target.base_url:
        name = _label(target)
        raise RuntimeError(
            L(
                f"keine Adresse fuer {name} - im Web-UI beim Quellsystem hinterlegen",
                f"no address for {name} - store it at the source system in the web UI",
            )
        )
    if platform == "infoblox":
        if not (target.username and target.password):
            raise RuntimeError(_no_credentials(target))
        return InfobloxAdapter(
            target.base_url, target.username, target.password, verify_tls=target.verify_tls
        )
    if not target.token:
        name = _label(target)
        raise RuntimeError(
            L(
                f"kein Lese-Token fuer {name} - im Web-UI beim Quellsystem unter 'API-Token "
                "(lesen)' hinterlegen (das Schreib-Token wird nie zum Lesen verwendet)",
                f"no read token for {name} - store it at the source system in the web UI under "
                "'API token (read)' (the write token is never used for reading)",
            )
        )
    return NetBoxAdapter(target.base_url, target.token, verify_tls=target.verify_tls)


def _label(target: Target | None) -> str:
    if target is not None and target.name:
        return L(f"„{target.name}“", f"“{target.name}”")
    return L("die Referenzquelle", "the reference source")


@german_only  # Ergebnis und Meldung speichert der Orchestrator - deutsch (ADR 0028)
def collect_references(
    platform: str, run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    total = 0
    try:
        adapter = _adapter(platform, target)
        batch: list = []
        for record in adapter.export():
            batch.append(record)
            if len(batch) >= CHUNK:
                submit_references(run_id, ReferenceBatch(records=batch), via_agent=via_agent)
                total += len(batch)
                batch = []
        if batch:
            submit_references(run_id, ReferenceBatch(records=batch), via_agent=via_agent)
            total += len(batch)
        result = CollectorResult(
            status="completed", reference_count=total, message=f"{total} Referenzeintraege gelesen"
        )
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        result = CollectorResult(
            status="failed", reference_count=total, message=diagnose.device_error(exc, platform)
        )
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


def check(platform: str, target: Target | None = None) -> tuple[bool, str]:
    try:
        adapter = _adapter(platform, target)
        version = adapter.ping()
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        return False, diagnose.device_error(exc, platform, target, local=True)
    product = "NetBox" if platform == "netbox" else "WAPI"
    return True, L(
        f"erreichbar: {target.base_url} ({product} {version})",
        f"reachable: {target.base_url} ({product} {version})",
    )


def collect_infoblox(run_id: str, target: Target | None = None, via_agent: bool = False):
    return collect_references("infoblox", run_id, target, via_agent)


def collect_netbox(run_id: str, target: Target | None = None, via_agent: bool = False):
    return collect_references("netbox", run_id, target, via_agent)


def check_infoblox(target: Target | None = None) -> tuple[bool, str]:
    return check("infoblox", target)


def check_netbox(target: Target | None = None) -> tuple[bool, str]:
    return check("netbox", target)


@app.task(name="collectors.infoblox.collect")
def collect_infoblox_task(run_id: str) -> None:
    agent_api.central(run_id, collect_infoblox)


@app.task(name="collectors.netbox.collect")
def collect_netbox_task(run_id: str) -> None:
    agent_api.central(run_id, collect_netbox)
