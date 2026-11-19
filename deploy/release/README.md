# Herausgeber-Schlüssel für Images und Manifest (ADR 0060)

Der Workflow `.github/workflows/images.yml` baut nach grüner CI die eigenen Images, legt sie in
GHCR ab und veröffentlicht je Kanal ein **Manifest** (`manifest.json`, Images nur per Digest) mit
einer **Ed25519-Signatur** (`manifest.sig`) im Release `kanal-<kanal>` des Repos. Ein Host nimmt
ein Update aus dem Cockpit (`deploy/betrieb.sh aktualisieren`, aufgerufen vom Host-Agenten) nur
mit gültiger Signatur an.

- Der **private** Schlüssel liegt nur als GitHub-Secret `MANIFEST_SIGNING_KEY` und offline im
  Tresor – nie im Repo, nie auf einem Host.
- Der **öffentliche** Schlüssel liegt auf jedem Host (`/etc/automato/herausgeber.pub`) und/oder
  hier im Repo (`deploy/release/herausgeber.pub`).

Bis der öffentliche Schlüssel da ist, liegt hier nur diese README: Hosts lehnen jedes Update aus
dem Cockpit ab (Exit 3 „Herausgeber-Schlüssel fehlt“), ohne Secret veröffentlicht der Workflow
das Manifest ohne `manifest.sig` (Warnung im Lauf) – auch das lehnen Hosts ab.

Die Befehle unten lassen sich so kopieren; wo `read` steht, fragt die Shell nach einer Eingabe.

## Kanäle

| Kanal | Quelle | Version | Release |
| --- | --- | --- | --- |
| `stable` | `deploy/release/stable` (abgenommen) bzw. Tag `v<X.Y.Z>` | `X.Y.Z` | `kanal-stable` |
| `latest` | jeder grüne Stand von `main` | `<VERSION>-latest.<n>.<sha7>` | `kanal-latest` (Vorabversion) |
| `dev` | jeder grüne Stand des Entwicklungs-Branches | `<VERSION>-dev.<n>.<sha7>` | `kanal-dev` (Vorabversion) |

- **stable ohne Tag** (1.18.1, ADR 0060 Nachtrag): Die Datei `deploy/release/stable` hier im Ordner
  nennt in einer Zeile die abgenommene Version und den vollen Commit, auf dem `VERSION` genau diese
  Version ist (Kommentarzeilen mit `#`):

  ```text
  1.17.0 315b05ff8e6ff73406f94c70c75af654e31671a5
  ```

  Bei jeder Abnahme auf die abgenommene Version setzen und pushen – mehr ist nicht zu tun. Der Job
  `stable` in `ci.yml` baut nach dem Push genau diesen Commit und legt Images (`:X.Y.Z`,
  `:stable`), `manifest.json` und `manifest.sig` in `kanal-stable` ab. Er tut das nur, wenn der
  Commit ein Vorfahre des gepushten Stands ist, dort `VERSION` = X.Y.Z steht, ein CI-Lauf dieses
  Commits erfolgreich war und `kanal-stable` noch keine gleiche oder neuere Version hat; ein
  Manifest ohne Signatur (Secret kam später) wird neu signiert ersetzt. Ohne Tag und ohne Release
  `v<X.Y.Z>`; ein Tag `v<X.Y.Z>` geht weiterhin und führt zum selben Kanal.
- **n** = `git rev-list --count <commit>` – wächst mit jedem Commit; im selben Kanal ist die
  Version mit dem grösseren n die neuere.
- Prüfen von Hand (im Repo): `python3 scripts/release_manifest.py freigabe --datei
  deploy/release/stable --repo . --stand HEAD`.

## 1. Schlüsselpaar erzeugen (einmal)

Auf einem vertrauenswürdigen Rechner mit OpenSSL ab 3.0 (`openssl version`), nicht auf einem Host:

```bash
umask 077
mkdir -p ~/automato-herausgeber && cd ~/automato-herausgeber
openssl genpkey -algorithm ed25519 -out manifest-signing.pem
openssl pkey -in manifest-signing.pem -pubout -out herausgeber.pub
cat herausgeber.pub   # öffentlich, darf überall hin
```

## 2. Privaten Schlüssel als GitHub-Secret ablegen

In der Oberfläche: Repository → Settings → Secrets and variables → Actions → New repository
secret, Name `MANIFEST_SIGNING_KEY`, Wert = der **ganze** Inhalt von `manifest-signing.pem`
(samt erster und letzter Zeile mit `BEGIN` bzw. `END`).

Oder mit der GitHub-CLI (`gh auth login` vorher):

```bash
cd ~/automato-herausgeber
read -r -p "Repository (Owner/Name): " REPO   # fragt nach
gh secret set MANIFEST_SIGNING_KEY --repo "$REPO" < manifest-signing.pem
```

Danach den privaten Schlüssel **offline** verwahren (Tresor, getrennt vom age-Schlüssel der
Sicherung) und vom Rechner entfernen:

```bash
shred -u ~/automato-herausgeber/manifest-signing.pem
```

## 3. Öffentlichen Schlüssel verteilen

Der Host nimmt den ersten vorhandenen: zuerst `/etc/automato/herausgeber.pub`, sonst
`deploy/release/herausgeber.pub` aus seinem **bisherigen** Checkout (gelesen, bevor er umstellt).

**Fest auf jedem Host** (empfohlen – kein Commit kann ihn ändern). `herausgeber.pub` vorher auf den
Host kopieren (z. B. mit `scp`), dann im Ordner der Datei:

```bash
sudo install -d -m 755 /etc/automato
sudo install -m 644 herausgeber.pub /etc/automato/herausgeber.pub
```

**Im Repo** (gilt für alle Hosts ohne eigenen Schlüssel; der Workflow prüft damit, dass das
Secret dazu passt, und bricht sonst ab):

```bash
cp ~/automato-herausgeber/herausgeber.pub deploy/release/herausgeber.pub
git add deploy/release/herausgeber.pub
git commit -m "Herausgeber-Schlüssel für Manifeste (ADR 0060)"
```

Einen Host ohne `/etc/automato/herausgeber.pub` erreicht der Schlüssel aus dem Repo erst mit einem
Checkout, der ihn enthält: einmal `sudo deploy/update.sh main` (Bau aus git; statt `main` auch ein
Tag) – danach geht jedes Update aus dem Cockpit.

## 4. Images in GHCR lesbar machen

Der erste Lauf legt die Pakete `automato-migrate`, `automato-orchestrator`, `automato-collector`
und `automato-web-ui` beim Owner des Repos an – **privat**. Entweder:

- **privat lassen** (1.18.0, ADR 0060 Nachtrag „Alles privat“): das Cockpit hat den GitHub-Zugang
  und gibt mit jedem Auftrag einen Pull-Zugang mit, versiegelt nur für diesen Host; der Agent
  legt ihn in ein temporäres `DOCKER_CONFIG` und löscht ihn danach. Auf dem Host nichts zu tun;
  oder
- **öffentlich stellen** (nur, solange auch das Repo öffentlich ist):
  GitHub → Owner → Packages → `automato-<dienst>` → Package settings → Change visibility →
  Public, je Paket einmal; oder
- auf **jedem Host** einmal mit einem Token nur mit `read:packages` anmelden (Docker legt es
  unverschlüsselt in `/root/.docker/config.json` ab):

  ```bash
  read -r -p "GitHub-Benutzer: " GH_USER   # fragt nach
  sudo docker login ghcr.io -u "$GH_USER"   # fragt nach dem Token
  ```

Zugangsdaten zu GHCR kommen aus einem Auftrag des Cockpits nur versiegelt für diesen Host und nur
für die Dauer des Auftrags (`betrieb.sh` setzt und nennt `DOCKER_CONFIG` nie).

## 5. Prüfen

Nach dem ersten Lauf (Actions → images) auf einem Rechner mit dem Repo:

```bash
read -r -p "Repository (Owner/Name): " REPO     # fragt nach
read -r -p "Kanal (dev, latest, stable): " KANAL   # fragt nach
curl -fsSLO "https://github.com/$REPO/releases/download/kanal-$KANAL/manifest.json"
curl -fsSLO "https://github.com/$REPO/releases/download/kanal-$KANAL/manifest.sig"
python3 scripts/release_manifest.py pruefen --manifest manifest.json --signatur manifest.sig \
  --schluessel deploy/release/herausgeber.pub
```

Ohne Repo, nur mit OpenSSL (so prüft auch das Cockpit):

```bash
base64 -d manifest.sig > manifest.sig.bin
openssl pkeyutl -verify -pubin -inkey herausgeber.pub -rawin -in manifest.json -sigfile manifest.sig.bin
```

Auf dem Host: `sudo deploy/betrieb.sh stand` (eine Zeile JSON; Fortschritt auf stderr).
Notweg ohne Cockpit mit den eben geholten Dateien, im Checkout des Hosts:

```bash
V=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')
sudo deploy/betrieb.sh aktualisieren --version "$V" --manifest manifest.json --signatur manifest.sig
```

## Schlüssel wechseln (auch bei Verdacht auf Kompromittierung)

1. Neues Paar wie in 1., Secret `MANIFEST_SIGNING_KEY` ersetzen (2.).
2. Neuen öffentlichen Schlüssel auf jeden Host nach `/etc/automato/herausgeber.pub` (3.) und ins
   Repo (`deploy/release/herausgeber.pub`, Commit).
3. Bis ein Host den neuen Schlüssel hat, lehnt er neue Manifeste ab und bleibt auf seinem Stand;
   mit dem neuen Schlüssel nimmt er keine Manifeste des alten mehr an. Ein Host nur mit dem
   Schlüssel aus dem Repo bekommt den neuen über `sudo deploy/update.sh main`.
