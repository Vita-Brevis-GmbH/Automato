"""Module der Plattform (0.107.0, ADR 0036, Anwender 2026-10-28):

- jede Datei in `app/api` und `app/store` gehoert genau einem Modul (Verzeichnis `app/modules`);
- die lesenden Module bleiben „starr getrennt von dem schreibenden Pfad“: nur der Schreibpfad
  nutzt `sot_gateway.adapters.netbox_writer` bzw. den NetBox-Sync, die Collector-Pakete gar
  nichts vom Orchestrator;
- Module je Mandant schaltbar: aus = keine API (403 `module_disabled`), keine Laeufe (Worker),
  keine Referenzquellen (Gateway), keine Befunde der Applikationen im Hintergrund; Kern und
  Einstellungen immer an, Abhaengigkeiten (Schreibpfad braucht das Gateway).
"""

import ast
from pathlib import Path

from app import modules
from app.models import AuditLog
from test_app_paths import _connections, _seed

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "orchestrator" / "app"
ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
WRITE = ("sot_gateway.adapters.netbox_writer", "app.store.netbox_sync", "app.api.netbox_sync")


def _files(folder: str) -> set[str]:
    return {p.stem for p in (APP / folder).glob("*.py") if p.stem != "__init__"}


def test_jede_datei_gehoert_genau_einem_modul():
    for folder, attr in (("api", "api"), ("store", "store")):
        owners: dict[str, list[str]] = {}
        for m in modules.MODULES:
            for name in getattr(m, attr):
                owners.setdefault(name, []).append(m.key)
        files = _files(folder)
        assert files - set(owners) == set(), f"{folder}: ohne Modul"
        assert set(owners) - files == set(), f"{folder}: im Verzeichnis, aber nicht vorhanden"
        assert {n: k for n, k in owners.items() if len(k) > 1} == {}, f"{folder}: doppelt"
    # Abhaengigkeiten zeigen auf bekannte, vorher stehende Module
    seen: set[str] = set()
    for m in modules.MODULES:
        assert set(m.depends) <= seen, m.key
        seen.add(m.key)


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def _owner(path: Path) -> str | None:
    rel = path.relative_to(APP).with_suffix("")
    if len(rel.parts) == 2 and rel.parts[0] in ("api", "store"):
        return next((m.key for m in modules.MODULES
                     if rel.parts[1] in getattr(m, rel.parts[0])), None)  # fmt: skip
    return None


def test_lesen_und_schreiben_starr_getrennt():
    writers = {m.key for m in modules.MODULES if m.role == modules.WRITE}
    bad = []
    for path in APP.rglob("*.py"):
        if path.relative_to(APP).as_posix() == "main.py":
            continue  # bindet nur die Router ein
        used = {w for w in WRITE if any(i == w or i.startswith(w + ".") for i in _imports(path))}
        if used and _owner(path) not in writers:
            bad.append((path.relative_to(APP).as_posix(), sorted(used)))
    assert bad == [], bad
    # die Collector-Pakete (Worker) und die Lese-Adapter des Gateways schreiben nie
    readers = [*(ROOT / "collectors" / "src").rglob("*.py"),
               *(p for p in (ROOT / "sot-gateway" / "src").rglob("*.py")
                 if p.stem != "netbox_writer")]  # fmt: skip
    for path in readers:
        imports = _imports(path)
        assert not any(i.startswith("app.") or i == "app" for i in imports), path
        assert not any(i.startswith(WRITE[0]) for i in imports), path


def _switch(client, tid, key, on, headers=ADMIN):
    return client.put(f"/modules/{key}", params={"tenant_id": tid}, json={"enabled": on},
                      headers=headers)  # fmt: skip


def _states(client, tid):
    return {m["key"]: m for m in client.get("/modules", params={"tenant_id": tid}).json()}


def test_module_je_mandant_schalten(client, session_factory):
    tid = _seed(client)
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    out = _connections(client, tid)  # Applikation „Web“ mit Verbindungen
    assert out["connections"]
    app_id = client.get("/applications", params={"tenant_id": tid}).json()[0]["id"]
    states = _states(client, tid)
    assert all(m["effective"] for m in states.values())
    assert (states["kern"]["switchable"], states["doku"]["switchable"]) == (False, True)
    assert states["schreibpfad"]["role"] == "schreiben" and states["schreibpfad"]["depends"] == [
        "gateway"]  # fmt: skip

    # nur der Admin schaltet; Kern bleibt an, unbekannte Module gibt es nicht
    assert _switch(client, tid, "doku", False, OP).status_code == 403
    assert _switch(client, tid, "kern", False).status_code == 422
    assert _switch(client, tid, "gibts-nicht", False).status_code == 404

    # Wegsucher aus: Verbindungen 403, die Doku selbst bleibt
    assert _switch(client, tid, "wege", False).status_code == 200
    r = client.get(f"/applications/{app_id}/connections")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "module_disabled"
    assert r.json()["detail"]["module"] == "wege"
    assert client.get(f"/applications/{app_id}/doc").status_code == 200
    assert client.get("/routing/trace", params={"tenant_id": tid, "dst": "10.60.0.5"}
                      ).status_code == 403  # fmt: skip
    assert client.get("/routing/summary", params={"tenant_id": tid}).status_code == 200

    # Doku aus: Applikationen 403 - der andere Mandant nicht
    assert _switch(client, tid, "doku", False).status_code == 200
    assert client.get("/applications", params={"tenant_id": tid}).status_code == 403
    assert client.get(f"/applications/{app_id}/doc").status_code == 403
    assert client.get("/applications", params={"tenant_id": other}).status_code == 200
    assert _switch(client, tid, "doku", True).status_code == 200

    # Findings aus: Liste 403, in der Doku keine Befunde
    assert _switch(client, tid, "findings", False).status_code == 200
    assert client.get("/findings", params={"tenant_id": tid}).status_code == 403
    assert client.get(f"/applications/{app_id}/doc").json()["findings"] == []

    # Gateway aus: der Schreibpfad ist mit aus und laesst sich nicht einschalten
    assert _switch(client, tid, "gateway", False).status_code == 200
    states = _states(client, tid)
    assert states["schreibpfad"]["enabled"] and not states["schreibpfad"]["effective"]
    r = _switch(client, tid, "schreibpfad", True)
    assert r.status_code == 422 and "Gateway" in r.text

    # Worker aus: keine Laeufe (manuell, alle, Zeitplan) fuer diesen Mandanten
    assert _switch(client, tid, "worker", False).status_code == 200
    sid = client.get(f"/systems?tenant_id={tid}", headers=ADMIN).json()[0]["id"]
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    r = client.post("/jobs/collect", json=body, headers=OP)
    assert r.status_code == 422 and "Worker" in r.text
    every = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=OP).json()
    assert every["queued"] == 0 and all("Worker" in i["skipped"] for i in every["items"])

    s = session_factory()
    audits = [r for r in s.query(AuditLog) if r.action == "module.switched"]
    s.close()
    assert len(audits) == 6
    # ohne Mandant (alle): alles an
    assert all(m["effective"] for m in client.get("/modules").json())
