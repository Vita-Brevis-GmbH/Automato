"""Cisco-ACI-Contracts -> normalisiertes Regelmodell.

ACI kennt keine klassischen Firewall-Regeln: Contracts + Subjects + Filter
zwischen EPGs. Die Abbildung hier ist bewusst einfach gehalten und wird mit
dem echten APIC-Modell in M7 verfeinert (siehe Risiko R10):
  Contract-Subject-Filter  -> eine Regel
  Provider-/Consumer-EPGs  -> dst/src (inkl. Zonen-Interpretation)
"""

from typing import Any

from collectors.common.dto import NormalizedRule


def normalize_contract(contract: dict[str, Any]) -> list[NormalizedRule]:
    rules: list[NormalizedRule] = []
    consumers = contract.get("consumer_epgs", [])
    providers = contract.get("provider_epgs", [])

    for subject in contract.get("subjects", []):
        services = [
            f"{f.get('protocol', 'ip')}/{f.get('dest_port', 'any')}"
            for f in subject.get("filters", [])
        ]
        rules.append(
            NormalizedRule(
                source_platform="cisco_aci",
                external_id=f"{contract['dn']}/{subject['name']}",
                name=f"{contract['name']}:{subject['name']}",
                action="allow",  # ACI-Contracts erlauben; deny via Taboo (spaeter)
                sources=consumers,
                destinations=providers,
                services=services,
                src_zone=_epg_zone(consumers),
                dst_zone=_epg_zone(providers),
                raw=contract,
            )
        )
    return rules


def _epg_zone(epgs: list[str]) -> str | None:
    return epgs[0] if epgs else None
