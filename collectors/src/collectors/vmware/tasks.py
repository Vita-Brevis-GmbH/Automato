"""VMware-vCenter-Collector-Task (ADR 0017, read-only).

Liest VMs, Hosts, Cluster und Portgruppen per pyVmomi, seit 1.8.0 auch die Hosts mit Hardware,
vmnics und den Nachbarn aus CDP/LLDP (Hinweise im Lauf, wo das nicht ging). Zugangsdaten vom
Orchestrator fuer das Geraet des Laufs (`Target`: Adresse, Benutzer/Passwort; ADR 0009). Ohne
Zugangsdaten liest nur der zentrale Worker im Labor die Beispieldaten (`samples_allowed`, 1.6.0)
- ein Agent und der Betrieb nie.
"""

import json
from importlib import resources

from collectors.celery_app import app
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, Target, VirtualInventory
from collectors.common.i18n import L, german_only
from collectors.common.upload import confirmed, submit_result
from collectors.config import samples_allowed
from collectors.vmware.client import NOT_QUERIED, VCenterClient
from collectors.vmware.normalize import normalize


def _sample() -> dict:
    return json.loads(
        resources.files("collectors.vmware").joinpath("sample_vcenter.json").read_text()
    )


def _client(target: Target | None) -> VCenterClient | None:
    if target is not None and target.base_url and target.username and target.password:
        return VCenterClient(
            target.base_url, target.username, target.password, verify_tls=target.verify_tls
        )
    return None


def _no_credentials(target: Target | None) -> str:
    name = (
        L(f"„{target.name}“", f"“{target.name}”")
        if target is not None and target.name
        else L("das vCenter", "the vCenter")
    )
    return L(
        f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen",
        f"no credentials for {name} - store them at the source system in the web UI",
    )


def gather(target: Target | None = None) -> dict:
    """vCenter-Objekte als einfache Dicts - echtes vCenter oder (nur im Labor) Beispieldaten."""
    client = _client(target)
    if client is not None:
        with client:
            return client.gather()
    if not samples_allowed():  # Agent bzw. Betrieb (1.6.0): nie Beispieldaten
        raise RuntimeError(_no_credentials(target))
    return _sample()


def check(target: Target | None = None) -> tuple[bool, str]:
    """Anmeldung und Leserecht im Inventar pruefen (read-only, kein Ingest)."""
    client = _client(target)
    if client is None:
        return False, _no_credentials(target) if target else L(
            "keine vCenter-Creds", "no vCenter credentials"
        )
    try:
        with client:
            now = client.ping()
            vms = client.probe()  # wie der Lauf: ohne Leserecht hier NoPermission
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        return False, diagnose.device_error(exc, "vmware", target, local=True)
    if not vms:
        return False, L(
            f"angemeldet an {client.host}, aber keine VM sichtbar - die Rolle Read-only des "
            "Kontos reicht nicht bis zu den VMs (an untergeordnete Objekte weitergeben?)",
            f"logged in to {client.host}, but no VM visible - the account's role Read-only does "
            "not reach the VMs (propagate to child objects?)",
        )
    return True, L(
        f"erreichbar: {client.host} (Zeit {now}), {vms} VMs sichtbar",
        f"reachable: {client.host} (time {now}), {vms} VMs visible",
    )


def summary(data: dict, virtual: VirtualInventory) -> str | None:
    """Meldung des Laufs (1.8.0): Hosts, vmnics mit Nachbarn und je Host, dessen CDP/LLDP nicht
    lesbar war, der Grund (Recht, getrennt, Zeitueberschreitung) - der Lauf gilt trotzdem."""
    hosts = data.get("HostSystem") or []
    if not hosts:
        return None
    heard = sum(1 for h in virtual.hosts or [] for p in h.pnics if p.neighbor is not None)
    parts = [
        L(
            f"ESXi-Hosts: {len(hosts)} gelesen, {heard} vmnics mit Nachbarn (CDP/LLDP)",
            f"ESXi hosts: {len(hosts)} read, {heard} vmnics with neighbors (CDP/LLDP)",
        )
    ]
    notes = [
        L(
            f"{h.get('name')}: CDP/LLDP nicht lesbar ({h['neighbors_error']})",
            f"{h.get('name')}: CDP/LLDP not readable ({h['neighbors_error']})",
        )
        for h in hosts
        if h.get("neighbors_error") and h["neighbors_error"] != NOT_QUERIED
    ][:20]
    skipped = sum(1 for h in hosts if h.get("neighbors_error") == NOT_QUERIED)
    if skipped:
        notes.append(
            L(
                f"CDP/LLDP von {skipped} weiteren Hosts nicht abgefragt (vCenter antwortet nicht)",
                f"CDP/LLDP of {skipped} further hosts not queried (vCenter does not respond)",
            )
        )
    if notes:
        parts.append(L("Hinweise: ", "Notes: ") + "; ".join(notes))
    return " · ".join(parts)


@german_only  # Ergebnis und Meldung speichert der Orchestrator - deutsch (ADR 0028)
def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery (Worker *und* externer Standalone-Collector)."""
    try:
        data = gather(target)
        virtual = normalize(data)
        result = CollectorResult(
            status="completed", virtual=virtual, message=summary(data, virtual)
        )
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        result = CollectorResult(status="failed", message=diagnose.device_error(exc, "vmware"))
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


@app.task(name="collectors.vmware.collect")
def collect(run_id: str) -> None:
    agent_api.central(run_id, collect_once)
