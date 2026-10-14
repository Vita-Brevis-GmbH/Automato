# ADR 0007 – AuthN/RBAC via Keycloak (dedizierter IdP, Produktions-Gate)

- Status: akzeptiert
- Datum: 2026-10-13
- Kontext-Meilenstein: Ausbaustufe B / E1 (Härtung & RBAC); schliesst das
  Produktions-Gate aus ADR 0005 (#6 RBAC)
- Bezug: ADR 0005 (Security-Review, geplante Folge-ADRs „RBAC-Rollenmodell" und
  „Secrets-Backend"), ADR 0004 (Mandantenfähigkeit), ADR 0002 (Adapter-Prinzip),
  `docs/BETRIEB.md` (§5 Sicherheit), Repo `Vita-Brevis-GmbH/magister`

## Kontext

Die öffentliche Read-API hat heute **kein AuthN/RBAC** (ADR 0005 #6, fail-closed
ausserhalb `dev`). Das ist das letzte Gate vor produktiver Freigabe. Zusätzliche
Anforderungen an die Plattform:

- **Hohe Web-Security**: HTTPS erzwungen, **OTP/MFA Pflicht**, ein **globaler
  Admin** ist beim Aufbau bereits vorkonfiguriert.
- **Lokale Benutzer *und* SAML** (Föderation zum Kunden-IdP) nebeneinander.
- **Mandantenfähigkeit** (ADR 0004): Benutzer sind auf ihre Mandanten beschränkt;
  ein Standort mit abweichendem Namens-/IP-Konzept kann eine eigene SoT-Instanz
  haben. Tenant-Isolation ist sicherheitskritisch (Risiko R13).
- **Rollenkonzept klein starten, aber ausbaubar.**
- **Synergie mit Projekt Magister** (`Vita-Brevis-GmbH/magister`): nahezu
  identischer Stack (Python 3.12 · FastAPI · SQLAlchemy 2 async · React/Vite ·
  PostgreSQL 16 · Docker Compose · Caddy 2). Magister nutzt heute OIDC **direkt**
  gegen Entra ID + einen lokalen Break-Glass-Admin; **weder Keycloak noch SAML**
  sind dort bisher im Einsatz. Ziel ist, Auth/Cockpit/Deploy-Scripts als
  gemeinsame Bausteine zu nutzen und die Module später zu vereinen.

Die langfristige Vision reicht über Firewalls/ACI hinaus (allgemeine
Workflow-Orchestrierung: VMs ausrollen, Applikationen installieren, automatische
Doku) bei Vorrang für Open Source und Vereinfachung. Das Auth-Fundament muss
diesen späteren **Schreibpfad** tragen, nicht nur die Read-API von UC1.

## Entscheid

### D1 – Keycloak als dedizierter Identity Provider (eigener Dienst)

**Keycloak** (Open Source, de-facto-Standard) wird der zentrale IdP für die
gesamte Produktlinie. Es liefert out-of-the-box: lokale Benutzer, **SAML- und
OIDC-Föderation** (Identity-Brokering zum Kunden-IdP), **TOTP/OTP erzwingbar**,
Admin-Console, Brute-Force-Schutz, Passwort-Policies.

- Betrieb: **eigener Container** → später **eigene VM**, unter **eigener URL**
  (getrennt von Automato und Magister).
- Flow: Standard **OIDC Authorization Code + PKCE** — die Plattform leitet zum
  Keycloak um, nach Login/MFA zurück zur Plattform.
- Clients im Keycloak-Realm: `automato-web` (public, PKCE) und `automato-api`
  (Resource Server, validiert das JWT). Analog später `magister-*`.
- **Globaler Admin** wird beim Aufbau via **Realm-Import** (Bootstrap) geseedet,
  mit **erzwungenem OTP** beim ersten Login.
- **SAML** zum Kunden-IdP wird **in Keycloak als Identity-Broker** konfiguriert;
  die Apps bleiben reine OIDC-Clients (eine Code-Seite, kein SAML-SP in Python).

Begründung gegen Alternativen: ein app-eingebetteter OIDC-Client (Magister-Muster)
müsste SAML selbst implementieren und böte kein zentrales SSO über die Produkte;
ein leichteres IdP (z. B. Authentik) wäre möglich, aber Keycloak ist der
verbreitetere Standard und bewusst als gemeinsame Basis gewählt.

### D2 – Rollenmodell: klein starten, additiv ausbaubar

Startsatz: **`viewer` / `operator` / `admin`** (Rollen aus einem Keycloak-Claim,
auf Automato-Rollen gemappt):

- `viewer`: Doku lesen (Regeln, NAT, Applikationen, Findings) — nur eigene Mandanten.
- `operator`: zusätzlich Collect auslösen, Findings triagieren (erster, enger
  Schreibpfad).
- `admin`: Benutzer/Rollen, Mandanten/Quellen; mandantsübergreifend.

Das Modell ist additiv: weitere Rollen/Rechte kommen dazu, ohne Bruch. Als
Zielbild dient Magisters **dynamische Rollen-/Rechte-Matrix** (Magister ADR 0010);
Durchsetzung per FastAPI-`Depends(require_role(...))` am Endpunkt.

### D3 – Mandanten-Scoping in jeder Query erzwingen

Der authentifizierte Principal trägt seine erlaubten Mandanten. Das Scoping wird
**zentral im Repository-/Store-Layer** erzwungen (`WHERE tenant_id IN (...)`),
nicht pro Endpunkt verstreut; Ausnahmen nur mit explizitem Marker
(`# scope-bypass: <grund>`). `?tenant_id=` wird für Nicht-Admins nur **innerhalb**
der erlaubten Menge akzeptiert. Damit ist ADR 0005 #6 erfüllt und R13 adressiert.
Muster übernommen von Magister (`school_id`-Filter).

### D4 – Secrets/Vault hinter einem Provider-Interface

Passwörter/Secrets nie im Klartext/Repo. Ein **Secrets-Provider-Interface**
(Adapter-Pattern wie beim SoT-Gateway) kapselt die Herkunft:

- Lab/Dev: `.env` (wie heute).
- Produktion: **OpenBao** (MPL, Vault-Fork) oder SOPS+age; **dezentrale**
  Speicherung bleibt möglich (spätere verteilte Bauweise).

Konkretisiert die in ADR 0005 geplante Folge-ADR „Secrets-Backend". Keycloak-
Client-Secrets, Session-/CSRF-Secrets und Ziel-Credentials laufen über diesen
Provider.

### D5 – Audit auf Principal + Aktion erweitern

Audit-Log wird um **wer** (authentifizierter Principal) ergänzt. State-ändernde
Aktionen (Collect, Triage) **immer**; Lesezugriffe per-Mandant zuschaltbar
(Firewall-Doku kann sensibel sein). Muster von Magister übernehmen:
pgcrypto-verschlüsselter Payload + Allowlist gegen versehentliches Secret-Logging.

**Umsetzung (E1.4):** Zentrale Schreibstelle `app/audit` mit zwei Schichten.
(1) **Allowlist** (`app/audit/allowlist.py`): pro Aktion sind die erlaubten
`detail`-Keys benannt; alles andere wird verworfen (fail-closed), verworfene Keys
nur als *Name* unter `_dropped` vermerkt – so landen Secrets strukturell nie im Log.
(2) **Optionale At-Rest-Verschlüsselung** (`app/audit/crypto.py`, Fernet, Schlüssel
`AUTOMATO_AUDIT_ENCRYPTION_KEY`): bewusst **app-seitig statt pgcrypto** gewählt,
damit der Schlüssel in der Anwendung/Vault (E1.5) liegt statt DB-nah und die Tests
DB-agnostisch (SQLite) bleiben; die DB-seitige pgcrypto-Variante bleibt als Option.
Default leer = Klartext (dev/Tests). Principal (`actor=user:<sub>`) nun auch an den
Namensprofil-Schreibpfaden (create/upsert/delete). **Noch offen:** per-Mandant
zuschaltbares Lesezugriffs-Audit.

### D6 – Magister-Synergie & -Integration (phasiert)

- **Automato zuerst** (grüne Wiese fürs Auth): direkt als Keycloak-Client bauen.
- **Magister-Migration als eigener Magister-Meilenstein**, **nicht** Big-Bang:
  Keycloak brokert zu Entra, Magisters OIDC-Client zeigt auf Keycloak statt
  direkt auf Entra; der lokale Break-Glass-Admin bleibt als Fallback (bzw. zieht
  nach Keycloak). URL-seitig eine Umlenkung: Plattform → Keycloak → zurück.
- **Gemeinsame Bausteine** (unabhängig vom IdP) werden aus Magister übernommen:
  - **Deploy-/Security-Skelett**: Caddy 2 (TLS 1.3 only, Auto-TLS/ACME, CSP, HSTS,
    SRI, Security-Header), docker-compose, `pg-backup`-Sidecar, `install-*.sh`.
  - **Cockpit** (`cockpit/`, vorgesehen für Auslagerung nach `vita-brevis-cockpit`):
    Multi-Instanz-Ops-Dashboard mit Health-Polling, Versionsanzeige,
    Update-Trigger (Webhook) und Channels `stable`/`latest` mit Rollback-Snapshot.
    Automato-Instanzen hängen sich ein, statt ein eigenes zu bauen.
  - **Audit-** und **Scope-Muster** (D3/D5) sowie die RBAC-Dependency-Konvention.

## Abgrenzung / Reihenfolge

Umsetzung in kleinen Schritten (Ausbaustufe B, siehe `ROADMAP.md`):

- **W1 – Web-UI-Ausbau** wird **vorgezogen** (Übersicht, Filter, Regel-Detail,
  Mandanten-Switcher), zunächst gegen die bestehende (fail-closed) API.
- **E1.1** AuthN-Gerüst (Keycloak-Client, OIDC+PKCE, Dev-Fallback, Principal).
- **E1.2** Mandanten-Scoping zentral erzwingen (schliesst das Datenleck).
- **E1.3** Rollen `viewer/operator/admin` + Durchsetzung.
- **E1.4** Audit-Erweiterung (D5).
- **E1.5** Secrets-/Vault-Provider (D4).

Nicht Teil dieses ADR: mTLS/at-rest-Verschlüsselung und Netzsegmentierung
(bleiben eigene Folge-ADRs aus 0005); der schreibende Use Case (ADR 0002).

## Konsequenzen

- Das **Produktions-Gate** (ADR 0005 #6) bekommt eine konkrete Lösung; nach
  E1.1–E1.3 kann die Read-API authentifiziert exponiert werden.
- Die in ADR 0005 geplanten Folge-ADRs „RBAC-Rollenmodell" und „Secrets-Backend"
  sind hiermit **konkretisiert** (dieses ADR tritt an ihre Stelle bzw. verweist
  darauf).
- **Neue Risiken** fürs Register (R18 ff., beim Epic-Start zu ergänzen): Keycloak
  als zentraler Ausfallpunkt / Break-Glass bei nicht erreichbarem IdP;
  Deployment-Footprint (JVM) auf kleinen Instanzen; korrekte SAML-Brokering-
  Konfiguration je Kunde.
- **Offen / Eingang abwarten:** UC1-Fachdaten (ACI, Namenskonventionen,
  Tenant-Struktur, Confluence-Feldschema). Sie präzisieren das
  „Applikationen"-Modell und Model 1 (ACI + Infoblox), **nicht** aber dieses
  Auth-Fundament.
