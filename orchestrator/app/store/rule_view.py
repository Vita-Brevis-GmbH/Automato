"""Darstellung einer Regel fuer Listen und Detail (Namen wie auf der Firewall, ACI-Contract,
NAT, fehlende/negierte Seiten). Reine Funktionen - genutzt vom Regelindex (Listenzeile beim
Einlesen) und vom Regel-Detail."""

import re

from app.models import EndpointRole, RefRole, RuleDomain
from app.store import epgs as epg_store

_ACI_TENANT = re.compile(r"(?:^|/)tn-([^/]+)")

# FortiGate: negierte Felder (Policy gilt fuer alles *ausser* den genannten Objekten)
_NEGATE = (
    ("srcaddr-negate", "src"),
    ("internet-service-src-negate", "src"),
    ("dstaddr-negate", "dst"),
    ("internet-service-negate", "dst"),
    ("service-negate", "service"),
)


def display_name(rule) -> str:
    return rule.name or f"#{rule.external_id}"


def snat_pools(rule) -> list[str] | None:
    """SNAT einer FortiGate-Policy: Pool-Namen; [] = NAT auf die Interface-IP; None = kein NAT."""
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    if str(raw.get("nat", "")).lower() != "enable":
        return None
    if str(raw.get("ippool", "")).lower() != "enable":
        return []
    pools = raw.get("poolname") or []
    return [p["name"] for p in pools if isinstance(p, dict) and p.get("name")]


def negated(rule) -> list[str]:
    """Negierte Seiten einer FortiGate-Policy: src | dst | service."""
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    return list(dict.fromkeys(side for key, side in _NEGATE if raw.get(key) == "enable"))


def missing(rule) -> list[str]:
    """Seiten ohne Endpunkt: src | dst. ACI: Contract ohne Consumer bzw. Provider - nicht
    wirksam (Dev-Befund: Regeln ohne Quelle/Ziel)."""
    roles = {e.role.value for e in rule.endpoints}
    return [side for side in ("src", "dst") if side not in roles]


def ref_names(rule, role: RefRole) -> list[str]:
    """Objektnamen wie auf der Firewall; gleichnamige IPv4/IPv6-Objekte einmal."""
    return list(dict.fromkeys(ref.name for ref in rule.refs if ref.role is role))


def bare_service(name: str) -> str:
    """ACI-Filter heissen im Store `Tenant/FT_x` (gleiche Namen in vielen ACI-Tenants) -
    angezeigt wird der Name der Konvention `FT_x` (0.36.0)."""
    return name.rsplit("/", 1)[-1] if "/" in name else name


def service_labels(rule) -> list[str]:
    """Dienste einer Regel mit dem Objektnamen (`FT_https`, `HTTPS`) statt `tcp/443`; ohne
    Verweise (aeltere Laeufe, Werte direkt in der Regel) die kanonischen Werte."""
    names = ref_names(rule, RefRole.SERVICE)
    if rule.domain is RuleDomain.ACI_CONTRACT:
        names = list(dict.fromkeys(bare_service(n) for n in names))
    return names or [s.canonical_name for s in rule.services]


def endpoint_label(e, objs, epgs=None, home_tenant: str | None = None) -> str:
    """Anzeigename eines Endpunkts: Objektname (Konvention) bevorzugt, ACI-EPG als AP/EPG,
    sonst Wert/inline."""
    obj = objs.get(e.network_object_id) if e.network_object_id else None
    if obj:
        return obj.name or obj.value or "?"
    epg = (epgs or {}).get(e.epg_id) if e.epg_id else None
    if epg is not None:
        return epg_store.label(epg, home_tenant)
    return e.inline_value or e.kind.value


def labels(rule, role: RefRole, endpoint_role: EndpointRole, objs, epgs=None) -> list[str]:
    """Objektnamen wie auf der Firewall; ohne Verweise (ACI, Altbestand) die Endpunkte."""
    names = ref_names(rule, role)
    if names:
        return names
    home = _ACI_TENANT.search(rule.contract_dn or (rule.raw or {}).get("dn") or "")
    home_tenant = home.group(1) if home else None
    return [
        endpoint_label(e, objs, epgs, home_tenant)
        for e in rule.endpoints
        if e.role is endpoint_role
    ]


def aci(rule) -> dict | None:
    """Contract/Subject/Tenant/Scope eines ACI-Contracts (Spalten, sonst aus dem Rohdatensatz)."""
    if rule.domain is not RuleDomain.ACI_CONTRACT:
        return None
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    dn = rule.contract_dn or raw.get("dn") or ""
    subject = rule.subject
    if subject is None and dn and rule.external_id.startswith(f"{dn}/"):
        subject = rule.external_id[len(dn) + 1 :]
    tenant = _ACI_TENANT.search(dn)
    return {
        "tenant": tenant.group(1) if tenant else None,
        "contract": raw.get("name") or rule.name.partition(":")[0] or None,
        "subject": subject,
        "scope": rule.aci_scope or raw.get("scope"),
        "dn": dn or None,
        "kind": raw.get("kind") or "contract",
    }


def nat(rule, vips: list[str]) -> dict | None:
    """NAT einer Firewall-Policy: SNAT (Pool bzw. Interface-IP) und VIPs (DNAT) im Ziel."""
    if rule.domain is not RuleDomain.FIREWALL:
        return None
    pools = snat_pools(rule)
    if pools is None and not vips:
        return None
    return {"snat": pools is not None, "pools": pools or [], "vips": vips}


def row(rule, objs, epgs, zones: dict, vips: list[str]) -> dict:
    """Listenzeile einer Regel (ohne die live gezaehlten nicht aufgeloesten Objekte)."""
    return {
        "name": display_name(rule),
        "action": rule.action.value,
        "section_label": rule.section_label,
        "src_zone": zones.get(rule.src_zone_id),
        "dst_zone": zones.get(rule.dst_zone_id),
        "num_src": sum(1 for e in rule.endpoints if e.role is EndpointRole.SRC),
        "num_dst": sum(1 for e in rule.endpoints if e.role is EndpointRole.DST),
        "num_services": len(rule.services),
        "sources": labels(rule, RefRole.SRC, EndpointRole.SRC, objs, epgs),
        "destinations": labels(rule, RefRole.DST, EndpointRole.DST, objs, epgs),
        "service_names": service_labels(rule),
        "aci": aci(rule),
        "nat": nat(rule, vips),
        "missing": missing(rule),
        "negate": negated(rule),
        "family": rule.ip_family,
    }
