"""Cisco-ACI-Collector-Task (Model 1, read-only).

Liest den MO-Baum der ACI-Tenants per APIC-REST (Login + `rsp-subtree=full`) und bildet
Contracts auf Regeln ab - wie beim FortiGate-Collector. Zugangsdaten in dieser Reihenfolge:
1. vom Orchestrator fuer das Geraet des Laufs (`Target`: Adresse, Benutzer/Passwort,
   ACI-Tenants; ADR 0009),
2. lokal `AUTOMATO_ACI_URL`/`_USERNAME`/`_PASSWORD` (Rueckfall),
3. nur zentral/Lab: Beispiel-Export. Ein Agent im Kundennetz liest **nie** Beispieldaten -
   ohne Zugangsdaten schlaegt der Lauf mit klarer Meldung fehl.
"""

import json
from dataclasses import dataclass
from importlib import resources

from collectors.celery_app import app
from collectors.cisco_aci import routing as aci_routing
from collectors.cisco_aci.client import ApicClient
from collectors.cisco_aci.inventory import parse_inventory
from collectors.cisco_aci.mo import parse_epgs, parse_filters, parse_mo_export
from collectors.cisco_aci.normalize import filter_objects, normalize_contract
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, NormalizedEpg, Target
from collectors.common.upload import confirmed, submit_result
from collectors.config import get_settings


def fetch_contracts() -> list[dict]:
    """Legacy-Beispiel (bereits vereinfachte Contract-Dicts) - Rueckwaertskompatibilitaet."""
    sample = resources.files("collectors.cisco_aci").joinpath("sample_contracts.json")
    return json.loads(sample.read_text())


def fetch_mo_export() -> dict:
    """Beispiel-MO-Export (Tenant `TE_share`) fuer Lab/Tests ohne APIC."""
    sample = resources.files("collectors.cisco_aci").joinpath("sample_apic_export.json")
    return json.loads(sample.read_text())


def fetch_fabric_sample() -> dict:
    """Beispiel-Fabric (Knoten, Access Policies, Ports, LLDP/CDP) fuer Lab/Tests."""
    sample = resources.files("collectors.cisco_aci").joinpath("sample_apic_fabric.json")
    return json.loads(sample.read_text())


@dataclass(frozen=True)
class _Apic:
    url: str
    username: str
    password: str
    verify_tls: bool
    tenants: str

    def client(self) -> ApicClient:
        return ApicClient(self.url, self.username, self.password, verify_tls=self.verify_tls)


def _credentials(target: Target | None) -> _Apic | None:
    """APIC-Zugang - vom Geraet (Orchestrator) vor lokaler Konfiguration."""
    if target is not None and target.base_url and target.username and target.password:
        return _Apic(
            target.base_url, target.username, target.password, target.verify_tls, target.read_scope
        )
    s = get_settings()
    if s.aci_url and s.aci_username and s.aci_password:
        return _Apic(s.aci_url, s.aci_username, s.aci_password, s.aci_verify_tls, s.aci_tenants)
    return None


def _no_credentials(target: Target | None) -> str:
    name = f"„{target.name}“" if target is not None and target.name else "den APIC"
    return f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen"


def gather(target: Target | None = None) -> dict:
    """Tenant-Baum + Fabric-Klassen + Routing der Border-Leafs (ADR 0019) - echter APIC oder
    (nur zentral) Beispieldaten."""
    apic = _credentials(target)
    if apic is not None:
        with apic.client() as client:
            export = client.export(apic.tenants)
            contexts = aci_routing.border_contexts(aci_routing.parse_l3outs(export))
            return {
                "tenants": export,
                "fabric": client.fabric(),
                "border": client.border_routing(contexts),
            }
    if get_settings().agent_token:
        # Agent im Kundennetz: nie Beispieldaten in einen echten Mandanten schreiben
        raise RuntimeError(_no_credentials(target))
    return {"tenants": fetch_mo_export(), "fabric": fetch_fabric_sample()}


def check(target: Target | None = None) -> tuple[bool, str]:
    """Erreichbarkeit/Login am APIC pruefen (read-only, kein Ingest)."""
    apic = _credentials(target)
    if apic is None:
        return False, _no_credentials(target) if target else "keine APIC-Creds"
    try:
        with apic.client() as client:
            count = client.ping()
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        return False, diagnose.device_error(exc, "cisco_aci", target, local=True)
    return True, f"erreichbar: {apic.url} ({count} Tenants sichtbar)"


def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery (Worker *und* externer Standalone-Collector)."""
    try:
        data = gather(target)
        export = data["tenants"]
        rules = [rule for c in parse_mo_export(export) for rule in normalize_contract(c)]
        epgs = [NormalizedEpg(**e) for e in parse_epgs(export)]
        inventory = parse_inventory(export, data.get("fabric"))
        objects = filter_objects(parse_filters(export))
        routing = aci_routing.normalize(export, data.get("fabric"), data.get("border"))
        result = CollectorResult(
            status="completed",
            rules=rules,
            epgs=epgs,
            inventory=inventory,
            objects=objects,
            routing=routing,
            message=_routing_message(routing),
        )
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        result = CollectorResult(status="failed", message=diagnose.device_error(exc, "cisco_aci"))
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


def _routing_message(routing) -> str | None:
    """Border-Leaf-Tabellen ohne Leserecht fuer die Lauf-Meldung (ADR 0019 Stufe 2)."""
    missing = [
        f"{c.node} {c.context}: {m}"
        for c in (routing.contexts if routing else [])
        for m in c.missing
    ]
    return f"Routing nicht lesbar: {'; '.join(missing)}" if missing else None


@app.task(name="collectors.cisco_aci.collect")
def collect(run_id: str) -> None:
    # Zentraler Worker: Zugangsdaten des Geraets vom Orchestrator (internes Token)
    collect_once(run_id, agent_api.run_target(run_id))
