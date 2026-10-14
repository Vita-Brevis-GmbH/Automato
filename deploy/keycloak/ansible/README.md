# Keycloak-Produktion – Ansible-Playbook (ADR 0007)

Kleines Playbook, das Keycloak **produktiv** auf einer **eigenen VM** aufsetzt:
gehärtet, mit TLS, persistenter DB und Realm-Import. Entspricht ADR 0007 (dedizierter
IdP, eigener Container → eigene VM, eigene URL) und dem Open-Source-Grundsatz
(Keycloak, Postgres, Caddy).

## Was es baut

Einen Docker-Compose-Stack auf der Zielmaschine (`/opt/automato-keycloak`):

```
Internet ──TLS/ACME──▶ Caddy ──http (intern)──▶ Keycloak ──▶ Postgres (persistent)
                       (443/80)                  (prod-mode)    (nur internes Netz)
```

Nur Caddy veröffentlicht Ports. Keycloak und Postgres sind **nicht** von aussen
erreichbar. Health/Metrics liegen auf Keycloaks Management-Port 9000 (nur intern).

## Voraussetzungen

- Ziel-VM (Debian/Ubuntu) mit SSH-Zugang (`become`/sudo).
- Öffentliches DNS für `kc_domain` zeigt auf die VM (für Let's Encrypt). Im Lab ohne
  DNS: `kc_tls_internal: true` → Caddy nutzt eine interne CA (Self-Signed).
- Ansible-Collections: `community.general` (für `ufw`). `ansible-galaxy collection
  install community.general`.

## Einrichten

```bash
cd deploy/keycloak/ansible
cp inventory.example.ini inventory.ini                 # VM eintragen
cp group_vars/keycloak.example.yml group_vars/keycloak.yml   # kc_domain etc.

# Secrets getrennt + verschlüsselt:
cat > group_vars/vault.yml <<'EOF'
kc_bootstrap_admin_password: "$(openssl rand -base64 24)"
kc_db_password: "$(openssl rand -base64 24)"
EOF
ansible-vault encrypt group_vars/vault.yml

ansible-galaxy collection install community.general
ansible-playbook -i inventory.ini keycloak.yml --ask-vault-pass
```

Das Playbook prüft zuerst die Pflicht-/Secret-Variablen (fail-closed), rendert Compose,
`.env` (Mode 0600), `Caddyfile`, setzt die Firewall (nur 22/80/443) und wartet am Ende,
bis die öffentliche OIDC-Discovery von `kc_domain` antwortet.

## Härtung (eingebaut)

- **TLS**: Caddy terminiert, automatische Zertifikate (ACME), **TLS 1.3 only** (wie
  Projekt Magister; im `Caddyfile.j2` auf 1.2 lockerbar), HSTS + Security-Header.
- **Keycloak prod-mode** (`start`, nicht `start-dev`), `KC_HOSTNAME=https://…`,
  `KC_PROXY_HEADERS=xforwarded`; HTTP nur intern, nie veröffentlicht.
- **Persistente DB** (Postgres mit Volume) statt H2 — User/OTP/Sessions überleben.
- **Realm-Härtung** (aus `realm-automato.json`, gilt dev + prod): Brute-Force-Schutz,
  Passwort-Policy (Länge 12, Mischung, History, Ablauf), **OTP (TOTP) Pflicht** für den
  geseedten Admin, Browser-Security-Header, `registrationAllowed=false`.
- **Secrets** aus Ansible Vault, `.env` mit Mode 0600; nichts Geheimes im Repo. Dieselbe
  Quelle wie der Secrets-Provider der Plattform (E1.5, `deploy/secrets/README.md`).
- **Firewall** (ufw): default deny incoming, nur SSH + HTTP/HTTPS.

## Nach dem ersten Start

1. Mit dem **Boot-Admin** (`kc_bootstrap_admin_user`) an der Admin-Konsole anmelden,
   dem geseedten `automato-admin` ein Initialpasswort setzen (Temporary = an).
2. `automato-admin` meldet sich an → Keycloak erzwingt **Passwort-Wechsel + OTP**.
3. Danach den temporären Boot-Admin **entfernen oder rotieren** (er ist nur fürs Setup).
4. Pro Kunde: SAML-Identity-Broker bzw. lokale User + `tenant_ids`-Attribut pflegen.
5. Orchestrator binden: `AUTOMATO_AUTH_MODE=oidc`, Issuer/JWKS/Audience auf
   `https://{{ kc_domain }}/realms/automato` (siehe `deploy/keycloak/README.md`).

## Updates

Image-Tag in `group_vars` anheben und Playbook erneut laufen lassen; der Compose-Up
zieht das neue Image und rekonfiguriert idempotent. DB-Volume bleibt erhalten.
