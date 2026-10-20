"""FortiGate-Policies -> normalisiertes Regelmodell (UC1).

Kern der M7-Aufloesung: Adressobjekt-Namen (srcaddr/dstaddr) werden ueber die
firewall/address(-grp)-Tabelle zu **Werten** (IP/CIDR/Range) aufgeloest, damit der
Orchestrator gegen das IPAM matchen kann. Nicht zu einer IP aufloesbare Objekte
(FQDN, `H-EXT_`, unbekannt) bleiben als **Name** stehen - der Orchestrator behandelt
sie dann ueber die Namenskonvention (z. B. `H-EXT_` = expected_external).

Service-Namen werden auf kanonische Tokens (`tcp/443`, `udp/53`, `icmp`) abgebildet;
FortiOS-Built-ins (HTTPS, ALL, ...) sind als Fallback hinterlegt.

Zusaetzlich (ADR 0010, Inventar-Ebene): Regeln tragen die **Objektnamen** der Firewall
(`source_refs`/`destination_refs`/`service_refs`), und `build_device_objects` liefert alle
Objekte so, wie sie auf dem Geraet stehen - Adressen, Gruppen mit Mitgliedern, VIPs,
IP-Pools, Services und Service-Gruppen, jeweils mit aufgeloesten Werten, Kommentar, Farbe.
"""

import ipaddress
from typing import Any

from collectors.common.dto import NormalizedNat, NormalizedObject, NormalizedRule

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


def build_address_map(
    addresses: list[dict],
    groups: list[dict],
    addresses6: list[dict] | None = None,
    groups6: list[dict] | None = None,
) -> dict[str, dict]:
    amap = {a["name"]: a for a in addresses}
    for a in addresses6 or []:
        amap[a["name"]] = a
    for g in list(groups) + list(groups6 or []):
        amap[g["name"]] = g
    return amap


def build_vip_map(vips: list[dict] | None) -> dict[str, str]:
    """VIP-Name -> externe IP (extip). Range-extip -> erste IP."""
    out: dict[str, str] = {}
    for v in vips or []:
        extip = v.get("extip") or ""
        if isinstance(extip, list):  # manche FortiOS-Versionen liefern eine Liste
            extip = (
                extip[0].get("range", "") if extip and isinstance(extip[0], dict) else ""
            ) or ""
        out[v["name"]] = str(extip).split("-")[0].strip()
    return out


def resolve_address(
    name: str, amap: dict[str, dict], vipmap: dict[str, str] | None = None, _depth: int = 0
) -> list[str]:
    """Objektname -> Liste von Endpunktwerten (IP/CIDR/Range) oder [name] als Fallback."""
    vipmap = vipmap or {}
    if name == _ANY:
        return [_ANY]
    obj = amap.get(name)
    if obj is None:
        # Kein Adressobjekt - evtl. eine Virtual IP (DNAT): externe IP + Name mitgeben.
        if name in vipmap:
            return [f"vip:{name}={vipmap[name]}"]
        return [name]  # nicht bekannt -> Name (Orchestrator nutzt Namenskonvention)
    if "member" in obj and _depth < 3:  # Adressgruppe -> Mitglieder aufloesen
        out: list[str] = []
        for member in obj["member"]:
            out.extend(resolve_address(member["name"], amap, vipmap, _depth + 1))
        return out or [name]

    obj_type = obj.get("type", "ipmask")
    # interface-subnet traegt das Subnetz des Interfaces im selben Feld wie ipmask.
    if obj_type in ("ipmask", "interface-subnet") and obj.get("subnet"):
        return [_subnet_to_value(obj["subnet"])]
    if obj.get("ip6"):  # IPv6-Adressobjekt (address6): CIDR/Host im Feld ip6
        return [_subnet_to_value(obj["ip6"])]
    if obj_type == "iprange" and obj.get("start-ip"):
        return [f"range:{obj['start-ip']}-{obj.get('end-ip', obj['start-ip'])}"]
    # Nicht als IP/CIDR aufloesbare Typen: mit Typ-Praefix markieren, damit der
    # Orchestrator sie korrekt einordnet (fqdn=extern, geo/mac/dynamic=n/a) statt
    # pauschal als Fehler. Unbekanntes bleibt der nackte Name (Namenskonvention).
    if obj_type == "fqdn" and obj.get("fqdn"):
        return [f"fqdn:{obj['fqdn']}"]
    if obj_type == "geography":
        return [f"geo:{name}"]
    if obj_type == "mac":
        return [f"mac:{name}"]
    if obj_type == "dynamic":
        return [f"dynamic:{name}"]
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


def build_service_map(
    services: list[dict], service_groups: list[dict] | None = None
) -> dict[str, list[str]]:
    smap = {s["name"]: _service_tokens(s) for s in services}
    groups = {g["name"]: g for g in service_groups or []}
    for name in groups:
        smap[name] = _group_tokens(name, groups, smap)
    return smap


def _group_tokens(name: str, groups: dict, smap: dict, _depth: int = 0) -> list[str]:
    out: list[str] = []
    for member in groups[name].get("member", []):
        mname = member["name"]
        if mname in groups and _depth < 3:
            out.extend(_group_tokens(mname, groups, smap, _depth + 1))
        else:
            out.extend(resolve_service(mname, smap))
    return _dedup(out) or [name.lower()]


def _service_tokens(service: dict) -> list[str]:
    tokens: list[str] = []
    for field, proto in (
        ("tcp-portrange", "tcp"),
        ("udp-portrange", "udp"),
        ("sctp-portrange", "sctp"),
    ):
        for rng in _dest_ranges(service.get(field) or ""):
            tokens.append(f"{proto}/{rng}")
    if not tokens:
        protocol = (service.get("protocol") or "").upper()
        if "ICMP6" in protocol:
            tokens.append("icmpv6")
        elif "ICMP" in protocol:
            tokens.append("icmp")
        else:
            tokens.append(service["name"].lower())
    return tokens


def _dest_ranges(rng: str) -> list[str]:
    # "443" | "443-445" | "443:1024-2048" (dest:src) | "443 8443" -> alle Dest-Bereiche
    out = []
    for part in rng.split():
        dest = part.split(":")[0]
        lo, _, hi = dest.partition("-")
        out.append(lo if not hi or hi == lo else f"{lo}-{hi}")
    return out


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
    vips: list[dict] | None = None,
    addresses6: list[dict] | None = None,
    groups6: list[dict] | None = None,
    service_groups: list[dict] | None = None,
) -> list[NormalizedRule]:
    amap = build_address_map(addresses, groups, addresses6, groups6)
    smap = build_service_map(services, service_groups)
    vipmap = build_vip_map(vips)
    return [_normalize_policy(p, amap, smap, vipmap) for p in policies]


# FortiGate-UTM-Profilfelder an der Policy (Referenznamen = "Schutzmechanismen").
_UTM_FIELDS = (
    "av-profile",
    "webfilter-profile",
    "dnsfilter-profile",
    "application-list",
    "ips-sensor",
    "waf-profile",
    "ssl-ssh-profile",
)


def _policy_protections(policy: dict) -> list[str]:
    return [str(policy[f]) for f in _UTM_FIELDS if policy.get(f)]


def _normalize_policy(policy: dict, amap: dict, smap: dict, vipmap: dict) -> NormalizedRule:
    src_refs = _names(policy, "srcaddr", "srcaddr6")  # Dual-Stack: IPv4- und IPv6-Felder
    dst_refs = _names(policy, "dstaddr", "dstaddr6")
    svc_refs = _names(policy, "service")
    sources = [v for n in src_refs for v in resolve_address(n, amap, vipmap)]
    destinations = [v for n in dst_refs for v in resolve_address(n, amap, vipmap)]
    svc = [t for n in svc_refs for t in resolve_service(n, smap)]

    return NormalizedRule(
        source_platform="fortigate",
        external_id=str(policy["policyid"]),
        name=policy.get("name", ""),
        action="allow" if policy.get("action") == "accept" else "deny",
        sources=_dedup(sources),
        destinations=_dedup(destinations),
        services=_dedup(svc),
        source_refs=src_refs,
        destination_refs=dst_refs,
        service_refs=svc_refs,
        src_zone=_first_name(policy.get("srcintf")),
        dst_zone=_first_name(policy.get("dstintf")),
        protections=_policy_protections(policy),
        raw=policy,
    )


def _names(policy: dict, *keys: str) -> list[str]:
    """Objektnamen einer Policy (Reihenfolge wie auf der Firewall, ohne Dubletten)."""
    return _dedup([e["name"] for key in keys for e in policy.get(key, []) if e.get("name")])


def _first_name(entries: list[dict[str, Any]] | None) -> str | None:
    return entries[0]["name"] if entries else None


def _dedup(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


# -- VIPs / NAT ---------------------------------------------------------------


def _first_mapped_ip(vip: dict) -> str | None:
    mapped = vip.get("mappedip")
    if isinstance(mapped, list) and mapped:
        first = mapped[0]
        return first.get("range") if isinstance(first, dict) else str(first)
    if isinstance(mapped, str):
        return mapped or None
    return None


def normalize_nat(vips: list[dict] | None) -> list[NormalizedNat]:
    """FortiGate-VIPs -> NAT-/DNAT-Dokumentation (extip -> mappedip:port)."""
    out: list[NormalizedNat] = []
    for vip in vips or []:
        extip = vip.get("extip") or ""
        if isinstance(extip, list):
            extip = (
                extip[0].get("range", "") if extip and isinstance(extip[0], dict) else ""
            ) or ""
        portforward = str(vip.get("portforward", "")).lower() == "enable"
        out.append(
            NormalizedNat(
                source_platform="fortigate",
                external_id=vip.get("name", ""),
                name=vip.get("name", ""),
                ext_ip=str(extip).strip() or None,
                ext_port=(vip.get("extport") or None) if portforward else None,
                mapped_ip=_first_mapped_ip(vip),
                mapped_port=(vip.get("mappedport") or None) if portforward else None,
                protocol=(vip.get("protocol") or None) if portforward else None,
                portforward=portforward,
                raw=vip,
            )
        )
    return out


def normalize_snat(ippools: list[dict] | None) -> list[NormalizedNat]:
    """FortiGate IP-Pools (IPP_) -> Source-NAT-Dokumentation (externe Quell-IP)."""
    out: list[NormalizedNat] = []
    for pool in ippools or []:
        start = str(pool.get("startip") or "").strip()
        end = str(pool.get("endip") or "").strip()
        ext = f"{start}-{end}" if end and end != start else (start or None)
        out.append(
            NormalizedNat(
                source_platform="fortigate",
                external_id=pool.get("name", ""),
                name=pool.get("name", ""),
                nat_type="snat_pool",
                ext_ip=ext,
                raw=pool,
            )
        )
    return out


# -- Objekt-Katalog (Typ + Farbcode-Signal) -----------------------------------


def build_object_catalog(
    addresses: list[dict], addresses6: list[dict] | None = None
) -> list[NormalizedObject]:
    """Nur Adressobjekte (Typ + Farbcode + Wert) - Rueckwaertskompatibilitaet."""
    return [
        o for o in build_device_objects(addresses, addresses6=addresses6) if o.category == "address"
    ]


def build_device_objects(
    addresses: list[dict],
    groups: list[dict] | None = None,
    addresses6: list[dict] | None = None,
    groups6: list[dict] | None = None,
    vips: list[dict] | None = None,
    ippools: list[dict] | None = None,
    services: list[dict] | None = None,
    service_groups: list[dict] | None = None,
) -> list[NormalizedObject]:
    """Alle Firewall-Objekte so, wie sie auf dem Geraet stehen (Inventar, ADR 0010)."""
    amap = build_address_map(addresses, groups or [], addresses6, groups6)
    vipmap = build_vip_map(vips)
    out: list[NormalizedObject] = []
    for family, items in (("v4", addresses), ("v6", addresses6 or [])):
        for a in items:
            out.append(
                NormalizedObject(
                    name=a["name"],
                    category="address",
                    obj_type=a.get("type"),
                    color=_as_color(a.get("color")),
                    value=_catalog_value(a),
                    values=_object_values(a["name"], amap, vipmap),
                    fqdn=a.get("fqdn"),
                    comment=a.get("comment") or None,
                    family=family,
                    interface=a.get("associated-interface") or None,
                )
            )
    for family, items in (("v4", groups or []), ("v6", groups6 or [])):
        for g in items:
            out.append(
                NormalizedObject(
                    name=g["name"],
                    category="address_group",
                    color=_as_color(g.get("color")),
                    members=[m["name"] for m in g.get("member", []) if m.get("name")],
                    values=_object_values(g["name"], amap, vipmap),
                    comment=g.get("comment") or None,
                    family=family,
                )
            )
    for v in vips or []:
        out.append(
            NormalizedObject(
                name=v["name"],
                category="vip",
                obj_type=v.get("type") or "static-nat",
                color=_as_color(v.get("color")),
                value=vipmap.get(v["name"]) or None,
                values=[x for x in (vipmap.get(v["name"]), _first_mapped_ip(v)) if x],
                comment=v.get("comment") or None,
                interface=v.get("extintf") or None,
            )
        )
    for p in ippools or []:
        start, end = str(p.get("startip") or ""), str(p.get("endip") or "")
        rng = f"{start}-{end}" if end and end != start else start
        out.append(
            NormalizedObject(
                name=p["name"],
                category="ippool",
                obj_type=p.get("type"),
                values=[rng] if rng else [],
                comment=p.get("comments") or p.get("comment") or None,
            )
        )
    smap = build_service_map(services or [], service_groups)
    for svc in services or []:
        out.append(
            NormalizedObject(
                name=svc["name"],
                category="service",
                obj_type=svc.get("protocol"),
                color=_as_color(svc.get("color")),
                values=smap.get(svc["name"], []),
                comment=svc.get("comment") or None,
            )
        )
    for g in service_groups or []:
        out.append(
            NormalizedObject(
                name=g["name"],
                category="service_group",
                color=_as_color(g.get("color")),
                members=[m["name"] for m in g.get("member", []) if m.get("name")],
                values=smap.get(g["name"], []),
                comment=g.get("comment") or None,
            )
        )
    return out


def _object_values(name: str, amap: dict, vipmap: dict) -> list[str]:
    """Aufgeloeste Werte (Gruppen: aller Mitglieder); der eigene Name faellt weg."""
    return _dedup([v for v in resolve_address(name, amap, vipmap) if v != name])


def _as_color(raw: Any) -> int | None:
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _catalog_value(obj: dict) -> str | None:
    """Nur echte IP/CIDR-Werte in den Katalog (keine fqdn/geo/range-Typpraefixe)."""
    resolved = resolve_address(obj["name"], {obj["name"]: obj})
    first = resolved[0] if resolved else None
    if not first or first == obj["name"]:
        return None
    for tag in ("fqdn:", "geo:", "mac:", "dynamic:", "range:", "vip:"):
        if first.startswith(tag):
            return None
    return first
