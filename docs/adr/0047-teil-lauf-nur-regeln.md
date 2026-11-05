# ADR 0047 — Lauf nur der Regeln einer Applikation, Vorrechnen in Durchgängen

## Status

**Angenommen** (2026-11-05), umgesetzt in 0.139.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-05: „Das Neuberechnen der Apps und Wege muss besser gesteuert werden. Wenn ich
Imports am Laufen habe, startet er jedes Mal neu, wenn ein Gerät importiert hat. Ich denke, wir
sollten vorberechnen ungeachtet der Daten mal starten und dann wenn alle vorberechnet sind, die
komplette Berechnung durchführen.“ Und: „Wenn ich im Realen System was ändere für eine App auf
diversen Firewalls und ACI, würde ich gerne nicht alle Firewalls und ACI neu integrieren,
sondern nur die Regeln neu berechnen, welche geändert haben. Dies würde viel Zeit sparen.“
Ergänzt ADR 0037 (Doku als Stand im Hintergrund) und ADR 0009 (Läufe über Agenten).

## Kontext

- Jeder Lauf ändert den Fingerabdruck aller Applikationen (letzter Lauf des Mandanten). Das
  Vorrechnen suchte nach jeder Berechnung neu, was nicht mehr passt, die schnellen zuerst – mit
  Läufen im Gang kamen dieselben schnellen Applikationen immer wieder dran, die langsamen nie.
- Die Befunde Regel ↔ Weg der Applikationen rechneten bis zu `app_findings_seconds` (120 s)
  im Einspielen jedes Laufs, unter der Sperre des Mandanten.
- Ein Lauf liest immer alles: Regeln, Objekte, NAT, Interfaces, Routing, HA, Fabric, Border-Leafs.
  Nach einer Regeländerung für eine Applikation dauerte das Einlesen aller betroffenen Geräte und
  das Neurechnen aller Applikationen lange.

## Entscheid

1. **Vorrechnen in Durchgängen** (`doc_snapshots.warm`): zu Beginn steht fest, was aussteht
   (schnelle zuerst); jeder Stand wird genau einmal gerechnet – mit den Daten, die beim Rechnen
   gelten. Ein Lauf bzw. eine Änderung während des Durchgangs bricht nichts ab, sondern merkt einen
   weiteren Durchgang mit allem, was danach nicht mehr passt. Was inzwischen (z. B. beim Öffnen)
   passt, wird übersprungen. Die Übersicht zeigt „Durchgang n: x von y · danach erneut“.
2. **Eine Applikation gleich** (`doc_snapshots.refresh_app`): nach ihrem Speichern bzw. nach dem
   Einlesen ihrer Regeln rechnet sie sofort, ohne auf einen Durchgang zu warten und ohne einen für
   alle anzustossen.
3. **Befunde hinter den Läufen** (`app_refresh.schedule_runs`): die Abweichungen Regel ↔ Weg,
   danach Doku und Routing-Filter, im Hintergrund und zusammengefasst – steht ein Durchgang aus,
   kommt keiner dazu. Die Routing-Filter rechnen nach einem Anstoß während des Rechnens noch
   einmal (bis dahin ging er verloren).
4. **Lauf nur der Regeln** (`collector_run.scope = rules`, `application_id`, Migration 0075):
   - FortiGate: je VDOM Policies, Adressen, Gruppen, Services, VIPs, IP-Pools (inkl. IPv6) – ohne
     Interfaces, Zonen, Routing, HA und System. Cisco ACI: nur der Tenant-Baum (Contracts,
     Filter, EPGs) – ohne Fabric und Border-Leafs. Das Ergebnis hat weder Inventar noch Routing;
     der Orchestrator ersetzt Regeln, Objekte, NAT und EPGs und lässt Inventar und Routing des
     letzten vollen Laufs stehen.
   - Angestoßen in der Applikation („Regeln neu einlesen“): alle FortiGates und Cisco ACI des
     Mandanten zur Auswahl, die der Applikation vorgewählt (Regeln bzw. Contracts ihrer Doku,
     Firewalls und Fabrics auf den Wegen ihrer Verbindungen, aus dem gespeicherten Stand).
   - Danach rechnet **nur diese Applikation** neu (Befunde, Doku, Verbindungen), sobald ihr
     letzter Teil-Lauf fertig ist. Andere Applikationen zeigen „veraltet“ und rechnen beim nächsten
     Durchgang bzw. beim Öffnen; ein Teil-Lauf stösst keinen Durchgang für alle an.
   - Ein Teil-Lauf zählt nicht als voller Lauf: nicht für die Datenqualität (Stand des
     Quellsystems) und nicht im Fingerabdruck der Routing-Filter.
   - Ein Agent ohne 0.139.0 liest beim Teil-Lauf alles (das Feld `scope` ist ihm unbekannt) – das
     Ergebnis wird wie ein voller Lauf eingespielt.

## Folgen

- Rein lesend gegenüber den Geräten wie jeder Lauf; kein neuer Schreibpfad.
- Mit Importen im Gang rechnet das Vorrechnen jeden Durchgang zu Ende; die Daten eines Durchgangs
  sind nicht alle vom selben Zeitpunkt – der nächste Durchgang zieht nach.
- Nach einem Teil-Lauf passen Regeln und Routing eines Geräts zu verschiedenen Zeitpunkten; wer
  auch Interfaces oder Routing geändert hat, liest das Gerät voll („Jetzt einlesen“).
