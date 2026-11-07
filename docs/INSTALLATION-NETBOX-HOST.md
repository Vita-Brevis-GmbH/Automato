# NetBox-Host: eine NetBox je Kunde (Produktion)

Seit 0.121.0 (ADR 0044). Eine eigene VM trägt die NetBox aller Kunden – je Kunde ein eigener
Stack (netbox-docker mit eigener Datenbank, eigenem Redis, eigenen Secrets und eigener
Sicherung) hinter **einem** Proxy (Caddy, Port 443) mit Wildcard-Zertifikat. Dazu bekommt jeder
Kunde eine Startseite mit Kacheln zu seinen Werkzeugen. Alles erledigt das Werkzeug
`deploy/netbox-host/netbox-host.sh`.

| Adresse | Inhalt |
|---|---|
| `<kunde>.mgmt.example.ch` | Startseite des Kunden (NetBox, Dokumentation, weitere Werkzeuge) |
| `<kunde>-netbox.mgmt.example.ch` | NetBox des Kunden |
| `start.mgmt.example.ch` | interne Übersicht aller Kunden |

Kurzname des Kunden: Kleinbuchstaben, Ziffern und Bindestrich (z. B. `kunde-a`) – er steht in
den Adressen und bleibt fest.

## 1. VM

- Grösse für rund 10 Kunden: 4 vCPU, 16–24 GB RAM, 200 GB Disk; je weiterer Kunde etwa 1,5 GB RAM
  und 5–10 GB Disk.
- Aufsetzen wie die Automato-VM nach `docs/INSTALLATION-PROD.md` **Schritte 1–5** (verschlüsselte
  Disk, Härtung, Konten und SSH, Host-Firewall, Docker mit `"userland-proxy": false`). Die NetBox
  liegt in Docker-Volumes – `/var/lib/docker` auf dem eigenen Volume.
- Host-Firewall: nur SSH aus dem Admin-Netz; Port 443 in `DOCKER-USER` (4.3) aus den Netzen der
  Benutzer **und der Automato-VM** (NetBox-Sync, Lesen). Auf der Firewall davor dieselben
  Freigaben, ohne NAT.

## 2. DNS und Zertifikat

- **Ein** DNS-Eintrag `*.mgmt.example.ch` → IP dieser VM. Eigene Einträge für andere VMs
  (z. B. `magister.mgmt…`) gehen dem Wildcard vor.
- Wildcard-Zertifikat für `*.mgmt.example.ch` (Fullchain + Schlüssel). Öffnen Kunden die Seiten
  von eigenen Rechnern, ein öffentlich gültiges Zertifikat (z. B. Let's Encrypt mit DNS-01) – sonst
  müssten sie der eigenen Root vertrauen.

## 3. Einrichten

```bash
sudo apt-get -y install git age openssl curl
sudo install -d -m 750 /opt/netbox-host
sudo git clone <repo-url> /opt/netbox-host/automato      # Lesezugang (Deploy-Key nur lesen)
cd /opt/netbox-host/automato && sudo git checkout <tag>
sudo deploy/netbox-host/netbox-host.sh init               # legt /opt/netbox-host/host.conf an
sudo nano /opt/netbox-host/host.conf                      # DOMAIN, ALLOW, AUTOMATO_URL
sudo install -m 640 /home/<admin>/wildcard.crt /opt/netbox-host/proxy/pki/wildcard.crt
sudo install -m 640 /home/<admin>/wildcard.key /opt/netbox-host/proxy/pki/wildcard.key
sudo deploy/netbox-host/netbox-host.sh init               # Proxy startet, Sicherung täglich (cron)
sudo deploy/netbox-host/netbox-host.sh pruefen
```

`init` legt den age-Schlüssel für die Sicherung an (`/root/netbox-host-backup.agekey`) – sofort in
den Tresor kopieren, ohne ihn ist keine Sicherung lesbar. In `host.conf`:

- `ALLOW`: Netze, die Startseiten und NetBox öffnen dürfen (Admin-Netz, Netze bzw. VPN der Kunden,
  die Automato-VM); der Proxy antwortet allen anderen mit 403.
- `AUTOMATO_URL`: Kachel „Dokumentation“ auf jeder neuen Startseite.
- `NETBOX_DOCKER_TAG` / `NETBOX_VERSION`: Version aller Instanzen (Vorgabe NetBox 4.6, wie
  `source-of-truth/netbox/setup.sh`).

## 4. Kunde anlegen

```bash
cd /opt/netbox-host/automato
sudo deploy/netbox-host/netbox-host.sh neu kunde-a "Kunde A"
```

- holt netbox-docker in `/opt/netbox-host/kunden/kunde-a/netbox`, erzeugt alle Secrets
  (`.env`, nur root – die Vorgabe-Passwörter von netbox-docker werden nie benutzt), hängt die
  NetBox als `nb-kunde-a` an das Netz des Proxys (kein eigener Port nach aussen), startet sie
  und legt den Benutzer `admin` an. Das Passwort steht **einmal** in der Ausgabe – in den Tresor,
  beim ersten Login ändern.
- schreibt den Proxy-Eintrag und die Startseite `https://kunde-a.mgmt.example.ch`.

**Anbinden an Automato:**

```bash
sudo deploy/netbox-host/netbox-host.sh token kunde-a
```

legt `automato-collector` (nur lesen) und `automato-sync` (schreibt nur die Objekttypen des
Syncs, ADR 0010) an und zeigt Adresse und Tokens. In Automato beim Mandanten des Kunden →
Quellsysteme → + Quellsystem „NetBox“ eintragen. Erneut aufrufen ersetzt die Tokens. Die
Automato-VM muss der CA des Zertifikats vertrauen (`docs/INSTALLATION-PROD.md` 6.3) und in
`ALLOW` stehen.

**Weitere Kacheln** (z. B. ein weiteres Werkzeug des Kunden):

```bash
sudo deploy/netbox-host/netbox-host.sh link kunde-a "Lizenzen und Geräte" https://kunde-a-lizenzen.mgmt.example.ch "Verträge, Laufzeiten"
```

Die Kacheln stehen in `/opt/netbox-host/kunden/<kunde>/links.tsv` (Titel, URL, Beschreibung,
mit Tabulator getrennt); `startseiten` erzeugt alle Seiten neu.

## 5. Betrieb

| Befehl | Wirkung |
|---|---|
| `liste` | Kunden mit Zustand, Version und Adresse |
| `pruefen` | Proxy, Zertifikat (Warnung 30 Tage vor Ablauf), je Kunde NetBox, Weg über den Proxy, Sicherung jünger als 26 h |
| `sichern <kunde\|alle>` | Datenbank (`pg_dump`) und Medien, verschlüsselt mit age, nach `BACKUP_DIR/<kunde>/`; älter als `BACKUP_KEEP_DAYS` wird gelöscht. Täglich um 02:17 per cron für alle |
| `wiederherstellen <kunde> <datei.sql.gz.age>` | NetBox stoppen, Datenbank (und Medien derselben Sicherung) einspielen, starten – einmal pro Jahr testen |
| `update <kunde\|alle>` | erst sichern, dann netbox-docker und NetBox in der Version aus `host.conf` holen, starten, prüfen. Neue Version zuerst bei einem Kunden |
| `loeschen <kunde> --ja` | letzte Sicherung, dann Container, Daten, Proxy-Eintrag und Startseite entfernen; die Sicherungen bleiben |

Alle Befehle mit `sudo deploy/netbox-host/netbox-host.sh …` im Ordner `/opt/netbox-host/automato`.
Das Werkzeug selbst aktualisieren: `sudo git pull` bzw. neuen Tag auschecken.

**Anmeldung:** vorerst lokale NetBox-Benutzer je Instanz (Anmeldung immer nötig). Anmeldung über
Keycloak mit einem Realm je Mandant folgt in Version 2 (ADR 0044).

## 6. Grenzen

- Die Startseiten sind statisch (nur Links); dynamisch je Benutzer erst mit dem Cockpit (V2).
- Ein Update ändert das Schema der Datenbank – zurück geht es nur über `wiederherstellen` mit der
  Sicherung, die `update` vorher anlegt.
- `token` legt klassische Tokens an (NetBox 4.6); für spätere Versionen die Tokens in NetBox
  erzeugen und in Automato eintragen.
