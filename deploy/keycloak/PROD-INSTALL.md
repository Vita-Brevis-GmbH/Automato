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

> - Die `.crt` muss die **vollständige Kette** enthalten: **Leaf zuerst, dann Intermediate(s)**.
>   Enthält sie nur das Leaf, verbinden Clients zwar, verwerfen das Zertifikat aber mit
>   „unable to get local issuer certificate" (und ein unsauberes `cat` ohne Zeilenumbruch
>   kann Caddy sogar zum Crashen bringen).
> - **Prüfen, wie viele Zertifikate drin sind** (≥ 2 = Kette vorhanden; 1 = nur Leaf):
>   `grep -c "BEGIN CERTIFICATE" auth.vitabrevis.ch.crt`.
> - **Nur Leaf?** Intermediate besorgen und **sauber** anhängen. Bei öffentlichen CAs
>   (z. B. Sectigo) steht die Intermediate-URL im Leaf (AIA) und lässt sich automatisch
>   ziehen:
>   ```bash
>   cp auth.vitabrevis.ch.crt leaf.crt
>   AIA=$(openssl x509 -in leaf.crt -noout -text | awk -F'URI:' '/CA Issuers/{print $2}' | tr -d ' \r')
>   curl -fsS "$AIA" -o interm.in
>   openssl x509 -in interm.in -noout 2>/dev/null && cp interm.in interm.pem \
>     || openssl x509 -inform DER -in interm.in -out interm.pem
>   # Fullchain sauber neu schreiben (openssl re-serialisiert mit korrekten Zeilenumbrüchen):
>   { openssl x509 -in leaf.crt; openssl x509 -in interm.pem; } > auth.vitabrevis.ch.crt
>   grep -c "BEGIN CERTIFICATE" auth.vitabrevis.ch.crt    # jetzt 2
>   openssl verify -untrusted interm.pem leaf.crt         # erwartet: leaf.crt: OK
>   ```
>   (Alternativ das `.ca-bundle` der CA nehmen: `{ openssl x509 -in leaf.crt; cat ca-bundle; } > auth.vitabrevis.ch.crt`.)
> - Der Key muss **ohne Passphrase** sein (Caddy entschlüsselt nicht interaktiv). Falls
>   verschlüsselt: `openssl rsa -in verschluesselt.key -out auth.vitabrevis.ch.key`.
> - Kette/Key gehören zusammen? Beide Public-Keys im **gleichen** Format vergleichen:
>   `openssl x509 -in auth.vitabrevis.ch.crt -noout -pubkey | openssl md5` vs.
>   `openssl pkey -in auth.vitabrevis.ch.key -pubout | openssl md5` — MD5 müssen gleich sein.
> - Nach jeder Änderung Config prüfen:
>   `docker run --rm -v $PWD/Caddyfile:/etc/caddy/Caddyfile:ro -v $PWD/pki:/etc/caddy/pki:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile`.

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

> **Troubleshooting — „This transfer is blocked" / UTM-Firewall.** Blockt eine FortiGate
> (o. ä.) den Zugriff, während der lokale curl (Schritt 7) funktioniert, liegt es an der
> Firewall im Client-Pfad, nicht am Server. Zwei häufige Fälle:
> - **`Event Type: waf-signature` (WAF).** Eine Web Application Firewall (z. B. Klasse
>   „Generic Attacks") hält die zufälligen OIDC-Querys (`code_challenge`/`state`/`nonce`,
>   Base64url) für encodierte Angriffe — ein klassischer False Positive vor einem IdP. Für
>   einen internen IdP am robustesten: den Keycloak-Host **von der WAF ausnehmen** (eigene
>   Firewall-Policy ohne WAF-Profil für die IdP-VM, oder URL-Ausnahmen für `/realms/*`,
>   `/admin/*`, `/resources/*`). Nur die auslösende Signaturklasse auf Monitor zu setzen
>   entschärft meist nur den ersten von mehreren False Positives.
> - **`Event Type: signature` (IPS/AV) bei SSL-Deep-Inspection.** Host von der SSL-Inspection
>   ausnehmen (Security Profiles → SSL/SSH Inspection → *Exempt from SSL Inspection*), die
>   Policy auf `certificate-inspection` stellen, oder die Signatur als Ausnahme freigeben.

---

## 8. Nach dem ersten Start (Admin + OTP, Boot-Admin ablösen)

Es gibt **zwei verschiedene Admins** — nicht verwechseln:
- **Keycloak-Administrator** (Realm `master`): verwaltet Keycloak selbst (Realms, Clients,
  User). Anfangs nur der temporäre `bootstrap-admin`.
- **`automato-admin`** (Realm `automato`, Rolle `admin`): Global-Admin **der
  Automato-Plattform** (mandantsübergreifend). Kann sich **nicht** an der
  Keycloak-Admin-Konsole anmelden.

> ⚠️ Den `bootstrap-admin` **erst löschen, wenn ein dauerhafter Master-Admin existiert und
> getestet ist** — sonst ist die Keycloak-Verwaltung ausgesperrt.

1. **Als Boot-Admin anmelden:** Admin-Konsole (bei Internet-Betrieb über den internen Namen,
   Schritt 12: `https://auth.int.vitabrevis.ch/admin/`), User `bootstrap-admin`,
   Passwort `$BOOT_PW` (aus Schritt 4).
2. **Dauerhaften Master-Admin anlegen** (Realm `master`): *Users → Add user* (persönliches
   Konto) → *Credentials → Set password* (Temporary = On) → *Role mapping → Assign role* →
   Realm-Rolle **`admin`** → *Required user actions*: **Configure OTP**.
   OTP für alle neuen User erzwingen: *Authentication → Required actions → Configure OTP →
   Set as default action* **On**. Hat ein User OTP eingerichtet, verlangt der Standard-Flow
   es bei jedem Login.
   > ⚠️ **Nicht** den Unterflow „Browser – Conditional 2FA" auf *Required* stellen. In
   > Keycloak 26 steht das OTP-Formular darin als *Alternative*: User ohne OTP kommen dann
   > nicht mehr durch (Fehlerseite „We are sorry… Invalid username or password" für
   > **alle** — sperrt die Admin-Konsole). Reparatur siehe Abschnitt 10.
3. **Abmelden, mit dem neuen Master-Admin anmelden** → Passwort-Wechsel + OTP einrichten.
   Prüfen, dass alles funktioniert (z. B. Realm `automato` öffnen).
4. **`automato-admin` (Plattform-Admin) aktivieren:** Realm **`automato`** → *Users →
   `automato-admin` → Credentials → Set password* (Temporary = On). Beim ersten Login
   (z. B. `https://auth.vitabrevis.ch/realms/automato/account/`) erzwingt Keycloak
   **Passwort-Wechsel + OTP**.
5. **Erst jetzt den Boot-Admin entfernen:** Realm `master` → *Users* → `bootstrap-admin`
   löschen (angemeldet als der neue Admin). Danach die Boot-Variablen aus `.env` **und**
   `docker-compose.yml` entfernen und Keycloak neu erstellen:
   ```bash
   cd /opt/automato-keycloak
   sed -i '/^KC_BOOTSTRAP_ADMIN_/d' .env
   sed -i '/KC_BOOTSTRAP_ADMIN_/d' docker-compose.yml
   grep -n BOOTSTRAP .env docker-compose.yml     # keine Treffer mehr
   docker compose up -d --force-recreate keycloak
   ```
   Die Boot-Credentials werden nur beim allerersten Start gebraucht.

---

## 9. Automato anbinden (Realm `automato`, lokale User, OIDC)

### 9a. Einmalig im Realm `automato` (Admin-Konsole)

Der Realm-Import läuft nur beim ersten Start (`IGNORE_EXISTING`) — spätere Ergänzungen im
`realm-automato.json` landen **nicht** automatisch in einem bestehenden Keycloak. Darum auf
einer bestehenden Installation von Hand:

1. **Audience-Mapper** (sonst lehnt der Orchestrator jedes Token mit 401 ab — er prüft
   `aud = automato-api`): *Clients → `automato-web` → Client scopes →
   `automato-web-dedicated` → Configure a new mapper → **Audience*** — Name
   `audience-automato-api`, *Included Client Audience* `automato-api`, *Add to access token*
   **On**, *Add to ID token* Off → Save.
2. **`tenant_ids` als verwaltetes Attribut** (steuert das Mandanten-Scoping): *Realm
   settings → User profile → Create attribute* — Name `tenant_ids`, Display name
   „Mandanten-IDs", **Multivalued On**, Required Off, *Who can edit*: **nur Admin**,
   *Who can view*: Admin (User optional). Optional Validator `integer`.
   Wichtig: User dürfen das Attribut **nicht** selbst bearbeiten (sonst könnten sie sich
   über die Account-Konsole fremde Mandanten geben).
3. **OTP für alle lokalen User erzwingen:** *Authentication → Required actions → Configure
   OTP → Set as default action* **On** (neue User müssen OTP beim ersten Login einrichten);
   bei bereits bestehenden Usern die Required action *Configure OTP* setzen. Den Browser-Flow
   **nicht** ändern (siehe Warnung in Schritt 8). Gilt für Passwort-Logins; über SAML-Broker
   angemeldete User authentisieren am Kunden-IdP.
4. **Redirect-URIs/Web-Origins** des Clients `automato-web` passend zur UI-URL
   (*Clients → `automato-web` → Settings*): Dev `http://localhost:5173/*` (ist drin),
   produktiv `https://<web-ui-host>/*` + Web-Origin `https://<web-ui-host>`.

### 9b. Lokale User anlegen

*Realm `automato` → Users → Create new user* (Username, E-Mail, *Email verified* On) →
*Credentials → Set password* (Temporary On) → *Role mapping → Assign role* → Realm-Rolle
`viewer` / `operator` / `admin` → im Tab *Details* das Feld **Mandanten-IDs** mit den IDs
der erlaubten Mandanten füllen (IDs aus dem Automato-UI-Tab „Mandant / Standorte" bzw.
`GET /tenants`). `admin` sieht immer alle Mandanten; `viewer`/`operator` ohne
Mandanten-IDs sehen nichts (fail-closed).

### 9c. Orchestrator auf OIDC umstellen

In der `.env` des Automato-Stacks (Repo-Wurzel; `docker-compose.yml` reicht die Werte an
den Orchestrator durch):

```env
AUTOMATO_AUTH_MODE=oidc
AUTOMATO_OIDC_ISSUER=https://auth.vitabrevis.ch/realms/automato
AUTOMATO_OIDC_AUDIENCE=automato-api
AUTOMATO_OIDC_JWKS_URL=https://auth.vitabrevis.ch/realms/automato/protocol/openid-connect/certs
# Defaults passen: ROLES_CLAIM=realm_access.roles, TENANTS_CLAIM=tenant_ids, WEB_CLIENT_ID=automato-web
```

```bash
docker compose --profile core up -d --force-recreate orchestrator
curl -s http://localhost:8000/auth/config      # auth_mode "oidc", issuer, client_id
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/rules   # 401 ohne Token = korrekt
```

Die Automato-VM muss `auth.vitabrevis.ch` erreichen (ausgehend HTTPS, für die JWKS).

### 9d. Web-UI-Login testen (Dev)

Der Browser braucht für PKCE einen **sicheren Kontext** (HTTPS oder `localhost`) — über
`http://<VM-IP>:5173` schlägt der Login fehl (*crypto.subtle* fehlt). Für Dev deshalb per
SSH-Tunnel, dann passt auch die bereits erlaubte Redirect-URI `http://localhost:5173/*`:

```bash
ssh -L 5173:localhost:5173 -L 8000:localhost:8000 <user>@<automato-vm>
# Browser: http://localhost:5173 -> Umleitung zu Keycloak -> Login (+ OTP) -> zurück
```

Bei Internet-Betrieb hinter WAF (Schritt 12): Holt der Browser das Token, geht ein POST an
`…/protocol/openid-connect/token` (Body mit `redirect_uri`). Blockt die WAF diesen Request
(WAF-Log), die URL-Access-Regel 20 auf `(auth|token|logout)` erweitern.

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
- **TLS:** Kein ACME → **Erneuerung ist manuell/extern**. Beim Austausch das neue
  Zertifikat wieder als **Fullchain** (Leaf + Intermediate, siehe Schritt 4) nach `./pki`
  legen (gleiche Dateinamen) und Caddy neu laden:
  `sudo docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`
  (oder `sudo docker compose restart caddy`). **Ablaufdatum überwachen** (z. B. Monitoring
  auf `notAfter`): `openssl x509 -enddate -noout -in /opt/automato-keycloak/pki/auth.vitabrevis.ch.crt`.
- **Logs:** `docker compose logs --since 1h keycloak`. Fehlgeschlagene Logins stehen als
  `LOGIN_ERROR` mit Grund drin: `docker compose logs --since 1h keycloak | grep LOGIN_ERROR`
  — `invalid_user_credentials` = falsches Passwort, `user_not_found` = User existiert in
  diesem Realm nicht, `user_temporarily_disabled` = Brute-Force-Sperre.
- **Alle Browser-Logins enden auf der Fehlerseite „We are sorry… Invalid username or
  password"** (statt Login-Formular mit Hinweis), `userId="null"` im Log, `kcadm` geht aber:
  Der 2FA-Unterflow steht auf *Required* (siehe Warnung Schritt 8). Zurückstellen:
  ```bash
  docker compose exec keycloak /opt/keycloak/bin/kcadm.sh get \
    authentication/flows/browser/executions -r master --fields id,displayName,requirement
  # ID von "Browser - Conditional 2FA" einsetzen:
  docker compose exec keycloak /opt/keycloak/bin/kcadm.sh update \
    authentication/flows/browser/executions -r master \
    -b '{"id":"<ID>","requirement":"CONDITIONAL"}'
  ```
- **Admin-Zugang wiederherstellen (ausgesperrt):** Keycloak 26 kann per CLI einen neuen
  temporären Admin im Master-Realm anlegen (Passwort wird abgefragt):
  ```bash
  cd /opt/automato-keycloak
  docker compose stop keycloak
  docker compose run --rm --no-deps keycloak bootstrap-admin user --username recovery-admin
  docker compose start keycloak
  ```
  Damit anmelden, gesperrte User entsperren bzw. Passwörter neu setzen, danach
  `recovery-admin` wieder löschen. Brute-Force im Master-Realm deshalb auf *Lockout
  temporarily* (nicht permanent) und eine nicht zu kleine Fehlversuchsgrenze stellen.
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
- [ ] Internet-exponiert? Admin-Konsole **nicht** öffentlich, nur über den internen Namen
      aus dem Admin-Netz (Schritt 12); WAF-Ausnahme eng gescoped + Negativ-Test bestanden.

---

## 12. Internet-Betrieb: Admin intern trennen + WAF-Ausnahme (gemeinsamer Virtual Server)

Ausgangslage: `auth.vitabrevis.ch` ist aus dem Internet erreichbar (Kunden-Logins) und läuft
mit anderen Tools über **einen** FortiGate-Virtual-Server (eine IP, Host-Routing per FQDN) →
**eine** Policy, **ein** WAF-Profil. Die WAF-Klasse „Generic Attacks" hält OIDC-Querys
(v. a. `redirect_uri=https://…`) für Angriffe — das trifft **jeden** Login, auch Kunden.
Ziel: Admin-Oberfläche gar nicht öffentlich, WAF nur für den IdP minimal lockern, die
anderen Tools unverändert lassen.

### 12a. Admin auf internen Hostnamen (`auth.int.vitabrevis.ch`)

- **DNS (AD-Zone `int.vitabrevis.ch`):** A-Record `auth` → `172.25.4.13`. Der Admin-Zugriff
  läuft damit direkt auf die VM, **nicht** über den Virtual Server/die WAF.
- **Keycloak** (`docker-compose.yml`, Service `keycloak`):
  `KC_HOSTNAME_ADMIN: "https://auth.int.vitabrevis.ch"` ergänzen.
- **Zertifikat** — das Wildcard `*.vitabrevis.ch` deckt `auth.int.vitabrevis.ch` **nicht** ab.
  Empfohlen: von der **DC-CA (AD CS)** ausstellen lassen (wie LDAPS; Domänen-Clients
  vertrauen der Wurzel bereits):
  ```bash
  cd /opt/automato-keycloak/pki
  openssl req -new -newkey rsa:3072 -nodes \
    -keyout auth.int.vitabrevis.ch.key -out auth.int.vitabrevis.ch.csr \
    -subj "/CN=auth.int.vitabrevis.ch" \
    -addext "subjectAltName=DNS:auth.int.vitabrevis.ch" \
    -addext "extendedKeyUsage=serverAuth"
  chmod 600 auth.int.vitabrevis.ch.key
  ```
  Auf einem Domänen-Windows-Rechner (CSR dorthin kopieren; CA-Name zeigt `certutil`):
  ```powershell
  certreq -submit -attrib "CertificateTemplate:WebServer" auth.int.vitabrevis.ch.csr auth.int.vitabrevis.ch.cer
  certutil -config "<DC>\<CA-Name>" -ca.cert issuing-ca.cer
  ```
  Beide `.cer` nach `/tmp` auf die VM, dann Fullchain bauen (PEM oder DER, beides geht):
  ```bash
  cd /opt/automato-keycloak/pki
  for f in /tmp/auth.int.vitabrevis.ch.cer /tmp/issuing-ca.cer; do
    openssl x509 -in "$f" 2>/dev/null || openssl x509 -inform DER -in "$f"
  done > auth.int.vitabrevis.ch.crt
  chmod 640 auth.int.vitabrevis.ch.crt
  grep -c "BEGIN CERTIFICATE" auth.int.vitabrevis.ch.crt          # 2
  openssl x509 -in auth.int.vitabrevis.ch.crt -noout -pubkey | openssl md5
  openssl pkey -in auth.int.vitabrevis.ch.key -pubout | openssl md5  # muss gleich sein
  ```
  Alternative ohne CA: im Caddyfile `tls internal` (Caddy erzeugt selbst ein Zertifikat);
  Browser warnen dann, bis Caddys Root (`/data/caddy/pki/authorities/local/root.crt` im
  Caddy-Container) per GPO verteilt ist — mit AD CS entfällt das.

- **Caddyfile** — öffentlicher Host sperrt Admin, interner Host nur aus dem Admin-Netz:
  ```caddy
  {
      servers {
          protocols h1 h2
      }
  }

  auth.vitabrevis.ch {
      encode zstd gzip
      tls /etc/caddy/pki/auth.vitabrevis.ch.crt /etc/caddy/pki/auth.vitabrevis.ch.key {
          protocols tls1.3 tls1.3
      }
      header {
          Strict-Transport-Security "max-age=31536000; includeSubDomains"
          X-Content-Type-Options "nosniff"
          Referrer-Policy "no-referrer"
          X-Frame-Options "SAMEORIGIN"
          -Server
      }
      @admin path /admin /admin/* /realms/master /realms/master/*
      respond @admin 404
      reverse_proxy keycloak:8080
  }

  auth.int.vitabrevis.ch {
      encode zstd gzip
      tls /etc/caddy/pki/auth.int.vitabrevis.ch.crt /etc/caddy/pki/auth.int.vitabrevis.ch.key {
          protocols tls1.3 tls1.3
      }
      header {
          Strict-Transport-Security "max-age=31536000; includeSubDomains"
          X-Content-Type-Options "nosniff"
          Referrer-Policy "no-referrer"
          X-Frame-Options "SAMEORIGIN"
          -Server
      }
      @notadmin not remote_ip <ADMIN-SUBNETZ-1> <ADMIN-SUBNETZ-2>
      respond @notadmin 403
      reverse_proxy keycloak:8080
  }
  ```
- **FortiGate (intern):** eigene Policy *Client → DMZ*, Source = Admin-Adressgruppe,
  Destination = `172.25.4.13`, Service HTTPS, **NAT aus** (sonst sieht Caddy nur die
  FortiGate-IP und `remote_ip` greift nicht), kein WAF nötig (intern, IP-beschränkt, MFA).
  Achtung: der interne Name geht **direkt** auf die Server-IP, nicht über die VIP — ohne
  diese Policy gibt es einen Timeout.
- **Falls die Konsole mit *„Timeout when waiting for 3rd party check iframe message"*
  hängen bleibt:** Direkt nach `--force-recreate` (Keycloak startet noch) tritt das
  vorübergehend auf — erst neu laden (Inkognito). Bleibt der Fehler, zeigen Login/
  3p-Cookie-Check der Konsole auf den öffentlichen Namen, wo `/realms/master` gesperrt ist
  (prüfen: F12 → Netzwerk → `3p-cookies`). Dann die Frontend-URL des Master-Realms auf den
  internen Namen setzen, per `kcadm` im Container:
  ```bash
  cd /opt/automato-keycloak
  docker compose exec keycloak /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 --realm master --user bootstrap-admin   # Passwort-Abfrage
  docker compose exec keycloak /opt/keycloak/bin/kcadm.sh update realms/master \
    -s 'attributes.frontendUrl=https://auth.int.vitabrevis.ch'
  docker compose exec keycloak /opt/keycloak/bin/kcadm.sh get realms/master --fields attributes
  ```
  (Später auch in der Konsole: Realm `master` → *Realm settings → General → Frontend URL*.)
  Die Kunden-Realms behalten `auth.vitabrevis.ch`.

### 12b. WAF-Ausnahme nur für `auth.vitabrevis.ch` im gemeinsamen Profil

Policy und Profil bleiben für alle Tools gleich; im Profil eine **URL-Access-Regel**, die
auf Host **und** Pfad gescoped ist. Minimal anfangen (nur der Endpoint, der auslöst) und
nur bei weiteren Log-Treffern gezielt erweitern:
```
config firewall address
    edit "FQDN_auth.vitabrevis.ch"
        set type fqdn
        set fqdn "auth.vitabrevis.ch"
    next
end
config waf profile
    edit "WAF_vs-https-vb"
        config url-access
            edit 10
                set address "FQDN_auth.vitabrevis.ch"
                set action block
                set log enable
                config access-pattern
                    edit 1
                        set pattern "^/(admin|realms/master)(/|$)"
                        set regex enable
                    next
                end
            next
            edit 20
                set address "FQDN_auth.vitabrevis.ch"
                set action bypass
                set log enable
                config access-pattern
                    edit 1
                        set pattern "^/realms/[^/]+/protocol/openid-connect/(auth|logout)"
                        set regex enable
                    next
                end
            next
        end
    next
end
```
Regel 10 sperrt Admin zusätzlich schon an der Edge, Regel 20 lässt nur den
OIDC-Authorization-/Logout-Endpoint des IdP an der WAF vorbei.

**Pflicht: Negativ-Test** — auf einer geteilten VIP haben alle Namen dieselbe IP; je nach
FortiOS-Version/Modus kann ein FQDN-Adressobjekt per IP statt per Host-Header matchen.
Dann würde die Ausnahme für **alle** Tools gelten. Prüfen (von extern):
```bash
# 1) Muss weiter BLOCKIERT werden (anderes Tool, gleicher Pfad, Angriffsmuster):
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<anderes-tool>.vitabrevis.ch/realms/x/protocol/openid-connect/auth?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
# 2) Muss weiter BLOCKIERT werden (IdP, aber Pfad nicht freigegeben):
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://auth.vitabrevis.ch/realms/x/account?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
# 3) Kunden-Login im Browser funktioniert, kein WAF-Log-Eintrag für auth.vitabrevis.ch.
```
Kommt bei (1) **keine** Blockseite, greift das Host-Scoping nicht → Regel 20 sofort
entfernen. Fallback: eigene öffentliche IP + eigene VIP/Policy/WAF-Profil für den IdP
(saubere Trennung) oder — als letzte Option — nur die eine auslösende Signatur-ID abschalten.

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
