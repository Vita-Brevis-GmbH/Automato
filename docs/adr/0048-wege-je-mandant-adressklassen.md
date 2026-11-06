# ADR 0048 — Wege einmal je Mandant, Urteile je Adressklasse, keine stillen Grenzen

## Status

**Angenommen** (2026-11-06), umgesetzt in 0.140.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-06: „Die Frage ist auch, ob wir nicht anders die Wege berechnen müssen und so die
Last minimieren. Wir rechnen heute nicht alle Wege durch und lassen auch sonst immer wieder viel
liegen, dies ist nicht zuverlässig und daher gefährlich. Die Frage ist, ob das Tool nicht für so
grosse Umgebungen bereit ist.“ Auf den Vorschlag in drei Stufen: „Alle drei Stufen in 140
umsetzen.“ Ergänzt ADR 0021 (Verbindungen Ende zu Ende), ADR 0037 (Doku als Stand) und
ADR 0047 (Vorrechnen in Durchgängen).

## Kontext

- Jede Applikation rechnete ihre Wege für sich (`app_paths.build`, `paths.trace`): der Weg vom
  Monitoring, AD, DNS oder Backup zu einem Server kam in fast jeder Applikation vor und wurde jedes
  Mal neu gerechnet; dazu je Applikation die Policies aller Firewalls neu geladen. Im Betrieb des
  Anwenders rund 3 s je Weg („89 von 120 Wegen in 297 s“).
- Damit es bedienbar blieb, kürzten Grenzen die Prüfung – teils mit Hinweis (2000 Verbindungen je
  Applikation), teils still: 6 Gegenstellen je Regel und Seite, 3 Dienste je Regel, 8 genauer
  geroutete Teile eines Netzes, 3 Subnetze einer externen EPG; in der Doku 400 Wege, 3 Gegenstellen
  und 2 Adressen je Regel. In der Doku geht das in die sichere Richtung (Regel bleibt stehen), bei
  Verbindungen und Befunden in die gefährliche: was nicht geprüft wird, zeigt keine Abweichung.
- Die Urteile je Hop wurden je genauer IP gemerkt – dieselbe Policy-Prüfung für jeden Server eines
  Netzes neu.

## Entscheid

1. **Keine stillen Grenzen** (`app_paths._Cuts`, Befund `app_coverage`): jede Grenze, die greift,
   wird gezählt – je Art (Gegenstellen, Dienste, Teile, Verbindungen je Applikation, Fehler im
   Programm) mit Zahl der Regeln und der fehlenden Teile. Die Applikation ist dann **unvollständig
   geprüft**: Hinweis und rotes Zeichen in den Verbindungen, Befund „Applikation: unvollständig
   geprüft“ (Warnung, Link zur Applikation). Die Doku zählt Regeln, deren Weg eine Grenze nicht
   prüfen liess (`summary.path_unchecked`, Zeichen im Kopf der Doku). Grenzen angehoben:
   Gegenstellen 64 (bisher 6), Dienste 16 (3), Teile 32 (8), Subnetze externer EPGs 64 (3); Doku
   64 Gegenstellen, 8 Adressen, 20 000 Wege (3, 2, 400). Neue Grenzen nur mit Zählung.
2. **Wege einmal je Mandant und Datenstand** (`store/path_store.py`): ein Speicher je Mandant,
   solange sein Datenstand gilt (wie ADR 0037: letzter Lauf, Quellsysteme, letzte Änderung, dazu
   die Version). Er merkt die Wege (`paths.trace`, je Aufruf und Sprache, gepackt; jeder Abruf
   bekommt eine eigene Kopie), die geladenen Policies je Firewall/VDOM (reine Daten) und die
   Urteile je Hop. Gesetzt in der Berechnung von Doku und Verbindungen (`doc_snapshots.compute`,
   im Hintergrund und beim Öffnen) und im Abgleich von Hand; die Weg-Suche rechnet wie bisher für
   sich. Ein neuer Lauf bzw. eine Änderung = neuer Datenstand, der alte Speicher fällt weg.
   Grösse: `AUTOMATO_PATH_STORE_MB` (Vorgabe 256 MB), darüber fallen die ältesten Wege weg und
   werden bei Bedarf neu gerechnet – eine Grenze für den Speicher, nicht für die Prüfung.
3. **Urteile je Adressklasse** (`policy_check.Checker.addr_class`): je Firewall/VDOM und Seite
   bilden die Grenzen aller Adressbereiche der Policies Klassen; Adressen derselben Klasse treffen
   genau dieselben Objekte, ihr Urteil je Hop ist gleich (exakt, keine Näherung). Dazu Familie und
   privat/öffentlich (Land, Internet Service). Ein Netz: Klasse seiner ersten und letzten Adresse.
   Das Urteil wird je Klasse einmal gerechnet und für alle Server, Netze und Applikationen
   wiederverwendet. ACI-Contracts weiterhin je IP.

## Folgen

- Rein lesend wie bisher; nichts davon wird gespeichert, nach einem Neustart baut sich der
  Speicher beim ersten Vorrechnen neu auf.
- Ein Ergebnis aus dem Speicher ist dasselbe wie neu gerechnet (Test `test_path_store.py`:
  gleiche Verbindungen mit und ohne Speicher, gleiches Urteil je Klasse).
- Was den Datenstand nicht ändert, darf die Wege nicht ändern – gilt schon für den Stand der Doku
  (ADR 0037: Audit-Eintrag, Lauf oder `store.touch(app)`).
- Unvollständig geprüfte Applikationen stehen als Befund da; die Grenze `AUTOMATO_APP_TRACES_MAX`
  (2000) bleibt einstellbar (0 = ohne Grenze).
- Mehr Gegenstellen und Dienste je Regel = mehr Verbindungen je Applikation; dank Speicher und
  Klassen ist der einzelne Weg billig, die Zahl der Wege steigt aber.
