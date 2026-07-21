"""FortiGate-Firewall-Policies -> normalisiertes Regelmodell.

Die Struktur entspricht der FortiOS-REST-API
(GET /api/v2/cmdb/firewall/policy). Bis das Lab steht (M6/M7), liefern die
Tasks Beispieldaten aus sample_policies.json durch dieselbe Normalisierung.
"""

from typing import Any

from collectors.common.dto import NormalizedRule


def normalize_policy(policy: dict[str, Any]) -> NormalizedRule:
    return NormalizedRule(
        source_platform="fortigate",
        external_id=str(policy["policyid"]),
        name=policy.get("name", ""),
        action="allow" if policy.get("action") == "accept" else "deny",
        sources=_names(policy.get("srcaddr")),
        destinations=_names(policy.get("dstaddr")),
        services=_names(policy.get("service")),
        src_zone=_first_name(policy.get("srcintf")),
        dst_zone=_first_name(policy.get("dstintf")),
        raw=policy,
    )


def _names(entries: list[dict[str, Any]] | None) -> list[str]:
    return [e["name"] for e in entries or []]


def _first_name(entries: list[dict[str, Any]] | None) -> str | None:
    names = _names(entries)
    return names[0] if names else None
