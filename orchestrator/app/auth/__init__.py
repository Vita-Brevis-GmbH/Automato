"""AuthN/RBAC (E1, ADR 0007).

dev-Modus: Standard-Admin-Principal -> unveraendertes Verhalten; Override via
X-Dev-Roles/X-Dev-Tenants fuer Tests/Demo. oidc-Modus: JWT gegen Keycloak
(Issuer/JWKS/Audience, Rollen/Mandanten aus Claims) - Folgeschritt E1.1b.
"""
