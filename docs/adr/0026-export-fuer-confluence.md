# ADR 0026 — Export für Confluence: Word und draw.io zum Import von Hand

## Status

Angenommen (2026-10-26), umgesetzt in 0.71.0. Erster Schritt der Umplanung des Anwenders vom
2026-10-26 („Export nach Confluence als Erstes“). Ergänzt ADR 0012/0021 (Applikations-Doku,
Verbindungen, Netzwerkplan) und ADR 0024 (Zonenplan mit Export draw.io/SVG/PDF).

## Kontext

Die Doku aus Automato (Applikationen, Zonenplan) soll in Confluence stehen. Bisher war dafür
ein **neuer Schreibweg** vorgesehen (ROADMAP 0.52.0: „später direkt nach Confluence, eigene
Freigabe“). Verbindliche Vorgabe: nur nach NetBox schreiben, sonst strikt lesen; neue
Schreibwege nur mit eigener ADR und Freigabe.

Entscheid des Anwenders (2026-10-26):

- Die Dokumente **importiert er selbst**, Automato schreibt nicht direkt nach Confluence.
- Zuerst **Applikations-Doku** und **Zonenplan**.
- Er will die Doku vor dem Import verbessern – dafür reichen **eine Word-Datei und eine
  draw.io-Datei**, die er in Word bzw. draw.io anpasst.

## Entscheidung

1. **Kein Schreibweg.** Automato erzeugt Dateien zum Herunterladen; es kennt weder die
   Adresse noch Zugangsdaten eines Confluence. Die Vorgabe „nur nach NetBox schreiben“ bleibt
   unverändert.
2. **Word (.docx)** – von Word, LibreOffice und dem Confluence-Import („Word-Dokument
   importieren“) gelesen. Eigener, kleiner Schreiber (`store/docx_writer.py`, wie beim PDF ohne
   Fremdbibliothek): Titel, Überschriften 1–3 mit Gliederungsebene (Confluence leitet daraus die
   Struktur ab), Absätze, Hinweise, Tabellen mit Kopfzeile (wiederholt auf jeder Seite),
   Querformat A4. Keine Makros, keine eingebetteten Objekte, keine Bilder – der Plan kommt als
   eigene Datei.
3. **draw.io** – der Plan wie bisher als bearbeitbare Zeichnung (`app_plan.drawio` bzw.
   `plan_export.drawio`); in Confluence mit der draw.io-App einfügen bzw. importieren.
4. **Ein Download:** „Für Confluence (Word + draw.io)“ liefert ein ZIP mit beiden Dateien; die
   Word-Datei nennt die draw.io-Datei im Abschnitt „Netzwerkplan“. Einzeln: Word allein.
5. **Inhalt** (`store/word_export.py`), gleich wie Ansicht und Markdown-Export:
   - Applikation: Steckbrief, Übersicht, Systeme, EPGs, Regeln & Contracts (ohne
     routingtechnisch unmögliche Treffer), Abhängigkeiten, Verbindungen Ende zu Ende (mit
     Rückrichtung), Hinweis auf den Netzwerkplan, Kategorien 1–10, ausgeblendete Regeln mit
     Begründung, Befunde; Tabellen höchstens 2000 Zeilen (Rest als Hinweis).
   - Zonenplan: Zonen (Kurzname, Art, Knoten, Netze), Übergänge mit Netzen und Routern,
     Zonenmatrix als Tabelle der Paare (Absicht mit Diensten, erlaubend/sperrend, gegen die
     Absicht, Notiz), Netze ohne Zone; wie die Ansicht gefiltert, auch eine gespeicherte Version.
6. **API:** `GET /applications/{id}/export?format=zip|docx` (viewer, **nicht** für
   Kunden-Benutzer – die Verbindungen zeigen die Infrastruktur), `GET /zones/plan/export` mit
   `format=docx|zip` (Kunden-Benutzer wie bei den anderen Formaten: eigene Sicht, ohne
   Gerätenamen, ohne Mandantenname). Lese-Audit wie bei den übrigen Exporten.

## Folgen

- Der Anwender verbessert Texte und Plan in Word bzw. draw.io und importiert von Hand;
  Änderungen in Confluence fliessen nicht zurück (bewusst).
- Ein direkter Schreibweg nach Confluence bleibt möglich, braucht aber weiterhin eine eigene
  ADR und Freigabe.
- Offen: weitere Inhalte (Doku je System, Verkabelung), der Plan zusätzlich als Bild in der
  Word-Datei.
