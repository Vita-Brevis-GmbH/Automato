# TLS-Proxy für die Automato-Web-UI (Profil `proxy`)

Macht die Web-UI unter einer echten HTTPS-Adresse erreichbar (z. B.
`https://automato.int.vitabrevis.ch`), statt `http://<VM>:5173`. Das braucht der
Keycloak-Login: PKCE funktioniert im Browser nur über HTTPS oder `localhost`.

```
Browser ──HTTPS 443──▶ Caddy (proxy) ──▶ web-ui:5173 (Vite) ──/api──▶ orchestrator:8000
```

Optional: ohne das Profil `proxy` bleibt der Stack wie bisher.

## 1. DNS
Interner DNS-Eintrag (z. B. AD-Zone `int.vitabrevis.ch`): `automato` → IP der Automato-VM.

## 2. Zertifikat (eigene CA, kein ACME)
Interne Namen bekommen ihr Zertifikat von der eigenen CA (z. B. AD CS auf dem DC) —
Domänen-Clients vertrauen ihr bereits. Auf der Automato-VM, im Repo:

```bash
H=automato.int.vitabrevis.ch
mkdir -p deploy/proxy/pki && cd deploy/proxy/pki
openssl req -new -newkey rsa:3072 -nodes -keyout $H.key -out $H.csr \
  -subj "/CN=$H" -addext "subjectAltName=DNS:$H" -addext "extendedKeyUsage=serverAuth"
chmod 600 $H.key
```

CSR auf einen Domänen-Windows-Rechner kopieren (PowerShell; `-config` aus `certutil`):

```powershell
certreq -submit -config "<DC>\<CA-Name>" -attrib "CertificateTemplate:WebServer" automato.int.vitabrevis.ch.csr automato.int.vitabrevis.ch.cer
(Get-Item automato.int.vitabrevis.ch.cer).Length      # > 0
```

Die `.cer` zurück nach `deploy/proxy/pki/`, dann Fullchain bauen (PEM oder DER, CA-Datei
optional — z. B. `issuing-ca.cer` per `Export-Certificate`):

```bash
H=automato.int.vitabrevis.ch; cd deploy/proxy/pki
{ openssl x509 -in $H.cer 2>/dev/null || openssl x509 -inform DER -in $H.cer
  [ -f issuing-ca.cer ] && { openssl x509 -in issuing-ca.cer 2>/dev/null || openssl x509 -inform DER -in issuing-ca.cer; }
} > $H.crt
chmod 644 $H.crt
openssl x509 -in $H.crt -noout -subject -ext subjectAltName
openssl x509 -in $H.crt -noout -pubkey | openssl md5
openssl pkey -in $H.key -pubout | openssl md5          # muss gleich sein
```

Caddy liest Key und Zertifikat im Container; `pki/` ist per `.gitignore` vom Repo
ausgeschlossen.

## 3. Konfiguration + Start
In der `.env` (Repo-Wurzel):

```env
AUTOMATO_UI_HOST=automato.int.vitabrevis.ch
```

```bash
docker compose --profile proxy run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile
docker compose --profile core --profile proxy up -d
docker compose ps proxy                                   # Up, nicht Restarting
curl -sk -o /dev/null -w "%{http_code}\n" --resolve automato.int.vitabrevis.ch:443:127.0.0.1 \
  https://automato.int.vitabrevis.ch/                     # 200
```

Firewall: Port **443** von den Client-Netzen zur Automato-VM erlauben (FortiGate-Policy;
auf der VM ggf. `ufw allow 443/tcp`).

## 4. Keycloak-Client ergänzen
Realm `automato` → *Clients → `automato-web` → Settings*:

- **Valid redirect URIs:** `https://automato.int.vitabrevis.ch/*` hinzufügen
- **Valid post logout redirect URIs:** `+`
- **Web origins:** `https://automato.int.vitabrevis.ch` hinzufügen

→ *Save*. Danach `https://automato.int.vitabrevis.ch` öffnen → Weiterleitung zu Keycloak →
Login (+ OTP) → zurück in Automato.

## Hinweise
- `/api/internal` (Agent-/Ingest-API) liefert der Proxy bewusst nicht aus; Agenten nutzen
  weiter ihren eigenen Zugang (ADR 0008, `collectors/EXTERNAL.md`).
- Die Web-UI läuft hier als Vite-Dev-Server — für Test/Lab gedacht. Produktiv folgt ein
  statischer Build, den Caddy direkt ausliefert.
- Läuft der Proxy, können die Direkt-Ports 5173/8000 später auf `127.0.0.1` gebunden werden.
