# ADR 0046 — Regeln je Zone aufteilen, Zusatz der Seite und Auswahl spezifisch/unspezifisch

## Status

**Angenommen** (2026-11-03), umgesetzt in 0.125.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-03: „Firewall Regeln manchmal nicht am richtigen Ort … vor allem wenn mehrere
Ziele drin sind, also solche von anderen Tenants, von VPN Verbindungen und dem MPLS. Eventuell
sollte man … pro Zone einstellen können, was man sehen will. Zum Beispiel Source Zone DC →
Spezifisch und oder Unspezifisch … Damit das klappt, muss man noch unterscheiden, ob die Source
das DC der Applikation ist, oder eben ein anderes DC Zone.“ Auf die Rückfrage: „A … lieber
aufgeteilt, nicht dass wir die Ziele im MPLS auch auf dem anderen Tenant sehen“ und „B …
abgewählte Zeilen nur unter Regeln & Contracts – so ist es korrekt. Ich kann in den Regeln und in
den Kategorien auch noch festlegen, dass die unspezifischen Zugriffe dort zu sehen sind. Es geht
also nichts verloren.“ Ergänzt ADR 0035 (Kategorien als Richtungen) und ADR 0043 (Regelwerk).

## Kontext

- Eine Firewall-Regel stand je Seite des Systems als **eine** Zeile mit allen Gegenstellen. Lagen
  diese in Zonen verschiedener Zonentypen (Clients, VPN, MPLS, anderer Tenant), war der Zonentyp
  der Seite nicht eindeutig und die ganze Zeile fiel in „Unspezifische Zugriffe“ – auch die
  Gegenstellen, die klar zu einer Kategorie gehören.
- Kategorien mit gleichem Zonentyp auf beiden Seiten (`DC → DC`) unterschieden nicht, ob die
  Applikation die Quelle oder das Ziel ist.
- Ob eine Kategorie auch unspezifische Zugriffe (any, grosse Netze, vzAny …) zeigt, war nur über
  das Regelwerk für alle Kategorien gleich einstellbar.

## Entscheid

1. **Aufteilen je Zone bzw. Kategorie:** je Gegenstelle einer Firewall-Regel (Objekt bzw. Wert
   der Regel, Gruppen aufgelöst) die Zonen und die Kategorie wie bisher (Zonentyp, Richtung,
   eigener Security Tenant); je Kategorie (und Auswahl, Punkt 3) **eine Zeile mit nur ihren
   Gegenstellen**, Zone und Gegenstellen dieser Zeile. Eine Gegenstelle über mehrere Zonentypen
   (any, Netz über mehrere Zonen) bleibt unspezifisch. Unter „Regeln & Contracts“ bleibt es eine
   Regel; ihre Treffer nennen alle Kategorien. ACI-Contracts bleiben je Seite eine Zeile.
2. **Zusatz am Zonentyp** in der Richtung einer Kategorie: `DC (Applikation)` = die Seite der
   Systeme der Applikation, `DC (andere)` = die Gegenstelle, ohne Zusatz beide (wie bisher). Die
   genaueste Richtung gewinnt: Zonentyp mit passendem Zusatz vor ohne, ohne vor `*`, bei
   Gleichstand die kleinere Nummer. Gespeichert deutsch (wie die übrigen Werte), angezeigt in der
   Sprache der Oberfläche. Gegenstellen in der eigenen Zone bleiben „innerhalb des Security
   Tenants“.
3. **Auswahl je Kategorie und Seite** (Quelle, Ziel): **spezifisch** und/oder **unspezifisch**,
   Vorgabe beides (keine Änderung ohne Einstellung), mindestens eines je Seite; auch bei den drei
   festen Kategorien.
   - Seite der Applikation: spezifisch = das System direkt (Host, Gruppe, EPG); unspezifisch =
     Subnetz, any, vzAny, Preferred Group (wie der Filter „Bezug“).
   - Gegenstelle: unspezifisch = any, 0.0.0.0/0 bzw. ::/0, Wert nicht ganz in einer Zone (Netz
     über mehrere Zonen); spezifisch = Host bzw. Netz in einer Zone, Adresse ausserhalb aller
     Zonen, EPGs.
4. **Wirkung der Auswahl:** sie gilt **nach dem Regelwerk** für Zeilen, die in der Kategorie
   stünden: abgewählte stehen **nur als Treffer** unter „Regeln & Contracts“ (mit Hinweis „Nicht
   gezeigt in Kategorie …“), nichts geht verloren. Direkt zugeordnete Regeln stehen immer in
   ihrer Kategorie. Die Auswahl ist eine Einstellung der Kategorie, keine Bedingung des Regelwerks
   – die eingebauten Regelwerke bleiben unverändert (ADR 0043); der Vergleich der Regelwerke nennt
   sie als Grund.
5. **Pflege** im Namensprofil → Kategorien der Applikations-Doku (Admin, Audit wie bisher);
   gespeichert nur Abweichungen (`src_scope`, `dst_scope` im Wörterbuch-Eintrag der Kategorie).
   Keine Migration.

## Ergänzung 0.126.0 – Reihenfolge

Anwender 2026-11-03: „Bei den Kategorien wäre es schön, wenn ich die Reihenfolge noch anpassen
könnte“, auf den Vorschlag „Ordnen wie beim Regelwerk“, „Bei Gleichstand ja“, „Nummerierung
fortlaufend“.

6. **Reihenfolge** je Namensprofil (↑/↓ wie beim Regelwerk, auch die festen): Doku und
   Word-Export zeigen die Kategorien so, **Nummern fortlaufend** nach der Position. Die Nummer
   der Kategorie bleibt ihre **feste Kennung** (ausgeblendete Regeln, Wörterbuch, Treffer); nur
   die Anzeige nummeriert neu.
7. **Gleichstand** zweier gleich genauer Richtungen: die weiter oben gewinnt (ohne eigene
   Reihenfolge wie bisher die kleinere Nummer).
8. Gespeichert als ein Eintrag `order` der Art `appdoc_category`, nur wenn die Reihenfolge von
   der Nummer abweicht; unbekannte Nummern werden übergangen, fehlende stehen am Ende.

## Folgen

- Regeln mit Gegenstellen in mehreren Zonen stehen nach dem Update in mehreren Kategorien statt in
  „Unspezifische Zugriffe“; der Stand der Doku ändert sich (Fingerabdruck) beim nächsten Lauf bzw.
  Speichern des Katalogs.
- Eine Regel kann in derselben Kategorie zweimal stehen, wenn ihre Gegenstellen dort teils
  spezifisch, teils unspezifisch sind (selten: any und ein Host in einer Regel).
- Docs: `docs/BETRIEB.md` (Kategorien der Applikations-Doku), Tests
  `orchestrator/tests/test_appdoc_split.py`, `e2e/release_0125.spec.ts`.
- Verworfen: die Auswahl als neue Bedingung im Regelwerk (in bestehenden Profilen ausgeschaltet
  – ohne Wirkung, bis der Anwender sie einschaltet, und dann für alle Kategorien gleich); eine
  Zeile mit allen Gegenstellen in jeder passenden Kategorie (Ziele im MPLS auch beim anderen
  Tenant).
