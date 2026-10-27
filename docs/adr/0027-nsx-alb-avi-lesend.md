# ADR 0027 — VMware NSX Advanced Load Balancer (Avi) als lesende Quelle

## Status

Angenommen (2026-10-27), umgesetzt in 0.77.0. Frage des Anwenders vom 2026-10-26 („kann ich
den VMware NSX Load Balancer – ehemals Avi – für die Doku integrieren?“), Entscheid: ja, nach
den Korrekturen 0.74.1–0.76.1. Ergänzt ADR 0010 (nur lesen), ADR 0019 (Weg Hop für Hop) und
ADR 0012/0021 (Applikations-Doku, Verbindungen).

## Kontext

Applikationen werden nicht nur über VIPs bzw. Virtual Server der FortiGate publiziert, sondern
auch über den **NSX Advanced Load Balancer** (Avi): ein Virtual Service mit VIP und Ports
verteilt auf die Server eines Pools. Ohne diese Daten endet die Doku an der VIP: die Regel zur
VIP zeigt kein Ziel, die Weg-Suche bleibt im Netz der VIP stehen, die Applikations-Doku kennt
die Server dahinter nicht.

Der Controller hat eine vollständige REST-API (`/api/<objekt>`, JSON, Seiten mit `next`). Die
Sicht über alle Tenants (`X-Avi-Tenant: *`) ist nur Superusern erlaubt – für ein Nur-Lese-Konto
also nicht nutzbar.

Verbindliche Vorgabe: nur nach NetBox schreiben, sonst strikt lesen; Testsysteme sind
produktiv, nur Read-only-Konten.

## Entscheidung

1. **Neue Plattform `avi`** („NSX ALB (Avi)“): Quellsystem mit Adresse des Controllers (bzw.
   der Cluster-IP), Benutzer und Passwort wie beim APIC, zentral gespeichert und dem Agent bzw.
   dem zentralen Collector übergeben (ADR 0009). TLS-Prüfung wie bei den anderen Quellen.
2. **Strikt lesend** (`collectors/avi/client.py`): nur `GET` auf eine **Positivliste** von
   Objekten; einzige Ausnahme `POST /login` und `POST /logout` für die Sitzung (wie `aaaLogin`
   beim APIC) – durchgesetzt mit `sot_gateway.readonly.enforce_read_only(allow_post=…)`, jeder
   andere Schreibversuch ist ein `ReadOnlyViolation`. Kein Basic-Auth (beim Controller meist
   aus), Sitzungs-Cookie, Abmelden am Ende. Header `X-Avi-Version` mit der Version des
   Controllers (`/api/initial-data`, ohne Anmeldung lesbar), Rückfall auf eine feste Version.
3. **Konto:** eine eigene Rolle mit **nur Leserechten** (`READ_ACCESS`) auf Virtual Service,
   VS VIP, Pool, Pool Group, VRF Context, Tenant und Application Profile, Zugriff auf die
   betroffenen Tenants. Zertifikate, Schlüssel, Benutzer und Systemeinstellungen liest Automato
   nicht (nicht in der Positivliste).
4. **Tenants:** `read_scope` = Liste der Avi-Tenants (leer = alle, die das Konto sieht,
   `GET /api/tenant`); je Tenant mit `X-Avi-Tenant: <name>`. Fehlende Rechte auf einen Tenant
   bzw. ein Objekt = Teilfehler in der Meldung, der Rest wird gelesen.
5. **Gelesen** (je Tenant, `include_name=true`, Seiten zu 200, höchstens 20 000 Objekte je
   Art): `virtualservice`, `vsvip`, `pool`, `poolgroup`, `vrfcontext`, `applicationprofile`
   und der Betriebszustand aus `virtualservice-inventory` (`runtime.oper_status.state`).
   Ausgewertet wird nur, was die Doku braucht (Positivliste der Felder im Collector).
6. **Abbildung als publizierter Dienst** (`NatRule`, wie der Virtual Server der FortiGate):
   je Virtual Service und VIP-Adresse ein Eintrag mit externer IP (IPv4/IPv6, auch Floating IP
   als eigene Adresse), Ports bzw. Port-Bereichen (`services`, SSL ja/nein), Methode
   (`lb_algorithm`), Zielen = Server der Pools (IP, Port bzw. `default_server_port`, aktiv,
   Gewicht; Pool Group: Pools nach Priorität), FQDN aus `vsvip.dns_info`, Tenant/Cloud/VRF,
   Zustand (aktiv/aus, oper up/down) und SNAT: Avi setzt die Quelle standardmässig auf die
   Service Engine, ausser das Application Profile erhält die Client-IP
   (`preserve_client_ip`). Child-Virtual-Services (SNI) erben die VIP ihres Parents.
   Content Switching über HTTP-Policies wird nur als Hinweis geführt („weitere Pools über
   Richtlinien“), nicht ausgewertet.
7. **Verwendung:** die Einträge erscheinen in **Publizierte Dienste** und **NAT** (Quelle =
   das Avi-Quellsystem), die VIP-Adressen gelten als gemanagt (kein Befund „extern“), der
   Abgleich prüft die Server gegen das IPAM wie bei der FortiGate („Ziel nicht im IPAM“).
   In der **Weg-Suche** endet der Weg zur VIP nicht mehr im Netz der VIP: dort steht ein Hop
   „Load Balancer“ (Virtual Service, Pool, gewählter Server = erster aktiver), danach geht der
   Weg zum Server weiter – Quelle nach dem Load Balancer ist die Service Engine (SNAT; Adresse
   unbekannt, Automato setzt die VIP-Seite als Start) bzw. der Client bei `preserve_client_ip`.
   Die **Doku je System** und die **Applikations-Doku** führen die Dienste dort, wo VIPs mit dem
   System als internem Ziel stehen (Server im Pool); die **Verbindungen Ende zu Ende** gehen wie
   die Weg-Suche über den Load Balancer.
8. **Kein Schreibweg**, keine Aktionen (kein Aktivieren/Deaktivieren von Servern), keine
   Statistiken/Logs.

## Folgen

- Regeln zu einer Avi-VIP zeigen das Ziel dahinter; die Weg-Suche geht über den Load
  Balancer bis zum Server; die Doku einer Applikation kennt ihre Avi-Dienste.
- Neue Abhängigkeit: keine (httpx wie beim APIC).
- Offen: Adressen der Service Engines lesen (`serviceengine`, Daten-NICs) für die genaue
  Quelle nach dem SNAT; Content Switching (HTTP-Policies, DataScripts) auswerten; GSLB.

## Ergänzung 0.79.0 – VIP der FortiGate vor dem Load Balancer

Rückmeldung des Anwenders: zeigt das Ziel einer VIP (DNAT) der FortiGate auf die VIP eines
Virtual Service, gehören beide zusammen – öffentliche IP → VIP der FortiGate → Virtual
Service → Server des Pools. Entscheid (ohne Migration, abgeleitet beim Lesen):

1. **Verknüpfung** (`store/nat.Balancers`): interne IP der FortiGate-VIP = VIP-Adresse eines
   Virtual Service; bei Port-Weiterleitung nur der Virtual Service mit dem weitergeleiteten
   Port, sonst alle Ports dieser Adresse. Ebenso eine Regel, deren Ziel direkt die VIP des
   Load Balancers ist (Host im Endpunkt).
2. **Regelindex:** die Server hinter dem Load Balancer sind Ziel der Regel – mit dem Weg im
   Bezug („V-EXT_x → vs-web“). Damit findet die Suche die Regel über den Server, Virtual
   Service bzw. Pool, und Doku, Relevanz, Kunden und Zonen sehen sie. Nach jedem Lauf des
   NSX ALB wird der Index des ganzen Mandanten neu aufgebaut.
3. **Relevanz:** für ein Ziel über den Load Balancer zählt die Route zur VIP des Load
   Balancers, nicht die zum Server dahinter.
4. **Anzeige:** Regel-Detail (VIP mit Virtual Service, Pool, Servern), publizierte Dienste
   (VIP der FortiGate → Virtual Service; Virtual Service „über VIP …“, die Regeln der VIP davor
   zählen mit), Applikations-Doku Kategorie 2 mit Spalte „Load Balancer“, Verbindungen Ende zu
   Ende mit der öffentlichen IP als Ziel (Weg über DNAT und Load Balancer).
5. **Weg-Suche:** am DNAT-Hop „NSX ALB vs-…“; liegt die VIP des Load Balancers hinter einem
   Router ohne gelesenes Gerät, geht der Weg trotzdem vom Load Balancer zum Server weiter.


## Ergänzung 0.95.0 – Kette in NAT / VIPs

Rückmeldung des Anwenders (Test 2026-10-27): „Bei den NAT / VIPs sind die zusammengehörenden
VIPs der Internet Firewall und die des Loadbalancers noch nicht miteinander verknüpft … Ziel
muss sein, dass ich sehe, welche externe IP zu welchen Servern geht, egal ob es noch durch
einen Loadbalancer führt.“

Ursachen: Die Ansicht NAT / VIPs zeigte die Verknüpfung aus 0.79.0 gar nicht (nur Regel-Detail,
publizierte Dienste, Weg-Suche). Die Verknüpfung selbst griff nur bei genau einer internen IP:
Bereiche in `mappedip` (1:1), mehrere Werte in `mappedip` (gespeichert war nur der erste),
Port-Weiterleitung mit Port-Bereichen, Virtual Server der FortiGate mit der VIP des Load
Balancers als Real Server und FQDN-VIPs fielen durch. Policies auf eine **VIP-Gruppe**
(`firewall/vipgrp`) kannten die VIPs der Gruppe nicht – der Collector las die Gruppen nicht.

Entscheid (ohne Migration, abgeleitet beim Lesen):

1. **Verknüpfung robust** (`store/nat.Balancers.links`): interne Adressen der VIP aus allen
   Werten von `mappedip` (Rohdaten), Bereiche als Bereich (eine VIP des Load Balancers im
   Bereich trifft; die externe Adresse dazu an derselben Stelle des externen Bereichs, wenn
   beide gleich gross sind), IPv6 normiert. Port-Weiterleitung: Virtual Services, deren Port im
   weitergeleiteten Bereich liegt; der externe Port mit demselben Versatz. Virtual Server der
   FortiGate: Real Server mit ihrem Port. FQDN-VIP: `mapped-addr` → FQDN des Adressobjekts =
   FQDN des Virtual Service (`vsvip.dns_info`). Umgekehrt `fronts` = die VIPs davor. Regelindex,
   Doku, publizierte Dienste und Relevanz nutzen dieselbe Verknüpfung.
2. **NAT / VIPs** (`GET /nat`, `GET /nat/{id}`): je VIP der Firewall `chain` = externe IP:Port
   → interne IP (VIP des Load Balancers) → Virtual Service (Quelle, Avi-Tenant, VRF, Floating
   IP, Zustand) → Pool → Server (IP:Port, Status, Firewall-Objekt, DNS-Name); am Virtual Service
   `fronted_by` = VIPs davor mit der externen IP; `dest_servers` = **Ziel-Server** jeder VIP
   (über den Load Balancer, sonst Real Server bzw. zugeordnete IP); `groups` = VIP-Gruppen. Die
   Regeln am Virtual Service sind die der VIPs davor bzw. mit seiner VIP als Ziel (Regelindex
   `nat` führt den Virtual Service, Filter `nat=` in der Regel-Liste).
3. **Suche** über die ganze Kette: externe IP (auch im Bereich) findet VIP und Virtual Service,
   Server-IP, Firewall-Objekt bzw. DNS-Name des Servers findet die VIP der Firewall davor,
   Virtual Service, Pool und VIP-Gruppe ebenso. Für Kunden-Benutzer nur VIPs davor aus ihrem
   Ausschnitt.
4. **Oberfläche:** Spalte „Ziel-Server“ (ersetzt „Server“) mit „über vs-…“, unter dem internen
   Ziel „→ vs-… (Load Balancer)“, am Virtual Service „davor 203.0.113.10:443 · V-EXT_…“; im
   Detail „Kette über den Load Balancer“ bzw. „Davor: VIPs der Firewall“ mit Sprung zwischen
   den Einträgen. **Export (CSV)** der Liste mit Suche und Filter, je Weg externe IP → Ziel-Server
   eine Zeile (Trennzeichen `;`, UTF-8 mit BOM, Spalten in der Sprache der Oberfläche).
5. **VIP-Gruppen** (Collector, nur lesend, optional wie `vip6`): `firewall/vipgrp` und
   `firewall/vipgrp6` → Adressgruppe vom Typ `vipgrp`; Policies auf die Gruppe lösen wie
   Adressgruppen in ihre VIPs auf. Ältere Agenten liefern sie nicht – dann bleibt es wie bisher.

Folgen: wirkt für Regel-Zahlen, Filter `nat=` und Suche der Regeln nach dem nächsten Einlesen
(der Regelindex wird je Lauf neu aufgebaut); die Kette in NAT / VIPs sofort. Nur im selben
Mandanten. Bewusst nicht: FQDN-VIP über die DNS-Auflösung im IPAM, gleiche VIP in mehreren
Avi-VRFs unterscheiden (beide werden gezeigt, mit VRF), Content Switching.
