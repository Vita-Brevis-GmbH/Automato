# TLS-Proxy für die Automato-Web-UI (Profil `proxy`)

Macht die Web-UI unter einer HTTPS-Adresse erreichbar (z. B. `https://172.25.12.12` oder
`https://automato.int.example.ch`), statt `http://<VM>:5173`. Das braucht der
Keycloak-Login: PKCE funktioniert im Browser nur über HTTPS oder `localhost`.

```
Browser ──HTTPS 443──▶ Caddy (proxy) ──▶ web-ui:5173 (Vite) ──/api──▶ orchestrator:8000
```

Optional: ohne das Profil `proxy` bleibt der Stack wie bisher.

## Variante A — Dev/Lab: per IP, selbst erzeugtes Zertifikat (Default)
Kein DNS, keine CA nötig. Caddy erzeugt das Zertifikat selbst (eigene lokale CA).

In der `.env` (Repo-Wurzel):

```env
AUTOMATO_UI_HOST=172.25.12.12
# AUTOMATO_UI_TLS=internal   (Default, muss nicht gesetzt werden)
```

```bash
docker compose --profile core --profile proxy run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile
docker compose --profile core --profile proxy up -d
docker compose --profile core --profile proxy ps proxy                                    # Up, nicht Restarting
curl -sk -o /dev/null -w "%{http_code}\n" https://172.25.12.12/   # 200
```

Der Browser warnt beim ersten Aufruf („Verbindung nicht privat“), weil er Caddys CA nicht
kennt: *Erweitert → Weiter zu 172.25.12.12*. Ohne Warnung: Caddys Root-Zertifikat einmal
exportieren und auf dem Client vertrauen:

```bash
docker compose --profile core --profile proxy cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-dev-root.crt
```

Auf Windows: Datei doppelklicken → *Zertifikat installieren* → *Aktueller Benutzer* →
*Alle Zertifikate in folgendem Speicher speichern* → **Vertrauenswürdige
Stammzertifizierungsstellen**. Die Root bleibt gleich, solange das Volume `proxy-data`
besteht. Nur für Dev — produktiv Variante B.

## Variante B — eigener Name + Zertifikat der eigenen CA (z. B. AD CS)
1. DNS: `automato` → IP der VM (z. B. AD-Zone).
2. Zertifikat per CSR von der CA ausstellen lassen (Ablauf wie in
   `deploy/keycloak/PROD-INSTALL.md`, Abschnitt 12a), als Fullchain `<host>.crt` + Key
   `<host>.key` nach `deploy/proxy/pki/` (per `.gitignore` vom Repo ausgeschlossen).
3. `.env`:
   ```env
   AUTOMATO_UI_HOST=automato.int.example.ch
   AUTOMATO_UI_TLS="/etc/caddy/pki/automato.int.example.ch.crt /etc/caddy/pki/automato.int.example.ch.key"
   ```
4. Start/Prüfung wie in Variante A.

## Firewall
Port **443** von den Client-Netzen zur VM erlauben (z. B. FortiGate-Policy; auf der VM ggf.
`ufw allow 443/tcp`).

## Keycloak-Client ergänzen
Realm `automato` → *Clients → `automato-web` → Settings* (Adresse aus `AUTOMATO_UI_HOST`):

- **Valid redirect URIs:** `https://<AUTOMATO_UI_HOST>/*` hinzufügen
- **Valid post logout redirect URIs:** `+`
- **Web origins:** `https://<AUTOMATO_UI_HOST>` hinzufügen

→ *Save*. Danach `https://<AUTOMATO_UI_HOST>` öffnen → Weiterleitung zu Keycloak →
Login (+ OTP) → zurück in Automato.

## Hinweise
- `/api/internal` (Agent-/Ingest-API) liefert der Proxy bewusst nicht aus; Agenten nutzen
  weiter ihren eigenen Zugang (ADR 0008, `collectors/EXTERNAL.md`).
- Die Web-UI läuft hier als Vite-Dev-Server — für Test/Lab gedacht. Produktiv folgt ein
  statischer Build, den Caddy direkt ausliefert.
- Läuft der Proxy, können die Direkt-Ports 5173/8000 später auf `127.0.0.1` gebunden werden.
- **TLS intern** (0.49.0, ADR 0023, `deploy/aufbau.sh tls-intern`): Der Proxy spricht die Web-UI
  dann ebenfalls per HTTPS an und prüft ihr Zertifikat gegen die interne CA
  (`deploy/tls/caddy/upstream-on.caddy`); für den Browser ändert sich nichts.
