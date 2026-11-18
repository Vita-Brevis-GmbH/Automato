# ADR 0051 — Anmelde-Tor im Front-Proxy: eine Anmeldung je Realm für alle Werkzeuge

## Status

**Angenommen** (2026-11-10), umgesetzt in 1.4.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-10: „Können wir die Authentication nicht bereits auf der Startseite für uns und
für die jeweiligen Kunden vorab abfragen, damit sie sich einloggen müssen, bevor sie auf die
Webseiten kommen?“ und „Ist es nicht möglich, dass wir die Startseite, Magister und Automato mit dem
gleichen Realm schützen … SSO einrichten … ich möchte nicht drei Loginscreens haben, zudem soll die
NetBox auf den Realm schauen.“ Auf den Vorschlag (Tor im Front-Proxy, NetBox über den Realm, Magister
beide Wege): „1.4 bauen“; Rückfragen: Magister „beide vorsehen“, Rechte in NetBox „passt so“, Kunde
ohne Realm „Realm notwendig, notfalls mit lokalen Usern“. Ergänzt ADR 0050 (Realm je Kunde) und
ADR 0044 Nachtrag (Front-Proxy).

## Kontext

- Seit 1.2.0 liefert der Front-Proxy (Caddy auf der Automato-VM, `front.sh`) Startseite und
  Weiterleitung je Werkzeug aus; die Startseite war ohne Anmeldung, jedes Werkzeug meldete sich
  selbst an (NetBox mit eigenen Konten, Automato über Keycloak, Magister eigen).
- Seit 1.3.0 hat jeder Kunde einen Realm (Azure/Entra, LDAPS oder lokale Benutzer).
- Die Kunden-Adressen sind über die WAF aus dem Internet erreichbar; NetBox und Magister hatten
  bisher ihre Anmeldemaske offen.

## Entscheid

1. **Anmelde-Tor vor jeder Kunden-Adresse** (`AUTOMATO_FRONT_TOR=an`): Startseite, `-nb`, `-doc`,
   `-um`, `-cmdb` und das interne Portal fragen zuerst per `forward_auth` bei **oauth2-proxy**
   (Open Source) – je Realm ein Container (Client `automato-front`, vertraulich, PKCE S256).
   Wer nicht angemeldet ist, landet bei Keycloak im Realm des Kunden und danach auf der
   ursprünglichen Adresse. Weitere Werkzeuge melden sich über die Keycloak-Sitzung ohne Maske an
   (SSO); eine Anmeldemaske je Realm.
2. **Reihenfolge fest** (Caddy `route`): Sperre nach Absender (WAF/Management-Netz), Kopfzeilen der
   Anmeldung vom Absender entfernen, Ausnahmen, Tor, Ziel. Die Kopfzeilen `X-Automato-User`
   (`<benutzer>@<realm>`), `-Realm` und `-Groups` (`automato-lesen` und die Realm-Rollen als
   `role:<rolle>`) setzt nur der Proxy nach dem Tor.
3. **Ausnahmen:** die NetBox-API mit Token (`Authorization: Token|Bearer`, NetBox prüft selbst –
   NetBox-Sync von Automato) und die API von Automato unter `-doc` (prüft das Token von Automato
   selbst, ADR 0050). Alles andere nur mit Anmeldung.
4. **Kunde ohne Realm bzw. Realm ohne Client-Secret: gesperrt** (fail-closed, 403 ohne Einzelheiten);
   notfalls ein Realm mit lokalen Benutzern. Das Portal `start.<intern>` nur für die Rollen
   `admin`/`operator` des Realms von Automato (eigener Container `tor-intern`).
5. **NetBox über den Realm:** NetBox übernimmt den Benutzer aus `X-Automato-User` (Remote-Auth,
   Gruppen-Abgleich): `role:admin` = Superuser, `role:operator` = bearbeiten, alle anderen lesen
   (`netbox-host.sh sso`). Der Proxy des NetBox-Hosts nimmt diese Kopfzeilen nur vom Front-Proxy
   (`FRONT`) an. Der Realm am Benutzernamen verhindert, dass ein Realm-Benutzer ein lokales Konto
   (`admin`, Token-Benutzer) trifft; lokale Konten bleiben für den Notfall (direkt aus dem
   Management-Netz).
6. **Magister: beide Wege** – Magister kann sich über Keycloak (OIDC) selbst anmelden und bekommt
   zusätzlich die Kopfzeilen des Tors; welchen Weg Magister nutzt, entscheidet Magister.
7. **Secrets nur auf der VM:** das Client-Secret je Realm legt `front.sh tor <realm>` (Eingabe
   unsichtbar, Datei nur root) ab, dazu ein Cookie-Secret. Die Oberfläche kennt keine Secrets; sie
   bekommt den Realm im Soll-Stand und zeigt den Stand des Tors aus `status.json`. *Geändert mit
   1.13.0 (ADR 0055):* im Klartext nur auf der VM; aus dem Cockpit nur versiegelt mit dem
   Empfangsschlüssel der VM, entschlüsselt nur von `front.sh sync`.
8. **Abmelden** (`/oauth2/sign_out`, Startseite und NetBox) beendet auch die Keycloak-Sitzung.
   *Ergänzt mit 1.19.3 (Nachtrag unten):* über alle Adressen des Kunden, auch aus der Oberfläche.

## Folgen

- Kunden sehen ohne Anmeldung nur noch Keycloak; NetBox- und Magister-Masken sind nicht mehr offen.
- Eine Anmeldung je Realm: Mitarbeitende melden sich einmal mit dem AD-Konto an und wechseln ohne
  Maske zwischen Startseite, Automato, NetBox und Magister.
- Je Realm ein kleiner Container (oauth2-proxy, gehärtet: nur lesend, ohne Capabilities); Image
  `quay.io/oauth2-proxy/oauth2-proxy` mit fester Version (`AUTOMATO_FRONT_TOR_IMAGE`).
- Sitzungen: 12 h, alle 5 min erneuert; endet die Keycloak-Sitzung, gilt das Tor spätestens nach
  5 min nicht mehr (nach „Abmelden“ seit 1.19.3 sofort auf allen Adressen des Kunden, Nachtrag).
- Ein Ende-zu-Ende-Test (CI-Job docker) prüft die erzeugte Konfiguration mit echtem Caddy und
  oauth2-proxy gegen einen Test-Keycloak.
- Offen: Mitarbeitende auf Kunden-Adressen ohne Konto im Kunden-Realm – über einen Identity
  Provider „Mit eigener Firma anmelden“ im Kunden-Realm (Keycloak-Brokering), nach Bedarf.

## Nachtrag 1.5.0 (2026-11-10): Zugriff je Kunde

Anwender: „Kann ich eventuell noch einstellen, von wo ich die einzelnen Kunden drauf zugreifen
können? Bei VB wäre es am liebsten nur von intern möglich, andere ‚zahlende‘ Kunden von extern“;
auf den Vorschlag „Drei Stufen“ und „Zusammen als 1.5.0“.

- Je Kunde **überall** (Vorgabe, wie bisher), **nur intern** (Management-Netz nach der Adresse der
  Gegenstelle, nie über die WAF – wie das Portal) oder **nur aus diesen Netzen** (Adresse des
  Benutzers: über die WAF aus `X-Forwarded-For`, nur der WAF geglaubt; sonst die Gegenstelle).
- Die Sperre steht in `route` direkt nach der Sperre nach Absender – vor den Ausnahmen (NetBox-API
  mit Token) und vor dem Tor. Gesperrt antwortet wie eine unbekannte Adresse (404 „Unbekannte
  Adresse“), damit die Antwort nicht verrät, dass es den Kunden gibt.
- Hinter einer FortiGate mit VIP (Adresse des Besuchers statt WAF) gilt für die Kunden-Adressen
  `AUTOMATO_FRONT_KUNDEN_ALLOW="0.0.0.0/0 ::/0"`; die Oberfläche bleibt über `AUTOMATO_UI_ALLOW`
  intern, interne Adressen prüft der Proxy weiter nach Absender.

## Nachtrag 1.13.0 (2026-11-16): Client-Secret aus dem Cockpit

Punkt 7 geändert durch ADR 0055: das Cockpit liefert das Client-Secret von `automato-front`
versiegelt mit dem Empfangsschlüssel der VM (`front.sh tor-empfang`); Datenbank und Soll-Stand
halten nur das Chiffrat, im Klartext steht es nur in `deploy/front/tor/<realm>.env`. Der Weg von
Hand (`front.sh tor <realm>`) bleibt und gilt, bis das Cockpit ein anderes Chiffrat schickt.

## Nachtrag 1.19.3 (2026-11-18): Abmelden mit einem Klick auf allen Adressen des Kunden

Anwender 2026-11-18: „Das Logout bei den Kundenportalen geht noch nicht, ich komme immer wieder
rein, auch, wenn ich nicht neu einlogge.“ und „Auch aus dem Automato heraus abmelden muss ich
mehrfach auf Abmelden klicken, damit ich zum Keycloak zurückkehre“.

Ursachen:

- Das Cookie des Tors (`_automato_tor`) gilt je Adresse (Host, ohne `--cookie-domain`). „Abmelden“
  löschte nur das Cookie der Adresse, auf der geklickt wurde, und beendete die Sitzung in Keycloak.
  Die übrigen Adressen des Kunden (`-nb`, `-doc`, `-um`, `-cmdb`, bzw. die Startseite nach
  `/logout/` in NetBox) liessen den Benutzer bis zur nächsten Erneuerung (`--cookie-refresh=5m`;
  erst dann lehnt Keycloak mit `invalid_grant` ab) ohne Anmeldung herein.
- Die Oberfläche meldete sich nur bei Keycloak ab (`end_session`) und kam auf `-doc` zurück, wo das
  Cookie des Tors noch galt. Kam sie binnen einer Minute nach der Anmeldung zurück, hielt der
  Schleifenschutz das für eine Schleife („Anmeldung nicht möglich“ mit einem weiteren „Abmelden“,
  danach fragte Keycloak ohne `id_token_hint` nach). Eine Anfrage während des Abmeldens löste
  zudem eine neue Anmeldung aus, die das Abmelden überholen konnte.

Entscheid:

- **Abmelden über alle Adressen des Kunden:** Einstieg `/oauth2/abmelden` auf jeder Adresse hinter
  dem Tor (Startseite, NetBox `/logout/`, Oberfläche, Magister, CMDB), im Caddy vor dem Tor. Der
  Browser geht der Reihe nach über `/oauth2/sign_out` jeder Adresse des Kunden (Startseite zuerst,
  jede leitet mit `/oauth2/abmelden/weiter` an die nächste), die letzte auf „Abgemeldet“. Die erste
  Adresse mit Sitzung beendet die Sitzung in Keycloak (`--backend-logout-url`), jede löscht ihr
  Cookie. Jeder Schritt ist einfach kodiert (keine verschachtelten Adressen, die eine WAF als
  doppelte Kodierung ablehnen könnte); nur Adressen des Kunden (gleicher Zugriff je Kunde), keine
  Seite von Keycloak dazwischen.
- **Oberfläche hinter dem Tor meldet über das Tor ab:** sie erkennt das Tor an der Weiterleitung
  von `/oauth2/abmelden`, löscht ihr Token und geht dorthin; ohne Tor wie bisher über Keycloak
  (mit `id_token_hint`, ohne Rückfrage). Ab dem Klick keine stille Neuanmeldung mehr, und der
  Schleifenschutz gilt nicht für die Anmeldung nach dem Abmelden.
- Kopfzeilen der Anmeldung setzt weiter nur das Tor; keine neuen Geheimnisse, keine Änderung an
  Keycloak oder am Cockpit nötig.

Grenzen (bleiben, bewusst): Kunden, die denselben Realm teilen, und das Portal `start.<intern>`
(eigener Container, nur intern) haben je eine eigene Kette – deren Adressen gelten nach dem
Abmelden bei einem anderen Kunden des Realms noch bis zur Erneuerung (höchstens 5 min). Eine eigene
Sitzung von Magister (eigenes OIDC) beendet das Tor nicht. Wer sich über einen Identity Provider
angemeldet hat (z. B. „Anmelden als Betreiber“, Cockpit ADR 0017), bleibt dort angemeldet; Keycloak
fragt im Kunden-Realm wieder, der Knopf des Identity Providers meldet aber ohne Passwort an (je
nach Ablauf mit dem zweiten Faktor) – das zu ändern hiesse, mit dem Kunden-Realm auch die Anmeldung
der Betreiber zu beenden (und damit die Konsole des Cockpits); offen, Entscheid des Anwenders.
