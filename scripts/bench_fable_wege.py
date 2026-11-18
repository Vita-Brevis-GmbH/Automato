# ruff: noqa: E501, S104, S106 - Messskript mit langen Testdaten-Zeilen; 0.0.0.0/0 sind Routen
"""Leistungsmessung der Weg-Suche (Prüfung A „Wege“, Fable Phase 1) - nur lokal.

Legt in einer LEEREN Datenbank einen Mandanten an (erzeugte, anonymisierte Daten): SITES
FortiGates mit je einem LAN, ROUTES Routen und RULES Policies, verbunden über zwei Core-Router
(ECMP) - ein Weg zwischen zwei Standorten geht fw → rt-core-01|02 → fw. Gemessen wird
`paths.trace` so, wie die Verbindungen der Applikations-Doku es aufrufen (ein Graph, geladene
Policies, ohne Backup-Wege bzw. mit), für verschiedene Paare Quelle/Ziel/Dienst; dazu der Anteil
von Routing und Regeln und - mit BENCH_PROFILE=1 - die teuersten Aufrufe (cProfile).

    AUTOMATO_DATABASE_URL=sqlite:////tmp/bench_wege.db \\
      uv run --package orchestrator python scripts/bench_fable_wege.py [sites] [routes] [rules] [traces]

Die Datenbank wird neu angelegt - nie gegen eine Betriebsdatenbank laufen lassen.
"""

import cProfile
import os
import pstats
import random
import sys
import time

os.environ.setdefault("AUTOMATO_CREDENTIALS_KEY", "bench")  # nur Testdaten

from app.api import jobs  # noqa: E402
from app.db import get_engine, get_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.store import path_store, paths, policy_check  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SITES = int(sys.argv[1]) if len(sys.argv) > 1 else 20
ROUTES = int(sys.argv[2]) if len(sys.argv) > 2 else 5000  # je Firewall und Core-Router
RULES = int(sys.argv[3]) if len(sys.argv) > 3 else 1000  # je Firewall
TRACES = int(sys.argv[4]) if len(sys.argv) > 4 else 500
TOKEN = {"X-Internal-Token": "dev-only-token"}
H = {"X-Dev-Roles": "admin"}
rnd = random.Random(7)  # noqa: S311 - reproduzierbare Testdaten, keine Kryptografie

engine = get_engine()
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
jobs.celery_app.dispatch_collect = lambda platform, run_id: None
c = TestClient(create_app())


def ok(r, code=200):
    if r.status_code != code:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def timed(label, fn):
    t = time.perf_counter()
    out = fn()
    ms = (time.perf_counter() - t) * 1000
    print(f"{label:<58} {ms:10.0f} ms", flush=True)
    return out, ms


def deliver(tid, name, platform, result, **extra):
    body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x", **extra}
    sid = ok(c.post("/systems", json=body, headers=H), 201)["id"]
    run = ok(
        c.post(
            "/jobs/collect",
            headers=H,
            json={"platform": platform, "tenant_id": tid, "source_system_id": sid},
        ),
        202,
    )["run_id"]
    ok(
        c.post(
            f"/internal/collector-runs/{run}/result",
            headers=TOKEN,
            json={"status": "completed", **result},
        )
    )
    return sid


def r(node, context, prefix, hops, protocol="static", **kw):
    return {
        "node": node,
        "context": context,
        "prefix": prefix,
        "protocol": protocol,
        "next_hops": hops,
        **kw,
    }


def filler(node, context, hops, n):
    """n weitere Routen (BGP, 172.16.0.0/12 und 100.64.0.0/10 als /24 bzw. /28) - Last im Index."""
    out = []
    for i in range(n):
        if i < 4096:
            prefix = f"172.{16 + i // 256}.{i % 256}.0/24"
        else:
            j = i - 4096
            prefix = f"100.{64 + (j // 4096) % 64}.{(j // 16) % 256}.{(j % 16) * 16}/28"
        out.append(r(node, context, prefix, hops, "bgp", distance=20))
    return out


def seed() -> int:
    tid = ok(c.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=H), 201)["id"]
    cores = ("rt-core-01", "rt-core-02")
    core_routes = []
    for k, core in enumerate(cores):
        for s in range(SITES):
            ip = f"10.255.{s}.{k * 4 + 2}"
            core_routes += [
                r(
                    core,
                    core,
                    f"10.255.{s}.{k * 4}/30",
                    [{"interface": f"Gi0/{s}"}],
                    "connected",
                    distance=0,
                ),
                r(core, core, f"{ip}/32", [{"interface": f"Gi0/{s}"}], "local", distance=0),
                r(
                    core,
                    core,
                    f"10.{s}.0.0/16",
                    [{"ip": f"10.255.{s}.{k * 4 + 1}", "interface": f"Gi0/{s}"}],
                    "bgp",
                    distance=20,
                ),
            ]
        core_routes += filler(
            core, core, [{"ip": f"10.255.0.{k * 4 + 1}", "interface": "Gi0/0"}], ROUTES
        )
    deliver(
        tid,
        "Core",
        "cisco_switch",
        {
            "inventory": {"devices": [{"name": n, "role": "switch"} for n in cores]},
            "routing": {
                "contexts": [{"node": n, "context": n, "kind": "l3"} for n in cores],
                "routes": core_routes,
            },
        },
        read_scope="10.255.255.1",
        username="ro",
        password="x",
    )  # noqa: S106 - Testdaten
    for s in range(SITES):
        fw = f"fw-{s:02d}"
        up = [
            {"ip": f"10.255.{s}.2", "interface": "up1"},
            {"ip": f"10.255.{s}.6", "interface": "up2"},
        ]
        routes = [
            r(fw, "root", f"10.{s}.0.0/16", [{"interface": "lan"}], "connected"),
            r(fw, "root", f"10.255.{s}.0/30", [{"interface": "up1"}], "connected"),
            r(fw, "root", f"10.255.{s}.4/30", [{"interface": "up2"}], "connected"),
            r(fw, "root", "10.0.0.0/8", up, "bgp", distance=20),
            *filler(fw, "root", up, ROUTES),
        ]
        rules = []
        for i in range(RULES):
            out = i % 2 == 0
            net = f"10.{s}.{i % 250}.0/24"
            other = f"10.{rnd.randrange(SITES)}.{rnd.randrange(250)}.0/24"
            src_if, dst_if = ("lan", "up1") if out else ("up1", "lan")
            rules.append(
                {
                    "source_platform": "fortigate",
                    "external_id": str(i + 1),
                    "name": f"p{i + 1}",
                    "action": "allow" if i % 7 else "deny",
                    "sources": [net if out else other],
                    "destinations": [other if out else net],
                    "services": [f"tcp/{rnd.choice((22, 80, 443, 1433, 3389, 8443))}"],
                    "src_zone": src_if,
                    "dst_zone": dst_if,
                    "vdom": "root",
                    "family": "v4",
                    "raw": {
                        "policyid": i + 1,
                        "srcintf": [{"name": src_if}],
                        "dstintf": [{"name": dst_if}],
                        "status": "enable",
                        "schedule": "always",
                    },
                }
            )
        deliver(
            tid,
            fw,
            "fortigate",
            {
                "rules": rules,
                "inventory": {
                    "devices": [{"name": fw, "role": "firewall"}],
                    "interfaces": [
                        {
                            "device": fw,
                            "name": "lan",
                            "ips": [f"10.{s}.0.1/16"],
                            "zone": "lan",
                            "vdom": "root",
                        },
                        {
                            "device": fw,
                            "name": "up1",
                            "ips": [f"10.255.{s}.1/30"],
                            "zone": "core",
                            "vdom": "root",
                        },
                        {
                            "device": fw,
                            "name": "up2",
                            "ips": [f"10.255.{s}.5/30"],
                            "zone": "core",
                            "vdom": "root",
                        },
                    ],
                },
                "routing": {"contexts": [{"node": fw, "context": "root"}], "routes": routes},
            },
        )
    return tid


def pairs(n):
    out = []
    for _ in range(n):
        a, b = rnd.sample(range(SITES), 2)
        out.append(
            (
                f"10.{a}.{rnd.randrange(250)}.{rnd.randrange(2, 250)}",
                f"10.{b}.{rnd.randrange(250)}.{rnd.randrange(2, 250)}",
                ("tcp", rnd.choice((22, 80, 443, 1433, 3389, 8443))),
            )
        )
    return out


def run(session, tid, todo, backups, with_rules=True, shared=False):
    """Wie `app_paths.connection_trace`: ein Graph und geladene Policies für alle Wege."""
    graph = paths.Graph(session, tid)
    checks = policy_check.Checks.load(session, tid)
    reached = 0
    for src, dst, svc in todo:
        res = paths.trace(
            session,
            tid,
            dst,
            src,
            None,
            0,
            svc,
            graph=graph,
            checks=checks,
            backups=backups,
            with_rules=with_rules,
        )
        reached += any(p["end"]["kind"] in paths.REACHED for p in res["paths"])
    return reached


def main() -> None:
    print(
        f"Standorte {SITES}, Routen je Gerät {ROUTES}, Policies je Firewall {RULES}, "
        f"Wege {TRACES}"
    )
    tid, _ = timed("Bestand anlegen (einlesen)", seed)
    session = get_session_factory()()
    todo = pairs(TRACES)
    _, first = timed(
        "erster Weg (Graph, Policies, Index kalt)", lambda: run(session, tid, todo[:1], False)
    )
    (reached, ms) = timed(
        f"{TRACES} Wege, ohne Backup, mit Regeln", lambda: run(session, tid, todo, False)
    )
    print(f"  je Weg {ms / TRACES:8.2f} ms, erreicht {reached}/{TRACES}")
    (_, only) = timed(
        f"{TRACES} Wege, nur Routing (ohne Regeln)", lambda: run(session, tid, todo, False, False)
    )
    print(
        f"  je Weg {only / TRACES:8.2f} ms  -> Anteil Regeln {max(ms - only, 0) / ms * 100:4.0f} %"
    )
    (_, withb) = timed(
        f"{TRACES} Wege, mit Backup-Wegen, mit Regeln", lambda: run(session, tid, todo, True)
    )
    print(f"  je Weg {withb / TRACES:8.2f} ms")
    # Wege-Speicher des Mandanten (ADR 0048): wie Doku und Befunde im Hintergrund
    with path_store.active(session, tid):
        (_, cold) = timed(
            f"{TRACES} Wege im Wege-Speicher, erster Durchgang",
            lambda: run(session, tid, todo, False),
        )
        (_, warm) = timed(
            f"{TRACES} Wege im Wege-Speicher, zweiter Durchgang",
            lambda: run(session, tid, todo, False),
        )
        print(f"  je Weg {cold / TRACES:8.2f} ms kalt, {warm / TRACES:8.2f} ms gemerkt")
    per = ms / TRACES
    for apps, conns in ((800, 50), (800, 200)):
        total = apps * conns * per / 1000
        print(
            f"Hochrechnung {apps} Applikationen x {conns} verschiedene Verbindungen: "
            f"{total / 60:6.1f} min (ein Prozess, ohne Rückweg/Zonen)"
        )
    if os.environ.get("BENCH_PROFILE") == "1":
        prof = cProfile.Profile()
        prof.enable()
        run(session, tid, pairs(TRACES), False)
        prof.disable()
        pstats.Stats(prof).sort_stats("cumulative").print_stats(35)
        pstats.Stats(prof).sort_stats("tottime").print_stats(25)


if __name__ == "__main__":
    main()
