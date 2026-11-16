# ADR 0055 — Client-Secret des Anmelde-Tors versiegelt aus dem Cockpit

## Status

**Angenommen** (2026-11-16), umgesetzt in 1.13.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-16: „Bitte auch einbauen, dass das Geheimnis auch erneuert werden kann … Bitte
anfügen, dass alles korrigiert werden kann“ und auf den Vorschlag (das Cockpit liefert das
Geheimnis von `automato-front` versiegelt an die Automato-VM, nur die VM kann es entschlüsseln;
ändert die Regel aus ADR 0051 „Client-Secrets nur auf der VM (`front.sh tor`)“): „go der
Vorschlag“. Ändert ADR 0051 Punkt 7, ergänzt ADR 0054 (Freigaben) und ADR 0053 (Inventar).
Gegenstück im Cockpit: Vertrag `docs/vertraege/freigaben.md` und `inventar.md` (v1, neue Felder
optional).

## Kontext

- Seit 1.4.0 (ADR 0051) braucht jeder Realm eines Kunden ein Client-Secret für `automato-front`
  auf der VM. Es kam nur mit `sudo deploy/front/front.sh tor <realm>` dorthin (Eingabe unsichtbar,
  Datei nur root): je neuer Kunde ein Handgriff auf der VM, und ein in Keycloak erneuertes Secret
  sperrte den Kunden, bis jemand es auf der VM nachtrug.
- Seit 1.11.0 (ADR 0054) legt das Cockpit Kunden und Realms an und schickt die Freigaben an
  Automato; es liest Keycloak mit eigenem Zugang (`view-clients`) und kennt das Secret ohnehin.
- Die Regel „nie in Oberfläche, Datenbank oder Repo“ soll im Kern bleiben: der Orchestrator ist
  im Internet-nahen Pfad, seine Datenbank wird gesichert, und er darf nie Rechte auf dem Host
  bekommen (ADR 0050).

## Entscheid

1. **Empfangsschlüssel der VM:** `front.sh` legt, sobald das Anmelde-Tor an ist, ein
   RSA-Schlüsselpaar (3072 Bit) an: privat `deploy/front/tor/empfang.key` (root, 600), öffentlich
   `empfang.pub`. Kennung = SHA-256 über die DER-Form des öffentlichen Schlüssels. Neu mit
   `front.sh tor-empfang neu`; `front.sh tor-empfang` zeigt Kennung und Herkunft je Realm.
2. **Inventar:** `front.sh` schreibt den öffentlichen Schlüssel mit Kennung nach `topologie.json`;
   `GET /inventory` gibt ihn als `front.tor_empfang` heraus (nur ein öffentlicher RSA-Schlüssel ab
   3072 Bit, dessen Kennung stimmt) und je Realm, ob ein Secret da ist und ob es aus dem Cockpit
   kam (`front.tor_realms`, aus `status.json`).
3. **Versiegelt:** das Cockpit versiegelt `{"realm","client","secret"}` mit RSA-OAEP (SHA-256,
   MGF1-SHA-256) und schickt es mit den Freigaben (`tor_geheimnisse`: Realm, Kennung, Chiffrat).
   **Im Klartext steht das Secret nur auf der VM** (`tor/<realm>.env`, root, 600); Datenbank
   (`portal_customer.tor_secret`), Soll-Stand (`soll.json`) und Audit halten nur das Chiffrat bzw.
   Realm und Kennung.
4. **Prüfen in Automato:** Form und Realm alles oder nichts (`422`, wie die Kunden); eine andere
   Kennung als die aktuelle der VM lehnt nur diesen Eintrag ab (`schluessel_veraltet`,
   `kein_empfang`) – das Cockpit versiegelt mit dem Schlüssel aus dem nächsten Inventar neu.
   Audit `portal.tor_secret.updated` nur bei neuem Chiffrat.
5. **Entschlüsseln nur `front.sh sync` als root:** `openssl pkeyutl` → Python über Pipes, das
   Secret steht in keiner Variable der Shell, keinem Argument, keiner Ausgabe, keinem Log und
   nicht in `status.json`. Geprüft wird noch einmal (Realm eines Kunden mit `QUELLE=cockpit`,
   Client `automato-front`, Form des Secrets); sonst verworfen mit Grund ohne Inhalt. Übernommen
   nur, wenn das Secret anders ist; das Cookie-Secret bleibt (Sitzungen bleiben), danach startet
   der Container des Tors dieses Realms neu.
6. **Von Hand bleibt:** `front.sh tor <realm>` geht weiterhin und gilt, bis das Cockpit ein anderes
   Chiffrat schickt (die VM merkt sich die Prüfsumme des zuletzt verarbeiteten Chiffrats je Realm).
   `front.sh tor`, `tor-empfang` und `pruefen` zeigen je Realm „von Hand“ bzw. „aus dem Cockpit“.

## Folgen

- Neue Kunden und erneuerte Secrets aus dem Cockpit brauchen keinen Handgriff mehr auf der VM.
- **Wer das Token des Cockpits hat**, kann für die Realms von Kunden aus dem Cockpit ein
  Client-Secret setzen – aber nur eines, das auch in Keycloak gilt; ein falsches lässt nur die
  Anmeldung in diesem Realm scheitern (das Tor holt kein Token), es öffnet nichts. Die Kunden
  selbst konnte dieses Token schon seit 1.11.0 anlegen und entfernen (ADR 0054).
- **Wer den Orchestrator bzw. seine Datenbank übernimmt**, sieht nur Chiffrate; ohne den
  privaten Schlüssel der VM nützen sie nichts. Der private Schlüssel verlässt die VM nie; eine
  Sicherung von `deploy/front/tor/` enthält ihn wie bisher die Secrets.
- **Schlüsselwechsel:** `front.sh tor-empfang neu` – die hinterlegten Secrets gelten weiter, das
  Cockpit versiegelt beim nächsten Abgleich mit dem neuen Schlüssel; bis dahin gesendete
  Chiffrate mit der alten Kennung lehnt Automato ab bzw. verwirft die VM.
- Das Klartext-JSON hat bei RSA 3072 mit OAEP-SHA-256 höchstens 318 Byte; Secrets von Keycloak
  (32 Zeichen) passen weit hinein.
- Risiko im Register: R25 (`docs/RISKS.md`).
