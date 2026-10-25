# ADR 0025 — Cisco-Switches lesen (SSH, strikt lesend) und Verkabelung aus CDP/LLDP

## Status

Entwurf (2026-10-25), umgesetzt in 0.62.0. Nächster Schritt der ROADMAP nach Zonen/Kunden und
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

## Konsequenzen

- Neuer Lese-Zugang über SSH. Anders als bei REST schützt keine HTTP-Methode; die
  Positivliste und das Read-only-Konto tragen die Garantie (Risiko R22).
- Neue Abhängigkeiten des Collectors: paramiko, textfsm, ntc-templates. Agenten laden sie beim
  Selbst-Update wie die übrigen (ADR 0011).
- Nach NetBox wird noch nichts geschrieben. Kabel und Ports nach NetBox (`dcim/cables`,
  `dcim/interfaces`) folgen bei Bedarf als weiterer Abschnitt des NetBox-Syncs mit eigener
  Freigabe – nie auf die Switches.
- Offen bzw. später:
  - **SNMPv3** (nur lesend, `authPriv`) als Alternative, wo SSH nicht erlaubt ist.
  - MAC-Adress-Tabelle und ARP (welcher Server an welchem Port), mit Frist wie gelernte
    Endpunkte.
  - L2-Sicht in der Topologie (Switches als Knoten, VLAN-Wege, Redundanz von Uplinks und
    vPC-Paaren) und im Weg Hop für Hop.
  - Weitere Hersteller (z. B. Aruba, Juniper) über dieselbe Struktur (Positivliste,
    Vorlagen).
