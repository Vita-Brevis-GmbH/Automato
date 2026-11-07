# ADR 0049 — Datenstand nach Inhalt, nur Betroffenes neu, erst nach den Läufen

## Status

**Angenommen** (2026-11-06), umgesetzt in 0.141.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-06: „Berechnungen starten, auch wenn noch einige Läufe in der Warteschlaufe
stehen. Die Neu berechnungen verhindern schnelles einlesen. Dies erst ausführen, wenn die Läufe
durch sind. Die Berechnungen nur anstossen, wenn was geändert hat, also nur die Wege berechnen,
welche von der geänderten Firewall Regel betroffen sind.“ Auf den Vorschlag (warten, nur bei
Änderung, nur Betroffenes): „Je Applikation“. Ergänzt ADR 0037 (Doku als Stand), ADR 0047
(Vorrechnen in Durchgängen) und ADR 0048 (Wege je Mandant).

## Kontext

- Der Abdruck der Doku enthielt den Zeitpunkt des letzten Laufs: jeder Lauf – auch ohne
  Änderung – machte alle Stände aller Applikationen veraltet, und nach jedem Lauf begannen die
  Befunde und das Vorrechnen, auch wenn weitere Läufe warteten.
- Ein Lauf legte Regeln, NAT, Objekte und VMs mit neuen Kennungen an; Doku und Verbindungen
  verweisen auf diese Kennungen. Ein Stand ohne Änderung wäre so trotzdem veraltet gewesen.
- Messung (künstlicher Mandant, 50 Applikationen): die Doku brauchte 83 % der Zeit, weil sie
  Regeln, Relevanz, Graph und Policies neben den Verbindungen ein zweites Mal aufbaute; jeder
  Graph lud den Routing-Index neu (21 000 Abfragen der Routen).

## Entscheid

1. **Erst nach den Läufen** (`runs.open_count`, `app_refresh.wait_for_runs`): Befunde und
   Vorrechnen beginnen erst, wenn beim Mandanten kein Lauf mehr wartet oder läuft – nach dem
   letzten Lauf, auch wenn dieser fehlschlug. Kommt während des Vorrechnens ein Lauf dazu, hält es
   nach dem aktuellen Stand an; der Zeitplan setzt fort, wenn ein Lauf ohne Ergebnis endet
   (`app_refresh.resume`). Hängende Läufe zählen nicht (Fristen wie `expire_stale`). Öffnen einer
   Applikation und Teil-Läufe nur der Regeln einer Applikation rechnen wie bisher sofort.
2. **Gleiche Kennung** (`store/stable_ids.py`): Regeln, NAT, Firewall-Objekte, VMs und Portgruppen
   behalten beim erneuten Einlesen die Kennung je fachlichem Schlüssel; die Reihenfolge der Regeln
   steht ausdrücklich in `sequence` (Position auf dem Gerät).
3. **Datenstand nach Inhalt** (`store/data_state.py`, Tabellen `data_state`, `data_change`): nach
   dem Einspielen vergleicht der Orchestrator je Quellsystem den Inhalt mit dem letzten Lauf – je
   Teil, je Eintrag (Objekt, VIP, EPG, VM mit Adressen) und je Regel (Inhalt, Adressbereiche,
   Dienste, Vorgänger). Nur bei einem Unterschied entsteht eine Änderung mit der nächsten Nummer
   des Mandanten; diese Nummer ersetzt im Abdruck der Doku, im Wege-Speicher und bei den
   Routing-Filtern den Zeitpunkt des letzten Laufs. Referenzquellen ebenso (Inhalt ihrer Einträge).
4. **Nur Betroffenes** (`store/footprint.py`, `doc_snapshots.carry`): jeder Stand merkt beim
   Speichern seinen Fussabdruck (Regeln, Geräte, EPGs, eigene Adressen, alle Adressen, je
   Verbindung Quelle und Ziel). Ändern sich nur Daten, ist er betroffen, wenn
   - die Änderung jede Applikation betreffen kann (angeschlossene Netze, Interfaces, Subnetze,
     VRFs, Kontexte, HA, L3Outs, BGP-Nachbarn und -Filter, Portgruppen, IPAM/DNS, erster Lauf);
   - NAT, Routen bzw. Policy-Routen eines Geräts geändert haben, das er nutzt;
   - geänderte Objekte, VIPs, VMs bzw. Endpunkte eine seiner Adressen bzw. EPGs treffen;
   - eine Regel geändert hat (neu, geändert, weg, verschoben), die er zeigt bzw. prüft, deren
     Bereiche ein eigenes System treffen, die eine seiner EPGs nennt, oder die auf einem seiner
     Geräte zu Quelle und Ziel einer seiner Verbindungen passt (any auf einer Seite: jede
     Verbindung über das Gerät).
   Sonst übernimmt er den neuen Datenstand ohne Neuberechnung. Im Zweifel betroffen; fehlt der
   Fussabdruck oder eine Änderung (aufgeräumt, `KEEP`), rechnet er neu. Den Grund zeigt die
   Übersicht „Stand der Doku“. MAC-/ARP-Tabellen und Nachbarn der Switches zählen nicht (Doku und
   Wege nutzen sie nicht).
5. **Grundlagen teilen**: Doku und Verbindungen einer Applikation teilen Daten, Graph und
   Policies; im Wege-Speicher teilen alle Graphen eines Datenstands Routing-Index, Routen (reine
   Daten), VRF je Interface, genauere und Backup-Routen und die Subnetze des Fabrics – neu, sobald
   ein Lauf die Routen neu anlegt.

## Folgen

- Ein Lauf ohne Änderung rechnet nichts; eine geänderte Regel rechnet nur die Applikationen, die
  sie betrifft – geprüft in `tests/test_data_state.py`.
- Kennungen bleiben über Läufe gleich: Links auf Regeln, NAT und VMs bleiben gültig.
- Wie bisher nur lesend gegenüber den Geräten; `data_state`/`data_change` sind abgeleitete Daten.
- Neue Teile eines Laufs (neue Felder im Collector) zählen ohne Eintrag in `SCOPES` als „jede
  Applikation“ – neue Abkürzungen nur mit Begründung und Test.

## Nachtrag 0.141.2 (2026-11-06)

Befund des Anwenders: Klick auf die Regel in den Findings – „404“. Die Befunde der Applikationen
rechnen erst nach den Läufen neu (Entscheid 1) und verweisen bis dahin auf die Kennung beim
Rechnen.

- Mehrere Regeln mit gleichem Schlüssel behalten ihre Kennungen in ihrer Reihenfolge (bis 0.141.1
  nur die erste); eine umbenannte Regel behält ihre Kennung über die Policy-ID bzw. DN, wenn genau
  eine alte und eine neue Regel dazu passen (`stable_ids.Ids.plan`).
- Beim Einlesen (`reconcile._app_rule_refs`) zeigen Befunde der Applikationen auf die Regel mit
  neuer Kennung (gleiches Quellsystem, Policy-ID bzw. DN, VDOM, Name) bzw. fallen weg, wenn es die
  Regel nicht mehr gibt. Das Detail einer nicht mehr vorhandenen Regel sagt das statt eines
  Fehlers.
