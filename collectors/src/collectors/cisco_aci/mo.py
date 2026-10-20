"""APIC-MO-Export (Tenant-Subtree) -> vereinfachte Contract-Dicts (Model 1, read-only).

Liest einen APIC-Managed-Object-Export (`{"imdata":[{"fvTenant":{attributes,children}}]}`,
auch verschachtelt) und leitet daraus die Contract-Struktur ab, die `normalize_contract`
konsumiert. Umsetzung des Lesevertrags `docs/collectors-read-contract.md` (Abschnitt 2):

  vzBrCP (CT_)  + vzSubj (SJ_) + vzRsSubjFiltAtt -> Regel + Services (vzFilter/vzEntry FT_)
                  (auch vzInTerm/vzOutTerm vzRsFiltAtt bei nicht-reflexiven Subjects)
  fvAEPg (EG_)  fvRsProv/fvRsCons/fvRsConsIf     -> Provider-/Consumer-EPGs
  l3extInstP (EN_) unter l3extOut (L3O_)         -> externe EPG (Subnetze: l3extSubnet)
  vzAny unter fvCtx (vzRsAnyToProv/Cons)         -> alle EPGs der VRF
  vzCPIf + vzRsIf                                -> exportierter Contract (anderer Tenant)
  fvBD (BD_)  fvRsCtx / fvSubnet                 -> VRF, Subnetze je EPG-Seite
  Zone (L3O) nur fuer externe EPGs: fvRsBDToOut einer BD gibt nur ihre Subnetze nach
  aussen bekannt - EPG-zu-EPG-Verkehr laeuft nicht ueber den L3Out

Identitaet ist die DN: dieselben Namen (Contract, EPG, BD, Filter) gibt es in vielen
Tenants. Verweise per Name loest der APIC im eigenen Tenant auf, sonst in `common` - genauso
hier. Ein echter APIC (`rsp-subtree=full`) liefert die `dn` nur am Wurzelobjekt, Kinder
tragen nur ihren `rn`; Konfig-Exporte teils keines von beidem. Die DN wird deshalb beim
Abstieg aus Eltern-DN + `rn` (bzw. Klassen-Praefix + Name) gebildet. Der APIC meldet
bekannte Ports mit Namen (`https`, `dns`, ...) - sie werden auf Portnummern abgebildet.

Keine echten Secrets im Export; Samples bleiben ausserhalb des Repos. Reine Funktion.
"""

from typing import Any

COMMON = "common"  # Tenant, in dem der APIC Namensverweise ersatzweise aufloest


def _attrs(mo_body: dict) -> dict:
    return mo_body.get("attributes", {}) or {}


def _children(mo_body: dict) -> list[dict]:
    return mo_body.get("children", []) or []


def _roots(data: Any) -> list[dict]:
    if isinstance(data, dict) and "imdata" in data:
        return data["imdata"]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


class _Bag:
    def __init__(self) -> None:
        self.bds: dict[tuple[str, str], dict] = {}  # (Tenant, BD) -> {vrf, subnets}
        # DN -> Contract-Endpunkt: EPG, externe EPG (L3Out) oder vzAny (VRF)
        self.endpoints: dict[str, dict] = {}
        self.contracts: dict[tuple[str, str], dict] = {}  # (Tenant, CT) -> {dn, scope, ...}
        self.filters: dict[tuple[str, str], list[str]] = {}  # (Tenant, FT) -> Services
        self.cifs: dict[tuple[str, str], str] = {}  # (Tenant, Contract-Interface) -> CT-DN


# rn-Praefixe je Klasse (fuer Exporte ohne dn/rn an den Kindern)
_RN_PREFIX = {
    "fvTenant": "tn-",
    "fvAp": "ap-",
    "fvAEPg": "epg-",
    "fvBD": "BD-",
    "fvCtx": "ctx-",
    "vzBrCP": "brc-",
    "vzSubj": "subj-",
    "vzFilter": "flt-",
    "vzEntry": "e-",
    "vzCPIf": "cif-",
    "l3extOut": "out-",
    "l3extInstP": "instP-",
}

# Platzhalter-Adressen eines fvCEp ohne gelernte IP
_NO_IP = frozenset({"0.0.0.0", "::"})  # noqa: S104 - kein Bind, nur Vergleichswert

# Port-Namen, die der APIC statt Nummern meldet (vzEntry dFromPort/dToPort)
_NAMED_PORTS = {
    "ftpdata": "20",
    "ssh": "22",
    "smtp": "25",
    "dns": "53",
    "http": "80",
    "pop3": "110",
    "https": "443",
    "rtsp": "554",
}


def _dn(cls: str, attrs: dict, parent_dn: str) -> str:
    if attrs.get("dn"):
        return attrs["dn"]
    rn = attrs.get("rn")
    if not rn and cls == "vzAny":
        rn = "any"
    if not rn and cls in _RN_PREFIX and attrs.get("name"):
        rn = f"{_RN_PREFIX[cls]}{attrs['name']}"
    if not rn:
        return ""
    if cls == "fvTenant" and not parent_dn:
        return f"uni/{rn}"
    return f"{parent_dn}/{rn}" if parent_dn else ""


def _cep_endpoints(kids: list[dict]) -> list[dict]:
    """Gelernte Endpunkte aus fvCEp (mac, ip) + fvIp (addr) unter einer EPG - je IP einmal."""
    out: dict[str, str | None] = {}
    for c in kids:
        if "fvCEp" not in c:
            continue
        attrs = _attrs(c["fvCEp"])
        mac = attrs.get("mac")
        ips = [_attrs(ic["fvIp"]).get("addr") for ic in _children(c["fvCEp"]) if "fvIp" in ic]
        ips.append(attrs.get("ip"))  # aeltere APIC-Versionen: nur fvCEp.ip
        for ip in ips:
            if ip and ip not in _NO_IP:
                out.setdefault(ip, mac)
    return [{"ip": ip, "mac": mac} for ip, mac in out.items()]


def _ap_from_dn(dn: str) -> str | None:
    """AP-Klammer (Projekt) aus der EPG-DN: .../ap-<AP>/epg-<EG> -> <AP>."""
    for part in dn.split("/"):
        if part.startswith("ap-"):
            return part[len("ap-") :]
    return None


def _port(value: str | None) -> str | None:
    if value in ("unspecified", "", None):
        return None
    return _NAMED_PORTS.get(value.lower(), value)


def _entry_service(attrs: dict) -> str:
    prot = (attrs.get("prot") or "unspecified").lower()
    if prot == "unspecified":
        ether = (attrs.get("etherT") or "unspecified").lower()
        if ether not in ("unspecified", "ip", "ipv4", "ipv6"):
            return f"{ether}/any"  # z. B. arp
        prot = "ip"
    if prot in ("icmp", "icmpv4"):
        return "icmp/any"
    if prot == "icmpv6":
        return "icmpv6/any"
    lo, hi = _port(attrs.get("dFromPort")), _port(attrs.get("dToPort"))
    if prot not in ("tcp", "udp") or lo is None:
        return f"{prot}/any"
    if hi and hi != lo:
        return f"{prot}/{lo}-{hi}"
    return f"{prot}/{lo}"


def _rel(kids: list[dict], cls: str, attr: str) -> list[str]:
    """Namensverweise der Kinder einer Klasse (z. B. fvRsCons.tnVzBrCPName)."""
    return [v for c in kids if cls in c for v in [_attrs(c[cls]).get(attr)] if v]


def _values(kids: list[dict], cls: str, attr: str = "ip") -> list[str]:
    """Attribut aller Kinder einer Klasse (z. B. fvSubnet.ip)."""
    return [v for c in kids if cls in c for v in [_attrs(c[cls]).get(attr)] if v]


def _contract_endpoint(kind: str, dn: str, tenant: str, kids: list[dict], **info) -> dict:
    if kind == "vzany":
        prov, cons = ("vzRsAnyToProv", "vzRsAnyToCons")
        cons_if = "vzRsAnyToConsIf"
    else:
        prov, cons, cons_if = ("fvRsProv", "fvRsCons", "fvRsConsIf")
    return {
        "kind": kind,
        "tenant": tenant,
        "dn": dn,
        "provides": _rel(kids, prov, "tnVzBrCPName"),
        "consumes": _rel(kids, cons, "tnVzBrCPName"),
        "consumes_if": _rel(kids, cons_if, "tnVzCPIfName"),
        **info,
    }


def _subject_filters(subj_kids: list[dict]) -> list[str]:
    names = _rel(subj_kids, "vzRsSubjFiltAtt", "tnVzFilterName")
    for c in subj_kids:  # nicht-reflexive Subjects: Filter je Richtung
        for term in ("vzInTerm", "vzOutTerm"):
            if term in c:
                names += _rel(_children(c[term]), "vzRsFiltAtt", "tnVzFilterName")
    return list(dict.fromkeys(names))


def _walk(
    mo: dict,
    bag: _Bag,
    tenant: str = "",
    l3out: str | None = None,
    l3out_vrf: str | None = None,
    parent_dn: str = "",
) -> None:
    for cls, body in mo.items():
        attrs = _attrs(body)
        name = attrs.get("name", "")
        kids = _children(body)
        dn = _dn(cls, attrs, parent_dn)
        t, out, out_vrf = tenant, l3out, l3out_vrf
        if cls == "fvTenant":
            t = name or (dn.split("/tn-", 1)[1].split("/", 1)[0] if "/tn-" in dn else "")
        elif cls == "fvBD":
            bag.bds[(t, name)] = {
                "vrf": next(iter(_rel(kids, "fvRsCtx", "tnFvCtxName")), None),
                "subnets": _values(kids, "fvSubnet"),
            }
        elif cls == "fvAEPg":
            key = dn or f"uni/tn-{t}/epg-{name}"
            bag.endpoints[key] = _contract_endpoint(
                "epg",
                key,
                t,
                kids,
                name=name,
                ap=_ap_from_dn(dn),
                bd=next(iter(_rel(kids, "fvRsBd", "tnFvBDName")), None),
                subnets=_values(kids, "fvSubnet"),
                endpoints=_cep_endpoints(kids),
            )
        elif cls == "l3extOut":
            out = name  # Kontext fuer verschachtelte l3extInstP
            out_vrf = next(iter(_rel(kids, "l3extRsEctx", "tnFvCtxName")), None)
        elif cls == "l3extInstP":
            key = dn or f"uni/tn-{t}/out-{out}/instP-{name}"
            bag.endpoints[key] = _contract_endpoint(
                "external",
                key,
                t,
                kids,
                name=name,
                l3out=out,
                vrf=out_vrf,
                subnets=_values(kids, "l3extSubnet"),
            )
        elif cls == "vzAny":
            vrf = parent_dn.rsplit("/ctx-", 1)[-1] if "/ctx-" in parent_dn else None
            key = dn or f"uni/tn-{t}/ctx-{vrf}/any"
            bag.endpoints[key] = _contract_endpoint("vzany", key, t, kids, name="vzAny", vrf=vrf)
        elif cls == "vzBrCP":
            subjects = [
                {
                    "name": _attrs(c["vzSubj"]).get("name", ""),
                    "filters": _subject_filters(_children(c["vzSubj"])),
                }
                for c in kids
                if "vzSubj" in c
            ]
            bag.contracts[(t, name)] = {
                "dn": dn or name,
                "tenant": t,
                "name": name,
                "scope": attrs.get("scope", "context"),
                "subjects": subjects,
            }
        elif cls == "vzFilter":
            bag.filters[(t, name)] = [
                _entry_service(_attrs(c["vzEntry"])) for c in kids if "vzEntry" in c
            ]
        elif cls == "vzCPIf":
            target = next((_attrs(c["vzRsIf"]).get("tDn") for c in kids if "vzRsIf" in c), None)
            if target:
                bag.cifs[(t, name)] = target
        for child in kids:
            _walk(child, bag, t, out, out_vrf, dn)


def _collect(data: Any) -> _Bag:
    bag = _Bag()
    for root in _roots(data):
        _walk(root, bag)
    return bag


def _lookup(table: dict, tenant: str, name: str):
    """Namensverweis wie der APIC: eigener Tenant, sonst `common`."""
    return table.get((tenant, name)) or table.get((COMMON, name))


def _ref(ep: dict) -> dict:
    """Kompakte Endpunkt-Angabe am Contract: wo liegt die EPG (Tenant, AP/L3Out/VRF)."""
    keys = ("kind", "tenant", "name", "dn", "ap", "bd", "l3out", "vrf")
    return {k: ep.get(k) for k in keys if ep.get(k) is not None}


def _zone(side: list[dict]) -> str | None:
    """Zone einer Contract-Seite (Consumer bzw. Provider): der L3Out, wenn *alle* Endpunkte
    externe EPGs desselben L3Outs sind. Konsumiert z. B. eine VPN-EPG viele Contracts
    zusammen mit Anwendungs-EPGs, hat die Seite keine Zone - die externe EPG steht mit ihrem
    L3Out in der Consumer-/Provider-Liste. Dass die BD einer EPG an einen L3Out gebunden ist
    (fvRsBDToOut), heisst nur, dass ihre Subnetze nach aussen bekannt gegeben werden."""
    l3outs = {ep.get("l3out") if ep["kind"] == "external" else None for ep in side}
    return l3outs.pop() if len(l3outs) == 1 else None


def parse_mo_export(data: Any) -> list[dict]:
    """APIC-MO-Export -> Liste von Contract-Dicts fuer `normalize_contract`."""
    bag = _collect(data)
    by_dn = {c["dn"]: key for key, c in bag.contracts.items()}
    sides: dict[tuple[str, str], dict[str, list[dict]]] = {
        key: {"consumers": [], "providers": []} for key in bag.contracts
    }
    for ep in bag.endpoints.values():
        t = ep["tenant"]
        for role, names in (("providers", ep["provides"]), ("consumers", ep["consumes"])):
            for cname in names:
                c = _lookup(bag.contracts, t, cname)
                if c is not None:
                    sides[(c["tenant"], c["name"])][role].append(ep)
        for ifname in ep["consumes_if"]:  # exportierter Contract eines anderen Tenants
            key = by_dn.get(_lookup(bag.cifs, t, ifname) or "")
            if key is not None:
                sides[key]["consumers"].append(ep)

    contracts: list[dict] = []
    for key, c in bag.contracts.items():
        consumers, providers = sides[key]["consumers"], sides[key]["providers"]
        subjects = [
            {
                "name": s["name"],
                "services": [
                    svc
                    for fn in s["filters"]
                    for svc in (_lookup(bag.filters, c["tenant"], fn) or [])
                ],
            }
            for s in c["subjects"]
        ]
        contracts.append(
            {
                "dn": c["dn"],
                "tenant": c["tenant"],
                "name": c["name"],
                "scope": c["scope"],
                "consumers": [_ref(e) for e in consumers],
                "providers": [_ref(e) for e in providers],
                # Namen wie bisher (Anzeige, aeltere Auswertungen)
                "consumer_epgs": [e["name"] for e in consumers],
                "provider_epgs": [e["name"] for e in providers],
                "subjects": subjects,
                "src_zone": _zone(consumers),
                "dst_zone": _zone(providers),
            }
        )
    return contracts


def parse_epgs(data: Any) -> list[dict]:
    """APIC-MO-Export -> Contract-Endpunkte fuer den Store: EPGs (Tenant, AP, BD, VRF,
    EPG- und BD-Subnetze, gelernte Endpunkte), externe EPGs (L3Out, Subnetze) und vzAny."""
    bag = _collect(data)
    out = []
    for dn, ep in sorted(bag.endpoints.items()):
        bd = _lookup(bag.bds, ep["tenant"], ep.get("bd") or "") or {}
        out.append(
            {
                "name": ep["name"],
                "kind": ep["kind"],
                "tenant": ep["tenant"] or None,
                "dn": dn,
                "ap": ep.get("ap"),
                "bd": ep.get("bd"),
                "vrf": ep.get("vrf") or bd.get("vrf"),
                "l3out": ep.get("l3out"),
                "subnets": ep.get("subnets", []),
                "bd_subnets": bd.get("subnets", []),
                "endpoints": ep.get("endpoints", []),
            }
        )
    return out
