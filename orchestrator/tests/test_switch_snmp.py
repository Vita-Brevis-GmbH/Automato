"""Switches & Router ueber SNMPv3 (ADR 0033): Zugang am Quellsystem, SNMPv3-Zugangsdaten nur
verschluesselt und nie in Antworten/Audit, nur authPriv, Wechsel des Zugangs, Ziel fuer den
Agenten, Pruefung vom Orchestrator (UDP), Ergebnis in Verkabelung und Endpunkten, Meldungen
englisch."""

import importlib
import sys
from pathlib import Path

import pytest
from app import credentials
from app.i18n import use
from app.models import AuditLog, SourceSystem
from app.store import reachability, run_text

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
AUTH, PRIV = "auth-geheim-1", "priv-geheim-2"  # noqa: S105 - Testwerte
SRV = "a0:b1:c2:d3:e4:f5"


def _tenant(client) -> int:
    return client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]


def _snmp_body(tid: int, **kw) -> dict:
    body = {"tenant_id": tid, "name": "Switches SNMP", "platform": "cisco_switch",
            "access": "snmp", "read_scope": "10.0.0.11\n10.0.0.12", "username": "ro-automato",
            "auth_password": AUTH, "priv_password": PRIV, "priv_protocol": "aes256",
            "context": "ctx-a"}  # fmt: skip
    return {**body, **kw}


def _secret_free(*texts) -> None:
    for text in texts:
        assert AUTH not in str(text) and PRIV not in str(text)


def test_zugangsdaten_snmp_verschluesselt_und_nie_ausgegeben(client, session_factory):
    tid = _tenant(client)
    r = client.post("/systems", json=_snmp_body(tid), headers=ADMIN)
    assert r.status_code == 201, r.text
    sw = r.json()
    assert (sw["access"], sw["base_url"], sw["has_credentials"]) == (
        "snmp", "snmp://10.0.0.11", True)  # fmt: skip
    assert sw["snmp"] == {"auth_protocol": "sha256", "priv_protocol": "aes256",
                          "has_context": True}  # Vorgabe SHA-256; nur Protokolle  # fmt: skip
    assert sw["host_keys"] == [] and "ro-automato" not in str(sw)
    listed = client.get("/systems", params={"tenant_id": tid}).json()
    _secret_free(sw, listed, client.get("/systems/labels", params={"tenant_id": tid}).json())

    s = session_factory()
    src = s.get(SourceSystem, sw["id"])
    _secret_free(src.credentials)  # nur der Fernet-Token
    assert credentials.decrypt(src.credentials) == {
        "username": "ro-automato", "auth_protocol": "sha256", "auth_password": AUTH,
        "priv_protocol": "aes256", "priv_password": PRIV, "context": "ctx-a"}  # fmt: skip
    _secret_free(*(a.detail for a in s.query(AuditLog)))
    created = s.query(AuditLog).filter_by(action="source.created").one().detail
    assert created["credentials_set"] is True and created["access"] == "snmp"
    s.close()

    # nur ein Passwort aendern: Rest bleibt; Protokoll aendern; Context entfernen
    r = client.patch(f"/systems/{sw['id']}", json={"priv_password": "neu-geheim-3",
                                                   "auth_protocol": "sha512", "context": ""},
                     headers=ADMIN)  # fmt: skip
    assert r.status_code == 200 and r.json()["has_credentials"] is True
    assert r.json()["snmp"] == {"auth_protocol": "sha512", "priv_protocol": "aes256",
                                "has_context": False}  # fmt: skip
    assert "neu-geheim-3" not in r.text
    # alles entfernen: auch Protokolle weg
    r = client.patch(f"/systems/{sw['id']}", json={"username": "", "auth_password": "",
                                                   "priv_password": ""}, headers=ADMIN)  # fmt: skip
    assert r.json()["has_credentials"] is False and r.json()["snmp"] is None
    s = session_factory()
    assert s.get(SourceSystem, sw["id"]).credentials is None
    s.close()


def test_nur_snmpv3_authpriv(client):
    tid = _tenant(client)
    bad = [
        {"priv_password": None},  # authNoPriv
        {"auth_password": None, "priv_password": None, "password": "x"},  # SSH-Feld
        {"auth_protocol": "md5"},
        {"priv_protocol": "des"},
        {"priv_password": "kurz"},  # weniger als 8 Zeichen
        {"auth_password": "1234567"},
    ]
    for i, change in enumerate(bad):
        body = {k: v for k, v in _snmp_body(tid, name=f"sw-{i}", **change).items()
                if v is not None}  # fmt: skip
        r = client.post("/systems", json=body, headers=ADMIN)
        assert r.status_code == 422, (change, r.text)
        _secret_free(r.text)
    fw = {"tenant_id": tid, "name": "fw", "platform": "fortigate", "access": "snmp"}
    assert client.post("/systems", json=fw, headers=ADMIN).status_code == 422
    # nur Protokolle ohne Benutzer/Passwoerter: keine Zugangsdaten
    body = {k: v for k, v in _snmp_body(tid, name="leer").items()
            if k not in ("username", "auth_password", "priv_password", "context")}  # fmt: skip
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201 and r.json()["has_credentials"] is False


def test_wechsel_des_zugangs_verwirft_zugangsdaten(client, session_factory):
    tid = _tenant(client)
    ssh = {"tenant_id": tid, "name": "Switches", "platform": "cisco_switch",
           "read_scope": "10.0.0.11", "username": "ro", "password": "ssh-pw"}  # fmt: skip
    sw = client.post("/systems", json=ssh, headers=ADMIN).json()
    assert (sw["access"], sw["base_url"], sw["has_credentials"]) == ("ssh", "ssh://10.0.0.11",
                                                                     True)  # fmt: skip
    r = client.patch(f"/systems/{sw['id']}", json={"access": "snmp"}, headers=ADMIN)
    got = r.json()
    assert (got["access"], got["base_url"], got["has_credentials"]) == (
        "snmp", "snmp://10.0.0.11", False)  # SSH-Konto gehoert zum anderen Zugang  # fmt: skip
    # SSH-Passwort passt nicht zu SNMPv3
    r = client.patch(f"/systems/{sw['id']}", json={"password": "x"}, headers=ADMIN)
    assert r.status_code == 422
    snmp = {"username": "ro-snmp", "auth_password": AUTH, "priv_password": PRIV}
    r = client.patch(f"/systems/{sw['id']}", json=snmp, headers=ADMIN)
    assert r.json()["has_credentials"] is True and r.json()["snmp"]["priv_protocol"] == "aes128"
    r = client.patch(f"/systems/{sw['id']}", json={"read_scope": "10.0.0.12:1161"}, headers=ADMIN)
    assert r.json()["base_url"] == "snmp://10.0.0.12:1161"
    r = client.patch(f"/systems/{sw['id']}", json={"access": "ssh"}, headers=ADMIN)
    back = r.json()
    assert (back["access"], back["has_credentials"], back["snmp"]) == ("ssh", False, None)
    s = session_factory()
    details = [a.detail for a in s.query(AuditLog).filter_by(action="source.updated")]
    assert [d.get("access") for d in details if "access" in d] == ["snmp", "ssh"]
    _secret_free(*details)
    s.close()


def _agent(client, tid: int) -> tuple[int, str]:
    a = client.post("/agents", json={"name": "agent-a", "tenant_id": tid}, headers=ADMIN).json()
    return a["id"], a["token"]


def _next_run(client, tid: int, sid: int, token: str) -> dict:
    body = {"platform": "cisco_switch", "tenant_id": tid, "source_system_id": sid}
    assert client.post("/jobs/collect", json=body, headers=ADMIN).status_code == 202
    run = client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).json()["run"]
    return run["target"]


def test_agent_bekommt_snmp_ziel_und_host_schluessel(client, session_factory):
    tid = _tenant(client)
    agent_id, token = _agent(client, tid)
    sw = client.post("/systems", json=_snmp_body(tid, agent_id=agent_id), headers=ADMIN).json()
    target = _next_run(client, tid, sw["id"], token)
    assert target["access"] == "snmp" and target["known_hosts"] == {}
    assert {k: target[k] for k in ("username", "auth_protocol", "auth_password", "priv_protocol",
                                   "priv_password", "context")} == {
        "username": "ro-automato", "auth_protocol": "sha256", "auth_password": AUTH,
        "priv_protocol": "aes256", "priv_password": PRIV, "context": "ctx-a"}  # fmt: skip
    assert target["password"] is None
    targets = client.get("/internal/agent/targets", headers={"X-Agent-Token": token}).json()
    assert targets[0]["priv_password"] == PRIV
    # die Agenten-Liste nennt nur „hinterlegt“
    agents = client.get("/agents", headers=ADMIN).json()
    assert agents[0]["systems"][0]["has_credentials"] is True
    _secret_free(agents)

    # SSH: festgehaltene Host-Schluessel gehen jetzt auch an den Agenten (vorher fehlten sie
    # im Ziel des Agenten - er lernte sie bei jedem Lauf neu)
    ssh = {"tenant_id": tid, "name": "Switches SSH", "platform": "cisco_switch",
           "read_scope": "10.0.0.21", "username": "ro", "password": "ssh-pw",
           "agent_id": agent_id}  # fmt: skip
    sw2 = client.post("/systems", json=ssh, headers=ADMIN).json()
    s = session_factory()
    s.get(SourceSystem, sw2["id"]).ssh_host_keys = {"10.0.0.21": "ssh-ed25519 AAAAPINNED"}
    s.commit()
    s.close()
    target = _next_run(client, tid, sw2["id"], token)
    assert target["known_hosts"] == {"10.0.0.21": "ssh-ed25519 AAAAPINNED"}
    assert target["access"] == "ssh" and "priv_password" not in target


def test_pruefung_vom_orchestrator_udp(client, monkeypatch):
    import socket

    monkeypatch.setattr(reachability.socket, "getaddrinfo",
                        lambda host, port, type=0: [(socket.AF_INET, type, 17, "",
                                                     ("10.0.0.11", port))])  # fmt: skip

    def no_tcp(*a, **kw):
        raise AssertionError("keine TCP-Probe fuer SNMP")

    monkeypatch.setattr(reachability.socket, "create_connection", no_tcp)
    result = reachability.check("snmp://10.0.0.11", "cisco_switch")
    assert result.ok and [s.step for s in result.steps] == ["dns"]
    assert "UDP 161" in result.hint and "-Check" in result.hint
    tid = _tenant(client)
    sw = client.post("/systems", json=_snmp_body(tid), headers=ADMIN).json()
    checked = client.post(f"/systems/{sw['id']}/check", headers=ADMIN).json()
    assert checked["ok"] is True and "UDP 161" in checked["hint"]
    _secret_free(checked)
    with use("en"):
        assert "runs over UDP 1161" in reachability.check("snmp://sw:1161", "x").hint


def _collectors():
    """Collector-Pakete aus diesem Quellbaum (gleicher Stand wie der Orchestrator)."""
    src = Path(__file__).resolve().parents[2] / "collectors" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return pytest.importorskip("collectors.snmp.normalize")


def _pl(*ports: int) -> bytes:
    out = bytearray(8)
    for p in ports:
        out[(p - 1) // 8] |= 0x80 >> ((p - 1) % 8)
    return bytes(out)


def _mac_index(mac: str) -> str:
    return ".".join(str(int(x, 16)) for x in mac.split(":"))


# Zwei Switches per SNMPv3: sw-a (Juniper EX) ge-0/0/47 <-> sw-b (Cisco) Gi1/0/48 per LLDP,
# Server an sw-a ge-0/0/1 (VLAN 20), seine IP im ARP von sw-b (Vl20)
SW_A = {
    "scalars": {"sysName": b"sw-a.example.ch", "sysObjectID": "1.3.6.1.4.1.2636.1.1.1.2.31",
                "sysDescr": b"Juniper Networks, Inc. ex4300-48t Ethernet Switch, kernel JUNOS "
                            b"21.4R3-S4.9"},
    "tables": {
        "ifName": {"501": b"ge-0/0/1", "547": b"ge-0/0/47"},
        "ifType": {"501": 6, "547": 6},
        "ifAdminStatus": {"501": 1, "547": 1}, "ifOperStatus": {"501": 1, "547": 1},
        "ifHighSpeed": {"501": 1000, "547": 10000},
        "dot3StatsDuplexStatus": {"501": 3, "547": 3},
        "dot1dBasePortIfIndex": {"1": 501, "47": 547},
        "dot1qVlanStaticName": {"1": b"default", "20": b"Clients"},
        "dot1qVlanStaticEgressPorts": {"1": _pl(47), "20": _pl(1, 47)},
        "dot1qVlanStaticUntaggedPorts": {"1": _pl(47), "20": _pl(1)},
        "dot1qPvid": {"1": 20, "47": 1},
        "dot1qTpFdbPort": {f"20.{_mac_index(SRV)}": 1},
        "dot1qTpFdbStatus": {f"20.{_mac_index(SRV)}": 3},
        "lldpLocPortIdSubtype": {"547": 5}, "lldpLocPortId": {"547": b"ge-0/0/47"},
        "lldpRemPortIdSubtype": {"0.547.1": 5}, "lldpRemPortId": {"0.547.1": b"Gi1/0/48"},
        "lldpRemSysName": {"0.547.1": b"sw-b"},
        "lldpRemSysCapEnabled": {"0.547.1": bytes([0x20, 0])},
    },
}  # fmt: skip
SW_B = {
    "scalars": {"sysName": b"sw-b", "sysObjectID": "1.3.6.1.4.1.9.1.2494",
                "sysDescr": b"Cisco IOS Software, Version 17.9.4a, RELEASE SOFTWARE"},
    "tables": {
        "ifName": {"48": b"Gi1/0/48", "200": b"Vl20"},
        "ifType": {"48": 6, "200": 53},
        "ifAdminStatus": {"48": 1, "200": 1}, "ifOperStatus": {"48": 1, "200": 1},
        "ifHighSpeed": {"48": 10000},
        "dot3StatsDuplexStatus": {"48": 3},
        "dot1dBasePortIfIndex": {"48": 48},
        "dot1qVlanStaticName": {"1": b"default", "20": b"Clients"},
        "dot1qVlanStaticEgressPorts": {"1": _pl(48), "20": _pl(48)},
        "dot1qVlanStaticUntaggedPorts": {"1": _pl(48)},
        "dot1qPvid": {"48": 1},
        "dot1qTpFdbPort": {f"20.{_mac_index(SRV)}": 48},
        "dot1qTpFdbStatus": {f"20.{_mac_index(SRV)}": 3},
        "ipNetToPhysicalPhysAddress": {"200.1.4.10.20.0.15": bytes.fromhex(SRV.replace(":", ""))},
        "ipNetToPhysicalType": {"200.1.4.10.20.0.15": 3},
        "lldpLocPortIdSubtype": {"48": 5}, "lldpLocPortId": {"48": b"Gi1/0/48"},
        "lldpRemPortIdSubtype": {"0.48.1": 5}, "lldpRemPortId": {"0.48.1": b"ge-0/0/47"},
        "lldpRemSysName": {"0.48.1": b"sw-a.example.ch"},
        "lldpRemSysCapEnabled": {"0.48.1": bytes([0x28, 0])},
    },
}  # fmt: skip


def test_ergebnis_landet_in_verkabelung_und_endpunkten(client):
    _collectors()
    normalize = importlib.import_module("collectors.cisco_switch.normalize")
    dto = importlib.import_module("collectors.common.dto")
    raw = {
        "switches": [
            {"host": "10.0.0.11", "platform": "snmp", "outputs": SW_A, "notes": []},
            {"host": "10.0.0.12", "platform": "snmp", "outputs": SW_B, "notes": []},
        ]
    }
    message = "Switches: 2 gelesen (SNMPv3: Cisco, Juniper)"
    result = dto.CollectorResult(
        status="completed", inventory=normalize.normalize(raw), message=message
    )
    tid = _tenant(client)
    sw = client.post("/systems", json=_snmp_body(tid), headers=ADMIN).json()
    body = {"platform": "cisco_switch", "tenant_id": tid, "source_system_id": sw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN,
                    json=result.model_dump(mode="json"))  # fmt: skip
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text

    dev = client.get("/cabling/devices", params={"tenant_id": tid}).json()
    assert dev["summary"]["switches"] == 2 and dev["summary"]["states"] == {
        "both": 1,
        "one_sided": 0,
        "unread": 0,
    }
    links = client.get("/cabling/links", params={"tenant_id": tid}).json()["items"]
    (link,) = links
    ends = {(link["a"]["device"], link["a"]["port"]), (link["b"]["device"], link["b"]["port"])}
    assert ends == {("sw-a", "ge-0/0/47"), ("sw-b", "Gi1/0/48")} and link["issues"] == []
    ports = client.get("/cabling/ports", params={"tenant_id": tid, "device": "sw-a"}).json()
    by = {p["name"]: p for p in ports["items"]}
    assert (by["ge-0/0/1"]["mode"], by["ge-0/0/1"]["vlan"]) == ("access", 20)
    assert by["ge-0/0/47"]["trunk_vlans"] == "1,20"
    # Endpunkt: der Server an sw-a ge-0/0/1 (nicht am Uplink), IP aus dem ARP von sw-b
    ep = client.get("/cabling/endpoints", params={"tenant_id": tid}).json()
    (item,) = ep["items"]
    assert (item["device"], item["port"], item["vlan"], item["mac"]) == ("sw-a", "ge-0/0/1", 20,
                                                                         SRV)  # fmt: skip
    assert item["ips"] == ["10.20.0.15"]
    hit = client.get("/cabling/locate", params={"tenant_id": tid, "q": "10.20.0.15"}).json()
    assert hit["kind"] == "ip" and hit["items"][0]["mac"] == SRV
    assert hit["items"][0]["locations"][0]["port"] == "ge-0/0/1"
    with use("en"):
        assert run_text.show("Switches: 2 gelesen (SNMPv3: Cisco, Juniper)") == (
            "Switches: 2 read (SNMPv3: Cisco, Juniper)")  # fmt: skip


def test_meldungen_snmp_englisch_gleich_wie_collector():
    """Was der SNMP-Teil des Collectors deutsch meldet, uebersetzt der Orchestrator so, wie der
    Collector es selbst englisch schreibt (Konsole)."""
    _collectors()
    ci18n = importlib.import_module("collectors.common.i18n")
    diagnose = importlib.import_module("collectors.common.diagnose")
    sc = importlib.import_module("collectors.snmp.client")
    oids = importlib.import_module("collectors.snmp.oids")
    dto = importlib.import_module("collectors.common.dto")

    def config_error(**kw):
        def make():
            base = {"username": "ro", "auth_password": AUTH, "priv_password": PRIV}
            try:
                sc.credentials(dto.Target(**{**base, **kw}))
            except sc.SnmpConfigError as exc:
                return str(exc)
            raise AssertionError("kein Fehler")

        return make

    def blocked():
        try:
            oids.check("walk", "1.3.6.1.6.3.15")
        except PermissionError as exc:
            return str(exc)
        raise AssertionError("nicht blockiert")

    makers = [
        lambda: diagnose.device_error(sc.SnmpAuthError("Unknown USM user"), "cisco_switch"),
        lambda: diagnose.device_error(sc.SnmpTimeout("timeout"), "cisco_switch"),
        lambda: diagnose.device_error(sc.SnmpError("genErr"), "cisco_switch"),
        config_error(priv_password=None),
        config_error(auth_protocol="md5"),
        config_error(priv_protocol="des"),
        config_error(auth_password="kurz"),
        blocked,
        lambda: str(sc.SnmpUnavailable(ci18n.L(
            "pysnmp fehlt - Agent neu installieren bzw. Selbst-Update abwarten",
            "pysnmp missing - reinstall the agent or wait for the self-update"))),
    ]  # fmt: skip
    for make in makers:
        with ci18n.use("de"):
            de = make()
        with ci18n.use("en"):
            en = make()
        assert de != en, de
        assert run_text.translate(de) == en
    # Hinweise des Laufs (deutsch gespeichert, collectors/snmp/tasks.py)
    message = (
        "Switches: 1 gelesen (SNMPv3: Aruba) · Hinweise: 10.0.0.12: ohne Antwort (Tabelle "
        "fehlt bzw. nicht in der View): LLDP-MIB; 10.0.0.12: MAC-Tabelle: mehr als 50000 "
        "Eintraege, nur die ersten uebernommen; 10.0.0.12: dot1qPvid: SNMP-Fehler: genErr"
    )
    assert run_text.translate(message) == (
        "Switches: 1 read (SNMPv3: Aruba) · Notes: 10.0.0.12: no response (table missing or not "
        "in the view): LLDP-MIB; 10.0.0.12: MAC table: more than 50000 entries, only the first "
        "taken over; 10.0.0.12: dot1qPvid: SNMP error: genErr")  # fmt: skip
