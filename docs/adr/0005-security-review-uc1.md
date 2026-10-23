# ADR 0005: Security-Review UC1 & Sicherheitsentscheide (M3)

## Status
Angenommen

## Kontext

M3 verlangt einen Security-Review der Architektur (gegen `SECURITY.md` und das
Risikoregister) für den UC1-Stand, bevor der Durchstich (M7) echte Systeme anfasst.
Neu gegenüber der ursprünglichen Architektur ist vor allem die **Mandantenfähigkeit
in einer Instanz** (ADR 0004) – mehrere Kunden/Security-Tenants in einer DB. Der Review
prüft den aktuellen Code-Stand (Grundaufbau + Datenmodell) und legt fest, was **jetzt**
gehärtet wird und was **bewusst vertagt** ist (mit Meilenstein bzw. Folge-ADR).

## Review gegen die SECURITY.md-Grundprinzipien

| # | Prinzip | Status | Befund / Entscheid |
|---|---|---|---|
| 1 | Least Privilege (Worker/Collector) | ✓ Design | Collectors haben keinen DB-Zugriff, liefern nur über die interne API. **Bedingung:** die Zielsystem-Accounts (FortiGate/ACI/NetBox) müssen read-only sein – wird mit dem Lab (M6) durchgesetzt und geprüft. |
| 2 | Strikte Kommunikationswege | ✓ | Web-UI → Orchestrator → Queue → Collector → interne API. Kein Bypass. |
| 3 | Verschlüsselung überall (auch intern, mTLS) | ✓ (0.49.0) | Agenten nur über das mTLS-Gateway (0.12.0); **intern seit 0.49.0 TLS mit eigener CA** (ADR 0023, Schalter `AUTOMATO_INTERNAL_TLS=on` über `deploy/aufbau.sh tls-intern`): Postgres nur `hostssl`/`verify-full`, Redis nur TLS, Orchestrator und Web-UI per HTTPS, alle Gegenstellen gegen die CA geprüft. Bis 0.48 intern HTTP/plain. |
| 4 | Secrets nie im Code/Repo | ⚠ teilweise | gitleaks (CI + pre-commit), `.env` nie committet. **Befund:** `AUTOMATO_INTERNAL_API_TOKEN` hat einen Default `dev-only-token`, der in Produktion ein Footgun wäre. **Härtung jetzt:** Fail-closed – ausserhalb `dev` wird der Default-Token abgelehnt (Startup-Check). Ziel-Credentials via Ansible Vault ab M6; HashiCorp Vault später (Folge-ADR „Secrets-Backend"). |
| 5 | Lückenloses Audit-Log | ✓ / ⚠ | Collector-Läufe und Finding-Statusänderungen werden auditiert. **Lücke:** lesender UI-Zugriff wird erst mit AuthN/RBAC protokollierbar – siehe #6. |
| 6 | RBAC | ✗ offen (**wichtigster Punkt**) | Noch keine AuthN/AuthZ. Durch die Mandantenfähigkeit ist **Tenant-Isolation** sicherheitskritisch (Risiko R13). **Entscheid:** (a) `tenant_id`-Scoping ist ab jetzt in **jeder** Store-Query Pflicht; (b) RBAC-Rollenmodell + Tenant-Scoping als Folge-ADR „RBAC", umgesetzt **bevor** das UI echte Daten exponiert / vor Produktion. Für den Lab-Durchstich (ein Operator) akzeptiert. |
| 7 | Trennung Lesen/Schreiben | ✓ | UC1 ist per Design read-only gegenüber Zielsystemen. |
| 8 | Signierte/validierte Jobs | ⚠ teilweise | Collector→Orchestrator über statisches Service-Token authentisiert. Orchestrator→Collector via Redis nicht signiert. **Vertagt:** Job-Signierung; bis dahin Redis nur im vertrauenswürdigen Netz + authentifiziert (Netzsegmentierung, Folge-ADR). |

## Offene Punkte → Folge-ADRs bzw. Vertagung

Damit ist das M3-Exit-Kriterium erfüllt („offene Punkte haben eine ADR-Nummer oder sind
bewusst vertagt"):

- **ADR (geplant) Secrets-Backend:** Ansible Vault (ab M6) → HashiCorp Vault. Auslöser:
  erste echte Ziel-Credentials.
- **ADR (geplant) RBAC-Rollenmodell:** Rollen, Tenant-Scoping, read-access-Audit.
  Auslöser: vor UI-Exposition realer Daten.
- **ADR (geplant) Netzwerksegmentierung:** Orchestrator/Worker/Redis/Zielsysteme.
- **ADR (geplant) mTLS/Zertifikatsverwaltung:** interne Service-Kommunikation – umgesetzt als
  ADR 0023 (TLS intern mit eigener CA, 0.49.0).

Diese sind bewusst **nicht** jetzt entschieden (kein Nutzen ohne Lab/Produktion, R6:
kein Vorratsbau), aber terminiert.

## Architektur-Validierung gegen UC1

Die Architektur trägt UC1: Collectors (read-only Worker-Variante), Rule & Service Store,
SoT Gateway, Web-UI, Job-Kreislauf mit Audit. Die einzige substanzielle Ergänzung
gegenüber `ARCHITECTURE.md` ist das **Tenant-Scoping** (ADR 0004) – architektonisch
konsistent, kein Blocker. Keine Grundsatzänderung nötig.

## Konsequenzen (jetzt umgesetzt)

- **Fail-closed Token-Check** (`app/config.py::check_security_config`): ausserhalb `dev`
  wird der Default-`internal_api_token` beim App-Start abgelehnt.
- **CORS aus Konfiguration** (`AUTOMATO_CORS_ORIGINS`), Default nur `localhost:5173`
  (Dev). In Produktion explizit zu setzen.
- **Tenant-Scoping-Pflicht** in Store-Queries als verbindliche Regel dokumentiert
  (Durchsetzung mit RBAC).
- Neue Risiken **R13** (Mandanten-Isolation) und **R14** (Daten-Aggregation) im Register.
- Der volle Code-Level-Security-Scan (`/security-review`) wird bei M7/M9 auf den dann
  vorhandenen Ziel-System- und Auth-Code angesetzt, nicht auf das Skeleton.
