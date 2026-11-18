# ruff: noqa: E501, S311 - Messskript: lange Zeilen, Zufall nur fuer reproduzierbare Testdaten
"""Leistungsmessung Regel-Abgleich und Zuordnung (Pruefung B „Regeln“, Fable) - nur lokal.

Legt in einer LEEREN Datenbank einen Mandanten mit einer FortiGate an (erzeugte, anonymisierte
Daten: Zonen, Routen, Hosts, Netze, Gruppen, Dienste, Policies in Reihenfolge) und misst

1. Einlesen samt Regelindex (`rule_index.rebuild`),
2. Laden der Policies fuer den Abgleich (`policy_check.Checker.policies`),
3. den Abgleich je Fluss (`Checker.check`, ohne Urteils-Cache) - erste Regel gewinnt,
4. die Zuordnung Regel <-> Adresse der Doku (`rule_index.covers`) je Adresse,
5. Doku und Verbindungen einer Applikation ueber die API.

Die Datenbank wird neu angelegt - nie gegen eine Betriebsdatenbank laufen lassen.

    AUTOMATO_DATABASE_URL=sqlite:////tmp/bench-fable.db \\
      uv run --package orchestrator python scripts/bench_fable_regeln.py [regeln] [hosts] [fluesse]

Mit BENCH_PROFILE=check|covers|doc die 25 teuersten Aufrufe (cProfile).
"""

import cProfile
import ipaddress
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
from app.store import policy_check, rule_index  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

N_RULES = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
N_HOSTS = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
N_FLOWS = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
N_ZONES = 12
TOKEN = {"X-Internal-Token": "dev-only-token"}
H = {"X-Dev-Roles": "admin"}
PROFILE = os.environ.get("BENCH_PROFILE")
rnd = random.Random(7)

engine = get_engine()
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
jobs.celery_app.dispatch_collect = lambda platform, run_id: None
c = TestClient(create_app())


def ok(r, *codes):
    if r.status_code not in (codes or (200, 201, 202, 204)):
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def timed(label, fn, n=1):
    t = time.perf_counter()
    out = fn()
    ms = (time.perf_counter() - t) * 1000
    per = f"  ({ms / n:8.3f} ms je Stueck, n={n})" if n > 1 else ""
    print(f"{label:<52} {ms:10.0f} ms{per}", flush=True)
    return out


def profiled(name, fn):
    if PROFILE != name:
        return fn()
    prof = cProfile.Profile()
    out = prof.runcall(fn)
    pstats.Stats(prof).sort_stats("cumulative").print_stats(25)
    return out


# -- Daten: Zone z hat 10.<z>.0.0/16, Hosts verteilt ---------------------------------------
zones = [f"z{z:02d}" for z in range(N_ZONES)]


def host_ip(i):
    z = i % N_ZONES
    k = i // N_ZONES
    return f"10.{z}.{k // 250}.{k % 250 + 1}"


hosts = [f"H_srv{i:05d}" for i in range(N_HOSTS)]
objects = [
    {
        "name": n,
        "category": "address",
        "obj_type": "ipmask",
        "values": [host_ip(i)],
        "family": "v4",
        "vdom": "root",
    }
    for i, n in enumerate(hosts)
]
nets = []
for z in range(N_ZONES):
    for k in range(16):
        name = f"N_z{z:02d}_{k:02d}"
        nets.append((name, f"10.{z}.{k}.0/24"))
        objects.append(
            {
                "name": name,
                "category": "address",
                "obj_type": "ipmask",
                "values": [f"10.{z}.{k}.0/24"],
                "family": "v4",
                "vdom": "root",
            }
        )
hosts_in = {z: [n for i, n in enumerate(hosts) if i % N_ZONES == z] for z in range(N_ZONES)}
nets_in = {z: [n for n in nets if n[0].startswith(f"N_z{z:02d}_")] for z in range(N_ZONES)}
groups = []
groups_in: dict[int, list[str]] = {z: [] for z in range(N_ZONES)}
for g in range(max(50, N_HOSTS // 10)):
    z = g % N_ZONES
    pool = hosts_in[z]
    members = rnd.sample(pool, min(len(pool), rnd.randint(3, 25)))
    if groups_in[z] and rnd.random() < 0.2:
        members.append(rnd.choice(groups_in[z]))
    name = f"G_grp{g:04d}"
    groups.append((name, members))
    groups_in[z].append(name)
    objects.append(
        {
            "name": name,
            "category": "address_group",
            "members": members,
            "values": [],
            "family": "v4",
            "vdom": "root",
        }
    )
by_group = dict(groups)
by_net = dict(nets)
ip_of = {n: host_ip(i) for i, n in enumerate(hosts)}


def values_of(name, seen=None):
    seen = seen if seen is not None else set()
    if name in ip_of:
        return [ip_of[name]]
    if name in by_net:
        return [by_net[name]]
    if name in seen:
        return []
    seen.add(name)
    return [v for m in by_group.get(name, []) for v in values_of(m, seen)]


def side(z):
    """Namen einer Regelseite in der Zone: Hosts/Gruppen, manchmal ein Netz, selten all."""
    r = rnd.random()
    if r < 0.05:
        return ["all"]
    if r < 0.25:
        return [rnd.choice(nets_in[z])[0]]
    if r < 0.55:
        return [rnd.choice(groups_in[z])]
    return rnd.sample(hosts_in[z], rnd.randint(1, 4))


rules = []
samples = []  # (Zone Quelle, Zone Ziel, IP Quelle, IP Ziel, Dienst) aus den Regeln
for i in range(N_RULES):
    sz, dz = rnd.randrange(N_ZONES), rnd.randrange(N_ZONES)
    src, dst = side(sz), side(dz)
    szone, dzone = zones[sz], zones[dz]
    ports = [
        f"tcp/{rnd.choice((22, 80, 443, 1433, 3389, 8443)) + rnd.randint(0, 3)}"
        for _ in range(rnd.randint(1, 3))
    ]
    if rnd.random() < 0.05:
        ports = ["all"]
    sv = [v for n in src for v in (["all"] if n == "all" else values_of(n))]
    dv = [v for n in dst for v in (["all"] if n == "all" else values_of(n))]
    a, b = sv[0], dv[0]
    if "/" not in a and "/" not in b and a != "all" and b != "all":
        port = int(ports[0].split("/")[1]) if "/" in ports[0] else 443
        samples.append((sz, dz, a, b, ("tcp", port)))
    rules.append(
        {
            "source_platform": "fortigate",
            "external_id": str(i + 1),
            "name": f"rule-{i + 1:05d}",
            "action": "deny" if rnd.random() < 0.08 else "allow",
            "sources": list(dict.fromkeys(sv)),
            "destinations": list(dict.fromkeys(dv)),
            "services": ports,
            "source_refs": src,
            "destination_refs": dst,
            "src_zone": szone,
            "dst_zone": dzone,
            "vdom": "root",
            "family": "v4",
            "raw": {
                "policyid": i + 1,
                "srcintf": [{"name": szone}],
                "dstintf": [{"name": dzone}],
                "status": "disable" if rnd.random() < 0.03 else "enable",
                "schedule": "always",
            },
        }
    )
payload = {
    "status": "completed",
    "rules": rules,
    "objects": objects,
    "inventory": {
        "devices": [{"name": "fw-01", "role": "firewall"}],
        "interfaces": [
            {
                "device": "fw-01",
                "name": f"p{z}",
                "ips": [f"10.{z}.0.254/16"],
                "zone": zones[z],
                "vdom": "root",
            }
            for z in range(N_ZONES)
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-01", "context": "root"}],
        "routes": [
            {
                "node": "fw-01",
                "context": "root",
                "prefix": f"10.{z}.0.0/16",
                "protocol": "connected",
                "next_hops": [{"interface": f"p{z}"}],
            }
            for z in range(N_ZONES)
        ],
    },
}

tid = ok(c.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=H))["id"]
fw = ok(
    c.post(
        "/systems",
        json={
            "tenant_id": tid,
            "name": "fw-01",
            "platform": "fortigate",
            "base_url": "https://fw.example.ch",
        },
        headers=H,
    )
)["id"]
run_id = ok(
    c.post(
        "/jobs/collect", json={"platform": "fortigate", "tenant_id": tid, "source_system_id": fw}
    )
)["run_id"]
print(
    f"Regeln {N_RULES}, Hosts {N_HOSTS}, Gruppen {len(groups)}, Zonen {N_ZONES}, "
    f"Datenbank {engine.dialect.name}"
)
timed(
    f"Einlesen samt Regelindex ({N_RULES} Regeln)",
    lambda: ok(c.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=payload)),
)

session = get_session_factory()()
from app.models import RuleIpRange  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

print(
    f"Bereiche im Regelindex (rule_ip_range): {session.scalar(select(func.count(RuleIpRange.id)))}"
)

# -- 2./3. Abgleich je Fluss ---------------------------------------------------------------
checker = policy_check.Checker(session, tid)
policies = timed("Policies laden (Checker.policies)", lambda: checker.policies(fw, "root"))
flows = []
for k in range(N_FLOWS):
    if k % 2 and samples:  # die Haelfte aus den Regeln (trifft etwas), sonst zufaellig
        sz, dz, a, b, svc = rnd.choice(samples)
        flows.append((sz, dz, ipaddress.ip_address(a), ipaddress.ip_address(b), svc))
        continue
    a, b = rnd.randrange(N_HOSTS), rnd.randrange(N_HOSTS)
    flows.append(
        (
            a % N_ZONES,
            b % N_ZONES,
            ipaddress.ip_address(host_ip(a)),
            ipaddress.ip_address(host_ip(b)),
            ("tcp", rnd.choice((22, 80, 443, 1433))),
        )
    )


def run_checks():
    verdicts: dict[str, int] = {}
    for sz, dz, src, dst, svc in flows:
        ing = {"interface": f"p{sz}", "zone": zones[sz]}
        eg = {"interface": f"p{dz}", "zone": zones[dz]}
        v = checker.check(fw, "root", ing, eg, src, dst, svc)["verdict"]
        verdicts[v] = verdicts.get(v, 0) + 1
    return verdicts


print(
    "  Urteile:",
    timed(f"Abgleich kalt ({N_FLOWS} Fluesse, Index je Ein-/Ausgang)", run_checks, N_FLOWS),
)
print(
    "  Urteile:",
    profiled("check", lambda: timed(f"Abgleich warm ({N_FLOWS} Fluesse)", run_checks, N_FLOWS)),
)

# zum Vergleich: ohne Vorauswahl nach Interface und Adresse (reiner Durchlauf aller Policies)
ing0 = {"interface": "p0", "zone": zones[0]}
src0, dst0 = ipaddress.ip_address("10.0.200.200"), ipaddress.ip_address("10.1.200.200")
timed(
    "Abgleich ohne Treffer (implizit gesperrt), 200x",
    lambda: [
        checker.check(
            fw, "root", ing0, {"interface": "p1", "zone": zones[1]}, src0, dst0, ("tcp", 9)
        )
        for _ in range(200)
    ],
    200,
)
net0 = ipaddress.ip_network("10.0.0.0/8")
timed(
    "Abgleich mit Netz /8 als Quelle (keine Vorauswahl), 50x",
    lambda: [
        checker.check(
            fw, "root", ing0, {"interface": "p1", "zone": zones[1]}, net0, dst0, ("tcp", 443)
        )
        for _ in range(50)
    ],
    50,
)

# -- 4. Zuordnung Regel <-> Adresse --------------------------------------------------------
sample = [host_ip(rnd.randrange(N_HOSTS)) for _ in range(400)]
covered = profiled(
    "covers",
    lambda: timed(
        "Zuordnung rule_index.covers (400 Adressen einzeln)",
        lambda: [rule_index.covers(session, tid, [ip]) for ip in sample],
        400,
    ),
)
print(
    f"  Regeln je Adresse: Mittel {sum(len(x) for x in covered) / len(covered):.0f}, "
    f"Maximum {max(len(x) for x in covered)}"
)
from app.models import NetworkObject  # noqa: E402

picked = [host_ip(i) for i in rnd.sample(range(N_HOSTS), 5)]
names = list(
    session.scalars(
        select(NetworkObject.name).where(
            NetworkObject.tenant_id == tid, NetworkObject.value.in_(picked)
        )
    )
)
session.close()

# -- 5. Doku und Verbindungen einer Applikation --------------------------------------------
members = [{"kind": "host", "ref": n} for n in names]
app = ok(
    c.post(f"/applications?tenant_id={tid}", json={"name": "App 1", "members": members}, headers=H)
)
doc = profiled(
    "doc",
    lambda: timed(
        "Doku einer Applikation (5 Hosts), kalt",
        lambda: ok(c.get(f"/applications/{app['id']}/doc", params={"tenant_id": tid}, headers=H)),
    ),
)
print(f"  Regeln {doc['summary'].get('rules')}, Treffer {len(doc.get('hits') or [])}")
timed(
    "Doku einer Applikation (5 Hosts), warm",
    lambda: ok(c.get(f"/applications/{app['id']}/doc", params={"tenant_id": tid}, headers=H)),
)
r = timed(
    "Verbindungen der Applikation, kalt",
    lambda: c.get(f"/applications/{app['id']}/connections", params={"tenant_id": tid}, headers=H),
)
print(
    f"  HTTP {r.status_code}",
    (
        r.text[:200]
        if r.status_code != 200
        else f"Verbindungen {len(r.json().get('connections') or [])}"
    ),
)
