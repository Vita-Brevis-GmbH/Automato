# Betriebs- & Anwenderhandbuch (UC1: Firewall-Regel-Dokumentation)

Für Model 2 (FortiGate + NetBox). Setup-Details: `lab/README.md`. Architektur/Entscheide:
`ARCHITECTURE.md`, `docs/adr/`.

## Überblick

```
FortiGate ──(read-only REST)──▶ Collector ─▶ Orchestrator ─▶ NetBox (Matching, SoT)
                                                 │
                                   Web-UI ◀──────┘  (Regeln, NAT/VIPs, Applikationen, Findings)
```

- **Collector** liest rein lesend von der FortiGate (Policies, Adress-/Service-Objekte, VIPs).
- **Orchestrator** persistiert, matcht Adressen gegen NetBox, erzeugt Findings, stellt die API.
- **NetBox** ist die **Source of Truth** fürs IPAM (nach dem Erst-Import gepflegt).
- **Web-UI** zeigt alles read-only mit Filtern.

## 1. Inbetriebnahme

Einmalig auf der VM (siehe `lab/README.md`):

```bash
sudo bash lab/install-prereqs.sh && newgrp docker
FORTIGATE_URL=https://<fortigate-ip> FORTIGATE_TOKEN='<readonly>' bash lab/bootstrap.sh
```

`bootstrap.sh` fährt NetBox + Automato hoch, importiert die FortiGate-Objekte als
IPAM-Ausgangsbasis nach NetBox (Tag `automato-import`) und löst einen ersten Lauf aus.

## 2. Betrieb

**Regeln erneut einlesen** (nach FortiGate-Änderungen):
```bash
docker compose exec orchestrator automato collect --platform fortigate --tenant lab --source fw-01
docker compose exec orchestrator automato status --run-id <id>
```
Der Lauf **ersetzt** die Regeln/NAT des Quellsystems und baut die Findings neu auf.

**FortiGate mit VDOMs** (0.15.0, ADR 0019): Der Collector liest alle VDOMs der FortiGate
einzeln. Das REST-API-Konto braucht dafür ein Read-only-Profil mit Zugriff auf **alle VDOMs**.
Die Lauf-Meldung nennt die gelesenen VDOMs („VDOMs: root, …“) bzw. die fehlenden („VDOM nicht
lesbar: … (HTTP 403)“). Fehlt ein VDOM, Profil bzw. VDOM-Liste des Kontos auf der FortiGate
prüfen und neu einlesen. Ohne Recht auf die VDOM-Liste liest er nur den Standard-VDOM des
Kontos.

**Routing, BGP, HA** (0.16.0, ADR 0019 Stufe 2): Jeder Lauf einer FortiGate liest je VDOM die
wirksame Routing-Tabelle, alle BGP-Pfade und -Nachbarn, statische Routen, Policy-Routen und
SD-WAN-Regeln sowie global HA und Session-Sync; ein APIC-Lauf die L3Outs und je Border-Leaf
und VRF dessen Routing-Tabelle und BGP-Nachbarn. Das Read-only-Profil der FortiGate braucht
Lesezugriff auf Router und System (inkl. Monitor), der APIC-Benutzer auf die Fabric-Knoten.
Passwörter und Schlüssel (HA, FGSP, BGP-Peers) werden nie gelesen. Seit 0.113.0 (ADR 0041)
dazu die BGP-Konfiguration je VDOM (`router bgp`: Filter je Nachbar, network, aggregate,
redistribute) mit Prefix-Listen, Route-Maps und Access-Listen sowie die Route Control der
L3Outs – dafür braucht das Profil Lesezugriff auf die Router-Konfiguration, nicht nur den
Monitor; fehlt er, steht „BGP-Konfiguration“ als nicht lesbar in der Lauf-Meldung.

**Zeitgesteuert:** Mit einem Zeitplan am Quellsystem legt der Orchestrator zum Termin selbst
einen Lauf an (`AUTOMATO_SCHEDULER_ENABLED=true`, in docker-compose Standard). Wie „Jetzt
einlesen“: Gerät mit Agent → der Agent holt den Lauf ab. Wartet oder läuft für das
Quellsystem noch ein Lauf (z. B. Agent offline), fällt der Termin aus; nach einem Ausfall des
Orchestrators wird nur der letzte verpasste Termin nachgeholt. Mehrere Orchestrator-Prozesse
lösen einen Termin nur einmal aus. „Letzte Läufe“ kennzeichnet solche Läufe mit „Zeitplan“.

**NetBox pflegen (SoT):** Die Firewall-Objekte sind nur der Startpunkt. Netze/IPs/Zonen
in NetBox kuratieren; Zonen hängen als Tag `zone-<name>` am Prefix. Nicht gematchte
Objekte erscheinen als Findings → in NetBox ergänzen oder als extern bestätigen.

**Alle auf einmal:** Übersicht → **Alle einlesen** bzw. `POST /jobs/collect-all` legt für jedes
aktive Quellsystem des Mandanten einen Lauf an (Referenzquellen zuerst).

**Aufbewahrung** (0.48.0, ADR 0022, R14): Was älter ist als seine Frist, löscht der Zeitplan
einmal am Tag – beendete Läufe nach `AUTOMATO_RETENTION_RUNS_DAYS` (90), NetBox-Sync-Vorschauen
nach `AUTOMATO_RETENTION_SYNC_DAYS` (90), Audit-Einträge nach `AUTOMATO_RETENTION_AUDIT_DAYS`
(365), seit 0.63.0 MAC-/ARP-Einträge der Switches, die so lange kein Lauf mehr gesehen hat, nach
`AUTOMATO_RETENTION_L2_DAYS` (30), seit 0.92.0 der Verlauf des Routings nach
`AUTOMATO_RETENTION_TOPO_CHANGES_DAYS` (180); `0` = nie. Es bleiben immer: Läufe, auf die der aktuelle Stand zeigt (Regeln, Objekte,
NAT, VMs, Referenzstand), je Quellsystem der neuste und der neuste erfolgreiche Lauf, offene
Läufe und die neuste Vorschau je NetBox (übernommene Syncs stehen mit ihren Zahlen weiter im
Audit). System → **Aufbewahrung** zeigt Fristen, was fällig ist und wann zuletzt aufgeräumt
wurde; **Jetzt aufräumen** (Admin) bzw. `docker compose exec orchestrator automato purge
[--dry-run]` löscht sofort. Jedes Aufräumen steht im Audit (`retention.purged` mit den Zahlen).
Der Betriebszustand warnt, wenn Läufe über der Frist liegen und seit zwei Tagen nicht
aufgeräumt wurde (z. B. Zeitplan aus).

**Stoppen / Zurücksetzen:**
```bash
docker compose --profile core --profile collectors down          # stoppen
docker compose --profile core --profile collectors down -v       # inkl. Daten
```

## 3. Bedienung (Web-UI, `http://<host>:5173` bzw. über den TLS-Proxy `https://<host>`)

Aufbau nach dem UI-Mockup: Sidebar links (Dokumentation / Administration, Zähler für
Regeln, NAT und offene Findings), Topbar mit **Mandanten-Schalter**, HTTPS-Anzeige,
Hell/Dunkel und Benutzermenü (Rolle, Mandanten, Mein Konto, Abmelden). Im OIDC-Modus
wird das Token still erneuert; läuft die Keycloak-Sitzung ab, folgt die Anmeldung erneut.
Fehler zeigt das UI mit der Meldung des Orchestrators (z. B. „Keine Berechtigung“).

**Sprache** (seit 0.86.0): Benutzermenü → **Sprache** *Deutsch* | *English*. Die Wahl gilt je
Browser (`localStorage automato_lang`), das Umschalten lädt die Seite neu; Standard ist Deutsch.
Umgestellt sind alle Texte der Oberfläche und seit 0.87.0 auch die **Texte vom Orchestrator**:
Befundtexte und Hinweise, Gründe und Urteile im Weg, Relevanz, Spaltenköpfe und Titel der
Kategorien, Verbindungen und Netzwerkplan, Zonen-Vorschläge, Betriebszustand, Verbindungsprüfung,
Vorschau des NetBox-Syncs, Fehlermeldungen der API. Die Oberfläche schickt die Sprache bei jedem
Aufruf als `Accept-Language` mit.
- **Sprache der Datei** (Word, PDF, draw.io, SVG): eigene Wahl im Export-Dialog der
  Applikations-Doku und an den Export-Knöpfen (Zonenplan, Netzwerkplan, Verkabelung), gemerkt im
  Browser (`automato_export_lang`), Vorgabe = Sprache der Oberfläche – so entsteht z. B. eine
  englische Doku aus der deutschen Oberfläche. Markdown und Druck folgen der Oberfläche. Für
  Aufrufe von Hand: `?lang=en` an der Export-Adresse.
- **Bleibt deutsch gespeichert:** Meldungen der Läufe (seit 0.90.0 beim Abruf übersetzt: Läufe,
  „Letzte Läufe“, Betriebszustand, Agentenliste, `automato status`), gespeicherte Teile eines
  Befunds (z. B. Gründe der Verbindungen in einem englischen Satz), Gründe der Kunden-Zuordnung
  in der Datenbank (angezeigt übersetzt), Audit, alles, was der NetBox-Sync nach NetBox schreibt
  (Beschreibungen, Tags), Versionen des Zonenplans, Server-Logs. Der Abgleich, das Einspielen und
  die Befunde laufen immer deutsch, egal in welcher Sprache sie ausgelöst werden. Neue
  gespeicherte Meldungen, die noch nicht in `orchestrator/app/store/run_text.py` stehen,
  erscheinen auch auf Englisch deutsch.
- **Agent und CLI (0.90.0):** Konsole und Log des Agenten folgen `AUTOMATO_LANG` in
  `C:\Automato\collector\agent.env` (`de` Standard, `en`; die Einrichtung der Oberfläche setzt
  `en` bei englischer Oberfläche). Was der Agent meldet, speichert Automato deutsch und zeigt es
  in der gewählten Sprache. CLI im Container: `docker compose exec orchestrator automato --lang en
  status --run-id <id>` (oder `AUTOMATO_LANG=en`); speichernde Befehle laufen deutsch.
- Begriffe in beiden Sprachen: `docs/GLOSSAR.md`; Fachbegriffe bleiben englisch.

**Datenqualität** (seit 0.91.0, ADR 0029): *Abgleich → Datenqualität* (bzw. Kachel in der
Übersicht) zeigt, wo Doku, Weg-Suche, Zonen und Kunden unsicher sind – je Bereich Quellsysteme,
Regeln, VMs, IPAM, Zonen & Kunden mit Ampel. Je Eintrag steht die Ursache mit Link in die Ansicht
zum Nachbessern (Läufe, Routing-Kontext, Regel, Findings, Netzwerkdesign, Kunden). „Prüfungen
ohne Befund zeigen“ blendet auch die erfüllten Prüfungen ein; ein oben gewählter Kunde schränkt
Regeln, VMs, Netze und Befunde auf seinen Ausschnitt ein.
- **Stand veraltet** (Befund `source_stale`, Betriebszustand): nach `AUTOMATO_STALE_DAYS` Tagen
  (Standard 7, `0` = nie) ohne erfolgreichen Lauf; mit Zeitplan mindestens der Abstand der
  Termine plus ein Tag (wöchentlich = 8 Tage). Der Zeitplan rechnet das stündlich nach.
- **Nur teilweise gelesen** (`source_partial`): Teilfehler aus der Laufmeldung und
  Routing-Kontexte „nicht lesbar“. Erreichbarkeit bzw. Leserechte prüfen und neu einlesen. Nur
  „Hinweise“ (Befehle ohne Ausgabe) erscheinen als Hinweis.
- Die Relevanz-Ursachen sind eine Stichprobe (höchstens 300 Firewall-Regeln, je Seite die erste
  Adresse); die Seite nennt ihre Grösse.

**Dokumentation**
- **Übersicht:** KPIs (Regeln, NAT/VIPs, offene Findings, Abdeckung gegen die SoT),
  **Letzte Läufe** und **Jetzt einlesen** (Operator): Mandant + Quellsystem wählen,
  Ausführung *Extern (Agent)* oder *Zentral (Worker)*. **Alle einlesen** legt für jedes aktive
  Quellsystem des Mandanten einen Lauf an (IPAM-Quellen zuerst; läuft/wartet schon einer oder
  fehlt der Lese-Zugang, wird das System mit Grund übersprungen, z. B. „Lauf läuft seit
  21.10. 09:12 (Agent „agent-01“)“). Laufende Läufe aktualisieren sich; ein Agent holt den
  Lauf im Wach-Modus innert rund 20 s ab.
  - **Hängende Läufe** (Agent beendet bzw. neu gestartet, Ergebnis-Upload verloren) beendet
    Automato selbst: laufend ohne Ergebnis nach `AUTOMATO_RUN_TIMEOUT_MINUTES` (Standard 120),
    nicht abgeholt nach `AUTOMATO_RUN_QUEUE_TIMEOUT_HOURS` (Standard 24).
  - **Wartet und wird nicht abgeholt (0.109.1):** die Meldung des Laufs sagt, wer ihn abholen
    muss – „wartet auf Agent „…“ (zuletzt gesehen …)“ bzw. „wartet auf den zentralen Worker“.
    Redis hält die Warteschlange zum zentralen Worker ohne Datenträger: wird der Container neu
    erstellt (Update, Neustart), sind wartende Aufträge weg. Automato schickt zentral gelesene
    Läufe deshalb nach `AUTOMATO_RUN_REDISPATCH_MINUTES` (Standard 5, `0` = aus) erneut
    („– erneut geschickt …“); gelesen wird trotzdem nur einmal. Antwortet der Worker nicht,
    zeigt das System → Verbindungen bzw. `deploy/aufbau.sh pruefen` unter **Zentraler Worker**
    (antwortet er, Aufträge in der Warteschlange, wartende Läufe) mit dem Befehl zum Starten
    (`docker compose --profile collectors up -d collector`) und den Logs.
  - Sofort geht es mit **Abbrechen** (Letzte Läufe) bzw. **Abbrechen & neu starten** im
    Dialog „Alle einlesen“ (Operator). Liefert der Agent danach doch noch ab, wird das
    Ergebnis eingespielt.
  - **Referenzquellen (NetBox, DDI) nur einmal zugleich (0.113.2):** „Jetzt einlesen“ während
    eines offenen Laufs antwortet „… wird schon gelesen: Lauf läuft seit … – abwarten bzw. den
    Lauf abbrechen“. Ist das Ziel eines zentralen Laufs nicht abrufbar, versucht der Worker es
    dreimal (2, 5, 15 s) und lässt den Lauf sonst stehen (erneut geschickt bzw. nach der Frist
    beendet) – er liest nie ohne Adresse.
  - „Letzte Läufe“ zeigt immer **alle offenen Läufe** (auch ältere), dazu die letzten acht und
    den Link **Alle Läufe (N)** (0.13.0).
- **Läufe** (0.13.0, seit 0.14.0 unter Administration): alle Collector-Läufe des Mandanten,
  seitenweise mit Gesamtzahl, neueste
  zuerst. Filter Status (**offen** = wartet oder läuft, wartet, läuft, fehlgeschlagen,
  erfolgreich), Quellsystem, Plattform, Auslöser (von Hand, Zeitplan). Je Lauf Ausführung
  (Agent/zentral, Zeitplan), **Einträge** (seit 0.78.5; Regeln, ohne Regeln die publizierten
  Dienste des NSX ALB, VMs des vCenters bzw. Geräte der Switches; beim IPAM die Datensätze),
  Status mit Meldung, angelegt/begonnen, beendet mit Dauer; offene Läufe **Abbrechen**
  (Operator). Offene Läufe auf der Seite aktualisieren sich alle 10 s. Beim NSX ALB steht seit
  0.78.5 auch bei Erfolg eine Zusammenfassung: Version, gelesene Avi-Tenants, Anzahl Virtual
  Services, VS VIPs, Pools, Pool Groups und Einträge (VIP × Port), dazu, was nicht lesbar war –
  zum Vergleich mit dem Controller. „Letzte Läufe“ in der Übersicht fragt seit 0.78.5 nie
  doppelt nach (nicht, solange die vorige Abfrage läuft) und behält die Liste bei einem
  Fehler beim Nachladen.
- **Firewall-Regeln:** Quelle/Ziel/Dienste mit den **Objektnamen wie auf der Firewall**;
  NAT-Kennzeichen je Regel (**SNAT** mit Pool bzw. Interface-IP, **VIP**; Klick → NAT-Detail).
  Quelle/Ziel umfassen auch Internet Services (ISDB), Benutzergruppen und ZTNA-Tags;
  **„nicht (negiert)“** markiert negierte Felder, **„kein Consumer · nicht wirksam“** einen
  ACI-Contract ohne Consumer bzw. Provider (auch als Befund „Regel nicht wirksam“).
  Die **Suche findet alles**, was zur Regel gehört: Regelname/-ID, Kommentar, Zonen, Aktion,
  Schutzprofile, Objekte **inkl. verschachtelter Gruppenmitglieder**, Services, Werte und IPs
  (eine IP trifft auch, wenn sie in einem Netz/Bereich der Regel liegt – „any“ ausgenommen),
  **DNS-Namen aus dem IPAM** (dort ohne Präfix, z. B. `web01.example.ch` für `H_web01`),
  EPG/AP/Tenant/BD/VRF, **VM-Namen** aus dem vCenter und NAT (VIP-/Pool-Name, externe/interne
  IP, Port). Mehrere Wörter =
  alle müssen vorkommen. Filter System, Quell-/Ziel-Zone, Aktion, **Familie** (IPv4, IPv6,
  IPv4+IPv6), **VDOM** (sobald es mehrere gibt; der VDOM steht unter dem System), „nur mit
  Findings“ (über alle Regeln, nicht nur die geladene Seite); Links
  `#regeln?object=<Name>` bzw. `#regeln?nat=<Name>` zeigen alle Regeln mit diesem Objekt (auch
  über Gruppen, beide Familien) bzw. NAT, `#regeln?object_id=<ID>` genau ein Objekt.
  **Suche nach einer IP mit Relevanz (0.26.0):** Ist die Suche genau eine IP, prüft Automato
  jede Firewall-Regel gegen das Routing ihrer Firewall, wie in der Doku. Regeln, die die IP
  nicht sehen können (z. B. DMZ-Netz am Ausgang internet), tragen „für <IP> nicht möglich“
  mit Begründung im Tooltip. „N routingtechnisch unmögliche ausblenden“ blendet sie aus.
  ACI-Contracts über externe EPGs (0.28.0) tragen die Zuordnung im ACI, z. B. „nur
  Routing“ für ein Subnetz ohne `import-security`.
  **Regeln je Subnetz:**
  - Objekte → Subnetze → „Regeln im Subnetz“ (`#regeln?net=<CIDR>`) zeigt Regeln, deren
    Quelle oder Ziel sich mit dem Subnetz überschneidet, mit Schalter „inkl. any“.
  - Ein Netz im Suchfeld (z. B. `10.20.0.0/25`) trifft genauso (ohne any).

  **IPv4/IPv6-Paare** (ADR 0016): Je Regel die Familie, und „↔ IPv6 #…“ bzw. „↔ IPv4 #…“
  markiert das Gegenstück auf derselben FortiGate.
  - Seit 0.36.0 steht die IPv6-Regel eines Paars direkt unter ihrer IPv4-Regel (auch über
    Seiten hinweg), mit „↳“. Seit 0.38.0 stehen beide in einem **Rahmen** (oben und unten
    durchgezogen, dazwischen gestrichelt), je Paar abwechselnd getönt, mit der Marke
    **„Paar“** – grün für gleich bzw. bestätigt, gelb „Paar?“ für Vorschläge („ähnlich“). So
    bleiben auch viele Paare nacheinander unterscheidbar. Die Reihenfolge der Firewall gilt
    sonst unverändert.
  - Grün: gleiche Objektnamen, Dienste und Interfaces.
  - Gelb „ähnlich“: Abweichungen im Tooltip.
  - Klick öffnet das Gegenstück; beim Überfahren wird es hervorgehoben. Beide bleiben eigene
    Regeln.
  - **Bestätigen bzw. ablehnen** (Operator, 0.12.0): im Detail „Als Paar bestätigen“ (für
    „ähnlich“) bzw. „Kein Paar“, mit „zurücknehmen“. Die Entscheidung gilt sofort und bleibt über
    neue Läufe erhalten (Policy-ID). Läuft gerade ein Einspielen bzw. Abgleich (Sperre des
    Mandanten), ist sie gespeichert und die Markierung folgt im Hintergrund – „Gespeichert – die
    Markierung folgt …“, das Detail lädt selbst neu (0.113.1). Filter „Paar“: ähnlich (offen) zum Abarbeiten, bestätigt,
    gleich, mit/ohne Paar.
  Klick → Detail: je Objekt Typ, Kommentar, Gruppenmitglieder (Objektnamen verlinkt) und je
  Wert die **Referenzdaten** aus dem IPAM (DNS-Name, Netz, VLAN, Standort; aufklappbar mit
  allen Feldern und Link in NetBox), die **VM** dahinter (vCenter: Name, Status, Portgruppe,
  EPG), dazu Dienste, Schutzprofile, Kommentar und **NAT** (VIPs/SNAT-Pools mit IPs, Ports und
  DNS-Namen). Seit 0.14.0 zeigen Regel-, Objekt- und NAT-Detail je VM alle Angaben aus dem
  vCenter (Abschnitt **VMs**: Status, Gast-OS, Hostname, IPs, Netzwerkkarte/MAC, Portgruppe →
  EPG, Cluster/Host, Datacenter, vCPU/RAM/Disk, Notiz).
- **Objekte & Inventar:** Adressobjekte (inkl. VIPs/IP-Pools), Gruppen, **Services**
  (FortiGate-Services und ACI-Filter `<Tenant>/FT_…` mit Protokoll/Ports, Quellports, ICMP,
  Kategorie, Timer, Ziel-Beschränkung bzw. Filter-Einträgen; Prüfung nach FT_-Schema) – mit
  Verwendung in Regeln/Gruppen und Befunden. **Klick auf ein Objekt** öffnet das Detail wie
  bei NAT: Attribute, Werte, Mitglieder, Befunde und die Firewall-Regeln, die es nutzen (auch
  über Gruppen), mit „In Regeln anzeigen“. „Objekt öffnen“ (z. B. aus NAT / VIPs) filtert
  seit 0.36.0 auch die Liste dahinter auf genau dieses Objekt („nur „…“ ✕“ hebt es auf).
  Dazu das Netzinventar aus FortiGate/APIC:
  **Subnetze** (mit Abgleich gegen führendes IPAM und Anreicherung, VLAN-Abweichung
  markiert), **VLANs** (inkl. EPGs), **Ports** (Modus, untagged/tagged VLANs, LAG/Parent,
  IPs, Zone/Policy-Gruppe, LLDP/CDP-Nachbar, EPGs), **Geräte**, **VRFs**. Filter Quelle,
  Suche (auch Mitglieder und DNS-Namen der Werte aus dem IPAM), „nur Abweichungen“.
  - Die **Regel-Zahl** je Objekt zählt die Regeln direkt und über (verschachtelte) Gruppen.
    Klick → genau diese Regeln.
  - **Typ** (0.36.0): Filter nach dem Typ wie auf dem Gerät (IP/Netz, Bereich, FQDN, Geo,
    **dynamisch (SDN-Connector)** – z. B. die Objekte der ACI-Integration über den
    FortiManager –, ACI-Filter …) mit Anzahl je Typ. Dynamische Objekte zeigen den Connector
    (`sdn`) im Detail (ab dem nächsten Lauf der FortiGate).
  - **Standardobjekte des Herstellers** (0.36.0): FortiOS-Services und -Gruppen (`HTTPS`,
    `ALL`, …) und die ACI-Filter in `common` (`default`, `arp`, `est`, `icmp`) stehen in
    Services und Gruppen zuunterst unter „Standardobjekte des Herstellers“, gedämpft und mit
    „Standard“ statt grünem Haken; „Standardobjekte ausblenden“ blendet sie aus. Verwendet eine
    erlaubende Regel ein solches Objekt (direkt bzw. über eine Gruppe), entsteht der Befund
    **„Standardobjekt verwendet“** – es folgt nicht der Konvention (statt `HTTPS` ein `FT_https`
    nach Konvention).
  - **IPv4/IPv6:** Gleichnamige Objekte stehen zweimal da, mit Chip „IPv4“ bzw. „IPv6“,
    eigenen Werten, Gruppen und Befunden, wie auf der FortiGate.
  - **VDOMs** (0.15.0): Gleichnamige Objekte zweier VDOMs sind ebenfalls zwei Objekte (VDOM in
    der Spalte Quelle). Gruppen, Regel-Zahl und Befunde gelten je VDOM; der Standard-VDOM
    `root` erscheint nur, wenn es mehrere VDOMs gibt.
  - **VIP-Ziele** (0.12.0): Die interne Ziel-IP einer VIP ohne eigenes Adressobjekt steht als
    „VIP-Ziel“ in den Adressobjekten (z. B. „VIP_web → 10.10.40.5“). Ein Adressobjekt hinter
    einer VIP (FQDN-VIP, Real Server, gleiche IP) trägt „Ziel von VIP …“. Beide zählen die
    Regeln der VIP mit.
  - **Ports je Gerät** (0.12.0): Gerät als Kopfzeile (Klick klappt auf/zu), Subinterfaces unter
    ihrem Parent („VLAN 10 getaggt über internal“), LAG-Mitglieder unter dem LAG.
    Seit 0.13.0 **kompakt**: eine Zeile je Port, lange VLAN-Listen als Bereiche bzw. Anzahl
    („tagged 24 VLANs“), EPGs als Anzahl. **Klick auf den Port** öffnet das Detail mit allen
    VLANs, EPGs, IPs, Zone, Policy-Gruppe, Nachbar, Beschreibung und Parent/LAG bzw.
    Subinterfaces/Mitgliedern (anklickbar).
  - **VMs** (vCenter, ADR 0017): Status, Netzwerk je NIC (Portgruppe mit VLAN → **EPG**, IPs
    mit DNS-Namen, „nicht im IPAM“), Cluster/Host/Datacenter, vCPU/RAM/Disk, Gast-OS und
    „Regeln der VM“. **Portgruppen** mit Switch, VLAN bzw. Trunk, Anzahl VMs und der
    zugeordneten EPG.
  - **Portgruppe im Detail** (Klick, 0.13.0): EPG mit Tenant/AP/BD/VRF/DN, **Subnetze** mit
    Quelle (ACI-EPG/BD/L3Out; Netzinventar mit gleichem VLAN nur als Hinweis, da VLAN-Nummern
    je Gerät/Domäne gelten; IPAM-Netz der VM-IPs) und „Regeln im Subnetz“, dazu die **VMs** mit
    Status, NIC/MAC, IPs mit DNS-Namen („nicht im IPAM“) und Cluster/Host.
  - Zuordnung Portgruppe → EPG: Name nach ACI-VMM-Schema `Tenant|AP|EPG`
    (z. B. `TE_x|AP_007|EG_dmz02`), auch ohne Präfixe (`EG_db01` = `EPG_db01`); sonst das
    VLAN, wenn es genau einer EPG gehört. Der Chip sagt, ob über Name oder VLAN.
- **NAT / VIPs:** VIPs (extern → intern, Port-Forward), **Virtual Server** und SNAT-Pools.
  Spalte **Prüfung** (0.14.0) mit den Befunden des VIP- bzw. Pool-Objekts wie in den Objekten
  (Namenskonvention, unbenutzt, nicht im IPAM) und „Ziel nicht im IPAM“ (Mapped-IP bzw. Real
  Server fehlt im führenden IPAM); Schalter **nur Abweichungen**. Im Detail dazu die VM
  hinter dem internen Ziel und „Objekt öffnen“.
  - Virtual Server (Lastverteilung) zeigen alle Real Server mit Status (aktiv/Standby/aus),
    Gewicht, Firewall-Objekt, DNS-Name und Health-Check. Im Detail stehen zusätzlich
    Server-Typ, Lastverteilung, Persistenz und die Health-Checks.
  - Alle Einträge mit **DNS-Namen**
  beider IPs aus DDI/NetBox, **Server** (Firewall-Objekt der internen IP), Interface,
  Kommentar und **Anzahl Regeln** (Klick → Regel-Liste). Suche über Name, IP (auch im
  Pool-Bereich), Port, DNS-Name, Server, Interface, Kommentar. Klick → Detail mit
  Einstellungen, IPAM-Sicht (Netz, VLAN, Standort) und den Firewall-Regeln, die den Eintrag
  nutzen.
  - **Kette über den Load Balancer (0.96.0):** Zeigt eine VIP der Firewall auf eine VIP des
    NSX ALB, steht unter dem internen Ziel „→ vs-… (Load Balancer)“, in „Ziel-Server“ die Server
    mit „über vs-…“; am Virtual Service „davor <externe IP> · <VIP>“. Im Detail „Kette über den
    Load Balancer“ bzw. „Davor: VIPs der Firewall“ – ein Klick auf den Namen öffnet den anderen
    Eintrag. Die Suche findet die Kette auch über Server-IP, externe IP, Virtual Service, Pool
    und VIP-Gruppe. „Export (CSV)“ speichert die Liste mit Suche und Filter, je Weg eine Zeile
    (`;`, UTF-8). Regel-Zahlen am Virtual Service und VIP-Gruppen wirken nach dem nächsten
    Einlesen der Firewall bzw. des NSX ALB (VIP-Gruppen mit dem Agenten ab 0.96.0).
  - **Namen der VIPs und IP-Pools (0.96.0):** der Abgleich prüft sie gegen die Präfixe ihres
    Typs (Namensprofile → Objekt-Präfixe → Typ „VIP“, „VIP / DNAT“, neu „Virtual Server“,
    „SNAT IP-Pool“); ein Adress-Präfix wie `H_` genügt an einer VIP nicht. Hat das Profil kein
    Präfix eines Typs, werden diese Namen nicht geprüft; NSX-ALB-Namen nie. Nach einer Änderung
    im Profil „Abgleich“ ausführen.
- **Parallele Firewalls (Verbund, 0.18.0, ADR 0020):** Firewalls mit denselben Regeln (z. B.
  zwei Cluster mit Session-Sync) bilden einen Verbund.
  - Gleiche Policies erscheinen in Regeln, Suche und Applikations-Doku nur einmal (führend: die
    Firewall mit dem kleinsten Namen, Chip „+ fw-b“).
  - Abweichende Policies stehen beide da („≠ fw-a: Ziel“), fehlende mit „fehlt auf fw-b“. Dazu
    gibt es Findings in der Kategorie „Parallele Firewalls“, auch für gleichnamige Objekte mit
    anderen Werten.
  - Setzen: über den Vorschlag oben in „Regeln & Contracts“ (bei Session-Sync bzw. vielen
    gleichen Policies, Admin) oder im Quellsystem-Dialog (Feld „Verbund“; leer = keiner).
  - Nicht parallel (0.19.0, Admin):
    - **Ablehnen** beim Vorschlag bzw. **Verbund auflösen** im Regel-Detail – die Firewalls
      werden nicht mehr vorgeschlagen.
    - „N abgelehnte Vorschläge“ → **wieder vorschlagen** hebt das auf.
  - Filter „Verbund: abweichend / fehlt“; mit Filter „System“ erscheinen die Regeln dieser
    Firewall vollständig.
  - **Policy-Routen, Routing und NAT (0.72.0):** Routing → Reiter **„Parallele Firewalls“**
    zeigt je Verbund (führend = erste Firewall) für Policies, Objekte, Policy-Routen & SD-WAN,
    Routen und NAT, wie viel gleich ist, abweicht bzw. auf einer Firewall fehlt; „Befunde“
    öffnet die Findings dieser Art.
    - *Policy-Routen/SD-WAN:* je VDOM über die Nummer (bzw. den Namen): Status, Bedingung,
      Aktion, Gateway, Ausgang (SD-WAN mit Reihenfolge der Mitglieder), Zonen.
    - *Routen:* je VDOM gegen die führende Firewall – wirksame Routen nach Protokoll und
      Interface, statische Routen mit Distanz, Interface, Gateway und Status. **Next-Hops zählen
      nicht** (jede Firewall hat eigene Router). Ein Befund je Firewall und VDOM; aufgeklappt
      eine Tabelle der Präfixe (anders / fehlt hier / nur hier, mit beiden Werten). Ist eine
      Tabelle auf einer Firewall nicht lesbar, wird sie nicht verglichen. BGP-Nachbarn werden
      nicht verglichen.
    - *NAT:* VIPs und IP-Pools je VDOM und Name (IPs, Ports, Typ, externes Interface, Real
      Server, Einstellungen).
    - Im Befund steht zuerst der Wert der Firewall des Befunds, dann der führenden; „Anzeigen“
      öffnet die Routen des VDOMs, die Policy-Routen bzw. die NAT-Ansicht.
- **Verlauf des Routings** (0.92.0, ADR 0030, Routing → Reiter „Verlauf“): Jeder Lauf einer
  FortiGate bzw. eines APIC vergleicht das Routing mit dem vorigen Stand und hält die Änderungen
  fest – Routen, BGP-Nachbarn, HA, Policy-Routen/SD-WAN, Kontexte, L3Outs. Der erste Lauf je
  Quellsystem ist der „Ausgangsstand“; bei bestehenden Installationen beginnt der Verlauf mit dem
  nächsten Lauf. Filter: Quellsystem, Kontext, Art, Änderung (hinzugefügt/entfernt/geändert/
  Vermerke), Zeitraum, Suche nach Präfix, Next-Hop oder Nachbar; „▸“ zeigt die Felder
  vorher/nachher, „#Lauf“ öffnet den Lauf in der Ansicht Läufe. Ist ein Teil nicht lesbar (z. B.
  HTTP 403), steht ein Vermerk „nicht lesbar“ mit der Anzahl nicht verglichener Einträge – nichts
  zählt als entfernt; beim nächsten lesbaren Lauf „wieder lesbar“. An einer Route zeigt
  „geändert <Zeit>“ die letzte Änderung (Klick öffnet ihren Verlauf); „Routing-Änderungen
  (7 Tage)“ in „Gelesene Kontexte“. Frist `AUTOMATO_RETENTION_TOPO_CHANGES_DAYS` (180 Tage,
  `0` = nie); Läufe haben eine kürzere Frist (90 Tage) – ältere Einträge zeigen nur noch die
  Kennung des Laufs. Nicht für Kunden-Benutzer; ein oben gewählter Kunde zeigt seinen Ausschnitt.
- **Routing** (0.16.0, ADR 0019; bis 0.34.0 „Routing & Wege“): oben die gelesenen Kontexte mit
  Anzahl Routen und dem, was nicht lesbar war (dort ist die Aussage „unbekannt“) – seit 0.75.0
  zugeklappt (Klick auf den Titel; die Zahl der Kontexte mit nicht Lesbarem bleibt sichtbar),
  damit die Reiter ohne Scrollen erscheinen. Allgemein seit 0.75.0: die Erklärung unter dem
  Titel jeder Seite ist eine Zeile, Klick klappt sie auf bzw. zu (gilt für alle Seiten, bleibt
  im Browser); in der Übersicht „Letzte Läufe“ und „Regeln je Zone“, in der Weg-Suche „Zonen“
  und „Anschluss (L2)“ zuklappbar. Bezeichnungen (0.17.0):
  - Eine FortiGate nur mit `root` heisst wie die **Firewall**, sonst „fw-01 · VDOM kunde-a“.
  - ACI: „leaf-101 · VRF Tenant:VRF“, im Weg und im Kontext-Filter die VRF über alle
    Border-Leafs („apic-01 · VRF Tenant:VRF“).

  Reiter: Routen, BGP-Nachbarn, HA & Session-Sync, Policy-Routen & SD-WAN, L3Outs, Anbindungen
  (unten). Den Weg einer Verbindung zeigt seit 0.35.0 die eigene Ansicht **Weg-Suche**:
  - **Weg-Suche** (0.17.0, eigene Ansicht seit 0.35.0): **Quelle** und/oder **Start** (Firewall,
    VDOM, ACI-VRF), dazu das **Ziel** – für Fehlersuche und die Kontrolle von Arbeiten.
    - **⇄ Richtung tauschen** (0.65.1): Quelle und Ziel tauschen (der Start ergibt sich wieder aus
      der neuen Quelle); war schon gesucht, zeigt die Weg-Suche gleich den Rückweg.
    - **Details am Weg (0.98.0):** ein Klick auf Policy, Contract, Route, Knoten, Interface, VIP
      bzw. Virtual Service oder Objekt am Weg öffnet das Detail rechts im Seitenpanel, die
      Weg-Suche bleibt stehen; „↗“ bzw. Ctrl-/Mittelklick öffnet die Ansicht in einem neuen Tab.
      „mehr“ in der Spalte # klappt je Hop Ein-/Ausgang mit Zone, Route mit Next-Hops,
      entscheidende Regel (Interfaces, Quelle, Ziel, Dienst), NAT/SNAT, Load Balancer, Zone und L2
      auf – je Firewall eines Verbunds. Die Suche steht in der Adresse (`#wege?src=…&dst=…&
      service=…`): Zurück aus einer anderen Ansicht, Neuladen oder ein weitergegebener Link
      zeigen sie wieder (neu berechnet).
    - **Ziel-Server einer VIP (0.114.1):** beim DNAT bzw. Load Balancer stehen alle Ziel-Server
      (höchstens 20) mit IP:Port, Name (VM, IPAM, Hostname), Zustand, Gewicht, Health-Check,
      Adressobjekt der Firewall (Klick öffnet es) und EPG der VM; „Weg“ markiert den Server, über
      den der gezeigte Weg führt. Dazu Methode, Health-Check, Bereich, Eingang und Kommentar der
      VIP bzw. Methode, Health-Monitore und FQDN des Virtual Service.
    - **Anbindung als Quelle bzw. Ziel (0.98.0):** im Feld z. B. „Internet“, „Anbindung“, den
      Namen eines Interfaces bzw. einer Firewall tippen und den Treffer unter „Anbindungen“
      wählen (von Hand erfasste immer, erkannte bei Art, Firewall, Interface bzw. Zone; mit Kunde
      oben nur an seinen Firewalls). Als Quelle beginnt der Weg an der Firewall der Anbindung
      mit ihrem Eingang (z. B. wan1) – so zeigt die Weg-Suche, welche Policy Verkehr von dort
      erlaubt. Geprüft wird das erste Netz der Anbindung als Ganzes bzw. bei Internet eine
      beliebige Adresse (Test-Adresse aus dem Dokumentationsbereich, „beliebige Adresse im
      Internet“). Die Karte der Anbindung zeigt Firewalls, Netze und Router; dort ein anderes
      Netz bzw. eine der Firewalls wählen (mehrere Firewalls = je ein Weg, Verbund als ein Hop).
      Als Ziel zeigt jeder Weg, ob er über die Anbindung hinausgeht („über die Anbindung“ bzw.
      Hinweis); das Routing lenkt die Anbindung nicht um.
    - **Warum dieser Weg? (0.109.0):** der Knopf an jeder Weg-Karte öffnet rechts den Weg Hop für
      Hop in Sätzen – Start (wo die Quelle angeschlossen ist), je Hop Eingang mit Zone und
      Routing-Tabelle, NAT, Policy-Routen bzw. SD-WAN-Regeln (passt sicher, möglich, keine
      passt), **alle Routen zum Ziel** in einer Tabelle (gewählt bzw. verworfen mit Grund:
      kürzeres Präfix, höhere Distanz, nur BGP-Tabelle, statische Route nicht aktiv, Policy-Route
      geht vor), der Next-Hop und wem er gehört, am Ende das Ergebnis mit Hinweis (fehlende
      Route, unbekannter Next-Hop mit IPAM-Name, Schleife, Default-Route nach aussen). Je Hop
      steht, wann das Routing des Geräts gelesen wurde (orange = alt). Grün = passt, orange =
      prüfen, rot = hier endet bzw. bricht der Weg. **„Erwartet über …“** wählt ein Gerät, über das
      der Verkehr gehen müsste: liegt es auf dem Weg, steht der Hop; sonst zeigt die Erklärung,
      wo der Weg an diesem Gerät vorbeigeht (direkt verbunden, Netz und Interface), welche Route
      über das Gerät es gäbe und warum sie verliert – bzw. dass sie fehlt –, und wie das Gerät
      selbst Ziel und Rückweg routet. Als Operator „+ Als Finding erfassen“ an jedem orangen bzw.
      roten Schritt (vorausgefüllt mit Satz, Hinweis, Gerät und Weg) bzw. „+ Finding erfassen“
      oben (frei). Rein lesend; erfasst wird nur in Automato.
    - **Quelle und Ziel als Name** (0.35.0): IP, Netz oder Name bzw. FQDN. Ab zwei Zeichen
      schlägt das Feld Treffer vor – DNS-Namen aus dem IPAM (auch nur das erste Label,
      `web01`), Adressobjekte der Firewalls (auch ohne Präfix, `web01` findet `H_web01`;
      FQDN-Objekte über das IPAM) und VMs (Name bzw. Hostname). Ein eindeutiger Name wird
      direkt aufgelöst; bei mehreren Treffern fragt die Suche nach („2 Treffer für „dup“ –
      bitte einen wählen“). Gesucht wird nur im Gelesenen (`GET /routing/resolve?q=`).
    - **Netze** (0.38.0): auch IPAM-Netze (Name, Beschreibung), Netz-Objekte der Firewalls,
      Subnetze des Inventars (FortiGate-Interface, ACI-BD/-EPG über Tenant, AP, EPG, BD) und
      ein Netz direkt (`10.20.0.0/24`); der Anfang einer Adresse (`10.20.0.`) sucht Hosts und
      Netze. Die Liste ist nach **Hosts** und **Netzen** gegliedert, oben der Umschalter
      Alle/Hosts/Netze mit Anzahl und „x von y“; **„Weitere laden“** holt alle weiteren
      Treffer seitenweise.
    - **Netz als Ganzes** (0.39.0): Ein Netz als Quelle bzw. Ziel („= Netz 10.10.0.0/23“)
      wird nicht über eine einzelne Adresse geprüft, sondern ganz:
      - **Routing:** es gilt die Route, die das ganze Netz enthält (z. B. `10.10.0.0/16`).
        Werden Teile des Netzes genauer über einen anderen Next-Hop geroutet, steht am Hop
        „Teile anders geroutet: 10.10.1.0/24 über …“ und oben ein Hinweis.
      - **Firewall-Regeln:** eine Policy passt sicher, wenn ihr Objekt das ganze Netz enthält –
        ein `10.10.0.0/23` ist mit der Regel `10.10.0.0/16` offen. Erfasst sie nur einen Teil
        (z. B. einzelne Hosts), ist sie „möglich (Quelle bzw. Ziel nur teilweise)“; das Urteil
        wird dann „abhängig“.
      - **ACI:** die EPG bzw. externe EPG, deren Subnetz das ganze Netz enthält; umfasst das
        Netz mehrere EPGs, ist die Zuordnung nicht sicher („Teile von …“).
      - Ein Netz als Quelle muss ganz in einem angeschlossenen Netz liegen, sonst den
        Startpunkt wählen. Für ein Netz gibt es kein DNAT (eine VIP ist eine Adresse).
    - **Dienst** als `tcp/443`, `udp/53`, `tcp`, `icmp` oder als Name eines Service-Objekts
      bzw. ACI-Filters (`FT_https`, `Tenant/FT_https`). Hat das Objekt mehrere Dienste, wird
      der erste geprüft; die übrigen nennt ein Hinweis.
    - Der Weg beginnt am Start bzw. dort, wo die Quelle angeschlossen ist (im ACI: im EPG-/
      BD-Subnetz). Je Weg eine Karte, Hop für Hop: Knoten, Eingang (Interface, Zone),
      Entscheidung (Route mit Präfix bzw. Policy-Route, die vorgeht) und Ausgang mit Next-Hop
      und Namen aus dem IPAM.
    - Blau heisst: die Policy-Route bzw. SD-WAN-Regel passt sicher. „möglich“ heisst: nur über
      Objekte bestimmt, der Weg folgt dann der Tabelle.
    - ECMP ergibt mehrere Wege. Der nächste Knoten ist der Besitzer der Next-Hop-Adresse. Ein
      gemeinsames Transfernetz allein reicht nicht (0.17.1), dort hängt meist ein Aussenrouter.
    - **Inter-VDOM-Links (0.27.0):** Geht eine Route ohne Next-Hop-IP über einen VDOM-Link
      (`<name>0`/`<name>1`, auch VLANs auf NPU-vlinks), führt der Weg im VDOM am anderen Ende
      weiter: „über VDOM-Link vl_root_dmz0 ↔ vl_root_dmz1“. Die Policies beider VDOMs werden
      mit dem Link als Aus- bzw. Eingang geprüft.
    - Das Ende steht oben in der Karte: Ziel erreicht, im Fabric, extern, Weg endet (Next-Hop
      ohne gelesenes Gerät), keine Route.
    - Ohne Quelle und Start (seit 0.113.1): der Hinweis „Quelle oder Startpunkt angeben“ – die
      Routen je Gerät stehen unter Routing → Routen. Geräte, deren Routing nicht gelesen ist
      (Switches, Router der Provider), beenden den Weg.
    - **Zonen** (0.57.0, wenn logische Zonen gepflegt sind): die Karte **Zonen** oben nennt
      Quell- und Zielzone und die Kette des Wegs mit den Übergängen („LAN →LAN-EINET→ Edge
      Internet →DMZ-EINET→ DMZ“), jeder Hop seine Zone, die Übersicht je Weg die Kette. Dazu die
      **Absicht** der Zonenmatrix für Quell- → Zielzone mit Urteil: „gegen die Absicht“, wenn der
      Weg erlaubt, was gesperrt ist bzw. einen Dienst, der bei „eingeschränkt“ nicht erlaubt ist;
      ohne Dienst in der Suche nur ein Hinweis. Knoten ohne Zone erscheinen als „ohne Zone“.
    - **L2** (0.64.0, wenn Switches gelesen sind): die Karte **Anschluss (L2)** nennt, wo Quelle
      bzw. Ziel hängen (Switch, Port, VLAN aus MAC-Tabelle/ARP), und den Weg von der Quelle zum
      ersten Hop; im Ausgang eines Hops die Zeile „L2: fw-08 dmz → sw-acc-01 (Te1/1/1 → Po1) →
      …“ – der Weg über die Switches bis zum nächsten Hop bzw. bis zum Port des Ziels, im VLAN
      des Ausgangs (VLAN-Subinterface → physischer Port bzw. Aggregat). Fehlen die Kabel, bleibt
      die Weg-Suche wie bisher.
    - **Redundante Wege (0.19.0):**
      - „Wege im Überblick“ zeigt alle Wege mit Art: aktiv · ECMP, redundant über einen
        Verbund paralleler Firewalls, oder Backup ab einem Knoten mit Grund (BGP-Backup mit
        AS-Pfad bzw. statische Backup-Route mit Distanz).
      - Je Weg stehen die Knoten nacheinander, die Next-Hops, die Anzahl Hops und das Ende.
      - Die Auswahl „Backup“ steuert, wie viel länger ein Backup-Weg sein darf: gleich lang,
        bis +1 Hop (Standard), bis +2 Hops, ausblenden.
      - **Nur, was das Gerät gelernt hat (0.112.0):** kein Backup über die Default-Route für ein
        privates Ziel (das Gerät kennt das Netz nicht – auch wenn die Firewall dahinter es
        lernt), kein BGP-Bestpfad als Backup und keine statische Route auf ein Adressobjekt
        (`dstaddr`). Fehlt ein erwarteter Backup-Weg, fehlt dem Gerät die Route dafür.
      - **Gegen die BGP-Filter (0.114.0):** ein BGP-Backup, das der Eingangsfilter des Geräts
        bzw. der Ausgangsfilter der Nachbar-FortiGate sperrt, ist kein Weg; der Hinweis unter
        dem Weg sagt „Backup an … über … verworfen: Eingangsfilter … sperrt …“.
      - Die Spalte „Länge“ (0.20.0) sagt je Weg „gleich lang“ bzw. „+1 Hop“ gegenüber dem
        kürzesten Weg mit Ziel.
      - **Verbund als ein Hop (0.29.0):** Die Firewalls eines Verbunds (z. B. Session-Sync-
        Paar) sind im Weg ein Hop „Verbund Nord“ statt zwei Wege.
        - Je Firewall steht eine Zeile mit Eingang, Ausgang und Regeln. Der Hop davor zeigt
          die Next-Hops zu beiden.
        - ECMP über verschiedene Router bleibt je Weg.
        - Urteilen die Regeln der Firewalls verschieden, steht der Weg auf „abhängig“ mit
          „im Verbund verschieden“.
    - **Regeln auf dem Weg (0.22.0):** Je Firewall bzw. FortiGate-VDOM steht in der Spalte
      „Regeln“, welche Policy den Verkehr erlaubt oder sperrt.
      - Die erste sicher passende Policy entscheidet, wie auf der Firewall. Ohne passende ist
        der Verkehr implizit gesperrt.
      - Im Feld „Dienst“ (z. B. `tcp/443`) lässt sich der Dienst angeben. Ohne Dienst ist nur
        eine Policy mit `ALL` sicher.
      - „möglich (…)“ nennt, was sich nicht prüfen lässt: Eingang/Quelle bei gewähltem Start,
        FQDN/ISDB, Benutzer/Gruppen, Zeitplan, Dienst. Kann eine solche Policy vor der
        entscheidenden greifen, ist der Hop „abhängig“.
        - Seit 0.112.0 steht der Grund im Urteil des Wegs („abhängig an fw-x (Policy 3 … kann
          vor Policy 7 … greifen – abhängig von: Ziel)“); in den Verbindungen Ende zu Ende bei
          mehreren aktiven Wegen jeder Weg mit seinem Urteil.
        - Länder (Geografie-Objekte) und Internet Services (FortiGuard) treffen keine private
          Adresse – eine Sperre „Land X“ zuoberst macht internen Verkehr nicht „abhängig“;
          eigene Internet Services bleiben „möglich“.
        - Startpunkt gewählt und die Quelle hängt dort nicht an: der Eingang ist das Interface
          der Route zur Quelle („gewählt, Eingang lan nach der Route zur Quelle“).
      - Seit 0.78.4 steht je Policy, **über welches Objekt** Quelle und Ziel passen
        („Quelle any, Ziel G_darknet“), und unter **„dahinter, nicht erreicht“** bis zu fünf
        weitere passende Policies nach der entscheidenden – z. B. hinter einer Sperre
        zuoberst („Default Block …“). Steht dahinter eine erlaubende, nennt das Urteil sie
        („gesperrt durch Policy 9 … – dahinter würde Policy 20 … erlauben (nicht erreicht)“).
        Sperrt die Policy zuoberst unerwartet, zeigt „über …“ das Objekt, das passt: im
        Objekt prüfen, ob es das Ziel wirklich enthält.
      - Dienste stehen mit dem Objektnamen (`FT_https`, `HTTPS`) wie in Regel-Liste,
        Regel-Detail und Applikations-Doku (0.35.0); die Werte (`tcp/443`) zeigt das Detail.
      - Je Weg steht das Gesamturteil („gesperrt an fw-x“, „erlaubt (2 Firewalls, 1 ACI-VRF)“).
        Ein Klick auf „Policy n“ bzw. „Contract …“ öffnet die Regel.
    - **ACI-Contracts auf dem Weg (0.35.0):** Je ACI-VRF-Hop steht, wie das Fabric Quelle und
      Ziel zuordnet und welcher Contract entscheidet.
      - Zuordnung im Fabric – seit 0.46.0 **Konfiguration vor Gelerntem**: statischer
        Endpunkt (fvStCEp), Portgruppe der VM (vCenter), dann gelernter Endpunkt bzw. Server
        der EPG, EPG-Subnetz, BD-Subnetz mit genau einer EPG. Die Herkunft steht dabei („EPG
        TE/AP/EG_a (Portgruppe der VM vm-a)“). Sonst die externe EPG über das längste
        Subnetz mit `import-security` (wie in der Doku, 0.28.0). Erfasst keine EPG die IP,
        verwirft das Fabric den Verkehr.
      - Gleiche EPG: ohne Contract erlaubt. Taboo-Contracts des Ziels sperren zuerst. Sonst
        erlaubt ein Contract mit der Quelle als Consumer und dem Ziel als Provider – direkt,
        über vzAny der VRF, eine ESG oder die Preferred Group – mit passendem Filter. Kein
        Contract: im ACI gesperrt. Stimmen Consumer und Provider, öffnet der Contract den Dienst
        aber nicht (0.111.0), heisst es „Contract CT_x von … zu … öffnet tcp/80 nicht (offen:
        tcp/443) – im ACI gesperrt“; der Contract steht mit „öffnet den Dienst nicht“ bei den
        Regeln des Hops.
      - Ohne Dienst ist nur ein Filter `ip/any` sicher, sonst „möglich“. Ordnet das ACI die
        Quelle nicht eindeutig zu (z. B. 0.0.0.0/0 in zwei L3Outs), ist der Hop „abhängig“,
        wenn die Kandidaten verschieden ausgehen.
      - **ACI ist zustandslos (0.69.1):** Filter ohne Ports (`ip`, `tcp/any`, ICMP) gelten auch
        vom Provider zum Consumer („↩ Rückrichtung“; Regel-Detail und Doku nach Kategorie seit
        0.112.0 „↔ auch vom Provider aus: icmp/any“ mit genau diesen Diensten); mit Zielport
        (`tcp/443`) bzw. `stateful` kommen zurück nur Antworten. Grund: ein Subject mit „Apply
        Both Directions“ (Standard) wendet seine Filter auch rückwärts an, ohne Ports ist das
        derselbe Verkehr. Nur in eine Richtung: „Apply Both Directions“ ausschalten bzw. Filter
        mit Zielport.
      - **Nur Antworten (0.72.1):** Lassen die Filter eines Subjects vom Consumer zum Provider
        nur Antworten zu (Eintrag nur mit Quellport, z. B. 443, bzw. TCP ohne SYN), greift der
        Contract in dieser Richtung nicht – das Urteil ist „gesperrt“ statt „möglich“, der
        Contract steht mit „greift nicht: … nur Antworten“ dabei. Zurück (vertauschte Ports)
        kann er gelten: Regel-Detail „← nur Provider → Consumer“. Gilt so auch in der
        Applikations-Doku (nur die Zeile der Rückrichtung), in den Verbindungen Ende zu Ende
        und in der Zonenmatrix. Erst ab einem Lauf mit 0.69.1 (Richtung je Subject).
      - **Zwischen VRFs (0.44.0):** Liegt das Ziel in einer anderen VRF, die es in diese leakt,
        endet der Weg im Fabric: „Ziel im Fabric der VRF T2:V2 … – Shared Service …“ bzw.
        „Route-Leaking …“, am Hop „→ VRF …“. Geleakt ist ein Subnetz, wenn
        - es geteilt ist (`fvSubnet.scope` mit `shared`, an der EPG bzw. ihrem BD) und ein
          Contract mit Scope `global` (über Tenants) bzw. `tenant` die beiden VRFs verbindet
          (Shared Service), oder
        - die VRF es per Route-Leaking (`leakRoutes`, ACI 5.0+) in diese VRF gibt.
        Dann gilt im Ziel seine EPG der anderen VRF, und der Contract braucht einen Scope über
        die VRF hinaus. Ohne Leak kennt die VRF das Ziel nicht; der Weg folgt ihrem Routing
        (L3Out, Firewall) – wie das ACI.
      - Nicht berücksichtigt: VRF „unenforced“, Intra-EPG-Isolation, ESG-IP-Selektoren,
        Service Graphs.
    - **NAT auf dem Weg (0.23.0):**
      - Ist das Ziel die externe IP einer VIP, steht am Hop „DNAT VIP_x: extern → intern“.
        Der Weg geht zur internen IP weiter, der nächste Hop zeigt „Ziel …“.
      - Bei Port-Weiterleitung gilt für die Policy der interne Port (Dienst mit dem externen
        Port eingeben, z. B. `tcp/443`).
      - **Load Balancer (NSX ALB, seit 0.77.0, ADR 0027):** Ist das Ziel die VIP eines Virtual
        Service und ist sie an einem Knoten angeschlossen (Interface-Netz der FortiGate bzw. im
        ACI-Fabric), steht dort „Load Balancer vs-…: VIP:Port → Server:Port“ (Pool, Zustand). Der
        Weg geht am selben Knoten vom Netz der VIP zum ersten aktiven Server weiter (Hop „über
        Load Balancer …“). Quelle für die Regeln danach ist die Service Engine – ihre Adresse
        liest Automato nicht, die VIP steht für ihr Netz – bzw. der Client, wenn das Application
        Profile die Client-IP erhält; Port ist der Port des Servers. Der Dienst (`tcp/443`) wählt
        den Virtual Service mit diesem Port, ohne Dienst gilt der erste.
      - **VIP der FortiGate vor dem Load Balancer (seit 0.79.0):** Zeigt eine VIP der FortiGate
        auf die VIP eines Virtual Service, steht am DNAT-Hop „NSX ALB vs-…“ und der Weg geht von
        der öffentlichen IP über die FortiGate und den Load Balancer zum Server. Das gilt auch,
        wenn die VIP des Load Balancers hinter einem Router ohne gelesenes Gerät liegt.
      - SNAT der erlaubenden Policy steht als „SNAT → IP (Pool …)“. Diese Quelle gilt für die
        Firewalls danach. Seit 0.47.0 genauer:
        - *IP-Pool:* `overload`, `one-to-one` und `port-block-allocation` – die Quelle danach
          ist der ganze Bereich des Pools (als Netz, wenn er eines ist; welche IP, entscheidet
          die FortiGate). Policies danach sind damit sicher, wenn sie den ganzen Bereich
          erlauben, sonst „nur teilweise“. `fixed-port-range` – die IP, die der Quelle fest
          zugeordnet ist (interner Bereich gleichmässig auf den externen verteilt).
        - *IPv6:* Pools aus `ippool6` (`nat6`/`poolname6` bzw. die Felder von `policy6`), ohne
          Pool die IPv6-Adresse des Ausgangs.
        - *VIP mit `nat-source-vip`:* Verkehr von der internen IP der VIP geht mit ihrer
          externen IP hinaus – vor dem NAT der Policy („SNAT → … (VIP …, nat-source-vip)“).
        - *Pool fehlt:* „Pool … nicht gelesen – Quelle danach unbekannt“, Policies danach nur
          „möglich“; dazu der Befund *SNAT-Pool fehlt* (Findings → Regeln).
        - Central-NAT ist bewusst nicht abgebildet (Entscheid des Anwenders).
  - **Routen:** wirksam (RIB), BGP-Pfade (bester bzw. Backup) und statische Routen (aktiv bzw.
    inaktiv); Suche, „enthält IP“ (längstes Präfix zuerst), Filter Tabelle und Kontext.
  - **BGP-Nachbarn** (mit Name aus dem IPAM und, falls gelesen, dem Knoten dahinter; seit
    0.113.0 mit **Filter (ein / aus)** und **Soll/Ist**: erwartet = was die Eingangsfilter
    ausdrücklich erlauben, mit „nichts angekommen“; erhalten = BGP-Pfade vom Nachbarn mit
    „erlaubt/gesperrt/unbekannt“ nach dem Filter; angekündigt = berechnet aus besten Pfaden,
    network, aggregate und redistribute durch die Ausgangsfilter – die FortiGate meldet es nicht;
    seit 0.114.1 mit `default-originate` (0.0.0.0/0, Chip) und ohne Routen, deren AS-Pfad das AS
    des eBGP-Nachbarn schon enthält (Schleifenschutz, gezählt).
    Darüber aufklappbar die gelesene BGP-Konfiguration je Firewall mit konfigurierten Nachbarn
    ohne Sitzung. Abweichungen stehen unter Findings → BGP-Filter. Seit 0.114.0 im Soll/Ist
    zusätzlich **„Soll aus den Zonen dahinter“**: Zone am Nachbarn und alle Zonen, die über ihn
    erreicht werden (Netzwerkdesign → Zonen mit Geräten), ihre Netze und Ausschlüsse als
    Prefix-Liste, „fehlt im Filter“ / „zu offen“ und ein FortiOS-CLI-Vorschlag zum Kopieren – nur
    Text, eingespielt wird auf der Firewall von Hand), **HA &
    Session-Sync** (Modus, Gruppe, Mitglieder mit Rolle, FGSP-Peers), **Policy-Routen &
    SD-WAN** (Bedingung, Weg, Mitglieder nach Vorrang), **L3Outs (ACI)** (VRF, Border-Leafs mit
    Namen, Router-ID und statischen Routen, Interfaces/SVIs mit VLAN und Adressen, BGP-Peers
    mit Namen; seit 0.113.0 **Route Control**: erzwungen (export, import), Anzahl Subnetze
    mit import-/export-rtctrl, „Soll/Ist“ mit Import erwartet/erhalten und Export).
    - **Firewall dahinter (0.97.0, ADR 0034):** Spalte „Firewall dahinter“ je L3Out – die
      Firewall (VDOM, Interface, Zone) mit Herkunft abgeleitet / bestätigt / von Hand / keine;
      abgeleitet aus dem Transfernetz (SVI im Netz eines Firewall-Interfaces) bzw. BGP-Peer oder
      statischem Next-Hop auf eine Firewall-Adresse. Operator: „bestätigen“, „ändern“ (Firewall
      und Interface wählen bzw. „keine Firewall“), „zurücksetzen“ (wieder abgeleitet). „Nicht
      erkannt“ = kein Firewall-Interface im Transfernetz bzw. als Peer – von Hand zuordnen,
      sonst gilt für diesen L3Out weiter das ACI. Befunde der Applikationen und Zonen sowie die
      Kunden ziehen danach von selbst nach.
    - **Transfernetz (0.111.0):** das Netz am Interface der Firewall zu diesem L3Out gehört zu
      ihm („hinaus“), auch wenn die Firewall es direkt angeschlossen hat – der Weg ins
      Transfernetz läuft nicht über einen anderen L3Out.
  - **Namen aus dem IPAM:** DNS-Name bzw. Name der IP aus DDI/NetBox. Ohne Eintrag zur IP
    erscheint blass das kleinste IPAM-Netz mit Beschreibung. Für Transfernetze lohnt sich
    deshalb eine Beschreibung im IPAM (z. B. „Transfer ISP B“).
  - **Anbindungen** (0.18.0, Operator): Anbindungen von Hand erfassen, über die Wege nach
    aussen gehen.
    - Felder: Art (Internet, MPLS, WAN, Partner), Next-Hop-IPs der Gegenstelle und/oder
      Interfaces/Zonen, Netze dahinter, manuelle Routen (Präfix → Firewall/VDOM bzw. VRF, bei
      der der Weg weitergeht).
    - Firewalls (0.20.0): eine oder mehrere, z. B. beide Session-Sync-Partner; keine gewählt =
      alle.
    - Ohne Anbindung endet ein Weg am ersten Next-Hop ohne gelesenes Gerät („Weg endet“).
      Dort hilft der Knopf „Als Anbindung erfassen“: Er füllt die ganze erkannte Anbindung
      vor, mit allen Routern und Firewalls.
    - **Erkannte Anbindungen** (0.20.0): Automato listet die Router ohne gelesenes Gerät, aus
      Routen, BGP-Pfaden, statischen Routen und BGP-Nachbarn.
      - Je Router: aktiv bzw. Backup, AS und BGP-Zustand, Default-Route, Firewalls.
      - Zusammengefasst je Firewall bzw. Verbund/Session-Sync-Partner über gemeinsame Router,
        ECMP, Zone bzw. Interface und BGP-AS.
      - „übernehmen“ legt die Anbindung an. „ergänzen“ erweitert eine, die schon einen Teil
        abdeckt. Beide springen hoch zum Formular.
      - „ausblenden“ (0.21.0): kein Vorschlag mehr für diese Router, für alle Benutzer – bis
        ein neuer Router dazukommt. „N ausgeblendete Vorschläge“ zeigt sie mit „wieder
        anzeigen“.
      - Die Vorschläge stehen zu unterst, unter den erfassten Anbindungen.
    - Im Formular stehen die erkannten Router zum Ankreuzen.
    - In der Liste zeigt „Erkannte Router · Redundanz“, was die Anbindung abdeckt. „nicht
      eingetragen“ markiert erkannte Router, die noch fehlen; ein Knopf übernimmt sie.
    - Danach in der Weg-Suche einfach neu suchen. Das Ende nennt bei mehreren Routern
      „Router n von N“. „Als Anbindung erfassen“ in der Weg-Suche öffnet das Formular hier.
    - **Netze dahinter als Liste** (0.111.0, bis 5 000): eine Liste einfügen, je Zeile bzw. mit
      Komma, in jeder Schreibweise (`10.80.0.0/16`, `10.81.0.0 255.255.0.0`, Wildcard
      `0.0.255.255`, Text dazwischen wird übergangen).
      - „Prüfen“ zählt gültige, doppelte und ungültige Einträge; „Zusammenfassen“ fasst
        angrenzende Netze zusammen (z. B. vier /24 zu einem /22).
      - „Aus dem Routing“ schlägt die Routen vor, die über die Next-Hops bzw. Interfaces der
        Anbindung gelernt sind (ohne Default-Route), zum Übernehmen. Die grossen Netze (z. B.
        10.0.0.0/8 hinter dem MPLS) bleiben von Hand einzutragen – sie entscheiden, wohin ein
        grosses Netz einer Regel gehört (unten).
      - „Aus den BGP-Filtern“ (0.113.0, ADR 0041) schlägt die Netze vor, die die
        Eingangsfilter für diese Next-Hops ausdrücklich erlauben (Prefix-Listen und Route-Maps
        der FortiGate, import-rtctrl der L3Outs mit diesem Peer) – ohne „alles“-Regeln und
        Default-Route; oft genau die grossen Netze dahinter.
      - Speichern mit ungültigen Einträgen nennt sie und speichert nicht.
    - **Grosse Netze der Regeln** (0.111.0, ADR 0040): routet eine Firewall ein Netz einer Regel
      (z. B. 10.0.0.0/8) nicht als Ganzes – Teile laufen über genauere Routen, oder es bleibt nur
      die Default-Route (private Netze) –, gehört es zu, in dieser Reihenfolge:
      1. der Zuordnung von Hand (Operator): zur Zone …, über die Anbindung …, „Weg nach dem
         Routing stimmt“ bzw. „nur die genauer gerouteten Teile zählen“ (die Verbindung über das
         ganze Netz fällt weg, die Teile bleiben);
      2. der Zone, die das ganze Netz enthält (Netzwerkdesign);
      3. der Anbindung, deren Netze dahinter es enthalten.
      Sonst bleibt es „aufgeteilt“ bzw. „unklar“: Befund **„Netz der Regel grösser als die
      Routen“** (Findings → Topologie) mit Route, Next-Hop, Teilen und Regeln, dort „Netz
      zuordnen“. In den Verbindungen Ende zu Ende steht der Zustand als Chip mit „zuordnen“ bzw.
      „ändern“. Die Liste „Grosse Netze der Regeln“ hier unter Anbindungen zeigt die Zuordnungen
      von Hand; „ändern“ öffnet den Dialog mit „Zuordnung entfernen“.
  - **Topologie** (0.50.0, ADR 0019, `GET /topology`): das Modell der Umgebung als Liste.
    - Knoten: Firewalls bzw. VDOMs, ACI-VRFs (alle Border-Leafs), Segmente (ein Netz mit mehr
      als zwei gelesenen Geräten), Anbindungen (von Hand) und Router ohne gelesenes Gerät
      (beim ACI nur an den L3Outs). Je Knoten Mitglieder, HA, Verbund und die Zahl der Befunde.
    - Klick auf einen Knoten zeigt seine Verbindungen: Transfernetz (gemeinsames Netz der
      Interfaces bzw. L3Out-SVIs), Inter-VDOM-Link, BGP-Sitzung, „nach aussen“ (Router bzw.
      Anbindung mit aktiv/Backup, AS, Interface), manuelle Route, Verbund, Session-Sync – mit
      Netz, Adressen, den Routen darüber (→ hinaus, ← herein, Backup) und der Herkunft
      (abgeleitet, von Hand, bestätigt).
    - **Plausibilität** (Findings → Topologie): doppelte IP im selben Netz, abweichende
      Netzmaske der Gegenstellen, Routing-Schleife zwischen zwei gelesenen Geräten, statische
      Route ohne Wirkung (eingeschaltet, nicht in der RIB, keine andere Route dafür),
      BGP-Sitzung nicht aufgebaut. Nach jedem Lauf neu berechnet.
    - Nicht für Kunden-Benutzer (zeigt die ganze Umgebung). Zeichnung und Export folgen
      (0.52.0), Korrekturen im Designer später. Die logischen Zonen hängt der Zonenplan an
      diese Knoten (Netzwerkdesign, 0.51.0).
  - **Routing bis an die Firmengrenze** (0.99.0, ADR 0032): Router, Core- und WAN-Router als
    Quellsystem „Switches & Router“ erfassen (Adressen im Lesebereich, Read-only-Konto wie bei
    den Switches – NX-OS `network-operator`, Junos `read-only`, Arista `network-operator`, IOS
    privilege 1 bzw. Parser-View mit den Routing-Befehlen des Lesevertrags). Gelesen werden nur
    `show`-Befehle der Positivliste: RIB je VRF (IPv4/IPv6), BGP-Nachbarn und -Pfade.
    - Der Lauf meldet „Routing: n L3-Geräte, m Kontexte, k Routen“; fehlende Rechte bzw.
      gekürzte Tabellen (Obergrenze 20 000 Routen je Kontext, Tabelle und Familie – Default-Route
      und private Netze bleiben zuerst) stehen als Hinweis und im Routing unter „nicht lesbar“.
    - Routing → Kontexte zeigt die Router als „Router/L3-Switch“ (je Gerät und VRF,
      Management-VRFs fallen weg); die Weg-Suche geht durch sie hindurch bis zur
      **Firmengrenze**: eBGP zu einem fremden AS, eine Anbindung der Art Internet, MPLS bzw.
      Partner oder eine Default-Route über eine öffentliche Adresse bzw. ein WAN-Interface –
      Chip „Firmengrenze · AS …“ am Weg und am letzten Hop.
    - Routing → Topologie: Knotenart „Router (gelesen)“, Filter „Firmengrenze“ zeigt Grenzknoten
      und Provider. Partner- bzw. MPLS-Übergänge ohne BGP als Anbindung der Art Partner/MPLS
      erfassen, damit sie als Grenze gelten. Erkannte Anbindungen gibt es nur noch für nicht
      gelesene Router.
    - Kunde oben: Router-Kontexte sind geteilt und gehören zum Ausschnitt, wenn sie ein Netz des
      Kunden routen; Kunden-Benutzer sehen in der Doku „Router“. Der Verlauf des Routings nimmt
      die Router mit.
- **Verkabelung** (0.62.0, ADR 0025): welches Gerät an welchem Port welches anderen hängt –
  abgeleitet aus CDP/LLDP aller gelesenen Geräte (Switches & Router, ACI-Leafs), nichts von Hand.
  Seit 0.68.0 auch Catalyst 1200/1300 und Juniper MX/EX/QFX; ein MX steht als **Router** da
  (in der L2-Sicht ein Endgerät, kein Switch), seine Interface-Netze im Inventar. Seit 0.94.0
  auch **Arista EOS** (Konto mit Rolle `network-operator`) und **HPE Aruba CX/AOS-CX** (Konto
  in der Gruppe `operators`) – einfach die Adressen im Quellsystem eintragen, die Plattform
  erkennt Automato an `show version`. ArubaOS-Switch/ProCurve wird nicht gelesen; der Lauf
  meldet „ArubaOS-Switch/ProCurve wird nicht gelesen“. Endpunkte zeigen neben IPv4 auch
  **IPv6-Adressen** aus den IPv6-Nachbarn (NDP) der Switches; Link-local-Adressen stehen
  getrennt („link-local …“) und zählen nicht als Identität. „Wo hängt …“ findet Geräte auch über
  eine IPv6- bzw. Link-local-Adresse. Ältere Software ohne den IPv6-Befehl: kein Hinweis, der
  Teil bleibt leer.
  Oben die Kennzahlen (gelesene Geräte, Switches, Verbindungen je Zustand, Abweichungen,
  Gegenstellen ohne CDP/LLDP, seit 0.63.0 Endpunkte), darunter die Reiter:
  - **Plan** (0.64.0, ADR 0025 Nr. 9): die L2-Sicht als Zeichnung – oben der Kern (Spines bzw.
    die meistvernetzten Switches), darunter je Abstand die weiteren Switches, Endgeräte (ESXi,
    Firewalls, nicht gelesene) unter ihrem obersten Nachbarn; Linien je Gerätepaar, dicker bei
    mehreren Kabeln („2×“), rot mit Abweichungen. „Endgeräte“ blendet sie aus.
    - **Ausfall simulieren:** Klick auf ein Gerät bzw. eine Linie mit einem Kabel – „Fällt … aus,
      ist getrennt: …“, getrennte Geräte blass. Durch Endgeräte führt kein Weg (ein ESXi-Host an
      zwei Switches verbindet diese nicht).
    - **VLAN hervorheben:** Linien grün (lässt das VLAN durch), rot gestrichelt (sperrt es),
      orange (nur ein Teil der Kabel), grau gepunktet (unbekannt); Geräte, die das VLAN nutzen
      (verbundene Access-Ports, SVI, Firewall-Subinterface), grün umrandet; darüber „durchgehend“
      bzw. „getrennt in N Inseln“.
    - Seit 0.65.0 (ADR 0025 Nr. 10): **vPC-Paare** nebeneinander im gestrichelten Rahmen
      „vPC-Domäne n“ (zusammen ein logischer Switch). **Spanning Tree**: Kabel mit blockierten
      VLANs gestrichelt und beschriftet („STP 30“), mit VLAN violett gestrichelt = *Reserve*
      (Spanning Tree blockiert das VLAN hier; fällt der aktive Weg aus, übernimmt es). Die
      Inseln eines VLANs zählen nur die Konfiguration, ein blockiertes Kabel trennt nicht.
    - **Mehrere Ausfälle gleichzeitig** (0.65.0): **Shift-Klick** nimmt weitere Geräte bzw.
      Kabel hinzu („Fallen A und B aus …“), nochmals Shift-Klick nimmt sie wieder heraus. Bei
      einer Linie mit mehreren Kabeln fallen mit Shift-Klick alle aus (z. B. ein ganzer
      Port-Channel); der einfache Klick meldet nur, dass die anderen Kabel bleiben.
    - **Export** (0.65.0): *draw.io* bzw. *SVG* – der Plan wie gezeigt (mit bzw. ohne Endgeräte,
      mit VLAN), Datei `verkabelung-<Datum>[-vlan<n>]`. In draw.io bleiben Geräte und Kabel mit
      `automato_*`-Eigenschaften (Ports, Kabelzahl, STP) erhalten und lassen sich weiter
      bearbeiten.
  - **Verbindungen:** je Zeile beide Enden (Gerät, Port, Beschreibung, Speed/Duplex, Modus/VLAN)
    mit Zustand **beidseitig** (beide melden einander), **einseitig** (die Gegenseite wird
    gelesen und meldet Nachbarn, aber diesen nicht – z. B. CDP/LLDP am Port aus) bzw.
    **Gegenstelle ohne CDP/LLDP** (Firewall, Server oder Gerät, das Automato nicht liest).
    Filter Gerät, Zustand, „nur Abweichungen“ und Suche (Gerät, Port, Beschreibung).
    Abweichungen zuerst.
  - **Geräte:** gelesene Geräte und die nur von Nachbarn gemeldeten, mit Ports (verbunden),
    Verbindungen und Abweichungen; ein Klick öffnet die Verbindungen bzw. Ports des Geräts.
    ESXi-Hosts aus dem vCenter erscheinen, wenn ein Switch sie meldet.
  - **Ports:** alle Ports der gelesenen Switches (Status, Speed, Duplex, Modus, VLAN bzw.
    Native VLAN und erlaubte VLANs, Port-Channel, vPC, Beschreibung, Gegenstelle); Filter
    Gerät, Status (up/down/abgeschaltet), VLAN, Suche. Seit 0.63.0 die Anzahl **Endpunkte**
    (MACs) je Port – ein Klick zeigt sie. Seit 0.65.0 unter der Konfiguration
    *vPC-Peer-Link (Domäne n)*, *STP blockiert VLAN …* (orange) und *Root-Port VLAN …* –
    Mitglieder eines Port-Channels zeigen die Werte des Channels.
  - **Endpunkte** (0.63.0, ADR 0025 Nr. 8): aus der MAC-Tabelle und ARP der Switches.
    - **Wo hängt …:** MAC (jede Schreibweise), IP oder Name (VM, DNS-Name aus dem IPAM)
      eingeben → je MAC der **Anschluss** (Switch, Port, VLAN, Beschreibung, „über“ die
      Gegenstelle am Port, z. B. `esx01 (vmnic2)`, zuletzt gesehen), sonst „nur auf Uplinks
      gesehen – dahinter …“ (Gerät hinter dem ACI bzw. einem Switch ohne Zugang) und früher
      gesehene Ports (Umzug, vMotion).
    - **Endpunkte an den Ports:** alle MACs an Ports, die keine Uplinks sind, mit VM (Host,
      Portgruppe), IPs (ARP, VMware Tools, ACI), IPAM-Namen; Filter Switch, VLAN, Suche.
    - Ein Port ist Uplink, wenn an ihm bzw. einem Mitglied seines Port-Channels laut CDP/LLDP
      ein Switch, Leaf, Spine bzw. ein Gerät mit Switch-/Router-Fähigkeit hängt (nicht
      ESXi-Host, Firewall, Telefon).
    - Die Einträge werden gesammelt: aktuell ist je Switch, was sein letzter Lauf gesehen hat;
      ältere bleiben als „früher“ bis zur Frist `AUTOMATO_RETENTION_L2_DAYS` (30 Tage).
  - **VLANs:** je VLAN die Namen und je Switch die Anzahl Access-Ports und Trunks; seit 0.69.1
    auch die VLANs des ACI (EPGs, je Leaf Ports und Port-Channel/vPC, Kennzeichen ACI/Switch).
  - **Patchungen im Plan** (seit 0.70.0): „Klick: Patchungen“ (Standard) zeigt unter dem Plan die
    Kabel des gewählten Geräts – je Kabel eigener Port und Gegenstelle mit Beschreibung, Speed/
    Duplex, Access/Trunk, Port-Channel/vPC, Spanning Tree, Medium, EPGs, Zustand, Abweichungen
    und Endpunkten am Port; Klick auf ein Kabel nur die Kabel zwischen den beiden Geräten. Für
    die Ausfall-Simulation auf „Ausfall“ umschalten.
  - **Befunde** (Kategorie „Verkabelung“ in Findings) nur zwischen zwei gelesenen Switches:
    Speed bzw. Duplex verschieden, VLANs verschieden (Modus, Access-VLAN, Native VLAN, erlaubte
    VLANs je Seite), Port-Channel nur auf einer Seite, Verbindung nur einseitig gemeldet.
    Seit 0.63.0 **VLAN fehlt am ESXi-Uplink**: an einem Port werden VMs eines ESXi-Hosts
    gesehen, er lässt aber ein VLAN nicht durch, das VMs im selben Cluster auf demselben
    vSwitch/DVS nutzen (nach vMotion bzw. beim Ausfall des anderen Uplinks ohne Netz). Ist der
    Port bewusst nur für einen Teil der VLANs da, den Befund quittieren.
    Seit 0.64.0 **Redundanz und VLAN-Wege**: *L2: nur ein Kabel* (Switch bzw. ESXi-Host mit einem
    einzigen Kabel zu den Switches), *L2: alle Kabel am selben Switch* (ESXi Warnung, Switch
    Information), *L2: Switch als einzelner Ausfallpunkt* (ohne ihn zerfällt das Netz),
    *Port-Channel unvollständig* (Mitglied nicht verbunden), *VLAN getrennt* (VLAN an Access-Ports
    bzw. SVI mehrerer Switches, die Trunks dazwischen sperren es).
    Seit 0.65.0 **vPC-Paar uneinheitlich**: Peer nicht verbunden (Fehler, mit Keepalive-Status),
    vPC nur auf einem der beiden Peers (wenn beide gelesen), vPC down bzw. Konsistenz nicht
    erfolgreich (Warnung) – auf dem Switch `show vpc consistency-parameters vpc <n>`. Hängt ein
    Gerät mit allen Kabeln an einem Peer, nennt *L2: alle Kabel am selben Switch* den
    ungenutzten Peer („… (vPC-Peer) nicht genutzt“).
    Für Spanning Tree und den vPC-Status braucht das Konto zusätzlich `show spanning-tree`
    (IOS privilege 1 bzw. NX-OS `network-operator` dürfen das); die Agenten holen den Befehl
    mit dem Selbst-Update.
  - Nicht für Kunden-Benutzer (Infrastruktur).
- **Applikationen** (ADR 0012): *Applikationen* mit Kürzel, externem und internem Namen,
  Verwendungszweck (Dev/Test/Prod), Verantwortlichen, Kritikalität, Standort und Mitgliedern
  (Application Profiles, EPGs, Server); **Vorschläge** nach der Projektnummer aus
  `AP_<nr>`/`H_…-<nr>-…` mit einem Klick übernehmen (Operator) – seit 0.38.0 unter der
  Liste der Applikationen, seit 0.74.1 je ACI-Tenant getrennt („Projekt 001 · TE_a“).
  - **Stammdaten aus CSV** (0.75.0, Operator, einzige Ausnahme vom „kein CSV-Import“):
    „CSV importieren“ über der Liste → Datei wählen (Excel-CSV mit „;“ oder „,“, UTF-8 bzw.
    Windows-1252) → Vorschau je Zeile (neu, ändern, unverändert, Fehler mit Grund) →
    „N übernehmen“. Spalten: Name (Pflicht), Externer Name, Interner Name, Zweck
    (dev/test/prod bzw. Entwicklung/Test/Produktion), Kürzel, Verantwortlich, Kritikalität
    (niedrig/mittel/hoch/kritisch), Standort (Code), Beschreibung; „Vorlage herunterladen“
    liefert die Kopfzeile. Gleicher Name = bestehende Applikation: ausgefüllte Zellen ändern,
    leere lassen den Wert; Zeilen mit Fehler werden ausgelassen. Mitglieder danach in der Doku
    bzw. im Drawer zuordnen. Audit `application.imported` (nur Zahlen und Kennungen).
  - **Ohne VMs** (0.75.0): Spalte „VMs“ (an den Portgruppen der EPGs bzw. hinter den Servern);
    „ohne VMs ausblenden“ (bleibt im Browser), „ohne VMs auswählen“ bzw. einzeln ankreuzen und
    „N löschen“ (Operator, mit Bestätigung; gelesene Daten bleiben).
  - **Klick auf eine Applikation öffnet ihre Doku** (0.14.0, Link `#apps?app=<id>`). Oben
    Stammdaten und Übersicht: VMs, EPGs, Regeln **spezifisch** bzw. **unspezifisch**
    (Subnetz, any, vzAny, Preferred Group; Klick filtert) und offene Findings.
  - **Systeme:** alle VMs der Applikation (Portgruppen ihrer EPGs, VMs hinter ihren Servern)
    und Server ohne VM – mit IPs/DNS, EPG (ganzer Pfad `Tenant/AP/EPG`), Portgruppe,
    Cluster/Host, Firewall-Objekt (je Name zusammengefasst mit den Firewalls, auf denen es
    steht, und „Name ≠ DNS“, wenn der Name nicht zum IPAM passt), Standort, Regel-Zahlen und
    dem Grund, warum das System dazugehört. **EPGs im ACI** mit BD, VRF, Subnetzen, VMs,
    Endpunkten. Klick auf ein System bzw. eine EPG filtert die Regeln.
  - **Regeln & Contracts** wie in der Regel-Ansicht (Aktion, Herkunft Firewall/ACI mit
    Quellsystem, Quelle, Ziel, Dienste, NAT) mit Richtung, Bezug und Kategorie. **Knopf
    Weg-Suche (0.112.0)** je Regel – auch in der Regel-Liste, im Regel-Detail und in der Doku
    nach Kategorie: die Weg-Suche öffnet sich in einem neuen Tab mit Quelle, Ziel und Dienst
    der Regel und sucht gleich; darüber „Aus Regel …“ mit den übrigen Quellen, Zielen und
    Diensten der Regel zum Anklicken (any steht nicht zur Wahl). Ist die Quelle any (0.113.1),
    sucht sie über die Anbindung am Eingang der Regel (Interface bzw. Zone in einer Anbindung)
    bzw. ab der Firewall der Regel mit ihrem Eingang – so geht der Weg bis zum Ziel, auch durch
    eine VIP (DNAT). Unter
    Quelle bzw. Ziel steht je Objekt der Regel, über das sie Systeme der Applikation erfasst
    (Host, Netz, any, EPG), welche Systeme darin liegen – eine Regel über ein Subnetz oder any
    erscheint einmal, nicht je VM (0.14.1). **Unspezifische Regeln** (any, Netz, vzAny,
    Preferred Group – typisch Zugriffe ins bzw. aus dem Internet oder MPLS) zeigen seit 0.37.0
    nur die Regel mit ihrem Objekt (`any`, `N_srv16 10.20.0.0/16`); welche Systeme der
    Applikation sie nutzen können, klappt **„▸ N Systeme“** auf. Gilt für beide Richtungen und
    ebenso in der Doku nach Kategorien (Source bzw. Destination = Objekt der Regel) und im Export (statt
    der Liste die Anzahl). Filter System/VM, EPG, **Richtung aus Sicht des Systems** (eingehend = Ziel
    bzw. Provider, ausgehend = Quelle bzw. Consumer), Art, Bezug, Suche. Klick → Detail mit
    **„Warum diese Regel für die Applikation gilt“** (je System Rolle, Richtung, Bezug,
    Kategorie, Mitglied/AP), Links in Regeln & Contracts und Findings.
  - **Findings** der Applikation: seit 0.38.0 je Befund die EPGs, zu denen er gehört
    (Server/VM über ihre EPGs, Subnetz über seine EPG, Regel über die Systeme, die sie
    trifft), filterbar nach **Tenant → AP → EPG** mit „x von y“.
  - Die **Doku nach Kategorien** (10 Tabellen) mit
    **Export (Markdown)**, **Für Confluence (Word + draw.io)** (0.71.0, siehe unten) und
    **Drucken / PDF**; **Mitglieder** und **Bearbeiten** oben. Seit 0.34.0 je Zeile **Aktion** (Allow/Deny) und **Gerät** (Firewall mit VDOM bzw.
    APIC); ein IPv4/IPv6-Paar (gleich bzw. bestätigt) steht als eine Zeile; Klick auf die
    Regel öffnet sie im Detail. NAT nur aus der Regel selbst: Internet-Zugriff mit ihrem SNAT
    (Pool bzw. „Interface-IP“), Zugriff aus dem Internet mit ihrer VIP; sperrende Regeln ohne
    NAT. Seit 0.79.0 auch über den **NSX ALB**: zeigt die VIP der FortiGate auf einen Virtual
    Service, dessen Server zur Applikation gehören, steht die Regel in Kategorie 2 mit der
    öffentlichen IP und in der Spalte **Load Balancer** (Virtual Service, VIP:Port, Pool); die
    Verbindungen Ende zu Ende gehen von der Quelle zur öffentlichen IP über DNAT und Load
    Balancer zum Server.
    - **Spalten (0.84.0):** **Source** und **Destination** in Verkehrsrichtung – eingehend steht
      die Gegenstelle in Source und das System der Applikation in Destination, ausgehend
      umgekehrt –, beide mit Name und IP (`web01 (10.20.0.10)`, `N_partner (192.0.2.0/24)`,
      `any → extern (Default-Route über internet)`); mehrere Einträge je auf einer Zeile. In
      Kategorie 2 „Zugriff aus dem Internet“ die Kette in eigenen Spalten: **Externer DNS
      Name**, **VIP (Firewall)** (`V-EXT_shop: 203.0.113.10 → 10.30.0.50`), **Load Balancer**
      (`vs-web · 10.30.0.50:443 · Pool pool-web`, nur die Ports der Regel) und **Destination** =
      Server mit dem Port im Pool (`web01 (10.10.0.20:8080)`). Sonst NAT in der Spalte **NAT**
      (`SNAT 198.51.100.7`), in Kategorie 1 **NAT Externe IPv4**. Spalten ohne Wert in einer
      Kategorie (NAT, DNS, VIP, Load Balancer, Schutzmechanismen) fallen weg – in der Ansicht, im
      Export (Markdown, Word) und im Druck.
    - **Richtung L3Out (0.78.2):** die Firewall ist führend – ein Contract zu einer externen
      EPG (L3Out) steht nicht separat da, solange er die Dienste der Firewall-Regeln in
      derselben Kategorie und Richtung abdeckt. Öffnet er weniger, steht er da mit „öffnet
      weniger als die Firewall-Regeln, es fehlt tcp/8443“ und ist ein Befund (Findings →
      Applikationen, „Contract enger als Firewall“; auch in der Applikations-Doku). Nur-ACI-
      Mandanten ohne Firewall sehen die Contracts wie bisher.
    - **Hilfs-Contracts (0.97.0, ADR 0034):** steht hinter dem L3Out eine Firewall, ist ein
      Contract zur externen EPG nur noch ein Hilfs-Contract: keine eigene Zeile in den
      Kategorien, in „Regeln & Contracts“, im Export und in den Kennzahlen, sondern an der
      Firewall-Regel in der Spalte **L3Out** („L3O_inet · Contract CT_inet“, enger als die
      Firewall „– es fehlt tcp/8443“ mit dem Befund). Ohne Firewall dahinter bleibt der Contract
      sichtbar mit „L3O_vpn (ohne Firewall)“. In den Verbindungen Ende zu Ende ist er keine
      eigene Verbindung, prüft aber im ACI-Hop mit.
    - **Nach L3Out geordnet (0.97.0):** je Kategorie zuerst die spezifischen Zugriffe, dann die
      unspezifischen je L3Out, über den sie hereinkommen (Zwischenüberschrift „über L3O_x“, in
      Markdown/Word die Spalte L3Out).
    - **Kategorie 10 (0.78.2):** nur Zugriffe auf die Applikation, nicht ihre Zugriffe auf
      andere Systeme bzw. Adressen ohne Zonensignal. Alle Regeln stehen weiter unter
      „Regeln & Contracts“.
  Die *Doku je System* und die *Publizierte Dienste* sind seit 0.107.0 entfernt (Anwender
  2026-10-28): die Doku läuft über die Applikationen, VIPs mit Kette VIP → Load Balancer →
  Server zeigt NAT / VIPs. Die Doku erfasst **alle** Regeln eines Systems. Der **Bezug** (in den Regeln der
  Applikation und in der Erklärung, nicht mehr als Spalte der Kategorien) sagt, wie die Regel
  greift:
  - direkt,
  - über ein Netz, einen Bereich oder eine Gruppe (Objektname),
  - any,
  - vzAny der VRF, ESG, oder die EPG des Servers.

  Für EPGs zählen ihre Subnetze und Endpunkte. Der Kopf nennt die **VM** des Servers bzw.
  die VMs an den Portgruppen der EPG; ein Server bringt auch die Contracts der EPG seiner
  Portgruppe mit (ohne gelernten Endpunkt).
  - **Rückrichtung (0.72.1):** Gilt ein ACI-Contract in beide Richtungen und fallen beide in
    dieselbe Kategorie, steht er in der Applikations-Doku als eine
    Zeile mit „(↔ beide Richtungen)“; in verschiedenen Kategorien (z. B. aus dem Internet und
    ins Internet) bleibt die Rückrichtung eine eigene Zeile „(Rückrichtung)“.
  - **Nur, wo der Verkehr durchkommt (0.24.0):** Automato prüft jede Firewall-Regel gegen das
    Routing ihrer Firewall.
    - Ziel: Leitet die Firewall den Server über den Ausgang der Regel (Interface bzw. Zone)?
    - Quelle: Kommt Verkehr vom Server über den Eingang der Regel an?
    - Wenn nicht, ist die Regel für dieses System **routingtechnisch nicht möglich** – z. B.
      „lan → internet, any“ für einen Server in der DMZ.
    - Solche Treffer fehlen in den Kategorien, im Export und in den Kennzahlen.
      - *Applikations-Doku:* Seit 0.34.0 stehen sie gar nicht drin, nur „N routingtechnisch
        unmögliche ausgelassen“. Beispiel: intern → Internet mit `any` im Ziel – die Antwort
        braucht keine Regel, ein Zugriff von dort bräuchte eine eigene.
    - Fehlt das Routing, die Route oder ein Interface, bzw. kann eine Policy-Route oder
      SD-WAN anders leiten, bleibt die Regel sichtbar („unbekannt“). Regeln mit `any` als
      Interface sind immer möglich.
  - **Gegenstelle any (0.25.0):** Statt „any“ steht, was hinter dem Interface bzw. der Zone
    der Regel liegt.
    - Zuerst eine erfasste Anbindung („all → Internet Provider (internet)“).
    - Sonst die Default-Route („extern“) bzw. die Netze dahinter („Netze hinter lan: …“).
    - Mit einer Internet-Anbindung landen solche Regeln in „Zugriff aus dem Internet“ bzw.
      „Internet Zugriff“. Anbindungen erfassen unter Routing → Anbindungen.
  - **ACI-Contracts über externe EPGs (0.28.0):** Ein System ausserhalb des Fabrics (z. B.
    hinter der Firewall) bekommt die Contracts der externen EPG, der das ACI seine IP
    zuordnet. Bezug: „externe EPG L3O_fw|EN_srv (10.20.0.0/24)“.
    - Seit 0.97.0 (ADR 0034) entscheiden die **Firewalls hinter den L3Outs:** es gilt der
      L3Out, dessen Firewall die IP am genauesten nach aussen routet; leitet sie die IP zurück
      ins ACI, kommt sie nicht über diesen L3Out (Grund z. B. „über L3O_inet: fw-inet routet
      192.168.50.0/24 über dmz“). So gehört eine DMZ hinter der Internet-Firewall zum L3O_inet,
      auch wenn L3O_bit 192.168.0.0/16 importiert. Ohne Firewall bzw. ohne Aussage gilt das ACI:
    - Es zählt das längste Subnetz mit Scope `import-security` in der VRF – über alle
      L3Outs. Contracts weniger genauer externer EPGs (z. B. 0.0.0.0/0) stehen unter
      „Routingtechnisch nicht möglich“ mit Begründung, ebenso Subnetze, die nur das Routing
      steuern.
    - Für eine IP im Fabric der VRF gelten die Contracts ihrer EPG; externe EPGs fehlen dann
      in der Doku. In der Regel-Liste ist diese Seite „nicht möglich“, der Contract bleibt
      sichtbar, wenn die IP auf der anderen Seite in einer EPG liegt.
    - 0.0.0.0/0 in mehreren L3Outs: möglich ist die EPG des L3Outs, über den die VRF die IP
      leitet; die übrigen bleiben sichtbar („unbekannt“).
    - Das Regel-Detail zeigt bei Subnetzen externer EPGs „nur Routing“, wenn sie nicht
      zuordnen.
    - **Export Route Control ist kein Netz der EPG (0.111.0):** ein Subnetz nur mit
      `export-rtctrl` (ohne `import-security`) steuert, welche Routen der L3Out ankündigt, und
      ordnet keinen Verkehr zu. Es zählt nicht als Gegenstelle der Verbindungen, nicht in der
      IP-Suche, nicht für Zonen und nicht in der Applikations-Doku; ein Contract nur darüber
      steht unter „ohne prüfbare Gegenstelle“.
  - **Nur Tenant/VRF des Systems (0.42.3):** Gleiche Adressen gibt es in mehreren ACI-Tenants
    bzw. VRFs.
    - Steht ein System in einer EPG (Portgruppe seiner VM), zählen gelernte Endpunkte mit
      seiner IP nur in deren VRF. Ein Endpunkt mit derselben IP in einer anderen VRF ist ein
      anderes System. Ohne Portgruppe gelten die VRFs der Applikation, sofern eine passt.
    - Externe EPGs anderer VRFs (z. B. 0.0.0.0/0 eines fremden Tenants) zählen für Systeme
      in einer EPG nicht. Ihre Contracts gehören zu den Systemen dort.
    - Der Filter „EPG“ in der Applikations-Doku zeigt die Contracts eines Systems nur über
      diese EPG (VM in mehreren EPGs).
  - **Scope, gemeinsamer BD, Portgruppen (0.43.1):**
    - *Scope der Contracts:* Der Scope legt fest, was ein Contract verbindet – auch für
      Contracts aus `common`:
      - `application-profile` (AP): nur EPGs im selben AP; vzAny steht für alle EPGs seiner
        VRF und verbindet mit einer EPG derselben VRF die EPGs ihres AP (seit 0.45.1);
      - `context` (VRF): nur EPGs derselben VRF;
      - `tenant`: nur EPGs im selben Tenant, auch über mehrere seiner VRFs;
      - `global`: alle verbundenen EPGs, auch über Tenants.
      Zwischen verschiedenen VRFs braucht es zusätzlich geteilte Subnetze bzw. Route-Leaking
      (0.44.0). Consumer bzw. Provider ausserhalb des Scopes sind keine Gegenstelle – in der
      Applikations-Doku, der Weg-Suche und seit 0.45.1 auch in der Regel-Liste (Suche nach
      einer IP: „nicht möglich“ mit „Scope VRF: … liegen in verschiedenen VRFs“) und im
      Regel-Detail: „Scope VRF: verbindet nur EPGs derselben VRF · N Paare“, EPGs ohne
      Gegenstelle im Scope mit „ohne Gegenstelle im Scope“. Befund *Contract: ohne
      Gegenstelle im Scope* (Findings → Regeln): Warnung, wenn der Contract wegen des Scopes
      gar nichts verbindet, sonst Hinweis mit den betroffenen EPGs.
    - *Gemeinsamer BD:* Teilt die EPG ihren BD mit anderen EPGs, zählen für sie Regeln, die das
      ganze BD-Subnetz enthalten. Ein Host bzw. Teilnetz darin zählt nur, wenn es eine IP der
      EPG trifft (VM an ihrer Portgruppe, Endpunkt der EPG).
    - *Portgruppe einer NIC:* Sie wird über Name und VLAN bestimmt, beim Distributed Switch
      auch über den Switch. Mehrdeutig heisst keine EPG. Unter „Systeme“ steht je NIC
      „Portgruppe (VLAN) → EPG“, bei Zuordnung nur über das VLAN mit Hinweis.
    - *Vorgabe vor Gelerntem:* Für eine IP an einer NIC mit EPG gilt die EPG der Portgruppe,
      nicht ein gelernter Endpunkt in einer anderen EPG. Seit 0.46.0 gilt das überall
      (Weg-Suche, Regel-Liste, Verbindungen) und auch für statische Endpunkte (fvStCEp):
      Reihenfolge statischer Endpunkt → Portgruppe der VM → gelernter Endpunkt → EPG-Subnetz
      → BD-Subnetz. Erfasst ein Contract eine IP nur über ein gemeinsames Subnetz, gehört sie
      aber nach der Konfiguration zu einer anderen EPG, ist er in der Regel-Liste für sie
      „nicht möglich“ („10.1.0.5 gehört nach Portgruppe der VM vm-a zur EPG …“).
    - *Befund „EPG: gelernt ≠ konfiguriert“* (Findings → ACI / VMware, 0.46.0): Das ACI hat
      eine IP in einer anderen EPG gelernt, als die Portgruppe ihrer VM bzw. ein statischer
      Endpunkt vorgibt (gleiche VRF). Je VM bzw. EPG ein Befund mit den IPs. Zu prüfen: VM an
      der richtigen Portgruppe, IP doppelt vergeben, statischer Endpunkt veraltet. Gleiche IPs
      in verschiedenen VRFs sind verschiedene Systeme und kein Befund.
    - *Hinweis Projektnummer:* Nennt der VM-Name (`app01-000-…`) ein anderes Projekt als das AP
      der EPG ihrer Portgruppe, steht ein Hinweis beim System. Die Zuordnung folgt der
      Portgruppe – zu prüfen ist dann die Portgruppe der VM im vCenter.
  - **Contracts zwischen VRFs (0.44.0):** Ein Contract zwischen EPGs verschiedener VRFs (z. B.
    einem anderen Tenant) zählt nur, wenn das Fabric die Subnetze leakt – geteiltes Subnetz
    beider Seiten (Shared Service) bzw. Route-Leaking. Sonst fehlt er in der Applikations-Doku
    (Grund: „… kein geteiltes Subnetz … im
    Fabric kein Weg. Verkehr geht über das Routing der VRF (L3Out, Firewall) – siehe
    Weg-Suche“. Doku und Weg-Suche urteilen so gleich. Fehlt der Scope der Subnetze (Lauf
    vor 0.44.0), bleibt er „unbekannt“ – neu einlesen. Das Regel-Detail zeigt geteilte
    Subnetze mit „geteilt“.
  - **Verbindungen Ende zu Ende (0.45.0, ADR 0021):** Karte über „Regeln & Contracts“. Je
    Verbindung (Quelle → Ziel, Dienst) eine Zeile mit dem Weg über ACI und Firewalls wie in der
    Weg-Suche, je Knoten eingefärbt nach seinem Urteil, und dem Gesamturteil.
    - Abgeleitet aus den Regeln der Doku: System der Applikation mit seiner IP; Gegenseite die
      Objekte der Regel (Host, Netz als Ganzes) bzw. beim Contract die Gegenstellen im Scope
      (EPG mit der IP ihrer ersten VM bzw. ihres ersten Endpunkts). Gegenstelle any, vzAny und
      Preferred Group bleiben Regeln ohne Verbindung; FQDN/ISDB stehen als Hinweis da.
    - **Netz als Ziel (seit 0.79.1):** Die Route, die das ganze Netz enthält (oft 0.0.0.0/0),
      gilt nicht für seine genauer gerouteten Teile. Diese Teile (z. B. Netze hinter einem
      anderen L3Out bzw. Interface) sind eigene Verbindungen mit ihrem Weg, beschriftet „Netz ›
      Teil“ und „Teil der Verbindung …“; die Verbindung zum ganzen Netz nennt sie („ohne die
      genauer gerouteten Teile: …“). Höchstens acht Teile je Netz, Host-Routen nicht.
    - **Contract mit EPG als Consumer und Provider (seit 0.79.1):** die eigene EPG ist keine
      Gegenstelle (Verkehr in der EPG ist ohnehin erlaubt), die Zeile trägt im Bezug
      „Consumer und Provider“; dazu der Befund „Contract: EPG Consumer und Provider“.
    - Klick zeigt je Hop die entscheidende Policy bzw. den Contract mit den Objekten (Quelle,
      Ziel, Dienste), Ein-/Ausgang, NAT und Leak, dazu je Regel der Doku: *entscheidet*,
      *möglich*, *greift nicht* (vorher entscheidet eine andere bzw. sie passt nicht), *nicht auf
      dem Weg* (ihr Gerät liegt nicht auf dem Weg), *nicht prüfbar* (kein Weg bekannt).
    - **Abweichung:** Eine Regel erlaubt, aber der Weg sperrt bzw. sie greift dort nicht.
      Abweichungen stehen zuerst; „nur Abweichungen und Gesperrte“ filtert.
    - **Nur, was auf dem Weg liegt (0.107.0, Anwender: „Wege, welche Routing technisch keinen
      Sinn ergeben“):** liegt das Gerät einer Regel nicht auf dem Weg bis zum Ziel (z. B. eine
      andere Firewall mit denselben Regeln), gehört die Regel nicht zur Verbindung – keine
      Abweichung, sondern unten „Nie auf dem Weg ihres Verkehrs …“ und der Befund
      **„Applikation: Regel nie gebraucht“** (Hinweis, Findings → Applikationen, mit dem Weg, den
      der Verkehr nimmt). Verbindungen nur aus solchen Regeln entfallen. Kommt die Quelle von
      ausserhalb (Internet, Partner), beginnt der Weg dort, wo der **Rückweg** vom Ziel das
      gelesene Netz verlässt (z. B. `wan1` der Internet-Firewall) – nicht mehr auf irgendeinem
      Gerät mit einer Regel dafür. Die Doku nach Kategorien zeigt die Regel weiter.
    - **Filter nach Art (0.80.0):** unter der Suche je Gruppe Knöpfe mit Zahl – *Urteil*
      (erlaubt, gesperrt, abhängig, offen = nicht prüfbar bzw. der Weg endet vor dem Ziel),
      *Regeln* (erlaubende bzw. sperrende Regeln der Doku), *Regel auf dem Weg* (entscheidet,
      möglich, greift nicht, nicht auf dem Weg, nicht prüfbar), *Richtung*, *Hinweise*
      (Abweichungen, Wege uneinheitlich, gegen die Zonen-Absicht). In einer Gruppe „oder“,
      zwischen den Gruppen „und“ (z. B. „gesperrt“ + „Abweichungen“ = erlaubende Regel, deren
      Weg sperrt); die Zahl am Knopf gilt mit den übrigen Filtern. Es erscheinen nur Arten, die
      es in der Applikation gibt; „N von M“ zeigt, wie viele Verbindungen übrig sind, „Filter
      zurücksetzen“ hebt alle auf. Der Plan zeigt nur die gefilterten Verbindungen hervor.
    - **Wege uneinheitlich (0.73.0):** Ist eine Verbindung über einen Weg offen und über einen
      anderen gesperrt (aktive Wege bzw. ECMP, Backup-Wege gleicher Länge, die Firewalls eines
      Verbunds), steht in der Zeile „Wege uneinheitlich“; das Detail zeigt je Weg (aktiv bzw.
      Backup, über welche Firewalls) das Urteil. Im Kopf der Karte „N Wege uneinheitlich“.
    - **Als Befund (0.59.0, seit 0.73.0 nur bei uneinheitlichen Wegen):** je erlaubender Regel
      und Applikation ein Befund „Applikation: Wege uneinheitlich“ (Findings → Applikationen,
      auch in der Applikations-Doku bei den Befunden) mit den Verbindungen und dem Urteil je
      Weg. Sperrt der Weg durchgehend bzw. liegt die Regel nicht auf dem Weg, hat das einen
      Grund – Abweichung in der Karte, aber kein Befund. Neu berechnet nach jedem Lauf, nach
      „Abgleich“ und beim Ändern bzw. Löschen einer Applikation; Triage bleibt erhalten. Nach
      einem Lauf höchstens `AUTOMATO_APP_FINDINGS_SECONDS` (120, `0` = ohne Grenze) – was nicht
      drankommt, behält seine Befunde und ist beim nächsten Lauf zuerst dran (Hinweis im Log).
    - **Export und Druck (0.59.0):** der Export der Applikations-Doku enthält den Abschnitt
      „Verbindungen Ende zu Ende“ (Richtung, Quelle, Ziel, Dienst, Weg mit der entscheidenden
      Regel je Hop, Urteil, Abweichung bzw. Wege uneinheitlich); „Drucken / PDF“ zeigt je
      Verbindung den Weg mit den entscheidenden Regeln und die Abweichung ausgeschrieben.
    - Liegt die Quelle an keinem gelesenen Gerät (Internet, Partner), beginnt der Weg auf dem
      Gerät der Regel. „In der Weg-Suche öffnen“ zeigt den ganzen Weg mit allen Varianten (seit
      0.109.1 in einem neuen Tab, die Doku bleibt offen).
      **„Warum dieser Weg?“** (0.109.0) erklärt denselben Weg wie die Verbindung Hop für Hop (wie
      in der Weg-Suche, mit „Erwartet über …“ und „Als Finding erfassen“).
    - **Zonen (0.78.0):** mit logischen Zonen steht unter dem Weg „Zonen: Server → Edge
      Internet → Public“ (die Zonen auf dem Weg, sonst die von Quelle und Ziel) und die Absicht
      zwischen Quell- und Zielzone als Chip (grün = passt, rot = gegen die Absicht, orange =
      abhängig bzw. nicht umgesetzt). Erlaubt der Weg, was die Absicht sperrt bzw. nicht
      vorsieht, steht in der Zeile „gegen Zonen-Absicht“ und im Kopf der Karte „N gegen
      Zonen-Absicht“. Der Export hat die Spalte „Zonen“.
    - **Netzwerkplan (0.67.0, kompakt seit 0.74.0):** Umschalter **Tabelle | Plan** im Kopf der
      Karte (bleibt gewählt). Links die Applikation: je Application Profile ein Rahmen
      (Tenant/AP) mit einem Kasten je EPG und ihren VMs bzw. Servern (höchstens sechs, sonst
      „+n weitere“), Systeme ohne EPG im Rahmen „ohne EPG“; in der Mitte L3Outs, Firewalls und
      VDOMs auf den Wegen (das ACI als der L3Out zur Gegenstelle hin – nach dem Routing bzw. der
      externen EPG –, sonst als VRF), rechts die Gegenstellen.
      - **Ebene „Regeln“:** unter jedem Knoten des Wegs ein Kasten mit den entscheidenden
        Policies bzw. Contracts und ihren Diensten („web-lan (#11) · tcp/443“, „Contract CT_web ·
        tcp/443“, „✕ implizit gesperrt“). Häkchen **Regeln** bzw. **Dienste und Interfaces**
        blenden die Ebenen aus (bleibt im Browser); der Export folgt dem Häkchen „Regeln“.
      - **Zoom und Verschieben:** −, +, 100 % und „einpassen“; Strg bzw. ⌘ + Mausrad zoomt,
        Ziehen auf freier Fläche verschiebt, sonst scrollt der Plan in seinem Rahmen. An jeder Linie die Dienste (→ von der Applikation
      weg, ← zu ihr hin, ✕ vorher gesperrt – kommt hier nicht mehr an), an den Enden die
      Interfaces (z. B. `port3`, `vlan5`). Farbe nach dem Urteil, gestrichelt = Weg nicht
      bekannt; Knoten mit Rahmen in der Farbe ihres Urteils. Linien über mehrere Spalten laufen
      an den Knoten vorbei.
      - **Wann gestrichelt?** (a) Das erste Stück von der Applikation aus: kein gelesenes Gerät
        hat das Netz des Systems angeschlossen (kein Interface der Firewall, kein BD-Subnetz im
        ACI). Der Weg beginnt dann ersatzweise auf dem Gerät der Regel – bei einem Contract in
        der ACI-VRF der EPG, bei einer Firewall-Policy direkt auf der Firewall. Darum gehen
        solche Linien teils „über das ACI VRF“, teils direkt zur Firewall. (b) Das letzte Stück
        zur Gegenstelle: der Weg endet vorher (keine Route, Router ohne gelesenes Gerät).
        (c) Gar kein Weg gefunden.
      - **Über L3Out, VRF oder direkt?** Steht das Gateway eines Netzes im ACI (BD mit
        Subnetz), beginnt der Weg in der VRF und verlässt das Fabric über den L3Out zur
        Firewall – der Knoten heisst dann wie der L3Out. Ist die Firewall das Gateway (BD ohne
        Subnetz, das ACI nur als Layer 2), beginnt der Weg auf der Firewall: die Linie geht von
        der EPG direkt zur Firewall, das Fabric liegt nur als Layer 2 dazwischen. Ein Knoten
        „ACI-VRF“ statt L3Out heisst: der Weg bleibt im Fabric bzw. kein L3Out passt zum
        Next-Hop.
      - **Load Balancer (0.85.0):** führt ein Weg über einen Virtual Service des NSX ALB,
        steht nach dem Knoten, an dem seine VIP liegt, ein eigener Kasten – benannt nach der
        EPG der VIP (im ACI) bzw. dem Load Balancer (VIP an einer Firewall), darunter je
        Virtual Service eine Zeile (`vs-web 10.30.0.50:443 · Pool pool-web`) – und von dort die
        Linie zur EPG der Server. Geht der Weg danach am selben Knoten weiter (dieselbe VRF bzw.
        Firewall), steht dieser nicht ein zweites Mal da; die Regel dort (Contract VIP-EPG →
        Server-EPG bzw. die Policy der Service Engine) steht im Kasten „Regeln“ des Load
        Balancers mit dem Gerät davor. An der Firewall mit der VIP (DNAT) steht die Zeile „VIP
        V-EXT_shop → 10.30.0.50“.
      - Seit 0.83.1 zählen BD-Subnetze, deren VRF in `common` liegt, für die VRF aus `common`
        (vorher stand der Tenant der BD da – die Wege dieser Systeme begannen nicht im Fabric
        und waren gestrichelt).
      - Suche und „nur Abweichungen und Gesperrte“ heben die passenden Verbindungen hervor,
        ebenso ein Klick auf einen Knoten bzw. eine Linie (Liste darunter, „Details“ öffnet die
        Verbindung in der Tabelle; erneuter Klick hebt die Auswahl auf).
      - **Export** draw.io bzw. SVG – mit Hervorhebung nur diese Verbindungen („Export (n
        Verbindungen)“). Im draw.io tragen Knoten IPs, Systeme und Urteil, Linien Dienste je
        Richtung und die Interfaces (`automato_ports_a`/`_b`) als Eigenschaften; die Rahmen der
        APs stehen hinter den Kästen, die Regeln auf der eigenen Ebene „Regeln“ (in draw.io
        über *Ansicht → Ebenen* ein- und ausblendbar). API
        `GET /applications/{id}/connections/plan/export?format=svg|drawio&key=…&rules=false`.
    - Nicht für Kunden-Benutzer (zeigt die Infrastruktur).
  - **Regeln direkt zuordnen bzw. ausschliessen (0.60.0, ADR 0012):** Im Regel-Detail der
    Applikations-Doku „Der Applikation zuordnen“ (die Regel gehört dazu, auch wenn sie nur über
    any bzw. ein grosses Netz oder gar nicht trifft) bzw. „Aus der Applikation ausschliessen“
    (sie gehört trotz Treffer nicht dazu). In „Regeln & Contracts“ ordnet das Regel-Detail
    eine Regel einer Applikation zu und springt in deren Doku. Nur Operator.
    - Zugeordnete Regeln stehen mit der Art „direkt zugeordnet“ in der Doku und zählen wie
      spezifische; ausgeschlossene fallen aus Liste, Kategorien, Verbindungen und Kennzahlen.
      „N zugeordnet · M ausgeschlossen“ über der Regel-Tabelle klappt die Liste mit
      „rückgängig“ auf.
    - Die Zuordnung hält über neue Läufe (Quellsystem, VDOM und Policy-ID bzw. Kennung des
      Contracts); fehlt die Regel im Lauf, steht das Mitglied als „nicht mehr gelesen“ da.
  - **Regeln je Kategorie ausblenden (0.69.0, ADR 0012):** In „Doku nach Kategorien“ hat jede
    Zeile (Operator) „ausblenden“. Der Dialog verlangt eine **Begründung** und fragt, ob die
    Regel nur in dieser Kategorie oder in allen ausgeblendet wird. Die Zeile fällt aus der
    Kategorie (Ansicht, Export, Druck); im Kopf der Kategorie „N ausgeblendet“, darunter
    aufklappbar die Liste mit Regel, System, Begründung, wer und wann sowie „einblenden“.
    - Die Regel bleibt sonst in der Doku (Regel-Liste, Verbindungen, Kennzahlen) – anders als
      „Aus der Applikation ausschliessen“. Das Ausblenden hält über neue Läufe.
    - Export (Markdown): Abschnitt „Ausgeblendete Regeln“ mit Kategorie und Begründung.
    - Audit: `application.rule_hidden` bzw. `application.rule_shown` mit Regel, Kategorie und
      Begründung (System → Audit).
  - **Übersicht (0.73.0):** Systeme, EPGs, Verbindungen Ende zu Ende, Regeln & Contracts,
    Findings und Doku nach Kategorien (auch jede Kategorie) klappen per Klick auf den Titel
    auf bzw. zu (▾/▸); oben „alle aufklappen“ / „alle zuklappen“. Der Zustand bleibt im
    Browser; die Kategorien sind zu Beginn zu. Die Tabelle **Systeme** zeigt nur System,
    Zustand, IPs, EPG und Regeln – Klick öffnet das Detail (Mitgliedschaft, Betriebssystem,
    DNS, Portgruppe je NIC mit EPG, Cluster/Host, Standort, Firewall-Objekte mit Namensprüfung
    und Link) mit „Regeln dieses Systems zeigen“.
  - **Export / Drucken (0.73.0):** der Knopf oben öffnet einen Dialog mit den Abschnitten
    (Übersicht, Systeme, EPGs, Verbindungen Ende zu Ende, Netzwerkplan, Regeln & Contracts,
    Findings, Doku nach Kategorien, ausgeblendete Regeln; der Kopf mit dem Steckbrief steht
    immer da). Die Auswahl bleibt im Browser.
    - **Markdown** – die gewählten Abschnitte (ohne Netzwerkplan); seit 0.104.0 vom Server aus
      demselben Aufbau wie Word (gleiche Gliederung und Tabellen).
    - **Word + draw.io (ZIP)** bzw. **Word (.docx)**, wenn der Netzwerkplan nicht gewählt ist –
      für Confluence, siehe unten.
    - **Drucken / PDF** – die gewählten Abschnitte offen, die übrigen fallen im Druck weg.
  - **Für Confluence (0.71.0, ADR 0026):** im Export-Dialog „Word + draw.io (ZIP)“ lädt
    `applikation-<Name>-<Datum>.zip` (Umlaute ausgeschrieben) mit
    - `….docx` – die Doku als Word-Datei (Querformat A4) mit den gewählten Abschnitten;
      Überschriften mit Gliederung, Tabellen mit wiederholter Kopfzeile;
    - `…-netzwerkplan.drawio` – der Netzwerkplan als bearbeitbare Zeichnung (wie der Export
      draw.io der Karte, alle Verbindungen); nur mit dem Abschnitt Netzwerkplan.
    - Automato schreibt **nicht** nach Confluence. Ablauf: Word-Datei in Word bzw. LibreOffice
      überarbeiten, in Confluence *Seite erstellen → ⋯ → Importieren → Word-Dokument* (bzw.
      *Inhalt importieren*); den Plan in draw.io (diagrams.net) bearbeiten und auf der Seite
      mit dem draw.io-Makro *Diagramm importieren* einfügen. Änderungen in Confluence fliessen
      nicht zurück.
    - Kunden-Benutzer (seit 0.104.0): Markdown bzw. Word ihres Ausschnitts – ohne Verbindungen
      und Netzwerkplan (sie zeigen die Infrastruktur), ohne Gerätenamen. API
      `GET /applications/{id}/export?format=zip|docx|md&sections=…` (Komma-Liste `overview`,
      `systems`, `epgs`, `connections`, `plan`, `rules`, `dependencies`, `findings`,
      `categories`, `hidden`; leer = alle).
  - **Detail statt Sprung (0.95.0):** Klick auf eine EPG (Karte „EPGs im ACI“ bzw. im Detail
    eines Systems) öffnet ihr Detail – Lage im ACI, Subnetze mit IPAM, Endpunkte, VMs,
    Leaf-Ports. „Regeln dieser EPG zeigen“ filtert die Regeln wie bisher; „In Objekte & Inventar
    öffnen“ zeigt ihre Portgruppen bzw. Subnetze. Firewall-Objekte im Detail eines Systems öffnen
    sich ebenfalls in der Doku. Die Kennzahlen oben filtern weiter.
  - **Namen der Gegenstellen (0.95.0):** Administration → Mandanten & Standorte → Mandant →
    „Applikations-Doku: Namen der Gegenstellen zeigen“ (nur Admin, im Audit). Danach nennen die
    Kategorien, Markdown und Word bei Gegenstellen, von denen die Firewall-Regel nur die Adresse
    kennt, den Namen (IPAM, VM, EPG bzw. Firewall-Objekt): „db01.example.ch (10.30.0.5)“. Gilt nur
    für Betreuer – Kunden-Benutzer sehen weiter nur die Adressen.
    Seit 0.101.0 schaltet der Global Admin die Namen auch direkt in der Karte „Doku nach
    Kategorien“ um („Namen der Gegenstellen“); Netze und Bereiche nennen dann zuerst die Gruppe,
    wie sie in der Regel steht: „G_clients (10.70.0.0/16, 10.71.0.0/16)“. Seit 0.107.0 steht der
    Schalter sofort auf dem neuen Stand („– lädt…“), bis die Doku neu geladen ist; das IPAM
    bleibt im Speicher (`AUTOMATO_REF_CACHE`, Vorgabe 4 Referenzquellen, `0` = aus) statt je
    Anfrage neu gelesen zu werden – ein neuer IPAM-Lauf ersetzt es.
  - **Stand der Doku (seit 0.108.0, ADR 0037):** Doku und Verbindungen Ende zu Ende (mit Plan)
    werden je Applikation gespeichert und nur neu gerechnet, wenn sich etwas ändert, das sie
    betrifft – ein Lauf eines Quellsystems (Geräte, IPAM), Zonen, Kategorien, Namensprofil,
    Verbund, L3Outs, Mitglieder bzw. ausgeblendete Regeln. Über der Doku bzw. in der Karte der
    Verbindungen steht „Stand von heute 14:05“; ändert sich etwas, zeigt die Seite sofort den
    letzten Stand mit „– wird im Hintergrund aktualisiert…“ und lädt von selbst neu, sobald er
    fertig ist. „↻ neu berechnen“ rechnet auf Wunsch neu (z. B. nach einer Änderung direkt an
    einem Gerät, bevor der nächste Lauf sie liest). Befunde in der Doku sind immer aktuell.
    Exporte (Word, Markdown, Plan) nehmen immer den aktuellen Stand. Nach Läufen und Änderungen
    rechnet der Orchestrator die zuletzt angesehenen Applikationen im Hintergrund vor.
    Einstellungen: `AUTOMATO_DOC_SNAPSHOTS` (Vorgabe `true`; `false` = jedes Mal neu wie bis
    0.107.0), `AUTOMATO_DOC_STALE_SECONDS` (1,5 – kürzere Berechnungen wartet die Seite ab),
    `AUTOMATO_DOC_WARM_DAYS` (7 – so lange angesehene Stände werden vorgerechnet, `0` = nie),
    `AUTOMATO_DOC_WARM_SECONDS` (600 – Zeitbudget je Durchgang, `0` = ohne Grenze).
  - **Kategorien nach Zonentyp (seit 0.106.0, ADR 0035):** jede Firewall-Regel bzw. jeder
    Contract steht in der Kategorie seiner **Richtung** Zonentyp der Quelle → Zonentyp des Ziels.
    In drei Schritten einrichten:
    1. **Netzwerkdesign → Kategorien** (Operator) je Zone:
       - **Zonentyp**: die Zone des Grundschemas, das im Namensprofil hochgeladen ist (ohne
         eigenes die Vorlage: DC, Edge Internet, Edge Office, Edge VPN, Public, …).
         „automatisch: …“ übernimmt die Erkennung nach Name bzw. Kurzname; nur von Hand wählen,
         wenn sie nicht passt.
       - **Netze** (Quell-Adressen, ein Netz je Zeile) mit **Ausschlüssen** („ohne“, z. B.
         10.0.0.0/8 ohne 10.20.0.0/16).
       - **Geräte**: Firewall bzw. VDOM, ACI-Tenant, ACI-VRF und Anbindungen (zuerst unter
         Routing → Anbindungen erfassen bzw. übernehmen).
       - Danach „Speichern“. Die übrigen Selektoren (EPG, Firewall-Zone, IPAM …) pflegt der
         Reiter Zonen.
    2. **Namensprofile → Kategorien der Applikations-Doku** (Admin): je Kategorie Titel,
       Titel englisch, **Source-Zone** und **Dest-Zone**. Je Seite einen oder mehrere Zonentypen
       wählen, „* jeder“ = jeder Zonentyp, z. B. „Zugriff aus den Ressourcen“ mit `DC-Ressourcen
       → DC`, „Zugriff aus anderen Kunden“ mit `DC → DC`.
       - Fest, nur der Titel änderbar: **innerhalb der Applikation** (7), **innerhalb des
         Security Tenants** (8) und **unspezifisch** (10).
       - Die Vorgaben (Internet, Unternehmensnetzwerk, VPN, andere Security Tenants, Management)
         lassen sich ändern oder entfernen; „Auf Vorgaben zurücksetzen“ stellt sie wieder her.
    3. Die **Doku** teilt so ein:
       - zuerst innerhalb der Applikation (Server intern, ACI im selben Application Profile),
         dann innerhalb des Security Tenants (ACI im selben Tenant bzw. Gegenstelle in der eigenen
         Zone);
       - dann die genaueste Richtung: beide genannt vor „*“, bei Gleichstand die kleinere Nummer;
       - sonst unspezifisch.

    **Ohne Netz einer Zone** (any, grosse Bereiche) zählt, was hinter dem Interface der Regel
    liegt: die Anbindung, die Netze, die die Firewall dort routet, bzw. die Default-Route (Zonen
    mit 0.0.0.0/0). Ein grosser Bereich zählt nur mit seinen Zonen, die dort liegen. Bei einer
    externen EPG zählt die Zone der Firewall hinter ihrem L3Out. Ist das nicht eindeutig, bleibt
    der Zugriff unspezifisch.

    **Ohne gepflegte Zone** nimmt Automato den Zonentyp nach dem Namen der Firewall-Zone bzw. des
    L3Outs, nach den Mustern des Grundschemas („L3O_inet“ → Edge Internet). Externe Gegenstellen
    gelten als Internet, Systeme im ACI und die dokumentierten Systeme als DC.

    Zur Anzeige der Doku:
    - Die Titel bleiben die der Kategorien. Der Kopf nennt die Zonen der Gegenstellen mit Netzen
      und Ausschlüssen, die Spalte **Zone** die Zonen der Gegenstelle. Kunden-Benutzer sehen
      Spalte und Zonen nicht.
    - Die VIP- und NAT-Spalten erscheinen, wenn eine Zeile sie hat.
    - In der Doku führt „Zonen der Kategorien ↗“ ins Netzwerkdesign.

    Lernt eine Firewall, die einer Zone als Gerät zugewiesen ist, ein Netz einer anderen Zone
    nicht über den Übergang Richtung dieser Zone im Zonenplan, steht der Befund **„Netz nicht über
    den Übergang zur Zone“** unter Findings → Zonen. Das gilt bei einem anderen Interface, nur über
    die Default-Route oder ohne Route; der Befund nennt das Interface und die Zonen dahinter.
  - **Nur relevante Firewall-Regeln (0.103.0):** ausgehende Firewall-Regeln, die die Systeme nur
    über eine breite Quelle erfassen (z. B. 10.0.0.0/8 → irgendwohin, any → Internet), stehen
    nicht mehr in der Doku – weder in den Kategorien noch unter „Regeln & Contracts“, in den
    Verbindungen; oben steht „n ausgehende mit breiter Quelle
    ausgelassen“. Eingehende Regeln über grosse Bereiche (Zugriffe auf die Systeme) und Regeln,
    die den Server konkret nennen, bleiben. Gehört eine solche Regel doch zur Applikation: im
    Regel-Detail „der Applikation zuordnen“.
  - **Freigaben ins Internet (0.114.1):** eine Firewall-Regel, die die Systeme nur über `all` im
    Ziel erfasst und deren Ausgang ins Internet führt (Default-Route bzw. Anbindung Internet
    hinter dem Ziel-Interface, auch einer SD-WAN-Zone, oder Zone vom Typ Internet), ist kein
    Zugriff auf die Applikation – sie fehlt in der Doku, oben steht „n Freigaben ins Internet
    ausgelassen“. Direkt zugeordnete Regeln bleiben.
  - **ACI im Weg (0.103.0):** leitet die Firewall hinter dem L3Out eine IP zurück ins ACI, ordnet
    die Prüfung im Fabric sie trotzdem der externen EPG mit dem längsten Subnetz zu („… –
    Zuordnung im ACI“), ein Contract erlaubt dann. Ordnet keine externe EPG zu, steht der Grund in
    Klammern dahinter.
  - **Abhängigkeiten (0.60.0, seit 0.88.0 wieder):** Bereich „Abhängigkeiten“ (zugeklappt,
    lädt beim Aufklappen) mit „nutzt“ und „wird genutzt von“ je Applikation und den Regeln.
    Abgeleitet aus den erlaubenden Regeln mit spezifischem Treffer, deren Gegenseite EPGs,
    Server bzw. ein Netz im Subnetz der EPGs einer anderen Applikation trifft. Applikationen wie
    Monitoring, Backup oder DNS im Formular als **Infrastruktur** kennzeichnen – Abhängigkeiten
    zu ihnen stehen dann eingeklappt unter „Infrastruktur“, getrennt von den fachlichen.
    Export-Abschnitt „Abhängigkeiten“ (Markdown, Word, Druck). API
    `GET /applications/{id}/dependencies`.
  - **Nur die Beteiligten (0.43.2):** Ein Contract zeigt in der Applikation nur ihre EPGs bzw.
    VMs und die Gegenstellen, die über ihn zugreifen können. Die Regel-Liste nennt „von N im
    Contract“. Das Regel-Detail blendet die übrigen Consumer bzw. Provider mit ihren VMs aus,
    „alle zeigen“ holt sie zurück. Firewalls mit mehreren VDOMs stehen mit dem VDOM der Policy
    da.
- **Findings:** oben eine **Übersicht nach Kategorien** (IPAM-Abgleich, Namenskonvention,
  Bereinigung, ACI / VMware) mit offenen/gesamten Befunden je Typ (Klick filtert). Neu mit
  dem vCenter: *VM-IP fehlt im IPAM* und *Portgruppe nach ACI-Schema ohne EPG*. Je Befund ein Satz,
  **was genau nicht stimmt**, und ein Hinweis, was zu tun wäre – z. B. „172.16.5.9 ist im IPAM
  weder als IP noch in einem bekannten Netz erfasst. Den Server db01.example.ch kennt das
  IPAM unter 10.30.0.9. Firewall-Objekt H_db01 …, verwendet in 1 Regel“. Aufklappen zeigt alle
  Angaben und Links auf die betroffenen Regeln. Filter Status/Typ, Suche; Status setzen
  (Triage) als Operator – bleibt über spätere Läufe erhalten (Findings werden nach jedem Lauf
  neu berechnet). Der Abgleich berücksichtigt, dass das IPAM den Server **ohne Präfix**
  führt: `H_web01` passt zu `web01` bzw. `web01.example.ch` (Kurzname nur, wenn eindeutig).
  **Externe Adressen** (seit 0.76.1): öffentliche Hosts und Netze, die in keinem Netz der
  gelesenen Geräte liegen (Interface-Netze der Firewalls inkl. WAN, ACI-BD/-EPG-Subnetze),
  erwartet niemand im IPAM – kein Befund „nicht im IPAM“, Status „extern (ohne IPAM)“. Eine
  öffentliche Adresse im Interface-Netz am WAN-Anschluss bleibt ein Befund, ebenso das WAN-Netz
  selbst („Subnetz fehlt im IPAM“), private Adressen und VIPs. Steht die Adresse später im
  IPAM, gilt sie wieder als gefunden.
  **Fenster (0.109.1):** Seitenpanels und Dialoge lassen sich am Rand mit der Maus breiter bzw.
  schmaler ziehen (die Breite merkt sich der Browser je Art; Doppelklick auf den Rand = Vorgabe),
  **⤢** öffnet sie fast über die ganze Breite, **⤡** zurück. Die Vorgabe richtet sich nach dem
  Bildschirm; lange Namen in Tabellen der Panels brechen um, wenn der Platz fehlt.
  **Finding von Hand (0.109.0):** „+ Finding erfassen“ oben (Operator, nicht für Kunden-Benutzer)
  bzw. „Als Finding erfassen“ in „Warum dieser Weg?“: was nicht stimmt, was anzupassen ist,
  Schwere, Gerät, Quelle/Ziel/Dienst. Typ „Von Hand erfasst“ (Kategorie „Von Hand“); der Abgleich
  nach einem Lauf lässt es stehen, Triage wie bei jedem Finding (z. B. „erledigt“, sobald die
  Konfiguration angepasst ist). Im Audit `finding.created` ohne Titel und Text.
  **Prüfungen (0.110.0, ADR 0039):** Reiter „Prüfungen“ (`#findings?tab=pruefungen`) – je
  Prüfung Zweck, Modul, Befund-Typen (Klick zeigt die Befunde des Typs), wann sie läuft (nach
  jedem Lauf; Zonen auch beim Speichern einer Zone, Stand der Quellsysteme auch im Zeitplan,
  Applikationen im Hintergrund), **Dauer** und **Anzahl** des letzten Durchgangs (seit dem Start
  des Orchestrators; „–“ = seither nicht gelaufen bzw. ausgeschaltet). Der **Admin** schaltet eine
  Prüfung je Mandant aus bzw. ein (Audit `check.switched`; `GET /checks?tenant_id=`,
  `PUT /checks/{key}?tenant_id=`): aus = sie läuft nicht, ihre Befunde fallen beim nächsten
  Abgleich weg (nach dem nächsten Lauf bzw. mit „Neu abgleichen“, Operator); wieder an = sie
  kommen mit ihrem Status zurück. Ist das Modul einer Prüfung aus, steht „Modul aus“.
- **Abgleich:** Referenzquellen des Mandanten (führend/Anreicherung, eingelesen?), Kacheln je
  Befund-Typ (Klick → Findings vorgefiltert): nicht im IPAM, Name ≠ DNS-Name, Subnetz fehlt
  im IPAM, VLAN weicht ab, Namenskonvention, Präfix/Typ, doppelte Werte, unbenutzte
  Objekte. Darunter **Vergleich DDI ↔ NetBox** (fehlt in NetBox / abweichend / nur in
  NetBox) mit Suche. **Jetzt abgleichen** (Operator) rechnet neu, ohne etwas zu lesen oder
  zu schreiben.
- **Netzwerkdesign** (0.51.0, ADR 0024): logische Zonen, Zonenmatrix und Zonenplan – in
  Automato gepflegt, rein lesend gegenüber den Geräten. **Kunden-Benutzer** (seit 0.53.0) sehen
  ihre Zonen (ohne Selektoren), die geteilten Zonen dahinter nur mit Namen, die Matrix
  mit Zellen ihrer Zonen (Zonen anderer Kunden als „anderer Kunde“), ihren Plan ohne Geräte und
  ohne Netze ohne Zone und können ihn exportieren; keine Versionen, keine Änderungen.
  **Speichern ohne Warten (seit 0.108.0):** Zone anlegen, ändern, löschen, Absichten,
  Vorschläge, Zonenplan-Import, Kategorien und Grundschema kehren sofort zurück; die Befunde der
  Zonen (Absicht, ausserhalb des Schemas, Netz nicht über den Ausgang) rechnet der Orchestrator
  danach im Hintergrund – in der Liste der Findings stehen sie nach einigen Sekunden. Ebenso nach
  einem geänderten Verbund paralleler Firewalls.
  - **Zonenmodell** (0.56.0, ADR 0024 Nr. 12): Zonen nach Ebenen – **Datacenter** (die
    ACI-Tenants), **Edge** (Internet, Office-Anbindung, VPN, Management), **Transfer** (zwischen
    den Firewalls), **Core**, **Distribution** und **Access** (Office, Branch), **Public**.
    - Eine Zone umfasst **Knoten** (Selektor VDOM einer Firewall, ACI-Tenant bzw. ACI-VRF) und
      **Netze**. Netze zwischen Knoten verschiedener Zonen sind **Übergänge** („DC-EINET“: die
      L3Out der VRF zur Internet-Firewall) und gehören keiner Zone; Firewalls derselben Zone
      untereinander sind zonenintern (ihr Transfernetz liegt in der Zone). Ein LAN direkt am
      Interface einer Firewall (ohne Router dahinter) bleibt Netz seiner Zone, das Interface ist
      der Übergang.
    - Zonen ohne eigene Netze sind normal – z. B. Edge Internet: ein Bein zur L3Out des DC, eines
      in Public.
    - **Zone nur hinter einem Router** (0.81.0): liegt ein Netz einer Zone (z. B. ein Standort
      10.88.0.0/16) an keinem gelesenen Gerät, routet aber eine Firewall bzw. VRF es über einen
      Router ohne gelesenes Gerät, ist das Netz dieses Routers der Übergang zur Zone. Der
      Übergang nennt die Route („Route 10.88.0.0/16“). Die Default-Route zählt nicht – ohne
      genauere Route bleibt es beim Übergang nach „ausserhalb“.
    - Namen der Übergänge aus den Kurznamen, nach Ebene geordnet (DC vor Edge vor Public).
    - **Farben** kommen aus dem Grundschema bzw. der eingebauten Vorlage und sind überall gleich;
      im Editor ist die Farbe dann fest (ändern im Grundschema der Namensprofile). Nur Zonen ohne
      Treffer in Schema bzw. Vorlage haben ihre eigene Farbe.
  - **Zonen** (Operator schreibt): Name, Kurzname, Art (Datacenter, Edge, Transfer, Core,
    Distribution, Access, Public/extern, dazu intern, DMZ, Management, geteilt), Farbe,
    Reihenfolge, Kunde (leer = geteilt bzw. Infrastruktur).
    - Selektoren wie beim Kunden (ACI-Tenant/VRF/AP/EPG/BD, VDOM/Zone/Interface je Firewall,
      Netz, Kürzel, IPAM-Merkmal), dazu **Anbindung**: die Netze hinter einer Anbindung aus
      Routing → Anbindungen. Ein- bzw. ausschliessend; die Vorschau zeigt Netze, EPGs und
      gleiche Netze in anderen Zonen.
    - **Vorschläge beim Erfassen** (0.66.0): sobald Name bzw. Kurzname stehen, schlägt der Editor
      Selektoren vor, deren Name passt – FortiGate-Zonen (je Firewall), Interfaces (Name,
      Beschreibung; Rolle dmz bzw. wan, wenn die Art DMZ bzw. extern ist), VDOMs, Netze im IPAM
      (Name, Beschreibung, VLAN-Name), IPAM-Merkmale und -Tags, ACI-Tenant/VRF/AP/BD/EPG und
      Anbindungen. Je Vorschlag der Grund („gleicher Name“, „Name in …“, Muster des Grundschemas),
      die Netze und ein Hinweis, wenn sie schon in einer anderen Zone liegen bzw. die Zone sie
      schon erfasst. **+ Übernehmen** fügt den Selektor an, **Alle ohne Überschneidung
      übernehmen** alle ohne Hinweis; gespeichert wird erst mit der Zone.
    - Eine IP gehört zur Zone mit dem **längsten Präfix** (DMZ 10.1.5.0/24 in Server
      10.1.0.0/16 geht vor). Internet am besten als Anbindung „Internet“ **ohne** die privaten
      Netze (Netz 10.0.0.0/8 usw. ausschliessen) – sonst landen interne Netze ohne Zone im
      Internet.
    - Klick auf eine Zone zeigt ihre Knoten, ihre Netze mit Grund (Link in die Regel-Suche) und
      die Übergänge zu anderen Zonen mit Netz und Interface bzw. L3Out; die Liste zeigt je Zone
      Knoten und Übergänge.
  - **Vorschläge** (0.54.0, seit 0.56.0 nach dem Zonenmodell): Automato schlägt Zonen aus dem
    Gelesenen vor – die **ACI-Tenants** als Datacenter (je Kunde eine Zone „DC <Kunde>“, der Kunde
    über seinen Selektor ACI-Tenant), **Firewalls** in die Zone des Grundschemas bzw. der Vorlage,
    deren Name oder Muster passt (z. B. `*inet*` → Edge Internet), Firewalls mit einem Bein an
    einer Internet-Anbindung in **Edge Internet**, die Anbindungen in **Public** (ohne die
    privaten Netze), Firewall-Zonen und Interfaces nach den Mustern (z. B. `transit` → Edge
    Transfer, `mgmt` → Edge Management) und das IPAM-Merkmal Zone bzw. Rolle – seit 0.65.1 ohne
    Werte, die ein Objekt im ACI nennen (Tenant, VRF, AP, BD, EPG, ESG, L3Out; z. B. die ESGs
    `SZ_…`): das sind Gruppen im ACI, das ACI ist schon das Datacenter.
    - Je Vorschlag Art (mit Grund), Kurzname, Kunde, woraus erkannt, die Selektoren, die Knoten
      und die Netze. „ergänzt Zone“: es gibt schon eine Zone mit dem Namen bzw. derselben Zone der
      Vorlage (z. B. „Internet“ = Public) – übernehmen fügt nur die neuen Selektoren hinzu.
    - Netze, die genau so schon in einer Zone liegen, schliesst der Vorschlag aus (ausschliessender
      Selektor) – nach dem Übernehmen gibt es keinen Befund „Netz in mehreren Zonen“.
    - **Übernehmen…** öffnet den Zonen-Editor mit dem Vorschlag (Name, Kurzname, Art, Selektoren
      anpassen); **Ausgewählte übernehmen** legt mehrere Zonen auf einmal an (Operator).
    - **Knoten ohne Zone:** Firewalls bzw. VDOMs ohne passende Zone – Zone wählen und
      **Zuordnen** (Operator, Selektor VDOM).
    - **Ausblenden** (Operator): der Vorschlag erscheint nicht mehr; „ausgeblendete zeigen“ →
      **Wieder vorschlagen**. Seit 0.65.1 **filtern** (Name, Kurzname, Kunde, Herkunft,
      Selektoren, z. B. `SZ_`), mit dem Kästchen im Kopf alle Treffer wählen und **Ausgewählte
      ausblenden**.
    - **Nicht erkannt:** gelesene Netze an Knoten ohne Zone und ohne Vorschlag – den Knoten bzw.
      das Netz von Hand einer Zone zuordnen.
    - Die Rolle der FortiGate-Interfaces liest der Agent ab 0.54.0 mit; bis zum nächsten Lauf
      fehlt sie (dann nur Name bzw. Management-Tabelle). Die NetBox-Rolle erst nach dem nächsten
      Einlesen der NetBox.
    - **Mit Grundschema** (0.55.0, Namensprofile → Grundschema der Zonen): passt ein Name auf eine
      Zone des Schemas (Name, Kurzname bzw. Erkennungsmuster), übernimmt der Vorschlag deren
      Name, Art und Farbe (Chip „Schema“); Interfaces werden auch allein über die Muster erkannt
      (z. B. `srv*`). Vorschläge ohne Treffer sind „nicht im Schema“ (Filter „nur aus dem
      Grundschema“). Darunter die Karte **Grundschema**: Zonen des Schemas ohne Zone und ohne
      Vorschlag (**anlegen…** öffnet den Editor), die **Absichten** des Schemas, die in der
      Zonenmatrix fehlen bzw. abweichen (**Fehlende Absichten übernehmen**, abweichende einzeln
      **Ersetzen**; Operator), und die Zonen ausserhalb des Schemas. Seit 0.78.0 ist jede
      solche Zone auch ein Hinweis unter Findings → Zonen („Zone ausserhalb des Schemas“):
      umbenennen bzw. das Grundschema ergänzen – der Hinweis fällt beim Speichern weg.
  - **Zonenmatrix:** Zeilen = Quellzone, Spalten = Zielzone, dazu „any“ und „ohne Zone“. Je
    Zelle ✓ erlaubende und ✕ sperrende Regeln (Firewall-Policies und ACI-Contracts, ohne
    Zwillinge paralleler Firewalls); oben, wie viele Regeln zugeordnet sind (ohne IP-Bereich,
    z. B. nur FQDN, zählt nicht).
    - Klick auf eine Zelle: die Regeln (Link zur Regel) und – als Operator – die **Absicht**
      für das Zonenpaar: erlaubt, eingeschränkt, gesperrt, mit Notiz.
    - **Nach dem Weg** (0.81.0): eine Regel zählt für ein Zonenpaar nur, wenn ihr Gerät die
      Zonen erreichen kann – wie in der Doku. Eine FortiGate-Policy mit Eingang `srv` kann ein
      LAN, das die Firewall über `lan` erreicht, nicht als Quelle sehen; ein Contract nur die
      Adressen, die seine externe EPG bzw. sein Scope erfasst. Solche Paare stehen in der Zelle
      als „N nicht möglich“, nicht als erlaubend bzw. sperrend und nicht als Verstoss gegen die
      Absicht (auch kein Befund). Die Zelle zeigt sie mit „nach dem Weg nicht möglich“ samt
      Begründung; oben steht, wie viele Regeln das Routing ganz ausschliesst. Ohne gelesenes
      Routing bzw. mit Policy-Route oder SD-WAN zählt die Regel weiter (nicht sicher
      ausgeschlossen).
    - **Absicht je Dienst** (0.57.0): bei „eingeschränkt“ die erlaubten **Dienste** (Feld
      „Dienste“, durch Komma getrennt): `tcp/443`, `udp/53`, `tcp/8000-8080`, `tcp` (alle
      Ports), `icmp`, `any` bzw. der Name eines Service-Objekts oder ACI-Filters (`FT_https`).
      Die Zelle zeigt „nur tcp/443“. Eine erlaubende Regel mit anderen Diensten (auch `ALL`) ist
      gegen die Absicht; in den Regeln der Zelle steht „Dienste ausserhalb: tcp/22“. Ohne Dienste
      bleibt „eingeschränkt“ eine reine Absicht ohne Prüfung.
    - Eine erlaubende Regel zwischen Zonen mit Absicht „gesperrt“ (bzw. mit Diensten ausserhalb
      von „eingeschränkt“) ist rot umrandet und ein Befund (Findings → Zonen, „Zone: gegen die
      Absicht“). „any“ und „ohne Zone“ zählen nicht als Verstoss.
  - **Zonenplan** (seit 0.56.0 nach dem Zonenmodell): Zonen in Spalten nach Ebene (Datacenter
    links, Public rechts) mit ihren Knoten, Übergänge als Linien mit Namen („DC-EINET“);
    gestrichelt „Knoten ohne Zone“ und „ausserhalb“ (Router ohne gelesenes Gerät).
    - **Ansicht** (oben rechts): **Übersicht** – die Zonen der Kunden je Kunde als eine Zone, damit
      die Zeichnung überschaubar bleibt; **Kunde X** – seine Zonen, die genutzten Zugänge zu anderen
      Kunden (Übergang bzw. Regeln zwischen den Zonen) je als eine Zone und von den geteilten Zonen
      **nur die Firewalls/VRFs, die der Kunde braucht** (seit 0.57.1: an seinen Netzen und auf den
      kürzesten Wegen zu den Zonen seiner Regeln; geteilte Zonen ohne solche Knoten und ohne
      Übergang fallen weg, der Hinweis nennt die ausgeblendeten Knoten); **alle Zonen einzeln**.
      Ist in der Kopfzeile ein **Kunde** gewählt, öffnet der Zonenplan dessen Ansicht.
    - **Übersichtlich bei vielen Tenants und Zonen** (0.61.0) – die Leiste über der Zeichnung:
      - **Suchen** (Zone, Kurzname, Kunde, Firewall/VRF, Übergang wie „DMZ-EINET“, Netz) und
        **Fokus auf Zone …** (mehrere wählbar, × entfernt; Doppelklick auf eine Zone in der
        Zeichnung setzt sie in den Fokus): gezeigt werden die Treffer mit ihren Nachbarn – „nur
        die Treffer“, „+ direkte Nachbarn“ (Vorgabe) bzw. bis 3 Übergänge weit. Die Zeile darunter
        nennt „N von M Zonen gezeigt“.
      - **Ebenen**: Spalten ein-/ausblenden (DC, Kunden, Edge, Transfer, Core, Distribution,
        Access, Public, Knoten ohne Zone, ausserhalb) und Elemente (Knoten in den Zonen, Namen der
        Übergänge, Kontakte zu Knoten ohne Zone, Zonen ohne Knoten und Übergang). Die Auswahl
        merkt sich der Browser; „Filter zurücksetzen“ zeigt wieder alles.
      - **Zonen ziehen**: mit der Maus an einen anderen Platz. Als Operator wird die Anordnung
        der Ansicht gespeichert (alle Zonen der Ansicht behalten ihren Platz, neue Zonen kommen
        darunter); Linien folgen der Lage – seit 0.65.1 als Kurve, die die Zone an der Seite zur
        anderen Zone verlässt (nebeneinander rechts/links, übereinander unten/oben, mit einer
        Zone dazwischen rechts herum), Anschlüsse je Seite nach der Lage der Gegenseite geordnet. „Anordnung zurücksetzen“ ordnet wieder nach Ebenen.
        Ohne Operator-Recht ordnet das Ziehen nur die eigene Anzeige (bis zum Neuladen).
      - **Export** und neue **Versionen** zeigen dasselbe: gefiltert (Filter im Kopf der Datei)
        und angeordnet.
      - **Kommunikation (Regeln)** (0.66.0): je Paar Zonen die erlaubenden Regeln zwischen ihnen
        als blau gepunktete Linie mit der Zahl („2 Regeln“, im Titel je Richtung) – auch ohne
        Übergang dazwischen. Mit Fokus bzw. Suche kommen die Zonen dazu, mit denen die Treffer
        kommunizieren (z. B. Fokus auf einen Tenant: die Tenants, mit denen er Regeln hat). Im
        Plan eines Kunden stehen die Zonen der anderen Kunden, mit denen er kommuniziert, einzeln
        da („Partner“, ohne ihre Knoten). Der Browser merkt sich den Schalter; der Export zeigt
        die Linien mit.
      - **Absicht je Richtung** (0.78.0): an jedem Übergang zeigt je Richtung ein Pfeil am Ziel
        die Absicht aus der Zonenmatrix – grün erlaubt, orange eingeschränkt, rot gesperrt;
        kein Pfeil = keine Absicht. Der Titel der Linie nennt Richtung und Dienste („Public →
        Edge Internet: eingeschränkt (tcp/443)“). Haben zwei Zonen eine Absicht, aber keinen
        Übergang (z. B. LAN → DMZ über die Firewall dazwischen), steht dafür eine eigene, farbig
        gestrichelte Linie; die Karte „Absichten ohne Übergang“ listet sie, die Tabelle der
        Übergänge hat die Spalte „Absicht“. In der Übersicht gilt je Kunde die strengste Absicht
        seiner Zonen („teils weniger streng“, wenn sie verschieden sind). Ausblenden unter
        Elemente → „Absichten“. Kunden-Benutzer sehen die Absichten ihrer Zonen ohne Notiz.
    - Darunter die **Übergänge** mit Netz und den Beinen (Knoten mit Interface bzw. L3Out, Router),
      die Knoten ohne Zone an Netzen einer Zone (Kontakt, kein Übergang), Knoten in mehreren Zonen
      und die **Netze ohne Zone**.
    - **Export** (0.52.0, Knöpfe oben rechts) in der gewählten Ansicht: **draw.io** (in
      diagrams.net weiter bearbeitbar, Zonen mit Knoten und Übergänge mit Netzen als
      Eigenschaften; als Grundschema wieder einlesbar), **SVG** (Wiki, Präsentation), **PDF**
      (Plan, Zonenmatrix, Zonen mit Knoten und Netzen, Übergänge, seit 0.78.0 „Absicht je
      Richtung“, Netze ohne Zone). Dateiname `zonenplan-<Datum>` bzw. `zonenplan-v<Nummer>`.
      Die Pfeile der Absicht stehen in allen Formaten (draw.io: Pfeile an den Kanten).
      - Seit 0.71.0 auch **Word** (Zonen, Übergänge, Zonenmatrix als Tabelle der Paare mit
        Absicht, Diensten und Notiz, Netze ohne Zone) und **Für Confluence (Word + draw.io)**
        = ZIP mit der Word-Datei und der Zeichnung, beides wie die Ansicht gefiltert bzw. von
        der gewählten Version. Import in Confluence von Hand wie bei der Applikations-Doku
        (ADR 0026); Kunden-Benutzer erhalten ihre Sicht ohne Gerätenamen.
    - **draw.io einlesen…** (0.83.0, Operator): den exportierten Plan nach dem Bearbeiten in
      draw.io zurück nach Automato. Die Vorschau zeigt, was die Datei ändert; übernommen wird
      je Teil auf Wunsch:
      - **Lage:** alle Zonen an ihrem Platz in der Zeichnung – als Anordnung der Ansicht, aus der
        der Export stammt (steht im Titel der Datei, `automato_view`); „verschoben“ zählt die
        Zonen, die anders liegen als jetzt im Plan.
      - **Namen:** geänderte erste Zeile einer Zone = neuer Name. Wurde die Zone seit dem
        Export in Automato umbenannt, gilt Automato (Hinweis). Ein Name, den schon eine andere
        Zone trägt, wird übersprungen.
      - **Absichten:** neu gezogene Pfeile zwischen zwei Zonen (Beschriftung erlaubt,
        eingeschränkt, gesperrt bzw. gestrichelt/rot wie im Grundschema, Dienste über
        `automato_services`, Richtung = Pfeil). Die Linien des Exports (Übergänge, Kommunikation,
        Absichten) sind keine neue Absicht – um eine zu ändern, an der Linie die Eigenschaft
        `automato_intent` setzen. Notiz der Absicht „Zonenplan (draw.io): …“.
      - **Neue Formen** ohne `automato_*` als Zonen ohne Selektoren anlegen (vorgewählt: nein).
      - Gelöschte Formen löschen keine Zone („nicht in der Zeichnung (bleiben)“). Audit
        `zone_plan.imported`; danach werden die Zonen-Befunde neu berechnet.
  - **Versionen** (0.52.0): **Version speichern** (Operator, mit Bezeichnung und Notiz) hält
    Zonen, Plan, Matrix und Absichten fest. Oben steht „aktueller Stand = Version n“ bzw.
    „geändert seit Version n“ (verglichen wird das Design, nicht die Regel-Zahlen).
    - Klick auf eine Version: Vergleich mit dem aktuellen Stand oder einer anderen Version
      (Zonen, Absichten, Knoten der Zonen, Übergänge, Netze ohne Zone; aufklappbar die Zellen der
      Matrix mit anderen Zahlen), ihre Zeichnung und der Export dieser Version. Versionen bis
      0.55.0 behalten ihre alte Zeichnung (Zonen an den Knoten); der Vergleich mit dem aktuellen
      Stand zeigt beim ersten Mal den Wechsel des Modells.
    - **Löschen** nur als Admin: der Stand fällt weg, die Nummer bleibt vergeben. Versionen
      fallen nicht unter die Aufbewahrungsfristen.
  - Befunde: „Netz in mehreren Zonen“ (gleiches Präfix in zwei Zonen) und „gegen die
    Absicht“; sofort nach dem Speichern und nach jedem Lauf neu berechnet.

**Administration**
- **Benutzer & Rollen:** Rollen viewer/operator/admin, eigenes Konto, Links in die
  Keycloak-Benutzerverwaltung (`AUTOMATO_OIDC_ADMIN_CONSOLE_URL`, sonst aus dem Issuer
  abgeleitet). Benutzer selbst werden in Keycloak gepflegt (`tenant_ids` = Mandanten-ID).
- **Module je Mandant (0.107.0, ADR 0036):** Mandanten & Standorte → Mandant → Karte
  **Module** (Admin schaltet, Audit `module.switched`; `GET /modules?tenant_id=`,
  `PUT /modules/{key}?tenant_id=`). Kern und Einstellungen sind immer an; ein- bzw. ausschalten
  lassen sich **Worker** (Läufe), **Gateway** (Referenzquellen IPAM/DNS), **Analyse** (Routing,
  Verkabelung, Abgleich, Topologie, Datenqualität), **Wegsucher** (Weg-Suche, Verbindungen Ende
  zu Ende), **Findings**, **Doku** (Applikationen), **Design** (Netzwerkdesign) und der
  **Schreibpfad** (NetBox-Sync; braucht das Gateway – „Abhängigkeit aus“, einschalten erst mit
  dem Gateway). Aus heisst: Ansicht weg (auch die Karten Verbindungen bzw. Befunde der
  Applikations-Doku), API 403 „Modul … ist für diesen Mandanten ausgeschaltet“, keine Läufe
  (auch nicht nach Zeitplan) bzw. keine Befunde der Applikationen im Hintergrund. Die gelesenen
  Daten bleiben; wieder an = sofort wieder da.
- **Mandanten & Standorte (Admin schreibt):** **+ Mandant**, Kürzel, Namensprofil,
  Lese-Audit, **Standorte** (ADR 0015): *virtuell* (dreistellige Nummer, z. B. `000` =
  Stretched DC) und *physisch* (Ort-Code, z. B. `chzrh1`); **+ Standort** mit Art, Code, Name
  und – bei virtuellen – den physischen Standorten, an denen er läuft (**Zuordnen** ändert das
  später). Die Standort-Auswahl in Quellsystem und Applikation ist nach Art
  gruppiert. Den Standort aus dem Namen (Netz `N_<tenant>-000-…`, Cluster
  `fwc11-000-<tenant>`, Host `evh01-chzrh1-<tenant>`) zeigen Objekte, Inventar (Geräte,
  Subnetze) und der Quellsystem-Dialog als Vorschlag („?“ = Code nicht erfasst). Weiter: die **Quellsysteme des
  Mandanten** (**+ Quellsystem** / **Bearbeiten**: FortiGate/Cisco ACI/Infoblox DDI/NetBox/VMware/Switches & Router/NSX ALB,
  Adresse, **Standort** des Geräts (für den NetBox-Sync), **Verwaltet von** = Collector-Agent
  oder zentraler Worker, read-only Zugangsdaten – FortiGate: **API-Token**, Cisco ACI:
  **APIC-Benutzer/Passwort** + **ACI-Tenants** (leer = alle ausser infra/mgmt), Infoblox:
  **WAPI-Benutzer/Passwort**, VMware: **vCenter-Benutzer/Passwort** (Konto mit der Rolle
  *Read-only* auf dem vCenter-Objekt bzw. als globale Berechtigung, jeweils mit „An
  untergeordnete Objekte weitergeben“), Switches & Router (0.62.0, ADR 0025; bis 0.67.0
  „Cisco-Switches“): **SSH-Benutzer/
  Passwort** eines Kontos, das nur lesen darf (IOS/IOS-XE privilege 1 bzw. eine Parser-View mit
  den `show`-Befehlen des Lesevertrags, NX-OS Rolle `network-operator`, seit 0.68.0 Junos-Klasse
  `read-only`, Catalyst 1200/1300 Stufe 1) und statt der Adresse
  die **Switches** (eine Adresse je Zeile, `host` bzw. `host:port`, bis 1000) – ein Quellsystem
  ist eine Gruppe von Switches mit demselben Konto, gemischt aus Catalyst 9200L/9300/3650/4500
  (IOS/IOS-XE), Nexus 9000 (NX-OS), Catalyst 1200/1300 (Business-CLI, gelesen über eine
  begrenzte Sitzung, weil diese Geräte kein Exec annehmen) und Juniper MX/EX/QFX (Junos) – die
  Plattform erkennt der Collector je Gerät, „Verbindung prüfen“ nennt sie; die **SSH-Host-Schlüssel** hält der erste
  Lauf je Adresse fest (Fingerabdruck im Dialog, „vergessen“ nach Tausch des Geräts); seit 0.100.0
  (ADR 0033) **Zugang SSH | SNMPv3** – bei SNMPv3 statt SSH-Benutzer/Passwort ein **SNMPv3-Benutzer**
  (nur authPriv), **Auth-Protokoll** (SHA bzw. SHA-2, Vorgabe SHA-256) und **-Passwort**,
  **Priv-Protokoll** (AES-128/-192/-256, Vorgabe AES-128) und **-Passwort** (je mindestens 8
  Zeichen), optional **Context**; Adressen wie bei SSH (optional `:Port`, Standard UDP 161); ein
  Wechsel des Zugangs verwirft die hinterlegten Zugangsdaten, **NSX ALB (Avi)**
  (seit 0.77.0, ADR 0027): Adresse des Controllers, **Avi-Benutzer/Passwort** eines Kontos in einer
  Rolle nur mit Leserechten (Virtual Service, VS VIP, Pool, Pool Group, VRF Context, Application
  Profile, Tenant) und **Avi-Tenants** (kommagetrennt, leer = alle, die das Konto sieht) –
  Automato meldet sich an und ab und liest sonst nur; „Verbindung prüfen“ liest die Virtual
  Services des ersten gewählten Avi-Tenants (seit 0.78.3), NetBox: **Lese-Token** + optional **Schreib-Token** (nur
  NetBox-Sync, geht nie an einen Agenten) – nur schreibbar, verschlüsselt gespeichert, Anzeige
  nur „hinterlegt"; bei Infoblox/NetBox die **Rolle** *IPAM führend* oder *Anreicherung*
  (genau ein führendes IPAM je Mandant); TLS-Prüfung, aktiv; **Zeitplan** (stündlich,
  täglich 02:00, werktags 06:00, wöchentlich oder eigener Cron-Ausdruck; Zeitzone
  `AUTOMATO_SCHEDULER_TIMEZONE`, Standard Europe/Zurich – die Tabelle zeigt den nächsten
  Termin); Name je Mandant eindeutig; ADR 0009/0010). **Löschen:** Quellsystem (✕, Rückfrage nennt die mit
  gelöschten Regeln/NAT-Einträge) und **Mandant löschen** (nur nach Eintippen des Namens;
  löscht alle Daten des Mandanten inkl. seiner Collector-Agenten; Audit-Log bleibt).
  Altbestände ohne Mandant erscheinen unter „Nicht zugeordnete Quellsysteme“ zum Zuordnen.
- **Kunden (0.30.0, ADR 0018, Operator schreibt):** Ein Kunde ist ein Ausschnitt der
  Umgebung (des Mandanten). Statt Listen beschreiben ihn **Selektoren**:
  - ACI-Tenant, VRF (`TE_ka:VRF_ka` bzw. `VRF_ka`), Application Profile (`TE_ka/AP_007`),
    EPG (`TE_ka/AP_007/EPG_web` bzw. DN), Bridge Domain (`TE_ka/BD_web`);
  - VDOM, Zone, Interface (optional nur auf einer Firewall); Netz (CIDR); Kürzel aus der
    Namenskonvention (`TE_<kürzel>`, Server- und Objektnamen wie `web01-007-ka`).
  - Aus dem IPAM (0.31.0): IPAM-Tenant (NetBox), IPAM-VRF bzw. DDI-Netzansicht, IPAM-Tag bzw.
    Attribut (`Kunde=A`) – die Netze und Adressen mit diesem Merkmal.
  - Jeder Selektor schliesst ein oder – mit „ausschliessen“ – aus, z. B. „Tenant TE_ka
    ohne AP TE_ka/AP_900“ oder ein Netz ohne ein Teilnetz.
  - Die Maske ist seit 0.36.0 breit; Werte (z. B. DNs) sind ganz lesbar, ohne Querscrollen.
  - Was dazugehört, leitet Automato nach jedem Lauf und beim Speichern ab: EPGs, Netze (auch
    die Subnetze der EPGs und die Netze an zugeordneten Interfaces), Werte-Objekte in diesen
    Netzen, Regeln (Contracts mit einer EPG des Kunden; Firewall-Regeln im VDOM, an der Zone
    bzw. am Interface oder mit Quelle bzw. Ziel in einem Netz des Kunden, ohne `any`) und VMs
    (Portgruppe einer EPG, IP im Netz, Kürzel im Namen); NAT mit interner bzw. externer IP im
    Netz; Findings über ihr Subjekt (0.31.0). Ein Element kann mehreren Kunden gehören.
  - **Nach dem Weg (0.58.0):** Eine Regel, die nur über ein Netz des Kunden dazukommt, zählt nur,
    wenn der Verkehr sie erreichen kann – die Firewall leitet das Netz über den Ein- bzw. Ausgang
    der Regel (wie die Relevanz in der Doku), im ACI ordnet die VRF das Netz der externen EPG des
    Contracts zu (z. B. nicht über ein Subnetz nur mit `export-rtctrl`). Nicht prüfbar (Routing
    nicht gelesen, Policy-Route, Interface unbekannt) zählt sie weiter. Über VDOM, Zone,
    Interface bzw. EPG des Kunden zählt eine Regel immer.
  - Beim Bearbeiten zeigt die **Vorschau**, was die Selektoren erfassen, dazu die Zeile **„nicht
    auf dem Weg“** mit den so ausgeschlossenen Regeln (Begründung beim Darüberfahren). Klick auf
    einen Kunden zeigt die Zugehörigkeit je Art mit dem Grund.
  - **Vorschläge:** je ACI-Tenant ohne Kunden ein Kunde (Kürzel aus `TE_<kürzel>`), `common`
    als **geteilter Teil** (gilt für alle Kunden) – „Übernehmen“ bzw. „Anpassen…“.
  - **Keinem Kunden zugeordnet** zeigt EPGs, Regeln und VMs ohne Kunden.
  - **Kunden-Filter (0.32.0):** Oben neben dem Mandanten wählt „Kunde“ einen Kunden (je
    Umgebung gemerkt). Übersicht, Regeln, Objekte, NAT, Findings, VMs/Portgruppen, Doku je
    System und Applikationen zeigen dann nur seinen Ausschnitt; vom geteilten Teil zählen
    EPGs, Netze und Objekte, Regeln, NAT, Findings und VMs nur aus dem Kunden selbst (0.33.0).
    Seit 0.76.0 gilt er **für alle Ansichten**: Routing (nur seine VDOMs/VRFs; in geteilten
    VDOMs wie `root` bzw. VRFs aus `common` nur die Routen zu seinen Netzen), Topologie,
    Weg-Suche (Namen nur in seinen Netzen), Inventar, Verkabelung (Geräte, Kabel, Ports, VLANs
    und Endpunkte des Kunden), Abgleich, publizierte Dienste und Zonen. Oben steht „Ausschnitt
    <Kunde>“ mit „alle Kunden“ zum Aufheben; Elemente des geteilten Teils tragen „geteilt“.
    Läufe, Betriebszustand und Quellsysteme bleiben unverändert.
  - **Kunden-Benutzer (0.33.0):** eigene Logins für Kunden. In Keycloak die Realm-Rolle
    `customer`, das Attribut **Mandanten-IDs** (die Umgebung) und **Kunden-IDs** (IDs aus der
    Ansicht „Kunden“) setzen, Anleitung `deploy/keycloak/PROD-INSTALL.md` 9a/9b. Sie sehen
    Übersicht (ohne Betriebszustand/Läufe), Regeln, Objekte (ohne Netzinventar), NAT,
    Applikationen und Findings – nur ihren Ausschnitt; Regel-Zahlen nennen nur ihre Regeln.
    Kein Routing, keine Verwaltung, keine Quellsysteme; `admin` gilt für sie nicht, ohne
    Kunden-IDs sehen sie nichts. **Ohne Gerätenamen (0.58.0):** statt der Namen der
    Quellsysteme „Firewall 1“, „Firewall 2“ … (Reihenfolge der Anlage), „ACI“, „vCenter“, „IPAM“;
    VDOMs nummeriert („VDOM 2“, `root` bleibt), Verbund „Verbund 1“, Leafs/Spines „Leaf“/„Spine“,
    LLDP-Nachbarn „Nachbar“; ESXi-Host, Cluster, Datacenter und verteilter Switch leer – in allen
    Antworten, Texten und dem Export des Zonenplans. Betreuer sehen weiter die echten Namen. Namen
    der Infrastruktur in Regel-, Objekt- oder VM-Namen bzw. Kommentaren bleiben stehen (nur ganze
    Wörter werden ersetzt). Ihre Lesezugriffe stehen immer im Audit (`read.*` mit den
    Kunden-IDs). Bei SAML-Brokern Kunden-IDs und Rolle **fest je IdP** hinterlegen
    (`saml-mappers.example.json`). Vorführen im dev-Modus: im Browser
    `localStorage.setItem("automato_dev_customer", "<Mandant>:<Kunden>")`, entfernen zum
    Zurückschalten.
  - **Kunden-Benutzer in Automato zuordnen (0.53.0, Admin):** Karte „Kunden-Benutzer“ unten in
    Administration → Kunden. Benutzername bzw. E-Mail (wie in Keycloak) → Kunde, Rolle „lesen“
    oder „lesen + eigene Findings triagieren“, Notiz. Keycloak meldet nur, wer jemand ist – eine
    Rolle oder Attribute in Keycloak braucht der Benutzer dafür nicht. Die Zuordnung macht ihn
    immer zum Kunden-Benutzer (nie Admin, auch wenn Keycloak ihm mehr gibt); das eigene Konto
    lässt sich nicht zuordnen. Wirkt ab der nächsten Anfrage, Entfernen ebenso; jede Änderung im
    Audit (`customer_user.*`). Das Attribut `customer_ids` bleibt als Alternative, beides
    zusammen ergibt die Kunden beider Quellen. Vorführen im dev-Modus:
    `localStorage.setItem("automato_dev_user", "<Benutzer>")`.
  - **Kunden-Rollen (0.89.0, ADR 0018 Ergänzung „Feinere Rollen“):** je Zuordnung die Rolle
    **Lesen** (`viewer`), **Triage** (`triage`, eigene Findings quittieren/kommentieren – bisher
    „lesen + eigene Findings triagieren“), **Editor** (`editor`, zusätzlich eigene Applikationen
    pflegen: Stammdaten, Regeln zuordnen/ausschliessen, Regeln je Kategorie ausblenden – nur
    Applikationen, die ganz beim Kunden liegen; Chip „pflegbar“) oder **Kunden-Admin**
    (`customer_admin`, zusätzlich die Kunden-Benutzer des eigenen Kunden zuordnen – höchstens
    mit der eigenen Rolle, nie das eigene Konto, nie andere Kunden). Kunden-Admins finden unter
    Administration → Kunden-Benutzer nur ihre Kunden; ihre Zuordnungen wirken nie auf Betreuer.
    Die Rolle gilt je Kunde. Alternativ das Keycloak-Attribut `customer_role` (Mapper
    `customer_role`; im bestehenden Realm von Hand anlegen, `deploy/keycloak/README.md`;
    `AUTOMATO_OIDC_CUSTOMER_ROLE_CLAIM`); Claim und Zuordnung zusammen = je Kunde die höhere
    Rolle. Die Attribute `customer_ids`/`customer_role` im User Profile nur für Admins
    bearbeitbar machen. Anlegen, Löschen, CSV-Import und die Mitglieder AP/EPG/Server bleiben
    den Betreuern. Nach dem Update `alembic upgrade head` (Migration `0058`). Vorführen im
    dev-Modus: `automato_dev_customer` als `<Mandant>:<Kunden>:<Rolle>`, z. B. `1:2:editor`.
  - **Zonen des Kunden (0.53.0):** In Netzwerkdesign → Zonen „Kunde“ setzen; ihre Netze, EPGs und
    alles daran gehören dann zum Kunden (Spalte „Zonen“ in der Kundenliste). Die Selektoren des
    Kunden bleiben für Ausnahmen; ausschliessende gelten auch für seine Zonen.
- **Namensprofile:** Standardprofil **„default"** (per Migration angelegt, gilt für alle
  Mandanten ohne eigenes Profil, frei pflegbar), **+ Neues Profil** (Vorlage: Kopie eines
  bestehenden Profils, z. B. „default", oder leer), **+ Neue Namenskonvention** (optional die
  Einträge einer bestehenden Konvention – auch aus einem anderen Profil – kopieren),
  Einträge anlegen/bearbeiten/löschen.
  - **Service- und Filter-Präfix** (0.36.0): In „Objekt-Prefixe → Typ“ steht `FT_` mit dem Typ
    **„Service / ACI-Filter“** (Migration `0045` im Profil „default“). Darüber prüft Automato die
    Namen der FortiGate-Services und ACI-Filter; weitere bzw. andere Präfixe hier mit diesem
    Typ eintragen. Ohne Eintrag gilt weiter `FT_` (vorher war das Präfix fest eingebaut und nur
    über die ausgeblendete Wörterbuch-Art `service_prefix` erweiterbar – die gilt weiter).
  - Die **Projektnummern** sind nicht mehr Teil der Namenskonvention (0.36.0): die Tabelle ist
    ausgeblendet, vorhandene Einträge bleiben gespeichert.
  - **Grundschema der Zonen** (0.55.0, ADR 0024 Nr. 11): die möglichen Zonen und die Absicht
    zwischen ihnen als **draw.io-Zeichnung** (diagrams.net).
    - **Vorlage (draw.io)** herunterladen (Beispiel-Zonen Internet, DMZ, Server, Clients, Shared
      Services, Management, Transfer mit Mustern und Übergängen) bzw. **aus den Zonen von …**
      einem Mandanten als Ausgangspunkt; in draw.io anpassen.
    - Zeichnen: jede Form = Zone (erste Zeile = Name, weitere = Beschreibung, Füllfarbe =
      Farbe), Text und Beschriftungen zählen nicht, eine Form mit Zonen darin ist eine Gruppe.
      Unter „Daten bearbeiten“ (Rechtsklick → Edit Data): `automato_kind` (intern, DMZ, extern,
      Management, geteilt, Transfer), `automato_code` (Kurzname), `automato_match`
      (Erkennungsmuster, mit Komma getrennt, z. B. `dmz*, *-web`), `automato_order`.
    - **Vorlage je Kunde** (seit 0.82.0): `automato_customer` = „je Kunde“ (bzw. ja, 1) – im
      Editor das Häkchen „je Kunde“. Vorgeschlagen wird dann je Kunde eine eigene Zone
      „<Zone> <Kunde>“ mit Kurzname „<Kurzname>-<Kürzel>“ (z. B. „Office Kunde B“, „OACC-KB“)
      und den Treffern, die ganz in den Netzen bzw. VDOMs genau dieses Kunden liegen
      (Interfaces, Firewall-Zonen, IPAM-Merkmale, Firewalls); Treffer keines Kunden bleiben in
      der Zone selbst. Zonen eines Kunden passen ohne Name bzw. Kürzel des Kunden auf die
      Vorlage (kein Hinweis „ausserhalb des Schemas“); die Absichten der Vorlage gelten nur
      zwischen Zonen desselben Kunden (bzw. mit geteilten Zonen).
    - Pfeil = Übergang: Beschriftung „erlaubt“, „eingeschränkt“ bzw. „gesperrt“ (oder
      `automato_intent`), sonst gestrichelt = eingeschränkt, rote Linie = gesperrt, sonst
      erlaubt; Linie ohne bzw. mit zwei Pfeilen gilt in beide Richtungen.
    - **Zeichnung hochladen…** (Admin, `.drawio`, höchstens 2 MB, auch komprimiert): Vorschau
      mit Zonen, Übergängen, Hinweisen und dem Unterschied zum gespeicherten Schema; **Als
      Grundschema speichern** ersetzt es. **Zeichnung herunterladen** gibt die hochgeladene Datei
      zurück, **Schema entfernen** löscht es (Zonen und Absichten bleiben).
    - **Bearbeiten** (seit 0.70.0, Admin): Zonen umbenennen (die Übergänge ziehen mit), Art,
      Kurzname, Farbe, Erkennung, Gruppe, Beschreibung, Reihenfolge (↑/↓), Zonen hinzufügen bzw.
      entfernen; Übergänge mit Absicht, Diensten (bei „eingeschränkt“) und Notiz. Doppelte Namen
      bzw. Übergänge sperren das Speichern. Danach steht „in der Oberfläche bearbeitet“ am
      Schema und **Zeichnung herunterladen** liefert den bearbeiteten Stand (Lage der Formen wie
      hochgeladen) – zum Weiterzeichnen in draw.io. Audit `zone_schema.edited`.
    - Das Schema gilt für alle Mandanten des Profils (bzw. „default“) und ist ohne Funktionszwang:
      ohne Schema arbeiten Zonen und Vorschläge wie bisher.
  - **Kategorien der Applikations-Doku** (0.104.0, ADR 0012 Ergänzung 0.104.0): die Auswahl je
    Zone im Netzwerkdesign → Kategorien und die Kategorien selbst – Vorgaben im Code, hier
    anpassen bzw. erweitern (Admin; gilt für die Mandanten des Profils bzw. „default“).
    - **Kategorien:** Titel deutsch/englisch, **Titel mit Zonen** (`{z}` = Namen der Zonen,
      z. B. „Zugriff aus {z}“; leer = Titel bleibt), **Spalten** (Standard, Internet ausgehend,
      Internet eingehend) und „nur mit Zone“ (erscheint nur mit einer Zone dieser Kategorie bzw.
      mit Zeilen). **+ Kategorie** legt eine eigene an (nächste freie Nummer, entfernbar).
      Eingebaute (1–11) bleiben; ACI intern (7/8/9) und unspezifisch (10) behalten ihre Rolle.
    - **Auswahl je Zone (Bereiche):** Kürzel (1–8 Kleinbuchstaben, Ziffern, `-`, `_`),
      Bezeichnung, **Kategorie eingehend** (Zugriffe aus der Zone) und **ausgehend** (Zugriffe des
      Systems in die Zone), **Merkmal** im Namen der Firewall-Zone (`L3O_inet`, gilt ohne Bereich
      an der Zone), **Vorschlag bei** (Muster nach dem Zonennamen, `*` = beliebig, z. B.
      `*partner*`), **angeboten** (eingebaute lassen sich ausschalten). **+ Bereich** legt einen
      eigenen an, z. B. „Partner“ → eigene Kategorie 12.
    - **Optionen:** ausgehende Firewall-Regeln mit breiter Quelle ausblenden (0.103.0),
      unspezifische Kategorie nur mit Zugriffen auf die Applikation (0.78.2).
    - **Speichern** übernimmt (gespeichert werden nur Abweichungen), die Doku der Applikationen
      ordnet beim nächsten Laden neu; **Auf Vorgaben zurücksetzen** entfernt die Anpassungen.
      Zonen mit einem nicht mehr angebotenen Bereich zählen wie ohne Bereich (die Auswahl zeigt
      „nicht mehr angeboten“). Audit `appdoc_catalog.saved` bzw. `appdoc_catalog.reset`.
- **Collector-Agenten (nur Admin):** **+ Agent** erzeugt das Token einmalig; Spalte
  **Geräte** (zugewiesene Quellsysteme); Status online/keine Meldung aus dem Heartbeat;
  Aktivieren/Deaktivieren. **Einrichtung Schritt für Schritt** direkt in der Ansicht: Geräte
  zuweisen, `agent.env`-Generator (nur Orchestrator-Adresse + Agent-Token, bleibt im Browser), Download des
  Agent-Pakets (`GET /agents/package`, ZIP ohne Zugangsdaten), PowerShell-Befehle zum
  Kopieren, Test und erster Lauf, Fehlerbehebung. Die geplante Aufgabe startet den Agenten
  alle 15 min (inkl. Selbst-Update-Prüfung); dazwischen bleibt er wach und fragt alle 20 s
  nach Läufen (`run-agent.ps1 -WatchSeconds`, von Hand einmal: `-Once`). Spalte **Version** mit Schalter
  **Auto-Update** (ADR 0011): Agenten aktualisieren sich vor jedem Lauf selbst, sobald das
  Orchestrator-Image ein neues Agent-Paket enthält („Update beim nächsten Lauf“ → „aktuell“);
  ist Auto-Update aus, zeigt die Liste „veraltet“ und das Paket wird manuell installiert.
- **Betriebszustand** (0.12.0): Ampel je Prüfung – Datenbank/Migrationen, Zeitplan, zentraler
  Worker (warten zentrale Läufe länger als 10 min?), Agenten (meldet sich seit 30 min nicht bzw.
  aktualisiert nicht), Quellsysteme (nie eingelesen, letzter Lauf fehlgeschlagen, älter als 7
  Tage), NetBox-Sync (fehlgeschlagen) und Sicherung (fehlt bzw. älter als 26 h). Vollständig auf
  der System-Seite, als Hinweis in der Übersicht, sobald etwas nicht stimmt.
- **System (nur Admin, ADR 0013):** Version, Migrationsstand DB/Code, Agent-Paket mit
  Signaturzustand und Anzahl aktueller Agenten, Anmeldung, Agent-Kanal (mTLS-Modus),
  Verschlüsselung von Audit-Log und Zugangsdaten, Zeitplan mit nächstem Termin, letzte
  Sicherung. Rein lesend, zeigt nie Schlüssel.
  - **Verbindungen** (0.40.0): „Jetzt prüfen“ prüft vom Orchestrator-Container aus – auf dem
    Weg, den Einlesen und NetBox-Sync nehmen:
    - Datenbank, Redis (Warteschlange zum zentralen Worker), Keycloak (bei OIDC die JWKS),
    - jedes zentral gelesene Quellsystem: Name, TCP, bei NetBox die API mit dem Lese-Token
      (nur lesend) und ob ein Schreib-Token für den Sync hinterlegt ist,
    - Systeme, die ein Agent liest, als Hinweis je Agent (prüft der Agent: `.\run-agent.ps1 -Check`).

    Je Ziel die Schritte (✓/✗) und die Abhilfe. Antwortet ein Dienst auf dem Docker-Host nicht
    (`host.docker.internal`), steht die passende ufw-Regel zum Kopieren da – schmal: nur das
    Netz des Containers, nur die Gateway-Adresse, nur der Port.
- **NetBox-Sync (nur Admin, ADR 0010):** einziger Schreibweg von Automato. Standorte werden
  NetBox-Sites in den Standort-Gruppen *Virtuelle Standorte* bzw. *Physische Standorte*, der
  Kommentar nennt die Zuordnung (ADR 0015; das Schreib-Token braucht dafür auch
  *DCIM › Site Group*). Ziel-NetBox
  wählen (Quellsystem mit Schreib-Token), Umfang *Netzinventar* (Geräte mit Ports, VLANs je
  Quellsystem, reale Subnetze inkl. ACI BD/EPG, VRFs), *DDI-Spiegel* (Netze, Ranges,
  IPs mit DNS-Namen aus dem führenden IPAM) und/oder *Virtualisierung* (ADR 0017: Cluster-Typ
  „VMware vSphere“, Cluster-Gruppe je Datacenter, Cluster, VMs mit Status, vCPU, RAM, Disk und
  Kommentar, VM-Interfaces mit MAC, Portgruppe, VLAN und EPG, IPs am VM-Interface; ohne
  Vorlagen), optional *Löschen zulassen*. Von Hand gepflegte IPs (NetBox als IPAM) bleiben
  unverändert; fehlt nur die Zuordnung zur VM, steht das als Konflikt da. Ohne den Abschnitt
  *Virtualisierung* bleiben VMs in NetBox unangetastet. Für diesen Abschnitt braucht der
  Sync-Benutzer die Rechte auf `virtualization.*` – `source-of-truth/netbox/sync-user.sh`
  nach dem Update erneut ausführen (idempotent). Seit 0.93.0 zwei weitere Abschnitte (ADR 0031,
  nur ausdrücklich gewählt, Vorgabe aus): *Verkabelung* – gelesene Switches & Router mit Ports
  und Kabeln zwischen zwei Ports, die beide Seiten per CDP/LLDP melden; Modus/VLANs nur, wenn die
  VLANs in NetBox eindeutig vorhanden sind (sonst Hinweis, Automato legt dafür keine VLANs an);
  Kabel zu ACI-Leafs brauchen deren Ports (Abschnitt Netzinventar mitwählen); ohne Standort bzw.
  Modell kein Gerät (Hinweis); belegter Port = Konflikt, umgesteckter Port mit „Löschen
  zulassen“ in einem Durchgang. *Applikationen* – je Applikation ein Tag `automato-app-…` an VMs
  und IP-Adressen ihrer Systeme (nur Objekte mit Tag `automato`, die es in NetBox gibt bzw. die
  der Sync anlegt – Virtualisierung/DDI-Spiegel mitwählen); fremde Tags bleiben, von Hand
  gepflegte Objekte sind ein Konflikt, Tags gelöschter Applikationen nur mit „Löschen
  zulassen“ entfernt. Objekte eines nicht gewählten Abschnitts bleiben unangetastet (auch nicht
  „veraltet“). Für Kabel braucht der Sync-Benutzer `dcim.cable` – `sync-user.sh` erneut
  ausführen. **Vorschau
  erstellen** liest NetBox nur und zeigt je Art: anlegen / ändern / unverändert / Konflikt
  (von Hand gepflegtes Objekt weicht ab – bleibt unverändert) / löschen / veraltet, mit
  Aktionsliste. **Übernehmen** (Bestätigung) berechnet neu, schreibt nur bei unveränderter
  Lage, auditiert und liest NetBox danach neu ein. Seit 0.115.0 (ADR 0042) folgt dem Schreiben
  die **Nachkontrolle**: Automato liest NetBox über einen eigenen, rein lesenden Zugang (Lese-Token,
  sonst Schreib-Token – Schreib-Methoden blockiert) neu und vergleicht jedes geschriebene Objekt.
  Am Plan steht „Nachkontrolle: geschrieben = gelesen (n)“ bzw. „n Abweichungen von m“ mit Tabelle
  (Art, Objekt, geschrieben, nach dem Lesen: fehlt in NetBox / andere Werte mit Feld alt → neu /
  noch in NetBox); je Abweichung ein Befund unter Findings → NetBox-Sync. Ursache meist ein
  Validator, eine Event Rule bzw. ein Skript in NetBox oder ein fehlendes Recht des Tokens; nach
  dem Beheben neue Vorschau und erneut übernehmen. Ist NetBox beim Zurücklesen nicht erreichbar,
  bleibt die Übernahme gültig („Nachkontrolle nicht möglich: …“). Geändert/gelöscht werden nur
  Objekte mit Tag `automato`. Geräte ohne Standort werden übersprungen (Hinweis) – Standort am
  Quellsystem setzen (bei genau einem Standort des Mandanten wird dieser genutzt).

## 4. API

Lesen = `viewer`, Läufe/Triage = `operator`, Verwaltung = `admin` (mandantsgescoped).

| Endpunkt | Zweck |
|---|---|
| `GET /health`, `GET /auth/config` | Status (API/DB), öffentliche Login-Konfiguration |
| `GET /rules` (IPv4/IPv6-Paare nebeneinander; `?q=`,`?object=`,`?nat=`,`?tenant_id=`,`?domain=firewall\|aci_contract`,`?source_system_id=`,`?src_zone=`,`?dst_zone=`,`?action=`,`?limit=`,`?offset=`), `GET /rules/{id}` | Regeln und ACI-Contracts seitenweise (Standard 500, Gesamtzahl im Header `X-Total-Count`; Suche über alles, was zur Regel gehört; `object` = Regeln mit diesem Objekt auch über Gruppen, `nat` = mit diesem VIP/SNAT-Pool; je Regel `nat`) / Detail (Objektnamen, `refs` mit Referenzdaten, `aci`, `comment`, `nat_entries`) |
| `GET /rules/summary` (`?tenant_id=`) | Kennzahlen aller Regeln für die Übersicht (je Art, Endpunkte, nicht aufgelöst, Quell-/Ziel-Zonen, Aktionen) |
| `GET /objects?tenant_id=` (`category`, `source_system_id`, `q`, `issues_only`), `GET /objects/{id}` | Firewall-Objekte mit IPAM-Abgleich und `attrs` / Detail mit Mitgliedern und Regeln (`rules`, `rule_count`) |
| `GET /inventory/{devices\|interfaces\|vlans\|subnets\|vrfs}?tenant_id=` | Netzinventar (Subnetze mit IPAM-Abgleich) |
| `GET /vms?tenant_id=` (`q`, `source_system_id`, `epg`, `limit`, `offset`), `GET /portgroups?tenant_id=` (`q`), `GET /portgroups/{id}` | VMware-Inventar (ADR 0017): VMs mit NICs, IPs (DNS, im IPAM?) und EPG der Portgruppe, Seite mit `total`; Portgruppen mit EPG und Art der Zuordnung; Detail mit EPG (BD/VRF), Subnetzen je Quelle und VMs |
| `GET /reconcile/summary`, `GET /reconcile/ipam`, `POST /reconcile` | Abgleich: Befunde je Typ, DDI ↔ NetBox, neu berechnen (operator) |
| `GET /findings` (`?status=`,`?type=`,`?q=`,`?limit=`,`?offset=`, `X-Total-Count`), `GET /findings/summary`, `PATCH /findings/{id}` | Datenqualität seitenweise (je Befund `message` = was nicht stimmt, `hint` = was tun), Zähler je Status/Typ, Triage |
| `GET /nat` (`?nat_type=`,`?q=`,`?issues_only=`,`?limit=`,`?offset=`, `X-Total-Count`), `GET /nat/{id}` | Doku: NAT mit DNS-Namen, Server, Regelzahl und Prüfung (`issues`) / Detail mit IPAM-Sicht, Regeln, VMs und `object_id`, publizierte Dienste (VIPs), Doku je System |
| `GET/POST /applications`, `GET/PATCH/DELETE /applications/{id}`, `POST …/{id}/members`, `DELETE …/{id}/members/{mid}`, `POST …/{id}/rules`, `GET …/{id}/doc`, `GET …/{id}/connections` (seit 0.67.0 mit `plan`), `GET …/{id}/connections/plan/export?format=svg\|drawio[&key=…]` (0.67.0), `POST …/{id}/hidden` (`rule_ids`, `category`, `reason`; 0.69.0), `DELETE …/{id}/hidden/{hide_id}`, `GET …/{id}/export?format=zip\|docx` (0.71.0, nicht Kunden-Benutzer) | Applikationen (ADR 0012; Schreiben = operator): Stammdaten (inkl. `external_name`, `internal_name`, `purpose` dev/test/prod), Mitglieder AP/EPG/Server und Regeln (`rule`/`norule`, seit 0.60.0 über `POST …/rules` mit `rule_id`, `exclude`), Doku mit `summary`, `systems` (VMs), `epgs`, `hits` (Regel × System: Richtung, Bezug, `scope` inkl. `assigned`), `rules`, `rule_members`, `dependencies` (`uses`/`used_by`), `findings`, `tables` |
| `GET /applications/candidates` (`?kind=`,`?q=`), `GET /applications/suggestions`, `POST /applications/suggestions/{nr}/accept` | Mögliche Mitglieder; Vorschläge nach Projektnummer übernehmen (operator) |
| `GET/POST /tenants`, `PATCH /tenants/{id}`, `DELETE /tenants/{id}?confirm=<Name>` | Mandanten |
| `GET/POST /customers`, `PUT/DELETE /customers/{id}`, `GET …/{id}/members?kind=`, `POST /customers/preview`, `GET /customers/suggestions`, `GET /customers/unassigned` (je `tenant_id`) | Kunden (ADR 0018; Schreiben = operator; Filter `customer_id` an den Listen, 0.32.0): Selektoren (`kind`, `value`, `source_system_id`, `exclude`), `shared`, Anzahl je Art (`epg`, `net`, `object`, `rule`, `vm`); Vorschau ohne Speichern (seit 0.58.0 mit `off_path`: Regeln über Netze nicht auf dem Weg, `count`/`samples` mit `why`); Vorschläge je ACI-Tenant; nicht Zugeordnetes |
| `GET/POST /zones`, `PUT/DELETE /zones/{id}`, `GET /zones/{id}/nets`, `POST /zones/preview`, `GET /zones/matrix`, `GET /zones/matrix/rules?src=&dst=`, `PUT /zones/policy` (seit 0.57.0 mit `services`), `GET /zones/plan[?customer_id=&all=true]` (je `tenant_id`); seit 0.52.0 `GET /zones/plan/export?format=svg\|drawio\|pdf[&version_id=]` (seit 0.71.0 auch `docx`, `zip` = Word + draw.io), `GET/POST /zones/plan/versions`, `GET /zones/plan/versions/{id}[/diff?to=]`, `DELETE /zones/plan/versions/{id}` (admin); seit 0.54.0 `GET /zones/suggestions[?dismissed=true]`, `POST /zones/suggestions/accept` und `POST /zones/suggestions/dismiss[/undo]` (`keys`, operator); seit 0.55.0 `POST /zones/schema/intents` (`pairs`, `overwrite`, operator); seit 0.56.0 `POST /zones/{id}/nodes` (`nodes` = `<Quellsystem>\|<Kontext>`, operator) und `customer_id`/`all` auch am Export; seit 0.61.0 Filter an Plan und Export (`q`, `focus` mehrfach, `depth` 0–3, `hide_layer` mehrfach: dc/customer/edge/transfer/core/distribution/access/public/none/outside, `hide` mehrfach: nodes/labels/contacts/empty) und `PUT /zones/plan/arrangement` (`view` overview/all/customer:<id>, `positions` je Zone `[x, y]`), `DELETE /zones/plan/arrangement?view=` (operator); seit 0.66.0 `flows=true` an Plan und Export und `POST /zones/assist` (`name`, `code`, `kind`, `zone_id`, `selectors`; viewer, nicht für Kunden-Benutzer); seit 0.83.0 `POST /zones/plan/import/preview` und `POST /zones/plan/import` (`content` = draw.io, `positions`/`renames`/`intents`/`new_zones`; operator) | Logische Zonen (ADR 0024, 0.51.0; Schreiben = operator, nicht für Kunden-Benutzer): Selektoren wie beim Kunden plus `exit`; Netze je Zone mit Grund; Vorschau mit gleichen Netzen anderer Zonen; Matrix je Paar Quell-/Zielzone (`allow`, `deny`, `violations`, `intent`, Achsen inkl. `any`/`none`) und die Regeln einer Zelle; Absicht `allow`/`limited`/`deny` (leer = entfernen); Plan (seit 0.56.0 `format` 2) mit `view` (all/overview/customer), `customers`, `zones` (mit `nodes`, `internal`, zusammengefasste Kunden `collapsed`/`members`, Pseudo-Zonen `none`/`outside`), `nodes`, `transitions` (`name`, `networks` mit `legs`/`routers`, `l3out`, `contact`), `conflicts`, `unzoned`, `layout` (Zeichnung; seit 0.61.0 `offset`, `placed` je Zone), seit 0.61.0 `filter` (gezeigt/gesamt, `label`), `zone_choices`, `arrangement`, `positions`; Zonen mit `color_source` (schema/template/zone/kind), `nodes`, `transitions`; Export als Datei; Versionen mit `fingerprint` (Liste mit `current_fingerprint`), Stand `data` (Zonen, Plan, Matrix) und Vergleich (`zones`/`intents`/`attachments` mit `added`/`removed`/`changed`, `unzoned`, `matrix`, `same`); Vorschläge je `key` (normalisierter Name) mit `action` create/extend, `kind`/`kind_reason`, `customer_id`, `sources`, `evidence`, `selectors`, `counts` (nets/epgs/nodes), `nodes`, `code`, `conflicts`, `dismissed`, dazu `nodes` (Knoten ohne Zone) und `rest` (Netze ohne Zone und Vorschlag); übernehmen liefert `created`/`updated`/`missing`; seit 0.55.0 je Vorschlag `schema` und `schema` mit `missing`/`outside`/`intents`/`matched` (Grundschema), Absichten übernehmen liefert `changed`; seit 0.66.0 im Plan `flows` (`a`, `b`, `rules`, `ab`, `ba`), Zonen mit `partner`, Kanten mit `flow`; Vorschläge beim Erfassen mit `items` (`selector`, `source_label`, `label`, `reason`, `score`, `nets`, `epgs`, `samples`, `new`, `covered`, `conflicts`), `total`, `schema` |
| `GET /cabling/devices`, `GET /cabling/links` (`device`, `state` both/one_sided/unread, `q`, `issues`, `limit`, `offset`), `GET /cabling/ports` (`device`, `status` up/down/disabled, `vlan`, `q`, `limit`, `offset`), `GET /cabling/vlans`, `GET /cabling/sources` (je `tenant_id`); seit 0.63.0 `GET /cabling/endpoints` (`device`, `port`, `vlan`, `q`, `uplinks`, `limit`, `offset`) und `GET /cabling/locate?q=`; seit 0.64.0 `GET /cabling/plan` (`ends`, `vlan`) und `GET /cabling/failure` (`device` bzw. `link`, seit 0.65.0 je mehrfach); seit 0.65.0 `GET /cabling/plan/export` (`format` svg/drawio, `ends`, `vlan`) | Verkabelung (0.62.0, ADR 0025; viewer, nicht für Kunden-Benutzer): Geräte mit `summary` (devices, switches, links, `states`, issues, unread, seit 0.63.0 `endpoints`) und je Gerät `read`, `kind`, Ports/verbunden, Verbindungen, Abweichungen; Verbindungen mit `a`/`b` (Gerät, Port, Schnittstelle), `state`, `protocols`, `issues`; Ports der Switches mit `peer`, `link_state`, `issues`, `endpoints`; VLANs mit Namen und je Switch Access-Ports/Trunks; Quellsysteme der Switches; Endpunkte mit `summary` (macs, ports, vms, with_ip) und je MAC-Eintrag `mac`, `vlan`, `device`, `port`, `uplink`, `via` (Gegenstelle am Port), `vm`, `ips`, `names`, `epgs`, `first_seen`/`last_seen`; Suche mit `kind` (mac/ip/name), `ips` und je MAC `reason`, `locations`, `behind`, `earlier`, `seen_on`; Plan mit `nodes` (`layer`, `x`, `y`, `switching`, `vlan_present`), `edges` (`ids`, `links`, `state`, `issues`, `lags`, `ports`, `vlan` yes/no/partial/unknown, seit 0.65.0 `standby` und `stp`), `pairs` (vPC: `a`, `b`, `domain`), `width`/`height`, `vlan` (`present`, `islands`); Ausfall mit `devices`, `links`, `parts`, `main`, `cut_off`, `cut_off_keys` (422 ohne Gerät/Kabel bzw. mehr als 50 Angaben, 404 unbekannt); Ports seit 0.65.0 mit `vpc_peer_link`, `stp_blocked`, `stp_root`; Export als Datei (`Content-Disposition`) |
| `GET /auth/me`, `GET/POST /customer-users`, `PATCH/DELETE /customer-users/{id}` | Wer bin ich laut Orchestrator (Rollen, Mandanten, Kunden, `customer_source` claim/automato) – die Oberfläche richtet sich danach; Zuordnung Benutzer → Kunde (0.53.0, Admin): `login`, `customer_id`, `role` viewer/operator, `note` |
| `GET/POST /tenants/{id}/sites`, `PATCH/DELETE /tenants/{id}/sites/{sid}` | Standorte (`kind` virtual/physical, `physical_site_ids` bzw. `virtual_site_ids`) |
| `POST /systems/{id}/check` | Erreichbarkeit eines zentral gelesenen Quellsystems vom Orchestrator aus (operator): Schritte `dns`, `tcp`, bei NetBox `http` (`/api/status/` mit dem Lese-Token) je mit `ok`/`detail`, dazu `hint`; Agent-Systeme 409 (`run-agent.ps1 -Check`), 0.34.0 |
| `GET /systems/labels?tenant_id=` | Bezeichnungen der Quellsysteme (`id`, `name`, `platform`, `vdoms`, `parallel_group`) ohne Konfiguration; Kunden-Benutzer nur die Systeme ihrer Regeln, NAT bzw. Objekte (0.33.0), seit 0.58.0 mit neutralen Namen („Firewall 1“, VDOM-Nummern) wie alle Antworten an Kunden-Benutzer |
| `GET /systems/parallel?tenant_id=` | Verbünde paralleler Firewalls (0.72.0): Mitglieder (führend zuerst) und je Bereich `rules`, `objects`, `policy_routes`, `routes`, `nat` die Zahlen `same`/`differs`/`missing`; viewer, nicht Kunden-Benutzer |
| `GET /systems?tenant_id=`, `POST /systems`, `PATCH/DELETE /systems/{id}` | Quellsysteme (je Mandant; `agent_id`, `site_id`, `read_scope`, `ref_role`, `schedule` → `next_run_at`; Switches: `read_scope` = Adressen, `host_keys` (Adresse, Art, Fingerabdruck), `forget_host_keys` beim PATCH; `token` bzw. `username`/`password`, NetBox `write_token` nur schreibbar → `has_credentials`/`has_write_token`; Referenzquellen mit `reference_count`/`reference_at`) |
| `GET /jobs` (`tenant_id`, `status` inkl. `open`, `source_system_id`, `platform`, `trigger`, `limit`, `offset`; Gesamtzahl im Header `X-Total-Count`), `GET /jobs/{id}`, `POST /jobs/collect`, `POST /jobs/collect-all`, `POST /jobs/{id}/cancel` | Läufe (Gerät mit Agent → immer `external`, `started_at` = abgeholt); `collect-all` = alle aktiven Quellsysteme des Mandanten (operator, Antwort je System `run_id` bzw. `skipped` mit `blocking_run_id`); `cancel` beendet einen offenen Lauf (operator) |
| `GET/POST /agents`, `PATCH /agents/{id}`, `GET /agents/package` | Collector-Agenten (inkl. zugewiesener Geräte), Agent-Paket |
| `GET /internal/agent/next-run`, `…/targets`, `POST …/heartbeat` | Agent-Kanal (`X-Agent-Token`): Lauf + Zugangsdaten nur für zugewiesene Geräte |
| `POST /internal/collector-runs/{id}/result`, `GET …/{id}/target` | Ergebnis (Agent-Token des abholenden Agenten oder internes Token; Antwort = Quittung `status`/`message`); Ziel für den zentralen Worker |
| `POST /internal/collector-runs/{id}/references` | Teil-Upload einer Referenzquelle (Infoblox/NetBox, je bis 2000 Einträge; das Ergebnis meldet `reference_count`, sonst wird der Lauf verworfen) |
| `GET /netbox-sync/targets`, `GET/POST /netbox-sync/plans`, `GET …/plans/{id}[/items]`, `POST …/plans/{id}/apply` | NetBox-Sync (admin): Vorschau, Aktionen, Übernahme |
| `GET/POST /naming-profiles`, `…/{id}/dictionary`, `DELETE …/dictionary/{id}`; seit 0.55.0 `GET/PUT/DELETE …/{id}/zone-schema`, `POST …/{id}/zone-schema/preview`, `GET …/{id}/zone-schema/drawing`, `GET /naming-profiles/zone-schema/template[?tenant_id=]`; seit 0.104.0 `GET/PUT/DELETE …/{id}/appdoc-catalog`, `GET /naming-profiles/appdoc-catalog/default`, `GET /zones/appdoc/catalog?tenant_id=` | Namensprofile; Kategorien der Applikations-Doku (`categories`, `areas`, `options`; schreiben = admin; je Mandant mit `suggestions` je Zone); Grundschema der Zonen (`content` = draw.io-XML, `filename`; schreiben = admin): `schema` mit `zones` (Name, Art, Kurzname, Farbe, `match`, Gruppe, Position) und `links` (`src`, `dst`, `intent`, `reason`), `warnings`, Vorschau mit `diff`; Zeichnung bzw. Vorlage als Datei |
| `GET /system/status` | Betriebsstand (admin, siehe System-Seite) |
| `GET /system/connections` (`?tenant_id=`) | Verbindungen jetzt prüfen (admin, 0.40.0): `status`, `problems`, `container.network`, `items` je Ziel mit `area`, `status` (ok/warn/error/info), `steps`, `hint`, `ufw` (Regel); dasselbe auf der VM: `automato check-connections [--json]` |
| `GET /system/health` (`?tenant_id=`) | Betriebszustand als Ampel (viewer, mandantsgescoped): `status`, `problems`, `checks` je Prüfung mit `area`, `status` (ok/warn/error), `title`, `detail`, `view` |
| `POST /rules/{id}/pair` (`partner_id`, `decision` confirmed/rejected), `DELETE /rules/pair-decisions/{id}` | IPv4/IPv6-Paar bestätigen/ablehnen bzw. zurücknehmen (operator); Liste `GET /rules?pair=similar\|confirmed\|same\|any\|none` |

Agent-Aufrufe (`/internal/agent/*`, Ergebnis-/Referenz-Upload mit `X-Agent-Token`) nimmt der
Orchestrator mit `AUTOMATO_INTERNAL_MTLS_MODE=require|bind` nur über das mTLS-Agent-Gateway
an (`deploy/agent-gateway`, Port 8443).

## 4b. Versionen und Updates

- **Eine Version für alles** (Datei `VERSION`, SemVer): Orchestrator (`/health` → `version`,
  Fusszeile der Web-UI), Collector-Agent, SoT-Gateway, Web-UI. Neue Version:
  `python3 scripts/version.py set X.Y.Z`, Änderungen in `CHANGELOG.md`.
- **Orchestrator aktualisieren:** `deploy/update.sh` (bzw. `deploy/update.sh <tag>`):
  verschlüsselte Sicherung → Code holen → (Docker aktualisieren) → Images holen und bauen →
  starten → `/health`. Migrationen
  laufen beim Start (`alembic upgrade head`). Schlägt etwas fehl: `deploy/update.sh --rollback`
  setzt Code **und** Datenbank auf den Stand vor dem Update zurück (ADR 0013). Am Ende prüft
  `deploy/aufbau.sh pruefen` den Aufbau (nur Hinweis, kein Abbruch).
- **Aufbau prüfen** (0.40.0, nach dem Muster von Magisters `plattform-aufbau.sh`):
  - `deploy/aufbau.sh pruefen` – Werkzeuge (docker, compose, curl, git, python3), Docker-Dienst,
    Container (läuft, Health), `/health`, die Verbindungen aus dem Orchestrator-Container
    (`automato check-connections`, wie System → Verbindungen) und die Host-Firewall: ufw aktiv,
    Sperren aus Docker-Netzen der letzten Stunde (Kernel-Log), nötige Freigaben. Als root
    aufrufen, sonst bleibt ufw ungeprüft. Exit 0 = in Ordnung, 1 = Fehler.
  - `deploy/aufbau.sh firewall` zeigt die nötigen ufw-Regeln, `… firewall --anwenden` setzt
    sie (root) und prüft danach erneut. Beispiel:
    `ufw allow proto tcp from 172.20.0.0/16 to 172.17.0.1 port 8080 comment 'Automato -> netbox'`.
  - `deploy/aufbau.sh status` – Container und Health kurz.
  - **Profile:** `deploy/update.sh` baut und startet nur die Profile aus
    `AUTOMATO_COMPOSE_PROFILES` (Standard `--profile core`). Laufen weitere Dienste (zentraler
    Collector, Proxy), gehören sie dazu – einmal in `.env`:
    `AUTOMATO_COMPOSE_PROFILES=--profile core --profile collectors --profile proxy`. Seit
    0.42.2 meldet `pruefen` Container ausserhalb dieser Profile (sie blieben auf altem Stand).
  - `deploy/aufbau.sh backup-schluessel` (0.42.0) – legt den age-Schlüssel für die Sicherung an
    (bzw. nimmt den vorhandenen) und trägt Empfänger und Pfad in `.env` ein. `deploy/update.sh`
    bricht ohne Empfänger bei der Sicherung ab; seit 0.42.0 liest es ihn aus `.env`, ein
    `export` vor jedem Update ist nicht mehr nötig. `pruefen` meldet fehlenden Empfänger,
    fehlende Identität für den Rückfall und das Alter der letzten Sicherung.
  - Seit 0.41.0 prüft `pruefen` auch: startet Docker beim Booten, haben die Container eine
    Neustart-Regel, und – bei einer NetBox auf derselben VM (`source-of-truth/netbox/
    netbox-docker`, sonst `AUTOMATO_NETBOX_DIR`) – laufen ihre Container und antwortet die
    Login-Seite. `deploy/aufbau.sh netbox-start` startet sie und setzt die Neustart-Regel.
- **Docker und Images aktuell halten (0.43.0):**
  - **Images:** Jedes `deploy/update.sh` holt die Fremd-Images (Postgres, Redis, Keycloak,
    Caddy) und die Basis-Images der eigenen neu (`pull`, `build --pull`). Es nimmt den
    neusten Stand im festen Tag (`postgres:16-alpine`, `keycloak:26.0`), also
    Sicherheitskorrekturen. Ein Wechsel der Hauptversion bleibt eine Änderung an
    `docker-compose.yml`. Danach räumt es alte Image-Stände auf (`docker image prune`).
    Abschalten mit `AUTOMATO_PULL_IMAGES=0` in `.env`.
  - **Docker selbst** (Engine, Compose-Plugin, containerd):
    - `pruefen` zeigt im Abschnitt „Docker aktuell?“ je installiertem Paket die Version und
      die neuere aus der Paketliste.
    - `sudo deploy/aufbau.sh docker-update` hebt nur die installierten Docker-Pakete an
      (`apt-get install --only-upgrade`, `/etc/docker/daemon.json` bleibt). Der Docker-Dienst
      startet neu. Die Container kommen über ihre Neustart-Regel zurück, das Skript startet
      Automato und die lokale NetBox sicherheitshalber.
    - Bei jedem Update mit: `AUTOMATO_DOCKER_UPDATE=1` in `.env`. `deploy/update.sh`
      aktualisiert Docker dann nach der Sicherung und vor dem Bau.
    - Docker aus snap aktualisiert snap selbst. Das Ubuntu-Paket `docker.io` hinkt hinterher;
      aktuelle Versionen bringt das Docker-Repository (`download.docker.com`).
  - **Nicht automatisch** über `unattended-upgrades`: Dort startet der Docker-Dienst zu einer
    beliebigen Zeit neu, mitten in einem Lauf. Über `deploy/update.sh` geschieht es nach einer
    Sicherung und mit Health-Prüfung danach.
  - Die NetBox-Images (`netbox-docker`) aktualisiert Automato nicht. Ein NetBox-Update bringt
    Migrationen mit; vorher die NetBox-Datenbank sichern (Anleitung von netbox-docker).
- **Sicherung:** täglich `deploy/backup/backup.sh` (age-verschlüsselt: Datenbank, Schlüssel der
  Zugangsdaten, Agent-Signatur), Wiederherstellung `deploy/backup/restore.sh` – Details und
  Verschlüsselung at rest in `deploy/backup/README.md`.
- **Regelindex** (ADR 0016): Er wird beim Einlesen je Quellsystem berechnet, nach einem
  IPAM-Lauf und nach „neu abgleichen“ für den ganzen Mandanten. Fehlt er nach einem Update,
  baut ihn die erste Anfrage einmalig auf; bei grossen Mandanten dauert diese Anfrage einige
  Sekunden.
- **Agent-Log:** Jede Ausgabe des Agenten steht seit 0.11.4 zusätzlich in
  `<Installationsordner>\logs\agent-JJJJ-MM-TT.log` (14 Tage). Die geplante Aufgabe selbst
  zeigt keine Ausgaben. Dort stehen auch Meldungen wie „Update nicht installiert“ oder ein
  Fehler beim Lesen eines Geräts.
- **Agenten** ziehen beim nächsten Lauf selbst nach (ADR 0011). Im Wach-Modus prüfen sie seit
  0.11.4 alle 5 Minuten auf eine neue Version, vorher nur beim Start. Seit 0.16.0 bekommt ein
  Agent mit alter Version neue Läufe erst nach dem Update; ab 0.16.0 aktualisiert er sich dann
  sofort. So liest schon der erste Lauf nach einem Update mit der neuen Version. Wartet ein
  Lauf länger als 10 Minuten, holt ihn auch die alte Version ab. Klappt ein Update nicht,
  steht der Grund in der Agentenliste unter der Version (z. B. „Signatur fehlt“, „Selbst-Update
  auf der Station abgeschaltet“, „Update-Prüfung nicht möglich“). Die Agentenliste zeigt je Agent
  „0.2.0 · Build …“ und „aktuell“ bzw. „Update beim nächsten Lauf“. Manuell (`install.ps1`) nur
  für die Erstinstallation oder wenn Auto-Update aus ist. Produktiv das Paket nach jedem Update
  **signieren** (`deploy/agent-signing/README.md`); Agenten mit
  `AUTOMATO_AGENT_SIGNING_PUBKEY` installieren nur signierte Updates.

## 5. Sicherheit

- Alle Zielsystem-Zugänge **read-only** (Least Privilege). `.env` wird nie committet.
- **TLS intern** (0.49.0, ADR 0023, R14): Mit `AUTOMATO_INTERNAL_TLS=on` laufen alle
  Verbindungen zwischen den Containern verschlüsselt, jede Gegenstelle wird gegen die eigene CA
  geprüft:
  - Postgres nimmt über das Netz nur noch TLS an (`hostssl`), Orchestrator und Migration
    verbinden mit `verify-full`; im Container (Healthcheck, `pg_dump` der Sicherung) wie bisher;
  - Redis nur TLS (kein Klartext-Port), Orchestrator und Collector über `rediss://`;
  - Orchestrator und Web-UI per HTTPS; Collector, Web-UI, Proxy und Agent-Gateway prüfen ihn.
  ```bash
  deploy/aufbau.sh tls-intern             # CA + Zertifikate je Dienst, Schalter in .env, Neustart
  deploy/aufbau.sh tls-intern --erneuern  # Zertifikate neu ausstellen (CA bleibt), z. B. vor Ablauf
  ```
  Die Zertifikate liegen je Dienst unter `deploy/tls/pki/<dienst>` (gitignored; jeder
  Container sieht nur seinen Schlüssel), der CA-Schlüssel in `deploy/tls/pki/ca` und in keinem
  Container. Gültig: CA 10 Jahre, Dienste 825 Tage; `pruefen` warnt 30 Tage vorher, der
  Betriebszustand ebenso. Zeigt: Betriebszustand („TLS intern aktiv“), System → Verbindungen
  (Schritt `tls` bei Datenbank und Redis), System-Seite (Intern), `deploy/aufbau.sh pruefen`.
  **Danach beachten:**
  - Agenten, die direkt auf Port 8000 gehen (ohne Agent-Gateway): URL `https://<VM>:8000` und
    `AUTOMATO_ORCHESTRATOR_CA_BUNDLE` = `deploy/tls/pki/client/ca.crt` auf der Station. Über das
    Agent-Gateway ändert sich nichts.
  - Die Web-UI direkt auf `:5173` ist dann `https://` mit der internen CA (Browser-Warnung,
    CA importieren); über den Proxy (`:443`) wie bisher.
  - Zurück: `AUTOMATO_INTERNAL_TLS=off` in `.env`, `docker compose … up -d`.
- **Getrennte DB-Rollen** (0.48.0, ADR 0022, R14): Die Migration läuft im Dienst `migrate` als
  Eigentümer der Tabellen (`automato`), danach richtet er die App-Rolle ein (`automato
  db-roles`); der Orchestrator verbindet sich mit ihr und kann Daten lesen und schreiben, aber
  keine Tabellen anlegen, ändern oder löschen. Einmal einrichten:
  ```bash
  deploy/aufbau.sh db-rollen          # AUTOMATO_DB_USER=automato_app + Zufalls-Passwort in .env, Neustart
  deploy/aufbau.sh db-rollen --neu    # neues Passwort
  ```
  Das Passwort geht nur als SCRAM-Verifier an Postgres. Ob es wirkt, zeigen System →
  Verbindungen (Datenbank: „automato_app ohne DDL“), die System-Seite (DB-Rolle) und
  `deploy/aufbau.sh pruefen` (Abschnitt Datenbank-Rollen). Ohne den Eintrag arbeitet der
  Orchestrator wie bis 0.47 als Eigentümer (Warnung im Betriebszustand). Sicherung und
  Wiederherstellung laufen weiter als `automato`.
- **AuthN/RBAC** über Keycloak (OIDC, `AUTOMATO_AUTH_MODE=oidc`, ADR 0007); im
  `dev`-Modus laufen alle Anfragen als Standard-Admin — nur im Lab verwenden.
- Ausserhalb `AUTOMATO_ENVIRONMENT=dev` verweigert der Orchestrator ohne RBAC den Start,
  solange `AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API` nicht bewusst gesetzt ist (ADR 0005).
- Agent-Tokens werden nur gehasht gespeichert und einmalig angezeigt. Produktiv sprechen
  Agenten nur über das **mTLS-Agent-Gateway** (`deploy/agent-gateway`, Modus `bind`:
  Zertifikat und Token gehören zum selben Agenten); Port 8000 nicht in Agent-Netze freigeben.
- **FortiGate-Token prüfen (R16):** `automato-collector --check --strict` testet, ob die
  FortiGate ein ungültiges Token ablehnt. Opt-in, weil der Versuch als fehlgeschlagene
  Anmeldung zählt (admin-lockout-threshold).
- **Geräte-Zugangsdaten** (ADR 0009) liegen verschlüsselt in der DB; der Schlüssel kommt aus
  `AUTOMATO_CREDENTIALS_KEY` (Passphrase oder Secret-Referenz `openbao:`/`file:`) oder aus der
  beim ersten Start erzeugten Datei `/var/lib/automato/credentials.key` (Volume
  `orchestrator-data`). **Schlüssel bzw. Volume mitsichern** – geht er verloren, sind die
  Tokens unlesbar (Läufe brechen mit „Zugangsdaten nicht lesbar“ ab) und müssen im UI neu
  hinterlegt werden. Ein Agent bekommt nur die Tokens der ihm zugewiesenen Geräte.
- **Nur lesen, ausser NetBox (ADR 0010):** Die Lese-Clients für FortiGate, APIC, Infoblox und
  NetBox lassen technisch nur GET zu (APIC zusätzlich Login/Logout) – ein Schreibversuch
  bricht vor dem Senden ab. Der vCenter-Client (SOAP) lässt nur lesende vSphere-Methoden zu
  (Anmelden, Container-View, Eigenschaften abrufen; ADR 0017). Der Switch-Client (SSH) setzt je
  Befehl einen Exec-Aufruf ohne Shell und Terminal ab und lässt nur die festen `show`-Befehle
  des Lesevertrags zu (kein `show running-config`); er verlangt den beim ersten Lauf
  festgehaltenen Host-Schlüssel, bevor er das Passwort sendet (ADR 0025, R22) – seit 0.100.0 auch
  bei Läufen über den Agenten. Der SNMP-Client (SNMPv3 authPriv, ADR 0033) sendet nur GET/GETBULK
  auf die Positivliste der Standard-MIBs; SET ist nicht eingebunden. Einziger Schreibweg ist der
  NetBox-Sync im Orchestrator mit dem Schreib-Token des NetBox-Quellsystems; er schreibt nur
  die synchronisierten Objekttypen (Tags, Tenants, Standort-Gruppen, Sites, Hersteller,
  Geräterollen/-typen, Geräte, Interfaces, Kabel (seit 0.93.0), VRFs, VLAN-Gruppen, VLANs, Prefixe, Ranges,
  IP-Adressen, Cluster-Typen/-Gruppen, Cluster, VMs, VM-Interfaces) und nur an diese NetBox.
  Das Schreib-Token in NetBox auf genau diese Objekttypen begrenzen (`sync-user.sh`).

## 6. Fehlerbehebung

- **„Interner Fehler – Referenz 1a2b3c4d …“ (HTTP 500, seit 0.94.1)** — der Orchestrator hat
  einen unerwarteten Fehler; die Antwort nennt nur die Referenz, die Ursache steht im
  Protokoll: `docker compose logs orchestrator 2>&1 | grep -A40 1a2b3c4d` (Zeile „Interner
  Fehler 1a2b3c4d bei GET /…“ mit Traceback). Die Ausgabe an den Entwickler geben – sie
  enthält keine Zugangsdaten, kann aber Namen und IPs der Umgebung zeigen.
- **„Anmeldung nicht möglich: Der Orchestrator lehnt das Anmelde-Token ab …“** (UI) —
  der Orchestrator nennt den Grund und loggt ihn (`docker compose logs orchestrator |
  grep OIDC`): `token audience invalid` → im Realm fehlt der Audience-Mapper
  `automato-api` (Client `automato-web` → Client scopes → dedicated → Mapper);
  `token issuer invalid` → `AUTOMATO_OIDC_ISSUER` passt nicht zum Realm (exakt wie
  `iss`, z. B. `https://auth.vitabrevis.ch/realms/automato-dev`); `Signaturschlüssel
  nicht in der JWKS` → `AUTOMATO_OIDC_JWKS_URL` zeigt auf einen anderen Realm. HTTP 503
  `JWKS vom Orchestrator nicht erreichbar` → der Orchestrator-Container erreicht Keycloak
  nicht (DNS/Firewall/WAF); Lösung „Backchannel direkt zur Keycloak-VM“ per
  `docker-compose.override.yml` in `deploy/keycloak/PROD-INSTALL.md` Abschnitt 9c.
  `token not yet valid` (bzw. `token expired` direkt nach der Anmeldung) → die Uhren von
  Keycloak-VM und Automato-VM weichen ab: auf beiden `timedatectl` prüfen („System clock
  synchronized: yes“), sonst NTP aktivieren (`sudo timedatectl set-ntp true`, intern ggf.
  den DC als NTP-Server eintragen). Toleranz: `AUTOMATO_OIDC_CLOCK_SKEW_SECONDS` (Default 60). Nach Änderungen an `.env`: `docker compose … up -d` (neu
  erstellt den Container).

- **FortiGate „antwortet auf <Pfad> nicht mit JSON (HTTP 200, …)“** (bis 0.11.1: „Expecting
  value: line 1 column 1 (char 0)“) — die FortiGate liefert für diese Tabelle eine leere bzw.
  HTML-Antwort. Seit 0.11.4 nennt die Meldung bei HTML auch den Seitentitel (z. B. die
  Sperrseite eines Proxys). Wer gelesen hat, zeigt die Spalte „Ausführung“ der Läufe: „Agent“
  bzw. „zentral“; ein Gerät ohne zugewiesenen Agenten liest der zentrale Worker vom
  Automato-Server aus, dessen Weg (Proxy, Firewall) kann ein anderer sein. Optionale Tabellen (`policy6`, `vip6`, `ldb-monitor`, Systemstatus) überspringt
  der Collector seit 0.11.2. Bei einer Pflicht-Tabelle: im Browser bzw. mit `curl` und dem
  Read-only-Token `https://<fortigate>/api/v2/cmdb/<Pfad>` abfragen. Häufige Ursachen sind
  ein Admin-Profil ohne Leserecht für diesen Bereich, ein VDOM-Zugriff oder ein Proxy
  dazwischen.

- **Lauf mit „VDOM nicht lesbar: <name> (HTTP 403)“** (ab 0.15.0) — das Konto darf diesen
  VDOM nicht lesen. Die übrigen VDOMs sind eingelesen, die Daten des fehlenden fehlen bis zum
  nächsten vollständigen Lauf. Auf der FortiGate dem REST-API-Konto den VDOM (bzw. alle VDOMs)
  mit einem Read-only-Profil geben.

- **„Selbst-Update fehlgeschlagen: Startpruefung fehlgeschlagen … cannot import name
  'selfupdate'“** bzw. **„nach der Installation fehlen … Datei(en)“** — pip hat das venv nicht
  vollständig aktualisiert. Häufigste Ursachen: zwei Agent-Prozesse aktualisieren gleichzeitig
  (die geplante Aufgabe läuft und `run-agent.ps1` wird zusätzlich von Hand gestartet) oder der
  Virenschutz entfernt eine Datei (Windows-Sicherheit → Schutzverlauf).
  - Ab 0.16.1 aktualisiert immer nur ein Prozess (`update.lock`). Fehlende Dateien werden einmal
    nachinstalliert, sonst kehrt der Agent auf die zuletzt guten Pakete (`packages\`) zurück.
    `run-agent.ps1` repariert sich beim Start aus `packages\`.
  - **Ein Agent bis 0.16.0** hat diese Absicherung noch nicht. Bricht sein Update so ab,
    startet er nach dem Ende des laufenden Prozesses nicht mehr. Dann einmalig von Hand: im
    Web-UI unter *Collector-Agenten* das Agent-Paket neu herunterladen, entpacken und in einer
    **administrativen** PowerShell `powershell -ExecutionPolicy Bypass -File .\install.ps1`
    ausführen. Das Skript hält die geplante Aufgabe an, installiert neu und behält die
    vorhandene `agent.env`. Danach mit `run-agent.ps1 -Check` testen.
  - Für Tests von Hand die geplante Aufgabe vorher anhalten oder `run-agent.ps1 -NoUpdate`
    verwenden.

- **Lauf mit „Routing nicht lesbar: … (HTTP 403)“** (ab 0.16.0) — Regeln und Objekte sind
  eingelesen, aber dem Konto fehlt das Leserecht auf diese Routing-Tabelle (FortiGate: Profil
  mit Router/System lesen; APIC: Fabric-Knoten lesen). Unter **Routing** steht der
  Kontext dann als nicht lesbar. Recht ergänzen und neu einlesen. „zu gross“ heisst: mehr als
  50 000 Einträge (z. B. volle Internet-Tabelle per BGP) – diese Tabelle wird nicht übernommen.

- **Lauf „Fehler“ mit „Einspielen fehlgeschlagen: …“** — das Gerät wurde gelesen, aber das
  Ergebnis liess sich nicht in den Store schreiben. Der Ingest wird dann vollständig verworfen
  (vorheriger Stand bleibt), der Agent meldet `failed` mit derselben Meldung; Details inkl.
  Traceback: `docker compose logs orchestrator | grep -A30 "Einspielen von Lauf"`.

- **vCenter „Keine Berechtigung im vCenter: dem Konto fehlt System.View auf
  vim.Folder:group-d1 (oberste Ebene des Inventars)“** (bis 0.11.0 als mehrzeiliges
  `vim.fault.NoPermission`) — die Anmeldung klappt, aber das Konto darf das Inventar nicht
  lesen. Die Rolle *Read-only* enthält `System.View`; sie ist nur am falschen Ort oder ohne
  Weitergabe zugewiesen. Lösung im vSphere Client:
  - oberstes Objekt (der vCenter Server selbst) → *Berechtigungen* → *Hinzufügen*: Konto,
    Rolle *Read-only*, **„An untergeordnete Objekte weitergeben“** anhaken; oder
  - *Verwaltung → Zugriffssteuerung → Globale Berechtigungen*: ebenfalls mit Weitergabe.
    Eine globale Berechtigung ohne Weitergabe gilt nicht für das Inventar.
  - Ist das Konto über eine SSO- bzw. AD-Gruppe berechtigt: Mitgliedschaft prüfen.

  Danach `automato-collector --check` auf dem Agenten: er meldet „… N VMs sichtbar“. Steht
  dort „keine VM sichtbar“, reicht die Berechtigung nicht bis zu den VMs (Weitergabe fehlt
  oder liegt nur auf einem Teil des Inventars).

- **Switch „Host-Schluessel hat sich geaendert (… gespeichert, … vorgewiesen)“** (ab 0.62.0) —
  der Switch weist einen anderen SSH-Host-Schlüssel vor als beim ersten Lauf. Die Verbindung
  bricht vor der Anmeldung ab (kein Passwort an ein fremdes Gerät). Wurde das Gerät getauscht
  bzw. neu aufgesetzt: am Gerät den Schlüssel prüfen (IOS `show crypto key mypubkey rsa`, NX-OS
  `show ssh key`), dann im Quellsystem beim Switch „vergessen“ anhaken und speichern – der
  nächste Lauf lernt den neuen Schlüssel. Sonst: Adresse und Netz prüfen (anderes Gerät unter
  der Adresse?).
- **Switch über SNMPv3: „ohne Antwort (Tabelle fehlt bzw. nicht in der View): …“** (ab 0.100.0) —
  der SNMPv3-Benutzer sieht die Tabelle nicht: View des Benutzers auf dem Gerät erweitern (Gruppe
  nur mit Read-View, ACL auf die Adresse des Agenten) bzw. das Gerät kennt die MIB nicht. Cisco
  IOS/IOS-XE: VLANs je Port nur per SSH vollständig. „Anmeldung abgelehnt“ = Benutzer, Protokolle
  bzw. Passwörter prüfen; „keine Antwort“ = UDP 161 vom Agenten zum Gerät freigeben. Auf der
  Station prüft `.\run-agent.ps1 -Check` je Gerät ein GET auf sysName; „Prüfen“ im Web-UI löst
  beim zentralen Worker nur den Namen auf (UDP ist ohne Anfrage nicht prüfbar). „pysnmp fehlt“ =
  Installation des Agenten unvollständig (Selbst-Update abwarten bzw. Agent-Paket neu
  installieren). Seit 0.100.0 gilt der festgehaltene SSH-Host-Schlüssel auch für Läufe über den
  Agenten: meldet ein Switch danach „Host-Schlüssel hat sich geändert“, Gerät prüfen und den
  Schlüssel im Quellsystem „vergessen“.
- **„Wo hängt …“ findet eine VM nicht bzw. zeigt „zurzeit in keiner MAC-Tabelle“** (ab 0.63.0) —
  die MAC-Tabelle kennt nur Geräte, die in den letzten Minuten gesendet haben (Standard 300 s),
  und nur auf gelesenen Switches. Steht unter „früher“ ein Port, hing das Gerät dort beim
  letzten Lauf, der es gesehen hat. „nur auf Uplinks gesehen“ heisst: das Gerät hängt hinter
  dem ACI bzw. einem Switch ohne Zugang (Gegenstelle genannt) – dort weitersuchen bzw. den
  Switch ins Quellsystem aufnehmen.
- **Switches „Hinweise: show lldp neighbors detail: … LLDP is not enabled“ bzw. ein Switch unter
  „nicht gelesen“** — ein Befehl lieferte einen Fehler (der Rest ist eingelesen) bzw. der
  Switch war nicht erreichbar oder lehnte die Anmeldung ab (Meldung je Adresse). Fehlt einem
  Konto ein Befehl („% Invalid input“, „% Permission denied“), die Parser-View bzw. Rolle um
  den Befehl ergänzen; `automato-collector --check` meldet je Switch Erreichbarkeit, Plattform
  und Fingerabdruck.
- **NSX ALB „HTTP 400 https://…/api/virtualservice“ bzw. Lauf „nicht lesbar: …: virtualservice
  (HTTP 400)“** (bis 0.78.2 ohne Grund) — der Controller lehnt die Abfrage ab. Seit 0.78.3 steht
  seine Begründung dabei („Geraet meldet: …“ bzw. „(HTTP 400: …)“). Lehnt er nur die
  API-Version ab (`X-Avi-Version`), liest Automato ohne sie weiter; die Prüfung nennt dann
  „Avi <Version> (ohne X-Avi-Version)“. Die Version liest Automato einmal kurz aus
  `/api/initial-data` (ohne Anmeldung, sonst danach) – keine Wiederholungen mehr, die den Lauf
  aufhalten. Weitere Gründe: ein Avi-Tenant, den es nicht gibt bzw. den das Konto nicht sieht
  (Feld **Avi-Tenants** im Quellsystem prüfen, Gross-/Kleinschreibung), oder ein Proxy
  dazwischen. Von Hand prüfen: im Browser am Controller angemeldet
  `https://<controller>/api/virtualservice?page_size=1` öffnen.
- **„Läufe laden: HTTP 500“ in „Letzte Läufe“ während ein Lauf eingespielt wird** (bis
  0.78.4) — beim Einspielen eines grossen Laufs ist der Orchestrator ausgelastet. Die Übersicht
  fragte alle 5 s nach, auch wenn die vorige Abfrage noch lief; die Abfragen stauten sich bzw.
  warteten auf den gesperrten Lauf. Seit 0.78.5 fragt die Übersicht erst nach, wenn die vorige
  Antwort da ist, und die Liste überspringt einen gerade gesperrten Lauf (`SKIP LOCKED`).
  Bleibt der Fehler: `docker compose logs orchestrator | grep -B2 -A30 Traceback` – die
  letzte Zeile nennt die Ursache.
- **„Einspielen fehlgeschlagen: DeadlockDetected … relation "rule_index"“ bzw. weiter HTTP 500
  beim Aktualisieren der Läufe** (bis 0.80.0) — beim Einlesen aller Geräte kamen mehrere
  Ergebnisse zugleich an. Jedes schrieb danach den ganzen Zustand des Mandanten neu (Findings,
  Regelindex, Zwillinge paralleler Firewalls, Applikations-Befunde, Kunden); zwei davon
  sperrten sich gegenseitig, und jedes wartende Einspielen hielt eine Verbindung zur
  Datenbank, bis für die Liste der Läufe keine mehr frei war. Seit 0.80.1:
  - Ergebnisse werden **nacheinander** eingespielt. Ein wartendes Ergebnis belegt keine
    Verbindung; der Lauf zeigt „Ergebnis angekommen – wartet aufs Einspielen (n davor)“ bzw.
    „… wird eingespielt“ und wird nicht als hängend beendet. Ein schon abgeschlossener Lauf
    (zweiter Upload des Agenten) wird ohne Warten quittiert. Der Agent wartet je Versuch bis zu
    10 Minuten auf die Quittung und versucht es viermal – bei vielen grossen Läufen zugleich
    kann ein Lauf darum länger „läuft“ zeigen.
  - Alles, was den Zustand des Mandanten neu schreibt (Einspielen, Referenzlauf, „Abgleich“,
    Paare bestätigen, Applikationen und Kunden nachziehen), sperrt den Mandanten in Postgres bis
    zum Ende der Transaktion (`pg_advisory_xact_lock`) – auch über mehrere Prozesse.
  - Bleibt ein Deadlock bzw. Serialisierungsfehler, wird das Einspielen bis zu dreimal
    wiederholt (Warnung im Log „… – neuer Versuch“).
  - Die Liste der Läufe beendet hängende Läufe im Savepoint und nur in einer Abfrage zugleich;
    scheitert das, bleibt die Liste trotzdem lesbar.
  - Mehr Verbindungen und Threads: `AUTOMATO_DB_POOL_SIZE` (10) und `AUTOMATO_DB_MAX_OVERFLOW`
    (20) in der `.env`, `AUTOMATO_API_THREADS` (80). Die Summe muss unter `max_connections` von
    Postgres bleiben (Standard 100, mit Celery-Worker und NetBox).
- **„Alle einlesen“ überspringt ein System, obwohl nichts läuft** (bis 0.11.2) — ein früherer
  Lauf wurde abgeholt, aber nie abgeschlossen (Agent beendet, Upload verloren), und steht
  ausserhalb der letzten acht Läufe noch auf „läuft“ (seit 0.13.0 zeigt die Übersicht alle
  offenen Läufe, die Ansicht „Läufe“ alle). Seit 0.11.3 nennt der Dialog den Lauf
  mit Zeit und Agent, bietet „Abbrechen & neu starten“ und beendet hängende Läufe nach der
  Frist selbst. Scheitert nur der Ergebnis-Upload, meldet der Agent den Lauf jetzt mit
  „Ergebnis-Upload fehlgeschlagen: …“ (z. B. HTTP 413, wenn ein Proxy das Ergebnis zu gross
  findet).

- **„nicht erreichbar … ConnectTimeout“ bzw. „Ergebnis-Upload fehlgeschlagen: … ReadTimeout“**
  (bis 0.17.0) — einzelne Netz-Aussetzer bzw. ein Einspielen, das länger als 30 s dauerte,
  liessen den Lauf scheitern. Seit 0.17.1 gilt:
  - Lesen wird bis zu dreimal wiederholt (5, 15, 30 s).
  - Der Agent wartet bis zu 10 min auf die Quittung und sendet erneut; der Orchestrator nimmt
    einen fertigen Lauf nicht doppelt an.

  Bleibt der Fehler, ist das Ziel wirklich weg. Bei NetBox auf demselben Docker-Host prüfen,
  ob der Container läuft und Port 8080 vom Agenten aus erreichbar ist
  (`Test-NetConnection host.docker.internal -Port 8080`). Beim Orchestrator die Last prüfen
  (`docker stats`) und „Alle einlesen“ nicht doppelt starten.

- **Agent: „Update-Pruefung nicht moeglich: nicht erreichbar …/internal/agent/update:
  ReadTimeout“, „Orchestrator-Aufruf fehlgeschlagen: …/next-run: ReadTimeout“ bzw.
  „RemoteProtocolError: Server disconnected without sending a response“** (Befund 0.78.0) —
  der Orchestrator nimmt die Verbindung an, antwortet aber nicht (kein Netz- oder
  Firewall-Problem: das wäre `ConnectTimeout`/`ConnectError`).
  - **Ursache bis 0.78.0:** während ein Lauf eingespielt wurde, hielt der Upload die Zeile des
    Agenten gesperrt (Lebenszeichen); jede weitere Anfrage desselben Agenten (Update-Prüfung,
    nächster Lauf, Ziele, `-Check`) wartete bis zum Ende des Einspielens und lief nach 30 s in
    den Timeout – und belegte bis dahin eine Verbindung der Datenbank, sodass bei vielen
    Wiederholungen auch die Oberfläche hängen konnte. Seit 0.78.1 wartet keine Anfrage mehr auf
    die Zeile (das Lebenszeichen fällt dann einmal aus); der Upload schreibt sie nicht mehr.
    Behoben im Orchestrator – `deploy/update.sh`, der Agent muss nichts tun.
  - **„Server disconnected without sending a response“ bei einer `http://`-Adresse** (Befund
    0.78.1): meist ist **TLS intern** eingeschaltet (`AUTOMATO_INTERNAL_TLS=on`) – der
    Orchestrator spricht auf Port 8000 nur noch HTTPS und beendet eine Klartext-Anfrage ohne
    Antwort. In `C:\Automato\collector\agent.env` `AUTOMATO_ORCHESTRATOR_URL=https://<VM>:8000`
    setzen und `AUTOMATO_ORCHESTRATOR_CA_BUNDLE=C:\Automato\collector\automato-ca.crt`
    ergänzen; die CA (nur das öffentliche Zertifikat) gibt es im Web-UI unter Collector-Agenten
    → Einrichtung („CA-Zertifikat“) bzw. auf dem Server unter `deploy/tls/pki/client/ca.crt`.
    Seit 0.78.2 nennt der Agent das in der Meldung, das Agent-Paket bringt die CA mit und
    `install.ps1` stellt die Adresse selbst um. Danach `run-agent.ps1 -Check`.
  - Sonst heisst „Server disconnected …“ meist: der Orchestrator-Container startet gerade neu
    (Docker nimmt die Verbindung an und schliesst sie). Prüfen auf dem Server:
    `docker compose ps orchestrator` (Status, Neustarts), `docker compose logs --tail 200
    orchestrator`, `docker stats --no-stream` (Speicher), `curl -sS -m 5
    http://127.0.0.1:8000/health` (mit TLS intern `https://` und `--cacert`).
  - Wartende Anfragen in Postgres: `docker compose exec postgres psql -U automato -d automato
    -c "select pid, state, wait_event_type, now() - xact_start as dauer, left(query, 70) from
    pg_stat_activity where datname = 'automato' order by xact_start"` – viele Zeilen
    `UPDATE agent …` mit `Lock` bestätigen die Ursache; sie lösen sich, sobald das Einspielen
    fertig ist (bzw. nach dem Update auf 0.78.1).

- **NetBox-Sync steht seit Langem auf „wird übernommen“** (bis 0.11.3) — die Übernahme läuft
  im Orchestrator-Prozess; ein Neustart (z. B. `deploy/update.sh`) beendete sie, der Plan blieb
  stehen und sperrte jeden weiteren Sync. Seit 0.11.4 werden solche Pläne beim Start beendet,
  ohne Lebenszeichen nach 30 min ebenfalls (`AUTOMATO_NETBOX_SYNC_TIMEOUT_MINUTES`), und
  **Abbrechen** beendet einen laufenden Plan von Hand. Die Übernahme hört dann vor der
  nächsten Objektart auf; bereits Geschriebenes bleibt. Eine neue Vorschau setzt dort an.

- **Infoblox/NetBox „Teil-Upload unvollstaendig (N von M)“** — bis 0.11.1 konnte der letzte
  Teil-Upload noch nicht gespeichert sein, wenn das Ergebnis eintraf. Behoben in 0.11.2
  (Orchestrator aktualisieren; der Agent muss nichts tun). Tritt die Meldung danach noch auf,
  ist ein Teil-Upload tatsächlich abgewiesen worden: Agent-Log prüfen. **„(0 von N)“, danach
  „keine Adresse fuer die Referenzquelle“** (bis 0.113.1): zwei Läufe derselben Quelle bzw. ein
  Worker-Auftrag ohne abrufbares Ziel verwarfen die Teil-Uploads des anderen – behoben in
  0.113.2 (Orchestrator und zentralen Worker aktualisieren, dann neu einlesen).

- **`deploy/update.sh`: „service "netbox" has neither an image nor a build context specified“**
  (Befund 0.42.0) — im Automato-Ordner liegt eine `docker-compose.override.yml` mit einem
  fremden Dienst (hier die NetBox-Override-Datei). Sie gehört nach
  `source-of-truth/netbox/netbox-docker/`; eine eigene Override-Datei von Automato (z. B.
  `extra_hosts` für Keycloak) danach wiederherstellen. Seit 0.42.1 melden `deploy/update.sh` und
  `deploy/aufbau.sh pruefen` das vorab mit der Datei.
- **NetBox „nicht erreichbar … ConnectTimeout“ beim Einlesen und im NetBox-Sync** (Dev-Befund
  0.33.0) — der Orchestrator- bzw. Collector-Container bekommt keine TCP-Verbindung zu
  `host.docker.internal:8080`. Das ist kein Rechte- oder Token-Problem. Zuerst
  `sudo deploy/aufbau.sh pruefen` – es prüft beide Ursachen der Befunde 0.39.0/0.40.0:
  - **NetBox ist aus** (z. B. nach einem Neustart der VM, Container `Exited (0)`, auch
    `curl -I http://localhost:8080/login/` auf dem Host bleibt stumm):
    `deploy/aufbau.sh netbox-start` startet sie, setzt die Neustart-Regel und wartet, bis sie
    antwortet. Seit 0.41.0 haben Automato (`docker-compose.yml`) und das NetBox-Setup
    `restart: unless-stopped`; Docker selbst muss beim Booten starten
    (`systemctl enable docker`).
  - **ufw** auf dem Docker-Host verwirft den Weg vom Container-Netz zum Host –
    im Kernel-Log `[UFW BLOCK] IN=br-… SRC=172.20.0.5 DST=172.17.0.1 … DPT=8080`. Abhilfe:
    `sudo deploy/aufbau.sh firewall --anwenden` (setzt nur die schmale Regel) bzw. die Regel
    aus System → Verbindungen. **Nicht** `172.16.0.0/12` und nicht `8080/tcp` für alle
    freigeben – das LAN kann im selben Bereich liegen.
  1. **Quellsysteme → Prüfen** (ab 0.34.0) bzw. **System → Verbindungen** (0.40.0) zeigt in
     Sekunden, wo es hängt:
     - Name nicht auflösbar: `extra_hosts` fehlt bzw. der Container wurde nicht neu
       erstellt.
     - Keine Antwort: NetBox ist aus, oder der Weg ist gesperrt.
     - Abgelehnt: Auf dem Port läuft nichts.
     - HTTP 401/403: Das Token stimmt nicht.
  2. Läuft NetBox? Auf dem Docker-Host `docker ps | grep netbox` und
     `curl -sI http://localhost:8080/login/` (Port wie `NETBOX_PORT`).
  3. Läuft NetBox, und trotzdem keine Antwort: Eine Firewall auf dem Host (ufw/firewalld)
     bzw. die Trennung der Docker-Netze sperrt den Weg vom Container zum Host. Die robuste
     Lösung: Automato direkt an das Docker-Netz von NetBox anschliessen und die Adresse
     `http://netbox:8080` verwenden – in `docker-compose.override.yml` (gitignored):

     ```yaml
     services:
       orchestrator:
         networks: [default, netbox]
       collector:
         networks: [default, netbox]
     networks:
       netbox:
         external: true
         name: netbox-docker_default   # docker network ls | grep netbox
     ```

     Danach `docker compose up -d --force-recreate orchestrator collector`, am Quellsystem
     die Adresse auf `http://netbox:8080` ändern und erneut **Prüfen**.

- **NetBox-Sync „Vorschau/Übernahme fehlgeschlagen“** — die Meldung von NetBox steht am
  Plan (z. B. fehlendes Schreibrecht → HTTP 403, Validierungsfehler → HTTP 400 mit Feld).
  „NetBox nicht erreichbar (host)“: der Orchestrator (nicht der Agent) schreibt – NetBox muss
  vom Orchestrator-Container aus erreichbar sein. „Seit der Vorschau hat sich … geändert“:
  nichts wurde geschrieben, neue Vorschau erstellen. Nach einem Abbruch liest Automato
  NetBox neu ein; die nächste Vorschau setzt am Teilstand an.
- **Referenzquelle „unvollständig“** — der Lauf hat weniger Einträge hochgeladen als gemeldet
  (Abbruch/Netz); der vorherige Stand bleibt, Lauf wiederholen.
- NetBox startet nicht / Token-Fehler: `source-of-truth/netbox/README.md` (Abschnitt Fehlerbehebung).
- Stack-/Lab-Probleme: `lab/README.md`.
- Logs: `docker compose logs -f orchestrator collector`.
