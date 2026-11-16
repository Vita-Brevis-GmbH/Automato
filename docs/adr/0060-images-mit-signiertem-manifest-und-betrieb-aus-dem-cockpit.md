# ADR 0060 — Images mit signiertem Manifest und Betrieb aus dem Cockpit

## Status

**Angenommen** (2026-11-16), umgesetzt in 1.15.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-16: „Bitte weiter bis P5“ – Cockpit-Roadmap P3 „Aktualisieren“: Host-Agent mit
Positivliste (Stand, Sicherung, Update auf eine Version, Rückfall, Neustart), Ablauf Sicherung →
neue Version → Health → sonst Rückfall, ohne SSH vom Cockpit. Cockpit ADR 0002 E4: „Automato als
Images in GHCR, Versions-Tag, signiertes Manifest je Kanal; der Host-Agent nimmt nur Images,
deren Digest im Manifest steht.“ Verträge `betrieb/v1` (Einstiegspunkt je Produkt) und
`vitabrevis-manifest/v1` (Manifest), Host-Agent: Cockpit ADR 0015.

## Kontext

- Bisher aktualisiert `deploy/update.sh` von Hand auf der VM: Sicherung → `git pull` → Images
  bauen → `/health`, `--rollback` setzt Code und Datenbank zurück (ADR 0013). Jede VM baut selbst
  (Minuten, Zugang zu Paketquellen nötig), und was läuft, ist nur „gebaut aus Commit X“.
- Das Cockpit soll Automato auf allen Hosts aktualisieren – ohne SSH: Ein Host-Agent holt
  signierte Aufträge und ruft je Produkt **einen** Einstiegspunkt mit festen Unterbefehlen auf.
  Was ein Update konkret heisst, weiss nur das Produkt.
- Images sollen einmal gebaut (CI) und auf allen Hosts gleich laufen; was läuft, soll belegbar
  sein (Digest), und kein Host darf etwas annehmen, das nicht vom Herausgeber freigegeben ist.

## Entscheidung

1. **Images je Commit in GHCR** (`.github/workflows/images.yml`), nur nach **grüner CI desselben
   Commits**:
   - Push auf `main` → Kanal `latest`, Version `<VERSION>-latest.<sha7>`; Push auf den
     Entwicklungs-Branch → `dev`, `<VERSION>-dev.<sha7>`: als Job `images` am Ende von `ci.yml`
     (`needs` auf alle Jobs, keiner rot oder abgebrochen, nur wenn sich Code, Images oder Betrieb
     geändert haben).
   - Tag `v<X.Y.Z>` (= `VERSION`, auf genau diesem Commit) → `stable`, Version `X.Y.Z`, dazu ein
     Release `v<X.Y.Z>`; von Hand (`workflow_dispatch`) mit gewähltem Kanal, `stable` nur auf
     diesem Tag. Beide erst, wenn die API einen erfolgreichen CI-Lauf dieses Commits zeigt.
   - Eigene Dienste aus `docker compose config` (alle Profile, Dienste mit `build:`), nicht fest
     im Workflow; `ghcr.io/<owner klein>/automato-<dienst>:<version>` und `:<kanal>`, Labels
     `org.opencontainers.image.source`/`revision`/`version`, ohne Provenance-Anhang (ein
     einfaches Image-Manifest – der Digest ist genau das, was der Host holt).
   - Warum `needs` statt `workflow_run`: `workflow_run` feuert nur für Workflows auf dem
     Standard-Branch – der Kanal `dev` liefe erst nach dem Zusammenführen; mit `needs` gehören
     grüne CI und Images zum selben Lauf.
   - Ein langsamerer Lauf eines älteren Commits ersetzt nie einen neueren Stand im Kanal (ältere
     Version bzw. Vorfahre des veröffentlichten Commits).
2. **Manifest** `manifest.json` (`vitabrevis-manifest/v1`, `scripts/release_manifest.py`, nur
   Standardbibliothek): Produkt, Kanal, Version, Commit, Zeit und die Images je Dienst **nur per
   Digest**; deterministisch (feste Reihenfolge, Images sortiert). Im Release `kanal-<kanal>`
   (`latest`/`dev` als Vorabversion), je Build ersetzt.
3. **Signatur Ed25519** über die exakten Bytes (`manifest.sig`, Base64). Der private Schlüssel
   liegt nur als GitHub-Secret `MANIFEST_SIGNING_KEY` – im Lauf nur in einer Datei 0600 unter
   `$RUNNER_TEMP` für einen Schritt, danach gelöscht, nie ausgegeben. Ohne Secret: Images und
   Manifest ohne `manifest.sig`, Warnung im Lauf (Hosts lehnen ab). Der Workflow prüft die eigene
   Signatur und, wenn `deploy/release/herausgeber.pub` im Repo liegt, dass das Secret dazu passt.
   - **Warum ein Ed25519-Schlüssel als Secret statt Sigstore/cosign:** Geprüft wird auf dem Host
     mit `openssl pkeyutl -verify -rawin` (OpenSSL ab 3.0 ist auf jeder VM, `openssl` ist schon
     Voraussetzung) – kein zusätzliches Werkzeug, kein Netzweg zu Fulcio, Rekor oder TUF, offline
     prüfbar, im Cockpit mit Standardbibliothek und `openssl`. Gleiches Muster wie die signierten
     Agent-Updates (ADR 0013): Ed25519, privater Schlüssel ausserhalb des Ziels, Prüfung auf dem
     Ziel. Schlüssellose Signaturen (OIDC-Identität des Workflows) wären stärker gegen einen
     gestohlenen Schlüssel, brauchen aber Transparenzlog, Vertrauen in die Sigstore-Infrastruktur
     und eine Prüfrichtlinie je Host – für wenige Hosts mit fester Vertrauensbeziehung zu viel.
     Später als zusätzliche Signatur möglich, das Format bleibt.
4. **Vertrauenskette auf dem Host:** Herausgeber-Schlüssel ist der erste vorhandene von
   `/etc/automato/herausgeber.pub` (fest, empfohlen) und `deploy/release/herausgeber.pub` aus dem
   **bisherigen** Checkout. Geprüft wird mit diesem Schlüssel und dem Skript des bisherigen
   Checkouts, **bevor** etwas umgestellt wird; ohne Schlüssel lehnt der Host ab (Exit 3). Die
   Signatur deckt den Commit (git-Hash: Compose-Datei, Skripte, Migrationen) und die Digests (die
   Images). Ein neuer Schlüssel im Repo gilt erst nach einem Update, das selbst noch mit dem
   bisherigen signiert war (bzw. nach `update.sh`). Zugangsdaten zu GHCR kommen nie aus einem
   Auftrag (öffentliche Pakete oder ein vorhandenes `docker login ghcr.io`).
5. **Einstiegspunkt `deploy/betrieb.sh`** (Vertrag `betrieb/v1`): `stand`, `sichern`,
   `aktualisieren --version --manifest --signatur`, `zurueck`, `neustart`. Fortschritt auf
   stderr, auf stdout genau eine Zeile JSON (die Aktion läuft in einer Subshell mit stdout auf
   stderr – kein gestartetes Programm erbt die echte Ausgabe; bricht sie unerwartet ab, folgt
   trotzdem eine Antwort mit Exit 2); `meldung` nur feste Texte. Sperre `flock` auf
   `.betrieb.lock` (gilt auch für `update.sh`), belegt → Exit 4.
   - `aktualisieren`: Format, Produkt, Version, Signatur; älterer Kern → 3, gleiche Version → 0
     „schon auf …“ → keine lokalen Änderungen, Sicherung möglich → **Sicherung** wie `update.sh`
     → `git fetch` → Images per Digest holen (`docker pull`) → Stand für `zurueck` (Commit,
     Version, Image-Liste, Sicherung) → Commit losgelöst → gepinnte Images → `up -d --no-build`
     (Fremd-Images im festen Tag wie `update.sh`) → `/health` bis 5 Minuten → sonst selbst zurück
     (Commit, Images bzw. Bau, Datenbank aus der eben gemachten Sicherung) und Exit 1; scheitert
     auch das, Exit 2 mit dem nächsten Schritt.
   - Scheitern Sicherung, `git fetch` oder das Holen der Images, ist noch nichts umgestellt: Exit
     1 („nichts umgestellt, läuft auf …“) – der Host läuft auf dem alten Stand, wie der Vertrag
     Exit 1 beschreibt. Darum holt `betrieb.sh` die Images mit `docker pull` **vor** dem
     Umstellen, nicht wie im Vertrag skizziert mit `docker compose pull` danach.
   - `zurueck`: auf den Stand vor dem letzten erfolgreichen Update (Code bzw. Images und
     Datenbank aus der Sicherung jenes Updates), **einmal** (Vermerk im Stand); `neustart` ohne
     Bau und ohne neue Images, mit Health. Docker selbst aktualisiert nur `update.sh` bzw.
     `aufbau.sh docker-update` (Neustart des Docker-Dienstes).
6. **Gepinnte Images:** Die eigenen Dienste in `docker-compose.yml` haben
   `image: ${AUTOMATO_IMAGE_<DIENST>:-${COMPOSE_PROJECT_NAME:-automato}-<dienst>}` – ohne Variable
   der bisherige lokale Name, gebaut wird wie bisher. Die Werte stehen in `.automato-images.env`
   (gitignored, bewusst nicht in `.env`), eingelesen von `deploy/env.sh` (nur diese Datei zählt).
   Die lokalen Namen zeigen zusätzlich auf die gepinnten Images: ein `docker compose up -d` von
   Hand nimmt dieselben, nie ältere gebaute. `deploy/update.sh` bleibt der Bau aus git und
   entfernt die gepinnten Images; `update.sh --rollback` und `betrieb.sh zurueck` gehen auf
   denselben Stand (auch Images). Gemeinsames liegt in `deploy/lib-update.sh`.

Verworfen:

- **Weiter auf jedem Host aus git bauen** (auch für das Cockpit): langsam, Paketquellen auf jedem
  Host, kein Beleg, was läuft.
- **Tags statt Digests** im Manifest: Tags sind veränderlich.
- **Sigstore/cosign** (siehe 3.) und **GPG** (zusätzliches Werkzeug und Schlüsselbund).
- **Gepinnte Werte in `.env`:** vom Betrieb gepflegt und mit Secrets – keine maschinellen
  Änderungen dort.

## Folgen

- **Einmal durch den Anwender** (`deploy/release/README.md`): Schlüsselpaar erzeugen, Secret
  `MANIFEST_SIGNING_KEY`, öffentlichen Schlüssel nach `/etc/automato/herausgeber.pub` je Host
  und/oder ins Repo, GHCR-Pakete öffentlich stellen (oder `docker login ghcr.io` je Host), jeden
  Host einmal per `update.sh` auf 1.15.0 bringen (damit `betrieb.sh` da ist).
- Nach einem Update aus dem Cockpit ist der Checkout losgelöst; `update.sh` braucht dann wie nach
  einem Rückfall eine Angabe (`update.sh main` bzw. ein Tag).
- Images sind immer der Produktions-Build (`web-ui` mit Ziel `prod`); `AUTOMATO_UI_TARGET=dev`
  wirkt nur beim Bau aus git.
- `zurueck` setzt die Datenbank auf die Sicherung jenes Updates – Daten seit dem Update gehen
  verloren; sind die Sicherungen älter als die Aufbewahrung (`AUTOMATO_BACKUP_KEEP_DAYS`, 14
  Tage), ist kein Zurück mehr möglich (`zurueck_moeglich: false`).
- Innerhalb desselben Versionskerns gilt ein anderer Commit als neuer (Vertrag): ein älteres,
  gültig signiertes Manifest desselben Kerns (z. B. ein früherer `dev`-Stand) würde angenommen.
- **Nur-Lese-Regel unberührt:** `betrieb.sh` ändert nur den eigenen Host (git, Docker,
  Sicherung), nichts an Geräten oder Quellsystemen. R29.
