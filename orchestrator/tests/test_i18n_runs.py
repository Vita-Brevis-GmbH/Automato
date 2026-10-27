"""Meldungen der Laeufe, Grund der Update-Pruefung und CLI auf Englisch (0.90.0, ADR 0028
Schritt 4). Gespeichert bleibt deutsch; uebersetzt wird beim Abruf (`store/run_text.py`).
Die englischen Teile, die der Collector erzeugt, stehen dort neben dem deutschen (`L()`) - der
Vergleich unten haelt beide Seiten gleich."""

import importlib
import sys
from pathlib import Path

import httpx
import pytest
from app import audit, cli, i18n
from app.i18n import use
from app.models import Agent, CollectorRun
from app.store import run_text, runs
from test_reference_sources import ADMIN, TOKEN, _agent, _system, _tenant

EN = {"Accept-Language": "en"}

KNOWN = [
    # Orchestrator: Abbruch, Ablauf, Einspielen, Zugangsdaten
    ("abgebrochen von alice", "cancelled by alice"),
    ("abgebrochen: kein Ergebnis nach 30 min - Agent beendet bzw. neu gestartet oder "
     "Ergebnis-Upload verloren; neu einlesen",
     "cancelled: no result after 30 min - agent stopped or restarted, or result upload lost; "
     "collect again"),
    ("abgebrochen: nach 24 h nicht abgeholt - Agent offline bzw. kein zentraler Worker",
     "cancelled: not picked up after 24 h - agent offline or no central worker"),
    ("Teil-Upload unvollstaendig (6000 von 6624)", "partial upload incomplete (6000 of 6624)"),
    ("Einspielen fehlgeschlagen: DeadlockDetected: deadlock detected",
     "Ingest failed: DeadlockDetected: deadlock detected"),
    ("Zugangsdaten nicht lesbar: Schlüssel passt nicht zu den gespeicherten Zugangsdaten",
     "Credentials not readable: key does not match the stored credentials"),
    # Collector: Zugangsdaten, Erreichbarkeit, Anmeldung mit Hinweis
    ("keine Zugangsdaten fuer „fw-01“ - im Web-UI beim Quellsystem hinterlegen",
     "no credentials for “fw-01” - store them at the source system in the web UI"),
    ("keine Zugangsdaten fuer das Geraet - im Web-UI beim Quellsystem hinterlegen",
     "no credentials for the device - store them at the source system in the web UI"),
    ("nicht erreichbar https://fw-01.example.ch/api/v2/monitor/system/status: ConnectTimeout "
     "timed out - keine Antwort: Ziel aus oder Weg gesperrt (Firewall/Netz)",
     "not reachable https://fw-01.example.ch/api/v2/monitor/system/status: ConnectTimeout "
     "timed out - no response: target off or path blocked (firewall/network)"),
    ("Anmeldung abgelehnt (HTTP 401 https://fw-01.example.ch/api). Geraet meldet: invalid "
     "token. Hinweis: API-Token im Web-UI neu setzen; Trusted Hosts des REST-API-Admins muessen "
     "den Agent-Rechner enthalten",
     "Login rejected (HTTP 401 https://fw-01.example.ch/api). Device reports: invalid token. "
     "Note: set the API token again in the web UI; the trusted hosts of the REST API admin must "
     "include the agent machine"),
    # Zusammengesetzt: Switches mit Teilfehler und Hinweisen
    ("Switches: 2 gelesen (IOS/IOS-XE, NX-OS) · nicht gelesen: 10.0.0.3: nicht erreichbar: "
     "Verbindung zu Port 22 abgelehnt (SSH aus bzw. Weg gesperrt) · Hinweise: 10.0.0.1: "
     "MAC-Tabelle: 120 Eintraege ueber 50000 nicht uebernommen",
     "Switches: 2 read (IOS/IOS-XE, NX-OS) · not read: 10.0.0.3: not reachable: connection to "
     "port 22 refused (SSH off or path blocked) · Notes: 10.0.0.1: MAC table: 120 entries "
     "above 50000 not taken over"),
    ("VDOM nicht lesbar: dmz (HTTP 403) - das Profil braucht Lesezugriff auf alle VDOMs; "
     "gelesen: root · Routing nicht lesbar: Routing-Tabelle IPv4 (HTTP 403); HA ha (HTTP 403)",
     "VDOM not readable: dmz (HTTP 403) - the profile needs read access to all VDOMs; read: "
     "root · Routing not readable: routing table IPv4 (HTTP 403); HA ha (HTTP 403)"),
    ("Avi 22.1.3 · Tenants admin · 3 Virtual Services, 2 VS VIPs, 4 Pools, 1 Pool Groups · "
     "6 Einträge (VIP × Port) · nicht lesbar: admin: pool (HTTP 403)",
     "Avi 22.1.3 · Tenants admin · 3 Virtual Services, 2 VS VIPs, 4 Pools, 1 Pool Groups · "
     "6 entries (VIP × port) · not readable: admin: pool (HTTP 403)"),
    ("1200 Referenzeintraege gelesen", "1200 reference entries read"),
    # Grund der Update-Pruefung (ASCII: Umlaute und Zeichen ausserhalb als „?“)
    ("Update 0.90.0 (Build ab12) nicht installiert: Signatur fehlt oder passt nicht zum "
     "hinterlegten Schluessel (Paket signieren, deploy/agent-signing)",
     "Update 0.90.0 (Build ab12) not installed: signature missing or does not match the stored "
     "key (sign the package, deploy/agent-signing)"),
    ("Selbst-Update fehlgeschlagen: nach der Installation fehlen 1 Datei(en), z. B. "
     "collectors/run.py - Virenschutz (Schutzverlauf) bzw. einen zweiten Agent-Prozess pruefen "
     "(zuletzt gute Pakete wieder installiert)",
     "Self-update failed: after the installation 1 file(s) are missing, e.g. collectors/run.py "
     "- check the antivirus (protection history) or a second agent process (last good packages "
     "reinstalled)"),
    ("Update-Pruefung nicht moeglich: RemoteProtocolError: Server disconnected "
     "http://orch:8000/internal/agent/update - spricht der Orchestrator HTTPS (TLS intern)? In "
     "agent.env AUTOMATO_ORCHESTRATOR_URL=https://? und AUTOMATO_ORCHESTRATOR_CA_BUNDLE=<Pfad zu "
     "automato-ca.crt> setzen (Web-UI: Collector-Agenten ? Einrichtung)",
     "Update check not possible: RemoteProtocolError: Server disconnected "
     "http://orch:8000/internal/agent/update - does the orchestrator speak HTTPS (internal TLS)? "
     "In agent.env set AUTOMATO_ORCHESTRATOR_URL=https://… and AUTOMATO_ORCHESTRATOR_CA_BUNDLE="
     "<path to automato-ca.crt> (web UI: Collector agents → Setup)"),
]  # fmt: skip


@pytest.mark.parametrize(("de", "en"), KNOWN)
def test_bekannte_meldungen_englisch(de, en):
    with use("en"):
        assert runs.show_message(de) == en
    assert runs.show_message(de) == de  # ohne Sprache: deutsch, unveraendert


def test_unbekannte_meldung_bleibt():
    with use("en"):
        assert runs.show_message("FortiGate antwortet nicht mit JSON") == (
            "FortiGate antwortet nicht mit JSON"
        )
        assert runs.show_message(None) is None and runs.show_message("") == ""
        # Text des Geraets bleibt, nur die bekannten Teile werden englisch
        assert runs.show_message("Einspielen fehlgeschlagen: Tabelle kaputt") == (
            "Ingest failed: Tabelle kaputt"
        )


def _collectors():
    """Collector-Pakete aus diesem Quellbaum (gleicher Stand wie der Orchestrator)."""
    src = Path(__file__).resolve().parents[2] / "collectors" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return pytest.importorskip("collectors.common.i18n")


def _http_error(url: str, status: int, **kw) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status, request=request, **kw)
    return httpx.HTTPStatusError("x", request=request, response=response)


def _fault(name: str, **attrs) -> Exception:
    return type(name, (Exception,), attrs)()


def test_collector_englisch_gleich_wie_tabelle(monkeypatch):
    """Was der Collector deutsch meldet, uebersetzt der Orchestrator genau so, wie es der
    Collector selbst englisch schreibt (Konsole) - je Meldung beide Fassungen erzeugen."""
    ci18n = _collectors()
    diagnose = importlib.import_module("collectors.common.diagnose")
    http = importlib.import_module("collectors.common.http")
    dto = importlib.import_module("collectors.common.dto")
    monkeypatch.setattr(diagnose, "get_settings", lambda: type("S", (), {"orchestrator_url": ""}))
    request = httpx.Request("GET", "https://dev.example.ch/api")
    target = dto.Target(name="dev-01")
    makers = [
        lambda: http.describe_error(httpx.ConnectTimeout("timed out", request=request)),
        lambda: http.describe_error(httpx.ConnectError("refused", request=request)),
        lambda: http.describe_error(
            httpx.RemoteProtocolError("closed", request=httpx.Request("GET", "http://o:8000/x"))
        ),
        lambda: diagnose.device_error(_fault("AuthenticationException"), "cisco_switch"),
        lambda: diagnose.device_error(_fault("SessionLoginFailed"), "cisco_switch"),
        lambda: diagnose.device_error(_fault("NoValidConnectionsError"), "cisco_switch"),
        lambda: diagnose.device_error(_fault("InvalidLogin"), "vmware"),
        lambda: diagnose.device_error(_fault("NoPermission"), "vmware"),
        lambda: diagnose.device_error(
            _fault(
                "NoPermission",
                object=type("Folder", (), {"_moId": "group-d1"})(),
                privilegeId="System.View",
            ),
            "vmware",
        ),  # fmt: skip
    ]
    for platform in ("avi", "vmware", "cisco_aci", "infoblox", "fortigate", "netbox"):
        makers.append(
            lambda p=platform: diagnose.device_error(
                _http_error("https://dev.example.ch/api", 401, json={"detail": "denied"}), p
            )
        )
    makers.append(
        lambda: diagnose.device_error(
            _http_error("https://dev.example.ch/api", 400, json={"error": "bad version"}), "avi"
        )
    )
    tasks = {
        "fortigate": "collectors.fortigate.tasks",
        "cisco_aci": "collectors.cisco_aci.tasks",
        "vmware": "collectors.vmware.tasks",
        "avi": "collectors.avi.tasks",
        "ipam": "collectors.ipam.tasks",
    }
    for module in tasks.values():
        mod = importlib.import_module(module)
        makers.append(lambda m=mod: m._no_credentials(target))
        makers.append(lambda m=mod: m._no_credentials(None))
    ipam = importlib.import_module("collectors.ipam.tasks")
    for platform, tgt in (("netbox", dto.Target(name="nb", base_url="http://nb")),
                          ("infoblox", None)):  # fmt: skip

        def adapter_error(p=platform, t=tgt):
            try:
                ipam._adapter(p, t)
            except RuntimeError as exc:
                return str(exc)
            raise AssertionError("kein Fehler")

        makers.append(adapter_error)
    try:  # Switches: paramiko/ntc-templates sind nur mit dem Collector installiert
        switch = importlib.import_module("collectors.cisco_switch.tasks")
    except ImportError:
        switch = None
    if switch is not None:
        makers += [lambda: switch._no_credentials(target), switch._no_hosts]
    for make in makers:
        with ci18n.use("de"):
            de = make()
        with ci18n.use("en"):
            en = make()
        assert de != en, de
        assert run_text.translate(de) == en


def test_api_laeufe_englisch_gespeichert_deutsch(client, session_factory):
    tid = _tenant(client)
    fw = _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    cancelled = client.post(f"/jobs/{run_id}/cancel", headers={**operator, **EN}).json()
    assert cancelled["message"] == "cancelled by dev"
    assert client.get(f"/jobs/{run_id}", headers=ADMIN).json()["message"] == "abgebrochen von dev"
    listed = client.get("/jobs", params={"tenant_id": tid}, headers={**ADMIN, **EN}).json()
    assert listed[0]["message"] == "cancelled by dev"
    s = session_factory()
    assert s.get(CollectorRun, run_id).message == "abgebrochen von dev"  # gespeichert: deutsch
    s.close()


def test_quittung_an_englischen_agenten_gespeichert_deutsch(client, session_factory):
    tid = _tenant(client)
    fw = _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    message = "keine Zugangsdaten fuer „fw-01“ - im Web-UI beim Quellsystem hinterlegen"
    ack = client.post(f"/internal/collector-runs/{run_id}/result", headers={**TOKEN, **EN},
                      json={"status": "failed", "message": message}).json()  # fmt: skip
    assert ack["message"] == message  # gespeicherte Meldung (deutsch)
    assert (
        ack["shown"] == "no credentials for “fw-01” - store them at the source system in the web UI"
    )
    s = session_factory()
    assert s.get(CollectorRun, run_id).message == message
    s.close()
    # deutscher Agent: Quittung wie bisher, ohne `shown`
    again = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN,
                        json={"status": "failed", "message": message}).json()  # fmt: skip
    assert "shown" not in again and again["message"] == message
    # Betriebszustand: letzter Lauf fehlgeschlagen, Meldung uebersetzt
    checks = client.get("/system/health", params={"tenant_id": tid}, headers={**ADMIN, **EN})
    detail = {c["key"]: c for c in checks.json()["checks"]}[f"source:{fw['id']}"]["detail"]
    assert detail.startswith("no credentials for “fw-01”")


def test_grund_der_update_pruefung_englisch(client, session_factory):
    tid = _tenant(client)
    agent_id, _ = _agent(client, tid)
    note = "Selbst-Update auf der Station abgeschaltet (AUTOMATO_AGENT_AUTO_UPDATE=false)"
    s = session_factory()
    s.get(Agent, agent_id).update_note = note
    s.commit()
    s.close()
    agents = {a["id"]: a for a in client.get("/agents", headers={**ADMIN, **EN}).json()}
    assert agents[agent_id]["update_note"] == (
        "Self-update switched off on the station (AUTOMATO_AGENT_AUTO_UPDATE=false)"
    )
    de = {a["id"]: a for a in client.get("/agents", headers=ADMIN).json()}
    assert de[agent_id]["update_note"] == note


def test_alle_einlesen_grund_ohne_lese_token_englisch(client):
    tid = _tenant(client)
    _system(client, tid, name="nb", platform="netbox", base_url="http://nb")  # ohne Token
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    en = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers={**operator, **EN})
    de = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator)
    assert (
        en.json()["items"][0]["skipped"]
        == "netbox source 'nb' has no read token ('API token (read)')"
    )
    assert de.json()["items"][0]["skipped"] == (
        "netbox-Quelle „nb“ hat kein Lese-Token („API-Token (lesen)“)"
    )


def _cli(monkeypatch, session_factory, *argv) -> int:
    monkeypatch.setattr(cli, "get_sessionmaker", lambda: session_factory)
    return cli.main(list(argv))


def test_cli_status_mit_lang_en(session_factory, monkeypatch, capsys):
    s = session_factory()
    run = CollectorRun(platform="fortigate", execution="inprocess", message="abgebrochen von bob")
    s.add(run)
    s.commit()
    run_id = run.id
    s.close()
    assert _cli(monkeypatch, session_factory, "--lang", "en", "status", "--run-id", run_id) == 0
    assert "message=cancelled by bob" in capsys.readouterr().out
    assert _cli(monkeypatch, session_factory, "status", "--run-id", run_id) == 0
    assert "message=abgebrochen von bob" in capsys.readouterr().out  # Standard deutsch
    # auch nach dem Befehl und ueber AUTOMATO_LANG
    assert _cli(monkeypatch, session_factory, "status", "--run-id", "fehlt", "--lang", "en") == 2
    assert capsys.readouterr().err.strip() == "ERROR: run not found"
    monkeypatch.setenv("AUTOMATO_LANG", "en")
    assert _cli(monkeypatch, session_factory, "status", "--run-id", "fehlt") == 2
    assert capsys.readouterr().err.strip() == "ERROR: run not found"
    assert _cli(monkeypatch, session_factory, "--lang", "de", "status", "--run-id", "fehlt") == 2
    assert capsys.readouterr().err.strip() == "FEHLER: Run nicht gefunden"


def test_cli_hilfe_englisch_und_speichern_deutsch(session_factory, monkeypatch, capsys):
    with use("en"):
        assert "queue a collect run" in cli.build_parser().format_help()
    assert "Collect-Lauf in die Queue stellen" in cli.build_parser().format_help()
    seen = []
    record = audit.record

    def spy(session, **kw):
        seen.append(i18n.lang())
        return record(session, **kw)

    monkeypatch.setattr(audit, "record", spy)
    monkeypatch.setattr(cli.celery_app, "dispatch_collect", lambda platform, run_id: None)
    assert _cli(monkeypatch, session_factory, "--lang", "en", "lab") == 0
    out = capsys.readouterr().out
    assert out.startswith("Lab run started: tenant=")
    assert seen == ["de"]  # Lauf angelegt und auditiert: deutsch (@german_only)
    assert _cli(monkeypatch, session_factory, "--lang", "en", "purge", "--dry-run") == 0
    assert capsys.readouterr().out.startswith("due: 0 runs, 0 NetBox sync previews")
