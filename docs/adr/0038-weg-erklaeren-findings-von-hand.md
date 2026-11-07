# ADR 0038 — Weg erklären („Warum dieser Weg?“) und Findings von Hand

## Status

**Entwurf** (2026-10-29), umgesetzt in 0.109.0 – zur Abnahme durch den Anwender.

Rückmeldung des Anwenders vom 2026-10-29: „Ich habe immer noch Routen / Wege, bei denen nicht
ersichtlich ist, weshalb er nicht erkennt, wo es durch muss … ein Analyse-Tool, das ich bei
Verbindungen Ende zu Ende und der Weg-Suche einsetzen kann und das mir aufzeigt, weshalb dieser Pfad
gewählt wird“ (dazu „Erwartet über …“, beides in 0.109) und „ein Finding-Button, wo ich die Findings
manuell eintragen kann“.

Ergänzt ADR 0019 (Routing, Weg Hop für Hop), ADR 0004/0010 (Findings), ADR 0028 (Sprache),
ADR 0036 (Module: die Erklärung gehört zum Wegsucher, Findings von Hand zu den Findings) und
ADR 0037 (Stand der Doku). Keine Migration.

## Kontext

- Die Weg-Suche und die Verbindungen Ende zu Ende zeigen das Ergebnis (Hops, Route, Regel), aber
  nicht, **warum** eine Route gewählt bzw. eine andere verworfen wurde. Ob ein falscher Weg an der
  Konfiguration eines Geräts, an einem alten Lauf oder an Automato liegt, war nicht zu erkennen.
- Häufige Fragen: Warum nicht über die Firewall X? Welche Route gewinnt dort und warum? Gilt eine
  Policy-Route? Wem gehört der Next-Hop, und warum endet der Weg?
- Was der Anwender dabei findet, liess sich bisher nirgends festhalten: Findings entstehen nur aus
  dem Abgleich und werden nach jedem Lauf neu berechnet.

## Entscheid

1. **Weg erklären** (`store/path_explain.py`, `GET /routing/explain`, Modul Wegsucher): dieselben
   Eingaben und derselbe Weg wie `GET /routing/trace` bzw. wie bei einer Verbindung
   (`connection=true`, `starts`: dieselbe Wahl des Starts wie `app_paths.connection_trace`). Keine
   eigene Wegfindung – die Erklärung liest das Ergebnis von `paths.trace` und das gespeicherte
   Routing (alle Routen der Tabellen `rib`, `bgp`, `static`) und beschreibt es in Sätzen:
   Start, je Hop Eingang mit Zone und Routing-Tabelle, NAT, Load Balancer, Policy-Routen bzw.
   SD-WAN-Regeln (sicher, möglich, keine), alle Routen, die das Ziel enthalten (gewählt bzw.
   verworfen mit Grund: längstes Präfix, kleinere Distanz, nur BGP-Tabelle, statische Route nicht
   aktiv, Policy-Route vor dem Routing; höchstens acht), der Next-Hop mit Besitzer, das Ende mit
   Hinweis (fehlende Route, unbekannter Next-Hop mit Namen aus dem IPAM, Schleife, Default-Route
   nach aussen). Je Hop der Stand des Routings (letzter abgeschlossener Lauf, alt nach
   `AUTOMATO_STALE_DAYS`). Status je Schritt: ok, Hinweis, prüfen, Ende/Bruch.
2. **„Erwartet über …“** (`expect=<Quellsystem>`): liegt das Gerät auf dem Weg, nennt die
   Erklärung den Hop. Sonst der erste Hop, der mit dem Gerät direkt verbunden ist bzw. eine Route
   über eine Adresse des Geräts kennt: welche Route über das Gerät es gibt und warum sie verliert
   (Grund wie oben) bzw. dass sie fehlt, dazu wie das Gerät selbst Ziel und Rückweg routet. Ohne
   Nachbarschaft: die Nachbarn des Geräts als Hinweis.
3. **Findings von Hand** (`POST /findings`, Typ `manual`, Subjekt der Weg `Quelle → Ziel` bzw. das
   Gerät): Titel, Text (was anzupassen ist), Schwere, Gerät, Hop, Quelle/Ziel/Dienst. Nur
   Operator; Kunden-Benutzer nicht. Der Abgleich nach einem Lauf löscht sie nicht
   (`reconcile._sync_findings`), Triage wie bei jedem Finding. Audit `finding.created` ohne Titel
   und Text (Positivliste); Findings ändern den Fingerabdruck der Doku nicht (ADR 0037: Befunde in
   der Doku kommen live).
4. **Oberfläche:** „Warum dieser Weg?“ an jeder Weg-Karte der Weg-Suche und im Detail einer
   Verbindung (Seitenpanel, Wege wählbar, „Erwartet über …“); an jedem Schritt mit „prüfen“ bzw.
   „Ende/Bruch“ und am Ende „Als Finding erfassen“ (vorausgefüllt), oben bzw. auf der Seite Findings
   „+ Finding erfassen“ (frei). Texte zweisprachig (ADR 0028), gespeicherte Findings deutsch wie
   eingegeben.
5. **Nur lesend, kein neuer Schreibweg:** Erklärung und Findings bleiben in Automato; an Geräten,
   IPAM und NetBox ändert sich nichts.

## Konsequenzen

- Ein falscher Weg lässt sich Schritt für Schritt nachvollziehen: Konfiguration (Route fehlt,
  Distanz, Policy-Route), alter Stand (Lauf) oder Lücke in Automato – und direkt als Finding
  festhalten.
- Die Erklärung rechnet den Weg wie die Weg-Suche (gleiche Dauer, ein Aufruf je Öffnen bzw. je
  „Erwartet über“); die Routen je Hop kommen aus dem Graphen, kein zusätzlicher Lauf.
- Findings von Hand wachsen nur durch Erfassen; sie verschwinden nicht mit dem Abgleich, sondern
  über die Triage („erledigt“, „ignoriert“).
- Neue Wegregeln in `paths.trace` brauchen einen passenden Satz in `path_explain` – sonst bleibt der
  Schritt ohne Begründung (Tests `test_path_explain.py`).
