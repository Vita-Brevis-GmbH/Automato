# Keycloak (AuthN/RBAC, E1 · ADR 0007)

Dedizierter IdP für Automato (und später Magister). Hier als **Starter** im
docker-compose (Profil `auth`); produktiv zieht Keycloak auf einen **eigenen Container
→ eigene VM, eigene URL** um (Plattform → Keycloak → zurück, OIDC + PKCE).

## Starten (lokal)

```bash
docker compose --profile auth up keycloak
# Admin-Konsole: http://localhost:8081  (Master-Admin: KEYCLOAK_ADMIN/KEYCLOAK_ADMIN_PASSWORD)
```

Beim Start importiert Keycloak den Realm **`automato`** aus `realm-automato.json`:
- Realm-Rollen **`viewer` / `operator` / `admin`**.
- Clients **`automato-web`** (public, Standard-Flow + **PKCE**) und **`automato-api`**
  (bearer-only, Resource-Server).
- Protocol-Mapper **`tenant_ids`** (User-Attribut → Token-Claim, multivalued) — steuert
  das Mandanten-Scoping im Orchestrator.
- Geseedter **`automato-admin`** mit Rolle `admin` und erzwungenem
  **`UPDATE_PASSWORD` + `CONFIGURE_TOTP`** (OTP) beim ersten Login.

## Erstes Login / OTP

`automato-admin` hat bewusst **kein** Passwort im Repo. Ops setzt das Initialpasswort
einmalig (Admin-Konsole → Users → Credentials, „Temporary" an) oder per `kcadm`; beim
ersten Login erzwingt Keycloak **Passwort-Wechsel + OTP-Einrichtung**. Lokale User und
**SAML**-Föderation (Kunden-IdP) werden in Keycloak als Identity-Broker konfiguriert.

## Orchestrator an Keycloak binden

```env
AUTOMATO_AUTH_MODE=oidc
AUTOMATO_OIDC_ISSUER=http://localhost:8081/realms/automato
AUTOMATO_OIDC_AUDIENCE=automato-api
AUTOMATO_OIDC_JWKS_URL=http://localhost:8081/realms/automato/protocol/openid-connect/certs
# Defaults passen zu Keycloak:
# AUTOMATO_OIDC_ROLES_CLAIM=realm_access.roles
# AUTOMATO_OIDC_TENANTS_CLAIM=tenant_ids
```

Der Orchestrator validiert dann das Bearer-JWT (JWKS/Issuer/Audience), liest Rollen aus
`realm_access.roles` und Mandanten aus `tenant_ids`; `admin` = alle Mandanten.

> Dies ist ein funktionsfähiger Ausgangspunkt, kein gehärtetes Prod-Setup (eigene VM,
> TLS/Caddy davor, persistente DB für Keycloak, Realm-Hardening folgen).
