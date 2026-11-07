# ADR 0033 — SNMPv3 lesend als Alternative zu SSH für Switches & Router

## Status

**Entwurf** (2026-10-27), umgesetzt in 0.100.0 – zur Abnahme durch den Anwender (Wunsch des Anwenders: „SNMPv3 als lesende Alternative zu SSH“, offen
aus ADR 0025). Ergänzt ADR 0025 (Switches strikt lesend über SSH, Verkabelung), ADR 0009
(Zugangsdaten zentral, verschlüsselt) und ADR 0010 (nur nach NetBox schreiben).

## Kontext

Automato liest Switches und Router über SSH mit einer Positivliste von `show`-Befehlen
(ADR 0025). Manche Kunden geben für ihre Netzwerkgeräte kein SSH-Konto heraus – auch kein
lesendes –, erlauben aber **SNMPv3** für das Monitoring. Für die Verkabelung, die Endpunkte und
die L2-Sicht reicht der Inhalt der Standard-MIBs weitgehend: Ports mit Zustand, VLANs je Port,
Nachbarn aus LLDP/CDP, MAC-Tabelle, ARP und IPv6-Nachbarn.

Vorgaben: strikt lesend (Testsysteme sind produktiv), keine Secrets in Logs/Antworten/Audit,
Open Source bevorzugt, Adapter-Pattern – die übrige Plattform darf nicht merken, wie gelesen
wurde.

## Entscheidung

1. **Kein neues Quellsystem, sondern ein Zugang**: „Switches & Router“ (`cisco_switch`) bekommt
   die Einstellung **Zugang: SSH | SNMPv3** (`source_system.access`, Migration 0062; leer bzw.
   `ssh` = wie bisher). Adressen im Lesebereich wie bei SSH (`host` bzw. `host:port`, Standard
   **UDP 161**), ein SNMPv3-Benutzer für alle Geräte der Gruppe, bis zu 8 parallel, ein Gerät
   ohne Antwort = Teilfehler. `base_url` = `snmp://<erste Adresse>`.
2. **Nur SNMPv3 mit authPriv**:
   - Benutzer, **Auth-Protokoll** HMAC-SHA bzw. SHA-2 (`sha`, `sha224`, `sha256` = Vorgabe,
     `sha384`, `sha512`; RFC 3414/7860) mit Passwort, **Priv-Protokoll** AES-CFB (`aes128` =
     Vorgabe, `aes192`, `aes256`; RFC 3826) mit Passwort, optional ein **Context**.
   - **Nicht**: SNMPv1/v2c, Community-Strings, noAuthNoPriv/authNoPriv, MD5, DES/3DES. Passwörter
     mindestens 8 Zeichen (RFC 3414). Geprüft im Orchestrator beim Speichern (422) und im
     Collector **vor dem ersten Paket** (`SnmpConfigError`).
   - AES-192/-256 nutzen die Schlüsselverlängerung wie Cisco (draft-reeder, in pysnmp
     `usmAesCfb192/256Protocol`); bei Problemen mit anderen Herstellern AES-128 nehmen.
3. **Zugangsdaten** wie alle anderen (ADR 0009): verschlüsselt (Fernet) in
   `source_system.credentials` – `username`, `auth_protocol`, `auth_password`, `priv_protocol`,
   `priv_password`, `context`. Nur schreibbar; die API gibt nur `has_credentials` und die
   Protokolle aus (`snmp: {auth_protocol, priv_protocol, has_context}`), nie Benutzer, Passwörter
   oder Context; das Audit nennt nur `credentials_set` und `access`. Der zugewiesene Agent (bzw.
   der zentrale Worker) bekommt sie beim Abholen eines Laufs. Ein **Wechsel des Zugangs verwirft
   die hinterlegten Zugangsdaten** (sie gehören zum anderen Zugang); die SSH-Host-Schlüssel
   bleiben für einen Wechsel zurück erhalten.
4. **Nur lesen, auch im Code** (`collectors/snmp/`):
   - Bibliothek **pysnmp** (BSD-2-Clause, rein Python, ab 7.1 asyncio) – erst beim Verbinden
     geladen; aus pysnmp werden nur einzelne lesende Namen importiert (`get_cmd`, `bulk_cmd`,
     Engine/Transport/USM-Klassen), nie eine SET-Funktion, nie das ganze Modul (Test über den
     Quelltext).
   - Der Client kennt nur **GET** (einzelne Werte) und **GETBULK** (Spalten einer Tabelle);
     jede Abfrage ausserhalb der **Positivliste** (`snmp/oids.py`) und jede Fortsetzung, die
     die Spalte verlässt, bricht **vor dem Senden** ab (`ReadOnlyViolation`) – im Client und noch
     einmal im Transport.
   - Erste Linie bleibt das Gerät: der SNMPv3-Benutzer gehört in eine Gruppe **nur mit
     Read-View** (keine Write-View), die ACL des SNMP-Agenten lässt nur den Agenten zu.
5. **Positivliste** (Standard-MIBs; je Spalte höchstens 20 000 Zeilen und 120 s, MAC-Tabelle wie
   bei SSH höchstens 50 000 Einträge):
   - SNMPv2-MIB: `sysDescr`, `sysObjectID` (Hersteller grob aus der Enterprise-Nummer),
     `sysName` (ohne Domäne, wie der Hostname per SSH).
   - ENTITY-MIB: `entPhysicalClass` bis zum ersten Chassis, dann `entPhysicalModelName`,
     `entPhysicalSerialNum`, `entPhysicalSoftwareRev` dieser Zeile (sonst Version/Modell aus
     `sysDescr`).
   - IF-MIB: `ifDescr`, `ifType`, `ifPhysAddress`, `ifAdminStatus`, `ifOperStatus`, `ifName`,
     `ifHighSpeed`, `ifAlias`. Übernommen: Ethernet, LAG und VLAN-Interfaces (bei Cisco
     `propVirtual` nur mit Namen `Po…`/`Vl…`).
   - EtherLike-MIB: `dot3StatsDuplexStatus`. IEEE8023-LAG-MIB: `dot3adAggPortSelectedAggID`,
     `dot3adAggPortAttachedAggID` (Port-Channel-Mitglieder).
   - BRIDGE-MIB: `dot1dBasePortIfIndex` (Bridge-Port → ifIndex), `dot1dTpFdbPort`/`Status`
     (Rückfall der MAC-Tabelle ohne VLAN).
   - Q-BRIDGE-MIB: `dot1qVlanStaticName`/`EgressPorts`/`UntaggedPorts`, `dot1qPvid`, ohne
     statische Tabelle `dot1qVlanCurrentEgressPorts`/`UntaggedPorts`, `dot1qVlanFdbId`
     (FDB → VLAN), `dot1qTpFdbPort`/`Status`. Rückfall **IEEE8021-Q-BRIDGE-MIB** (gleiche
     Spalten mit der Komponente im Index) und `ieee8021BridgeBasePortIfIndex`.
   - IP-MIB: `ipNetToPhysicalPhysAddress`/`Type`/`State` (ARP und IPv6-Nachbarn), Rückfall
     `ipNetToMediaPhysAddress`/`Type` (nur IPv4).
   - LLDP-MIB: `lldpLocPortIdSubtype`/`Id`/`Desc` (lokaler Port), `lldpRem…` (Chassis, Port,
     Beschreibung, Systemname/-beschreibung, Fähigkeiten), `lldpRemManAddrIfSubtype`
     (Management-Adresse aus dem Index).
   - CISCO-CDP-MIB: `cdpCacheAddressType`/`Address`/`DeviceId`/`DevicePort`/`Platform`/
     `Capabilities`.
   - **Nicht** gelesen: Konfiguration, Community/USM/VACM-Tabellen, Zähler, Routing (dafür SSH),
     Spanning Tree, vPC, MLAG, Traps; nie SET.
6. **Gleiches Ergebnis wie per SSH**: `snmp/normalize.py` erzeugt dieselben DTOs (Gerät, Ports,
   VLANs, Nachbarn, MAC, ARP inkl. IPv6) – Verkabelung, Endpunkte, L2-Sicht, Befunde und der
   NetBox-Sync laufen unverändert. Abbildung:
   - Status `up`/`down`/`disabled` (Admin aus), Speed aus `ifHighSpeed` (`1000`, `10G`), Duplex
     `full`/`half`, Beschreibung aus `ifAlias`, MAC des Ports in `attrs.mac`.
   - VLANs je Bridge-Port: nur ungetaggt = Access (VLAN = ungetaggtes bzw. PVID), sonst Trunk mit
     erlaubten VLANs als Bereiche und Native VLAN = ungetaggtes VLAN (PVID).
   - MAC-Tabelle: nur gelernte bzw. statische Unicast-Einträge an übernommenen Ports, nicht die
     eigenen; ARP/IPv6 ohne Typ `local`/`invalid`, ohne unvollständige Einträge, ohne MACs eigener
     Interfaces; Link-local bleibt wie bei SSH (`nd.py`).
   - Nachbarn: lokaler LLDP-Port über Port-ID/Beschreibung = Name eines Interfaces, sonst Nummer
     als ifIndex bzw. Bridge-Port; Gegenstelle mit Systemname (sonst Chassis-ID), Port-ID (bei MAC
     bzw. lokaler Nummer die Portbeschreibung), Management-IP (IPv4 vor IPv6), Fähigkeiten in den
     Wörtern von CDP/LLDP; am Port gilt **CDP vor LLDP** wie per SSH.
   - Juniper MX = Rolle `router` (wie per SSH).
7. **Fehlende Tabellen** sind kein Fehler: fehlt IF-MIB, Q-BRIDGE (bzw. IEEE 802.1Q), die
   MAC-Tabelle oder LLDP (bzw. liegt sie nicht in der View des Benutzers), steht ein Hinweis im
   Lauf („ohne Antwort (Tabelle fehlt bzw. nicht in der View): …“); EtherLike, ENTITY, LAG, CDP
   und die Rückfälle fehlen ohne Hinweis. Eine Spalte mit Fehlermeldung bleibt leer (Hinweis),
   eine gekappte Spalte steht im Lauf.
8. **Prüfen**: `--check` des Agenten macht je Gerät **ein GET auf `sysName`** mit SNMPv3
   authPriv („erreichbar (SNMPv3 authPriv, sysName …)“); „Prüfen“ im Orchestrator löst nur den
   Namen auf und weist darauf hin, dass UDP ohne Anfrage nicht prüfbar ist.
9. **Meldungen** deutsch gespeichert, beim Abruf übersetzt (`store/run_text.py`, ADR 0028); auf
   der Konsole des Agenten in `AUTOMATO_LANG`. Abgelehnte Anmeldung (USM: unbekannter Benutzer,
   falscher Digest, Entschlüsselung) und keine Antwort (manche Geräte antworten bei falschem
   Benutzer gar nicht) mit Hinweis auf Benutzer, Protokolle, Read-View und ACL.

## Konsequenzen

- Neue Abhängigkeit des Collectors: **pysnmp** (≥ 7.1, BSD-2-Clause, rein Python; zieht pyasn1
  nach). Agenten laden sie beim Selbst-Update (ADR 0011); fehlt sie, meldet der Lauf „pysnmp
  fehlt“, SSH und alle anderen Quellen laufen weiter.
- **Grenzen gegenüber SSH**: Cisco IOS/IOS-XE führt die VLANs je Port und Trunk-Listen nur in
  eigenen MIBs (CISCO-VTP-MIB, CISCO-VLAN-MEMBERSHIP-MIB) und die MAC-Tabelle je VLAN in eigenen
  Contexts (`vlan-<id>`) – per SNMP fehlen dort VLANs je Port bzw. MACs ausserhalb von VLAN 1
  (Hinweis im Lauf); für Cisco IOS bleibt SSH genauer. Statische Port-Channels ohne LACP fehlen,
  wo das Gerät sie nicht in der LAG-MIB führt. Nur ein Context je Quellsystem; ARP nur der
  Standard-VRF (bzw. des Contexts). Ohne `dot1dBasePortIfIndex` gilt Bridge-Port = ifIndex.
- Kein Spanning Tree, kein vPC/MLAG, kein Routing per SNMP – die zugehörigen Befunde und Pläne
  bleiben bei SNMP-Geräten ohne diese Angaben.
- Behoben nebenbei: das Ziel des Agenten enthielt die festgehaltenen SSH-Host-Schlüssel nicht
  (das Schema der Antwort liess `known_hosts` weg) – der Agent lernte sie bei jedem Lauf neu. Jetzt
  gehen sie mit (nur bei Switches & Router); das Ziel anderer Plattformen bleibt gleich.
- Risiko R22 ergänzt (zweiter Lese-Zugang, gemeinsamer SNMPv3-Benutzer, UDP).
- Offen bzw. später: CISCO-VTP-MIB/CISCO-VLAN-MEMBERSHIP-MIB und MAC je VLAN-Context für Cisco IOS,
  mehrere Contexts, ENTITY-MIB je Stack-Mitglied, BRIDGE-MIB/RSTP-MIB für Spanning Tree.
