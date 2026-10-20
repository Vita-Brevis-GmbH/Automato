# Betriebs- & Anwenderhandbuch (UC1: Firewall-Regel-Dokumentation)

Für Model 2 (FortiGate + NetBox). Setup-Details: `lab/README.md`. Architektur/Entscheide:
`ARCHITECTURE.md`, `docs/adr/`.

## Überblick

```
FortiGate ──(read-only REST)──▶ Collector ─▶ Orchestrator ─▶ NetBox (Matching, SoT)
                                                 │
                                   Web-UI ◀──────┘  (Regeln, NAT/VIPs, Applikationen, Findings)
```

- **Collector** liest rein lesend von der FortiGate (Policies, Adress-/Service-Objekte, VIPs).
- **Orchestrator** persistiert, matcht Adressen gegen NetBox, erzeugt Findings, stellt die API.
- **NetBox** ist die **Source of Truth** fürs IPAM (nach dem Erst-Import gepflegt).
- **Web-UI** zeigt alles read-only mit Filtern.

## 1. Inbetriebnahme

Einmalig auf der VM (siehe `lab/README.md`):

```bash
sudo bash lab/install-prereqs.sh && newgrp docker
FORTIGATE_URL=https://<fortigate-ip> FORTIGATE_TOKEN='<readonly>' bash lab/bootstrap.sh
```

`bootstrap.sh` fährt NetBox + Automato hoch, importiert die FortiGate-Objekte als
IPAM-Ausgangsbasis nach NetBox (Tag `automato-import`) und löst einen ersten Lauf aus.

## 2. Betrieb

**Regeln erneut einlesen** (nach FortiGate-Änderungen):
```bash
docker compose exec orchestrator automato collect --platform fortigate --tenant lab --source fw-01
docker compose exec orchestrator automato status --run-id <id>
```
Der Lauf **ersetzt** die Regeln/NAT des Quellsystems und baut die Findings neu auf.

**NetBox pflegen (SoT):** Die Firewall-Objekte sind nur der Startpunkt. Netze/IPs/Zonen
in NetBox kuratieren; Zonen hängen als Tag `zone-<name>` am Prefix. Nicht gematchte
Objekte erscheinen als Findings → in NetBox ergänzen oder als extern bestätigen.

**Zyklisch:** Für regelmässige Läufe den Collect per Scheduler/Cron gegen die CLI bzw.
`POST /jobs/collect` auslösen (Celery-Beat ist im Stack vorhanden).

**Stoppen / Zurücksetzen:**
```bash
docker compose --profile core --profile collectors down          # stoppen
docker compose --profile core --profile collectors down -v       # inkl. Daten
```

## 3. Bedienung (Web-UI, `http://<host>:5173` bzw. über den TLS-Proxy `https://<host>`)

Aufbau nach dem UI-Mockup: Sidebar links (Dokumentation / Administration, Zähler für
Regeln, NAT und offene Findings), Topbar mit **Mandanten-Schalter**, HTTPS-Anzeige,
Hell/Dunkel und Benutzermenü (Rolle, Mandanten, Mein Konto, Abmelden). Im OIDC-Modus
wird das Token still erneuert; läuft die Keycloak-Sitzung ab, folgt die Anmeldung erneut.
Fehler zeigt das UI mit der Meldung des Orchestrators (z. B. „Keine Berechtigung“).

**Dokumentation**
- **Übersicht:** KPIs (Regeln, NAT/VIPs, offene Findings, Abdeckung gegen die SoT),
  **Letzte Läufe** und **Jetzt einlesen** (Operator): Mandant + Quellsystem wählen,
  Ausführung *Extern (Agent)* oder *Zentral (Worker)*. Laufende Läufe aktualisieren sich.
- **Firewall-Regeln:** Quelle/Ziel/Dienste mit den **Objektnamen wie auf der Firewall**;
  Suche (Regel, Objektname, IP), Filter System, Quell-/Ziel-Zone, Aktion, „nur mit Findings“.
  Klick → Detail: je Objekt Typ, Kommentar, Gruppenmitglieder und je Wert die
  **Referenzdaten** aus dem IPAM (DNS-Name, Netz, VLAN, Standort; aufklappbar mit allen
  Feldern und Link in NetBox), dazu Dienste, Schutzprofile, VIP-Ziel.
- **Objekte & Inventar:** Adressobjekte (inkl. VIPs/IP-Pools), Gruppen, Services – mit
  Verwendung in Regeln/Gruppen und Befunden – sowie das Netzinventar aus FortiGate/APIC:
  **Subnetze** (mit Abgleich gegen führendes IPAM und Anreicherung, VLAN-Abweichung
  markiert), **VLANs** (inkl. EPGs), **Ports** (Modus, untagged/tagged VLANs, LAG/Parent,
  IPs, Zone/Policy-Gruppe, LLDP/CDP-Nachbar, EPGs), **Geräte**, **VRFs**. Filter Quelle,
  Suche, „nur Abweichungen“.
- **NAT / VIPs:** VIPs (extern → intern, Port-Forward) und SNAT-Pools.
- **Applikationen:** *Applikations-Doku* je System (10 Kategorien, Standort verorten,
  **Export (Markdown)**) und *Publizierte Dienste* (VIPs mit Regeln).
- **Findings:** nach Status und Typ gefiltert, Suche, Quellsystem; Status setzen (Triage)
  als Operator – bleibt über spätere Läufe erhalten (Findings werden nach jedem Lauf neu
  berechnet).
- **Abgleich:** Referenzquellen des Mandanten (führend/Anreicherung, eingelesen?), Kacheln je
  Befund-Typ (Klick → Findings vorgefiltert): nicht im IPAM, Name ≠ DNS-Name, Subnetz fehlt
  im IPAM, VLAN weicht ab, Namenskonvention, Präfix/Typ, doppelte Werte, unbenutzte
  Objekte. Darunter **Vergleich DDI ↔ NetBox** (fehlt in NetBox / abweichend / nur in
  NetBox) mit Suche. **Jetzt abgleichen** (Operator) rechnet neu, ohne etwas zu lesen oder
  zu schreiben.
- **Netzwerkdesign:** geplant (Platzhalter).

**Administration**
- **Benutzer & Rollen:** Rollen viewer/operator/admin, eigenes Konto, Links in die
  Keycloak-Benutzerverwaltung (`AUTOMATO_OIDC_ADMIN_CONSOLE_URL`, sonst aus dem Issuer
  abgeleitet). Benutzer selbst werden in Keycloak gepflegt (`tenant_ids` = Mandanten-ID).
- **Mandanten & Standorte (Admin schreibt):** **+ Mandant**, Kürzel, Namensprofil,
  Lese-Audit, Standorte (**+ Standort**, 3-stelliger Code) und die **Quellsysteme des
  Mandanten** (**+ Quellsystem** / **Bearbeiten**: FortiGate/Cisco ACI/Infoblox DDI/NetBox,
  Adresse, **Standort** des Geräts (für den NetBox-Sync), **Verwaltet von** = Collector-Agent
  oder zentraler Worker, read-only Zugangsdaten – FortiGate: **API-Token**, Cisco ACI:
  **APIC-Benutzer/Passwort** + **ACI-Tenants** (leer = alle ausser infra/mgmt), Infoblox:
  **WAPI-Benutzer/Passwort**, NetBox: **Lese-Token** + optional **Schreib-Token** (nur
  NetBox-Sync, geht nie an einen Agenten) – nur schreibbar, verschlüsselt gespeichert, Anzeige
  nur „hinterlegt"; bei Infoblox/NetBox die **Rolle** *IPAM führend* oder *Anreicherung*
  (genau ein führendes IPAM je Mandant); TLS-Prüfung, aktiv; Name je Mandant eindeutig;
  ADR 0009/0010). **Löschen:** Quellsystem (✕, Rückfrage nennt die mit
  gelöschten Regeln/NAT-Einträge) und **Mandant löschen** (nur nach Eintippen des Namens;
  löscht alle Daten des Mandanten inkl. seiner Collector-Agenten; Audit-Log bleibt).
  Altbestände ohne Mandant erscheinen unter „Nicht zugeordnete Quellsysteme“ zum Zuordnen.
- **Namensprofile:** Standardprofil **„default"** (per Migration angelegt, gilt für alle
  Mandanten ohne eigenes Profil, frei pflegbar), **+ Neues Profil** (Vorlage: Kopie eines
  bestehenden Profils, z. B. „default", oder leer), **+ Neue Namenskonvention** (optional die
  Einträge einer bestehenden Konvention – auch aus einem anderen Profil – kopieren),
  Einträge anlegen/bearbeiten/löschen.
- **Collector-Agenten (nur Admin):** **+ Agent** erzeugt das Token einmalig; Spalte
  **Geräte** (zugewiesene Quellsysteme); Status online/keine Meldung aus dem Heartbeat;
  Aktivieren/Deaktivieren. **Einrichtung Schritt für Schritt** direkt in der Ansicht: Geräte
  zuweisen, `agent.env`-Generator (nur Orchestrator-Adresse + Agent-Token, bleibt im Browser), Download des
  Agent-Pakets (`GET /agents/package`, ZIP ohne Zugangsdaten), PowerShell-Befehle zum
  Kopieren, Test und erster Lauf, Fehlerbehebung.
- **NetBox-Sync (nur Admin, ADR 0010):** einziger Schreibweg von Automato. Ziel-NetBox
  wählen (Quellsystem mit Schreib-Token), Umfang *Netzinventar* (Geräte mit Ports, VLANs je
  Quellsystem, reale Subnetze inkl. ACI BD/EPG, VRFs) und/oder *DDI-Spiegel* (Netze, Ranges,
  IPs mit DNS-Namen aus dem führenden IPAM), optional *Löschen zulassen*. **Vorschau
  erstellen** liest NetBox nur und zeigt je Art: anlegen / ändern / unverändert / Konflikt
  (von Hand gepflegtes Objekt weicht ab – bleibt unverändert) / löschen / veraltet, mit
  Aktionsliste. **Übernehmen** (Bestätigung) berechnet neu, schreibt nur bei unveränderter
  Lage, auditiert und liest NetBox danach neu ein. Geändert/gelöscht werden nur Objekte mit
  Tag `automato`. Geräte ohne Standort werden übersprungen (Hinweis) – Standort am
  Quellsystem setzen (bei genau einem Standort des Mandanten wird dieser genutzt).

## 4. API

Lesen = `viewer`, Läufe/Triage = `operator`, Verwaltung = `admin` (mandantsgescoped).

| Endpunkt | Zweck |
|---|---|
| `GET /health`, `GET /auth/config` | Status (API/DB), öffentliche Login-Konfiguration |
| `GET /rules` (`?q=`,`?tenant_id=`), `GET /rules/{id}` | Regelliste / -detail (Objektnamen, `refs` mit Referenzdaten) |
| `GET /objects?tenant_id=` (`category`, `source_system_id`, `q`, `issues_only`) | Firewall-Objekte mit IPAM-Abgleich |
| `GET /inventory/{devices\|interfaces\|vlans\|subnets\|vrfs}?tenant_id=` | Netzinventar (Subnetze mit IPAM-Abgleich) |
| `GET /reconcile/summary`, `GET /reconcile/ipam`, `POST /reconcile` | Abgleich: Befunde je Typ, DDI ↔ NetBox, neu berechnen (operator) |
| `GET /findings` (`?type=`), `PATCH /findings/{id}` | Datenqualität, Triage |
| `GET /nat`, `GET /applications[/{name}]`, `GET /appdoc/systems[/{name}]` | Doku |
| `GET/POST /tenants`, `PATCH /tenants/{id}`, `DELETE /tenants/{id}?confirm=<Name>` | Mandanten |
| `GET/POST /tenants/{id}/sites`, `DELETE /tenants/{id}/sites/{sid}` | Standorte |
| `GET /systems?tenant_id=`, `POST /systems`, `PATCH/DELETE /systems/{id}` | Quellsysteme (je Mandant; `agent_id`, `site_id`, `read_scope`, `ref_role`; `token` bzw. `username`/`password`, NetBox `write_token` nur schreibbar → `has_credentials`/`has_write_token`; Referenzquellen mit `reference_count`/`reference_at`) |
| `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/collect` | Läufe (Gerät mit Agent → immer `external`) |
| `GET/POST /agents`, `PATCH /agents/{id}`, `GET /agents/package` | Collector-Agenten (inkl. zugewiesener Geräte), Agent-Paket |
| `GET /internal/agent/next-run`, `…/targets`, `POST …/heartbeat` | Agent-Kanal (`X-Agent-Token`): Lauf + Zugangsdaten nur für zugewiesene Geräte |
| `POST /internal/collector-runs/{id}/result`, `GET …/{id}/target` | Ergebnis (Agent-Token des abholenden Agenten oder internes Token; Antwort = Quittung `status`/`message`); Ziel für den zentralen Worker |
| `POST /internal/collector-runs/{id}/references` | Teil-Upload einer Referenzquelle (Infoblox/NetBox, je bis 2000 Einträge; das Ergebnis meldet `reference_count`, sonst wird der Lauf verworfen) |
| `GET /netbox-sync/targets`, `GET/POST /netbox-sync/plans`, `GET …/plans/{id}[/items]`, `POST …/plans/{id}/apply` | NetBox-Sync (admin): Vorschau, Aktionen, Übernahme |
| `GET/POST /naming-profiles`, `…/{id}/dictionary`, `DELETE …/dictionary/{id}` | Namensprofile |

## 5. Sicherheit

- Alle Zielsystem-Zugänge **read-only** (Least Privilege). `.env` wird nie committet.
- **AuthN/RBAC** über Keycloak (OIDC, `AUTOMATO_AUTH_MODE=oidc`, ADR 0007); im
  `dev`-Modus laufen alle Anfragen als Standard-Admin — nur im Lab verwenden.
- Ausserhalb `AUTOMATO_ENVIRONMENT=dev` verweigert der Orchestrator ohne RBAC den Start,
  solange `AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API` nicht bewusst gesetzt ist (ADR 0005).
- Agent-Tokens werden nur gehasht gespeichert und einmalig angezeigt.
- **Geräte-Zugangsdaten** (ADR 0009) liegen verschlüsselt in der DB; der Schlüssel kommt aus
  `AUTOMATO_CREDENTIALS_KEY` (Passphrase oder Secret-Referenz `openbao:`/`file:`) oder aus der
  beim ersten Start erzeugten Datei `/var/lib/automato/credentials.key` (Volume
  `orchestrator-data`). **Schlüssel bzw. Volume mitsichern** – geht er verloren, sind die
  Tokens unlesbar (Läufe brechen mit „Zugangsdaten nicht lesbar“ ab) und müssen im UI neu
  hinterlegt werden. Ein Agent bekommt nur die Tokens der ihm zugewiesenen Geräte.
- **Nur lesen, ausser NetBox (ADR 0010):** Die Lese-Clients für FortiGate, APIC, Infoblox und
  NetBox lassen technisch nur GET zu (APIC zusätzlich Login/Logout) – ein Schreibversuch
  bricht vor dem Senden ab. Einziger Schreibweg ist der NetBox-Sync im Orchestrator mit dem
  Schreib-Token des NetBox-Quellsystems; er schreibt nur die synchronisierten Objekttypen
  (Tags, Tenants, Sites, Hersteller, Geräterollen/-typen, Geräte, Interfaces, VRFs,
  VLAN-Gruppen, VLANs, Prefixe, Ranges, IP-Adressen) und nur an diese NetBox. Das
  Schreib-Token in NetBox auf genau diese Objekttypen begrenzen.

## 6. Fehlerbehebung

- **„Anmeldung nicht möglich: Der Orchestrator lehnt das Anmelde-Token ab …“** (UI) —
  der Orchestrator nennt den Grund und loggt ihn (`docker compose logs orchestrator |
  grep OIDC`): `token audience invalid` → im Realm fehlt der Audience-Mapper
  `automato-api` (Client `automato-web` → Client scopes → dedicated → Mapper);
  `token issuer invalid` → `AUTOMATO_OIDC_ISSUER` passt nicht zum Realm (exakt wie
  `iss`, z. B. `https://auth.vitabrevis.ch/realms/automato-dev`); `Signaturschlüssel
  nicht in der JWKS` → `AUTOMATO_OIDC_JWKS_URL` zeigt auf einen anderen Realm. HTTP 503
  `JWKS vom Orchestrator nicht erreichbar` → der Orchestrator-Container erreicht Keycloak
  nicht (DNS/Firewall/WAF); Lösung „Backchannel direkt zur Keycloak-VM“ per
  `docker-compose.override.yml` in `deploy/keycloak/PROD-INSTALL.md` Abschnitt 9c.
  `token not yet valid` (bzw. `token expired` direkt nach der Anmeldung) → die Uhren von
  Keycloak-VM und Automato-VM weichen ab: auf beiden `timedatectl` prüfen („System clock
  synchronized: yes“), sonst NTP aktivieren (`sudo timedatectl set-ntp true`, intern ggf.
  den DC als NTP-Server eintragen). Toleranz: `AUTOMATO_OIDC_CLOCK_SKEW_SECONDS` (Default 60). Nach Änderungen an `.env`: `docker compose … up -d` (neu
  erstellt den Container).

- **Lauf „Fehler“ mit „Einspielen fehlgeschlagen: …“** — das Gerät wurde gelesen, aber das
  Ergebnis liess sich nicht in den Store schreiben. Der Ingest wird dann vollständig verworfen
  (vorheriger Stand bleibt), der Agent meldet `failed` mit derselben Meldung; Details inkl.
  Traceback: `docker compose logs orchestrator | grep -A30 "Einspielen von Lauf"`.

- **NetBox-Sync „Vorschau/Übernahme fehlgeschlagen“** — die Meldung von NetBox steht am
  Plan (z. B. fehlendes Schreibrecht → HTTP 403, Validierungsfehler → HTTP 400 mit Feld).
  „NetBox nicht erreichbar (host)“: der Orchestrator (nicht der Agent) schreibt – NetBox muss
  vom Orchestrator-Container aus erreichbar sein. „Seit der Vorschau hat sich … geändert“:
  nichts wurde geschrieben, neue Vorschau erstellen. Nach einem Abbruch liest Automato
  NetBox neu ein; die nächste Vorschau setzt am Teilstand an.
- **Referenzquelle „unvollständig“** — der Lauf hat weniger Einträge hochgeladen als gemeldet
  (Abbruch/Netz); der vorherige Stand bleibt, Lauf wiederholen.
- NetBox startet nicht / Token-Fehler: `source-of-truth/netbox/README.md` (Abschnitt Fehlerbehebung).
- Stack-/Lab-Probleme: `lab/README.md`.
- Logs: `docker compose logs -f orchestrator collector`.
