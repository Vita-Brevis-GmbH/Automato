# ADR 0059 — Abbau mit Sperre und Löschaufträgen, Ist-Stand der Instanzen

## Status

**Angenommen** (2026-11-16), umgesetzt in 1.14.0 (zusammen mit ADR 0058) – zur Abnahme durch den
Anwender.

Anwender 2026-11-16 auf den Vorschlag „Abbau-Plan, Daten nie ohne Sicherung, Seite ‚Aufräumen‘,
Frist 7 Tage ‚zum Entfernen vorgemerkt‘, für Dev ‚sofort‘“: **„Einstellbar, aber sonst ok, Dann
sollten wir auch noch die Instanzen auslesen, damit wir sehen können, ob wir altlasten haben“**;
dazu **„Weiter sollen die Kunden und Portale im Automato durch das Cockpit erstellt und verwaltet
werden, nicht ein Mix wie heute.“** Gegenstück im Cockpit: Frist und Abbau, Seite „Aufräumen“;
Verträge `freigaben.md` und `inventar.md` (v1, neue Felder optional). Ergänzt ADR 0050 (Host-Agent),
0053 (Inventar), 0054 (Freigaben, NetBox-Host „entfernt nie selbst“), 0055 (Tor-Geheimnis), 0056.

## Kontext

- Seit 1.11.0 entfernt Automato einen Kunden aus dem Kunden-Portal, sobald das Cockpit ihn nicht
  mehr schickt; der Front-Proxy nimmt seine Adressen weg. Zurück bleiben: die NetBox-Instanz auf dem
  NetBox-Host (der Agent meldet sie nur als „ohne Freigabe“), das Tor-Geheimnis des Realms auf der
  VM (`tor/<realm>.env`) und ein Kunde in Automato, falls Automato ihn für den Portal-Kunden
  angelegt hat (`zuordnung.kunde_anlegen`, anderer Zweig dieser Runde). Löschen war ein Handgriff je
  Werkzeug, und was vergessen ging, sah niemand.
- Ein Kunde, der sofort verschwindet, lässt keinen Weg zurück, wenn er versehentlich entfernt wurde.
- Der Orchestrator ändert nie selbst etwas auf einem Host (ADR 0050); der NetBox-Host entfernt nie
  selbst (ADR 0054 Punkt 6).

## Entscheidung

1. **Sperre während der Frist (`gesperrt`).** Merkt das Cockpit einen Kunden zum Entfernen vor,
   schickt es ihn bis zum Ablauf seiner Frist mit `gesperrt: true` (`PUT /freigaben`, je Kunde,
   Vorgabe false). Automato speichert es (`portal_customer.gesperrt`), das Kunden-Portal markiert
   ihn, Benutzer seines Realms bekommen keine Rechte mehr (wie ein unbekannter Realm), und der
   Soll-Stand trägt `gesperrt` (nur, wenn gesperrt). `front.sh sync` sperrt **alle** Adressen des
   Kunden: nach der Sperre nach Absender und dem Zugriff antwortet jede mit 403 „Zugang gesperrt“
   – auch die NetBox-API mit Token und Automato selbst. Daten, Realm und Client-Secret bleiben;
   rückgängig bis zum Ende der Frist (Cockpit schickt `gesperrt: false`).
2. **Löschauftrag nach der Frist (`entfernen`, oberste Ebene).** Nach der Frist nimmt das Cockpit
   den Kunden aus `kunden[]` und schickt `entfernen: [{kurzname, netbox, automato_kunde, tor}]`, so
   lange, bis das Inventar „erledigt“ meldet. Automato prüft die Form (alles oder nichts, `422`)
   und **nimmt nur an**, was das Cockpit selbst entfernt hat: einen Kunden aus dem Cockpit, der in
   derselben Anfrage wegfällt, oder einen, dessen letzter Eintrag `portal.customer.*` im Audit
   `deleted` durch das Cockpit ist. **Nie** einen Kunden aus der Oberfläche, nie einen Kurznamen in
   `kunden[]`, nie einen, den Automato nie vom Cockpit hatte (`abgelehnt` mit Grund
   `oberflaeche`, `in_kunden`, `nicht_vom_cockpit`). Gibt es den Kurznamen wieder als Portal-Kunden,
   ist der Auftrag **aufgehoben** – ein neuer Kunde gleichen Namens wird nie abgebaut. Ohne
   `entfernen` keine Aussage (Aufträge bleiben); ein Auftrag, den das Cockpit nicht mehr sendet,
   endet. Die **Frist** führt das Cockpit (einstellbar, Vorgabe 7 Tage, 0 = sofort); Automato kann
   sie nicht prüfen und verlässt sich darauf, dass nur das Cockpit mit seinem Token Aufträge
   schickt.
3. **Ausgeführt von den Hosts, je Auftrag einmal.** Jeder Auftrag trägt eine zufällige Kennung.
   - **NetBox (neuer Schreibweg auf dem NetBox-Host):** `GET /netbox-host` nennt offene Aufträge in
     `loeschen` (`kurzname`, `auftrag`). Der Agent löscht ohne Rückfrage **nur diese** – wie
     `loeschen <kunde> --ja`, also **immer zuerst eine frische Sicherung** (ohne Empfänger bzw. wenn
     sie scheitert, nichts), die Sicherungen bleiben –, nie einen noch freigegebenen Kunden, nie bei
     fremder Domain, und merkt sich die Kennung (`geloescht/<kunde>`): eine danach von Hand wieder
     angelegte Instanz bleibt. Danach meldet er seinen Ist-Stand (Punkt 5).
   - **Tor:** der Soll-Stand trägt `tor_entfernen` (`realm`, `auftrag`) für Realms, die kein
     Portal-Kunde nutzt. `front.sh sync` prüft noch einmal (kein Kunde, auch keiner von Hand, und
     nie der Realm von Automato) und entfernt `tor/<realm>.env` samt Siegel; die Kennung bleibt in
     `tor/<realm>.entfernt`. Der Container des Tors fällt schon weg, sobald kein Kunde den Realm
     nutzt. **Verwaiste Tor-Dateien ohne Auftrag meldet der Front-Proxy nur** (`pruefen`,
     `status.json`, Inventar) – nie still gelöscht.
   - **Kunde in Automato:** nur der, den Automato für diesen Portal-Kunden angelegt hat
     (Verknüpfung aus `kunde_anlegen`, beim Annehmen gemerkt), und nur ohne Daten (Selektoren,
     Zugehörigkeit, Benutzer-Zuordnungen, eigene Zonen, eigene Anordnung des Zonenplans) und ohne
     Zuordnung zu einem anderen Portal-Kunden; sonst bleibt er, der Teil bleibt offen (`hat_daten`
     bzw. `zugeordnet`) und wird bei jedem Abgleich wieder geprüft. Audit `customer.deleted`, Akteur
     `cockpit`. Gelesene Daten der Quellsysteme gehören dem Mandanten und bleiben ohnehin.
4. **Stand je Teil.** `offen` mit dem Grund, worauf er wartet (`netbox_host`, `kein_agent`,
   `loeschen_fehlgeschlagen`, `front_proxy`, `hat_daten`, `zugeordnet`), bzw. `erledigt` (mit Grund
   wie `geloescht`, `entfernt`, `nicht_vorhanden`, `keiner_angelegt`, `realm_in_verwendung`,
   `haupt_realm`, `kein_front_proxy`). Erledigt bleibt erledigt. NetBox gilt als erledigt, sobald
   eine Meldung des NetBox-Hosts **nach** dem Annehmen die Instanz nicht mehr nennt; das Tor, sobald
   der Front-Status keine Datei mehr für den Realm nennt. Audit `portal.abbau.*` (angenommen,
   geändert, Fortschritt, erledigt, aufgehoben) – zählt nicht für den Stand der Doku (ADR 0037).
5. **Ist-Stand der Instanzen (`GET /inventory` → `ist`).** Was die Werkzeuge wirklich haben, damit
   das Cockpit Altlasten findet:
   - `front`: aus `status.json` (`front.sh` schreibt `ist`): Kunden, gesperrte, Realms mit
     Tor-Geheimnis, Realms mit Container des Tors, Container ohne bekannten Realm, verwaiste
     Tor-Dateien, zuletzt auf Auftrag entfernt bzw. abgelehnt;
   - `netbox_host`: letzte Meldung des Agenten nach jedem `sync` – **`PUT /netbox-host/stand`** mit
     dem Token des NetBox-Hosts, nur Kurznamen und Zeit (`instanzen`, `ohne_freigabe`, `geloescht`,
     `loeschen_fehlgeschlagen`), geprüft wie fremde Eingabe;
   - `automato_kunden`: Kunden in Automato mit Mandant, für welchen Portal-Kunden angelegt
     (`von_cockpit`, `portal_kunde`), `hat_daten` und welche Daten;
   - `entfernen`: Stand der Aufträge, dazu die zuletzt abgelehnten.
   Je Kunde zusätzlich `gesperrt` (aus `topologie.json`).

## Folgen

- **Neuer Schreibweg auf dem NetBox-Host:** er löscht eine NetBox jetzt auch auf Auftrag aus
  Automato. Grenzen: nur Kunden, die das Cockpit selbst entfernt hat; nur nach der Frist des
  Cockpits; nie ohne frische Sicherung; je Auftrag einmal. Wer das Token des Cockpits hat, kann
  damit nach dem Entfernen eines Kunden aus dem Cockpit dessen NetBox löschen lassen – mit Sicherung,
  wiederherstellbar (`wiederherstellen`). Risiko R28.
- **Nur-Lese-Regel unberührt:** Automato schreibt weiterhin nur nach NetBox über den NetBox-Sync
  (ADR 0010); den Löschauftrag führt der NetBox-Host auf seiner eigenen Instanz aus, wie er sie
  anlegt (ADR 0054). Automato ändert auf den Hosts nichts selbst.
- **Das Token des NetBox-Hosts** darf jetzt auch den Ist-Stand melden – nur Kurznamen und Zeit; eine
  falsche Meldung kann einen NetBox-Teil fälschlich als erledigt erscheinen lassen (Aufträge enden
  dann früher, gelöscht wird dadurch nichts).
- **Kunde in Automato aus `zuordnung.kunde_anlegen` (ADR 0058):** welche Kunden Automato für
  einen Portal-Kunden angelegt hat, steht in `cockpit_customer` (`ist_stand.angelegte_kunden`
  über `store/verwaltung.py`; je Mandant einer, auch frühere Mandanten). Die Verknüpfung hängt am
  Kurznamen, nicht am Portal-Kunden – sie bleibt, wenn die Freigaben den Portal-Kunden entfernen,
  und fällt erst mit dem Kunden in Automato (bzw. seinem Mandanten). Der Abbau merkt sich die Ids
  zusätzlich beim Annehmen, bevor die Freigaben derselben Anfrage den Portal-Kunden entfernen;
  entfernt wird aber nur, was noch verknüpft ist – eine gemerkte Id, die es nur noch als anderen
  Kunden gibt (SQLite vergibt Ids gelöschter Kunden neu), nie.
  Dieselbe Verknüpfung liefert `zuordnung.kunde_angelegt` je Kunde und `von_cockpit` im Ist-Stand.
  Ohne Verknüpfung (Kunde von Hand bzw. vom Cockpit nur gewählt) entfernt der Abbau keinen Kunden
  in Automato (`keiner_angelegt`).
- Realms in Keycloak löscht das Cockpit selbst (nur selbst angelegte, mit Export vorher); Automato
  meldet nur, was auf der VM liegt.
- Vertrag v1 bleibt (nur optionale Felder); ein älteres Cockpit sendet weder `gesperrt` noch
  `entfernen`, ein älterer Agent ignoriert `loeschen` und meldet keinen Stand (der NetBox-Teil
  bleibt dann offen: `netbox_host`).

## Nachtrag 1.14.0 (2026-11-16): Werkzeug abwählen entfernt auch die Instanz

Anwender 2026-11-16: **„weiter müssen wir schauen, dass das Cockpit alles orchestriert, also die
Tools zufügt aber auch entfernt, wir haben auf dem dev Server nun leichen liegen“**; zum Abbau mit
Frist: „Einstellbar, aber sonst ok“.

**Lücke:** `entfernen` galt nur für Kunden, die nicht mehr in `kunden[]` stehen. Wählte der
Betreiber bei einem **bleibenden** Kunden ein Werkzeug ab (z. B. NetBox), blieb die Instanz liegen
– der Agent des NetBox-Hosts meldete sie nur „ohne Freigabe“.

**Entscheidung** (Vertrag v1 bleibt, nur neue Werte):

1. **Teile bleibender Kunden.** Ein Eintrag in `entfernen` darf auch einen Kunden nennen, der in
   `kunden[]` derselben Anfrage steht (bisher abgelehnt mit `in_kunden` – der Grund entfällt).
   Dann gilt ein Teil nur, wenn sein Werkzeug bei diesem Kunden **aus** ist:
   - `netbox` nur ohne `nb` → der Agent des NetBox-Hosts löscht die NetBox (mit Sicherung, je
     Auftrag einmal, wie bisher). Seine Regel heisst jetzt „nie einen Kunden, den Automato noch
     **mit NetBox** freigibt“ (`kunden` derselben Antwort) – das galt schon technisch, weil
     `GET /netbox-host` nur Kunden mit NetBox nennt; Automato nennt den Auftrag auch nur dann
     (`netbox_loeschen`).
   - `automato_kunde` nur ohne `doc` und ohne `zuordnung.kunde_anlegen` in derselben Anfrage (dann
     will das Cockpit den Kunden noch) → den für ihn angelegten Kunden entfernen, nur ohne Daten
     (wie bisher). Die Zuordnung des bleibenden Portal-Kunden selbst zählt dabei nicht, solange
     sie aus Automato stammt (das Cockpit sendet keine `zuordnung`); die Id fällt dort mit weg.
     Nennt das Cockpit den Kunden selbst in `zuordnung.kunden`, bleibt er (`zugeordnet`) – sonst
     wäre seine nächste Anfrage ungültig (unbekannter Kunde, `422`).
   - `tor` für den **früheren** Realm des Kunden (Realm gewechselt bzw. entfernt; aus dem Verlauf
     `portal.customer.*` im Audit) – entfernt wie bisher nur, wenn ihn kein Kunde mehr nutzt
     (sonst erledigt mit `realm_in_verwendung`). Gab es nie einen anderen Realm und hat der Kunde
     einen, ist das Tor in Verwendung (`aktiv`); ohne jeden Realm nichts zu tun (`kein_realm`).
   - Für Kunden aus der Oberfläche weiterhin nie (`oberflaeche`, auch wenn das Cockpit ihn in
     derselben Anfrage übernimmt – der Teil-Abbau gilt erst danach).
2. **Abgelehnt je Teil.** Ein Teil, dessen Werkzeug an ist, steht in der Antwort als
   `{"kurzname", "grund": "aktiv", "teil": "netbox"|"automato_kunde"|"tor"}`; die übrigen Teile
   desselben Eintrags gelten (der Kurzname steht dann auch in `angenommen`). Im Inventar
   (`ist.entfernen`) stehen abgelehnte Teile in `teile` mit Stand `abgelehnt`.
3. **Wieder gewählt = aufgehoben, je Teil.** Nutzt der Kunde dieses Kurznamens das Werkzeug eines
   noch offenen Teils (wieder), endet der Auftrag für diesen Teil: `erledigt` mit Grund
   `aufgehoben` (Audit `portal.abbau.aufgehoben`, Grund `wieder_da`, je Teil), die Hosts bekommen
   ihn nicht mehr, die Instanz bleibt mit ihren Daten. Schickt das Cockpit den Teil nach erneutem
   Abwählen wieder, gilt er wieder – mit **neuer Kennung** (die Hosts führen jede Kennung nur
   einmal aus). Ein schon **ausgeführter** Teil, dessen Werkzeug wieder an ist, fällt aus dem
   Auftrag (die Instanz von jetzt ist eine andere); ein späterer Auftrag ist ein neuer. Hatte der
   Agent den Auftrag beim Wiederwählen schon abgeholt und meldet die NetBox danach als gelöscht,
   zeigt der Stand `geloescht` statt `aufgehoben`.
   - Das gilt für jeden Auftrag, auch für den Abbau eines ganzen Kunden: Gibt es den Kurznamen
     wieder als Portal-Kunden, enden nur die Teile, deren Werkzeug er nutzt (bisher: der ganze
     Auftrag). Hat der neue Kunde keine NetBox, geht die des alten also trotzdem – sie gehört
     ihm nicht.
4. **Vom Teil- zum ganzen Abbau.** Je Kurzname gibt es einen Auftrag. Entfernt das Cockpit den
   Kunden später ganz, wird der offene Auftrag zum ganzen: neue Teile kommen dazu (neue Kennung),
   der Realm ist der des entfernten Kunden – ausser das Tor eines früheren Realms ist gerade noch
   offen (dann bleibt dieser; das übrige Tor-Geheimnis meldet der Front-Proxy als verwaist).

**Folgen:**

- Der Schreibweg auf dem NetBox-Host wird nicht breiter, nur häufiger: er löscht weiterhin nur auf
  Auftrag, nur mit Sicherung, nur Kunden ohne NetBox-Freigabe. Neu kann das Cockpit damit die
  NetBox eines Kunden löschen lassen, der bleibt – nach seiner Frist, rückgängig bis dahin
  (Werkzeug wieder wählen), wiederherstellbar aus der Sicherung (R28 ergänzt).
- Das Cockpit sendet je abgewähltem Werkzeug genau dessen Teil, bis `ist.entfernen` den Teil als
  erledigt meldet (`geloescht`, `nicht_vorhanden`, `entfernt`, … bzw. `aufgehoben`); „von Hand“
  entfällt für NetBox, Automato und Tor.
- `front.sh` bleibt unverändert (es entfernt Tor-Geheimnisse ohnehin je Realm).
