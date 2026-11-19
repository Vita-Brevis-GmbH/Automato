# ADR 0061 — Eigene Automato-Instanz je Kunde

## Status

**Angenommen** (2026-11-16), umgesetzt in 1.16.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-16: „Bitte weiter bis P5“ – Cockpit-Roadmap P4 „Ausrollen“: neue Instanz = neuer
Container-Stack auf einem bestehenden Host (Cockpit ADR 0002 E2), Produktvorlagen, Geheimnisse
erzeugt auf dem Host (das Cockpit kennt nur Verweise), Adresse und TLS über den Front-Proxy, erster
Benutzer über den Realm, Aktion `install` in der Positivliste, Löschen bzw. Archivieren mit
Sicherung. **Abnahme:** „Eine neue Automato-Instanz für einen Testkunden entsteht aus dem Cockpit
und ist über das Kundenportal erreichbar.“ Vertrag „Ausrollen“ (Entscheide 1–5) zwischen Cockpit
(ADR 0016) und Automato, dazu `betrieb/v1` und `vitabrevis-manifest/v1` (ADR 0060).

## Kontext

- Bisher gibt es **eine** Automato-Installation je VM: alle Kunden teilen sie, getrennt über
  Mandanten und das Kunden-Portal (ADR 0018, 0050). `<kunde>-doc.<domain>` zeigt im Front-Proxy auf
  die Oberfläche dieser Installation (`web-ui:5173`).
- Manche Kunden sollen eine **eigene** Automato-Instanz bekommen (eigene Datenbank, eigene
  Geheimnisse, eigene Version), ohne dass jemand von Hand eine VM aufbaut. Das Cockpit hat dafür
  den Host-Agenten mit einer Positivliste von Aktionen (ADR 0060): er kennt keine Produktbefehle,
  sondern ruft `deploy/betrieb.sh` mit festen Unterbefehlen auf.
- Die Hauptinstanz darf sich dadurch nicht ändern (Ports, Namen, Update-Weg), und mehrere
  Instanzen müssen nebeneinander auf einem Host laufen.

## Entscheidung

1. **Optional je Kunde, Standard bleibt die gemeinsame Instanz.** Wählt der Betreiber im Cockpit
   „eigene Automato-Instanz“, entsteht ein eigener Stack `automato-<instanz>`; sonst bleibt alles
   wie bisher. Instanzname `^[a-z][a-z0-9-]{1,30}$`, Ordner `<basisordner>/<instanz>` (Basisordner
   aus der Konfiguration des Agenten, z. B. `/opt/automato/instanzen`).
2. **Nur auf dem Host des Front-Proxys (bis zum Proxmox-Adapter).** P4 legt Instanzen auf der
   Automato-VM an, auf der auch der Front-Proxy läuft: er erreicht sie über ein gemeinsames
   Docker-Netz ohne Port auf dem Host, wie heute die Oberfläche der Hauptinstanz. Andere Hosts
   brauchen TLS zwischen den Hosts, Adressen und Firewall-Regeln – das kommt mit dem
   Proxmox-Adapter (eigene ADR). **Grenze:** `installieren` lehnt auf einem Host ohne das Netz
   `automato-front-instanzen` ab (Exit 3).
3. **`deploy/betrieb.sh installieren`** (im frischen Checkout, den der Agent geklont und auf den
   Commit des Manifests gestellt hat; neue Datei `deploy/lib-instanz.sh`):
   - prüft alle Eingaben (`--instanz`, `--version`, `--manifest`, `--signatur`, `--kunde`,
     `--domain`, `--issuer` nur `https://…/realms/<realm>`, optional `--kopplung` als Einmal-Code),
     den Ordner (heisst wie die Instanz, noch keine `.env`, keine lokalen Änderungen), Manifest und
     Signatur wie `aktualisieren` (Schlüssel `/etc/automato/herausgeber.pub`, sonst aus diesem
     Checkout), dass der Checkout genau auf dem Commit des Manifests steht, das Netz des
     Front-Proxys und dass es **keine Reste** einer früheren Instanz gleichen Namens gibt
     (Container, Volumes, Netze des Projekts – nur gemeldet, nie still gelöscht). Sonst Exit 3,
     nichts geändert.
   - schreibt die `.env` **auf dem Host** (0600) mit neuen Geheimnissen (`openssl rand` wie
     `aufbau.sh`/INSTALLATION-PROD: Datenbank, App-Rolle ohne DDL, internes Token, Audit-Schlüssel),
     `COMPOSE_PROJECT_NAME=automato-<instanz>`, `COMPOSE_FILE` mit `deploy/instanz/compose.yml`,
     `COMPOSE_PROFILES=core,collectors` (Datenbank, Redis, migrate, Orchestrator, Oberfläche, Worker
     – kein eigener Proxy, kein Agent-Gateway), `AUTOMATO_ENVIRONMENT=prod`, OIDC auf den Realm des
     Kunden (`--issuer`, Client `automato-web`, Audience `automato-api`, JWKS unter dem Issuer),
     CORS auf `https://<kunde>-doc.<domain>`, Sicherung mit der age-Identität des Hosts
     (`$HOME/automato-backup.agekey` wie `aufbau.sh backup-schluessel`, angelegt, wenn es keine
     gibt) nach `/var/backups/automato/instanzen/<instanz>`. Das Cockpit sieht kein Geheimnis.
   - holt die Images nur per Digest aus dem Manifest (nie bauen), startet, prüft Health bis 5
     Minuten und hinterlegt danach den Einmal-Code der Kopplung (ADR 0057) – im Container von stdin
     (`automato cockpit-kopplung --hinterlegen`, nur der Hash, 15 Minuten, einmal, 5 Fehlversuche),
     nie als Argument und nie in einer Ausgabe. Das Cockpit löst ihn danach über
     `https://<kunde>-doc.<domain>/api/kopplung` ein.
   - Scheitert ein Schritt nach dem Schreiben der `.env`, räumt `installieren` selbst auf: `down -v`
     und alles mit dem Label des Projekts (nur dieses Projekts), lokale Namen, `.env` – Exit 1
     („Instanz wieder entfernt, nichts installiert“); gelingt das Aufräumen nicht, Exit 2 mit dem
     Befehl, der die Reste zeigt. Den Ordner entfernt der Agent (er war neu).
   - Antwort wie `stand` plus `instanz` (`name`, `projekt`, `web_ui` =
     `automato-<instanz>-web-ui:5173`, `domain`, zusätzlich `kunde` und `adresse`). In einer
     Instanz gehen danach `stand`, `sichern`, `aktualisieren`, `zurueck`, `neustart` wie in der
     Hauptinstanz; ihre Antworten nennen ebenfalls `instanz`.
4. **`deploy/betrieb.sh entfernen --ja`** nur in einer Instanz (`AUTOMATO_INSTANZ`, `COMPOSE_FILE`
   der Instanz, Ordner heisst wie die Instanz, Compose-Projekt `automato-<instanz>` – sonst Exit 3;
   die Hauptinstanz wird nie entfernt): verschlüsselte Sicherung (startet die Instanz dafür, wenn sie
   nicht läuft) → **ohne gelungene Sicherung Exit 2, nichts gelöscht** → Container und Volumes nur
   dieses Projekts → Ordner nach `<basisordner>/archiv/<instanz>-<zeit>` (0700; `.env` bleibt dort,
   ohne sie ist die Sicherung des Audits nicht lesbar). Die Sicherung bleibt unter
   `/var/backups/automato/instanzen/<instanz>`.
5. **Mehrere Instanzen auf einem Host ohne feste Ports und Namen:** `deploy/instanz/compose.yml`
   (nur in einer Instanz geladen) setzt die Ports von Orchestrator und Oberfläche zurück
   (`ports: !reset []`, Compose ≥ 2.24), schaltet das Kunden-Portal ab
   (`AUTOMATO_FRONT_STATE_DIR` leer) und hängt einen **Anschluss** an: einen gehärteten Caddy
   (`caddy:2-alpine`, nur lesend, ohne Capabilities, `admin off`), der im Netz
   `automato-front-instanzen` unter `automato-<instanz>-web-ui:5173` annimmt und nur an die eigene
   Oberfläche weiterleitet (mit TLS intern der Instanz gegen deren CA). Health prüft
   `deploy/env.sh` in einer Instanz im Container (`python -m app.serve --check`) statt über Port
   8000 auf dem Host. Container- und Volume-Namen kommen aus dem Projektnamen.
   - **Warum ein Anschluss statt der Oberfläche direkt im Netz:** Docker gibt jedem Dienst in jedem
     seiner Netze auch den Namen des Dienstes (`web-ui`). Unter genau diesem Namen erreicht der
     Front-Proxy die Oberfläche der Hauptinstanz; hinge eine Instanz mit `web-ui` im selben Netz,
     könnte die Namensauflösung des Proxys bei ihr landen – ein Kunde sähe die Instanz eines
     anderen. Der Anschluss heisst dort `anschluss` (von niemandem benutzt) und
     `automato-<instanz>-web-ui` (eindeutig).
   - Das Netz `automato-front-instanzen` gehört dem Compose-Projekt der Hauptinstanz (wie
     `automato-front-tor`), ist `internal` (kein Weg nach aussen) und nur der Proxy hängt darin; die
     Instanz nimmt es als `external`. Es entsteht mit `docker compose up -d proxy` (≥ 1.16.0) –
     nicht von `front.sh` oder `aufbau.sh`: ein von Hand angelegtes Netz ohne die Labels von
     Compose lehnt Compose für das Projekt der Hauptinstanz ab.
6. **Freigaben und Front-Proxy:** `PUT /freigaben` nimmt je Kunde optional `automato_instanz`
   (gleiche Regel wie oben, eine Instanz gehört genau einem Kunden – doppelt: abgelehnt, alles oder
   nichts; Vertrag v1 bleibt). Gespeichert am Portal-Kunden (`portal_customer.automato_instanz`,
   Migration `0088_automato_instanz`), im Soll-Stand nur, wenn gesetzt. `front.sh sync` prüft den
   Namen noch einmal (Vertrauensgrenze, `deploy/front/instanzen.sh`) und leitet
   `<kunde>-doc.<domain>` dann **hinter dem Tor** (gleiche Reihenfolge: Sperre nach Absender,
   Zugriff, `/api/internal` 404, API ohne Tor, Tor, Ziel) an `automato-<instanz>-web-ui:5173`.
   Läuft der Anschluss nicht im Netz, antwortet die Adresse **503** („Automato-Instanz nicht
   erreichbar“) – nie ein anderes Ziel, auch nicht die Hauptinstanz. Die Kachel „Dokumentation“
   behält ihre Adresse. Da eine Instanz nach dem Soll-Stand kommen bzw. gehen kann, erzeugt `sync`
   (Timer, 10 Minuten) auch bei unverändertem Soll-Stand neu, sobald sich die laufenden Instanzen
   der Kunden ändern. Ohne Feld wie bisher.
7. **Inventar:** je Kunde `automato_instanz` (aus dem Kunden-Portal, sonst aus `topologie.json`,
   sonst null) und unter `ist.front.instanzen` die Instanzen, deren Anschluss der Front-Proxy auf
   der VM gefunden hat (`status.json`; null bei einem älteren `front.sh`). Läuft eine Instanz, die
   kein Kunde nutzt, meldet `front.sh pruefen` sie als Altlast – entfernt wird sie nur auf Auftrag
   aus dem Cockpit (ADR 0059).
8. **Rechte in der Instanz:** Der Realm des Kunden ist dort der Realm der Installation (wie
   `automato` für die Hauptinstanz) – Rollen `admin`/`operator`/`viewer` und `tenant_ids` kommen
   aus dem Token dieses Realms (Vorlage `make_realm.py`). Die Regeln der Hauptinstanz für
   Kunden-Realms (keine Rechte aus dem Token, ADR 0050/0058) gelten dort weiter; in der eigenen
   Instanz vergibt das Cockpit die Rollen im Realm (ADR 0058: Benutzer und Rollen aus dem Cockpit).

Verworfen:

- **Instanzen auf beliebigen Hosts schon in P4:** braucht TLS zwischen Hosts, Adressen im
  Front-Proxy und Firewall-Regeln je Instanz – mit dem Proxmox-Adapter.
- **Oberfläche der Instanz direkt im gemeinsamen Netz** bzw. Netz je Instanz am Proxy: beides gibt
  dem Proxy mehrere `web-ui` (siehe 5.).
- **Feste Ports je Instanz auf dem Host:** Kollisionen, Freigaben, an der Sperre des Proxys
  vorbei.
- **`docker-compose.override.yml` für die Instanz:** nicht versioniert – ein Update brächte die
  Regeln der Instanz nicht mit; `COMPOSE_FILE` mit einer Datei aus dem Commit schon.
- **Geheimnisse aus dem Cockpit:** das Cockpit kennt nur Verweise (Vertrag); die Instanz erzeugt
  sie selbst.

## Folgen

- **Vorlagen in P4:** `automato-instanz` vollständig; NetBox je Kunde entsteht weiter über den
  Agenten des NetBox-Hosts (ADR 0054/0056); **Magister-Plattform und Front-Proxy als ganze Hosts
  bleiben von Hand nach Runbook** (selten, brauchen DNS, Zertifikate und eine eigene VM – ein
  Automatismus lohnt sich erst mit dem Proxmox-Adapter).
- Ohne TLS intern läuft der Weg Front-Proxy → Anschluss in HTTP im internen Netz
  `automato-front-instanzen` (nur Proxy und Anschlüsse); innerhalb der Instanz gilt deren eigener
  Schalter `AUTOMATO_INTERNAL_TLS`. Wer TLS bis in die Instanz will, richtet in der Instanz
  `aufbau.sh tls-intern` ein (Anschluss → Oberfläche per HTTPS).
- Die Instanz muss den Issuer ihres Realms erreichen (JWKS); braucht Keycloak eine eigene CA bzw.
  eine interne Adresse, gehören die CA nach `<instanz>/deploy/trust/` und
  `AUTOMATO_OIDC_JWKS_URL` von Hand in ihre `.env` (Neustart).
- NetBox des Kunden aus der Instanz: der Weg über den Front-Proxy gilt für die Instanz nicht als
  „Automato selbst“ (das Netz der Instanzen zählt nicht zu den eigenen Netzen) – NetBox-Sync aus
  einer Instanz braucht eine Freigabe wie für einen Absender von aussen (offen).
- `docker compose down` der Hauptinstanz kann das Netz `automato-front-instanzen` nicht entfernen,
  solange Instanzen laufen (Meldung von Compose, wie bei `automato-front-tor`).
- **Nur-Lese-Regel unberührt:** `installieren`/`entfernen` ändern nur den eigenen Host (git,
  Docker, Sicherung), nichts an Geräten oder Quellsystemen. R30.
