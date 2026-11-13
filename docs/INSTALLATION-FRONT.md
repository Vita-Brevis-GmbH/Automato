# Front-Proxy auf der Automato-VM (Startseiten, Portal, Weiterleitung je Werkzeug)

Seit 1.2.0 (ADR 0044 Nachtrag). Der Proxy von Automato (Profil `proxy`, Caddy) ist zugleich der
Eingang für die Kunden: Er liefert die Startseite je Kunde und das interne Portal aus und leitet
je Werkzeug weiter. Das Werkzeug `deploy/front/front.sh` erzeugt dafür die Einträge und Seiten.
Seit 1.4.0 (ADR 0051) steht davor das **Anmelde-Tor**: eine Anmeldung im Realm des Kunden für
Startseite und alle Werkzeuge (Abschnitt 6).

```
Kunden / Mitarbeitende ──▶ WAF (TLS mit dem öffentlichen Wildcard) ──▶ Automato-VM :443
   <kunde>.mgmt.example.ch        Startseite (Kacheln)
   <kunde>-nb.mgmt.example.ch     ──▶ NetBox-Host (docs/INSTALLATION-NETBOX-HOST.md)
   <kunde>-doc.mgmt.example.ch    ──▶ Oberfläche von Automato (nur mit --doc)
   <kunde>-um.mgmt.example.ch     ──▶ Magister, Benutzerverwaltung (nur mit --um)
   <kunde>-cmdb.mgmt.example.ch   ──▶ CMDB (später, nur mit --cmdb)
Management-Netz ──▶ Automato-VM :443 direkt
   start.mgmt.int.example.ch      Portal mit allen Kunden
   mgmt.int.example.ch            Verwaltung (Cockpit, Version 2) – bis dahin aufs Portal
```

- **Kunden-Adressen** nur von der **WAF** (und zum Testen aus dem Management-Netz); der Angabe
  `X-Forwarded-For` wird nur von der WAF geglaubt. **Interne Adressen** nur aus dem
  Management-Netz und **nie über die WAF** – auch wenn das Management-Netz die WAF einschliesst.
  Unbekannte Namen unter beiden Domains: 404.
- Startseiten und Portal sind statisch, ohne Skripte (Content-Security-Policy `default-src 'none'`).
- Mit Anmelde-Tor (`AUTOMATO_FRONT_TOR=an`) kommt ohne Anmeldung niemand weiter als bis zu Keycloak;
  Kunden ohne Realm sind gesperrt.
- Die Adresse der Oberfläche von Automato (`AUTOMATO_UI_HOST`, z. B. `automato.int.example.ch`)
  bleibt, wie sie ist.

## 1. Voraussetzungen

- Automato produktiv nach `docs/INSTALLATION-PROD.md`, mit Profil `proxy`
  (`COMPOSE_PROFILES` in `.env` enthält `proxy`, z. B. `core,collectors,proxy`; bis 1.5.x
  `AUTOMATO_COMPOSE_PROFILES`, umstellen mit `sudo deploy/aufbau.sh env ordnen`), Stand ≥ 1.2.0.
- NetBox-Host nach `docs/INSTALLATION-NETBOX-HOST.md`; dort `ALLOW`/`FRONT` = Adresse dieser VM.
- **DNS:** `*.mgmt.example.ch` → externe Adresse der WAF; intern `mgmt.int.example.ch` und
  `start.mgmt.int.example.ch` (oder `*.mgmt.int.example.ch`) → interne Adresse dieser VM.
- **WAF:** Backend = diese VM, Port 443, TLS neu aufbauen (Re-Encryption) mit dem Namen der Anfrage
  als SNI, `X-Forwarded-For` an. Sie prüft das Zertifikat dieser VM (2.).
- **Host-Firewall dieser VM** (`INSTALLATION-PROD.md` 4.3): in `DOCKER-USER` Port 443 zusätzlich
  von der Adresse der WAF; Firewall davor ebenso, ohne Source-NAT.

### Variante: FortiGate mit VIP statt WAF als Proxy (1.5.0)

Leitet die Firewall nur um (VIP/DNAT, auch mit WAF-Profil), kommt nicht die WAF an, sondern die
**Adresse des Besuchers** (`tcpdump -ni any 'tcp port 443 and (tcp[tcpflags] & tcp-syn != 0)'`
zeigt öffentliche Absender). Dann:

1. `.env`: `AUTOMATO_FRONT_KUNDEN_ALLOW="0.0.0.0/0 ::/0"` – wer hereinkommt, entscheidet die
   FortiGate; `AUTOMATO_FRONT_WAF` bleibt (niemandem sonst wird `X-Forwarded-For` geglaubt).
2. **`AUTOMATO_UI_ALLOW` setzen** (Management-Netz): sonst wäre die Oberfläche über ihre Adresse
   auch von aussen erreichbar (`front.sh pruefen` warnt). Interne Adressen prüft der Proxy weiter
   nach Absender.
3. `/etc/ufw/after.rules` im Block `DOCKER-USER` **vor** der `DROP`-Zeile (nur 443, 8443 bleibt
   eingeschränkt), danach `sudo ufw reload`:
   ```text
   -A DOCKER-USER -i ens192 -p tcp -m conntrack --ctorigdstport 443 --ctdir ORIGINAL -j RETURN
   ```
4. `sudo docker compose up -d` und `sudo deploy/front/front.sh erzeugen`. Einzelne Kunden nur intern
   bzw. nur aus bestimmten Netzen: Zugriff je Kunde (Abschnitt 5).

## 2. Zertifikate

In `deploy/proxy/pki/` (nicht im Repo), Fullchain zuerst mit dem eigenen Zertifikat:

| Datei | Für | Von |
|---|---|---|
| `mgmt.crt` / `mgmt.key` | `*.mgmt.example.ch` (Strecke WAF → diese VM) | eigene CA (die WAF prüft gegen sie) – oder das öffentliche Wildcard, dann liegt sein Schlüssel auch hier |
| `mgmt-int.crt` / `mgmt-int.key` | `mgmt.int.example.ch` und `*.mgmt.int.example.ch` | eigene CA |

Schlüssel auf der VM erzeugen, nur die Anforderung (CSR) geht an die CA:

```bash
cd /opt/automato/automato && P=deploy/proxy/pki && sudo install -d -m 750 $P
sudo openssl req -new -newkey rsa:3072 -nodes -keyout $P/mgmt-int.key -out /tmp/mgmt-int.csr \
  -subj "/CN=mgmt.int.example.ch" \
  -addext "subjectAltName=DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch"
sudo chmod 600 $P/mgmt-int.key
# CSR bei der CA einreichen (Windows-CA: certreq -submit -attrib "CertificateTemplate:WebServer" …),
# Zertifikat + Kette als Base64 zurück, dann:
{ cat ~/mgmt-int.cer; echo; cat ~/ca.crt; echo; } | sed -e 's/\r$//' -e '/^[[:space:]]*$/d' \
  | sudo tee $P/mgmt-int.crt >/dev/null && sudo chmod 640 $P/mgmt-int.crt
```

Für `mgmt.crt` dasselbe mit `/CN=*.mgmt.example.ch` und `subjectAltName=DNS:*.mgmt.example.ch`.

## 3. Einstellungen (`.env`)

```ini
AUTOMATO_FRONT_DOMAIN=mgmt.example.ch
AUTOMATO_FRONT_DOMAIN_INT=mgmt.int.example.ch
AUTOMATO_FRONT_WAF=10.0.40.1/32          # Adresse der WAF
AUTOMATO_FRONT_MGMT=10.0.10.0/24         # Management-Netz
AUTOMATO_FRONT_NETBOX=10.0.30.20         # NetBox-Host
# AUTOMATO_FRONT_NETBOX_CA=ca.crt        # nur wenn der NetBox-Host ein Zertifikat der eigenen CA hat
# AUTOMATO_FRONT_MAGISTER=https://…      # später: Benutzerverwaltung (-um)
# AUTOMATO_FRONT_KUNDEN_ALLOW=           # leer = WAF + Management-Netz
# Anmelde-Tor (1.4.0, Abschnitt 6)
AUTOMATO_FRONT_TOR=an
# AUTOMATO_FRONT_KEYCLOAK=https://login.example.ch   # leer = aus AUTOMATO_OIDC_ISSUER
# AUTOMATO_FRONT_KEYCLOAK_INTERN=        # leer = aus AUTOMATO_OIDC_JWKS_URL
# AUTOMATO_FRONT_KEYCLOAK_CA=ca.crt      # wenn Keycloak ein Zertifikat der eigenen CA hat
# AUTOMATO_FRONT_INTERN_ROLLEN=admin operator
```

## 4. Starten

```bash
cd /opt/automato/automato
sudo deploy/update.sh main               # hängt wieder an den Branch an (seit 1.6.0)
cat VERSION                              # ≥ 1.2.1
sudo deploy/front/front.sh erzeugen      # Portal, Einträge, Proxy neu laden („Proxy neu geladen“)
```

- `update.sh` startet den Proxy mit den neuen Ordnern, sofern `COMPOSE_PROFILES` das Profil
  `proxy` enthält; sonst `proxy` dort ergänzen und `sudo docker compose up -d`.
- Compose liest die Profile aus `COMPOSE_PROFILES` in `.env` – darin immer **beide** Profile
  `core` und `proxy`: der Proxy hängt an der Web-UI, nur mit `proxy` lehnt Compose das Projekt ab.
- Antwortet ein neuer Name mit `ERR_SSL_PROTOCOL_ERROR`, kennt der Proxy ihn noch nicht (kein
  Zertifikat für den Namen): `front.sh erzeugen` bzw. `sudo docker compose restart proxy`.

## 5. Kunden

**Seit 1.3.0 in der Oberfläche** (ADR 0050): Administration → **Kunden-Portal** – Kurzname, Name,
Werkzeuge, Kacheln und optional ein eigener Realm. Den Front-Proxy ändert ein Host-Agent auf der
VM; einmal einrichten:

```bash
cd /opt/automato/automato
sudo deploy/front/front.sh agent      # Ordner deploy/front/state (uid 1001) + systemd-Einheiten
systemctl status automato-front-sync.path   # aktiv (waiting)
```

Danach übernimmt der Agent jede Änderung aus der Oberfläche innert Sekunden (`front.sh sync`,
Protokoll: `journalctl -u automato-front-sync`); das Kunden-Portal zeigt, ob der Front-Proxy aktuell
ist bzw. was der Agent meldet. Kunden aus der Oberfläche ändert der Befehl nicht; von Hand angelegte
bleiben unberührt. Je Kunde zeigt „Einrichtung“ die Befehle für die NetBox-Instanz und den Realm.

**Von Hand** (wie bis 1.2.x, z. B. ohne Oberfläche):

```bash
sudo deploy/front/front.sh kunde kunde-a "Kunde A"            # Startseite + NetBox
sudo deploy/front/front.sh kunde kunde-a "Kunde A" --doc      # dazu Automato (-doc)
sudo deploy/front/front.sh link kunde-a "Handbuch" https://… "Beschreibung"   # weitere Kachel
sudo deploy/front/front.sh liste
sudo deploy/front/front.sh entfernen kunde-a --ja             # NetBox und Daten bleiben
```

- Derselbe Kurzname wie auf dem NetBox-Host (`netbox-host.sh neu kunde-a`).
- **`--doc`:** Anmeldung über Keycloak – im Client `automato-web` je Kunde die *Valid redirect
  URI* `https://kunde-a-doc.mgmt.example.ch/*` und den *Web origin* ergänzen (das Werkzeug zeigt
  beides an). Die Anmeldeseite (Keycloak) muss für die Benutzer erreichbar sein.
- **Eigener Realm je Kunde (1.3.0):** im Kunden-Portal den Realm eintragen – unter
  `https://kunde-a-doc.…` meldet sich der Kunde dann dort an (z. B. Azure/Entra oder LDAPS), mit den
  Rechten aus dem Portal (Mandant, Kunden, Rolle). Realm anlegen mit
  `deploy/keycloak/make_realm.py --realm kunde-a --ui-url https://kunde-a-doc.mgmt.example.ch` und
  in der Admin-Konsole importieren (`deploy/keycloak/PROD-INSTALL.md`, Abschnitt 13).
- **`--um`, `--cmdb`:** wirken erst mit `AUTOMATO_FRONT_MAGISTER` bzw. `AUTOMATO_FRONT_CMDB`.
- **Kunde von Hand → Portal:** wird im Kunden-Portal ein Kunde mit demselben Kurznamen gespeichert,
  gilt ab dann der Eintrag aus dem Portal (Werkzeuge, Realm, Zugriff und Kacheln – eigene Kacheln
  vorher dort eintragen). `front.sh liste` zeigt die Quelle (Portal bzw. Hand).

**Zugriff je Kunde (1.5.0):** im Kunden-Portal bzw. mit `--zugriff`:

| Zugriff | Wer erreicht die Adressen des Kunden | Von Hand |
|---|---|---|
| Überall (Vorgabe) | wie `AUTOMATO_FRONT_KUNDEN_ALLOW` (WAF bzw. Internet) | `--zugriff ueberall` |
| Nur intern | nur das Management-Netz (`AUTOMATO_FRONT_MGMT`), nie über die WAF | `--zugriff intern` |
| Nur aus diesen Netzen | nur die Liste (Adresse des Benutzers, über die WAF aus `X-Forwarded-For`) | `--zugriff "203.0.113.0/24 198.51.100.7"` |

Gilt für Startseite und alle Werkzeuge des Kunden – vor dem Anmelde-Tor und auch für die NetBox-API
mit Token. Allen anderen antworten die Adressen wie ein Name, den es nicht gibt (404 „Unbekannte
Adresse“), so verrät die Antwort nicht, dass es den Kunden gibt.

## 5a. Übrige Namen an die Plattform dahinter (1.10.0, ADR 0053)

Teilt sich der Front-Proxy die Domain mit einer anderen Plattform – z. B. die Kundenseiten von
Magister unter derselben Domain –, gehen deren Namen über den Front-Proxy: er bedient seine
Kunden selbst und gibt **jeden anderen Namen** der Domain weiter, statt „Unbekannte Adresse“.
Ein Wildcard `*.<domain>` im DNS auf diese VM (bzw. die WAF) genügt dann für alles.

```bash
sudo install -m 644 /pfad/zur/root-ca.pem deploy/proxy/pki/magister-root.pem   # CA des Ziels
setenv AUTOMATO_FRONT_UEBRIGE 10.0.30.10            # Ziel, optional :Port (Magister: 443)
setenv AUTOMATO_FRONT_UEBRIGE_CA magister-root.pem  # leer = öffentliches Zertifikat
sudo deploy/front/front.sh erzeugen && sudo deploy/front/front.sh pruefen
```

- Name und SNI bleiben, die Plattform erkennt ihre Kunden daran; ihr Zertifikat muss die Namen
  abdecken, und die CA dazu liegt in `deploy/proxy/pki`.
- Die Sperre nach Absender gilt wie für Kunden-Adressen (`AUTOMATO_FRONT_KUNDEN_ALLOW`).
  Kopfzeilen der Anmeldung (`X-Automato-*`, `X-Auth-Request-*`) werden entfernt; die Antwort
  behält die Kopfzeilen der Plattform.
- **Magister:** in `plattform.conf` `PLATTFORM_WAF=<Adresse dieser VM>` (dann `plattform-aufbau.sh
  up`): Magister glaubt `X-Forwarded-For` nur von hier und sieht so die Adresse der Benutzer.
- Das Zertifikat des Front-Proxys (`mgmt.crt`, `*.<domain>`) deckt die Namen ab. Eine Plattform,
  die ihre Zertifikate selbst bei Let's Encrypt holt, gehört nicht dahinter (TLS endet hier).
- `pruefen` meldet unter „Ziele“, ob das Ziel antwortet und sein Zertifikat vertraut ist, und
  unter „Adressen“, wohin der Wildcard zeigt.

## 6. Anmelde-Tor (1.4.0, ADR 0051)

Vor jeder Kunden-Adresse (Startseite, `-nb`, `-doc`, `-um`, `-cmdb`) und vor dem Portal fragt der
Proxy einen **oauth2-proxy** (je Realm ein Container, Projekt `automato-tor`, Netz
`automato-front-tor`). Wer nicht angemeldet ist, geht zu Keycloak im Realm des Kunden und danach
zurück auf die ursprüngliche Adresse. Die Werkzeuge melden sich über die Keycloak-Sitzung ohne
zweite Maske an: Automato selbst, NetBox über den Benutzer vom Tor
(`docs/INSTALLATION-NETBOX-HOST.md`, `sso`), Magister wahlweise über Keycloak oder die Kopfzeilen.

1. **Client je Realm** `automato-front` in Keycloak (`deploy/keycloak/PROD-INSTALL.md` 13c);
   `sudo deploy/front/front.sh tor` zeigt je Realm die *Valid redirect URIs*.
2. **Client-Secret** je Realm auf dieser VM hinterlegen (Eingabe unsichtbar, Datei nur root in
   `deploy/front/tor/`):
   ```bash
   sudo deploy/front/front.sh tor vb        # Realm von Automato bzw. eines Kunden
   ```
3. `AUTOMATO_FRONT_TOR=an` in `.env`, dann `sudo deploy/front/front.sh erzeugen` (startet die
   Container des Tors, lädt den Proxy neu). Bei Kunden aus der Oberfläche übernimmt das der Agent.

- **Realm nötig:** Kunden ohne Realm bzw. mit Realm ohne Client-Secret sind gesperrt (403);
  notfalls ein Realm mit lokalen Benutzern. Von Hand: `front.sh kunde <kunde> "<Name>" --realm <r>`.
- **Portal** `start.<intern>` nur für die Rollen `AUTOMATO_FRONT_INTERN_ROLLEN` (Vorgabe `admin
  operator`) im Realm von Automato (aus `AUTOMATO_OIDC_ISSUER`).
- **Ohne Tor erreichbar:** die NetBox-API mit Token (`Authorization: Token …`, NetBox prüft selbst –
  NetBox-Sync von Automato) und die API von Automato unter `-doc` (prüft ihr Token selbst).
- **Benutzer für die Werkzeuge:** `X-Automato-User` (`<benutzer>@<realm>`), `X-Automato-Realm`,
  `X-Automato-Groups` (`automato-lesen`, Realm-Rollen als `role:<rolle>`); nur das Tor setzt sie,
  vom Absender werden sie immer entfernt. Magister vertraut ihnen nur von dieser VM.
- **Abmelden:** „Abmelden“ auf der Startseite bzw. in NetBox beendet auch die Keycloak-Sitzung.
- **Sitzung:** 12 h, alle 5 min bei Keycloak erneuert.
- **Keycloak erreichbar:** die Anmeldeseite für alle Benutzer (Kunden über die WAF); dieser Proxy
  braucht Keycloak für Token und Schlüssel (`AUTOMATO_FRONT_KEYCLOAK_INTERN`).

## 7. Prüfen

```bash
sudo deploy/front/front.sh pruefen
```

Einstellungen (seit 1.5.0: Warnung, wenn Kunden-Adressen aus jedem Netz erreichbar sind und
`AUTOMATO_UI_ALLOW` leer ist), Host-Agent (läuft, Ordner gehört dem Orchestrator, von Hand angelegte
Kunden), beide Zertifikate (passt der Schlüssel, deckt es die Namen ab, 30 Tage vor Ablauf eine
Warnung), Proxy, Anmelde-Tor (Client-Secrets, Container, gesperrte Kunden, Keycloak vom Proxy aus
erreichbar), je Kunde Startseite (mit Tor: Weiterleitung zur Anmeldung; bei Zugriff „intern“ bzw.
„Netze“ 404 für diese VM), Zugriff, Weg zur NetBox (von dieser VM aus) und ob Orchestrator und
zentraler Worker die NetBox-Adresse gleich auflösen. Im Browser aus dem
Management-Netz: `https://start.mgmt.int.example.ch` (Portal), über die WAF
`https://kunde-a.mgmt.example.ch` und `https://kunde-a-nb.mgmt.example.ch`.

**Adressen und Ziele (1.9.2):** `pruefen` zeigt jede Adresse, die der Proxy bedient (Oberfläche,
Portal, je Kunde Startseite und Werkzeuge) – ob sie im erzeugten Proxy steht, ob das Zertifikat sie
abdeckt und wohin sie im DNS dieser VM zeigt (diese VM; mit WAF die WAF bzw. VIP). Dazu der
Wildcard: zeigt `*.<domain>` auf diese VM, landet **jeder** andere Name der Domain hier und bekommt
„Unbekannte Adresse“ – teilt sich der Front-Proxy die Domain mit einer anderen Plattform (z. B.
Dev: Kundenseiten von Magister unter derselben Domain), bleibt der Wildcard dort und jede Adresse
des Front-Proxys bekommt einen eigenen Eintrag. Unter „Ziele“ fragt der Proxy-Container Automato
(`web-ui`), Magister (`AUTOMATO_FRONT_MAGISTER`) und die CMDB ab. Nur die Adressen, schnell:

```bash
sudo deploy/front/front.sh adressen
```

## 8. Automato an die NetBox eines Kunden

1. `netbox-host.sh token kunde-a` auf dem NetBox-Host (Lese- und Schreib-Token werden nur einmal
   angezeigt – direkt eintragen bzw. in den Tresor).
2. In Automato beim Mandanten des Kunden → Quellsysteme → NetBox mit
   `https://kunde-a-nb.mgmt.example.ch` und den beiden Token.
3. **Zertifikat der internen CA** (1.10.0): stammt das Zertifikat des Front-Proxys bzw. der WAF von
   der eigenen CA, deren Stammzertifikat nach `deploy/trust/` (Orchestrator und zentraler Worker
   vertrauen ihm dann zusätzlich, `deploy/trust/README.md`):
   ```bash
   sudo install -D -m 644 /root/ca.crt deploy/trust/interne-ca.crt
   sudo docker compose up -d --force-recreate orchestrator collector
   ```
4. Prüfen: `sudo deploy/front/front.sh pruefen` geht je Kunde den Weg des NetBox-Syncs aus dem
   Orchestrator bis zur NetBox („Automato erreicht seine NetBox über den Front-Proxy“).

**Der Weg:** über die WAF (die VM muss die externe Adresse erreichen; die WAF darf die API der
NetBox – JSON, `PATCH`/`POST` beim NetBox-Sync – nicht blockieren) bzw. ohne WAF direkt über den
Front-Proxy auf dieser VM. Im zweiten Fall kommen Orchestrator und Worker mit einer Adresse ihrer
Docker-Netze an; seit 1.10.0 (ADR 0053) lässt der Front-Proxy diese Netze für die **API mit Token**
an die NetBox durch, vor der Sperre nach Absender und dem Zugriff je Kunde und ohne Kopfzeilen der
Anmeldung. Die Netze ermittelt `front.sh erzeugen` aus den laufenden Containern – nach einem
`docker compose down` (neue Netze) `front.sh erzeugen` erneut.

**Erreicht diese VM die Adresse gar nicht** (z. B. FortiGate ohne Hairpin; `pruefen` meldet „erreicht
… nicht“): den Namen in `docker-compose.override.yml` direkt auf den NetBox-Host zeigen – **für
`orchestrator` und `collector`** (Prüfen und NetBox-Sync laufen im Orchestrator, Einlesen und
Abgleich im zentralen Worker), dann mit dem Port des NetBox-Hosts in der Adresse des Quellsystems:

```yaml
services:
  orchestrator:
    extra_hosts:
      - "kunde-a-nb.mgmt.example.ch:10.0.30.20"
  collector:
    extra_hosts:
      - "kunde-a-nb.mgmt.example.ch:10.0.30.20"
```

Danach `sudo docker compose up -d`. Steht `COMPOSE_PROFILES` in `.env`, kein `--profile` auf der
Kommandozeile angeben – es ersetzt die Profile aus `.env` (dann fehlt z. B. `redis`). Prüfen:
`sudo docker compose exec collector getent hosts kunde-a-nb.mgmt.example.ch`; `front.sh pruefen`
warnt, wenn Orchestrator und Worker verschieden auflösen.

## 9. Betrieb

- **Collector-Agenten über die WAF bzw. FortiGate** (Port des Agent-Gateways, Vorgabe 8443,
  `AUTOMATO_AGENT_GATEWAY_PORT`): nur als reine TCP-Durchleitung, ohne TLS aufzubrechen – das
  Agent-Gateway prüft das Client-Zertifikat des Agenten selbst. In `DOCKER-USER` muss dieser Port
  vom Absender erlaubt sein (WAF bzw. bei einer VIP die Netze der Agenten). Die Agenten zeigen
  auf `https://<AUTOMATO_AGENT_GATEWAY_HOST>:<Port>`, nie auf eine Kunden-Adresse und nie auf 8000.
- `front.sh pruefen` und `aufbau.sh pruefen` warnen, wenn `DOCKER-USER` keine Sperre enthält
  (veröffentlichte Ports sonst aus jedem Netz erreichbar).
- Nach einem Update bleiben Kunden und erzeugte Dateien erhalten (`deploy/front/kunden/`,
  `deploy/front/generated/`, nicht im Repo); `front.sh erzeugen` schreibt alles neu.
- **Sicherung:** `deploy/front/kunden/` und `deploy/front/tor/` (Client-Secrets) zusammen mit
  `.env` und `deploy/proxy/pki` sichern (wie die Konfiguration in `INSTALLATION-PROD.md` 7).
- **Update auf 1.4.0:** `update.sh` startet den Proxy im neuen Netz `automato-front-tor`; danach
  `sudo deploy/front/front.sh erzeugen`. Ohne `AUTOMATO_FRONT_TOR=an` bleibt alles wie in 1.3.

## 10. Inventar fürs Cockpit (1.10.0, ADR 0053)

Das Cockpit (vitabrevis-cockpit, ADR 0004 dort) holt von hier, was diese Installation betreibt –
VM, Front-Proxy mit Portal, Keycloak-Realm und je Kunde die NetBox – und führt daraus sein
Inventar nach. Grundlage ist `deploy/front/state/topologie.json`, die `front.sh erzeugen` (auch
über den Host-Agenten) schreibt.

```bash
sudo deploy/aufbau.sh cockpit-token     # neues Token in .env, Orchestrator neu, einmal angezeigt
```

Im Cockpit unter Inventar → Automato-Instanz → „Token hinterlegen“ eintragen (nur dort, nie in
Chat, Ticket oder Datei). Ein erneutes `cockpit-token` macht das alte ungültig.

- `GET /api/inventory` antwortet nur mit diesem Token (Bearer), nie mit einer Anmeldung von
  Personen; ohne eingerichtetes Token `404`. Inhalt: nur Namen und Adressen.
- Das Cockpit erreicht die Oberfläche: seine Adresse steht in `AUTOMATO_UI_ALLOW`, und es vertraut
  dem Zertifikat (im Cockpit `deploy/certs/inventory-ca.pem`, wenn es eine interne CA ist).

