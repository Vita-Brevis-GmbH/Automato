"""APIC-MO-Export (Tenant-Subtree) -> vereinfachte Contract-Dicts (Model 1, read-only).

Liest einen APIC-Managed-Object-Export (`{"imdata":[{"fvTenant":{attributes,children}}]}`,
auch verschachtelt) und leitet daraus die Contract-Struktur ab, die `normalize_contract`
konsumiert. Umsetzung des Lesevertrags `docs/collectors-read-contract.md` (Abschnitt 2):

  vzBrCP (CT_)  + vzSubj (SJ_) + vzRsSubjFiltAtt -> Regel + Services (vzFilter/vzEntry FT_)
  fvAEPg (EG_)  fvRsProv/fvRsCons                -> Provider-/Consumer-EPGs
  fvBD   (BD_)  fvRsBDToOut.tnL3extOutName       -> Zone (L3O) je EPG-Seite
  l3extOut (L3O_) l3extInstP (EN_)               -> externe EPGs + externe Zone

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


def _entry_service(attrs: dict) -> str:
    prot = (attrs.get("prot") or "ip").lower()
    if prot in ("icmp", "icmpv4"):
        return "icmp/any"
    if prot == "icmpv6":
        return "icmpv6/any"
    lo, hi = attrs.get("dFromPort", "unspecified"), attrs.get("dToPort", "unspecified")
    if lo in ("unspecified", "", None):
        return f"{prot}/any"
    if hi and hi != lo:
        return f"{prot}/{lo}-{hi}"
    return f"{prot}/{lo}"


def _walk(mo: dict, bag: _Bag, parent_l3out: str | None = None) -> None:
    for cls, body in mo.items():
        attrs = _attrs(body)
        name = attrs.get("name", "")
        kids = _children(body)
        if cls == "fvBD":
            l3outs = [
                _attrs(c["fvRsBDToOut"]).get("tnL3extOutName") for c in kids if "fvRsBDToOut" in c
            ]
            bag.bds[name] = [x for x in l3outs if x]
        elif cls == "fvAEPg":
            bd = next((_attrs(c["fvRsBd"]).get("tnFvBDName") for c in kids if "fvRsBd" in c), None)
            provides = [_attrs(c["fvRsProv"]).get("tnVzBrCPName") for c in kids if "fvRsProv" in c]
            consumes = [_attrs(c["fvRsCons"]).get("tnVzBrCPName") for c in kids if "fvRsCons" in c]
            bag.epgs[name] = {
                "bd": bd,
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
                "dn": attrs.get("dn", name),
                "scope": attrs.get("scope", "context"),
                "subjects": subjects,
            }
        elif cls == "vzFilter":
            bag.filters[name] = [
                _entry_service(_attrs(c["vzEntry"])) for c in kids if "vzEntry" in c
            ]
        _walk_children(kids, bag, parent_l3out)


def _walk_children(kids: list[dict], bag: _Bag, parent_l3out: str | None) -> None:
    for child in kids:
        _walk(child, bag, parent_l3out)


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
