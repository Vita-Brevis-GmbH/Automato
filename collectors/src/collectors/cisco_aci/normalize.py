"""Cisco-ACI-Contracts -> normalisiertes Regelmodell (Model 1, read-only).

ACI kennt keine klassischen Firewall-Regeln: Contracts (CT_) + Subjects (SJ_) + Filter
(FT_) zwischen EPGs. Abbildung:
  Contract-Subject  -> eine Regel (action=allow); Dienste Consumer -> Provider, in `raw`
                       `reverse` = Dienste, die der Provider zum Consumer aufbauen kann
                       (ACI ist zustandslos, 0.69.1 - `mo._directions`)
  Taboo-Subject     -> eine Regel (action=deny, Quelle any, Ziel = geschuetzte EPGs)
  Preferred Group   -> eine Regel je VRF (Mitglieder untereinander, ip/any)
  Consumer-/Provider  -> sources/destinations als `epg:<DN>` (typisiert; EPG, externe EPG
                         oder vzAny - die DN ist ueber alle ACI-Tenants eindeutig)
  externe EPG       -> ihr L3Out als src_zone/dst_zone; EPG-zu-EPG ohne Zone (die BD-
                       Bindung an einen L3Out gibt nur Subnetze nach aussen bekannt)

Nimmt die von `mo.parse_mo_export` erzeugten Contract-Dicts (Subjekte mit vorab
aufgeloesten `services`, plus `src_zone`/`dst_zone`). Faellt auf das aeltere Schema
(Subjekte mit `filters`) zurueck, solange es noch Beispieldaten in dieser Form gibt.
"""

from typing import Any

from collectors.common.dto import NormalizedObject, NormalizedRule


def _subject_services(subject: dict[str, Any]) -> list[str]:
    if "services" in subject:
        return list(subject["services"])
    # Rueckwaertskompatibel: filters -> "proto/port"
    return [
        f"{f.get('protocol', 'ip')}/{f.get('dest_port', 'any')}" for f in subject.get("filters", [])
    ]


def _epg_refs(contract: dict[str, Any], side: str, legacy: str) -> list[str]:
    """`epg:<DN>` je Consumer/Provider; aeltere Contract-Dicts nur mit Namen -> `epg:<Name>`."""
    if side in contract:
        return [f"epg:{e['dn']}" for e in contract[side] if e.get("dn")]
    return [f"epg:{e}" for e in contract.get(legacy, [])]


def normalize_contract(contract: dict[str, Any]) -> list[NormalizedRule]:
    # Kein Rueckfall auf EPG-Namen als Zone: eine Zone ist nur der L3Out einer externen EPG
    src_zone = contract.get("src_zone")
    dst_zone = contract.get("dst_zone")
    kind = contract.get("kind", "contract")
    sources = _epg_refs(contract, "consumers", "consumer_epgs")
    if kind == "taboo":
        sources = ["any"]  # Taboo gilt fuer jede Quelle
    destinations = _epg_refs(contract, "providers", "provider_epgs")

    rules: list[NormalizedRule] = []
    for subject in contract.get("subjects", []):
        name = contract["name"]
        if kind != "preferred_group":
            name = f"{name}:{subject['name']}"
        raw = contract
        if "reverse" in subject:  # ACI ist zustandslos (0.69.1): Dienste Provider -> Consumer
            raw = {**contract, "subject": subject["name"], "reverse": list(subject["reverse"])}
        rules.append(
            NormalizedRule(
                source_platform="cisco_aci",
                external_id=f"{contract['dn']}/{subject['name']}",
                name=name,
                action="deny" if kind == "taboo" else "allow",
                sources=sources,
                destinations=destinations,
                services=_subject_services(subject),
                service_refs=list(subject.get("filter_refs", [])),
                src_zone=src_zone,
                dst_zone=dst_zone,
                aci_scope=contract.get("scope"),
                raw=raw,
            )
        )
    return rules


def filter_objects(filters: list[dict]) -> list[NormalizedObject]:
    """ACI-Filter als Service-Objekte (`<Tenant>/<FT_name>` - Namen gibt es in vielen Tenants)."""
    return [
        NormalizedObject(
            name=f"{f['tenant']}/{f['name']}",
            category="service",
            obj_type="vzFilter",
            values=list(dict.fromkeys(f["services"])),
            comment=f.get("descr"),
            attrs={"tenant": f["tenant"], "dn": f["dn"], "entries": f["entries"]},
        )
        for f in filters
    ]
