# ADR 0066 — Reservierungen von VLAN- und AS-Nummern, auch in NetBox

## Status

**Angenommen** (2026-11-17), umgesetzt in 1.19.0 – zur Abnahme durch den Anwender.

Anwender 2026-10-30 (Roadmap Version 2 Nr. 10): „Reservierungen in der Doku: Elemente festlegen
und reservieren – erst VLANs und BGP-AS-Nummern –, Befund bei Überschneidung (reserviert gegen
gelesen bzw. doppelt); AS-Nummern im Routing einzeln darstellen (je AS die Nachbarn, Firewalls und
Router)“. 2026-11-17 (Priorität 6 „Reservierungen“), auf die Rückfrage: „Reservationen auch ins
Netbox“. Ergänzt ADR 0010 (NetBox-Sync als einziger Schreibweg), ADR 0031 (Abschnitte des Syncs),
ADR 0039 (Prüfungen je Modul), ADR 0041 (BGP), ADR 0037/0049 (Stand der Doku, Datenstand).

## Kontext

- Wer ein neues VLAN bzw. eine neue AS-Nummer braucht, sucht heute in den Geräten bzw. in
  NetBox, was frei ist, und merkt sich die Wahl ausserhalb. Zwei Projekte nehmen so dieselbe
  Nummer; was gelesen schon in Gebrauch ist, fällt erst beim Konfigurieren auf.
- Automato kennt die VLANs je Standort (1.9.0: Standort des Quellsystems) und die BGP-Daten der
  FortiGates, Router und ACI-L3Outs (ADR 0041) – aber nicht je AS gebündelt.
- Der einzige Schreibweg ist der NetBox-Sync (Vorschau → Übernehmen); NetBox kennt VLANs mit
  Status `reserved` und ASN-Objekte (Pflichtfeld RIR).

## Entscheidung

1. **Reservierung = Laufzeitdaten je Mandant** (`reservation`, Migration `0093_reservierungen`):
   Art `vlan` bzw. `asn`, Nummer oder Bereich (`low`..`high`; VLAN 1–4094, AS 1–4294967295),
   bei VLANs der **Standort** (wie die VLAN-Ansicht seit 1.9.0; ohne = alle Standorte) und
   wahlweise die **VLAN-Gruppe in NetBox** (Slug), **Zweck**, reserviert von/am, geändert von/am.
   Anlegen, Ändern und **Freigeben** (= Löschen, das Audit `reservation.*` hält es fest) in der
   App (Operator), kein CSV. Der Standort steht ohne Fremdschlüssel (wie `stp_target`): ein
   gelöschter Standort bleibt als „Standort fehlt“ sichtbar, statt die Reservierung still zu
   löschen bzw. auf alle Standorte auszuweiten. Modul **Analyse** (gehört mit Priorität 3 zu L2
   bzw. L3).
2. **In Gebrauch** ist, was gelesen ist: VLANs der VLAN-Tabellen (`inv_vlan` der Switches, des ACI
   und der FortiGates) am Standort ihres Quellsystems; AS-Nummern lokal an gelesenen Geräten bzw.
   als BGP-Nachbar (Punkt 5). Beim Reservieren merkt Automato die schon belegten Nummern
   (`used_before`); beim Ändern behalten Nummern, die die Reservierung schon hatte, ihren Stand –
   nur neu dazugekommene werden festgehalten.
3. **Befunde** (steckbar, `app/checks/analyse/reservierung_gebrauch.py` und
   `reservierung_doppelt.py`, eigener Teil-Durchgang `reservierungen` nach jedem Speichern,
   sonst im Abgleich nach jedem Lauf):
   - `reservation_in_use` (**Hinweis**): beim Reservieren frei, jetzt gelesen in Gebrauch – die
     Reservierung ist umgesetzt bzw. die Nummer vergeben; freigeben;
   - `reservation_conflict` (**Warnung**): schon beim Reservieren in Gebrauch;
   - `reservation_overlap` (**Warnung**): zwei Reservierungen derselben Art überschneiden sich
     (VLANs am selben Standort bzw. eine für alle Standorte).
4. **Auch in NetBox – Abschnitt „Reservierungen“ des NetBox-Syncs** (`store/netbox_sync_reserv.py`,
   nur ausdrücklich gewählt, Vorschau → Übernehmen; neuer Schreibweg innerhalb von ADR 0010):
   - **VLANs** mit Status `reserved`, Name = Zweck, Beschreibung `Reservierung: <Zweck> ·
     reserviert von <wer> am <Datum>`, Tag `automato`, Tenant. **Gruppe:** die an der
     Reservierung gewählte (muss es in NetBox geben bzw. der Sync anlegen); sonst die **eine**
     VLAN-Gruppe mit dem Standort als Geltungsbereich (in NetBox bzw. vom Sync angelegt, z. B. die
     des Quellsystems der Switches); ohne solche eine eigene Gruppe „Reservierungen <Standort>“
     (`<Mandant>-reservierungen-<Standort>`, Beschreibung `Automato: Reservierungen …`; für alle
     Standorte ohne Geltungsbereich). Mehrere Gruppen am Standort: Hinweis, nichts geraten.
   - **AS-Nummern** als ASN-Objekte (`ipam/asns`, neu in `netbox_writer.WRITABLE` und
     `sync-user.sh`) mit der **RIR der vorhandenen NetBox**: die RIR, die die ASN gleicher Art
     (privat bzw. öffentlich) dort schon tragen, sonst die einzige RIR dieser Art (`is_private`)
     bzw. die einzige überhaupt. Ohne eindeutige RIR: klarer Hinweis, keine ASN – eine RIR legt
     der Sync nie an (RIR nur gelesen). Bereiche über 1024 AS werden nicht als einzelne
     ASN-Objekte übernommen (Hinweis, keine stille Grenze).
   - **Nie etwas Gelesenes überschreiben:** gibt es in NetBox schon ein VLAN mit dieser Nummer in
     der Gruppe bzw. diese ASN (von Hand, aus einem anderen Abschnitt bzw. Mandanten), zeigt die
     Vorschau es nur an (Konflikt mit Vermerk „Reservierung – nur angezeigt“, nichts geschrieben).
     Will ein anderer Abschnitt dasselbe VLAN (gelesen in Gebrauch), gewinnt er – ein eigenes
     reserviertes VLAN geht dabei in das gelesene über (Status `active`, gelesener Name).
   - **Eigentum:** reservierte VLANs (Tag, Status `reserved`, Beschreibung `Reservierung: `), ihre
     Gruppen (`Automato: Reservierungen`) und die ASN mit Tag `automato` des Mandanten gehören nur
     diesem Abschnitt: andere Abschnitte führen sie weder als veraltet noch löschen sie sie;
     freigegebene Reservierungen sind mit dem Abschnitt veraltet bzw. werden mit „Löschen
     zulassen“ entfernt.
   - **Ändern nur bestätigt** (derselbe Weg wie Kabelnummern und DNS-Namen): weicht ein schon
     angelegtes reserviertes Objekt ab (neuer Zweck in Automato bzw. in NetBox von Hand geändert),
     ist die Aktion `update` mit `confirm` – die Vorschau zeigt beide Werte, geschrieben wird nur,
     was der Betreiber je Eintrag bestätigt (`POST …/apply` mit `{"confirm": [...]}`, am Plan
     `options.confirm`, im Audit `reservations_confirmed`).
     **Ports und Netze nehmen nie ein reserviertes VLAN** (`_vlan_candidates`).
5. **AS-Nummern einzeln** (Ansicht „AS-Nummern“, `GET /routing/as-numbers`, `store/as_numbers.py`)
   nur aus den schon gelesenen BGP-Daten: je AS die Geräte mit diesem AS (FortiGate aus dem
   BGP-Prozess als Firewall, Router aus `local_as` ihrer Nachbarn; das lokale AS des ACI-Fabrics
   liest Automato nicht) und die Nachbarn mit diesem AS (Sitzungen, nur konfigurierte
   FortiGate-Nachbarn, Peers der ACI-L3Outs) mit Gerät, Zustand, eBGP/iBGP, Namen aus dem IPAM und
   dem gelesenen Gerät dahinter; eigen (wie die Firmengrenze, ADR 0032) bzw. fremd, privat, die
   Reservierungen und reservierte AS ohne Gebrauch.
6. **Datenstand und Doku:** Reservierungen ändern weder Doku noch Wege – ihr Audit zählt nicht für
   den Abdruck der Doku und den Wege-Speicher (`doc_snapshots._QUIET`), keine neuen Teile im
   Datenstand (ADR 0049); die Befunde dagegen gleich nach dem Speichern.

## Folgen

- Neue Nummern lassen sich in Automato festhalten, bevor sie konfiguriert werden; Kollisionen mit
  Gelesenem bzw. anderen Reservierungen sind Befunde, und NetBox zeigt die Reservierungen allen,
  die dort nachschauen.
- Der Sync-Benutzer braucht `ipam.asn` (`sync-user.sh` erneut ausführen); Lesen auf `ipam.rir`
  hat er schon (Lesen auf alles).
- Eine Reservierung, die umgesetzt ist, bleibt als Hinweis „jetzt belegt“ stehen, bis sie
  freigegeben wird – bewusst: wer sie umgesetzt hat, gibt sie frei.
- Grenzen: ACI-Fabric-AS nicht gelesen (nur Nachbarn), VLANs der Portgruppen (vCenter) zählen
  nicht als „in Gebrauch“, AS-Bereiche über 1024 nicht einzeln nach NetBox.
