"""Gegenprüfung (Phase 3, Fable): was die Collectors für Weg-Suche und Regel-Zuordnung liefern.

Nummern wie in `orchestrator/tests/test_fable_pruefung.py` und im Bericht der Prüfung. P-11 ist
korrigiert (ADR 0071 Nachtrag „Gegenprüfung“) - der Test läuft ohne `xfail`.
"""

from collectors.cisco_aci import mo


def test_p11_filter_mit_ethertype_ip_ist_nicht_der_filter_ohne_ethertype():
    """Der Vorrang der Zoning-Rules hängt am Filter: nur der Filter ohne EtherType (z. B.
    `common/default`) bekommt die Stufe „any“. Der Collector muss beide unterscheidbar liefern."""
    unspecified = mo._entry_service({"etherT": "unspecified", "prot": "unspecified"})
    ip_only = mo._entry_service({"etherT": "ip", "prot": "unspecified"})
    assert unspecified != ip_only, (unspecified, ip_only)
