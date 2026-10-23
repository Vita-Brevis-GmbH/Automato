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

from collectors.common.dto import (
    NetworkInventory,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedNat,
    NormalizedObject,
    NormalizedRule,
    NormalizedSubnet,
    NormalizedVlan,
)

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
    """Ein Namensverzeichnis ueber beide Familien (nur fuer Einzel-Aufloesungen); Policies und
    Objekte loesen je Familie auf (`address_maps`)."""
    amap = {a["name"]: a for a in addresses}
    for a in addresses6 or []:
        amap[a["name"]] = a
    for g in list(groups) + list(groups6 or []):
        amap[g["name"]] = g
    return amap


def address_maps(
    addresses: list[dict],
    groups: list[dict] | None = None,
    addresses6: list[dict] | None = None,
    groups6: list[dict] | None = None,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """(IPv4, IPv6): FortiOS fuehrt address/addrgrp und address6/addrgrp6 getrennt - derselbe
    Name ist in jeder Familie ein eigenes Objekt mit eigenem Wert (Dev-Befund)."""
    v4 = {o["name"]: o for o in [*addresses, *(groups or [])]}
    v6 = {o["name"]: o for o in [*(addresses6 or []), *(groups6 or [])]}
    return v4, v6


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


# Dynamische Adressen (SDN-Connector, z. B. ACI ueber FortiManager): Herkunft per Positivliste
_DYNAMIC_ATTRS = ("sub-type", "sdn", "filter", "obj-tag")


def _address_attrs(a: dict) -> dict:
    if a.get("type") != "dynamic":
        return {}
    return {k: a[k] for k in _DYNAMIC_ATTRS if a.get(k) not in (None, "", [])}


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
    policies6: list[dict] | None = None,
    vips6: list[dict] | None = None,
) -> list[NormalizedRule]:
    """Policies (IPv4/Dual-Stack in firewall/policy) und - FortiOS 6.x - firewall/policy6."""
    maps = address_maps(addresses, groups, addresses6, groups6)
    smap = build_service_map(services, service_groups)
    vipmaps = (build_vip_map(vips), build_vip_map(vips6))
    rules = [_normalize_policy(p, maps, smap, vipmaps) for p in policies]
    rules += [_normalize_policy(p, maps, smap, vipmaps, v6_table=True) for p in policies6 or []]
    return rules


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


# Internet Services (ISDB) statt Adressen: Ziel bzw. Quelle der Policy (Dev-Befund: Regeln
# ohne Quelle/Ziel). Bei aktivem Internet Service sind dstaddr/service leer.
_ISDB_DST = tuple(
    f"internet-service{v}-{k}"
    for v in ("", "6")
    for k in ("name", "group", "custom", "custom-group", "fortiguard")
)
_ISDB_SRC = tuple(
    f"internet-service{v}-src-{k}"
    for v in ("", "6")
    for k in ("name", "group", "custom", "custom-group", "fortiguard")
)
# Identitaet als zusaetzliche Quell-Bedingung (Benutzer, Gruppen, FSSO) und ZTNA-Tags
_IDENTITY = (("users", "user"), ("groups", "usergroup"), ("fsso-groups", "usergroup"))
_ZTNA = ("ztna-ems-tag", "ztna-geo-tag")


def _extra_endpoints(policy: dict, side: str) -> tuple[list[str], list[str]]:
    """(Namen, typisierte Werte) fuer ISDB, Identitaet und ZTNA einer Policy-Seite."""
    names: list[str] = []
    values: list[str] = []
    for n in _names(policy, *(_ISDB_SRC if side == "src" else _ISDB_DST)):
        names.append(n)
        values.append(f"isdb:{n}")
    if side == "src":
        for key, tag in _IDENTITY:
            for n in _names(policy, key):
                names.append(n)
                values.append(f"{tag}:{n}")
        for n in _names(policy, *_ZTNA):
            names.append(n)
            values.append(f"dynamic:{n}")
    return names, values


def _normalize_policy(
    policy: dict,
    maps: tuple[dict, dict],
    smap: dict,
    vipmaps: tuple[dict, dict] | dict,
    v6_table: bool = False,
) -> NormalizedRule:
    """Eine Policy; IPv4- (srcaddr/dstaddr) und IPv6-Felder (srcaddr6/dstaddr6) loesen je
    gegen ihre eigene Objekt-Tabelle auf (VIPs: vip bzw. vip6). In policy6 ist alles IPv6."""
    amap4, amap6 = maps
    vipmap, vipmap6 = vipmaps if isinstance(vipmaps, tuple) else (vipmaps, {})
    if v6_table:
        src4, dst4 = [], []
        src6, dst6 = _names(policy, "srcaddr", "srcaddr6"), _names(policy, "dstaddr", "dstaddr6")
    else:
        src4, dst4 = _names(policy, "srcaddr"), _names(policy, "dstaddr")
        src6, dst6 = _names(policy, "srcaddr6"), _names(policy, "dstaddr6")
    svc_refs = _names(policy, "service")
    sources = [v for n in src4 for v in resolve_address(n, amap4, vipmap)]
    sources += [v for n in src6 for v in resolve_address(n, amap6, vipmap6)]
    destinations = [v for n in dst4 for v in resolve_address(n, amap4, vipmap)]
    destinations += [v for n in dst6 for v in resolve_address(n, amap6, vipmap6)]
    svc = [t for n in svc_refs for t in resolve_service(n, smap)]
    extra_src, extra_src_values = _extra_endpoints(policy, "src")
    extra_dst, extra_dst_values = _extra_endpoints(policy, "dst")
    sources += extra_src_values
    destinations += extra_dst_values
    if not svc and extra_dst:
        svc = ["internet-service"]  # Ports kommen aus dem Internet Service

    pid = policy["policyid"]
    return NormalizedRule(
        source_platform="fortigate",
        external_id=f"v6-{pid}" if v6_table else str(pid),
        name=policy.get("name", ""),
        action="allow" if policy.get("action") == "accept" else "deny",
        sources=_dedup(sources),
        destinations=_dedup(destinations),
        services=_dedup(svc),
        source_refs=src4 + extra_src,
        destination_refs=dst4 + extra_dst,
        source_refs6=src6,
        destination_refs6=dst6,
        family=_family(policy, bool(src4 or dst4), bool(src6 or dst6) or v6_table),
        service_refs=svc_refs,
        src_zone=_first_name(policy.get("srcintf")),
        dst_zone=_first_name(policy.get("dstintf")),
        protections=_policy_protections(policy),
        raw=policy,
    )


def _family(policy: dict, has4: bool, has6: bool) -> str | None:
    """v4 | v6 | dual nach den Adressfeldern; ohne Adressen nach den Internet-Service-Feldern."""
    if not (has4 or has6):
        has4 = any(policy.get(k) for k in (*_ISDB_SRC, *_ISDB_DST) if "service6" not in k)
        has6 = any(policy.get(k) for k in (*_ISDB_SRC, *_ISDB_DST) if "service6" in k)
    if has4 and has6:
        return "dual"
    return "v6" if has6 else "v4" if has4 else None


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


def _extip(vip: dict) -> str:
    extip = vip.get("extip") or ""
    if isinstance(extip, list):
        extip = (extip[0].get("range", "") if extip and isinstance(extip[0], dict) else "") or ""
    return str(extip).strip()


def _names_of(value) -> list[str]:
    """FortiOS-Liste [{name: …}] bzw. Einzelwert -> Namen."""
    if isinstance(value, list):
        return [str(v.get("name")) for v in value if isinstance(v, dict) and v.get("name")]
    return [str(value)] if value else []


SLB = "server-load-balance"
# Virtual Server: Einstellungen wie auf der FortiGate (Zertifikat nur mit Namen, nie Schluessel)
_SLB_ATTRS = (
    "server-type",
    "ldb-method",
    "persistence",
    "http-ip-header",
    "http-multiplex",
    "http-host",
    "max-embryonic-connections",
    "ssl-mode",
    "ssl-min-version",
    "ssl-max-version",
)
_SLB_DEFAULTS = {"", "0", "none", "disable", "None"}


def _real_servers(vip: dict) -> list[dict[str, Any]]:
    """Real Server eines Virtual Servers: IP bzw. Adressobjekt, Port, Status, Gewicht,
    Health-Check (je Server oder vom VIP), Holddown, max. Verbindungen."""
    out: list[dict[str, Any]] = []
    for rs in vip.get("realservers") or []:
        if not isinstance(rs, dict):
            continue
        ip = str(rs.get("ip") or "").strip()
        entry = {
            "id": rs.get("id"),
            "ip": None if ip in ("", "0.0.0.0", "::") else ip,  # noqa: S104 - leer = Objekt
            "address": rs.get("address") or None,  # type=address: FQDN-/Adressobjekt
            "port": rs.get("port") or None,
            "status": rs.get("status") or "active",
            "weight": rs.get("weight"),
            "healthcheck": rs.get("healthcheck"),
            "monitor": _names_of(rs.get("monitor")),
            "holddown": rs.get("holddown-interval"),
            "max_connections": rs.get("max-connections") or None,
            "http_host": rs.get("http-host") or None,
        }
        out.append({k: v for k, v in entry.items() if v not in (None, [], "")})
    return out


def _slb_attrs(vip: dict, monitors: dict[str, dict]) -> dict[str, Any]:
    attrs = {k: vip[k] for k in _SLB_ATTRS if str(vip.get(k, "")).strip() not in _SLB_DEFAULTS}
    names = _names_of(vip.get("monitor"))
    if names:
        attrs["monitor"] = [{"name": n, **monitors.get(n, {})} for n in names]
    cert = _names_of(vip.get("ssl-certificate"))
    if cert:
        attrs["ssl-certificate"] = cert
    return attrs


# Health-Check (firewall/ldb-monitor): was er prueft - ohne Standardwerte
_MONITOR_ATTRS = ("type", "port", "interval", "timeout", "retry", "http-get", "http-match")


def monitor_map(monitors: list[dict] | None) -> dict[str, dict]:
    return {
        m["name"]: {k: m[k] for k in _MONITOR_ATTRS if m.get(k) not in (None, "", 0)}
        for m in monitors or []
        if m.get("name")
    }


def normalize_nat(
    vips: list[dict] | None,
    vips6: list[dict] | None = None,
    monitors: list[dict] | None = None,
) -> list[NormalizedNat]:
    """FortiGate-VIPs -> NAT-/DNAT-Dokumentation (extip -> mappedip:port). Virtual Server
    (Typ server-load-balance) mit allen Real Servern, Methode, Persistenz und Health-Checks."""
    mons = monitor_map(monitors)
    out: list[NormalizedNat] = []
    for family, items in (("v4", vips or []), ("v6", vips6 or [])):
        for vip in items:
            vtype = vip.get("type") or "static-nat"
            slb = vtype == SLB
            targets = _real_servers(vip) if slb else []
            portforward = str(vip.get("portforward", "")).lower() == "enable"
            if slb:  # erster Real Server mit IP steht fuer Altsichten als internes Ziel
                first = next((t for t in targets if t.get("ip")), {})
                mapped_ip = first.get("ip")
                mapped_port = str(first["port"]) if first.get("port") else None
                protocol = vip.get("server-type") or None
            else:
                mapped_ip = _first_mapped_ip(vip)
                mapped_port = (vip.get("mappedport") or None) if portforward else None
                protocol = (vip.get("protocol") or None) if portforward else None
            out.append(
                NormalizedNat(
                    source_platform="fortigate",
                    external_id=vip.get("name", ""),
                    name=vip.get("name", ""),
                    ext_ip=_extip(vip) or None,
                    ext_port=(vip.get("extport") or None) if (portforward or slb) else None,
                    mapped_ip=mapped_ip,
                    mapped_port=mapped_port,
                    protocol=protocol,
                    portforward=portforward,
                    vip_type=vtype,
                    family=family,
                    targets=targets,
                    attrs=_slb_attrs(vip, mons) if slb else {},
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
    vips6: list[dict] | None = None,
) -> list[NormalizedObject]:
    """Alle Firewall-Objekte so, wie sie auf dem Geraet stehen (Inventar, ADR 0010). IPv4- und
    IPv6-Objekte loesen je in ihrer Familie auf (gleiche Namen = verschiedene Objekte)."""
    amap4, amap6 = address_maps(addresses, groups, addresses6, groups6)
    maps = {"v4": amap4, "v6": amap6}
    vipmap = build_vip_map(vips)
    vipmaps = {"v4": vipmap, "v6": build_vip_map(vips6)}
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
                    values=_object_values(a["name"], maps[family], vipmaps[family]),
                    fqdn=a.get("fqdn"),
                    comment=a.get("comment") or None,
                    family=family,
                    interface=a.get("associated-interface") or None,
                    attrs=_address_attrs(a),
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
                    values=_object_values(g["name"], maps[family], vipmaps[family]),
                    comment=g.get("comment") or None,
                    family=family,
                )
            )
    for family, items in (("v4", vips or []), ("v6", vips6 or [])):
        for v in items:
            ext = vipmaps[family].get(v["name"])
            if v.get("type") == SLB:  # Virtual Server: externe IP + alle Real Server
                targets = [t["ip"] for t in _real_servers(v) if t.get("ip")]
            else:
                targets = [x for x in (_first_mapped_ip(v),) if x]
            out.append(
                NormalizedObject(
                    name=v["name"],
                    category="vip",
                    obj_type=v.get("type") or "static-nat",
                    color=_as_color(v.get("color")),
                    value=ext or None,
                    values=_dedup([x for x in (ext, *targets) if x]),
                    comment=v.get("comment") or None,
                    interface=v.get("extintf") or None,
                    family=family,
                    attrs={"server-type": v["server-type"]} if v.get("server-type") else {},
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
                family="v4",
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
                fqdn=svc.get("fqdn") or None,
                attrs=service_attrs(svc),
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
                attrs={"proxy": "enable"} if g.get("proxy") == "enable" else {},
            )
        )
    return out


# Service-Attribute wie auf der FortiGate; Standardwerte (0, leer, "auto") entfallen
_SERVICE_ATTRS = (
    "protocol",
    "protocol-number",
    "icmptype",
    "icmpcode",
    "tcp-portrange",
    "udp-portrange",
    "sctp-portrange",
    "category",
    "iprange",
    "fqdn",
    "session-ttl",
    "tcp-halfclose-timer",
    "tcp-halfopen-timer",
    "tcp-timewait-timer",
    "tcp-rst-timer",
    "udp-idle-timer",
    "check-reset-range",
    "helper",
    "proxy",
    "app-service-type",
)
_SERVICE_DEFAULTS = {"", "0", "0.0.0.0", "auto", "disable", "default", "None"}  # noqa: S104


def service_attrs(svc: dict) -> dict[str, Any]:
    """Attribute eines FortiGate-Services (Quellports stehen in den Portbereichen nach ':')."""
    out: dict[str, Any] = {}
    for key in _SERVICE_ATTRS:
        value = svc.get(key)
        icmp = key in ("icmptype", "icmpcode")
        if value is None or str(value).strip() in ({""} if icmp else _SERVICE_DEFAULTS):
            continue
        if key == "protocol-number" and str(svc.get("protocol", "")).upper() != "IP":
            continue
        if key in ("icmptype", "icmpcode") and "ICMP" not in str(svc.get("protocol", "")).upper():
            continue
        out[key] = value
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


# -- Netzinventar (Interfaces, VLANs, Subnetze; ADR 0010) ---------------------

_IFACE_KIND = {
    "physical": "physical",
    "vlan": "vlan",
    "aggregate": "lag",
    "redundant": "lag",
    "loopback": "loopback",
    "tunnel": "tunnel",
    "vdom-link": "vdom-link",  # Inter-VDOM-Link (Endpunkte <name>0/<name>1), ADR 0019
}


_UNSET_IP = "0.0.0.0"  # noqa: S104 - FortiOS-Platzhalter "keine Adresse", kein Bind


def _iface_cidr(raw: str | None) -> str | None:
    """'10.1.1.1 255.255.255.0' -> '10.1.1.1/24'; 0.0.0.0/leer -> None."""
    parts = (raw or "").split()
    if len(parts) != 2 or parts[0] == _UNSET_IP:
        return None
    try:
        iface = ipaddress.ip_interface(f"{parts[0]}/{parts[1]}")
    except ValueError:
        return None
    return f"{iface.ip}/{iface.network.prefixlen}"


def _iface_ips(iface: dict) -> list[str]:
    """Adressen des Interfaces inkl. Sekundaer-IPs (`secondaryip`) - auch die sind Next-Hops
    anderer Geraete (Weg fuer IP)."""
    ips = [x for x in (_iface_cidr(iface.get("ip")),) if x]
    for extra in iface.get("secondaryip") or []:
        ip = _iface_cidr(extra.get("ip")) if isinstance(extra, dict) else None
        if ip and ip not in ips:
            ips.append(ip)
    ip6 = (iface.get("ipv6") or {}).get("ip6-address")
    if ip6 and not ip6.startswith("::/"):
        ips.append(ip6)
    return ips


def _vdom_name(iface: dict) -> str | None:
    """VDOM eines Interfaces (cmdb system/interface `vdom`) - ADR 0019."""
    vdom = iface.get("vdom")
    return vdom if isinstance(vdom, str) and vdom else None


def _mgmt_ip(items: list[dict]) -> str | None:
    """Management-Adresse: IPv4 eines aktiven Interfaces mit HTTPS-Zugriff (`allowaccess`) -
    Management-Interfaces (`dedicated-to management`, Name mgmt*) zuerst (Dev-Befund 0.33.0)."""

    def rank(i: dict) -> int:
        name = str(i.get("name") or "").lower()
        return 0 if i.get("dedicated-to") == "management" else 1 if name.startswith("mgmt") else 2

    for i in sorted(items, key=rank):
        if "https" not in str(i.get("allowaccess") or "").split():
            continue
        if (i.get("status") or "up") == "down":
            continue
        ip = _iface_cidr(i.get("ip"))
        if ip:
            return ip.split("/")[0]
    return None


def normalize_inventory(
    interfaces: list[dict] | None,
    zones: list[dict] | None,
    system: dict | None,
    device_name: str,
) -> NetworkInventory:
    """FortiGate-Interfaces/VLAN-Interfaces/Zonen -> Netzinventar (read-only gelesen)."""
    system = system or {}
    device = system.get("hostname") or device_name
    zone_of = {
        m.get("interface-name"): z["name"]
        for z in zones or []
        for m in z.get("interface", [])
        if m.get("interface-name")
    }
    items = interfaces or []
    lag_of = {
        m.get("interface-name"): i["name"]
        for i in items
        for m in i.get("member", [])
        if m.get("interface-name")
    }
    children: dict[str, list[int]] = {}
    for i in items:
        if i.get("type") == "vlan" and i.get("interface") and i.get("vlanid"):
            children.setdefault(i["interface"], []).append(int(i["vlanid"]))

    out_ifaces: list[NormalizedInterface] = []
    vlans: dict[int, NormalizedVlan] = {}
    subnets: list[NormalizedSubnet] = []
    for i in items:
        name = i["name"]
        vid = int(i["vlanid"]) if i.get("type") == "vlan" and i.get("vlanid") else None
        tagged = sorted(set(children.get(name, [])))
        ips = _iface_ips(i)
        out_ifaces.append(
            NormalizedInterface(
                device=device,
                name=name,
                kind=_IFACE_KIND.get(i.get("type") or "", "virtual"),
                enabled=(i.get("status") or "up") != "down",
                # VLAN-Subinterface: sendet getaggt ueber den Parent - nicht untagged (Dev-Befund
                # 0.11.4: "internal"-Subinterfaces standen als untagged da)
                mode="tagged" if vid or tagged else None,
                untagged_vlan=None,
                tagged_vlans=[vid] if vid else tagged,
                lag=lag_of.get(name),
                parent=i.get("interface") if vid else None,
                ips=ips,
                description=i.get("description") or i.get("alias") or None,
                zone=zone_of.get(name),
                vdom=_vdom_name(i),
            )
        )
        if vid is not None and vid not in vlans:
            vlans[vid] = NormalizedVlan(
                vid=vid, name=i.get("alias") or name, description=i.get("description") or None
            )
        for ip in ips:
            iface = ipaddress.ip_interface(ip)
            if iface.network.num_addresses == 1:
                continue
            subnets.append(
                NormalizedSubnet(
                    prefix=str(iface.network),
                    gateway=ip,
                    vlan=vid,
                    context="interface",
                    context_name=name,
                    description=i.get("alias") or i.get("description") or None,
                    vdom=_vdom_name(i),
                )
            )
    dev = NormalizedDevice(
        name=device,
        role="firewall",
        model=system.get("model"),
        serial=system.get("serial"),
        version=system.get("version"),
        mgmt_ip=_mgmt_ip(items),
    )
    return NetworkInventory(
        devices=[dev], interfaces=out_ifaces, vlans=list(vlans.values()), subnets=subnets
    )
