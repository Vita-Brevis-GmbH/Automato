# ADR 0014 — Vita-Brevis-Cockpit für alle Werkzeuge, zusammen mit Magister (Entwurf, Plan)

## Status

**Entwurf v2** (2026-10-27): Plan auf Wunsch des Anwenders, **noch nichts umgesetzt**.
Ersetzt den Entwurf vom 2026-10-20 (gemeinsamer Status-Vertrag, Cockpit nur lesend) und baut
auf dem bestehenden Cockpit von Magister auf (`Vita-Brevis-GmbH/magister`, `cockpit/`,
Magister-ADR 0003). Wird nach den Entscheiden unten in einzelne ADRs aufgeteilt; Schreibwege
(Hosts, Keycloak) brauchen je eine eigene ADR und Freigabe.

> Anwender (2026-10-27): „Schön wäre, dass ich ein Cockpit habe für alle Tools und dort drin
> sagen kann, was ich ausrollen möchte, Automato, Magister, NetBox usw. Weiter dies dann über
> den Keycloak steuern und Zugänge ermöglichen.“

## Ausgangslage

**Magister** hat bereits ein Cockpit (`cockpit/`, „Vita Brevis Cockpit“, Magister-ADR 0003),
geplant zum Herauslösen in ein eigenes Repo `vita-brevis-cockpit`:

- FastAPI (async) + React/Tailwind + Postgres, Docker Compose.
- Instanzliste (1 Schulträger = 1 Instanz), Health-Abfrage `/api/healthz` mit Version,
  Release-Manifest je Kanal `stable`/`latest`.
- Update-Aufträge (`update_requests`: pending → in_progress → done/failed) und ein
  **Update-Runner per SSH**: `pg_dump` vorher, `docker compose pull/up`, Smoke-Test, Rückmeldung.
- Anmeldung mit Bootstrap-Token und rotierbaren Service-Tokens, hinter VPN – **kein OIDC**.
- Auf den Magister-Hosts ein **Ops-Agent** (systemd-Timer, Aufträge als Datei, nur die festen
  Aktionen `restart`/`update`).
- Images fertig auf GHCR. Magister-Benutzer melden sich **direkt bei Entra ID** an.

**Automato** bringt mit:

- Betriebsseiten: System-Seite, Betriebszustand, Verbindungen.
- Updates: `deploy/update.sh` mit Sicherung und Rückfall (heute `git pull` + Build auf der
  VM), dazu `deploy/aufbau.sh`.
- **Keycloak 26**: Realm-Vorlage `realm-automato.json`/`make_realm.py`, Rollen
  viewer/operator/admin/customer, Claims `tenant_ids`/`customer_ids`, OTP-Pflicht.
- Als Muster für einen Host-Agenten: der **Collector-Agent** mit Pull, signierten Paketen und
  mTLS-Gateway.
- **NetBox** lokal über `aufbau.sh netbox-start`, Token von Hand.

## Zielbild

```
Ops-Benutzer ──OIDC (Ops-Realm, OTP)──► Cockpit (Web + API + Postgres: Inventar, Aufträge, Audit)
                                           │
          ┌──────────────── lesen ─────────┼──────────── verwalten ─────────────┐
          ▼                                ▼                                     ▼
  Status je Instanz              Aufträge (signiert)                    Keycloak Admin-API
  GET /system/status             ◄── Host-Agent je VM (Pull, mTLS)      (Service-Account,
  (Automato, Magister,                 führt nur Positivliste aus:       eingeschränkt)
   NetBox, Keycloak)                   install · update · rollback ·     Realm/Organisation je Firma,
                                       backup · restart · status         Clients je Instanz, Rollen,
                                            │                            Benutzer, IdP der Firma
                                            ▼
                               docker compose: Automato | Magister | NetBox | Keycloak
                               Images + signiertes Release-Manifest (GHCR)
```

## Bausteine

1. **Inventar:** Firma → Umgebung (z. B. Produktion/Test) → Host (VM) → Instanz. Eine Instanz
   hat Produkt, Version, Kanal, URL, Zustand und zuletzt gesehen.
2. **Produktkatalog:** Automato, Magister, NetBox, Keycloak; später Proxy/Caddy, OpenBao,
   Monitoring. Je Produkt:
   - eine Vorlage (Compose-Bündel, benötigte Secrets, Abhängigkeiten – Automato braucht
     Keycloak-Clients und optional einen NetBox-Token),
   - Versionen aus einem **signierten Release-Manifest** je Kanal.
3. **Ausrollen und Aktualisieren über einen Host-Agenten:**
   - Der Agent holt Aufträge ab (Pull, mTLS) und prüft die Signatur.
   - Er führt nur eine Positivliste aus: install, update, rollback, backup, restart, status.
   - Die Schritte sind dieselben wie in `update.sh` (Sicherung → neue Version → Health →
     sonst Rückfall).
   - Kein SSH vom Cockpit auf die Hosts.
4. **Keycloak steuern:** Das Cockpit verwaltet Keycloak über einen eingeschränkten
   Service-Account:
   - Firma anlegen = Realm aus der Vorlage (oder Organisation, siehe Entscheid 3).
   - Je Instanz die Clients anlegen.
   - Benutzer einladen und je Instanz Rollen geben: Automato viewer/operator/admin/customer
     mit `tenant_ids`/`customer_ids`, Magister-Rollen, NetBox-Gruppen.
   - Anbindung an den IdP der Firma (Entra/AD/SAML-Brokering); OTP-Pflicht.
5. **Zugänge ermöglichen:** ein **App-Starter** im Cockpit bzw. im Keycloak-Konto. Jeder
   Benutzer sieht die Instanzen, auf die er Zugriff hat, und meldet sich mit Single Sign-on an
   (Automato, NetBox über OIDC/social-auth, Magister nach der Umstellung).
6. **Status-Vertrag:**
   - gemeinsames `GET /system/status` mit Version, Migrationsstand, letzter Sicherung, Health
     und plattformspezifischen Angaben unter `extra`;
   - Magisters `/api/healthz` bleibt gültig;
   - das Cockpit liest mit Client-Credentials;
   - Ampel nach festen Regeln, z. B. Sicherung älter als 26 h, DB ≠ Code, Signatur fehlt.
7. **Secrets:**
   - beim Ausrollen erzeugt;
   - auf dem Host (`.env`) bzw. in OpenBao abgelegt;
   - das Cockpit kennt nur Verweise, nie Klartext.
8. **Audit:** jeder Auftrag und jede Änderung an Zugängen (wer, wann, was); auf Wunsch
   Vier-Augen-Freigabe für Ausrollen und Löschen.

## Optionen und Empfehlung

| Frage | Möglichkeiten | Empfehlung |
|---|---|---|
| Wo entsteht das Cockpit? | in Automato · Magister-Cockpit ausbauen · ganz neu | **Magister-Cockpit ausbauen** und als `vita-brevis-cockpit` herauslösen (Magister plant das). Derselbe Stack wie Automato; Instanzen, Kanäle und Update-Aufträge sind schon da. |
| Automato für mehrere Firmen | eine Instanz je Firma · eine gemeinsame Instanz mit Firmen | **Instanz je Firma** wie bei Magister: Daten, NetBox und Zugänge sind getrennt, das Cockpit macht viele Instanzen handhabbar. Kunden (ADR 0018) bleiben innerhalb einer Firma. |
| Keycloak | Realm je Firma · ein Realm mit Keycloak-Organisationen (ab KC 26) | **Ops-Realm für Vita Brevis + Realm je Firma** (Vorlage vorhanden, harte Trennung, eigener IdP je Firma). Organisationen sind die Alternative bei vielen kleinen Firmen. |
| Ausrollen auf die Hosts | SSH-Runner (Magister heute) · Host-Agent mit Pull | **Host-Agent**: kein offener SSH-Zugang, dasselbe Muster wie der Collector-Agent (Pull, mTLS, signiert). |
| Verteilen der Software | Build auf der VM (Automato heute) · fertige Images | **Fertige Images in GHCR + signiertes Manifest** (wie Magister); Voraussetzung für Ausrollen ohne Quellcode auf der VM. |
| VMs anlegen | nur vorbereitete VMs · VM erzeugen (Proxmox/VMware über OpenTofu) | **Zuerst vorbereitete VMs** (Host-Agent per Einmal-Token einrichten); VM-Erzeugung später mit eigener ADR. |

## Was geht – und wo die Grenzen sind

**Machbar** mit dem bestehenden Stack:

- Inventar und Status aller Werkzeuge mit Ampel und Versionen.
- Updates und Rückfall aus dem Cockpit.
- Ausrollen von Automato, Magister, NetBox und Keycloak auf vorbereiteten VMs.
- Keycloak-Realms, Clients, Rollen und Benutzer aus dem Cockpit.
- Single Sign-on für Automato und NetBox sofort, für Magister nach der Umstellung.
- App-Starter.

**Grenzen und Risiken:**

- **Das Cockpit wird zum mächtigsten System** (rollt überall aus, verwaltet Keycloak). Es
  braucht ein eigenes Härtungskonzept:
  - nur aus dem Verwaltungsnetz erreichbar, OTP, Vier-Augen-Freigabe;
  - signierte Aufträge, die der Host-Agent nur aus seiner Positivliste ausführt;
  - Break-glass-Zugang, Audit.
- **Henne-Ei bei der Anmeldung:** Das Cockpit meldet sich an dem Keycloak an, den es selbst
  verwaltet. Lösung: Ops-Realm getrennt von den Firmen-Realms, Break-glass-Token wie heute bei
  Magister.
- **Magister meldet heute direkt bei Entra an:** Die Umstellung auf Keycloak (Brokering zu
  Entra) ist ein eigener Magister-Meilenstein (ADR 0007 D6). Bis dahin startet der App-Starter
  Magister mit dessen eigener Anmeldung.
- **NetBox-Rechte** sind feiner als Rollen (Gruppen, Object Permissions) – Zuordnung über
  Gruppen-Mapping aus Keycloak.
- **Schreibwege:** Die Vorgabe „nur nach NetBox schreiben“ betrifft die Geräte (FortiGate, APIC,
  DDI) und bleibt. Die Schreibwege des Cockpits (eigene Hosts, Keycloak) sind neu und brauchen
  je eine ADR und Freigabe.
- **Das Cockpit speichert keine Fachdaten** (keine Regeln, keine Schüler) – nur Metadaten,
  Aufträge und Audit.

## Phasen

| Phase | Inhalt | Grösse |
|---|---|---|
| **P0 Entscheiden** | Entscheide unten, Magister-ADR abstimmen, Cockpit-Repo herauslösen | S |
| **P1 Sehen** | Inventar für alle Produkte, Status-Vertrag in Automato (`/system/status`), Health von NetBox/Keycloak, Ampel, App-Starter; Cockpit-Login über den Ops-Realm (OIDC) statt Bootstrap-Token | M |
| **P2 Zugänge** | Keycloak aus dem Cockpit: Firma → Realm, Clients je Instanz, Benutzer/Gruppen/Rollen, Einladung, IdP-Brokering; Automato-Rollen mit `tenant_ids`/`customer_ids`; NetBox-SSO | M–L |
| **P3 Aktualisieren** | Host-Agent (Pull, mTLS, signiert); Release-Manifeste je Produkt (Automato: Images in GHCR); Update, Rückfall und Sicherung aus dem Cockpit; Magister-Runner auf den Host-Agenten umstellen | L |
| **P4 Ausrollen** | neue Instanz auf vorbereiteter VM: Produktvorlagen (Automato inkl. Keycloak-Clients und optional NetBox, Magister, NetBox, Keycloak), Secrets erzeugen, DNS/TLS (Caddy), erster Benutzer; Löschen/Archivieren mit Sicherung | L |
| **P5 Später** | VMs erzeugen (Proxmox/VMware über OpenTofu), Benachrichtigung (Mail/Teams), Firmen-Admins im Cockpit (Selbstbedienung), Berichte | – |

## Was das für Automato heisst

- `GET /system/status` nach dem gemeinsamen Vertrag, lesbar für das Cockpit
  (Client-Credentials).
- Release:
  - Images in GHCR und ein signiertes Manifest aus der CI;
  - `update.sh` versteht zusätzlich ein Image-Tag statt `git pull`.
- Realm-Vorlage je Firma parametrisierbar (`make_realm.py`); Rollen und Claims als Vertrag
  festhalten.
- NetBox-Token für Automato als Secret beim Ausrollen statt von Hand.
- Mehrmandanten-Entscheid (geplant nach der Produktivschaltung): mit dem Cockpit **Instanz je
  Firma**.

## Was das für Magister heisst

- Cockpit herauslösen (`vita-brevis-cockpit`) und produktneutral machen: Produkt je Instanz,
  Katalog.
- Status-Vertrag übernehmen.
- Runner auf den Host-Agenten umstellen.
- Login ins Cockpit über Keycloak; langfristig auch der Magister-Login über Keycloak
  (Brokering zu Entra).

## Offene Entscheide (Anwender)

1. Cockpit aus Magister herauslösen und für alle Werkzeuge ausbauen – oder neu bauen?
2. Automato für mehrere Firmen: eine Instanz je Firma (Empfehlung) oder eine gemeinsame?
3. Keycloak: Realm je Firma (Empfehlung) oder ein Realm mit Organisationen? Soll Magister auf
   Keycloak umziehen (Brokering zu Entra)?
4. Ausrollen über einen Host-Agenten (Empfehlung) oder über SSH wie der Magister-Runner?
5. Nur vorbereitete VMs (Empfehlung für den Anfang) oder soll das Cockpit auch VMs auf
   Proxmox/VMware anlegen?
6. Wer benutzt das Cockpit: nur Vita-Brevis-Betrieb, oder auch Admins der Firmen
   (Selbstbedienung)?
7. Fertige Images in GHCR für Automato (Empfehlung) statt Build auf der VM?
8. Zeitpunkt: P0/P1 sofort (nur lesend, geringes Risiko) und P2–P4 nach der Produktivschaltung
   des DC – oder alles danach?
