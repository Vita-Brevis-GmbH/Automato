"""NetBox-Sync (ADR 0010): Vorschau -> Uebernahme gegen eine simulierte NetBox.

Die Fake-NetBox bildet das Noetige der REST-API nach: seitenweises Lesen mit Filtern,
Bulk-POST/-PATCH/-DELETE, verschachtelte Referenzen (`{"id", "name", "slug"}`), Auswahlfelder
(`{"value"}`) und Tags.
"""

import json

import httpx
import pytest
from app.api import netbox_sync as api
from app.models import AuditLog
from sot_gateway.adapters.netbox_writer import NetBoxWriter

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}

_FK = {
    "tenant": "tenancy/tenants",
    "site": "dcim/sites",
    "group": "ipam/vlan-groups",
    "vlan": "ipam/vlans",
    "untagged_vlan": "ipam/vlans",
    "tagged_vlans": "ipam/vlans",
    "vrf": "ipam/vrfs",
    "device": "dcim/devices",
    "role": "dcim/device-roles",
    "device_type": "dcim/device-types",
    "manufacturer": "dcim/manufacturers",
    "lag": "dcim/interfaces",
    "parent": "dcim/interfaces",
    "cluster": "virtualization/clusters",
    "virtual_machine": "virtualization/virtual-machines",
}
_FK_AT = {  # gleiches Feld, anderes Ziel je Endpunkt
    ("dcim/sites", "group"): "dcim/site-groups",
    ("virtualization/clusters", "group"): "virtualization/cluster-groups",
    ("virtualization/clusters", "type"): "virtualization/cluster-types",
}
_CHOICE = ("status", "mode", "type")


class FakeNetBox:
    def __init__(self) -> None:
        self.db: dict[str, dict[int, dict]] = {}
        self.next_id = 1
        self.writes: list[tuple[str, str, int]] = []
        self.fail_on: tuple[str, str] | None = None  # (Methode, Endpunkt) -> HTTP 400

    def objects(self, endpoint: str) -> list[dict]:
        return list(self.db.get(endpoint, {}).values())

    def find(self, endpoint: str, **match) -> dict | None:
        for o in self.objects(endpoint):
            if all(o.get(k) == v for k, v in match.items()):
                return o
        return None

    def _nested(self, endpoint: str, ident):
        if ident is None:
            return None
        o = self.db.get(endpoint, {}).get(int(ident))
        if o is None:
            raise ValueError(f"{endpoint} {ident} fehlt")
        return {k: o[k] for k in ("id", "name", "slug", "vid", "model") if k in o}

    def _store(self, endpoint: str, raw: dict, current: dict | None = None) -> dict:
        obj = dict(current or {})
        for k, v in raw.items():
            if k == "tags":
                obj["tags"] = [{"slug": t["slug"], "name": t["slug"]} for t in v]
            elif k in _FK or (endpoint, k) in _FK_AT:
                target = _FK_AT.get((endpoint, k)) or _FK[k]
                if isinstance(v, list):
                    obj[k] = [self._nested(target, i) for i in v]
                else:
                    obj[k] = self._nested(target, v)
            elif k in _CHOICE:
                obj[k] = {"value": v, "label": v} if v else None
            else:
                obj[k] = v
        obj.setdefault("tags", [])
        return obj

    def _duplicate(self, endpoint: str, raw: dict) -> bool:
        """Eindeutigkeit wie NetBox: VLAN-Name je Gruppe, Site-Name global."""
        name = str(raw.get("name") or "").lower()
        for o in self.objects(endpoint):
            same = str(o.get("name") or "").lower() == name
            if (
                endpoint == "ipam/vlans"
                and same
                and (o.get("group") or {}).get("id") == raw.get("group")
            ):
                return True
            if endpoint == "dcim/sites" and same:
                return True
        return False

    def add(self, endpoint: str, raw: dict) -> dict:
        obj = self._store(endpoint, raw)
        obj["id"] = self.next_id
        self.next_id += 1
        self.db.setdefault(endpoint, {})[obj["id"]] = obj
        return obj

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/").strip("/")
        if path == "status":
            return httpx.Response(200, json={"netbox-version": "4.6.10"})
        if self.fail_on == (request.method, path):
            return httpx.Response(400, json=[{"address": ["Duplicate IP address"]}])
        if request.method == "GET":
            params = request.url.params
            rows = self.objects(path)
            if "slug" in params:
                rows = [o for o in rows if o.get("slug") == params["slug"]]
            for fk in ("group", "device", "virtual_machine"):
                ids = params.get_list(f"{fk}_id")
                if ids:
                    rows = [o for o in rows if str((o.get(fk) or {}).get("id")) in ids]
            off, lim = int(params.get("offset", 0)), int(params.get("limit", 50))
            return httpx.Response(200, json={"count": len(rows), "results": rows[off : off + lim]})
        body = json.loads(request.content)
        self.writes.append((request.method, path, len(body)))
        if request.method == "POST":
            for o in body:
                if self._duplicate(path, o):
                    return httpx.Response(
                        500, json={"error": "duplicate key value violates unique constraint"}
                    )
            return httpx.Response(201, json=[self.add(path, o) for o in body])
        if request.method == "PATCH":
            table = self.db[path]
            for o in body:
                table[o["id"]] = self._store(path, o, table[o["id"]])
            return httpx.Response(200, json=[table[o["id"]] for o in body])
        if request.method == "DELETE":
            for o in body:
                del self.db[path][o["id"]]
            return httpx.Response(204)
        return httpx.Response(405)


@pytest.fixture
def netbox(monkeypatch):
    return fake_netbox(monkeypatch)


def fake_netbox(monkeypatch) -> FakeNetBox:
    """Fake-NetBox statt des echten Schreibers (auch fuer andere Testmodule)."""
    fake = FakeNetBox()
    seen_tokens = []

    def make_writer(target):
        token = api.credentials.decrypt(target.credentials).get("write_token")
        if not token:
            raise api.HTTPException(status_code=422, detail="netbox source has no write token")
        seen_tokens.append(token)
        client = httpx.Client(transport=httpx.MockTransport(fake.handler))
        return NetBoxWriter(target.base_url, token, client=client)

    monkeypatch.setattr(api, "make_writer", make_writer)
    fake.seen_tokens = seen_tokens
    return fake


def _setup(client, *, write_token="nb-write") -> dict:  # noqa: S107 - Test-Token
    tid = client.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=ADMIN).json()[
        "id"
    ]
    site = client.post(
        f"/tenants/{tid}/sites", json={"code": "ZRH", "name": "Zürich"}, headers=ADMIN
    ).json()["id"]

    def system(**body):
        r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    ids = {
        "tid": tid,
        "site": site,
        "fw": system(name="fw-01", platform="fortigate", site_id=site),
        "apic": system(name="apic-01", platform="cisco_aci"),
        "ddi": system(name="ddi", platform="infoblox", username="u", password="p"),
        "nb": system(
            name="netbox",
            platform="netbox",
            base_url="https://nb.kunde.lan",
            token="nb-read",
            **({"write_token": write_token} if write_token else {}),
        ),
    }
    _references(client, ids, DDI)
    _result(client, ids, "fw", "fortigate", {"inventory": FW_INVENTORY})
    _result(client, ids, "apic", "cisco_aci", {"inventory": ACI_INVENTORY})
    return ids


def _collect(client, ids, key, platform) -> str:
    body = {"platform": platform, "tenant_id": ids["tid"], "source_system_id": ids[key]}
    return client.post("/jobs/collect", json=body).json()["run_id"]


def _references(client, ids, records) -> None:
    run_id = _collect(client, ids, "ddi", "infoblox")
    client.post(
        f"/internal/collector-runs/{run_id}/references", headers=TOKEN, json={"records": records}
    )
    result = {"status": "completed", "reference_count": len(records)}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.json()["status"] == "completed", r.text


def _result(client, ids, key, platform, result) -> None:
    run_id = _collect(client, ids, key, platform)
    r = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers=TOKEN,
        json={"status": "completed", **result},
    )
    assert r.json()["status"] == "completed", r.text


DDI = [
    {"kind": "prefix", "value": "10.10.10.0/24", "description": "Server"},
    {"kind": "prefix", "value": "10.10.20.0/24", "description": "DMZ"},
    {"kind": "prefix", "value": "10.10.0.0/16", "description": "RZ", "status": "container"},
    {"kind": "range", "value": "10.10.10.100-10.10.10.150", "description": "DHCP"},
    {"kind": "host", "value": "10.10.10.5", "dns_name": "app01.example.ch"},
    {"kind": "host", "value": "10.10.10.1", "dns_name": "fw-01-srv.example.ch"},
    {"kind": "ptr_record", "value": "10.10.10.6", "dns_name": "db01.example.ch"},
]
FW_INVENTORY = {
    "devices": [{"name": "fw-01", "role": "firewall", "model": "FGVM64", "serial": "FGV1"}],
    "interfaces": [
        {"device": "fw-01", "name": "port3", "kind": "physical", "mode": "tagged",
         "tagged_vlans": [10]},
        {"device": "fw-01", "name": "agg1", "kind": "lag"},
        {"device": "fw-01", "name": "port4", "kind": "physical", "lag": "agg1"},
        {"device": "fw-01", "name": "srv-vl10", "kind": "vlan", "parent": "port3",
         "mode": "access", "untagged_vlan": 10, "ips": ["10.10.10.1/24"], "zone": "SRV"},
    ],
    "vlans": [{"vid": 10, "name": "Server"}],
    "subnets": [
        {"prefix": "10.10.10.0/24", "gateway": "10.10.10.1/24", "vlan": 10,
         "context": "interface", "context_name": "srv-vl10"},
    ],
}  # fmt: skip
ACI_INVENTORY = {
    "devices": [{"name": "leaf-101", "role": "leaf", "model": "N9K-C93180YC-FX", "node_id": "101"}],
    "interfaces": [
        {"device": "leaf-101", "name": "eth1/1", "kind": "physical", "mode": "tagged",
         "tagged_vlans": [110], "epgs": ["TE/AP/EG_web"], "neighbor": "esx01"},
    ],
    "vlans": [{"vid": 110, "name": "EG_web", "epgs": ["TE/AP/EG_web"]},
              {"vid": 111, "name": "EG_web", "epgs": ["TE/AP/EG_web"]}],
    "subnets": [
        {"prefix": "10.20.0.0/24", "gateway": "10.20.0.1/24", "vrf": "TE:VRF", "vlan": 110,
         "context": "bd", "context_name": "BD_app", "aci_tenant": "TE", "ap": "AP",
         "epg": "EG_web"},
    ],
    "vrfs": ["TE:VRF"],
}  # fmt: skip


def _plan(client, ids, **opts) -> dict:
    body = {"tenant_id": ids["tid"], "source_system_id": ids["nb"], **opts}
    r = client.post("/netbox-sync/plans", json=body, headers=ADMIN)
    assert r.status_code == 202, r.text
    plan = client.get(f"/netbox-sync/plans/{r.json()['id']}", headers=ADMIN).json()
    assert plan["status"] == "ready", plan
    return plan


def _items(client, plan_id, **params) -> list[dict]:
    r = client.get(f"/netbox-sync/plans/{plan_id}/items", params=params, headers=ADMIN)
    return r.json()["rows"]


def _apply(client, plan_id) -> dict:
    r = client.post(f"/netbox-sync/plans/{plan_id}/apply", headers=ADMIN)
    assert r.status_code == 202, r.text
    return client.get(f"/netbox-sync/plans/{plan_id}", headers=ADMIN).json()


def _tagged(obj) -> bool:
    return any(t["slug"] == "automato" for t in obj["tags"])


def test_vorschau_uebernahme_und_konflikt(client, netbox):
    # von Hand gepflegt: Standort (gleicher Slug) und ein Netz mit eigener Beschreibung
    netbox.add("dcim/sites", {"name": "Zürich", "slug": "zrh", "status": "active"})
    netbox.add("ipam/prefixes", {"prefix": "10.10.20.0/24", "description": "von Hand"})
    ids = _setup(client)

    plan = _plan(client, ids)
    assert not netbox.writes  # Vorschau liest nur
    assert plan["totals"]["conflict"] == 1 and plan["totals"]["create"] > 10
    assert plan["can_apply"] and plan["warnings"] == []
    (conflict,) = _items(client, plan["id"], action="conflict")
    assert conflict["key"] == "10.10.20.0/24" and conflict["manual"]
    changes = {c["field"]: c for c in conflict["changes"]}
    assert changes["description"]["old"] == "von Hand" and "tenant" in changes
    kinds = {i["kind"] for i in _items(client, plan["id"], action="create", limit=500)}
    assert {"vlan", "interface", "device", "prefix", "ip_range", "ip_address", "vrf"} <= kinds

    done = _apply(client, plan["id"])
    assert done["status"] == "applied", done
    assert done["result"]["interface"]["create"] == 5

    vlan = netbox.find("ipam/vlans", vid=10)
    assert vlan["group"]["slug"] == "kunde-a-fw-01" and _tagged(vlan)
    # zwei VLANs derselben EPG: NetBox verlangt eindeutige Namen je Gruppe (Dev-Befund)
    assert netbox.find("ipam/vlans", vid=110)["name"] == "EG_web (110)"
    assert netbox.find("ipam/vlans", vid=111)["name"] == "EG_web (111)"
    assert vlan["tenant"]["slug"] == "kunde-a"
    svi = netbox.find("dcim/interfaces", name="srv-vl10")
    assert svi["mode"]["value"] == "access" and svi["untagged_vlan"]["vid"] == 10
    assert svi["parent"]["name"] == "port3" and svi["type"]["value"] == "virtual"
    assert netbox.find("dcim/interfaces", name="port4")["lag"]["name"] == "agg1"
    trunk = netbox.find("dcim/interfaces", name="eth1/1")
    assert [v["vid"] for v in trunk["tagged_vlans"]] == [110] and "EG_web" in trunk["description"]
    leaf = netbox.find("dcim/devices", name="leaf-101")
    assert leaf["site"]["slug"] == "zrh"  # einziger Standort des Mandanten
    gw = netbox.find("ipam/ip-addresses", address="10.10.10.1/24")
    assert gw["assigned_object_id"] == svi["id"] and gw["dns_name"] == "fw-01-srv.example.ch"
    assert (
        netbox.find("ipam/ip-addresses", address="10.10.10.6/24")["dns_name"] == "db01.example.ch"
    )
    aci_net = netbox.find("ipam/prefixes", prefix="10.20.0.0/24")
    assert aci_net["vrf"]["name"] == "TE:VRF" and aci_net["vlan"]["vid"] == 110
    assert "BD BD_app" in aci_net["description"]
    assert netbox.find("ipam/prefixes", prefix="10.10.10.0/24")["description"].startswith("Server")
    # Network Container der DDI bleibt Container (0.74.1)
    assert netbox.find("ipam/prefixes", prefix="10.10.0.0/16")["status"]["value"] == "container"
    assert netbox.find("ipam/ip-ranges", description="DHCP · Spiegel ddi")["start_address"] == (
        "10.10.10.100/24"
    )
    # manuelle Objekte unveraendert und ohne Tag
    manual = netbox.find("ipam/prefixes", prefix="10.10.20.0/24")
    assert manual["description"] == "von Hand" and not _tagged(manual)
    assert len([s for s in netbox.objects("dcim/sites") if s["slug"] == "zrh"]) == 1
    # nur das Schreib-Token, NetBox wird danach neu eingelesen
    assert set(netbox.seen_tokens) == {"nb-write"}
    assert client.dispatched[-1][0] == "netbox"

    again = _plan(client, ids)  # idempotent: nichts mehr zu schreiben
    assert again["totals"]["create"] == again["totals"]["update"] == 0
    assert again["totals"]["conflict"] == 1 and not again["can_apply"]
    assert client.post(f"/netbox-sync/plans/{again['id']}/apply", headers=ADMIN).status_code == 409


def test_aenderung_und_loeschen_nur_mit_freigabe(client, netbox):
    ids = _setup(client)
    assert _apply(client, _plan(client, ids)["id"])["status"] == "applied"
    # IP verschwindet aus der DDI, Beschreibung eines Netzes aendert sich
    changed = [r for r in DDI if r["value"] not in ("10.10.10.6", "10.10.20.0/24")]
    _references(client, ids, [*changed, {"kind": "prefix", "value": "10.10.20.0/24",
                                         "description": "DMZ neu"}])  # fmt: skip
    plan = _plan(client, ids)
    (obsolete,) = _items(client, plan["id"], action="obsolete")
    assert obsolete["key"] == "10.10.10.6"
    assert {i["key"] for i in _items(client, plan["id"], action="delete")} == set()
    (update,) = _items(client, plan["id"], action="update")
    assert update["key"] == "10.10.20.0/24" and update["changes"][0]["new"].startswith("DMZ neu")

    plan = _plan(client, ids, allow_delete=True)
    assert [i["key"] for i in _items(client, plan["id"], action="delete")] == ["10.10.10.6"]
    assert _apply(client, plan["id"])["status"] == "applied"
    assert netbox.find("ipam/ip-addresses", address="10.10.10.6/24") is None
    assert netbox.find("ipam/prefixes", prefix="10.10.20.0/24")["description"].startswith("DMZ neu")


def test_uebernahme_bricht_ab_wenn_netbox_sich_geaendert_hat(client, netbox):
    ids = _setup(client)
    plan = _plan(client, ids)
    netbox.add("ipam/vlans", {"vid": 999, "name": "neu"})  # fremde Aenderung, ohne Bezug
    netbox.add("ipam/prefixes", {"prefix": "10.20.0.0/24", "description": "inzwischen da"})
    done = _apply(client, plan["id"])
    assert done["status"] == "failed" and "geändert" in done["message"]
    assert not netbox.writes  # nichts geschrieben


def test_fehler_von_netbox_wird_am_plan_gemeldet(client, netbox, session_factory):
    ids = _setup(client)
    plan = _plan(client, ids)
    netbox.fail_on = ("POST", "ipam/ip-addresses")
    done = _apply(client, plan["id"])
    assert done["status"] == "failed" and "Duplicate IP address" in done["message"]
    s = session_factory()
    logs = {a.action for a in s.query(AuditLog).all()}
    s.close()
    assert {"netbox_sync.planned", "netbox_sync.applied"} <= logs
    assert client.dispatched[-1][0] == "netbox"  # Teilstand -> neu einlesen


def test_nur_admin_und_nur_mit_schreib_token(client, netbox):
    ids = _setup(client, write_token=None)
    body = {"tenant_id": ids["tid"], "source_system_id": ids["nb"]}
    for role in ("viewer", "operator"):
        r = client.post("/netbox-sync/plans", json=body, headers={"X-Dev-Roles": role})
        assert r.status_code == 403
    assert client.post("/netbox-sync/plans", json=body, headers=ADMIN).status_code == 422
    targets = client.get(f"/netbox-sync/targets?tenant_id={ids['tid']}", headers=ADMIN).json()
    assert [(t["name"], t["has_write_token"]) for t in targets] == [("netbox", False)]
    fw = {**body, "source_system_id": ids["fw"]}
    assert client.post("/netbox-sync/plans", json=fw, headers=ADMIN).status_code == 422
    # Standort eines anderen Mandanten am Quellsystem -> 422
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    foreign = client.post(
        f"/tenants/{other}/sites", json={"code": "GVA", "name": "Genf"}, headers=ADMIN
    ).json()["id"]
    r = client.patch(f"/systems/{ids['fw']}", json={"site_id": foreign}, headers=ADMIN)
    assert r.status_code == 422
    # Standort loeschen loest die Zuordnung am Quellsystem
    client.delete(f"/tenants/{ids['tid']}/sites/{ids['site']}", headers=ADMIN)
    fw_out = next(s for s in client.get("/systems", headers=ADMIN).json() if s["id"] == ids["fw"])
    assert fw_out["site_id"] is None


def test_ohne_standort_keine_geraete(client, netbox):
    ids = _setup(client)
    client.post(f"/tenants/{ids['tid']}/sites", json={"code": "BRN", "name": "Bern"}, headers=ADMIN)
    plan = _plan(client, ids)  # zwei Standorte: APIC ohne Standort -> Hinweis
    assert any("apic-01" in w and "Standort" in w for w in plan["warnings"])
    devices = {i["key"] for i in _items(client, plan["id"], kind="device")}
    assert devices == {"fw-01"}


def test_haengender_sync_wird_beendet_und_ist_abbrechbar(client, netbox, session_factory):
    """Dev-Befund 0.11.4: nach einem Neustart des Orchestrators stand ein Plan seit gestern auf
    "wird übernommen" und sperrte jeden weiteren Sync."""
    from datetime import UTC, datetime, timedelta

    from app.api import netbox_sync as nb_api
    from app.models import SyncPlan

    ids = _setup(client)
    plan = _plan(client, ids)

    def set_plan(**values):
        s = session_factory()
        p = s.get(SyncPlan, plan["id"])
        for k, v in values.items():
            setattr(p, k, v)
        s.commit()
        s.close()

    # Hintergrundaufgabe mit dem alten Prozess gestorben: beim Start beendet
    set_plan(status="applying", heartbeat_at=datetime.now(UTC))
    assert nb_api.abort_interrupted(session_factory) == 1
    out = client.get(f"/netbox-sync/plans/{plan['id']}", headers=ADMIN).json()
    assert out["status"] == "failed" and "neu gestartet" in out["message"]
    assert "Teilstand" in out["message"]

    # ohne Lebenszeichen nach 30 min: beim naechsten Blick beendet, neuer Sync moeglich
    set_plan(status="applying", heartbeat_at=datetime.now(UTC) - timedelta(minutes=31))
    out = client.get(f"/netbox-sync/plans/{plan['id']}", headers=ADMIN).json()
    assert out["status"] == "failed" and "kein Lebenszeichen seit 30 min" in out["message"]
    fresh = _plan(client, ids)

    # von Hand: nur laufende Plaene
    r = client.post(f"/netbox-sync/plans/{fresh['id']}/cancel", headers=ADMIN)
    assert r.status_code == 409
    set_plan(status="planning")
    r = client.post(f"/netbox-sync/plans/{plan['id']}/cancel", headers=ADMIN)
    assert r.status_code == 200 and r.json()["message"].startswith("abgebrochen von ")


def test_abbruch_waehrend_der_uebernahme_stoppt_vor_der_naechsten_art(
    client, netbox, session_factory
):
    from app.models import SyncPlan

    ids = _setup(client)
    plan = _plan(client, ids)
    handler = netbox.handler

    def cancel_after_tags(request):
        response = handler(request)
        if request.method == "POST" and "extras/tags" in request.url.path:
            s = session_factory()
            s.get(SyncPlan, plan["id"]).status = "failed"  # wie POST .../cancel
            s.commit()
            s.close()
        return response

    netbox.handler = cancel_after_tags
    done = _apply(client, plan["id"])
    assert done["status"] == "failed"
    written = {path for _, path, _ in netbox.writes}
    assert written == {"extras/tags"}  # danach nichts mehr geschrieben
