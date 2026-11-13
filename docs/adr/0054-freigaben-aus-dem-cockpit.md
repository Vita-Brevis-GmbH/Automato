# ADR 0054 — Freigaben aus dem Cockpit, NetBox als Werkzeug

## Status

**Angenommen** (2026-11-13), umgesetzt in 1.11.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-13: „starte mal P2“ nach dem Vorschlag „Ein Ort für Kunden: im Cockpit Kunden
anlegen und Werkzeuge wählen; Automato übernimmt“. Gegenstück im Cockpit: P2 „Zugänge und
Freigaben“, Kundenregister (ADR 0005 dort), Vertrag `docs/vertraege/freigaben.md` (v1).

## Kontext

- Seit 1.3.0 (ADR 0050) pflegt das Kunden-Portal von Automato die Kunden des Kundenpfads:
  Kurzname, Name, Werkzeuge, Zugriff (1.5.0) und Realm gehen als `soll.json` an den Front-Proxy,
  Mandant, Kunden, Standard-Rolle und Kacheln bestimmen, was Benutzer eines Kunden-Realms sehen.
- Dieselben Kunden stehen im Cockpit (Magister-Mandanten, Bereitstellung, Sicherung). Zwei Orte
  für denselben Kunden laufen auseinander; das Cockpit wird zum Kundenregister für alle Werkzeuge.
- Das Cockpit kennt Automato bisher nur lesend (`GET /inventory`, ADR 0053, eigenes Token).
- NetBox (`-nb`) war bisher immer dabei, sobald der Front-Proxy einen NetBox-Host kennt; nicht
  jeder Kunde hat eine NetBox.

## Entscheidung

1. **Ein neuer Eingang: `PUT /freigaben`** mit demselben Token wie das Inventar
   (`AUTOMATO_COCKPIT_TOKEN`, Bearer; ohne Token 404, falsch 401, nie mit der Anmeldung einer
   Person). Das Cockpit schickt immer den **ganzen Stand** seiner Kunden für diese Installation;
   dieselbe Anfrage zweimal ändert nichts (kein Audit, der Soll-Stand bleibt). **Alles oder
   nichts:** ein ungültiger Kunde oder ein Realm, der einem Kunden aus der Oberfläche gehört,
   ändert nichts (`422` mit `fehler[]`). Geprüft wird wie in der Oberfläche (`store/portal.py`),
   `front.sh` prüft den Soll-Stand danach noch einmal.
2. **Wer Autor wofür ist:** das Cockpit für Kurzname, Name, Werkzeuge, Zugriff und Realm;
   Automato bleibt Autor für Mandant, Kunden (ADR 0018), Standard-Rolle und weitere Kacheln – das
   Cockpit kennt Automatos Mandanten nicht. `portal_customer.source` hält die Herkunft fest
   (`oberflaeche` | `cockpit`). In der Oberfläche sind die Felder des Cockpits bei solchen Kunden
   nur lesbar, entfernt werden sie nur im Cockpit (`409`).
3. **Übernahme und Entfernen:** gibt es den Kurznamen schon aus der Oberfläche, übernimmt das
   Cockpit ihn (Herkunft Cockpit; Mandant, Kunden, Rolle und Kacheln bleiben). Kunden aus dem
   Cockpit, die nicht mehr in der Anfrage stehen, entfernt Automato; Kunden aus der Oberfläche
   bleiben unberührt. Jede Änderung im Audit (`portal.customer.*`, Akteur `cockpit`, im Detail
   `quelle: cockpit`), danach Soll-Stand für den Front-Proxy (nur bei neuem Inhalt) und die
   Antwort, ob der Host-Agent ihn schon übernommen hat.
4. **NetBox als Werkzeug** (`portal_customer.nb`, im Soll-Stand `nb`, in `kunden/<k>.conf`
   `NB=ja|nein`, `front.sh kunde … --ohne-nb`): ohne NetBox keine `-nb`-Adresse, keine Kachel,
   keine Prüfung und kein Callback des Anmelde-Tors. Vorgabe an – bestehende Kunden und ältere
   Dateien ohne den Wert behalten ihre NetBox.
5. **Gegenrichtung:** das Inventar nennt je Kunde zusätzlich Herkunft (`cockpit`, `oberflaeche`,
   `befehl`), Werkzeuge, Zugriff, Netze und Realm (aus `topologie.json`) – so übernimmt das
   Cockpit Kunden, die es noch nicht kennt, ohne Realm oder Zugriff zu verlieren. Vertrag bleibt
   v1 (nur neue optionale Felder).

6. **NetBox je Kunde über einen Agenten auf dem NetBox-Host** (nach Vorschlag des Cockpit-P2: „die
   NetBox je Kunde legt der Host-Agent dort an“): wie der Host-Agent des Front-Proxys ändert der
   Orchestrator nichts auf dem Host. Der NetBox-Host holt `GET /netbox-host` (Kunden mit NetBox,
   nur Kurzname, Name und Domain) mit einem **eigenen** Token (`AUTOMATO_NETBOX_HOST_TOKEN`) und
   legt fehlende Instanzen selbst an (`netbox-host.sh sync`, systemd-Timer). Er entfernt nie
   selbst; Löschen bleibt ein Handgriff mit Sicherung (`loeschen --ja`).

## Folgen

- **Nur-Lese-Regel unberührt:** `PUT /freigaben` ist ein Eingang in Automatos eigene Datenbank,
  kein Schreibweg nach aussen. Automato schreibt weiterhin nur nach NetBox (ADR 0010); der
  Front-Proxy übernimmt den Soll-Stand wie bisher selbst (`front.sh sync`, ADR 0050).
- Das Token des Cockpits erlaubt jetzt auch, Kunden des Kunden-Portals anzulegen, zu ändern und
  zu entfernen (nicht Mandant, Kunden und Rolle). Es bleibt nur in `.env` und im Cockpit
  (verschlüsselt); ein erneutes `aufbau.sh cockpit-token` macht das alte ungültig.
- `front.sh` behandelt Kunden mit `QUELLE=cockpit` wie Kunden aus der Oberfläche: nicht per
  Befehl änderbar, entfernt, wenn sie im Soll-Stand fehlen.
- Der NetBox-Host braucht HTTPS zur Oberfläche von Automato; sein Token erlaubt nur die Liste
  der Kurznamen (kein Schreiben, keine Realms). Die Tokens für Automato in der neuen NetBox
  (`netbox-host.sh token`) bleiben ein Handgriff.
- Neue Felder im Vertrag nur optional; eine andere Bedeutung = `vertrag: 2`, zuerst in Automato,
  dann im Cockpit.
