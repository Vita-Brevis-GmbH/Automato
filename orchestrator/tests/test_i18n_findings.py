"""Befundtexte auf Englisch (0.87.0, ADR 0028): Satz und Hinweis entstehen beim Abruf in der
Sprache der Anfrage; gespeichert (Detail) wird immer deutsch, Deutsch bleibt unveraendert."""

from app.i18n import use
from app.store import finding_text
from app.store.finding_text import explain
from test_findings_explain import _findings, _seed

GERMAN_DUP = (
    "Der Name „dup“ ist im IPAM (ddi-01) nicht eindeutig: dup.example.ch → 10.10.10.31, "
    "dup.other.ch → 10.10.10.32. Verwendet in 1 Regel (by-name)."
)


def test_tabellen_als_paare():
    """Feste Tabellen halten (de, en) - nie einen beim Import ausgewerteten Text."""
    ft = finding_text
    for table in (ft._CATEGORY, ft._CLASS, ft._VERDICT, ft._ROUTE_KIND, ft._L2_HINT, ft._STATE):
        assert all(isinstance(v, tuple) and len(v) == 2 for v in table.values())


def test_explain_englisch():
    d = {"category": "address", "source": "fw-01", "vdom": "v1", "prefixes": ["H_", "N_"]}
    with use("en"):
        msg, hint = explain("invalid_name", "srv-x", d)
    assert msg == (
        "Address “srv-x” on fw-01 (VDOM v1) does not start with any prefix of the naming "
        "convention (known: H_, N_)."
    )
    assert hint.startswith("Name the object according to the convention")
    # ohne Sprache: Deutsch wie bisher
    assert explain("invalid_name", "srv-x", d)[0] == (
        "Adresse „srv-x“ auf fw-01 (VDOM v1) beginnt mit keinem Präfix der Namenskonvention "
        "(bekannt: H_, N_)."
    )

    with use("en"):
        msg, _ = explain("name_type_mismatch", "H_x", {"expected": "host", "value": "10.0.0.0/24"})
        assert msg == (
            "“H_x” is a host (single IP) according to its prefix, but the value 10.0.0.0/24 "
            "is a network."
        )
        msg, _ = explain("vm_not_in_ipam", "vm1", {"ips": ["10.0.0.1", "10.0.0.2"]})
        assert msg == "VM “vm1”: the IPs 10.0.0.1, 10.0.0.2 are not recorded in the IPAM."
        msg, _ = explain("rule_incomplete", "p", {"missing": ["src", "dst"], "source": "fw"})
        assert msg == "Policy “p” on fw has no source and no destination that Automato can read."
        msg, _ = explain("duplicate_value", "x", {"value": "1.2.3.4", "names": list("abcdefgh")})
        assert msg.endswith("in 8 address objects: a, b, c, d, e, f (+2 more).")


def test_explain_englisch_weg_und_verbund():
    conn = {"src": "a", "dst": "b", "service": "tcp/443", "variants": [
        {"verdict": "allow", "via": "fw-r → fw-p1"},
        {"verdict": "deny", "via": "fw-r → fw-p2", "kind": "backup"},
    ]}  # fmt: skip
    d = {"name": "r", "application": "App", "count": 1, "connections": [conn]}
    with use("en"):
        msg, hint = explain("app_connection_mismatch", "r", d)
    assert msg == (
        "Rule “r”: 1 connection of the application “App” is open or blocked depending on the "
        "path: a → b tcp/443 (allowed via fw-r → fw-p1; blocked via backup fw-r → fw-p2)."
    )
    assert "parallel group" in hint and "“End-to-end connections”" in hint
    assert "Backup über fw-r → fw-p2 gesperrt" in explain("app_connection_mismatch", "r", d)[0]

    routes = {"vdom": "v1", "source": "fw-a", "other": "fw-b", "group": "G", "entries": [
        {"prefix": "10.0.0.0/8", "kind": "differs"}, {"prefix": "11.0.0.0/8", "kind": "only_lead"},
    ]}  # fmt: skip
    with use("en"):
        msg, _ = explain("parallel_route_mismatch", "x", routes)
    assert msg == (
        "Routing in VDOM v1 on fw-a differs from the parallel firewall fw-b (parallel group G): "
        "2 prefixes – 10.0.0.0/8 (differs), 11.0.0.0/8 (missing)."
    )


def test_gespeicherter_text_bleibt():
    """`text` aus dem Detail (beim Abgleich deutsch gespeichert) wird nicht uebersetzt."""
    d = {"text": "VLAN 40 ist getrennt: sw-a | sw-b"}
    with use("en"):
        msg, hint = explain("vlan_split", "40", d)
    assert msg == "VLAN 40 ist getrennt: sw-a | sw-b."
    assert hint.startswith("Allow the VLAN on the trunks in between")
    with use("en"):
        d = {"a": {"device": "sw1", "port": "Gi1"}, "b": {"device": "sw2"}, "text": "nur sw1"}
        msg, _ = explain("link_one_sided", "x", d)
    assert msg == "Link sw1 Gi1 ↔ sw2 ?: nur sw1."


def test_api_folgt_der_sprache(client):
    # Einlesen und Abgleich mit englischer Oberflaeche: gespeichert wird trotzdem deutsch
    client.headers.update({"Accept-Language": "en"})
    tid = _seed(client)
    assert client.post("/reconcile", params={"tenant_id": tid}).status_code == 200

    en = _findings(client, tid, "unmatched_object")["H_dup"]
    assert en["message"] == (
        "The name “dup” is ambiguous in the IPAM (ddi-01): dup.example.ch → 10.10.10.31, "
        "dup.other.ch → 10.10.10.32. Used in 1 rule (by-name)."
    )
    assert en["hint"].startswith("Name the object with the full DNS name")

    client.headers.pop("Accept-Language")
    de = _findings(client, tid, "unmatched_object")["H_dup"]
    assert de["message"] == GERMAN_DUP
    assert de["detail"] == en["detail"]

    # Downloads/Links: ?lang= geht vor
    r = client.get("/findings", params={"tenant_id": tid, "type": "invalid_name", "lang": "en"})
    invalid = {f["subject_id"]: f for f in r.json()}["srv-x"]
    assert invalid["message"].startswith("Address “srv-x” on fw-01 does not start with any prefix")
