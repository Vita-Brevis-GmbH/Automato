"""APIC-MO-Export (Tenant-Subtree) -> vereinfachte Contract-Dicts (Model 1, read-only).

Liest einen APIC-Managed-Object-Export (`{"imdata":[{"fvTenant":{attributes,children}}]}`,
auch verschachtelt) und leitet daraus die Contract-Struktur ab, die `normalize_contract`
konsumiert. Umsetzung des Lesevertrags `docs/collectors-read-contract.md` (Abschnitt 2):

  vzBrCP (CT_)  + vzSubj (SJ_) + vzRsSubjFiltAtt -> Regel + Services (vzFilter/vzEntry FT_)
  fvAEPg (EG_)  fvRsProv/fvRsCons                -> Provider-/Consumer-EPGs
  fvBD   (BD_)  fvRsBDToOut.tnL3extOutName       -> Zone (L3O) je EPG-Seite
  l3extOut (L3O_) l3extInstP (EN_)               -> externe EPGs + externe Zone

Ein echter APIC (`rsp-subtree=full`) liefert die `dn` nur am Wurzelobjekt, Kinder tragen
nur ihren `rn`; Konfig-Exporte teils keines von beidem. Die DN wird deshalb beim Abstieg aus
Eltern-DN + `rn` (bzw. Klassen-Praefix + Name) gebildet. Der APIC meldet bekannte Ports mit
Namen (`https`, `dns`, ...) - sie werden auf Portnummern abgebildet.

Keine echten Secrets im Export; Samples bleiben ausserhalb des Repos. Reine Funktion.
"""

from typing import Any


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
        self.bds: dict[str, list[str]] = {}  # BD-Name -> [L3O-Namen]
        self.epgs: dict[str, dict] = {}  # EPG-Name -> {bd, provides[], consumes[]}
        self.ext_epgs: dict[str, str] = {}  # externe EPG (l3extInstP) -> L3O-Name
        self.contracts: dict[str, dict] = {}  # Contract-Name -> {dn, scope, subjects[]}
        self.filters: dict[str, list[str]] = {}  # Filter-Name -> [Service-Strings]


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


def _walk(mo: dict, bag: _Bag, parent_l3out: str | None = None, parent_dn: str = "") -> None:
    for cls, body in mo.items():
        attrs = _attrs(body)
        name = attrs.get("name", "")
        kids = _children(body)
        dn = _dn(cls, attrs, parent_dn)
        if cls == "fvBD":
            l3outs = [
                _attrs(c["fvRsBDToOut"]).get("tnL3extOutName") for c in kids if "fvRsBDToOut" in c
            ]
            bag.bds[name] = [x for x in l3outs if x]
        elif cls == "fvAEPg":
            bd = next((_attrs(c["fvRsBd"]).get("tnFvBDName") for c in kids if "fvRsBd" in c), None)
            provides = [_attrs(c["fvRsProv"]).get("tnVzBrCPName") for c in kids if "fvRsProv" in c]
            consumes = [_attrs(c["fvRsCons"]).get("tnVzBrCPName") for c in kids if "fvRsCons" in c]
            subnets = [_attrs(c["fvSubnet"]).get("ip") for c in kids if "fvSubnet" in c]
            bag.epgs[name] = {
                "bd": bd,
                "ap": _ap_from_dn(dn),
                "subnets": [x for x in subnets if x],
                "endpoints": _cep_endpoints(kids),
                "provides": [x for x in provides if x],
                "consumes": [x for x in consumes if x],
            }
        elif cls == "l3extOut":
            parent_l3out = name  # Kontext fuer verschachtelte l3extInstP
        elif cls == "l3extInstP":
            bag.ext_epgs[name] = parent_l3out or name
        elif cls == "vzBrCP":
            subjects = []
            for c in kids:
                if "vzSubj" not in c:
                    continue
                sattrs = _attrs(c["vzSubj"])
                filters = [
                    _attrs(fc["vzRsSubjFiltAtt"]).get("tnVzFilterName")
                    for fc in _children(c["vzSubj"])
                    if "vzRsSubjFiltAtt" in fc
                ]
                subjects.append(
                    {"name": sattrs.get("name", ""), "filters": [f for f in filters if f]}
                )
            bag.contracts[name] = {
                "dn": dn or name,
                "scope": attrs.get("scope", "context"),
                "subjects": subjects,
            }
        elif cls == "vzFilter":
            bag.filters[name] = [
                _entry_service(_attrs(c["vzEntry"])) for c in kids if "vzEntry" in c
            ]
        _walk_children(kids, bag, parent_l3out, dn)


def _walk_children(kids: list[dict], bag: _Bag, parent_l3out: str | None, dn: str) -> None:
    for child in kids:
        _walk(child, bag, parent_l3out, dn)


def parse_mo_export(data: Any) -> list[dict]:
    """APIC-MO-Export -> Liste von Contract-Dicts fuer `normalize_contract`."""
    bag = _Bag()
    for root in _roots(data):
        _walk(root, bag)

    def epg_zone(epg: str) -> str | None:
        if epg in bag.ext_epgs:
            return bag.ext_epgs[epg]  # externe EPG -> L3O
        bd = (bag.epgs.get(epg) or {}).get("bd")
        l3outs = bag.bds.get(bd or "", [])
        return l3outs[0] if l3outs else bd

    contracts: list[dict] = []
    for cname, cinfo in bag.contracts.items():
        providers = [e for e, i in bag.epgs.items() if cname in i["provides"]]
        consumers = [e for e, i in bag.epgs.items() if cname in i["consumes"]]
        subjects = [
            {
                "name": s["name"],
                "services": [svc for fn in s["filters"] for svc in bag.filters.get(fn, [])],
            }
            for s in cinfo["subjects"]
        ]
        contracts.append(
            {
                "dn": cinfo["dn"],
                "name": cname,
                "scope": cinfo["scope"],
                "consumer_epgs": consumers,
                "provider_epgs": providers,
                "subjects": subjects,
                "src_zone": epg_zone(consumers[0]) if consumers else None,
                "dst_zone": epg_zone(providers[0]) if providers else None,
            }
        )
    return contracts


def parse_epgs(data: Any) -> list[dict]:
    """APIC-MO-Export -> Liste von EPG-Dicts (name, ap, bd, subnets) fuer den Store."""
    bag = _Bag()
    for root in _roots(data):
        _walk(root, bag)
    return [
        {
            "name": name,
            "ap": info.get("ap"),
            "bd": info.get("bd"),
            "subnets": info.get("subnets", []),
            "endpoints": info.get("endpoints", []),
        }
        for name, info in sorted(bag.epgs.items())
    ]
