"""OIDC/Keycloak-JWT (E1.1b): Claim-Mapping, RSA-Validierung, Bearer-Scoping."""

import jwt
from app.auth import oidc
from app.config import Settings
from app.models import (
    IpamType,
    Platform,
    Rule,
    RuleAction,
    RuleDomain,
    SourceSystem,
    Tenant,
)
from app.models.tenant import DnsType
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _settings(**kw) -> Settings:
    base = {
        "auth_mode": "oidc",
        "oidc_jwks_url": "https://kc/realms/automato/protocol/openid-connect/certs",
        "oidc_roles_claim": "realm_access.roles",
        "oidc_tenants_claim": "tenant_ids",
    }
    base.update(kw)
    return Settings(**base)


def _tenant_with_rule(session, name: str) -> int:
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name=f"fw-{name}", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True
    )
    session.add_all([t, fw])
    session.flush()
    session.add(
        Rule(
            tenant_id=t.id,
            source_system_id=fw.id,
            domain=RuleDomain.FIREWALL,
            external_id="1",
            name=f"r-{name}",
            action=RuleAction.ALLOW,
        )
    )
    session.flush()
    return t.id


def test_claims_to_principal_admin_viewer_und_ohne_tenant():
    s = _settings()
    admin = oidc.claims_to_principal(
        {"sub": "a", "realm_access": {"roles": ["admin", "viewer"]}}, s
    )
    assert admin.is_admin and admin.tenant_ids is None

    viewer = oidc.claims_to_principal(
        {"sub": "v", "realm_access": {"roles": ["viewer"]}, "tenant_ids": [1, 2]}, s
    )
    assert not viewer.is_admin and viewer.tenant_ids == (1, 2)

    # Nicht-Admin ohne tenant_ids-Claim -> leer (fail-closed)
    none = oidc.claims_to_principal({"sub": "x", "realm_access": {"roles": ["viewer"]}}, s)
    assert none.tenant_ids == ()


def test_verify_token_rsa_roundtrip(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = jwt.encode(
        {"sub": "u", "realm_access": {"roles": ["operator"]}}, priv, algorithm="RS256"
    )

    class _SigningKey:
        def __init__(self, k):
            self.key = k

    monkeypatch.setattr(
        oidc.PyJWKClient,
        "get_signing_key_from_jwt",
        lambda self, t: _SigningKey(key.public_key()),
    )
    claims = oidc.verify_token(token, _settings())
    assert claims["realm_access"]["roles"] == ["operator"]


def _signed(claims: dict, monkeypatch) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    public = key.public_key()

    class _SigningKey:
        key = public

    monkeypatch.setattr(oidc.PyJWKClient, "get_signing_key_from_jwt", lambda self, t: _SigningKey())
    return jwt.encode(claims, priv, algorithm="RS256")


def test_abgelehntes_token_nennt_den_grund(client, monkeypatch):
    iss = "https://kc/realms/automato-dev"
    settings = _settings(oidc_issuer=iss, oidc_audience="automato-api")
    monkeypatch.setattr("app.auth.deps.get_settings", lambda: settings)

    def call(claims):
        token = _signed(claims, monkeypatch)
        return client.get("/tenants", headers={"Authorization": f"Bearer {token}"})

    base = {"sub": "u", "iss": iss, "aud": "automato-api", "realm_access": {"roles": ["admin"]}}
    assert call(base).status_code == 200

    r = call({**base, "aud": "account"})  # Audience-Mapper fehlt im Realm
    assert r.status_code == 401 and "audience" in r.json()["detail"]
    r = call({**base, "iss": "https://kc/realms/automato"})  # falscher Realm
    assert r.status_code == 401 and "issuer" in r.json()["detail"]
    r = call({**base, "exp": 1})
    assert r.status_code == 401 and r.json()["detail"] == "token expired"


def test_jwks_nicht_erreichbar_ist_503_nicht_401(client, monkeypatch):
    from jwt.exceptions import PyJWKClientConnectionError

    monkeypatch.setattr("app.auth.deps.get_settings", _settings)

    def boom(self, t):
        raise PyJWKClientConnectionError("Fail to fetch data from the url")

    monkeypatch.setattr(oidc.PyJWKClient, "get_signing_key_from_jwt", boom)
    r = client.get("/tenants", headers={"Authorization": "Bearer x.y.z"})
    assert r.status_code == 503 and "JWKS" in r.json()["detail"]


def test_oidc_mode_bearer_scoping(session_factory, client, monkeypatch):
    seed = session_factory()
    a = _tenant_with_rule(seed, "kunde-a")
    _tenant_with_rule(seed, "kunde-b")
    seed.commit()

    monkeypatch.setattr("app.auth.deps.get_settings", _settings)
    # Token-Validierung umgehen -> Claims direkt (viewer, Mandant a)
    monkeypatch.setattr(
        "app.auth.oidc.verify_token",
        lambda token, settings: {
            "sub": "v",
            "realm_access": {"roles": ["viewer"]},
            "tenant_ids": [a],
        },
    )

    assert client.get("/rules").status_code == 401  # ohne Bearer
    r = client.get("/rules", headers={"Authorization": "Bearer x.y.z"})
    assert r.status_code == 200
    assert {row["tenant_id"] for row in r.json()} == {a}  # auf Mandant a verengt
