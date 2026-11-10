# Front-Proxy auf der Automato-VM (Startseiten, Portal, Weiterleitung je Werkzeug)

Seit 1.2.0 (ADR 0044 Nachtrag). Der Proxy von Automato (Profil `proxy`, Caddy) ist zugleich der
Eingang für die Kunden: Er liefert die Startseite je Kunde und das interne Portal aus und leitet
je Werkzeug weiter. Das Werkzeug `deploy/front/front.sh` erzeugt dafür die Einträge und Seiten.

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
- Die Adresse der Oberfläche von Automato (`AUTOMATO_UI_HOST`, z. B. `automato.int.example.ch`)
  bleibt, wie sie ist.

## 1. Voraussetzungen

- Automato produktiv nach `docs/INSTALLATION-PROD.md`, mit Profil `proxy`
  (`AUTOMATO_COMPOSE_PROFILES` in `.env` enthält `--profile proxy`), Stand ≥ 1.2.0.
- NetBox-Host nach `docs/INSTALLATION-NETBOX-HOST.md`; dort `ALLOW`/`FRONT` = Adresse dieser VM.
- **DNS:** `*.mgmt.example.ch` → externe Adresse der WAF; intern `mgmt.int.example.ch` und
  `start.mgmt.int.example.ch` (oder `*.mgmt.int.example.ch`) → interne Adresse dieser VM.
- **WAF:** Backend = diese VM, Port 443, TLS neu aufbauen (Re-Encryption) mit dem Namen der Anfrage
  als SNI, `X-Forwarded-For` an. Sie prüft das Zertifikat dieser VM (2.).
- **Host-Firewall dieser VM** (`INSTALLATION-PROD.md` 4.3): in `DOCKER-USER` Port 443 zusätzlich
  von der Adresse der WAF; Firewall davor ebenso, ohne Source-NAT.

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
```

## 4. Starten

```bash
cd /opt/automato/automato
sudo deploy/update.sh                                   # bzw. bei laufendem Stand:
sudo docker compose --profile core --profile proxy up -d proxy   # Proxy mit den neuen Ordnern
sudo deploy/front/front.sh erzeugen                     # Portal, Einträge, Proxy neu laden
```

## 5. Kunden

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
  beides an). Die Anmeldeseite (Keycloak) muss für die Benutzer erreichbar sein; für Kunden von
  aussen erst mit Version 2 (Realm je Mandant).
- **`--um`, `--cmdb`:** wirken erst mit `AUTOMATO_FRONT_MAGISTER` bzw. `AUTOMATO_FRONT_CMDB`.

## 6. Prüfen

```bash
sudo deploy/front/front.sh pruefen
```

Einstellungen, beide Zertifikate (passt der Schlüssel, deckt es die Namen ab, 30 Tage vor Ablauf
eine Warnung), Proxy, je Kunde Startseite und Weg zur NetBox (von dieser VM aus). Im Browser aus dem
Management-Netz: `https://start.mgmt.int.example.ch` (Portal), über die WAF
`https://kunde-a.mgmt.example.ch` und `https://kunde-a-nb.mgmt.example.ch`.

## 7. Automato an die NetBox eines Kunden

`netbox-host.sh token kunde-a` auf dem NetBox-Host, dann in Automato beim Mandanten des Kunden →
Quellsysteme → NetBox mit `https://kunde-a-nb.mgmt.example.ch`. Der Weg geht über die WAF (diese
VM muss die externe Adresse erreichen); die WAF darf die API der NetBox (JSON, `PATCH`/`POST` beim
NetBox-Sync) nicht blockieren.

## 8. Betrieb

- Nach einem Update bleiben Kunden und erzeugte Dateien erhalten (`deploy/front/kunden/`,
  `deploy/front/generated/`, nicht im Repo); `front.sh erzeugen` schreibt alles neu.
- **Sicherung:** `deploy/front/kunden/` zusammen mit `.env` und `deploy/proxy/pki` sichern (wie die
  Konfiguration in `INSTALLATION-PROD.md` 7).
