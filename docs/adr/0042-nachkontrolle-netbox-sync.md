# ADR 0042 — Nachkontrolle des NetBox-Syncs: geschrieben = gelesen?

## Status

**Angenommen** (2026-10-30), umgesetzt in 0.115.0 – zur Abnahme durch den Anwender.

Roadmap Version 1 Nr. 5 („Schreibpfad mit Nachkontrolle“, Entscheid des Anwenders 2026-10-29: in
Version 1). Anwender 2026-10-30: „go 115“, gewählt die Nachkontrolle. Ergänzt ADR 0010 (NetBox
als einziger Schreibweg, Vorschau → Übernehmen), ADR 0036 (Modul Schreibpfad) und ADR 0039
(Prüfungen je Modul). Migration 0071 (`sync_plan.verification`). Kein neuer Schreibweg.

## Kontext

- Bis 0.114 schrieb „Übernehmen“ die Aktionen des Plans und stiess danach ein Neu-Einlesen der
  NetBox an. Ob jedes Objekt so in NetBox steht, wie es geschrieben wurde, prüfte niemand: eine
  NetBox kann Werte nach dem Schreiben ändern (Custom Validators, Event Rules, Skripte), still
  verwerfen oder Löschungen nicht ausführen (fehlende Rechte des Tokens, Schutz-Regeln).
- Die Vorschau zeigt solche Abweichungen erst beim nächsten Sync – als „ändern“, ohne den Hinweis,
  dass genau das schon einmal geschrieben wurde.

## Entscheid

1. **Zurücklesen über den Lesepfad:** Nach dem Schreiben öffnet der Orchestrator einen **eigenen**
   Client zur Ziel-NetBox mit dem Lese-Token (ohne Lese-Token mit dem Schreib-Token). An diesem
   Client hängt der Nur-Lese-Schutz (`sot_gateway.readonly.enforce_read_only`): POST, PUT, PATCH
   und DELETE brechen **vor dem Senden** ab. Die Nachkontrolle sieht nur `read` (Klasse
   `netbox_sync.Reader`).
2. **Vergleich je geschriebenem Objekt:** Mit dem frisch gelesenen Stand rechnet Automato den
   Abgleich wie für eine Vorschau neu und prüft jede Aktion des übernommenen Plans:
   - *angelegt* bzw. *geändert* → muss jetzt „unverändert“ sein; sonst **fehlt in NetBox**
     (wieder „anlegen“) bzw. **andere Werte** (Felder alt → neu);
   - *gelöscht* → darf nicht mehr da sein; sonst **noch in NetBox**;
   - will Automato ein Objekt inzwischen anders (Daten während der Übernahme geändert), ist es
     **nicht prüfbar** und keine Abweichung.
3. **Ergebnis am Plan** (`verification`: geprüft, in Ordnung, nicht prüfbar, Abweichungen je Art,
   höchstens 200 Abweichungen mit Feldern) und in der Meldung: „Übernommen und nachkontrolliert:
   geschrieben = gelesen (n Objekte)“ bzw. „Übernommen – Nachkontrolle: n Abweichungen von m
   Objekten“. Im Audit `verified` und `deviations`.
4. **Befund** `netbox_sync_mismatch` (Warnung, Gruppe „NetBox-Sync“) je Abweichung der letzten
   Übernahme je NetBox – Prüfung `netbox_nachkontrolle` im Modul Schreibpfad
   (`app/checks/schreibpfad/`), im Teil-Durchgang „Quellen“ sofort nach der Übernahme und im
   Abgleich nach jedem Lauf. Mit der nächsten Übernahme gilt deren Nachkontrolle.
5. **Scheitert das Zurücklesen** (NetBox nicht erreichbar, kein Token), bleibt die Übernahme
   gültig: Status „übernommen“, Meldung „Nachkontrolle nicht möglich: …“; Neu-Einlesen wie bisher.
   Ein Abbruch während der Nachkontrolle ändert am Geschriebenen nichts – der Plan wird
   „übernommen“.

## Folgen

- Die Übernahme dauert etwa so lange länger, wie eine Vorschau braucht (NetBox wird einmal mehr
  gelesen).
- Abweichungen sind sofort sichtbar – am Plan und unter Findings – statt erst beim nächsten Sync.
- Kein neuer Schreibweg: die Nachkontrolle kann technisch nicht schreiben; der einzige Schreibweg
  bleibt „Übernehmen“ (ADR 0010).
