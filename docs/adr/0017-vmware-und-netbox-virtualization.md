# ADR 0017 — VMware vCenter lesen und VMs nach NetBox Virtualization

## Status

Angenommen (2026-10-21). Ergänzt ADR 0010 (Inventar, Abgleich, Schreiben nur nach NetBox)
und ADR 0008/0009 (Agent, Zugangsdaten). Freigabe des Anwenders für den zusätzlichen
NetBox-Schreibumfang: „Zusätzlich nach NetBox“ (2026-10-21).

## Kontext

Firewall-Regeln und ACI-Contracts nennen IPs, Netze und EPGs. Welche **VM** dahinter steht,
an welcher **Portgruppe** sie hängt und welcher **EPG** diese entspricht, weiss nur das
vCenter. Damit lassen sich Regeln, Objekte, Applikationen und der IPAM-Abgleich
vervollständigen.

- Portgruppen der ACI-VMM-Integration heissen `<Tenant>|<AP>|<EPG>`, z. B.
  `TE_x|AP_007|EG_dmz02` (Tenant `TE_x`, Applikation 007, EPG `dmz02`).
- Andere Portgruppen lassen sich nur über das VLAN einer EPG zuordnen.
- VMs tragen im Namen das Applikationsprofil und den Tenant, keinen Standort (ADR 0015).
- NetBox soll die VMs ebenfalls dokumentieren (Cluster, VMs, Interfaces, IPs).

## Entscheidung

1. **Neue Plattform `vmware`** (Quellsystem mit Benutzer/Passwort, ADR 0009). Gelesen wird
   über den Agenten mit einem vCenter-Konto der Rolle **Read-only** – auf dem vCenter-Objekt
   (oberste Ebene) oder als globale Berechtigung, jeweils **an untergeordnete Objekte
   weitergegeben**. Der Lauf liest ab dem Wurzelordner und braucht dort `System.View`.
2. **Nur lesen, auch im Code:**
   - Bibliothek pyVmomi (Apache-2.0); gelesen wird mit dem PropertyCollector.
   - SOAP kennt nur POST, deshalb schützt keine Methoden-Prüfung wie bei den REST-Clients.
     Stattdessen lässt der Collector nur lesende vSphere-Methoden zu: Anmelden, Abmelden,
     Service-Content, Container-View anlegen/entfernen, Eigenschaften abrufen.
   - Jeder andere Aufruf bricht **vor dem Senden** mit `ReadOnlyViolation` ab.
   - Container-Views sind sitzungsgebunden und ändern am vCenter nichts.
3. **Gelesen werden:**
   - VMs: Name, UUID, Status, Gast-OS, Hostname, vCPU, RAM, Disk, Notiz, Vorlage ja/nein,
     NICs mit MAC, Portgruppe und IPs.
   - Hosts, Cluster und Datacenter.
   - Portgruppen (verteilt: VLAN/Trunk/PVLAN und Switch; Standard: VLAN je Host).
   - Keine Zugangsdaten, keine Gast-Inhalte, keine Leistungsdaten.
4. **Zuordnung Portgruppe → EPG** im Orchestrator, in dieser Reihenfolge:
   - Name `Tenant|AP|EPG` → EPG-DN (`~` als Trennzeichen geht auch). Passen die Namen nicht
     exakt, gilt der Vergleich ohne Präfixe (`TE_`, `AP_`, `EG_`).
   - Sonst das VLAN der Portgruppe, wenn genau eine EPG dieses Encap-VLAN hat.
   - Die Art der Zuordnung wird festgehalten (Name | VLAN).
5. **Nutzen in Automato:**
   - IPs in Regeln und Objekten zeigen die VM dahinter.
   - Die Regelsuche findet VM-Namen.
   - EPGs und die Doku je System nennen ihre VMs. Ein Server bringt in seiner Doku die
     Contracts der EPG seiner Portgruppe mit, auch ohne gelernten Endpunkt.
   - Befunde: VM-IP fehlt im IPAM; Portgruppe nach ACI-Schema ohne passende EPG.
6. **NetBox-Sync, neuer Abschnitt `virtualization`:**
   - Neue Objektarten: Cluster-Typ `VMware vSphere`, Cluster-Gruppe je Datacenter, Cluster
     (mit Tenant), VM (Status, vCPU, RAM, Disk, Kommentar mit Gast-OS, Host und EPG),
     VM-Interface (Beschreibung mit MAC, Portgruppe, VLAN und EPG) sowie die IPs der VM am
     Interface.
   - Ablauf wie bisher: Vorschau → Übernehmen mit Fingerabdruck. Geändert oder gelöscht wird
     nur, was das Tag `automato` trägt.
   - Von Hand gepflegte IPs (NetBox als IPAM) werden nur referenziert. Weicht allein die
     Zuordnung zum VM-Interface ab, ist das ein Konflikt; Adresse, Status und Beschreibung
     zählen dort nicht.
   - Ohne den Abschnitt liest der Sync die Virtualisierungs-Objekte nicht; VMs werden dann
     weder geändert noch als veraltet geführt.
   - Löschen nur mit `allow_delete`.
   - Der Sync-Benutzer braucht die Rechte für `virtualization.*` (Cluster-Typ, -Gruppe,
     Cluster, VM, VM-Interface).
   - Am Grundsatz ändert sich nichts: geschrieben wird nur nach NetBox, nie ins vCenter.

## Konsequenzen

- Neuer Lese-Zugang. Das vCenter-Konto muss in vSphere auf **Read-only** stehen. Der
  Methoden-Schutz im Code ist die zweite Linie.
- pyVmomi ist eine neue Abhängigkeit des Collectors.
  - Agenten laden sie beim Selbst-Update wie die übrigen Abhängigkeiten (ADR 0011).
  - Fehlt sie, laufen alle anderen Collectors weiter. Nur `vmware` meldet einen klaren
    Fehler.
- Die MAC-Adresse steht vorerst in der Interface-Beschreibung. Eigene MAC-Objekte
  (`dcim/mac-addresses`) folgen bei Bedarf.
- Eine primäre IP der VM wird nicht gesetzt (erfordert einen zweiten Schritt nach dem
  Anlegen der IPs).
- Proxmox folgt später mit demselben Modell (VM, Portgruppe/Bridge).

## Nachtrag 1.8.0 – ESXi-Hosts mit vmnics und Nachbarn (CDP/LLDP)

Anwender: „Wird in VMware die Ports und Nachbarschaft nicht ausgelesen von den Hosts?“ – und
„Nur wegen den Verbindungen“. Der Kunde hat Switches und ein vCenter, aber kein ACI; in der
Verkabelung fehlten die Verbindungen Switch-Port ↔ vmnic. ESXi hört CDP standardmässig nur zu
(sendet nicht): der Switch sieht den Host meist nicht, der Host aber den Switch.

- **Gelesen wird zusätzlich je Host** (PropertyCollector, wie bisher nur Eigenschaften):
  Hardware (`hardware.systemInfo`: Hersteller, Modell, Seriennummer – ab vSphere 6.7
  `serialNumber`, sonst Service-Tag aus den Kennungen), Version (`config.product.version`),
  Verwaltungs-Adresse (IPv4 der VMkernel-NIC an „Management …“, sonst `vmk0`; die
  Management-Rolle selbst wird nicht abgefragt), Zustand der Verbindung
  (`runtime.connectionState`), die physischen NICs (`config.network.pnic`: vmnic, MAC, Speed,
  Duplex) und die Referenz `configManager.networkSystem`.
- **Nachbarn je vmnic**: je verbundenem Host einmal `HostNetworkSystem.QueryNetworkHint()` ohne
  Argument (alle vmnics). Ergebnis je vmnic: CDP (`connectedSwitchPort`: Geräte-ID, Port,
  Management-Adresse, Plattform, Fähigkeiten) bzw. LLDP (`lldpInfo`: Chassis-ID, Port-ID,
  Parameter wie „System Name“, „Management Address“, „System Description“). CDP geht vor; ist
  die LLDP-Port-ID eine MAC, gilt die Port-Beschreibung.
- **Warum lesend**: Die Methode liefert, was der Host je vmnic an CDP/LLDP-Paketen und Netzen
  beobachtet hat; sie ändert nichts am Host. Laut vSphere-API (in pyVmomi `privId` der Methode)
  braucht sie nur das Recht **System.Read** – Teil der Rolle **Read-only**. Sie steht deshalb in
  der Liste der erlaubten Methoden (`READ_METHODS`); jede andere Methode des Netzwerk-Systems
  (z. B. `UpdateNetworkConfig`, `RefreshNetworkSystem`) bleibt vor dem Senden blockiert.
- **Ohne Recht bzw. bei Fehlern** bricht der Lauf nicht ab: Der Host kommt ohne Nachbarn ins
  Inventar, der Lauf meldet „Hinweise: <Host>: CDP/LLDP nicht lesbar (<Grund>)“ (z. B.
  `NoPermission`, `disconnected`, `TimeoutError`). Getrennte Hosts werden nicht gefragt. Nach
  drei Verbindungsfehlern in Folge (keine vSphere-Meldung, z. B. Zeitüberschreitung) fragt der
  Lauf die übrigen Hosts nicht mehr („CDP/LLDP von n weiteren Hosts nicht abgefragt“) – sonst
  wartete er bei einem hängenden vCenter je Host bis zum Timeout.
- **Übergabe**: `VirtualInventory.hosts` (Name, Hersteller, Modell, Seriennummer, Version,
  Verwaltungs-Adresse, vmnics mit Speed im Stil der Switches – „1000“, „10G“ – und Nachbar).
  Fehlt das Feld (älterer Agent), bleiben die Hosts im Inventar unangetastet; eine leere Liste
  heisst „keine Hosts mehr“.
- **Speicherung im Netzinventar des vCenters** (wie die Switches, ADR 0010/0025): Host =
  `inv_device` mit Rolle `esxi` und neuer Spalte `vendor` (Migration 0080, für den Gerätetyp im
  NetBox-Sync), vmnic = `inv_interface` (physisch, Speed, Status, MAC in `attrs`), Nachbar =
  `inv_neighbor`. Ersetzt werden nur die Hosts des vCenters mit ihren Ports und Nachbarn – VMs,
  Portgruppen und anderes Inventar bleiben. Für den Datenstand (ADR 0049) ist der Teil
  `virtual.hosts` ohne Wirkung auf Doku und Wege.
- **Verkabelung**: siehe ADR 0025 (Nachtrag 1.8.0). Geschrieben wird weiterhin nur nach NetBox,
  nie ins vCenter und nie auf einen Host.
