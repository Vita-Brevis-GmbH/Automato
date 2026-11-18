# ADR 0058 — Kunden, Portale, Benutzer und Rollen nur aus dem Cockpit

## Status

**Angenommen** (2026-11-16), umgesetzt in 1.14.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-16 (wörtlich):

- „die Rollen und User (Sync vom Keycloak einrichten) sollen nur noch im Cockpit sein. Die Kunden
  sollen dies nicht machen können, da es zu gefährlich ist, dass wir neuen Benutzern bei einem
  Problem von aussen geben können. Also in den Tools raus und im Cockpit rein.“
- „Weiter sollen die Kunden und Portale im Automato durch das Cockpit erstellt und verwaltet
  werden, nicht ein Mix wie heute.“

Ergänzt ADR 0018 (Kunden, Kunden-Rollen), ADR 0050 (Kunden-Portal, Realm je Kunde), ADR 0054
(Freigaben aus dem Cockpit, mit Nachträgen) und ADR 0057 (Kopplung). Vertrag mit dem Cockpit:
`docs/vertraege/freigaben.md` und `inventar.md` dort (v1, nur neue optionale Felder). Gegenstück im
Cockpit: Benutzer des Realms lesen („Sync vom Keycloak“) und Rollen je Werkzeug vergeben; der Abbau
mit Frist (`gesperrt`, `entfernen`) und der Ist-Stand (`ist`) sind eigene Teile desselben Vertrags
(ADR 0059, ebenfalls 1.14.0).

## Kontext

- Seit 1.11.0 kommen Portal-Kunden aus dem Cockpit, daneben lassen sie sich weiter in der
  Oberfläche von Automato und mit `front.sh kunde|link|entfernen` von Hand anlegen – ein Mix aus
  drei Orten.
- Rechte vergeben heute auch Kunden selbst: der Kunden-Admin (`customer_admin`) ordnet in
  Automato Benutzer seinem Kunden zu (`customer_user`, `origin = customer`), und ein Kunden-Realm
  darf die Kunden-Rolle über das Token melden (`customer_role` bzw. Realm-Rollen, z. B. aus Gruppen
  von Entra/LDAP). Beides gibt neuen Benutzern Rechte „von aussen“, ohne dass der Betreiber es
  sieht.
- Den Kunden in Automato (ADR 0018, Ausschnitt im Mandanten) musste bisher ein Admin von Automato
  anlegen, bevor das Cockpit ihn zuordnen konnte.

## Entscheidung

1. **Verbunden heisst: Kunden nur aus dem Cockpit.** Ist Automato mit einem Cockpit verbunden
   (gekoppelt, ADR 0057, oder `AUTOMATO_COCKPIT_TOKEN` gesetzt), lehnt das Kunden-Portal Anlegen,
   Ändern und Entfernen ab (`409`, „Kunden entstehen und ändern sich nur noch im Cockpit“); die
   Oberfläche zeigt alles nur lesbar („Ansehen“, kein „+ Kunde“, Hinweis oben), Kunden aus der
   Oberfläche mit „im Cockpit übernehmen“ (das Übernehmen gibt es seit ADR 0054). Ohne Cockpit
   bleibt alles wie bisher (Installationen ohne Cockpit). Mandanten (Umgebungen) bleiben in
   Automato.
2. **Kunde in Automato auf Wunsch des Cockpits** (`zuordnung.kunde_anlegen`, Vorgabe `false`):
   Automato legt im Mandanten der Zuordnung einen Kunden mit dem Namen des Portal-Kunden an und
   nimmt ihn zusätzlich zu den gesendeten Kunden in die Zuordnung. Die Verknüpfung Portal-Kunde
   (Kurzname) + Mandant → Kunde steht in `cockpit_customer` (Migration `0086_cockpit_verwaltung`)
   – **idempotent**: dieselbe Anfrage legt keinen zweiten an, ändert nichts und schreibt kein Audit.
   Neu angelegt: Audit `customer.created` mit Akteur `cockpit`, `quelle: cockpit` und dem
   Portal-Kunden. Wechselt der Mandant, entsteht dort ein neuer; der alte bleibt (Abbau mit Frist,
   ADR 0059: `entfernen` mit `automato_kunde` entfernt alle für diesen Portal-Kunden angelegten
   Kunden, nur ohne Daten). Wird der Kunde in Automato gelöscht, fällt die Verknüpfung mit, und die nächste
   Anfrage legt ihn wieder an (Soll-Stand des Cockpits).
   - **Name belegt:** gibt es im Mandanten schon einen Kunden mit diesem Namen, heisst der neue
     „<Name> (<Kurzname>)“. Ein bestehender Kunde wird **nie still übernommen** – dafür wählt ihn
     das Cockpit in `kunden`. Ist auch der zweite Name belegt, ändert die Anfrage nichts (`422`,
     alles oder nichts).
   - Inventar je Kunde: `zuordnung.kunde_angelegt` (Id des für den aktuellen Mandanten
     angelegten Kunden oder `null`).
3. **Benutzer und Rollen aus dem Cockpit** (`benutzer` je Kunde, höchstens 2000 Einträge):
   Keycloak-Benutzername (dieselbe Regel wie Magister: klein geschrieben gespeichert, 1–255
   Zeichen, druckbar, ohne Leerraum; je Benutzer ein Eintrag) → Kunden-Rolle (`viewer`, `triage`,
   `editor`, `customer_admin`; nie `admin`). Ungültig → `422` (alles oder nichts).
   - **Fehlt bzw. `null`:** keine Aussage – alle Benutzer des Realms bekommen die Rolle der
     Zuordnung (Standard-Rolle des Portals), wie bisher.
   - **Gesetzt (auch `[]`):** nur diese Benutzer haben Rechte, mit ihrer Rolle (nach
     `preferred_username` des Tokens). Alle anderen Benutzer des Realms bekommen keine Rechte
     (fail-closed) und sehen nach der Anmeldung eine eigene Seite: „Für Sie ist in Automato keine
     Rolle vergeben – das macht der Betreiber im Cockpit beim Kunden“ (`/auth/me` →
     `rolle_fehlt`, alle übrigen Endpunkte `403` mit derselben Meldung) – wie `zuordnung_fehlt`
     (ADR 0054 Nachtrag), nur mit anderem Grund.
   - Gilt für Kunden mit eigenem Realm (ADR 0050); im gemeinsamen Realm von Automato gilt die
     bisherige Anmeldung.
   - Gespeichert in `portal_customer.users`, in der Oberfläche nur lesbar; Audit wie die übrigen
     Felder (`portal.customer.*`): Anzahl (`benutzer`) und was sich ändert (`benutzer_neu`,
     `benutzer_entfernt`, `benutzer_rolle`, je Benutzername und Rolle, höchstens 50). Inventar je
     Kunde `benutzer` (aktueller Stand bzw. `null`).
4. **Bei Kunden aus dem Cockpit vergeben Kunden keine Rechte.** „Kunde aus dem Cockpit“ ist ein
   Kunde in Automato, den ein Portal-Kunde aus dem Cockpit in seinem Mandanten nennt, oder den
   Automato für das Cockpit angelegt hat (`store/verwaltung.cockpit_kunden`). Für ihn:
   - **Kein Zuordnen durch den Kunden-Admin:** `POST`/`PATCH`/`DELETE /customer-users` →
     `409` „Benutzer und Rollen dieses Kunden vergibt der Betreiber im Cockpit“. Dem Kunden-Admin
     fehlt für diesen Kunden das Recht `customer_users.manage`; ist er nur bei solchen Kunden
     Kunden-Admin, blendet die Oberfläche „Kunden-Benutzer“ aus (sonst nur seine übrigen Kunden).
     `/auth/me` nennt die Kunden (`cockpit_kunden`).
   - **Zuordnungen eines Kunden-Admins** (`customer_user.origin = customer`, vor dieser Version
     angelegt) **wirken nicht mehr**; die des Betreuers bleiben.
   - **Die Rolle aus dem Token zählt nicht:** bei Portal-Kunden aus dem Cockpit gilt für Benutzer
     des Realms nur `benutzer` bzw. die Standard-Rolle – nicht `customer_role` oder Realm-Rollen
     (die z. B. aus Gruppen des Kunden-Verzeichnisses kommen können).
   - **Notzugang bleibt:** der globale Admin von Automato kann weiterhin Benutzer zuordnen (wie
     das lokale Administrationskonto in Magister).
   Weitere Wege für Kunden-Benutzer, Rechte zu vergeben, gibt es nicht: Kunden-Portal, Freigaben,
   Kopplung, Mandanten, Kunden und Module sind Kunden-Benutzern verschlossen (Freigabeliste,
   ADR 0018), Automato schreibt nie nach Keycloak.
5. **`front.sh` von Hand nur noch mit `--trotzdem`.** Der Soll-Stand für den Front-Proxy nennt
   ein verbundenes Cockpit (`soll.json`: `"verwaltung": "cockpit"`, nachgeführt beim Koppeln,
   Entkoppeln, nach den Freigaben und beim Start). Dann lehnen `front.sh kunde|link|entfernen` ab
   und nennen den Weg über das Cockpit; mit ausdrücklichem `--trotzdem` (an beliebiger Stelle)
   ändern sie wie bisher, mit Warnung „am Cockpit vorbei“. Kunden aus dem Cockpit bzw. der
   Oberfläche bleiben auch mit `--trotzdem` unberührt.
   - **Begründung:** ein hartes Verbot hilft nicht – wer root auf der VM hat, kann die Dateien
     unter `deploy/front/kunden/` ohnehin ändern, dann aber ohne Prüfungen. Der Befehl bleibt der
     **Notweg** (Cockpit nicht erreichbar, Störung), ist aber ausdrücklich und sichtbar; ein so
     angelegter Kunde erscheint im Inventar mit Herkunft `befehl`, das Cockpit kann ihn übernehmen
     bzw. als Altlast abbauen.

Verworfen:

- **Kunden-Admin ganz abschaffen:** die Rolle bleibt für Kunden ohne Cockpit (und pflegt weiter
  Applikationen); bei Kunden aus dem Cockpit fehlt ihr nur das Zuordnen.
- **Bestehenden Kunden gleichen Namens übernehmen:** wäre still und könnte beim Abbau einen von
  Hand gepflegten Kunden treffen.
- **Rolle aus dem Token weiter als Obergrenze bzw. Zusatz:** genau das wäre „Rechte von aussen“.

## Folgen

- **Nur-Lese-Regel unberührt:** alles sind Eingänge in Automatos eigene Datenbank; Automato
  schreibt weiterhin nur nach NetBox (ADR 0010) und nie nach Keycloak.
- Das Token des Cockpits darf jetzt auch Kunden in Automato anlegen und Rollen einzelner Benutzer
  eines Kunden-Realms setzen (höchstens `customer_admin`, nie Verwaltung, nie fremde Mandanten) –
  vertrauenswürdig wie ein Admin des Kunden-Portals (R26).
- Bestehende Installationen mit Cockpit: das Kunden-Portal wird mit dem Update nur lesbar;
  Benutzer eines Realms aus dem Cockpit, die bisher über das Token eine höhere Rolle hatten,
  haben nach dem Update die Standard-Rolle, bis das Cockpit `benutzer` schickt. Zuordnungen von
  Kunden-Admins bei Kunden aus dem Cockpit wirken nicht mehr (der Betreuer ordnet sie bei Bedarf
  neu zu oder das Cockpit nennt die Benutzer).
- Ein älteres Cockpit ohne `benutzer` und `kunde_anlegen`: keine Aussage, wie bisher.
- UI-Tests: der Testserver ist mit einem Test-Cockpit gekoppelt (umschaltbar,
  `e2e/cockpit.ts`), Tests der Oberfläche ohne Cockpit entkoppeln dafür.

## Nachtrag 1.19.2 (2026-11-18): eigener Mandant je Portal-Kunde aus dem Cockpit

Anwender 2026-11-18 (wörtlich): „In Automato drin habe ich in der shared Version nur einen Mandant
(erster Kunde) und keine neuen, alle zusätzlich erstellten werden dann dem ersten zugewiesen und ich
sehe nur Dokumentation und L4–7 Firewall & Dienste Reiter. Ich kann keinen Mandanten auswählen oder
ändern. Es sollte gemäss mir einen neuen Mandanten angelegt und mit dem Account verknüpft werden,
sowie die anderen Mandanten nicht sichtbar machen.“

Ursache: das Cockpit schlug für einen neuen Kunden den einzigen Mandanten vor und liess Automato
darin einen Kunden anlegen (`kunde_anlegen`, Entscheidung 2); einen neuen Mandanten konnte es nicht
wünschen („Mandanten bleiben in Automato“, Entscheidung 1). Die Benutzer des Kunden-Realms sahen
damit den Mandanten des ersten Kunden.

Entscheidung (ändert Entscheidung 1 „Mandanten bleiben in Automato“ für diesen Fall; Gegenstück im
Cockpit: ADR 0013 Nachtrag dort, Vertrag `freigaben.md`/`inventar.md` v1, nur neue optionale
Felder):

1. **Wunsch `zuordnung.mandant_anlegen`** (Wahrheitswert, Vorgabe `false`; `store/cockpit_mandant.py`):
   statt `mandant` (beides zusammen → `422`), optional `mandant_name` (1–200 Zeichen, ohne
   Tabulator/Umbruch; Vorgabe der Name des Portal-Kunden). Automato legt für den Portal-Kunden einen
   **eigenen Mandanten** an (Vorgaben wie in der Oberfläche: IPAM NetBox, DNS Active Directory, ohne
   Kürzel), darin wie bei `kunde_anlegen` einen Kunden mit dem Namen des Portal-Kunden
   (`kunde_anlegen` gilt dann immer), und ordnet beides zu (`scope_source = cockpit`). `kunden` nennt
   nur Kunden dieses Mandanten – vor dem Anlegen leer (sonst `422`). Audit `tenant.created` und
   `customer.created` mit Akteur `cockpit`, `quelle: cockpit` und dem Portal-Kunden.
2. **Idempotent je Kurzname:** `cockpit_tenant` (Migration `0094_cockpit_mandant`) hält Portal-Kunde
   → Mandant fest; ein zweites `PUT /freigaben` nimmt denselben, ändert nichts und schreibt kein
   Audit. Der Name gilt nur beim Anlegen (Automato benennt nie um). Gibt es den Namen schon, heisst
   der neue „<Name> (<Kurzname>)“ – ein bestehender Mandant wird **nie still übernommen** (sonst
   sähen die Benutzer eines Kunden die Daten eines anderen); ist auch das belegt, ändert die Anfrage
   nichts (`422`, alles oder nichts). Wird der Mandant in Automato gelöscht, fällt die Verknüpfung
   mit (und `portal_customer.tenant_id` wird leer); die nächste Anfrage legt wieder einen an.
3. **Rechte unverändert eng:** Benutzer des Kunden-Realms bekommen Mandant, Kunden und Rolle nur aus
   dem Kunden-Portal (ADR 0050, Entscheidung 3 oben) – also nur den eigenen Mandanten; andere
   Mandanten sehen sie nicht (Mandanten-Liste, Endpunkte mit fremdem Mandanten `403`). Nie Rechte
   aus dem Token, nie Admin. Der globale Admin bleibt Notzugang. Ohne eigenen Realm (Realm von
   Automato) gilt wie bisher die Anmeldung von Automato.
4. **Leer:** Quellsysteme, Standorte, Namensprofil und Modul-Schalter gehören dem Mandanten – sie
   kommen beim Wechsel nicht mit; der neue Mandant beginnt leer. Bei einem neuen Kunden entsteht
   das Quellsystem seiner NetBox gleich im eigenen Mandanten (Agent des NetBox-Hosts, ADR 0056).
   Zieht ein bestehender Kunde um, bleibt ein Quellsystem seiner NetBox mit Zugangsdaten im alten
   Mandanten und gilt weiter als angebunden; soll es mit, entfernt es ein Admin dort – der Agent
   liefert den Zugang dann beim nächsten Abgleich für den neuen Mandanten.
5. **Umzug:** stand der Portal-Kunde vorher in einem anderen Mandanten X, entfernt Automato in
   derselben Anfrage den Kunden, den es in X für ihn angelegt hat (`kunde_anlegen`) – nur ohne
   Daten und ohne Zuordnung zu einem anderen Portal-Kunden (dieselbe Prüfung wie der Abbau,
   ADR 0059, Audit `customer.deleted`). Hat er Daten bzw. ist er zugeordnet, bleibt er und wird
   gemeldet: Antwort `umzug` (`stand` `entfernt`/`bleibt`, `grund` `hat_daten`/`zugeordnet`),
   Audit `portal.customer.umzug`, im Inventar unter `ist.automato_kunden` (Seite „Aufräumen“ im
   Cockpit). Nur beim Wechsel – später, auch ohne Daten, entfernt Automato ihn nicht still. Von Hand
   gewählte Kunden in X bleiben immer.
6. **Nie automatisch gelöscht:** einen so angelegten Mandanten entfernt Automato nie selbst. Der
   Abbau aus dem Cockpit (ADR 0059) hat keinen Teil für Mandanten; er entfernt nur den Kunden darin
   (Teil `automato_kunde`, nur ohne Daten; mit `mandant_anlegen` in der Anfrage ist der Teil `aktiv`
   wie bei `kunde_anlegen`). Den leeren Mandanten löscht ein Admin unter Administration →
   Mandanten (Bestätigung mit dem Namen); das Inventar nennt ihn mit `portal_kunde`.
7. **Inventar und Antwort:** `automato.funktionen: ["mandant_anlegen"]` (das Cockpit sendet den
   Wunsch nur dann – ein älteres Automato lehnte eine Zuordnung ohne `mandant` ab), je Mandant der
   Auswahl `portal_kunde` (für welchen Portal-Kunden angelegt, sonst `null`), je Kunde
   `zuordnung.mandant_angelegt` (`{"id", "name"}` oder `null`); `PUT /freigaben` antwortet
   zusätzlich mit `mandanten_angelegt` (`[{"kurzname", "id", "name"}]`, nur in der anlegenden
   Anfrage) und `umzug`. Das Kunden-Portal zeigt beim Mandanten „für das Cockpit angelegt“.

Verworfen:

- **Bestehenden Mandanten gleichen Namens übernehmen:** wäre still und gäbe den Benutzern eines
  Kunden womöglich einen fremden Mandanten.
- **Den angelegten Kunden in X auch später entfernen, sobald er leer ist:** ein Admin, der ihn
  gerade umbaut, verlöre ihn ohne Zutun; gemeldet wird er stattdessen als Rest.
- **Mandanten im Abbau mitlöschen:** ein Mandant trägt Quellsysteme und gelesene Daten; Löschen
  bleibt eine bewusste Handlung in Automato (eigene ADR, falls gewünscht).

Folgen: das Token des Cockpits kann jetzt auch Mandanten anlegen (nie ändern, umbenennen oder
löschen) – vertrauenswürdig wie ein Admin des Kunden-Portals (R26). Die Reihenfolge der Updates
bleibt frei: ein älteres Cockpit sendet weiter `mandant`, ein älteres Automato meldet keine
`funktionen`, dann bietet das Cockpit „Neuer Mandant“ nicht an.
