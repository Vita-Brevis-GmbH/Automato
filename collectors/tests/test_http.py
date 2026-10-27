"""HTTP-Konfiguration zum Orchestrator: optionales mTLS/CA-Pinning (ADR 0008)."""

from collectors.common.http import client_kwargs
from collectors.config import Settings


def test_default_system_trust():
    assert client_kwargs(Settings())["verify"] is True


def test_verify_abschaltbar():
    assert client_kwargs(Settings(orchestrator_verify_tls=False))["verify"] is False


def _pki(tmp_path):
    """Agent-CA + Client-Zertifikat wie deploy/agent-gateway/make-certs.sh."""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("ca"))
        .issuer_name(name("ca"))
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name("agent-a"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(2)
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    files = {
        "ca.pem": ca.public_bytes(pem),
        "agent.crt": cert.public_bytes(pem),
        "agent.key": key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    }
    for fname, data in files.items():
        (tmp_path / fname).write_bytes(data)
    files["agent.pem"] = files["agent.crt"] + files["agent.key"]
    (tmp_path / "agent.pem").write_bytes(files["agent.pem"])
    return tmp_path


def test_ca_bundle_und_client_cert_im_ssl_kontext(tmp_path):
    """Regression: verify=<pfad> + cert= legte unter httpx 0.28 kein Zertifikat vor."""
    import ssl

    d = _pki(tmp_path)
    k = client_kwargs(
        Settings(
            orchestrator_ca_bundle=str(d / "ca.pem"),
            client_cert=str(d / "agent.crt"),
            client_key=str(d / "agent.key"),
        )
    )
    ctx = k["verify"]
    assert isinstance(ctx, ssl.SSLContext) and "cert" not in k
    assert ctx.verify_mode is ssl.CERT_REQUIRED and ctx.check_hostname
    assert len(ctx.get_ca_certs()) == 1  # nur die gepinnte CA


def test_kombiniertes_pem_und_ohne_pruefung(tmp_path):
    import ssl

    d = _pki(tmp_path)
    ctx = client_kwargs(Settings(client_cert=str(d / "agent.pem"), orchestrator_verify_tls=False))[
        "verify"
    ]
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode is ssl.CERT_NONE


def test_describe_error_nennt_ziel_und_ursache():
    import httpx

    from collectors.common.http import describe_error

    def refuse(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    try:
        client.get("http://host.docker.internal:8080/api/ipam/prefixes/?limit=1000&offset=0")
    except httpx.HTTPError as exc:
        text = describe_error(exc)
    assert text.startswith("nicht erreichbar http://host.docker.internal:8080/api/ipam/prefixes/")
    assert "Connection refused" in text and "limit" not in text
    assert describe_error(RuntimeError("kein Lese-Token")) == "kein Lese-Token"


def test_describe_error_nennt_den_grund_beim_verbindungsaufbau():
    """Dev-Befund 0.33.0: ConnectTimeout bzw. abgelehnte Verbindung mit kurzem Hinweis."""
    import httpx

    from collectors.common.http import describe_error

    def timeout(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    try:
        client.get("http://host.docker.internal:8080/api/ipam/prefixes/")
    except httpx.HTTPError as exc:
        text = describe_error(exc)
    assert text.endswith(
        "ConnectTimeout timed out - keine Antwort: Ziel aus oder Weg gesperrt (Firewall/Netz)"
    )


def test_describe_error_klartext_an_https_orchestrator():
    """Befund 0.78.1: seit TLS intern beendet der Orchestrator auf :8000 eine Klartext-Anfrage
    ohne Antwort - die Meldung nennt https:// und die CA; bei https:// kein solcher Hinweis."""
    import httpx

    from collectors.common.http import describe_error

    def drop(request):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.",
                                        request=request)  # fmt: skip

    client = httpx.Client(transport=httpx.MockTransport(drop))
    texts = []
    for url in ("http://172.25.12.12:8000/internal/agent/update",
                "https://172.25.12.12:8000/internal/agent/update"):  # fmt: skip
        try:
            client.get(url)
        except httpx.HTTPError as exc:
            texts.append(describe_error(exc))
    plain, tls = texts
    assert plain.startswith("RemoteProtocolError: Server disconnected")
    assert "AUTOMATO_ORCHESTRATOR_URL=https://" in plain and "automato-ca.crt" in plain
    assert "TLS intern" not in tls
