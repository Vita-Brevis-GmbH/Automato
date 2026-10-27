# ADR 0032 — Routing bis an die Firmengrenze: Router und L3-Switches lesen, Grenze markieren

## Status

**Entwurf** (2026-10-27) – erste Fassung, zur Abnahme durch den Anwender. Auftrag des Anwenders
2026-10-27: „Systeme bis an die Firmengrenze“. Ergänzt ADR 0019 (Topologie und Pfade, Stufe 3
„Weg Hop für Hop“ und Stufe 4 „Topologie-Modell“), ADR 0025 (Switches & Router strikt lesend über
SSH) und ADR 0030 (Verlauf des Routings).

## Kontext

- Der Weg (`store/paths.py`) läuft über die gelesenen Routing-Instanzen: FortiGate-VDOMs und
  ACI-VRFs. Gehört der Next-Hop keinem gelesenen Gerät, endet er dort („Next-Hop … gehört
  keinem gelesenen Gerät“, „weiter nach extern – Default-Route“). In grösseren Umgebungen liegen
  dahinter aber noch eigene **Core-, WAN- und Aussenrouter** – der Weg endet mitten in der Firma.
- Diese Router sind meist Cisco IOS-XE/NX-OS, Juniper MX bzw. Arista – also dieselben Plattformen,
  die der Collector „Switches & Router“ (ADR 0025) schon strikt lesend per SSH liest (Positivliste
  `show`, `ReadOnlyViolation`, Host-Schlüssel). Gelesen wurden bisher nur Inventar, Ports, VLANs,
  Nachbarn, MAC/ARP – **nicht das Routing** (Lesevertrag: „Nicht gelesen: Routing-Tabelle“).
- „Erkannte Anbindungen“ (`store/exits.py`) und die Topologie (`store/topo_model.py`) zeigen
  Router ohne gelesenes Gerät als Gegenstellen; ob dort die Firma endet (Provider, Partner) oder
  nur ein nicht gelesener eigener Router hängt, ist nicht unterschieden.
- Vorgabe: nur NetBox wird geschrieben, alles andere strikt gelesen; keine Konfiguration lesen
  (kein `show running-config` – Passwörter, Schlüssel, Communities); SNMP ist ein eigener Auftrag.

## Entscheidung

1. **Routing der Router und L3-Switches lesen** (`collectors/cisco_switch/routing.py`): je Gerät
   zusätzlich zu den bisherigen Befehlen die Routing-Befehle seiner Plattform – **nur `show`**,
   in der Positivliste (`client.ALLOWED` = Befehle der Plattformen ∪ `routing.ALL`):

   | Plattform | Befehle | Auswertung |
   |---|---|---|
   | IOS/IOS-XE | `show ip route`, `show ip route vrf *`, `show ipv6 route`, `show ip bgp summary`, `show bgp ipv4 unicast` | ntc-Vorlagen (`cisco_ios`; BGP-Pfade mit der Vorlage `show ip bgp`) |
   | NX-OS | `show ip route vrf all`, `show ipv6 route vrf all`, `show ip bgp summary vrf all` | eigene Auswertung der RIB (beide Familien, nur `*via`), ntc für BGP |
   | Junos | `show route \| display json \| no-more`, `show bgp neighbor \| display json \| no-more` | JSON (alle Tabellen mit aktiven und inaktiven Einträgen; `show bgp summary` nennt das lokale AS nicht – daher `show bgp neighbor`, nur die Felder der Positivliste) |
   | Arista EOS | `show ip route vrf all \| json`, `show ipv6 route vrf all \| json`, `show ip bgp summary vrf all \| json`, `show ip interface \| json` | JSON; die eigenen Adressen (primär, sekundär, VARP) werden `local`-Routen |

   Ergebnis wie bei FortiGate/ACI (`RoutingInventory`): je Gerät und VRF ein **Kontext der Art
   `l3`** (Name = Gerät bzw. `Gerät:VRF`, `node` = Gerät), Routen der RIB (ECMP = mehrere
   Next-Hops, Interfaces in der Kurzform des Inventars, ohne Interface das des angeschlossenen
   Netzes), BGP-Pfade (bester und Backup) und bei Junos die statischen Routen, **BGP-Nachbarn**
   mit AS und Zustand, `attrs.local_as` und `attrs.type` (`ibgp`/`ebgp`). Management-VRFs
   (`management`, `mgmt`, `Mgmt-vrf`, `mgmt_junos` …), Link-local und Multicast fallen weg.
   Aruba CX und die Business-CLI bleiben ohne Routing (offener Punkt).
2. **L3-Gerät ist, wer Routen bzw. BGP-Nachbarn hat.** Ein Switch ohne `ip routing` (nur „Default
   gateway is …“) bzw. mit unbekanntem Befehl bekommt keinen Kontext; nach dem ersten
   Routing-Befehl entfallen die weiteren. Die Rolle im Inventar bleibt (ein L3-Switch ist in der
   L2-Sicht weiter ein Switch).
3. **Nicht lesbar statt Fehler:** fehlen Rechte (`authorization`, `permission`, `denied` …) bzw.
   bricht ein Befehl ab oder ist die Ausgabe nicht auswertbar, steht der Teil unter
   `topo_context.missing` mit den Bezeichnungen der FortiGate („Routing-Tabelle IPv4“,
   „BGP-Nachbarn“, „BGP-Pfade IPv4“ …; der Verlauf erkennt sie, ADR 0030) und als Hinweis im Lauf.
   Gibt es den Befehl bzw. die Funktion nicht (`% Invalid input`, BGP aus, IPv6 aus), bleibt der
   Teil ohne Hinweis leer.
4. **Obergrenze für grosse Tabellen** (Internet-Fullfeed): `routing.MAX_ROUTES` = 20 000 je
   Kontext, Tabelle und Familie. Behalten werden alle Routen ausser BGP und die Default-Route, dann
   BGP-Routen in privaten Netzen vor öffentlichen, kürzeste Präfixe zuerst; der Rest ist ein
   Hinweis im Lauf und der Teil gilt als „nicht (ganz) lesbar“ (`… (gekuerzt: n von m)`). Eine
   Text-Ausgabe über `client.MAX_OUTPUT` (32 MB) wird an der letzten vollständigen Zeile
   abgeschnitten und gekürzt ausgewertet (`cut`), JSON nicht (dann nicht lesbar); Routing-Befehle
   dürfen bis 300 s dauern.
5. **Leerer Stand geht mit:** der Collector schickt das Routing immer (auch ohne L3-Gerät), damit
   der Orchestrator den Stand des Quellsystems ersetzt (`topology.replace`). Der Verlauf (ADR 0030)
   nimmt die Router-Kontexte so automatisch mit; ein erster Lauf ganz ohne Routing schreibt keinen
   „Ausgangsstand“.
6. **Router als Knoten im Weg** (`paths.Graph`): Kontexte `l3` sind Knoten wie VDOM bzw. VRF
   (Bezeichnung „rt-01“ bzw. „rt-01 · VRF x“). Ihre Adressen (Next-Hop-Besitzer) kommen aus den
   `local`-Host-Routen der RIB (`Graph.router_addresses`), nicht aus dem Inventar; HSRP/VRRP-Adressen
   (NX-OS) und VARP (Arista) zählen als eigene, virtuelle Adressen (`attrs.virtual`: der Weg geht
   zu jedem Router, der sie meldet; in der Topologie keine Interface-Adresse und kein Befund
   „doppelte IP“). Der Weg läuft durch gelesene Router weiter; keine Firewall-Regeln am Router
   (keine ACLs gelesen).
7. **Topologie** (`topo_model`): Knotenart **`l3`** („Router (gelesen)“; `router` bleibt die
   Gegenstelle ohne gelesenes Gerät), Transfernetze aus den eigenen Adressen mit der Länge des
   angeschlossenen Netzes am selben Interface. „Erkannte Anbindungen“ und Router-Knoten aussen
   gibt es nur noch für wirklich nicht gelesene Next-Hops/Nachbarn (`exits.detect` über
   `Graph.owners`).
8. **Firmengrenze** (`store/boundary.py`): an einem Next-Hop bzw. BGP-Nachbarn, der **keinem
   gelesenen Gerät gehört**, wenn (in dieser Reihenfolge)
   - `ebgp`: er BGP-Nachbar des Knotens mit **fremdem AS** ist. Eigene AS = lokale AS der gelesenen
     BGP-Konfigurationen (`attrs.local_as`), AS der iBGP-Nachbarn und AS von Nachbarn, die selbst
     gelesene Geräte sind; ein privates AS steht dabei (`private_as`);
   - `exit`: eine Anbindung von Hand der Art **Internet, MPLS bzw. Partner** passt (WAN und
     „andere“ nicht);
   - `default`: die **Default-Route** über ihn geht und er eine öffentliche Adresse hat bzw. das
     Interface die FortiGate-Rolle `wan` trägt (Dokumentationsnetze gelten als öffentlich).

   Ausgabe: im Trace `path.boundary` (Grund, AS, Gerät/Knoten, Interface, Next-Hop, Text in der
   Sprache der Anfrage) und `hop.boundary` am letzten Hop; in der Topologie `boundary` an der Kante
   nach aussen, an Router bzw. Anbindung aussen (Gründe, AS) und `at_boundary` am gelesenen
   Grenzknoten, `summary.boundary` (aussen/innen). Oberfläche: Weg-Suche „Firmengrenze · AS …“ am
   Weg, am letzten Hop und in der Übersicht; Topologie mit Zähler, Filter „Firmengrenze“, Chip und
   Markierung der Zeile.
9. **Kunden:** Kunden-Benutzer erreichen wie bisher keine Routing-Endpunkte (Freigabeliste). Mit
   Kunde oben (`customer_infra`) sind Router-Kontexte **immer geteilt** und gehören zum Ausschnitt,
   wenn sie ein Netz des Kunden routen (Route ausser der Default-Route); im geteilten Kontext nur
   die Routen zu seinen Netzen und die Default-Route. Die Kunden-Doku nennt Router „Router“.

## Grenzen

- Nur RIB/BGP-Sicht: keine ACLs, keine Policy-Routen (PBR), kein VRF-Leaking-Detail, kein
  NAT der Router – der Weg geht am Router nach der Routing-Tabelle weiter.
- IOS: BGP (Nachbarn, Pfade) und IPv6 nur der globalen Tabelle; VRFs nur in der RIB
  (`vrf *`). NX-OS: keine BGP-Pfade (nur die RIB), keine IPv6-BGP-Nachbarn.
- HSRP/VRRP-Adressen der IOS-Geräte stehen nicht in der RIB: zeigt eine Firewall auf die
  virtuelle Adresse, endet der Weg dort (offener Punkt: `show standby brief`/`show vrrp brief`).
- Die Firmengrenze ist abgeleitet: ein nicht gelesener eigener Router mit privatem AS per eBGP
  gilt als Grenze (`private_as` macht das sichtbar); eigene AS sind (noch) nicht pflegbar.
- Fullfeed-Router: nur ein Ausschnitt der BGP-Routen (Obergrenze); Ziele ausserhalb fallen auf
  die Default-Route bzw. „keine Route bekannt – nicht (ganz) lesbar“. Junos und Arista liefern
  JSON: über 32 MB je Befehl ist die Tabelle ganz „nicht lesbar“ (offener Punkt: je Tabelle bzw.
  nur aktive Pfade lesen, z. B. `show route active-path`).

## Offene Punkte

- Eigene AS-Nummern bzw. Partner-AS pflegbar machen (Grenze von Hand bestätigen/ablehnen).
- HSRP/VRRP/GLBP der IOS-Geräte, BGP je VRF (`show ip bgp vpnv4 all summary`), NX-OS-BGP-Pfade.
- Aruba CX und Business-CLI (L3-Modus) mit Routing; FortiGate-`local_as` aus `router/bgp`.
- Grenze im Zonenplan bzw. als Zone „ausserhalb“ und in der Applikations-Doku (Verbindungen).

## Konsequenzen

- Wege enden an der Firmengrenze statt am ersten nicht gelesenen Router; die Topologie zeigt die
  Grenzknoten. Mehr Knoten in Weg, Topologie und Zonenplan (Router ohne Zone).
- Mehr Befehle je Gerät (bis fünf, auf L2-Switches einer) und grössere Läufe bei Routern mit vielen
  Routen (Obergrenze, längere Wartezeit).
- Keine Schemaänderung: Kontext-Art `l3` passt in `topo_context.kind`.
