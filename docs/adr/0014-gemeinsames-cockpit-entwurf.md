# ADR 0014 — Gemeinsames Betriebs-Cockpit mit Magister (Entwurf)

## Status

**Entwurf** (2026-10-20) – zur Abstimmung mit Projekt Magister (`Vita-Brevis-GmbH/magister`).
Kein Code in Automato ausser der System-Seite (ADR 0013), die als erster Baustein dient.

## Kontext

Automato und Magister haben fast denselben Stack (FastAPI, Postgres, Keycloak, React,
docker-compose auf VMs) und dieselben Betriebsfragen: Welche Version läuft wo, sind die
Migrationen durch, wann war die letzte Sicherung, wie aktualisiere ich und wie komme ich
zurück? Heute beantwortet das jede Plattform für sich (Automato: System-Seite,
`deploy/update.sh`).

## Vorschlag

1. **Gemeinsamer Status-Vertrag** `GET /system/status` (Admin, OIDC wie die übrige API) mit
   festen Feldern:
   - `version`,
   - `database{revision, head, up_to_date}`,
   - `backup{at, encrypted}`,
   - `security{auth_mode, …}`,
   - plattformspezifisch unter `extra` (Automato: Agent-Paket/Signatur, Zeitplan).
2. **Cockpit als eigene kleine App** (eigener Container), die mehrere Plattform-Instanzen per
   OIDC-Client-Credentials liest:
   - je Instanz Version, Migrationsstand, letzte Sicherung, Health,
   - Ampel nach festen Regeln (Sicherung älter als 26 h, DB ≠ Code, Signatur fehlt).
3. **Update/Rückfall bleibt auf der VM** (`deploy/update.sh` je Plattform, gleiche Schritte
   und Exit-Codes). Das Cockpit zeigt und protokolliert, löst aber nicht selbst aus.
   Schreibzugriff aus einer zentralen App auf viele VMs wäre ein neues, grosses Risiko.
   Ein späterer Auslöser käme nur per signiertem Auftrag an einen VM-Agenten mit eigener ADR.
4. **Gemeinsame Bausteine:**
   - Status-Schema als kleines Paket (`automato-status`/`magister-status` → gemeinsames
     `vb-ops-status`),
   - Update-Skript-Konventionen,
   - age-Sicherung, Keycloak-Realm-Vorlage.

## Offene Fragen

- Wo läuft das Cockpit (eigene VM, neben Keycloak)?
- Wer betreibt es, und welche Rollen gibt es (nur Admins)?
- Benachrichtigung (Mail/Teams) bei roter Ampel?
- Zeitplan für die Vereinigung der Module (ROADMAP „Ausbaustufe B“, Synergie Magister).
