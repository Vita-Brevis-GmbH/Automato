# ADR 0040 — Grosse Netze der Regeln zuordnen (kombiniert)

## Status

**Entwurf** (2026-10-29), umgesetzt in 0.111.0 – zur Abnahme durch den Anwender.

Rückmeldung des Anwenders zu 0.109 (2026-10-29): „Haben wir aber ein Objekt mit 10.0.0.0/8 in der
Regel, aber wir lernen die Subnetze einzeln und nicht als ganzes Grosses, dann wird das
Netzwerkobjekt 10.0.0.0/8 dem nächst besseren zugewiesen, nämlich 0.0.0.0/8 [gemeint: 0.0.0.0/0] und nicht den kleinen
Routen 10.0.1.0/24. Da wir bei den Regeln nicht genau sagen können, wo sie hin gehen, müssen wir
diese markieren und zuordnen lassen, wo sie hin gehören, oder sie anhand von Angaben in
Anbindungen und Zonen ausschliessen oder zuweisen.“ Gewählt: **kombiniert** – von Hand, dann Zone
bzw. Anbindung, dann die Teile nach dem Routing, der Rest unklar mit Befund.

Ergänzt ADR 0019 (Routing, Weg), ADR 0021 (Verbindungen Ende zu Ende), ADR 0024 (Zonen),
ADR 0034 (L3Out Firewall führend),
ADR 0036 (Module: Wegsucher) und ADR 0039 (Prüfungen je Modul). Migration 0069 (`net_assignment`).

## Kontext

- Der Weg eines Netzes folgt der Route mit dem längsten passenden Präfix, die das **ganze** Netz
  enthält. Für 10.0.0.0/8 ohne eigene Route ist das die Default-Route – der Weg zeigt dann ins
  Internet, obwohl die Teile (10.0.1.0/24 …) über genauere Routen ganz anders laufen.
- Seit 0.79.1 werden genauer geroutete Teile eines Ziel-Netzes als eigene Verbindungen gezeigt.
  Offen blieb, wohin das Netz **als Ganzes** gehört: ob der Rest über die kürzere Route richtig
  ist, ob nur die Teile zählen, oder ob es zu einer Zone bzw. Anbindung gehört.
- Aus den Regeln allein lässt sich das nicht sagen; Zonen (Netzwerkdesign) und die Netze hinter
  den Anbindungen kennen es oft schon.

## Entscheid

1. **Netz grösser als die Routen** (`store/net_assign.wider`): das Gerät routet das Netz nicht als
   Ganzes (die gewählte Route ist kürzer als das Netz) und entweder laufen genauere Routen darin
   anders, oder es bleibt nur die Default-Route und das Netz ist privat. Öffentliche Netze über die
   Default-Route sind normal und kein Fall.
2. **Wohin es gehört**, in dieser Reihenfolge (`Assigner.decide`):
   1. **von Hand** (`net_assignment`, je Mandant und Netz): zur Zone …, über die Anbindung …,
      „Weg nach dem Routing stimmt“ (`route`) bzw. „nur die genauer gerouteten Teile zählen“
      (`ignore`: die Verbindung über das ganze Netz fällt weg, die Teile bleiben). Eine gelöschte
      Zone bzw. Anbindung gilt als keine Zuordnung.
   2. **Zone** (Netzwerkdesign): genau eine Zone enthält das ganze Netz (Netze und Ausschlüsse).
   3. **Anbindung**: ihre Netze dahinter enthalten das ganze Netz.
   4. **Teile nach dem Routing**: gibt es genauere Teile, „aufgeteilt“ (`split`), sonst „unklar“
      (`unclear`) – beides noch zuzuordnen.
3. **Befund** `net_wider_than_routes` (Warnung, Prüfung `app/checks/wege/netz_groesser.py`, Modul
   Wegsucher): je Firewall, VDOM und Netz-Objekt einer Firewall-Regel, das grösser als die Routen
   ist und offen bleibt – mit Route, Next-Hop, Teilen und den Regeln. Mit Zuordnung (von Hand,
   Zone, Anbindung) fällt der Befund weg.
4. **Oberfläche:** Verbindungen Ende zu Ende zeigen den Zustand als Chip mit „zuordnen“ bzw.
   „ändern“ (Operator); der Befund hat „Netz zuordnen“; Routing → Anbindungen listet die
   Zuordnungen von Hand („Grosse Netze der Regeln“) zum Ändern und Entfernen.
5. **API** im Modul Wegsucher: `GET /routing/net-assignments` (viewer), `PUT` (operator; Netz ohne
   Host und nicht any, Zone bzw. Anbindung des Mandanten) und `DELETE /{id}` (operator), je mit
   Audit `net_assignment.set` bzw. `.deleted` – das ändert den Fingerabdruck der Doku (ADR 0037).
6. **Netze der Anbindungen** werden dafür als Liste gepflegt (bis 5 000 Einträge): einfügen in
   jeder Schreibweise (CIDR, Netz und Maske, Wildcard, mit Text dazwischen), prüfen (gültig,
   ungültig, doppelt), zusammenfassen und als Vorschlag die über den Next-Hop bzw. das Interface
   gelernten Routen (`store/net_lists`). Die grossen Netze bleiben von Hand einzutragen
   (Anwender: „man sollte die Filter dennoch eintragen, damit man die grossen Subnetze
   miteintragen kann“).
7. **Nur lesend gegenüber den Quellsystemen:** die Zuordnung ist Datenhaltung von Automato, kein
   neuer Schreibweg.

## Konsequenzen

- Regeln mit grossen Netzen zeigen keinen falschen Weg über die Default-Route mehr, ohne dass es
  auffällt: entweder erklärt eine Zone bzw. Anbindung das Netz, oder es gibt einen Befund.
- Private Netze, die nur über die Default-Route geroutet werden, werden neu als Befund gemeldet –
  bei vielen Regeln einmal je Firewall und Netz; mit einer Zone bzw. Anbindung verschwinden sie
  in grosser Zahl.
- Offen: die BGP-Filter (welche Netze erwartet werden) lesen und mit den Netzen der Anbindungen
  abgleichen (Version 1, BGP-Filter je Zone).
