# ADR 0043 — Regelwerk der Applikations-Doku als Einstellung

## Status

**Angenommen** (2026-10-30), umgesetzt in 0.118.0 – zur Abnahme durch den Anwender.

Anwender 2026-10-30: „Können wir die Regeln in die Settings übernehmen und entsprechend aktivieren
oder deaktivieren? Und die Wirkung einstellen … die beiden Regelsets hinterlegen, damit man
umschalten kann. Wenn es neue Reihenfolgen gibt, immer dort ein neues Profil anlegen.“ Ergänzt
ADR 0019 (Relevanz nach dem Routing), ADR 0034 (L3Outs: Firewall führend) und ADR 0035
(Kategorien als Konfiguration). Keine Migration (Wörterbuch-Einträge im Namensprofil), kein
Schreibweg zu Geräten.

## Kontext

- Bis 0.117 entschied `appdoc_filter.place` fest im Code, ob eine Zeile der Applikations-Doku in
  ihrer Kategorie steht, nur als Treffer („Regeln & Contracts“) bleibt, gezählt oder ganz
  weggelassen wird. Die Regeln kamen einzeln aus Rückmeldungen (0.33.0 unmöglich, 0.60.0
  zugeordnet/ausgeschlossen, 0.78.2 L3Out und unspezifisch ausgehend, 0.103.0 breite Quelle,
  0.113.2 nicht auf dem Weg, 0.114.1 Internet). Jede neue Regel änderte die Doku für alle Kunden.
- Der Anwender will die Regeln fein einstellen und zwischen Ständen vergleichen können.

## Entscheid

1. **Bedingungen im Code, Regelwerk als Konfiguration** (`store/appdoc_ruleset.py`): elf
   Bedingungen (ausgeschlossen, direkt zugeordnet, nicht im Pfad nach dem Routing der Firewall,
   Firewall nicht auf dem Weg – unspezifisch bzw. konkret, über das Internet-Gateway hinaus,
   ausgehend mit breiter Quelle, deaktivierte Firewall-Regel, L3Out-Hilfs-Contract, L3Out-Contract
   nicht enger als die Firewall, unspezifische Kategorie ausgehend). Je Regel eines Profils:
   Schalter und **Wirkung** aus den für die Bedingung erlaubten (markiert, nur Treffer, nur
   Vermerk, gezählt, weggelassen, Ausnahme).
2. **Lesen wie ein Firewall-Regelwerk:** von oben nach unten, die erste eingeschaltete Regel, die
   eine Zeile trifft, entscheidet (`appdoc_filter.decide`); keine trifft → Kategorie. „Direkt
   zugeordnet“ ist eine **Ausnahme**, die die gewählten Regeln darunter aufhebt. Gezählt wird je
   Regel der Firewall einmal, bei der ersten Bedingung des Profils, und nur, wenn keine ihrer
   Zeilen bleibt.
3. **Profile:** eingebaut und unveränderlich **Regelwerk 1** (Stand bis 0.117.0, Vorgabe – ohne
   Einstellung bleibt die Doku gleich) und **Regelwerk 2** (Rückmeldung 2026-10-30: nicht im Pfad
   und über das Internet-Gateway hinaus weder gezeigt noch gezählt, direkt zugeordnete heben das
   auf). Eigene Profile und das aktive im Namensprofil des Mandanten (Wörterbuch-Arten
   `appdoc_ruleset`, `appdoc_ruleset_active`). Änderungen eines eingebauten Profils nur als neues
   Profil; **eine neue Reihenfolge nur als neues Profil** – auch bei eigenen. Bedingungen, die
   neuer sind als ein Profil, kommen darin ausgeschaltet ans Ende.
4. **Kosten nur bei Bedarf:** „Firewall nicht auf dem Weg“ für konkrete Regeln braucht je
   Gegenstelle eine Weg-Suche; sie läuft nur, wenn das aktive Profil die Bedingung eingeschaltet
   hat (Grenzen wie 0.113.2: 3 Gegenstellen, 2 IPs, 400 Wege je Doku; Weg unbekannt → Regel
   bleibt).
5. **Stand der Doku** (ADR 0037): Umschalten, Speichern und Löschen schreiben ein Audit
   (`appdoc_ruleset.*`) – der Fingerabdruck ändert sich, die Doku rechnet neu; Befunde der
   Applikationen des Profils laufen nach.

## Folgen

- Fachentscheide zur Doku sind je Kunde einstellbar; neue Regeln aus Rückmeldungen kommen als
  Bedingung dazu und in ein **neues** eingebautes Profil, bestehende Profile bleiben, wie sie
  sind.
- Regelwerk 2 lässt Regeln „nicht im Pfad“ ganz weg – auch aus „Regeln & Contracts“ und den
  Verbindungen. Der Befund „Regel nie gebraucht“ (`app_rule_unused`) entsteht für konkrete
  Regeln abseits des Wegs dann nicht mehr (sie gehören nicht zur Applikation); mit der Bedingung
  „Firewall nicht auf dem Weg (konkrete Regel)“ ausgeschaltet bleibt er wie bisher.
- Die Zahlen `impossible`, `outbound_broad`, `to_internet` der Doku bleiben (Regelwerk 1 wie
  bisher); neu `summary.filtered` je Bedingung und `summary.ruleset`.
