"""Leistungsmessung mit grossem kuenstlichem Regelwerk (R18, ADR 0016) - nur lokal.

Legt in einer LEEREN Datenbank einen Mandanten mit DDI und einer FortiGate an (anonymisierte,
erzeugte Daten: Hosts, Netze, verschachtelte Gruppen, Services, VIPs, Regeln), misst das
Einlesen und die wichtigsten Abfragen. Die Datenbank wird dabei neu angelegt - nie gegen eine
Betriebsdatenbank laufen lassen.

    AUTOMATO_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/automato_bench \
      uv run --package orchestrator python scripts/bench_large_ruleset.py [regeln] [hosts]

Mit BENCH_SKIP_SEED=1 nur die Abfragen gegen einen vorhandenen Bestand (Mandant 1) messen.
"""

import os
import random
import sys
import time

os.environ.setdefault("AUTOMATO_CREDENTIALS_KEY", "bench")  # nur Testdaten

from app.api import jobs  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

N_RULES = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
N_HOSTS = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
SKIP_SEED = os.environ.get("BENCH_SKIP_SEED") == "1"
TOKEN = {"X-Internal-Token": "dev-only-token"}
rnd = random.Random(7)  # noqa: S311 - reproduzierbare Testdaten, keine Kryptografie

engine = get_engine()
if not SKIP_SEED:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
jobs.celery_app.dispatch_collect = lambda platform, run_id: None
app = create_app()
c = TestClient(app)


def ok(r, code=200):
    if r.status_code != code:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:500]}")
    return r


def timed(label, fn, n=1):
    t = time.perf_counter()
    for _ in range(n):
        out = fn()
    dt = (time.perf_counter() - t) / n
    print(f"{label:<44} {dt * 1000:9.0f} ms")
    return out


def host_ip(i):
    return f"10.{i // 62500}.{(i // 250) % 250}.{i % 250 + 1}"


if not SKIP_SEED:
    tid = ok(c.post("/tenants", json={"name": "Kunde A", "code": "KA"}), 201).json()["id"]
    ddi = ok(
        c.post(
            "/systems",
            json={
                "tenant_id": tid,
                "name": "ddi-01",
                "platform": "infoblox",
                "base_url": "https://ddi.example.ch",
                "username": "ro",
                "password": "x",
            },
        ),
        201,
    ).json()["id"]
    fw = ok(
        c.post(
            "/systems",
            json={
                "tenant_id": tid,
                "name": "fw-01",
                "platform": "fortigate",
                "base_url": "https://fw.example.ch",
            },
        ),
        201,
    ).json()["id"]

    # DDI: Hosts + Netze
    records = [
        {"kind": "host", "value": host_ip(i), "dns_name": f"srv{i:05d}.example.ch"}
        for i in range(N_HOSTS)
    ]
    nets = sorted({".".join(host_ip(i).split(".")[:3]) + ".0/24" for i in range(N_HOSTS)})
    records += [
        {"kind": "prefix", "value": n, "vlan_vid": 100 + k % 3000, "description": f"Netz {k}"}
        for k, n in enumerate(nets)
    ]
    run_id = ok(
        c.post(
            "/jobs/collect",
            json={"platform": "infoblox", "tenant_id": tid, "source_system_id": ddi},
        ),
        202,
    ).json()["run_id"]
    t = time.perf_counter()
    for i in range(0, len(records), 5000):
        ok(
            c.post(
                f"/internal/collector-runs/{run_id}/references",
                headers=TOKEN,
                json={"records": records[i : i + 5000]},
            ),
            204,
        )
    ok(
        c.post(
            f"/internal/collector-runs/{run_id}/result",
            headers=TOKEN,
            json={"status": "completed", "reference_count": len(records)},
        )
    )
    label = f"DDI einlesen ({len(records)} Eintraege)"
    print(f"{label:<44} {(time.perf_counter() - t) * 1000:9.0f} ms")

    # FortiGate-Objekte
    objects = []
    hosts = [f"H_srv{i:05d}" for i in range(N_HOSTS)]
    for i, name in enumerate(hosts):
        objects.append(
            {
                "name": name,
                "category": "address",
                "obj_type": "ipmask",
                "values": [host_ip(i)],
                "family": "v4",
            }
        )
    netnames = []
    for k, n in enumerate(nets):
        name = f"N_net{k:04d}"
        netnames.append(name)
        objects.append(
            {
                "name": name,
                "category": "address",
                "obj_type": "ipmask",
                "values": [n],
                "family": "v4",
            }
        )
    groups = []
    for g in range(max(50, N_HOSTS // 10)):
        members = rnd.sample(hosts, rnd.randint(3, 25)) + rnd.sample(netnames, rnd.randint(0, 2))
        if groups and rnd.random() < 0.2:
            members.append(rnd.choice(groups))
        name = f"G_grp{g:04d}"
        groups.append(name)
        objects.append(
            {
                "name": name,
                "category": "address_group",
                "members": members,
                "values": [],
                "family": "v4",
            }
        )
    services = []
    for s in range(400):
        port = 1000 + s
        name = f"FT_tcp{port}"
        services.append(name)
        objects.append(
            {
                "name": name,
                "category": "service",
                "values": [f"tcp/{port}"],
                "attrs": {"tcp-portrange": str(port)},
            }
        )
    sgroups = []
    for s in range(60):
        name = f"SG_{s:03d}"
        sgroups.append(name)
        objects.append(
            {
                "name": name,
                "category": "service_group",
                "members": rnd.sample(services, 5),
                "values": [],
            }
        )
    vips = []
    for v in range(200):
        name = f"V-EXT_app{v:03d}.example.ch"
        vips.append(name)
        objects.append(
            {"name": name, "category": "vip", "values": [f"203.0.113.{v % 250}", host_ip(v)]}
        )
    by_name = {o["name"]: o for o in objects}

    def resolve(name, depth=0):
        o = by_name.get(name)
        if o is None:
            return [name]
        if o["category"] in ("address_group", "service_group") and depth < 4:
            return [v for m in o["members"] for v in resolve(m, depth + 1)]
        return o["values"][:1] if o["category"] == "vip" else o["values"]

    rules = []
    addr_pool = hosts + netnames + groups
    for r in range(N_RULES):
        src = rnd.sample(addr_pool, rnd.randint(1, 4))
        dst = rnd.sample(addr_pool, rnd.randint(1, 4))
        if rnd.random() < 0.05:
            dst.append(rnd.choice(vips))
        if rnd.random() < 0.03:
            src = ["all"]
        svc = rnd.sample(services + sgroups, rnd.randint(1, 3))
        rules.append(
            {
                "source_platform": "fortigate",
                "external_id": str(r + 1),
                "name": f"rule-{r:05d}",
                "action": "allow",
                "sources": ["all"]
                if src == ["all"]
                else list(dict.fromkeys(v for n in src for v in resolve(n))),
                "destinations": list(
                    dict.fromkeys(
                        (f"vip:{n}={by_name[n]['values'][0]}" if n in vips else v)
                        for n in dst
                        for v in ([n] if n in vips else resolve(n))
                    )
                ),
                "services": list(dict.fromkeys(v for n in svc for v in resolve(n))),
                "source_refs": src,
                "destination_refs": dst,
                "service_refs": svc,
                "src_zone": f"zone{rnd.randint(1, 12)}",
                "dst_zone": f"zone{rnd.randint(1, 12)}",
                "raw": {"comments": f"CTASK{r:06d}"},
            }
        )
    print(
        f"Objekte {len(objects)}, Regeln {len(rules)}, Endpunkte "
        f"{sum(len(r['sources']) + len(r['destinations']) for r in rules)}"
    )
    run_id = ok(
        c.post(
            "/jobs/collect",
            json={"platform": "fortigate", "tenant_id": tid, "source_system_id": fw},
        ),
        202,
    ).json()["run_id"]
    t = time.perf_counter()
    ok(
        c.post(
            f"/internal/collector-runs/{run_id}/result",
            headers=TOKEN,
            json={"status": "completed", "rules": rules, "objects": objects},
        )
    )
    print(f"{'FortiGate einlesen':<44} {(time.perf_counter() - t) * 1000:9.0f} ms")
else:
    tid = 1

H = {"X-Dev-Roles": "admin"}
p = {"tenant_id": tid}
timed("GET /rules (Seite 500)", lambda: ok(c.get("/rules", params={**p, "limit": 500}, headers=H)))
timed(
    "GET /rules?q=srv00042", lambda: ok(c.get("/rules", params={**p, "q": "srv00042"}, headers=H))
)
timed(
    "GET /rules?q=10.0.3.7 (IP im Netz)",
    lambda: ok(c.get("/rules", params={**p, "q": "10.0.3.7"}, headers=H)),
)
timed(
    "GET /rules?object=H_srv00042",
    lambda: ok(c.get("/rules", params={**p, "object": "H_srv00042"}, headers=H)),
)
objs = timed(
    "GET /objects (Seite 200)", lambda: ok(c.get("/objects", params={**p, "limit": 200}, headers=H))
)
timed(
    "GET /objects?q=srv0004", lambda: ok(c.get("/objects", params={**p, "q": "srv0004"}, headers=H))
)
grp = next(
    o for o in ok(c.get("/objects", params={**p, "q": "G_grp0001"}, headers=H)).json()["rows"]
)
timed("GET /objects/{id} (Gruppe)", lambda: ok(c.get(f"/objects/{grp['id']}", headers=H)))
print("  used_in_rules G_grp0001:", grp["used_in_rules"])
systems = ok(c.get("/appdoc/systems", params=p, headers=H)).json()
name = systems[len(systems) // 2]["name"] if systems else None
if name:
    timed(
        f"GET /appdoc/systems/{name}",
        lambda: ok(c.get(f"/appdoc/systems/{name}", params=p, headers=H)),
    )
# Kunden (ADR 0018): Ableitung ueber Netze - nach jedem Lauf und beim Speichern eines Kunden
body = {"tenant_id": tid, "name": "Bench-Kunde",
        "selectors": [{"kind": "prefix", "value": "10.0.0.0/16"},
                      {"kind": "prefix", "value": "10.0.3.0/24", "exclude": True}]}  # fmt: skip
cust = timed("POST /customers (Netz, Ausschluss)",
             lambda: ok(c.post("/customers", json=body, headers=H), 201))  # fmt: skip
print("  Kunde:", cust.json()["counts"])
