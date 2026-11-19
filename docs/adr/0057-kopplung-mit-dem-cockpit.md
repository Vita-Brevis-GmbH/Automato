# ADR 0057 — Kopplung mit dem Cockpit über einen Einmal-Code

## Status

**Angenommen** (2026-11-16), umgesetzt in 1.13.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-16 (Cockpit ADR 0011 „Ein Einstellort“): „Ich möchte allgemein alles solche Dinge
in das globale Cockpit holen, so dass wir nicht mehr überall was einstellen müssen“; Stufe C der
Liste: „Automato mit dem Cockpit koppeln – bisher Token kopieren (`aufbau.sh cockpit-token`) –
künftig Kopplung mit Einmal-Code“; dazu „go für die nächsten 4-5 Punkte“.

## Kontext

- Das Cockpit holt das Inventar (`GET /inventory`, ADR 0053) und schickt die Freigaben
  (`PUT /freigaben`, ADR 0054) mit einem eigenen Token. Bisher: `sudo deploy/aufbau.sh
  cockpit-token` schreibt `AUTOMATO_COCKPIT_TOKEN` in `.env`, startet den Orchestrator neu und
  zeigt das Token einmal; der Betrieb kopiert es von der VM ins Cockpit.
- Das sind ein Schritt auf der VM, ein Neustart und ein Geheimnis, das im Klartext durch die
  Hände des Betriebs (Terminal, Zwischenablage) geht – genau die Art Schritt, die das Cockpit
  ablösen soll.
- Seit 1.11.0 darf dieses Token Kunden des Kunden-Portals anlegen, ändern und entfernen; es ist
  also mehr als ein Lesezugang.

## Entscheidung

1. **Einmal-Code statt Token.** Ein globaler Admin erzeugt in der Oberfläche (Administration →
   Kunden-Portal → Karte „Cockpit“, `POST /kopplung/cockpit/code`) einen Einmal-Code und gibt ihn
   im Cockpit bei der Automato-Instanz ein. Notweg auf der VM: `sudo deploy/aufbau.sh
   cockpit-kopplung` (ruft `automato cockpit-kopplung` im Orchestrator auf; auch `status`,
   `verwerfen`, `entkoppeln`).
   - 12 Zeichen Crockford-Base32 (60 Bit), angezeigt als `XXXX-XXXX-XXXX`; die Eingabe darf klein
     geschrieben sein, ohne Trennzeichen, `O`/`I`/`L` statt `0`/`1`.
   - **15 Minuten** gültig, **einmal** nutzbar, nach **5 Fehlversuchen** ungültig; je Gegenstelle
     höchstens ein offener Code – ein neuer ersetzt ihn, „Code verwerfen“ beendet ihn.
   - Nur einmal angezeigt (Antwort mit `Cache-Control: no-store`), gespeichert nur als SHA-256.
2. **Einlösen ohne andere Anmeldung:** `POST /api/kopplung` mit `{"vertrag": 1, "code": …,
   "cockpit": {"name": …}}`. Die Antwort enthält **einmal** ein **neues** Token (32 Byte zufällig,
   URL-sicher), dazu Version, Umgebung und Adresse der Oberfläche und die Endpunkte. Automato hält
   das Token nur als SHA-256 (Tabelle `coupling`, Migration); ein neues Koppeln ersetzt das
   bisherige Token.
   - **Nichts verraten:** kein Code offen, falscher, abgelaufener, schon benutzter oder gesperrter
     Code – immer dieselbe Antwort (`401`, gleicher Text).
   - **Gedrosselt:** höchstens 10 Versuche je Minute über alle Absender (hinter dem Proxy ist die
     Adresse des Absenders nicht verlässlich), sonst `429` mit `Retry-After`; gedrosselte
     Versuche zählen nicht als Fehlversuch. Zusammen mit 5 Fehlversuchen je Code und einem
     offenen Code ist Raten aussichtslos (5 von 2^60).
3. **Prüfung des Tokens** (`auth/cockpit.py`, `GET /inventory`, `PUT /freigaben`): gültig ist das
   gekoppelte Token **oder** wie bisher `AUTOMATO_COCKPIT_TOKEN` aus `.env` (rückwärtskompatibel;
   künftig leer lassen und koppeln). Ohne beides gibt es die Endpunkte weiterhin nicht (`404`),
   ein falsches Token ergibt `401`. **Entkoppeln** in der Oberfläche bzw. mit `aufbau.sh
   cockpit-kopplung entkoppeln` macht das gekoppelte Token sofort ungültig. „Zuletzt benutzt“
   führt Automato höchstens alle 5 Minuten nach.
4. **Audit ohne Geheimnisse:** `cockpit.kopplung.code`, `.code_verworfen`, `.fehlversuch`
   (Versuch, gesperrt), `.abgelaufen`, `.gekoppelt` (Akteur `cockpit`, Name, ersetzt, wer den
   Code erzeugt hat), `.entkoppelt` – nie Code oder Token (Allowlist). Diese Einträge ändern die
   Doku nicht und zählen nicht für den Stand der Doku (ADR 0037).
5. **Wiederverwendbar je Gegenstelle:** Tabelle `coupling` mit einer Zeile je `peer`,
   `store/kopplung.py` mit der Gegenstelle als Parameter, Audit `<peer>.kopplung.*`. Heute nur das
   Cockpit; dasselbe Muster ist für den Agenten des NetBox-Hosts (`AUTOMATO_NETBOX_HOST_TOKEN`,
   ADR 0054) vorgesehen, aber nicht gebaut.

Verworfen:

- **Token weiter kopieren** – bleibt nur als früherer Weg (`aufbau.sh cockpit-token`).
- **Das Cockpit erzeugt das Token und schickt es an Automato:** braucht trotzdem einen ersten
  Vertrauensanker; der Einmal-Code ist dieser Anker, und das Geheimnis entsteht dort, wo es
  geprüft wird.
- **Drossel je Absender:** hinter Proxy bzw. WAF nicht verlässlich; die Fehlversuche je Code
  begrenzen ohnehin.

## Folgen

- Kein Schritt auf der VM, kein Neustart und kein Token in `.env` mehr nötig; das Cockpit legt
  sein Token verschlüsselt ab (dort Sache des Cockpits).
- Ein neuer Eingang ohne Anmeldung (`POST /api/kopplung`): wirkt nur mit einem offenen Code, den
  ein Admin von Automato erzeugt hat. Wer die Oberfläche als Admin bedienen kann, kann damit das
  Cockpit koppeln – dieselbe Berechtigung wie das Kunden-Portal; das Audit nennt, wer den Code
  erzeugt hat.
- **Nur-Lese-Regel unberührt:** die Kopplung ist ein Eingang in Automatos eigene Datenbank, kein
  Schreibweg nach aussen.
- Die Drossel gilt je Orchestrator-Prozess (heute einer); mit mehreren Prozessen gilt sie je
  Prozess, die Fehlversuche je Code bleiben in der Datenbank.
- Vertrag für die Gegenseite: `docs/vertraege/kopplung.md` im Cockpit (v1); neue Felder nur
  optional, eine andere Bedeutung = `vertrag: 2`, zuerst in Automato, dann im Cockpit.
