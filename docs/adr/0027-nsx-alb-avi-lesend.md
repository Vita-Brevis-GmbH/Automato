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
