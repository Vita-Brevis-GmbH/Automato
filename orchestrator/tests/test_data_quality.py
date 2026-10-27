"""Datenqualitaet (ADR 0029, Roadmap Schritt 6 „Daten verschaerfen“): Bereiche mit Ampel,
Ursachen und Links (`GET /data-quality`), Befunde `source_stale`/`source_partial` im Abgleich und
im Zeitplan, Rechte (viewer, nicht fuer Kunden-Benutzer) und Englisch.

Umgebung: die Firewall aus `test_policy_check` (LAN, DMZ, Internet, Routing) mit zwei
zusaetzlichen Policies - eine ueber `ssl.root` (nicht im Inventar), eine zu einer IPv6-Adresse
ohne Route - und einem BGP-Kontext „nicht lesbar“; dazu eine Firewall ohne Routing.
"""

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import test_customers as cu
import test_policy_check as pc
from app.config import get_settings
from app.i18n import use
from app.models import CollectorRun, Finding, SourceSystem
from app.store import data_quality, finding_text
from sqlalchemy import select

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
TOKEN = {"X-Internal-Token": "dev-only-token"}

FW = copy.deepcopy(pc.FW)
FW["rules"] += [
    pc._policy(8, "vpn-to-dmz", "ssl.root", "dmz", ["10.99.0.0/24"], ["10.30.0.10"], ["tcp/443"]),
    pc._policy(9, "lan-to-v6", "lan", "dmz", ["10.10.0.0/24"], ["2001:db8::5"], ["tcp/443"]),
]
FW["routing"]["contexts"] = [{"node": "fw-r", "context": "root",
                              "missing": ["BGP-Pfade IPv4 (HTTP 403)"]}]  # fmt: skip
FW["message"] = "Routing nicht lesbar: BGP-Pfade IPv4 (HTTP 403)"

NOROUTE = {  # Firewall ohne Routing: Relevanz ihrer Regeln bleibt „unbekannt“
    "status": "completed",
    "rules": [pc._policy(1, "a-to-b", "port1", "port2", ["10.1.0.0/24"], ["10.2.0.5"], ["tcp/22"])],
    "inventory": {
        "devices": [{"name": "fw-x", "role": "firewall"}],
        "interfaces": [{"device": "fw-x", "name": "port1", "vdom": "root"},
                       {"device": "fw-x", "name": "port2", "vdom": "root"}],
    },
}  # fmt: skip


def _system(client, tid, name, platform="fortigate", **extra) -> int:
    body = {"tenant_id": tid, "name": name, "platform": platform,
            "base_url": f"https://{name}.example.ch", **extra}  # fmt: skip
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _deliver(client, tid, sid, result, platform="fortigate") -> str:
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200, r.text
    return run_id


def _report(client, tid, headers=None, **params) -> tuple[dict, dict]:
    r = client.get("/data-quality", params={"tenant_id": tid, **params}, headers=headers or {})
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {a["key"]: a for a in body["areas"]}


def _entries(area: dict) -> dict:
    return {e["key"]: e for e in area["entries"]}


def _seed(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = _system(client, tid, "fw-r")
    _deliver(client, tid, fw, FW)
    other = _system(client, tid, "fw-x")
    _deliver(client, tid, other, NOROUTE)
    return tid, fw, other


def _age(session_factory, sid: int, days: int) -> None:
    """Alle Laeufe des Quellsystems um `days` Tage zurueckdatieren."""
    s = session_factory()
    for run in s.scalars(select(CollectorRun).where(CollectorRun.source_system_id == sid)):
        run.queued_at = run.queued_at - timedelta(days=days)
        run.finished_at = (run.finished_at or run.queued_at) - timedelta(days=days)
    src = s.get(SourceSystem, sid)
    src.created_at = src.created_at - timedelta(days=days)
    s.commit()
    s.close()


def _findings(client, tid, ftype, headers=None) -> list[dict]:
    r = client.get("/findings", params={"tenant_id": tid, "type": ftype}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def test_regeln_relevanz_ursachen_und_stichprobe(client):
    tid, fw, other = _seed(client)
    body, areas = _report(client, tid)
    assert [a["key"] for a in body["areas"]] == ["sources", "rules", "vms", "ipam", "assignment"]
    rules = _entries(areas["rules"])

    # Firewall ohne Routing: ihre Regel mit Interfaces ist nicht pruefbar (Anteil 1 von 9 = gelb)
    no_routing = rules["rules:no_routing"]
    assert (no_routing["status"], no_routing["count"]) == ("warn", 1)
    assert no_routing["cause"] == "fw-x (1)"
    assert no_routing["link"]["view"] == "regeln" and no_routing["link"]["params"]["rule"]
    # Interface nicht im Inventar (ssl.root): Relevanz unbekannt
    unknown = rules["rules:interface_unknown"]
    assert (unknown["count"], unknown["cause"]) == (1, "fw-r: ssl.root")
    assert areas["rules"]["metric"] == "7 von 9 Firewall-Regeln mit Interfaces prüfbar"

    # Stichprobe: je Seite die erste Adresse der Regel - die IPv6-Adresse hat keine Route, das
    # DMZ-Netz der Regel 7 leitet die Firewall nicht ueber ihren Ausgang internet
    sample = rules["rules:sample"]
    assert (sample["count"], sample["title"]) == (
        1, "Stichprobe: Regelseiten mit unbekannter Relevanz")  # fmt: skip
    assert sample["cause"] == "keine Route zur Adresse (1) – geprüft: 8 Seiten von 7 Regeln"
    assert body["sample"] == {"rules": 7, "of": 7}
    impossible = rules["rules:sample_impossible"]
    assert (impossible["status"], impossible["count"]) == ("info", 1)
    assert "die Regel gilt nur für den Ausgang internet" in impossible["cause"]
    assert rules["finding:unmatched_object"]["link"] == {
        "view": "findings", "params": {"type": "unmatched_object"}}  # fmt: skip
    assert areas["rules"]["status"] == "warn"

    # Quellsysteme: beide eingelesen; fw-r nur teilweise (BGP-Pfade nicht lesbar) - mit Link
    # auf den Kontext im Routing, die Meldung „Routing nicht lesbar“ steht nur einmal da
    src = _entries(areas["sources"])
    assert src[f"source:{fw}"]["status"] == "ok"
    assert src[f"source:{fw}"]["link"] == {"view": "laeufe", "params": {"system": str(fw)}}
    partial = src[f"source:{fw}:partial"]
    assert partial["status"] == "warn"
    assert partial["cause"] == "Routing nicht lesbar: fw-r · root (BGP-Pfade IPv4 (HTTP 403))"
    assert partial["link"] == {"view": "routing",
                               "params": {"tab": "routes", "context": f"{fw}|root"}}  # fmt: skip
    assert areas["sources"]["metric"] == "2 von 2 aktuell"

    # ohne IPAM, VMs, Zonen und Kunden: IPAM rot, VMs und Zuordnung zaehlen nicht
    assert _entries(areas["ipam"])["ipam:primary"]["status"] == "crit"
    assert not areas["vms"]["applicable"] and not areas["assignment"]["applicable"]
    assert body["score"] == {"percent": 0, "ok": 0, "total": 3, "status": "crit",
                             "by_status": {"warn": 2, "crit": 1}}  # fmt: skip


def test_ursache_der_relevanz_als_schluessel(client, session_factory):
    """`relevance.check` liefert neben dem Text die Ursache (Datenqualitaet zaehlt danach)."""
    from app.models import Rule
    from app.store import relevance

    tid, fw, other = _seed(client)
    s = session_factory()
    rules = {r.name: r for r in s.scalars(select(Rule).where(Rule.tenant_id == tid))}
    rel = relevance.Relevance(s, tid)
    assert rel.check(rules["a-to-b"], "src", ["10.1.0.1"])["cause"] == relevance.NO_ROUTING
    assert rel.check(rules["lan-to-v6"], "dst", ["2001:db8::5"])["cause"] == relevance.NO_ROUTE
    falsch = rel.check(rules["lan-to-dmz-falsch"], "dst", ["10.30.0.1"])
    assert (falsch["state"], falsch["cause"]) == ("impossible", relevance.OTHER_INTERFACE)
    ok = rel.check(rules["lan-to-dmz-web"], "dst", ["10.30.0.10"])
    assert (ok["state"], ok["cause"]) == ("possible", None)
    assert rel.check(rules["lan-to-dmz-web"], "dst", ["fqdn:x"])["cause"] == relevance.NO_IP
    s.close()


def test_befund_veraltet_mit_zeitplan_und_fehlschlag(client, session_factory):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sid = _system(client, tid, "fw-r")
    _deliver(client, tid, sid, pc.FW)
    assert _findings(client, tid, "source_stale") == []
    _age(session_factory, sid, 10)
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    (f,) = _findings(client, tid, "source_stale")
    assert (f["subject_kind"], f["subject_id"], f["source_system_id"]) == ("source", "fw-r", sid)
    assert f["detail"]["age_days"] == 10 and f["detail"]["limit_days"] == 7
    assert f["message"] == (
        "Quellsystem „fw-r“ (FortiGate) wurde zuletzt vor 10 Tagen erfolgreich eingelesen "
        "(Grenze 7 Tage) – Doku, Weg-Suche und Zonen zeigen einen alten Stand."
    )
    assert "AUTOMATO_STALE_DAYS" in f["hint"]
    (en,) = _findings(client, tid, "source_stale", {"Accept-Language": "en"})
    assert en["message"] == (
        "Source system “fw-r” (FortiGate) was last collected successfully 10 days ago "
        "(limit 7 days) – documentation, path search and zones show outdated data."
    )

    # letzter Lauf fehlgeschlagen: steht im Satz; im Bericht rot erst ab der vierfachen Grenze
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    failed = {"status": "failed", "message": "FortiGate antwortet nicht (Zeitüberschreitung)."}
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=failed)
    client.post("/reconcile", params={"tenant_id": tid}, headers=OP)
    (f,) = _findings(client, tid, "source_stale")
    assert f["message"].endswith(
        "Letzter Lauf fehlgeschlagen: FortiGate antwortet nicht (Zeitüberschreitung)."
    )
    _, areas = _report(client, tid)
    entry = _entries(areas["sources"])[f"source:{sid}"]
    assert (entry["status"], entry["title"]) == ("warn", "fw-r: Stand veraltet (vor 10 Tagen)")
    assert "letzter Lauf fehlgeschlagen: FortiGate antwortet nicht" in entry["cause"]

    # Zeitplan woechentlich: Grenze = Abstand + 1 Tag = 8 Tage, 10 Tage bleibt veraltet
    patch = client.patch(f"/systems/{sid}", json={"schedule": "0 3 * * 1"}, headers=ADMIN)
    assert patch.status_code == 200, patch.text
    client.post("/reconcile", params={"tenant_id": tid}, headers=OP)
    (f,) = _findings(client, tid, "source_stale")
    assert f["detail"]["limit_days"] == 8 and "Zeitplan 0 3 * * 1" in f["message"]
    # monatlich: noch kein Termin verpasst - kein Befund
    client.patch(f"/systems/{sid}", json={"schedule": "0 3 1 * *"}, headers=ADMIN)
    client.post("/reconcile", params={"tenant_id": tid}, headers=OP)
    assert _findings(client, tid, "source_stale") == []


def test_grenze_aus_einstellung_und_zeitplan(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stale_days", 7)
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    limit, period = data_quality.stale_limit(SimpleNamespace(schedule=None), settings, now)
    assert (limit, period) == (timedelta(days=7), None)
    daily = SimpleNamespace(schedule="15 2 * * *")
    assert data_quality.stale_limit(daily, settings, now) == (timedelta(days=7), timedelta(days=1))
    weekly = SimpleNamespace(schedule="0 3 * * 1")
    assert data_quality.stale_limit(weekly, settings, now)[0] == timedelta(days=8)
    assert data_quality.stale_limit(SimpleNamespace(schedule="kaputt"), settings, now)[1] is None
    monkeypatch.setattr(settings, "stale_days", 0)  # 0 = nie veraltet
    assert data_quality.stale_limit(weekly, settings, now) == (None, None)


def test_befund_teilweise_gelesen(client, session_factory):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sw = _system(client, tid, "sw-gruppe", "cisco_switch", username="ro", password="x",
                 read_scope="10.0.0.8\n10.0.0.9")  # fmt: skip
    result = {
        "status": "completed",
        "inventory": {"devices": [{"name": "sw-01", "role": "switch"}]},
        "message": "Switches: 1 gelesen (IOS) · nicht gelesen: 10.0.0.9: Zeitüberschreitung · "
        "Hinweise: 10.0.0.8: show vpc: Ausgabe nicht auswertbar",
    }
    _deliver(client, tid, sw, result, "cisco_switch")
    (f,) = _findings(client, tid, "source_partial")
    assert (f["severity"], f["subject_id"]) == ("warning", "sw-gruppe")
    assert [p["kind"] for p in f["detail"]["parts"]] == ["unreached", "notes"]
    assert f["message"] == (
        "Der letzte Lauf von „sw-gruppe“ hat nur einen Teil gelesen: nicht gelesen: 10.0.0.9: "
        "Zeitüberschreitung; Hinweise: 10.0.0.8: show vpc: Ausgabe nicht auswertbar."
    )
    assert f["hint"].startswith("Erreichbarkeit der Geräte prüfen")
    (en,) = _findings(client, tid, "source_partial", {"Accept-Language": "en"})
    assert en["message"].startswith("The last run of “sw-gruppe” only collected part of the data")
    assert en["hint"].startswith("Check the reachability of the devices")

    # Routing-Kontexte nicht lesbar (FortiGate): Befund mit Kontext, Meldung nicht doppelt
    fw = _system(client, tid, "fw-r")
    _deliver(client, tid, fw, FW)
    found = {x["subject_id"]: x for x in _findings(client, tid, "source_partial")}
    assert found["fw-r"]["detail"]["contexts"] == [
        {"node": "fw-r", "context": "root", "missing": ["BGP-Pfade IPv4 (HTTP 403)"]}
    ]
    assert "parts" not in found["fw-r"]["detail"]
    assert found["fw-r"]["message"] == (
        "Der letzte Lauf von „fw-r“ hat nur einen Teil gelesen: Routing nicht lesbar in 1 "
        "Kontext: fw-r · root (BGP-Pfade IPv4 (HTTP 403))."
    )

    # nur Hinweise: Befund als Hinweis (info); vollstaendig gelesen: kein Befund mehr
    result["message"] = "Switches: 2 gelesen (IOS) · Hinweise: 10.0.0.8: show vpc: x"
    _deliver(client, tid, sw, result, "cisco_switch")
    found = {x["subject_id"]: x for x in _findings(client, tid, "source_partial")}
    assert found["sw-gruppe"]["severity"] == "info"
    result["message"] = "Switches: 2 gelesen (IOS)"
    _deliver(client, tid, sw, result, "cisco_switch")
    assert "sw-gruppe" not in {x["subject_id"] for x in _findings(client, tid, "source_partial")}


def test_zeitplan_rechnet_befunde_ohne_lauf_nach(client, session_factory, monkeypatch):
    """Gelingt kein Lauf mehr, laeuft kein Abgleich - der Zeitplan rechnet stuendlich nach."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sid = _system(client, tid, "fw-r")
    _deliver(client, tid, sid, pc.FW)
    never = _system(client, tid, "fw-neu")
    _age(session_factory, sid, 9)
    _age(session_factory, never, 9)  # vor 9 Tagen angelegt, nie eingelesen
    monkeypatch.setattr(data_quality, "_last_refresh", None)
    assert data_quality.maybe_refresh(session_factory) == 2
    assert data_quality.maybe_refresh(session_factory) is None  # erst in einer Stunde wieder
    found = {f["subject_id"]: f for f in _findings(client, tid, "source_stale")}
    assert found["fw-neu"]["detail"]["never"] is True
    assert found["fw-neu"]["message"].startswith(
        "Quellsystem „fw-neu“ (FortiGate) wurde noch nie erfolgreich eingelesen (Grenze 7 Tage)"
    )
    assert found["fw-r"]["detail"]["age_days"] == 9
    # Triage bleibt beim Nachrechnen erhalten
    s = session_factory()
    finding = s.scalars(select(Finding).where(Finding.subject_id == "fw-r")).one()
    finding.status = "acknowledged"
    s.commit()
    s.close()
    monkeypatch.setattr(data_quality, "_last_refresh", None)
    data_quality.maybe_refresh(session_factory)
    s = session_factory()
    assert s.scalars(select(Finding).where(Finding.subject_id == "fw-r")).one().status == (
        "acknowledged"
    )
    s.close()


def test_betriebszustand_mit_derselben_grenze(client, session_factory, monkeypatch):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sid = _system(client, tid, "fw-r")
    _deliver(client, tid, sid, pc.FW)
    _age(session_factory, sid, 3)

    def check():
        r = client.get("/system/health", params={"tenant_id": tid})
        return {c["key"]: c for c in r.json()["checks"]}[f"source:{sid}"]

    assert check()["status"] == "ok"
    monkeypatch.setattr(get_settings(), "stale_days", 2)
    assert (check()["status"], check()["title"]) == ("warn", "fw-r: Stand veraltet")


def test_vms_ipam_zonen_und_kunden(client):
    tid, ids = cu._seed(client)  # APIC, FortiGate ohne Routing, vCenter mit zwei VMs
    _, areas = _report(client, tid)
    vms = _entries(areas["vms"])
    assert areas["vms"]["metric"] == "2 von 2 VMs mit IP"
    assert vms["vms:no_ip"]["status"] == vms["vms:no_portgroup"]["status"] == "ok"
    assert vms["vms:no_epg"]["count"] == 0  # beide Portgruppen haben ihre EPG
    assert vms["finding:vm_not_in_ipam"]["count"] == 0  # ohne IPAM kein Abgleich
    assert vms["vms:no_ip"]["link"] == {"view": "objekte", "params": {"tab": "vms"}}
    assignment = _entries(areas["assignment"])
    assert assignment["zones:none"]["status"] == "info"
    assert not areas["assignment"]["applicable"]

    # Kunde A ueber seinen ACI-Tenant: der Rest ist ohne Kunde
    cu._create(client, tid, "Kunde A", [{"kind": "aci_tenant", "value": "TE_ka"}])
    _, areas = _report(client, tid)
    assignment = _entries(areas["assignment"])
    assert areas["assignment"]["applicable"]
    assert assignment["customers:epg"]["count"] == 2  # EPG_app, EPG_dns
    assert set(assignment["customers:epg"]["samples"]) == {"TE_kb/AP_100/EPG_app",
                                                           "common/AP_infra/EPG_dns"}  # fmt: skip
    assert assignment["customers:vm"]["samples"] == ["app01-100-kb"]
    assert assignment["customers:vm"]["link"] == {"view": "kunden", "params": {}}

    # logische Zone ueber das Netz von EPG_web: die anderen Netze sind ohne Zone
    zone = {"tenant_id": tid, "name": "DC", "kind": "dc",
            "selectors": [{"kind": "prefix", "value": "10.50.0.0/16"}]}  # fmt: skip
    assert client.post("/zones", json=zone, headers=OP).status_code == 201
    _, areas = _report(client, tid)
    unzoned = _entries(areas["assignment"])["zones:unzoned"]
    assert unzoned["title"] == "Gelesene Netze ohne Zone"
    # EPG-Subnetze 10.50.1.0/24 und 10.50.9.0/24 in der Zone, 10.60.1.0/24 und 10.0.53.0/24 nicht
    assert areas["assignment"]["metric"] == "2 von 4 Netzen in Zonen; ohne Kunde: 6"
    assert unzoned["samples"] == ["10.0.53.0/24", "10.60.1.0/24"]
    assert "10.60.1.0/24" in unzoned["samples"] and "10.50.1.0/24" not in unzoned["samples"]

    # Kunde oben gewaehlt: Regeln/VMs nur seine, Quellsysteme und IPAM als geteilt markiert
    a = client.get("/customers", params={"tenant_id": tid}).json()[0]["id"]
    body, areas = _report(client, tid, customer_id=a)
    assert body["customer_id"] == a
    assert areas["sources"]["note"] == "gilt für alle Kunden (geteilt)"
    assert areas["vms"]["metric"] == "1 von 1 VMs mit IP"
    assert not any(k.startswith("customers:") for k in _entries(areas["assignment"]))


def test_rechte_und_englisch(client):
    tid, fw, other = _seed(client)
    # Kunden-Benutzer: nicht freigegeben (Freigabeliste, fail-closed)
    cust = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": "1"}
    r = client.get("/data-quality", params={"tenant_id": tid}, headers=cust)
    assert (r.status_code, r.json()["detail"]) == (403, "not available for customer users")
    # Viewer eines anderen Mandanten: 403; ohne Mandant 422
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid + 1)}
    assert client.get("/data-quality", params={"tenant_id": tid}, headers=viewer).status_code == 403
    assert client.get("/data-quality", headers=ADMIN).status_code == 422
    mine = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert client.get("/data-quality", params={"tenant_id": tid}, headers=mine).status_code == 200

    body, areas = _report(client, tid, {"Accept-Language": "en"})
    assert [a["title"] for a in body["areas"]] == ["Source systems", "Rules", "VMs", "IPAM",
                                                   "Zones & customers"]  # fmt: skip
    rules = _entries(areas["rules"])
    assert rules["rules:sample"]["cause"] == (
        "no route to the address (1) – checked: 8 sides of 7 rules"
    )
    assert areas["rules"]["metric"] == "7 of 9 firewall rules with interfaces verifiable"
    assert _entries(areas["sources"])[f"source:{fw}:partial"]["title"] == (
        "fw-r: only partially collected"
    )
    assert _entries(areas["ipam"])["ipam:primary"]["title"] == "No primary reference source (IPAM)"


def test_tabellen_als_paare():
    for table in (finding_text._PLATFORM, finding_text._PARTIAL_HINT, data_quality._CAUSE):
        assert all(isinstance(v, tuple) and len(v) == 2 for v in table.values())
    assert all(len(v) == 3 for v in data_quality.FINDINGS.values())
    with use("en"):
        msg, _ = finding_text.explain("source_stale", "ddi-01", {
            "platform": "infoblox", "never": True, "limit_days": 7})  # fmt: skip
    assert msg.startswith("Source system “ddi-01” (Infoblox DDI) has never been collected")


def test_teilfehler_aus_der_meldung():
    parts = data_quality.partial_parts(
        "VDOM nicht lesbar: v2 (HTTP 403) - das Profil braucht Lesezugriff · VDOMs: root · "
        "Routing nicht lesbar: root: router/static (HTTP 403)"
    )
    assert [p["kind"] for p in parts] == ["unreadable", "unreadable"]
    assert data_quality.partial_parts("Avi 22.1 · Tenants admin · 3 Virtual Services") == []
    assert data_quality.partial_parts(None) == []
