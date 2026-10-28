# ADR 0035 — Zonentyp, Richtungen und Ausgänge der Zonen

## Status

**Entwurf** (2026-10-28), umgesetzt in 0.105.0 – zur Abnahme durch den Anwender. Rückmeldung des
Anwenders vom 2026-10-28 zu 0.104.0 („Ich sehe bei dem Netzwerkdesign immer noch nur Ressourcen
Tenant, die Kat. 9 ist nicht mehr auswählbar. Kann man dies nicht vereinfachen?“):

- „Ich weise in den Zonen die Subnetze aus, welche sie dahinter verbergen, mit der Möglichkeit,
  einzelne auszuschliessen.“
- „Ich weise in den Zonen die möglichen Ausgänge an, wohin die L3Os oder die Transfer-Netzwerke
  hinführen dürfen, also die Zielzone für die L3Os/Transfer-Interfaces.“
- „Ich weise einer Zone einen Zonentyp zu, also DC, DC-Ressourcen, Edge Internet usw.“ – „Zonentyp
  aus dem Grundschema, welches ich in das Namensprofil jeweils hochlade.“
- „Ich erstelle die Kategorien, welche ich brauche: DC zu DC-Ressourcen, Internet zu DC usw. So lege
  ich die Richtung der Firewall-Regeln an. Dann gibt es noch Spezialfälle (innerhalb des Security
  Tenants und innerhalb der Applikation).“
- „Nun wird dies anhand der gelernten Netzwerke im Routing und der Zonen sauber dargestellt.“
- „Weiter habe ich Findings, wenn ich die Netzwerke nicht von dort lerne, wo ich sie erwarte gemäss
  Netzwerk-Zone.“
- Alles in einer Version („Entwicklungszeit sparen“).

Ergänzt ADR 0012 (Kategorien der Applikations-Doku, Ergänzungen 0.101.0, 0.102.0, 0.104.0), ADR
0024 (logische Zonen) und ADR 0034 (L3Outs: Firewall führend). Migration `0064`
(`logical_zone.zone_type`).

## Kontext

- Seit 0.102.0 ordnet jede Zone im Netzwerkdesign einen **Bereich** zu (Kategorie eingehend/
  ausgehend), seit 0.104.0 im Namenskonzept anpassbar. Ein Bereich gilt für die Zone **als
  Gegenstelle** – aus Sicht jeder Applikation gleich. „Zugriffe aus den Ressourcen-Tenants in die
  Kunden-Applikationen“ und „Zugriffe aus anderen Kunden-Tenants“ liessen sich damit nur über
  eigene Bereiche je Paar ausdrücken; der Anwender sah nach dem Anlegen einer Kategorie bei allen
  Zonen nur noch diese.
- Die Zonen haben schon Netze mit Ausschlüssen (0.102.0) und ihre Zone im Grundschema (erkannt nach
  Name, Kurzname, Muster bzw. Vorlage je Kunde, Befund „Zone ausserhalb des Schemas“, 0.78.0).
- Wohin ein L3Out bzw. ein Transfer-Interface einer Firewall führt, stand nirgends; die Zuordnung
  einer Gegenstelle „any“ zu einer Zone war nur über die Firewall-Zone der Regel als Merkmal möglich.

## Entscheid

1. **Zonentyp = Zone des Grundschemas.** Jede Zone hat einen Zonentyp: von Hand
   (`logical_zone.zone_type`, Netzwerkdesign → Kategorien bzw. Zonen-Editor, nur Zonen des
   Grundschemas, sonst 422), sonst automatisch wie beim Befund „Zone ausserhalb des Schemas“. Ohne
   hochgeladenes Grundschema gilt die eingebaute Vorlage (DC, Edge Internet, Edge Office, …). Der
   Zonentyp von Hand gilt auch für den Zonenplan (Farbe, Schema).
2. **Kategorien als Richtungen.** Im Namenskonzept trägt jede Kategorie optional Richtungen
   „Zonentyp der Quelle → Zonentyp des Ziels“ (`*` = jeder, höchstens 60 je Kategorie). Sobald
   eine Kategorie Richtungen hat, gilt für den Mandanten die Einteilung nach Richtungen; die
   Bereiche je Zone entfallen dann (eingeklappt, Spalte „Kategorie“ im Netzwerkdesign
   ausgeblendet). Reihenfolge der Einteilung:
   1. **Innerhalb der Applikation** (Rolle `aci_ap`, Vorgabe Kategorie 7): ACI im selben
      Application Profile bzw. Scope, oder alle Gegenstellen der Zeile gehören zur Applikation.
   2. **Innerhalb der eigenen Zone** (Rolle `aci_tenant`, Vorgabe Kategorie 8 – „innerhalb des
      Security Tenants“): alle Zonen der Gegenstelle sind Zonen der eigenen Systeme.
   3. **Richtung** Zonentyp der Quelle → Zonentyp des Ziels (eingehend: Gegenstelle → System,
      ausgehend: System → Gegenstelle); die genaueste Richtung gewinnt (ohne `*` vor einem `*`),
      bei Gleichstand die kleinere Nummer. Ein Zonentyp zählt nur, wenn alle Zonen der Seite
      denselben haben.
   4. Sonst wie bisher (Merkmale der Firewall-Zone, ACI-Beziehung, unspezifisch), ohne Bereiche.
   Ohne Richtungen bleibt alles wie in 0.104.0 – bis auf die Korrektur: ein Bereich zählt nicht,
   wenn die Gegenstelle in der eigenen Zone liegt.
3. **Ausgänge.** Eine Zone nennt ihre Ausgänge als Selektoren: Firewall-Interface
   (`via_fw_interface`), Firewall-Zone (`via_fw_zone`), je optional mit der Firewall, und ACI-L3Out
   (`via_l3out`). Liegt eine Gegenstelle nicht ganz in den Netzen einer Zone (any, grosse Bereiche),
   gehört sie zur Zone, deren Ausgang die Regel nutzt (Interface bzw. Zone der Regel auf der Seite
   der Gegenstelle; externe EPG über ihren L3Out). Netze vor Ausgängen.
4. **Befund `zone_net_exit`.** Je Firewall und Kontext, in dem ein Ausgang einer Zone vorkommt,
   zählt für jedes Netz der Zone die wirksame Route mit dem längsten Präfix, das das Netz ganz
   enthält (RIB, Standard-VRF). Führt sie nicht über einen Ausgang der Zone – über ein anderes
   Interface (mit den Zonen, deren Ausgang es ist), nur über die Default-Route oder gar nicht –,
   entsteht ein Befund (Warnung) im Bereich Zonen. L3Outs ordnen nur zu, geprüft werden Firewalls.
5. **Rein lesend.** Zonentyp, Richtungen und Ausgänge sind Laufzeitdaten in Automato (Zone bzw.
   Wörterbuch des Namenskonzepts, Vorgaben im Code); kein neuer Schreibweg, kein CSV-Import.

## Konsequenzen

- „Zugriffe aus den Ressourcen-Tenants“ und „aus anderen Kunden-Tenants“ sind je eine Kategorie
  mit der Richtung „DC-Ressourcen → DC“ bzw. „DC → DC“ – aus Sicht jeder Applikation richtig, ohne
  Bereiche je Paar; Zugriffe innerhalb der eigenen Zone gehen vorher in Kategorie 8.
- Die Zonentypen kommen aus dem Grundschema des Namensprofils: wer ein eigenes hochlädt, sieht
  dessen Zonen zur Auswahl; Richtungen mit unbekannten Zonentypen meldet das Namenskonzept
  („nicht im Grundschema“).
- Der Befund setzt gelesene Routen voraus; ohne Ausgänge an einer Zone keine Prüfung.
- Offen: BGP-Filter (Prefix-Listen) aus Netzen, Ausschlüssen und Ausgängen je Zone vorschlagen –
  nur als Vorschlag bzw. Export, kein Schreibweg (ROADMAP).
