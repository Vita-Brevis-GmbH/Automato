"""DNS-Server per Zonentransfer (ADR 0045): echter AXFR gegen einen kleinen Server im Test."""

import ast
import socket
import struct
import threading
from pathlib import Path

import dns.message
import dns.rcode
import dns.rdatatype
import dns.zone
import pytest
from sot_gateway.adapters import dns_zone
from sot_gateway.adapters.dns_zone import (
    DnsZoneAdapter,
    ZoneTransferError,
    normalize_zones,
    parse_server,
    server_url,
)
from sot_gateway.models import ReferenceKind

FORWARD = """
$ORIGIN example.ch.
$TTL 3600
@               IN SOA dc01.example.ch. hostmaster.example.ch. 42 900 600 86400 3600
@               IN NS  dc01.example.ch.
@               IN A   10.0.0.10
@               IN A   10.0.0.11
dc01            IN A   10.0.0.10
dc02            IN A   10.0.0.11
web01           IN A   10.1.2.3
web01           IN AAAA 2001:db8::3
WEB02           IN A   10.1.2.4
intranet        IN CNAME web01
portal          IN CNAME intranet
extern          IN CNAME www.example.org.
_ldap._tcp      IN SRV 0 100 389 dc01.example.ch.
gc._msdcs       IN A   10.0.0.10
DomainDnsZones  IN A   10.0.0.10
ForestDnsZones  IN A   10.0.0.11
*               IN A   10.9.9.9
sub             IN NS  ns.sub.example.ch.
ns.sub          IN A   10.5.5.5
host.sub        IN A   10.5.5.6
"""

REVERSE = """
$ORIGIN 2.1.10.in-addr.arpa.
$TTL 3600
@   IN SOA dc01.example.ch. hostmaster.example.ch. 7 900 600 86400 3600
@   IN NS  dc01.example.ch.
3   IN PTR web01.example.ch.
4   IN PTR web02.example.ch.
"""


class AxfrServer:
    """Kleiner TCP-DNS-Server: beantwortet AXFR fuer bekannte Zonen (eine Nachricht je Zone,
    SOA am Anfang und am Ende), sonst REFUSED."""

    def __init__(self, zones: dict[str, str], chunk: int = 3) -> None:
        self.zones = {
            name: dns.zone.from_text(text, origin=name, relativize=False)
            for name, text in zones.items()
        }
        self.chunk = chunk
        self.queries: list[tuple[str, str]] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    self._answer(conn)
                except OSError:
                    pass

    def _read(self, conn, n: int) -> bytes:
        data = b""
        while len(data) < n:
            part = conn.recv(n - len(data))
            if not part:
                raise OSError("closed")
            data += part
        return data

    def _send(self, conn, msg) -> None:
        wire = msg.to_wire()
        conn.sendall(struct.pack("!H", len(wire)) + wire)

    def _answer(self, conn) -> None:
        (length,) = struct.unpack("!H", self._read(conn, 2))
        query = dns.message.from_wire(self._read(conn, length))
        q = query.question[0]
        name = q.name.to_text()
        self.queries.append((name, dns.rdatatype.to_text(q.rdtype)))
        zone = self.zones.get(name)
        if zone is None or q.rdtype != dns.rdatatype.AXFR:
            response = dns.message.make_response(query)
            response.set_rcode(dns.rcode.REFUSED)
            self._send(conn, response)
            return
        soa = zone.find_rrset(zone.origin, dns.rdatatype.SOA)
        rrsets = [rr for rr in zone.iterate_rdatasets()]
        body = [
            zone.find_rrset(n, rds.rdtype)
            for n, rds in rrsets
            if not (n == zone.origin and rds.rdtype == dns.rdatatype.SOA)
        ]
        parts = [body[i : i + self.chunk] for i in range(0, len(body), self.chunk)] or [[]]
        for i, part in enumerate(parts):
            response = dns.message.make_response(query)
            response.answer = ([soa] if i == 0 else []) + part
            if i == len(parts) - 1:
                response.answer.append(soa)
            self._send(conn, response)

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def server():
    srv = AxfrServer({"example.ch.": FORWARD, "2.1.10.in-addr.arpa.": REVERSE})
    yield srv
    srv.close()


def _adapter(server, zones=("example.ch", "2.1.10.in-addr.arpa"), **kw):
    return DnsZoneAdapter.from_url(f"dns://127.0.0.1:{server.port}", list(zones), **kw)


def test_export_reads_hosts_aliases_and_ptr(server):
    records = list(_adapter(server).export())
    got = {(r.kind.value, r.dns_name, r.value) for r in records}
    assert got == {
        ("a_record", "dc01.example.ch", "10.0.0.10"),
        ("a_record", "dc02.example.ch", "10.0.0.11"),
        ("a_record", "web01.example.ch", "10.1.2.3"),
        ("a_record", "web01.example.ch", "2001:db8::3"),
        ("a_record", "web02.example.ch", "10.1.2.4"),  # klein geschrieben
        # Aliase auf einen Host der gelesenen Zonen, auch ueber mehrere CNAMEs
        ("a_record", "intranet.example.ch", "10.1.2.3"),
        ("a_record", "intranet.example.ch", "2001:db8::3"),
        ("a_record", "portal.example.ch", "10.1.2.3"),
        ("a_record", "portal.example.ch", "2001:db8::3"),
        ("ptr_record", "web01.example.ch", "10.1.2.3"),
        ("ptr_record", "web02.example.ch", "10.1.2.4"),
    }
    # weggelassen: Zonenkopf, _msdcs/_tcp, Anwendungspartitionen, *, Unterzone, externer CNAME
    names = {r.dns_name for r in records}
    skipped = {"example.ch", "gc._msdcs.example.ch", "domaindnszones.example.ch",
               "host.sub.example.ch", "ns.sub.example.ch", "extern.example.ch"}  # fmt: skip
    assert not names & skipped
    web = next(
        r
        for r in records
        if r.dns_name == "web01.example.ch"
        and r.value == "10.1.2.3"
        and r.kind is ReferenceKind.A_RECORD
    )
    assert web.attrs == {"zone": "example.ch"} and web.tags == ["zone=example.ch"]
    assert web.ref_id == "web01.example.ch/A/10.1.2.3"
    alias = next(r for r in records if r.dns_name == "portal.example.ch")
    assert alias.attrs["cname"] == "web01.example.ch"
    ptr = next(r for r in records if r.kind is ReferenceKind.PTR_RECORD)
    assert ptr.attrs == {"zone": "2.1.10.in-addr.arpa"}
    # nur Zonentransfers abgefragt (eine Abfrage je Zone)
    assert server.queries == [("example.ch.", "AXFR"), ("2.1.10.in-addr.arpa.", "AXFR")]


def test_ping_reads_only_the_start_of_each_zone(server):
    assert _adapter(server).ping() == {"example.ch": 42, "2.1.10.in-addr.arpa": 7}


def test_refused_zone_names_zone_and_code(server):
    adapter = _adapter(server, zones=("example.ch", "andere.example.ch"))
    with pytest.raises(ZoneTransferError) as err:
        list(adapter.export())
    assert err.value.zone == "andere.example.ch"
    assert err.value.reason == "refused" and err.value.detail == "REFUSED"
    with pytest.raises(ZoneTransferError) as err:
        adapter.ping()
    assert err.value.reason == "refused"


def test_connection_refused():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # Port frei, niemand hoert zu
    adapter = DnsZoneAdapter("127.0.0.1", ["example.ch"], port=port, timeout=2, lifetime=5)
    with pytest.raises(ZoneTransferError) as err:
        adapter.ping()
    assert err.value.reason == "connect"


def test_closed_without_answer():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    def close_at_once():
        conn, _ = sock.accept()
        conn.close()

    threading.Thread(target=close_at_once, daemon=True).start()
    adapter = DnsZoneAdapter("127.0.0.1", ["example.ch"], port=port, timeout=2, lifetime=5)
    try:
        with pytest.raises(ZoneTransferError) as err:
            adapter.ping()
        assert err.value.reason == "closed"
    finally:
        sock.close()


def test_unknown_server_name():
    adapter = DnsZoneAdapter("dc01.invalid", ["example.ch"])
    with pytest.raises(ZoneTransferError) as err:
        adapter.ping()
    assert err.value.reason == "dns"


def test_normalize_zones():
    assert normalize_zones("Example.CH.\n10.in-addr.arpa, example.ch;_msdcs.example.ch") == [
        "example.ch",
        "10.in-addr.arpa",
        "_msdcs.example.ch",
    ]
    assert normalize_zones("") == []
    for bad in ("exa mple", "a..b", "-x.example.ch", "x" * 64 + ".ch", "ex/ample.ch"):
        with pytest.raises(ValueError):
            normalize_zones([bad])
    with pytest.raises(ValueError):
        normalize_zones([f"z{i}.example.ch" for i in range(dns_zone.MAX_ZONES + 1)])
    with pytest.raises(ValueError):
        DnsZoneAdapter("dc01", [])


def test_parse_server_and_url():
    assert parse_server("dc01.example.ch") == ("dc01.example.ch", 53)
    assert parse_server("dns://dc01.example.ch:5353") == ("dc01.example.ch", 5353)
    assert parse_server("10.0.0.10:53") == ("10.0.0.10", 53)
    assert parse_server("2001:db8::10") == ("2001:db8::10", 53)
    assert parse_server("[2001:db8::10]:5353") == ("2001:db8::10", 5353)
    assert server_url(" DC01.example.ch ") == "dns://dc01.example.ch"
    assert server_url("dc01:5353") == "dns://dc01:5353"
    assert server_url("2001:db8::10") == "dns://[2001:db8::10]"
    for bad in ("", "https://dc01", "dns://dc01/x", "dc01:99999"):
        with pytest.raises(ValueError):
            parse_server(bad)


def test_read_only_by_protocol():
    """Nie dynamische Updates: das Modul importiert dns.update nicht und ruft von dns.query nur
    den Zonentransfer (xfr) auf."""
    tree = ast.parse(Path(dns_zone.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "dns.update" not in imported and "dns.query" in imported
    calls = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Attribute)
        and n.value.attr == "query"
    }
    assert calls == {"xfr"}
