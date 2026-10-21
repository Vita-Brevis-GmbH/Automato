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
   über den Agenten mit einem vCenter-Konto der Rolle **Read-only** (global, vererbt).
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
