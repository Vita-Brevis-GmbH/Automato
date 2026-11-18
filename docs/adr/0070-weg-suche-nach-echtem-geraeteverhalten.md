# ADR 0070 — Weg-Suche nach echtem Geräteverhalten: Forwarding-Modell und Stufen je Hersteller

## Status

**Angenommen** (2026-11-18) – zur Abnahme durch den Anwender.

Anwender 2026-11-18:

- „Da ich nicht alle Geräte im Dev habe, würde ich gerne zuerst die Wegsuche (Routing) und
  Firewall Rule zuordnung zu Applikation den Algorithmus kontrollieren lassen … Es soll absolut
  der Wahrheit entsprechen, wie das Netzwerk in Echt funktioniert.“
- „Direkt korrigieren oder noch besser, wenn es schneller geht, einen neuen Algorithmus schreiben,
  damit die Suche auch schneller und besser funktioniert.“
- „Ich bin für ersetzen, da das alte Verhalten nicht immer richtig lag.“

Ersetzt den Kern von ADR 0019 Stufe 3 (Weg je Hop); ergänzt ADR 0032 (Router), ADR 0027 (Load
Balancer), ADR 0048 (keine stillen Grenzen, Nachtrag dort), ADR 0063 (IPsec, Nachtrag dort). Die
Regel-Zuordnung nach echtem Treffer ist ADR 0071.

## Kontext

Die Prüfung der Weg-Suche gegen die Herstellerdoku und drei Stichproben des Anwenders (anonymisiert
nachgestellt in `orchestrator/tests/test_fable_wege.py`, `collectors/tests/test_fable_wege.py`)
fand 18 Abweichungen (A-1 … A-18). Mehrere hatten dieselbe Wurzel: die Entscheidung je Hop war
eine Schleife ohne Begriff von *verwerfen*, *lokal* oder *Leitung*, der Dienst erreichte die
Routing-Stufe nicht, und ohne Start begann der Weg auf dem Gerät irgendeiner passenden Regel.

Belege (Herstellerdoku; in der Prüfung nur über Suchtreffer erreichbar, nicht im Wortlaut gelesen
– wo ein Verhalten nicht sicher belegt ist, gilt „nicht geprüft“ statt einer Annahme):

- FortiOS „Parallel Path Processing (Life of a packet)“: DNAT vor dem Routing, Policy-Routen und
  SD-WAN vor der Routing-Tabelle; „Routing concepts“ (Blackhole, Reverse Path, Priorität);
  „Policy routes“ (Protokoll und Ports, Reihenfolge der Liste); „Stop policy based routing“;
  „Equal cost multi-path“; „Virtual IPs with port forwarding“; „DNAT logic when Central NAT is
  disabled“; „IPsec … no matching IPsec selector, drop“; „SD-WAN rule matching“.
- Cisco „Specify a next hop IP address for static routes“ und „Recursive static route“;
  RFC 4271 §5.1.3 (rekursiver Next-Hop).
- VMware Avi „Application profile“ (HTTP → HTTPS), „Virtual hosting / SNI“.

## Entscheid

1. **Ersetzen, kein Umschalter.** Der Kern der Weg-Suche ist neu; es gibt kein altes Verhalten
   zum Zurückschalten und keinen Schattenbetrieb im Produkt. Das Ergebnisformat von `paths.trace`
   bleibt (Wege-Speicher, Vergleich, Rückweg, VIP, VPN, Zonen und Fingerabdruck arbeiten weiter);
   neu sind nur zusätzliche Felder und drei Arten des Endes.
2. **Forwarding-Modell je Knoten und VRF** (`store/forwarding.py`) – was ein Gerät mit einem
   Paket tut, getrennt von der Suche:
   - Auswahl: längstes Präfix, kleinste Distanz, (FortiGate) kleinste Priorität; was danach gleich
     ist, ist ECMP. Der Collector fasst Routen gleicher Distanz mit verschiedener Priorität nicht
     mehr zu ECMP zusammen (die übrigen stehen als nicht wirksam da).
   - Aktion: `connected`; `drop` (Null0, Blackhole, discard – der Weg ist **nicht** erreicht);
     `forward`; `link` (Route nur mit Interface).
   - Rekursiver Next-Hop: steht ein Next-Hop ohne Interface da (iBGP über Loopback), wird er über
     die Routing-Tabelle aufgelöst (höchstens 4 Stufen, nie über die Default-Route) – der Weg geht
     über das Gerät dazwischen. Mit Interface (FortiGate `gateway` + `device`) keine Rekursion.
   - Route nur mit Interface: Inter-VDOM-Link und IPsec-Tunnel wie bisher; eine
     Punkt-zu-Punkt-Leitung (Tunnel, seriell, /30 bzw. /31) geht an die gelesene Gegenseite bzw.
     endet „Gegenseite nicht gelesen“; auf einem Netz mit mehreren Teilnehmern fragt das Gerät
     direkt nach dem Ziel (ARP) – ob es dort ist, ist nicht belegt: Ende `unchecked`.
   - Die Entscheidung je Menge gleicher Routen wird je Datenstand einmal gerechnet und im
     Wege-Speicher geteilt.
3. **Stufen je Hersteller** (`store/path_stages.py`), jede mit Entscheidung und Begründung:
   - **FortiGate:** Reverse Path am Eingang → DNAT (VIP nach Adresse, `extintf` **und Dienst**;
     mehrere VIPs je Adresse nach Port; ohne Central NAT nur VIPs in einer eingeschalteten
     Policy) → Policy-Routen in der Reihenfolge der Liste (Position vom Collector, nicht
     `seq-num`) mit Protokoll und Port; `action deny` beendet das Policy-Routing → SD-WAN (Regel
     übergangen, wenn die beste Route nicht über ein SD-WAN-Mitglied geht; Mitglieder mit Route
     zum Ziel) → Routing-Tabelle → IPsec-Selektoren am Ausgang → Central SNAT.
   - **Router/L3-Switch:** Routing-Tabelle; virtuelle Gateway-Adressen (HSRP/VRRP) auf IOS/IOS-XE
     aus `show standby brief` bzw. `show vrrp brief` – den Verkehr nimmt der aktive Router an.
   - **ACI:** Fabric, Leak, Border-Leafs wie bisher (Contracts: ADR 0071).
   - **Load Balancer (NSX ALB):** alle Virtual Services derselben VIP und desselben Ports zusammen
     (SNI: der Parent ohne Pool verteilt auf die Pools der Children), Weg zu **allen**
     Mitgliedern; eine Umleitung HTTP → HTTPS endet am Load Balancer (Ende `redirect`, erreicht).
4. **Quelle, wie sie am Gerät ankommt.** Reverse Path und IPsec-Selektoren prüft die Weg-Suche
   nach den Regeln (`paths._enforce`) mit der Quelle nach dem SNAT der Hops davor; hinter einem
   Load Balancer mit SNAT ist die Adresse der Service Engine unbekannt – dort kein Reverse Path.
5. **Reverse Path nur lesend.** Der Collector liest je VDOM `system settings` (`asymroute`,
   `strict-src-check`, `central-nat`) und `src-check` je Interface (`topo_context.settings`,
   Migration 0097). Gelesen: ohne aktive Route zur Quelle über den Eingang ist der Weg
   **verworfen** (`dropped`); `asymroute` bzw. `src-check disable` schalten die Prüfung ab,
   `strict-src-check` verlangt die beste Route. Nicht gelesen (älterer Lauf, kein Leserecht):
   nach der Vorgabe des Geräts verwürfe es – der Weg endet als **nicht geprüft** (`unchecked`),
   gezählt, nie still verworfen und nie als erreicht behauptet.
6. **Central NAT:** je VDOM erkannt (`central-nat`). Aktiv: jede eingeschaltete VIP wirkt als
   DNAT (auch ohne Policy), das SNAT kommt nur aus `firewall central-snat-map` in der Reihenfolge
   der Tabelle (erste passende Zeile: Interfaces, Adressen, Protokoll) – nicht aus der Policy.
   Tabelle nicht lesbar bzw. Zeile nicht sicher prüfbar: „NAT nicht geprüft“, gezählt. Ob Central
   NAT aktiv ist, nicht gelesen und eine VIP steht in keiner Policy: kein DNAT (Vorgabe des
   Geräts), gezählt.
7. **Start nur, wo Verkehr ankommt.** In den Verbindungen der Applikationen beginnt der Weg (a) am
   Knoten, an dem die Quelle angeschlossen ist bzw. in ihrer ACI-VRF, (b) am Eingang nach dem
   Rückweg (0.107.0), (c) an den Knoten am Rand, die die Quelle über einen nicht gelesenen
   Nachbarn kennen (Eingang = Interface dieser Route; ein Eingang, der den Verkehr gleich wieder
   zurückschickt, zählt nicht; eine private Quelle nur über die Default-Route zählt nicht), (d) in
   der ACI-VRF der EPG, in der die Quelle nachweislich ist. Den Rückfall auf das Gerät einer
   Firewall-Regel gibt es nicht mehr – sonst „Start unbekannt“, gezählt.
8. **Privates Ziel über die Default-Route an ein nicht gelesenes Gerät** (auch an eine Anbindung
   „Internet“ ohne passende Netze): Ende `unchecked` „nicht geprüft (Default-Route)“ statt
   „extern“ bzw. erreicht. Öffentliches Ziel über die Default-Route bleibt „extern“/„Internet“.
9. **Rückweg mit ECMP:** symmetrisch nur, wenn **alle** gleichwertigen Rückwege über dieselben
   Firewalls gehen (Verbund und Session-Sync zählen weiter als eine Firewall); sonst
   asymmetrisch mit dem Anteil der abweichenden Rückwege.
10. **Jede Unsicherheit gezählt** (`result.uncertain`, in den Verbindungen `coverage.cuts`, Befund
    `app_coverage` – Nachtrag zu ADR 0048).
11. **Speicher je Äquivalenzklasse:** die Forwarding-Entscheidung je Menge gleicher Routen (alle
    Ziele derselben Route), die Urteile je Adressklasse der Policies (ADR 0048) und Wege zu
    Servern derselben Klasse (gleiche Hops, gleiche Adressklasse) als **ein** Weg mit allen
    Servern (`targets`). Ganze Wege bleiben je Aufruf gemerkt: sie tragen die Adressen und Namen
    des Aufrufs – ein Speicher je Klasse über den ganzen Weg müsste jede Abhängigkeit (VIP, Fabric,
    Selektoren, Anbindungen, IPAM-Namen) kennen; ein übersehener Fall wäre ein still falscher Weg.

## Neue Felder im Ergebnis von `paths.trace`

- `end.kind`: zusätzlich `dropped` (das Gerät verwirft), `unchecked` (nicht geprüft), `redirect`
  (Umleitung am Load Balancer; zählt als erreicht, `paths.REACHED`).
- `uncertain`: `{Art: Anzahl}` – `truncated`, `policy_route`, `sdwan`, `rpf`, `nat`, `vpn`,
  `default`, `onlink`, `return`; in den Verbindungen dazu `start`.
- Hop: `route.action`, `route.recursive`, `egress.recursive` (`next_hop`, `via`), `dropped`
  (`stage`: `rpf` | `ipsec`, `text`), `central_snat`, `nat.targets`/`lb.targets` (Server dieses
  Wegs), `lb.services` (weitere Virtual Services), `lb.redirect`, Policy-Routen mit `action`,
  `members`, `skipped`.

## Folgen

- **Sichtbar anders:** mehr Wege enden als „verworfen“, „nicht geprüft“ bzw. „Start unbekannt“;
  Applikationen werden dadurch „unvollständig geprüft“ (Befund `app_coverage`), bis das Gerät
  dahinter gelesen bzw. als Anbindung erfasst ist. Befunde und Stand der Doku ändern sich nach dem
  Update (die Version ist Teil des Datenstands der Wege).
- **Neu einlesen:** Reverse Path, Central NAT, Priorität, Reihenfolge der Policy-Routen, HSRP auf
  IOS und die Umleitung am Load Balancer kommen erst mit dem nächsten Lauf der Quellsysteme. Bis
  dahin gilt für Firewalls „Reverse Path nicht geprüft“, wo er nach der Vorgabe scheitern würde.
- **Leserechte:** FortiGate zusätzlich `system/settings` und `firewall/central-snat-map` (nur
  lesend; ohne Recht „VDOM-Einstellungen“ bzw. „Central SNAT“ als nicht lesbar im Lauf); Switches
  zusätzlich `show standby brief`, `show vrrp brief` (Positivliste).
- **Laufzeit:** je Weg nicht langsamer (Messung in `docs/TESTS.md`, `scripts/bench_fable_wege.py`).
- **Nicht gelesen bzw. nicht entscheidbar (bleibt gezählt bzw. offen):** Zustand der SD-WAN-Links
  (welches Mitglied), Policy-Routen nach TOS, Adressobjekte ohne Bereich (FQDN, Geo, ISDB) in
  Policy-Routen und Central SNAT, Pools des Load Balancers nur über HTTP-Policy-Sets bzw.
  DataScripts, VRF-Leaking der FortiGate, gleiche Adresse in mehreren VRFs (der Besitzer einer
  Next-Hop-Adresse wird mandantenweit gesucht), Reverse Path auf Routern (uRPF nicht gelesen),
  Felder einer Blackhole-Route in der Monitor-API der FortiGate (nicht belegt – erkannt wird sie
  am Interface `Null`/`blackhole`, am Protokoll bzw. über die statische Konfiguration
  `blackhole enable` mit gleichem Präfix und gleicher Distanz).
