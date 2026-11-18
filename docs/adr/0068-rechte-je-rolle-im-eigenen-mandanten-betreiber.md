# ADR 0068 — Rechte je Rolle im eigenen Mandanten, Betreiber mit ganzer Plattform

## Status

**Angenommen** (2026-11-18) – zur Abnahme durch den Anwender.

Anwender 2026-11-18:

- „Warum sieht ein Kundenadmin und auch automato-admin nur so wenig, der Admin sollte einiges mehr
  sehen und auch bearbeiten können. Es gibt noch Viewer und Operator, dies sollen stufenweise
  weniger zu sehen bekommen. Der Betreiber soll alles sehen und anpassen können und auch einen Link
  zu der ganzen Plattform erhalten, dies wenn es nicht eine eigene Instanz ist, sondern nur ein
  Mandanten.“
- „Admins alles sehen, und anpassen können in ihrem Mandanten, Operatoren sollen alles sehen und nur
  das einlesen anstossen können. Viewer sollen nur das sehen, wo heute der Admin sieht.“

Neuer Weg zu Rechten (`CLAUDE.md`: „jeder neue Weg zu Rechten nur mit ADR“). Ergänzt ADR 0007
(Rollen, Mandanten-Scoping), ADR 0018 (Kunden-Benutzer, feinere Rollen), ADR 0050 (Realm je Kunde)
und ADR 0058 (Benutzer und Rollen aus dem Cockpit, eigener Mandant 1.19.2); im Cockpit ADR 0017
(Betreiber über Keycloak, Nachtrag „Betreiber auf der ganzen Plattform“).

## Kontext

- Unter `<kunde>-doc` ist jede Anmeldung ein Kunden-Benutzer (`auth/oidc.realm_principal`), auch der
  Betreiber (`<name>@betrieb`, Rolle aus `benutzer` des Cockpits, Vorgabe `customer_admin`). Kunden
  sehen nur die Ansichten für Kunden (Doku und L4–7), der Server erlaubt nur die Freigabeliste
  `auth/customer_routes.py` und filtert alles auf den Ausschnitt ihrer Kunden.
- Seit 1.19.2 legt Automato für einen Portal-Kunden aus dem Cockpit auf Wunsch einen **eigenen
  Mandanten** an (`cockpit_tenant`, ADR 0058 Nachtrag). Darin liegen nur seine Daten – der
  Kunden-Filter schneidet dort nur noch weg, was ohnehin ihm gehört (Netze ohne Zone, Inventar, L1–L3).
- In gemeinsamen Mandanten sind die Daten nicht nach Kunde getrennt; dort muss der Kunden-Filter
  bleiben.
- Betreuer-Rechte (`viewer`/`operator`/`admin` aus dem Token) gibt es nur im eigenen Realm von
  Automato (ADR 0007). Ein Betreiber unter `<kunde>-doc` kommt nie an die übrigen Mandanten.
- Nebenbefund: scheitert `GET /auth/me`, fiel die Oberfläche auf die Sicht aus dem Token zurück
  (fail-open in der Anzeige; der Server prüfte trotzdem).

## Entscheidung

### 1. Kunden-Rollen stufenweise, neue Rolle `customer_operator`

Rangfolge (`CUSTOMER_ROLES`, aufsteigend): `viewer` < `triage` < `editor` < `customer_operator` <
`customer_admin`. Gespeichert wie bisher (Kunden-Portal, `benutzer`, `customer_user`); die Spalten
`portal_customer.default_role` und `customer_user.role` fassen jetzt 32 Zeichen (Migration
`0095_rolle_operator`, Rückweg: `customer_operator` wird `viewer`, fail-closed). Das Inventar nennt
die Rolle in `automato.rollen` – ein älteres Automato kennt sie nicht (unbekannt = `viewer`), das
Cockpit bietet sie dann nicht an.

| Rolle | Anzeige | gemeinsamer Mandant (wie bisher, Kunden-Filter) | **eigener Mandant** |
|---|---|---|---|
| `viewer` | Viewer | Doku des Kunden lesen | wie heute: Doku und L4–7 des Kunden |
| `triage` | Triage | + eigene Findings quittieren | wie viewer + Findings quittieren |
| `editor` | Editor | + eigene Applikationen pflegen | wie viewer + Applikationen pflegen |
| `customer_operator` | Operator | nur lesen (wie viewer – Einlesen gibt es im gemeinsamen Mandanten nicht) | **alles des Mandanten lesen** (L1–L7, Weg-Suche, Wege vergleichen, Abgleich, Läufe, Quellsysteme ohne Geheimnisse, Zeitplan); **nur** Einlesen anstossen |
| `customer_admin` | Admin | wie bisher (+ Kunden-Benutzer, nicht bei Kunden aus dem Cockpit) | **alles des Mandanten lesen und anpassen** (siehe 4.) |

`customer_operator` schliesst bewusst **nicht** `triage`/`editor` ein („nur das Einlesen
anstossen“); er steht in der Rangfolge darüber, weil er im eigenen Mandanten mehr sieht.

### 2. Eigener Mandant

„Eigener Mandant“ eines Benutzers aus dem Realm eines Portal-Kunden = der Mandant, den Automato für
genau diesen Portal-Kunden angelegt hat (`cockpit_tenant.short_name` = Kurzname, `tenant_id` =
Mandant der Zuordnung). Er wird beim Lesen der Realms bestimmt (`store/portal.realm_scopes`,
`RealmScope.own_tenant`), nie aus dem Token. Alle anderen Mandanten – auch einer, den ein Admin von
Hand gewählt hat – gelten als gemeinsam: dort bleibt alles wie bisher.

- **Eigene Instanz** (ADR 0061): die Instanz kennt ihren Kunden (`AUTOMATO_INSTANZ_KUNDE`), aber ihr
  Realm ist dort der Realm der Installation – Benutzer kommen dort nicht als Kunden-Benutzer, sondern
  mit den Rollen aus dem Token (`admin`/`operator`/`viewer`, ADR 0061 Nr. 8). Daran ändert diese
  ADR nichts; Kunden-Realms gibt es in einer Instanz nicht.
- **Zuordnungen in Automato** (`customer_user`) und der Claim `customer_ids` (Realm von Automato)
  bleiben Kunden-Sicht: `customer_operator` liest dort nur.

### 3. Mandanten-Benutzer: Prüfung auf dem Server

Benutzer eines Kunden-Realms mit `customer_operator` bzw. `customer_admin` **im eigenen Mandanten**
bekommen einen eigenen Principal: Betreuer-Sicht ohne Kunden-Filter, **auf genau diesen Mandanten
begrenzt** (`tenant_ids = (eigener,)`, `customer_ids = None`), nie `admin` (die Rolle bleibt global
und kommt nie aus einem Kunden-Realm), mit der **Mandanten-Rolle** `Principal.tenant_role`.

1. **Positivliste je Endpunkt** (`auth/tenant_routes.py`, wie die Freigabeliste der Kunden):
   Methode und Pfad → Recht `mandant.read` (lesen), `mandant.collect` (Einlesen anstossen) bzw.
   `mandant.manage` (anpassen). `require_role` schickt jeden Mandanten-Benutzer durch dieses Tor
   (`deps._tenant_gate`): nicht gelistet = 403 (fail-closed, auch für später dazukommende
   Endpunkte); ein Endpunkt für Betreuer-Operatoren öffnet sich nie mit `mandant.read`, einer für
   Admins nur mit `mandant.manage`. Die Plattform-Endpunkte stehen ausdrücklich in
   `PLATFORM_ROUTES`; ein Test verlangt, dass **jeder** Endpunkt mit Rollenprüfung in genau einer der
   beiden Listen steht – ein neuer Endpunkt braucht so einen Entscheid.
2. **Mandant:** jeder Endpunkt grenzt wie bisher mit `tenant_scope` ein (ADR 0007 D3, wie für
   Betreuer mit `tenant_ids`); das Tor weist zusätzlich jedes `tenant_id` eines anderen Mandanten in
   der Anfrage ab. Objekte anderer Mandanten → 403/404 (Tests je Endpunktgruppe).
3. **Rechte je Rolle:** `customer_operator` = `mandant.read` + `mandant.collect`; `customer_admin` =
   alle drei.
4. **Lesezugriffe immer protokolliert** wie bei Kunden-Benutzern (`audit/reads.py`), unabhängig von
   `audit_reads` des Mandanten.

### 4. Was der Admin im eigenen Mandanten anpassen darf (`mandant.manage`)

Alles, was ein Betreuer für einen Mandanten tut: Applikationen (anlegen, Import, löschen,
Mitglieder), Findings (quittieren, von Hand anlegen, Abgleich rechnen), Prüfungen und Module des
Mandanten, Zonen und Zonenplan (auch Versionen löschen), Wege-Zuordnungen, Ausgänge, L3Out-Firewalls,
Regel-Paare, Spanning-Tree-Soll, Kabelnummern, Reservierungen, Verbund paralleler Firewalls,
Standorte, Quellsysteme (Verbindung prüfen, Läufe abbrechen) und den **NetBox-Sync** des Mandanten
(Vorschau → Übernehmen, bestehender Schreibweg ADR 0010).

**Quellsysteme mit Grenze (Schutz vor Zugriffen der Plattform auf beliebige Adressen):** Ein
Mandanten-Admin legt Quellsysteme nur mit einem **Collector-Agenten seines Mandanten** an (Agent mit
`tenant_id` = eigener Mandant) – gelesen wird dann im Netz des Kunden, nicht vom zentralen Worker
bzw. einem mandantsübergreifenden Agenten des Betreibers. Adresse, Agent, TLS-Prüfung, Adressen der
Switches, Zugang und Host-Schlüssel ändert er nur an solchen Systemen. Name, Aktiv, Standort,
Zeitplan, Verbund und Zugangsdaten (nur schreibbar, nie zurück) ändert er an allen Systemen seines
Mandanten; löschen darf er sie. **NetBox** als Quellsystem (Ziel des Schreibwegs) legt nur der
Betreiber an bzw. um; den Sync dorthin stösst der Admin an. Agenten selbst (anlegen, Zugang mit
Zertifikat aus der globalen Agent-CA, Paket) bleiben beim Betreiber; der Admin sieht nur die Agenten
seines Mandanten (Auswahl im Quellsystem).

### 5. Nie für Benutzer eines Kunden-Realms

Plattform-Funktionen: System (Status, Verbindungen, Aufbewahrung, Betriebszustand), Kunden-Portal,
Kopplung, Mandanten anlegen/ändern/löschen/wechseln, andere Mandanten, Benutzer & Rollen
(Kunden-Benutzer), Kunden in Automato anlegen/ändern/löschen (kommen aus dem Cockpit, ADR 0058),
Namensprofile und Wörterbücher (gelten für mehrere Mandanten, nicht je Mandant), Collector-Agenten
verwalten, globale Agent-CA, globaler Admin. Rechte weiter nur aus dem Kunden-Portal bzw. Cockpit
(`benutzer`, Standard-Rolle), nie aus dem Token des Kunden-Realms.

### 6. Betreiber

- Unter `<kunde>-doc` bleibt ein Betreiber (`<name>@betrieb`) ein Benutzer des Kunden-Realms; seine
  Rolle kommt aus dem Cockpit (Vorgabe Admin = `customer_admin`) und gibt ihm im eigenen Mandanten
  alles (Nr. 3/4). Keine Rechte aus dem Benutzernamen.
- **Link „Ganze Plattform“** in der Kopfzeile: nur für Benutzer eines Kunden-Realms, deren Name auf
  `@betrieb` endet (nur Anzeige – die Rechte prüft das Ziel), nur in der gemeinsamen Instanz (nicht in
  einer eigenen, `AUTOMATO_INSTANZ_KUNDE` leer) und nur, wenn die interne Adresse der Oberfläche
  bekannt ist (`topologie.json` des Front-Proxys, `oberflaeche` = `https://<AUTOMATO_UI_HOST>`).
  `GET /auth/me` liefert `betreiber` und `plattform_url`.
- **Rechte auf der ganzen Plattform:** über den bestehenden Weg ADR 0007 – Rollen im Token des
  **eigenen Realms von Automato**. Das Cockpit verbindet den Realm der Betreiber auch dort als
  Identity Provider `betrieb` (wie in den Kunden-Realms) und setzt per Mapper die Realm-Rolle der
  Betreiber (Einstellung unter Betreiber, Vorgabe `admin`; Cockpit ADR 0017 Nachtrag). Automato
  ändert dafür keinen Code; voraussetzung ist der Zugang `cockpit` im Realm von Automato mit
  `manage-identity-providers` (von Hand, Anleitung in `docs/BETRIEB.md`).

### 7. Oberfläche

- Navigation nach Rolle: Mandanten-Benutzer sehen alle Ansichten und Stellen der Schichten L1–L7
  (auch die bisher nur für Betreuer, z. B. Netze, VRF, Wege, Abgleich, Läufe, Mandanten &
  Standorte); Plattform-Ansichten (`platform` im Verzeichnis der Ansichten) nie; Administrator-
  Ansichten des Mandanten (`tenantAdmin`, z. B. NetBox-Sync) nur der Admin. Bearbeiten-Knöpfe nur
  mit Recht; „Jetzt einlesen“ auch für den Operator.
- Rollenanzeige oben rechts: „Kunde · Viewer/Triage/Editor/Operator/Admin“, „Betreiber“.
- **Fail-open behoben:** ohne Antwort von `GET /auth/me` keine Sicht aus dem Token mehr – die
  Oberfläche zeigt den Fehler mit „Neu laden“ bzw. „Abmelden“.

Verworfen:

- **Kunden-Benutzer im eigenen Mandanten als Betreuer `operator`/`admin` mit `tenant_ids`:** öffnete
  jeden Endpunkt dieser Rollen, auch Plattform-Endpunkte, die nicht nach Mandant prüfen (Agenten,
  Kunden-Portal, Namensprofile).
- **Die Freigabeliste der Kunden erweitern:** der Kunden-Filter bliebe und verdeckte im eigenen
  Mandanten Inventar, Netze ohne Zone und L1–L3.
- **Betreiber am Benutzernamen erkennen und Rechte geben:** ein Kunde mit eigenem Verzeichnis könnte
  einen solchen Namen anlegen; darum nur die Anzeige des Links.
- **Kachel „Ganze Plattform“ auf der Startseite des Kundenportals jetzt:** die Startseite ist eine
  statische Seite des Front-Proxys für alle Benutzer (CSP ohne Skripte); eine Sicht je Benutzer
  bräuchte eine zweite Seite je Kunde hinter einem Matcher auf die Kopfzeilen des Tors bzw.
  Templates (Gefahr der Template-Einschleusung über Kacheltitel). Kommt mit einer eigenen Änderung
  am Front-Proxy (offen).

## Folgen

- Kunden mit eigenem Mandanten bedienen ihre Umgebung selbst (Operator: einlesen; Admin: alles im
  Mandanten), ohne je an andere Mandanten oder die Plattform zu kommen.
- Ein Kunden-Admin kann jetzt Quellsysteme anlegen – Zugriffe der Plattform auf beliebige Adressen
  verhindert die Grenze in Nr. 4 (nur eigene Agenten); Risiko R32.
- Der Realm der Betreiber wird für Automato zum Weg zum globalen Admin (Cockpit-Mapper): wer ihn
  übernimmt, ist Admin der ganzen Plattform. Gemildert durch Pflicht-Zweitfaktor und Sperre an
  einer Stelle (Cockpit ADR 0017); ein zurückgenommener Mapper entzieht bestehenden Konten die
  Rolle nicht (kein `manage-users`) – Rolle in Keycloak entfernen (R32).
- Neue Endpunkte brauchen einen Eintrag in `auth/tenant_routes.py` (Lesen, Einlesen, Anpassen) oder
  in `PLATFORM_ROUTES` – sonst scheitert `tests/test_tenant_roles.py`.
