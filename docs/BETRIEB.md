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
Passwörter und Schlüssel (HA, FGSP, BGP-Peers) werden nie gelesen.

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
  - Sofort geht es mit **Abbrechen** (Letzte Läufe) bzw. **Abbrechen & neu starten** im
    Dialog „Alle einlesen“ (Operator). Liefert der Agent danach doch noch ab, wird das
    Ergebnis eingespielt.
  - „Letzte Läufe“ zeigt immer **alle offenen Läufe** (auch ältere), dazu die letzten acht und
    den Link **Alle Läufe (N)** (0.13.0).
- **Läufe** (0.13.0, seit 0.14.0 unter Administration): alle Collector-Läufe des Mandanten,
  seitenweise mit Gesamtzahl, neueste
  zuerst. Filter Status (**offen** = wartet oder läuft, wartet, läuft, fehlgeschlagen,
  erfolgreich), Quellsystem, Plattform, Auslöser (von Hand, Zeitplan). Je Lauf Ausführung
  (Agent/zentral, Zeitplan), Regeln, Status mit Meldung, angelegt/begonnen, beendet mit Dauer;
  offene Läufe **Abbrechen** (Operator). Offene Läufe auf der Seite aktualisieren sich alle 10 s.
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
  - Grün: gleiche Objektnamen, Dienste und Interfaces.
  - Gelb „ähnlich“: Abweichungen im Tooltip.
  - Klick öffnet das Gegenstück; beim Überfahren wird es hervorgehoben. Beide bleiben eigene
    Regeln.
  - **Bestätigen bzw. ablehnen** (Operator, 0.12.0): im Detail „Als Paar bestätigen“ (für
    „ähnlich“) bzw. „Kein Paar“, mit „zurücknehmen“. Die Entscheidung gilt sofort und bleibt über
    neue Läufe erhalten (Policy-ID). Filter „Paar“: ähnlich (offen) zum Abarbeiten, bestätigt,
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
  über Gruppen), mit „In Regeln anzeigen“. Dazu das Netzinventar aus FortiGate/APIC:
  **Subnetze** (mit Abgleich gegen führendes IPAM und Anreicherung, VLAN-Abweichung
  markiert), **VLANs** (inkl. EPGs), **Ports** (Modus, untagged/tagged VLANs, LAG/Parent,
  IPs, Zone/Policy-Gruppe, LLDP/CDP-Nachbar, EPGs), **Geräte**, **VRFs**. Filter Quelle,
  Suche (auch Mitglieder und DNS-Namen der Werte aus dem IPAM), „nur Abweichungen“.
  - Die **Regel-Zahl** je Objekt zählt die Regeln direkt und über (verschachtelte) Gruppen.
    Klick → genau diese Regeln.
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
- **Routing & Wege** (0.16.0, ADR 0019): oben die gelesenen Kontexte mit Anzahl Routen und dem,
  was nicht lesbar war (dort ist die Aussage „unbekannt“). Bezeichnungen (0.17.0):
  - Eine FortiGate nur mit `root` heisst wie die **Firewall**, sonst „fw-01 · VDOM kunde-a“.
  - ACI: „leaf-101 · VRF Tenant:VRF“, im Weg und im Kontext-Filter die VRF über alle
    Border-Leafs („apic-01 · VRF Tenant:VRF“).

  Reiter:
  - **Weg für IP** (0.17.0): **Quelle (IP)** und/oder **Start** (Firewall, VDOM, ACI-VRF), dazu
    das **Ziel**.
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
    - Ohne Quelle und Start: je Kontext die wirksame Route wie bisher. Geräte, deren Routing
      nicht gelesen ist (Switches, Router der Provider), beenden den Weg.
    - **Redundante Wege (0.19.0):**
      - „Wege im Überblick“ zeigt alle Wege mit Art: aktiv · ECMP, redundant über einen
        Verbund paralleler Firewalls, oder Backup ab einem Knoten mit Grund (BGP-Backup mit
        AS-Pfad bzw. statische Backup-Route mit Distanz).
      - Je Weg stehen die Knoten nacheinander, die Next-Hops, die Anzahl Hops und das Ende.
      - Die Auswahl „Backup“ steuert, wie viel länger ein Backup-Weg sein darf: gleich lang,
        bis +1 Hop (Standard), bis +2 Hops, ausblenden.
      - Die Spalte „Länge“ (0.20.0) sagt je Weg „gleich lang“ bzw. „+1 Hop“ gegenüber dem
        kürzesten Weg mit Ziel.
    - **Regeln auf dem Weg (0.22.0):** Je Firewall bzw. FortiGate-VDOM steht in der Spalte
      „Regeln“, welche Policy den Verkehr erlaubt oder sperrt.
      - Die erste sicher passende Policy entscheidet, wie auf der Firewall. Ohne passende ist
        der Verkehr implizit gesperrt.
      - Im Feld „Dienst“ (z. B. `tcp/443`) lässt sich der Dienst angeben. Ohne Dienst ist nur
        eine Policy mit `ALL` sicher.
      - „möglich (…)“ nennt, was sich nicht prüfen lässt: Eingang/Quelle bei gewähltem Start,
        FQDN/ISDB, Benutzer/Gruppen, Zeitplan, Dienst. Kann eine solche Policy vor der
        entscheidenden greifen, ist der Hop „abhängig“.
      - Je Weg steht das Gesamturteil („gesperrt an fw-x“). Ein Klick auf „Policy n“ öffnet
        die Regel.
      - ACI-Contracts sind dabei noch nicht berücksichtigt.
    - **NAT auf dem Weg (0.23.0):**
      - Ist das Ziel die externe IP einer VIP, steht am Hop „DNAT VIP_x: extern → intern“.
        Der Weg geht zur internen IP weiter, der nächste Hop zeigt „Ziel …“.
      - Bei Port-Weiterleitung gilt für die Policy der interne Port (Dienst mit dem externen
        Port eingeben, z. B. `tcp/443`).
      - SNAT der erlaubenden Policy steht als „SNAT → IP (Pool …)“. Diese Quelle gilt für die
        Firewalls danach.
  - **Routen:** wirksam (RIB), BGP-Pfade (bester bzw. Backup) und statische Routen (aktiv bzw.
    inaktiv); Suche, „enthält IP“ (längstes Präfix zuerst), Filter Tabelle und Kontext.
  - **BGP-Nachbarn** (mit Name aus dem IPAM und, falls gelesen, dem Knoten dahinter), **HA &
    Session-Sync** (Modus, Gruppe, Mitglieder mit Rolle, FGSP-Peers), **Policy-Routen &
    SD-WAN** (Bedingung, Weg, Mitglieder nach Vorrang), **L3Outs (ACI)** (VRF, Border-Leafs mit
    Namen, Router-ID und statischen Routen, Interfaces/SVIs mit VLAN und Adressen, BGP-Peers
    mit Namen).
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
    - Danach unter „Weg für IP“ einfach neu suchen. Das Ende nennt bei mehreren Routern
      „Router n von N“.
- **Applikationen** (ADR 0012): *Applikationen* mit Kürzel, externem und internem Namen,
  Verwendungszweck (Dev/Test/Prod), Verantwortlichen, Kritikalität, Standort und Mitgliedern
  (Application Profiles, EPGs, Server); **Vorschläge** nach der Projektnummer aus
  `AP_<nr>`/`H_…-<nr>-…` mit einem Klick übernehmen (Operator).
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
    Quellsystem, Quelle, Ziel, Dienste, NAT) mit Richtung, Bezug und Kategorie. Unter
    Quelle bzw. Ziel steht je Objekt der Regel, über das sie Systeme der Applikation erfasst
    (Host, Netz, any, EPG), welche Systeme darin liegen – eine Regel über ein Subnetz oder any
    erscheint einmal, nicht je VM (0.14.1). Filter System/VM, EPG, **Richtung aus Sicht des Systems** (eingehend = Ziel
    bzw. Provider, ausgehend = Quelle bzw. Consumer), Art, Bezug, Suche. Klick → Detail mit
    **„Warum diese Regel für die Applikation gilt“** (je System Rolle, Richtung, Bezug,
    Kategorie, Mitglied/AP), Links in Regeln & Contracts und Findings.
  - **Findings** der Applikation und die **Doku nach Kategorien** (10 Tabellen) mit
    **Export (Markdown)** und **Drucken / PDF**; **Mitglieder** und **Bearbeiten** oben.
  Dazu
  *Doku je System* (10 Kategorien, Standort verorten) und *Publizierte Dienste* (VIPs mit
  Regeln). Die Doku erfasst **alle** Regeln eines Systems. Die Spalte **„Bezug“** sagt, wie
  die Regel greift:
  - direkt,
  - über ein Netz, einen Bereich oder eine Gruppe (Objektname),
  - any,
  - vzAny der VRF, ESG, oder die EPG des Servers.

  Für EPGs zählen ihre Subnetze und Endpunkte. Der Kopf nennt die **VM** des Servers bzw.
  die VMs an den Portgruppen der EPG; ein Server bringt auch die Contracts der EPG seiner
  Portgruppe mit (ohne gelernten Endpunkt).
  - **Nur, wo der Verkehr durchkommt (0.24.0):** Automato prüft jede Firewall-Regel gegen das
    Routing ihrer Firewall.
    - Ziel: Leitet die Firewall den Server über den Ausgang der Regel (Interface bzw. Zone)?
    - Quelle: Kommt Verkehr vom Server über den Eingang der Regel an?
    - Wenn nicht, ist die Regel für dieses System **routingtechnisch nicht möglich** – z. B.
      „lan → internet, any“ für einen Server in der DMZ.
    - Solche Treffer fehlen in den Kategorien, im Export und in den Kennzahlen.
      - *Doku je System:* Sie stehen oben unter „Routingtechnisch nicht möglich“ mit
        Begründung.
      - *Applikations-Doku:* „N routingtechnisch unmögliche einblenden“ zeigt sie, mit
        „nicht möglich“ in der Spalte Bezug und dem Grund in der Erklärung.
    - Fehlt das Routing, die Route oder ein Interface, bzw. kann eine Policy-Route oder
      SD-WAN anders leiten, bleibt die Regel sichtbar („unbekannt“). Regeln mit `any` als
      Interface sind immer möglich.
  - **Gegenstelle any (0.25.0):** Statt „any“ steht, was hinter dem Interface bzw. der Zone
    der Regel liegt.
    - Zuerst eine erfasste Anbindung („all → Internet Provider (internet)“).
    - Sonst die Default-Route („extern“) bzw. die Netze dahinter („Netze hinter lan: …“).
    - Mit einer Internet-Anbindung landen solche Regeln in „Zugriff aus dem Internet“ bzw.
      „Internet Zugriff“. Anbindungen erfassen unter Routing & Wege → Anbindungen.
  - **ACI-Contracts über externe EPGs (0.28.0):** Ein System ausserhalb des Fabrics (z. B.
    hinter der Firewall) bekommt die Contracts der externen EPG, der das ACI seine IP
    zuordnet. Bezug: „externe EPG L3O_fw|EN_srv (10.20.0.0/24)“.
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
- **Abgleich:** Referenzquellen des Mandanten (führend/Anreicherung, eingelesen?), Kacheln je
  Befund-Typ (Klick → Findings vorgefiltert): nicht im IPAM, Name ≠ DNS-Name, Subnetz fehlt
  im IPAM, VLAN weicht ab, Namenskonvention, Präfix/Typ, doppelte Werte, unbenutzte
  Objekte. Darunter **Vergleich DDI ↔ NetBox** (fehlt in NetBox / abweichend / nur in
  NetBox) mit Suche. **Jetzt abgleichen** (Operator) rechnet neu, ohne etwas zu lesen oder
  zu schreiben.
- **Netzwerkdesign:** geplant (Platzhalter).

**Administration**
- **Benutzer & Rollen:** Rollen viewer/operator/admin, eigenes Konto, Links in die
  Keycloak-Benutzerverwaltung (`AUTOMATO_OIDC_ADMIN_CONSOLE_URL`, sonst aus dem Issuer
  abgeleitet). Benutzer selbst werden in Keycloak gepflegt (`tenant_ids` = Mandanten-ID).
- **Mandanten & Standorte (Admin schreibt):** **+ Mandant**, Kürzel, Namensprofil,
  Lese-Audit, **Standorte** (ADR 0015): *virtuell* (dreistellige Nummer, z. B. `000` =
  Stretched DC) und *physisch* (Ort-Code, z. B. `chzrh1`); **+ Standort** mit Art, Code, Name
  und – bei virtuellen – den physischen Standorten, an denen er läuft (**Zuordnen** ändert das
  später). Die Standort-Auswahl in Quellsystem, Doku je System und Applikation ist nach Art
  gruppiert. Den Standort aus dem Namen (Netz `N_<tenant>-000-…`, Cluster
  `fwc11-000-<tenant>`, Host `evh01-chzrh1-<tenant>`) zeigen Objekte, Inventar (Geräte,
  Subnetze) und der Quellsystem-Dialog als Vorschlag („?“ = Code nicht erfasst). Weiter: die **Quellsysteme des
  Mandanten** (**+ Quellsystem** / **Bearbeiten**: FortiGate/Cisco ACI/Infoblox DDI/NetBox/VMware,
  Adresse, **Standort** des Geräts (für den NetBox-Sync), **Verwaltet von** = Collector-Agent
  oder zentraler Worker, read-only Zugangsdaten – FortiGate: **API-Token**, Cisco ACI:
  **APIC-Benutzer/Passwort** + **ACI-Tenants** (leer = alle ausser infra/mgmt), Infoblox:
  **WAPI-Benutzer/Passwort**, VMware: **vCenter-Benutzer/Passwort** (Konto mit der Rolle
  *Read-only* auf dem vCenter-Objekt bzw. als globale Berechtigung, jeweils mit „An
  untergeordnete Objekte weitergeben“), NetBox: **Lese-Token** + optional **Schreib-Token** (nur
  NetBox-Sync, geht nie an einen Agenten) – nur schreibbar, verschlüsselt gespeichert, Anzeige
  nur „hinterlegt"; bei Infoblox/NetBox die **Rolle** *IPAM führend* oder *Anreicherung*
  (genau ein führendes IPAM je Mandant); TLS-Prüfung, aktiv; **Zeitplan** (stündlich,
  täglich 02:00, werktags 06:00, wöchentlich oder eigener Cron-Ausdruck; Zeitzone
  `AUTOMATO_SCHEDULER_TIMEZONE`, Standard Europe/Zurich – die Tabelle zeigt den nächsten
  Termin); Name je Mandant eindeutig; ADR 0009/0010). **Löschen:** Quellsystem (✕, Rückfrage nennt die mit
  gelöschten Regeln/NAT-Einträge) und **Mandant löschen** (nur nach Eintippen des Namens;
  löscht alle Daten des Mandanten inkl. seiner Collector-Agenten; Audit-Log bleibt).
  Altbestände ohne Mandant erscheinen unter „Nicht zugeordnete Quellsysteme“ zum Zuordnen.
- **Namensprofile:** Standardprofil **„default"** (per Migration angelegt, gilt für alle
  Mandanten ohne eigenes Profil, frei pflegbar), **+ Neues Profil** (Vorlage: Kopie eines
  bestehenden Profils, z. B. „default", oder leer), **+ Neue Namenskonvention** (optional die
  Einträge einer bestehenden Konvention – auch aus einem anderen Profil – kopieren),
  Einträge anlegen/bearbeiten/löschen.
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
  nach dem Update erneut ausführen (idempotent). **Vorschau
  erstellen** liest NetBox nur und zeigt je Art: anlegen / ändern / unverändert / Konflikt
  (von Hand gepflegtes Objekt weicht ab – bleibt unverändert) / löschen / veraltet, mit
  Aktionsliste. **Übernehmen** (Bestätigung) berechnet neu, schreibt nur bei unveränderter
  Lage, auditiert und liest NetBox danach neu ein. Geändert/gelöscht werden nur Objekte mit
  Tag `automato`. Geräte ohne Standort werden übersprungen (Hinweis) – Standort am
  Quellsystem setzen (bei genau einem Standort des Mandanten wird dieser genutzt).

## 4. API

Lesen = `viewer`, Läufe/Triage = `operator`, Verwaltung = `admin` (mandantsgescoped).

| Endpunkt | Zweck |
|---|---|
| `GET /health`, `GET /auth/config` | Status (API/DB), öffentliche Login-Konfiguration |
| `GET /rules` (`?q=`,`?object=`,`?nat=`,`?tenant_id=`,`?domain=firewall\|aci_contract`,`?source_system_id=`,`?src_zone=`,`?dst_zone=`,`?action=`,`?limit=`,`?offset=`), `GET /rules/{id}` | Regeln und ACI-Contracts seitenweise (Standard 500, Gesamtzahl im Header `X-Total-Count`; Suche über alles, was zur Regel gehört; `object` = Regeln mit diesem Objekt auch über Gruppen, `nat` = mit diesem VIP/SNAT-Pool; je Regel `nat`) / Detail (Objektnamen, `refs` mit Referenzdaten, `aci`, `comment`, `nat_entries`) |
| `GET /rules/summary` (`?tenant_id=`) | Kennzahlen aller Regeln für die Übersicht (je Art, Endpunkte, nicht aufgelöst, Quell-/Ziel-Zonen, Aktionen) |
| `GET /objects?tenant_id=` (`category`, `source_system_id`, `q`, `issues_only`), `GET /objects/{id}` | Firewall-Objekte mit IPAM-Abgleich und `attrs` / Detail mit Mitgliedern und Regeln (`rules`, `rule_count`) |
| `GET /inventory/{devices\|interfaces\|vlans\|subnets\|vrfs}?tenant_id=` | Netzinventar (Subnetze mit IPAM-Abgleich) |
| `GET /vms?tenant_id=` (`q`, `source_system_id`, `epg`, `limit`, `offset`), `GET /portgroups?tenant_id=` (`q`), `GET /portgroups/{id}` | VMware-Inventar (ADR 0017): VMs mit NICs, IPs (DNS, im IPAM?) und EPG der Portgruppe, Seite mit `total`; Portgruppen mit EPG und Art der Zuordnung; Detail mit EPG (BD/VRF), Subnetzen je Quelle und VMs |
| `GET /reconcile/summary`, `GET /reconcile/ipam`, `POST /reconcile` | Abgleich: Befunde je Typ, DDI ↔ NetBox, neu berechnen (operator) |
| `GET /findings` (`?status=`,`?type=`,`?q=`,`?limit=`,`?offset=`, `X-Total-Count`), `GET /findings/summary`, `PATCH /findings/{id}` | Datenqualität seitenweise (je Befund `message` = was nicht stimmt, `hint` = was tun), Zähler je Status/Typ, Triage |
| `GET /nat` (`?nat_type=`,`?q=`,`?issues_only=`,`?limit=`,`?offset=`, `X-Total-Count`), `GET /nat/{id}`, `GET /published-services[/{name}]`, `GET /appdoc/systems[/{name}]` | Doku: NAT mit DNS-Namen, Server, Regelzahl und Prüfung (`issues`) / Detail mit IPAM-Sicht, Regeln, VMs und `object_id`, publizierte Dienste (VIPs), Doku je System |
| `GET/POST /applications`, `GET/PATCH/DELETE /applications/{id}`, `POST …/{id}/members`, `DELETE …/{id}/members/{mid}`, `GET …/{id}/doc` | Applikationen (ADR 0012; Schreiben = operator): Stammdaten (inkl. `external_name`, `internal_name`, `purpose` dev/test/prod), Mitglieder AP/EPG/Server, Doku mit `summary`, `systems` (VMs), `epgs`, `hits` (Regel × System: Richtung, Bezug, `scope`), `rules`, `findings`, `tables` |
| `GET /applications/candidates` (`?kind=`,`?q=`), `GET /applications/suggestions`, `POST /applications/suggestions/{nr}/accept` | Mögliche Mitglieder; Vorschläge nach Projektnummer übernehmen (operator) |
| `GET/POST /tenants`, `PATCH /tenants/{id}`, `DELETE /tenants/{id}?confirm=<Name>` | Mandanten |
| `GET/POST /tenants/{id}/sites`, `PATCH/DELETE /tenants/{id}/sites/{sid}` | Standorte (`kind` virtual/physical, `physical_site_ids` bzw. `virtual_site_ids`) |
| `GET /systems?tenant_id=`, `POST /systems`, `PATCH/DELETE /systems/{id}` | Quellsysteme (je Mandant; `agent_id`, `site_id`, `read_scope`, `ref_role`, `schedule` → `next_run_at`; `token` bzw. `username`/`password`, NetBox `write_token` nur schreibbar → `has_credentials`/`has_write_token`; Referenzquellen mit `reference_count`/`reference_at`) |
| `GET /jobs` (`tenant_id`, `status` inkl. `open`, `source_system_id`, `platform`, `trigger`, `limit`, `offset`; Gesamtzahl im Header `X-Total-Count`), `GET /jobs/{id}`, `POST /jobs/collect`, `POST /jobs/collect-all`, `POST /jobs/{id}/cancel` | Läufe (Gerät mit Agent → immer `external`, `started_at` = abgeholt); `collect-all` = alle aktiven Quellsysteme des Mandanten (operator, Antwort je System `run_id` bzw. `skipped` mit `blocking_run_id`); `cancel` beendet einen offenen Lauf (operator) |
| `GET/POST /agents`, `PATCH /agents/{id}`, `GET /agents/package` | Collector-Agenten (inkl. zugewiesener Geräte), Agent-Paket |
| `GET /internal/agent/next-run`, `…/targets`, `POST …/heartbeat` | Agent-Kanal (`X-Agent-Token`): Lauf + Zugangsdaten nur für zugewiesene Geräte |
| `POST /internal/collector-runs/{id}/result`, `GET …/{id}/target` | Ergebnis (Agent-Token des abholenden Agenten oder internes Token; Antwort = Quittung `status`/`message`); Ziel für den zentralen Worker |
| `POST /internal/collector-runs/{id}/references` | Teil-Upload einer Referenzquelle (Infoblox/NetBox, je bis 2000 Einträge; das Ergebnis meldet `reference_count`, sonst wird der Lauf verworfen) |
| `GET /netbox-sync/targets`, `GET/POST /netbox-sync/plans`, `GET …/plans/{id}[/items]`, `POST …/plans/{id}/apply` | NetBox-Sync (admin): Vorschau, Aktionen, Übernahme |
| `GET/POST /naming-profiles`, `…/{id}/dictionary`, `DELETE …/dictionary/{id}` | Namensprofile |
| `GET /system/status` | Betriebsstand (admin, siehe System-Seite) |
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
  verschlüsselte Sicherung → Code holen → Images bauen → starten → `/health`. Migrationen
  laufen beim Start (`alembic upgrade head`). Schlägt etwas fehl: `deploy/update.sh --rollback`
  setzt Code **und** Datenbank auf den Stand vor dem Update zurück (ADR 0013).
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
  (Anmelden, Container-View, Eigenschaften abrufen; ADR 0017). Einziger Schreibweg ist der
  NetBox-Sync im Orchestrator mit dem Schreib-Token des NetBox-Quellsystems; er schreibt nur
  die synchronisierten Objekttypen (Tags, Tenants, Standort-Gruppen, Sites, Hersteller,
  Geräterollen/-typen, Geräte, Interfaces, VRFs, VLAN-Gruppen, VLANs, Prefixe, Ranges,
  IP-Adressen, Cluster-Typen/-Gruppen, Cluster, VMs, VM-Interfaces) und nur an diese NetBox.
  Das Schreib-Token in NetBox auf genau diese Objekttypen begrenzen (`sync-user.sh`).

## 6. Fehlerbehebung

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
  mit Router/System lesen; APIC: Fabric-Knoten lesen). Unter **Routing & Wege** steht der
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

- **NetBox-Sync steht seit Langem auf „wird übernommen“** (bis 0.11.3) — die Übernahme läuft
  im Orchestrator-Prozess; ein Neustart (z. B. `deploy/update.sh`) beendete sie, der Plan blieb
  stehen und sperrte jeden weiteren Sync. Seit 0.11.4 werden solche Pläne beim Start beendet,
  ohne Lebenszeichen nach 30 min ebenfalls (`AUTOMATO_NETBOX_SYNC_TIMEOUT_MINUTES`), und
  **Abbrechen** beendet einen laufenden Plan von Hand. Die Übernahme hört dann vor der
  nächsten Objektart auf; bereits Geschriebenes bleibt. Eine neue Vorschau setzt dort an.

- **Infoblox/NetBox „Teil-Upload unvollstaendig (N von M)“** — bis 0.11.1 konnte der letzte
  Teil-Upload noch nicht gespeichert sein, wenn das Ergebnis eintraf. Behoben in 0.11.2
  (Orchestrator aktualisieren; der Agent muss nichts tun). Tritt die Meldung danach noch auf,
  ist ein Teil-Upload tatsächlich abgewiesen worden: Agent-Log prüfen.

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
