# ADR 0019 — Topologie und Pfade: Regeln nur zeigen, wo der Verkehr durchkommt

## Status

**Entwurf** (2026-10-22) – zur Durchsicht durch den Anwender. Setzt den Topology Service
aus ADR 0002 und das Epic E7 (ROADMAP) um; baut auf ADR 0018 (Umgebung und Kunden) auf.

**Stufe 1 (VDOM-Kontext) umgesetzt in 0.15.0** und gegen die echten VDOMs getestet
(2026-10-22). **Stufe 2 (Routing, HA, L3Out lesen) umgesetzt in 0.16.0** und gegen die echten
Systeme getestet (Dev-Befunde in 0.17.0 umgesetzt). **Stufe 3, erste Fassung (Weg Hop für Hop
mit Startpunkt) in 0.17.0;** Regel-Relevanz und „Internet“ zusammengefasst folgen.

Rahmen laut Anwender (2026-10-22):

- Routing lesen ist freigegeben (nur lesend).
- Es gibt VDOMs mit eigenen Interfaces (Einlesen noch nicht getestet), HA-Cluster und zwei
  HA-Cluster, deren Sessions synchronisiert werden.
- Mehrere Firewalls stehen in Serie; das ist über Zonen und Interfaces erkennbar.
- BGP mit Filtern und ECMP ist aktiv. Im ACI hängen immer vier Border-Leafs an den
  Umsystemen.

## Kontext

- Die Doku zeigt heute jede Regel, deren Quelle bzw. Ziel eine IP abdeckt, ohne zu prüfen,
  ob der Verkehr diese Firewall überhaupt passiert. Dadurch erscheinen routingtechnisch
  unmögliche Zugriffe, und `any` aus dem Internet wirkt wie eine Liste aller Gegenstellen.
- Gelesen werden heute: Interfaces mit IPs und Zonen, ACI-BD/EPG-Subnetze, L3Outs mit
  externen EPGs und deren Subnetzen.
- Nicht gelesen werden: Routing-Tabellen, BGP, HA-Status, L3Out-Knoten und -Pfade, Routen
  der Border-Leafs.
- Der FortiGate-Collector fragt heute ohne `vdom`-Parameter ab und liest damit nur den
  Standard-VDOM des Kontos. Bei mehreren VDOMs fehlen die übrigen ganz.

## Entscheidung

1. **Die Topologie wird abgeleitet** aus gelesenen Daten. Der Designer bestätigt, korrigiert
   und ergänzt sie. Eine Zeichnung ist nur eine Ansicht des Modells.
2. **VDOM als Kontext:**
   - Ein FortiGate-Quellsystem liest die Liste der VDOMs und danach jeden VDOM einzeln
     (REST-API mit `vdom=`).
   - Policies, Objekte, Interfaces, Zonen, VIPs und Routen tragen den VDOM. Policy-ID und
     Objektname sind je VDOM eindeutig; gleiche Namen in zwei VDOMs sind zwei Objekte.
   - Inter-VDOM-Links werden Kanten der Topologie.
   - Das Konto braucht ein Read-only-Profil mit Zugriff auf alle VDOMs. Das testen wir
     zuerst gegen die echten VDOMs.
3. **Zusätzlich gelesen** (nur lesend; genaue Endpunkte bzw. Klassen werden je Version
   geprüft):
   - FortiGate je VDOM:
     - die wirksame Routing-Tabelle IPv4/IPv6 aus dem Monitor-API – BGP nach allen
       Filtern, ECMP als mehrere Next-Hops;
     - die BGP-Tabelle mit allen angenommenen Pfaden, soweit verfügbar, damit
       Backup-Wege sichtbar sind, die gerade nicht aktiv sind;
     - BGP-Nachbarn mit Status, statische Routen, Policy-Routes und SD-WAN-Regeln;
     - HA-Status (Cluster, Mitglieder, Rolle) und die Session-Sync-Konfiguration.
   - ACI:
     - L3Out-Knoten und -Pfade (Border-Leafs, Router-IDs, Interfaces bzw. SVIs mit IPs und
       Encap), BGP-Peers und statische Routen;
     - die Routing-Tabelle je VRF auf den Border-Leafs.
   - Fehlt ein Teil, bleibt die betroffene Aussage „unbekannt“ statt falsch.
4. **Modell:**
   - **Knoten** sind Routing-Instanzen:
     - ein FortiGate-VDOM eines HA-Clusters (die Mitglieder sind eine Eigenschaft);
     - der Rand einer ACI-VRF (die Border-Leafs sind eine Eigenschaft – vier Anbindungen
       sind eine Kante mit vierfachem Pfad, nicht vier Wege in der Doku);
     - externe Knoten: Internet, WAN/MPLS, Partner, nicht gelesene Router.
   - **Kanten:**
     - L3-Nachbarschaft über ein gemeinsames Transfernetz (Interface-IP der Firewall im
       Subnetz einer L3Out-SVI bzw. eines anderen Firewall-Interfaces);
     - Inter-VDOM-Links und BGP-Sessions.
     - Herkunft: abgeleitet, bestätigt oder von Hand; dazu die ECMP-Breite.
   - **Redundanz:** Zwei per Session-Sync gekoppelte HA-Cluster bilden ein redundantes Paar
     und zählen als ein Hop. Weichen die Regeln der beiden voneinander ab, entsteht ein
     Befund.
   - **Routen** werden je Lauf gespeichert (Knoten, Präfix, Next-Hops mit IP und Interface,
     Protokoll); der Verlauf ist begrenzt.
5. **Pfade:**
   - Start ist der Knoten, an dem das Quellnetz angeschlossen ist. Von dort geht es Hop für
     Hop per längstem Präfix in der Routing-Tabelle des Knotens; der Next-Hop führt über die
     Kante zum Nachbarknoten.
   - ECMP verzweigt; ein redundantes Paar ist ein Hop; es gibt einen Schleifenschutz und eine
     Hop-Grenze.
   - Ein VIP (DNAT) auf dem Weg ändert das Ziel: Weiter geht es zur internen IP.
   - Ergebnis je Quelle und Ziel: die Firewall-Kontexte auf dem Weg mit Ein- und
     Ausgangs-Interface bzw. -Zone. Die Default-Route Richtung extern endet im Knoten
     „Internet“.
6. **Relevanz einer Regel** für ein System bzw. einen Kunden:
   - *möglich:* Ihr Firewall-Kontext liegt auf einem Pfad, und ihre Quell- bzw. Ziel-Zone
     (Interface) passt zu Ein- bzw. Ausgang.
   - *nicht möglich:* Kein Pfad führt über sie.
   - *unbekannt:* Daten fehlen.
   - Die Doku blendet „nicht möglich“ standardmässig aus; einblendbar mit Begründung.
   - Eine Gegenstelle `any` erscheint als das, was hinter dem Ausgang liegt: „Internet“ bzw.
     die Netze hinter dem Interface.
   - Die Relevanz ist abgeleiteter Zustand wie der Regelindex: je Lauf neu berechnet, mit
     Zwischenspeicher je Paar aus Quellnetz und Zielnetz.
7. **Designer** (Oberfläche):
   - Knoten und Kanten mit Herkunft zeigen, bestätigen oder korrigieren, externe Knoten
     benennen, fehlende Wege ergänzen, Verbindungen Kunden zuordnen (ADR 0018).
   - Korrekturen werden in Automato gespeichert, nicht in NetBox. Ein Export nach NetBox
     (Kabel, Verbindungen) wäre ein neuer Schreibweg und braucht eine eigene ADR und
     Freigabe.
8. **Zeichnung** (z. B. draw.io, optional) kommt erst danach.
9. **Vorgesehen für später** (Anwender, 2026-10-22): normale Switches und weitere Geräte
   erfassen und ihre **Verkabelung** dokumentieren.
   - Das Modell trennt darum die L3-Sicht (Routing-Instanzen, Transfernetze) von einer
     L1/L2-Sicht: Geräte, Ports, Kabel bzw. Verbindungen zwischen Ports, LAGs.
   - Quellen: gelesen (LLDP/CDP-Nachbarn, die wir bei ACI und FortiGate teilweise schon
     haben; weitere Plattformen als Collector nach demselben Lesevertrag) und von Hand im
     Designer.
   - Ob die Verkabelung auch nach NetBox (Kabel) geht, ist ein neuer Schreibumfang und wird
     dann eigens freigegeben.

## Konsequenzen

- Die Doku zeigt nur noch Zugriffe, die über das Netz möglich sind; Zugriffe aus dem
  Internet erscheinen als ein Eintrag.
- Der VDOM-Kontext ändert Schlüssel (Policy-ID, Objektname): Das braucht eine Migration,
  und der Regelindex wird neu aufgebaut.
- Mehr gelesene Daten (Routing, Peers) machen die Datenbank sensibler (R14). Dem begegnen
  wir mit einer Instanz je Umgebung (ADR 0018) und den Massnahmen in RISKS.
- **Grenzen:**
  - Dynamische Routen sind eine Momentaufnahme des Laufs.
  - Failover-Wege, die gerade nicht aktiv sind, kennt nur die BGP-Tabelle; fehlt sie,
    ergänzt der Designer.
  - Asymmetrisches Routing, Policy-Routing und SD-WAN gelten nur, soweit sie gelesen
    werden; sonst ist die Aussage „unbekannt“.

## Umsetzung (Stufen)

1. **VDOM-Kontext:** Collector, Datenmodell und Migration; Test gegen die echten VDOMs.
   Umgesetzt in 0.15.0 (Alembic `0037`):
   - Der Collector liest `system/vdom` und danach jede Tabelle je VDOM; ein VDOM ohne
     Leserecht steht in der Lauf-Meldung.
   - `vdom` an Regel, Objekt, NAT, Interface und Subnetz; Objekte eindeutig je
     Quellsystem/VDOM/Kategorie/Familie/Name; Paare und Entscheidungen je VDOM.
   - Zonen bleiben Namen; eine Zone gilt im VDOM ihrer Regel bzw. ihres Interfaces.
   - Inter-VDOM-Links (`vdom-link`-Interfaces) sind mit ihrem VDOM gelesen; als Kanten folgen
     sie mit Stufe 2.
2. **Routing, HA und L3Out lesen** (FortiGate je VDOM, ACI-Border-Leafs). Umgesetzt in
   0.16.0 (Alembic `0038`, Lesevertrag Abschnitt 1 und 2):
   - FortiGate je VDOM: wirksame Tabelle (ECMP), alle BGP-Pfade (auch Backup), BGP-Nachbarn,
     statische Routen (auch inaktive), Policy-Routen, SD-WAN-Regeln; global HA-Cluster mit
     Mitgliedern und Rolle sowie Session-Sync (FGSP). Felder per Positivliste, keine Secrets.
   - ACI: L3Outs aus dem Tenant-Baum (Border-Leafs, Router-ID, Interfaces/SVIs mit vPC-Seiten,
     Encap, BGP-Peers, statische Routen) und je Border-Leaf und VRF die Routing-Tabelle und die
     BGP-Nachbarn.
   - Je Kontext (VDOM bzw. VRF auf dem Leaf) wird festgehalten, was nicht lesbar war; die
     Aussage ist dort „unbekannt“.
   - Speicher: `topo_context`, `topo_route` (mit Bereich für das längste Präfix), `topo_peer`,
     `topo_ha`, `topo_policy_route`, `topo_l3out` – je Lauf ersetzt. Ein Verlauf der Routen
     (Punkt 4) folgt bei Bedarf.
   - Ansicht „Routing & Wege“ mit „Weg für IP“: je Kontext die wirksame Route und die
     Policy-Routen bzw. SD-WAN-Regeln, die vorgehen. Das ist noch kein Pfad über mehrere Hops
     (Stufe 3).
3. **Pfade und Relevanz** in Regeln und Applikations-Doku, „Internet“ zusammengefasst.
   Erste Fassung in 0.17.0 (`store/paths.py`, `GET /routing/trace`):
   - Knoten: FortiGate-VDOM, bei einer FortiGate nur mit `root` die Firewall selbst; ACI-VRF
     über alle Border-Leafs zusammen.
   - Start: gewählter Knoten bzw. der, an dem die Quelle angeschlossen ist (RIB `connected`,
     sonst EPG-/BD-Subnetz einer gelesenen VRF).
   - Je Hop:
     - Eine Policy-Route bzw. SD-WAN-Regel, die sicher passt (Subnetze, Eingangs-Interface),
       geht vor. Über Objekte bestimmte ist „möglich“; der Weg folgt dann der Tabelle.
     - Sonst gilt das längste Präfix mit kleinster Distanz; die Next-Hops aller
       Border-Leafs werden zusammengefasst, ECMP verzweigt.
   - Nächster Knoten: Besitzer der Next-Hop-Adresse (Interface-IP inkl. Sekundär-IPs, L3Out-SVI
     inkl. vPC-Seiten). Ein gemeinsames Transfernetz reicht nicht (0.17.1). Dort hängt meist ein
     Aussenrouter; das Raten ergab bei zwei Firewalls am selben Router Schleifen. Der Weg
     endet dann. Anbindungen nach aussen werden mit 0.18.0 erfassbar.
   - VRF (0.17.1): je VDOM gilt die Tabelle der VRF des Eingangs (aus den angeschlossenen
     Netzen des Interfaces), ohne Eingang die VRF 0. Management-Interfaces (`dedicated-to`)
     sind eine eigene Tabelle.
   - Ende: angeschlossen, im Fabric (EPG/BD der VRF), extern (Default ohne gelesenen
     Nachbarn), unbekannter Next-Hop, keine Route, Schleife, mehr als 12 Hops; höchstens 16
     Wege.
   - IPs mit Namen aus dem IPAM (sonst das IPAM-Netz mit Beschreibung).
   - **Redundante Wege (0.19.0):**
     - Aktive Wege sind ECMP bzw. „redundant über Verbund“, wenn sie sich nur in Firewalls
       desselben Verbunds unterscheiden (ADR 0020).
     - Backup-Wege: je Weg eine Abweichung an einem Hop über einen BGP-Pfad bzw. eine
       eingeschaltete statische Route für dasselbe Präfix mit anderem Next-Hop.
     - Gezeigt werden Backup-Wege, die ein Ziel erreichen und höchstens `slack` Hops (Standard
       1) länger sind als der kürzeste aktive („gleiche oder fast gleiche Länge“).
     - Mehrere gleichzeitige Ausfälle und das Abwägen nach BGP-Attributen sind nicht
       abgebildet.
     - Länge im Vergleich (0.20.0): je Weg „gleich lang“ bzw. „+n Hops“ gegenüber dem
       kürzesten Weg, der ein Ziel erreicht.
   - Offen: Session-Sync-Paar als ein Hop, Inter-VDOM-Links als Kanten, „Internet“
     zusammengefasst, Regel-Relevanz je Hop (Zonen-Paar → Regeln), NAT auf dem Weg.
4. **Designer-Oberfläche:** Modell, Korrekturen, Verbindungen je Kunde. Erster Teil in
   0.18.0: **Anbindungen nach aussen** (`topo_exit`, von Hand erfasst, Operator):
   - Art (Internet, MPLS, WAN, Partner), optional für eine Firewall, Next-Hop-IPs und/oder
     Interfaces/Zonen, Netze dahinter, **manuelle Routen** (Präfix → gelesener Knoten).
   - Im Weg: Gehört der Next-Hop keinem gelesenen Gerät und passt eine Anbindung (zuerst über
     die Next-Hop-IP, dann über Interface/Zone), endet der Weg dort. Sonst geht er nach der
     manuellen Route weiter, z. B. über MPLS zur Firewall eines anderen Standorts.
   - Liegt das Ziel nicht in den Netzen der Anbindung, sagt das der Text am Ende.
   - Aus einem Weg, der an einem unbekannten Next-Hop endet, lässt sich die Anbindung
     vorausgefüllt erfassen. Änderungen stehen im Audit (`routing.exit.*`).
   - **Erkannte Anbindungen (0.20.0, Alembic `0041`, `store/exits.py`):**
     - Router ohne gelesenes Gerät werden aus den gelesenen Daten erkannt: Next-Hops aus RIB,
       BGP-Pfaden und eingeschalteten statischen Routen sowie BGP-Nachbarn, ohne die
       Management-Tabelle.
     - Je Router: aktiv, Backup oder nur Nachbar, dazu AS/Zustand und Interface/Zone je
       Firewall.
     - Zusammengefasst über gemeinsame Router, ECMP derselben Route, Interface bzw. Zone und
       BGP-AS – je Firewall bzw. Verbund und Session-Sync-Partner (ADR 0020).
     - Vorschläge (`GET /routing/exits/suggestions`) übernimmt der Operator mit allen Routern
       und Firewalls bzw. ergänzt damit eine Anbindung, die schon einen Teil abdeckt.
     - Eine Anbindung gilt für eine oder mehrere Firewalls (`source_system_ids`, leer =
       alle). Ihr Zustand zeigt die erkannten Router, die sie abdeckt – auch nicht
       eingetragene.
     - Wege tragen `extra`: Hops mehr als der kürzeste Weg mit Ziel.
   - Später: Aussenrouter selbst lesen (nur lesend).
5. **Zeichnung** (optional).
6. **Später:** Switches und weitere Geräte, Verkabelung (L1/L2-Sicht, siehe Punkt 9).
