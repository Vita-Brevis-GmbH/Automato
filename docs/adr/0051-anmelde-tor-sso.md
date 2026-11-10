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
   bekommt den Realm im Soll-Stand und zeigt den Stand des Tors aus `status.json`.
8. **Abmelden** (`/oauth2/sign_out`, Startseite und NetBox) beendet auch die Keycloak-Sitzung.

## Folgen

- Kunden sehen ohne Anmeldung nur noch Keycloak; NetBox- und Magister-Masken sind nicht mehr offen.
- Eine Anmeldung je Realm: Mitarbeitende melden sich einmal mit dem AD-Konto an und wechseln ohne
  Maske zwischen Startseite, Automato, NetBox und Magister.
- Je Realm ein kleiner Container (oauth2-proxy, gehärtet: nur lesend, ohne Capabilities); Image
  `quay.io/oauth2-proxy/oauth2-proxy` mit fester Version (`AUTOMATO_FRONT_TOR_IMAGE`).
- Sitzungen: 12 h, alle 5 min erneuert; endet die Keycloak-Sitzung, gilt das Tor spätestens nach
  5 min nicht mehr.
- Ein Ende-zu-Ende-Test (CI-Job docker) prüft die erzeugte Konfiguration mit echtem Caddy und
  oauth2-proxy gegen einen Test-Keycloak.
- Offen: Mitarbeitende auf Kunden-Adressen ohne Konto im Kunden-Realm – über einen Identity
  Provider „Mit eigener Firma anmelden“ im Kunden-Realm (Keycloak-Brokering), nach Bedarf.
