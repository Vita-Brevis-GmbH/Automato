# ADR 0062 — Betrieb von NetBox-Host und Keycloak aus dem Cockpit

## Status

**Angenommen** (2026-11-17), umgesetzt in 1.17.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-17 (Rückmeldung nach P3–P5, Punkt 4): „Update und Neustart soll für alle
eingesetzt werden können, hier in Plattform“. Entscheid 4 der Runde: neue Produkte des
Host-Agenten mit festem Einstiegspunkt nach Vertrag `betrieb/v1` (ADR 0060) – `netbox-host` und
`keycloak` – neben `automato` und den Automato-Instanzen.

## Kontext

- Seit 1.15.0 ruft der Host-Agent des Cockpits auf der Automato-VM `deploy/betrieb.sh` auf
  (`stand`, `sichern`, `aktualisieren`, `zurueck`, `neustart`; eine Zeile JSON, Exit 0–4). Der
  Agent kennt keine Produktbefehle – was ein Update konkret heisst, weiss nur das Produkt.
- Der **NetBox-Host** (ADR 0044) trägt je Kunde eine NetBox (netbox-docker) hinter einem Proxy;
  das Werkzeug ist `deploy/netbox-host/netbox-host.sh` in einem Automato-Checkout
  (`/opt/netbox-host/automato`). Updates gingen bisher von Hand: `git pull`, `init`, Version in
  `host.conf` ändern, `update alle`.
- **Keycloak** läuft auf einer eigenen VM als Compose-Stack (Keycloak, Postgres, Caddy) in
  `/opt/automato-keycloak` (Ansible-Playbook bzw. `PROD-INSTALL.md`). Updates gingen von Hand:
  Image-Tag ändern, `pull`, `up -d`; die Sicherung war ein unverschlüsselter `pg_dump`.
- Beide Datenbanken ändern beim Start einer neueren Version ihr Schema (Django- bzw.
  Liquibase-Migrationen). Ein Rückfall ohne Datenbank aus der Sicherung geht nicht.

## Entscheidung

1. **Zwei neue Einstiegspunkte nach `betrieb/v1`** im Automato-Checkout des jeweiligen Hosts:
   `deploy/netbox-host/betrieb.sh` (Produkt `netbox-host`) und `deploy/keycloak/betrieb.sh`
   (Produkt `keycloak`). Gemeinsam (`deploy/lib-betrieb.sh`): Antwort genau eine Zeile JSON auf
   stdout (auch nach einem unerwarteten Abbruch, dann Exit 2), Fortschritt auf stderr, Sperre
   `<checkout>/.betrieb.lock` (belegt → 4), Prüfung von `--ref` wie im Vertrag, alles in Funktionen
   (der Checkout wechselt während `aktualisieren`). `deploy/betrieb.sh` von Automato bleibt
   unverändert.
2. **NetBox-Host – `aktualisieren --ref <git-ref>`** (Branch auf origin, Tag oder Commit; Branch
   wird wieder angehängt, sonst losgelöst; nur Commits, die `git fetch origin` bringt):
   - abgelehnt (Exit 3, nichts geändert): ungültige Ref, lokale Änderungen, älter als der laufende
     Stand (Vorfahre bzw. kleinere Version), Ziel ohne `deploy/netbox-host/betrieb.sh` bzw. ohne
     NetBox-Version, eine Instanz hätte danach eine **ältere** NetBox, `AGE_RECIPIENT` fehlt, und –
     wenn eine Instanz eine neue NetBox bekommt – keine passende age-Identität auf dem Host (ohne
     sie kein Rückfall der Datenbank), Health schon **vor** dem Update nicht ok (das Update könnte
     nur scheitern und Datenbanken zurückholen). Gleicher Commit → Exit 0 „schon auf …“.
   - Ablauf: Abgleich des Agenten anhalten (seine Sperre `$NBH_DIR/.sync.lock`) → `netbox-host.sh
     sichern alle` → Zustand für `zurueck` schreiben (vor dem Umschalten) → Checkout umschalten →
     `netbox-host.sh init` (Proxy-Vorlage, wie bisher nach `git pull`) → je Instanz mit anderer
     NetBox `netbox-host.sh update <kunde>` (mit seiner eigenen Sicherung direkt davor) → Health:
     Proxy läuft und jede Instanz antwortet im Container (`/login/`) bis 5 Minuten.
   - **Rückfall** (Exit 1): alter Checkout, je Instanz, deren NetBox sich geändert hat: NetBox
     stoppen, netbox-docker, `VERSION` und Override zurück, Sicherung zuerst probeweise entschlüsseln
     und entpacken, dann Schema leeren (`DROP SCHEMA public CASCADE` – sonst scheitert das Einspielen
     an Tabellen der neueren Version) und mit `netbox-host.sh wiederherstellen` einspielen
     (die Sicherung, die `update` direkt vor dem Umstellen gemacht hat); `init`; Health. Scheitert
     das: Exit 2, der Zustand bleibt für den Weg von Hand.
   - **NetBox-Version kommt mit dem Werkzeug:** neu `deploy/netbox-host/netbox-version.conf`
     (`NETBOX_DOCKER_TAG`, `NETBOX_VERSION`); `netbox-host.sh` nimmt sie, solange `host.conf` keine
     eigene setzt. Steht sie in `host.conf`, ist sie **festgehalten** – `aktualisieren` ändert dann
     nur das Werkzeug, `stand` sagt „(fest in host.conf)“. `host.conf.example` setzt sie nicht mehr.
   - `stand`: Version und Commit des Checkouts, NetBox-Version, Health (Proxy und Instanzen), ob
     `zurueck` geht, `letzte_sicherung` = Zeit, bis zu der **jede** Instanz gesichert ist.
     `sichern`: `sichern alle`. `neustart`: Proxy und alle Instanzen (`restart`, ohne neue Images).
   - `zurueck` (einmal): wie der Rückfall, mit dem Zustand des letzten erfolgreichen Updates.
     Instanzen, die seither entstanden sind, bleiben unverändert.
3. **Keycloak – `aktualisieren --version <X.Y.Z>`** (Keycloak-Version = Image-Tag):
   - Konfiguration `/etc/automato/keycloak-betrieb.conf` (Zeilen `NAME=Wert`, gelesen, nicht
     ausgeführt): `KC_DIR` (Vorgabe `/opt/automato-keycloak`), `AGE_RECIPIENT` (sonst der der
     Identität), `AGE_IDENTITY`, `BACKUP_DIR`, `BACKUP_KEEP_DAYS`. Ohne Stack
     (`<KC_DIR>/docker-compose.yml`) lehnt jede Aktion mit klarer Meldung ab (Exit 3).
   - Version: aus dem laufenden Container (`/opt/keycloak/version.txt`), sonst aus Tag bzw. Image.
     Health: alle Dienste des Stacks laufen und Keycloak meldet `/health/ready` mit HTTP 200 auf
     dem Management-Port (9000, ältere 8080) – im Container über `bash` (`/dev/tcp`, das Image hat
     kein curl).
   - Ablauf: nie älter (nur der Kern zählt; gleich → „schon auf …“), Health vorher ok (sonst
     Exit 3) → Image `<repo>:<X.Y.Z>`
     holen (Repository wie bisher konfiguriert) und **prüfen** (`version.txt` im Image =
     Auftrag, sonst Exit 3) → Sicherung → Version in **`<KC_DIR>/docker-compose.override.yml`**
     (Tag **mit Digest**, eigene Kopfzeile) → `up -d keycloak` → Health bis 5 Minuten → sonst
     Rückfall: Keycloak stoppen, Sicherung probeweise lesen, Schema leeren, einspielen, früheres
     Override (bzw. keines), starten, Health (Exit 1; sonst 2).
   - Warum ein Override: `docker-compose.yml` gehört dem Playbook (bzw. dem Runbook) – ein neuer
     Lauf würde einen geänderten Tag zurückdrehen (auf eine ältere Keycloak-Version, deren Schema
     nicht mehr passt). Das Override liest `docker compose` immer mit; `kc_image` gilt nur ohne
     diese Datei (das Playbook weist darauf hin). Ein von Hand angelegtes Override (ohne Kopfzeile)
     wird nie überschrieben (Exit 3).
   - `sichern`: `pg_dump` der Keycloak-Datenbank → gzip → age direkt in die Datei (nie Klartext auf
     der Platte; `.tmp`, dann umbenannt, 0600 in einem 0700-Ordner); älter als `BACKUP_KEEP_DAYS`
     wird gelöscht – die Sicherung für `zurueck` bleibt. `neustart`: `docker compose restart` +
     Health. `zurueck` (einmal): Image und Datenbank des Stands vor dem letzten Update.
4. **Einrichtung neuer VMs:** Das Keycloak-Playbook legt `git`, `age`, `python3`, die
   Konfiguration (Empfänger aus `kc_backup_age_recipient`, sonst neuer Schlüssel auf der VM), den
   Automato-Checkout (`kc_automato_repo`, nur anlegen) und eine tägliche Sicherung über
   `betrieb.sh sichern` an. Den Host-Agenten richtet der Betreiber im Cockpit ein (Produkte
   `netbox-host=/opt/netbox-host/automato` bzw. `keycloak=/opt/automato-keycloak/automato`).
5. **Zustand** für `zurueck` nur auf dem Host, ohne Geheimnisse: `$NBH_DIR/betrieb/zustand`
   (Commit, Versionen, Dateien der Sicherungen, Override je Instanz) bzw. `<KC_DIR>/.betrieb`
   (Version, Image, Sicherung, früheres Override); 0700. Nach einem Rückfall gilt wieder der Zustand
   des letzten erfolgreichen Updates.

Verworfen:

- **Update des NetBox-Hosts mit signiertem Manifest wie Automato:** es gibt keine eigenen Images,
  nur das Werkzeug aus git (wie Cockpit und Magister, Vertrag `--ref`) – NetBox selbst kommt aus
  den Images von netbox-docker.
- **Keycloak-Tag in `docker-compose.yml` bzw. `.env` ändern:** beide schreibt das Playbook neu.
- **Rückfall ohne Datenbank:** die neuere Version hat das Schema schon migriert; die ältere startet
  darauf nicht verlässlich.
- **Sicherung nur über `update` je Instanz:** der Vertrag verlangt eine Sicherung aller Instanzen
  vor dem Umstellen; `update` sichert zusätzlich direkt davor (die frischere nimmt der Rückfall).

## Folgen

- NetBox-Host und Keycloak erscheinen im Cockpit auf der Seite Plattform mit Stand, Sichern,
  Aktualisieren, Zurück und Neu starten, sobald dort ein Host-Agent mit dem Produkt läuft.
- Wer `NETBOX_DOCKER_TAG`/`NETBOX_VERSION` in `host.conf` stehen hat (Vorlage bis 1.16.0), behält
  die NetBox fest, bis er die Zeilen entfernt – `stand` zeigt es.
- Der automatische Rückfall braucht die age-Identität auf dem Host (NetBox-Host: wie bisher
  `/root/netbox-host-backup.agekey`; Keycloak: `AGE_IDENTITY`). Wer sie nur im Tresor hält, kann
  aus dem Cockpit sichern und neu starten, aber nicht aktualisieren.
- Ein Rückfall bzw. `zurueck` verwirft Daten seit der Sicherung jenes Updates (wie Automato).
- Grenze: Instanzen werden nacheinander aktualisiert, je bis 5 Minuten – bei vielen Instanzen kann
  das Zeitlimit des Agenten (30 Minuten) knapp werden; dann bleibt der Zustand für `zurueck`.
- **Nur-Lese-Regel unberührt:** die Einstiegspunkte ändern nur den eigenen Host (git, Docker,
  Sicherungen), nichts an Geräten oder Quellsystemen. R31.

## Nachtrag 1.18.0 (2026-11-17) – NetBox-Host: Stand aus dem Bundle des Cockpits

Mit den privaten Repos (ADR 0060 Nachtrag 1.18.0, Cockpit ADR 0019) gibt das Cockpit den Code als
git-Bundle mit:

- **`deploy/netbox-host/betrieb.sh aktualisieren --ref <ref> --bundle <datei> --commit <40 hex>`**
  (Erweiterung von `betrieb/v1`): `--bundle` und `--commit` nur zusammen, jede Angabe einmal. Der
  Stand kommt aus dem Bundle (`deploy/lib-bundle.sh`: `git bundle verify`, `git fetch <bundle>
  <commit>`, der Commit gehört zum Stand des Bundles), origin wird nie kontaktiert; Bundle defekt
  bzw. ohne den Commit → Exit 3, nichts geändert. Alles Weitere (nie älter, Sicherung, Health,
  Rückfall, `zurueck`) wie oben.
- **Branch oder losgelöst:** Commit (Hex-Präfix des Commits) und Tag (im Bundle bzw. schon lokal)
  → losgelöst; ein Branch, den das Bundle (`refs/heads/<ref>`) bzw. der Checkout kennt → lokaler
  Branch `git checkout -B <ref> <commit>` **ohne Upstream** (ein früherer wird entfernt; origin
  wird ja nicht mehr gelesen); unbekannt → losgelöst. Hat der lokale Branch Commits, die der Stand
  aus dem Cockpit nicht hat → Exit 3.
- Ohne `--bundle` wie bisher von origin; scheitert `git fetch`, nennt die Meldung den Weg über das
  Bundle (Host-Agent aktualisieren).
- Keycloak bleibt unverändert (Images, kein git).

## Nachtrag 1.18.2 (2026-11-17) – nur Nachfahren, Herkunft im Stand

Befund des Anwenders (Cockpit 0.8.0, Plattform → Updates): ein Host auf einem Entwicklungs-Branch
bekam die Vorgabe `main` als Update angeboten – eine andere Linie; erst der Rückfall hat es
abgefangen. Es darf gar nicht erst laufen:

- **NetBox-Host `aktualisieren --ref`** (mit und ohne Bundle): ist das Ziel kein Nachfahre des
  laufenden Commits (und nicht derselbe), Exit 3 „Abgelehnt: <ref> liegt auf einer anderen Linie
  als der laufende Stand <version> (<branch>) – nichts geändert“ – auch mit neuerer Version, vor
  Sicherung und Umschalten. Geprüft nur mit Objekten im Repo (`git merge-base --is-ancestor`,
  `nachfahre` in `deploy/lib-betrieb.sh`), mit Bundle nie origin; fehlt ein Commit, Exit 3 „nicht
  prüfbar“. Ältere Stände lehnt es wie bisher ab; zurück nur über `zurueck`.
- **`stand.herkunft`** bei NetBox-Host und Keycloak (rückwärts verträglich, wie `deploy/betrieb.sh`
  seit 1.18.1, ohne Kanal): `{"branch": <Branch des Checkouts>|null, "losgeloest": true|false}`
  (`herkunft_json` in `deploy/lib-betrieb.sh`). Das Cockpit nimmt den Branch als Vorgabe seiner
  Quelle und bietet nur an, was nach dem Vergleich bei GitHub vorne liegt (Cockpit ADR 0015
  Nachtrag „Updates mit Übersicht“).
