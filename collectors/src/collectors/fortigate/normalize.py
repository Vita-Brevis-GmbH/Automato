"""FortiGate-Policies -> normalisiertes Regelmodell (UC1).

Kern der M7-Aufloesung: Adressobjekt-Namen (srcaddr/dstaddr) werden ueber die
firewall/address(-grp)-Tabelle zu **Werten** (IP/CIDR/Range) aufgeloest, damit der
Orchestrator gegen das IPAM matchen kann. Nicht zu einer IP aufloesbare Objekte
(FQDN, `H-EXT_`, unbekannt) bleiben als **Name** stehen - der Orchestrator behandelt
sie dann ueber die Namenskonvention (z. B. `H-EXT_` = expected_external).

Service-Namen werden auf kanonische Tokens (`tcp/443`, `udp/53`, `icmp`) abgebildet;
FortiOS-Built-ins (HTTPS, ALL, ...) sind als Fallback hinterlegt.
"""

import ipaddress
from typing import Any

from collectors.common.dto import NormalizedRule

_ANY = "all"

# FortiOS-Built-in-Services (erscheinen nicht in firewall/service/custom)
_BUILTIN_SERVICES: dict[str, list[str]] = {
    "ALL": ["all"],
    "HTTPS": ["tcp/443"],
    "HTTP": ["tcp/80"],
    "SSH": ["tcp/22"],
    "DNS": ["udp/53", "tcp/53"],
    "PING": ["icmp"],
    "ALL_ICMP": ["icmp"],
}


# -- Adressen -----------------------------------------------------------------


def build_address_map(addresses: list[dict], groups: list[dict]) -> dict[str, dict]:
    amap = {a["name"]: a for a in addresses}
    for g in groups:
        amap[g["name"]] = g
    return amap


def resolve_address(name: str, amap: dict[str, dict], _depth: int = 0) -> list[str]:
    """Objektname -> Liste von Endpunktwerten (IP/CIDR/Range) oder [name] als Fallback."""
    if name == _ANY:
        return [_ANY]
    obj = amap.get(name)
    if obj is None:
        return [name]  # nicht bekannt -> Name (Orchestrator nutzt Namenskonvention)
    if "member" in obj and _depth < 3:  # Adressgruppe -> Mitglieder aufloesen
        out: list[str] = []
        for member in obj["member"]:
            out.extend(resolve_address(member["name"], amap, _depth + 1))
        return out or [name]

    obj_type = obj.get("type", "ipmask")
    if obj_type == "ipmask" and obj.get("subnet"):
        return [_subnet_to_value(obj["subnet"])]
    if obj_type == "iprange" and obj.get("start-ip"):
        return [f"{obj['start-ip']}-{obj.get('end-ip', obj['start-ip'])}"]
    # fqdn / geography / mac / ... -> nicht als IP aufloesbar -> Name behalten
    return [name]


def _subnet_to_value(subnet: str) -> str:
    """FortiOS-Subnetz -> CIDR bzw. Host-IP.

    Formen: '10.0.0.0 255.255.255.0' | '10.0.0.0/24' | '10.0.0.1'.
    """
    subnet = subnet.strip()
    if " " in subnet:
        ip, mask = subnet.split()
        net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
    elif "/" in subnet:
        net = ipaddress.ip_network(subnet, strict=False)
    else:
        net = ipaddress.ip_network(subnet, strict=False)
    if net.prefixlen == net.max_prefixlen:  # /32 bzw. /128 -> Host-IP
        return str(net.network_address)
    return str(net)


# -- Services -----------------------------------------------------------------


def build_service_map(services: list[dict]) -> dict[str, list[str]]:
    return {s["name"]: _service_tokens(s) for s in services}


def _service_tokens(service: dict) -> list[str]:
    tokens: list[str] = []
    for field, proto in (("tcp-portrange", "tcp"), ("udp-portrange", "udp")):
        rng = service.get(field)
        if rng:
            tokens.append(f"{proto}/{_first_port(rng)}")
    if not tokens:
        if "ICMP" in (service.get("protocol") or "").upper():
            tokens.append("icmp")
        else:
            tokens.append(service["name"].lower())
    return tokens


def _first_port(rng: str) -> str:
    # "443" | "443-445" | "443:1024-2048" (dest:src) | "443 8443" -> erster Dest-Bereich
    return rng.split(":")[0].split()[0]


def resolve_service(name: str, smap: dict[str, list[str]]) -> list[str]:
    if name in smap:
        return smap[name]
    if name.upper() in _BUILTIN_SERVICES:
        return _BUILTIN_SERVICES[name.upper()]
    return [name.lower()]  # unbekannt -> Name (Orchestrator legt Fallback-Service an)


# -- Policies -----------------------------------------------------------------


def normalize(
    policies: list[dict],
    addresses: list[dict],
    groups: list[dict],
    services: list[dict],
) -> list[NormalizedRule]:
    amap = build_address_map(addresses, groups)
    smap = build_service_map(services)
    return [_normalize_policy(p, amap, smap) for p in policies]


def _normalize_policy(policy: dict, amap: dict, smap: dict) -> NormalizedRule:
    sources: list[str] = []
    for entry in policy.get("srcaddr", []):
        sources.extend(resolve_address(entry["name"], amap))
    destinations: list[str] = []
    for entry in policy.get("dstaddr", []):
        destinations.extend(resolve_address(entry["name"], amap))
    svc: list[str] = []
    for entry in policy.get("service", []):
        svc.extend(resolve_service(entry["name"], smap))

    return NormalizedRule(
        source_platform="fortigate",
        external_id=str(policy["policyid"]),
        name=policy.get("name", ""),
        action="allow" if policy.get("action") == "accept" else "deny",
        sources=sources,
        destinations=destinations,
        services=_dedup(svc),
        src_zone=_first_name(policy.get("srcintf")),
        dst_zone=_first_name(policy.get("dstintf")),
        raw=policy,
    )


def _first_name(entries: list[dict[str, Any]] | None) -> str | None:
    return entries[0]["name"] if entries else None


def _dedup(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
