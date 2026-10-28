# ADR 0035 — Kategorien nach Zonentyp: Richtung, Geräte der Zonen, Ausgänge aus dem Routing

## Status

**Entwurf** (2026-10-28), erste Fassung in 0.105.0, seit **0.106.0 vereinfacht** – zur Abnahme durch
den Anwender.

Rückmeldung des Anwenders vom 2026-10-28 zu 0.104.0:

- „Ich sehe bei dem Netzwerkdesign immer noch nur Ressourcen Tenant, die Kat. 9 ist nicht mehr
  auswählbar. Kann man dies nicht vereinfachen?“
- Gewünscht: Netze je Zone mit Ausschlüssen, Ausgänge der Zonen, Zonentyp je Zone („Zonentyp aus
  dem Grundschema, welches ich in das Namensprofil jeweils hochlade“), Kategorien als Richtung
  („DC zu DC-Ressourcen, Internet zu DC“) mit den Spezialfällen „innerhalb des Security Tenants“
  und „innerhalb der Applikation (Server intern)“, Darstellung nach Routing und Zonen, Befund,
  wenn ein Netz nicht dort gelernt wird, wo die Zone es erwartet.

Rückmeldung zu 0.105.0:

- „Es ist immer noch die Kategorien und Bereiche da. Ich finde es ist nicht notwendig.“
- Ausgänge: „Vielleicht brauchen wir dies gar nicht, denn eigentlich müsste aus den Daten
  ersichtlich sein, über welches L3O die Routen und die Zone dahinter feststehen, wenn ich den
  Zonen Geräte zuweise … man müsste dies besser im Design ablegen können, anstelle der Bereiche.“
- Namensprofil: „Spalten Titel, Titel English, Source Zone, Dest Zone“ – „Titel mit Zone
  weglassen“; VIP- und NAT-Spalten bleiben, wo die Zeilen sie haben.
- „Die Regeln any auf einer Firewall wären dann unspezifisch, wenn nicht klar ist, wo überall es
  hin geht.“
- „Es sollen auch Anbindungen aus dem Routing einer Zone zugeordnet werden können.“
- „Sorry, aber einfacher ist besser, in allem.“

Ergänzt ADR 0012 (Kategorien der Applikations-Doku), ADR 0024 (logische Zonen, Zonenplan) und ADR
0034 (L3Outs: Firewall führend). Migrationen `0064` (`logical_zone.zone_type`) und `0065`
(Bereich je Zone und Ausgänge von Hand entfernt).

## Kontext

- Bis 0.104.0 ordnete jede Zone einen **Bereich** zu (Kategorie eingehend/ausgehend), dazu Merkmale
  im Namen der Firewall-Zone (`L3O_inet` …), Spalten-Layout, „nur mit Zone“ und Titel mit Zonen je
  Kategorie. Ein Bereich galt für die Zone als Gegenstelle, gleich aus Sicht jeder Applikation;
  „aus den Ressourcen-Tenants in die Kunden-Applikationen“ liess sich so nicht ausdrücken.
- 0.105.0 brachte Zonentyp, Richtungen und Ausgänge von Hand – neben den Bereichen, also noch
  komplizierter.
- Die Zonen haben Netze mit Ausschlüssen (0.102.0) und eine Zone im Grundschema (erkannt nach
  Name, Kurzname, Muster bzw. Vorlage je Kunde, 0.78.0). Der Zonenplan kennt **Geräte** je Zone
  (Firewall/VDOM, ACI-Tenant/-VRF) und leitet die **Übergänge** (Interfaces, L3Outs) daraus ab
  (0.56.0). Was hinter dem Interface einer Regel liegt (Anbindung, geroutete Netze,
  Default-Route), kennt die Doku seit 0.25.0; die Firewall hinter einem L3Out seit 0.97.0.

## Entscheid

1. **Zonentyp = Zone des Grundschemas.** Jede Zone hat einen Zonentyp: von Hand
   (`logical_zone.zone_type`, Netzwerkdesign → Kategorien bzw. Zonen-Editor, nur Zonen des
   Grundschemas, sonst 422), sonst automatisch wie beim Befund „Zone ausserhalb des Schemas“.
   Ohne hochgeladenes Grundschema gilt die eingebaute Vorlage. Der Typ von Hand gilt auch für den
   Zonenplan.
2. **Kategorie = Titel + Richtung.** Im Namenskonzept hat jede Kategorie nur Nummer, Titel
   (deutsch/englisch), **Source-Zone** und **Dest-Zone**. Das sind je ein oder mehrere Zonentypen,
   `*` = jeder; „Internet“ etwa ist Edge Internet und Public.
   - Fest, nur der Titel änderbar: **innerhalb der Applikation** (7), **innerhalb des Security
     Tenants** (8) und **unspezifisch** (10).
   - Die übrigen Vorgaben sind Richtungen auf der Vorlage (1/2 Internet, 3/4
     Unternehmensnetzwerk, 5/6 VPN, 9 DC → DC, 11 Edge Management → DC). Sie lassen sich ändern
     und entfernen; eigene Kategorien kommen dazu.
   - Entfallen sind Bereiche je Zone, Merkmale im Namen der Firewall-Zone, Optionen,
     Spalten-Layout, „nur mit Zone“ und Titel mit Zonen. Ausgehende Regeln mit breiter Quelle
     und unspezifische ausgehende bleiben fest ausgeblendet (wie die Vorgaben bisher).
3. **Einteilung**, in dieser Reihenfolge:
   1. innerhalb der Applikation: ACI im selben Application Profile bzw. Scope, oder alle
      Gegenstellen gehören zur Applikation;
   2. innerhalb des Security Tenants: ACI im selben Tenant, oder alle Zonen der Gegenstelle sind
      Zonen des Systems;
   3. die genaueste passende Richtung Zonentyp der Quelle → Zonentyp des Ziels (eingehend:
      Gegenstelle → System). Ohne `*` vor `*`, bei Gleichstand die kleinere Nummer. Mehrere
      Zonentypen auf einer Seite ergeben keine Richtung;
   4. sonst unspezifisch.

   **Ohne Zone** nimmt eine Seite den Zonentyp nach Namen, wie bei den Zonen nach den Mustern
   des Grundschemas: die Firewall-Zone bzw. der L3Out der Gegenstelle („L3O_inet“ → Edge
   Internet), externe Gegenstellen bzw. eine VIP „internet“ (Vorlage: Public). Systeme im ACI und
   die dokumentierten Systeme gelten als DC, d. h. als Zone der Art „dc“ im Grundschema. So
   funktioniert die Doku auch ohne gepflegte Zonen.
4. **Ausgänge ohne Pflege.** Liegt eine Gegenstelle nicht ganz in den Netzen einer Zone (any,
   grosser Bereich), zählt, was hinter dem Interface der Regel auf ihrer Seite liegt: die Zonen
   der Anbindung dort, der Netze, die die Firewall darüber routet, bzw. mit der Default-Route die
   Zonen mit 0.0.0.0/0.
   - any: diese Zonen.
   - Ein grosser Bereich: nur seine Zonen, die dort liegen.
   - Bei einer externen EPG ohne eigene Zone gilt die Zone der Firewall hinter ihrem L3Out.
   - Nicht eindeutig (mehrere Zonentypen bzw. keiner): unspezifisch.

   Die Selektoren „Ausgang Interface/Firewall-Zone/L3Out“ aus 0.105.0 entfallen.
5. **Geräte im Netzwerkdesign.** Netzwerkdesign → Kategorien hat je Zone die Spalten Zonentyp,
   Netze, Ausschlüsse und **Geräte**. Geräte sind Firewall/VDOM, ACI-Tenant, ACI-VRF und
   Anbindungen aus Routing → Anbindungen (Selektoren `fw_vdom`, `aci_tenant`, `aci_vrf`, `exit`,
   `PUT /zones/appdoc` mit `devices`). Die übrigen Selektoren pflegt der Reiter Zonen.
6. **Befund `zone_net_exit`.** Eine Firewall bzw. VDOM, die einer Zone A zugewiesen ist, erwartet
   ein Netz einer anderen Zone Z über ihre Interfaces zu den Nachbarzonen auf dem kürzesten Weg
   von A nach Z im Zonenplan (Übergänge).
   - Gezählt wird die wirksame Route mit dem längsten Präfix, das das Netz ganz enthält (RIB,
     Standard-VRF).
   - Führt sie über ein anderes Interface (mit den Zonen dahinter), nur über die Default-Route
     oder gar nicht dorthin, entsteht ein Befund (Warnung).
   - Ohne Interface der Firewall Richtung Z bzw. ohne Weg im Zonenplan prüft Automato nichts.
7. **Spalten nach den Zeilen.** Mit einer VIP der Firewall die Kette DNS → VIP → Load Balancer →
   Server; NAT bzw. NAT-IP nur, wenn eine Zeile sie hat – je nach Zonenübergang.
8. **Rein lesend.** Zonentyp, Geräte und Kategorien sind Laufzeitdaten in Automato (Zone bzw.
   Wörterbuch des Namenskonzepts, Vorgaben im Code); kein neuer Schreibweg, kein CSV-Import.

## Konsequenzen

- „Zugriffe aus den Ressourcen-Tenants“ und „aus anderen Kunden-Tenants“ sind je eine Kategorie mit
  „DC-Ressourcen → DC“ bzw. „DC → DC“, aus Sicht jeder Applikation richtig. Zugriffe innerhalb der
  eigenen Zone gehen vorher in Kategorie 8.
- Gespeicherte Bereiche und Optionen aus 0.104.0/0.105.0 gelten nicht mehr (Migration `0065`,
  Wörterbuch-Einträge werden beim nächsten Speichern entfernt). Richtungen aus 0.105.0 werden zu
  Source-/Dest-Zone zusammengefasst. Ausgänge von Hand sind gelöscht – die Geräte der Zonen
  ersetzen sie.
- Ohne Zonen und ohne passende Namen landen mehr Zugriffe in „unspezifisch“ als mit den
  Merkmalen bis 0.104.0 (z. B. Firewall-Zonen ohne Muster im Grundschema). Abhilfe: Zonen mit
  Zonentyp bzw. Muster im Grundschema.
- Der Befund setzt Geräte an den Zonen und gelesene Routen voraus.
- Offen: BGP-Filter (Prefix-Listen) aus Netzen und Ausschlüssen je Zone vorschlagen – nur als
  Vorschlag bzw. Export, kein Schreibweg (ROADMAP).
