"""Avi-Objekte -> publizierte Dienste (ADR 0027): je Virtual Service, VIP-Adresse und Port ein
NAT-Eintrag wie der Virtual Server der FortiGate (externe IP:Port -> Server des Pools).

Ausgewertet wird nur, was die Doku braucht (Positivliste der Felder): Namen, Adressen, Ports,
Pools mit Servern, Methode, Health-Monitor-Namen, FQDN, Tenant/Cloud/VRF, Zustand und ob die
Service Engine die Quelle ersetzt (SNAT) oder die Client-IP erhaelt.
"""

import ipaddress
from typing import Any

from collectors.common.dto import NormalizedNat

SLB = "server-load-balance"  # gleiche Art wie der Virtual Server der FortiGate


def ref_uuid(ref: str | None) -> str | None:
    """`https://ctrl/api/pool/pool-1#web` -> `pool-1`."""
    if not ref:
        return None
    return ref.split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1] or None


def ref_name(ref: str | None) -> str | None:
    """Name aus dem Verweis (`include_name=true`: `…#web`)."""
    return ref.split("#", 1)[1] or None if ref and "#" in ref else None


def _addr(value: Any) -> str | None:
    """`{"addr": "10.0.0.1", "type": "V4"}` bzw. Text -> normierte IP."""
    raw = value.get("addr") if isinstance(value, dict) else value
    try:
        return ipaddress.ip_address(str(raw).strip()).compressed if raw else None
    except ValueError:
        return None


def _by_uuid(rows: list[dict]) -> dict[str, dict]:
    return {r["uuid"]: r for r in rows if isinstance(r, dict) and r.get("uuid")}


def vip_addresses(vsvip: dict | None, vs: dict) -> list[tuple[str, bool, bool]]:
    """(Adresse, Floating, aktiv) aus dem VS-VIP-Objekt bzw. (aeltere Versionen) der VS."""
    entries = (vsvip or {}).get("vip") or vs.get("vip") or []
    out: list[tuple[str, bool, bool]] = []
    for v in entries if isinstance(entries, list) else []:
        enabled = v.get("enabled", True) is not False
        for key, floating in (("ip_address", False), ("ip6_address", False),
                              ("floating_ip", True), ("floating_ip6", True)):  # fmt: skip
            ip = _addr(v.get(key))
            if ip and (ip, floating, enabled) not in out:
                out.append((ip, floating, enabled))
    return out


def _ports(vs: dict) -> list[tuple[str, bool]]:
    """(Port bzw. Bereich a-b, SSL) je Service des Virtual Service."""
    out: list[tuple[str, bool]] = []
    for s in vs.get("services") or []:
        port = s.get("port")
        if port in (None, ""):
            continue
        end = s.get("port_range_end")
        text = f"{port}-{end}" if end and str(end) != str(port) else str(port)
        out.append((text, bool(s.get("enable_ssl"))))
    return out


def _servers(pool: dict, priority: Any = None) -> list[dict[str, Any]]:
    out = []
    default_port = pool.get("default_server_port")
    for srv in pool.get("servers") or []:
        ip = _addr(srv.get("ip"))
        entry = {
            "ip": ip,
            "address": None if ip else (srv.get("hostname") or None),
            "port": srv.get("port") or default_port or None,
            "status": "active" if srv.get("enabled", True) is not False else "disable",
            "weight": srv.get("ratio"),
            "hostname": srv.get("hostname") or None,
            "pool": pool.get("name"),
            "priority": priority,
        }
        out.append({k: v for k, v in entry.items() if v not in (None, "", [])})
    return out


class _Tenant:
    """Objekte eines Avi-Tenants mit Zugriff ueber die UUID."""

    def __init__(self, name: str, data: dict) -> None:
        self.name = name
        self.vs = [v for v in data.get("virtualservice") or [] if isinstance(v, dict)]
        self.vs_by = _by_uuid(self.vs)
        self.vsvips = _by_uuid(data.get("vsvip") or [])
        self.pools = _by_uuid(data.get("pool") or [])
        self.groups = _by_uuid(data.get("poolgroup") or [])
        self.profiles = _by_uuid(data.get("applicationprofile") or [])
        self.oper = {
            r.get("uuid") or (r.get("config") or {}).get("uuid"): (
                ((r.get("runtime") or {}).get("oper_status") or {}).get("state")
            )
            for r in data.get("virtualservice-inventory") or []
            if isinstance(r, dict)
        }

    def pool_targets(self, vs: dict) -> tuple[list[dict], dict[str, Any]]:
        """Server der Pools (Pool bzw. Pool Group nach Prioritaet) und Angaben des Pools."""
        info: dict[str, Any] = {}
        pool = self.pools.get(ref_uuid(vs.get("pool_ref")) or "")
        if pool is not None:
            info = _pool_info(pool)
            return _servers(pool), info
        group = self.groups.get(ref_uuid(vs.get("pool_group_ref")) or "")
        if group is None:
            return [], info
        info["pool_group"] = group.get("name")
        members = sorted(
            group.get("members") or [],
            key=lambda m: -_int(m.get("priority_label")),  # hoechste Prioritaet zuerst
        )
        targets: list[dict] = []
        pools = []
        for m in members:
            p = self.pools.get(ref_uuid(m.get("pool_ref")) or "")
            if p is not None:
                targets += _servers(p, m.get("priority_label"))
                pools.append(_pool_info(p))
        if pools:
            info["pools"] = pools
            info["lb_algorithm"] = pools[0].get("lb_algorithm")
        return targets, info


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pool_info(pool: dict) -> dict[str, Any]:
    monitors = [n for n in (ref_name(r) for r in pool.get("health_monitor_refs") or []) if n]
    info = {
        "pool": pool.get("name"),
        "lb_algorithm": pool.get("lb_algorithm"),
        "health_monitors": monitors,
        "default_server_port": pool.get("default_server_port"),
    }
    return {k: v for k, v in info.items() if v not in (None, "", [])}


def normalize(data: dict) -> list[NormalizedNat]:
    """Alle Tenants -> NAT-Eintraege (Art `server-load-balance`, Plattform `avi`)."""
    out: list[NormalizedNat] = []
    for tenant_name, tdata in (data.get("tenants") or {}).items():
        t = _Tenant(tenant_name, tdata or {})
        for vs in t.vs:
            out += _entries(t, vs)
    return out


def _entries(t: _Tenant, vs: dict) -> list[NormalizedNat]:
    parent = t.vs_by.get(ref_uuid(vs.get("vh_parent_vs_ref")) or "")
    front = parent or vs  # SNI-Child: VIP und Ports des Parents
    vsvip = t.vsvips.get(ref_uuid(front.get("vsvip_ref")) or "")
    addresses = vip_addresses(vsvip, front)
    ports = _ports(front) or [("", False)]
    targets, pool_info = t.pool_targets(vs)
    profile = t.profiles.get(ref_uuid(vs.get("application_profile_ref")) or "") or {}
    fqdns = [d.get("fqdn") for d in (vsvip or {}).get("dns_info") or [] if d.get("fqdn")]
    first = next((x for x in targets if x.get("ip") and x.get("status") == "active"), None)
    first = first or next((x for x in targets if x.get("ip")), {})
    base_attrs = {
        "platform": "avi",
        "tenant": t.name,
        "cloud": ref_name(vs.get("cloud_ref")),
        "vrf": ref_name(front.get("vrf_context_ref") or (vsvip or {}).get("vrf_context_ref")),
        "vsvip": (vsvip or {}).get("name"),
        "enabled": vs.get("enabled", True) is not False,
        "traffic_enabled": vs.get("traffic_enabled", True) is not False,
        "oper_status": t.oper.get(vs.get("uuid")),
        "application_profile": ref_name(vs.get("application_profile_ref")),
        # Avi ersetzt die Quelle durch die Service Engine, ausser die Client-IP bleibt
        "snat": "client" if profile.get("preserve_client_ip") else "service_engine",
        "fqdn": fqdns,
        "parent": (parent or {}).get("name"),
        "domains": vs.get("vh_domain_name") or [],
        "content_switching": bool(vs.get("http_policies")),
        **pool_info,
    }
    base_attrs = {k: v for k, v in base_attrs.items() if v not in (None, "", [])}
    out = []
    for ip, floating, vip_enabled in addresses:
        family = "v6" if ":" in ip else "v4"
        for port, ssl in ports:
            attrs = {**base_attrs, **({"ssl": True} if ssl else {})}
            if floating:
                attrs["floating_ip"] = True
            if not vip_enabled:
                attrs["vip_enabled"] = False
            out.append(
                NormalizedNat(
                    source_platform="avi",
                    external_id="|".join((t.name, vs.get("uuid") or vs.get("name", ""), ip, port)),
                    name=vs.get("name", ""),
                    ext_ip=ip,
                    ext_port=port or None,
                    mapped_ip=first.get("ip"),
                    mapped_port=str(first["port"]) if first.get("port") else None,
                    protocol="tcp",
                    portforward=bool(port),
                    vip_type=SLB,
                    family=family,
                    targets=targets,
                    attrs=attrs,
                    raw={
                        "tenant": t.name,
                        "virtual_service": vs.get("name"),
                        "uuid": vs.get("uuid"),
                        "type": vs.get("type"),
                    },  # fmt: skip
                )
            )
    return out
