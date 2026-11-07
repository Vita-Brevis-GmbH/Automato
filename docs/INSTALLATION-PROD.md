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
  | Admin-Netz → VM | 22/tcp | SSH (Passwort, nur Gruppe `automato-admins`) |
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

Anmeldung per SSH mit **Passwort** (Entscheid des Anwenders 2026-11-02) – abgesichert durch: SSH
nur aus dem Admin-Netz (4.3), kein Root-Login, nur Konten der Gruppe `automato-admins`, höchstens
drei Versuche je Verbindung und `fail2ban` (sperrt Quellen nach Fehlversuchen). Je Betreiber ein
eigenes Konto mit starkem Passwort. SSH-Schlüssel gehen zusätzlich (optional, unten).

```bash
sudo groupadd -f automato-admins
sudo usermod -aG automato-admins <admin>            # je Betreiber ein eigenes Konto
sudo passwd -l root                                 # Root-Login gesperrt (Ubuntu-Vorgabe)
sudo tee /etc/ssh/sshd_config.d/10-automato.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication yes
PubkeyAuthentication yes
KbdInteractiveAuthentication no
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
sudo apt-get -y install fail2ban                    # Jail sshd ist bei Ubuntu eingeschaltet
sudo fail2ban-client status sshd
```

**Zweite SSH-Sitzung offen lassen**, bis die Anmeldung in einer neuen Sitzung klappt (`id` zeigt
die Gruppe `automato-admins` erst in einer neuen Sitzung; ohne die Gruppe weist SSH das Konto ab).
`sudo` verlangt das Passwort des eigenen Kontos. Die Gruppe `docker` ist gleichwertig mit root –
niemanden aufnehmen, Docker-Befehle mit `sudo` (steht dann im Journal).

**Optional – SSH-Schlüssel** (zusätzlich zum Passwort): auf dem eigenen Rechner
`ssh-keygen -t ed25519`, dann unter Windows (PowerShell)
`type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh <admin>@<vm> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"`
bzw. unter macOS/Linux `ssh-copy-id <admin>@<vm>`. Mit **PuTTY**: in PuTTYgen *EdDSA (Ed25519)*
erzeugen, `.ppk` speichern, den Text „Public key for pasting into OpenSSH authorized_keys file“
in `~/.ssh/authorized_keys` der VM einfügen und in PuTTY unter *Connection → SSH → Auth →
Credentials* die `.ppk` wählen. Wer später **nur noch Schlüssel** zulassen will: erst eine neue
Sitzung mit Schlüssel prüfen, dann in `10-automato.conf` `PasswordAuthentication no` setzen.

**Ausgesperrt?** (Meldung „No supported authentication methods available (server sent:
publickey)“ – z. B. nach einer früheren Fassung dieser Anleitung mit nur Schlüsseln): über die
**Konsole im vCenter** (Web- bzw. Remote-Konsole – die SSH-Einstellungen gelten dort nicht) mit
Konto und Passwort anmelden und die Passwort-Anmeldung wieder zulassen:

```bash
sudo sed -i -e 's/^PasswordAuthentication no/PasswordAuthentication yes/' \
  -e '/^AuthenticationMethods/d' /etc/ssh/sshd_config.d/10-automato.conf
sudo sshd -t && sudo systemctl restart ssh   # neu starten, nicht nur neu einlesen
# was für das eigene Konto wirklich gilt (auch Match-Blöcke), IP des eigenen PCs:
sudo sshd -T -C user=<admin>,host=pc,addr=<IP-des-PCs> | grep -E "^(passwordauthentication|authenticationmethods|allowgroups)"
id <admin>                                   # muss automato-admins enthalten, sonst usermod -aG …
```

Erwartet: `passwordauthentication yes` und `authenticationmethods any`. Steht weiterhin `no`
bzw. `publickey`, setzt es eine andere Datei oder ein `Match`-Block – suchen mit
`sudo grep -rniE "^\s*(match|passwordauthentication|authenticationmethods)" /etc/ssh/sshd_config /etc/ssh/sshd_config.d/`
(es gilt der **erste** gefundene Wert; `10-automato.conf` kommt vor `50-cloud-init.conf`).
Klappt es danach noch nicht: `sudo journalctl -u ssh -f` beim Anmelden mitlesen – bleibt es
still, erreicht der Client einen anderen Rechner (IP/Name in PuTTY prüfen).

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
`/etc/ufw/after.rules` anfügen (Netze und Schnittstelle anpassen; den Namen der Schnittstelle
zeigt `ip -br addr`, z. B. `ens33` oder `ens192` – ohne passenden Namen greift keine Zeile):

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
Beim Aufruf der Oberfläche muss der Zähler der `443`-Zeile steigen.

**Firewall vor der VM:** Freigaben zur VM ohne NAT (sonst sieht Automato nur die Adresse der
Firewall, `AUTOMATO_UI_ALLOW` greift nicht) – HTTPS 443 aus den Client-Netzen, TCP 8443 aus den
Netzen der Agenten. Ping und SSH sagen darüber nichts. Prüfen: `sudo tcpdump -ni <schnittstelle>
tcp port 443` auf der VM, dazu auf dem PC `Test-NetConnection <name> -Port 443` – kommt auf der VM
kein Paket an, fehlt die Freigabe davor (FortiGate: *Policy Lookup* bzw. `diagnose debug flow`).
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

### 5.1 Installieren

Voraussetzung: `/var/lib/docker` ist eingehängt (Schritt 2, `findmnt /var/lib/docker` zeigt das
eigene Volume) – sonst landen Images und Datenbank auf dem Root-Dateisystem.

**Variante A – Ubuntu-Pakete (empfohlen, 24.04 und 26.04):** `docker-compose-v2` aus `universe`
zieht die Engine (`docker.io`), `containerd` und `runc` mit; Sicherheitsupdates kommen mit den
normalen Ubuntu-Updates (Schritt 4.1).

```bash
sudo apt-get update
sudo apt-get -y install docker-compose-v2 docker-buildx   # buildx: ohne Warnung „Bake“ beim Bau
sudo systemctl enable --now docker containerd
docker --version && docker compose version    # beide müssen eine Version zeigen
```

**Variante B – Docker-Upstream-Pakete** (von Docker Inc., meist neuer) – nur eine der beiden
Varianten, **nicht mischen**. Fehlt der Codename bei Docker noch (`404 … Release`, oft bei einem
frischen Ubuntu-Release), auf das letzte unterstützte LTS zeigen (`CODENAME=noble`):

```bash
sudo apt-get -y install ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")     # bei 404: CODENAME=noble
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
sudo systemctl enable --now docker containerd
docker --version && docker compose version
```

- Schlägt `apt-get update` mit der Docker-Zeile fehl: `sudo rm -f /etc/apt/sources.list.d/docker.list
  /etc/apt/keyrings/docker.gpg` und Variante A nehmen.
- Benutzer **nicht** in die Gruppe `docker` aufnehmen (sie entspricht root) – Docker-Befehle mit
  `sudo`.
- Später aktualisieren: `sudo deploy/aufbau.sh docker-update` (erkennt beide Varianten).

### 5.2 Härten

**Nach 5.1** (ohne installiertes Docker fehlt `/etc/docker`, die Datei entsteht nicht) und **vor
dem ersten Start von Automato:**

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
sudo docker info --format '{{.LoggingDriver}} live-restore={{.LiveRestoreEnabled}} root={{.DockerRootDir}}'
sudo docker info --format '{{.SecurityOptions}}' # apparmor, seccomp, no-new-privileges
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

Alle Einstellungen und Secrets stehen in `.env` im Repo-Ordner – **vor dem ersten Start** (6.4)
setzen. Die Secrets direkt in die Datei schreiben (sie erscheinen nicht am Bildschirm); die
Hilfsfunktion ersetzt eine vorhandene (auch auskommentierte) Zeile bzw. hängt sie an. Sie gilt –
wie später die Variable `P` – **nur in der aktuellen Shell**: nach einer neuen Anmeldung oder
`sudo -i` die Zeile `setenv() …` erneut einfügen (als eine Zeile; `type setenv` zeigt „is a
function“); `sudo setenv` geht nicht.

```bash
cd /opt/automato/automato
setenv() { if sudo grep -q "^#\? *$1=" .env; then sudo sed -i "s|^#\? *$1=.*|$1=$2|" .env; else echo "$1=$2" | sudo tee -a .env >/dev/null; fi; }
for k in POSTGRES_PASSWORD AUTOMATO_INTERNAL_API_TOKEN AUTOMATO_AUDIT_ENCRYPTION_KEY AUTOMATO_AGENT_GATEWAY_SECRET; do
  setenv "$k" "$(openssl rand -hex 32)"          # hex, weil Teile davon in URLs stehen
done
```

Die übrigen Werte mit `setenv NAME 'wert'` bzw. `sudo nano .env` – eigene Namen und Netze statt
der Beispiele:

```ini
AUTOMATO_ENVIRONMENT=prod
AUTOMATO_COMPOSE_PROFILES="--profile core --profile collectors --profile proxy --profile agent-gateway"
COMPOSE_PROFILES=core,collectors,proxy,agent-gateway
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

Was die Werte bedeuten:

- `POSTGRES_PASSWORD` gilt nur beim **ersten** Anlegen der Datenbank – später ändern wirkt nicht
  (ohne Daten: `sudo docker compose $P down -v` löscht die Datenbank, dann neu starten).
- `AUTOMATO_COMPOSE_PROFILES`: `core` (Datenbank, Orchestrator, Oberfläche), `collectors`
  (Worker), `proxy` (HTTPS 443), `agent-gateway` (8443, nur mit Agenten in anderen Netzen – sonst
  weglassen, dann auch `AUTOMATO_AGENT_GATEWAY_*` und `AUTOMATO_INTERNAL_MTLS_MODE`).
  Die Skripte (`aufbau.sh`, `update.sh`) lesen diese Variable.
- `COMPOSE_PROFILES`: dieselben Profile mit Komma – Compose liest sie selbst aus `.env`. Damit
  genügt im Repo-Ordner `sudo docker compose ps|logs|up -d …` ohne `$P`, auch in neuen
  Sitzungen (ohne sie und ohne `$P` meldet Compose z. B. „no such service: web-ui“).
- `AUTOMATO_UI_HOST`: DNS-Name der Oberfläche (im internen DNS auf die VM, passend zum
  Zertifikat). `AUTOMATO_UI_TLS`: Pfade **im Container** – `/etc/caddy/pki` ist
  `deploy/proxy/pki/` auf der VM (6.3). `AUTOMATO_CORS_ORIGINS` = `https://` + `UI_HOST`.
- `AUTOMATO_UI_ALLOW`: Netze, die die Oberfläche öffnen dürfen (mit Leerzeichen getrennt).
- OIDC: Adresse der Keycloak-VM mit Realm `automato`, `AUDIENCE` = Client der API. Keycloak darf
  später fertig werden (6.5) – bis dahin startet Automato, nur die Anmeldung geht noch nicht.
- `*_VERIFY_TLS`: Vorgaben für Quellsysteme ohne eigene Einstellung (diese je Gerät in der
  Oberfläche).
- Werte mit Leerzeichen (`UI_TLS`, `UI_ALLOW`, `COMPOSE_PROFILES`) in Anführungszeichen; jede
  Variable nur einmal in `.env`.

Prüfen (ohne Secrets anzuzeigen):

```bash
sudo chmod 600 .env && sudo chown root:root .env
sudo grep -E '^(AUTOMATO_|POSTGRES_)' .env | sed -E 's/((PASSWORD|TOKEN|SECRET|KEY)=).*/\1***/'
P="--profile core --profile collectors --profile proxy --profile agent-gateway"
sudo docker compose $P config --quiet && echo "Konfiguration ok"
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

Der Name der Oberfläche (`AUTOMATO_UI_HOST`) muss im Zertifikat stehen (SAN). `AUTOMATO_UI_TLS`
nennt die Pfade **im Container** (`/etc/caddy/pki/…`) – auf der VM ist das `deploy/proxy/pki/` im
Repo-Ordner. Alle Befehle im Repo-Ordner `/opt/automato/automato`.

```bash
H=automato.int.example.ch                     # = AUTOMATO_UI_HOST
sudo install -d -m 750 deploy/proxy/pki
```

**Variante 1 – Schlüssel und Anfrage auf der VM** (empfohlen, der Schlüssel verlässt die VM nie):

```bash
sudo openssl req -new -newkey rsa:3072 -nodes -keyout deploy/proxy/pki/$H.key -out /tmp/$H.csr \
  -subj "/CN=$H" -addext "subjectAltName=DNS:$H" -addext "extendedKeyUsage=serverAuth"
sudo chmod 640 deploy/proxy/pki/$H.key
```

Die CSR (`/tmp/<host>.csr`) bei der CA einreichen – AD CS auf einem Domänen-Rechner:
`certreq -submit -attrib "CertificateTemplate:WebServer" <host>.csr <host>.cer`, das Zertifikat
der ausstellenden CA mit `certutil -ca.cert issuing-ca.cer`. Beide `.cer` nach `/tmp` auf die VM
und als Fullchain zusammensetzen (Leaf zuerst; PEM oder DER):

```bash
for f in /tmp/$H.cer /tmp/issuing-ca.cer; do
  openssl x509 -in "$f" 2>/dev/null || openssl x509 -inform DER -in "$f"
done | sudo tee deploy/proxy/pki/$H.crt >/dev/null
```

**Variante 2 – fertiges Zertifikat mit Schlüssel** (z. B. `.pfx` aus der CA), per WinSCP bzw.
`scp` nach `/home/<admin>/`:

```bash
PFX=/home/<admin>/$H.pfx                      # fragt jeweils das Passwort der PFX ab
openssl pkcs12 -in "$PFX" -clcerts -nokeys | sed -n '/BEGIN/,/END/p' | sudo tee deploy/proxy/pki/$H.crt >/dev/null
openssl pkcs12 -in "$PFX" -cacerts -nokeys | sed -n '/BEGIN/,/END/p' | sudo tee -a deploy/proxy/pki/$H.crt >/dev/null
openssl pkcs12 -in "$PFX" -nocerts -nodes | sed -n '/BEGIN/,/END/p' | sudo tee deploy/proxy/pki/$H.key >/dev/null
sudo chmod 640 deploy/proxy/pki/$H.crt deploy/proxy/pki/$H.key
```

Meldet OpenSSL „unsupported“, an jede `openssl pkcs12`-Zeile `-legacy` anhängen. Liegen Fullchain
und Schlüssel schon als PEM vor: `sudo install -m 640 /home/<admin>/<host>.crt
/home/<admin>/<host>.key deploy/proxy/pki/` (Quellpfade vollständig angeben).

Prüfen:

```bash
sudo openssl x509 -in deploy/proxy/pki/$H.crt -noout -subject -ext subjectAltName -enddate
sudo grep -c "BEGIN CERTIFICATE" deploy/proxy/pki/$H.crt            # 2 oder mehr (mit Kette)
sudo openssl x509 -in deploy/proxy/pki/$H.crt -noout -pubkey | openssl md5
sudo openssl pkey -in deploy/proxy/pki/$H.key -pubout | openssl md5  # muss gleich sein
```

**Agent-Gateway:** eigene Agent-CA, Server- und Agent-Zertifikate (`deploy/agent-gateway/README.md`):

```bash
cd deploy/agent-gateway
sudo ./make-certs.sh ca
sudo ./make-certs.sh server automato.int.example.ch   # = AUTOMATO_AGENT_GATEWAY_HOST
sudo ./make-certs.sh agent <agent-name>               # je Agent, Name wie unter Collector-Agenten
cd ../..
```

Den Schlüssel der Agent-CA (`deploy/agent-gateway/pki/agents-ca.key`) nach dem Ausstellen in
den Tresor übernehmen und auf der VM löschen; er wird erst für das nächste Agent-Zertifikat
wieder gebraucht – auch für `server`, wenn sich `AUTOMATO_AGENT_GATEWAY_HOST` ändert (die
Agent-Zertifikate bleiben dann gültig, nur die Gateway-Adresse der Agenten ändert sich).

**Der eigenen CA vertrauen (VM und Container):** Stammen die Zertifikate von Keycloak, NetBox oder
Geräten von der eigenen CA, braucht die VM deren **Root** – und Orchestrator und Worker dieselbe
Liste. Die richtige Root ist der Aussteller (`i:`) des letzten Zertifikats der Kette; die
Adresse zum Herunterladen steht meist im Zertifikat (AIA, „CA Issuers“):

```bash
openssl s_client -connect auth.example.ch:443 -servername auth.example.ch -showcerts </dev/null 2>/dev/null | grep -E "^ *[0-9] s:|^ *i:"
openssl s_client -connect auth.example.ch:443 -servername auth.example.ch </dev/null 2>/dev/null | openssl x509 -noout -ext authorityInfoAccess
```

Steht in der Kette nur `0 s:…`, schickt der Server das Zwischenzertifikat nicht mit – dann ist
dessen Aussteller die gesuchte CA (bzw. die Fullchain auf dem Server ergänzen). Root aufnehmen
(PEM; DER vorher mit `openssl x509 -inform der -in ca.cer -out ca.crt` umwandeln):

```bash
openssl x509 -in /home/<admin>/root-ca.crt -noout -subject -issuer   # subject = issuer → Root
sudo install -m 644 /home/<admin>/root-ca.crt /usr/local/share/ca-certificates/interne-root-ca.crt
sudo update-ca-certificates                                           # „1 added“
```

Die Warnung „rehash: skipping ca-certificates.crt“ ist harmlos. Für die Container eine
`docker-compose.override.yml` im Repo-Ordner (gitignored, gilt nur für diese VM; Compose und die
Skripte lesen sie automatisch mit):

```bash
sudo tee docker-compose.override.yml >/dev/null <<'EOF'
services:
  orchestrator:
    environment:
      SSL_CERT_FILE: /etc/automato/host-ca.crt
    volumes:
      - /etc/ssl/certs/ca-certificates.crt:/etc/automato/host-ca.crt:ro
  collector:
    environment:
      SSL_CERT_FILE: /etc/automato/host-ca.crt
    volumes:
      - /etc/ssl/certs/ca-certificates.crt:/etc/automato/host-ca.crt:ro
EOF
```

TLS zwischen den Containern (interne CA, 6.4) bleibt davon unberührt. Nach jeder Änderung an den
CAs: `sudo update-ca-certificates` und `sudo docker compose up -d --force-recreate orchestrator
collector` – die eingebundene Datei liest ein laufender Container sonst nicht neu.

### 6.4 Erster Start und Härtung des Stacks

```bash
# P nur ohne COMPOSE_PROFILES in .env nötig – gilt nur in dieser Sitzung
P="--profile core --profile collectors --profile proxy --profile agent-gateway"   # wie in .env
sudo docker compose $P run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile
sudo deploy/aufbau.sh backup-schluessel     # age-Schlüssel für die Sicherung, Eintrag in .env
sudo docker compose $P up -d --build        # erster Start (Migrationen laufen im Dienst migrate)
sudo deploy/aufbau.sh db-rollen             # Orchestrator ohne DDL-Rechte (eigene DB-Rolle)
sudo deploy/aufbau.sh tls-intern            # TLS zwischen allen Containern (interne CA)
sudo deploy/aufbau.sh pruefen               # alles grün, Exit 0
ss -tlnp | grep -E ':(443|8443|8000|5173) '  # 8000/5173 nur auf 127.0.0.1
pgrep -c docker-proxy                       # 0 – sonst ist die Härtung aus 5.2 nicht aktiv
```

`caddy validate` meldet „Valid configuration“ nur, wenn Zertifikat und Schlüssel zusammenpassen
und lesbar sind. Läuft `pruefen` direkt nach einem Neustart („orchestrator (startet gerade)“,
`/health` antwortet nicht), nach etwa 30 Sekunden wiederholen. Den age-Schlüssel
(`/root/automato-backup.agekey`) **sofort** in den Tresor kopieren – ohne ihn ist keine Sicherung
lesbar. Danach `sudo augenrules --load` (Audit-Regeln aus 4.5 greifen jetzt). Spätere Updates
laufen mit `deploy/update.sh` (Schritt 9) – es sichert vorher und braucht dafür den laufenden
Stack.

### 6.5 Anmeldung (Keycloak)

Je Automato-Umgebung ein **eigener Realm** – die Mandanten-IDs gehören zur Datenbank der
Umgebung; Benutzer, OTP und erlaubte Adressen bleiben getrennt.

1. **Realm anlegen.** Neuer Keycloak: `deploy/keycloak/PROD-INSTALL.md` (Abschnitte 9a–9c,
   Import beim ersten Start). **Bestehender Keycloak:** Realm-Datei aus der Vorlage erzeugen und
   importieren – bestehende Realms bleiben unberührt:

   ```bash
   python3 deploy/keycloak/make_realm.py --realm automato --ui-url https://automato.int.example.ch \
     --display-name "Automato" > /tmp/realm-automato.json
   ```

   Admin-Konsole → Realm-Auswahl → **Create realm** → *Resource file* = die Datei → *Create*.
   Die Datei enthält Rollen, die Clients `automato-web`/`automato-api`, die Mapper (Audience,
   `tenant_ids`, `customer_ids`), die Härtung und `automato-admin` ohne Passwort – keine
   Secrets. Ist der Name schon vergeben (z. B. von Dev), einen eigenen wählen (`automato-prod`).
2. **Im Realm einmalig:**
   - *Realm settings → User profile → Create attribute* `tenant_ids` (Display name
     „Mandanten-IDs“, **Multivalued** an, *Who can edit* **nur Admin**), ebenso `customer_ids`
     („Kunden-IDs“) – ohne die Einträge lassen sich die Werte nicht setzen; Benutzer dürfen sie
     nie selbst ändern.
   - **OTP für alle:** *Authentication → Required actions → Configure OTP* → **Set as default
     action**; bestehende Benutzer unter *Users → … → Required user actions → Configure OTP*.
   - `automato-admin`: *Credentials → Set password* (Temporary an) – beim ersten Login neues
     Passwort und OTP.
   - Redirect-URI und Web-Origin von `automato-web` = `https://<AUTOMATO_UI_HOST>/*` bzw.
     `https://<AUTOMATO_UI_HOST>` (setzt `make_realm.py`; nach einer Namensänderung von Hand,
     dazu *Valid post logout redirect URIs*).
3. **Issuer = `KC_HOSTNAME` von Keycloak** – der Name, mit dem Keycloak die Tokens ausstellt
   (z. B. `https://auth.example.ch`), **nicht** der Admin-Name (`KC_HOSTNAME_ADMIN`, nur aus dem
   Admin-Netz – von der Automato-VM HTTP 403). Mit einem anderen Namen lehnt der Orchestrator jedes
   Token ab:

   ```ini
   AUTOMATO_OIDC_ISSUER=https://auth.example.ch/realms/automato
   AUTOMATO_OIDC_JWKS_URL=https://auth.example.ch/realms/automato/protocol/openid-connect/certs
   ```

4. **Backchannel direkt zur Keycloak-VM** (Server zu Server, ohne Umweg über die WAF): erst
   prüfen, dann in `docker-compose.override.yml` unter `orchestrator:` ergänzen:

   ```bash
   curl -sS -o /dev/null -w "%{http_code}\n" --resolve auth.example.ch:443:<KEYCLOAK-VM-IP> \
     https://auth.example.ch/realms/automato/protocol/openid-connect/certs      # 200
   ```

   ```yaml
       extra_hosts:
         - "auth.example.ch:<KEYCLOAK-VM-IP>"
   ```

   Danach `sudo docker compose up -d`, nach etwa 30 Sekunden `sudo deploy/aufbau.sh pruefen` →
   „Keycloak (JWKS)“ OK mit Schlüsseln. Sonst: `ConnectError` = Name, Weg oder Zertifikat (6.3:
   der eigenen CA vertrauen), `HTTP 403` = Admin-Name oder WAF, `HTTP 404` = Realm.
5. **Benutzer:** *Users → Create new user* (Benutzername, E-Mail, *Email verified* an) →
   *Credentials* (Temporary an) → *Role mapping → Assign role*:

   | Rolle | Darf | Mandanten-IDs |
   |---|---|---|
   | `admin` | alles, alle Mandanten | nicht nötig |
   | `operator` | lesen, Findings bearbeiten, Läufe | nötig |
   | `viewer` | nur lesen | nötig |
   | `customer` | nur die Doku der eigenen Kunden | Mandanten- **und** Kunden-IDs |

   Ohne Mandanten-IDs sehen `viewer` und `operator` nichts. Zuerst als `automato-admin` anmelden
   und die Mandanten anlegen, dann deren IDs bei den übrigen Benutzern eintragen (Tab *Details*).
6. Prüfen:

   ```bash
   curl -s http://127.0.0.1:8000/auth/config            # auth_mode "oidc"
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/rules   # 401 ohne Token
   ```

   (mit TLS intern `https://` und `--cacert deploy/tls/pki/client/ca.crt`). Im Browser
   `https://automato.int.example.ch` → Keycloak → Passwort + OTP → zurück. Antwortet die Seite
   nicht: Freigabe auf der Firewall vor der VM (4.3); „Zugriff nur aus freigegebenen Netzen“ (403):
   Netz in `AUTOMATO_UI_ALLOW` ergänzen, `sudo docker compose up -d --force-recreate proxy`.

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
- [ ] **SSH:** `sshd -T` → `permitrootlogin no`, `allowgroups automato-admins`,
      `maxauthtries 3`; `fail2ban-client status sshd` aktiv.
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
- [ ] **Container (1.0.0):** `docker compose ps` alle `healthy` bzw. `Up`; die Oberfläche ist
      der Produktions-Build (Browser-Konsole ohne „React DevTools“-Hinweis,
      `curl -s 127.0.0.1:5173/healthz` → `ok`); gehärtet:
      `docker inspect -f '{{.Name}} ro={{.HostConfig.ReadonlyRootfs}} drop={{.HostConfig.CapDrop}}'
      $(docker compose ps -q)` zeigt für Orchestrator, Collector, Web-UI, Proxy und Agent-Gateway
      `ro=true drop=[ALL]`.
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
- **Konten:** Betreiber verlassen das Team → Konto sperren bzw. entfernen (`sudo usermod -L
  <admin>`, `gpasswd -d <admin> automato-admins`, ggf. SSH-Schlüssel), Keycloak-Benutzer
  deaktivieren.

## 10. Bekannte Grenzen (Stand 1.0.0)

- Die Oberfläche läuft seit 1.0.0 als Produktions-Build (statische Dateien hinter Caddy im
  Container `web-ui`, nur auf `127.0.0.1`); Orchestrator, Migration, Collector, Web-UI, Proxy und
  Agent-Gateway sind gehärtet (`cap_drop: ALL`, `no-new-privileges`, Dateisystem nur lesend).
  Postgres, Redis und Keycloak laufen mit ihren eigenen Start-Skripten (Postgres und Redis mit
  `no-new-privileges`); ihre Härtung geschieht auf VM- und Docker-Ebene (dieses Runbook).
- Ressourcen-Grenzen je Dienst (CPU, Speicher) sind nicht gesetzt – sie hängen von der Grösse
  der VM und der Umgebung ab; bei Bedarf in `docker-compose.override.yml` (`mem_limit`, `cpus`).
- OTP für alle Benutzer ist ein Schritt in der Keycloak-Konsole (6.5), nicht im Realm-Import.
