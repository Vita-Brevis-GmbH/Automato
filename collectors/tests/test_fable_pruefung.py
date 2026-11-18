"""Gegenprüfung (Phase 3, Fable): was die Collectors für Weg-Suche und Regel-Zuordnung liefern.

Abweichungen als `xfail(strict=True)` mit Grund; Nummern wie in
`orchestrator/tests/test_fable_pruefung.py` und im Bericht der Prüfung.
"""

import pytest

from collectors.cisco_aci import mo


@pytest.mark.xfail(
    strict=True,
    reason="P-11: ACI-Filter mit EtherType `ip` (Zoning-Rule mit Priorität 7 bzw. 13/14/17) und "
    "der Filter ohne EtherType (`unspecified`, Priorität 9 bzw. 15/16/21) werden beide zu "
    "`ip/any` - der Vorrang im Abgleich nimmt für beide die tiefere Stufe",
)
def test_p11_filter_mit_ethertype_ip_ist_nicht_der_filter_ohne_ethertype():
    """Der Vorrang der Zoning-Rules hängt am Filter: nur der Filter ohne EtherType (z. B.
    `common/default`) bekommt die Stufe „any“. Der Collector muss beide unterscheidbar liefern."""
    unspecified = mo._entry_service({"etherT": "unspecified", "prot": "unspecified"})
    ip_only = mo._entry_service({"etherT": "ip", "prot": "unspecified"})
    assert unspecified != ip_only, (unspecified, ip_only)
