# Keycloak produktiv auf einer neuen VM installieren (`auth.vitabrevis.ch`)

Schritt-für-Schritt-Runbook für eine **frische VM**: Paketinstallation + Konfiguration von
Hand, produktionsreif gehärtet (ADR 0007). Ergebnis ist derselbe Stack wie das
Ansible-Playbook (`deploy/keycloak/ansible/`) — nur manuell, damit jeder Schritt sichtbar
ist. Wer einen Ansible-Control-Host hat, nimmt stattdessen das Playbook (identisches
Resultat, ein Befehl).

```
Internet ──TLS 1.3/ACME──▶ Caddy ──http (nur intern)──▶ Keycloak ──▶ Postgres (persistent)
           443/80                   (prod-mode)             (kein veröffentlichter Port)
```

Nur Caddy veröffentlicht Ports. Keycloak (8080) und Postgres (5432) sind **nicht** von
aussen erreichbar. Health/Metrics liegen auf Keycloaks Management-Port 9000 (nur intern).

---

## 0. Voraussetzungen (vorher erledigen)

- **VM:** Ubuntu 26.04 LTS („Resolute") — oder 22.04/24.04 LTS bzw. Debian 12 — 2 vCPU /
  2–4 GB RAM / 20 GB Disk, SSH mit sudo. Dediziert nur für Keycloak (ADR 0007: eigener IdP,
  eigene VM).
- **Wildcard-Zertifikat** (`*.vitabrevis.ch`) als **PEM** bereithalten — **kein Let's
  Encrypt/ACME**:
  - Zertifikatskette (Leaf **+** Intermediates) in **einer** Datei, z. B. `auth.vitabrevis.ch.crt`
  - privater Schlüssel **unverschlüsselt** (ohne Passphrase), z. B. `auth.vitabrevis.ch.key`
- **DNS:** Ein A/AAAA-Record **`auth.vitabrevis.ch` → öffentliche IP der VM**. Prüfen:
  `dig +short auth.vitabrevis.ch`. (Ohne ACME wird Port 80 dafür **nicht** gebraucht.)
- **Firewall am Perimeter/Cloud-Provider:** eingehend nur **22 (SSH) + 443** offen; **80
  nur**, falls ihr den automatischen HTTP→HTTPS-Redirect wollt (sonst weglassen).

---

## 1. VM-Grundhärtung

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo timedatectl set-timezone Europe/Zurich
sudo hostnamectl set-hostname auth-01

# Unbeaufsichtigte Sicherheitsupdates
sudo apt-get -y install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

Optional, aber empfohlen: SSH nur mit Key (kein Passwort), root-Login aus. Dann `sshd`
neu starten.

---

## 2. Docker + Compose installieren (Ubuntu 26.04 „Resolute")

Auf Ubuntu 26.04 am einfachsten und zuverlässig die **Canonical-Pakete** aus `universe`:
`docker-compose-v2` zieht `docker.io` (Engine) + `containerd` als Abhängigkeit mit. Docker
veröffentlicht sein **Upstream-Repo für ein frisches Release oft verzögert** — deshalb
scheitert der Fremd-Repo-Weg mit `404 … resolute Release`.

**Falls die kaputte Docker-Repo-Zeile schon angelegt wurde (wie im Fehlerfall), zuerst weg:**

```bash
sudo rm -f /etc/apt/sources.list.d/docker.list /etc/apt/keyrings/docker.gpg
sudo apt-get update
```

**Installieren (Ubuntu-Pakete):**

```bash
sudo apt-get -y install docker-compose-v2
sudo systemctl enable --now docker
docker --version && docker compose version    # beide müssen eine Version zeigen
```

> **Optional — Docker-Upstream-Pakete** (von Docker Inc., ggf. neuer): nur wenn ausdrücklich
> gewünscht und **nicht mit den Ubuntu-Paketen mischen**. Repo mit **`linux/ubuntu`** (nicht
> `debian`); fehlt euer Codename (`resolute`) noch bei Docker, auf das neueste unterstützte
> LTS zeigen (z. B. `noble`):
>
> ```bash
> sudo apt-get -y install ca-certificates curl
> sudo install -m 0755 -d /etc/apt/keyrings
> curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
>   sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
> sudo chmod a+r /etc/apt/keyrings/docker.gpg
> CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")   # 404? -> CODENAME=noble
> echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
>   https://download.docker.com/linux/ubuntu $CODENAME stable" | \
>   sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
> sudo apt-get update
> sudo apt-get -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
> sudo systemctl enable --now docker
> ```

---

## 3. Firewall auf der VM (ufw): default-deny

```bash
sudo apt-get -y install ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 443/tcp
sudo ufw allow 80/tcp     # optional: nur für HTTP->HTTPS-Redirect; ohne Redirect weglassen
sudo ufw --force enable
sudo ufw status verbose
```

---

## 4. Verzeichnisse + Secrets anlegen

```bash
sudo mkdir -p /opt/automato-keycloak/import
cd /opt/automato-keycloak

# Secrets EINMALIG erzeugen und sicher notieren (Passwort-Manager/Vault):
DB_PW=$(openssl rand -base64 24)
BOOT_PW=$(openssl rand -base64 18)
echo "DB_PW=$DB_PW"      # merken
echo "BOOT_PW=$BOOT_PW"  # merken (temporärer Boot-Admin)
```

Realm-Export auf die VM bringen (aus dem Repo `deploy/keycloak/realm-automato.json`) nach
`/opt/automato-keycloak/import/realm-automato.json` — z. B. per `scp`:

```bash
# vom Arbeitsplatz aus:
scp deploy/keycloak/realm-automato.json ops@<VM>:/tmp/realm-automato.json
# auf der VM:
sudo mv /tmp/realm-automato.json /opt/automato-keycloak/import/realm-automato.json
```

Der Realm bringt die Härtung mit (Brute-Force, Passwort-Policy Länge ≥12 + Mix + History +
Ablauf, **OTP/TOTP-Pflicht**, Security-Header, `registrationAllowed=false`), die Rollen
`viewer/operator/admin`, die Clients `automato-web` (PKCE) / `automato-api` (bearer-only),
den `tenant_ids`-Mapper und den geseedten `automato-admin`.

### Wildcard-Zertifikat + Key auf die VM bringen

```bash
sudo mkdir -p /opt/automato-keycloak/pki
# vom Arbeitsplatz aus:
scp auth.vitabrevis.ch.crt auth.vitabrevis.ch.key ops@<VM>:/tmp/
# auf der VM:
sudo mv /tmp/auth.vitabrevis.ch.crt /opt/automato-keycloak/pki/
sudo mv /tmp/auth.vitabrevis.ch.key /opt/automato-keycloak/pki/
sudo chmod 640 /opt/automato-keycloak/pki/auth.vitabrevis.ch.crt
sudo chmod 600 /opt/automato-keycloak/pki/auth.vitabrevis.ch.key   # Key streng 0600
```

> - Die `.crt` muss die **vollständige Kette** enthalten: **Leaf zuerst, dann Intermediates**.
>   Liegen sie getrennt vor:
>   `cat leaf.crt intermediate.crt > auth.vitabrevis.ch.crt`.
> - Der Key muss **ohne Passphrase** sein (Caddy entschlüsselt nicht interaktiv). Falls
>   verschlüsselt: `openssl rsa -in verschluesselt.key -out auth.vitabrevis.ch.key`.
> - Kette/Key prüfen (Modulus muss übereinstimmen):
>   `openssl x509 -noout -modulus -in auth.vitabrevis.ch.crt | openssl md5` vs.
>   `openssl rsa -noout -modulus -in auth.vitabrevis.ch.key | openssl md5`.

---

## 5. Konfigurationsdateien schreiben

### 5a. `/opt/automato-keycloak/.env` (Secrets, Mode 0600)

```bash
sudo tee /opt/automato-keycloak/.env >/dev/null <<EOF
KC_DB_PASSWORD=$DB_PW
KC_BOOTSTRAP_ADMIN_USERNAME=bootstrap-admin
KC_BOOTSTRAP_ADMIN_PASSWORD=$BOOT_PW
EOF
sudo chmod 600 /opt/automato-keycloak/.env
```

### 5b. `/opt/automato-keycloak/docker-compose.yml`

Pinne eine **exakte** Keycloak-Patch-Version (aktuell die 26.7.x-Linie; neuesten Tag auf
`quay.io/keycloak/keycloak` prüfen, z. B. `26.7.1`):

```bash
sudo tee /opt/automato-keycloak/docker-compose.yml >/dev/null <<'EOF'
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: keycloak
      POSTGRES_USER: keycloak
      POSTGRES_PASSWORD: ${KC_DB_PASSWORD}
    volumes:
      - kc-postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U keycloak"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks: [kc]

  keycloak:
    image: quay.io/keycloak/keycloak:26.7.1
    restart: unless-stopped
    command: ["start", "--import-realm"]
    environment:
      KC_DB: postgres
      KC_DB_URL: "jdbc:postgresql://postgres:5432/keycloak"
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: ${KC_DB_PASSWORD}
      # Öffentliche URL; TLS wird von Caddy terminiert
      KC_HOSTNAME: "https://auth.vitabrevis.ch"
      KC_PROXY_HEADERS: xforwarded
      KC_HTTP_ENABLED: "true"       # nur intern; Caddy spricht http an
      KC_HEALTH_ENABLED: "true"
      KC_METRICS_ENABLED: "true"
      KC_BOOTSTRAP_ADMIN_USERNAME: ${KC_BOOTSTRAP_ADMIN_USERNAME}
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_BOOTSTRAP_ADMIN_PASSWORD}
    volumes:
      - ./import:/opt/keycloak/data/import:ro
      - kc-data:/opt/keycloak/data
    depends_on:
      postgres:
        condition: service_healthy
    networks: [kc]

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./pki:/etc/caddy/pki:ro
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - keycloak
    networks: [kc]

networks:
  kc: { driver: bridge }

volumes:
  kc-postgres:
  kc-data:
  caddy-data:
  caddy-config:
EOF
```

### 5c. `/opt/automato-keycloak/Caddyfile` (eigenes Wildcard-Zertifikat, TLS 1.3, HSTS)

Kein `email`/ACME — Caddy nutzt die Dateien aus `./pki` (Schritt 4). Dass hier ein
**eigenes Zertifikat** angegeben ist, schaltet die automatische Zertifikatsverwaltung für
diese Domain ab.

```bash
sudo tee /opt/automato-keycloak/Caddyfile >/dev/null <<'EOF'
{
    # Eigenes (Wildcard-)Zertifikat -> kein ACME/Let's Encrypt.
    servers {
        protocols h1 h2
    }
}

auth.vitabrevis.ch {
    encode zstd gzip

    # Wildcard-Zertifikat aus ./pki (im Container /etc/caddy/pki). TLS 1.3 only.
    # Nur falls Altclients zwingen: protocols tls1.2 tls1.3
    tls /etc/caddy/pki/auth.vitabrevis.ch.crt /etc/caddy/pki/auth.vitabrevis.ch.key {
        protocols tls1.3 tls1.3
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
        X-Frame-Options "SAMEORIGIN"
        -Server
    }

    reverse_proxy keycloak:8080

    # Optional härter: Admin-Konsole nur aus dem Management-Netz.
    # @admin path /admin/*
    # handle @admin {
    #     @block not remote_ip 10.0.0.0/8 192.168.0.0/16
    #     respond @block 403
    #     reverse_proxy keycloak:8080
    # }
}
EOF
```

---

## 6. Stack starten

```bash
cd /opt/automato-keycloak
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs -f caddy      # lädt das Wildcard-Zertifikat aus ./pki (kein ACME)
sudo docker compose logs -f keycloak   # "Imported realm automato" + "started in ..."
```

Beim ersten Start importiert Keycloak den Realm; Caddy serviert sofort mit eurem
Wildcard-Zertifikat (kein ACME, nichts zu holen). Fehlt/ungültig das Zertifikat, meldet
Caddy das im Log.

---

## 7. Verifikation

```bash
# Öffentliche OIDC-Discovery über TLS (muss 200 + JSON liefern):
curl -s https://auth.vitabrevis.ch/realms/automato/.well-known/openid-configuration | head

# Zertifikat/TLS-Version prüfen:
curl -Iv https://auth.vitabrevis.ch/realms/automato 2>&1 | grep -Ei 'TLS|HTTP/'
```

Admin-Konsole im Browser: **https://auth.vitabrevis.ch/admin/** (grünes Schloss).

---

## 8. Nach dem ersten Start (Admin + OTP, Boot-Admin entfernen)

1. **Als Boot-Admin anmelden:** `https://auth.vitabrevis.ch/admin/`, User
   `bootstrap-admin`, Passwort `$BOOT_PW` (aus Schritt 4). Master-Realm.
2. Realm **`automato`** wählen → **Users → `automato-admin` → Credentials →
   Set password**: ein Initialpasswort, **„Temporary" = On**.
3. **Abmelden**, dann unter `https://auth.vitabrevis.ch/realms/automato/account/` (oder
   beim nächsten Admin-Login im Realm) als `automato-admin` anmelden → Keycloak erzwingt
   **Passwort-Wechsel + OTP/TOTP-Einrichtung** (Authenticator-App scannen).
4. `automato-admin` hat die Realm-Rolle `admin` → Global-Admin (mandantsübergreifend).
5. **Boot-Admin entfernen/rotieren:** Master-Realm → Users → `bootstrap-admin` löschen
   (oder Passwort rotieren). Er ist nur fürs Setup. Danach `KC_BOOTSTRAP_ADMIN_*` aus der
   `.env` entfernen und `docker compose up -d --force-recreate` — Boot-Credentials werden
   nur beim ersten Bootstrap gebraucht.

---

## 9. Dev gegen diese Keycloak testen

Der Realm-Client **`automato-web`** erlaubt bereits den Redirect `http://localhost:5173/*`
— die Dev-Web-UI (Vite) kann sich also direkt gegen die Produktions-Keycloak anmelden.
Orchestrator (Dev) auf OIDC umstellen:

```env
AUTOMATO_AUTH_MODE=oidc
AUTOMATO_OIDC_ISSUER=https://auth.vitabrevis.ch/realms/automato
AUTOMATO_OIDC_AUDIENCE=automato-api
AUTOMATO_OIDC_JWKS_URL=https://auth.vitabrevis.ch/realms/automato/protocol/openid-connect/certs
# Defaults passen: ROLES_CLAIM=realm_access.roles, TENANTS_CLAIM=tenant_ids
```

Die Dev-VM muss `auth.vitabrevis.ch` erreichen (ausgehend HTTPS). Rollen kommen aus
`realm_access.roles`, Mandanten aus dem `tenant_ids`-Claim; `admin` = alle Mandanten.

> **Wenn ihr die Web-UI später produktiv hostet** (eigene URL), im Realm unter
> Clients → `automato-web` die **Redirect-URI** und **Web-Origin** dieser URL ergänzen
> (`https://<web-ui-host>/*`). `localhost:5173` nur für Dev.

Test-User anlegen: Realm `automato` → Users → Add user, Attribut **`tenant_ids`** mit den
erlaubten Mandanten-IDs setzen, Rolle `viewer`/`operator` zuweisen, Passwort setzen.

---

## 10. Betrieb

- **Backup (täglich, DB ist der Zustand):**
  ```bash
  docker compose exec -T postgres pg_dump -U keycloak keycloak | \
    gzip > /var/backups/kc-$(date +%F).sql.gz
  ```
  Per Cron sichern und off-site kopieren. Die Realm-Config lebt nach dem Import in der DB
  (nicht mehr im Import-File) — DB-Backup genügt.
- **Updates:** Image-Tag in `docker-compose.yml` anheben, `docker compose pull && docker
  compose up -d`. DB-Volume bleibt. Vorher Backup. Keycloak-Release-Notes auf Breaking
  Changes prüfen.
- **TLS:** Kein ACME → **Erneuerung ist manuell/extern**. Beim Austausch des Wildcard-
  Zertifikats die zwei Dateien in `./pki` ersetzen (gleiche Namen) und Caddy neu laden:
  `sudo docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`
  (oder `sudo docker compose restart caddy`). **Ablaufdatum überwachen** (z. B. Monitoring
  auf `notAfter`): `openssl x509 -enddate -noout -in /opt/automato-keycloak/pki/auth.vitabrevis.ch.crt`.
- **Logs:** `docker compose logs --since 1h keycloak`.
- **Monitoring:** Health/Readiness intern auf `http://keycloak:9000/health/ready` (nicht
  veröffentlicht) — von einem internen Monitoring-Container im `kc`-Netz abfragen.

---

## 11. Sicherheits-Checkliste (vor Go-Live)

- [ ] DNS zeigt auf die VM, **nur 22 + 443** eingehend offen (80 nur bei Redirect-Wunsch).
- [ ] **Wildcard-Zertifikat** geladen (Kette komplett, Key 0600), **TLS 1.3**, HSTS aktiv
      (Schritt 7); Key **ohne Passphrase**; Ablauf-Überwachung eingerichtet.
- [ ] Keycloak läuft im **prod-mode** (`start`, nicht `start-dev`), `KC_HOSTNAME=https://…`.
- [ ] Keycloak/Postgres haben **keinen** veröffentlichten Port (`docker compose ps` → nur
      Caddy auf 80/443).
- [ ] `.env` ist **0600**, Secrets nur im Passwort-Manager/Vault, **nicht** im Repo.
- [ ] **Boot-Admin entfernt/rotiert**; `automato-admin` hat **OTP** aktiv.
- [ ] Passwort-Policy + Brute-Force + OTP-Pflicht aktiv (aus dem Realm — in der
      Admin-Konsole unter Realm settings → Security defenses / Authentication gegenprüfen).
- [ ] DB-Backup eingerichtet und einmal **Restore getestet**.
- [ ] (Optional) Admin-Konsole per Caddy auf Management-Netz eingeschränkt (Schritt 5c).

---

## Alternative: dasselbe per Ansible (ein Befehl)

Wer einen Control-Host hat, bekommt denselben Stack ohne Handarbeit:

```bash
cd deploy/keycloak/ansible
cp inventory.example.ini inventory.ini                      # VM eintragen
cp group_vars/keycloak.example.yml group_vars/keycloak.yml  # kc_domain: auth.vitabrevis.ch
#   + Wildcard statt ACME: kc_tls_cert / kc_tls_key auf die PEM-Dateien setzen
cat > group_vars/vault.yml <<EOF
kc_bootstrap_admin_password: "$(openssl rand -base64 18)"
kc_db_password: "$(openssl rand -base64 24)"
EOF
ansible-vault encrypt group_vars/vault.yml
ansible-galaxy collection install community.general
ansible-playbook -i inventory.ini keycloak.yml --ask-vault-pass
```

Für euer Wildcard-Zertifikat `kc_tls_cert`/`kc_tls_key` setzen (kein ACME) — Details und
TLS-Modi: `deploy/keycloak/ansible/README.md`.
