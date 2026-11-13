# NetBox-Host: eine NetBox je Kunde (Produktion)

Seit 0.121.0 (ADR 0044), seit 1.1.0 als **Backend hinter dem Front-Proxy** (ADR 0044 Nachtrag).
Eine eigene VM trägt die NetBox aller Kunden – je Kunde ein eigener Stack (netbox-docker mit
eigener Datenbank, eigenem Redis, eigenen Secrets und eigener Sicherung) hinter einem Proxy
(Caddy, Port 443). Alles erledigt das Werkzeug `deploy/netbox-host/netbox-host.sh`.

```
Kunden ──▶ WAF ──▶ Front-Proxy (Automato-VM) ──┬─▶ <kunde>.…        Startseite
                                               ├─▶ <kunde>-doc.…    Automato
                                               ├─▶ <kunde>-um.…     Magister (Benutzerverwaltung)
                                               └─▶ <kunde>-nb.…  ──▶ NetBox-Host (diese VM)
Management-Netz ──▶ Front-Proxy: start.mgmt.int.… (Portal), mgmt.int.… (Verwaltung)
Management-Netz ──▶ NetBox-Host: SSH 22, HTTPS 443 (Test, Betrieb)
```

| Adresse | Inhalt |
|---|---|
| `<kunde>-nb.mgmt.example.ch` | NetBox des Kunden (diese VM) |
| `<kunde>.mgmt.example.ch`, `<kunde>-doc`, `<kunde>-um`, `<kunde>-cmdb` | Front-Proxy (Automato-VM) |
| `start.mgmt.int.example.ch`, `mgmt.int.example.ch` | Front-Proxy, nur Management-Netz |

**Kurzname** des Kunden: Kleinbuchstaben, Ziffern und Bindestrich (z. B. `kunde-a`), nicht auf
`-nb`, `-doc`, `-um` oder `-cmdb` endend – er steht in allen Adressen und bleibt fest.

## 1. VM aufsetzen und härten

- Grösse für rund 10 Kunden: 4 vCPU, 16–24 GB RAM, 200 GB Disk; je weiterer Kunde etwa 1,5 GB RAM
  und 5–10 GB Disk.
- Wie die Automato-VM nach `docs/INSTALLATION-PROD.md` **Schritte 1–5** (vCenter, Ubuntu mit LUKS,
  `/var/lib/docker` auf eigenem Volume, Entsperren, Härtung, SSH, Kernel, Audit, Docker mit
  `daemon.json`: `"no-new-privileges": true` gilt für alle NetBox-Container, `"userland-proxy":
  false` braucht der Proxy für `ALLOW`). Abweichungen:
  - **4.3 Host-Firewall:** in `DOCKER-USER` nur Port **443**, und nur von der **Automato-VM**
    (Front-Proxy) und aus dem **Management-Netz** – Kunden kommen nie direkt. Kein 8443.
  - **4.5 Audit:** statt der Pfade unter `/opt/automato`:
    `-w /opt/netbox-host/host.conf -p rwa -k nbh-conf`,
    `-w /opt/netbox-host/proxy/pki -p rwa -k nbh-pki`,
    `-w /opt/netbox-host/kunden -p wa -k nbh-kunden`,
    `-w /root/netbox-host-backup.agekey -p rwa -k nbh-backupkey` (die übrigen Zeilen gleich).
- **Ausgehend** braucht die VM: GitHub (netbox-docker, Repo), Docker Hub (Images),
  Paketquellen, DNS, NTP.

### 1a. Eigene Daten-Disk (empfohlen)

Die Daten der NetBox-Instanzen (Docker: Datenbanken, Medien, Images) liegen auf einer **zweiten
virtuellen Disk**, die Sicherungen auf der System-Disk – fällt eine Disk aus, bleibt die andere.
Nach der Installation von Ubuntu (Schritt 2) und **vor Docker** (Schritt 5); ersetzt dort
„Volumes für Daten anlegen“. Richtwerte: System-Disk 80 GB (Root 30 GB, Sicherung 40 GB),
Daten-Disk 200 GB.

1. **vCenter:** Neues Gerät → Festplatte, 200 GB, Thin, Modus *Abhängig* (in Snapshots und
   VM-Sicherung enthalten), gleiche Speicherrichtlinie wie die System-Disk.
2. **Verschlüsseln** (LUKS2; entsperrt sich mit einer Schlüsseldatei auf der verschlüsselten
   System-Disk, sobald diese offen ist – die Disk allein ist unlesbar):

   ```bash
   lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS      # neue Disk ohne Partition, z. B. sdb
   DISK=/dev/sdb                                    # anpassen und prüfen!
   sudo wipefs $DISK                                # keine Ausgabe = leer
   sudo parted -s $DISK mklabel gpt mkpart daten 1MiB 100% && sudo partprobe $DISK
   sudo cryptsetup luksFormat --type luks2 ${DISK}1   # Passphrase = Notfall (Tresor)
   sudo install -d -m 700 /etc/cryptsetup-keys.d
   sudo dd if=/dev/urandom of=/etc/cryptsetup-keys.d/daten.key bs=512 count=1 status=none
   sudo chmod 400 /etc/cryptsetup-keys.d/daten.key
   sudo cryptsetup luksAddKey ${DISK}1 /etc/cryptsetup-keys.d/daten.key
   echo "daten UUID=$(sudo blkid -s UUID -o value ${DISK}1) /etc/cryptsetup-keys.d/daten.key luks,nofail" | sudo tee -a /etc/crypttab
   sudo cryptsetup open --key-file /etc/cryptsetup-keys.d/daten.key ${DISK}1 daten
   ```

3. **Volumes** (LVM, Platz zum Vergrössern bleibt frei):

   ```bash
   sudo pvcreate /dev/mapper/daten && sudo vgcreate daten-vg /dev/mapper/daten
   sudo lvcreate -L 150G -n docker daten-vg        # Container, Volumes (Datenbanken, Medien)
   sudo lvcreate -L 30G -n containerd daten-vg     # Images (containerd)
   sudo mkfs.ext4 -L docker /dev/daten-vg/docker
   sudo mkfs.ext4 -L containerd /dev/daten-vg/containerd
   sudo mkdir -p /var/lib/docker /var/lib/containerd
   echo '/dev/daten-vg/docker /var/lib/docker ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
   echo '/dev/daten-vg/containerd /var/lib/containerd ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
   for s in docker containerd; do        # Docker startet nur mit eingehängter Daten-Disk
     sudo mkdir -p /etc/systemd/system/$s.service.d
     printf '[Unit]\nRequiresMountsFor=/var/lib/docker /var/lib/containerd\n' \
       | sudo tee /etc/systemd/system/$s.service.d/10-daten-disk.conf >/dev/null
   done
   sudo systemctl daemon-reload && sudo mount -a && df -h /var/lib/docker /var/lib/containerd
   ```

4. **Sicherungen auf der System-Disk:**

   ```bash
   sudo vgs ubuntu-vg                               # VFree = freier Platz
   sudo lvcreate -L 40G -n backup ubuntu-vg && sudo mkfs.ext4 -L backup /dev/ubuntu-vg/backup
   sudo mkdir -p /var/backups/netbox-host
   echo '/dev/ubuntu-vg/backup /var/backups/netbox-host ext4 defaults,nodev,nosuid,noexec 0 2' | sudo tee -a /etc/fstab
   sudo mount -a
   ```

5. **LUKS-Header sichern** (`luksHeaderBackup` wie in `INSTALLATION-PROD.md` Schritt 2, Datei in
   den Tresor), Audit-Regel `-w /etc/cryptsetup-keys.d -p rwa -k luks-key`, dann **Neustart-Test**:
   `lsblk -f` zeigt `daten` → `daten--vg-docker`/`-containerd`, `findmnt /var/lib/docker
   /var/lib/containerd /var/backups/netbox-host` alle eingehängt.

**Vergrössern:** im vCenter die Disk vergrössern, dann
`echo 1 | sudo tee /sys/class/block/sdb/device/rescan`, `sudo growpart /dev/sdb 1`,
`sudo cryptsetup resize --key-file /etc/cryptsetup-keys.d/daten.key daten`,
`sudo pvresize /dev/mapper/daten`, `sudo lvextend -r -L +50G daten-vg/docker`.

## 2. Zertifikat

Ein Zertifikat für `*.mgmt.example.ch` (Fullchain + Schlüssel, PEM) von der **eigenen CA**: Die
Benutzer sehen nur den Front-Proxy (öffentliches Wildcard-Zertifikat), der Front-Proxy prüft
diese VM gegen die eigene CA. So liegt der Schlüssel des öffentlichen Zertifikats nur auf dem
Front-Proxy. (Das öffentliche Wildcard-Zertifikat ginge auch – dann liegt sein Schlüssel auf zwei
VMs.)

## 3. Werkzeug holen und einrichten

Lesezugang zum Repo mit einem **Deploy-Key nur lesen**:

```bash
sudo apt-get -y install git age openssl curl
sudo ssh-keygen -t ed25519 -N '' -C 'netbox-host (nur lesen)' -f /root/.ssh/automato_deploy
sudo cat /root/.ssh/automato_deploy.pub     # GitHub: Repo → Settings → Deploy keys → Add (ohne Schreibrecht)
sudo install -d -m 750 /opt/netbox-host
sudo GIT_SSH_COMMAND='ssh -i /root/.ssh/automato_deploy -o IdentitiesOnly=yes' \
  git clone <repo-ssh-url> /opt/netbox-host/automato
cd /opt/netbox-host/automato
sudo git config core.sshCommand 'ssh -i /root/.ssh/automato_deploy -o IdentitiesOnly=yes'
sudo git checkout main                      # freigegebener Stand (VERSION)
sudo deploy/netbox-host/netbox-host.sh init  # legt /opt/netbox-host/host.conf an
sudo nano /opt/netbox-host/host.conf         # DOMAIN, ALLOW, FRONT
```

In `host.conf`:

- `DOMAIN`: z. B. `mgmt.example.ch` – die NetBox eines Kunden heisst `<kunde>-nb.<DOMAIN>`.
- `ALLOW`: Adresse der Automato-VM (Front-Proxy) und das Management-Netz; alle anderen bekommen 403.
- `FRONT`: Adresse der Automato-VM – nur ihrer Angabe `X-Forwarded-For` wird geglaubt (NetBox
  sieht die Adresse des Benutzers).
- `NETBOX_DOCKER_TAG` / `NETBOX_VERSION`: Version aller Instanzen (Vorgabe NetBox 4.6).
- `PORT` (1.9.1): Port des Proxys auf dieser VM, Vorgabe 443. Nur wenn hier schon ein anderer
  Dienst Port 443 belegt (in Dev z. B. die Magister-VM), einen freien Port wie `8444` setzen. Dann:
  - auf dem Front-Proxy `AUTOMATO_FRONT_NETBOX=<ip dieser VM>:8444`;
  - in der Firewall dieser VM (`DOCKER-USER`) den Port für die Automato-VM und das
    Management-Netz öffnen;
  - direkt aus dem Management-Netz heisst die NetBox `https://<kunde>-nb.<DOMAIN>:8444`.

  `ALLOW` und `FRONT` bleiben wie oben (Adresse der Automato-VM). Nach einer Änderung `init`.

Zertifikat ablegen und den Proxy starten:

```bash
sudo install -m 640 ~/nbhost.crt /opt/netbox-host/proxy/pki/wildcard.crt
sudo install -m 600 ~/nbhost.key /opt/netbox-host/proxy/pki/wildcard.key && shred -u ~/nbhost.key
sudo deploy/netbox-host/netbox-host.sh init   # Proxy startet (gehärtet), Sicherung täglich 02:17
sudo cat /root/netbox-host-backup.agekey       # sofort in den Tresor – ohne ihn ist keine Sicherung lesbar
sudo deploy/netbox-host/netbox-host.sh pruefen
```

Der Proxy läuft ohne Linux-Capabilities ausser Port 443, mit Dateisystem nur lesend und
`no-new-privileges` (wie die Dienste von Automato seit 1.0.0).

## 4. Kunde anlegen

```bash
cd /opt/netbox-host/automato
sudo deploy/netbox-host/netbox-host.sh neu kunde-a "Kunde A"
```

- holt netbox-docker in `/opt/netbox-host/kunden/kunde-a/netbox`, erzeugt alle Secrets
  (`.env`, nur root – die Vorgabe-Passwörter von netbox-docker werden nie benutzt), hängt die
  NetBox als `nb-kunde-a` an das Netz des Proxys (kein eigener Port nach aussen), startet sie
  (beim ersten Mal einige Minuten) und legt den Benutzer `admin` an. Das Passwort steht **einmal**
  in der Ausgabe – in den Tresor, beim ersten Login ändern; für jede Person ein eigenes Konto.
- schreibt den Proxy-Eintrag `https://kunde-a-nb.mgmt.example.ch`. Startseite und Weiterleitung
  für Kunden kommen vom Front-Proxy.

**Test aus dem Management-Netz**, solange der Front-Proxy noch fehlt: auf dem eigenen PC einen
Eintrag in der hosts-Datei `<IP dieser VM> kunde-a-nb.mgmt.example.ch`, dann die Adresse im
Browser öffnen (Zertifikat der eigenen CA).

### 4a. Agent: Kunden aus Automato (seit 1.11.0)

Statt `neu` von Hand legt der Agent die NetBox je Kunde selbst an: alle 5 Minuten holt er aus
Automato die Kunden mit dem Werkzeug NetBox (Kunden-Portal bzw. Kundenregister des Cockpits) und
ruft für jeden ohne Instanz `neu` auf. Entfernt wird nie selbst – eine NetBox, die in Automato
nicht mehr freigegeben ist, meldet er nur (`pruefen`); entfernen bleibt `loeschen <kunde> --ja`.

Auf der **Automato-VM** ein eigenes Token für den NetBox-Host erzeugen (wird einmal angezeigt):

```bash
cd /opt/automato/automato && sudo deploy/aufbau.sh netbox-host-token
```

Auf dem **NetBox-Host** (fragt nach dem Token, die Eingabe ist unsichtbar):

```bash
cd /opt/netbox-host/automato
sudo deploy/netbox-host/netbox-host.sh agent https://automato.int.example.ch
sudo deploy/netbox-host/netbox-host.sh pruefen      # Abschnitt „Agent“
```

- Die Adresse ist die der Oberfläche von Automato; der NetBox-Host braucht dorthin HTTPS. Hat
  ihr Zertifikat eine eigene CA, die das System nicht kennt: CA als PEM ablegen und in
  `host.conf` `AUTOMATO_CA=/opt/netbox-host/automato-ca.pem` setzen.
- Das Token liegt in `/opt/netbox-host/automato.token` (nur root), nie in `host.conf`; der Agent
  übergibt es als Kopfzeile aus einer Datei, nicht in der Befehlszeile.
- Das Passwort des Benutzers `admin` einer neuen NetBox liegt in
  `/opt/netbox-host/kunden/<kunde>/admin-passwort` (nur root): in den Tresor, dann löschen.
- Die Tokens für Automato (`token <kunde>`, unten) bleiben ein Handgriff.
- Log: `journalctl -u netbox-host-sync -f`; einmal sofort: `sudo deploy/netbox-host/netbox-host.sh sync`.

**Anbinden an Automato:**

```bash
sudo deploy/netbox-host/netbox-host.sh token kunde-a
```

legt `automato-collector` (nur lesen) und `automato-sync` (schreibt nur die Objekttypen des
Syncs, ADR 0010) an und zeigt Adresse und Tokens. In Automato beim Mandanten des Kunden →
Quellsysteme → + Quellsystem „NetBox“ eintragen. Erneut aufrufen ersetzt die Tokens. Die
Automato-VM muss der eigenen CA vertrauen (`docs/INSTALLATION-PROD.md` 6.3) und in `ALLOW` stehen.

## 5. Betrieb

| Befehl | Wirkung |
|---|---|
| `liste` | Kunden mit Zustand, Version und Adresse |
| `pruefen` | Proxy, Zertifikat (Warnung 30 Tage vor Ablauf), `ALLOW`/`FRONT`, je Kunde NetBox und Weg über den Proxy, Sicherungen jünger als 26 h (je Kunde und Konfiguration) |
| `sichern <kunde\|alle\|konfig>` | je Kunde Datenbank (`pg_dump`) und Medien, verschlüsselt mit age, nach `BACKUP_DIR/<kunde>/`; `alle` (täglich 02:17 per cron) sichert dazu die **Konfiguration** (`host.conf`, Proxy mit Zertifikat, Secrets je Kunde) nach `BACKUP_DIR/_konfig/`; älter als `BACKUP_KEEP_DAYS` wird gelöscht |
| `wiederherstellen <kunde> <datei.sql.gz.age>` | NetBox stoppen, Datenbank (und Medien derselben Sicherung) einspielen, starten – einmal pro Jahr testen |
| `update <kunde\|alle>` | erst sichern, dann netbox-docker und NetBox in der Version aus `host.conf` holen, starten, prüfen. Neue Version zuerst bei einem Kunden |
| `loeschen <kunde> --ja` | letzte Sicherung, dann Container, Daten und Proxy-Eintrag entfernen; die Sicherungen bleiben |
| `sso <kunde\|alle>` | Anmeldung über das Anmelde-Tor einrichten bzw. nachführen (1.4.0): Proxy-Vorlage übernehmen, NetBox-Einstellungen schreiben, NetBox neu starten, Gruppen und Rechte anlegen |

Alle Befehle mit `sudo deploy/netbox-host/netbox-host.sh …` im Ordner `/opt/netbox-host/automato`.
Das Werkzeug selbst aktualisieren: `sudo git pull`, danach `init` (übernimmt die Proxy-Vorlage).

- **Sicherung ausser Haus:** `BACKUP_DIR` liegt auf der VM – regelmässig auf das Sicherungssystem
  kopieren (die Dateien sind verschlüsselt). Konfiguration zurückholen:
  `age -d -i /root/netbox-host-backup.agekey konfig-….tar.gz.age | tar -xzf - -C /opt/netbox-host`.
- **Anmeldung (seit 1.4.0, ADR 0051):** über das Anmelde-Tor des Front-Proxys im Realm des Kunden –
  einmal anmelden, NetBox öffnet sich ohne eigene Maske. Der Front-Proxy übergibt den Benutzer als
  `X-Automato-User` (`<benutzer>@<realm>`) und die Realm-Rollen; dieser Proxy nimmt die Kopfzeilen
  nur von `FRONT` an und entfernt sie bei allen anderen. Rechte: Realm-Rolle **admin** = Superuser,
  **operator** = bearbeiten, alle anderen lesen. `SSO=an` in `host.conf` (Vorgabe); neue Instanzen
  richtet `neu` ein, bestehende `sso <kunde|alle>`. Lokale Konten (`admin`, Token-Benutzer) bleiben
  für den Notfall und melden sich direkt aus dem Management-Netz mit Passwort an.

## 6. Grenzen

- Startseiten, Portal und Weiterleitung liegen seit 1.2.0 auf dem Front-Proxy der Automato-VM
  (`deploy/front/front.sh`, `docs/INSTALLATION-FRONT.md`); ohne ihn erreicht man die NetBox nur
  aus dem Management-Netz.
- Ein Update ändert das Schema der Datenbank – zurück geht es nur über `wiederherstellen` mit der
  Sicherung, die `update` vorher anlegt.
- `token` legt klassische Tokens an (NetBox 4.6); für spätere Versionen die Tokens in NetBox
  erzeugen und in Automato eintragen.
