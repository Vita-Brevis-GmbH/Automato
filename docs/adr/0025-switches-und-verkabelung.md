# ADR 0025 — Cisco-Switches lesen (SSH, strikt lesend) und Verkabelung aus CDP/LLDP

## Status

Entwurf (2026-10-25), umgesetzt in 0.62.0, 0.63.0 (Endpunkte aus MAC-Tabelle und ARP,
Nr. 8), 0.64.0 (L2-Sicht: Plan, Ausfall, Redundanz, VLAN-Wege, L2 in der Weg-Suche, Nr. 9) und
0.65.0 (vPC-Paare, Spanning Tree, mehrere Ausfälle, Export des Plans, Nr. 10), 0.68.0 (weitere
Produkte: Catalyst 1200/1300, Juniper MX/EX/QFX, Nr. 11).
Nächster Schritt der ROADMAP nach Zonen/Kunden und
Applikationen (Entscheid des Anwenders 2026-10-25: „Switching/Verkabelung“). Ergänzt ADR 0010
(Inventar, Schreiben nur nach NetBox), ADR 0008/0009 (Agent, Zugangsdaten zentral) und ADR 0019
(Topologie: bisher L3, jetzt die L1/L2-Sicht darunter).

## Kontext

Automato kennt Firewalls, das ACI, das vCenter und das IPAM. Die Switches dazwischen fehlen:

- Welcher Port eines Switches führt zu welcher Firewall, welchem Leaf, welchem ESXi-Host?
- Passen beide Seiten einer Verbindung zusammen (Speed, Duplex, VLANs, Port-Channel)?
- Welche VLANs liegen an welchen Switches als Access-Port bzw. im Trunk?

Cisco-Switches (IOS, IOS-XE, NX-OS ausserhalb des ACI) haben keine einheitliche, lesende API:
RESTCONF/NX-API sind nicht überall eingeschaltet, SNMP liefert keine Trunk-Details ohne
herstellerspezifische MIBs. Die Kommandozeile über SSH gibt es überall.

Vorgaben: **strikt lesend** (nur nach NetBox schreiben), Testsysteme sind produktiv, keine
Secrets, Open Source bevorzugt.

## Entscheidung

1. **Neue Plattform `cisco_switch`**, ein Quellsystem = eine **Gruppe von Switches** mit
   gemeinsamem Konto (Benutzer/Passwort zentral, ADR 0009):
   - Die Adressen stehen im Lesebereich (`read_scope`, eine je Zeile, `host` bzw. `host:port`),
     höchstens 1000 je Quellsystem. `base_url` = `ssh://<erste Adresse>` (Verbindung prüfen).
   - Gelesen wird über den Agenten (bzw. den zentralen Worker), bis zu 8 Switches parallel.
     Ein Switch, der nicht antwortet, fehlt im Ergebnis und steht in der Meldung des Laufs; die
     anderen werden eingelesen. Antwortet keiner, schlägt der Lauf fehl.
2. **Nur lesen, auch im Code:**
   - SSH mit paramiko (LGPL-2.1), je Befehl ein **Exec-Aufruf** (wie `ssh switch "show …"`):
     keine interaktive Shell, kein Terminal, kein Konfigurationsmodus, kein `terminal length`.
   - Nur die festen `show`-Befehle der Positivliste (`cisco_switch/client.py`, `COMMANDS`);
     jeder andere Befehl bricht **vor dem Senden** ab (`ReadOnlyViolation`, Test).
   - IOS/IOS-XE: `show version`, `show interfaces status`, `show interfaces description`,
     `show interfaces switchport`, `show vlan brief`, `show etherchannel summary`,
     `show cdp neighbors detail`, `show lldp neighbors detail`.
   - NX-OS: dieselben in NX-OS-Schreibweise (`show interface …`, `show port-channel summary`)
     und `show vpc`. `show version` erkennt die Plattform.
   - Keine `show running-config`: so gelangen weder Passwörter noch Schlüssel noch
     Community-Strings in den Collector. Je Befehl höchstens 32 MB und 120 s.
   - Das Konto braucht nur Leserechte: IOS **privilege 1** (bzw. eine Parser-View mit den
     Befehlen), NX-OS Rolle **`network-operator`**. Das ist die erste Linie, die Positivliste
     die zweite.
3. **Host-Schlüssel festhalten (Trust on First Use):**
   - Beim ersten Lauf übernimmt der Collector den Schlüssel, den der Switch vorweist, und meldet
     ihn mit dem Ergebnis. Der Orchestrator hält ihn je Adresse fest
     (`source_system.ssh_host_keys`, Audit `source.ssh_host_keys_learned`).
   - Danach muss der Switch genau diesen Schlüssel vorweisen; sonst bricht die Verbindung **vor
     der Anmeldung** ab (kein Passwort an ein fremdes Gerät), der Lauf meldet den Switch als
     „Host-Schlüssel geändert“ mit beiden Fingerabdrücken.
   - Ein Operator kann einen Schlüssel im Web-UI vergessen (nach Tausch des Geräts,
     Audit `source.ssh_host_keys_forgotten`). Ein gemeldeter Schlüssel überschreibt nie einen
     festgehaltenen. Fällt eine Adresse aus dem Lesebereich, fällt auch ihr Schlüssel weg.
4. **Auswerten mit ntc-templates** (Apache-2.0, TextFSM) im Collector:
   - Gerät (Name, Modell, Version, Seriennummer, Management-IP), Ports mit Status, Speed,
     Duplex, Beschreibung, Modus (Access/Trunk), Access-VLAN, Native VLAN, erlaubte VLANs,
     Port-Channel, Medium, vPC; VLANs mit Namen; Nachbarn je Port aus CDP und LLDP (Name,
     Port, IP, Plattform, Fähigkeiten).
   - Fehlt eine Vorlage oder meldet ein Befehl einen Fehler (z. B. LLDP aus), bleibt der Teil
     leer und steht als Hinweis im Lauf; der Rest wird eingelesen.
   - Neue Tabellen bzw. Spalten (Migration 0054): `inv_interface` mit Status, Speed, Duplex,
     Native VLAN, erlaubten VLANs und `attrs`; `inv_neighbor` (Nachbarn je Port).
5. **Verkabelung als abgeleiteter Zustand** (`store/cabling.py`), nichts wird von Hand gepflegt:
   - Jede Angabe „an meinem Port P sitzt Gerät G, Port Q“ wird zu einer Verbindung: Switches
     über `inv_neighbor`, ACI-Leafs (LLDP/CDP aus dem APIC) über `inv_interface.neighbor`.
   - Ein Ende ist ein gelesenes Gerät (Name bzw. Management-IP), ein ESXi-Host aus dem vCenter
     oder ein Gerät, das Automato nicht liest. Portnamen werden in Kurzform verglichen
     (`GigabitEthernet1/0/1` = `Gi1/0/1`, `Ethernet1/1` = `Eth1/1`).
   - Zustand: **beidseitig** (beide melden sich), **einseitig** (die Gegenseite wird gelesen und
     meldet Nachbarn, aber diesen nicht) bzw. **Gegenstelle ohne CDP/LLDP** (nicht gelesen).
6. **Befunde nur zwischen zwei gelesenen Switches** (dort sind beide Ports vollständig bekannt),
   im allgemeinen Abgleich, Gegenstand `link`:
   - `link_speed_mismatch` (Warnung), `link_duplex_mismatch` (Fehler),
     `link_vlan_mismatch` (Modus, Access-VLAN, Native VLAN, erlaubte VLANs je Seite; Warnung),
     `link_lag_mismatch` (Port-Channel nur auf einer Seite; Warnung),
     `link_one_sided` (Information).
7. **Ansicht Verkabelung** (Navigation nach Routing) mit Verbindungen, Geräten, Ports und VLANs;
   API `/cabling/{devices,links,ports,vlans,sources}`, Rolle viewer, mandantsgescoped, **nicht
   für Kunden-Benutzer** (Infrastruktur, Freigabeliste bleibt unverändert).

8. **Endpunkte aus MAC-Tabelle und ARP** (0.63.0):
   - Zwei weitere Befehle der Positivliste: `show mac address-table` und ARP (`show ip arp`
     bzw. NX-OS `show ip arp detail vrf all`). Übernommen werden nur Unicast-MACs an einzelnen
     Ports (nicht CPU, Supervisor, Router, vPC-Peer-Link), höchstens 50 000 je Switch; ARP ohne
     die eigenen Adressen des Switches.
   - **Gesammelt statt ersetzt** (`mac_entry`, `arp_entry`, Migration 0055): bekannte
     Einträge bekommen ein neues `last_seen`, neue kommen dazu. Aktuell ist je Switch, was
     sein letzter Lauf gesehen hat; ältere Einträge zeigen, wo ein Gerät früher hing. Nach
     der Frist `AUTOMATO_RETENTION_L2_DAYS` (30 Tage, Aufbewahrung ADR 0022) fallen sie weg.
   - **Anschluss** eines Endpunkts (`store/l2.py`): der Port mit der MAC, der kein Uplink ist.
     Uplink = an dem Port bzw. einem Mitglied seines Port-Channels hängt laut CDP/LLDP ein
     weiterleitendes Gerät (Switch, Leaf, Spine, meldet Switch/Bridge/Router; nicht ESXi-Host,
     Firewall, Telefon). Steht die MAC nur auf Uplinks, hängt das Gerät hinter einem Gerät
     ohne MAC-Tabelle in Automato – gezeigt wird die Gegenstelle.
   - **Identität**: VM-NIC aus dem vCenter (MAC), IPs aus ARP, VMware Tools und
     ACI-Endpunkten, Namen aus dem IPAM.
   - **„Wo hängt …“** (`GET /cabling/locate`): MAC, IP oder Name (VM, IPAM) → Anschluss mit
     VLAN, Gegenstelle am Port (z. B. ESXi-Host mit vmnic), früher gesehene Ports.
   - **Befund `uplink_vlan_missing`**: Die Ports, an denen VMs eines ESXi-Hosts gesehen werden,
     sind seine Uplinks für diesen vSwitch/DVS. Jedes VLAN, das VMs des Clusters auf diesem
     vSwitch/DVS nutzen, muss dort erlaubt sein – sonst verliert eine VM nach vMotion bzw.
     beim Ausfall eines Uplinks das Netz. Nur Ports gelesener Switches mit bekannter
     VLAN-Konfiguration; ESXi-Hosts müssen dafür kein CDP senden.
   - Wie die übrigen Daten der Verkabelung nicht für Kunden-Benutzer.
9. **L2-Sicht** (0.64.0, `store/l2_topo.py`), abgeleitet aus der Verkabelung, nichts gepflegt:
   - **Graph**: Knoten = Geräte, Kante = alle Kabel zwischen zwei Geräten (Port-Channel bzw.
     mehrere Kabel = eine Kante mit mehreren Links). Durch weiterleitende Geräte (Switch,
     Leaf, Spine, Switch-/Router-Fähigkeit) führt ein Weg, durch Endgeräte (ESXi, Firewall,
     Telefon) nicht – ein ESXi-Host an zwei Switches verbindet sie nicht.
   - **Plan** (`GET /cabling/plan`): Ebenen ab dem Kern (Spines bzw. die meistvernetzten
     Switches je Teilnetz), darunter je Abstand; Endgeräte unter ihrem obersten Nachbarn;
     innerhalb der Ebene nach den Nachbarn darüber sortiert. Mit `vlan` je Kante ja/nein/
     teilweise/unbekannt und die Inseln des VLANs.
   - **Ausfall** (`GET /cabling/failure`): ohne ein Gerät bzw. ein Kabel – welche Teile
     entstehen, was ist vom grössten Teil (meiste Switches) getrennt.
   - **Befunde** (Kategorie Verkabelung): `l2_single_link` (Switch bzw. ESXi-Host mit einem
     einzigen Kabel zu weiterleitenden Geräten), `l2_single_switch` (alle Kabel am selben
     Switch; ESXi Warnung, Switch Information), `l2_single_point` (ohne den Switch zerfällt das
     Netz; Information), `lag_degraded` (Port-Channel mit nicht verbundenen Mitgliedern),
     `vlan_split` (VLAN an verbundenen Access-Ports bzw. SVI auf mehreren Switches, die Trunks
     dazwischen sperren es; VLAN 1 und 1002–1005 nicht).
   - **Weg-Suche**: zwischen zwei Hops der Weg über die Switches vom physischen Port des
     Ausgangs (VLAN-Subinterface → Parent, Aggregat → Mitglieder) im VLAN des Subinterfaces bis
     zum nächsten Gerät (`hop.l2`); Anschluss von Quelle und Ziel aus MAC-Tabelle/ARP
     (`l2.src`/`l2.dst`) und der Weg vom letzten Hop bis zum Port des Ziels bzw. von der Quelle
     zum ersten Hop. Nur wo die Kabel bekannt sind; sonst bleibt die Weg-Suche wie bisher.
10. **Verkabelung abgerundet** (0.65.0):
    - **Spanning Tree**: ein weiterer Befehl der Positivliste, `show spanning-tree` (IOS und
      NX-OS; auf NX-OS ohne passende Vorlage mit der IOS-Vorlage ausgewertet). Je Port bzw.
      Port-Channel die VLANs, in denen er blockiert (`BLK` bzw. Rolle Alternate/Backup ohne
      Weiterleitung, `attrs.stp_blocked`), und die Root-Ports (`attrs.stp_root`), als
      VLAN-Bereiche. Keine neue Tabelle.
    - **vPC** genauer aus `show vpc` (eigene Auswertung, die Vorlage kennt nur einen Teil):
      Domäne, Rolle, Peer- und Keepalive-Status am Peer-Link (`attrs.vpc_peer_link` = Domäne,
      `vpc_role`, `vpc_peer`, `vpc_keepalive`, `vpc_peer_link_status`), je vPC Status und
      Konsistenz (`vpc_status`, `vpc_consistency`).
    - **vPC-Paar als logischer Switch**: Paar = Switch mit Peer-Link und das weiterleitende
      Gerät an dessen Mitgliedern (CDP/LLDP). Im Plan auf derselben Ebene nebeneinander mit
      Rahmen „vPC-Domäne n“; hängt ein Gerät nur an einem Peer, nennt der Befund
      `l2_single_switch` den ungenutzten Peer. Befund **`vpc_mismatch`** (Kategorie
      Verkabelung): Peer nicht verbunden (Fehler, mit Keepalive), vPC nur auf einem Peer
      (wenn beide gelesen), vPC down bzw. Konsistenz nicht erfolgreich (Warnung).
    - **Spanning Tree im Plan und im L2-Weg**: Kanten mit blockierten VLANs gestrichelt, mit
      VLAN „Reserve“ (`standby`); der Weg in der Weg-Suche meidet blockierte Kabel. Führt ohne
      sie kein Weg zum Ziel (Läufe zu verschiedenen Zeiten), dann mit ihnen. Die Inseln eines
      VLANs richten sich weiter nur nach der Konfiguration – ein blockiertes Kabel ist Reserve,
      keine Trennung.
    - **Mehrere gleichzeitige Ausfälle**: `GET /cabling/failure` mit `device` bzw. `link`
      mehrfach (höchstens 50 Angaben); im Plan Shift-Klick auf weitere Geräte bzw. Kabel (bei
      mehreren Kabeln zwischen zwei Geräten fallen mit Shift-Klick alle aus).
    - **Export des Plans** (`GET /cabling/plan/export?format=svg|drawio`, `ends`, `vlan`): dieselbe
      Lage wie in der Ansicht mit vPC-Rahmen, Kabelzahl, Port-Channels und STP; draw.io mit
      `automato_device/kind/source` an den Geräten und `automato_ports/links/stp_blocked` an
      den Kabeln, dort weiter bearbeitbar.

11. **Weitere Produkte** (0.68.0, Wunsch des Anwenders: Catalyst 1300, 9200L, 3650, 4500,
    Nexus 9000, Juniper MX). Dasselbe Quellsystem (`cisco_switch`, in der Oberfläche „Switches &
    Router“); die Plattform erkennt der Collector je Gerät an `show version`:
    - **Cisco IOS/IOS-XE** – Catalyst 9200L, 9300, 3650, 4500 (auch klassisches IOS 15 der
      4500 mit eigener Form der MAC-Tabelle) und **NX-OS** – Nexus 9000: wie bisher.
    - **Cisco Business-CLI** – Catalyst 1200/1300 (wie CBS 250/350): eigene Befehlsliste
      (`show version`, `show system`, `show inventory`, `show interfaces status/description`,
      `show vlan`, `show interfaces port-channel`, `show lldp neighbors`,
      `show mac address-table`, `show arp`) und eigene Auswertung der Tabellen (Spalten nach
      der Strichzeile, umgebrochene Zeilen; die ntc-Vorlagen `cisco_s300` kennen keine
      Umbrüche). VLANs der Ports aus `show vlan` (getaggt/ungetaggt). Diese Geräte nehmen
      **kein Exec** an: der Collector liest sie über eine **begrenzte Sitzung** (`_Shell`):
      Terminal mit Prompt, Anmeldung in der Sitzung („User Name:“/„Password:“, nur einmal),
      danach ausschliesslich die Befehle der Positivliste und `terminal datadump` (Seitenumbruch
      nur für diese Sitzung aus, keine Konfiguration); bei „More“ ein Leerzeichen. Nie
      Konfigurationsmodus, keine Antwort auf Rückfragen, Ausgabe wie bisher begrenzt. Die
      Sitzung wird nur genommen, wenn `show version` per Exec nicht antwortet.
    - **Juniper Junos** – MX (Rolle **`router`**), EX/QFX (Rolle `switch`): Befehle mit
      strukturierter Ausgabe `| display json | no-more` (`show chassis hardware`,
      `show interfaces`, `show lldp neighbors`, `show vlans`, `show arp no-resolve`,
      `show ethernet-switching table`, `show bridge mac-table`), Konto der Klasse `read-only`.
      Logische Units mit VLAN-Tag werden VLAN-Interfaces am Port (wie Subinterfaces der
      FortiGate), Unit 0 ohne Tag gehört zum Port; IPv4/IPv6-Adressen kommen ans Interface,
      ihre Netze als Interface-Netze ins Inventar (`inv_subnet`). Aggregat `ae` aus der
      Adressfamilie `aenet`, MAC-Tabelle je nach Plattform (ELS bzw. Bridge-Domains, der
      jeweils andere Befehl fehlt ohne Hinweis). Interne Interfaces (`pfe-`, `cbp`, `lsi` …)
      fallen weg. **Nicht** gelesen: Routing-Tabelle des MX (beim Internet-Router zu gross) –
      die Wege über den MX folgen bei Bedarf mit einer Zusammenfassung.
    - Ein Router leitet in der L2-Sicht nicht weiter (`Device.switching` falsch); Speed und
      Duplex der Hersteller (`10gbps`, `10G`, `10000`, `Full`/`full`) vergleicht die Verkabelung
      einheitlich.
12. **Patchungen im Plan** (0.70.0, Wunsch des Anwenders: „wenn ich ein Gerät auswähle, unten
    gleich die Patchungen der Kabel mit Details“). Im Plan schaltet „Klick: Patchungen |
    Ausfall“ um (Standard Patchungen): Klick auf ein Gerät zeigt darunter seine Kabel, Klick auf
    ein Kabel nur die zwischen den beiden Geräten. `GET /cabling/patches?device=&peer=` (viewer,
    nicht Kunden-Benutzer): je Kabel das eigene Ende links, die Gegenstelle rechts – Port,
    Beschreibung, Speed/Duplex, Access/Trunk mit Native VLAN (ACI aus der VLAN-Liste), Port-
    Channel/vPC (Werte des Channels an den Mitgliedern), Spanning Tree, Medium, EPGs, Status;
    Zustand, Protokolle, Abweichungen und die Endpunkte am eigenen Port; Port-Channel als
    Gruppe. Die Gegenstelle öffnet ihre Patchungen, die Zahl der Endpunkte den Reiter
    Endpunkte. Rein lesend.

## Konsequenzen

- Neuer Lese-Zugang über SSH. Anders als bei REST schützt keine HTTP-Methode; die
  Positivliste und das Read-only-Konto tragen die Garantie (Risiko R22).
- Neue Abhängigkeiten des Collectors: paramiko, textfsm, ntc-templates. Agenten laden sie beim
  Selbst-Update wie die übrigen (ADR 0011).
- MAC- und IP-Adressen sind Betriebsdaten über Geräte und (indirekt) Personen (Clients). Sie
  werden nur für die Verkabelung genutzt, nicht an Kunden-Benutzer gegeben und nach der Frist
  gelöscht (R14, R22).
- Nach NetBox wird noch nichts geschrieben. Kabel und Ports nach NetBox (`dcim/cables`,
  `dcim/interfaces`) folgen bei Bedarf als weiterer Abschnitt des NetBox-Syncs mit eigener
  Freigabe – nie auf die Switches.
- Offen bzw. später:
  - **SNMPv3** (nur lesend, `authPriv`) als Alternative, wo SSH nicht erlaubt ist.
  - ~~MAC-Adress-Tabelle und ARP~~ – umgesetzt in 0.63.0 (Nr. 8). Offen: IPv6-Nachbarn
    (`show ipv6 neighbors`), ARP der VRFs auf IOS (`show ip arp vrf …`), ARP der FortiGate.
  - ~~L2-Sicht~~ – umgesetzt in 0.64.0 (Nr. 9); ~~vPC-Paar, Spanning Tree, mehrere Ausfälle,
    Export des Plans~~ – umgesetzt in 0.65.0 (Nr. 10). Offen: Root-Bridge und Topologie-
    Wechsel je VLAN (`show spanning-tree detail`), StackWise/VSS als ein Gerät.
  - Weitere Hersteller (z. B. Aruba, Juniper) über dieselbe Struktur (Positivliste,
    Vorlagen).
