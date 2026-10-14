# Keycloak (AuthN/RBAC, E1 · ADR 0007)

Dedizierter IdP für Automato (und später Magister). **Dev** läuft als Container in der
Automato-VM (docker-compose, Profil `auth`); **Produktion** zieht per Ansible-Playbook
auf eine **eigene VM, eigene URL** mit TLS/Härtung um (Plattform → Keycloak → zurück,
OIDC + PKCE).

- **Dev (hier):** `docker compose --profile auth up keycloak` (unten).
- **Prod:** `deploy/keycloak/ansible/` (eigenes README) — gehärteter Stack
  Caddy (TLS/ACME) + Keycloak prod-mode + Postgres.

## Starten (lokal / Dev)

```bash
docker compose --profile auth up keycloak
# Admin-Konsole: http://localhost:8081  (Master-Admin: KEYCLOAK_ADMIN/KEYCLOAK_ADMIN_PASSWORD)
```

Dev ist bewusst schlank, aber nicht nackt:
- **Nur an `127.0.0.1` gebunden** (kein Zugriff aus dem Netz der VM).
- **Persistentes Volume** `keycloak-data` → lokal angelegte User/OTP überleben Neustarts.
- **Realm-Härtung** (Brute-Force, Passwort-Policy, OTP-Pflicht, Security-Header) steckt
  im importierten `realm-automato.json` und gilt damit dev **und** prod.
- Health-Endpoint aktiv (`KC_HEALTH_ENABLED`, Management-Port 9000).

HTTPS/Reverse-Proxy, eigene VM und externe DB sind der Produktion vorbehalten (Playbook).

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

## Produktion

Siehe `deploy/keycloak/ansible/README.md`. Das Playbook baut auf einer eigenen VM einen
gehärteten Stack: Caddy (TLS 1.3/ACME, HSTS, Security-Header) → Keycloak prod-mode
(`start`, `KC_HOSTNAME`, Proxy-Header) → Postgres (persistent). Secrets via Ansible Vault
(nichts im Repo), Firewall default-deny, OTP-Pflicht + Passwort-Policy aus dem Realm.
