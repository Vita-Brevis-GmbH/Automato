"""Verkabelung (0.62.0, ADR 0025): Geraete, Verbindungen, Ports und VLANs aus dem Inventar.

- `GET /cabling/devices`  Geraete (gelesen bzw. nur von Nachbarn gemeldet) mit Ports und
                          Verbindungen.
- `GET /cabling/links`    Verbindungen (beidseitig, einseitig, Gegenstelle nicht gelesen) mit
                          beiden Enden und Abweichungen; Filter Geraet, Zustand, Suche, nur
                          Abweichungen.
- `GET /cabling/ports`    Ports der gelesenen Switches seitenweise; Filter Geraet, Status, VLAN.
- `GET /cabling/vlans`    VLANs mit den Switches, an denen sie als Access bzw. im Trunk liegen.
- `GET /cabling/endpoints` (0.63.0) MACs an den Ports der Switches (ohne Uplinks) mit VM, IPs,
                          IPAM-Namen; Filter Geraet, Port, VLAN, Suche.
- `GET /cabling/locate`   (0.63.0) „Wo haengt …“: MAC, IP oder Name -> Anschluss (Switch,
                          Port, VLAN), sonst hinter welcher Gegenstelle, frueher gesehen.
- `GET /cabling/plan`     (0.64.0) Plan: Geraete nach Ebenen mit Kabeln; mit `vlan` je Kabel,
                          ob es das VLAN durchlaesst, und die Inseln des VLANs.
- `GET /cabling/failure`  (0.64.0) Ausfall eines Geraets bzw. Kabels: was ist dann getrennt?
                          Seit 0.65.0 auch mehrere gleichzeitig (`device`/`link` mehrfach).
- `GET /cabling/plan/export` (0.65.0) Plan als SVG bzw. draw.io.
Lesen = `viewer`, mandantsgescoped; nicht fuer Kunden-Benutzer (Infrastruktur). Rein lesend.
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import InvVlan, MacEntry, SourceSystem, Tenant
from app.models.source_system import Platform
from app.store import cabling as store
from app.store import l2, l2_export, l2_topo

router = APIRouter(prefix="/cabling", tags=["cabling"])
STATES = ("both", "one_sided", "unread")


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "cabling")
    return tid


def _matches(link: store.Link, q: str) -> bool:
    texts = [link.a.device.name, link.a.port, link.b.device.name, link.b.port,
             *(i.get("text") for i in link.issues)]  # fmt: skip
    for end in (link.a, link.b):
        if end.iface is not None:
            texts.append(end.iface.description)
    return any(q in (t or "").casefold() for t in texts)


@router.get("/devices")
def devices(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Geraete mit Anzahl Ports (verbunden), Verbindungen und Abweichungen."""
    tid = _tenant(session, principal, tenant_id)
    cab = store.build(session, tid)
    ports: dict[str, list] = defaultdict(list)
    for p in cab.ports:
        ports[store.device_key(p.device)].append(p)
    links: dict[str, list[store.Link]] = defaultdict(list)
    for link in cab.links.values():
        links[link.a.device.key].append(link)
        links[link.b.device.key].append(link)
    items = []
    for d in cab.devices.values():
        mine = links.get(d.key, [])
        if not d.read and not mine:
            continue  # ESXi-Host ohne Verbindung
        own = ports.get(d.key, [])
        items.append({
            "key": d.key, "name": d.name, "kind": d.kind, "read": d.read, "source": d.source,
            "source_system_id": d.source_system_id, "platform": d.platform, "model": d.model,
            "version": d.version, "mgmt_ip": d.mgmt_ip,
            "ports": sum(1 for p in own if p.kind == "physical"),
            "connected": sum(1 for p in own if p.kind == "physical"
                             and (p.status or "") in ("connected", "up")),
            "links": len(mine),
            "issues": sum(len(link.issues) for link in mine),
            "one_sided": sum(1 for link in mine if link.state == "one_sided"),
        })  # fmt: skip
    order = {"switch": 0, "leaf": 1, "spine": 1, "router": 2, "firewall": 2, "esxi": 4,
             "unknown": 5}  # fmt: skip
    items.sort(key=lambda x: (not x["read"], order.get(x["kind"], 3), x["name"].casefold()))
    summary = {
        "devices": sum(1 for x in items if x["read"]),
        "switches": sum(1 for x in items if x["kind"] == "switch"),
        "routers": sum(1 for x in items if x["kind"] == "router"),  # 0.68.0 (Juniper MX)
        "links": len(cab.links),
        "states": {s: sum(1 for link in cab.links.values() if link.state == s) for s in STATES},
        "issues": sum(len(link.issues) for link in cab.links.values()),
        "unread": sum(1 for x in items if not x["read"]),
        # Endpunkte (0.63.0): MACs an Ports, die keine Uplinks sind
        "endpoints": len({e.mac for e in l2.Locator(session, tid, cab).edge_entries()}),
    }
    return {"summary": summary, "items": items}


_KIND_RANK = {"switch": 0, "leaf": 1, "spine": 1, "controller": 2, "router": 3, "firewall": 3,
              "esxi": 4}  # fmt: skip


def _rank(d: store.Device) -> tuple:
    return (not d.read, _KIND_RANK.get(d.kind, 5), d.name.casefold())


@router.get("/links")
def links(
    tenant_id: int | None = None,
    device: str | None = Query(None, description="nur Verbindungen dieses Geraets (Schluessel)"),
    state: str | None = Query(None, pattern="^(both|one_sided|unread)$"),
    q: str | None = Query(None, max_length=200),
    issues: bool = Query(False, description="nur Verbindungen mit Abweichungen"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(session, principal, tenant_id)
    cab = store.build(session, tid)
    rows = list(cab.links.values())
    key = store.device_key(device) if device else None
    if key:
        rows = [r for r in rows if key in (r.a.device.key, r.b.device.key)]
    for r in rows:  # links das gewaehlte bzw. das gelesene Geraet (Switch vor Leaf vor Firewall)
        if (r.b.device.key == key and r.a.device.key != key) or (
            key is None and _rank(r.b.device) < _rank(r.a.device)
        ):
            r.a, r.b = r.b, r.a
    if state:
        rows = [r for r in rows if r.state == state]
    if issues:
        rows = [r for r in rows if r.issues]
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [r for r in rows if _matches(r, needle)]
    rows.sort(key=lambda r: (not r.issues, r.state != "one_sided", r.a.device.name.casefold(),
                             store.port_key(r.a.port)))  # fmt: skip
    return {"total": len(rows), "items": [r.out() for r in rows[offset : offset + limit]]}


@router.get("/ports")
def ports(
    tenant_id: int | None = None,
    device: str | None = None,
    status: str | None = Query(None, pattern="^(up|down|disabled)$"),
    vlan: int | None = Query(None, ge=1, le=4094),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Ports der gelesenen Switches (Quellsysteme `cisco_switch`) mit Verbindung."""
    tid = _tenant(session, principal, tenant_id)
    cab = store.build(session, tid)
    macs: dict[tuple[str, str], int] = defaultdict(int)  # Endpunkte je Port (0.63.0)
    for e in l2.Locator(session, tid, cab).edge_entries():
        macs[(store.device_key(e.device), store.port_key(e.port))] += 1
    peer: dict[tuple[str, str], tuple[store.Link, store.End]] = {}
    for link in cab.links.values():
        peer[link.a.key] = (link, link.b)
        peer[link.b.key] = (link, link.a)
    rows = [p for p in cab.ports if p.source_system_id in cab.switch_sources]
    if device:
        key = store.device_key(device)
        rows = [p for p in rows if store.device_key(p.device) == key]
    if status:
        up = {"connected", "up"}
        rows = [p for p in rows if (status == "up") == ((p.status or "") in up)
                and (status != "disabled" or p.enabled is False)]  # fmt: skip
    if vlan:

        def carries(p) -> bool:
            if p.mode == "access":
                return p.untagged_vlan == vlan
            vs = store.vlan_set(p.trunk_vlans)
            return bool(vs and vlan in vs) or p.native_vlan == vlan

        rows = [p for p in rows if carries(p)]
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [p for p in rows if any(needle in (t or "").casefold()
                                       for t in (p.name, p.description, p.neighbor,
                                                 p.device))]  # fmt: skip
    rows.sort(key=lambda p: (p.device.casefold(), _port_order(p.name)))
    # Spanning Tree und vPC stehen am Port-Channel - Mitglieder zeigen die Werte ihres Channels
    by_port = {(store.device_key(p.device), store.port_key(p.name)): p.attrs or {}
               for p in cab.ports}  # fmt: skip
    out = []
    for p in rows[offset : offset + limit]:
        link, other = peer.get((store.device_key(p.device), store.port_key(p.name)), (None, None))
        own = p.attrs or {}
        lag = by_port.get((store.device_key(p.device), store.port_key(p.lag)), {}) if p.lag else {}
        out.append({
            "device": p.device, "name": p.name, "kind": p.kind, "status": p.status,
            "enabled": p.enabled, "speed": p.speed, "duplex": p.duplex, "mode": p.mode,
            "vlan": p.untagged_vlan, "native_vlan": p.native_vlan, "trunk_vlans": p.trunk_vlans,
            "lag": p.lag, "description": p.description, "media": own.get("media"),
            "vpc": own.get("vpc") or lag.get("vpc"),
            "vpc_peer_link": own.get("vpc_peer_link") or lag.get("vpc_peer_link"),
            "stp_blocked": own.get("stp_blocked") or lag.get("stp_blocked"),
            "stp_root": own.get("stp_root") or lag.get("stp_root"),
            "peer":({"device": other.device.name, "port": other.port, "read": other.device.read,
                      "kind": other.device.kind} if other is not None else None),
            "link_state": link.state if link is not None else None,
            "issues": [i["text"] for i in link.issues] if link is not None else [],
            "endpoints": macs.get((store.device_key(p.device), store.port_key(p.name)), 0),
        })  # fmt: skip
    return {"total": len(rows), "items": out}


def _port_order(name: str) -> tuple:
    """Gi1/0/2 vor Gi1/0/10: Zahlen numerisch."""
    import re

    return tuple(int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", name))


@router.get("/vlans")
def vlans(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """VLANs der Switches: Name, je Switch Anzahl Access-Ports und Trunks mit dem VLAN."""
    tid = _tenant(session, principal, tenant_id)
    cab = store.build(session, tid)
    switch_ids = cab.switch_sources
    names: dict[int, set[str]] = defaultdict(set)
    if switch_ids:
        rows = session.execute(
            select(InvVlan.vid, InvVlan.name).where(InvVlan.tenant_id == tid,
                                                    InvVlan.source_system_id.in_(switch_ids))
        )  # fmt: skip
        for vid, name in rows:
            names[vid].add(name)
    use: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for p in cab.ports:
        if p.source_system_id not in switch_ids or p.kind != "physical":
            continue
        if p.mode == "access" and p.untagged_vlan:
            use[p.untagged_vlan][p.device][0] += 1
            names.setdefault(p.untagged_vlan, set())
        elif p.mode == "tagged":
            for vid in store.vlan_set(p.trunk_vlans) or set():
                if vid in names:  # nur VLANs, die ein Switch kennt (nicht 1-4094 aufblaehen)
                    use[vid][p.device][1] += 1
    return [
        {"vid": vid, "names": sorted(names[vid]),
         "devices": [{"device": d, "access": a, "trunks": t}
                     for d, (a, t) in sorted(use.get(vid, {}).items())]}
        for vid in sorted(names)
    ]  # fmt: skip


@router.get("/sources")
def sources(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Quellsysteme der Switches (fuer den Hinweis „noch keine Switches“)."""
    tid = _tenant(session, principal, tenant_id)
    rows = session.scalars(
        select(SourceSystem).where(SourceSystem.tenant_id == tid,
                                   SourceSystem.platform == Platform.CISCO_SWITCH)
    )  # fmt: skip
    return [{"id": s.id, "name": s.name, "switches": len(s.read_scope.split())} for s in rows]


def _endpoint_text(loc: l2.Locator, e: MacEntry) -> str:
    ident = loc.identity(e.mac)
    iface = loc.iface(e)
    vm = ident.vm or {}
    return " ".join(str(t) for t in (e.mac, e.device, e.port, e.vlan, vm.get("name"),
                                      vm.get("host"), *ident.ips, *ident.epgs,
                                      iface.description if iface is not None else None)
                    if t).casefold()  # fmt: skip


@router.get("/endpoints")
def endpoints(
    tenant_id: int | None = None,
    device: str | None = None,
    port: str | None = Query(None, max_length=200),
    vlan: int | None = Query(None, ge=1, le=4094),
    q: str | None = Query(None, max_length=200),
    uplinks: bool = Query(False, description="auch Eintraege auf Uplinks"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """MACs an den Ports der Switches (aktueller Stand je Switch), ohne Uplinks."""
    tid = _tenant(session, principal, tenant_id)
    loc = l2.Locator(session, tid)
    conds = [MacEntry.vlan == vlan] if vlan else []
    rows = loc.current(*conds)
    if not uplinks:
        rows = [e for e in rows if not loc.is_uplink(e)]
    if device:
        key = store.device_key(device)
        rows = [e for e in rows if store.device_key(e.device) == key]
    if port:
        pkey = store.port_key(port)
        rows = [e for e in rows if store.port_key(e.port) == pkey]
    if q and q.strip():
        needle = q.strip().casefold()
        mac = l2.mac_key(needle)
        rows = [e for e in rows if (mac and e.mac == mac) or needle in _endpoint_text(loc, e)]
    rows.sort(key=lambda e: (e.device.casefold(), _port_order(e.port), e.vlan or 0, e.mac))
    summary = {
        "macs": len({e.mac for e in rows}),
        "ports": len({(e.device, e.port) for e in rows}),
        "vms": sum(1 for m in {e.mac for e in rows} if loc.identity(m).vm),
        "with_ip": sum(1 for m in {e.mac for e in rows} if loc.identity(m).ips),
    }
    return {"total": len(rows), "summary": summary,
            "items": [loc.row(e) for e in rows[offset : offset + limit]]}  # fmt: skip


@router.get("/locate")
def locate(
    q: str = Query(..., min_length=2, max_length=200),
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Wo haengt …: MAC, IP oder Name (VM, IPAM) -> je MAC Anschluss, Gegenstelle, frueher."""
    tid = _tenant(session, principal, tenant_id)
    found = l2.find_macs(session, tid, q)
    loc = l2.Locator(session, tid)
    items = [{**loc.locate(mac), "reason": why} for mac, why in found["macs"].items()]
    items.sort(key=lambda x: (not x["locations"], not x["behind"], x["mac"]))
    return {"query": q, "kind": found["kind"], "ips": found["ips"], "items": items}


@router.get("/plan")
def plan(
    tenant_id: int | None = None,
    ends: bool = Query(True, description="auch Endgeraete (ESXi-Hosts, Firewalls, nicht gelesene)"),
    vlan: int | None = Query(None, ge=1, le=4094),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Plan der Verkabelung: Knoten mit Ebene und Lage, Kanten je Geraetepaar."""
    tid = _tenant(session, principal, tenant_id)
    return l2_topo.L2(session, tid).plan(ends=ends, vlan=vlan)


@router.get("/failure")
def failure(
    tenant_id: int | None = None,
    device: Annotated[list[str] | None, Query(description="Geraet (mehrfach)")] = None,
    link: Annotated[list[str] | None, Query(description="Kabel/Link-ID (mehrfach)")] = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Ausfall simulieren: ohne die Geraete (Name bzw. Schluessel) und Kabel (Link-ID) - auch
    mehrere gleichzeitig (0.65.0)."""
    devices = [d for d in device or [] if d.strip()]
    links = [lk for lk in link or [] if lk.strip()]
    if not devices and not links:
        raise HTTPException(status_code=422, detail="device oder link angeben")
    if len(devices) + len(links) > 50 or any(len(x) > 1000 for x in devices + links):
        raise HTTPException(status_code=422, detail="zu viele bzw. zu lange Angaben")
    tid = _tenant(session, principal, tenant_id)
    topo = l2_topo.L2(session, tid)
    if any(store.device_key(d) not in topo.devices for d in devices):
        raise HTTPException(status_code=404, detail="Gerät unbekannt")
    if any(lk not in topo.cab.links for lk in links):
        raise HTTPException(status_code=404, detail="Kabel unbekannt")
    return topo.failure(devices=devices, links=links)


_EXPORT = {"svg": ("image/svg+xml", "svg"), "drawio": ("application/xml", "drawio")}


@router.get("/plan/export")
def export_plan(
    format: str = Query("svg", pattern="^(svg|drawio)$"),  # noqa: A002 - Name der Abfrage
    tenant_id: int | None = None,
    ends: bool = True,
    vlan: int | None = Query(None, ge=1, le=4094),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Plan der Verkabelung als Datei (wie in der Ansicht, mit Endgeraeten bzw. VLAN)."""
    tid = _tenant(session, principal, tenant_id)
    plan = l2_topo.L2(session, tid).plan(ends=ends, vlan=vlan)
    tenant = session.get(Tenant, tid)
    title = f"Verkabelung {tenant.name}" if tenant else "Verkabelung"
    if vlan:
        title += f" – VLAN {vlan}"
    when = datetime.now(UTC)
    body = (l2_export.svg if format == "svg" else l2_export.drawio)(plan, title, when)
    media, ext = _EXPORT[format]
    name = f"verkabelung-{when:%Y-%m-%d}{f'-vlan{vlan}' if vlan else ''}.{ext}"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip
