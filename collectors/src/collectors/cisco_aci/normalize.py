"""Cisco-ACI-Contracts -> normalisiertes Regelmodell (Model 1, read-only).

ACI kennt keine klassischen Firewall-Regeln: Contracts (CT_) + Subjects (SJ_) + Filter
(FT_) zwischen EPGs. Abbildung:
  Contract-Subject  -> eine Regel (action=allow; deny via Taboo spaeter)
  Consumer-/Provider-EPGs -> sources/destinations als `epg:<name>` (typisiert)
  BD->L3Out / externe EPG -> src_zone/dst_zone (steuert Applikations-Doku-Kategorie)

Nimmt die von `mo.parse_mo_export` erzeugten Contract-Dicts (Subjekte mit vorab
aufgeloesten `services`, plus `src_zone`/`dst_zone`). Faellt auf das aeltere Schema
(Subjekte mit `filters`) zurueck, solange es noch Beispieldaten in dieser Form gibt.
"""

from typing import Any

from collectors.common.dto import NormalizedRule


def _subject_services(subject: dict[str, Any]) -> list[str]:
    if "services" in subject:
        return list(subject["services"])
    # Rueckwaertskompatibel: filters -> "proto/port"
    return [
        f"{f.get('protocol', 'ip')}/{f.get('dest_port', 'any')}" for f in subject.get("filters", [])
    ]


def _epg_refs(epgs: list[str]) -> list[str]:
    return [f"epg:{e}" for e in epgs]


def normalize_contract(contract: dict[str, Any]) -> list[NormalizedRule]:
    consumers = contract.get("consumer_epgs", [])
    providers = contract.get("provider_epgs", [])
    src_zone = contract.get("src_zone") or (consumers[0] if consumers else None)
    dst_zone = contract.get("dst_zone") or (providers[0] if providers else None)

    rules: list[NormalizedRule] = []
    for subject in contract.get("subjects", []):
        rules.append(
            NormalizedRule(
                source_platform="cisco_aci",
                external_id=f"{contract['dn']}/{subject['name']}",
                name=f"{contract['name']}:{subject['name']}",
                action="allow",
                sources=_epg_refs(consumers),
                destinations=_epg_refs(providers),
                services=_subject_services(subject),
                src_zone=src_zone,
                dst_zone=dst_zone,
                aci_scope=contract.get("scope"),
                raw=contract,
            )
        )
    return rules
