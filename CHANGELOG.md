# Changelog

Alle Pakete (Orchestrator, Collector-Agent, SoT-Gateway, Web-UI) tragen dieselbe Version aus
der Datei `VERSION` (SemVer). Neue Version setzen: `python3 scripts/version.py set X.Y.Z`
(aktualisiert `pyproject.toml`, `uv.lock`, `package.json`); die CI prüft mit
`python3 scripts/version.py check`, dass alle Stellen übereinstimmen. Patch = Korrekturen,
Minor = neue Funktionen. Die Agent-Version lautet `<Release>+<Build>` – der Build ist ein
Fingerabdruck der ausgelieferten Dateien und unterscheidet auch Stände ohne neue Release-Nummer.

## 0.46.0 – 2026-10-23

Konfiguration vor Gelerntem überall (R12, Entscheid des Anwenders). Migration `0047`; der
Collector-Agent aktualisiert sich, danach APICs neu einlesen.

- **Reihenfolge der Zuordnung IP → EPG** (`store/epg_locate.py`):
  1. statischer Endpunkt (fvStCEp);
  2. Portgruppe der VM (vCenter);
  3. gelernter Endpunkt;
  4. EPG-Subnetz, dann BD-Subnetz.
  Bisher galt in der Weg-Suche zuerst der gelernte Endpunkt, und die Portgruppe der VM
  zählte dort gar nicht.
- **Weg-Suche:** Die EPG von Quelle bzw. Ziel folgt der Konfiguration und nennt die Herkunft,
  z. B. „EPG TE/AP/EG_a (Portgruppe der VM vm-a)“.
- **Regel-Liste (Suche nach einer IP):** Erfasst ein Contract die IP nur über ein gemeinsames
  Subnetz, gehört sie nach der Konfiguration aber zu einer anderen EPG, ist er „nicht möglich“
  mit Begründung.
- **Neuer Befund „EPG: gelernt ≠ konfiguriert“** (ACI / VMware): Das ACI hat eine IP in einer
  anderen EPG gelernt, als Portgruppe bzw. statischer Endpunkt vorgeben (gleiche VRF). Ein
  Befund je VM bzw. EPG, mit den IPs.
- **Collector:** liest statische Endpunkte (`fvStCEp`/`fvStIp`) und die Herkunft gelernter
  Endpunkte (`fvCEp.lcC`: static, vmm, learned). Alles nur lesend, aus dem bereits
  gelesenen Baum des Tenants.

## 0.45.1 – 2026-10-23

Korrektur: Scope der ACI-Contracts im ganzen Modell (Hinweis des Anwenders). Ohne Migration.

- **Was der Scope verbindet** (eine Stelle für alle Prüfungen, `aci_relevance.Classifier`):
  - AP: nur innerhalb des AP. vzAny steht für alle EPGs seiner VRF und verbindet mit einer
    EPG derselben VRF die EPGs ihres AP. Bisher fiel ein solcher Contract weg.
  - VRF: nur innerhalb der VRF.
  - Tenant: im Tenant, auch über mehrere VRFs.
  - Global: alle verbundenen EPGs.
  - Zwischen VRFs braucht es weiter geteilte Subnetze bzw. Route-Leaking (0.44.0).
- **Regel-Detail:** Beim Scope steht, was er verbindet, und wie viele Paare Consumer →
  Provider es gibt. EPGs ohne Gegenstelle im Scope tragen „ohne Gegenstelle im Scope“.
- **Regel-Liste:** Bei der Suche nach einer IP ist ein Contract „nicht möglich“, wenn sein
  Scope die EPG dieser IP mit keiner Gegenstelle verbindet, mit Begründung („Scope VRF: …
  liegen in verschiedenen VRFs“).
- **Neuer Befund „Contract: ohne Gegenstelle im Scope“** (Findings → Regeln): Warnung, wenn
  der Contract wegen des Scopes gar nichts verbindet; sonst Hinweis mit den EPGs, für die
  er keine Wirkung hat.
- Applikations-Doku, Weg-Suche, Shared Services und Verbindungen Ende zu Ende nutzen
  dieselbe Prüfung wie bisher.

## 0.45.0 – 2026-10-23

Verbindungen Ende zu Ende in der Applikations-Doku (Wunsch des Anwenders: eine Zeile je Weg,
wenn mehrere Firewalls und das ACI hintereinander stehen). Ohne Migration. ADR 0021 (Entwurf).

- **Eine Zeile je Verbindung:**
  - Neue Karte „Verbindungen Ende zu Ende“ über „Regeln & Contracts“. Je Verbindung (Quelle →
    Ziel, Dienst) der Weg über ACI und Firewalls wie in der Weg-Suche, je Knoten nach seinem
    Urteil eingefärbt, dazu das Gesamturteil.
  - Abgeleitet aus den Regeln der Doku: das System der Applikation mit seiner IP, die
    Gegenseite aus den Objekten der Regel bzw. den Gegenstellen des Contracts im Scope. Gleiche
    Verbindungen aus mehreren Regeln (Firewall, Contract) stehen einmal da.
  - Gegenstelle any, vzAny und Preferred Group bleiben Regeln ohne Verbindung.
- **Details je Hop:** die entscheidende Policy bzw. der Contract mit den Objekten (Quelle,
  Ziel, Dienste), die möglichen davor, Ein- und Ausgang, NAT und Leak zwischen VRFs.
- **Regel ↔ Weg:**
  - Je Regel der Doku: entscheidet, möglich, greift nicht (vorher entscheidet eine andere),
    nicht auf dem Weg (ihr Gerät liegt nicht auf dem Weg), nicht prüfbar (kein Weg bekannt).
  - **Abweichung**, wenn eine Regel erlaubt, aber der Weg sperrt bzw. sie nicht auf dem Weg
    liegt. Das ist der Fall „Regel zeigt Zugriff, Weg-Suche sperrt“. Abweichungen stehen zuerst,
    ein Filter zeigt nur sie und die Gesperrten.
- **Weg-Suche aus der Doku:** „In der Weg-Suche öffnen“ füllt Quelle, Ziel und Dienst aus und
  sucht gleich (`#wege?src=…&dst=…&service=…`).
- API `GET /applications/{id}/connections`; nicht für Kunden-Benutzer (zeigt die
  Infrastruktur).
- Intern: Weg-Suche und Regel-Prüfung laden Graph, Policies und Contracts für viele Wege nur
  einmal (`paths.trace(graph=, checks=, backups=)`, `policy_check.Checks`).

## 0.44.1 – 2026-10-23

Korrektur der Web-Oberfläche. Ohne Migration.

- **Listen mit Filtern:** Kam die Antwort einer älteren Anfrage nach der neueren (z. B. Filter
  gesetzt, bevor die ungefilterte Liste da war), überschrieb sie die gefilterte Liste. Jetzt
  zählt nur die Antwort zur letzten Anfrage – in Routen, BGP-Nachbarn und weiteren Reitern des
  Routings, Inventar, VMs, Portgruppen, Abgleich, NetBox-Sync und Kunden-Zugehörigkeit. Fiel
  in der CI als sporadisch fehlschlagender UI-Test auf.

## 0.44.0 – 2026-10-23

ACI zwischen VRFs und Tenants (Befund aus dem Test von 0.43.2: eine Regel zeigte in der
Applikations-Doku einen Zugriff auf ein System in einem anderen Tenant, die Weg-Suche sperrte
ihn). Migration `0046`; der Collector-Agent aktualisiert sich, danach die APICs neu einlesen.

- **Shared Services und Route-Leaking:**
  - Ein Contract zwischen EPGs verschiedener VRFs öffnet allein keinen Weg: das Fabric muss
    die Subnetze in die andere VRF leaken.
  - *Shared Service:* Das Subnetz ist geteilt (`fvSubnet.scope` mit `shared`, an der EPG
    bzw. ihrem BD), und ein Contract mit Scope `global` bzw. `tenant` verbindet die beiden
    VRFs.
  - *Route-Leaking* (`leakRoutes`, ACI 5.0+): Die VRF gibt das Subnetz an die andere VRF.
  - Neues Modul `store/aci_shared.py`; Weg-Suche und Applikations-Doku entscheiden damit
    gleich.
- **Weg-Suche:**
  - Liegt das Ziel in einer anderen VRF, die es in diese leakt, endet der Weg im Fabric:
    „Ziel im Fabric der VRF … – Shared Service …“ bzw. „Route-Leaking …“.
  - Der Hop zeigt „→ VRF …“. Der Contract wird mit der EPG des Ziels in dessen VRF geprüft.
  - Ohne Leak folgt der Weg wie bisher dem Routing der VRF (L3Out, Firewall).
- **Applikations-Doku:**
  - Ein Contract zwischen VRFs zählt nur mit dem Leak beider Seiten (Hin- und Rückweg).
  - Ohne Leak fehlt er in der Applikations-Doku. In der Doku je System steht er unter „nicht
    möglich“ mit der Begründung „kein geteiltes Subnetz … siehe Weg-Suche“.
  - Ohne Scope der Subnetze (Lauf vor 0.44.0) bleibt er „unbekannt“, bis neu eingelesen ist.
- **VRF eindeutig:**
  - Der Tenant der VRF kommt aus `fvRsCtx.tDn` (`epg.vrf_tenant`), z. B. eine VRF aus
    `common`.
  - vzAny vergleicht die aufgelöste VRF statt Name und Tenant.
- **Collector:** liest zusätzlich `fvSubnet.scope` der BDs und EPGs, den Tenant der VRF aus
  `tDn` und `leakRoutes` je VRF (`vrf_leaks`). Alles nur lesend.
- **UI:** Weg-Suche mit „→ VRF …“ und dem Grund am Hop; im EPG-Detail „geteilt“ an
  geteilten Subnetzen.

## 0.43.2 – 2026-10-23

Korrektur der Applikations-Doku (Befunde aus dem Test von 0.43.1). Ohne Migration.

- **Contracts nur mit den Beteiligten:**
  - Ein Contract (z. B. aus `common`) zeigt in der Applikation nur die eigenen EPGs bzw.
    VMs und die Gegenstellen, die über den Contract zugreifen können (Scope).
  - Bisher standen in der Regel-Liste alle Consumer bzw. Provider und im Regel-Detail jede
    EPG mit ihren VMs, bei Contracts aus `common` hunderte. Die Regel-Liste nennt jetzt dazu
    „von N im Contract“; das Detail blendet die übrigen aus, „alle zeigen“ holt sie zurück.
  - Je Treffer tragen die Daten die DNs der Beteiligten (`parties`).
- **Firewall mit VDOM:** Herkunft und Erklärung nennen bei Firewalls mit mehreren VDOMs den
  VDOM der Policy („fw-01 · VDOM kunde-a“), wie in Regeln & Contracts.

## 0.43.1 – 2026-10-23

Korrektur der Applikations-Doku (Befunde aus dem Test von 0.43.0: Regeln, bei denen nicht klar
war, warum sie zur Applikation gehören). Ohne Migration; der Collector-Agent aktualisiert sich.

- **Scope der Contracts:**
  - Ein Contract mit Scope VRF bzw. AP verbindet nur EPGs derselben VRF bzw. desselben AP. Das
    gilt auch, wenn er in `common` liegt und EPGs vieler Tenants ihn nutzen.
  - Consumer bzw. Provider ausserhalb des Scopes sind keine Gegenstelle mehr. Bleibt keine
    übrig, fehlt der Contract in der Doku.
  - Scope `tenant`: gleicher ACI-Tenant. Gleiche Prüfung in der Weg-Suche.
- **Firewall-Regeln einer EPG mit gemeinsamem BD:**
  - Teilt eine EPG ihren BD mit anderen EPGs, zählen Regeln zum BD-Subnetz nur, wenn sie das
    ganze Subnetz enthalten.
  - Ein Host bzw. Teilnetz darin zählt nur über die IPs der EPG selbst: ihre VMs
    (Portgruppe) und ihre Endpunkte. Bisher kam die Regel zum Host einer anderen EPG im
    gleichen BD dazu – sogar als „spezifisch“.
- **VMs:**
  - Die Portgruppe einer NIC wird über Name, VLAN und – beim Distributed Switch – den Switch
    bestimmt. Bisher nur über den Namen; eine gleichnamige Portgruppe mit anderem VLAN brachte
    eine fremde EPG.
  - Ist die Portgruppe mehrdeutig, gibt es keine EPG statt einer geratenen.
  - Der Collector liefert Standard-Portgruppen je Name und VLAN.
  - Gilt für Applikations-Doku, VM-Liste, Portgruppen, Kunden und NetBox-Sync.
- **Vorgabe vor Gelerntem (R12):** Für IPs an einer NIC mit EPG gilt die EPG der Portgruppe.
  Ein gelernter Endpunkt mit derselben IP in einer anderen EPG (alt, verschoben) bringt keine
  Contracts mehr. Das gilt auch in der Doku je System.
- **Applikations-Doku, Systeme:**
  - Je NIC steht die Portgruppe mit VLAN und EPG da, bei Zuordnung nur über das VLAN mit
    Hinweis.
  - Nennt der VM-Name ein anderes Projekt als das AP der EPG, steht ein Hinweis dabei, z. B.
    „Name nennt Projekt 000, die Portgruppe … gehört zur EPG …/AP_001/… (Projekt 001)“.
- Tests (Orchestrator, Collector, UI), Doku (BETRIEB, TESTS).

## 0.43.0 – 2026-10-23

Docker und Images aktuell halten (Anwender: „wie kann man den Docker gleich mit updaten,
damit er immer aktuell ist?“). Ohne Migration.

- **Images:** `deploy/update.sh` holt die Fremd-Images (Postgres, Redis, Keycloak, Caddy)
  und die Basis-Images der eigenen bei jedem Update neu (`pull --ignore-buildable`,
  `build --pull`). Es nimmt den neusten Stand im festen Tag, die Hauptversion bleibt in
  `docker-compose.yml`. Alte Image-Stände räumt es danach auf. Abschalten mit
  `AUTOMATO_PULL_IMAGES=0`; ist die Registry nicht erreichbar, baut es mit den vorhandenen.
- **Docker selbst:** `deploy/aufbau.sh pruefen` zeigt je installiertem Docker-Paket die
  Version und eine neuere aus der Paketliste. `deploy/aufbau.sh docker-update` hebt nur
  diese Pakete an und startet danach Automato und die lokale NetBox. Mit
  `AUTOMATO_DOCKER_UPDATE=1` in `.env` macht `deploy/update.sh` das bei jedem Update,
  nach der Sicherung. snap bzw. das Ubuntu-Paket `docker.io` werden erkannt.
- `--help` beider Skripte zeigt den ganzen Kopf. Doku (BETRIEB), Test mit Ersatz für
  apt-cache/apt-get/docker.

## 0.42.3 – 2026-10-23

Korrektur der Applikations-Doku (Befund: im Projekt eines Tenants standen Contracts anderer
Tenants, die weder Tenant noch VRF der Applikation betreffen). Ohne Migration.

- EPGs, die nur über die IP passen (gelernter Endpunkt, damit verknüpfter Server), zählen
  nur in den VRFs der Portgruppen der VM. Ohne Portgruppe gelten die VRFs der Applikation,
  sofern eine passt. Gleiche Adressen in anderen Tenants bzw. VRFs bringen keine fremden
  Contracts mehr.
- Externe EPGs anderer VRFs (z. B. 0.0.0.0/0) zählen für Systeme in einer EPG nicht mehr –
  in der Applikations-Doku und in der Doku je System.
- Jeder Treffer eines ACI-Contracts nennt die EPG, über die er gilt; der Filter „EPG“ zeigt
  die Contracts eines Systems nur über diese EPG.
- Test mit zwei weiteren Tenants (gleicher EPG-Name, gleiche IP, externe EPG 0.0.0.0/0).

## 0.42.2 – 2026-10-23

Korrektur der Prüfung (Befund nach dem Update auf 0.42.0). Ohne Migration.

- `deploy/aufbau.sh pruefen` meldet Container, die laufen, aber nicht in den Profilen von
  `deploy/update.sh` sind (`AUTOMATO_COMPOSE_PROFILES`) – sie blieben bei jedem Update auf
  altem Stand, hier der Collector. Abhilfe in `.env`:
  `AUTOMATO_COMPOSE_PROFILES=--profile core --profile collectors --profile proxy`.
- „Health starting“ direkt nach dem Start gilt nicht mehr als Warnung.
- Frühere ufw-Sperren, deren Ziele jetzt antworten, stehen nur noch als Hinweis da.
- Scheitert `docker compose ps` im NetBox-Ordner, steht die Meldung dabei (und ob NetBox
  trotzdem antwortet).

## 0.42.1 – 2026-10-23

Korrektur (Befund 0.42.0: `deploy/update.sh` brach in der Sicherung mit „service "netbox" has
neither an image nor a build context specified“ ab – die NetBox-Override-Datei lag im
Automato-Ordner). Ohne Migration.

- `deploy/update.sh` prüft vor der Sicherung, ob `docker compose` das Projekt lesen kann, und
  nennt bei einem Fehler die VM-spezifische `docker-compose.override.yml` als Ursache.
- `deploy/aufbau.sh pruefen`: neuer Abschnitt Compose-Konfiguration – Automato- und
  NetBox-Projekt gültig, sonst die Meldung mit Hinweis auf die Override-Datei.
- Doku: Fehlerbild in BETRIEB; Test für die ungültige Konfiguration.

## 0.42.0 – 2026-10-23

Sicherung einmal einrichten (Befund 0.41.0: `deploy/update.sh` brach ab – „AUTOMATO_BACKUP_AGE_
RECIPIENT fehlt“). Ohne Migration.

- **Einstellungen aus `.env`:** `deploy/update.sh`, `deploy/aufbau.sh`, `deploy/backup/backup.sh`
  und `restore.sh` lesen `AUTOMATO_BACKUP_AGE_RECIPIENT`, `…_AGE_IDENTITY`, `…_DIR`,
  `…_KEEP_DAYS`, `…_PLAIN` und `AUTOMATO_COMPOSE_PROFILES` auch aus `.env` (`deploy/env.sh`:
  nur bekannte Schlüssel, gelesen statt ausgeführt; die Umgebung geht vor). Ein Update
  vergisst damit keine Angabe.
- **`deploy/aufbau.sh backup-schluessel`:** legt die age-Identität an (bzw. nimmt die
  vorhandene, nur für root lesbar) und trägt Empfänger und Pfad in `.env` ein.
- **`deploy/aufbau.sh pruefen`:** neuer Abschnitt Sicherung – Empfänger, age, Identität für
  den Rückfall, Alter der letzten Sicherung.
- `deploy/update.sh` nennt ohne Empfänger gleich den Weg (`backup-schluessel`).
- Tests: Schlüssel einrichten, wiederholt aufrufen, Werte aus `.env`, Umgebung geht vor.

## 0.41.0 – 2026-10-23

Neustart-Regeln und NetBox im Aufbau-Skript (Befund 0.40.0: nach einem Neustart der VM liefen
die Automato-Container wieder, NetBox nicht – „ConnectTimeout“ beim Einlesen und im Sync).
Ohne Migration.

- **`restart: unless-stopped`** für alle Automato-Dienste in `docker-compose.yml` (postgres,
  redis, orchestrator, collector, web-ui, keycloak; proxy und agent-gateway hatten sie schon).
  Wirkt mit dem nächsten `deploy/update.sh` bzw. `docker compose up -d`.
- **NetBox-Setup:** `setup.sh` schreibt die Neustart-Regel für alle Dienste der vorhandenen
  netbox-docker-Version ins Override (nur Dienste aus `docker compose config --services` –
  ein unbekannter Dienst liesse `up` scheitern).
- **`deploy/aufbau.sh`:**
  - `pruefen` meldet zusätzlich, ob Docker beim Booten startet, welche Container keine
    Neustart-Regel haben und – bei einer NetBox auf derselben VM – ob ihre Container laufen
    und die Login-Seite antwortet (Hintergrunddienste wie `netbox-worker` nur als Warnung).
  - neu `netbox-start`: startet die lokale NetBox, setzt die Neustart-Regel
    (`docker update`) und wartet, bis sie antwortet – ohne `setup.sh` neu auszuführen (das
    würde neue Tokens erzeugen).
- Doku: Fehlerbild „NetBox aus nach Neustart“ (BETRIEB, NetBox-README).
- Tests: Aufbau-Skript mit NetBox aus, ohne Neustart-Regel, `netbox-start`.

## 0.40.0 – 2026-10-23

Verbindungen prüfen – im Aufbau-Skript und unter System (Anwender, Befund: ufw sperrte den Weg
vom Container zu NetBox). Ohne Migration.

- **System → Verbindungen:** „Jetzt prüfen“ vom Orchestrator-Container aus, auf dem Weg von
  Einlesen und NetBox-Sync:
  - Datenbank, Redis (Warteschlange zum zentralen Worker), Keycloak (JWKS bei OIDC),
  - jedes zentral gelesene Quellsystem (Name, TCP, bei NetBox `/api/status/` mit dem
    Lese-Token, nur lesend; Schreib-Token für den Sync hinterlegt?),
  - Agent-Systeme als Hinweis je Agent.

  Je Ziel die Schritte und die Abhilfe. Antwortet ein Dienst auf dem Docker-Host nicht, steht
  die schmale ufw-Regel zum Kopieren da (Container-Netz aus der Routing-Tabelle, Gateway-
  Adresse, Port). API `GET /system/connections` (admin), CLI `automato check-connections
  [--json]`.
- **`deploy/aufbau.sh`** (nach dem Muster von Magisters `plattform-aufbau.sh`): `pruefen`
  (Werkzeuge, Docker, Container, Health, Verbindungen aus dem Container, ufw aktiv und Sperren
  aus Docker-Netzen der letzten Stunde), `firewall [--anwenden]` (nötige ufw-Regeln zeigen bzw.
  setzen und erneut prüfen), `status`. `deploy/update.sh` ruft am Ende `pruefen` auf (Hinweis,
  kein Abbruch).
- Doku: Fehlerbild „NetBox ConnectTimeout“ mit der Ursache ufw und der Abhilfe.
- Tests: Verbindungen (Netz aus `/proc/net/route`, ufw-Regel, Agent-Hinweis, Rechte, Redis-PING,
  CLI), Aufbau-Skript mit Ersatz für `docker compose`, UI (Karte mit Regel zum Kopieren).

## 0.39.0 – 2026-10-23

Weg-Suche: ein Netz als Ganzes prüfen (Anwender: „ob das Netzwerk in die Richtung geroutet wird
und ob das Subnetz über die Firewalls offen ist“). Ohne Migration.

- **Netz als Quelle bzw. Ziel** (`10.10.0.0/23`, aus der Namenssuche oder direkt) statt einer
  Stellvertreter-Adresse:
  - **Routing:** die Route, die das ganze Netz enthält (längstes solches Präfix). Genauere
    Routen innerhalb des Netzes mit anderem Next-Hop stehen am Hop („Teile anders geroutet“)
    und als Hinweis oben.
  - **Firewall-Policies:** sicher nur, wenn ein Objekt das ganze Netz enthält (`10.10.0.0/23`
    in `10.10.0.0/16` = offen); nur teilweise erfasst = „möglich (Quelle/Ziel nur
    teilweise)“, das Urteil wird „abhängig“. Policy-Routen bzw. SD-WAN-Regeln ebenso.
  - **ACI:** EPG bzw. externe EPG über das Subnetz, das das ganze Netz enthält; nur Teile =
    Zuordnung nicht sicher.
  - Kein DNAT für ein Netz; eine Quelle muss ganz in einem angeschlossenen Netz liegen.
- `GET /routing/trace` und `GET /routing/lookup` nehmen auch ein Netz; die Namenssuche liefert
  für Netze das Netz selbst (`ip` = CIDR) statt einer geprüften Adresse.
- Tests: Route und Policy für ein Netz (Beispiel /23 in /16), nur teilweise, Netz über Firewall
  und ACI, Lookup; UI (Netz mit „nur teilweise“ und anders gerouteten Teilen).

## 0.38.0 – 2026-10-23

Weg-Suche mit Netzen, IPv4/IPv6-Paare im Rahmen, Applikationen (Anwender). Ohne Migration.

- **Weg-Suche – Netze und vollständige Trefferliste:** Die Namenssuche bei Quelle und Ziel
  findet neben Hosts auch **Netze**:
  - IPAM-Netze (Name, Beschreibung bzw. Anfang des Netzes),
  - Netz-Objekte der Firewalls (ohne 0.0.0.0/0),
  - Subnetze des Inventars (FortiGate-Interfaces, ACI-BD/-EPG über Tenant, AP, EPG, BD),
  - ein Netz direkt (`10.20.0.0/24`).
  Die Liste ist nach **Hosts** und **Netzen** gegliedert, mit Anzahl je Art und Umschalter
  (Alle/Hosts/Netze), zeigt „x von y“ und lädt mit **„Weitere laden“** alle Treffer
  seitenweise nach (statt der ersten 20). Auch der Anfang einer Adresse sucht (`10.20.0.`).
  Ein Netz wird mit **einer Adresse daraus** geprüft – der ersten, die keinem gelesenen
  Gerät gehört (Interface, Gateway); sie steht beim Feld und im Ergebnis
  („Netz 10.20.0.0/24 (geprüft mit 10.20.0.2)“). `GET /routing/resolve` antwortet mit
  `hits`, `total`, `counts`, `capped` (Parameter `type`, `limit`, `offset`).
- **IPv4/IPv6-Paare in „Regeln & Contracts“:** beide Regeln eines Paars stehen in einem
  **Rahmen** (oben und unten durchgezogen, dazwischen gestrichelt), je Paar abwechselnd
  getönt, mit der Marke **„Paar“** (Vorschlag: „Paar?“, gelb) – auch viele Paare
  nacheinander bleiben unterscheidbar.
- **Applikationen:** die Vorschläge aus der Namenskonvention stehen **unter** der Liste.
- **Applikations-Doku – Findings nach Tenant, AP, EPG filtern:** je Befund die EPGs, zu
  denen er gehört (Server/VM über ihre EPGs, Subnetz über seine EPG, Regel über die Systeme,
  die sie trifft); Auswahl Tenant → AP → EPG mit „x von y“.
- Tests: Netze und Seiten der Namenssuche, EPGs der Befunde, UI (Netze und Umschalter in der
  Weg-Suche, Paar-Rahmen, Lage der Vorschläge, Findings-Filter).

## 0.37.0 – 2026-10-23

Applikations-Doku: offene Regeln nur als Regel (Anwender). Ohne Migration.

- **Unspezifische Regeln** (any bzw. grosse Netze, vzAny, Preferred Group – typisch Zugriffe
  ins oder aus dem Internet bzw. MPLS) stehen nur als Regel mit ihrem Objekt (`any`,
  `N_srv16 (10.20.0.0/16)`) statt mit allen Systemen, die sie erfasst – in beiden Richtungen.
  Welche Systeme der Applikation sie nutzen können, zeigt der Knopf **„▸ N Systeme“**:
  - in „Regeln & Contracts“ unter Quelle bzw. Ziel,
  - in der Doku nach Kategorien in der Spalte „Server Name“ (`_systems` je Zeile),
  - im Markdown-Export als Anzahl statt der Liste.
- Spezifische Objekte (Server, Gruppe, EPG) nennen ihre Systeme weiter direkt.
- Tests: Kategorien mit Objekt und Systemliste, UI (Knopf in Regeln und Kategorien, öffnet
  nicht das Detail).

## 0.36.0 – 2026-10-23

Objekte, Namenskonvention, IPv4/IPv6-Paare und Kunden-Editor (Anwender, „Weitere Arbeiten“
Punkte 3–10). Mit Migration `0045` (Datenmigration: `FT_` im Profil „default“); für die
Angaben dynamischer Objekte die FortiGates neu einlesen (Agent aktualisiert sich selbst).

- **IPv4/IPv6-Paare zusammen:** In „Regeln & Contracts“ steht die IPv6-Regel eines Paars
  direkt unter ihrer IPv4-Regel (auch seitenweise), als zusammengehörend markiert – grün
  gleich/bestätigt, gelb Vorschlag („ähnlich“).
- **Typ-Filter für Objekte:** Auswahl nach dem Typ wie auf dem Gerät mit Anzahl, z. B.
  **dynamisch (SDN-Connector)** für die Objekte der ACI-Integration über den FortiManager.
  Der Collector liest dazu `sub-type`, `sdn`, `filter`, `obj-tag` dynamischer Adressen
  (Positivliste).
- **Standardobjekte des Herstellers** (FortiOS-Services wie `HTTPS`/`ALL`, ACI-Filter in
  `common`): zuunterst unter eigener Überschrift, gedämpft, „Standard“ statt grünem Haken,
  „Standardobjekte ausblenden“. Neuer Befund **„Standardobjekt verwendet“**
  (`predefined_service_used`), wenn eine erlaubende Regel eines verwendet (direkt bzw. über
  eine Gruppe).
- **Objekt öffnen** filtert auch die Liste dahinter auf genau dieses Objekt
  (`GET /objects?object_id=`).
- **Namenskonvention:**
  - `FT_` steht in „Objekt-Prefixe → Typ“ mit dem neuen Typ **„Service / ACI-Filter“** und
    ist dort pflegbar (bisher fest eingebaut); die Prüfung der Services und Filter nimmt die
    Präfixe von dort.
  - Die **Projektnummern** sind aus der Namenskonvention entfernt (ausgeblendet, Daten
    bleiben).
- **Kunden-Editor:** breite Maske, Felder in voller Breite, Werte ganz lesbar, kein
  Querscrollen.
- Tests: Paar-Reihenfolge (auch seitenweise), Standardobjekte (Reihenfolge, Ausblenden,
  Befund direkt und über Gruppe, nicht bei Deny), Typ-Filter und Anzahl, Objekt-Filter,
  eigenes Service-Präfix im Profil, Collector (SDN-Attribute); UI (Standardobjekte, Typ
  dynamisch, Objekt öffnen filtert, Paar zusammen, FT_ im Namensprofil ohne Projektnummern,
  Kunden-Editor ohne Querscrollen).

## 0.35.0 – 2026-10-23

Weg-Suche als eigene Ansicht und Dienste mit Objektnamen (Anwender, „Weitere Arbeiten“
Punkte 1 und 2). Ohne Migration; die Regel-Liste zeigt die Namen für FortiGate-Regeln nach dem
nächsten Lauf, für ACI-Filter sofort.

- **Weg-Suche** steht als eigener Punkt in der Navigation (`#wege`), nicht mehr als Reiter
  im Routing. Die Ansicht „Routing & Wege“ heisst jetzt **Routing**. „Als Anbindung erfassen“
  springt aus der Weg-Suche ins Routing (Reiter Anbindungen, vorausgefüllt).
- **Quelle und Ziel als Name:** IP oder Name bzw. FQDN. Vorschläge ab zwei Zeichen aus dem
  IPAM (DNS-Name, auch nur das erste Label), den Adressobjekten der Firewalls (auch ohne
  Präfix; FQDN-Objekte über das IPAM) und den VMs. Eindeutige Namen werden direkt aufgelöst,
  bei mehreren IPs fragt die Suche nach (`GET /routing/resolve?q=`).
- **Dienst als Objekt:** `FT_https` bzw. `Tenant/FT_https` (Service-Objekt der FortiGate bzw.
  ACI-Filter, Gruppen aufgelöst). Bei mehreren Diensten wird der erste geprüft, ein Hinweis
  nennt die übrigen.
- **ACI-Contracts auf dem Weg:** Je ACI-VRF-Hop die Zuordnung von Quelle und Ziel (EPG im
  Fabric bzw. externe EPG über das längste zuordnende Subnetz) und das Urteil: gleiche EPG
  erlaubt, Taboo sperrt zuerst, sonst ein Contract Consumer → Provider (direkt, vzAny, ESG,
  Preferred Group) mit passendem Filter; kein Contract = gesperrt. Das Gesamturteil zählt
  Firewalls und ACI-VRFs („erlaubt (1 Firewall, 1 ACI-VRF)“). `store/aci_check.py`.
- **Dienste mit Objektnamen** statt Werten: Regel-Liste, Regel-Detail (Werte darunter),
  Applikations-Doku (Spalte TCP/UDP) und Weg-Suche zeigen `FT_https` bzw. `HTTPS` wie auf der
  Firewall statt `tcp/443`; ACI-Filter ohne den Tenant davor. Ohne Objekt (Werte direkt in
  der Regel) bleiben die Werte.
- Tests: Contracts je VRF-Hop (externe EPG, Taboo, vzAny, gleiche EPG, mehrdeutige
  Zuordnung, ohne Quelle), Dienst als Objekt, Namenssuche, Dienstnamen in Liste und Weg; UI
  (Weg-Suche als Ansicht, Ziel als Name mit Vorschlag, mehrdeutiger Name, Dienst als Objekt).

## 0.34.0 – 2026-10-23

Befunde aus dem Test von 0.33.0. Ohne Migration; für die Management-Adresse die FortiGates
neu einlesen (Agent aktualisiert sich selbst).

- **NetBox nicht erreichbar:** neu **Quellsysteme → Prüfen** für zentral gelesene Systeme.
  Die Prüfung läuft vom Orchestrator aus in Sekunden: Name auflösen, TCP-Verbindung, bei
  NetBox `GET /api/status/` mit dem Lese-Token. Jeder Schritt zeigt einen Hinweis, z. B.
  Firewall/Docker-Netz bei keiner Antwort bzw. Port bei abgelehnter Verbindung
  (`POST /systems/{id}/check`). Der Verbindungsaufbau zu NetBox bricht nach 10 statt 30 s
  ab, die Meldung kommt nach rund 1,5 statt 3 Minuten. Die Meldungen nennen den Grund
  (ConnectTimeout = keine Antwort, ConnectError = abgelehnt bzw. Name unbekannt).
  Abhilfe-Weg in `docs/BETRIEB.md`.
- **Seitenkopf fixiert:** Titel und Reiter (Objekte, Routing) bleiben beim Scrollen stehen.
- **Geräte:** FortiGates haben eine Management-Adresse – die IPv4 des aktiven Interfaces mit
  HTTPS-Zugriff (`allowaccess`), Management-Interfaces zuerst.
- **Finding „Portgruppe: Name ≠ EPG“** (`portgroup_name_mismatch`): Portgruppe nach
  ACI-Schema (`Tenant|AP|EPG`), die nur über das VLAN einer anders benannten EPG zugeordnet
  ist. Die Zuordnung über das VLAN bleibt.
- **Routing:**
  - Spalte VRF an den Routen und ein Filter nach VRF bzw. Management-Tabelle
    (`GET /routing/routes?vrf=`, VRFs in `/routing/summary`).
  - Suche und System-Filter in BGP-Nachbarn (dazu Zustand), HA, Policy-Routen/SD-WAN
    (dazu Art) und L3Outs.
- **Applikations-Doku, Kategorien:**
  - Neue Spalten **Aktion** (Allow/Deny) und **Gerät** (Firewall mit VDOM bzw. APIC); die
    Spalte „Bezug“ entfällt.
  - **IPv4/IPv6-Paare** (gleich bzw. bestätigt) stehen als eine Zeile.
  - **Klick auf die Regel** öffnet sie im Detail, bei einem Paar je Familie.
  - **NAT nur aus der Regel:** Internet-Zugriff mit dem SNAT der Policy (ihr Pool bzw.
    „Interface-IP“) statt allen Pools des Mandanten. Zugriff aus dem Internet mit der VIP, auf
    die die Regel zeigt (externer DNS-Name aus dem IPAM). Sperrende Regeln haben kein NAT.
    Eine VIP zum Server macht eine Regel aus dem eigenen Netz nicht mehr zu „Zugriff aus dem
    Internet“.
- **Applikations-Doku ohne unmögliche Regeln:** Routingtechnisch unmögliche Treffer, z. B.
  intern → Internet mit `any` im Ziel, stehen nicht mehr in der Applikation. Sie sind auch
  nicht einblendbar, nur gezählt.
- **Kunden-Übersicht:** Die Zugehörigkeit verlinkt auf Regel, Objekt, VM, NAT, Netz bzw.
  Finding.
- **Roadmap:** „Jede Verbindung einmal, Ende zu Ende“ über Firewalls und ACI mit der
  Reihenfolge der Regeln (Applikationen ausbauen).
- Tests: Verbindungsprüfung, Management-Adresse, Portgruppen-Befund, VRF-Filter, NAT je Regel
  (Pool, VIP, Deny), Kategorien-Spalten, IPv4/IPv6-Paar, UI (Kategorien mit Link,
  Routing-Filter, Kunden-Links).

## 0.33.0 – 2026-10-22

Kunden-Benutzer mit serverseitiger Einschränkung (ADR 0018, Schritt 3). Ohne Migration.

- **Kunden-Benutzer** tragen in Keycloak die Rolle `customer` bzw. Kunden-IDs im Attribut
  `customer_ids` (Claim `AUTOMATO_OIDC_CUSTOMERS_CLAIM`). Sie sind nie Admin (auch nicht mit
  der Rolle `admin`), immer auf ihre Mandanten und Kunden eingeschränkt; ohne Kunden sehen sie
  nichts (fail-closed). Bestehende Benutzer ohne Attribut bleiben Betreuer.
- **Freigabeliste (fail-closed):** Kunden-Benutzer erreichen nur die Lese-Endpunkte der Doku
  (`auth/customer_routes.py`): Regeln, Findings (Triage eigener mit `operator`), NAT,
  Objekte, VMs, Portgruppen, Doku je System, Applikationen, ihre Kunden, Mandanten,
  Standorte und die Bezeichnungen der Quellsysteme. Alles andere – auch neue Endpunkte –
  ergibt 403: Quellsysteme, Agenten, Läufe, NetBox-Sync, Abgleich, Routing & Wege, Inventar,
  System, Verwaltung.
- **Einschränkung je Endpunkt:** Listen und Zähler auf den Ausschnitt; Details ausserhalb
  ergeben 404 (Regel, NAT, Objekt, Portgruppe, System, Applikation, Finding). Regel-Zahlen
  und Regel-Listen bei Objekten, NAT und in der Doku nennen nur die eigenen Regeln, Findings
  ohne Namen fremder Regeln (ADR 0018: Gegenstellen sichtbar, aber nur die eigenen Regeln).
- **Geteilter Teil präzisiert** (gilt auch für den Kunden-Filter der Betreuer): EPGs, Netze und
  Objekte des geteilten Teils zählen für alle Kunden; Regeln, NAT, Findings und VMs nur aus
  den eigenen Kunden – eine Regel von Kunde B zum geteilten DNS betrifft Kunde A nicht.
- **`GET /systems/labels`:** nur Name, Plattform, VDOMs und Verbund der Quellsysteme; für
  Kunden-Benutzer nur die Systeme ihrer Regeln, NAT bzw. Objekte und nur ihre VDOMs.
- **Lese-Audit** für Kunden-Benutzer immer an (`read.*` mit den Kunden-IDs), auch ohne
  `audit_reads` des Mandanten; neu auch für Objekte, VMs/Portgruppen und Kunden.
- **Web-UI:** Kunden-Benutzer sehen Übersicht (ohne Betriebszustand und Läufe), Regeln,
  Objekte (ohne Netzinventar), NAT, Applikationen (ohne Pflege) und Findings; keine
  Administration, kein Routing. Rolle „Kunde“ im Benutzermenü. Im dev-Modus lässt sich ein
  Kunden-Benutzer vorführen (`localStorage automato_dev_customer = "<Mandant>:<Kunden>"`).
- **Keycloak:** Rolle `customer` und Mapper `customer_ids` in `realm-automato.json`; für
  SAML-Broker Kunden-IDs und Rolle fest je IdP (`saml-mappers.example.json`), nie aus der
  Assertion. Anleitung in `deploy/keycloak/PROD-INSTALL.md` (9a/9b).
- Tests: Ausschnitt je Endpunkt (Listen, Details, Zähler, Triage), ohne Kunden/fremder
  Kunde/Admin-Rolle, **jeder Endpunkt der App** gegen die Freigabeliste, Audit, OIDC-Claims,
  UI als Kunden-Benutzer.

## 0.32.0 – 2026-10-22

Kunden-Filter in den Ansichten (ADR 0018, Schritt 2). Ohne Migration.

- **Umschalter „Kunde“** in der Kopfzeile (sobald die Umgebung Kunden hat): „alle“ bzw. ein
  Kunde, je Umgebung gemerkt. Gefiltert werden Übersicht und Navigation, Regeln & Contracts,
  Objekte, NAT, Findings, VMs und Portgruppen, Doku je System und Applikationen.
- Sichtbar ist der Ausschnitt des Kunden **samt geteiltem Teil** (z. B. Tenant common).
- Applikationen gehören über ihre Mitglieder dazu (AP mit einer EPG des Kunden, EPG bzw.
  Server des Kunden); Firewall-Objekte über ihre Werte bzw. die Regeln des Kunden.
- **API:** `customer_id` an `GET /rules`, `/rules/summary`, `/findings`, `/findings/summary`,
  `/nat`, `/objects`, `/vms`, `/portgroups`, `/appdoc/systems`, `/applications`; ein Kunde
  eines anderen Mandanten ergibt 404. Gefiltert wird per SQL (`store/customer_scope.py`).
- Tests (jede Liste mit Kunde und geteiltem Teil, Applikationen, fremder Kunde, UI).

## 0.31.0 – 2026-10-22

Kunden: Merkmale aus dem IPAM, NAT und Findings (ADR 0018). Ohne Migration.

- **Selektoren aus dem IPAM:** IPAM-Tenant (NetBox-Tenant), IPAM-VRF bzw. DDI-Netzansicht,
  IPAM-Tag bzw. Extensible Attribute (`key=value`). Netze, Bereiche und Adressen des
  Referenzstands mit diesem Merkmal zählen als Netze des Kunden – ein- und
  ausschliessend. Gelesen wird nur, was die IPAM-Adapter schon liefern (ADR 0002).
- **NAT:** VIPs bzw. SNAT-Pools, deren interne oder externe IP in einem Netz des Kunden
  liegt, gehören zum Kunden.
- **Findings** gehören dem Kunden über ihr Subjekt: Regel, Werte- bzw. Firewall-Objekt, VM,
  Portgruppe (Tenant im Namen) bzw. Subnetz des Kunden. Nach „Abgleich neu berechnen“ wird
  die Zugehörigkeit ebenfalls neu abgeleitet.
- Ansicht Kunden: Spalten NAT und Findings, Zugehörigkeit je Art.
- Tests (Tenant, VRF ohne Gross/klein, Tag, Attribut, Ausschluss, NAT über die interne IP,
  Finding über die Regel bzw. das Objekt).

## 0.30.0 – 2026-10-22

Kunden als Ausschnitt der Umgebung (ADR 0018, Schritt 1). Alembic `0044`.

- **Kunden mit Selektoren** (Administration → Kunden, Operator): statt Listen „gehört dazu,
  wenn …“.
  - ACI-Tenant, VRF, Application Profile, EPG, Bridge Domain; VDOM, Zone, Interface (je
    Firewall); Netz; Kürzel aus der Namenskonvention.
  - Je Selektor einschliessen oder ausschliessen, z. B. „Tenant TE_ka ohne AP TE_ka/AP_900“
    bzw. ein Netz ohne ein Teilnetz.
  - Ein Kunde kann der **geteilte Teil** der Umgebung sein (z. B. Tenant common).
- **Zugehörigkeit abgeleitet** – nach jedem Lauf, beim Speichern und nach dem Löschen eines
  Quellsystems, nie von Hand:
  - EPGs; Netze (Präfixe, Subnetze der EPGs, Netze an zugeordneten Interfaces);
    Werte-Objekte in diesen Netzen;
  - Regeln (Contracts mit einer EPG des Kunden; Firewall-Regeln im VDOM, an Zone bzw.
    Interface oder mit Quelle bzw. Ziel im Netz des Kunden, ohne `any`);
  - VMs (Portgruppe einer EPG, IP im Netz, Kürzel im Namen).
  - Ein Element darf mehreren Kunden gehören. Neue EPGs bzw. Netze gehören nach dem nächsten
    Lauf ohne Pflege dazu.
- **Vorschau** beim Bearbeiten (Anzahl und Beispiele je Art), **Zugehörigkeit je Art** mit
  Grund, **Vorschläge** je ACI-Tenant (Kürzel aus `TE_<kürzel>`, `common` als geteilter
  Teil) und **keinem Kunden zugeordnet** (EPGs, Regeln, VMs).
- **API:** `/customers` (CRUD, `preview`, `suggestions`, `unassigned`, `{id}/members`), Audit
  `customer.created|updated|deleted`.
- Messskript: Ableitung bei 3000 Regeln und 9500 Objekten rund 0,7 s.
- Tests (Selektoren, Ausschluss, Kürzel, neuer Lauf, Löschen, Rechte, UI).
- Noch nicht: Kunden-Filter in den Ansichten (Schritt 2), Kunden-Benutzer (Schritt 3).

## 0.29.0 – 2026-10-22

Verbund bzw. Session-Sync-Paar als ein Hop im Weg (ADR 0019 Punkt 4/5). Ohne Migration.

- **Ein Weg statt mehrerer:** Wege, die sich nur in den Firewalls eines Verbunds
  unterscheiden, sind ein Weg. Bisher standen sie als „redundant · Verbund“ nebeneinander.
  - Der Verbund ist darin ein Hop „Verbund Nord“. Je Firewall steht eine Zeile mit Eingang,
    Ausgang mit Next-Hop und Regeln.
  - Der Hop davor zeigt die Next-Hops zu beiden Firewalls; das Ende nennt beide („… bzw. …“).
  - ECMP über verschiedene Router bleibt je Weg; Backup-Wege werden genauso zusammengefasst.
- **Regeln:** Urteilen die Firewalls des Verbunds gleich, gilt das Urteil über alle. Sonst
  ist der Weg „abhängig“ mit „im Verbund verschieden – fw-a: …; fw-b: …“.
- **API:** `GET /routing/trace` mit `hop.parts` (je Firewall bzw. Variante) und
  `end.also`.
- Tests (ECMP in einen Verbund, vier WAN-Router an einem Verbund, verschiedene Urteile, UI),
  Beispieldaten der UI-Tests mit Verbund „Süd“.

## 0.28.0 – 2026-10-22

ACI-Contracts über externe EPGs nach der Zuordnung im ACI (ADR 0019 Punkt 6). Alembic `0043`.

- **Systeme ausserhalb des Fabrics** (z. B. hinter der Firewall) bekommen in der Doku je
  System und in der Applikations-Doku die Contracts der externen EPG, der das ACI ihre IP
  zuordnet. Bezug: „externe EPG L3O_fw|EN_srv (10.20.0.0/24)“.
- **Zuordnung wie im ACI:**
  - Es zählen Subnetze externer EPGs mit Scope `import-security` (APIC-Standard).
    Subnetze, die nur das Routing steuern (`export-rtctrl` …), ordnen nichts zu.
  - Je VRF gewinnt das längste passende Subnetz – über alle L3Outs. Contracts weniger
    genauer externer EPGs (z. B. 0.0.0.0/0) sind für diese IP „nicht möglich“, mit
    Begründung.
  - Eine IP im Fabric der VRF (BD-/EPG-Subnetz, Endpunkt) gehört zu ihrer EPG: Externe EPGs
    fehlen in ihrer Doku; in der Regel-Liste ist diese Seite „nicht möglich“. Liegt die IP auf
    der anderen Seite in einer EPG, bleibt der Contract sichtbar („unbekannt“).
  - 0.0.0.0/0 in mehreren L3Outs: möglich ist die EPG des L3Outs, über den die VRF die IP
    leitet. Die übrigen bleiben sichtbar („unbekannt“), nicht ausgeschlossen.
- **Regel-Liste:** Bei der Suche nach einer IP tragen auch Contracts die Relevanz
  („für <IP> nicht möglich“, ausblendbar wie in 0.26.0).
- **Collector (APIC):** liest `l3extSubnet.scope` mit (nur lesend, dieselben Objekte).
  Gespeichert in `epg_subnet.scope`; Regel-Detail zeigt „nur Routing“.
- Tests (Zuordnung, Gleichstand nach Routing, Fabric, Regel-Liste, Collector, UI), Doku
  (ADR 0019, BETRIEB, Lesevertrag).

## 0.27.0 – 2026-10-22

Inter-VDOM-Links im Weg (ADR 0019 Stufe 3). Ohne Migration.

- **Weg über VDOM-Links:** Geht eine Route ohne Next-Hop-IP über einen Inter-VDOM-Link,
  führt der Weg im VDOM am anderen Ende weiter („über VDOM-Link vl_root_dmz0 ↔
  vl_root_dmz1“). Bisher endete er dort als „angeschlossen“.
  - Paare: `<name>0` ↔ `<name>1` derselben FortiGate (vdom-link bzw. NPU-vlink), Enden in
    verschiedenen VDOMs. VLAN-Subinterfaces auf den Enden paaren sich über Parent und VLAN-ID.
  - Physische Ports in verschiedenen VDOMs sind keine Kante.
  - Hat der Link IPs, gilt wie bisher der Besitzer der Next-Hop-IP.
  - Regeln auf dem Weg prüfen den Link als Aus- bzw. Eingang der VDOMs.
- **Collector:** FortiGate-Interfaces vom Typ `vdom-link` tragen die Art `vdom-link` (bisher
  `virtual`).
- Doku: veralteter Hinweis „Transfernetz (vermutet)“ in BETRIEB korrigiert (gilt seit 0.17.1
  nicht mehr).

## 0.26.0 – 2026-10-22

Relevanz in der Regel-Liste (ADR 0019 Punkt 6). Ohne Migration.

- **Suche nach einer IP:** Ist die Suche in „Regeln & Contracts“ genau eine IP, prüft Automato
  jede gefundene Firewall-Regel gegen das Routing ihrer Firewall, wie in der Doku (0.24.0).
  - Ziel: Leitet die Firewall die IP über den Ausgang der Regel?
  - Quelle: Kommt Verkehr von der IP über den Eingang der Regel an?
  - Regeln, die die IP nicht sehen können, tragen „für <IP> nicht möglich“ mit Begründung,
    z. B. eine Regel für das DMZ-Netz am Ausgang internet.
  - „N routingtechnisch unmögliche ausblenden“ blendet sie aus (über alle Regeln, nicht nur
    die geladene Seite).
- **API:** `GET /rules?q=<IP>` mit `relevance` je Regel und `X-Impossible-Count`;
  `routable=true` blendet aus.
- Test-Firewall um eine Regel mit falschem Ausgang ergänzt.

## 0.25.0 – 2026-10-22

Gegenstelle `any` in der Doku (ADR 0019 Punkt 6). Konzeptbefund: `any` aus dem Internet wirkte
wie eine Liste aller Gegenstellen. Ohne Migration.

- **Statt „any“** nennen Doku je System und Applikations-Doku, was hinter Interface bzw. Zone
  der Regel auf der Seite der Gegenstelle liegt – nach dem Routing ihrer Firewall:
  - eine erfasste **Anbindung**, z. B. „all → Internet Provider (internet)“;
  - sonst die **Default-Route** über dieses Interface: „all → extern (Default-Route über
    wan)“;
  - sonst die **Netze dahinter** (angeschlossen und geroutet), z. B. „all → Netze hinter lan:
    10.10.0.0/24“ (die ersten drei, dann „+n“).
- **Kategorie:** Liegt hinter der Gegenstelle eine Internet-Anbindung, steht eine eingehende
  Regel unter „Zugriff aus dem Internet“ (2) und eine ausgehende unter „Internet Zugriff“ (1).
  Eingehend gilt das jetzt auch für Gegenstellen, die im IPAM als extern bekannt sind.
- Ohne gelesenes Routing bzw. bei `any` als Interface bleibt es bei „any“.

## 0.24.0 – 2026-10-22

Regeln nur, wo der Verkehr durchkommt (ADR 0019 Punkt 6, Relevanz in der Doku). Konzeptbefund:
Ohne Routing zeigte die Doku Zugriffe, die routingtechnisch nicht möglich sind. Ohne Migration.

- **Relevanz je Regel und System:** Für jede Firewall-Regel, die ein System erfasst – direkt,
  über ein Netz, eine Gruppe oder `any` –, prüft Automato das Routing ihrer Firewall bzw. ihres
  VDOMs.
  - *Ziel:* Leitet die Firewall den Server (bzw. das EPG-Subnetz) über den Ausgang der Regel
    (`dstintf`, Interface oder Zone)?
  - *Quelle:* Kommt Verkehr vom Server über den Eingang der Regel an (`srcintf`;
    symmetrisches Routing)?
  - Passt keines, ist der Treffer **routingtechnisch nicht möglich**, mit Begründung, z. B.
    „fw leitet 10.30.0.10 über dmz (Zone dmz) – die Regel gilt nur für den Ausgang internet“.
  - Im Zweifel bleibt er sichtbar (**unbekannt**): Routing nicht gelesen, keine Route,
    Interface bzw. Zone unbekannt, Policy-Route bzw. SD-WAN, die anders leiten kann. Regeln
    mit `any` als Interface sind immer möglich.
- **Doku je System:** Unmögliche Treffer fehlen in den 10 Kategorien und im Export. Sie stehen
  oben unter „Routingtechnisch nicht möglich“ (Regel, als Quelle bzw. Ziel, Begründung).
- **Applikations-Doku:**
  - Unmögliche Treffer sind ausgeblendet; „N routingtechnisch unmögliche einblenden“ zeigt
    sie, mit „nicht möglich“ in der Spalte Bezug und dem Grund in der Erklärung.
  - Kennzahlen, Kategorien und Export zählen nur Mögliche bzw. Unbekannte. Die Übersicht nennt
    „N nicht möglich“.
- **API:**
  - `GET /appdoc/systems/{name}` mit `impossible`;
  - Applikations-Doku mit `relevance`/`relevance_text` je Treffer und `summary.impossible`.
- **Beispieldaten der UI-Tests:** zusätzlich fw-08 (LAN, DMZ, Internet mit Routing).

## 0.23.0 – 2026-10-22

NAT auf dem Weg (ADR 0019 Stufe 3). Ohne Migration.

- **DNAT (VIP):** Ist das Ziel die externe IP einer VIP einer FortiGate auf dem Weg, geht der
  Weg ab diesem Hop zur **internen IP** weiter.
  - Erkannt werden einzelne IPs und Bereiche (Bereich auf Bereich mit gleichem Abstand) sowie
    das Interface der VIP (`extintf`, sonst jedes).
  - Bei einem Virtual Server stehen alle Real Server am Hop; der Weg folgt dem ersten.
  - Am Hop steht „DNAT VIP_x: extern → intern“, bei Port-Weiterleitung mit den Ports. Der
    nächste Hop zeigt das Ziel nach NAT; die Übersicht markiert den Hop mit „DNAT“.
- **Regeln am NAT-Hop:**
  - Für die Policy gilt das ursprüngliche Ziel, also die VIP.
  - Bei Port-Weiterleitung gilt der interne Port, wie auf der FortiGate. Ab dort ist auch der
    Dienst für die weiteren Firewalls übersetzt.
- **SNAT:** Erlaubt eine Policy mit NAT, ist die neue Quelle die erste Adresse des IP-Pools
  bzw. die IP des Ausgangs-Interfaces. Sie gilt für die Firewalls danach („SNAT → …“ in der
  Spalte „Regeln“). Ist die Adresse unbekannt, sind die Quellen danach nur „möglich“.
- **API:** `GET /routing/trace` mit `nat`, `dst` und `snat` je Hop.

## 0.22.0 – 2026-10-22

Regeln auf dem Weg (ADR 0019 Stufe 3, Regel-Relevanz je Hop, erster Teil). Ohne Migration.

- **Weg für IP** prüft je Firewall bzw. FortiGate-VDOM auf dem Weg die Policies – nur aus
  den gelesenen Daten.
  - Geprüft werden Eingang und Ausgang des Hops (Interface bzw. Zone, `any`), Quelle, Ziel und
    optional der **Dienst** (neues Feld, z. B. `tcp/443`, `udp/53`, `tcp`, `icmp`).
  - Die Reihenfolge ist die der Firewall: Die erste Policy, die sicher passt, entscheidet
    (**erlaubt** bzw. **gesperrt**). Passt keine, ist der Verkehr **implizit gesperrt**.
  - **Möglich** ist eine Policy, wenn keine Bedingung verletzt, aber eine nicht prüfbar ist:
    Quelle oder Interface unbekannt (Start gewählt), FQDN/ISDB/Geo, Benutzer/Gruppen,
    Zeitplan, Dienst nicht angegeben. Hat eine mögliche Policy davor eine andere Aktion, ist
    der Hop **abhängig** – mit dem Grund.
  - Abgeschaltete Policies zählen nicht; negierte Quelle/Ziel/Dienst werden umgekehrt.
- **Anzeige:**
  - Je Hop die Spalte „Regeln“ mit Urteil, entscheidender Policy und den möglichen davor;
    „Policy n“ öffnet die Regel.
  - Je Weg „Regeln: erlaubt / gesperrt an fw-x / abhängig an …“ in der Karte und in der
    Übersicht.
  - Ohne gelesene Policies heisst es „unbekannt“.
- **Grenzen:**
  - NAT (VIP, SNAT) ändert die Adressen auf dem Weg nicht.
  - ACI-Contracts werden auf dem Weg noch nicht geprüft.
- **API:** `GET /routing/trace?service=` mit `rules` je Hop und `policy` je Weg.

## 0.21.0 – 2026-10-22

Erkannte Anbindungen ausblenden (Alembic `0042`). Dev-Befunde 0.20.0.

- **Ausblenden:** Jeder Vorschlag unter „Erkannte Anbindungen“ hat neben „übernehmen“ den
  Knopf **ausblenden** (Operator, im Audit), wenn es keine Anbindung ist.
  - Ausgeblendet werden die offenen Router des Vorschlags, für alle Benutzer.
  - Kommt später ein neuer Router dazu, erscheint der Vorschlag wieder.
  - „N ausgeblendete Vorschläge“ zeigt sie; **wieder anzeigen** hebt das Ausblenden auf.
  - Die Wege ändern sich dadurch nicht.
- **Reihenfolge:** „Erkannte Anbindungen“ steht jetzt zu unterst, unter den erfassten
  Anbindungen.
- **Sprung zum Formular:** „übernehmen“, „ergänzen“, „ändern“, „erkannte Router übernehmen“
  und „Als Anbindung erfassen“ springen hoch zum Formular, der Name ist bereit zur Eingabe.
- **API:**
  - `POST /routing/exits/dismiss` und `/routing/exits/dismiss/undo` mit `ips`;
  - `GET /routing/exits/suggestions?dismissed=` mit `dismissed` je Vorschlag und Router.

## 0.20.0 – 2026-10-22

Erkannte Anbindungen nach aussen mit Redundanz, Anbindung für mehrere Firewalls, Länge der
Wege im Vergleich (Alembic `0041`). Dev-Befunde 0.19.0: Bei vier WAN-Routern mit BGP zeigte die
Anbindung nur den ersten Router; eine Anbindung kann auch den Session-Sync-Partner betreffen.

- **Erkannte Anbindungen** (Routing & Wege → Anbindungen): Automato sucht die Gegenstellen
  selbst, nur aus den gelesenen Daten.
  - Gegenstellen sind Next-Hops aus der Routing-Tabelle, aus BGP-Pfaden und aus eingeschalteten
    statischen Routen sowie BGP-Nachbarn, die keinem gelesenen Gerät gehören. Die
    Management-Tabelle zählt nicht mit.
  - Je Router: **aktiv** (Präfixe in der Routing-Tabelle), **Backup** (nur als BGP-Pfad bzw.
    statische Route) oder nur BGP-Nachbar; dazu AS und Zustand der BGP-Sitzung, Default-Route,
    Präfixe und je Firewall Interface und Zone. Namen kommen aus dem IPAM.
  - **Zusammengefasst** wird zu einem Vorschlag:
    - Router, die sich Firewalls teilen;
    - Router derselben aktiven Route (ECMP);
    - Router am selben Interface bzw. an derselben Zone;
    - BGP-Nachbarn mit derselben AS – je Firewall bzw. je Verbund und Session-Sync-Partner
      (FGSP).
  - Beispiel: vier WAN-Router mit BGP an beiden Firewalls eines Verbunds ergeben einen
    Vorschlag mit vier Routern, „2 aktiv (ECMP ×2) · 2 Backup · 2 Firewalls“.
  - **übernehmen** füllt das Formular mit allen Routern, Firewalls und Zonen sowie Name und Art
    (geraten aus Zone, Namen, Default-Route bzw. BGP). Deckt eine Anbindung schon einen Teil ab,
    heisst der Knopf **ergänzen** und erweitert diese.
  - **Als Anbindung erfassen** im Weg nimmt ebenfalls die ganze erkannte Anbindung, nicht mehr
    nur den einen Router.
- **Anbindung für mehrere Firewalls:** Statt einer Firewall sind mehrere wählbar, z. B. beide
  Session-Sync-Partner; ohne Wahl gilt sie für alle. Wird eine Firewall gelöscht, fällt sie
  aus ihren Anbindungen; galt eine Anbindung nur für sie, entfällt die Anbindung.
- **Formular:** Unter „Erkannte Router“ stehen alle Router der Anbindung mit Zustand zum
  Ankreuzen, dazu „alle Router übernehmen“.
- **Liste der Anbindungen:** Die neue Spalte „Erkannte Router · Redundanz“ zeigt alle Router,
  die die Anbindung abdeckt (über Next-Hop bzw. Interface/Zone, wie im Weg).
  - Router, die erkannt, aber nicht eingetragen sind, tragen den Hinweis „nicht eingetragen“;
    „N erkannte Router übernehmen“ trägt sie ein.
  - Eingetragene Router, die in keiner Route stehen, erscheinen als „nicht erkannt“.
- **Weg:** Endet ein Weg an einer Anbindung mit mehreren Routern, nennt das Ende den Router,
  z. B. „weiter über WAN Nord – Router 2 von 4“.
- **Länge der Wege:** Die Übersicht hat eine Spalte „Länge“ mit „gleich lang“ bzw. „+1 Hop“
  gegenüber dem kürzesten Weg mit Ziel; jede Karte trägt denselben Hinweis.
- **API:**
  - `GET /routing/exits/suggestions?covered=`;
  - `GET /routing/exits` mit `status` (Router, `missing`, `unseen`, `redundancy`);
  - `source_system_ids` statt `source_system_id`;
  - `GET /routing/trace` mit `extra` je Weg.

## 0.19.0 – 2026-10-22

Verbund ablehnen und redundante Wege (Alembic `0040`). Dev-Befunde 0.18.0; abgenommen:
Regeln paralleler Firewalls korrekt und nur einmal, keine Schleifen mehr.

- **Verbund ablehnen:** Neben „zusammenfassen“ gibt es **Ablehnen**: Die Firewalls sind nicht
  parallel und werden nicht mehr vorgeschlagen.
  - Im Regel-Detail löst **Verbund auflösen** einen Verbund wieder auf (Regeln wieder einzeln),
    ebenfalls ohne neuen Vorschlag.
  - „N abgelehnte Vorschläge“ zeigt die Ablehnungen; **wieder vorschlagen** hebt eine auf.
  - Ein von Hand gesetzter Verbund hebt die Ablehnung seiner Firewalls ebenfalls auf.
  - Alles nur Admin, im Audit.
- **Redundante Wege** (Weg für IP): neben den aktiven Wegen (ECMP, parallele Firewalls eines
  Verbunds) die **Backup-Wege** mit gleicher oder fast gleicher Länge.
  - Backup-Weg: eine Abweichung an einem Hop über einen BGP-Pfad bzw. eine eingeschaltete
    statische Route für dasselbe Präfix mit anderem Next-Hop, z. B. zweiter Provider oder
    schwebende Route mit höherer Distanz.
  - Länge: Auswahl „Backup: gleich lang / bis +1 Hop (Standard) / bis +2 Hops / ausblenden“.
    Längere Backup-Wege und solche ohne Ziel nennt ein Hinweis.
  - Neue **Übersicht** „Wege im Überblick“: Art (aktiv · ECMP, redundant · Verbund, Backup ab
    Knoten mit Grund), Knoten nacheinander, Next-Hops, Anzahl Hops, Ende. Dazu je Weg eine
    Karte, am abweichenden Hop „Backup“ mit Präfix und Grund.
- **API:**
  - `POST /systems/parallel-reject` (Firewalls bzw. `group`) und `/systems/parallel-reject/undo`;
  - `GET /systems/parallel-suggestions?include_rejected=`;
  - `GET /routing/trace?slack=` mit `kind` (active/backup), `deviation`, `parallel` je Weg.

## 0.18.0 – 2026-10-22

Parallele Firewalls und Anbindungen nach aussen (Alembic `0039`, ADR 0020, ADR 0019 Stufe 4
erster Teil). Dev-Befunde 0.17.0.

- **Parallele Firewalls (Verbund):** Firewalls mit denselben Regeln (z. B. zwei Cluster mit
  Session-Sync) bilden einen Verbund.
  - Gleiche Policies stehen nur einmal in Liste, Suche, Kennzahlen und Applikations-Doku. Führend
    ist die Firewall mit dem kleinsten Namen, dazu „+ fw-b“.
  - Gleich heisst: gleiche Policy-ID im selben VDOM (sonst gleicher Name) und gleicher Inhalt.
    Verglichen werden Quelle, Ziel, Dienst, Aktion, Interfaces, Status, Zeitplan, NAT,
    Schutzprofile, Logging und Name; uuid und Kommentar nicht.
  - **Findings** (Kategorie „Parallele Firewalls“): Policy abweichend (mit Feldern und Werten),
    Policy fehlt auf einer Firewall, gleichnamiges Objekt anders definiert.
  - Verbund setzen:
    - Vorschlag oben in „Regeln & Contracts“ bei Session-Sync (FGSP) bzw. wenn mindestens die
      Hälfte der Policies gleich ist; „zusammenfassen“ ist Admin.
    - Sonst im Quellsystem-Dialog (Feld „Verbund“).
  - Filter „Verbund: abweichend / fehlt“; mit Filter auf eine Firewall erscheinen deren Regeln
    vollständig. Im Regel-Detail stehen der Verbund und die Firewalls, auf denen die Regel gleich
    steht.
- **Anbindungen nach aussen** (Routing & Wege, Reiter „Anbindungen“, Operator): Internet, MPLS,
  WAN oder Partner von Hand erfassen.
  - Felder: über Next-Hop-IPs und/oder Interfaces/Zonen (für eine Firewall oder alle), Netze
    dahinter, manuelle Routen (Präfix → Firewall bzw. VRF eines anderen Standorts).
  - Der Weg endet an der Anbindung („weiter über Internet“) statt an „Next-Hop gehört keinem
    gelesenen Gerät“. Mit einer manuellen Route geht er weiter, z. B. über MPLS zur Firewall des
    anderen Standorts.
  - Endet ein Weg an einem unbekannten Next-Hop, gibt es „Als Anbindung erfassen“ mit Firewall,
    Interface und Next-Hop vorausgefüllt.
- **API:**
  - `GET /systems/parallel-suggestions`, `POST /systems/parallel-group`, `parallel_group` an
    `/systems`;
  - `GET /rules?parallel=differs|missing&twins=`, `parallel` an der Regel, `twins` im Detail;
  - `GET/POST/PUT/DELETE /routing/exits`.
- Nach dem Update ist nichts neu einzulesen. Der Verbund wirkt sofort, sobald er gesetzt ist.

## 0.17.1 – 2026-10-22

Korrekturen aus dem Test von 0.17.0 (keine Migration). Die parallelen Firewalls (Regeln
zusammenfassen, Abweichungen als Findings) und Anbindungen nach aussen (Internet, MPLS, WAN)
folgen mit 0.18.0.

- **Läufe robuster** (Dev-Befund: „nicht erreichbar … ConnectTimeout“, „Ergebnis-Upload
  fehlgeschlagen: … ReadTimeout“):
  - Lesen von FortiGate, APIC, DDI und NetBox wird bei Timeouts, abgebrochenen Verbindungen und
    502/503/504 bis zu dreimal wiederholt (nach 5, 15 und 30 s). Das ist gefahrlos, es wird nur
    gelesen.
  - Der Agent wartet bis zu 10 Minuten auf die Quittung des Ergebnis-Uploads (bisher 30 s;
    das Einspielen dauert mit mehreren Firewalls länger) und sendet bei Netzproblemen erneut.
  - Der Orchestrator spielt ein Ergebnis für einen schon abgeschlossenen Lauf nicht noch einmal
    ein, er quittiert dessen Stand. Ein zweiter Upload wartet, bis der erste fertig ist. Bisher
    konnte die Fehlermeldung „Ergebnis-Upload fehlgeschlagen“ einen eingespielten Lauf auf
    „failed“ setzen.
  - Teil-Uploads der Referenzquellen werden nur wiederholt, wenn sie den Orchestrator nicht
    erreicht haben (sonst gäbe es Doppelte).
- **VRF der FortiGate** (Dev-Befund: Default-Route in der Management-VRF): Routen gelten je VRF.
  Bisher ergaben die Default-Routen beider VRFs zusammen ein ECMP.
  - Die Weg-Suche folgt der VRF des Eingangs; ohne Eingang der Standard-VRF 0.
  - Routen über ein dediziertes Management-Interface sind eine eigene Tabelle und nie ein Weg
    für den Datenverkehr.
  - „Weg für IP“ (nur Ziel) zeigt weitere VRFs als eigene Zeile, in der Routen-Liste steht die
    VRF unter „Weitere Angaben“.
- **Keine Schleifen mehr über geratene Transfernetze:** Gehört der Next-Hop keinem gelesenen
  Gerät (z. B. dem Aussenrouter), endet der Weg dort. Bisher folgte er einer anderen Firewall
  am selben Netz, und zwei Firewalls am selben Aussenrouter ergaben „Schleife“. Sekundär-IPs
  der FortiGate-Interfaces zählen jetzt als Adressen der Firewall.
- Nach dem Update FortiGates neu einlesen (VRF, Sekundär-IPs).

## 0.17.0 – 2026-10-22

Weg Hop für Hop mit Startpunkt (ADR 0019 Stufe 3, erste Fassung), Bezeichnungen und Namen aus
dem IPAM (keine Migration). Dev-Befunde 0.16.0.

- **Weg für IP mit Quelle und Startpunkt:** Quelle (IP) und/oder Startpunkt (Firewall, VDOM
  bzw. ACI-VRF), dazu das Ziel. Der Weg beginnt am gewählten Startpunkt bzw. dort, wo die
  Quelle angeschlossen ist (angeschlossenes Netz, sonst EPG-/BD-Subnetz im ACI), und folgt Hop
  für Hop:
  - Eine Policy-Route bzw. SD-WAN-Regel, die sicher passt (Quelle, Ziel, Eingang als Subnetz
    bzw. Interface), geht vor. Über Adressobjekte bestimmte steht als „möglich“ dabei.
  - Sonst gilt das längste Präfix der Routing-Tabelle; ECMP verzweigt in mehrere Wege.
  - Der nächste Knoten ist der, dem der Next-Hop gehört (Interface-IP der FortiGate,
    SVI/Interface eines L3Outs). Sonst ist es ein Knoten mit diesem Transfernetz („vermutet“).
  - Alle Border-Leafs einer VRF sind ein Knoten (vier Anbindungen = ein Hop).
  - Je Hop: Eingang und Ausgang mit Zone, Route bzw. Policy-Route und Next-Hop mit Namen aus
    dem IPAM.
  - Ende: Ziel angeschlossen, Ziel im Fabric (EPG/BD), extern (Default-Route ohne gelesenen
    Nachbarn), Next-Hop ohne gelesenes Gerät, keine Route, Schleife.
  - Ohne Quelle und Start bleibt es bei der bisherigen Sicht: je Kontext die wirksame Route.
- **Bezeichnungen:** Eine FortiGate nur mit `root` erscheint als **Firewall** (ihr Name),
  sonst als „fw · VDOM x“. ACI erscheint als „leaf · VRF Tenant:VRF“. Das gilt für alle
  Routing-Reiter und den Kontext-Filter der Routen (jetzt mit dem Namen der Firewall; ACI-VRF
  über alle Border-Leafs). Auch Regeln, Objekte, NAT und Ports zeigen „VDOM root“ nur noch
  bei Firewalls mit weiteren VDOMs (`vdoms` je Quellsystem in `GET /systems`). Bisher reichte
  dafür irgendeine Firewall mit VDOMs auf der Seite.
- **Nachbarn mit Namen aus dem IPAM:** BGP-Nachbarn, Next-Hops, Policy-Routen-Gateways,
  Session-Sync-Peers, L3Out-Peers und die Next-Hops statischer L3Out-Routen zeigen den Namen
  aus DDI/NetBox. Ohne Namen zur IP erscheint das IPAM-Netz mit Beschreibung (z. B.
  „Transfer ISP B“). Dazu kommt, falls gelesen, der Knoten hinter der Adresse (z. B. die ACI-VRF
  hinter dem BGP-Nachbarn der Firewall). L3Out-Border-Leafs tragen ihren Namen statt
  `node-101`.
- **Korrektur:** SD-WAN-Regeln haben bei Quelle/Ziel Adressobjekte, keine Subnetze. Bisher
  fielen sie bei „Weg für IP“ deshalb weg. Jetzt stehen sie als „möglich“ dabei.
- **API:** `GET /routing/trace?dst=&src=&start=`. Die Routing-Antworten tragen `label` bzw.
  `display` und die IPAM-Namen (`names`, `network`). `GET /routing/summary` liefert die
  Knoten für den Startpunkt (`nodes`).

## 0.16.1 – 2026-10-22

Korrektur des Selbst-Updates (keine Migration). Dev-Befund 0.16.0: Das Update von 0.15.0 auf
0.16.0 brach mit „Startpruefung fehlgeschlagen … cannot import name 'selfupdate'“ ab. Nach
pip fehlte eine Datei im venv (vermutlich zwei Agent-Prozesse gleichzeitig – geplante
Aufgabe und ein Start von Hand – oder der Virenschutz). Der Agent lief mit der alten Version
weiter, wäre beim nächsten Start aber nicht mehr hochgekommen.

- **Nur ein Update gleichzeitig** je Installation (`update.lock`); eine Sperre älter als 30 min
  (Abbruch) wird übernommen.
- **Nach pip prüfen, ob alle Dateien der Wheels im venv stehen**; fehlt eine, einmal neu
  installieren. Fehlt sie danach immer noch, nennt die Meldung die Datei und rät, Virenschutz
  bzw. zweiten Agent-Prozess zu prüfen.
- **Rückfall:** Scheitert das Update, installiert der Agent die zuletzt guten Pakete wieder
  (`packages\` im Installationsordner, abgelegt von `install.ps1` und jedem erfolgreichen
  Update).
- **Selbstheilung beim Start:** `run-agent.ps1` prüft vor dem Start, ob der Agent importierbar
  ist, und installiert sonst die Pakete aus `packages\` neu.
- **Einmalig von Hand:** Ein Agent, dessen Update auf 0.16.0 so abgebrochen ist, startet nach
  dem Ende des laufenden Prozesses nicht mehr. Das Agent-Paket im Web-UI neu herunterladen und
  `install.ps1` in einer administrativen PowerShell ausführen (BETRIEB, Fehlerbehebung).

## 0.16.0 – 2026-10-22

Routing, HA und L3Out lesen (ADR 0019 Stufe 2, Alembic `0038`); Agent aktualisiert sich vor
dem Lauf. Der Test gegen die echten Systeme steht noch aus.

- **Erster Lauf nach einem Update mit der neuen Version** (Dev-Befund 0.15.0: es brauchte zwei
  Läufe): Ein Agent mit alter Version bekommt neue Läufe erst nach seinem Update. Ab dieser
  Version aktualisiert er sich dann sofort statt nach bis zu 5 Minuten. Wartet ein Lauf
  länger als 10 Minuten (Update scheitert, Selbst-Update aus), holt ihn auch die alte Version.
- **FortiGate je VDOM** (nur lesend):
  - wirksame Routing-Tabelle IPv4/IPv6, ECMP als mehrere Next-Hops;
  - alle BGP-Pfade, auch nicht beste (Backup-Wege), und die BGP-Nachbarn mit Zustand;
  - statische Routen, auch inaktive bzw. mit höherer Distanz;
  - Policy-Routen und SD-WAN-Regeln;
  - global: HA-Cluster (Modus, Gruppe, Mitglieder mit Rolle, Session-Pickup) und
    Session-Sync zu anderen Clustern (FGSP).
- **ACI:** L3Outs mit Border-Leafs (Router-ID, statische Routen), Interfaces bzw. SVIs (vPC-Seiten,
  Encap/VLAN, Adressen), BGP-Peers und externen EPGs; je Border-Leaf und VRF die
  Routing-Tabelle und die BGP-Nachbarn.
- **Keine Secrets:** Felder werden per Positivliste übernommen – das HA-Passwort, der
  FGSP-Schlüssel und das Passwort der ACI-BGP-Peers kommen nie mit. Die BGP-Konfiguration der
  FortiGate wird nicht gelesen (nur der Monitor).
- **Was nicht lesbar ist** (Profil ohne Recht), steht in der Lauf-Meldung („Routing nicht
  lesbar: …“) und je Kontext in der neuen Ansicht.
- **Neue Ansicht „Routing & Wege“:**
  - gelesene Kontexte mit Anzahl Routen und dem, was fehlt;
  - **Weg für IP:** je Kontext die wirksame Route (längstes Präfix) mit Next-Hops,
    Interface und Zone, dazu Policy-Routen bzw. SD-WAN-Regeln, die vorgehen (sicher bzw.
    möglich);
  - Routen (wirksam, BGP-Pfade, statisch; Suche, „enthält IP“), BGP-Nachbarn, HA &
    Session-Sync, Policy-Routen & SD-WAN, L3Outs.
- **API:** `GET /routing/summary`, `/routing/routes`, `/routing/lookup?ip=`, `/routing/peers`,
  `/routing/ha`, `/routing/policy-routes`, `/routing/l3outs`.
- Nach dem Update FortiGates und APIC einmal neu einlesen.

## 0.15.0 – 2026-10-22

VDOM-Kontext der FortiGate (ADR 0019 Stufe 1, Alembic `0037`). Der Test gegen die echten
VDOMs steht noch aus.

- **Collector liest alle VDOMs:** zuerst die Liste (`system/vdom`), dann jede Tabelle je VDOM
  (`vdom=`). Bisher las er nur den Standard-VDOM des Kontos.
  - Ohne Recht auf die Liste (VDOM-Admin) bleibt es beim Standard-VDOM; sein Name kommt aus
    der Antwort.
  - Ein VDOM ohne Leserecht fehlt und steht in der Lauf-Meldung („VDOM nicht lesbar: …“).
  - Interne VDOMs (`vsys_*`) werden nicht gelesen.
- **Regeln, Objekte, VIPs/IP-Pools, Interfaces und Subnetze tragen ihren VDOM.** Policy-ID,
  Objekt- und VIP-Name gelten je VDOM; gleiche Namen in zwei VDOMs sind zwei Objekte mit
  eigener Regel-Zahl. Verweise, Gruppen, VIP-Ziele, NAT-Nutzung und Server hinter einer VIP
  bleiben im VDOM der Regel.
- **IPv4/IPv6-Paare** nur innerhalb eines VDOMs. Entscheidungen von vor 0.15.0 (ohne VDOM)
  gelten weiter für jeden VDOM der Firewall; eine neue Entscheidung gilt für den VDOM des
  Paares.
- **Findings:** doppelte Werte je VDOM; Objekte ausserhalb von `root` tragen den VDOM im Namen
  des Befunds („H_x (VDOM kunde-a)“) und im Satz („auf fw-01 (VDOM kunde-a)“).
- **Gleicher VIP-Name, andere externe IP** (zwei VDOMs bzw. Firewalls): eigenes Werte-Objekt je
  IP. Bisher zeigte eine der Regeln die externe IP der anderen.
- **Oberfläche:**
  - Regeln: VDOM unter dem System und Filter „VDOM“ (sobald es mehrere gibt); Suche nach dem
    VDOM-Namen.
  - Regel-Detail: VDOM mit Policy-ID.
  - Objekte, NAT / VIPs und Port-Detail: VDOM.
  - Applikations-Doku: Firewall-Objekte mit VDOM.
  - Der Standard-VDOM `root` erscheint nur, wenn es mehrere VDOMs gibt.
- **API:** `vdom` in Regeln, Objekten, NAT und Interfaces; `GET /rules?vdom=`; Kennzahl
  `by_vdom` in `/rules/summary`.
- Nach dem Update **jede FortiGate einmal neu einlesen**; erst dann haben Regeln und Objekte
  ihren VDOM.

## 0.14.1 – 2026-10-22

Korrekturen aus dem Betrieb (keine Migration).

- **NAT / VIPs zu breit, kein Scrollbalken:** lange Listen (NAT / VIPs, Regeln & Contracts,
  Regeln der Applikation) scrollen in ihrer Karte mit fester Kopfzeile – der waagrechte
  Scrollbalken ist immer im Bild. Server im NAT je Zeile statt mit Komma.
- **Applikation → Systeme → Firewall-Objekt** je Name zusammengefasst: darunter die
  Firewalls, auf denen das Objekt steht (Klick öffnet es), und ob der Name zum DNS-Namen im
  IPAM passt (✓ bzw. „Name ≠ DNS“).
- **Regeln der Applikation zeigen das Objekt der Regel:** Quelle und Ziel wie in der Regel, darunter je
  Objekt, über das die Regel Systeme der Applikation erfasst (`H_web01 10.10.10.5`,
  `N_srv16 10.20.0.0/16`, `any`, `EPG Tenant/AP/EPG`), die Systeme darin. So ist sichtbar, ob
  die Regel für die VM steht oder für ein Subnetz bzw. any.
- **Keine Mehrfach-Zeilen mehr über any bzw. ein Netz:** In den 10 Kategorien steht eine Regel je
  Seite und Objekt einmal (`any: web01, db01, …` statt einer Zeile je System). Auch das
  Regel-Detail fasst so zusammen.

## 0.14.0 – 2026-10-22

Applikationen mit der Doku im Mittelpunkt, Prüfung in NAT / VIPs, VM-Angaben in den
Detailansichten (Alembic `0036`).

- **Applikationen** (ADR 0012, Ergänzung 0.14.0):
  - **Klick auf eine Applikation öffnet ihre Doku** (`#apps?app=<id>`). Mitglieder und
    Stammdaten werden von dort gepflegt.
  - Neue Felder: **externer Name**, **interner Name**, **Verwendungszweck** (Dev, Test, Prod).
  - **Übersicht** oben: VMs, EPGs, Regeln **spezifisch** bzw. **unspezifisch** (Subnetz, any,
    vzAny, Preferred Group) und offene Findings; Klick filtert die Regeln.
  - **Systeme = alle VMs der Applikation** (Portgruppen ihrer EPGs, VMs hinter ihren Servern)
    und Server ohne VM, mit IPs/DNS, EPG, Portgruppe, Cluster/Host, Firewall-Objekt (Link),
    Standort und dem Grund, warum sie dazugehören. Darunter die **EPGs im ACI** (BD, VRF,
    Subnetze, VMs, Endpunkte).
  - **Regeln & Contracts** wie in der Regel-Ansicht: Aktion (allow/deny, auch bei ACI und
    L3Outs), **Herkunft** (Firewall bzw. ACI mit Quellsystem), Richtung, System der
    Applikation, Gegenstelle, Dienste, Bezug, Kategorie. Filter **System/VM**, **EPG**,
    **Richtung aus Sicht des Systems** (eingehend/ausgehend), Art, Bezug, Suche.
  - **Regel-Detail mit Erklärung**: je System Rolle (Ziel/Quelle bzw. Provider/Consumer),
    Bezug, Kategorie und über welches Mitglied bzw. AP es zur Applikation gehört; Name des
    Contracts bzw. der Firewall-Regel mit Link, „Findings dazu“.
  - **Ganzer ACI-Pfad** `Tenant/AP/EPG` statt nur `EG_…` – als System, Gegenstelle und in den
    10 Kategorien (auch in der Doku je System).
  - Findings der Applikation (Regeln, Server, VMs, Portgruppen, Subnetze der EPGs) mit Links.
  - Nennt eine Regel das System auf einer Seite gezielt, gilt any auf der Gegenseite nicht
    mehr als eigener Zugriff (Server → any erscheint nur noch ausgehend).
- **NAT / VIPs: Korrektheitsprüfung** (Spalte „Prüfung“, Schalter „nur Abweichungen“,
  `GET /nat?issues_only=`): Befunde des VIP- bzw. Pool-Objekts wie in den Objekten
  (Namenskonvention, unbenutzt, nicht im IPAM) und „Ziel nicht im IPAM“ (Mapped-IP bzw. Real
  Server). Im Detail „Objekt öffnen“ und die VM hinter dem internen Ziel.
- **VM-Angaben in den Detailansichten:** Regel-, Objekt- und NAT-Detail zeigen je VM Status,
  Gast-OS, Hostname, alle IPs, Netzwerkkarte/MAC, Portgruppe → EPG, Cluster/Host,
  Datacenter, vCPU/RAM/Disk und Notiz.
- **Läufe** stehen jetzt unter Administration.
- Direktlinks: `#objekte?object_id=<id>` öffnet das Objekt-Detail, `#findings?q=<Text>` sucht
  in den Findings.

## 0.13.0 – 2026-10-22

Läufe-Ansicht, kompakte Ports, Portgruppen im Detail (keine Migration).

- **Läufe** (neue Ansicht unter Dokumentation, `#laeufe`): alle Collector-Läufe des Mandanten,
  seitenweise mit Gesamtzahl.
  - Filter Status (offen = wartet oder läuft, wartet, läuft, fehlgeschlagen, erfolgreich),
    Quellsystem, Plattform und Auslöser (von Hand, Zeitplan).
  - Je Lauf Ausführung, Regeln, Meldung, angelegt/begonnen/beendet mit Dauer; offene Läufe
    abbrechen (Operator).
  - `GET /jobs` nimmt dafür `status` (auch `open`), `trigger` und `offset` und liefert die
    Gesamtzahl im Header `X-Total-Count`.
- **Übersicht:** „Letzte Läufe“ zeigt immer **alle offenen Läufe** (auch ältere und bei mehr
  als zehn Geräten), dazu die letzten acht, mit Zähler „N offen“ und Link „Alle Läufe (N)“.
- **Ports kompakt:**
  - Eine Zeile je Port. Lange VLAN-Listen als Bereiche (`100-120, 300`), ab vier Bereichen nur
    die Anzahl („tagged 24 VLANs“). EPGs als Anzahl („3 EPGs“), IPs gekürzt.
  - **Klick → Detail** mit allen Angaben: Status, Beschreibung, Parent/LAG und
    Subinterfaces/Mitglieder (anklickbar), alle VLANs, IPs, Zone, Policy-Gruppe, Nachbar,
    alle EPGs.
  - In den VLANs ist die EPG-Liste gekürzt (alle im Tooltip).
- **Portgruppen im Detail** (Klick, `GET /portgroups/{id}`):
  - EPG mit Tenant, AP, BD, VRF und DN, dazu die Art der Zuordnung (Name bzw. VLAN).
  - **Subnetze** mit Quelle: ACI (EPG, BD bzw. L3Out), das Netzinventar mit gleichem VLAN (als
    Hinweis, „VLAN 1107 in …“) und das IPAM-Netz der VM-IPs. Dazu „Regeln im Subnetz“.
  - **VMs** mit Status, NIC (MAC, getrennt), IPs mit DNS-Namen und „nicht im IPAM“ sowie
    Cluster/Host.

## 0.12.0 – 2026-10-22

Paare bestätigen, VIP-Ziele, Ports je Gerät, Betriebszustand (Alembic `0035`).

- **IPv4/IPv6-Paare bestätigen bzw. ablehnen** (Operator):
  - Im Regel-Detail „Als Paar bestätigen“ (für „ähnlich“) bzw. „Kein Paar“; „zurücknehmen“
    macht es rückgängig. Angezeigt wird, wer wann entschieden hat.
  - Gilt sofort und über neue Läufe hinweg. Gespeichert wird an den Policy-IDs der FortiGate.
  - Bestätigte Paare tragen „✓ bestätigt“ (Abweichungen im Tooltip); abgelehnte werden nicht
    mehr vorgeschlagen.
  - Neuer Filter „Paar“ in den Regeln: ähnlich (offen), bestätigt, gleich, mit/ohne Paar.
- **VIP-Ziele als Objekte:**
  - Die interne Ziel-IP einer VIP ohne eigenes Adressobjekt wird ein Objekt „VIP-Ziel“
    (Mapped-IP bzw. Real Server, z. B. „VIP_web → 10.10.40.5“). Die Suche findet es.
  - Adressobjekte hinter einer VIP – per Name (FQDN-VIP `mapped-addr`, Real Server mit
    Adressobjekt) oder mit genau der Ziel-IP – tragen „Ziel von VIP …“ (Link in NAT / VIPs).
  - Beide zählen die Regeln der VIP mit und gelten nicht als unbenutzt.
- **Ports je Gerät:** Das Gerät steht als Kopfzeile (auf-/zuklappbar). Darunter die Ports,
  Subinterfaces unter ihrem Parent, LAG-Mitglieder unter dem LAG. Subinterfaces zeigen „VLAN 10
  getaggt über internal“.
- **Betriebszustand** (Ampel, `GET /system/health`): Datenbank/Migrationen, Zeitplan, zentraler
  Worker (holt er zentrale Läufe ab?), Agenten (meldet sich nicht, aktualisiert nicht),
  Quellsysteme (nie eingelesen, letzter Lauf fehlgeschlagen, Stand älter als 7 Tage),
  NetBox-Sync (fehlgeschlagen) und Sicherung (fehlt bzw. älter als 26 h).
  - Auf der System-Seite vollständig, in der Übersicht als Hinweis, sobald etwas nicht stimmt
    (mit Link in die betroffene Ansicht).

## 0.11.4 – 2026-10-22

Korrekturen aus dem Betrieb (Alembic `0034`).

- **Agent aktualisiert sich auch im Wach-Modus:** Er prüft alle 5 Minuten auf eine neue
  Version, vorher nur beim Start. Die neue Version übernimmt die restliche Wachzeit.
- **Warum ein Agent nicht aktualisiert, steht in der Agentenliste**, z. B. „Signatur fehlt“,
  „Selbst-Update auf der Station abgeschaltet“ oder „Update-Prüfung nicht möglich: …“. Der
  Agent meldet das Ergebnis der letzten Prüfung mit jedem Aufruf (`X-Agent-Update`).
- **Agent-Log:** Jede Ausgabe steht zusätzlich in
  `<Installationsordner>\logs\agent-JJJJ-MM-TT.log` (14 Tage). Die geplante Aufgabe zeigte
  bisher keine Ausgaben, Fehler waren deshalb unsichtbar.
- **FortiGate ohne JSON:** Die Meldung nennt bei HTML zusätzlich den Seitentitel (z. B. die
  Sperrseite eines Proxys dazwischen).
- **NetBox-Sync robust:**
  - Vorschau und Übernahme, die mit einem Neustart des Orchestrators gestorben sind, werden
    beim Start beendet. Bisher blieben sie für immer auf „wird übernommen“ und sperrten jeden
    weiteren Sync.
  - Ohne Lebenszeichen nach 30 min ebenfalls (`AUTOMATO_NETBOX_SYNC_TIMEOUT_MINUTES`); die
    Übernahme meldet sich je Objektart („schreibt: VLAN“).
  - **Abbrechen** für laufende Pläne (`POST /netbox-sync/plans/{id}/cancel`, auditiert). Die
    Übernahme hört vor der nächsten Objektart auf.
- **FortiGate-VLAN-Subinterfaces** werden als getaggt über den Parent eingelesen, nicht mehr
  als untagged (z. B. Subinterfaces auf „internal“). Gilt ab dem nächsten Lauf der FortiGate;
  der NetBox-Sync passt die Ports dann an.

## 0.11.3 – 2026-10-21

Korrektur: „Alle einlesen“ übersprang ACI und DDI, obwohl nichts lief (Alembic `0033`).

- **Ursache:** Ein Lauf, den ein Agent abgeholt, aber nie abgeschlossen hatte (Agent beendet
  bzw. neu gestartet, Ergebnis-Upload verloren), stand für immer auf „läuft“. Er blockierte
  „Alle einlesen“ und den Zeitplan. In der Übersicht war er nicht zu sehen (nur die letzten
  acht Läufe); „Jetzt einlesen“ prüft nicht auf offene Läufe und ging deshalb.
- **Hängende Läufe werden beendet:**
  - laufend ohne Ergebnis nach 120 min (`AUTOMATO_RUN_TIMEOUT_MINUTES`, ab dem Abholen);
  - nicht abgeholt nach 24 h (`AUTOMATO_RUN_QUEUE_TIMEOUT_HOURS`).
  - Ein später eintreffendes Ergebnis wird trotzdem eingespielt.
- **Abbrechen von Hand** (Operator): „Abbrechen“ bei offenen Läufen in der Übersicht,
  „Abbrechen & neu starten“ im Dialog „Alle einlesen“ (`POST /jobs/{id}/cancel`, auditiert).
- **Genauer Grund beim Überspringen**, z. B. „Lauf läuft seit 21.10. 09:12 (Agent
  „agent-01“)“ oder „Lauf wartet seit … auf den zentralen Worker“.
- **Agent:** Scheitert der Ergebnis-Upload (z. B. HTTP 413, Zeitüberschreitung), meldet er den
  Lauf kurz als fehlgeschlagen („Ergebnis-Upload fehlgeschlagen: …“), statt ihn offen zu
  lassen.
- Läufe tragen den Beginn (`started_at`: abgeholt bzw. vom zentralen Worker gestartet).

## 0.11.2 – 2026-10-21

Korrekturen aus dem Betrieb gegen die echten Systeme.

- **NetBox/Infoblox „Teil-Upload unvollständig (6000 von 6624)“:** Der Orchestrator hat erst
  gespeichert, nachdem die Antwort beim Agenten war. FastAPI führt den Commit einer
  Dependency sonst erst nach dem Senden aus. Der Agent schickte so das Ergebnis, bevor der
  letzte Teil-Upload in der Datenbank stand.
  - Jetzt wird bei allen Anfragen vor der Antwort gespeichert (`Depends(get_session,
    scope="function")`).
  - Ein fehlgeschlagenes Speichern kommt damit auch als Fehler beim Aufrufer an statt als
    Erfolg.
  - Ein Test prüft das für jede Route.
- **FortiGate „Expecting value: line 1 column 1 (char 0)“:** Eine FortiGate antwortete auf
  eine Abfrage mit HTTP 200, aber ohne JSON.
  - Optionale Tabellen (`firewall/policy6`, `firewall/vip6`, `firewall/ldb-monitor`,
    Systemstatus) gelten dann als nicht vorhanden; der Lauf geht weiter.
  - Bei Pflicht-Tabellen nennt die Meldung jetzt Pfad, Status und Inhalt, z. B. „FortiGate
    antwortet auf firewall/policy nicht mit JSON (HTTP 200, text/html, 18 Bytes)“.

## 0.11.1 – 2026-10-21

Korrektur aus dem ersten Lauf gegen ein echtes vCenter.

- **Fehlende vCenter-Berechtigung verständlich gemeldet:** Statt des mehrzeiligen
  `vim.fault.NoPermission` steht im Lauf z. B. „Keine Berechtigung im vCenter: dem Konto fehlt
  System.View auf vim.Folder:group-d1 (oberste Ebene des Inventars)“. Der Hinweis nennt die
  Lösung: Rolle *Read-only* auf dem vCenter-Objekt oder als globale Berechtigung, jeweils mit
  „An untergeordnete Objekte weitergeben“.
- **`--check` prüft auch das Leserecht:** Er liest wie der Lauf ab dem Wurzelordner und meldet
  die Zahl der sichtbaren VMs. Bisher prüfte er nur die Anmeldung und war deshalb grün, obwohl
  der Lauf scheiterte.
- Fix: „Anmeldung abgelehnt“ beim vCenter wurde nicht erkannt (Klassenname in pyVmomi
  `vim.fault.InvalidLogin`).

## 0.11.0 – 2026-10-21

VMware vCenter lesen, VMs in Regeln, Objekten und Doku, VMs nach NetBox Virtualization
(ADR 0017, Alembic `0032`).

- **Neue Plattform VMware** (Quellsystem mit Benutzer/Passwort, über den Agenten):
  - Gelesen werden VMs (Status, Gast-OS, Hostname, vCPU, RAM, Disk, Notiz, Vorlage), ihre
    NICs mit MAC, Portgruppe und IPs (VMware Tools), Hosts, Cluster, Datacenter und
    Portgruppen (verteilt mit VLAN/Trunk/PVLAN, Standard je Host).
  - **Nur lesen, auch im Code:** Der Collector lässt nur lesende vSphere-Methoden zu
    (Anmelden, Service-Content, Container-View, Eigenschaften abrufen). Jeder andere Aufruf
    bricht vor dem Senden ab. Das vCenter-Konto braucht die Rolle **Read-only**.
- **Portgruppe → ACI-EPG:**
  - Name nach ACI-VMM-Schema `Tenant|AP|EPG` (z. B. `TE_x|AP_007|EG_dmz02`), auch ohne
    Präfixe (`EG_db01` = `EPG_db01`).
  - Sonst das VLAN der Portgruppe, wenn es genau einer EPG gehört.
  - Die Zuordnung wird bei jedem Abgleich neu berechnet.
- **VMs in Automato:**
  - Objekte → **VMs** (Netzwerk mit Portgruppe → EPG, IPs mit DNS-Namen und „nicht im
    IPAM“, Cluster/Host, Ressourcen, „Regeln der VM“) und **Portgruppen**.
  - IPs in Regeln und Objekten zeigen die VM dahinter; die Regelsuche findet VM-Namen.
  - EPGs nennen ihre VMs.
  - Doku je System: der Server nennt seine VM und bringt die Contracts der EPG ihrer
    Portgruppe mit, auch ohne gelernten Endpunkt. Die Doku je EPG nennt die VMs.
  - Neue Befunde: **VM-IP fehlt im IPAM**, **Portgruppe nach ACI-Schema ohne EPG**.
- **NetBox-Sync, neuer Abschnitt „Virtualisierung“:**
  - Cluster-Typ „VMware vSphere“, Cluster-Gruppe je Datacenter, Cluster, VMs (Status, vCPU,
    RAM, Disk, Kommentar mit Gast-OS, Host und EPG), VM-Interfaces (MAC, Portgruppe, VLAN,
    EPG) und die IPs am VM-Interface. Vorlagen werden nicht übernommen.
  - Von Hand gepflegte IPs (NetBox als IPAM) bleiben unverändert; fehlt nur die Zuordnung
    zur VM, ist das ein Konflikt.
  - Ohne den Abschnitt bleiben VMs in NetBox unangetastet.
  - Der Sync-Benutzer braucht die Rechte für `virtualization.*`: `sync-user.sh` erneut
    ausführen.

## 0.10.0 – 2026-10-21

FortiGate Virtual Server, vollständige Doku je System bzw. Applikation, Regeln je Subnetz
(Alembic `0031`).

- **Virtual Server** (FortiGate-VIP vom Typ *server-load-balance*):
  - Erfasst werden externe IP und Port sowie alle **Real Server** mit IP bzw. Adressobjekt,
    Port, Status (aktiv/Standby/aus), Gewicht, Health-Check und maximalen Verbindungen.
  - Dazu Server-Typ, Lastverteilung, Persistenz, Health-Checks (`firewall/ldb-monitor`:
    Typ, Port, Intervall, URL) und das Zertifikat. Vom Zertifikat wird nur der Name gelesen.
  - NAT-Sicht: Typ „Virtual Server“, Real Server mit Firewall-Objekt und DNS-Namen.
  - Suche über Real-Server-IP, Server, Methode und Health-Check.
  - Regeln mit dem Virtual Server treffen auch über jeden Real Server (IP-Suche, Doku je
    System).
- **IPv6-VIPs** (`firewall/vip6`) werden gelesen, auch als Virtual Server. `dstaddr6`-Verweise
  lösen gegen sie auf.
- **Doku je System und je Applikation erfasst alle Regeln.** Sie erscheinen gleichwertig;
  die neue Spalte **„Bezug“** sagt, wie die Regel greift.
  - Firewall-Regeln direkt sowie über Netze, Bereiche, Gruppen und any, für Server (Host-IP)
    und für EPGs (Subnetze und Endpunkte der EPG).
  - ACI-Contracts direkt, über **vzAny** der VRF und über **ESGs**, die die EPG einschliessen.
  - Ein Server bringt die Contracts seiner EPG mit (gelernter Endpunkt).
- **Regeln je Subnetz:**
  - Objekte → Subnetze → „Regeln im Subnetz“ zeigt alle Regeln, deren Quelle oder Ziel sich
    mit dem Subnetz überschneidet. any lässt sich ein- und ausblenden.
  - Ein Netz (CIDR) im Suchfeld der Regeln trifft genauso.

## 0.9.0 – 2026-10-21

IPv4/IPv6 getrennt, Regel-Zahl über Gruppen, grosse Regelwerke schnell (ADR 0016, Alembic `0030`).

- **IPv4- und IPv6-Objekte gleichen Namens** sind zwei Objekte, wie auf der FortiGate
  (`address`/`addrgrp` bzw. `address6`/`addrgrp6`):
  - Werte, Gruppen, Regel-Zahl und Befunde je Familie; Befunde zu IPv6-Objekten heissen
    „Name (IPv6)“.
  - Policies lösen `srcaddr`/`dstaddr` gegen IPv4, `srcaddr6`/`dstaddr6` gegen IPv6 auf.
  - `firewall/policy6` (FortiOS 6.x) wird mitgelesen, wo es die Tabelle gibt.
- **Familie je Regel** (IPv4, IPv6, IPv4+IPv6) und Filter „Familie“.
- **IPv4/IPv6-Paare** werden nur markiert, beide bleiben eigene Regeln:
  - „↔ IPv6 #…“ (grün = deckt dasselbe ab, gelb = ähnlich, mit Abweichungen im Tooltip).
  - Klick öffnet das Gegenstück; beim Überfahren wird es hervorgehoben.
- **Regel-Zahl bei Objekten** zählt die Nutzung direkt und über (verschachtelte) Gruppen.
  - Sie stimmt mit dem Objekt-Detail überein.
  - Der Link öffnet genau dieses Objekt (`#regeln?object_id=…`).
- **Grosse Regelwerke** (gemessen: 3000 Regeln, 9500 Objekte, 39 000 Endpunkte):
  - Regelliste 0,9 → 0,07 s, Suche 3,5 → 0,04 s, IP-Suche 4,1 → 0,1 s.
  - Objekt-Detail 8,7 → 0,05 s, Doku je System 2,2 → 0,04 s.
  - Einlesen 216 → 25 s.
  - Möglich macht das ein abgeleiteter Regelindex (Suchtext, Objekt-Nutzung, IP-Bereiche),
    beim Einlesen berechnet. Dazu ein Zwischenspeicher je Lauf und seitenweise Abfragen in
    der Datenbank.
- **Nach dem Update:** Den Index baut die erste Anfrage einmalig auf. Die getrennten
  IPv6-Objekte und Familien kommen mit dem nächsten Lauf eines Agenten ab 0.9.0
  („Alle einlesen“).

## 0.8.1 – 2026-10-20

Tests und CI ausgebaut (`docs/TESTS.md`), zwei Korrekturen (Alembic `0029`).

- **UI-Tests im Browser (Playwright, CI-Job `e2e`):**
  - Jede Ansicht lädt ohne Seiten- und API-Fehler.
  - Kernabläufe: Suche über Gruppenmitglieder und IPAM-Namen, SNAT/VIP ↔ NAT ↔ Regeln,
    Findings mit Link, „Regel nicht wirksam“, Dienste mit Detail, Standorte, „Alle einlesen“.
  - Beispieldaten aus den Orchestrator-Tests (`web-ui/frontend/e2e/`); lokal `npm run e2e`.
- **`alembic check`** im Postgres-Job: Modell und Migrationen müssen übereinstimmen.
- **Mindestabdeckung 90 %** je Python-Paket (Stand 94/95/94 %).
- **PowerShell-Syntaxprüfung** der Agent-Skripte (`scripts/check_powershell.ps1`).
- **Images bauen** (Orchestrator, Collector, Web-UI) und Compose-Konfiguration aller Profile
  prüfen (Job `docker`).
- **Nächtlicher Lauf** aller Jobs und Start von Hand (*Run workflow*).
- **Fix:** `agent.created_at` war in der Datenbank nullable, im Modell nicht (gefunden von
  `alembic check`).
- **Fix NAT:** Ist die interne IP in mehreren Firewall-Objekten definiert, zeigt die NAT-Sicht
  alle als Server (bevorzugt die derselben Firewall), nicht nur das alphabetisch erste.

## 0.8.0 – 2026-10-20

Dev-Befunde: NetBox-Sync, Regeln ohne Quelle/Ziel, Dienste (Alembic `0028`).

- **Fix NetBox-Sync** („duplicate key … ipam_vlan_unique_group_name“): Mehrere VLANs einer
  ACI-EPG trugen denselben Namen. NetBox verlangt eindeutige Namen je VLAN-Gruppe (Sites
  global). Doppelte Namen bekommen jetzt die VID bzw. den Code angehängt, z. B.
  „EG_web (110)“. Fällt die Dublette weg, gilt beim nächsten Sync wieder der schlichte Name.
- **Regeln ohne Quelle/Ziel:**
  - FortiGate: Internet Services (ISDB) als Ziel bzw. Quelle, Benutzer/Gruppen/FSSO und
    ZTNA-Tags als Quelle werden gelesen. Negierte Felder sind als „nicht (negiert)“
    gekennzeichnet.
  - ACI: Contract Master (geerbte Contracts) und Intra-EPG-Contracts füllen Consumer und
    Provider.
  - Was danach noch ohne Quelle/Ziel bleibt, ist gekennzeichnet („kein Consumer · nicht
    wirksam“) und ein Befund **„Regel nicht wirksam“** (ACI: Info).
- **Dienste wie Adressobjekte:**
  - FortiGate-Services mit Protokoll, Ports inkl. Quellports, ICMP-Typ/-Code, IP-Protokoll,
    Kategorie, Timern und Ziel-Beschränkung.
  - **ACI-Filter** als Service-Objekte `<Tenant>/FT_…` mit ihren Einträgen; Contracts
    verweisen darauf (Dienste-Spalte, Suche, Nutzung).
  - **Prüfung nach Namensschema:** Präfix `FT_` (erweiterbar über das Wörterbuch
    `service_prefix`) und Port passend zum Namen – laut Wörterbuch `service` bzw. Schema
    `FT_<proto><port>` (neuer Befund **„Service ≠ Name“**). FortiOS-Standardservices sind
    ausgenommen und zählen nicht als unbenutzt.
- **Objekt-Detail wie bei NAT** (`GET /objects/{id}`): Attribute, Werte, Mitglieder, Befunde und
  die Regeln, die das Objekt nutzen (auch über Gruppen), mit „In Regeln anzeigen“; Klick auf
  eine Regel öffnet sie (`#regeln?rule=<id>`). Gilt für Services wie für Adressobjekte.

**Hinweis:** Die neuen Felder liest erst der aktualisierte Collector bzw. Agent (Selbst-Update).
Danach FortiGate und APIC neu einlesen.

## 0.7.0 – 2026-10-20

Virtuelle und physische Standorte (ADR 0015, Alembic `0027`).

- **Zwei Arten Standort:** *virtuell* = dreistellige Nummer der Namenskonvention (`000` z. B.
  Stretched DC, `001` …), *physisch* = Ort-Code des Rechenzentrums (z. B. `chzrh1`). Ein
  virtueller Standort kann an mehreren physischen laufen (Zuordnung n:m, „Zuordnen“).
  - Bestand bleibt: Ziffern-Codes werden virtuell, andere (z. B. `ZRH`) physisch; alle
    Verortungen bleiben erhalten.
  - Standort-Auswahl (Quellsystem, Doku je System, Applikation) nach Art gruppiert, Anzeige
    mit Zuordnung („000 · Stretched DC (chzrh1, chbsl1)“).
- **Standort aus dem Namen (Vorschlag):** Netz-Objekte (`N_<tenant>-000-…`, `T_`, `IPP_`),
  BD-/Subnetz-Namen, Inventar-Geräte und Quellsysteme (`fwc11-000-<tenant>`,
  `evh01-chzrh1-<tenant>`) zeigen den erkannten Standort; „?“ = Code nicht erfasst. VMs tragen
  keinen Standort im Namen und werden nicht ausgewertet.
- **NetBox-Sync:** Standorte als NetBox-Sites in den Standort-Gruppen *Virtuelle Standorte* und
  *Physische Standorte*, Kommentar mit der Zuordnung. Gruppe/Kommentar nur an eigenen Sites
  (Tag `automato`); von Hand gepflegte Sites werden referenziert, ohne Konflikt. Gerät ohne
  Standort am Quellsystem: der einzige physische Standort.

**Hinweis NetBox:** Das Schreib-Token braucht zusätzlich das Recht auf *DCIM › Site Group*
(Permission `automato-sync-write` ergänzen oder `source-of-truth/netbox/sync-user.sh` erneut
ausführen – erzeugt einen neuen Token, ausser `SYNC_TOKEN` ist gesetzt).

## 0.6.0 – 2026-10-20

Dev-Befunde aus dem Test gegen die echten Systeme: Suche, NAT, Findings, Namensabgleich,
„Alle einlesen“ und ein reaktiver Agent. Keine Schemaänderung.

- **Regelsuche findet alles:** Regel (Name, ID, Kommentar, Zonen, Aktion, Schutzprofile),
  Objekte **inkl. verschachtelter Gruppenmitglieder**, Services, Werte, DNS-Namen aus dem
  IPAM, EPG/AP/Tenant/BD/VRF und NAT. Eine IP trifft auch, wenn sie in einem Netz/Bereich der
  Regel liegt („any“ ausgenommen); mehrere Wörter müssen alle vorkommen. Neue exakte Filter
  `object` (Regeln mit dem Objekt, auch über Gruppen) und `nat`; Zonen-/Aktionsfilter
  serverseitig über alle Regeln.
- **NAT in den Regeln:** Kennzeichen **SNAT** (Pool bzw. Interface-IP) und **VIP** je Regel,
  im Detail ein NAT-Abschnitt mit IPs, Ports und DNS-Namen; Klick führt in die NAT-Sicht.
- **NAT / VIPs detailliert:** DNS-Namen beider IPs aus DDI/NetBox, Server hinter der internen
  IP, Interface, Kommentar, Regelzahl; Suche (Name, IP auch im Pool-Bereich, Port, DNS-Name,
  Server); Detail mit Einstellungen, IPAM-Sicht und den Firewall-Regeln, „In Regeln anzeigen“.
- **Verlinkung:** Objekte in Regel-Detail, Objekt-Liste und Findings führen zu allen Regeln mit
  dem Objekt; NAT-Einträge zu ihren Regeln und zurück (`#regeln?object=…`, `#regeln?nat=…`).
- **Findings:** Übersicht nach Kategorien (IPAM-Abgleich, Namenskonvention, Bereinigung, ACI)
  mit Zählern je Typ; je Befund ein Satz, **was genau nicht stimmt**, und ein Hinweis, was zu tun
  wäre. „Nicht im IPAM“ nennt Firewall-Objekte, Regeln und – wenn das IPAM den Server unter
  seinem Namen mit anderer IP kennt – diese IP; mehrdeutige Namen nennen die Kandidaten.
- **IPAM-Name ohne Präfix:** Im IPAM steht der Server ohne Präfix (`web01.example.ch`), auf
  Firewall/ACI mit Präfix (`H_web01`). Der Abgleich sucht ohne Präfix und über den Kurznamen
  (nur wenn eindeutig); Regel- und Objektsuche finden über den IPAM-Namen.
- **Alle einlesen:** Übersicht → „Alle einlesen“ bzw. `POST /jobs/collect-all` – ein Lauf je
  aktivem Quellsystem des Mandanten, laufende und nicht lesbare werden mit Grund übersprungen.
- **Agent reaktiver:** `automato-collector --poll --watch 20 --for 14` bleibt zwischen den
  Starts der geplanten Aufgabe wach und holt neue Läufe nach Sekunden statt nach bis zu 15 min.
  `run-agent.ps1` nutzt das standardmässig (`-Once` = einmal holen); `install.ps1` setzt
  `IgnoreNew`, damit nie zwei Agenten parallel laufen. Bestehende Agenten übernehmen den
  Wach-Modus mit dem nächsten Selbst-Update.

## 0.5.0 – 2026-10-20

Applikationen, zeitgesteuerte Läufe, ACI-Lücken und Produktions-Gate (Alembic `0024`–`0026`).

- **Applikationen (ADR 0012):** eigenes Objekt mit Kürzel, Verantwortlichen, Kritikalität,
  Standort und Mitgliedern: Application Profiles (`Tenant/AP`), einzelne EPGs, Server.
  - **Vorschläge** nach der Projektnummer aus `AP_<nr>` bzw. `H_<funktion>-<nr>-…`, mit einem
    Klick übernehmen bzw. ergänzen.
  - **Doku je Applikation** über alle Systeme mit Markdown-Export und Druckansicht (PDF über
    den Browser).
  - Die VIP-Sicht heisst jetzt **Publizierte Dienste** (`/published-services`).
- **EPG-Lage:** Leaf-Ports (statische Pfade, LLDP/CDP-Nachbar) und VLANs der EPG.
- **ACI:** **ESGs** (Selektoren EPG/Tag/IP) als Consumer/Provider, **Taboo-Contracts** als
  Deny-Regeln auf die geschützten EPGs, **Preferred Groups** als eine Regel je VRF. Nach dem
  Update den APIC neu einlesen.
- **Zeitgesteuerte Läufe:** Zeitplan je Quellsystem (Vorlagen oder Cron, Zeitzone
  Europe/Zurich); „Letzte Läufe“ kennzeichnet sie mit „Zeitplan“.
  - Ein Termin wird auch bei mehreren Orchestrator-Prozessen nur einmal ausgelöst.
  - Läuft oder wartet schon ein Lauf, fällt der Termin aus.
- **NAT und Findings seitenweise** mit Gesamtzahl; Zähler in Übersicht und Navigation aus
  Summary-Endpunkten statt aus gekappten Listen.
- **Produktions-Gate (ADR 0013):**
  - **mTLS-Agent-Gateway** (`deploy/agent-gateway`, `AUTOMATO_INTERNAL_MTLS_MODE=require|bind`),
  - **signierte Agent-Updates** (Ed25519, offline signiert),
  - **Sicherung/Wiederherstellung** mit age (`deploy/backup`),
  - **`deploy/update.sh`** mit Rückfall auf Code und Datenbank,
  - **R16-Prüfung** `automato-collector --check --strict`,
  - neue Admin-Ansicht **System**.
- **Fix Agent-mTLS:** Mit gepinnter CA legte der Agent sein Client-Zertifikat nicht vor (httpx
  0.28 ignoriert `cert=` neben `verify=<pfad>`); jetzt ein gemeinsamer SSL-Kontext.
- **CI:** Migrationen hoch/runter und alle Orchestrator-Tests zusätzlich gegen Postgres 16.
- **Doku:** CLAUDE.md gestrafft (Verlauf bis 0.4.2 in `docs/ENTWICKLUNGSVERLAUF.md`),
  ROADMAP-Status, ADR 0012–0014 (0014 = Entwurf gemeinsames Cockpit mit Magister).

**Hinweis Agenten:** Das Update auf 0.5.0 bringt die neue Abhängigkeit `cryptography`. Die
Station braucht dafür einmalig PyPI-Zugang oder ein Wheelhouse. Ohne Zugang bleibt der Agent
auf seiner Version und meldet den pip-Fehler.

## 0.4.2 – 2026-10-20

- **ACI-Zone je Contract-Seite (Dev-Befund):** Die Zone kam vom *ersten* Consumer bzw.
  Provider. Konsumiert eine externe EPG (z. B. VPN hinter `L3O_vpn`) viele Contracts zusammen
  mit Anwendungs-EPGs, trug dadurch fast jeder Contract „L3O_vpn“. Jetzt hat eine Seite nur
  dann einen L3Out als Zone, wenn **alle** ihre Endpunkte externe EPGs desselben L3Outs sind;
  gemischte Seiten haben keine Zone, die externe EPG steht als `L3O_vpn/<EPG>` in der
  Consumer-/Provider-Liste. Nach dem Update den APIC neu einlesen.

## 0.4.1 – 2026-10-20

- **ACI-Zonen korrigiert (Dev-Befund):** Contracts zwischen EPGs zeigten einen L3Out als Quell-/
  Ziel-Zone. Ursache: die Bindung einer Bridge Domain an einen L3Out (`fvRsBDToOut`) wurde als
  Zone gewertet – sie bedeutet aber nur, dass die Subnetze der BD nach aussen bekannt gegeben
  werden. Ohne L3Out fiel die Zone zudem auf den BD- bzw. EPG-Namen zurück. Jetzt hat nur eine
  **externe EPG** eine Zone (ihren L3Out); EPG zu EPG hat keine. Im Detail steht die Zeile nur
  noch, wenn eine Seite extern ist („L3Out“).
- **Applikations-Doku:** Contracts mit externer Gegenstelle landen nach dem L3Out in den
  Tabellen 1–6/9 (z. B. `L3O_inet` → „Zugriff aus dem Internet“) statt in 7/8/9.

Nach dem Update den APIC einmal neu einlesen – die Zonen der bestehenden Contracts werden
dabei ersetzt.

## 0.4.0 – 2026-10-20

Wo liegen Consumer und Provider eines ACI-Contracts? (Alembic `0023`)

- **Lage je Consumer/Provider:** Das Detail eines ACI-Contracts zeigt pro Seite eine Karte mit
  ACI-Tenant, Applikationsprofil, Bridge Domain, VRF bzw. L3Out, den Subnetzen (EPG, BD,
  extern) samt passendem IPAM-Netz der DDI/NetBox (Beschreibung, VLAN, Standort), den gelernten
  Endpunkten mit DNS-Namen aus der Referenzquelle und dem verknüpften Server. In der Liste
  stehen Consumer/Provider als `AP/EPG` bzw. `L3Out/EPG`, mit ACI-Tenant davor, wenn er vom
  Contract abweicht.
- **ACI-Parser tenant-bewusst:** Identität per DN statt Name – gleichnamige Contracts, EPGs,
  BDs und Filter in mehreren ACI-Tenants werden nicht mehr zusammengelegt. Namensverweise wie
  im APIC: eigener Tenant, sonst `common`. Neu als Consumer/Provider: **externe EPGs**
  (L3Out, Subnetze), **vzAny** und exportierte Contracts über **Contract-Interfaces**;
  Filter nicht-reflexiver Subjects (`vzInTerm`/`vzOutTerm`).
- **Store:** EPG mit Art, ACI-Tenant, DN, VRF, L3Out und Quellsystem, Subnetze mit Herkunft.
  Bestehende EPGs (nur Name) ersetzt der nächste ACI-Lauf; veraltete EPGs eines APICs ohne
  Regelbezug fallen weg, ebenso beim Löschen des Quellsystems.
- **Applikations-Doku:** gleichnamige EPGs heissen „EG_x · Tenant · AP“; Gegenstelle in einem
  anderen ACI-Tenant → Tabelle 9. Behoben: die Doku berücksichtigte nur die ersten 500 Regeln.

## 0.3.0 – 2026-10-20

ACI-Contracts in der Regel-Ansicht.

- **Regeln & Contracts:** Die Ansicht (vorher „Firewall-Regeln“) zeigt FortiGate-Policies und
  ACI-Contracts. Neuer Filter **Art** (Firewall-Regeln / ACI-Contracts). ACI-Zeilen tragen das
  Kennzeichen „ACI“ mit Contract, Subject, ACI-Tenant und Scope; Quelle/Ziel heissen dort
  **Consumer**/**Provider**. Der Detail-Drawer zeigt Contract, Subject, Tenant, Scope, DN und
  die Consumer-/Provider-EPGs.
- **Keine Kappung mehr bei 500:** `/rules` lieferte höchstens 500 Regeln, sortiert nach
  Quellsystem – bei einer FortiGate mit vielen Policies fielen die ACI-Contracts heraus, und
  die Suche fand sie nicht. Jetzt filtert der Orchestrator nach Art, System und Suche über
  alle Regeln, liefert seitenweise (`limit`/`offset`, Gesamtzahl im Header `X-Total-Count`),
  das UI lädt mit „weitere … laden“ nach.
- **Übersicht:** Kennzahlen kommen aus dem neuen `GET /rules/summary` (alle Regeln statt der
  ersten 500), aufgeteilt in Firewall und ACI.

## 0.2.3 – 2026-10-20

- **ACI-Einspielen:** Lauf scheiterte mit `duplicate key … rule_service_pkey`, wenn mehrere
  Filter-Einträge eines Contracts denselben Service ergeben (z. B. `https` und `443`, Einträge
  nur mit anderem Quell-Port). Jeder Service zählt jetzt einmal je Regel – gilt für alle
  Plattformen.

## 0.2.2 – 2026-10-20

Diagnose bei abgelehnter Anmeldung am Gerät.

- **Collector:** Lehnt ein Gerät die Anmeldung ab (HTTP 401/403), nennen `--check` und die
  Lauf-Meldung jetzt den Fehlertext des Geräts (APIC z. B. „FAILED local authentication“) und
  einen Hinweis je Plattform (APIC: Remote-Benutzer als `apic#<Login-Domäne>\<Benutzer>`;
  Infoblox: API-Recht der Admin-Gruppe; FortiGate: Trusted Hosts). Nur lokal bei `--check`
  zusätzlich der verwendete Benutzer und Hinweise zum Passwort (Leerzeichen am Rand,
  Nicht-ASCII-Zeichen) – das Passwort selbst wird nie ausgegeben.
- **Web-UI:** Zugangsdaten-Felder der Quellsysteme werden nicht mehr vom Browser oder
  Passwort-Manager automatisch befüllt (sonst konnte ein gespeichertes fremdes Login beim
  Bearbeiten unbemerkt als neue Gerätezugangsdaten gespeichert werden).

## 0.2.1 – 2026-10-20

Korrekturen am Agent-Paket.

- **Agent-Paket:** Das sot-gateway-Wheel aus dem Orchestrator-Image war leer (der Docker-Bau
  kopierte nur `pyproject.toml`) – pip meldete „Successfully installed sot-gateway“, der Agent
  startete danach mit `No module named 'sot_gateway'`. Der Bau kopiert jetzt den Quellcode und
  bricht ab, wenn ein Wheel sein Modul nicht enthält (`scripts/check_agent_package.py`); die CI
  baut die Docker-Stage `agent-package` mit.
- **Selbst-Update:** `run-agent.ps1` wird als neue Datei abgelegt statt überschrieben – die
  Sperrmarkierung der alten Datei (Sicherheitswarnung beim Start) fällt dabei weg.
- **install.ps1:** zeigt den gefundenen Python-Starter korrekt an (statt `System.Object[]`).

Bereits installierte 0.2.0-Agenten reparieren sich beim nächsten Lauf selbst.

## 0.2.0 – 2026-10-20

Erste versionierte Ausgabe.

- **Phase A (ADR 0010):** Firewall-Objekte mit Namen und Regel-Verweisen, Netzinventar aus
  FortiGate und APIC, Infoblox DDI und NetBox als Referenzquellen (nur lesend), Abgleich
  Firewall/ACI ↔ IPAM und DDI ↔ NetBox, Ansichten „Objekte & Inventar“ und „Abgleich“,
  NetBox-Sync mit Vorschau und Übernahme per Klick (einziger Schreibweg).
- **Agent-Selbstupdate (ADR 0011):** Agenten aktualisieren sich über ihren Agent-Kanal, prüfen
  SHA-256, reparieren eine unvollständige Installation selbst; Schalter je Agent im UI.
- **Versionierung:** `VERSION` + `scripts/version.py`, Version in `/health`, UI-Fusszeile,
  Agentenliste und `automato-collector --version`.
- **Agent-Installation:** `install.ps1` entsperrt die Dateien (keine Sicherheitswarnung mehr),
  hält die geplante Aufgabe an, installiert collectors- und sot-gateway-Wheel sichtbar und
  prüft am Ende, dass der Agent startet.
- **Robustheit:** NetBox ohne Lese-Token wird beim Einlesen abgelehnt; Lauf-Fehler stehen mit
  Ursache im Log und in „Letzte Läufe“; unbekannte Plattform → Lauf `failed` statt Absturz.

## 0.1.0

Stand bis einschliesslich UC1 Model 2, ACI (Model 1), Keycloak/RBAC, externe Agenten mit
zentralen Geräte-Zugangsdaten (ADR 0001–0009). Keine einzelnen Release-Nummern; der
ausführliche Verlauf steht in `docs/ENTWICKLUNGSVERLAUF.md`.
