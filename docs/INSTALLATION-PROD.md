# Automato produktiv installieren – VM mit Disk-Verschlüsselung und Hardening

Runbook für eine **frische VM** (seit 0.116.0, Roadmap Version 1 Nr. 6, Risiko R14). Es führt
von der leeren VM im vCenter bis zur Abnahme: verschlüsselte Disk, gehärtetes Betriebssystem,
gehärtetes Docker, Automato mit Anmeldung, Agent-Gateway und Sicherung. Jeder Schritt sagt, wie
man prüft, ob er wirkt. Beispiele sind anonymisiert (`example.ch`, Netze `10.0.x.0/24`) – vor
dem Einsatz durch die eigenen Werte ersetzen.

Keycloak läuft auf einer **eigenen VM** (ADR 0007): für sie gelten die Schritte 1–6 genauso,
danach `deploy/keycloak/PROD-INSTALL.md`.

```
Admin-Netz ──SSH 22──────────────▶┐
Client-Netze ──HTTPS 443─────────▶│ Automato-VM (Ubuntu, LUKS)
Agent-Stationen ──mTLS 8443──────▶│   proxy (Caddy) ─▶ web-ui ─▶ orchestrator ─▶ postgres, redis
                                  │   agent-gateway (Caddy, mTLS) ─▶ orchestrator
                                  │   collector (zentraler Worker)
                                  └─▶ ausgehend: Geräte (nur lesen), NetBox (Sync), Keycloak,
                                      Tang (Disk entsperren), NTP, DNS, Paketquellen
```

**Grundsätze:** Von aussen sind nur **22** (Admin-Netz), **443** (Oberfläche) und **8443**
(Agenten) erreichbar; Orchestrator (8000) und Web-UI (5173) hören nur auf `127.0.0.1`. Alle Daten
liegen auf der verschlüsselten Disk; die Geräte werden nur gelesen, geschrieben wird nur nach
NetBox (ADR 0010).

---

## 0. Voraussetzungen

- **Ports** (am Perimeter, z. B. FortiGate-Policy, und auf der VM):

  | Richtung | Port | Zweck |
  |---|---|---|
  | Admin-Netz → VM | 22/tcp | SSH (nur Schlüssel) |
  | Client-Netze → VM | 443/tcp | Oberfläche über den Proxy |
  | Agent-Stationen → VM | 8443/tcp | Agent-Gateway (Client-Zertifikat Pflicht) |
  | VM → Geräte | 443/tcp, 22/tcp, 161/udp | FortiGate, APIC, Infoblox, vCenter, NSX ALB (HTTPS); Switches (SSH bzw. SNMPv3) – nur lesend |
  | VM → NetBox | 443/tcp | Lesen und NetBox-Sync (einziger Schreibweg) |
  | VM → Keycloak | 443/tcp | Anmeldung (OIDC, JWKS) |
  | VM → Tang | z. B. 7500/tcp | Disk beim Start entsperren (Schritt 3, Variante B) |
  | VM → Infrastruktur | 53, 123/udp, 443/tcp | DNS, NTP, Paketquellen/Registry (direkt oder über Proxy) |

- **DNS:** `automato.int.example.ch` → IP der VM; **Zertifikat** dafür von der eigenen CA
  (Fullchain + Schlüssel als PEM, `deploy/proxy/README.md`, Variante B).
- **Keycloak** auf eigener VM (`deploy/keycloak/PROD-INSTALL.md`), Realm `automato`.
- **Read-only-Konten** je Gerät (FortiGate-API-Profil nur lesen, APIC `read-all`, Infoblox nur
  lesen, vCenter Rolle *Read-only*, NSX ALB nur lesen, Switches nur `show`); für NetBox ein
  Lese-Token und ein auf die Sync-Objekttypen begrenztes Schreib-Token (`sync-user.sh`).
- **Tresor** (Passwort-Manager/Vault) für Wiederherstellungs-Passphrase der Disk, LUKS-Header,
  Backup-Schlüssel und `.env`-Sicherung – getrennt von der VM.
- Für das automatische Entsperren: **zwei Tang-Server** im internen Netz (Schritt 3) oder ein
  Key Provider im vCenter für vTPM.

---

## 1. VM im vCenter anlegen

**Richtwerte** (nach Datenmenge anpassen; die System-Seite zeigt Datenbankgrösse und Laufzeiten):

| | Minimum | Empfohlen |
|---|---|---|
| vCPU | 4 | 8 |
| RAM | 8 GB | 16 GB |
| Disk | 80 GB | 150 GB (thin) |
| Netzwerk | 1 × VMXNET3 | 1 × VMXNET3 im Server-/Management-Netz |

**Einstellungen:**

- *Gastbetriebssystem:* Ubuntu Linux (64-bit), aktuelle VM-Hardware-Version.
- *VM-Optionen → Boot-Optionen:* **Firmware EFI**, **Secure Boot aktiviert**.
- Optional **vTPM** (Gerät *Trusted Platform Module* hinzufügen) – braucht einen Key Provider im
  vCenter (z. B. Native Key Provider) und ist nur für Variante C in Schritt 3 nötig.
- Optional **VM-Verschlüsselung** bzw. *Verschlüsseltes vMotion: Erforderlich*, falls ein Key
  Provider vorhanden ist. Sie ersetzt LUKS im Gast nicht (Schritt 2): Image-Sicherungen und
  Kopien der VMDK bleiben nur mit LUKS verschlüsselt.
- **Nicht benötigte Geräte entfernen:** Diskette, serielle/parallele Schnittstelle,
  USB-Controller, Soundkarte; das CD/DVD-Laufwerk nach der Installation trennen bzw. entfernen.
- *Erweiterte Parameter* (vSphere Security Configuration Guide; in vSphere 8 sind viele davon
  schon Vorgabe – prüfen, nicht doppelt setzen):

  ```ini
  isolation.tools.copy.disable = TRUE
  isolation.tools.paste.disable = TRUE
  isolation.tools.dnd.disable = TRUE
  isolation.device.connectable.disable = TRUE
  tools.setInfo.sizeLimit = 1048576
  RemoteDisplay.maxConnections = 1
  tools.guest.desktop.autolock = TRUE
  log.keepOld = 10
  log.rotateSize = 2048000
  ```

- **Rechte im vCenter:** Konsole, Snapshots und Einstellungen der VM nur für die Betreiber
  (eigene Rolle); die Konsole ist der Weg zur Disk-Passphrase.
- **Snapshots nie mit Arbeitsspeicher** („Arbeitsspeicher der VM einbeziehen“ aus) und die VM
  **nicht anhalten** (Suspend): RAM-Abbild bzw. `.vmss`/`.vmem` enthalten den entschlüsselten
  Disk-Schlüssel. Snapshots nur kurz (vor einem Update), nie als Sicherung.

---

## 2. Ubuntu mit verschlüsselter Disk installieren

Ubuntu Server **26.04 LTS** (oder 24.04 LTS), ISO aus der offiziellen Quelle, Prüfsumme prüfen.

Im Installer:

1. *Ubuntu Server (minimized)*; Netzwerk statisch (IP, Gateway, DNS), IPv6 nur, wenn genutzt.
2. **Speicher:** *Use an entire disk* → *Set up this disk as an LVM group* → **Encrypt the LVM
   group with LUKS**, starke Passphrase (≥ 20 Zeichen, aus dem Tresor); wenn angeboten, auch den
   *Recovery Key* erzeugen und im Tresor ablegen. Ergebnis: EFI- und `/boot`-Partition
   unverschlüsselt (nur Kernel und initramfs), alles andere – Root, Swap, Daten – in LUKS2.
3. Profil: **persönliches Admin-Konto** (kein Sammelkonto), Hostname z. B. `automato-01`.
4. *Install OpenSSH server* an, Schlüssel optional gleich importieren; **keine** Snaps auswählen.

Nach dem ersten Start prüfen:

```bash
lsblk -f                                   # sda3 crypto_LUKS -> dm_crypt-0 LVM2_member -> ubuntu--vg-*
LUKS=$(sudo blkid -t TYPE=crypto_LUKS -o device); echo "$LUKS"
sudo cryptsetup luksDump "$LUKS" | grep -E "Version|cipher|PBKDF"   # 2, aes-xts-plain64, argon2id
swapon --show                              # Swap liegt im verschlüsselten Root (/swap.img)
mokutil --sb-state                         # SecureBoot enabled
```

**Volumes für Daten anlegen** (vor Docker; der Installer lässt Platz in der Volume-Gruppe frei):

```bash
sudo vgs                                              # freier Platz in ubuntu-vg
sudo lvextend -r -L 30G ubuntu-vg/ubuntu-lv           # Root mindestens 30 GB
sudo lvcreate -L 60G -n docker ubuntu-vg              # Images, Container, Volumes (Postgres)
sudo lvcreate -L 20G -n backup ubuntu-vg              # Sicherungen
sudo mkfs.ext4 -L docker /dev/ubuntu-vg/docker
sudo mkfs.ext4 -L backup /dev/ubuntu-vg/backup
sudo mkdir -p /var/lib/docker /var/backups/automato
echo '/dev/ubuntu-vg/docker /var/lib/docker ext4 defaults 0 2' | sudo tee -a /etc/fstab
echo '/dev/ubuntu-vg/backup /var/backups/automato ext4 defaults,nodev,nosuid,noexec 0 2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount -a && df -h /var/lib/docker /var/backups/automato
```

Ein volles Docker-Volume legt so nicht das Betriebssystem lahm. Alle Volumes liegen in derselben
LUKS-Hülle; TRIM/discard bleibt aus (Vorgabe), damit die Belegung nicht durchscheint.

**LUKS-Header und Wiederherstellung sichern** (einmalig, danach bei jeder Schlüsseländerung):

```bash
sudo cryptsetup luksAddKey "$LUKS"         # zweite Passphrase = Notfall (Tresor, Vier-Augen)
sudo cryptsetup luksHeaderBackup "$LUKS" --header-backup-file /root/luks-header-$(hostname).img
# Datei per scp in den Tresor übernehmen, dann auf der VM löschen:
sudo shred -u /root/luks-header-$(hostname).img
```

Ein beschädigter Header macht die Disk unlesbar – mit der Kopie und einer Passphrase ist sie
wiederherstellbar (`cryptsetup luksHeaderRestore`). Kopie und Passphrase nie zusammen auf der VM.

---

## 3. Disk beim Start entsperren

LUKS fragt beim Start nach der Passphrase. Drei Varianten – die Passphrase bleibt in allen als
Rückfall gültig:

| Variante | Start | Schützt gegen | Aufwand |
|---|---|---|---|
| **A** Passphrase an der VM-Konsole | jedes Mal von Hand | Kopie der VMDK/Sicherung, Datastore-Zugriff | keiner |
| **B** Clevis + Tang (empfohlen) | automatisch, solange ein Tang-Server erreichbar ist | wie A, zusätzlich: Kopie ausserhalb des Netzes startet nicht | zwei kleine Tang-Server |
| **C** Clevis + vTPM | automatisch | Kopie nur der VMDK; vCenter-Admins mit Key Provider können starten | Key Provider im vCenter |

Variante A reicht, wenn Neustarts selten und geplant sind (Wartungsfenster mit Zugang zur
Konsole). B und C passen zu automatischen Sicherheits-Updates mit Neustart. Tang (Open Source)
authentisiert keine Clients: der Tang-Port darf nur aus dem Netz der Automato- und
Keycloak-VM erreichbar sein.

### Variante B – Clevis + Tang

**Tang-Server** (zwei bestehende Linux-VMs im internen Netz, nicht auf dem Datastore der
Automato-VM allein):

```bash
sudo apt-get -y install tang
sudo systemctl edit tangd.socket          # Port festlegen:
#   [Socket]
#   ListenStream=
#   ListenStream=7500
sudo systemctl daemon-reload && sudo systemctl enable --now tangd.socket
sudo tang-show-keys 7500                  # Fingerabdruck (thp) notieren
```

**Auf der Automato-VM:**

```bash
sudo apt-get -y install clevis clevis-luks clevis-tpm2
# initramfs-Werkzeug feststellen: dracut (neuere Releases) oder initramfs-tools (24.04)
dpkg -l dracut initramfs-tools 2>/dev/null | grep ^ii
sudo apt-get -y install clevis-dracut       # bei dracut
sudo apt-get -y install clevis-initramfs    # bei initramfs-tools

# an beide Tang-Server binden, einer genügt (t=1); IP statt Name: im initramfs gibt es kein DNS
LUKS=$(sudo blkid -t TYPE=crypto_LUKS -o device)
sudo clevis luks bind -d "$LUKS" sss '{"t":1,"pins":{"tang":[
  {"url":"http://10.0.30.11:7500","thp":"<thp-tang1>"},
  {"url":"http://10.0.30.12:7500","thp":"<thp-tang2>"}]}}'
sudo clevis luks list -d "$LUKS"            # 1: sss '{"t":1,"pins":{"tang":[...]}}'
```

Statische IP schon im initramfs (ohne DHCP): in `/etc/default/grub`

```ini
GRUB_CMDLINE_LINUX="ip=10.0.30.21::10.0.30.1:255.255.255.0:automato-01:ens192:none rd.neednet=1"
```

(`ens192` = Name der Schnittstelle, `ip -br a`; `rd.neednet=1` braucht nur dracut), dann:

```bash
sudo update-grub
sudo dracut -f --regenerate-all             # bei dracut
sudo update-initramfs -u -k all             # bei initramfs-tools
sudo reboot                                 # startet ohne Eingabe, Konsole zeigt "clevis"
```

**Rückfall testen** (einmal vor Go-Live): Tang-Port für die VM kurz sperren, neu starten → die
Konsole fragt nach der Passphrase; wieder freigeben. **Schlüssel der Tang-Server wechseln**
(z. B. jährlich): auf dem Tang-Server neue Schlüssel erzeugen, auf der VM
`sudo clevis luks regen -d "$LUKS" -s <slot>` (Slot aus `clevis luks list`), alte Tang-Schlüssel
danach umbenennen (`.<name>.jwk`), damit sie nicht mehr angeboten werden.

### Variante C – Clevis + vTPM

VM mit vTPM (Schritt 1), dann statt der Tang-Bindung:

```bash
sudo clevis luks bind -d "$LUKS" tpm2 '{"pcr_bank":"sha256","pcr_ids":"7"}'   # an Secure Boot gebunden
```

initramfs wie in Variante B neu erzeugen. Strenger: beides verlangen
(`sss` mit `"t":2` und den Pins `tang` und `tpm2`) – dann startet die VM nur mit ihrem vTPM
**und** im eigenen Netz.

---

## 4. Betriebssystem härten

### 4.1 Updates

```bash
sudo apt-get update && sudo apt-get -y full-upgrade
sudo apt-get -y install unattended-upgrades needrestart
sudo dpkg-reconfigure -plow unattended-upgrades
sudo tee /etc/apt/apt.conf.d/52automato >/dev/null <<'EOF'
// Sicherheits-Updates automatisch; Docker kontrolliert über deploy/aufbau.sh docker-update
Unattended-Upgrade::Package-Blacklist { "docker.*"; "containerd.*"; "runc"; };
Unattended-Upgrade::Remove-Unused-Dependencies "true";
// Neustart nur mit automatischem Entsperren (Schritt 3 B/C), sonst "false" + Wartungsfenster
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:30";
EOF
sudo unattended-upgrade --dry-run -d 2>&1 | tail -5
```

Docker wird nicht nebenbei aktualisiert (ein Update startet alle Container neu), sondern mit
`AUTOMATO_DOCKER_UPDATE=1` bei jedem Automato-Update bzw. von Hand mit
`sudo deploy/aufbau.sh docker-update` (Schritt 9).

### 4.2 Konten und SSH

```bash
sudo groupadd -f automato-admins
sudo usermod -aG automato-admins <admin>            # je Betreiber ein eigenes Konto
sudo passwd -l root                                 # Root-Login gesperrt (Ubuntu-Vorgabe)
sudo tee /etc/ssh/sshd_config.d/10-automato.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
AllowGroups automato-admins
X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep -E "^(permitrootlogin|passwordauthentication|allowgroups)"
```

**Zweite SSH-Sitzung offen lassen**, bis die Anmeldung mit Schlüssel in einer neuen Sitzung
klappt. `sudo` verlangt das Passwort des eigenen Kontos. Die Gruppe `docker` ist gleichwertig mit
root – niemanden aufnehmen, Docker-Befehle mit `sudo` (steht dann im Journal).

### 4.3 Host-Firewall

```bash
sudo apt-get -y install ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing                     # ausgehend am Perimeter einschränken
sudo ufw allow from 10.0.10.0/24 to any port 22 proto tcp comment 'SSH Admin-Netz'
sudo ufw --force enable && sudo ufw status verbose
```

**Wichtig:** Ports, die Docker veröffentlicht (443, 8443), laufen an ufw vorbei (Kette
`FORWARD`, nicht `INPUT`). Sie werden in der Kette `DOCKER-USER` eingeschränkt – am Ende von
`/etc/ufw/after.rules` anfügen (Netze und Schnittstelle anpassen):

```text
# Automato: von Docker veröffentlichte Ports nur aus freigegebenen Netzen
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DOCKER-USER -i ens192 -p tcp -m conntrack --ctorigdstport 443 --ctdir ORIGINAL -s 10.0.10.0/24 -j RETURN
-A DOCKER-USER -i ens192 -p tcp -m conntrack --ctorigdstport 443 --ctdir ORIGINAL -s 10.8.0.0/16 -j RETURN
-A DOCKER-USER -i ens192 -p tcp -m conntrack --ctorigdstport 8443 --ctdir ORIGINAL -s 10.0.20.0/24 -j RETURN
-A DOCKER-USER -i ens192 -m conntrack --ctstate NEW -j DROP
-A DOCKER-USER -j RETURN
COMMIT
```

```bash
sudo ufw reload && sudo iptables -L DOCKER-USER -n -v --line-numbers
```

Gilt für das iptables-Backend von Docker (Vorgabe). Mit IPv6 dasselbe in `after6.rules`.
Ausgehende Verbindungen der Container (Geräte, NetBox, Keycloak) sind davon nicht betroffen.
Ob der Weg aus den Containern zum Host frei ist, prüft später `sudo deploy/aufbau.sh pruefen`
(Abschnitt Host-Firewall; Regeln dafür mit `firewall --anwenden`).

### 4.4 Kernel und Dateisysteme

```bash
sudo tee /etc/sysctl.d/90-automato.conf >/dev/null <<'EOF'
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2
kernel.yama.ptrace_scope = 1
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.tcp_syncookies = 1
# net.ipv4.ip_forward bleibt 1 - Docker braucht es
EOF
sudo sysctl --system >/dev/null
echo '* hard core 0' | sudo tee /etc/security/limits.d/90-nocore.conf
echo 'tmpfs /tmp tmpfs defaults,nosuid,nodev,size=2G,mode=1777 0 0' | sudo tee -a /etc/fstab
echo 'tmpfs /dev/shm tmpfs defaults,nosuid,nodev,noexec 0 0' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount -o remount /dev/shm && sudo mount /tmp
findmnt /tmp /dev/shm
```

### 4.5 Zeit, Protokolle, Audit

```bash
sudo apt-get -y install chrony auditd
# interne NTP-Server (Zeit ist für die Anmeldung und TLS wichtig)
echo 'server ntp1.example.ch iburst' | sudo tee /etc/chrony/sources.d/intern.sources
sudo systemctl restart chrony && chronyc sources && timedatectl | grep synchronized
sudo timedatectl set-timezone Europe/Zurich

# Journal dauerhaft, begrenzt
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=1G\n' | sudo tee /etc/systemd/journald.conf.d/90-automato.conf
sudo systemctl restart systemd-journald

# Audit: Zugriffe auf Konfiguration und Schlüssel
sudo tee /etc/audit/rules.d/90-automato.rules >/dev/null <<'EOF'
-w /opt/automato/automato/.env -p rwa -k automato-env
-w /opt/automato/automato/deploy/tls/pki -p rwa -k automato-pki
-w /etc/docker -p wa -k docker-config
-w /usr/bin/docker -p x -k docker-cmd
-w /etc/ssh/sshd_config.d -p wa -k sshd
-w /etc/sudoers -p wa -k sudoers
-w /etc/sudoers.d -p wa -k sudoers
EOF
sudo augenrules --load && sudo auditctl -l | head
```

Die Pfade unter `/opt/automato` gibt es erst nach Schritt 6; danach `sudo augenrules --load`
wiederholen. Journal und Audit-Log an ein zentrales Log-System weiterleiten, wenn vorhanden.

### 4.6 Nicht benötigte Dienste

```bash
systemctl list-unit-files --state=enabled --type=service
sudo systemctl disable --now multipathd ModemManager 2>/dev/null   # in einer VM nicht nötig
sudo apt-get -y purge snapd 2>/dev/null; sudo apt-get -y autoremove
sudo aa-status | head -3                                          # AppArmor aktiv
ss -tulpn                                                         # nur sshd (22), chrony
```

### 4.7 Prüfen

```bash
sudo apt-get -y install lynis
sudo lynis audit system --quick | tail -30        # Hardening-Index und Vorschläge
```

Vorschläge von Lynis bewusst übernehmen oder begründet ablehnen (in der Betriebsdoku
festhalten). Wer Ubuntu Pro schon hat, kann zusätzlich das CIS-Profil von USG prüfen lassen.

---

## 5. Docker installieren und härten

Installation wie in `deploy/keycloak/PROD-INSTALL.md`, Abschnitt 2 (Ubuntu-Pakete
`docker-compose-v2` oder Docker-Upstream-Pakete, nicht mischen). Danach **vor dem ersten Start
von Automato**:

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "log-driver": "local",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true,
  "no-new-privileges": true,
  "userland-proxy": false
}
EOF
sudo systemctl restart docker && sudo systemctl enable docker containerd
docker info --format '{{.LoggingDriver}} live-restore={{.LiveRestoreEnabled}} root={{.DockerRootDir}}'
docker info --format '{{.SecurityOptions}}'      # apparmor, seccomp, no-new-privileges
```

- `log-driver local` mit Grösse: Container-Protokolle füllen die Disk nicht.
- `live-restore`: Container laufen weiter, wenn der Docker-Dienst neu startet.
- `no-new-privileges`: Prozesse in Containern können keine Rechte über setuid gewinnen.
- `userland-proxy: false`: veröffentlichte Ports gehen über iptables (DNAT); der Proxy sieht die
  echte Quell-IP – nötig für `AUTOMATO_UI_ALLOW` (Schritt 6).
- `DockerRootDir` = `/var/lib/docker` auf dem eigenen Volume (Schritt 2).

---

## 6. Automato installieren

### 6.1 Code und Grundgerüst

```bash
sudo apt-get -y install git age curl python3 openssl
sudo install -d -m 750 -o root -g automato-admins /opt/automato
cd /opt/automato
sudo git clone <repo-url> automato           # Lesezugang, z. B. Deploy-Key nur lesen
cd automato && sudo git checkout <tag>       # freigegebener Stand
sudo cp .env.example .env && sudo chmod 600 .env && sudo chown root:root .env
```

### 6.2 `.env` für Produktion

Werte erzeugen (`openssl rand -hex 32` je Secret – hex, weil Teile davon in URLs stehen) und in
`.env` setzen bzw. ergänzen:

```ini
AUTOMATO_ENVIRONMENT=prod
AUTOMATO_COMPOSE_PROFILES="--profile core --profile collectors --profile proxy --profile agent-gateway"
POSTGRES_PASSWORD=<hex>
AUTOMATO_INTERNAL_API_TOKEN=<hex>
AUTOMATO_AUDIT_ENCRYPTION_KEY=<hex>

# Orchestrator und Web-UI nur lokal - von aussen nur Proxy (443) und Agent-Gateway (8443)
AUTOMATO_API_BIND=127.0.0.1
AUTOMATO_UI_DEV_BIND=127.0.0.1

# Oberfläche: Name, Zertifikat der eigenen CA, erlaubte Netze (Management-Netz, VPN)
AUTOMATO_UI_HOST=automato.int.example.ch
AUTOMATO_UI_TLS="/etc/caddy/pki/automato.int.example.ch.crt /etc/caddy/pki/automato.int.example.ch.key"
AUTOMATO_UI_ALLOW="10.0.10.0/24 10.8.0.0/16"
AUTOMATO_CORS_ORIGINS=["https://automato.int.example.ch"]

# Anmeldung über Keycloak (eigene VM); die Read-API ohne Anmeldung bleibt zu
AUTOMATO_AUTH_MODE=oidc
AUTOMATO_OIDC_ISSUER=https://auth.example.ch/realms/automato
AUTOMATO_OIDC_AUDIENCE=automato-api
AUTOMATO_OIDC_JWKS_URL=https://auth.example.ch/realms/automato/protocol/openid-connect/certs
AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API=false

# Agenten nur über das mTLS-Gateway, Zertifikat gehört zum Agenten
AUTOMATO_AGENT_GATEWAY_HOST=automato.int.example.ch
AUTOMATO_AGENT_GATEWAY_SECRET=<hex>
AUTOMATO_INTERNAL_MTLS_MODE=bind

# Docker bei jedem Automato-Update mit aktualisieren; Zertifikate der Gegenstellen prüfen
AUTOMATO_DOCKER_UPDATE=1
AUTOMATO_NETBOX_VERIFY_TLS=true
AUTOMATO_FORTIGATE_VERIFY_TLS=true
AUTOMATO_ACI_VERIFY_TLS=true
```

Hinweise:

- Mit `AUTOMATO_ENVIRONMENT=prod` startet der Orchestrator nicht mit dem Dev-Token und
  verlangt im Modus `dev` (ohne Anmeldung) die bewusste Freigabe der Read-API; mit `oidc` ist
  sie nicht nötig und bleibt `false` (seit 0.116.0).
- `AUTOMATO_UI_ALLOW` sperrt die Oberfläche im Proxy für alle anderen Quellen (HTTP 403) –
  zusätzlich zur `DOCKER-USER`-Regel. IPv6-Clients mit ihrem Netz aufnehmen.
- Secrets dürfen statt Klartext Referenzen sein (`openbao:feld`, `file:name`,
  `deploy/secrets/README.md`); ohne Tresor-Dienst liegen sie in `.env` (0600, root) auf der
  verschlüsselten Disk.
- Der Schlüssel der Geräte-Zugangsdaten entsteht beim ersten Start im Volume
  `orchestrator-data` (oder `AUTOMATO_CREDENTIALS_KEY`); er wird mitgesichert (Schritt 7).

### 6.3 Zertifikate

```bash
# Oberfläche: Fullchain + Schlüssel der eigenen CA
sudo install -d -m 750 deploy/proxy/pki
sudo install -m 640 automato.int.example.ch.crt automato.int.example.ch.key deploy/proxy/pki/

# Agent-Gateway: eigene Agent-CA, Server- und Agent-Zertifikate (deploy/agent-gateway/README.md)
cd deploy/agent-gateway
sudo ./make-certs.sh ca
sudo ./make-certs.sh server automato.int.example.ch
sudo ./make-certs.sh agent <agent-name>        # je Agent, Name wie unter Collector-Agenten
cd ../..
```

Den Schlüssel der Agent-CA (`deploy/agent-gateway/pki/agents-ca.key`) nach dem Ausstellen in
den Tresor übernehmen und auf der VM löschen; er wird erst für das nächste Agent-Zertifikat
wieder gebraucht.

### 6.4 Erster Start und Härtung des Stacks

```bash
P="--profile core --profile collectors --profile proxy --profile agent-gateway"   # wie in .env
sudo docker compose $P run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile
sudo deploy/aufbau.sh backup-schluessel     # age-Schlüssel für die Sicherung, Eintrag in .env
sudo docker compose $P up -d --build        # erster Start (Migrationen laufen im Dienst migrate)
sudo deploy/aufbau.sh db-rollen             # Orchestrator ohne DDL-Rechte (eigene DB-Rolle)
sudo deploy/aufbau.sh tls-intern            # TLS zwischen allen Containern (interne CA)
sudo deploy/aufbau.sh pruefen               # alles grün, Exit 0
ss -tlnp | grep -E ':(443|8443|8000|5173) '  # 8000/5173 nur auf 127.0.0.1
```

Den age-Schlüssel (`/root/automato-backup.agekey`) **sofort** in den Tresor kopieren – ohne ihn
ist keine Sicherung lesbar. Danach `sudo augenrules --load` (Audit-Regeln aus 4.5 greifen jetzt).
Spätere Updates laufen mit `deploy/update.sh` (Schritt 9) – es sichert vorher und braucht dafür
den laufenden Stack.

### 6.5 Anmeldung (Keycloak)

1. Keycloak nach `deploy/keycloak/PROD-INSTALL.md` (Abschnitte 9a–9c), Redirect-URI und Web
   Origin von `automato-web` auf `https://automato.int.example.ch` (`deploy/proxy/README.md`).
2. **OTP für alle**: Admin-Konsole → Realm `automato` → *Authentication → Required actions →
   Configure OTP* → **Set as default action** (gilt für neue Benutzer); bestehende Benutzer unter
   *Users → … → Required user actions → Configure OTP*.
3. Prüfen:

   ```bash
   curl -s http://127.0.0.1:8000/auth/config            # auth_mode "oidc"
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/rules   # 401 ohne Token
   ```

   (mit TLS intern `https://` und `--cacert deploy/tls/pki/client/ca.crt`). Im Browser
   `https://automato.int.example.ch` → Keycloak → Passwort + OTP → zurück.

### 6.6 Quellsysteme

Mandant, Quellsysteme und Zugangsdaten in der Oberfläche anlegen (nur Read-only-Konten; NetBox
mit Lese- und Schreib-Token), Agenten nach `deploy/collector-agent/README.md` mit Zertifikat aus
6.3 (`AUTOMATO_ORCHESTRATOR_URL=https://automato.int.example.ch:8443`). Danach System →
Verbindungen: jede Gegenstelle grün. Den Test, ob ein FortiGate-Token Schreibversuche ablehnt
(`automato-collector --check --strict`, R16), nur bewusst und abgesprochen ausführen – er zählt
als fehlgeschlagene Anmeldung.

---

## 7. Sicherung

```bash
sudo crontab -e
# Datenbank, Schlüssel der Zugangsdaten, Agent-Signatur (age-verschlüsselt, 14 Tage)
15 1 * * * cd /opt/automato/automato && deploy/backup/backup.sh
# Konfiguration: .env und Zertifikate (nicht in backup.sh enthalten), ebenfalls mit age
30 1 * * * cd /opt/automato/automato && tar -czf - .env deploy/tls/pki deploy/proxy/pki deploy/agent-gateway/pki | age -r "$(grep '^AUTOMATO_BACKUP_AGE_RECIPIENT=' .env | cut -d= -f2)" > /var/backups/automato/konfig-$(date +\%F).tar.gz.age && find /var/backups/automato -name 'konfig-*.age' -mtime +14 -delete
```

Die Sicherungen zusätzlich auf ein anderes System kopieren (sie sind verschlüsselt), die
**Wiederherstellung** einmal auf einer Test-VM üben (`deploy/backup/README.md`). Image-Sicherungen
der VM (z. B. Veeam) enthalten dank LUKS nur verschlüsselte Blöcke; wiederhergestellt startet die
VM mit Tang/vTPM bzw. der Passphrase.

---

## 8. Abnahme vor Go-Live

- [ ] **Disk:** `lsblk -f` zeigt `crypto_LUKS`; LUKS2, `aes-xts-plain64`, `argon2id`; Neustart
      ohne Eingabe (Variante B/C) **und** Rückfall mit Passphrase getestet; Header-Kopie,
      Notfall-Passphrase und Recovery Key im Tresor.
- [ ] **VM:** EFI + Secure Boot (`mokutil --sb-state`), keine CD/Diskette/seriell/USB,
      Snapshots ohne Arbeitsspeicher, Konsole nur für Betreiber.
- [ ] **SSH:** `sshd -T` → `permitrootlogin no`, `passwordauthentication no`,
      `allowgroups automato-admins`.
- [ ] **Firewall:** `ufw status` (22 nur Admin-Netz); `iptables -L DOCKER-USER`; aus einem
      nicht freigegebenen Netz sind 443 und 8443 **nicht** erreichbar; 8000/5173 nur auf
      `127.0.0.1` (`ss -tlnp`).
- [ ] **Updates:** `unattended-upgrade --dry-run` ohne Fehler, Docker ausgenommen,
      Neustart-Regel passend zur Entsperr-Variante.
- [ ] **Docker:** `daemon.json` aktiv (Log-Rotation, live-restore, no-new-privileges,
      userland-proxy aus).
- [ ] **Automato:** `sudo deploy/aufbau.sh pruefen` Exit 0; System-Seite: Umgebung `prod`,
      Anmeldung `oidc`, TLS intern aktiv, DB-Rolle ohne DDL, Audit verschlüsselt, Agent-Kanal
      `bind`, letzte Sicherung von heute.
- [ ] **Anmeldung:** Login nur mit OTP; ohne Token `401`; Rollen (viewer/operator/admin) und
      Mandanten je Benutzer geprüft.
- [ ] **Quellsysteme:** nur Read-only-Konten, NetBox-Schreib-Token auf die Sync-Objekttypen
      begrenzt, System → Verbindungen grün.
- [ ] **Sicherung:** Datenbank- und Konfig-Sicherung vorhanden, extern kopiert,
      Wiederherstellung auf einer Test-VM geübt, age-Schlüssel und Agent-CA-Schlüssel im Tresor.
- [ ] **Lynis** gelaufen, Abweichungen begründet; Zeit synchron (`timedatectl`).

---

## 9. Betrieb

- **Update:** `sudo deploy/update.sh <tag>` (ohne Angabe nur auf einem Branch: `git pull`) –
  sichert vorher, baut, startet, prüft `/health`;
  bei Problemen `sudo deploy/update.sh --rollback` (BETRIEB, Abschnitt 4b). Mit
  `AUTOMATO_DOCKER_UPDATE=1` kommt Docker dabei mit.
- **Neustart der VM:** Variante A braucht die Passphrase an der Konsole; danach starten alle
  Container von selbst (`restart: unless-stopped`), Kontrolle mit `sudo deploy/aufbau.sh status`.
- **Zertifikate:** Oberfläche (eigene CA) vor Ablauf ersetzen und `docker compose … restart
  proxy`; TLS intern mit `sudo deploy/aufbau.sh tls-intern --erneuern` (`pruefen` warnt 30 Tage
  vorher); Agent-Zertifikate je Agent neu ausstellen.
- **Schlüssel wechseln:** Disk-Passphrase mit `cryptsetup luksChangeKey`, danach neue
  Header-Kopie; Tang-Schlüssel mit `clevis luks regen`; Secrets in `.env` (Token, Passwörter) bei
  Personalwechsel – DB-Passwort der App-Rolle mit `deploy/aufbau.sh db-rollen --neu`.
- **Konten:** Betreiber verlassen das Team → SSH-Schlüssel und Konto entfernen, Keycloak-Benutzer
  deaktivieren.

## 10. Bekannte Grenzen (Stand 0.116.0)

- Die Oberfläche läuft im Container `web-ui` als Vite-Server hinter dem Proxy (nur auf
  `127.0.0.1`); ein Produktions-Build mit statischen Dateien ist offen (Roadmap Version 1 Nr. 6).
- Die Container laufen noch ohne `read_only`, `cap_drop` und Ressourcen-Grenzen je Dienst; die
  Härtung geschieht heute auf VM- und Docker-Ebene (dieses Runbook).
- OTP für alle Benutzer ist ein Schritt in der Keycloak-Konsole (6.5), nicht im Realm-Import.
