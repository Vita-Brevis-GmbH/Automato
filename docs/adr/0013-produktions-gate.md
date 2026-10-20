# ADR 0013 — Produktions-Gate: Agent-Kanal, signierte Updates, Sicherung, Update/Rückfall

## Status

Angenommen (2026-10-20). Ergänzt ADR 0005 (Security-Review), 0007 (Secrets), 0008/0009
(externer Agent), 0011 (Selbst-Update).

## Kontext

Vor dem Produktivbetrieb waren offen: mTLS für `/internal` nur als Empfehlung (Snippet),
Agent-Updates nur per SHA-256 aus demselben Kanal abgesichert, keine Sicherung/Wiederherstellung,
kein geführtes Update mit Rückfall, R16 (FortiGate akzeptiert jedes Token, Trusted-Host-only)
nicht prüfbar, kein Überblick über den Betriebsstand.

## Entscheidung

1. **mTLS-Agent-Gateway** (`deploy/agent-gateway`, Caddy, Port 8443): Client-Zertifikat einer
   eigenen Agent-CA Pflicht, nur die Agent-Pfade werden weitergereicht. Das Gateway setzt
   `X-Gateway-Secret` und `X-Client-Cert-Subject` selbst. Der Orchestrator erzwingt
   `AUTOMATO_INTERNAL_MTLS_MODE`:
   - `require`: Agent-Token gilt nur, wenn der Aufruf vom Gateway kommt,
   - `bind`: zusätzlich CN = Agent-Name.

   Der Start wird ohne Secret verweigert. Der zentrale Worker (internes Token) bleibt direkt.
   Port 8000 wird nicht in Agent-Netze freigegeben. CRLs prüft Caddy nicht → kurze
   Laufzeiten, Agent deaktivieren, neues Zertifikat.
2. **Signierte Agent-Updates** (Ed25519):
   - Signiert wird offline (`scripts/agent_signing.py`) die Nachricht aus Version und SHA-256
     je Datei.
   - Der Orchestrator liefert die Signatur nur mit (`/internal/agent/update` → `signature`).
     Er prüft beim Einspielen (`automato agent-package install-signature`), wenn der Schlüssel
     gesetzt ist.
   - Agenten mit `AUTOMATO_AGENT_SIGNING_PUBKEY` installieren nur gültig signierte Updates.
   - Die Erstinstallation bleibt manuell aus dem Web-UI (trust on first use).
   - `cryptography` wird Abhängigkeit des Agenten.
3. **Sicherung/Wiederherstellung** (`deploy/backup`):
   - `pg_dump -Fc` und der Schlüssel der Zugangsdaten, getrennt, verschlüsselt mit age;
     ohne Empfänger bricht die Sicherung ab.
   - Die Wiederherstellung prüft zuerst die Entschlüsselung und ersetzt dann die DB.
   - Der Zeitpunkt der letzten Sicherung steht im Daten-Volume (System-Seite).
   - At rest: Fernet für Zugangsdaten und Audit (app-seitig), Festplattenverschlüsselung der
     VM für die Volumes.
4. **Update/Rückfall** (`deploy/update.sh`):
   - Ablauf: Sicherung → Code (`--ff-only` oder Tag) → Build → Start → `/health`.
   - `--rollback` setzt Code **und** DB auf den Stand vor dem Update zurück, weil Migrationen
     beim Start laufen.
   - Unsauberes Repo oder losgelöster HEAD ohne Ziel: Abbruch.
5. **R16-Prüfung** opt-in: `automato-collector --check --strict` versucht je FortiGate einen
   Abruf mit bewusst ungültigem Token.
   - Wird er beantwortet, prüft das Gerät nur die Quell-IP → Fehler (Exit 2).
   - Nicht Standard, weil der Versuch als fehlgeschlagene Anmeldung zählt
     (admin-lockout-threshold).
6. **System-Seite** (`GET /system/status`, Admin, UI „System“) zeigt:
   - Version, Migrationsstand DB/Code,
   - Agent-Paket mit Signaturzustand und Anzahl aktueller Agenten,
   - Zeitplan, Anmeldung, Agent-Kanal, Audit-/Zugangsdaten-Verschlüsselung,
   - letzte Sicherung.

   Nie Schlüssel oder Secrets.

## Konsequenzen

- Produktiv: Gateway (`bind`), signierte Pakete, tägliche verschlüsselte Sicherung, Updates nur
  über `deploy/update.sh`.
- Agenten brauchen beim Update auf 0.5.0 einmalig PyPI-Zugang (oder Wheelhouse) für
  `cryptography`. Ohne Zugang bleibt der Agent auf seiner Version und meldet den pip-Fehler.
- Das Cockpit für Version/Update/Rückfall über mehrere Plattformen (gemeinsam mit Magister) ist
  ein eigener Entwurf (ADR 0014). Die System-Seite ist der Automato-Teil davon.
