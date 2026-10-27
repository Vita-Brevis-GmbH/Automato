"""FortiGate-Collector-Task.

Liest die Regeln per FortiOS-REST-API (read-only). Zugangsdaten in dieser Reihenfolge:
1. vom Orchestrator fuer das Geraet des Laufs (`Target`, ADR 0009),
2. lokal `AUTOMATO_FORTIGATE_URL`/`_TOKEN` (Rueckfall, z. B. alte agent.env),
3. nur zentral/Lab: Beispieldaten. Ein Agent im Kundennetz liest **nie** Beispieldaten -
   ohne Zugangsdaten schlaegt der Lauf mit klarer Meldung fehl.
Ergebnis geht per HTTP an die interne Orchestrator-API (kein DB-Zugriff).
"""

import json
from importlib import resources

import httpx

from collectors.celery_app import app
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, Target
from collectors.common.i18n import L, german_only
from collectors.common.upload import confirmed, submit_result
from collectors.config import get_settings
from collectors.fortigate import normalize as norm
from collectors.fortigate import routing
from collectors.fortigate.client import FortiGateClient


def _sample(name: str) -> list | dict:
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def _credentials(target: Target | None) -> tuple[str, str, bool] | None:
    """(URL, Token, verify_tls) - vom Geraet (Orchestrator) vor lokaler Konfiguration."""
    if target is not None and target.base_url and target.token:
        return target.base_url, target.token, target.verify_tls
    settings = get_settings()
    if settings.fortigate_url and settings.fortigate_token:
        return settings.fortigate_url, settings.fortigate_token, settings.fortigate_verify_tls
    return None


def _no_credentials(target: Target | None) -> str:
    name = (
        L(f"„{target.name}“", f"“{target.name}”")
        if target is not None and target.name
        else L("das Geraet", "the device")
    )
    return L(
        f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen",
        f"no credentials for {name} - store them at the source system in the web UI",
    )


def gather(target: Target | None = None) -> dict:
    """FortiGate-Rohdaten (IPv4+IPv6, NAT) - echtes Geraet je VDOM (`vdoms`) oder (nur zentral)
    Beispieldaten als ein VDOM ohne Namen (flach, wie aeltere Clients)."""
    creds = _credentials(target)
    if creds is not None:
        url, token, verify = creds
        return FortiGateClient(url, token, verify_tls=verify).gather()
    if get_settings().agent_token:
        # Agent im Kundennetz: nie Beispieldaten in einen echten Mandanten schreiben
        raise RuntimeError(_no_credentials(target))
    return {
        "policies": _sample("sample_policies.json"),
        "addresses": _sample("sample_addresses.json"),
        "addresses6": _sample("sample_addresses6.json"),
        "groups": _sample("sample_addrgrps.json"),
        "groups6": _sample("sample_addrgrps6.json"),
        "services": _sample("sample_services.json"),
        "service_groups": _sample("sample_service_groups.json"),
        "vips": _sample("sample_vips.json"),
        "ippools": _sample("sample_ippools.json"),
        "interfaces": _sample("sample_interfaces.json"),
        "zones": _sample("sample_zones.json"),
        "system": _sample("sample_system.json"),
    }


def check(target: Target | None = None) -> tuple[bool, str]:
    """Erreichbarkeit/Auth des Zielsystems pruefen (read-only, kein Ingest)."""
    creds = _credentials(target)
    if creds is None:
        return False, _no_credentials(target) if target else _no_creds()
    url, token, verify = creds
    try:
        FortiGateClient(url, token, verify_tls=verify).ping()
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        return False, diagnose.device_error(exc, "fortigate", target, local=True)
    return True, L(f"erreichbar: {url}", f"reachable: {url}")


def _no_creds() -> str:
    return L("keine FortiGate-Creds", "no FortiGate credentials")


# Bewusst ungueltig: darf von keiner FortiGate akzeptiert werden (R16)
_INVALID_TOKEN = "automato-r16-invalid-token"  # noqa: S105 - Testwert, kein Secret


def strict_check(target: Target | None = None) -> tuple[bool, str]:
    """R16 (opt-in, `--check --strict`): lehnt die FortiGate ein ungueltiges Token ab?

    Liefert sie auch damit Daten, prueft sie nur die Quell-IP (Trusted Hosts) - dann reicht
    eine kompromittierte Agent-Station fuer Lese-Zugriff. Achtung: der Versuch zaehlt als
    fehlgeschlagene Anmeldung (Sperre nach admin-lockout-threshold, FortiOS-Standard 3)."""
    creds = _credentials(target)
    if creds is None:
        return False, _no_credentials(target) if target else _no_creds()
    url, _token, verify = creds
    try:
        FortiGateClient(url, _INVALID_TOKEN, verify_tls=verify).ping()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return True, L("ungueltiges Token wird abgelehnt", "invalid token is rejected")
        code = exc.response.status_code
        return False, L(f"nicht pruefbar: HTTP {code}", f"not checkable: HTTP {code}")
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        why = f"{type(exc).__name__}: {exc}"
        return False, L(f"nicht pruefbar: {why}", f"not checkable: {why}")
    return False, L(
        "FortiGate liefert Daten auch mit UNGUELTIGEM Token (nur Trusted Hosts) - REST-API-"
        "Admin mit echtem Token-Zwang einrichten (Risiko R16)",
        "FortiGate returns data even with an INVALID token (trusted hosts only) - set up the "
        "REST API admin with a real token requirement (risk R16)",
    )


def _normalize_vdom(d: dict) -> tuple[list, list, list]:
    """Regeln, NAT und Objekte eines VDOMs, je Eintrag mit dem VDOM (ADR 0019)."""
    rules = norm.normalize(
        d["policies"],
        d["addresses"],
        d["groups"],
        d["services"],
        d["vips"],
        d["addresses6"],
        d["groups6"],
        d.get("service_groups", []),
        d.get("policies6", []),
        d.get("vips6", []),
        d.get("vipgrps", []),
        d.get("vipgrps6", []),
    )
    nat_rules = norm.normalize_nat(
        d["vips"], d.get("vips6", []), d.get("ldb_monitors", [])
    ) + norm.normalize_snat(d["ippools"], d.get("ippools6", []))
    objects = norm.build_device_objects(
        d["addresses"],
        d["groups"],
        d["addresses6"],
        d["groups6"],
        d["vips"],
        d["ippools"],
        d["services"],
        d.get("service_groups", []),
        d.get("vips6", []),
        d.get("vipgrps", []),
        d.get("vipgrps6", []),
        d.get("ippools6", []),
    )
    for item in (*rules, *nat_rules, *objects):
        item.vdom = d.get("vdom")
    return rules, nat_rules, objects


def _vdom_message(d: dict) -> str | None:
    """VDOMs des Laufs fuer die Lauf-Meldung; nicht lesbare zuerst (Profil pruefen), dazu
    Routing-Tabellen ohne Leserecht (ADR 0019 Stufe 2). Deutsch - der Orchestrator speichert
    die Meldung und uebersetzt beim Abruf (ADR 0028)."""
    read = [str(v.get("vdom") or "Standard") for v in d.get("vdoms") or []]
    skipped = [f"{s['vdom']} ({s['error']})" for s in d.get("skipped") or []]
    parts = []
    if skipped:
        parts.append(
            f"VDOM nicht lesbar: {', '.join(skipped)} - das Profil braucht Lesezugriff auf alle "
            f"VDOMs; gelesen: {', '.join(read) or '-'}"
        )
    elif len(read) > 1:
        parts.append(f"VDOMs: {', '.join(read)}")
    missing = [
        f"{v.get('vdom') or 'Standard'}: {m}" if len(read) > 1 else m
        for v in d.get("vdoms") or []
        for m in (v.get("routing") or {}).get("missing", [])
    ]
    missing += (d.get("ha") or {}).get("missing", [])
    if missing:
        parts.append(f"Routing nicht lesbar: {'; '.join(missing)}")
    return " · ".join(parts) or None


@german_only  # Ergebnis und Meldung speichert der Orchestrator - deutsch (ADR 0028)
def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery: gather -> normalize -> submit (HTTPS). Fuer den
    Celery-Worker *und* den externen Standalone-Collector (collectors.run)."""
    try:
        d = gather(target)
        rules, nat_rules, objects, zones = [], [], [], []
        for vdom in d.get("vdoms") or [d]:  # Beispieldaten bzw. aeltere Clients: flach
            r, n, o = _normalize_vdom(vdom)
            rules += r
            nat_rules += n
            objects += o
            zones += vdom.get("zones") or []
        name = target.name if target is not None and target.name else "fortigate"
        inventory = norm.normalize_inventory(d.get("interfaces"), zones, d.get("system"), name)
        node = (d.get("system") or {}).get("hostname") or name  # wie das Geraet im Inventar
        result = CollectorResult(
            status="completed",
            rules=rules,
            nat_rules=nat_rules,
            objects=objects,
            inventory=inventory,
            routing=routing.normalize(d.get("vdoms") or [], d.get("ha"), node, d.get("interfaces")),
            message=_vdom_message(d),
        )
    except Exception as exc:  # noqa: BLE001 - Fehler gehen als failed-Result zurueck
        result = CollectorResult(status="failed", message=diagnose.device_error(exc, "fortigate"))
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


@app.task(name="collectors.fortigate.collect")
def collect(run_id: str) -> None:
    # Zentraler Worker: Zugangsdaten des Geraets vom Orchestrator (internes Token)
    collect_once(run_id, agent_api.run_target(run_id))
