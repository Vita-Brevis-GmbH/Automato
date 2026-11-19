# ADR 0056 — Der NetBox-Host richtet SSO und den Zugang für Automato selbst ein

## Status

**Angenommen** (2026-11-16), umgesetzt in 1.13.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-16 (Cockpit ADR 0011 „Ein Einstellort“): „alles solche Dinge in das globale
Cockpit holen, so dass wir nicht mehr überall was einstellen müssen“; Stufe B „NetBox: SSO und
Token für Automato – bisher NetBox-Host (`netbox-host.sh sso`, `token`) – künftig Agent des
NetBox-Hosts“; „go für die nächsten 4-5 Punkte“.

## Kontext

- Seit 1.11.0 (ADR 0054 Punkt 6) holt der Agent des NetBox-Hosts (`netbox-host.sh sync`,
  systemd-Timer) mit einem eigenen Token `GET /netbox-host` und legt fehlende NetBox-Instanzen an.
- Danach blieben zwei Handgriffe: `netbox-host.sh sso <kunde>` für Instanzen ohne Anmeldung über
  das Tor (angelegt vor 1.4.0 oder mit `SSO=aus`) und `netbox-host.sh token <kunde>` – die Tokens
  erschienen auf dem Bildschirm und mussten in Automato beim Quellsystem NetBox des Mandanten
  eingetragen werden. Jeder Handgriff ist eine Stelle, an der etwas vergessen geht; kopierte
  Tokens landen in Terminal-Verläufen und Notizen.
- Regel (`CLAUDE.md`): Zugangsdaten zu Geräten und Referenzquellen nie in `.env`/Umgebung, nur am
  Quellsystem – verschlüsselt (`app/credentials.py`, ADR 0009), nur schreibbar, nie ausgegeben.
- Automato erreicht die NetBox eines Kunden über den Front-Proxy unter
  `https://<kurzname>-nb.<AUTOMATO_FRONT_DOMAIN>` (1.10.0, ADR 0053 Punkt 4).

## Entscheidung

1. **Anmeldung über das Tor im Abgleich:** bei `SSO=an` (`host.conf`) schreibt der Agent die
   Einstellungen jeder Instanz, deren Datei fehlt oder veraltet ist, startet nur dann deren
   NetBox-Dienste neu und legt danach Gruppen und Rechte an (wie `sso <kunde>`). Ein Stand je
   Instanz (`kunden/<k>/sso-stand`: `neustart` | `ok`) macht das idempotent: ein fehlgeschlagener
   Neustart wird beim nächsten Abgleich wiederholt, eine vor 1.13.0 eingerichtete Instanz bekommt
   nur die Rechte noch einmal (ohne Neustart). Nie, solange der Proxy des NetBox-Hosts gefälschte
   Anmelde-Kopfzeilen nicht entfernt (Caddyfile ohne `X-Automato-User` → Warnung, `init`). `SSO=aus`
   entfernt weiterhin nur der Befehl `sso`.
2. **Stand des Zugangs je Kunde:** `GET /netbox-host` nennt je Kunde zusätzlich `zugang`:
   - `fehlt` – Automato nimmt einen Zugang an;
   - `vorhanden` – ein Quellsystem NetBox mit dem Namen `<kurzname>-nb.<domain>` hat Zugangsdaten
     (von Hand oder früher geliefert; Port und Pfad zählen nicht, der Mandant auch nicht);
   - `kein_mandant` – der Kunde hat im Kunden-Portal noch keinen Mandanten (ab 1.13.0 kann ihn
     auch das Cockpit setzen – massgebend ist `portal_customer.tenant_id`);
   - `andere_adresse` – der Mandant hat ein Quellsystem NetBox mit einem anderen Namen;
   - `keine_adresse` – Automato kennt die Domain des Front-Proxys nicht.
3. **Tokens erst, wenn Automato sie annimmt:** nur bei `fehlt` legt der Agent wie `token` die
   Benutzer `automato-collector` (nur lesen) und `automato-sync` (nur die Objekttypen des Syncs,
   ADR 0010) mit neuen Tokens an und liefert sie sofort mit **`PUT /netbox-host/zugang`**. Fehlt
   der Mandant, entstehen keine Tokens; der Agent fragt beim nächsten Abgleich wieder.
   *Abgewogen gegen* „sofort erzeugen, auf dem NetBox-Host nur für root zwischenspeichern“: das
   wäre ein weiteres Geheimnis im Ruhezustand, das `sichern konfig` (ganzes `NBH_DIR`) in jede
   Sicherung mitnähme und das veraltet, sobald jemand `token` von Hand aufruft. Ein verworfenes
   Token kostet nichts – das nächste ersetzt es in NetBox.
4. **Eingang `PUT /netbox-host/zugang`** mit demselben Token wie `GET /netbox-host`
   (`AUTOMATO_NETBOX_HOST_TOKEN`, Bearer; ohne 404, falsch 401) – siehe Vertrag unten. Nur für
   Kunden mit NetBox (sonst 404), nur für ein Quellsystem der Plattform NetBox, nie für andere
   Quellen; den Inhalt prüft Automato selbst (Kurzname, Tokens), Fehler nennen nur das Feld.
5. **Gespeichert wie von Hand:** beim Mandanten des Portal-Kunden ein Quellsystem NetBox – ein
   vorhandenes mit diesem Namen ohne Zugangsdaten bekommt sie, sonst ein neues „NetBox“ (bzw.
   „NetBox <kurzname>“) mit Adresse `https://<kurzname>-nb.<domain>`, TLS-Prüfung an, zentral
   gelesen, Rolle wie in der Oberfläche ohne Angabe (führend nur ohne andere führende Quelle).
   Lese-Token als `token`, Sync-Token als `write_token`, verschlüsselt (`app/credentials.py`). Die
   **Adresse bestimmt Automato** aus seiner Domain, nicht der NetBox-Host.
6. **Nie still überschreiben:** vorhandene Zugangsdaten (auch teilweise, auch bei einem anderen
   Mandanten) bleiben; ein Quellsystem NetBox des Mandanten mit anderer Adresse bleibt unberührt.
   Zweimal dieselbe Anfrage ändert nichts (`unveraendert`, kein zweites Audit).
7. **Geheimnisse auf dem Weg:** der Agent gibt die Tokens nur über die Umgebung an
   `docker compose exec` (`-e NAME` ohne Wert, wie `sync-user.sh`) und über stdin an curl –
   nie in eine Befehlszeile, Datei, Statusdatei oder Ausgabe; Fehlermeldungen von NetBox beim
   Anlegen bleiben aus dem Protokoll. `netbox-host.sh token` übergibt sie seit 1.13.0 ebenso.
   Audit nur `source.netbox_host_access` (Akteur `netbox-host`) mit Kunde, Quellsystem, Mandant,
   ob neu angelegt und Herkunft – nie die Tokens.
8. **Von Hand bleibt:** `netbox-host.sh token <kunde>` als Notweg (ersetzt die Tokens – in
   Automato dann von Hand nachführen). Zugang erneuern: in Automato beim Quellsystem NetBox „Alle
   hinterlegten Tokens entfernen“ – der Agent liefert beim nächsten Abgleich neue (Stand `fehlt`).
   Den Zeitplan des neuen Quellsystems setzt der Betrieb wie bei jedem anderen.

## Vertrag (Automato ↔ Agent des NetBox-Hosts, beide in diesem Repo)

Das Cockpit sieht diese Schnittstelle nicht; `vertrag` bleibt 1 (nur ein optionales Feld neu –
ein älterer Agent ignoriert `zugang`, ein neuer Agent übergibt ohne `zugang` nichts).

```json
GET /api/netbox-host
{"vertrag": 1, "domain": "mgmt.example.ch",
 "kunden": [{"kurzname": "kunde-a", "name": "Kunde A", "zugang": "fehlt"}]}

PUT /api/netbox-host/zugang
{"kurzname": "kunde-a", "lesen": "<40 Hex>", "sync": "<40 Hex>"}

200 {"status": "uebernommen" | "unveraendert" | "vorhanden", "detail": "…"}
409 {"status": "kein_mandant" | "andere_adresse" | "keine_adresse", "detail": "…"}
404 kein Kunde mit NetBox · 422 Inhalt ungültig (ohne Werte) · 401/404 Token
```

## Folgen

- **Nur-Lese-Regel unberührt:** der neue Eingang schreibt in Automatos eigene Datenbank. Automato
  liest NetBox weiterhin bzw. schreibt nur über den NetBox-Sync (Vorschau → Übernehmen, ADR 0010);
  die Benutzer in NetBox legt wie bisher der NetBox-Host auf seiner eigenen Instanz an.
- **Das Token des NetBox-Hosts darf jetzt Zugangsdaten setzen** – nur für NetBox-Quellen von
  Kunden mit NetBox, nur wo noch keine sind, an einer Adresse, die Automato selbst bestimmt. Es
  kann vorhandene Zugangsdaten weder lesen noch ändern. Wer das Token stiehlt, könnte für einen
  Mandanten ohne NetBox-Zugang eigene Tokens hinterlegen – Automato spräche damit nur die echte
  NetBox des Kunden an (sein Lesen und der Sync liefen dann unter fremden Tokens bzw. schlügen
  fehl). Schutz wie bisher: Token nur in `.env` bzw. `/opt/netbox-host/automato.token` (nur
  root), `aufbau.sh netbox-host-token` macht das alte ungültig.
- **Rest-Risiko Gleichzeitigkeit:** trägt jemand genau zwischen Abruf und Lieferung (Sekunden) von
  Hand einen Zugang ein, hat NetBox schon neue Tokens – Automato behält den Handeintrag (`vorhanden`)
  und der Agent warnt („Verbindung prüfen“).
- `pruefen` auf dem NetBox-Host zeigt je letztem Abgleich: Anmeldung eingerichtet, Zugang
  übergeben, ohne Mandant (folgt), von Hand klären. `INSTALLATION-NETBOX-HOST.md` 4a.
- Die Stufe B „NetBox: SSO und Token für Automato“ der Cockpit-ADR 0011 ist damit umgesetzt; das
  Cockpit braucht dafür nichts Neues (es setzt den Mandanten, Automato nimmt den Zugang dann an).
