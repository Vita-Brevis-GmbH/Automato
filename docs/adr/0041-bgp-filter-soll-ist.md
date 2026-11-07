# ADR 0041 — BGP-Filter und Route Control lesen: Soll/Ist je Nachbar

## Status

**Angenommen** (2026-10-29): Stufe 1 in 0.113.0 (abgenommen mit 0.113.1), Stufe 2 in 0.114.0
(abgenommen mit 0.114.1), Stufe 3 in 0.116.0 – zur Abnahme durch den Anwender.

Rückmeldung des Anwenders zu 0.110 (2026-10-29): „Ich habe in der Wegsuche Backup Wege erhalten,
welche nicht funktionieren würden. Die Firewalls dahinter lernen zwar das Netzwerk auch, aber die
Firewalls davor würden das Netzwerk von der Firewall dahinter niemals lernen, da sie in den Filter
gesperrt wurden.“ Roadmap Version 1 Nr. 3 („Filter im Routing“). Gewählt für 0.113.0: **Filter
lesen + Soll/Ist** – die BGP-Konfiguration der FortiGate und die Route Control der ACI-L3Outs strikt
lesend einlesen, je Nachbar erwartet/erhalten/angekündigt zeigen, Abweichungen als Befund, die
Netze der Filter als Vorschlag für „Netze dahinter“ der Anbindungen. Filter je Zone ableiten
folgt in der nächsten Version (Stufe 2).

Ergänzt ADR 0019 (Routing, BGP), ADR 0020 (Anbindungen), ADR 0034 (L3Out Firewall führend),
ADR 0036 (Module: Analyse), ADR 0039 (Prüfungen je Modul) und ADR 0040 (Netzlisten der
Anbindungen). Migration 0070 (`topo_bgp_object`, `topo_l3out.route_control`).

## Kontext

- Bis 0.112.0 kam BGP nur aus dem Monitor-API der FortiGate (Nachbarn mit Zustand, alle Pfade).
  Die Konfiguration `router bgp` wurde wegen der Passwörter der Nachbarn nicht gelesen – damit
  fehlten die Filter (Prefix-Listen, Route-Maps), die bestimmen, was eine Firewall von einem
  Nachbarn übernimmt und ihm weitergibt.
- 0.112.0 zeigt Backup-Wege nur noch, wenn das Gerät das Netz gelernt hat. Ob die Firewall davor
  das Netz von der dahinter je lernen würde, sagt erst ihr Eingangsfilter bzw. der Ausgangsfilter
  der anderen.
- Beim ACI entscheidet die Route Control eines L3Outs (`enforceRtctrl`, Subnetze der externen EPGs
  mit `import-rtctrl` bzw. `export-rtctrl`), welche Routen übernommen bzw. angekündigt werden;
  `import-rtctrl` wirkt nur, wenn Import am L3Out erzwungen ist.

## Entscheid

1. **Lesen, strikt nur lesend** (Collector, Positivliste):
   - FortiGate je VDOM: `router/bgp` (AS, Router-ID, `network`, `aggregate-address`, eingeschaltete
     `redistribute`, je Nachbar bzw. Nachbar-Gruppe die Filter `route-map-in/-out`,
     `prefix-list-in/-out`, `distribute-list-in/-out`, `filter-list-in/-out` – je auch `…6` – und
     wenige Angaben wie `remote-as`, `shutdown`, `route-reflector-client`; Nachbar-Bereiche für
     dynamische Nachbarn), `router/prefix-list`, `prefix-list6`, `route-map`, `access-list`,
     `access-list6` mit ihren Regeln. **Nie** `password`, `auth-options`, `keychain`.
   - Die Konfiguration je Nachbar hängt am gelesenen Nachbarn (`topo_peer.attrs.config`, bei
     dynamischen Nachbarn die der Gruppe); konfigurierte Nachbarn ohne Sitzung im Monitor stehen nur
     im BGP-Prozess (keine Nachbarn ohne Zustand – Verlauf, Anbindungen und Befunde bleiben).
   - ACI je L3Out: `enforceRtctrl` (fehlt = `export`) und die Subnetze mit `export-rtctrl` bzw.
     `import-rtctrl` (`aggregate` = alle Längen darin) → `topo_l3out.route_control`.
2. **Auswerten wie das Gerät** (`store/bgp_filters`): Prefix-Liste ohne ge/le = genau das Netz,
   `le`/`ge` als Bereich, `any` = alles; Access-Liste = das Netz und alles darin (`exact` = nur das
   Netz); Route-Map = erste passende Regel, eine Regel ohne Bedingung passt immer, keine passt =
   deny; alle Filter einer Richtung müssen erlauben, ohne Filter alles. Bedingungen, die die
   Plattform nicht auswertet (AS-Path, Community, Wildcard), ergeben „unbekannt“, nie „erlaubt“.
3. **Soll/Ist je Nachbar:** **erwartet** = was die Eingangsfilter ausdrücklich erlauben (permit-
   Regeln, auch über `match ip-address` einer Route-Map; „alles“-Regeln zählen nicht als Soll);
   **erhalten** = die BGP-Pfade vom Nachbarn (`learned_from`) mit dem Urteil des Filters;
   **angekündigt** (berechnet, die FortiGate meldet es nicht) = beste BGP-Pfade (nicht vom Nachbarn
   selbst, von iBGP an iBGP nur über Route-Reflector), `network`, `aggregate` und `redistribute`
   (RIB des Protokolls, mit dessen Route-Map) durch die Ausgangsfilter. ACI je L3Out: Import
   (erwartet, nur mit enforce import), erhalten = Routen der VRF über die Peers des L3Outs, Export.
4. **Befunde** (Prüfung `app/checks/analyse/bgp_filter.py`, Modul Analyse, je Mandant schaltbar):
   `bgp_expected_missing` (Filter erlaubt Netze, der verbundene Nachbar liefert nichts davon; ACI:
   Import erzwungen, nichts davon kommt an), `bgp_outside_filter` (erhalten, obwohl der Filter
   sperrt – Konfiguration und Tabelle passen nicht zusammen), `bgp_no_inbound_filter` (Info:
   eBGP-Nachbar ohne Eingangsfilter), `aci_import_not_enforced` (Info: import-rtctrl ohne enforce
   import – wirkt nicht).
5. **Vorschlag „Netze dahinter“:** für die Next-Hops einer Anbindung die Netze, die die
   Eingangsfilter der Nachbarn mit dieser IP bzw. die import-rtctrl-Subnetze der L3Outs mit diesem
   Peer erlauben (ohne „alles“ und Default-Route) – übernehmen wie beim Vorschlag aus dem Routing.
6. **Oberfläche:** Routing → BGP-Nachbarn mit Spalte „Filter (ein / aus)“ und „Soll/Ist“, darüber
   die gelesene BGP-Konfiguration je Firewall; L3Outs mit Spalte „Route Control“ und „Soll/Ist“;
   Anbindung mit „aus den BGP-Filtern“. API `GET /routing/bgp/config`, `/peers/{id}`,
   `/l3outs/{id}`, `/filtered` (viewer, Kunden-Ausschnitt wie die Listen).

## Folgen

- Das Read-only-Profil der FortiGate braucht Lesezugriff auf **Router** (Konfiguration, nicht nur
  Monitor); fehlt er, steht „BGP-Konfiguration“ in der Lauf-Meldung als nicht lesbar und die
  Soll/Ist-Sicht sagt „nicht gelesen“.
- Der Verlauf des Routings (ADR 0030) vergleicht die Filter noch nicht – Änderungen an
  Prefix-Listen erscheinen erst über ihre Wirkung (erhaltene Pfade).
- Kein neuer Schreibweg; alles Gelesene bleibt beim Quellsystem.

## Stufe 2 (0.114.0)

Anwender 2026-10-29 („go 114“, gewählt: Backup-Wege prüfen und das Soll aus **allen** Zonen, die
über den Nachbarn erreicht werden):

7. **Backup-Wege gegen die Filter** (`paths.Graph.backups`, `bgp_filters.Filters.blocked`): ein
   BGP-Backup vom Nachbarn X gilt nur, wenn der Eingangsfilter des Geräts von X das Netz erlaubt
   und – ist X eine gelesene FortiGate mit einem Nachbarn auf dieses Gerät – deren Ausgangsfilter
   ebenso. Sonst kein Backup-Weg, sondern der Hinweis „Backup an … über X verworfen: Eingangsfilter
   … vom Nachbarn X sperrt …“ (FortiGate mit soft-reconfiguration führt solche Pfade in der
   BGP-Tabelle, übernimmt sie aber nie). Unbekannt (AS-Path, Community) verwirft nicht.
8. **Soll je Nachbar aus den Zonen** (`store/bgp_zones`, Analyse): Zone der Firewall (Geräte der
   Zone), Zone am Nachbarn (gelesenes Gerät mit seiner IP, sonst der Übergang mit seinem Netz bzw.
   dem Interface dorthin), **Zonen dahinter** = alle, deren kürzester Weg im Zonenplan über die Zone
   am Nachbarn führt. Soll = ihre Netze (wie erfasst und abgeleitet, grössere umfassen kleinere)
   `permit … le 32`, Ausschlüsse davor `deny … le 32`, je Familie eine Prefix-Liste
   `PL_<Zone am Nachbarn>_IN`.
9. **Vergleich** mit den Eingangsfiltern: Netz der Zonen gesperrt („fehlt im Filter“), Ausschluss
   erlaubt, Regel ausserhalb der Zonen bzw. „alles“ („zu offen“), ohne Eingangsfilter. Befund
   `bgp_zone_mismatch` (Info, Prüfung `app/checks/analyse/bgp_zonen.py` im Teil-Durchgang „Zonen“ –
   neu nach jedem Speichern einer Zone).
10. **CLI-Vorschlag** (FortiOS `config router prefix-list`/`prefix-list6`, ohne Filter am Nachbarn
    mit `set prefix-list-in`) zum Kopieren im Soll/Ist des Nachbarn – nur Text, Automato schreibt
    nichts auf die Firewall. API `GET /routing/bgp/peers/{id}/zones`.

Offen nach Stufe 2: Ausgangsfilter je Zone, ACI import-rtctrl aus den Zonen (beides Stufe 3) und
der Vergleich im Verlauf des Routings.

## Stufe 3 (0.116.0)

Anwender 2026-10-30 („go 116“, Roadmap Version 1 Nr. 3 Rest):

11. **Ausgangsfilter je Nachbar aus den Zonen diesseits** (`ZoneSoll.for_peer(p, "out")`, API
    `…/peers/{id}/zones?way=out`): was die Firewall dem Nachbarn ankündigen soll = die Netze der
    Zonen, die der Nachbar über sie erreicht – die Zone der Firewall und alle Zonen, deren
    kürzester Weg von der Zone am Nachbarn über sie führt (`behind(Nachbar, Firewall)`, gleiche
    Regel wie Stufe 2 in die andere Richtung). Ausschlüsse `deny … le 32` davor, Prefix-Liste
    `PL_<Zone am Nachbarn>_OUT`, am Nachbarn `set prefix-list-out`.
12. **Nach aussen nur öffentliche Netze:** liegt die Zone am Nachbarn in einer Zone der Art
    `external`, enthält das Soll nur Netze ausserhalb der internen Bereiche (RFC 1918, CGNAT
    100.64/10, Link-Local, Loopback, ULA fc00::/7, fe80::/10) – feste Liste statt `is_global`,
    damit Dokumentations- und andere reservierte Bereiche im Lab wie öffentliche zählen.
13. **Vergleich** mit dem Ausgangsfilter wie in Stufe 2 (gesperrt, zu offen, Ausschluss erlaubt,
    ohne Filter) und zusätzlich mit der **heutigen Ankündigung** (`Filters.report`, angekündigt
    nach Filter): Routen ausserhalb der Zonen (gekürzt, mit Gesamtzahl); default-originate ist
    gewollt und zählt nicht. Befund `bgp_zone_out_mismatch` (Info, Prüfung
    `app/checks/analyse/bgp_zonen_aus.py`, Teil-Durchgang „Zonen“).
14. **ACI Import Route Control je L3Out** (`ZoneSoll.for_l3out`, API `…/l3outs/{id}/zones`): Zone
    der VRF des L3Outs (Selektor `aci_vrf`), Zone am Peer (Adressen der L3Out-Nachbarn), Zonen
    dahinter wie in Stufe 2. Je Netz der Zonen das Urteil der heutigen Route Control
    (`import-rtctrl`, aggregate); Abweichungen: Import nicht erzwungen (`enforceRtctrl` ohne
    import – der L3Out übernimmt alles), Netz ohne Import-Subnetz, Import-Subnetz ausserhalb der
    Zonen bzw. 0.0.0.0/0 aggregate, Ausschluss importiert. Befund `aci_import_zone_mismatch`
    (Info, `app/checks/analyse/aci_import_zonen.py`). Vorschlag als Text: Subnetze der externen
    EPG mit „Import Route Control Subnet“ (und aggregate, wo zusammengefasst) – Automato schreibt
    nichts ins ACI (ADR 0010).

## Ergänzung 0.128.0 (2026-11-03)

15. **Gleiche Zonen nicht über eine Zone dazwischen:** Anwender: „Eine Internet Firewall sieht
    zwar vielleicht alle Tenants, soll sie aber nicht mit einander verbinden“, Entscheid: der
    Schalter betrifft nur „die Verbindungen zu gleichen Zonen unterschiedlicher Tenants“, im
    Grundschema des Namensprofils, Vorgabe aus. Für `behind` (Zonen dahinter bzw. diesseits, Stufe
    2/3 und ACI Import) ist ein Weg A → Z → B gesperrt, wenn A und B denselben Zonentyp haben und
    der Zonentyp von Z im Grundschema nicht „verbindet gleiche Zonen“ (`automato_join_same`) hat.
    Kürzeste Wege mit dieser Sperre (Breitensuche über Paare Zone/Vorgänger); Übergänge zu Zonen
    anderer Typen und Zonen ohne Zonentyp wie bisher. Die Zonen, die nur ohne Sperre dazu
    gehörten, stehen in der Begründung (`chain.not_via`). Konfiguration statt Code: ohne Häkchen
    trennt jede Zone gleiche Zonen – die verbindende (Zonen-Firewall) setzt der Anwender.

## Ergänzung 0.130.0 (2026-11-03)

16. **Session-Sync ist kein Zonenübergang:** Anwender: „Es zeigt mir nun auch andere Zonen an, wo
    es nicht möglich sein soll. Zonen, welche in anderen Tenants oder Session Links, welche dort
    nicht hingehören“. Das Netz mit der Adresse eines FGSP-Sync-Partners (`peerip` aus `topo_ha`),
    an dem nur die beiden Partner liegen, ist im Zonenplan (`zone_struct.build`) weder Übergang noch
    Netz einer Zone (`Structure.sync_links`) – es trägt keine Daten, verband aber Zonen (fremde
    Zonen dahinter) bzw. stand als Netz im Soll. Liegt ein drittes Gerät daran, bleibt es ein
    gewöhnliches Netz.
17. **Abstand je Weg:** die Breitensuche über Paare Zone/Vorgänger (Nr. 15) zählt je Paar seinen
    eigenen Abstand; bis 0.129.0 gab eine Zone, die zuerst über einen gesperrten Durchgang und
    später über einen Umweg erreicht wurde, den kürzeren Abstand weiter (Zonen dahinter mit zu
    kleinem Abstand, falsche Zonen im Soll).
18. **Weg je Zone in der Begründung:** `chain.zones[].path` = die Zonen ab der Zone der Firewall
    (Ausgang: ab dem Nachbarn) mit dem Netz je Übergang (zwischen Firewall und Nachbar zuerst das
    Netz mit seiner Adresse, `more` = weitere Netze desselben Übergangs) – sichtbar, welcher
    Übergang eine Zone hereinbringt; in Oberfläche („Weg je Zone“) und Befund.

## Ergänzung 0.131.0 (2026-11-04)

19. **Netze von Hand nur als Rahmen:** Anwender: „in dem Netzwerk design Kategorien habe ich den
    groben Filter angewendet, der sollte aber nicht auf die Routing Filter angesetzt werden sondern
    nur Filtern, welche Netzwerke dort dahinter sein könnten“. Ein Selektor „Netz“ einer Zone ist
    für das BGP-Soll kein Eintrag mehr, sondern der Rahmen: im Soll stehen die bekannten Netze darin
    (Routen der RIBs des Mandanten, ohne lokale Adressen und Host-Routen, gleich oder genauer als
    der Rahmen), die dieser Zone gehören – genauestes Netz von Hand bzw. abgeleitetes Netz bzw. Netz
    an einem Gerät einer Zone entscheidet; ohne Transfernetze und ohne Sammelrouten über Netze einer
    anderen Zone. Ein Netz an Interfaces gelesener Geräte, von denen keines in der Zone steht (die
    Zone hat selbst Geräte), gehört nicht zu ihr, auch wenn ihr Rahmen es umfasst. Ohne bekanntes
    Netz im Rahmen: Hinweis (`frames`), kein Eintrag; 0.0.0.0/0 bzw. ::/0 gilt als Ganzes. Eine
    Filterregel im Rahmen ist nicht „zu offen“. Die Zonen selbst (Zonenplan, Kategorien) bleiben wie
    bisher.
20. **Transfernetze nicht im Soll:** Anwender: „die Transfer Netzwerke nicht mit eingerechnet …
    (oder ist dies nach Technischem Standard ein Fehler, von dem ich nichts weiss?)“ – üblich ist,
    Transfernetze nicht zu verteilen (der Next Hop liegt im direkt angeschlossenen Netz; nur für
    Überwachung bzw. Management nötig). Transfernetz = Übergang zwischen Zonen, Session-Sync bzw.
    Netz einer Zone mit zwei Geräten oder einem L3Out daran bis /27 (IPv4) bzw. /64 (IPv6) –
    grössere Netze mit zwei Routern sind eher ein LAN mit VRRP/HSRP (`zone_struct.transfer_nets`).
    Nicht im Soll; erlaubt bzw. angekündigt ist keine Abweichung.
21. **Adressfamilien der Sitzung:** die Familien wie bisher (`_families`), ohne die am Nachbarn
    ausgeschalteten (`activate`/`activate6` = disable, `session_families`); je Familie der Sitzung
    eine Liste. Je L3Out die Familien der Adressen seiner BGP-Peers (ohne Adresse beide).
22. **default-originate:** mit `capability-default-originate` zählt eine Route, deren Ausgangsfilter
    sich nicht auswerten lässt (Urteil unbekannt), nicht mehr als angekündigt (`originate_only`) –
    technisch kündigt die FortiGate mit default-originate zusätzlich die Default-Route an; ob
    weitere Routen gehen, entscheidet der Ausgangsfilter. Ohne Eingangsfilter nennt das Soll, was
    heute vom Nachbarn ankommt (`received_total`, `received_default_only`) – default-originate an
    der Firewall wirkt nur im Ausgang.
23. **Vorab berechnet** (`bgp_cache`, wie ADR 0037): je Mandant und Sprache im Prozess, Abdruck wie
    die Doku; Vorrechnen nach Läufen und nach Änderungen an Zonen, Verbund und L3Outs, Anfragen
    ohne passenden Stand einzeln wie bisher.

## Ergänzung 0.132.0 (2026-11-04)

24. **Eingangsfilter nur zu fremd verwalteten Zonen:** Anwender: „Generell Filtere ich lediglich
    die Ausgänge bei Geräten, welche ich selber kontrolliere. Eingangsfilter gibt es nur bei
    Verbindungen nach nicht durch mich verwaltete Geräte. Also WAN, MPLS und Fremde Anbindungen“;
    Vorgabe „Standard immer intern Verwaltet“. Je Zone das Merkmal `external_managed` (Migration
    `0072`, Netzwerkdesign, Vorgabe nein – auch für Zonen der Art `external`). Ohne Eingangsfilter
    ist nur ein Nachbar in einer fremd verwalteten Zone eine Abweichung (`open_ok` sonst); der
    Befund `bgp_no_inbound_filter` gehört deshalb zur Prüfung `bgp_zonen` (Teil-Durchgang Zonen)
    statt `bgp_filter` und braucht keine eBGP-Sitzung mehr. Ein vorhandener Eingangsfilter wird
    wie bisher mit den Zonen verglichen. ACI: Import nicht erzwungen nur zu einer fremd
    verwalteten Zone am Peer eine Abweichung. Ohne Zone am Nachbarn: selbst verwaltet.
25. **default-originate = nur die Default-Route:** Entscheid des Anwenders („dieser Filtert
    bereits alles weg und sendet dem nächsten Gerät nur noch die Default Route“): mit
    `capability-default-originate(6)` kündigt die Firewall in dieser Familie nur die Default-Route
    an (`report`: übrige gezählt `originate_only`, nicht gezeigt), das Soll aus den Zonen im
    Ausgang entfällt (kein Vorschlag, keine Abweichung, `originate`). Hinweis an den Anwender: nach
    FortiOS-Dokumentation sendet default-originate die Default-Route zusätzlich, weitere Routen
    unterdrückt der Ausgangsfilter bzw. fehlende network/redistribute – Automato folgt hier dem
    Betriebsstandard des Anwenders.

Offen: Vergleich im Verlauf des Routings (Änderungen der Filter über die Zeit), Export-Route-Control
gegen die Zonen diesseits (heute nur gelesen und gezeigt).
